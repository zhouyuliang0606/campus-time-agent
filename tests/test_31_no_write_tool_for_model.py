"""模型侧写权限收口：工具箱里一个能落库的工具都不许有（test_31）。

**守的是什么**：README 的核心主张——「模型无论走哪条路都碰不到写入接口」。
2026-10-08 核查时发现这句话被代码证伪了：`planner.build_tools()` 里注册着
`add_todo_tool`，其函数体直接 `add_todo()` 落库，而 ReAct 引擎能把模型给的
tool_call 原样执行。当时唯一的防线是工具描述里那句「必须等学生明确确认之后才能调用」——
**规矩写在提示词里，模型口语一变就可能绕过去**。同批收归系统的本来还有
`update_todo_status`（见 `propose_todo_done_tool` 的 docstring），当时唯独漏了这一个。

仓库里**早就有一条断言**写着「没有一个能写库的工具混进来」（test_06 第十二批 ②），
但它只查了 `build_scheduling_tools()`，没查模块自己的 `build_tools()`——
所以 planner 那条真写库的路一直没被钉住。**这是"测试全绿、洞还在"的现行样本**，
本护栏把断言从"某个子集"换成"全部模块 × 全部工具 × AST 看真调用"。

⚠️ 只删**注册**，函数本体必须留着：`planner.py` 里"学生回确认类文字"那条系统侧路径
（`results.append(add_todo_tool(...))`）还在调它写库。C 批专门钉这一点，
防止下一个人以为是"把这功能删了"而顺手删干净。

⚠️ B 批那次"模型偷写"用的时间，是从当天真实空档里挑的，不是随手写的 19:00。
理由：随手写会撞课被拒，"一条待办都没多"就变成**因为撞课才通过**——把漏洞装回去
护栏照样全绿。反向验证过：把注册装回来时 A1/A2/A3/B1/B2/C3 **和 B3** 一起变红，
说明 B3 是真的有牙齿。
"""
import ast
import datetime as _dt
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (Checker, SEED_COURSES, sandbox, write_timetable, seed_todos,  # noqa: E402
                      title)
from app.modules.planner import find_free_slots  # noqa: E402

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些函数落库。工具函数体里**真调用**它们（AST 层面，不是提提名字）即为越权。
WRITE_FUNCS = {
    "add_todo", "update_todo", "delete_todo", "remove_todo", "update_todo_status",
    "import_timetable", "clear_timetable", "save_timetable", "save_courses",
    "add_course", "delete_course", "save_pending",
}


def _write_calls_in(fn) -> set:
    """函数体里真实调用了哪些写库函数（docstring / 提示词里提名字不算）。"""
    try:
        tree = ast.parse(inspect.getsource(fn))
    except (OSError, TypeError, SyntaxError):
        return set()
    hits = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None)
            if name in WRITE_FUNCS:
                hits.add(name)
    return hits


def batch_structure(c):
    title("A. 结构：所有模块的工具箱里，没有任何一个工具能落库")
    from app import main as M

    offenders = []
    total_tools = 0
    for key, (_prompt, build) in M.REGISTRY.items():
        for tool_name, tool in build().items():
            total_tools += 1
            hits = _write_calls_in(tool.func)
            if hits:
                offenders.append(f"{key}.{tool_name}→{sorted(hits)}")
    c.check(f"A1 全模块 {total_tools} 个工具，AST 查到真写库的：0 个",
            not offenders, f"越权工具：{offenders}")
    tools = M.REGISTRY["planner"][1]()
    c.check("A2 planner 的工具箱里确实没有 add_todo_tool 这个名字",
            "add_todo_tool" not in tools)
    c.check("A3 只删了那一个、只多了 #63 那一只：planner 现在 13 个工具，提案/查询的本事一个没少"
            "（12 + 1；这个数字钉的是\"没有第二个人顺手往里塞\"，动它之前先查是哪只进的）",
            len(tools) == 13 and {
                "propose_todo_tool", "propose_todo_mode_tool", "propose_todo_slots_tool",
                "propose_slots", "propose_todo_remove", "propose_todo_done",
                "propose_timetable_change", "propose_course_change",
                "propose_clear_timetable", "find_free_slots", "list_day_todos",
                "get_weekly_timetable", "list_todos_range"} <= set(tools),
            sorted(tools))
    c.check("A4 摘的是注册、不是把能力删了：scheduling_tools() 那组仍齐（test_06 原来只查到这儿）",
            {"find_free_slots", "propose_todo_tool", "propose_slots", "list_day_todos"}
            <= set(__import__("app.modules.planner", fromlist=["x"]).build_scheduling_tools()))


class ScriptedLLM:
    """剧本：第一次开口就要求调用 add_todo_tool（模拟模型"想走捷径"），
    被拒之后再好好回答。不联网，纯本地。

    证据直接记在这个假货身上：引擎把工具结果作为 `role: tool` 消息回灌，
    下一轮 chat 的 messages 里有没有那句拒绝，是**唯一算数的**判据
    （光看源码里有没有那行字符串，证明不了它真的传回了模型手里）。
    """

    def __init__(self, date: str, start: str, end: str):
        self.turn = 0
        self.tools_offered = None
        self.saw_reject = False
        self.reject_text = ""
        # 参数用**当天真实空档**，不能随手写一个时间：那样它会撞课被拒，
        # "一条都没多"就变成因为撞课才过的——把漏洞装回来这一项照样绿，
        # 护栏就成了摆设。只有让它"本来能写成功"，B3 才有牙齿。
        self.args = json.dumps({"title": "偷写的待办", "date": date,
                               "start": start, "end": end}, ensure_ascii=False)

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        self.turn += 1
        if self.turn == 1:
            # 模型这一轮实际拿到的工具清单（schema 名）——它连这个名字都不该见到
            self.tools_offered = {t["function"]["name"] for t in (tools or [])}
            return {"content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "add_todo_tool", "arguments": self.args}}]}
        for m in messages:
            if m.get("role") == "tool":
                txt = str(m.get("content"))
                if "没有名为 add_todo_tool" in txt:
                    self.saw_reject = True
                    self.reject_text = txt
        return {"content": "我没有这个工具，所以什么都没写。"
                  if self.saw_reject else "（异常：没看到拒绝）"}


def _a_free_slot():
    """从今天起往后找一天，挑一个课程和已有待办都避开的时间窗。"""
    for i in range(1, 8):
        d = (_dt.date.today() + _dt.timedelta(days=i)).isoformat()
        free = find_free_slots(d)
        if free:
            return d, free[0]["start"], free[0]["end"]
    return None, None, None


def batch_behavior(c):
    title("B. 行为：模型硬要点这个工具，也写不了库")
    import asyncio
    from app.agent.engine import AgentEngine
    from app.modules.planner import build_tools
    from app.store import list_todos

    date, start, end = _a_free_slot()
    c.check("B0 这一步用的时间确实是空档（否则 B3 会因为撞课而假过）",
            date is not None, f"{date} {start}-{end}")
    llm = ScriptedLLM(date or "2026-12-24", start or "19:00", end or "20:30")
    engine = AgentEngine(llm=llm, tools=build_tools(), system_prompt="测试",
                         session_id="t31-behavior")
    before = {t.get("id") for t in list_todos()}
    asyncio.run(engine.run("帮我加个待办"))
    after = {t.get("id") for t in list_todos()}

    c.check("B1 这个工具名压根没出现在发给模型的 schema 清单里（模型无从点名）",
            llm.tools_offered is not None and "add_todo_tool" not in llm.tools_offered,
            f"模型拿到 {len(llm.tools_offered or [])} 个工具")
    c.check("B2 模型硬点，引擎回的是「没有名为 add_todo_tool 的工具」，且这句真回到了模型手里",
            llm.saw_reject, llm.reject_text[:60])
    c.check("B3 **一条待办都没多**（这次调用彻底落空）", before == after,
            f"多了 {sorted(after - before)}")
    c.check("B4 拒绝之后还有一轮：模型收到观察再继续想，不是静默吞掉", llm.turn >= 2)


def batch_system_side(c):
    title("C. 系统侧写路径没被砍断（学生确认后仍然落得了库）")
    from app.modules.planner import add_todo_tool
    from app.store import get_timetable, list_todos

    c.check("C1 函数本体还在、仍是系统可调的写入口", callable(add_todo_tool))
    src = open(os.path.join(PROJ_DIR, "app", "modules", "planner.py"),
               encoding="utf-8").read()
    c.check("C2 系统侧那条调用还在（`results.append(add_todo_tool(...))`）",
            "results.append(add_todo_tool(" in src)
    c.check("C3 它不在 build_tools() 的注册块里（源码级再看一眼，防 AST 判定被绕过）",
            '"add_todo_tool": Tool(' not in src)

    # 沙箱里真写一条：证明"收口"没有把确认落库这条路一起断掉。
    # 时间不硬编码——演示课表里 19:00 是有课的，硬写会撞上校验，
    # 那是校验该做的事（C7 专门验它），不是写入失败。
    from app.modules.planner import find_free_slots
    date, slot = "", None
    for i in range(1, 8):
        d = (_dt.date.today() + _dt.timedelta(days=i)).isoformat()
        free = find_free_slots(d)
        if free:
            date, slot = d, free[0]
            break
    before = len(list_todos())
    r = add_todo_tool("系统侧确认写入", date, slot["start"], slot["end"]) if slot else "找不到空档"
    c.check("C4 系统调用它 → 真落库（数量 +1）",
            slot is not None and len(list_todos()) == before + 1, r[:40])

    # 校验没被顺手删掉：时间不合法要拒
    bad = add_todo_tool("非法时间", "2026-12-24", "10:00", "09:00")
    c.check("C5 时间不合法仍然拒绝", "不合法" in bad, bad[:40])
    # 注意别用 "20261224" 这种紧凑写法测"坏日期"：Python 3.11+ 的
    # date.fromisoformat 认它，会真的解析成 12 月 24 日，测不出拒绝。
    c.check("C6 日期不成格式仍然拒绝", "不合法" in add_todo_tool("坏日期", "下周四", "10:00", "11:00"))

    # 防撞课：拿沙箱里真实的一节课造冲突，不靠硬编码日期猜
    courses = get_timetable()
    if courses:
        cc = min(courses, key=lambda x: str(x.get("start") or ""))
        wd = int(cc.get("day", 0))
        delta = (wd - _dt.date.today().isoweekday()) % 7 or 7
        date = (_dt.date.today() + _dt.timedelta(days=delta)).isoformat()
        clash = add_todo_tool("撞课测试", date, cc.get("start"), cc.get("end"))
        c.check("C7 跟课程撞时间仍然拒绝（提示层里的防撞课校验没跟着注册一起被删）",
                "撞了" in clash, clash[:60])
    else:
        c.check("C7 沙箱里没有课程数据，撞课这一项没法验（如实报红而不是假装通过）", False)


def batch_prompt_layer(c):
    title("D. 提示层不再教模型「确认后你去调用它」")
    pl = open(os.path.join(PROJ_DIR, "app", "modules", "planner.py"), encoding="utf-8").read()
    mn = open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read()
    c.check("D1 planner 提示里不再有「才调用 add_todo_tool 真正写进日程」",
            "才调用 add_todo_tool" not in pl)
    c.check("D2 planner 提示里明说「你没有任何写入工具」", "没有任何写入工具" in pl)
    c.check("D3 main 的人设规矩同样改口（原来写的是「add_todo_tool 等写入工具必须等学生点头」）",
            "add_todo_tool 等写入工具" not in mn and "工具箱里没有任何写入工具" in mn)
    c.check("D4 propose_slots 的说明书不再暗示模型有个 add_todo 可调",
            "不要调用 add_todo" not in pl)
    c.check("D5 main.py 不再顶置导入 add_todo_tool（摘注册后它就成了死引用）",
            "    add_todo_tool,\n" not in mn)


def main():
    print("===== 模型侧写权限收口（test_31） =====")
    c = Checker()
    # 整个文件共用一个沙箱：B 批要数待办、C 批要真写一条，
    # 都只能写在临时副本上（sandbox 是上下文管理器，退出即删）。
    with sandbox():
        # ★ 自己种状态，别信"沙箱里恰好有什么"（2026-10-14 整族扫）：
        #   C7 要一节真课程来撞，本机有演示课表就绿、干净克隆里课表是空的就红；
        #   待办也清成已知的一份，B3「一条都没多」才是真的在数自己摆的桌子。
        write_timetable(SEED_COURSES)
        seed_todos([{"title": "已有的那条", "date": _dt.date.today().isoformat(),
                    "start": "09:00", "end": "10:00"}])
        batch_structure(c)
        batch_behavior(c)
        batch_system_side(c)
        batch_prompt_layer(c)
    return c.summary("模型侧写权限收口 ")


if __name__ == "__main__":
    raise SystemExit(main())
