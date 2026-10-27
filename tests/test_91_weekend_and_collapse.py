"""#75 的护栏：「周末」要认得，同名多张 `todo_add` 不许叠成两张卡。

现场（学生截图 + 实测轨迹，都在单据 §〇）：
  · 他说「周末安排打篮球」，`candidate_slots` 只有「不排周末」那一半（`workday_mode`），
    没有「只排周末」⇒ 候选铺成周三/周四/周五，他把哪天方便说得最死的一次被当成没说；
  · 模型给周六、周日各调一次 `propose_todo_tool` ⇒ options 里两条 `todo_add`，
    前端 `filter(o => o.kind === "todo_add")` 逐条渲染 → **屏幕上叠两张确认条**；
    而暂存 `save_pending` 是覆盖语义，只留最后一张 → 学生点第一张，系统认的是第二张。

批次分工（跑变异时看**红的是哪几条名字**，别看总数）：
  A 批 · §一 日期档（函数级）：T1 全落周末 / T2 周六不变 / 周日打桩那一档（V2 的靶子）。
  B 批 · 端到端出卡（HTTP）：真走 `/api/chat`，钉「卡片本身是周末」而不是「函数算得对」。
  C 批 · 两把尺子不许互相吃光 + 周期否决仍生效（正控族：`workday_mode`、每个周末）。
  D 批 · §二 同名收敛：T4（options + 暂存**一起**断）/ T5（不同名各留）/ 混合 / repeat / 删待办。
  S 批 · 静态接线：成对跳过、放宽档、收敛入口在第一句、两个调用点同源、只吃 `todo_add`。

⚠️ 三条自省：
  1. **段数判据靠沙箱里那份自种课表**，不靠「库里正好空」（A/B 各批自己 `write_timetable`）。
  2. **A4 必须打桩日期**：今天不是周日，「没放宽 days_ahead」这一档永远跑得过去 ——
     不打桩的断言是一条空判据（单据 §3.2 点名了这一条）。打桩还要配 `pin_now(21, 0)`：
     周日**上午**问「周末」，今天那段就算够得着，删掉放宽也照样出卡（实测），红不了。
  3. D 批只断言 `options` 不够：暂存里若还躺着两条，学生点【确认加入】照样错位
     （单据 §五.3 原话），所以 T4 把 `peek_pending` 一起钉上。
"""
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, pin_now, sandbox,  # noqa: E402
                      write_timetable)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

WD = "周一 周二 周三 周四 周五 周六 周日".split()
WEEKEND = ("周六", "周日")
# 自种课表：周六上午 09:00-10:30 有一节课 → 「候选避开课」这条才有东西可避
BUSY = [{"day": 6, "start": "09:00", "end": "10:30", "course": "周六实验", "location": "实验楼"},
        {"day": 3, "start": "10:00", "end": "11:30", "course": "体育课", "location": "操场"}]


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def wd(date):
    return WD[datetime.date.fromisoformat(date).weekday()]


def ask(cli, msg, sid):
    return cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                       "module": "planner"}).json()


def cards(d):
    return [o for o in (d.get("options") or []) if isinstance(o, dict)]


def add_card(title, date, start, end, minutes=60, weekday="", repeat=""):
    """照模型那一路真实产出的形状造一条 `todo_add`（截图里就是这两张）。"""
    return {"kind": "todo_add", "title": title, "date": date, "weekday": weekday,
            "start": start, "end": end, "minutes": minutes, "repeat": repeat,
            "summary": f"{title}｜{date}（{weekday}）{start}-{end}"}


def mins_of(slot):
    return (int(slot["end"][:2]) * 60 + int(slot["end"][3:])) \
        - (int(slot["start"][:2]) * 60 + int(slot["start"][3:]))


def stub_today(day, fn):
    """把 planner 眼里的「今天」换成 `day`（只在这一段里有效，出来必还原）。"""
    from app.modules import planner
    saved = planner.clock.today
    planner.clock.today = lambda: day
    try:
        return fn()
    finally:
        planner.clock.today = saved


def last_sunday():
    from app import clock
    real = clock.today()
    return real - datetime.timedelta(days=real.isoweekday())   # 上一个周日


def batch_a_date():
    c = Checker()
    from app.modules import planner as P
    print("\n=== A 批 · 日期档（函数级）===")
    with sandbox():
        write_timetable(BUSY)
        with pin_now(9, 0):
            t1 = P.todo_slots_proposal("周末打篮球")
            s1 = (t1 or {}).get("slots") or []
            c.check("A1 T1「周末打篮球」全落在周六/周日",
                    bool(s1) and all(wd(s["date"]) in WEEKEND for s in s1),
                    str([(s["date"], wd(s["date"])) for s in s1]))
            c.check("A1b T1 段数 >= 3（一个周末只有两天，够 3 段靠的是把范围放宽到两个周末）",
                    len(s1) >= 3, str(len(s1)))
            for phrase in ("周末 打篮球", "这周末 打篮球", "双休 打篮球"):
                card = P.todo_slots_proposal(phrase)
                got = (card or {}).get("slots") or []
                c.check(f"A1c {phrase!r} 同样全落周末",
                        bool(got) and all(wd(s["date"]) in WEEKEND for s in got),
                        str([(s["date"], wd(s["date"])) for s in got]))
            t2 = P.todo_slots_proposal("周六 打篮球")
            s2 = (t2 or {}).get("slots") or []
            c.check("A2 T2「周六 打篮球」仍全是周六（现状，不许被这次改动改坏）",
                    bool(s2) and all(wd(s["date"]) == "周六" for s in s2),
                    str([wd(s["date"]) for s in s2]))
            t3 = P.todo_slots_proposal("我平时想去跑步")
            s3 = (t3 or {}).get("slots") or []
            c.check("A3 T3「平时」一段周末都没有（正控：workday_mode 没被改坏）",
                    bool(s3) and all(wd(s["date"]) not in WEEKEND for s in s3),
                    str([wd(s["date"]) for s in s3]))
        # ★ 打桩「今天 = 周日晚上」：默认 days_ahead=5 时下一个周末在 6 天后，
        #   不放宽就一段都够不着（A4 是 V2 的靶子，不打桩这条永远绿）
        sun = last_sunday()
        with pin_now(21, 0):
            card = stub_today(sun, lambda: P.todo_slots_proposal("周末我要去打篮球"))
            got = (card or {}).get("slots") or []
            c.check(f"A4 打桩今天={sun}（周日）21:00 问「周末…」还出得了卡"
                    "（删掉 `days_ahead = max(...)` 这一条会红：这周末过完了、"
                    "下周末在 6 天外 ⇒ 一张都出不来）",
                    bool(got) and all(wd(s["date"]) in WEEKEND for s in got),
                    str([(s["date"], wd(s["date"])) for s in got]))
            # 对照：打桩本身要"是走到的"——不然 A4 红不红都说明不了放宽档的事
            hit = stub_today(sun, lambda: P.clock.today())
            c.check("A4b 打桩确实换掉了 planner 用的那份 `clock.today`"
                    "（`stub_today` 打偏 = A4 是一条空判据）",
                    hit == sun, str(hit))
    return c.summary("A 批（周末日期档）")


def batch_b_e2e():
    c = Checker()
    from app.modules import planner as P
    print("\n=== B 批 · 端到端出卡 ===")
    with sandbox():
        write_timetable(BUSY)
        cli = make_client()
        for i, msg in enumerate(("周末我要去打篮球", "周末安排打篮球")):
            with pin_now(9, 0):
                d = ask(cli, msg, f"s91-b{i}")
            k = cards(d)
            card = k[0] if k else None
            slots = (card or {}).get("slots") or []
            c.check(f"B{i + 1} {msg!r} → 出的就是**一张** todo_slots、每一段都在周六/周日",
                    len(k) == 1 and card.get("kind") == "todo_slots" and bool(slots)
                    and all(wd(s["date"]) in WEEKEND for s in slots),
                    str([(x.get("kind"), x.get("title")) for x in k])[:120])
            c.check(f"B{i + 1}b 事名是「打篮球」（没把「周末」擦成残渣挂上）",
                    bool(card) and card.get("title") == "打篮球", str(card and card.get("title")))
            c.check(f"B{i + 1}c 周六 09:00-10:30 那节课没有被排进去",
                    all(s["start"] >= "10:30" or s["end"] <= "09:00"
                        for s in slots if wd(s["date"]) == "周六"),
                    str([(s["date"], s["start"], s["end"]) for s in slots]))
        with pin_now(9, 0):
            d2 = ask(cli, "周六我要去打篮球", "s91-b3")
        s2 = (cards(d2)[0].get("slots") or []) if cards(d2) else []
        c.check("B3 说了「周六」就全是周六（weekend_mode 不许把单日扩成双休）",
                bool(s2) and all(wd(s["date"]) == "周六" for s in s2),
                str([wd(s["date"]) for s in s2]))
        with pin_now(9, 0):
            d3 = ask(cli, "周末晚上七点我要去打篮球", "s91-b4")
        s3 = (cards(d3)[0].get("slots") or []) if cards(d3) else []
        c.check("B4 「周末晚上七点」→ 每段起手都是 19:00 且全在周末",
                bool(s3) and all(s["start"] == "19:00" and wd(s["date"]) in WEEKEND for s in s3),
                str([(wd(s["date"]), s["start"]) for s in s3]))
        with pin_now(9, 0):
            card = P.todo_slots_proposal("周末游泳一小时")
        s4 = (card or {}).get("slots") or []
        c.check("B5 「周末游泳一小时」每段 60 分钟（#74 的时长四级没被这次改动带歪）",
                bool(s4) and all(mins_of(s) == 60 for s in s4),
                str([(s["start"], s["end"]) for s in s4]))
    return c.summary("B 批（端到端）")


def batch_c_two_rulers():
    c = Checker()
    from app.modules import planner as P
    print("\n=== C 批 · 两把尺子 + 周期否决 ===")
    with sandbox():
        write_timetable(BUSY)
        with pin_now(9, 0):
            both = P.todo_slots_proposal("平时周末都可以打篮球")
            got = (both or {}).get("slots") or []
            c.check("C1 一句里同时说「平时」和「周末」= 没限制 ⇒ 必须还出得了卡"
                    "（两条跳过各自都成立，一起开着会把整段范围吃光 → 实测 None）",
                    bool(got), str(got)[:120])
            c.check("C1b 这一档不许假装只排周末（工作日也得在候选里）",
                    len(got) >= 2 and any(wd(s["date"]) not in WEEKEND for s in got),
                    str([wd(s["date"]) for s in got]))
        cli = make_client()
        for idx, msg in enumerate(("每个周末打篮球", "每周末我要去打篮球")):
            d = ask(cli, msg, f"s91-c2-{idx}")
            c.check(f"C2 {msg!r} 仍走周期否决"
                    "（不许被 weekend_mode 收成「这周末一次」的候选卡）",
                    not any(o.get("kind") in ("todo_slots", "todo_add") for o in cards(d))
                    and "记不了" in (d.get("answer") or ""),
                    str([(o.get("kind"), o.get("title")) for o in cards(d)])[:100])
        with pin_now(9, 0):
            veto = P.unsupported_period("周末打篮球")
            c.check("C3 `unsupported_period('周末打篮球')` 是空（一次性说法不该被否决）——"
                    "否则 weekend_mode 永远到不了学生手上",
                    veto == "", repr(veto))
            c.check("C4 「周末」仍在 `_TIME_WORDS` 里（这次修的是**候选档**，"
                    "不是把时间词从擦除表里摘出来 —— 摘了会把事名一起改坏）",
                    "周末" in P._TIME_WORDS,
                    f"周末在表={('周末' in P._TIME_WORDS)} 双休在表={('双休' in P._TIME_WORDS)}"
                    "（双休不在擦除表里 → 事名会带上它，那是 #76 的活）")
    return c.summary("C 批（正控与否决）")


def batch_d_collapse():
    c = Checker()
    from app.main import _remember_todo_options, peek_pending
    print("\n=== D 批 · 同名收敛 ===")
    with sandbox():
        write_timetable(BUSY)
        cli = make_client()
        ask(cli, "你好", "s91-d-pin")        # 先把 owner 定成链路会用到的那个（顺序别倒）
        opts = [add_card("打篮球", "2026-10-31", "16:00", "17:00", weekday="周六"),
                add_card("打篮球", "2026-11-01", "16:00", "17:00", weekday="周日")]
        got = _remember_todo_options("s91-t4", opts)
        pend = (peek_pending("s91-t4") or {}).get("options") or []
        c.check("T4a 两条同名 `todo_add` → options 里只剩**一张**、且是 `todo_slots`",
                len(got) == 1 and got[0].get("kind") == "todo_slots",
                str([o.get("kind") for o in got]))
        c.check("T4b 那张卡两段都在（周六 16:00 + 周日 16:00，时间一个没丢）",
                [(s["date"], s["start"], s["end"]) for s in got[0].get("slots") or []]
                == [("2026-10-31", "16:00", "17:00"), ("2026-11-01", "16:00", "17:00")],
                str(got[0].get("slots"))[:150])
        c.check("T4c 暂存里也只有这一张（`save_pending` 是覆盖语义，"
                "只断 options 会放过「点第一张、系统认第二张」）",
                len(pend) == 1 and pend[0].get("kind") == "todo_slots"
                and len(pend[0].get("slots") or []) == 2, str(pend)[:140])
        c.check("T4d 事名与每段分钟数没被吃掉（title=打篮球 / minutes=60）",
                got[0].get("title") == "打篮球"
                and all(s.get("minutes") == 60 for s in got[0].get("slots") or []),
                str(got[0])[:120])
        c.check("T4e 每段 label 是既有那一族「周六（10/31）16:00-17:00」（前端就认这个字）",
                [s.get("label") for s in got[0].get("slots") or []]
                == ["周六（10/31）16:00-17:00", "周日（11/1）16:00-17:00"],
                str([s.get("label") for s in got[0].get("slots") or []]))

        got5 = _remember_todo_options("s91-t5", [
            add_card("打篮球", "2026-10-31", "16:00", "17:00", weekday="周六"),
            add_card("背单词", "2026-11-01", "16:00", "17:00", weekday="周日")])
        c.check("T5 两条**不同名**的 todo_add 仍是两张（这是「只收同名」那条约束的唯一证据 —— "
                "把同名条件去掉时红的就是这一条）",
                len(got5) == 2 and all(o.get("kind") == "todo_add" for o in got5),
                str([(o.get("kind"), o.get("title")) for o in got5]))

        got6 = _remember_todo_options("s91-mix", [
            add_card("打篮球", "2026-10-31", "16:00", "17:00", weekday="周六"),
            add_card("背单词", "2026-11-01", "16:00", "17:00", weekday="周日"),
            add_card("打篮球", "2026-11-01", "16:00", "17:00", weekday="周日")])
        c.check("D1 混合三条：同名两张合成一张、另一件原样保留，位置按第一张",
                len(got6) == 2 and got6[0].get("kind") == "todo_slots"
                and len(got6[0].get("slots") or []) == 2
                and got6[1].get("kind") == "todo_add" and got6[1].get("title") == "背单词",
                str([(o.get("kind"), o.get("title")) for o in got6]))

        got7 = _remember_todo_options("s91-rp", [
            add_card("健身", "2026-10-31", "16:00", "17:00", repeat="weekly"),
            add_card("健身", "2026-11-01", "16:00", "17:00")])
        c.check("D2 repeat 取第一条非空的（模型常只在第一张带 weekly，丢了就变「只排这一周」）",
                len(got7) == 1 and got7[0].get("repeat") == "weekly", str(got7)[:140])

        got8 = _remember_todo_options("s91-rm", [
            {"kind": "todo_remove", "title": "打篮球", "date": "2026-10-31",
             "start": "16:00", "end": "17:00", "id": "a"},
            {"kind": "todo_remove", "title": "打篮球", "date": "2026-11-01",
             "start": "16:00", "end": "17:00", "id": "b"}])
        c.check("D3 两张同名 `todo_remove` 原样留着（收敛只吃 todo_add —— "
                "删两条被合成一张「挑时间」的卡，就变成「删一条 = 新加一条」）",
                len(got8) == 2 and all(o.get("kind") == "todo_remove" for o in got8),
                str([(o.get("kind"), o.get("id")) for o in got8]))

        got9 = _remember_todo_options("s91-part", [
            add_card("打篮球", "2026-10-31", "16:00", "17:00"),
            {"kind": "todo_add", "title": "打篮球", "date": "", "start": "", "end": ""}])
        c.check("D4 残缺的那条（没 date/start/end）不算同名伙伴、也不被顺手编一段时间",
                len(got9) == 2 and all(o.get("kind") == "todo_add" for o in got9)
                and not any(o.get("slots") for o in got9),
                str([(o.get("kind"), len(o.get("slots") or [])) for o in got9]))

        got10 = _remember_todo_options("s91-one", [add_card("打篮球", "2026-10-31", "16:00", "17:00")])
        c.check("D5 只有一条时**一个字都不动**（不许把单条确认条升级成候选卡 —— "
                "学生已经定好一个点，再摊几段是把他刚说的话重问一遍）",
                len(got10) == 1 and got10[0].get("kind") == "todo_add",
                str([o.get("kind") for o in got10]))

        got11 = _remember_todo_options("s91-dur", [
            add_card("打篮球", "2026-10-31", "16:00", "17:00", minutes=60),
            add_card("打篮球", "2026-11-01", "16:00", "17:30", minutes=90)])
        c.check("D6 两段时长不一致时卡上的 duration 记 0（宁可说「他没说每次多久」，"
                "也不把 60 说成两段都是 60）",
                len(got11) == 1 and got11[0].get("duration") == 0
                and [s.get("minutes") for s in got11[0].get("slots") or []] == [60, 90],
                str(got11)[:150])
    return c.summary("D 批（同名收敛）")


def batch_s_static():
    c = Checker()
    print("\n=== S 批 · 静态接线 ===")
    pl = read("app/modules/planner.py")
    i = pl.find("def candidate_slots(")
    seg = pl[i:pl.find("\ndef ", i + 10)]
    c.check("S1 两条跳过**成对**存在（「平时」跳周末 / 「周末」跳工作日）——"
            "只留一边就是这次要修的那个洞",
            "if workday_mode and _iso_weekday(day) >= 6:" in seg
            and "if weekend_mode and _iso_weekday(day) < 6:" in seg, "")
    c.check("S2 weekend_mode 的词表就这两个（周末/双休）；判据钉的是**常量本身**，"
            "不是在源码里数「周末」出现几次 —— #76 把这两份词表抽成了 `_WEEKEND_WORDS`/"
            "`_WORKDAY_WORDS` 一处共用（能力清单要用同一把尺子），所以字面量搬进了常量定义行；"
            "抄第二份词表这件事由下面 S2c 钉（candidate_slots 里不许再出现字面量）",
            "weekend_mode = any(w in t for w in _WEEKEND_WORDS)" in seg
            and '_WEEKEND_WORDS = ("周末", "双休")' in pl
            and '_WORKDAY_WORDS = ("平时", "工作日")' in pl, "")
    c.check("S2c `candidate_slots` 里不许再出现「周末」/「平时」的字面量元组"
            "（两份词表 = #76 的能力清单会与候选档各说各话）",
            'for w in ("周末"' not in seg and 'for w in ("平时"' not in seg, "")
    j = seg.find("if weekend_mode:")
    k = seg.find("picked_day =")
    c.check("S3 放宽档挂在 `if weekend_mode:` 与 `picked_day =` 之间，且用的是 `max` "
            "（写死数字会在调用方给了更大值时把它砍小）",
            0 <= j < k and "days_ahead = max(days_ahead," in seg[j:k], f"j={j} k={k}")
    c.check("S4 两把尺子同时出现时**一起关掉**（少这一条，「平时和周末都行」= 一张卡都不出）",
            "workday_mode = weekend_mode = False" in seg, "")

    mn = read("app/main.py")
    i2 = mn.find("def _remember_todo_options(")
    seg2 = mn[i2:mn.find("\ndef ", i2 + 10)]
    c.check("S5 收敛挂在 `_remember_todo_options` 的**入口**（在 `picks = []` 之前）："
            "这个函数既返回渲染用的 options、又 save_pending，挂在这里两边才同源",
            "_collapse_same_title_adds(options)" in seg2
            and seg2.find("_collapse_same_title_adds(options)") < seg2.find("picks = []"), "")
    i3 = mn.find("def _collapse_same_title_adds(")
    seg3 = mn[i3:mn.find("\ndef ", i3 + 10)]
    c.check("S6 收敛只吃 `todo_add`（这一段里出现 todo_remove 就是越界）",
            '"todo_add"' in seg3 and "todo_remove" not in seg3, "")
    c.check("S7 卡由既有的 `build_todo_slots` 造 ⇒ 不新开 kind（前端/暂存都不用认新的）",
            "build_todo_slots(" in seg3 and '"kind":' not in seg3, "")
    c.check("S8 同名条件写在分组键上（按 `o.get(\"title\")` 归堆、少于两条不收），"
            "不是「整轮全合并」——判据钉在**键本身**，钉 `groups.setdefault(` 这种外壳"
            "换个键名照样绿（V3 实测就是这么漏的）",
            "groups.setdefault((o.get(\"title\")" in seg3 and "len(members) < 2" in seg3, "")
    n_call = mn.count("_remember_todo_options(session_id, ")
    n_opt = mn.count('"options": _remember_todo_options(session_id, ')
    c.check("S9 两个出口都把这个函数的**返回值**挂进 options（渲染与暂存同源的接线证据；"
            "只调而不用返回值 = 屏幕两张、系统一张）",
            n_call == 2 and n_opt == 2, f"调用 {n_call} 处 / 挂进 options {n_opt} 处")
    c.check("S10 前端那一支没被改成「只渲染最后一张」（单据 §四.2 明令不做："
            "丢信息，且和暂存的覆盖语义继续错位）",
            'options.filter(o => o.kind === "todo_add")' in read("app/static/student.html"), "")
    return c.summary("S 批（静态接线）")


if __name__ == "__main__":
    batch_a_date()
    batch_b_e2e()
    batch_c_two_rulers()
    batch_d_collapse()
    batch_s_static()
