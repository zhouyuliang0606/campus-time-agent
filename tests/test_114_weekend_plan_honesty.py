"""#114 的护栏：「周末」要在整条加待办链上活着，装不满就别说「按你说的 X 排的」。

现场（单据 §〇，全部真机实测）：
  · 「周末安排两次打乒乓球一次三小时」→ 排到**周三** 08:00-10:05（125 分钟），
    卡上还写着「按你说的 3 小时 排的」——日子错、时长假，一次全占。
  · 学生纠正「周三是明天，我说的周末」→ 回答与上一轮**一字不差**（§0.7）：
    「明天」是学生用来指出我们排错了的词，`_pick_date` 却拿它把日子锁死。
  · 11/8 客观上有 10:30-14:00 的 210 分钟连续空档，`_day_slots` 却因为
    "候选必须整段落在一个档里"（phi=12:00/18:00）判成排不下 ⇒「这周末」被跳过。

五处修法（1.1→1.5）各有一批钉着；批次分工（跑变异时看**红的是哪几条名字**）：
  A 批 · 1.1 擦频次留住范围词：函数级三判 + 真链路 ①（含"加重周六"后首段就落这周末）。
  B 批 · 1.4 装不满说真话：备胎 210 分钟卡不许再写「按你说的 4 小时」（M4 的靶子）
         + 装得下时照写 + 没报时长就不许宣称。
  C 批 · 1.5 具体日子退让：§0.7 合并句（M5 的靶子）、candidate_slots 两侧正控、
         兜底路「平时和周末都行」= 没限制、「平时」那半没被改坏。
  D 批 · 1.2 档尾放开：跨档 10:30-13:30 出得来（M3 的靶子）+ 装得下时不许多跨（两条正控）。
  S 批 · 静态接线：补词函数挂在两处调用点、三处裸 `_FREQ_RE.sub` 原样、
         兜底路 14 天宽 + 两把尺子一起关、`picked_day` 双模式退让、`_auto_card` 无星号。

三条自省（写断言前先看，免得造出空判据）：
  1. **日期全打桩**：`stub_today` 把 planner 眼里的"今天"钉成未来的周二/周六
     （从真实"今天"推下周一再取，任何星期几跑都一样），`pin_now(9,0)` 同时钉住
     "此刻+30 分"缓冲 —— 不打桩，"首段是这周六"这类断言就是一条空判据。
  2. **课表/待办全自种**：`sandbox()` 是复制真实数据不是空库；部分装下那档
     靠 seed_todos 把四个周末日堵到只剩 14:30-18:00（210 分钟）一条缝。
  3. §0.7 的**两轮对话**真机形态（带 Key 走 mode=ai 卡）在无 Key 沙箱里到不了
     plan_todo_slot（规则层另一条分支把它接走了，且改前改后一字不差 = 既有行为，
     已用 HEAD 裸工树对照证实）⇒ 这里用单据 §0.7 自己的那张模块级读数表等价复现：
     合并句直接过真链路，判「卡不落 _pick_date 抓到的那天」。
"""
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, pin_now, sandbox,  # noqa: E402
                      seed_todos, write_timetable)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

WD = "周一 周二 周三 周四 周五 周六 周日".split()
WEEKEND = ("周六", "周日")

# 从真实"今天"往后推：NMON=下周一（永远在未来，任何星期几跑都成立），
# TUE=那周的周二（§0.7 现场：学生说「周三是明天」）、SAT=那个周末。
_TODAY = datetime.date.today()
_NMON = _TODAY + datetime.timedelta(days=(8 - _TODAY.isoweekday()))
TUE = _NMON + datetime.timedelta(days=1)
SAT = (_NMON + datetime.timedelta(days=5)).isoformat()
SUN = (_NMON + datetime.timedelta(days=6)).isoformat()
SAT2 = (_NMON + datetime.timedelta(days=12)).isoformat()
SUN2 = (_NMON + datetime.timedelta(days=13)).isoformat()
TOMORROW = (TUE + datetime.timedelta(days=1)).isoformat()      # 「周三是明天」的那天

BUSY = [{"day": 6, "start": "09:00", "end": "10:30", "course": "周六实验", "location": "实验楼"},
        {"day": 3, "start": "10:00", "end": "11:30", "course": "体育课", "location": "操场"}]
# 周六被堵到只剩 10:30-14:00 一条真空档（上午段 90 分 + 下午段 0 分）：
# 3 小时的事在任何单档里都装不下，只有 1.2 的"越过档尾"才接得住。
HEAVY_SAT = [{"day": 6, "start": "09:00", "end": "10:30", "course": "早课", "location": "x"},
             {"day": 6, "start": "14:00", "end": "22:00", "course": "满档", "location": "x"}]
# 部分装下：四个周末日各剩 14:30-18:00（210 分钟）一条缝，4 小时哪儿都装不满。
PARTIAL_TODOS = ([{"title": "占上午", "date": d, "start": "09:00", "end": "14:30"}
                  for d in (SAT, SUN, SAT2, SUN2)]
                 + [{"title": "占晚上", "date": d, "start": "18:00", "end": "22:00"}
                    for d in (SAT, SUN, SAT2, SUN2)])


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def wd(date):
    return WD[datetime.date.fromisoformat(date).weekday()]


def stub_today(day, fn):
    """把 planner 眼里的「今天」换成 `day`（只在这一段里有效，出来必还原）。"""
    from app.modules import planner
    saved = planner.clock.today
    planner.clock.today = lambda: day
    try:
        return fn()
    finally:
        planner.clock.today = saved


def chain(P, s):
    """真链路：mode 提案 → "你帮我挑"那一路（#114 的五处全在这条链上）。"""
    card = P.todo_mode_proposal(s)
    return P.mode_to_card(card, "ai") if card else None


def batch_a_freq():
    c = Checker()
    from app.modules import planner as P
    print("\n=== A 批 · 1.1 擦频次留住范围词 ===")
    s1 = "周末安排两次打乒乓球一次三小时"
    out = P._strip_freq_keep_range(s1)
    c.check("A1 「周末…两次…三小时」擦完频次后「周末」还在（病根一："
            "裸 `_FREQ_RE.sub` 会把「周末」连同频次一起吃掉 ⇒ 下游判不出周末）",
            "周末" in out and "两次" not in out, repr(out))
    out2 = P._strip_freq_keep_range("平时去健身房一周三次")
    c.check("A1b 「平时」同样被留住（两个词表都补，别只修周末那一半）",
            "平时" in out2 and "三次" not in out2, repr(out2))
    raw = P._FREQ_RE.sub(" ", s1)
    c.check("A1c 对照：裸 `_FREQ_RE.sub` 确实把「周末」吃了（这条钉的是病根本身，"
            "谁要是把 helper 改回裸 sub，A1/A1c 一起红）",
            "周末" not in raw, repr(raw))

    with sandbox():
        write_timetable(BUSY)
        with pin_now(9, 0):
            card = stub_today(TUE, lambda: chain(P, s1))
        slots = (card or {}).get("slots") or []
        c.check("A2 §4.1 第一句 → 一张 todo_slots、preselect=2（「两次」读对了、"
                "也**替他勾好了两段**）、每段 180 分钟",
                bool(card) and card.get("kind") == "todo_slots"
                and card.get("preselect") == 2 and len(slots) >= 2
                and all(s.get("minutes") == 180 for s in slots),
                str([(s.get("date"), s.get("start"), s.get("minutes")) for s in slots]))
        c.check("A2b 每一段都在周六/周日（「周末」这个约束不许被频次擦没）",
                all(wd(s["date"]) in WEEKEND for s in slots),
                str([wd(s["date"]) for s in slots]))

        # 加重周六：单档全装不下 3 小时 ⇒ 首段必须落**这**周六的跨档缝，
        # 而不是悄悄滑到下周（病根三在候选路上的现场）
        write_timetable(HEAVY_SAT)
        with pin_now(9, 0):
            card3 = stub_today(TUE, lambda: chain(P, s1))
        slots3 = (card3 or {}).get("slots") or []
        c.check("A3 周六只剩跨档缝时首段就落**这**周六 10:30-13:30"
                "（删掉 1.2 的越过档尾，周六整段被判排不下 ⇒ 首段滑到周日，这条红）",
                bool(slots3) and slots3[0].get("date") == SAT
                and slots3[0].get("start") == "10:30" and slots3[0].get("end") == "13:30",
                str([(s.get("date"), s.get("start"), s.get("end")) for s in slots3[:2]]))

        write_timetable(BUSY)
        with pin_now(9, 0):
            card4 = stub_today(TUE, lambda: chain(P, "周六打乒乓球三小时"))
        c.check("A4 正控「周六打乒乓球三小时」→ 单条、落周六、180 分钟、"
                "照写「按你说的 3 小时」（点名哪天时 weekend_mode 不许多管闲事）",
                bool(card4) and card4.get("kind") == "todo_add"
                and wd(card4["date"]) == "周六" and card4.get("minutes") == 180
                and "按你说的 3 小时" in (card4.get("reason") or ""),
                str((card4 or {}).get("date")) + " / " + str((card4 or {}).get("reason"))[:60])
    return c.summary("A 批（擦频次留范围词）")


def batch_b_honesty():
    c = Checker()
    from app.modules import planner as P
    print("\n=== B 批 · 1.4 装不满说真话 ===")
    with sandbox():
        write_timetable([])
        seed_todos(PARTIAL_TODOS)
        with pin_now(9, 0):
            card = stub_today(TUE, lambda: chain(P, "周末打篮球，四个小时"))
        reason = (card or {}).get("reason") or ""
        c.check("B1 四个周末日都只剩 210 分钟 ⇒ 备胎卡分钟数=210（两轮循环："
                "装不满的先记下、有装得下的才轮到它）",
                bool(card) and card.get("kind") == "todo_add"
                and card.get("minutes") == 210 and card.get("date") == SAT,
                str((card or {}).get("summary")))
        c.check("B1b 卡上写的是「只排得出 3 小时 30 分钟…不足你说的 4 小时」"
                "——排不满就明说排了多少（这句会原样进确认条，学生签字前看得见）",
                "只排得出" in reason and "不足你说的 4 小时" in reason,
                reason[:80])
        c.check("B1c 不许再写「按你说的 4 小时 排的」（单据 §0.5：卡片 125 分钟、"
                "嘴上说 4 小时，学生点【确认加入】就等于认了假话 —— M4 的靶子）",
                "按你说的" not in reason, reason[:60])
        c.check("B1d reason 里一个星号都没有（纯文本渲染，markdown 会原样印出来）",
                "*" not in reason, repr(reason[-40:]))

        seed_todos([])
        with pin_now(9, 0):
            card2 = stub_today(TUE, lambda: chain(P, "周末打篮球，四个小时"))
        reason2 = (card2 or {}).get("reason") or ""
        c.check("B2 对照：周末全空、真装得下 4 小时 ⇒ 240 分钟、照写「按你说的 4 小时」"
                "（改的是**说假话**，不是不许说这句）",
                bool(card2) and card2.get("minutes") == 240
                and "按你说的 4 小时" in reason2,
                str((card2 or {}).get("summary")) + " / " + reason2[:50])
        c.check("B2b 装得下那条的 reason 同样没有星号（两套话术都过纯文本这一关）",
                "*" not in reason2, repr(reason2[-40:]))

        with pin_now(9, 0):
            card3 = stub_today(TUE, lambda: chain(P, "周末打乒乓球"))
        reason3 = (card3 or {}).get("reason") or ""
        c.check("B3 没报时长就不许替他宣称（said 判据：「按你说的」只在他真说过时出现）",
                bool(card3) and wd(card3.get("date") or "") in WEEKEND
                and "按你说的" not in reason3,
                str((card3 or {}).get("summary")) + " / " + reason3[:50])
        c.check("B3b 这条的 reason 也没有星号（said=False 那一支同样过纯文本这一关）",
                "*" not in reason3, repr(reason3[-40:]))
    return c.summary("B 批（装不满说真话）")


def batch_c_yield_day():
    c = Checker()
    from app.modules import planner as P
    print("\n=== C 批 · 1.5 具体日子退让 ===")
    merged = "周末打篮球，四个小时，周三是明天，我说的周末"
    with sandbox():
        write_timetable(BUSY)
        trap = stub_today(TUE, lambda: P._pick_date(merged))
        c.check("C1 前提：合并句里 `_pick_date` 抓到的确实是「明天」"
                "（学生用来指出我们排错了的词 —— 不钉这一条，下面那判就是空转）",
                trap == TOMORROW, f"_pick_date={trap} / 明天={TOMORROW}")
        with pin_now(9, 0):
            card = stub_today(TUE, lambda: chain(P, merged))
        c.check("C1b §0.7 纠正句 → 卡落**周末**、不落「明天」那个周三"
                "（删掉 1.5 的两处让日退让，days=[明天] 先命中 ⇒ 这条红）",
                bool(card) and wd(card.get("date") or "") in WEEKEND
                and card.get("date") != TOMORROW,
                str((card or {}).get("summary")))
        c.check("C1c 卡上 240 分钟且不再撒谎（纠正轮也要过 1.4 那道关）",
                bool(card) and card.get("minutes") == 240
                and "按你说的 4 小时" in ((card or {}).get("reason") or ""),
                str((card or {}).get("reason"))[:60])

        with pin_now(9, 0):
            got = stub_today(TUE, lambda: P.candidate_slots("周末打乒乓球，周三是明天", 4, 7))
        c.check("C2 candidate_slots 同一条规矩：「周末」在场时不许让「明天」锁日"
                "（全部候选都在周六/周日 —— M5 的另一半靶子）",
                bool(got) and all(wd(s["date"]) in WEEKEND for s in got),
                str([wd(s["date"]) for s in got]))
        with pin_now(9, 0):
            got2 = stub_today(TUE, lambda: P.candidate_slots("平时打乒乓球，周三是明天", 4, 7))
        c.check("C2b 正控「平时…」：一个周末都没有（workday_mode 那半没被这次改动带歪）",
                bool(got2) and all(wd(s["date"]) not in WEEKEND for s in got2),
                str([wd(s["date"]) for s in got2]))

        _SAT = _NMON + datetime.timedelta(days=5)
        with pin_now(9, 0):
            card3 = stub_today(_SAT, lambda: chain(P, "平时和周末都行打篮球"))
        c.check("C3 兜底路「平时和周末都行」= 他压根没限制 ⇒ 打桩的**周六**今天就能排"
                "（两把尺子一起关：留着任何一个，整段范围被轮流吃光）",
                bool(card3) and card3.get("date") == _SAT.isoformat(),
                str((card3 or {}).get("summary")))
        with pin_now(9, 0):
            card4 = stub_today(TUE, lambda: chain(P, "平时打篮球，四个小时"))
        c.check("C4 正控「平时打篮球，四个小时」→ 落工作日（1.3 补的是周末那一半，"
                "工作日那一半不许被顺手改坏）",
                bool(card4) and datetime.date.fromisoformat(card4["date"]).isoweekday() <= 5,
                str((card4 or {}).get("summary")))
    return c.summary("C 批（具体日子退让）")


def batch_d_day_slots():
    c = Checker()
    from app.modules import planner as P
    print("\n=== D 批 · 1.2 装不下才越过档尾 ===")
    mon = _NMON.isoformat()
    tue2 = (_NMON + datetime.timedelta(days=1)).isoformat()
    with sandbox():
        write_timetable([{"day": 1, "start": "08:00", "end": "10:30", "course": "早课", "location": "x"},
                         {"day": 1, "start": "14:00", "end": "16:00", "course": "下午课", "location": "x"}])
        got = P._day_slots(mon, length=180)
        c.check("D1 上午 10:30-12:00 只有 90 分 ⇒ 180 分钟的请求允许越过档尾，"
                "给出 10:30-13:30（11/8 那 210 分钟真空档被 phi 整段吃掉的同形现场；M3 的靶子）",
                any(s["start"] == "10:30" and s["end"] == "13:30" for s in got),
                str([(s["start"], s["end"]) for s in got]))
        got90 = P._day_slots(mon, length=90)
        c.check("D2 正控：90 分钟在上午档内装得下 ⇒ 仍是 10:30-12:00，**不多跨**"
                "（放开只发生在装不下时，短线请求行为一字不变）",
                any(s["start"] == "10:30" and s["end"] == "12:00" for s in got90)
                and not any(s["end"] == "13:30" for s in got90),
                str([(s["start"], s["end"]) for s in got90]))
        write_timetable([{"day": 2, "start": "08:00", "end": "12:00", "course": "满上午", "location": "x"}])
        got240 = P._day_slots(tue2, length=240)
        c.check("D3 正控：下午档本身装得下 240 ⇒ 原样给出 14:00-18:00（phi 仍是偏好带，"
                "不是被这次改动拆掉的禁区）",
                any(s["start"] == "14:00" and s["end"] == "18:00" for s in got240),
                str([(s["start"], s["end"]) for s in got240]))
    return c.summary("D 批（档尾放开）")


def batch_s_static():
    c = Checker()
    print("\n=== S 批 · 静态接线 ===")
    pl = read("app/modules/planner.py")

    def seg_of(name, src=None):
        s = src or pl
        i = s.find(f"def {name}(")
        return s[i:s.find("\ndef ", i + 10)] if i >= 0 else ""

    seg_help = seg_of("_strip_freq_keep_range")
    c.check("S1 helper 在 `_FREQ_RE` 之后定义，且逻辑就是「裸 sub + 两张词表补回」"
            "（判据钉词表常量本身：改正则会被九处共用点连坐，这里只许补词）",
            "_FREQ_RE.sub(\" \", t)" in seg_help
            and "_WEEKEND_WORDS" in seg_help and "_WORKDAY_WORDS" in seg_help, "")

    seg_tsp = seg_of("todo_slots_proposal")
    c.check("S2 `todo_slots_proposal` 两处调用全换成 helper（day 与 plain 各一处；"
            "判据钉两行代码本身 —— 段内那处 `_FREQ_RE.sub` 字样是 #114 的注释，不算）",
            seg_tsp.count("_strip_freq_keep_range(") == 2
            and "day = _pick_date(_FREQ_RE.sub" not in seg_tsp
            and "plain = _FREQ_RE.sub" not in seg_tsp, "")
    seg_m2c = seg_of("mode_to_card")
    seg_ptp = seg_of("parse_todo_patch")
    seg_cf = seg_of("carry_freq")
    c.check("S3 三处**不需要**范围词的裸 `_FREQ_RE.sub` 原样没动"
            "（carry_freq 周期否决 / mode_to_card 频次闸 / parse_todo_patch —— 单据 §三 明令不动）",
            "_FREQ_RE.sub" in seg_m2c and "_FREQ_RE.sub" in seg_ptp
            and "_FREQ_RE.sub" in seg_cf, "")

    seg_plan = seg_of("plan_todo_slot")
    c.check("S4 兜底路周末宽度 14 天 + 只挑 isoweekday>=6（周日问「周末」下一个周末在 6 天后，"
            "5 天够不着；宽度与 candidate_slots 同源）",
            "range(14)" in seg_plan and "isoweekday() >= 6" in seg_plan, "")
    c.check("S5 兜底路两把尺子一起关（「平时和周末都行」= 没限制）",
            "_workday_mode = _weekend_mode = False" in seg_plan, "")
    c.check("S6 兜底路「周末/平时」在场时具体日子退让（`if _workday_mode or _weekend_mode: day = \"\"`）"
            "—— 不然 §1.3 那段 `if day: days=[day]` 先命中，周末那半永远走不到",
            'if _workday_mode or _weekend_mode:' in seg_plan, "")
    c.check("S7 两轮循环：装不满的记 `_partial` 备胎、装得下的先走（少这一步，"
            "第一个 125 分钟的缝就被当成答案）",
            seg_plan.count("_partial") >= 3 and 'slot["minutes"] < want' in seg_plan, "")

    seg_cand = seg_of("candidate_slots")
    c.check("S8 candidate_slots 的 `picked_day` 双模式退让（workday_mode 与 weekend_mode 同权）",
            "picked_day = None if (workday_mode or weekend_mode) else _pick_date(t)" in seg_cand, "")

    i = pl.find("finish = min(begin + length, to_minutes(s[\"end\"]), phi)")
    seg_ds = pl[i:i + 1400]
    c.check("S9 `_day_slots` 的越过档尾是**嵌套在装不下里**的第二次尝试"
            "（外层照旧带 phi ⇒ 装得下时行为一字不变；拍平成无条件去掉 phi 就是改宽了带子）",
            i > 0 and 'finish = min(begin + length, to_minutes(s["end"]))' in seg_ds
            and seg_ds.find('if finish - begin < min_needed:')
            < seg_ds.find('finish = min(begin + length, to_minutes(s["end"]))'), "")

    seg_auto = seg_of("_auto_card")
    c.check("S10 `_auto_card` 两套话术并存：装得下才说「按你说的」、装不满明说"
            "「只排得出…不足你说的」（星号判据钉在运行时 reason 上 —— B1d/B2b/B3b，"
            "源码段切分会带到隔壁函数的注释，钉不准）",
            "按你说的" in seg_auto and "只排得出" in seg_auto
            and "不足你说的" in seg_auto, "")
    return c.summary("S 批（静态接线）")


if __name__ == "__main__":
    batch_a_freq()
    batch_b_honesty()
    batch_c_yield_day()
    batch_d_day_slots()
    batch_s_static()
