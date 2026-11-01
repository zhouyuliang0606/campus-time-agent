"""刀 2：共享 AsyncClient —— 同一进程内复用 TCP/TLS 连接，别再每次调用重握手。

实测依据（2026-10-14，`llm_usage.jsonl` + 直连测量）：每次新建 `AsyncClient` 的三笔握手
2188 / 1967 / 1987 ms，复用同一 client 是 679 / 574 / 450 ms —— **每次固定多付 ≈1.3~1.5 秒**。
「食堂几点关门」那种三次往返（实测 9437ms）里，重复握手合计 +4.2 秒。

改的只有传输层一处（全仓 `AsyncClient` 只在这里创建），SSRF 闸门、异常映射 kind、
超时值、网关记账、润色与引擎的守卫全部不动。

动手前实测了施工单的两个假设，**其中一个把我自己的结论打翻了**（改口记在这儿，别当推理看）：
  · 模块级 `asyncio.Lock` 跨 `asyncio.run` 复用不炸 —— 这条成立；
  · "已经建好 keep-alive 连接的 client 换个循环用也不炸" —— **不成立**。第一版按"全进程就一个
    client"写，A 批在一个循环里建好连接、B 批换第二个循环去 `aclose()` 它，直接
    `RuntimeError: Event loop is closed`（anyio → Windows proactor `call_soon`）。
    于是改成**按事件循环各留一份**：生产上 uvicorn 一 worker 一循环，永远只有 1 个条目，
    复用一分不少；测试里 `TestClient` / `asyncio.run` 本来就一次一个循环，各留一份才不串。
    下面 C 批钉的就是"换循环不炸"。

⚠️ 2026-11-01（#102 追记）：上面那段"超时值不动"已经作废。connect 从 3 秒放宽到 8 秒
（冷连接 = TCP 握手 + TLS 来回到不了 3 秒预算里：当天 12 次"连不上"全是 3018~3048ms
才断，一条不差），并在 `chat` / `chat_stream` 的调用点加了"只在握手超时上重试一次"。
read（30 / 60 秒）、连接池三参数、闸门次数、错误 kind 与话术一概没动。D 批钉这一条。
"""
import ast
import asyncio
import contextlib
import http.server
import json
import os
import tempfile
import threading

import httpx

# 本进程绕开系统代理：这台机器上 `HTTP_PROXY` 是开着的，httpx 默认 `trust_env=True`
# 会把"打给本地假接口的请求"丢给代理 → 代理回 502，测试就在测代理而不是测传输层。
# （顺带记下这个事实：**生产里对上游的调用也走系统代理**，与"429/连不上"那笔账有关。）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

from _harness import Checker, title

code = 0


class _CountingServer(http.server.BaseHTTPRequestHandler):
    """假的大模型接口：每**一条连接**只会 new 一个 handler 实例，用它数连接数。

    keep-alive 生效时两条请求共用一条连接 = 一个 handler 实例；
    每次新建 client（= 改回老写法）就变成两个 handler。
    """

    connections = 0
    requests = 0
    # 收到的请求体（test_48 要看 `max_tokens` 到底有没有跟着发出去 —— 光看函数签名不算数）
    bodies: list = []
    lock = threading.Lock()
    # ⚠️ 必须是 HTTP/1.1：默认的 1.0 会在每个响应后关连接，keep-alive 根本谈不起来，
    #    那"两条请求复用一条连接"就永远测不到（第一版实测 2 条连接其实是这里造成的）。
    protocol_version = "HTTP/1.1"

    def setup(self):
        # 一个 handler 实例 = 一条 TCP 连接（keep-alive 下同一条连接会处理多笔请求）
        super().setup()          # ⚠️ 必须先让父类把 rfile/wfile 建好，不然请求处理直接炸
        with type(self).lock:
            type(self).connections += 1

    def do_POST(self):
        cls = type(self)
        with cls.lock:
            cls.requests += 1
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            with cls.lock:
                cls.bodies.append(json.loads(raw.decode("utf-8") or "{}"))
        except Exception:
            with cls.lock:
                cls.bodies.append({})
        body = json.dumps({"choices": [{"message": {"content": "pong"}}]},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CountingServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _spy_ssrf():
    """把 SSRF 闸门换成"放行 + 计数"的替身：本批要测的是闸门**每次都跑**，不是它拦谁。"""
    import app.llm.client as C
    old = C.check_base_url_ssrf
    hits = {"n": 0}

    def spy(url, **kw):
        hits["n"] += 1
        return url
    C.check_base_url_ssrf = spy
    return old, hits


def _point_at_local_server(port):
    """把 base_url 指到本地假接口 —— **两处都要打**：

    ⚠️ 本仓库踩过三次的老坑（`from X import Y` 是导入时绑定）：`client.py` 顶部
    `from app.config import get_llm_config`，所以只打 `app.config.get_llm_config`
    **完全没用**——第一版就是这么写的，结果这两笔请求真发到 DeepSeek 上去了
    （白烧额度，还拿回了"一/二是什么字"两篇作文）。必须连 `app.llm.client` 里那个名字一起打。
    """
    import app.config as cfg
    import app.llm.client as C
    fake = {"api_key": "test-key", "base_url": f"http://127.0.0.1:{port}/v1", "model": "m"}
    old = (cfg.get_llm_config, C.get_llm_config)
    cfg.get_llm_config = lambda *a, **k: dict(fake)
    C.get_llm_config = lambda *a, **k: dict(fake)

    def _restore():
        cfg.get_llm_config, C.get_llm_config = old
    return _restore


# ============ #102 用的替身与静态判据 ============
# 这一族不碰网：`_FakeClient` 按剧本"抛异常 / 给响应"，`chat` / `chat_stream` 跑的是
# 真代码（只有 `_get_client`、`get_llm_config`、闸门三样换成替身），所以数出来的
# "试了几次"就是生产里真会重试几次。


class _FakeResp:
    """假响应：`chat` 只用到 `raise_for_status()` / `json()` 两样。"""

    def __init__(self, payload=None, status_code=200):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"role": "assistant", "content": "pong"}}]}
        if status_code >= 400:
            req = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
            self._err = httpx.HTTPStatusError(
                "err", request=req, response=httpx.Response(status_code, request=req))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise self._err

    def json(self):
        return self._payload


class _FakeStream:
    """假 SSE 响应：剧本项是字符串就吐一行，是异常实例就地抛（模拟"吐到一半断了"）。"""

    def __init__(self, items):
        self._items = list(items)
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for item in self._items:
            if isinstance(item, BaseException):
                raise item
            yield item


class _FakeClient:
    """按剧本回放的假 client：每一项要么是异常（抛），要么是响应/流（成功）。

    ⚠️ `stream()` 是**普通**方法（真 httpx 也是）：`async with client.stream(...)` 先把它
    调用出来、再进上下文 —— 也就是"握手超时"确实发生在进出 `async with` 之前。
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []      # post 被调了几次
        self.streams = []    # stream 被调了几次

    async def post(self, url, **kw):
        self.calls.append(url)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    def stream(self, method, url, **kw):
        self.streams.append(url)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _ct(msg="connect timed out"):
    return httpx.ConnectTimeout(msg)


def _rt(msg="read timed out"):
    return httpx.ReadTimeout(msg)


def _sse(pieces, tail=None):
    """造几行 SSE：每个片段一块；`tail` 是可选的一个异常，放在片段之后抛。"""
    items = [f'data: {json.dumps({"choices": [{"delta": {"content": p}}]}, ensure_ascii=False)}'
             for p in pieces]
    if tail is not None:
        items.append(tail)
    items.append("data: [DONE]")
    return items


@contextlib.contextmanager
def _patched_client(fake):
    """把"发请求"这件事整个换成替身；`chat` / `chat_stream` 本身一字不动照样跑。

    三样各为什么：
      · `_get_client` → 给出假 client（`chat` 里走的是模块级名字，打模块就生效）；
      · `get_llm_config` → 给一份假 Key（否则第一道 no_key 就把人挡在门外）；
      · `check_base_url_ssrf` → 放行（本批测超时/重试，闸门由 A/C 批与 test_37 钉着）。
    """
    import app.llm.client as C
    old = (C._get_client, C.get_llm_config, C.check_base_url_ssrf)

    async def _fake_get_client():
        return fake

    C._get_client = _fake_get_client
    C.get_llm_config = lambda *a, **k: {"api_key": "test-key",
                                       "base_url": "https://api.example.com/v1", "model": "m"}
    C.check_base_url_ssrf = lambda url, **kw: url
    try:
        yield C
    finally:
        C._get_client, C.get_llm_config, C.check_base_url_ssrf = old


def _run_chat(fake):
    """跑一次 `DeepSeekClient.chat`，返回 `(结果, 异常)` —— 异常不外抛。"""
    import app.llm.client as C

    async def go():
        try:
            return await C.DeepSeekClient().chat([{"role": "user", "content": "x"}]), None
        except Exception as e:
            return None, e

    with _patched_client(fake):
        return asyncio.run(go())


def _run_stream(fake):
    """抽干一次 `chat_stream`，返回 `(吐出来的片段, 异常)`。"""
    import app.llm.client as C

    async def go():
        out = []
        try:
            async for frag in C.DeepSeekClient().chat_stream([{"role": "user", "content": "x"}]):
                out.append(frag)
            return out, None
        except Exception as e:
            return out, e

    with _patched_client(fake):
        return asyncio.run(go())


@contextlib.contextmanager
def _temp_usage_log():
    """账本临时换到临时目录（`store._usage_path()` 每次现读 env，当场生效）。"""
    prev = os.environ.get("CAMPUSTIME_USAGE_FILE")
    tmp = tempfile.TemporaryDirectory(prefix="t47-usage-")
    path = os.path.join(tmp.name, "llm_usage.jsonl")
    os.environ["CAMPUSTIME_USAGE_FILE"] = path
    try:
        yield path
    finally:
        if prev is None:
            os.environ.pop("CAMPUSTIME_USAGE_FILE", None)
        else:
            os.environ["CAMPUSTIME_USAGE_FILE"] = prev
        tmp.cleanup()


def _read_rows(path):
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


# ---- 静态判据：AST 读源码，不用字面串数（注释里写着同样的字眼不会顶红）----

def _num(node):
    """常量数值节点 → float；认不出给 None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _num(node.operand)
        return -v if v is not None else None
    return None


def _call_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    return getattr(node, "id", "")


def _timeout_calls(src):
    """全文件所有 `X.Timeout(read, connect=…)`，返回排好序的 `[(read, connect), …]`。"""
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and _call_name(node.func) == "Timeout":
            read = _num(node.args[0]) if node.args else None
            connect = None
            for kw in node.keywords:
                if kw.arg == "connect":
                    connect = _num(kw.value)
            out.append((read, connect))
    return sorted(out, key=lambda c: (c[0] or 0))


def _timeout_problems(src):
    """超时口径的问题列表（前缀 `[count]` / `[connect]` / `[read]`；空 = 合格）。

    判据（2026-11-01 #102）：connect 一律 8 秒（两处都要）；read 保持 30 秒（非流式）
    与 60 秒（流式）两档 —— 放宽 connect 不许顺手把"等回答"那一半也拉长。
    """
    calls = _timeout_calls(src)
    probs = []
    if len(calls) != 2:
        probs.append(f"[count] Timeout 构造应恰有 2 处，实际 {len(calls)} 处：{calls}")
    bad = [c[1] for c in calls if c[1] != 8.0]
    if bad:
        probs.append(f"[connect] connect 不是 8 秒：{bad}")
    reads = sorted(c[0] for c in calls)
    if reads != [30.0, 60.0]:
        probs.append(f"[read] read 不是 30/60 两档：{reads}")
    return probs


def _method(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _top_loops(fn):
    """函数体里**不被别的循环包着**的循环（内层 `for ch in …` 不算"重试循环"）。"""
    out = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.For, ast.While, ast.AsyncFor)):
                out.append(child)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            walk(child)

    walk(fn)
    return out


def _retry_guards(handler):
    """这个 except 分支里"带 continue 的 if"的条件文本（认 `attempt == 0 [and not pieces]`）。"""
    return [ast.unparse(node.test) for node in ast.walk(handler)
            if isinstance(node, ast.If)
            and any(isinstance(x, ast.Continue) for x in ast.walk(node))]


def _retry_wiring_problems(src):
    """AST 读"重试挂在哪一支、挂了几次"，返回带前缀的问题列表（空 = 合格）。

    前缀：`[loop]` 循环形状、`[order]` 分支顺序、`[retry]` continue 挂在哪一支、
          `[pieces]` 流式"吐过字就不再重试"、`[factory]` 重试有没有跑进 `_get_client()`。
    """
    tree = ast.parse(src)
    probs = []
    factory = _method(tree, "_get_client")
    if factory is None:
        probs.append("[factory] 找不到 _get_client()")
    else:
        for node in ast.walk(factory):
            if isinstance(node, ast.ExceptHandler):
                probs.append("[factory] _get_client() 里有异常处理（重试必须留在两个调用点）")
                break
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "range":
                probs.append("[factory] _get_client() 里有 range(…) 重试（同上）")
                break

    for name in ("chat", "chat_stream"):
        fn = _method(tree, name)
        if fn is None:
            probs.append(f"[loop] 找不到 {name}()")
            continue
        loops = _top_loops(fn)
        if len(loops) != 1:
            probs.append(f"[loop] {name}(): 顶层重试循环应恰有 1 个，实际 {len(loops)} 个")
        if not loops:
            continue
        loop = loops[0]
        ok_loop = (isinstance(loop, ast.For)
                   and isinstance(loop.iter, ast.Call)
                   and getattr(loop.iter.func, "id", "") == "range"
                   and len(loop.iter.args) == 1
                   and _num(loop.iter.args[0]) == 2.0)
        if not ok_loop:
            probs.append(f"[loop] {name}(): 重试应是 `for attempt in range(2)`，实际 "
                         f"`{ast.unparse(loop).splitlines()[0][:60]}`")
            continue
        tries = [n for n in loop.body if isinstance(n, ast.Try)]
        if not tries:
            probs.append(f"[loop] {name}(): 重试循环里没有 try")
            continue
        handlers = tries[0].handlers
        names = [_call_name(h.type) for h in handlers]
        if "ConnectTimeout" not in names:
            probs.append(f"[retry] {name}(): 没有单独的 ConnectTimeout 分支")
        if "RequestError" not in names:
            probs.append(f"[retry] {name}(): 没有 RequestError 兜底分支")
        if "ConnectTimeout" in names and "RequestError" in names \
                and names.index("ConnectTimeout") > names.index("RequestError"):
            probs.append(f"[order] {name}(): ConnectTimeout 分支排在 RequestError 之后（永远轮不到）")
        ct = next((h for h in handlers if _call_name(h.type) == "ConnectTimeout"), None)
        re_h = next((h for h in handlers if _call_name(h.type) == "RequestError"), None)
        if ct is not None:
            guards = _retry_guards(ct)
            if not guards:
                probs.append(f"[retry] {name}(): ConnectTimeout 分支里没有受条件保护的 continue")
            elif not all("attempt" in g for g in guards):
                probs.append(f"[retry] {name}(): continue 没受 attempt 条件保护（会一直重试）")
            elif name == "chat_stream" and not all("pieces" in g for g in guards):
                probs.append("[pieces] chat_stream(): 重试条件没带 pieces（吐过字还会重试）")
        if re_h is not None and any(isinstance(n, ast.Continue) for n in ast.walk(re_h)):
            probs.append(f"[retry] {name}(): RequestError 分支里有 continue（读超时会被重试）")
    return probs


# 正控样本：把"重试写错地方"的形状显式写出来（不依赖真文件当前长什么样）——
# 判据要是认不出这个，那它对真文件的"绿"就一文不值。
_BROKEN_SAMPLE = '''
import httpx

class Broken:
    def _get_client(self):
        return None

    async def chat(self):
        for attempt in range(2):
            try:
                pass
            except httpx.ConnectTimeout as e:
                raise e
            except httpx.RequestError as e:
                if attempt == 0:
                    continue
                raise e

    async def chat_stream(self):
        pieces = []
        for attempt in range(2):
            try:
                pass
            except httpx.ConnectTimeout as e:
                if attempt == 0:
                    continue
                raise e
            except httpx.RequestError as e:
                raise e
            except httpx.HTTPStatusError as e:
                raise e
'''

# 第二份正控：分支顺序颠倒（ConnectTimeout 排在 RequestError 后面 = 永远轮不到它）。
_BROKEN_ORDER = '''
import httpx

class Broken2:
    def _get_client(self):
        return None

    async def chat(self):
        for attempt in range(2):
            try:
                pass
            except httpx.RequestError as e:
                raise e
            except httpx.ConnectTimeout as e:
                if attempt == 0:
                    continue
                raise e
            except httpx.HTTPStatusError as e:
                raise e

    async def chat_stream(self):
        pieces = []
        for attempt in range(2):
            try:
                pass
            except httpx.ConnectTimeout as e:
                if attempt == 0 and not pieces:
                    continue
                raise e
            except httpx.RequestError as e:
                raise e
            except httpx.HTTPStatusError as e:
                raise e
'''

# 第三份正控：超时数值写错（一处退回 3 秒 = 冷连接照样被顶破）。
_BAD_TIMEOUT = ("x = httpx.Timeout(30.0, connect=3.0)\n"
                "y = httpx.Timeout(60.0, connect=8.0)\n")


def batch_a_reuse():
    """A 批 · 真发两次：同一 client、闸门两次、**服务端只看到一条连接**。"""
    c = Checker()
    title("A. 两次调用复用同一条连接（这是本刀唯一要买的东西）")
    import app.llm.client as C

    srv = _serve()
    port = srv.server_address[1]
    old_spy, hits = _spy_ssrf()
    restore_cfg = _point_at_local_server(port)
    _CountingServer.connections = 0
    _CountingServer.requests = 0

    try:
        a1, a2 = asyncio.run(_two_calls())
        c.check("两笔请求确实打到本地假接口（不是偷偷跑去真上游）",
                _CountingServer.requests == 2, f"{_CountingServer.requests} 笔")
        c.check("两次调用都拿到回答（复用没把请求弄坏）",
                a1.get("content") == "pong" and a2.get("content") == "pong",
                f"{a1} / {a2}")
        c.check("服务端只看到**一条** TCP 连接（两条请求复用了 keep-alive）",
                _CountingServer.connections == 1, f"{_CountingServer.connections} 条连接")
        c.check("SSRF 闸门两次都跑了（没因为共享 client 就少跑一次）",
                hits["n"] == 2, f"{hits['n']} 次")
    finally:
        restore_cfg()
        C.check_base_url_ssrf = old_spy
        srv.shutdown()
    return c


async def _two_calls():
    import app.llm.client as C
    cli = C.DeepSeekClient()
    a1 = await cli.chat([{"role": "user", "content": "一"}])
    a2 = await cli.chat([{"role": "user", "content": "二"}])
    return a1, a2


def batch_b_same_instance():
    """B 批 · 同一实例 + 关掉会自动重建（别留一个死 client 在模块里）。"""
    c = Checker()
    title("B. 同一个实例；被关掉之后下一次自动重建")
    import app.llm.client as C

    async def check():
        one = await C._get_client()
        two = await C._get_client()
        same = one is two
        await one.aclose()                      # 模拟"某个循环把 client 关了"
        three = await C._get_client()
        return same, (three is not one and not three.is_closed)

    same, rebuilt = asyncio.run(check())
    c.check("同一进程内 `_get_client()` 拿到的是同一个 client 实例", same, "")
    c.check("旧 client 被关掉后，下一次自动重建（不会拿着死的继续用）", rebuilt, "")

    src = open("app/llm/client.py", encoding="utf-8").read()
    # ⚠️ 形状断言先把注释行剔掉再数 —— 本仓库第三次踩"自己的注释把断言绊红"：
    #    文件头那段注释里写了 `httpx.AsyncClient(...)` 这个字面串，数出来就成了 2 处。
    code_src = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    c.check("全文件只有一处创建 AsyncClient（在共享工厂里，不在 chat 里）",
            code_src.count("httpx.AsyncClient(") == 1
            and "async with httpx.AsyncClient" not in code_src,
            f"{code_src.count('httpx.AsyncClient(')} 处")
    # ★ 2026-11-01（#102）：这条原来是「超时值没动（connect=3.0 还在…）」的字面串判断。
    #   3 秒在冷连接上会被 TCP 重传顶破（当天 12 次全是 3018~3048 ms 才断），connect 已
    #   放宽到 8 秒。判据换性质不换口径：改成读 AST 问"connect 是不是 8 秒、read 还是
    #   30/60 两档" —— 顺带把"注释里写着同样的字眼"这个老坑彻底躲开。
    _probs = _timeout_problems(src)
    c.check("connect 8 秒 / read 30 秒（流式那一路 read 60 秒）",
            not _probs, "; ".join(_probs) or f"{_timeout_calls(src)}")
    for kind in ('kind="auth"', 'kind="rate_limited"', 'kind="unreachable"',
                 'kind="bad_format"', 'kind="no_key"'):
        c.check(f"异常映射一支没少：{kind}", kind in src, "")
    return c


def batch_c_loops():
    """C 批 · 跨事件循环安全（今天实测出来的两条前提，钉住免得以后有人重猜）。"""
    c = Checker()
    title("C. 跨事件循环：换循环不炸（测试里 TestClient / asyncio.run 一次一个循环）")
    import app.llm.client as C

    srv = _serve()
    port = srv.server_address[1]
    old_spy, hits = _spy_ssrf()
    restore_cfg = _point_at_local_server(port)

    async def one():
        return (await C.DeepSeekClient().chat([{"role": "user", "content": "x"}])).get("content")

    async def take():
        return await C._get_client()

    try:
        r1 = asyncio.run(one())        # 循环 1
        r2 = asyncio.run(one())        # 循环 2（同一个共享 client、可能带着池里的连接）
        c.check("两个不同的事件循环各发一次，都成功（不报 Event loop is closed）",
                r1 == "pong" and r2 == "pong", f"{r1} / {r2}")
        c.check("闸门在两个循环里都跑了（共 2 次）", hits["n"] == 2, f"{hits['n']} 次")
        # 这条钉的就是"按事件循环各留一份"本身。退回全进程只留一个（变异 X3），
        # 第二个循环就会拿到第一个那个带着死连接的实例 —— 今天实测是
        # `RuntimeError: Event loop is closed`。
        k1, k2 = asyncio.run(take()), asyncio.run(take())
        c.check("两个循环各留一份（第二个循环不许拿第一个那份）", k1 is not k2,
                f"同一实例? {k1 is k2}")
    except Exception as e:
        c.check("跨事件循环复用不该抛异常", False, f"{type(e).__name__}: {str(e)[:90]}")
    finally:
        restore_cfg()
        C.check_base_url_ssrf = old_spy
        srv.shutdown()
    return c


def batch_d_connect_budget():
    """D 批 · connect 预算 8 秒 + 只在握手超时上重试一次（2026-11-01 #102）。

    甲 行为：假 client 按剧本抛异常，`chat` / `chat_stream` 跑真代码，数"试了几次、
             抛的是哪个 kind、消息末尾带哪个机器标签"；
    乙 形状：AST 读源码，钉住"两处 connect=8 秒、read 30/60 不动、重试只挂
             ConnectTimeout 那一支且只有一次"；
    丙 正控：把写错的形状喂给同一套判据，必须当场红；
    丁 账本：机器标签真落到 `llm_usage.jsonl` 的 error 列（网关那条路，不经人手拼串）。

    为什么不用字面串数：B 批上面记过——注释里的字面串会把计数顶高；而且"这个
    continue 在哪个 except 分支里"这种问题，字面串根本答不上来。
    """
    c = Checker()
    title("D. connect 预算 8 秒；只在握手超时上重试一次（#102）")
    import app.llm.client as C

    # —— 甲 1：握手超时一次 → 重试 → 成功
    fake = _FakeClient([_ct(), _FakeResp()])
    msg, err = _run_chat(fake)
    c.check("chat：握手超时一次后重试，第二次成功（学生看不到那句报警）",
            err is None and (msg or {}).get("content") == "pong",
            f"err={type(err).__name__ if err else None} msg={str(msg)[:60]}")
    c.check("chat：一共只试了 2 次（不重试=1、重试两次=3 都不对）",
            len(fake.calls) == 2, f"{len(fake.calls)} 次")

    # —— 甲 2：两次都超时 → 抬出去；带机器标签、不回显地址
    fake = _FakeClient([_ct(), _ct()])
    msg, err = _run_chat(fake)
    text = str(err or "")
    c.check("chat：连着两次握手超时 → kind=unreachable（上层按这个挑学生那句话）",
            getattr(err, "kind", "") == "unreachable", f"{getattr(err, 'kind', '')!r}")
    c.check("chat：消息末尾带 [CONNECT_TIMEOUT]（llm_usage.jsonl 的 error 存的就是这句）",
            text.endswith("[CONNECT_TIMEOUT]"), text[-40:])
    c.check("chat：最多两次，不多试", len(fake.calls) == 2, f"{len(fake.calls)} 次")
    c.check("chat：消息里不回显上游地址", "api.example.com" not in text, text[:80])

    # —— 甲 3：读超时不重试（重试只挂握手超时）
    fake = _FakeClient([_rt()])
    msg, err = _run_chat(fake)
    text = str(err or "")
    c.check("chat：读超时**不**重试（重试只挂握手超时）",
            len(fake.calls) == 1, f"{len(fake.calls)} 次")
    c.check("chat：读超时带 [READ_TIMEOUT]（与握手超时能分开）",
            text.endswith("[READ_TIMEOUT]"), text[-40:])

    # —— 甲 4：普通连不上（ConnectError）也不重试，且不带任何标签
    fake = _FakeClient([httpx.ConnectError("refused")])
    msg, err = _run_chat(fake)
    c.check("chat：普通连不上（ConnectError）不重试", len(fake.calls) == 1, f"{len(fake.calls)} 次")
    c.check("chat：既不是握手也不是读 ⇒ 末尾不带任何机器标签",
            "[" not in str(err or ""), str(err)[-40:])

    # —— 甲 5：429 不重试（重试只会加重限流）
    fake = _FakeClient([_FakeResp(status_code=429)])
    msg, err = _run_chat(fake)
    c.check("chat：429 不重试、kind=rate_limited",
            getattr(err, "kind", "") == "rate_limited" and len(fake.calls) == 1,
            f"{getattr(err, 'kind', '')!r} / {len(fake.calls)} 次")

    # —— 甲 6：流式同口径
    fake = _FakeClient([_ct(), _FakeStream(_sse(["po", "ng"]))])
    out, err = _run_stream(fake)
    c.check("流式：握手超时一次后重试，字照常吐出来",
            err is None and "".join(out) == "pong",
            f"err={type(err).__name__ if err else None} out={out}")
    c.check("流式：也只试 2 次", len(fake.streams) == 2, f"{len(fake.streams)} 次")

    fake = _FakeClient([_ct(), _ct()])
    out, err = _run_stream(fake)
    c.check("流式：两次握手超时 → unreachable + [CONNECT_TIMEOUT]",
            getattr(err, "kind", "") == "unreachable"
            and str(err).endswith("[CONNECT_TIMEOUT]"),
            f"{getattr(err, 'kind', '')!r} {str(err)[-30:]}")
    c.check("流式：最多两次", len(fake.streams) == 2, f"{len(fake.streams)} 次")

    fake = _FakeClient([_rt()])
    out, err = _run_stream(fake)
    c.check("流式：读超时不重试 + [READ_TIMEOUT]",
            len(fake.streams) == 1 and str(err).endswith("[READ_TIMEOUT]"),
            f"{len(fake.streams)} 次 / {str(err)[-30:]}")

    # —— 甲 7：已经吐过字之后再断 → 不许重试（否则同一句话在屏幕上放两遍）
    fake = _FakeClient([_FakeStream(_sse(["po"], tail=_ct()))])
    out, err = _run_stream(fake)
    c.check("流式：已经吐过字之后再出握手错 ⇒ 不再重试",
            out == ["po"] and getattr(err, "kind", "") == "unreachable"
            and len(fake.streams) == 1,
            f"out={out} kind={getattr(err, 'kind', '')!r} {len(fake.streams)} 次")

    # —— 乙：静态形状（真文件）
    src = open("app/llm/client.py", encoding="utf-8").read()
    calls = _timeout_calls(src)
    c.check("静态：全文件恰有两处 Timeout 构造（共享工厂 + 流式那一路）",
            len(calls) == 2, f"{calls}")
    c.check("静态：两处 connect 都是 8 秒（漏一处，冷连接照样被顶破）",
            [k for _, k in calls] == [8.0, 8.0], f"{calls}")
    c.check("静态：read 仍是 30 秒 / 60 秒两档（放宽 connect 没顺手拉长等回答那一半）",
            [r for r, _ in calls] == [30.0, 60.0], f"{calls}")

    wiring = _retry_wiring_problems(src)
    c.check("静态：chat / chat_stream 各一个重试循环，都是 `for attempt in range(2)`",
            not [p for p in wiring if p.startswith("[loop]")], "; ".join(wiring))
    c.check("静态：ConnectTimeout 分支排在 RequestError 之前（排后面永远轮不到它）",
            not [p for p in wiring if p.startswith("[order]")], "; ".join(wiring))
    c.check("静态：continue 只在 ConnectTimeout 那一支（挪到 RequestError 上读超时也会重试）",
            not [p for p in wiring if p.startswith("[retry]")], "; ".join(wiring))
    c.check("静态：流式只在「一个字都没吐」时重试（否则同一句话会放两遍）",
            not [p for p in wiring if p.startswith("[pieces]")], "; ".join(wiring))
    c.check("静态：重试没跑进 `_get_client()`（它只负责建 client，重试是调用点的事）",
            not [p for p in wiring if p.startswith("[factory]")], "; ".join(wiring))

    # —— 丙：正控（把写错的形状喂给同一套判据，必须当场红）
    broken = _retry_wiring_problems(_BROKEN_SAMPLE)
    c.check("正控：重试写在 RequestError 上 → 判据红（命门这一条必须能响）",
            any("RequestError 分支里有 continue" in p for p in broken), "; ".join(broken))
    c.check("正控：ConnectTimeout 分支里没有 continue → 判据红",
            any("ConnectTimeout 分支里没有受条件保护的 continue" in p for p in broken),
            "; ".join(broken))
    c.check("正控：流式重试条件没带 pieces → 判据红",
            any(p.startswith("[pieces]") for p in broken), "; ".join(broken))
    c.check("正控：重试次数写成 range(3) → 判据红",
            any(p.startswith("[loop]") and "range(2)" in p
                for p in _retry_wiring_problems(_BROKEN_SAMPLE.replace("range(2)", "range(3)", 1))),
            "")
    c.check("正控：分支顺序颠倒 → 判据红",
            any(p.startswith("[order]") for p in _retry_wiring_problems(_BROKEN_ORDER)), "")
    bad_t = _timeout_problems(_BAD_TIMEOUT)
    c.check("正控：connect 写回 3 秒 → 判据红",
            any(p.startswith("[connect]") for p in bad_t), "; ".join(bad_t))

    # —— 丁：机器标签真落到账本里（网关记账那条路）
    from app.llm.gateway import ModelGateway

    async def drive_fail_chat():
        gw = ModelGateway(user="内部:测试", client=C.DeepSeekClient())
        with _patched_client(_FakeClient([_ct(), _ct()])):
            try:
                await gw.chat([{"role": "user", "content": "x"}])
            except Exception:
                pass

    async def drive_fail_stream():
        gw = ModelGateway(user="内部:测试", client=C.DeepSeekClient())
        with _patched_client(_FakeClient([_rt()])):
            try:
                async for _ in gw.stream([{"role": "user", "content": "x"}]):
                    pass
            except Exception:
                pass

    async def drive_ok():
        gw = ModelGateway(user="内部:测试", client=C.DeepSeekClient())
        with _patched_client(_FakeClient([_FakeResp()])):
            await gw.chat([{"role": "user", "content": "x"}])

    with _temp_usage_log() as log_path:
        asyncio.run(drive_fail_chat())
        asyncio.run(drive_fail_stream())
        asyncio.run(drive_ok())
        rows = _read_rows(log_path)

    c.check("账本：握手超时那笔的 error 带 [CONNECT_TIMEOUT]（管理员能分清是哪一种超时）",
            any("[CONNECT_TIMEOUT]" in str(r.get("error")) for r in rows),
            str([str(r.get("error"))[-24:] for r in rows])[:160])
    c.check("账本：流式读超时那笔带 [READ_TIMEOUT] 且标了 stream=1",
            any("[READ_TIMEOUT]" in str(r.get("error")) and r.get("stream") == 1 for r in rows),
            str([(r.get("stream"), str(r.get("error"))[-16:]) for r in rows])[:160])
    c.check("账本：成功那笔 error 仍是空串（标签只挂在失败上）",
            any(r.get("status") == "ok" and r.get("error") == "" for r in rows),
            str([(r.get("status"), r.get("error")) for r in rows])[:160])

    # —— 戊：学生那一句话术不许被机器标签污染（它只进管理端账本）
    from app.main import _STUDENT_LLM_WORDS
    words = json.dumps(_STUDENT_LLM_WORDS, ensure_ascii=False)
    c.check("学生话术表里没有任何机器标签（[CONNECT_TIMEOUT] 只给管理员看）",
            "CONNECT_TIMEOUT" not in words and "READ_TIMEOUT" not in words,
            words[:100])
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_47_shared_async_client：共享连接，别每次重握手")
    print("=" * 60)
    code |= batch_a_reuse().summary("A 批（真发两次看连接数）")
    code |= batch_b_same_instance().summary("B 批（同一实例 + 形状）")
    code |= batch_c_loops().summary("C 批（跨循环）")
    code |= batch_d_connect_budget().summary("D 批（connect 8 秒 + 重试一次）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
