"""时间规划模块（人话：学生个人库的"大脑助手"——照着周表找空、排待办、并跟学生商量）。

这个模块最关键的一条规矩写在 SYSTEM_PROMPT 里：

    **学生没点头之前，绝不许把待办写进去。**

流程是这样的：
  学生说"我要复习高数" → AI 调 find_free_slots 找空 → 给出几个候选并**问一句**
  → 学生说"第一个可以" → AI 才调 add_todo 真正落库

为什么非要多问这一句？因为时间安排是"要学生去执行"的事，
AI 自作主张塞进去，学生不认可就等于白排。
也正因为要来回商量，这个模块依赖会话历史（不然 AI 记不住自己上轮提议了什么）。
"""
import datetime
import json

from app.agent.tools import Tool
from app.store import (
    add_todo, delete_todo, get_timetable, list_todos,
    save_timetable, update_todo,
)

MODULE_KEY = "planner"

# 一天里可以用来安排待办的时间范围（太早太晚不适合打扰学生）
DAY_START = "07:00"
DAY_END = "22:00"

# 一天的星期几怎么对应周表的 day 字段
DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


# ---------- 时间小工具 ----------

def to_minutes(hhmm: str) -> int:
    """把 "08:30" 换成 510（分钟数），方便比较先后和算时长。"""
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return -1


def to_hhmm(minutes: int) -> str:
    """把 510 换回 "08:30"。"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _iso_weekday(date_str: str) -> int:
    """某天是周几，1=周一 … 7=周日（和周表 day 字段一致）。"""
    try:
        return datetime.date.fromisoformat(date_str).isoweekday()
    except Exception:
        return 0


def _weekday_name(date_str: str) -> str:
    return DAY_NAMES.get(_iso_weekday(date_str), "未知")


# ---------- 工具实现 ----------

def get_weekly_timetable() -> list:
    """查看周表全部课程（人话：先搞清楚这周哪些时间已经被课占了）。"""
    courses = get_timetable()
    if not courses:
        return []
    return sorted(courses, key=lambda c: (c.get("day", 9), c.get("start", "")))


def find_free_slots(date: str, min_minutes: int = 60) -> list:
    """查某一天的空档（人话：这天哪些时间段还空着，够塞一件事进去）。

    会同时避开**课程**和**已经排好的待办**，所以返回的一定是真空档。

    :param date: 日期，形如 "2026-09-25"
    :param min_minutes: 至少要空出多少分钟才值得推荐，默认 60
    """
    if _iso_weekday(date) == 0:
        return [{"error": f"{date} 不是合法日期，请用 2026-09-25 这种格式"}]

    wd = _iso_weekday(date)
    # 1) 收集这天已经被占掉的时间段
    busy = []
    for c in get_timetable():
        if int(c.get("day", 0)) == wd:
            busy.append((to_minutes(c.get("start", "00:00")),
                         to_minutes(c.get("end", "00:00")),
                         f"课程：{c.get('course', '')}"))
    for t in list_todos(date):
        if t.get("status") == "done":
            continue
        busy.append((to_minutes(t.get("start", "00:00")),
                     to_minutes(t.get("end", "00:00")),
                     f"待办：{t.get('title', '')}"))
    busy = [b for b in busy if b[0] >= 0 and b[1] > b[0]]
    busy.sort()

    # 2) 在 DAY_START ~ DAY_END 之间抠出空档
    cursor = to_minutes(DAY_START)
    limit = to_minutes(DAY_END)
    free = []
    for s, e, _label in busy:
        if s > cursor:
            free.append((cursor, min(s, limit)))
        cursor = max(cursor, e)
        if cursor >= limit:
            break
    if cursor < limit:
        free.append((cursor, limit))

    # 3) 只保留够长的空档
    result = []
    for s, e in free:
        if e - s >= min_minutes:
            result.append({
                "date": date,
                "weekday": _weekday_name(date),
                "start": to_hhmm(s),
                "end": to_hhmm(e),
                "minutes": e - s,
            })
    if not result:
        return [{"info": f"{date}（{_weekday_name(date)}）没有 ≥{min_minutes} 分钟的空档了，"
                         f"要么缩短时长，要么换一天。"}]
    return result


def list_day_todos(date: str) -> list:
    """列某天已安排的待办（人话：看看这天已经塞了些什么）。"""
    todos = list_todos(date)
    if not todos:
        return [{"info": f"{date}（{_weekday_name(date)}）目前没有安排待办"}]
    return [{
        "id": t["id"],
        "title": t["title"],
        "date": t["date"],
        "start": t["start"],
        "end": t["end"],
        "status": t["status"],
    } for t in todos]


def add_todo_tool(title: str, date: str, start: str, end: str, note: str = "") -> str:
    """把待办正式写进日程（人话：学生点头之后，才调用这个落库）。

    :param date: 日期 "2026-09-25"
    :param start / end: 起止时间 "14:00" / "15:30"
    """
    if _iso_weekday(date) == 0:
        return f"日期 {date} 不合法，请用 2026-09-25 这种格式。"
    s, e = to_minutes(start), to_minutes(end)
    if s < 0 or e < 0 or e <= s:
        return f"时间不合法：start={start} end={end}，要用 HH:MM 且结束晚于开始。"

    # 防撞课：跟课程重叠就直接拒绝，逼 AI 换个时间再来
    wd = _iso_weekday(date)
    for c in get_timetable():
        if int(c.get("day", 0)) != wd:
            continue
        cs, ce = to_minutes(c.get("start", "")), to_minutes(c.get("end", ""))
        if cs >= 0 and ce > cs and s < ce and e > cs:
            return (f"这段时间跟课程《{c.get('course', '')}》"
                    f"（{c.get('start')}-{c.get('end')}）撞了，"
                    f"请换一个不冲突的时间段，或先问学生愿不愿意调。")

    item = add_todo(title, date, start, end, note=note)
    return (f"已加入日程：{item['title']} · {date}（{_weekday_name(date)}）"
            f" {start}-{end}（编号 {item['id']}）")


def update_todo_status(todo_id: str, status: str) -> str:
    """改待办状态（人话：做完了标 done，还没做标 planned）。"""
    if status not in ("planned", "done"):
        return "status 只能是 planned 或 done"
    item = update_todo(todo_id, {"status": status})
    if not item:
        return f"找不到编号 {todo_id} 的待办"
    return f"已更新《{item['title']}》的状态为 {status}"


def remove_todo(todo_id: str) -> str:
    """删一条待办。"""
    if not delete_todo(todo_id):
        return f"找不到编号 {todo_id} 的待办"
    return f"已删除编号 {todo_id} 的待办"


def import_timetable(courses_json: str) -> str:
    """把整理好的课程写进周表（人话：学生传了课表文件后，AI 读完整理成这个格式存进来）。

    :param courses_json: JSON 字符串数组，每条形如
        {"day":1,"start":"08:00","end":"09:40","course":"高等数学","location":"教三301"}
        day 用 1=周一 … 7=周日
    """
    try:
        courses = json.loads(courses_json)
    except Exception as e:
        return f"没解析成功，需要合法的 JSON 数组：{e}"

    if not isinstance(courses, list) or not courses:
        return "内容为空或不是数组，请给出至少一门课。"

    cleaned, errors = [], []
    for i, c in enumerate(courses):
        if not isinstance(c, dict):
            errors.append(f"第 {i + 1} 条不是对象")
            continue
        try:
            day = int(c.get("day"))
        except (TypeError, ValueError):
            errors.append(f"第 {i + 1} 条 day 不是数字")
            continue
        if day not in range(1, 8):
            errors.append(f"第 {i + 1} 条 day={day} 越界（应 1-7，1 是周一）")
            continue
        s, e = to_minutes(str(c.get("start", ""))), to_minutes(str(c.get("end", "")))
        if s < 0 or e <= s:
            errors.append(f"第 {i + 1} 条时间不合法：{c.get('start')}-{c.get('end')}")
            continue
        cleaned.append({
            "day": day,
            "start": c.get("start"),
            "end": c.get("end"),
            "course": str(c.get("course", "")).strip() or "未命名课程",
            "location": str(c.get("location", "")).strip(),
        })

    if errors:
        return "有几条没通过校验，请修正后重新调用：\n" + "\n".join(errors[:8])

    save_timetable(cleaned)
    return f"周表已更新，共写入 {len(cleaned)} 门课，学生可以在「我的日程」里看到了。"


# ---------- 系统提示 ----------

def build_system_prompt() -> str:
    """动态拼接提示：把**今天的日期**告诉 AI。

    为什么必须注入日期？学生说的是"明天""下周三"这种相对时间，
    AI 不知道今天几号就根本算不出来。
    """
    today = datetime.date.today()
    return f"""你是校园时间管家的时间规划助手，负责帮学生把待办安排进真实的空档里。

【今天的日期】{today.isoformat()}（{DAY_NAMES.get(today.isoweekday(), "")}）
学生说的"今天/明天/后天/周几"，都要换算成上面的基准日期再动手。

【你的工作流程 —— 必须照做】
1. 学生提出一件要做的事（比如"我要复习高数"），先用 find_free_slots 查空档；
   如果学生没说哪天，就优先看今天和明后两天。
2. 给出 **2-3 个候选时间段**，说清楚每个的日期、星期、起止时间和时长。
3. **然后停下来问学生**："你觉得哪个合适？" —— 这一步绝不能省。
4. **只有学生明确同意之后**（比如"第一个可以""就这样""好"），
   才调用 add_todo 真正写进日程。学生没确认前，**绝对不要写入**。
5. 学生提出调整（"太晚了""换个时间"），就重新查空档再提议，继续问。

【硬性约束】
- 待办不能跟课程撞时间（工具会自动拦截，但你要先自己看清楚）。
- 一次只推 2-3 个候选，不要甩一长串让人挑花眼。
- 说话简短、具体，直接给时间点，不要长篇分析。
- 如果周表是空的（还没上传课表），先提醒学生去「我的日程」上传课表，
  否则无从判断什么时间空着。

【工具用法】
- get_weekly_timetable：看整周课程
- find_free_slots(date, min_minutes)：查某天空档
- list_day_todos(date)：看某天已排了什么
- add_todo_tool(title, date, start, end, note)：**确认后**才写入
- import_timetable(courses_json)：学生上传课表后，把课程整理成 JSON 存进周表
- update_todo_status / remove_todo：标记完成或删除
"""


def build_tools() -> dict[str, Tool]:
    """时间规划工具箱。"""
    return {
        "get_weekly_timetable": Tool(
            name="get_weekly_timetable",
            description="查看学生周表上的全部课程（哪天第几节、上什么、在哪）。排任何安排前都应该先看一眼。",
            parameters={"type": "object", "properties": {}},
            func=get_weekly_timetable,
        ),
        "find_free_slots": Tool(
            name="find_free_slots",
            description=(
                "查某一天的空档时间段，会同时避开课程和已排的待办。"
                "学生提出要做一件事时，用它来找出能安排的时间。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，格式如 2026-09-25"},
                    "min_minutes": {
                        "type": "integer",
                        "description": "至少要空出多少分钟才推荐，默认 60",
                    },
                },
                "required": ["date"],
            },
            func=find_free_slots,
        ),
        "list_day_todos": Tool(
            name="list_day_todos",
            description="列出某一天已经安排的待办事项。",
            parameters={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，格式如 2026-09-25"},
                },
                "required": ["date"],
            },
            func=list_day_todos,
        ),
        "add_todo_tool": Tool(
            name="add_todo_tool",
            description=(
                "把待办正式写进日程。**必须等学生明确确认之后才能调用**——"
                "学生只是提出想法、还在商量阶段时不要调用。"
                "如果时间跟课程冲突，工具会拒绝并说明原因。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待办标题，如 复习高等数学第三章"},
                    "date": {"type": "string", "description": "日期，格式如 2026-09-25"},
                    "start": {"type": "string", "description": "开始时间，如 14:00"},
                    "end": {"type": "string", "description": "结束时间，如 15:30"},
                    "note": {"type": "string", "description": "可选备注"},
                },
                "required": ["title", "date", "start", "end"],
            },
            func=add_todo_tool,
        ),
        "import_timetable": Tool(
            name="import_timetable",
            description=(
                "把学生上传的课表整理成结构化数据后写入周表（覆盖式）。"
                "入参是 JSON 字符串数组，每条含 day(1=周一..7=周日)、"
                "start、end、course、location。写完学生就能在「我的日程」里看到课表了。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "courses_json": {
                        "type": "string",
                        "description": 'JSON 数组字符串，如 [{"day":1,"start":"08:00","end":"09:40","course":"高等数学","location":"教三301"}]',
                    }
                },
                "required": ["courses_json"],
            },
            func=import_timetable,
        ),
        "update_todo_status": Tool(
            name="update_todo_status",
            description="把某条待办标记为已完成(done)或未完成(planned)。",
            parameters={
                "type": "object",
                "properties": {
                    "todo_id": {"type": "string", "description": "待办编号"},
                    "status": {"type": "string", "description": "planned 或 done"},
                },
                "required": ["todo_id", "status"],
            },
            func=update_todo_status,
        ),
        "remove_todo": Tool(
            name="remove_todo",
            description="删除一条待办。",
            parameters={
                "type": "object",
                "properties": {"todo_id": {"type": "string", "description": "待办编号"}},
                "required": ["todo_id"],
            },
            func=remove_todo,
        ),
    }
