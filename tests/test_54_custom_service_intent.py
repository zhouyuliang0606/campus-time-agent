"""#22 单子 4 · A 批：判定层认识低代码服务（`custom:<服务id>`）。

这块最难的不是"能不能命中"，而是**新加的一档有没有把既有顺序挤坏**。
判定层既有顺序是 样本库 → 语义层 → 意图先行 → 关键词兜底，
触发词那一档插在"意图先行之后、通用关键词表之前"，动一个位置就等于换了一档判据强度。

所以 A 批分三层钉：
  1) **认得出**：学生一句「我丢了饭卡」在没有密钥、没有样本的情况下也要归到那项服务；
  2) **抢不走**：内置那项（工单链路）和既有的 faq/express 归属不许被顶掉；
  3) **不作数就丢**：语义层给的服务 id 只要在 `services.json` 里不存在，整条判定丢弃
     —— 只校验前缀 `custom:` 等于没校验，模型编一个名字就能把话送到一条没人核对的路上。
"""
import json
import pathlib

from _harness import Checker, make_client, sandbox, title

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


def main():
    global code
    print("\n" + "=" * 60)
    print("test_54_custom_service_intent：低代码服务接进判定层")
    print("=" * 60)
    code |= batch_a_recognized().summary("A1 批（认得出）")
    code |= batch_b_no_hijack().summary("A2 批（抢不走）")
    code |= batch_c_distrust().summary("A3 批（不作数就丢）")
    code |= batch_d_shape().summary("A4 批（形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
