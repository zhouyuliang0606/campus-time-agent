# 2026-10-08 前端布局重构：日程当主页 + 校园问答降为模块 + 上传课表主按钮

## 用户原话 Prompt（摘录）
- 「我觉得日程当主页然后校园问答作为一个小模块，在主页可以点进去，手动上传课表做成一个按钮，现在的前端布局有点乱」

## AI 研判与决策
- **根因（为什么乱）**：旧默认主页是「校园问答」(chatStage)，里面叠了「校园通知 + 模块卡片(#modules) + hint + 校园问答视图」；而日程/快递等面板视图是叠在同一 chatStage 下方的 #panel。
  其实 CSS 早已在 `.panel-mode` 下隐藏 `.notices/#modules/#hint`（492 行），且 `#modules/#hint` 已永久 `display:none`（498-499 行）——所以真正的"乱"就是默认那一屏（校园问答）上方堆着校园通知，且校园问答被当成默认主页而非模块。
- **布局目标**：
  1. **日程当主页**——进入即看日程（时间轴+周列表），干净无通知堆叠。
  2. **校园问答作为小模块**——主页顶部「快捷入口」模块条里点「💬 校园问答」即进对话，不再默认霸屏。
  3. **手动上传课表做成按钮**——主页「📤 上传课表」主按钮（品牌色）+ 面板工具栏里同样的主按钮，点它触发文件选择 → 走既有导入流程。
  4. **清理**——去掉主页上误导性的「收起面板 ✕」按钮（日程是主页不是从对话展开的面板）。

## 具体改动（`app/static/student.html`，纯前端）
1. **默认主页=日程**：`currentView` 默认 `"chat"`→`"schedule"`（881 行）；初始化 `currentView="schedule"; resetLogForModule("schedule"); switchNav("schedule")`（2735-2737 行，原本是 chat）。侧边栏 `校园问答` 的 `active` 移到 `日程`（679 行）。`switchNav` 已在 panel 态给 chatStage 加 `panel-mode` 并显 `view-schedule`，无需新逻辑。
2. **去掉主页关闭按钮**：`view-schedule` 的 `view-head` 删掉 `<button class="close">收起面板 ✕</button>`。
3. **主页快捷入口模块条**（view-schedule 内、view-body 前）：
   `<div class="home-quick" id="homeQuick">` 含 5 个 `.hq` 按钮：`data-go="chat"` 校园问答、`id="homeUpload"` 📤上传课表(`.primary`)、`data-go="express/takeout/todos"`。
4. **事件绑定**（uploadTtPanel 监听段后新增）：`homeQuick [data-go]` 点击 → 触发对应 `.sb-item[data-nav=...]` 的 click（复用 switchNav）；`homeUpload` 点击 → `uploadTtPanel.click()`（复用既有上传逻辑，走 `/api/upload`+聊天导入）。
5. **样式**：新增 `.home-quick/.hq/.hq.primary` 与 `#uploadTtPanel` 主按钮（品牌色，含深色主题用 CSS 变量），插入到响应式前。

## 测试护栏与真机验收
- **`test_29` 仍 22/0**（只动前端布局，断言未受影响）。
- **JS 语法校验**：`node --check` SYNTAX OK。
- **真机 Playwright 探针（msedge）RESULT: PASS**：
  - 默认主页 = 日程（chatStage `panel-mode`=true、`#view-schedule` 可见、侧栏 `日程` active、`校园问答` 非 active、`.notices` 计算 `display=none`）；
  - `homeQuick` 5 个按钮、可见、主页无 `.close` 按钮；
  - 点「校园问答」→ 切到对话（`panel-mode`=false、`.notices` 显示、侧栏 `校园问答` active）；
  - 点回「日程」→ `panel-mode` 恢复；
  - 点主页「上传课表」→ 触发文件选择对话框（接线正确）；
  - **Console 无报错**（已过滤 favicon 404）。
- 上传课表完整流程未动（仍走既有 `/api/upload` + 管家导入 + 确认卡），本轮仅改入口与样式。

## 提交（二分，按纪律）
- 代码：`app/static/student.html`（2770a13）
- 文档：本快照 + README 布局说明 + 索引

## 备注
- 纯前端改动，硬刷新即可见，无需重启后端（后台 uvicorn 仍运行）。
- 侧边栏仍保留全部 nav（校园问答/日程/代办/设置/更多:快递/外卖），作为标准导航；主页模块条是其快捷镜像。
