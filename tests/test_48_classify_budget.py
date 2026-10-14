"""刀 3（A + B）：`max_tokens` 一路透传到判定层，并给判定装一根**会落地的**保险丝。

施工单 11 号 §2.1 A：`client.chat` 加 `max_tokens` → 网关两处透传 → `intent` 传一个预算。
它自己的诚实话也写在 §2.1：这顶帽**不提速**（生成时长由 TTFT + 实际输出长度决定），
价值是"模型万一啰嗦/跑偏就硬截 + 顺手省钱" —— 所以本文件不拿它当延迟证据。

§2.1 估的"合法输出约 38 token、预算给 64"与实测不符，本仓按实测改成 **128**：
`app/data/llm_usage.jsonl` 里 882 笔打到上游且成功的语义判定，completion_tokens
p50=43 / p90=57 / p99=86 / 最大 166 —— **64 会硬截掉 25 笔（2.8%）的正常判定输出**，
那些句子就此静默掉回关键词表（是掉质量，不是省钱）。128 只截最跑偏的 0.2%。

§2.2 B 原写「超时收紧到 2.0~2.5s，判定质量零损」—— **这条与实测矛盾，用户 2026-10-14 改判 6s**：
同样这 882 笔的 latency_ms，p50=4.2s、p90=6.0s、最长 9.1s，**767 笔（87%）超过 2.5s**。
设 2.5s 不是提速，是把 87% 的判定**静默作废**、掉回关键词表。超时值最终取 **6.0s**：
p50 之上、只砍最慢的一档（91 笔 = 10.3%），同时终结"没有超时 = 最坏占住客户端 30 秒读超时"
这个真实风险。"失败/超时 → None → 掉回规则" 的语义（`main.py:1284-1285`）不变。

不做的：C 缩判定 prompt（用户拍板跳过）、流式（另单）、合并判定与引擎首调用（另单）。
"""
import asyncio
import json
import time

from _harness import Checker, make_client, sandbox, title
# 复用 test_47 那套本地假大模型接口与打桩（别抄第二份）
from test_47_shared_async_client import (
    _CountingServer, _point_at_local_server, _serve, _spy_ssrf,
)

code = 0

VERDICT = json.dumps({"module": "planner", "intent": "add_todo", "title": "游泳",
                      "date": "", "time": "", "duration": "", "is_a_question": False,
                      "needs_student_detail": False, "confidence": 0.9},
                     ensure_ascii=False)


def _serve_with_body():
    _CountingServer.bodies = []
    return _serve()


def batch_a_max_tokens():
    """A 批 · max_tokens 真发出去了吗（看请求体，不看函数签名）。"""
    c = Checker()
    title("A. max_tokens 一路透传：判定带上它，别人不许被它截断")
    import app.llm.client as C
    from app.llm.gateway import ModelGateway
    from app.llm import gateway as GW

    srv = _serve_with_body()
    port = srv.server_address[1]
    old_spy, _hits = _spy_ssrf()
    restore = _point_at_local_server(port)
    msgs = [{"role": "user", "content": "hi"}]

    def send_and_get_body(label, fn):
        """发一次并回最后一个请求体；参数还不支持时（改前）把 TypeError 当现场值报出来。"""
        _CountingServer.bodies.clear()
        try:
            asyncio.run(fn())
        except TypeError as e:
            return {"__error__": str(e)[:60]}
        return _CountingServer.bodies[-1] if _CountingServer.bodies else {}

    try:
        b1 = send_and_get_body("client", lambda: C.DeepSeekClient().chat(msgs, max_tokens=128))
        c.check("client.chat(max_tokens=128) → 请求体里真有 max_tokens=128",
                b1.get("max_tokens") == 128, json.dumps(b1, ensure_ascii=False)[:90])

        b2 = send_and_get_body("client-no-kw", lambda: C.DeepSeekClient().chat(msgs))
        c.check("不传就不该出现 max_tokens（引擎/润色的输出比这预算长，不能被顺手截断）",
                "max_tokens" not in b2, json.dumps(list(b2), ensure_ascii=False)[:80])

        b3 = send_and_get_body("gateway", lambda: ModelGateway(user="内部:测试").chat(
            msgs, max_tokens=128))
        c.check("ModelGateway.chat 透传到位", b3.get("max_tokens") == 128,
                json.dumps(b3, ensure_ascii=False)[:90])

        b4 = send_and_get_body("gateway-fn", lambda: GW.chat(
            msgs, user="内部:测试", max_tokens=128))
        c.check("模块级 gateway.chat 也透传（三处签名一路通）", b4.get("max_tokens") == 128,
                json.dumps(b4, ensure_ascii=False)[:90])
    finally:
        restore()
        C.check_base_url_ssrf = old_spy
        srv.shutdown()

    src = open("app/agent/intent.py", encoding="utf-8").read()
    # 剔掉注释再比形状：否则注释里写一个 "max_tokens = 8" 都能把这条判红（本项目踩过两次）。
    src_code = "\n".join(line.split("#")[0] for line in src.splitlines())
    c.check("判定层的预算写成常量 = 128（64 会硬截掉 2.8% 的正常判定，见文件头实测）",
            "max_tokens=CLASSIFY_MAX_TOKENS" in src_code
            and "CLASSIFY_MAX_TOKENS = 128" in src_code, "")
    c.check("超时值写在 intent 层（不是散在调用方）",
            src.count("CLASSIFY_TIMEOUT") >= 2, f"{src.count('CLASSIFY_TIMEOUT')} 处")
    return c


def batch_b_fuse():
    """B 批 · 保险丝：慢成 8 秒的判定必须被 6s 砍掉；3.5 秒的（p50 档）不许误杀。"""
    c = Checker()
    title("B. 判定超时：8s 被砍、3.5s 放过（今天最坏能占 30 秒）")
    from app.agent import intent as I
    import app.llm.client as C
    from test_20_polish import _FakeLLM, _patch_client

    async def run(delay):
        r = _patch_client(VERDICT, with_key=True, delay=delay)
        try:
            t0 = time.perf_counter()
            got = await I.classify_with_llm("帮我安排游泳", [])
            return (time.perf_counter() - t0) * 1000, got
        finally:
            r()

    # 慢过阈值 → 必须回 None（掉回规则），且不能等满客户端那 30 秒
    ms_slow, got_slow = asyncio.run(run(8.0))
    c.check("判定 8s 没回 → 6s 保险丝砍掉、返回 None（掉回规则，不是报错）",
            got_slow is None, repr(got_slow)[:40])
    c.check("而且真的只等了 6~7 秒（不是客户端那 30 秒读超时）",
            5500 < ms_slow < 7200, f"{ms_slow:.0f} ms")

    # p50 那一档（3.5s）必须放过，否则就是在砍判定质量
    ms_ok, got_ok = asyncio.run(run(3.5))
    c.check("3.5s 的判定（实测 p50 档）正常返回，6s 不误杀",
            bool(got_ok) and (got_ok or {}).get("intent") == "add_todo",
            json.dumps(got_ok, ensure_ascii=False)[:70])
    c.check("耗时确实等了模型（没被误砍）", ms_ok > 3000, f"{ms_ok:.0f} ms")
    # 上一行证明"等到了"，这一行证明"模型那头真收到了预算"——
    # 光看 intent.py 的源码形状抓不到"网关中途把参数丢了"这类回归。
    # ⚠️ 这里必须写死 128，不许写 I.CLASSIFY_MAX_TOKENS：拿被测代码自己的常量当期望值 = 断言自证，
    #    把常量改成 8（会把合法 JSON 截断）时这条照样绿。
    c.check("判定这一跳真把 max_tokens=128 交给了模型（假货收到的值）",
            _FakeLLM.last.get("max_tokens") == 128,
            json.dumps(_FakeLLM.last, ensure_ascii=False))
    return c


def batch_c_gate():
    """C 批 · 早退门不许动（施工单 §3.1）：规则接得住的句子，一次判定都不许多打。"""
    c = Checker()
    title("C. 规则已接住 → 根本不进判定（别为了并行把这道门改松）")
    import app.main as M

    calls = {"n": 0}
    real_classify = M.classify

    async def spy(*a, **k):
        calls["n"] += 1
        return await real_classify(*a, **k)

    with sandbox():
        client = make_client()
        M.classify = spy
        try:
            # 规则层直接出卡的那类（加待办 + 报了事名）：判定不该被叫
            calls["n"] = 0
            client.post("/api/chat", json={"message": "帮我安排游泳", "module": "planner",
                                           "session_id": "t48-gate-1"})
            c.check("「帮我安排游泳」（规则接住）→ 判定被叫 0 次",
                    calls["n"] == 0, f"{calls['n']} 次")

            # 规则读不出的那类（问句、没有意图词）：判定该被叫，且只叫一次
            calls["n"] = 0
            client.post("/api/chat", json={"message": "校车在哪里坐", "module": "faq",
                                           "session_id": "t48-gate-2"})
            c.check("「校车在哪里坐」（规则读不出）→ 判定被叫 1 次",
                    calls["n"] == 1, f"{calls['n']} 次")
        finally:
            M.classify = real_classify
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_48_classify_budget：max_tokens 透传 + 判定 6s 保险丝")
    print("=" * 60)
    code |= batch_a_max_tokens().summary("A 批（max_tokens）")
    code |= batch_b_fuse().summary("B 批（保险丝）")
    code |= batch_c_gate().summary("C 批（早退门）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
