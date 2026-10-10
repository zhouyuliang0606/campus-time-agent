"""两条 2026-10-11 真机复现的收口：**不支持的周期不许单独接单**、**事名不许串味**。

起因（都是用户原话，都在真机上跑出来的）：
  ① 「在下个月的每个周末三点开始大约三个小时」→ 系统建了一条**普通待办**（15:00-18:00 那一条），
     而学生要的是"以后每个周末"。规则层其实**从来不接**这一类（`wants_add_todo` 一律 False），
     接单的是**样本库/语义层** —— 它能绕过规则层单独判成 add_todo、出卡，
     还把这句**学进了样本库**（`_sem_done` 里那句 `remember`），于是同一个错会自己繁殖。
     所以判据要有一处**不分层**的收口：句子里出现「每天/每月/每个周末/下个月…」这种
     待办模型表达不了的周期时，**哪一档说"这是加待办"都不出卡**，并如实说清能接的是哪两种。
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

    for text, word in (("每天背30个单词", "每天"),
                       ("天天练琴", "天天"),
                       ("每个月交一次电费", "每个月"),
                       ("每个周末去爬山", "每个周末"),
                       ("隔一天去跑步", "隔一天"),
                       ("在下个月的每个周末三点开始大约三个小时", "每个周末")):
        c.check(f"{text} → 认出「{word}」", unsupported_period(text) == word,
                unsupported_period(text))
    for text in ("每周三下午两点开会", "每个周天都写作业", "一周去三次健身房",
                 "这周六去爬山", "今晚八点排练"):
        c.check(f"{text} → 不拦（这是能表达的：每周几 / 这一周N次 / 就这一天）",
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
            r = cli.post("/api/chat", json={"message": "每天背30个单词",
                                            "session_id": "t42-b", "module": "planner"})
            d0 = r.json()
            ans = d0.get("answer") or ""
            c.check("接口 200", r.status_code == 200, str(r.status_code))
            c.check("语义层判了 add_todo 也**一张卡都不出**",
                    not [o for o in (d0.get("options") or []) if isinstance(o, dict)],
                    d0.get("options"))
            c.check("回话里要说清这句为什么办不了（不是含糊一句「没听懂」）",
                    "记不了" in ans and "每天" in ans, ans[:120])
            c.check("要给出**两种能办的说法**（每周几 / 这一周N次），否则学生无路可走",
                    "每周" in ans and "这一周" in ans, ans[:160])
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
        c.check("这条分支不出卡：返回里 `options` 是空列表",
                '"options": []' in seg, seg[-160:])
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


def main():
    results = [batch_a_period_boundary(), batch_b_veto_no_card_no_learning(),
               batch_c_shape_pinned(), batch_d_title_carry(), batch_e_e2e_carry()]
    tp = sum(r.passed for r in results)
    tf = sum(r.failed for r in results)
    print(f"\n===== 周期否决 + 事名不串味总账：通过 {tp} 项，失败 {tf} 项 =====")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
