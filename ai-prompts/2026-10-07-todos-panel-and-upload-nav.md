# 2026-10-07 待办面板字段修复 + 删除「上传文件」独立栏

来源：用户粘贴《CampusTime-改造方案prompt.md》（`D:/Workbuddy/2026-09-20-18-56-18/`，2026-09-20 写的）。
该文档第 0 节「最高纪律」写明 **AI 只分析出指令、绝不修改代码**，故先做**逐条核实**，
再由用户拍板执行范围（用户选「只做已核实的低风险两项」）。

## 一、逐条核实结论（对着当前代码 + 真机 curl）

| 条款 | 文档说法 | 实测 |
|---|---|---|
| 1.1 待办增删查改 | **已修复**（t.todo_id→t.id） | ❌ **根本没修**。真机 `GET /api/todos` 返回主键是 `id`、**无 `todo_id`**；`student.html` 的 `loadTodosView` 仍写 `t.todo_id` → `data-id="undefined"` → 勾选/删除打 `/api/todos/undefined` 全 404 |
| 1.2 工单不弹卡 | woSubmit 无卡片 + 静默 catch | ✅ 属实且**仍未修**（localStorage + 硬编码「已提交」徽标 + `catch(e){}`，无 renderOptions） |
| 1.3 加课卡 | 3c-bis 在 main.py ~2402 | ✅ 属实（2402-2423）；`wants_add_course`/`parse_add_course` 在 planner.py **3847/3884**（行号完全一致）。触发是否过窄**未实测** |
| 1.4 标记已读 | 缺失 | ✅ 仍未实现，`已读|mark_read|read_at|is_read` 在 `app/` 下**零命中** |
| 2.5 手改日程 | 无第三种入口 | ✅ 仍未实现，只有注释「面板是列表式排版（没有时间轴）」 |
| 第 4 节 删「上传文件」栏 | 6 处 | ✅ **全部仍在，行号与文档预测几乎完全一致** |

## 二、本轮执行的两项改动

### ① 修 1.1：待办面板主键字段（`app/static/student.html`）

`loadTodosView` 里 2 处 `encodeURIComponent(t.todo_id)` → `encodeURIComponent(t.id)`。

**★ 关键辨析（别一刀切全局替换）**：`todo_id` **不是统一错字段**。
`confirm.html:276/296/326/360` 与 `main.py:1273/1292/1358/1376` 用的 `todo_id` 是
**提案卡**字段，由 `planner.py:3315/3703` 造的 `"todo_id": item.get("id")` —— **是对的，绝不能动**。
只有 `loadTodosView` 把**原始待办**（来自 `/api/todos`，主键 `id`）当提案用了，才是真 bug。
所以只改这 2 处、不做全局替换。

### ② 删「上传文件」独立栏（`app/static/student.html`，方案第 4 节 6 处）

1. 左侧导航项 `data-nav="upload"`（546）
2. 视图容器 `navUpload` 整个节点（674-676）
3. `MODULE_GREETING` 的 `upload:` 那行（1510）
4. `NAV_TITLE` 去掉 `upload:"上传文件",`（2112）
5. 切换分支 `else if(view==="upload") loadUploadView();`（2134）
6. `loadUploadView()` 函数 + `upInput`/`upDrop` 事件处理器整段（2261-2283，到 `loadSettingsView()` 之前）

**保留项已核实仍在**：`ttFilePanel` / `uploadTtPanel` / `handleFile`（位于 `view-schedule` 内），
「上传课表」不受影响。

## 三、验证

- 方案第 4 节自检命令全过：`loadUploadView`/`data-nav="upload"`/`navUpload`/`upInput`/`upDrop`/`upList`
  **全部无命中**；`uploadTtPanel`/`ttFilePanel`/`handleFile` **有命中**。
- 新增 `tests/test_25_todos_panel_and_nav.py` **11 项全绿**（钉住 t.id、6 个删除项、3 个保留项）。
- 全量回归见本节末。
- 静态文件改动：浏览器**刷新即生效**（无需重启后端）；仍按项目习惯重启了 uvicorn。
- 先 grep 确认 `tests/` 里两处「上传文件」命中都是**无关上下文**（test_05 是文档串、test_23 是探测器样例句），
  没有任何测试断言该导航存在 → 删栏不会影响测试。

## 四、提交（未推送，PAT 0 字节）

`student.html` 代码 / `test_25` 测试 / 本快照+索引 文档，三 commit 分开。

## 五、仍未做（等用户再拍板）

1.2 工单弹回执卡、1.4 标记已读、2.1/2.2 抽取优先于关键词闸门、2.3 写操作统一收编进 options 卡、
2.5 时间轴手改日程（第三种入口）。其中 2.3/2.4 的「Propose / Execute 分离」架构红线**项目本来就有**，
工单（1.2）是漏网之鱼。
