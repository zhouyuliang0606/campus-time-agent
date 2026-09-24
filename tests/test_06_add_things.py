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


def main():
    global code
    print("\n" + "=" * 60)
    print("test_06_add_things：加课 / 加待办 / 会话历史 / 清理记录")
    print("=" * 60)
    code |= test_add_todo_pipeline()
    code |= test_add_course_pipeline()
    code |= test_conversation_history()
    code |= test_clear_conversation()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
