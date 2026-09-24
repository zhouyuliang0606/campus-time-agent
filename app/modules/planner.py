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
    """
    if op not in ("remove", "add", "update"):
        return f"op 只能是 remove/add/update，收到的是 {op}"
    try:
        day = int(day)
    except (TypeError, ValueError):
        return "day 不是数字"
    if day not in range(1, 8):
        return f"day={day} 越界（应 1-7，1 是周一）"

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
        }
    }, ensure_ascii=False)


def _weekday_name_by_num(day: int) -> str:
    return DAY_NAMES.get(int(day), "未知")


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
你只能生成**预览/提案**，学生确认之前数据库绝不会变；真正的写入由**系统**在学生点击确认卡后执行：
1. **修改课表**（删课/加课/改单节课，如"周一去掉第一节"）：
   **必须调用 propose_course_change 出确认卡**——新课表由服务端算好，你只说清对哪节课做什么。
   **绝不允许只在文字里给预览、问学生"确认吗"——没有卡片的确认等于没确认。**
   学生点确认后系统自动写入，你不需要也**无法**直接写周表——这是设计如此，**不是你没有权限**，
   绝不要说"我没有权限删除/修改"；也绝不要在卡片确认前声称"已删除/已修改"。
   如果学生口头说"确认/可以"但上一轮你还没出过卡，**立刻调用工具把卡补上**。
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
