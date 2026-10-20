"""旗舰模块：课表时间规划（人话：这是项目的"门面"功能，专门展示 Agent 是怎么一步步推理的）。

它给 Agent 五个工具：
  1) get_day_courses  —— 查某天上了哪些课（**只查本人周表**，见文件里 45-A 那段）
  2) plan_task        —— 根据空闲，自动排出一个学习计划
  3) propose_todo_mode_tool  —— 先问「时间你自己定 / 我帮你挑」那张二选一卡（只读，不写库）
  4) propose_todo_slots_tool —— 学生选了"我自己定"之后，把几段候选空档摊成一张**能打勾**的卡片
  5) propose_todo_tool —— 已经定了准点时，出一张**单条**确认条（只读，不写库）

（以前这里还写着第 6 个 `find_free_slots` —— 它和 planner 那个同名工具入参不一样却注册在
 同一张表上，赢的是 planner 那份，于是提示教模型传"周三"、执行要的是"2026-10-20"。
 45-A② 把本模块这份**下了架**，查空档只留 planner 那一份真数据。）

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
from app.agent.tools import Tool
from app.modules.planner import (
    propose_todo_mode_tool, propose_todo_mode_tool_tool,
    propose_todo_slots_tool, propose_todo_slots_tool_tool,
    propose_todo_tool, propose_todo_tool_tool,
)
from app.store import get_timetable

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
- **学生要加一件事、还没定时间时，直接替他敲定一段，不要再问"你自己定还是我帮你挑"**
  （学生原话：「**不需要先问，直接去安排，重复的确认太麻烦，直接敲定结果，主打效率**」）。
  「周四加个健身」「帮我加个游泳」这类话（说了哪天就只看那天；没说就从今天往后找
  第一个排得下的）：先用 find_free_slots 查空档（⚠️ 它的入参是**日期**，如 `2026-10-20`，
  **不是星期** —— 说"周三"要先换算成这一周的哪一天），然后**立刻调用 propose_todo_tool
  出一张单条确认条**（时长按他说的来，「大概一个小时」就是 60 分钟，别一律排 90），
  卡片上写清为什么排在这儿，然后停下来等他点【确认加入】。
  ⛔ 不要再出二选一卡问"怎么定"，也不要反问"几点到几点"——他嫌点不合适会自己报。
  他想自己挑（说"我自己挑/列几个"）→ 才用 propose_todo_slots_tool 一次列 2~4 段。
  那天真排不下 → 如实说"这天满了"，别硬凑。
- ⛔ **排任何事项都用待办提案工具**——游泳、吃饭、健身这类生活事项也一样，
  用 propose_todo_tool / propose_todo_slots_tool，**不要拿排学习计划/排课表的工具凑**
  （实测用错工具，排出来的时段跟学生说的对不上，学生回「确认」你还得道歉重来）。
  ⛔ **不许拿他那半句话当代办名挂出去**（学生原话：「要识别啥才是真的事情，
  不是随便拿那一句话就去当代办加入日程了」）——判不出事名（只有时长、只有"帮我安排"、
  只有"你帮我找个时间"）就只问"要安排什么事"，别出卡。

【场景边界 —— 学生自主 vs 工单上报（重要）】
- 学生的日程操作（加待办、增减课、查空档、改时间）**全部默认学生自主操作**：
  直接处理他本人的待办/课表提案，**不需要上报驿站，也绝不说"权限不足"**。
- 只有学生**主动**说要找管理员协助（快递丢失、外卖遗失并要求上报）才走工单流程，
  且要他确认后才推送驿站；他没主动提就只在对话里聊。
- ⛔ 你永远不直接写数据库：所有增删改由后端在学生点【确认】之后执行，
  严禁谎称"已经改好了/已经加上了"。
- 确认写入成功后，前端会自动刷新课表/待办面板，不用提醒学生手动刷新。
- 说话简洁自然，直接给方案，去掉多余引导提问。

【学生说"没看见/日程里没有 X" —— 你不编原因，也不打发他】
系统会先真查库（查到了告诉他看哪儿，没查到把确认条补上），真落到你这儿时
只做两件事：问清是哪一条，或按他最近说过的事直接出提案卡。
⛔ 禁止说"系统推送出了问题""得让管理端看一下""我帮你反馈给管理端"（换成驿站也一样禁）——
  你看不到他的屏幕，推送不归你管，这些全是编的；确认条没弹出来的正确做法是补一张。
⛔ 不要让他"二选一：再刷新一次 / 反馈管理端"，也不要建议退出重进、下拉刷新。"""


def _to_min(t: str) -> int:
    """把 '08:00' 变成分钟数 480（人话：方便做时间加减和比较）。"""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _fmt(minute: int) -> str:
    """把分钟数变回 '08:00' 这种字符串。"""
    return f"{minute // 60:02d}:{minute % 60:02d}"


DAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 个人周表里 `day` 存的是 1~7（1=周一），这里把话里的"周三"翻译成那个数。
_DAY_INDEX = {d: i + 1 for i, d in enumerate(DAYS)}

# ★ 45-A（2026-10-20，P0）：这个模块**以前**读的是 `app/data/courses.json` ——
# 一份写死 `"student": "示例同学（演示数据）" / "2026 春季学期"` 的**公共演示课表**，
# 跟"谁在问"完全无关。实测 `admin2` 的周表是空的，问「今天有什么课」却被答
# 「08:00~09:40 线性代数 · 孙教授 · 教三-202 / 10:00~11:40 体育 · 周老师 · 体育馆」——
# 逐字就是那份文件的周二两行，而这条回答现在还存在 `student/admin2/sessions.json` 里。
# 现在**只有一个课表来源**：`store.get_timetable()`（按 cookie 分人的那份）。
# `courses.json` 从此只是**测试种子**（`tests/_e2e_env.py:demo_courses_from_json` 还读它），
# 不再在任何一条产品路径上被打开。
# ⚠️ 个人周表的字段是 {day, start, end, course, location}，**没有 teacher** ——
# 所以输出一律不带老师名。别为了"保持原来的格式"去给 `timetable.json` 加字段，
# 那是改数据结构，不在这一单。


def _courses_on(day: str) -> list[dict]:
    """某一天属于**当前这名学生**的课（人话：只问本人周表，不去翻演示文件）。"""
    wd = _DAY_INDEX.get((day or "").strip())
    if not wd:
        return []
    out = [c for c in get_timetable() if int(c.get("day") or 0) == wd]
    out.sort(key=lambda c: _to_min(c.get("start") or "00:00"))
    return out


def _empty_day(day: str) -> str:
    """空课表要**分两种话说**。
    旧那句「{day} 没有排课，整天都是空闲的」把"你还没导入课表"说成"你这天很闲"，
    对空库的新账号（#35 撤了登录播种之后这是常态）是彻底的误导。
    """
    if not get_timetable():
        return (f"你的周表还是空的（没导入过课表），所以{day}有没有课上我看不出来。"
                "在日程面板点【上传课表】导一份，我再帮你排。")
    return f"{day}没排课。"


def _free_blocks(day: str) -> list[tuple[int, int]]:
    """某天 08:00~22:00 里的空档（本地算法，**不跨模块调 planner**）。

    为什么本地还留一份：`plan_task` 要"哪天有多少空、能塞下几小时"，
    而 planner 那份 `find_free_slots(date)` 要的是**日期**、还算进待办占用 —— 两件事。
    ⚠️ 这不是"第二套课表"：两边的数据源都是 `get_timetable()`，只是算法不同。
    """
    cursor, free = _to_min("08:00"), []
    for c in _courses_on(day):
        s, e = _to_min(c.get("start") or "00:00"), _to_min(c.get("end") or "00:00")
        if s > cursor:
            free.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < _to_min("22:00"):
        free.append((cursor, _to_min("22:00")))
    return free


def get_day_courses(day: str) -> str:
    """工具①：查某一天**这个学生**的全部课程。"""
    courses = _courses_on(day)
    if not courses:
        return _empty_day(day)
    lines = [f"{day} 共 {len(courses)} 门课："]
    for c in courses:
        loc = str(c.get("location") or "").strip()
        lines.append(f"- {c.get('start')}~{c.get('end')} {c.get('course') or '（没写课名）'}"
                     + (f"（{loc}）" if loc else ""))
    return "\n".join(lines)


def plan_task(task_name: str, need_hours: float, prefer_day: str = "") -> str:
    """工具②：根据**本人**周表的空闲，为某个任务排出学习计划。"""
    if not get_timetable():
        # 空周表 ≠ 一周全是空档。以前这里会兴高采烈排出「周一 08:00~11:00 做《复习》」，
        # 而那个"空"其实来自一份**没导入过的**周表 —— 跟 `_empty_day` 是同一个误导。
        return ("你的周表还是空的（没导入过课表），所以我没法按课表找空档。"
                "先在日程面板点【上传课表】导一份；"
                "或者直接说「周一下午两点到四点复习高数」，我按这句话给你出确认条。")
    # 优先按指定星期排；没指定就从周一到周五找空
    days = [prefer_day] if prefer_day in DAYS else DAYS[:5]

    plan: list[str] = []
    remaining = float(need_hours)
    for day in days:
        if remaining <= 0:
            break
        for s, e in _free_blocks(day):
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
            description="查询某一天**这个学生自己**的课（时间/课程/地点）。入参 day 如 '周一'。",
            parameters={
                "type": "object",
                "properties": {"day": {"type": "string", "description": "星期，如 周一"}},
                "required": ["day"],
            },
            func=get_day_courses,
        ),
        # ⛔ 这里**故意不放** `find_free_slots`（45-A②）：
        # 以前 schedule 与 planner 各注册了一个同名工具，入参还不一样
        # （schedule 版收 `day="周三"`、planner 版收 `date="2026-10-20"`），
        # `main.py` 里 `tools.update(scheduling_tools())` 在后 ⇒ **planner 那份赢了**，
        # 而本模块的提示词教模型传 `day` ⇒ 学生问「我这周什么时候有空」实打实拿到过
        # 「周三 不是合法日期，请用 2026-09-25 这种格式」，模型只能道歉。
        # 现在查空档只有 planner 那一份（真数据、按日期），它的说明书由 planner 维护；
        # 要恢复"两份同名、各写一半"就是把这个坑再挖回来 —— 护栏 `test_72` F 批钉着"两边没有同名工具"。
        "plan_task": Tool(
            name="plan_task",
            description="根据**本人周表**的空闲，为某个任务排出学习计划。task_name 任务名，need_hours 需要小时数，prefer_day 可指定优先星期（可选）。",
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
