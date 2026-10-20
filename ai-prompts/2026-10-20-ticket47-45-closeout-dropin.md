# 47｜45 收尾：暂存卡按人分 + mutate45 + 真机七条（drop-in）

> 来源：2026-10-20 执行方回报两处偏离与账，用户定「下一轮从 45-C + mutate45 + 真机那七条开始，#46 单独一轮」。
> 前置：#45 已落 45-A / 45-B / 45-D（`21a5370` 之后两笔），`test_72` A 14 / B 16 全绿（我已用 `.venv` 独立复跑确认）。
> 交付性质：给执行方（Qoder）的落地施工单。**我不改代码。**

---

## 〇、范围

**本单做四件**：
- 47-A　**45-C**（唯一还没动的代码）：暂存卡键加身份 + 登录页清会话；
- 47-B　**mutate45**：M1–M6 补跑，新增 M15–M18（45-C 的变异）；
- 47-C　**真机七条**（拆成 3 条可自动 + 4 条必须带 Key）；
- 47-D　全量回归 + 严格克隆 + 快照/文档落账。

**本单不做**（别扩）：
- #46 全部（关键词派生 / 面板 catch / 试检索探针）—— 单独一轮；
- `kb.json` 恢复与否 —— **仍等用户拍板**，别动；
- `public_kb` 收紧 —— 进 §五 已知缺口，用户点头再单独一刀。

---

## 一、47-A：45-C —— 暂存卡按人分

### 1.1 病根（已复核，行号准）

```python
# app/agent/pending.py:108
_PENDING[session_id] = {"options": list(options), "at": time.time(), "applied": False}
```

- 键**只有** `session_id`；`_load()` 从 `pending.json` 整份读回，键同样是裸 session_id；
- `session_id` 来自 `localStorage["campustime_session"]`（`student.html:1880-1885`），
  **只有点【退出登录】才清**（`student.html:1951`）；
- `login.html` **全文没有 `campustime_session` 字样** ⇒ 直接换学号登录时它原样留着。

⇒ 学生 1 出了张待办卡没确认 → 学生 2 直接登录 → `GET /api/pending/{sid}` 把那张卡发给他 →
他点【确认加入】，`/api/todos` 写进**学生 2** 的库。既看到了别人的东西，又替别人做了决定。

### 1.2 接缝：只在 `pending.py` 加一个私有函数，**对外签名一个字不改**

```python
def _key(session_id: str) -> str:
    """暂存的真正键：`<当前口径>|<session_id>`（#45-C）。

    ⚠️ 为什么必须拼身份：session_id 是**浏览器级**的，换账号不清它（除了点退出）。
    学生 1 没点确认就换人，那张卡会原样发给学生 2 —— 而学生 2 一点确认，
    写进去的是**他的**库。看到的和写进去的两头都错。

    ⚠️ 口径只取 `store.current_owner()`（没身份 = demo），**不在这里另读 cookie**：
    身份只有一个来源（`main.bind_student` 那个中间件设的 ContextVar），
    两处来源就会打架，打架的结果永远是"谁读都算数"。
    """
    if not session_id:
        return ""
    from app.store import current_owner      # 函数内导入：pending 有可能比 store 先被加载
    return f"{current_owner()}|{session_id}"
```

**六个调用点全部换成 `_key(...)`**（一个都不许漏，漏一个就是"改了一半"）：

| 行 | 现在 | 改成 |
|---|---|---|
| `save_pending:106` | `if not session_id` | `k = _key(session_id)`；`if not k: return` |
| `save_pending:108` | `_PENDING[session_id] =` | `_PENDING[k] =` |
| `peek_pending:119` | `_pending_entry(session_id)` | `_pending_entry(_key(session_id))` |
| `take_pending:125-127` | `_pending_entry(session_id)` / `.pop(session_id)` | 两个都换 |
| `mark_applied:139` | `_PENDING.get(session_id)` | `_PENDING.get(_key(session_id))` |
| `clear_pending:154` | `_PENDING.pop(session_id, None)` | `_PENDING.pop(_key(session_id), None)` |
| `_pending_entry:158-166` | 收裸 id、内部再查 | **改成收已拼好的键**（`_key` 只在上面五个出口调一次） |

⚠️ `_pending_entry` 里 `_PENDING.pop` 与 `_save()` 也要跟着用同一个键 —— 这是最容易被漏的一处
（它自己又 pop 一次）。

### 1.3 两个后果，必须在提交信息里写实

1. **老 `pending.json` 的裸键从此读不到**（3 小时 TTL 自然过期）。
   这是**要的**：那些键没记是谁的，留着就是留一颗地雷。⚠️ **不要加清理逻辑**去删它们 ——
   多一段就多一处要维护，TTL 已经处理了。
2. 学生登录后看不到"未登录时留下的卡"。这是正确的（他换了身份）。
   ⚠️ 反过来：cookie 掉了（浏览器清缓存）会看不到自己刚出的卡 —— 可接受，页面刷新会重新拿 cookie。

### 1.4 `login.html`：换人时清会话

```js
// app/static/login.html:126 附近
localStorage.setItem("campustime_student", d.student);   // 只是显示用；后端认 cookie
// ⚠️ #45-C：对话 id 是**浏览器级**的，留着就是把上一位没点确认的卡递给下一位。
//    与 doLogout（student.html:1949-1951）同一条理由，两边都要有。
localStorage.removeItem("campustime_session");
```

⚠️ 只清这一个键。`ct_bg / ct_profile_name / ct_model_cfg` 那些是**偏好**，换账号不该被弄丢。

### 1.5 护栏（`test_72` 加 C 批，或新 `test_73`；两批都要能单独跑）

| # | 断言 | 为什么 |
|---|---|---|
| C1 | 学生1 `save_pending("s1")` → 学生2 `peek_pending("s1")` = None | 主判据 |
| C2 | 学生1 自己 `peek_pending("s1")` 仍有 | 对照：别把正常路也砍了 |
| C3 | 未登录（demo）save → 学生1 peek = None；demo 自己 peek 仍有 | demo 口径自洽、且学生看不到 demo 的 |
| C4 | `GET /api/pending/{sid}`：admin1 与 admin2 两个 cookie 各取一次，**一个 null 一个有** | 接口层真验，不止内存 |
| C5 | `login.html` 里有 `removeItem` + `campustime_session` | 静态断言（防下一个人"整理注释"时删掉） |
| C6 | 落盘形状：`pending.json` 的键**全部**含 `|` | 防"只改内存没改盘"（`_save` 落的是 `_PENDING`，理论上一处，但要钉住） |

⚠️ C4 的坑：如果环境配了 `CAMPUSTIME_COOKIE_SECRET`，cookie 是**签名**的（`学号.hmac`），
手写 `Cookie: ct_student=admin1` 会被 `_clean_student` 判成非法 → 走 demo 口径 → 两条都一样，**假绿**。
正确姿势见 §三 的 `curl -c/-b` 走 `/api/login`。

### 1.6 变异（并进 §二 的 mutate45）

| 编号 | 怎么改 | 预期 |
|---|---|---|
| M15 | `_key` 直接 `return session_id` | C1 / C4 红 |
| M16 | **只** `save_pending` 用 `_key`，peek/take 不用 | C1 红（这条最像"改了一半"，M15 抓不到） |
| M17 | 删掉 `login.html` 那行 | C5 红 |
| M18 | `_key` 用 `current_student()` 而不是 `current_owner()` | C3 红（未登录时键变成 `|s1` 那种，demo 自己也读不到） |

---

## 二、47-B：mutate45 —— M1~M6 落点 + 规矩

### 2.1 M1~M6 的准确落点（别只写"红了"，要写"红的是不是预期那条"）

| 编号 | 改哪一行 | 预期红（按 `test_72` 现有编号） | 在验什么 |
|---|---|---|---|
| M1 | `schedule._courses_on()` 里的 `get_timetable()` → 读回 `app/data/courses.json` | **A1 / A1-bis / A2**（静态先拦），再 A6 / A7 | 数据源真换掉了，不是"另加了一个分支" |
| M2 | 把 `_DAY_INDEX["周二"]` 改成 3 | A6 / A9 | 星期映射只有一处 |
| M3 | `can_read_upload` 删掉 `if who: return who == current_owner()` 那一段 | B1 / B3 / B4 / B5-bis | 有归属的只认本人 |
| M4 | `_courses_on()` 里写死 `os.path.join(os.path.dirname(__file__), "..", "data")` | A13（+ A1 因为又 open 了文件） | 数据跟着沙箱走 |
| M5 | `get_day_courses` 去掉"整份周表为空"那个分支 | A11 / A12 | "没导入课表"≠"那天没课" |
| M6 | 把 schedule 的 `find_free_slots` 加回 `build_tools()` | A3 / A4 | 同名工具互盖 |

⚠️ A1 / A1-bis 是**静态 AST/文本断言**（"`schedule.py` 里不再打开任何文件"），
所以 M1、M4 会**先被静态那条拦住**才轮到时序断言 —— 这不是重复，是"拦住得更早"。
**读变异结果时要分清"红的是静态那条还是行为那条"**，两条都红才算真的钉住了。

### 2.2 规矩

1. **`mutate45.py` 不入库**（与 `mutate_L.py` 同一约定），放仓库外或临时目录，跑完删。
2. 每条变异**一次只改一处**，跑 `test_72` + 该变异相关的那一个邻居测试，记录：
   `M编号 / 改了哪一行 / 红了哪几条 / 是否与预期完全一致 / 不一致的原因`。
3. 每次变异后 `git checkout -- <file>`，**全部跑完先 `git status` 确认干净再提交**。
4. 结果写进 `开发者接手指南.md` 的 #45 段（照 #60 "变异 M1~M8" 的写法），**不写进测试文件注释**。

---

## 三、47-C：真机七条（拆成"能自动"与"必须模型"）

### 3.1 先解决"要带 Key"这件事

- Key 来源：`app/data/settings.json`（**已 gitignore**）或 `.env` 的 `DEEPSEEK_API_KEY`。
- ⚠️ **红线**：Key 不许出现在任何测试、脚本、快照、提交、截图里。
  探针脚本放仓库外（如 `D:\_probe45\`），跑完删；截屏前把 Key 那行糊掉。
- 摘掉 Key 时能测什么、不能测什么，**必须写进这次快照**：
  - 能测：接口层（`/api/timetable`、`/api/uploads`、`/api/pending`、工具函数直接调）；
  - **不能测**：schedule 模块那句回答（无 Key 只会回"答不上来"，实测如此）——
    这正是 45-A 的护栏必须打在**函数与接线**上、而不是只做端到端的原因。

### 3.2 三条可自动（curl，`-c/-b` 走登录接口，别手写 cookie）

```bash
cd /d/CampusTime
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &

# 拿两个学生的真 cookie（配了 SECRET 也是签名过的，手写会假绿）
curl -s -c /tmp/a1.txt -X POST http://127.0.0.1:8000/api/login \
     -H "Content-Type: application/json" -d '{"student":"admin1"}'
curl -s -c /tmp/a2.txt -X POST http://127.0.0.1:8000/api/login \
     -H "Content-Type: application/json" -d '{"student":"admin2"}'
```

| # | 命令 | 期望 |
|---|---|---|
| ③ | `curl -s -b /tmp/a2.txt http://127.0.0.1:8000/api/uploads` | 返回里**没有** `2026秋季课表`（有就说明 45-B 老数据还敞着） |
| ⑦ | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/student`（**不带 cookie**） | `200`（公共库兜底没被弄坏） |

⚠️ 原表第 ⑤ 条（换账号看不到上一位的卡）**从 curl 挪进 `test_72` C 批（C4）**，不在这里做。
理由很实在：造一张待确认卡要走 `/api/chat`，而那条路在**没有 Key** 时根本到不了出卡那一步
（卡是模型调 `propose_*` 工具才挂出来的；没 Key 就没有工具调用 —— 实测 schedule 那条路只会回"答不上来"）
—— 写成 curl 就是一条"没 Key 时必然假绿"的检查。
C4 用 `TestClient` + 直接 `save_pending()` 造卡，**不依赖模型**，验的还是同一个 HTTP 出口
（`GET /api/pending/{sid}`）在两个 cookie 下的分化。真机那种"人眼看到卡消失"的体感，
放到录屏里顺手做一次即可，不进验收表。

### 3.3 四条必须带 Key、必须手点（浏览器，录屏时顺手做完）

记一行就是一行，**别写"测试绿了"当证据** —— 这四条验的是"模型会不会按说明书调工具"。

| # | 问句 | 必须含 | 必须不含 |
|---|---|---|---|
| ① | 「今天有什么课」（admin1） | 本人周表里的课名**和地点**（如 `教二108`） | `孙教授` / `教三-202` |
| ② | 同一句（admin2，周表空） | 「还没导入课表」+ 去哪儿导入 | **任何课程名**（一个都不许出现） |
| ④ | 「图书馆在哪里」（admin2） | 中轴北侧 / 六层（来自《学生手册》） | 「知识库里没有」 |
| ⑥ | 「我这周什么时候有空」（admin1） | 按**本人**周表算出的空档 | **`不是合法日期`** ← 出现就是 M6 那条同名工具没清干净 |

⚠️ ⑥ 是 45-A② 的唯一真机验法。若这次仍出现那句话，**先回去查 `schedule.build_tools()`
里 `find_free_slots` 是否真的下架了**，不要用"模型偶尔抽风"解释过去。

---

## 四、47-D：账与提交

1. **提交拆分**：
   - `fix(45-C): 暂存卡按人分 + 换账号清会话`（`pending.py` + `login.html` + C 批护栏）**一笔**；
   - 变异**不产生提交**（脚本不入库），结果写指南；
   - 真机七条**不产生提交**，结果写快照。
2. **全量回归**：`cd tests && ../.venv/Scripts/python.exe run_all.py`。
   ⚠️ 别用系统 `python`（3.14 没 fastapi，会在 `make_client` 处 `ModuleNotFoundError`，
   而它前面的 check 已经打完 —— 看着像"跑到一半挂了"，其实是解释器选错）。
3. **严格克隆**：`git clone` 到临时目录（不要 `cp -r`，要把 `.gitignore` 里的东西天然排除）→
   `pip install -r requirements.txt` → 跑 `test_72` + 至少 `test_02/03/23/29/31/45/66/67`。
   这一步验的是"评委 clone 下来能不能跑"，比本机全绿更硬。
4. **快照**：`ai-prompts/2026-10-20-ticket45-...-drop-in.md` 原文照入 + `ai-prompts/2026-10-20-ticket47-...-drop-in.md`，
   `cp -p` + md5 核对；对话侧只放决定轮次节选。
5. **工作树**：提交前确认那三个未跟踪项的去留 ——
   `demo_timetable/`（测试种子？该进仓库还是删）、`run_local.bat`（**这个该入库**，README 指向它）、
   `tests/_optional_browser/`（可选依赖，该 gitignore）。**逐条判断，别 `add -A`。**

---

## 五、本轮新记的已知缺口（写进 README §九，别只在对话里说）

1. **`public_kb=1` 是客户端字段，`/api/upload` 免口令** ⇒ 任何学生可以把自己的文件标成"公共知识"
   推进检索面。**与 #45 之前同宽（不是新增洞）**，但既然有了这个字段，收紧只要 10 行：
   - `admin_guard.py` 加一个**不抛异常**的 `is_admin()`（`get_admin_token()` 有值且 `compare_digest` 通过）；
   - `/api/upload` 里 `owner = "" if (purpose=="kb" and public_kb=="1" and is_admin(...)) else None`；
   - `admin.html:829` 那句 `fetch("/api/upload", ...)` **必须改走 `adminFetch`**（它才带 `X-Admin-Token`），
     否则管理端上传会静默变成私有文档 —— 那正是 `docStatus` 那句"没进知识库"要防的坑。
   ⚠️ 冻结前不做就**写进 README**，别让它只活在注释里。
2. **未登录 = demo 口径 ⇒ 能读无主老文件**（`test_72` B9 钉着）。
   设计如此，但要在 README 写实"**这不是鉴权**"；并且**演示前把那 25 份无主老课表清掉**
   （`uploads.json` 里 `purpose=None / owner=None` 的那些）——
   否则评委不登录敲一下 `/api/uploads`，屏幕上就是一整屏「2026秋季课表」。
   ⚠️ 清之前确认 `notices.json` 引用的那份附件**不在**删除名单里。
3. **`can_read_upload` 里三个条件的顺序不许重排**：`who` 必须最先判。
   下一个人"整理一下"把 `purpose=="kb"` 提到前面，B5-bis 立刻红 —— 这条要写进函数注释里（已有，别删）。

---

## 六、文档跟账：README §九 有一段已经**把做完的事说成没做**

> 2026-10-20 17:59 用户拍板「kb.json 保持空」后追加的一节。
> 这一节只动 `README.md` / `app/static/admin.html` 的文案，**不动业务代码**。

### 6.1 必须先改的：README §九 第一条（`README.md:528-530`）

现在是这么写的：

```
- **演示级的身份与归属**：`uploads` 是一个共享桶 —— AI 那件「读上传的文件」工具能读到任何人传的文件；
  管理端那张「文档知识库」只收知识文档（`purpose=kb`，#40 补），但这个标记目前由上传方自己带。
```

**前半句在 45-B 之后已经不成立**（学生读不到别人的了）。留着它有两个坏处：
① 评审读到"共享桶、谁都能读"会直接判隐私没做；
② 下一轮谁看见这句，可能真的去"修"一个已经修好的东西。

换成（可直接替换这三行）：

```
- **身份是"按学号分目录"，不是鉴权**：`app/data/student/<学号>/` 让课表 / 待办 / 对话历史按人分开；
  上传桶也按人分了（#45：管理员传的知识文档人人可读，学生传的只有本人可读，
  三条免口令的 `/api/uploads*` 口与检索面都走**同一处**判据）。但**身份本身不是鉴权** ——
  学号是登录页自己填的、cookie 在浏览器里能改，没配 `CAMPUSTIME_COOKIE_SECRET` 时签名退回"只写学号"。
  另外**未登录 = 演示公共库**（没有身份时按 `demo` 口径读"无主"老文件）：
  这是为了让"不登录直接开 `/student`"这条复现路径仍然可用，**不是演示账号有特权**。
- `purpose`（这份上传是"材料"还是"进知识库"）仍是**上传方自己带**的表单字段，`public_kb=1` 同理 ——
  服务端目前没有校验"你是不是管理员"。收紧只需在 `/api/upload` 加一次可选的口令校验
  （`admin_guard.is_admin()`，不抛异常那种），本版按"一套上传口 + 边界写清楚"取舍。
```

⚠️ 别把 `#45` 写成"已实现多用户鉴权"——它不是。诚实写"分目录 ≠ 鉴权"比写成"隔离已做"更抗问。

### 6.2 再加一条：知识库条目表刻意留空（用户 2026-10-20 拍板）

紧跟在"长资料的检索还是词法的"那条后面加：

```
- **演示版问答条目表刻意留空**（`app/data/kb.json` 是 `{"entries": []}`）：
  知识库由管理端上传的整份文档构成（《学生手册》等），学生问「图书馆在哪」的答案是
  **文档检索**给的，不是一问一条。条目表那条路仍然可用（管理端「知识库」面板能加能删、
  加了立刻生效、**不需要切块**），只是这次演示不用它。
  这么定是因为仓库里那 11 条演示条目与《学生手册》的闭馆时间互相冲突
  （8:00–22:00 vs 七点半至二十二点半），两套同时进检索会让模型把两种都讲出来。
```

⚠️ 必须点出"不需要切块"，否则评委看两个面板（「知识库」/「文档知识库」）会以为前者是后者没做完的版本。

### 6.3 这两处改完要跟的那笔提交

`docs(README): 局限里两条已经说不实 —— 隔离按人分已落地；问答条目表刻意留空`

⚠️ **只改文档**，不要顺手改代码；与 47-A 的代码提交分开。
