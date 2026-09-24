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

        # —— ③ 信息不全：系统追问，绝不掉回模型 ——
        sid2 = "oral-todo-2"
        r3 = client.post("/api/chat", json={
            "message": "帮我安排周二的游泳", "module": "schedule", "session_id": sid2,
        })
        d3 = r3.json()
        c.check("缺时间时先追问，不出提案（也不交给模型瞎说）",
                not (d3.get("options") or []) and d3.get("module") == "planner",
                (d3.get("answer") or "")[:60])
        c.check("追问里点明缺的是时间", "时间" in (d3.get("answer") or ""),
                (d3.get("answer") or "")[:60])

        # 补充一句「下午两点到三点」→ 标题要跟着上一句走（游泳），不能变成「待办」
        r4 = client.post("/api/chat", json={
            "message": "下午两点到三点", "module": "schedule", "session_id": sid2,
        })
        o4 = r4.json().get("options") or []
        c.check("补充的时间接上了，出提案", any(o.get("kind") == "todo_add" for o in o4),
                _json.dumps(o4[:1], ensure_ascii=False)[:120])
        c.check("标题仍是上一句的「游泳」（追问后的补充要合并原话）",
                bool(o4) and o4[0].get("title") == "游泳",
                (o4[0].get("title") if o4 else ""))
        c.check("时间换算成 14:00-15:00",
                bool(o4) and o4[0].get("start") == "14:00", (o4[0].get("start") if o4 else ""))

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
    return code


if __name__ == "__main__":
    raise SystemExit(main())
