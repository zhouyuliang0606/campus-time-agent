"""第二十批测试：架构升级 · 模型提案 → 系统裁决（试点 1 = 3d 读待办，试点 2 = 3c 加待办）。

背景
====
现状里确定性分支命中后回的是**硬编码模板话术**（「📝 要不要把 **X** 排进日程？…」），
事实都对、但听着"呆"。本批验证新增的**润色层**（`app/agent/polish.py`）：

    系统算好事实 → 模型把它说成人话（润色）→ 失败/没 key/超时/承诺词违规 → 退回模板

三条红线（本批逐条钉住）：
  1. 模型只复述、不决定事实（卡字段 / 列表数据与迁移前逐字一致）；
  2. 失败即降级（离线/超时/异常 → 原模板，用户侧无感知、不 500）；
  3. 承诺词守卫（模型说"已经帮你办好了"但本轮没写操作 → 拦下、退回模板）。

验证手段（对照式，防假绿）
====================
  · 单元：polish_reply 在 没key/有key/空回复/承诺词/超时 五种输入下的取舍；
  · 端到端 3d、3c：打桩模型 → 润色话术出现；不打桩（沙箱无 key）→ 模板话术，
    且**卡字段 == 迁移前的确定性解析**、**交给模型的事实 == 库里的真实列表**；
  · 反向验证：把 polish 拆成"永远返回 fallback" → 润色话术必须消失（证明断言盯着真链路）。

全部跑在数据沙箱里，不碰真实演示数据。
"""
import asyncio
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title, sandbox, make_client  # noqa: E402

from app.agent import polish as _polish  # noqa: E402
from app.agent.polish import polish_reply, has_promise  # noqa: E402


class _FakeLLM:
    """假模型：照剧本回一句，可选延迟（用来测超时降级）。"""
    def __init__(self, reply: str, delay: float = 0.0):
        self._reply, self._delay = reply, delay

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        if self._delay:
            await asyncio.sleep(self._delay)
        return {"content": self._reply}


def _patch_client(reply: str, with_key: bool = True, delay: float = 0.0):
    """把客户端换成假货（+ 可选：配 key / 延迟）。返回恢复函数。

    ⚠️ 两处模块属性要打：`app.llm.client.DeepSeekClient`（分类/路由/润色/网关
    都是**函数内** import，看得见补丁）、`app.config.get_llm_config`（决定有没有 key）。

    2026-10-02 起**不再打** `app.agent.engine.DeepSeekClient`：引擎改走网关，
    网关按需 import 客户端，打 `app.llm.client` 一处就通。要是哪天又有人给引擎
    加回模块顶层的客户端 import，这条经验就得翻回来——但那是倒退，别做。
    """
    import app.config as _cfg
    import app.llm.client as _client
    old = (_client.DeepSeekClient, _cfg.get_llm_config)
    factory = lambda *a, **k: _FakeLLM(reply, delay)
    _client.DeepSeekClient = factory
    if with_key:
        _cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key",
                                               "base_url": "http://x", "model": "m"}
    else:
        _cfg.get_llm_config = lambda *a, **k: {"api_key": "",
                                               "base_url": "http://x", "model": "m"}

    def _restore():
        _client.DeepSeekClient, _cfg.get_llm_config = old
    return _restore


def _say(client, msg, sid, module="planner"):
    return client.post("/api/chat", json={"message": msg, "module": module,
                                          "session_id": sid}).json()


def _has_todo_card(opts):
    return any(isinstance(o, dict) and o.get("kind") in ("todo_add", "todo_mode", "todo_slots")
               for o in (opts or []))


# ── 第一批：polish_reply 单元（五种输入的取舍） ──────────────────────────────
def test_polish_unit():
    title("1. polish_reply 单元：没key/有key/空回复/承诺词/超时")
    c = Checker()
    facts = {"intent": "list_todos", "count": 0, "todos": []}

    r = _patch_client("模型文本", with_key=False)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK"))
    finally:
        r()
    c.check("没配 key → 退回模板", got == "FALLBACK", repr(got))

    r = _patch_client("模型文本", with_key=True)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK"))
    finally:
        r()
    c.check("有 key → 用模型文本", got == "模型文本", repr(got))

    r = _patch_client("", with_key=True)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK"))
    finally:
        r()
    c.check("模型回空 → 退回模板", got == "FALLBACK", repr(got))

    r = _patch_client('{"intent":"list_todo","title":""}', with_key=True)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK"))
    finally:
        r()
    c.check("模型回成 JSON → 判无效、退回模板", got == "FALLBACK", repr(got))

    r = _patch_client("已经帮你排好了～", with_key=True)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK"))
    finally:
        r()
    c.check("承诺词 + 本轮没写操作 → 退回模板", got == "FALLBACK", repr(got))

    r = _patch_client("已经帮你排好了～", with_key=True)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK", executed=True))
    finally:
        r()
    c.check("承诺词 + 本轮确实写了 → 放行", got == "已经帮你排好了～", repr(got))

    r = _patch_client("慢回复", with_key=True, delay=0.4)
    try:
        got = asyncio.run(polish_reply(facts, "FALLBACK", timeout=0.05))
    finally:
        r()
    c.check("超时 → 退回模板", got == "FALLBACK", repr(got))
    return c.summary("第一批（润色取舍）")


# ── 第二批：承诺词识别本身 ───────────────────────────────────────────────
def test_promise_words():
    title("2. has_promise：命中承诺词、不误伤未来式")
    c = Checker()
    for t in ("已经帮你加好了", "已删除那条", "安排好了", "搞定了", "已经写入日程"):
        c.check(f"「{t}」判为承诺", has_promise(t))
    for t in ("要不要帮你排进日程？", "点确认后我就写进去", "我可以帮你挑几个时段", ""):
        c.check(f"「{t}」不算承诺", not has_promise(t))
    return c.summary("第二批（承诺词识别）")


# ── 第三批：试点 1 · 3d 读待办 ──────────────────────────────────────────
def test_3d_list_polish():
    title("3. 试点 1（3d 读待办）：模板 → 模型润色，列表数据不变")
    c = Checker()
    with sandbox():
        client = make_client()
        from app.store import list_todos
        real = list_todos()

        b0 = _say(client, "看看我的待办", "t20-3d-nokey")
        c.check("没 key → 出现模板化列表", "你目前的待办（共" in b0["answer"], b0["answer"][:36])
        c.check("没 key → 列出的条数与库一致",
                ("共 %d 条" % len(real)) in b0["answer"], f"库 {len(real)} 条")
        c.check("3d 纯读：不出任何卡", not _has_todo_card(b0.get("options")))

        r = _patch_client("【润色】我帮你念一下手头的安排～")
        try:
            b1 = _say(client, "看看我的待办", "t20-3d-key")
        finally:
            r()
        c.check("有 key → 出现模型润色话术",
                b1["answer"] == "【润色】我帮你念一下手头的安排～", b1["answer"][:36])
        c.check("润色后不再出现模板头", "你目前的待办（共" not in b1["answer"])

        # 交给模型的"事实"必须就是库里那份（数据与迁移前一致）
        captured = {}

        async def _spy(facts, fallback, **kw):
            captured.clear()
            captured.update(facts)
            return fallback

        old = _polish.polish_reply
        _polish.polish_reply = _spy
        try:
            _say(client, "看看我的待办", "t20-3d-spy")
        finally:
            _polish.polish_reply = old
        c.check("交给模型的事实条数 == 库中条数",
                captured.get("count") == len(real), str(captured.get("count")))
        c.check("事实里的标题集合 == 库中标题集合",
                {x.get("title") for x in captured.get("todos", [])}
                == {t.get("title") for t in real})
    return c.summary("第三批（3d 润色）")


# ── 第四批：试点 2 · 3c 加待办 ──────────────────────────────────────────
def test_3c_add_polish():
    title("4. 试点 2（3c 加待办）：模板 → 润色，卡字段/写库不变")
    c = Checker()
    with sandbox():
        client = make_client()
        from app.modules.planner import parse_add_todo
        before = parse_add_todo("帮我加个游泳周四19:00")
        keys = ("title", "date", "start", "end")

        r = _patch_client("【润色】要不要把游泳排一下？")
        try:
            b1 = _say(client, "帮我加个游泳周四19:00", "t20-3c-key")
        finally:
            r()
        card = next((o for o in (b1.get("options") or [])
                     if isinstance(o, dict) and o.get("kind") == "todo_add"), None)
        c.check("有 key → 出现模型润色话术",
                b1["answer"] == "【润色】要不要把游泳排一下？", b1["answer"][:36])
        c.check("照旧出 todo_add 卡", card is not None,
                _json.dumps(b1.get("options"), ensure_ascii=False)[:80])
        c.check("卡字段 == 迁移前的确定性解析（逐字段）",
                card is not None and {k: card.get(k) for k in keys}
                == {k: before.get(k) for k in keys},
                _json.dumps({k: (card or {}).get(k) for k in keys}, ensure_ascii=False))
        c.check("照旧等学生点确认", b1.get("awaiting_choice") is True)

        # 没 key → 回退到迁移前的模板（沙箱里就是这条）
        b0 = _say(client, "帮我加个游泳周四19:00", "t20-3c-nokey")
        c.check("没 key → 模板话术（与迁移前一致）",
                "要不要把 **游泳** 排进日程" in b0["answer"], b0["answer"][:36])
    return c.summary("第四批（3c 润色）")


# ── 第五批：承诺词守卫（端到端）+ 问句仍不出卡 ────────────────────────────
def test_promise_guard_and_question():
    title("5. 承诺词守卫（端到端）+ 问句仍不出卡")
    c = Checker()
    with sandbox():
        client = make_client()

        r = _patch_client("好的，已经帮你把游泳加好了！")
        try:
            b = _say(client, "帮我加个游泳周四19:00", "t20-promise")
        finally:
            r()
        c.check("模型说『已经帮你加好了』→ 被拦、退回模板",
                "要不要把 **游泳** 排进日程" in b["answer"], b["answer"][:40])
        c.check("被拦时也没有把话学进样本库（答案里没有承诺词）",
                "已经帮你" not in b["answer"])

        r = _patch_client("【润色】随便回一句")
        try:
            q = _say(client, "我要去哪里取快递", "t20-q")
        finally:
            r()
        c.check("问句 → 不出任何待办卡", not _has_todo_card(q.get("options")),
                _json.dumps(q.get("options"), ensure_ascii=False)[:60])
    return c.summary("第五批（承诺词守卫 + 问句）")


# ── 第六批：离线兜底（没 key 不 500、不白屏）─────────────────────────────
def test_offline_fallback():
    title("6. 离线兜底：没配 key → 不 500、不白屏，功能照旧")
    c = Checker()
    with sandbox():
        client = make_client()
        r1 = client.post("/api/chat", json={"message": "看看我的待办", "module": "planner",
                                            "session_id": "t20-off-1"})
        c.check("离线 3d → HTTP 200", r1.status_code == 200, str(r1.status_code))
        c.check("离线 3d → 有内容、无报错字样",
                bool(r1.json().get("answer")) and "⚠️" not in r1.json()["answer"])
        r2 = client.post("/api/chat", json={"message": "帮我加个游泳周四19:00",
                                            "module": "planner", "session_id": "t20-off-2"})
        c.check("离线 3c → HTTP 200 且照旧出卡",
                r2.status_code == 200 and _has_todo_card(r2.json().get("options")),
                str(r2.status_code))
    return c.summary("第六批（离线兜底）")


# ── 第七批：反向验证（拆掉润色 → 润色话术必须消失）──────────────────────
def test_reverse_verification():
    title("7. 反向验证：拆掉润色 → 润色话术消失、模板回来")
    c = Checker()
    with sandbox():
        client = make_client()

        r = _patch_client("【润色】我念给你听～")
        try:
            b1 = _say(client, "看看我的待办", "t20-rev-1")
        finally:
            r()
        c.check("[对照] 有 key 时确实出现润色话术",
                b1["answer"] == "【润色】我念给你听～", b1["answer"][:30])

        async def _no_polish(facts, fallback, **kw):
            return fallback

        old = _polish.polish_reply
        _polish.polish_reply = _no_polish
        r = _patch_client("【润色】我念给你听～")
        try:
            b2 = _say(client, "看看我的待办", "t20-rev-2")
        finally:
            r()
            _polish.polish_reply = old
        c.check("[反向] 拆掉润色 → 润色话术不再出现",
                b2["answer"] != "【润色】我念给你听～")
        c.check("[反向] 拆掉润色 → 模板话术回来",
                "你目前的待办（共" in b2["answer"], b2["answer"][:30])
    return c.summary("第七批（反向验证）")


if __name__ == "__main__":
    fails = 0
    fails += test_polish_unit()
    fails += test_promise_words()
    fails += test_3d_list_polish()
    fails += test_3c_add_polish()
    fails += test_promise_guard_and_question()
    fails += test_offline_fallback()
    fails += test_reverse_verification()
    sys.exit(1 if fails else 0)
