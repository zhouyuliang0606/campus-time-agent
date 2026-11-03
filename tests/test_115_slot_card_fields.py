# -*- coding: utf-8 -*-
"""#115：模型自己拼的那几张候选卡必须补成"前端画得出来"的形状。

起因（2026-11-03 20:58 用户截图）：「去图书馆」三段候选，屏幕上三行全是
`2026-11-04（）11:45-13:45 · 约 ? 分钟` —— 括号里没星期、时长是问号，而且
**没有确认条弹窗**，只有三颗 checkbox 和一颗【确认所选】。
勾两条发出去只生效一条。

根因是"模型挑了哪只工具"，不是渲染时机：795 笔轨迹里 41 轮出加待办的卡，
`propose_slots` 24 轮（交出来的是裸 JSON，一个 `kind` 都没有）、
`propose_todo_tool` / `propose_todo_slots_tool` / `propose_todo_change` 17 轮（都带 kind）。
带 kind 的那 17 轮弹窗正常，裸的 24 轮全不正常 ⇒ "时好时坏"是结构性的。

裸 JSON 往下掉的两处后果（本文件逐条钉住）：
  ① 前端 `renderOptions` 每一档都是 `options.filter(o => o.kind === "X")`，
     没 kind 就一路穿透到 `student.html` 那个 `drawable` 兜底（#59 留的老内联勾选框），
     `openConfirm` 根本没被调用 ⇒ 没有弹窗；
  ② `main.py:_collapse_same_title_adds`（#75 造的收敛器）分组条件是
     `kind != "todo_add"` 就跳过 ⇒ 这条路**一次也没被收敛过**，屏幕上就是几张裸卡；
     而【确认所选】把勾选拼成 `确认 日期 起-止 标题\\n确认 ...` 一句发出去，
     后端 `_match_todo_pick` 只 `parse_add_todo` 抠**一组**日期时间 ⇒ 只写第一条。

所以修法只有一个动作：在引擎捕获处把缺的字段补齐（`kind` / `weekday` / `minutes` / `title`）。
补了 kind 以后 #75 那个收敛器第一次接得上这条路，几张同名裸卡并成一张 `todo_slots`，
前端 `slotsCards` 那一支走确认条 + 多选 + `/api/todos/batch` —— ① ② 一起没。

两处偏离施工单 115 的地方（都按实测改的，见快照）：
  · 单据写 `if d and not item.get("weekday"): item["weekday"] = _weekday_name(d)`。
    实测 `_weekday_name` 对认不出的日期（`2026-11-32`、`2026-11-5`）回的是**「未知」**
    —— 那等于替系统编一个它不知道的星期，比空着更糟。改成"算得出来才填"（A6 钉）。
  · 单据写 `if m > 0`。实测 `to_minutes("99:00")` 不报错、算出 5940，于是起止写反或
    写歪时减法照样得正数，填进去就是一句「约 5340 分钟」的假话。加了上限一天（A5 钉）。
"""
import asyncio
import datetime
import io
import json
import os
import sys

from _harness import Checker, make_client, sandbox, seed_todos, wipe_todos

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

WD = "一二三四五六日"


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("\r\n", "\n")


def _d(off: int) -> str:
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()


def _wd(off: int) -> str:
    """那一天对应的「周X」（**按当天算**，写死就是 #90 那个老毛病）。"""
    return "周" + WD[(datetime.date.today() + datetime.timedelta(days=off)).isoweekday() - 1]


def bare(off_a=3, off_b=4, off_c=5, title="去图书馆"):
    """三条**裸卡**——照模型实际交出来的形状造（只有 title/date/start/end，没 kind）。"""
    return [
        {"title": title, "date": _d(off_a), "start": "11:45", "end": "13:45", "note": "上午空档"},
        {"title": title, "date": _d(off_b), "start": "11:45", "end": "13:45"},
        {"title": title, "date": _d(off_c), "start": "13:00", "end": "15:00"},
    ]


def main():
    c = Checker()
    from app.modules.planner import normalize_slot_cards as N

    # ───────────────────── A 批：补字段本体 ─────────────────────
    print("\n[A] normalize_slot_cards 把缺的字段补上")

    out = N(bare())
    c.check("A1 三条都补出了 kind（补不上就永远穿到 drawable 那一档）",
            all(o.get("kind") == "todo_add" for o in out) and len(out) == 3,
            str([o.get("kind") for o in out]))

    # ★ 正控：三个日期各自算出的周几**必须互不相同**，且各等于自己那一天的。
    #   只验"weekday 不为空"的话，把它写成常量「周六」也能过。
    c.check("A2 weekday 按那一天算出来的（三条各指各的，不是同一个值）",
            [o.get("weekday") for o in out] == [_wd(3), _wd(4), _wd(5)]
            and len({o.get("weekday") for o in out}) == 3,
            str([o.get("weekday") for o in out]) + " | 期望 "
            + str([_wd(3), _wd(4), _wd(5)]))

    # 换一组日期再算一遍：常量写法在这一条会红（上一组碰巧对上不算数）
    out2 = N(bare(off_a=1, off_b=2, off_c=6))
    c.check("A2-bis 换一组日期，weekday 跟着变（防写死）",
            [o.get("weekday") for o in out2] == [_wd(1), _wd(2), _wd(6)],
            str([o.get("weekday") for o in out2]))

    c.check("A3 minutes = 止 - 起（11:45→13:45 是 120，13:00→15:00 也是 120）",
            [o.get("minutes") for o in out] == [120, 120, 120],
            str([o.get("minutes") for o in out]))

    diff = N([{"title": "x", "date": _d(3), "start": "09:00", "end": "10:30"},
              {"title": "x", "date": _d(4), "start": "13:00", "end": "15:00"}])
    c.check("A3-bis 不同时长各算各的（不是拿第一条的时长盖全部）",
            [o.get("minutes") for o in diff] == [90, 120],
            str([o.get("minutes") for o in diff]))

    rev = N([{"title": "x", "date": _d(3), "start": "15:00", "end": "09:00"}])
    c.check("A4 起止写反 → **不许**填 minutes（负数比空着更糟）",
            "minutes" not in rev[0], json.dumps(rev, ensure_ascii=False))

    wild = N([{"title": "x", "date": _d(3), "start": "09:00", "end": "99:00"}])
    c.check("A5 止写成 99:00（to_minutes 不报错、算出 5940）→ 不许填「约 5340 分钟」",
            "minutes" not in wild[0], json.dumps(wild, ensure_ascii=False))

    bad = N([{"title": "x", "date": "2026-11-32", "start": "09:00", "end": "10:00"},
             {"title": "x", "date": "2026-11-5", "start": "09:00", "end": "10:00"}])
    c.check("A6 认不出的日期 → weekday **留空**，不许填「未知」（那是替系统编一个它不知道的星期）",
            all("weekday" not in o or not o["weekday"] for o in bad),
            json.dumps(bad, ensure_ascii=False))

    keep = N([{"title": "x", "date": _d(3), "start": "09:00", "end": "10:00",
               "weekday": "星期六", "minutes": 999}])
    c.check("A7 模型已经写了 weekday/minutes 的 → 不覆盖（只补缺的，不替它改）",
            keep[0].get("weekday") == "星期六" and keep[0].get("minutes") == 999,
            json.dumps(keep, ensure_ascii=False))

    c.check("A8 没标题的补成「待办」（前端 `o.title || \"待办\"` 那一档在源头就补齐）",
            N([{"date": _d(3), "start": "09:00", "end": "10:00"}])[0].get("title") == "待办")
    c.check("A8-bis 空标题字符串也算没标题",
            N([{"title": "", "date": _d(3), "start": "09:00", "end": "10:00"}])[0].get("title") == "待办")

    junk = N(["不是字典", {"title": "ok", "date": _d(3), "start": "09:00", "end": "10:00"}, None])
    c.check("A9 列表里混进非字典 → 丢掉那一项，不许整批崩",
            len(junk) == 1 and junk[0].get("title") == "ok", json.dumps(junk, ensure_ascii=False))
    c.check("A9-bis 空列表 / None 都回空列表（引擎传进来什么都可能是）",
            N([]) == [] and N(None) == [])

    # ★ 这条一开始是**哑弹**：写成 `"kind" not in bare(3, 4, 5)[0]` 时，`bare()` 每次调用
    #   都现造一份新列表，验的根本不是传进去那个对象 —— 把 `item = dict(c)` 改成 `item = c`
    #   （原地改）也照样绿。必须先把那份列表存下来，再拿同一份去断言。
    src = bare()
    N(src)
    c.check("A10 不改调用方传进来的那份（引擎后面还要拿它做别的判断）",
            all("kind" not in o for o in src),
            json.dumps(src[0], ensure_ascii=False))

    # ───────────────────── B 批：收敛真的发生了 ─────────────────────
    print("\n[B] 补上 kind 以后，#75 那个收敛器第一次接得上这条路")
    import app.main as M

    with sandbox():
        wipe_todos()
        got = M._remember_todo_options("s115-collapse", N(bare()))
        c.check("B1 ★三条同名裸卡 → 屏幕上**只剩一张** todo_slots（M1 打在这一条：删掉 kind 就退回三张裸卡）",
                len(got) == 1 and got[0].get("kind") == "todo_slots",
                f"张数={len(got)} kinds={[o.get('kind') for o in got]}")

        slots = (got[0].get("slots") or []) if got else []
        c.check("B2 收敛出来的每一段都带 label/weekday/minutes（前端那一支本来就画得出来）",
                len(slots) == 3 and all(s.get("label") and s.get("weekday") and s.get("minutes")
                                        for s in slots),
                json.dumps(slots, ensure_ascii=False)[:220])
        c.check("B2-bis label 里念的是**那一天**的周几（不是整张卡共用一个）",
                [s.get("label", "")[:2] for s in slots] == [_wd(3), _wd(4), _wd(5)],
                str([s.get("label") for s in slots]))

        from app.agent.pending import peek_pending
        pend = (peek_pending("s115-collapse") or {}).get("options") or []
        c.check("B3 暂存里那张和屏幕上那张是**同一张**（点确认时系统认的才是他看到的那张）",
                len(pend) == 1 and pend[0].get("kind") == "todo_slots"
                and len(pend[0].get("slots") or []) == 3,
                str([(o.get("kind"), len(o.get("slots") or [])) for o in pend]))

        one = M._remember_todo_options("s115-single", N(bare()[:1]))
        c.check("B4 只有一段时**不许**包成 todo_slots（留一张 todo_add，前端走单条确认条那一支）",
                len(one) == 1 and one[0].get("kind") == "todo_add",
                str([o.get("kind") for o in one]))

        two = M._remember_todo_options("s115-two", N([
            {"title": "去图书馆", "date": _d(3), "start": "11:45", "end": "13:45"},
            {"title": "打篮球", "date": _d(4), "start": "15:00", "end": "16:30"}]))
        c.check("B5 不同名的两张 → 各留一张（全合并会吃掉一件事，#75 那条约束在这条路上同样成立）",
                len(two) == 2 and {o.get("title") for o in two} == {"去图书馆", "打篮球"},
                json.dumps([(o.get("kind"), o.get("title")) for o in two], ensure_ascii=False))

        c.check("B6 一张都没补出 kind 的话收敛不发生 → 这条用来当 B1 的反证（形状不同判据必须不同）",
                len(M._remember_todo_options("s115-raw", bare())) == 3,
                "裸卡原样进去还是三张（证明 B1 靠的是补出来的 kind，不是别的）")

    # ───────────────────── C 批：引擎那一路真的接上了 ─────────────────────
    print("\n[C] 捕获处（engine.py）—— 这条是唯一的改动点")

    eng_src = read("app/agent/engine.py")
    c.check("C1 `self.options = raw` 这个写法已经不在引擎里了（谁改回去，弹窗当场回到裸卡）",
            "self.options = raw" not in eng_src,
            "engine.py 里还留着原样赋值")
    c.check("C2 引擎捕获处调的是 normalize_slot_cards（不是只写在 planner 里没人读）",
            "normalize_slot_cards(raw)" in eng_src
            and eng_src.count("normalize_slot_cards") >= 2,
            "出现次数=" + str(eng_src.count("normalize_slot_cards")))

    # 真引擎 + 假网关：让模型"调一次 propose_slots 交三条裸卡"，看引擎手里那份变成什么
    from app.agent.engine import AgentEngine
    from app.llm.gateway import ModelGateway
    from app.modules.planner import build_tools

    class _Script(ModelGateway):
        def __init__(self, script):
            self.script, self.i = list(script), 0

        async def chat(self, messages, tools=None, **kw):
            out = self.script[min(self.i, len(self.script) - 1)]
            self.i += 1
            return out

    payload = json.dumps([{"title": "去图书馆", "date": _d(3), "start": "11:45", "end": "13:45"},
                          {"title": "去图书馆", "date": _d(4), "start": "11:45", "end": "13:45"},
                          {"title": "去图书馆", "date": _d(5), "start": "13:00", "end": "15:00"}],
                         ensure_ascii=False)
    script = [
        {"content": "我看看你这几天什么时候空。",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "propose_slots",
                                      "arguments": json.dumps({"options_json": payload},
                                                              ensure_ascii=False)}}]},
        {"content": "给你挑了三段，勾方便的。"},
    ]

    with sandbox():
        wipe_todos()
        seed_todos([{"title": "占位", "date": _d(1), "start": "09:00", "end": "10:00"}])
        eng = AgentEngine(llm=_Script(script), tools=build_tools(),
                          system_prompt="你是规划助手。调工具。", session_id="s115-engine")
        res = asyncio.get_event_loop().run_until_complete(eng.run("周末去图书馆"))
        opts = eng.options or []
        c.check("C3 ★模型只交 title/date/start/end，引擎带出来的每条都已经有 kind/weekday/minutes",
                len(opts) == 3 and all(o.get("kind") == "todo_add" and o.get("weekday")
                                       and o.get("minutes") for o in opts),
                json.dumps(opts, ensure_ascii=False)[:240])
        c.check("C4 这一轮引擎确实调到了 propose_slots（否则 C3 是空跑，什么都没测到）",
                any(t.get("action") == "propose_slots" for t in (res.get("trace") or [])),
                json.dumps([t.get("action") for t in (res.get("trace") or [])], ensure_ascii=False))

    # ───────────────────── D 批：本单不许动的东西 ─────────────────────
    print("\n[D] 红线：前端判据、工具描述、收敛器本体都没被顺手动")

    stu = read("app/static/student.html")
    c.check("D1 `drawable` 那条判据逐字未动（#59 的红线：加 weekday 进判据会让整张卡消失）",
            "const drawable = options.filter(o => o && o.date && o.start && o.end);" in stu)
    c.check("D1-bis 老【确认所选】那条拼多行消息的代码还在（本单不碰前端，兜底留着）",
            "确认 ${o.date} ${o.start}-${o.end} ${o.title || \"待办\"}" in stu)

    pln = read("app/modules/planner.py")
    c.check("D2 工具描述没被改成「模型必须传 weekday」（那会让模型开始编星期，编错比空着更糟）",
            "每条含 title(待办标题)、date(如2026-09-25)、" in pln
            and "weekday" not in pln.split('name="propose_slots"')[1].split('func=propose_slots')[0])
    c.check("D3 收敛器的分组条件仍是 `kind != \"todo_add\"`（本单靠它，不是改它）",
            'if not isinstance(o, dict) or o.get("kind") != "todo_add":' in read("app/main.py"))
    c.check("D4 propose_slots 仍在写侧工具名单里（本单没动 #86 那张反向断言的地基）",
            '"propose_slots"' in read("app/agent/readonly.py"))

    print(c.summary("#115 候选卡补字段（kind / weekday / minutes）"))
    return c


if __name__ == "__main__":
    main()
