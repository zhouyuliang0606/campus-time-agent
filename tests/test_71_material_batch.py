"""#43 的护栏：粘进来的一段材料 → 提取多条待办 → 学生勾选 → 系统批量写。

用户 2026-10-19 12:57 真机：日程栏粘一段考试通知（`项目 内容 / 考试科目 离散数学 /
考试日期 2026年10月21日 / 19:30-21:30 / 松山湖 6F-403`），系统回的是
「那天图书馆按平时时间 22:30 闭馆」—— 它把材料当了一句普通问话，交给了**带着上一轮历史**的模型。
实测这一版偶尔能读对（`probe43_repro.py`），但那更糟：**同一段材料的结果取决于模型那一轮想到什么**。

四批：A 判据与提取（纯函数，不经模型 ⇒ 稳）、B 端到端（真发 /api/chat + /api/todos/items_add）、
C 判据形状（AST：位置、一份定义、不许带上文）、D #43 顺手补的那道「是什么」排除。

⚠️ 全程**不依赖大模型**：沙箱里没有密钥（`test_68` B3 那句 no_key 降级提示就是证据），
而材料这一路本来就该是确定性分支 —— 提取靠 `material`，写库靠"学生点了那条按钮"。
"""
import ast
import datetime
import json
import os
import re
import shutil

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, seed_todos, title

code = 0


# ⚠️ 材料里的日期一律**从"明天"往后推**，不写死（2026-10-22 全量实测：本文件原来钉着
#    「10月21日」，跨过午夜它就成了**过去**的日子 —— 提取那一步整条丢掉、写库那一步整批 400，
#    一次红 8 条，而且以后每天更红。挂账 #69 那一族的第四个实例，同 #44-B 的修法。）
_BASE = datetime.date.today() + datetime.timedelta(days=1)


def _md(off=0):
    """「10月25日」这种材料里常见的写法。"""
    d = _BASE + datetime.timedelta(days=off)
    return f"{d.month}月{d.day}日"


def _cn(off=0):
    """「2026年10月25日」。"""
    d = _BASE + datetime.timedelta(days=off)
    return f"{d.year}年{d.month}月{d.day}日"


def _iso(off=0):
    d = _BASE + datetime.timedelta(days=off)
    return d.strftime("%Y-%m-%d")


TABLE = ("项目 内容\n考试科目 离散数学\n考试日期 " + _cn(0) + "\n"
         "考试时间 19:30-21:30\n考试地点 松山湖校区 6F-403\n"
         "监考老师 李老师\n注意事项 请携带学生证与身份证，提前三十分钟到场。\n")
LIST3 = (_md(0) + " 19:30-21:30 离散数学考试\n"
         + _md(1) + " 14:00-15:40 数据结构补考\n"
         + _md(2) + " 09:00-10:30 概率论答疑\n")
# 上文里那份文档的事实：材料答错时就会冒出这句（G2 的反面靶子）
LIB_FACT = "图书馆平时开放至二十二点三十分闭馆"
DOC_NAME = "43-图书馆开放时间.txt"
# test_23 里模型"读了课表文件却不出卡"时爱发的那张表（原文形状）：
# 学生把它复制回来再粘一次，看起来就和裸粘课表一模一样 ⇒ 材料判据必须不接（A11-ter）
TABLE_ANSWER = (
    "解析出来了，共 2 门课：\n\n"
    "| 星期 | 时间 | 课程 | 地点 |\n|---|---|---|---|\n"
    "| 周一 | 08:00–09:40 | 高等数学 | 教三-301 |\n"
    "| 周五 | 14:00–15:40 | 体育 | 大操场 |\n\n"
    "先给你看看，需要导入再说。"
)
# 两种"裸粘课表"：一个字没提「上传/导入」，所以 `looks_like_timetable_import` 接不住（A11/B5）
TT_MD = ("| 星期 | 时间 | 课程 | 地点 |\n|---|---|---|---|\n"
         "| 周一 | 08:00-09:40 | 高等数学 | 教三-301 |\n"
         "| 周二 | 10:00-11:40 | 线性代数 | 教三-302 |\n"
         "| 周五 | 14:00-15:40 | 体育 | 大操场 |\n")
TT_XLS = ("周一 08:00-09:40 高等数学 教三-301\n周二 10:00-11:40 线性代数 教三-302\n"
          "周三 14:00-15:40 概率论 教三-303\n周五 14:00-15:40 体育 大操场\n")


def _seed_lib_doc():
    from app import store
    body = f"《图书馆开放时间》\n{PAD * 6}\n{LIB_FACT}。\n" + PAD * 6
    return store.save_upload(DOC_NAME, body, raw=body.encode("utf-8"), purpose="kb")["id"]


PAD = "本条为通用说明，受理时段与办结时限以当日公告为准，节假日安排另行公布。"


def _wipe_uploads():
    """只留本单自种的文档（`sandbox()` 是复制真实数据，开发机上有管理员真传过的手册）。"""
    json.dump({"files": []}, open(data_path("uploads.json"), "w", encoding="utf-8"), ensure_ascii=False)
    up = data_path("uploads")
    if os.path.isdir(up):
        shutil.rmtree(up)
    os.makedirs(up, exist_ok=True)


def _wipe_kb():
    json.dump({"entries": []}, open(data_path("kb.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _env():
    _wipe_kb()
    _wipe_uploads()
    _seed_lib_doc()
    seed_todos([])


def _say(cli, msg, module=None, sid="t71"):
    body = {"message": msg, "session_id": sid}
    if module:
        body["module"] = module
    d = cli.post("/api/chat", json=body).json()
    phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
    return d, (d.get("answer") or ""), phases


def _card(d):
    opts = d.get("options") or []
    return opts[0] if opts else {}


def _tt_import(t):
    """课表那句**文字**判据（"上传/读取/解析/导入 + 课表"）—— A11 要证明让路不是靠它。"""
    from app.modules.planner import looks_like_timetable_import
    return looks_like_timetable_import(t)


def _titles(card):
    return [i.get("title") for i in (card.get("items") or [])]


def batch_a_extract():
    c = Checker()
    title("A. 材料判据与提取（纯函数：不经模型 ⇒ 结果不随心情漂）")
    from app.modules import material as M

    with sandbox():
        _env()

        items, missing = M.extract_deterministic(TABLE)
        c.check("A1 用户那段考试通知 → **一条**：离散数学考试｜明天 19:30-21:30（施工单 G1 的期望）",
                items == [{"title": "离散数学考试", "date": _iso(0),
                           "start": "19:30", "end": "21:30"}], str(items))
        c.check("A2 表里的「监考老师/注意事项」不再被拆成两件事（一张表 = 一件事）",
                len(items) == 1 and not any("李老师" in t for t in _titles({"items": items})),
                str(items))

        it3, miss3 = M.extract_deterministic(LIST3)
        c.check("A3 三行清单 → 三条（施工单 G3）", len(it3) == 3,
                json.dumps([i["title"] for i in it3], ensure_ascii=False))
        c.check("A3-bis 三条各自带自己的日期与时段（不是把第一条复制到三行）",
                {i["date"] for i in it3} == {_iso(0), _iso(1), _iso(2)}
                and all(i["start"] and i["end"] for i in it3), json.dumps(it3, ensure_ascii=False))

        mixed = ("1. 周三下午两点班会（14:00-15:30）\n2. 周五交离散数学作业\n3. 下周一体测 08:00-09:00\n")
        it4, miss4 = M.extract_deterministic(mixed)
        c.check("A4 编号清单里缺钟点的那行 → 进 missing，**不**替它编时间",
                len(it4) == 2 and len(miss4) == 1 and "周五" in miss4[0],
                f"{[i['title'] for i in it4]}｜{miss4}")
        c.check("A4-bis 残渣别上卡：「周三下午两点班会（14:00-15:30）」标题不该是 `班会（）`",
                all("(" not in t and "（" not in t for t in _titles({"items": it4})),
                _titles({"items": it4}))

        no_date = ("项目 内容\n考试科目 离散数学\n考试地点 松山湖校区 6F-403\n监考老师 李老师\n")
        it5, miss5 = M.extract_deterministic(no_date)
        c.check("A5 整段没日期 → items 空、missing 非空（G8 的一半：**不许编一个今天**）",
                it5 == [] and len(miss5) >= 1, f"{it5}｜{miss5}")

        two = "10月28日 09:00-10:00 新生体检\n10月29日 14:00-15:00 体测补测\n"
        it6, _ = M.extract_deterministic(two)
        c.check("A9 两行带日期的清单也算材料（「多行」从两行起 —— 要求三行起就把最常见的清单漏了）",
                M.looks_like_material(two) is True and len(_titles({"items": it6})) == 2,
                str(_titles({"items": it6})))
        c.check("A9-bis 两行但只有一行带日期 → 不算材料（另一行是附带说明时别硬凑成两条）",
                M.looks_like_material("明天开会\n记得带笔记本") is False)
        # 浏览器实测（2026-10-19，/student 上真粘了一次）：`input#text` 会把换行**整个吃掉**，
        # 46 字那份短通知既不够 60 字也没有 \n ⇒ 从前被规则层的"加待办"接走，
        # 卡上标题是残渣「目内容考试科目离散数学考试日期2026年考试」。前端改把换行换成"；"，
        # 这里则必须认"分号分隔的短材料"，两头都得有，少一头这条链就断。
        flat4 = "项目 内容；考试科目 离散数学；考试日期 2026年10月21日；考试时间 19:30-21:30"
        it7, _ = M.extract_deterministic(flat4)
        c.check("A10 粘成一行的短材料也认（只数 `\\n` 的话它就不算材料，会掉回加待办那条出残渣）",
                M.looks_like_material(flat4) is True and len(it7) == 1
                and it7[0]["title"] == "离散数学考试" and it7[0]["start"] == "19:30",
                json.dumps(it7, ensure_ascii=False)[:120])
        c.check("A10-bis 残渣不进卡：整行被当成一个事名时（没有分号也没有换行）不算材料",
                M.looks_like_material("项目 内容考试科目离散数学考试日期2026年10月21日") is False)

        # ── 对照：这几句都不是材料（判据写太宽就会把正常问话吞掉）
        for q in ("考试时间是什么", "今晚安排点啥", "图书馆几点关门", "帮我加个周五的游泳 19:00",
                  "我今天要做什么", "校历什么时候发布", "宿舍晚上几点断电"):
            c.check(f"A6 对照：「{q}」不算材料（长度/字段/问句三条里至少一条不成立）",
                    M.looks_like_material(q) is False, str(M.looks_like_material(q)))
        c.check("A7 带问号的长文本也不算材料（疑问主干是**硬否决**）",
                M.looks_like_material(TABLE + "\n这个安排是从哪里来的？") is False)
        c.check("A8 材料判据**不看模块**：同一段不带 module 也算材料（前端收起面板时压根不带）",
                M.looks_like_material(TABLE) is True)

        # ── A11：粘进来的是**周课表**时不许接（实测 `probe43_shapes.py` / `probe43_yield.py`）
        # 裸粘的课表既是多行、又满是"课程/时间/地点"，形状三条一条不落全命中；
        # 从前只挡 `looks_like_timetable_import`（认的是"上传/导入 + 课表"那串**文字**），
        #   而下面三种课表那句话都没提 —— 于是卡上写着 `|||高等数学|教三301|`，
        #   还把每周重复的课钉死到 2026-10-26 这一天，学生点【加入日程】就真写进日程。
        for nm, tt in (("A11 markdown 课表", TT_MD), ("A11-bis Excel 复制的纯文本课表", TT_XLS)):
            c.check(f"{nm} 不算材料 —— 且**不是靠** tt_import 让的路（它一个字没提「上传/导入」）",
                    M.looks_like_material(tt) is False
                    and _tt_import(tt) is False
                    and M._looks_like_weekly_grid(tt.strip(), M.split_material(tt.strip())) is True,
                    str([M.looks_like_material(tt), _tt_import(tt)]))
        c.check("A11-ter 连**模型自己回的那张课表表格**也不算材料（学生把它复制回来问一句，"
                "形状和裸粘课表一模一样）",
                M.looks_like_material(TABLE_ANSWER) is False
                and M._looks_like_weekly_grid(TABLE_ANSWER.strip(), M.split_material(TABLE_ANSWER.strip())) is True,
                str(M.looks_like_material(TABLE_ANSWER)))
        c.check("A11-quater 「1. 周三… 2. 周五…」那种**用星期写的待办清单**仍然算材料"
                "（行首编号就是「它不是周表」的证据；把编号豁免摘掉这批清单就全退回 faq）",
                M.looks_like_material(mixed) is True
                and M._looks_like_weekly_grid(mixed, M.split_material(mixed)) is False,
                str(M.looks_like_material(mixed)))
        c.check("A11-quinquies 只要有一行写了**几月几日**就不再是周表（周X + 月日混着粘也得接住，"
                "那是本单要处理的清单）",
                M.looks_like_material("周一 08:00-09:40 高等数学\n10月21日 19:30-21:30 离散数学考试 地点 教三") is True
                and M.looks_like_material(LIST3) is True)

        # ── A12：标题只剩日期/竖线时**不许上卡**
        # 实测「关于召开本学期第二次班会的通知：时间：2026年10月23日 15:00-16:00 …」经表那条支
        # 拿到的标题就是那串日期 —— 卡上写着日期、看不出是哪件事，点【加入日程】却真能写进去。
        MEET = ("关于召开本学期第二次班会的通知：\n时间：2026年10月23日 15:00-16:00\n"
                "地点：教三-301\n对象：全体同学\n议题：期中安排、评奖评优、宿舍安全。\n")
        it8, miss8 = M.extract_deterministic(MEET)
        c.check("A12 残渣标题不进卡（items 空 ⇒ 走「拆不出」那句），原文落进 missing 让学生自己补",
                it8 == [] and len(miss8) >= 1,
                json.dumps({"items": it8, "missing": miss8}, ensure_ascii=False)[:160])
        c.check("A12-bis 闸只认**形状**：同一句里标题写成「班会」就必须能上卡"
                "（不许写成「标题不认识就一律不收」那种一刀切）",
                M._mk_item("班会", {"date": "2026-10-23", "start": "15:00", "end": "16:00"}) is not None
                and M._mk_item("离散数学考试", {"date": "2026-10-21", "start": "19:30", "end": "21:30"}) is not None)
    return c


def batch_b_end_to_end():
    c = Checker()
    title("B. 端到端：粘材料 → 多选卡 → 勾完才写（无密钥，全确定性）")
    from app import store

    with sandbox():
        _env()
        cli = make_client()

        # ── G1 主案
        d1, a1, p1 = _say(cli, TABLE, sid="t71-g1")
        card = _card(d1)
        c.check("G1 不带 module 粘材料：落 planner、出 todo_add_batch 卡、**不落 faq**",
                d1.get("module") == "planner" and card.get("kind") == "todo_add_batch"
                and _titles(card) == ["离散数学考试"], f"{d1.get('module')}｜{_titles(card)}")
        c.check("G1-bis 卡上每条默认带上 checked（前端据此打勾），且没写时间的走 missing 字段",
                card["items"][0].get("checked") is True and isinstance(card.get("missing"), list),
                json.dumps(card, ensure_ascii=False)[:120])
        c.check("G1-三 轨迹里看得见这条分支（不是掉给模型现编的一句）",
                "材料" in p1, p1[:100])

        # ── G2 上文污染：先在同一会话里留下一轮**带日期时段**的回话（列待办），再粘材料。
        #    （第一版拿"图书馆几点闭馆"当上文，变异 M2 把历史拼进提取都测不出红 ——
        #      那句回话里没有可被当成待办的字段；上文里必须**有可漏的东西**，这条才不是空跑。）
        seed_todos([{"title": "高数作业", "date": "2026-10-30", "start": "09:00", "end": "10:00"}])
        d0, a0, _ = _say(cli, "我有哪些待办", module="planner", sid="t71-g2")
        d2, a2, _ = _say(cli, TABLE, sid="t71-g2")
        card2 = _card(d2)
        from app.modules.faq import search_kb
        c.check("G2-bis 见证：上一轮那条回话里**确实有一件带日期时段的事**（这才是可漏的上文）",
                "高数作业" in a0 and "2026-10-30" in a0, a0[:80])
        c.check("G2 上文在列待办、再粘考试通知 → 卡里只有考试（历史那条不许混进来）",
                _titles(card2) == ["离散数学考试"], str(_titles(card2)))
        c.check("G2-三 文档里那句闭馆事实是真的存在（知识库里查得到），但这条气泡里没有它",
                LIB_FACT in search_kb("图书馆几点闭馆") and "闭馆" not in a2, a2[:90])

        # ── G4 不确认不写库；刷新（GET /api/pending）卡还在
        before = len(store.list_todos())
        d3, _, _ = _say(cli, TABLE, sid="t71-g4")
        c.check("G4 只粘不点 → 日程一个字不动", len(store.list_todos()) == before,
                f"{before} → {len(store.list_todos())}")
        pend = cli.get("/api/pending/t71-g4").json()
        c.check("G4-bis 刷新后卡还在（pending 落盘 + 未 applied 才回，#33 那一族）",
                isinstance(pend.get("pending"), dict)
                and pend["pending"].get("kind") == "todo_add_batch",
                json.dumps(pend, ensure_ascii=False)[:110])

        # ── G5 取消勾选：勾两条写两条、一条不勾不写
        d5, _, _ = _say(cli, LIST3, sid="t71-g5")
        card5 = _card(d5)
        c.check("G5-前置 三行清单先真出卡（B 批这条链要的是「卡 → 点按钮」，不是直接打接口）",
                card5.get("kind") == "todo_add_batch" and len(card5.get("items") or []) == 3,
                str(_titles(card5)))
        r5 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g5", "from_card": True,
            "items": [{"title": "离散数学考试", "date": _iso(0), "start": "19:30", "end": "21:30"},
                      {"title": "数据结构补考", "date": _iso(1), "start": "14:00", "end": "15:40"}]})
        j5 = r5.json()
        c.check("G5 勾两条点【加入日程】→ 只写这两条", r5.status_code == 200 and j5.get("count") == 2,
                json.dumps(j5, ensure_ascii=False)[:130])
        c.check("G5-bis 回执一句里带着标题与日期（学生能看到写进去的是哪几条）",
                "离散数学考试" in (j5.get("message") or "") and _iso(0) in (j5.get("message") or ""),
                (j5.get("message") or "")[:90])
        n0 = len(store.list_todos())
        r6 = cli.post("/api/todos/items_add", json={"session_id": "t71-g6", "items": []})
        c.check("G5-三 一条都没勾 → 400 且不写库（前端也不该发，双道）",
                r6.status_code == 400 and len(store.list_todos()) == n0,
                r6.json().get("error", "")[:70])

        # ── G6 重复粘贴 / 已存在：跳过而不是再写一条
        #    日期必须与 G5 写进去的那两条**同一把算出来的**（写死日期 = 跨过午夜整批 400）
        r7 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g7",
            "items": [{"title": "离散数学考试", "date": _iso(0), "start": "19:30", "end": "21:30"}]})
        j7 = r7.json()
        c.check("G6 同一条再写一次 → 算重复跳过，库里不出现第二条",
                j7.get("count") == 0 and len(j7.get("skipped") or []) == 1,
                json.dumps(j7, ensure_ascii=False)[:130])
        c.check("G6-bis 库里那条『离散数学考试』仍然只有一条",
                sum(1 for t in store.list_todos() if t["title"] == "离散数学考试") == 1)
        r8 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g8",
            "items": [{"title": "离散数学考试", "date": _iso(0), "start": "19:30", "end": "21:30"},
                      {"title": "概率论答疑", "date": _iso(2), "start": "09:00", "end": "10:30"}]})
        j8 = r8.json()
        c.check("G6-三 一批里混着已有与新的：已有的跳过、新的写进去（回执两数都说清）",
                j8.get("count") == 1 and len(j8.get("skipped") or []) == 1,
                json.dumps(j8, ensure_ascii=False)[:140])

        # ── 整批拒：坏一条不写半批
        n1 = len(store.list_todos())
        r9 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g9",
            "items": [{"title": "好的", "date": "2026-10-25", "start": "09:00", "end": "10:00"},
                      {"title": "坏的", "date": "2026-13-99", "start": "09:00", "end": "10:00"}]})
        e9 = r9.json().get("error", "")
        c.check("B1 一条不合法 → 整批 400、点名第几条、半批都不写",
                r9.status_code == 400 and "第 2 条" in e9 and len(store.list_todos()) == n1,
                f"{e9[:70]}｜库 {n1}→{len(store.list_todos())}")
        r10 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g10",
            "items": [{"title": "过去的", "date": "2020-05-06", "start": "09:00", "end": "10:00"}]})
        c.check("B2 过去的日子算不合法（与单条口同一份判据 `is_past_date`）",
                r10.status_code == 400 and "过去" in r10.json().get("error", ""),
                r10.json().get("error", "")[:80])

        # ── #33 那一幕：写完刷新又点一次（出卡 → 点 → 再点）
        #    刻意用一份本文件里**没写过**的料：上面 G5/G6 已经把 概率论答疑 等几条落库了，
        #    复用同一件事的话第二笔会被"去重"先挡掉，量的就不再是销卡那一档了。
        FRESH = "10月28日 09:00-10:00 新生体检\n10月29日 14:00-15:00 体测补测\n"
        d11, _, _ = _say(cli, FRESH, sid="t71-g11")
        c11 = _card(d11)
        row11 = {"title": "新生体检", "date": "2026-10-28", "start": "09:00", "end": "10:00"}
        r11a = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g11", "from_card": True, "items": [row11]})
        r11 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g11", "from_card": True, "items": [row11]})
        c.check("B3 同一张卡点第二次 → 回『刚刚已经办过了』，不写第二遍",
                r11a.json().get("count") == 1 and r11.json().get("already") is True
                and sum(1 for t in store.list_todos() if t["title"] == "新生体检") == 1,
                json.dumps([r11a.json(), r11.json()], ensure_ascii=False)[:150])
        c.check("B3-bis 出卡那一步确实挂上了多选卡（否则 B3 是空跑：压根没点过卡）",
                c11.get("kind") == "todo_add_batch" and "新生体检" in _titles(c11), str(_titles(c11)))
        r12 = cli.post("/api/todos/items_add", json={
            "session_id": "t71-g12x", "items": [
                {"title": "图书馆读书会上午场", "date": "2026-10-30", "start": "10:00", "end": "11:00"}]})
        c.check("B4 不带 from_card 的调用（面板/自动化直接打接口）照常写 —— 销卡闸只认卡片那一路",
                r12.status_code == 200 and r12.json().get("count") == 1,
                json.dumps(r12.json(), ensure_ascii=False)[:110])

        # ── G8 无密钥 + 拆不出：老实说，不编日期
        d12, a12, _ = _say(cli, "项目 内容\n考试科目 离散数学\n监考老师 李老师\n", sid="t71-g12")
        # 改口（#50，2026-10-21）：原来钉的是「没有一条写着日期或钟点」，而那句**在说假话** ——
        # 真机那份材料日期读到了、缺的只是钟点。新文案「没有一条**同时**写着日期和钟点」在两种
        # 情况下都成立。**放宽的只是字面，判据两半都还在**：仍然不许出卡、不许写库（下面照旧）。
        c.check("G8 没日期又没密钥 → 一句「没有一条同时写着日期和钟点」，不出卡、不写库",
                "同时写着日期和钟点" in a12 and not (d12.get("options") or []), a12[:100])
        c.check("G8-bis 这条不许掉给模型（无密钥时那句 no_key 降级提示不算答案）",
                "答不上来" not in a12 and "还没接通" not in a12, a12[:80])

        # ── B5 端到端的抢线：A11 那条形状判据在真实链路上有没有用
        # 摘掉 `_looks_like_weekly_grid` 这一半（变异 M10），这句话在 `/api/chat` 上就会挂出
        # 一张标题是 `|||高等数学|教三301|` 的多选卡 —— 只测纯函数是测不到这一层的。
        d13, a13, _ = _say(cli, TT_MD, sid="t71-b5")
        kinds13 = [o.get("kind") for o in (d13.get("options") or []) if isinstance(o, dict)]
        c.check("B5 裸粘 markdown 课表：端到端不许挂出 todo_add_batch 卡（点一下就把每周的课写死成某一天）",
                "todo_add_batch" not in kinds13, json.dumps(kinds13, ensure_ascii=False))
        c.check("B5-bis 这条也不许被吞成一张**单条**待办卡（那等于把整张课表当成一件事）",
                "todo_add" not in kinds13, json.dumps(kinds13, ensure_ascii=False))
        c.check("B5-ter 见证：没出卡≠链路坏了 —— 同一次会话里再把真材料粘进来，卡照常出",
                _card(_say(cli, TABLE, sid="t71-b5")[0]).get("kind") == "todo_add_batch",
                a13[:60])
    return c


def batch_c_shape():
    c = Checker()
    title("C. 形状（位置即判据：AST 看接线，不看注释）")
    mn = _tree("app/main.py")
    mt = _tree("app/modules/material.py")

    body = _func(mn, "_chat_impl")
    txt = ast.unparse(body)
    i_judge = txt.find("looks_like_material(message)")
    i_classify = txt.find("await classify(")
    i_force = txt.find("if _is_material:")
    i_route = txt.find("module_key = _explicit_module or _sem_module")
    c.check("C1 材料判据排在 `classify` **之前**（判定层带着上一轮历史，长文本喂进去就是这次翻车的那一步）",
            0 <= i_judge < i_classify, f"判据 @{i_judge} / classify @{i_classify}")
    c.check("C2 强制归 planner 排在 `module_key` 解析**之后**（要在它之后才改得动结论）",
            0 <= i_route < i_force, f"module_key @{i_route} / 强制 @{i_force}")

    br = None
    for node in ast.walk(body):
        if isinstance(node, ast.If) and "looks_like_material" in _calls_of(node.test):
            br = None
    c.check("C3 提取那一段不调 faq / 不调模型：分支体里只有 `extract_deterministic` 与几个 `_material_*`",
            _material_branch_isolated(body), _branch_calls(body))

    c.check("C4 `_material_answer` / `_material_card` 各只有一份定义（三处出口迟早会漂）",
            _def_count(mn, "_material_answer") == 1 and _def_count(mn, "_material_card") == 1)
    c.check("C5 `material.py` 不 import faq / agent / store（一 import 就把上文与检索面拖进来）",
            not any(m.startswith("app.modules.faq") or m.startswith("app.agent") or m == "app.store"
                    for m in _imports(mt)), str(sorted(_imports(mt))))
    c.check("C6 写入口用 `_card_close_guard` + `_close_card`（#33 那一幕的两个半边，少一个就会刷新复活）",
            {"_card_close_guard", "_close_card"} <= _calls_in(_func(mn, "todo_items_add")))
    st = _tree("app/store.py")
    ab = _func(st, "add_todos_batch")
    c.check("C7 批量写只**读写一次**（循环调 add_todo 时后一条看不见刚写进去的前一条，重复就拦不住）",
            _call_count(ab, "_swrite") == 1 and _call_count(ab, "_sread") == 1)
    c.check("C8 材料卡进了 `_holding_card` 那份名单（挂着它时 #42-A 那道闸与 3i 直答都不许接管这句）",
            "_pending_todo_add_batch" in _holding_card_calls(body), _holding_card_calls(body)[:60])

    html = open(os.path.join(PROJ_DIR, "app", "static", "student.html"), encoding="utf-8").read()
    js_lines = [ln for ln in html.splitlines()
                if "/api/todos/items_add" in ln and not ln.strip().startswith("//")]
    c.check("C9 前端真在打这个口（数**非注释行**，注释里提一句不算接线）",
            len(js_lines) == 1, str([l.strip()[:60] for l in js_lines]))
    return c


def batch_d_content_ask():
    c = Checker()
    title("D. #43 顺手补的那道排除：问『那件事是什么』不是问『我该做什么』")
    from app.modules.planner import _asking_what_to_do, asks_about_content

    for q in ("考试时间是什么", "会议议程是什么", "考试地点是什么"):
        c.check(f"D1 「{q}」不再被当成问该做什么（施工单 G7 的那一句）",
                _asking_what_to_do(q) is False, str(_asking_what_to_do(q)))
    # 反向：这几句确实是在问"我该做什么"，被摘掉就是 #42 那一族修坏了
    for q in ("我该做什么", "我要做什么", "今晚安排点啥", "现在干啥"):
        c.check(f"D2 对照：「{q}」照旧算问该做什么（新排除不许把它一起摘了）",
                _asking_what_to_do(q) is True, str(_asking_what_to_do(q)))
    c.check("D3 判据只认句尾「事名 + 是 + 什么/啥」：裸一个「是什么」开头不算",
            asks_about_content("是什么") is False and asks_about_content("考试是啥") is True)
    c.check("D4 列表档不受影响（「我现在都有啥安排」仍然归列表，不归这族）",
            _asking_what_to_do("我现在都有啥安排") is False)
    # 这条钉的是**动词表不许扩到名词**（把"排"放进 `[做干办搞整]` 就会被吃掉）；
    # 新规则自己的牙在 D1 + 变异 M9（摘掉第四道排除 ⇒ D1 三条全红）。
    c.check("D5 「考试安排是什么」不算问该做什么（动词表里不许塞进名词性说法）",
            _asking_what_to_do("考试安排是什么") is False, str(_asking_what_to_do("考试安排是什么")))
    c.check("D6 对照：「今晚安排点啥」有「安排 + 疑问」但不是「…是什么」，照旧算问该做什么",
            _asking_what_to_do("今晚安排点啥") is True)
    return c


# ---------------- AST 小工具 ----------------

def _tree(rel):
    return ast.parse(open(os.path.join(PROJ_DIR, *rel.split("/")), encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _def_count(tree, name):
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls_in(fn):
    out = set()
    if not fn:
        return out
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _call_count(fn, name):
    if not fn:
        return 0
    return sum(1 for n in ast.walk(fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name)


def _imports(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
    return out


def _calls_of(node):
    return _calls_in(node)


def _material_branch_if(body):
    """材料那道分支的节点：条件里带 `_is_material` **且体内真在提取**（`classify` 那句条件里
    也有 `_is_material`，光看条件会挑错节点 —— C3 第一版就是这么假红的）。"""
    best = None
    for n in ast.walk(body):
        if isinstance(n, ast.If) and "_is_material" in ast.unparse(n.test):
            if "extract_deterministic" in _calls_in(n):
                return n
            best = best or n
    return best


def _branch_calls(body):
    br = _material_branch_if(body)
    return sorted(_calls_in(br)) if br else []


def _material_branch_isolated(body):
    """材料分支体内**不许出现**：知识库、模型、会话历史（"不带上文"这条钉的是结构，
    不是某次跑出来的文案 —— 无密钥时历史里有没有可漏的东西全看分支写法）。"""
    br = _material_branch_if(body)
    if br is None:
        return False
    got = _calls_in(br)
    leak = {"search_kb", "answer_for_student", "_ask", "ask", "run", "classify",
            "get_conversation", "peek_pending", "take_pending"} & got
    return ({"extract_deterministic", "_material_card", "_material_answer"} <= got) and not leak


def _holding_card_calls(body):
    """`_holding_card = any(f(session_id) for f in (…))` 括号里那份**名单**（都是 Name，不是 Call）。"""
    for n in ast.walk(body):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "_holding_card":
                    return sorted({x.id for x in ast.walk(n.value) if isinstance(x, ast.Name)})
    return []


def main():
    global code
    print("\n" + "=" * 60)
    print("test_71_material_batch：#43 粘贴材料 → 批量提取 → 多选确认 → 系统写")
    print("=" * 60)
    for fn, name in ((batch_a_extract, "A 批（判据与提取）"),
                     (batch_b_end_to_end, "B 批（端到端）"),
                     (batch_c_shape, "C 批（形状）"),
                     (batch_d_content_ask, "D 批（『是什么』排除）")):
        rc = fn().summary(name)
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
