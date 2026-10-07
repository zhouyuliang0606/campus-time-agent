# 2026-10-08 周时间轴 + 周列表「上下同卡」（终态）

## 用户原话 Prompt（摘录）
- （前一轮已交付「时间轴/周表/月表」一组 tab 切换）→ 用户发截图，只问「怎么排能看清楚」
- 我给了三种排法推荐（上下同卡 / 左右并排 / 点天钻取），说明窄侧栏下上下最清楚
- 用户回「上下」→ 直接落地

## AI 研判与决策
- 用户要「放一起」的终态 = **时间轴在上 + 周列表在下，同卡片一起滚动**（不是 tab 来回切，也不是左右并排）。
- 左右并排被否：侧栏窄，7 列时间轴 + 7 列列表并排会各挤成一半宽，色块和字都看不清。
- 月表尺度不同（整月日历），保留独立 tab，不与周视图混。
- 工具条从「时间轴/周表/月表」三 tab 收敛为「周视图(默认) / 月表」两 tab；周视图内时间轴 + 列表上下同卡。

## 具体改动（`app/static/student.html`，纯前端）
1. 工具条：删独立 `tabTimeline`；`tabWeek` 改名「周视图」、默认 active；保留 `tabMonth`。
2. 结构：时间轴 `tlWrap` 与周列表 `schWeek` 之间加「📋 逐日明细」小标题 `schWeekHead`（随周列表显隐）。
3. `schTab` 默认 `timeline` → `week`。
4. `setSchTab()` 重写：周视图同时显示 `tlWrap + schWeek + schWeekHead` 并各渲染（`tlRender()`+`renderSchWeek()`）；月表独立显 `schMonth`；删 `timeline` 分支。
5. `schPrev/schNext`：周视图翻 `schWeekOffset`、月表翻 `schMonthOffset`，统一 `setSchTab()`。

## 测试护栏与真机验收
- **`test_29` 仍 22/0**（只动纯前端接线，断言未受影响）。
- **JS 语法校验**：`node --check` SYNTAX OK。
- **真机 Playwright 探针（msedge）RESULT: PASS**：
  - 默认周视图 active、`tlWrap`/`schWeek`/`schWeekHead` 均可见、`schMonth` 隐藏、`#tlGrid .tl-daycol`=7；
  - DOM 顺序时间轴在列表前、渲染位置时间轴 y≈233 < 列表 y≈782（确为上下堆叠）；
  - 切月表→`tlWrap`/`schWeek`/`schWeekHead` 隐藏、`schMonth` 显示；切回周视图三者恢复、7 列仍在；
  - **Console 无报错**（已过滤 favicon 404）。
- 增改删持久化逻辑未动（仍走 `test_29` 的 API 往返 + 先前真机探针），本轮仅改视图整合。

## 提交（二分，按纪律）
- 代码：`app/static/student.html`（dfcf6c5）
- 文档：`README.md` 三点五节说明改为「上下同卡」终态 + 本快照 + 索引

## 备注
- 纯前端改动，浏览器硬刷新即可见，无需重启后端。
- 若日后侧栏变宽（如全屏模式），左右并排可作为可选变体再加。
