"""第一刀 B：润色不许站在关键路径上白等（实测 97 次里 20 次吃满 4.0s 拿回的却是模板）。

用户 2026-10-14 的诊断与决定（原话摘录）：
    「护栏不依赖 polish……真正靠 polish 的是承诺词守卫 + JSON 守卫，这两道不能跟着删——
      正确做法是先返回系统算好的卡片/话术（本就安全），再……绝不回退到"等满 4s 拿模板"」
    「顺手消掉同类出口不一致：候选时段 80ms 没接润色 vs 列待办/单条确认卡 3.2~3.9s 接了，
      统一成"先模板后异步"后 48 倍差消失」
   走法选 **B**（只删等待 + 统一出口），A（BackgroundTasks + 轮询）与 C 留给第二刀。

本刀定的规格（三类"系统已算好事实"的出口归到同一档）：
    · 卡片类（加待办确认卡 / 候选时段）**不润色** —— 文案就是系统算好的那句事实；
    · 长列表（列待办）**保留润色**，但超时从 4.0s 降到 ≤1.0s，超时/失败退回模板且不加消息；
    · 三族守卫一道都不许删：润色自守卫（承诺词 + JSON）、引擎 claim 守卫、出口内脏保险。

为什么 B 批用"哨兵"而不是比字符串：光断言 `_card_answer == _card_fallback` 会被绕过
（把 polish 换成一个恰好返回 fallback 的假货就绿了）。这里打桩一个**带明显标记的**假模型，
问的是"这条出口到底有没有经过模型的手"：卡片类不许出现哨兵、列待办必须出现哨兵 ——
两边一起看，才证明"摘的是位置不对的那一处"，不是"把润色整个关掉了"。
"""
import asyncio
import json as _json
import time

from _harness import Checker, make_client, sandbox, seed_todos, title, write_timetable, SEED_COURSES
from test_20_polish import _patch_client  # 复用同一套假模型（别抄第二份）

code = 0

SENTINEL = "【润色哨兵】这句话经过模型的手～"


def _say(client, msg, sid, module="planner"):
    return client.post("/api/chat", json={"message": msg, "module": module,
                                          "session_id": sid}).json()


def batch_a_shape():
    """A 批 · 常量与调用点：润色只剩一处，阈值 <=1.0s。"""
    c = Checker()
    title("A. 阈值与调用点（形状）")
    import app.agent.polish as PL
    import inspect

    c.check("POLISH_TIMEOUT ≤ 1.0s（润色是锦上添花，出不来就退模板，绝不拖住回复）",
            PL.POLISH_TIMEOUT <= 1.0, f"现在是 {PL.POLISH_TIMEOUT}")

    src = open("app/main.py", encoding="utf-8").read()
    c.check("main 里 `await polish_reply(` 只剩 1 处（列待办）",
            src.count("await polish_reply(") == 1,
            f"{src.count('await polish_reply(')} 处：" +
            str([ln.split("polish_reply")[0][:24] for ln in src.splitlines()
                 if "await polish_reply(" in ln]))
    # 确认卡那一档：从 _card_fallback 拼好到交出去，中间不许有润色
    i_fb = src.find("_card_fallback = (")
    i_off = src.find("_offer_response(\n                session_id, proposal, message, _card_answer")
    if i_off < 0:
        i_off = src.find("session_id, proposal, message, _card_answer")
    c.check("加待办确认卡这一档：_card_fallback → 交出之间没有 polish_reply",
            0 < i_fb < i_off and "polish_reply(" not in src[i_fb:i_off],
            f"_card_fallback 在 {i_fb}、交出在 {i_off}")
    # 超时分支一字节不许动（施工单 §2.1）
    body = inspect.getsource(PL.polish_reply)
    c.check("超时/失败的降级分支还在（except → fallback）",
            "except Exception:" in body and "return fallback" in body, "")
    return c


def batch_b_three_exits():
    """B 批 · 行为：三类出口归到同一档（卡片不碰模型、长列表照旧润色）。"""
    c = Checker()
    title("B. 三个出口实测：卡片类不经模型、列待办照旧经模型")
    from app.modules.planner import parse_add_todo
    from app.store import list_todos

    with sandbox():
        write_timetable(SEED_COURSES)
        seed_todos([
            {"title": "交实验报告", "date": "2099-01-01", "start": "10:00", "end": "11:00"},
            {"title": "取快递", "date": "2099-01-02", "start": "16:00", "end": "17:00"},
        ])
        client = make_client()
        r = _patch_client(SENTINEL, with_key=True)
        try:
            # ① 加待办确认卡（施工单点名要摘掉润色的那处）
            b1 = _say(client, "帮我加个游泳周四19:00", "t46-card")
            card = next((o for o in (b1.get("options") or [])
                         if isinstance(o, dict) and o.get("kind") == "todo_add"), None)
            c.check("确认卡出口：**没有**经过润色（回的是系统事实文案）",
                    SENTINEL not in (b1.get("answer") or "")
                    and "要不要把 **游泳** 排进日程" in (b1.get("answer") or ""),
                    (b1.get("answer") or "").replace("\n", " ")[:60])
            c.check("卡片字段一个都没变（照旧是系统算出来的那份）",
                    card is not None and card.get("title") == "游泳"
                    and card.get("start") == "19:00",
                    _json.dumps({k: (card or {}).get(k) for k in ("title", "date", "start", "end")},
                                ensure_ascii=False))
            base = parse_add_todo("帮我加个游泳周四19:00") or {}
            c.check("卡片 == parse_add_todo 的确定性解析（逐字段）",
                    card is not None and all(card.get(k) == base.get(k)
                                             for k in ("title", "date", "start", "end")),
                    str([(k, card.get(k) if card else None, base.get(k))
                         for k in ("title", "date", "start", "end")]))
            c.check("照旧等学生点确认（awaiting_choice 不受影响）",
                    b1.get("awaiting_choice") is True, str(b1.get("awaiting_choice")))

            # ② 候选时段（本就没接润色，作对照）
            b2 = _say(client, "帮我安排游泳", "t46-slots")
            c.check("候选时段出口：也没有经过润色（三类同档）",
                    SENTINEL not in (b2.get("answer") or ""),
                    (b2.get("answer") or "").replace("\n", " ")[:50])

            # ③ 列待办（施工单 §2.3：这条**保留**润色）
            b3 = _say(client, "看看我的待办", "t46-list")
            c.check("列待办出口：润色**还在**（没被顺手摘光 —— 证明本刀摘的是位置不对的那处）",
                    SENTINEL in (b3.get("answer") or ""),
                    (b3.get("answer") or "").replace("\n", " ")[:50])
        finally:
            r()
        # 没 key（沙箱默认）：列表内容必须是系统真读出来的那两条，一条不多一条不少
        b4 = _say(client, "看看我的待办", "t46-list-nokey")
        c.check("没 key → 模板话术里两条待办都在（数据来自 list_todos，不是模型编的）",
                "你目前的待办（共 2 条" in (b4.get("answer") or "")
                and "交实验报告" in (b4.get("answer") or "")
                and "取快递" in (b4.get("answer") or ""),
                (b4.get("answer") or "").replace("\n", " ")[:70])
        c.check("整批下来库里一条都没多（只出提案，不写库）",
                len(list_todos()) == 2, f"{len(list_todos())} 条")
    return c


def batch_c_timeout_bites():
    """C 批 · 计时：**直接量 `polish_reply` 这一层**。

    ⚠️ 第一版这里是端到端量的，写错了：慢回复实测 3066ms，看着像"阈值没生效"，
    其实是**判定层 classify 也吃了 1.5s 假货延迟**（沙箱里样本库被清、`_rule_hit` 落空），
    1.5 + 1.5 才是 3066 —— 端到端那个数字量的不是阈值，是两次模型调用之和。
    要测阈值就得隔离测这一层，别拿被它影响的总时长当判据。
    """
    c = Checker()
    title("C. 超时真的兜住（直接量润色这一层）")
    from app.agent.polish import polish_reply

    facts = {"intent": "list_todos", "count": 1,
             "todos": [{"title": "交实验报告", "date": "2099-01-01",
                        "weekday": "周五", "start": "10:00", "end": "11:00"}]}

    def _run(delay):
        r = _patch_client(SENTINEL, with_key=True, delay=delay)
        t0 = time.perf_counter()
        try:
            got = asyncio.run(polish_reply(facts, "模板话术"))
        finally:
            r()
        return (time.perf_counter() - t0) * 1000, got

    # 回复比阈值慢 → 阈值先撤：退回模板，而且没等满回复
    ms_slow, got_slow = _run(1.5)
    c.check("慢回复（1.5s）→ 退回模板，不出现润色文本", got_slow == "模板话术", repr(got_slow)[:40])
    c.check("慢回复：润色这一层 < 1.2s（阈值 ≤1.0s 真生效，不是等满 4s）",
            ms_slow < 1200, f"{ms_slow:.0f} ms")

    # 回复比阈值快 → 照旧润色（这条排掉"把润色整个关掉"这种假修法）
    ms_fast, got_fast = _run(0.15)
    c.check("快回复（0.15s）→ 润色照旧生效（不是把润色删了完事）",
            got_fast == SENTINEL, repr(got_fast)[:40])
    c.check("快回复也没被误杀", ms_fast < 1200, f"{ms_fast:.0f} ms")
    return c


def batch_d_guards_survive():
    """D 批 · 三族守卫一道都不许少（施工单 §3.2，红线 8）。"""
    c = Checker()
    title("D. 守卫族：润色自守卫 / 引擎 claim 守卫 / 出口内脏保险")
    import inspect
    import app.agent.polish as PL

    body = inspect.getsource(PL.polish_reply)
    c.check("润色自守卫①：承诺词 + 本轮没写操作 → 退模板",
            "has_promise(text)" in body and "executed" in body, "")
    c.check("润色自守卫②：回成 JSON/数组 → 判无效退模板",
            'text[:1] in ("{", "[")' in body, "")

    src = open("app/main.py", encoding="utf-8").read()
    c.check("引擎 claim 守卫还在（嘴上说办好、系统没写 → 如实纠正）",
            'claims_proposal_sent(result["answer"])' in src, "")
    c.check("出口内脏保险①还在（它把工具调用打成字 → 不许回显）",
            "looks_like_tool_echo(final_answer" in src, "")
    c.check("出口内脏保险②还在（系统便条不许当答案念给学生）",
            "_is_system_note(final_answer" in src, "")
    c.check("写库只走确认漏斗（本刀没给卡片类加任何写路径）",
            "save_pending" in src and src.count("def _offer_response") == 1,
            "漏斗仍是一处")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_46_polish_off_critical_path：润色不站在关键路径上")
    print("=" * 60)
    code |= batch_a_shape().summary("A 批（形状）")
    code |= batch_b_three_exits().summary("B 批（三出口同档）")
    code |= batch_c_timeout_bites().summary("C 批（计时）")
    code |= batch_d_guards_survive().summary("D 批（守卫族）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
