"""#45 的护栏：学生个人数据必须**按人**分（P0，评委开两个账号点两下就能撞见）。

用户 2026-10-20 实测：「学生2 没上传课表也能问出今日课表，读的是学生1的」，并要「顺便看看
学生的个人数据有没有全部隔离」。核完是三个病根，本文件按批钉：

A（45-A）`app/modules/schedule.py` 以前读的是 `app/data/courses.json` —— 一份写死
  「示例同学（演示数据） / 2026 春季学期」的**公共演示课表**，跟"谁在问"无关；
  admin2 的周表是空的，却被答出该文件周二那两行（孙教授 / 教三-202），
  这条回答现在还存在 `student/admin2/sessions.json` 里。
  另一处：schedule 与 planner 各注册了一个 `find_free_slots`（入参 `day` vs `date`），
  `main.py` 里 planner 那份覆盖前者 ⇒ 提示教模型传"周三"、执行要日期 ⇒「不是合法日期」。
B（45-B + 45-D）上传桶**全站共享**：`uploads.json` 实测 35 条、**0 条有 owner**，
  其中 25 条名字含"课表"（同名最多 14 份）。任何学生说一句「我有哪些文件 / 读一下那份课表」
  就能把别人的课表原件整篇读出来。⚠️ 施工单只列了工具那三处，整族扫还查出**三条未鉴权路由**
  （`/api/uploads`、`/api/uploads/{fid}`、`/api/uploads/{fid}/download`）——那是开个 URL 就能看全桶。
C（45-C）暂存卡的键**只有 session_id**，而 `login.html` 换人时不清 `campustime_session`
  ⇒ 不点退出直接换学号，上一位没确认的待办卡/清空课表提案会原样发给下一位，他点确认还写进自己库。

⚠️ 全程**不依赖大模型**：无 Key 时 schedule 模块那条路只会回一句"答不上来"（实测），
所以课表这类判据必须打在**函数与接线**上，光看端到端会测不到东西。
"""
import ast
import json
import os

from _harness import (PROJ_DIR, Checker, data_path, make_client, sandbox, seed_todos,
                      title, wipe_todos, write_timetable)

code = 0

# 沙箱里种的个人周表：故意与 courses.json **不一样**（地点/课名都换），
# 这样"答案跟着沙箱走"才是证据，而不是两份数据碰巧相同。
TT_ADMIN = [
    {"day": 1, "start": "08:00", "end": "09:40", "course": "工程图学", "location": "教一-101"},
    {"day": 2, "start": "08:00", "end": "09:40", "course": "结构力学", "location": "教五-01"},
    {"day": 2, "start": "14:00", "end": "15:40", "course": "毕业实习动员", "location": ""},
    {"day": 3, "start": "10:00", "end": "11:40", "course": "流体力学", "location": "教五-03"},
]
FAKE_MARKS = ("孙教授", "教三-202", "周老师", "体育馆", "示例同学")


def _strip(src: str) -> str:
    """剥掉注释再判"有没有 open(" —— 我自己写的注释里正好在解释旧写法（红线 4c 那一族）。"""
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(ln.split("#")[0])
    return "\n".join(out)


def _src(rel: str) -> str:
    return open(os.path.join(PROJ_DIR, rel), encoding="utf-8").read()


def batch_a_schedule_source():
    c = Checker()
    title("A. 45-A：schedule 只有一个课表来源 = 本人周表（`store.get_timetable()`）")
    from app.modules import schedule as sched
    from app.modules.planner import build_scheduling_tools

    src = _src("app/modules/schedule.py")
    bare = _strip(src)
    # 三条**分开钉**、都按"剔掉注释"的 `bare` 判 —— 合成一条时详情只会说"红了"，
    # 看不出是哪一半红的；而按整份文件判更糟：我自己写在注释里的那句"以前读 courses.json"
    # 会把它自己绊红（红线 4c 第七次应验）。
    c.check("A1 schedule.py 里不再打开任何文件（旧写法是 `open(_DATA_PATH)` 读演示课表）",
            "open(" not in bare, f"剔注释后 open( 出现 {bare.count('open(')} 次")
    c.check("A1-bis 那份写死「示例同学 / 2026 春季学期」的文件不再被这个模块引用",
            "courses.json" not in bare and "_DATA_PATH" not in bare,
            f"courses.json {bare.count('courses.json')} 次 / _DATA_PATH {bare.count('_DATA_PATH')} 次")
    c.check("A2 课表只从 store 拿（`get_timetable` 是唯一入口）",
            "from app.store import get_timetable" in src and "_courses_on" in src)
    c.check("A3 `find_free_slots` 只在 planner 那一侧注册（schedule 那份同名工具已下架）",
            "find_free_slots" not in sched.build_tools() and "find_free_slots" in build_scheduling_tools())
    inter = set(sched.build_tools()) & set(build_scheduling_tools())
    c.check("A4 两边共用的那几个工具必须是**同一个函数**（同名不同实现才是病，同名同实现是复用）",
            all(sched.build_tools()[n].func is build_scheduling_tools()[n].func for n in inter),
            f"共用 {sorted(inter)}")
    c.check("A5 提示词里点名 `find_free_slots` 收的是**日期**（旧话术教模型传星期，实打实换来一句『不是合法日期』）",
            "2026-10-20" in sched.SYSTEM_PROMPT and "不是星期" in sched.SYSTEM_PROMPT)

    with sandbox():
        write_timetable(TT_ADMIN)
        s1 = sched.get_day_courses("周二")
        c.check("A6 周二答的是**本人**那两门（教五-01 在），演示课表那五个标记一个都不在",
                "教五-01" in s1 and "结构力学" in s1 and not any(w in s1 for w in FAKE_MARKS),
                s1.replace("\n", " ⏎ ")[:120])
        c.check("A7 输出不带老师名（个人周表没这个字段；不许为了旧格式去改数据结构）",
                "老师" not in s1 and "教授" not in s1, s1[:80])
        c.check("A8 地点为空时不摆空括号（第二门课没写地点）",
                "（）" not in s1 and "毕业实习动员" in s1, s1.replace("\n", " ⏎ ")[:120])
        sun = sched.get_day_courses("周日")
        c.check("A9 有周表但那天没课 → 「周日没排课」，不说『整天都是空闲的』（那是把没课说成你很闲）",
                "没排课" in sun and "空闲" not in sun, sun[:90])
        pt = sched.plan_task("复习高数", 3)
        c.check("A10 plan_task 也只读本人周表：周一 08:00-09:40 有课 ⇒ 计划从 09:40 起，不压在课上",
                "《复习高数》" in pt and "周一 09:40" in pt and "08:00~" not in pt,
                pt.replace("\n", " ⏎ ")[:130])

        write_timetable([])
        empty = sched.get_day_courses("周二")
        c.check("A11 空周表 → 明说『还没导入课表 + 去哪儿导入』，一个课程名都不出现",
                "周表还是空的" in empty and "上传课表" in empty
                and not any(w in empty for w in FAKE_MARKS + ("结构力学", "线性代数")),
                empty[:110])
        pt_empty = sched.plan_task("复习高数", 3)
        c.check("A12 空周表时 plan_task 不许兴高采烈排出一周计划（那个「空」是没导入，不是空闲）",
                pt_empty.startswith("你的周表还是空的") and "做《" not in pt_empty,
                pt_empty[:110])

        # M4（施工单）：换数据目录 ⇒ 结果必须跟着变。走到这里已经变过一次
        #（ courses.json 是 14 门春季课表、沙箱 admin 是 3 门），这里再把它钉成一条：
        write_timetable([{"day": 5, "start": "09:00", "end": "10:40",
                          "course": "人工智能导论", "location": "教六-02"}])
        fri, tue = sched.get_day_courses("周五"), sched.get_day_courses("周二")
        c.check("A13 数据跟着**沙箱目录**走（不是源码旁那份文件）：换种子之后周五有课、周二没有",
                "人工智能导论" in fri and "没排课" in tue, f"{fri[:40]} | {tue[:30]}")
    return c


def _as_student(sid):
    """在"这个人正在请求"的语境下调函数。

    为什么要自己拼：`current_student()` 是中间件按 cookie 设的 ContextVar，而 store 只给了 getter。
    无 Key 时工具那一路（`list_uploaded_files` / `read_uploaded_file`）不会被 `/api/chat` 调到
    （模型根本没跑），所以**只能直接调函数**测它们 —— 那就必须把身份摆进去，
    否则测的是"没登录的 demo 口径"，跟学生之间的隔离根本不是一个命题。
    ⚠️ 用完必须复原成空串：ContextVar 是进程级的，漏出去会让后面那批测试以为一直有人登录着。
    """
    from app.store import _CURRENT_STUDENT

    class _Ctx:
        def __enter__(self):
            self._prev = _CURRENT_STUDENT.get()
            _CURRENT_STUDENT.set(sid or "")
            return sid

        def __exit__(self, *exc):
            # ⚠️ **复原成进来时那个值**，不是清成空串 —— 嵌套用的时候（admin1 的块里
            # 再开一层 admin2 验"归属人自己查得到"）退出内层就把外层身份擦了，
            # 后面几条会静默按 demo 口径跑，红得完全看不出原因（实测踩过）。
            _CURRENT_STUDENT.set(self._prev)
            return False

    return _Ctx()


def _seed_upload(fid, name, *, purpose=None, owner=None, body="内容", chunks=None, notice=None):
    """往沙箱的上传台账里摆一条（**不经路由**，为的是能造出"老数据没 owner"这种形状）。"""
    d = data_path("uploads")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{fid}.txt"), "w", encoding="utf-8") as f:
        f.write(body)
    item = {"id": fid, "name": name, "size": len(body), "ext": ".txt", "has_raw": True,
            "ts": "2026-10-20 00:00:00", "chars": len(body), "preview": body[:200]}
    if purpose is not None:
        item["purpose"] = purpose
    if owner is not None:
        item["owner"] = owner
    # **upsert**（不是覆盖整份）：一份台账里要同时摆"有归属的 / 没归属的 / 公共的 / 被通知引用的"，
    # 每次 dump 单条的话，后一次会把前一次抹掉 —— 那 B 批测的就是最后一份数据了。
    data = json.load(open(data_path("uploads.json"), encoding="utf-8")) \
        if os.path.exists(data_path("uploads.json")) else {"files": []}
    rows = [f for f in (data.get("files") or []) if f.get("id") != fid]
    rows.append(item)
    with open(data_path("uploads.json"), "w", encoding="utf-8") as f:
        json.dump({"files": rows}, f, ensure_ascii=False)
    if chunks is not None:
        # ⚠️ `<fid>.chunks.json` 顶层是 **dict**（`{"chunk_size":…, "chunks":[…]}`），
        # 不是裸列表 —— 写错形状 `all_chunks()` 直接吐 0 条，检索那两条就成了空跑。
        with open(os.path.join(d, f"{fid}.chunks.json"), "w", encoding="utf-8") as f:
            json.dump({"chunk_size": 600, "overlap": 80, "chunks": chunks}, f, ensure_ascii=False)
    if notice:
        # ⚠️ `notices.json` 顶层**就是列表**（`list_notices` 是 `reversed(_read(...))`），
        # 不是 `{"notices": [...]}` —— 写错形状会让 `upload_notice_refs` 一路 iter 到键名字符串上。
        with open(data_path("notices.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "n1", "title": "教学安排", "attachment":
                        {"id": fid, "name": name}}], f, ensure_ascii=False)
    return item


def batch_b_uploads():
    c = Checker()
    title("B. 45-B/45-D：上传桶按人分（工具那一路 + 三条免口令路由）")
    from app.modules import files
    from app import store

    A1, A2 = "admin1", "admin2"
    PRIV1, PRIV2, PUB, ATT, OLD = "f1admin1", "f2admin2", "kbpublic", "attach1", "oldlegacy"
    KBOTHER = "kbownedbyadmin2"                      # 管理员在学生浏览器里传的 kb（有归属）
    SECRET_TEXT = "线性代数 教三-301 周一第一节"      # 学生 1 课表原件的正文（要证明别人读不到）
    PUB_TEXT = "图书馆平时开放至二十二点三十分闭馆"
    OTHER_KB_TEXT = "游泳馆周四下午清场，六点以后不进人"

    with sandbox():
        _seed_upload(PRIV1, "2026秋季课表-纯文本.txt", purpose="file", owner=A1,
                     body=SECRET_TEXT,
                     # 旧块：#40 最初版给非 kb 也建过索引，磁盘上可能残留 —— 检索面必须自己再判一次
                     chunks=[{"i": 0, "text": SECRET_TEXT, "start": 0}])
        _seed_upload(PRIV2, "别人的课表.txt", purpose="file", owner=A2, body="流体力学 教五-03")
        _seed_upload(PUB, "学生手册-测试样本.txt", purpose="kb", owner="", body=PUB_TEXT,
                     chunks=[{"i": 0, "text": PUB_TEXT, "start": 0}])
        _seed_upload(ATT, "数据结构实验报告样例.docx", body="附件正文", notice=True)
        _seed_upload(OLD, "老数据没有归属.txt", body="没 owner 也没 purpose 的老行")
        _seed_upload(KBOTHER, "别人名下的知识文档.txt", purpose="kb", owner=A2, body=OTHER_KB_TEXT,
                     chunks=[{"i": 0, "text": OTHER_KB_TEXT, "start": 0}])

        with _as_student(A1):
            names1 = [f["name"] for f in files.list_uploaded_files()]
            c.check("B1 学生 1 的文件列表：自己的那份在，别人那两份（含同名）不在",
                    "2026秋季课表-纯文本.txt" in names1 and "别人的课表.txt" not in names1,
                    json.dumps(names1, ensure_ascii=False)[:120])
            c.check("B2 公共知识文档 + 通知附件对他也可见（别把该看的锁死）",
                    "学生手册-测试样本.txt" in names1 and "数据结构实验报告样例.docx" in names1,
                    json.dumps(names1, ensure_ascii=False)[:160])
            r1 = files.read_uploaded_file(PRIV1)
            c.check("B3 本人读自己的那份读得到", SECRET_TEXT in r1, r1[:60])
            r2 = files.read_uploaded_file(PRIV2)
            c.check("B4 读别人的那份 ⇒ 回「找不到」，而且**不许出现『无权』那种确认句**、正文一个字都不漏",
                    "找不到" in r2 and SECRET_TEXT not in r2 and "无权" not in r2 and "没有权限" not in r2,
                    r2[:80])
            hits = files.doc_hits("线性代数 教三301")
            c.check("B5 检索面也过不去：非 kb 的旧块（而且是别人传的）命不中",
                    not any(h["fid"] == PRIV1 for h in hits),
                    json.dumps([h.get("fid") for h in hits], ensure_ascii=False)[:100])
            other_hits = files.doc_hits("游泳馆 清场")
            c.check("B5-bis 带归属的 kb（管理员在已登录学生的浏览器里传的那种）也不进别人的检索",
                    not any(h["fid"] == KBOTHER for h in other_hits),
                    json.dumps([h.get("fid") for h in other_hits], ensure_ascii=False)[:100])
            with _as_student(A2):
                c.check("B5-ter 但那份的归属人自己查得到（证明 B5-bis 是「按人」不是「全砍」）",
                        any(h["fid"] == KBOTHER for h in files.doc_hits("游泳馆 清场")),
                        "admin2 视角命中列表为空")
            c.check("B6 对照：公共知识文档该命中还是要命中（别把闸写成谁都拦的死闸）",
                    any(h["fid"] == PUB for h in files.doc_hits("图书馆 闭馆")),
                    json.dumps([h.get("fid") for h in files.doc_hits("图书馆 闭馆")], ensure_ascii=False)[:90])
            vis1 = [f["name"] for f in files.list_uploaded_files()]
            c.check("B7 老数据（无 owner 无 purpose）对学生不放开 —— 谁读到都是错的",
                    "老数据没有归属.txt" not in vis1,
                    "身份=%r can_read(老)=%s can_read(本人那份)=%s 列表=%s"
                    % (store.current_owner(), store.can_read_upload({"id": OLD}),
                       store.can_read_upload({"id": PRIV1, "owner": A1, "purpose": "file"}),
                       json.dumps(vis1, ensure_ascii=False)[:120]))
            c.check("B8 管理员在同一浏览器（带着学生 cookie）上传但没标 public_kb ⇒ 记成私有",
                    store.can_read_upload({"id": "x", "owner": A1, "purpose": "kb"}) is True
                    and store.can_read_upload({"id": "x", "owner": A2, "purpose": "kb"}) is False,
                    "本人=%s 别人=%s" % (store.can_read_upload({"id": "x", "owner": A1, "purpose": "kb"}),
                                        store.can_read_upload({"id": "x", "owner": A2, "purpose": "kb"})))

        with _as_student(""):
            c.check("B9 未登录（demo 口径）时那份无主老数据仍可读 —— #35 的空状态引导与 test_59 B 批靠它",
                    "老数据没有归属.txt" in [f["name"] for f in files.list_uploaded_files()])
            c.check("B10 但未登录也**读不到有归属的**（demo 口径不是超级账号）",
                    "2026秋季课表-纯文本.txt" not in [f["name"] for f in files.list_uploaded_files()])

        cli1 = make_client(headers={"Cookie": f"ct_student={A1}"})
        cli2 = make_client(headers={"Cookie": f"ct_student={A2}"})
        l1 = [f["name"] for f in (cli1.get("/api/uploads").json().get("files") or [])]
        l2 = [f["name"] for f in (cli2.get("/api/uploads").json().get("files") or [])]
        c.check("B11 GET /api/uploads（免口令那条口）也按人过滤：两条列表交集只剩公共那两份",
                PRIV1 not in l2 and PRIV2 not in l1 and "学生手册-测试样本.txt" in l1 and "学生手册-测试样本.txt" in l2,
                f"admin1={l1} | admin2={l2}")
        d1 = cli2.get(f"/api/uploads/{PRIV1}")
        d2 = cli2.get(f"/api/uploads/{PRIV1}/download")
        c.check("B12 读正文与下载原件两条口：别人的 fid ⇒ 404（不是 200 空、不是 403）",
                d1.status_code == 404 and d2.status_code == 404, f"{d1.status_code}/{d2.status_code}")
        c.check("B13 本人那份仍 200（对照：闸不是「一律拒」）",
                cli1.get(f"/api/uploads/{PRIV1}").status_code == 200
                and cli1.get(f"/api/uploads/{PRIV1}/download").status_code == 200)
        c.check("B14 通知附件那份：学生能下载（这一条是实测逼出来的偏离，施工单的判据会误伤它）",
                cli2.get(f"/api/uploads/{ATT}/download").status_code == 200
                and cli1.get(f"/api/uploads/{ATT}/download").status_code == 200)
    return c


def batch_c_pending():
    c = Checker()
    title("C. 45-C：暂存卡的键 = 身份 + session_id（换账号不再串卡）")
    from app import store
    from app.agent import pending

    A1, A2 = "admin1", "admin2"
    SID, SID2 = "sess-c-1", "sess-c-2"
    CARD = [{"kind": "todo_add", "title": "交实验报告", "due": "2026-10-21 20:00",
             "text": "周三 20:00 交实验报告", "applied": False}]

    # ---- 静态那两条先跑（不进沙箱：它们判的是源码与前端文件，跟数据目录无关）----
    html = _src("app/static/login.html")
    c.check("C5 登录页换人时清掉浏览器级 session（与退出登录同一条理由，两边都得有）",
            'localStorage.removeItem("campustime_session")' in html,
            "campustime_session 在该文件出现 %d 次" % html.count("campustime_session"))
    c.check("C5-bis 只清那一个键：整片清空会把外观/昵称/模型配置这些**偏好**一起弄丢",
            "localStorage.clear()" not in html,
            "出现 localStorage.clear() ⇒ 换账号会连带丢掉 ct_bg / ct_profile_name / ct_model_cfg")

    bare = _strip(_src("app/agent/pending.py"))
    leftovers = [b for b in ("_PENDING[session_id]", "_PENDING.get(session_id",
                             "_PENDING.pop(session_id", "_pending_entry(session_id)")
                if b in bare]
    c.check("C7 pending.py 里对 `_PENDING` 的每一次读写都过 `_key`（漏一处就是「只改了一半」，"
            "而 C1 那种单向断言正好抓不到漏的那一半）",
            not leftovers and bare.count("_key(session_id)") >= 5,
            f"仍收裸 id：{leftovers} / _key(session_id) 出现 {bare.count('_key(session_id)')} 次（出口应有 5 个）")

    with sandbox():
        with _as_student(A1):
            pending.save_pending(SID, CARD)

        with _as_student(A2):
            other = pending.peek_pending(SID)
        with _as_student(A1):
            mine = pending.peek_pending(SID)
        # ⚠️ 配对断言（不是单向）：施工单把 C1 写成"别人拿不到"，可"只改了一半"的实现
        # 同样能通过它 —— 只有把"本人还得拿得到"放进**同一条**里，改一半才会当场红。
        c.check("C1 学生 1 存的卡：他自己在同一 session_id 上仍拿得到，学生 2 一个字都看不到",
                mine is not None and other is None,
                "本人=%r 别人=%r" % (None if mine is None else mine.get("options"), other))

        with _as_student(A2):
            stolen = pending.take_pending(SID)
        with _as_student(A1):
            still = pending.peek_pending(SID)
        c.check("C2 别人来『取』（确认后落库那条路）也拿不走，而且不许顺手把本人那张擦掉",
                stolen is None and still is not None,
                "admin2 take=%r / admin1 之后还在=%s" % (stolen, still is not None))

        with _as_student(""):
            pending.save_pending(SID2, CARD)
        with _as_student(A1):
            student_view = pending.peek_pending(SID2)
        with _as_student(""):
            demo_view = pending.peek_pending(SID2)
        c.check("C3 未登录（demo 口径）出的卡：登录后看不到（他换了身份），demo 自己仍然接得回来",
                student_view is None and demo_view is not None,
                "学生=%r demo=%r" % (student_view, demo_view is not None))

        # 接口层真验（内存对了不代表 HTTP 出口对）：cookie **走 /api/login 拿**。
        # ⚠️ 别手写 `Cookie: ct_student=admin1`：一旦配了 CAMPUSTIME_COOKIE_SECRET，
        #    手写值会被 `_clean_student` 判非法 ⇒ 两边都走 demo 口径 ⇒ 两条都一样 = **假绿**。
        cli1, cli2 = make_client(), make_client()
        r1 = cli1.post("/api/login", json={"student": A1})
        r2 = cli2.post("/api/login", json={"student": A2})
        p1 = cli1.get(f"/api/pending/{SID}").json()
        p2 = cli2.get(f"/api/pending/{SID}").json()
        c.check("C4 GET /api/pending/{sid} 在两个真 cookie 下分化：一个发回那张卡，一个回 null 且不泄漏标题",
                r1.status_code == 200 and r2.status_code == 200
                and (p1.get("pending") or {}).get("title") == "交实验报告"
                and p2.get("pending") is None
                and "交实验报告" not in json.dumps(p2, ensure_ascii=False),
                "登录=%s/%s cookie=%s/%s admin1→%s admin2→%s"
                % (r1.status_code, r2.status_code, bool(dict(cli1.cookies)), bool(dict(cli2.cookies)),
                   json.dumps(p1, ensure_ascii=False)[:80], json.dumps(p2, ensure_ascii=False)[:60]))

        disk = json.load(open(data_path("pending.json"), encoding="utf-8"))
        keys = list(disk)
        # ⚠️ 判据钉的是"前缀 == store 的口径本身"，**不是**"键里含 |"：
        # 变异 M18（`_key` 用 `current_student()` 而非 `current_owner()`）落出来的键是 `|sess-c-2`，
        # 一样带分隔符、一样把两个人分得开，只写"含 |"它当场溜过去（2026-10-20 实测：C 批 8 条全绿、
        # 退出码 0）。而它实际是坏的 —— 没身份时前缀成了空串，与 `visible_rows` / `_spath`
        # 那套 `demo` 口径不是同一个东西，两处口径迟早打架。
        c.check("C6 落盘形状跟着变：键 = `<store 口径>|<sid>`（裸 sid 一个都不许有，前缀必须是口径本身）"
                "（只改内存、盘上仍旧共享 = 重启后照样串）",
                f"{A1}|{SID}" in keys and f"{store.DEMO_OWNER}|{SID2}" in keys
                and SID not in keys and SID2 not in keys,
                "键=%s / DEMO_OWNER=%r / 当前口径=%r" % (keys, store.DEMO_OWNER, store.current_owner()))
    return c


BATCHES = [("A 批（45-A 课表来源）", batch_a_schedule_source),
           ("B 批（45-B/45-D 上传桶按人分）", batch_b_uploads),
           ("C 批（45-C 暂存卡按身份分键）", batch_c_pending)]


def main():
    global code
    print("test_72_data_isolation：#45 学生个人数据必须按人分（课表 / 上传桶 / 暂存卡）")
    print("=" * 60)
    for name, fn in BATCHES:
        rc = fn().summary(name)
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
