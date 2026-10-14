"""第二十一批测试：闲聊出口与人设红线（"闲聊兜不住，聊日常会被转到待办"）。

背景
====
学生报障：说累、说热、说饿这类**日常闲聊**，系统不接，反而往待办上带。两个实证：
  · 「记一下今天很开心」→ `_ADD_INTENT` 里的"记一下"命中，`_pick_title` 抠出「很开心」，
    `looks_like_thing('很开心')` 判真通过 → 一张写着「要不要把 **很开心** 排进日程？」
    的卡挂到了他眼前。他要的是吐槽，不是排事。
  · 「我饿了」规则层是干净的（`wants_add_todo` 返回 False），可**在线语义层把它判成了
    add_todo**，照样拉出一张待办卡 —— 模型把"状态"读成了"指令"。
另外：共享排程工具（propose_todo_tool 等）是注入**所有**模块的，尾部模型手上正好有
"出待办卡"的工具，学生一闲聊，它顺手就来一句"要不要我帮你安排个…？"——闲聊变成推销。

根治
====
1. 人设层：所有学生端模块的系统提示统一拼上「闲聊守则」——闲聊自然回一两句、
   绝不主动提议排时间、绝不调排程工具、不用"要不要我帮你安排个…？"收尾推销。
2. 语义层提示词：`chat` 的定义下补日常例子（好累啊/今天好热/中午吃什么/我饿了/我困了）
   + 两条红线（感叹、心情、生理状态不是 add_todo；学生说饿/困是在表达状态，
   没有他的话不许替他排）。
3. **确定性否决**（保险丝）：`intent.physio_state_only` —— 纯生理状态感叹
   （生理词 + 无行动词 + 没报钟点 + 短句）→ 强制按 chat 走，哪怕语义层判了 add_todo；
   同时"一个排程工具都不给"。
4. 标题判真：`planner.looks_like_chitchat` —— 标题主体摘掉心情/感叹词后一个字不剩
   → 不是一件事（「很开心」「好累」「肚子好饿」），走既有追问。

双向验证（防误伤真待办）
====
  · `我饿了`/`肚子好饿` → 不出卡；`提醒我晚饭前买饭`/`帮我安排晚饭 18:00` → 仍出卡。
  · `记一下今天很开心` → 不出卡；`记一下买酱油` → 仍出卡。
  · `wants_add_todo('我想去吃火锅')` 保持 True（设计内，本轮不动）。

全部跑在数据沙箱里，不碰真实演示数据。
"""
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title, sandbox, make_client

from app.agent.intent import INTENT_ADD_TODO, physio_state_only
from app.main import CHITCHAT_RULES, _schedule_tools_allowed
from app.modules.planner import (
    _pick_title, _title_ok, looks_like_chitchat, parse_add_todo, wants_add_todo,
)


# ---------------------------------------------------------------------------
# 假模型：语义分类调用（无 tools）回一段意图 JSON；引擎调用（带 tools）回一句普通文本。
# 这样能精准复现"语义层硬判 add_todo"+"模型手上真有排程工具"那一幕。
# 打三处的原因见 test_18/test_19：函数内 import 的看得见模块属性，
# 引擎在模块顶层 import，必须单独把 `app.agent.engine.DeepSeekClient` 也换掉。
# ---------------------------------------------------------------------------
class _FakeLLM:
    def __init__(self, sem_reply: str, engine_reply: str = "嗯，知道了。"):
        self._sem_reply = sem_reply
        self._engine_reply = engine_reply
        self.engine_calls = 0

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        if tools:                       # 引擎调用（带工具清单）
            self.engine_calls += 1
            return {"content": self._engine_reply}
        return {"content": self._sem_reply}   # 语义分类 / 润色调用（无工具）


class _EngineSpy:
    """替掉 AgentEngine：记下系统提示，回一句固定文本（不真联网）。"""
    last_prompt = None

    def __init__(self, system_prompt=None, tools=None, session_id=None,
                 llm_override=None, **kw):
        _EngineSpy.last_prompt = system_prompt
        self._tools = tools or {}

    async def run(self, message, history=None):
        return {"answer": "嗯，知道了。", "trace": [], "options": []}


def _patch_llm(sem_reply: str, engine_reply: str = "嗯，知道了。"):
    import app.config as _cfg
    import app.llm.client as _client
    # 只打 `app.llm.client` 一处即可（2026-10-02 网关化之后）：
    # 语义层 / 润色层 / 引擎都改走网关，网关是**要用时才** import 客户端，
    # 所以模块属性补丁传得进去。以前"引擎那份是顶层 import 的副本、得单独打一份"
    # 不再是事实，`_engine.DeepSeekClient` 这个属性已经不存在了（打了会 AttributeError）。
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    fake = _FakeLLM(sem_reply, engine_reply)
    _factory = lambda *a, **k: fake
    _client.DeepSeekClient = _factory
    _cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                           "model": "m"}

    def _restore():
        _client.DeepSeekClient = old_client
        _cfg.get_llm_config = old_cfg
    return _restore


def _has_todo_card(opts):
    return any(isinstance(o, dict) and o.get("kind") in ("todo_add", "todo_mode", "todo_slots")
               for o in (opts or []))


# ---------------------------------------------------------------------------
def test_physio_guard_unit():
    title("1. 单元：physio_state_only —— 纯生理状态感叹（双向）")
    c = Checker()
    # 是状态句：生理词 + 无行动词 + 没报钟点 + 短句
    for s in ("我饿了", "肚子好饿", "好累啊", "我困了", "我渴了", "我好撑",
              "有点饿了", "我有点困", "今天好累啊", "累死了", "饿了", "我累了"):
        c.check(f"「{s}」→ 判为状态句（否决）", physio_state_only(s))
    # 不是状态句：有行动词/事名/明确时间 → 照常走老路
    for s in ("提醒我晚饭前买饭", "帮我安排晚饭 18:00", "我想去吃火锅",
              "我饿了想吃饭", "记一下今天很开心", "我要去健身", "好困想睡",
              "我饿了 18:00"):
        c.check(f"「{s}」→ 不否决（照常走老路）", not physio_state_only(s))
    c.check("长句不算状态句（>15 字）",
            not physio_state_only("我今天真的好累好累好累啊什么都不想干只想躺着"))
    c.check("空串不否决", not physio_state_only(""))
    return c.summary("第一批（生理状态判据）")


def test_chitchat_title_unit():
    title("2. 单元：闲聊黑名单 —— 标题主体是心情/感叹就不是一件事")
    c = Checker()
    for t in ("很开心", "好累", "好热", "我饿", "肚子好饿", "无聊死了",
              "烦", "emo了", "好爽"):
        c.check(f"「{t}」→ 判为闲聊", looks_like_chitchat(t))
    for t in ("游泳", "买酱油", "三体", "冷饮", "热饮", "复习线性代数",
              "作业12", "开会", "健身", "吃自助餐"):
        c.check(f"「{t}」→ 不是闲聊（真事名保留）", not looks_like_chitchat(t))
    # 借道 _title_ok：闲聊词过不了判真，真事名照过
    c.check("_title_ok('很开心') → False", not _title_ok("很开心"))
    c.check("_title_ok('好累') → False", not _title_ok("好累"))
    c.check("_title_ok('买酱油') → True", _title_ok("买酱油"))
    c.check("_title_ok('复习线性代数') → True", _title_ok("复习线性代数"))
    return c.summary("第二批（闲聊标题判假）")


def test_pick_title_and_proposal_bidirectional():
    title("3. 单元：抠标题/出提案 —— 记一下今天很开心 vs 记一下买酱油")
    c = Checker()
    c.check("「记一下今天很开心」抠不出事名（待办）",
            _pick_title("记一下今天很开心") == "待办",
            repr(_pick_title("记一下今天很开心")))
    c.check("「记一下今天很开心」出不了提案", parse_add_todo("记一下今天很开心") is None)
    p = parse_add_todo("记一下买酱油明天下午三点")
    c.check("「记一下买酱油明天下午三点」仍出提案", p is not None)
    if p:
        c.check("真待办事名是『买酱油』", p.get("title") == "买酱油", p.get("title"))
        c.check("真待办时间对（15:00 起）", p.get("start") == "15:00", p.get("start"))
    c.check("「我想去吃火锅」仍判为下单（设计内，勿动）",
            wants_add_todo("我想去吃火锅"))
    return c.summary("第三批（提案双向）")


def test_persona_rules_in_all_modules():
    title("4. 人设：所有学生端模块的系统提示都带上「闲聊守则」")
    c = Checker()
    # 守则文本本身：四条红线一条不少
    c.check("守则含『绝不调用 propose_todo_tool』",
            "propose_todo_tool" in CHITCHAT_RULES)
    c.check("守则含『绝不主动提议』推销红线", "绝不" in CHITCHAT_RULES)
    c.check("守则含『要不要我帮你安排个…？』反例", "要不要我帮你安排个" in CHITCHAT_RULES)
    c.check("守则要求一两句、不许写小作文", "一两句" in CHITCHAT_RULES)

    with sandbox():
        client = make_client()
        import app.main as _m
        old_engine = _m.AgentEngine
        _m.AgentEngine = _EngineSpy
        restore = _patch_llm(_json.dumps({"module": "faq", "intent": "chat",
                                          "confidence": 0.9}))
        try:
            for mod in ("faq", "express", "planner"):
                _EngineSpy.last_prompt = None
                client.post("/api/chat", json={"message": "我好累啊", "module": mod})
                got = _EngineSpy.last_prompt or ""
                c.check(f"{mod} 模块的系统提示带上闲聊守则",
                        "闲聊守则" in got, got[-60:].replace("\n", " "))
        finally:
            restore()
            _m.AgentEngine = old_engine
    return c.summary("第四批（人设拼装）")


def test_sem_add_todo_vetoed_for_physio():
    title("5. 端到端：语义层硬判 add_todo 的「我饿了」被否决（不出卡）")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(
            _json.dumps({"module": "planner", "intent": "add_todo",
                         "title": "饿", "confidence": 0.95}),
            engine_reply="饿了就先去吃点东西吧，别硬撑。",
        )
        try:
            resp = client.post("/api/chat", json={"message": "我饿了"})
            body = resp.json()
        finally:
            restore()
        opts = body.get("options") or []
        c.check("「我饿了」响应里没有任何待办卡", not _has_todo_card(opts),
                _json.dumps(opts, ensure_ascii=False)[:80])
        c.check("不需要学生去确认卡片", not body.get("awaiting_choice", False))
        c.check("被归到 faq（不是 planner）", body.get("module") == "faq",
                str(body.get("module")))
        c.check("回复里没有待办推销", "要不要" not in body.get("answer", ""),
                body.get("answer", "")[:40])
        trace = body.get("trace") or []
        c.check("思考轨迹第一条说『听成问事』",
                bool(trace) and "问事" in (trace[0].get("phase") or ""),
                _json.dumps(trace[:1], ensure_ascii=False)[:80])
    return c.summary("第五批（生理状态否决）")


def test_schedule_tools_gate():
    title("6. 端到端：状态句一个排程工具都不给（闲聊推销的入口堵死）")
    c = Checker()
    c.check("planner + 『我饿了』→ 不给出卡工具",
            not _schedule_tools_allowed("planner", None, "我饿了"))
    c.check("faq + 语义层=add_todo + 『我饿了』→ 仍不给",
            not _schedule_tools_allowed("faq", INTENT_ADD_TODO, "我饿了"))
    c.check("planner + 『你是谁』→ 照旧给出卡工具（不误伤）",
            _schedule_tools_allowed("planner", None, "你是谁"))
    c.check("faq + 『帮我加个游泳周四19:00』→ 照旧给（不误伤）",
            _schedule_tools_allowed("faq", None, "帮我加个游泳周四19:00"))
    return c.summary("第六批（工具门禁）")


def test_faq_chitchat_no_pitch():
    title("7. 端到端：faq 栏闲聊 —— 不出卡、不推销")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(
            _json.dumps({"module": "faq", "intent": "chat", "confidence": 0.9}),
            engine_reply="嗯，这天儿是挺热的，多喝点水。",
        )
        try:
            resp = client.post("/api/chat", json={"message": "今天好热", "module": "faq"})
            body = resp.json()
        finally:
            restore()
        opts = body.get("options") or []
        ans = body.get("answer", "")
        c.check("『今天好热』不出待办卡", not _has_todo_card(opts), ans[:40])
        c.check("不需要确认卡片", not body.get("awaiting_choice", False))
        c.check("回复里没有『要不要我帮你安排』这类推销",
                "要不要我帮你安排" not in ans and "要不要" not in ans, ans[:40])
        c.check("闲聊有回话（不是空答）", bool(ans.strip()), ans[:30])
    return c.summary("第七批（faq 闲聊不推销）")


def test_still_cards_real_todo():
    title("8. 端到端：真加待办仍正常出卡（不误伤）")
    c = Checker()
    with sandbox():
        client = make_client()
        restore = _patch_llm(_json.dumps({"module": "planner", "intent": "add_todo",
                                          "title": "游泳", "confidence": 0.9}))
        try:
            resp = client.post("/api/chat",
                               json={"message": "帮我加个游泳周四19:00", "module": "planner"})
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
    return c.summary("第八批（真待办仍出卡）")


def test_asking_add_exemption():
    title("9. 端到端：追问中的回答不被拦（不在追问里则照拦）")
    c = Checker()
    with sandbox():
        from app.store import append_conversation
        client = make_client()
        restore = _patch_llm(_json.dumps({"module": "planner", "intent": "add_todo",
                                          "title": "饿", "confidence": 0.95}))
        try:
            # a) 没有追问上下文 → 状态句被拦，路由落到 faq
            b1 = client.post("/api/chat", json={"message": "我饿了"}).json()
            c.check("无追问上下文 → 归 faq（被拦）", b1.get("module") == "faq",
                    str(b1.get("module")))
            # b) 上一轮是"要安排什么事"的追问 → 这句是在**回答**，不拦
            sid = "test21-exempt"
            append_conversation(sid, "user", "帮我加个东西")
            append_conversation(sid, "assistant", "加待办缺细节")
            b2 = client.post("/api/chat",
                             json={"message": "我饿了", "session_id": sid}).json()
            c.check("追问中的回答不被拦（仍归 planner）", b2.get("module") == "planner",
                    str(b2.get("module")))
        finally:
            restore()
    return c.summary("第九批（追问例外）")


def test_reverse_summary():
    title("10. 反向验证：本轮改动没有误伤既有能力")
    c = Checker()
    for s in ("帮我加个游泳周四19:00", "我要去健身", "复习线性代数 周四下午三点",
              "帮我安排下周的数学复习", "记一下交电费 明天下午三点"):
        c.check(f"「{s}」仍被规则认作下单", wants_add_todo(s) or _title_ok(_pick_title(s)),
                f"wants_add_todo={wants_add_todo(s)}")
    c.check("时间碎片守卫仍在（『明天中午12』抠不出事名）",
            _pick_title("明天中午12") == "待办")
    c.check("『大概一个小时』抠不出事名", _pick_title("大概一个小时") == "待办")
    return c.summary("第十批（反向验证）")


if __name__ == "__main__":
    fails = 0
    fails += test_physio_guard_unit()
    fails += test_chitchat_title_unit()
    fails += test_pick_title_and_proposal_bidirectional()
    fails += test_persona_rules_in_all_modules()
    fails += test_sem_add_todo_vetoed_for_physio()
    fails += test_schedule_tools_gate()
    fails += test_faq_chitchat_no_pitch()
    fails += test_still_cards_real_todo()
    fails += test_asking_add_exemption()
    fails += test_reverse_summary()
    sys.exit(1 if fails else 0)
