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
from app.agent.pending import is_confirmation
from app.store import (
    add_todo, get_timetable, list_todos,
    save_timetable, update_todo,
)
from app.modules.student_persona import mock_phrase

MODULE_KEY = "planner"

# 一天里可以用来安排待办的时间范围（太早太晚不适合打扰学生）
DAY_START = "07:00"
DAY_END = "22:00"

# 「替学生挑一个空档」时的两个默认值（人话：别把整晚都占掉，先给一段合理的）
# 为什么要有这两个数：学生只说了"周四加个健身"、没说几点，
# 我们要主动排一个点（他问了才给 = 被投诉"没有帮我想时间"），
# 但也不能把 15:40~22:00 整段都占成"健身"——那是给人添乱，不是帮忙。
AUTO_SLOT_MINUTES = 60   # 空档至少这么长才值得推荐
AUTO_SLOT_LENGTH = 90    # 替他挑的那一段给多久（学生点头后写入的就是这段）

# 一天的星期几怎么对应周表的 day 字段
DAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

# ---- 节次（"第几节课"）→ 具体时间 ----
# 学生习惯用节次指时间：「在原本第一节课的位置加入健身代办」。
# 演示课表按两节课一个时段算：
# 第1-2节 08:00、第3-4节 10:00、第5-6节 14:00、第7-8节 16:00；晚上的课学生会直接说时间。
# 这一份表**排待办和删课共用**：删课只要起点（拿去周表里反查那节课叫什么），
# 排待办要整段（学生指的就是那"一整节课的位置"）。
_PERIOD_RE = re.compile(r"第\s*([一二三四五六七八1-8])\s*节")
_PERIOD_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
               "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8}
_PERIOD_START = {1: "08:00", 2: "08:00", 3: "10:00", 4: "10:00",
                 5: "14:00", 6: "14:00", 7: "16:00", 8: "16:00"}
_PERIOD_SPAN = {1: ("08:00", "09:40"), 2: ("08:00", "09:40"),
                3: ("10:00", "11:40"), 4: ("10:00", "11:40"),
                5: ("14:00", "15:40"), 6: ("14:00", "15:40"),
                7: ("16:00", "17:40"), 8: ("16:00", "17:40")}


def period_span(text: str):
    """「第一节课的位置」→ ("08:00", "09:40")（人话：学生拿节次指时间时要接得住）。

    截图里那句就是它：「在原本第一节课的位置加入健身代办」——
    "第一节课的位置"是**时间**，不是标题的一部分。认不出来就返回 None。
    """
    m = _PERIOD_RE.search(text or "")
    if not m:
        return None
    return _PERIOD_SPAN.get(_PERIOD_NUM.get(m.group(1), 0))



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


def _prefer_window(text: str):
    """学生话里的"上午/下午/晚上"决定往哪一段找（人话：别在早上七点给人排健身）。

    没说时间段 → 默认按"白天 08:00~22:00"找。为什么不直接用 DAY_START(07:00)：
    学生只说"周六加个健身"时，那天整天都空，最长的那段从 07:00 起手，
    排出来就是"周六早上七点健身"——帮他排了，但帮得挺糟。
    早八点起手是学生作息里最自然的默认。
    """
    t = text or ""
    if any(w in t for w in ("上午", "早上", "早晨", "一早")):
        return (6 * 60, 12 * 60)
    if "中午" in t:
        return (11 * 60, 14 * 60)
    if "下午" in t:
        return (12 * 60, 18 * 60)
    if any(w in t for w in ("晚上", "傍晚", "夜里", "今晚", "今夜")):
        return (18 * 60, 22 * 60)
    return (8 * 60, 22 * 60)


def pick_free_slot(date: str, prefer: str = "",
                   minutes: int = AUTO_SLOT_MINUTES,
                   length: int = AUTO_SLOT_LENGTH,
                   not_before: int | None = None) -> dict | None:
    """替学生从那天的空档里挑一段（人话：他说"周四加个健身"，几点由系统算）。

    这是被投诉的那句「**没有帮我想时间，是我问了才说的**」的正解：
    学生只说了"哪天"、没说"几点"，系统不该把问题推回去问他，
    而要自己去查空档、挑一段合适的排上，再把确认条挂出来让他点头。

    挑法（尽量好猜、别花哨）：
      1. 学生说了"上午/下午/晚上"就只在那一段里找；没说就按白天 08:00~22:00 找。
      2. 优先**最长的一段**空档——最经得起塞一件新事；同样长时挑靠后的
         （下午/傍晚往往比一早更有空，也更符合安排健身、自习这类事）。
      3. 在选中的空档里，从那一段的起手点开始取 AUTO_SLOT_LENGTH 那么长
         （不会把整段空档都占掉），并且尽量落进学生说的时间段里。

    挑不出来（那天满课、或空档都不够长）返回 None —— 由调用方如实告知，
    绝不硬凑一个时间写进日程。

    :param not_before: 分钟数。给了就**不早于这个点**起手——列候选时会用到：
        今天是"此刻往后"才有意义，不然上午问一句"加个健身"，
        系统会把早上八点那段排给学生，而他看到的时候早过了。
    """
    slots = [s for s in find_free_slots(date, min_minutes=minutes) if s.get("start")]
    if not slots:
        return None
    lo, hi = _prefer_window(prefer)
    # 只留下"跟目标时间段有交集、且交集够长"的空档
    fits = [s for s in slots
            if min(to_minutes(s["end"]), hi) - max(to_minutes(s["start"]), lo) >= minutes]
    if fits:
        slots = fits
    best = max(slots, key=lambda s: (s["minutes"], to_minutes(s["start"])))
    # 起手点：不早于目标时间段的起点（学生说"下午"就别从早上开始）
    begin = max(to_minutes(best["start"]), lo)
    if not_before is not None:
        begin = max(begin, not_before)
    finish = min(begin + length, to_minutes(best["end"]))
    if finish - begin < minutes:
        return None        # 这段已经过去了 / 装不下 → 当作这天没有可用空档
    return {
        "date": date,
        "weekday": _weekday_name(date),
        "start": to_hhmm(begin),
        "end": to_hhmm(finish),
        "minutes": finish - begin,
    }


def _now_minutes() -> int:
    """现在几点（换成分钟数，人话：判断"今天这段是不是已经过去了"）。"""
    now = datetime.datetime.now()
    return now.hour * 60 + now.minute


# 一天切成三档（人话：学生嘴里的"上午/下午/晚上"）。
# 为什么按这三档列候选，而不是"把最长的那段空档给他切一半"：
# 学生要的是**几个可以挑的时间**（他自己打勾）。课表再空，他心里的备选
# 也就是"上午 / 下午 / 晚上"这几档；给他一段 15:40-17:10、一段 17:40-19:10，
# 他反而看不懂这两段有什么差别。切完还顺手避开了午休和太早的时段。
DAY_PARTS = (("上午", 9 * 60, 12 * 60),
             ("下午", 14 * 60, 18 * 60),
             ("晚上", 19 * 60, 22 * 60))


def _part_order(prefer: str = "") -> tuple:
    """三档的先后（人话：学生说了"晚上"，晚上那段就排第一个）。

    没说就按"下午 → 上午 → 晚上"——下午那档最像样：
    早上要赶课、晚上容易困，"排件事"通常排在下午。
    """
    t = prefer or ""
    if any(w in t for w in ("上午", "早上", "早晨", "一早")):
        return ("上午", "下午", "晚上")
    if any(w in t for w in ("晚上", "傍晚", "夜里", "今晚", "今夜")):
        return ("晚上", "下午", "上午")
    return ("下午", "上午", "晚上")


def _span_free(date: str, span) -> bool:
    """那天的某个时刻区间是不是完全空着（人话：这段能不能塞下这件事）。

    走 find_free_slots 而不是自己比对课表：它会同时避开**课程**和**已排的待办**，
    自己写一份迟早会漏掉一边（漏了待办，学生就会收到两个撞在一起的安排）。
    """
    s0, s1 = to_minutes(span[0]), to_minutes(span[1])
    for s in find_free_slots(date, min_minutes=1):
        if not s.get("start"):
            continue
        if to_minutes(s["start"]) <= s0 and to_minutes(s["end"]) >= s1:
            return True
    return False


def _day_slots(date: str, prefer: str = "", limit: int = 3,
               rotate: int = 0, length: int = AUTO_SLOT_LENGTH) -> list:
    """那一天里"像样的几段"（人话：上午/下午/晚上各给一段塞得进的）。

    排序按 _part_order：学生点名了哪一档，那一档排第一个；没说就下午优先。
    一天里某一档本来就不空（有课或有约）→ 那一档跳过，不硬塞——
    列出来的每一段都是真能排的。

    :param rotate: 学生**没点名**哪一档时，把三档的顺序往后挪几格。
        给"跨天列候选"用的：不然连列三天，三条都是"下午 14:00-15:30"，
        学生看着像同一个选项复制了三遍；挪一挪就变成
        「明天下午 / 周六上午 / 周日晚上」，一眼看出有的挑。
    :param length: 这一段要排多久。学生说了「大概三小时」就按 180 分钟找，
        没说才用默认 90 分钟。空档不够长就跳过，绝不硬凑。
    """
    not_before = (_now_minutes() + 30
                  if date == datetime.date.today().isoformat() else None)
    min_needed = length
    free = [s for s in find_free_slots(date, min_minutes=min_needed)
            if s.get("start")]
    out = []
    for name, plo, phi in DAY_PARTS:
        for s in free:
            begin = max(to_minutes(s["start"]), plo)
            if not_before is not None:
                begin = max(begin, not_before)
            finish = min(begin + length, to_minutes(s["end"]), phi)
            if finish - begin < min_needed:
                continue
            out.append({"part": name, "date": date,
                        "weekday": _weekday_name(date),
                        "start": to_hhmm(begin), "end": to_hhmm(finish),
                        "minutes": finish - begin})
            break
    order = list(_part_order(prefer))
    if rotate and not _said_part(prefer):
        rotate %= len(order)
        order = order[rotate:] + order[:rotate]
    out.sort(key=lambda s: order.index(s["part"]) if s["part"] in order else 9)
    return out[:limit]


def _said_part(text: str) -> bool:
    """学生自己说了"上午/下午/晚上"没有（人话：他点名了就别替他挪顺序）。"""
    t = text or ""
    return any(w in t for w in ("上午", "早上", "早晨", "一早", "中午",
                                "下午", "晚上", "傍晚", "夜里", "今晚", "今夜"))


def candidate_slots(text: str = "", max_slots: int = 3, days_ahead: int = 5) -> list:
    """替学生**列几个**候选时段（人话：不替他定一个，摊开来让他打勾）。

    为什么要从"挑一个"改成"列几个"：学生原话——
    「**由 ai 帮我去挑选合适时间，进行列举**……由我打勾，进行增加」。
    只给一个时间，等于替他做了主；他要么全盘接受，要么再让你换一次，
    来回两轮。摊出三段、他自己勾，通常一轮就定下来了。

    列举规则（尽量好懂、别花哨）：
      1. 学生说了哪一天 → **只列那天**（他说周四就是周四），那天里的
         上午/下午/晚上各一段，最多 `max_slots` 段；那天真排不下就如实返回空列表，
         由调用方去说，**不偷偷换到别的天**。
      2. 没说哪天 → 从今天起往后逐天看，一天一段，凑够 `max_slots` 段。
         今天只看"此刻之后"的时段（已经过去的上午不该出现在候选里）。
      3. 学生提了「第N节课的位置」→ 那一段（按节次表换算）**排到第一位**，
         前提是它真空着；那天被课占了就往后找最近一个空着的那天，
         都占着就跳过——绝不把候选排在课上。
    标题不在这里拼——这里只负责"时间"。
    """
    t = (text or "").strip()
    prefer = t
    today = datetime.date.today()
    # 「平时 / 工作日」= 周一到周五哪天都行 → 不锁死单日，跨工作日摊开列。
    # 实测「我要平时去吃火锅」若照常 _pick_date，抠不出日期就按"从今天起逐天"，
    # 学生观感是"怎么给我排周六"；他说的是"平时"，周末就不该出现在候选里。
    workday_mode = any(w in t for w in ("平时", "工作日"))
    picked_day = None if workday_mode else _pick_date(t)
    # 学生说了时长（"大概三小时"）→ 候选段就按 3 小时找；没说才回落 90 分钟。
    # 这里必须认，不然候选卡上全是 90 分钟，学生会拿到「不是我想要的 3 小时」。
    want = wanted_minutes(t) or AUTO_SLOT_LENGTH
    want = max(5, min(int(want), 8 * 60))
    out = []

    def _push(slot):
        if slot and not any(s["date"] == slot["date"] and s["start"] == slot["start"]
                            for s in out):
            out.append(slot)

    # ③ 先处理「第一节课的位置」这种按节次指时间的话
    span = period_span(t)
    if span:
        days = ([picked_day] if picked_day
                else [(today + datetime.timedelta(days=i)).isoformat()
                      for i in range(days_ahead + 1)])
        for d in days:
            if d == today.isoformat() and to_minutes(span[1]) <= _now_minutes():
                continue                      # 今天这一节已经过去了
            if not _span_free(d, span):
                continue                      # 那个位置被课占着 → 往后找
            _push({"part": "上午" if to_minutes(span[0]) < 12 * 60 else "下午",
                   "date": d, "weekday": _weekday_name(d),
                   "start": span[0], "end": span[1],
                   "minutes": to_minutes(span[1]) - to_minutes(span[0])})
            break

    if picked_day:
        for s in _day_slots(picked_day, prefer, limit=max_slots, length=want):
            _push(s)
        return out[:max_slots]

    for off in range(0, days_ahead + 1):
        if len(out) >= max_slots:
            break
        day = (today + datetime.timedelta(days=off)).isoformat()
        if workday_mode and _iso_weekday(day) >= 6:
            continue          # 「平时 / 工作日」不排周末，周六周日直接跳过
        for s in _day_slots(day, prefer, limit=1, rotate=off, length=want):
            _push(s)
    return out[:max_slots]


def parse_slot_text(text: str, title: str = "", fallback_date: str = "") -> dict | None:
    """把学生在「其他」里自己填的一行字，解析成一张待办卡（人话：自填也要认）。

    三种都认：
      · 说全了（"周六 19:00-20:00"）→ 直接用他给的时间；
      · 只说了哪天（"周六"）→ 那天替他挑一段空档；
      · **只报了钟点、没报哪天**（"晚上七点到八点"）→ 拿 `fallback_date` 补上那天
        （调用方传候选卡里最靠前的那天；那天这一节已经过了就顺延到明天）。
        不补这一档，「其他」里最自然的写法会直接掉进"没认出来"，学生白填一行。
    认不出来返回 None —— 由调用方如实告诉他"这行没认出来"，**不瞎猜一个时间**。
    标题缺省用外面传进来的那件事（他说的是"其他时间"，不是"其他事情"）。
    """
    t = (text or "").strip()
    if not t:
        return None
    prop = parse_add_todo(f"{t} {title}".strip())
    if prop is None:
        prop = auto_todo_proposal(f"{t} {title}".strip())
    if prop is None:
        prop = _span_only_proposal(t, title, fallback_date)
    if prop is None:
        return None
    if title and prop.get("title") in ("", "待办"):
        prop["title"] = title
        prop["summary"] = (f"{title}｜{prop['date']}（{prop.get('weekday', '')}）"
                           f"{prop['start']}-{prop['end']}")
    return prop


def _span_only_proposal(text: str, title: str, fallback_date: str) -> dict | None:
    """只有起止钟点、没有日期的那一行字 → 用兜底那天补成一张卡（内部用）。

    顺延规则：兜底那天就是今天、而这一段已经过去了 → 挪到明天。
    宁可挪一天，也不给学生排一段"已经过去的晚上七点"。
    """
    begin, finish = _pick_span(text or "")
    if not begin:
        return None
    day = (fallback_date or "").strip()
    if _iso_weekday(day) == 0:
        return None
    if not finish:
        finish = to_hhmm(to_minutes(begin) + 60)
    if to_minutes(finish) <= to_minutes(begin):
        return None
    if day == datetime.date.today().isoformat() and to_minutes(begin) <= _now_minutes():
        day = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    name = _pick_title(text or "")
    if not name or name == "待办":
        name = (title or "").strip() or "待办"
    if name == "待办":
        return None
    wd = _weekday_name(day)
    return {
        "kind": "todo_add",
        "title": name,
        "date": day,
        "start": begin,
        "end": finish,
        "weekday": wd,
        "minutes": to_minutes(finish) - to_minutes(begin),
        "summary": f"{name}｜{day}（{wd}）{begin}-{finish}",
    }


def build_todo_slots(title: str, slots: list, hint: str = "") -> dict:
    """把几个候选时段包成一张「挑时间」的提案（人话：弹框里那串能打勾的时间）。

    它的形状跟别的提案不一样：**一个提案里装着好几个时段**，学生勾哪个算哪个
    （还能在「其他」里自己补一个）。所以它不是"已经定好的安排"，
    而是"摊开来的备选"——真正写库要等学生勾完点确认，走 `/api/todos/batch`。
    """
    items = []
    for s in slots or []:
        if not (s.get("date") and s.get("start") and s.get("end")):
            continue
        items.append({
            "date": s["date"],
            "weekday": s.get("weekday", ""),
            "start": s["start"],
            "end": s["end"],
            "minutes": s.get("minutes"),
            "label": f"{s.get('weekday', '')}（{_mmdd(s['date'])}）"
                     f"{s['start']}-{s['end']}",
        })
    return {
        "kind": "todo_slots",
        "title": title or "待办",
        "slots": items,
        "hint": hint,
        "summary": f"{title or '待办'}｜{len(items)} 个候选时段",
    }


def _mmdd(date: str) -> str:
    """2026-10-01 → 10/01（人话：弹框里那行字要短，学生扫一眼就懂）。"""
    try:
        return f"{int(date[5:7])}/{int(date[8:10])}"
    except Exception:
        return date


def todo_title_of(text: str) -> str:
    """从一句话里抠出"要加的那件事"（人话：候选卡上那个名字）。

    `_pick_title` 是内部的抠法，外面（main.py 的候选分支、测试）也要用同一份，
    所以在这里公开一个入口——抄第二份抠法迟早会走样。
    """
    try:
        return _pick_title(text or "")
    except Exception:
        return "待办"


# 「没看见 / 看不到 / 没显示」——学生这句不是要加新事，是在**问在不在**。
# 这一档早先没人接，整句掉给模型，它就会开始编原因（实测原话：
# 「基本可以确定是系统推送出了问题……得让管理端那边看一下」）——
# 系统明明能真查库，却让学生去"刷新二选一"，全踩在场景边界的红线上。
_MISSING_RE = re.compile(
    # ⚠️ 顺序有讲究：「看到/看见/显示」这些**具体的**放前面——
    # 「怎么没」要是排在前面，「我怎么没看到周一的健身」会先匹配掉"怎么没"，
    # 剩下光秃秃的「看到」混进事名里（抠出来就成了「看到健身」）。
    r"没有?看见|没有?看到|看不到|看不见|没有?显示|没显示|显示不出来|显示不出|"
    r"怎么还没有?|怎么没有|为什么没有|没有?找到|没找|"
    # ⚠️ 「在哪」后面**跟着时间单位就不算报障**：「游泳在哪天来着」是问时间，
    # 不是"看不见"。一句话有两种问法，这条查的是"在不在"，问"哪天"该由
    # 找空档那条路接（它本来就排时间）。早先没加这道前面看，这句话被判成
    # 报障，还把残片「游泳天来着」当成事名回出去了。
    r"在哪(?:里)?(?!天|星期|周|月|年|日|号)|"
    r"(?:日程|周表|月表|课表|待办|清单)[^，.！!？?]{0,6}没有|"
    r"没有[^，.！!？?]{0,6}(?:日程|周表|月表|课表|待办|显示)")

# 抱怨句里的"场话"不是事名的一部分（日程/显示/待办项目……），
# 抹干净之后剩下的才可能是"游泳"。
_MISSING_META = ("日程", "周表", "月表", "课表", "待办项目", "待办", "代办",
                 "项目", "显示", "面板", "清单", "列表", "为什么", "怎么",
                 "我现在", "现在", "还是", "还没", "没有", "没", "我",
                 "里面", "里边", "里面", "里", "上面", "上边", "上",
                 "出来", "进去", "出现")

# 两头的小零碎（「有游泳」的"有"、"健身还"的"还"）直接剥掉。
# ⚠️ 只剥**两头**：「有氧运动」中间的"有"不是碎屑，动了就把名字削坏了。
_MISSING_EDGE = "有在里是的还去来啊呢吧呀哦 了"

# 场话抹完之后还赖在事名里的碎屑（「游泳天来着」「看到健身」这类）。
# 判据不是"长度够不够"，是**里面有没有这些词**——抹干净的短名（如「健身」）
# 照样是真名，沾上碎屑的再长也是垃圾。
_MISSING_JUNK = ("来着", "天来", "在哪", "怎么", "为什么", "还是",
                 "出来", "进去", "里面", "里边", "上面", "上边")


def _missing_title_ok(name: str) -> bool:
    """这句抠出来的事名像不像真名（人话：抹过场话之后剩下的还算一个名字吗）。

    为什么要有这一道：抹场话是按词表逐个替换的，替换不掉的残片会留在结果里，
    `_pick_title` 又只看长度和噪声词，于是「游泳在哪天来着」能抠出
    「游泳天来着」这种谁也没说过的名字，还拿它去库里查、回给学生。
    判到垃圾就返回空串，调用方会改走"最近要加的那件事"或者直接如实回答。
    """
    if not name:
        return False
    if any(w in name for w in _MISSING_META):
        return False
    if any(w in name for w in _MISSING_JUNK):
        return False
    return bool(re.search(r"[一-鿿A-Za-z0-9]", name))


def missing_thing_of(text: str, extra_names: list | None = None) -> dict | None:
    """学生这句「没看见日程里有 X」里，说的是哪件事、哪一天。

    返回 `{"title": ..., "date": ...}`（date 可能为 None——他没说哪天；
    **title 也可能是空串**：问到了这件事、但名字抠出来是残片），
    不像在问"在不在"就返回 None，交给别的分支。
    抠法分两步：先拿**库里已有的待办名 / 调用方给的近期事名**到原句里找
    （学生抱怨的十有八九是他刚弄过的那件事，名字原样躺在句子里，这步最准）；
    都落空再抹"场话"后 `_pick_title`，最后过一遍 `_missing_title_ok`。
    """
    t = (text or "").strip()
    if not t or not _MISSING_RE.search(t):
        return None
    day = _pick_date(t)
    for name in [n for n in (extra_names or []) if n and len(n) >= 2]:
        if name in t:
            return {"title": name, "date": day}
    t2 = _MISSING_RE.sub(" ", t)
    for w in _MISSING_META:
        t2 = t2.replace(w, " ")
    t2 = re.sub(r"[？?！!。．，,的了是就也]", " ", t2)
    # 「有游泳」的"有"是存现动词（日程里**有**X），剥；「有氧运动」的"有"
    # 是名字的一半，留。别拿长度判断——"有游泳啊"带个语气词就四打了，
    # 判据靠不住；白名单列已知的"有X"复合词，短小但每一项都是真名。
    edge = _MISSING_EDGE
    if re.match(r"\s*有(氧|轨)", t2):
        edge = edge.replace("有", "", 1)
    t2 = t2.strip(edge)
    title = ""
    try:
        cand = _pick_title(t2)
        if cand and cand != "待办":
            title = cand
    except Exception:
        pass
    # 抠出来的是残片（「游泳天来着」）就当没问到名，交给调用方走"最近要加的那件事"，
    # 别拿一个谁也没说过的名字去库里查。
    if not _missing_title_ok(title):
        title = ""
    return {"title": title, "date": day}


def todo_slots_proposal(text: str, max_slots: int = 3) -> dict | None:
    """学生只说了"加个健身"（没说几点）→ 摊开几个候选时段，让他自己打勾。

    这是**本轮规格的核心改动**。上一版的做法是"系统替他挑一个时间、出一张单条确认卡"，
    学生原话是「**我定的太严了，你改一下，由 ai 帮我去挑选合适时间，进行列举**……
    由我打勾，进行增加」。只给一个点等于替他做了主：他要么全盘接受，要么再让你换一次，
    来回两轮。摊出三段、他自己勾，通常一轮就定下来了。

    返回一张 `kind="todo_slots"` 的提案（装着好几个时段），
    还是一样**不写库**——真写入要等学生勾完、点弹框上那颗按钮，走 `/api/todos/batch`。

    凑不出来（没有"要做什么事"、或者那天/那几天真排不下）返回 None，
    交给调用方如实说缺什么、或者直说"这天满了"，**绝不硬凑一个时间**。
    """
    t = (text or "").strip()
    if not t:
        return None
    title = _pick_title(t)
    if not title or title == "待办":
        return None          # 连"要做什么事"都没说 → 由追问分支去问，别拿"待办"占位
    slots = candidate_slots(t, max_slots=max_slots)
    if not slots:
        return None          # 那天的空档真排不下（或全是过去时段）
    day = _pick_date(t)
    if day:
        hint = (f"{day}（{_weekday_name(day)}）的空档都在这儿了——"
                f"避开课和已有的安排，勾一个方便的。")
    else:
        hint = "避开课和已有的安排，挑一个方便的勾上（可以勾好几个）。"
    return build_todo_slots(title, slots, hint=hint)


def build_todo_mode(title: str, when: str = "", date: str = "",
                    minutes: int | None = None, hint: str = "") -> dict:
    """包一张「这件事怎么定时间」的提案（人话：弹框上那两颗按钮）。

    学生规格原话：
    「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
      不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定），
      用户选择后，针对没时间……**」

    所以这张卡不装时间，只装一个问题 + 两条路：
      · self → 时间他自己定（出候选时段让他勾，或者他直接在「其他时间」里写）；
      · ai   → 他还没想好，由系统去把时间规划出来（`plan_todo_slot`）。
    它跟别的提案一样**一个字节都不写库**，只是把"接下来走哪条路"问清楚。
    """
    return {
        "kind": "todo_mode",
        "title": title or "待办",
        "date": date or "",
        "minutes": minutes,
        "when": when or "",          # 学生的原话：后面出候选卡/替他规划时还要用
        "hint": hint,
        "summary": f"{title or '待办'}｜还没定时间",
    }


def format_minutes(m: int | None) -> str:
    """60 → "1 小时"，90 → "1 小时 30 分钟"（人话：卡片上写给人看，别写"90 分钟"）。"""
    try:
        m = int(m or 0)
    except Exception:
        return ""
    if m <= 0:
        return ""
    if m % 60 == 0:
        return f"{m // 60} 小时"
    if m > 60:
        return f"{m // 60} 小时 {m % 60} 分钟"
    return f"{m} 分钟"


def todo_mode_proposal(text: str) -> dict | None:
    """要加一件事、但没定时间 → 先问「你自己定，还是我帮你挑」。

    为什么要多这一步，而不是像上一轮那样直接把候选摊出来：
    学生原话是「**不要一上来就询问详细时间，先给弹窗**」。
    他要的是**先分清**这件事属于哪种：
      · 他心里已经有数（"我想周六上午去"）→ 让他自己说，别塞给他一堆候选；
      · 他压根没主意（"你帮我安排"）→ 由系统真的去规划，而不是反过来问他。
    上一版不分这两种，一律先摊候选卡 —— 对一个"我随便，你看着办"的学生，
    摊三个时间段等于又把决定权推回给他了。

    凑不出"要做什么事"（`_pick_title` 判空）→ 返回 None，交给追问分支问他是要加什么事，
    **绝不拿半句话当这件事**（实测「大概一个小时帮我安排时间」被抠成
    「大概小时帮我时间」还挂出了候选卡，就是没有这一关的结果）。
    """
    t = (text or "").strip()
    if not t:
        return None
    title = _pick_title(t)
    if not title or title == "待办":
        return None
    day = _pick_date(t) or ""
    mins = wanted_minutes(t)
    bits = [title]
    if mins:
        bits.append(f"约 {format_minutes(mins)}")
    if day:
        bits.append(f"{day}（{_weekday_name(day)}）")
    hint = ("　· ".join(bits) + "。\n时间你想自己定，还是要我从空档里替你挑一段？")
    return build_todo_mode(title, when=t, date=day, minutes=mins, hint=hint)


def plan_todo_slot(title: str, text: str = "",
                   minutes: int | None = None) -> dict | None:
    """**AI 帮他把时间规划出来**（人话：他说"你帮我挑"，那就真去挑一段排上）。

    学生原话：「**核心是 ai 帮我安排时间，ai 去规划时间，然后这个加入代办**」。
    这是"没时间规划"那条路的正解 —— 不是反问他"你想几点"，而是：

      1. 时长：学生说了就按他说的（「大概一个小时」→ 60 分钟，不多占他的时间）；
         没说才回落到默认 90 分钟。
      2. 哪天：说了哪天就只在那天找；没说就从今天起往后几天找**第一个排得下的**。
         （今天是"此刻+30 分钟"之后才算空，午休和深夜都不排。）
      3. 哪一段：学生提了"上午/下午/晚上"就优先那一档；没说按下→上午→晚上，
         也就是"排件事通常排在下午"。
      4. 一定避开**课程**和**已排的待办**（走 find_free_slots，两边都避）。

    返回一张 `auto=True` 的单条提案，`reason` 里写清**为什么排在这儿**——
    学生看到一个自己没指定的时间，第一反应就是"凭什么"，得让他一眼看懂。

    挑不出来（那天满课、空档都装不下这个时长）返回 None，
    由调用方如实说"这天排不下"，**绝不硬凑一个时间写进日程**。
    """
    t = (text or "").strip()
    name = (title or "").strip() or _pick_title(t)
    if not name or name == "待办":
        return None
    want = minutes or wanted_minutes(t) or AUTO_SLOT_LENGTH
    want = max(5, min(int(want), 8 * 60))
    today = datetime.date.today()
    day = _pick_date(t)
    # 「平时 / 工作日」→ 单条兜底那条路同样只在工作日里找（跟候选卡同一条规矩）。
    _workday_mode = any(w in (t or "") for w in ("平时", "工作日"))
    if day:
        days = [day]
    elif _workday_mode:
        days = [(today + datetime.timedelta(days=i)).isoformat()
                for i in range(7)
                if (today + datetime.timedelta(days=i)).isoweekday() <= 5]
    else:
        days = [(today + datetime.timedelta(days=i)).isoformat() for i in range(6)]
    for d in days:
        # 今天要留出缓冲（"此刻+30 分"之后才算空），不然会给他排一段已经开始的时段
        nb = (_now_minutes() + 30) if d == today.isoformat() else None
        slot = pick_free_slot(d, prefer=t, minutes=min(want, AUTO_SLOT_MINUTES),
                              length=want, not_before=nb)
        if not slot:
            continue
        part = next((n for n, plo, phi in DAY_PARTS
                     if plo <= to_minutes(slot["start"]) < phi), "")
        wd = slot["weekday"]
        # 说清"为什么排在这儿"：学生看到一个自己没指定的时间，第一反应就是"凭什么"。
        # 日期一律写成 9/25（周五）这种短样子——卡片上写 2026-09-25 太长了。
        #
        # ⚠️ 这句话会**原样进确认条**（那是纯文本渲染，不认 markdown），
        #    所以这里一个星号都不能加——加了卡片上就会看到「**1 小时**」这种字面量。
        head = f"{_mmdd(d)}（{wd}）"
        why = (f"{head}{part}这一段是空的（你的课和已经排好的事都避开了）。")
        if minutes or wanted_minutes(t):
            why = f"按你说的 {format_minutes(want)} 排的，{why}"
        return {
            "kind": "todo_add",
            "title": name,
            "date": d,
            "start": slot["start"],
            "end": slot["end"],
            "weekday": wd,
            "minutes": slot["minutes"],
            "auto": True,      # 时间不是学生给的，是系统规划的 —— 卡片上要说清楚
            "reason": why,
            "summary": f"{name}｜{d}（{wd}）{slot['start']}-{slot['end']}",
        }
    return None


# 「这件事怎么定时间」的两条路，学生可能**不点按钮、直接在聊天里回一句**。
# 接不住的后果跟前面那些坑一模一样：掉回模型，模型说"好的已经帮你排啦"，日程里空的。
_MODE_SELF_WORDS = (
    "我自己定", "我自己来", "我自己说", "我有时间", "我有空", "时间我有",
    "我知道时间", "我来定", "我自己挑", "我自己选", "我说个时间",
)
_MODE_AI_WORDS = (
    "你帮我", "帮我定", "帮我挑", "帮我选", "帮我安排", "你定", "你挑", "你选",
    "你决定", "你看着办", "看着办", "随你", "听你的", "你安排", "交给", "都行",
    "随便", "没想好", "还没想好", "想不到", "不知道", "不确定", "没主意",
)


def is_mode_answer(text: str) -> bool:
    """这句是在回答「你自己定 / 我帮你挑」吗（人话：二分卡下面那句补充）。

    判据是**两条路的说法**都认，而且都要够短——长句多半是在说别的事，
    不该被这张卡吃掉（跟 `is_slot_answer` 同一个思路）。
    """
    t = (text or "").strip()
    if not t or len(t) > 24:
        return False
    if any(k in t for k in ("吗", "？", "?")):
        return False
    return any(w in t for w in _MODE_SELF_WORDS + _MODE_AI_WORDS)


def parse_mode_answer(text: str) -> str | None:
    """学生回的那句话选的是哪条路（人话：返回 "self" 或 "ai"）。

    先看 self：他说"我自己有时间"的时候，"有时间"里也含着"我"，
    但反过来把"帮我"当 self 就错了 —— 所以 self 的判据更具体，排在前面。
    """
    t = (text or "").strip()
    if not t:
        return None
    if any(w in t for w in _MODE_SELF_WORDS):
        # "我自己定" 里也可能出现"我自己"+"你帮我"混着说，以更具体的 self 为准
        return "self"
    if any(w in t for w in _MODE_AI_WORDS):
        return "ai"
    return None


def mode_to_card(card: dict, mode: str) -> dict | None:
    """学生在二分卡上选了哪条路 → 出对应的卡片（人话：接下来那张卡长什么样）。

      · "self"（我有时间了）→ **候选时段卡**：几段摊开让他勾，
        几段都不合意在「其他时间」里自己写一句（学生明确要的"自行补充"）。
      · "ai"（还没定，你帮我挑）→ **AI 规划的单条**：系统直接挑一段并说明为什么。

    两条路都凑不出东西（那天真排不下）→ 返回 None，由调用方如实说，
    **绝不硬凑一个时间**。
    """
    title = card.get("title") or "待办"
    text = card.get("when") or title
    if mode == "ai":
        return plan_todo_slot(title, text, card.get("minutes"))
    return todo_slots_proposal(text, max_slots=4)


def slot_is_free(date: str, start: str, end: str) -> bool:
    """这段时间现在还是空的吗（人话：落库前再核一次，别把两件事排到同一个点）。

    为什么不能只信学生勾的那一下：候选是**上一轮**算出来的，中间他可能自己
    又往同一天加了一条；「其他」里自填的时段也可能正好压在他刚勾中的那段上。
    复用 `_span_free` 走同一条判定（它同时避开课程和已排待办），
    省得这里另写一份、漏掉一边。
    """
    s0, s1 = to_minutes(start), to_minutes(end)
    if s0 < 0 or s1 < 0 or s1 <= s0:
        return False
    if _iso_weekday(date) == 0:
        return False
    return _span_free(date, (start, end))


def is_slot_answer(text: str) -> bool:
    """这句是在**挑时间**，还是在**开新单**（人话：候选卡下面那句补充怎么算）。

    两种都得接住，但接法不一样：
      · 「改成晚上七点到八点」「周六下午三点到四点」→ 挑时间，**事情沿用候选卡上那个**；
      · 「周六加个游泳」→ 开新单（换成别的事了），得走正常的加待办链路重新解析。
    判据就是有没有"要加什么事"的意图词/口语写法。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    if any(k in t for k in ("吗", "？", "?")):
        return False
    if any(w in t for w in _ADD_INTENT) or _ADD_TALK_RE.search(t):
        return False
    return _pick_span(t)[0] is not None or _pick_date(t) is not None


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
# 上面那条要求"日期 + 时间段"连在一起写，可学生常常**单说一个日期**——
# 「删掉 2026-09-29 那条」「9月29号那个预约怎么没了」。补两条只认日期的：
_DATE_ONLY_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_CN_MD_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")
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

    # 4) 学生随口一句应答（「好的」「嗯」）或没说清要做什么的话 —— **不摊候选卡**。
    #    以前这里无条件兜底：卡上的"任务名"就是那句原话本身——
    #    实测「你帮我找一个合适的时间」整句被印在卡上当代办名。
    #    学生明确驳回过（原话）：「**要识别啥才是真的事情，不是随便拿那一句话
    #    就去当代办加入日程了**」。判不出事名就如实问他要安排什么，
    #    绝不把半句话当代办名。
    if is_confirmation(msg) or todo_title_of(msg) == "待办":
        return {
            "answer": mock_phrase(persona_key, "ask_thing"),
            "options": [], "awaiting_choice": False,
        }

    # 5) 学生提出一件要做的事 → 查近三天空档，给最多 3 个候选
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
# 口语里最常见的写法是「加个健身」「添一条交电费」「记一笔」——动词和量词连写，
# 词表永远列不全（"加个"漏了，学生说「周三加个健身」就整句掉给模型，
# 模型回一句"已经帮你排好啦"，日程里空空如也）。所以这里用正则兜住"动词+量词"。
_ADD_TALK_RE = re.compile(r"[加添记排]\s*(个|条|件|笔|次|项|一下|一节课|一门|代办|待办)")
# 「我要去吃火锅」「我想去游泳」——口语里连"加/安排"都省掉的第一人称下单。
# 学生实测原话：「我要平时去吃火锅，76分钟」——没有任何"加/安排"字样，整句掉给模型，
# 模型把「平时」错排成单独某一天还只出一条。光认"加"字不够，这里把
# "我要/我想(去)/我打算"也认进来；但**光有开头不接**（"我想问一下"是问答不是下单），
# 必须事名判真（_pick_title 抠得出像样的事）才放行，见 wants_add_todo。
_GO_INTENT_RE = re.compile(r"我(想|要|打算)(去)?")
# 几样"看起来像加待办、其实归别处管"的说法，要在上面那条之前让路
_NOT_TODO_PHRASES = ("加课", "加一门课", "加个课", "加一节", "加门课", "加节课",
                     "添加课程", "导入课表")
# 削完只剩这些的，说明学生**没说要做的事**（"周四加一个"）——
# 它们不是待办名字，是"加"这个动作本身，遇到就当没标题、由追问分支问他要加什么。
_EMPTY_TITLES = (
    "加", "加个", "加一个", "加一条", "添加", "安排", "安排一下", "记一下",
    "记一条", "排一下", "预约", "个", "一个", "一条", "一件事", "待办", "事情",
)
# 括号里框出来的事，多半就是标题本身（学生习惯用 『』「」把任务名框起来）
_QUOTE_RE = re.compile(r"[『「\"“]([^」』\"”]{1,40})[」』\"”]")
_WEEKDAY_RE = re.compile(r"周([一二三四五六日天])")

# ---------- 「这句话里到底有没有一件事」 ----------
# 学生原话：「**要识别啥才是真的事情，不是随便拿那一句话就去当代办加入日程了**」。
# 实测两个翻车现场（把截图里那两句原样喂进去跑出来的）：
#   · 「大概一个小时帮我安排时间」→ 标题被抠成「大概小时帮我时间」，还挂出一张
#     候选卡让他勾时间——可连"要做什么事"都还不知道，挑时间是要往日程里写什么？
#   · 「周三12点到2点我要去吃自助餐，帮我添加」→ 标题变成「去吃自助餐帮我」。
# 病根：旧抠法只会**擦掉**时间词和意图词，擦完剩下什么就当名字用。
# 可剩下的常常是残渣（"大概小时""帮我"），不是一件事。
# 所以擦完还要**判真**：像不像一件事？不像就当作"他还没说清"，去追问，
# 绝不把残渣当成待办名写进日程。
#
# 时长（"大概一个小时"）：既是判真的依据（"小时"不该出现在名字里），
# 也是 AI 规划时间时唯一该尊重的约束——学生说他只要一小时，就别排 90 分钟。
_DURATION_RE = re.compile(
    r"(\d{1,3}(?:\.\d{1,2})?|[一二两三四五六七八九十半]{1,3})\s*(?:个)?\s*"
    r"(小时|钟头|分钟|分|h|min)"
)
# 出现在"名字"里就说明抠到的不是事情，是时间/类别/动作残渣
_TITLE_NOISE = (
    # 时间与时长残渣
    "小时", "钟头", "分钟", "时长", "时间", "几点", "大概", "大约", "左右",
    # 类别词：学生嘴里的"待办/代办/日程"是**类别**，不是事情本身
    "待办", "代办", "日程", "事项", "事情",
    # 只剩动作词：说了"帮我安排"却没说安排什么
    "帮我", "帮忙", "安排", "添加", "加入", "加进", "排一下", "记一下",
)
# 一个名字里总得有个正经字符（汉字/字母/数字）。
# 为什么单列：第三路抠法（标题跟在时段后面）实测会抠出 `**`——
# 「提案已经挂出来了：**周四 08:00~09:00**」里时段后面紧跟的是两个星号，
# 长度够、也不含噪声词，就这么过了判真，卡片上会写「要不要把 ** 排进日程？」
_HAS_WORD_CHAR_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


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
    # 只写了日期、没跟时间段的也要认（"删掉 2026-09-29 那条""9月29号那个预约"）。
    # 缺了这一档，学生说得再清楚也解析不出来，只会收到一句"没找到"。
    m = _DATE_ONLY_RE.search(t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _CN_MD_RE.search(t)
    if m:
        # 只写"9月29号"没写年份 → 按今年算（学生日常都这么说）
        return (f"{datetime.date.today().year}-"
                f"{int(m.group(1)):02d}-{int(m.group(2)):02d}")
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


def wanted_minutes(text: str) -> int | None:
    """学生说了这件事大概要多久吗（人话：「大概一个小时」→ 60，「半小时」→ 30）。

    为什么必须认：学生原话「**大概一个小时帮我安排时间**」，时长是他给出来的
    **全部**信息，也是 AI 规划时间时唯一该尊重的约束。不认的话系统会按默认
    90 分钟去排，卡片上写着"约 90 分钟"——学生只会觉得它压根没听自己说话。

    认不出来返回 None（由调用方回落到默认时长），**不瞎猜一个数字**。
    """
    t = _normalize_clock(text or "")
    m = _DURATION_RE.search(t)
    if not m:
        return None
    raw, unit = m.group(1), m.group(2)
    if raw == "半":
        val = 0.5
    elif re.fullmatch(r"\d+(?:\.\d+)?", raw):
        # 小数也要认（"1.5小时"=90 分钟）。写成 int(raw) 的话正则会去匹配后半截，
        # "1.5小时" 会被读成"5小时"=300 分钟 —— 差着一倍多。
        val = float(raw)
    else:
        n = _cn_num(raw)
        if n is None:
            return None
        val = float(n)
    mins = int(round(val * 60)) if unit in ("小时", "钟头", "h") else int(round(val))
    # 太小（"1分钟"多半是在说别的事）或太大（"300小时"不是一件事的时长）都不认
    if mins < 5 or mins > 600:
        return None
    return mins


def looks_like_thing(name: str) -> bool:
    """这个名字像不像"一件真的事"（人话：能不能当待办名写进日程）。

    学生原话：「**不是随便拿那一句话就去当代办加入日程了**」。
    判据很朴素，但每一条都是实测出来的坑：
      · 太短（1 个字）→ 多半是擦剩下的渣；
      · 里面还有"小时/时间/大概" → 抠到的是时间，不是事；
      · 里面还有"待办/日程/事项" → 抠到的是类别词；
      · 整串就是"帮我/安排/添加"这种动作 → 他说了要加，但没说加什么；
      · 太长（>24 字）→ 那是在说一整句话，不是在说一件事的名字；
      · 一个字都没有、全是符号（`**`）→ 那是排版标记，不是名字。
    不像就当"没说清"，交给追问分支去问，绝不写进日程。
    """
    t = (name or "").strip()
    if len(t) < 2 or len(t) > 24:
        return False
    if t in _EMPTY_TITLES:
        return False
    if not _HAS_WORD_CHAR_RE.search(t):
        return False
    return not any(w in t for w in _TITLE_NOISE)


def _strip_title_noise(text: str) -> str:
    """把一句人话里**不属于事情名字**的东西全擦掉（时间、日期、语气词、动作词…）。

    从 `_pick_title` 里抽出来的，为的是能**一句一句地**擦（见那边的"切段重试"）。
    它只负责擦，**不管擦完像不像一件事**——判真是 `looks_like_thing` 的活。
    """
    t = _normalize_clock(text or "")   # 先把「两点到三点」换成 2:00-3:00，下面的正则才擦得掉
    # 时长整块擦掉（"大概一个小时"）：它既是判真的依据，也不该粘进名字里。
    # 必须在抠日期/钟点之前擦——"一个小时"里的"一"会被后面的中文数字逻辑当成别的。
    t = _DURATION_RE.sub(" ", t)
    t = re.sub(r"(大概|大约|差不多|约|预计|估计|有个|差不多要)(?=\s|$)", " ", t)
    # 「改成周六」「换到晚上七点」这类话里，"改成/换到"是**动作**不是事情的名字。
    # 不擦的话，学生在候选卡下面回一句「改成周六」，标题就会变成「改成健身」。
    t = re.sub(r"(改成|改到|换成|换到|换个时间|换个点|挪到|调整到|调到|重排|改一下|重来)", " ", t)
    # 「第N节课」整块是时间（"在原本第一节课的位置加入健身"里的"第一节课的位置"）——
    # 不擦掉的话标题会变成「原本第一节课的位置健身」，卡片上就是这一长串。
    t = re.sub(r"第\s*[一二三四五六七八1-8]\s*节(课)?(的)?(位置|时候|时间|时段)?", " ", t)
    # 「在…的位置/地方」也是时间状语，整块擦掉
    t = re.sub(r"在[^\s，,。]{0,12}?(的)?(位置|地方|时段)", " ", t)
    # 「合适/合适的时间」是形容词不是事名——「你帮我找一个合适的时间」擦完会剩
    # 「找合适」这种动词残渣，还恰好能过判真。擦掉它，「找工作」这类真名不受影响。
    t = t.replace("合适", " ")
    t = _DATE_RE.sub(" ", t)
    # 单写日期的那两种形式也要擦掉，否则「删掉 2026-09-29 那条」的标题会变成一串日期
    t = _DATE_ONLY_RE.sub(" ", t)
    t = _CN_MD_RE.sub(" ", t)
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
    # 「那你帮我加个健身在周四」这种口语，开头那串语气词得先削掉，
    # 否则待办标题会变成「那你帮我健身」——截图里卡片名就是这副别扭样子。
    # （长词排前面：正则的备选是"先匹配到的算数"，写成 (那|那么) 会先把"那"吃掉。）
    t = re.sub(r"^(那么|那|您|你|咱们|我们|要不|不然|麻烦|就|先|再|请|原本|原来|以前|之前|把)+",
               "", t.lstrip())
    t = re.sub(r"^(我要|我想|帮我|我想让|请帮我|给我|把|帮)\s*", "", t.lstrip())
    for w in _CONFIRM_WORDS:
        t = t.replace(w, " ")
    for w in _ADD_INTENT + ("的安排", "一下", "一个", "里", "在"):
        t = t.replace(w, " ")
    t = re.sub(r"[，,。！!？?、；;：:~～\-—『』「」\"'“”‘’]", " ", t)
    t = re.sub(r"\s+", "", t).strip()
    # ⚠️ 上面那些 replace() 会在开头留下空格，把原来那句 `^(帮我|我要…)` 顶掉
    #    （"周日帮我安排游泳" → " 帮我 游泳" → 首字符是空格，"^" 就匹配不上了），
    #    所以"语气词 + 口语动词"这一轮**必须在去空格之后**再削一遍。
    t = re.sub(r"^(那么|那|您|你|咱们|我们|要不|不然|麻烦|就|先|再|请|原本|原来|以前|之前|把)+",
               "", t)
    t = re.sub(r"^(我要|我想|帮我|我想让|请帮我|给我|把|帮)\s*", "", t)
    # 「加个健身」「记一条交电费」这类口语，动词和量词是连在一起的——
    # _ADD_INTENT 里只有"加一个/添加/预约"这些写法，"加个""添条"漏在外面，
    # 不收拾掉的话待办标题会变成「加个健身」（截图里那种别扭的卡片名就是它）。
    # 留个保险：削完只剩一个字就别削了（"记账""加油打卡"这种是正经标题）。
    _trimmed = re.sub(r"^[加添记]?(?:个|条|件|次|项|一下|一)?", "", t)
    if len(_trimmed) >= 2:
        t = _trimmed
    # 「我要**去**吃自助餐，帮我添加」——擦掉"我要"之后留下一个"去"，
    # 而"去"是趋向动词、不是事情的一部分（留着就成了「去吃自助餐」，
    # 跟最后那句"帮我"叠在一起就是实测的「去吃自助餐帮我」）。
    # 只在开头擦：这样"去健身房"里的"去"没了、但"打卡去"不会被动。
    t = re.sub(r"^(去|来)\s*", "", t)
    # 「帮我」在这类话里首尾都会出现（"**帮我**安排游泳**帮我**"），
    # 旧代码只擦了开头那一个，尾巴上就挂着一个"帮我"（实测标题「去吃自助餐帮我」）。
    # 循环擦：擦一轮开头可能又露出新的收尾。
    for _ in range(3):
        before = t
        t = re.sub(r"^(帮我|帮忙|帮|给我|替我|帮我把|帮我把那个)\s*", "", t)
        t = re.sub(r"(帮我|帮忙|谢谢|谢谢啦|多谢|吧|呗|呢|啊|哦|呀|唉)+$", "", t)
        t = t.strip(" 的了")
        if t == before:
            break
    # 「加入健身**代办**」——"代办/待办"是学生嘴里的类别词（还常写成"代办"），
    # 不是事情本身；留在标题里就会看到「健身代办」这种卡片名。
    # 结尾的"（的）时间"也要剥：管家爱说「帮我安排出去吃饭的时间」，
    # 剥完才是「出去吃饭」。不剥的话 `_TITLE_NOISE` 里的"时间"会把整句否掉，
    # 学生明明说了要干什么，系统却反问他"要安排的是什么事"。
    # ⚠️ 只剥**结尾**：中间的"时间"（比如「时间管理」）不是后缀，留着。
    t = re.sub(r"(的)?(待办|代办|事项|日程|时间)$", "", t).strip("的")[:30]
    # Markdown 的排版符号不是名字的一部分。实测管家爱写
    # 「跟你确认一下这条待办：- 名称：健身」，从粗体里抠出来就成了 `健身**`，
    # 卡片上写着「要不要把 **健身\*\*** 排进日程？」——星号跟着名字一起进门了。
    return t.strip("*`_~ \t")


def _pick_title(text: str) -> str:
    """从一句话里剩下那部分抠出待办标题（人话：把时间、日期、安排这类废话都扔掉）。

    抠完还要过一道 `looks_like_thing`：不像一件事就返回 "待办"（=他没说清），
    由调用方去追问——这是本轮补上的关键一环，见 `_TITLE_NOISE` 上面的说明。

    ⚠️ 为什么要有"**切段重试**"这一档：擦噪声的最后一步是
    `re.sub(r"[，,。！…]", " ", t)` 再 `re.sub(r"\\s+", "", t)`，
    也就是**把逗号换成空格、紧接着又把空格全删掉** —— 两句话于是粘成一整串。
    实测学生说「我要去健身，帮我安排个时间」：
        「我要去健身」+「帮我安排个时间」 → 粘成「去健身帮我个时间」
    判真一看里头有"帮我"（语气词，不是事名的一部分）→ 整句否掉 → 返回"待办"
    → 系统回他「好——**要安排的是什么事**？」。他明明第一句就说了"健身"。
    （截图报障：「我要去健身，帮我安排个时间」→ 管家问"要安排的是什么事"；
      他补一句「你帮我安排」→ **还是同一句追问**，原地打转。）

    所以：整句擦完判不过，就**按标点切回一句句**、逐句擦、逐句判真，取第一个过关的。
    宁可这样，也别把两句话粘成一句、把学生已经说过的事情当成"没说"。
    """
    t = text or ""
    m = _QUOTE_RE.search(t)          # 优先拿『』「」框着的那段，最准
    if m and 1 <= len(m.group(1)) <= 30:
        return m.group(1).strip()
    whole = _strip_title_noise(t)
    if _title_ok(whole):
        return whole
    # 整句不行 → 按标点切成一句句重试（一段脏，不该拖累整句话）
    for piece in re.split(r"[，,。！!？?；;：:\n]+", t):
        piece = (piece or "").strip()
        if not piece:
            continue
        cand = _strip_title_noise(piece)
        if _title_ok(cand):
            return cand
    return "待办"


def _title_ok(t: str) -> bool:
    """擦完的这串能不能当待办名用（没内容 / 只剩类别词 → 不行）。"""
    return bool(t) and t not in _EMPTY_TITLES and looks_like_thing(t)


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
    if any(w in t for w in _NOT_TODO_PHRASES):
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
    if any(w in t for w in _ADD_INTENT) or _ADD_TALK_RE.search(t):
        return True
    # 「我要去吃火锅」「我想去游泳」这类第一人称口语下单（见 _GO_INTENT_RE 的注释）。
    # 三道关：有意图开头、无疑问词（问答不接）、事名判真（光"我想问一下"不接）。
    _name = _pick_title(t)
    if (_GO_INTENT_RE.search(t)
            and not any(k in t for k in ("吗", "？", "?"))
            and _name and _name != "待办"):
        return True
    # 没有意图词，但话里自带"日期/星期 + 起止时间"的也算下单——
    # （比如"确认 2026-09-24 19:00-20:30 背单词"，前端点候选卡回发的是这副模样；
    #   "今天19:00-20:30 复习线性代数"、"周五下午3点20写作业"这种不带安排字样的也该接住）。
    # 带疑问词的不算——那多半是在问课表，不是在下单。
    return bool(_pick_date(t) and _pick_span(t)[0]
                and not any(k in t for k in ("吗", "？", "?")))


def wants_list_todo(text: str) -> bool:
    """判断一句话是不是「读我已有的待办」（人话：纯读，不写库、不弹窗）。

    跟加/删待办区分开：这句没有任何"加/删"意图，就是想看一眼现在有哪些待办。
    能由系统算死的就别交给模型——模型会**凭空编**一堆"你目前有 3 条待办"
    （其实一个字都没读库）。这里由 main.py 直接 `list_todos()` 真查，查到什么说什么。

    ⚠️ 也兜住「待办显示不出来 / 代办看不了」这类——学生不是说某一件不见了，
    是整张列表他看不见，最有用的是把待办念给他听，而不是回一句"确实还没有"。
    这条判据只认"待办/代办"整体，不碰具体事名（"健身显示不出来"归「看不见 X」那条）。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    return any(k in t for k in (
        "我的待办", "我的代办", "我有哪些待办", "我有哪些代办",
        "有哪些待办", "有哪些代办", "待办列表", "代办列表",
        "待办清单", "代办清单", "看看我的待办", "看看我的代办",
        "待办都有啥", "代办都有啥", "待办在哪", "代办在哪",
        "待办显示不出来", "代办显示不出来", "待办显示不出", "代办显示不出",
        "待办看不了", "代办看不了",
    ))


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


def todo_missing_advice(text: str) -> str:
    """追问时到底该说哪句话（人话：分清"你没说哪天"和"那天真排不进"）。

    为什么单独写这一小段：学生说「周四加个健身」时，系统**已经替他查过空档**了。
    如果那天真排不下，就得如实说"这天满了，换个日子"——
    否则学生会一遍遍补"几点几点"，而问题根本不在时间上，他只会觉得系统在绕他。
    反过来，学生啥都没说清时也别吓唬他"这天满了"。

    ⚠️ 这一档现在只剩**两种情况**会走到（见 main.py 3c）：
      · 学生根本没说要做什么事（连"健身"都没给）→ 问他要加什么；
      · 学生说了哪天、可那天真排不下 → 如实说这天满了。
    "缺时间"已经不再是追问的理由了 —— 那是他自己挑、或者系统替他规划的事。
    """
    t = (text or "").strip()
    date = _pick_date(t)
    has_time = _pick_span(t)[0] is not None
    if date and not has_time:
        if pick_free_slot(date):
            # 有空档却没成提案 → 差的是"要加什么事"（标题）
            return (f"{date}（{_weekday_name(date)}）有空档，就是还不知道要加**什么事**——"
                    f"说一下事情的名字，比如「加个健身」。")
        return (f"{date}（{_weekday_name(date)}）那天实在排不下了，课和待办都占满了。"
                f"换个日子，或者你给个具体钟点，我看看能不能挤一挤。")
    if has_time and not date:
        return "时间有了，还差**哪一天**——说「周二」「明天」这样的一天就行。"
    # 日期和时间都没给：先看看"要做什么事"说了没有，别追问人家已经说过的东西
    name = _pick_title(t)
    if name and name != "待办":
        return (f"**{name}**记下了，就差**哪一天**——"
                f"说「周二」「明天」这样的一天，我就去把空档挑出来。")
    # 到这儿说明他**连要做什么事都没说**（比如「大概一个小时帮我安排时间」——
    # 时长给了、事情没给）。旧版会把抠出来的残渣当成名字念回去
    # （实测念的是「大概小时帮我时间」），或者直接漏出内部标记「加待办缺细节」，
    # 学生看了完全不知道该干嘛。所以这里**只说人话**：把他已经给的信息认下来，
    # 再问那件真正缺的事。
    mins = wanted_minutes(t)
    if mins:
        return (f"好——时长 **{format_minutes(mins)}** 我记下了，"
                f"就是还不知道要**安排什么事**。\n"
                f"说个名字就行，比如「游泳」「健身」「交电费」，我接着去帮你挑时间。")
    return ("好——**要安排的是什么事**？\n"
            "说个名字就行，比如「游泳」「健身」「交电费」。"
            "时间你还没定的话，我也可以替你挑一段。")


def auto_todo_proposal(text: str) -> dict | None:
    """学生只说了"哪天"、没说"几点"——由系统替他把时间想出来，凑成一张完整提案。

    这就是被截屏投诉的那一幕（原话：「**没有帮我想时间，是我问了才说的**」）：
    学生说「那我加一个健身在周四」，正确做法是
      「我看了周四的空档，15:40-17:10 空着，就把健身排这儿了，点【确认加入】」
    而不是回一句「几点到几点？」把活儿推回给学生——
    他正是因为不知道哪天哪会儿有空才来问你，你把问题退回去，等于没帮上忙。

    所以这里：拿标题 + 日期 → 查那天的空档 → 挑一段（pick_free_slot）→ 拼成提案。
    任何一块凑不出来（没日期、没标题、那天真排不进）都返回 None，
    交给原来的追问逻辑如实说缺什么——**绝不硬凑一个时间写进日程**。

    返回的 dict 只是**提案**，本函数不写库。
    """
    t = (text or "").strip()
    if not t:
        return None
    if _pick_span(t)[0]:        # 学生自己说了几点，这不归它管
        return None
    date = _pick_date(t)
    if not date:
        return None
    title = _pick_title(t)
    if not title or title == "待办":
        return None
    slot = pick_free_slot(date, prefer=t)
    if not slot:
        return None
    wd = _weekday_name(date)
    return {
        "kind": "todo_add",
        "title": title,
        "date": date,
        "start": slot["start"],
        "end": slot["end"],
        "weekday": wd,
        "minutes": slot["minutes"],
        "auto": True,           # 时间不是学生给的，是系统挑的 —— 话术里要说清楚
        "summary": f"{title}｜{date}（{wd}）{slot['start']}-{slot['end']}",
    }


def propose_todo_tool(title: str, when: str = "", date: str = "",
                      start: str = "", end: str = "") -> str:
    """把"建议"变成一张**能点的确认条**（只读，不写库）。

    为什么需要它：学生问"哪天有空复习高数"，Agent 找完空档会给出建议，
    可它手里一个写入工具都没有——于是它就在**文字里**自己写一句
    「好，那我按这个出个提案：- 任务：健身 - 时间：周一 16:30~18:00」，
    学生回「可以」之后，界面上**连个【确认】按钮都没有**（截图里就是这个）。
    给它一张"纸"：提案由系统生成，学生点了按钮，后端才写库。

    参数都能吃学生口语：when 传「周一 16:30~18:00」「明晚七点到八点」都行。

    ⚠️ 关键一档：**只给了哪天、没给几点 → 时间由系统自己挑，不报错**。
    学生说「周四加个健身」，他要的就是"哪天哪会儿空着"这件事本身；
    要是这里回一句"开始时间没解析出来，请补 start"，模型转头就会去问学生
    「你想几点？」——学生只能瞎报一个，或者干脆放弃（投诉原话就是
    「**没有帮我想时间，是我问了才说的**」）。
    所以这一档直接查那天空档、挑一段（pick_free_slot），把确认条挂出来。
    真挑不出来（那天满课）才返回带 hint 的 JSON —— 绝不瞎凑一个时间。
    """
    blob = " ".join(x for x in (date, when, start, end) if x)
    day = _pick_date(blob) or _pick_date(f"{date} {when}")
    begin, finish = _pick_span(blob)
    if not begin and start:
        begin, _ = _pick_span(start)
    if not finish and end:
        # ⚠️ 一个孤零零的钟点（"17:30"）在 `_pick_span` 里算**起点**，不是终点
        # （它返回 `("17:30", None)`）。所以写成 `_, finish = _pick_span(end)`
        # 会让 finish 拿到 None，然后被下面那行"没给结束时间"的兜底接手，
        # 静默改成 begin + 60 —— **学生要的时长就这么被悄悄砍了**。
        #
        # 实测现场：模型调 propose_todo_tool(title="游泳", date="周二",
        # start="16:00", end="17:30")，挂出来的卡片却是 16:00-17:00，
        # 而模型照着卡片给学生解释了一句"系统这边默认按 1 小时出的条"——
        # 把一个 bug 说成了设计。学生要的是 90 分钟，卡片上只有 60。
        end_begin, end_finish = _pick_span(end)
        finish = end_finish or end_begin
    name = (title or "").strip().strip("\"'“”‘’『』「」") or "待办"
    if not day:
        return json.dumps({
            "error": "日期没解析出来",
            "hint": ("date 传 '2026-09-28' 或 '周一'/'明天'。"
                     "**只缺时间不要紧**：日期给全了，时间会由系统从那天的空档里挑。"),
            "got": blob,
        }, ensure_ascii=False)
    auto_slot = False
    if not begin:
        # 学生只说了"哪天"、没说"几点" —— 由系统从那天的空档里挑一段
        slot = pick_free_slot(day, prefer=blob)
        if not slot:
            return json.dumps({
                "error": f"{day}（{_weekday_name(day)}）实在排不进了",
                "hint": (f"那一天的空档都不够排一件事。如实告诉学生'这天排满了'，"
                         f"让他换个日子，或给个具体钟点你再看。**不要反问他想几点**。"),
                "got": blob,
            }, ensure_ascii=False)
        begin, finish, auto_slot = slot["start"], slot["end"], True
        if not finish:
            finish = to_hhmm(to_minutes(begin) + 60)
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
    if auto_slot:
        prop["auto"] = True
    human = (f"确认条已经挂在下面了：《{name}》{day}（{_weekday_name(day)}）"
             f"{begin}-{finish}。点【确认加入】才会写进日程；"
             f"你只要说一句『你可以点确认条上的按钮，或直接回确认』，"
             f"**禁止说已经写好了**。")
    if auto_slot:
        human += ("（这个时间段是你替他从那天的空档里挑的，说清楚『我看 XX 空着，就排这儿了』，"
                  "并补一句『这个点不合适就说得改到几点』。）")
    return json.dumps({"__proposal__": prop, "human": human}, ensure_ascii=False)


# 管家自己那句"提案"长这样（截图里学生遇到的那种）：
#   好，那我按这个出个提案：
#   - 任务：健身
#   - 时间：周一 16:30~18:00
#   - 范围：本周
#   提案这就发给你，点一下【确认】就入库了。
#
# ⚠️ 它也可能**挤成一行**（模型经常把列表压成一整段话）：
#   「…：- 任务：健身 - 时间：周四 15:40~17:10 - 范围：本周 点【确认】就入库了。」
#   所以捕获组里必须把「-」「：」也当分隔符排掉，否则标题会变成
#   「健身 - 时间：周四 15:40~17:10 - 范围：本周」——卡片上就是这一长串。
#   注意"时间"那条**不能**排掉「~」：15:40~17:10 里的波浪号是时段本身的一部分。
_REPLY_TASK_RE = re.compile(
    # 「名称」是实测漏掉的一个（管家爱写「- 名称：健身 - 时间：周一 07:00~08:00」，
    # 就因为没有这个词，第一档整条对不上，只好退给散文抠法，标题还带了两个星号）。
    r"(?:任务|事项|标题|名称|安排)\s*[：:]\s*([^\n，,。；;：:｜|\-—]{1,30})")
_REPLY_TIME_RE = re.compile(r"(?:时间|时段|几点)\s*[：:]\s*([^\n。；;｜|\-—]{1,40})")

# —— 散文式方案的抠法（它这次没按上面那个格式写）——
#
# 实测它写过这么一段（学生截屏投诉：「**没有代办页**…没有出现弹窗」）：
#   好的，帮你把「游泳」加到周一待办里，时间就按咱们说的 16:00~17:30 来。
#   确认一下：**周一 16:00 游泳（1.5 小时）**，对吧？你回个「确认」，系统就入库了。
# 任务名、日期、时段**三样全在**（`_pick_title` / `_pick_date` / `_pick_span`
# 单独喂进去都能抠出来），可上面的固定格式一个都对不上 →
# 整个方案被判成"捞不出来" → 卡片没挂、库没写，
# 学生下一句「确认」，模型还回了一句"已提交，等系统入库后…"（彻头彻尾的假话）。
#
# 所以补两路抠法：先看引号里的词（模型写方案爱用「」把任务名括起来），
# 再看"把 X 加到/排到"这种口语说法。
_QUOTED_TITLE_RE = re.compile(r"[「『《“\"]([^」』》”\"\n]{2,24})[」』》”\"]")
_TITLE_AFTER_VERB_RE = re.compile(
    r"把\s*(.{2,16}?)\s*(?:加到|加进|加入|加|排到|排进|排上|放进|记到|记进|安排到|安排进)")
# 「标题跟在时段后面」——写日程最自然的一种写法，管家也爱这么写：
#   **周四 08:00~09:00 健身（1 小时）**
# 时段后面紧跟的那几个字就是任务名。
#
# ⚠️ 排除字符里**必须带上括号和引号**：实测漏了会抠出 `」这一条` 这种垃圾 ——
# 原话是「…就会多出「游泳 16:00~17:30」这一条。」，时段后面紧跟的是右引号，
# 不排掉它，标题就变成 `」这一条`，还一路通过了 `looks_like_thing`（长度够、不含噪声词）。
_TITLE_AFTER_SPAN_RE = re.compile(
    r"\d{1,2}:\d{2}\s*(?:到|至|-|~|～)\s*\d{1,2}:\d{2}[\s*`_]*"
    # 时段和名字之间可能夹着 Markdown 的粗体/代码符号，两种写法都得吃下：
    #   「**周一 07:00~08:00 健身**」（星号包整段，时段后面是空格）
    #   「07:00~08:00 **健身**」  （星号紧贴名字）
    # 所以前面那截用 `[\s*`_]*` 先啃掉，而名字本身不许再含这些符号。
    r"([^（）()「」『』《》“”\"'，,。；;：:｜|、*`_\s\n]{2,12})")

# 引号里这些词**不是**任务名（"你回个「确认」"里的「确认」）。
# 单独列一份，不并进 `_TITLE_NOISE`：那份是拿来判"学生说的这句话算不算一件事"的，
# 而"确认"当待办名确实没意义 —— 只是这儿语境更特殊，独立一份更好读。
_PROSE_TITLE_STOP = ("确认", "确定", "可以", "好的", "好", "取消", "是的", "加入", "行")

# 时段后面紧跟的那几个字，**可能在说提案本身、而不是在说事**。
# 实测原话：「已提交，周四 08:00~09:00 那条稍等生效就好，我这边不直接改数据。」
#   → 第三路抠出来的是「那条稍等生效就好」，长度够、也不含 `_TITLE_NOISE` 里的词，
#     就这么过了判真，卡片上会写「要不要把 **那条稍等生效就好** 排进日程？」
# 这份表只给**第三路**用（引号里的、和"把 X 加到"后面那一段更可信，
# 不该被这份表误伤），判据是"这几个字眼在讲这次操作，不是在讲要做什么事"。
#
# 只收**有实测出处**或一看就不可能当任务名的：宁可少几条，也别误伤真名字。
# （"写好/写完/排好"这类本来想加，可它们能当动词用——「写好后交」「排好队」
#   都是正经待办名，收了会把真事名一起挡掉。）
_SPAN_TITLE_NOISE = (
    "提案", "确认条", "确认", "提交", "入库", "挂出",
    "这条", "那条", "稍等", "生效", "收到", "已经", "以上",
)


def _clean_prose_title(cand: str, extra_noise: tuple = ()) -> str:
    """把引号里那一串洗干净（人话：它有时候连时间一起括进来）。

    实测它写过「**游泳 16:00~17:30**」「健身 - 时间：周四 15:40~17:10 - 范围：本周」
    这种 —— 直接当任务名，卡片上就是一长串。做法：先把时段和零散钟点抠掉，
    再按分隔符切开，取第一个**判得过真**的片段。

    `extra_noise`：这一路自己的额外噪声词（见 `_SPAN_TITLE_NOISE`）。
    """
    cand = _CONCRETE_SPAN_RE.sub(" ", cand or "")
    cand = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}", " ", cand)
    for piece in re.split(r"[-—–－：:｜|、,，。;；\s]+", cand):
        # Markdown 的排版符号不是名字的一部分。管家爱写
        # 「- 名称：**健身** - 时间：周一 07:00~08:00」，抠出来就成了 `健身**`，
        # 卡片上印着「要不要把 **健身\*\*** 排进日程？」——星号跟着名字一起进门了。
        # 所有散文路（引号 / 把X加到 / 时段后紧跟）都从这儿出，一处擦全线干净。
        piece = piece.strip().strip("的了的").strip("*`_~ \t")
        if extra_noise and any(w in piece for w in extra_noise):
            continue
        if looks_like_thing(piece):
            return piece
    return ""


def _pick_title_from_prose(t: str) -> str:
    """从**散文式**的方案里抠任务名（人话：它这次没按格式写，那就照人话抠）。

    三路，按"有多确定"排序：
      ① 引号里的词（「游泳」）—— 管家最常用（可能连时间一起括进来，洗一遍）；
      ② "把 X 加到/排到"（把健身加到周四）；
      ③ 时段后面紧跟的词（`08:00~09:00 健身`）—— 写日程最自然的写法。
    """
    for m in _QUOTED_TITLE_RE.finditer(t or ""):
        cand = (m.group(1) or "").strip()
        if cand in _PROSE_TITLE_STOP:
            continue
        cleaned = _clean_prose_title(cand)
        if cleaned:
            return cleaned
    m = _TITLE_AFTER_VERB_RE.search(t or "")
    if m:
        cleaned = _clean_prose_title(m.group(1))
        if cleaned:
            return cleaned
    m = _TITLE_AFTER_SPAN_RE.search(t or "")
    if m:
        cleaned = _clean_prose_title(m.group(1), _SPAN_TITLE_NOISE)
        if cleaned:
            return cleaned
    return ""


def _make_todo_card(name: str, day: str, begin: str, finish: str) -> dict:
    """把"任务名 + 日期 + 起止"拼成一张能直接落库的待办卡（人话：出卡这步只写一遍）。"""
    if not finish:
        finish = to_hhmm(to_minutes(begin) + 60)
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


def _clean_reply_name(raw: str) -> str:
    """固定格式里「任务：xxx」后面那一串洗干净（去引号、去 Markdown 的排版符号）。

    实测管家写「- 名称：**健身**」——照原样取值就带回来两个星号，
    卡片上印着「要不要把 **健身\\*\\*** 排进日程？」。引号和星号可能交替嵌套
    （`**「健身」**`），所以放一个集合里一起 strip，让它反复剥。
    """
    return (raw or "").strip().strip("\"'“”‘’『』「」*`_~ \t").strip()


def parse_todo_from_reply(text: str) -> dict | None:
    """从**管家自己那句话**里把「任务 + 时间」捞出来（人话：它只说了没挂条，系统替它挂）。

    什么时候用：学生回「可以」/「确认」，但暂存里什么都没有——
    因为管家上一条只是把方案写在文字里，压根没调工具。
    照「确认落空捞回学生原话」的思路，这一次是捞**管家给的方案**。

    两档：
      · 固定格式（"任务/事项/标题：" + "时间/时段："）—— 一直是主力；
      · **散文式**（"帮你把「游泳」加到周一待办里…你回个「确认」"）—— 后补的兜底。

    第二档必须**要求它自称发过提案**（`claims_proposal_sent`）：散文抠法比固定格式
    宽松得多，不加这一关，助手随便答一句「「高等数学」在周一 08:00-09:40 上课」
    都会被当成一张待办提案挂出去。

    两档都对不上就返回 None：宁可不出，也别从闲聊里瞎猜出一个待办。
    """
    t = text or ""
    m_task = _REPLY_TASK_RE.search(t)
    m_time = _REPLY_TIME_RE.search(t)
    if m_task and m_time:
        when = m_time.group(1)
        name = _clean_reply_name(m_task.group(1)) or "待办"
    else:
        if not claims_proposal_sent(t):
            return None
        name = _pick_title_from_prose(t)
        if not name:
            return None
        when = t        # 日期和时段直接从整句里找
    day = _pick_date(when) or _pick_date(t)
    begin, finish = _pick_span(when)
    if not day or not begin:
        return None
    return _make_todo_card(name, day, begin, finish)


# 「管家自称把提案发出去了」的说法（人话：它说挂出来了，可界面上一个按钮都没有）。
#
# 为什么需要这么一关：模型偶尔会**不调工具**，只在文字里念一段方案，
# 甚至一本正经地说"提案已发出，请点确认条上的【确认】"——界面上什么都没有。
# 学生看到的就变成"我说了要加、它说发了、可我没收到弹窗"（截屏投诉原话）。
# 它自称发了 = 它本来打算给一张卡 → 系统就照它的意思把这张卡补出来。
_CLAIM_PROPOSAL_RE = re.compile(
    r"提案(?:已经|已)?(?:发|挂|生成|出)"
    r"|确认条(?:已经|已)?(?:挂|放|出)"
    r"|点(?:一下|击)?【?确认"
    r"|回个?「?确认」?"
    r"|就(?:能|会|可以)?入库"
    # 「提案好了，就一条：」/「系统会帮你入库」/「回我一句「确认添加」就行」——
    # 这三种说法一个字段都不沾上面那几条（实测踩过）。
    # "入库"索性按整词算：在这套代码的语境里它只出现在提案/确认相关的话里。
    r"|提案(?:已经|已)?好"
    r"|入库"
    r"|确认添加"
    # 过去式的假话：它说"已经提交/入库/加进去了"，可界面上什么都没有。
    # 实测原话：「已提交，等系统入库后周一待办里就会多出「游泳 16:00~17:30」这一条。」
    # 这类比"我这就发提案"更坏 —— 学生看完就等着，什么也不会发生。
    r"|(?:已经|已)(?:提交|入库|加入|写入|排好|安排好|放进)")


# 一句里带**具体时段**（08:00~09:00 这种）。用来判断"它这段话是在讲一件排好的事"，
# 而不是在解释流程（"你点确认后系统才会入库"这种就没时段）。
_CONCRETE_SPAN_RE = re.compile(r"\d{1,2}:\d{2}\s*(?:到|至|-|~|～)\s*\d{1,2}:\d{2}")


def claims_proposal_sent(text: str) -> bool:
    """判断管家这句话是不是在"自称提案已经好了"（人话：它说发了、甚至说提交了）。

    三种都算：**将来时**（"我这就把提案发出来"）、**现在时**（"提案好了"）、
    **过去时**（"已提交，等系统入库"）。越往后越坏 ——
    过去时那种学生看完就等着，接口那边其实一张卡都没挂。
    """
    return bool(_CLAIM_PROPOSAL_RE.search(text or ""))


def has_concrete_span(text: str) -> bool:
    """这句话里有没有**具体时段**（`08:00~09:00`）。

    用来分清"它这段话是在讲一件已经排好的事"和"它在解释流程"：
    后者（"你点确认后系统才会入库"）没有时段，不该被当成假话去纠正 ——
    纠正是有代价的（会把一段本来没问题的回答换掉）。
    """
    return bool(_CONCRETE_SPAN_RE.search(text or ""))


def rescue_proposal_from_reply(text: str, add_req: str = "") -> dict | None:
    """模型这轮没调工具 → 系统替它把该有的卡片补出来（人话：别让它"嘴上说发了"）。

    跟 `parse_todo_from_reply` 的分工：
      · 那个只干一件事——把**固定格式的方案**从文字里捞成提案，权限最小；
      · 这个多两道判断，专给"模型那一路"用：
        ① 它得**自称发过提案**（`claims_proposal_sent`）。没自称就多半在闲聊
           或举例，硬挂一张卡反而吓人；
        ② 正文里捞不出方案时，才往"第一站"退：先二选一卡 → 再候选卡 →
           最后才"替它挑一段"（`auto_todo_proposal`）。

    返回一张卡（`todo_add` / `todo_mode` / `todo_slots`）或 `None`。
    分辨是不是"从它文字里捞的"：捞出来的 `todo_add` **不带 `auto`**，
    系统自己挑的那张带 `auto: True`（`propose_todo_tool` 与 `plan_todo_slot` 同一个约定）。
    """
    t = (text or "").strip()
    if not claims_proposal_sent(t):
        return None
    p = parse_todo_from_reply(t)
    if p is not None:
        return p
    # 它那段话格式认不出、可**学生原话里有任务名**（"帮我加个健身"）——
    # 那就把两边的信息合起来：**任务名用学生的，时间用它给出来的**。
    # 人也是这么分工的：管家负责把时间定下来，事名是学生自己说的。
    # （实测那段「提案好了，就一条：**周四 08:00~09:00 健身（1 小时）**」里，
    #   时段有、日期有，就是格式恰好落在旧抠法的盲区。）
    if add_req:
        name = _pick_title_from_prose(t) or _pick_title(add_req)
        day = _pick_date(t) or _pick_date(add_req)
        begin, finish = _pick_span(t)
        if not begin:
            begin, finish = _pick_span(add_req)
        if name and name != "待办" and day and begin:
            return _make_todo_card(name, day, begin, finish)
    if not (add_req or "").strip():
        return None
    for maker in (todo_mode_proposal, todo_slots_proposal, auto_todo_proposal):
        card = maker(add_req)
        if card is not None:
            return card
    return None


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


def is_add_todo_answer(text: str) -> bool:
    """判断这句是不是在**回答**"某天/某点"的追问（人话：刚问过，他答了一句）。

    跟 is_add_todo_followup 的差别：这个也认"只补了日期"的回答（"周四"）。
    为什么要区分：追问问的可能是**日期**（"这要说哪一天？"），
    学生答一句"周四"就没有时间段——旧判据只认"有时间没日期"，
    这一句会被漏到模型那边去，又是一句"已经帮你排好啦"。
    带疑问词的不算（那多半是在问课表，不是在回答）。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    if any(k in t for k in ("吗", "？", "?")):
        return False
    return _pick_span(t)[0] is not None or _pick_date(t) is not None


# 学生嫌系统挑的点不合适、要求换时间的说法（人话："改成七点""挪到周六"）
_RETIME_WORDS = ("改成", "改到", "换成", "换到", "换个时间", "换个点", "挪到",
                 "调整到", "调到", "重排", "改一下", "重来")


def wants_retime_todo(text: str) -> bool:
    """判断这句是不是"换时间/换天"（人话：上一条确认条的点他不满意）。"""
    return any(w in (text or "") for w in _RETIME_WORDS)


def retime_todo_proposal(card: dict, text: str) -> dict | None:
    """学生嫌系统挑的时间不合适、自己报了个点 → **沿用原卡**换时间重出条。

    为什么必须有这一条：系统替他挑时间时，话里说的是"这个点不合适你说个点就行"。
    他真说了（"下午两点到三点"），就得接住——不然那句话等于放空炮，
    学生会觉得"我说了它当没听见"。

    两种换法都认：
      · 只说时刻（"下午两点到三点"、"改成晚上七点到八点"）→ 换时段，日期不动；
      · 说了一个新日子、没说时刻（"改成周六"）→ 在那个新日子上重新挑一段空档。
    标题永远沿用原卡（他没说要换事，只说时间不对）。

    认不出来的返回 None，交给别的分支处理——绝不硬猜。
    """
    if not isinstance(card, dict) or card.get("kind") != "todo_add":
        return None
    t = (text or "").strip()
    if not t:
        return None
    begin, finish = _pick_span(t)
    new_date = _pick_date(t)
    old_date = card.get("date") or ""
    date = new_date or old_date
    if not date:
        return None
    title = card.get("title") or "待办"
    wd = _weekday_name(date)
    if begin:
        if not finish:
            finish = to_hhmm(to_minutes(begin) + 60)
    elif new_date and new_date != old_date:
        # 只换日子、没说几点 → 在那个新日子上重新挑一段空档
        slot = pick_free_slot(date, prefer=t)
        if not slot:
            return None
        begin, finish = slot["start"], slot["end"]
    else:
        return None
    if to_minutes(finish) <= to_minutes(begin):
        return None
    return {
        "kind": "todo_add",
        "title": title,
        "date": date,
        "start": begin,
        "end": finish,
        "weekday": wd,
        "minutes": to_minutes(finish) - to_minutes(begin),
        "retimed": True,        # 换过时间 —— 话术里要说清"按你说的改成了…"
        "summary": f"{title}｜{date}（{wd}）{begin}-{finish}",
    }


# ============================================================
# 删待办：跟删课同一套规矩——先列清楚，再动手，学生点头才算数
# ============================================================
#
# 为什么单独写一段：删待办和"加待办"共用一堆词，但风险完全相反——
# 加错了顶多多一条安排，删错了**找不回来**。所以这一段的判定宁可收着点：
#   · 唯一命中才出确认条；
#   · 命中好几条（或学生只说"明天那条"）就**列清单**问是哪一条，绝不挑一条删了；
#   · 说的是课（"去掉周二的高数"）立刻让路给课表那条分支。
# 而且执行权不在模型手里：写工具 remove_todo 已经从工具箱里撤掉了，
# 模型最多只能拿到一张提案，真正落库由系统在"学生点头"之后执行。

# 删待办的意图词。比删课多几个口语说法（"取消那条安排""不想做了"）。
_REMOVE_TODO_INTENT = (
    "删掉", "删除", "删了", "去掉", "移除", "取消", "撤销", "退掉", "不想做", "别安排了",
)

# 学生明说这几个词，就是在说待办（而不是课）——哪怕话里带了星期几。
_TODO_WORDS = ("待办", "日程", "事项")

# 候选清单里最多列几条（列一屏出来反而看不清）
_REMOVE_LIST_CAP = 6


def _pick_todo_name(text: str) -> str:
    """从"把周二那条游泳的待办删掉"里抠出「游泳」（人话：待办名的抠法）。

    跟 _pick_title 是亲戚，但擦的词不一样：删待办的句子里有"那条""待办""取消"
    这类词，_pick_title 不认识，会原样留在名字里，导致后面匹配不上真正的待办。
    """
    t = text or ""
    m = _QUOTE_RE.search(t)             # 『』「」框起来的优先，最准
    if m and 1 <= len(m.group(1)) <= 30:
        return m.group(1).strip()
    t = _normalize_clock(t)             # 「两点」这类中文报时先换成数字，免得粘在名字里
    t = _DATE_RE.sub(" ", t)
    t = _DATE_ONLY_RE.sub(" ", t)       # 同上：单写的日期别粘进名字里
    t = _CN_MD_RE.sub(" ", t)
    t = re.sub(r"(下|本|这|上)?周[一二三四五六日天]", " ", t)
    for w in ("大后天", "后天", "今天", "今晚", "今夜", "明天", "上午", "下午",
              "晚上", "中午", "早上", "夜里", "周末", "下周", "本周", "这周",
              "上周", "下个", "这个", "那个", "那天", "这天", "当天", "那一天",
              "这一天", "当天的"):
        t = t.replace(w, " ")
    for w in _REMOVE_TODO_INTENT + _REMOVE_INTENT + ("待办", "日程", "事项", "安排",
                                                    "那条", "这条", "那一条", "这一条",
                                                    "一条", "一下", "一下下", "的"):
        t = t.replace(w, " ")
    t = re.sub(r"^(我要|我想|帮我|请帮我|请|给我|把|别|我)\s*", "", t)
    t = re.sub(r"[，,。！!？?、；;：:~～\-—『』「」\"'“”‘’\s]", "", t)
    return t.strip()[:30]


def _todo_name_match(want: str, title: str) -> bool:
    """待办名对不对得上（人话：含糊匹配，「游泳」能对上「游泳锻炼」）。"""
    a, b = (want or "").strip(), (title or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _pick_todo_index(text: str) -> int | None:
    """认出"第2条/第二个/最后一条"（人话：列完清单后学生用序号点名）。"""
    t = (text or "").strip()
    if not t:
        return None
    if "最后一条" in t or "最后一个" in t or "最后那条" in t:
        return -1
    m = re.search(r"第\s*([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:条|个|项)", t)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _cn_num(raw)
    return n if n and 1 <= n <= 20 else None


def wants_remove_todo(text: str) -> bool:
    """判断一句话算不算「删掉一条待办」（确定性分支用的开关）。

    让路规则（顺序要紧，先让出去再谈接住）：
      · 清空整张课表 → 那是另一条分支的事；
      · 删课（"去掉周二的高数"）→ 归课表那条路，**别拿待办去套**（规格第 3 条）；
      · 加课/加待办 → 那是新增，不是删除。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if not any(w in t for w in _REMOVE_TODO_INTENT):
        return False
    if wants_clear_timetable(t):
        return False
    # 删课让路给课表那条分支（"去掉周二的高数"）——但学生**明说了待办/日程**的话，
    # 这句就是待办，别让出去（"把周二那条游泳的待办删掉"里也有"周二"）。
    if wants_remove_course(t) and not any(w in t for w in _TODO_WORDS):
        return False
    if wants_add_course(t) or wants_add_todo(t):
        return False
    return True


def is_remove_todo_followup(text: str) -> bool:
    """判断这是不是对"你要删哪一条"的回答（人话：清单列完了，学生点名）。

    比如学生回「游泳那条」「第二条」「就是交电费那个」——这些句子本身
    没有"删除"两个字，按 wants_remove_todo 的规矩不算下单，可它就是在回答我们。
    不认它，学生点完名又掉回大模型，模型只能瞎聊。
    """
    t = (text or "").strip()
    if not t or len(t) > 30:
        return False
    if any(w in t for w in _REMOVE_TODO_INTENT):   # 又带了删除词，那是新的一句下单
        return False
    if _pick_todo_index(t) is not None:
        return True
    name = _pick_todo_name(t)
    if not name:
        return False
    return any(_todo_name_match(name, x.get("title", "")) for x in list_todos())


def resolve_todo_remove(text: str) -> dict:
    """把"删掉周二那条游泳的待办"解析成**具体哪一条待办**（只读，一个字节都不删）。

    返回：
      {"status": "ok",        "proposal": {...}, "todos": [...]}  恰好一条，可以出确认条
      {"status": "many",      "todos": [...]}                     好几条，得让学生挑
      {"status": "empty",     "todos": []}                        这一天/这个名字没有待办
      {"status": "unclear",   "todos": [...]}                     没说清是哪条
    """
    t = (text or "").strip()
    day = _pick_date(t)
    name = _pick_todo_name(t)

    # 先把池子缩小：说了日期就只看那天，说了名字就再按名字筛
    pool = list_todos(day) if day else list_todos()
    by_name = bool(name) and name not in ("", "待办", "日程", "事项")
    if by_name:
        hits = [x for x in pool if _todo_name_match(name, x.get("title", ""))]
        # 名字没命中，但整句话里出现了某条待办的名字（"帮我把交电费取消了吧"），
        # 兜一层：从**全部**待办里找（学生可能没提日期，或日期说得不准）
        if not hits:
            hits = [x for x in list_todos() if _todo_name_match(name, x.get("title", ""))]
        if not hits:
            return {"status": "empty", "todos": [], "day": day, "name": name}
        pool = hits

    if not pool:
        return {"status": "empty", "todos": [], "day": day, "name": name}
    if len(pool) == 1:
        return {"status": "ok", "proposal": _todo_remove_proposal(pool[0]), "todos": pool}
    # 剩下好几条：只有"点名了名字"才算唯一命中（名字已经筛过了），
    # 否则就是学生没说清（"删掉明天那条"而明天有两条）——绝不猜
    return {"status": "many", "todos": pool, "day": day, "name": name}


def _todo_remove_proposal(item: dict) -> dict:
    """把一条待办包成一张"待删除"提案（人话：原样念一遍，等学生点头）。"""
    date = item.get("date", "")
    return {
        "kind": "todo_remove",
        "todo_id": item.get("id", ""),
        "title": item.get("title", "待办"),
        "date": date,
        "start": item.get("start", ""),
        "end": item.get("end", ""),
        "weekday": _weekday_name(date),
        "minutes": to_minutes(item.get("end", "")) - to_minutes(item.get("start", "")),
        "summary": (f"{item.get('title', '待办')}｜{date}（{_weekday_name(date)}）"
                    f"{item.get('start', '')}-{item.get('end', '')}"),
    }


def parse_remove_todo(text: str) -> dict | None:
    """解析成一张删待办确认卡；不是"恰好一条"就返回 None（交给上层列清单/如实说）。

    返回的只是**提案**——本函数不删任何东西。学生点确认条上的【确认删除】、
    或在聊天框回一句"确认"，才由系统落库。
    """
    res = resolve_todo_remove(text)
    return res.get("proposal") if res.get("status") == "ok" else None


def _todo_line(item: dict, idx: int | None = None) -> str:
    """清单里的一行（人话：原样念给学生听——标题 + 日期 + 时段）。"""
    date = item.get("date", "")
    head = f"{idx}. " if idx else "· "
    return (f"{head}**{item.get('title', '待办')}**"
            f"｜{date}（{_weekday_name(date)}）{item.get('start', '')}-{item.get('end', '')}")


def render_todo_remove_list(todos: list, limit: int = _REMOVE_LIST_CAP) -> str:
    """把候选待办列成一段人话清单（人话：规格第 2 条要的"把清单列出来"）。"""
    shown = todos[:limit]
    lines = [_todo_line(x, i + 1) for i, x in enumerate(shown)]
    if len(todos) > limit:
        lines.append(f"（还有 {len(todos) - limit} 条没列出来，你可以直接说名字。）")
    return "\n".join(lines)


def wants_remove_todo_loose(text: str) -> bool:
    """宽松一档的判定：话里没写"待办"二字，但**精准对上**了一条待办的名字。

    用在「删掉下周三的游泳」这种句子上——它没写"待办"，按 wants_remove_todo
    会因为带了"周三"被判成**删课**，可它对上的明明是一条待办。
    判据收得很紧，免得把课表那条路的东西抢过来：
      · 名字必须**完全相等**（"游泳" == "游泳" 才算；"高数" vs "复习高数" 只是沾边，不算）；
      · 带了"课"字或"第N节"的一律让路（那是在说课）。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if not any(w in t for w in _REMOVE_TODO_INTENT):
        return False
    if wants_clear_timetable(t) or "课" in t or _PERIOD_RE.search(t):
        return False
    name = _pick_todo_name(t)
    if not name:
        return False
    return any(x.get("title", "") == name for x in list_todos())


def resolve_todo_remove_reply(text: str, candidates: list | None = None) -> dict:
    """学生回答"是下面哪一条"时用它（人话：先按序号点名，不然就按名字解析）。

    为什么要认序号：系统上一句刚列了编号清单（"1. 游泳　2. 交电费"），
    学生顺手回一句「第二条」是最自然的答法。不认它，这句又会掉回大模型，
    模型只能瞎猜或者改口说"请你说清楚"。

    :param candidates: 上一轮列给学生看的那几条（按显示顺序），没有就只按名字解析
    """
    t = (text or "").strip()
    idx = _pick_todo_index(t)
    if idx is not None and candidates:
        i = len(candidates) - 1 if idx == -1 else idx - 1
        if 0 <= i < len(candidates):
            item = candidates[i]
            return {"status": "ok", "proposal": _todo_remove_proposal(item),
                    "todos": [item]}
        return {"status": "unclear", "todos": list(candidates), "reason": "序号超出范围"}
    return resolve_todo_remove(t)


def propose_todo_remove_tool(title: str = "", date: str = "", when: str = "") -> str:
    """给模型用的删待办工具——**只读**，最多产出一张确认条，删不了任何东西。

    为什么要把写工具拿走：老版本工具箱里有个真能删的 remove_todo，
    规矩靠提示层"必须先确认"约束着——可规矩写在提示里，模型口语一变就可能绕过它，
    学生没点头就删掉是**找不回来**的。所以照清空课表/删课的做法，把写入口收归系统：
    模型只能出提案，学生点确认条（或回一句"确认"）之后才由系统执行。

    命中不止一条时**不猜**，把候选原样返回，让模型照着列清单问学生（规格第 2 条）。
    """
    blob = " ".join(x for x in (title, date, when) if x)
    res = resolve_todo_remove(blob)
    status = res.get("status")

    if status == "ok":
        prop = res["proposal"]
        return json.dumps({
            "__proposal__": prop,
            "human": (f"确认条已经挂在下面了：《{prop['title']}》{prop['date']}"
                      f"（{prop['weekday']}）{prop['start']}-{prop['end']}。"
                      f"你只要说一句『点下面的【确认删除】就删掉，直接回「确认」也一样』，"
                      f"**禁止说已经删掉了**。"),
        }, ensure_ascii=False)

    if status == "many":
        cand = [{"id": x.get("id"), "title": x.get("title"), "date": x.get("date"),
                 "start": x.get("start"), "end": x.get("end")} for x in res["todos"]]
        return json.dumps({
            "status": "ambiguous",
            "candidates": cand,
            "hint": ("命中不止一条，**不许猜**：把上面这些待办**原样列出来**"
                     "（标题 + 日期 + 时段），问学生'是下面哪一条'，然后停下来等。"
                     "一条都不许删，也不许说已经删了。"),
        }, ensure_ascii=False)

    return json.dumps({
        "status": "empty",
        "hint": ("没找到符合条件的待办。如实告诉学生'没找到'，"
                 "可以用 list_day_todos 看一下那天到底排了什么，"
                 "或者问学生是哪一天哪一条。**不许编一条出来删**。"),
    }, ensure_ascii=False)


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
# "第N节"→ 上课时间：表在文件开头（`_PERIOD_START` / `_PERIOD_SPAN`），
# 排待办和删课共用同一份，改一处两边都生效。

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
    # 明说了"待办/日程/事项"就不是课——否则「把周二那条游泳的待办删掉」会因为
    # 带了"周二"被判成删课，跑到课表那条路上去追问"想删哪一节"（问错人了）。
    if any(w in t for w in _TODO_WORDS):
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

【学生要加一件事、但还没定时间 —— 直接敲定一段，主打效率】
学生原话：「**不需要先问，直接去安排，重复的确认太麻烦，直接敲定结果，主打效率**」。
「周四加个健身」「帮我加个游泳」这类话（说了哪天就只看那天；没说就从今天往后找
第一个排得下的）：查完空档**直接调用 propose_todo_tool 出一张单条确认条**，
时长按他说的来（「大概一个小时」就是 60 分钟，别一律排 90 分钟），
卡片上写清为什么排在这儿——然后停下来，等他点【确认加入】。
⛔ 不要问他"你自己定还是我帮你挑"，也不要反问"你想几点"（学生投诉原话：
  「**没有帮我想时间，是我问了才说的**」）。他嫌点不合适，会自己报新时间。
· 他想自己挑 → 一句"想自己挑时间就说一声，我列几段给你勾"；
  他真说"我自己挑"时，再用 propose_todo_slots_tool 一次列 2~4 段（能打勾的卡片），
  文字里也列一遍，并说清"都不合适就在「其他时间」自己写一个"。
· 那天真排不下 → 如实说"这天满了"，别硬凑一个时间。
⛔ **不许拿他那半句话当代办名挂出去**。学生原话：「**要识别啥才是真的事情，
  不是随便拿那一句话就去当代办加入日程了**」。实测「大概一个小时帮我安排时间」
  被抠成「大概小时帮我时间」还挂出了候选卡——那是残渣，不是一件事。
  判不出事名（只有时长、只有"帮我安排"、只有"你帮我找个时间"）
  → **只问"要安排什么事"**，别出卡。

【场景边界 —— 学生自主 vs 工单上报】
- 学生的日程操作（加待办、增减课、查空档、改时间）**全部默认学生自主操作**：
  直接处理他本人的待办/课表提案，**不需要上报管理端，也绝不说"权限不足"**。
- 只有学生**主动**说要找管理员协助（比如快递丢失、外卖遗失并要求上报）才走工单；
  他没主动提就只在对话里聊，不推给管理端。
- ⛔ 你永远不直接写数据库：所有增删改由后端在学生点【确认】之后执行。
  严禁谎称"已经改好了/已经加上了"。
- 确认写入成功后，前端会自动刷新课表/待办面板，不用提醒学生手动刷新。
- 说话简洁自然，直接给方案，去掉多余引导提问。

【学生说"没看见/日程里没有 X" —— 核实的事交给系统，你不编原因】
这种话多半到不了你这儿：系统会先真查库，查到了直接告诉他在哪儿，没查到就把
确认条补上。真落到你这儿（他没点名是哪件事），你只做两件事：
问清是哪一条，或者按他最近说过的事直接出提案卡。
⛔ **禁止拿"系统/推送/管理端"当原因**（实测反面教材，学生截图投诉）：
  "基本可以确定是系统推送出了问题""得让管理端那边看一下""我帮你反馈给管理端"
  ——你看不到他的屏幕，也不管数据推送，说这些全是编的；
  "确认条没弹出来"的正确做法是**补一张**，不是诊断原因。
⛔ 不要让他"二选一：再刷新一次 / 反馈管理端"，也不要建议"退出重进、下拉刷新"——
  系统确认写入后本来就会自动刷新，这套打发既啰嗦又不解决问题。

【硬性约束】
- 待办不能跟课程撞时间（工具会自动拦截，但你要先自己看清楚）。
- 一次只推 2-3 个候选，不要甩一长串让人挑花眼。
- 说话简短、具体，直接给时间点，不要长篇分析。
- **学生报了一个"哪天"，就说明他要的是"帮他挑个点"**：查完空档直接出确认条，
  别在文字里只写方案——没有卡片，学生回"确认"时系统不知道你在确认什么。 
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
3. **删待办（学生说"删掉待办""取消那条安排"）**：跟删课是同一套规矩——先列清楚，再动手。
   ⛔ 三条红线，一条都不能破：
      · **你没有任何删除接口**：工具箱里能删待办的写入口一个都没有（这是刻意的）。
        学生没点头就说"已经删了"是骗人——数据还在库里，删错了也找不回来。
      · **不许只说"我需要你确认一下"却不列清单**：学生不知道你在指哪一条，
        这种"确认"等于瞎确认。要删就必须把那条**原样念出来**（标题 + 日期 + 时段）。
      · **不许猜**：学生只说"删掉明天那条"而明天有好几条时，
        必须把候选**原样列出来**问"是下面哪一条"，列完就停。
   三步走：① 学生点名了唯一一条 → 调 propose_todo_remove 出确认条，然后停在"等你点确认"；
   ② 命中好几条 → 用 propose_todo_remove 拿到的候选清单原样列出来问是哪一条，**列完就停**；
   ③ 学生说的是课（"去掉周二的高数"）→ 那归课表，调 propose_course_change，别拿待办去套。
   一句话记住：**删除不可逆，宁可多问一句，也别替学生做主。**
4. **改待办状态**（update_todo_status）：先跟学生确认是哪条、改成什么，再调用。
5. 学生说"改一下课表"却没说怎么改时，先问清楚改哪里，别自作主张。

【工具用法】
- get_weekly_timetable：看整周课程
- find_free_slots(date, min_minutes)：查某天空档
- list_day_todos(date)：看某天已排了什么
- **propose_todo_mode_tool(title, when)：学生要加一件事、还没定时间时，先用它**——
  出"时间你自己定 / 我帮你挑"这张二选一卡片。**这是第一站，别跳过它直接列时间。**
- **propose_todo_slots_tool(title, when)：学生说"我自己挑"时**，用它把几段空档
  （when 留空就跨天各给一段）摊成一张**能打勾**的卡片，学生自己勾一个或几个，
  勾完点【加入日程】才写库。⛔ 别拿它当第一站（第一站是直接敲定的单条）。
- **propose_slots(options_json)：把候选结构化地交给前端渲染成可勾选卡片。
  提完候选后必须调用它**（options_json 是 JSON 数组，每条含 title/date/start/end）。**
- **propose_todo_tool(title, date/when...)：你的第一站**——学生要加事、没定时间时
  就用它出**单条**"要不要排在这儿"的确认条（只给 date 也行，时间它会自己挑，
  并且会在卡片上写清"为什么排在这儿"）。
- add_todo_tool(title, date, start, end, note)：**确认后**才写入
- **propose_clear_timetable()：清空整张课表**——只出确认弹窗，纯只读，写完就停手
- **propose_course_change(op, day, ...)：删课/加课/改单节课的首选**——服务端算好新课表出确认卡
- **propose_timetable_change(courses_json, change_summary)：整表重排/文件导入用**——
  把调整后的完整课表（JSON 数组）交给前端渲染成「确认修改」卡片，学生点确认后系统写入。
  **注意：写入不经过你**，所以卡片确认后不用（也不能）再调任何写入工具。
- **propose_todo_remove(title, date)：删待办**——只出确认条，删不了任何东西；
  命中多条时返回候选清单，照着列出来问学生是哪一条。
- update_todo_status：标记某条待办已完成/未完成
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
        # 出确认条的三件（都是**只读**：真写入永远在系统那侧）：
        #   · propose_todo_mode_tool → 还没定时间，先问「你自己定 / 我帮你挑」  ← 首选
        #   · propose_todo_tool      → 已经定好一个点，出单条"要不要排在这儿"
        #   · propose_todo_slots_tool→ 摊开几段**让学生自己打勾**（学生点了"我自己定"之后）
        # 学生明确要求过"先给弹窗、别一上来就问详细时间"（原话：「**要区分两种，
        # 一种是我有时间规划了，一种是我没有时间规划让他帮我安排，不要一上来就
        # 询问详细时间，先给弹窗**」），所以 mode 卡是这三件里的第一站。
        "propose_todo_mode_tool": propose_todo_mode_tool_tool(),
        "propose_todo_tool": propose_todo_tool_tool(),
        "propose_todo_slots_tool": propose_todo_slots_tool_tool(),
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
        "propose_todo_remove": Tool(
            name="propose_todo_remove",
            description=(
                "**删待办唯一能用的工具**（学生说'把周二那条游泳的待办删掉''取消交电费'时用）。"
                "它只生成一张【确认删除】确认条，**一个字节都不删**——"
                "你也没有任何删除接口可用。title 传学生说的名字，date 传'2026-09-28'或'明天'，"
                "两个都不确定就都留空（工具会告诉你命中了哪些）。"
                "⚠️ 命中不止一条时它不会替你挑，会返回候选清单——"
                "**照清单原样列给学生、问'是下面哪一条'，然后停下来等**，"
                "⛔ 不许猜哪一条、更不许说'已经删了'。"
                "命中唯一时确认条自动挂出，你只需说'点【确认删除】才真的删'。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待办名字，如 游泳；不确定就留空",
                              "default": ""},
                    "date": {"type": "string", "description": "日期，如 '2026-09-28'、'明天'；可空",
                             "default": ""},
                    "when": {"type": "string", "description": "学生原话里的时间描述；可空",
                             "default": ""},
                },
                "required": [],
            },
            func=propose_todo_remove_tool,
        ),
    }


# ============================================================
# 给**所有**模块共用的"只读找时间 + 出待办确认条"工具组
# ============================================================
#
# 为什么要单独导出这一组：学生说「我想周四去健身」时，意图路由可能把这句
# 分给「校园问答」，而那个模块原本文具盒里只有 search_kb —— 它只能在**文字里**
# 写一句「- 任务：健身 - 时间：周四 15:40~17:10 点【确认】就入库了」，
# 界面上一个按钮都没有；学生回「确认」，系统也不知道他在确认什么
#（这一幕被截屏投诉过：「没有确认」）。
#
# 把"查空档 + 出确认条"这两件本事发给每个模块，问题就从根上没了：
# 不管路由把话分给谁，管家都有能耐**替学生把时间算出来、并挂出一张能点的确认条**。
# 这一组工具**一个字节都不写库**（真写入永远是后端的确认条），所以发给谁都安全。
def build_scheduling_tools() -> dict[str, Tool]:
    """「查空档 → 替学生挑时间 → 出确认条」的只读工具组（供各模块共用）。"""
    all_tools = build_tools()
    names = ("get_weekly_timetable", "find_free_slots", "list_day_todos", "propose_slots")
    picked = {n: all_tools[n] for n in names if n in all_tools}
    picked["propose_todo_mode_tool"] = propose_todo_mode_tool_tool()
    picked["propose_todo_tool"] = propose_todo_tool_tool()
    picked["propose_todo_slots_tool"] = propose_todo_slots_tool_tool()
    return picked


def propose_todo_tool_tool() -> Tool:
    """把 propose_todo_tool 包成 Agent 工具（schedule 与本文件共用同一份说明书）。

    单独抽出来是为了**只维护一份描述**：以前这段说明写在 schedule 模块里，
    现在要给所有模块共用，抄两份迟早会走样（改了一处、另一处还是老话术）。
    """
    return Tool(
        name="propose_todo_tool",
        description=(
            "把一个具体任务排进日程（只出提案，不写库）。"
            "title 任务名；when 直接写学生那句时间，如 '周一 16:30-18:00'、"
            "'明天下午两点到三点'；也可以分着给 date（'2026-09-28' 或 '周一'）"
            "和 start/end（'16:30'/'18:00'）。"
            "**学生只说了哪天、没说到几点时，把 date 给上就行，"
            "时间由系统从那天的空档里挑好**——⛔ 不要为了凑 start 去反问学生'你想几点'，"
            "他要的就是'哪天哪会儿空着'这件事本身。"
            "⚠️ 但它只出**一段**时间。学生没说定要哪个点时，优先用 propose_todo_slots_tool "
            "**一次列几段让他自己打勾**；只有在学生已经报了准点、或者只需要一个建议时才用它。"
            "调用成功界面上会挂出【确认加入】按钮，学生点了才写库；"
            "没调用这个工具就不要说'提案已发给你'。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务名称，如 健身"},
                "when": {
                    "type": "string",
                    "description": "时间描述，如 '周一 16:30-18:00'、'明天下午两点到三点'；可空",
                    "default": "",
                },
                "date": {"type": "string", "description": "日期，如 '2026-09-28'、'周一'；可空",
                         "default": ""},
                "start": {"type": "string", "description": "开始时间，如 '16:30'；可空（不给就系统挑）",
                          "default": ""},
                "end": {"type": "string", "description": "结束时间，如 '18:00'；可空（默认一小时）",
                        "default": ""},
            },
            "required": ["title"],
        },
        func=propose_todo_tool,
    )


def propose_todo_mode_tool(title: str, when: str = "") -> str:
    """出「你自己定时间 / 我帮你挑」这张二分卡（只读，不写库）。

    为什么模型也需要它：确定性分支（main.py 3c）已经会在"有事情名、没定时间"时
    自动出这张卡，但路由/措辞总有落进模型手里的漏网之句。要是模型这时候
    直接摊候选卡（或者更糟——反问"你想几点"），同一件事的体验就分成两套，
    而"体验分裂"正是这个项目反复踩的那类坑。所以给它同一件工具。

    :param title: 要加的那件事（"游泳"）
    :param when: 学生那句时间描述，可以是空的
    :return: `__proposal__` 包着的二分卡；凑不出来（连事名都没有）返回 error + hint。
    """
    blob = " ".join(x for x in ((when or "").strip(), (title or "").strip()) if x).strip()
    if not blob:
        return json.dumps({
            "error": "至少给个任务名",
            "hint": "title 传「游泳」这样的事名。⛔ 别拿学生整句话当名字。",
        }, ensure_ascii=False)
    card = todo_mode_proposal(blob)
    if card is None:
        return json.dumps({
            "error": "还不知道要安排什么事",
            "hint": ("`_pick_title` 判不出这件事是什么（学生可能只说了时长、或者整句都是废话）→ "
                     "**问学生要安排什么事**，比如'说个名字就行，比如「游泳」「健身」'；"
                     "⛔ 不要问'你想几点'，也不要把那半句话当待办名挂出去。"),
            "got": blob,
        }, ensure_ascii=False)
    return json.dumps({
        "__proposal__": card,
        "human": (f"二分卡已经挂在下面了：《{card['title']}》。"
                  f"文字里请告诉学生：**时间你自己定**、或者**我替你挑一段**，"
                  f"让他在这张卡上点一下。⛔ 严禁说已经排好了、也别反问他几点。"),
    }, ensure_ascii=False)


def propose_todo_mode_tool_tool() -> Tool:
    """把 propose_todo_mode_tool 包成 Agent 工具（跟别的只读工具共用一份）。"""
    return Tool(
        name="propose_todo_mode_tool",
        description=(
            "出一张**二选一**的卡片：'时间你自己定' / '还没定，你帮我挑'（只出提案，不写库）。"
            "**学生说了要加一件事、但没说定时间时，先用它**——"
            "先分清他是已经有主意、还是想让系统替他安排，别一上来就问他几点。"
            "title 任务名（'游泳'），when 写学生那句时间描述（可空）。"
            "⛔ 学生已经报了准点时改用 propose_todo_tool。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务名称，如 游泳"},
                "when": {"type": "string",
                         "description": "学生那句时间描述，如 '周二'；可空",
                         "default": ""},
            },
            "required": ["title"],
        },
        func=propose_todo_mode_tool,
    )


def propose_todo_slots_tool(title: str, when: str = "") -> str:
    """把"哪几段时间空着"做成一张**能打勾**的候选卡（只读，不写库）。

    跟 propose_todo_tool 的分工（这一条很关键，别混）：
      · propose_todo_tool：已经定好**一个点** → 出一张"要不要排在这儿"的单条确认条；
      · 就是这一件：还没定哪个点 → **摊开几段**让学生自己勾（本轮规格的正解）。
    学生原话：「由 ai 帮我去挑选合适时间，**进行列举**……由我打勾，进行增加」。

    :param title: 要加的那件事（"健身"）
    :param when: 学生那句时间描述（"周四"、"周四下午"、"周六"）；可以空着——
        空着就**跨天**列几段（今天往后逐天一段），一样比他再问一轮快。
    :return: 带 __proposal__ 的 JSON 字符串（引擎据此在聊天里挂出候选条）；
        凑不出来时返回 error + hint，让模型照 hint 去问**正确的那件事**。
    """
    blob = " ".join(x for x in ((when or "").strip(), (title or "").strip()) if x).strip()
    if not blob:
        return json.dumps({
            "error": "至少给个任务名",
            "hint": "title 传「健身」这样的事名；时间不确定就留空，系统会自己列几段。",
        }, ensure_ascii=False)
    card = todo_slots_proposal(blob)
    if card is None:
        return json.dumps({
            "error": "凑不出候选时段",
            "hint": ("两种可能：① 连'要加什么事'都还没说清 → 问学生**要加什么事**，"
                     "⛔ 不要问'你想几点'；② 那几天课和待办都排满了 → 如实说"
                     "'这几天排满了，换个日子'。（若学生已经报了具体钟点，"
                     "改用 propose_todo_tool 出单条确认条。）"),
            "got": blob,
        }, ensure_ascii=False)
    return json.dumps({
        "__proposal__": card,
        "human": (f"候选卡已经挂在下面了：《{card['title']}》共 {len(card['slots'])} 段空档。"
                  f"**要学生在卡片上打勾**，勾完点【加入日程】才写库；"
                  f"文字里也把这几段列一遍，并告诉他'都不合适就在「其他时间」自己写一个'。"
                  f"⛔ 严禁说已经写好了、也别再反问他几点。"),
    }, ensure_ascii=False)


def propose_todo_slots_tool_tool() -> Tool:
    """把 propose_todo_slots_tool 包成 Agent 工具（`build_scheduling_tools` 里共用一份）。"""
    return Tool(
        name="propose_todo_slots_tool",
        description=(
            "把几个候选时段摊开成一张**可打勾**的卡片（只出提案，不写库）。"
            "**学生说了要加一件事、但没指定具体钟点时，用这个**——"
            "title 任务名（'健身'），when 写学生那句时间描述（'周四'、'周四下午'、'周六'），"
            "when 留空也行（系统会从今天起逐天各列一段）。"
            "一次列 2~4 段，学生在卡片上自己勾一个或几个，"
            "勾完点【加入日程】才由系统写库。"
            "⛔ 不要为了凑时间反问学生'你想几点'（他要的就是'哪几段空着'）；"
            "⛔ 学生已经报了准点时改用 propose_todo_tool；"
            "⛔ 没调用工具就不要说'提案已发给你'、更不要说'已经排好了'。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任务名称，如 健身"},
                "when": {"type": "string",
                         "description": "学生那句时间描述，如 '周四'、'周四下午'；可空",
                         "default": ""},
            },
            "required": ["title"],
        },
        func=propose_todo_slots_tool,
    )
