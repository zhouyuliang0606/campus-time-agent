"""两条 2026-10-11 真机复现的收口：**不支持的周期不许单独接单**、**事名不许串味**。

起因（都是用户原话，都在真机上跑出来的）：
  ① 「在下个月的每个周末三点开始大约三个小时」→ 系统建了一条**普通待办**（15:00-18:00 那一条），
     而学生要的是"以后每个周末"。规则层其实**从来不接**这一类（`wants_add_todo` 一律 False），
     接单的是**样本库/语义层** —— 它能绕过规则层单独判成 add_todo、出卡，
     还把这句**学进了样本库**（`_sem_done` 里那句 `remember`），于是同一个错会自己繁殖。
     所以判据要有一处**不分层**的收口：句子里出现「每月/每个周末/下个月…」这种
     待办模型表达不了的周期时，**哪一档说"这是加待办"都不出卡**，并如实说清能接的是哪几种。
     ⚠️ 名单 2026-10-13 缩过一次：「每天」从这儿**出去了** —— 一条 `repeat=daily` 的
     习惯表达得了（只进代办清单、不画进日程格子，见 test_39 的 D 批）。
     闸门本身一条没松，只是例句换了人。
  ② 挂着上一轮候选卡（事名「写作业」）时说「周五晚上七点到九点值日」→ 卡片标题成了
     「值日写作业」。病根在 `parse_slot_text` 无条件把上一轮的事名**拼进这句一起解析**。
     他换了事情，系统却把上一轮的事情缝进名字里。

⚠️ 边界是刻意划的：`_UNSUPPORTED_PERIOD` 里**不许**有「平时/工作日」——
   `candidate_slots` 的 workday_mode 早就支持"从今天起跨工作日摊开列"，
   列进来等于打掉现在能用的功能（同一句话判据别越划越宽）。
"""
import ast
import io
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title
from app.store import set_settings

PLANNER = "app/modules/planner.py"
MAIN = "app/main.py"


def _samples():
    p = data_path("intent_samples.json")
    if not os.path.exists(p):
        return []
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _no_key_settings():
    set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "m"})


def batch_a_period_boundary():
    c = Checker()
    title("A 批 · unsupported_period：拦哪些、必须放哪些")
    from app.modules.planner import _UNSUPPORTED_PERIOD, unsupported_period

    # ⚠️ 例句 2026-10-13 换过：原来这里第一、二条是「每天背30个单词」「天天练琴」，
    #   可"每天"现在**表达得了**（一条 daily 习惯，只进代办不进日历），
    #   拿它当"拦得住"的例子，钉的就不是这道闸了。改抓「每月/每个周末/隔天」这些真表达不了的。
    for text, word in (("每个月交一次电费", "每个月"),
                       ("每个周末去爬山", "每个周末"),
                       ("隔一天去跑步", "隔一天"),
                       ("在下个月的每个周末三点开始大约三个小时", "每个周末")):
        c.check(f"{text} → 认出「{word}」", unsupported_period(text) == word,
                unsupported_period(text))
    for text in ("每周三下午两点开会", "每个周天都写作业", "一周去三次健身房",
                 "这周六去爬山", "今晚八点排练",
                 # ★ 2026-10-13 新增这一档：每天类习惯表达得了，就不许再被这道闸拦下
                 "每天背30个单词", "天天练琴", "每日英语打卡", "以后每天晨跑"):
        c.check(f"{text} → 不拦（这是能表达的：每周几 / 这一周N次 / 就这一天）",
                unsupported_period(text) == "", unsupported_period(text))
    # 词表抄不完 → 结构性判据（2026-10-11 一批真机句子打出来的漏网之鱼）
    for text, word in (("两周去一次健身房", "两周去一次"),
                       ("三周练一回琴", "三周练一回"),
                       ("一个月剪两次头发", "一个月剪两次"),
                       ("每周两次开会", "每周两次"),
                       ("隔两天洗一次车", "隔两天")):
        c.check(f"结构性判据认得出「{text}」（词表里没有这个语序）",
                unsupported_period(text) == word, unsupported_period(text))
    # 反向：**具体日子**不是周期，误伤就等于把正常下单拦死了
    for text in ("10月20日下午三点看电影", "下周三三点开会", "9月1号到9月5号有课",
                 "这周六下午三点游泳", "一周三次健身房"):
        c.check(f"{text} → 不算周期（那是具体日子/现有能力，拦了就是误伤）",
                unsupported_period(text) == "", unsupported_period(text))
    # 边界本身也要钉：判据里混进「平时/工作日」就等于把 workday_mode 那条能用的路打死
    c.check("判据里不许有「平时」「工作日」（跨工作日摊开列是现有功能）",
            not any(w in ("平时", "工作日") for w in _UNSUPPORTED_PERIOD),
            _UNSUPPORTED_PERIOD)
    c.check("空输入不炸", unsupported_period("") == "" and unsupported_period(None) == "")
    return c


def batch_b_veto_no_card_no_learning():
    c = Checker()
    title("B 批 · 判定层否决：不出卡、不写库、**更不许学进样本库**")
    import app.main as M
    from app.store import list_todos

    async def fake_sem(text, conv=None, override=None, **kw):
        # 沙箱没有密钥，真语义层会静默返回 None —— 必须打这个假货，
        # 否则"样本库/语义层单独接单"那一路根本没被走过，断言就是假绿。
        return {"intent": "add_todo", "module": "planner", "title": "背单词"}

    real_sem = M.classify
    with sandbox():
        _no_key_settings()
        cli = make_client()
        try:
            M.classify = fake_sem

            n0 = len(_samples())
            t0 = len(list_todos())
            # 旗舰句 2026-10-13 从「每天背30个单词」换成「每个月交一次电费」：
            # 前者现在**该出卡**（daily 习惯），拿它钉"语义层判了也不许出卡"就自相矛盾了。
            r = cli.post("/api/chat", json={"message": "每个月交一次电费",
                                            "session_id": "t42-b", "module": "planner"})
            d0 = r.json()
            ans = d0.get("answer") or ""
            c.check("接口 200", r.status_code == 200, str(r.status_code))
            c.check("语义层判了 add_todo 也**一张卡都不出**",
                    not [o for o in (d0.get("options") or []) if isinstance(o, dict)],
                    d0.get("options"))
            c.check("回话里要说清这句为什么办不了（不是含糊一句「没听懂」）",
                    "记不了" in ans and "每个月" in ans, ans[:120])
            c.check("要给出**三种能办的说法**（每周几 / 每天 / 这一周N次），否则学生无路可走",
                    "每周" in ans and "这一周" in ans and "每天" in ans, ans[:200])
            c.check("这句**不许学进样本库**（学了就等于把同一个错存成模板）",
                    len(_samples()) == n0,
                    [s.get("text") for s in _samples()][n0:])
            c.check("也**没写成待办**", len(list_todos()) == t0, len(list_todos()))

            # 对照组：同一句里的周期换成能表达的，就必须照常出卡
            r2 = cli.post("/api/chat", json={"message": "每周三下午两点开会",
                                             "session_id": "t42-b2", "module": "planner"})
            d2 = r2.json()
            cards = [o for o in (d2.get("options") or []) if isinstance(o, dict)]
            c.check("对照：「每周三下午两点开会」照常出卡、14:00 用的是他点的钟点",
                    bool(cards) and any(
                        (x.get("start") == "14:00" or any(
                            s.get("start") == "14:00" for s in (x.get("slots") or [])))
                        for x in cards),
                    [(x.get("kind"), x.get("start")) for x in cards])
            c.check("对照：「我平时想去跑步」（工作日能表达）不被这条否决打掉",
                    "记不了" not in (cli.post("/api/chat", json={
                        "message": "我平时想去跑步", "session_id": "t42-b3",
                        "module": "planner"}).json().get("answer") or "")[:200])
        finally:
            M.classify = real_sem
    return c


def batch_c_shape_pinned():
    c = Checker()
    title("C 批 · 钉代码形状：否决在判定层、且不走 _sem_done（AST）")
    tree = ast.parse(io.open(MAIN, encoding="utf-8").read())
    fn = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    body = fn["_chat_impl"].body

    def _src(node):
        return ast.unparse(node).replace("'", '"')

    period_ifs = [n for n in ast.walk(fn["_chat_impl"])
                  if isinstance(n, ast.If) and "_period_block" in _src(n.test)]
    c.check("main 里有一条按 `_period_block` 分叉的分支（不是散在各处的 if）",
            len(period_ifs) >= 1, len(period_ifs))
    if period_ifs:
        seg = _src(period_ifs[0])
        c.check("这条分支**不叫 `_sem_done`**（那个函数就是 remember 学样本的入口）",
                "_sem_done(" not in seg, seg[:120])
        # 2026-10-11：回话与响应组装抽成了 `_period_reject_response`（三个出口共用一份话术），
        # 所以"不出卡"这件事要钉在**它**身上，而不是钉分支里那几行字面量。
        c.check("这条分支走 `_period_reject_response`（话术只有一份，别处不许另写一套）",
                "_period_reject_response(" in seg, seg[:120])
        helper = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_period_reject_response"]
        c.check("`_period_reject_response` 的返回里 `options` 是空列表（真不出卡）",
                bool(helper) and '"options": []' in _src(helper[0]),
                _src(helper[0])[-120:] if helper else "没找到这个函数")
        c.check("它也不挂暂存（函数体里不许出现 save_pending）",
                bool(helper) and "save_pending" not in _src(helper[0]),
                _src(helper[0])[:120] if helper else "")
    # 否决的驱动条件也要钉：必须同时看规则层和语义层，只看一层就是"各读各的"
    assign = [n for n in body if isinstance(n, ast.Assign)
              and any(getattr(t, "id", "") == "_period_block" for t in n.targets)]
    c.check("`_period_block` 在函数开头就算好（判定层收口，不在各分支里重复判）",
            bool(assign) and body.index(assign[0]) < len(body) // 3,
            body.index(assign[0]) if assign else "没找到")
    if assign:
        cond = _src(assign[0].value)
        c.check("条件同时钉住 `_rule_hit` 和 `_sem_intent` 两档（不分层才叫收口）",
                "_rule_hit" in cond and "_sem_intent" in cond, cond)
    # planner 侧：三件提案工具都过同一道关，少一件就是留了个后门
    ptree = ast.parse(io.open(PLANNER, encoding="utf-8").read())
    pfn = {n.name: n for n in ast.walk(ptree) if isinstance(n, ast.FunctionDef)}
    for tool in ("propose_todo_tool", "propose_todo_mode_tool", "propose_todo_slots_tool"):
        node = pfn.get(tool)
        c.check(f"{tool} 调了 period_reject（模型直调那三条路同样拦）",
                node is not None and "period_reject" in _src(node), tool)
    c.check("period_reject 复用 unsupported_period（不另写一套词表）",
            "unsupported_period" in _src(pfn["period_reject"]))
    return c


def batch_d_title_carry():
    c = Checker()
    title("D 批 · 事名不串味：这句自己报了名就不许借上一轮的")
    from app.modules.planner import parse_add_todo, parse_slot_text

    # 先把病根本身钉住：老写法（把上一轮事名拼进这句）会抠出什么
    old = parse_add_todo("周五晚上七点到九点值日 写作业")
    c.check("病根复刻：拼着解析就会得到「值日写作业」（证明下面那条不是假绿）",
            (old or {}).get("title") == "值日写作业", (old or {}).get("title"))
    got = parse_slot_text("周五晚上七点到九点值日", "写作业", "2026-10-15") or {}
    c.check("「周五晚上七点到九点值日」挂着「写作业」→ 卡片名就是「值日」",
            got.get("title") == "值日", got.get("title"))
    c.check("摘要里也不许留旧事名（卡片其他地方同样会露出来）",
            "写作业" not in (got.get("summary") or ""), got.get("summary"))
    c.check("时间照样是他点的 19:00-21:00（改名不能把时间改没）",
            got.get("start") == "19:00" and got.get("end") == "21:00",
            (got.get("start"), got.get("end")))

    # 反方向：只报时间、没报名的事，**必须**照旧沿用上一轮那件事
    for text, want in (("晚上七点到八点", "健身"), ("周六", "健身"),
                       ("下午两点到三点", "健身"), ("周六 19:00-20:00", "健身")):
        p = parse_slot_text(text, want, "2026-10-15") or {}
        c.check(f"只报时间「{text}」→ 事名仍沿用上一轮「{want}」（没报名才借）",
                p.get("title") == want, p.get("title"))

    # 「随便啦」这种既没时间也没事情的：不许硬造一个名字出来
    c.check("「随便啦」（没有时间没有事）→ 不出卡，交给调用方说「这行没认出来」",
            parse_slot_text("随便啦", "健身", "2026-10-15") is None,
            parse_slot_text("随便啦", "健身", "2026-10-15"))
    return c


def batch_e_e2e_carry():
    c = Checker()
    title("E 批 · 端到端：候选卡挂着「写作业」，改口说值日 → 卡片名必须是值日")
    import app.main as M

    async def fake_sem(text, conv=None, override=None, **kw):
        return {"intent": "add_todo", "module": "planner", "title": "写作业"}

    real_sem = M.classify
    with sandbox():
        _no_key_settings()
        cli = make_client()
        try:
            M.classify = fake_sem
            r1 = cli.post("/api/chat", json={"message": "晚上写作业",
                                             "session_id": "t42-e", "module": "planner"})
            d1 = r1.json()
            cards1 = [o for o in (d1.get("options") or []) if isinstance(o, dict)]
            slots_card = next((x for x in cards1 if x.get("kind") == "todo_slots"), None)
            c.check("第一轮：摊出候选时段卡、事名是「写作业」",
                    slots_card is not None and slots_card.get("title") == "写作业",
                    [(x.get("kind"), x.get("title")) for x in cards1])
            if slots_card is not None:
                r2 = cli.post("/api/chat", json={"message": "周五晚上七点到九点值日",
                                                 "session_id": "t42-e",
                                                 "module": "planner"})
                d2 = r2.json()
                cards2 = [o for o in (d2.get("options") or []) if isinstance(o, dict)]
                titles = [x.get("title") for x in cards2]
                c.check("第二轮换了事情 → 卡片名是「值日」，不许出现「值日写作业」",
                        "值日" in titles and not any("写作业" in (t or "") for t in titles),
                        titles)
                c.check("而且时间还是他点的 19:00-21:00",
                        any(x.get("start") == "19:00" and x.get("end") == "21:00"
                            for x in cards2),
                        [(x.get("start"), x.get("end")) for x in cards2])
            else:
                c.check("第一轮没出候选卡（②无法验）", False, str(d1)[:120])
        finally:
            M.classify = real_sem
    return c


def batch_f_funnel_gates():
    c = Checker()
    title("F 批 · 出口漏斗：判定层没出声时，**出卡的地方**也得再判一次")
    # 为什么要有这一批（2026-10-11 一批真机句子打出来的）：判定层那条否决要求
    # "某一档判成 add_todo"，可「以后每月晨跑」两档都没出声，却被"报了事名没定时间"
    # 那一档接走，挂出一张**把"每月"扔掉**的二选一卡；模型那一路更离谱 ——
    # （例句 2026-10-13 从「每天」换成「每月」：每天现在表达得了，见 D 批。）
    # 工具里的 `period_reject` 只看模型自己传的参数，它换个说法就绕过了。
    import app.main as M
    from app.agent.pending import peek_pending

    card = {"kind": "todo_mode", "title": "晨跑", "summary": "晨跑｜还没定时间"}
    with sandbox():
        _no_key_settings()
        r1 = M._offer_response("t42-f1", dict(card), "以后每月晨跑", "原话术", "🙋 二分卡")
        c.check("学生原话里有「每月」→ `_offer_response` 把卡撤了（options 空）",
                r1.get("options") == [], r1.get("options"))
        c.check("回话换成如实说明，而不是原来那句「时间你自己定还是我帮你挑」",
                "记不了" in (r1.get("answer") or ""), (r1.get("answer") or "")[:80])
        c.check("暂存里**没挂上**这张卡（学生点不到，就写不了库）",
                peek_pending("t42-f1") is None, peek_pending("t42-f1"))
        r2 = M._offer_response("t42-f2", dict(card), "周六早上七点晨跑", "原话术", "🙋 二分卡")
        c.check("对照：能表达的说法照常出卡（这道闸不是一刀切拒绝）",
                len(r2.get("options") or []) == 1, r2.get("options"))
        c.check("对照：卡真挂进暂存了", peek_pending("t42-f2") is not None)

        # 判定层没出声、却由模型/兜底那一段自己摊卡 —— 真机上这类句子就是这么
        # 挂出一张三段的候选卡的（无密钥时这段由 mock planner 走，形状一样，正好可测）。
        cli = make_client()
        r3 = cli.post("/api/chat", json={"message": "以后每月晨跑",
                                         "session_id": "t42-f3", "module": "planner"})
        d3 = r3.json()
        opts3 = [o for o in (d3.get("options") or []) if isinstance(o, dict)]
        c.check("两档都没判成 add_todo 的那一句，模型/兜底出口也**一张卡都不留**",
                not opts3, [(o.get("kind"), o.get("title")) for o in opts3])
        c.check("这一句同样要说清为什么办不了（不许默默回一句'帮你挑了三段'）",
                "记不了" in (d3.get("answer") or ""), (d3.get("answer") or "")[:90])

    tree = ast.parse(io.open(MAIN, encoding="utf-8").read())
    impl = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_chat_impl"][0]
    body = ast.unparse(impl).replace("'", '"')
    calls = sum(1 for n in ast.walk(impl) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "unsupported_period")
    c.check("判定层之外，出口那一段也按**学生原话**复核（同一函数里≥3 处：模型出口 + Mock 出口 + 判定层）",
            calls >= 3, f"_chat_impl 里调了 {calls} 次")
    # 撤卡的动作要钉住，而且**不能钉成"清空所有 options"**：课表那几种卡必须留着
    # （兜底/mock 摊出来的候选**没有 kind 字段**，所以判"是不是待办"只能反着写白名单）。
    c.check("撤卡用白名单反写：只留 timetable_change / timetable_clear / course_add",
            '_non_todo = ("timetable_change", "timetable_clear", "course_add")' in body
            and "model_opts = _keep" in body,
            body[body.find("_non_todo"):][:150] if "_non_todo" in body else "没找到 _non_todo")
    c.check("Mock 那条出口自己 return（走不到下面的漏斗），所以它单独判了一次",
            "_per_mock = unsupported_period(message)" in body
            and "return _period_reject_response(session_id, message, _per_mock)" in body,
            "见 main.py 里 mock_planner 那一支")
    whole = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "unsupported_period")
    c.check("判定层 + `_offer_response` + 模型出口，全模块至少三处用同一道判据",
            whole >= 3, f"main 里调了 {whole} 次")
    return c


def main():
    results = [batch_a_period_boundary(), batch_b_veto_no_card_no_learning(),
               batch_c_shape_pinned(), batch_d_title_carry(), batch_e_e2e_carry(),
               batch_f_funnel_gates()]
    tp = sum(r.passed for r in results)
    tf = sum(r.failed for r in results)
    print(f"\n===== 周期否决 + 事名不串味总账：通过 {tp} 项，失败 {tf} 项 =====")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
