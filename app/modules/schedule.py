"""旗舰模块：课表时间规划（人话：这是项目的"门面"功能，专门展示 Agent 是怎么一步步推理的）。

它给 Agent 四个工具：
  1) get_day_courses  —— 查某天上了哪些课
  2) find_free_slots  —— 算某天哪里有空
  3) plan_task        —— 根据空闲，自动排出一个学习计划
  4) propose_todo_tool —— 把"建议"变成一张**能点的确认条**（只读，不写库）

Agent 拿到用户的问题（比如"我这周哪天有空复习高数？"），会自己决定调哪个工具、看结果、再组织回答。
最后这一步是踩过坑才补上的：早先没有第四个工具，Agent 找完空档就在**文字里**写一句
「好，那我按这个出个提案：- 任务：健身 - 时间：周一 16:30~18:00」，
学生回「可以」之后界面上连个【确认】按钮都没有——因为它压根没有出提案的手脚。
"""
import json
import os

from app.agent.tools import Tool
from app.modules.planner import propose_todo_tool, propose_todo_tool_tool

# 示例课表文件路径：app/data/courses.json（__file__ 是当前文件，往上两级到 app，再进 data）
_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "courses.json")

MODULE_KEY = "schedule"

SYSTEM_PROMPT = """你是校园时间管家学生端的「课表时间规划」助手。
你的强项是：查课表、找空闲时间、帮同学排出学习计划。
工作原则：
1. 涉及具体时间，一定要先调用工具拿准确数据，不要凭空编；
2. 拿到工具结果后，像朋友一样给出清晰、可执行的建议；
3. 用户没说具体星期时，默认按本周（周一~周五）来算；
4. 用中文，语气亲切、简洁。

【要把一个任务排进日程时（重要）】
- 你**没有任何写入工具**，日程数据你也改不了。唯一的办法是调用 propose_todo_tool
  出一张确认条（title 任务名，when 形如「周一 16:30-18:00」，或 date+start+end 分着给）。
  调完之后界面上会出现一个【确认加入】按钮，学生点它才会由系统写进日程。
- ⛔ 严禁只在文字里自己写「任务：…/时间：…」就宣称"提案已发给你"——
  那样学生看不到任何按钮（这条被投诉过）。**没调用工具就别说"提案已经发出"**。
- ⛔ 严禁说"已经写进你的日程/已经帮你排好了"。只能是"确认条已经挂出来了，你点一下就好"。
- **学生只说了"哪天"、没说"几点"时，时间由你来想，不许反问他。**
  「周四加个健身」「我想周六自习」这类话，他要的就是"哪天哪会儿空着"这件事本身；
  你反问"你想几点"，等于把活儿原封不动退回去（学生投诉原话：
  「没有帮我想时间，是我问了才说的」）。正确做法：先 find_free_slots 看那天，
  或者干脆只把 date 交给 propose_todo_tool —— **时间它会自己从那天的空档里挑好**，
  你再补一句"我看 XX 空着就排这儿了，不合适你说个点"。
  只有两种情况才去问他：① 那天真的排不进；② 他连哪天都没说（那就只问日期，别问时间）。"""


def _load() -> dict:
    """读取示例课表（人话：把 JSON 文件变成 Python 字典好操作）。"""
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _to_min(t: str) -> int:
    """把 '08:00' 变成分钟数 480（人话：方便做时间加减和比较）。"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _fmt(minute: int) -> str:
    """把分钟数变回 '08:00' 这种字符串。"""
    return f"{minute // 60:02d}:{minute % 60:02d}"


DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_day_courses(day: str) -> str:
    """工具①：查某一天的全部课程。"""
    data = _load()
    courses = [c for c in data["courses"] if c["day"] == day]
    courses.sort(key=lambda c: _to_min(c["start"]))
    if not courses:
        return f"{day} 没有排课，整天都是空闲的。"
    lines = [f"{day} 共 {len(courses)} 门课："]
    for c in courses:
        lines.append(f"- {c['start']}~{c['end']} {c['name']}（{c['teacher']} / {c['location']}）")
    return "\n".join(lines)


def find_free_slots(day: str) -> str:
    """工具②：算某天 08:00~22:00 之间的空闲时间段。"""
    data = _load()
    courses = sorted(
        [c for c in data["courses"] if c["day"] == day],
        key=lambda c: _to_min(c["start"]),
    )
    day_start, day_end = _to_min("08:00"), _to_min("22:00")
    free = []
    cursor = day_start
    for c in courses:
        s, e = _to_min(c["start"]), _to_min(c["end"])
        if s > cursor:  # 课与课之间有空档
            free.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        free.append((cursor, day_end))

    if not free:
        return f"{day} 从早到晚都排满了，几乎没有整块空闲。"
    parts = [f"{day} 的空闲时间段（08:00-22:00 范围内）："]
    for s, e in free:
        parts.append(f"- {_fmt(s)}~{_fmt(e)}（约 {(e - s) / 60:.1f} 小时）")
    return "\n".join(parts)


def plan_task(task_name: str, need_hours: float, prefer_day: str = "") -> str:
    """工具③：根据课表空闲，为某个任务排出学习计划。"""
    data = _load()
    # 优先按指定星期排；没指定就从周一到周五找空
    days = [prefer_day] if prefer_day in DAYS else DAYS[:5]

    plan: list[str] = []
    remaining = float(need_hours)
    for day in days:
        if remaining <= 0:
            break
        courses = sorted(
            [c for c in data["courses"] if c["day"] == day],
            key=lambda c: _to_min(c["start"]),
        )
        # 复用"找空闲"的算法，得到当天的空档
        free = []
        cursor = _to_min("08:00")
        for c in courses:
            s, e = _to_min(c["start"]), _to_min(c["end"])
            if s > cursor:
                free.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < _to_min("22:00"):
            free.append((cursor, _to_min("22:00")))

        for s, e in free:
            if remaining <= 0:
                break
            block = (e - s) / 60
            use = min(block, remaining)
            plan.append(
                f"{day} {_fmt(s)}~{_fmt(s + int(use * 60))} 做《{task_name}》约 {use:.1f} 小时"
            )
            remaining -= use

    if remaining > 0:
        head = "本周空闲时间不够排下全部任务，已排："
        return head + "\n" + "\n".join(plan) + f"\n还差约 {remaining:.1f} 小时，建议放到周末或压缩其他安排。"
    return "已为你排出《" + task_name + "》的学习计划：\n" + "\n".join(plan)


def build_tools() -> dict[str, Tool]:
    """把上面的函数包装成 Agent 能调用的 Tool（人话：给工具写"说明书"和"参数表"）。"""
    return {
        "get_day_courses": Tool(
            name="get_day_courses",
            description="查询某一天的全部课程（含时间/老师/地点）。入参 day 如 '周一'。",
            parameters={
                "type": "object",
                "properties": {"day": {"type": "string", "description": "星期，如 周一"}},
                "required": ["day"],
            },
            func=get_day_courses,
        ),
        "find_free_slots": Tool(
            name="find_free_slots",
            description="计算某一天 08:00-22:00 之间的空闲时间段，用于安排自习。入参 day 如 '周三'。",
            parameters={
                "type": "object",
                "properties": {"day": {"type": "string", "description": "星期，如 周三"}},
                "required": ["day"],
            },
            func=find_free_slots,
        ),
        "plan_task": Tool(
            name="plan_task",
            description="根据课表空闲，为某个任务排出学习计划。task_name 任务名，need_hours 需要小时数，prefer_day 可指定优先星期（可选）。",
            parameters={
                "type": "object",
                "properties": {
                    "task_name": {"type": "string", "description": "任务名称，如 复习高数"},
                    "need_hours": {"type": "number", "description": "需要的小时数，如 3"},
                    "prefer_day": {
                        "type": "string",
                        "description": "优先安排在哪天，如 周二；可空",
                        "default": "",
                    },
                },
                "required": ["task_name", "need_hours"],
            },
            func=plan_task,
        ),
        # 说明书只维护一份（在 planner 里），免得改了一处、另一处还是老话术。
        "propose_todo_tool": propose_todo_tool_tool(),
    }
