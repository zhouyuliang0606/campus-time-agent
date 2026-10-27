# 70 · 收尾四小笔：一条噪音、两句断言、一笔引用

> 施工单原件。**#69 已冻结产品代码，本单不碰 `app/`、不碰 `app/static/`、不碰任何既有判据。**
> 前置：`08db609`（#69 追记，全量 `5125 / 0`，89 批）。
> 四笔全部落在 `tests/` 与 `ai-prompts/`，属测试脚手架与交付文档 —— 与冻结口径不冲突。
>
> **本单是写作侧出的单，不是执行方。** 下面所有行号都对着 `08db609` 现查过；
> 落地前请自己 `git log -1` 确认 HEAD 没漂。

---

## 〇 现场事实（逐条现查）

### 0.1 那条 `ValueError: I/O operation on closed file` 的完整来源链

先给结论：**它不是失败，是 `truncate` 剧本自己剪线、Python 标准库在后台收尾时的抱怨。** 链条四段：

| 段 | 位置 | 事实 |
|---|---|---|
| ① 剧本故意剪线 | `tests/test_49_streaming_sse.py:147-155` | `truncate` 模式开头就写明「不给 `[DONE]`、也不发结束 chunk，直接关连接 → httpx 当协议错误抛出来」，代码是 `self.close_connection = True` + `self.wfile.close()`，外面裹了 `try/except: pass` |
| ② 假服务器是库自带的 | `tests/test_49_streaming_sse.py:176-179` | `_serve_stream()` 里 `srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StreamServer)`，起一个 daemon 线程跑 `serve_forever` |
| ③ 收尾仍要 flush 已关的流 | `http.server` / `socketserver` 内部 | `handle_one_request` 末尾会 `self.wfile.flush()`，`StreamRequestHandler.finish()` 收尾也会 flush；`wfile` 已被 ① 关掉 ⇒ `ValueError: I/O operation on closed file` |
| ④ 库把 traceback 打到 stderr | `socketserver.BaseServer.handle_error` | `ThreadingMixIn.process_request_thread` 的 except 分支调它，默认实现前后各打一行 40 个 `-`，中间 `Exception occurred during processing of request from ('127.0.0.1', <port>)` + 整条 traceback |

**关键细节（决定了改哪里）**：`handle_error` 是**服务器对象**的方法（`BaseServer` 上），**不是 handler 的方法**。
`_StreamServer` 继承的 `BaseHTTPRequestHandler` 根本没有这个钩子。所以静音点必须加在**服务器类**上，
改成给 handler 加方法不会有任何效果。

**还有一半已经静音了，这次是补齐对称**：`tests/test_49_streaming_sse.py:166-167` 已经有

```python
    def log_message(self, *a):
        pass
```

把**访问日志**吞掉了。缺的正好是另一半 —— `handle_error`。补上之后这四段链条里 ④ 就不再出声，①②③ 一个字节不动。

**旁证：另一台假服务器没有这个问题，也别去动它。** `tests/test_47_shared_async_client.py:83` 同样起了
`http.server.ThreadingHTTPServer`，但它的 `_CountingServer` 没有「剪线」剧本，不会制造这个异常。**本单只改 `test_49`。**

### 0.2 `README` 里那句「每个快照都包含 … commit 哈希」站不住

`ai-prompts/README.md:328-332` 现文：

```
## 评审怎么看

每个快照都包含：用户原话 Prompt（摘录）、AI 的代码研判与决策、具体改动文件、
测试护栏结果与 commit 哈希。把任意快照文件与其 commit 对照，即可还原那一步的协同过程；
把「施工单原件」与其快照对照，则能看出**哪一方的判断在什么时候被实测推翻了**。
```

「**每个**」是绝对断言。现数（`08db609` 上跑，命令见下）：

- 会话快照（排除 `-dropin` / `-drop-in` / `-brief` / `README`）共 **137** 份；
- 正文**一个 7 位以上十六进制哈希都没有**的有 **25** 份，**最晚到 `2026-10-13`**；
- 最薄的一份是 `2026-09-27-panel-chat-visible.md`（**1557 字节**），末尾原文只有一行
  `- 本批 1 个代码 commit（student.html）` —— 没有哈希。

也就是说 **18% 的快照兑现不了这句承诺**，而且那 25 份恰好是评委随手翻最容易翻到的早期文件。
它把整套的信用一次性押上去了，代价与收益完全不成比例。

现数命令（落地时请自己复跑，别照抄上面的数字）：

```bash
cd ai-prompts && python -c "
import glob,re,os
fs=[f for f in glob.glob('*.md') if not re.search(r'dropin|-drop-in|brief|README',f)]
noh=[f for f in sorted(fs) if not re.search(r'[0-9a-f]{7,40}',open(f,encoding='utf-8',errors='replace').read())]
print('快照',len(fs),'零哈希',len(noh),'最晚',max(noh)[:10] if noh else '-')
"
```

### 0.3 `ai-prompts/README.md` 的原件后缀约定只写了一种写法

`:263` 现文：

```
文件名取原件日期 + 主题（`-dropin` / `-brief` 结尾即本文所说的"原件"），**正文一字未改**。
```

实测三种后缀都在用：

| 后缀 | 份数 | 起止 |
|---|---|---|
| `-dropin.md` | **13** | `2026-10-14-ticket05…` ~ `2026-10-22-ticket50…` |
| `-drop-in.md` | **15** | `2026-10-22-ticket54…` ~ `2026-10-27-ticket69…` |
| `-brief.md` | **1** | `2026-10-16-ticket24-two-decisions-brief.md` |

按约定那两个后缀去筛，只筛得到 **14** 份（13 + 1），**漏掉 15 份**（多一个横杠的那一半）。
下面「用户侧施工单原件」那张表倒是逐份写对了文件名，所以是**约定文字漏写**，不是表错。

### 0.4 `#68` 的引用在仓库里不可达（四笔里最硬的一笔）

`ai-prompts/2026-10-27-ticket69-code-freeze-checkup-drop-in.md` 有 **4 处**引用 `#68` 的小节：

- `:187` 「不收 `files.py` 那 5 处硬取（**#68 §2.2**）」
- `:188` 「不统一 `main.py:3415` 第四条路（**#68 §三**）」
- `:190` 「不删 `/api/todos/month`（**#68 §2.2**）」
- `:179` 「#66 之后连续三单（#66/#67/#68）…」

而 `grep -rn "68_67追记" ai-prompts/` **零命中** —— 68 号单的原件名在仓库里根本不存在，
评审顺着 `#68 §2.2` 点过去，只会发现仓库里没有这份文件。

**这是「引用指向仓库外文件」，不是「少入库一份」的问题。** 差的不是文件，是**可达性**。

---

## 一、四笔修法

### 一.1 `tests/test_49_streaming_sse.py`：给假服务器补 `handle_error`（推荐窄静音）

**第一步**，文件头补一个 import（现在 `:21-26` 只有 `asyncio` / `http.server` / `json` / `os` / `threading` / `time`，
**没有 `sys`**，漏了会 `NameError`）：

```python
import sys
```

**第二步**，在 `_serve_stream()` 前面（`:176` 上方）插类：

```python
class _QuietServer(http.server.ThreadingHTTPServer):
    """只吞 truncate 剧本自己制造的收尾异常，其它异常照旧打到 stderr。

    见 _StreamServer 的 truncate 分支：它故意 close 掉 wfile 模拟断流，
    随后标准库收尾时仍会 flush 那条已关的流，抛 ValueError(closed file)。
    那不是被测代码的问题，把 traceback 打出来只会让人以为测试挂了。
    访问日志那一半已经由 _StreamServer.log_message 静音，这里补齐另一半。
    """

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, ValueError) and "closed file" in str(exc):
            return
        super().handle_error(request, client_address)
```

**第三步**，把 `:177` 的创建点换掉（全仓只有这一处，`test_47:83` 那处**不动**）：

```python
# 改前
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StreamServer)
# 改后
    srv = _QuietServer(("127.0.0.1", 0), _StreamServer)
```

#### 为什么是窄静音、不是 `pass` 了事

宽版只要三行（`def handle_error(self, *a): pass`），看起来更省事。**但它会把这台假服务器将来任何真异常一起吞掉。**
`_chunk()` / `_end_chunk()` / 那几段剧本都在 handler 里跑，将来有人改测试改出 `AttributeError`、`BrokenPipeError`，
`handle_error` 打出来的 traceback 是唯一的线索 —— 吞掉之后表现就是「测试莫名其妙红/绿，一行日志都没有」。

`log_message` 那种宽静音是安全的，因为访问日志永远不重要；`handle_error` 不是，它可能承载真故障。
两者形状相似、性质不同，**不要因为「都一样静音」就抄同一份写法**。

（如果执行方认为窄版值不值多这两行有异议，**宽版也可以接受**，但请在注释里明写「已知会吞掉本测试内的一切
handler 异常，排查时先临时恢复父类实现」—— 把代价写出来，别让它变成静默的坑。）

### 一.2 `ai-prompts/README.md:330`：把「每个」改成有时间前提的实话

替换 `:330-332` 三行（含前面那一行空行后的正文）：

```
从 `2026-10-14` 起的快照都逐笔写全 commit 哈希，每份包含：用户原话 Prompt（摘录）、AI 的代码研判与决策、
具体改动文件、测试护栏结果与 commit 哈希。把快照与其 commit 对照，即可还原那一步的协同过程；
把「施工单原件」与其快照对照，则能看出**哪一方的判断在什么时候被实测推翻了**。

> 早期那一段节奏不一样：`2026-09-26` ~ `2026-10-13` 的二十余份只记到「本批 N 个代码 commit」与改动文件名，
> 没写哈希 —— 那时一天十几个小提交，逐笔贴哈希反而没人核。要定位它们，用快照里点到的文件反查：
> `git log --oneline -- <快照里出现的文件名>`。份数与最晚日期现数，别照抄这里。
```

**为什么这样改而不是回填 25 个哈希**：`2026-09-27` 一天就有五份快照、底下十几个 code commit，
靠 `git log --since` 认领很容易认错 —— 而**认错的哈希比没有哈希更糟**（评委一核就是假的）。
冻结前不回填。真要补，那是换个时间、逐份对着 `git show` 做的活，不该塞进收尾单。

### 一.3 `ai-prompts/README.md:263`：约定段补上第三种后缀

替换 `:263`：

```
文件名取原件日期 + 主题（以 `-dropin` / `-drop-in` / `-brief` 结尾即本文所说的「原件」；`-dropin` 与
`-drop-in` 是同一个意思的两种写法，早期写作前者、`#54` 起写作后者，改名会牵连索引与已有引用，故按原样保留），
**正文一字未改**。
```

**为什么不统一文件名**：29 份原件里有 15 份被下面那张表按全名引用、README 索引里也逐份写过，
改名的连带修改面比收益大得多。**约定服从事实，比事实迁就约定便宜。**

### 一.4 `ai-prompts/README.md:308` 那段「几份没入库的」：补上 68 号

在那段 blockquote 列表末尾（`:319` 「那属于另一件事，不混进本单。」之后）追加一条，格式照现有条目：

```
> - `68` 号（#67 追记的四条批示：`dropped` 零消费者 / `/api/todos/month` 无人调 / 第四条路三口径 / B4 计数判据）
>   **是判断单，不单独入库**：它一份独立快照都没有，因为它的落地就是 `69`，`69` 的原件里已逐条引用了它的
>   §2.2 与 §三、`69` 的快照里也逐条复核过。所以 `#68 §x` 这类引用指的是**那份判断单的小节**，不在本目录。
>   要读完整推理（比如「为什么 `dropped` 只改注释、不加前端」），口径是：结论在 `69`，取舍理由在 `68`。
```

**如果执行方倾向另一条路**（把 68 原件也按 `-drop-in.md` 入库、README 索引加两行、`69` 里的 `#68 §x` 就自然可达），
也**同样成立**，而且可达性更彻底。两条路的差别只是：
- 补说明（推荐）：**动 1 处、4 行**，代价是判断的完整推理链仍留在仓库外；
- 入库 68：**动 3 处**（原件 + 索引 + 表），好处是推理链进仓库，代价是「哪些是施工单」这条线变模糊 ——
  而 `:308` 那段「收录规则」第 1 条写的就是「只收真正交给过执行侧的**施工单**，并与快照同批入库」，
  68 既不是施工单、也没有配套快照，**按既有规则本就该不入库**。

推荐补说明，理由是**别为了修可达性去破自己定的收录规则** —— 规则破了，以后每份判断稿都要争一次「入不入」。

---

## 二、判断与理由

### 2.1 一.1 到底该不该修 —— 修。理由不是洁癖，是**现场解释成本**

反对修的理由很实在：这只是 stderr 上一条 traceback，该批断言本来就绿，改测试脚手架还要重跑一遍。
如果这是纯本地开发，不修是对的。

但本项目的处境不同：**这是要现场演示、要评委看日志的比赛交付。** 评委看到的那一幕是
「跑全量测试，屏幕刷到最后突然一段红色 traceback」—— 你只有两个选择：
当场解释「这是假警报」，或者不解释（那在评委眼里就是「有报错」）。
**用一条假警报换一次现场解释，不划算。** 而修它的成本是 3 处、约 12 行、不碰任何被测代码。

### 2.2 一.2 为什么必须改，而不是「反正没人看」

`:328` 那个小标题叫「评审怎么看」，是全套快照里**唯一一段教评委怎么核的话**。这段话的分量不在描述、在**承诺**。
承诺兑现不了 18%，一旦被抽查命中，评委的合理推论不是「早期几份偷懒」，而是
**「这套材料里的其它描述大概也不能照字面信」** —— 那正是这套材料唯一不能亏的东西。

改成有时间前提的实话之后，同一个抽查反而变成加分项：评委看到的是「作者自己把边界交代清楚了，
还给了反查命令」。**诚实本身是可核验的，这一笔不亏。**

### 2.3 一.3 是纯文字订正，没有取舍空间

约定与事实不一致，只有两种修法：改约定，或改事实。改事实要动 15 个文件名 + 索引 + 表，
改约定动 1 行。**事实贵、文字便宜，改便宜的。**

### 2.4 一.4 的取舍已在修法里写清

推荐「补说明」不「入库原件」，核心理由是**不为修可达性破自己定的收录规则**。
代价照实认：判断的完整推理链仍在仓库外，评审只能看到结论（`69`）而看不到推导（`68`）。
这个代价可以接受，因为 `69` 把那四条批示的**结论与理由一句话**都写进去了
（「崩比错便宜」「行为变更，先回答『缺 id 算不算一道待办』」），够用。

---

## 三、验证

### 3.1 一.1 的验证（必须真跑，不能只看代码）

**基线（改之前先抓一次，拿到靶子）**：

```bash
cd tests && python test_49_streaming_sse.py > /tmp/t49.out 2> /tmp/t49.err; echo "rc=$?"
grep -c "Exception occurred during processing of request" /tmp/t49.err   # 期望 1（或 ≥1）
```

**改之后**：

```bash
cd tests && python test_49_streaming_sse.py > /tmp/t49b.out 2> /tmp/t49b.err; echo "rc=$?"
grep -c "Exception occurred during processing of request" /tmp/t49b.err   # 期望 0
grep -c "I/O operation on closed file" /tmp/t49b.err                      # 期望 0
```

**非空正控（这条不能省）**：仅凭「stderr 干净」不能证明测试跑到了 `truncate` —— 万一那两条断言被跳过，
stderr 天然干净。所以必须同时确认 **B 批 10 条 `c.check` 全绿**，尤其下面这两条，
它们只在 `truncate` 之后才有值（`tests/test_49_streaming_sse.py:360` / `:362`）：

- 「中途断线：已吐的那一块留着，不补整段」→ `toks4 == [PIECES[0]]`
- 「中途断线要把错误上抛（上层才能发一句人话，不是留半句当没事）」→ `kind4 == "unreachable"`

**两条都要绿。** 只要有一条变红，说明静音把该冒出来的错误也吞了（比如 `kind4` 拿不到异常类型），
**立刻回退**，不要在窄静音上继续加工。

### 3.2 一.1 的变异（每条都要能变红，打在判据上游）

| # | 变异 | 期望 |
|---|---|---|
| V1 | 把 `_QuietServer.handle_error` 的 `return` 改回 `super().handle_error(request, client_address)` | traceback **重现**（`grep -c` 回到 1） |
| V2 | 把 `:177` 换回 `http.server.ThreadingHTTPServer(...)` | 同上重现 —— 证明替换点唯一、且必须是这个类 |
| V3 | 在窄版里把 `isinstance(exc, ValueError)` 去掉、只留 `"closed file" in str(exc)` | 仍绿（说明判据冗余但无害）；**再进一步**改成 `exc is not None` 全吞 → 见 V4 |
| V4 | 让 `_chunk()` 抛一个 `AttributeError("probe")`（临时改一行，测完撤掉） | 窄版下**必须仍然打到 stderr**；若没打 ⇒ 说明静音写宽了，回一.1 收窄 |

**V4 是这一笔唯一真正值钱的验证**：它钉住的不是「能不能静音」，而是「**静音会不会误伤**」。
V1/V2 只证明静音生效，随便写成 `pass` 也能过；只有 V4 能区分窄版和宽版。
所以 **V4 不许省，也不许用「看代码觉得对」代替**。

### 3.3 一.2 / 一.3 的验证（可量化，前后对比）

```bash
cd ai-prompts
# 一.2：改后不应再有绝对断言
grep -c "每个快照都包含" README.md          # 期望 0
# 一.2：新写的反查命令必须真能定位到那 25 份中的任意一份
git log --oneline -- student.html | head -3                     # 应有多行
git log --oneline -- app/static/student.html | head -3          # 同上（带不带目录都试一次）
# 一.3：按新约定三种后缀筛，应得 29 份
ls *-dropin.md *-drop-in.md *-brief.md 2>/dev/null | wc -l       # 期望 29
```

**一.3 的前后对比要留一笔**：改前按约定只筛得到 `13 + 1 = 14` 份，改后 `29` 份 —— 这个差值本身就是
「约定曾经漏掉一半」的物证，比单子上写「实测 15 份」有力。

### 3.4 回归

- `tests/test_49_streaming_sse.py` 单独跑：B 批 10 条全绿（见 3.1）。
- **全量 `python tests/run_all.py`：期望仍 `5125 / 0`（89 批）**。本单只动测试脚手架的一个类 + 两份文档，
  **项目数一条都不该变**；变成 `5126` 说明顺手加了断言（不在本单范围），变少说明有批没跑起来。
- `test_47` 那条假服务器**必须一条不动**：跑一遍 `test_47` 确认仍绿（证明「只改 `test_49`」这句是真的）。

---

## 四、不做的事（逐条给理由，别顺手做了）

1. **不回填那 25 份的 commit 哈希。** 早期一天十几个小提交，靠 `git log --since` 认领极易认错，
   而**认错的哈希比没有哈希更糟**（评委一核就是假的）。要补就另开一单、逐份 `git show` 对着来。
2. **不补写 `2026-09-27` 那批薄快照（1.5~4 KB）的内容。** 补写等于事后重写历史，
   反而削弱「这个过程真实发生过」。薄本身就是那时节奏的证据，留着。
3. **不给 `tests/test_47_shared_async_client.py` 的 `_CountingServer` 加同样的静音。** 它没有
   `truncate` 剧本、不制造这个异常。给没有问题的地方加防御，等于给将来的排查埋噪音。
4. **不改 `truncate` 分支本身**（`self.wfile.close()`）。那是它模拟「流到一半断线」的**唯一手段**，
   动了就等于把被测场景改没了 —— 静音点是「库怎么抱怨」，不是「剧本怎么演」。
5. **不统一 `-dropin` / `-drop-in` 文件名。** 29 份原件里有 15 份被 README 表按全名引用、索引里也逐份写过，
   改名的连带面远大于收益。约定改文字，不改事实。
6. **不把 68 号原件入库**（除非执行方选 2.4 的另一条路，那也可以，但选了就别两头都做）。
7. **不动 `app/` 下任何一个字节。** 本单是交付层收尾，跑完 `git diff --stat` 应该只看到
   `tests/test_49_streaming_sse.py` 与 `ai-prompts/README.md` 两个文件。

---

## 五、给执行方的三句

1. **一.1 的 V4（故意抛个 `AttributeError` 看它有没有被吞）是这单一半的价值所在。**
   只做 V1/V2 的话，`handle_error` 写成 `pass` 也全绿 —— 那样你验证的不是「窄静音对不对」，
   只是「静音生效了没有」。别省这一条。
2. **一.2 改完请把「按老约定筛只得 14 份、按新约定得 29 份」这个前后对比记进快照。**
   它比任何形容词都能说明「约定曾经漏掉一半」，而且是可复跑的。
3. **四笔建议合成一笔提交**，提交信息里点明「只动 `tests/` + `ai-prompts/`，产品代码零改动」——
   #69 刚立了冻结口径，这一笔要让下一个人一眼看出没破冻。跑完给 `git diff --stat` 的实际输出，
   只有那两个文件才对。
