# 2026-10-14 · 刀 2：共享 AsyncClient（真机 食堂几点关门 9437 → 4353 ms）

## 一、输入

第 10 号施工单 `D:/Workbuddy/2026-10-12-21-36-55/prompts/10_刀2_共享AsyncClient_降延迟落地prompt.md`：
只动传输层一处，把"每次调用新建 `AsyncClient`"改成进程内共享（连接池 + keep-alive），
实测依据是 §0 那两组数字（新建 2188/1967/1987ms vs 复用 679/574/450ms）。
§3.1 特别警告：**SSRF 闸门必须每次 `chat` 都跑**，不许挪进"只建一次"的共享工厂里。

## 二、做了什么

`app/llm/client.py`：
- 新增 `_get_client()`：懒建一个 `httpx.AsyncClient(timeout=Timeout(30.0, connect=3.0),
  limits=Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30.0))`；
- `chat()` 里 `async with httpx.AsyncClient(...)` → `client = await _get_client()`
  （**不能**对共享实例用 `async with`：退出即关闭，keep-alive 当场失效）；
- 异常五支 `kind`、超时值、SSRF 闸门位置、网关记账，一字未动。

## 三、与施工单的一处偏离（有可复现证据，不是偏好）

§2.1 写的是"全进程只留一个"。**照它写完，第二次换事件循环用就炸**：

```
X3b 变异 = 原样照抄 §2.1 的 `_get_client`，跑 tests/test_47 →
RuntimeError: Event loop is closed     （anyio → Windows proactor call_soon）
```

复现路径很普通：第一批请求在一个 `asyncio.run` 里建好连接并留在池里，
第二批换**另一个事件循环**去碰这个 client（`aclose()` 或复用那条连接）→ 崩。
而本仓 47 个测试文件天天就是这个模式（`TestClient` 一个循环、`asyncio.run` 又一个）。

所以改成**按事件循环各留一份**（`_loop_clients` / `_loop_locks`，取用时顺手清掉已死循环的条目——
那些连接**不能**在别的循环里关，交给 GC 收 socket）：
- 生产上 uvicorn 一个 worker 一个循环 → 永远只有 1 个条目，**复用收益一分不少**；
- 测试里两个循环各拿各的，不会互相踩。

这条偏离回写进了 `client.py` 的文件头注释和 `test_47` 的文件头（我在那儿先写下过
"不需要分桶"的错误结论，实测打脸后原地改口——活文件要改，已提交的快照不动）。

## 四、护栏（`tests/test_47_shared_async_client.py`，16 项，先红后绿）

不测"实例是不是同一个"那种表面形状，而是**起本地假大模型接口数 TCP 连接**：
一个 handler 实例 = 一条连接，两条请求复用 keep-alive 就该只看到 **1 条**。

| 批 | 钉什么 |
| --- | --- |
| A | 两笔确实打到本地假接口（防"偷偷去真上游"）／都拿到回答／**连接数 == 1**／**SSRF 闸门 == 2 次** |
| B | 同一循环内 `_get_client()` 同一实例；旧 client 被关掉会自动重建；形状（全文件只 1 处建 client、`async with httpx.AsyncClient` 不许回来、超时值在、五支 kind 都在） |
| C | 跨事件循环不炸；闸门在两个循环里都跑；**两个循环各留一份**（钉住 §三 那条偏离） |

变异（逐字节还原）：

```
X1 发请求改回每次新建 client   → 红 2 条（连接数 2 + 形状）
X2 把 SSRF 闸门从每次 chat 挪走 → 红 2 条（闸门计数 0）
X3b 照抄 §2.1 全进程单例        → 直接 RuntimeError: Event loop is closed
```

⚠️ 还有一笔**变异写坏了**要记账：X3 第一版拿 `0` 当字典 key，是把代码弄崩而不是"换一种实现"，
结果"红 0 条"——那不算断言有牙（正是指南 4h 那条）。换成 X3b 才测出真实差别。

## 五、我自己写测试时踩的四个坑（都写进注释了）

1. **假接口没声明 `protocol_version = "HTTP/1.1"`** → 服务端每笔都关连接，
   "复用一条连接"永远测不到（第一版实测 2 条连接其实是这里造成的，不是复用失败）。
2. **补丁只打了 `app.config.get_llm_config`** → `client.py` 是 `from X import Y` 导入时绑定，
   结果**两笔请求真发到 DeepSeek 上去了**（白烧额度，拿回两篇"一/二是什么字"的作文）。
   这是本仓库写第三遍的同一条老坑；现在测试直接断言"笔数落在本地假接口上"堵死它。
3. **形状断言被自己的注释绊红**（第四次）：注释里写了 `httpx.AsyncClient(` 字面串 → 数出 2 处。
   两头修：断言先剔 `#` 行再数。
4. **curl 在 Git-Bash 里被引号拆坏** → 多 worker 检查第一遍全是 400；换成 Python 发请求才是真测。

顺带记两个事实（对以后查"慢/429/连不上"有用）：
- 这台机器 `HTTP_PROXY` 开着，httpx 默认 `trust_env=True` → **生产里对上游的调用也走系统代理**
  （测试进程里我用 `NO_PROXY=*` 绕开，否则本地假接口会被代理成 502）。

## 六、验证与数字

| 项 | 结果 |
| --- | --- |
| 真机（8000 重启后，各两遍，第二遍=稳态） | 「食堂几点关门」**9437 → 4353 ms**；「今天几号」**6315 → 2539 ms**；对照「帮我安排游泳」77 → 79 ms（零模型调用，本刀无关） |
| 服务日志 | 无 `Event loop is closed` / `Client is closed` / Traceback |
| 多 worker sanity（8011，`--workers 2`） | `Started server process` ×2；六笔全 200、每笔 1 张卡；日志 0 噪声 —— 与 §三 一致：每 worker 各一份 client |
| 本机全量 | **3268 通过 / 0 失败（47 批）**，`全部通过 ✅`，exit 0 |
| 干净克隆（`app/data/student/` 整个目录都没有） | **同为 3268 通过 / 0 失败**、`全部通过 ✅`，`pytest` 1 passed，两个退出码都是 0 |
| 探针脏数据 | `smoke-k2-*` / `smoke-k2w-*` 共 12 个 session + 12 张挂起卡按前缀清回；sessions 数量回到 656；`pending.json` 里剩的 `s3ec3b8e543` **是用户自己的**，没碰 |

## 七、还剩什么

- **刀 3**：判定层用一次完整对话补全做 4 字节分类（1249 prompt → 38 completion，2.7~4.2s）、
  请求体没有 `max_tokens`、没有 `stream`。这三条才是"三次往返 4.3 秒"里剩下的主体。
- 刀 1 的 A 方案（异步换润色话术）仍需要先建推送/轮询通道。
- 本刀没加 `close_client()` 与 lifespan：main 现在没有 lifespan，加了就是死代码；
  施工单 §2.3 的"或至少保证进程级单例、进程退出随进程消亡"这一分支适用。

## 八、commit

`9852a4a`（护栏 test_47，本笔故意留红）· `5d6df4c`（共享 client + 按事件循环各留一份）+ 本快照。
