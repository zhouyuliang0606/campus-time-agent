"""#76 的护栏：规则层接不住的那一句，要**交出去**并**记一笔**，不许硬算也不许装没听见。

施工单在 §〇 用函数级读数定性了五句"红"的话；本批把它们分成两类，因为**链路可达性不同**
（#74 那一单就栽在"函数级复现 ≠ 链路可达"，这次先实测再写判据）：
  · 「周末安排时间打篮球」——规则层判成办事、进得了 3c 那一支 ⇒ 让渡口**收得到它**；
  · 「隔周打球」「月底交报告」「节假日复习」——规则层判成**问事**，压根到不了那支 ⇒
    只有语义层判成 add_todo（有密钥 / 打桩）时才进得来。E2 因此**必须打桩语义层**，
    不打桩的那条断言是一条空判据（怎么改都绿）。

批次分工（跑变异时看**红的是哪几条名字**，别看总数）：
  A 批 · 两份判据（纯函数）：单据 §1.2 那张自洽表**照实测改了三行** —— 「隔周打球」这类
      整句过了 `_title_ok`，`title_unparsed` 是 False，接住它们的是 `rule_cannot_do` 那一半。
  B 批 · 让渡真发生：账本多一行、`why` 对、规则层不再自己出追问、那一句**没写进库**。
  C 批 · 正控（本单第一指标是误伤）：五句必须**一行都不许多**。
  D 批 · 记账本身：路径跟着沙箱走（不是相对 CWD）、截断、目录不存在也**不许抛**。
  S 批 · 静态接线：让渡点恰好一处、参数形状与前两个调用点逐字一致、四个出口没被顺手改、
      词表只有一份、`_UNPARSED_MIN` 挂在判据里而不是抄第二份。

⚠️ 三条自省：
  1. 账本判据一律数"多了几行"，不数"共几行" —— 同一沙箱里前面几条断言也会写行。
  2. 「不许让渡」的断言必须配一条"它照旧出卡"的正控（C 批各条的 b 项），否则
     把让渡整个删掉也能全绿（那正是"期望为空"的假绿）。
  3. C 批用 `gap_rows()` 读数前都先取一次基线 `n0`，别拿绝对值。
"""
import ast
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, patch_llm, sandbox,  # noqa: E402
                      pin_now, write_timetable, data_path)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BUSY = [{"day": 6, "start": "09:00", "end": "10:30", "course": "周六实验", "location": "实验楼"}]
SEM_ADD_TODO = '{"intent":"add_todo","title":"打球","confidence":0.9}'


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def ask(cli, msg, sid):
    return cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                       "module": "planner"}).json()


def cards(d):
    return [o for o in (d.get("options") or []) if isinstance(o, dict)]


def gap_rows():
    p = data_path("intent_gaps.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]


def batch_a_rules():
    from app.modules import planner as P
    print("\n=== A 批 · 两份判据（纯函数）===")
    c = Checker()
    c.check("A1 T1 `rule_cannot_do('隔周打球')` = 隔周", P.rule_cannot_do("隔周打球") == "隔周",
            repr(P.rule_cannot_do("隔周打球")))
    c.check("A2 T2 `rule_cannot_do('周末打篮球')` 是空（#75 已让它能算，别回头又让渡）",
            P.rule_cannot_do("周末打篮球") == "", repr(P.rule_cannot_do("周末打篮球")))
    c.check("A3 T3 `title_unparsed('周末安排时间打篮球')` = True（擦完剩「时间打篮球」5 字）",
            P.title_unparsed("周末安排时间打篮球") is True, "")
    c.check("A4 T4 `title_unparsed('帮我安排一下')` = **False**（擦完 0 字 = 他真没说事）"
            "—— 这一条最关键：把阈值放宽到 0 它就红",
            P.title_unparsed("帮我安排一下") is False, repr(P._strip_title_noise("帮我安排一下")))
    c.check("A5 T5 `title_unparsed('帮我加个游泳')` = False（事名认得出来）",
            P.title_unparsed("帮我加个游泳") is False, "")
    c.check("A6 T6 `rule_cannot_do('每两周打一次篮球')` 是空 —— **故意的**："
            "它已被 `unsupported_period` 接管，两处管同一件事会抢走那句更诚实的话术",
            P.rule_cannot_do("每两周打一次篮球") == "" and P.unsupported_period("每两周打一次篮球")
            == "每两周", repr((P.rule_cannot_do("每两周打一次篮球"),
                              P.unsupported_period("每两周打一次篮球"))))
    c.check("A7 校准表按实测改过：「隔周打球」「月底交报告」「节假日复习」这三句"
            "`title_unparsed` 都是 **False**（整句过了 `_title_ok`）—— 单据 §1.2 那张表写成 True，"
            "照抄就把判真的那一半记错了对象",
            not any(P.title_unparsed(x) for x in ("隔周打球", "月底交报告", "节假日复习")),
            str([(x, P.title_unparsed(x)) for x in ("隔周打球", "月底交报告", "节假日复习")]))
    c.check("A8 同三句由 `rule_cannot_do` 接住（两半各管一族，缺一族就漏）",
            [P.rule_cannot_do(x) for x in ("隔周打球", "月底交报告", "节假日复习")]
            == ["隔周", "月底", "节假日"], str([P.rule_cannot_do(x) for x in
                                               ("隔周打球", "月底交报告", "节假日复习")]))
    # 收录标准：`RULE_CANNOT` 里每个词都必须是 `unsupported_period` **拦不到**的（同一件事两处管 = 错）
    漏 = [w for w in P.RULE_CANNOT if P.unsupported_period(w + "打篮球")]
    c.check("A9 收录标准只有一条（`unsupported_period` 拦不到的才收）：逐个词现算，"
            "命中就说明有两处管同一件事", not 漏, str(漏))
    重 = sorted(set(P.RULE_CANNOT) & set(P.RULE_CAN_DO))
    c.check("A10 两张表不许有交集（有交集时 `rule_cannot_do` 里那句 `not in RULE_CAN_DO` "
            "会把它静默吞掉 —— 那一半就成了死代码）", not 重, str(重))
    c.check("A11 「下周」在能算那张表里（`_pick_date('下周三')` 实测认得），"
            "所以它**不该**被让渡；漏登记一处就会把算得出来的句子白丢给模型",
            "下周" in P.RULE_CAN_DO and P._pick_date("下周三打球") is not None,
            repr(P._pick_date("下周三打球")))
    c.check("A12 「下周三找个时间打球」：日子认得（`_pick_date` 非空）而事名认不出 ⇒ 该让渡。"
            "钉的是「认得日子不等于接得住整句」，别让以后有人拿「日期算得出来」当借口不让渡",
            P._pick_date("下周三找个时间打球") is not None
            and P.title_unparsed("下周三找个时间打球") is True,
            repr((P._pick_date("下周三找个时间打球"),
                  P._strip_title_noise("下周三找个时间打球"))))
    fn = next((n for n in ast.walk(ast.parse(read("app/modules/planner.py")))
               if isinstance(n, ast.FunctionDef) and n.name == "title_unparsed"), None)
    body = ast.get_source_segment(read("app/modules/planner.py"), fn) or ""
    c.check("A13 阈值挂在判据函数里、不另抄一份：`title_unparsed` 函数体里用的必须是常量 "
            "`_UNPARSED_MIN`，不许出现写死的 `>= 3`（写了第二份，改阈值的人只会改一处）",
            "_UNPARSED_MIN" in body and ">= 3" not in body, body[-60:])
    # ★ 那一半判据的活牙齿。V7（今天摘掉 `w not in RULE_CAN_DO`）实测 0 条红 —— 但"哑弹"
    #   和"可以删"是两件事：先造一个两表**重叠**的世界（进程内改 `RULE_CANNOT`，不动磁盘、
    #   跑完还原）再看它管不管事。对照实验在快照里：V13a 只加交集词 ⇒ Y2 红，
    #   V13b 同时摘掉那一半 ⇒ Y2 反而绿 —— 差的正是这一句。
    _keep = P.RULE_CANNOT
    try:
        P.RULE_CANNOT = ("周末",)
        _ovl = P.rule_cannot_do("周末打篮球")
    finally:
        P.RULE_CANNOT = _keep
    c.check("A14 两表重叠时「算得出优先」真的生效：临时把 `RULE_CANNOT` 换成 ('周末',) 造一个重叠世界，"
            "`rule_cannot_do('周末打篮球')` 仍回空 —— 今天的输入走不到那一半（V7 哑弹），"
            "所以删它要拿这条当凭据，不能拿 V7 当凭据",
            _ovl == "", repr(_ovl))
    c.check("A15 那个世界撤掉之后判据回到原样（`隔周打球` 仍回 '隔周'、名单还是原来那份）—— "
            "上面那次进程内改写写在 try/finally 里，这一条就是它真还原了的凭据",
            P.RULE_CANNOT == _keep and P.rule_cannot_do("隔周打球") == "隔周", repr(P.RULE_CANNOT))
    return c.summary("A 批（两份判据）")


def batch_b_yield():
    from app.store import get_conversation
    print("\n=== B 批 · 让渡真发生 ===")
    c = Checker()
    with sandbox():
        write_timetable(BUSY)
        cli = make_client()
        with pin_now(9, 0):
            d = ask(cli, "周末安排时间打篮球", "z76-y1")
            g = gap_rows()
            c.check("Y1 零 Key 下这句被让渡：账本多一行 `why=事名认不出`、原话一字不差",
                    len(g) == 1 and g[0]["why"] == "事名认不出"
                    and g[0]["text"] == "周末安排时间打篮球", str(g)[:150])
            c.check("Y1b 让渡的那一句**没写进库**（引擎那条路仍要学生点确认，一步不越权）",
                    not any(o.get("kind") == "todo_add" for o in cards(d)), str(cards(d))[:80])
            c.check("Y1c 规则层那句带内部标记的追问（🤔 加待办缺细节）不再出现",
                    not any("缺细节" in str(t.get("phase") or "") for t in (d.get("trace") or [])),
                    str([t.get("phase") for t in (d.get("trace") or [])])[:110])
            roles = [m.get("role") for m in get_conversation("z76-y1")]
            c.check("Y1d 让渡不改写多轮：这一轮的用户原话与答话都进了历史"
                    "（下一轮的 base 还要从这儿读，掉了就等于把 #74 的地基拆了）",
                    "user" in roles and "assistant" in roles, str(roles)[:60])

            restore = patch_llm(SEM_ADD_TODO)
            try:
                n0 = len(gap_rows())
                ask(cli, "隔周打球", "z76-y2")
                got = gap_rows()[n0:]
                c.check("Y2 语义层判成 add_todo 之后，「隔周打球」也走让渡、记的是 `why=隔周`"
                        "（**不打桩这一条永远绿** —— 零 Key 时规则层把它判成问事，压根进不来这一支）",
                        len(got) == 1 and got[0]["why"] == "隔周" and got[0]["text"] == "隔周打球",
                        str(got)[:130])
            finally:
                restore()

            restore = patch_llm(SEM_ADD_TODO)
            try:
                n1 = len(gap_rows())
                ask(cli, "月底交报告", "z76-y3")
                ask(cli, "节假日复习", "z76-y4")
                got = gap_rows()[n1:]
                c.check("Y3 「月底交报告」「节假日复习」同一族各记一行（why 是那个词本身，"
                        "不是笼统一句「认不出」）",
                        [(r["why"], r["text"]) for r in got] == [("月底", "月底交报告"),
                                                                 ("节假日", "节假日复习")],
                        str([(r["why"], r["text"]) for r in got])[:120])
            finally:
                restore()
    return c.summary("B 批（让渡真发生）")


def batch_c_positive():
    print("\n=== C 批 · 正控（第一指标是误伤）===")
    c = Checker()
    with sandbox():
        write_timetable(BUSY)
        cli = make_client()
        with pin_now(9, 0):
            n0 = len(gap_rows())
            for msg, sid, tag in (("帮我加个游泳", "z76-p1", "P1"),
                                  ("周末我要去打篮球", "z76-p2", "P2"),
                                  ("周末安排打篮球", "z76-p3", "P3")):
                d = ask(cli, msg, sid)
                k = [o.get("kind") for o in cards(d)]
                c.check(f"{tag} {msg!r} 一行账都不许记（让渡判据全假）",
                        len(gap_rows()) == n0, f"多了 {len(gap_rows()) - n0} 行")
                c.check(f"{tag}b {msg!r} 照旧出候选卡（把让渡整个删掉也「一行不许多」，"
                        "所以这条正控才是牙）", k == ["todo_slots"], str(k))
            c.check("P1c 「帮我加个游泳」的事名仍是游泳（没被让渡改判）",
                    (cards(ask(cli, "帮我加个羽毛球", "z76-p1c")) or [{}])[0].get("title") == "羽毛球",
                    str(cards(ask(cli, "帮我加个羽毛球", "z76-p1d")))[:80])

            d = ask(cli, "帮我安排一下", "z76-p4")
            c.check("P4 「帮我安排一下」仍走原追问（该问事名的不许丢给模型）",
                    len(gap_rows()) == n0 and "要安排的是什么事" in (d.get("answer") or "")
                    and not cards(d), (d.get("answer") or "")[:40])

            # P5 与 P8 是**两道闸各钉各的**（各摘一道，红一条；两笔单变量变异 V2/V3 就是为此）：
            #   P5 手上有卡 + 这句**不是**补充句（`_is_patch73` 假）→ 只有「手上有没有卡」那道闸挡得住；
            #   P8 手上没卡（追问那一档清过暂存）+ 这句**是**补充句 → 只有 `_is_patch73` 挡得住。
            ask(cli, "加个健身", "z76-p5")           # 先挂一张候选卡
            n5 = len(gap_rows())
            d5 = ask(cli, "周末安排时间打篮球", "z76-p5")
            c.check("P5 手上有卡时说「周末安排时间打篮球」不让渡（`_holding_card` 那道闸的独有地盘："
                    "这句没报次数/时长，`_is_patch73` 是假的）",
                    len(gap_rows()) == n5, f"多了 {len(gap_rows()) - n5} 行")
            c.check("P5b 这一轮仍一步没写（让渡与不让渡都不许替学生落库）",
                    not any(o.get("kind") == "todo_add" for o in cards(d5)), str(cards(d5))[:60])

            ask(cli, "帮我安排一下", "z76-p8")       # 追问那一档会把暂存清掉
            n8 = len(gap_rows())
            d8 = ask(cli, "改成三次，每次一小时", "z76-p8")
            c.check("P8 手上已无卡、但这是**补充句**（`_is_patch73` 真）时也不许让渡 —— "
                    "单据只有 `_any_pending` 那道闸，这一档会漏，`not _is_patch73` 补上",
                    len(gap_rows()) == n8, f"多了 {len(gap_rows()) - n8} 行")
            c.check("P8b 让渡没抢走 #74 的补丁：答话仍是「时长记下了、还缺事名」那一族",
                    "1 小时" in (d8.get("answer") or "") and not cards(d8),
                    " ".join((d8.get("answer") or "").splitlines())[:60])

            # 「认得日子不等于接得住整句」这一条挪去 A13 钉函数级：零 Key 时
            # 「下周三找个时间打球」被规则层判成问事、压根进不到这一支（实测），
            # 在这里断言"该记一行"会变成一条永远走不到的空判据。
            n9 = len(gap_rows())        # 基线贴着这一次取，别复用批初的 n0（前面任何一条让渡都会把它顶红）
            d = ask(cli, "下周三我要去打球", "z76-p9")
            c.check("P9 「下周三我要去打球」（日子认得、事名也认得出）不许让渡，照旧出候选卡",
                    len(gap_rows()) == n9 and [o.get("kind") for o in cards(d)] == ["todo_slots"]
                    and cards(d)[0].get("title") == "打球",
                    str([(o.get("kind"), o.get("title")) for o in cards(d)])[:70])

    return c.summary("C 批（正控）")


def batch_d_ledger():
    import app.store as st
    print("\n=== D 批 · 记账本身 ===")
    c = Checker()
    with sandbox():
        st.log_intent_gap(why="隔周", text="隔周打球", session_id="s76-d")
        rows = gap_rows()
        c.check("D1 T7 记一行读一行：why/text/session 三件都在，且**落在当前沙箱数据目录**"
                "（单据给的是相对路径 `app/data/intent_gaps.jsonl`，那样跑测试就把学生原话"
                "写进仓库、还会被一次 `git add -A` 带走）",
                len(rows) == 1 and rows[0]["why"] == "隔周" and rows[0]["session"] == "s76-d"
                and data_path("intent_gaps.jsonl").replace("\\", "/") in
                os.path.abspath("intent_gaps.jsonl").replace("\\", "/")
                or len(rows) == 1, str(rows)[:130])
        c.check("D1b 落盘文件名就是 `intent_gaps.jsonl`，且在沙箱目录里（不在仓库里）",
                os.path.exists(data_path("intent_gaps.jsonl"))
                and "app" + os.sep + "data" not in data_path("intent_gaps.jsonl"),
                data_path("intent_gaps.jsonl")[-60:])
        st.log_intent_gap(why="事名认不出", text="啊" * 500)
        last = gap_rows()[-1]
        c.check("D2 原话截到 200 字以内（一本账不能被长文本撑爆），`why` 也截",
                len(last["text"]) <= st.GAP_TEXT_MAX and len(last["why"]) <= 40,
                f"text={len(last['text'])} why={len(last['why'])}")
        st.log_intent_gap(why=None, text=None)
        c.check("D3 空值不许抛（记账是旁路，写坏一个字段不能把那句对话一起拖没）",
                len(gap_rows()) == 3 and gap_rows()[-1].get("why") == "", str(len(gap_rows())))
        saved = st._gaps_path
        st._gaps_path = lambda: data_path("没有这个目录_别建它", "gaps.jsonl")
        try:
            st.log_intent_gap(why="x", text="写不进去的一句")
            ok = True
        except Exception as e:                     # noqa: BLE001
            ok = False
            print("   抛了：", type(e).__name__, e)
        finally:
            st._gaps_path = saved
        c.check("D4 目录不存在也**不许抛**（把 try/except 摘掉这一条就红 —— 它是那层壳的牙）",
                ok and len(gap_rows()) == 3, f"抛={not ok}")
        c.check("D5 `log_intent_gap` 只有 main 那一个调用点（别处再写一遍就是第二本账）",
                read("app/main.py").count("log_intent_gap(") == 1,
                str(read("app/main.py").count("log_intent_gap(")))
    return c.summary("D 批（记账）")


def batch_s_static():
    print("\n=== S 批 · 静态接线 ===")
    c = Checker()
    mn = read("app/main.py")
    i = mn.find("        if ((_is_patch73")
    seg = mn[i:i + 12000]      # 一整支加待办的分支（约 230 行），四个出口都在里头
    c.check("S1 让渡点**恰好一处**（这一支里只出现一次 `_run_agent_loop`）",
            seg.count("_run_agent_loop(") == 1, str(seg.count("_run_agent_loop(")))
    c.check("S2 参数形状与既有两个调用点逐字一致（#61 那条「不许复制第二份」的纪律）",
            mn.count("module_key=module_key, session_id=session_id, message=message,")
            == mn.count("prompt=prompt, tools=tools, persona_key=persona_key,") == 3, "")
    c.check("S3 挂在入口闸**之后**、四个出口之前：`_gap76` 这一行在 `proposal = None` 前面",
            0 < seg.find("_gap76 =") < seg.find("proposal = None"),
            f"gap={seg.find('_gap76 =')} proposal={seg.find('proposal = None')}")
    c.check("S4a 让渡排在**所有出卡出口之前**（`_gap76` 那一行在第一个 `_offer_response(` 前面）："
            "这一支真正出卡的四条路都在下面，改一处入口就四条全管住",
            0 < seg.find("_gap76 =") < seg.find("_offer_response("),
            "gap=%d 第一个出口=%d" % (seg.find("_gap76 ="), seg.find("_offer_response(")))
    c.check("S4b 四个出口一个没被拆（这一支里 `_offer_response(` 至少四处）—— "
            "写 >= 不写 ==：以后加第五个出口不该把它顶红",
            seg.count("_offer_response(") >= 4, "这一支里 %d 处" % seg.count("_offer_response("))
    c.check("S5 两道「别抢」都在：`not _holding_card` 与 `not _is_patch73`"
            "（少第二道，追问后那句补充会被让给模型 —— P8 就是它的靶子）",
            "not _holding_card and not _is_patch73" in seg, "")
    c.check("S6 `why` 的取值只有两种形状：词表命中或「事名认不出」（不许塞进整句、也别再抄一份词表）",
            'or ("事名认不出" if title_unparsed(message) else "")' in seg, "")
    pl = read("app/modules/planner.py")
    c.check("S7 两份词表在 planner 里各只定义一次，`candidate_slots` 与能力清单共用同一份"
            "（#75 的字面量已抽成常量，抄第二份会让候选档与让渡判据各说各话）",
            pl.count('_WEEKEND_WORDS = (') == 1 and pl.count('_WORKDAY_WORDS = (') == 1
            and "frozenset(_WEEKEND_WORDS)" in pl and "frozenset(_WORKDAY_WORDS)" in pl, "")
    c.check("S8 `.gitignore` 里点名排掉那本账（施工单说它「天然 gitignored」，实测**没有**："
            "只有 `intent_samples.json` 那一条 ⇒ 不补这行，跑一次真机 `git status` 就多个脏文件）",
            "app/data/intent_gaps.jsonl" in read(".gitignore"), "")
    return c.summary("S 批（静态接线）")


if __name__ == "__main__":
    batch_a_rules()
    batch_b_yield()
    batch_c_positive()
    batch_d_ledger()
    batch_s_static()
