# 2026-10-07 自行编辑日程：学生端「我的日程」加周时间轴直改待办（第三种入口）

## 用户 Prompt（原话摘录）

> 做完这个把 readme 也写了
>（附 `CampusTime-自行编辑日程-实现指令.md`：F 项——在学生端「我的日程」里加左侧周时间轴，
> 不对话、不上传，直接点格子增/改/删日程）

## AI 研判

现状核了一遍（按指令给的锚点，不死盯行号）：

- `app/static/student.html` 的 `view-schedule` 是**列表式**周表 + 月表（只读展示），
  注释明确写"管理去完整版 /plan" —— 学生想自己动手改一条，在聊天面板里**没有落点**；
- 已有三条入口：① 对话出卡 ② 上传课表（`0f9ab36` 修好的出卡通道）③ `/plan` 独立整页；
- 写库接口齐备：`POST/PUT/DELETE /api/todos`（`main.py`），**F 项不新增后端接口**。

指令把这条入口定性为"**用户本人直接操作**，不是 AI 提案"，所以**不走 options 提案卡**，
直接调既有确定性接口；但破坏性操作（删）沿用 `plan.html` 的 `confirm()` 范式。

## 改动（`app/static/student.html`，一处文件；静态改动刷新浏览器即生效）

**1. CSS（浅色 + 深色两套）** — 新增 `.tl-*` 系列：`.tl-grid`（7 列 × 每小时一行的 Grid）、
`.tl-cell`（可点空格）、`.tl-evt`（待办/课程色块，`.done` 划线）、`.tl-form`（内联表单）、
`.tl-notime .tl-chip`（没定时间的待办单独列）。深色主题在既有主题块内补一遍覆盖，不留白底。

**2. HTML** — 在 `view-schedule` 的 `ttPanelStatus` 之后、`schWeek` 之前插入 `#tlWrap`
（含 `#tlGrid` / `#tlNoTime` / `#tlFormBox` / `#tlNote` / `#tlRange`）。列表仍是列表，
时间轴是**额外区块**，不改 `showView` 的显隐逻辑。

**3. JS** — 新增（统一 `tl_` 前缀，与 `loadScheduleView` 等既有逻辑隔离）：
- `tlRender()`：画 08:00–22:00 × 周一~周日的格子；按 `schCourses`/`schTodos`（与列表同源）
  把安排落成色块；`tlOverlapsHour()` 判交叠落格；渲染后绑事件；
- 点空格 → `tlOpenForm(null, date, hour)`（新增，预填这一格）；
  点色块 → `tlOpenForm(id, ...)`（编辑，预填原值；课程色块不可点）；
- `tlSave(editId)`：新增走 `POST /api/todos`、编辑走 `PUT /api/todos/:id`，
  成功后 `loadScheduleView()` 刷新（与既有"确认完立刻刷新日程"惯例一致）；
- `tlDelete(editId)`：`confirm("确定删除这条待办？")` → `DELETE /api/todos/:id`；
- 没定时间的待办（`start/end` 缺失）不硬塞进格子，列在 `#tlNoTime` 里，点了照样能编辑；
- `loadScheduleView()` 末尾加 `tlRender()`；`schPrev`/`schNext` 翻周时也 `tlRender()`。

## 红线（逐条自守）

① 删有 `confirm`，不一点就删；② 写库走既有 `/api/todos` 接口，前端只是调用方，
不让模型直连写库；③ **不碰 `todo_id` 提案卡字段**（那是 confirm 卡专用的，见历史护栏）；
④ `tl_` 前缀不撞名；⑤ 只改静态文件，刷新浏览器即生效。

## 验证

- **真机探针（真实浏览器，非只跑单测）**：`tl_probe.py` 打开 `/student` → 切「我的日程」→
  点空格新增 → 核对 `/api/todos` 落库（`6dc102d4`）→ 点色块改标题 → 核对改掉 →
  点色块删（自动接受 confirm）→ 核对清空、待办总数回到 5（**零残留**）。
- 页面干净加载：非 2xx 响应 = 0（`tl_404.py` 核过）。
- 内联 JS 过 `node --check`（语法 OK）。
- 新增 `tests/test_29_schedule_timeline_edit.py` **21 项 / 3 批**全绿
  （源码接线断言 + 深色主题 + TestClient 沙箱接口往返，证明与 `/api/todos` 同源）。
- 全量 `tests/run_all.py`：**1824 通过 / 3 失败（30 批）**；3 红为 test_06/test_23 老项，
  已 `git stash` A/B 证明**改动前就红**（与本轮无关）。
  ★ 顺带把上午遗留的 13 红收敛到 3 红：收紧 `wants_add_todo` 的放行条件
  （要求动作动词"去/到/来"）后，test_09/test_10 的误判回归全部消失。

## 关联 commit

代码 `student.html` → 测试 `test_29_*` → 文档快照 + README。未 push（PAT 0 字节）。
