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
import json

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
                                     retime_todo_proposal, span_only_proposal,
                                     todo_slots_proposal, weekly_repeat_day)

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
    # 「工作日」+ 报了准点：以前单条卡这一路**根本不看星期几**，
    # 真机那句「工作日晚上七点背单词」就排到了周日 19:00。
    import datetime
    today = datetime.date.today()
    sat = (today + datetime.timedelta(days=(6 - today.isoweekday()) % 7)).isoformat()
    s1 = span_only_proposal("工作日晚上七点背单词", "背单词", sat)
    c.check(f"兜底那天是周末（{sat}）时，说了「工作日」就要挪到工作日",
            bool(s1) and _wd(s1["date"]) < 6 and s1["start"] == "19:00",
            s1 and (s1["date"], _wd(s1["date"]), s1["start"]))
    s2 = span_only_proposal("晚上七点背单词", "背单词", sat)
    c.check("对照：没说「工作日」的照旧用兜底那天（不许无端往后挪）",
            bool(s2) and s2["date"] == sat, s2 and s2["date"])
    # ★ 同一天 H 组还打出一条更阴的：「每周一次」的"一"是**次数**，不是星期几。
    #   老正则 `([一二三四五六日天])` 会把它吃掉 → 系统替学生挑了个周一、还挂成"每周重复"。
    c.check("「每周一次大扫除」不许被读成「每周一」（次数不是星期几）",
            weekly_repeat_day("每周一次大扫除") is None,
            weekly_repeat_day("每周一次大扫除"))
    c.check("「每周三」「每周一升旗」照旧认（前瞻不能把真星期几也挡掉）",
            weekly_repeat_day("每周三下午两点开会") == 3
            and weekly_repeat_day("每周一升旗") == 1,
            (weekly_repeat_day("每周三下午两点开会"), weekly_repeat_day("每周一升旗")))
    _h4 = todo_slots_proposal("每周一次大扫除") or {}
    c.check("这张卡也不许带 repeat=weekly（他没点过哪天）",
            (_h4.get("repeat") or "") != "weekly",
            (_h4.get("repeat"), [(s.get("weekday")) for s in _h4.get("slots") or []]))
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


def batch_i_no_internal_note():
    c = Checker()
    title("I 批 · 系统自己写进会话的挂卡标记，不许被当成回话念给学生")
    # 真机现场：连着两次说「那就周六吧」，第二次的回答整句就是
    #   「已生成待办确认：健身｜2026-10-17（周六）08:00-09:30」，而且一张卡都没有。
    # 那句是系统往**会话历史**里记的账（给"读历史"那几支认"这条挂过卡"用），
    # 模型读到就原样复读 —— 学生看到的是一句没人话的账目。
    import app.main as M

    class _EchoEngine:
        def __init__(self, *a, **kw):
            pass

        async def run(self, message, history=None):
            last = [m.get("content") or "" for m in (history or []) if m.get("role") == "assistant"]
            return {"answer": (last[-1] if last else "（没历史可读）"),
                    "trace": [{"step": 1, "phase": "💡 最终回答", "answer": "复读上一句"}],
                    "options": [], "awaiting_choice": False}

    async def quiet_sem(text, conv=None, override=None, **kw):
        return {"intent": "chat", "module": "planner"}

    real_sem, real_engine = M.classify, M.AgentEngine
    with sandbox():
        set_settings({"api_key": "fake-key-for-test-only",
                      "base_url": "https://api.deepseek.com", "model": "m"})
        cli = make_client()
        try:
            M.classify = quiet_sem
            M.AgentEngine = _EchoEngine
            cli.post("/api/chat", json={"message": "帮我加个游泳在周六",
                                        "session_id": "t43-i", "module": "planner"})
            # 第二句故意不含任何时间/事项线索，确定性地走不到别处（不会被"补挂确认条"
            # 那一支截走）—— 上一版我拿「周六」当第二句，它被 3c-pre2 接走了，
            # 于是这条断言根本没碰到保险本身，是个假绿的测试。
            r = cli.post("/api/chat", json={"message": "你刚才说的那是什么话",
                                            "session_id": "t43-i", "module": "planner"})
            ans = r.json().get("answer") or ""
            c.check("这一句确实走到了引擎（否则下面两条测不到东西）",
                    "复读上一句" in json.dumps(r.json().get("trace") or [], ensure_ascii=False),
                    json.dumps(r.json().get("trace") or [], ensure_ascii=False)[:120])
            c.check("回话里不许出现「已生成待办确认：」这种系统记账句",
                    not ans.startswith("已生成待办确认") and "已生成待办确认" not in ans,
                    ans[:80])
            c.check("换掉之后仍要有一句人话（不许回空串）",
                    bool(ans.strip()) and "已生成" not in ans, ans[:80])
        finally:
            M.classify, M.AgentEngine = real_sem, real_engine
    return c



def batch_j_strip_not_veto():
    """J 批 · 事名里的时间残渣走**擦除**，不走否决表（2026-10-11 探针四处）。

    `_TITLE_NOISE` 是 `any(w in t)` 的**否决表**：往里塞词 = 整串不算事名 = 系统反问
    「要安排的是什么事」。学生明明说了要干什么，却被问一句 —— 比挂个丑名字更坑。
    所以这一批同时钉代码形状（那张表里不许有单字）和真行为（擦完得是干净名字）。
    """
    c = Checker()
    title("J 批 · 残渣擦干净，但不许靠往否决表塞单字来做到")
    from app.modules import planner as P

    # ① 代码形状：否决表里每一项都必须是两字起。
    #    「半」塞进去的后果：「半夜」→「夜」、「半小时」→「小时」，
    #    而这两个都是 `_TIME_WORDS` 里的有效档词/时长 —— 一次改坏一整类句子。
    c.check("形状：`_TITLE_NOISE` 里没有单字（那张表整串否决，单字迟早咬掉正经词）",
            all(len(w) >= 2 for w in P._TITLE_NOISE),
            [w for w in P._TITLE_NOISE if len(w) < 2])
    c.check("形状：「半」「开始」都不在那张否决表里（走的是擦除，不是否决）",
            "半" not in P._TITLE_NOISE and "开始" not in P._TITLE_NOISE,
            P._TITLE_NOISE)

    # ② 真行为：擦完是干净名字
    for s, want in (("三点开始值日", "值日"),
                    ("晚上七点开始写作业", "写作业"),
                    ("6点开始晨跑", "晨跑"),
                    ("周三上午八点到九点开始班会", "班会"),
                    ("十二点整集合", "集合"),
                    ("12点整集合", "集合"),
                    ("傍晚六点半踢球", "踢球")):
        got = P._pick_title(s)
        c.check(f"「{s}」→ 事名「{want}」（不是残渣）", got == want, got)

    # ③ 反证：这次补的擦除不许咬到正经名字
    for s, want in (("运动会开幕式", "运动会开幕式"),
                    ("半夜三点起来喝水", "起来喝水"),
                    ("半小时后交作业", "后交作业")):
        got = P._pick_title(s)
        c.check(f"不误伤：「{s}」的事名照旧是「{want}」", got == want, got)

    # ④ 「傍晚」两张表都得登着：`_PART_WORDS` 认它拨档、`_TIME_WORDS` 才擦得掉。
    #    只登一张的实测后果 = 卡片名「傍晚踢球」（档对、名字脏）。
    c.check("「傍晚」在这两张表里都在（一处登记一处漏 = 档对名字脏）",
            "傍晚" in P._TIME_WORDS
            and any("傍晚" in words for _name, words in P._PART_WORDS),
            ("傍晚" in P._TIME_WORDS, P._PART_WORDS))
    c.check("反向：「傍晚六点」照样拨到 18:00（补进时间词表不许把档弄丢）",
            P.parse_time_spec("傍晚六点踢球")["start"] == "18:00",
            P.parse_time_spec("傍晚六点踢球"))
    return c



def batch_k_category_word_in_middle():
    """K 批 · 类别词夹在中间时，不许把整句判成"没说事名"（2026-10-11 用户两张截图）。

    病根链：`_strip_title_noise` 只擦**结尾**的「待办/代办/事项/日程」，中间那颗留着
    → `looks_like_thing` 拿 `_TITLE_NOISE` 做 `any(w in t)` → **整串**不算事名
    → 系统反问「要安排的是什么事」，而「去图书馆」白纸黑字在他那句话里。
    中文写法一样中招，跟阿拉伯数字无关。
    """
    c = Checker()
    title("K 批 · 「加个代办去图书馆」：说了的事不许被当成没说")
    from app.modules import planner as P

    # ① 机制本身（反证）：类别词留在中间，否决表就是会把整串打死。
    #    没有这一条，下面那四条"名字抠出来了"全是假绿 —— 看不见否决表在起作用。
    c.check("病根复刻：「代办去图书馆」这串过不了判真（否决表整串打死）",
            P._title_ok("代办去图书馆") is False, P._title_ok("代办去图书馆"))

    for s, want in (("周六加个代办去图书馆", "图书馆"),
                    ("周日加个待办去图书馆", "图书馆"),
                    ("帮我安排个日程去体检", "体检"),
                    ("周六加个事项去医务室", "医务室"),
                    ("代办：交作业 周六", "交作业")):
        got = P._pick_title(s)
        c.check(f"「{s}」→ 事名「{want}」（不是反问）", got == want, got)
        card = P.todo_slots_proposal(s) or {}
        c.check(f"「{s}」的卡片标题也是「{want}」（残渣不许印到卡上）",
                card.get("title") == want, card.get("title"))

    # ② 反向：擦完什么都不剩的那种，照旧该问 —— 这次改动不许把追问能力打掉
    for s in ("加个待办", "周六加个代办", "帮我安排个日程"):
        c.check(f"只有类别词、没有事情：「{s}」仍判不出事名（该问）",
                P._pick_title(s) == "待办", P._pick_title(s))
        c.check(f"「{s}」也不许硬摊一张候选卡",
                P.todo_slots_proposal(s) is None, P.todo_slots_proposal(s))

    # ③ 形状：否决表照旧留着这几个词（它管的是"只剩类别词"那种形状）
    c.check("形状：`_TITLE_NOISE` 里四个类别词一个都没撤（两种形状各管各的）",
            all(w in P._TITLE_NOISE for w in ("待办", "代办", "事项", "日程")),
            P._TITLE_NOISE)

    # ④ 副作用按住：「日程表」是名词的一部分，擦成「整理表」比反问更伤
    c.check("「整理日程表」不许被擦成「整理表」（错名比问一句更坑）",
            P._pick_title("整理日程表") != "整理表", P._pick_title("整理日程表"))
    return c



def batch_l_arabic_weekday():
    """L 批 · 「周6 / 每周6 / 星期六」：读日子的地方以前只认一种写法（2026-10-11 三张截图）。

    一条链上八处各自手写 `[一二三四五六日天]`，于是学生写阿拉伯数字时：
    周期丢（`每周6` 不认）→ 掉进摊候选那条路（四段里一段周六都没有）→
    擦标题只擦掉「每周」、把「6」留在事名上 → **卡片标题印着「6去图书馆」**。
    顺带挖出更早的一条：`_WEEKDAY_RE` 连「星期X / 礼拜X」都不认，
    「星期六去图书馆」不但日子读不出，`_pick_title` 把**整句**当事名。
    """
    import datetime
    c = Checker()
    title("L 批 · 周6 / 每周6 / 星期六：一份字符类管住八处")
    from app.modules import planner as P

    today = datetime.date.today()
    nxt = lambda want: (today + datetime.timedelta(
        days=(want - today.isoweekday()) % 7 or 7)).isoformat()
    next_sat, next_sun = nxt(6), nxt(7)

    # ① 用户截图原句
    sp = P.parse_time_spec("周6加个代办去图书馆")
    c.check("「周6加个代办去图书馆」→ 日子读得出（下周六）",
            sp["date"] == next_sat, sp["date"])
    c.check("同一句的事名是「图书馆」（不是「6去图书馆」）",
            P._pick_title("周6加个代办去图书馆") == "图书馆",
            P._pick_title("周6加个代办去图书馆"))

    # ② 周期：数字写法与中文写法**结果必须一模一样**
    for cn, ar in (("每周六去图书馆", "每周6去图书馆"),
                   ("每个礼拜六去图书馆", "每个礼拜6去图书馆"),
                   ("每个星期六去图书馆", "每个星期6去图书馆")):
        a, b = P.parse_time_spec(cn), P.parse_time_spec(ar)
        c.check(f"「{ar}」与「{cn}」同解（都是 weekly + 周六）",
                (a["repeat"], a["weekday"]) == (b["repeat"], b["weekday"]) == ("weekly", 6),
                (a["repeat"], a["weekday"], b["repeat"], b["weekday"]))
        c.check(f"「{ar}」的事名干净", P._pick_title(ar) == "图书馆", P._pick_title(ar))
    for s, want in (("每周3游泳", 3), ("每周1升旗", 1), ("每周7聚餐", 7)):
        c.check(f"「{s}」→ 星期 {want}", P.weekly_repeat_day(s) == want,
                P.weekly_repeat_day(s))

    # ③ 「星期X / 礼拜X」以前压根不读日子，还把整句当事名
    for s, want_day in (("星期六去图书馆", next_sat), ("礼拜六去图书馆", next_sat),
                        ("星期天去图书馆", next_sun)):
        c.check(f"「{s}」→ 日子 {want_day}（以前读不出）",
                P.parse_time_spec(s)["date"] == want_day, P.parse_time_spec(s)["date"])
        c.check(f"「{s}」的事名不许是整句", P._pick_title(s) == "图书馆", P._pick_title(s))

    # ④ ★ 歧义反证：数字后面紧跟「点」的那颗是**钟点**，认成星期几就静默排错日子
    sp = P.parse_time_spec("下周6点开会")
    c.check("「下周6点开会」不许把 6 读成周六（日子留空、钟点照给 06:00）",
            sp["date"] is None and sp["start"] == "06:00", (sp["date"], sp["start"]))
    sp = P.parse_time_spec("每周6点起床")
    c.check("「每周6点起床」不许变成每周六（repeat 留空、钟点 06:00）",
            not sp["repeat"] and P.weekly_repeat_day("每周6点起床") is None
            and sp["start"] == "06:00", (sp["repeat"], sp["start"]))
    sp = P.parse_time_spec("周日6点看升旗")
    c.check("「周日6点看升旗」两样都要：日子=周日、钟点=06:00",
            sp["date"] == next_sun and sp["start"] == "06:00", (sp["date"], sp["start"]))

    # ⑤ 次数不是星期几（14b 的阿拉伯版）：「每周6次」照旧走频次，不许读成周六
    c.check("「每周6次去图书馆」→ 次数 6、周期不认周六",
            P.weekly_times("每周6次去图书馆") == 6
            and P.weekly_repeat_day("每周6次去图书馆") is None,
            (P.weekly_times("每周6次去图书馆"),
             P.weekly_repeat_day("每周6次去图书馆")))

    # ⑥ 形状：读日子的那几条正则都从**同一份**字符类取（八处各写一份就是这次的病根）
    src = io.open(PLANNER, encoding="utf-8").read()
    lines = src.splitlines()
    for name in ("_WEEKDAY_RE", "_DAY_WORD_RE", "_REPEAT_WEEKDAY_RE", "_REPEAT_PHRASE_RE"):
        at = [i for i, ln in enumerate(lines) if ln.startswith(name + " = ")]
        window = " ".join(lines[at[0]:at[0] + 5]) if at else ""
        c.check(f"形状：{name} 用共享字符类 `_WD_CHARS`，不是自己手写一串",
            bool(at) and "_WD_CHARS" in window, (name, bool(at)))
    # 判据打在**代码行**上：注释里引用旧原话（"以前这里是 .index(ch)"）是历史说明，
    # 不算违规 —— 但断言本身不许因此放宽。
    _pd_code = "\n".join(ln for ln in src.split("def _pick_date(")[1].split("\ndef ")[0].splitlines()
                         if not ln.strip().startswith("#"))
    c.check("形状：`_pick_date` 里不许再自己数下标（「周6」曾在那儿抛 ValueError）",
            ".index(" not in _pd_code, "还留着 index 写法")
    c.check("形状：那份字符类只有一份定义", src.count("_WD_CHARS = ") == 1,
            src.count("_WD_CHARS = "))
    return c



def batch_m_family_sweep():
    """M 批 · 整族扫：同族写法一次列全，逐条比**期望值**（2026-10-11 用户：「不要只改我截图的」）。

    这张表来自 `D:/campustime-scratch/sweep_family.py`：四族、162 个检查点。
    第一轮扫出来 160 对 2 错 —— 错的两条都是"动作/类别词出现在另一头"：
    「周六去图书馆**加个**代办」→「图书馆加个」（类别词在结尾擦掉了，尾巴上那颗没人管）、
    「周六加个**提醒**去拿快递」→「提醒去拿快递」（「提醒」当时不在容器词族里）。
    把整张表留在这里，是为了下一个改代码的人**别只补被投诉的那一句**。
    """
    import datetime
    c = Checker()
    title("M 批 · 整族扫：周几 / 容器词 / 时间残渣 / 数字钟点，四族一次过")
    from app.modules import planner as P

    today = datetime.date.today()

    def _nxt(want):
        return (today + datetime.timedelta(days=(want - today.isoweekday()) % 7 or 7)).isoformat()

    SAT, SUN, MON = _nxt(6), _nxt(7), _nxt(1)

    # (句子, 期望 date, 期望 repeat, 期望 weekday, 期望 start, 期望事名或 None=不查)
    A = [
        ("周6去图书馆", SAT, "", None, None, "图书馆"),
        ("周六去图书馆", SAT, "", None, None, "图书馆"),
        ("星期六去图书馆", SAT, "", None, None, "图书馆"),
        ("礼拜六去图书馆", SAT, "", None, None, "图书馆"),
        ("星期6去图书馆", SAT, "", None, None, "图书馆"),
        ("下周六去图书馆", SAT, "", None, None, "图书馆"),
        ("下礼拜六去图书馆", SAT, "", None, None, "图书馆"),
        ("下星期6去图书馆", SAT, "", None, None, "图书馆"),
        ("本周日去图书馆", SUN, "", None, None, "图书馆"),
        ("星期天去图书馆", SUN, "", None, None, "图书馆"),
        ("周一开会", MON, "", None, None, "开会"),
        ("周1开会", MON, "", None, None, "开会"),
        ("周0去图书馆", None, "", None, None, None),      # 0 算周日还是周一我们说了不算：宁可少认
        ("周8去图书馆", None, "", None, None, None),      # 不是星期几
        ("每周6去图书馆", None, "weekly", 6, None, "图书馆"),
        ("每周六去图书馆", None, "weekly", 6, None, "图书馆"),
        ("每个星期6去图书馆", None, "weekly", 6, None, "图书馆"),
        ("每个礼拜六去图书馆", None, "weekly", 6, None, "图书馆"),
        ("每星期6去图书馆", None, "weekly", 6, None, "图书馆"),
        ("每周日晨跑", None, "weekly", 7, None, "晨跑"),
        ("每周7聚餐", None, "weekly", 7, None, "聚餐"),
        ("每周3游泳", None, "weekly", 3, None, "游泳"),
        ("每周6点起床", None, "", None, "06:00", "起床"),   # 那个 6 是钟点，不是周六
    ]
    for s, wd, wr, wwd, ws, wt in A:
        sp = P.parse_time_spec(s)
        got = (sp.get("date"), sp.get("repeat"), sp.get("weekday"), sp.get("start"))
        c.check(f"A｜{s}", got == (wd, wr, wwd, ws), (got, (wd, wr, wwd, ws)))
        if wt is not None:
            c.check(f"A·名｜{s}", P._pick_title(s) == wt, P._pick_title(s))

    # (句子, 期望事名)：容器词在**开头/中间/结尾**三种位置都要擦干净
    B = [
        ("周六加个代办去图书馆", "图书馆"),
        ("周六加个待办去图书馆", "图书馆"),
        ("周日添加一个日程去体检", "体检"),
        ("帮我记个事项去医务室", "医务室"),
        ("周六安排个待办交作业", "交作业"),
        ("加个代办：周五还书", "还书"),
        ("周六去图书馆加个代办", "图书馆"),          # 整族扫查出来的：尾巴上露出「加个」
        ("周六加个提醒去拿快递", "拿快递"),          # 整族扫查出来的：「提醒」是容器词
        ("加个待办", "待办"),                        # 擦完什么都不剩 → 该问，不许硬摊卡
        ("帮我安排个日程", "待办"),
        ("周六加个代办", "待办"),
        ("加个提醒", "待办"),
        ("整理日程表", "待办"),                      # 「表」保护：不许擦成「整理表」
        ("代办：交作业 周六", "交作业"),
        ("查件", "查件"),                            # 反向：量词不是谁都吃
        ("做个手工", "做个手工"),
    ]
    for s, want in B:
        c.check(f"B｜{s}", P._pick_title(s) == want, P._pick_title(s))

    # 「该问」那一档不许顺手摊出一张卡（名字判不出 = 不出卡，红线 13）
    for s in ("加个待办", "加个提醒", "周六加个代办"):
        c.check(f"B·不出卡｜{s}", P.todo_slots_proposal(s) is None, P.todo_slots_proposal(s))

    C = [
        ("三点开始值日", "值日"), ("晚上七点开始写作业", "写作业"),
        ("6点开始晨跑", "晨跑"), ("早上6点半跑步", "跑步"),
        ("傍晚六点半踢球", "踢球"), ("十二点整集合", "集合"),
        ("12点整点名", "点名"), ("下午两点半复习", "复习"),
        ("周六上午九点小组讨论", "小组讨论"), ("明天下午三点到四点面谈", "面谈"),
    ]
    for s, want in C:
        c.check(f"C｜{s}", P._pick_title(s) == want, P._pick_title(s))

    D = [
        ("早上6点半跑步", "06:30", None), ("早上六点半跑步", "06:30", None),
        ("早上6:30跑步", "06:30", None), ("晚上8点半到9点半复习", "20:30", "21:30"),
        ("下午2点30分开会", "14:30", None), ("12点半吃午饭", "12:30", None),
        ("中午十二点半吃饭", "12:30", None), ("半小时后交作业", None, None),
        ("下周6点开会", "06:00", None), ("周日6点看升旗", "06:00", None),
        ("晚上九点泡脚", "21:00", None), ("早上6点半", "06:30", None),
    ]
    for s, wstart, wend in D:
        sp = P.parse_time_spec(s)
        c.check(f"D｜{s}", (sp.get("start"), sp.get("end")) == (wstart, wend),
                ((sp.get("start"), sp.get("end")), (wstart, wend)))
    return c



def batch_n_no_past_dates():
    """N 批 · 待办不许落在**已经过去的日子**上（用户 2026-10-11：「代办不能加到过去的日期」）。

    真机引子：「晚上八点开始值日」那轮，语义层吐了一个 `2023-04-18`，
    确定性分支照收 → 卡片挂出去、学生刷新一看**永远找不到**；
    另一种更常见的走漏是卡片过夜 —— 昨天勾好的时段，今天点【确认加入】。
    所以出卡那一路（四条）和写库那一路（两个接口）都查**同一份**判据
    `store.is_past_date`，不许各写一个比较。
    """
    import datetime
    from collections import Counter
    c = Checker()
    title("N 批 · 过去的日子：出卡不兜底、写库不进门")
    from app.modules import planner as P
    from app.store import is_past_date

    today = datetime.date.today()
    y = (today - datetime.timedelta(days=1)).isoformat()
    t0 = today.isoformat()
    tm = (today + datetime.timedelta(days=1)).isoformat()

    # ① 判据本身
    for d, want in (("2023-04-18", True), (y, True), (t0, False), (tm, False)):
        c.check(f"判据：{d} → {want}", is_past_date(d) is want, is_past_date(d))
    for bad in ("", "昨天", "not-a-date"):
        c.check(f"判据：读不出格式「{bad}」不拦（交给必填校验，别顺手吞掉别的错）",
                is_past_date(bad) is False, is_past_date(bad))

    # ② 出卡那四条路一律不兜底到过去的日子
    c.check("parse_add_todo：过去的日子不建卡",
            P.parse_add_todo("2023年4月18号晚上八点写作业") is None,
            P.parse_add_todo("2023年4月18号晚上八点写作业"))
    c.check("auto_todo_proposal：过去的日子不替他挑时段",
            P.auto_todo_proposal(f"{y}加个健身") is None, y)
    c.check("span_only_proposal：兜底日期给了过去的日子 → 不出卡",
            P.span_only_proposal("晚上八点写作业", "写作业", y) is None,
            P.span_only_proposal("晚上八点写作业", "写作业", y))
    c.check("retime_todo_proposal：不许把已有待办挪到过去的日子",
            P.retime_todo_proposal({"title": "写作业", "date": t0, "start": "20:00",
                                    "end": "21:00"}, f"改到{y}晚上八点") is None,
            P.retime_todo_proposal({"title": "写作业", "date": t0, "start": "20:00",
                                    "end": "21:00"}, f"改到{y}晚上八点"))

    # ③ 反向：这道闸不许把正常句子挡掉
    c.check("反向：今天/将来的日子照旧出卡（闸不是把功能关掉）",
            P.span_only_proposal("晚上八点写作业", "写作业", t0) is not None, t0)
    c.check("反向：「明天下午三点交表」照旧建得出卡",
            P.parse_add_todo(f"明天下午三点交表") is not None,
            P.parse_add_todo("明天下午三点交表"))

    # ④ 形状：四条出卡路 + 两个写入口都查同一份判据
    src = io.open(PLANNER, encoding="utf-8").read()
    msrc = io.open(MAIN, encoding="utf-8").read()
    c.check("形状：planner 里至少四处用 `is_past_date`（四条出卡路一条不落）",
            src.count("is_past_date(") >= 4, src.count("is_past_date("))
    c.check("形状：两个写入口都查（单条 + 批量），判据不重写一遍",
            msrc.count("is_past_date(") >= 2 and "def is_past_date" not in msrc,
            msrc.count("is_past_date("))

    # ⑤ 端到端：写库那一层真的进不去
    with sandbox():
        from app.store import list_todos
        client = make_client()
        # ⚠️ 断言一律用**前后对比**，不能假设沙箱里是空的：
        #    `sandbox()` 是把真实 app/data 整个复制进去的（只清 SCRATCH 那几个文件），
        #    所以 `list_todos()` 一上来就有演示数据 —— 写"不该有"是假判据。
        base = [(t.get("title"), t.get("date")) for t in list_todos()]
        r = client.post("/api/todos", json={"title": "补作业", "date": y,
                                            "start": "20:00", "end": "21:00"})
        c.check("POST /api/todos 过去的日子 → 400", r.status_code == 400,
                (r.status_code, (r.json() or {}).get("error")))
        c.check("拒的是人话：说清「已经过去了」",
                "过去" in ((r.json() or {}).get("error") or ""), (r.json() or {}).get("error"))
        c.check("单条被拒之后库里一条没多",
                [(t.get("title"), t.get("date")) for t in list_todos()] == base,
                [(t.get("title"), t.get("date")) for t in list_todos()])
        r2 = client.post("/api/todos/batch", json={"title": "健身", "repeat": "",
                                                   "slots": [{"date": y, "start": "19:00",
                                                              "end": "20:00"}]})
        c.check("批量：勾的全是过去的日子 → 400，一条都不写",
                r2.status_code == 400
                and sorted([(t.get("title"), t.get("date")) for t in list_todos()]) == sorted(base),
                (r2.status_code, [(t.get("title"), t.get("date")) for t in list_todos()]))
        r3 = client.post("/api/todos/batch", json={"title": "健身", "repeat": "",
                                                   "slots": [{"date": y, "start": "19:00",
                                                              "end": "20:00"},
                                                             {"date": tm, "start": "19:00",
                                                              "end": "20:00"}]})
        got = [(t.get("title"), t.get("date")) for t in list_todos()]
        # 契约只有一句：**过去的日子一条都不许落库**。
        # 不写成"将来那条一定进库"—— 批量接口另有一道空档复核，将来时段撞上课表
        # 会被正当拒掉（实测就是这么红的：19:00 那格被占），那是接口在做对的事。
        added = [x for x in got if Counter(got)[x] > Counter(base)[x]]
        c.check("批量：混合勾选 → 新增里没有任何一条落在过去的日子",
                r3.status_code == 200 and not [a for a in added if a[1] == y],
                (r3.status_code, added))
        r4 = client.post("/api/todos", json={"title": "交表", "date": tm,
                                             "start": "15:00", "end": "16:00"})
        c.check("反向：将来的日子照旧写得进去", r4.status_code == 200, r4.status_code)
    return c



def batch_o_reminder_frame_words():
    """O 批 · 「记得 / 别忘了 / 提醒我 / 想起来 + 事件」这类**框词**不许留在事名里。

    学生说「提醒我周五交作业」，要办的事是「交作业」，"提醒我"是他给系统的**指令外壳**。
    旧代码只把开头的"我要/帮我"削掉，这类外壳整批漏在外面，实测写进日程的名字是
    「得我交作业」「想起来要买点水果」「别忘了还书」—— 判真还都过了。
    另一头是**动词不能被单独吃**：「加油打卡」被削成「油打卡」，那是把名字改坏，
    比留个外壳更伤（同 `(?!表)` 那条理由）。所以这一批两向都钉。
    """
    c = Checker()
    title("O 批 · 框词剥干净、动词不许误吃")
    from app.modules import planner as P

    # ① 框词族：在前 / 带人称 / 带"要" / 带日子 / 长句里
    for s, want in (
            ("记得提醒我周五交作业", "交作业"),
            ("想起来要买点水果", "买点水果"),
            ("别忘了周四还书", "还书"),
            ("千万别忘了交报告", "交报告"),
            ("帮我提醒妈妈生日", "妈妈生日"),
            ("给我提醒一下去取快递", "取快递"),
            ("记着带伞", "带伞"),
            ("记住带钥匙", "带钥匙"),
            ("忽然想起来要交表", "交表"),
            ("想起来交表", "交表"),
            ("记得要喝水", "喝水"),
            ("设个提醒吃药", "吃药"),
            ("提醒我明天开会", "开会"),
            ("提醒我们周五交作业", "交作业"),
            ("你提醒我喝水", "喝水"),
            # 他说的是「我妈」：不许吃掉这个"妈"，也不许替他补成"妈妈"
            ("提醒我妈生日买礼物", "妈生日买礼物")):
        got = P._pick_title(s)
        c.check(f"框词剥掉｜「{s}」→「{want}」", got == want, got)

    # ② 动词 + 数词 + 量词：成对出现才整体吃
    for s, want in (("记一条还信用卡", "还信用卡"),
                    ("加两条复习", "复习"),
                    ("添一件买牛奶", "买牛奶"),
                    ("一条还信用卡", "还信用卡"),
                    ("条还信用卡", "还信用卡"),
                    ("记一下交作业", "交作业")):
        got = P._pick_title(s)
        c.check(f"动量整体吃｜「{s}」→「{want}」", got == want, got)

    # ③ 反向：光杆动词开头**不许吃动词**（`len>=2` 那道护栏只挡得住"削完剩一个字"，
    #    「油打卡」三个字照样溜过去 —— 第一版注释说这条被护栏保住了，实测没有）
    for s, want in (("记账", "记账"), ("加油打卡", "加油打卡"),
                    ("打卡", "打卡"), ("备忘", "备忘"), ("还信用卡", "还信用卡"),
                    ("做个手工", "做个手工"), ("查件", "查件")):
        got = P._pick_title(s)
        c.check(f"不误吃动词｜「{s}」照旧是「{want}」", got == want, got)

    # ④ 只剩框词 = 判不出事名，该问（不许硬摊一张卡）
    for s in ("记得", "提醒我", "设个提醒"):
        c.check(f"只有框词｜「{s}」判不出事名", P._pick_title(s) == "待办", P._pick_title(s))

    # ⑤ 形状：框词那道擦除必须排在类别词那道**之前**（顺序错了会把「设个提醒」拆散）
    src = io.open(PLANNER, encoding="utf-8").read()
    i_frame = src.find("千万别忘了|千万记得")
    i_cat = src.find("(待办|代办|事项|日程|提醒)(?!表)")
    c.check("形状：框词擦除在中间类别词擦除之前（顺序是这次踩出来的）",
            0 < i_frame < i_cat, (i_frame, i_cat))
    return c


def batch_p_done_claim_not_routed_by_sample():
    """P 批 · 「去完图书馆了」被样本库按 add_todo 学死了，确定性层要把它夺回来。

    用户 2026-10-12 的截图：轨迹写着「听成 **办事**（加待办）｜依据：样本库」，
    卡片标题是「完图书馆」。本机 `app/data/intent_samples.json`（gitignored）里
    就躺着那条脏证据：
        {"text": "去完图书馆了", "intent": "add_todo", "hits": 1}
    —— 当年模型判错、还真出了卡，`remember()` 就照**错的意图**把这句学死了；
    现在它每次都以 1.0 **全命中**回 add_todo，而加待办那条分支排在标记完成之前，
    于是识别层再怎么对也轮不到它说话（红线 12 的老形状：学习层能单独接单）。

    钉四件事：
      ① 一条待办都不许多出来（新增那条路当场堵死）；
      ② 走的是标记完成那条路 —— 出确认条，等学生点头；
      ③ 思考轨迹不许再写着「加待办」：轨迹说假话比没有轨迹更坏；
      ④ 判定只算一次（`wants_done_todo(` 在 main.py 里只许出现一处，其余分支共用 `_done_claim`）。
    """
    import datetime as _dt
    c = Checker()
    title("P 批 · 样本库把「办完了」学成「新增」：确定性层的分层否决（截图「完图书馆」）")
    today = _dt.date.today().isoformat()
    with sandbox():
        from app.agent.intent import match_sample, remember
        from app.modules import planner as _P
        from app.store import add_todo, delete_todo, list_todos
        client = make_client()
        # 沙箱继承真实演示数据，先清干净：这一批要的是"库里只有那一条《图书馆》"
        for t in list_todos():
            delete_todo(t["id"])
        add_todo("图书馆", today, "19:00", "20:00")
        # 脏样本必须**自己种**：sandbox 会把真实样本库删掉（学到的句子不许进测试），
        # 不种就等于没复现现场。
        remember("去完图书馆了", "add_todo", {"module": "planner"})
        c.check("现场复现：样本库确实把这句回成 add_todo（全命中，字段照抄）",
                (match_sample("去完图书馆了") or {}).get("intent") == "add_todo",
                match_sample("去完图书馆了"))

        before = sorted(t["id"] for t in list_todos())
        sid = "p-done-claim-1"
        d = client.post("/api/chat", json={
            "message": "去完图书馆了", "session_id": sid,
        }).json()
        blob = json.dumps(d, ensure_ascii=False)
        opts = d.get("options") or []

        c.check("一条待办都没多出来（加待办那条路被堵死）",
                sorted(t["id"] for t in list_todos()) == before,
                [t["title"] for t in list_todos()])
        c.check("走的是标记完成：出了确认条，不是新增卡",
                any(o.get("kind") == "todo_done" for o in opts)
                and not any(o.get("kind") == "todo" for o in opts),
                [(o.get("kind"), o.get("title")) for o in opts])
        c.check("确认条上念的是《图书馆》，不是「完图书馆」那种残渣",
                any(o.get("kind") == "todo_done" and o.get("title") == "图书馆"
                    for o in opts),
                [(o.get("kind"), o.get("title")) for o in opts])
        c.check("轨迹不撒谎：整条响应里搜不到「加待办」",
                "加待办" not in blob, blob[:150])
        c.check("轨迹把这句报成「标记完成」（依据那一档仍如实写着样本库）",
                "标记完成" in blob, blob[:150])
        c.check("学生没点头之前，那条状态还不是 done",
                all(t.get("status") != "done" for t in list_todos()),
                [(t["title"], t.get("status")) for t in list_todos()])

        # 形状：这一句在整个请求里**只判一次**，多算一次就是多一个漂移点（红线 10/11b）。
        src = io.open(MAIN, encoding="utf-8").read()
        c.check("形状：main.py 里 wants_done_todo( 只许出现一处（其余分支共用 _done_claim）",
                src.count("wants_done_todo(") == 1, src.count("wants_done_todo("))

        # 形状：否决是在**判定层**做的，不是在下面的分支里各补一份（同 _period_block 的位置）。
        # 锚点用加待办分支那一行**独有的** `is_add_todo_answer(message)` ——
        # 拿 `INTENT_ADD_TODO)` 当锚点会先撞上上面 `_period_block` 那一处，测的就不是分支入口了。
        i_veto = src.find('_done_claim = wants_done_todo(message)')
        i_add = src.find("asking_add and is_add_todo_answer(message)")
        c.check("形状：否决在加待办分支入口之前（排在后面就拦不住了）",
                0 < i_veto < i_add, (i_veto, i_add))
        # 落地与识别同源：`_done_resultative` 必须是识别层唯一的判据
        c.check("形状：识别层只有一份动结式判据（_done_resultative 定义一次）",
                io.open(PLANNER, encoding="utf-8").read().count("def _done_resultative") == 1,
                _P._done_resultative("图书馆", "去完图书馆了"))
    return c


def main():
    results = [batch_a_time_words_registered(), batch_b_title_has_no_tail(),
               batch_c_tool_echo_never_shown(), batch_d_bare_clock_asked_in_place(),
               batch_e_period_carry(), batch_f_weekly_e2e(),
               batch_g_slot_answer_shape(), batch_h_model_exit_gate_behaviour(),
               batch_i_no_internal_note(), batch_j_strip_not_veto(),
               batch_k_category_word_in_middle(),
               batch_l_arabic_weekday(),
               batch_m_family_sweep(),
               batch_n_no_past_dates(),
               batch_o_reminder_frame_words(),
               batch_p_done_claim_not_routed_by_sample()]
    tp = sum(r.passed for r in results)
    tf = sum(r.failed for r in results)
    print(f"\n===== 真机批次查出的四个缺口总账：通过 {tp} 项，失败 {tf} 项 =====")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
