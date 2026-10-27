"""#34（P0）：驿站端不许读、也不许写**学生个人数据** —— 端边界缺失的护栏。

用户 2026-10-17 12:26 真机截图原话：「出大问题了」。
现象：在 `/station`（驿站端）输入「我的代办」，客服回了
「📋 你目前的待办（共 1 条）：· 铅球比赛｜2026-10-17（周六）11:00-12:00」。
定性不是观感问题：**店员那台机器读到了学生的个人待办**，而同一个形状的分支还能写（加/删/改期/改课表）。

根因（见 `app/main.py` 端级闸门那段注释）：`/api/chat` 里那二十多条确定性分支的判据
全是"这句话长什么样"，**没有一处问"谁在说"**。
所以修法不是给每条分支补 `and module != "station"`（抄二十遍必漏一处，红线 4/11），
而是把整段圈进 `if not _station_surface:` —— 驿站端**走不到**这里。

⚠️ 三条本仓规矩照办：
  ① 起点自己种（`seed_todos`/`write_timetable`），不依赖谁的演示数据（红线 18）；
  ② 写没写成一律**读盘比字节**，不看状态码（红线 24 同一个病）；
  ③ 形状/位置断言先剔注释，且判"走不到"要用 AST 位置，不认行号数字。
"""
import ast
import datetime
import hashlib
import json
import os
import re

from _harness import (Checker, data_path, make_client, patch_llm, sandbox,
                      seed_todos, title, write_timetable)

SRC_MAIN = "app/main.py"
SRC_STATION = "app/modules/station.py"
PROBE = "探针-不许念给店员"
# ⚠️ 不许写死（挂账 #69，#56-C）：原来钉 `"2026-12-31"`，跨过那天它就是**过去**，
#    红线 17「待办不许落在过去的日子」会正确拒收 —— 红的是夹具不是功能（test_42/test_71 同族）。
FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
COURSES = [{"day": 6, "start": "19:00", "end": "21:00",
            "course": "晚课·社团", "location": "体育馆"}]


def _fp(*parts):
    """当前沙箱里某个文件的指纹（人话：一个字节动了就知道）。"""
    p = data_path(*parts)
    if not os.path.exists(p):
        return "<不存在>"
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def _todos():
    p = data_path("student", "todos.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8")).get("todos") or []


def _chat(cli, msg, module=None, sid="t61"):
    body = {"message": msg, "session_id": sid}
    if module:
        body["module"] = module
    r = cli.post("/api/chat", json=body)
    return r.status_code, (r.json() if r.status_code == 200 else {})


def batch_a_station_cannot_touch():
    """A 批 · 驿站端：读不到、也写不进（六句都是真机验收里要挨着说的那几句）。"""
    c = Checker()
    title("A. 驿站端（module=station）读不到 / 写不进学生个人数据")
    with sandbox():
        cli = make_client()                      # 先导入 app，再打桩（顺序反了会把桩塞进注解里）
        restore = patch_llm("学生个人日程我这边看不到，请用学生端查。", with_key=False)
        try:
            seed_todos([{"title": PROBE, "date": FUTURE, "start": "10:00", "end": "11:00"}])
            write_timetable(COURSES)

            sc, d = _chat(cli, "我的代办", "station", "t61-list")
            ans = d.get("answer") or ""
            c.check("A1 驿站端说「我的代办」：回的是 station（没被改判去 planner），且**正文里不许出现探针待办的标题**",
                    sc == 200 and d.get("module") == "station" and PROBE not in ans,
                    f"HTTP {sc} module={d.get('module')} answer={ans[:70]}")
            c.check("A1-bis 也不许出现「列出待办」那套回执形状（「你目前的待办（共 N 条）」就是事故截图那句）",
                    "你目前的待办" not in ans and "待办（共" not in ans, ans[:70])

            for tag, msg, target in [
                ("A2 加待办", f"帮我加个铅球比赛，{FUTURE} 10:00-11:00", ("student", "todos.json")),
                ("A3 改课表", "上传课表", ("student", "timetable.json")),
                ("A4 删待办", f"删掉{PROBE}那条待办", ("student", "todos.json")),
                ("A5 改期", f"把{PROBE}改到{FUTURE} 20:00", ("student", "todos.json")),
                ("A6 标完成", f"{PROBE} 办完了", ("student", "todos.json")),
            ]:
                before = _fp(*target)
                n_before = len(_todos())
                sc2, d2 = _chat(cli, msg, "station", f"t61-{tag[:2]}")
                c.check(f"{tag}：驿站端说完这句，{target[1]} **一个字节都不许变**",
                        sc2 == 200 and _fp(*target) == before,
                        f"HTTP {sc2} 指纹 {before[:8]}→{_fp(*target)[:8]}")
                if target[1] == "todos.json":
                    c.check(f"{tag}-bis 条数还是 {n_before} 条，且**没挂出任何确认卡**",
                            len(_todos()) == n_before and not (d2.get("options") or []),
                            f"条数 {len(_todos())} options={len(d2.get('options') or [])}")

            # 本职不许被一起关掉：查快递台账是驿站端该干的（它只有两个查询工具）
            sc3, d3 = _chat(cli, "现在有哪些快递还没取走", "station", "t61-express")
            c.check("A7 驿站端问快递（本职）照旧走通：HTTP 200 + module=station（闸门不是一刀切）",
                    sc3 == 200 and d3.get("module") == "station", f"HTTP {sc3}")

            # ★ 第二条出口不许绕过闸门：`/api/chat` 按 Accept 协商出 SSE（#07 流式那一刀），
            #   两条出口都走 `_chat_impl`，但"走得到/走不到"这件事必须**各出口各验一遍** ——
            #   只测 JSON 那条等于给"换个请求头就能读学生数据"留了门。
            r_sse = cli.post("/api/chat", headers={"Accept": "text/event-stream"},
                             json={"message": "我的代办", "module": "station", "session_id": "t61-sse"})
            body = r_sse.text or ""
            c.check("A8 SSE 那条出口同样读不到（换 Accept 头绕不过闸门）",
                    r_sse.status_code == 200 and PROBE not in body,
                    f"HTTP {r_sse.status_code} 流里长度 {len(body)}")
        finally:
            restore()
    return c


def batch_b_student_still_works():
    """B 批 · 对照：学生端照旧。缺了这批，"关掉驿站"和"关掉功能"就分不清了。"""
    c = Checker()
    title("B. 学生端照旧（防一刀切）")
    with sandbox():
        cli = make_client()
        restore = patch_llm("{}", with_key=False)
        try:
            seed_todos([{"title": PROBE, "date": FUTURE, "start": "10:00", "end": "11:00"}])
            write_timetable(COURSES)

            sc, d = _chat(cli, "我的代办", None, "t61-stu-list")
            ans = d.get("answer") or ""
            c.check("B1 **不传 module**（学生端主路径）说「我的代办」→ 照旧列出那条探针待办",
                    sc == 200 and PROBE in ans, f"HTTP {sc} answer={ans[:80]}")

            sc2, d2 = _chat(cli, f"帮我加个游泳，{FUTURE} 19:00-20:30", None, "t61-stu-add")
            opts = d2.get("options") or []
            c.check("B2 学生端加待办 → 照旧出**待办确认卡**（没点之前不落库）",
                    sc2 == 200 and len(opts) == 1 and opts[0].get("kind") == "todo_add"
                    and d2.get("awaiting_choice") is True,
                    f"options={str(opts)[:80]}")
            c.check("B2-bis 卡片只是提案：todos.json 还是 1 条（探针那条），没多出游泳",
                    len(_todos()) == 1 and all("游泳" not in (t.get("title") or "") for t in _todos()),
                    f"{len(_todos())} 条")

            sc3, d3 = _chat(cli, "我的代办", "faq", "t61-faq-list")
            c.check("B3 学生换到**问答栏**（module=faq）说同一句 → 仍列得出来（跨栏操作待办是有意保留的）",
                    sc3 == 200 and PROBE in (d3.get("answer") or ""),
                    f"HTTP {sc3} answer={(d3.get('answer') or '')[:70]}")

            # 低代码那一路（#34 施工单 §3.4）：`custom:<id>` 是**学生端**用的，闸门不许跟着拦
            from app import store
            store.add_service({"id": "lost1", "name": "失物招领",
                               "triggers": ["我丢了饭卡"], "collect_fields": ["物品描述"],
                               "reply_template": "已登记。", "table": "lostfound",
                               "created_at": "测试"})
            sc4, d4 = _chat(cli, "我的代办", "custom:lost1", "t61-cus-list")
            c.check("B4 闸门只认 station：低代码栏（custom:lost1）说「我的代办」**照旧列得出来**",
                    sc4 == 200 and PROBE in (d4.get("answer") or ""),
                    f"HTTP {sc4} answer={(d4.get('answer') or '')[:70]}")

            # B5 是 A8 的**对照**，少了它 A8 就是假绿：SSE 那条出口在学生这边确实把待办发出来了，
            #   才说明"驿站端流里没有"是因为闸门拦住了，而不是因为那条流本来就什么都不回。
            r_stu_sse = cli.post("/api/chat", headers={"Accept": "text/event-stream"},
                                 json={"message": "我的代办", "session_id": "t61-sse-stu"})
            c.check("B5 对照：SSE 那条出口**学生说同一句照旧发得出待办**（证明 A8 的红是闸门拦的，不是那条流是空的）",
                    r_stu_sse.status_code == 200 and PROBE in (r_stu_sse.text or ""),
                    f"HTTP {r_stu_sse.status_code} 流里长度 {len(r_stu_sse.text or '')}")
        finally:
            restore()
    return c


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _code_only(src):
    """剔掉整行注释再比（负向断言不剔就会把"说明书"当代码判红，本仓反复踩过）。"""
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(re.sub(r"#.*$", "", ln))
    return "\n".join(out)


def _strip(text):
    """剔注释（HTML 块 + JS 块/行注释）—— 页面上"数次数"的断言先剥再比。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(ln.split("//")[0] for ln in t.splitlines())


def batch_c_shape():
    """C 批 · 形状与位置：钉的是"走不到"，不是"回话好不好看"。"""
    c = Checker()
    title("C. 形状：判据一处、闸门在硬分支之前、跑 agent 只有一份")
    src = open(SRC_MAIN, encoding="utf-8").read()
    code = _code_only(src)
    tree = ast.parse(src)
    fn = _fn(tree, "_chat_impl")
    ifs = [n for n in fn.body if isinstance(n, ast.If)]
    guard = [n for n in ifs if ast.unparse(n.test) == "not _station_surface"]
    c.check("C1 端级闸门在 `_chat_impl` 里恰好一处（判据写成 `not _station_surface`，不是就地比字面量）",
            len(guard) == 1, f"{len(guard)} 处")
    c.check("C1-bis 端级判据**只算一次**：`_station_surface = module_key == \"station\"` 全文件 1 处",
            code.count('_station_surface = module_key == "station"') == 1,
            str(code.count('_station_surface = module_key == "station"')))

    span = (guard[0].lineno, guard[0].body[-1].end_lineno) if guard else (0, 0)

    def inside(pred, label):
        hits = [n for n in ast.walk(fn) if isinstance(n, ast.If) and pred(ast.unparse(n.test))]
        outside = [n.lineno for n in hits if not (span[0] < n.lineno <= span[1])]
        c.check(f"C2 {label} 整条落在闸门**圈内**（驿站端走不到；圈外一处就等于没修）",
                bool(hits) and not outside, f"命中 {len(hits)} 处，圈外 {outside}")

    inside(lambda t: "wants_list_todo" in t, "列出待办分支")
    inside(lambda t: "wants_add_todo" in t, "加待办分支")
    inside(lambda t: "wants_remove_todo" in t, "删待办分支")
    inside(lambda t: "wants_retime_todo" in t, "改期分支")

    # 课表那条要分开说：**圈外确实还有一处**（组装回话的尾巴上，把模型已经出的课表卡统一成一句"要不要改"）。
    # 实测它无害：驿站端的工具箱里只有 4 个只读工具（两个快递查询 + 两个读上传文件），
    # 没有 `propose_timetable_change`，那条 `if` 里 `_tt_opts` 恒为空 —— 它只改文案、不碰数据。
    # 所以这里钉的是"**圈外那处不许写库**"，而不是硬要它也必须圈进来（那等于把尾巴整段搬进闸门，
    # 而尾巴是学生端和驿站端共用的）。判据：圈外的子树里一个写入口都没有。
    _WRITERS = ("save_timetable", "apply_pending_timetable_change", "_write_todo", "add_todo",
                "delete_todo", "save_pending", "mark_applied", "set_todo_status")
    tt_all = [n for n in ast.walk(fn)
              if isinstance(n, ast.If) and "looks_like_timetable_import" in ast.unparse(n.test)]
    tt_inside = [n for n in tt_all if span[0] < n.lineno <= span[1]]
    tt_outside = [n for n in tt_all if n not in tt_inside]
    c.check("C2 课表导入硬分支：**闸门圈内有一处**（驿站端走不到那条直接出卡的路）",
            bool(tt_inside), f"圈内 {len(tt_inside)} / 圈外 {len(tt_outside)}")
    for n in tt_outside:
        sub = ast.unparse(n)
        hit = [w for w in _WRITERS if w + "(" in sub]
        c.check(f"C2-bis 圈外那处课表分支（L{n.lineno}）只许改回话文案，**一个写入口都不许有**"
                "（哪天它开始落库，驿站端就重新打通了）",
                not hit, f"出现写入口 {hit}")

    # ★ #61-C 之后 `_run_agent_loop` 里也多了一处课表尾巴（它原本就在 `_chat_impl` 尾部，
    #   是整段搬过去的，逻辑一个字没改）。它同样在闸门**之外**、驿站端同样会走到 ⇒
    #   那条"只许改文案、不许有写入口"必须跟着搬过去，**不能因为它换了函数就少查一处**。
    #   （实测：改口第一版就是这儿静默少了一条，test_61 从 34 条变 33 条，而全量还是全绿。）
    loop_fn = _fn(tree, "_run_agent_loop")
    tt_loop = [n for n in (ast.walk(loop_fn) if loop_fn else [])
               if isinstance(n, ast.If) and "looks_like_timetable_import" in ast.unparse(n.test)]
    for n in tt_loop:
        hit = [w for w in _WRITERS if w + "(" in ast.unparse(n)]
        c.check(f"C2-bis2 搬进 `_run_agent_loop` 的那处课表尾巴（L{n.lineno}）仍然只改文案、"
                "一个写入口都不许有（驿站端与学生端共用这一段）",
                not hit, f"出现写入口 {hit}")

    if guard:
        tb = [n.lineno for n in ast.walk(fn)
              if isinstance(n, ast.If) and "looks_like_timetable_import" in ast.unparse(n.test)]
        c.check("C3 闸门排在课表那条**硬信号之前**（顺序错了 = 驿站端照样能改学生课表）",
                bool(tb) and guard[0].lineno < min(tb), f"闸门 L{guard[0].lineno} / 课表 L{min(tb) if tb else '无'}")
        c.check("C4 圈起来的分支不止一两条（整段跳过才是本单要的形状；只圈一条就是抄判据的写法）",
                len([n for n in guard[0].body if isinstance(n, ast.If)]) >= 10,
                f"圈内有 {len([n for n in guard[0].body if isinstance(n, ast.If)])} 条 if")

    c.check("C5 跑 agent 的代码**只有一份**：`AgentEngine(` 全文件 1 处",
            code.count("AgentEngine(") == 1, str(code.count("AgentEngine(")))
    # ★ #61-C 把那 272 行引擎段从 `_chat_impl` 抽成了 `_run_agent_loop`（好让"正常走到这儿"和
    #   "Agent 模式跳过快路"两个调用点共用同一份）。原来这条钉的是"`engine.run` 在闸门之后"，
    #   抽完之后它压根不在 `_chat_impl` 里了 —— 那是**改口**，不是放宽：现在钉三件事，
    #   每一件都比原来更难被绕过。
    loop = _fn(tree, "_run_agent_loop")
    runs_in_impl = [n for n in ast.walk(fn) if "engine.run" in ast.unparse(n)]
    runs_in_loop = [n for n in ast.walk(loop) if "engine.run" in ast.unparse(n)] if loop else []
    c.check("C5-bis `engine.run` 只写在 `_run_agent_loop` 那一份里"
            "（`_chat_impl` 里出现一处就是复制了一份引擎代码去各改各的）",
            bool(runs_in_loop) and not runs_in_impl,
            f"`_run_agent_loop` {len(runs_in_loop)} 处 / `_chat_impl` {len(runs_in_impl)} 处")
    c.check("C5-bis2 全文件 `_run_agent_loop` 的定义恰好一处、调用点恰好三处"
            "（★ #76 把这一条从 2 改口成 3，是**改口不是放宽**：委托那两处（短路 + 尾部）+ #76 让渡点一处。"
            "原话『出现第三次就是有人复制了参数形状』现在由 C5-bis3 的**名分**和 `test_82` A16/A17 的"
            "**形状一致**两把更硬的尺子接着管；这里的数只挡『有人把让渡点写成不 return 的半接管』）",
            code.count("async def _run_agent_loop(") == 1
            and code.count("await _run_agent_loop(") == 3
            and code.count("return await _run_agent_loop(") == 3,
            f"定义 {code.count('async def _run_agent_loop(')} / 调用 {code.count('await _run_agent_loop(')}"
            f" / 带 return {code.count('return await _run_agent_loop(')}")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_run_agent_loop"]
    inside = [n.lineno for n in calls if span[0] < n.lineno <= span[1]]
    after = [n.lineno for n in calls if n.lineno > span[1]]
    before = [n.lineno for n in calls if n.lineno <= span[0]]
    ifs = [n for n in ast.walk(fn) if isinstance(n, ast.If)]

    def _inner_if(node):
        """包住这个调用的**最里面**那条 if（外层 if 的 unparse 含着下面每一条分支，
        从外往里比会把短路点也认成让渡点 —— 同一个坑见 `test_83._line_of` 的注释②）。"""
        cands = [i for i in ifs if i.lineno <= node.lineno <= i.end_lineno]
        return max(cands, key=lambda i: i.lineno) if cands else None

    def _tagged(node):
        i = _inner_if(node)
        if i is None:
            return "尾部"
        if any(isinstance(x, ast.Call) and getattr(x.func, "id", "") == "log_intent_gap"
               for x in ast.walk(i)):
            return "让渡"          # #76：让渡那一支的入口，体内先记一笔缺口账
        return "短路" if "_agent_mode" in ast.unparse(i.test) else "别的"

    named = {}
    for n in calls:
        named.setdefault(_tagged(n), []).append(n.lineno)
    c.check("C5-bis3 三处调用点各有名分、位置也钉死：短路点在闸门圈内且排在让渡点之前"
            "（#61-C 的 Agent 模式短路，驿站端走不到它）、让渡点也在圈内但排在闸门之后（#76 让渡口，"
            "所以驿站端与 Agent 模式开着的请求都到不了它）、尾部那处两端共用且必须排在闸门圈外；"
            "闸门之前出现一处就是把引擎提到了端边界前面，冒出『别的』这一类就是有人新加了一处没名分的调用",
            len(inside) == 2 and len(after) == 1 and not before
            and set(named) == {"短路", "让渡", "尾部"}
            and len(named["短路"]) == len(named["让渡"]) == len(named["尾部"]) == 1
            and named["短路"][0] < named["让渡"][0] < span[1] < named["尾部"][0],
            f"圈内 {sorted(inside)} / 圈后 {sorted(after)} / 圈前 {sorted(before)} / {named}")

    c.check("C6 驿站端拿不到排程工具：`if not _station_surface and _schedule_tools_allowed(` 恰好 1 处",
            code.count("if not _station_surface and _schedule_tools_allowed(") == 1,
            str(code.count("if not _station_surface and _schedule_tools_allowed(")))

    # §七 不许碰的：有人会把"从驿站工具箱删工具"当修法 —— 那里本来就没有写工具，删了等于什么都没改
    sstat = ast.parse(open(SRC_STATION, encoding="utf-8").read())
    bt = _fn(sstat, "build_tools")
    tool_names = sorted({kw.value.value for call in (ast.walk(bt) if bt else [])
                         if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Tool"
                         for kw in call.keywords if kw.arg == "name"
                         and isinstance(kw.value, ast.Constant)})
    c.check("C7 驿站工具箱仍然只有两个查询（不许拿「删工具」当本单的修法）",
            tool_names == ["get_by_code", "list_pending_by_student"], str(tool_names))
    # ⚠️ C7 这份名单在 #36 换过一次名字：`list_pending_pickup` 是**学生口径**那一份
    #   （说「你有 N 件、请尽快取」），驿站换成了 `list_pending_by_student`（全店 + 按学号分组 +
    #   店员语气）。本单要钉的本数是"驿站手里只有查询、一个写入都没有"—— 名字换了，这件事没变，
    #   所以这里是**改口**而不是放松：断言仍然要求恰好这两件、且都不带写。

    p = _fn(sstat, "build_system_prompt")
    ptxt = ast.unparse(p) if p else ""
    c.check("C8 提示词里有「端边界」那一条：个人日程/待办/课表不归驿站客服，且指路去学生端",
            "个人日程" in ptxt and "学生端" in ptxt and "不要猜" in ptxt, "")
    c.check("C9 分支区里不许再出现散的 `== \"station\"` 判据（本单要避免的写法，出现一处就是抄第二遍）",
            'module_key == "station"' not in code.split("_station_surface = module_key == \"station\"")[-1],
            f"判据之后还出现 {code.split('_station_surface = module_key == \"station\"')[-1].count('module_key == \"station\"')} 处")

    # C10 端边界的地基在前端那一行：**`module` 一摘，后端就以为这是学生在说话** ——
    #   闸门当场失效，而且不会报错，只是又回到事故那个样子。
    #   ⚠️ 剔注释再数：station.html 里那句注释「显式带 module:"station"」跟代码长得一样。
    sta_raw = open("app/static/station.html", encoding="utf-8").read()
    sta_code = _strip(sta_raw)
    c.check("C10 驿站端每次对话都显式带 `module: \"station\"`（闸门靠它认出这是店员，摘掉就形同虚设）",
            sta_code.count('module: "station"') == 1
            and re.search(r'JSON\.stringify\(\{[^}]*module: "station"[^}]*\}\)', sta_code) is not None,
            f"代码里 {sta_code.count('module: \"station\"')} 处")
    return c


def main():
    print("\n" + "=" * 60)
    print("test_61_surface_boundary：驿站端不许读/写学生个人数据（#34 P0）")
    print("=" * 60)
    code = 0
    code |= batch_a_station_cannot_touch().summary("A 批（驿站端六句：读不到、写不进、本职照旧）")
    code |= batch_b_student_still_works().summary("B 批（对照：学生端 / 问答栏 / 低代码栏照旧）")
    code |= batch_c_shape().summary("C 批（形状：一处判据、硬分支之前、agent 只有一份）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
