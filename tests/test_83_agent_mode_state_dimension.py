"""#62 的护栏：Agent 模式的短路判据补上**状态**这一维（手上有卡 / 有材料 ⇒ 不许交给模型）。

五批，各钉一件事：

  A 形状：`_any_pending` 只有一份、名单**从源码里数出来**再比（新增取器漏进名单就红）；
          短路条件是三个操作数的 `and`（开关 + 有没有卡 + 有没有材料）且位置没动；
          `_holding_card` 改成取用同一个函数（校历闸与 planner 直答那道闸仍读它）。
  B 行为：挂着四种卡、以及粘一段材料时，Agent 模式**一次都不许叫引擎**（那句话是在回应上一轮）；
          手上干净时才叫，而且叫的位置是短路点，不是转完所有分支之后的尾部。
          五句在关着时逐字不变，且写库红线一条不动（挂卡 + 「确认」仍不写库）。
  C 语义：`_any_pending` 对八种待办卡回真、对两份"候选清单"与三种非待办暂存回假（口径写死，别漂）。
  D 配对：只挂二选一卡时，#42-A 那道校历闸**不许接管**——判据是"它和挂别的卡时行为一致"，
          并配一条"没挂卡时必须照样接管"的正控（免得为了绿把闸整个关掉）。
  E 码页：#62-B 那处 subprocess 的两端都钉死了，且换 shell 不改变结果。

⚠️ 两件事容易踩（都在 test_82 文件头记过）：
   ① 模型配置要**两处**一起打桩（`app.config` 与 `app.main`）—— main 是 by-value 导入的；
   ② 本文件用**哨兵**替掉整段引擎（判"走没走引擎"不该靠轨迹里有没有 🤔 猜），
      所以 `app.main._run_agent_loop` 也在打桩名单里，出去时必须一起恢复。
"""
import ast
import datetime
import io
import locale
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (PROJ_DIR, Checker, make_client, pin_now, sandbox, seed_kb,  # noqa: E402
                      seed_todos, title, write_accounts, write_timetable, wipe_todos)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
T67 = os.path.join(PROJ_DIR, "tests", "test_67_kb_docs_merged.py")

# 短路点上"挂着就算回应上一轮"的那八种卡（顺序与 main 里那份名单一致，改名要一起改）
CARD8 = ["_pending_todo", "_pending_todo_remove", "_pending_todo_done",
         "_pending_todo_remove_batch", "_pending_todo_done_batch",
         "_pending_todo_slots", "_pending_todo_add_batch", "_pending_todo_mode"]
# 每种卡对应暂存里的 `kind`（C2 逐个真挂一次，验证名单不是纸面上的）
KIND8 = {"_pending_todo": "todo_add", "_pending_todo_remove": "todo_remove",
         "_pending_todo_done": "todo_done", "_pending_todo_remove_batch": "todo_remove_batch",
         "_pending_todo_done_batch": "todo_done_batch", "_pending_todo_slots": "todo_slots",
         "_pending_todo_add_batch": "todo_add_batch", "_pending_todo_mode": "todo_mode"}


# --------------------------------------------------------------------------- 夹具
def _next_wed_iso() -> str:
    """下一个周三的 ISO 日期（**不写死**：写死的日期跨过午夜就成过去，
    "不许落在过去"会把卡整个不出——那是 #69/#44-B 那一族，别再犯一次）。"""
    d = datetime.date.today() + datetime.timedelta(days=1)
    while d.weekday() != 2:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def _cards(day: str):
    """四种"上一轮留下的东西"，全部用**生产代码**造。

    不手搓卡片字段：手搓的形状只能证明"名单里有这个名字"，证明不了真卡走得进那条分支。
    （批量/删除那几种在 C 批里才手搓最小形状——那里要判的只是"这种 kind 算不算一张卡"。）
    """
    from app.modules.planner import (mode_to_card, parse_add_todo, todo_mode_proposal,
                                     todo_slots_proposal)
    mode = todo_mode_proposal(f"{day} 晚上去游泳")
    return {
        "mode": mode,                                        # 二选一卡：只有问题、没有时间
        "auto": mode_to_card(mode, "ai") if mode else None,  # 系统替他挑的那一条（带 auto）
        "slots": todo_slots_proposal(f"{day} 晚上去游泳"),    # 候选时段卡
        "stated": parse_add_todo(f"帮我加个游泳 {day} 19:00到20:30"),  # 学生自己报了点的
    }


# 一段"粘贴材料"：两行、各自带日期（`looks_like_material` 的 ② 走的是"≥2 行带日期"那半）
def _material(day: str) -> str:
    d = datetime.date.fromisoformat(day)
    f = lambda off, hm, name: (                     # noqa: E731
        (d + datetime.timedelta(days=off)).strftime("%m月%d日") + f" {hm} {name}")
    return (f(1, "09:00-10:00", "新生体检") + "\n" + f(2, "14:00-15:00", "体测补测") + "\n")


# --------------------------------------------------------------------------- 打桩
class _NoLLM:
    """判定层用的假货：什么都不回（让规则层去判，语义层自然落空）。"""

    async def chat(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        return {"content": ""}

    async def stream(self, messages, tools=None, tool_choice="auto", override=None, max_tokens=None):
        yield {"content": ""}


def _patch():
    """把模型换成假货、把**整段引擎换成哨兵**，返回 (恢复函数, 命中记录)。

    为什么打 `_run_agent_loop` 而不是只打模型客户端：#62 要判的是"走没走引擎"。
    打模型只能靠轨迹里有没有 🤔 去猜，而 phase 是引擎内部产生的——哨兵一跳到位，
    还能顺带记下**是哪一行叫的它**（短路点叫 = 判据成立；转完所有分支掉到尾部才叫 = 分支没接住）。
    这两种在"引擎确实被叫了"这一层看得一模一样，差别全在下一层。
    """
    import app.config as _cfg
    import app.llm.client as _client
    import app.main as _M

    hits = []

    def cfg(*a, **k):
        return {"api_key": "test-key", "base_url": "http://x", "model": "m"}

    async def sentinel(*, module_key=None, session_id=None, message=None, prompt=None,
                       tools=None, persona_key=None, model_config=None, emit=None):
        # ⚠️ 这里收到的 `tools` 是**注册表那份 dict**（name → Tool），不是摆给模型的 schema 列表
        #    —— 那层转换在 `_run_agent_loop` 里面才做。按 list 迭代它会吐出键名字符串，
        #    `t["function"]["name"]` 就是 "TypeError: string indices must be integers"。
        hits.append({"line": sys._getframe(1).f_lineno, "module": module_key,
                     "tools": sorted(tools) if isinstance(tools, dict) else []})
        return {
            "module": module_key, "session_id": session_id,
            "answer": "【哨兵】这一段该交给模型", "trace": [{"step": 1, "phase": "🤔 哨兵",
                                                            "answer": "引擎被叫到了"}],
            "options": [], "awaiting_choice": False,
        }

    old = (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config, _M._run_agent_loop)
    _client.DeepSeekClient = lambda *a, **k: _NoLLM()
    _cfg.get_llm_config = cfg
    _M.get_llm_config = cfg
    _M._run_agent_loop = sentinel

    def restore():
        (_client.DeepSeekClient, _cfg.get_llm_config, _M.get_llm_config,
         _M._run_agent_loop) = old
    return restore, hits


def _parse_main():
    return ast.parse(io.open(MAIN, encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _chat_impl(tree):
    fn = _func(tree, "_chat_impl")
    if fn is None:
        raise AssertionError("main.py 里找不到 `_chat_impl`（用这个 helper 的断言全在找它）")
    return fn


def _engine_call_ranges(body):
    """`_chat_impl` 里两个 `_run_agent_loop(...)` 调用点的行范围。

    返回 (短路点范围, 尾部范围)：短路点认的是"条件里带 `_agent_mode` 的那个 `if` 包着的那次调用"，
    不认行号——行号每天在漂，条件不漂。
    """
    ifs = [n for n in ast.walk(body)
           if isinstance(n, ast.If) and "_agent_mode" in ast.unparse(n.test)]
    out = {"短路点": None, "尾部": None}
    for n in ast.walk(body):
        if isinstance(n, ast.Return) and "_run_agent_loop" in ast.unparse(n):
            rng = (n.lineno, n.end_lineno)
            in_if = any(i.lineno <= rng[0] and rng[1] <= i.end_lineno for i in ifs)
            out["短路点" if in_if else "尾部"] = rng
    return out


def _call_name(node):
    """Call 节点的被调名（人话：`ast.Name` 的字段叫 `id`，不叫 `name` —— 写错就是静默 0 命中）。"""
    f = node.func
    return getattr(f, "id", None) or getattr(f, "attr", None) or ""


def _line_of(body, needles, what):
    """按**条件本身**找那条分支的行号（找不到就报错，不静默返回 None）。

    两个坑，都在这函数里挡掉：
      ① `ast.unparse` 给布尔式补括号：源码 `is_confirmation(message) and not peek_pending(session_id)`
         解出来是 `is_confirmation(message) and (not peek_pending(session_id))`
         ⇒ 判据只能用**子串数组**（每个都得在），不能拿整句去比，否则红的是我的写法不是代码。
      ② `ast.walk` 会先走到**外层**语句：`if not _station_surface:` 的 unparse 里
         含着下面每一条分支 ⇒ "整句在里面"这种写法永远返回外层行号，比出来的位置全是错的。
         ⇒ If 只比它的 `test`，赋值只比它的 `value`，并取最里面（行号最大）那一处。
    """
    if isinstance(needles, str):
        needles = [needles]
    cands = []
    for n in ast.walk(body):
        if isinstance(n, ast.If):
            hay = ast.unparse(n.test)
        elif isinstance(n, (ast.Assign, ast.Expr)):
            hay = ast.unparse(n.value if isinstance(n, ast.Assign) else n)
        else:
            continue
        if all(x in hay for x in needles):
            cands.append(n.lineno)
    if not cands:
        raise AssertionError(f"找不到{what}（{needles}）—— 分支被改写过了，先修这条判据")
    return max(cands)


def _ask(cli, msg, sid, mod="planner", agent=None):
    body = {"message": msg, "session_id": sid, "module": mod}
    if agent is not None:
        body["agent_mode"] = agent
    d = cli.post("/api/chat", json=body).json()
    ph = [t.get("phase", "") for t in (d.get("trace") or []) if isinstance(t, dict)]
    return d, ph


def _kinds(d):
    return [o.get("kind") for o in (d.get("options") or []) if isinstance(o, dict)]


def _save(sid, card):
    from app.agent.pending import save_pending
    save_pending(sid, [card])


# --------------------------------------------------------------------------- A 形状
def batch_a_shape():
    c = Checker()
    title("A 批 · 形状：判据收成一份、名单从源码数、短路条件三个操作数且位置没动")
    src = io.open(MAIN, encoding="utf-8").read()
    tree = _parse_main()
    body = _chat_impl(tree)

    fn = _func(tree, "_any_pending")
    c.check("A1 `_any_pending` 全项目只定义一处、就在模块级、返回的是 bool",
            src.count("def _any_pending(") == 1 and fn is not None
            and isinstance(fn.returns, ast.Name) and fn.returns.id == "bool",
            f"定义 {src.count('def _any_pending(')} 处")

    # A2 是这一批的核心：**名单不是抄的，是从源码里数出来的**
    accessors = sorted(n.name for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name.startswith("_pending_")
                       and n.returns is not None
                       and ast.unparse(n.returns) == "dict | None")
    listed = sorted({x.id for x in ast.walk(fn)
                     if isinstance(x, ast.Name) and x.id in accessors}) if fn else []
    # 自证：先证明"扫到了东西"。扫回空表时 A2 会两边都空、照样绿 —— 那是假绿的那种绿。
    c.check("A2-bis 样本跑通了：从模块级 `_pending_*` 里确实数出了 8 个「返回 dict | None」的单卡取器"
            "（一个都没数到 = AST 走错了地方，后面那条比对就没有意义）",
            len(accessors) == 8, f"数到 {len(accessors)} 个：{accessors}")
    c.check("A2 名单与取器**逐个对齐**：新增一种卡却忘了加进 `_any_pending` 就红"
            "（#33 那一族『卡在但看不见』的结构性防法，W3/W4 的靶子）",
            listed == accessors, f"名单 {len(listed)} / 取器 {len(accessors)}")
    c.check("A3 #62 补的那两条点名叫得出：`_pending_todo_mode`（二选一卡，以前就漏在这里）"
            "与 `_pending_todo_slots`（候选卡）都在名单里",
            {"_pending_todo_mode", "_pending_todo_slots"} <= set(listed), listed[:8])
    c.check("A4 就地写的『把取器排一圈』全站只剩这一份（别处再写一份就是两个判据，改一处漏一处）",
            src.count("any(f(session_id) for f in (") == 1,
            f"{src.count('any(f(session_id) for f in (')} 处")

    sc_if = [n for n in ast.walk(body)
             if isinstance(n, ast.If) and "_agent_mode" in ast.unparse(n.test)
             and "_run_agent_loop" in ast.unparse(n)]
    c.check("A5 短路条件正好是三个操作数的 `and`：开关 + 「手上没卡」+ 「没材料」"
            "（写成 `or`、或把状态那一维丢掉，这条先红 —— W1/W2 的靶子）",
            len(sc_if) == 1 and isinstance(sc_if[0].test, ast.BoolOp)
            and isinstance(sc_if[0].test.op, ast.And)
            and sorted(ast.unparse(v) for v in sc_if[0].test.values)
            == sorted(["_agent_mode", "not _any_pending(session_id)", "not _is_material"]),
            sc_if and sorted(ast.unparse(v) for v in sc_if[0].test.values))
    c.check("A5-bis 短路块里只有一次调用：直接 `return await _run_agent_loop(...)`（没夹别的话）",
            len(sc_if) == 1 and len(sc_if[0].body) == 1
            and [_call_name(n) for n in ast.walk(sc_if[0].body[0])
                 if isinstance(n, ast.Call)] == ["_run_agent_loop"],
            [type(s).__name__ for s in (sc_if[0].body if sc_if else [])])

    rng = _engine_call_ranges(body)
    c.check("A6 短路点仍在红线三件之后、`# 3b)` 清空分支之前（#61 定的位置，本单只加条件没搬动它）",
            rng["短路点"] is not None and rng["尾部"] is not None
            and _line_of(body, ["is_confirmation(message)", "not peek_pending(session_id)"],
                         "3a-c 确认落空兜底") < rng["短路点"][0]
            < _line_of(body, ["wants_clear_timetable(message)"], "3b 清空分支"),
            f"短路 {rng['短路点']} / 尾部 {rng['尾部']}")
    c.check("A6-bis `_is_material` 在短路点**之上**就算好了（判据读得到，不是读到半成品）",
            _line_of(body, "looks_like_material(message)", "材料判据") < rng["短路点"][0],
            f"材料 @{_line_of(body, 'looks_like_material(message)', '材料判据')}")

    holds = [n for n in ast.walk(body)
             if isinstance(n, ast.Assign) and any(
                 isinstance(t, ast.Name) and t.id == "_holding_card" for t in n.targets)]
    gate = _line_of(body, ["_campus_fact_ask(message)", "not _holding_card"], "校历闸")
    c.check("A7 `_holding_card` 改成**取用**同一个函数（不是再抄一份名单），且赋值恰好一处",
            len(holds) == 1 and ast.unparse(holds[0].value) == "_any_pending(session_id)",
            holds and ast.unparse(holds[0].value))
    c.check("A7-bis 取值时刻没上提：仍紧贴校历闸前一行（2971→3210 之间有分支会改写暂存，"
            "上提就等于让那道闸读到分支执行前的状态——今天靠巧合成立，明天就是 bug）",
            0 < gate - holds[0].lineno <= 6, f"赋值 @{holds[0].lineno} / 闸 @{gate}")
    c.check("A8 聊天主体里 `_any_pending(...)` 只有两处调用（短路 + `_holding_card`），"
            "尾部 planner 知识库直答那道闸照旧读 `_holding_card` —— 判据一份、取值两处",
            sum(1 for n in ast.walk(body) if isinstance(n, ast.Call)
                and _call_name(n) == "_any_pending") == 2
            and "_holding_card" in ast.unparse(body),
            "")
    c.check("A9 名单必须写在函数体里、不是默认参数：`_any_pending` 的函数体引用了那 8 个取器",
            fn is not None and sorted({x.id for x in ast.walk(fn)
                                       if isinstance(x, ast.Name)} & set(accessors)) == listed, "")
    return c


# --------------------------------------------------------------------------- B 行为
def batch_b_behaviour():
    c = Checker()
    title("B 批 · 行为：挂着卡 / 有材料 ⇒ 一次都不许叫引擎；干净 ⇒ 叫的是短路点")
    day = _next_wed_iso()
    restore, hits = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([{"day": 1, "start": "08:00", "end": "09:40",
                              "course": "高等数学", "location": "教五-01"}])
            wipe_todos()
            cli = make_client()
            with pin_now(10, 0):
                cards = _cards(day)
                mat = _material(day)
                # B0 夹具自证：卡造不出来时，下面每一条都会以"没叫引擎"蒙过去 —— 先把这条路堵死
                c.check("B0 四张卡都用生产代码造出来了（mode/auto/slots/stated）＋材料判据认这段",
                        all(cards[k] is not None for k in ("mode", "auto", "slots", "stated"))
                        and cards["mode"]["kind"] == "todo_mode"
                        and cards["auto"].get("auto") is True
                        and _from_main(cards), f"{ {k: (v or {}).get('kind') for k, v in cards.items()} }")

                def run(tag, kind_or_none, msg, sid):
                    hits.clear()
                    if kind_or_none is not None:
                        _save(sid, kind_or_none)
                    d, ph = _ask(cli, msg, sid, agent=True)
                    return d, ph, list(hits)

                # 1) 二选一卡 + 「我自己挑」：3c-pre3 必须接住（W1 的正控）
                d, ph, h = run("b1", cards["mode"], "我自己挑", "t62-mode")
                c.check("B1 Agent 模式 + 挂一张二选一卡 + 「我自己挑」→ 当场出候选卡，引擎一次都没叫"
                        "（这就是 #61 那版判据会被关掉的那支）",
                        not h and _kinds(d) == ["todo_slots"] and any("学生选了「我自己定」" in p for p in ph),
                        f"引擎 {len(h)} 次 / 卡 {_kinds(d)} / {ph[-1][:34] if ph else '无轨迹'}")
                c.check("B1-bis 这一次确实是因为「有卡」才走的确定性：`_any_pending` 对这张卡回真",
                        _any_pending("t62-mode") is True, "")

                # 2) 系统替他挑的那条 + 「我自己挑」：3c-pre4
                d, ph, h = run("b2", cards["auto"], "我自己挑", "t62-auto")
                c.check("B2 Agent 模式 + 挂单条确认条(带 auto) + 「我自己挑」→ 摊候选卡，引擎 0 次",
                        not h and _kinds(d) == ["todo_slots"], f"引擎 {len(h)} 次 / 卡 {_kinds(d)}")

                # 3) 候选卡 + 学生报了个点：3c-pre2（W4 的正控）
                d, ph, h = run("b3", cards["slots"], "下午三点到四点", "t62-slots")
                c.check("B3 Agent 模式 + 候选卡 + 「下午三点到四点」→ 出单条(15:00-16:00)，引擎 0 次"
                        "（注释里那句『模型只会说已经帮你排好啦，日程里空的』就是关掉它的代价）",
                        not h and _kinds(d) == ["todo_add"]
                        and (d["options"][0].get("start"), d["options"][0].get("end")) == ("15:00", "16:00"),
                        f"引擎 {len(h)} 次 / 卡 {_kinds(d)}")

                # 4) 学生自己报了点的单条卡 + 换时间：3c-pre
                d, ph, h = run("b4", cards["stated"], "改成晚上七点到八点", "t62-retime")
                c.check("B4 Agent 模式 + 单条卡 + 「改成晚上七点到八点」→ 换时间重出条(19:00-20:00)，引擎 0 次",
                        not h and _kinds(d) == ["todo_add"]
                        and (d["options"][0].get("start"), d["options"][0].get("end")) == ("19:00", "20:00"),
                        f"引擎 {len(h)} 次 / 卡 {_kinds(d)}")

                # 5) 粘一段材料（手上没卡）：`_is_material` 那一支（W2 的正控）
                d, ph, h = run("b5", None, mat, "t62-mat")
                c.check("B5 Agent 模式 + 粘两行带日期的材料 → 走确定性提取出批量卡，引擎 0 次"
                        "（材料不是『这句话像什么』，是『刚上传了什么』）",
                        not h and any("认出一段材料" in p for p in ph) and d.get("options"),
                        f"引擎 {len(h)} 次 / {ph[-1][:30] if ph else '无轨迹'}")

                # 6) 手上干净：这时才该去找模型，而且要**在短路点**就去找
                hits.clear()
                d, ph = _ask(cli, "帮我把课表全删了", "t62-clear-on", agent=True)
                site = _site_of(rng := _engine_call_ranges(_chat_impl(_parse_main())), hits)
                c.check("B6 Agent 模式 + 手上干净 + 「课表全删了」→ 引擎被叫 1 次，"
                        "且叫它的是**短路点**（不是转完所有分支才掉到尾部）",
                        len(hits) == 1 and site == "短路点", f"命中 {len(hits)} 次 / {site}")
                c.check("B6-bis 同一句话**关着**时引擎一次都不叫、走确定性清空分支"
                        "（B6 的差异只能来自那个开关，不能来自别的东西）",
                        _off_clear(cli) == "确定性", "")

                # 7) 判据的代价，写清楚：挂着一张卡时说了句与卡无关的话
                hits.clear()
                _save("t62-digress", cards["auto"])
                d, ph = _ask(cli, "今天天气怎么样", "t62-digress", agent=True)
                site = _site_of(_engine_call_ranges(_chat_impl(_parse_main())), hits)
                c.check("B7 挂着卡 + 一句无关的话 → 仍然**不短路**：转完所有分支才掉到尾部引擎"
                        "（施工单说清的代价：有卡时这句话先给确定性分支过一遍筛）",
                        len(hits) == 1 and site == "尾部", f"命中 {len(hits)} 次 / {site}")

                # 8) 五句在**非 Agent** 模式下与 Agent 模式逐字相同（本单没碰确定性分支本体）
                for tag, card, msg in (("mode", cards["mode"], "我自己挑"),
                                       ("auto", cards["auto"], "我自己挑"),
                                       ("slots", cards["slots"], "下午三点到四点"),
                                       ("retime", cards["stated"], "改成晚上七点到八点"),
                                       ("mat", None, mat)):
                    # 两边都要先把"上一轮留下的那张卡"摆好：不摆的话这一句就不是同一道题
                    #       （实测：空手上限一句「改成晚上七点到八点」被"看不见 X"那一档接走，
                    #        回「没找到那条待办」—— 那是在比两句不同的话。）
                    if card is not None:
                        _save(f"t62-off-{tag}", card)
                        _save(f"t62-on-{tag}", card)
                    a = cli.post("/api/chat", json={"message": msg, "session_id": f"t62-off-{tag}",
                                                    "module": "planner"}).json()
                    b = cli.post("/api/chat", json={"message": msg, "session_id": f"t62-on-{tag}",
                                                    "module": "planner", "agent_mode": True}).json()
                    c.check(f"B8-{tag} 开关只改「走不走快路」，不改答案：关/开两边逐字相同、卡同 kind",
                            a.get("answer") == b.get("answer") and _kinds(a) == _kinds(b),
                            f"{_kinds(a)} / {_kinds(b)} / {(a.get('answer') or '')[:20]}")

                # 9) 写库红线：状态那一维把话接住了，也不许因此提前写库
                seed_todos([])
                _save("t62-confirm", cards["mode"])
                d, ph = _ask(cli, "确认", "t62-confirm", agent=True)
                c.check("B9 Agent 模式 + 挂着二选一卡 + 学生回「确认」→ 仍然**一条都不写**"
                        "（他还没选时间由谁定；写库必须等他点带时间的卡）",
                        _todo_count() == 0, f"库里 {len(_todo_list())} 条")

                # 10) 卡不许被短路"挤掉"：走到引擎那一路时暂存里的卡还在（B7 的续集）
                c.check("B10 那一句闲话之后那张卡**还在**（掉到模型不等于把卡清掉，#33 那一族）",
                        _any_pending("t62-digress") is True, "")
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- C 语义
def batch_c_pending_semantics():
    c = Checker()
    title("C 批 · `_any_pending` 的回值：八种卡算真，两份候选清单与三种非待办暂存算假")
    restore, _hits = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            c.check("C1 新会话（什么都没挂）回 False —— 这是「干净就交给模型」那一半的地基",
                    _any_pending("t62-none") is False, "")
            for name in CARD8:
                sid = f"t62-kind-{name}"
                _save(sid, {"kind": KIND8[name], "title": "游泳", "summary": "s", "applied": False})
                c.check(f"C2 {KIND8[name]:18s} 挂着时 `_any_pending` 回真（取器 `{name}` 在名单里且真的读得到）",
                        _any_pending(sid) is True, "")
            c.check("C2-bis 已点过的卡（`applied: True`）回假：那条取器都带 `not o.get('applied')`，"
                    "名单跟着这个口径走",
                    _any_pending_after_apply() is False, "")
            for aux in ("todo_remove_candidates", "todo_done_candidates"):
                sid = f"t62-aux-{aux}"
                _save(sid, {"kind": aux, "todos": [{"id": "x", "title": "游泳"}]})
                c.check(f"C3 挂着「{aux}」时回假 —— #59 定过它**不是卡**，本单沿用同一口径。"
                        "⚠️ 代价：Agent 模式下挂着它、学生回「第二条」会被短路给模型（已回报，等批）",
                        _any_pending(sid) is False, "")
            for kind in ("timetable_change", "timetable_clear", "work_order_report"):
                sid = f"t62-non-{kind}"
                _save(sid, {"kind": kind, "summary": "s", "applied": False})
                c.check(f"C4 非待办那一族的暂存「{kind}」回假（docstring 里那句实话钉住，别让它漂）"
                        "—— 那三种的「学生回确认」由短路点**之上**的 3) / 3a-c 接住，卡不会丢",
                        _any_pending(sid) is False, "")
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- D 配对：卡不丢
def batch_d_card_not_evicted():
    c = Checker()
    title("D 批 · 只挂二选一卡时校历闸不许接管（与挂别的卡配对，外加一条『没挂卡时必须接管』的正控）")
    day = _next_wed_iso()
    restore, hits = _patch()
    try:
        with sandbox():
            write_accounts(["admin1"])
            write_timetable([])
            wipe_todos()
            seed_kb([("什么时候放假", "寒假从一月十九号开始，三月二号报到注册。", ["放假", "开学"])])
            cli = make_client()
            with pin_now(10, 0):
                cards = _cards(day)
                Q = "啥时候放假"

                def holding(card, sid, agent):
                    hits.clear()
                    _save(sid, card)
                    d, ph = _ask(cli, Q, sid, agent=agent)
                    return d, ph, list(hits)

                # 基线：挂 todo_add 卡时闸不接管（#42-A 一直就这样，这是"应该长什么样"的那一半）
                d_add, ph_add, _ = holding(cards["stated"], "t62-cal-add", False)
                # 本单要修的那一半：挂 modality 卡时必须**同样**（以前名单漏了它 ⇒ 被挤掉）
                d_mode, ph_mode, _ = holding(cards["mode"], "t62-cal-mode", False)
                c.check("D1 挂 todo_add 卡 + 问校历 → 知识库直答**不接管**（基线）",
                        not any("📚" in p for p in ph_add), ph_add[:2])
                c.check("D2 挂二选一卡 + 问校历 → 与 D1 **同一个结果**（W3 的靶子：漏一项这里就分叉）",
                        not any("📚" in p for p in ph_mode), ph_mode[:2])
                c.check("D2-bis 而且那张卡确实还在暂存里（不接管 ≠ 把卡清了）",
                        _any_pending("t62-cal-mode") is True, "")
                # 正控：什么都没挂时必须接管。少了这条，"把闸整个关掉"也能蒙过 D1/D2。
                hits.clear()
                d_none, ph_none = _ask(cli, Q, "t62-cal-none", agent=False)
                c.check("D3 正控：手上干净时那道闸**照旧接管**、答得出来（不许为了绿 D2 把闸关掉）",
                        any("📚" in p for p in ph_none) and "寒假" in (d_none.get("answer") or ""),
                        ph_none[:2])
                # Agent 模式那一侧：挂着卡 ⇒ 不短路 ⇒ 闸同样不接管
                d_ag, ph_ag, h_ag = holding(cards["mode"], "t62-cal-mode-on", True)
                c.check("D4 Agent 模式下同样不接管，而且**没有**在短路点跳去引擎"
                        "（挂着卡这句话先给确定性分支过筛，与 D2 同一条路）",
                        not any("📚" in p for p in ph_ag)
                        and _site_of(_engine_call_ranges(_chat_impl(_parse_main())), h_ag) != "短路点",
                        f"引擎 {len(h_ag)} 次 / {ph_ag[:1]}")
                c.check("D5 两栏共用一份判据：planner 栏那条知识库直答闸读的也是 `_holding_card`"
                        "（同一份名单、两处各自取数；这里核的是「闸的条件里带着它」）",
                        src_count("_holding_card") >= 3, f"引用 {src_count('_holding_card')} 处")
    finally:
        restore()
    return c


# --------------------------------------------------------------------------- E 码页
def batch_e_codepage():
    c = Checker()
    title("E 批 · #62-B：subprocess 的两端一起钉死，换 shell 不改变结果")
    t67 = io.open(T67, encoding="utf-8").read()
    c.check("E1 test_67 K5 那个子进程**子端**写死 UTF-8（`-X utf8`），不是靠继承外层 env",
            '"-X", "utf8"' in t67, "")
    c.check("E1-bis **父端**解码也写死（`encoding=\"utf-8\"` + `errors=\"replace\"`），"
            "施工单只给了这一半 —— 实测单钉父端时子进程照样吐 GBK，解出来是一串「֪ʶ」，断言假红",
            'encoding="utf-8"' in t67 and 'errors="replace"' in t67, "")
    c.check("E1-ter 不再出现「`probe.stdout + probe.stderr`」这种 None 一参与就崩整批的写法",
            "probe.stdout + probe.stderr" not in t67
            and '(probe.stdout or "") + (probe.stderr or "")' in t67, "")

    CN = "知识库里没有「天文台」"
    argv_utf8 = [sys.executable, "-X", "utf8", "-c", f"print('{CN}')"]
    clean = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}

    def child(tag, argv, outer):
        env = dict(clean)
        env.update(outer)
        p = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env,
                           encoding="utf-8", errors="replace")
        return (p.stdout or "").strip()

    # ⚠️ 这里**不放**"原件形状"的反例跑一遍。第一版放了，实测代价写在这里：
    #   两端不钉的那一格会在**父进程 stderr** 上打出 reader 线程的 UnicodeDecodeError  traceback，
    #   全量日志里看着就像"环境坏了"（正是本单要治的那类噪声），而它是我自己造的。
    #   反例的证据归变异跑（W5a/W5b，`D:\campustime-scratch\mut62.py`），不进交付的测试集：
    #     W5a 只钉父端 ⇒ 干净外层那一格假红；W5b 两端都不钉 ⇒ 带 utf-8 外层那一格崩。
    print(f"  本机父进程 locale.getpreferredencoding(False) = {locale.getpreferredencoding(False)}")
    for outer in ({}, {"PYTHONIOENCODING": "utf-8"}, {"PYTHONUTF8": "1"}):
        got = child("fix", argv_utf8, outer)
        print(f"  · 外层 {outer or '干净'}：两端钉死后读到 {got[:16]!r}")
        c.check(f"E2 外层 {outer or '干净'} → 两端钉死后仍解出正确中文"
                f"（这条与外层无关，所以换机器、换 shell 都不会莫名红）",
                got == CN, got[:16])
    c.check("E3 test_67 里**每一个** `subprocess.run(..., text=True)` 都带了 encoding"
            "（不是只核 K5 那一条 —— 一个文件里留两处，红的永远是没人注意的那处）",
            all("encoding=" in ast.unparse(n) for n in ast.walk(ast.parse(t67))
                if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run"
                and any(k.arg == "text" for k in n.keywords)),
            "")
    # 同类整扫：施工单说"只报不改"，这里就把账目打出来（数字只当读数，不当断言）。
    # ⚠️ 判据必须是 **AST 级的关键字**，不是 `grep "text=True" | grep -v encoding`：
    #    后者会把 `run_all.py:52`、`test_77` 那四处都记进来 —— 它们其实都写了 encoding，
    #    只是换行写在下一行（关键字跨行 ⇒ 行级 grep 看不见）。整族扫的是同一族，别用错尺子。
    pend = []
    for fn in sorted(os.listdir(os.path.join(PROJ_DIR, "tests"))):
        if not fn.endswith(".py"):
            continue
        try:
            tr = ast.parse(io.open(os.path.join(PROJ_DIR, "tests", fn), encoding="utf-8").read())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tr):
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "run" \
                    and any(k.arg == "text" for k in n.keywords) \
                    and not any(k.arg == "encoding" for k in n.keywords):
                pend.append(f"{fn}:{n.lineno}")
    print(f"  同类未写 encoding 的记账（本单只报不改）：{len(pend)} 处 → {', '.join(pend) or '无'}")
    c.check("E3-bis test_67 自己那一处已经干净（其余文件先记账，别一次全改 —— 一次改一处才分得清是谁红的）",
            not [x for x in pend if x.startswith("test_67")], "")
    return c


# --------------------------------------------------------------------------- 小工具（放后面，名字先用）
def src_count(s):
    return io.open(MAIN, encoding="utf-8").read().count(s)


def _any_pending(sid):
    from app.main import _any_pending as f
    return f(sid)


def _any_pending_after_apply():
    from app.agent.pending import mark_applied
    sid = "t62-applied"
    _save(sid, {"kind": "todo_add", "title": "游泳", "summary": "s", "applied": False})
    mark_applied(sid)
    return _any_pending(sid)


def _todo_list():
    """沙箱里当前有多少条待办（用生产读接口，不自己拼文件路径）。"""
    from app.store import list_todos
    return list_todos()


def _todo_count():
    return len(_todo_list())


def _from_main(cards):
    """材料那一段真被 `looks_like_material` 认下来了吗（判据本体在生产代码里，不在这儿重算）。"""
    from app.modules.material import looks_like_material
    return looks_like_material(_material(_next_wed_iso()))


def _site_of(ranges, hits):
    """按调用点的行范围给每次命中归位：短路点 / 尾部 / 别处（范围来自 AST，不写死行号）。"""
    if not hits:
        return "没叫"
    ln = hits[0]["line"]
    for tag, rng in ranges.items():
        if rng and rng[0] <= ln <= rng[1]:
            return tag
    return f"别处(@{ln})"


def _off_clear(cli):
    hits2 = []
    d, ph = _ask(cli, "帮我把课表全删了", "t62-clear-off", agent=False)
    return "确定性" if any("生成清空提案" in p for p in ph) else f"其他{ph[:2]}"


def main():
    code = 0
    print("\n" + "=" * 60)
    print("test_83_agent_mode_state_dimension：#62 Agent 模式短路判据补『状态』这一维")
    print("=" * 60)
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（行为）", batch_b_behaviour),
                      ("C 批（`_any_pending` 语义）", batch_c_pending_semantics),
                      ("D 批（卡不丢 / 配对）", batch_d_card_not_evicted),
                      ("E 批（#62-B 码页）", batch_e_codepage)):
        try:
            rc = fn().summary(label)
        except Exception as e:                       # 一批崩不许拖着其余批次一起看不见数
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
