"""2026-10-11 那批真机句子打出来的四个缺口，一处一个判据钉住。

这批一共 45 句（周期否决 / 不该误伤 / 裸钟点 / 多轮改口 / 不该办事的话），
A 组 10 句、E 组 7 句全过；剩下的红项查下去是**四个不同的根因**，都不是"再补一个词"能了事的：

① **时间词表登记不全 → 笔误闸门把正经词当成错字改掉**（`_fix_typo_time_words` 的第①道闸
   只认 `_TIME_WORDS` 里登过的）。漏登记「明晚/半夜/午夜」的代价：
   「明晚七点到九点」被改写成「明天七点到九点」→ 卡片排在**早上 07:00**；
   「半夜12点」被改写成「半夜里2点」→ 14:00。**静默排错时间**比排不进更坑 ——
   学生会以为系统听懂了，日程里却躺着一条不对的安排。
② **事名尾巴粘着语气词**：学生回「那就周六吧」，拼上候选卡的「写作业」一起擦噪声，
   剩「吧健身」这种名字。修法不是再擦一个词，而是**名字只认两个来源**：
   这句自己报的，或者外面传进来的那件事。
③ **模型把工具调用打成字**（真机原话：`propose_todo_tool / {"title": "写作业", ...}`
   直接回给了学生）。这一幕同时踩两条线：卡没出来（学生白等），内部机制端上桌（红线 8）。
   正解不是"藏起来"，是**替它执行这一次调用**（同一件函数，校验一道不少），
   并且无论执行得出与否，那段文本都不许出现在回话里。
④ **裸钟点没在这儿问清**：候选卡下面学生报「三点开始大约三个小时」，
   `parse_slot_text` 认不出（正是②那条 clock_ok 拒掉的），于是一路掉回模型 ——
   ③ 那一幕就是这么触发的。修法是在**这里**就问一句"上午还是下午"，候选卡留着不撤。
"""
import ast
import io

from _harness import Checker, make_client, sandbox, title
from app.store import set_settings

PLANNER = "app/modules/planner.py"
MAIN = "app/main.py"


def batch_a_time_words_registered():
    c = Checker()
    title("A 批 · 「明晚/半夜」是正经时间词，不许被笔误闸门改成错字")
    from app.modules.planner import _fix_typo_time_words, _pick_date, parse_time_spec

    for s, want in (("明晚八点看直播", "20:00"), ("明晚七点到九点", "19:00"),
                    ("周五半夜12点赶车", "00:00"), ("周六凌晨1点接人", "01:00"),
                    ("昨晚八点自习", "20:00")):
        sp = parse_time_spec(s)
        c.check(f"{s} → 钟点 {want}（晚上/凌晨说在字面上，别掉到另一边）",
                sp["start"] == want, (sp["start"], _fix_typo_time_words(s)))
    c.check("病根复刻：这几个词**没登记进 _TIME_WORDS 时**会被改写（登记后原样保留）",
            _fix_typo_time_words("明晚七点到九点") == "明晚七点到九点"
            and _fix_typo_time_words("半夜12点赶车") == "半夜12点赶车",
            (_fix_typo_time_words("明晚七点到九点"), _fix_typo_time_words("半夜12点赶车")))
    tomorrow = None
    import datetime
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    c.check("「明晚」也认得出日子（不靠把「晚」字擦掉来蒙对）",
            _pick_date("明晚八点看直播") == tomorrow, _pick_date("明晚八点看直播"))
    c.check("「昨晚」不排日子（过去的事排了也是错过）",
            _pick_date("昨晚八点自习") is None, _pick_date("昨晚八点自习"))
    # 反向：补登记不许把笔误闸门本身打坏
    for s, want in (("中文12点的车", "中午"), ("下物三点开会", "下午"), ("上物十点上课", "上午")):
        c.check(f"笔误「{s}」照旧纠成带「{want}」的写法", want in _fix_typo_time_words(s),
                _fix_typo_time_words(s))
    c.check("没钟点语境的正经词一个字都不动（「加个上班的提醒」）",
            _fix_typo_time_words("加个上班的提醒") == "加个上班的提醒",
            _fix_typo_time_words("加个上班的提醒"))
    return c


def batch_b_title_has_no_tail():
    c = Checker()
    title("B 批 · 事名只认「这句报的」或「上一轮那件」，不许是拼出来擦干净的残渣")
    from app.modules import planner as P

    # 病根本身
    c.check("病根复刻：整串粘一起擦完就是「吧健身」",
            P._pick_title("那就周六吧 健身") == "吧健身", P._pick_title("那就周六吧 健身"))
    for text, held, want in (("那就周六吧", "健身", "健身"),
                             ("周六吧", "写作业", "写作业"),
                             ("那就明天好了", "爬山", "爬山")):
        got = (P.parse_slot_text(text, held, "2026-10-17") or {}).get("title")
        c.check(f"「{text}」挂着「{held}」→ 名字就是「{want}」（不带语气词尾巴）",
                got == want, got)
    c.check("摘要跟着名字一起改口（别处露出旧名也一样是错）",
            "吧" not in ((P.parse_slot_text("那就周六吧", "健身", "2026-10-17") or {})
                         .get("summary") or ""),
            (P.parse_slot_text("那就周六吧", "健身", "2026-10-17") or {}).get("summary"))
    # 反向：这句自己报了名，仍不许被外面那个名字盖掉（test_42 钉过，这里再钉一次同一条契约）
    got = P.parse_slot_text("周五晚上七点到九点值日", "写作业", "2026-10-15") or {}
    c.check("报了名的还是用这句的（改名不许把时间也改没）",
            got.get("title") == "值日" and got.get("start") == "19:00",
            (got.get("title"), got.get("start")))
    return c


def batch_c_tool_echo_never_shown():
    c = Checker()
    title("C 批 · 模型把工具调用打成字 → 系统替它执行，那段文本绝不回显")
    from app.modules.planner import looks_like_tool_echo, textual_tool_card

    echo = 'propose_todo_tool\n{"title": "写作业", "when": "明天15:00~18:00"}'
    c.check("认得出这个形状", looks_like_tool_echo(echo) is True)
    card = textual_tool_card(echo)
    c.check("替它执行得出一张真卡（走的是同一件函数：时间由系统算、空档照查）",
            isinstance(card, dict) and card.get("kind") == "todo_add"
            and card.get("start") == "15:00", card)
    # ⚠️ 判据要保守：正常话术里提到"确认/日程"不能被当成 echo 撤掉
    for s in ("📝 要不要把 **写作业** 排进日程？2026-10-12 15:00-18:00。",
              "这一步没办成，你再说具体一点就好：",
              "我可以调用 propose_todo_tool 帮你安排（这句话里没有参数）"):
        c.check(f"不误判正常回话：{s[:24]}…", looks_like_tool_echo(s) is False, s[:60])
    # 执行不出的那一件（只读工具不产卡片）也必须"检测得到"，好让出口把它撤下来
    c.check("只读那几件也认得出形状（撤文本，不代执行）",
            looks_like_tool_echo('propose_slots {"options": [{"date": "2026-10-12"}]}') is True
            and textual_tool_card('propose_slots {"options": []}') is None,
            "")
    src = io.open(MAIN, encoding="utf-8").read()
    tree = ast.parse(src)
    impl = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_chat_impl"][0]
    body = ast.unparse(impl)
    c.check("主链路里既写了「替它执行」（textual_tool_card），也写了回显前最后一道保险",
            "textual_tool_card(" in body and 'looks_like_tool_echo(final_answer or' in body, "")
    return c


def batch_d_bare_clock_asked_in_place():
    c = Checker()
    title("D 批 · 候选卡下面报裸钟点：在这儿问清楚，别掉回模型（C 批那一幕的引信）")
    import app.main as M

    async def fake_sem(text, conv=None, override=None, **kw):
        return {"intent": "add_todo", "module": "planner", "title": "写作业"}

    real = M.classify
    with sandbox():
        set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "m"})
        cli = make_client()
        try:
            M.classify = fake_sem
            r1 = cli.post("/api/chat", json={"message": "晚上写作业",
                                             "session_id": "t43-d", "module": "planner"})
            cards1 = [o for o in (r1.json().get("options") or []) if isinstance(o, dict)]
            c.check("第一轮先挂出候选卡（这一支要有前提才谈得上「在这儿问清楚」）",
                    any(o.get("kind") == "todo_slots" for o in cards1),
                    [(o.get("kind"), o.get("title")) for o in cards1])
            r2 = cli.post("/api/chat", json={"message": "三点开始大约三个小时",
                                             "session_id": "t43-d", "module": "planner"})
            d2 = r2.json()
            ans2 = d2.get("answer") or ""
            c.check("第二轮报的是裸钟点 → 问一句上下午，不替他挑",
                    "上午还是下午" in ans2, ans2[:110])
            c.check("这一步不出卡（模型那条路也就没机会把调用打成字）",
                    not [o for o in (d2.get("options") or []) if isinstance(o, dict)],
                    d2.get("options"))
            # 候选卡不许被清空：他补一句「下午三点」就得当场能办
            r3 = cli.post("/api/chat", json={"message": "下午三点开始大约三个小时",
                                             "session_id": "t43-d", "module": "planner"})
            cards3 = [o for o in (r3.json().get("options") or []) if isinstance(o, dict)]
            times3 = [(o.get("start") or s.get("start")) for o in cards3
                      for s in [{}] + (o.get("slots") or [])]
            c.check("补一句「下午三点」照样出卡、且用的是 15:00（问一句不是把这句作废）",
                    bool(cards3) and "15:00" in times3,
                    [(o.get("kind"), o.get("start")) for o in cards3])
        finally:
            M.classify = real
    return c


def batch_e_period_carry():
    c = Checker()
    title("E 批 · 语义层重拼不许把「每周几 / 平时」丢掉（B3·B5·F5·F7 那一组的根因）")
    from app.modules.planner import (candidate_slots, carry_period, parse_add_todo,
                                     retime_todo_proposal, weekly_repeat_day)

    # 病根先复刻：重拼出来的那句里周期字样没了 → 判据一律判不出
    c.check("病根复刻：只拼槽位的规范话里「每个周六」蒸发了（weekly 判不出）",
            weekly_repeat_day("加个爬山，上午九点") is None,
            weekly_repeat_day("加个爬山，上午九点"))
    c.check("补一句「每周六」就回来了（carry_period 干的正是这件事）",
            weekly_repeat_day(carry_period("加个爬山", "每个周六上午九点爬山")) == 6,
            carry_period("加个爬山", "每个周六上午九点爬山"))
    c.check("「平时/工作日」同样带得过去",
            "工作日" in carry_period("加个跑步", "我平时想去跑步"),
            carry_period("加个跑步", "我平时想去跑步"))
    c.check("原话已经带过周期字样就别再粘一遍（幂等，粘两遍标题就脏了）",
            carry_period("加个爬山 每周六", "每个周六爬山") == "加个爬山 每周六",
            carry_period("加个爬山 每周六", "每个周六爬山"))
    got_slots = candidate_slots(carry_period("加个跑步", "我平时想去跑步"), max_slots=4)
    c.check("带过去之后候选真避开周末（workday_mode 那句判据重新生效）",
            bool(got_slots) and all(_wd(s["date"]) < 6 for s in got_slots),
            [(s["date"], _wd(s["date"])) for s in got_slots])

    # 报了准点的单条卡也要带 repeat（以前只有摊候选那一路带）
    for s, want in (("每个周六上午九点爬山", "weekly"), ("每周三下午两点开会", "weekly"),
                    ("周六上午九点爬山", ""), ("今天下午三点开会", "")):
        got = (parse_add_todo(s) or {}).get("repeat")
        c.check(f"{s} → 卡片 repeat={repr(want)}", got == want,
                (got, s))
    kept = retime_todo_proposal(
        {"kind": "todo_add", "title": "爬山", "date": "2026-10-17",
         "start": "09:00", "end": "10:00", "repeat": "weekly"}, "改成上午十点")
    c.check("换时间不许顺手把「每周」换没（上一张是 weekly，改完还得是）",
            (kept or {}).get("repeat") == "weekly" and (kept or {}).get("start") == "10:00",
            (kept or {}).get("repeat"))
    return c


def _wd(iso):
    import datetime
    try:
        return datetime.date.fromisoformat(iso).isoweekday()
    except Exception:
        return 0


def batch_f_weekly_e2e():
    c = Checker()
    title("F 批 · 端到端：「每个周六上午九点爬山」这张卡与回话都得认下「每周」")
    import datetime

    from app.store import add_todo, todo_occurs_on

    with sandbox():
        set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "m"})
        cli = make_client()
        r = cli.post("/api/chat", json={"message": "每个周六上午九点爬山",
                                        "session_id": "t43-f", "module": "planner"})
        d = r.json()
        cards = [o for o in (d.get("options") or []) if isinstance(o, dict)]
        ans = d.get("answer") or ""
        c.check("出了卡", bool(cards), str(d)[:100])
        c.check("卡上带着 repeat=weekly（不是一条一次性的）",
                any((o.get("repeat") or "") == "weekly" for o in cards),
                [(o.get("kind"), o.get("repeat")) for o in cards])
        c.check("回话里要确认「每周」这件事办了（只报日子，学生会以为就排这一天）",
                "每周六" in ans or "每个周六" in ans, ans[:130])
        one = next((o for o in cards if (o.get("repeat") or "") == "weekly"), None)
        if one is not None:
            t = add_todo(one["title"], one["date"], one["start"], one["end"], repeat="weekly")
            nxt = (datetime.date.fromisoformat(one["date"])
                   + datetime.timedelta(days=7)).isoformat()
            c.check(f"照这张卡写进库，下个周六（{nxt}）它还在",
                    todo_occurs_on(t, nxt), (t.get("repeat"), t.get("weekday"), nxt))
        else:
            c.check("这一批没拿到 weekly 卡（F 批最后一项无法验）", False, [(o.get("kind"), o.get("repeat")) for o in cards])
    return c


def batch_g_slot_answer_shape():
    c = Checker()
    title("G 批 · 「挑时间」只认纯时间话（否则报障会被听成下单，test_06 十六批那一幕）")
    from app.modules import planner as P

    # 病根：老写法只要求"句子里有钟点或有日子"，这句里恰好有"周二"
    bad = "我现在没有看见日程显示周二游泳代办项目啊"
    c.check("病根复刻：这句里有日期词（老判据就是因为这个才认成挑时间）",
            P._pick_date(bad) is not None, P._pick_date(bad))
    c.check("但擦干净时间词之后剩一大堆话 → 这是在报障，不是挑时间",
            P.is_slot_answer(bad) is False, P._strip_title_noise(bad))
    for s in ("那就周六吧", "下午两点到三点", "改成晚上七点到八点", "周六 19:00-20:00",
              "三点开始大约三个小时", "晚上七点到九点值日"):
        c.check(f"正写的挑时间话照旧认：「{s}」", P.is_slot_answer(s) is True,
                P._strip_title_noise(s))
    return c


def batch_h_model_exit_gate_behaviour():
    c = Checker()
    title("H 批 · 模型那一路的出口：周期说法生成出来的候选，一张都不留")
    # 为什么单独钉这一批：G14 变异（把撤卡那行改成"原样留着"）在别的断言下**全绿** ——
    # 形状断言只看见 `model_opts = _keep` 那行还在，看不见 `_keep` 其实没滤掉任何东西。
    # 所以必须拿真行为打它：假引擎 + 假密钥（绕开无密钥那条 Mock 分支），
    # 让"模型自己摊了三段候选"这一幕原样复现。
    import app.main as M
    from app.store import set_settings as _ss

    async def quiet_sem(text, conv=None, override=None, **kw):
        return {"intent": "chat", "module": "planner"}

    class _FakeEngine:
        def __init__(self, *a, **kw):
            pass

        async def run(self, message, history=None):
            return {"answer": "给你挑了几段空档",
                    "trace": [{"step": 1, "phase": "💡 最终回答", "answer": "几段空档"}],
                    "options": [{"date": "2026-10-13", "start": "19:00", "end": "20:30",
                                 "label": "周二 19:00-20:30", "title": "晨跑"},
                                {"date": "2026-10-14", "start": "19:00", "end": "20:30",
                                 "label": "周三 19:00-20:30", "title": "晨跑"}],
                    "awaiting_choice": True}

    real_sem, real_engine = M.classify, M.AgentEngine
    with sandbox():
        # 有"密钥"才会走引擎（没密钥时走 Mock 分支，那是另一道闸，test_42 F 批钉过了）
        _ss({"api_key": "fake-key-for-test-only",
             "base_url": "https://api.deepseek.com", "model": "m"})
        cli = make_client()
        try:
            M.classify = quiet_sem
            M.AgentEngine = _FakeEngine
            r = cli.post("/api/chat", json={"message": "以后每天晨跑",
                                            "session_id": "t43-h", "module": "planner"})
            d = r.json()
            opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
            c.check("模型摊出来的这两段候选被出口撤掉（学生说的「每天」不能被偷偷改成两天）",
                    not opts, [(o.get("date"), o.get("title")) for o in opts])
            c.check("撤掉之后必须说一句为什么，不许只回一句「给你挑了几段」",
                    "记不了" in (d.get("answer") or ""), (d.get("answer") or "")[:90])
            r2 = cli.post("/api/chat", json={"message": "以后周六晨跑",
                                             "session_id": "t43-h2", "module": "planner"})
            c.check("对照：没说「每天」的那句，引擎给的候选照旧留着（不是无差别撤卡）",
                    bool([o for o in (r2.json().get("options") or []) if isinstance(o, dict)]),
                    (r2.json().get("options") or [])[:1])
        finally:
            M.classify, M.AgentEngine = real_sem, real_engine
    return c


def main():
    results = [batch_a_time_words_registered(), batch_b_title_has_no_tail(),
               batch_c_tool_echo_never_shown(), batch_d_bare_clock_asked_in_place(),
               batch_e_period_carry(), batch_f_weekly_e2e(),
               batch_g_slot_answer_shape(), batch_h_model_exit_gate_behaviour()]
    tp = sum(r.passed for r in results)
    tf = sum(r.failed for r in results)
    print(f"\n===== 真机批次查出的四个缺口总账：通过 {tp} 项，失败 {tf} 项 =====")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
