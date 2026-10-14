"""07 号流式（SSE）的护栏：传输层真流、出口按 Accept 协商、**done 必须晚于守卫**。

先说实测（2026-10-14，两笔真请求 + `llm_usage.jsonl`）：
  · `api.deepseek.com/deepseek-chat` 支持 `stream:true` + `stream_options:{include_usage:true}`，
    79 块、`finish_reason=stop`，**末块确实带 usage** → 流式不会把账记成 0；
  · 小 prompt 的 TTFT 2696ms（含探针自己新建连接的握手）。但生产首字压不到施工单说的「1~2s」：
    语义判定（p50 **4.2s**）按施工单 §6.1 明令**保持非流式**，它就挡在引擎最后一个字之前。
    这一刀真买到的是：trace/思考过程 ~0.2s 就推出去（「卡死」变「在想」）+ 末段回答逐字显现。
  · 引擎只有**最后一步**才有 `content`（`engine.py:194`），前面都是 tool_calls → token 流只存在于末步。

三处对施工单的偏离（都有理由，不是偷懒）：
  1. §4.3「engine.run 改异步生成器 + run_collected」→ 这里改成**可选回调 `on_event`**。
     理由：`/api/chat` 的 227 处测试与 plan.html:499、station.html:201 读的都是 `.json()`；
     默认出口保持 JSON，回归面从「整个仓库」缩到「新增的那条 SSE 分支」。
  2. §5 的 `result` 事件**不发到前端**（施工单自己也说「仅供后端内部衔接」）——
     本设计里守卫直接吃 `_chat_impl` 的返回值，根本没有中间事件要传，少一个前端要认的 `k`。
  3. §4.5 只列了 student.html 两处；整族扫实测是**四处**（另有 plan.html / station.html 各一处）。
     那两处不在本刀接线（以卡片为主、正文由系统拼模板，流式零收益），而默认 JSON 出口
     保证它们**一行不改也不会坏** —— F 批钉住「student.html 里只剩一处打 /api/chat」。
"""
import asyncio
import http.server
import json
import os
import threading
import time

# 与 test_47 同因：这台机器 HTTP_PROXY 开着，不绕开就把本地假接口代理成 502
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from _harness import Checker, FakeLLM, make_client, sandbox, title
from test_47_shared_async_client import _point_at_local_server, _spy_ssrf

code = 0

PIECES = ["食堂", "今晚", "九点", "关门"]          # 分四块送，块数与内容都要能对上
FULL = "".join(PIECES)
# 引擎自称"提案已发出"这句话正是当年学生截图投诉的原文形状 → 必须被 claim 守卫换掉
CLAIM = "帮你把《游泳》排到周二 16:00~17:30，提案已发出，请点确认条上的【确认】。"
CHAT_FETCH = 'fetch("/api/chat"'


class _StreamServer(http.server.BaseHTTPRequestHandler):
    """假的大模型接口：会发 SSE 分块，也会按 mode 演「不支持流式」「鉴权失败」「中途断」。

    mode：
      sse        —— 正常流式：逐块 data:，末块带 usage，再发 [DONE]
      sse_tool   —— 流式，但内容是被切成碎片的 tool_calls（引擎要能拼回完整调用）
      plain      —— 非流式整包 JSON（网关降级就落在这里）
      reject     —— 见到 stream:true 回 400，其余走 plain（演「这上游不会流式」）
      deny       —— 一律 401（鉴权错误不许被降级咽掉）
      truncate   —— 吐一块之后直接把连接掐了（演「流到一半断了」）
    """

    protocol_version = "HTTP/1.1"
    requests = 0
    bodies: list = []
    mode = "sse"
    gap = 0.15
    lock = threading.Lock()

    def _chunk(self, payload: bytes):
        # 手工写 chunked：一块 flush 一次，客户端才能真的"边收边解析"，
        # 而不是等服务端把整包写完（A 批那条「首块比末块早到」靠的就是它）
        self.wfile.write(b"%x\r\n" % len(payload) + payload + b"\r\n")
        self.wfile.flush()

    def _end_chunk(self):
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _sse_head(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _json_once(self, code_no: int, payload: bytes, ctype="application/json"):
        self.send_response(code_no)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _plain(self, content):
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": content},
                                        "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                                     "total_tokens": 18}},
                          ensure_ascii=False).encode("utf-8")
        self._json_once(200, body)

    def do_POST(self):
        cls = type(self)
        with cls.lock:
            cls.requests += 1
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            body = {}
        with cls.lock:
            cls.bodies.append(body)

        if cls.mode == "deny":
            self._json_once(401, b'{"error":{"message":"bad key"}}')
            return
        if cls.mode == "deny_stream":
            # 只对**流式**鉴权失败，非流式照样回整段：
            # 这是 M3 那条"把降级条件写宽"唯一能分辨出来的形状
            #（若两种都回 401，退回非流式也会抛同一个错，变异与原文等价 → 测不出差别）
            if body.get("stream"):
                self._json_once(401, b'{"error":{"message":"stream denied"}}')
                return
            self._plain("整段答案（不该被用来顶替流式）")
            return
        if cls.mode == "plain":
            self._plain(FULL)
            return
        if cls.mode == "reject":
            if body.get("stream"):
                self._json_once(400, b'{"error":{"message":"stream not supported"}}')
                return
            self._plain(FULL)
            return

        self._sse_head()
        if cls.mode == "sse_tool":
            # 一次工具调用被切成三块：id / name / arguments 的碎片
            self._chunk(_sse({"choices": [{"delta": {"role": "assistant", "tool_calls": [
                {"index": 0, "id": "call_9", "type": "function",
                 "function": {"name": "propose_slots"}}]}}]}))
            time.sleep(cls.gap)
            self._chunk(_sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"date":'}}]}}]}))
            self._chunk(_sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '"2026-10-14"}'}}]},
                "finish_reason": "tool_calls"}]}))
            self._chunk(_sse({"choices": [{"delta": {}}],
                              "usage": {"prompt_tokens": 20, "completion_tokens": 9,
                                        "total_tokens": 29}}))
            self._chunk(b"[DONE]")
            self._end_chunk()
            return
        if cls.mode == "truncate":
            self._chunk(_sse({"choices": [{"delta": {"content": PIECES[0]}}]}))
            # 不给 [DONE]、也不发结束 chunk，直接关连接 → httpx 当协议错误抛出来
            try:
                self.close_connection = True
                self.wfile.close()
            except Exception:
                pass
            return

        for p in PIECES:
            self._chunk(_sse({"choices": [{"delta": {"content": p}}]}))
            time.sleep(cls.gap)
        self._chunk(_sse({"choices": [{"delta": {}, "finish_reason": "stop"}],
                          "usage": {"prompt_tokens": 20, "completion_tokens": 12,
                                    "total_tokens": 32}}))
        self._chunk(b"[DONE]")
        self._end_chunk()

    def log_message(self, *a):
        pass


def _sse(obj) -> bytes:
    if isinstance(obj, bytes):
        return b"data: " + obj + b"\n\n"
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


def _serve_stream():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StreamServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _set_mode(mode):
    _StreamServer.mode = mode
    _StreamServer.requests = 0
    _StreamServer.bodies = []


def _drain(gen):
    """抽干异步生成器，返回 (token 文本列表, 全部事件, 首块时刻, 末块时刻)。"""
    async def go():
        toks, evs = [], []
        first = last = None
        async for ev in gen:
            evs.append(ev)
            if ev.get("k") == "token":
                toks.append(ev["v"])
                now = time.perf_counter()
                if first is None:
                    first = now
                last = now
        return toks, evs, first, last
    return asyncio.run(go())


def _parse_sse(text: str) -> list:
    """把 SSE 正文切成事件列表（等价于前端的读法：只认 data: 行，空行分块）。"""
    out = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "[DONE]":
                    try:
                        out.append(json.loads(raw))
                    except Exception:
                        out.append({"k": "坏行", "v": raw[:40]})
    return out


# ============================ A 批 · 传输层真流 ============================

def batch_a_transport():
    c = Checker()
    title("A. 本地假接口真发 SSE：分块到齐、逐块早到、tool_calls 拼得回、闸门照跑")
    import app.llm.client as C
    from app.llm.gateway import ModelGateway

    srv = _serve_stream()
    port = srv.server_address[1]
    old_spy, hits = _spy_ssrf()
    restore = _point_at_local_server(port)
    _set_mode("sse")
    msgs = [{"role": "user", "content": "食堂几点关门"}]

    try:
        toks, evs, first, last = _drain(ModelGateway(user="内部:流式自检").stream(msgs))
        c.check("服务端真收到这笔流式请求（没偷偷跑去真上游）",
                _StreamServer.requests == 1, f"{_StreamServer.requests} 笔")
        sent = _StreamServer.bodies[-1] if _StreamServer.bodies else {}
        c.check("请求体带 stream 与 stream_options.include_usage（末块才有 usage 可记）",
                sent.get("stream") is True
                and (sent.get("stream_options") or {}).get("include_usage") is True,
                json.dumps({k: sent.get(k) for k in ("stream", "stream_options")},
                           ensure_ascii=False))
        c.check("token 按顺序到齐，拼起来就是整段", "".join(toks) == FULL,
                f"{toks} -> {''.join(toks)}")
        gap_ms = (last - first) * 1000 if (first and last) else -1
        c.check("是真流式：首块比末块至少早 0.2 秒到（不是攒一整包再吐）",
                gap_ms > 200, f"首末间隔 {gap_ms:.0f} ms")
        msg_ev = [e for e in evs if e.get("k") == "message"]
        c.check("抽干之后末尾给一条完整 message（引擎按它决定调不调工具）",
                len(msg_ev) == 1 and msg_ev[0]["v"].get("content") == FULL,
                json.dumps(msg_ev[-1]["v"], ensure_ascii=False)[:70] if msg_ev else "没有 message")
        c.check("SSRF 闸门在流式这一路也跑了（每次调用一次，不许挪进建 client）",
                hits["n"] == 1, f"{hits['n']} 次")

        # 工具调用被切成碎片送来，必须拼回完整（拼不回 = 引擎永远调不了工具）
        _set_mode("sse_tool")
        toks2, evs2, _, _ = _drain(ModelGateway(user="内部:流式自检").stream(msgs))
        m2 = [e for e in evs2 if e.get("k") == "message"]
        tc = ((m2[-1]["v"] if m2 else {}).get("tool_calls") or [{}])[0]
        fn = tc.get("function") or {}
        c.check("流式碎片拼回的 tool_calls：函数名与参数 JSON 字符串都完整",
                fn.get("name") == "propose_slots"
                and fn.get("arguments") == '{"date":"2026-10-14"}',
                json.dumps(tc, ensure_ascii=False)[:110])
        c.check("工具调用那一步不该有正文字冒出来（否则会看到半句废话）",
                "".join(toks2) == "", repr("".join(toks2))[:40])

        # 非流式那条路一个字没动：同一个 client 还能按老样子拿答案
        _set_mode("plain")
        got = asyncio.run(C.DeepSeekClient().chat(msgs))
        c.check("chat() 一个字没改：非流式照旧拿到整段", got.get("content") == FULL,
                json.dumps(got, ensure_ascii=False)[:60])
    finally:
        restore()
        C.check_base_url_ssrf = old_spy
        srv.shutdown()
    return c


# ============================ B 批 · 记账与三种降级 ============================

def batch_b_accounting_and_fallback():
    c = Checker()
    title("B. 流式不许把账记漏；上游不会流式要退，鉴权失败与中途断线不许退")
    import app.llm.client as C
    from app.llm.gateway import ModelGateway
    from app.store import clear_llm_usage, read_llm_usage

    srv = _serve_stream()
    port = srv.server_address[1]
    old_spy, _hits = _spy_ssrf()
    restore = _point_at_local_server(port)
    msgs = [{"role": "user", "content": "食堂几点关门"}]

    with sandbox():
        clear_llm_usage()
        try:
            _set_mode("sse")
            _drain(ModelGateway(user="内部:流式自检").stream(msgs))
            rows = read_llm_usage()
            c.check("流式这一笔照样进用量流水（记账不能因为改流式就漏）",
                    len(rows) == 1, f"{len(rows)} 笔")
            r0 = rows[0] if rows else {}
            c.check("末块 usage 被读到：tokens 不是 0",
                    r0.get("prompt_tokens") == 20 and r0.get("completion_tokens") == 12,
                    json.dumps({k: r0.get(k) for k in ("prompt_tokens", "completion_tokens",
                                                       "total_tokens")}))
            c.check("记的是这笔的 user 标签与状态（管理端那张表靠它）",
                    r0.get("user") == "内部:流式自检" and r0.get("status") == "ok",
                    json.dumps({k: r0.get(k) for k in ("user", "key_source", "status")}))
            c.check("这笔标着 stream=1（出问题时能分清走的是哪条路）",
                    r0.get("stream") == 1, str(r0.get("stream")))

            # 上游不认识 stream → 一块都没吐时退回非流式，学生照样拿到整段
            _set_mode("reject")
            toks, evs, _, _ = _drain(ModelGateway(user="内部:流式自检").stream(msgs))
            c.check("上游不支持流式：自动退回非流式，内容不缺字",
                    "".join(toks) == FULL, repr("".join(toks))[:40])
            c.check("退回来的那段只算一次（不许既流一遍又整段一遍）",
                    len(toks) == 1, f"{len(toks)} 块")

            # 鉴权失败不许被降级咽掉（否则学生永远看不见「Key 无效」那句话）
            _set_mode("deny")
            kind = ""
            try:
                _drain(ModelGateway(user="内部:流式自检").stream(msgs))
            except Exception as e:
                kind = getattr(e, "kind", "")
            c.check("401 照常上抛 kind=auth（降级只给上游不支持流式用，鉴权错误不许静默）",
                    kind == "auth", kind or "没抛异常")

            # 只对"流式"鉴权失败、非流式能成的那种上游：这才是"降级写宽"能露形的位置
            _set_mode("deny_stream")
            kind_w, toks_w = "", []
            try:
                async def go_w():
                    async for ev in ModelGateway(user="内部:流式自检").stream(msgs):
                        if ev["k"] == "token":
                            toks_w.append(ev["v"])
                asyncio.run(go_w())
            except Exception as e:
                kind_w = getattr(e, "kind", "")
            c.check("上游只对 stream 回 401 时也必须上抛，不许偷偷退回非流式把坏 Key 当没事",
                    kind_w == "auth" and toks_w == [],
                    f"kind={kind_w or '没抛'} 退回来的内容={toks_w}")

            # 流到一半断了：已经吐过内容就**不许**再整段补一遍（同屏两遍是事故）
            _set_mode("truncate")
            toks4, kind4 = [], ""
            try:
                async def go():
                    async for ev in ModelGateway(user="内部:流式自检").stream(msgs):
                        if ev["k"] == "token":
                            toks4.append(ev["v"])
                asyncio.run(go())
            except Exception as e:
                kind4 = getattr(e, "kind", type(e).__name__)
            c.check("中途断线：已吐的那一块留着，不补整段",
                    toks4 == [PIECES[0]], repr(toks4)[:40])
            c.check("中途断线要把错误上抛（上层才能发一句人话，不是留半句当没事）",
                    kind4 == "unreachable", kind4)
        finally:
            restore()
            C.check_base_url_ssrf = old_spy
            srv.shutdown()
    return c


# ============================ C 批 · /api/chat 按 Accept 协商 ============================

def batch_c_negotiation():
    c = Checker()
    title("C. 出口协商：不带 Accept 仍是今天那份 JSON（那 227 处测试与另两个页面才不吃亏）")
    from test_20_polish import _patch_client

    with sandbox():
        client = make_client()
        restore = None
        try:
            # 默认出口：形状与今天逐键一致（这条是"别把别人弄坏"的保险）
            r1 = client.post("/api/chat", json={"message": "帮我安排游泳", "module": "planner",
                                                "session_id": "t49-json"})
            c.check("不带 Accept → Content-Type 还是 application/json",
                    "application/json" in (r1.headers.get("content-type") or ""),
                    r1.headers.get("content-type") or "")
            d1 = r1.json()
            c.check("JSON 那份的五个键一个不少（answer/options/trace/session_id/module）",
                    all(k in d1 for k in ("answer", "options", "trace", "session_id", "module")),
                    json.dumps(sorted(d1), ensure_ascii=False)[:90])

            restore = _patch_client(CLAIM, with_key=True)
            r2 = client.post("/api/chat", json={"message": "帮我加个游泳", "module": "planner",
                                                "session_id": "t49-sse"},
                             headers={"Accept": "text/event-stream"})
            c.check("带 Accept: text/event-stream → 回的是 event-stream",
                    "text/event-stream" in (r2.headers.get("content-type") or ""),
                    r2.headers.get("content-type") or "")
            evs = _parse_sse(r2.text)
            ks = [e.get("k") for e in evs]
            # ⚠️ 这条必须连着"事件非空"一起判：空列表 ⊆ 任何集合都成立，
            #    第一版就是这么假绿过去的（SSE 根本没实现时它也绿）。
            c.check("事件只认三种 k（token / trace / done；施工单那个内部 result 不发给前端）",
                    bool(ks) and set(ks) <= {"token", "trace", "done"}, json.dumps(ks[:8]))
            c.check("最后一个事件是 done，且 done.v 就是今天那份 JSON 的形状",
                    bool(ks) and ks[-1] == "done"
                    and all(k in (evs[-1].get("v") or {})
                            for k in ("answer", "options", "trace", "session_id", "module")),
                    json.dumps(ks[-3:], ensure_ascii=False))
            done_v = (evs[-1].get("v") or {}) if (ks and ks[-1] == "done") else {}
            c.check("done.v.session_id 与请求会话一致（前端存 id 的逻辑不用改）",
                    done_v.get("session_id") == "t49-sse", str(done_v.get("session_id")))
        finally:
            if restore:
                restore()
    return c


# ============================ D 批 · done 必须晚于守卫 ============================

# 这句是 07 号施工单自己举的例子，实测**会走到 ReAct 引擎**（trace 里出现 🤔 思考）。
# 换成「帮我加个游泳」会被规则层直接接走、一个 token 都不发 —— 那 D 批就变成测空气
# （"测的必须是走到的"，本项目撞过好几轮的那条）。
ENGINE_MSG = "图书馆几点关门"
ENGINE_MODULE = "faq"


def batch_d_guard_order():
    c = Checker()
    title("D. 本刀唯一要命的时序：token 是引擎原话的预览，done 必须是守卫修正后的权威答案")
    from test_20_polish import _patch_client

    with sandbox():
        client = make_client()
        restore = None
        try:
            restore = _patch_client(CLAIM, with_key=True)
            r = client.post("/api/chat", json={"message": ENGINE_MSG,
                                               "module": ENGINE_MODULE,
                                               "session_id": "t49-guard"},
                            headers={"Accept": "text/event-stream"})
            evs = _parse_sse(r.text)
            preview = "".join(e["v"] for e in evs if e.get("k") == "token")
            done = next((e["v"] for e in evs if e.get("k") == "done"), {})
            phases = [str(t.get("phase")) for t in (done.get("trace") or []) if isinstance(t, dict)]
            c.check("前提：这一句确实走到了 ReAct 引擎（trace 里有思考/最终回答）",
                    any("思考" in p or "最终回答" in p for p in phases),
                    json.dumps(phases[:4], ensure_ascii=False)[:110])
            c.check("流式预览确实是模型原话（token 不替守卫做主）",
                    "提案已发出" in preview, repr(preview)[:60])
            c.check("done.answer 是守卫修正后的版本（原话被换掉，幻觉不透传给学生）",
                    bool(str(done.get("answer") or "").strip())
                    and "提案已发出" not in str(done.get("answer")),
                    str(done.get("answer"))[:60])
            c.check("守卫不只换话，还把该挂的卡补了出来（options 不空）",
                    bool(done.get("options")), f"{len(done.get('options') or [])} 张")
            c.check("done 之后不能再有 token（防 done 后还在流的残缺回复）",
                    bool(evs) and evs[-1].get("k") == "done",
                    json.dumps([e.get("k") for e in evs][-3:]))

            # 同一条守卫在非流式那条路上也得同样生效（两个出口口径一致）
            restore()
            restore = _patch_client(CLAIM, with_key=True)
            r2 = client.post("/api/chat", json={"message": ENGINE_MSG,
                                                "module": ENGINE_MODULE,
                                                "session_id": "t49-guard2"})
            c.check("非流式那条路的 answer 同样被守卫换掉（两条出口口径一致）",
                    "提案已发出" not in str(r2.json().get("answer") or ""),
                    str(r2.json().get("answer"))[:60])
        finally:
            if restore:
                restore()
    return c


# ============================ E 批 · 老假货不许被流式弄崩 ============================

def batch_e_legacy_fakes():
    c = Checker()
    title("E. 兼容：客户端桩只有 chat（没有 chat_stream）时，SSE 出口照样给 done")
    from _harness import patch_llm

    with sandbox():
        client = make_client()
        restore = None
        try:
            c.check("前提成立：全仓 227 处测试用的那个 FakeLLM 确实只有 chat（没有 chat_stream）",
                    not hasattr(FakeLLM, "chat_stream"),
                    "有 chat_stream" if hasattr(FakeLLM, "chat_stream") else "只有 chat")
            restore = patch_llm("图书馆晚上九点半关门，周末到八点。", with_key=True)
            r = client.post("/api/chat", json={"message": ENGINE_MSG,
                                               "module": ENGINE_MODULE,
                                               "session_id": "t49-legacy"},
                            headers={"Accept": "text/event-stream"})
            evs = _parse_sse(r.text)
            ks = [e.get("k") for e in evs]
            c.check("没有 chat_stream 的桩不炸：SSE 里仍拿到 done（内部退回非流式整段）",
                    "done" in ks and bool(ks) and ks[-1] == "done", json.dumps(ks[:6]))
            preview = "".join(e["v"] for e in evs if e.get("k") == "token")
            done = next((e["v"] for e in evs if e.get("k") == "done"), {})
            c.check("退回那一段照样逐字给到（预览就是整段，一个字不缺）",
                    preview == "图书馆晚上九点半关门，周末到八点。", repr(preview)[:50])
            c.check("done.answer 非空（学生看不见空白气泡）",
                    bool(str(done.get("answer") or "").strip()),
                    str(done.get("answer"))[:40])
        finally:
            if restore:
                restore()
    return c


# ============================ F 批 · 形状（别把非流式那条路改坏） ============================

def batch_f_shape():
    c = Checker()
    title("F. 形状：chat() 没被流式化、判定与润色照旧非流式、前端只留一处打 /api/chat")

    def code_of(path):
        # 剔掉注释再比形状（本项目被自己注释里的字面串绊红过四次）
        return "\n".join("" if ln.lstrip().startswith("#") else ln.split("#")[0]
                         for ln in open(path, encoding="utf-8").read().splitlines())

    client_src = code_of("app/llm/client.py")
    seg = client_src[client_src.index("async def chat("):client_src.index("async def chat_stream")]
    c.check("client.chat() 的函数体里不许出现 stream 字段（非流式那条路一字未动）",
            '"stream"' not in seg and "stream_options" not in seg, "")
    gw_src = code_of("app/llm/gateway.py")
    seg2 = gw_src[gw_src.index("    async def chat("):gw_src.index("    async def stream(")]
    c.check("gateway.chat() 也不许偷偷去调 chat_stream", "chat_stream" not in seg2, "")

    intent_src = code_of("app/agent/intent.py")
    c.check("语义判定仍走非流式 gateway.chat（施工单 §6.1，流式对它零收益）",
            "gateway.chat(" in intent_src and ".stream(" not in intent_src, "")
    polish_src = code_of("app/agent/polish.py")
    c.check("润色也仍走非流式（本刀不碰 polish 的调用方式）",
            ".stream(" not in polish_src, "")

    eng_src = code_of("app/agent/engine.py")
    c.check("引擎只通过网关要流式（不直接碰 client.chat_stream），且只有一处",
            "chat_stream" not in eng_src and eng_src.count("self.llm.stream(") == 1,
            f"self.llm.stream( 出现 {eng_src.count('self.llm.stream(')} 次")
    c.check("引擎 run() 的流式是**可选回调**（默认不传，227 处测试与 JSON 出口才不受影响）",
            "on_event" in eng_src.split("async def run(")[1][:260], "")

    stu = open("app/static/student.html", encoding="utf-8").read()
    c.check("student.html 里打 /api/chat 的地方只剩一处（统一走 streamChat 那个 helper）",
            stu.count(CHAT_FETCH) == 1, f"{stu.count(CHAT_FETCH)} 处")
    c.check("前端确实按 SSE 读（getReader + text/event-stream），12 号施工单的 helper 名是 streamChat",
            "getReader(" in stu and "text/event-stream" in stu
            and stu.count("streamChat(") >= 3,      # 1 处定义 + 2 处调用
            f"streamChat( 出现 {stu.count('streamChat(')} 次")
    # ⚠️ 这条钉的是**不变量**（预览用 token、定稿用 done 里的权威答案），不是施工单那段代码的字面形状：
    #    本仓的实现是"删掉占位气泡再调 addMsg(d.answer)"，刻意不手抄第二份 addMsg 的 HTML
    #    （施工单 §4.2 那段自己拼 whoHtml/bodyHtml/traceHtml = 两本账，traceOn() 一改就漂移）。
    #    第一版写成 `"addMsg(botName(), d.answer" in stu`，被**上传课表那条**（同一串前缀）蒙过去，
    #    变异 M7 把定稿换成预览拼串它照样绿 —— 现在钉整行形状，一处不多一处不少。
    done_line = 'addMsg(botName(), d.answer, "bot", d.module, d.trace)'
    c.check("done 必须覆盖预览：预览走 token 变量，定稿走 done 里的权威答案（整行形状只此一处）",
            "bubble.textContent = streamed" in stu and stu.count(done_line) == 1,
            f"整行出现 {stu.count(done_line)} 次")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_49_streaming_sse：流式传输层 / Accept 协商 / done 晚于守卫")
    print("=" * 60)
    for fn, label in ((batch_a_transport, "A 批（传输层）"),
                      (batch_b_accounting_and_fallback, "B 批（记账与降级）"),
                      (batch_c_negotiation, "C 批（协商）"),
                      (batch_d_guard_order, "D 批（守卫时序）"),
                      (batch_e_legacy_fakes, "E 批（老假货兼容）"),
                      (batch_f_shape, "F 批（形状）")):
        code |= fn().summary(label)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
