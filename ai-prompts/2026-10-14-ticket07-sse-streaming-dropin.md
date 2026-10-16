# 07 · 流式输出（SSE）—— 最终落地施工单

> 给落地 AI（Qoder）的 drop-in 施工单。**只读这文件，不要改其它 prompt。**
> 这是「降延迟」系列里**唯一针对"感知延迟"的一刀**——09/10/11 已把总耗时砍下来，
> 但**判定层 p50 4.2s 是模型固有硬地板**（实测 语义判定 p50 3863ms / max 4219ms，11 文档已钉死），
> 总耗时压不到那以下；流式让首字 1~2s 即显，把"卡死"变"在想"。**总耗时不变，感知质变。**
> 本刀**不删任何 LLM 调用、不删任何护栏逻辑**，只把传输层从"等整包 JSON"改成"边生成边推 token"。

---

## 一、目标（一句话）
让 `/api/chat` 用 SSE 把模型回答**逐 token 推给前端**，用户看到字一个个冒出来；
结构化数据（确认卡 `options`、`trace`、守卫修正后的最终 `answer`）在流水线跑完、守卫跑完之后，
用最后一个 `done` 事件一次性带上。

---

## 二、为什么做（实测依据，今天 1939 行用量日志）
- 现状：`/api/chat` 等**整条流水线跑完**才回一个 JSON，用户全程看转圈。
- 今天实测 `app/data/llm_usage.jsonl`（1939 行筛 2026-10-14）：语义判定 p50 3863ms / max 4219ms；
  单次 LLM 中位 ~3.4s、p90 ~5.7s、最坏贴 30s 读超时。
- 典型"加待办"链路 = 语义判定(~3.8s) + 引擎 2 步(~6s) + 润色(~0.9s 已砍超时) ≈ **10s 才出第一个字**。
- 09/10/11 之后：握手、润色白等已砍，但**判定层 4.2s 地板压不下**（11 文档已证明：A/B 都压不下，C 已跳过）。
- **流式是剩下的唯一感知杠杆**：首 token 约 1~3s 即显字，体感从"卡死"变"在想"。

---

## 三、改动范围（只动传输层 + 引擎产出方式）

| 文件 | 落点（已核对当前代码） | 改什么 | 性质 |
|---|---|---|---|
| `app/llm/client.py` | `chat()` 在 :100-150 | 新增 `chat_stream()`（`stream:true`，解析 SSE delta，yield token，末尾抽 usage） | 新增，**不改 `chat()`** |
| `app/llm/gateway.py` | `chat()` 在 :228-285 | 新增 `ModelGateway.stream()` + 模块级 `stream()`，透传 token、末尾记一笔账 | 新增，**不改 `chat()`** |
| `app/agent/engine.py` | `run()` 在 :60-95 | 改**异步生成器**：每步 yield `token`/`trace`，末步 yield `result`（原始结果 dict） | 改签名/结构 |
| `app/main.py` | `/api/chat` 在 :1190；`engine.run` 调用在 :3452；**后置守卫块 :3478-3680** | `/api/chat` 改 `StreamingResponse`；用生成器包住 `engine.run`，**守卫块跑完后再发 `done`** | 改出口 + 守卫时序 |
| `app/static/student.html` | `/api/chat` fetch 在 **:1801（sendMsg 主路径）** 和 **:2129（上传课表→chat 自 fetch 路径）** | 两处都从 `.json()` 改成 `fetch`+`getReader()` 逐块读、增量渲染；收到 `done` 再渲染卡片 | 改前端（**两处**） |

**不动**：`classify` / `classify_with_llm` / `router.route` / `polish_reply` 仍走原 `chat()`（非流式）。
`/classify` 端点（student.html:2353）只读判定、不进聊天，不受影响。

---

## 四、各落点改法要点

### 4.1 `app/llm/client.py`（新增 `chat_stream`，不改 `chat`）
- 保留 `async def chat(...)` 一字不改（classify/polish/router 都靠它，测试 `patch_llm` 也桩它）。
- 新增 `async def chat_stream(self, messages, tools=None, override=None)`：
  - 复用 `_config()`、`check_base_url_ssrf()`（**SSRF 闸门必须过**，和 `chat` 一致；学生自带 base_url 也走同一闸）。
  - `payload` 加 `"stream": True` 与 `"stream_options": {"include_usage": True}`（末尾 chunk 带 usage，记账不丢）。
  - 用 `httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=3.0))` 发 POST（流式读超时放宽，connect 仍 3s 防内网探测卡死）。
  - 逐行读响应体，按 SSE 解析：`data: {...}` → `json.loads` → `choices[0].delta.content`（可能为空）→ yield 该片段；`choices[0].delta.tool_calls` 也要 yield（给引擎拼工具调用）。
  - 末尾 chunk 抽 `usage` 存到 `self.last_usage`（网关记账用）。
  - 任何异常：**向上抛**，让上层发 `error` 事件降级，别静默 swallow。

### 4.2 `app/llm/gateway.py`（新增 `stream`，不改 `chat`）
- 保留 `async def chat(...)` / 模块级 `chat(...)` 不改。
- 新增 `async def ModelGateway.stream(self, messages, tools=None, override=None)` 与模块级 `stream(...)`：
  - 内部调用 `client.chat_stream(...)`，对每个 token 做 `normalize_message` 同款处理（工具调用块标准化）后 yield。
  - `started = time.perf_counter()`；末尾用 `log_llm_usage(... latency_ms=...)` 记一笔（和 `chat` 的 finally 同口径），**记账不能因改流式而漏**。
  - `user` 字段沿用（学生会话 id / 内部功能名）。

### 4.3 `app/agent/engine.py`（`run` 改异步生成器）
- 现状 `run()` 在 :60 返回 dict，:95 `await self.llm.chat(...)` 拿模型消息。
- 改为 `async def run(...)` 异步生成器，`yield` 事件字典：
  - **每步先** `yield {"k": "trace", "v": {...}}`（把现有 `self.trace.append` 的内容即时推出，前端可渐进画"思考步骤"）。
  - 调 LLM：**优先** `await self.llm.stream(...)`；若 `self.llm` 没有 `stream`（测试桩只给了 `chat`），**回退** `await self.llm.chat(...)` 并把整段 content 作为一个 `token` 块 yield（保证现有 `patch_llm` 测试仍绿）。
  - 若该步模型返回 `tool_calls`：按现有逻辑执行工具、`yield` 工具结果 trace（**不流内容**）；若该步返回 `content` 且无 `tool_calls`：这是最终回答，**逐 token yield `{"k": "token", "v": fragment}`**。
  - 循环结束（或命中 `max_steps=6` 兜底）后，把现有 `save_pending`、`_finish` 逻辑跑完，最后
    `yield {"k": "result", "v": {answer, options, awaiting_choice, trace}}`——这是**引擎原始结果**，尚未过 `_chat_impl` 后置守卫。
- `max_steps=6` 不变；工具执行逻辑（`tool.run_async` / 抓 `propose_slots` / `propose_timetable_change` / `__proposal__`）一字不改，只是从"等结果"变成"边跑边 yield trace"。
- **测试兼容（关键，别让 44 份自检红）**：保留一个同步包装 `def run_collected(self, ...)`（或生成器内 `if not hasattr(self.llm,'stream')` 回退），让非流式调用方/测试仍拿到和今天一样的 dict；`patch_llm` 继续桩 `chat`，流式测试再补一个 `stream` 桩。

### 4.4 `app/main.py`（`/api/chat` + 守卫时序，这是最易写错处）
- `engine.run` 调用在 **:3452**；其返回结果在 **:3478-3680** 一大段**后置守卫**里被再处理
  （`final_answer = result["answer"]` → `_no_card_answer()` / `claims_proposal_sent` + `has_concrete_span` → `_no_card_answer()` /
  `looks_like_tool_echo(final_answer)` / 写库漏斗 / `awaiting_choice` 在 :3680）。
  **这些守卫是"引擎 claim 守卫"族（与润色自守卫 polish.py:107-115 是不同两族，11 文档已拆）**，绝不能因流式而删或绕过。
- 改法：
  - `/api/chat`（:1190）`return StreamingResponse(_chat_impl_stream(req), media_type="text/event-stream")`。
  - `_chat_impl_stream` 先跑 `_chat_impl` 里的**判定/路由/护栏**那段（:1280 早退门、`physio_state_only` / `unsupported_period` / `wants_done_todo` / 问句守卫等确定性否决，**一字不改**，它们不需要流式）。
  - 拿到 module/engine 配置后，`async for ev in engine.run(...)`：把 `token`/`trace` 即时转成 SSE `data:` 写出；
    收到 `result` 事件（引擎原始结果 dict）后，**先跑 :3478-3680 那整段后置守卫**（用 `result` 当输入），
    **守卫跑完之后再 yield 最终的 `done` 事件**（携带守卫修正后的 `answer` + `options` + `awaiting_choice` + `trace` + `module` + `session_id`）。
  - 中途异常（引擎抛 LLMError 等）：`yield {"k":"error","v": _say_llm_error(...)}`，前端据此把气泡替换成友好报错，**绝不**留半截残缺回复。
  - `options` / `awaiting_choice` 在 `done` 里给，和现在 JSON 里的字段对齐，前端渲染确认卡的逻辑不用改。
- **关键时序**：流式 token 是引擎原始回答的"预览"；`done` 携带的是**守卫修正后**的权威答案。若守卫改写了答案（如 `_no_card_answer()`），前端在收到 `done` 时**用 `done.answer` 覆盖气泡内容**。

### 4.5 `app/static/student.html`（**两处** fetch 都要改）
- **主路径** `sendMsg`，fetch 在 **:1801-1806**：`const data = await resp.json();`。
- **上传课表→chat 自 fetch 路径**，fetch 在 **:2129-2132**（注释 :2138-2143 专门提醒"凡是自己打 /api/chat 的都要渲染 options"）。
- 两处都改成：
  - 先建一条空的 bot 气泡；
  - `const reader = (await fetch("/api/chat", {...})).body.getReader();`，`TextDecoder` 逐块读；
  - 按 SSE 切分（以 `\n\n` 或 `data:` 行），每行 `JSON.parse`：
    - `k=="token"` → 把 `v` 追加进气泡文本并 `scrollIntoView`；
    - `k=="trace"` → 渐进渲染"思考步骤"（可选，先做文本气泡即可，轨迹可后置）；
    - `k=="done"` → **用 `v.answer` 覆盖气泡**（守卫修正后的权威答案），再用 `v.options` / `v.awaiting_choice` 渲染确认卡、挂确认条（**复用现有 `options`→卡片的代码**）；
    - `k=="error"` → 把气泡内容替换为 `v` 友好报错。
  - 读完后，把 `done.answer` 也 `append_conversation`（现有逻辑：把 assistant 回复写进会话历史），保证刷新后还在；**token 预览阶段不写历史，避免重复写**。
- `:2129` 那条路径同样要接 SSE 读取 + 渲染 options，不能只 `.json()`。

---

## 五、SSE 事件协议（前后端约定，写死一处）
```
data: {"k":"token","v":"你"}
data: {"k":"token","v":"好，"}
...
data: {"k":"result","v":{"answer":"...","options":[...],"awaiting_choice":false,"trace":[...]}}   ← 引擎原始结果，内部用
data: {"k":"done","v":{"answer":"（守卫修正后）你好，已帮你…","options":[...],"trace":[...],"awaiting_choice":false,"module":"planner","session_id":"sxxxx"}}
```
- 每行一个 `data:`，以空行分隔（标准 SSE）。前端只认 `k` 字段：`token` 增量拼接，`done` 收尾带结构化信封并覆盖气泡，`error` 降级。
- 不用 `event:` 字段，减少解析复杂度。`result` 事件仅供后端内部衔接（守卫），前端可不处理它。

---

## 六、护栏清单（改的时候一条都不能破）
1. **不改 `chat()`**：classify / polish / router 的非流式调用照旧，别为"统一"把它们也改流式。
2. **两族守卫全保留（最容易漏的就是这里）**：
   - 润色自守卫 `polish.py:107-115`（`has_promise` + JSON 守卫）—— 本刀不碰 polish，照旧；
   - **引擎 claim 守卫**（`main.py:3478-3680`：`claims_proposal_sent` + `has_concrete_span` → `_no_card_answer()`、`looks_like_tool_echo`、写库只走确认漏斗）——**`done` 必须在它们跑完之后发**，否则"引擎说已帮你加好"的幻觉会透传给用户。
   - `_chat_impl` 里的确定性否决（`physio_state_only` / `unsupported_period` / `wants_done_todo` / 问句守卫）原样保留。
3. **记账不丢**：`gateway.stream` 末尾仍 `log_llm_usage`，`llm_usage.jsonl` 照常增长。
4. **SSRF 闸门不漏**：`chat_stream` 必须过 `check_base_url_ssrf`（学生自带 base_url 也走同一闸）。
5. **失败可降级**：流式中途断线/超时/401/429，必须能发 `error` 事件给用户一句人话，不白屏、不残留半句。
6. **确认卡不丢**：`options` / `awaiting_choice` 必须在 `done` 里带全，前端渲染确认条的逻辑不变。
7. **守卫优先于显示**：`done.answer` 是守卫修正后的权威答案，**必须覆盖**流式预览的 token 文本。

---

## 七、纪律（给实现 AI 的红线）
- **整族扫**：全仓搜 `engine.run(`、`gateway.chat(`、`client.chat(`、`/api/chat` 的所有调用方（含 :1801 / :2129 两处前端、测试 mock `patch_llm`、后台测连通），确认流式改造不波及非流式路径；`/classify` 端点不受影响。
- **测试兼容**：`patch_llm` 桩的是 `chat`；engine 生成器必须能回退到 `chat`（无 `stream` 时不报错），并提供 `stream` 桩给流式单测。44 份自检 + `pytest_gate` 必须仍绿。
- **变异测试**：构造"模型中途抛错""模型只回 tool_calls 不回 content""超长回答""守卫改写答案（如 `_no_card_answer`）"四种输入，确认前端不崩、能降级、`done.answer` 是守卫后版本。
- **真机复验表**：① 浏览器发一句加待办，看字是否渐进出现 + 确认卡是否照常弹；② 发一句纯问答，确认无卡片只有渐进文本；③ 断网/填错 key，确认出友好报错而非白屏；④ 看 `llm_usage.jsonl` 仍有新记录（含 `user="内部:语义判定"` 与聊天会话）。
- **回滚**：若流式引入回归，最小回滚 = `/api/chat` 改回 `JSONResponse(_chat_impl_collected(...))` 原样返回（引擎用 `run_collected` 把事件聚成 dict 即可），前端两处 fetch 回退到 `.json()`。

---

## 八、验证清单（交付前自测）
- [ ] `curl -N` 打 `/api/chat`，肉眼看到 token 分块到达（不再是等 10s 一次性吐）。
- [ ] 浏览器实测：首字出现时间 < 4s（之前 ≈ 整链路 10s）。
- [ ] 确认卡（`options`）在回答后正常弹出，点确认仍能写库；`done.answer` 与守卫修正一致。
- [ ] `/classify`、后台语义判定、润色路径未受影响（非流式照旧）。
- [ ] `llm_usage.jsonl` 流式调用仍记了 latency/usage（含 内部:语义判定）。
- [ ] 断网/错 key：前端显示友好报错，无白屏、无半句残留。
- [ ] 现有 44 份自检脚本 + `pytest_gate` 仍绿（mock 客户端兼容：`chat` 桩保留 + 新增 `stream` 桩）。
- [ ] 守卫验证：构造让引擎 claim 守卫触发的输入（如"刚才那句"回声），确认 `done.answer` 是 `_no_card_answer()` 之类修正版，而非引擎原话。
