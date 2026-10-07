# 2026-10-07 周时间轴 + 日程表「放一起」（同一组 tab）

## 用户原话 Prompt（摘录）
- 「周时间轴和日程表放一起吧」（附截图：两个日程表上下分离、各自像独立卡片）
- 「你卡住了重新来」（打断：要求直接动手，别卡在读文件）

## AI 研判与决策
- **现象根因**：`view-schedule` 里周时间轴 `tlWrap` 在列表式 `schWeek`/`schMonth` 上方，**两者默认同时显示**（初始加载根本没调 `setSchTab()`，display 全靠各自默认态），所以学生看到的是「上面一张时间轴卡片 + 下面一张列表卡片」两个分离块——正是他说的「两个表」。
- **整合方案（最贴合「放一起」）**：把时间轴、列表周表、列表月表做成「我的日程」面板下**同一组 tab 切换**——`tabTimeline 时间轴` / `tabWeek 周表` / `tabMonth 月表`，默认激活「时间轴」。三者同源 `/api/todos`+`/api/timetable`，由 `setSchTab()` 统一显隐与刷新，互不干扰：列表照旧只读，时间轴才允许动手。
- **为什么不用「并排」**：并排会把窄面板挤得更挤、跨小时色块可读性下降；tab 切换既「放一起」（同一面板同一区域）又各自清爽，且改动最小、零风险（纯 `student.html` 前端接线，不动 `app/*.py`）。若学生想要并排，再据反馈调整。

## 具体改动（`app/static/student.html`，纯前端）
1. **工具条加 tab**：在 `tabWeek`/`tabMonth` 前加 `<button class="tab active" id="tabTimeline">时间轴</button>`，原周/月表默认非激活。
2. **默认 tab 改 `timeline`**：`let schTab = "week"` → `"timeline"`（1800 行）。
3. **`setSchTab()` 重写**：三按钮 active 类切换 + `tlWrap/schWeek/schMonth/schDayDetail` 显隐统一由 `schTab` 决定；`schTab==="timeline"` 调 `tlRender()`，否则 `renderSchCurrent()`。
4. **初始同步显隐**：`loadScheduleView()` 末尾由 `tlRender()` 改为 `setSchTab()`（首次进入「我的日程」即按默认 tab 正确显隐，修掉「两个表同时堆着」）。
5. **tab 监听 + 上下周/月按钮**：新增 `tabTimeline` 监听；`schPrev/schNext` 改为「月表调 `schMonthOffset`、其余（时间轴/周表）调 `schWeekOffset`」后统一 `setSchTab()`（时间轴是周视图，前后翻周）。
6. 更新时间轴说明注释，点明三者同组 tab。

## 测试护栏与真机验收
- **`test_29` 自行编辑日程（周时间轴）仍 22/0**（只动纯前端接线，断言未受影响）。
- **JS 语法校验**：提取内联脚本 `node --check` → SYNTAX OK。
- **真机 Playwright 探针（走系统 Edge，`channel="msedge"`）RESULT: PASS**：
  - 默认 `tabTimeline.active=true`、`tlWrap` 可见、`schWeek/schMonth` 隐藏、`#tlGrid .tl-daycol` = 7；
  - 点「周表」→ `tlWrap` 隐藏、`schWeek` 可见、`schMonth` 隐藏；
  - 点「月表」→ `schMonth` 可见、`tlWrap` 隐藏；
  - 点回「时间轴」→ `tlWrap` 可见、7 个 daycol 仍在；
  - **Console 无报错**（已过滤 favicon 404）。
- 增改删持久化逻辑未动（仍走 `test_29` 的 API 往返 + 真机探针验证过），本轮仅改视图整合。

## 提交（三分，按纪律）
- 代码：`app/static/student.html`（tab 整合接线）
- 测试：`tests/test_29_schedule_timeline_edit.py` 未改动（维持 22/0，无新测试文件必要；tab 切换由真机探针覆盖）
- 文档：`README.md` 补「三者同组 tab」说明 + 本快照 `ai-prompts/2026-10-07-schedule-timeline-tab-merge.md` + 索引

## 未决 / 备注
- 用户截图模型侧不可读（当前模型不支持读图），故以「同组 tab 整合」为默认实现并说明，等学生确认是否要「并排」变体。
- 纯前端改动，浏览器硬刷新即可见，无需重启后端（但本轮为跑真机探针已起 uvicorn）。
