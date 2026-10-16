# 10 · 刀 2：共享 AsyncClient（连接池 + keep-alive）砍掉重复握手

> 给落地 AI（Qoder）的 drop-in 施工单。**只读这文件，不要改其它 prompt。**
> 这是「三刀降延迟」的**第二刀**（第一刀 B 见 `09`）。
> 本刀**只动传输层**（client.py 一处），不改任何 prompt / 判定逻辑 / 护栏 / 流式。

---

## 0. 一句话目标与实测依据

实测 `app/data/llm_usage.jsonl`：每次新建 `httpx.AsyncClient` 的握手成本 **2188 / 1967 / 1987 ms**，
复用连接则是 **679 / 574 / 450 ms** —— 光握手每次固定多付 **≈1.3~1.5 秒**。

「三次往返 9 秒」路径（如「食堂几点关门」= 判定 1 次 + 引擎 1 次 + 带工具再 1 次，实测 9437ms）
里，**重复握手合计 +4.2 秒**，是这条链路里最大的可回收块。

**改法**：把 `client.py:120` 的「每次调用新建 AsyncClient」改成「进程内共享一个 AsyncClient
（带连接池 + keep-alive）」，同一 upstream host 的 TCP/TLS 连接复用，握手只付一次。

---

## 1. 改动范围（一处 + 一个共享变量，纯传输层）

| 文件:行 | 现在 | 改成 | 性质 |
|---|---|---|---|
| `app/llm/client.py:120` | `async with httpx.AsyncClient(timeout=...) as client:`（每调用新建） | 复用进程内共享 client 实例 | 核心改动 |
| `app/llm/client.py`（模块级） | 无 | 新增共享 client 懒初始化变量 + `httpx.Limits` 连接池 + 关闭钩子 | 新增 |
| `app/llm/client.py:107` | `check_base_url_ssrf(base_url)` | **保持不变**（每次 chat 算完 base_url 后、发请求前跑） | 不动 |

> `grep -rn "AsyncClient" app/llm/` 全包**只有** `client.py:120` 这一处创建 client，
> 所以改这一处即覆盖所有流量：意图判定 / 引擎 / 润色 / `main.py:4073` 健康探针全 funnel 到此。

---

## 2. 具体改法（给实现 AI 的指令）

### 2.1 新增进程内共享 client（模块级懒单例，改动最小、不碰 main 的 lifespan）
在 `client.py` 顶部（class 之外）加：
```python
import asyncio
import httpx

_shared_client: "httpx.AsyncClient | None" = None
_client_lock = asyncio.Lock()

async def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=3.0),
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                        keepalive_expiry=30.0,
                    ),
                )
    return _shared_client

async def close_client() -> None:
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
```

### 2.2 改 `chat()` 里的发请求段（client.py:119-125 一带）
把：
```python
async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0)) as client:
    resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
```
改成（**不要**再 `async with` 共享 client —— 那样会在退出时把连接关掉，keep-alive 失效）：
```python
client = await _get_client()
resp = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
resp.raise_for_status()
data = resp.json()
```
- `timeout=httpx.Timeout(30.0, connect=3.0)` 保留（connect=3.0 仍要：SSRF 探测代价在 connect，
  且区分"这台机器连不连得上"；read=30 仍要：带工具长回答真的会慢）。
- 错误映射分支（401/403 → auth、429 → rate_limited、RequestError → unreachable，**含 kind 字段**）
  **一字不改**，回给请求方的消息不反射地址的逻辑也不改。

### 2.3 关闭钩子
在 FastAPI 的 lifespan（或应用退出路径）里调 `await close_client()`，避免进程退出时
"Event loop is closed" 噪声。若 main 没有现成 lifespan，用 `app.router.lifespan_context` 包一层，
或至少保证 `_shared_client` 是进程级单例、进程退出随进程消亡即可（连接会自动回收）。

---

## 3. 必须钉住的护栏（缺一条就是新 bug）

1. **SSRF 闸门零影响**：`check_base_url_ssrf(base_url)` 在 `client.py:107`，位于 120 之前、
   与 client 创建**无关**——保留原样，每次 chat 算完最终 base_url 后、发请求前跑。
   ⚠️ 别把闸门误挪到"共享 client 创建时"：那时 base_url 还没算出来，且创建只发生一次，
   会漏掉后续每次调用的校验。**必须在每次 `chat` 内、post 之前跑**（现状已对，保持）。
2. **多 worker 安全**：`httpx.AsyncClient` 是进程内单例，uvicorn 多 worker 时**每个 worker 各自一个**，
   已是复用，OK；不要试图做跨进程共享（不可行）。单 worker 默认也直接受益。
3. **连接池上限别失控**：`max_connections=20 / max_keepalive_connections=10 / keepalive_expiry=30.0`
   足够，避免 fd 耗尽；别设成无上限。
4. **守卫族零影响**：polish 自守卫（`polish.py:107-115`）、引擎 claim 守卫
   （`main.py:3600-3601` + `:3654` + 写库漏斗）全都不动——本刀只动传输层。
5. **用量记账零影响**：`ModelGateway(user=...)` 的 user 标签与记账在 gateway 层，本刀不动，
   管理端「用量统计」照常。
6. **keep-alive 按 origin 复用**：base_url 可能因人而异（服务器配置 vs 学生自定义），
   httpx 连接池按 (scheme,host,port) 分组——同一 upstream 复用，不同 upstream 各建各的池，均正确。

---

## 4. 老规矩验证清单（落地 AI 必须逐项过）

- **护栏先行（先写断言，再改）**：
  - 新断言 1：同一进程内连续两次 `gateway.chat(...)` 走的是**同一个** client 实例
    （mock `_get_client` 返回固定对象，或在 client 上加调用计数，断言计数只增、实例地址不变）。
  - 新断言 2：`check_base_url_ssrf` 在每次 `chat` 仍被调用（mock 计数 == 调用次数，不因共享 client 而少跑）。
- **变异打回（证明断言真能抓回归）**：把 `_get_client()` 改回 `async with httpx.AsyncClient() as client:` 局部创建
  → 断言 1 必须变红。红了再改回共享，断言转绿。
- **真机复测（用 `app/data/llm_usage.jsonl` 或手测五句）**：
  - 重点看「食堂几点关门」（3 次模型）端到端从 ~9437ms 下降、「今天几号」（2 次模型）从 ~6315ms 下降。
  - 掐一次简单请求的握手成本，目标接近复用的 **679 / 574 / 450 ms** 档（而非新建的 2s 档）。
  - 对照：规则层 0 模型的话（「帮我安排游泳」）本刀无影响，作基线。
- **多 worker sanity**：开多 worker 跑通，确认无 "Event loop is closed" / "Client is closed" 报错、
  每个 worker 各自一个 client、请求正常返回。
- **交付纪律**：细粒度 commit（① 加共享 client + 懒初始化 ② 改 chat 发请求段 ③ 加关闭钩子）
  + 打快照 + 重启 8000 复测。

---

## 5. 明确不做（防止范围蔓延）

- 不碰 `intent.py` 判定逻辑（那是刀 3）、不碰 `polish.py`（刀 1 已 B）、不碰流式（已有 `07` 单）。
- 不改 `base_url` 取值逻辑、不改 SSRF 规则、不改 `get_llm_config` 取配置路径。
- 不做 A 方案的 BackgroundTasks / 轮询（那是刀 1 的备选，已否）。

---

## 6. 与刀 1（B）的关系

两刀**互补、可独立落地、互不冲突**：
- 刀 1 B：把润色从关键路径摘掉（卡片类不润色、列待办 ≤1s）→ 砍掉 3~4s 白等 + 消不一致。
- 刀 2：把每次往返的握手从 ~2s 降到 ~0.5s → 多轮链路（意图+引擎+工具）上限再省 ~4s。
- 叠加后「食堂几点关门」9437ms 路径：握手 -4.2s 且润色不在关键路径，量级应落到 4~5s 档。
- 建议落地顺序：**先刀 1（纯删等待、零风险），再刀 2（纯传输层、护栏零影响）**。

---

## 7. 给实现 AI 的纪律提醒（来自项目接手指南）

- **整族扫**：`grep -rn "AsyncClient" app/llm/` 确认全包只有 `client.py:120` 一处，改这一处即覆盖全部流量，
  不要只改被投诉那条路径。
- **注释里的例子必须实测过**：§0 的 2188/1967/1987 vs 679/574/450 与五句 ms 都是今天用量日志真量出来的，不是估。
- **变异测试 + 真机复验表 + 编号红线**：先写断言再改，变异必须红，改回必须绿。
- **学生面前不暴露技术内脏**：本刀不新增任何报错白屏路径；连接复用失败 httpx 会自动新建，现象与现状一致。
