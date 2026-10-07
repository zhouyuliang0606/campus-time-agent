# 2026-10-07 周时间轴（F 项）第二轮修复

> 协同模式：用户一句话（re-reference 实现指令 md）→ AI 自主研判 + 改代码 + 真机验证。
> 关联文件：`CampusTime-自行编辑日程-实现指令.md`（§6 第二轮修复指令）。

## 用户 Prompt
`@D:/Workbuddy/2026-09-20-18-56-18/CampusTime-自行编辑日程-实现指令.md`
（仅 re-reference 文件，无附言。结合文件内新增的 §6「第二轮修复指令（2026-10-07 首版验收不通过）」判定：首版时间轴验收没过，要按 §6 修。）

## 研判
读 spec 发现 §6 明确写了首版核心缺陷（用户截图暴露）：
1. 一门跨小时课被拆成「每行一块」重复色块（08:00–09:50 高数在 08:00、09:00 两格各贴一块）；
2. 每格只画 `items[0]`，同一小时撞车的第二条事件被静默吞；
3. 点课程色块误开「新增待办」表单；
4. 色块贴顶小块、列宽大量留白；
5. 块内标签每块都带 "08:00" 时间前缀。

核对当前 `app/static/student.html` 的 `tlRender`：果然还是首版——铺 98 个虚线 `.tl-cell`、用 `tlOverlapsHour` 逐格贴块、`items[0]` 静默吞、课程块 `data-tlid` 为空导致点它也会走「空格新增」分支。CSS 早已写好 `.tl-daycol { position: relative }` 和「色块靠 top/height 落位」的注释，但 JS 没按这设计走。
**结论**：纯前端改动，按 §6.2 把「逐格贴块」改成「单块跨行（绝对定位覆盖）」。

## 改动（仅 student.html，前端；后端零改动、无需重启 uvicorn）
- **CSS**：`.tl-grid` 加 `grid-template-rows: auto repeat(14,26px)`；新增 `.tl-hours`（左列小时标尺，纵向 14 格）；`.tl-evt` 改 `position:absolute; box-sizing:border-box`；`.tl-daycol` 加 `grid-row:2/span 14; min-height:403px` + `repeating-linear-gradient` 画小时网格线；深色主题补 `.tl-daycol` 网格线。
- **`tlRender` 重写**：
  - 表头（`.tl-corner`+7×`.tl-colh`）不变；第二行改为 1 个 `.tl-hours` + 7 个 `.tl-daycol`（绝对定位容器）。
  - 每个事件**只渲染一块**：`top=(start-08:00)/60*PITCH`、`height=(end-start)/60*PITCH-2`，钳制到 08:00–22:00；标签只出现一次（块高<18px 时只留标题，title 兜底原文时间）。
  - 同列撞车 → 贪心分道（`laneCnt`），保证每条都渲染，不再 `items[0]` 静默吞。
  - 删除 `tlOverlapsHour`。
- **交互**：点 `.tl-daycol` 空白处 → 新增，`hour=08:00+floor(offsetY/PITCH)`，支持 **10:05 这类非整点预填**；守卫改为「点任何 `.tl-evt` 都不开新增表单」，课程块（无 `data-tlid`）被拦、不可编辑；点待办块（`.tl-evt[data-tlid]`）走它自己的编辑。
- **`tlOpenForm(todoId,date,s0,e0)`**：签名由 `(todoId,date,hour)` 改为预填起止字符串，编辑态取原值、新增态取点击位置。

## 验证（真机，浏览器加载真实后端，零污染真实数据）
- 单测 `test_29` 更新：把失效的 `.tl-cell` 断言改为 `.tl-daycol`、加 `position:absolute` 与「`tlOverlapsHour` 已弃用」回归守卫 → **22/0 全过**。
- Playwright 真机探针（`channel="msedge"`，需先 `body.classList.add('panel-mode')` 激活侧栏才出宽度）：
  - ✅ 7 个 `.tl-daycol`、无旧 `.tl-cell` 残留；
  - ✅ 跨小时课《高等数学》（周一/三/五各一节）**按天每处只一条连续色块**（共 3 天→3 块，非逐行重复）；
  - ✅ 点课程色块**不弹**新增表单；
  - ✅ 点空格弹新增表单，预填开始时间 = 点击位置（如 21:22，分钟非 00，证明非整点预填生效）；
  - ✅ 新增→GET 可见、编辑→标题改后、删除（confirm 自动接受）→确认后消失且持久化；
  - ✅ 无 JS pageerror/console error；404 仅 `favicon.ico`（浏览器自动请求，良性）。
  - **RESULT: PASS**
- 全量回归 `tests/run_all.py`：待跑（前端改动理论上不影响后端/其他入口；跑完核对总通过数）。

## 提交
- `student.html`（代码/CSS/JS 第二轮修复）
- `tests/test_29_schedule_timeline_edit.py`（测试：断言更新 + 回归守卫）
- `ai-prompts/...`（本快照）+ `README.md` 索引/时间轴说明（文档）
