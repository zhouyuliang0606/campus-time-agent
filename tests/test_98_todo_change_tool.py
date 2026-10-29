# -*- coding: utf-8 -*-
"""#86 的护栏：给引擎一只**能改待办时间**的手（`propose_todo_change(op=retime)`）。

现场（#84 之后）：引擎查得出「哪条撞了哪节课」，可 planner 那 13 只工具里
加/删/完成都有，**唯独没有"改"**（课表侧反而有 `propose_course_change(op=...)`）
⇒ 它只能反问「你说的是哪一条、想改到几点」。学生原话：「没有帮我想时间，是我问了才说的」。

本批四组，强度不一样，别看混：
  T 工具级（直接调函数）—— 单据 §3.1 的 T1~T6，外加"调用前后库里一个字节都没动"；
  W 接线 —— 挂载 / 回显正则**与派发那张表两头** / 写侧名单 / 说明书里那三句关键话；
  A 默认值 —— `auto_slot` 默认 `False` 这一条是本单命门（单据 §五.1）：两头各钉一条，
             只钉"True 会挑"不钉"False 不挑"= 把默认值改成 True 也没人红；
  E 端到端 —— 真 `_run_agent_loop` + 脚本好的 `_ask`（不联网）：卡片真挂出来、`old_todo_id` 有值、
             need_pick 时**一张都不许出**、加待办那一路不许被它抢走。

⚠️ 五处与施工单不一致，都按实测钉（细节在快照）：
  1. §3.3 说 `test_31` A3 会从 13→14 —— **不会**：新工具挂在 `build_scheduling_tools()`，
     不在 `planner.build_tools()` 那 13 只里。实测红的是 `test_82` 的 A6 / B1(planner) / C2-bis 三处。
  2. §3.3 只点 planner 一栏 —— 实测 faq/schedule/express/takeout 那四个数**不变**
     （它们的样本句没开 `_schedule_tools_allowed` 那道闸；schedule 那三只 `propose_todo_*` 是它自己工具箱里的）。
  3. §3.1 T6 的中间路径：单据说"`looks_like_thing` 挡下 → 抠不出名字 → 当没点名"，
     实测抠出来一个 '挪到'（不是抠不出），**仍**落到"当没点名" ⇒ `many` → need_pick。结论对、理由不对。
  4. §1.5 只要求登记 `_TOOL_ECHO_RE` —— 只加正则**不接派发**（`textual_tool_card` 那张按名字的表）
     = "模型把调用打成字"这一族对新工具静默不执行（文本撤掉了、事没办）。两头都钉（W2/W3）。
  5. §1.1 说 auto_slot 要治"没给新时间" —— 实测**只换日子**那一档本来就会挑空档出卡，
     真正新增的只有"什么都没给"那一档（`unclear/no_time`）。默认值的靶子按这一档造。
"""
import ast
import datetime
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, patch_llm, pin_now, sandbox, seed_todos, title, write_timetable  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PL = "app/modules/planner.py"
RO = "app/agent/readonly.py"
TODAY = datetime.date.today()
NEW = "propose_todo_change"
COURSE = [{"day": TODAY.isoweekday(), "start": "08:00", "end": "09:40",
           "course": "高数", "location": "A302"}]


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("\r\n", "\n")


def row(title_, off, start="15:00", end="16:00"):
    """照**真实行的字段形状**造（`id/note/category/status=planned/created_at` 一个都不缺）：
    缺 `id` 会让只读工具把整库念成「另有 N 条没有 id」，量的就不是同一件事了（#84 补 踩过）。"""
    return {"id": "t98-" + title_, "title": title_,
            "date": (TODAY + datetime.timedelta(days=off)).isoformat(),
            "start": start, "end": end, "note": "", "category": "", "status": "planned",
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def three():
    """三条夹具：一条**真撞课**（今天 08:00-09:40 = 高数那一节）、两条别的日子。"""
    return [row("游泳", 0, "08:00", "09:40"), row("做题", 1, "20:00", "21:00"),
            row("健身房", 2, "09:00", "10:00")]


def call(P, **kw):
    return json.loads(P.propose_todo_change_tool(**kw))


# --------------------------------------------------------------- T 批（工具级）
def batch_t_tool():
    c = Checker()
    title("T. 工具级：单据 §3.1 那六条 + 一条'不许写库'")
    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P
        write_timetable(COURSE)
        seed_todos(three())
        ids_before = {x.get("id") for x in P.list_todos()}

        r1 = call(P, title="游泳")
        p1 = r1.get("__proposal__") or {}
        c.check("T1 撞课那条、什么都没给 → 出提案，且**新时段不是旧的 08:00-09:40**（=不是空改动）",
                p1.get("title") == "游泳" and p1.get("start") not in ("", "08:00"),
                str((p1.get("date"), p1.get("start"), p1.get("end"))))
        c.check("T1-bis 挑出来的那段**不与高数（08:00-09:40）重叠**，也不早于 now+30（不挂过去的卡）",
                p1.get("start", "") >= "09:40" or p1.get("end", "") <= "08:00",
                str((p1.get("start"), p1.get("end"))))
        c.check("T1-ter 卡片形状与确定性分支**逐字同一套**：kind=todo_add + retimed + old_todo_id"
                "（前端只认这份形状；换引擎这条路不能另造一套 kind）",
                p1.get("kind") == "todo_add" and p1.get("retimed") is True
                and p1.get("old_todo_id") == "t98-游泳", str(sorted(p1.keys())))

        d4 = (TODAY + datetime.timedelta(days=4)).isoformat()
        r2 = call(P, title="游泳", new_date=d4, new_start="18:00", new_end="19:00")
        p2 = r2.get("__proposal__") or {}
        c.check("T2 学生报了准点 → **照他说的出**，不许自作主张改成系统挑的那段",
                (p2.get("date"), p2.get("start"), p2.get("end")) == (d4, "18:00", "19:00"),
                str((p2.get("date"), p2.get("start"), p2.get("end"))))
        c.check("T2-bis 卡上标题是**库里那条**「游泳」，不是拼出来的串、也不带 1800 这种残渣",
                p2.get("title") == "游泳", repr(p2.get("title")))

        r3 = call(P, title="不存在的东西")
        c.check("T3 名字对不上 → not_found，而且**带上 pool_n**（库里有 3 条，'没找到'就是假话）",
                r3.get("ok") is False and r3.get("not_found") is True and r3.get("pool_n") == 3,
                str(sorted(r3.keys())))

        r4 = call(P, op="rename", title="游泳")
        c.check("T4 op=rename 明确拒绝（**不许静默当 retime 处理**：悄悄改时间是最坏的一种坏）",
                r4.get("ok") is False and "retime" in (r4.get("error") or "") and "__proposal__" not in r4,
                str(r4)[:130])

        r5 = call(P)
        c.check("T5 没给 title → 拒绝，且**不去猜**库里第一条（猜错的代价=把 A 改了，回退要两次）",
                r5.get("ok") is False and "title" in (r5.get("error") or ""), str(r5)[:120])

        r6 = call(P, title="待办")
        c.check("T6 脏 title（'待办'）+ 夹具 ≥2 条 → need_pick 带候选清单，**不许直接出卡**"
                "（库里只 1 条时会改那一条是既有行为，所以夹具必须 ≥2）",
                r6.get("need_pick") is True and len(r6.get("todos") or []) == 3
                and "__proposal__" not in r6, str([x.get("title") for x in (r6.get("todos") or [])]))

        c.check("T7 全程一个字节都没写库（只出提案；执行权在学生点【确认加入】之后）",
                {x.get("id") for x in P.list_todos()} == ids_before, str(len(ids_before)))

        r8 = call(P, title="游泳", new_date=TODAY.isoformat())
        c.check("T8 只给日子、那天排不下也不硬凑：给 reason/slot_label 说一句人话，"
                "而**不是**回'你想改到几点'（本单要治的就是这句）",
                r8.get("ok") is False or (r8.get("__proposal__") or {}).get("start"),
                str(r8)[:150])
    return c.summary("T 批（工具级）")


# --------------------------------------------------------------- W 批（接线）
def batch_w_wiring():
    c = Checker()
    title("W. 接线：挂载 + 回显两头 + 写侧名单 + 说明书那三句话")
    src = read(PL)
    with sandbox():
        from app.modules import planner as P
        sched = P.build_scheduling_tools()
        own = P.build_tools()
        c.check("W1 挂进 `build_scheduling_tools()`（与三只 propose_todo_* 并排），"
                "**没有**塞进 planner 自己的 `build_tools()`（那 13 只是 test_31 A3 钉着的账）",
                NEW in sched and NEW not in own, f"sched {len(sched)} 只 / own {len(own)} 只")
        t = sched.get(NEW)
        c.check("W2 说明书里那三句关键话一句都不能少：不新建 / 别反问几点 / need_pick 就列清单然后停",
                all(k in (t.description or "") for k in ("不会新建", "不要反问", "是下面哪一条")),
                (t.description or "")[:80])
        c.check("W3 参数 schema：只有 `title` 必填（其余留空 = 让系统替他挑）",
                t.parameters.get("required") == ["title"]
                and set(t.parameters.get("properties") or {}) == {"op", "title", "new_date", "new_start", "new_end"},
                str(t.parameters.get("required")))
        c.check("W4 它登记的写侧名字与 `readonly.WRITE_TOOL_NAMES` 对得上（漏一条=那张反向断言的表静默失守）",
                NEW in __import__("app.agent.readonly", fromlist=["x"]).WRITE_TOOL_NAMES)

    echo = src.split("_TOOL_ECHO_RE = re.compile(")[1].split(")\n")[0] if "_TOOL_ECHO_RE = re.compile(" in src else ""
    c.check("W5 回显正则认识它（不认=模型把调用打成字时，那段 JSON 端到学生面前，红线 8）",
            NEW in echo, echo[:110])
    disp = src.split("def textual_tool_card(")[1].split("def rescue_proposal_from_reply")[0]
    c.check("W6 **两头都改**：派发那张表也得认它（只加正则=文本撤掉了、事还是不办 —— 这条单据没列）",
            '"propose_todo_change": propose_todo_change_tool' in disp,
            [ln.strip()[:60] for ln in disp.split("\n") if "propose_todo" in ln][:4])
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "propose_todo_change_tool")
    writes = [n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id in ("add_todo", "delete_todo", "update_todo", "set_todo_status")]
    c.check("W7 AST 查这一只函数自己：真写库的调用 0 个（提案与写库分家这条红线没被本单挪动）",
            not writes, str(writes))
    # ⚠️ 这里**不能**拿 `ast.unparse(...)` 去比字面量：unparse 会把双引号统一成单引号，
    #    拿源码里那种 `"retime"` 去 `in` 会永远 False（#84 补 那一轮刚踩过）。比结构，不比文本。
    first_if = next((n for n in fn.body if isinstance(n, ast.If)), None)
    early = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    call_site = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "build_retime_existing_proposal"]
    c.check("W8 `op` 那道闸门是函数体里**第一个 if**、且在真正干活那次调用之前"
            "（op 不是 retime 时**早退**，不许往下走到改时间那段，也不许静默当 retime 处理）",
            first_if is not None and "op" in ast.dump(first_if.test)
            and bool(early) and bool(call_site) and min(e.lineno for e in early) < call_site[0].lineno,
            str([type(s).__name__ for s in fn.body[:4]]))
    # W9：#82 那笔老洞（不传 `not_before` ⇒ 挑得出"今天 08:00"这种已经过去的卡，学生点不到）。
    #      行为级在这一档测不出来（夹具里那节课占掉了早段，挑到的自然是晚段），所以钉**写法**：
    #      `auto_slot` 那个分支里对 `pick_free_slot` 的唯一一次调用必须带着 `not_before=`。
    i = src.index("        elif auto_slot:")
    blk = src[i:src.index("        else:", i)]
    c.check("W9 `auto_slot` 分支里那次 `pick_free_slot` 必须传 `not_before`（#82 同一处调用点的老洞一起堵）",
            blk.count("pick_free_slot(") == 1 and "not_before=_nb2" in blk,
            f"{blk.count('pick_free_slot(')} 次调用 / not_before {'在' if 'not_before=_nb2' in blk else '缺席'}")
    return c.summary("W 批（接线）")


# --------------------------------------------------------------- A 批（默认值命门）
def batch_a_default():
    c = Checker()
    title("A. 命门：`auto_slot` 默认 False —— 确定性分支逐字不变（单据 §五.1）")
    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P
        write_timetable(COURSE)
        seed_todos(three())
        old = P.build_retime_existing_proposal("游泳改一下时间", {"title": "游泳"}, "游泳")
        c.check("A1 默认那条路照旧：什么都没给 → `unclear` + `reason=no_time` + 带着旧条目与现状",
                old.get("status") == "unclear" and old.get("reason") == "no_time"
                and (old.get("todos") or [{}])[0].get("title") == "游泳"
                and "08:00" in (old.get("old_label") or ""),
                str((old.get("status"), old.get("reason"), old.get("old_label"))))
        c.check("A2 而且**没有 pool_n**（#84 那一支的开关只由 empty 出口带；unclear 不许被升级）",
                "pool_n" not in old, str(sorted(old.keys())))
        auto = P.build_retime_existing_proposal("游泳改一下时间", {"title": "游泳"}, "游泳",
                                                auto_slot=True)
        pa = auto.get("proposal") or {}
        c.check("A3 换 True：同一句输入 → 出卡（这就是本单唯一新增的那一档）",
                auto.get("status") == "ok" and pa.get("retimed") is True
                and pa.get("start") not in ("", "08:00"), str((auto.get("status"), pa.get("start"))))
        c.check("A4 两头一对才算钉住默认值：False 出 `unclear`、True 出 `ok`（只钉一头，"
                "把默认值改成 True 也没人红 —— 单据 §3.5 说的就是这一条）",
                old.get("status") == "unclear" and auto.get("status") == "ok")
        # 只换日子那一档**本来就会挑空档**（实测），本单没动它：
        d2 = (TODAY + datetime.timedelta(days=2)).isoformat()
        only_date = P.build_retime_existing_proposal(f"游泳挪到 {d2}", None, "")
        c.check("A5 正控（偏离 5）：'只换日子没报钟点'改前改后**同一份行为**（本来就挑空档）",
                only_date.get("status") == "ok" and only_date.get("proposal", {}).get("date") == d2,
                str((only_date.get("status"), (only_date.get("proposal") or {}).get("date"))))
        empty = P.build_retime_existing_proposal("游泳改一下时间", {"title": "游泳"}, "游泳")
        with_empty = sandbox_empty = None
        seed_todos([])
        with_empty = P.build_retime_existing_proposal("游泳改一下时间", {"title": "游泳"}, "游泳",
                                                      auto_slot=True)
        c.check("A6 库真空时不许凭空造卡：True 也照样 empty + pool_n=0（#84 那条实话不能被本单带掉）",
                with_empty.get("status") == "empty" and with_empty.get("pool_n") == 0,
                str((with_empty.get("status"), with_empty.get("pool_n"))))
        del empty
    sig = read(PL).split("def build_retime_existing_proposal")[1].split(") -> dict")[0]
    c.check("A7 签名里 `auto_slot` 的**默认值就是 False**（写在签名上，不靠调用方记得传）",
            "auto_slot: bool = False" in sig, sig[-70:])
    return c.summary("A 批（默认值命门）")


# --------------------------------------------------------------- E 批（端到端）
def tc(name, args, content=""):
    return {"role": "assistant", "content": content,
            "tool_calls": [{"id": "c1", "function": {"name": name,
                                                     "arguments": json.dumps(args, ensure_ascii=False)}}]}


def batch_e_e2e():
    c = Checker()
    title("E. 端到端：引擎调这只工具 → 卡真挂出来（不联网，假 `_ask` 驱动真引擎）")
    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P
        import app.main as M
        write_timetable(COURSE)
        seed_todos(three())
        ids_before = {x.get("id") for x in P.list_todos()}
        d4 = (TODAY + datetime.timedelta(days=4)).isoformat()

        async def go(script, msg="游泳撞时间了，帮我换一个时间"):
            from app.agent.engine import AgentEngine
            import types
            it = iter(script)

            async def fake_ask(self, messages):
                try:
                    return next(it)
                except StopIteration:
                    return {"role": "assistant", "content": "（脚本用完了）"}

            def key_ok(*a, **k):
                # **必须是同步函数**：`_run_agent_loop:2054` 是 `get_llm_config(...)["api_key"]`
                # 这么用的 —— 换成 async 就把协程对象递下去，报 `TypeError: 'coroutine' object is not subscriptable`
                return {"api_key": "test-key", "base_url": "http://x", "model": "m"}

            saved = (AgentEngine._ask, M.get_llm_config)
            AgentEngine._ask = fake_ask
            M.get_llm_config = key_ok
            try:
                import app.modules.files as F
                from app.modules.planner import build_scheduling_tools, build_tools
                tools = build_tools()
                tools.update(F.build_tools())
                tools.update(build_scheduling_tools())
                # `go` 本身就是协程，由外面的 `_await` 起环 —— 这里**不许再起一个环**
                # （嵌套 `run_until_complete` = `RuntimeError: Cannot run the event loop while another is running`，
                #  而异常会被 `_run_agent_loop` 外面那层咽成一句人话，红得看不出原因）
                return await M._run_agent_loop(
                    module_key="planner", session_id="t98e-" + str(len(msg)),
                    message=msg, prompt="测试用提示", tools=tools,
                    persona_key="warm", model_config=None, emit=None)
            finally:
                AgentEngine._ask, M.get_llm_config = saved

        out1 = _await(go([tc(NEW, {"title": "游泳"}, "我先看看哪条撞了。"),
                          plain("给你挑了个不撞课的时间，点确认就改。")]))
        opts = out1.get("options") or []
        cards = [o for o in opts if isinstance(o, dict)]
        c.check("E1 引擎调了这只工具 → **屏幕上真有一张改期卡**（options 非空、old_todo_id 有值）"
                "—— 这就是 #84 之后那 4 句'只反问不办事'的出路",
                len(cards) == 1 and cards[0].get("old_todo_id") == "t98-游泳"
                and cards[0].get("retimed") is True, str(cards)[:150])
        c.check("E1-bis 轨迹里那一步看得见（🔧 调用工具 + 📦 工具结果都在，不是只在答案里念一句）",
                any("调用工具" in str(t.get("phase")) and NEW in str(t.get("action"))
                    for t in (out1.get("trace") or [])),
                str([str(t.get("phase"))[:8] for t in (out1.get("trace") or [])]))
        c.check("E1-ter 一个字节都没写库：学生点【确认加入】才由系统执行（提案与写库分家）",
                {x.get("id") for x in P.list_todos()} == ids_before, str(len(ids_before)))

        out2 = _await(go([tc(NEW, {"title": "待办"}), plain("是下面哪一条？你说个名字。")]))
        c.check("E2 命中不止一条 → **一张卡都不出**（工具回 need_pick，模型只能列清单问）",
                not [o for o in (out2.get("options") or []) if isinstance(o, dict)],
                str(out2.get("options"))[:120])

        out3 = _await(go([tc(NEW, {"title": "游泳", "new_date": d4,
                                   "new_start": "18:00", "new_end": "19:00"}),
                          plain("按你说的改到那天晚上。")]))
        card3 = (out3.get("options") or [{}])[0]
        c.check("E3 他报了准点 → 卡上就是那个点（引擎不许替他改主意）",
                (card3.get("date"), card3.get("start"), card3.get("end")) == (d4, "18:00", "19:00"),
                str((card3.get("date"), card3.get("start"), card3.get("end"))))

        out4 = _await(go([tc("propose_todo_tool",
                             {"title": "游泳", "date": d4, "start": "18:00", "end": "19:00"}),
                          plain("加好了，点确认。")], "帮我加个游泳"))
        card4 = (out4.get("options") or [{}])[0]
        c.check("E4 正控：'帮我加个游泳'仍走**新建**那只，没被 change 抢走（卡不带 retimed/old_todo_id）",
                card4.get("title") == "游泳" and not card4.get("retimed")
                and not card4.get("old_todo_id"), str(card4)[:130])
    return c.summary("E 批（端到端）")


def plain(text):
    return {"role": "assistant", "content": text}


def _await(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def main():
    code = 0
    ran = 0
    batches = (("T 批（工具级）", batch_t_tool),
               ("W 批（接线）", batch_w_wiring),
               ("A 批（默认值命门）", batch_a_default),
               ("E 批（端到端）", batch_e_e2e))
    for label, fn in batches:
        ran += 1
        try:
            rc = fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  \u274c {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    # 别写成 `code = code or fn()`：`or` 短路会让第一批红后面的批根本不跑（#82 踩过）
    assert ran == len(batches), f"只跑了 {ran}/{len(batches)} 批"
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
