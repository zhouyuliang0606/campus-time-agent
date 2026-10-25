"""#65 的护栏：一句「我今天有什么课」怎么走到"答成待办"，又被哪几道闸拦回来。

批次分工（数字不写在标题里，这种数每加一批就得改一次，是本项目反复赔过的债）：

  A 形状：`_sem_done` 的判据追上 docstring（**办成了**才学，不是**返回了**就学）；
          七处调用点里**恰好两处**声明只读，且声明的就是「念清单」与「手上没卡」那两支；
          「问课」这把尺子只有 schedule 一份，main 不抄第二份词表；
          `_rule_intent` 那一格排在 `wants_list_todo` **之前**（顺序即判据）；
          新意图四处登记齐（常量 / KNOWN_INTENTS / INTENT_MODULE / INTENT_MEANING）。
  B 行为：真打。问课不再被列待办接走、**连问三遍样本库一字不长**（那个自学习闭环钉死在这里）；
          真在看清单的那句照旧答对、但**不再学**；找卡·手上确实有卡那一支**仍要学**（防收过头）；
          「明天不上课了，我要回家」不被"问课"抢走；清空课表照旧。
  C 数据：真实库里那两条「问课记成 list_todo」的化石已清掉，
          而**形似**的两条好样本一条没少；再加一条整扫闸（比黑名单耐用）。
  D 配对：新增那一格不破坏「`_rule_intent` 的返回值都在白名单里」这条不变式；
          这批测试自己不污染真实样本库。

⚠️ 「每栏几只手」在 `test_82` 钉、只读工具粒度在 `test_84` 钉、轨迹落盘与只读接口在 `test_85` 钉
   —— 本文件一条都不重复钉（同一件事两处各钉就是两个会互相打脸的判据）。
"""
import ast
import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (PROJ_DIR, Checker, data_path, make_client, sandbox,  # noqa: E402
                      seed_todos, title, write_timetable)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
SCHED = os.path.join(PROJ_DIR, "app", "modules", "schedule.py")
INTENT_PY = os.path.join(PROJ_DIR, "app", "agent", "intent.py")
SAMPLES = os.path.join(PROJ_DIR, "app", "data", "intent_samples.json")

LIST_MARK = "📋 列出待办（系统读取）"      # 列待办那一支的轨迹标记（认调用点用，不靠行号）
EMPTY_CARD_MARK = "暂存为空，如实说"      # 找卡但手上没卡那一支
COURSE_ASK = "我今天有什么课"
COURSES = [
    {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
    {"day": 1, "start": "10:05", "end": "11:45", "course": "大学英语", "location": "外语楼204"},
    {"day": 1, "start": "14:00", "end": "15:40", "course": "数据结构", "location": "机房B"},
    {"day": 2, "start": "08:00", "end": "09:40", "course": "线性代数", "location": "教二108"},
]
TODOS = [{"title": "做题", "date": "2026-10-27", "start": "14:00", "end": "16:00"},
         {"title": "约会", "date": "2026-10-30", "start": "15:40", "end": "18:40"}]
# 复现的前提：库里就得有这些样本（真实库里量到的原值 hits=3 / 1 / 1 / 1）
SEED_LIB = [
    {"text": "今天有什么课", "intent": "list_todo", "module": "planner", "hits": 3},
    {"text": "今天有课吗", "intent": "list_todo", "module": "planner", "hits": 1},
    {"text": "今天有什么代办", "intent": "list_todo", "module": "planner", "hits": 1},
    {"text": "我有什么代办", "intent": "list_todo", "module": "planner", "hits": 1},
    {"text": "我的卡呢", "intent": "find_card", "module": "planner", "hits": 1},
]


def read(path):
    return io.open(path, encoding="utf-8").read()


def seg_of(path, node):
    return ast.get_source_segment(read(path).replace("\r\n", "\n"), node) or ""


def func_of(path, name):
    for n in ast.walk(ast.parse(read(path).replace("\r\n", "\n"))):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def calls_of(path, fname):
    src = read(path).replace("\r\n", "\n")
    return [n for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == fname]


def write_lib(rows):
    io.open(data_path("intent_samples.json"), "w", encoding="utf-8", newline="\n").write(
        json.dumps(rows, ensure_ascii=False))


def lib():
    from app.agent import intent as it
    return it.load_samples()


def snapshot():
    """样本库当前的可比快照：(text, intent, hits) 全排序。条数/强化都逃不掉。"""
    return sorted((str(r.get("text")), str(r.get("intent")), r.get("hits")) for r in lib())


def ask(cl, msg, sid, agent=None):
    body = {"message": msg, "session_id": sid, "module": "planner"}
    if agent is not None:
        body["agent_mode"] = agent
    r = cl.post("/api/chat", json=body)
    js = r.json() if isinstance(r.json(), dict) else {}
    tr = [t for t in (js.get("trace") or []) if isinstance(t, dict)]
    return {"answer": str(js.get("answer") or ""),
            "options": js.get("options") or [],
            "tools": [str(t.get("action")) for t in tr if t.get("action")],
            "phases": [str(t.get("phase") or "") for t in tr]}


def listed_todo(answer):
    """这份答案是不是「把待办清单念了一遍」（本单要挡的那个症状）。"""
    return "目前的待办" in answer or "待办（共" in answer or "没有待办" in answer


def _todos():
    """当前沙箱里的待办（用来钉"问课被挡下之后没顺手写库"）。"""
    from app.store import list_todos
    return list_todos()


def batch_a_shape():
    c = Checker()
    title("A. 形状：判据追上 docstring、只读那两支声明了、词表只有一份")
    fn = func_of(MAIN, "_sem_done")
    src = seg_of(MAIN, fn) if fn else ""
    argnames = [a.arg for a in fn.args.args] if fn else []
    c.check("A1 `_sem_done` 多了一个 readonly 形参，且**默认 False**（没声明的调用点行为与改前一致，"
            "新增分支忘了声明也只是照旧学，不会静默改语义）",
            "readonly" in argnames and argnames[-1] == "readonly"
            and "Constant(value=False)" in ast.dump(fn.args.defaults[-1]) if argnames else False,
            str(argnames))
    c.check("A2 实现判据里带着「且不是只读」这一半（不是光看 resp 非空——那正是改前的写法）",
            "and not readonly" in src and "resp is not None" in src, "")
    calls = calls_of(MAIN, "_sem_done")
    with_ro = [n for n in calls if any(k.arg == "readonly" for k in n.keywords)]
    c.check("A3 调用点七处、声明只读的**恰好两处**（数量也钉：以后加第八处或多标一处，都得回这张表登记）",
            len(calls) == 7 and len(with_ro) == 2, f"共 {len(calls)} 处，只读 {len(with_ro)} 处")
    marks = " ".join(seg_of(MAIN, n) for n in with_ro)
    c.check("A4 那两处正是病根与它的同伴：列待办（念一遍清单）+ 找卡·手上没卡（什么都没办）",
            LIST_MARK in marks and EMPTY_CARD_MARK in marks, marks[:70])
    others = [n for n in calls if not any(k.arg == "readonly" for k in n.keywords)]
    oth = " ".join(seg_of(MAIN, n) for n in others)
    c.check("A5 其余五处一个没动 —— 尤其**改期**那处传进来的就是提案对象本身（没有 options 字段），"
            "它必须继续学；按「有没有 options」猜形状会把它的学习一起停掉",
            all(LIST_MARK not in seg_of(MAIN, n) and EMPTY_CARD_MARK not in seg_of(MAIN, n) for n in others)
            and "_sem_done(_rt_prop)" in oth, oth[:70])
    doc = ast.get_docstring(fn) or ""
    c.check("A6 docstring 写的是「办成了」并点名「不是这条分支返回了」（实现与说明同一份，别再留第三种说法）",
            "返回了" in doc and "办成" in doc, doc[:40].replace("\n", " "))
    c.check("A7 声明成只读却挂着卡 = 调用方判错了，要**当场说出来**（自证不许静默）",
            "[WARN]" in src and "options" in src and "awaiting_choice" in src, "")
    sc = read(SCHED)
    m = read(MAIN)
    c.check("A8 「问课」这把尺子只有一份定义：schedule 一处 def，main 里没有第二份实现（只 import）",
            sc.count("def looks_like_course_ask") == 1 and m.count("def looks_like_course_ask") == 0
            and "looks_like_course_ask" in m, "")
    c.check("A9 三张字表都只在 schedule 出现，main 一处都不抄（同一件事两个写法必然漂）",
            all(sc.count(w) >= 1 and m.count(w) == 0
                for w in ("_COURSE_WORDS", "_COURSE_ASK_EXCLUDE", "_COURSE_ASK_ASKS")), "")
    rfs = seg_of(MAIN, func_of(MAIN, "_rule_intent")) or ""
    # ⚠️ 不能拿 `rfs.index("wants_list_todo")` 比顺序：我自己在这一格里写的注释先提了
    #   `wants_list_todo`，字符串比的是注释的位置（第一次跑就是这么红的）。要比**两条 if 的行号**。
    def first_if_line(pred):
        for i, line in enumerate(rfs.split("\n")):
            s = line.strip()
            if s.startswith("if ") and pred(s):
                return i
        return -1
    a_line = first_if_line(lambda s: "looks_like_course_ask" in s)
    b_line = first_if_line(lambda s: "wants_list_todo" in s)
    c.check("A10 `_rule_intent` 里问课那一格排在 wants_list_todo **之前**（排在后面就永远轮不到它）",
            a_line != -1 and b_line != -1 and a_line < b_line, f"问课在第 {a_line} 行、列待办在第 {b_line} 行")
    it = read(INTENT_PY)
    c.check("A11 新意图四处登记齐：常量 / KNOWN_INTENTS / INTENT_MODULE / INTENT_MEANING"
            "（少 KNOWN_INTENTS 那处 = `remember` 与 `match_sample` 静默丢掉它，等于没加）",
            "INTENT_LIST_COURSE = " in it
            and all(it.count("INTENT_LIST_COURSE") >= 4 for _ in [0])
            and "INTENT_LIST_COURSE, INTENT_CHAT" in it.replace("\n", " ").replace("  ", " "),
            "出现 %d 次" % it.count("INTENT_LIST_COURSE"))
    c.check("A12 提示词里**不枚举**这个新意图（语义层不许发明意图；这一格只给规则层当预判用）",
            "list_course" not in it[it.index("def _intent_system"):], "")
    return c


def batch_b_live():
    c = Checker()
    title("B. 行为：真打 —— 问课不再答成待办、闭环不再长样本、该学的还学")
    with sandbox():
        write_timetable(COURSES)
        seed_todos(TODOS)
        write_lib(SEED_LIB)
        cl = make_client()
        base = snapshot()

        a1 = ask(cl, COURSE_ASK, "t65-course")
        c.check("B1 「我今天有什么课」不再被列待办接走（答案不是念了一遍待办清单）",
                not listed_todo(a1["answer"]) and LIST_MARK not in " ".join(a1["phases"]),
                a1["answer"][:60].replace("\n", " "))
        ask(cl, COURSE_ASK, "t65-course-2")
        ask(cl, COURSE_ASK, "t65-course-3")
        after = snapshot()
        c.check("B2 同一句**连问三遍**之后样本库一字不长 —— 这就是那个自学习闭环"
                "（改前每问一遍多一条/加一次 hits，下次 Dice 直接顶满，永久错下去）",
                after == base,
                f"{len(base)} → {len(after)}；差异：{(set(after) ^ set(base)) or '无'}")
        c.check("B2-bis 挡下之后库里一条不多不少（不接走 ≠ 顺手写库）",
                [t.get("title") for t in _todos()] == [t.get("title") for t in TODOS],
                str([t.get("title") for t in _todos()]))
        # ⚠️ 这里**不**断言「也不许出卡」。实测：没有 key 的沙箱里，这句会掉到无 key 兜底
        #   `mock_planner`，它会吐 3 张候选时段卡（改前改后都是它的行为，与本单无关）；
        #   带 key 的副本服务上同一句答的是「今天（周一）有 3 门课…」（实测记在快照 §真机）。
        #   把"没卡"钉进来只会钉住 mock 的脾气，不钉住本单的判据。

        s2 = ask(cl, "今天有什么代办", "t65-todo")
        c.check("B3 真在看清单的那句照旧答对（列出 2 条待办）—— 修的是方向，不是把列表功能打死",
                listed_todo(s2["answer"]) and "做题" in s2["answer"] and "约会" in s2["answer"],
                s2["answer"][:60].replace("\n", " "))
        c.check("B4 但它这一轮**不学样本**了（只读就是只读）；与 B3 成对，缺一半就是收得过宽或没收住",
                snapshot() == base, str(sorted(set(snapshot()) ^ set(base)))[:80])

        ask(cl, "加个吃火锅，周六晚上七点", "t65-hold")     # 先挂一张卡，让找卡落在「有卡」那一支
        f0 = snapshot()
        f1 = ask(cl, "我的卡呢", "t65-hold")
        f2 = snapshot()
        c.check("B5 找卡·手上确实有卡 → 原样重挂，而且**照旧学**（证明只读那两处没把整条学习路径停掉）",
                len(f1["options"]) > 0 and ("find_card", 1) in [(i, 1) for (_t, i, h) in f0]
                and f2 != f0,
                f"卡 {len(f1['options'])} 张；库 {len(f0)} → {len(f2)}；差异 "
                f"{sorted(set(f2) ^ set(f0))[:2]}")
    return c


def batch_b_edges():
    c = Checker()
    title("B-bis. 边界：问课这把尺子宁窄 —— 加待办与清空课表都不许被它抢走")
    with sandbox():
        write_timetable(COURSES)
        seed_todos(TODOS)
        write_lib(SEED_LIB)
        cl = make_client()

        e1 = ask(cl, "明天不上课了，我要回家", "t65-e1")
        c.check("B6 「明天不上课了，我要回家」仍走**加待办**出卡（课只是前提，句里没有疑问代词）"
                "—— 本单最容易踩反的那一句",
                len(e1["options"]) > 0 and not listed_todo(e1["answer"]),
                f"卡 {len(e1['options'])} 张｜{e1['answer'][:40]}")
        e2 = ask(cl, "帮我把课表清空", "t65-e2")
        c.check("B7 「帮我把课表清空」仍是清空提案（一张确认条，等学生点头），没被「问课」那格劫走",
                len(e2["options"]) > 0 and "清空" in (e2["answer"] + json.dumps(e2["options"], ensure_ascii=False)),
                f"卡 {len(e2['options'])} 张")
        e3 = ask(cl, "看看我的待办", "t65-e3")
        c.check("B8 「看看我的待办」这种规则层就认得的，照旧由系统念清单（不因新增预判格而改道）",
                listed_todo(e3["answer"]) and LIST_MARK in " ".join(e3["phases"]),
                e3["answer"][:50].replace("\n", " "))
        e4 = ask(cl, "明天有课吗", "t65-e4")
        c.check("B9 「明天有课吗」也不答成待办（与 B1 成对：一个问今天、一个问明天）",
                not listed_todo(e4["answer"]), e4["answer"][:50].replace("\n", " "))
        e5 = ask(cl, COURSE_ASK, "t65-agent", agent=True)
        c.check("B10 Agent 模式同一句仍然不答待办（#61-C 那个短路点在本单之后依然有效）",
                not listed_todo(e5["answer"]), e5["answer"][:50].replace("\n", " "))

        from app.modules.schedule import looks_like_course_ask as f
        yes = ("我今天有什么课", "明天有课吗", "这周几节课", "今天哪节课在机房", "后天没课吗")
        no = ("明天不上课了，我要回家", "帮我把课表清空", "把高数课删了", "加一节体育课",
              "周一的课改成晚上", "帮我记一下要交实验报告", "做题", "课" + "有什么课" + "课" * 40)
        c.check("B11 正面族逐个为真（课程词 + 疑问形状）", all(f(s) for s in yes),
                [s for s in yes if not f(s)])
        c.check("B12 反面族逐个为假：没有疑问形状的、超 40 字的、只出现「课」字的"
                "（带办事动作的那半张表不靠这里钉，见 B14）",
                not any(f(s) for s in no), [s for s in no if f(s)])
        c.check("B13 空串 / None 不炸（规则层每句话都会调它一次）",
                f("") is False and f(None) is False, "")
        # B14 单独钉**半张表**。Z5 变异（拆掉「办事动作」排除表）实测是哑弹：
        #   上面那族反例全都被"疑问形状"那一半先挡掉了（「帮我把课表清空」没有疑问词、
        #   「明天不上课了我要回家」没有"吗/什么"），排除表一个字都没用到。
        #   下面这两句是课程词 + 疑问词都齐、**只有**排除表能挡的（实测：什么课+吗+删 / 课表+吗+清空）。
        only_excl = ("有什么课是可以删掉的吗", "课表能清空吗")
        c.check("B14 只靠「办事动作」那半张表挡下的句子（课程词与疑问形状都齐）——"
                "拆掉排除表的变异必须红，否则那半张表是装饰",
                not any(f(s) for s in only_excl), [s for s in only_excl if f(s)])
    return c


def batch_c_data():
    c = Checker()
    title("C. 数据：清掉那两条化石，形似的两条一条没少")
    # ⚠️ `app/data/intent_samples.json` 是 **.gitignored 的运行时数据**（种子那几条内置在代码里），
    #   所以"本机库里应该有/不应该有哪几条"**不能钉成硬断言** —— 换台机器（严格克隆）这本文件根本不存在，
    #   钉了就是一台机器绿、另一台红（#56 那族"耦合本机数据"的病）。
    #   做法：文件不存在时，"没有化石"这条天然成立、"好样本还在"这条**不去要求**（它本来就还没被学到）；
    #   真正不分机器都成立的判据放在 C5（整扫）与 B11~B13（词表正反例）里。
    if not os.path.exists(SAMPLES):
        c.check("C0 这台机器上还没有运行时样本库（.gitignored）—— 那么本批只剩「不许有化石」这条天然成立",
                True, "文件不存在")
        from app.modules.schedule import looks_like_course_ask
        c.check("C5 整扫：库里没有任何一条「看着像在问课」却被标成 list_todo/add_todo 的样本"
                "（这条判据本身就是闸门，比逐条黑名单耐用；新化石一进来就红）",
                not [r for r in [] if looks_like_course_ask(str(r.get("text") or ""))], "空库")
        return c
    rows = json.loads(read(SAMPLES))
    rows = rows if isinstance(rows, list) else (rows.get("samples") or [])
    by_text = {}
    for r in rows:
        by_text.setdefault(str(r.get("text")), []).append(str(r.get("intent")))
    c.check("C1 「今天有什么课」不再记成 list_todo（这就是那次事故的固化物）",
            "list_todo" not in (by_text.get("今天有什么课") or []), str(by_text.get("今天有什么课")))
    c.check("C2 「今天有课吗」同上", "list_todo" not in (by_text.get("今天有课吗") or []),
            str(by_text.get("今天有课吗")))
    c.check("C3 「明天不上课了，明天回家」→ add_todo 是对的（回家才是待办），没被连坐删"
            "（本机还没学到这条时也算通过：要求不存在的东西不是这条断言的目的）",
            "add_todo" in (by_text.get("明天不上课了，明天回家") or [])
            or "明天不上课了，明天回家" not in by_text, str(by_text.get("明天不上课了，明天回家")))
    c.check("C4 「帮我加节周一上午的课」同款也没被圈走：单据 §C 那条判据按字面会把它一起删掉（含「课」又标 add_todo），"
            "所以清理是**点名删**、不是跑那条判据 —— 少删比错删好",
            "add_todo" in (by_text.get("帮我加节周一上午的课") or [])
            or "帮我加节周一上午的课" not in by_text, str(by_text.get("帮我加节周一上午的课")))
    from app.modules.schedule import looks_like_course_ask
    still = [r for r in rows
             if looks_like_course_ask(str(r.get("text") or ""))
             and r.get("intent") in ("list_todo", "add_todo")]
    c.check("C5 整扫：库里没有任何一条「看着像在问课」却被标成 list_todo/add_todo 的样本"
            "（这条判据本身就是防同类再进来的闸，比逐条黑名单耐用）",
            not still, str(still)[:90])
    c.check("C6 清完还剩多少条要报得出数（改前 68、清两条 = 66；只报「清好了」不给数=没法核对）",
            len(rows) >= 60, f"{len(rows)} 条")
    return c


def batch_d_pair():
    c = Checker()
    title("D. 配对与自污：新增那一格不破坏不变式，这批测试不写真实库")
    md5_before = (hashlib.md5(read(SAMPLES).encode("utf-8")).hexdigest()
                  if os.path.exists(SAMPLES) else "NO-SUCH-FILE")
    with sandbox():
        write_timetable(COURSES)
        seed_todos(TODOS)
        write_lib(SEED_LIB)
        cl = make_client()
        for s in (COURSE_ASK, "今天有什么代办", "明天有课吗", "看看我的待办", "我的卡呢"):
            ask(cl, s, "t65-d-" + s[:2])
        c.check("D1 这一批真打之后**真实**样本库的 md5 一字不变（沙箱不许脏演示台账，与用量账同一条规矩）",
                (hashlib.md5(read(SAMPLES).encode("utf-8")).hexdigest()
                 if os.path.exists(SAMPLES) else "NO-SUCH-FILE") == md5_before, "")
        c.check("D2 沙箱里那条脏样本也没被这批调用加强（B2 不是假绿：同一支路真跑过，只是不学了）",
                not any(r.get("text") == COURSE_ASK for r in lib()),
                str([r for r in lib() if r.get("text") == COURSE_ASK])[:70])
    from app.agent import intent as it
    rfs = seg_of(MAIN, func_of(MAIN, "_rule_intent")) or ""
    named = {tok for tok in rfs.replace("(", " ").replace(":", " ").split()
             if tok.startswith("INTENT_") and len(tok) < 30}
    # KNOWN_INTENTS 装的是**取值**（"list_todo"），不是常量名 —— 拿名字去比会全判不通过（第一次跑就在这红的）
    values = set()
    missing_name = []
    for nm in sorted(named):
        if hasattr(it, nm):
            values.add(getattr(it, nm))
        else:
            missing_name.append(nm)
    c.check("D3 `_rule_intent` 吐出的每一个意图都在 KNOWN_INTENTS 里（新增一格最容易坏的就是这条不变式："
            "不在白名单的意图会被 remember/match_sample 静默丢掉）",
            bool(values) and values.issubset(set(it.KNOWN_INTENTS)) and not missing_name,
            f"取值 {sorted(values)}；取不到的名字 {missing_name}")
    c.check("D4 新意图在 INTENT_MEANING 里有人话（轨迹与 /classify 都从这张表取，缺了就给学生一串原始码）",
            it.INTENT_LIST_COURSE in it.INTENT_MEANING
            and bool(str(it.INTENT_MEANING.get(it.INTENT_LIST_COURSE, "")).strip()),
            str(it.INTENT_MEANING.get(it.INTENT_LIST_COURSE)))
    m = read(MAIN)
    c.check("D5 它不落在任何**写库**分支的判据里（本单只加「别接走」，没加「由我接管」）："
            "main 里这个符号只出现在导入 + 规则预判两处 + 列待办那两半",
            m.count("INTENT_LIST_COURSE") <= 3,
            f"main 里出现 {m.count('INTENT_LIST_COURSE')} 次")
    return c


def main():
    print("test_86_course_ask_not_todo：#65 问课被答成待办 + 模糊匹配宽判据 + 自学习固化")
    print("=" * 60)
    code = 0
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（行为）", batch_b_live),
                      ("B-bis 批（边界）", batch_b_edges),
                      ("C 批（数据）", batch_c_data),
                      ("D 批（配对与自污）", batch_d_pair)):
        try:
            rc = fn().summary(label)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
