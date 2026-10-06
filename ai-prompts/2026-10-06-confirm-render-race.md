# 2026-10-06（续二）课表卡「后端出了、前端没弹」——确认条渲染竞态

学生贴出上一轮诊断的真实界面输出，证明后端**已经出卡**
（`1🔍 课表导入探测：命中 … 模型自带 options 数 = 1` /
`2✅ 模型自己调了 propose_timetable_change，引擎已出卡 … timetable_change 卡数 = 1`），
但界面上**没有弹卡**。诊断立功了——把故障从「出卡环节」精确缩小到「前端 / iframe 渲染」。

## 用户 Prompt（原文粘贴的聊天界面输出，节选）

```
1🔍 课表导入探测：命中（识别成「读课表文件导入」） — 模型自带 options 数 = 1
2✅ 模型自己调了 propose_timetable_change，引擎已出卡（只统一回话） — timetable_change 卡数 = 1
没有弹卡
```

## AI 研判（根因）

后端把 `timetable_change` 卡写进了 `/api/chat` 的 `options`（`_remember_todo_options`
**原样返回 options，不丢 kind**，已核实）。出卡没问题。故障在前端渲染链：

1. `student.html` 的 `renderOptions` 对单张 `timetable_change` 走 `openConfirm(prop, …)`
   → 嵌一个 `/confirm` 的 **iframe**；`fr.onload` 时把真卡 `prop` 通过 `postMessage` 递给 iframe。
2. `confirm.html` 的 `load()` 先 `fetch("/api/pending/<sid>")` 取数渲染。
   **但 `timetable_change` 根本不进暂存**——`_remember_todo_options` 只持久化
   `todo_add/todo_mode/todo_slots`，timetable_change 被 `continue` 跳过，
   所以 `/api/pending/<sid>` 对"纯课表导入"会话返回 `{"pending": null}`。
3. `render(null)` → 命中 `if (!k)` 分支 → **同步调用 `tellParent("empty")`** →
   父页面 `student.html` 的 message 监听执行 `removeCfWrap()`，把整个 iframe 从 DOM 摘掉。
4. 父页面的 `postMessage`（带着真卡）此刻才/即将到达，只能渲染进一个**已被摘掉的 iframe**，
   学生什么都看不到。

为什么**只有课表导入**中招、其他确认卡正常：todo 类提案进了暂存，
`/api/pending` 能取到 `todo_pick`，`render` 走 `k` 真分支、**不调** `tellParent("empty")`，
所以卡照常显示；timetable_change 取不到 → 进空分支 → 撤条 → 卡消失。这是确定性的 race，
不是偶发。

## 改动（app/static/confirm.html 的 render()）

在 `render(p)` 的空分支里，按是否嵌在聊天框（`inFrame`）分两路：

- **嵌在聊天框（`inFrame` 为真）**：暂存取空**不再立即** `tellParent("empty")`。
  先显示「正在加载确认条…」，并 `setTimeout(1200ms)` 延迟发 `tellParent("empty")`，
  且门控在 `if (!fromParent)`——父页面的 `postMessage` 一旦到达（`fromParent=true`、
  渲染真卡），延迟回调就跳过撤条。彻底消除"卡刚出好就被自己撤掉"的竞态。
- **独立打开（非 iframe）**：保留原行为（立即 `tellParent("empty")`），
  独立确认页本来就是"没提案就空着"，不受 postMessage 影响。

改动只动渲染时序，不动写库路径、不碰执行层红线；`postMessage` 仍是官方注释里的
"双保险"，只是不再被提前的 empty 通知秒撤。

## 验证

- 新增 `tests/test_24_confirm_render.py`（**5 项全绿**）：静态守住三件事——
  ① inFrame 空提案显示「加载中」而非立即撤条；② `tellParent("empty")` 被延迟且门控 `!fromParent`；
  ③ 独立打开分支仍立即 `tellParent("empty")`（原行为不变）。
- 全量 **1733 通过 / 1 失败（25 批）**，1 红仍是 pre-existing 的 `test_06` 老项，
  较上轮 1728 净增 5 项、**零回归**。
- 后台重启到新 PID（旧 41200 已 kill，新 uvicorn 起在 8000），
  `/student` 与 `/confirm` 均 HTTP 200。
- ⚠️ 学生侧需**硬刷新**（Ctrl+Shift+R）浏览器，否则可能命中旧的 `confirm.html` 缓存。

## 提交（未推送，PAT 0 字节）

`app/static/confirm.html` 代码 / `tests/test_24_confirm_render.py` 测试 /
本快照 + `tests/README.md` 索引 文档，三 commit 分开。

## 已知边界（告知学生，未改）

- 若 iframe 整体被环境拦截（`confirmPageOk` 走 error 分支），`timetable_change` 目前**无内联兜底**
  （todo 类有 fallback，timetable 没有）——属既有设计，非本轮引入；本轮只修"卡出了却被自己撤掉"。
- 独立打开 `/confirm?session=…` 时若会话是纯课表导入，`/api/pending` 仍取不到 timetable_change，
  需靠 postMessage（嵌在聊天里才有）。如需独立页也显示，要另把 timetable_change 也持久化进暂存，
  那会碰执行层数据模型，留待拍板。
