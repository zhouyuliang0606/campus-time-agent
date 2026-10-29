"""#61 的护栏：只读工具跨栏挂载（A）+ 判定进轨迹的验收（B）+ Agent 模式开关（C）。

三批形状 + 两批行为，各钉一件事：

  A 形状：账本只收只读那 16 只、写侧一只都不许进来；挂载在端边界**圈内**；
          开关判据全项目一处；短路点在红线三件之后、3b 之前；引擎段只有一份、两个调用点。
  B 行为：模型每一栏实际拿到几只手（数字写死）、写侧一只都不许出现、
          驿站端一只手都不许多挂、`get_by_code` 用的是学生口径、只读池子跑一遍不写库。
  C 行为：Agent 模式确实跳过了"替模型抢话"那几条快路，但**确认执行仍是当场执行**（安全红线），
          闲聊仍是一轮（不许为了"看起来在推理"多烧一次），不给参数 = 与关着逐字相同。
  D 验收：#61-B 那三条（两条出口都带 🧭、`read` 的三个覆盖点不吞 `reasons`、前置行都带 step:0）。
  E 前端：node 真跑页面里那份 `agentOn`（默认关、"1" 才开），注入点只有一处、传的是布尔。

⚠️ 为什么 B/C 两批都得打**两处** get_llm_config：
   `app/main.py` 是 `from app.config import get_llm_config`（by-value 导入），
   只打 `app.config` 那一处时，`_run_agent_loop` 里"没配密钥就走离线 Mock"那道闸看的还是真配置
   —— 实测症状是"引擎明明被调了，trace 里却没有 🤔"，很容易误判成短路点写错了（本单第一版就这么绕了一圈）。
   所以 `_patch()` 里两处一起打，别只打一处。
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from _harness import (Checker, chat_nodes, chat_src, data_path, make_client, sandbox,  # noqa: E402
                      seed_todos, title, wipe_todos, write_accounts, write_express,
                      write_takeout, write_timetable)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(PROJ, "app", "main.py")
RO = os.path.join(PROJ, "app", "agent", "readonly.py")
STUDENT = os.path.join(PROJ, "app", "static", "student.html")
ESC_JS = os.path.join(PROJ, "app", "static", "esc.js")

# 16 只只读工具的名字（钉名单，不钉数量 —— 数量跟着名单走，改名单是另一件事）
RO16 = ["find_free_slots", "get_by_code", "get_day_courses", "get_order", "get_weekly_timetable",
        "list_active_orders", "list_day_todos", "list_my_orders", "list_my_packages",
        "list_pending_pickup", "list_uploaded_files", "plan_task", "read_uploaded_file",
        "list_todos_range", "search_docs", "search_kb"]

# 写侧：#61 一条都不许跨栏挂出去（`_schedule_tools_allowed` 那道判据不动）
# ★ #86 加了一只 `propose_todo_change`（改期待办）⇒ 名单 11 → 12。这份表是 A6/B2-bis 的**反向断言地基**：
#   新增写侧工具不登记进来，那两条就静默失去保护（症状是"什么都没红"，而护栏已经不再覆盖这一只）。
WRITE12 = ["propose_clear_timetable", "propose_course_change", "propose_slots",
           "propose_timetable_change", "propose_todo_done", "propose_todo_mode_tool",
           "propose_todo_remove", "propose_todo_slots_tool", "propose_todo_tool",
           "propose_todo_change",
           "propose_custom_record", "submit_work_order"]

# 实测的"每栏模型拿到几只手"（Agent 模式强制进引擎后取的数；数字变了就是挂载清单变了）
# ★ #86：planner 25 → **26**（`build_scheduling_tools()` 里多了 `propose_todo_change`，单据 §3.3 那处）。
#   另外四栏的数**没动** —— 不是漏钉：faq/express/takeout 的样本句没开 `_schedule_tools_allowed` 那道闸，
#   schedule 那三只 `propose_todo_*` 来自它**自己的**工具箱（实测 B1 五行里只有 planner 这一行会变）。
#   ⇒ 施工单 §3.3 预测的 `test_31` A3「13→14」**不会红**：那只工具没进 `planner.build_tools()`，
#     那张账（13 只）钉的是"没有第二个人顺手往里塞"，本单塞的那一处不在这本账上（test_98 W1 两头各钉一条）。
COUNTS = {"faq": 16, "schedule": 19, "express": 17, "takeout": 17, "planner": 26}
import datetime as _dt
_mon = _dt.date.today() + _dt.timedelta(days=(7 - _dt.date.today().weekday()) % 7 or 7)
_NEXT_MONDAY = _mon.isoformat()
_NEXT_SUNDAY = (_mon + _dt.timedelta(days=6)).isoformat()

SENT = {"faq": "图书馆几点关门", "schedule": "今天有什么课", "express": "我有哪些快递",
        "takeout": "我的外卖到哪了", "planner": "周三什么时候有空"}


# --------------------------------------------------------------------------- 假模型
class RecLLM:
    """记录"每一轮把哪些工具摆给了模型"，回一句没有工具调用的话。

    只记不答：本单要验的是**挂载清单**与**走没走引擎**，不是模型会说啥。
    """

    def __init__(self, *a, **k):
        self.seen = []

    async def chat(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        self.seen.append(sorted(t["function"]["name"] for t in (tools or [])))
        return {"content": "好"}

    async def stream(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        self.seen.append(sorted(t["function"]["name"] for t in (tools or [])))
        yield {"content": "好"}


def _patch():
    """把模型客户端换成 RecLLM，返回 (恢复函数, 那个假货)。见文件头那条两处打桩的警告。"""
    import app.config as _cfg
    import app.llm.client as _client
    import app.main as _M
    box = {}

    def _factory(*a, **k):
        llm = RecLLM(*a, **k)
        box["llm"] = llm
        return llm

    cfg = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x", "model": "m"}   # noqa: E731
    old = (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config)
    _client.DeepSeekClient = _factory
    _cfg.get_llm_config = cfg
    _M.get_llm_config = cfg

    def restore():
        _client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config = old
    return restore, box


def _engine_round(box):
    """引擎那一轮实际摆给模型的工具名（判定层那一轮不带 tools，所以取**最长**的那次）。"""
    llm = box.get("llm")
    seen = (llm.seen if llm else []) or []
    with_tools = [s for s in seen if s]
    return max(with_tools, key=len) if with_tools else []


def _sse_done(cli, msg, mod, sid, agent=None):
    """走 SSE 那条出口，取 `done` 里那份权威答案（与 JSON 出口同构）。"""
    body = {"message": msg, "session_id": sid, "module": mod}
    if agent is not None:
        body["agent_mode"] = agent
    r = cli.post("/api/chat", json=body, headers={"Accept": "text/event-stream"})
    for line in (r.text or "").splitlines():
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            if ev.get("k") == "done":
                return ev.get("v") or {}
    return {}


# --------------------------------------------------------------------------- A 形状
def batch_a_shape():
    c = Checker()
    title("A 批 · 形状：账本只收只读、挂载在圈内、短路点位置、引擎段一份两调用点")
    ro_src = io.open(RO, encoding="utf-8").read()
    main_src = io.open(MAIN, encoding="utf-8").read()
    code = re.sub(r"#.*$", "", main_src, flags=re.M)

    tree = ast.parse(ro_src)
    spec = None
    for n in tree.body:
        # 带标注的赋值是 AnnAssign（`READONLY_SPEC: dict[str, str] = {...}`），不是 Assign：
        # 只找 Assign 的话 spec 永远是 None，A1/A3 会以"我的测试读错了节点"的理由长红。
        val = n.value if isinstance(n, (ast.Assign, ast.AnnAssign)) else None
        name = (n.target.id if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                else (n.targets[0].id if isinstance(n, ast.Assign)
                      and isinstance(n.targets[0], ast.Name) else None))
        if name == "READONLY_SPEC" and isinstance(val, ast.Dict):
            spec = {k.value: v.value for k, v in zip(val.keys, val.values)}
    c.check("A1 READONLY_SPEC 就是那 16 只（名单逐字钉住，不是钉数量 —— 数量对了名单错了等于没钉）",
            sorted(spec or []) == sorted(RO16), f"实际 {sorted(spec or [])}")
    c.check("A2 反向断言：写侧那 11 只一只都不许在账本里（M1 的靶子）",
            not (set(spec or {}) & set(WRITE12)),
            f"混进来的：{sorted(set(spec or {}) & set(WRITE12))}")
    c.check("A3 `get_by_code` 的来源写死 express（驿站那份同名不同实现，M2 的靶子）",
            (spec or {}).get("get_by_code") == "express", f"来源写的是 {(spec or {}).get('get_by_code')}")
    c.check("A4 账本里不许有 `list_pending_by_student`（它是店员口径，既不算只读池也不该跨栏）",
            "list_pending_by_student" not in (spec or {}), "")
    # 账本里每条的来源模块**都得真的取得到**（写错模块名不会报错，只会静默少一只手）
    from app.agent.readonly import READONLY_SPEC, WRITE_TOOL_NAMES, readonly_tools

    pool = readonly_tools()
    c.check("A5 16 只全都能从各自模块取到（缺一只就是静默跳过，症状是「那一栏查不到」，最难查）",
            len(pool) == len(READONLY_SPEC), f"取到 {len(pool)} / 账本 {len(READONLY_SPEC)}")
    c.check("A6 WRITE_TOOL_NAMES 与实测的写侧名单一致（这份表是反向断言的依据，它漏了 M1 就白设）",
            sorted(WRITE_TOOL_NAMES) == sorted(WRITE12), sorted(WRITE_TOOL_NAMES))
    c.check("A7 `files` 不在 REGISTRY 这一事实被处理了（照抄 `REGISTRY[mod][1]` 会 KeyError→静默少 3 只）",
            '"files"' in ro_src and "files_build_tools" in ro_src, "")

    mtree = ast.parse(main_src)
    impl = [n for n in ast.walk(mtree) if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_chat_impl"][0]
    ifs = [n for n in impl.body if isinstance(n, ast.If)]
    gate = [n for n in ifs if ast.unparse(n.test) == "not _station_surface"]
    if not gate:
        # 闸门没了（有人把它改成 `if True:`、或整段搬走）时后面每条都要行号，硬往下走只会崩在 IndexError 上，
        # 崩了这一批就"看不见数"—— 先出一条红、把剩下的数交出来。
        c.check("A8 端边界闸门 `if not _station_surface:` 还在（本单的挂载就写在它圈内，没了就无处可钉）",
                False, "0 处")
        return c
    c.check("A8 挂载写在端边界闸门**圈内**（不是圈外再补一句同款 if —— 那会长出第二道闸门）",
            len(gate) == 1 and any("readonly_tools()" in ast.unparse(n) for n in ast.walk(gate[0])),
            f"闸门 {len(gate)} 处")
    mount = [n for n in ast.walk(gate[0]) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "readonly_tools"]
    c.check("A8-bis 圈内那一处就是挂载本身（走不到 = 驿站端一只都不挂，不靠记得加判据）",
            len(mount) == 1, f"{len(mount)} 处")
    setd = [n for n in ast.walk(gate[0]) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "setdefault"
            and n.func.value.id == "tools"]
    c.check("A9 用的是 `tools.setdefault(...)`，不是 `update`（模块自己的说明书归模块维护，M5 的靶子）",
            bool(setd) and "tools.update(readonly_tools())" not in code, f"setdefault {len(setd)} 处")

    c.check("A10 开关判据全项目一处：`def _agent_mode_on(` 1 处、`_agent_mode = _agent_mode_on(` 1 处",
            code.count("def _agent_mode_on(") == 1 and code.count("_agent_mode = _agent_mode_on(body)") == 1,
            f"定义 {code.count('def _agent_mode_on(')} / 取值 {code.count('_agent_mode = _agent_mode_on(body)')}")
    am = [n for n in ast.parse(main_src).body if isinstance(n, ast.FunctionDef)
          and n.name == "_agent_mode_on"]
    am_txt = ast.unparse(am[0]) if am else ""
    c.check("A10-bis 判据不许写成 `bool(body.get(...))`（字符串「false」也是真值，那个坑单子里点名了）"
            " —— 而且**不能拿源码字符串比**：那句警告就写在函数自己的 docstring 里，"
            "剔注释救不了 docstring（红线 4c 又应验一次）",
            bool(am) and "isinstance(v, bool)" in am_txt and "isinstance(v, str)" in am_txt
            and "return bool(body.get(" not in am_txt, am_txt[:120] or "没找到这个函数")
    c.check("A11 默认关：`_AGENT_MODE_DEFAULT` 由环境变量算出来，而且没有把空串当成真",
            "_AGENT_MODE_DEFAULT = (os.getenv(" in code and 'in ("1", "true", "on", "yes")' in code, "")

    gspan = (gate[0].lineno, gate[0].body[-1].end_lineno)
    # ⚠️ #62 起短路条件多了两维（`and not _any_pending(session_id) and not _is_material`），
    #   所以这里不能钉"整个条件 == `_agent_mode`"。要钉的从来就不是那句字面文本，而是
    #   "有这么一块：开关打头、块里只叫引擎"——位置（闸门圈内 / 红线之后 / 3b 之前）一条都不放松。
    br = [n for n in ast.walk(impl) if isinstance(n, ast.If)
          and ast.unparse(n.test).strip().startswith("_agent_mode")
          and "_run_agent_loop" in ast.unparse(n)]
    c.check("A12 短路块只有一处、而且就在闸门圈内",
            len(br) == 1 and gspan[0] < br[0].lineno <= gspan[1], f"{[n.lineno for n in br]}")
    if br:
        # 红线的安全半边用**写入口**来定位，不靠分支的注释名：
        # "确认执行 / 候选卡确认 / 删除 / 标记完成 / 清空执行" 每一支的子树里都有这几个调用之一，
        # 而"我自己挑"那一类只是**提到** `is_confirmation`、并不写库 —— 按名字找会把它算成红线。
        writers = ("mark_applied(", "_execute_clear(", "apply_pending_timetable_change(",
                   "set_todo_status(", "take_pending(")
        w_ifs = [n.lineno for n in ast.walk(gate[0])
                 if isinstance(n, ast.If) and n is not gate[0]
                 and any(w in ast.unparse(n) for w in writers)]
        c.check("A13 短路点排在**所有能替学生执行写入的分支之后**"
                "（Agent 模式不许把「学生点头 → 系统写库」那条路关掉）",
                bool(w_ifs) and br[0].lineno > max(w_ifs),
                f"短路 L{br[0].lineno} / 最靠后的写入库分支 L{max(w_ifs) if w_ifs else '无'}")
        b3 = [n for n in ast.walk(impl) if isinstance(n, ast.If)
              and "wants_clear_timetable" in ast.unparse(n.test)]
        c.check("A14 短路点排在 3b 清空分支**之前**（往后挪就漏掉那条最典型的抢话分支，M4 之外第二条命）",
                bool(b3) and br[0].lineno < min(n.lineno for n in b3),
                f"短路 L{br[0].lineno} / 3b L{min(n.lineno for n in b3) if b3 else '无'}")
    else:
        c.check("A13/A14 短路块找不到（先修这个，别往下比）", False, f"br={len(br)}")

    loop = [n for n in ast.walk(mtree) if isinstance(n, ast.AsyncFunctionDef)
            and n.name == "_run_agent_loop"]
    c.check("A15 引擎段只有一份：`_run_agent_loop` 定义 1 处、`AgentEngine(` 全文件 1 处",
            len(loop) == 1 and code.count("AgentEngine(") == 1,
            f"定义 {len(loop)} / AgentEngine {code.count('AgentEngine(')}")
    calls = [n for n in ast.walk(impl) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_run_agent_loop"]
    kwsets = [frozenset(k.arg for k in n.keywords) for n in calls]
    c.check("A16 每一处调用点的参数形状**完全一致**（差一个参数就是有人复制了一段去各改各的）。"
            "★ #76 把数从 2 改口成 3、#84 又多一处（改期让渡点）—— 这次干脆**不再数总数**："
            "『几处』由 test_61 C5-bis3 按名分管（那里每加一处都要报名字），"
            "这里只管形状，所以既不会因为合法新增而假红，也不会放过任何一处走样",
            len(calls) >= 3 and len(set(kwsets)) == 1, f"{[sorted(k) for k in kwsets]}")
    need = {"module_key", "session_id", "message", "prompt", "tools", "persona_key",
            "model_config", "emit"}
    c.check("A17 传进去的就那 8 个（单子量的那个数；多一个就是剪早了/剪晚了）",
            bool(kwsets) and kwsets[0] == frozenset(need), sorted(kwsets[0]) if kwsets else "无调用点")
    c.check("A18 写侧挂载判据一个字没动（本单不动它；`if not _station_surface and _schedule_tools_allowed(` 仍恰好 1 处）",
            code.count("if not _station_surface and _schedule_tools_allowed(") == 1, "")
    c.check("A19 提示词里那句「先查再说」加上了，而且只有一处（ASSISTANT_RULES 是共享漏斗，别去模块里再写一遍）",
            main_src.count("先查再说") == 1, "")
    return c


# --------------------------------------------------------------------------- B 行为：挂载
def batch_b_mount():
    c = Checker()
    title("B 批 · 行为：模型每栏实际拿到几只手、写侧一只都不许有、驿站端一只都不许多挂")
    restore, box = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([{"day": 1, "start": "08:00", "end": "09:40",
                              "course": "高等数学", "location": "教五-01"}])
            wipe_todos()
            write_express([])
            write_takeout([])
            cli = make_client()
            from app.modules.files import build_tools as files_tools
            from app.modules.planner import build_scheduling_tools
            import app.main as _M

            for mod in ("faq", "schedule", "express", "takeout", "planner"):
                got = None
                for _attempt in range(2):
                    box.clear()
                    cli.post("/api/chat", json={"message": SENT[mod], "session_id": f"b61-{mod}",
                                                "module": mod, "agent_mode": True})
                    got = _engine_round(box)
                    if got:
                        break
                # 改前那一份 = 模块自己的 + files；排程那组单独算（它按意图给，不是每次都挂）
                pre = set(_M.REGISTRY[mod][1]()) | set(files_tools())
                sched = set(build_scheduling_tools())
                added = sorted(n for n in (got or []) if n not in pre and n not in sched)
                c.check(f"B1 {mod} 栏引擎那一轮拿到 {COUNTS[mod]} 只（数字写死，改了挂载清单必须一起改这里）",
                        len(got or []) == COUNTS[mod], f"实际 {len(got or [])} 只：{got}")
                c.check(f"B2 {mod} 栏跨栏补进来的每一只都在 READONLY_SPEC 那 16 只里"
                        "（多出来一只就是本单越界，M1 的靶子）",
                        set(added) <= set(RO16), f"账本之外的收获：{added}")
                c.check(f"B2-bis {mod} 栏跨栏补进来的没有一只是写侧（写侧仍只走 _schedule_tools_allowed 那道判据）",
                        not (set(added) & set(WRITE12)), f"混进来的写侧：{sorted(set(added) & set(WRITE12))}")
            # 驿站端：一只都不许多挂（那 16 只里 10 只是学生个人数据）
            box.clear()
            cli.post("/api/chat", json={"message": "我的代办", "session_id": "b61-station",
                                        "module": "station", "agent_mode": True})
            st = _engine_round(box)
            c.check("B3 驿站端一只手都不许多挂：模型仍只拿到它自己那 2 只查询 + 3 只读文件",
                    sorted(st) == ["get_by_code", "list_pending_by_student", "list_uploaded_files",
                                   "read_uploaded_file", "search_docs"], st)
            c.check("B4 驿站端不许看见学生的个人数据工具（list_my_packages / list_day_todos 一只都不行）",
                    not any(n in st for n in ("list_my_packages", "list_day_todos", "get_weekly_timetable",
                                              "list_my_orders")), st)
            # faq 栏 + 闲聊：写侧仍然一只都不给（_schedule_tools_allowed 那道判据没被本单放宽）
            box.clear()
            cli.post("/api/chat", json={"message": "你是谁", "session_id": "b61-faq-chat",
                                        "module": "faq", "agent_mode": True})
            chat_seen = _engine_round(box)
            c.check("B5 faq 栏那句「你是谁」拿不到任何出卡工具（跨栏只借只读，旧事故那条判据照旧）",
                    not [n for n in chat_seen if n.startswith("propose_")],
                    [n for n in chat_seen if n.startswith("propose_")])
    finally:
        restore()

    # 函数级：`get_by_code` 挂出来的是**学生口径**那一份（M2 的行为半边）
    from app import store
    from app.agent.readonly import readonly_tools
    with sandbox():
        write_express([{"id": "X1", "owner": "1", "item": "样本甲", "station": "北区驿站",
                        "pickup_code": "A-1001", "arrived_at": "2026-12-20", "status": "待取"},
                       {"id": "X4", "owner": "2", "item": "别人的键盘", "station": "自提点",
                        "pickup_code": "D-3344", "arrived_at": "2026-12-21", "status": "待取"}])
        prev = store.current_student()
        store.set_current_student("1")
        try:
            f = readonly_tools()["get_by_code"].func
            mine = f(pickup_code="A-1001")
            other = f(pickup_code="D-3344")
            c.check("B6 池子里那只 `get_by_code` 查得到自己的件", "样本甲" in mine, mine[:40])
            c.check("B6-bis 查不到别人的件（来源若被改成 station 那份 partial(staff=True)，这句会念出「别人的键盘」）",
                    "没找到" in other and "别人的键盘" not in other, other[:40])
        finally:
            store.set_current_student(prev)

    # 「只读」这个词的行为证明：16 只挨个跑一遍，沙箱里一个字节都不许多
    from app.agent.readonly import READONLY_SPEC, readonly_tools
    with sandbox():
        write_timetable([{"day": 1, "start": "08:00", "end": "09:40",
                          "course": "高等数学", "location": "教五-01"}])
        seed_todos([{"title": "游泳", "date": "2026-10-28", "start": "19:00", "end": "20:30"}])
        write_express([{"id": "X1", "owner": "demo", "item": "样本甲", "station": "北区驿站",
                        "pickup_code": "A-1001", "arrived_at": "2026-12-20", "status": "待取"}])
        write_takeout([{"id": "Y1", "owner": "demo", "shop": "甲店", "items": "套餐",
                        "eta": "12:00", "status": "配送中", "pickup_point": "A 柜"}])
        before = _tree_fingerprint(data_path())
        ran, wrote, boomed = 0, [], []
        args = {"get_day_courses": {"day": "周一"}, "plan_task": {"task_name": "复习高数", "need_hours": 2},
                "find_free_slots": {"date": "2026-10-28"}, "list_day_todos": {"date": "2026-10-28"},
                "get_by_code": {"pickup_code": "A-1001"}, "get_order": {"order_id": "Y1"},
                "read_uploaded_file": {"file_id": "nope"}, "search_docs": {"query": "图书馆"},
                "search_kb": {"query": "图书馆"},
                # #63 新加的这一只也要真跑一遍：日期**按今天算**，不写死（写死的跨过午夜就成过去）
                "list_todos_range": {"start": _NEXT_MONDAY, "end": _NEXT_SUNDAY}}
        import asyncio
        for name, tool in sorted(readonly_tools().items()):
            kw = args.get(name, {})
            try:
                out = asyncio.run(tool.func(**kw)) if _is_async(tool.func) else tool.func(**kw)
                ran += 1
                if isinstance(out, str) and "出错了" in out:
                    boomed.append(f"{name}:{out[:40]}")
            except Exception as e:                       # 参数对不上不算写库，但要知道哪只没跑通
                boomed.append(f"{name}:{type(e).__name__}")
            if isinstance(out, dict) and out.get("__proposal__"):
                wrote.append(name)
        after = _tree_fingerprint(data_path())
        c.check("B7 16 只全跑了一遍（没跑通的断言等于没断言，所以这里先数跑了几只）",
                ran == len(READONLY_SPEC), f"{ran}/{len(READONLY_SPEC)} 只｜跑不通：{boomed[:4]}")
        c.check("B8 跑完一遍，沙箱数据树一个字节都没变（这就是「只读」两个字的定义）",
                before == after, _fingerprint_diff(before, after))
        c.check("B8-bis 跑出来的东西里不许有提案（`__proposal__` 一旦冒出来，下一步就是学生点确认、就写库了）",
                not wrote, wrote)
    return c


def _is_async(fn):
    import asyncio
    return asyncio.iscoroutinefunction(fn)


def _tree_fingerprint(root):
    """整棵数据树的 (相对路径, 大小, mtime_ns, 内容 md5) 清单。"""
    import hashlib
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            try:
                b = open(p, "rb").read()
                st = os.stat(p)
            except Exception:
                continue
            out[os.path.relpath(p, root)] = (len(b), st.st_mtime_ns,
                                             hashlib.md5(b).hexdigest())
    return out


def _fingerprint_diff(before, after):
    keys = set(before) | set(after)
    d = [f"{k}: {before.get(k)} → {after.get(k)}" for k in sorted(keys) if before.get(k) != after.get(k)]
    return " ｜ ".join(d[:4]) or "无差异"


# --------------------------------------------------------------------------- C 行为：开关
def batch_c_agent_mode():
    c = Checker()
    title("C 批 · Agent 模式：跳掉抢话的快路，但确认执行、硬信号卡、闲聊单轮一条都不动")
    restore, box = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([{"day": 1, "start": "08:00", "end": "09:40",
                              "course": "高等数学", "location": "教五-01"}])
            seed_todos([{"title": "游泳", "date": "2026-10-28", "start": "19:00", "end": "20:30"}])
            cli = make_client()

            def ask(msg, mod, agent, sid):
                box.clear()
                body = {"message": msg, "session_id": sid, "module": mod}
                if agent is not None:
                    body["agent_mode"] = agent
                d = cli.post("/api/chat", json=body).json()
                ph = [t.get("phase", "") for t in (d.get("trace") or []) if isinstance(t, dict)]
                return d, ph, _engine_round(box)

            # 1) 3b 清空那一条：关着走确定性分支，开着必须去找引擎
            d_off, ph_off, _ = ask("帮我把课表全删了", "planner", False, "c61-clear-off")
            d_on, ph_on, eng_on = ask("帮我把课表全删了", "planner", True, "c61-clear-on")
            c.check("C1 关着时「课表全删了」走确定性清空分支（轨迹里那句系统话术是它的身份证）",
                    any("生成清空提案" in p for p in ph_off), ph_off[:4])
            # ★ C2 在 #86 被**改过口径**（改一条护栏要留两份证据）：
            #   它原来防什么：防 Agent 模式那根开关是假的 —— 勾了却还是被 3b 快路抢走（M4 的靶子）。
            #   为什么必须让位：原来拿「轨迹里有没有 🤔」当"进过引擎"的身份证。#86 实测发现
            #                  模型只在**一边调工具一边开口说话**那一轮才有话可写，直答那轮的
            #                  `🤔` 早被前端 ① 过滤掉、后端也不再发空标记 ⇒ 这条判据量的其实是
            #                  "这一轮模型开没开口"，不是"进没进引擎" —— 它会自己失效。
            #   牙换成了什么：换成本来就存在的两根硬证 —— `eng_on`（引擎那一轮摆给模型的**带工具**
            #                  调用；判定层那一轮不带 tools，所以非空就等于真进过引擎）+
            #                  `💡 最终回答`（引擎自己的身份证，改前改后都给）+ 快路那句消失。
            #                  一根没少，只是不再拿一件不相干的事当证据。
            c.check("C2 开着时同一句话跳过那条分支、进了引擎（身份证＝引擎摆出过**带工具的**调用 +"
                    " 💡 那一行 + 清空提案那句消失。#86：`🤔` 不再能当「进过引擎」的证据 —— 理由见上）",
                    len(eng_on) > 0 and any("\U0001f4a1" in p for p in ph_on)
                    and not any("生成清空提案" in p for p in ph_on), ph_on[:4])
            c.check("C2-bis 开着时模型手里确实有一整箱工具（跳过快路 ≠ 空转）——"
                    "数字与 COUNTS['planner'] 同源，#86 之后是 26 只（多的是 `propose_todo_change`）",
                    len(eng_on) == COUNTS["planner"], f"{len(eng_on)} 只")

            # 2) 3c 加待办报了准点：同样被跳过
            d2_off, ph2_off, _ = ask("帮我加个游泳 周三19:00到20:30", "planner", False, "c61-add-off")
            d2_on, ph2_on, _ = ask("帮我加个游泳 周三19:00到20:30", "planner", True, "c61-add-on")
            c.check("C3 关着时那句直接出待办提案（确定性分支）",
                    any("生成待办提案" in p for p in ph2_off) and len(d2_off.get("options") or []) == 1,
                    ph2_off[:3])
            c.check("C4 开着时它不替模型抢这一步（改由模型调 propose_*；这条路径变长正是演示要看的）",
                    not any("生成待办提案" in p for p in ph2_on), ph2_on[:4])

            # 3) 安全红线：Agent 模式下学生回「确认」仍然**当场执行、不进引擎**
            seed_todos([])
            box.clear()
            r1 = cli.post("/api/chat", json={"message": "帮我加个游泳 周三19:00到20:30",
                                             "session_id": "c61-confirm", "module": "planner",
                                             "agent_mode": True}).json()
            _ = r1
            box.clear()
            r2 = cli.post("/api/chat", json={"message": "确认", "session_id": "c61-confirm",
                                             "module": "planner", "agent_mode": True}).json()
            ph_conf = [t.get("phase", "") for t in (r2.get("trace") or []) if isinstance(t, dict)]
            c.check("C5 学生回「确认」时 Agent 模式**不拦它**：仍然由系统当场执行，不交给模型",
                    not any("🤔" in p for p in ph_conf), ph_conf[:4])
            c.check("C5-bis 这一轮假模型没出提案，所以走的是「确认落空」那条诚实兜底（不是掉进引擎让模型编一句）",
                    any("确认落空" in p for p in ph_conf), ph_conf[:3])

            # 4) 闲聊必须还是一轮，不许为了"看起来在推理"多烧一次
            for msg in ("你好", "你是谁"):
                box.clear()
                d3 = cli.post("/api/chat", json={"message": msg, "session_id": "c61-chit-" + msg,
                                                 "module": "faq", "agent_mode": True}).json()
                seen = (box.get("llm").seen if box.get("llm") else []) or []
                engine_rounds = [s for s in seen if s]
                c.check(f"C6 闲聊那句「{msg}」在 Agent 模式下仍是单轮（0 次工具调用，一张卡都不许挂）",
                        len(engine_rounds) <= 1 and not (d3.get("options") or []),
                        f"引擎轮次 {len(engine_rounds)} 卡 {len(d3.get('options') or [])}")

            # 5) 不给参数 = 与关着逐字相同（默认关，别把老行为改了）
            for msg, mod in (("帮我把课表全删了", "planner"), ("我有哪些快递", "express")):
                a = cli.post("/api/chat", json={"message": msg, "session_id": "c61-def-" + mod,
                                                "module": mod}).json()
                b = cli.post("/api/chat", json={"message": msg, "session_id": "c61-off-" + mod,
                                                "module": mod, "agent_mode": False}).json()
                c.check(f"C7 请求体里不给 agent_mode 时，与显式关掉**逐字相同**（{mod}）",
                        a.get("answer") == b.get("answer") and a.get("module") == b.get("module")
                        and len(a.get("options") or []) == len(b.get("options") or []),
                        f"{(a.get('answer') or '')[:24]} / {(b.get('answer') or '')[:24]}")

            # 6) 字符串 "false" 不许被当成"开着"（C1 那个坑的行为半边）
            s_false = cli.post("/api/chat", json={"message": "帮我把课表全删了",
                                                  "session_id": "c61-strfalse", "module": "planner",
                                                  "agent_mode": "false"}).json()
            ph_sf = [t.get("phase", "") for t in (s_false.get("trace") or []) if isinstance(t, dict)]
            c.check("C8 传字符串 \"false\" = 关（写成 bool(v) 的话这句会被当成开，M3 的靶子）",
                    any("生成清空提案" in p for p in ph_sf), ph_sf[:3])
            s_on = cli.post("/api/chat", json={"message": "帮我把课表全删了",
                                               "session_id": "c61-stron", "module": "planner",
                                               "agent_mode": "on"}).json()
            ph_so = [t.get("phase", "") for t in (s_on.get("trace") or []) if isinstance(t, dict)]
            c.check("C8-bis 传字符串 \"on\" = 开（两种写法都得收，别让前端猜）",
                    not any("生成清空提案" in p for p in ph_so), ph_so[:3])

            # 7) 驿站端就算点名要 Agent 模式也不许挂只读工具
            box.clear()
            cli.post("/api/chat", json={"message": "我的代办", "session_id": "c61-station-on",
                                        "module": "station", "agent_mode": True})
            c.check("C9 驿站端在 Agent 模式下仍然一只手都不许多挂（短路块在闸门圈内，它根本走不到）",
                    sorted(_engine_round(box)) == ["get_by_code", "list_pending_by_student",
                                                   "list_uploaded_files", "read_uploaded_file",
                                                   "search_docs"], _engine_round(box))
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- D #61-B 验收
def batch_d_reasons_exits():
    c = Checker()
    title("D 批 · #61-B 验收：判定层的 🧭 两条出口都带、read 的三个覆盖点不吞它、行行带 step:0")
    src = io.open(MAIN, encoding="utf-8").read()
    c.check("D1 记录点一处、出口一处（这是 60 号的形状，本单只验收不改）",
            src.count('meta["reasons"] = _reason_steps(') == 1
            and "head = ([read] if isinstance(read, dict) else []) + reasons" in src, "")
    reads = [src.count("\n", 0, m.start()) + 1
             for m in re.finditer(r'meta\["read"\] =', src)]
    c.check("D2 `meta[\"read\"] =` 恰好 4 处（1 初记 + 3 覆盖），而且**没有一处**跟着写 reasons"
            "（设计就是两个键各写各的，覆盖 read 不该动 reasons）",
            len(reads) == 4 and src.count('meta["reasons"] =') == 1, f"read 赋值行 {reads}")

    restore, box = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([])
            wipe_todos()
            cli = make_client()
            # 三个覆盖点各真走一遍：2.5 课表导入（要先有那份点名的文件）、2.6 上报卡、材料
            up = cli.post("/api/upload", files={"file": ("课表61.txt",
                        "周一 高等数学 08:00-09:40 教五-01\n周二 线性代数 10:00-11:40\n".encode("utf-8"),
                        "text/plain")}, data={"purpose": "file"})
            cases = [("初记点", "faq", "图书馆几点关门"),
                     ("2.6 上报卡", "express", "我的快递丢了怎么办"),
                     ("材料覆盖点", "planner", "1. 周一 高等数学 08:00-09:40\n"
                      "2. 周一 线性代数 10:00-11:40\n3. 周二 大学英语 08:00-09:40"),
                     ("2.5 课表导入", "schedule", "按《课表61.txt》导入课表")]
            c.check("D2-bis 上传那份课表得先成功（不然 2.5 那条覆盖点根本没被走到，D3 就是假绿）",
                    up.status_code == 200, str(up.status_code))
            for name, mod, msg in cases:
                for kind in ("json", "sse"):
                    sid = f"d61-{name}-{kind}-{abs(hash(msg)) % 10**6}"
                    if kind == "json":
                        d = cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                                        "module": mod}).json()
                    else:
                        d = _sse_done(cli, msg, mod, sid)
                    tr = [t for t in (d.get("trace") or []) if isinstance(t, dict)]
                    compass = [t for t in tr if "🧭" in (t.get("phase") or "")]
                    zeros = [t for t in tr if t.get("step") == 0]
                    c.check(f"D3 [{name}/{kind}] 两条出口都带 🧭 判定行（60 号只在 JSON 那条验过）",
                            bool(compass), [t.get("phase", "")[:12] for t in tr[:3]])
                    # 位置即判据：出口是把 head 拼在**最前面**的，所以 step==0 那几行必须是连续前缀。
                    # 按档名（🧠/📋）去找会误伤 —— 分支自己也会画 📋 开头、step=1 的行（实测就是这样）。
                    c.check(f"D4 [{name}/{kind}] 前置那几行都带 step:0、而且它们是 trace 的连续前缀"
                            "（缺字段会画成「多了一步没编号的」，那是 #60-C 的反面）",
                            bool(zeros) and tr[:len(zeros)] == zeros and not [t for t in tr if "step" not in t],
                            [(t.get("step"), t.get("phase", "")[:8]) for t in tr[:4]])
            # 覆盖点走没走到，用回话里那句话的身份证确认（不然 D3 只是在测初记点）
            d25 = cli.post("/api/chat", json={"message": "按《课表61.txt》导入课表",
                                              "session_id": "d61-25", "module": "schedule"}).json()
            c.check("D5 2.5 那条覆盖点确实走到了（🧠 那句是「办事（导入课表）」，不是初记的「问事」）",
                    any("导入课表" in (t.get("phase") or "") and (t.get("phase") or "").startswith("🧠")
                        for t in (d25.get("trace") or []) if isinstance(t, dict)),
                    [t.get("phase", "")[:22] for t in (d25.get("trace") or [])[:2]])
            d26 = cli.post("/api/chat", json={"message": "我的快递丢了怎么办",
                                              "session_id": "d61-26", "module": "express"}).json()
            c.check("D5-bis 2.6 那条也走到了，而且 🧭 与它同时在场（覆盖 read 不许顺手把 reasons 弄没）",
                    any("上报工单" in (t.get("phase") or "") for t in (d26.get("trace") or [])
                        if isinstance(t, dict))
                    and any("🧭" in (t.get("phase") or "") for t in (d26.get("trace") or [])
                            if isinstance(t, dict)),
                    [t.get("phase", "")[:22] for t in (d26.get("trace") or [])[:3]])
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- E 前端
def _node(expr_js, argv=None):
    if not shutil.which("node"):
        raise AssertionError("本机没有 node —— E 批靠 node 真跑页面那份函数，不许退回读源码")
    p = subprocess.run(["node", "-e", expr_js, ESC_JS, json.dumps(argv or [])],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    if p.returncode != 0:
        raise AssertionError("node 跑挂：" + (p.stderr or "")[:400])
    return p.stdout


def batch_e_frontend():
    c = Checker()
    title("E 批 · 前端：node 真跑 agentOn（默认关），注入点一处、传的是布尔、状态标看得见")
    import test_79_trace_rendered as t79      # 复用同一个抠函数的手法，别再抄一份 runner

    src = io.open(STUDENT, encoding="utf-8").read()
    fn = t79._slice_fn(src, "function agentOn(")
    js = ("const store={};globalThis.localStorage={getItem:k=>(k in store?store[k]:null),"
          "setItem:(k,v)=>{store[k]=String(v)},removeItem:k=>{delete store[k]}};"
          + fn +
          "const cs=JSON.parse(process.argv[2]);"
          "process.stdout.write(JSON.stringify(cs.map(p=>{localStorage.setItem('agent_mode',p[0]);"
          "return agentOn();})));")
    out = json.loads(_node(js, [["0"], ["1"], ["true"], ["x"], [None]]))
    c.check("E1 agentOn 认得住：\"1\" 才开，\"0\"/\"true\"/别的/没存过都算关（默认关这条由它兜住）",
            out == [False, True, False, False, False], str(out))

    c.check("E2 开关存的是 localStorage.agent_mode，而且只读一处（设置页与请求体各读一份就会不一致）",
            src.count('localStorage.getItem("agent_mode")') == 1
            and src.count('localStorage.setItem("agent_mode"') == 1,
            f"读 {src.count('localStorage.getItem(\"agent_mode\")')} / 写 {src.count('localStorage.setItem(\"agent_mode\"')}")
    c.check("E3 请求体里那一只注入点在 streamChat 内部（两处 fetch 各写一遍必漏一处）"
            "，而且传的是**布尔**不是字符串",
            src.count("agent_mode: agentOn()") == 1
            and 'agent_mode: "' not in src, str(src.count("agent_mode: agentOn()")))
    c.check("E4 设置页有那颗勾选，文案写清了「演示用 / 慢一点 / 默认关」",
            'id="setAgent"' in src and "Agent 模式（演示用）" in src and "默认关" in src, "")
    c.check("E5 输入框旁边那颗状态标存在、默认不显形、勾上才 .on（演示时靠它当场证明在不在这个模式）",
            'id="agentBadge"' in src and "Agent 模式" in src
            and ".agent-badge { display: none" in src and ".agent-badge.on { display: inline-flex" in src, "")
    # 单子的原话是「小标 🧭 Agent 模式」，但 `test_44` 的 RETIRED 名单里就有 U+1F9ED（🧭）——
    # 界面 chrome 的图标一律走页面自己的 symbol 精灵（全量第一次就是这么抓到我这条的）。
    # 所以图形用 `#gi-compass`，文字仍是「Agent 模式」：语义与单据一致，形状跟着仓库既有规矩走。
    c.check("E5-bis 那颗标用的是页面 sprite 里的罗盘（<use href=\"#gi-compass\"/>），**不是** 🧭 那个 emoji"
            "（`test_44` RETIRED 名单钉着：chrome 区不许再出现下线的图标 emoji）",
            '<use href="#gi-compass"/>' in src.split('id="agentBadge"')[1][:200]
            and "\U0001F9ED Agent 模式" not in src, "")
    c.check("E5-ter 状态标在启动时与勾选时各刷一次（只在启动刷就会「勾了但看不见」）",
            src.count("syncAgentBadge()") >= 2, str(src.count("syncAgentBadge()")))
    return c


# --------------------------------------------------------------------------- 入口
def main():
    code = 0
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（挂载行为）", batch_b_mount),
                      ("C 批（Agent 模式开关）", batch_c_agent_mode),
                      ("D 批（#61-B 三条验收）", batch_d_reasons_exits),
                      ("E 批（前端）", batch_e_frontend)):
        try:
            code |= fn().summary(label)
        except Exception as e:                       # 一批崩不许拖着其余批次一起看不见数
            import traceback
            traceback.print_exc()
            print(f"!! {label} 崩了：{type(e).__name__}: {e}")
            code |= 1
    return code


if __name__ == "__main__":
    sys.exit(main())
