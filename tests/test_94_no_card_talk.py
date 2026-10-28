# -*- coding: utf-8 -*-
"""#80 护栏：屏幕上没卡，回话就不许说「去勾 / 去点」（test_94）。

**守的是什么**：2026-10-28 23:08:40 真机那一幕（`trace_log.jsonl` id=`35efcea54909`，
`tool_calls=2` 而 `options_n=0`）—— 学生说「撞时间了有一处」，它查了周四的课、也算出了两段空档，
**唯独没调 `propose_slots`**，然后照提示词工作流程第 3 条补了一句「勾一个，点【加入日程】。」
学生的说法是「瞎编一个弹窗」。

各批分工（缺一批就是哑弹）：
  · **A 函数级**：`_drop_card_talk` 摘得对不对 —— 负控与正控**成对**（施工单 §3.2）。
    A7~A9 钉的不是 bug，是**挂载位置的理由**：那三份系统自己写的无卡回话同样被词表命中，
    所以判据只能挂在"模型原文"那一支；挪到外层出口 = 砍掉刚说出口的那句实话。
  · **B 静态接线**：那一处落在 `_run_agent_loop` 的 `not model_opts` 圈内、在 `claims_proposal_sent`
    之后、且**早于** `append_conversation(..., final_answer)`。
  · **C 端到端**：真打 `/api/chat`，先证明这一句**走到了**那一支（轨迹里有那行 🛟）再看屏幕上的话 ——
    函数级过、链路没走到是本项目最贵的那类假绿。
  · **D 提示词与判定行**：§1.1 的前提（「调用成功之后」）回来了、反作用的尾巴（「绝不能省」）没了；
    判定行里不许出现「不出卡」这种承诺（判成问事 ≠ 不出卡）。
"""
import ast
import datetime as _dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (PROJ_DIR, Checker, make_client, sandbox, seed_todos, title,   # noqa: E402
                      write_accounts, write_timetable)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
PLANNER = os.path.join(PROJ_DIR, "app", "modules", "planner.py")

# 真机那句（从 `trace_log.jsonl` 那条的 💡 最终回答原样抄，一个字没改）
LIE = ("确实撞了：周四 14:00-15:40 有大学物理，游泳 15:00 开始，重叠 40 分钟。\n\n"
       "周四剩下的空档：15:40-19:00、22:00 之后。给你两段：\n\n"
       "- 周四 16:00-17:00\n- 周四 17:30-18:30\n\n"
       "勾一个，点【加入日程】。")

# 施工单 §1.2 给的那串说法：一个都不许漏（漏一个 = 那一族句子照样瞎编而没人管）
CARD_TALK = ["勾一个", "勾一下", "打勾", "勾选", "勾好", "下面这条卡", "下面那张卡", "下面的卡",
             "在卡上", "卡片上", "点【加入日程】", "点【确认】", "点一下就能", "点一下想"]
# §五.3 点名"不许进词表"的泛词：它们大量出现在**真出卡**的正确话术里
FORBIDDEN_WORDS = ["确认条", "已生成", "选一个", "怎么选", "点确认"]

# 日期跟着"下一个周四"走，别写死（写死就是挂账 #69 那一族的第五个实例）
_NEXT_THU = _dt.date.today() + _dt.timedelta(days=(4 - _dt.date.today().isoweekday()) % 7 or 7)
_THU = _NEXT_THU.isoformat()


# --------------------------------------------------------------------------- 假模型
class Scripted:
    """剧本模型：判定层那一趟（不带 tools）回一句解析不了的废话，引擎那几趟按剧本走。

    ⚠️ 签名必须与 `DeepSeekClient.chat` 一致（`tool_choice`/`max_tokens` 都得在）——
    少一个参数不会在这儿报错，会被判定层的 `except Exception` 吞成"模型没答"。
    """

    def __init__(self, turns):
        self.turns = turns
        self.calls = 0

    async def chat(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        if not tools:                       # 判定层：让它解析失败 ⇒ 回到"语义层没给结论"那一档
            return {"content": "这不是 JSON"}
        idx = min(self.calls, len(self.turns) - 1)
        self.calls += 1
        return self.turns[idx]


def _patch(script):
    """把模型客户端换成 `script`，并伪装有密钥。

    ⚠️ 没有 key 时 planner 走的是 `mock_planner` 那条**早退**（`_run_agent_loop` 里就 return 了），
    根本到不了本单的守卫 —— 施工单 §1.2 末句"离线/mock 那条同样被治到"就是这么没成立的：
    mock 那份答案自带候选卡（`options` 非空），本单不需要治它，也就治不到它。
    """
    import app.config as _cfg
    import app.llm.client as _client
    import app.main as _M

    cfg = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x", "model": "m"}  # noqa: E731
    old = (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config)
    _client.DeepSeekClient = lambda *a, **k: script
    _cfg.get_llm_config = cfg
    _M.get_llm_config = cfg

    def restore():
        _client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config = old
    return restore


# --------------------------------------------------------------------------- A 函数级
def batch_a_function():
    c = Checker()
    title("A 函数级 · 摘得对不对（负控与正控成对）")
    from app.main import _CARD_TALK_RE, _NO_CARD_TAIL, _drop_card_talk

    out = _drop_card_talk(LIE)
    c.check("A1 负控：那句指向不存在的卡片被摘干净了（「勾一个」「点【加入日程】」都不在）",
            "勾一个" not in out and "点【加入日程】" not in out, out[-70:].replace("\n", " ⏎ "))
    c.check("A2 负控：补的是那句实话（明说这一轮没挂出卡片）",
            "没有挂出卡片" in out and _NO_CARD_TAIL in out, "")
    c.check("A3 负控：真查出来算出来的东西一行没丢（冲突 + 两段空档原样还在）",
            "14:00-15:40 有大学物理" in out and "- 周四 16:00-17:00" in out
            and "- 周四 17:30-18:30" in out, "")
    _kept = [ln for ln in LIE.splitlines() if ln.strip() and not _CARD_TALK_RE.search(ln)]
    c.check("A4 负控：只摘命中的那一行，没连坐（原回答里每个实质行都还在）",
            all(ln in out for ln in _kept) and out.count("周四剩下的空档") == 1,
            f"{len(_kept)} 行实质内容")
    c.check("A5 正控（施工单 §五.2 的命门）：不含动作词的回答**逐字不动**"
            "（函数自带这道前置筛，调用方哪天忘了 `search` 也误伤不到）",
            _drop_card_talk("周四有三门课。\n高等数学 08:00-09:40。")
            == "周四有三门课。\n高等数学 08:00-09:40。", "")
    c.check("A6 空回话不炸、也不凭空造一句出去", _drop_card_talk("") == "",
            repr(_drop_card_talk(""))[:40])

    from app.main import _no_card_answer, _period_reject_answer

    note_ans = ("⚠️ 刚才那句是我自己记账的话，不是说给你听的 —— 这一轮我没办成什么。\n"
                "你再说一遍要加什么（比如「帮我加个游泳」），我重新出一张确认条；"
                "点【确认加入】或回一句「确认」才会真写进日程。")
    risks = (("_no_card_answer（自称已提交、其实没卡的那句纠正）", _no_card_answer()),
             ("_period_reject_answer（周期接不了，里面写着「你打勾」）", _period_reject_answer("每周三次")),
             ("记账话术（`_is_system_note` 那一支自己写的回话）", note_ans))
    for i, (name, txt) in enumerate(risks, start=7):
        c.check(f"A{i} 风险实证：{name} **确实**被这套词表命中"
                "（⇒ 守卫只能挂在模型原文那一支；挪到外层出口就是把实话改成假话）",
                bool(_CARD_TALK_RE.search(txt)),
                next((ln for ln in txt.splitlines() if _CARD_TALK_RE.search(ln)), "")[:56])

    pat = _CARD_TALK_RE.pattern
    c.check("A10 词表宁短勿长：泛词一个都不许进（「确认条 / 已生成 / 选一个」都是真出卡时的正确说法）",
            not any(w in pat for w in FORBIDDEN_WORDS), [w for w in FORBIDDEN_WORDS if w in pat])
    c.check("A11 词表逐个有效：上面那串说法一个都没漏",
            all(_CARD_TALK_RE.search("请" + w) for w in CARD_TALK),
            [w for w in CARD_TALK if not _CARD_TALK_RE.search("请" + w)])
    c.check("A12 补的那句自己不许带病：里面不能再有任何一个命中词",
            not _CARD_TALK_RE.search(_NO_CARD_TAIL), _NO_CARD_TAIL[:56].replace("\n", " ⏎ "))
    return c


# --------------------------------------------------------------------------- B 静态接线
def batch_b_wiring():
    c = Checker()
    title("B 静态接线 · 守卫挂在哪儿，比它长什么样更要紧")
    mt = ast.parse(open(MAIN, encoding="utf-8").read())
    ral = next((n for n in ast.walk(mt)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_agent_loop"), None)
    c.check("B0 `_run_agent_loop` 还在（本单的判据全挂在它身上）", ral is not None, "")

    calls = [n for n in ast.walk(ral or mt) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_drop_card_talk"]
    c.check("B1 主链路里 `_drop_card_talk` **只被调用一处**（两处各砍一次 = 同一判据抄两遍，红线 4/11）",
            len(calls) == 1, f"{len(calls)} 处")
    call_line = calls[0].lineno if calls else -1

    gates = [n for n in ast.walk(ral or mt) if isinstance(n, ast.If)
             and ast.unparse(n.test) == "not model_opts"]
    inside = bool(calls and gates) and any(x is calls[0] for x in ast.walk(gates[0]))
    c.check("B2 那一处落在 `elif not model_opts:` **圈内**（判据是「没挂出卡」这个客观事实，不是 intent）",
            inside, f"闸门 If {len(gates)} 处")

    claims = [n for n in ast.walk(ral or mt) if isinstance(n, ast.If)
              and "claims_proposal_sent" in ast.unparse(n.test)]
    c.check("B3 位置在那条链的**末尾**：`claims_proposal_sent` 那支在前、这一支在后"
            "（排前面就先抢走系统那句实话，见 A7~A9）",
            bool(claims) and call_line > max(n.test.lineno for n in claims),
            f"claims@{max([n.test.lineno for n in claims], default=-1)} vs 本支@{call_line}")

    cj = next((n for n in ast.walk(mt)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "_chat_json"), None)
    c.check("B4 `_chat_json` 那个统一出口里**没有**这一刀（它看得见系统自写的无卡回话，砍的就是实话）",
            cj is not None and not any(isinstance(x, ast.Call)
                                       and getattr(x.func, "id", "") == "_drop_card_talk"
                                       for x in ast.walk(cj)), "")

    appends = [n for n in ast.walk(ral or mt) if isinstance(n, ast.Call)
               and getattr(n.func, "id", "") == "append_conversation"
               and any(getattr(a, "id", "") == "final_answer" for a in n.args)]
    c.check("B5 写会话历史那句 `append_conversation(..., final_answer)` 在守卫**之后**"
            "（历史里也得是改过的那句，否则下一轮模型照着上文接着编）",
            bool(appends) and call_line < min(n.lineno for n in appends),
            f"append@{min([n.lineno for n in appends], default=-1)} vs 守卫@{call_line}")

    src = open(MAIN, encoding="utf-8").read()
    c.check("B6 词表只有一份（`_CARD_TALK_RE = re.compile` 恰 1 处），`点【…】` 那条带花括号限长",
            src.count("_CARD_TALK_RE = re.compile(") == 1 and "[^】]{1,8}" in src, "")
    c.check("B7 这一刀在轨迹里留了一行（管理端「Agent 工作流」看得见它改过话，不是静默改写）",
            any(isinstance(x, ast.Dict) and any("🛟" in ast.unparse(v) for v in x.values)
                for x in ast.walk(ral or mt)), "")
    c.check("B8 判定挂在**模型原文**上（那一支的条件是 `_CARD_TALK_RE.search(result[\"answer\"]...)`，"
            "不是挂在 `final_answer` 上 —— 后者会连系统话术一起扫）",
            any(isinstance(x, ast.If) and "_CARD_TALK_RE.search" in ast.unparse(x.test)
                and "result[" in ast.unparse(x.test) for x in ast.walk(ral or mt)), "")
    return c


# --------------------------------------------------------------------------- C 端到端
def batch_c_endtoend():
    c = Checker()
    title("C 端到端 · 真打 /api/chat：先证明走到了，再看屏幕上的话")
    from app.store import get_conversation

    with sandbox():
        write_accounts(["admin1"])
        write_timetable([{"day": 4, "start": "14:00", "end": "15:40",
                          "course": "大学物理", "location": "教二205"}])
        seed_todos([{"title": "游泳", "date": _THU, "start": "15:00", "end": "16:00"}])
        cli = make_client()

        # ---- 负控：模型只把候选念在嘴里，一次提案工具都没调
        restore = _patch(Scripted([{"content": LIE}]))
        try:
            d = cli.post("/api/chat", json={"message": "撞时间了有一处",
                                            "session_id": "t94-lie", "module": "planner"}).json()
        finally:
            restore()
        ans = str(d.get("answer") or "")
        phases = " | ".join(str(t.get("phase")) for t in (d.get("trace") or []))
        c.check("C0 这一句**真的走到了**守卫那一支（轨迹里有那行 🛟；没有就是函数级过、链路没走到）",
                "去勾/点" in phases, phases[:130])
        c.check("C1 屏幕上的话不再指向一张不存在的卡（「勾一个」「点【加入日程】」都不在）",
                "勾一个" not in ans and "点【加入日程】" not in ans, ans[-70:].replace("\n", " ⏎ "))
        c.check("C2 真算出来的空档留下了，末尾补的是那句实话",
                "- 周四 16:00-17:00" in ans and "没有挂出卡片" in ans, "")
        c.check("C3 本单不替模型出卡：`options` 仍为空（只治话，不治判定）",
                not (d.get("options") or []), f"{len(d.get('options') or [])} 张")
        hist = get_conversation("t94-lie")
        last = str((hist[-1] if hist else {}).get("content") or "")
        c.check("C4 会话历史里存的就是改过的那句（下一轮模型看不到那句瞎编）",
                bool(hist) and "勾一个" not in last and "没有挂出卡片" in last, last[-56:])

        # ---- 正控：模型真调了 propose_slots、屏幕上**有**卡 ⇒ 一个字都不许动
        opts = [{"title": "做题", "date": _THU, "start": "16:00", "end": "17:00"},
                {"title": "做题", "date": _THU, "start": "17:30", "end": "18:30"}]
        say = "周四剩下的空档给你两段，勾一个就行。"
        turns = [{"tool_calls": [{"id": "call_1", "type": "function", "function": {
                       "name": "propose_slots",
                       "arguments": json.dumps({"options_json": json.dumps(opts,
                                                                          ensure_ascii=False)},
                                               ensure_ascii=False)}}]},
                 {"content": say}]
        restore2 = _patch(Scripted(turns))
        try:
            d2 = cli.post("/api/chat", json={"message": "撞时间了有一处，给我两段空档",
                                             "session_id": "t94-card",
                                             "module": "planner"}).json()
        finally:
            restore2()
        ans2 = str(d2.get("answer") or "")
        ph2 = " | ".join(str(t.get("phase")) for t in (d2.get("trace") or []))
        c.check("C5 正控：这一轮**真出了卡**（`options` 非空）——这条不成立，C6 就是空判据",
                bool(d2.get("options")), f"{len(d2.get('options') or [])} 张")
        c.check("C6 正控（本单命门）：卡都在，那句「勾一个就行」逐字未改（拿 == 比，不是 in）",
                ans2 == say, repr(ans2)[:80])
        c.check("C7 正控：这一轮的轨迹里不许出现那行 🛟（判据压根没被走到，才算真没误伤）",
                "去勾/点" not in ph2, ph2[:110])
    return c


# --------------------------------------------------------------------------- D 提示词与判定行
def batch_d_prompt_and_reason():
    c = Checker()
    title("D 提示词与判定行 · §1.1 的前提回来了，§1.3 那种谎没写进去")
    ps = open(PLANNER, encoding="utf-8").read()
    pt = ast.parse(ps)
    fn = next((n for n in ast.walk(pt) if isinstance(n, ast.FunctionDef)
               and n.name == "build_system_prompt"), None)
    seg = (ast.get_source_segment(ps, fn) or "") if fn else ""

    c.check("D1 第 3 条补回了前提（**调用成功之后**才可以在文字里补那句）", "调用成功之后" in seg, "")
    c.check("D2 反作用最强的那条尾巴没了（「绝不能省」不再出现 —— 它只强调了补话）",
            "绝不能省" not in seg, "")
    c.check("D3 第 2 条那句「必须调用 propose_slots 工具」原样保留（本单没削弱它，只是补前提）",
            "必须调用 propose_slots 工具" in seg, "")
    c.check("D4 新加的那句把后果说死了（只在文字里罗列 = 屏幕上什么都没有）",
            "只在文字里罗列时间段等于什么都没给学生" in seg, "")
    c.check("D5 顺序对：先「调用成功之后」、后「点一下想要的时间就行」（反过来就又成了无条件补话）",
            "调用成功之后" in seg and "点一下想要的时间就行" in seg
            and seg.index("调用成功之后") < seg.index("点一下想要的时间就行"), "")
    c.check("D6 这一族禁令也进了提示词（没出卡就不许提「勾选 / 打勾 / 点【…】」）",
            "一个字都不许提" in seg, "")

    from app.main import _reason_steps
    base = dict(rule_hit=None, asks=False, qa_view=False, rule_veto=False,
                period_block=False, done_claim=False, physio=False, is_material=False,
                sem=None, sem_intent=None)
    matrix = [dict(base), dict(base, physio=True), dict(base, rule_hit="add_todo"),
              dict(base, rule_veto=True), dict(base, is_material=True),
              dict(base, asks=True, sem_intent="chat", sem={"intent": "chat"}),
              dict(base, sem={"intent": "chat"}, sem_intent="chat"),
              dict(base, qa_view=True, rule_hit="add_todo"),
              dict(base, period_block=True),
              dict(base, done_claim=True, sem_intent="done_todo", rule_hit="add_todo",
                   sem={"intent": "done_todo"})]
    lines = [str(r.get("answer")) for kw in matrix for r in _reason_steps(**kw)]
    blob = " / ".join(lines)
    c.check("D7 判定行的全触发矩阵里**没有任何一句承诺结果**（「不出卡 / 没出卡」都不许出现）",
            "不出卡" not in blob and "没出卡" not in blob,
            [ln for ln in lines if "不出卡" in ln or "没出卡" in ln][:1])
    c.check("D8 「按聊天答」这句照旧留着（test_81 的 D 批按子串钉它 —— 本单 §1.3 选择不改这句话）",
            any("按聊天答" in ln for ln in lines), "")
    return c


def main():
    print("===== 没出卡却让人勾选（test_94） =====")
    code = 0
    for fn_ in (batch_a_function, batch_b_wiring, batch_c_endtoend, batch_d_prompt_and_reason):
        code |= fn_().summary()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
