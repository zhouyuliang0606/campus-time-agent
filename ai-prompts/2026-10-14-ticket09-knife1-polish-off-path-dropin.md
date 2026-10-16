# 09 · 第一刀 B：把润色从「关键路径白等 3~4s」里摘掉

> 给落地 AI（Qoder）的 drop-in 施工单。**只读这文件，不要改其它 prompt。**
> 这是「三刀降延迟」的第一刀，选 **B 方案**（不是 A、不是 C）。
> 本刀**只删等待、统一出口，不引入任何新通道**（无 BackgroundTasks / 无 SSE）。

---

## 0. 一句话目标

实测今天 1939 行里，润色在关键路径上白等：97 次里有 20 次 `status=error`，每次吃满
`POLISH_TIMEOUT=4.0s`，且拿到的是本来就能立刻给的模板话术。同一类「系统已算好事实」的出口，
因为「润色插在谁后面」差 40 倍：

| 出口 | 实测 | 是否接润色 |
|---|---|---|
| 「帮我安排游泳」候选时段 | 80 ms | 否（_slots_answer） |
| 「周六下午三点爬山」确认卡 | 3223 ms | 是（_card_answer） |
| 「看看我的待办」列待办 | 3883 ms | 是（list_todos） |

**本刀把这两类统一**：卡片类不润色、长列表才润色（且 ≤1s）。关键路径上不再有 3~4s 的 await。

---

## 1. 改动范围（三处，全是删/降，不加）

| 文件:行 | 现在 | 改成 | 性质 |
|---|---|---|---|
| `app/agent/polish.py:34` | `POLISH_TIMEOUT = 4.0` | `POLISH_TIMEOUT = 0.9`（或 1.0） | 一处常量 |
| `app/main.py:2702` | `await polish_reply(...)`（加待办确认卡） | 直接回 `_card_fallback`（系统事实文案） | 摘掉润色 |
| `app/main.py:2332` | `await polish_reply(...)`（列待办） | **保留**，自动享受降下来的 ≤1s 超时 | 不动逻辑，只受益 |

> 全仓 `polish_reply` 调用点只有这两处（`grep -n "polish_reply" app/main.py` 验证）。
> `_slots_answer` 本就不调润色，无需改动。

---

## 2. 具体改法（给实现 AI 的指令，不是给你抄的成品）

### 2.1 polish.py:34 —— 降超时
```python
# 改前
POLISH_TIMEOUT = 4.0
# 改后
POLISH_TIMEOUT = 0.9   # 润色是锦上添花，0.9s 内出不来就退模板，绝不拖住回复
```
超时分支逻辑（`except` → `return fallback`、空文本 → `fallback`）**一字节不许动**。

### 2.2 main.py:2702 —— 摘掉确认卡的润色
把：
```python
from app.agent.polish import polish_reply
_card_answer = await polish_reply(
    {"intent": "add_todo", "title": proposal["title"], ... "executed": False},
    _card_fallback, override=model_config)
return _sem_done(_offer_response(session_id, proposal, message, _card_answer, "📝 生成待办提案（系统判定）"))
```
改成（`_card_answer` 直接等于系统事实文案，**不要**再 `await polish`）：
```python
_card_answer = _card_fallback   # 系统算好的事实文案，本就是超时降级那句；卡片类不润色
return _sem_done(_offer_response(session_id, proposal, message, _card_answer, "📝 生成待办提案（系统判定）"))
```
- `_offer_response(...)` 内部的 `proposal` / 卡片字段 / `save_pending` **全部保持原样**——它们来自系统事实，
  与润色无关，不得因摘掉润色而改口径。
- `from app.agent.polish import polish_reply` 这行若仅服务于 2702，**整行删除**（避免无用的函数内 import）。

### 2.3 main.py:2332 —— 不动
列待办这条保留 `await polish_reply(...)`；降超时后它的上限从 4.0s 变 0.9~1.0s。
`append_conversation` 那两行（2333-2334）写的是系统事实「已列出待办：N 条」，**不是**润色文本，保持。

---

## 3. 四条必须钉住的不变量（缺一条就是新 bug）

1. **暂存与卡片不许被改口径**：`save_pending` / 卡片字段 / `append_conversation` 一律用系统那份事实文案；
   润色（仅 2332 还在用）只换「显示层」。否则会出现「屏幕上说 A、点【确认加入】执行的是 B」。
   → 验证：改完后对比 `_offer_response` 产出的 `pending` 内容与改前逐字一致。

2. **落地前要过同一批守卫（红线 8）**：
   - 润色自守卫（`polish.py:107-115`）：`has_promise(text) and not executed → fallback`、`text[:1] in "{[" → fallback`。
   - 引擎 claim 守卫（**另一族，别和上面并成一道**）：
     `main.py:3600-3601` `claims_proposal_sent(result["answer"]) and (has_concrete_span(...) or recent_add_req)`
     → `_no_card_answer()` 如实纠正；
     `main.py:3654` `looks_like_tool_echo(final_answer)` 出口保险；
     外加「写库只走确认漏斗」。
   → 三处守卫族全保留，异步/摘润色都不许删任何一道。test_06 十二/十三批钉的就是引擎 claim 守卫后两处。

3. **失败就什么都不做**：超时/报错走 `return fallback`，**不许**往会话里追加第二条消息（不重复、不跳变），
   也**不许**把「降级」写成一条新话术。现状已是这样，保持。

4. **用量记账别丢**：润色调用走网关 `ModelGateway(user="内部:话术润色")`（`polish.py:92`）。
   2332 仍在调 → 仍记；2702 不再调 → 那条出口的「话术润色」计数自然减少，不是丢失。
   管理端「用量统计」靠这个 `user` 标签，别把 2332 的调用也摘了。

---

## 4. 顺手消掉的不一致（并进本刀）

统一规则：**卡片类不润色（`_card_answer` + `_slots_answer`），仅长列表（`list_todos`）才润色且 ≤1s**。
这比「全都异步」更省、也更难写错。改完后三类「系统已算好事实」出口在同一档：候选时段 / 加待办卡 / 列待办
都不该再有 3~4s 的等待。

---

## 5. 老规矩验证清单（落地 AI 必须逐项过）

- **护栏先行（先写断言，再改代码）**：
  - 新断言 1：加待办确认卡这条路径上，`_card_answer` 必须 == `_card_fallback`（即不再 `await polish_reply`）。
  - 新断言 2：`POLISH_TIMEOUT <= 1.0`。
  - 新断言 3：候选时段 / 加待办卡 / 列待办 三类出口，关键路径上均无 >1s 的润色 await（同一档）。
- **变异打回（证明断言真能抓回归）**：
  - 把 `POLISH_TIMEOUT` 改回 `4.0`，或把 `polish_reply` 重新塞回 2702 → 上面断言 **必须变红**。
  - 红了再改回正确值，断言转绿，才算通过。
- **真机复测那五句（改前/改后对比 ms）**：
  「帮我安排游泳」/「今晚安排点啥」（规则层，应仍 ~80-90ms）、
  「周六下午三点爬山」（确认卡，应从 ~3223ms 掉到 ~80-300ms 档）、
  「看看我的待办」（列待办，应从 ~3883ms 掉到 ≤1.5s）、
  「今天几号」（2 次模型，不受本刀影响，作对照）、
  「食堂几点关门」（3 次模型，作对照）。
- **交付纪律**：细粒度 commit（每处改动一个 commit：①降超时 ②摘卡润色）+ 打快照 + 重启 8000 复测。

---

## 6. 明确不做（防止范围蔓延）

- 不做 A（BackgroundTasks + 轮询）和 C（A+B）——那是第二刀，本刀只 B。
- 不碰 `client.py` 的连接池（那是刀 2）、不碰 `intent.py` 判定逻辑（刀 3）、不碰流式（已有 07 单）。
- 不删任何守卫（见 §3.2 两族），不删 SSRF 闸门，不删「写库只走确认漏斗」。

---

## 7. 给实现 AI 的纪律提醒（来自项目接手指南）

- **整族扫**：改前 grep 全部 `polish_reply` 调用点（只有 2 处），不要只改被投诉那一句。
- **注释里的例子必须实测过**：§0 的 ms、§5 的五句实测，都是今天 `app/data/llm_usage.jsonl` 真量出来的，不是估。
- **变异测试 + 真机复验表 + 编号红线**：先写断言再改，变异必须红，改回必须绿。
- **学生面前不暴露技术内脏**：本刀不新增任何报错白屏路径，降级静默。
