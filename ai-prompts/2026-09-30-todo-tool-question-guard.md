# 收口"待办误判/错别字/思考"残留 + 文档策略 —— 任务一落地（模型直调路径统一守卫）

> 用户原始 Prompt 文件：`D:/Workbuddy/2026-09-20-18-56-18/CampusTime-收口误判与文档策略-prompt.md`
> 该文件原话要求"本窗口只做方案与验证，此文件直接粘到另一任务窗口落地"——本会话接手执行。

## 用户 Prompt（摘录要点）

一份"收口"清单，核心是**任务一**：堵死"瞎起弹窗弄成代办"的残留缺口。

- 现状（用户已核对代码）：规则路径 `wants_add_todo` 已加 `_GO_QUESTION_RE` 守卫；
  但模型能**直接调**共享工具 `propose_todo_tool` / `propose_todo_slots_tool`
  （这两个在 `build_scheduling_tools()` 里注入所有模块），**那条路没有问句/非下单守卫**，
  问句、废话仍可能被弄成待办卡——这是"还会出现瞎起弹窗弄成代办"的残留根因。
- 要求：在"出待办卡"上做**统一守卫**，规则路径与模型直调路径都过同一道关卡：
  ① 出题前 `looks_like_question(message)` 或 `_GO_QUESTION_RE.search(message)` 命中 → 一律不出卡，改走 faq；
  ② 收紧 `_GO_INTENT_RE`："我要去X" 必须 X 经 `_title_ok()` 判真才放行；
  ③ **关键**：在 `propose_todo_tool` / `propose_todo_slots_tool` **函数体里**也加这道否决（模型直调也能拦），
     `_pick_title` 返回"待办"（没说清事名）时也拒出卡、交追问分支；
  ④ 复用现有 `looks_like_question` / `_GO_QUESTION_RE` / `_title_ok` / `_NOT_TODO_PHRASES`，**禁止另写一套**。
- 任务二（待办"整理"）：本版可选；若不做，在回复/注释里说明"以思考轨迹替代主动整理"。
- 文档策略（重要）：**不重写正式 `docs/技术文档.pdf`**；继续用 `ai-prompts` 快照 + README 索引；
  等本批功能（/classify + 思考面板 + 错别字/误判修复）全绿、demo 定型后再一次性 sync PDF。

## AI 研判

先把 spec 里"已落地"的声明逐条对账到 **2026-09-30 当前代码**，确认没漂、再动手：

| spec 声称已落地 | 当前代码核查 | 结论 |
|---|---|---|
| 错别字纠正已做 | `planner._fix_typo_time_words`（三道闸门）+ 语义层 Prompt 要求纠笔误、title 不混时间 | ✅ 仍成立（上一轮已落） |
| 思考轨迹面板已做 | `main._chat_impl` 单出口 + `student.html` 默认展开、可关 | ✅ 仍成立 |
| `/classify` 已做 | `POST /classify` + 意图诊断按钮 + `test_intent_classify.py` 62 项 | ✅ 仍成立 |
| 标记完成系统接管已做 | `INTENT_DONE` + 确认执行块 | ✅ 仍成立 |

**核心缺口定位（spec 任务一）**：spec 的判断准确——`grep` 实证 `build_scheduling_tools()` 在
`main.py:1009` 注入、并在 `main.py:2790` 的 `AgentEngine(tools=tools)` 里交给模型，
所以模型确实能直调 `propose_todo_tool` / `propose_todo_slots_tool` / `propose_todo_mode_tool`。
而这三个函数体里**只有 `if not day` / `if not blob` 这类空值检查，没有问句/非真事否决**——
`propose_todo_tool(title="我想问一下交电费怎么操作", date="周三")` 在旧代码里会真出一张待办卡。
这正是"残留根因"。

**为什么不能只在规则路径拦**：规则路径 `wants_add_todo` 只在"确定性分支 3c"生效；
一旦消息落进 ReAct 引擎、模型自行决定调 `propose_todo_tool`，守卫就被绕过了。
所以必须**在函数体里也加否决**，做到"统一守卫"。

**为什么不另写一套**：`looks_like_question` / `_GO_QUESTION_RE` / `_title_ok` 都是已验证、已在别处
使用的函数，再写一份判定只会产生漂移（改一处忘两处）。直接复用。

**循环依赖排查**：`app/agent/intent.py` 只在函数内 `from app.modules.planner import ...`
（line 478，懒导入），模块顶层不 import planner；因此 planner 里 `from app.agent.intent import looks_like_question`
不会成环。为更稳，放在三个工具函数**体内**懒导入。

## 改动

全部在 `app/modules/planner.py`（一个 commit），未动任何前端、未动 PDF：

1. **`propose_todo_tool`**：函数体开头加统一否决——
   - 用 `title+when+date+start+end` 重建 `_raw`，`if looks_like_question(_raw) or _GO_QUESTION_RE.search(_raw):`
     返回 `{"error": "这像是在问事/问办法，不是要加待办", "hint": "走问答/知识库去答他…"}`，**不出卡**；
   - 算完 `name` 后 `if not _title_ok(name):` 同样返回 error（事名说不清 → 交追问分支，绝不把半句话挂成待办名）。
2. **`propose_todo_slots_tool`**：`if not blob` 之后加同一道 `looks_like_question(blob) or _GO_QUESTION_RE.search(blob)` 否决（出候选卡前先拦）。
3. **`propose_todo_mode_tool`**：同上，加同一道否决（二分卡前先拦）。
4. **`wants_add_todo`**：`_GO_INTENT_RE` 分支的放行条件
   `and _name and _name != "待办"` → 改为 `and _title_ok(_name)`：
   "我要去X" 必须 X 经 `_title_ok` 判真是件真事才放行，纯疑问/愿望不放开（同一道关，规则路径也收紧）。

四条改动共享同一组既有判定函数，无新增词表、无新增正则。

## 验证

- 新增 `tests/test_17_tool_question_guard.py`（4 批 20 项）：
  - 规则路径：`wants_add_todo` 对"我要去哪里取快递 / 我想问一下交电费怎么操作 / 图书馆几点开门"均 False；
    原有"帮我加个游泳周四19:00"仍 True。
  - 模型直调路径：三个工具收到这三句（含"问句+顺手给日期"）**全部返回 error、不出卡**（`__proposal__` 不存在）。
  - 事名说不清（待办/加个/安排一下/帮我安排）也不出卡。
  - 正常加待办 `propose_todo_tool(游泳,周四,19:00-20:00)` 仍出 `kind=todo_add` 卡，事名/时间正确。
- **反向验证（防假绿）**：运行时把 `looks_like_question` 打桩成永远 False、`_GO_QUESTION_RE` 换成永不命中的正则，
  再调 `propose_todo_tool("我想问一下交电费怎么操作", date="周三")` → **确实出了 `todo_add` 卡**、
  `propose_todo_mode_tool("我要去哪里取快递")` → 出了 `todo_mode` 卡。证明断言盯的是真信号，
  摘掉守卫问句就会变卡，不是空绿。
- **全量**：`python tests/run_all.py` → **通过 1426 项，失败 0 项（共 18 批）**，较上一轮 1406/17 批新增 20 项、无回归。

## 提交清单

- `f62f4d4` — 收口误判·统一守卫：模型直调工具路径也拦问句/废话（planner.py 4 处）
- `0a35da0` — 测试·第十七批：堵死模型直调工具把问句弄成待办卡的残留缺口（tests/test_17_tool_question_guard.py）

## 文档策略执行

- 严格遵守 spec 文档策略：**未重写 / 未重生成 `docs/技术文档.pdf`**（架构仍在变动，冻结 = 反复返工）。
- 本改动的决策上下文只落在本快照 + 下方 README 索引一行，便宜且留痕。

## 备注：任务二（待办"整理"）本版不做

本版以**已落地的"思考轨迹面板"**替代"主动整理"：学生能在轨迹里看到"我把这句听成办事/问事、事名读成了什么"，
比半吊子的"自动合并建议"更可控、不误伤既有待办。按 spec 允许，明确说明此选择、不强行做。
