# 45｜学生个人数据全量隔离：假课表 + 共享上传桶 + 跨人暂存卡（drop-in）

> 来源：2026-10-20 用户实测「学生2 没上传课表也能问出今日课表，读的是学生1的」，并要求「顺便看看学生的个人数据，有没有全部隔离」。
> 交付性质：给执行方（Qoder）的落地施工单。**我不改代码。**
> 本单是 **P0**：答辩/评审时评委只要开两个账号点两下就能看见，属于「一票否决」级别。

---

## 〇、先说结论（每一条都核过代码，不是猜）

| # | 个人数据 | 落盘位置 | 隔离现状 | 判定依据 |
|---|---|---|---|---|
| 1 | 周表（课程） | `student/<学号>/timetable.json` | ✅ 已隔离 | `_spath:780` + `_sread/_swrite`；实测 admin1 有 14 门、admin2 为空 |
| 2 | 待办 | `student/<学号>/todos.json` | ✅ 已隔离 | 同上；admin2 问「我的代办」实测回「0 条」 |
| 3 | 会话历史 | `student/<学号>/sessions.json` | ✅ 已隔离 | 同上；两份文件互不可见 |
| 4 | **课表问答（schedule 模块）** | **`app/data/courses.json`（写死在仓库里）** | ❌ **完全没隔离** | `schedule.py:34-35` 拼死路径 + `:93-96` 直接开文件 |
| 5 | 上传文件（含课表原件） | `data/uploads.json` + `data/uploads/*` | ❌ **全站共享** | `save_upload:526` 不记 owner；`list_uploads:574` 全量返回 |
| 6 | 待确认卡 / 暂存 | `data/pending.json` | ⚠️ **按 session_id 分，换账号串卡** | `pending.py:108`；`login.html` 不清 session |
| 7 | 快递台账 | `data/express.json` | ✅ 已隔离 | `store.visible_rows:105` 学生口径只留本人 |
| 8 | 外卖订单 | `data/takeout.json` | ✅ 已隔离 | 同上 |
| 9 | 工单 | `data/workorders.json` | ✅ 已隔离 | `add_workorder:231` 默认取 `current_student()` |
| 10 | 学生消息 | `data/student_messages.json` | ✅ 已有 owner | `add_message:178` |
| — | LLM 用量 | `data/llm_usage.jsonl` | ⚠️ 无学号字段 | 不算隐私泄露，只是审计口径（本单不改） |

**一句话**：地基（`_spath` 那条接缝）是好的 —— 周表/待办/历史三件确实分开了。
漏的是**没走那条接缝的地面**：第 4、5、6 三条。

---

## 一、45-A（最严重）：schedule 模块答的是仓库里那份「示例同学」的课表

### 现象

学生 2（`admin2`，周表为空）在 `/student` 问「今天又什么课」，实际答：

```
今天（周二）2 门课：
- 08:00~09:40 线性代数 · 孙教授 · 教三-202
- 10:00~11:40 体育 · 周老师 · 体育馆
```

这段历史确实存在 `app/data/student/admin2/sessions.json` 的 `se564fb57f1` 里 —— **学生 2 的库是空的，答案却有内容**。

### 根因（两处，缺一不可）

**① 数据源是静态文件，跟"谁在问"无关**

```python
# app/modules/schedule.py:34-35
_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "courses.json")

# app/modules/schedule.py:93-96
def _load() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)
```

`courses.json` 的头两行长这样：

```json
{ "student": "示例同学（演示数据）", "semester": "2026 春季学期", ... }
```

那两条答案就是它 `周二` 的两门课，**逐字对得上**。所以学生 2 拿到的不是学生 1 的课表，
是**写死在仓库里的公共演示课表** —— 对演示来说更糟：所有账号永远拿到同一份，而且学期还写着"春季"。

> 顺带解释用户为什么会以为是"学生1的"：admin1 的真实周表周二也是 `08:00-09:40 线性代数`、`10:05-11:45 体育`，
> **时间点撞上了、地点和老师不一样**（教二108/田径场 vs 教三-202/体育馆）。撞脸撞出来的误会，病根在别处。

**② 同名工具两个实现，后注册的把先注册的盖掉了**

```python
# app/main.py:1823-1834
tools = build_tools()                 # ← schedule.build_tools() 里有 "find_free_slots"(参数 day)
tools.update(files_tools())
if ... : tools.update(scheduling_tools())   # ← planner 的 "find_free_slots"(参数 date) 覆盖同名项
```

`planner.build_scheduling_tools():5985` 里那份 `find_free_slots(date, min_minutes)` 是**真数据**（走 `get_timetable()`），
`schedule.build_tools():207` 那份是**假数据**（走 `courses.json`）。

于是 **schedule 模块里同时有两套课表**：

| 学生问 | 命中工具 | 数据源 | 结果 |
|---|---|---|---|
| 「周二有什么课」 | `get_day_courses`（schedule 版） | `courses.json` | 假课表 |
| 「周二什么时候空」 | `find_free_slots`（被 planner 版覆盖） | `get_timetable()` | 真课表（但 admin2 是空的） |

更糟的是 `SCHEDULE_PROMPT:62` 教模型「先用 find_free_slots 查空档」，
模型按 schedule 的说明书传 `day="周三"`，实际执行的是 planner 版（要 `date`）→
回 `[{"error": "周三 不是合法日期，请用 2026-09-25 这种格式"}]` → 模型只能道歉。
**这就是"有时查不到"的一条独立病根，跟检索无关。**

### 修法

**目标：schedule 模块只有一个课表来源，就是 `store.get_timetable()`。**

```python
# app/modules/schedule.py 顶部
from app.store import get_timetable      # ← 按 cookie 分人的那份（唯一真源）

_DAY_INDEX = {"周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7}

def _courses_on(day: str) -> list[dict]:
    """某一天属于**当前这名学生**的课（人话：只问本人周表，不去翻演示文件）。"""
    wd = _DAY_INDEX.get(day)
    if not wd:
        return []
    out = [c for c in get_timetable() if int(c.get("day") or 0) == wd]
    out.sort(key=lambda c: str(c.get("start") or ""))
    return out
```

要点逐条：

1. **删掉 `_DATA_PATH` 与 `_load()`**。`courses.json` 从此只是测试种子（`tests/_e2e_env.py:demo_courses_from_json` 仍读它），
   不再是任何一条产品路径的数据源。留一行注释写清这件事。
2. `get_day_courses` / `find_free_slots` / `plan_task` 三个函数**全部改走 `_courses_on()`**，不许有第二个读法。
3. **输出不再带老师**：个人周表的字段只有 `{day,start,end,course,location}`，没有 `teacher`。
   统一成 `- {start}~{end} {course}（{location}）`，`location` 为空时省掉括号。
   ⚠️ 别为了"保持格式"去给 `timetable.json` 加 teacher 字段 —— 那是改数据结构，本单不碰。
4. **空课表要分两种话**（现在的 `"那天没有排课，整天都是空闲的"` 会把"你还没导入课表"说成"整天空闲"，是误导）：
   - `get_timetable()` 整体为空 → 「你还没导入课表，去日程面板点【上传课表】…」；
   - 有课表但那天没课 → 「周二没排课」。
5. **`find_free_slots` 必须去重**。首选：**删掉 `schedule.build_tools()` 里那个同名项**（`schedule.py:207-216`），
   让 planner 那份（真数据）单独存在；同时把 `SCHEDULE_PROMPT:62` 的措辞改成"查某天空档用 `find_free_slots`，
   入参是**日期**（如 2026-10-20）"。次选是改名 `find_free_day_slots`，但那就是留着两套课表，**不推荐**。
   ⚠️ 无论选哪条，`plan_task` 内部若调用了本模块的 `find_free_slots`，要跟着改成调用 `_courses_on()` 的本地算法，
   不要跨模块调 planner 的（会变成隐藏的第二条数据路）。

### 变异测试（必须造这个错才算钉住）

| 编号 | 变异 | 期望 |
|---|---|---|
| M1 | 把 `_courses_on` 改回 `_load()["courses"]` | `test_45` 红 |
| M2 | 给 admin2 的空周表问「周二有什么课」 | 必须**不含**「孙教授 / 教三-202 / 线性代数」，且含"还没导入课表" |
| M3 | 给 admin1 的真实周表问同一句 | 含「教二108」，**不含**「教三-202」 |
| M4 | 在 `sandbox()` 里把 `CAMPUSTIME_DATA_DIR` 指向临时目录 | 结果跟着变（证明没走源码旁那份） |
| M5 | `get_day_courses("周日")`（周表里没有周日） | 回"周日没排课"，不报错、不返回假课 |
| M6 | 断言 `schedule.build_tools()` 与 `planner.build_scheduling_tools()` **没有同名工具** | 集合交集为空 |

---

## 二、45-B：上传桶是全站共享的，学生能列出/读取别人传的文件

### 现状

```python
# app/store.py:526-571  save_upload(...) —— 落盘时**不记 owner**
item = {"id": fid, "name": filename, ..., "purpose": purpose}     # :557-567

# app/store.py:574-576
def list_uploads() -> list:
    return list(reversed(_read("uploads.json", {"files": []}).get("files", [])))   # ← 全量，不过滤
```

实测当前 `uploads.json` 里 35 条，其中 30 条是学生传的课表原件（`2026秋季课表-*.txt/.xlsx/.docx`），
**全部 `owner: null`**。而文件工具是挂在**每个模块**上的：

```python
# app/main.py:1823-1824 —— 任何学生端模块都能拿到这三个工具
tools = build_tools()
tools.update(files_tools())      # list_uploaded_files / read_uploaded_file / search_docs
```

⇒ 学生 2 只要说一句「我有哪些文件 / 读一下《2026秋季课表-纯文本.txt》」，
就能把学生 1 的课表原件**整篇读出来**。这条路比 45-A 更直接，也更容易被评委随手撞上。

> `#40 补` 只堵了"学生上传自动切块进检索"（`save_upload:554` 的 purpose 闸），
> **没堵"能列、能读"**。两件事，别只修一半。

### 修法

**口径先定死**（一句话）：**管理员上传的（`purpose=="kb"`，owner 为空）是公共知识，人人可读；
学生上传的（`purpose=="file"`）只有本人可读。**

1. `save_upload()` 增加 `owner: str | None = None`，默认 `current_student()`，写进 `item["owner"]`。
   ⚠️ **不接受调用方传任意值**（理由同 `add_workorder:238-242`：body 里谁都能改，前端传 owner = 可以冒充别人）。
   管理员那条上传路径（`/api/admin/docs` 的 POST）显式传 `owner=""`。
2. 新增**唯一一处**可见性判据（照 `visible_rows:105` 的样子来）：

```python
def can_read_upload(meta) -> bool:
    """当前请求方能不能读这份上传文件（人话：公共知识谁都能读，私人文件只有本人）。"""
    if not meta:
        return False
    o = str(meta.get("owner") or "")
    if o:                                  # 有归属 ⇒ 只有本人
        return o == (current_student() or DEMO_OWNER)
    return upload_purpose(meta) == "kb"    # 无归属 ⇒ 只有"公共知识文档"这一种放行
```

   ⚠️ 无归属且 `purpose != "kb"` 的老数据（就是现在那 30 份课表）**一律不放行**。
   这条不是"顺手清数据"，是默认安全：老文件本来就没有主人，谁读到都是错的。
   想留哪份，管理员手工补 owner 即可（**不写迁移脚本**，同 `status/archived` 那几单的做法）。
3. `list_uploaded_files()`（`files.py:24`）改成 `[f for f in list_uploads() if can_read_upload(f)]`；
   `read_uploaded_file()`（`:37`）与 `doc_hits()`（`:128`）**入口第一句就判** `can_read_upload(get_upload_meta(fid))`，
   不通过返回「找不到这份文件」——不返回"无权访问"，那是把"这里确实有东西"告诉了他。
4. `all_chunks()`（`store.py:656`）加同样的过滤（`purpose=="kb"` **且** `can_read_upload(f)`）。
   ⚠️ 这个函数是生成器、被 `doc_hits` 每问一次全量扫一遍，别在这儿引入 N 次 IO —— `list_uploads()` 一次读，循环里判内存对象。
5. 管理端「文档知识库」`list_docs()`（`store.py:586`）**保持不变**（管理员看全部 kb 文档，这是对的）。

### 变异测试

| 编号 | 变异 | 期望 |
|---|---|---|
| M7 | 去掉 `read_uploaded_file` 里的 `can_read_upload` | `test_45` 红 |
| M8 | 学生 2 调 `list_uploaded_files` | 返回里**没有** `2026秋季课表-*`（owner 为空且非 kb） |
| M9 | 学生 2 直接 `read_uploaded_file("<学生1传的 fid>")` | 回"找不到"，**不**回文件内容 |
| M10 | 学生 2 `search_docs` 搜「线性代数 教三301」 | 命不中别人传的课表块 |
| M11 | 管理员上传的 `学生手册-测试样本.txt`（purpose=kb, owner=""） | 两个学生、以及未登录，**都读得到**（别把公共知识一起锁死） |

---

## 三、45-C：暂存卡按 `session_id` 存，换账号会串

### 现状

```python
# app/agent/pending.py:108
_PENDING[session_id] = { ... }      # ← 键只有 session_id，文件是全站一份 pending.json
```

`session_id` 来自 `localStorage["campustime_session"]`（`student.html:1880-1885`），**只有点【退出登录】才清**
（`student.html:1951`）。而登录页 `login.html:113-126` **只写不删**：

```js
const r = await fetch("/api/login", { ... });
localStorage.setItem("campustime_student", d.student);   // :126 —— 没有 removeItem("campustime_session")
```

⇒ 学生 1 直接去登录页输学生 2 的学号（没有点退出）→ 同一个 `session_id` →
`pending_get` 把学生 1 那张**没点确认的待办卡 / 清空课表提案**原样发给学生 2；
学生 2 一点【确认加入】，写进的是**学生 2 的**库。既读到了别人的东西，又把别人的意愿落到自己账上。

### 修法（两处，都要）

1. **键上加身份**：`pending` 的内存键与落盘键统一成 `f"{current_student() or '-'}|{session_id}"`。
   ⚠️ 只在 `put/peek/take/clear` 这几个函数内部拼，**外部调用方一个字都不用改**（接缝只有一处）。
   ⚠️ 老 `pending.json` 的键没有前缀 → 读不到就是读不到，让它自然过期（`_PENDING` 有 TTL 吗？没有的话本单补一个按 `at` 的清理，
   别留一堆永远读不到的死键）。
2. **登录页换人时清 session**：`login.html` 在 `setItem("campustime_student", ...)` 旁边加
   `localStorage.removeItem("campustime_session")`，理由与 `student.html:1949-1951` 那条注释同源
   （"对话 id 是浏览器级的，留着就是把上一位的记忆递给下一位"）。
   ⚠️ 只清 session，**不清** `campustime_session` 之外的东西（`ct_bg` 那些是偏好，不该被换账号弄丢）。

### 变异测试

| 编号 | 变异 | 期望 |
|---|---|---|
| M12 | 学生1 出卡未确认 → 学生2 用同一 session_id 查 `pending_get` | 返回 `{"pending": null}` |
| M13 | 学生1 出卡未确认 → 学生1 自己再查 | 卡还在（别为了隔离把正常路也断了） |
| M14 | `login.html` 少写 `removeItem` 这行 | 静态断言红（grep `campustime_session` 在该文件里出现 `removeItem`） |

---

## 四、45-D：`_tt_file_courses` 按文件名在**全局桶**里找文件

```python
# app/main.py:882-890
if not name:
    return None
fid = None
for f in (list_uploads() or []):        # ← 全站，不判归属
    if (f.get("name") or "").strip() == name:
        fid = f.get("id")
        break
```

同一份课表被传过 8 次（`2026秋季课表-纯文本.txt` 在表里有 8 条），同名多份时**取到的是最新的那条**，
不一定是本人的。45-B 的 `can_read_upload` 落下去之后，这里顺手改成：

```python
for f in (list_uploads() or []):
    if can_read_upload(f) and (f.get("name") or "").strip() == name:
        ...
```

---

## 五、明确**不做**的（别顺手扩大）

- 不做 `uploads` 目录按学号分层（`uploads/<学号>/<fid>.txt`）—— 那是把已有 35 份文件全搬一遍，
  收益只是"物理上分开"，判据仍在 `can_read_upload` 一处。**留到冻结后**。
- 不给 `timetable.json` 加 `teacher` 字段。
- 不写数据迁移脚本（老 uploads 记录保持 `owner: null`，靠 `can_read_upload` 的"默认不放行"兜住）。
- 不动 `llm_usage.jsonl` 的口径。
- 不动 `student/` 根目录那份公共库（无 cookie 直接开 `/student` 的兜底路，`test_59` B 批钉着）。

---

## 六、真机复验表（交付前必须逐条点一遍，不许只看测试绿）

| # | 操作 | 期望 |
|---|---|---|
| 1 | 用 `admin1` 登录，问「今天有什么课」 | 答的是**你自己**导入的那份（教二108 / 田径场），**没有**「孙教授 / 教三-202」 |
| 2 | 退出，用 `admin2` 登录，问同一句 | 答「你还没导入课表」，**一个课程名都不出现** |
| 3 | `admin2` 问「我有哪些文件」 | 列表里**没有**任何 `2026秋季课表-*` |
| 4 | `admin2` 问「图书馆在哪里」 | 仍然答得出来（管理员传的公共知识文档要照旧可用） |
| 5 | 不点退出，直接从登录页把账号换成另一个 | 聊天区顶部灰条/历史**不出现**上一位的对话，也不出现上一位没点确认的卡 |
| 6 | `admin1` 问「我这周什么时候有空」 | 空档按**本人**周表算（不是 `courses.json` 的春季课表） |
| 7 | 未登录直接开 `/student` | 页面仍能进（公共库兜底没被本单弄坏） |

⚠️ 第 6 条是 45-A② 的验法：如果 `find_free_slots` 那份重复没清掉，模型可能回
「周三 不是合法日期，请用 2026-09-25 这种格式」—— **看到这句就是没修完**。

---

## 七、给执行方的三条纪律

1. **整族扫**：本单的病根是"没走 `_spath` / 没有 owner 判据"，改完 grep 一遍
   `open(` 与 `list_uploads`，确认没有第二处绕过判据的读法。
2. **注释里的例子必须实测过**：上面每条"实测"都是我在这台机器上跑出来的真实输出，
   你改完若与实测不符，先把不符的贴回来，别按注释去"凑"。
3. **提交拆分**：45-A / 45-B / 45-C 三笔分开提交（每笔对应一个病根），
   别把假课表、owner 闸、pending 键混成一个 `fix: 隔离` —— 评审看的是"每一笔对应一个问题"。

---

## 八、as-built 更正（2026-10-20 执行方落地后补记，**更正了 §二 与 §六 的两条**）

> 本节是**追加的**，上面 §一~§七 原文一字未改（原件留档的规矩）。
> 若两者冲突，**以本节为准**，并把"为什么原文是错的"一起看。

### 8.1 §二 的判据被推翻两处（推翻成立，我认）

| 原文写法 | 实测后果 | 改成 | 依据 |
|---|---|---|---|
| 无归属 ⇒ 只有 `purpose=="kb"` 放行 | `notices.json` 里唯一那份附件（`数据结构实验报告样例.docx`）`purpose/owner` 皆空 ⇒ **通知附件点下载变 404**，把已上线功能当 collateral damage 删了 | 再放行一条：**被通知引用的那份可读**（复用现成的 `upload_notice_refs`，不加字段） | `test_72` B2 / B14 |
| 无归属非 kb **一律不放行** | 未登录演示那一路（#35 空状态引导、`test_59` B 批）与一批测试一起红 —— 那不等于"隔离做严了"，只是把没登录的人关在自己刚传的文件外面 | 按 `current_owner()` 既有约定：**只有 demo 口径可读**（`current_owner() == DEMO_OWNER`） | `test_72` B9 / B10 |

**这两条是我施工单的判据漏洞，不是执行方擅自放宽。** 教训记一句：
我写「一律不放行」时只推了"学生之间"，没推"**同一条判据还管着谁**"（通知附件、未登录演示）。
下一单起，凡写 `return False` 的兜底，必须同时列"谁会被这一句误伤"。

### 8.2 §六 真机表漏了三处**免口令路由**（我的漏项，执行方整族扫才查出来）

`§七-1` 我只写了 grep `open(` 与 `list_uploads`，**没写 `get_upload`**。漏掉的正是最直白的三条：

- `GET /api/uploads` —— 以前 `list_uploads()` 全量倒出（评委敲地址栏就拿到 30 份文件名）
- `GET /api/uploads/{fid}` —— 返回提取全文
- `GET /api/uploads/{fid}/download` —— 返回原件

⇒ **"整族扫"必须给到具体 grep 词**，写"扫一遍"等于没写。本族完整词表：
`list_uploads` / `get_upload` / `get_upload_meta` / `get_upload_raw_path` / `all_chunks` / `visible_uploads`。

### 8.3 `public_kb` 那条"两个条件"的正确读法（执行方说法容易被误读成读时判两个）

实际实现是**写时固化**，读时只看一个字段 —— 这是更好的做法，别改回去：

```
/api/upload：owner = "" if (purpose=="kb" and public_kb=="1") else None
             ↓ save_upload 里 owner is None ⇒ owner = current_student()
落盘：公共知识 ⇒ owner="" ；其余 ⇒ owner=<学号>（含""，未登录时就是空串）
读时：can_read_upload 只看 owner 与 purpose，不再看 public_kb
```

⇒ "公共"这个词在库里就是 **`owner==""` 且 `purpose=="kb"`**，不是第二个布尔字段。
**判据里三个条件的顺序不许重排**：`who` 必须先判，否则"管理员在学生浏览器里传的 kb"会因为
`purpose=="kb"` 被当公共（`test_72` B5-bis 就是钉这个）。

### 8.4 本轮账（我独立复跑过，不是转述）

`tests/test_72_data_isolation.py` 用项目 `.venv` 跑：**A 批 14/0、B 批 16/0，退出码 0**。
⚠️ 系统 `python`（3.14）没有 fastapi，直接 `python test_72...` 会在 `make_client` 处
`ModuleNotFoundError`，**前面的 check 已经打完成功**，看着像"跑到一半挂了"，其实是解释器选错。
→ 全量回归一律 `cd tests && ../.venv/Scripts/python.exe run_all.py`。

### 8.5 还欠的（别当已交）

- **45-C 未开工**（`pending` 键 + `login.html` 清 session）→ 见 #47
- **mutate45 的 M1~M6** 未做 → 见 #47 §二
- **真机七条**未做（须带 Key）→ 见 #47 §三
- 全量回归 + 严格克隆、45/46 的快照与文档 → 见 #47 §四
