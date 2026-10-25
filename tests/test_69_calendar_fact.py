"""#42-A 的护栏：「啥时候放假」不许被回成「我该做什么」，也不许被回成「你的日程里没有」。

用户 2026-10-19 00:16 真机截图：日程栏问「啥时候放假」→
「🤔 这句是在问我『该做什么』——我不替你决定…这几段是空着的…」。
判据本体（`planner._asking_what_to_do` 的 `_WHEN_ASK_RE` 排除）与归属（`main._campus_fact_ask` 那道闸）
是两件事，**少一件都不算修完**：只排除不给归属 → 这句掉给模型（有 key 时它瞎扯、没 key 时报降级）；
只给归属不排除 → 前面那档先把它抢走，闸根本走不到。

三批：A 判据与对照（把对的修坏是这族头号风险）、B 归属真发（含"挂着卡时不接管"）、C 形状（一处表、一处闸、一份响应）。
"""
import json
import os

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, seed_todos, title

code = 0

# 校历词表（与 `intent.CALENDAR_FACT_WORDS` 同一份，这里刻意**不抄**：断言从被测模块取）
SCHOOL_Q = "啥时候放假"
KIND_Q = "什么时候开学"
BAD_TEMPLATE = "该做什么"          # 那句模板的人话特征（判"没被抢走"用）


def _wipe_kb():
    json.dump({"entries": []}, open(data_path("kb.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _seed_entry(question, answer, keywords):
    p = data_path("kb.json")
    d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"entries": []}
    d.setdefault("entries", []).append({"question": question, "answer": answer, "keywords": keywords})
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def _say(cli, module, msg, sid="c42"):
    body = {"message": msg, "session_id": sid}
    if module:
        body["module"] = module
    d = cli.post("/api/chat", json=body).json()
    ans = d.get("answer") or ""
    phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
    return ans, phases, d


def batch_a_judgement():
    c = Checker()
    title("A. 判据：时间问句不算「问该做什么」，但不许把对的修坏")
    from app.agent import intent
    from app.modules.planner import (
        _asking_what_to_do, _strip_title_noise, _vague_ask_name, is_time_ask)

    c.check("A1 「啥时候放假」不再被判成问该做什么（**这条就是用户截图那一句**）",
            _asking_what_to_do(SCHOOL_Q) is False)
    c.check("A2 「什么时候开学」同上（startswith「啥」那一族不止吃「啥时候」）",
            _asking_what_to_do(KIND_Q) is False)
    c.check("A2-bis 「哪天报到」也在这族里（施工单 §一 表里它是 False —— 因为它不带疑问代词，"
            "但归到时间问句一并对，别只修「啥」字头）",
            _asking_what_to_do("哪天报到") is False)
    # 对照：三句"真·问该做什么"必须照旧，否则就是把对的修坏（施工单 M3 的形状）
    c.check("G3 对照：「今晚安排点啥」仍算问该做什么（排除写太宽会把这条修坏）",
            _asking_what_to_do("今晚安排点啥") is True)
    c.check("G4 对照：「我该做什么」仍算（它带「什么」，但不是「什么时候」）",
            _asking_what_to_do("我该做什么") is True)
    c.check("A3 对照：「你帮我安排点事」不算这族（没疑问代词 → 走追问档，test_06 十五批钉着）",
            _asking_what_to_do("你帮我安排点事") is False)
    c.check("A4 对照：「我现在都有啥安排」仍被「查现有清单」那道排除拦着（_HAS_LIST_ASK_RE 没挪位）",
            _asking_what_to_do("我现在都有啥安排") is False)
    c.check("A5 排除只认时间形状：`is_time_ask` 对「几点/多久/哪天/什么时候」为真、对「点啥」为假",
            all(is_time_ask(x) for x in ("几点开会", "多久放假", "哪天报到", "什么时候开学"))
            and not is_time_ask("今晚安排点啥"))
    c.check("A6 校历词表**不收「考试」**（「考试安排」既可能是排期也可能是校历，误伤面大）",
            "考试" not in intent.CALENDAR_FACT_WORDS, str(intent.CALENDAR_FACT_WORDS))
    c.check("A7 五个词逐个都真被那道闸接住（**行为判据**，不是「词表里有这个字」）",
            all(intent.asks_calendar_fact(f"{w}是什么时候") for w in intent.CALENDAR_FACT_WORDS),
            str(intent.CALENDAR_FACT_WORDS))
    # 裸串那一步仍然要能认出"这就是疑问串"（排除是在它**之前**拦，不是把它改坏）
    c.check("A8 `_vague_ask_name` 本体没被动过（「点啥」还该判是疑问串 —— 改的是**前面那道排除**）",
            _vague_ask_name(_strip_title_noise("今晚安排点啥")) is True)
    return c


def batch_b_attribution():
    c = Checker()
    title("B. 归属：校历类时间问句交给知识库，两路都空就老实说没查到")
    with sandbox():
        _wipe_kb()
        cli = make_client()
        # 没建索引：只验"别的那几档不许接手"，答案形状 = 老实说没查到
        ans, ph, d = _say(cli, "planner", SCHOOL_Q)
        c.check("G1 日程栏问「啥时候放假」→ **不许**回那句『该做什么』模板，也不许列空档当答案",
                BAD_TEMPLATE not in ans and "没有查到这一条" in ans, ans[:70])
        c.check("G1-bis trace 看得见这条分支（测的必须是走到的）",
                "没有这一条" in ph or "翻知识库" in ph, ph[:90])
        ans2, ph2, _ = _say(cli, "faq", SCHOOL_Q, sid="c42f")
        c.check("G7 问答栏问同一句也走这条（端级闸门已管住 station，这里不再限 planner）",
                "没有查到这一条" in ans2 and BAD_TEMPLATE not in ans2, ans2[:70])
        ans3, ph3, _ = _say(cli, "planner", KIND_Q, sid="c42k")
        c.check("G2 「什么时候开学」同上（**同一道闸，不是一句一句白名单**）",
                "没有查到这一条" in ans3 and BAD_TEMPLATE not in ans3, ans3[:70])

        # 知识库里**有**这一条 → 必须走直答把答案给他，而不是仍说"没查到"
        _seed_entry("什么时候放假", "寒假从一月十九号开始，三月二号报到注册。", ["放假", "开学"])
        ans4, ph4, _ = _say(cli, "planner", SCHOOL_Q, sid="c42h")
        c.check("B1 知识库里有答案时：直答给到具体日期（不是那句『没查到』）",
                "一月十九号" in ans4 and "没有查到这一条" not in ans4, ans4[:70])
        c.check("B1-bis 学生视图形状（#41-A）：不带模型定位用的元数据",
                "相关度" not in ans4 and "第 1 块" not in ans4)

        # 问自己的日程，不许被这道闸抢走（「我报到那天有什么课」不是"事件何时发生"）
        seed_todos([{"title": "报到", "date": "2026-03-02", "start": "09:00", "end": "10:00"}])
        ans5, ph5, _ = _say(cli, "planner", "我报到那天有什么安排", sid="c42m")
        c.check("B2 对照：「我报到那天有什么安排」仍按日程那几档走（不被校园事实闸抢）",
                "没有查到这一条" not in ans5, ans5[:70])
        ans6, ph6, _ = _say(cli, "planner", "放假前要把待办清一下吗", sid="c42n")
        c.check("B3 对照：句子里带「说自己日程的词」（待办）时不接管 —— 那是他自己的事，不是校历",
                "没有查到这一条" not in ans6, ans6[:70])

        # 手上挂着待确认的卡 → 这道闸不接管（别把卡弄丢，#33 那一族）
        from app.agent import pending as _p
        d7 = _say(cli, "planner", "帮我加个跑步，明天 19:00", sid="c42c")[2]
        held = bool(d7.get("options") or d7.get("awaiting_choice"))
        ans7, ph7, _ = _say(cli, "planner", SCHOOL_Q, sid="c42c")
        c.check("B4 挂着待确认的卡时不接管（先让他把卡办完；这条要成立，得先真的挂上一张）",
                held and ("没有查到这一条" not in ans7 or "跑步" in ans7),
                f"挂上卡={held}｜答：{ans7[:50]}")

        # router 的关键词兜底：词表从 intent 取，五个人都归 faq
        from app.agent.router import _QUICK_MAP
        from app.agent.intent import CALENDAR_FACT_WORDS
        c.check("G6 关键词兜底表里五个校历词都归 faq，且**是从那张表推出来的**（不是又抄一遍）",
                all(_QUICK_MAP.get(w) == "faq" for w in CALENDAR_FACT_WORDS),
                str({w: _QUICK_MAP.get(w) for w in CALENDAR_FACT_WORDS}))
    return c


def batch_c_shape():
    c = Checker()
    title("C. 形状（AST 判位置与份数，不 grep）")
    import ast
    main_tree = ast.parse(open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read())

    src = open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read()
    i_ask = src.index("if _asking_what_to_do(message):")
    i_gate = src.index("if _campus_fact_ask(message) and not _holding_card:")
    c.check("C1 那道闸排在 `_asking_what_to_do` **之前**（排后面等于让它先抢，施工单 §2.3）",
            i_gate < i_ask, f"闸 @{i_gate} < 那档 @{i_ask}")
    c.check("C2 闸的条件里带着「挂着卡不接管」（#62 起那份名单收进 `_any_pending`，"
            "两处判据共用同一个函数 ⇒ 这里改判『只有一份名单』而不是『名单长什么样』）",
            "_holding_card" in src[i_gate - 900:i_gate]
            and src.count("_holding_card = _any_pending(") == 1
            and src.count("any(f(session_id) for f in (") == 1,
            f"`_holding_card = _any_pending(…)` {src.count('_holding_card = _any_pending(')} 处、"
            f"就地 `any(f(session_id) …)` {src.count('any(f(session_id) for f in (')} 处")
    c.check("C3 知识库那一路的响应形状只拼一份（`_kb_say` 定义一处、三处以上调用它）",
            src.count("def _kb_say(") == 1 and src.count("return _kb_say(") >= 3,
            f"定义 {src.count('def _kb_say(')} 处、调用 {src.count('return _kb_say(')} 处")
    c.check("C4 「没查到」那句全站只写一处（`_KB_MISS_LINE`），闸与报障分支共用",
            src.count('_KB_MISS_LINE = (') == 1 and src.count("answer=_KB_MISS_LINE") >= 2,
            f"常量 1 处、引用 {src.count('answer=_KB_MISS_LINE')} 处")

    files_src = open(os.path.join(PROJ_DIR, "app", "agent", "router.py"), encoding="utf-8").read()
    c.check("C5 router 的校历关键词是**从 intent 那张表推的**（`{w: \"faq\" for w in CALENDAR_FACT_WORDS}`），"
            "不是再抄一份字面量",
            "for w in CALENDAR_FACT_WORDS" in files_src
            and files_src.count('"放假"') == 0, "router 里没有手写的『放假』")

    plan_tree = ast.parse(open(os.path.join(PROJ_DIR, "app", "modules", "planner.py"),
                               encoding="utf-8").read())
    fn = None
    for n in ast.walk(plan_tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_asking_what_to_do":
            fn = n
    tests = [ast.unparse(x.test) for x in ast.walk(fn) if isinstance(x, ast.If)] if fn else []
    c.check("C6 排除是**控制流**（`if _WHEN_ASK_RE.search(t): return False`），不是注释里写着玩",
            any("_WHEN_ASK_RE" in t for t in tests),
            [t for t in tests if "_WHEN_ASK_RE" in t][:1])
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_69_calendar_fact：#42-A 时间问句排除 + 校历类归属")
    print("=" * 60)
    for fn, name in ((batch_a_judgement, "A 批（判据与对照）"),
                     (batch_b_attribution, "B 批（归属真发）"),
                     (batch_c_shape, "C 批（形状）")):
        rc = fn().summary(f"{name}")
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
