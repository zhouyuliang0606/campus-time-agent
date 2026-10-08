# 2026-10-08 完整日程/去问AI 不再新开窗口

## 用户原话 Prompt（摘录）
- （回退布局重构后）「改不另开」——指前面说的「打开完整日程再点去问ai，会新开一个窗口」，要求改成不弹新窗。

## AI 研判与决策
- 用户说的"新开窗口"链路：学生端点顶栏「📅 完整日程」→ 新标签页打开 /plan → 在 /plan 点「去问 AI →」。
- **根因**：`student.html` 顶栏完整日程链接带 `target="_blank"`（唯一的新窗来源）。
  `/plan` 内的「去问 AI →」（`plan.html:131`）本就是 `<a href="/student">` 普通同标签链接，
  自身不新开窗口——之前感知到的"第二个窗口"是 /plan 作为新标签页存在、再导航的观感。
- 工具栏的「完整版日程 →」(student.html:737) 本就无 `target="_blank"`，同标签，无需改。
- 修复：删掉顶栏完整日程链接的 `target="_blank"`，整条链路同标签跳转。

## 具体改动（`app/static/student.html`，1 行）
- 第 702 行：`<a class="tb-link" href="/plan" target="_blank">📅 完整日程</a>` → 去掉 `target="_blank"`。

## 测试护栏与真机验收
- **真机 Playwright 探针 RESULT: PASS**：在 /student 点顶栏「完整日程」→ `url=/plan`、无 popup；
  在 /plan 点「去问 AI →」→ `url=/student`、无 popup；`popups=[]`、无 pageerror。
- 仅改 1 行链接属性，无逻辑/测试改动；`test_29` 不受影响（未重跑，纯属性改动）。

## 提交（二分，按纪律）
- 代码：`app/static/student.html`（4c55d50）
- 文档：本快照 + 索引

## 备注
- 纯前端改动，硬刷新即可见，无需重启后端。
- /admin、/station 顶栏链接仍保留 `target="_blank"`（用户未要求改，属独立工具）。
