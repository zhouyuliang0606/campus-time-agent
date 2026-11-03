"""#118 的护栏（口头单，无原件）：Mock（无 Key）那条路也要尊重学生说得最死的两个约束。

现场（用户 2026-11-04 实测，带 Key 走语义层正常、没 Key 走 chat 路由翻车）：
  · 「周末打篮球，四个小时」在周三说 → 候选落在**周三/四/五**、每段 60 分钟——
    「周末」和「四个小时」两个词都没进过 mock_planner §5 的视野
    （原来写死 `base+0/1/2`、`find_free_slots(d, 60)`）。
  · 卡上事名原样是「周末打篮球，四个小时」（`_guess_title` 不擦排期条件），
    确认回传（student.html `确认 <日期> <起-止> <title>` → §3 同一函数抠名）
    会把这个名字**原样写进日程库**。

修法（只动 mock_planner 一处 + 它私有的 _guess_title，两个调用点 :1779/:1809 都在 §3/§5）：
  ① 标题擦排期条件（周末/双休/平时/工作日/周X/时长/时段词）——T 批；
  ② 时长走现成的 wanted_minutes()（读不出回 60），准入线语义 = minutes >= want
     （find_free_slots 返回的是整段空档，60 只是门槛）——W 批；
  ③ 「周末/双休」→ 下一个即将到来的周六+周日：周三说 +3/+4；今天就是周六 →
     今天+明天；今天是周日 → 下周六日(+6/+7)，同一把尺没有特例——W4；
  ④ 凑不出时说人话：「空档最长只有一段约 X 分钟，放不下你说的 Y 分钟」——W2/W3。

三条自省（写断言前先看）：
  1. **日期全打桩**：`stub_today` 钉"今天"，目标周末从真实"今天"推下周一再取，
     任何星期几跑都成立——不打桩，"候选落周六"就是一条空判据（#69 家族）。
  2. **课表/待办全自种**：wipe_todos + write_timetable/seed_todos，
     与真实演示数据（周六被排到 22:00）解耦。
  3. **准入线不是段长**：判据写 `minutes >= 240`，不许写 `== 240`——
     空档 900 分钟的段也是合法候选。
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, sandbox,  # noqa: E402
                      seed_todos, wipe_todos, write_timetable)

WD = "周一 周二 周三 周四 周五 周六 周日".split()
WEEKEND = ("周六", "周日")

_TODAY = datetime.date.today()
_NMON = _TODAY + datetime.timedelta(days=(8 - _TODAY.isoweekday()))
WED = (_NMON + datetime.timedelta(days=2)).isoformat()   # 那一周的周三（对照句的"今天"）
SAT = (_NMON + datetime.timedelta(days=5)).isoformat()
SUN = (_NMON + datetime.timedelta(days=6)).isoformat()
SAT2 = (_NMON + datetime.timedelta(days=12)).isoformat()  # 周日规则的"下一个周末"
SUN2 = (_NMON + datetime.timedelta(days=13)).isoformat()

# 周三有课 10:00-11:30，周末整天空——出卡那条路的夹具
FREE_WEEK = [{"day": 3, "start": "10:00", "end": "11:30", "course": "体育课", "location": "x"}]
# 真机现状的同构：周六被待办占到 21:10（只剩 50 分钟），周日全天占满
BOOKED_SAT = [{"title": "占白天", "date": SAT, "start": "07:00", "end": "21:10"},
              {"title": "占全天", "date": SUN, "start": "07:00", "end": "22:00"}]
# 周末只剩 120/180 分钟的缝：够 60 的门槛、不够 240 的准入线（M2 的靶子）
GAPS_120 = ([{"title": "占上午", "date": d, "start": "07:00", "end": "09:00"}
             for d in (SAT, SUN)]
            + [{"title": "占下午", "date": d, "start": "11:00", "end": "19:00"}
               for d in (SAT, SUN)])


def wd(date):
    return WD[datetime.date.fromisoformat(date).weekday()]


def stub_today(day, fn):
    """把 planner 眼里的「今天」换成 `day`（只在这一段里有效，出来必还原）。

    接受 str 或 date：§5 拿返回值当 datetime.date 用（.weekday()/+timedelta），必须归一。
    """
    day = datetime.date.fromisoformat(day) if isinstance(day, str) else day
    from app.modules import planner
    saved = planner.clock.today
    planner.clock.today = lambda: day
    try:
        return fn()
    finally:
        planner.clock.today = saved


def batch_t_title():
    c = Checker()
    from app.modules import planner as P
    print("\n=== T 批 · 标题：排期条件不算事名（_guess_title）===")
    cases = [
        ("周末打篮球，四个小时", "打篮球", "现场原句"),
        ("周三下午打篮球", "打篮球", "具体日子+时段"),
        ("晚上打篮球", "打篮球", "只有时段"),
        ("确认 2026-09-24 14:00-15:30 打篮球", "打篮球",
         "确认回传形状（§3 同一函数抠名，抠残了就写错库）"),
        ("工作日晚上背单词", "背单词", "「工作日」也要擦（_WORKDAY_WORDS）"),
        ("我要去游泳", "去游泳", "照旧正控：没排期条件就不许多擦"),
        ("周末双休去爬山", "去爬山", "两个周末词叠着也不留残渣"),
    ]
    for i, (msg, want, why) in enumerate(cases, 1):
        got = P._guess_title(msg)
        c.check(f"T{i} {why}：「{msg}」→「{want}」", got == want, f"got={got!r}")
    return c.summary("T 批（标题）")


def batch_w_slots():
    c = Checker()
    from app.modules import planner as P
    msg = "周末打篮球，四个小时"
    print("\n=== W 批 · §5 周末窗 + 时长准入 + 诚实话 ===")

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        r = stub_today(WED, lambda: P.mock_planner(msg, [], "gentle"))
        opts = r.get("options") or []
        c.check("W1 现场句出卡：候选全落周六/周日（「周末」这个约束不许再被当成没说）",
                bool(opts) and all(wd(o["date"]) in WEEKEND for o in opts),
                str([(o["date"], wd(o["date"]), o["minutes"]) for o in opts]))
        c.check("W1b 准入线是 >= 不是 ==：每段 minutes ≥ 240（find_free_slots 返回整段，"
                "900 分钟的空档也是合法候选）",
                bool(opts) and all(o["minutes"] >= 240 for o in opts),
                str([o["minutes"] for o in opts]))
        c.check("W1c 事名干净：所有候选 title=「打篮球」（M1/M2 之外本批的第三个命门）",
                bool(opts) and all(o["title"] == "打篮球" for o in opts),
                str([o["title"] for o in opts]))
        c.check("W1d 仍是可勾选候选卡（awaiting_choice=True，交互形状没变）",
                r.get("awaiting_choice") is True, "")

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        seed_todos(BOOKED_SAT)
        r = stub_today(WED, lambda: P.mock_planner(msg, [], "gentle"))
        c.check("W2 装不下说人话：不出卡 + 回答里有「最长只有一段约 50 分钟」和「240」"
                "（真机现状：周六被排到只剩 50 分钟，评委看到的是有依据的诚实话）",
                not (r.get("options") or []) and r.get("awaiting_choice") is False
                and "50 分钟" in r.get("answer", "") and "240" in r.get("answer", ""),
                repr(r.get("answer", ""))[:120])

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        seed_todos(GAPS_120)
        r = stub_today(WED, lambda: P.mock_planner(msg, [], "gentle"))
        c.check("W3 准入线真在拦：周末只剩 120/180 的缝 ⇒ 不出卡、诚实话报 180"
                "（M2 的靶子：把准入线摘掉这条就红——候选会带着 <240 的段冒出来）",
                not (r.get("options") or []) and r.get("awaiting_choice") is False
                and "180 分钟" in r.get("answer", "") and "240" in r.get("answer", ""),
                repr(r.get("answer", ""))[:120])

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        r = stub_today(SUN, lambda: P.mock_planner(msg, [], "gentle"))
        opts = r.get("options") or []
        c.check("W4 周日规则：今天就是周日 ⇒「周末」指下周六日(+6/+7)，"
                "候选一个都不落在今天（同一把尺，没有特例分支）",
                bool(opts) and all(o["date"] in (SAT2, SUN2) for o in opts)
                and all(o["date"] != SUN for o in opts),
                str([o["date"] for o in opts]))

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        r = stub_today(WED, lambda: P.mock_planner("周三下午打篮球", [], "gentle"))
        opts = r.get("options") or []
        near = {WED, (_NMON + datetime.timedelta(days=3)).isoformat(),
                (_NMON + datetime.timedelta(days=4)).isoformat()}
        c.check("W5 对照不许让位过头：「周三下午打篮球」仍铺周三起三天、事名=打篮球",
                bool(opts) and all(o["date"] in near for o in opts)
                and all(o["title"] == "打篮球" for o in opts),
                str([(o["date"], o["title"]) for o in opts]))

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        r = stub_today(WED, lambda: P.mock_planner("晚上打篮球", [], "gentle"))
        opts = r.get("options") or []
        c.check("W6 对照：没说日子/周末的照旧从今天起铺（第一段就是今天）、事名=打篮球",
                bool(opts) and opts[0]["date"] == WED
                and all(o["title"] == "打篮球" for o in opts),
                str([(o["date"], o["title"]) for o in opts]))

    with sandbox():
        wipe_todos()
        write_timetable(FREE_WEEK)
        seed_todos(BOOKED_SAT)
        r = stub_today(WED, lambda: P.mock_planner("周末去打球", [], "gentle"))
        plain = P.mock_phrase("gentle", "empty")
        c.check("W7 没报时长的周末装不下 ⇒ 照旧走通用 empty 档（诚实话只在报了时长时接管，"
                "别把没报时长的路也改了口风）",
                not (r.get("options") or []) and r.get("answer") == plain,
                repr(r.get("answer", ""))[:80])
    return c.summary("W 批（§5 周末+时长）")


if __name__ == "__main__":
    rc1 = batch_t_title()
    rc2 = batch_w_slots()
    sys.exit(rc1 or rc2)
