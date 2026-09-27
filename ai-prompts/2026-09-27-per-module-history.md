# 2026-09-27 每个模块独立历史 + 固定左侧导航 + 模块化开场白

## 用户原话 Prompt
> 现在在地下发送消息会自动跳到校园问答，固定左边，每个模块一个历史记录，然后每个模块的初始欢迎提示词要适应当前模块

（"地下"=底部输入区；"固定左边"=左侧导航不被发送动作拽走）

## AI 研判
1. **自动跳回校园问答的根因**：底部 send/Enter 的真·发送逻辑在外层（约 1598-1599 行），但 IIFE 里另挂了一个第二监听（原 1945-1946 行），发送时调用 `switchNav("chat")`，把左侧导航强行拽回"校园问答"。→ 删掉这两个监听即可，发送本身不受影响。
2. **每个模块一条历史**：原 `sessionId()` 用全局 `campustime_session` 单一 key。
   - 关键约束：该全局 key 被 `plan.html`（规划页）与 `tests/` 的端到端脚本共用。若改成纯按模块 key，会破坏跨页上下文与测试。
   - 决策（折中且零回归）：**校园问答(chat) 沿用全局 key**；**日程/快递/外卖三个带面板的模块各自独立 key**（`campustime_session_schedule` 等）。这样"每个模块一条历史"在真正有面板的模块上成立，又不破坏既有兼容。
3. **模块化开场白**：原 `#greetBubble` 是写死的单条文案。改为 `MODULE_GREETING` 表，按 `currentView`（左侧导航模块）渲染；切到哪个模块就显示哪套欢迎语。
4. **模块切换换历史**：在 `switchNav` 里记录 `oldView`/`currentView`，进入"对话类"模块（chat/schedule/express/takeout）且 view 变化时调用 `resetLogForModule(view)`：清掉上一段消息 → 换开场白 → `restoreHistory()` 拉回该模块历史。
5. **卡片与左侧导航同源**：点模块卡片（日程/快递/外卖/校园问答）现在也走 `switchNav`，高亮与历史一致（faq 卡片映射到左侧 chat）。

## 具体改动（仅 student.html，后端无需改）
- 新增 `currentView` / `switchNavRef` 状态变量。
- 重写 `sessionId(view)`：按模块 key（chat 走全局）。
- 新增 `MODULE_GREETING` / `applyGreeting(view)` / `resetLogForModule(view)`。
- `switchNav`：跟踪 currentView、暴露 `switchNavRef` 给卡片、切模块时换历史。
- `selectModule`：卡片点击改走 `switchNavRef`。
- `doClearChat`：清当前模块 key + 重渲染开场白。
- `refreshGreeting`、粘贴发送、send/text 的第二监听：相应收敛。

## 验证
- 语法校验：提取 `<script>` 用 `new Function` 解析通过（SYNTAX OK）。
- 后端 `/api/chat`、`/api/conversation/{sid}`、`/api/chat/reset` 均按 `session_id` 存储，天然支持按模块区分的 sid，无需改动。
- 既有 `tests/` 与 `plan.html` 仍用全局 `campustime_session`（chat 模块沿用），兼容性保留。

## 关联 commit
- 本批 1 个代码 commit（student.html）
- 1 个 Prompt 快照 commit（本文件 + README 追加条目）
