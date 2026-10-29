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

from app import clock      # #56-A
from app.agent.tools import Tool
from app.agent.pending import is_confirmation
from app.store import (
    add_todo, get_timetable, list_todos,
    save_timetable, update_todo,
    REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY, is_past_date, todo_done_on,
    todo_occurs_on, WEEKLY_HORIZON_MONTHS,
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
        # ⚠️ 判"这条还占不占地方"必须问 `todo_done_on(t, 那一天)`，不能看整条 status：
        # 重复/习惯待办的 status 永远是 planned（它的完成按天记在 done_dates 里），
        # 照老写法 `status == "done"` 就永远跳不过它 —— 学生今天已经把单词背完了，
        # 系统还把 21:00-21:30 当成占着、另给他挑别的时间。一次性待办两种写法等价。
        if todo_done_on(t, date):
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
    now = clock.now()
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


def parse_time_spec(text: str) -> dict:
    """**一次**把一句话里的时间信息读全，交给所有下游（人话：别再各读各的）。

    为什么要有这个结构（2026-10-10 重构）：`_pick_date` / `_pick_span` /
    `wanted_minutes` / `weekly_repeat_day` 每一样**单独都能读对**，
    但 `candidate_slots` 只挑了其中一部分用 —— 结果就是学生说「周五晚上七点到九点值日」，
    钟点被丢掉、给他排了 15:40-17:10；说「每周三下午两点开会」，给的 19:00。
    分了层却不合起来用，比不分层更难查：单测每个函数都是对的。
    现在下游一律吃这个 spec，**要么整体传下去，要么整体不用**，
    不再可能出现"日期用了、钟点没用"。

    字段（读不出来一律 None / 空串，不猜）：
      date     点名了具体哪天（ISO）
      weekday  点名了"每周几"（1..7）
      repeat   "" 一次性 / "weekly" 每周重复 / "daily" 每天的习惯（daily 时 weekday 一定是 None）
      start    点名的开始钟点 "14:00"
      end      点名的结束钟点 "21:00"（「七点到九点」才有）
      minutes  点名的时长（「大概三个小时」=180；有 start+end 时按差算）
      part     只说了档没说钟点："上午" / "下午" / "晚上"
      clock    有没有点名钟点（start 非空就是 True）
    """
    t = (text or "").strip()
    # 周期说法要先摘掉：「每周三下午两点」里的"三"会被日期解析读成"周三"以外的东西，
    # 而"每周"更常被 `_pick_date` 当成相对日期（一周后）。摘完剩下的才是"哪天几点"。
    rest = _REPEAT_PHRASE_RE.sub(" ", t)
    start, end = _pick_span(t)
    minutes = wanted_minutes(t)
    if start and end:
        minutes = to_minutes(end) - to_minutes(start)
    spec = {
        "date": _pick_date(rest),
        "weekday": weekly_repeat_day(t),
        "start": start, "end": end, "minutes": minutes,
        "part": _part_said(t),
    }
    # 先 weekly 后 daily —— 这条判据只写在 `repeat_of` 里，这里只问它（红线 11b）
    spec["repeat"] = repeat_of(t)
    spec["clock"] = bool(start)
    # ⚠️ 裸钟点（只说"三点"、没说上午下午）不可信：`_pick_span('三点开始')` 会给出 03:00，
    # 而 03:00 那段**真空着**（凌晨没课），pinned 逻辑就会把学生的作业排到凌晨三点。
    # 7/8 点例外 —— 中文里裸说"七点/八点"几乎都是早上（晨跑、早八）。
    # 1..6 点一律标记为"没听清"：不 pin、不猜，回一句问清楚。
    hour = int(start.split(":")[0]) if start else -1
    spec["clock_ok"] = bool(start) and (bool(spec["part"]) or not (1 <= hour <= 6))
    return spec


def _bare_clock_note(text: str) -> str:
    """裸钟点没听清时那句问话（人话：我不敢替你决定是凌晨还是下午）。"""
    sp = parse_time_spec(text)
    if not (sp.get("clock") and not sp.get("clock_ok")):
        return ""
    hour = int(sp["start"].split(":")[0])
    return (f"⚠️ 你说的「{hour} 点」没说是上午还是下午，我不敢替你定 —— "
            f"照着字面排就成了凌晨 {sp['start']}，那多半不是你的意思。\n"
            f"补两个字（「下午{hour}点」或「早上{hour}点」），我就照你点的那个时间排。")


_PART_WORDS = (("上午", ("上午", "早上", "早晨", "一早", "清晨")),
               ("下午", ("下午", "午后", "傍晚")),
               ("晚上", ("晚上", "夜里", "今晚", "今夜", "晚间", "明晚", "后晚", "昨晚")))
# 「凌晨/半夜/午夜」算**报了档**：学生说「凌晨1点赶火车」就是 01:00，不是"没听清"。
# 归到"晚上"这一档只是为了给三档排序（DAY_PARTS 里没有凌晨档），
# 钟点本身走 pinned —— 它不看档的上下界，只看那段排不排得进。
# （0 点/12 点不用列：`clock_ok` 那条本来就只怀疑 1..6 点。）
_AMPM_EXTRA = ("晚上", ("凌晨", "半夜", "午夜"))
# 「中午」单独处理：它落在 11:00-13:00，跨在上午档和下午档中间，
# 归到"上午"还是"下午"都会让学生觉得"我说的不是这个意思"，所以只认三档本名。
_NOON_WORDS = ("中午", "正午", "午间")


def _part_said(text: str) -> str:
    """学生点的是哪一档（对齐 DAY_PARTS 的三个名字）；没点或说"中午"都回 ""。"""
    t = text or ""
    if any(w in t for w in _NOON_WORDS):
        return ""
    for name, words in _PART_WORDS + (_AMPM_EXTRA,):
        if any(w in t for w in words):
            return name
    return ""


def _day_slots(date: str, prefer: str = "", limit: int = 3,
               rotate: int = 0, length: int = AUTO_SLOT_LENGTH,
               spec: dict | None = None) -> list:
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
    :param spec: `parse_time_spec` 的结果。**学生点名了钟点，那一天就得先给那个钟点**
        ——这是 2026-10-10 修的那个洞：以前 prefer 只用来给三档排序，
        说「周五晚上七点到九点」照样排成 15:40-17:10。排不进（有课/已过点）时
        仍旧退回三档候选，不硬塞，也不偷偷换日子。
    """
    not_before = (_now_minutes() + 30
                  if date == clock.today().isoformat() else None)
    min_needed = length
    free = [s for s in find_free_slots(date, min_minutes=min_needed)
            if s.get("start")]
    out = []
    # ① 先摆学生点死的那个钟点（如果有），它排第一，后面才是"像样的几档"
    pinned = []
    if spec and spec.get("start") and spec.get("clock_ok", True):
        s0 = to_minutes(spec["start"])
        e0 = (to_minutes(spec["end"]) if spec.get("end")
              else s0 + int(spec.get("minutes") or length))
        if e0 > s0 and (not_before is None or s0 >= not_before) \
                and _span_free(date, (spec["start"], to_hhmm(e0))):
            pinned.append({"part": _part_of_minute(s0), "date": date,
                           "weekday": _weekday_name(date),
                           "start": spec["start"], "end": to_hhmm(e0),
                           "minutes": e0 - s0,
                           "label": f"你定的 {spec['start']}-{to_hhmm(e0)}"})
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
    # 学生点死的钟点永远排第一（它不是"候选"，是他自己说的），后面才是替他挑的档
    return (pinned + out)[:limit]


def _part_of_minute(m: int) -> str:
    """某个时刻属于哪一档（跟 DAY_PARTS 同一套名字，别自己另分一段）。"""
    for name, lo, hi in DAY_PARTS:
        if lo <= m < hi:
            return name
    return "上午" if m < 12 * 60 else ("下午" if m < 18 * 60 else "晚上")


def _said_part(text: str) -> bool:
    """学生自己说了"上午/下午/晚上"没有（人话：他点名了就别替他挪顺序）。"""
    t = text or ""
    return any(w in t for w in ("上午", "早上", "早晨", "一早", "中午",
                                "下午", "晚上", "傍晚", "夜里", "今晚", "今夜"))


# ── #76 · 规则层能力清单（这张表就是"我们会算什么"的可执行文档）────────────────
# 为什么摆在这儿：`candidate_slots` 判"平时/周末"用的就是这两份词表，而 #76 的
# "接不住就交给引擎" 也须按**同一份**判 —— 抄第二份迟早走样（红线 11b）：
# 一边加了「双休」另一边没加，就会出现"能算出来却被告警说算不出"，那种错最难查。
_WORKDAY_WORDS = ("平时", "工作日")
_WEEKEND_WORDS = ("周末", "双休")

# **规则层算得出**的说法。加一行 = 学会一类话（不是学一句），永久生效、零成本 ——
# 这正是"学完不变慢"的意思：样本库学的是"这句→这个意图"的映射，只覆盖说过的话；
# 这里划的是**边界**，边界不随句子增多而膨胀。
RULE_CAN_DO = frozenset({
    "今天", "明天", "后天", "大后天",
    "周一", "周二", "周三", "周四", "周五", "周六", "周日",
    "下周", "本周", "这周", "下星期",
    "上午", "中午", "下午", "晚上", "早上", "夜里",
    "每天",                      # 一条 daily 习惯，`unsupported_period` 已放行
}) | frozenset(_WEEKEND_WORDS) | frozenset(_WORKDAY_WORDS)   # ★「周末」这一档是 #75 挣来的

# 规则层算不了、**且现在没有任何机制拦**的说法。收录标准只有一条：
# `unsupported_period()` 拦不到它 —— 拦得到的（每月/下个月/隔天/每个周末…）一律不收，
# 那边有 `_period_reject_response` 的专门话术（"这个我办不了、能办的是哪几种"），
# 比丢给引擎更诚实、更可控。**同一件事不许两处管。**
RULE_CANNOT = ("隔周", "双周", "月底", "季末", "节假日", "寒暑假", "调休")

_UNPARSED_MIN = 3     # 擦完还剩几个字，才算"有内容、但我们没认出来"


def rule_cannot_do(text: str) -> str:
    """原话里有"规则层算不了"的约束吗？返回那个词；没有返回空串。"""
    t = text or ""
    for w in RULE_CANNOT:
        if w in t and w not in RULE_CAN_DO:
            return w
    return ""


def title_unparsed(text: str) -> bool:
    """有实词、却抠不出事名？—— 不是"他没说要加什么"，是"我们没认出来"。

    分界线全靠实测校准（单据 §1.2 那张表里三行的长度是错的，实测见快照）：
      '时间打篮球'    (5 字) → True    ← 有内容，认不出
      '找个时间打篮球' (7 字) → True
      ''             (0 字) → False   ← 真的什么都没说（"帮我安排一下"），该去追问
      '隔周打球'      → **False**（整句过了 `_title_ok`，事名是"隔周打球"）
       ⇒ 这一族靠 `rule_cannot_do` 那一半接住，**不是**这一半。
    """
    if todo_title_of(text) != "待办":
        return False
    return len(_strip_title_noise(text)) >= _UNPARSED_MIN


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
    today = clock.today()
    # 「平时 / 工作日」= 周一到周五哪天都行 → 不锁死单日，跨工作日摊开列。
    # 实测「我要平时去吃火锅」若照常 _pick_date，抠不出日期就按"从今天起逐天"，
    # 学生观感是"怎么给我排周六"；他说的是"平时"，周末就不该出现在候选里。
    workday_mode = any(w in t for w in _WORKDAY_WORDS)
    # ★ #75：与上面**对称**的那一半——以前只有"不排周末"，没有"只排周末"。
    #   学生说「周末打篮球」，「周末」在 `_TIME_WORDS` 里是时间词、擦干净就没了事，
    #   `_pick_date` 也不认它（那张正则要「周 + 一二三四五六日天」，「末」不在字符集里），
    #   于是候选从今天往后铺 → 周三/周四/周五。他把哪天方便说得最死的一次，
    #   系统当成他没说（实测 `todo_slots_proposal('周末打篮球')` 就是这个结果）。
    weekend_mode = any(w in t for w in _WEEKEND_WORDS)
    if workday_mode and weekend_mode:
        # 一句里两样都报了（「平时和周末都行」）= 他压根没限制。这两条跳过必须**一起关掉**：
        # 留着任何一个，工作日被 `workday_mode` 跳过、周末被 `weekend_mode` 跳过，
        # 整段范围被两边轮流吃光 ⇒ **一张卡都出不来**（实测「平时周末都可以打篮球」= None）。
        workday_mode = weekend_mode = False
    if weekend_mode:
        # 放宽 `days_ahead` 有两笔账，缺一笔都会出"某天才坏"那种最难解释的 bug：
        #   ① 今天要是周日，下一个周末在 6 天后：默认 5 天够不着，下面那条跳过会把
        #      整段范围吃光 ⇒ **一张卡都出不来**（周日问"周末打球"正好撞上）；
        #   ② 一个周末只有周六周日**两天**，而跨天那一档每天只给一段（`limit=1`）——
        #      只看这周末最多两段，学生说了"周末"却只挑两个，这周末课排满就直接归零。
        #      放到 14 天 = 连下个字周末一起摊开（段数仍由 `max_slots` 封顶）。
        days_ahead = max(days_ahead, 14)
    picked_day = None if workday_mode else _pick_date(t)
    # 学生说了时长（"大概三小时"）→ 候选段就按 3 小时找；没说才回落 90 分钟。
    # 这里必须认，不然候选卡上全是 90 分钟，学生会拿到「不是我想要的 3 小时」。
    # 2026-10-10：钟点/时长/档统一从 `parse_time_spec` 一次读出来（日期仍按老样子
    # 由 _pick_date 判 —— 那一条链路上有 test_28/test_35 的既有行为要保，不在这个
    # 补丁的范围里，混在一起改出事很难归因）。
    spec = parse_time_spec(t)
    want = spec.get("minutes") or wanted_minutes(t) or AUTO_SLOT_LENGTH
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
        for s in _day_slots(picked_day, prefer, limit=max_slots, length=want, spec=spec):
            _push(s)
        return out[:max_slots]

    for off in range(0, days_ahead + 1):
        if len(out) >= max_slots:
            break
        day = (today + datetime.timedelta(days=off)).isoformat()
        if workday_mode and _iso_weekday(day) >= 6:
            continue          # 「平时 / 工作日」不排周末，周六周日直接跳过
        if weekend_mode and _iso_weekday(day) < 6:
            continue          # #75「周末 / 双休」只排周六周日（与上面那条成对，方向相反）
        for s in _day_slots(day, prefer, limit=1, rotate=off, length=want, spec=spec):
            _push(s)
    return out[:max_slots]


def parse_slot_text(text: str, title: str = "", fallback_date: str = "",
                    minutes: int = 0) -> dict | None:
    """把学生在「其他」里自己填的一行字，解析成一张待办卡（人话：自填也要认）。

    三种都认：
      · 说全了（"周六 19:00-20:00"）→ 直接用他给的时间；
      · 只说了哪天（"周六"）→ 那天替他挑一段空档；
      · **只报了钟点、没报哪天**（"晚上七点到八点"）→ 拿 `fallback_date` 补上那天
        （调用方传候选卡里最靠前的那天；那天这一节已经过了就顺延到明天）。
        不补这一档，「其他」里最自然的写法会直接掉进"没认出来"，学生白填一行。
    认不出来返回 None —— 由调用方如实告诉他"这行没认出来"，**不瞎猜一个时间**。
    标题缺省用外面传进来的那件事（他说的是"其他时间"，不是"其他事情"）。
    ⚠️ 但**这句自己报了事名时必须只用这句的**：以前无条件拼上 `title` 一起解析，
    实测候选卡挂着「写作业」时学生说「周五晚上七点到九点值日」，
    拼成「…值日 写作业」→ `_pick_title` 抠出 **「值日写作业」** 挂上确认条。
    他换了事情，系统却把上一轮的事情缝进名字里（2026-10-10 真机复现）。

    `minutes`（#73）= 调用方从卡上读到的"这件事单次多久"。没这一参，凡是只报哪天
    或只报时长的那句都会掉到 `AUTO_SLOT_LENGTH`=90（实测：卡上 60 分钟、学生说
    「改成周六」，出来的还是 08:00-09:30/90）。谁优先见下面两处的写法。
    """
    t = (text or "").strip()
    if not t:
        return None
    mine = _pick_title(t)
    keep = mine if (mine and mine != "待办" and _title_ok(mine)) else ""
    # 拼过去的那半句只在"这句没报名"时才用；报了名就不许再借上一轮的名字。
    # ⚠️ 拼接符用顿号而不是空格（2026-10-11 真机）：空格粘出来是一整串，
    # 「那就周六吧」+「健身」→ 擦掉"那就/周六"之后剩「吧健身」当名字；
    # 用标点切开，`_pick_title` 的"逐句重试"那一档才会分别判：前半句不算名字、后半句是「健身」。
    blob = t if keep else f"{t}，{title}".strip("， ")
    prop = parse_add_todo(blob)
    if prop is None:
        prop = auto_todo_proposal(blob, minutes=minutes)
    if prop is None:
        prop = span_only_proposal(t, title, fallback_date, minutes=minutes)
    if prop is None:
        return None
    # 名字**只认这两个来源**：这句自己报的（`keep`），或者外面传进来的那件事（`title`）。
    # 不许用 `parse_*` 从拼出来的整串里抠的名字 —— 2026-10-11 真机：学生回「那就周六吧」，
    # 拼上「健身」擦干净之后剩 **「吧健身」** 当卡片标题，语气词的尾巴粘在事名上。
    want_name = keep or (title or "").strip()
    if want_name and prop.get("title") != want_name:
        prop["title"] = want_name
        prop["summary"] = (f"{want_name}｜{prop['date']}（{prop.get('weekday', '')}）"
                           f"{prop['start']}-{prop['end']}")
    return prop


def span_only_proposal(text: str, title: str = "", fallback_date: str = "",
                       minutes: int = 0) -> dict | None:
    """只有起止钟点、没有日期的那一行字 → 用兜底那天补成一张卡。

    顺延规则：兜底那天就是今天、而这一段已经过去了 → 挪到明天。
    宁可挪一天，也不给学生排一段"已经过去的晚上七点"。

    ★ 2026-09-30 起**对外**（原来叫 _span_only_proposal，只给 parse_slot_text 用）。
      两处要用它，口径只留一份：
      · `parse_slot_text`：学生在「其他」里自己填的那一行；
      · `app/main.py` 的**语义层加待办分支**：模型/样本库说了"这是件要办的事"、
        句子里也报了钟点，**可就是没说哪天**（「中文12点的车」）。
        只报钟点不等于"没说清时间" —— 再摊一堆候选时段，等于让他把自己刚说过的
        那个点重挑一遍，而且挑出来的候选里根本没有他说的那个点。

    ⚠️ 规则层那一路**故意不用它**：「三点下课，帮我加个买水果」里的"三点"
       未必是这件事的时间（`_pick_span` 还会把它读成 03:00）。
       规则层交不出日期时，摊候选时段更安全 —— 宁可漏，不可替学生办错事。
    """
    begin, finish = _pick_span(text or "")
    if not begin:
        return None
    # ⚠️ 裸钟点（1..6 点、没上下午）不出这张卡：这里给的是**单条确认条**，
    # 一旦出条就是"照你说的点排"，而 _pick_span('三点开始') 给的是 03:00 ——
    # 学生要的多半是下午三点，凌晨三点这条他绝不会想到我读成了什么。
    # 判据与 `_day_slots` 的 pinned 同一条（`clock_ok`），两条路不会一处认一处不认。
    sp = parse_time_spec(text or "")
    if sp.get("clock") and not sp.get("clock_ok"):
        return None
    day = (fallback_date or clock.today().isoformat()).strip()
    if _iso_weekday(day) == 0:
        return None
    if not finish:
        # 时长：这一句说的 > 卡上记着的（`minutes`）> 60。以前是写死的 60 ——
        # 实测学生说「下周三，一小时」排出来 90 分钟那一档，他刚报的数当没听见。
        finish = to_hhmm(to_minutes(begin) + (wanted_minutes(text) or minutes or 60))
    if to_minutes(finish) <= to_minutes(begin):
        return None
    if day == clock.today().isoformat() and to_minutes(begin) <= _now_minutes():
        day = (clock.today() + datetime.timedelta(days=1)).isoformat()
    # 说了「平时/工作日」却没报哪天：兜底那天落在周末就得往后挪到工作日。
    # （`candidate_slots` 的 workday_mode 早就这么做，但**单条卡这一路以前不看星期几** ——
    #  真机「工作日晚上七点背单词」就排到了周日 19:00。）
    if any(w in (text or "") for w in ("平时", "工作日")):
        while _iso_weekday(day) >= 6:
            day = (datetime.date.fromisoformat(day)
                   + datetime.timedelta(days=1)).isoformat()
    name = _pick_title(text or "")
    if not name or name == "待办":
        name = (title or "").strip() or "待办"
    # 语义层偶尔吐出一个**陈年日子**（真机实测 `2023-04-18`），照收就是一张
    # "刷新永远看不见"的卡 —— 过去的日子一律不兜底（用户 2026-10-11 明令）。
    if is_past_date(day):
        return None
    # 语义层可能把"12"这种时间碎片当 title 传下来，必须再过判真关
    if not _title_ok(name):
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
        # 同 `parse_add_todo`：说了"每周几"就要带上 repeat，不然这条只出现一次
        "repeat": repeat_of(text),
        "summary": f"{name}｜{day}（{wd}）{begin}-{finish}",
    }


def build_todo_slots(title: str, slots: list, hint: str = "", preselect: int = 0,
                     repeat: str = "", times: int = 0, duration: int = 0) -> dict:
    """把几个候选时段包成一张「挑时间」的提案（人话：弹框里那串能打勾的时间）。

    它的形状跟别的提案不一样：**一个提案里装着好几个时段**，学生勾哪个算哪个
    （还能在「其他」里自己补一个）。所以它不是"已经定好的安排"，
    而是"摊开来的备选"——真正写库要等学生勾完点确认，走 `/api/todos/batch`。

    `preselect` = 前几段**默认替他勾上**（人话：他说"一周三次"，就别让他再点三下）。
    0 = 一段都不预选，跟以前一样由他自己勾。

    `repeat="weekly"` = 这张卡勾出来的每一段都要写成**一条每周重复**的待办
    （2026-10-10 加，服务「每个周天都写作业」这类说法）。它必须一路传到
    `/api/todos/batch`，中途任何一处把它丢了，学生看到的就还是"只排这一周"。
    `repeat="daily"` 同理，只是那条以后**每天**出现、且只进代办清单不进日历
    （2026-10-13 加，服务「我每天都要背单词」）。

    `times` / `duration`（#73 加）= 学生**明说了**的几个、每次多少分钟，0 = 他没说。
    这两个数以前只用来算这一轮的段数，算完就丢了：下一轮他补一句「改成三次」，
    系统手上没有任何地方能读到"上一轮要几次"，只能拿整句话重新猜一遍。
    卡上存下来之后，补充句只补他明说的那几项，其余照旧不动。
    `origin` = 这件事最早那个名字（不是拼过之后的整串），给"不许从整句重抠事名"当底。
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
    pre = max(0, min(int(preselect or 0), len(items)))
    return {
        "kind": "todo_slots",
        "title": title or "待办",
        "slots": items,
        "hint": hint,
        "preselect": pre,
        "repeat": repeat or "",
        "times": int(times or 0),
        "duration": int(duration or 0),
        "origin": title or "",
        "summary": (f"{'每周重复 · ' if repeat == REPEAT_WEEKLY else ''}"
                    f"{'每天 · ' if repeat == REPEAT_DAILY else ''}"
                    f"{title or '待办'}｜{len(items)} 个候选时段"
                    + (f"（已勾 {pre} 段）" if pre else "")),
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


def what_to_do_suggestion(text: str = "", days_ahead: int = 3,
                          minutes: int = 90) -> tuple[str, list]:
    """他在问「该做什么」：读周表空档回一句**实在的**建议，不建卡、不替他决定。

    为什么不摊候选卡（2026-10-14 用户定的做法，他原话「这种是问事情闲聊的，不入库」）：
    摊卡等于把"建议"变成"替他排了一件事"，而那件事的名字只能是他那句问话本身——
    实测卡片标题就印着「点啥」「干点啥好呢」。卡越像方案，越是在编造他没说过的事。
    所以只报事实（哪几段空着）+ 请他报个名字。

    说了日子（「今晚安排点啥」）就只看那一天；没日子就从今天起往后挑，一天只取第一段，
    最多两段——够他判断"有没有空"就行，别把整周摊给他。
    """
    day = _pick_date(text or "")
    if day:
        try:
            base = datetime.date.fromisoformat(day)
        except ValueError:
            base = clock.today()
        offsets = (0,)
    else:
        base = clock.today()
        offsets = range(days_ahead)

    picks = []
    for off in offsets:
        d = (base + datetime.timedelta(days=off)).isoformat()
        for s in find_free_slots(d, minutes):
            if "error" in s or "info" in s:
                continue
            picks.append(s)
            break
        if len(picks) >= 2:
            break

    if not picks:
        return ("🤔 这句是在问我『该做什么』——**我不替你决定，也没往日程里写一条**。\n"
                "　　　这几天课和已经排好的事把空档占得差不多了，"
                "你直接说个名字＋哪天几点（比如「周六下午三点爬山」），我看看能不能挤一挤。",
                [])
    when = "、".join(f"{s['weekday']}（{s['date'][5:7]}/{s['date'][8:10]}）"
                     f"{s['start']}-{s['end']}" for s in picks)
    return ("🤔 这句是在问我『该做什么』——**我不替你决定，也没往日程里写一条**。\n"
            f"　　　这几段是空着的：{when}。\n"
            "说个名字（比如「跑步」「写作业」），我就照上面的空档给你排一条；"
            "想自己定就连日子带钟点一起说，比如「周六下午三点爬山」。"), picks


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


def carry_freq(blob: str, message: str) -> str:
    """语义层重拼那句话时，把「一周三次」这类频次字样**带过去**，别在半路丢掉。

    `to_canonical` 只按语义层给的槽位（日期/事名/钟点）重拼一句规范话术，
    而语义层**没有"频次"这个槽位** —— 于是同一句话走规则层能排 3 段、
    走语义层只排 1 段（2026-10-08 实测：「我一周要去三次健身房」被拼成「健身房锻炼」，
    次数直接蒸发）。频次字样该不该留在标题里是 `_pick_title` 的事（不该留），
    但**次数本身不能丢**，所以这里把原话里匹配到的那段补回句尾，
    下游 `todo_slots_proposal` 才仍然看得见它。
    """
    blob = blob or ""
    if weekly_times(blob) is not None:
        return blob
    m = _FREQ_RE.search(message or "")
    if not m:
        return blob
    # 语义层会把「一周」猜成一个具体日子（实测拼出「加个健身房锻炼，2026-10-11」），
    # 那个日子一旦进 blob，下游"他点名了哪天就别预选多段"那道闸就会误关 —— 次数又丢了。
    # 判据：**原话去掉频次短语之后有没有点名哪天**。没有，就把拼出来的 ISO 日期摘掉，
    # 因为那是语义层替学生编的，不是学生说的。
    if not _pick_date(_FREQ_RE.sub(" ", message or "")):
        blob = _DATE_ONLY_RE.sub(" ", blob)
        blob = _DAY_WORD_RE.sub(" ", blob)
    # 补的是**规范写法**「一周三次」，不是原话里那一段（原话可能是「一周要去三次」）。
    # 差别的代价实测过：把原话整段拼上去，擦频次时「一周」「三次」各自被擦掉，
    # 中间的"要去"没人擦，标题就成了「健身房锻炼要去」——卡片名字带尾巴。
    return f"{blob} 一周{m.group(1)}次"


def carry_period(blob: str, message: str) -> str:
    """语义层重拼那句话时，把**周期字样**（每周几 / 平时·工作日）也带过去。

    跟 `carry_freq` 是同一个病：`to_canonical` 只按语义层给的槽位重拼，
    槽位里没有的字样就整段蒸发。2026-10-11 一批真机句子实测到三种后果：
      · 「每个周六上午九点爬山」→ 拼出来没有"每周"，`weekly_repeat_day` 判不出，
        学生要的每周重复落成了一条**一次性**待办；
      · 「我平时想去跑步」→ 拼出来没有"平时"，`candidate_slots` 的 workday_mode 关掉，
        候选里赫然排着**周日**；
      · 「工作日晚上七点背单词」→ 同上，只能反问一句"每周几"。
    补的是**规范写法**（「每周六」「工作日」），不是原话那一段 —— 原话可能是「每个礼拜六」，
    下游 `_REPEAT_PHRASE_RE` / workday_mode 认的就是这两串，标题也照样擦得干净
    （实测「加个跑步 工作日」擦完剩「跑步」，不会把"工作日"黏进事名）。
    """
    blob, msg = blob or "", message or ""
    add = []
    wd = weekly_repeat_day(msg)
    if wd and not weekly_repeat_day(blob):
        add.append("每周" + "一二三四五六日天"[wd - 1])
    if any(w in msg for w in ("平时", "工作日")) and not any(w in blob for w in ("平时", "工作日")):
        add.append("工作日")
    # 「每天」也要一起带过去（判据问 `daily_repeat`，不在这里再抄一份词表）：
    # 学生先说「我每天都要背单词」被追问，再补一句「晚上九点」——
    # 不带的话第二轮落库成的是一条**只有一次**的待办，"每天"当场丢了。
    if daily_repeat(msg) and not daily_repeat(blob):
        add.append("每天")
    return (blob + " " + " ".join(add)).strip() if add else blob


def _clock_note(text: str, slots: list) -> str:
    """学生点了钟点、但那一段排不进去时，**必须说一句为什么**。

    为什么不许默默换：学生说「周五晚上七点到九点值日」，卡片给 15:40-17:40，
    他只会认为"你没听懂我的话"（实测就是这么被投诉的）。而真实原因是
    那晚 19:00-20:40 有《班会》—— 这是**该说的事实**，不是错误。
    """
    sp = parse_time_spec(text)
    day = sp.get("date") or (slots[0]["date"] if slots else "")
    # ⚠️ `clock_ok` 为假时**不许走这里**：那句"那个时间有课或已有安排"是假话 ——
    # 凌晨三点通常空得很，我们没排它是因为它没说是凌晨还是下午（判据见 `_bare_clock_note`）。
    # 两条注记各管一种"没照你点的排"，叠在一起就成了编造理由。
    if not sp.get("clock") or not day or not sp.get("clock_ok", True):
        return ""
    if any(s.get("start") == sp["start"] for s in slots or []):
        return ""
    want = f"{sp['start']}-{sp['end']}" if sp.get("end") else f"{sp['start']} 开始"
    # 「没排上」有两种，话必须分开说（合成一句就变成编理由）：
    #   ① 那个点确实被课或已有安排占了 —— 说"有课或已有安排"是对的；
    #   ② 那个点根本**不在找空档的时间窗里**（`find_free_slots` 只在
    #      `DAY_START`-`DAY_END` 这段里找，07:00-22:00）。
    #      「早上6点半跑步」就是这一档 —— 那会儿操场空得很，
    #      回一句"那个时间有课"学生一听就知道在糊弄（2026-10-11 一批真机句子打出来的）。
    lo, hi = to_minutes(DAY_START), to_minutes(DAY_END)
    s0 = to_minutes(sp["start"])
    if s0 < lo or s0 >= hi:
        return (f"⚠️ 你说的 {want}（{day}）我这边**排不了**——"
                f"找空档只在 {DAY_START}-{DAY_END} 这一段里找（上课和睡觉的时间不动），"
                f"你说的那个点在外头。换个里面的钟点，或者到「我的日程」里自己加一条。")
    return (f"⚠️ 你说的 {want}（{day}）排不进去——那个时间有课或已有安排，"
            f"下面给的是别的时间段，不是你点的那个。")


def todo_slots_proposal(text: str, max_slots: int = 3,
                        times: int | None = None) -> dict | None:
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
    # 裸钟点（只说"三点"）先记一句"没听清"：下面的候选段一律按常规档排，
    # 但**不能什么都不说** —— 学生会以为自己报的钟点被听见了。
    bare_clock = _bare_clock_note(t)
    day = _pick_date(_FREQ_RE.sub(" ", t))
    # `times` 允许调用方从上一轮那张卡上递进来（#73）：补充句常常只剩「三次」这种
    # 没有"周"字的省略写法，`weekly_times` 结构上看不到上一轮，读出来是 None，
    # 于是这一句从频次分支掉回普通分支 —— 段数和预勾两处一起变，学生看到的卡就"没循环确认"。
    times = (weekly_times(t) or 0) if times is None else max(0, int(times))
    # 「一周三次」里的"一周"会被 `_pick_date` 当成相对日期（一周后 = 2026-10-14），
    # 于是频次模式被"他指定了哪天"这道闸误拦，候选还全挤到那一天去。
    # 所以判"有没有点名哪天"、以及去找空档，都用**去掉频次短语之后**的那句话。
    plain = _FREQ_RE.sub(" ", t)
    # ==== 「每个周天都写作业」：固定星期几 + 无限重复（2026-10-10 加）====
    # 与「一周三次」是两回事：那种没有固定日子，只能在这一周里铺 N 条（下一条分支）；
    # 这种点名的就是"每周的某一天"，所以勾中一段只写**一条** repeat=weekly 的记录，
    # 以后每个那天自动出现（读侧展开在 store.todo_occurs_on）。
    # 候选只落在被点名的那个星期几 —— 给学生一堆别的日子的空档，等于没听懂他说的话。
    wd = weekly_repeat_day(t)
    if wd:
        slots = [s for s in candidate_slots(_REPEAT_PHRASE_RE.sub(" ", plain),
                                            max_slots=max_slots + 6, days_ahead=28)
                 if _iso_weekday(s.get("date") or "") == wd]
        if not slots:
            return None      # 往后四周里那个星期几真排不下 → 如实说排不出，不硬凑别的日子
        wd_name = "周" + "一二三四五六日"[wd - 1]
        hint = (f"这是**每周{wd_name}**都要做的事，不是只排这一周："
                f"勾一个固定的时间段，以后每个{wd_name}都会出现在日程里"
                f"（从 {slots[0]['date']} 起，勾完点【加入日程】我只建一条）。")
        _note = _clock_note(_REPEAT_PHRASE_RE.sub(" ", t), slots)
        if _note:
            hint = _note + "\n" + hint
        if bare_clock:
            hint = bare_clock + "\n" + hint
        return build_todo_slots(title, slots, hint=hint, preselect=0, repeat="weekly",
                                times=0, duration=wanted_minutes(t) or 0)
    # ==== 「我每天都要背单词」：固定每天 + 无限重复 → 一条 repeat=daily 的**习惯** ====
    # 与 weekly 的分工不在"铺几天"，在**出没出现在日历上**（用户 2026-10-13 拍板）：
    # weekly 有固定星期几和钟点，是时间承诺，照旧画进日程；daily 没说哪天，
    # 画进日历就是"以后每一天都占一格"，他要的是打卡不是格子 → 只进代办清单。
    # 所以候选**随便哪天挑都行**（每天都重复，落在哪天只是"从哪天开始"这个锚点），
    # 话术必须把"不进日历"说在明处 —— 不然他加完会在日程里找，找不到就说系统没记上。
    if daily_repeat(t):
        # 锚点：说了「从今天/明天/下周一开始」就从那天起，没说就从今天起。
        # ⚠️ 这句话既带着"每天"又带着"哪天开始"，直接丢给 candidate_slots 会被当成
        #   "他点名了某一天"→ 只列那一天的空档（实测：候选只剩 1 段，还全在今天下午）。
        #   习惯天天都做，所以这里把起手式连同那个日子一起摘掉去摊候选，
        #   再用 anchor 把"比锚点早的那几天"滤掉 —— 摘和滤缺一半都不对。
        anchor = start_anchor(t) or clock.today().isoformat()
        lead = max(0, (datetime.date.fromisoformat(anchor)
                       - clock.today()).days)
        slots = [s for s in candidate_slots(_START_FORM_RE.sub(" ",
                                            _REPEAT_PHRASE_RE.sub(" ", plain)),
                                            max_slots=max_slots + 6,
                                            days_ahead=7 + lead)
                 if (s.get("date") or "") >= anchor]
        if not slots:
            return None
        hint = (f"这是**每天**都要做的事：勾一个固定的时间段，以后每天出现在你的"
                f"**代办清单**里（从 {slots[0]['date']} 起，勾完点【加入日程】我只建一条；"
                f"习惯不画进日程格子，不然每一天都占一格，越看越乱）。")
        _note = _clock_note(_REPEAT_PHRASE_RE.sub(" ", t), slots)
        if _note:
            hint = _note + "\n" + hint
        if bare_clock:
            hint = bare_clock + "\n" + hint
        return build_todo_slots(title, slots, hint=hint, preselect=0, repeat=REPEAT_DAILY,
                                times=0, duration=wanted_minutes(t) or 0)
    # 「一周三次」且**没指定哪天** → 按一周 N 段排，前 N 段替他勾上。
    # 为什么要求"没指定哪天"：他要是说了「周四一周三次」，那两句本身就打架
    # （候选全挤在周四 = 一天三练），这种矛盾句宁可退回老路子只排那天，
    # 也别替他造出"周四连跑三趟健身房"这种谁也没说要的东西。
    if times >= 2 and not day:
        slots = candidate_slots(plain, max_slots=times + 2, days_ahead=7)
        if not slots:
            return None
        pre = min(times, len(slots))
        if pre >= times:
            hint = (f"你说一周 {times} 次，先按这一周给你挑了 {pre} 段"
                    f"（一天一段、避开课和已有的安排），已经替你勾上了——"
                    f"不合适的取消，或改勾后面 {len(slots) - pre} 段备选。")
        else:
            # 排不满 N 段（那几天真没空）→ 如实说排到几段，不假装凑齐了
            hint = (f"你说一周 {times} 次，但这几天只排得出 {pre} 段空档"
                    f"（避开课和已有的安排），先勾上这 {pre} 段；"
                    f"剩下的过几天再说，或者你在「其他时间」里自己补。")
    else:
        slots = candidate_slots(t, max_slots=max_slots)
        if not slots:
            return None      # 那天的空档真排不下（或全是过去时段）
        pre = 0
        if day:
            hint = (f"{day}（{_weekday_name(day)}）的空档都在这儿了——"
                    f"避开课和已有的安排，勾一个方便的。")
        else:
            hint = "避开课和已有的安排，挑一个方便的勾上（可以勾好几个）。"
        # 点了钟点却没排进去 → 说一句为什么（默默换成别的时间，学生只会认为没听懂）
        _note = _clock_note(t, slots)
        if _note:
            hint = _note + "\n" + hint
    if bare_clock:
        hint = bare_clock + "\n" + hint
    # ⚠️ `times` 在这一支**以前被整个丢掉**：只有频次分支（`times >= 2` 且没点名哪天）
    #    替学生勾了段数，走这一支的（说了具体哪天、或只说一次）下一轮补「改成三次」时，
    #    卡上读不到次数，只能重猜。#73 T3 那种"次数不变"就挂在这一句上。
    return build_todo_slots(title, slots, hint=hint, preselect=pre,
                            times=times, duration=wanted_minutes(t) or 0)


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
    today = clock.today()
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
    if looks_like_question(t):
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
        # 报了频次（"一周三次"）又说"你帮我安排" → 替他挑的就不该只有一段。
        # 走同一张候选卡（前 N 段已勾上），比"替他敲定一个点"更贴合他这句话。
        if (weekly_times(text) or 0) >= 2 and not _pick_date(_FREQ_RE.sub(" ", text)):
            multi = todo_slots_proposal(text)
            if multi is not None:
                return multi
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
    if looks_like_question(t):
        return False
    if any(w in t for w in _ADD_INTENT) or _ADD_TALK_RE.search(t):
        return False
    # ★ 「纯时间/纯日期」才算挑时间：把时间词、日期词、语气字都擦干净之后**只剩两三个字以内**
    #   才算数。老写法只要求"句子里有钟点或有日子"，真机那句
    #   「我现在没有看见日程显示周二游泳代办项目啊」（里面有"周二"）就被当成挑时间，
    #   系统回了一句「好，那 游泳 就定在 11:40-13:10」—— 学生的报障被听成了下单，就此蒸发。
    #   用"擦完剩多少字"而不是"再列一张报障词表"：词表抄不完，这一条是这句话的**形状**。
    residue = _strip_title_noise(t) or ""
    residue = re.sub(r"[\s，,。.、！!？?；;：:_\-~～]|就|那|吧|啦|呀|啊|呢|嘛|了|的|好|嗯|改|换|到|个|来|再", "", residue)
    if len(residue) > 2:
        return False
    return _pick_span(t)[0] is not None or _pick_date(t) is not None


def _todo_has_id(t: dict) -> bool:
    """这条待办有没有可用的 `id`。缺了就**不往外交**（#66，这是兜底的唯一例外）。

    为什么不兜成空串：`id` 在界面上是**操作句柄**（勾完成 / 删除 / 改期都按它回传），
    兜成 `""` 之后所有缺 id 的记录会挤到同一个 key 上 —— `list_todos_range` 里
    `by_id[""]` 那一行会把好几件不同的事**并成一条**（`row["dates"].append` 一路加下去），
    学生勾一条会连带勾掉别的。**静默错乱比崩溃贵得多**，所以宁可这一条不出。
    """
    return bool(str(t.get("id") or "").strip())


def _todo_public_row(t: dict, date: str) -> dict:
    """待办记录 → 给模型/前端的对外形状（**这一族只此一处翻译**，#66）。

    为什么兜底、不许硬取（口径与 `store.owner_of_row` / `trail` / `ref` / `purpose` 那几处
    成文的规矩一致：**缺键按默认值兜，不批量迁移文件** —— 每迁移一次就多一次改坏真实数据的机会）：
      · 两个写入口确实把六个键写全（实测：`store.add_todo` 从存储层第一笔 `0a31534` 起就写
        `status`，`add_todos_batch` 同样）⇒ **"老记录天然缺 status"这句不成立**，会缺键的
        只有被手改过的 `todos.json` 或外部导入的那一份。
      · 而这两把工具是挂在 `build_tools()` 里给模型用的（`readonly.py` 也把它们放进跨栏只读池）：
        硬取一个缺掉的键得到的不是"报警"，是**这一轮工具调用失败** —— 模型会拿着那条报错
        继续编一句答案，比少列一条更糟。校验的位置在写侧（那边已经在做），不该由读展示层当报警器。
      · 代价要说明白：兜底之后"数据被手改过"在界面上只剩一个空值。要当场暴露得靠**启动自检**
        （另立一单），不是靠这里崩一下。
    ⚠️ 三个键兜成 `""` 是**可见的降级**，不是掩盖：`status` 缺 ⇒ 与 `store.todo_done_on`
       （`str(t.get("status") or "") == "done"`）天然一致 = 没完成，**宁可不勾**；
       `title` 缺 ⇒ 一行只有时间的空标题；`date` 缺 ⇒ 排序沉到最前。
    ⚠️ `id` 这里虽然也写成 `.get`，但缺 id 的记录**进不到这一行** —— 调用方先过 `_todo_has_id`。
    `date` 由调用方传，不从记录里取：`list_day_todos` 传记录自己的锚点日期（既有行为，不许顺手改），
    `list_todos_range` 传**落到的那天**（`test_84` B3 钉着这一头）。两种语义都留着，本单只收进同一处翻译。
    """
    return {
        "id": str(t.get("id") or ""),
        "title": str(t.get("title") or ""),
        "date": str(date or ""),
        "start": str(t.get("start") or ""),
        "end": str(t.get("end") or ""),
        "status": str(t.get("status") or ""),
    }


def list_day_todos(date: str) -> list:
    """列某天已安排的待办（人话：看看这天已经塞了些什么）。"""
    todos = list_todos(date)
    if not todos:
        return [{"info": f"{date}（{_weekday_name(date)}）目前没有安排待办"}]
    rows = [_todo_public_row(t, str(t.get("date") or "")) for t in todos if _todo_has_id(t)]
    dropped = len(todos) - len(rows)
    if dropped:
        # 说出去，不静默：模型看得见才会跟学生解释，而不是回一句「这天没有安排」
        rows.append({"info": f"另有 {dropped} 条待办没有 id，列不出来（数据可能被手改过）"})
    return rows


def list_todos_range(start: str, end: str) -> list:
    """列 start~end（含两端，YYYY-MM-DD）这段时间里已安排的待办：**一条一行**，行里带上它落在哪几天。

    为什么要有它（#63）：`list_day_todos` 只能查一天，学生问「这周有什么待办」时模型手里
    没有"查一段"的那只手，只能把七天挨个点一遍 —— 实测 7 次调用、轨迹 22 行、七次返回长得一模一样。
    那是**工具粒度与提问粒度没对齐**，不是模型笨。底层能力早就齐（`store.list_todos()` 一次取全量），
    这一支只是把"逐天筛"从模型那一侧搬进系统这一侧。

    ⚠️ 筛法走 `store.todo_occurs_on`，**不是** `start <= t["date"] <= end`。
       后者实测会**吞掉重复待办**：一条「每周三游泳」的记录只有一个锚点（上周三），
       按字面 date 筛，本周三就看不见它 ⇒ 回一句「之间没有安排任何待办」——
       那不是省一次调用，那是**答错**（探针 `probe63.py`：三条待办只筛出 1 条）。
       `list_todos_in_month` 2026-10-10 就是为这件事改成逐天问的 ⇒ 判据仍只有 `todo_occurs_on` 一处。
    ⚠️ 一条一行（不是一天一行）：习惯（daily）在一周里天天出现，按天摊就是 7 行同样的事，
       比模型逐日调还啰嗦；所以把"这周哪几天"放进 `dates` 里给模型，行数只跟**事情数**挂钩。
       习惯**照实列出**：问的是"这周有什么事要做"，天天要做的晨跑就是这周的事
       （月视图滤习惯是因为格子装不下习惯，用户 2026-10-13 拍板，不是"习惯不算待办"）。
    ⚠️ 跨度夹住：尺子借 `WEEKLY_HORIZON_MONTHS`（每周重复最多铺 3 个月那把），
       别让模型一句「查 2020 到 2030」把整库摊进一次工具结果里。
    """
    from datetime import date as _date, timedelta

    s, e = str(start or "").strip(), str(end or "").strip()
    try:
        d0, d1 = _date.fromisoformat(s), _date.fromisoformat(e)
    except ValueError:
        # 模型常把「这周」直接当参数递过来：别猜哪天，把该给的格式说回去，它下一步就会补对。
        return [{"info": "起止日期要写成 YYYY-MM-DD（例如 2026-10-26 到 2026-11-01）。"
                         "先按学生的说法算出具体日子，再来查这一段。"}]
    if d1 < d0:
        d0, d1, s, e = d1, d0, e, s          # 学生/模型说反了不算错，换过来就是了

    notes: list = []
    max_span = WEEKLY_HORIZON_MONTHS * 31
    if (d1 - d0).days + 1 > max_span:
        d1 = d0 + timedelta(days=max_span - 1)
        e = d1.isoformat()
        notes.append({"info": f"一次最多查 {max_span} 天（与「每周重复最多铺 "
                              f"{WEEKLY_HORIZON_MONTHS} 个月」同一把尺子），下面只算到 {e}"})

    todos = list_todos()
    # 缺 `id` 的记录一次数清（**别在按天的循环里数** —— 一条习惯落到七天会被计成七条）
    no_id = sum(1 for t in todos if not _todo_has_id(t))
    todos = [t for t in todos if _todo_has_id(t)]
    by_id: dict = {}
    order: list = []
    day = d0
    while day <= d1:
        iso = day.isoformat()
        for t in todos:
            if not todo_occurs_on(t, iso):
                continue
            row = _todo_public_row(t, iso)          # 这一族唯一的翻译处（#66）
            seen = by_id.get(row["id"])
            if seen is None:
                row["dates"] = [iso]
                rep = str(t.get("repeat") or "")
                if rep and rep != REPEAT_NONE:
                    row["repeat"] = rep
                by_id[row["id"]] = row
                order.append(row)
            else:
                seen["dates"].append(iso)      # 同一件事在这段里出现几天，就并到一行里
        day += timedelta(days=1)
    if no_id:
        # 与 `list_day_todos` 同一句实话。#66 单据原本决定这里不吭声（怕打断"一条一行"），
        # 但 `notes` 这一栏 #63 就是为"这种该说一声的话"设的、且摆在最前面不混进条目里
        # （`test_84` B6/B7 取条目时是先滤掉带 info 的行再算的 ⇒ 不破坏那条契约）。
        notes.append({"info": f"另有 {no_id} 条待办没有 id，没列进来（数据可能被手改过）"})
    if not order:
        return notes + [{"info": f"{s} ~ {e} 之间没有安排任何待办"}]
    order.sort(key=lambda r: (r["date"], r["start"] or ""))
    return notes + order


def add_todo_tool(title: str, date: str, start: str, end: str, note: str = "") -> str:
    """把待办正式写进日程。

    ⚠️ **这是系统侧函数，不再交给模型**：2026-10-08 起它已从 `build_tools()` 里摘掉，
    模型工具箱里没有它，模型说什么都写不了库。调用方只有两处——
    `planner.py:1037`（学生回确认类文字，系统解析后落库）和测试。
    写入永远发生在"学生已经点头"之后，这一点由调用方保证，不由提示词保证。

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
# 中文月日的写法比想象的杂：「10月3号」「10月三号」「十月三日」都有人写。
# 早先只认阿拉伯数字，"10月三号"这种**日是中文数字**的写法直接被当成"没说日期"——
# 删待办/标记完成走到"没找到"就是这么来的（学生截图实测）。
_CN_MD_RE = re.compile(
    r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*月\s*"
    r"([0-9]{1,2}|[一二三四五六七八九十]{1,3})\s*[号日]")
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
            "answer": "课表导入要我真读到你上传的文件内容才行，这条通道现在还没接通。"
                      "麻烦叫管理员在后台检查一下；"
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
    base = clock.today()
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


# ============ 课表导入的出卡兜底（模型没调 propose_timetable_change 时用） ============
#
# 2026-10-03 学生截图实测：上传课表文件后，模型读了文件、把课程整理成一张**文字表格**，
# 就是不调 propose_timetable_change → 界面上什么卡都没有；
# 更糟的一次还触发了 main.py 的待办补条兜底，把话术里的「确认修改」当成了任务名，
# 挂出「要不要把 确认修改 排进日程」的假卡。
# 所以这里给两样东西：① 认出"这是导入课表的请求"；② 从模型的文字表格里把课程抠回来，
# 让系统自己把「确认修改」卡挂出来（写库仍然只在学生点卡之后）。

# 表格里时间格的写法：08:00-09:40 / 08:00–09:40（en dash）/ 8:00~9:40 / 08:00到09:40
_TT_TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-–—―~～至到]\s*(\d{1,2})[:：](\d{2})")
# 星期格的写法：周一 / 星期一 / 周天（表格里 day=1..7 的纯数字格另算）
_TT_DAY_RE = re.compile(r"(周[一二三四五六日天]|星期[一二三四五六日天])")
# 表头/分隔线格：出现这些词的行不是课程
_TT_HEAD_WORDS = ("星期", "时间", "课程", "地点", "day", "weekday")
_TT_MAX_COURSES = 80  # 一张课表不可能有这么多门课，超了多半是抠错了，宁可放弃
# 课名/地点格的"像名字"闸门：短、没有句读、至少含一个汉字或字母。
# 没有这道闸门，模型话术里的整句（"已经把「确认修改」卡发给你了…"）会被当成课名——
# 抠出假课比抠不出来更糟（2026-10-03 反向测试实测抓到）。
_TT_SENT_PUNCT = "，。！？…《》‘’“”"
# 时段词：它是"什么时候"，不是"在哪儿"。学生文件里常见「…；晚上 19:00-20:40 大学英语」，
# 不滤掉的话前一门课的地栏会被写成"晚上"。
_TT_PERIOD_WORDS = ("上午", "中午", "下午", "晚上", "早上", "傍晚", "夜里", "凌晨", "白天", "晚间")
# 拆词的分隔符：竖线（含全角）、空白、分号逗号顿号——学生文件用「；」串一整天的课
_TT_SPLIT_RE = re.compile(r"[|\s\uFF5C;；,，、]+")
# 「高等数学（教三301）」这种课名带括号地点的写法
_TT_BRACKET_RE = re.compile(r"^(.+?)[（(【\[](.+?)[）)】\]]$")


def _tt_name_ok(t: str) -> bool:
    if not t or len(t) > 12:
        return False
    if any(p in t for p in _TT_SENT_PUNCT):
        return False
    return any("\u4e00" <= ch <= "\u9fff" or (ch.isascii() and ch.isalpha()) for ch in t)


def _tt_tokens(seg: str) -> list:
    """按分隔符切词（不做过滤，过滤交给调用方）。"""
    return [t.strip() for t in _TT_SPLIT_RE.split(seg or "") if t.strip()]


def _tt_pick_names(seg: str, day: str | None) -> list:
    """一段文字里"像课名/地点"的词（滤掉分隔线、表头、时段词、星期）。"""
    out = []
    for t in _tt_tokens(seg):
        if t in ("---", "--", "-", ":", "：") or set(t) <= set("-—―:："):
            continue
        if any(h in t.lower() for h in _TT_HEAD_WORDS):
            continue
        if day and day in t:  # 残留的「周二：」这种下一天的表头不许混进来
            continue
        if t in _TT_PERIOD_WORDS or _TT_DAY_RE.search(t):
            continue
        if not _tt_name_ok(t):
            continue
        out.append(t)
    return out


def _tt_course_loc(name: str, rest: list) -> tuple:
    """从一个词里拆出（课名, 地点）。「高等数学（教三301）」→「高等数学」+「教三301」。"""
    m = _TT_BRACKET_RE.match(name)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        if _tt_name_ok(a) and len(a) <= 12:
            return a, (b if _tt_name_ok(b) else "")
    for t in rest:  # 括号里没有地点 → 后面的那个词多半是教室
        if t != name:
            return name, t
    return name, ""


def _tt_courses_in_line(line: str) -> list:
    """一整行能抠出**好几门课**——学生文件与模型回话都是"一天一行、用；串起来"。

    老写法一行只认第一个钟点（其余全被吞掉），学生那份文件因此只抠出 5 门里的 1 门
    （2026-10-04 实测）。现在按**每个钟点**切段：两个钟点之间那段就是这一门课的
    "课名 + 地点"。
    """
    spans = list(_TT_TIME_RE.finditer(line))
    if not spans:
        return []  # 没有起止钟点的行不是课程行（表头、分隔线、「第1-2节」未换算的行）
    dm = _TT_DAY_RE.search(line)
    day = dm.group(1) if dm else None

    # markdown 表的 day 列可能是纯数字 1-7（前端模板就是这么交代的）
    if day is None:
        pre = _tt_tokens(line[:spans[0].start()])
        day = next((t for t in pre if re.fullmatch(r"[1-7]", t)), None)
    if not day:
        return []

    out = []
    for i, m in enumerate(spans):
        start = f"{int(m.group(1)):02d}:{m.group(2)}"
        end = f"{int(m.group(3)):02d}:{m.group(4)}"
        stop = spans[i + 1].start() if i + 1 < len(spans) else len(line)
        names = _tt_pick_names(line[m.end():stop], day)
        if not names:
            continue  # 有时段没课名 → 这半行不认（比编一个课名强）
        course, location = _tt_course_loc(names[0], names[1:])
        out.append({"day": day, "start": start, "end": end,
                    "course": course, "location": location})
    return out


# 课表导入请求的自然语言探测：允许「上传了课表」「课表读取一下」「解析这份课表」
# 这类中间带别的字的表达。对这些句子挂"待办卡"永远是错的，所以宁可放宽、不可漏判。
_TT_IMPORT_RE = re.compile(
    r"(?:上传|读取|解析|导入).*课表|课表.*(?:上传|读取|解析|导入)", re.I)


def looks_like_timetable_import(message: str) -> bool:
    """这句是不是「让管家读课表文件导入」的请求。

    只用在 main.py 的出卡兜底里挑路（要不要走课表补卡而不是待办补条），
    **不参与路由**——正常请求照旧走各自分支。
    探测刻意放宽：凡是提到课表文件/导入课表/读取课表的都算，
    因为对这些句子挂"待办卡"永远都是错的（宁可漏补卡，不可挂假卡）。
    """
    t = message or ""
    # 前端 timetableImportMsg 模板里点名了工具名（最稳）
    if "课表" in t and "read_uploaded_file" in t:
        return True
    # 自然语言：上传/读取/解析/导入 + 课表（允许中间有别的字）
    return bool(_TT_IMPORT_RE.search(t))


# ============ 快递/外卖丢失破损 → 上报工单的出卡兜底（2026-10-07 学生报障） ============
# 现象：学生说"快递丢了 / 上报管理员"，被语义层判成「问事」，助手只回一长段查件话术、
#       从没出卡，学生要上报却找不到入口（"没有出卡，也没有上传"）。
# 这里把"上报工单"做成确定性硬闸门：命中就直接出卡（可取消的提案），
# 学生点【确认上报】才由系统写工单（走 /api/workorders，不经过模型，守住写库红线）。
#
# ⚠️ 探测刻意放宽：只要是"快递/包裹/外卖 丢失破损异常"或明确"上报/工单"都算——
#   对这些句子挂"待办卡"或静默当问事永远都是错的（宁可出卡让学生取消，不可不出卡）。
#   普通查件（"到哪了""取件码多少"）不含这些词，照旧走 express 模块，不受此分支影响。

# 「丢件那族」的四个词（品类判断里"这句的正题是丢件，不是设施"用）。
# ⚠️ 它与下面 `_WORK_ORDER_RE` 第一组是同一批词，但**不拿它去拼那条**：
#    那条第二组故意只列三个词（「丢了取件码」不该进上报闸门），一改就换行为。
#    两份清单必须同步这件事由 `test_75` 静态钉住（#54）。
_PARCEL_WORDS = ("快递", "包裹", "外卖", "取件码")
_PARCEL_RE = re.compile("(" + "|".join(_PARCEL_WORDS) + ")")

_WORK_ORDER_RE = re.compile(
    r"(快递|包裹|外卖|取件码).{0,6}(丢|丢失|遗失|不见|破损|异常|没收到|收不到|找不到)"
    r"|丢(了|失|踪).{0,5}(快递|包裹|外卖)"
    r"|(上报(管理端|管理员|给管理)?|报给?(管理端|管理员)|提交工单|开个?工单|走工单|报修)"
)

# 设施报修那一族（#54：真机原话「帮我报修电梯」被兜底成「疑似丢件」，处理的人收到一条看不懂的单子）。
# 只列**设施**，不列"报修/维修"这种动词：那两个词已经由 `_WORK_ORDER_RE` 管进门，
# 这里只负责"进来这句到底是哪一族"。
_FACILITY_RE = re.compile(r"(电梯|空调|水龙头|水管|门锁|灯|插座|马桶|下水道|洗衣机"
                          r"|热水器|宿舍.{0,4}(坏|修|报修)"
                          # #71：网络这一族原来一个词都没有，「网络报修」直接掉进兜底「疑似丢件」。
                          # 不写孤零零一个「网络」（那是"网络课""网络中心在哪"也认），要**带故障态或报修动词**。
                          r"|网络.{0,6}(坏|断|不通|连不上|没网|报修|维修)"
                          r"|宽带.{0,6}(坏|断|不通|慢|报修|维修))")


def _work_order_kind(message: str) -> str:
    """从原话里估一个工单问题类型（人话：卡上和工单里显示的「kind」）。"""
    t = message or ""
    # ★ 设施报修要在丢件那三枝**之前**判（2026-10-22 #54）：这一族不是丢件，
    #   落到兜底「疑似丢件」会让处理的人收到一条看不懂的单子 ——
    #   真机原话「帮我报修电梯」当时就写着"上报驿站：疑似丢件"。
    #   但**带着快递/外卖那族词的**照旧走下面三枝：那句的正题是丢件，设施只是顺嘴提的。
    if _FACILITY_RE.search(t) and not _PARCEL_RE.search(t):
        return "设施报修"
    if "破损" in t:
        return "包裹破损"
    if "取件码" in t and any(w in t for w in ("异常", "不对", "错", "失效")):
        return "取件码异常"
    if "外卖" in t:
        return "外卖丢失"
    return "疑似丢件"


def looks_like_work_order_report(message: str) -> str | None:
    """这句是不是「要上报驿站处理快递/外卖问题」。

    命中返回问题类型（疑似丢件 / 包裹破损 / 取件码异常 / 外卖丢失 / **设施报修**），
    没命中返回 None。第五种是 #54 加的（真机原话「帮我报修电梯」当时被兜底成"疑似丢件"），
    这句 docstring 到 #71 才跟 `_work_order_kind` 的实际返回值对齐。
    只用在 main.py 的出卡兜底里挑路，**不参与路由**——正常请求照旧走各自分支。
    """
    t = message or ""
    if not _WORK_ORDER_RE.search(t):
        return None
    return _work_order_kind(t)


# 问的是**进度/时长**（"要多久""几天能好"），不是"替我报这一单"。
# #71：`_WORK_ORDER_RE` 里「报修」二字是硬信号，所以「报修要多久」也进了门，
#      出卡之前没人看过"这句是不是在问"——屏幕上因此先说「这句先按问事答」、下一行就挂了卡。
#      这道闸只管这一件窄事：问进度的句子不许被替它挂上报卡（"我的快递丢了吗"这类
#      是非问句照旧出卡，那条由 `test_54` 钉着）。
_ETA_ASK_RE = re.compile(r"(多久|多长时间|几天|几周|什么时候|啥时候|几号)")


def asks_repair_eta(message: str) -> bool:
    """这句是在问"要等多久 / 什么时候能好"吗（人话：他要的是一个时间，不是一张卡）。"""
    return bool(_ETA_ASK_RE.search(message or ""))


def timetable_courses_from_reply(text: str) -> list | None:
    """模型没调工具、只在文字里念了课表清单 → 系统替它把课程抠回来。

    只认"表格形状"的话：竖线分列的表行（markdown 表）、
    「周一 08:00-09:40 高等数学 教三301」这种纯文本行、
    以及学生文件里最常见的"一天一行、用；串好几门课"。
    抠出来后再过一遍 validate_courses（day/时间不合法的整行丢弃）；
    一条都抠不出 / 全都不合法 → 返回 None，让上层**如实说**没整理出来，
    绝不许上层把它捞成待办卡。
    """
    courses, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for row in _tt_courses_in_line(line):
            key = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if key in seen:  # 模型爱把同一门课念两遍，去重
                continue
            seen.add(key)
            courses.append(row)
        if len(courses) >= _TT_MAX_COURSES:
            break
    if not courses:
        return None
    cleaned, _errors = validate_courses(courses)
    return cleaned or None


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
# 「我要去哪里取快递」「我要去怎么走」——第一人称开头，但这是**问句**（问路/问办法），
# 不是下单。`_GO_INTENT_RE` 只挡了「吗/？/?」，挡不住「哪里/哪儿/怎么」这种**不带问号**
# 的疑问词，于是这句被当成"要加一件事"，系统就跑去给他排时间了
# （实测截屏：校园问答里问「我要去哪里取快递」→ 出成「哪取快递」的候选时段卡）。
# 只列**几乎只出现在问句里**的词；像「什么/几点」这种能出现在
# 「什么时间都行」「几点你定」这类下单句里的词**不列**，免得误伤真下单。
_GO_QUESTION_RE = re.compile(
    r"哪里|哪儿|在哪|哪个|哪种|怎么|怎样|咋|如何|是不是|能不能|可不可以|为什么要|为啥")
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
# ==== 星期几的写法：中文 + 阿拉伯数字，**一份字符类**（2026-10-11 真机两张截图）====
# 病根：以前读"周几"的地方各自手写 `[一二三四五六日天]`（一共八处），
# 学生写「周6加个代办去图书馆」「每周6去图书馆」时八处**全不认**：
# 日子读不出、周期读不出、擦标题只擦掉「每周」把「6」留在事名上（卡片标题成了「6去图书馆」）。
# 一处一处补必然漏 —— 收成一份，读日子的地方都从这里取（同红线 10 的"一次收口"）。
_WD_CHARS = "一二三四五六日天1234567"
_WD_CLASS = "[" + _WD_CHARS + "]"
# ⚠️ 数字后面紧跟「点/时/冒号」的那一颗**是钟点，不是星期几**：
#    「下周6点开会」「每周6点起床」要是被读成"周六/每周六"，就是把"下周 + 6:00"
#    换成了"下周六"—— 静默排错日子，比不认更坏。中文写法没这个歧义（「周六点」不成词），
#    但字符类是合在一起的，所以这道前瞻对整类一起加。
_WD_NOT_CLOCK = "(?![点时:：])"
# 「星期X / 礼拜X」以前**根本不在读日子的正则里**（只认「周X」），于是
# 「星期六去图书馆」日子读不出、`_pick_title` 还把整句当事名（卡片标题就成了「星期六去图书馆」）。
# 这条先于阿拉伯数字那一版就存在，同属"读日子的地方只认一种写法"，一起收口。
_WEEKDAY_RE = re.compile(r"(?:周|星期|礼拜)([" + _WD_CHARS + r"])" + _WD_NOT_CLOCK)

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
# 「一周三次 / 每周 3 回 / 一星期跑两趟」——**频次**是排期条件，不是事情的名字。
# 2026-10-08 之前它哪儿都没管：整句「我一周要去三次健身房」被原样当成待办名挂出去，
# 而"三次"这个数量直接丢了（用户报障：「日程的长久代办没法实现」）。
# 只认「周/星期/礼拜 + 次数」这一族。**「一天三次」（吃药那种）刻意不认**：
# 待办模型是一条 = 一天一个时段，按天铺开等于替学生造 3 条他没说要的东西，
# 宁可少认、回一句"这个我还排不了"，也别把兑现不了的承诺挂出去。
# 「一周三次 / 每周 3 回 / 一星期跑两趟」——**频次**是排期条件，不是事情的名字。
# 2026-10-08 之前它哪儿都没管：整句「我一周要去三次健身房」被原样当成待办名挂出去，
# 而"三次"这个数量直接丢了（用户报障：「日程的长久代办没法实现」）。
#
# 两条正则分工，**别合成一条**：
#   _FREQ_RE       认语境用（"周 + 次数"这一族确实出现了吗）——中间允许夹动词（"一周**要去**三次"）；
#   _COUNT_UNIT_RE 擦除用（只擦「三次」这个数量）；
#   _WEEK_SCOPE_RE 擦除用（只擦「一周/每星期」这个范围词）。
# 为什么擦的时候要拆两段：整块一起擦会把句子吃光——「一星期跑两回」整块擦完一个字不剩，
# 标题退回"待办"、候选卡直接出不来，学生只会被反问"要安排的是什么事"。
#
# 只认「周/星期/礼拜 + 次数」这一族。**「一天三次」（吃药那种）刻意不认**：
# 待办模型是一条 = 一天一个时段，按天铺开等于替学生造 3 条他没说要的东西，
# 宁可少认、回一句"这个我还排不了"，也别把兑现不了的承诺挂出去。
_FREQ_RE = re.compile(
    r"(?:"
    r"(?:一|每|这|本)\s*(?:周|星期|礼拜)"          # 带前缀：一周 / 每周 / 这星期
    r"|(?<![一二每这本])(?:周|星期|礼拜)(?![一二三四五六日天])"  # 光杆"周"，且不是"周四"那种星期几
    r")"
    r"(?:之?内|里)?[^，,。；;！!？?\d]{0,6}?"
    r"(?<!第)(\d{1,2}|[一二两三四五六七八九十]{1,2})\s*(?:次|回|趟|遍|练)(?!课)"
)
# 数量本身（"三次"/"3 回"）。`(?<!第)` 挡「周四下午第一次上课」——
# 那是节次不是频次；`(?!课)` 挡「这周还有两次课」——那是课，不是要排的待办。
_COUNT_UNIT_RE = re.compile(
    r"(?<!第)(\d{1,2}|[一二两三四五六七八九十]{1,2})\s*(?:次|回|趟|遍|练)(?!课)"
)
# 频次里的范围词（"一周"/"每星期"/"一礼拜"）。只在 _FREQ_RE 命中时才擦，
# 所以「忙了一周」「下周三」这类不带次数的写法不受影响。
_WEEK_SCOPE_RE = re.compile(r"(?:一|每|这|本)\s*(?:周|星期|礼拜)\s*(?:之?内|里)?")
# 语义层爱替学生"补"一个日子（实测样本库里存着 day_hint="周一至周日"）。
# 学生原话没点名哪天的时候，这种补出来的日子要能摘掉 —— 见 `carry_freq`。
_DAY_WORD_RE = re.compile(r"(?:下|本|这|上)?(?:周|星期|礼拜)[" + _WD_CHARS + "]" + _WD_NOT_CLOCK)

# ==== 每周重复（2026-10-10 加）：与上面的「一周 N 次」是两回事 ====
#   「一周三次」= 数量频次，**没有固定日子** → 只能在这一周里铺 N 条（test_35 那条路，不动它）。
#   「每个周天」= 固定星期几 + 无限重复   → 落成**一条** repeat=weekly 的记录，每周都出现。
# 判据必须带「每/所有/全部」这类**周期冠词**：少了它，「周日去健身」这种单日说法
# 会被判成永久重复 —— 那是替学生造了几十件他没说要的事，比漏判严重得多。
_WEEKDAY_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7,
                # 阿拉伯写法（2026-10-11 真机「每周6去图书馆」）。️ 不配「0」：
                # 「每周0」几乎没人这么写，而 0 到底是周日还是周一我们说了不算 —— 宁可少认。
                "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
_REPEAT_WEEKDAY_RE = re.compile(
    r"(?:每|所有|全部)(?:一个|一|个)?\s*(?:周|星期|礼拜)([" + _WD_CHARS + r"])"
    # 前瞻挡两类：① 次数（「每周一次大扫除」的"一"是次数，见 14b）；
    # ② 钟点（「每周6点起床」的 6 是 6:00，不是周六）。两道并一条字符类，
    # ⚠️ 别写成 `(?![次回趟遍][点时])` —— 那是"先次后点"两个字，等于没挡。
    r"(?![次回趟遍点时:：])")
# ⚠️ 后面那个前瞻是必需的（2026-10-11 真机）：「每周一次大扫除」里的"一"是**次数**，
# 不是星期几 —— 少了前瞻，这句会被读成"每周一"，系统替学生挑了个周一 19:00，
# 还写成"每周重复"挂出来（他从来没说过周一）。
# 擦标题/擦日子时用这条：整串周期说法（含「以后/一直」这类前缀和「都/都要」这类尾巴）一起擦。
# 星期几那个字**可选** —— 「每周都写作业」里没点具体周几，标题照样不该留「每周都」。
# ★ 什么叫"每天类"只写一份（`_DAILY_RE`），这条直接拼它的原文 ——
#   另抄一份词表就是第二个病（红线 13e：词表一旦分叉，多出来的那截是死条目）。
_DAILY_RE = re.compile(r"(?:每\s*一?天|天天|每日)")
# ★ 「从今天开始 / 从明天起 / 从现在开始 / 自今天起 / 从下周一开始」这一串**起手式**。
# 只从**事情名字**里擦，不许进日期解析那一步 —— 那句里的「今天/明天」正是"从哪天开始"的锚点，
# 在 parse_time_spec 里擦了就不知道从哪天起（实测：「从明天开始每天背单词」的锚点必须是明天）。
# 为什么要单独一条：不擦的话卡片标题就成了「从我要背单词」「从背单词」「自起练琴」——
# 2026-10-13 拿用户原话量的，三句里两句带「从」字残渣（这洞比 daily 早，「从今天开始背单词」一样中招）。
# ⚠️ 那个"日子"整块吃掉，别只吃「从」：只擦"从"的话，「从现在开始」会剩个「现」字在名字里。
# ⚠️ 周几要连着限定词整块吃（「下周一 / 这周五 / 上周三」）——
#    写成 `下[周天月]` 的话只吃两个字，「从下周一开始」剩「一」赖在名字里
#    （实测标题成了「从背单词」）。
_START_FORM_RE = re.compile(
    r"(?:从|自|由)\s*(?:现在|今天|今晚|明天|明早|明晚|后天|大后天"
    r"|(?:这|本|下|上|那)?(?:周|星期|礼拜)[一二三四五六日天]?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*[日号])?"
    r"\s*(?:开始|起始|起|以来|之后|以后)")
_REPEAT_PHRASE_RE = re.compile(
    r"(?:"
    r"(?:以后|从今以后|今后|长期|一直|始终)?\s*(?:每|所有|全部)(?:一个|一|个)?\s*"
    r"(?:周|星期|礼拜)(?:[" + _WD_CHARS + "末](?![点时:：]))?(?:都|都要|都会|得|会)?"
    r"|(?:(?:以后|从今以后|今后|长期|一直|始终)\s*)?" + _DAILY_RE.pattern + r"(?:都|都要|都会|得|会)?"
    r")")


# ★ 倒装语序（2026-10-13 真机「周天早上去健身房每周」）：先点某天、句尾再补一句「每周」。
# 单独一条正则、不塞进上面那个 pattern —— 同一个 pattern 里两个同名组 Python 直接报错
# （`redefinition of group name 'wd'`，施工单那版就是栽在这儿，粘进去整个模块 import 失败）。
# ⚠️ 两段之间**允许隔任意内容**：写成「周X」紧跟「每」的话，在它自己举的那两句上都不命中
#   （「周天」和「每周」中间隔着"早上去健身房"六个字）—— 量过，两条都返回 None，照抄就是死分支。
_REPEAT_WEEKDAY_INV_RE = re.compile(
    r"(?<![下上这明昨本])(?:周|星期|礼拜)([" + _WD_CHARS + r"])(?![次回趟遍点时:：])"
    r"[\s\S]*?"
    # 「每」后面不能是另一个星期几 / 数量 / 别的单位：那些各有主人 ——
    #   「每周三」归正装那条（实测「周日去健身房，每周三交报告」在这里返回 None，
    #   靠的就是下面那张拦截表，不是靠"上面先查"）、
    #   「每周两次」是频次（test_35 那一路）、「每月/每年」归 `unsupported_period` 的硬闸门。
    # ⚠️ 那个「周」要**先吃掉再 lookahead**，而且「周/星」本身也得进拦截表：
    #   只把「周」当可选单位、表里却放行，引擎就会选"不吃"这条路 —— 「每周两次」
    #   在"每"后面直接看到"周"（不在表里）照样命中（实测：这条第一版没挡住）。
    #   两条路都堵住之后：「…每周」收尾 ✓ 认，「…每周两次」✗ 不认。
    r"(?:每|所有|全部)(?:周|星期|礼拜)?"
    r"(?![一二三四五六日天两几多0-9次回趟遍点时:：月年周星])")


def weekly_repeat_day(text: str) -> int | None:
    """这句话点名了「每周固定某一天」吗？回了 1..7（周一=1）就是重复，None 就是没说。

    ⚠️ 故意**不认**「每个周末」（那是周六+周日两天，一条记录表达不了）和
    「每周都…」（没点哪天 —— 替他挑一个周日，等于替他做主）。宁可少认。
    两种语序都认：正装「每周天去健身房」先查，倒装「周天去健身房每周」兜底。
    ⚠️ 量过：两条同时命中的句子（「每周三上课，周日去健身房每周」）给的是**同一个答案**，
    倒装那条对「周三开会每周五汇报」根本不命中 —— 所以"谁先查"现在不改结果。
    顺序仍是刻意的（更具体、更严的那条在前），别无声调换：test_39 H 批钉着这个形状。
    """
    t = text or ""
    m = _REPEAT_WEEKDAY_RE.search(t)
    if m:
        return _WEEKDAY_NUM.get(m.group(1))
    m = _REPEAT_WEEKDAY_INV_RE.search(t)
    return _WEEKDAY_NUM.get(m.group(1)) if m else None


# ==== 每天类习惯（2026-10-13 用户 Prompt 02）====
# 「我每天都要背单词」= 固定每天 + 无限重复 → 落成一条 repeat=daily。
# 词表是上面的 `_DAILY_RE`（标题/日子擦除与这里共用一份，不另抄）。
# 与 weekly 天然互斥：daily 不点名星期几（`weekly_repeat_day` 对「每天」返回 None）。
# ⚠️ 不认「今天/明天/昨天」：那是具体某一天（走 date），不是每天重复 ——
#   「每」是必需前缀，"今天"的"天"前面是"今"，实测这条正则不会在最左把它吃掉。
def daily_repeat(text: str) -> bool:
    """这句话是不是「每一天都要做」？（它不带星期几，落库就是一条 daily 习惯）"""
    return bool(_DAILY_RE.search(text or ""))


def start_anchor(text: str) -> str:
    """「从今天/明天/下周一开始」那句里的**起始日**（没说就从今天起，回空串）。

    ⚠️ 只能从起手式那一小截里取日子，**不许拿整句去 `_pick_date`**：
    「以后天天背单词」整句抠会抠出 `2026-10-15` —— 因为「以后**天天**」四个字里连着
    一个「后天」，被当成"后天"跳了两天（2026-10-13 拿用户原话实测到的）。
    起手式是「从X开始/自X起」这种带介词的说法，「以后/长期」根本不该进日子判据。
    """
    m = _START_FORM_RE.search(text or "")
    return _pick_date(m.group(0)) if m else ""


def repeat_of(text: str) -> str:
    """这句话该落成哪一种 repeat —— **判据只写这一处**，五条出卡路径都问它。

    顺序即规则：先 weekly 后 daily。一句里同时出现「每周几」和「每天」时，
    点名了星期几的那半是更具体的承诺（有固定日子，该画进日历），
    不许被 daily 抢成"不进日历的习惯"。
    ⚠️ 别再在各调用点手写 `REPEAT_WEEKLY if weekly_repeat_day(...) else REPEAT_NONE` ——
    那种写法每多一档 repeat 就要多改一处，漏一处就是"卡上写着每天、库里落成一次性"
    （2026-10-11 真机三个缺口里的一条，接手指南红线 11b）。
    """
    if weekly_repeat_day(text):
        return REPEAT_WEEKLY
    if daily_repeat(text):
        return REPEAT_DAILY
    return REPEAT_NONE


# ==== 明确不支持的周期说法（2026-10-10）====
# 这些表达**规则层从来不接**（`wants_add_todo` 一律 False），但真机实测发现：
# 样本库/语义层可以绕过规则层单独接单 —— 用户那句
# 「在下个月的每个周末三点开始大约三个小时」被**样本库**判成 add_todo、
# 事名学成了「活动」，排出一条 15:00-18:00 的孤零零待办。
# 所以判据要有一处**不分层**的收口：只要句子里出现下面这些说法，
# 任何一档说"这是加待办"都不许出卡 —— 因为待办模型表达不了它
# （工作日=多个星期几、周末=两天、每两周/每月=非周周期、下个月=范围边界）。
# ⚠️ 2026-10-13 摘掉「每天/天天/每日」三项：模型现在表达得了（`REPEAT_DAILY` 一条记录，
#    只进代办不进日历 —— 用户 Prompt 02）。其余各项一律不动。
# ⚠️ 不含「平时/工作日」：`candidate_slots` 的 workday_mode 早就支持"跨工作日摊开列"，
#    把它们列进来会打掉现有能用的功能（test_28 那一类）。
_UNSUPPORTED_PERIOD = (
    "每个周末", "每周末", "每个星期末", "隔一天", "隔天",
    "每两周", "两周一次", "每月", "每个月", "一月一次", "下个月", "上个月", "每个月末",
)
# 「跨度 + 次数」的**结构**判据（词表抄不完：2026-10-11 真机就漏了「两周去一次」这种语序，
# 漏了还被样本库学进去，以后每次近似说法都照着错）。三条一起用：
#   ① 单位是「月」→ 一律表达不了（一个月两次也不行 —— 一条记录落不了"每个月"）；
#   ② 单位是「周/星期/礼拜」且**跨度不止一周** → 表达不了；
#      正好一周（「一周三次」）是现有能力：在这一周里铺 N 条，别拦。
#   ③ 「隔 N 天/日」这种跳过几天的 → 也表达不了（词表里只有「隔天/隔一天」）。
# 次数必须跟在跨度后面、六个字以内，免得「10月20号那周我还有三次课」被误伤。
_SPAN_UNIT_RE = re.compile(r"([一两二三四五六七八九十]{1,2}|\d{1,2})\s*(个?周|星期|礼拜|个?月)")
_TIMES_RE = re.compile(r"([一两二三四五六七八九十]{1,2}|\d{1,2})\s*(?:次|回|趟|遍)(?!课)")
_SKIP_RE = re.compile(r"隔\s*([一两二三四五六七八九十]{1,2}|\d{1,2})\s*(?:天|日|周|星期|个月|月)")
# 「每周两次」这种"一周好几回 + 长期"也表达不了：一条记录落不了长期，铺 N 条只管这一周。
# ⚠️ 后面紧跟星期几的（「每周三开一次会」）是**现有能力**（repeat=weekly），必须让路 ——
#    少了这个前瞻，"每周三…一次"就被当成"每周一次以上"拦掉了。
_EVERY_UNIT_RE = re.compile(r"(?:每|所有|整)\s*(?:个|一)?\s*(周|星期|礼拜|个月|月)(?![一二三四五六日天])")


def unsupported_period(text: str) -> str:
    """这句话要的是不是一种**当前模型表达不了**的周期？要到了就把词原样交回去。"""
    t = text or ""
    for w in _UNSUPPORTED_PERIOD:
        if w in t:
            return w
    skip = _SKIP_RE.search(t)
    if skip:
        return skip.group(0).strip()
    ev = _EVERY_UNIT_RE.search(t)
    if ev:
        tm = _TIMES_RE.search(t[ev.end():ev.end() + 5])
        if tm and _num(tm.group(1)):
            return ev.group(0) + t[ev.end():ev.end() + tm.end()]
    sp = _SPAN_UNIT_RE.search(t)
    if not sp:
        return ""
    n = _num(sp.group(1))
    unit = sp.group(2)
    month = "月" in unit
    if n in (None, 1) and not month:
        return ""                      # 「一周三次」= 现有能力，不拦
    rest = t[sp.end():sp.end() + 6]
    tm = _TIMES_RE.search(rest)
    if tm and _num(tm.group(1)):
        return sp.group(0) + rest[:tm.end()]
    return ""


def period_reject(text: str) -> str:
    """三件提案工具共用的**拒单回话**（不是周期就回空串，调用方照旧往下走）。

    为什么单列一份：`propose_todo_tool` / `propose_todo_mode_tool` / `propose_todo_slots_tool`
    是模型手里直接出卡的三条路，**它们绕得过判定层、绕不过这里**。
    少这一道，学生说「每天背30个单词」，模型就能自己替他挑一天出确认条 ——
    跟样本库单独接单是同一类翻车（判据同 `unsupported_period`，别各写一套）。
    """
    word = unsupported_period(text)
    if not word:
        return ""
    return json.dumps({
        "error": f"「{word}」这种周期安排不了",
        "hint": ("一条日程只能落**某一天**，或**每周固定的某一天**（repeat=weekly）。"
                 f"学生说了「{word}」→ ⛔ **不要替他挑一天出确认条**，"
                 "回一句跟他说清能接的两种说法：「每周几」建一条以后每周都出现、"
                 "「这一周N次」在这一周里铺N段；想只排一次就让他报个日子。"),
        "got": (text or "").strip(),
    }, ensure_ascii=False)


# 出现在"名字"里就说明抠到的不是事情，是时间/类别/动作残渣
_TITLE_NOISE = (
    # 时间与时长残渣
    "小时", "钟头", "分钟", "时长", "时间", "几点", "大概", "大约", "左右",
    # 类别词：学生嘴里的"待办/代办/日程"是**类别**，不是事情本身
    "待办", "代办", "日程", "事项", "事情",
    # 只剩动作词：说了"帮我安排"却没说安排什么
    "帮我", "帮忙", "安排", "添加", "加入", "加进", "排一下", "记一下",
)
# ==== 问「该做什么」的那族说法（2026-10-14 用户定案：这是问事/闲聊，不是下单）====
# 原话：「**今晚安排点啥** 这种是问事情闲聊的，不入库」→ 修法他选的是
# 「不建卡，但给一句实在的建议」（见 `what_to_do_suggestion`、test_45）。
#
# 病根：擦完剩下的其实是一句**疑问串**，不是一件事的名字，可它长度够、也不在
# `_TITLE_NOISE` 里，于是过了判真 —— 实测卡片标题印着「点啥」「点什么」「啥比较好」
# 「说我啥」「点事给我」（无密钥兜底那一档更糙，`_guess_title` 直接拿整句话当名字）。
#
# ⚠️ 判据写成"**这串的首或尾**是疑问代词"，不是"含疑问代词就否"：
#   含就否会把《什么是时间管理》这类真名字一起挡掉 —— 同「写好/排好」那条教训
#   （宁可漏掉几个噪声，也不误伤真名字）。
# ⚠️ 这张表**只有这一份**：`looks_like_thing`（能不能当事名）与
#   `_asking_what_to_do`（这句是不是在问做什么）都从这儿取，别再抄第二份（红线 10）。
_WH_ASK_WORDS = ("啥", "什么", "嘛")
# "点事 / 一些事"这种**模糊量词 + 类别词**等于没报事名（「安排点事给我」实测抠出「点事给我」）。
# 不收"点东西"：「去买点东西」是正经待办，收了就误伤。
_VAGUE_THINGS = ("点事", "一些事", "些事")
# 疑问串常见的尾巴（「点啥呢」「什么好」「啥比较好」）——剥掉才看得出首尾
_VAGUE_TAIL_RE = re.compile(r"[呢啊呀哦吧好样较]+$")
# 「我(现在)都有啥安排」「有啥事」「今天都有什么课」—— 问的是**已经有什么**（查清单），
# 不是"该做什么"。判据：疑问代词前面出现过"有"。
_HAS_LIST_ASK_RE = re.compile(r"有[^，,。？?!！]{0,8}(?:啥|什么|嘛)")


def _has_delete_intent(text: str) -> bool:
    """这句是不是在**删/取消**一件事（含手误写法与口语"不要了"）。

    从 `wants_add_todo` 里搬出来的同一道闸：那张表以前内联在那儿，现在两处要用
    （加待办让路、问"该做什么"排除），抄第二份迟早走样（指南红线 10）。
    """
    t = text or ""
    return (bool(_DEL_ANY_RE.search(t))
            or any(w in t for w in _REMOVE_TODO_INTENT)
            or any(w in t for w in _REMOVE_TODO_SOFT))


def _vague_ask_name(name: str) -> bool:
    """这串其实是"在问做什么"（点啥 / 点什么 / 啥比较好 / 说我啥 / 点事）——不是事名。"""
    core = _VAGUE_TAIL_RE.sub("", (name or "").strip())
    if not core:
        return True                      # 剥完什么都不剩，那更不是一件事
    for w in _WH_ASK_WORDS + _VAGUE_THINGS:
        if core.startswith(w) or core.endswith(w):
            return True
    return False


# 「啥时候放假 / 什么时候开学 / 哪天报到 / 几月几号截止」问的是**什么时候发生**，
# 不是「我该做什么」。#42-A：`_vague_ask_name` 是 `startswith("啥")`，任何以"啥"开头的短句都吃，
# 所以时间问句必须在它**之前**排掉（实测「啥时候放假」「什么时候开学」原先都被判成问该做什么，
# 屏幕回的是"我不替你决定 + 这几段是空着的"）。
# ⚠️ 这条只管"问事件的时间"，不碰"问自己的空档"：`我什么时候有空` 本来就不在这族里
#    （剥完首尾不是疑问代词），`明天什么时候有空` 排除后交给模型/空档那条，实测答得更准。
_WHEN_ASK_RE = re.compile(r"(?:啥|什么)时候|哪天|几号|几月几[号日]|多久|几点")


def is_time_ask(text: str) -> bool:
    """这句是在问**某件事什么时候发生**（#42-A 的另一半：校园事实归属也用它）。"""
    return bool(_WHEN_ASK_RE.search(text or ""))


# 「考试时间是什么」问的是**那件事的内容**，不是"我该做什么"（#43 G7 实测：这句被回成
# "我不替你决定 + 这几段是空着的" —— #42-A 只挡了"什么时候"，没挡「是什么」）。
# 反向的口子必须留着：「我该做什么」「要做什么」也是"…做什么"，但那**确实**是问该做什么。
# 所以这条只认"疑问串前面还挂着一个具体事名"（≥2 个汉字 + 是/为 + 什么/啥，并在句尾）。
_ASKS_CONTENT_RE = re.compile(r"[\u4e00-\u9fff]{2,}(?:是|为)(?:什么|啥)[吗呢啊]?[？?]?$")
# 反向那条**只认句尾**（锚 `$`），并且动词表里**故意没有"排"** ——
# 「考试安排是什么」里的"安排"是名词，一放进表里就会被当成"我该做什么"那族吃掉（D5 钉这个）。
_DO_ASK_RE = re.compile(r"[做干办搞整][点些]?(?:什么|啥)$")


def asks_about_content(text: str) -> bool:
    """"X 是什么 / X 是啥"：问的是那件事的内容（#43），不是"我该做什么"。"""
    t = (text or "").strip()
    if not _ASKS_CONTENT_RE.search(t):
        return False
    return not _DO_ASK_RE.search(t)


def _asking_what_to_do(text: str) -> bool:
    """这句话是不是在问「该做什么」（人话：他没报出事名，是在让你出主意）。

    两道一起才算：① 句子里有疑问代词（啥/什么/嘛）；② **剥掉时间、动作词之后剩下的那串**
    本身就是个疑问串（「点啥」「干点啥」「啥比较好」）。
    第②道用裸串而不是"抠不出事名"，是因为抠不出事名有**别的原因**：
    实测「安排看《什么是时间管理》」标题被「时间」这个噪声词挡掉了（噪声表一直就这么定），
    拿"抠不出"当判据会把这句有真名字的下单句判成问事 —— 而裸串「看《什么是时间管理》」
    首尾都不是疑问代词，判据不放行。
    「帮我安排点事」也不算这族：句里没有疑问代词，他是**说了要加、没说加什么**，
    该走原来的追问档（「要安排的是什么事？」）—— 事名「点事」已经不再放行（见 `_VAGUE_THINGS`）。
    只满足②不算：「你帮我安排」没疑问代词，照旧走追问（test_06 十五批钉着，别一起改掉）。

    四道排除：
      · **在删东西**：「把那个啥删了」带疑问代词，可他是在删一条叫不出名字的待办 ——
        被这档截走就等于当着要删东西的人报空档（test_15 那条红咬回来的）。
      · **在查现有清单**：「我现在都有啥安排」问的是"已经有什么"，不是"该做什么"，
        那该走列表档；判成这族会抢掉它（test_08 ⑥ 的复核前提也因此得留着）。
      · **在问某件事什么时候发生**（#42-A）：「啥时候放假 / 什么时候开学」——
        `_vague_ask_name` 是 `startswith("啥")`，任何以"啥"开头的短句都吃，
        于是校历问题被回成"我不替你决定 + 这几段是空着的"（用户 2026-10-19 真机截图）。
      · **在问某件事是什么**（#43）：「考试时间是什么」——同一族的漏（那条只挡"什么时候"），
        实测这句也被回成"我不替你决定"。反向钉住「我该做什么」照旧算（判据见 `asks_about_content`）。
    """
    t = (text or "").strip()
    if not t or len(t) > 30:             # 长句多半在讲一件事的来龙去脉，不在这族里
        return False
    if _has_delete_intent(t) or _HAS_LIST_ASK_RE.search(t):
        return False
    if _WHEN_ASK_RE.search(t):           # #42-A：问"什么时候发生"不是问"我该做什么"
        return False
    if asks_about_content(t):            # #43：问"那件事是什么"也不是问"我该做什么"
        return False
    if not any(w in t for w in _WH_ASK_WORDS):
        return False
    return _vague_ask_name(_strip_title_noise(t))
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


def _num(s: str) -> int | None:
    """阿拉伯数字、中文数字都转成 int（人话：'3'→3、'三'→3、'十'→10）。

    月日两种写法学生混着用（"10月3号"/"10月三号"），统一走这里再拼日期。
    """
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    return _cn_num(s)


def weekly_times(text: str) -> int | None:
    """读出「一周 N 次」里的 N（人话：'我一周要去三次健身房'→3）。读不出返回 None。

    上限 7：一周七次已经是每天一回，再多就是听错了，宁可不认。
    **N=1 也照实回 1** —— 调用方拿它决定"要不要排多段"，
    但"一周一次"里的频次字样仍然要从标题里擦掉（那是排期条件，不是事情名）。
    """
    m = _FREQ_RE.search(text or "")
    if not m:
        return None
    n = _num(m.group(1))
    if n is None or n < 1 or n > 7:
        return None
    return n


# 「N 次」的**省略写法**：上一句已经说过"周"，这一句只报个数（「是改成三次」）。
# 认得极窄：整片必须是「(纠正前缀)?(范围词)?N次」，前后不许再挂别的东西 ——
# 「我去了三次图书馆」这种陈述句因此一条都进不来（片里有"我去了""图书馆"）。
# 为什么要自己认「下周三次」而不是靠 `weekly_times`：`_FREQ_RE` 里那个
# `(?![一二三四五六日天])` 是**故意的** ——「周三」得读成日子、不能读成"礼拜三次"，
# 代价就是「下周三次」它不认。那个 lookahead 不能动（动了全项目遭殃），
# 所以这一档在补丁自己的窄口里补上，影响面只有"补充句"这一条路。
# ⚠️ 这张表只给 `parse_todo_patch` 用。**不要拿去放宽 `weekly_times`**：那个函数被
# `todo_slots_proposal` 三条分支和 `unsupported_period` 共用，一放宽，凡是带「N 次」的
# 话都会开始要学生排日程，而那类误伤在测试里往往没人写、只有真机上学生会踩到。
_PATCH_PREFIX_RE = re.compile(
    r"^(?:是|不对|不是|应该|我说的是|还是|就|要|只要)*\s*"
    r"(?:改成|换成|改为|变成|调成|排成)?\s*")
_PATCH_TIMES_RE = re.compile(
    r"^(?:(?:下|本|这|上|每)?(?:周|星期|礼拜))?\s*"
    r"([0-9]{1,2}|[一二两三四五六七八九十]{1,2})\s*(?:次|回|遍|趟)$")


def parse_todo_patch(text: str) -> dict:
    """这一句**改了什么**：说到的填值，没说的填 None（人话：不许替他重算）。

    学生说「改成三次，每次一小时」，就只交出 `{"times":3,"duration":60}`，
    事名、日期、钟点一律 None —— 那些他没提，上一轮的答案就该原样留着。

    为什么要有这个函数：`weekly_times` / `_pick_title` / `_pick_span` / `_pick_date` /
    `wanted_minutes` 的签名都只收一句话，结构上没有"上一轮"这个入口。以前补一句信息是
    拿整串重猜一遍（实测：次数从 None 救回 3 ✓，事名却从「健身」变成「健身是每次」✗）——
    猜对一项就赌坏另一项。这里把"这句话到底说了哪几项"先量出来，
    合并那一步（见 `main.py` 的加待办分支）只覆盖他明说的项。

    `title` 一律 None：事名是这一族里最容易猜坏、也最贵的字段（卡上写着谁也没说过的
    「每次」，学生只会认为系统没听懂）。要换事名走的是**全新下单**那条路，由调用方的
    否决（`_veto_as_new_order`）负责，不在这里猜。
    """
    t = (text or "").strip()
    out = {"times": None, "duration": None, "date": None,
           "start": None, "end": None, "title": None}
    if not t:
        return out
    times = weekly_times(t)
    if times is None and len(t) <= 12:
        pieces = [p.strip() for p in re.split(r"[，,。；;、\s]+", t) if p.strip()]
        hit = None
        for p in pieces:
            m = _PATCH_TIMES_RE.match(_PATCH_PREFIX_RE.sub("", p))
            if m:
                hit = p
                n = _num(m.group(1))
                if n is not None and 1 <= n <= 7:
                    times = n
                break
        # 除了那一片"N次"，别片擦完不许剩下一个合法事名 ——
        # 「三次，去游泳」这种混着新名字的，不配当补丁（宁可走全新下单）。
        if times is not None:
            for p in pieces:
                if p == hit:
                    continue
                cand = _strip_title_noise(p)
                if cand and cand != "待办" and _title_ok(cand):
                    times = None
                    break
    out["times"] = times
    out["duration"] = wanted_minutes(t)
    # 「下周三次」里的"下周"会被 `_pick_date` 读成一个具体日子（同 `todo_slots_proposal`：
    # 那里也先用 `_FREQ_RE` 把频次短语摘掉再找日子），所以这儿同源处理。
    out["date"] = _pick_date(_FREQ_RE.sub(" ", t))
    sp_begin, sp_finish = _pick_span(t)
    # 裸钟点（1..6 点、没说上下午）不进补丁：判据与 `span_only_proposal` / `_day_slots`
    # 同一条 —— 替他猜"下午三点"错了要改一整条安排，问一句的成本低得多。
    sp = parse_time_spec(t)
    if sp_begin and (not sp.get("clock") or sp.get("clock_ok")):
        out["start"] = sp_begin
        out["end"] = sp_finish
    return out


def patched_todo_blob(base_phrase: str, message: str) -> str:
    """把"上一轮剩下的那半句/那个名字"和这一句缝成解析器吃的一句话（#73）。

    拼接只是**为了让只收一句话的老解析器看得见事名和日子**，最终屏幕上写什么名字由
    `retitle_todo_card` 按铁律定 —— 两件事必须分开：实测光靠拼接吐出来的事名
    就是「健身是每次」，救回次数、弄坏名字。
    """
    b = (base_phrase or "").strip().strip("，,、 ")
    m = (message or "").strip().strip("，,、 ")
    if not b or not m:
        return b or m
    if b in m:
        return m
    if m in b:
        return b
    return f"{m}，{b}"


def retitle_todo_card(card: dict, name: str) -> dict:
    """把一张待办卡的事名换成 `name`，`summary` 跟着重算（事名铁律的落点）。

    只走这一处，别在调用点手拼 summary：候选卡回到 `build_todo_slots` 重造，
    单条卡按 `_make_todo_card` 那一行的格式重算 —— 模板各只有一份，
    抄第二份迟早和屏幕上那句对不上。
    """
    if not isinstance(card, dict) or not name or card.get("title") == name:
        return card
    if card.get("kind") == "todo_slots":
        return build_todo_slots(name, card.get("slots") or [],
                                hint=card.get("hint") or "",
                                preselect=card.get("preselect") or 0,
                                repeat=card.get("repeat") or "",
                                times=card.get("times") or 0,
                                duration=card.get("duration") or 0)
    out = dict(card)
    out["title"] = name
    if out.get("date") and out.get("start"):
        out["summary"] = (f"{name}｜{out['date']}（"
                          f"{out.get('weekday') or _weekday_name(out['date'])}）"
                          f"{out['start']}-{out.get('end') or ''}")
    return out


def todo_patch_empty(patch: dict) -> bool:
    """这一句什么都没明说（人话：它不是一句补丁，别拿它覆盖卡上的任何东西）。"""
    return not any(v is not None for v in (patch or {}).values())


# 全角 → 半角，**只转跟时间沾的这几个字符**。
# 为什么不用 `unicodedata.normalize("NFKC", …)` 整句归一：那会把书名号/括号/顿号一起换掉，
# 而 `_fix_typo_time_words` 与标题残渣那几处判据全是按中文标点写的 —— 一次全变，
# 连带改掉标题抽取的结果（那是另一族 bug 的入口）。
# 这张表**只许有一份**：别在任何调用点再抄一张。
_FW2HW = str.maketrans("０１２３４５６７８９：－—–～　", "0123456789:---~ ")


def normalize_fullwidth(t: str) -> str:
    """把上面那张表应用一次（人话：全角的数字/冒号/破折号先变成半角）。

    为什么单独露一个入口：材料那条路要**先洗整段再拆行** —— 全角日期（「１０月２５日」）
    走的是 `_pick_date`，它不经过 `_normalize_clock`。表还是那一张，只是多一个入口；
    在调用点再抄一张就是第二份真相。
    """
    return (t or "").translate(_FW2HW)


def _normalize_clock(t: str) -> str:
    """把中文报时换成数字钟（人话：'两点'→2:00、'两点半'→2:30）。

    为什么要有它：学生说时间十有八九是「下午两点到三点」这种口语，
    原来的正则只认阿拉伯数字，「两点」在它眼里就是没有时间——
    于是整句话掉回大模型，模型顺嘴一句"搞定，已经写进待办啦"，其实什么都没写。

    2026-09-30 起它**也负责纠时间词的形近笔误**（「中文」→「中午」）。
    放在这里是因为它是"跟时间沾边的规范化"的**唯一入口**：抠标题（`_strip_title_noise`）
    和抠时段（`_pick_span`）都从这儿过。放一处，两条路一起受益：
      · 抠标题那条 —— 不纠的话「中文」不是时间词、擦不掉，会整串留在标题里；
      · 抠时段那条 —— 「下物三点」纠成「下午三点」才会算出 15:00 而不是 03:00。
    判据（三道闸门）见 `_fix_typo_time_words`。

    2026-10-21 起它**还负责全角→半角**（`_FW2HW`，就在最前面一刀）：
    中文输入法下「19：30-21：30」是默认打法，全角冒号让整条钟点读不出来，
    于是粘进来的考试安排被回一句「里面没有一条写着日期或钟点」（真机）。
    """
    t = normalize_fullwidth(t)

    def rep(m):
        h = _cn_num(m.group(1))
        if h is None or h > 23:
            return m.group(0)
        return f"{h}:30" if m.group(2) else f"{h}:00"

    t = _fix_typo_time_words(t or "")
    t = re.sub(r"([零一二两三四五六七八九十]{1,3})点(半)?", rep, t)
    # '3点20' 这种"数字点+分钟"也顺手归一成 3:20
    def rep2(m):
        h, mm = int(m.group(1)), int(m.group(2))
        if h > 23 or mm > 59:
            return m.group(0)
        return f"{h}:{mm:02d}"
    t = re.sub(r"(\d{1,2})\s*点\s*(\d{1,2})\s*分?", rep2, t)
    # ⚠️ 阿拉伯数字写法的「6点半」以前**没人管**：中文「六点半」由上面第二条归成 6:30，
    # 「6点半」却只被认成 6 点 —— 半小时被**静默吞掉**，学生说 6 点半、卡片排 06:00。
    # 静默排错时间比排不进更坑（同一批还查到「明晚七点」被改写成「明天七点」）。
    # 只有"点+半"这一种形状会进来：「半小时」前面没有「点」，中文写法上面那条已经归一过了。
    t = re.sub(r"(\d{1,2})\s*点\s*半",
               lambda m: f"{int(m.group(1))}:30" if int(m.group(1)) <= 23 else m.group(0), t)
    return t


# 时段词表：决定一个 12 小时制的钟点该不该 +12（人话：下午/晚上 = 加，上午/早上 = 不加）。
_PM_WORDS = ("下午", "午后", "傍晚", "晚上", "夜里", "晚间", "今晚", "今夜",
             "明晚", "后晚", "昨晚")     # 「明晚八点」少这三个词就会被当成早上 08:00（2026-10-11 真机查到）
_AM_WORDS = ("上午", "早上", "早晨", "一早", "凌晨", "清晨", "半夜", "午夜")
_MIDNIGHT_WORDS = ("凌晨", "半夜", "午夜")
_SPAN_PERIOD_WORDS = _AM_WORDS + ("中午",) + _PM_WORDS


def _period_hour(ctx: str, whole: str, hour: int | None) -> int | None:
    """把一个钟点拨成 24 小时制（人话：'下午2点'→14，'早上8点'还是 8）。

    为什么拆成 ctx 和 whole 两层：一句「上午9点到下午5点」两头时段词**不一样**——
    老写法只看整句，会被"下午"带偏，读成 21:00-17:00。所以每个钟点**先看它自己前面**
    那段字里的时段词；自己前面没说，才拿整句兜底（「4点到晚上7点」这种只把"晚上"
    说了一次的写法，前头的"4点"仍该按整句的"晚上"算下午）。
    """
    if hour is None or hour > 12:
        return hour
    # 「半夜12点」「凌晨12点」是**当天 0 点**，不是中午 12 点（2026-10-11 真机读成了 14:00）。
    # 只在这一处特判 12，别的钟点照旧走下面的时段词判断。
    if hour == 12 and any(w in (ctx or "") for w in _MIDNIGHT_WORDS):
        return 0
    if any(w in (ctx or "") for w in _PM_WORDS):
        return hour + 12 if hour < 12 else hour
    if any(w in (ctx or "") for w in _AM_WORDS):
        return hour                     # 自己前面白纸黑字写了"上午/早上"，就是早上，不加
    # 自己前面没说时段词 → 拿整句兜底；但整句里只要出现"上午/早上"，
    # 说明这是一句"上午…到下午…"的话，这个没说清的钟点按早上算（宁可不动）。
    if any(w in (whole or "") for w in _AM_WORDS):
        return hour
    if any(w in (whole or "") for w in _PM_WORDS):
        return hour + 12 if hour < 12 else hour
    return hour


# 「起 到 止」：两头各自可以是 19:00 / 7点 / 下午四点（"四点"已被 _normalize_clock 归一）。
# 中间允许夹一个时段词（"到**晚上**7点"）——少了这一档，「下午四点到晚上7点」的右边就
# 认不出来，整句退化成"只有一个起始钟点"，再被按默认 1 小时补成 16:00-17:00
# （学生截屏投诉的就是这一条）。
_SPAN_RE = re.compile(
    r"(\d{1,2})\s*(?::(\d{2})|点)\s*(?:到|至|-|~|～)\s*"
    r"(?:" + "|".join(_SPAN_PERIOD_WORDS) + r")?\s*(\d{1,2})\s*(?::(\d{2})|点)"
)
_CLOCK_RE = re.compile(r"(\d{1,2})\s*(?::(\d{2})|点)")


def _pick_span(text: str):
    """从一句话里抠出「起-止」时间。

    认这些写法（人话：学生怎么顺口怎么说）：
      · 19:00-20:30 / 16:30~18:00（全阿拉伯，含波浪号）
      · 晚上七点到八点（全中文）
      · **下午四点到晚上7点**（中英混用，而且两头时段词还不一样）
      · 19点到20点 / 下午2点到3点

    返回 (start, end)：只给了一个时刻时 end 为 None（单个钟点算**起点**，不是终点）。
    """
    t = _normalize_clock(text or "")

    # ① 先找「起 … 到 … 止」一对钟点。两头的时段词各看各的（见 _period_hour）。
    m = _SPAN_RE.search(t)
    if m:
        sh = _period_hour(t[:m.start(1)], t, int(m.group(1)))
        eh = _period_hour(t[:m.start(3)], t, int(m.group(3)))
        sm = int(m.group(2)) if m.group(2) else 0
        em = int(m.group(4)) if m.group(4) else 0
        # 止比起还早 = 跨过了中午（"12点到1点"其实是 12:00-13:00）。
        # 注意必须是严格小于：写成 <= 的话 "7:00-7:40" 会被拨成 19:40。
        if eh is not None and sh is not None and eh < sh and eh <= 11:
            eh += 12
        return (f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}")

    # ② 只给了一个钟点 → 当起点，终点留给调用方（"真没给结束时间"那一档才补默认值）
    m = _CLOCK_RE.search(t)
    if m:
        sh = _period_hour(t[:m.start(1)], t, int(m.group(1)))
        sm = int(m.group(2)) if m.group(2) else 0
        return (f"{sh:02d}:{sm:02d}", None)
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
        # 只写"9月29号"没写年份 → 按今年算（学生日常都这么说）。
        # 月/日两头都可能是中文数字（"10月三号"/"十月三日"），统一用 _num 转。
        mo, da = _num(m.group(1)), _num(m.group(2))
        if mo and da:
            return (f"{clock.today().year}-"
                    f"{mo:02d}-{da:02d}")
    today = clock.today()
    for word, off in (("大后天", 3), ("后天", 2), ("明天", 1), ("今晚", 0), ("今夜", 0), ("今天", 0),
                      # 「明晚/明早」以前靠笔误闸门把它改成「明天」才碰上日期 —— 那一步顺带把
                      # "晚"字里的**晚上**信息擦了（明晚八点 → 早上 08:00）。现在两头都认：
                      # 时间词表登记了「明晚」不再被改写，这里也直接算出明天。
                      # ⚠️ 不加「昨晚」：那是过去，算出日期只会排一条学生已经错过的待办。
                      ("明晚", 1), ("明早", 1)):
        if word in t:
            return (today + datetime.timedelta(days=off)).isoformat()
    m = _WEEKDAY_RE.search(t)
    if m:
        # 以前这里又手写了一份 `"一二三四五六日".index(ch) + 1`（还先把「天」换成「日」）——
        # 学生写「周6」时 `index` 直接 ValueError 整条链路崩掉。收成一张表：
        # `_WEEKDAY_NUM` 里中文/数字/「天」都有映射，认不出就返回 None（宁可少认）。
        want = _WEEKDAY_NUM.get(m.group(1))
        if want is None:
            return None
        base = clock.today()
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
      · 整串只剩数字/中文数字/钟点词（"12" / "十二" / "12点" / "十点半"）
        → 那是**时间碎片**，不是一件事（学生说"明天中午12"，话还没说完）；
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
    # 时间碎片："12" / "十二点" / "12点" / "十点半" / "3分" / "整点" 都不是一件事
    if re.fullmatch(r"[0-9零一二两三四五六七八九十半刻点分整]+", t):
        return False
    # 疑问串残渣："点啥" / "点什么" / "啥比较好" / "说我啥" / "点事" —— 他在问该做什么，
    # 不是在报一件事（判据与 `_asking_what_to_do` 同一份表，见 `_WH_ASK_WORDS` 上面的注释）
    if _vague_ask_name(t):
        return False
    return not any(w in t for w in _TITLE_NOISE)


# 真正的"时间词"（擦标题时要擦掉的那一批）。**只写一份**：抠标题、抠待办名、
# 形近字纠正三处都从这儿取，省得改一处忘两处。
_TIME_WORDS = ("今天", "今晚", "今夜", "明天", "后天", "大后天",
               "上午", "下午", "中午", "晚上", "早上", "夜里", "周末",
               # ↓ 2026-10-11 补：这些**本来就是时间词**，漏登记就会被笔误闸门当成错字改掉。
               # 代价实测过：「明晚七点到九点」被改写成「明天七点到九点」→ 钟点从 19:00 变 07:00；
               # 「半夜12点」被改写成「半夜里2点」→ 14:00。**静默排错时间**比排不进更坑。
               "明晚", "后晚", "昨晚", "凌晨", "半夜", "午夜", "清晨", "早晨", "一早",
               # 「傍晚」在 `_PART_WORDS` 的下午组里登着（`_part_said` 认它），但这张表漏了
               # 它 → 擦标题时它不掉，卡片名成了「傍晚踢球」（2026-10-11 探针）。
               # 两张表各写一份、一份登记一份没登记，就是这种后果。
               "傍晚")


# 长得像时间词、其实是**正经事名**的两字词 —— 绝不能被当成笔误纠掉。
# 只收"同首字 + 只差一字"的那一类（=这一层唯一会误伤到的形状）：
# 「上班」↔「上午」、「上课」↔「上午」、「上旬」↔「上午」、「早点」↔「早上」…
_NOT_TIME_WORDS = ("上班", "下班", "上学", "上课", "上铺", "上旬", "下旬",
                   "上边", "下边", "上周", "下周", "早点", "早些", "晚会")

# 「时间语境」：时间词后面**紧跟着**的东西。只有处在这种语境里，才敢把形近词
# 当成笔误去纠 —— 因为这时候它**确实是在当时间用**（「中文12点」里的"中文"就是在说几点）。
# 反过来，「加个上班的提醒」里的"上班"后面没有钟点，就一个字都不许动。
# 收窄到这一步是被逼的：光靠"同首字 + 差一字"会误伤一堆正经词（见 _NOT_TIME_WORDS）。
_CLOCK_AFTER_RE = re.compile(
    r"的?\s*(?:[零一二三四五六七八九十两\d]{1,3}\s*(?:点|:|时)|周[一二三四五六日天])")


def _fix_typo_time_words(text: str) -> str:
    """把**时间词的形近 / 同音笔误**纠正过来（人话：「中文」其实是「中午」）。

    学生是手打的，笔画相近的字很容易选错：
    `中午/中文`、`上午/上物`、`下午/下物`、`明天/明夭`、`周末/周未`。

    不纠正的代价不只是"没听懂"——**「中文」不是时间词，擦不掉**，于是整串留在
    标题里，卡片上写着「中文的车」这种谁也没说过的事名（学生报障那一幕，
    2026-09-30）。它比"没听懂"更糟：系统**自信地**把一句谁也没说的话写进日程。

    三道闸门，一道比一道严（每一道都是实测撞出来的）：
      ① **先认正经时间词**：这个位置上本来就是时间词的，原样收下，绝不纠正。
         不拦的话会**连环改**——今天 → 今晚 → 今夜 互相都只差一个字，一路改下去
         （实测「今田晚上有课」被改成「今夜里上午课」，整句话烂掉）。
      ② **别碰长得像时间词的正经词**：见 `_NOT_TIME_WORDS`。
         实测「上班」和「上午」只差一个字，不拦就会被纠成「上午」然后擦掉，
         学生的「加个上班打卡」变成「打卡」—— 悄悄少了一个词，比报错还难发现。
         周一到周天同理（和「周末」只差一个字），单独用 `_WEEKDAY_RE` 拦。
      ③ **必须处在时间语境里**：后面紧跟钟点或星期才纠（见 `_CLOCK_AFTER_RE`）。
         「中文12点」→ 纠；「加个上班的提醒」→ 后面没钟点，一个字不动。

    ⚠️ 它只纠正**写法**，纠正完照原样往下走（该擦的照样擦）。
       这样才能保证"写成错别字的句子"和"写对了的句子"最终走同一条路 ——
       行为一致，不会因为一个字打错就换一种处理方式。
    ⚠️ 取向是**宁可漏纠**：③ 收得紧，没钟点的笔误（「明夭交电费」）这里不纠，
       留给语义层（有 key 时它会纠正，实测「会义」→「开会」）。这条兜底只为
       "没配 key 的离线演示也别把错字写进日程"。
    """
    t = text or ""
    out = []
    i = 0
    while i < len(t):
        # ① 位置上本来就是时间词 → 原样收下（防连环改）
        hit = next((w for w in _TIME_WORDS if t.startswith(w, i)), None)
        if hit:
            out.append(hit)
            i += len(hit)
            continue
        # ② 长得像时间词的正经词 / 星期几 → 原样收下
        if (any(t.startswith(w, i) for w in _NOT_TIME_WORDS)
                or _WEEKDAY_RE.match(t, i)):
            out.append(t[i])
            i += 1
            continue
        # ④ 前一个字是「点」或数字 → 这个位置上是**钟点自己的尾巴**，不许当笔误改写。
        #    开这一道的人是我自己：2026-10-11 往 `_TIME_WORDS` 补「半夜」之后，
        #    「晚上8点半到9点」里的「半到」被认成「半夜」的笔误（同首字 + 差一字 +
        #    后面跟钟点，三道闸门全过），整句成了「晚上8点半夜9点」——
        #    **句尾那个钟点直接读不出来了**，学生说的一小时掉回默认时长。
        #    「点/数字后面跟着的不是时间词、是钟点的一部分」这条以前没人写，因为
        #    以前表里没有"半"开头的档词。宁可漏纠（取向见上面）。
        if i > 0 and (t[i - 1] == "点" or t[i - 1].isdigit()):
            out.append(t[i])
            i += 1
            continue
        # ③ 处在时间语境里、又只差一个字 → 认作那个时间词并纠正
        fixed = None
        for w in _TIME_WORDS:
            win = t[i:i + len(w)]
            if (len(win) == len(w) and win != w and win[:1] == w[:1]
                    and _lev(win, w) == 1 and _CLOCK_AFTER_RE.match(t, i + len(w))):
                fixed = w
                break
        if fixed:
            out.append(fixed)
            i += len(fixed)
            continue
        out.append(t[i])
        i += 1
    return "".join(out)


def _strip_title_noise(text: str, keep_freq: bool = False) -> str:
    """把一句人话里**不属于事情名字**的东西全擦掉（时间、日期、语气词、动作词…）。

    从 `_pick_title` 里抽出来的，为的是能**一句一句地**擦（见那边的"切段重试"）。
    它只负责擦，**不管擦完像不像一件事**——判真是 `looks_like_thing` 的活。
    """
    # `_normalize_clock` 现在顺带纠时间词的形近笔误（「中文」→「中午」）——
    # 不纠的话「中文」不是时间词、擦不掉，会整串留在标题里（「中文的车」）。
    t = _normalize_clock(text or "")   # 先把「两点到三点」换成 2:00-3:00，下面的正则才擦得掉
    # 时长整块擦掉（"大概一个小时"）：它既是判真的依据，也不该粘进名字里。
    # 必须在抠日期/钟点之前擦——"一个小时"里的"一"会被后面的中文数字逻辑当成别的。
    t = _DURATION_RE.sub(" ", t)
    # 频次：只擦「三次」这个数量和「一周」这个范围词，**中间的动词留着**。
    # 必须在抠日期/钟点之前擦——"三次"里的"三"会被后面的中文数字逻辑当成别的。
    if _FREQ_RE.search(t) and not keep_freq:
        t = _COUNT_UNIT_RE.sub(" ", t)
        t = _WEEK_SCOPE_RE.sub(" ", t)
    # 「从今天开始 / 从明天起 / 从现在开始」这串起手式先整块擦掉，
    # 再擦周期说法 —— 顺序反了不行：先擦「每天」的话，「从今天开始每天背单词」
    # 剩「从背单词」，那个「从」就再也擦不掉了（2026-10-13 用户原话实测）。
    t = _START_FORM_RE.sub(" ", t)
    # 每周重复的整串说法一起擦掉。漏了它，「每个周天都加一个写作业的代办」的标题就会
    # 剩成「每个都写作业」（2026-10-10 用户截图里原样出现，很难看）——
    # 重复这件事现在由 repeat/weekday 字段承载，标题里没有它的位置。
    t = _REPEAT_PHRASE_RE.sub(" ", t)
    t = re.sub(r"(大概|大约|差不多|约|预计|估计|有个|差不多要)(?=\s|$)", " ", t)
    # 「改成周六」「换到晚上七点」这类话里，"改成/换到"是**动作**不是事情的名字。
    # 不擦的话，学生在候选卡下面回一句「改成周六」，标题就会变成「改成健身」。
    t = re.sub(r"(改成|改到|换成|换到|换个时间|换个点|挪到|调整到|调到|重排|改一下|重来)", " ", t)
    # 「开始」是**时间框架词**，不是事情的一部分：「三点开始值日」「晚上七点开始写作业」
    # 「周三上午八点到九点开始班会」擦完分别该是「值日」「写作业」「班会」。
    # ⛔ 别塞进 `_TITLE_NOISE`——那张表整串否决，结果不是干净名字而是"待办"，
    #    系统会反问「要安排的是什么事」，而学生明明第一句就说了。
    #    「开幕式/开端」这类不含「开始」，不受影响。
    t = t.replace("开始", " ")
    # 「第N节课」整块是时间（"在原本第一节课的位置加入健身"里的"第一节课的位置"）——
    # 不擦掉的话标题会变成「原本第一节课的位置健身」，卡片上就是这一长串。
    t = re.sub(r"第\s*[一二三四五六七八1-8]\s*节(课)?(的)?(位置|时候|时间|时段)?", " ", t)
    # 「在…的位置/地方」也是时间状语，整块擦掉
    t = re.sub(r"在[^\s，,。]{0,12}?(的)?(位置|地方|时段)", " ", t)
    # 「合适/合适的时间」是形容词不是事名——「你帮我找一个合适的时间」擦完会剩
    # 「找合适」这种动词残渣，还恰好能过判真。擦掉它，「找工作」这类真名不受影响。
    t = t.replace("合适", " ")
    # 「这个点/那个点/这点/那点」是指代时间的赘词（「这个点安排跑步咋样」→ 名字该是「跑步」）。
    # 不擦的话「咋样/怎么样」擦完只剩「点跑步」，那个"点"是从「这个点」掉下来的半截。
    t = re.sub(r"(?:这|那)(?:个|些)?点", " ", t)
    # 疑问**尾词**走擦除、不走否决：「今晚去健身怎么样」这句确实是下单（要加"健身"），
    # 只是名字后面拖了个「怎么样」——整串否决会变成"待办"，系统反问「要安排的是什么事」，
    # 而他明明说了。（对照 `_vague_ask_name`：那是整串都在问"做什么"，没有事情剩下，才否决。）
    t = re.sub(r"(?:怎么样|咋样|怎样|好不好|行不行|如何|算了)[呢啊呀哦吧]?(?=$|[，,。！!\s])", " ", t)
    t = _DATE_RE.sub(" ", t)
    # 单写日期的那两种形式也要擦掉，否则「删掉 2026-09-29 那条」的标题会变成一串日期
    t = _DATE_ONLY_RE.sub(" ", t)
    t = _CN_MD_RE.sub(" ", t)
    t = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}整?(?:\s*(?:到|至|-|~|～)\s*\d{1,2}\s*[:：]?\s*\d{2}整?)?", " ", t)
    t = re.sub(r"\d{1,2}\s*点到\s*\d{1,2}\s*点?", " ", t)
    # 中英混写的时间（"下午四点到晚上7点" 归一后 = "下午4:00到晚上7点"）右头只剩一个
    # 孤零零的"N点"，上面两条都够不着，残留下来标题就成了「到7点」。把这种"数字+点"
    # 整块擦掉。（要求前面有阿拉伯数字，"早点/重点/地点/三点水"这类词不受影响。）
    # ⚠️ 这里只补「整」不补「半」，是因为**变异打出来的**：把「半」也加进来之后，
    #    删掉它测试一条都不红 —— 阿拉伯写法的「6点半」在上面 `_normalize_clock`
    #    就已经归成 `6:30` 了，这条路根本走不到（"测的必须是走到的"）。
    #    「12点整」那一条才是这里真正在吃的（中文写法由归一处理，见 `_TIME_WORDS` 那条注释）。
    t = re.sub(r"\d{1,2}\s*点(\s*\d{1,2}\s*分|整)?", " ", t)
    for w in _TIME_WORDS + (
              # 「平时 / 工作日」跟「周末」是一类：说的是**哪天方便**，不是要做什么事。
              # 实测「我要平时去吃火锅，76分钟」卡片上写的是「平时去吃火锅」——
              # 学生要办的事是「吃火锅」，"平时"是排期条件，不该印在待办名上。
              "平时", "工作日"):
        t = t.replace(w, " ")
    # 类别词在**中间**也要擦（2026-10-11 真机两张截图）：「周六加个代办去图书馆」
    # 以前只擦结尾那颗（下面那条 `(待办|代办|…)$`），中间这颗留着 → `_TITLE_NOISE`
    # 拿 `any(w in t)` 一判，**整串**不算事名 → 系统反问「要安排的是什么事」，
    # 而「去图书馆」白纸黑字就在他那句话里。擦掉之后是「去图书馆」→「图书馆」。
    # 「提醒」同族（整族扫描查出来的：「周六加个提醒去拿快递」以前是「提醒去拿快递」）——
    # 它和"待办"一样是**容器词**，学生真正要办的事在后面那截。
    # 代价照实记：「取件提醒」短成「取件」、「提醒妈妈生日」短成「妈妈生日」，
    # 名字变短但没改意思，比挂出「提醒去拿快递」这种整句强。
    # ⚠️ 否决表照旧留着这几个词，它管的是另一种形状："擦完什么都不剩"
    #    （「加个待办」→ 空 → 该问）。两种形状别混：一种有真事名，一种没有。
    # ⚠️ `(?!表)` 是护住「日程表/时间表」这类**成词的名词**：不护的话
    #    「整理日程表」会被擦成「整理表」—— 卡片挂出一个改坏了的名字，
    #    比老老实实反问一句"要安排什么事"更伤（「值日写作业」就是这么被投诉的）。
    # ── 提醒框词（2026-10-11 探针实测：这类词不剥，标题就是垃圾）──
    # 学生说「记得 / 别忘了 / 提醒我 / 想起来 + 事件」时，真正的待办是**后面那截**，
    # 框词本身不是事名。旧代码把：
    #   「记得提醒我周五交作业」→「得我交作业」（"记"被下面「加添记」首字修剪单独吃掉）
    #   「想起来要买点水果」→「想起来要买点水果」
    #   「别忘了周四还书」→「别忘了还书」
    #   「提醒我妈生日买礼物」→「我妈生日买礼物」
    # 这些全过判真、直接写进日程。整串删掉框词，留下真正的事件。
    # ⚠️ 必须放在下面「（待办|代办|事项|日程|提醒）」那道中间类别词擦拭**之前**——
    #    否则"提醒"先被那道单独擦成空格，把「设个提醒 / 提醒我」拆散，
    #    「设个提醒吃药」就只剩「设个吃药」、「提醒我」里的"我"也留下来。
    # ⚠️ 长词排前面（正则的备选是"先匹配到的算数"）：`千万别忘了` 得排在 `别忘了` 前头，
    #    否则「千万别忘了」靠"从位置 0 逐个试"侥幸能中 —— 同一文件里"那/那么"那条注释
    #    写的就是这个教训。
    # ⚠️ 「提醒」这一支要把**人称**一起吃掉、但**不许碰后面的称谓**：
    #    「提醒我妈生日买礼物」里该剥的是「提醒我」，留下「妈妈生日买礼物」。
    #    第一版写成 `提醒我(?!妈|爸…)` 是修过头 —— 前瞻一挡，整条不匹配，
    #    于是「我」被下面那道类别词擦拭单独留下，成了「我妈生日买礼物」（另一种残渣）。
    #    人称按**长排在前面**（我们/你们… 要在 我/你 之前），没宾语的「设个提醒」照旧吃。
    t = re.sub(
        r"(千万别忘了|千万记得|忽然想起来|突然想起|想起来|"
        r"别忘了|别忘记|记着|记住|记得|"
        r"帮我提醒|给我提醒|提醒一下|设个提醒|弄个提醒|"
        r"提醒(?:我们|你们|他们|她们|大家|各位|自己|我|你|他|她)?)\s*",
        " ", t)
    # 剥完框词常露头一个"要"（「想起来要交表」→「 要交表」），顺手清掉。
    # ⚠️ `^\s*` 那三个空格是必需的：上面擦框词留下的是**空格**，
    #    写成 `^(要)` 顶不上（第一版就是这么漏的：「想起来要买点水果」仍剩「要买点水果」）。
    t = re.sub(r"^\s*要\s*", " ", t)
    t = re.sub(r"(待办|代办|事项|日程|提醒)(?!表)", " ", t)
    # 星期连着时间修饰一起擦（"下周二/这周四"整块都是时间，不是标题的一部分）。
    # 注意顺序：必须整块匹配——先削"下周"再削"周二"，会剩下一个"二"粘进标题里。
    t = re.sub(r"(下|本|这|上)?(?:周|星期|礼拜)[" + _WD_CHARS + "](?![点时:：])", " ", t)
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
    # 时间被擦掉后，连接词（"到/至"）会孤零零剩下（"下午四点到晚上7点"→只剩"到"）。
    # 它只是个连接词、不是事情名的一部分，收掉。只擦**独立成词**的到/至（前后是空白），
    # 所以"到家/到底/至少"里作为词素的字不会被误伤。
    t = re.sub(r"(?:^|\s)[到至](?=\s|$)", " ", t)
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
    # 「记一条 / 加两条」这类：动词 + 数词 + 量词要作为一个整体吃掉（"记一条还信用卡"→「还信用卡」）。
    # 数词收「一/两/几/这/那/阿拉伯」；量词收「个/条/件/次/项」（"一下"不在内——
    # 它由上面 `_ADD_INTENT` 那一轮统一当意图词擦掉，这里不抢，免得把「记一下交作业」削成「下交作业」）。
    # ⚠️ 但**动词不能单独吃**：老写法 `[加添记]?(?:个|条…)?` 两个都是可选，于是光杆一个
    #    "加/记"开头也被削 —— 「加油打卡」实测被削成「油打卡」（第一版注释里说这条被
    #    `len>=2` 护栏保住了，其实保不住：护栏只在"削完只剩一个字"时生效，「油打卡」三个字照过）。
    #    现在要求**成对**才吃：动词必须跟着数词或量词；或者开头本来就是数词+量词/裸量词。
    #    「记账」「加油打卡」「打卡」这类正经名字此后再不动。
    _trimmed = re.sub(
        r"^(?:[加添记](?:\d|一|两|几|这|那)?(?:个|条|件|次|项)"
        r"|(?:\d|一|两|几|这|那)(?:个|条|件|次|项)"
        r"|[个条件次项])", "", t)
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
        # 尾巴上的"动作 + 量词"也要收：「周六去图书馆**加个**代办」——类别词「代办」在结尾
        # 被擦掉之后，露出的不是名字本身而是「图书馆加个」（整族扫描查出来的，见快照）。
        # 只擦**结尾**、且必须是"加/添/记/安排/弄/搞 + 量词"连在一起，
        # 所以「查件」「做个手工」这类不会被动。
        t = re.sub(r"(?:加|添|记|安排|弄|搞)(?:一个|个|条|件|项|一下)$", "", t)
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
    # 频次擦完什么都不剩了（「一星期跑两回」→ 只剩一个"跑"字，判真嫌短）
    # → 退回**不擦频次**再抠一遍。名字丑（「星期跑两回」）也比没卡强：
    # 学生明明说了要干什么，这时候反问一句"要安排的是什么事"最惹恼他。
    if _FREQ_RE.search(t):
        cand = _strip_title_noise(t, keep_freq=True)
        if _title_ok(cand):
            return cand
    return "待办"


# 学生报"几次"时，把时间词/时长都擦干净之后剩下的那一点**计数残渣**（#73）。
# 实测：「下周三次，每次一小时」擦完是「每次」、「一次一小时」擦完是「一次」——
# 这两个都不是事情的名字，可 `looks_like_thing` 照样放行，于是候选卡上写着「每次」。
# 这道门开在 `_title_ok` 上而不是开在某个调用点上，理由有两条：
#   1. `_pick_title` 的整句档和切段档都过这一道门，一处改两档都生效；
#   2. 拦掉之后 `_pick_title` 返回 "待办"，而 `parse_slot_text` / `span_only_proposal`
#      本来就有"名字是待办就退回调用方递进来的那个"的兜底 —— 卡上的真名字自然落回来，
#      不用新写一条兜底，`main.py:3235`、`main.py:6339` 两处调用点一起好。
# ⚠️ 必须整串匹配（等价 `fullmatch`），不许 `search`：`search` 会把「二次元」「第一次」
#    「下次」这些真名字一起判死（实测：只有整串匹配这一种写法放行它们）。
#    形如「(计数词)*计数单元」 repeated —— 「次每次」「三次每次」这种**多个单元粘在一起**的
#    残渣也得拦掉（实测它们是 `_pick_title` 在 档③ 那句上吐出来的名字），所以用 `+` 而不是只匹配一段。
_COUNT_RESIDUE_RE = re.compile(r"(?:[每共一两二三四五六七八九十百半]*[次回遍趟])+")


def _title_ok(t: str) -> bool:
    """擦完的这串能不能当待办名用（没内容 / 只剩类别词 / 只剩计数残渣 / 只是心情感叹 → 不行）。"""
    return (bool(t) and t not in _EMPTY_TITLES
            and not _COUNT_RESIDUE_RE.fullmatch(t)
            and not _looks_like_correction(t)
            and not looks_like_chitchat(t) and looks_like_thing(t))


# 学生在**纠正上一句**、不是在下单。2026-10-10 用户截图里的原话：
#   「你没理解我的画，每一个周天，所有周天」
# 老代码把这句擦成「没理解我的画每所有」并**判真通过**，于是卡片标题成了
# 「没理解我的画每所有每个都写作业」—— 越纠正越离谱。
# 这里不试图"听懂纠错"（那是上下文修复，另一个量级的活），只做一件事：
# **拒绝把吐槽写成待办名**。宁可回一句问清楚，也不给他造一条看不懂的日程。
_CORRECTION_RE = re.compile(
    r"(没(有)?(理解|听懂|明白|看懂|看清)|我(说|讲)的(是|意思)|你(听|说|理解|搞|弄)(错|歪)"
    r"|答非所问|不(是|对)这(样|个|件)|重新(来|说|讲|排|弄|改)"
    r"|每(?:个|一)?(?:周|星期|礼拜)|所有(?:周|星期|礼拜))")


def _looks_like_correction(t: str) -> bool:
    """这串读起来像"你没听懂我刚才那句"，而不像一件事的名字。"""
    return bool(_CORRECTION_RE.search(t or ""))


# ── 极小的"闲聊"黑名单（2026-09-30 补）──────────────────────────────────────
# 实测：学生说「**记一下今天很开心**」→ `_ADD_INTENT` 里的"记一下"命中，
# `_pick_title` 抠出「很开心」，`looks_like_thing('很开心')` 判真**通过** →
# 一张写着「要不要把 **很开心** 排进日程？」的卡挂到了他眼前。
# 他要的是吐槽，不是排事。
#
# 判据刻意收成"**主体只剩这些词**"（而不是"含这些字就否掉"）：
# 把心情/生理/天气感叹词、程度词、语气词、人称都摘掉，**一个字都剩不下**才算闲聊。
# 所以「买冷饮」摘掉"冷"还剩"买饮"（长度也够）→ 照旧判真，不会被误伤。
# ⚠️ 词表**宁小勿大**：只收几乎不可能单独构成"真事名"的词，拿不准的一律不加。
_CHITCHAT_WORDS = ("开心", "难过", "无聊", "难受", "郁闷", "崩溃", "疲惫", "疲倦",
                   "累", "饿", "困", "渴", "烦", "热", "冷", "爽", "emo")
_CHITCHAT_FILLER = ("很", "太", "真", "超", "好", "有点", "非常", "特别", "实在",
                    "简直", "死了", "啦", "呀", "吧", "啊", "呢", "嘛", "哦",
                    "嗯", "哈", "哎", "诶", "了", "的", "一下",
                    "我", "你", "他", "她", "它", "咱们", "自己", "肚子", "身上")


def looks_like_chitchat(name: str) -> bool:
    """这个标题主体是不是"心情/感叹"（人话：他不是要办一件事，是在吐槽）。

    只看**主体**：摘掉闲聊词与程度/语气词后还剩字符 → 不像闲聊，交给 `looks_like_thing`。
    剩不下 → 这串根本没说出一件事（「很开心」「好累」「肚子好饿」）。
    """
    s = name or ""
    for w in _CHITCHAT_WORDS:
        s = s.replace(w, "")
    return len(s.strip("".join(_CHITCHAT_FILLER))) == 0


def wants_add_todo(text: str) -> bool:
    """判断一句话算不算「往日程里加一件事」（人话：确定性分支用的开关）。

    只认短促的下单句。管它路由落到哪个模块、模型想说什么，系统都按这条链路走。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:      # 太长的多半是在聊天，不是在下单
        return False
    # ★ 带"删"（含手误写法）或口语"不要了" → 这是在**删除**，不是下单 → 让给删待办。
    #   不让的话「删悼交电费」「上次那个事不要了」会被"口语下单"接住，
    #   跑去给学生**排空档**（实测：弹出的是"我看了下你这周的空档…"候选时段卡）——
    #   他要删，系统却帮他排时间，这一幕比"没听懂"更离谱。
    #   ⚠️ 必须放在所有"放行"判断**之前**：加待办分支（3c）排在删待办（3g）前面，
    #   不在这里先让出去，删待办那条路永远没机会接手。
    if _has_delete_intent(t):
        return False
    # ⚠️ 下面这几条"让路"必须排在"有意图词就放行"前面。
    # 「今晚安排点啥」这类**问该做什么**的句子不在这儿（2026-10-14 用户定案：
    # 是问事/闲聊，不入库）—— 它带"安排"这个 `_ADD_INTENT` 词，不在这里先让出去，
    # 「安排」就会把它拽进加待办分支（实测摊出的候选卡标题是「点啥」）。
    # 接手的是 main 里那条"给空档建议、不建卡"的确定性分支，见 `_asking_what_to_do`。
    if _asking_what_to_do(t):
        return False
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
            and not looks_like_question(t)
            and not _GO_QUESTION_RE.search(t)      # 问路/问办法的（"我要去哪里取快递"）不是下单
            and _title_ok(_name)):                 # "我要去X" 必须 X 是件真事才放行，纯疑问/愿望不放开
        return True
    # 没有意图词，但话里自带"日期/星期 + 起止时间"的也算下单——
    # （比如"确认 2026-09-24 19:00-20:30 背单词"，前端点候选卡回发的是这副模样；
    #   "今天19:00-20:30 复习线性代数"、"周五下午3点20写作业"这种不带安排字样的也该接住）。
    # 带疑问词的不算——那多半是在问课表，不是在下单。
    # 2026-10-07 补：说了"哪天 + **去/到/来** 做一件事"、但**还没说几点**
    # （"周六早上去图书馆""周四去还书""周六到图书馆"）也是明确的加待办意图，必须接住。
    # 否则 `wants_add_todo` 为 False、语义层又判成 add_todo 时，main.py 会改走
    # `to_canonical(message)` 这条路——它把"周六"这种日期从规范话里丢掉
    # （见 main.py 2322 行注释），后面 `candidate_slots` 认不出周六，就从"今天"往后扫几天，
    # 列出周三/周四…多天空档（实测：今天周三，学生说周六，却摊出一堆周三起的空档）。
    # 接住后走 `blob = message` 原话，候选只列周六。
    # ⚠️ 必须带"去/到/来"这类**动作动词**才放行：光有"日期 + 事名"会把问句
    # （"帮我看看周一第一节是什么课"）、管理指令（"标记完成 周日…"）也误判成下单——
    # 它们同样带日期和事名，但学生不是在"排一件事"，是在查、是在管。动作动词是
    # "人要去办"的硬信号，没有就宁可漏判、不可替学生办错事。
    if (_pick_date(t) and _title_ok(_pick_title(t))
            and not looks_like_question(t)
            and re.search(r"(去|到|来)", t)):
        return True
    # 「我一周要去三次健身房」这类**只报了频次**的句子：没有"加/安排"这类意图词，
    # 也没有具体日期钟点，以前整句漏给模型 —— 模型有时判成"问事"，回一句
    # 「好的，请问您希望每次健身的具体时间是什么时候呢？」（2026-10-09 实测），
    # 学生的原话当场不弹卡。而"一周 N 次"本身就是"请把这周排上 N 场"的硬信号。
    # ⚠️ 带疑问样的照旧不接：「一周三次健身够吗」是在问强度，不是在下单。
    # 也不接 N=1（"一周一次大扫除"更像随口说一句，交给原路去追问）。
    if ((weekly_times(t) or 0) >= 2 and _title_ok(_pick_title(t))
            and not any(k in t for k in ("吗", "？", "?", "多少", "够不", "是不是", "怎么样"))):
        return True
    # 「每周日都写作业」「以后每个周天晚上写作业」：点名了星期几 + 周期冠词，
    # 本身就是"以后每周都要办"的硬信号，可它既没有"加/安排"这类动作词、也没有具体日期，
    # 老规则一律不接（实测 2026-10-10：这两句 wants_add_todo 都是 False，
    # 学生只能看着系统回一句通用废话）。疑问样照样不接（「每周日写作业来得及吗」是问句）。
    if (weekly_repeat_day(t) and _title_ok(_pick_title(t))
            and not any(k in t for k in ("吗", "？", "?", "够不", "是不是", "怎么样", "来得及"))
            and not _done_already_said(t)):
        return True
    # 「我每天都要背单词」「天天跑步」：说了"每一天"就是长期下单的硬信号（2026-10-13 Prompt 02）。
    # 判据形状与上面 weekly 那条**并列**而不是套进来：它不点名星期几，`weekly_repeat_day` 返回 None。
    # ⚠️ 实测过（施工单说"摘掉 `_UNSUPPORTED_PERIOD` 那三项就彻底放开"是**错的**）：
    #   摘掉三项之后这几句 `wants_add_todo` 仍然是 False —— 拦它的不是否决表，是这里没有对应分支。
    #   两句都要改，只改否决表会得到"不拦了，但也没接"：卡不出来，学生只看到一句通用废话。
    # 疑问/感叹样照样不接（「每天都这么累有什么用」「每天几点起最好」不是下单）。
    # ⚠️ 还要挡**否定开头**：「你每天都不回我消息」是吐槽，不是"每天做一件叫"不回我消息"的事"。
    #   实测第一版就是被这句接走的（擦完标题剩「不回我消息」，`_title_ok` 还判它合法）。
    if (daily_repeat(t) and _title_ok(_pick_title(t))
            and not _pick_title(t).startswith(("不", "没", "别", "无"))
            and not any(k in t for k in ("吗", "？", "?", "多少", "够不", "是不是", "怎么样",
                                         "来得及", "有什么用", "为什么", "怎么办"))
            and not _done_already_said(t)):
        return True
    return bool(_pick_date(t) and _pick_span(t)[0]
                and not looks_like_question(t))


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
    if is_past_date(date):
        # 学生点了个**已经过去的日子**（「3月5号交的那个作业补一下」）→ 不出一张
        # 他永远看不见的卡。返回 None 交给上层如实说"那天已经过了"（用户 2026-10-11：
        # 「代办不能加到过去的日期」）。四条出卡路都查这同一道闸，判据在 `store.is_past_date`。
        return None
    # ⚠️ 裸钟点（只说"三点"、没报上午下午）**不出这张单条卡**：卡上写的是"就排在这个点"，
    # 而 `_pick_span('周日三点')` 给的是 03:00 —— 2026-10-11 真机就是这么漏的：
    # 「周日三点开始写作业」直接成了一张 03:00-04:00 的凌晨卡。
    # 判据跟 `_day_slots` 的 pinned、`span_only_proposal`、`retime_todo_proposal` **同一条**
    # （`clock_ok`），四条路不会一处认、一处不认。
    # 返回 None 不等于"没听懂这句"：调用方会转去摊候选时段，
    # 那一路的 hint 里会明说"你说的 3 点没讲上下午，我不敢替你定"。
    if not parse_time_spec(t).get("clock_ok"):
        return None
    if not end:                    # 只给了开始时间，默认排 1 小时
        end = to_hhmm(to_minutes(start) + 60)
    title = _pick_title(t)
    # 2026-09-30 补：时间碎片（"12" / "12点"）不能当待办名挂卡，否则
    # 「明天中午12」会出一张标题叫"12"的确认卡。判不过就当作"没说清"。
    if not _title_ok(title):
        return None
    return {
        "kind": "todo_add",
        "title": title,
        "date": date,
        "start": start,
        "end": end,
        "weekday": _weekday_name(date),
        "minutes": to_minutes(end) - to_minutes(start),
        # ★ 「每个周六上午九点爬山」这种**每周几 + 报了准点**的说法，repeat 必须一起带上：
        # 以前只有"摊候选时段"那一路带 repeat，报了准点就走这一路 → 学生说了"每个周六"，
        # 拿到的却是一条**一次性**待办（2026-10-11 一批真机句子打出来的，三句都一样）。
        # `store.add_todo` 在 weekday 不传时按 date 推，所以这里只补 repeat 就够。
        "repeat": repeat_of(t),
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


def auto_todo_proposal(text: str, minutes: int = 0) -> dict | None:
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
    if is_past_date(date):
        return None                      # 同 `parse_add_todo`：过去的日子不排待办
    title = _pick_title(t)
    if not title or title == "待办":
        return None
    # 时长四级：这一句的起止（上面 `_pick_span` 有值就不归它管）> 这一句说的时长
    # > 卡上记着的 `minutes` > `AUTO_SLOT_LENGTH`。以前只走最后一档，
    # 实测学生说「周四加个两小时的自习」排出来是 90 分钟 —— 他明说的 120 没人看。
    _want = wanted_minutes(t) or minutes or AUTO_SLOT_LENGTH
    # `minutes` 这一参是"这段至少要多长才作数"（不是学生要的时长）——
    # 学生要 30 分钟却不一起压低它，`pick_free_slot` 会拿 60 当下限把这段否掉、返回 None
    # （同文件 `plan_todo_slot` 早就这么处理了，两处口径要一致）。
    slot = pick_free_slot(date, prefer=t, minutes=min(_want, AUTO_SLOT_MINUTES),
                          length=_want)
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
        "repeat": repeat_of(t),
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
    # ★ 统一否决：模型直调这条路也必须过和 wants_add_todo 同一道关。
    #   否则「我想问一下交电费怎么操作」被模型直接 propose_todo_tool(title="交电费", date="周三")
    #   就会绕过 wants_add_todo 的 _GO_QUESTION_RE 守卫，照样出成待办确认卡。
    #   复用现有的 looks_like_question / _GO_QUESTION_RE（禁止另写一套）。
    _raw = " ".join(x for x in (str(title or ""), when, date, start, end) if x).strip()
    from app.agent.intent import looks_like_question
    if looks_like_question(_raw) or _GO_QUESTION_RE.search(_raw):
        return json.dumps({
            "error": "这像是在问事/问办法，不是要加待办",
            "hint": ("学生这句是在提问（问路、问流程、问时间），不该出待办确认条。"
                     "**走问答/知识库去答他**，别替他排日程；"
                     "真要加待办，等他把「要加哪件事」说清楚再说。"),
            "got": _raw,
        }, ensure_ascii=False)
    day = _pick_date(blob) or _pick_date(f"{date} {when}")
    # ★ 同一条不分层否决：模型直调也不许替学生把「每天/每月」挑成某一天一次
    _rej = period_reject(_raw)
    if _rej:
        return _rej
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
    # ★ 事名判真：title 抠不出一件真事（"待办"/残渣/类别词）就拒出卡，
    #   交追问分支去问他要加什么——绝不把半句话当代办名挂出去。
    if not _title_ok(name):
        return json.dumps({
            "error": "还不知道要安排什么事",
            "hint": ("title 没说清要加哪件事（学生可能只说了时长、或者整句都是废话）→ "
                     "**问学生要安排什么事**，比如「说个名字就行，比如『游泳』『健身』」；"
                     "⛔ 不要问「你想几点」，也不要把那半句话当待办名挂出去。"),
            "got": _raw,
        }, ensure_ascii=False)
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
# ⚠️ 这一条**不在 `_normalize_clock` 下游**（它两个消费者：`_clean_prose_title` 洗模型回话里的
# 引号串、`has_concrete_span` 判模型有没有念出排好的时间 —— 都不过学生那句话那条路），
# 所以全角标点得在**这里**就认：冒号两种、分隔符四种。别在调用点再抄一张全角表。
_CONCRETE_SPAN_RE = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}\s*(?:到|至|[-~～－—–])\s*\d{1,2}\s*[:：]\s*\d{2}")


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


# 模型"把工具调用打成字"的形状：工具名 + 紧跟的一段 JSON。
# ⚠️ 只列**能直接变成卡片**的那三件 + 两件只读的（撤文本不代执行）：
#    诊断页/日志里出现工具名是正常的，别一看到名字就把回话换掉。
_TOOL_ECHO_RE = re.compile(
    r"(propose_todo_mode_tool|propose_todo_slots_tool|propose_todo_tool"
    r"|propose_todo_remove|propose_todo_done|propose_slots"
    # ★ #86：新工具也进来。只加这一处还不够 —— 下面 `textual_tool_card` 那张按名字派发的表
    #   也得认它，否则"模型把调用打成字"这一族对新工具**静默不执行**（文本撤掉了、事没办）。
    r"|propose_todo_change)"
    r"\s*[:：]?\s*(\{.*?\})", re.S)


def looks_like_tool_echo(text: str) -> bool:
    """这段回话是不是"模型把工具调用当成文本打出来了"。

    2026-10-11 真机实测：学生问「三点开始大约三个小时」，管家回的不是卡片，而是原样一段——
        propose_todo_tool
        {"title": "写作业", "when": "2024-01-05 15:00~18:00"}
    这是**两件事**：卡没出来（学生白等），而且把内部机制端到了学生面前
    （红线 8「学生面前不出现技术内脏」里最直白的一种）。
    判据要保守：必须是"工具名 + 花括号里的一段 JSON"，光提到工具名不算
    （管理员的诊断页、日志里都可能出现工具名）。
    """
    return bool(_TOOL_ECHO_RE.search(text or ""))


def textual_tool_card(text: str) -> dict | None:
    """模型没"调"工具、而是把调用**打成字**了 → 系统替它执行这一次调用。

    走的是**同一件函数**：参数校验、空档检查、周期否决（`period_reject`）一道都不少，
    所以"文本调用"和"真调用"结果一致 —— 不因为模型换了种表达方式就少查一遍。
    认不出形状、或那件函数根本不产卡片（如 `propose_slots` 只渲染已有候选），返回 None，
    由调用方**至少把那段文本撤下来**（`looks_like_tool_echo` 那条红线）。
    """
    m = _TOOL_ECHO_RE.search(text or "")
    if not m:
        return None
    name, payload = m.group(1), m.group(2)
    fn = {"propose_todo_tool": propose_todo_tool,
          "propose_todo_mode_tool": propose_todo_mode_tool,
          "propose_todo_slots_tool": propose_todo_slots_tool,
          # ★ #86：回显派发与上面那份正则**必须同时改**（只改正则=撤掉文本、不执行；只改这里=认不出形状）
          "propose_todo_change": propose_todo_change_tool}.get(name)
    if fn is None:
        return None
    try:
        args = json.loads(payload)
    except Exception:
        return None
    if not isinstance(args, dict):
        return None
    try:
        raw = fn(**args)
        prop = (json.loads(raw) or {}).get("__proposal__")
    except Exception:
        return None
    return prop if isinstance(prop, dict) else None


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
    if looks_like_question(t):
        return False
    return _pick_span(t)[0] is not None or _pick_date(t) is not None


# 学生嫌系统挑的点不合适、要求换时间的说法（人话："改成七点""挪到周六"）
_RETIME_WORDS = ("改成", "改到", "换成", "换到", "换个时间", "换个点", "挪到", "改改",
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
    时长同理：这一句没报长度就沿用原卡那条的，不再一律按 90 分钟重挑（#73）。

    认不出来的返回 None，交给别的分支处理——绝不硬猜。
    """
    if not isinstance(card, dict) or card.get("kind") != "todo_add":
        return None
    t = (text or "").strip()
    if not t:
        return None
    begin, finish = _pick_span(t)
    # 裸钟点不猜（判据与 `_day_slots` 的 pinned、`span_only_proposal` 同一条）：
    # 「改成三点」换出来的是 03:00 的凌晨条，而学生说的大多是下午三点。
    # 返回 None 不算失败 —— 调用方会照旧走"没认出来"，顶多让他补一句上下午。
    if begin and not parse_time_spec(t).get("clock_ok"):
        return None
    new_date = _pick_date(t)
    old_date = card.get("date") or ""
    date = new_date or old_date
    if not date:
        return None
    if is_past_date(date):
        return None                      # 同 `parse_add_todo`：不许把待办挪到过去的日子上
    title = card.get("title") or "待办"
    wd = _weekday_name(date)
    if begin:
        if not finish:
            finish = to_hhmm(to_minutes(begin)
                             + (wanted_minutes(t) or card.get("minutes") or 60))
    elif new_date and new_date != old_date:
        # 只换日子、没说几点 → 在那个新日子上重新挑一段空档。
        # 长度**沿用卡上那一条的**（#73）：以前这里不传 length，落到 `AUTO_SLOT_LENGTH`=90，
        # 实测卡上 19:00-20:00（60 分钟）、学生只说「改成周六」，出来 08:00-09:30/90 ——
        # 他改的是日子，长度被系统顺手改长了 30 分钟。
        _want = wanted_minutes(t) or card.get("minutes") or AUTO_SLOT_LENGTH
        slot = pick_free_slot(date, prefer=t,
                              minutes=min(_want, AUTO_SLOT_MINUTES), length=_want)
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
        # ⚠️ 换时间**不许顺手把"每周"换没**：上一张卡是 weekly 的（「每个周天写作业」），
        # 学生只说"改成上午十点"，落库就该还是每周重复（2026-10-11 真机：改成 10:00 之后
        # 卡片上的 repeat 掉成 ""，学生以为改好了，下周那天却不再出现）。
        "repeat": (card.get("repeat") or REPEAT_NONE),
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

# ★「删」字的**容错**写法：学生手快打成「删悼」「删叼」「删去」都是同一个意思。
#   早先只认上面那串固定词，打错一个字就整句掉出确定性分支——
#   结果「删悼交电费」被当成"要加个什么事"，跑去排空档（实测：弹出候选时段卡）。
#   「删」这个字在中文里几乎只用于删除，所以单字命中 + 后面最多跟两个字，风险很低。
# ⚠️ 只用来**补**上面那张表，不替换它：固定词更准，容错是兜底。
# ⚠️ 后面**最多跟 1 个字**，不能 2 个：「删除撞时间的代办」里，跟 2 个字会把
#   「删除撞」整块擦掉，剩下的「时间」被当成待办名 —— 名字一旦非空，
#   "撞时间"这个筛条件就走不到了，于是报"没找到"（这一版真踩过）。
_DEL_ANY_RE = re.compile(r"删[^，。！!？?、\s]{0,1}")

# ★ 口语里的"不要了"：学生说「上次那个事不要了」「那个不去了」就是**要删**，
#   可是这几个词不在"删除"词表里（字面上也确实没有删字）→ 整句掉给加待办那条路。
#   ⚠️ 只放**宽松那档**（wants_remove_todo_loose）："我不要加待办了"这种
#   是在**撤掉刚才那张加卡**，由确认/取消那条路先接，不能让删除抢走。
# ★ 2026-10-12 补「不吃了」「不做了」：学生反悔的是**刚提的那件事**，说的是
#   「算了不吃了」（前面刚提「吃自助餐」）—— 这一句三档都没人接，掉给模型当闲聊。
#   只补这两颗**整条反悔话**，不补「算了不吃了 / 算了不去了 / 算了不做了」那三颗：
#   它们是这两颗的**超串**，写进表里永远轮不到自己被命中（死条目，见红线 13c 的教训：
#   词表里每一条都得有"只有它能接住"的句子，否则测不到、也删不掉）。
#   「算了」**单独那颗仍然不收** —— 它更常在说"算了不说这个了"（闲聊收尾），
#   而这张表加一颗词的代价不只是多认删除：`wants_add_todo` 开头就拿它让路
#   （含软词 → 直接判"不是新增"），所以「这周不吃了，帮我加个体检复查」这类
#   **两件事一句说**的句子会被挡掉新增那件。这是这张表本来就有的取舍（「不去了」同形），
#   测试里照实钉住，别让它悄悄变大。
_REMOVE_TODO_SOFT = ("不要了", "不去了", "不搞了", "算了删", "取消掉", "去掉吧", "删了吧",
                     "不吃了", "不做了")

# 标记完成（把某条**已存在的待办**标成已完成）的触发词。
# 注意：**不放**"完成"这种极泛的词当总开关——"帮我安排完成作业"是新增，不是标记完成，
# 碰上就会被误判。只有显式的"标记/勾/划"指令，或"完成/做完/搞定"精准对上一条已有待办时才算。
_DONE_TODO_INTENT = (
    "标记为完成", "标记完成", "标为完成", "标成完成", "标记一下完成", "标记已",
    "勾掉", "勾了", "划掉", "划了", "打勾", "打卡", "标记done", "标done",
    "标记为已", "完成掉",
)
# 软动词：仅当精准对上一条已有待办名字时才算标记完成（避免跟"新增"意图撞车）
# ⚠️ 「完了」是 2026-10-12 补的：以前收了"做完/完成了/办完/搞定"，**偏偏漏了光杆的「完了」**，
#    而学生最自然的说法就是它 —— 实测「今天健身完了」「我健身完了」「跑完了」「作业写完了」
#    四条全都不认（库里明明有《健身》《跑步》《写作业》），于是一路掉给模型，
#    模型把它当**问事**答了一句，学生那句"我做完了"就这么蒸发了。
#    收进来仍然要求"精准对上一条已有待办的名字"，所以「忙完了」「说完了」不会误触发。
_DONE_TODO_VERB = ("完成了", "做完", "搞定了", "搞定", "办完", "做掉了", "划掉", "勾掉", "完了")
# 「完了」单独一档：它太泛 —— 「健身累完了」「忙完了」是在**说状态**，不是宣布做完某事。
# 加了它之后实测「健身累完了」被判成标记完成（把吐槽当成办好了的动作），
# 所以这一颗要求**紧贴事名**（《健身》+「完了」）才算，其余动词沿用原有的含糊匹配。
_DONE_TODO_VERB_TIGHT = ("完了",)

# ── 动结式（结果补语）：整词表够不着的那一整族口语 ────────────────────────
# 上面那张表收的都是**整词**（做完/办完/搞定），可学生宣布办完时最常说的是
# 「动词 + 完/好」这个**结构**，它一个整词都不带：
#   「去完图书馆了」「图书馆看完了」「健完身了」「作业写完了」「从图书馆回来了」
# 2026-10-12 学生实测：这一族全都不认，于是掉给模型 —— 模型当**问事**回一句，
# 或者被样本库听成"新增"，弹出一张标题写着「完图书馆」的卡。他那句"我办完了"就这么蒸发。
_DONE_CPL = r"(?:完|好)"
# 能挂结果补语的是**动作动词**；「累/烦/忙/饿/气」这类状态词故意不进册 ——
# 这正是「健身做完了」（宣布办完）跟「健身累完了」（在说累）的分界线。
# 少收一个动词的最坏结果是**退回原样**（这句还是掉给模型），不会替他改状态，所以这张表宁缺毋滥。
_DONE_ACT_V = "去看听写做办买取拿送交还借背读练习跑跳游吃喝上下洗刷扫擦打搞弄干骑走学教考查修" \
              "张贴包煮炒晾晒拍编排绘约签逛试穿叠清消接转汇报答复预备值缝剪烫拖抬搬拆装卸寄印补"
# 已然标记：句末的"了/啦/咯"，或明说"已经/刚才/昨天"。
_DONE_EVIDENCE_WORDS = ("已经", "已", "刚", "刚刚", "刚才", "方才", "昨晚", "昨天", "前天")
# 反面的**计划**标记：话里带着这些，那个「V完」就还没发生（「明天作业写完了就能玩」）。
# 不收「回头」——它同时是"回头看一眼"的回头，会误伤正经的完成声明。
# 跟上面两份判据是同一个方向的活（红线 11 那个形状：「是不是过去/未来」将来该由
# TimeSpec 一次算出一个字段，而不是各处自己认词）。现在先用词表顶着 —— 记在这儿，
# 别让它长成第三份。⚠️ 这一条上一版写的是"红线 24"，那是待办清单的编号、指南里没有，
# 已按红线 13c（注释里的引用也要实测过）改回来。
_DONE_FUTURE_WORDS = ("明天", "明儿", "后天", "大后天", "下周", "下个星期", "下个月",
                      "今晚", "今夜", "待会", "待会儿", "一会儿", "等会", "等下",
                      "改天", "到时候", "打算", "准备", "计划", "记得",
                      "别忘", "应该", "必须", "争取", "尽量")

# 学生明说这几个词，就是在说待办（而不是课）——哪怕话里带了星期几。
_TODO_WORDS = ("待办", "日程", "事项")


def looks_like_question(text: str) -> bool:
    """这句话是不是在**问事**（人话：带了"吗/？"就不是在宣布自己做完了什么）。

    收成一份的原因：同一个 `("吗","？","?")` 判断以前在文件里抄了三份
    （模式卡判定、加待办判定、问路放行），再加第四份就是"每处各写一遍、
    改一处漏三处"的老形状（红线 10/11）。
    """
    t = text or ""
    return any(k in t for k in ("吗", "？", "?"))


# 问的是**校园事实**（在哪 / 几点 / 怎么走 / 多少钱 / 能不能），不是"我日程里那条东西呢"。
# #41-B 用它做判据：知识库两路都查空时，这种句子该老实说"没查到"，不许掉进"查日程"那条兜底。
# 实测那一幕：问「外星人驻扎在哪个楼」（问答条目与上传文档都没有），
# `_MISSING_RE` 里的「在哪」先把它认成报障、抠出一个不存在的事名「外星人驻扎个楼」，
# 屏幕回「查了日程，确实还没有『外星人驻扎个楼』」—— 他问的是校园信息，系统在翻他的日程。
_CAMPUS_FACT_RE = re.compile(
    r"在哪(?:里)?(?!天|星期|周|月|年|日|号)|去哪里|去哪|哪儿|哪栋|哪个楼|几点|什么时候|何时|"
    r"多少钱|收费怎么|咋|怎么|怎样|能不能|可不可以")
# ⚠️ 报了"看不见"就不算在问校园事实：「我怎么没看到周一的健身」里也有「怎么」，
#    但那句说的是**某条待办不见了**，让路一让就把它甩给知识库冷处理，比原来那句更糟。
_REPORT_WORDS = ("看不见", "看不到", "没看见", "没有看见", "没看到", "没有看到",
                 "没显示", "没有显示", "显示不出来", "显示不出")


def asks_campus_fact(text: str) -> bool:
    """这句话是在问校园信息（地点/时间/办法/价格/规矩）吗？

    只做**形状**判断，不管知识库有没有答案 —— 那由调用方另判（`faq.kb_has_answer`）。
    句子太长（>40 字）就当它在说别的事：这类句子多半带着"我的/那条/周X"之类的上下文。
    """
    t = (text or "").strip()
    if not t or len(t) > 40:
        return False
    if any(w in t for w in _REPORT_WORDS):
        return False
    return bool(_CAMPUS_FACT_RE.search(t))

# "撞时间的/冲突的/重复的"这类词在**说筛选条件**（挑出时间上打架的那几条），
# 不是待办名。学生在日程里看到时间重叠的条目会被打上「撞」角标，就照这个词来点。
_COLLIDE_WORDS = ("撞时间", "撞车", "时间冲突", "冲突", "时间重叠", "重叠",
                  "重复", "同一时间", "时间一样", "一样的", "重了")

# 量词 / 修饰词：不是待办名的一部分。不擦掉，"两个撞时间的代办"会被当成一个
# 叫「两个撞时间」的待办名 → 匹配不上任何真待办 → 误报"没找到"（学生截图实测）。
_TODO_NAME_NOISE = (
    "两个", "俩", "两个都", "三个", "几个", "多条", "两条", "三条", "四条", "几条",
    "两件", "几件", "都", "全部", "所有", "代办",
)


def _wants_colliding(text: str) -> bool:
    """这句话是不是在按"时间撞车"筛选（人话："撞时间的待办都删掉"）。"""
    return any(w in (text or "") for w in _COLLIDE_WORDS)


# "都 / 全部 / 一起 / 剩下的" —— 学生对着刚列出的清单说这些词时，
# 指的就是「**你刚列的那几条，我全都要（或全不要）**」。
#
# 这是**指代**，不是字面上的"没说名字"。旧写法把「都删了」当"没点名"，
# 于是端出**整张日程**（学生截图：系统列了 2 条，他回「都删了」，
# 屏幕却冒出 11 条 —— "上下文连不上"说的就是这个）。
#
# ⚠️ 只收**整体量词**（"全都"）；**不收分配量词**（"每个/每组/各"）——
#    「每个删一个」是"每一组各删掉一个"（去重），不是"全部都删"。
#    混在一起就会把去重听成全删（学生截图："脑子有问题"就是这么来的）。
_ALL_WORDS = (
    "都", "全", "全部", "全都", "统统", "一起", "一块", "一并",
    "所有", "这些", "这几条", "这两条", "这俩",
    "剩下的", "其余的", "其他", "别的",
)

# 分配量词 + "删/留一个" → 「每一组各处理一个」（人话：「每个删一个」＝把重复的删掉一个）。
_ONE_EACH_RE = re.compile(
    r"(每|各)\s*[个样条组种位]?\s*(都\s*)?(删|去掉|去|清|取消|移除|留|保留|剩)\s*一\s*[个条项种位]?")

# 同义的"去重"说法（不含"每/各"也能听出意思）
_DEDUP_WORDS = ("去重", "去掉重复", "去重复", "删重复", "去掉多余的",
                "把重复的删", "重复的删", "重复的去掉", "只留一条", "只留一个",
                "留一条就行", "留一个就行")


def _wants_one_each(text: str) -> bool:
    """学生是不是在说「**每一组各处理一个**」（人话：「每个删一个」＝去重）。

    为什么单独拎出来：这句话里同时有"每"和"一"，字面上跟"全都要删"很像，
    意思却正相反——他要的是**每组只留一条**，不是把清单清空。
    """
    t = (text or "").strip()
    if not t or len(t) > 30:
        return False
    if _ONE_EACH_RE.search(t):
        return True
    return any(w in t for w in _DEDUP_WORDS)


def _one_per_group(items: list) -> list:
    """从候选里挑出**每组多出来的那条**（人话：「每个删一个」到底该删哪几条）。

    分组按"标题+日期+时段"——同一个 key 就是学生眼里的"一模一样的两条"。
    每组**留最早写入的那条**（先来的留下、后来重复的删掉），只把多出来的交回去。
    一组的唯一一条不会被算进来：他说的是"删**一个**"，不是"把这条删掉"。
    """
    seen, out = {}, []
    for it in (items or []):
        key = _equiv_key(it)
        if key in seen:
            out.append(it)          # 组里多出来的 → 删
        else:
            seen[key] = it          # 每组第一条 → 留
    return out


def _equiv_key(item: dict) -> tuple:
    """两条待办"在业务上等价"的判据（人话：删哪条学生看到的日程都一样）。

    标题 + 日期 + 起止 + 状态，四项全同才算等价。
    状态必须进 key：一条 planned、一条 done 的两条同名待办，删哪条**不一样**。
    """
    return (item.get("title", ""), item.get("date", ""),
            item.get("start", ""), item.get("end", ""), item.get("status", ""))


def _single_equiv_group(items: list) -> list | None:
    """整批候选是不是**同一组等价的**（人话：这几条删哪条都一样）。

    是 → 把这一组原样返回（调用方挑"先写进去的那条"）；
    不是（存在两种以上不同）→ None，意思是"真的分不清，别猜"。
    空集 / 只有一条，都返回 None：只有"≥2 条且全等"才算"选了也不会有差别"。
    """
    if not items or len(items) < 2:
        return None
    keys = {_equiv_key(x) for x in items}
    return list(items) if len(keys) == 1 else None


def _earliest(items: list) -> dict:
    """挑"先写进去的那条"（人话：等价的几条里，留下最早那条的规矩）。

    没有 created_at 的就按列表顺序（存储层本来就是按写入先后排的）。
    """
    for i, it in enumerate(items):
        if not it.get("created_at"):
            return items[0]
    return min(items, key=lambda x: (x.get("created_at") or "", ))


# 学生看到卡发现"不是这条"时的说法 —— 把他放回清单，而不是硬着头皮执行。
_RECHOOSE_WORDS = ("不对", "不是", "换一条", "换一个", "换那条", "重选", "重新选",
                   "再看看", "不是我", "错了", "搞错")


def _wants_rechoose(text: str) -> bool:
    """学生在说"不是这条/换一条"（人话：把清单再端出来让他重挑）。"""
    t = (text or "").strip()
    if not t or len(t) > 20:
        return False
    return any(w in t for w in _RECHOOSE_WORDS)


def _wants_all_candidates(text: str) -> bool:
    """学生在回答"是哪一条"时，是不是在说「都要 / 都不要」（人话：全选）。

    只在**上一句刚列过清单**时才会被问到（见 resolve_todo_remove_reply），
    所以"都"这种极常见的字在这里不会误伤——但它仍然**不抢名字**：
    若这句话能明确对上某一条真待办的名字，听名字的（见调用处）。
    """
    t = (text or "").strip()
    if not t or len(t) > 30:
        return False
    return any(w in t for w in _ALL_WORDS)


def _colliding_subset(todos: list) -> list:
    """从一组待办里挑出**跟同一天另一条时间重叠**的那些（人话：挑出"撞上的"）。

    判据：同一天、且时间段有交叠（含起止完全相同）——跟前端给条目打「撞」角标
    是同一套判据。只跟"另一条"比，所以孤零零一条永远不会被算成撞。
    """
    out = []
    for i, a in enumerate(todos):
        ad = a.get("date", "")
        as_, ae = a.get("start", ""), a.get("end", "")
        if not (as_ and ae):
            continue
        for j, b in enumerate(todos):
            if i == j or b.get("date", "") != ad:
                continue
            bs, be = b.get("start", ""), b.get("end", "")
            if not (bs and be):
                continue
            if to_minutes(as_) < to_minutes(be) and to_minutes(bs) < to_minutes(ae):
                out.append(a)
                break
    return out


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
                                        # （顺带纠时间词的形近笔误，见 _fix_typo_time_words）
    t = _DATE_RE.sub(" ", t)
    t = _DATE_ONLY_RE.sub(" ", t)       # 同上：单写的日期别粘进名字里
    t = _CN_MD_RE.sub(" ", t)
    t = re.sub(r"(下|本|这|上)?(?:周|星期|礼拜)[" + _WD_CHARS + "](?![点时:：])", " ", t)
    for w in _TIME_WORDS + ("下周", "本周", "这周",
                           "上周", "下个", "这个", "那个", "那天", "这天", "当天",
                           "那一天", "这一天", "当天的"):
        t = t.replace(w, " ")
    # ⚠️ 「一下 / 一下下」要在擦删除词**之前**擦掉：先擦删除词的话，「删+一」
    #    被整块吃掉，只剩一个「下」赖在名字里（"10月三号…删一下" → 名字变成"下"，
    #    而这句话里压根没有名字，全是修饰词）。
    for w in ("一下下", "一下子", "一下"):
        t = t.replace(w, " ")
    # ★ 手误的删除词（「删悼」「删叼」）要先整块擦掉：只擦单字"删"的话
    #   剩下的「悼」「叼」会粘进名字里（"悼交电费" → 对不上任何待办）。
    t = _DEL_ANY_RE.sub(" ", t)
    # ★ 纠正句里的"否定词"也不是名字的一部分（"不是这个，是交电费" → 交电费）。
    #   早先这些字赖在名字里，剩下「不是是交电费」，匹配不上 → 只能原样重列清单，
    #   学生明明说了是哪一条，系统却像没听见。
    for w in ("不是这个", "不是这条", "不是那个", "不是", "不对", "错了", "搞错了",
              "搞错", "换一条", "换一个", "重新选", "重选", "我要的是", "我说的是",
              "其实是", "应该是", "上次", "刚才", "前面", "那回"):
        t = t.replace(w, " ")
    # ★ 「算了」是**反悔的框词**，不是名字的一部分（2026-10-12 端到端才暴露的）：
    #   「算了不吃了」把软词「不吃了」擦掉之后剩下「算了」，而「算了」既不在否决表里、
    #   也不算指代词 → 抠出来的"名字"就是它 → `by_name=True` → 一条都对不上 →
    #   回一句「我在日程里没找到叫『算了』的待办」。判定层明明认出了删待办，
    #   学生听到的还是"没这条"。同一形状的教训见红线 13（残渣当点名）。
    t = t.replace("算了", " ")
    # 纠正句里"是交电费"的"是"留在名字里，卡上会写出「是交电费」这种怪话 → 只擦句首那个。
    # ⚠️ 前面可能还留着逗号（"不是这个，是交电费" → "，是交电费"），标点要一起放过。
    t = re.sub(r"^[\s，,、；;]*是\s*", "", t)
    for w in (_REMOVE_TODO_INTENT + _REMOVE_TODO_SOFT + _REMOVE_INTENT + _DONE_TODO_INTENT
              + _DONE_TODO_VERB
              + _TODO_NAME_NOISE + _COLLIDE_WORDS + (
              "待办", "日程", "事项", "安排", "标记", "完成", "删",
              "那条", "这条", "那一条", "这一条",
              "一条", "一下", "一下下", "的")):
        t = t.replace(w, " ")
    # ⚠️ "把"可能连着出现两次（"帮我**把**交电费去掉" → 先擦掉"帮我"之后"把"又到句首了），
    #    也可能被学生说在句尾（"游泳删掉**把**"，倒装）→ 两头都要擦。
    for _ in range(2):
        t = re.sub(r"^(我要|我想|帮我|请帮我|请|给我|把|别|我|将|把了)\s*", "", t)
    t = re.sub(r"[呗啊呀呢吧哦啦哈呐咧咯嘛喽把把了]+$", "", t)
    t = re.sub(r"[，,。！!？?、；;：:~～\-—『』「」\"'“”‘’\s]", "", t)
    return t.strip()[:30]


def _todo_name_match(want: str, title: str) -> bool:
    """待办名对不对得上（人话：含糊匹配，「游泳」能对上「游泳锻炼」）。"""
    a, b = (want or "").strip(), (title or "").strip()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _lev(a: str, b: str) -> int:
    """两个字符串的编辑距离（人话：打错几个字）——小字符串够用，不引第三方库。"""
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_name_match(want: str, title: str) -> bool:
    """**打错字**也算对得上（人话：「游永」→《游泳》）。

    ⚠️ 判据刻意收紧成两条**同时**成立，宁可漏也不可替他删错：
      · 编辑距离：短名（≤4 字）只许错 **1** 个字，长名许错 2 个；
      · 字符重合度 ≥ 0.5（「游永」vs「游泳」= 2×1/3 ≈ 0.67 过；
        「上课」vs「吃饭」= 0 不过）。
    不做"最像的那一条"这种排序猜测——0.31 比 0.29 是噪声；
    这里只认"明显就是它打错了"的那一档，猜出来的卡还要**写明依据**并留"不对"的出口。
    """
    a, b = (want or "").strip(), (title or "").strip()
    if not a or not b or len(a) < 2:
        return False
    tol = 1 if min(len(a), len(b)) <= 4 else 2
    if _lev(a, b) > tol:
        return False
    sa, sb = set(a), set(b)
    return (2 * len(sa & sb) / (len(sa | sb) or 1)) >= 0.5


def _fuzzy_name_hits(name: str, items: list) -> list:
    """在所有条目里按**打错字**的判据找一遍（名字精确匹配落空后的兜底）。"""
    return [x for x in (items or [])
            if _fuzzy_name_match(name, (x.get("title") or "").strip())]


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


def _pick_todo_indexes(text: str, cap: int | None = None) -> list:
    """认出学生用小序号点名，可能一次点好几条（人话：「第二条」「2」「第2、3条」「23」）。

    为什么光秃秃的数字也要认：系统上一句刚列了编号清单，学生顺手敲「2」、
    甚至把想删的几条连写成「23」，都是极自然的答法。旧写法只认「第N条」，
    学生回「23」就被当成一个叫"23"的待办名 → 报"没找到"（学生截图："记忆有问题"）。

    :param cap: 当前清单有几条。给了才敢把「23」拆成 [2,3]（每一位都得是合法序号）；
                没给就只认「第N条」这种明确写法。返回 1 起的序号，-1 = 最后一条。
    """
    t = (text or "").strip()
    if not t:
        return []
    if "最后一条" in t or "最后一个" in t or "最后那条" in t:
        return [-1]
    # ① 明确的「第N条/个/项」，可能一次点了多个（"第2、3条" → [2,3]）
    m = re.search(r"第\s*([0-9一二三四五六七八九十两\s、,，/和跟与]{1,20}?)\s*(?:条|个|项)", t)
    if m:
        out = []
        for tok in re.findall(r"[0-9]{1,2}|[一二三四五六七八九十两]{1,3}", m.group(1)):
            n = int(tok) if tok.isdigit() else _cn_num(tok)
            if n and 1 <= n <= 20:
                out.append(n)
        if out:
            return out
    # ② 光秃秃的数字串（可带 、,/ 空格 等分隔）：只在"整句基本就是一个序号"时才算
    if cap is None:
        return []
    digits = re.sub(r"[\s、,，.。/和跟与\-—]+", "", t)
    if not digits.isdigit():
        return []
    whole = int(digits)
    if 1 <= whole <= cap:
        return [whole]
    # 整体超出范围，但每位都合法 → 当"一次点了好几条"（"23" → [2,3]）
    if len(digits) > 1:
        parts = [int(c) for c in digits]
        if all(1 <= p <= cap for p in parts):
            return parts
    return []


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
    if not (any(w in t for w in _REMOVE_TODO_INTENT)
            or any(w in t for w in _REMOVE_TODO_SOFT)
            or _DEL_ANY_RE.search(t)):
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


def _done_already_said(t: str) -> bool:
    """这句话带没带"**已经**办完"的标记（而且是明说，不是猜）。

    句末的"了/啦/咯"是中文的已然体；"已经/刚才/昨天"那些是显式过去。
    ⚠️ 顺序是**先否后肯**：话里出现「明天/打算/记得」这类计划词就一律不算，
    哪怕句子以"了"结尾 —— 「明天作业写完了就能玩」里的"写完"是**假设**，
    先查已然再查计划就会把它当成宣布办完（真机形状，测试 G 批钉着）。
    """
    if any(w in t for w in _DONE_FUTURE_WORDS):
        return False
    tail = (t or "").rstrip("。！!，,、~…\u2026 ）)】》")
    if tail.endswith("了") or tail.endswith("啦") or tail.endswith("咯"):
        return True
    return any(w in t for w in _DONE_EVIDENCE_WORDS)


def _done_resultative(name: str, t: str) -> bool:
    """这条待办的名字是不是被一句**动结式**宣布办完了（识别用的唯一判据）。

    五种口语写法，每一种都要求补语**紧贴**事名，不许隔山打牛：
      「去完图书馆」   动词+补语在名字**前**
      「图书馆去完了」  动词+补语在名字**后**
      「健完身」      补语插在名字第一个字后面（动宾式的名字常被这么拆开）
      「作业写完了」   宾语提到动词前（《写作业》被倒过来说）
      「从图书馆回来了」办完就回来了，说的同样是"这条已经办了"
    ⚠️ 三道闸缺一不可：①已然标记（见 `_done_already_said`），
    ②补语前不能是「没/不/未/别」（「作业没写完」「还没去完图书馆」是**没办完**），
    ③动词必须在册（「健身累完了」的"累"不在册 → 不算宣布）。
    """
    n = (name or "").strip()
    t = (t or "").strip()
    if len(n) < 2 or not t:
        return False
    if not _done_already_said(t):
        return False
    nn = re.escape(n)
    tail_cpl = r"(?:了|啦)?"          # 「去完了图书馆」里的"了"在补语后面
    neg = r"(?<![没不未别])"           # 否定贴着动词 → 这句说的是没办完
    pats = [
        rf"{neg}[{_DONE_ACT_V}]{_DONE_CPL}{tail_cpl}\s*{nn}",   # 去完图书馆 / 写完了作业
        rf"{nn}\s*{neg}[{_DONE_ACT_V}]{_DONE_CPL}{tail_cpl}",   # 图书馆去完了
        rf"{re.escape(n[0])}{_DONE_CPL}{tail_cpl}{re.escape(n[1])}",  # 健完身 / 写完作业
    ]
    if len(n) >= 3:
        # 宾语前置只对 **3 字以上**的名字开放。两字名字的前置形态只剩最后一个字当锚，
        # 实测（chk_r5_gate.py）放宽会把「身穿完了」撞上《健身》；而两字名字真被倒过来说时
        # 多半是「书写完了」这种看着像动宾、其实像另一件事的句子。
        # 两字名常见的倒装（「作业做完了」）本来就由上面①的整词表接住，不靠这一档。
        pats.append(rf"{re.escape(n[1:])}\s*{neg}[{_DONE_ACT_V}]{_DONE_CPL}{tail_cpl}")
    pats.append(rf"{nn}[^，。！？]{{0,3}}?(?:回来了|回来啦|回了|回宿舍了|回寝了)")
    return any(re.search(p, t) for p in pats)


def _done_shape_present(t: str) -> bool:
    """句子里到底有没有"办完了"的**形状**——纯预筛，不判对错（把关全在 `_done_resultative`）。

    ⚠️ 宽到只要求出现「完 / 好 / 回来了」之一，**不许拿动词表当预筛**：
    「健完身」的"健"不在动词表里，上一版在这儿就把这句挡掉了 ——
    补语插在名字中间的那种写法，动词位置根本不是我们认识的那个字。
    """
    return (any(v in t for v in _DONE_TODO_VERB)
            or "完" in t or "好" in t
            or bool(re.search(r"(?:回来了|回来啦|回了|回宿舍了|回寝了)", t)))


def wants_done_todo(text: str) -> bool:
    """判断一句话算不算「把某条待办标记为完成」（确定性分支用的开关）。

    跟 wants_remove_todo 对称：删待办是"拿走一条"，标记完成是"把已有的一条置 done"。
    同样的让路规则——清空、删课、加课/加待办都优先于它。
    此外「完成」太泛不能当总开关，所以：
      · 显式的"标记/勾/划"指令（"把游泳标记完成""勾掉交电费"）直接算；
      · "完成/做完/搞定"这类软动词，只在**精准对上一条已有待办的名字**时才算
        （"游泳做完了"对上《游泳》才算；"帮我安排完成作业"里"作业"不是待办 → 不算）。
      · ③ 动结式（"去完图书馆""健完身""作业写完了""从图书馆回来了"）——
        学生最口语的一族，整词表一个都不沾，判据见 `_done_resultative`。
    """
    t = (text or "").strip()
    if not t or len(t) > 80:
        return False
    if wants_clear_timetable(t):
        return False
    # 删待办优先：学生说"删掉并标记完成"极少，按删处理更稳妥
    if wants_remove_todo(t) or wants_remove_todo_loose(t):
        return False
    # 删课让路给课表那条路
    if wants_remove_course(t) and not any(w in t for w in _TODO_WORDS):
        return False
    if wants_add_course(t) or wants_add_todo(t):
        return False
    # ① 显式"标记/勾/划"指令：本身就是标记完成
    if any(w in t for w in _DONE_TODO_INTENT):
        return True
    # ② 软动词：只有精准对上一条已有待办才认（否则跟"新增"撞车）
    #    问句先挡掉：「健身完了吗」「我作业写完了没」是在**问**，不是在宣布做完。
    #    显式的"标记/勾/划"指令走上面①，不受这里影响。
    if looks_like_question(t):
        return False
    # 预筛：这句话有没有"办完了"的**形状**（整词 / 动结式 / 回来了），
    # 三种都不沾就一次名字都不比（大部分句子在这儿就出去了）。
    if not _done_shape_present(t):
        return False
    for x in list_todos():
        name = (x.get("title") or "").strip()
        if not name:
            continue
        if _todo_name_match(name, t):
            # 常规软动词：事名对得上就算（「游泳做完了」对上《游泳》）
            if any(v in t for v in _DONE_TODO_VERB if v not in _DONE_TODO_VERB_TIGHT):
                return True
            # 「完了」这一颗要紧贴事名：「健身完了」✓，「健身累完了」✗（那是在说累）
            if any(re.search(re.escape(name) + r"\s*" + v, t)
                   for v in _DONE_TODO_VERB_TIGHT):
                return True
        # ③ 动结式**不要求名字被原样包含**：「健完身」对《健身》、「作业写完了」对《写作业》，
        #    补语插在名字中间或宾语提前，包含关系当场就断了。判据自己带着名字做贴合，够严。
        if _done_resultative(name, t):
            return True
    return False


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


def resolve_todo_remove(text: str, pool: list | None = None) -> dict:
    """把"删掉周二那条游泳的待办"解析成**具体哪一条待办**（只读，一个字节都不删）。

    返回：
      {"status": "ok",        "proposal": {...}, "todos": [...]}  恰好一条，可以出确认条
      {"status": "many",      "todos": [...]}                     好几条，得让学生挑
      {"status": "empty",     "todos": []}                        这一天/这个名字没有待办
      {"status": "unclear",   "todos": [...]}                     没说清是哪条

    :param pool: 只在**这批候选**里找（学生正对着上一句列的清单回答时用）。
                 不传 ＝ 按全库找，是原来的行为。
    """
    t = (text or "").strip()
    day = _pick_date(t)
    name = _pick_todo_name(t)
    in_context = pool is not None

    # 先把池子缩小：说了日期就只看那天，说了名字就再按名字筛
    pool = list(pool) if in_context else (list_todos(day) if day else list_todos())
    if in_context and day:
        narrowed = [x for x in pool if (x.get("date") or "") == day]
        if narrowed:
            pool = narrowed
    # ★ "那个啥/上次那个事"这种**指代词**不算点名：他说的是"某个东西"，不是某个名字。
    #   算成点名 → 按名字一条都找不到 → 回"没找到"；不算点名 → 池子就是全部待办 →
    #   走"列清单问是哪一条"，比"没找到"有用得多（模糊口语的正确答法）。
    _VAGUE_NAMES = ("", "待办", "日程", "事项", "事", "事情", "东西", "玩意",
                    "那个", "这个", "啥", "什么", "一件", "那条", "这条")
    by_name = bool(name) and name not in _VAGUE_NAMES
    fuzzy = False          # 这一批是不是靠"打错字"的判据捞上来的
    if by_name:
        hits = [x for x in pool if _todo_name_match(name, x.get("title", ""))]
        # 名字没命中，但整句话里出现了某条待办的名字（"帮我把交电费取消了吧"），
        # 兜一层：从**全部**待办里找（学生可能没提日期，或日期说得不准）。
        # ⚠️ 这一兜在"对着清单回答"时**也要留着**：学生在清单摆着的时候说了个
        #    清单外的新目标（"删掉跑步"），那是**新的一句下单**，不该被清单困住
        #    （不给兜的话他会得到"重新列一遍游泳/交电费"，看着像没听懂）。
        if not hits:
            hits = [x for x in list_todos() if _todo_name_match(name, x.get("title", ""))]
        if not hits:
            # ★ 再兜一层**打错字**（「把游永删了」→《游泳》）。
            #   先在这批候选里找，再在全库找。早先没有这一兜，学生打错一个字
            #   就得到"没找到这条待办"——可库里明明就躺着那一条。
            hits = _fuzzy_name_hits(name, pool) or _fuzzy_name_hits(name, list_todos())
            fuzzy = bool(hits)
        if not hits:
            return {"status": "empty", "todos": [], "day": day, "name": name}
        pool = hits

    # 「撞时间的」是**筛条件**（挑出时间重叠的），不是待办名。说了就照它收窄池子；
    # 收窄后为空 = 那天压根没有撞上的，如实说"没找到"，绝不把不撞的端出来充数。
    if _wants_colliding(t):
        pool = _colliding_subset(pool)

    if not pool:
        return {"status": "empty", "todos": [], "day": day, "name": name}
    # ★ 靠"打错字"只捞到**一条**时也要**写明是猜的**（真机实测出来的漏子）：
    #   早先直接走下面 `len(pool) == 1` 那一支，卡上干干净净、一个字都不提——
    #   学生打错字、系统替他改了名字还不说，他会以为本来就有一条叫这个的，
    #   看都不看就点确认。猜的和命中的，卡片上必须长得不一样。
    if by_name and fuzzy and len(pool) == 1:
        return {"status": "ok",
                "proposal": _todo_remove_proposal(
                    pool[0], picked_by="fuzzy_name",
                    note=(f"日程里没有**正好**叫「{name}」的，"
                          f"最接近的是《{pool[0].get('title', '')}》——"
                          f"我当你是打错字了。不是这条就回我「不对」。")),
                "todos": pool}
    if len(pool) == 1:
        return {"status": "ok", "proposal": _todo_remove_proposal(pool[0]), "todos": pool}

    # ★ 命中好几条：先看**证据够不够硬**，够硬就替他挑一条（仍然只出提案，不直接删）。
    #   ⚠️ 两道闸门都必须过：**先得点名了东西**，再看证据。
    #      为什么"点了名"是前提：只说筛条件（"删除撞时间的代办"）时，学生连**要删几条**
    #      都还没定（他可能要删一整批）——这时候替他挑一条，是在他没说的地方做主，
    #      比不猜还糟。点了名才是"处理这个名字名下的东西"。
    #   过了闸门再看两处**结构上确定**的事实：
    #   ① 名字**精确等于**的只有一条（"游泳" 对上《游泳》和《游泳锻炼》→ 前者是精确的）；
    #   ② 这名字名下几条**在业务上完全等价**（同名同日同时段同状态）——删哪条学生看到的
    #      日程都一样，所以挑一条**不存在"猜错"**：他要的就是"把重复的去掉一份"。
    #   两个信号都不做相似度猜测：0.31 比 0.29 是噪声，按"最像"下结论会给出**假确定**。
    #   ⚠️ 猜出来的仍然只是**提案**：卡上写明"我是怎么挑的"，学生点头才动。
    if by_name:
        if fuzzy:
            # ★ 打错字这一档：**等价组**优先（"游永"对上两份一模一样的《游泳》→
            #   删哪份都一样，直接出卡），否则只有一条就出卡并写明"我当你是打错字了"；
            #   好几条都像打错字 → 不猜，列清单让他点名。
            grp = _single_equiv_group(pool)
            if grp:
                pick = _earliest(grp)
                others = [x for x in grp if x.get("id") != pick.get("id")]
                return {"status": "ok",
                        "proposal": _todo_remove_proposal(
                            pick, picked_by="fuzzy_name", alternates=others,
                            note=(f"没有正好叫「{name}」的，最接近的是"
                                  f"《{pick.get('title', '')}》，它有 **{len(grp)} 份一模一样**的"
                                  f"——删掉任意一份都一样，我挑的是**先写进去的那条**。"
                                  f"要删另一份就回我「换一条」。")),
                        "todos": [pick]}
            # （只捞到一条的情形已在上面先返回了——那里也要写明"我当你是打错字了"）
            return {"status": "many", "todos": pool, "day": day, "name": name}
        exact = [x for x in pool if (x.get("title") or "").strip() == name]
        if len(exact) == 1:
            return {"status": "ok",
                    "proposal": _todo_remove_proposal(
                        exact[0], picked_by="exact_title",
                        note=f"日程里名字**正好**是「{name}」的只有这一条，就是它。"),
                    "todos": [exact[0]]}
        grp = _single_equiv_group(pool)
        if grp:
            pick = _earliest(grp)
            others = [x for x in grp if x.get("id") != pick.get("id")]
            return {"status": "ok",
                    "proposal": _todo_remove_proposal(
                        pick, picked_by="duplicate_group", alternates=others,
                        note=(f"「{name}」有 **{len(grp)} 份一模一样**的（同一天、同一时段），"
                              f"删掉任意一份效果都一样——我挑的是**先写进去的那条**。"
                              f"要删另一份的话，回我「换一条」。")),
                    "todos": [pick]}

    # 剩下好几条、证据又不够硬：学生没说清（"删掉明天那条"而明天有两条不同的）——绝不猜
    return {"status": "many", "todos": pool, "day": day, "name": name}


def _todo_remove_proposal(item: dict, note: str = "", picked_by: str = "",
                          alternates: list | None = None) -> dict:
    """把一条待办包成一张"待删除"提案（人话：原样念一遍，等学生点头）。

    :param note:      这条是怎么被挑中的（**必须**在学生能看见的地方写出来）。
                      系统替他拿主意时，得让他一眼看出"挑的依据"，否则他会产生
                      虚假确定感——不看卡就点确认，那和"直接删"没区别。
    :param picked_by: 依据的机器名（"duplicate_group" / "exact_title"），给前端和测试认。
    :param alternates: 同等可选的其它条（同一天同一时段的等价条目），供"不对"时参考。
    """
    date = item.get("date", "")
    prop = {
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
    # "这条是怎么挑中的"只在这条**是系统替他拿主意**的时候才带上；
    # 唯一命中、或学生自己点名的，picked_by 为空，卡上就不啰嗦。
    if picked_by:
        prop["picked_by"] = picked_by
    if note:
        prop["note"] = note
    if alternates:
        prop["alternates"] = [x.get("id", "") for x in alternates if x.get("id")]
    return prop


def todo_remove_batch_proposal(items: list) -> dict:
    """把"你列的这几条**全都要删**"包成一张**批量**确认条（人话：一次列清、一次点头）。

    为什么要一张卡、不挂 N 张单条卡：学生说的就是"都"——那就该**一次点头、一次删完**。
    挂 N 张条让他点 N 次，等于没听懂他的话（学生原话："去思考学生表达的意思，
    不是字面上的"）。卡上把**每一条都原样念出来**，他点【确认删除全部】之前，
    一条都不会动 —— "先列清楚，再动手"这条规矩不破。
    """
    its = [_todo_remove_proposal(x) for x in (items or [])]
    shown = "、".join(x["title"] for x in its[:3])
    tail = f" 等" if len(its) > 3 else ""
    return {
        "kind": "todo_remove_batch",
        "items": its,
        "count": len(its),
        "summary": f"{shown}{tail}（共 {len(its)} 条）",
    }


def todo_done_batch_proposal(items: list) -> dict:
    """标记完成的批量版（跟 todo_remove_batch_proposal 一对：学生说"都"就一次办完）。"""
    its = [_todo_done_proposal(x) for x in (items or [])]
    shown = "、".join(x["title"] for x in its[:3])
    tail = f" 等" if len(its) > 3 else ""
    return {
        "kind": "todo_done_batch",
        "items": its,
        "count": len(its),
        "summary": f"{shown}{tail}（共 {len(its)} 条）",
    }


def parse_remove_todo(text: str) -> dict | None:
    """解析成一张删待办确认卡；不是"恰好一条"就返回 None（交给上层列清单/如实说）。

    返回的只是**提案**——本函数不删任何东西。学生点确认条上的【确认删除】、
    或在聊天框回一句"确认"，才由系统落库。
    """
    res = resolve_todo_remove(text)
    return res.get("proposal") if res.get("status") == "ok" else None


def build_retime_existing_proposal(text: str, sem: dict | None = None,
                                   hint_title: str = "", auto_slot: bool = False) -> dict:
    """把"把周四那个健身挪到周五"解析成一张**改期提案**（人话：已排进日程的待办想换时间）。

    跟 `retime_todo_proposal` 的区别：那条是"学生嫌**待确认卡**的点不合适、
    在原来的待确认卡上换时间"；这条是"**已经写进日程**的待办想换个时间"——
    所以得先把目标待办从库里找出来，再基于它生成新时间的提案，并在提案里记下
    `old_todo_id`，确认时由系统走"删旧 + 加新"（不是就地改字段，方便追溯）。

    返回：
      {"status":"ok","proposal":{...},"old_label":""}  恰好一条，可出确认条
      {"status":"many","todos":[...]}                  好几条同名，得让学生挑
      {"status":"empty","todos":[],"pool_n":N}          没找到那条待办 —— #84：`pool_n` 是
                                                       **收窄之前**的全库条数：
                                                       >0 = 我没认出来（该换那条能查的路），
                                                       0 = 库里真没有（"没找到"是实话）
      两种 `unclear` 按 `reason` 分（#82 追记 §1.3：**话术要和来源对上**，别都算"没听清"）：
      {"status":"unclear","reason":"no_slot","slot_label":"2026-10-30（周五）","todos":[]}
                                                       他**说了日子**、是那天满了 ⇒ "那天排不下"
      {"status":"unclear","reason":"no_slot","slot_label":"最近的几个周六周日",
       "todos":[旧条目],"old_label":...}
                                                       #82 的周末档全排不下 = 同一个 `no_slot`，
                                                       不再单长一套 `weekend_full`
      {"status":"unclear","reason":"no_time","todos":[旧条目],"old_label":...}
                                                       #81：新三元组与旧值全等 = 空改动 ⇒ 追问（念现状）
      {"status":"unclear","reason":"no_time","todos":[]}
                                                       钟点不合法（给了但认不出）= 同一句"没听清"

    `hint_title`（#77）：**上文最近唯一提到的那条待办名**。学生只说「换个时间」时他指的是它，
    而这函数原先只看得到这一句 —— 名字抠不出来就把"匹配所有"当成答案，把全部待办倒给他。
    只在 `name` 为空/占位符时接手；`name` 有值时它**完全不参与**。调用方给的必须已是
    "唯一匹配"那一份（判据在 `main._recent_todo_title`），多条或零条一律传空串。

    `auto_slot`（#86）：**只给引擎那一路用**（`propose_todo_change_tool`），默认 `False`。
      · `False` = 现有行为**逐字不变**：什么新时间都没给 ⇒ 沿用旧时刻 ⇒ 被 #81 的空改动判据拦成
        `unclear/no_time`，由确定性分支追问一句（快、稳，学生补一句就办了）；
      · `True` = **替他挑一段**（`pick_free_slot`，往后最多试 3 天），挑不出 ⇒ `unclear/reason=no_slot`。
      为什么同一份函数要两种策略：确定性分支手上只有"这句没给时间"这个事实，追问是对的；
      引擎那一头已经查过课表和待办、知道撞了谁，他要的是"帮我想个时间"（学生原话
      「没有帮我想时间，是我问了才说的」）。**用参数分开，不在函数里猜"现在是谁在调我"。**
      真正新增的只有"什么都没给"那一档 —— 实测「挪到 2026-11-02」（只换日子）本来就会挑空档出卡，
      两档共用下面那一次 `pick_free_slot`，所以默认值一改，普通模式立刻开始自作主张（E4 钉这个）。

    只读，一个字节都不写库——执行权永远在"学生点头"之后。
    """
    t = (text or "").strip()
    # 先把"改成/挪到/换到"这类换时间的词擦掉，免得粘在名字里
    #（"把瑜伽挪到周五"不擦会变成"瑜伽挪到"，对不上库里的"瑜伽"）。
    t_clean = re.sub(r"(改成|改到|换成|换到|换个时间|换个点|挪到|调整到|调到|重排|改一下|重来)", " ", t)
    # 目标名字：优先用语义层读出来的（模型偶尔回怪名字），不像话就退回从话里抠
    name = (sem or {}).get("title") or ""
    if name:
        try:
            if not looks_like_thing(_pick_title(name)):
                name = _pick_todo_name(t_clean)
        except Exception:
            name = _pick_todo_name(t_clean)
    else:
        name = _pick_todo_name(t_clean)

    # ★ #77：这句里没点名 → 接上文最近唯一提到的那条（学生说「换个时间」指的就是它）。
    #   只有 `name` 抠不出东西时才走到这儿接手，所以带名字的「把周四的游泳挪到周五」
    #   照旧按名字走，hint 一个字都不参与（P1 钉这一条）。
    #   猜错的代价 = 把 A 改成 B，而改期是"删旧 + 加新"，回退要两次；
    #   多问一句的代价 = 他多打五个字。⇒ 上游必须只传"唯一匹配"，宁可空着。
    if not name or name in ("", "待办", "日程", "事项"):
        name = (hint_title or "").strip()

    # 候选池：按名字在**全部**待办里找（话里既可能有旧日期也可能有新日期，
    # 不能拿 _pick_date 去缩池，否则容易把"周五"当成定位条件反而不匹配）。
    pool = list_todos()
    if name and name not in ("", "待办", "日程", "事项"):
        hits = [x for x in pool if _todo_name_match(name, x.get("title", ""))]
        if not hits:
            # 语义层/样本库给的名字没命中（典型：样本库把"瑜伽"误记成"健身"——
            # "把周四那个健身挪到周五"和"把周四那个瑜伽挪到周五"长得几乎一样，
            # Dice 一撞就把缓存的旧名字盖过来）。退回从**学生原句**里抠名字再试一次，
            # 他这句话里通常就带着真名字（"瑜伽"），抠出来就能对上库里的那条。
            fallback = _pick_todo_name(t_clean)
            if (fallback and fallback not in ("", "待办", "日程", "事项")
                    and fallback != name):
                fb_hits = [x for x in pool
                           if _todo_name_match(fallback, x.get("title", ""))]
                if fb_hits:
                    name, hits = fallback, fb_hits
        if not hits:
            # ★ #84：把"库里到底有没有东西"带出去 —— 调用方要靠它分清
            #   「我没认出来」（池子非空）和「库里真的没有」（池子空）。这两样共用一句
            #   "没找到那条待办"就是假话（学生要改的那条就在里面）。
            #   这里取 len(pool) 不是笔误：收窄发生在**下一行** `pool = hits`；
            #   谁把那句提上来，test_96 A2 会当场红（它钉的是"这一处带的是全库条数"）。
            return {"status": "empty", "todos": [], "pool_n": len(pool)}
        pool = hits
    if not pool:
        # 库里真的一条都没有 ⇒ pool_n=0，调用方那句"没找到、你先加进日程"在这一档是实话
        return {"status": "empty", "todos": [], "pool_n": 0}
    if len(pool) > 1:
        return {"status": "many", "todos": pool}

    old = pool[0]
    old_id = old.get("id", "")
    old_date = old.get("date", "")
    old_label = (f"{old_date}（{_weekday_name(old_date)}）"
                 f"{old.get('start', '')}-{old.get('end', '')}")

    # 新日期：语义层给的 > 话里抠出来的 > 没说就沿用旧日期（只改时刻）
    new_date = (sem or {}).get("date") or ""
    if not new_date:
        dh = (sem or {}).get("day_hint") or ""
        if dh:
            new_date = _pick_date(dh)
        if not new_date:
            # 换时间词（"挪到/改到"）后面的那个日期才是目标日。
            # 学生常说"把**周四**那个瑜伽挪到**周五**"——前面的"周四"是旧日，
            # 只按第一个日期抠会把目标误判成周四（等于原地没动）。
            # 取换词之后的片段来认日期，避开旧日干扰。
            tail = t
            for w in ("改成", "改到", "换成", "换到", "换个时间", "换个点", "挪到",
                      "调整到", "调到", "重排", "改一下", "重来"):
                idx = t.find(w)
                if idx >= 0:
                    tail = t[idx + len(w):]
                    break
            new_date = _pick_date(tail) or _pick_date(t)
    _from_weekend = False   # ★ #82 追记 §1.2 的另一半：见下面 `if new_date != old_date or _from_weekend`
    # ★ #82：说了「周末 / 双休」却没落到具体哪天 —— `_pick_date` 不认它（那张正则要「周|星期|礼拜 + 一二三四五六日天1234567」，实测 `_pick_date('周末') is None`，
    #   :528 那条注释自证）。不接这一档就掉进下面那句"沿用旧日期"，再被 #81 的空改动判据拦住 ⇒
    #   追问「想改到哪天」，而学生**刚刚说过**。词表用 #75 那份 `_WEEKEND_WORDS`，**不动 `_pick_date` 的正则**：
    #   它二十几处调用方把返回值当"学生说了一个确定的日子"用（施工单 §2.1）。
    if not new_date and any(w in t for w in _WEEKEND_WORDS):
        base = clock.today()
        cand = []
        if base.weekday() >= 5:            # 今天就是周末 → 先看今天（"改到周末"多半指眼前这个）
            cand.append(base.isoformat())
        # (0,1)=本周六日，(7,8)=下个周六日：一个周末只有两天，这两天可能全被课占满（同 #75 放 `days_ahead` 的那笔账）。
        sat0 = base + datetime.timedelta(days=(5 - base.weekday()) % 7)
        cand += [(sat0 + datetime.timedelta(days=off)).isoformat() for off in (0, 1, 7, 8)]
        cand = list(dict.fromkeys(cand))   # 今天是周六时 sat0 就是今天，去重
        # ★ #82 追记 §1.2：他已经在周末里再说「改到周末」，多半是想**换到另一天** ⇒ 旧日期排到最后
        #   （**不是删掉**：另一天满时，"这一天里换个时段"仍比"那几天都满了"有用）。
        _old_d = old.get("date", "")
        if _old_d in cand:
            cand = [d for d in cand if d != _old_d] + [_old_d]
        for _cd in cand:
            # 今天要留出缓冲（与本文件 :1267 `list_slots` 同一个约定）：实测 23:30 不传 not_before 时
            # 它能挑出"今天 08:00-09:30"——一张已经过去的卡，学生刷新永远点不到它。
            _nb = (_now_minutes() + 30) if _cd == base.isoformat() else None
            if pick_free_slot(_cd, prefer=t, not_before=_nb):
                new_date = _cd
                _from_weekend = True   # 这一段的"排得下"已经算过 ⇒ 定时刻那一步不许再用旧时刻顶回去
                break
        if not new_date:
            # 候选的周末全排不下 → **如实说排不下**，不能退回 #81 那句"想改到哪天"（他刚说了周末，再问一遍
            #   就是本单要治的病）。带一个自己的标记，让调用方换句话；不新造 status。
            return {"status": "unclear", "todos": [old], "old_label": old_label,
                    "reason": "no_slot", "slot_label": "最近的几个周六周日"}
    if not new_date:
        new_date = old_date

    # 新时刻：语义层给的 > 话里抠出来的 > 换了个日子就挑空档 > 否则沿用旧时刻
    sem_start = (sem or {}).get("start") or ""
    sem_end = (sem or {}).get("end") or ""
    begin, finish = (None, None)
    if sem_start and sem_end:
        begin, finish = sem_start, sem_end
    else:
        sp = _pick_span(t)
        begin, finish = (sp[0], sp[1]) if sp else (None, None)
    if begin and not finish:
        finish = to_hhmm(to_minutes(begin) + 60)
    if not begin:
        if new_date != old_date or _from_weekend:
            # 今天要留出缓冲（与本文件 :1267 `list_slots` 同一个约定）。不传 `not_before` 时实测挑得出"今天 08:00-09:30"
            # 这种**已经过去**的段（09:00 问「挪到今天」、#82 之后周末候选里也常有今天）——卡片挂出来却没人点得到。
            _nb = (_now_minutes() + 30) if new_date == clock.today().isoformat() else None
            slot = pick_free_slot(new_date, prefer=t, not_before=_nb)
            if not slot:
                # ★ #82 追记 §1.3：他**说了日子**、是那天满了 —— 说「新时间我没听清」是当众说错话。
                return {"status": "unclear", "todos": [],
                        "reason": "no_slot",
                        "slot_label": f"{new_date}（{_weekday_name(new_date)}）"}
            begin, finish = slot["start"], slot["end"]
        elif auto_slot:
            # ★ #86：引擎那一路**不许反问"你想改到几点"** —— 替他挑一段。
            #   从目标那天开始往后最多试 3 天（`pick_free_slot` 本身就避开课与已排的事）；
            #   今天要留 30 分钟缓冲（同一份 `not_before` 约定，#82 那笔老洞就在这里堵着）。
            #   挑不出不硬凑旧时刻，退成 `unclear/no_slot` 让工具说"这几天排不下"。
            base_d = datetime.date.fromisoformat(new_date or clock.today().isoformat())
            for _off in (0, 1, 2):
                _d = (base_d + datetime.timedelta(days=_off)).isoformat()
                _nb2 = (_now_minutes() + 30) if _d == clock.today().isoformat() else None
                _s = pick_free_slot(_d, prefer=t, not_before=_nb2)
                if _s:
                    new_date, begin, finish = _d, _s["start"], _s["end"]
                    break
            if not begin:
                return {"status": "unclear", "todos": [old], "old_label": old_label,
                        "reason": "no_slot",
                        "slot_label": f"{base_d.isoformat()} 起三个日子"}
        else:
            begin, finish = old.get("start", ""), old.get("end", "")
    # ★ #81：上面两个"没给就沿用旧值"的兜底同时生效 ⇒ 新三元组与旧的一模一样。
    #   这不是改期，是原地不动 —— 放它过去会出一张「从 15:00-16:00 改到 15:00-16:00」的卡，
    #   而话术写着「旧的那段会挪走」：学生点【确认加入】= 删一条再加一条同样的，
    #   并且提案不带 note/category（`main.py` 确认口从 body 取，没给就落成空串）⇒ 备注与分类会被抹掉。
    #   判"结果是否相同"而不是"输入缺了哪一项"：缺项的组合有十几种，相同只有一种（施工单 §2.2）。
    #   只判"没说日子"会漏掉「游泳改到 15:00」这种**报了同样的钟点**（实测：也是新==旧）；
    #   只判"没报钟点"会误伤「挪到周五」（换了日子、钟点由空档挑，是有效改动）。
    #   落进已有的 `unclear` 分支就够，不新造 status（施工单 §2.1）；
    #   带上 `todos:[old]` 与 `old_label`：调用方要能说出"你要改的是这条、它现在是这样"。
    if (new_date == old_date and begin == old.get("start", "")
            and finish == old.get("end", "")):
        return {"status": "unclear", "todos": [old], "old_label": old_label,
                "reason": "no_time"}
    if not (begin and finish) or to_minutes(finish) <= to_minutes(begin):
        # reason=no_time：这一族统一叫"没有可用的新时间"（给了但认不出 = 压根没说，同一句话术）。
        return {"status": "unclear", "todos": [], "reason": "no_time"}

    title = old.get("title") or "待办"
    wd = _weekday_name(new_date)
    proposal = {
        "kind": "todo_add",
        "title": title,
        "date": new_date,
        "start": begin,
        "end": finish,
        "weekday": wd,
        "minutes": to_minutes(finish) - to_minutes(begin),
        # ★ 同 `retime_todo_proposal` 里那一条（本文件 3649 行附近）：换时间不许顺手
        #   把"每天/每周"换没。这一条是"删旧 + 加新"，旧条目身上的 repeat 不接过来，
        #   学生说「把背单词改到晚上十点」之后，那条**每天**的习惯就只剩这一天了
        #   （2026-10-13 实测：新条目 repeat=None，而旧的那条已被删掉）。
        "repeat": old.get("repeat") or REPEAT_NONE,
        "retimed": True,
        "old_todo_id": old_id,
        "summary": f"{title}｜{new_date}（{wd}）{begin}-{finish}",
    }
    return {"status": "ok", "proposal": proposal, "old_label": old_label}


def propose_todo_change_tool(op: str = "retime", title: str = "",
                             new_date: str = "", new_start: str = "",
                             new_end: str = "") -> str:
    """改一条**已经写进日程**的待办的时间（改期）。只出提案，不写库。

    ⚠️ 它**不新建**：要往日程里加新的事，用 `propose_todo_tool` / `propose_todo_slots_tool`。
    存在的意义就一格：引擎查得出"哪条撞了哪节课"，可过去手里没有能改的那只手，
    只能反问"你说的是哪一条、想改到几点"（#84 之后实测 4 句全是这个形状）。

    走的是确定性分支**同一个** `build_retime_existing_proposal`（`auto_slot=True`）：
    避开课与已排的事、不会挑已经过去的点、挑不出来就说"排不下" —— 能由代码定死的都不交给模型。
    """
    if (op or "").strip() != "retime":
        # 只支持 retime。**不许静默当 retime 处理**：op=rename 悄悄改了时间是最坏的一种坏
        return json.dumps({"ok": False,
                           "error": f"op={op} 还没做，目前只支持 retime（改时间）"},
                          ensure_ascii=False)
    hint = (title or "").strip()
    if not hint:
        return json.dumps({"ok": False, "error": "至少要给 title（改哪一条）"},
                          ensure_ascii=False)
    parts = [hint]
    nd, ns, ne = (new_date or "").strip(), (new_start or "").strip(), (new_end or "").strip()
    if nd:
        parts.append("挪到 " + nd)
    if ns:
        parts.append(ns + ("-" + ne if ne else ""))
    # 名字走 `sem` 递进去：模型已经把名字当结构化参数传来了，那是全链路里最准的一份，
    # 不必再靠 `_pick_todo_name` 从拼出来的串里抠。**但这是保险、不是判据** —— 实测
    # `sem=None` 时抠出来的名字（含「游泳18001900」那种残渣）靠包容式匹配仍命中、
    # 卡上标题取的也是库里那条（`old.get("title")`），九例全过 ⇒ 别把这句写成"修掉了一个 bug"。
    res = build_retime_existing_proposal(" ".join(parts), {"title": hint},
                                        hint_title=hint, auto_slot=True)
    st = res.get("status")
    if st == "ok":
        return json.dumps({"__proposal__": res.get("proposal") or {}}, ensure_ascii=False)
    if st == "many":
        # 命中不止一条：照清单原样列出来、问"是下面哪一条"，然后停（与 done/remove 同一套规矩）
        return json.dumps({"ok": False, "need_pick": True,
                           "todos": [{"title": x.get("title"), "date": x.get("date"),
                                      "start": x.get("start"), "end": x.get("end")}
                                     for x in (res.get("todos") or [])][:6]},
                          ensure_ascii=False)
    if st == "empty":
        # 库里有东西、只是没对上名字 ⇒ 不许说"没有这条"（#83/#84 同一条口径），带上池子条数
        return json.dumps({"ok": False, "not_found": True,
                           "pool_n": res.get("pool_n") or 0}, ensure_ascii=False)
    # unclear：两种来源分开（#82 追记 §1.3 那套 `reason`）。auto_slot 之下 no_time 基本不该出现，
    # 真出现也只说"没排出合适的时段"，**不许反问学生要几点**。
    return json.dumps({"ok": False, "reason": res.get("reason") or "no_time",
                       "slot_label": res.get("slot_label") or ""}, ensure_ascii=False)


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
    # 删字（含手误写法）+ 口语"不要了"都算（后者只有"宽松"这档认，理由见词表注释）
    if not (any(w in t for w in _REMOVE_TODO_INTENT)
            or any(w in t for w in _REMOVE_TODO_SOFT)
            or _DEL_ANY_RE.search(t)):
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
    cap = len(candidates) if candidates else None
    # 「不对 / 不是这条 / 换一条」——他在卡上看到系统替他挑的那条，说不合意。
    # 这时**不许**硬着头皮往下走：把上一句的清单原样再端出来让他自己挑。
    #
    # ⚠️ 但有一种例外必须接住：**他一边否定一边说了是哪一条**
    #   （"不是这个，是交电费""不对，我要删的是晨读"）。这时候"重列一遍清单"
    #   等于没听见后半句——学生明明说了是哪条，系统却像失忆。
    #   所以先按他说的名字找一遍，找到了就照他的；找不到（光说"不对"）才重列。
    if candidates and _wants_rechoose(t):
        alt = resolve_todo_remove(t, candidates)
        if alt.get("status") == "ok":
            return alt
        return {"status": "many", "todos": list(candidates), "reason": "学生说不对，重新列"}
    idxs = _pick_todo_indexes(t, cap)
    if idxs and candidates:
        picked = []
        for idx in idxs:
            i = len(candidates) - 1 if idx == -1 else idx - 1
            if 0 <= i < len(candidates):
                picked.append(candidates[i])
        if len(picked) == 1:
            return {"status": "ok", "proposal": _todo_remove_proposal(picked[0]),
                    "todos": [picked[0]]}
        if len(picked) > 1:
            # 学生一次点了好几条：一次只删一条（不猜、不批量），列出来请他挑一条
            return {"status": "many", "todos": picked, "reason": "一次只能删一条"}
        return {"status": "unclear", "todos": list(candidates), "reason": "序号超出范围"}
    # ★「每个删一个 / 每组删一个 / 重复的删掉一个」——这是**去重**，不是全删。
    #   必须排在"都/全"那条**前面**：这句话里也带量词，谁先接就归谁。
    #   算出来是"每组多出来的那几条"，交给上层挂同一张批量卡（他点头才动）。
    if candidates and _wants_one_each(t):
        dup = _one_per_group(candidates)
        if dup:
            return {"status": "dedup", "todos": dup}
    # ★「都删了 / 全部删掉 / 一起删了吧 / 剩下的都不要了」——
    #   这在**接着上一句清单**说"你列的那几条我全都要删"，是指代，不是"没点名"。
    #   放在名字解析**之前**是有意的：这种句子里本来就抠不出名字
    #   （`_pick_todo_name("都删了") == ""`），先去解析名字只会掉进
    #   "没点名 → 端出整张日程"的老坑（学生截图：列了 2 条，回「都删了」冒出 11 条）。
    #   但先让一步：这句话若**明确对上某一条真待办的名字**，还是听名字的——
    #   别让一个"都"字把学生点名的意思盖掉。
    if candidates and _wants_all_candidates(t):
        named = resolve_todo_remove(t, candidates)
        if named.get("status") != "ok":
            return {"status": "all", "todos": list(candidates)}
    # ★ 把**上一句那份清单**当上下文传进去：学生正对着它回答，
    #   所以只在清单里找（别把清单外的东西捞回来），也让"等价组/精确命中"那两个
    #   证据判决在**这批候选**上生效——这才是"按上下文判断"。
    res = resolve_todo_remove(t, candidates)
    # 手上有清单、学生这句却既不是序号也没对上名字 → 回"没看明白是哪一条"（带上清单），
    # 绝不报"没找到"——清单还在记忆里，报"没找到"就是学生说的"记忆有问题"。
    if res.get("status") == "empty" and candidates:
        return {"status": "unclear", "todos": list(candidates)}
    return res


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


# ─────────────────────────────────────────────────────────────────────────────
# 标记完成（把某条**已存在的待办**置为 done）——跟删待办同一套"系统接管"思路：
#   只读解析 + 三档分支（ok / many / empty）+ 确认条，模型碰不到写库接口。
#   模型手里只有一个**只读**的 propose_todo_done，真正落库（update_todo_status）
#   只在学生点头之后由系统执行。这也是把原先那个"模型直调 update_todo_status"
#   撤掉、改成系统接管的落点。
# ─────────────────────────────────────────────────────────────────────────────
def resolve_todo_done(text: str) -> dict:
    """把"把游泳标记完成"解析成**具体哪一条待办**（只读，一个字节都不写）。

    返回形态跟 resolve_todo_remove 完全一致：
      {"status": "ok",        "proposal": {...}, "todos": [...]}  恰好一条，可以出确认条
      {"status": "many",      "todos": [...]}                     好几条，得让学生挑
      {"status": "empty",     "todos": []}                        没找到这条待办
      {"status": "unclear",   "todos": [...]}                     没说清是哪条
    """
    t = (text or "").strip()
    day = _pick_date(t)
    name = _pick_todo_name(t)

    pool = list_todos(day) if day else list_todos()
    # ★ "那个啥/上次那个事"这种**指代词**不算点名：他说的是"某个东西"，不是某个名字。
    #   算成点名 → 按名字一条都找不到 → 回"没找到"；不算点名 → 池子就是全部待办 →
    #   走"列清单问是哪一条"，比"没找到"有用得多（模糊口语的正确答法）。
    _VAGUE_NAMES = ("", "待办", "日程", "事项", "事", "事情", "东西", "玩意",
                    "那个", "这个", "啥", "什么", "一件", "那条", "这条")
    by_name = bool(name) and name not in _VAGUE_NAMES
    if by_name:
        hits = [x for x in pool if _todo_name_match(name, x.get("title", ""))]
        if not hits:
            hits = [x for x in list_todos() if _todo_name_match(name, x.get("title", ""))]
        if not hits:
            # 抠出来的残渣对不上任何一条名字 —— 动结式恰好会把名字拆开或倒过来说
            # （「健完身」对《健身》、「作业写完了」对《写作业》），拿字符串比是比不上的。
            # 这里**复用识别层那一个判据**（整句 + 候选名），不另抄一份匹配规则（红线 11b）：
            # 认得出这句话是"宣布办完"却找不到是哪条，等于把学生推回"没找到"。
            hits = [x for x in list_todos()
                    if _done_resultative((x.get("title") or "").strip(), t)]
        if not hits:
            return {"status": "empty", "todos": [], "day": day, "name": name}
        pool = hits

    # 同删待办：说了"撞时间的"就只留时间重叠的那几条（收窄后为空 → 如实说没找到）。
    if _wants_colliding(t):
        pool = _colliding_subset(pool)

    if not pool:
        return {"status": "empty", "todos": [], "day": day, "name": name}
    if len(pool) == 1:
        return {"status": "ok", "proposal": _todo_done_proposal(pool[0]), "todos": pool}
    return {"status": "many", "todos": pool, "day": day, "name": name}


def _todo_done_proposal(item: dict) -> dict:
    """把一条待办包成一张"标记完成"提案（人话：原样念一遍，等学生点头）。"""
    date = item.get("date", "")
    return {
        "kind": "todo_done",
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


def parse_done_todo(text: str) -> dict | None:
    """解析成一张标记完成确认卡；不是"恰好一条"就返回 None（交给上层列清单/如实说）。"""
    res = resolve_todo_done(text)
    return res.get("proposal") if res.get("status") == "ok" else None


def render_todo_done_list(todos: list, limit: int = _REMOVE_LIST_CAP) -> str:
    """把候选待办列成一段人话清单（人话：规格要的"把清单列出来"）。"""
    shown = todos[:limit]
    lines = [_todo_line(x, i + 1) for i, x in enumerate(shown)]
    if len(todos) > limit:
        lines.append(f"（还有 {len(todos) - limit} 条没列出来，你可以直接说名字。）")
    return "\n".join(lines)


def resolve_todo_done_reply(text: str, candidates: list | None = None) -> dict:
    """学生回答"是下面哪一条"时用它（人话：先按序号点名，不然就按名字解析）。"""
    t = (text or "").strip()
    cap = len(candidates) if candidates else None
    idxs = _pick_todo_indexes(t, cap)
    if idxs and candidates:
        picked = []
        for idx in idxs:
            i = len(candidates) - 1 if idx == -1 else idx - 1
            if 0 <= i < len(candidates):
                picked.append(candidates[i])
        if len(picked) == 1:
            return {"status": "ok", "proposal": _todo_done_proposal(picked[0]),
                    "todos": [picked[0]]}
        if len(picked) > 1:
            return {"status": "many", "todos": picked, "reason": "一次只能改一条"}
        return {"status": "unclear", "todos": list(candidates), "reason": "序号超出范围"}
    # 「都完成了 / 全标记完成」同删待办：接着上一句清单指代"这几条都要办"。
    if candidates and _wants_all_candidates(t):
        named = resolve_todo_done(t)
        if named.get("status") != "ok":
            return {"status": "all", "todos": list(candidates)}
    res = resolve_todo_done(t)
    # 同删待办：有清单时绝不报"没找到"
    if res.get("status") == "empty" and candidates:
        return {"status": "unclear", "todos": list(candidates)}
    return res


def propose_todo_done_tool(title: str = "", date: str = "", when: str = "") -> str:
    """给模型用的标记完成工具——**只读**，最多产出一张确认条，写不了任何东西。

    为什么要把写工具拿走：老版本工具箱里有个真能改状态的 update_todo_status，
    规矩靠提示层"必须先确认"约束着——可规矩写在提示里，模型口语一变就可能绕过它。
    所以照删待办的做法，把写入口收归系统：模型只能出提案，学生点确认条
    （或回一句"确认"）之后才由系统执行 update_todo_status(done)。
    """
    blob = " ".join(x for x in (title, date, when) if x)
    res = resolve_todo_done(blob)
    status = res.get("status")

    if status == "ok":
        prop = res["proposal"]
        return json.dumps({
            "__proposal__": prop,
            "human": (f"确认条已经挂在下面了：《{prop['title']}》{prop['date']}"
                      f"（{prop['weekday']}）{prop['start']}-{prop['end']}。"
                      f"你只要说『点下面的【确认完成】就标记完成，直接回「确认」也一样』，"
                      f"**禁止说已经标记完成了**。"),
        }, ensure_ascii=False)

    if status == "many":
        cand = [{"id": x.get("id"), "title": x.get("title"), "date": x.get("date"),
                 "start": x.get("start"), "end": x.get("end")} for x in res["todos"]]
        return json.dumps({
            "status": "ambiguous",
            "candidates": cand,
            "hint": ("命中不止一条，**不许猜**：把上面这些待办**原样列出来**"
                     "（标题 + 日期 + 时段），问学生'是下面哪一条'，然后停下来等。"
                     "一条都不许动，也不许说已经标记完成。"),
        }, ensure_ascii=False)

    return json.dumps({
        "status": "empty",
        "hint": ("没找到符合条件的待办。如实告诉学生'没找到'，"
                 "可以用 list_day_todos 看一下那天到底排了什么，"
                 "或者问学生是哪一天哪一条。**不许编一条出来标记**。"),
    }, ensure_ascii=False)


def _pick_part(text: str) -> str:
    """抠出"上午/下午/晚上"这类时段词（人话：学生说"周一早上加一节高数"，没给准点只给了半天的范围）。

    `_pick_span` 只认具体钟点（"8:00"），认不出"早上"；而学生口语里大量用
    "周一早上加一节高数课"这种——没给钟点，只给了个半天的范围。这一段早被
    `pick_free_slot` / `_part_order` 用上了，加课这里却漏接了，导致"加一节"被
    语义层当成加待办塞进日程。
    """
    t = text or ""
    if any(w in t for w in ("上午", "早上", "早晨", "一早")):
        return "上午"
    if "中午" in t:
        return "中午"
    if "下午" in t:
        return "下午"
    if any(w in t for w in ("晚上", "傍晚", "夜里", "今晚", "今夜")):
        return "晚上"
    return ""


# 每个时段词对应的"标准两节课段"候选（人话：学生说"早上"就是这两段里挑一段）。
# 排在前面的优先；下面选段时会跳过被现有课占了的那个，绝不硬塞进撞课的位置。
_PART_PERIODS = {
    "上午": [("08:00", "09:40"), ("10:00", "11:40")],   # 第1-2节 / 第3-4节
    "中午": [("12:00", "13:40")],
    "下午": [("14:00", "15:40"), ("16:00", "17:40")],   # 第5-6节 / 第7-8节
    "晚上": [("19:00", "20:40")],
}


def _slot_free(day: int, s: str, e: str) -> bool:
    """那天那个时段有没有被现有课占着（人话：想塞的整段跟已有课任何一处重叠都不行）。

    跟 propose_course_change 用的是同一套重叠判定（s<ce 且 e>cs），两边别写岔了。
    """
    s0, e0 = to_minutes(s), to_minutes(e)
    if s0 < 0 or e0 <= s0:
        return False
    for c in get_timetable():
        if int(c.get("day", 0)) != day:
            continue
        cs, ce = to_minutes(c.get("start", "")), to_minutes(c.get("end", ""))
        if cs >= 0 and ce > cs and s0 < ce and e0 > cs:
            return False
    return True


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
    part = _pick_part(t)
    course = _pick_course_name(t)
    # 1) 说清了星期 + 具体钟点（"周一 19:00 加一节高数"）→ 直接算
    if day is not None and begin:
        return True
    # 2) 星期 + 上午/下午/晚上（"周一早上加一节高数课"）——
    #    学生把"哪天、哪一半天"都说清了，具体钟点由系统先给个默认时段，
    #    卡片上他看一眼就能改，比丢给模型编"已经加上了"稳得多。
    #    只认带"课"字的（"高数课""体育课"），"加一节瑜伽"这类仍归待办。
    if day is not None and part and "课" in t:
        return True
    # 3) 有时钟、有课名、就是没说星期 → "想加课"成立，但让系统去问清楚星期
    if begin and course:
        return True
    # 4) 说了星期、也点名了课、就差个时间（"周六加一节高数课"）→ 想加课，问时间
    if day is not None and course and "课" in t:
        return True
    return False


def parse_add_course(text: str) -> dict | None:
    """把"周一加一节体育，体育馆，19:00 到 20:40"解析成一张加课确认卡。

    返回的是**提案**（含算好的新课表），本函数一个字节都不写库；
    学生点卡片上的确认、或回一句"确认"，才由系统真正落库。
    """
    t = (text or "").strip()
    day = _pick_day(t)
    start, end = _pick_span(t)
    part = _pick_part(t)
    # 学生说了"上午/下午/晚上"但没给具体钟点 → 在那个半天内挑一段**空着的**塞进去。
    # 不能盲塞第一个（会跟现有课撞、被 propose_course_change 打回，反而变成"缺时间"追问），
    # 要跳过被占的段；整段都排满了就老老实实返回 None，由上层问清具体钟点。
    if day is not None and not start and part:
        for dspan in _PART_PERIODS.get(part, []):
            if _slot_free(day, dspan[0], dspan[1]):
                start, end = dspan
                break
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
        guess = re.sub(r"周[" + _WD_CHARS + "](?![点时:：])", " ", guess)
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
    head = re.sub(r"周[" + _WD_CHARS + "](?![点时:：])", "", head)
    head = re.sub(r"\d{1,2}\s*[:：]\s*\d{2}.*$", "", head)
    head = re.sub(r"[^\w一-龥]+", " ", head)
    for w in _ADD_INTENT + ("加一节", "加一门", "加门", "课表", "课程", "课", "的",
                            # 时段词也要剥掉：学生说"周一早上加一节高数课"，
                            # 不剥的话课名会被抠成"早上高数"，整句话还会被误当成上课地点。
                            "早上", "上午", "下午", "晚上", "中午", "傍晚",
                            "早晨", "一早", "夜里", "今晚", "今夜"):
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
    # 整句"加课"指令漏进来时（没给地点、只说了"周一早上加一节高数课"），
    # 这一截里带着"加/课/周/时段词"，显然不是地点——直接否掉，别让它顶替地点。
    if any(w in seg for w in ("加", "课", "周", "早上", "上午", "下午",
                              "晚上", "中午", "傍晚", "早晨", "一早",
                              "夜里", "今晚", "今夜")):
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
    """规划助手的基础提示。

    ⚠️ **这里不再自己写今天的日期** —— 基准日期由 `app.main._now_block()` 统一注入，
    每个模块都拼得到（原先只有这一份有，于是问答栏问「今天几号」模型就现编一个）。
    同一个事实两处各写一遍，迟早对不齐（红线 11b）。
    """
    return """你是校园时间管家的时间规划助手，负责帮学生把待办安排进真实的空档里。

【你的工作流程 —— 必须照做】
1. 学生提出一件要做的事（比如"我要复习高数"），先用 find_free_slots 查空档；
   如果学生没说哪天，就优先看今天和明后两天。
2. 挑出 **2-3 个候选时间段**，然后**必须调用 propose_slots 工具**，
   把这些候选以结构化形式交给前端——学生会在界面上看到可勾选的卡片。
   ⛔ **只在文字里罗列时间段等于什么都没给学生**：屏幕上不会出现任何可点的东西。
3. **调用成功之后**，才可以在文字里补一句「点一下想要的时间就行」，然后停下来等学生选。
   ⛔ **没调用 propose_slots 就一个字都不许提「勾选 / 打勾 / 点【…】 / 下面这条卡」** ——
   这一轮屏幕上没有卡，那句话就是在指一个不存在的东西（学生原话：「瞎编一个弹窗」）。
   这种情况下要么**先把卡出出来**，要么**改成问一句**（「要我把游泳挪到 16:00 吗？」）。
4. **然后停在这里等学生**。学生点头（点确认条 / 回"就用这个"）之后，
   **由系统落库**——你手上没有任何写入工具，这是设计如此，不是办不到。
   ⛔ 学生确认前别说"已经加上了"；确认后也别承诺"我来帮你写入"。
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
  直接处理他本人的待办/课表提案，**不需要上报驿站，也绝不说"权限不足"**。
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
  —— 换成"让驿站看一下/我帮你反馈给驿站"同样禁止，挡箭牌换个名字还是挡箭牌。
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
4. **标记完成（把某条待办置为已完成）**：跟删待办是同一个思路——**只读出提案，
   写状态（update_todo_status）只在学生点头之后由系统执行**，你手里只有一个
   只读的 `propose_todo_done`，既没有改状态的接口、也绝不允许自己宣称"已经标记完成"。
   ⛔ 三条红线，跟删待办对称：
      · **你没有任何写状态接口**：工具箱里能改待办状态的写入口一个都没有（这是刻意的）。
        学生没点头就说"已经标记完成了"是骗人——状态还是 planned，刷新一看就露馅。
      · **不许只说"我需要你确认一下"却不列清单**：学生不知道你在指哪一条，
        这种确认等于瞎确认。要标记就必须把那条**原样念出来**（标题 + 日期 + 时段）。
      · **不许猜**：学生只说"标记完成明天那条"而明天有好几条时，
        必须把候选**原样列出来**问"是下面哪一条"，**列完就停**，一条都不许动。
   三步走（跟删待办一一对应）：① 学生点名了唯一一条 → 调 propose_todo_done 出确认条，
   然后停在"等你点确认完成"；② 命中好几条 → 用 propose_todo_done 拿到的候选清单
   原样列出来问是哪一条，**列完就停**；③ 没找到那条待办 → 如实说"没找到"，
   **不许编一条出来标记**。一句话记住：**标记完成也是不可逆的展示动作，
   宁可多问一句，也别替学生做主。**
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
- **你没有任何写入工具**：`add_todo` / `update_todo_status` 这类落库函数不在工具箱里。
  学生点头之后由系统执行写入——这不是"办不到"，落库本来就不是你的活
- **propose_clear_timetable()：清空整张课表**——只出确认弹窗，纯只读，写完就停手
- **propose_course_change(op, day, ...)：删课/加课/改单节课的首选**——服务端算好新课表出确认卡
- **propose_timetable_change(courses_json, change_summary)：整表重排/文件导入用**——
  把调整后的完整课表（JSON 数组）交给前端渲染成「确认修改」卡片，学生点确认后系统写入。
  **注意：写入不经过你**，所以卡片确认后不用（也不能）再调任何写入工具。
- **propose_todo_remove(title, date)：删待办**——只出确认条，删不了任何东西；
  命中多条时返回候选清单，照着列出来问学生是哪一条。
- **propose_todo_done(title, date)：标记完成**——只出确认条，改不了任何状态；
  命中多条时返回候选清单，照着列出来问学生是哪一条，**绝不许说已经标记完成**。
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
                "start、end、可选 note。学生没确认前不要声称已经排好（落库由系统做）。"
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
            description="列出**某一天**已经安排的待办事项。"
                        "只看这一天；要查一段（这周 / 这个月 / 周三到周五）用 `list_todos_range`，"
                        "别一天一天调这把。",
            parameters={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "日期，格式如 2026-09-25"},
                },
                "required": ["date"],
            },
            func=list_day_todos,
        ),
        # ★ #63：只补**一个粒度**，理由是"底层能力现成、只是没切出这一段"，
        #   不是"多给模型一只手"（那是 #61-A 已经做完的事）。参数就 start/end 两个：
        #   参数每多一个（按状态筛、按标题搜），模型选错的概率就高一档。
        "list_todos_range": Tool(
            name="list_todos_range",
            description=(
                "查**一段时间内**已安排的待办（含起止那两天），一条待办返回一行，"
                "行里的 `dates` 是它在这段里出现的具体日子（重复的事不会变成好几行）。"
                "学生问「这周 / 这个月有什么安排」「周三到周五有没有事」时用**这一个**，"
                "不要一天一天调 `list_day_todos`。"
                "入参 start、end 都是日期，格式如 2026-10-25；说反了会自动换过来。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "起始日期，如 2026-10-25"},
                    "end": {"type": "string", "description": "结束日期（含），如 2026-10-31"},
                },
                "required": ["start", "end"],
            },
            func=list_todos_range,
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
        # ★ 这里**没有** add_todo_tool。
        #   它是全项目唯一还挂在模型工具箱里的真写库函数（AST 扫过 6 个模块 29 个工具，只有它）。
        #   老版本的"防线"是工具描述里那句"必须等学生明确确认之后才能调用"——
        #   规矩写在提示里，模型口语一变就可能绕过去；同批收归系统的还有
        #   update_todo_status（见 propose_todo_done_tool 的 docstring），当时唯独漏了它。
        #   函数本体保留：确认卡路径 planner.py:1037 是**系统侧**在调用，写入照旧走那条。
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
        "propose_todo_done": Tool(
            name="propose_todo_done",
            description=(
                "**标记完成唯一能用的工具**（学生说'把周二那条游泳标记完成''勾掉交电费'时用）。"
                "它只生成一张【确认完成】确认条，**一个字节都不改**——"
                "你也没有任何写状态接口可用。title 传学生说的名字，date 传'2026-09-28'或'明天'，"
                "两个都不确定就都留空（工具会告诉你命中了哪些）。"
                "⚠️ 命中不止一条时它不会替你挑，会返回候选清单——"
                "**照清单原样列给学生、问'是下面哪一条'，然后停下来等**，"
                "⛔ 不许猜哪一条、更不许说'已经标记完成了'。"
                "命中唯一时确认条自动挂出，你只需说'点【确认完成】才真的标记'。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待办标题或学生说的名字，如 游泳"},
                    "date": {"type": "string", "description": "日期，格式如 2026-09-25，或 明天/周一"},
                    "when": {"type": "string", "description": "可选，补充时间描述"},
                },
                "required": [],
            },
            func=propose_todo_done_tool,
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
    # ★ #86：排程这一族里一直缺"改"这一格（课表侧有 propose_course_change，待办侧一只改的都没有）
    picked["propose_todo_change"] = propose_todo_change_tool_tool()
    return picked


def propose_todo_change_tool_tool() -> Tool:
    """把 `propose_todo_change_tool` 包成 Agent 工具（#86：引擎第一只能改待办的手）。"""
    return Tool(
        name="propose_todo_change",
        description=(
            "**改一条已经在日程里的待办的时间**（改期）。只出提案，不写库。\n"
            "title 传学生说的那条待办名（必填）；new_date / new_start / new_end 只在学生**说了**新时间时才填，"
            "格式 '2026-10-31' / '16:00'。\n"
            "⚠️ 学生只说「撞了/换一个时间」、没说新时间时，**这三样一个都别填** —— "
            "系统自己挑一段排得下的（避开课和已排的事、不会挑已经过去的点），"
            "⛔ 不要反问『你想改到几点』（学生投诉原话：「没有帮我想时间，是我问了才说的」）。\n"
            "⛔ **它不会新建待办**：学生要往日程里加新的事，用 propose_todo_tool / propose_todo_slots_tool。\n"
            "⚠️ 它返回 need_pick + 候选清单时（命中不止一条）—— **照清单原样列给学生、问「是下面哪一条」，然后停**，"
            "不许猜一条，更不许说『已经改好了』。\n"
            "调用成功界面上会挂出【确认加入】按钮，学生点了才真改（旧的那段被挪走）；"
            "没调用这个工具就不要说『已经帮你改好时间』。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "description": "只支持 retime（改时间）；不填即 retime", "default": "retime"},
                "title": {"type": "string", "description": "要改的那条待办名（学生说的那个）"},
                "new_date": {"type": "string",
                             "description": "学生说了新日子才填：'2026-10-31' 或 '周五'；可空",
                             "default": ""},
                "new_start": {"type": "string", "description": "学生说了新钟点才填：'18:00'；可空",
                              "default": ""},
                "new_end": {"type": "string", "description": "学生说了结束钟点才填：'19:00'；可空",
                            "default": ""},
            },
            "required": ["title"],
        },
        func=propose_todo_change_tool,
    )


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
    # ★ 统一否决（同 propose_todo_tool）：模型直调这条路也要过同一道关。
    from app.agent.intent import looks_like_question
    if looks_like_question(blob) or _GO_QUESTION_RE.search(blob):
        return json.dumps({
            "error": "这像是在问事/问办法，不是要加待办",
            "hint": ("学生这句是在提问（问路、问流程、问时间），不该出「时间你自己定」二分卡。"
                     "**走问答/知识库去答他**，别替他排日程。"),
            "got": blob,
        }, ensure_ascii=False)
    _rej = period_reject(blob)
    if _rej:
        return _rej
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
    # ★ 统一否决（同 propose_todo_tool）：模型直调这条路也要过同一道关。
    from app.agent.intent import looks_like_question
    if looks_like_question(blob) or _GO_QUESTION_RE.search(blob):
        return json.dumps({
            "error": "这像是在问事/问办法，不是要加待办",
            "hint": ("学生这句是在提问（问路、问流程、问时间），不该出候选时段卡。"
                     "**走问答/知识库去答他**，别替他排日程；"
                     "真要加待办，等他把「要加哪件事」说清楚再说。"),
            "got": blob,
        }, ensure_ascii=False)
    _rej = period_reject(blob)
    if _rej:
        return _rej
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
