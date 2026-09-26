# AI 对话快照 · 2026-09-26（续）· 加课被当代办：漏接时段词 + 样本污染双重根因

> 关联前一篇：`2026-09-26-retime-live-collision-fix.md`（改期收尾）。
> 本篇是另一条**真机报障**：学生说「周一早上加一节高数课」，系统却把"高数课"当成**待办**加进了日程，
> 而不是落到**课表**。这是"加课"和"加待办"两条判定分支打架，且被一条被污染的样本放大了。

## 用户原话 / 背景

- 学生报障（原话大意）：**「我说加一节课的时候，把课当代办加进日程了」**。
- 典型触发句：`周一早上加一节高数课`、`周四早上加一节高数课`。
- 期望行为：这种带"课"字 + 星期 +（上午/下午/晚上/钟点）的句子，应走**加课（课表）**分支，
  出 `timetable_change` 确认卡，且**绝不**写进 `todos`。
- 反向约束：带"课"字但说的是**运动/兴趣课**（瑜伽/游泳）仍应归**待办**，不能误判成课表。

## AI 研判（逐步定位根因）

离线用 `wants_add_course` / `wants_add_todo` / `understand()` 对 `周一早上加一节高数课` 打点：

1. `wants_add_course(t)` = **False**（根因①）。
   - 旧 `wants_add_course` 只认「星期 + 钟点」或「钟点 + 课名」或「星期 + 课名」。
   - `_pick_span` 只匹配显式钟点（`HH:MM` / "X点"），**不认"早上/上午"**这类时段词。
   - 所以"周一**早上**加一节高数课"既没钟点、课名"高数"又没被抠出来 → 判定 False。
2. `wants_add_todo(t)` = **True**（规则层含"加一节"加待办词表）。
3. 更糟的是 `understand()` 里 `match_sample()` **先于** `classify_with_llm()` 命中了一条
   **被污染的种子**：`app/data/intent_samples.json` 里存着
   `"周一早上加一节高数课" → intent: add_todo, title: 高数课`（这正是 bug 本身被记进了样本库，
   越记越错）→ `_sem_intent = INTENT_ADD_TODO`。
4. 于是 `main.py` 第 ~1707 行的**加待办**分支（在加课分支 ~1868 之前）先命中，
   把"高数课"当待办出了 `todo_add` 卡并写进 `todos`。

> 根因是两处叠加：① `wants_add_course` 漏接时段词；② 一条把加课记成 add_todo 的污染样本，
> 让语义/样本层也站在了"当待办"那边。两者任一堵住都能救，但必须**双管齐下**才稳。

附带的小问题：`_pick_course_name` 没把"早上/上午"剥掉 → 课名被抠成"早上高数"，
并污染到地点识别（`_looks_like_location` 把这句误判成地点）。

## 代码改动

### 1. `app/modules/planner.py`

- **新增 `_pick_part(text)`**：抠出"上午/早上/早晨/一早 / 中午 / 下午 / 晚上/傍晚/夜里"等时段词。
- **`wants_add_course` 重写**，接受"星期 + 时段词（带课字）"：
  ```python
  day, begin = _pick_day(t), _pick_span(t)[0]
  part = _pick_part(t)
  course = _pick_course_name(t)
  if day is not None and begin:                 return True
  if day is not None and part and "课" in t:    return True   # 星期+上午/下午/晚上(带课)
  if begin and course:                          return True
  if day is not None and course and "课" in t:  return True
  return False
  ```
  反向闸门：无"课"字（瑜伽/游泳）一律 **不** 走加课。
- **`parse_add_course` 改造**：给到"星期 + 时段词"但没钟点时，从 `_PART_PERIODS`
  （上午 08:00/10:00、中午 12:00、下午 14:00/16:00、晚上 19:00 这几个标准节次）里
  **挑当天下午/上午仍空闲的那一节**（`_slot_free` 与现有课表比对，避开冲突）；
  若该半天上满则回 `None`，由对话层追问具体钟点。
  （首版用固定 08:00–09:40 会跟周一《高等数学》撞车 → 已改成"挑空闲节次"。）
- **`_pick_course_name`**：strip 列表补上"早上/上午/下午/晚上/中午/傍晚/早晨/一早/夜里…"，
  让"周一早上加一节高数课"抠出"高数"而非"早上高数"。
- **`_looks_like_location`**：加入"加/课/周/早上/上午/…"黑名单，这条命令式残句不再被误判成地点。

### 2. `app/main.py`

- **加待办分支加硬闸门**：原条件尾部补 `and not wants_add_course(message)`，
  即"只要被判定为加课，就绝不许漏进加待办分支"——即便语义层误判成 add_todo 也兜得住。
  ```python
  if ((not _rule_add_veto
          and (wants_add_todo(message) or (asking_add and is_add_todo_answer(message))))
          or _sem_intent == INTENT_ADD_TODO) and not wants_add_course(message):
  ```
- **3c-bis 追问改进**：原来一律回「加课缺星期」，现在先 `_pick_day` 判缺的是星期还是时间，
  缺星期问"星期几"，星期有了但连时段词都没给才问"几点/上午下午晚上"。

### 3. 清掉污染样本（gitignored，不入库，仅修本地演示态）

- `app/data/intent_samples.json` 删除 `"周一早上加一节高数课" → add_todo` 这条被 bug 自己写进去的种子。
- 同步清掉 live 真机/手测写进 `app/data/student/todos.json` 的 `高数课` 待办与 `游泳` 待办、
  `app/data/pending.json` 里的 `健身` 待办残片，恢复 3 条演示种子，让本地套件与干净 checkout 一致。

### 4. `tests/test_06_add_things.py`（新增第十八批回归）

覆盖本 bug 的每一个断言：
- `周一早上加一节高数课` → `wants_add_course` True / `wants_add_todo` False；
- `parse_add_course("周四早上加一节高数课")` → 提案 `action.course` 含"高数"、`new_start=="08:00"`、`day==4`；
- 端到端 POST `/api/chat` "周四早上加一节高数课" → 出 `timetable_change` 卡（而非 `todo_add`），
  且 `todos` 里**没有**高数课、周表条数不变（等学生确认）；
- 反向护栏：`加一节瑜伽` / `周一早上加一节瑜伽` / `周六下午加一节瑜伽` **不** 算加课。

## 测试护栏

- `tests/test_06_add_things.py` 第十八批：**17/17 通过**；全文件 18 批 **0 失败**。
- `git stash` 基线比对确认：修复前 test_06 的 17 项失败为**历史环境残留**（live 数据污染），
  与本次改动无关；清掉本地污染数据后全绿，干净 checkout 评测亦不受影响。

## Commit（每个逻辑改动单独提交，不与快照混在一起）

- `app/modules/planner.py` — 加课漏接"上午/下午/晚上"时段词（_pick_part + wants_add_course 重写 + parse 选空闲节次 + 抠名/地点修正）
- `app/main.py` — 加课优先于加待办的硬闸门 + 3c-bis 追问区分缺星期/缺时间
- `tests/test_06_add_things.py` — 第十八批「加课被当代办」回归
- `ai-prompts/2026-09-26-add-course-as-todo-fix.md` — 本快照（与 README 索引同一 commit）
