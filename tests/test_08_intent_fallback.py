"""第八批测试：意图兜底层（样本库 + 语义层），Req G。

起因是学生那句报障：「**很多的字都是接不住的，你现在能接住的都是我测试给的**」。
此前每报一次就往词表里加一条，`planner.py` 堆到 30 多张表、`router._QUICK_MAP`
50 多个硬编码词，全是 `if keyword in text` 的子串匹配——词表加多长都收敛不了。

`app/agent/intent.py` 换了个思路：**把「听懂人话」和「动手执行」切开**。
判定层（规则 → 样本库 → LLM 只读意图）怎么换都行，
执行层（查空档 / 排期 / 出卡 / 写库）**系统独占，模型永远碰不到**。

这一批钉死四件事：
  ① 样本库：模糊匹配认得出同义改写、认不出无关句、太短的不乱配、能自增长；
  ② `to_canonical`：把语义层读出的字段翻成解析器认得的规范话；
  ③ 语义层：只认白名单意图、把握低的不要、没 key 不生效、**拿不到写库接口**；
  ④ 端到端：规则接不住的口语 → 出卡；提问句不许被误判成下单。

全部跑在数据沙箱里，绝不碰你的真实演示数据。
"""
import asyncio
import json as _json
import os
import time

from _harness import Checker, sandbox, make_client, data_path, title


def _seed_timetable():
    """往沙箱写一份示例周表（没课表就查不到空档，出不了候选卡）。"""
    import app.store as _store
    _store.DATA_DIR = os.environ.get("CAMPUSTIME_DATA_DIR") or _store.DATA_DIR
    sample = [{"day": d, "start": "08:00", "end": "09:40",
               "course": f"课程{d}", "location": "教学楼"} for d in range(1, 6)]
    _store.save_timetable(sample)


class _FakeLLM:
    """假的模型客户端（人话：照着剧本回一句 JSON，不真联网）。"""

    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, messages, tools=None, tool_choice="auto"):
        return {"content": self._reply}


def _patch_llm(reply: str, with_key: bool = True):
    """把大模型客户端换成打桩的，返回恢复函数。"""
    import app.config as _cfg
    import app.llm.client as _client
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    _client.DeepSeekClient = lambda *a, **k: _FakeLLM(reply)
    if with_key:
        _cfg.get_llm_config = lambda: {"api_key": "test-key", "base_url": "http://x",
                                       "model": "m"}
    else:
        _cfg.get_llm_config = lambda: {"api_key": "", "base_url": "http://x", "model": "m"}

    def _restore():
        _client.DeepSeekClient = old_client
        _cfg.get_llm_config = old_cfg
    return _restore


# ── ① 样本库（离线，零成本那一档）──────────────────────────────────────────
def test_sample_store():
    title("1. 样本库：模糊匹配 + 自增长")
    c = Checker()
    from app.agent import intent as it

    with sandbox():
        # 同义改写要能认出来，无关句必须认不出来——这是阈值存在的全部意义
        same = it._dice("我要去吃火锅", "我想去吃火锅")
        diff = it._dice("我要去吃火锅", "图书馆几点关门")
        c.check("同义改写相似度高", same >= it._SIM_THRESHOLD, f"{same:.2f}")
        c.check("无关句相似度低", diff < it._SIM_THRESHOLD, f"{diff:.2f}")

        hit = it.match_sample("我要去吃火锅")
        c.check("内置种子里认得出「我要去吃火锅」", bool(hit) and hit.get("intent") == "add_todo",
                _json.dumps(hit, ensure_ascii=False)[:80] if hit else "None")
        c.check("命中来源标了 sample（排查时看得出没花钱调 API）",
                bool(hit) and hit.get("source") == "sample")

        c.check("无关的提问句不命中",
                it.match_sample("图书馆几点关门") is None)
        # 太短的话不做模糊匹配：字少时 Dice 会虚高，一字之差意思全变
        c.check("太短的话不乱配（「加个」）", it.match_sample("加个") is None)

        # —— 自增长：系统办成一句就学一句 ——
        # ⚠️ 用一条**独一无二**的句子：样本库是运行时累积的（真机上早就学过别的），
        #    拿"下礼拜得把论文交了"这种常见句子测"条数 +1"，第二次跑就会撞上已存在的
        #    样本变成 +0（本机实测踩过）。长度断言必须用别人没说过的句子。
        unseen = "帮我把自行车链条上点油安排一下"[:14] + str(time.time())[-6:]
        before = len(it.load_samples())
        it.remember(unseen, "add_todo", {"title": "上链条油"})
        after = len(it.load_samples())
        c.check("记住一句之后样本库变长了", after == before + 1, f"{before} → {after}")
        got = it.match_sample(unseen[:-1])
        c.check("学过的那句换个说法还认得出来",
                bool(got) and got.get("intent") == "add_todo",
                _json.dumps(got, ensure_ascii=False)[:80] if got else "None")

        it.remember(unseen, "add_todo", {"title": "上链条油"})
        c.check("同一句不重复记（只加命中次数）", len(it.load_samples()) == after)
        # chat 不该被记住：那是"没听懂"，学进去只会污染样本库
        it.remember("今天天气不错", "chat")
        c.check("「没听懂」的 chat 不记进样本库",
                not any(s.get("intent") == "chat" for s in it.load_samples()))
    return c.summary("第一批（样本库）")


# ── ② 规范话翻译 ──────────────────────────────────────────────────────────
def test_canonical():
    title("2. to_canonical：口语 → 解析器认得的规范话")
    c = Checker()
    from app.agent import intent as it

    got = it.to_canonical("我要平时去吃火锅，76分钟",
                          {"intent": "add_todo", "title": "吃火锅", "minutes": 76,
                           "day_hint": "工作日"})
    c.check("拼出了解析器认得的写法", got == "加个吃火锅，76分钟，工作日", got)

    got2 = it.to_canonical("随便说一句", {"intent": "add_todo", "title": "",
                                        "minutes": 0, "day_hint": ""})
    c.check("啥字段都没读到 → 原样返回（不硬凑）", got2 == "随便说一句", got2)

    got3 = it.to_canonical("原话", None)
    c.check("没语义结果 → 原样返回", got3 == "原话", got3)
    return c.summary("第二批（规范话翻译）")


# ── ③ 语义层：只读意图，拿不到写库接口 ─────────────────────────────────────
def test_llm_layer():
    title("3. 语义层：只认白名单、把握低的不要、没 key 不生效")
    c = Checker()
    from app.agent import intent as it

    ok = '{"intent":"add_todo","title":"吃火锅","minutes":76,"day_hint":"工作日","confidence":0.9}'

    with sandbox():
        # 正常一档：读得出意图和字段
        restore = _patch_llm(ok)
        try:
            got = asyncio.run(it.classify_with_llm("我要平时去吃火锅，76分钟"))
        finally:
            restore()
        c.check("读出了 add_todo", bool(got) and got.get("intent") == "add_todo",
                _json.dumps(got, ensure_ascii=False)[:90] if got else "None")
        c.check("时长也读出来了（76 分钟）", bool(got) and got.get("minutes") == 76)
        c.check("来源标了 llm", bool(got) and got.get("source") == "llm")

        # 模型自造了一个意图 → 不认（那是执行权的口子）
        restore = _patch_llm('{"intent":"delete_everything","confidence":0.99}')
        try:
            bad = asyncio.run(it.classify_with_llm("把什么都删了"))
        finally:
            restore()
        c.check("模型自造的意图一律不认（不让它发明新动作）", bad is None)

        # 它自己都没把握 → 别替学生做主
        restore = _patch_llm('{"intent":"add_todo","title":"吃火锅","confidence":0.3}')
        try:
            low = asyncio.run(it.classify_with_llm("大概也许想吃点什么"))
        finally:
            restore()
        c.check("把握太低（0.3）→ 不要", low is None)

        # 没配 key → 这一层不生效，但绝不能报错
        restore = _patch_llm(ok, with_key=False)
        try:
            nokey = asyncio.run(it.classify_with_llm("我要去吃火锅"))
        finally:
            restore()
        c.check("没配 key → 安静地返回 None（掉回样本库/模型）", nokey is None)

        # 输出不是 JSON（模型自由发挥了一段话）→ 不认
        restore = _patch_llm("好的，已经帮你排好啦！")
        try:
            junk = asyncio.run(it.classify_with_llm("我要去吃火锅"))
        finally:
            restore()
        c.check("模型没按格式回 → 不认（掉回老路）", junk is None)

        # 标题判真：模型把整句抄回来当标题，不许印到卡片上
        restore = _patch_llm('{"intent":"add_todo","title":"帮我安排一下那个事情","confidence":0.9}')
        try:
            tt = asyncio.run(it.classify_with_llm("帮我安排一下那个事情"))
        finally:
            restore()
        c.check("标题不像一件事 → 标题留空（不拿半句话当代办名）",
                bool(tt) and not tt.get("title"), _json.dumps(tt, ensure_ascii=False)[:90])
    return c.summary("第三批（语义层）")


# ── ④ 端到端：规则接不住的口语要能出卡，提问句不许被误判 ───────────────────
def test_end_to_end():
    title("4. 端到端：规则接不住 → 语义层接住 → 出卡（不误伤提问）")
    c = Checker()
    from app.agent import intent as it
    from app.main import _rule_intent

    # 这句口语里没有「加 / 安排 / 记一下 / 我要」任何一个现有规则认的字样，
    # 正是"词表永远补不全"的那一类。
    oral = "下礼拜得把论文交了"
    with sandbox():
        c.check("先确认这句老规则确实接不住（否则测的不是语义层）",
                _rule_intent(oral) is None)

        client = make_client()
        _seed_timetable()
        reply = ('{"intent":"add_todo","title":"交论文","minutes":60,'
                 '"day_hint":"","confidence":0.92}')
        restore = _patch_llm(reply)
        try:
            r = client.post("/api/chat", json={
                "message": oral, "module": "planner", "session_id": "sem1"})
        finally:
            restore()
        d = r.json()
        opts = d.get("options") or []
        kinds = [o.get("kind") for o in opts]
        c.check("规则接不住的口语 → 还是出了卡", bool(opts), str(kinds))
        c.check("出的是候选时段卡（让他自己勾，不是替他定）",
                "todo_slots" in kinds, str(kinds))
        card = next((o for o in opts if o.get("kind") == "todo_slots"), {})
        c.check("卡片上的事名是「交论文」（不是那半句原话）",
                card.get("title") == "交论文", card.get("title"))
        c.check("awaiting_choice 置上了", d.get("awaiting_choice") is True)
        c.check("这句被学进样本库了（下次不用再问一次 API）",
                bool(it.match_sample(oral)) or any(
                    s.get("text") == oral for s in it.load_samples()))

        # —— 不误伤：提问句不许被判成下单 ——
        ask = "我想问一下图书馆几点关门"
        restore = _patch_llm('{"intent":"chat","title":"","confidence":0.95}')
        try:
            r2 = client.post("/api/chat", json={
                "message": ask, "module": "planner", "session_id": "sem2"})
        finally:
            restore()
        c.check("提问句 → 一张卡都不出（不许替他办错事）",
                not (r2.json().get("options") or []),
                _json.dumps(r2.json().get("options"), ensure_ascii=False)[:80])
    return c.summary("第四批（端到端）")


# ── ⑤ 路由：关键词没命中时，先查样本库再让模型联想 ─────────────────────────
def test_router_sample_fallback():
    title("5. 路由：关键词落空时先查样本库（「卡片呢」那一幕的病根）")
    c = Checker()
    from app.agent import intent as it
    from app.agent.router import Router, _QUICK_MAP

    with sandbox():
        c.check("确认「卡片呢」不在关键词表里（所以它以前只能掉给模型联想）",
                not any(k in "卡片呢" for k in _QUICK_MAP))
        # 学一句"待办类"的说法，再让路由分一句它的改写
        it.remember("我都有啥待办来着", "list_todo")
        got = asyncio.run(Router().route("我都有啥待办来"))
        c.check("样本库命中 → 直接回 planner（不用劳烦模型）", got == "planner", got)
    return c.summary("第五批（路由兜底）")


# ── ⑥ 复核：规则被多义词骗了 → 语义层复核一次，以它为准 ───────────────────
def test_rule_review():
    title("6. 规则被多义词骗了 → 语义层复核（不是再补一条规则）")
    c = Checker()
    from app.main import _ASKING_RE, _rule_intent

    with sandbox():
        # 「安排」既是名词（日程安排）又是动词（帮我安排），而 _ADD_INTENT 里就有
        # "安排" 两个字——子串匹配分不清，于是「我都有啥安排」被判成下单，
        # 卡片上会写着「我现都有啥」。这是关键词表的天生盲区，补词补不好。
        c.check("先确认规则确实判错了（否则测的就不是复核）",
                _rule_intent("我现在都有啥安排") == "add_todo")
        c.check("这句话带查询词 → 会被标记复核",
                bool(_ASKING_RE.search("我现在都有啥安排")))
        c.check("常规下单句不触发复核（不该白白多问一次 API）",
                not _ASKING_RE.search("帮我安排个健身"))

        client = make_client()
        _seed_timetable()

        # 语义层说：这是查列表 → 否决规则
        restore = _patch_llm('{"intent":"list_todo","title":"","confidence":0.95}')
        try:
            r = client.post("/api/chat", json={
                "message": "我现在都有啥安排", "module": "planner",
                "session_id": "rev1"})
        finally:
            restore()
        d = r.json()
        c.check("复核后**不出**加待办的卡（不再写「我现都有啥」）",
                not (d.get("options") or []),
                _json.dumps(d.get("options"), ensure_ascii=False)[:70])
        c.check("改成念待办列表（他本来就是想看一眼）",
                "待办" in (d.get("answer") or ""), (d.get("answer") or "")[:40])

        # 反过来：带查询词、但语义层**同意**是下单 → 不否决，照旧出卡
        restore = _patch_llm('{"intent":"add_todo","title":"健身","minutes":60,'
                             '"confidence":0.9}')
        try:
            r2 = client.post("/api/chat", json={
                "message": "看看能不能安排个健身", "module": "planner",
                "session_id": "rev2"})
        finally:
            restore()
        o2 = r2.json().get("options") or []
        c.check("语义层跟规则判得一样 → 不否决，照旧出卡",
                any(x.get("kind") == "todo_slots" for x in o2),
                [x.get("kind") for x in o2])
    return c.summary("第六批（规则复核）")


if __name__ == "__main__":
    fails = 0
    fails += test_sample_store()
    fails += test_canonical()
    fails += test_llm_layer()
    fails += test_end_to_end()
    fails += test_router_sample_fallback()
    fails += test_rule_review()
    import sys
    sys.exit(1 if fails else 0)
