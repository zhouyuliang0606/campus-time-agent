"""旗舰模块：课表时间规划（人话：这是项目的"门面"功能，专门展示 Agent 是怎么一步步推理的）。

它给 Agent 六个工具：
  1) get_day_courses  —— 查某天上了哪些课
  2) find_free_slots  —— 算某天哪里有空
  3) plan_task        —— 根据空闲，自动排出一个学习计划
  4) propose_todo_mode_tool  —— 先问「时间你自己定 / 我帮你挑」那张二选一卡（只读，不写库）
  5) propose_todo_slots_tool —— 学生选了"我自己定"之后，把几段候选空档摊成一张**能打勾**的卡片
  6) propose_todo_tool —— 已经定了准点时，出一张**单条**确认条（只读，不写库）

Agent 拿到用户的问题（比如"我这周哪天有空复习高数？"），会自己决定调哪个工具、看结果、再组织回答。
最后这一步是踩过坑才补上的：早先没有出提案的工具，Agent 找完空档就在**文字里**写一句
「好，那我按这个出个提案：- 任务：健身 - 时间：周一 16:30~18:00」，
学生回「可以」之后界面上连个【确认】按钮都没有——因为它压根没有出提案的手脚。

⚠️ 4/5/6 的分工是**学生定的**：
  学生原话一（第二轮）：「由 ai 帮我去挑选合适时间，**进行列举**……由我打勾，进行增加」
  学生原话二（第三轮）：「**要区分两种，一种是我有时间规划了，一种是我没有时间规划
    让他帮我安排，不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定）**」
所以顺序是：**先 4 问清"你自己定还是我帮你挑"** → 他自己定走 5（列几段让他勾）、
他让你挑走 6（时间由系统规划好，并在卡片上写清为什么排在这儿）。
⛔ 别跳过 4 直接问"你想几点"——那是把活儿退回给他。
"""
import json
import os

from app.agent.tools import Tool
from app.modules.planner import (
    propose_todo_mode_tool, propose_todo_mode_tool_tool,
    propose_todo_slots_tool, propose_todo_slots_tool_tool,
    propose_todo_tool, propose_todo_tool_tool,
)

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
- ⛔ 尤其禁止用「**待办提案**」「- 事项：…」「- 时间：…」这种**排版**去替代工具调用。
  这是最容易犯的一种：看着像一份正式提案，界面上却什么都没有。要出条，就调工具；
  调完工具之后**不要再把方案重抄一遍**，直接说"确认条已经挂出来了，点一下就行"。
- ⛔ 也不要说「提案我这边还没生成出来」「你现在点【确认】是空确认」这种话——
  学生没看到按钮就是没看到，你的任务是把工具**调出来**，不是解释为什么没有。
- ⛔ 严禁说"已经写进你的日程/已经帮你排好了"。只能是"确认条已经挂出来了，你点一下就好"。
- **学生要加一件事、还没定时间时，第一步不是列时间、也不是问他几点**（重要）。
  学生原话：「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
  不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定）**」。
  「周四加个健身」「帮我加个游泳」这类话，先用 **propose_todo_mode_tool(title, when)**
  出那张**二选一卡片**，然后停下来等他点：
  · 他点「我自己定」→ 之后才用 **propose_todo_slots_tool 一次列 2~4 段**给他挑
    （摊出一张**能打勾**的卡片，自己勾一个或几个，勾完点【加入日程】才由系统写库）；
    文字里也把这几段列一遍，并说清"都不合适就在「其他时间」自己写一个"。
  · 他点「你帮我挑」→ 由系统把时间规划出来（单条确认条，卡片上写清为什么排在这儿），
    时长按他说的来（「大概一个小时」就是 60 分钟，别一律排 90 分钟）。
  ⛔ 不要跳过这张卡直接甩时间段——对"我随便、你看着办"的学生，先摊三个点等于把
  决定权又推回给他。⛔ 也不要只给一个点就替他定下来（「由 ai 帮我去挑选合适时间，
  **进行列举**……由我打勾」）。
  例外：**学生自己报了准点**（"周四下午两点到三点"）→ 用 propose_todo_tool
  出单条确认条就够。真要问他什么，只问"要加什么事"，别问"几点到几点"。
  ⛔ **不许拿他那半句话当代办名挂出去**（学生原话：「要识别啥才是真的事情，
  不是随便拿那一句话就去当代办加入日程了」）——判不出事名（只有时长、只有"帮我安排"）
  就只问"要安排什么事"，别出卡。"""


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
        # 顺序也就是学生该走的顺序：先问"你自己定还是我帮你挑"，再列 / 再出单条。
        "propose_todo_mode_tool": propose_todo_mode_tool_tool(),
        "propose_todo_slots_tool": propose_todo_slots_tool_tool(),
        "propose_todo_tool": propose_todo_tool_tool(),
    }
