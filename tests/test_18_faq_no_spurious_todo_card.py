"""第十八批测试：堵死"非排程模块（如校园问答）被乱挂待办确认卡"（收口误判·任务二 / Item B）。

背景
====
2026-09-30 学生报障：在「校园问答」栏问"你是谁"，模型却把"校园问答"当待办名、
用旧提案的时间出了一张确认卡（日期 2026-10-02、时间 15:40-16:40）。

根因：main.py 把 `scheduling_tools()`（含 propose_todo_tool / propose_todo_slots_tool /
propose_todo_mode_tool——三个都能挂待办确认条）**无条件注入所有模块**。
faq 模块本只该做知识库检索，却拿到了出待办卡的工具；模型顺手一调就挂了卡。
任务一的"统一守卫"拦不住这种情形——"校园问答"是个非平凡标题，能通过 `_title_ok`
判真（这就是根因不在守卫、而在工具下发范围）。

修复：工具下发改为**按语境收敛**——只有 planner 模块，或语义层/规则层明确判成
add_todo 时，才把出待办卡的工具交给模型（见 main.py `_schedule_tools_allowed`）。

验证（钉死）
==========
  · 单元：_schedule_tools_allowed 对四类语境给出正确开关；
  · 端到端：faq 栏问"你是谁"——即便模型硬要调 propose_todo_tool，也不出待办卡；
  · 端到端：faq 栏说一句真加待办（"帮我加个游泳周四19:00"），仍正常出卡。

全部跑在数据沙箱里，绝不碰真实演示数据。
"""
import asyncio
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title, sandbox, make_client

from app.agent.intent import INTENT_ADD_TODO
from app.main import _schedule_tools_allowed


# ---------------------------------------------------------------------------
# 自定义"假模型"：语义分类调用（无 tools）回一段意图 JSON；
# 引擎调用（带 tools）第一次回一个"调 propose_todo_tool"的工具调用，
# 之后回一句普通文本收尾。这样能精准复现"模型硬要出待办卡"那一幕。
# ---------------------------------------------------------------------------
class _ToolCaller:
    def __init__(self, sem_reply: str, tool_name: str, tool_args: dict):
        self._sem_reply = sem_reply
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._engine_calls = 0

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        if tools:  # 引擎调用：带工具清单
            self._engine_calls += 1
            if self._engine_calls == 1:
                return {
                    "tool_calls": [{
                        "id": "c1", "type": "function",
                        "function": {
                            "name": self._tool_name,
                            "arguments": _json.dumps(self._tool_args, ensure_ascii=False),
                        },
                    }]
                }
            return {"content": "好的，已为你处理。"}
        # 语义分类 / 路由调用：无 tools
        return {"content": self._sem_reply}


def _patch_llm(sem_reply: str, tool_name: str, tool_args: dict):
    """把 DeepSeek 客户端换成上面那个会"硬调出待办卡"的假模型，返回恢复函数。

    ⚠️ 必须同时打两处（否则引擎仍走真网络，测试变成「看真模型心情」的非确定性）：
      · app.config.get_llm_config        —— classify_with_llm 在函数内 `from app.config
                                             import`，改模块属性看得见，没 key 它直接跳过；
      · app.llm.client.DeepSeekClient     —— 语义/路由/润色/网关都是**函数内** import，
                                            改模块属性看得见。

    2026-10-02 起**不再打** `app.agent.engine.DeepSeekClient`：引擎已改为走网关
    `app/llm/gateway.py`，而网关是**要用时才** import 客户端，所以打上面一处就通。
    （旧的"引擎模块顶层 import、必须单独打一份"那条经验已随网关化失效。）
    """
    import app.config as _cfg
    import app.llm.client as _client
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    _factory = lambda *a, **k: _ToolCaller(sem_reply, tool_name, tool_args)
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


# ---------------------------------------------------------------------------
def test_gate_logic():
    title("1. 单元：_schedule_tools_allowed 按语境开关出卡工具")
    c = Checker()
    # 非排程模块 + 闲聊/问候/问句 → 不给（正是报障那一幕）
    c.check("faq + 『你是谁』→ 不给出卡工具",
            not _schedule_tools_allowed("faq", None, "你是谁"))
    c.check("faq + 『你好』→ 不给出卡工具",
            not _schedule_tools_allowed("faq", None, "你好"))
    c.check("express + 『快递几点到』→ 不给出卡工具",
            not _schedule_tools_allowed("express", None, "快递几点到"))
    # planner 主业就是排日程 → 始终给
    c.check("planner + 任意消息 → 始终给出卡工具",
            _schedule_tools_allowed("planner", None, "你是谁"))
    # 语义层明确判成 add_todo → 给（哪怕人此刻在 faq 栏说要加事）
    c.check("faq + 语义层=add_todo → 给出卡工具",
            _schedule_tools_allowed("faq", INTENT_ADD_TODO, "你是谁"))
    # 规则层认出是加待办（关键词命中）→ 给
    c.check("faq + 规则判加待办(『帮我加个游泳周四19:00』) → 给出卡工具",
            _schedule_tools_allowed("faq", None, "帮我加个游泳周四19:00"))
    return c.summary("第一批（门禁逻辑）")


def test_faq_greeting_no_card():
    title("2. 端到端：faq 栏问『你是谁』不出待办卡（复现报障）")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(
            sem_reply=_json.dumps({"module": "faq", "intent": "chat", "confidence": 0.9}),
            tool_name="propose_todo_tool",
            tool_args={"title": "校园问答", "date": "周三", "start": "15:40", "end": "16:40"},
        )
        try:
            resp = client.post("/api/chat", json={"message": "你是谁", "module": "faq"})
            body = resp.json()
        finally:
            restore()

        opts = body.get("options") or []
        c.check("faq『你是谁』响应里没有任何待办卡", not _has_todo_card(opts),
                _json.dumps(opts, ensure_ascii=False)[:80])
        c.check("faq『你是谁』不需要学生去确认卡片",
                not body.get("awaiting_choice", False))
    return c.summary("第二批（faq 问候不出卡）")


def test_faq_add_todo_still_cards():
    title("3. 端到端：faq 栏说真加待办仍正常出卡（不误伤原有能力）")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(
            sem_reply=_json.dumps({"module": "planner", "intent": "add_todo",
                                    "confidence": 0.9}),
            tool_name="propose_todo_tool",
            tool_args={"title": "游泳", "date": "周四", "start": "19:00", "end": "20:00"},
        )
        try:
            resp = client.post(
                "/api/chat",
                json={"message": "帮我加个游泳周四19:00", "module": "faq"},
            )
            body = resp.json()
        finally:
            restore()

        opts = body.get("options") or []
        card = next((o for o in opts if isinstance(o, dict)
                     and o.get("kind") == "todo_add"), None)
        c.check("faq 栏真加待办 → 仍出 todo_add 卡", card is not None,
                _json.dumps(opts, ensure_ascii=False)[:80])
        if card:
            c.check("卡片事名是『游泳』（不是半句话）", card.get("title") == "游泳",
                    card.get("title"))
            c.check("卡片时间对（19:00-20:00）",
                    card.get("start") == "19:00" and card.get("end") == "20:00",
                    f"{card.get('start')}-{card.get('end')}")
    return c.summary("第三批（faq 真加待办仍出卡）")


if __name__ == "__main__":
    fails = 0
    fails += test_gate_logic()
    fails += test_faq_greeting_no_card()
    fails += test_faq_add_todo_still_cards()
    sys.exit(1 if fails else 0)
