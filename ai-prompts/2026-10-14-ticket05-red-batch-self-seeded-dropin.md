# 05 · 测试红批「沙箱自种最小数据」实现 prompt（给 Qoder / 落地 AI）

> 背景：CampusTime 自检套件有一批"红线测试"（`test_06` / `test_23` / `test_40`）目前是红的，pytest 门也红。
> 根因是它们依赖真实演示数据 `app/data/student/todos.json`，没有在沙箱里自己准备数据。
> 本 prompt 落地用户拍板的 **方案 ①：让红批在沙箱里自种一份最小可验的数据**，
> 不依赖任何人的真实课表 / 待办。方案 ②（把演示数据 commit 进仓库）不采用，但**否决理由不是隐私**：
> 演示数据本就是 AI 生成的假数据，不含真实学院/教室/姓名，`courses.json` 作为 AI 生成演示种子早已入库、Public 也安全。
> 真正否决它的是**架构与确定性**：`student/todos.json` 是运行时数据（已 gitignore），把"恰好 6 条"这种状态 commit 进去，
> 等于让测试重新依赖"文件里恰好有某份数据"——这正是这一打红批当初变红的根因；且只要你手测改过这份文件，本地测试立刻又红。
> 自种数据 = 克隆下来任何环境一套就绿，谁的真实/演示数据都不要。

---

## 一句话目标
让那 ~14 条红批在开始验证前，自己往沙箱里写一份"恰好已知"的待办数据（例如正好 6 条），
从此测试结果与谁的真实数据无关；全套变绿、pytest 门变绿、README"失败 0 项"成真。

---

## 根因（先读再动手，别凭印象）
- `tests/_harness.py` 的 `sandbox()`：把真实 `app/data` 复制到临时目录，并清掉 `SCRATCH` 清单里的
  "运行时痕迹"（`settings.json` / `uploads` / `notices` / `student_messages` / `workorders` / `intent_samples`）。
- **`student/todos.json` 故意没进 `SCRATCH`**（见 `_harness.py:88` 注释：
  "⚠️ `student/todos.json` 还没进来（那是要不要顺带修 7 条存量红的大刀，见指南红线 18）"）。
- 于是沙箱副本里**带着真实待办文件**。红批断言"没确认 → 6 条一条没少"（`len(list_todos()) == 6`），
  一旦真实文件不是恰好 6 条就红。
- `tests/pytest_gate.py:21-23` 自己记着："本机全量 14 条存量失败（test_06×10 + test_23×2 + test_40×2），
  根因是 sandbox() 不清 student/todos.json，测试读的是真实演示数据。这件事在等拍板的账上。"

---

## 落点文件
1. `tests/_harness.py` —— 新增 `seed_todos(items)` 工具（参考同仓已有的
   `tests/_e2e_env.py` 的 `seed_todos(待办)` 与 `test_04_persona_options.py` 的 `_seed_timetable()`）。
   它把一份已知待办列表写成**当前沙箱**的 `student/todos.json`
   （用 `data_path('student','todos.json')`，绝不在沙箱外用 `REAL_DATA_DIR` 写）。
2. `tests/test_06_add_things.py` / `tests/test_23_timetable_import_rescue.py` / `tests/test_40_*.py`
   （先确认实际文件名）中**依赖"恰好 N 条已知待办"开头的红批** —— 在 `with sandbox():` 之后、
   做断言之前，先 `seed_todos([...已知 N 条...])`，让起始状态确定。
3. `tests/pytest_gate.py` —— 红批修绿后，把文件顶部 docstring 里
   "这条门目前是红的……本机全量 14 条存量失败……在等拍板的账上"这段删掉 / 改成"已绿"。

---

## 具体做法（给实现 AI 的步骤）
1. **先读 `开发者接手指南.md` 的「红线 18」**，按里面定的方案走
   （红线 18 大概率就是"红批自种数据、不读真实 student/todos.json"，与本方案一致）。
   **整族扫**：别只改被投诉那一句，先把所有"假设起始待办数"的地方找齐。
2. 在 `_harness.py` 加 `seed_todos(items: list[dict]) -> None`：
   - 路径用 `data_path('student', 'todos.json')`（必须在 `sandbox()` 之后调用，才是沙箱副本）；
   - 若 `student/` 目录不存在就 `os.makedirs`；
   - 直接 `json.dump(items, ...)` 覆盖写；
   - 不碰 `REAL_DATA_DIR`，不碰 `settings.json` 等 SCRATCH 文件。
3. 在三个红批文件里，找到"开头假设库里已有恰好 N 条待办"的那几批
   （test_06 约 10 处、test_23 约 2 处、test_40 约 2 处，对照 pytest_gate 的计数），
   在每批 `sandbox()` 内最前面 `seed_todos(那 N 条已知待办)`。待办内容用**假数据**
   （固定几门作业 / 事项，**不含真实学院 / 教室 / 姓名**）。
4. 跑 `python tests/run_all.py`，确认：
   ① 那 ~14 条变绿；② 总账"失败 0 项"；③ `pytest` 门 `test_run_all_suite_is_green` 过（退出码 0）。
5. **真机复验**：在一个 `student/todos.json` 为空或内容不同的干净环境各跑一次，
   确认不再依赖真实数据（这正是本方案要解决的脆弱点）。

---

## 给实现 AI 的纪律提醒（沿用项目铁律）
- **整族扫**：红批依赖数据的写法可能不止 3 个文件。先在 `tests/` 全量 grep
  `len(list_todos`, `student/todos.json`, `== 6`, `list_todos()) ==` 等，
  把所有"假设起始待办数"的地方一起改，别漏。
- **变异测试**：故意把 seed 的条数改错，确认断言会红（证明断言真的在盯这个数）。
- **不要一刀切清空** `student/todos.json`：有的批需要"有数据"才能验"删之前不动"，
  清空反而错；精准做法是"红批自种已知数据"。
- **不要走方案 ②**：把 `student/todos.json`（运行时数据，本就 gitignore）commit 进仓库当测试前置。
  隐私不是问题（演示数据是 AI 生成的假数据），问题是它会**重染**"测试依赖运行时数据状态"这个病根、
  且污染版本库（每次演示产生的待办都进 diff）。正确做法是复用本仓已有的
  `courses.json` 还原模式：红批在沙箱里自种已知数据（类比 `test_04` 的 `_seed_timetable()`），
  而不是依赖某个已提交文件。

---

## 验收（交给用户的标准）
- `python tests/run_all.py` → 总账"失败 0 项"，且任意真实数据环境下复跑一致。
- `pytest` → 门绿（无 ❌，退出码 0）。
- README 里"失败 0 项"从假话变真话（套件确实 0 失败即无需改文案；
  若想更稳，可把那行补一句"—— Y 应为 0"的模板说明）。
