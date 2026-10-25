"""#63 的护栏：只读工具的**粒度**要跟学生的提问粒度对齐（查一段用一把工具，别逐日点）。

四批：

  A 形状：新工具的定义/注册/账本登记/说明书边界/提示词那句各只有一处；
          筛法**必须**走 `store.todo_occurs_on`（字面 date 筛会吞掉重复待办 —— 实测过，钉成红线）；
          跨度那把尺子借 `WEEKLY_HORIZON_MONTHS`，不许在两处各写一个数字；
          函数体里一个写库调用都没有（"只读"两个字的 AST 证明）。
  B 行为：同一周里一次性 / 每周重复 / 每天习惯三种待办，一次调用全看见；
          顺序、并一行、date 用落到的那天、说反日期、坏日期、超跨度、真的空、
          与"逐日调 7 次"**事实相同**；外加一条**红证**：单据那种 `date` 字面筛法当场吞数据。
  C 挂载：这一只确实跨栏拿得到（faq 栏问同一句话也不用逐日调），驿站端仍然一只都不许多。
  D 边界：新工具没挤进 `3d` 那条确定性分支（它本来就用 `list_todos()`，本单一个字不该动它）。

⚠️ 每栏"模型实际拿到几只手"的数字**只在 `test_82` 钉一份**（那里是 COUNTS）。
   本文件只钉"这一只名字在不在清单里" —— 同一件事两处各钉一遍就是两个判据（本项目赔过好几次的东西）。
"""
import ast
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (PROJ_DIR, Checker, make_client, sandbox, seed_todos,  # noqa: E402
                      title, write_accounts, write_timetable, wipe_todos)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
PLANNER = os.path.join(PROJ_DIR, "app", "modules", "planner.py")
RO = os.path.join(PROJ_DIR, "app", "agent", "readonly.py")

NEW = "list_todos_range"
OLD = "list_day_todos"
# 写库/提案侧的名字：新工具里出现任何一个就是越界（"只读"的定义）
WRITEISH = ("add_todo", "add_todos_batch", "update_todo", "save_timetable", "_swrite",
            "propose_todo_tool", "propose_todo", "take_pending", "save_pending", "mark_applied")


def _tree(path):
    return ast.parse(io.open(path, encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _tool_entry(body, name):
    """从 `build_tools()` 那个 dict 字面量里取出 `{name: Tool(...)}` 那一项。"""
    for n in ast.walk(body):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == name and isinstance(v, ast.Call):
                    return v
    return None


def _kw_of(call, key):
    for k in call.keywords:
        if k.arg == key:
            return k.value
    return None


def _desc_of(call):
    v = _kw_of(call, "description")
    return ast.unparse(v) if v is not None else ""


def _dates(off_start=0, days=6):
    """下一周一起算（**不写死日期**：写死的日期跨过午夜就成过去，那是 #69/#44-B 那一族）。"""
    today = datetime.date.today()
    monday = today + datetime.timedelta(days=(7 - today.weekday()) % 7 or 7)
    d0 = monday + datetime.timedelta(days=off_start)
    return d0.isoformat(), (d0 + datetime.timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- A 形状
def batch_a_shape():
    c = Checker()
    title("A 批 · 形状：一只新工具、一处注册、账本登记、说明书与提示词各补一句")
    pt = _tree(PLANNER)
    src = io.open(PLANNER, encoding="utf-8").read()
    rt = _tree(RO)
    fn = _func(pt, NEW)
    c.check("A1 `%s` 全项目只定义一处、就在模块级；`%s` 还在（不删单日那只，find_free_slots 等还在按天用它）"
            % (NEW, OLD),
            src.count("def %s(" % NEW) == 1 and fn is not None and _func(pt, OLD) is not None,
            "定义 %d 处" % src.count("def %s(" % NEW))

    body = _func(pt, "build_tools")
    entry = _tool_entry(body, NEW) if body else None
    c.check("A2 工具箱里挂着它，且 `func` 就是那个函数（注册与实现接错线是 #61 那一族的老账）",
            entry is not None and ast.unparse(_kw_of(entry, "func") or ast.Constant(value="")) == NEW,
            entry and ast.unparse(_kw_of(entry, "func") or ""))
    params = _kw_of(entry, "parameters") if entry else None
    props = []
    required = []
    if isinstance(params, ast.Dict):
        for k, v in zip(params.keys, params.values):
            if ast.unparse(k).strip("'\"") == "properties" and isinstance(v, ast.Dict):
                props = sorted(ast.unparse(x) for x in v.keys)
            if ast.unparse(k).strip("'\"") == "required" and isinstance(v, ast.List):
                required = sorted(ast.unparse(x).strip("'\"") for x in v.elts)
    c.check("A3 参数**只有** start / end 两个（单据那条'参数每多一个、模型选错概率高一档'是这一单的决定，"
            "不是装饰 —— 顺手加个 status 筛选就该红）",
            props == ["'end'", "'start'"] and required == ["end", "start"], f"{props} / {required}")
    c.check("A4 它自己的说明书把「别逐日」写进去了（模型只在 description 里看得见这句）",
            "list_day_todos" in _desc_of(entry) and "这周" in _desc_of(entry), _desc_of(entry)[:60])
    old_entry = _tool_entry(body, OLD) if body else None
    c.check("A5 `%s` 的说明书补了边界、指向新工具（边界只写在另一只上，旧的那只永远不知道自己该让路）" % OLD,
            old_entry is not None and NEW in _desc_of(old_entry), _desc_of(old_entry)[:60])

    spec_map = {}
    for n in ast.walk(rt):
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            if any(isinstance(t, ast.Name) and t.id == "READONLY_SPEC" for t in targets) \
                    and isinstance(n.value, ast.Dict):
                spec_map = {ast.unparse(k).strip("'\""): ast.unparse(v).strip("'\"")
                            for k, v in zip(n.value.keys, n.value.values)}
    c.check("A6 登记进跨栏账本，且来源写死是 planner（不登记就只在日程栏有，换个栏问同一句又要逐日翻 —— "
            "这一条是单据点名的'不能漏'）",
            spec_map.get(NEW) == "planner", f"账本 {len(spec_map)} 条，本只 → {spec_map.get(NEW)}")

    rules = io.open(MAIN, encoding="utf-8").read()
    i5 = rules.find("5. 先查再说")
    i_end = rules.find('"""', i5)
    seg = rules[i5:i_end]
    c.check("A7 提示词那句「查一段时间用一次工具」补在第 5 条**里面**（不新起一条，条数仍是 5：6. 一冒出来"
            "就和'输出要求/上报工单/身份隔离/自我定位'那几条并列，权重是假的）",
            i5 > 0 and NEW in seg and "\n6." not in seg and "list_day_todos" in seg, seg[-60:].replace("\n", " ⏎ "))

    calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)} if fn else set()
    c.check("A8 函数体里**没有任何写库/提案调用**（'只读'两个字要能被 AST 查出来，不能只看函数名）",
            not any(w in calls for w in WRITEISH), sorted(calls))
    c.check("A9 筛法走 `todo_occurs_on`，**不许**出现按字面 `date` 比较的那行（实测它会吞掉每周重复与习惯；"
            "这一条就是钉住别再有人照施工单原样改回去）",
            "todo_occurs_on" in calls and 'start <= (t.get("date")' not in ast.unparse(fn or ast.Expr()),
            [x for x in sorted(calls) if "occurs" in x or "list_todos" in x])
    c.check("A10 跨度那把尺子借 `WEEKLY_HORIZON_MONTHS`，函数体里不写死 92/93 这种天数"
            "（'每周重复最多铺 3 个月'与'一次最多查多久'必须是同一个数）",
            "WEEKLY_HORIZON_MONTHS" in ast.unparse(fn or ast.Expr())
            and not any(str(x) in ast.unparse(fn or ast.Expr()) for x in (92, 93)),
            "")
    c.check("A11 它不进写侧那份名单（`WRITE_TOOL_NAMES` 里没有这个名字，读侧账本也没串进去）",
            NEW not in io.open(RO, encoding="utf-8").read().split("WRITE_TOOL_NAMES")[1].split("})")[0], "")
    return c


# --------------------------------------------------------------------------- B 行为
def batch_b_behaviour():
    c = Checker()
    title("B 批 · 行为：一次调用看全这一段（一次性 / 每周 / 每天 三种都得看见）")
    from app.modules.planner import list_day_todos, list_todos_range
    from app.store import list_todos

    d0, d1 = _dates()
    oneoff = (datetime.date.fromisoformat(d0) + datetime.timedelta(days=1)).isoformat()
    outside = (datetime.date.fromisoformat(d1) + datetime.timedelta(days=3)).isoformat()
    anchor_weekly = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=(
        datetime.date.fromisoformat(d0).weekday() - 2) % 7 or 7)).isoformat()   # 上一个周三
    anchor_daily = (datetime.date.fromisoformat(d0) - datetime.timedelta(days=3)).isoformat()

    with sandbox():
        write_accounts(["admin1"])
        write_timetable([])
        seed_todos([
            {"title": "一次性·范围内", "date": oneoff, "start": "10:00", "end": "11:00"},
            {"title": "一次性·范围外", "date": outside, "start": "09:00", "end": "10:00"},
            {"title": "每周三游泳", "date": anchor_weekly, "start": "19:00", "end": "20:00",
             "repeat": "weekly", "weekday": 3},
            {"title": "每天晨跑", "date": anchor_daily, "start": "07:00", "end": "07:30",
             "repeat": "daily"},
        ])
        rows = list_todos_range(d0, d1)
        got = [r for r in rows if "info" not in r]
        titles = {r["title"] for r in got}
        c.check("B0 正控：库里确实四条、这段里该看见三条（扫回空表时下面全是空转的绿）",
                len(list_todos()) == 4 and titles == {"一次性·范围内", "每天晨跑", "每周三游泳"},
                sorted(titles))

        # 红证：把**单据那一版**的筛法当场算一遍，证明它不是"少几行"而是"答错"
        naive = sorted(t["title"] for t in list_todos() if d0 <= (t.get("date") or "") <= d1)
        c.check("B0-bis 红证：按字面 `date` 筛（施工单那一版）这段里只剩「一次性·范围内」一条 —— "
                "「每周三游泳」「每天晨跑」的锚点都在范围外，模型就会回一句『之间没有安排任何待办』（答错，不是省调用）",
                naive == ["一次性·范围内"], f"字面筛 {naive} vs 本实现 {titles}")

        weekly = next(r for r in got if r["title"] == "每周三游泳")
        habit = next(r for r in got if r["title"] == "每天晨跑")
        c.check("B1 重复待办并成**一行**、`dates` 只列这段里落到的那天",
                len(weekly["dates"]) == 1 and weekly.get("repeat") == "weekly", weekly["dates"])
        c.check("B2 习惯（daily）照实列出、七天并成一行（月视图滤它是格子装不下，不是'习惯不算待办'）",
                len(habit["dates"]) == 7 and habit.get("repeat") == "daily", habit["dates"])
        c.check("B3 每行的 `date` 是**落到的那天**，不是记录里那个锚点（锚点在上周，学生问这周）",
                all(r["date"] == r["dates"][0] for r in got) and weekly["date"] >= d0,
                [(r["title"], r["date"]) for r in got])
        c.check("B4 顺序按 date、start 升序（列表要能直接念给学生）",
                got == sorted(got, key=lambda r: (r["date"], r["start"])), [r["date"] for r in got])
        c.check("B5 范围外那条不许混进来", "一次性·范围外" not in titles, titles)
        c.check("B6 字段形状与 `list_day_todos` 一致（id/title/date/start/end/status）+ 多出来的只有 dates/repeat",
                set(next(iter(got))) >= {"id", "title", "date", "start", "end", "status", "dates"}
                and set(next(r for r in got if r["title"] == "一次性·范围内"))
                == {"id", "title", "date", "start", "end", "status", "dates"},
                sorted(got[0]))

        rev = list_todos_range(d1, d0)
        c.check("B7 起止说反了自动换过来，与正着传**逐字相同**（学生/模型说反不是错误）",
                rev == rows, f"{len(rev)} 行")
        bad = list_todos_range("这周", "")
        c.check("B8 日期不合法时**不许猜也不许抛**：回一行 info 把该给的格式说回去（模型下一步就会补对）",
                len(bad) == 1 and "YYYY-MM-DD" in bad[0]["info"] and "TypeError" not in str(bad), bad)
        wide = list_todos_range("2020-01-01", "2030-01-01")
        c.check("B9 跨度夹住且**明说**（借 3 个月那把尺子；不夹就等于让模型一句话把整库摊进结果里）",
                any("一次最多查" in r.get("info", "") for r in wide[:2]), wide[:1])
        empty = list_todos_range("2029-01-01", "2029-01-05")
        c.check("B10 真的空就如实回一句 info（空列表会让模型自己编一句'你这周很空'）",
                len(empty) == 1 and "没有安排任何待办" in empty[0]["info"], empty)

        days = [(datetime.date.fromisoformat(d0) + datetime.timedelta(days=i)).isoformat()
                for i in range(7)]
        per_day = sorted((t["title"], dd) for dd in days
                         for t in list_day_todos(dd) if "info" not in t)
        merged = sorted((r["title"], dt) for r in got for dt in r["dates"])
        c.check("B11 与「逐日调 7 次」**事实完全相同**（只是并成一行）—— 粒度对齐不许以少报为代价",
                per_day == merged, f"逐日 {len(per_day)} 条 / 一次 {len(merged)} 条")
    return c


# --------------------------------------------------------------------------- C 挂载
class _NoLLM:
    async def chat(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        return {"content": ""}

    async def stream(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        yield {"content": ""}


def _patch():
    """三处打桩（模型客户端 + 两处 get_llm_config），并让**引擎自己被叫到时记下拿到的工具名**。

    ⚠️ `app.main` 是 by-value 导入 `get_llm_config` 的，只打 `app.config` 那一处时
       引擎里"没配密钥就走离线 Mock"那道闸看的还是真配置（test_82 文件头记过这个坑）。
    """
    import app.config as _cfg
    import app.llm.client as _client
    import app.main as _M

    box = {"seen": []}

    def cfg(*a, **k):
        return {"api_key": "test-key", "base_url": "http://x", "model": "m"}

    async def sentinel(*, module_key=None, session_id=None, message=None, prompt=None,
                       tools=None, persona_key=None, model_config=None, emit=None):
        box["seen"].append({"module": module_key,
                            "tools": sorted(tools) if isinstance(tools, dict) else []})
        return {"module": module_key, "session_id": session_id, "answer": "【哨兵】",
                "trace": [{"step": 1, "phase": "🤔 哨兵", "answer": "x"}],
                "options": [], "awaiting_choice": False}

    old = (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config, _M._run_agent_loop)
    _client.DeepSeekClient = lambda *a, **k: _NoLLM()
    _cfg.get_llm_config = cfg
    _M.get_llm_config = cfg
    _M._run_agent_loop = sentinel

    def restore():
        (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config,
         _M._run_agent_loop) = old
    return restore, box


def batch_c_mount():
    c = Checker()
    title("C 批 · 挂载：换一栏问同一句话也别逐日调；驿站端一只都不许多")
    restore, box = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([])
            seed_todos([{"title": "游泳", "date": _dates()[0], "start": "19:00", "end": "20:00"}])
            cli = make_client()
            for mod in ("faq", "schedule", "express", "takeout", "planner"):
                box["seen"].clear()
                cli.post("/api/chat", json={"message": "我这周有什么待办",
                                            "session_id": "t63-mount-" + mod,
                                            "module": mod, "agent_mode": True})
                tools = box["seen"][0]["tools"] if box["seen"] else []
                c.check(f"C1 {mod} 栏跨栏之后拿得到 `{NEW}`（账本登记生效的唯一证明是另一栏问同一句也用这一把）",
                        NEW in tools and OLD in tools, f"{len(tools)} 只手")
            box["seen"].clear()
            cli.post("/api/chat", json={"message": "我的代办", "session_id": "t63-station",
                                        "module": "station", "agent_mode": True})
            st = box["seen"][0]["tools"] if box["seen"] else []
            box["seen"].clear()
            cli.post("/api/chat", json={"message": "我的代办", "session_id": "t63-station",
                                        "module": "station", "agent_mode": True})
            st = box["seen"][0]["tools"] if box["seen"] else []
            c.check("C2 驿站端不许出现它（那是学生的个人待办；闸门圈内根本没挂跨栏工具，本单也没开口子）",
                    NEW not in st and OLD not in st, st[:6])
            cli.post("/api/chat", json={"message": "我这周有什么待办",
                                        "session_id": "t63-writecheck", "module": "planner",
                                        "agent_mode": True})
            pl = box["seen"][0]["tools"] if box["seen"] else []
            from app.agent.readonly import WRITE_TOOL_NAMES
            c.check("C3 planner 栏这一趟里**一只写侧都没有**（只读池扩了一只，写侧那道判据仍是一只不加 —— "
                    "本单最容易顺手做错的地方）",
                    not (set(pl) & set(WRITE_TOOL_NAMES)), sorted(set(pl) & set(WRITE_TOOL_NAMES)))
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- D 边界
def batch_d_boundary():
    c = Checker()
    title("D 批 · 边界：`3d` 那条确定性分支一个字没动，`find_free_slots` 也照旧")
    tree = _tree(MAIN)
    impl = _func(tree, "_chat_impl")
    br = [n for n in ast.walk(impl) if isinstance(n, ast.If)
          and "wants_list_todo(message)" in ast.unparse(n.test)] if impl else []
    inside = []
    for n in br:
        inside += [ast.unparse(x.func) for x in ast.walk(n) if isinstance(x, ast.Call)]
    c.check("D1 `3d`（列待办）那支里**没有**新工具 —— 它本来就用 `list_todos()`，"
            "非 Agent 模式那条更快更稳的路径不该被改动碰（单据§不做的事 3）",
            bool(br) and NEW not in inside and "list_todos" in inside, f"{len(br)} 处分支")
    pt = _tree(PLANNER)
    ffs = _func(pt, "find_free_slots")
    c.check("D2 `find_free_slots` 内部照旧按单日用它（本单没动它，也没顺手把新工具塞进去）",
            ffs is not None and "list_todos" in ast.unparse(ffs) and NEW not in ast.unparse(ffs), "")
    c.check("D3 新工具没被误挂成写侧名字（`WRITE_TOOL_NAMES` 是那份反向断言的地基）",
            NEW not in _func_write_names(), "")
    return c


def _func_write_names():
    src = io.open(RO, encoding="utf-8").read()
    seg = src.split("WRITE_TOOL_NAMES")[1].split("})")[0] if "WRITE_TOOL_NAMES" in src else ""
    return {w.strip().strip("'\"") for w in seg.split(",") if w.strip()}


def main():
    code = 0
    print("\n" + "=" * 60)
    print("test_84_todo_range_tool_granularity：#63 只读工具的粒度要跟提问粒度对齐")
    print("=" * 60)
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（行为）", batch_b_behaviour),
                      ("C 批（挂载）", batch_c_mount),
                      ("D 批（边界）", batch_d_boundary)):
        try:
            rc = fn().summary(label)
        except Exception as e:                       # 一批崩不许拖着其余批次一起看不见数
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
