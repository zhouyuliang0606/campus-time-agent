# -*- coding: utf-8 -*-
"""#111：改期遇到同名多条以后，「第一条」「周三那条」必须把那条**定下来**——不许再问一遍。

起因（2026-11-02 22:47 实测 + 2026-11-03 用户截图）：待办里「健身房」有两条，说
「把健身房挪到周六」→ 列出两条问改哪一条 → 回「周三那条」「第一条」「周二的」**六种续答
六次回到同一句**。卡不点、话没法说，演示第 4 步当场录废。

根因两层，都不在"识别不出来"上：
  ① `build_retime_existing_proposal` 早就有 `anchor_date` 这个收窄维度（#87 磨的刀），
     但**只有引擎工具那一路在传**，学生说话走的确定性分支（`main`）压根没递 ⇒ "哪一条"这一维被整个丢掉；
  ② 列完清单**什么都不记**：下一句只能按名字重新筛，重新筛出来的还是那几条 ⇒ 同一句追问再问一遍。

两处施工单自己没写到的地方（本文件按实测钉住）：
  · `_pick_date("周二的")` 给的是**下一个周二**（`:3048` 那句 `% 7 or 7`）。施工单 11-02 测的
    那张表（周二=11-03）那天成立，第二天（今天就是周二）就不成立了——照它只传 `anchor_date`，
    「把周二的健身房改到周六」**照样 many**。所以另开一把尺 `anchor_weekday`（有「周X」就按周几筛）。
  · ② 那份账**必须记**：只补 ① 的话，「周三那条」能把池子收窄，但第一句里的"周六"丢了，
    只能回"那改到几点"；他补一句「周六晚上八点」，名字又是两条 ⇒ 循环只是往后挪了一步。

还有一条是施工中自己踩出来的（不是推演）：清单是上一轮的快照，那条要是中途被删了，
**退回"按名字再筛一遍"会端出一张指向另一条同名的卡**（他点第一条，卡是隔壁那条）。
所以读回时必须验一遍"这条还在库里"，不在了就把清单丢掉重来，宁可再问一次。
"""
import datetime

from _harness import (Checker, make_client, sandbox, seed_todos, title,
                      wipe_todos)

WD = "一二三四五六日"


def _d(off: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()


def _wd(off: int) -> str:
    """那一天对应的「周X」说法（**按当天算**，不写死——写死就是 #90 那个老毛病）。"""
    return "周" + WD[(datetime.date.today() + datetime.timedelta(days=off)).isoweekday() - 1]


def seed2():
    """两条同名（今天 + 明天，正好差一个周几）+ 一条不同名的（用来演"报名字"）。

    ⚠️ 两条都排在**今天和明天**是有意的：`_pick_date(今天那个周几)` 永远回**下一周**那一天，
    这一份种法让"只按 ISO 收窄"那种写法每一天都红（不是只在周二红）。
    """
    return [{"id": "gym-a", "title": "健身房", "date": _d(0), "start": "20:40", "end": "21:40"},
            {"id": "gym-b", "title": "健身房", "date": _d(1), "start": "19:00", "end": "20:30"},
            {"id": "lib", "title": "图书馆", "date": _d(4), "start": "08:00", "end": "09:30"}]


def _go(fn, *a, **kw):
    """把"抛异常"变成"红"，不是让整批崩掉（#110 的教训：拿不到值会被当成红，这里是崩）。"""
    try:
        return fn(*a, **kw), ""
    except Exception as e:                       # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _old_id(r):
    return ((r or {}).get("proposal") or {}).get("old_todo_id", "")


def batch_a_planner_dimensions():
    c = Checker()
    title("A. planner 的定位维度（anchor_weekday / pick_id / 两把尺的先后）")
    with sandbox():
        seed_todos(seed2())
        from app.modules.planner import (_RETIME_SPLIT_RE, _RETIME_WORDS, _hit_when,
                                         _pick_date, build_retime_existing_proposal)
        say = "把健身房改到" + _d(7)
        today_wd = datetime.date.today().isoweekday()

        r1, e1 = _go(build_retime_existing_proposal,
                     "把" + _wd(1) + "的健身房改到" + _d(7),
                     anchor_date=_d(1), anchor_weekday=0)
        c.check("T1 说明天那条（走 ISO 那一把尺，和 #87 的老行为一致）→ ok，点的就是 gym-b",
                (r1 or {}).get("status") == "ok" and _old_id(r1) == "gym-b",
                f"{e1} {(r1 or {}).get('status')} old={_old_id(r1)}")

        # T2 是本单的**命门**：今天那条只能用"周几"这一把尺够得着（ISO 会算到下周同日）。
        left_today = "把" + _wd(0) + "的健身房"
        r2a, e2 = _go(build_retime_existing_proposal, left_today + "改到" + _d(7),
                      anchor_weekday=today_wd)
        r2b, e2b = _go(build_retime_existing_proposal, left_today + "改到" + _d(7),
                       anchor_date=_pick_date(left_today))
        c.check("T2 ★「把" + _wd(0) + "的健身房改到…」：按**周几**收窄命中今天那条（gym-a）",
                (r2a or {}).get("status") == "ok" and _old_id(r2a) == "gym-a",
                f"{e2} {(r2a or {}).get('status')} old={_old_id(r2a)}")
        c.check("T2-bis ★同一句只按 `_pick_date` 那个 ISO 收窄（" + repr(_pick_date(left_today)) +
                "）⇒ **仍然 many**：施工单 §2.1 那张表在 11-02 成立、11-03 就不成立了"
                "（`_pick_date('周二的')` 回的是下周二）——这一条就是 `anchor_weekday` 存在的理由",
                (r2b or {}).get("status") == "many",
                f"{e2b} {(r2b or {}).get('status')}")

        r3, e3 = _go(build_retime_existing_proposal, say, hint_title="健身房")
        c.check("T3 没说哪一条 → many + 两条都在（不许替他挑一条）",
                (r3 or {}).get("status") == "many" and len((r3 or {}).get("todos") or []) == 2,
                f"{e3} {(r3 or {}).get('status')}")

        r4, e4 = _go(build_retime_existing_proposal,
                     "把" + _wd(6) + "的健身房改到" + _d(7),      # 库里没有周六那条健身房
                     anchor_weekday=6)
        c.check("T4 收窄落空要**原样退回**：「把周六的健身房…」库里没有 → 还是 many，不许变 empty"
                "（empty = \"没找到那条待办\"，那是 #84 花一整张单修掉的假话）",
                (r4 or {}).get("status") == "many" and len((r4 or {}).get("todos") or []) == 2,
                f"{e4} {(r4 or {}).get('status')} pool_n={(r4 or {}).get('pool_n')}")

        # T5 同一天两条同名：日子那一维到此用尽，只有 id 分得开
        seed_todos([{"id": "n-1", "title": "夜跑", "date": _d(2), "start": "07:00", "end": "08:00"},
                    {"id": "n-2", "title": "夜跑", "date": _d(2), "start": "18:00", "end": "19:00"}])
        r5, e5 = _go(build_retime_existing_proposal,
                     "把夜跑挪到" + _d(7), anchor_date=_d(2))
        r5b, e5b = _go(build_retime_existing_proposal, "把夜跑挪到" + _d(7), pick_id="n-2")
        c.check("T5 同一天两条同名：按那天筛还是两条 → many（不瞎选）；`pick_id` 一条命中 → ok",
                (r5 or {}).get("status") == "many" and (r5b or {}).get("status") == "ok"
                and _old_id(r5b) == "n-2",
                f"{e5}{e5b} {(r5 or {}).get('status')} / {(r5b or {}).get('status')} old={_old_id(r5b)}")

        r5c, _ = _go(build_retime_existing_proposal, "把夜跑挪到" + _d(7), pick_id="不存在")
        c.check("T5-bis `pick_id` 库里没有 = **当这个参数没给**（照旧走名字那一维 → many），"
                "不许因为 id 没碰上就报\"没找到那条待办\"",
                (r5c or {}).get("status") == "many", str((r5c or {}).get("status")))

        pool = [{"id": "x1", "date": _d(0)}, {"id": "x2", "date": _d(1)}]
        c.check("T6 两把尺的先后写死在 `_hit_when`：**给了周几就只按周几**（这里 ISO 那把尺会指到 x1，"
                "而句子里说的周几是明天那条 x2）；没给周几才按那一天筛；两把都没给 → 空",
                [x["id"] for x in _hit_when(pool, ((datetime.date.today()
                                                    + datetime.timedelta(days=1)).isoweekday()),
                                            _d(0))] == ["x2"]
                and [x["id"] for x in _hit_when(pool, 0, _d(0))] == ["x1"]
                and _hit_when(pool, 0, "") == [],
                f"wd={_hit_when(pool, 2, _d(0))} d={_hit_when(pool, 0, _d(0))}")

        c.check("T7 `_RETIME_SPLIT_RE` 是从 `_RETIME_WORDS` **推出来的**，不是另抄一份词表"
                "（两张表各长各的是这类 bug 的老家）",
                all(w in _RETIME_SPLIT_RE.pattern for w in _RETIME_WORDS)
                and _RETIME_SPLIT_RE.split("把健身房挪到周六")[0] == "把健身房",
                f"{_RETIME_WORDS[:2]} pattern={_RETIME_SPLIT_RE.pattern[:60]}")
    return c


def batch_b_candidate_pick_shape():
    c = Checker()
    title("B. `pick_retime_candidate`：这句是不是在**选择**（形状判据）")
    with sandbox():
        seed_todos(seed2())
        from app.modules.planner import pick_retime_candidate
        lst = seed2()

        cases = [("第一条", "gym-a"), ("第二条", "gym-b"), ("2", "gym-b"), ("最后一个", "lib"),
                 (_wd(1) + "那条", "gym-b"), ("明天那个", "gym-b"),
                 ("图书馆", "lib"), ("图书馆那条", "lib")]
        for s, want in cases:
            got, e = _go(pick_retime_candidate, lst, s)
            c.check(f"T8 {s!r} → {want}", (got or {}).get("id") == want,
                    f"{e} got={(got or {}).get('id', 'None')}")

        rejects = ("第五条", "第0条", "", "随便", "确认", "今天天气怎么样",
                   "图书馆几点开门", "把周三那条挪到周六", _wd(0) + "的天气")
        for s in rejects:
            got, e = _go(pick_retime_candidate, lst, s)
            c.check(f"T9 {s!r} → None（**不许**兜到第一条/最后一条）", got is None,
                    f"{e} got={(got or {}).get('id', 'None')}")

        # 序号必须跟着**屏上那份列表**走：列表顺序换了，答案也要跟着换（错位就是改错条）。
        # ⚠️ 「第一条」和「第二条」**两个都要测**：只测第一条的话，`return todos[0]`（写死取第一条）
        #   这种实现两边都给对 —— 轮转后清单的第一条正好就是 gym-b，M11 第一版就是这么溜过去的
        #   （我按施工单口径只写了一条断言，实测出来是哑弹，所以补成 2×2）。
        rot = lst[1:] + lst[:1]
        for phrase, source, want in (("第一条", lst, "gym-a"), ("第二条", lst, "gym-b"),
                                     ("第一条", rot, "gym-b"), ("第二条", rot, "lib")):
            got, e = _go(pick_retime_candidate, source, phrase)
            c.check(f"T10 {phrase} 按{'轮转过' if source is rot else '原序'}那份清单数 → {want}"
                    "（同一句话在两份清单上必须给**不同**的那条）",
                    (got or {}).get("id") == want, f"{e} got={(got or {}).get('id', 'None')}")

        dup = [{"id": "w1", "title": "课", "date": _d(2)}, {"id": "w2", "title": "课", "date": _d(2)}]
        c.check("T11 同一天两条时，「周几那条」分不开 → None（日子这一维到此用尽，只能回序号）",
                pick_retime_candidate(dup, _wd(2) + "那条") is None,
                str(pick_retime_candidate(dup, _wd(2) + "那条")))

        two = [{"id": "t1", "title": "甲课", "date": _d(2)}, {"id": "t2", "title": "乙课", "date": _d(2)}]
        c.check("T12 「周几」筛出**两条**（那天有两条不同的）→ None；报名字才分得开",
                pick_retime_candidate(two, _wd(2) + "那条") is None
                and (pick_retime_candidate(two, "甲课") or {}).get("id") == "t1",
                f"{pick_retime_candidate(two, _wd(2) + '那条')} {pick_retime_candidate(two, '甲课')}")

        c.check("T13 空清单 / 清单不是 list 时不许崩（`todos` 是从暂存读出来的，形状不保）",
                pick_retime_candidate([], "第一条") is None
                and pick_retime_candidate(None, "第一条") is None, "ok")
    return c


def batch_c_end_to_end():
    c = Checker()
    title("C. 端到端（/api/chat，无 Key 也能走：续答靠的是**记账**，不是模型）")
    with sandbox():
        client = make_client()
        from app import store as S
        from app.agent.pending import peek_pending

        def ask(sid, msg):
            d = client.post("/api/chat", json={"message": msg, "module": "planner",
                                               "session_id": sid}).json()
            return d, [o for o in (d.get("options") or []) if isinstance(o, dict)]

        def card(opts):
            return next((o for o in opts if o.get("kind") == "todo_add" and o.get("old_todo_id")),
                        None)

        seed_todos(seed2())
        sid = "s111-list"
        d1, o1 = ask(sid, "把健身房改到" + _d(7))
        aux = next((o for o in ((peek_pending(sid) or {}).get("options") or [])
                    if isinstance(o, dict) and o.get("kind") == "todo_retime_candidates"), None)
        c.check("T14 第一句（同名两条、没说哪一条）→ 列清单，而且**编了号**、把清单记进暂存",
                "改哪一条" in (d1.get("answer") or "") and "1. " in d1.get("answer", "")
                and bool(aux) and len((aux or {}).get("todos") or []) == 2,
                (d1.get("answer") or "")[:120])
        c.check("T14-bis 清单里的 `want` 记的是学生那句**原话**（新时间\"改到下周六\"在里面，下一句要用）",
                _d(7) in str((aux or {}).get("want") or ""), str((aux or {}).get("want")))
        c.check("T15 例子不许写死「周四那条」：库里没有周四那条时，这句话里不能出现它；"
                "例子必须**现取**清单第一条的那个星期",
                ("周四那条" not in d1.get("answer", "")) or (_wd(0) == "周四") or (_wd(1) == "周四"),
                (d1.get("answer") or "")[-40:])

        for reply, want in (("第一条", "gym-a"), (_wd(1) + "那条", "gym-b"), (_wd(0), "gym-a")):
            seed_todos(seed2())
            s = f"s111-{reply}"
            ask(s, "把健身房改到" + _d(7))
            d2, o2 = ask(s, reply)
            cd = card(o2)
            c.check(f"T16 列完清单回 {reply!r} → **直接出改期卡**，且点的是那条对的（old_todo_id={want}）"
                    "、新时间用的是第一句给的" + _d(7) + "（不再问第二遍\"改哪一条\"）",
                    bool(cd) and cd.get("old_todo_id") == want and _d(7) in str(cd.get("date") or "")
                    and "改哪一条" not in (d2.get("answer") or ""),
                    f"card={(cd or {}).get('old_todo_id'), (cd or {}).get('date')} | "
                    f"{(d2.get('answer') or '')[:70]}")

        # ★ 一次性说全（没有清单、没有续答）：走的是 anchor 那一刀，与上面的记账路径互不相干。
        #   「把{今天那个周几}的…」这一句是**单据 §2.1 那张表的照妖镜**：`_pick_date` 给的是下周同日，
        #   只有把"周几"这把尺递进去才指得到今天那条（M1/M2 两笔变异各打在这里）。
        for one_shot, want in (("把" + _wd(0) + "的健身房改到" + _d(7), "gym-a"),
                               ("把" + _wd(1) + "的健身房改到" + _d(7), "gym-b")):
            seed_todos(seed2())
            d1s, o1s = ask("s111-1s-" + one_shot, one_shot)
            cds = card(o1s)
            c.check(f"T16-bis 一次性说全 {one_shot!r} → 直接出卡，点的是 {want}"
                    "（无清单、无续答，靠的是「换时间词左边」那一刀）",
                    bool(cds) and cds.get("old_todo_id") == want
                    and "改哪一条" not in (d1s.get("answer") or ""),
                    f"card={(cds or {}).get('old_todo_id')} | {(d1s.get('answer') or '')[:60]}")

        # ★ 语序代价照实钉住（不是"修好"，是把边界写清楚，免得日后当 bug 改回去）：
        #   anchor 取的是"换时间词**左边**"那截 ⇒ **日子落在后面**的那种说法丢定位，回到列清单
        #   （多问一句而已，不冤）。⚠️ 顺带纠正施工单 §2.1：它说"整句喂 `_pick_date` 会取到**新**日子"，
        #   实测**方向反了** —— `_pick_date`/`_pick_day` 取的都是**最左**那个日子，而中文里"哪一条"
        #   通常就在左边 ⇒ 它列的六句里两种写法**同解**（M2 因此第一版是哑弹；这一句才是它的靶子）。
        seed_todos(seed2())
        d_ws, o_ws = ask("s111-word-order", "把健身房改到" + _d(7) + "，" + _wd(1) + "那条")
        c.check("T28 语序代价登记：「把健身房改到{新日子}，{周X}那条」—— 日子落在换时间词**右边**，"
                "左边那截没日子 ⇒ 定位信息丢，回到列清单（多问一句，不许瞎猜）。"
                "M2 那笔变异就是打在这里：改成「取整句」这一条会红（红=那种写法确实不一样，不代表更好）",
                card(o_ws) is None and "改哪一条" in (d_ws.get("answer") or ""),
                (d_ws.get("answer") or "")[:70])

        for junk in ("今天天气怎么样", "图书馆几点开门", "我有哪些待办"):
            seed_todos(seed2())
            s = "s111-j-" + junk
            ask(s, "把健身房改到" + _d(7))
            d3, o3 = ask(s, junk)
            c.check(f"T17 清单挂着时说 {junk!r} **不许**被改期接走（不许出卡，也不许把那句追问再端出来）",
                    card(o3) is None and "改哪一条" not in (d3.get("answer") or ""),
                    str((card(o3) or {}).get("old_todo_id")) + " | " + (d3.get("answer") or "")[:60])

        seed_todos(seed2())
        s = "s111-del"
        ask(s, "把健身房改到" + _d(7))
        d4, o4 = ask(s, "删除第二条")
        c.check("T18 ★串味闸：清单挂着时「删除第二条」带着**删**的意图 → 让路给删的那一族，"
                "不许出一张改期卡（改期是\"删旧+加新\"，他要的是删干净）",
                card(o4) is None, str((card(o4) or {})) + " | " + (d4.get("answer") or "")[:70])

        seed_todos(seed2())
        s = "s111-sticky"
        ask(s, "把健身房改到" + _d(7))
        ask(s, "我有哪些待办")
        d5, o5 = ask(s, "第一条")
        c.check("T19 追问标记**不粘住**（#109 那条纪律）：隔了一轮别的话以后再回「第一条」，"
                "不算在回答改期，不许出卡", card(o5) is None,
                str((card(o5) or {}).get("old_todo_id")))

        seed_todos(seed2())
        s = "s111-stale"
        ask(s, "把健身房改到" + _d(7))
        seed_todos([x for x in seed2() if x["id"] != "gym-a"])        # 第一条没了
        d6, o6 = ask(s, "第一条")
        c.check("T20 ★过期清单不许指错条：那条已被删掉时，「第一条」**不能**出一张指向 gym-b 的卡"
                "（实测过那种退回\"按名字再筛\"正好落在隔壁那条上）",
                card(o6) is None or card(o6).get("old_todo_id") != "gym-b",
                str((card(o6) or {}).get("old_todo_id")))
        c.check("T20-bis 过期清单被丢掉（下一句重新列，编号跟着重排）",
                peek_pending(s) is None or not any(
                    isinstance(o, dict) and o.get("kind") == "todo_retime_candidates"
                    for o in ((peek_pending(s) or {}).get("options") or [])),
                str([o.get("kind") for o in ((peek_pending(s) or {}).get("options") or [])]))

        seed_todos(seed2())
        s = "s111-two-hop"
        ask(s, "把健身房换个时间")                       # 第一句没给新时间
        d7, o7 = ask(s, _wd(1) + "那条")
        d8, o8 = ask(s, "挪到" + _d(7) + "晚上八点")
        cd8 = card(o8)
        c.check("T21 两跳也通：没给新时间时先**念现状**（那句里必须带着那条的日期，让他知道那句起作用了），"
                "补一句「挪到…」之后出卡，而且还是同一条",
                _d(1) in (d7.get("answer") or "") and card(o7) is None
                and bool(cd8) and cd8.get("old_todo_id") == "gym-b"
                and _d(7) in str(cd8.get("date") or ""),
                f"hop1={(d7.get('answer') or '')[:50]} | hop2 card={(cd8 or {}).get('old_todo_id')}")

        # 只出提案、不写库：重新种一份已知的，走一整趟到出卡，条数必须还是那一份
        seed_todos(seed2())
        n_clean = len(S.list_todos())
        s = "s111-nowrite"
        ask(s, "把健身房改到" + _d(7))
        ask(s, "第一条")
        c.check("T22 全程只出提案：一整趟（列清单→点定→出卡）之后待办条数一字没动"
                "（写库要等他点【确认加入】）",
                len(S.list_todos()) == n_clean and n_clean == 3,
                f"{len(S.list_todos())} vs {n_clean}")
        wipe_todos()
    return c


def batch_d_boundaries():
    c = Checker()
    title("D. 没碰坏别人（引擎那一路 / 恢复接口 / 判据只有一份）")
    with sandbox():
        seed_todos(seed2())
        from app import main as M
        from app.modules.planner import build_retime_existing_proposal

        r, e = _go(build_retime_existing_proposal, "改 11-09 那天那个",
                   {"title": "待办"}, anchor_date=_d(4))
        c.check("T23 引擎那一路的 #87 保护照旧：**占位名 + 一个日子**不许把那天任何一条当答案"
                "（X1 那格假命中的放大版），收窄只在\"按名字筛过\"之后才用 anchor_date",
                (r or {}).get("status") != "ok" and not _old_id(r),
                f"{e} {(r or {}).get('status')} old={_old_id(r)}")

        c.check("T24 `todo_retime_candidates` 已登记进 `_PENDING_AUX_KINDS`"
                "（#59：没登记的话刷新时会被当成卡，前端三个 undefined）",
                "todo_retime_candidates" in M._PENDING_AUX_KINDS,
                str(sorted(M._PENDING_AUX_KINDS)))

        client = make_client()
        s = "s111-pending"
        client.post("/api/chat", json={"message": "把健身房改到" + _d(7), "module": "planner",
                                       "session_id": s})
        got = client.get(f"/api/pending/{s}").json()
        c.check("T25 只挂着候选清单时 `/api/pending/{sid}` 回的是 `pending: null`（清单不是卡）",
                got.get("pending") in (None, {}), str(got)[:120])

        import inspect
        src = inspect.getsource(M)
        c.check("T26 `_last_bot_message` 定义全站**只有一份**，而且改期这一支用的是它"
                "（\"上一句 bot 说的话\"这个判据别处再抄一份就会漂 —— :4511 那档已改成取用）",
                src.count("def _last_bot_message(") == 1
                and src.count("_last_bot_message(session_id)") >= 2,
                str([src.count("def _last_bot_message("),
                     src.count("_last_bot_message(session_id)")]))
        c.check("T26-bis ★这一单的根因形状：**写进会话的那个标记必须有人读**。"
                "「改期缺细节」出现两次 = 一处写（many 那一支）+ 一处读（`_retime_followup`）；"
                "以前它只写不读，所以续答全都掉回\"再问一遍\"",
                src.count("\"改期缺细节\"") == 2, str(src.count("\"改期缺细节\"")))

        r2, e2 = _go(build_retime_existing_proposal, "把" + _wd(1) + "的图书馆改到" + _d(3))
        c.check("T27 唯一一条时 anchor 那两把尺**完全不参与**（旧行为逐字不变，不 regress #77/#81）",
                (r2 or {}).get("status") in ("ok", "unclear")
                and _old_id(r2) in ("lib", ""),
                f"{e2} {(r2 or {}).get('status')} old={_old_id(r2)} reason={(r2 or {}).get('reason')}")
    return c


if __name__ == "__main__":
    code = 0
    for fn in (batch_a_planner_dimensions, batch_b_candidate_pick_shape,
               batch_c_end_to_end, batch_d_boundaries):
        code |= fn().summary(f"test_111 {fn.__name__} ")
    raise SystemExit(code)
