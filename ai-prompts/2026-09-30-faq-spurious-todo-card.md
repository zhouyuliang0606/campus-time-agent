# 2026-09-30 · faq 栏「你是谁」被乱挂待办确认卡（Item B / 收口误判·任务二）

## 学生报障（原话 + 截图）
> `@image#1:Clipboard_Screenshot.png，意图整段没啥实际用处体现`

截图内容：在「校园问答」栏问 **"你是谁"**，模型却回了一张 **待办确认卡**——
标题「校园问答」、日期 2026-10-02、时间 15:40-16:40。学生顺带吐槽「意图整段没啥实际用处体现」：
判定/思考轨迹那段在闲聊场景里净显示些没用的东西。

## 研判（用户 Prompt → AI 研判）
- 这句是**问候 / 身份问答**，不是要办待办。出卡本身就是错的。
- 根因不在「意图判定」层（wants_add_todo 的 `_GO_QUESTION_RE` 守卫、任务一的「统一守卫」都拦不住）：
  「校园问答」是个**非平凡标题**，能过 `looks_like_thing` / `_title_ok` 判真，所以任务一加在
  `propose_todo_tool` 函数体里的质询挡不住它。
- 真正的破口在**工具下发范围**：`app/main.py` 原来把 `scheduling_tools()`（含
  `propose_todo_tool` / `propose_todo_slots_tool` / `propose_todo_mode_tool`，三个都能挂待办确认条）
  **无条件注入所有模块**。faq 模块本只该做知识库检索，却拿到了出卡工具；模型顺手一调就挂了卡。
- 一句话：**判定层（该不该办）守住了，但执行层工具越界发到了不相干的模块**。

## 改动（`app/main.py`，1 处逻辑 + 1 个辅助函数）
1. 新增辅助函数 `_schedule_tools_allowed(module_key, sem_intent, message)`：
   - `module_key == "planner"` → 始终给（主业就是排日程）；
   - `sem_intent == INTENT_ADD_TODO`（语义层明确判成加待办）→ 给；
   - 否则 `wants_add_todo(message)`（规则层关键词命中加待办）→ 给；
   - 其余（闲聊 / 问候 / 问答栏问句 / 其它模块）→ **不给**。
2. 把原来「`tools.update(scheduling_tools())` 无条件注入」改成：
   `if _schedule_tools_allowed(module_key, _sem_intent, message): tools.update(scheduling_tools())`。

> 这是「判定层 / 执行层分离」红线在**工具下发**这一环的收口：模型碰不到写库接口不变，
> 但**只读出卡工具也只发给确实在办日程的语境**，避免把问候/问句弄成待办卡。

## 验证（钉死，见 `tests/test_18_faq_no_spurious_todo_card.py`，共 11 项）
- **单元**：`_schedule_tools_allowed` 对 6 类语境给出正确开关
  （faq+「你是谁」/「你好」/ express+问句 → 不给；planner → 始终给；faq+语义=add_todo /
  faq+规则判加待办 → 给）。
- **端到端①（复现报障）**：`POST /api/chat {"message":"你是谁","module":"faq"}`，
  即便假模型硬要 `propose_todo_tool`，响应 `options` 为空、`awaiting_choice` 为 False——**不出卡**。
- **端到端②（不误伤）**：`POST /api/chat {"message":"帮我加个游泳周四19:00","module":"faq"}`，
  语义层判 add_todo → 仍正常出 `todo_add` 卡（标题「游泳」、19:00-20:00）。

### 反向验证（证明不是假绿）
临时把 `_schedule_tools_allowed` 打成「永远放行」模拟修复前状态，复跑端到端①：
复现出 `{"kind":"todo_add","title":"校园问答","date":"2026-10-07","start":"15:40",...}` 的卡——
**与学生的报障截图完全一致**（标题、时段都吻合），对应 2 项断言 FAIL。证明门禁确实拦住了破口。

## 与任务一的关系
- 任务一（2026-09-30 上午）堵的是「模型直调工具把**问句/废话**弄成待办卡」——守卫加在工具函数体里。
- 本项（Item B）堵的是「**非排程模块被发了出卡工具**，问候/模块名也能被当成待办名挂卡」——
  守卫加在**工具下发**这一环。两者互补，共同把「意图整段没用」这类误判收口。

## 文档策略
按 2026-09-20 约定：**不重写 `docs/技术文档.pdf`**，只补本快照 + README 索引（提交与代码分开）。
