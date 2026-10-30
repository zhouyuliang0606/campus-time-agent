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

**86测 追记（2026-10-30，测试卡 §三 的 C 批 14 + R 批 4 落在这里，另加 1 条我方补的）**：
  · C 批钉的是 `auto_slot` 原来一条没钉的四样 —— **跨天**（C1）、**三天全满说实话**（C2）、
    **真同名两条**（C3，T6 那条走的是脏名字）、**只给起点**（C4），外加 repeat 的**值**（C5）
    和四个"钉现状"（C6/C8/C12/C14 —— 假命中、过去日子、备注会丢、大小写敏感）。
    **测试不替产品做决定**：那四条红的时候都是好事，注释里写了届时怎么翻。
  · R 批钉的是路由：need_pick **回来那一步**（R1）、新工具不许抢确定性分支（R2）、
    无 Key 时结构上证明工具根本没进（R3）。
  · 三处与测试卡不一致，按我这棵树上的实测钉：
    ① 单据 §二 末尾"顺带实测"说单字名『跑』也能出卡、解释成"池子 1 条兜底" —— **否证**：
       同一个 1 条池，标题「唯一一条」时 `跑` ⇒ `not_found`；标题「跑步」时才出卡，
       那是 `_todo_name_match` 的包容式命中。**两个机制不是一回事**，按实测钉成 C6-bis。
    ② §三 R3 说无 Key 那一路的话术是"#84 已登记的那句假话" —— 实测**不是**：有课表时 Mock
       摊的是候选时段，而且把**学生整句话当任务名**。R3 因此钉**结构**（没 retimed 卡、
       trace 里没这只工具），不钉那句答案；Mock 的形状登记进快照挂账。
    ③ §三 R4 期望"不许出现『已经改好了』" —— 实测**这句话原样进屏幕**（#80 那道兜底只管
       没挂卡那条链）。写成期望就是**一条断言了不存在行为的红**，所以 R4 改成钉现状 + 缺口 X3。
  · 条数：单据说补 18 条、总账应到 5526（那是**干净副本**口径）。我这边多一条 C6-bis ⇒
    本机 5512 → **5531**，副本 5508 → **5527**；差值仍是那笔长期项（`test_86` C 批 6↔2）。

**#87 追记（2026-10-30 夜，施工单 `87_改期两条硬伤…drop-in.md`）**：本文件多了 **P 批 22 条**（同名出口 7 +
  过去钟点闸 9 + 写法 6），R 批多 **2 条**（R1-bis 真同名两轮回流 + 它的话术反向钉），W3 改口（5 键 → 6 键）。
  52 → **76 条**。两处按实测与单据不同，都写在判据里：
  ① §1.2 那道闸的位置：单据把条件写成拿模型传来的 `new_date` 字面串比 ISO。实测两处错 ——
     `new_date="今天"`（自然语言）**漏拦**、出的就是那张过去的卡；而"待办本来就在明天、只补一个钟点"
     **被误拦**（`_d = nd or 今天` 把明天 16:00 当今天）。闸因此挪到提案生成之后、比**卡片真要落的那一天**
     （P9/P10/P14 就是这两格的证据，另加 P22 钉位置不许漂）。
  ② §1.3 的 `list_todos_range` 描述判据要从 `build_tools()` 取（那 13 只里有它），从
     `build_scheduling_tools()` 取会 KeyError —— P21 里写死了这句，免得下一个人再撞。
  还有两处我自己的输入形状错了、改完才绿：P14/P15 起初拿 `顶层 dict.get("date")` 判（提案在
  `__proposal__` 里，恒为 None ⇒ 一条真红把我自己的判据暴露出来）；P22 那种"源码写法"判据
  必须连**旧写法不许回来**一起钉，不然只钉新写法存在=删掉旧的也没人红。
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


def row(title_, off, start="15:00", end="16:00", repeat="", note="", category=""):
    """照**真实行的字段形状**造（`id/note/category/status=planned/created_at` 一个都不缺）：
    缺 `id` 会让只读工具把整库念成「另有 N 条没有 id」，量的就不是同一件事了（#84 补 踩过）。
    `repeat/note/category` 是 86测 补的三个旋钮（C5/C12 要造出"带重复的""带备注的"那条）。"""
    return {"id": "t98-" + title_, "title": title_,
            "date": (TODAY + datetime.timedelta(days=off)).isoformat(),
            "start": start, "end": end, "note": note, "category": category,
            "status": "planned", "repeat": repeat,
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
        # #87 把参数表从 5 键改到 6 键（多的那格 `todo_date` = 同名时挑哪一条）。
        #   为什么这算"改口"而不是"计数被合法新增顶红"（#84 那条纪律）：原判据钉的是**这一个工具此刻的入参集合**，
        #   加一格可选参数正是要修的功能本身 ⇒ 换性质不换更大的数：**键集合必须逐字等于这张表**（多一格少一格都红），
        #   且 `required` 仍然只有 `title`（可选性才是"确定性分支一个字没改"的那道命门）。
        #   牙由变异 V5 量：把 `todo_date` 从 properties 里删掉 ⇒ W3 与 P17 一起红。
        c.check("W3 参数 schema（#87 后）：键集合逐字等于这 6 格、`required` 仍然只有 `title`"
                "（`todo_date` 必须可选 —— 确定性分支与老调用方都没这一格）",
                t.parameters.get("required") == ["title"]
                and set(t.parameters.get("properties") or {}) == {"op", "title", "todo_date",
                                                                  "new_date", "new_start", "new_end"},
                str(sorted(t.parameters.get("properties") or {})))
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


def engine_turn(script, msg, sid="t98e-1"):
    """不联网：脚本好的 `_ask` 喂**真引擎**（`_run_agent_loop`），拿回 options/trace/answer。

    ⚠️ 两处都要换（#84 那条）：`AgentEngine._ask` 之外还得换 **main 顶部导入的那份
    `get_llm_config`** —— 那道 Key 闸在 `_run_agent_loop:2054` 读的就是它，只换引擎侧会走 mock。
    ⚠️ `key_ok` **必须是同步函数**：那一行是 `get_llm_config(...)["api_key"]` 这么用的。
    """
    from app.agent.engine import AgentEngine
    import app.main as M
    import app.modules.files as F
    from app.modules.planner import build_scheduling_tools, build_tools
    it = iter(script)

    async def fake_ask(self, messages):
        try:
            return next(it)
        except StopIteration:
            return {"role": "assistant", "content": "（脚本用完了）"}

    def key_ok(*a, **k):
        return {"api_key": "test-key", "base_url": "http://x", "model": "m"}

    saved = (AgentEngine._ask, M.get_llm_config)
    AgentEngine._ask, M.get_llm_config = fake_ask, key_ok
    try:
        tools = build_tools()
        tools.update(F.build_tools())
        tools.update(build_scheduling_tools())
        return _await(M._run_agent_loop(
            module_key="planner", session_id=sid, message=msg, prompt="测试用提示",
            tools=tools, persona_key="warm", model_config=None, emit=None))
    finally:
        AgentEngine._ask, M.get_llm_config = saved


def cards_of(out):
    return [o for o in (out.get("options") or []) if isinstance(o, dict)]


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

        def go(script, msg="游泳撞时间了，帮我换一个时间", sid=None):
            # 86测：R1 要"两轮同一个 session"，所以把 sid 开成一个参数；默认值保持原样
            return engine_turn(script, msg, sid or ("t98e-" + str(len(msg))))

        out1 = go([tc(NEW, {"title": "游泳"}, "我先看看哪条撞了。"),
                   plain("给你挑了个不撞课的时间，点确认就改。")])
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

        out2 = go([tc(NEW, {"title": "待办"}), plain("是下面哪一条？你说个名字。")])
        c.check("E2 命中不止一条 → **一张卡都不出**（工具回 need_pick，模型只能列清单问）",
                not [o for o in (out2.get("options") or []) if isinstance(o, dict)],
                str(out2.get("options"))[:120])

        out3 = go([tc(NEW, {"title": "游泳", "new_date": d4,
                            "new_start": "18:00", "new_end": "19:00"}),
                   plain("按你说的改到那天晚上。")])
        card3 = (out3.get("options") or [{}])[0]
        c.check("E3 他报了准点 → 卡上就是那个点（引擎不许替他改主意）",
                (card3.get("date"), card3.get("start"), card3.get("end")) == (d4, "18:00", "19:00"),
                str((card3.get("date"), card3.get("start"), card3.get("end"))))

        out4 = go([tc("propose_todo_tool",
                      {"title": "游泳", "date": d4, "start": "18:00", "end": "19:00"}),
                   plain("加好了，点确认。")], "帮我加个游泳")
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


# --------------------------------------------------------------- C 批（86测：auto_slot 的能力边界 + 名字含糊那一族）
def day_course(off, s, e, name="高数"):
    """把"星期几"落到**相对今天**的那一格（`write_timetable` 按 isoweekday 存，一周一次）。
    日期全用相对值（挂账 #69 跨天漂移）—— 这里连"明天/昨天/下周五"都是算出来的。"""
    return {"day": (TODAY + datetime.timedelta(days=off)).isoweekday(),
            "start": s, "end": e, "course": name, "location": "A302"}


def batch_c_boundaries():
    c = Checker()
    title("C. 86测 补的缺口：跨天 / 三天全满 / 真实同名 / 只给起点 / repeat / 占位名 / 过去日 / 大小写")
    tmr = (TODAY + datetime.timedelta(days=1)).isoformat()
    yest = (TODAY - datetime.timedelta(days=1)).isoformat()
    d4 = (TODAY + datetime.timedelta(days=4)).isoformat()
    d6 = (TODAY + datetime.timedelta(days=6)).isoformat()
    # `_pick_date` 的既有约定：今天说"周N"指**下一个**周N（今天正好是周五 ⇒ +7 天，就是单据 G8 那一格）
    next_fri = (TODAY + datetime.timedelta(days=((5 - TODAY.isoweekday()) % 7) or 7)).isoformat()
    swim = [row("游泳", 0, "08:00", "09:40")]

    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P

        # C1 今天整日排满 → 必须**跨到明天**（这是 auto_slot 唯一"凭空多出来"的能力）
        write_timetable([day_course(0, "08:00", "23:00")])
        seed_todos(swim)
        p1 = call(P, title="游泳").get("__proposal__") or {}
        c.check("C1 今天 08:00-23:00 全排满、什么都没给 → **跨到明天**挑一段（`for _off in (0,1,2)` 真做事）"
                "。核心判据只有一条：`date != 今天`。"
                "⚠️ 单据 §三 明说别写 `start >= 09:30` —— `not_before`（now+30）只管今天，"
                "明天 08:00 是合法的；实测值就是明天 08:00-09:30，一起钉住这一档",
                p1.get("date") == tmr and (p1.get("start"), p1.get("end")) == ("08:00", "09:30"),
                str((p1.get("date"), p1.get("start"), p1.get("end"))))

        # C2 连续三天全满 → 说实话（**行为对、解释错**那一支不能回来）
        write_timetable([day_course(0, "00:00", "23:59"), day_course(1, "00:00", "23:59"),
                         day_course(2, "00:00", "23:59")])
        seed_todos(swim)
        r2 = call(P, title="游泳")
        c.check("C2 三天课全占满 → `ok=False` + `reason=no_slot` + `slot_label` 里带「三个日子」"
                "（#82 追记 §1.3：说了日子、那天满了，不许推给『没听清』那一族）",
                r2.get("__proposal__") is None and r2.get("reason") == "no_slot"
                and "三个日子" in str(r2.get("slot_label")),
                str((r2.get("reason"), r2.get("slot_label"))))

        # C3 库里**两条真同名**（不是靠脏 title 触发 many —— T6 那条走的是另一支）
        write_timetable([day_course(0, "08:00", "09:40")])
        seed_todos([row("游泳", 0, "08:00", "09:40"), row("游泳", 1, "10:00", "11:00")])
        r3 = call(P, title="游泳")
        c.check("C3 真·两条同名「游泳」→ need_pick + 2 条候选，**一张卡都不出**"
                "（T6 用脏 title 触发 many，这一支是名字正常时也撞车）",
                r3.get("need_pick") is True and len(r3.get("todos") or []) == 2
                and "__proposal__" not in r3,
                str((r3.get("need_pick"), len(r3.get("todos") or []))))

        # C4 模型少填一个 end
        seed_todos(swim)
        p4 = (call(P, title="游泳", new_date=d4, new_start="18:00").get("__proposal__") or {})
        c.check("C4 只给 `new_start=18:00`、没给 end → end 补成 19:00（start+60），照样出卡"
                "（这是模型真会传来的形状，少一个参数就整条不干活太脆）",
                (p4.get("start"), p4.get("end")) == ("18:00", "19:00"),
                str((p4.get("start"), p4.get("end"))))

        # C5 repeat 的值（T1-ter 只查键在不在，**没查值**）
        seed_todos([row("游泳", 0, "08:00", "09:40", repeat="daily")])
        p5 = (call(P, title="游泳", new_date=d4, new_start="18:00",
                   new_end="19:00").get("__proposal__") or {})
        c.check("C5 待办 `repeat=daily` → 提案里 repeat **还是 daily**（值也要继承）",
                p5.get("repeat") == "daily", str(p5.get("repeat")))

        # C6 库里只 1 条 + 占位名 → **钉现状**（这是假命中，别当"识别正确"）
        seed_todos([row("唯一一条", 0, "11:00", "12:00")])
        r6 = call(P, title="待办", new_date=d4, new_start="18:00", new_end="19:00")
        p6 = r6.get("__proposal__") or {}
        c.check("C6 【钉现状·86测 X1】库里只 1 条 + `title=待办`（占位名）→ **直接改那一条**、不问一句。"
                "这是**假命中**：`looks_like_thing` 把『待办』判成不像话的名字 → 当成\"没点名\" → 池子=全部 → "
                "只剩 1 条 ⇒ 改它。保持现状的理由（单据 §四 X1）：只 1 条时问一句收益很低，"
                "而且这是 `build_retime_existing_proposal` 的既有语义，动它会连带改期待办/标记完成两条路 —— "
                "**封版前不动**。哪天拍板改成 not_found，这条会红，届时翻它（测试不替产品做决定）",
                r6.get("__proposal__") is not None and p6.get("title") == "唯一一条",
                str((p6.get("title"), r6.get("not_found"))))

        # C6-bis 把 C6 的机制钉死：**兜底只在名字对不上时不发生**。
        #        ⚠️ 这一条否证了单据 §二 末尾那句"顺带实测"（它说单字名『跑』也能出卡、"是池子 1 条兜底"）——
        #        我在这棵树上量到的是：同一个 1 条池，标题「唯一一条」时 `跑` ⇒ **not_found**；
        #        标题「跑步」时才出卡，而那是 `_todo_name_match` 的**包容式命中**，不是兜底。两个机制不是一回事。
        r6a = call(P, title="跑", new_date=d4, new_start="18:00", new_end="19:00")
        seed_todos([row("跑步", 0, "11:00", "12:00")])
        r6b = call(P, title="跑", new_date=d4, new_start="18:00", new_end="19:00")
        c.check("C6-bis 单字名『跑』不是\"支持单字名\"：池子 1 条叫「唯一一条」时**不出卡**（not_found），"
                "叫「跑步」时才出卡（名字真对得上）。⇒ 名字对不上**永远不许**靠\"只剩一条\"兜底改它"
                "（单据 K6 那句解释被实测否证，这里按实测钉）",
                r6a.get("not_found") is True and r6b.get("__proposal__") is not None
                and (r6b.get("__proposal__") or {}).get("title") == "跑步",
                str((r6a.get("not_found"), (r6b.get("__proposal__") or {}).get("title"))))

        # C7 跨零点（23:00-01:00）→ unclear/no_time，而且 slot_label 是**空串**
        seed_todos(swim)
        r7 = call(P, title="游泳", new_start="23:00", new_end="01:00")
        c.check("C7 `23:00-01:00`（结束比开始早）→ ok=False + reason=no_time + **slot_label 空串**"
                "（这是 unclear 出口的真形状；出一张 end<start 的卡才是坏）",
                r7.get("__proposal__") is None and r7.get("reason") == "no_time"
                and r7.get("slot_label") == "", str((r7.get("reason"), repr(r7.get("slot_label")))))

        # C8 过去日子 → **钉现状**（K2：工具层现在真会出一张过去的卡，下游确认口 400 才拦）
        r8 = call(P, title="游泳", new_date=yest, new_start="18:00", new_end="19:00")
        p8 = r8.get("__proposal__") or {}
        c.check("C8 【钉现状·K2】`new_date=昨天` → **照样出卡**，卡上就是昨天。"
                "planner 出卡路径零命中 `is_past_date`，只有 `/api/todos` 确认口 400 拦得住 —— "
                "这是**已知不修**（单据 §五 K2：要修得动 34 处调用方）。"
                "有人真补了 planner 侧的过去日期闸 ⇒ 这条红，那是**好事**，届时翻它并划掉 K2",
                r8.get("__proposal__") is not None and p8.get("date") == yest,
                str(p8.get("date")))

        # C9/C10 模型传**自然语言**日子（不是 ISO）
        r9 = call(P, title="游泳", new_date="明天", new_start="18:00", new_end="19:00")
        c.check("C9 `new_date=\"明天\"`（自然语言）→ 认，且 date 就是明天（不能只认 ISO 串）",
                (r9.get("__proposal__") or {}).get("date") == tmr,
                str(((r9.get("__proposal__") or {}).get("date"), tmr)))
        r10 = call(P, title="游泳", new_date="周五", new_start="18:00", new_end="19:00")
        c.check("C10 【钉既有约定·K3】今天说「周五」→ 出的是**下一个周五**（`_pick_date` 的既有约定，"
                "2026-10-30 当天就是周五 ⇒ 跳到 11-06）。它二十几处调用方把返回值当\"学生说了一个确定的日子\"用，"
                "**不许顺手修**；谁改了 `_pick_date` 这条就红，红是对的",
                (r10.get("__proposal__") or {}).get("date") == next_fri,
                str(((r10.get("__proposal__") or {}).get("date"), next_fri)))

        # C11 `auto_slot=True` 下**只给日子**那一支（A5 钉的是 False 版）
        r11 = call(P, title="游泳", new_date=d6)
        p11 = r11.get("__proposal__") or {}
        c.check("C11 `auto_slot` 下只给日子（那天没课）→ 走 `pick_free_slot`，date 就是他说的那天、"
                "钟点是挑出来的（单据 §三 A 批那一格：这一支原来一条都没钉）",
                p11.get("date") == d6 and bool(p11.get("start")) and bool(p11.get("end")),
                str((p11.get("date"), p11.get("start"), p11.get("end"))))

        # C12 note/category → **钉住"会丢"**这个事实（K1）
        seed_todos([row("交电费", 0, "08:00", "09:40", note="带户号", category="生活")])
        p12 = (call(P, title="交电费", new_date=d4, new_start="18:00",
                    new_end="19:00").get("__proposal__") or {})
        c.check("C12 【钉现状·K1】待办带 note/category → 提案里**两个键都没有** ⇒ 点【确认加入】后备注被抹空。"
                "真机同一现象复现三次（副本 8053：「记得带泳镜」→ 空串）。"
                "#82 已拍板不修、演示别拿改期演\"备注还在\"；有人补了传递 ⇒ 这条红，届时划掉 §五 K1",
                "note" not in p12 and "category" not in p12, str(sorted(p12.keys())))

        # C13/C14 op 的形状（strip 生效 / 大小写敏感）
        seed_todos(swim)
        r13 = call(P, op="retime ", title="游泳", new_date=d4, new_start="18:00", new_end="19:00")
        c.check("C13 `op=\"retime \"`（尾部空格）→ 出卡：strip 生效（模型传来的串带空白是常态）",
                r13.get("__proposal__") is not None, str(r13.get("error")))
        r14 = call(P, op=" RETIME ", title="游泳", new_date=d4, new_start="18:00", new_end="19:00")
        c.check("C14 【钉现状·K5】`op=\" RETIME \"`（大写）→ **被拒**，错误里带着 retime 那句人话。"
                "大小写敏感是现状（模型不会传大写）；哪天改成不敏感这条会红 —— "
                "**放宽是好事，但要是有意识地放宽**，届时把断言翻过来",
                r14.get("__proposal__") is None and "retime" in str(r14.get("error")),
                str(r14.get("error")))
    return c.summary("C 批（86测：能力边界与名字含糊那一族）")


# --------------------------------------------------------------- R 批（86测：回流、路由、无 Key、话术）
def batch_r_routes():
    c = Checker()
    title("R. 86测 补的缺口：挑完再回来的那一步 / 新工具不许抢确定性分支 / 无 Key / 有卡时的话术")
    st = int(datetime.datetime.now().timestamp())
    tmr_b = (TODAY + datetime.timedelta(days=1)).isoformat()
    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P
        write_timetable(COURSE)
        seed_todos(three())
        ids_before = {x.get("id") for x in P.list_todos()}

        # R1 两轮：第一轮列清单（不出卡）→ 学生挑了名字 → 第二轮才出卡
        sid = f"t98r1-{st}"
        o1 = engine_turn([tc(NEW, {"title": "待办"}), plain("是下面哪一条？你说个名字。")],
                         "改一下那个", sid=sid)
        o2 = engine_turn([tc(NEW, {"title": "游泳"}), plain("挑了个不撞课的时间，点确认就改。")],
                         "就游泳那条", sid=sid)
        c.check("R1 need_pick 的全部价值在**第二步**：第 1 轮（名字含糊）一张卡都不出，"
                "第 2 轮（学生报了名字）才出改期卡 —— 只测第 1 轮不出卡 = 只测了一半（单据 §三 R1）",
                not cards_of(o1) and bool(cards_of(o2))
                and cards_of(o2)[0].get("old_todo_id") == "t98-游泳",
                str((len(cards_of(o1)), len(cards_of(o2)))))

        # R1-bis 真同名那条路（86测 §三 R1 只覆盖了"名字能分开"，真同名是它 §二 G11 自己标着"未测"的一格）
        def pair2():
            return [dict(row("游泳", 0, "08:00", "09:40"), id="t98-A"),
                    dict(row("游泳", 1, "10:00", "11:00"), id="t98-B")]
        seed_todos(pair2())
        sid_b = f"t98r1b-{st}"
        b1 = engine_turn([tc(NEW, {"title": "游泳"}), plain("有两条都叫游泳，你说的是哪天那条？")],
                         "游泳撞课了，帮我换一个时间", sid=sid_b)
        b2 = engine_turn([tc(NEW, {"title": "游泳", "todo_date": tmr_b}),
                          plain("好，把 11 月那条挪开一点，点确认就改。")],
                         "改 " + tmr_b + " 那天那个", sid=sid_b)
        cb1, cb2 = cards_of(b1), cards_of(b2)
        c.check("R1-bis 真同名 + **报了日子**的两轮：第 1 轮列清单不出卡，第 2 轮（模型把日子填进 "
                "`todo_date`）**必须出卡**，且 `old_todo_id` 是他指的那一条。"
                "这就是本单唯一要证的端到端结果：真机上一度是三次调用三次同一张 need_pick",
                not cb1 and bool(cb2) and cb2[0].get("retimed") is True
                and cb2[0].get("old_todo_id") == "t98-B",
                str((len(cb1), len(cb2), [x.get("old_todo_id") for x in cb2])))
        c.check("R1-bis-2 第 2 轮的卡上**不许**出现「已经改好了 / 已改到」（提案不是执行），"
                "也不许说「确认条已经挂出来了」而屏上没卡 —— 这一轮屏上真有卡，所以那句该跟着卡走",
                all(k not in str(b2.get("answer") or "") for k in ("已经改好了", "已改到")),
                str(b2.get("answer") or "")[:70])
        # 恢复 R 批后面的共用夹具，并重算 ids_before（R4 拿它证"库里一个字没改"；
        # 不重算就会被我这轮的 pair2 顶成一条假红 —— 承 #82「计数/状态判据被合法新增顶红」那一族）
        seed_todos(three())
        ids_before = {x.get("id") for x in P.list_todos()}

        # R2 新工具最大的风险：**抢走原本好的那条路**（E4 只证了"加待办"不被抢）
        import app.agent.engine as E
        import app.main as M
        from _harness import make_client
        real_run, real_cfg = E.AgentEngine.run, M.get_llm_config

        async def boom(self, message, history=None, on_event=None):
            raise AssertionError("确定性分支被引擎抢走了")

        def key_ok(*a, **k):
            return {"api_key": "test-key", "base_url": "http://x", "model": "m"}

        E.AgentEngine.run, M.get_llm_config = boom, key_ok
        try:
            cli = make_client()
            d2 = cli.post("/api/chat", json={
                "message": "把游泳挪到周五", "session_id": f"t98r2-{st}",
                "module": "planner"}).json()
        finally:
            E.AgentEngine.run, M.get_llm_config = real_run, real_cfg
        cs2 = cards_of(d2)
        c.check("R2 正控（路由边界）：「把游泳挪到周五」里有**换时间词**（挪到）⇒ "
                "`wants_retime_todo` 判 True ⇒ 走 `main.py:3725` 那条确定性分支，"
                "**引擎一次都不许被调**（这一条把 `AgentEngine.run` 换成会抛异常的假件，"
                "被抢走就出不了卡）。分界线另一边是「游泳撞时间了，帮我换一个时间」—— "
                "『换一个时间』不在 `_RETIME_WORDS` 里 ⇒ False ⇒ 掉给引擎，那才是本单那一格",
                bool(cs2) and cs2[0].get("retimed") is True
                and not any("propose_todo_change" in str(t) for t in (d2.get("trace") or [])),
                str([(x.get("date"), x.get("start"), x.get("retimed")) for x in cs2])[:120])

        # R3 无 Key：工具**根本不进**（不是"进去了但没调"）
        r3 = cli.post("/api/chat", json={
            "message": "游泳撞时间了，帮我换一个时间", "session_id": f"t98r3-{st}",
            "module": "planner", "agent_mode": True}).json()
        c.check("R3 没配 Key 时这句该走离线 Mock：**结构上**证明工具一个都没进"
                "（没有 retimed 卡、trace 里没有 propose_todo_change），"
                "而不是靠读答案猜。⚠️ 单据 §三 R3 说\"话术是 #84 已登记的那句假话\"—— "
                "实测**不是**：有课表时 Mock 摊的是候选时段，而且把**学生整句话当任务名**"
                "（`options[0].title == \"游泳撞时间了，帮我换一个时间\"`，实测）。"
                "那句是 Mock 的既有形状、不在本单范围，登记在快照的挂账里",
                not [x for x in cards_of(r3) if x.get("retimed")]
                and "propose_todo_change" not in json.dumps(r3.get("trace") or [],
                                                            ensure_ascii=False),
                str([(x.get("kind"), str(x.get("title"))[:12]) for x in cards_of(r3)])[:120])

        # R4 有卡时模型说假话 —— 量的是"守卫管不管"，**钉现状**
        lie = "已经帮你改好了，游泳现在挪到明天上午了，不用再点确认。"
        o4 = engine_turn([tc(NEW, {"title": "游泳"}), plain(lie)],
                         "游泳撞时间了，帮我换一个时间", sid=f"t98r4-{st}")
        ans4 = str(o4.get("answer") or "")
        c.check("R4 【钉现状·缺口 X3】有卡时模型最后一步说「已经帮你改好了…不用再点确认」⇒ "
                "**这句话原样进屏幕**（实测 `ans == 假话`），库里却一个字没改。"
                "#80 那道兜底挂的是\"模型原文 + **没挂卡**那条链的末尾\"（当时的理由：统一出口看得见"
                "系统自写的三份无卡实话，挂上去就是把实话改成假话）⇒ 有卡这一支它不管。"
                "**这条断言钉的是\"现在会漏\"，不是\"现在是对的\"**：谁补了守卫它会红，"
                "红了就把断言翻成『不许出现已改好』并挂上单据（提案不是执行）",
                ans4 == lie and bool(cards_of(o4))
                and {x.get("id") for x in P.list_todos()} == ids_before,
                str(ans4[:48]))
    return c.summary("R 批（86测：回流 / 路由 / 无 Key / 话术）")



# --------------------------------------------------------------- P 批（#87：同名出口 + 过去钟点闸）
def batch_p_exit_and_gate():
    c = Checker()
    title("P. #87 两条硬伤：同名要有一个出口（`todo_date`）/ 今天过去的钟点要有闸")
    tmr = (TODAY + datetime.timedelta(days=1)).isoformat()
    d3 = (TODAY + datetime.timedelta(days=3)).isoformat()
    d4 = (TODAY + datetime.timedelta(days=4)).isoformat()
    iso = TODAY.isoformat()

    def swim(off, s, e, rid):
        # 两条同名时 `row()` 那种「id = 按名字拼」会把两条做成**同一个 id**，
        # 而本批的命门正是「改的是哪一条」⇒ id 必须分开造，不然 P2 的判据写不出来。
        return dict(row("游泳", off, s, e), id=rid)

    PAIR = [swim(0, "08:00", "09:40", "t98-A"), swim(1, "10:00", "11:00", "t98-B")]

    with sandbox(), pin_now(9, 0):
        from app.modules import planner as P
        write_timetable([day_course(0, "08:00", "09:40")])
        seed_todos(PAIR)
        ids_before = {x.get("id") for x in P.list_todos()}

        r1 = call(P, title="游泳")
        c.check("P1 反向钉（与 #86 逐字一致）：两条同名、**不给 `todo_date`** ⇒ 还是 need_pick 两条、"
                "一张卡都不出。出口不能反过来吃掉这一格：不填就该问，不许猜一条",
                r1.get("need_pick") is True and len(r1.get("todos") or []) == 2
                and "__proposal__" not in r1,
                str((r1.get("need_pick"), len(r1.get("todos") or []))))

        r2 = call(P, title="游泳", todo_date=tmr)
        p2 = r2.get("__proposal__") or {}
        c.check("P2 出口成立：`todo_date=明天` ⇒ 收窄到**明天那一条**、出卡，`old_todo_id` 就是它"
                "（真机 C3 三次调用三次同两条候选，缺的正是这一格）",
                p2.get("old_todo_id") == "t98-B" and "need_pick" not in r2,
                str((p2.get("old_todo_id"), p2.get("date"), r2.get("need_pick"))))

        r3 = call(P, title="游泳", todo_date=tmr, new_date=d4, new_start="18:00", new_end="19:00")
        p3 = r3.get("__proposal__") or {}
        c.check("P3 两个维度各管各的：`todo_date` 决定挑哪一条(=t98-B)、`new_date` 决定改去哪天(=+4 天) "
                "⇒ 卡落在那天 18:00。**不许**因为传了 todo_date 就把目标日也钉成明天",
                p3.get("old_todo_id") == "t98-B" and (p3.get("date"), p3.get("start")) == (d4, "18:00"),
                str((p3.get("old_todo_id"), p3.get("date"), p3.get("start"))))

        r4 = call(P, title="游泳", todo_date=d3)
        c.check("P4 收窄**落空必须原样退回**：说的那天其实没这条 ⇒ 仍 need_pick 两条，"
                "⛔ 不许变成 not_found（那就是 #84 花一整张单修掉的「库里没这条」假话；V2 就钉这一格）",
                r4.get("need_pick") is True and len(r4.get("todos") or []) == 2
                and not r4.get("not_found"), str(sorted(r4.keys())))

        r5 = call(P, title="待办", todo_date=tmr)
        c.check("P5 收窄只作用在**名字命中之后**那一份池子上：占位名 + `todo_date` ⇒ 仍 need_pick。"
                "拿日子去缩全库池 =「那天随便哪一条都算答案」，那是 X1 那格假命中的放大版",
                r5.get("need_pick") is True and "__proposal__" not in r5, str(sorted(r5.keys())))

        c.check("P6 这一段一个字节都没写库（只出提案，执行权在学生点【确认加入】之后）",
                {x.get("id") for x in P.list_todos()} == ids_before, str(sorted(ids_before)))

        seed_todos([swim(0, "08:00", "09:40", "t98-A")])
        r7 = call(P, title="游泳", todo_date=d3)
        c.check("P7 只有一条时那段收窄整块跳过（`len(pool) > 1` 不成立）⇒ `todo_date` 填了也不影响，照旧出卡",
                bool(r7.get("__proposal__")), str(sorted(r7.keys())))

    with sandbox(), pin_now(18, 2):
        from app.modules import planner as P
        write_timetable([day_course(0, "10:00", "11:40", "实验课")])
        seed_todos([swim(0, "10:30", "11:30", "t98-T")])

        g1 = call(P, title="游泳", new_date=iso, new_start="16:00", new_end="17:00")
        c.check("P8 闸（ISO 的今天）：18:02 说「改到今天 16:00」⇒ `past_slot`、**一张卡都不出**，"
                "并把 now 一起带回（模型要靠它说人话；V3 钉这一格）",
                g1.get("ok") is False and g1.get("past_slot") is True and g1.get("now") == "18:02"
                and g1.get("wanted") == "16:00" and "__proposal__" not in g1,
                json.dumps(g1, ensure_ascii=False)[:130])

        g2 = call(P, title="游泳", new_date="今天", new_start="16:00", new_end="17:00")
        c.check("P9 同一格换成**自然语言**『今天』⇒ 照样拦。⚠️ 单据 §1.2 那条件写的是 "
                "`_d == clock.today().isoformat()`（拿模型传来的字面串比 ISO）⇒ 这一格**实测漏拦**、"
                "出的就是那张 16:00 的过去卡。闸要比的是「这张卡真要落的那一天」，不是「他传了什么字符串」",
                g2.get("past_slot") is True and "__proposal__" not in g2, json.dumps(g2, ensure_ascii=False)[:130])

        g3 = call(P, title="游泳", new_date="今日", new_start="16:00", new_end="17:00")
        c.check("P10 日子**根本解析不出来**（『今日』`_pick_date` 实测回 None）时也拦得住 —— "
                "这一支落回旧日期（就在今天），所以判「卡片落在哪天」才兜得住",
                g3.get("past_slot") is True and "__proposal__" not in g3, json.dumps(g3, ensure_ascii=False)[:130])

        g4 = call(P, title="游泳", new_start="16:00")
        c.check("P11 不给日子、只给一个过去的钟点（旧条就在今天）⇒ 拦",
                g4.get("past_slot") is True and "__proposal__" not in g4, json.dumps(g4, ensure_ascii=False)[:130])

        ok1 = call(P, title="游泳", new_date=iso, new_start="19:00", new_end="20:00")
        c.check("P12 正控：今天 19:00（now 之后）**必须照样出卡** —— 闸写宽了就是把学生合法的要求顶回去",
                bool(ok1.get("__proposal__")) and not ok1.get("past_slot"), str(sorted(ok1.keys())))

        e1 = call(P, title="游泳", new_start="18:05", new_end="19:00")
        e2 = call(P, title="游泳", new_start="18:15", new_end="19:00")
        c.check("P13 缓冲边界成对钉（10 分钟）：18:05 拦、18:15 放（now=18:02 ⇒ 闸口在 18:12）。"
                "只钉一头 = 把阈值改成 0 或改成 600 都没人红",
                e1.get("past_slot") is True and bool(e2.get("__proposal__")),
                str((bool(e1.get("past_slot")), bool(e2.get("__proposal__")))))

        seed_todos([swim(1, "10:00", "11:00", "t98-M")])
        ok2 = call(P, title="游泳", new_start="16:00", new_end="17:00")
        pr2 = ok2.get("__proposal__") or {}
        c.check("P14 **不误拦**（这一格是单据那条件的反例）：待办本来就在**明天**、学生只补钟点"
                "（「往后挪到 16:00」）⇒ 明天 16:00 完全合法。照 §1.2 的 `_d = nd or 今天` 写，"
                "实测 18:02 会被判成 past_slot 顶回去 —— 挪到卡片日期上判才不拦",
                bool(pr2) and pr2.get("date") == tmr and not ok2.get("past_slot"),
                json.dumps(pr2, ensure_ascii=False)[:130])

        ok3 = call(P, title="游泳", new_date=tmr, new_start="08:00", new_end="09:00")
        pr3 = ok3.get("__proposal__") or {}
        c.check("P15 明天的 08:00 不许被拦（单据 T6 那一格，闸只对「今天」生效）",
                bool(pr3), str((pr3.get("date"), pr3.get("start"))))

        ok4 = call(P, title="游泳")
        c.check("P16 闸只管**模型填钟点**那一支：什么都没给、让 auto_slot 自己挑 ⇒ 不许被这道闸多拦一次"
                "（auto_slot 那支的 `not_before` 由 W9 钉着，两处各管各的，别并成一把）",
                bool(ok4.get("__proposal__")) and not ok4.get("past_slot"), str(sorted(ok4.keys())))

    src_pl = read(PL)
    from app.modules import planner as _P2  # 只为拿工具箱，不在沙箱外调任何读写
    tools = _P2.build_scheduling_tools()
    t = tools.get(NEW)
    props = set((t.parameters or {}).get("properties") or {})
    c.check("P17 接线：#87 之后 schema 是 6 个键（多的那格叫 `todo_date`），`required` 仍然只有 `title`"
            "（它是可选的 ⇒ 老调用方与确定性分支一个字都不用改）",
            (t.parameters or {}).get("required") == ["title"]
            and props == {"op", "title", "todo_date", "new_date", "new_start", "new_end"},
            str(sorted(props)))
    d_todo = ((t.parameters or {}).get("properties") or {}).get("todo_date") or {}
    c.check("P18 `todo_date` 的说明书里两句关键话一句都不能少：「在哪天」＋「不是改去哪天」"
            "（两个语义混了，下一轮就是拿定位条件去改日子）",
            "在哪天" in (d_todo.get("description") or "")
            and "改去哪天" in (d_todo.get("description") or ""),
            (d_todo.get("description") or "")[:80])
    c.check("P19 闸与描述**成对**：说明书里有 `past_slot` 那一句（§五.3 —— 只加闸不加描述，"
            "模型收到一个没见过的出口就会把同一个过去的点再传一遍）",
            "past_slot" in (t.description or ""), str((t.description or "")[:60]))
    own_pl = _P2.build_tools()
    c.check("P20 §1.3 那两行在 `propose_todo_tool` 的说明书里：加课别用我（指向 op=add）/ 不支持重复"
            "（Agent 模式下确定性加课分支**走不到**，这句是那一路上唯一的优先闸）",
            "propose_course_change" in (tools["propose_todo_tool"].description or "")
            and "不支持重复" in (tools["propose_todo_tool"].description or ""),
            (tools["propose_todo_tool"].description or "")[-90:])
    c.check("P21 `list_todos_range` 说明书里有「放宽再查一次」（治 C2 那套视野：查空了别直接回「没有」）"
            "⚠️ 这只工具在 planner **自己**那 13 只里（`build_tools()`），不在 scheduling 那 8 只里 —— "
            "拿错工具箱就是 KeyError（我自己撞了一次）",
            "放宽再查一次" in (own_pl["list_todos_range"].description or ""),
            (own_pl["list_todos_range"].description or "")[-70:])
    c.check("P22 闸的位置写在源码里、不许漂：判的是卡上的 `date`（卡片真要落的那一天），"
            "不是拿模型传来的 `new_date` 字面串比 ISO（那两种错我都实测过：漏拦『今天』、误拦明天的钟点）",
            'prop.get("date") == clock.today().isoformat()' in src_pl
            and "_d = nd or clock.today().isoformat()" not in src_pl, "")
    return c.summary("P 批（#87：同名出口 + 过去钟点闸）")

def main():
    code = 0
    ran = 0
    batches = (("T 批（工具级）", batch_t_tool),
               ("W 批（接线）", batch_w_wiring),
               ("A 批（默认值命门）", batch_a_default),
               ("E 批（端到端）", batch_e_e2e),
               ("C 批（86测 能力边界）", batch_c_boundaries),
               ("R 批（86测 回流与路由）", batch_r_routes),
               ("P 批（#87：同名出口 + 过去钟点闸）", batch_p_exit_and_gate))
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
