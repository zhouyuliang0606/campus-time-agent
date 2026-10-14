"""第十九批测试：时间碎片不能当待办名挂卡（"明天中午12" → 不应出"12"的确认卡）。

背景
====
学生报障：说一句没说完的话「明天中午12」就直接被加了待办，卡片标题叫「12」、
时间 2026-10-01 12:00-13:00。系统把「12」这个时间碎片当成了「一件事」的名字。

根因
====
`_pick_title` 擦完时间词后剩下「12」，而 `looks_like_thing("12")` 返回 True：
长度 2 在允许区间、有字符、不含噪声词。于是「12」被当成真事名，
`parse_add_todo` / `span_only_proposal` 就把它排进了确认卡。

修复
====
1. `looks_like_thing` 新增守卫：标题整串只剩数字/中文数字/钟点词
   （"12" / "十二" / "12点" / "十点半" / "一刻" / "整点" / "3分"）→ 不是一件事。
2. `parse_add_todo` 和 `span_only_proposal` 在生成提案前再过 `_title_ok`，
   防止语义层把"12"这种时间碎片当 title 传下来时 still 出卡。

验证
====
  · 单元：looks_like_thing / _pick_title 对时间碎片返回 False / "待办"；
  · 单元：真实事名（游泳/项目12/三体/双十一）仍通过；
  · 端到端：planner 栏说「明天中午12」→ 不出 todo_add 卡，进入追问；
  · 端到端：planner 栏说真加待办「帮我加个游泳周四19:00」→ 仍正常出卡。

全部跑在数据沙箱里，不碰真实演示数据。
"""
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title, sandbox, make_client

from app.modules.planner import looks_like_thing, _pick_title, _title_ok


class _FakeLLM:
    """只用于语义分类的假模型（引擎在这两条测试里不会走到，但统一备着）。"""
    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        return {"content": self._reply}


def _patch_llm(sem_reply: str):
    """把语义分类用的大模型客户端换成假货；引擎如走到也用它（返回纯文本）。

    ⚠️ 只打 `app.llm.client.DeepSeekClient` 一处就够了（2026-10-02 起）：
    引擎不再自己 import 客户端，它走**网关**，而网关是**在要用的时候**才
    `from app.llm.client import DeepSeekClient`，所以改模块属性照样看得见。
    以前"引擎在模块顶层 import，必须连 `app.agent.engine.DeepSeekClient` 一起打"
    那条经验，已经在网关化之后失效——别再照抄回去。
    """
    import app.config as _cfg
    import app.llm.client as _client
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    _factory = lambda *a, **k: _FakeLLM(sem_reply)
    _client.DeepSeekClient = _factory
    _cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                           "model": "m"}

    def _restore():
        _client.DeepSeekClient = old_client
        _cfg.get_llm_config = old_cfg
    return _restore


def _has_todo_card(opts):
    return any(
        isinstance(o, dict) and o.get("kind") in ("todo_add", "todo_mode", "todo_slots")
        for o in (opts or [])
    )


def test_rejects_time_only_titles():
    title("1. looks_like_thing：纯时间/数字碎片不是一件事")
    c = Checker()
    bad = ("12", "十二", "12点", "十点半", "一刻", "整点", "3分", "零分")
    for t in bad:
        c.check(f"「{t}」→ 不像一件事", not looks_like_thing(t))
    # 边界：长度不够的直接被长度守卫拦住，但这里显式测 also
    c.check("空串不像一件事", not looks_like_thing(""))
    c.check("单字『1』不像一件事", not looks_like_thing("1"))
    return c.summary("第一批（时间碎片判假）")


def test_pick_title_fragments():
    title("2. _pick_title：时间碎片擦完或判不过 → 返回 '待办'（触发追问）")
    c = Checker()
    for raw in ("明天中午12", "明天十二点", "12点帮我安排一下", "后天12",
                "大概一个小时"):
        got = _pick_title(raw)
        c.check(f"「{raw}」→ 标题未明（待办）", got == "待办", repr(got))
    return c.summary("第二批（标题未明）")


def test_pick_title_keeps_real_things():
    title("3. _pick_title：真实事名不受影响")
    c = Checker()
    for raw, want in (
        ("游泳", "游泳"),
        ("帮我加个游泳周四19:00", "游泳"),
        ("三体", "三体"),
        ("双十一", "双十一"),
        ("作业12", "作业12"),
    ):
        got = _pick_title(raw)
        c.check(f"「{raw}」→ 标题是「{want}」", got == want, repr(got))
        c.check(f"「{got}」通过 _title_ok", _title_ok(got))
    return c.summary("第三批（真实事名保留）")


def test_chat_no_card_for_time_fragment():
    title("4. 端到端：planner 栏『明天中午12』不出卡、进入追问")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(_json.dumps({"module": "planner", "intent": "add_todo",
                                           "title": "12", "confidence": 0.9}))
        try:
            resp = client.post("/api/chat", json={"message": "明天中午12",
                                                   "module": "planner"})
            body = resp.json()
        finally:
            restore()

        opts = body.get("options") or []
        c.check("响应里没有任何待办卡", not _has_todo_card(opts),
                _json.dumps(opts, ensure_ascii=False)[:80])
        c.check("不需要学生去确认卡片", not body.get("awaiting_choice", False))
        c.check("回答进入追问（含'什么事'）", "什么" in body.get("answer", ""),
                body.get("answer", "")[:40])
    return c.summary("第四批（时间碎片不出卡）")


def test_chat_still_adds_real_todo():
    title("5. 端到端：真加待办仍正常出卡（不误伤）")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(_json.dumps({"module": "planner", "intent": "add_todo",
                                           "title": "游泳", "confidence": 0.9}))
        try:
            resp = client.post(
                "/api/chat",
                json={"message": "帮我加个游泳周四19:00", "module": "planner"},
            )
            body = resp.json()
        finally:
            restore()

        opts = body.get("options") or []
        card = next((o for o in opts if isinstance(o, dict)
                     and o.get("kind") == "todo_add"), None)
        c.check("真加待办 → 仍出 todo_add 卡", card is not None,
                _json.dumps(opts, ensure_ascii=False)[:80])
        if card:
            c.check("卡片事名是『游泳』", card.get("title") == "游泳", card.get("title"))
            c.check("卡片时间对（19:00-20:00）",
                    card.get("start") == "19:00" and card.get("end") == "20:00",
                    f"{card.get('start')}-{card.get('end')}")
    return c.summary("第五批（真加待办仍出卡）")


if __name__ == "__main__":
    fails = 0
    fails += test_rejects_time_only_titles()
    fails += test_pick_title_fragments()
    fails += test_pick_title_keeps_real_things()
    fails += test_chat_no_card_for_time_fragment()
    fails += test_chat_still_adds_real_todo()
    sys.exit(1 if fails else 0)
