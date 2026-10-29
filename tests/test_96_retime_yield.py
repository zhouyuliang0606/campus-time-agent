# -*- coding: utf-8 -*-
"""#84 的护栏：改期这一支**兜不住就换那条能查的路**（#76 那扇门多开一处）。

现场（施工单 §0.1）：学生说「有个代办撞时间了，帮我换一个时间」，屏幕上是
「没找到那条待办——你先加进日程」——可他库里躺着 6 条待办，要改的那条就在里面。
根因不是「少调了一次工具」：这一支**本来就查了库**（`pool = list_todos()`），
卡住的是**拿什么键去查** —— 事名抠成残渣「有个了帮我时间」，六条一条都对不上。

本单的做法：`empty` 分两种，只有「库里没东西」那一种才许说「没找到」。
判据是 planner 带出来的 `pool_n`（**收窄之前**的全库条数）；非零就交给 `_run_agent_loop`。

⚠️ 三处执行侧偏离（都配了独立判据，细节在快照）：
  1. 单据 §1.1 说「三处 empty 出口」—— 实测本函数里只有**两处**（`planner.py:5210 / :5213`），
     其余 empty 出口属于 `resolve_todo_remove` / `resolve_todo_done`（删/完成两支），本单没动。
  2. 单据 §1.1 要新造 `pool_all` —— 没造：`:5210` 那一点 `pool` 还没被下一行 `pool = hits` 收窄，
     `len(pool)` 就是全库条数。**代价是 A2/A6 两根轴必须钉住**（尤其 A6：写成字符串 "0" 会当真值）。
  3. 单据 §1.3 说"照 #76 那处抄一行" —— #76 那处只有 `log_intent_gap`（写 `intent_gaps.jsonl`，
     学生端页面不读它），**没有**学生可见的那一行；引擎自己的 trace 也不收外部行。
     ⇒ 本单照本文件 :2213 那两处「复核行」的形状（step=1、往 trace 后面 append）补了一条（C6 钉住）。

⚠️ 打桩形状（踩过一次，写在 B 批的 helper 里）：`_harness.patch_llm` 打的是
   `app.config.get_llm_config`，而 `app/main.py:36` 是**模块顶部导入** ⇒ 那道 Key 闸
   （`_run_agent_loop:2054`）看不见补丁，会静默走 mock。要证明「真的进了引擎」，
   必须连 `app.main.get_llm_config` 一起换。
"""
import ast
import datetime
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, patch_llm, pin_now, sandbox,   # noqa: E402
                      seed_todos, write_timetable)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WD = "周一 周二 周三 周四 周五 周六 周日".split()
MSG = "有个代办撞时间了，帮我换一个时间"          # 现场那句（事名会抠成残渣）
SEM_RETIME = json.dumps({"intent": "retime_todo", "module": "planner",
                         "confidence": 0.9}, ensure_ascii=False)
COURSE = [{"day": 3, "start": "10:00", "end": "11:30", "course": "专业课", "location": "A302"}]


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("\r\n", "\n")


def _iso(off=0):
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()


def row(title, off, start="15:00", end="16:00"):
    return {"title": title, "date": _iso(off), "start": start, "end": end}


SIX = [row("做题", -2), row("约会", 1, "19:00", "20:00"), row("健身房", -3, "09:00", "10:00"),
       row("（周）健身房", 0, "09:00", "10:00"), row("打篮球", 3, "15:00", "17:00"),
       row("游泳", 0)]


def stub_engine(answer="引擎替你把这件事查完了：跟课撞了的是「游泳」，说个新时间我替你挪。",
                tool_phase="🔧 调用工具 list_todos_range"):
    """把引擎换成假的，返回恢复函数。

    ⚠️ 两处都要打，缺一处就是**静默假信号**：
      · `app.main.get_llm_config` —— 那道 Key 闸在 `_run_agent_loop:2054` 读的是 main 自己
        顶部导入的那一份，`patch_llm` 换 `app.config.get_llm_config` 换不到它 ⇒ 会走 mock；
      · `AgentEngine.run` —— 换成 async 的假实现（同步的会撞 `await dict` 的 TypeError，
        而那异常被 `except Exception` 咽成「这一步没办成」，红得看不出原因）。
    """
    import app.agent.engine as E
    import app.main as MN
    real_run, real_cfg = E.AgentEngine.run, MN.get_llm_config

    async def fake_run(self, message, history=None, on_event=None):
        return {"answer": answer, "options": [], "awaiting_choice": False,
                "trace": [{"step": 1, "phase": tool_phase, "answer": "查了这周的待办"}]}

    E.AgentEngine.run = fake_run
    MN.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x", "model": "m"}

    def restore():
        E.AgentEngine.run = real_run
        MN.get_llm_config = real_cfg
    return restore


# ------------------------------------------------- A 批（planner 契约）
def batch_a_contract():
    print("\n=== A 批 · planner 把「库里有没有东西」带得出来吗 ===")
    c = Checker()
    from app.modules import planner as P

    with sandbox():
        seed_todos(SIX)
        r = P.build_retime_existing_proposal(MSG)
        c.check("A1 名字没命中（残渣「有个了帮我时间」）→ empty + `pool_n` = **全库 6 条**"
                "（这一条就是本单的开关：非零 ⇒ 不许断定「没有这条」）",
                r.get("status") == "empty" and r.get("pool_n") == 6,
                str((r.get("status"), r.get("pool_n"))))

        seed_todos(SIX[:3])
        r2 = P.build_retime_existing_proposal(MSG)
        c.check("A2 正控：同一判据换个库容量读数要跟着变（6→3）—— 证明它真在数库，不是常量",
                r2.get("pool_n") == 3, str(r2.get("pool_n")))

        seed_todos([])
        r3 = P.build_retime_existing_proposal(MSG)
        c.check("A3 库真空 → `pool_n` = 0（这一档「没找到、你先加进日程」是实话，T3 的前提）",
                r3.get("status") == "empty" and r3.get("pool_n") == 0,
                str((r3.get("status"), r3.get("pool_n"))))

        seed_todos([row("游泳", 0)])
        ok = P.build_retime_existing_proposal("把游泳挪到周五", {"title": "游泳"})
        c.check("A4 办得成那一支**不许**带 pool_n（键集合钉）—— 免得哪天把「升级」判据写成"
                "`if r.get('pool_n')` 之外的形状",
                ok.get("status") == "ok" and "pool_n" not in ok, str(sorted(ok.keys())))

        seed_todos(SIX)                      # A4 刚把库换成一条游泳 —— many 要的是"有好几条"
        many = P.build_retime_existing_proposal("换个时间")
        c.check("A5 many 那一支也不带 pool_n（#77 那一条列出候选的路，本单不碰）",
                many.get("status") == "many" and "pool_n" not in many,
                str((many.get("status"), sorted(many.keys()))))

        seed_todos(SIX)
        r6 = P.build_retime_existing_proposal(MSG)
        c.check("A6 `pool_n` 必须是 **int** 不是 str：main 那支判的是真值，`\"0\"` 会当真值 ⇒ "
                "库真空那句实话会被升级掉（这一条看着小，是 A3 与 T3 之间唯一的墙）",
                isinstance(r6.get("pool_n"), int) and isinstance(r3.get("pool_n"), int),
                str(type(r6.get("pool_n")).__name__))

        # hint 那一族（#77 A8）：夹具里**只有一条**健身房，两颗重名会把读数搅浑
        seed_todos([row("健身房", 0, "09:00", "10:00")])
        h = P.build_retime_existing_proposal("换个时间", None, "健身房")
        c.check("A7 单据 T5 那句「hint 接手 → 仍 ok 出改期卡」实测**不成立**：#81 之后 hint 认出了那条、"
                "但这句没给新时间 ⇒ 走 unclear 追问（念旧值）。钉成当前口径：unclear + 认领的就是健身房 +"
                " **不带 pool_n**（不是 empty ⇒ 本单不升级它）。test_93 A8 早就为这件事改过口，同一件事",
                h.get("status") == "unclear" and "pool_n" not in h
                and (h.get("todos") or [{}])[0].get("title") == "健身房",
                str((h.get("status"), "pool_n" not in h,
                     (h.get("todos") or [{}])[0].get("title"))))

    pl_src = read("app/modules/planner.py")
    doc = pl_src.split("def build_retime_existing_proposal", 1)[1].split(chr(34) * 3)[1]
    c.check("A8 docstring 的返回档写了 pool_n 是什么（**收窄之前**的全库条数）—— "
            "契约不写在类型里就写在文档里，否则下一个调用方只能猜",
            "pool_n" in doc and "收窄之前" in doc, str(len(doc)))
    return c.summary("A 批（planner 契约）")


# ------------------------------------------------- B 批（端到端）
def batch_b_e2e():
    print("\n=== B 批 · 端到端：兜不住的那一句真的换了条路吗 ===")
    c = Checker()
    st = int(datetime.datetime.now().timestamp())

    with sandbox(), pin_now(9, 0):
        write_timetable(COURSE)
        cli = make_client()

        # ---- T1：库 6 条 + 现场那句 → 进引擎 ----
        seed_todos(SIX)
        from app.modules import planner as P
        before = {x.get("id"): x for x in P.list_todos()}   # 与那一支**同一份取数视图**
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            d1 = cli.post("/api/chat", json={"message": MSG, "session_id": f"t84-t1-{st}",
                                             "module": "planner"}).json()
        finally:
            back(); restore()
        ph1 = [str(t.get("phase") or "") for t in (d1.get("trace") or [])]
        ans1 = str(d1.get("answer") or "")
        c.check("T1 库里有 6 条、事名没认出来 → **不许**再回「你先加进日程」，这句现在该由引擎去查",
                "你先加进日程" not in ans1, ans1[:70])
        c.check("T1-bis 真的进了引擎（轨迹里有 🔧 那一族）—— 不是话术换了个说法",
                any("🔧" in p for p in ph1), str([p[:26] for p in ph1]))
        c.check("T1-ter 让渡那一行进的是**学生看得见**的 trace（#76 那处只记了流水账），"
                "且排在引擎自己那几行**之后**（不许重排引擎的 step）",
                any("交给能查的那条路" in p for p in ph1) and "交给能查的那条路" in ph1[-1],
                str([p[:26] for p in ph1][-2:]))
        after = {x.get("id"): x for x in P.list_todos()}
        c.check("T1-4 让渡**不写库**：库里那 6 条前后一字未改（切换的是「谁去查」，不是「查完顺手写」）",
                before == after and len(after) == 6, str((len(before), len(after))))
        pend = cli.get(f"/api/pending/t84-t1-{st}").json()
        c.check("T1-5 让渡**不挂暂存**：交给引擎那一轮不该留下一张指向「没找到的那条」的卡",
                not (pend.get("options") or pend.get("pending") or pend.get("items")),
                json.dumps(pend, ensure_ascii=False)[:90])

        # ---- T3：库真空 → 那句实话必须还在（本单最容易带掉的一条）----
        seed_todos([])
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            d3 = cli.post("/api/chat", json={"message": MSG, "session_id": f"t84-t3-{st}",
                                             "module": "planner"}).json()
        finally:
            back(); restore()
        ph3 = [str(t.get("phase") or "") for t in (d3.get("trace") or [])]
        c.check("T3 池子**真空** → 仍回「没找到那条待办——你先加进日程」（这句在这一档是实话）。"
                "⚠️ 它就在改动点正下方，演示时库里永远有数据 ⇒ 没有任何一场会碰到它，只能靠这条钉",
                "你先加进日程" in str(d3.get("answer") or "")
                and not any("交给能查的那条路" in p for p in ph3),
                str([str(d3.get("answer"))[:40], [p[:22] for p in ph3]]))

        # ---- T2 正控：办得成的那句不许被升级 ----
        seed_todos(SIX)
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            d2 = cli.post("/api/chat", json={"message": "把游泳挪到周五",
                                             "session_id": f"t84-t2-{st}", "module": "planner"}).json()
        finally:
            back(); restore()
        ph2 = [str(t.get("phase") or "") for t in (d2.get("trace") or [])]
        cards2 = [o for o in (d2.get("options") or []) if isinstance(o, dict)]
        c.check("T2 正控：「把游泳挪到周五」仍走**确定性**那一支出改期卡，"
                "轨迹里**不许**有引擎那一族（办得成的不许升级 —— 否则本单变成「什么都交给模型」）",
                len(cards2) == 1 and (cards2[0] or {}).get("old_todo_id")
                and not any("🔧" in p for p in ph2)
                and not any("交给能查的那条路" in p for p in ph2),
                str([(k.get("kind"), k.get("date"), bool(k.get("old_todo_id"))) for k in cards2]))

        # ---- T4：#77 的 many（列出候选）不许被升级抢走 ----
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            d4 = cli.post("/api/chat", json={"message": "换个时间",
                                             "session_id": f"t84-t4-{st}", "module": "planner"}).json()
        finally:
            back(); restore()
        ph4 = [str(t.get("phase") or "") for t in (d4.get("trace") or [])]
        c.check("T4 「换个时间」（无上文、库 6 条）仍走 #77 的 many：把候选念给他挑，"
                "不是升级去让模型猜",
                "哪一条" in str(d4.get("answer") or "") and not any("🔧" in p for p in ph4),
                str([str(d4.get("answer"))[:30], [p[:22] for p in ph4]]))

        # ---- T5-bis：挂着待确认卡时，本单这一支**根本不参与**（`not held_add` 那道闸）----
        #   实测：第一轮「明天下午三点去健身房」挂出一张 todo_add 卡，第二轮「换个时间」
        #   走的是**链尾兜底**（有 🔧、但**没有**本单这行让渡说明）——
        #   因为改期支的入口闸是 `... and not held_add`。这条钉的是"本单没把挂卡时的行为改掉"，
        #   而 #77 的 hint 那一族（不挂卡）由 A7 在函数级钉 —— 两件事别混成一条。
        seed_todos([row("健身房", 0, "09:00", "10:00")])
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            cli.post("/api/chat", json={"message": "明天下午三点去健身房",
                                        "session_id": f"t84-t5-{st}", "module": "planner"}).json()
            d5 = cli.post("/api/chat", json={"message": "换个时间",
                                             "session_id": f"t84-t5-{st}", "module": "planner"}).json()
        finally:
            back(); restore()
        ph5 = [str(t.get("phase") or "") for t in (d5.get("trace") or [])]
        c.check("T5-bis 手上挂着待确认卡 → 这一支不参与：引擎可以接手（链尾那扇门），"
                "但**不许**出现本单那行让渡说明（出现了就说明入口那道 `not held_add` 被碰坏了）",
                not any("交给能查的那条路" in p for p in ph5),
                str([p[:24] for p in ph5][-2:]))

        # ---- 只有 empty 才升级：unclear（#81 空改动）那一支照旧 ----
        seed_todos(SIX)
        restore = patch_llm(SEM_RETIME)
        back = stub_engine()
        try:
            d6 = cli.post("/api/chat", json={"message": "把游泳改到游泳那个时间",
                                             "session_id": f"t84-t6-{st}", "module": "planner"}).json()
        finally:
            back(); restore()
        ph6 = [str(t.get("phase") or "") for t in (d6.get("trace") or [])]
        c.check("边界：不是 empty 的那些出口（#81 空改动 / #82 排不下）**不许**升级 —— "
                "本单只接「没认出是哪条」这一种失败",
                not any("交给能查的那条路" in p for p in ph6), str([p[:24] for p in ph6][-2:]))
    return c.summary("B 批（端到端）")


# ------------------------------------------------- C 批（静态接线）
def batch_c_static():
    print("\n=== C 批 · 静态接线（判据挂在哪儿、几处、不许出现什么）===")
    c = Checker()
    pl = read("app/modules/planner.py")
    mn = read("app/main.py")
    tree = ast.parse(pl)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name == "build_retime_existing_proposal"][0]
    body = ast.get_source_segment(pl, fn)

    def ret_keys(node):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
                out.append({k.value for k in n.value.keys if isinstance(k, ast.Constant)})
        return out

    ks = ret_keys(fn)
    with_pool = [k for k in ks if "pool_n" in k]
    c.check("C1 planner 侧带 `pool_n` 的返回**恰两处**（= 本函数的两个 empty 出口）。"
            "⚠️ 单据 §1.1 写的是「三处」—— 实测只有两处，其余 empty 出口在 "
            "`resolve_todo_remove` / `resolve_todo_done`（删/完成两支），本单**没动**它们",
            len(with_pool) == 2, str(len(with_pool)))
    c.check("C2 带 pool_n 的出口**都是** empty 那一族（不许哪天给 ok 也带上，"
            "那等于把开关的语义从「没认出来」稀释成「库里有多少条」）",
            all("status" in k for k in with_pool)
            and body.count('"status": "empty", "todos": [], "pool_n"') == 2,
            str(body.count('"status": "empty", "todos": [], "pool_n"')))

    i0 = mn.find('if _rt_status == "empty":')
    i1 = mn.find("# unclear：新时间认不出来", i0)
    seg = mn[i0:i1] if i0 >= 0 and i1 > i0 else ""
    c.check("C3 改期支里引擎调用**恰一处**，且这一支**自己 return**（不是 #76 那种 `pass`）—— "
            "这一支在函数中段，`pass` 会掉到后面的分支被抢走（单据 §五.1）。"
            "⚠️ 形状是 `_up = await _run_agent_loop(...)` → 补一行 trace → `return _up`，"
            "不是字面的 `return await`：要补的那一行得先拿到返回值才补得上",
            seg.count("await _run_agent_loop(") == 1 and "return _up" in seg
            and "pass" not in seg.split("log_intent_gap")[-1].split("append_conversation")[0],
            str((seg.count("await _run_agent_loop("), "return _up" in seg)))
    c.check("C4 全文件 `_run_agent_loop(` 的**调用点** = 4 处（#61 前置闸门 / #76 让渡口 / "
            "链尾兜底 / 本单）。多一处就要问「这是不是第五扇门」，少一处说明有扇门被并掉了",
            len(re.findall(r"await _run_agent_loop\(", mn)) == 4,
            str(len(re.findall(r"await _run_agent_loop\(", mn))))
    c.check("C5 改期支里**不许判有没有 Key**（单据 §2.4：那道闸在 `_run_agent_loop:2054`，"
            "写第二遍 = 同一个判据两份，而且对不齐时症状是「能进去但跑的是 mock」）",
            "get_llm_config" not in seg, str("get_llm_config" in seg))
    c.check("C6 让渡那一行是**往 trace 后面 append**（照 :2213 那两处复核行的形状），"
            "不是重排引擎自己的 step —— 重排会让落盘那份与屏幕上那份对不上",
            'list(_up.get("trace") or []) + [' in seg
            and seg.split("return _up")[0].count('"step": 1') == 1,
            str(seg.count('"step": 1')))
    c.check("C7 让渡要记账：改期支里 `log_intent_gap(` 恰一处（#76 立的规矩「让渡不是白让」）",
            seg.count("log_intent_gap(") == 1, str(seg.count("log_intent_gap(")))
    c.check("C8 契约两端成对（#62 那条）：planner 带 pool_n 两处、main 读它**恰一处**，"
            "且读的时候兜了 0（`or 0`）—— 老形状没有这个键，不许 KeyError",
            len(re.findall(r'_rt\.get\("pool_n"\) or 0', mn)) == 1,
            str(len(re.findall(r'_rt\.get\("pool_n"\) or 0', mn))))
    c.check("C9 T3 那句实话的 return 还在，且位置在升级支**之后**（先判 pool_n、后说没找到）—— "
            "顺序倒了就等于库真空也不许说「没找到」",
            '你先加进日程' in seg and seg.find("return await _run_agent_loop(") < seg.find("你先加进日程"),
            str((seg.find("return await _run_agent_loop("), seg.find("你先加进日程"))))
    return c.summary("C 批（静态接线）")


def main():
    code = 0
    ran = 0
    batches = (("A 批（planner 契约）", batch_a_contract),
               ("B 批（端到端）", batch_b_e2e),
               ("C 批（静态接线）", batch_c_static))
    for label, fn in batches:
        ran += 1
        try:
            rc = fn()
        except Exception as e:      # 一批崩不能冒充「别的批通过」
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    # ⚠️ 别写成 `code = code or fn()`：`or` 短路会让第一批变红后面的批**根本不跑**
    #    （#82 那一轮踩过，`test_75:535` 至今还是旧写法）。跑了几批要自己钉。
    assert ran == len(batches), f"只跑了 {ran}/{len(batches)} 批"
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
