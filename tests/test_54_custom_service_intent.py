"""#22 单子 4：低代码服务接进判定层 —— 学生一句「我丢了饭卡」走到确认卡，点了才落库。

这个文件分两大段，各自三层：

**A 段（判定层）** —— 新加的一档最难的不是"能不能命中"，而是**有没有把既有顺序挤坏**。
判定层既有顺序是 样本库 → 语义层 → 意图先行 → 关键词兜底，触发词那一档插在
"意图先行之后、通用关键词表之前"，挪一个位置就等于换了一档判据强度。三层钉：
  1) **认得出**：没密钥、没样本，光靠服务自己写的触发词也要归到那项服务；
  2) **抢不走**：内置那项（工单链路）和既有 faq/express/planner 归属一个都不许动；
  3) **不作数就丢**：语义层给的服务 id 必须回查 `services.json`，编的整条丢弃
     —— 只校验前缀 `custom:` 等于没校验。

**B 段（出卡与落库）** —— 这段守的是本项目地基那条红线：
  1) 模型手里**只有一个"出卡"动作**（propose_custom_record），一个写入都没有；
  2) **没点确认 = 台账里一个字都没有**；点了才由系统写 `custom/<id>.json`；
  3) 凭空 POST 一个 service_id + fields **登记不进去**（暂存里那张卡才是授权凭据），
     点第二次不许写第二条（幂等）。
"""
import json
import os
import pathlib

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

LOST = {"name": "失物招领", "triggers": ["我丢了饭卡", "捡到东西交哪", "丢了校园卡"],
        "collect_fields": ["物品描述", "丢失地点", "大概时间"],
        "reply_template": "已登记，找到后会通知你来驿站领。", "table": "lostfound"}
REPAIR = {"name": "宿舍报修", "triggers": ["空调坏了", "灯不亮"],
          "collect_fields": ["宿舍楼", "坏了什么"],
          "reply_template": "已登记报修，维修工会联系你。", "table": "repair"}


def _seed():
    """往沙箱放两项服务（id 写死，断言里要逐字比对）。"""
    from app import store
    store.add_service(dict(LOST, id="lost1", created_at="测试"))
    store.add_service(dict(REPAIR, id="rep1", created_at="测试"))


async def _route(text, **kw):
    from app.agent.router import Router
    return await Router().route(text, **kw)


def _patch_model(payload):
    """把模型换成"照剧本回一个 JSON"的假货（判定层两条都打它：分类与丢弃都要能复现）。"""
    import app.config as cfg
    import app.llm.client as C

    raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

    class _M:
        last_usage = None
        last_model = "m"

        async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                       max_tokens=None):
            return {"content": raw}

    old = (C.DeepSeekClient, cfg.get_llm_config)
    C.DeepSeekClient = lambda *a, **k: _M()
    cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                          "model": "m"}

    def restore():
        C.DeepSeekClient, cfg.get_llm_config = old
    return restore


class _ToolCaller:
    """会"硬调工具"的假模型：语义/路由那一跳回意图 JSON，引擎那一跳回一次工具调用。

    形状抄 `tests/test_18_faq_no_spurious_todo_card.py` 里那个（本项目已有三份，
    说明它该挪进 `_harness`——那是另一张单子的事，这里先按现成的写法来）。
    """

    def __init__(self, sem_reply: str, tool_name: str, tool_args: dict):
        self._sem_reply = sem_reply
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._engine_calls = 0
        self.seen_tools = []          # 引擎每次拿到的工具清单（用来断言"没给写入工具"）

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        if tools:
            self._engine_calls += 1
            # 引擎交给客户端的是 OpenAI 形状：{"type":"function","function":{"name":...}}
            # 只取顶层 name 会一律拿到 None，那条"没有写入动作"的断言就变成空转 —— 两种形状都认。
            def _n(t):
                if isinstance(t, dict):
                    fn = t.get("function")
                    if isinstance(fn, dict) and fn.get("name"):
                        return fn["name"]
                    return t.get("name")
                return getattr(t, "name", None) or str(t)
            self.seen_tools.append([_n(t) for t in tools])
            if self._engine_calls == 1:
                return {"tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": self._tool_name,
                                 "arguments": json.dumps(self._tool_args, ensure_ascii=False)},
                }]}
            return {"content": "确认条挂在下面了，点一下才登记。"}
        return {"content": self._sem_reply}


def _patch_tool_model(sem_reply: str, tool_name: str, tool_args: dict):
    """两处一起打（config + client），否则引擎照旧打真网络（指南红线：打一处会静默失效）。"""
    import app.config as cfg
    import app.llm.client as C

    fake = _ToolCaller(sem_reply, tool_name, tool_args)
    old = (C.DeepSeekClient, cfg.get_llm_config)
    C.DeepSeekClient = lambda *a, **k: fake
    cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                          "model": "m"}

    def restore():
        C.DeepSeekClient, cfg.get_llm_config = old
    restore.fake = fake
    return restore


def batch_a_recognized():
    c = Checker()
    title("A1 · 认得出：没有密钥、没有样本，光靠服务自己写的触发词也要归对")
    import asyncio

    with sandbox():
        _seed()
        got = asyncio.run(_route("我丢了饭卡"))
        c.check("「我丢了饭卡」→ custom:lost1", got == "custom:lost1", str(got))
        got2 = asyncio.run(_route("空调坏了"))
        c.check("「空调坏了」→ 第二项服务 custom:rep1（不是只认第一项）",
                got2 == "custom:rep1", str(got2))
        # 最长的那个触发词算赢：否则一句长话会被短词随机抢走
        from app import store
        store.add_service({"name": "失物细节", "triggers": ["丢了", "我丢了饭卡啊"],
                           "collect_fields": ["物品", "地点"],
                           "reply_template": "已登记。", "table": "lostfound",
                           "id": "lost9", "created_at": "测试"})
        got3 = asyncio.run(_route("我丢了饭卡啊"))
        c.check("两个服务的触发词互相包含时，**更长的那条**算赢（不是先到先得）",
                got3 == "custom:lost9", str(got3))
        c.check("服务不存在就是不存在：custom:没有这一项 也算不作数",
                store.service_of_module("custom:nope") is None
                and store.service_of_module("faq") is None, "")

        # 提示是**每次现算**的：刚建的服务下一句就该出现在给模型的清单里
        from app.agent.intent import _intent_system
        s = _intent_system()
        c.check("新建的服务立刻出现在语义层的候选清单里（现算，不是写死在提示里）",
                "custom:lost9" in s and "失物细节" in s, "")
        c.check("清单里排掉了内置那项（它的台账是工单，不是 custom/<id>.json）",
                "custom:pkgloss" not in s, "")
    return c


def batch_b_no_hijack():
    c = Checker()
    title("A2 · 抢不走：既有归属（工单 / faq / planner）一个都不许动")
    import asyncio

    with sandbox():
        _seed()
        cases = [
            # 内置那项 table=workorders 不参与自动路由：这句照旧走 express → 工单上报硬分支
            ("快递丢了", "express", "内置「快递/外卖问题处理」不抢工单链"),
            ("图书馆几点关门", "faq", "纯问句不许被登记类触发词拐走"),
            ("我要取快递", "express", "取快递照旧"),
        ]
        for text, want, why in cases:
            got = asyncio.run(_route(text))
            c.check(f"{why}：「{text}」→ {want}", got == want, f"实际 {got}")

        # 日程那句：主链路会带**规则层预检的意图**进来（`intent_hint="add_todo"`），
        # 意图先行那一档排在触发词之前，所以它必须照旧归 planner。
        # ⚠️ 单独 `route()` 不带 intent_hint 时它回的是 faq —— 那是**本来就有的行为**
        #   （关键词表里没有"游泳"），不是这一轮改出来的，别在这儿断言 planner。
        got_plan = asyncio.run(_route("帮我加个游泳周四19:00", intent_hint="add_todo"))
        c.check("带意图先行的日程那句照旧归 planner（触发词档排在它后面）",
                got_plan == "planner", f"实际 {got_plan}")

        # ★ 问句否决：触发词命中了，但学生是在**问流程**，不是在下达登记。
        #   「失物去哪登记」正好是服务自己写的触发词之一 —— 光靠子串命中就会出成登记卡，
        #   而学生要的答案是"交到驿站"那句话。判据复用 `looks_like_question`，不另写一套。
        for q in ("空调坏了怎么办", "失物去哪登记", "捡到东西交哪"):
            gotq = asyncio.run(_route(q))
            c.check(f"问句「{q}」不许被触发词拐去登记（照旧回问答/faq）",
                    gotq != "custom:lost1" and gotq != "custom:rep1", f"实际 {gotq}")

        # ★ 顺序的证据（指南那条「往沙箱灌同款样本再测」）：
        #   样本库要是把这句话学成了别的（这里灌一条**判错的**：我丢了饭卡 → add_todo），
        #   触发词档**不许**盖过它 —— 这是既有顺序的一部分，也是"脏样本是地雷"（红线 6）的形状。
        #   （`remember` 故意不收 chat 样本，所以这里只能灌 add_todo 那种"学错了"的。）
        from app.agent.intent import remember
        remember("我丢了饭卡", "add_todo", {})
        got2 = asyncio.run(_route("我丢了饭卡"))
        c.check("样本库学过这句 → 仍按样本库走（新档排在它后面，不是插到它前面）",
                got2 == "planner", f"实际 {got2}")

        # 语义层给了合法 custom:<id> → 收；给的是别的模块 → 照旧用它给的
        r = _patch_model({"module": "custom:lost1", "intent": "custom_record",
                          "confidence": 0.9})
        try:
            got3 = asyncio.run(_route("东西落在驿站了"))
        finally:
            r()
        c.check("语义层给出真实存在的 custom:<id> → 收下", got3 == "custom:lost1", str(got3))
    return c


def batch_c_distrust():
    c = Checker()
    title("A3 · 不作数就丢：服务 id 必须回查 services.json，编的一律丢弃")
    import asyncio

    from app.agent.intent import classify_with_llm
    with sandbox():
        _seed()
        # 1) 模型给了个不存在的 id + custom_record → 整条 None（不是"降级成 chat 继续用"）
        r = _patch_model({"module": "custom:999nope", "intent": "custom_record",
                          "confidence": 0.9})
        try:
            got = asyncio.run(classify_with_llm("我丢了饭卡"))
        finally:
            r()
        c.check("custom_record + 不存在的 service_id → 丢弃整条判定（宁可不办）",
                got is None, str(got)[:80])

        # 2) 模型自造意图（不在 KNOWN_INTENTS）→ 照旧丢弃（这条是老红线，别被新代码放水）
        r = _patch_model({"module": "custom:lost1", "intent": "register_lost_card",
                          "confidence": 0.95})
        try:
            got2 = asyncio.run(classify_with_llm("我丢了饭卡"))
        finally:
            r()
        c.check("模型自造 intent（不在白名单）→ 仍然丢弃", got2 is None, str(got2)[:60])

        # 3) custom:<字符串> 长得像合法也不许过（只校验前缀 = 没校验）
        from app import store
        c.check("module_ok 不看字符串像不像，只回查 services.json",
                store.is_custom_module("custom:lost1")
                and not store.is_custom_module("custom:lost1x")
                and not store.is_custom_module("custom:")
                and not store.is_custom_module("custom:lost1/../../x"), "")

        # 4) 低代码那一路**不给模型任何写库工具**：主链路的工具箱里不许出现 add/write/save 类动作
        client = make_client()
        names = set()
        import app.main as M
        for key, (_p, builder) in M.REGISTRY.items():
            try:
                names |= set((builder() or {}).keys())
            except Exception:
                pass
        bad = [n for n in names if any(w in n.lower() for w in
                                       ("add_", "write_", "save_", "insert_", "create_record"))]
        c.check("模型侧看不到任何写库动作（登记工具只能是『提案』，不许是写入）",
                not bad, "、".join(sorted(bad))[:80])
    return c


def batch_d_shape():
    c = Checker()
    title("A4 · 形状：判据只有一份，档位顺序钉住")
    src = pathlib.Path("app/agent/router.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    code_only = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    # 触发词档必须排在 ②③④ 之后、⑤ 通用关键词表之前
    i_sample = code_only.index("match_sample(user_input)")
    i_sem = code_only.index("classify_with_llm(user_input")
    i_trig = code_only.index("match_service_by_trigger(user_input)")
    i_quick = code_only.index("for keyword, module in _QUICK_MAP.items()")
    c.check("触发词档的位置：样本库/语义层之后、通用关键词表之前（位置本身就是判据强度）",
            i_sem < i_trig < i_quick and i_sample < i_trig,
            f"{i_sample} < {i_sem} < {i_trig} < {i_quick}")
    # 合法性判据只有一处实现
    intent_src = pathlib.Path("app/agent/intent.py").read_text(encoding="utf-8")
    ic = "\n".join(ln.split("#")[0] for ln in intent_src.splitlines())
    c.check("「module 合不合法」只有一个函数在判（module_ok），别处不许各写一份",
            ic.count("def module_ok(") == 1
            and "module = module_hint(intent)" in ic
            and "if module not in MODULE_KEYS:" not in ic, "")
    rtr = "module_ok(" in code_only and code_only.count("in MODULE_KEYS") <= 1
    c.check("router 里三档都改走同一个判据（不再自己写 in MODULE_KEYS）", rtr,
            f"router 里还剩 {code_only.count('in MODULE_KEYS')} 处 in MODULE_KEYS")
    c.check("触发词那一档带问句否决（复用 looks_like_question，没另造一张疑问词表）",
            "looks_like_question(user_input)" in code_only
            and "match_service_by_trigger(user_input)" in code_only, "")
    return c


def batch_b1_tool_makes_card():
    c = Checker()
    title("B1 · 出卡：模型只调 propose_custom_record，写库一个字节都没有")
    import asyncio

    with sandbox():
        _seed()
        sem = json.dumps({"module": "custom:lost1", "intent": "custom_record",
                          "confidence": 0.9}, ensure_ascii=False)
        args = {"fields": {"物品描述": "蓝色饭卡", "丢失地点": "三食堂", "大概时间": "",
                           "admin_note": "模型顺手多给一个键"}}
        restore = _patch_tool_model(sem, "propose_custom_record", args)
        client = make_client()
        sid = "T54-card-1"
        try:
            got = client.post("/api/chat", json={"message": "我丢了饭卡，蓝色饭卡，在三食堂丢的",
                                                 "session_id": sid})
        finally:
            restore()
        body = got.json()
        cards = [o for o in (body.get("options") or [])
                 if isinstance(o, dict) and o.get("kind") == "custom_record"]
        c.check("学生一句话 → 挂出一张 custom_record 卡（不是回一句'已经登记好了'）",
                got.status_code == 200 and len(cards) == 1,
                f"{got.status_code} / {len(cards)} 张 / {str(body)[:80]}")
        card = cards[0] if cards else {}
        c.check("卡绑在这项服务上（service/name 都对，reply 用的是服务自己的话术）",
                card.get("service") == "lost1" and card.get("name") == "失物招领"
                and "驿站领" in str(card.get("reply")), str(card)[:120])
        c.check("模型读到的字段进了卡；没说的那项**留空**（不替他编）",
                card.get("fields", {}).get("物品描述") == "蓝色饭卡"
                and card.get("fields", {}).get("丢失地点") == "三食堂"
                and card.get("fields", {}).get("大概时间") == "", str(card.get("fields")))
        c.check("字段顺序按服务定义给前端（不靠 dict 顺序、也不让前端自己猜一份表）",
                card.get("field_order") == ["物品描述", "丢失地点", "大概时间"],
                str(card.get("field_order")))
        # 出卡这一步就把"多给的键"丢掉（不等落库那次才判）：卡是前端与确认页读的东西，
        # 卡上多一个键，确认页就多渲染一个框，学生还会以为真有这么一项。
        c.check("模型顺手多给的键**进不了卡**（卡上的键只能是服务定义那几项）",
                set(card.get("fields") or {}) == {"物品描述", "丢失地点", "大概时间"},
                str(sorted((card.get("fields") or {}).keys())))
        # ★ 这段的核心：**没点确认，台账里一个字都没有**
        p = data_path(os.path.join("custom", "lost1.json"))
        c.check("没点确认 → custom/lost1.json 不存在（出卡不等于写库）",
                not os.path.exists(p), p)
        pend = data_path("pending.json")
        stored = json.load(open(pend, encoding="utf-8")) if os.path.exists(pend) else {}
        c.check("卡确实进了系统暂存（确认页读的就是这份）",
                any(any((o or {}).get("kind") == "custom_record"
                        for o in (v or {}).get("options", [])) for v in stored.values()),
                str(list(stored))[:70])

        # 工具箱：低代码这一路给模型的就那一件，而且名字里不许有写入动作
        from app import store as _s
        from app.modules.custom import custom_tools
        names = set(custom_tools(_s.get_service("lost1")))
        c.check("这项服务的工具箱**只有一件、而且是提案**（没有任何 add/write/save 类动作）",
                names == {"propose_custom_record"}, "、".join(sorted(names))[:80])
        # 同一件事的**行为面**证据：引擎那一跳实际收到的工具清单（比扫源码更硬，
        # 因为源码没写不代表拼给模型的时候没多带一份）
        got_tools = (restore.fake.seen_tools[-1] if restore.fake.seen_tools else [])
        c.check("引擎那一跳模型实际看到的工具清单里，没有任何写入动作",
                bool(got_tools) and all(isinstance(t, str) and t for t in got_tools)
                and not any(any(w in t.lower() for w in
                                ("add_", "write_", "save_", "delete_", "update_"))
                            for t in got_tools),
                str(got_tools)[:110])

        # 提示是现算的：服务名与要收的字段都出现在给模型的说明里
        from app.modules.custom import custom_prompt
        pr = custom_prompt(_s.get_service("lost1"))
        c.check("系统提示里带着这项服务的名字和要收的字段（现算，不是写死）",
                "失物招领" in pr and "物品描述" in pr and "严禁说" in pr, "")

        # 优先级诚实写下来：句子里带「报修」这类硬信号时，**先出工单卡**（既有分支排在引擎之前）
        got2 = client.post("/api/chat", json={"message": "宿舍空调坏了，帮我报修",
                                              "session_id": "T54-card-2"})
        kinds2 = [o.get("kind") for o in (got2.json().get("options") or [])
                  if isinstance(o, dict)]
        c.check("带「报修」这种既有硬信号的句子照旧先走工单上报（新档不抢既有分支）",
                "work_order_report" in kinds2 and "custom_record" not in kinds2, str(kinds2))

        # ★ 这条才是"确定性"的证据：**模型把这句当成闲聊**（intent=chat、module=faq，
        #   也就是语义层根本没选服务），只要句子里有管理员写下的触发原话，仍然归到那项服务。
        #   为什么单独钉：配了密钥时语义层几乎总给一个合法 module（faq 也算合法），
        #   `router.route()` 那一档压根不会被调用 —— 只靠 router 的话，真机上能不能命中
        #   全看模型心情。测试在无密钥下绿、真机有心跳红，就是这种形状。
        sem_chat = json.dumps({"module": "faq", "intent": "chat", "confidence": 0.7})
        r2 = _patch_tool_model(sem_chat, "propose_custom_record",
                               {"fields": {"物品描述": "饭卡", "丢失地点": "", "大概时间": ""}})
        try:
            got3 = client.post("/api/chat", json={
                "message": "我丢了饭卡", "session_id": "T54-card-3"})
        finally:
            r2()
        kinds3 = [o.get("kind") for o in (got3.json().get("options") or []) if isinstance(o, dict)]
        c.check("模型只当它是闲聊时，触发词这个硬信号照样接管（不靠模型心情）",
                "custom_record" in kinds3 and str(got3.json().get("module")).startswith("custom:"),
                f"{got3.json().get('module')} / {kinds3}")

        # 反过来：同一句里学生**明确要办另一件事**（加待办），触发词不许把它抢去登记
        r3 = _patch_tool_model(json.dumps({"module": "planner", "intent": "add_todo",
                                           "title": "吃饭", "confidence": 0.9},
                                          ensure_ascii=False),
                               "propose_custom_record", {"fields": {"物品描述": "饭卡"}})
        try:
            got4 = client.post("/api/chat", json={
                "message": "我丢了饭卡，先帮我加个明天下午吃饭的待办", "session_id": "T54-card-4"})
        finally:
            r3()
        kinds4 = [o.get("kind") for o in (got4.json().get("options") or []) if isinstance(o, dict)]
        c.check("这句已经被认成『要加待办』→ 触发词不抢（既有优先级：意图 > 子串命中）",
                "custom_record" not in kinds4, str(kinds4)[:90])

        # 再反过来一次：学生在**问办法**（「…怎么办」），不是在下达登记 —— 这条走的是
        # 现成的问句判据（`QUESTION_RE` 里就有"怎么/如何"），不另造一张词表。
        r4 = _patch_tool_model(sem_chat, "propose_custom_record",
                               {"fields": {"物品描述": "饭卡"}})
        try:
            got5 = client.post("/api/chat", json={
                "message": "我丢了饭卡怎么办", "session_id": "T54-card-5"})
        finally:
            r4()
        kinds5 = [o.get("kind") for o in (got5.json().get("options") or []) if isinstance(o, dict)]
        c.check("「我丢了饭卡怎么办」是在问办法 → 不许挂登记卡（照旧答他一句）",
                "custom_record" not in kinds5, str(kinds5)[:90])
    return c


def batch_b2_confirm_is_the_only_writer():
    c = Checker()
    title("B2 · 确认才写库：那张卡是唯一的授权凭据")
    with sandbox():
        _seed()
        client = make_client()
        sid = "T54-ok-1"
        sem = json.dumps({"module": "custom:lost1", "intent": "custom_record",
                          "confidence": 0.9}, ensure_ascii=False)
        restore = _patch_tool_model(sem, "propose_custom_record",
                                    {"fields": {"物品描述": "钥匙", "丢失地点": "图书馆",
                                                "大概时间": "周二"}})
        try:
            client.post("/api/chat", json={"message": "我丢了饭卡", "session_id": sid})
        finally:
            restore()

        full = {"物品描述": "钥匙", "丢失地点": "图书馆", "大概时间": "周二中午"}
        ok = client.post("/api/custom/records",
                         json={"session_id": sid, "service": "lost1", "fields": full})
        p = data_path(os.path.join("custom", "lost1.json"))
        rows = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        c.check("点了确认 → 系统写进 custom/lost1.json，回的是服务自己的话",
                ok.status_code == 200 and len(rows) == 1
                and rows[0]["service"] == "lost1"
                and "驿站领" in str(ok.json().get("reply")),
                f"{ok.status_code} / {len(rows)} 条")
        c.check("落库的字段与卡片一致（多一个键都没有）",
                rows and set(rows[0]["fields"]) == set(full), str(rows[:1])[:90])

        # 幂等：同一张卡点第二次不许写第二条
        again = client.post("/api/custom/records",
                            json={"session_id": sid, "service": "lost1", "fields": full})
        rows2 = json.load(open(p, encoding="utf-8"))
        c.check("点第二次 → 409 且台账还是 1 条（卡用掉就没了，不重复写）",
                again.status_code == 409 and len(rows2) == 1,
                f"{again.status_code} / {len(rows2)} 条")

        # 凭空 POST（会话里没有这张卡）→ 409，什么都不写
        ghost = client.post("/api/custom/records",
                            json={"session_id": "T54-没有这张卡", "service": "lost1",
                                  "fields": full})
        rows3 = json.load(open(p, encoding="utf-8"))
        c.check("没有卡就 POST → 409 挡掉、台账没动（暂存里那张卡才是授权凭据）",
                ghost.status_code == 409 and len(rows3) == 1,
                f"{ghost.status_code} / {len(rows3)} 条")

        # 拿 A 服务的卡去登记 B 服务 → 409（跨卡写入被拒）。
        # ⚠️ 这条必须**现挂一张新卡**再试：上面那次点确认已经把 T54-ok-1 的暂存清空了，
        #   拿它去测"卡绑服务"，去掉绑定也照样回 409（因为根本没卡）——
        #   这就是本项目反复踩的"因为错误的原因变绿"（变异 S5 第一次就是这么蒙过去的）。
        cross = "T54-cross"
        r5 = _patch_tool_model(sem, "propose_custom_record",
                               {"fields": {"物品描述": "手表", "丢失地点": "教室", "大概时间": "今早"}})
        try:
            client.post("/api/chat", json={"message": "我丢了饭卡", "session_id": cross})
        finally:
            r5()
        other = client.post("/api/custom/records",
                            json={"session_id": cross, "service": "rep1",
                                  "fields": {"宿舍楼": "3 号楼", "坏了什么": "空调"}})
        rep_p = data_path(os.path.join("custom", "rep1.json"))
        c.check("手上是一张失物招领的卡、却去登记报修 → 409（卡绑服务，跨服务写入不认）",
                other.status_code == 409 and not os.path.exists(rep_p),
                f"{other.status_code} / rep1 文件存在={os.path.exists(rep_p)}")
        # 同一个会话、同一张卡，登记到它自己那项服务 → 应当成功（证明上面那条不是"怎么都 409"）
        same = client.post("/api/custom/records",
                           json={"session_id": cross, "service": "lost1",
                                 "fields": {"物品描述": "手表", "丢失地点": "教室",
                                            "大概时间": "今早"}})
        rows_l = json.load(open(data_path(os.path.join("custom", "lost1.json")),
                                 encoding="utf-8"))
        c.check("同一张卡登记到它自己那项服务 → 成功（证明上一条不是「怎么都 409」）",
                same.status_code == 200 and len(rows_l) == 2,
                f"{same.status_code} / lost1 共 {len(rows_l)} 条")

        # 缺字段 / 多给键：与驿站端**同一份判据**（_custom_field_errors）
        restore2 = _patch_tool_model(sem, "propose_custom_record",
                                    {"fields": {"物品描述": "校园卡"}})
        try:
            client.post("/api/chat", json={"message": "我丢了饭卡", "session_id": "T54-miss"})
        finally:
            restore2()
        miss = client.post("/api/custom/records",
                           json={"session_id": "T54-miss", "service": "lost1",
                                 "fields": {"物品描述": "校园卡"}})
        c.check("缺必填 → 400 并说清缺哪几项（文案与驿站端同源）",
                miss.status_code == 400 and "丢失地点" in str(miss.json().get("error")),
                str(miss.json())[:80])
        stray = client.post("/api/custom/records",
                            json={"session_id": "T54-miss", "service": "lost1",
                                  "fields": {"物品描述": "校园卡", "丢失地点": "东门",
                                             "大概时间": "今天", "admin_note": "塞一条"}})
        c.check("多给的键 → 400（往台账里塞任意键不能是低代码的副作用）",
                stray.status_code == 400 and "admin_note" in str(stray.json().get("error")),
                str(stray.json())[:80])
        nf = client.post("/api/custom/records",
                         json={"session_id": sid, "service": "nosuch", "fields": full})
        c.check("服务不存在 → 404", nf.status_code == 404, str(nf.status_code))
    return c


def batch_b3_shape():
    c = Checker()
    title("B3 · 形状：写入只有系统那一条路，判据只有一份")
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    code_only = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    # 旧的裸判据必须消失（两处 `in REGISTRY` 都换成同一个函数）
    c.check("主链路两处归属判断都走同一个 `_module_handled`（不留一份裸 in REGISTRY）",
            code_only.count("module_key in REGISTRY") == 0
            and code_only.count("_explicit_module in REGISTRY") == 0
            and code_only.count("_module_handled(") >= 3,
            f"裸判据 {code_only.count('module_key in REGISTRY')} 处")
    c.check("低代码的提示与工具箱都按那份定义现算（不是 REGISTRY 里写死一个）",
            "custom_prompt(s)" in code_only and "custom_tools(s)" in code_only, "")
    c.check("字段判据只有一份实现、两个入口都调它",
            code_only.count("def _custom_field_errors(") == 1
            and code_only.count("_custom_field_errors(svc,") == 2,
            f"调用 {code_only.count('_custom_field_errors(svc,')} 处")

    # custom.py 里**不许出现写入函数**：那是这段代码的边界声明
    cs = pathlib.Path("app/modules/custom.py").read_text(encoding="utf-8")
    cs_code = "\n".join(ln.split("#")[0] for ln in cs.splitlines())
    for banned in ("add_custom_record", "add_todo", "add_workorder", "add_service",
                   "_write(", "open("):
        c.check(f"模型那一路（modules/custom.py）碰不到写入：没有 {banned}",
                banned not in cs_code, "")
    c.check("全项目写 custom 台账只有两个入口：驿站端【登记】与学生端【确认】，不许长出第三个",
            code_only.count("add_custom_record(") == 2, f"{code_only.count('add_custom_record(')} 处")
    # 引擎的通用机制才把 __proposal__ 变成卡：低代码不自己塞 options
    c.check("出卡走引擎那套『结果即提案』（返回值里带 __proposal__），没有另接一条 options 通道",
            '"__proposal__"' in cs, "")

    # ---- 前端两层：字段表来自服务定义，不在这页上再抄一份 ----
    cf = pathlib.Path("app/static/confirm.html").read_text(encoding="utf-8")
    c.check("确认页按 field_order 渲染输入框（字段是运行时数据，前端不许写死一份清单）",
            "p.field_order" in cf and "data-f" in cf
            and '"/api/custom/records"' in cf, "")
    c.check("每个字段三个位置都转义（label / data-f / value），不许留裸插值",
            "${esc(String(k))}</label>" in cf
            and 'data-f="${esc(String(k))}"' in cf
            and 'value="${esc(String(f[k] || ""))}"' in cf, "")
    # 硬信号那一档的**驱动条件**（顺序本身就是判据，注释挡不住改代码的人）：
    # 语义层已经选了服务就不动；意图已被认成"要办别的事"也不动；问句不动。
    c.check("接管条件钉住三条：不是 custom / 不是问句 / 意图为空或 chat",
            "not is_custom_module(module_key) and not looks_like_question(message)" in code_only
            and "_eff_intent in (None, INTENT_CHAT)" in code_only, "")
    st = pathlib.Path("app/static/student.html").read_text(encoding="utf-8")
    c.check("学生端认得 custom_record 这张卡（走既有确认页通道，没新造一套卡片组件）",
            'o.kind === "custom_record"' in st and "openConfirm(one, container)" in st, "")
    c.check("`custom:<id>` 不印给学生看（标签映射成『驿站服务』，转义那一层还在）",
            'indexOf("custom:") === 0 ? "驿站服务"' in st
            and "${esc(moduleTag(module))}" in st, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_54_custom_service_intent：低代码服务接进判定层（认识 → 出卡 → 确认才写库）")
    print("=" * 60)
    code |= batch_a_recognized().summary("A1 批（认得出）")
    code |= batch_b_no_hijack().summary("A2 批（抢不走）")
    code |= batch_c_distrust().summary("A3 批（不作数就丢）")
    code |= batch_d_shape().summary("A4 批（形状）")
    code |= batch_b1_tool_makes_card().summary("B1 批（模型只出卡）")
    code |= batch_b2_confirm_is_the_only_writer().summary("B2 批（确认才写库）")
    code |= batch_b3_shape().summary("B3 批（写入边界形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
