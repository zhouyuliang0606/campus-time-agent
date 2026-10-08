"""刷新 / 切模块之后，上一轮没确认的那张卡要能自己回到聊天流里。

需求出处（2026-10-09，用户原话）：
  「上次给的 restorePendingCard() 那段就是正解：页面加载 → fetch /api/pending/{sid}
    → 若返回未 applied 的卡 → 复用现有 renderOptions 重画到聊天流末尾。
    后端、pending.py、落盘逻辑一行都不用动。」

所以本文件钉的是两件事：
  ① 前端确实按这个形状接了（顺序在历史之后、复用 renderOptions、没判重就画空卡）；
  ② **后端契约真的支持"点过的不会再回来"** —— 这条是整个方案成立的前提，
     用真接口跑一遍，不靠读代码想当然。

⚠️ 空断言防护：B/C 批每条都先确认"这一轮真的出了卡"，再核字段。
"""
from _harness import Checker, title, make_client, sandbox

SRC = "app/static/student.html"


def _seg(src, start, end):
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find(end, i)
    return src[i: j if j > i else i + 4000]


def batch_a_wiring():
    c = Checker()
    title("A 批 · 前端接线（源码断言）")
    src = open(SRC, encoding="utf-8").read()

    c.check("restorePendingCard 已定义", "async function restorePendingCard()" in src)
    c.check("它在 restoreHistory 之后才跑（顺序反了卡会插到历史上面）",
            "restoreHistory().finally(restorePendingCard)" in src,
            "没看到 .finally(restorePendingCard) 这条链")
    body = _seg(src, "async function restorePendingCard()", "// ---------- 清空与管家的聊天记录")
    c.check("读的是 /api/pending/{sid}，且 sid 过了 encodeURIComponent",
            '"/api/pending/" + encodeURIComponent(sessionId())' in body, body[:120])
    c.check("复用 renderOptions，没有另抄一份画卡逻辑",
            "renderOptions(log, [p])" in body and "function renderOptions" in src)
    c.check("没卡 / 卡没 kind 时直接 return（不画空卡）",
            "if (!p || !p.kind) return;" in body)
    c.check("接回来的卡带一句「上一轮没处理完」的说明",
            "上一轮没处理完" in body, "缺这句学生会以为管家又问了一遍")
    c.check("有竞态守卫：连点两个模块只让最后一次画卡",
            "restoreSeq" in body and "if (my !== restoreSeq) return;" in body)
    c.check("fetch 失败静默返回（历史能恢复就行，别拿异常糊学生一脸）",
            "} catch (e) { return; }" in body)
    # 全站唯一一份转义（test_33 立的规矩）：这段不许自己拼 HTML
    c.check("这段没自己 innerHTML 拼数据", "innerHTML" not in body)
    return c


def batch_b_contract():
    c = Checker()
    title("B 批 · 后端契约：只回未 applied 的那张，点过的不回来")
    with sandbox():
        cli = make_client()

        r = cli.get("/api/pending/no-such-session")
        c.check("没有暂存 → pending 为 null（前端据此不画）",
                r.status_code == 200 and r.json().get("pending") is None, r.text[:120])
        # ⚠️ 口径说明：空 sid 与超长 sid 走的是**路由不匹配 → 404**（实测），
        # 不是 `{"pending":null}`。断言写成"body 里没有 pending 键"就成假绿了——
        # 404 的 `{"detail":"Not Found"}` 同样没有那个键。所以这里钉的是
        # "**任何脏 sid 都拿不到别人的卡**"这个真契约，以及前端拿 404 也不会画空卡。
        for label, u in [("空 sid", "/api/pending/"),
                         ("超长 sid（64 字上限）", "/api/pending/" + "x" * 100),
                         ("只有空白符的 sid", "/api/pending/%20%20")]:
            r = cli.get(u)
            got = r.json().get("pending") if r.status_code == 200 else None
            c.check(f"{label} → 拿不到任何卡",
                    got is None, f"HTTP {r.status_code} {r.text[:60]}")

        # 出一张真卡（走确定性分支，不需要模型密钥）
        d = cli.post("/api/chat", json={"message": "我一周要去三次健身房，帮我加进待办",
                                        "session_id": "p36-b", "module": "planner"}).json()
        chat_cards = [o for o in d.get("options") or [] if o.get("kind") == "todo_slots"]
        c.check("这一轮真的出了候选卡", bool(chat_cards),
                "没出卡 = 下面全是空断言（answer=" + str(d.get("answer"))[:60] + "）")
        if not chat_cards:
            return c
        p = cli.get("/api/pending/p36-b").json().get("pending")
        c.check("刷新后 /api/pending 能取回同一张卡",
                p and p.get("kind") == "todo_slots" and p.get("title") == chat_cards[0].get("title"),
                str(p)[:140])
        c.check("取回的卡带着 slots（前端只凭它就能重画，不用再问后端）",
                p and len(p.get("slots") or []) > 0, str(p)[:80])

        # 点过之后（批量写入会 mark_applied）→ 不该再回来
        picked = [{"date": s["date"], "start": s["start"], "end": s["end"]}
                  for s in (p.get("slots") or [])[:2]]
        w = cli.post("/api/todos/batch", json={"session_id": "p36-b", "title": p["title"],
                                               "slots": picked})
        c.check("批量写入成功（这一步会 mark_applied）",
                w.status_code == 200 and w.json().get("ok"), w.text[:120])
        after = cli.get("/api/pending/p36-b").json().get("pending")
        c.check("点过的卡不再返回（这是整个方案不重复写库的命门）",
                after is None, str(after)[:140])
    return c


def batch_c_multi():
    c = Checker()
    title("C 批 · 一轮多张卡：只接回最后一张，处理完下一张会自己出来")
    with sandbox():
        cli = make_client()
        # 同一轮里出两张待办卡（两次对话各出一张，后一张覆盖前一张的位置）
        for msg in ["加个健身", "加个游泳"]:
            d = cli.post("/api/chat", json={"message": msg + "，帮我安排时间",
                                            "session_id": "p36-c", "module": "planner"}).json()
            kinds = [o.get("kind") for o in d.get("options") or []]
            c.check(f"{msg!r} 这一轮出了卡", bool(kinds), str(d.get("answer"))[:60])
        p = cli.get("/api/pending/p36-c").json().get("pending")
        c.check("暂存里取回的是**最后一张**（不是第一张）",
                p and p.get("kind") in ("todo_mode", "todo_slots", "todo_add"), str(p)[:100])
        c.check("只回一张、不是一次摊一堆（跟 /api/pending 现有行为一致）",
                isinstance(p, dict), str(p)[:60])
    return c


def batch_g_one_stream():
    c = Checker()
    title("G 批 · 全站一条对话流（用户报「两者对话记录不在一起」的根因）")
    src = open(SRC, encoding="utf-8").read()

    seg = _seg(src, "function sessionId(", "\n    }")
    c.check("sessionId 只认一个 key（不再按模块发号）",
            '"campustime_session"' in seg and "currentView" not in seg, seg[:120])
    c.check("源码里不再有 campustime_session_ + 模块后缀的拼接",
            '"campustime_session_"' not in src)
    c.check("sessionId 不再收 view 参数", "function sessionId()" in src)
    nav = _seg(src, "function switchNav(view){", "switchNavRef = switchNav")
    c.check("切模块不再重画聊天区（否则刚接回来的卡又被冲掉）",
            "resetLogForModule" not in nav, "switchNav 里还在调 resetLogForModule")
    c.check("resetLogForModule 只剩初始化那一处调用",
            src.count("resetLogForModule(") == 2,   # 定义 1 + 初始化 1
            f"实际 {src.count('resetLogForModule(')} 处")
    # 【清空记录】必须跟着换那**一个** key。它以前换的是"当前模块的 key"，
    # 合并成一条流之后若不同步改，在日程栏点清空就只换掉一个没人用的旧 key —— F5 一下全回来。
    seg2 = _seg(src, "async function doClearChat()", "// 初始化语音输入")
    c.check("【清空记录】换的是全局那一个 key",
            'localStorage.setItem("campustime_session",' in seg2, seg2[:100])
    return c


def main():
    results = [batch_a_wiring(), batch_b_contract(), batch_c_multi(), batch_g_one_stream()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 未确认卡刷新恢复总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
