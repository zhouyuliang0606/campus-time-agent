"""加课 / 加待办 / 会话历史 / 清理聊天记录：四条"说了却看不到结果"的链路回归护栏。

为什么单独成一批？
    学生报过一次：「跟客服说了加课、加代办，回复已经加上，但日程里啥都没有，
    刷新也没有，聊天记录也没了」。

    查下来根因有三个，且全都是"把能由系统算死的事交给了模型和路由"：
      1. 模块路由会把同一句话甩到 planner / schedule / admin 任意一个，
         而写待办的工具只有 planner 有 → 时灵时不灵；
      2. 配了大模型密钥后跑的是真模型，模型最爱说"已经加上了"，其实一个字没写；
      3. 学生端只往 sessions.json 里写、从不读回来 → 刷新即失忆。

    这一批把前三条链路钉死：系统算出提案（不写库）→ 学生点头 → 系统写入 → 前端能刷新看到。
    第四批（清理聊天记录）说的是同一类事的另一头——删数据只能由学生点按钮触发后端，
    AI 既调不到、也无权替学生决定。两条合起来："写了要看得见，删了要真删掉"。
"""
import datetime
import json as _json
import os
import pathlib

from _e2e_env import demo_courses_from_json
from _harness import Checker, make_client, sandbox, title

code = 0


def seed_timetable():
    """把一份**干净的**演示周表播种进沙箱（人话：每次都从同一张表出发）。

    为什么不再"复制真实 app/data 里的那份"：沙箱本来就是复制真实数据，
    可真实数据也会被别的脚本改（旧版 e2e 就往里写过好几轮测试待办），
    复制一份脏的进去，等于把偶发失败写进了测试里——
    上一批明明全过、单独重跑就挂三条，根因就在这儿。

    改从 courses.json（演示数据的源头）还原，谁动过真实文件都不影响这一批。
    """
    from app.store import get_timetable, save_timetable
    save_timetable(demo_courses_from_json())
    return len(get_timetable())


def test_add_todo_pipeline():
    """第一批：加待办——系统算出提案，学生点头才写。"""
    title("1. 加待办：系统算时间、出提案，确认后落库")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.store import list_todos
    from app.modules.planner import parse_add_todo, wants_add_todo

    with sandbox():
        client = make_client()
        base = seed_timetable()
        today = datetime.date.today().isoformat()
        n_before = len(list_todos(today))

        # —— ① 出提案这一轮：只出方案，不许碰数据 ——
        r = client.post("/api/chat", json={
            "message": f"帮我把今天的『复习线性代数』安排到 19:00 到 20:30",
            "module": "planner", "session_id": "add-todo-1",
        })
        d = r.json()
        c.check("/api/chat 返回 200", r.status_code == 200)
        opts = d.get("options") or []
        c.check("回了一张待办确认卡", any(o.get("kind") == "todo_add" for o in opts),
                _json.dumps(opts, ensure_ascii=False)[:80])
        c.check("卡片上是系统算出的时间",
                bool(opts) and opts[0].get("date") == today and opts[0].get("start") == "19:00",
                _json.dumps(opts[:1], ensure_ascii=False)[:120])
        c.check("标题从原话里抠出来了", bool(opts) and opts[0].get("title") == "复习线性代数",
                (opts[0].get("title") if opts else ""))
        c.check("出提案这一轮没写库", len(list_todos(today)) == n_before,
                f"待办 {n_before} → {len(list_todos(today))} 条")
        c.check("" "这一轮不许谎称已经加上了""",
                "已经加上" not in (d.get("answer") or ""))

        # —— ② 学生回一句「确认」：系统落库 ——
        r2 = client.post("/api/chat", json={
            "message": "确认", "module": "schedule", "session_id": "add-todo-1",
        })
        d2 = r2.json()
        c.check("回「确认」后落库到待办",
                any(t["title"] == "复习线性代数" for t in list_todos(today)),
                [t["title"] for t in list_todos(today)])
        c.check("落库时间就是系统算的那两个钟点",
                bool([t for t in list_todos(today)
                      if t["start"] == "19:00" and t["end"] == "20:30"]))
        c.check("回答里说清加了什么", "已加入日程" in (d2.get("answer") or ""),
                (d2.get("answer") or "")[:50])

        # —— ③ 不带 module（完全交给路由）也必须能写 ——
        r3 = client.post("/api/chat", json={
            "message": "明天上午 9 点到 10 点 加一个『晨读』", "session_id": "add-todo-2",
        })
        o3 = (r3.json().get("options") or [])
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        c.check("路由漫无目标也不影响出卡",
                any(o.get("kind") == "todo_add" and o.get("date") == tomorrow for o in o3),
                _json.dumps(o3, ensure_ascii=False)[:100])
        client.post("/api/chat", json={
            "message": "确认", "session_id": "add-todo-2",
        })
        c.check("走路由那条同样写进去了",
                any(t["title"] == "晨读" for t in list_todos(tomorrow)),
                [t["title"] for t in list_todos(tomorrow)])

        # —— ④ 点候选卡回发的「确认 日期 起-止 标题」也要认 ——
        #     这里不再靠 planner 真去查空档（完整演示周表下最近三天可能确实挤不下），
        #     而是直接把"上一轮出过的候选卡"摆进暂存，专测"认出并写入"这一步。
        from app.agent.pending import save_pending
        day4 = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        save_pending("add-todo-3", [{
            "kind": "todo_pick", "title": "看论文", "date": day4, "weekday": "周六",
            "start": "09:00", "end": "10:30", "minutes": 90,
        }])
        picked = f"确认 {day4} 09:00-10:30 看论文"
        r5 = client.post("/api/chat", json={
            "message": picked, "module": "planner", "session_id": "add-todo-3",
        })
        c.check("点候选卡回发的那句话被系统认出并写入",
                any(t["title"] == "看论文" for t in list_todos(day4)),
                (r5.json().get("answer") or "")[:60])
        c.check("写完之后那张候选卡作废（不会写两遍）",
                peek_pending("add-todo-3") is None)

        # —— ⑤ 判定不许误伤：课表类、问句、闲聊都不算加待办 ——
        c.check("「周一加一节体育」不算加待办（那是加课）",
                not wants_add_todo("周一再加一节『体育』，体育馆，晚上 19:00 到 20:40"))
        c.check("「我的周课表是什么样」不算", not wants_add_todo("我的周课表是什么样"))
        c.check("「帮我看看周一第一节是什么课」不算", not wants_add_todo("帮我看看周一第一节是什么课"))
        c.check("空消息不算", not wants_add_todo(""))
        c.check("解析不出日期时刻时老实返回 None",
                parse_add_todo("帮我安排一下复习线性代数") is None)
    return c.summary("第一批（加待办确定性链路）")


def test_add_course_pipeline():
    """第二批：加课——系统算出新课表，学生点头才写。"""
    title("2. 加课：系统算新课表，确认后落库")
    c = Checker()
    from app.store import get_timetable, list_todos, save_timetable
    from app.modules.planner import wants_add_course, parse_add_course

    with sandbox():
        client = make_client()
        base = seed_timetable()

        # —— ① 出提案不写库 ——
        r = client.post("/api/chat", json={
            # 挑一个完整演示周表里确实空着的时段（周四下午16点后），
            # 免得撞课被拦——撞课是另一条断言在管，这里要测的是"出卡 + 落库"本身
            "message": "周四再加一节『马克思主义原理』，教一-101，16:00 到 17:40",
            "module": "planner", "session_id": "add-course-1",
        })
        d = r.json()
        opts = d.get("options") or []
        c.check("回了一张加课确认卡", any(o.get("kind") == "timetable_change" for o in opts),
                _json.dumps(opts, ensure_ascii=False)[:100])
        c.check("出提案这一周表没动", len(get_timetable()) == base, f"{base} → {len(get_timetable())}")
        c.check("" "出提案这一轮不许谎称已加""",
                "已经加上" not in (d.get("answer") or "")
                and "已经加好" not in (d.get("answer") or ""))

        # —— ② 学生点卡片 → 前端调 /api/timetable/apply → 系统写入 ——
        card = next((o for o in opts if o.get("kind") == "timetable_change"), {})
        ra = client.post("/api/timetable/apply", json={
            "courses": card.get("courses"), "session_id": "add-course-1",
        })
        da = ra.json()
        c.check("/api/timetable/apply 写入成功", da.get("ok") is True, str(da)[:80])
        c.check("周表真的多了一门课", len(get_timetable()) == base + 1,
                f"{base} → {len(get_timetable())}")
        c.check("新课就在学生说的那个时段",
                any(x["day"] == 4 and x["start"] == "16:00" and x["course"] == "马克思主义原理"
                    for x in get_timetable()),
                [f"{x['day']}{x['start']}{x['course']}" for x in get_timetable()][-4:])
        # 顺手看一眼地点：课表数据里曾经留过一条"没写地点"的课，
        # 那条不是演示数据，是某次写入没把地点带上留下的。写进去就该带上。
        c.check("新课的上课地点也一起写进去了",
                any(x["day"] == 4 and x["start"] == "16:00" and x.get("location") == "教一-101"
                    for x in get_timetable()),
                [x.get("location") for x in get_timetable()
                 if x["day"] == 4 and x["start"] == "16:00"])
        n_after_apply = len(get_timetable())

        # —— ③ 没说星期：系统追问，不交给模型妄加 ——
        r2 = client.post("/api/chat", json={
            "message": "帮我加一节毛概课，10:00 到 11:40",
            "module": "planner", "session_id": "add-course-2",
        })
        d2 = r2.json()
        c.check("没说星期时由系统追问（不许交回模型）", "星期" in (d2.get("answer") or ""),
                (d2.get("answer") or "")[:70])
        c.check("追问这一轮没出卡也没写库",
                not (d2.get("options") or []) and len(get_timetable()) == n_after_apply,
                f"{n_after_apply} → {len(get_timetable())} 门")
        c.check("" "追问里不许出现「已经加上」这种谎话""",
                "已经加上" not in (d2.get("answer") or ""))

        # —— ④ 判定不该误伤 ——
        c.check("" "「帮我把今天的『X』安排到 19:00」不算加课""",
                not wants_add_course("帮我把今天的『复习线性代数』安排到 19:00 到 20:30"))
        c.check("" "「周一的高数几点上课」不算加课""",
                not wants_add_course("周一的高数几点上课"))
        c.check("" "「周末有什么安排吗」不算加课""", not wants_add_course("周末有什么安排吗"))
        c.check("加课确实能解析出新课表", parse_add_course(
            "周一再加一节『体育』，体育馆，晚上 19:00 到 20:40") is not None)
    return c.summary("第二批（加课确定性链路）")


def test_conversation_history():
    """第三批：会话历史——学生刷新页面，聊过的话还得在。"""
    title("3. 会话历史：刷新后聊天记录不丢")
    c = Checker()
    from app.store import append_conversation, clear_conversation, get_conversation

    with sandbox():
        client = make_client()
        clear_conversation("hist-a")
        append_conversation("hist-a", "user", "帮我看看这周的空档")
        append_conversation("hist-a", "assistant", "周六整天都空着")

        r = client.get("/api/conversation/hist-a")
        c.check("/api/conversation/{sid} 返回 200", r.status_code == 200)
        msgs = r.json().get("messages") or []
        c.check("历史里两条都在", len(msgs) == 2, f"{len(msgs)} 条")
        c.check("内容和顺序没乱",
                bool(msgs) and msgs[0]["role"] == "user" and msgs[0]["content"] == "帮我看看这周的空档")

        empty = client.get("/api/conversation/hist-zzz").json()
        c.check("没聊过的会话返回空列表而不是报错", empty.get("messages") == [])

        # 越界参数不能把服务打崩（前端拼错一个字符也不该 500）
        c.check("非法 session_id 不炸",
                client.get("/api/conversation/" + "x" * 200).status_code == 200)
        clear_conversation("hist-a")
    return c.summary("第三批（会话历史恢复）")


def test_clear_conversation():
    """第四批：清理聊天记录——学生亲手点按钮，AI 碰不到。

    为什么要守这条：清空课表那条规矩已经被学生投诉过一回，聊天记录是同一类事——
    删数据只能由学生点按钮触发后端。AI 既调不到这个接口，也没权替学生决定
    这段对话要不要留着；它要是在回答里说"我帮你清了"，那纯属撒谎。

    顺手要钉住两件容易被绕过去的事：
      · 清完必须换新的 session id，不然学生一按 F5，restoreHistory 又把旧的捞回来，
        "清了跟没清一个样"；
      · 清掉这段对话时，挂在暂存里的那张确认卡要一起作废，
        否则学生手滑再回一句「确认」，系统照着一张早已无人认领的提案去写库。
    """
    title("4. 清理聊天记录：按钮触发后端，课表待办不受影响")
    c = Checker()
    from app.agent.pending import clear_pending, peek_pending, save_pending
    from app.store import (append_conversation, get_conversation, get_timetable,
                           list_todos, save_timetable)

    with sandbox():
        client = make_client()
        seed_timetable()
        today = datetime.date.today().isoformat()

        # —— ① 先把一段"聊过天"的局面造出来：3 条记录 + 1 张待确认提案 ——
        sid = "clear-chat-1"
        append_conversation(sid, "user", "帮我看下这周空档")
        append_conversation(sid, "assistant", "周六整天都空着")
        append_conversation(sid, "user", "那就周六加一节体育")
        save_pending(sid, [{"kind": "todo_add", "title": "体育", "date": today,
                            "start": "19:00", "end": "20:30"}])
        n_tt = len(get_timetable())
        n_todo = len(list_todos(today))
        c.check("前置：会话里确实有 3 条", len(get_conversation(sid)) == 3,
                f"{len(get_conversation(sid))} 条")
        c.check("前置：暂存里确实挂着一张待确认卡", peek_pending(sid) is not None)

        # —— ② 学生点【清空记录】：后端执行 ——
        r = client.post("/api/chat/reset", json={"session_id": sid})
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        c.check("/api/chat/reset 返回 200", r.status_code == 200, r.status_code)
        c.check("回执里报了清掉几条", d.get("removed") == 3, str(d))

        c.check("服务器上这段历史真的没了", get_conversation(sid) == [],
                f"还剩 {len(get_conversation(sid))} 条")
        c.check("读回接口也跟着空了",
                client.get(f"/api/conversation/{sid}").json().get("messages") == [])
        c.check("暂存里的那张确认卡一起作废",
                peek_pending(sid) is None, str(peek_pending(sid))[:60])

        # —— ③ 课表和待办一根汗毛都不许掉 ——
        c.check("课表没被顺手删掉", len(get_timetable()) == n_tt,
                f"{n_tt} → {len(get_timetable())} 门")
        c.check("待办没被顺手删掉", len(list_todos(today)) == n_todo,
                f"{n_todo} → {len(list_todos(today))} 条")

        # —— ④ 作废之后，学生再回一句「确认」也不能写出东西 ——
        #     这条是整批里最重要的一条：提案已经随会话一起没了，
        #     系统不许再照着一张没人认领的卡去写库。
        #
        #     顺带说明「确认」这句话本身会怎样：它被当成了新的一轮请求，
        #     planner 又算出来一张空档卡——这没关系，重要的是**旧的那张不见了**，
        #     系统不可能再拿它去写库。所以断言盯的是"旧提案没有被复活"，
        #     而不是"一张卡都不能有"（那样会把正常的追问也一起误伤）。
        r2 = client.post("/api/chat", json={
            "message": "确认", "module": "planner", "session_id": sid,
        })
        c.check("清空后再说【确认】没写出东西",
                not any(t["title"] == "体育" for t in list_todos(today)),
                [t["title"] for t in list_todos(today)])
        after = peek_pending(sid)
        old_card = [o for o in ((after or {}).get("options") or [])
                    if o.get("title") == "体育" or o.get("start") == "19:00"]
        c.check("被清空的那张卡没有复活",
                not old_card,
                _json.dumps(after, ensure_ascii=False)[:80])

        # —— ⑤ 别的表动得动，别把别的会话误伤了 ——
        append_conversation("clear-other", "user", "这条要留着")
        client.post("/api/chat/reset", json={"session_id": sid})
        c.check("清 A 会话不会连累 B 会话",
                any(m["content"] == "这条要留着" for m in get_conversation("clear-other")))

        # —— ⑥ 手滑参数不能把服务打崩 ——
        c.check("空 session_id 不炸",
                client.post("/api/chat/reset", json={"session_id": ""}).status_code == 200)
        r3 = client.post("/api/chat/reset", json={"session_id": ""})
        c.check("空 session_id 报删了 0 条", r3.json().get("removed") == 0, str(r3.json()))
        r4 = client.post("/api/chat/reset", json={"session_id": "x" * 200})
        c.check("超长 session_id 被忽略且不炸（返回 0 条）",
                r4.json().get("removed") == 0, str(r4.json())[:60])
        c.check("清一个从没聊过的会话也只是空操作",
                client.post("/api/chat/reset", json={"session_id": "never-chatted"}).json()
                .get("removed") == 0)

        clear_pending("clear-other")
    return c.summary("第四批（清理聊天记录）")


def test_add_todo_chinese_clock():
    """第五批：学生嘴里那个「下午两点到三点」——口语时间必须由系统接住。

    学生报障原话（截图）：「确认」之后管家回「搞定！游泳这条已经正式写进你的待办啦 ✅」，
    可日程里什么都没有——schedule 模块**连一个写入工具都没有**，那是纯嘴甜。

    查下来根因两层：
      1. 学生说的是「周二下午两点到三点游泳」，老解析器只认阿拉伯数字，
         「两点」在它眼里等于没给时间 → parse 失败 → 整句话掉回大模型；
      2. 掉回模型之后，学生回「确认」时暂存里空空如也，模型就继续撒谎。

    这一批照"删除课表"的规矩钉死三件事：中文报时／下午时段要能算出来；
    算不全就**系统追问**（绝不掉回模型）；确认落空时把学生的原话捞回来重新出提案。
    """
    import datetime as _dt

    title("5. 加待办（口语时间）：两点到三点、下午、追问、确认落空都要接住")
    c = Checker()
    from app.modules.planner import parse_add_todo, wants_add_todo, pick_todo_missing
    from app.modules.planner import find_free_slots
    from app.store import list_todos, append_conversation

    with sandbox():
        client = make_client()
        tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()

        # —— ① 解析层：中文报时 + 下午要换算成 24 小时制 ——
        p = parse_add_todo("帮我安排明天下午两点到三点游泳")
        c.check("「下午两点到三点」算得出 14:00-15:00",
                bool(p) and p.get("start") == "14:00" and p.get("end") == "15:00",
                _json.dumps(p, ensure_ascii=False) if p else "None")
        c.check("日期算成明天", bool(p) and p.get("date") == tomorrow,
                (p or {}).get("date", ""))
        c.check("标题抠成「游泳」（时间词要擦干净）", bool(p) and p.get("title") == "游泳",
                (p or {}).get("title", ""))
        p2 = parse_add_todo("安排明天晚上七点半跑步")
        c.check("「晚上七点半」算得出 19:30",
                bool(p2) and p2.get("start") == "19:30", (p2 or {}).get("start", ""))
        p3 = parse_add_todo("周五下午3点20写作业")
        c.check("「3点20」这种点+分也认", bool(p3) and p3.get("start") == "15:20",
                (p3 or {}).get("start", ""))
        c.check("「周一加一节体育」仍然归加课，不被待办抢走",
                not wants_add_todo("周一加一节体育，19:00-20:40，体育馆"))
        c.check("只给星期不给时间时，缺的是「时间」",
                pick_todo_missing("帮我安排周二的游泳") == "时间")

        # —— ①-bis 只有意图、没说时间的短句，也要系统接住（不能漏给模型）——
        #     实测漏出去的后果：模型回一张空档推荐表，学生再补一句时间又只是聊天，
        #     日程里始终什么都没有。
        c.check("「帮我安排游泳」算下单（由系统追问，不丢给模型）",
                wants_add_todo("帮我安排游泳"))
        c.check("「记一下交电费」也算", wants_add_todo("记一下交电费"))
        # 但"安排学习计划"是 schedule 模块的看家本领（找空档排计划），不许被待办抢走
        c.check("「帮我安排这周的学习计划」让给 schedule 模块",
                not wants_add_todo("帮我安排这周的学习计划"))
        c.check("「帮我安排一下课表」让给课表那条路",
                not wants_add_todo("帮我安排一下课表"))

        # —— ② 出提案 → 确认 → 真落库（走的是学生截图里那整条链路）——
        sid = "oral-todo-1"
        r = client.post("/api/chat", json={
            "message": "帮我安排明天下午两点到三点游泳",
            "module": "schedule",            # 学生当时正停在日程面板，显式锁了 schedule
            "session_id": sid,
        })
        d = r.json()
        opts = d.get("options") or []
        c.check("停在 schedule 模块也照样由系统出提案（不再掉给模型）",
                d.get("module") == "planner"
                and any(o.get("kind") == "todo_add" for o in opts),
                f"module={d.get('module')} opts={len(opts)}")
        c.check("提案上就是 14:00-15:00",
                bool(opts) and opts[0].get("start") == "14:00"
                and opts[0].get("end") == "15:00",
                _json.dumps(opts[:1], ensure_ascii=False)[:120])
        c.check("这一轮说的是「要不要」，不是「已经写进」",
                "要不要" in (d.get("answer") or "")
                and "已经写进" not in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])
        c.check("出提案没写库", not any(t["title"] == "游泳" for t in list_todos(tomorrow)),
                [t["title"] for t in list_todos(tomorrow)])

        r2 = client.post("/api/chat", json={"message": "确认", "session_id": sid})
        c.check("回一句「确认」就真落库了",
                any(t["title"] == "游泳" for t in list_todos(tomorrow)),
                [t["title"] for t in list_todos(tomorrow)])
        c.check("落库时间是 14:00-15:00",
                bool([t for t in list_todos(tomorrow)
                      if t["start"] == "14:00" and t["end"] == "15:00"]))
        c.check("回答说的是「已加入日程」而不是「搞定」",
                "已加入日程" in (r2.json().get("answer") or ""),
                (r2.json().get("answer") or "")[:50])

        # —— ③ 只说了"哪天"、没说"几点"：**先问"时间怎么定"，再摊空档** ——
        #     这一档的规格改过两次，三版都记在这儿（免得以后又翻回去）：
        #       · 第一版：系统替他**挑一个**时间、出一张单条确认条。方向对（被截屏投诉的
        #         原话是「没有帮我想时间，是我问了才说的」），但做法太死——学生原话
        #         「我定的太严了……由 ai 帮我去挑选合适时间，**进行列举**……由我打勾」。
        #       · 第二版：**列几个候选**，勾哪个算哪个，还能在「其他」里自填。
        #       · 第三版（这一批盯的）：**先出二选一卡**——学生原话
        #         「要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
        #          不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定）」。
        #         他选"我自己定"才摊候选；选"你帮我挑"就由系统规划一段。
        #     三版共有的铁律：不反问他"几点到几点"，也不硬凑一个时间。
        sid2 = "oral-todo-2"
        r3 = client.post("/api/chat", json={
            "message": "帮我安排周二的游泳", "module": "schedule", "session_id": sid2,
        })
        d3 = r3.json()
        o3 = d3.get("options") or []
        c.check("只说了哪天 → 先出『时间怎么定』的二选一卡（不问他几点）",
                any(o.get("kind") == "todo_mode" for o in o3),
                _json.dumps(o3[:1], ensure_ascii=False)[:120])
        c.check("二选一卡上把标题记下了（游泳）",
                bool(o3) and o3[0].get("title") == "游泳",
                (o3[0].get("title") if o3 else ""))
        c.check("话里把两条路都摊明白了（我自己定 / 你帮我挑）",
                "我自己定" in (d3.get("answer") or "")
                and "帮我挑" in (d3.get("answer") or ""),
                (d3.get("answer") or "")[:90])
        c.check("绝不把问题退回去问『几点到几点』",
                "几点到几点" not in (d3.get("answer") or ""),
                (d3.get("answer") or "")[:80])
        c.check("出二选一卡这一轮没写库（要学生点头）",
                not any(t["title"] == "游泳" for t in list_todos("2026-09-29")),
                [t["title"] for t in list_todos("2026-09-29")])

        # 他选「时间我自己定」→ 这才摊出那天的几段空档让他打勾
        r3c = client.post("/api/chat", json={"message": "我自己定", "session_id": sid2})
        d3c = r3c.json()
        o3c = d3c.get("options") or []
        _slots = (o3c[0].get("slots") if o3c else None) or []
        c.check("选了『我自己定』→ 才摊出候选时段卡",
                any(o.get("kind") == "todo_slots" for o in o3c),
                _json.dumps(o3c[:1], ensure_ascii=False)[:120])
        c.check("候选摊开好几段（不再是替他定死一个点）",
                len(_slots) >= 2, [s.get("label") for s in _slots])
        c.check("每一段都在周二、而且都是真实空档",
                bool(_slots) and all(
                    s.get("date") == "2026-09-29" and any(
                        f["start"] <= s["start"] and f["end"] >= s["end"]
                        for f in find_free_slots(s["date"], min_minutes=1))
                    for s in _slots),
                [f"{s.get('date')} {s.get('start')}-{s.get('end')}" for s in _slots])
        c.check("话里说明白『这几段都空着，你自己勾』",
                "空着" in (d3c.get("answer") or "")
                and "勾" in (d3c.get("answer") or ""),
                (d3c.get("answer") or "")[:60])
        c.check("并且留了『其他时间』自己写的活口（不是把问题退回去）",
                "其他" in (d3c.get("answer") or ""),
                (d3c.get("answer") or "")[:80])
        c.check("摊候选这一轮仍然没写库（要学生点头）",
                not any(t["title"] == "游泳" for t in list_todos("2026-09-29")),
                [t["title"] for t in list_todos("2026-09-29")])

        # 学生看了不满意，自己报了个点 → 换成他说的那个时间（以学生为准）
        r4 = client.post("/api/chat", json={
            "message": "下午两点到三点", "module": "schedule", "session_id": sid2,
        })
        o4 = r4.json().get("options") or []
        c.check("学生报了点就换成他说的（系统的推荐只是起步）",
                any(o.get("kind") == "todo_add" for o in o4),
                _json.dumps(o4[:1], ensure_ascii=False)[:120])
        c.check("标题仍是上一句的「游泳」（补充句没有标题，要沿用卡片上的）",
                bool(o4) and o4[0].get("title") == "游泳",
                (o4[0].get("title") if o4 else ""))
        c.check("时间换算成 14:00-15:00",
                bool(o4) and o4[0].get("start") == "14:00", (o4[0].get("start") if o4 else ""))
        c.check("学生自己点的时间不带 auto 标记",
                bool(o4) and not o4[0].get("auto"))

        # 连"哪天"都没说 → **也不反问哪天**，照样先出二选一卡；
        # 他选「你帮我挑」→ 由**系统把时间规划出来**（本轮的核心诉求：
        # 「核心是 ai 帮我安排时间，ai 去规划时间，然后这个加入代办」）。
        sid2b = "oral-todo-2b"
        r3b = client.post("/api/chat", json={
            "message": "帮我安排游泳", "module": "schedule", "session_id": sid2b,
        })
        d3b = r3b.json()
        o3b = d3b.get("options") or []
        c.check("连哪天都没说 → 也不反问，照样先给二选一卡",
                any(o.get("kind") == "todo_mode" for o in o3b),
                _json.dumps(o3b[:1], ensure_ascii=False)[:90])
        c.check("绝不把问题退回去问『几点到几点』或『哪一天』",
                "几点到几点" not in (d3b.get("answer") or "")
                and "哪一天" not in (d3b.get("answer") or ""),
                (d3b.get("answer") or "")[:80])
        c.check("话里已经把标题记住了（游泳），不是笼统地问",
                "游泳" in (d3b.get("answer") or ""),
                (d3b.get("answer") or "")[:70])

        r3b2 = client.post("/api/chat", json={"message": "你帮我挑", "session_id": sid2b})
        d3b2 = r3b2.json()
        o3b2 = d3b2.get("options") or []
        c.check("选『你帮我挑』→ 出**单条**（时间由系统规划好了，不再摊一堆让他挑）",
                bool(o3b2) and o3b2[0].get("kind") == "todo_add",
                _json.dumps(o3b2[:1], ensure_ascii=False)[:110])
        c.check("标了 auto（话术和卡片都要说清『这个点是我挑的』）",
                bool(o3b2) and o3b2[0].get("auto") is True)
        c.check("规划出来的那一段**真的空着**",
                bool(o3b2) and any(
                    f["start"] <= o3b2[0]["start"] and f["end"] >= o3b2[0]["end"]
                    for f in find_free_slots(o3b2[0]["date"], min_minutes=1)),
                f"{o3b2[0].get('date')} {o3b2[0].get('start')}-{o3b2[0].get('end')}"
                if o3b2 else "")
        c.check("并且说清『为什么排在这儿』（他看到自己没指定的时间会问凭什么）",
                bool(o3b2) and "空" in (o3b2[0].get("reason") or ""),
                (o3b2[0].get("reason") if o3b2 else "")[:80])
        c.check("回答里也把理由说了一遍",
                "空" in (d3b2.get("answer") or ""),
                (d3b2.get("answer") or "")[:90])
        c.check("规划完也还没写库", not any(
            t["title"] == "游泳" and t["date"] == (o3b2[0].get("date") if o3b2 else "")
            for t in list_todos()))

        # —— ④ 确认落空兜底：上一轮已经掉给模型撒过谎，学生照样回「确认」——
        #     把学生截图里的那段真实历史摆出来：学生原话 + 模型那句「搞定！已经正式写进」，
        #     暂存里什么都没有。此时回「确认」应该把原话捞回来重新出提案，而不是继续装。
        sid3 = "oral-todo-3"
        append_conversation(sid3, "user", "帮我安排下周二的游泳，下午两点到三点")
        append_conversation(sid3, "assistant",
                            "搞定！游泳这条已经正式写进你的待办啦 ✅ 刷新一下就能看到。")
        r5 = client.post("/api/chat", json={"message": "确认", "session_id": sid3})
        d5 = r5.json()
        o5 = d5.get("options") or []
        c.check("确认落空时把学生原话捞回来重新出提案",
                any(o.get("kind") == "todo_add" for o in o5),
                _json.dumps(o5[:1], ensure_ascii=False)[:120])
        c.check("捞回来的提案标题是「游泳」、时间是 14:00-15:00",
                bool(o5) and o5[0].get("title") == "游泳"
                and o5[0].get("start") == "14:00",
                _json.dumps(o5[:1], ensure_ascii=False)[:120])
        day5 = (o5[0].get("date") if o5 else "")
        c.check("这一轮还是没写库（要等学生真的点头）",
                not any(t["title"] == "游泳" and t["date"] == day5
                        for t in list_todos(day5)),
                [t["title"] + "@" + t["date"] for t in list_todos(day5)])

        r6 = client.post("/api/chat", json={"message": "确认", "session_id": sid3})
        c.check("再确认一次就真写进去了",
                any(t["title"] == "游泳" and t["date"] == day5
                    for t in list_todos(day5)),
                [t["title"] + "@" + t["date"] for t in list_todos(day5)])

        # —— ⑥ 写成功了就别再挂确认条：学生多回一句「好的」不该写出第二条 ——
        n_written = len([t for t in list_todos(day5)
                         if t["title"] == "游泳" and t["date"] == day5])
        r7 = client.post("/api/chat", json={"message": "好的", "session_id": sid3})
        c.check("已经写成功过，就不再把原话捞出来重新出提案",
                not (r7.json().get("options") or []),
                (r7.json().get("answer") or "")[:50])
        client.post("/api/chat", json={"message": "确认", "session_id": sid3})
        c.check("所以也不会重复写第二条",
                len([t for t in list_todos(day5)
                     if t["title"] == "游泳" and t["date"] == day5]) == n_written,
                [t["title"] + "@" + t["date"] for t in list_todos(day5)])
    return c.summary("第五批（加待办·口语时间 / 追问 / 确认落空）")


def test_schedule_proposal_tool():
    """第六批：日程面板的管家只在文字里写「提案」，界面上连个确认按钮都没有。

    学生报障原话（截图）：「没有确认按钮」。截图里学生停在日程面板，管家回的是——
        好，那我按这个出个提案：
        - 任务：健身
        - 时间：周一 16:30~18:00
        - 范围：本周
        提案这就发给你，点一下【确认】就入库了！
    文案写得像提案已经发了，可界面上**连一个按钮都没有**。

    根因：课表规划模块此前只有"查课 / 找空档 / 排计划"三个**只读**工具。
    它压根没有"出一张确认条"的手脚，找完空档只能在文字里把方案念一遍。
    （跟上一批是同一个病根的两张脸：一个是"嘴上说写好了"，一个是"嘴上说提案发了"。）

    这一批钉三件事：
      ① 模块手里有 propose_todo_tool 了，调成功会回 __proposal__，引擎据此挂确认条；
      ② 模块设定里写死了"没调工具就不许说提案已发"；
      ③ 万一模型还是不调工具，学生回「可以」时系统能把它那句方案捞回来重新挂条。
    """
    title("6. 日程面板提案：工具出条 / 提示层兜底 / 文字方案捞回来")
    c = Checker()
    from app.agent.engine import AgentEngine
    from app.modules.planner import parse_todo_from_reply
    from app.modules import schedule as sched
    from app.store import list_todos, append_conversation

    # 截图里管家那段原文，逐字抄下来当输入——测试要盯的就是这个真实场景。
    butler_text = (
        "好，那我按这个出个提案：\n"
        "- 任务：健身\n"
        "- 时间：周一 16:30~18:00\n"
        "- 范围：本周\n"
        "提案这就发给你，点一下【确认】就入库了！"
    )

    # —— ① 工具得在架子上：注册了、说明书说清了"不写库" ——
    tools = sched.build_tools()
    c.check("课表规划模块多了 propose_todo_tool（之前只有三个只读工具）",
            "propose_todo_tool" in tools, list(tools))
    t = tools.get("propose_todo_tool")
    c.check("入参里 title 是必填，还能分着给 date/start/end",
            bool(t) and t.parameters.get("required") == ["title"]
            and {"when", "date", "start", "end"} <= set(t.parameters["properties"]),
            _json.dumps(t.parameters, ensure_ascii=False)[:110] if t else "None")
    c.check("说明书里说清『只出提案，不写库』",
            bool(t) and "只出提案" in t.description and "不写库" in t.description)
    c.check("系统设定里点名要用这个工具出确认条",
            "propose_todo_tool" in sched.SYSTEM_PROMPT)
    c.check("系统设定里禁止『没调工具就说提案已发』",
            "没调用工具就别说" in sched.SYSTEM_PROMPT
            and "严禁" in sched.SYSTEM_PROMPT)

    # —— ② 工具本身：吃口语时间、给 __proposal__；解析不出来就给提示让模型补 ——
    got = _json.loads(sched.propose_todo_tool("健身", when="周一 16:30~18:00"))
    prop = got.get("__proposal__") or {}
    c.check("调一次工具就回一张能渲染的 todo_add 提案",
            prop.get("kind") == "todo_add", _json.dumps(prop, ensure_ascii=False)[:110])
    c.check("提案上是 16:30-18:00、星期一是对的",
            prop.get("start") == "16:30" and prop.get("end") == "18:00"
            and prop.get("weekday") == "周一",
            prop.get("summary", ""))
    c.check("工具回来的话里明说『禁止说已经写好了』",
            "禁止说已经写好了" in (got.get("human") or ""), (got.get("human") or "")[:60])
    oral = _json.loads(sched.propose_todo_tool("游泳", when="明天下午两点到三点"))
    c.check("口语时间也吃：「下午两点到三点」→ 14:00-15:00",
            (oral.get("__proposal__") or {}).get("start") == "14:00"
            and (oral.get("__proposal__") or {}).get("end") == "15:00",
            (oral.get("__proposal__") or {}).get("summary", ""))
    bad = _json.loads(sched.propose_todo_tool("健身", when="随便吧"))
    c.check("解析不出来就返回 error + hint（让模型补参数重试，绝不瞎凑一个时间）",
            "error" in bad and "hint" in bad and "__proposal__" not in bad,
            list(bad))

    # —— ③ 引擎那一段：工具结果带 __proposal__ 就要变成确认条 ——
    #     这里塞一个"假模型"：第一轮说要调 propose_todo_tool，第二轮给句收尾话。
    #     不用联网、不用密钥，也能把"调工具 → 收提案 → 存暂存"这条线走通。
    class _FakeLLM:
        """假模型（人话：照着剧本回话，用来验引擎的接线，不验模型的智商）。"""

        def __init__(self):
            self.rounds = 0
            self.seen_tools: list[str] = []

        async def chat(self, messages, tools=None, tool_choice="auto"):
            self.rounds += 1
            # 顺手记下引擎有没有把工具菜单递给模型
            self.seen_tools = [x["function"]["name"] for x in (tools or [])]
            if self.rounds == 1:
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "propose_todo_tool",
                            "arguments": _json.dumps(
                                {"title": "健身", "when": "周一 16:30~18:00"},
                                ensure_ascii=False),
                        },
                    }],
                }
            return {"role": "assistant", "content": "确认条已经挂出来了，点一下【确认加入】就好。"}

    import asyncio as _asyncio
    fake = _FakeLLM()
    eng = AgentEngine(llm=fake, tools=tools, system_prompt=sched.SYSTEM_PROMPT,
                      session_id="sched-tool-1")
    res = _asyncio.run(eng.run("周一 16:30-18:00 我要健身，帮我排一下"))
    c.check("工具菜单里带给模型了（不然它想调也调不到）",
            "propose_todo_tool" in fake.seen_tools, fake.seen_tools)
    eng_opts = res.get("options") or []
    c.check("工具结果里的 __proposal__ 被引擎收成了确认条",
            any(o.get("kind") == "todo_add" for o in eng_opts),
            _json.dumps(eng_opts[:1], ensure_ascii=False)[:110])
    c.check("带上去的就是《健身》那条",
            bool(eng_opts) and eng_opts[0].get("title") == "健身",
            (eng_opts[0].get("summary") if eng_opts else ""))
    c.check("awaiting_choice 也置上了（前端据此知道有东西要确认）",
            res.get("awaiting_choice") is True)

    # —— ④ 捞方案这一步：截图那段原文要能捞出来，闲聊不许捞 ——
    p = parse_todo_from_reply(butler_text)
    c.check("从管家那句话里能捞出「健身 / 周一 / 16:30-18:00」",
            bool(p) and p.get("title") == "健身" and p.get("start") == "16:30"
            and p.get("end") == "18:00" and p.get("weekday") == "周一",
            _json.dumps(p, ensure_ascii=False) if p else "None")
    c.check("闲聊里不瞎猜（没有固定格式就返回 None）",
            parse_todo_from_reply("今天天气不错，要不要去操场跑两圈？") is None)
    c.check("只顾着说『点确认就入库』、没给任务时间，也不猜",
            parse_todo_from_reply("提案这就发给你，点一下【确认】就入库了！") is None)

    # —— ⑤ 整条链路（走接口）：模型没调工具、只在文字里写方案，学生回「可以」——
    with sandbox():
        client = make_client()
        seed_timetable()
        sid = "sched-text-proposal"
        append_conversation(sid, "user", "帮我安排一下健身")
        append_conversation(sid, "assistant", butler_text)

        r = client.post("/api/chat", json={"message": "可以", "session_id": sid})
        d = r.json()
        opts = d.get("options") or []
        c.check("管家只在文字里写方案、学生回「可以」时，系统替它把确认条挂出来",
                any(o.get("kind") == "todo_add" for o in opts),
                _json.dumps(opts[:1], ensure_ascii=False)[:110])
        c.check("捞出来的正是它写的那条：健身 16:30-18:00",
                bool(opts) and opts[0].get("title") == "健身"
                and opts[0].get("start") == "16:30" and opts[0].get("end") == "18:00",
                (opts[0].get("summary") if opts else "无"))
        c.check("这一轮还是没写库（要等学生真的点头）",
                not any(t["title"] == "健身" for t in list_todos()), 
                [(t["title"], t["date"]) for t in list_todos()])

        day = (opts[0].get("date") if opts else "")
        r2 = client.post("/api/chat", json={"message": "确认", "session_id": sid})
        c.check("再回一句「确认」就真写进日程了",
                any(t["title"] == "健身" and t["date"] == day for t in list_todos(day)),
                [(t["title"], t["start"]) for t in list_todos(day)])
        c.check("回答说的是「已加入日程」而不是「搞定」",
                "已加入日程" in (r2.json().get("answer") or ""),
                (r2.json().get("answer") or "")[:50])

        # 已经在文字里瞎承诺过的情形，不能再被同一个兜底重复挂条（多挂一次=可能多写一条）
        r3 = client.post("/api/chat", json={"message": "好的", "session_id": sid})
        c.check("写成功之后，再回「好的」不会又挂出一条",
                not (r3.json().get("options") or []),
                (r3.json().get("answer") or "")[:50])
    return c.summary("第六批（日程面板提案：工具出条 / 捞回文字方案）")


def test_remove_todo_pipeline():
    """第七批：删待办——先列清楚，再动手，学生点头才算数。

    这一批把规格里的三条走法和三条红线都钉住：
      1) 点名了唯一一条 → 出确认条（把那条**原样念一遍**），学生没点头前一个字节都不删；
      2) 没说清是哪条（"删掉明天那条"而那天有好几条）→ **列清单问是哪一条，列完就停**，
         绝不挑一条删掉；
      3) 说的是课（"去掉周二的高数"）→ 归课表那条路，别拿待办去套。
    红线：没点头不许调删除、不许只说"要确认"却不列清单、删除不可逆所以宁可多问一句。
    """
    import datetime as _dt

    title("7. 删待办：原样念一遍 / 列清单不猜 / 说的是课就归课表")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.modules.planner import (
        resolve_todo_remove, wants_remove_todo, wants_remove_todo_loose,
    )
    from app.modules import planner as pl
    from app.store import append_conversation, add_todo, list_todos

    with sandbox():
        client = make_client()
        seed_timetable()
        d_near = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()   # 同一天两条
        d_far = (_dt.date.today() + _dt.timedelta(days=5)).isoformat()    # 同一天一条
        add_todo("游泳", d_near, "14:00", "15:00")
        add_todo("交电费", d_near, "19:00", "19:30")
        add_todo("晨读", d_far, "07:00", "07:30")

        # —— ① 工具箱：删待办只剩一个**只读**提案工具 ——
        tools = pl.build_tools()
        c.check("工具箱里有 propose_todo_remove（只出确认条）",
                "propose_todo_remove" in tools, list(tools))
        c.check("真能删的 remove_todo 已经撤掉（模型没有删除接口）",
                "remove_todo" not in tools)
        desc = tools["propose_todo_remove"].description
        c.check("说明书里写清『一个字节都不删』", "一个字节都不删" in desc)
        c.check("说明书里写清命中多条要列清单、不许猜",
                "列" in desc and "不许猜" in desc)

        # —— ② 判定开关：待办归待办，课归课 ——
        c.check("「把周二那条游泳的待办删掉」算删待办",
                wants_remove_todo("把周二那条游泳的待办删掉"))
        c.check("「取消交电费」算删待办", wants_remove_todo("取消交电费"))
        c.check("「删掉明天那条」算删待办", wants_remove_todo("删掉明天那条"))
        c.check("「去掉周二的高数」**不算**删待办（那是课）",
                not wants_remove_todo("去掉周二的高数"))
        c.check("「删掉课表全部课程」不算删待办（那是清空）",
                not wants_remove_todo("删掉课表全部课程"))
        # 没写"待办"二字但精准对上一条待办时，走宽松那一档
        c.check("「把交电费删了」精准对上 → 宽松档认它",
                wants_remove_todo_loose("把交电费删了"))
        c.check("只沾边的（「高数」对上「复习高数」）宽松档不认",
                not wants_remove_todo_loose("删掉周三的高数"))

        # —— ③ 解析层：唯一 = ok；多条 = many（绝不替学生挑）；没有 = empty ——
        r = resolve_todo_remove("把交电费删掉")
        c.check("点名唯一一条 → 解析成一张待删提案",
                r["status"] == "ok" and r["proposal"]["title"] == "交电费",
                _json.dumps(r.get("proposal"), ensure_ascii=False)[:100])
        c.check("提案里带上了那条的编号（系统靠它落库）",
                bool(r["proposal"].get("todo_id")))
        c.check("提案里的 summary 是『标题｜日期（周几）时段』的模样",
                "交电费" in r["proposal"]["summary"] and "19:00-19:30" in r["proposal"]["summary"],
                r["proposal"]["summary"])
        c.check("那天有两条、又没点名 → many（要去列清单）",
                resolve_todo_remove(f"删掉{d_near}的待办")["status"] == "many",
                [x["title"] for x in resolve_todo_remove(f"删掉{d_near}的待办")["todos"]])
        c.check("没找到就是 empty，绝不硬凑一条",
                resolve_todo_remove("删掉读研申请")["status"] == "empty")

        # —— ④ 出确认条：原样念一遍、不写库 ——
        sid = "rm-1"
        r1 = client.post("/api/chat", json={
            "message": "把游泳的待办删掉", "module": "planner", "session_id": sid,
        })
        d1 = r1.json()
        opts = d1.get("options") or []
        c.check("回了一张删待办确认条",
                any(o.get("kind") == "todo_remove" for o in opts),
                _json.dumps(opts[:1], ensure_ascii=False)[:110])
        c.check("确认条上把那条**原样念了一遍**（标题 + 日期 + 时段）",
                bool(opts) and opts[0].get("title") == "游泳"
                and opts[0].get("date") == d_near
                and opts[0].get("start") == "14:00" and opts[0].get("end") == "15:00",
                (opts[0].get("summary") if opts else "无"))
        c.check("话里说的是「点确认删除我才删」，不是「已经删了」",
                "确认删除" in (d1.get("answer") or "")
                and "已经删" not in (d1.get("answer") or ""),
                (d1.get("answer") or "")[:60])
        c.check("**学生还没点头**：待办一条都没少",
                any(t["title"] == "游泳" for t in list_todos(d_near)),
                [t["title"] for t in list_todos(d_near)])
        c.check("确认条进了暂存（学生回「确认」时系统找得到它）",
                any(o.get("kind") == "todo_remove"
                    for o in (peek_pending(sid) or {}).get("options") or []))
        c.check("删待办提案没有被记成『候选时段』（记错会把删除变成新增）",
                not any(o.get("kind") == "todo_pick"
                        for o in (peek_pending(sid) or {}).get("options") or []))

        # —— ⑤ 学生回一句「确认」→ 系统真删（只有这一条路能删到库） ——
        r2 = client.post("/api/chat", json={"message": "确认", "session_id": sid})
        c.check("回「确认」之后那条真没了",
                not any(t["title"] == "游泳" for t in list_todos(d_near)),
                [t["title"] for t in list_todos(d_near)])
        c.check("同一天的另一条没被误删",
                any(t["title"] == "交电费" for t in list_todos(d_near)))
        c.check("回答说的是「已删除」而不是「搞定」",
                "已删除" in (r2.json().get("answer") or ""),
                (r2.json().get("answer") or "")[:50])
        c.check("删除回执进了会话历史（刷新后还看得见）",
                any("已删除待办" in (m.get("content") or "")
                    for m in __import__("app.store", fromlist=["get_conversation"])
                    .get_conversation(sid)))

        # 删完之后再回一句「好的」，不该把确认条又挂一遍（挂一次就可能再删一条）
        r3 = client.post("/api/chat", json={"message": "好的", "session_id": sid})
        c.check("删成功之后不再重复挂确认条",
                not (r3.json().get("options") or []),
                (r3.json().get("answer") or "")[:50])

        # —— ⑥ 没说清是哪条：列清单，问是哪一条，列完就停，一条都不删 ——
        sid2 = "rm-2"
        r4 = client.post("/api/chat", json={
            "message": f"删掉 {d_near} 的待办", "module": "planner", "session_id": sid2,
        })
        d4 = r4.json()
        ans4 = d4.get("answer") or ""
        c.check("命中好几条时**不出确认条**（出条就等于替学生挑了）",
                not (d4.get("options") or []),
                _json.dumps(d4.get("options"), ensure_ascii=False)[:80])
        c.check("把那天剩下的待办**列了出来**（这就是红线②要的清单）",
                "交电费" in ans4 and "19:00-19:30" in ans4, ans4.replace("\n", " / ")[:120])
        c.check("列完就问「是哪一条」", "哪一条" in ans4, ans4.replace("\n", " / ")[:60])
        c.check("列了清单也没删任何东西", len(list_todos(d_near)) == 2,
                [t["title"] for t in list_todos(d_near)])

        # 学生用序号点名（"第二条"）→ 系统接得住，出确认条
        r5 = client.post("/api/chat", json={"message": "第二条", "session_id": sid2})
        o5 = r5.json().get("options") or []
        c.check("学生回序号点名，系统接得住并出确认条",
                any(o.get("kind") == "todo_remove" for o in o5),
                _json.dumps(o5[:1], ensure_ascii=False)[:110])
        c.check("点中的就是清单里第二条（交电费）",
                bool(o5) and o5[0].get("title") == "交电费",
                (o5[0].get("title") if o5 else "无"))

        # 另一种情况：清单还在，学生却直接回「确认」→ 不猜、不删，把清单再念一遍
        sid5 = "rm-5"
        client.post("/api/chat", json={"message": f"删掉 {d_far} 的待办", "session_id": sid5})
        add_todo("跑步", d_far, "18:00", "18:30")
        client.post("/api/chat", json={"message": f"删掉 {d_far} 的待办", "session_id": sid5})
        r6 = client.post("/api/chat", json={"message": "确认", "session_id": sid5})
        c.check("还没点名就回「确认」→ 一条都不删（宁可多问一句）",
                len(list_todos(d_far)) == 2,
                [t["title"] for t in list_todos(d_far)])
        c.check("并且明确告诉学生『一条都没删』",
                "一条都没删" in (r6.json().get("answer") or ""),
                (r6.json().get("answer") or "")[:60])

        # —— ⑦ 说的是课：归课表那条路，别拿待办去套 ——
        #     演示课表里周二 08:00 有「线性代数」，拿它当靶子。
        sid7 = "rm-7"
        before7 = len(pl.get_timetable())
        r7 = client.post("/api/chat", json={
            "message": "去掉周二的线性代数", "module": "planner", "session_id": sid7,
        })
        d7 = r7.json()
        kinds7 = [o.get("kind") for o in (d7.get("options") or [])]
        c.check("「去掉周二的线性代数」走的是**删课**提案，不是删待办",
                "timetable_change" in kinds7 and "todo_remove" not in kinds7,
                _json.dumps(kinds7, ensure_ascii=False))
        c.check("周表当时没被动（要学生点确认才写）",
                len(pl.get_timetable()) == before7)
        c.check("学生的待办一条都没被牵连",
                len(list_todos(d_near)) == 2,
                [t["title"] for t in list_todos(d_near)])

        # —— ⑧ 确认落空：上一轮出过条、暂存没了（比如刷新过），学生再回「确认」 ——
        sid8 = "rm-8"
        append_conversation(sid8, "user", "把游泳的待办删掉")
        append_conversation(sid8, "assistant", "已生成删待办提案：游泳｜口径")
        add_todo("游泳", d_far, "16:00", "17:00")
        r8 = client.post("/api/chat", json={"message": "确认", "session_id": sid8})
        o8 = r8.json().get("options") or []
        c.check("确认落空时把学生原话捞回来、重新挂出确认条",
                any(o.get("kind") == "todo_remove" for o in o8),
                _json.dumps(o8[:1], ensure_ascii=False)[:110])
        c.check("这一轮仍然没删（还是要等学生真的点头）",
                any(t["title"] == "游泳" for t in list_todos(d_far)),
                [t["title"] for t in list_todos(d_far)])
    return c.summary("第七批（删待办：原样念一遍 / 列清单不猜 / 课归课）")


def test_proactive_time_and_confirm():
    """第八批：学生只给"哪天"时，**时间由系统算**；回「确认」必须真挂出条、真写进去。

    这一批盯的是学生截屏投诉的两句话：
      · 「**没有帮我想时间，是我问了才说的**」——
        学生说「那我加一个健身在周四」，系统不该反问他"几点到几点"，
        而要自己查空档、挑一段排上、把确认条挂出来，并说清"这是我挑的，不合适你改"。
      · 「**没有确认**」——
        管家只在文字里写「- 任务：健身 - 时间：周四 15:40~17:10 点【确认】就入库了」，
        界面上连按钮都没有；学生回「确认」，系统也不知道他在确认什么。
    所以这里两件事一起钉：
      ① 只给"哪天" → 出**候选卡（todo_slots）**：那天里几段真实空档摊开来让学生打勾，
         并且留一个「其他时间」的口子让他自己写；候选一律落在真实空档里，绝不排到课上；
      ② 管家只写了文字方案 → 学生回「确认」时，系统替他**算出时间**把条挂出来；
      ③ 学生报了个点/换了一天 → 沿用原卡换时间重出条（那句"你说个点"不能是空炮）。

    ⚠️ ① 的形态改过一次：上一版是"系统替他挑一个点、出一张单条确认条"，被学生驳回——
      原话「我定的太严了，你改一下，由 ai 帮我去挑选合适时间，**进行列举**，
      采用和删除课表时同样的弹框，内容变成那几个时间的选择或者其他，**由我打勾**」。
    """
    import datetime as _dt

    title("8. 只给「哪天」：时间由系统算；回「确认」必出条、必落库")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.modules import planner as pl
    from app.modules.planner import (
        auto_todo_proposal, build_scheduling_tools, pick_free_slot,
        propose_todo_tool, retime_todo_proposal,
    )
    from app.store import append_conversation, list_todos

    with sandbox():
        client = make_client()
        seed_timetable()
        # 挑一个"有课、也有空档"的周四：空档里最长的那段应该被挑中
        thu = (_dt.date.today() + _dt.timedelta(
            days=(4 - _dt.date.today().isoweekday()) % 7 or 7)).isoformat()

        # —— ① 工具层：只给 date、不给 start，也必须出条（时间自己挑）——
        raw = _json.loads(propose_todo_tool(title="健身", date=thu))
        prop = raw.get("__proposal__") or {}
        c.check("propose_todo_tool 只给日期也给得出确认条（不再报'缺开始时间'）",
                prop.get("kind") == "todo_add", _json.dumps(raw, ensure_ascii=False)[:110])
        c.check("挑出来的时间落在真实空档里",
                bool(prop) and any(s["start"] == prop.get("start")
                                   for s in pl.find_free_slots(thu)),
                f"{prop.get('start')}-{prop.get('end')}")
        c.check("标了 auto，好让话术说清'这个点是我挑的'", prop.get("auto") is True)
        c.check("说明书里写明『只给哪天也行』",
                "空档" in (build_scheduling_tools()["propose_todo_tool"].description or ""))

        # —— ② 任何模块都拿得到这组只读工具（路由把话分给谁都出得了条）——
        sched = build_scheduling_tools()
        c.check("这一组里备齐了『查空档 + 出条』四件套",
                {"find_free_slots", "propose_todo_tool", "propose_slots",
                 "list_day_todos"} <= set(sched),
                sorted(sched))
        c.check("**没有一个能写库的工具**混进来（写权限仍然只在系统手里）",
                not ({"add_todo_tool", "remove_todo", "update_todo_status"} & set(sched)),
                sorted(sched))

        # —— ③ 单元：只给"哪天"就能凑出提案；那天排不进就如实返回 None ——
        auto = auto_todo_proposal("周四帮我加个健身")
        c.check("auto_todo_proposal 认『哪天 + 什么事』",
                bool(auto) and auto.get("date") == thu and auto.get("auto") is True,
                _json.dumps(auto, ensure_ascii=False)[:110])
        c.check("只给事、不给哪天 → 不硬凑（返回 None，交给追问细问）",
                auto_todo_proposal("帮我安排游泳") is None)
        c.check("只给哪天、没说做什么 → 也不硬凑",
                auto_todo_proposal("周四帮我安排一下") is None)
        # 把那天空档全塞满 → 应该挑不出来，返回 None（如实说"排不进"）
        from app.store import add_todo as _add
        for s in pl.find_free_slots(thu, min_minutes=30):
            _add("占位", thu, s["start"], s["end"])
        c.check("那天排满了就不硬凑一个时间（返回 None）",
                pick_free_slot(thu) is None
                and auto_todo_proposal("周四帮我加个健身") is None)
        c.check("排不进时如实说『这天排不下』，并给他换日子的办法",
                "排不" in pl.todo_missing_advice("周四帮我加个健身"),
                pl.todo_missing_advice("周四帮我加个健身")[:60])
        for t in list_todos(thu):          # 清掉占位，后面还要用这天
            from app.store import delete_todo
            delete_todo(t["id"])
        c.check("清完占位，空档又回来了", pick_free_slot(thu) is not None)

        # —— ④ 端到端：学生只说了"哪天" → **先给二选一卡**，选完才摊 / 才规划 ——
        #     （本轮规格：不许一上来就问他几点，也不许替他定死一个点）
        sid = "proactive-1"
        d = client.post("/api/chat", json={
            "message": "那你帮我加一个健身在周四", "session_id": sid}).json()
        o = d.get("options") or []
        c.check("学生只给『哪天』先出二选一卡（不再反问他几点）",
                any(x.get("kind") == "todo_mode" for x in o),
                _json.dumps(o[:1], ensure_ascii=False)[:120])
        c.check("标题干净（不是『那你帮我健身』这种）",
                bool(o) and o[0].get("title") == "健身",
                (o[0].get("title") if o else ""))
        c.check("卡片上带上了他说过的那天（后面摊候选/规划就只在这天找）",
                bool(o) and o[0].get("date") == thu, (o[0].get("date") if o else ""))
        c.check("话里把两条路都摊明白了",
                "我自己定" in (d.get("answer") or "") and "帮我挑" in (d.get("answer") or ""),
                (d.get("answer") or "")[:90])
        c.check("出条没写库（学生还没点头）",
                not any(t["title"] == "健身" for t in list_todos(thu)))

        # 他点/回「我自己定」→ 这才摊出那天的几段空档让他打勾
        d1b = client.post("/api/chat", json={"message": "我自己定", "session_id": sid}).json()
        o1b = d1b.get("options") or []
        c.check("选了『我自己定』→ 摊出候选时段卡",
                any(x.get("kind") == "todo_slots" for x in o1b),
                _json.dumps(o1b[:1], ensure_ascii=False)[:120])
        _os = (o1b[0].get("slots") if o1b else None) or []
        c.check("候选全在周四那天、而且不止一段",
                len(_os) >= 2 and all(s.get("date") == thu for s in _os),
                [s.get("label") for s in _os])
        c.check("话里说清『这几段都空着，你自己勾』",
                "空着" in (d1b.get("answer") or "") and "勾" in (d1b.get("answer") or ""),
                (d1b.get("answer") or "")[:80])
        c.check("并留了活口：都不合适就在「其他时间」自己写一个",
                "其他" in (d1b.get("answer") or ""), (d1b.get("answer") or "")[:80])
        c.check("摊候选这一轮也没写库",
                not any(t["title"] == "健身" for t in list_todos(thu)))

        # 他真报了个点 → 沿用原卡换时间，重新出条
        d2 = client.post("/api/chat", json={
            "message": "下午两点到三点", "session_id": sid}).json()
        o2 = d2.get("options") or []
        c.check("学生嫌点不合适、报了个新点 → 按他说的换时间重出条",
                bool(o2) and o2[0].get("title") == "健身"
                and o2[0].get("start") == "14:00" and o2[0].get("end") == "15:00",
                _json.dumps(o2[:1], ensure_ascii=False)[:120])
        c.check("换时间这一轮仍然没写库",
                not any(t["title"] == "健身" for t in list_todos(thu)))

        d3 = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        c.check("回「确认」真的写进日程了",
                any(t["title"] == "健身" and t["start"] == "14:00" for t in list_todos(thu)),
                [f"{t['title']}@{t['start']}" for t in list_todos(thu)])
        c.check("回答说的是「已加入日程」", "已加入日程" in (d3.get("answer") or ""),
                (d3.get("answer") or "")[:60])

        # —— ⑤ 截图那一幕：管家只在**文字里**写了方案，没挂条；学生回「确认」——
        #     系统要拿得出方案（包括自己补时间），把条挂出来，而不是让他空等。
        sid2 = "proactive-2"
        append_conversation(sid2, "user", "周四下午还有什么活动没")
        append_conversation(sid2, "assistant",
                            "周四下午确实还空着一段比较完整的空档：15:40–17:10。"
                            "如果你考虑把健身放到这个时间段，我可以顺手帮你安排。")
        append_conversation(sid2, "user", "那你帮我加一个健身在周四")
        append_conversation(sid2, "assistant",
                            "好，周四这档提案这就发给你：- 任务：健身 - 时间：周四 15:40~17:10 "
                            "- 范围：本周 点【确认】就入库了。")
        before = [t["title"] for t in list_todos(thu)]
        d4 = client.post("/api/chat", json={"message": "确认", "session_id": sid2}).json()
        o4 = d4.get("options") or []
        c.check("管家只写了文字方案时，学生回「确认」也要把条挂出来",
                any(x.get("kind") == "todo_add" for x in o4),
                _json.dumps(o4[:1], ensure_ascii=False)[:120])
        c.check("捞回来的就是他说的那件事、那天",
                bool(o4) and o4[0].get("title") == "健身" and o4[0].get("date") == thu,
                _json.dumps(o4[:1], ensure_ascii=False)[:120])
        c.check("这一轮还没写（还是要他点一下/回一句确认）",
                [t["title"] for t in list_todos(thu)] == before,
                [t["title"] for t in list_todos(thu)])
        n_before = len([t for t in list_todos(thu) if t["title"] == "健身"])
        d5 = client.post("/api/chat", json={"message": "确认", "session_id": sid2}).json()
        n_after = len([t for t in list_todos(thu) if t["title"] == "健身"])
        c.check("再回一句「确认」就真落库了，而且**只多一条**（不重复写）",
                n_after == n_before + 1,
                [f"{t['title']}@{t['start']}" for t in list_todos(thu)])

        # —— ⑥ 换一天也接得住：学生说"改成下周六"——
        uid = "proactive-3"
        client.post("/api/chat", json={"message": "周三加个健身", "session_id": uid})
        c.check("周三那条先挂出二选一卡（等他先选时间怎么定）",
                any(x.get("kind") == "todo_mode"
                    for x in (peek_pending(uid) or {}).get("options") or []),
                _json.dumps((peek_pending(uid) or {}).get("options") or [], ensure_ascii=False)[:90])
        # 选「你帮我挑」→ 系统规划出单条（而不是摊一堆让他再挑一遍）
        client.post("/api/chat", json={"message": "你帮我挑", "session_id": uid})
        c.check("选了『你帮我挑』→ 出系统规划的单条确认条",
                any(x.get("kind") == "todo_add"
                    for x in (peek_pending(uid) or {}).get("options") or []),
                _json.dumps((peek_pending(uid) or {}).get("options") or [], ensure_ascii=False)[:110])
        d6 = client.post("/api/chat", json={"message": "改成周六", "session_id": uid}).json()
        o6 = d6.get("options") or []
        sat = (_dt.date.today() + _dt.timedelta(
            days=(6 - _dt.date.today().isoweekday()) % 7 or 7)).isoformat()
        c.check("说『改成周六』就换到周六（并在那天重新挑了空档）",
                bool(o6) and o6[0].get("date") == sat and o6[0].get("title") == "健身",
                _json.dumps(o6[:1], ensure_ascii=False)[:120])
        c.check("换天也是只出条、不写库",
                not any(t["title"] == "健身" for t in list_todos(sat)))

        # —— ⑦ 单元：retime 不会把"一句新的下单"吃掉 ——
        card = {"kind": "todo_add", "title": "健身", "date": thu,
                "start": "15:40", "end": "17:10"}
        c.check("『改成晚上七点到八点』→ 换时段、日期不动",
                (retime_todo_proposal(card, "改成晚上七点到八点") or {}).get("start") == "19:00")
        c.check("『周四加个复习』这种新下单不会被当成'改时间'",
                retime_todo_proposal(card, "周四加个复习") is None)
    return c.summary("第八批（只给哪天→系统算时间 / 回确认必出条）")


def test_todo_slots_pick_and_batch():
    """第九批：候选时段——AI 列几段、学生打勾、批量落库、顺手刷新日程。

    这一批盯的是学生那条驳回（截图 + 原话）：
      「我定的太严了，你改一下，由 ai 帮我去挑选合适时间，**进行列举**，
        采用和删除课表时同样的弹框，内容变成那几个时间的选择或者其他
        （这个选项可以由我来自行补充），**由我打勾**，进行增加，
        **增加确认完立刻刷新日程**」

    上一版只给他**一个**时间点（"就排这儿了"），等于替他做了主——
    他要么全盘接受、要么再让你换一次，来回两轮。这一批钉五件事：
      ① **列举**：说了哪天就只列那天、没说就跨天各列一段；每段都必须真在空档里，
         绝不排到课上，也绝不硬凑（那天满了就如实给空列表，不偷偷换天）；
      ② **同款弹框**：跟前两类一样做成 `todo_slots` 提案，由独立的 /confirm 页面渲染；
      ③ **打勾落库**：勾几段落几段，走 `POST /api/todos/batch`，
         同一批里**逐条复核空档**（后一条能看见前一条，不会自己撞自己）；
      ④ **「其他」自填**：学生自己写的那句要认（认不出就如实说，绝不瞎猜）；
      ⑤ **落库即刷新 + 回执留痕**：写完立刻刷日程面板，回执进会话历史。
    """
    import datetime as _dt

    title("9. 候选时段：AI 列举 → 学生打勾 → 批量落库 → 日程刷新")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.modules.planner import (
        build_scheduling_tools, candidate_slots, parse_slot_text,
        slot_is_free, todo_slots_proposal,
    )
    from app.store import add_todo, delete_todo, list_todos

    with sandbox():
        client = make_client()
        seed_timetable()
        today = _dt.date.today().isoformat()
        # 挑一个"有课、也有空档"的周四（跟第八批同一套取法）
        thu = (_dt.date.today() + _dt.timedelta(
            days=(4 - _dt.date.today().isoweekday()) % 7 or 7)).isoformat()

        # —— ① 列举规则：说哪天只列那天；没说就跨天各列一段 ——
        only_thu = candidate_slots("周四加个健身")
        c.check("说了哪天 → 只列那一天（不偷偷换天）",
                bool(only_thu) and all(s["date"] == thu for s in only_thu),
                [f"{s['date']} {s['start']}-{s['end']}" for s in only_thu])
        c.check("那天里给的不止一段（上午/下午/晚上各一段，学生有得挑）",
                len(only_thu) >= 2, len(only_thu))
        c.check("每一段都真的落在空档里（课和已排待办都避开了）",
                all(slot_is_free(s["date"], s["start"], s["end"]) for s in only_thu),
                [f"{s['start']}-{s['end']}" for s in only_thu])
        spread = candidate_slots("加个健身")
        c.check("没说哪天 → 跨天各列一段（不是同一天复制三遍）",
                len({s["date"] for s in spread}) >= 2,
                [f"{s['date']} {s['start']}" for s in spread])
        c.check("今天只列「此刻之后」的时段（已经过去的上午不该出现在候选里）",
                all(not (s["date"] == today and s["end"] <= _dt.datetime.now().strftime("%H:%M"))
                    for s in spread),
                [f"{s['date']} {s['start']}-{s['end']}" for s in spread])
        per = candidate_slots("在原本第一节课的位置加入健身")
        c.check("按「第N节课」指时间也接得住 → 换算成 08:00-09:40 并排第一",
                bool(per) and per[0]["start"] == "08:00" and per[0]["end"] == "09:40",
                _json.dumps(per[:1], ensure_ascii=False)[:110])
        c.check("那一段也真在空档里（那天被课占了就往后找，绝不排到课上）",
                bool(per) and slot_is_free(per[0]["date"], per[0]["start"], per[0]["end"]))
        # 把某一天整天塞满 → 该如实给空列表（不硬凑、也不换天）
        far = (_dt.date.today() + _dt.timedelta(days=9)).isoformat()
        add_todo("占位", far, "07:00", "22:00")
        c.check("那天排满了 → 如实给空列表（不硬凑，也不偷偷换一天）",
                candidate_slots(f"{far} 加个健身") == []
                and todo_slots_proposal(f"{far} 加个健身") is None,
                f"cand={candidate_slots(far + ' 加个健身')}")
        for t in list_todos(far):
            delete_todo(t["id"])

        # —— ② 卡片形状：一张 todo_slots（跟清空课表/删待办并列的第三类确认条）——
        card = todo_slots_proposal("周四加个健身")
        c.check("凑成一张 todo_slots 卡",
                bool(card) and card.get("kind") == "todo_slots", (card or {}).get("kind"))
        c.check("卡上带着那件事的名字，还有一句提示",
                bool(card) and card.get("title") == "健身" and bool(card.get("hint")),
                f"{card.get('title')} / {card.get('hint')}")
        c.check("每一段都有能直接显示给学生看的 label（周X + 日期 + 时段）",
                bool(card) and len(card.get("slots") or []) >= 2
                and all(s.get("label") for s in card["slots"]),
                [s.get("label") for s in (card or {}).get("slots") or []])
        c.check("只说了要加什么事、没说哪天 → 也列得出来（跨天，不再反问他）",
                bool(todo_slots_proposal("加个健身")))
        c.check("连「要加什么事」都没说（只剩语气词）→ 不硬凑一张卡",
                todo_slots_proposal("周四加一个") is None)

        # —— ③ 落库前的空档复核（候选是上一轮算的，中间可能又冒出安排）——
        probe = only_thu[0]
        add_todo("占位甲", probe["date"], probe["start"], probe["end"])
        c.check("学生勾完到落库之间又冒出一条安排 → 复核会拦下",
                not slot_is_free(probe["date"], probe["start"], probe["end"]))
        for t in list_todos(probe["date"]):
            if t["title"] == "占位甲":
                delete_todo(t["id"])
        c.check("清掉之后同一段又空了",
                slot_is_free(probe["date"], probe["start"], probe["end"]))
        c.check("坏输入一律当「占着」（宁可拦下，也别把两条安排排到同一个点）",
                not slot_is_free(thu, "25:00", "26:00")
                and not slot_is_free(thu, "10:00", "09:00")
                and not slot_is_free("不是日期", "10:00", "11:00"))

        # —— ④ 「其他」自填：学生自己写的那行 ——
        c.check("自填说全了 → 直接用他给的时间",
                (parse_slot_text("周六 19:00-20:00", "健身", thu) or {}).get("start") == "19:00")
        c.check("自填只报了钟点、没报哪天 → 用候选里最靠前那天补上",
                (parse_slot_text("晚上七点到八点", "健身", thu) or {}).get("date") == thu,
                _json.dumps(parse_slot_text("晚上七点到八点", "健身", thu), ensure_ascii=False)[:90])
        c.check("自填只说了哪天 → 那天替他挑一段空档",
                (parse_slot_text("周六", "健身", thu) or {}).get("weekday") == "周六")
        c.check("自填那行也跟着带上事情的名字（他写的是「其他时间」，不是「其他事情」）",
                (parse_slot_text("周六 19:00-20:00", "健身", thu) or {}).get("title") == "健身")
        c.check("自填里认不出时间 → 返回 None（交给接口如实说，不瞎猜一个）",
                parse_slot_text("随便啦", "健身", thu) is None)

        # —— ⑤ 批量落库接口：勾两段 → 两条一起写 ——
        #     注意：这一批测的是 **todo_slots 卡 + 批量接口**，
        #     所以先走一步「我自己定」把候选卡换出来（本轮规格：第一站是二选一卡）。
        sid = "slots-batch-1"
        client.post("/api/chat", json={
            "message": "那你帮我加一个健身在周四", "session_id": sid})
        ds = client.post("/api/chat", json={
            "message": "我自己定", "session_id": sid}).json()
        o = ds.get("options") or []
        got = o[0] if o else {}
        ss = got.get("slots") or []
        c.check("选了『我自己定』之后出的是候选卡（不是替他定死一条）",
                got.get("kind") == "todo_slots" and len(ss) >= 2,
                f"{got.get('kind')} / {len(ss)} 段")
        picked = ss[:2]
        r = client.post("/api/todos/batch", json={
            "session_id": sid, "title": got.get("title"),
            "slots": [{"date": s["date"], "start": s["start"], "end": s["end"]}
                      for s in picked],
        })
        dd = r.json()
        c.check("勾了两段 → 两条一起落库（一次写完，不会写一半）",
                r.status_code == 200 and dd.get("count") == 2
                and len(dd.get("added") or []) == 2,
                _json.dumps(dd, ensure_ascii=False)[:130])
        c.check("两条都真在日程里",
                all(any(t["title"] == "健身" and t["date"] == s["date"]
                        and t["start"] == s["start"] for t in list_todos(s["date"]))
                    for s in picked),
                [f"{t['title']}@{t['date']} {t['start']}"
                 for t in list_todos(picked[0]["date"])])
        c.check("回执进会话历史了（刷新页面还看得见「我加过」）",
                any("已加入日程" in (m.get("content") or "")
                    for m in client.get("/api/conversation/" + sid).json().get("messages", [])))
        c.check("卡片被标记已执行（学生再回一句「确认」不会重复写一遍）",
                (peek_pending(sid) or {}).get("applied") is True)

        r2 = client.post("/api/todos/batch", json={
            "session_id": sid, "title": "健身",
            "slots": [{"date": picked[0]["date"], "start": picked[0]["start"],
                       "end": picked[0]["end"]}],
        }).json()
        c.check("已经排过的那一段再提交 → 拦下并说出原因（不叠第二条）",
                r2.get("ok") is False and r2.get("skipped")
                and "占" in r2["skipped"][0]["reason"],
                _json.dumps(r2, ensure_ascii=False)[:120])

        r3 = client.post("/api/todos/batch", json={
            "session_id": sid, "title": "健身", "slots": [],
            "other": "周日晚上七点到八点"}).json()
        c.check("只写「其他」也行——这就是学生要的「自行补充」",
                r3.get("ok") is True and r3.get("count") == 1
                and r3["added"][0]["start"] == "19:00",
                _json.dumps(r3, ensure_ascii=False)[:120])

        r4 = client.post("/api/todos/batch", json={
            "session_id": "slots-batch-2", "title": "健身", "slots": [],
            "other": "随便啦"}).json()
        c.check("自填认不出来 → 如实说「没认出」，一条都不写",
                r4.get("ok") is False and r4.get("unparsed") == "随便啦"
                and not r4.get("added"),
                _json.dumps(r4, ensure_ascii=False)[:120])

        r5 = client.post("/api/todos/batch", json={
            "session_id": "slots-batch-3", "title": "健身", "slots": [], "other": ""})
        c.check("一段没勾、也没自填 → 明确报错（不静默地什么都不做）",
                r5.status_code == 400, r5.status_code)

        # —— ⑥ 只回「确认」但还没选时间怎么定 → 不替他挑，把卡再挂一遍 ——
        #     本轮规格下这一档有两种卡都会遇到：
        #       · 暂存是**二选一卡**（时间由谁定都还没选）→ 一个字都不许写；
        #       · 暂存是**候选卡**（他选了自己定、但还没勾）→ 也不许写。
        #     所以这里两条都测。
        sid2 = "slots-confirm-only"
        client.post("/api/chat", json={"message": "加个健身", "session_id": sid2})
        n_before = len(list_todos())
        d6 = client.post("/api/chat", json={"message": "确认", "session_id": sid2}).json()
        c.check("只回「确认」还没选方式 → 一条都不写",
                len(list_todos()) == n_before
                and "一条都没写进去" in (d6.get("answer") or ""),
                (d6.get("answer") or "")[:80])
        c.check("而且把二选一卡再挂出来，请他先选一种",
                any(x.get("kind") == "todo_mode" for x in (d6.get("options") or [])),
                _json.dumps((d6.get("options") or [])[:1], ensure_ascii=False)[:100])

        # 换成候选卡之后（他说了"我自己定"）再回「确认」→ 同样不许写、把候选条再挂一遍
        sid2b = "slots-confirm-only-2"
        client.post("/api/chat", json={"message": "加个健身", "session_id": sid2b})
        client.post("/api/chat", json={"message": "我自己定", "session_id": sid2b})
        n_before2 = len(list_todos())
        d6b = client.post("/api/chat", json={"message": "确认", "session_id": sid2b}).json()
        c.check("候选卡上只回「确认」没打勾 → 也不替他挑、一条都不写",
                len(list_todos()) == n_before2
                and "一条都没写进去" in (d6b.get("answer") or ""),
                (d6b.get("answer") or "")[:80])
        c.check("而且把候选条再挂出来，请他勾一个",
                any(x.get("kind") == "todo_slots" for x in (d6b.get("options") or [])),
                _json.dumps((d6b.get("options") or [])[:1], ensure_ascii=False)[:100])

        # —— ⑥-bis 模型那条路（引擎收 __proposal__）也要把候选卡存进暂存 ——
        # 不存的话：学生看着候选卡回一句「确认」，系统手里什么都没有，
        # 就会掉进"确认落空"兜底，话术变成"刚才那次可能没接上"，看着像系统忘了事。
        from app.agent.pending import peek_pending as _peek
        from app.main import _remember_todo_options
        _remember_todo_options("slots-remember",
                               [todo_slots_proposal("周六加个健身")])
        _held = (_peek("slots-remember") or {}).get("options") or [{}]
        c.check("候选卡进暂存时**保持原样**（转成单张 todo_pick 会把几段候选整个丢掉）",
                _held[0].get("kind") == "todo_slots" and len(_held[0].get("slots") or []) >= 2,
                _json.dumps(_held[0], ensure_ascii=False)[:90])

        # —— ⑦ 前端契约：跟清空课表同一个弹框（勾 + 其他），落库即刷新 ——
        proj = pathlib.Path(__file__).resolve().parent.parent
        cf = (proj / "app" / "static" / "confirm.html").read_text(encoding="utf-8")
        page = (proj / "app" / "static" / "student.html").read_text(encoding="utf-8")
        for needle, why in (
            ("todo_slots:", "确认页上多了 todo_slots 这一类（跟清空课表/删待办并列）"),
            ('type="checkbox"', "确认页里是**打勾**选时间（不是只念一遍让学生回话）"),
            ('id="cfOther"', "确认页里有「其他时间」自填输入框（学生明确要的「自行补充」）"),
            ("/api/todos/batch", "落库走批量写入口（逐条复核空档后一起写）"),
        ):
            c.check(why, needle in cf)
        c.check("每勾一段按钮上的字跟着变（学生一眼看到自己要写几条）",
                "syncSlots" in cf and "加入日程（" in cf)
        for needle, why in (
            ('o.kind === "todo_slots"', "学生端认得这一类提案"),
            ("openConfirm(card, container)", "走的是**同一个内嵌确认条**（跟清空课表同待遇）"),
            ('d.type === "applied"', "接住确认页回传的成功结果"),
            ("loadScheduleView()", "确认完立刻刷新日程面板（学生不用自己刷）"),
        ):
            c.check(why, needle in page)
        c.check("这组只读工具多了一件 propose_todo_slots_tool（模型也列得出候选）",
                "propose_todo_slots_tool" in build_scheduling_tools(),
                sorted(build_scheduling_tools()))
        pl_src = (proj / "app" / "modules" / "planner.py").read_text(encoding="utf-8")
        c.check("提示层跟着改成「列几段让学生打勾」，不是「替他挑一个」",
                "进行列举" in pl_src and "列 2~4 段" in pl_src)
    return c.summary("第九批（候选时段：列举 / 打勾 / 批量落库 / 其他自填）")


def test_todo_mode_and_ai_plan():
    """第十批：先问"时间怎么定" + AI 去规划 + **识别啥才是真的事情**。

    这一批盯的是学生第三轮驳回（七张截图 + 一段话）：
      「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
        不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定），
        用户选择后，针对没时间……**」
      「**核心是 ai 帮我安排时间，ai 去规划时间，然后这个加入代办，
        要识别啥才是真的事情，不是随便拿那一句话就去当代办加入日程了**」

    所以四件事一起钉：
      ① 标题要**判真**：不像一件事就当"没说清"，去追问，绝不拿半句话当待办名；
      ② 时长要认："大概一个小时"→60 分钟，AI 规划时按它排，不一律 90 分钟；
      ③ 第一站是**二选一卡**（不是我列你挑、就是我替你排），学完才往下走；
      ④ 选了"你帮我挑" → **系统真去规划**，并说清"为什么排在这儿"。

    ⚠️ 形态改过两次，都记在这儿：
      · 第一版：系统替他挑一个点（被驳回："我定的太严了"）；
      · 第二版：一律先摊候选卡（又被驳回："要区分两种……先给弹窗"）；
      · 这一版（第三版）：先问"时间怎么定"，再分两条路走。
    """
    import datetime as _dt

    title("10. 先问「时间怎么定」/ AI 规划 / 识别真的事情")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.modules import planner as pl
    from app.modules.planner import (
        build_scheduling_tools, format_minutes, is_mode_answer, looks_like_thing,
        mode_to_card, parse_mode_answer, plan_todo_slot, todo_mode_proposal,
        todo_title_of, wanted_minutes,
    )
    from app.store import list_todos

    with sandbox():
        client = make_client()
        seed_timetable()

        # —— ① 标题判真：两个实测到的垃圾标题必须修掉 ——
        #    「大概一个小时帮我安排时间」→ 旧版抠成「大概小时帮我时间」还挂出了候选卡；
        #    「周三12点到2点我要去吃自助餐，帮我添加」→ 旧版抠成「去吃自助餐帮我」。
        c.check("只有时长、没说做什么 → **判为没有事情名**（不拿残渣当代办）",
                todo_title_of("大概一个小时帮我安排时间") == "待办",
                todo_title_of("大概一个小时帮我安排时间"))
        c.check("一句话里全是要加的事 → 抠出「吃自助餐」（不是整句也不是残渣）",
                todo_title_of("周三12点到2点我要去吃自助餐，帮我添加") == "吃自助餐",
                todo_title_of("周三12点到2点我要去吃自助餐，帮我添加"))
        c.check("「我要去跑步」→「跑步」（「我要去」整块擦掉）",
                todo_title_of("我要去跑步") == "跑步", todo_title_of("我要去跑步"))
        for raw, want in (("帮我加个游泳", "游泳"), ("周四加个健身", "健身"),
                          ("记一下交电费", "交电费"), ("周三加个健身", "健身"),
                          ("周六加个游泳，大概一个小时", "游泳")):
            c.check(f"正常下单还是抠得准：{raw} → {want}",
                    todo_title_of(raw) == want, todo_title_of(raw))
        c.check("判真函数认得「这不是一件事」",
                not looks_like_thing("大概小时")
                and not looks_like_thing("帮我")
                and not looks_like_thing("时间")
                and not looks_like_thing("待办"))
        c.check("判真函数也认得「这就是一件事」",
                looks_like_thing("游泳") and looks_like_thing("吃自助餐")
                and looks_like_thing("复习线性代数"))
        c.check("判真把「时间/小时」这类残渣挡掉（它们不该出现在名字里）",
                not looks_like_thing("大概小时") and not looks_like_thing("1 小时"))

        # —— ② 时长：学生给了就是唯一该尊重的约束 ——
        for raw, want in (("大概一个小时帮我安排时间", 60), ("半小时", 30),
                          ("两个小时", 120), ("四十分钟", 40), ("1.5小时", 90)):
            c.check(f"时长认得出来：{raw} → {want} 分钟",
                    wanted_minutes(raw) == want, wanted_minutes(raw))
        c.check("没提时长 → None（回落到默认，不瞎猜一个数字）",
                wanted_minutes("周三12点到2点") is None and wanted_minutes("2点") is None)
        c.check("「90 分钟」写成人话是「1 小时 30 分钟」",
                format_minutes(90) == "1 小时 30 分钟" and format_minutes(60) == "1 小时",
                format_minutes(90))

        # —— ③ 二选一卡：只有问题、没有时间；说不出事名就出不出来 ——
        mc = todo_mode_proposal("帮我加个游泳，大概一个小时")
        c.check("有事情名 + 没定时间 → 出二选一卡",
                bool(mc) and mc.get("kind") == "todo_mode", _json.dumps(mc, ensure_ascii=False)[:90])
        c.check("卡上记下了标题和时长（后面两条路都要用）",
                bool(mc) and mc.get("title") == "游泳" and mc.get("minutes") == 60)
        c.check("**卡里没有任何时间**（它只问「怎么定」，不是「排在这儿」）",
                bool(mc) and not mc.get("start") and not mc.get("end"))
        c.check("说不出事名 → 出不了卡（交给追问问「要做什么事」）",
                todo_mode_proposal("大概一个小时帮我安排时间") is None)
        c.check("只说了哪一天、没说做什么 → 也出不了卡",
                todo_mode_proposal("周四帮我安排一下") is None)

        # —— ④ 两条路：mode_to_card ——
        c.check("选 'self' → 候选卡（几段摊开让他勾）",
                (mode_to_card(mc, "self") or {}).get("kind") == "todo_slots")
        ai_card = mode_to_card(mc, "ai")
        c.check("选 'ai' → 系统规划好的单条（不是又摊一堆让他挑）",
                bool(ai_card) and ai_card.get("kind") == "todo_add"
                and ai_card.get("auto") is True,
                _json.dumps(ai_card, ensure_ascii=False)[:110])
        c.check("规划出来的那段**真的空着**（课和已排的待办都避开了）",
                bool(ai_card) and any(
                    f["start"] <= ai_card["start"] and f["end"] >= ai_card["end"]
                    for f in pl.find_free_slots(ai_card["date"], min_minutes=1)),
                f"{ai_card.get('date')} {ai_card.get('start')}-{ai_card.get('end')}"
                if ai_card else "")
        c.check("**按学生说的时长排**（「大概一个小时」就是 60 分钟，不是默认 90）",
                bool(ai_card) and ai_card.get("minutes") == 60,
                (ai_card or {}).get("minutes"))
        c.check("并且写清「为什么排在这儿」",
                bool(ai_card) and "空" in (ai_card.get("reason") or ""),
                (ai_card or {}).get("reason", "")[:70])
        c.check("理由里提了他的时长（让他知道系统听进去了）",
                bool(ai_card) and "1 小时" in (ai_card.get("reason") or ""))

        no_mins = plan_todo_slot("健身", "周四加个健身")
        c.check("没说时长 → 回落到默认（而且照样是真的空档）",
                bool(no_mins) and no_mins.get("auto") is True
                and no_mins.get("minutes") == 90,
                (no_mins or {}).get("minutes"))

        # —— ⑤ 聊天里回"你帮我挑 / 我自己定"也接得住 ——
        for raw, want in (("你帮我挑", "ai"), ("你帮我定", "ai"), ("随便", "ai"),
                          ("你看着办", "ai"), ("我自己定", "self"), ("我有时间", "self")):
            c.check(f"认得出这是选哪条路：{raw} → {want}",
                    parse_mode_answer(raw) == want, parse_mode_answer(raw))
        c.check("只是回答「哪条路」，别把长句也吃进来",
                not is_mode_answer("帮我加个游泳，大概一个小时")
                and is_mode_answer("你帮我挑"))

        # —— ⑥ 端到端：先问 → 选"你帮我挑" → 系统规划 → 落库 ——
        sid = "mode-ai"
        d1 = client.post("/api/chat", json={
            "message": "帮我加个游泳，大概一个小时", "session_id": sid}).json()
        o1 = d1.get("options") or []
        c.check("第一轮先给二选一卡（没有一上来就问他几点）",
                any(x.get("kind") == "todo_mode" for x in o1),
                _json.dumps(o1[:1], ensure_ascii=False)[:110])
        c.check("回答里说清了两条路，且没有反问「几点到几点」",
                "我自己定" in (d1.get("answer") or "")
                and "帮我挑" in (d1.get("answer") or "")
                and "几点到几点" not in (d1.get("answer") or ""),
                (d1.get("answer") or "")[:80])
        c.check("第一轮一个字都没写库", not any(t["title"] == "游泳" for t in list_todos()))

        d2 = client.post("/api/chat", json={"message": "你帮我挑", "session_id": sid}).json()
        o2 = d2.get("options") or []
        c.check("选『你帮我挑』→ 出系统规划的单条确认条",
                bool(o2) and o2[0].get("kind") == "todo_add" and o2[0].get("auto") is True,
                _json.dumps(o2[:1], ensure_ascii=False)[:110])
        c.check("回答里把「为什么排在这儿」也说了一遍",
                "空" in (d2.get("answer") or ""), (d2.get("answer") or "")[:90])
        c.check("规划完还没写库（要学生点头）",
                not any(t["title"] == "游泳" for t in list_todos()))

        day = o2[0]["date"] if o2 else ""
        d3 = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        c.check("回「确认」才真写进日程",
                any(t["title"] == "游泳" and t["date"] == day for t in list_todos(day)),
                [f"{t['title']}@{t['date']}" for t in list_todos(day)])
        c.check("回答说的是「已加入日程」", "已加入日程" in (d3.get("answer") or ""))

        # —— ⑦ 端到端：选"我自己定" → 候选卡 → 打勾落库 ——
        sid2 = "mode-self"
        client.post("/api/chat", json={"message": "周四加个健身", "session_id": sid2})
        d4 = client.post("/api/chat", json={"message": "我自己定", "session_id": sid2}).json()
        o4 = d4.get("options") or []
        ss = (o4[0].get("slots") if o4 else None) or []
        c.check("选『我自己定』→ 出候选卡（几段摊开、留「其他」口子）",
                bool(o4) and o4[0].get("kind") == "todo_slots" and len(ss) >= 2,
                f"{len(ss)} 段")
        c.check("候选全落在真实空档里", all(
            any(f["start"] <= s["start"] and f["end"] >= s["end"]
                for f in pl.find_free_slots(s["date"], min_minutes=1)) for s in ss),
            [s.get("label") for s in ss])
        picked = ss[:1]
        r = client.post("/api/todos/batch", json={
            "session_id": sid2, "title": o4[0].get("title"),
            "slots": [{"date": s["date"], "start": s["start"], "end": s["end"]}
                      for s in picked]}).json()
        c.check("打了勾 → 真落库",
                r.get("count") == 1
                and any(t["title"] == "健身" and t["start"] == picked[0]["start"]
                        for t in list_todos(picked[0]["date"])),
                _json.dumps(r, ensure_ascii=False)[:110])

        # —— ⑧ 只回「确认」而卡是二选一卡 → 一条都不许写 ——
        sid3 = "mode-confirm-only"
        client.post("/api/chat", json={"message": "加个跑步", "session_id": sid3})
        n0 = len(list_todos())
        d5 = client.post("/api/chat", json={"message": "确认", "session_id": sid3}).json()
        c.check("还没选「时间怎么定」就回确认 → 一条都不写",
                len(list_todos()) == n0 and "一条都没写进去" in (d5.get("answer") or ""),
                (d5.get("answer") or "")[:70])
        c.check("而且把二选一卡再挂出来，请他先选一种",
                any(x.get("kind") == "todo_mode" for x in (d5.get("options") or [])))

        # —— ⑨ 接口那条路（前端两颗按钮按的就是它）——
        sid4 = "mode-api"
        client.post("/api/chat", json={"message": "加个背单词，大概40分钟", "session_id": sid4})
        ra = client.post("/api/todos/mode",
                         json={"session_id": sid4, "mode": "ai"}).json()
        pa = ra.get("pending") or {}
        c.check("/api/todos/mode mode=ai → 返回规划好的单条",
                ra.get("ok") and pa.get("kind") == "todo_add"
                and pa.get("minutes") == 40 and pa.get("auto") is True,
                _json.dumps(pa, ensure_ascii=False)[:110])
        c.check("换出来的新卡**进了暂存**（这样学生回「确认」才找得到）",
                any(x.get("kind") == "todo_add"
                    for x in (peek_pending(sid4) or {}).get("options") or []))
        c.check("换卡这一步本身不写库",
                not any(t["title"] == "背单词" for t in list_todos()))

        sid5 = "mode-api-2"
        client.post("/api/chat", json={"message": "加个背单词", "session_id": sid5})
        rs = client.post("/api/todos/mode",
                         json={"session_id": sid5, "mode": "self"}).json()
        ps = rs.get("pending") or {}
        c.check("/api/todos/mode mode=self → 返回候选卡",
                rs.get("ok") and ps.get("kind") == "todo_slots"
                and len(ps.get("slots") or []) >= 2,
                f"{ps.get('kind')} / {len(ps.get('slots') or [])} 段")
        bad = client.post("/api/todos/mode", json={"session_id": sid5, "mode": "???"})
        c.check("mode 传别的值 → 400（不猜）", bad.status_code == 400, bad.status_code)
        gone = client.post("/api/todos/mode",
                           json={"session_id": "no-such-session", "mode": "ai"})
        c.check("卡已经过期/没有卡 → 404 并让人重说一句",
                gone.status_code == 404 and "过期" in (gone.json().get("error") or ""),
                gone.json().get("error"))

        # —— ⑩ 暂存必须**原样**保住二选一卡 ——
        #     它是"没有时间"的那一类，很容易被完整性检查（要求 date/start/end）整张丢掉；
        #     丢掉的后果就是学生回「确认」时系统手里什么都没有，
        #     掉进"确认落空"兜底、话术变成"刚才那次可能没接上"。这是 todo_slots 刚踩过的坑。
        from app.main import _remember_todo_options, _pending_todo_mode
        _remember_todo_options("mode-remember",
                               [todo_mode_proposal("周六加个健身")])
        c.check("二选一卡进暂存时**保持原样**（kind 不改、问题还在）",
                (_pending_todo_mode("mode-remember") or {}).get("title") == "健身",
                _json.dumps((peek_pending("mode-remember") or {}).get("options") or [],
                            ensure_ascii=False)[:110])

        # —— ⑪ 没说什么事 → 追问，而且**不许把内部标记漏给学生看** ——
        #     学生截屏投诉过一句光秃秃的「加待办缺细节」——那是内部接续用的标记，
        #     漏到对话框里他完全不知道要干嘛。
        sid6 = "no-thing-name"
        d7 = client.post("/api/chat", json={
            "message": "大概一个小时帮我安排时间", "session_id": sid6}).json()
        c.check("只有时长、没说做什么 → **不出卡**（连要做什么都不知道，排什么时间）",
                not (d7.get("options") or []),
                _json.dumps(d7.get("options"), ensure_ascii=False)[:80])
        c.check("追问的是「要安排什么事」，不是「哪一天/几点」",
                "什么事" in (d7.get("answer") or "")
                and "几点" not in (d7.get("answer") or ""),
                (d7.get("answer") or "")[:80])
        c.check("把他给过的时长认下来（别让他重复说一遍）",
                "1 小时" in (d7.get("answer") or ""), (d7.get("answer") or "")[:70])
        c.check("**内部标记不许出现在回答里**（「加待办缺细节」这种）",
                "加待办缺细节" not in (d7.get("answer") or ""),
                (d7.get("answer") or "")[:70])
        c.check("这一轮也没写库", not any(t["title"] in ("待办", "") for t in list_todos()))

        # —— ⑫ 前端契约 & 提示层 ——
        proj = pathlib.Path(__file__).resolve().parent.parent
        cf = (proj / "app" / "static" / "confirm.html").read_text(encoding="utf-8")
        page = (proj / "app" / "static" / "student.html").read_text(encoding="utf-8")
        for needle, why in (
            ("todo_mode:", "确认页上多了 todo_mode 这一类（二选一的弹框）"),
            ('data-mode="self"', "卡上有「时间我自己定」那颗按钮"),
            ('data-mode="ai"', "卡上有「你帮我挑」那颗按钮"),
            ("/api/todos/mode", "点按钮打的是换卡接口"),
            ("noOk: true", "这一类不走页脚确认键（它的选择本身就是动作）"),
            ("p.reason", "AI 规划的单条会把「为什么排在这儿」显示出来"),
        ):
            c.check(why, needle in cf)
        for needle, why in (
            ('o.kind === "todo_mode"', "学生端认得二选一卡"),
            ("/api/todos/mode", "兜底路径也直接打换卡接口"),
        ):
            c.check(why, needle in page)
        c.check("这组只读工具多了一件 propose_todo_mode_tool（模型也出得了这张卡）",
                "propose_todo_mode_tool" in build_scheduling_tools(),
                sorted(build_scheduling_tools()))
        pl_src = (proj / "app" / "modules" / "planner.py").read_text(encoding="utf-8")
        c.check("提示层改成「先问时间怎么定」，不再写「一上来就列几段」",
                "先给弹窗" in pl_src and "你帮我定" in pl_src)
        c.check("提示层写明了「不许拿那半句话当代办名」",
                "不是随便拿那一句话就去当代办加入日程了" in pl_src)
    return c.summary("第十批（先问时间怎么定 / AI 规划 / 识别真的事情）")


def test_rescue_when_model_forgot_tool():
    """第十一批：模型没调工具、只在文字里念方案 → 系统替它补条（人话：别再"嘴上说发了"）。

    报障原话就一句：「**没有收到弹窗**」（附一张截图）。

    截图里管家那段文字前后是矛盾的：前半句"提案我这边还没生成出来…你现在点
    【确认】是空确认"，后半句"**待办提案** - 事项：游泳 - 时间：周二 16:00~17:30
    …提案已发出，请点确认条上的【确认】"——界面上一个按钮都没有。

    根因不是"捞不出来"（那段原文 `parse_todo_from_reply` 捞得**分毫不差**），
    而是**那一步压根没被调用**：它以前只挂在"学生回确认"那一支（3a-c）里，
    而学生说的是"没有收到弹窗"，`is_confirmation` 为假 → 整条兜底被跳过 →
    请求掉给模型 → 模型又没调工具 → 界面上什么都没有。

    这一批钉两件事：
      ① 补条兜底从"只在回确认时"扩到"模型这一路也算"；
      ② 顺手修掉 propose_todo_tool 把 end 丢掉的老 bug（学生要 90 分钟、
         卡片上只有 60，模型还照着卡片解释了一句"系统默认按 1 小时出的条"）。
    """
    title("11. 模型没调工具时的补条兜底 / propose_todo_tool 的 end")
    c = Checker()
    from app.modules.planner import (_pick_span, claims_proposal_sent,
                                     propose_todo_tool, rescue_proposal_from_reply,
                                     parse_todo_from_reply)
    from app.modules import schedule as sched
    from app.store import append_conversation, get_conversation, list_todos

    # 截图里管家那段原文，逐字抄下来当输入。
    shot_text = (
        "我理解你想赶紧定下来，但得跟你说实话：**提案我这边还没生成出来**，"
        "你现在点【确认】是空确认，不会入库。\n\n"
        "咱们把节奏捋一下：\n\n"
        "1. 我发提案 → 2. 你看到确认条 → 3. 你点【确认】→ 4. 系统入库\n\n"
        "现在卡在第 1 步。我这就把提案发出来，你等我这条消息之后，再点确认条。\n\n"
        "**待办提案**\n- 事项：游泳\n- 时间：周二 16:00~17:30\n\n"
        "提案已发出，请点确认条上的【确认】。"
    )

    # —— ① propose_todo_tool 必须尊重 end ——
    #     坑：`_pick_span("17:30")` 返回的是 ("17:30", None) —— 单个钟点算**起点**。
    c.check("单个钟点在 _pick_span 里算起点（这是下面那个 bug 的根）",
            _pick_span("17:30") == ("17:30", None), _pick_span("17:30"))
    got = _json.loads(propose_todo_tool("游泳", date="周二",
                                        start="16:00", end="17:30"))
    prop = got.get("__proposal__") or {}
    c.check("分着给 date+start+end 时，end 不许被丢掉（原来会变成 16:00-17:00）",
            prop.get("start") == "16:00" and prop.get("end") == "17:30",
            prop.get("summary", ""))
    c.check("时长按学生说的算：90 分钟，不是默认的 60",
            prop.get("minutes") == 90, str(prop.get("minutes")))
    c.check("「周二」也算对了（不是今天，也不是空）",
            bool(prop.get("date")) and prop.get("weekday") == "周二",
            f"{prop.get('date')} {prop.get('weekday')}")
    got2 = _json.loads(propose_todo_tool("游泳", when="周一 16:30~18:00"))
    c.check("回归：when 里带波浪号的时段照旧解析得对",
            (got2.get("__proposal__") or {}).get("start") == "16:30"
            and (got2.get("__proposal__") or {}).get("end") == "18:00",
            (got2.get("__proposal__") or {}).get("summary", ""))
    got3 = _json.loads(propose_todo_tool("游泳", date="2026-09-28",
                                         start="19:00", end="20:30"))
    c.check("回归：日期给全 + 分着给起止，也照旧对",
            (got3.get("__proposal__") or {}).get("end") == "20:30",
            (got3.get("__proposal__") or {}).get("summary", ""))
    # 「默认 1 小时」只能留给"真没给结束时间"的那一档，不能被 bug 顺手用掉。
    got4 = _json.loads(propose_todo_tool("游泳", date="2026-09-28",
                                         start="19:00"))
    c.check("真没给结束时间时才补默认 1 小时（19:00-20:00）",
            (got4.get("__proposal__") or {}).get("end") == "20:00",
            (got4.get("__proposal__") or {}).get("summary", ""))

    # —— ② 「自称发了提案」这一关 ——
    c.check("认得出『提案已发出』", claims_proposal_sent("提案已发出，请点确认条上的【确认】。"))
    c.check("认得出『确认条已经挂出来了』", claims_proposal_sent("确认条已经挂出来了，点一下就好。"))
    c.check("认得出『点一下【确认】就入库了』", claims_proposal_sent("点一下【确认】就入库了！"))
    c.check("认得出『我这就把提案发出来』", claims_proposal_sent("我这就把提案发出来。"))
    c.check("闲聊里不认（不出手补条）",
            not claims_proposal_sent("今天天气不错，要不要去操场跑两圈？"))
    c.check("正经回答问题不认",
            not claims_proposal_sent("高等数学在教三-201，周一 08:00 上课。"))

    # —— ③ 补条这一关：能捞就用它的数字，捞不出才退"第一站" ——
    c.check("截图那段原文本身是捞得出来的（所以问题不在「捞」，在「没被调用」）",
            (parse_todo_from_reply(shot_text) or {}).get("start") == "16:00"
            and (parse_todo_from_reply(shot_text) or {}).get("end") == "17:30",
            _json.dumps(parse_todo_from_reply(shot_text), ensure_ascii=False))
    r1 = rescue_proposal_from_reply(shot_text)
    c.check("补出来的就是那张 todo_add，数字用它的（16:00-17:30）",
            bool(r1) and r1.get("kind") == "todo_add" and r1.get("start") == "16:00"
            and r1.get("end") == "17:30",
            _json.dumps(r1, ensure_ascii=False)[:120] if r1 else "None")
    c.check("标题没被抠残（是「游泳」，不是那半句话）",
            bool(r1) and r1.get("title") == "游泳", (r1 or {}).get("title"))
    c.check("从它文字里捞出来的不带 auto（那不是系统替学生挑的时间）",
            bool(r1) and not r1.get("auto"))
    # 它只说"发了"、正文里没有可捞的格式 → 回到第一站：先问时间怎么定。
    r2 = rescue_proposal_from_reply("提案已发出，请点确认条上的【确认】。",
                                    add_req="周四加个健身")
    c.check("只自称发了、正文捞不出方案时，退到第一站出二选一卡",
            bool(r2) and r2.get("kind") == "todo_mode",
            _json.dumps(r2, ensure_ascii=False)[:110] if r2 else "None")
    c.check("这张二选一卡上不出现任何时间（第一站只问「归谁定」）",
            bool(r2) and not r2.get("start") and not r2.get("end"))
    c.check("连学生要加什么都没说过 → 什么都不补（不硬凑）",
            rescue_proposal_from_reply("提案已发出，请点确认条上的【确认】。",
                                       add_req="") is None)
    c.check("它没自称发提案 → 不补（闲聊里别硬挂一张卡）",
            rescue_proposal_from_reply("今天天气不错，要不要去操场跑两圈？",
                                       add_req="周四加个健身") is None)

    # —— ④ 走接口：截图那一幕必须出卡 ——
    class _FakeEngine:
        """假引擎（人话：照着剧本回一句，专门模拟"模型没调工具"）。"""

        def __init__(self, *a, **kw):
            pass

        async def run(self, message, history=None):
            return {"answer": shot_text,
                    "trace": [{"step": 1, "phase": "💡 最终回答", "answer": shot_text}],
                    "options": []}

    with sandbox():
        seed_timetable()
        import app.main as _m
        _real_engine = _m.AgentEngine
        _m.AgentEngine = _FakeEngine
        try:
            client = make_client()
            sid = "rescue-popup"
            append_conversation(sid, "user", "周二下午四点到五点半游泳")
            append_conversation(sid, "assistant", "好的，我看看周二下午有没有空。")

            r = client.post("/api/chat", json={"message": "没有收到弹窗",
                                               "module": "schedule",
                                               "session_id": sid})
            d = r.json()
            opts = d.get("options") or []
            c.check("学生说「没有收到弹窗」→ 终于有卡了（这就是报障那一幕）",
                    any(o.get("kind") == "todo_add" for o in opts),
                    _json.dumps(opts, ensure_ascii=False)[:120])
            card = next((o for o in opts if o.get("kind") == "todo_add"), {})
            c.check("卡上是学生要的 16:00-17:30（不是被砍成 60 分钟的 16:00-17:00）",
                    card.get("start") == "16:00" and card.get("end") == "17:30",
                    card.get("summary", ""))
            c.check("awaiting_choice 置上了（前端据此知道有东西要确认）",
                    d.get("awaiting_choice") is True)
            c.check("回答里说明了「卡片没跟上、我补一张」，不让学生去猜",
                    "可卡片没跟上" in (d.get("answer") or "")
                    and "点【确认加入】" in (d.get("answer") or ""),
                    (d.get("answer") or "")[:70])
            c.check("模型那段自相矛盾的话不再原样端给学生",
                    "空确认，不会入库" not in (d.get("answer") or ""))
            c.check("轨迹里留了痕迹（排查时看得出是系统补的条）",
                    any("补出确认条" in (t.get("phase") or "")
                        for t in (d.get("trace") or [])),
                    [t.get("phase") for t in (d.get("trace") or [])])
            hist = get_conversation(sid)
            c.check("写进历史的是**学生看到的那一句**（刷新后不回到模型那段）",
                    bool(hist) and "可卡片没跟上" in (hist[-1].get("content") or ""),
                    (hist[-1].get("content") or "")[:60])

            # 闭环：补出来的条得真能用——学生回一句「确认」就落库。
            r2 = client.post("/api/chat", json={"message": "确认",
                                                "module": "schedule",
                                                "session_id": sid})
            c.check("补出来的条回一句「确认」就真写进日程了",
                    any(t["title"] == "游泳" and t["start"] == "16:00"
                        and t["end"] == "17:30" for t in list_todos("2026-09-29")),
                    [f"{t['title']}@{t['date']} {t['start']}-{t['end']}"
                     for t in list_todos("2026-09-29")])
            c.check("回答里报了回执（不是「我没办法」）",
                    "已加入日程" in (r2.json().get("answer") or ""),
                    (r2.json().get("answer") or "")[:60])

            # 不误伤：模型正常答一句、且没自称发过提案 → 一张卡都不许补。
            class _QuietEngine:
                def __init__(self, *a, **kw):
                    pass

                async def run(self, message, history=None):
                    return {"answer": "高等数学在教三-201，周一 08:00。",
                            "trace": [], "options": []}

            _m.AgentEngine = _QuietEngine
            sid2 = "rescue-quiet"
            append_conversation(sid2, "user", "周一第一节是什么课")
            r3 = client.post("/api/chat", json={"message": "周一第一节是什么课",
                                                "module": "schedule",
                                                "session_id": sid2})
            c.check("模型正常答一句 → 不许凭空补一张卡",
                    not (r3.json().get("options") or []),
                    _json.dumps(r3.json().get("options"), ensure_ascii=False))
        finally:
            _m.AgentEngine = _real_engine

    # —— ⑤ 前端不用改：卡片还是走 options 那条老路 ——
    proj = pathlib.Path(__file__).resolve().parent.parent
    page = (proj / "app" / "static" / "student.html").read_text(encoding="utf-8")
    c.check("补出来的卡走的是同一套 options 渲染（前端零改动）",
            "renderOptions" in page and "todo_add" in page)
    sched_src = (proj / "app" / "modules" / "schedule.py").read_text(encoding="utf-8")
    c.check("提示层点名禁掉「待办提案 / - 事项：」这种排版替代工具调用",
            "待办提案" in sched_src and "替代工具调用" in sched_src)
    c.check("提示层禁掉「提案我这边还没生成出来」这类解释",
            "提案我这边还没生成出来" in sched_src)
    return c.summary("第十一批（模型没调工具 → 系统补条）")


def main():
    global code
    print("\n" + "=" * 60)
    print("test_06_add_things：加课 / 加待办 / 会话历史 / 清理记录")
    print("=" * 60)
    code |= test_add_todo_pipeline()
    code |= test_add_course_pipeline()
    code |= test_conversation_history()
    code |= test_clear_conversation()
    code |= test_add_todo_chinese_clock()
    code |= test_schedule_proposal_tool()
    code |= test_remove_todo_pipeline()
    code |= test_proactive_time_and_confirm()
    code |= test_todo_slots_pick_and_batch()
    code |= test_todo_mode_and_ai_plan()
    code |= test_rescue_when_model_forgot_tool()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
