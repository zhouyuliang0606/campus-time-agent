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
"""
import asyncio
import http.server
import json
import os
import threading

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
        self.rfile.read(n)
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
    c.check("超时值没动（connect=3.0 还在，read 30 秒还在）",
            "Timeout(30.0, connect=3.0)" in code_src, "")
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


def main():
    global code
    print("\n" + "=" * 60)
    print("test_47_shared_async_client：共享连接，别每次重握手")
    print("=" * 60)
    code |= batch_a_reuse().summary("A 批（真发两次看连接数）")
    code |= batch_b_same_instance().summary("B 批（同一实例 + 形状）")
    code |= batch_c_loops().summary("C 批（跨循环）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
