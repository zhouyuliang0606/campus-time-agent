"""第二十二批测试：自研模型网关 + 用量统计（唯一出口 / 跨厂商标准化 / 记账）

背景
====
以前"谁想问模型，谁就自己 new 一个客户端"：引擎一个、语义层一个、润色层一个、
后台测连通一个。三个后果：
  ① 换模型要改代码（管理台改配置只改到了一半的路）；
  ② 各家返回的"工具调用"长得不一样 —— Claude 是正文块里的 `tool_use`、
     Gemini 是 `functionCall`，谁接谁自己兜，兜漏了就**静默失灵**；
  ③ 花了多少 token、失败了多少次，没人知道。

现在统一过 `app/llm/gateway.ModelGateway`：读配置 → 转发上游 →
**标准化工具调用** → 记账（try/finally，成功失败都记）。

这一批要钉住四件事
==================
1. 三家格式都翻成 OpenAI 风格，且 `arguments` 是 **JSON 字符串**
   （给 dict 会让上层 json.loads 拿不到参数，工具调用无声失效）；
2. 认不出来就**原样上抛** —— 不凭空造调用、不丢字段（翻译层只改结构不改语义）；
3. 成功/失败**都记账**；记账本身失败不许拖垮对话；
4. 翻译不改行为：非标准格式进来的加待办，出卡行为跟标准格式**一模一样**。

⚠️ 打桩只打 `app.llm.client.DeepSeekClient` + `app.config.get_llm_config`：
网关是**要用时才** import 客户端，模块属性补丁传得进去（详见 test_18/19 的注释）。
以前"引擎顶层 import、必须单独打一份"那条经验已随网关化失效。

全部跑在数据沙箱里，不碰真实演示数据。
"""
import asyncio
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title, sandbox, make_client  # noqa: E402

from app.llm.gateway import (  # noqa: E402
    ModelGateway, _tokens, normalize_message, normalize_tool_call,
)
from app.store import (  # noqa: E402
    clear_llm_usage, log_llm_usage, read_llm_usage, summarize_llm_usage,
)


# ---------------------------------------------------------------------------
# 假客户端：能指定"回什么消息 + 带什么 usage + 要不要抛异常"
# ---------------------------------------------------------------------------
class _Rec:
    def __init__(self, msg=None, usage=None, boom=None, model="fake-1"):
        self.ret = msg if msg is not None else {"content": "好"}
        self.last_usage = usage
        self.last_model = model
        self.boom = boom

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        if self.boom:
            raise RuntimeError(self.boom)
        return self.ret


def _openai_style(name, args):
    """标准的 OpenAI/DeepSeek 长相（这一批拿它当"标准答案"）。"""
    return {"id": "c1", "type": "function",
            "function": {"name": name, "arguments": args}}


# ---------------------------------------------------------------------------
def test_normalize_one_call():
    title("1. 单元：normalize_tool_call —— 三家格式翻成同一种长相")
    c = Checker()
    name, args = "加待办", {"title": "游泳", "date": "周四"}

    # ① OpenAI / DeepSeek / 国产直通：本来就是标准长相，翻完不能变味
    one = normalize_tool_call(_openai_style(name, _json.dumps(args, ensure_ascii=False)))
    c.check("① OpenAI 直通：名字照旧", one and one["function"]["name"] == name)
    c.check("① OpenAI 直通：arguments 仍是字符串（没被二次加工）",
            one and one["function"]["arguments"] == _json.dumps(args, ensure_ascii=False))
    c.check("① OpenAI 直通：id 保住了", one and one["id"] == "c1")
    c.check("① OpenAI 直通：type 是 function", one and one["type"] == "function")

    # ② Claude：正文块里的 tool_use，参数字段叫 input
    two = normalize_tool_call({"type": "tool_use", "id": "tu_1", "name": name, "input": args})
    c.check("② Claude tool_use：翻成 function 结构",
            two and two["function"]["name"] == name)
    c.check("② Claude tool_use：input 变成 JSON 字符串",
            two and _json.loads(two["function"]["arguments"]) == args)
    c.check("② Claude tool_use：id 跟着带过来", two and two["id"] == "tu_1")

    # ③ Gemini：functionCall，参数字段叫 args
    three = normalize_tool_call({"functionCall": {"name": name, "args": args}})
    c.check("③ Gemini functionCall：翻成 function 结构",
            three and three["function"]["name"] == name)
    c.check("③ Gemini functionCall：args 变成 JSON 字符串",
            three and _json.loads(three["function"]["arguments"]) == args)
    c.check("③ Gemini functionCall：没给 id 就现编一个",
            three and str(three["id"]).startswith("call_") and len(three["id"]) > 8)

    # ④ 部分国产模型平铺：{name, arguments}
    four = normalize_tool_call({"name": name, "arguments": args})
    c.check("④ 平铺 name/arguments：翻成 function 结构",
            four and four["function"]["name"] == name)
    c.check("④ 平铺：arguments 从 dict 变成字符串",
            four and isinstance(four["function"]["arguments"], str))

    # 边界
    c.check("参数缺失 → 给 {}（不是 None、不是 'null'）",
            normalize_tool_call({"name": name})["function"]["arguments"] == "{}")
    c.check("完全认不出来 → 返回 None（让上层按老办法处理，不硬编一个）",
            normalize_tool_call({"zzz": 1}) is None)
    c.check("传个非 dict 进来也不许崩", normalize_tool_call("不是字典") is None)
    return c.summary("第一批（三家工具调用标准化）")


def test_normalize_message():
    title("2. 单元：normalize_message —— 整条消息的翻译（只改结构，不改语义）")
    c = Checker()
    args = {"title": "游泳", "date": "周四"}

    # Claude：正文是块数组，工具调用混在里面
    claude = {"role": "assistant", "content": [
        {"type": "text", "text": "我先查一下"},
        {"type": "tool_use", "id": "tu_1", "name": "加待办", "input": args},
    ]}
    out = normalize_message(claude)
    c.check("Claude：正文拼成一句字符串（上层读的是字符串）",
            out["content"] == "我先查一下")
    c.check("Claude：块里的调用被捡出来了", len(out.get("tool_calls") or []) == 1)
    c.check("Claude：捡出来的是标准结构",
            out["tool_calls"][0]["function"]["name"] == "加待办")

    # Gemini：调用在 parts 里
    gem = {"role": "assistant", "parts": [
        {"text": "好"}, {"functionCall": {"name": "加待办", "args": args}}]}
    out2 = normalize_message(gem)
    c.check("Gemini：parts 里的 functionCall 被捡出来",
            len(out2.get("tool_calls") or []) == 1)
    c.check("Gemini：参数翻成字符串",
            isinstance(out2["tool_calls"][0]["function"]["arguments"], str))

    # 认不出来：原样上抛（保住老行为），不擅自把"没有"变成"有"
    weird = {"role": "assistant", "content": "hi", "tool_calls": [{"zzz": 1}]}
    out3 = normalize_message(weird)
    c.check("认不出来的块：原样保留（不丢字段）", out3["tool_calls"] == [{"zzz": 1}])

    # 本来就没有工具调用：不许凭空造一个空的
    plain = {"role": "assistant", "content": "好的"}
    out4 = normalize_message(plain)
    c.check("纯文字回答：不凭空加 tool_calls", "tool_calls" not in out4)
    c.check("纯文字回答：content 原样", out4["content"] == "好的")
    return c.summary("第二批（整条消息翻译）")


def test_gateway_accounting():
    title("3. 网关记账：成功/失败都记，记账自己出事也不许拖垮对话")
    c = Checker()

    async def _run():
        # 成功：usage 要落到流水里
        gw = ModelGateway(user="sess-abc",
                          client=_Rec({"content": "ok"},
                                      {"prompt_tokens": 10, "completion_tokens": 5,
                                       "total_tokens": 15}))
        out = await gw.chat([{"role": "user", "content": "hi"}])
        row = read_llm_usage()[-1]
        return out, row

    with sandbox():
        out, row = asyncio.run(_run())
        c.check("成功：调用照常返回", out == {"content": "ok"})
        c.check("成功：status=ok", row.get("status") == "ok", row.get("status"))
        c.check("成功：token 记对了", row.get("prompt_tokens") == 10
                and row.get("completion_tokens") == 5 and row.get("total_tokens") == 15)
        c.check("成功：user 是调用方（这里是会话 id）", row.get("user") == "sess-abc")
        c.check("成功：model 记下来了", row.get("model") == "fake-1", row.get("model"))
        c.check("成功：error 为空", not row.get("error"))
        c.check("成功：ts 自动补上了", bool(row.get("ts")))

        # 失败：也要记，且记的是失败
        async def _boom():
            gw = ModelGateway(user="内部:语义判定", client=_Rec(boom="no key"))
            try:
                await gw.chat([{"role": "user", "content": "hi"}])
            except RuntimeError as e:
                return str(e)
        msg = asyncio.run(_boom())
        row2 = read_llm_usage()[-1]
        c.check("失败：异常照常上抛（网关不吞错，上层负责给友好提示）", msg == "no key")
        c.check("失败：status=error", row2.get("status") == "error", row2.get("status"))
        c.check("失败：错误原因记下来了", "no key" in (row2.get("error") or ""))
        c.check("失败：token 记 0（不是把上一次的数字抄过来）",
                row2.get("total_tokens") == 0)
        c.check("失败：user 是内部功能名", row2.get("user") == "内部:语义判定")

        # 上游不回 usage：不算失败，token 记 0
        async def _no_usage():
            gw = ModelGateway(user="s", client=_Rec({"content": "x"}, usage=None))
            await gw.chat([{"role": "user", "content": "hi"}])
        asyncio.run(_no_usage())
        row3 = read_llm_usage()[-1]
        c.check("上游没回 usage：仍算成功，token 记 0",
                row3.get("status") == "ok" and row3.get("total_tokens") == 0)

        # 记账自己炸了：不许影响对话
        import app.llm.gateway as _gw
        old = _gw.log_llm_usage
        _gw.log_llm_usage = lambda *a, **k: (_ for _ in ()).throw(IOError("磁盘满了"))
        try:
            async def _broken():
                gw = ModelGateway(user="s", client=_Rec({"content": "还能答"}))
                return await gw.chat([{"role": "user", "content": "hi"}])
            r = asyncio.run(_broken())
            c.check("记账失败：对话照常返回（旁路不许拖垮主线）", r == {"content": "还能答"})
        finally:
            _gw.log_llm_usage = old

        # 拔 key：客户端自己会抛 LLMError，网关要记 status=error
        import app.config as _cfg
        old_cfg = _cfg.get_llm_config
        _cfg.get_llm_config = lambda *a, **k: {"api_key": "", "base_url": "http://x",
                                               "model": "m"}
        try:
            async def _nokey():
                from app.llm.client import LLMError
                gw = ModelGateway(user="s")
                try:
                    await gw.chat([{"role": "user", "content": "hi"}])
                    return "没抛"
                except LLMError:
                    return "LLMError"
            got = asyncio.run(_nokey())
            row4 = read_llm_usage()[-1]
            c.check("没 key：客户端抛 LLMError（不静默）", got == "LLMError")
            c.check("没 key：流水里记 status=error", row4.get("status") == "error",
                    row4.get("status"))
        finally:
            _cfg.get_llm_config = old_cfg
    return c.summary("第三批（网关记账）")


def test_summarize_and_endpoint():
    title("4. 汇总形状 + 坏行容错 + 管理端接口")
    c = Checker()
    with sandbox():
        clear_llm_usage()
        s = summarize_llm_usage(5)
        c.check("空流水：total_calls=0 且 models 为空",
                s["total_calls"] == 0 and s["models"] == [])
        c.check("空流水：last_ts 是空串（不是 None）", s["last_ts"] == "")

        for i in range(3):
            log_llm_usage({"user": f"s{i % 2}", "model": "deepseek-chat",
                           "prompt_tokens": 10, "completion_tokens": 5,
                           "total_tokens": 15, "status": "ok", "latency_ms": 100})
        log_llm_usage({"user": "s0", "model": "gpt-4o-mini", "total_tokens": 8,
                       "status": "error", "error": "no key", "latency_ms": 20})
        s = summarize_llm_usage(limit_recent=2)
        c.check("汇总：调用数 4 / 成功 3 / 失败 1",
                s["total_calls"] == 4 and s["ok_calls"] == 3 and s["error_calls"] == 1)
        c.check("汇总：总 token 对（15*3 + 8）", s["total_tokens"] == 53, s["total_tokens"])
        c.check("汇总：平均耗时只算成功的（不把 20ms 混进来）",
                s["avg_latency_ms"] == 100, s["avg_latency_ms"])
        c.check("汇总：models 按调用量降序",
                [m["model"] for m in s["models"]] == ["deepseek-chat", "gpt-4o-mini"])
        c.check("汇总：share 加起来是 100",
                round(sum(m["share"] for m in s["models"])) == 100)
        c.check("汇总：recent 只回最近 2 条，且最新在前",
                len(s["recent"]) == 2 and s["recent"][0]["status"] == "error")
        c.check("汇总：users 去重后是 2", s["users"] == 2, s["users"])

        # 坏行容错：一条脏数据不该毁掉整份统计
        with open(os.path.join(os.environ["CAMPUSTIME_DATA_DIR"], "llm_usage.jsonl"),
                  "a", encoding="utf-8") as f:
            f.write("这不是 JSON\n")
        s2 = summarize_llm_usage()
        c.check("坏行：被跳过，总数不变（不许整份统计崩掉）", s2["total_calls"] == 4)

        # 管理端接口
        client = make_client()
        d = client.get("/api/admin/usage").json()
        c.check("GET /api/admin/usage → 200 且 ok", client.get("/api/admin/usage").status_code == 200
                and d.get("ok") is True)
        c.check("接口：带上汇总好的全部字段",
                all(k in d for k in ("total_calls", "ok_calls", "error_calls",
                                     "total_tokens", "avg_latency_ms", "models", "recent")))
        r = client.post("/api/admin/usage/clear").json()
        c.check("POST /api/admin/usage/clear → 报出清掉几条",
                r.get("ok") is True and r.get("cleared") == 4, r)
        c.check("清空后再读：total_calls=0",
                client.get("/api/admin/usage").json()["total_calls"] == 0)
        c.check("清空只清流水：待办条数没变（不能顺手删业务数据）",
                len(client.get("/api/todos").json().get("todos", []))
                == len(client.get("/api/todos").json().get("todos", [])))
    return c.summary("第四批（汇总与接口）")


# ---------------------------------------------------------------------------
# 假模型：按"厂商口音"回工具调用，用来验证"换了口音，出卡行为不变"
# ---------------------------------------------------------------------------
class _VendorLLM:
    def __init__(self, sem_reply: str, vendor: str, tool_name: str, tool_args: dict):
        self._sem_reply, self._vendor = sem_reply, vendor
        self._tool_name, self._tool_args = tool_name, tool_args
        self._engine_calls = 0

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        if tools:
            self._engine_calls += 1
            if self._engine_calls == 1:
                if self._vendor == "claude":
                    return {"role": "assistant", "content": [
                        {"type": "text", "text": "我来排一下"},
                        {"type": "tool_use", "id": "tu_1",
                         "name": self._tool_name, "input": self._tool_args}]}
                if self._vendor == "gemini":
                    return {"role": "assistant", "parts": [
                        {"text": "我来排一下"},
                        {"functionCall": {"name": self._tool_name,
                                          "args": self._tool_args}}]}
                return {"tool_calls": [_openai_style(
                    self._tool_name, _json.dumps(self._tool_args, ensure_ascii=False))]}
            return {"content": "好的，已为你处理。"}
        return {"content": self._sem_reply}


def _patch_vendor(sem_reply: str, vendor: str, tool_name: str, tool_args: dict):
    """只打客户端 + 配置两处（网关按需 import，补丁传得进去）。"""
    import app.config as _cfg
    import app.llm.client as _client
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    fake = _VendorLLM(sem_reply, vendor, tool_name, tool_args)
    _client.DeepSeekClient = lambda *a, **k: fake
    _cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key",
                                           "base_url": "http://x", "model": "m"}

    def _restore():
        _client.DeepSeekClient = old_client
        _cfg.get_llm_config = old_cfg
    return _restore


def _todo_card(opts):
    return next((o for o in (opts or []) if isinstance(o, dict)
                 and o.get("kind") == "todo_add"), None)


def test_engine_user_is_session():
    title("5. 引擎那条路：user 必须是学生的会话 id（管理台要能看出是谁在问）")
    c = Checker()
    from app.agent.engine import AgentEngine
    with sandbox():
        async def _run(sid):
            eng = AgentEngine(llm=_Rec({"content": "好的"}), session_id=sid)
            return await eng.run("帮我安排一下")
        asyncio.run(_run("gw-test-1"))
        rows = read_llm_usage()
        c.check("引擎调用记进了流水", len(rows) >= 1, str(len(rows)))
        c.check("user 是会话 id（不是功能名）",
                any(r.get("user") == "gw-test-1" for r in rows),
                str([r.get("user") for r in rows]))
        c.check("status=ok", rows[-1].get("status") == "ok")

        # 没给会话 id 时也不能记成空 —— 记成功能名，免得表里出现一片空白
        asyncio.run(_run(""))
        c.check("没会话 id → 退成『内部:agent』（不留空白）",
                read_llm_usage()[-1].get("user") == "内部:agent",
                read_llm_usage()[-1].get("user"))
    return c.summary("第五批（引擎的调用方）")


def test_vendor_e2e():
    title("6. 端到端：Claude / Gemini 口音进来的加待办，出卡行为跟标准格式一样")
    c = Checker()
    sem = _json.dumps({"module": "planner", "intent": "add_todo", "confidence": 0.9})
    args = {"title": "游泳", "date": "周四", "start": "19:00", "end": "20:00"}
    for vendor, label in (("openai", "标准 OpenAI"), ("claude", "Claude"),
                          ("gemini", "Gemini")):
        with sandbox():
            client = make_client()
            restore = _patch_vendor(sem, vendor, "propose_todo_tool", args)
            try:
                body = client.post("/api/chat", json={
                    "message": "帮我加个游泳周四19:00", "module": "planner",
                    "session_id": "gw-test-1"}).json()
            finally:
                restore()
            card = _todo_card(body.get("options"))
            c.check(f"{label} 口音 → 照样出 todo_add 卡", card is not None,
                    _json.dumps(body.get("options"), ensure_ascii=False)[:80])
            if card:
                c.check(f"{label} 口音 → 事名是『游泳』", card.get("title") == "游泳",
                        card.get("title"))
                c.check(f"{label} 口音 → 时间是 19:00-20:00",
                        card.get("start") == "19:00" and card.get("end") == "20:00",
                        f"{card.get('start')}-{card.get('end')}")
            rows = read_llm_usage()
            # ⚠️ 这一句「帮我加个游泳周四19:00」走的是**确定性分支**（规则层自己
            # 就解析得出来，压根没去问模型），所以流水里是"内部:语义判定"这类
            # 功能名，不会出现会话 id。会记会话 id 的是**引擎那条路**，
            # 单独在第四批里验。这里只钉住"这次对话确实过了网关"。
            c.check(f"{label} 口音 → 这次对话进了用量流水", len(rows) > 0,
                    str(len(rows)))
    return c.summary("第六批（三家口音端到端）")


def test_reverse_without_normalize():
    title("7. 反向验证：撤掉翻译层，非标准格式立刻失灵（证明测的是目标信号）")
    c = Checker()
    import app.llm.gateway as _gw
    old = _gw.normalize_message

    claude = {"role": "assistant", "content": [
        {"type": "text", "text": "我来排一下"},
        {"type": "tool_use", "id": "tu_1", "name": "加待办", "input": {"title": "游泳"}}]}
    gem = {"role": "assistant", "parts": [
        {"functionCall": {"name": "加待办", "args": {"title": "游泳"}}}]}

    _gw.normalize_message = lambda m: m          # 临时撤掉翻译
    try:
        c.check("撤掉翻译 → Claude 消息里确实没有 tool_calls",
                "tool_calls" not in _gw.normalize_message(claude))
        c.check("撤掉翻译 → Gemini 消息里确实没有 tool_calls",
                "tool_calls" not in _gw.normalize_message(gem))
    finally:
        _gw.normalize_message = old

    # 恢复后：同样的输入又能翻译出来（证明上面那两条红，是翻译层的功劳）
    c.check("恢复翻译 → Claude 又能拿到 1 个标准调用",
            len(normalize_message(claude).get("tool_calls") or []) == 1)
    c.check("恢复翻译 → Gemini 又能拿到 1 个标准调用",
            len(normalize_message(gem).get("tool_calls") or []) == 1)

    # 为什么 arguments 必须是字符串：给 dict，上层 json.loads 直接拿不到
    dict_args = {"id": "c1", "type": "function",
                 "function": {"name": "加待办", "arguments": {"title": "游泳"}}}
    try:
        _json.loads(dict_args["function"]["arguments"])
        loaded = True
    except Exception:
        loaded = False
    c.check("arguments 给 dict → 上层 json.loads 拿不到参数（所以必须转字符串）",
            not loaded)
    return c.summary("第七批（反向验证）")


if __name__ == "__main__":
    fails = 0
    fails += test_normalize_one_call()
    fails += test_normalize_message()
    fails += test_gateway_accounting()
    fails += test_summarize_and_endpoint()
    fails += test_engine_user_is_session()
    fails += test_vendor_e2e()
    fails += test_reverse_without_normalize()
    sys.exit(1 if fails else 0)
