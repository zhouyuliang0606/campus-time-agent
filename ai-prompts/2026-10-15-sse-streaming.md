# 2026-10-15 · 07/12 号流式（SSE）：协商式出口 + done 晚于守卫 + 学生端接流

## 一、输入

两份施工单（同一窗口给的，前后脚）：
- `D:/Workbuddy/2026-10-12-21-36-55/prompts/07_流式输出_SSE_最终落地prompt.md` —— 后端四件套
  （`client.chat_stream` / `gateway.stream` / 引擎改生成器 / `/api/chat` 改出口）+ 前端两处 fetch。
- `D:/Workbuddy/2026-10-12-21-36-55/prompts/12_前端SSE接入_落地prompt.md` —— 后端已部署之后，前端精确接法
  （`streamChat(body, handlers)` + 两处调用点）。

范围拍板用了一次现问现答：我把"首字压不到 1~2s"的实测摆出来后，用户选 **全做（token + trace + done 覆盖）**。

## 二、先实测（施工单的头号前提不成立）

一笔真请求到 `api.deepseek.com/deepseek-chat`：
`stream:true` + `stream_options:{include_usage:true}` **可用**，79 块、`finish_reason=stop`、
**末块确实带 usage** → 流式这一路记账不会瞎。这条成立。

但 §一「首 token 约 1~3s 即显」不成立，因为 §6.1 自己明令 **判定层保持非流式**：
判定 p50 4.2s 就挡在引擎第一个字之前。真机浏览器实测（见 §七）：首块 2.9s、末块 4.5s、done 4.8s。
另外两条实测事实也影响设计：
- 引擎只有**最后一步**才有 `content`（`engine.py` 那一支），前面几步都在调工具 → token 流只存在于末步；
- 规则/样本库接住的句子（191ms 那一类）根本不打模型 → 流式对它零收益。
所以这一刀买到的是「字在冒 + 思考过程立刻出」，**不是把总耗时压下去**。这句话写进了指南第 15 条。

## 三、做了什么

| 提交 | 内容 |
| --- | --- |
| `f76db14` | `client.chat_stream`（逐行解 SSE、`tool_calls` 按 index 拼回、末块读 usage、异常带 kind 上抛、SSRF 每次调用都过）+ `gateway.stream`（与 `chat` 同口径记账，多带 `stream=1`；两种明写的降级） |
| `69e6d60` | 护栏 `tests/test_49_streaming_sse.py`（六批，本笔故意留红 13 条） |
| `e2e1e05` | `engine.run(..., on_event=None)` 可选回调：`self.trace.append` 四处收进 `_trace()`，挂了回调才走流式 |
| `028bf17` | `/api/chat` 按 `Accept: text/event-stream` 协商出 SSE；后台跑完整 `_chat_json` → **返回了才发 done** |
| `4281445` | `student.html` 新增 `streamChat` + 两处调用点接流；`test_26` 的形状断言跟着改名走 |
| `8a7aaae` | test_49 六批全绿 44 项 + 三条空转断言当场补牙 |
| 本笔 | 指南第 15/16 条 + 本快照 + 索引一行 |

## 四、与施工单的偏离（五条，每条有理由，不是偷懒）

1. **§4.4「`/api/chat` 直接改 `StreamingResponse`」→ 改成按 `Accept` 协商。**
   整族扫的实测数：读 `/api/chat` 的有 `plan.html:499`、`station.html:201`（都 `await r.json()`）
   + 测试里 **227 处 `post("/api/chat", ...).json()`（35 个文件）**。无条件换出口 = 一次弄红三族。
   协商 = 新增一条分支，老路一个字不动（本轮全程 227 处没红过一条，就是这个决定的直接证据）。
2. **§4.3「engine 改异步生成器 + run_collected」→ 改成可选回调 `on_event`。**
   生成器化会牵动 14 处写死 `run(self, message, history=None)` 的假引擎；而且非流式那条我**连参数都不多传**
   —— 多传一个 `on_event` 就是 `TypeError`，会被 `except Exception` 咽成"这句没答成"（刀 3 那课的同一类坑）。
3. **§5 的内部 `result` 事件不发到前端。** 本设计里守卫直接吃 `_chat_impl` 的返回值，没有中间事件要衔接，
   少一个前端要认的 `k`（施工单自己就注明"仅供后端内部衔接"）。
4. **§4.5 说前端是两处，实测是四处**（另有 `plan.html` / `station.html` 各一处）。
   这两处本刀不接：它们以卡片为主、正文由系统拼模板，流式零收益；而默认 JSON 出口保证它们**一行不改也不坏**。
5. **§4.2 / §4.3 那两段可直接粘贴的代码没照抄。**
   - §4.2 自己拼 `whoHtml/bodyHtml/traceHtml` = 把 `addMsg` 的 HTML 抄第二份（`traceOn()` 一改就漂移）
     → 改成"删掉占位气泡再调 `addMsg(d.answer, ...)`"，逐字复用同一份渲染；
   - §4.3 的替换代码把 `addMsg` + `renderOptions` 两行丢了，而那两行正是 2026-10-07 报障
     「第一次不弹第二次追问才弹」的修复本体 → 只换取数方式，渲染一律照旧。

## 五、真机才暴露的两个坑（都是"看代码看不出来"那种）

1. **必须先读请求体，再发 `StreamingResponse`。** 第一版照施工单写：后台任务里 `await req.json()` ——
   TestClient 与真 uvicorn **两边都挂**，事件一个不来（HTTP 200 头先到）。服务端日志里打上
   `ClientDisconnect`：响应一开头发出去，之后 `receive()` 只会拿到 `http.disconnect`，
   于是整句话被当成空请求，学生收到"这句话我没听懂"。修法就是发响应之前先 `await req.body()`
   （读一次就缓存进 `req._body`，`_chat_impl` 里那句 `req.json()` 拿的是缓存，不读第二遍）。
   诊断过程也值得记一句：我先用 `python -c` 加 print 想看进度，**Git-Bash 把带中文的 `-c` 吞成空输出**
   （这条规矩我写过，又踩第二次），改成落成 `.py` 文件才看见现场。
2. **`env PYTHONUTF8=1 python ...` 前缀和 `timeout 90 ...` 在 Git-Bash 里都会吃掉输出**
   （`timeout` 是 Windows 的 timeout.exe，整条命令被它吞掉、返回 0）——
   我一度以为"测试卡住且无输出"，实际是我的调用方式在骗我。判"卡住"要另开一条能出字的路子。

## 六、护栏（`tests/test_49_streaming_sse.py`，六批 44 项，先红后绿）

| 批 | 钉什么 | 为什么这么测 |
| --- | --- | --- |
| A 传输层 | 假接口真发 SSE：分块到齐、**首块比末块早 ≥0.2s**（真流式而不是攒整包）、`tool_calls` 碎片拼回完整、请求体带 `stream`+`include_usage`、闸门计数、`chat()` 照旧非流式 | 起**本地假接口**手工写 chunked + 每块 flush，用"到得早晚"证明流式；不测函数签名 |
| B 记账与降级 | 流式这笔照样进 `llm_usage`（含末块 usage、`stream=1`）；上游 reject 流式 → 退回非流式且**只算一次**；401 上抛 `auth`；**只对 stream 回 401** 的那种也必须上抛；流到一半断了已吐的那块留着、不补整段 | 三条降级边界各一条，"不许静默咽掉鉴权错误"是 §6.5 |
| C 协商 | 不带 `Accept` → 仍 `application/json` 且五个键齐全；带 → `text/event-stream`、事件只三种 k、最后一个必须是 done、done.v 与老 JSON 同构 | 这条是"227 处 + 另两页不吃亏"的保险 |
| D 守卫时序 | 前提：这句**真走到了引擎**（trace 里有 🤔 思考）；token 拼起来 = 引擎原话（含"提案已发出"）；`done.answer` = 守卫修正后的版本（不含那句）且卡被补出来；done 之后不再有 token；非流式那条同样被守卫换掉 | 本刀唯一要命的时序；预览与权威版必须分别断言 |
| E 老假货兼容 | `_harness.FakeLLM` 只有 `chat`（先断言这个前提）→ SSE 出口仍给 done、退回那段逐字不缺 | 全仓 227 处用的就是它，不许被流式弄崩 |
| F 形状 | `client.chat()` 函数体里没有 `stream` 字段；`gateway.chat` 不碰 `chat_stream`；判定层与润色仍非流式；引擎只经网关要流式且 `run()` 的流式是可选参数；`student.html` 只剩**一处**打 `/api/chat`、两处都调 `streamChat`、定稿走 `d.answer`（整行形状只许出现一次） | 形状断言先剔 `#`/注释；被自己注释绊红过四次，这条纪律已固化 |

## 七、变异八条（全部能分辨；跑完逐字节还原）

```
M1 攒整包再吐（假流式）        → 红 3：A"拼起来是整段"、A"首块早到"（间隔 -1ms）、B"断线留一块"
M2 流式漏记账                  → 红 1：B"这笔标着 stream=1"变 None
M3 降级条件写宽（咽掉鉴权错误）  → 第一次**全绿**（假接口两种请求都回 401，退回非流式抛同一个错，
                                变异与原文等价）→ 补"只对 stream 回 401"的模式后红 1 ✅
M4 拆掉流式那一路的 SSRF 闸门   → 红 1：A"闸门跑了 1 次"变 0 次
M5 无条件 SSE（施工单原写法）   → 红 1：C"不带 Accept 仍是 application/json"变 event-stream
M6 守卫不纠正（done 给原话）     → 第一次锚错分支**全绿**（CLAIM 走的是"捞得出方案就补卡"那一支，
                                不是 `_no_card_answer()`）→ 换锚后红 2：D 两条出口的 answer 都露出原话 ✅
M7 前端定稿用预览拼串           → 第一次**全绿**（`addMsg(botName(), d.answer` 这个前缀被上传课表那条
                                蒙混过去）→ 改成钉整行形状且只许一次后红 ✅
M8 拆掉"假货没有 chat_stream"的退路 → 红 4：D 批三条（引擎当场没答成）+ E 批一条
```

三条"全绿"都是我自己写错的（断言没牙 / 锚点打错分支），不是断言无辜 —— 每一次都先把护栏补对再重跑，
这正是指南 4h/4i/4l 那三条纪律的第 N 次应验。

## 八、真机与浏览器实测（8000，用户自配 Key 的现状一起记）

| 场景 | 结果 |
| --- | --- |
| 直接调 `streamChat`（服务器 Key，闲聊长答） | **44 块**：首块 4278ms、末块 4655ms、done 4711ms；`done.answer` 权威版、trace 3 条、0 张卡 |
| UI 全链路（`sendMsg`，服务器 Key） | **50 块**：首块 **2920ms**、末块 4543ms、done 4794ms，气泡文本从 2 字一路长到 76 字（rAF 在后台页被冻结，改用给 `streamChat` 打桩记时间戳才拿到序列） |
| 卡片没丢（规则接住的「加个今晚九点回邮件」） | 末条 planner + 205 字话术 + **`/confirm?session=…` iframe 可见**（高 168）、服务端 pending 里有那张 `todo_add` |
| 降级（浏览器里那把**学生自配 Key 已被上游拒**，401） | 没有 token、直接一个 done 给一句人话：「⚠️ 负责回答的服务说这次的授权不对（可能过期了）……」，**不白屏、无半截残留** |
| 现场还原 | `ct_model_cfg`（73 字节）**写回后逐字节一致**；`campustime_session` 还原为 `s2b2aa9f61d`；探针 pending/sessions 按 `sse-ui-probe-* / sse-bisect-* / dbg-* / sse-live-*` 前缀清掉（备份 `D:\campustime-scratch\bak-sse\`），sessions 回到 **656**；用户自己那张 `s57644701af`（课表替换提案，18 门课）**没碰** |
| 没做的一项 | 「点【确认】仍能写库」没在浏览器里点（点下去就是往真实 `todos.json` 写数据）。写库漏斗本刀未动，由 test_09/test_06 那族守着 —— 这一条按"只出卡不点确认"的旧规矩留给用户手测 |

## 九、验证与数字

| 项 | 结果 |
| --- | --- |
| test_49 单跑 | **44 项 / 0 失败**（六批全绿），exit 0 |
| 受影响的老测试（10 个文件，含 9 处假引擎的 test_06） | 全部 exit 0，**一条没红** |
| 本机全量 | **3323 通过 / 3 失败（共 49 批）**，exit 1 —— 三条全在 `test_39_weekly_recurring_todo.py` |
| 三条红的归因 | **A/B 实测**：`cp` 备份 + `git checkout HEAD -- app/…` 撤下本刀代码后单跑 test_39，**同样红这 3 条**（跑完 md5 自证还原）。根因是数据漂移：真实 `todos.json` 多了一条「背单词」（`created_at 2026-10-14 23:05:49`，用户手测留下的，11 → 12 条），而 test_39 的起点依赖那份数据 |
| 干净克隆（committed 树、连 `app/data/student/` 都没有） | **3326 通过 / 0 失败**、`全部通过 ✅`、exit 0；`pytest` **1 passed**（116.93s），exit 0。同一批 test_39 在克隆里是 **191 项 0 失败**（本机 188+3）→ 与"数据漂移"的判断一致 |
| 前端语法闸 | 抠出 `<script>` 拼 `.js` → `node --check` **exit 0**（指南新增第 16 条；并进 test_44 仍是挂账 #29） |

**本机这 3 条红我没有"顺手修掉"**：要么动 test_39 的自种（把它依赖的那条习惯在沙箱里自己种一遍），
要么清用户真实数据里的「背单词」—— 后者是删真实数据，前者属于另一窗口的习惯族，两条都等用户点头。

## 十、还剩什么

- **判定层那一跳没动**：首字仍受 `classify` 的 p50 4.2s 限制。要再往前走只有两条（都已另开单）：
  把判定结论喂给引擎首调用的合并重构，或让判定与规则并行 —— 都不是本刀范围。
- `plan.html` / `station.html` 两处仍在 JSON 通道（功能正确，只是不逐字）；要不要接上由用户定。
- 挂账 #47（`sandbox()` 不重定向 `llm_usage.jsonl`）本刀又多了一笔：流式那几笔自检也写进了真实流水。
- 比赛交付物里的 push / PDF / 演示视频仍 deferred（push 需用户明确确认）。

## 十一、纪律回报（用户定的那三条）

细粒度提交 7 笔（`f76db14` `69e6d60` `e2e1e05` `028bf17` `4281445` `8a7aaae` + 本笔）；
本快照 + 索引一行（`git diff --numstat` = `1 0`）；未 push。
