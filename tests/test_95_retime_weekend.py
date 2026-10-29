"""#82 的护栏：改期认「周末 / 双休」—— 他说了范围，就不许再把「想改到哪天」问第二遍。

现场（施工单 §0.1）：第一轮没给新时间 → #81 的追问（念旧值）是对的；第二轮他说了**周末**，
系统当没听见，**一字不差**又问一遍。根因是 `new_date` 那条推导链里没有「范围词」这一档，
「周末」掉进「没说日子 → 沿用旧日期」的兜底，再被 #81 的空改动判据拦下。

本批覆盖单据 §3.1（T1~T4）/ §3.2（P1~P5 正控，命门）/ §3.3（V1~V4 的靶子），另加若干
**实测出来的、单据没写但会咬人**的东西（理由写在每条注释里）：

  · 单据 T2 写的形状（`hint_title="游泳"`）**走不通** —— 那句的 `_pick_todo_name` 抠出的是残渣
    「你帮我想一个时间改改过去」（非空、非占位名）⇒ #77 的 hint 一个字都不接手。A3 把这个「救不到」
    钉成口径，不是漏测（要救得动那张词表 = 已挂账的事名残渣族）。
  · 截图那句**当初规则层不认**（`_RETIME_WORDS` 里没有「改改」），全靠语义层判成 retime_todo 才进这一支
    （单据那行轨迹就写着「依据：语义层」）⇒ 端到端当时用的是「周末改一下」。**#82 追记 §1.1 补了「改改」**，
    但只稳住**判定**、稳不住**事名**（F5/F6 钉住：三行残渣一个字没变）；两条路各留一条（D2 规则层、D6 只有语义层）。
  · 候选日里可能出现**今天**，而 `pick_free_slot` 不传 `not_before` 时实测挑得出「今天 08:00-09:30」
    这种**已经过去**的段（B 批）。这一刀不只修周末：「把游泳挪到今天」同一支、同一个洞。
  · 全排不下那一支**不能**沿用 #81 那句追问（那就是本单要治的病本身）。当时新长了一个 `weekend_full`，
    **#82 追记 §1.3 把它并进 `reason`**：`no_slot`（说了日子、那天满了）与 `no_time`（没有可用的新时间）
    一套机制、**每一处**出口都要标（F4），main 按 `reason` 选话术（E3 把契约两端成对钉住）。
    **#86 追记**：`auto_slot` 挑不出时段时又长了第五处 `no_slot` ⇒ E3 / E3-c / E5 那三根钉死的
    "四个出口 / 两处调用"总数全被合法改动顶红，已按「换性质不换更大的数」改口（每条注释里写了
    原来钉什么、现在拦什么、以及换成性质之后还剩哪根轴防空判据）。
  · `_pick_date` 的正则**一个字没改**（E1 钉着）：它二十几处调用方把返回值当「确定的一天」用。

⚠️ 自省三条：正控句子一律按夹具真实值挑（C6 最初写成「约会挪到周五」而约会就在周五，自己就是空改动）；
   日期全用相对值（挂账 #69 跨天漂移）；「今天是不是周末」必须**打桩**才测得到 —— `pin_now` 只钉时:分、
   钉不了日期，所以本批自带 `stub_today`（借 test_91 那个写法）。
"""
import ast
import datetime
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, patch_llm, pin_now, sandbox,   # noqa: E402
                      seed_todos, write_timetable)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WD = "周一 周二 周三 周四 周五 周六 周日".split()
WEEKEND = ("周六", "周日")
# 课表只占周三（周末留空）⇒「周末挑得出日子」这件事不依赖本机真实课表
COURSE = [{"day": 3, "start": "10:00", "end": "11:30", "course": "专业课", "location": "A302"}]


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def _iso(off=0):
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()


def wd(date):
    if not date:
        return ""
    return WD[datetime.date.fromisoformat(date).weekday()]


def sat(off=0):
    """最近那个周六 + `off` 天（与 planner 里 `(5 - weekday) % 7` 同一把尺子）。"""
    base = datetime.date.today()
    return (base + datetime.timedelta(days=(5 - base.weekday()) % 7 + off)).isoformat()


def swim(title="游泳", date=None, start="15:00", end="16:00"):
    date = date or _iso(0)
    return {"title": title, "date": date, "weekday": wd(date),
            "start": start, "end": end}


def blocker(date):
    """把某一天的 07:00-22:00 整段占掉（标题带日期 ⇒ 不会被名字匹配撞上）。"""
    return {"title": "占用" + date.replace("-", ""), "date": date, "weekday": wd(date),
            "start": "07:00", "end": "22:00"}


def count_title(title):
    from app.store import list_todos
    return len([x for x in list_todos() if (x.get("title") or "") == title])


def stub_today(day, fn):
    """把 planner 眼里的「今天」换成 `day`（出来必还原）—— `pin_now` 钉不了日期。"""
    from app.modules import planner
    saved = planner.clock.today
    planner.clock.today = lambda: day
    try:
        return fn()
    finally:
        planner.clock.today = saved


def ask(cli, msg, sid):
    return cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                       "module": "planner"}).json()


def cards(d):
    return [o for o in (d.get("options") or []) if isinstance(o, dict)]


def retime_src():
    """`build_retime_existing_proposal` 的函数体源码（AST 定位，不靠同名文本）。"""
    src = read("app/modules/planner.py")
    lines = src.splitlines()
    for n in ast.parse(src).body:
        if isinstance(n, ast.FunctionDef) and n.name == "build_retime_existing_proposal":
            return "\n".join(lines[n.lineno - 1:n.end_lineno])
    return ""


# --------------------------------------------------------------------- A 函数级
def batch_a_weekend():
    print("\n=== A 批 · 改期认周末（函数级）===")
    c = Checker()
    from app.modules import planner as P
    with sandbox(), pin_now(9, 0):
        seed_todos([swim()])
        r = P.build_retime_existing_proposal("帮我把游泳改到周末")
        p = r.get("proposal") or {}
        c.check("A1 T1「帮我把游泳改到周末」→ ok，且挑出来的日子真是周六/周日",
                r.get("status") == "ok" and wd(p.get("date", "")) in WEEKEND,
                str((r.get("status"), p.get("date"), wd(p.get("date", "")))))
        c.check("A1-bis 那张卡带着 `old_todo_id`（改期是删旧+加新，链路不能断），"
                "且时刻齐全（`_pick_date` 认不出周末不等于可以不报时刻）",
                bool(p.get("old_todo_id")) and bool(p.get("start")) and bool(p.get("end")),
                str((p.get("old_todo_id"), p.get("start"), p.get("end"))))

        r2 = P.build_retime_existing_proposal("你帮我想一个周末的时间改改过去", {"title": "游泳"})
        p2 = r2.get("proposal") or {}
        c.check("A2 T2 截图那句 + 语义层给了名字 → **出卡**（§0.1 第二轮该发生的事）",
                r2.get("status") == "ok" and p2.get("title") == "游泳"
                and wd(p2.get("date", "")) in WEEKEND,
                str((r2.get("status"), p2.get("title"), p2.get("date"))))

        r3 = P.build_retime_existing_proposal("你帮我想一个周末的时间改改过去", None, "游泳")
        c.check("A3 同一句改走 `hint_title` 那一形状 → **仍是 empty**（口径，不是漏测）："
                "`_pick_todo_name` 抠出的是残渣（非空、非占位名）⇒ #77 的 hint 完全不接手。"
                "要救它得动那张词表（挂账：事名残渣族，#77 §四.2 禁改）",
                r3.get("status") == "empty",
                str((r3.get("status"), P._pick_todo_name("你帮我想一个时间改改过去"))))

        r4 = P.build_retime_existing_proposal("把游泳挪到双休")
        p4 = r4.get("proposal") or {}
        c.check("A4 T4「双休」同样认（`_WEEKEND_WORDS` 两个词都要接住，不许只认「周末」）",
                r4.get("status") == "ok" and wd(p4.get("date", "")) in WEEKEND,
                str((r4.get("status"), p4.get("date"))))

        r5 = P.build_retime_existing_proposal("周末改一下", None, "游泳")
        p5 = r5.get("proposal") or {}
        c.check("A5「周末改一下」+ hint=游泳 → ok（#77 的上下文继承 × #82 的周末档，两件得同时成立）",
                r5.get("status") == "ok" and p5.get("title") == "游泳"
                and wd(p5.get("date", "")) in WEEKEND,
                str((r5.get("status"), p5.get("title"), p5.get("date"))))

        seed_todos([swim()] + [blocker(sat(o)) for o in (0, 1, 7, 8)])
        r6 = P.build_retime_existing_proposal("帮我把游泳改到周末")
        c.check("A6 T3 四个候选周末全排不下 → unclear + `reason=no_slot` + `slot_label` 说的是周末"
                " + 带着那条旧待办（#82 追记 §1.3：`weekend_full` 已并进 `no_slot`，不许两套机制）",
                r6.get("status") == "unclear" and r6.get("reason") == "no_slot"
                and "weekend_full" not in r6
                and "周六" in (r6.get("slot_label") or "")
                and (r6.get("todos") or [{}])[0].get("title") == "游泳",
                str((r6.get("status"), r6.get("reason"), r6.get("slot_label"),
                     [x.get("title") for x in (r6.get("todos") or [])])))

        seed_todos([swim(), blocker(sat(0)), blocker(sat(1))])
        r7 = P.build_retime_existing_proposal("帮我把游泳改到周末")
        c.check("A7 V3 靶子：只本周末满、下周末空 → 挑到**下周六**（候选真给了两个周末）",
                (r7.get("proposal") or {}).get("date") == sat(7),
                str(((r7.get("proposal") or {}).get("date"), sat(7))))

        seed_todos([swim(), blocker(sat(0))])
        r8 = P.build_retime_existing_proposal("帮我把游泳改到周末")
        c.check("A8 V4 靶子：周六满、周日空 → 挑到**周日**（是逐个试过，不是写死周六）",
                (r8.get("proposal") or {}).get("date") == sat(1),
                str(((r8.get("proposal") or {}).get("date"), sat(1))))

        # ★ 这条原本是"口径登记"（单据 §1.2 的裁决把它改了）：那条待办本来就排在周六、
        #   再说「改到周末」⇒ 旧日期**排到最后**而不是删掉 ⇒ 优先换到周日，周日满了才回周六换个时段。
        seed_todos([swim(date=sat(0))])
        r9 = P.build_retime_existing_proposal("把游泳改到周末")
        p9 = r9.get("proposal") or {}
        c.check("A9（#82 追记 §1.2 改口）待办本来就在周六 + 说「改到周末」→ 出**周日**的卡"
                "（旧日期不优先，但没被删掉）",
                r9.get("status") == "ok" and p9.get("date") == sat(1)
                and p9.get("start") != swim(date=sat(0))["start"],
                str((r9.get("status"), p9.get("date"), sat(1), p9.get("start"))))

        today = datetime.date.today()
        sun = today + datetime.timedelta(days=(6 - today.weekday()) % 7)
        seed_todos([swim(date=(sun + datetime.timedelta(days=1)).isoformat())])
        r10 = stub_today(sun, lambda: P.build_retime_existing_proposal("帮我把游泳改到周末"))
        p10 = r10.get("proposal") or {}
        c.check("A10 打桩今天=周日：`(5 - weekday()) % 7` 少了 `% 7` 就会算出**过去的本周六**"
                "（V2 靶子）⇒ 挑到的日子不许早于今天，且仍是周末",
                r10.get("status") == "ok" and p10.get("date", "") >= sun.isoformat()
                and wd(p10.get("date", "")) in WEEKEND,
                str((r10.get("status"), p10.get("date"), sun.isoformat())))

        # ★ A10 在 09:00 那一档**挡不住 V2**（实测：去掉 `% 7` 后今天=周日仍挑今天 ⇒ 绿）。
        #   要让"算出过去的本周六"这条坏写法必须现形，得把今天挡掉：23:30 时今天没有 90 分钟的段，
        #   循环才会走到第二颗候选 —— 少了 `% 7` 那里就是**昨天（周六）**。
        with pin_now(23, 30):
            r12 = stub_today(sun, lambda: P.build_retime_existing_proposal("帮我把游泳改到周末"))
            p12 = r12.get("proposal") or {}
            c.check("A10-bis 今天=周日、23:30：今天排不下 ⇒ 往后挪，**不许回头挑本周六（昨天）**"
                    "（V2 的真牙：`(5 - weekday())` 不带 `% 7` 时 sat0 = 昨天）",
                    r12.get("status") == "ok" and p12.get("date", "") > sun.isoformat()
                    and wd(p12.get("date", "")) in WEEKEND,
                    str((r12.get("status"), p12.get("date"), sun.isoformat())))
    return c.summary("A 批（改期认周末）")


# ------------------------------------------------------------------- B 今天的缓冲
def batch_b_today_buffer():
    print("\n=== B 批 · 候选日含「今天」时的缓冲（not_before）===")
    c = Checker()
    from app.modules import planner as P
    real = datetime.date.today()
    sat_date = real + datetime.timedelta(days=(5 - real.weekday()) % 7)
    sun_date = sat_date + datetime.timedelta(days=1)
    for tag, day in (("周六", sat_date), ("周日", sun_date)):
        with sandbox():
            seed_todos([swim(date=(day + datetime.timedelta(days=3)).isoformat())])
            with pin_now(9, 0):
                r = stub_today(day, lambda: P.build_retime_existing_proposal("帮我把游泳改到周末"))
                p = r.get("proposal") or {}
                c.check(f"B1 今天={tag}、09:00 问「改到周末」→ 挑出的那段不许早于 09:30"
                        "（不传 `not_before` 时实测挑得出「今天 08:00-09:30」= 一张已经过去的卡）",
                        r.get("status") == "ok" and p.get("start", "") >= "09:30",
                        str((r.get("status"), p.get("date"), p.get("start"))))
            with pin_now(23, 30):
                r2 = stub_today(day, lambda: P.build_retime_existing_proposal("帮我把游泳改到周末"))
                p2 = r2.get("proposal") or {}
                c.check(f"B2 今天={tag}、23:30 问「改到周末」→ **不选今天**（今天已没有 90 分钟的段）："
                        "往后挪到下一个周末日子",
                        r2.get("status") == "ok" and p2.get("date") != day.isoformat(),
                        str((r2.get("status"), p2.get("date"), day.isoformat())))

    with sandbox(), pin_now(9, 0):
        seed_todos([swim(date=_iso(3))])
        r3 = P.build_retime_existing_proposal("把游泳挪到今天")
        p3 = r3.get("proposal") or {}
        c.check("B3 正控（这一刀不只修周末）：「把游泳挪到今天」在 09:00 也只许挑 09:30 之后"
                " —— 同一个 `pick_free_slot` 调用点、同一个洞",
                r3.get("status") == "ok" and p3.get("date") == _iso(0)
                and p3.get("start", "") >= "09:30",
                str((r3.get("status"), p3.get("date"), p3.get("start"))))
        r4 = P.build_retime_existing_proposal("把游泳挪到明天")
        p4 = r4.get("proposal") or {}
        c.check("B4 对照：不是今天的那一档**不受缓冲影响**（明天 08:00 照样排得进 —— "
                "缓冲只针对『今天』，不许写成整天压低）",
                r4.get("status") == "ok" and p4.get("date") == _iso(1)
                and p4.get("start", "") >= "08:00",
                str((r4.get("status"), p4.get("date"), p4.get("start"))))
    return c.summary("B 批（今天的缓冲）")


# ----------------------------------------------------------------------- C 正控
def batch_c_positive():
    print("\n=== C 批 · 正控（日期推导链没被插坏 + #81/#75 都还活着）===")
    c = Checker()
    from app.modules import planner as P
    with sandbox(), pin_now(9, 0):
        seed_todos([swim(date=_iso(3))])
        r1 = P.build_retime_existing_proposal("把游泳挪到周五")
        p1 = r1.get("proposal") or {}
        c.check("P1「把游泳挪到周五」→ ok 且落在**周五**（周末档不许抢具体日子的活）",
                r1.get("status") == "ok" and wd(p1.get("date", "")) == "周五",
                str((r1.get("status"), p1.get("date"), wd(p1.get("date", "")))))

        r2 = P.build_retime_existing_proposal("游泳改到 20:00")
        p2 = r2.get("proposal") or {}
        c.check("P2「游泳改到 20:00」→ ok，日子**不变**、钟点 20:00（只改时刻那一族照旧）",
                r2.get("status") == "ok" and p2.get("date") == _iso(3)
                and p2.get("start") == "20:00",
                str((r2.get("status"), p2.get("date"), p2.get("start"))))

        seed_todos([swim(title="做题", date=_iso(0), start="14:00", end="16:00")])
        r3 = P.build_retime_existing_proposal("把做题挪到周日")
        p3 = r3.get("proposal") or {}
        c.check("P3「把做题挪到周日」→ ok 且日子真的换了（#77 的 A12 同族，不许被本单改成 unclear）",
                r3.get("status") == "ok" and p3.get("date") != _iso(0)
                and wd(p3.get("date", "")) == "周日",
                str((r3.get("status"), p3.get("date"), wd(p3.get("date", "")))))

        seed_todos([swim()])
        r4 = P.build_retime_existing_proposal("换个时间", None, "游泳")
        c.check("P4 空改动（#81）仍走 unclear + `reason=no_time`、带着 old_label —— 本单不许碰 #81 那一档"
                "（⚠️ 原来钉的是\"没有 weekend_full\"，那键并掉之后就永远为真 = 空判据）",
                r4.get("status") == "unclear" and r4.get("reason") == "no_time"
                and bool(r4.get("old_label")),
                str((r4.get("status"), r4.get("reason"))))

        r5 = P.build_retime_existing_proposal("把游泳改到下午")
        c.check("C6 口径登记（#82 追记 §2.1 拍板：**不做**）：只报偏好（下午）→ 仍算空改动、"
                "`reason=no_time`。判据是\"会不会死循环\"——追问「几点」他答得出「下午三点」，"
                "而追问「哪天」他只能再说一遍周末",
                r5.get("status") == "unclear" and r5.get("reason") == "no_time",
                str((r5.get("status"), r5.get("reason"))))

        seed_todos([])
        write_timetable(COURSE)
        card = P.todo_slots_proposal("周末打篮球")
        got = (card or {}).get("slots") or []
        c.check("P5 #75 那条路一个字没动：「周末打篮球」的候选仍**全是周六/周日**"
                "（本单只往改期那条链里插了一段）",
                bool(got) and all(wd(s["date"]) in WEEKEND for s in got),
                str([(s["date"], wd(s["date"])) for s in got][:5]))
    return c.summary("C 批（正控）")


# ------------------------------------------------------------------- D 端到端
def batch_d_e2e():
    print("\n=== D 批 · 端到端（两轮 → 第二轮出卡 / 全排不下换句话）===")
    c = Checker()
    with sandbox():
        write_timetable(COURSE)
        cli = make_client()
        with pin_now(9, 0):
            seed_todos([swim()])
            sid = "b82-d1"
            d1 = ask(cli, "游泳时间撞了，改一下时间", sid)
            ans1 = d1.get("answer") or ""
            c.check("D1 第一轮（没给新时间）→ 不出卡、仍是 #81 那句念旧值的追问（本单没换它）",
                    not cards(d1) and "现在是" in ans1 and "想改到哪天" in ans1,
                    ans1.replace("\n", " ⏎ ")[:110])

            d2 = ask(cli, "周末改一下", sid)
            k2 = cards(d2)
            c.check("D2 第二轮说了「周末」→ **出一张改期卡**（不再是同一句追问）："
                    "title=游泳、日子在周六/周日、带 old_todo_id",
                    len(k2) == 1 and k2[0].get("title") == "游泳"
                    and wd(k2[0].get("date", "")) in WEEKEND and bool(k2[0].get("old_todo_id")),
                    str([(x.get("title"), x.get("date"), x.get("old_todo_id")) for x in k2])[:130])
            c.check("D2-bis 屏幕那句话里不许再出现「想改到哪天」（他已经说了周末）",
                    "想改到哪天" not in (d2.get("answer") or ""),
                    (d2.get("answer") or "").replace("\n", " ⏎ ")[:100])
            c.check("D2-c 只出提案、一个字节都不写库（库里仍是那一条游泳）",
                    count_title("游泳") == 1, str(count_title("游泳")))

            seed_todos([swim()] + [blocker(sat(o)) for o in (0, 1, 7, 8)])
            sid3 = "b82-d3"
            ask(cli, "游泳时间撞了，改一下时间", sid3)
            d3 = ask(cli, "周末改一下", sid3)
            ans3 = d3.get("answer") or ""
            c.check("D3 四个候选周末全满 → 不出卡，话术**如实说排不下**（含「排不下」）",
                    not cards(d3) and "排不下" in ans3, ans3.replace("\n", " ⏎ ")[:120])
            c.check("D3-bis 这一支不许把 #81 那句「想改到哪天」搬过来（同一句问两遍就是本单的病）",
                    "想改到哪天" not in ans3, ans3.replace("\n", " ⏎ ")[:100])
            c.check("D3-c 轨迹 phase 是「改期：目标日子排不下」（#82 追记并了周末那一套），且不写库、不挂暂存",
                    any("目标日子排不下" in str(t.get("phase") or "") for t in (d3.get("trace") or []))
                    and d3.get("awaiting_choice") is False and count_title("游泳") == 1,
                    str([t.get("phase") for t in (d3.get("trace") or [])])[:110])

            seed_todos([swim()])
            sid4 = "b82-d4"
            d4 = ask(cli, "换个时间", sid4)
            ans4 = d4.get("answer") or ""
            c.check("D4 正控：没有周末词的那一句仍走 #81 那一支（phase 还是「改期新时间缺失」）"
                    " —— 三种说法各管各的，不许被本单合并成一句",
                    "改期新时间缺失" in str([t.get("phase") for t in (d4.get("trace") or [])])
                    and "想改到哪天" in ans4 and "排不下" not in ans4,
                    str([t.get("phase") for t in (d4.get("trace") or [])])[:110])
            # ★ #82 追记 §1.3 的屏幕证据：说了日子、那天满了 ⇒ 不许再说「没听清」
            seed_todos([swim(date=_iso(0)), blocker(_iso(1))])
            d5 = ask(cli, "把游泳改到明天", "b82-d5")
            ans5 = d5.get("answer") or ""
            c.check("D5 §1.3 「把游泳改到明天」而明天满 → 话术说「排不下」，"
                    "**不许**出现「没听清」（他说了日子，那句是当众说错话）",
                    not cards(d5) and "排不下" in ans5 and "没听清" not in ans5,
                    ans5.replace("\n", " ⏎ ")[:120])
            c.check("D5-bis 这一支仍是确定性（不挂卡、不写库），phase 是「改期：目标日子排不下」",
                    d5.get("awaiting_choice") is False and count_title("游泳") == 1
                    and any("目标日子排不下" in str(x.get("phase") or "")
                            for x in (d5.get("trace") or [])),
                    str([x.get("phase") for x in (d5.get("trace") or [])])[:110])

            # ★ 单据 §四.1：规则层认得的、与**只有语义层认得**的，两条链路各要有一条
            import json as _json
            seed_todos([swim(date=_iso(0))])
            msg6 = "周六的时间不太合适"
            from app.modules.planner import wants_retime_todo as _wrt
            c.check("D6-a 前置条件先钉住：这句话**规则层不认**（否则 D6 测的就不是语义层那条路）",
                    _wrt(msg6) is False, str(_wrt(msg6)))
            restore = patch_llm(_json.dumps(
                {"intent": "retime_todo", "module": "planner", "title": "游泳",
                 "confidence": 0.92}, ensure_ascii=False))
            try:
                d6 = ask(cli, msg6, "b82-d6")
                k6 = cards(d6)
                c.check("D6 §四.1 只有语义层认得（判成 retime_todo 并给了事名）也走得通 → 出周末的卡",
                        len(k6) == 1 and k6[0].get("title") == "游泳"
                        and wd(k6[0].get("date", "")) in WEEKEND,
                        str([(x.get("title"), x.get("date")) for x in k6])[:110])
            finally:
                restore()
    return c.summary("D 批（端到端）")


# ------------------------------------------------------------------- E 静态接线
def batch_e_static():
    print("\n=== E 批 · 静态接线（判据的位置 / 成对 / 不许改的地方）===")
    c = Checker()
    pl = read("app/modules/planner.py")
    mn = read("app/main.py")
    body = retime_src()

    from app.modules import planner as P
    c.check("E1 没改公共函数的语义：`_WEEKDAY_RE` 的字符集里**不许出现「末」**"
            "（单据 §2.1：它二十几处调用方把返回值当「确定的一天」用）",
            "末" not in P._WEEKDAY_RE.pattern, P._WEEKDAY_RE.pattern)
    c.check("E1-bis 正控：同一条判据换个字就命中（证明 E1 不是永远为真的死断言）",
            "五" in P._WEEKDAY_RE.pattern, P._WEEKDAY_RE.pattern)

    i_word = body.find("for w in _WEEKEND_WORDS)")
    i_fallback = body.find("new_date = old_date")
    c.check("E2 位置判据：周末档必须在「沿用旧日期」那句**之前**"
            "（放后面 `new_date` 已被占上，永远走不到 —— 单据 §五.1）",
            0 < i_word < i_fallback, str((i_word, i_fallback)))
    c.check("E2-bis 周末档确实长在 `build_retime_existing_proposal` 函数体里"
            "（AST 取体，不是「文件里有这几个字」）",
            i_word > 0 and body.startswith("def build_retime_existing_proposal"), str(i_word))

    tree = ast.parse(body)
    # ★ unclear 出口按 AST 数，不按子串数：函数 docstring 里就写着 status: unclear 那种形状，
    #   子串判据会随它带不带空格漂（计数判据被下一个合法改动顶成假红那一族）。
    def _ret_dict(nd):
        if isinstance(nd, ast.Return) and isinstance(nd.value, ast.Dict):
            return {(k.value if isinstance(k, ast.Constant) else "?"):
                    (v.value if isinstance(v, ast.Constant) else "<expr>")
                    for k, v in zip(nd.value.keys, nd.value.values)}
        return {}
    exits = [d for d in (_ret_dict(n) for n in ast.walk(tree)) if d.get("status") == "unclear"]
    n_exit = len(exits)
    n_slot = sum(1 for d in exits if d.get("reason") == "no_slot")
    n_time = sum(1 for d in exits if d.get("reason") == "no_time")
    n_mn = len(re.findall(r'get\("reason"\)', mn))
    c.check("E3 契约两端一起钉（#62 那条）：planner 的**每一处** unclear 出口都带 reason、"
            "且 reason 只有 no_slot / no_time 两种取值（no_slot = 说了日子、那几天排不下；"
            "no_time = 没有可用的新时间），main 里恰**一处**读它 —— 单据 §1.3 技术要求 1："
            "不许只给新来源标、老来源留空。"
            "⚠️ #86 改口：原来钉的是「四个出口 = no_slot 2 + no_time 2」，#86 给 `auto_slot` "
            "挑不出时段又加了第五处 no_slot ⇒ 那个总数被合法改动顶红。换成性质"
            "「标了 reason 的两类之和 == 出口总数」：它照样拦『新加一处没标 reason 的出口』"
            "（那会让左边少于右边），也不拦下一位施工者加第六处。"
            "`>= 2` 是下限轴（防『出口被删到看不见、等式两边同为零而空过』），不是上限",
            n_slot >= 2 and n_time >= 2 and n_slot + n_time == n_exit and n_mn == 1,
            str((n_slot, n_time, n_exit, n_mn)))
    c.check("E3-bis 老调用方拿不到 reason 时不许 KeyError：main 那行必须写成带默认值的取法"
            "（单据 §1.3 技术要求 2）",
            '_rt.get("reason") or "no_time"' in mn,
            str('_rt.get("reason") or "no_time"' in mn))
    planted = {"status": "unclear", "weekend_full": "<expr>"}   # E3-c 的正控样本，见那条注释
    c.check("E3-c 别再长第二套：所有 unclear 出口里**没有任何一个**带 weekend_full 这个键，"
            "main 里也不许出现这个名字（追记 §1.3：它就是 no_slot 的一个特例）"
            "⚠️ 原来钉的是「planner 函数体里没有 weekend_full 这几个字」—— 机制并掉之后 "
            "docstring 里的历史说明照样含这几个字，那条判据从此看打字脸色；判据要钉在**返回键**上，"
            "并带 `len(exits)==4` 这根轴防自己变空判据。#86 把第五处出口加进来顶红了那根轴 ⇒ "
            "换成一只**正控**：同一句判据喂一棵故意长着 weekend_full 的树必须判 False —— "
            "「扫得到这个键」从此是被证明的，不再借出口总数",
            all("weekend_full" not in d for d in exits) and "weekend_full" not in mn
            and bool(exits) and not all("weekend_full" not in d for d in [planted]),
            str((len(exits), sorted({k for d in exits for k in d}), "weekend_full" in mn)))

    c.check("E4 词表不许抄第二份：周末档判的是 `_WEEKEND_WORDS`，函数体里不许出现「周末」的字面量元组"
            "（与 #75 的 S2c 同族；以后加一颗「礼拜末」只该改一处）",
            "_WEEKEND_WORDS" in body and '("周末", "双休")' not in body,
            str(("_WEEKEND_WORDS" in body, '("周末", "双休")' in body)))
    c.check("E4-bis `_WEEKEND_WORDS` 全仓只有**一处定义**（两处定义迟早走样）",
            len(re.findall(r"^_WEEKEND_WORDS\s*=", pl, re.M)) == 1,
            str(len(re.findall(r"^_WEEKEND_WORDS\s*=", pl, re.M))))

    pfs = [n for n in ast.walk(tree)      # tree 在 E3 那里就解析好了（同一份 AST 两处用）
           if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "pick_free_slot"]
    nb = [n for n in pfs if any(k.arg == "not_before" for k in n.keywords)]
    c.check("E5 这一函数里**每一处** `pick_free_slot` 都得带 `not_before`"
            "（挑日子那次 + 定时刻那次 + #86 的 `auto_slot` 那次）：少任何一处都会出「今天 08:00」"
            "那种过去的卡。⚠️ #86 改口：原来钉 `调用数 == 2 且带 not_before == 2`，"
            "新分支合法加了第三处 ⇒ 换成「带 not_before 的处数 == 调用总数」+ 一根 `>= 2` 下限轴"
            "（下限只拦『调用点被删到看不见』，不拦以后加第四处）",
            len(nb) == len(pfs) and len(pfs) >= 2, str((len(pfs), len(nb))))

    bare = re.compile(r"(?<![\w.])timedelta\(")
    c.check("E6 写法判据（按函数体算，不按全文算）：`build_retime_existing_proposal` 里"
            "用的是 `datetime.timedelta(`，不许出现裸 `timedelta(` —— 这一支没有局部 "
            "`from datetime import`，写错就是运行期 NameError（单据 §五.3）。"
            "⚠️ 全文级同判据**不成立**：`list_todos_range`（#63）自带局部 import，"
            "那里裸写是对的 —— 所以这条只管本函数（同名函数两本账，#71 那条）",
            not bare.search(body) and body.count("datetime.timedelta(") >= 1
            and "from datetime import" not in body,
            str((len(bare.findall(body)), body.count("datetime.timedelta("))))
    c.check("E6-bis 正控：同一个正则喂一句「x = timedelta(days=1)」必须命中"
            "（否则 E6 只是永远为真的空判据）",
            bool(bare.search("x = timedelta(days=1)")), "正则本身是活的")

    head = mn.find('if _rt_reason == "no_slot":')
    nxt = mn.find("            elif", head)
    blk = mn[head:nxt] if head >= 0 and nxt > head else ""
    blk_ask = blk.split("_rt_why")[0]
    c.check("E7 main 的 no_slot 那一支：**贴学生的那两句**（_rt_ask 两种形状）都说「排不下」，"
            "且那一段里**不许**出现「没听清」（那是「你压根没说」的暗示，而他说了日子）。"
            "⚠️ 判据范围切到 _rt_why **之前**：那一行是写给施工者看的自律话（内容正是「不许说没听清」），"
            "拿整支做范围会让这条永远为红 —— 范围为什么这么切见 E7-ter",
            head >= 0 and bool(blk) and blk_ask.count("排不下") >= 2 and "没听清" not in blk_ask,
            blk_ask.replace("\n", " ⏎ ")[:110])
    c.check("E7-ter 正控：整支（含 _rt_why 那行自律话）**确实**含「没听清」"
            "—— 证明 E7 的切分是有意义的范围限制，不是把判据悄悄放宽到永远为真",
            bool(blk) and "没听清" in blk and "没听清" not in blk_ask,
            str(("没听清" in blk, "没听清" in blk_ask)))
    c.check("E7-bis 话术里的日子来自 `slot_label`（函数知道自己试了哪些天，main 不许自己再拼一遍）",
            "slot_label" in blk and "_rt.get(\"slot_label\")" in blk, blk[:0] or "见上一行")

    zone = mn[mn.find("wants_retime_todo(message)"):]
    zone = zone[:zone.find("# 3c-ter)")]
    c.check("E8 三条 unclear 说法都不写库、也不挂暂存：这一族里 `save_pending` 只许出现一次"
            "（只在 ok 那一支挂卡）",
            zone.count("save_pending(") == 1, str(zone.count("save_pending(")))
    c.check("E8-bis 这一族里三个分支**各设各的 phase**，且 no_slot 那句不许和 no_time 共用一条"
            "（轨迹那行是给评委看的解释：行为对了它也能当众说错话）—— "
            "「改期新时间缺失」与「改期：目标日子排不下」都在，_rt_phase 赋值恰三处，"
            "trace 里用的是变量不是又一份字面量",
            "改期新时间缺失" in zone and "改期：目标日子排不下" in zone
            and zone.count('_rt_phase = "') == 3 and '"phase": _rt_phase' in zone,
            str([zone.count('_rt_phase = "'), "改期新时间缺失" in zone,
                 "改期：目标日子排不下" in zone, '"phase": _rt_phase' in zone]))
    return c.summary("E 批（静态接线）")



# ------------------------------------------------- F 批（#82 追记三条拍板）
def batch_f_verdicts():
    print("\n=== F 批 · #82 追记：旧日期排到最后 / reason 对上来源 / 「改改」只稳判定 ===")
    c = Checker()
    from app.modules import planner as P
    real = datetime.date.today()
    sat_date = real + datetime.timedelta(days=(5 - real.weekday()) % 7)
    sun_date = sat_date + datetime.timedelta(days=1)
    sd, nd = sat_date.isoformat(), sun_date.isoformat()

    with sandbox(), pin_now(9, 0):
        # ---- §1.2 旧日期**排到最后**（不是删掉）----
        seed_todos([swim(date=sd)])
        r1 = stub_today(sat_date, lambda: P.build_retime_existing_proposal("把游泳改到周末"))
        p1 = r1.get("proposal") or {}
        c.check("F1 §1.2 今天=周六、待办就在周六、周日空着 → 出**周日**的卡"
                "（旧日期排到最后；把那段排序删掉，第一颗候选就是今天=旧日期 ⇒ 空改动、又一句追问）",
                r1.get("status") == "ok" and p1.get("date") == nd,
                str((r1.get("status"), p1.get("date"), nd)))

        seed_todos([swim(date=sd), blocker(nd), blocker(sat(7)), blocker(sat(8))])
        r2 = stub_today(sat_date, lambda: P.build_retime_existing_proposal("把游泳改到周末"))
        p2 = r2.get("proposal") or {}
        c.check("F2 §1.2 的另一半：周日 + 下周末全满、周六另有空档 → 回**周六换个时段**"
                "（不是「那几天都满了」）。这条要的是定时刻那一步也认周末档 —— 光排序做不到："
                "`new_date != old_date or _from_weekend` 少半边就落回旧时刻 ⇒ 空改动",
                r2.get("status") == "ok" and p2.get("date") == sd
                and p2.get("start") not in ("", "15:00"),
                str((r2.get("status"), p2.get("date"), p2.get("start"))))

        # ---- §1.3 说了日子、那天满了：reason / slot_label 要对上来源 ----
        seed_todos([swim(date=_iso(0)), blocker(_iso(1))])
        r3 = P.build_retime_existing_proposal("把游泳改到明天")
        c.check("F3 §1.3 「把游泳改到明天」而明天满 → `reason=no_slot` + `slot_label` 就是那个日子"
                "（以前是光秃秃一个 unclear `todos:[]` ⇒ main 只能说「没听清」= 当众说错话）",
                r3.get("status") == "unclear" and r3.get("reason") == "no_slot"
                and _iso(1) in (r3.get("slot_label") or ""),
                str((r3.get("reason"), r3.get("slot_label"))))

        # ---- §1.1 「改改」：稳住判定，稳不住事名 ----
        c.check("F4 §1.1 加词后规则层认得「改改时间」——而它**照样**抠不出事名（还是残渣「改改时间」）"
                " ⇒ 这条钉的是「加词只解决判定，别以为修好了」（单据 §1.1 那张表）",
                P.wants_retime_todo("改改时间") is True
                and P._pick_todo_name("改改时间") == "改改时间",
                str((P.wants_retime_todo("改改时间"), P._pick_todo_name("改改时间"))))
        c.check("F4-bis 单据 §1.1 要的正控：「改改错别字」也 True —— **这是有意的**，"
                "词表只判「这句是不是改期」，不负责区分语义；别在测试里把它写成「不误伤」",
                P.wants_retime_todo("改改错别字") is True, str(P.wants_retime_todo("改改错别字")))
        seed_todos([swim(date=_iso(0))])
        r5 = P.build_retime_existing_proposal("周末改改", None, "游泳")
        c.check("F5 §1.1 的诚实出口：「周末改改」+ hint=游泳 → 仍 **empty**"
                "（name 抠出残渣「改改」非空 ⇒ hint 不接手）。单据写死的「不是修好，是让失败方式变稳变诚实」"
                "就在这一条上；要救得动 `_pick_todo_name` 那张词表（事名残渣族，另单）",
                r5.get("status") == "empty",
                str((r5.get("status"), P._pick_todo_name("周末改改"))))

        # 加词只动判定：三行残渣一个字没变（单据 §1.1 那三行，本轮复跑）
        c.check("F6 加词前后**事名一个字没变**（证明这次改的确实只有判定这一维）",
                P._pick_todo_name("你帮我想一个时间改改过去") == "你帮我想一个时间改改过去"
                and P._pick_todo_name("游泳时间撞了改时间") == "游泳时间撞了改时间",
                str((P._pick_todo_name("你帮我想一个时间改改过去"),
                     P._pick_todo_name("游泳时间撞了改时间"))))
    return c.summary("F 批（三条拍板）")


def main():
    batches = (("A 批（改期认周末）", batch_a_weekend),
               ("B 批（今天的缓冲）", batch_b_today_buffer),
               ("C 批（正控）", batch_c_positive),
               ("D 批（端到端）", batch_d_e2e),
               ("E 批（静态接线）", batch_e_static),
               ("F 批（三条拍板）", batch_f_verdicts))
    code = 0
    ran = 0
    for label, fn in batches:
        try:
            rc = fn()        # ★ 先把这一批**跑掉**再合并结果。写成 `code = code or fn()`
                             #   会在第一批变红后把后面几批整个短路掉（`or` 的副作用），
                             #   于是"红得比预期少"会被当成"牙不多"—— test_76 已成文的教训。
        except Exception as e:      # 一批崩不能冒充「别的批通过」
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
        ran += 1
    if ran != len(batches):
        print(f"  ❌ 只跑了 {ran}/{len(batches)} 批 ⇒ 有批被跳过，这份总账不算数")
        code = 1
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
