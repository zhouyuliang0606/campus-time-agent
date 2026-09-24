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
import re

from app.agent.tools import Tool
from app.store import (
    add_todo, delete_todo, get_timetable, list_todos,
    save_timetable, update_todo,
)
from app.modules.student_persona import mock_phrase

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


def propose_slots(options_json: str) -> str:
    """把候选时间段以结构化方式交给前端，渲染成可勾选的卡片（而不是挤在一段话里）。

    什么时候调：助手已经用 find_free_slots 查好空档、准备问学生选哪个时调它。
    前端会把这些候选显示成卡片，学生点一下就确认，助手才真正写入待办。
    （引擎会捕获这个工具的参数，作为响应里的 options 发给前端。）
    """
    try:
        opts = json.loads(options_json)
    except Exception as e:
        return f"候选格式不对：{e}"
    if not isinstance(opts, list) or not opts:
        return "没有候选可展示"
    return f"已向前端展示 {len(opts)} 个候选时间段，等学生确认。"


# ---------- 无密钥时的「确定性规划助手」（让排时间这套交互离线也能演示） ----------
#
# 没有配置大模型密钥时，真模型用不了，但学生端「提议候选 → 学生勾选 → 写入」这套
# 交互又很想让评委看到。于是这里写一个轻量的「脚本助手」：只处理规划模块最常见的
# 两种意图（提出一件要做的事 / 确认某个时间），直接调下面的纯函数工具，不碰网络。
# 有密钥时走真模型，这个不会被用到。

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})-(\d{2}:\d{2})")
# 从学生话里抠出"要做的事"当待办标题：去掉确认词、去掉日期时间。
# 注意：这里**不能**放 ":" / "："——它们会把时间里的冒号（07:00）也吃掉，
# 导致下面的日期正则匹配不上、整串日期时间漏进标题。冒号分隔符在 _guess_title 末尾单独清理。
_CONFIRM_WORDS = ("确认安排", "确认", "选这个", "就用这个", "这个", "安排", "✅")


def _guess_title(msg: str) -> str:
    t = msg
    for w in _CONFIRM_WORDS:
        t = t.replace(w, " ")
    # 先去掉「日期 起-止」（形如 2026-09-24 14:00-15:30）。必须在清理冒号之前做，
    # 否则时间里的冒号被当分隔符吃掉后，这段就再也匹配不上了。
    t = _DATE_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" -·")
    # 去掉口语前缀，标题更干净
    t = re.sub(r"^(我要|我想|帮我|我想让|请帮我|给我)\s*", "", t)
    # 去掉开头可能残留的冒号 / 全角冒号等分隔符（确认词后面的「：」）
    t = re.sub(r"^[\s：:·\-]+", "", t)
    return t or "待办"


def mock_planner(message: str, history: list | None = None, persona_key: str = None) -> dict:
    """无密钥时处理规划对话（人话：一个会查空档、会给候选、会等你确认的极简助手）。

    返回 {answer, options, awaiting_choice}：
      - options 非空且 awaiting_choice=True 时，前端把候选渲染成可勾选卡片；
      - 学生点卡片确认后，前端把"日期 起-止"塞进消息发回来，这里识别到就真的写入。
    """
    msg = (message or "").strip()

    # 1) 课表修改/导入的请求：无密钥模式下管家没法真的读文件/构提案，明确说明而不是乱给候选。
    #    （真模型会走 read_uploaded_file / get_weekly_timetable → propose_timetable_change 出确认卡）
    if "课表" in msg and any(k in msg for k in (
            "导入", "上传", "写进", "整理", "import_timetable",
            "删除", "删掉", "去掉", "修改", "换课", "加一门")):
        return {
            "answer": "导入课表需要我真的读到你上传的文件内容，这要用到大模型——目前还没配置密钥。"
                      "请进管理控制台（/admin）的「API 配置」填好 Key 再传一次；"
                      "也可以先到「我的日程」里手动安排。",
            "options": [], "awaiting_choice": False,
        }

    # 2) 周表是空的：先让学生去上传，否则无从判断空档
    if not get_timetable():
        return {"answer": mock_phrase(persona_key, "need_upload"), "options": [], "awaiting_choice": False}

    # 3) 消息里带了「日期 起-止」→ 这是学生在点选项卡片确认，真正写入
    matches = _DATE_RE.findall(msg)
    if matches:
        results = []
        for (date, start, end) in matches:
            title = _guess_title(msg)
            results.append(add_todo_tool(title, date, start, end))
        if any("已加入日程" in r for r in results):
            head = mock_phrase(persona_key, "confirm")
            return {"answer": head + "\n" + "\n".join(results), "options": [], "awaiting_choice": False}
        # 都没写入（比如撞课），把原因原样告诉学生，让他换时间
        return {"answer": "\n".join(results), "options": [], "awaiting_choice": False}

    # 4) 学生提出一件要做的事 → 查近三天空档，给最多 3 个候选
    base = datetime.date.today()
    days = [(base + datetime.timedelta(days=off)).isoformat() for off in (0, 1, 2)]
    opts = []
    for d in days:
        for s in find_free_slots(d, 60):
            if "error" in s or "info" in s:
                continue
            opts.append({
                "id": f"{d}-{s['start']}",
                "title": _guess_title(msg),
                "date": d,
                "weekday": s["weekday"],
                "start": s["start"],
                "end": s["end"],
                "minutes": s["minutes"],
            })
            if len(opts) >= 3:
                break
        if len(opts) >= 3:
            break

    if not opts:
        return {"answer": mock_phrase(persona_key, "empty"), "options": [], "awaiting_choice": False}

    lines = "\n".join(
        f"{i + 1}. {o['date']}（{o['weekday']}）{o['start']}-{o['end']}（{o['minutes']} 分钟）"
        for i, o in enumerate(opts)
    )
    answer = mock_phrase(persona_key, "propose") + "\n" + lines + "\n点一下你想要的时间就行～"
    return {"answer": answer, "options": opts, "awaiting_choice": True}


def validate_courses(courses: list) -> tuple[list, list]:
    """校验并清洗课程列表（人话：day/时间不合法的挑出来，合格的整成规范格式）。

    :returns: (cleaned, errors) —— cleaned 是规范化的课程列表，errors 是逐条错误说明
    """
    cleaned, errors = [], []
    for i, c in enumerate(courses):
        if not isinstance(c, dict):
            errors.append(f"第 {i + 1} 条不是对象")
            continue
        # day 既接受 1-7，也接受"周一"这种写法（模型爱这么写，旧数据里也可能有）
        day = _coerce_day(c.get("day"))
        if day is None:
            errors.append(f"第 {i + 1} 条 day 没看懂（1=周一 … 7=周日或 周一/周三）")
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
    return cleaned, errors


def import_timetable(courses_json: str) -> str:
    """把整理好的课程写进周表（**仅供测试与内部调用**——AI 不再直接持有这个写入工具，
    修改/导入课表一律走 propose_timetable_change 提案 → 学生点确认卡 → 系统 apply）。

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

    cleaned, errors = validate_courses(courses)
    if errors:
        return "有几条没通过校验，请修正后重新调用：\n" + "\n".join(errors[:8])

    save_timetable(cleaned)
    return f"周表已更新，共写入 {len(cleaned)} 门课，学生可以在「我的日程」里看到了。"


def propose_timetable_change(courses_json: str, change_summary: str = "") -> str:
    """把调整/导入后的**完整课表**作为提案交给前端，渲染成「确认修改」卡片。

    **这是 AI 修改课表的唯一途径**——本工具不写任何数据；学生点卡片上的
    「确认修改」后，由前端直接调 /api/timetable/apply 确定性写入。
    这样写入权不在模型手里：模型忘了调工具、重构 JSON 出错、或者嘴上说
    "已删除"都不会再造成"说了没做"或"误写"的事故。

    :param courses_json: JSON 数组字符串，调整后的**完整**课表（含未改动的课程），
        每条含 day(1=周一..7=周日)、start、end、course、location
    :param change_summary: 一句话说明改了什么，会显示在确认卡上
    """
    try:
        courses = json.loads(courses_json)
    except Exception as e:
        return f"提案格式不对，需要合法的 JSON 数组：{e}"
    if not isinstance(courses, list) or not courses:
        return "提案为空或不是数组，至少要有一门课。"

    cleaned, errors = validate_courses(courses)
    if errors:
        return "提案里有几条没通过校验，请修正后重新调用：\n" + "\n".join(errors[:8])

    return (f"已把课表修改提案交给前端（共 {len(cleaned)} 门课）"
            + (f"：{change_summary}" if change_summary else "")
            + "。学生会在界面上看到「确认修改」卡片，点击后由系统执行写入。"
              "在学生点确认之前，绝不要声称已经修改/删除完成。")


def _name_match(a: str, b: str) -> bool:
    """课程名模糊匹配（人话：模型说的课名和学生表里的写法未必一字不差）。

    三层匹配：完全相等 / 连续子串 / 子序列缩写（"高数"→"高等数学"、"英语"→"大学英语"）。
    """
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    it = iter(long)
    return all(ch in it for ch in short)


def propose_course_change(op: str, day: int, course: str = "", start: str = "",
                          new_start: str = "", new_end: str = "",
                          new_course: str = "", new_location: str = "",
                          change_summary: str = "") -> str:
    """单课程级课表提案（删除/新增/修改），**新课表由服务端基于当前周表算好**。

    为什么不让模型自己拼整表 JSON？实测它偶尔会重构错/漏课程，或者干脆只回文字不出卡。
    这里模型只需要说清「对哪天的哪节课做什么」，新表由服务端算——模型想错都难。
    结果是一个带 __proposal__ 的 JSON，引擎捕获后交给前端出确认卡；
    学生点确认后由前端调 /api/timetable/apply 写入，写入不经过模型。

    :param op: "remove"（删课）/ "add"（加课）/ "update"（改某节课的时间或信息）
    :param day: 1=周一 … 7=周日
    :param course: 目标课程名（remove/update 用，模糊匹配）
    :param start: 目标课程的开始时间（可选，同一天同名多节时用来区分）
    :param new_start/new_end/new_course/new_location: update 的新值（add 用 new_start 当 start）
    :param change_summary: 一句话说明改了什么，会显示在确认卡上

    为什么提案里除了 courses 还要带一个 action？
        同一轮可能出多张卡（比如"周二早上的课都去掉"出了两张），
        每张卡存的又是"原始周表减掉自己那处改动"。要是直接按卡片顺序覆盖着写，
        后一张会把前一张的改动盖回去。带上 action 就能按**当前**周表重算，
        一张一张往前叠，谁也抹不掉谁。
    """
    if op not in ("remove", "add", "update"):
        return f"op 只能是 remove/add/update，收到的是 {op}"
    # 容错：模型常把 day 写成"周一"/"星期三"，这里顺手认下来，别为格式卡住整件事
    day = _coerce_day(day)
    if day is None:
        return "day 没看懂（1=周一 … 7=周日，写中文星期也行），请核对后重试"
    if day not in range(1, 8):
        return f"day={day} 越界（应 1-7，1 是周一）"
    if not course and op != "add":
        return "删课/改课要说明是哪门课（course）"

    cur = list(get_timetable())

    if op == "remove":
        matches = [c for c in cur if int(c.get("day", 0)) == day and _name_match(c.get("course"), course)]
        if start:
            matches = [m for m in matches if m.get("start") == start]
        if not matches:
            day_courses = [f"{c.get('start')}-{c.get('end')} {c.get('course')}"
                           for c in cur if int(c.get("day", 0)) == day]
            hint = "；".join(day_courses) or "这天没有课"
            return (f"周表里没找到{_weekday_name_by_num(day)}的《{course}》。"
                    f"这天现存的课：{hint}。请核对后重试，或先问学生想删哪节。")
        if len(matches) > 1:
            lst = "；".join(f"{m.get('start')}-{m.get('end')} {m.get('course')}" for m in matches)
            return f"{_weekday_name_by_num(day)}有 {len(matches)} 节同名课：{lst}。请带上 start 参数区分。"
        target = matches[0]
        new_courses = [dict(c) for c in cur if c is not target]
        summary = change_summary or f"删除{_weekday_name_by_num(day)} {target.get('start')} 的《{target.get('course')}》"

    elif op == "add":
        s, e = to_minutes(new_start or start), to_minutes(new_end)
        if not course:
            return "加课需要提供课程名（course）"
        if s < 0 or e <= s:
            return f"时间不合法：start={new_start or start} end={new_end}，要用 HH:MM 且结束晚于开始"
        for c in cur:
            if int(c.get("day", 0)) != day:
                continue
            cs, ce = to_minutes(c.get("start", "")), to_minutes(c.get("end", ""))
            if cs >= 0 and ce > cs and s < ce and e > cs:
                return (f"新课时间和{_weekday_name_by_num(day)} {c.get('start')}-{c.get('end')} "
                        f"的《{c.get('course')}》撞了。请换时间，或先删掉那节再加。")
        new_courses = cur + [{"day": day, "start": to_hhmm(s), "end": to_hhmm(e),
                              "course": course.strip(), "location": (new_location or "").strip()}]
        summary = change_summary or (f"新增{_weekday_name_by_num(day)} {to_hhmm(s)}-{to_hhmm(e)} 《{course}》"
                                     + (f" @{new_location}" if new_location else ""))

    else:  # update
        matches = [c for c in cur if int(c.get("day", 0)) == day and _name_match(c.get("course"), course)]
        if start:
            matches = [m for m in matches if m.get("start") == start]
        if not matches:
            return f"周表里没找到{_weekday_name_by_num(day)}的《{course}》，请核对后重试"
        if len(matches) > 1:
            lst = "；".join(f"{m.get('start')}-{m.get('end')}" for m in matches)
            return f"{_weekday_name_by_num(day)}有 {len(matches)} 节同名课：{lst}。请带上 start 参数区分。"
        target = matches[0]
        merged = dict(target)
        if new_start:
            merged["start"] = new_start
        if new_end:
            merged["end"] = new_end
        if new_course:
            merged["course"] = new_course.strip()
        if new_location:
            merged["location"] = new_location.strip()
        s, e = to_minutes(merged["start"]), to_minutes(merged["end"])
        if s < 0 or e <= s:
            return f"改完的时间不合法：{merged.get('start')}-{merged.get('end')}"
        merged["start"], merged["end"] = to_hhmm(s), to_hhmm(e)
        new_courses = [merged if c is target else dict(c) for c in cur]
        summary = change_summary or (f"修改{_weekday_name_by_num(day)}《{target.get('course')}》→ "
                                     f"{merged['start']}-{merged['end']} {merged['course']}")

    cleaned, errors = validate_courses(new_courses)
    if errors:
        return "算出的新课表没通过校验（这不该发生，请原样反馈）：\n" + "\n".join(errors[:5])

    return json.dumps({
        "__proposal__": {
            "kind": "timetable_change",
            "summary": summary,
            "courses": cleaned,
            # action：把"对哪节课做什么"原样留下来，落库时按当前周表重算（多张卡叠加用得上）
            "action": {"op": op, "day": day, "course": course, "start": start,
                       "new_start": new_start, "new_end": new_end,
                       "new_course": new_course, "new_location": new_location},
        }
    }, ensure_ascii=False)


# 「学生要清空整张课表」的措辞。整表清空是不可逆操作，判定宁可保守也别误伤：
#     "把周一的高数删掉"绝不能被判成清空 → 所以必须有"全部/所有/清空/一门不留"这类
#     整表信号，且话里得点明是课表或课程，才认。
# 自带宾语（说这句就等于在说课表），见到就认
_CLEAR_SELF = (
    "清空课表", "课表清空", "清空整个", "整个课表", "全部课程", "所有课程",
    "全部课表", "课表全部", "全删课表", "课表全删", "一门不留", "一门都不留",
    "删除课表", "课表删除", "删光", "课表删掉",
)
# 不带宾语（"全部删除""都删掉"），必须话里真点了课表/课程才认，
# 否则"我的待办全部删除"会被误判成清课表
_CLEAR_NEED_OBJ = ("全部删除", "全部删掉", "全删掉", "都删掉", "都删除")


# ---------- 「往日程里加一件事」的确定性判定 ----------
# 跟上面清空课表是同一个思路：能把时间算出来的事，别交给路由和模型碰运气。
#
# 血泪背景（实测复现）：学生说"帮我把今天的『复习线性代数』安排到 19:00 到 20:30"，
# 后端路由时而定到 planner、时而定到 schedule、时而定到 admin——
# 只有 planner 手里有写待办的工具，于是同一句话时有时会写、有时只回一段空档分析；
# 学生再回一句"确认"，路由把它甩到 admin，收到的答复是
# "你回「确认」了，但我这边还没生成待办提案"——整条链路当场断死。
# 学生眼里的世界就是：客服说已经加上了，日程里什么都没有，刷新也没用。
#
# 所以这里把"学生说了什么 + 时间落在哪"都由系统算死：
# 能算出日期和起始时刻 → 系统直接出一张确认卡，点卡片或回一句"确认"才真写；
# 算不出（比如学生压根没说哪天几点）→ 才交回 planner 出候选时段。
_ADD_INTENT = (
    "安排", "排一下", "排进", "排到", "加入", "加进", "添加", "加上",
    "加一个", "记一下", "记进", "塞进", "预约",
)
# 括号里框出来的事，多半就是标题本身（学生习惯用 『』「」把任务名框起来）
_QUOTE_RE = re.compile(r"[『「\"“]([^」』\"”]{1,40})[」』\"”]")
_WEEKDAY_RE = re.compile(r"周([一二三四五六日天])")


def _cn_num(s: str) -> int | None:
    """中文数字转阿拉伯数字（人话：'两'→2、'十二'→12、'十'=10、'二十三'=23）。"""
    if not s:
        return None
    digit = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" in s:
        hi, _, lo = s.partition("十")
        h = digit.get(hi, 1) if hi else 1      # "十" 开头 → 十几
        l = digit.get(lo, 0) if lo else 0
        return h * 10 + l
    return digit.get(s)


def _normalize_clock(t: str) -> str:
    """把中文报时换成数字钟（人话：'两点'→2:00、'两点半'→2:30）。

    为什么要有它：学生说时间十有八九是「下午两点到三点」这种口语，
    原来的正则只认阿拉伯数字，「两点」在它眼里就是没有时间——
    于是整句话掉回大模型，模型顺嘴一句"搞定，已经写进待办啦"，其实什么都没写。
    """
    def rep(m):
        h = _cn_num(m.group(1))
        if h is None or h > 23:
            return m.group(0)
        return f"{h}:30" if m.group(2) else f"{h}:00"

    t = re.sub(r"([零一二两三四五六七八九十]{1,3})点(半)?", rep, t)
    # '3点20' 这种"数字点+分钟"也顺手归一成 3:20
    def rep2(m):
        h, mm = int(m.group(1)), int(m.group(2))
        if h > 23 or mm > 59:
            return m.group(0)
        return f"{h}:{mm:02d}"
    return re.sub(r"(\d{1,2})\s*点\s*(\d{1,2})\s*分?", rep2, t)


def _pm_fix(text: str, hour: int | None) -> int | None:
    """按上下文把 12 小时制拨成 24 小时制（人话：'下午2点'→14，'早上8点'还是 8）。"""
    if hour is None or hour > 12:
        return hour
    if re.search(r"下午|午后|傍晚|晚上|夜里|晚间", text or ""):
        return hour + 12 if hour < 12 else hour
    return hour


def _pick_span(text: str):
    """从一句话里抠出「起-止」时间（人话：认得 19:00-20:30 / 19点到20点 /
    两点到三点 / 下午2点到3点）。

    返回 (start, end)，只给了一个时刻时 end 为 None。
    """
    t = _normalize_clock(text or "")
    m = re.search(r"(\d{1,2}):(\d{2})\s*(?:到|至|-|~|～)\s*(\d{1,2}):(\d{2})", t)
    if m:
        sh = _pm_fix(t, int(m.group(1)))
        eh = _pm_fix(t, int(m.group(3)))
        # 止比起还早 = 跨过了中午（"12点到1点"其实是 12:00-13:00）。
        # 注意必须是严格小于：写成 <= 的话 "7:00-7:40" 会被拨成 19:40。
        if eh is not None and sh is not None and eh < sh and eh <= 11:
            eh += 12
        return (f"{sh:02d}:{m.group(2)}", f"{eh:02d}:{m.group(4)}")
    m = re.search(r"(\d{1,2})\s*点\s*(?:到|至|-|~|～)\s*(\d{1,2})\s*点?", t)
    if m:
        sh = _pm_fix(t, int(m.group(1)))
        eh = _pm_fix(t, int(m.group(2)))
        if eh < sh and eh <= 11:
            eh += 12
        return f"{sh:02d}:00", f"{eh:02d}:00"
    m = re.search(r"(\d{1,2}):(\d{2})", t)
    if m:
        sh = _pm_fix(t, int(m.group(1)))
        return f"{sh:02d}:{m.group(2)}", None
    return None, None


def _pick_date(text: str):
    """从一句话里抠出日期（人话：2026-09-24 / 今天 / 明天 / 后天 / 周一）。"""
    t = text or ""
    m = _DATE_RE.search(t)
    if m:
        return m.group(1)
    today = datetime.date.today()
    for word, off in (("大后天", 3), ("后天", 2), ("明天", 1), ("今晚", 0), ("今夜", 0), ("今天", 0)):
        if word in t:
            return (today + datetime.timedelta(days=off)).isoformat()
    m = _WEEKDAY_RE.search(t)
    if m:
        ch = "日" if m.group(1) == "天" else m.group(1)
        want = "一二三四五六日".index(ch) + 1
        base = datetime.date.today()
        # 今天说的"周一"，指下一个周一（今天就是周一的话也算，往后推满一周更符合直觉）
        return (base + datetime.timedelta(days=(want - base.isoweekday()) % 7 or 7)).isoformat()
    return None


def _pick_title(text: str) -> str:
    """从一句话里剩下那部分抠出待办标题（人话：把时间、日期、安排这类废话都扔掉）。"""
    t = text or ""
    m = _QUOTE_RE.search(t)          # 优先拿『』「」框着的那段，最准
    if m and 1 <= len(m.group(1)) <= 30:
        return m.group(1).strip()
    t = _normalize_clock(t)          # 先把「两点到三点」换成 2:00-3:00，下面的正则才擦得掉
    t = _DATE_RE.sub(" ", t)
    t = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}(?:\s*(?:到|至|-|~|～)\s*\d{1,2}\s*[:：]?\s*\d{2})?", " ", t)
    t = re.sub(r"\d{1,2}\s*点到\s*\d{1,2}\s*点?", " ", t)
    for w in ("大后天", "后天", "今天", "今晚", "今夜", "明天", "上午", "下午",
              "晚上", "中午", "早上", "夜里", "周末"):
        t = t.replace(w, " ")
    # 星期连着时间修饰一起擦（"下周二/这周四"整块都是时间，不是标题的一部分）。
    # 注意顺序：必须整块匹配——先削"下周"再削"周二"，会剩下一个"二"粘进标题里。
    t = re.sub(r"(下|本|这|上)?周[一二三四五六日天]", " ", t)
    for w in ("下周", "本周", "这周", "上周", "下个", "这个"):
        t = t.replace(w, " ")
    t = re.sub(r"^(我要|我想|帮我|我想让|请帮我|给我|把|帮)\s*", "", t)
    for w in _CONFIRM_WORDS:
        t = t.replace(w, " ")
    for w in _ADD_INTENT + ("的安排", "一下", "一个", "里", "在"):
        t = t.replace(w, " ")
    t = re.sub(r"[，,。！!？?、；;：:~～\-—『』「」\"'“”‘’]", " ", t)
    t = re.sub(r"\s+", "", t).strip()
    return t.strip("的")[:30] or "待办"


def wants_add_todo(text: str) -> bool:
    """判断一句话算不算「往日程里加一件事」（人话：确定性分支用的开关）。

    只认短促的下单句。管它路由落到哪个模块、模型想说什么，系统都按这条链路走。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:      # 太长的多半是在聊天，不是在下单
        return False
    # ⚠️ 下面这几条"让路"必须排在"有意图词就放行"前面。
    # 否则"安排"这个词会把课表请求、学习计划请求一起拽过来——
    # 先把不属于待办的交出去，再谈接住。
    #
    # 课表类的话由课表那条路管，别抢（"周一加一节体育"要进的是周表，不是待办）
    if "课表" in t or "课程" in t:
        return False
    if any(w in t for w in ("加课", "加一门课", "加一节", "加门课", "加节课")):
        return False
    # "帮我安排这周的学习计划"是 schedule 模块的看家本领（找空档排计划），
    # 不是往日程里塞一条待办。判据：说了"计划/规划"又没给具体钟点 → 让给 schedule。
    if ("计划" in t or "规划" in t) and _pick_span(t)[0] is None:
        return False
    # 学生明说了要加一件事（"帮我安排游泳""记一下交电费"），就算他没提日期时刻，
    # 系统也得接住——由确定性追问分支问他"哪天几点"，绝不能丢给模型。
    # （原来这里最后还要过一遍"日期/时刻有一个算得出"才放行，
    #   结果"帮我安排游泳"这种只有意图、没有时间的短句全漏给了模型，
    #   模型回一句"我已经帮你排啦"，日程里空空如也。）
    if any(w in t for w in _ADD_INTENT):
        return True
    # 没有意图词，但话里自带"日期/星期 + 起止时间"的也算下单——
    # （比如"确认 2026-09-24 19:00-20:30 背单词"，前端点候选卡回发的是这副模样；
    #   "今天19:00-20:30 复习线性代数"、"周五下午3点20写作业"这种不带安排字样的也该接住）。
    # 带疑问词的不算——那多半是在问课表，不是在下单。
    return bool(_pick_date(t) and _pick_span(t)[0]
                and not any(k in t for k in ("吗", "？", "?")))


def parse_add_todo(text: str) -> dict | None:
    """把一句话解析成一张待办确认卡（人话：系统自己算时间，模型一个字节都不用操心）。

    日期和起始时刻有一个算不出来就返回 None——这种情况学生八成没说清，
    交给 planner 出候选时段让他挑，比硬凑一个时间靠谱。

    返回的 dict 只是**提案**，本函数不写库；学生点头后才由系统真正落库。
    """
    t = (text or "").strip()
    date = _pick_date(t)
    start, end = _pick_span(t)
    if not date or not start:
        return None
    if not end:                    # 只给了开始时间，默认排 1 小时
        end = to_hhmm(to_minutes(start) + 60)
    title = _pick_title(t)
    return {
        "kind": "todo_add",
        "title": title,
        "date": date,
        "start": start,
        "end": end,
        "weekday": _weekday_name(date),
        "minutes": to_minutes(end) - to_minutes(start),
        "summary": f"{title}｜{date}（{_weekday_name(date)}）{start}-{end}",
    }


def pick_todo_missing(text: str) -> str:
    """解析失败时说清到底缺哪块（人话：追问要问到点上，别让学生再猜）。

    返回 "时间" / "日期" / "时间和日期"，给确定性追问分支拼话术用。
    """
    t = text or ""
    has_date = _pick_date(t) is not None
    has_time = _pick_span(t)[0] is not None
    if has_date and not has_time:
        return "时间"
    if has_time and not has_date:
        return "日期"
    return "时间和日期"


def propose_todo_tool(title: str, when: str = "", date: str = "",
                      start: str = "", end: str = "") -> str:
    """给「课表时间规划」模块用的**只读**提案工具（人话：把建议变成一张能点的确认条）。

    为什么这个模块需要它：学生问"哪天有空复习高数"，Agent 找完空档会给出建议，
    可它手里一个写入工具都没有——于是它就在**文字里**自己写一句
    「好，那我按这个出个提案：- 任务：健身 - 时间：周一 16:30~18:00」，
    学生回「可以」之后，界面上**连个【确认】按钮都没有**（截图里就是这个）。
    给它一张"纸"：提案由系统生成，学生点了按钮，后端才写库。

    参数都能吃学生口语：when 传「周一 16:30~18:00」「明晚七点到八点」都行。
    解析不出来就返回带 hint 的 JSON，让模型补参数重试——绝不瞎凑一个时间。
    """
    blob = " ".join(x for x in (date, when, start, end) if x)
    day = _pick_date(blob) or _pick_date(f"{date} {when}")
    begin, finish = _pick_span(blob)
    if not begin and start:
        begin, _ = _pick_span(start)
    if not finish and end:
        _, finish = _pick_span(end)
    name = (title or "").strip().strip("\"'“”‘’『』「」")  or "待办"
    if not day or not begin:
        return json.dumps({
            "error": "日期或开始时间没解析出来",
            "hint": ("date 传 '2026-09-28' 或 '周一'/'明天'；"
                     "when 传 '16:30-18:00'（学生说的『下午两点到三点』也认）。"
                     "两个都给全了才会出确认条。"),
            "got": blob,
        }, ensure_ascii=False)
    if not finish:
        finish = to_hhmm(to_minutes(begin) + 60)
    prop = {
        "kind": "todo_add",
        "title": name,
        "date": day,
        "start": begin,
        "end": finish,
        "weekday": _weekday_name(day),
        "minutes": to_minutes(finish) - to_minutes(begin),
        "summary": f"{name}｜{day}（{_weekday_name(day)}）{begin}-{finish}",
    }
    return json.dumps({
        "__proposal__": prop,
        "human": (f"确认条已经挂在下面了：《{name}》{day}（{_weekday_name(day)}）"
                  f"{begin}-{finish}。点【确认加入】才会写进日程；"
                  f"你只要说一句『你可以点确认条上的按钮，或直接回确认』，"
                  f"**禁止说已经写好了**。"),
    }, ensure_ascii=False)


# 管家自己那句"提案"长这样（截图里学生遇到的那种）：
#   好，那我按这个出个提案：
#   - 任务：健身
#   - 时间：周一 16:30~18:00
#   - 范围：本周
#   提案这就发给你，点一下【确认】就入库了。
_REPLY_TASK_RE = re.compile(r"(?:任务|事项|标题|安排)\s*[：:]\s*([^\n，,。；;｜|]{1,30})")
_REPLY_TIME_RE = re.compile(r"(?:时间|时段|几点)\s*[：:]\s*([^\n。；;｜|]{1,40})")


def parse_todo_from_reply(text: str) -> dict | None:
    """从**管家自己那句话**里把「任务 + 时间」捞出来（人话：它只说了没挂条，系统替它挂）。

    什么时候用：学生回「可以」/「确认」，但暂存里什么都没有——
    因为管家上一条只是把方案写在文字里，压根没调工具。
    照「确认落空捞回学生原话」的思路，这一次是捞**管家给的方案**：
    按固定格式（"任务/事项/标题：" + "时间/时段："）把内容还原成一张待办提案。

    格式对不上就返回 None：宁可不出，也别从闲聊里瞎猜出一个待办。
    """
    t = text or ""
    m_task = _REPLY_TASK_RE.search(t)
    m_time = _REPLY_TIME_RE.search(t)
    if not m_task or not m_time:
        return None
    when = m_time.group(1)
    day = _pick_date(when) or _pick_date(t)
    begin, finish = _pick_span(when)
    if not day or not begin:
        return None
    if not finish:
        finish = to_hhmm(to_minutes(begin) + 60)
    name = m_task.group(1).strip().strip("\"'“”‘’『』「」") or "待办"
    return {
        "kind": "todo_add",
        "title": name,
        "date": day,
        "start": begin,
        "end": finish,
        "weekday": _weekday_name(day),
        "minutes": to_minutes(finish) - to_minutes(begin),
        "summary": f"{name}｜{day}（{_weekday_name(day)}）{begin}-{finish}",
    }


def is_add_todo_followup(text: str) -> bool:
    """判断一句"补充"（人话：系统刚问过"几点到几点"，学生答"下午两点到三点"）。

    这种句子只有时间、没有日期，按 wants_add_todo 的规矩不算完整下单——
    可它是对追问的回答，必须接着算。不认它，学生答完就掉回大模型，
    模型又是一句"搞定"（什么都没写）。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    return _pick_span(t)[0] is not None and _pick_date(t) is None


def wants_add_course(text: str) -> bool:
    """判断一句话算不算「往周表里加一门课」（人话：确定性分支用的开关）。

    为什么也要由系统接管：配了大模型密钥之后，学生其实一直在跟真模型对话，
    而模型是会"嘴上说已经加了、其实没动手"的——学生看到的回复是"已经加上了"，
    周表里干干净净。加课这件事学生说得很清楚（哪天、几点、什么课），
    剩下"新课表长什么样"本来就该服务端算，没道理交给模型临场发挥。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    has_intent = any(w in t for w in ("加课", "加一门课", "加一门", "加节课",
                                      "加一节", "加门课", "加课程"))
    # 没说"加课"这两个字的话，必须同时点明课表/课程、带上"加"和星期+时间
    if not has_intent and not ("加" in t and ("课表" in t or "课程" in t)):
        return False
    day, begin = _pick_day(t), _pick_span(t)[0]
    if day is not None and begin:
        return True
    # 有课名、有时钟、就是没说星期。这种情况也算"想加课"，
    # 但要让**系统**去问清楚——交给模型的话，它多半回一句"已经加上了"。
    return bool(begin) and bool(_pick_course_name(t))


def parse_add_course(text: str) -> dict | None:
    """把"周一加一节体育，体育馆，19:00 到 20:40"解析成一张加课确认卡。

    返回的是**提案**（含算好的新课表），本函数一个字节都不写库；
    学生点卡片上的确认、或回一句"确认"，才由系统真正落库。
    """
    t = (text or "").strip()
    day = _pick_day(t)
    start, end = _pick_span(t)
    if day is None or not start:
        # 没说星期就只能算到一半——与其让模型自作主张（它最爱说"已经加上了"），
        # 不如让解析函数如实返回 None，由上层问清楚学生到底是哪天。
        return None
    if not end:
        end = to_hhmm(to_minutes(start) + 90)
    course = _pick_course_name(t)
    if not course:
        return None
    location = _pick_location(t)
    raw = propose_course_change(
        "add", day, course, start,
        new_start=start, new_end=end, new_location=location,
    )
    try:
        proposal = (json.loads(raw) or {}).get("__proposal__")
    except Exception:
        proposal = None
    if not isinstance(proposal, dict):
        return None
    proposal.setdefault("kind", "timetable_change")
    return proposal


# ---- 删课：确定性解析（跟加课同一个思路：能由代码定死的，就别交给模型）----
# "第N节"→ 上课时间。演示课表按两节课一个时段算：
# 第1-2节 08:00、第3-4节 10:00、第5-6节 14:00、第7-8节 16:00；晚上的课学生会直接说时间。
_PERIOD_RE = re.compile(r"第\s*([一二三四五六七八1-8])\s*节")
_PERIOD_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
               "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}
_PERIOD_START = {1: "08:00", 2: "08:00", 3: "10:00", 4: "10:00",
                 5: "14:00", 6: "14:00", 7: "16:00", 8: "16:00"}

# 删课意图词。刻意不收"不上"（"明天不上课"是闲聊不是指令）、
# 不收光杆"删"（"把这条消息删了"跟课没关系），误伤比漏收难看。
_REMOVE_INTENT = ("删除", "删掉", "去掉", "删了", "删一节", "删门", "移除", "退掉", "删课")


def wants_remove_course(text: str) -> bool:
    """判断一句话算不算「从周表里删掉一节课」（确定性分支用的开关）。

    为什么要由系统接管：真模型最爱"只在文字里给个预览、让学生回「确认」"，
    可它压根没调出提案工具——暂存里什么都没有，学生回了确认也是白回
    （学生看到的就是"说了删除、弹窗不来、确认不删"）。
    删课这件事学生说得很清楚（哪天、第几节或哪门课），剩下"新课表长什么样"
    本来就该服务端算，没道理交给模型临场发挥。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if not any(w in t for w in _REMOVE_INTENT):
        return False
    # "把课表全部删除/删除课表"是清空（另一条确定性分支管），不是删一节
    if wants_clear_timetable(t):
        return False
    # 也不是加课（"删掉再加一节"这种混着说的让加课分支先接）
    if wants_add_course(t):
        return False
    # 跟课有关就行：说了"课"字、提到了"第N节"、或点名了星期/课程——
    # "去掉周二的高数""把周五的心理学选修删了"没写"课"字，但都是删课。
    return ("课" in t) or bool(_PERIOD_RE.search(t)) or _pick_day(t) is not None


def parse_remove_course(text: str) -> dict | None:
    """把"删除周一第一节课"/"去掉周二的高数"解析成一张删课确认卡。

    返回的是**提案**（含服务端算好的新课表），本函数一个字节都不写库；
    学生点弹窗上的确认、或回一句"确认删除"，才由系统真正落库。
    解析不出（没说星期/当天好几节课又没说哪节）就如实返回 None，
    由上层追问，绝不把话交给模型自作主张。

    课名的来历按可信度排四层：
      ① 『』「」框着的 —— 学生特意框起来，最可信；
      ② "第N节" —— 拿节次换算成时间，再从周表里查那节课叫什么；
      ③ 文本里剩下的那截 —— "去掉周二的高数"清掉意图词和星期后剩"高数"，
        交给 propose_course_change 的子序列匹配（高数→高等数学）；
      ④ 周表反查 —— 那天只有一节课时，说了星期就够了。
    """
    t = (text or "").strip()
    day = _pick_day(t)
    if day is None:
        return None
    cur = list(get_timetable())

    # ① 引号里的名字
    course = ""
    m = _QUOTE_RE.search(t)
    if m and 1 <= len(m.group(1)) <= 20:
        course = m.group(1).strip()

    # ② "第N节" → 该时段的开课时间，课名从周表里反查
    start = ""
    pm = _PERIOD_RE.search(t)
    if pm:
        start = _PERIOD_START.get(_PERIOD_NUM.get(pm.group(1)) or 0, "")
        if not course:
            hits = [c for c in cur
                    if int(c.get("day", 0)) == day and c.get("start") == start]
            if len(hits) == 1:
                course = str(hits[0].get("course", ""))

    # ③ 文本里剩下的那截当课名（去掉意图词/星期/节次后还剩下的汉字）
    if not course:
        guess = re.sub(r"第\s*[一二三四五六七八1-8]\s*节", " ", t)
        guess = re.sub(r"周[一二三四五六日天]", " ", guess)
        for w in _REMOVE_INTENT:
            guess = guess.replace(w, " ")
        guess = re.sub(r"[^\w一-龥]+", " ", guess)
        runs = [r for r in re.findall(r"[一-龥A-Za-z]{2,20}", guess)
                if r not in ("课", "节课", "的课")]
        if runs:
            course = max(runs, key=len)

    # ④ 周表反查：点明了星期、那天恰好只有一节课
    if not course:
        day_courses = [c for c in cur if int(c.get("day", 0)) == day]
        if not day_courses:
            return None          # 那天压根没课，没什么可删
        if len(day_courses) == 1:
            course = str(day_courses[0].get("course", ""))
            start = str(day_courses[0].get("start", ""))
        else:
            return None          # 当天好几节课又没说哪节 → 上层追问

    if not course:
        return None
    raw = propose_course_change("remove", day, course, start=start)
    try:
        proposal = (json.loads(raw) or {}).get("__proposal__")
    except Exception:
        proposal = None
    if not isinstance(proposal, dict):
        return None
    proposal.setdefault("kind", "timetable_change")
    return proposal


def _pick_day(text: str) -> int | None:
    """从一句话里抠出周几（人话：周一 … 周日，1=周一 … 7=周日）。"""
    m = _WEEKDAY_RE.search(text or "")
    if not m:
        return None
    ch = "日" if m.group(1) == "天" else m.group(1)
    if ch not in "一二三四五六日":
        return None
    return "一二三四五六日".index(ch) + 1


def _pick_course_name(text: str) -> str:
    """抠出课程名（人话：优先取 『』「」框着的那段，其次是引号后的第一截）。"""
    t = text or ""
    m = _QUOTE_RE.search(t)
    if m and 1 <= len(m.group(1)) <= 20:
        return m.group(1).strip()
    head = re.split(r"[，,。；;]", t)[0]
    # "帮我加课：周二 10:00 到 11:40 数据结构" → "周二 10:00 到 11:40 数据结构"
    head = re.split(r"[：:]", head)[-1]
    head = re.sub(r"^(我要|我想|帮我|请帮我|给我|把|帮|在)\s*", "", head)
    head = re.sub(r"周[一二三四五六日天]", "", head)
    head = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}.*$", "", head)
    head = re.sub(r"[^\w一-龥]+", " ", head)
    for w in _ADD_INTENT + ("加一节", "加一门", "加门", "课表", "课程", "课", "的"):
        head = head.replace(w, " ")
    head = re.sub(r"\s+", "", head).strip()
    # 兜底：剩下的片段里挑最长的一截（"到 数据结构" 会挑中"数据结构"这种）
    runs = re.findall(r"[一-龥A-Za-z]{2,20}", head)
    return (max(runs, key=len) if runs else "")[:20]


# 教室号长这样：教一-101 / 外语楼-305 / 机房-B / 教三-201。
# 数字是它的正身，不是噪声——早先判"凡是带数字的都像时间"，把这些全扔了。
_ROOM_RE = re.compile(r"[一-龥A-Za-z][-－]{0,2}\d{1,3}(?:[-－]?\d{1,3})?")


def _looks_like_location(seg: str) -> bool:
    """判断一小截算不算上课地点（人话：不像时间、不像动作，短且没标点）。"""
    if not seg or len(seg) > 14:
        return False
    if any(w in seg for w in _ADD_INTENT):
        return False
    # "晚上 19 点到 20 点" 这种时段，丢掉
    if any(w in seg for w in ("点", "时", "分")):
        return False
    # 带冒号的时间一律不是地点
    if re.search(r"\d{1,2}\s*[:：]\s*\d{2}", seg):
        return False
    # 有数字没关系，但数字得是"教一-101"这种教室号，不能是光溜溜一串数
    if re.search(r"\d", seg) and not _ROOM_RE.search(seg):
        return False
    return True


def _pick_location(text: str) -> str:
    """抠上课地点（人话：引号后面那截、不像时间的短词，多半就是地点）。

    为什么这里要认数字：教室号天生带数字（教一-101、外语楼-305、机房-A），
    原来的写法"凡是带数字的都当时间扔掉"，结果加进来的新课全没地点。
    学生看到的就是"课加上了，可教室是空的"。
    """
    t = text or ""
    m = _QUOTE_RE.search(t)
    if m:
        tail = t[m.end():]
        parts = re.split(r"[，,。；;]|晚上|上午|下午|中午", tail)
    else:
        # 压根没用引号的（"周四加一节马原 教三-301 16点到17点"），
        # 按逗号和空格切开逐段看——教室号形状太特别，误伤不到别的东西。
        parts = re.split(r"[，,。；;]|\s+", t)
    for seg in parts:
        # 先把时间那一截切掉（"16:00 到 17:40"），再看剩下的是不是地点
        seg = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}.*$", "", seg).strip(" 　")
        if _looks_like_location(seg):
            return seg
    return ""


def wants_clear_timetable(text: str) -> bool:
    """判断一句话算不算「我要清空整张课表」（人话：确定性分支用的开关）。

    为什么不放进模型里判断？
        实测同一句"把课表全部删除，一门都不留"，模型有时老老实实调
        propose_clear_timetable，有时却反问"你是想全删还是只删几门"，
        学生被绕回来，整条链路当场断掉。清空意图本就明明白白——
        "学生点了什么按钮"这种才能交给模型，这种不该交。

    :param text: 学生原话
    """
    t = (text or "").strip()
    if not t:
        return False
    # 自带宾语的整表信号，见到就认
    if any(p in t for p in _CLEAR_SELF):
        return True
    # 不带宾语的删除词，必须话里真点了课表/课程才算
    if any(p in t for p in _CLEAR_NEED_OBJ):
        return "课表" in t or "课程" in t
    # 再兜一层："清空/清掉" + 点明是课表/课程，才算
    if "清空" in t or "清掉" in t:
        return "课表" in t or "课程" in t
    return False


def propose_clear_timetable(reason: str = "") -> str:
    """清空整个周表的提案（人话：学生说"把课表全删了/清空课表"时用）。

    三条铁律（对应需求，别改）：
      1. 这是个**只读提案**——函数本身一个字节都不写库，只返回方案；
      2. 模型拿到它只能转述"要清空几门课、让你确认"，**绝不能**自己去调任何删除接口；
      3. **绝不能**在弹窗被点掉之前说"已经删了"。

    真正的删除只走一条路：学生在本页看到确认弹窗 → 点【确认】→ 前端调
    /api/timetable/clear → 后端执行。模型碰不到那条路。

    :param reason: 一句话说明为什么要清空（可选，会显示在弹窗里）
    """
    cur = get_timetable()
    n = len(cur)
    if n == 0:
        return "周表本来就是空的，没有课可清空，不用再确认了。"
    courses = [{"day": int(c.get("day", 0)), "start": c.get("start", ""),
                "end": c.get("end", ""), "course": c.get("course", ""),
                "location": c.get("location", "")} for c in cur]
    # courses 故意留空数组：表示"清空"。
    # 执行时前端必须先把这份提案原样发回来（见 /api/timetable/clear），
    # 后端比对一致才删——学生点确认这个动作本身就是唯一的开关。
    summary = (reason.strip() if reason.strip()
               else f"清空全部 {n} 门课")
    return json.dumps({
        "__proposal__": {
            "kind": "timetable_clear",
            "summary": summary,
            "courses": [],
            "clear_count": n,
        }
    }, ensure_ascii=False)


def apply_pending_timetable_change(session_id: str) -> dict | None:
    """学生回一句"确认"时，**由系统把暂存的课表提案真正写入**（人话：不经过模型）。

    为什么要这么写？
        之前"学生确认之后"这一步是交给模型判断的，模型偶尔会犯迷糊——
        嘴上说自己办不到，还让学生自己去页面上找删除按钮
        （而页面上根本没有这个入口，学生只能一脸问号地回一句"我怎么找不到呢"）。
        现在执行权明确归系统：
          · 提案由 AI 出（propose_course_change / propose_timetable_change）→ 存进待确认暂存；
          · 学生点头（点卡片 或 在聊天框回"确认"）→ 这里确定性写入。

    :return: 写入结果 dict；暂存里没有课表提案时返回 None（调用方据此走正常对话）
    """
    from app.agent.pending import take_pending
    entry = take_pending(session_id) or {}
    proposals = [o for o in (entry.get("options") or [])
                 if isinstance(o, dict)
                 and o.get("kind") == "timetable_change"
                 and not o.get("applied")]
    if not proposals:
        return None

    before = get_timetable()
    done = 0
    for opt in proposals:
        # 卡片带 action（删/加/改单节课）→ 按**当前**周表重新算一遍新课表再落，
        # 这样一轮里的多张卡能一张张叠加，后一张不会把前一张的改动盖掉。
        courses = opt.get("action")
        if isinstance(courses, dict):
            try:
                recomputed = propose_course_change(**courses)
            except Exception:
                recomputed = ""
            if "__proposal__" not in (recomputed or ""):
                continue  # 目标已经没了（比如被上一张卡删过）→ 跳过，不写脏数据
            courses = json.loads(recomputed)["__proposal__"].get("courses")
        cleaned, errors = validate_courses(courses or [])
        if not errors and cleaned:
            save_timetable(cleaned)
            done += 1
    after = get_timetable()

    return {
        "summary": str(proposals[-1].get("summary") or "修改课表"),
        "kind": str(proposals[-1].get("kind") or "timetable_change"),
        "applied": done,
        "before": len(before),
        "after": len(after),
        "courses": list(after),
    }


def _weekday_name_by_num(day: int) -> str:
    return DAY_NAMES.get(int(day), "未知")


# 模型偶尔把 day 写成"周一""星期三"这种中文，而不是 1/2/3。
# 与其让它报错，不如认出来——学生说的是同一个意思。
_CN_DAY = {
    "周一": 1, "一": 1, "星期一": 1,
    "周二": 2, "二": 2, "星期二": 2,
    "周三": 3, "三": 3, "星期三": 3,
    "周四": 4, "四": 4, "星期四": 4,
    "周五": 5, "五": 5, "星期五": 5,
    "周六": 6, "六": 6, "星期六": 6,
    "周日": 7, "日": 7, "天": 7, "星期日": 7, "星期天": 7, "周末": 7,
}


def _coerce_day(value) -> int | None:
    """把"周一"/3/"3 "这类写法统一成整数；实在认不出来返回 None。

    为什么需要它：工具外面虽已加了错误围栏（参数写错不再崩整轮），
    但这里直接"认得出"更省事——模型少走一轮弯路，学生也不用重复描述。

    注意：**只管转换，不管范围**。9 这种越界值照样返回 9，
    由调用方按 1-7 判定并给出"越界"提示，报错信息才够具体。
    """
    if isinstance(value, bool):           # True 会被当成 1，是坑，先挡掉
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        key = value.strip()
        if key in _CN_DAY:
            return _CN_DAY[key]
        digits = "".join(ch for ch in key if ch.isdigit())
        if digits:
            return int(digits)
    return None


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
2. 挑出 **2-3 个候选时间段**，然后**必须调用 propose_slots 工具**，
   把这些候选以结构化形式交给前端——学生会在界面上看到可勾选的卡片，
   **不要只在文字里罗列时间段**，否则学生没法点选。
3. 文字里补一句"你点一下想要的时间就行"，然后**停下来等学生选** —— 这一步绝不能省。
4. **只有学生明确同意之后**（比如"第一个可以""就用这个""好"），
   才调用 add_todo_tool 真正写进日程。学生没确认前，**绝对不要写入**。
5. 学生提出调整（"太晚了""换个时间"），就重新查空档、再调用 propose_slots 提议，继续等。

【硬性约束】
- 待办不能跟课程撞时间（工具会自动拦截，但你要先自己看清楚）。
- 一次只推 2-3 个候选，不要甩一长串让人挑花眼。
- 说话简短、具体，直接给时间点，不要长篇分析。
- 如果周表是空的（还没上传课表），先提醒学生去「我的日程」上传课表，
  否则无从判断什么时间空着。

【个人数据隔离 —— 预览确认制，绝对不许跳过】
你只能生成**预览/提案**，学生确认之前数据库绝不会变；真正的写入由**系统**执行（你碰不到周表，
这不是权限问题，是刻意的职责划分——学生的数据只能由系统写）。
1. **修改课表**（删课/加课/改单节课，如"周一去掉第一节"）：
   **必须调用 propose_course_change 出确认卡**——新课表由服务端算好，你只说清对哪节课做什么。
   **绝不允许只在文字里给预览、问学生"确认吗"——没有卡片的确认等于没确认。**
   一句话说明白：propose_course_change 就长在你的工具箱里，**你完全有权调用它**。
   学生无论是点卡片、还是在聊天框回一句"确认"，**都是系统负责执行**，你只需在旁边说明改了什么。
   ⛔ 口癖红线（三条，实测踩过，说出任一条都算 bug，务必打住）：
      · 不许说"自己办不了/没能耐改课表"这类话——出提案的工具就在你的工具箱里，
        你完全有权调用；写不写由系统兜着，跟你没关系。
      · 不许让学生自己去界面上找删除入口——系统界面**压根没有手动删课的按钮**，
        学生照着找一圈也找不到，白白多一轮往返。课表增删改只有你出的确认卡一个入口。
      · 不许在卡片还等着确认时就说"已经删好了/已经改完了"——数据还没动，说了就是骗人。
      一句话记住：你负责**说清改什么**，系统负责**动手改**，学生负责**点头**。
   ⛔ 上一轮踩的坑：提示里如果把上面那些错话原样写出来当反例，模型反而更容易学会。
      所以这里只描述"不能做什么"，一个错例句都不列。
   如果学生说"确认/可以"但上一轮你还没出过卡，**立刻调用 propose_course_change 把卡补上**。
1b. **清空整张课表**（学生说"课表全删了""一门都不留""清空课表"）：
   **只调用 propose_clear_timetable 出一张确认弹窗就够**，弹窗上有【确认】【取消】两个按钮。
   ⛔ 三条红线，一条都不能破：
      · **你没有任何删除接口可用于清空**——工具箱里没有一个能动周表的写入口，
        这是刻意的：删除只能由学生点弹窗按钮触发，模型碰不到。
      · **不许宣布结果**——数据在你说话时还在库里，学生点【取消】就白说一句，
        这种谎话比不说更伤信任。你只说"确认弹窗已弹出，你点确认我就执行"。
      · **不许替学生做决定**：不许说"我帮你清了""剩下几门我也顺手删了"，
        你要说的是"清空 N 门课的确认弹窗已经弹出，你点确认我再执行"。
      学生点【确认】→ 系统执行 → 页面自动刷新成空课表；点【取消】→ 什么都没发生。
2. **导入课表文件 / 整表重排**：读完文件 → 构造完整课程列表（"第几节"换算成 HH:MM）→
   调用 propose_timetable_change 出确认卡。没出卡前绝不说"导入成功"。
3. **删除/修改待办**（remove_todo、update_todo_status）：先列出要动的待办，
   学生确认后再执行；删除是不可恢复的，更要问清楚。
4. 学生说"改一下课表"却没说怎么改时，先问清楚改哪里，别自作主张。

【工具用法】
- get_weekly_timetable：看整周课程
- find_free_slots(date, min_minutes)：查某天空档
- list_day_todos(date)：看某天已排了什么
- **propose_slots(options_json)：把候选结构化地交给前端渲染成可勾选卡片。
  提完候选后必须调用它**（options_json 是 JSON 数组，每条含 title/date/start/end）。**
- add_todo_tool(title, date, start, end, note)：**确认后**才写入
- **propose_clear_timetable()：清空整张课表**——只出确认弹窗，纯只读，写完就停手
- **propose_course_change(op, day, ...)：删课/加课/改单节课的首选**——服务端算好新课表出确认卡
- **propose_timetable_change(courses_json, change_summary)：整表重排/文件导入用**——
  把调整后的完整课表（JSON 数组）交给前端渲染成「确认修改」卡片，学生点确认后系统写入。
  **注意：写入不经过你**，所以卡片确认后不用（也不能）再调任何写入工具。
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
        "propose_slots": Tool(
            name="propose_slots",
            description=(
                "把候选时间段交给前端展示成可勾选的卡片。**查好空档、准备问学生选哪个时调用**。"
                "入参 options_json 是 JSON 数组，每条含 title(待办标题)、date(如2026-09-25)、"
                "start、end、可选 note。学生没确认前不要调用 add_todo。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "options_json": {
                        "type": "string",
                        "description": '候选数组的 JSON 字符串，如 [{"title":"复习高数","date":"2026-09-25","start":"19:00","end":"20:30"}]',
                    }
                },
                "required": ["options_json"],
            },
            func=propose_slots,
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
        "propose_clear_timetable": Tool(
            name="propose_clear_timetable",
            description=(
                "**清空整个周表**的唯一途径（学生说'把课表全删了''清空课表''一门都不留'时用）。"
                "这个工具是纯只读的——它只生成一张清空确认弹窗，**一个字节都不写库**，"
                "也**没有任何删除接口给你调用**。你只负责告诉学生'要清空几门课，确认弹窗已弹出'。"
                "⛔ 严禁说'我已经帮你清空了'。学生点了弹窗上的【确认】，由系统执行，"
                "点到【取消】就什么都没发生。"
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            func=lambda: propose_clear_timetable(),
        ),
        "propose_timetable_change": Tool(
            name="propose_timetable_change",
            description=(
                "修改/导入课表的唯一途径：把调整后的**完整**课表提案交给前端渲染成「确认修改」卡片，"
                "学生点确认后由系统直接写入周表（写入不经过你，确认后不要再调任何写入工具，"
                "也不要声称你已修改）。入参 courses_json 是 JSON 数组字符串，每条含 "
                "day(1=周一..7=周日)、start、end、course、location，必须包含未改动的课程；"
                "change_summary 用一句话说明改了什么，会显示在卡片上。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "courses_json": {
                        "type": "string",
                        "description": 'JSON 数组字符串，如 [{"day":1,"start":"08:00","end":"09:40","course":"高等数学","location":"教三301"}]',
                    },
                    "change_summary": {
                        "type": "string",
                        "description": "一句话说明这次改了什么，如 删除周一第一节高等数学",
                    },
                },
                "required": ["courses_json"],
            },
            func=propose_timetable_change,
        ),
        "propose_course_change": Tool(
            name="propose_course_change",
            description=(
                "**删课/加课/改单节课的首选工具**：你只需说清对哪天的哪节课做什么，"
                "新课表由服务端基于当前周表算好并生成确认卡（不写入，等学生点确认）。"
                "op=remove 删课（参数 day+course，可加 start 区分）；op=add 加课"
                "（day+course+new_start+new_end+new_location）；op=update 改课"
                "（day+course 定位，new_start/new_end/new_course/new_location 指定新值）。"
                "**必须调用它出卡，绝不允许只在文字里问学生'确认吗'**。"
                "没找到课或同名多节时，工具会返回提示，照它说的做。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "op": {"type": "string", "description": "remove / add / update"},
                    "day": {"type": "integer", "description": "1=周一 … 7=周日"},
                    "course": {"type": "string", "description": "目标课程名（remove/update 用）"},
                    "start": {"type": "string", "description": "可选，同名多节课时用开始时间区分，如 08:00"},
                    "new_start": {"type": "string", "description": "add/update：新开始时间 HH:MM"},
                    "new_end": {"type": "string", "description": "add/update：新结束时间 HH:MM"},
                    "new_course": {"type": "string", "description": "update：新课程名（可选）"},
                    "new_location": {"type": "string", "description": "add/update：新地点（可选）"},
                    "change_summary": {"type": "string", "description": "一句话说明改了什么（可选，会显示在卡上）"},
                },
                "required": ["op", "day"],
            },
            func=propose_course_change,
        ),
        "update_todo_status": Tool(
            name="update_todo_status",
            description="把某条待办标记为已完成(done)或未完成(planned)。**先跟学生确认是哪条、改成什么，再调用**。",
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
            description=(
                "删除一条待办（不可恢复）。**必须先向学生列出要删的待办并得到明确确认后才能调用**。"
            ),
            parameters={
                "type": "object",
                "properties": {"todo_id": {"type": "string", "description": "待办编号"}},
                "required": ["todo_id"],
            },
            func=remove_todo,
        ),
    }
