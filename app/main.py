"""总入口（人话：FastAPI 把上面所有能力接到网页接口上，是整个服务的"大门"）。

请求进来后的完整链路：
  前端说话 → /api/chat → router 判断意图 → 取对应模块的工具箱+系统提示
          → engine 跑 ReAct 循环（必要时调工具）→ 返回回答 + 思考轨迹
"""
import datetime
import json
import os
import re
import uuid
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.engine import AgentEngine
from app.agent.intent import (
    INTENT_ADD_TODO, INTENT_CLEAR_TIMETABLE, INTENT_FIND_CARD,
    INTENT_LIST_TODO, INTENT_REMOVE_TODO, INTENT_RETIME,
    remember, to_canonical, understand,
)
from app.agent.pending import (
    clear_pending, is_confirmation, peek_pending, save_pending, take_pending,
)
from app.agent.router import Router
from app.config import DEBUG, check_config, get_llm_config
from app.llm.client import DeepSeekClient, LLMError
from app.upload import UploadError, extract_text, ext_supported, unsupported_reason
from app.modules.schedule import SYSTEM_PROMPT as SCHEDULE_PROMPT, build_tools as schedule_tools
from app.store import (
    add_message, list_messages, add_notice, list_notices,
    list_kb, add_kb_entry, update_kb_entry, delete_kb_entry,
    get_persona, set_persona,
    get_settings, set_settings,
    MAX_UPLOAD_BYTES,
    save_upload, list_uploads, get_upload, get_upload_meta, get_upload_raw_path,
    get_conversation, append_conversation, clear_conversation,
    get_timetable_data, list_todos, list_todos_in_month,
    add_todo, update_todo, delete_todo, get_todo, save_timetable,
    add_workorder, list_workorders, update_workorder_status,
)
from app.modules.faq import SYSTEM_PROMPT as FAQ_PROMPT, build_tools as faq_tools
from app.modules.express import SYSTEM_PROMPT as EXPRESS_PROMPT, build_tools as express_tools
from app.modules.takeout import SYSTEM_PROMPT as TAKEOUT_PROMPT, build_tools as takeout_tools
# 学生端卡片视图的只读数据源（人话：给面板展示用的，不参与 AI 对话）
from app.modules.express import packages_view as express_packages_view
from app.modules.takeout import orders_view as takeout_orders_view
from app.modules.files import build_tools as files_tools
from app.modules.station import build_system_prompt as STATION_PROMPT, build_tools as station_tools
# planner 也是"可调用提示"：每次对话都要把**今天的日期**动态拼进去，
# 否则学生说"明天"，AI 根本算不出是哪一天
from app.modules.planner import (
    add_todo_tool,
    auto_todo_proposal,
    build_system_prompt as PLANNER_PROMPT,
    build_tools as planner_tools,
    build_scheduling_tools as scheduling_tools,
    format_minutes,
    mock_planner,
    parse_add_course,
    parse_add_todo,
    parse_remove_course,
    parse_slot_text,
    parse_todo_from_reply,
    parse_mode_answer,
    plan_todo_slot,
    mode_to_card,
    claims_proposal_sent,
    missing_thing_of,
    _name_match as _planner_name_match,
    DAY_NAMES,
    _weekday_name,
    has_concrete_span,
    render_todo_remove_list,
    rescue_proposal_from_reply,
    resolve_todo_remove,
    resolve_todo_remove_reply,
    slot_is_free,
    todo_mode_proposal,
    todo_slots_proposal,
    wants_remove_todo,
    wants_remove_todo_loose,
    is_add_todo_answer,
    is_add_todo_followup,
    is_mode_answer,
    is_slot_answer,
    pick_todo_missing,
    retime_todo_proposal,
    todo_missing_advice,
    todo_title_of,
    wants_add_course,
    wants_add_todo,
    wants_clear_timetable,
    wants_list_todo,
    wants_remove_course,
    wants_retime_todo,
)
from app.modules.student_persona import STUDENT_PERSONAS, persona_block, is_valid

# 项目根目录（本文件在 app/ 下，根目录是上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")

app = FastAPI(title="校园时间管家 CampusTime", version="0.1.0")
check_config()

# —— 模块注册表（人话：每个模块在这里登记一下，路由命中后就能取用）——
# 学生端四个模块：我的日程 / 校园问答 / 快递 / 外卖。以后加驿站侧、管理台照此加一行。
REGISTRY: dict[str, tuple[Any, Any]] = {
    "schedule": (SCHEDULE_PROMPT, schedule_tools),
    "faq": (FAQ_PROMPT, faq_tools),
    "express": (EXPRESS_PROMPT, express_tools),
    "takeout": (TAKEOUT_PROMPT, takeout_tools),
    # station 的提示是"可调用的"：每次对话时动态拼入管理员配置的客服人格
    "station": (STATION_PROMPT, station_tools),
    # planner 学生个人日程：提示里动态注入今天日期，才能听懂"明天/后天"
    "planner": (PLANNER_PROMPT, planner_tools),
}
# 没命中任何模块时的兜底提示
DEFAULT_PROMPT = "你是校园时间管家，一个友好、靠谱的校园 AI 助手。"

# 统一追加给每个模块的"读资料"能力说明。
# 放在这里而不是逐个写进模块的 SYSTEM_PROMPT，是为了改一处所有模块都生效。
FILE_HINT = """
【你还具备读取资料的能力】
用户可能会上传文件（课表、通知、名单、表格等）。当用户的提问看起来需要参考某份文件，
或者你不确定有没有相关资料时：
1. 先调用 list_uploaded_files 看看手头有哪些文件；
2. 再用 read_uploaded_file 读取其中相关的文件内容；
3. 基于读到的**真实内容**作答——文件里没有的信息千万不要编造，宁可直接说没找到。
"""

# 学生端 AI 助手的统一人设规矩（学生要求写进人设的四条，全局生效）。
# 与 FILE_HINT 同理：改一处、所有模块都生效，不用去每个模块重复写。
ASSISTANT_RULES = """
【你的人设规矩 —— 所有场景都必须遵守】
1. 输出要求：回答简洁、贴合学生使用场景，不冗长废话；遇到乱码、损坏文件、异常输入，
   友好提示学生重试或换一份文件，**禁止把系统内部报错、堆栈、字段名直接暴露给学生**；
   绝不输出暴力、低俗、煽动类内容。
2. 上报工单：不是学生的每句话都上报管理端。**只有**学生提出快递丢失、外卖遗失这类
   需要管理员协助处理的问题时，才走这个流程，且必须两步：
     ① 先问一句「要不要上报给管理端？」；
     ② 学生明确确认上报，工单才推送到管理端收件箱。
   学生说不上报（或没表态）→ 只在学生这段对话里记着，**绝不推送管理端**。
   反过来：**增减课程、增删日常待办属于学生本人自主管理自己的日程，
   这类请求一律不上报管理端**，也别提"要先上报"这种话。
3. 个人数据隔离：每个学生的数据相互独立，你的操作只影响当前学生。你可以生成
   **增减课程、增删待办的变更提案**，但学生确认之前数据库绝不会变；
   修改课表走 propose_course_change / propose_timetable_change 提案
   （学生点确认条上的【确认】，或在聊天框回"确认添加/确认删除"这类确认文字，
   都由**系统**执行入库，不经过你）；
   add_todo_tool 等写入工具必须等学生点头后才能调用。
   ⛔ 日程修改只走提案这一条路。学生点确认条上的【确认】是优先方式，
      在聊天框回确认类文字是备选方式，两者都由**系统**写入。
   ⛔ 禁止说"自己办不到、权限不够、需要先上报管理端"这类话——
      增删课程和待办是学生自主管理个人日程，出提案的工具本来就在工具箱里；
      也禁止让学生去界面上手动删课（界面上没有那个入口，
      说了就是把学生指到死路，学生只会回一句"我怎么找不到"）。
   ⛔ 学生确认之前，禁止说"已经加上了/已经删掉了/已经改好了"——
      没入库就是没入库，谎报比不做更伤信任。
   ⛔ 想加课、加待办、改课表时：你手上**一个写入工具都没有**（真写库的是后端的确认条）。
      所以只能说"说一下时间/哪天，我算好给你出确认条"，禁止用"搞定／没问题／
      已经正式写进你的待办啦／刷新一下就能看到"这类话暗示已完成——
      说这话的时候数据库里空空如也，学生一刷新就发现被骗（这条被投诉过）。
   ⛔ **学生要加一件事、还没定时间时，第一步不是列时间、也不是问他几点。**
      学生原话：「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
      不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定），用户选择后……**」
      所以：
      · 先用 **propose_todo_mode_tool(title, when)** 出那张**二选一卡**（"你自己定" /
        "你帮我挑"），挂出来，**停下来等他点**。别跳过这张卡直接甩时间段——
        对"我随便、你看着办"的学生，先摊三个点等于把决定权又推回给他。
      · 他点「我自己定」→ 再用 propose_todo_slots_tool 列 2~4 段让他打勾。
      · 他点「你帮我挑」→ 由系统把时间规划出来（单条、并写清"为什么排在这儿"）。
        时长按他说的来（「大概一个小时」就是 60 分钟，别一律排 90 分钟）。
      ⛔ 不要反问他"你想几点到几点"——他不知道哪会儿空着才来问你（投诉原话：
      「**没有帮我想时间，是我问了才说的**」）。
      只有两种情况才去问他：① 那天/那几天真的排不进（如实说"满了、换个日子"）；
      ② 他连"要加什么事"都没说清（那就问**要加什么事**，⛔ 别问"几点到几点"）。
      例外：**学生自己报了准点**（"周四下午两点到三点"）→ 用 propose_todo_tool
      出单条确认条就够，别再问他。
   ⛔ **不许拿学生那半句话当代办名挂出去。** 学生原话：
      「**要识别啥才是真的事情，不是随便拿那一句话就去当代办加入日程了**」。
      实测「大概一个小时帮我安排时间」被抠成「大概小时帮我时间」还挂出了候选卡，
      「周三12点到2点我要去吃自助餐，帮我添加」被抠成「去吃自助餐帮我」——
      前者根本不是一件事（只有时长），后者是个残渣名字。
      判不出事名（只剩时长、"帮我安排"这类动作词）→ **只问"要安排什么事"**，不出卡。
   ⛔ 学生报出"哪天/几点"要往日程里加事时，**一定要调工具把确认条挂出来**。
      光在文字里写「- 任务：健身 - 时间：周四 15:40~17:10 - 点【确认】就入库了」
      等于没出条——界面上一个按钮都没有，学生回"确认"时系统也不知道他在确认什么
      （这一幕被截屏投诉过：**「没有确认」**）。
      换句话说：**确认条只有挂出来，才叫"提案发给你了"。**
   ⛔ **删待办**（"把周二那条游泳的待办删掉""取消交电费""删掉明天那条"）
      跟删课是同一套规矩：**先列清楚，再动手，学生点头才算数**。三条红线：
      · 学生没点头就删、或先宣布"已经删了"——都是骗人：数据还在库里，
        而且删错了**找不回来**。你手里能用的只有 propose_todo_remove（只读，只出确认条），
        **没有任何删除接口**。
      · 不许只说"我需要你确认一下"却**不列清单**：学生不知道你在指哪一条，
        这种确认等于瞎确认。要删就必须把那条**原样念出来**（标题 + 日期 + 时段）。
      · 不许猜：学生只说"删掉明天那条"而那天有好几条时，把候选**原样列出来**
        问"是下面哪一条"，**列完就停**，一条都不许删。
      学生说的是课（"去掉周二的高数"）→ 那归课表，调 propose_course_change，别拿待办去套。
4. 自我定位：你是辅助工具，不是决策者。所有对用户数据的改动，决定权永远在学生本人；
   学生上传的文件（docx/xlsx 等）只读取内容，**禁止修改或覆盖源文件**。
"""

router = Router()


def _parse_clear_proposal() -> dict | None:
    """跑一次只读的清空提案，取出其中要交给前端的提案（人话：AI 只出方案，不碰数据）。

    返回 None 表示周表本来就是空的（没什么可清），交回模型按老规矩回话。
    """
    from app.modules.planner import propose_clear_timetable

    raw = propose_clear_timetable()
    try:
        prop = (json.loads(raw) or {}).get("__proposal__")
    except Exception:
        prop = None
    return prop if isinstance(prop, dict) else None


def _execute_clear(session_id: str) -> dict | None:
    """**执行**一次清空周表（人话：把课表清空的那一刻落库）。

    为什么抽成一个函数：清空有两个入口——
      · 优先方式：学生在确认条上点【确认】（/api/timetable/clear）
      · 备选方式：学生在聊天框回"确认删除"这类确认文字（确认分支）
    两处必须走**同一段**代码，否则一个改了另一个忘改，就会出现
    "点按钮能删、回确认删不掉"这种一半灵的怪事。

    :return: {"removed": 清掉几门, "summary": 提案摘要}；暂存里没有清空提案时返回 None
    """
    from app.modules.planner import get_timetable
    entry = take_pending(session_id) or {}
    proposals = [o for o in (entry.get("options") or [])
                 if isinstance(o, dict) and o.get("kind") == "timetable_clear"]
    if not proposals:
        return None
    before = get_timetable()
    save_timetable([])   # 只动当前这名学生本人的周表
    return {
        "removed": len(before),
        "summary": str(proposals[-1].get("summary") or "清空课表"),
    }


def _pending_todo(session_id: str) -> dict | None:
    """暂存里那张待确认的待办卡（人话：学生上一轮刚看到的确认卡）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_add" and not o.get("applied"):
            return o
    return None


def _pending_remove_candidates(session_id: str) -> list:
    """暂存里那份"删待办候选清单"（人话：上一轮列给学生看的那几条，按显示顺序）。

    存它是为了接住学生最自然的答法——系统刚列了编号清单，他回一句"第二条"。
    认不出这句，学生点完名又掉回大模型，模型只能瞎猜或者干巴巴地"请你说清楚"。
    """
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_remove_candidates":
            todos = o.get("todos")
            if isinstance(todos, list):
                return todos
    return []


def _pending_todo_remove(session_id: str) -> dict | None:
    """暂存里那张待确认的**删待办**确认条（跟 _pending_todo 是一对，一个加一个删）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_remove" and not o.get("applied"):
            return o
    return None


_WRITTEN_RECEIPTS = ("已加入日程", "已按你的确认", "已删除待办")


def _cn_day(date_str: str) -> str:
    """'2026-09-29' → '周二'（人话：回话里要说学生听得懂的那一天）。"""
    try:
        return "周" + "一二三四五六日"[
            datetime.date.fromisoformat(date_str).isoweekday() - 1]
    except Exception:
        return ""


def _iso_wd(date_str: str):
    """'2026-09-29' → 2（周表 course.day 用的就是 1=周一…7=周日）。"""
    try:
        return datetime.date.fromisoformat(date_str).isoweekday()
    except Exception:
        return None


def _already_written_recently(session_id: str) -> bool:
    """最近几轮系统有没有真写过库（人话：认自己写下的那句回执）。

    为什么需要它：学生加完待办、回一句"好的"，话里也带确认词；
    此时若再挂一次确认条，他再点一下就是**重复写两条**。
    """
    return any(any(k in (m.get("content") or "") for k in _WRITTEN_RECEIPTS)
               for m in get_conversation(session_id)[-6:]
               if m.get("role") == "assistant")


# 系统自己写进会话的那些句子（挂卡标记 / 写库回执）。它们**不是**"管家给学生的方案"，
# 从里头捞确认条等于把已经落地的再挂一次（学生再点一下就是重复写两条）。
_SYSTEM_NOTE_PREFIXES = ("已生成待办确认", "已生成删待办提案",
                         "已生成清空提案", "已生成加课提案")


def _is_system_note(text: str) -> bool:
    """这句话是不是系统自己写下的账（人话：回执和挂卡标记，别当方案读）。"""
    t = (text or "").strip()
    return (t.startswith(_SYSTEM_NOTE_PREFIXES)
            or any(k in t for k in _WRITTEN_RECEIPTS))


# "我自己定 / 我自己挑 / 列几个给我看 / 还有别的空档吗"——学生要**自己挑**时间。
# 第六轮改版后（第一站直接敲定），这是"自己挑"那条路仅存的聊天入口，见 3c-pre4。
_SELF_PICK_RE = re.compile(
    r"我自己(?:定|挑|选)|自己挑|自己选|列(?:几个|出来)|换几个|"
    r"还有别的(?:空档|时间)|其他空档|别的时间")


def _recent_add_request(session_id: str, span: int = 8) -> str:
    """学生最近那句"要加一件事"的原话（人话：找不到就替他补不出条来）。

    两个地方共用：
      · 3a-c「确认落空」——学生回了"确认"可暂存是空的，得把他的原话捞回来重算；
      · 模型那一路——它没调工具、只在文字里念了方案，同样得靠这句话兜底。
    以前这段是内联在 3a-c 里的，抽出来是为了让两边**同一套判据**
    （改一处漏一处的话，症状会是"回确认能补条、说别的补不出来"）。

    span 只往前看这么多条消息——学生是真加待办，原话一定在最近几轮里；
    翻太远会把很久以前那句捞出来，学生早改主意了。
    """
    msgs = get_conversation(session_id)
    # 前提：最近几轮**没真的写进去过**，否则他再点一下就是重复写两条。
    if _already_written_recently(session_id):
        return ""
    return next(
        (m.get("content") or "" for m in reversed(msgs[-span:])
         if m.get("role") == "user"
         and not is_confirmation(m.get("content") or "")
         and wants_add_todo(m.get("content") or "")),
        "")


def _rescue_answer(card: dict) -> str:
    """系统替模型补出卡片时那句回话。

    为什么不能沿用模型自己那段文字：它那段在这一刻**是自相矛盾的**——
    实测原文里前半句是"提案我这边还没生成出来…你现在点【确认】是空确认"，
    后半句又是"提案已发出，请点确认条上的【确认】"。现在条真的挂出来了，
    这两句一句假一句更假，留着只会让学生更懵。所以整段换成系统话术。
    """
    kind = card.get("kind")
    if kind == "todo_mode":
        return _mode_answer(
            card, lead="📝 刚才那条消息里说要发提案，可卡片没跟上——我补一张，"
                       "先把时间怎么定说清：\n\n")
    if kind == "todo_slots":
        return _slots_answer(
            card, lead="📝 刚才那条消息里说要发提案，可卡片没跟上——"
                       "我把空着的时间段列出来，你勾一个：\n\n")
    return (
        "📝 刚才那条消息里写了方案，可卡片没跟上——我替你把确认条挂出来了：\n\n"
        f"要不要把 **{card.get('title')}** 排进日程？"
        f"{card.get('date')}（{card.get('weekday')}）"
        f"{card.get('start')}-{card.get('end')}。\n"
        "点【确认加入】执行；直接回一句「确认」也一样。"
    )


def _no_card_answer() -> str:
    """卡补不出来、可它已经对学生说"已提交/已加入"了 → 如实纠正。

    为什么这句不能留：学生看完就去等了，而库里一条都没有。
    「嘴上说写好了」是我们从 `ai-logs/13` 一路修到现在的老毛病，
    到这一版连"过去式"都出来了（实测原话：「已提交，等系统入库后周一待办里
    就会多出「游泳 16:00~17:30」这一条。」——**什么都没挂、什么都没写**）。
    """
    return (
        "⚠️ 先纠正一下：我这边**没有挂出确认条**，也**没有写进日程**——"
        "刚才那句「已经提交 / 已经加入」不作数，是我说错了，别等它。\n\n"
        "你再说一遍要加什么（比如「帮我加个游泳」），我重新出一张确认条；\n"
        "**点【确认加入】、或者回一句「确认」**才会真正写进你的周表和月表。"
    )


def _pending_todo_mode(session_id: str) -> dict | None:
    """暂存里那张**二选一卡**（人话：上一轮问学生"时间你自己定还是我帮你挑"）。

    它跟 todo_slots / todo_add 都不一样：那两张卡里**都有时间**，这一张**只有问题**。
    所以在确认分支里必须排在最前面处理——学生回一句「确认」时，
    他确认的可能是"我知道要做这件事"，可**还没选**时间由谁定：
    这时候写任何时间进日程都是替他做主（写错了还得再来一轮删）。
    """
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_mode" and not o.get("applied"):
            return o
    return None


def _mode_answer(card: dict, lead: str = "") -> str:
    """二分卡出条时那句回话（人话：把这个二选一摊在聊天里）。

    为什么聊天里也要写一遍这两个选项：确认条是个 iframe，万一没载进来
    （网络抖动、环境不让嵌），学生至少还能在文字里看见有两条路可选，
    而不是"管家说了半天，我什么都没看到"。
    """
    title = card.get("title") or "待办"
    bits = [f"📝 **{title}** 这件事，先确认一下时间怎么定："]
    if card.get("minutes"):
        bits.append(f"你说的时长我记下了（约 {format_minutes(card.get('minutes'))}）。")
    bits.append("　· **时间我自己定** —— 我列出几段空着的时间，你挑；")
    bits.append("　　（几段都不合意，也可以在「其他时间」里自己写一句。）")
    bits.append("　· **还没定，你帮我挑** —— 我按这件事该花多久，直接挑一段排上，")
    bits.append("　　并告诉你为什么排在这儿。")
    bits.append("下面那张卡上点一下就行。")
    return lead + "\n".join(bits)


def _pending_todo_slots(session_id: str) -> dict | None:
    """暂存里那张**候选时段**卡（人话：上一轮摊给学生打勾的那几段时间）。

    跟 _pending_todo 的差别：todo_add 是"已经定好一个点、就等你点头"，
    todo_slots 是"几个点摊开、你自己挑"。学生挑完是走弹框上的按钮
    （前端直接打 /api/todos/batch），但他也可能**在聊天里报一个点**
    （"改成周六下午"、"晚上七点到八点"）——那就要接住，别让它掉回模型。
    """
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_slots" and not o.get("applied"):
            return o
    return None


def _slots_answer(card: dict, lead: str = "") -> str:
    """候选卡出条时那句回话（人话：把"我替你挑了哪几段"摊在聊天里）。

    为什么聊天里也要把几段列一遍：确认条是个 iframe，万一它没载进来
    （网络抖动、环境不让嵌），学生至少还能在文字里看见有哪几段可挑，
    而不是"管家说了半天，我什么都没看到"——上一轮被投诉的就是这个。
    """
    title = card.get("title") or "待办"
    slots = card.get("slots") or []
    lines = "\n".join(f"　· {s.get('label', '')}" for s in slots)
    return (
        f"{lead}"
        f"📝 **{title}** 这件事我替你看了几个空着的时间段（都避开了课和已有的安排）：\n\n"
        f"{lines}\n\n"
        f"下面的确认条里可以直接**打勾**——勾一个、或者勾几个都行，"
        f"勾好点【加入日程】我就写进去。\n"
        f"这几段都不合适？在确认条的「其他时间」里自己写一个也行"
        f"（比如「周六下午三点到四点」）。"
    )


# 明显的**查询**词。规则判了 add_todo 却带这些词 → 多半是规则被多义词骗了
# （「我都有啥安排」里的"安排"是名词，不是"帮我安排"那个动词），
# 交给语义层复核一次。这里刻意**不列"呢/吗"**：「帮我安排个健身吗」是下单不是查询。
_ASKING_RE = re.compile(
    r"(有\s*啥|有\s*什么|都有啥|啥安排|什么安排|哪些|看看|查查|有没有|在\s*哪)")


def _rule_intent(message: str) -> str | None:
    """把所有确定性分支的开关**预检**一遍（人话：先问"规则接不接得住"）。

    为什么单独列一份：语义兜底（app/agent/intent.py）**只在规则全落空时**才该跑，
    不然每句话都多一次 LLM 调用，"零成本"就成了空话。

    ⚠️ 它只用于判断"要不要升级到语义层"——**真正的分支还在下面各判各的**。
    这里漏判/多判都不会改变最终结果，最坏只是"该省的没省下"或"多问了一次"。
    所以它不需要跟下面完全一致，也不该被当成第二份判定逻辑去维护。
    """
    if wants_clear_timetable(message):
        return INTENT_CLEAR_TIMETABLE
    if wants_add_todo(message):
        return INTENT_ADD_TODO
    if wants_list_todo(message):
        return INTENT_LIST_TODO
    if wants_remove_todo(message) or wants_remove_todo_loose(message):
        return INTENT_REMOVE_TODO
    return None


def _slots_lead(persona_key: str | None) -> str:
    """给**确定性那一路**的候选卡回话也带上性格腔调（没选性格就返回空串）。

    踩过的坑：性格原本只拼进**模型**的系统提示，可「加个健身」「我要去吃火锅」
    这种最常见的说法走的是系统确定性分支（快、稳、不烧 token，也不怕模型乱发挥），
    那一路上一个字都不带性格——学生明明选了「暖心朋友」，结果每次加待办
    都听回同一句官方腔，换性格跟没换一样。
    所以确定性分支出条时，也去 student_persona 里取一句同场景的话术当开头。
    没选/选了非法 key 就返回空串，调用方照旧用那句不带性格的默认引导语。
    """
    if not persona_key:
        return ""
    try:
        from app.modules.student_persona import mock_phrase
        phrase = (mock_phrase(persona_key, "propose") or "").strip()
    except Exception:
        return ""
    return (phrase + "\n\n") if phrase else ""


def _offer_response(session_id: str, card: dict, message: str,
                    answer: str, phase: str) -> dict:
    """把一张待办提案挂进暂存 + 组装响应（人话：出条这件事只写一遍）。

    抽出来是因为现在有**三个出口**都要挂条：学生说「加个健身」（3c）、
    学生在候选卡下面报了准点（3c-pre）、系统刚问过一轮而学生答了"确认"（3a-c 兜底）。
    抄三份迟早走样——比如有一处忘了写 `save_pending`，
    学生点了按钮后端找不到提案，现象又是"点了没反应"。
    """
    save_pending(session_id, [card])
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", f"已生成待办确认：{card.get('summary', '')}")
    return {
        "module": "planner",
        "session_id": session_id,
        "answer": answer,
        "trace": [{"step": 1, "phase": phase, "answer": card.get("summary", "")}],
        "options": [card],
        "awaiting_choice": True,
    }


def _remember_todo_options(session_id: str, options: list) -> list:
    """把候选时段记进暂存（人话：学生点【确认所选】时，系统要自己认出是哪一张）。

    为什么必须记下来：学生点完卡片，前端回发的是
    "确认 2026-09-24 19:00-20:30 背单词"——这句话交给路由，会随机落到
    planner / schedule / admin 中的任意一个，落到别处就没人写这条待办。
    系统手里有这份候选，就能自己认出"就是它"，直接落库。
    """
    picks = []
    for o in options or []:
        # ⚠️ 这一段专门接"**没有** date/start/end 的提案"，两种都要原样保住（kind 不改）：
        #
        # 候选时段卡（todo_slots）：它是"好几个时段摊着、等学生挑"，不是"已经定好一个点"。
        # 上面的完整性检查会把它整张过滤掉 —— 转成 todo_pick 那套（单个时段已定）
        # 会把候选列表整个丢掉，学生再回一句「确认」，`_pending_todo_slots` 就找不到
        # 该给他哪几段了。
        #
        # 二选一卡（todo_mode）：一样的道理，而且更脆 —— 它**连时间都没有**，
        # 被过滤掉之后学生回「确认」时 `_pending_todo_mode` 什么都找不到，
        # 现象就是"我点了没反应 / 它又问我一遍"。这是 todo_slots 刚踩过的坑，
        # 同一段代码里必须一起接住。
        if isinstance(o, dict) and not (o.get("date") and o.get("start") and o.get("end")):
            if o.get("kind") == "todo_mode":
                picks.append({**o, "summary": o.get("summary")
                              or f"{o.get('title') or '待办'}｜还没定时间"})
            elif o.get("kind") == "todo_slots" and o.get("slots"):
                picks.append({**o, "summary": o.get("summary")
                              or f"{o.get('title') or '待办'}｜"
                                 f"{len(o['slots'])} 个候选时段"})
            continue
        # ⚠️ 只有"加待办"类提案才记成候选时段。
        # 删待办提案（todo_remove）也带 date/start/end，要是被顺手记成 todo_pick，
        # 学生回一句"确认"就会走 _match_todo_pick —— **删一条变成新加一条**，
        # 而且 save_pending(picks) 还会把那张删待办确认条从暂存里顶掉。
        if o.get("kind") and o.get("kind") != "todo_add":
            continue
        card = {**o, "kind": "todo_pick"}
        card["title"] = o.get("title") or "待办"
        card["summary"] = (
            f"{card['title']}｜{card['date']}（{card.get('weekday', '')}）"
            f"{card['start']}-{card['end']}"
        )
        picks.append(card)
    if picks:
        save_pending(session_id, picks)
    return options


def _lone_todo_pick(session_id: str, message: str) -> dict | None:
    """暂存里只有**一张**已定好时间的卡、学生又只回了一句「确认」→ 那就是它。

    为什么需要它（这是一条"点了没用"的投诉）：
    **模型那一路**挂出来的条（它自己调 `propose_todo_tool`，或系统替它补的那张）
    存进暂存时是 `todo_pick` 形状 —— 那是 `_remember_todo_options` 转的，
    本意是给"学生点候选卡片"用的。麻烦在于：
      · `_pending_todo` 只认 `todo_add`；
      · `_match_todo_pick`（3a-bis）又要求消息里**带日期+时间**。
    两头都不认，于是学生裸回一句「确认」时既不写库，也不会报错，
    而是掉进 3a-c 换来一句"刚才那次可能没接上，我把确认条又挂了出来"——
    条挂出来了、按钮也是好的，可他再回一句"确认"还是这句。原地打转。

    两道闸门，差一道就会写错：
      · 消息里**不能**带日期+时间 —— 带了说明他是在点某一张候选卡，
        那张该由 3a-bis 的 `_match_todo_pick` 按日期/时间**精确匹配**，别抢；
      · 暂存里**只能有一张** —— 多张时不知道他要哪张，宁可不写（交回兜底去问）。
    """
    if parse_add_todo(message):
        return None
    entry = peek_pending(session_id) or {}
    lone = [o for o in entry.get("options") or []
            if isinstance(o, dict) and o.get("kind") == "todo_pick"
            and not o.get("applied")]
    return lone[0] if len(lone) == 1 else None


def _match_todo_pick(session_id: str, message: str) -> dict | None:
    """学生已经点了候选卡上的【确认所选】：从暂存里找出他选的那一张，直接写入。

    返回 None 表示这次不是"确认候选"，交给后面的分支照旧处理。
    """
    parsed = parse_add_todo(message)
    if not parsed:
        return None
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if (isinstance(o, dict) and o.get("kind") == "todo_pick"
                and o.get("date") == parsed["date"]
                and o.get("start") == parsed["start"]):
            return _todo_card(o, parsed)
    return None


def _todo_card(pick: dict, parsed: dict) -> dict:
    """把候选归一成一张能直接落库的待办卡。

    为什么要点这一步归一化：候选是**别的模块/别的模型**吐出来的，形状未必一致——
    真实模型给的候选可能压根没有 weekday 字段。拿到手先补齐，
    免得回话拼字符串时一个 KeyError 把整个请求打成 500，
    学生眼里的现象就是"我点了确认，页面白屏"。
    """
    card = {**pick, "kind": "todo_add"}
    card["title"] = pick.get("title") or parsed.get("title") or "待办"
    card["date"] = pick.get("date") or parsed.get("date")
    card["start"] = pick.get("start") or parsed.get("start")
    card["end"] = pick.get("end") or parsed.get("end")
    card["weekday"] = pick.get("weekday") or ""
    card["summary"] = (
        f"{card['title']}｜{card['date']}（{card['weekday']}）"
        f"{card['start']}-{card['end']}"
    )
    return card


def _write_todo(card: dict) -> dict:
    """把一张待办确认卡真正写进日程（人话：系统落库，模型碰不到）。"""
    return add_todo(
        card.get("title") or "待办", card.get("date") or "", card.get("start") or "",
        card.get("end") or "", note=card.get("note", ""),
    )


@app.get("/api/health")
async def health():
    """健康检查（人话：不依赖大模型密钥，用来确认服务正常启动了）。"""
    return {"status": "ok", "modules": list(REGISTRY.keys())}


@app.post("/api/chat")
async def chat(req: Request):
    """对话入口（人话：前端把用户的话发到这里，返回回答 + Agent 思考轨迹）。

    前端可以显式带 module 字段（如 {"message": "...", "module": "express"}）直接锁定模块，
    方便主页四个模块各自独立对话；不带的则交给 router 自动判断意图。
    """
    body = await req.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 学生端选的性格（前端随对话发来；只有合法 key 才生效，否则忽略）
    persona_key = body.get("persona")
    if not is_valid(persona_key):
        persona_key = None

    # 1) 若前端显式指定了模块且合法，直接用；否则交给 router 自动分类意图
    module_key = body.get("module")
    if not (module_key and module_key in REGISTRY):
        module_key = await router.route(message)

    # 2) 取该模块的系统提示和工具箱（提示可能是函数，按需调用以拼入最新人格）
    prompt_src, build_tools = REGISTRY.get(module_key, (DEFAULT_PROMPT, lambda: {}))
    base_prompt = prompt_src() if callable(prompt_src) else prompt_src
    # 统一拼上"你会读文件"的说明 + 学生助手人设规矩，每个模块都生效
    prompt = base_prompt + FILE_HINT + ASSISTANT_RULES
    # 学生端选了性格，且不是驿站模块（驿站用管理员配的人格），就把性格腔调拼进去
    if persona_key and module_key != "station":
        prompt += "\n" + persona_block(persona_key)

    # 工具箱 = 模块自己的工具 + 通用的文件读取工具。
    # 把"读文件"做成通用工具而不是复制进每个模块，
    # 以后新增模块会自动带上这个能力，不用重复实现。
    tools = build_tools()
    tools.update(files_tools())
    # 不管路由把话分到哪个模块，都给它配上"查空档 + 把待办变成能点的确认条"这几件
    # **只读**工具。原因很具体：学生说「我想周四去健身」时，路由可能把这句分给
    # 「校园问答」，而那个模块原本文具盒里只有一个 search_kb —— 它就只能**在文字里**
    # 写一句「- 任务：健身 - 时间：周四 15:40~17:10 点【确认】就入库了」，
    # 界面上一个按钮都没有；学生回「确认」，系统也不知道他在确认什么。
    # 这几件工具都不写库（真写入永远是后端的确认条），所以发给谁都安全。
    tools.update(scheduling_tools())

    # 会话 id：前端带上，服务端就能把几轮对话串起来。
    # 没带就服务端生成一个并在响应里返回，前端存起来下次继续用。
    session_id = (body.get("session_id") or "").strip() or uuid.uuid4().hex[:12]

    # 3) **确定性确认分支**（人话：学生回一句"确认"就当场执行，不劳模型判断）
    #    踩过的坑：学生明明打了"确认"，AI 却回"我没有权限删除"，还让学生自己去
    #    页面上找删除按钮——那里根本没有入口。这里把执行权收归系统，学生点头即写入。
    # 不按模块卡死：确认卡就渲染在同一条聊天里，学生不管当前切到哪个面板、
    # 路由把这句分去哪个模块（实测"确认"常被分到 FAQ），点头就是点头，该执行就执行。
    if is_confirmation(message) and peek_pending(session_id):
        from app.modules.planner import apply_pending_timetable_change
        # 待办确认：学生回一句"确认"，把上一轮那张待办确认卡真正写进日程。
        # 为什么不能等路由决定：实测学生回一句"确认"，路由把它甩到 admin 模块，
        # 收到的是"你回「确认」了，但我这边还没生成待办提案"——话都说到这份上了还写不进去，
        # 学生只会觉得系统在耍他。这里把执行权收归系统，点头就是写入。
        # 第二种"待办确认"：暂存里那张是 `todo_pick`（模型那一路存的形状）——
        # 见 `_lone_todo_pick` 的说明，不接这一档的话学生回「确认」会原地打转。
        picked = _pending_todo(session_id) or _lone_todo_pick(session_id, message)
        if picked:
            take_pending(session_id)
            _write_todo(picked)
            # 用 .get() 而不是 picked['summary']：提案里的字段少一个就 500 了，
            # 学生只会看到一句"网络开小差了"。摘要本来就是可选的，自己拼一句就行。
            summary = (picked.get("summary")
                       or f"{picked.get('title', '待办')}｜{picked.get('date', '')}"
                          f"{picked.get('start', '')}-{picked.get('end', '')}")
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已加入日程：{summary}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"✅ 已加入日程：**{picked.get('title', '待办')}**｜{picked.get('date', '')}"
                    f"（{picked.get('weekday', '')}）"
                    f"{picked.get('start', '')}-{picked.get('end', '')}。"
                ),
                "trace": [{"step": 1, "phase": "✅ 学生确认（系统写入）",
                           "answer": summary}],
                "options": [],
                "awaiting_choice": False,
            }
        # 学生回"确认"，可暂存里那张是**二选一卡**（时间由谁定都还没选）——
        # 这时候更不能写：他确认的可能是"我知道要做这件事"，
        # 而不是"就按某个时间排上"。写哪个时间都是替他做主，写错了还要再来一轮删。
        # 正解：把二选一卡**再挂一遍**，说清楚"得先选一种"。
        only_mode = _pending_todo_mode(session_id)
        if only_mode:
            return _offer_response(
                session_id, only_mode, message,
                _mode_answer(only_mode,
                             lead="先别急——**还没选时间怎么定**，我这边一条都没写进去。\n\n"),
                "🗓️ 只回了「确认」还没选方式 → 二选一卡再挂一遍，不替学生定")
        # 学生回"确认"，可暂存里那张是**候选时段卡**（几个点摊着、他还没勾）——
        # 这时候不能当成"他点头了"就随便挑一个写进去：他根本不知道你会写哪一个，
        # 而写进去的是一条真安排，写错了还得再来一轮删。
        # 正解：把候选条**再挂一遍**，并且说清楚"得你自己勾一个"。
        # 这一支必须排在下面任何"执行"之前，也排在 apply_*（会把暂存 take 走）之前。
        only_slots = _pending_todo_slots(session_id)
        if only_slots:
            return _offer_response(
                session_id, only_slots, message,
                _slots_answer(only_slots,
                              lead="先别急——这几个时间段得**你挑一个**，我这边还没收到你的勾选，"
                                   "**一条都没写进去。**\n\n"),
                "🗓️ 只回了「确认」还没勾时间 → 候选条再挂一遍，不替学生挑")
        # **清空课表的确认**：按新规格，弹窗点【确认】是优先方式，
        # 在聊天框回"确认删除"这类确认文字是**备选方式，同样要执行**（需求③）。
        # 早先这里为了保护"删空不可恢复"硬加了一道"必须点按钮"的闸门，
        # 结果学生回「确认」毫无反应，被当成"删除失败"投诉了一轮。闸门让位给规格。
        # 执行逻辑收在 _execute_clear() 里，跟弹窗按钮走同一段代码——
        # 模型依然碰不到删除接口，只是"谁来按这个按钮"多了一个入口。
        entry_now = peek_pending(session_id) or {}
        clear_held = [o for o in entry_now.get("options") or []
                      if isinstance(o, dict) and o.get("kind") == "timetable_clear"]
        if clear_held:
            done = _execute_clear(session_id)
            if done is not None:
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已按你的确认清空课表：{done['removed']} 门")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"✅ 已按你的确认执行（{done['summary']}），"
                        f"清空了 **{done['removed']} 门课**，周表现在一门课都不剩。"
                    ),
                    "trace": [{"step": 1, "phase": "✅ 学生确认（系统执行清空）",
                               "answer": f"清掉 {done['removed']} 门"}],
                    "options": [],
                    "awaiting_choice": False,
                }
            # 周表已经是空的：没什么可清，提案作废，直说。
            clear_pending(session_id)
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "周表本来就是空的")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": "周表本来就是空的，一门课都没有，没有课可清空 🗑️",
                "trace": [{"step": 1, "phase": "🗑️ 周表已为空",
                           "answer": "没有课可清，直接告知学生"}],
                "options": [],
                "awaiting_choice": False,
            }
        # **删待办的确认**：学生回一句"确认"，把上一轮那张【确认删除】真正执行掉。
        # 跟清空课表同一个道理——执行权在系统手里，模型连删除接口都碰不到。
        # 规格三条红线里的第一条（"学生没点头就宣布已经删了"）就是靠这里兜住的：
        # 只有走到这一段，delete_todo 才会被调用到。
        #
        # ⚠️ 位置不能挪到下面 apply_pending_timetable_change() 之后：
        #    那个函数是**先把暂存 take 走**、再按 kind 过滤的（拿不到课表提案也照样清空），
        #    排在它后面读暂存永远是空的 —— 学生回"确认"就执行不了，
        #    转头掉进"确认落空"兜底、把确认条又挂一遍，看着像点了没用。
        #    （这不是推演，是这一步真踩过的坑。）
        rm = _pending_todo_remove(session_id)
        if rm:
            take_pending(session_id)
            target = get_todo(rm.get("todo_id") or "")
            if target is None:
                # 那条已经不在了（比如学生自己在上一条里点过删除条）——
                # 如实说，不能假装删了一次。
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", "那条待办已经不在了")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"🗑️ 这条待办已经不在日程里了（可能刚才已经删掉了），"
                        f"没有重复删。\n刚才那条是：**{rm.get('title', '待办')}**｜"
                        f"{rm.get('date', '')}（{rm.get('weekday', '')}）"
                        f"{rm.get('start', '')}-{rm.get('end', '')}。"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 待办已不存在", "answer": "不重复删"}],
                    "options": [],
                    "awaiting_choice": False,
                }
            delete_todo(rm.get("todo_id"))
            summary = (rm.get("summary")
                       or f"{rm.get('title', '待办')}｜{rm.get('date', '')}"
                          f"{rm.get('start', '')}-{rm.get('end', '')}")
            append_conversation(session_id, "user", message)
            # 这句回执是"真的删过了"的凭据：下一轮再有确认词进来，
            # 兜底分支靠它判断"最近写过/删过，别再挂一次条"（跟加待办那边对称）。
            append_conversation(session_id, "assistant", f"已删除待办：{summary}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"✅ 已删除：**{rm.get('title', '待办')}**｜{rm.get('date', '')}"
                    f"（{rm.get('weekday', '')}）{rm.get('start', '')}-{rm.get('end', '')}，"
                    f"日程里已经没有它了。"
                ),
                "trace": [{"step": 1, "phase": "✅ 学生确认（系统删除）",
                           "answer": summary}],
                "options": [],
                "awaiting_choice": False,
            }

        # 暂存里只摆着"删待办候选清单"、学生却直接回了一句"确认"——
        # 他还没点名是哪一条（或者我们列的清单他还没看清）。这时**不能**清掉清单，
        # 也不能拿任意一条去删：把清单再念一遍，请他点名。
        held_cand = _pending_remove_candidates(session_id)
        if held_cand:
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "删待办缺细节")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    "🗑️ 先别急——你要删的是哪一条？我还没收到你的选择，"
                    "**一条都没删。**\n\n"
                    f"{render_todo_remove_list(held_cand)}\n\n"
                    "回我一个名字（或序号，比如「第二条」），我就把确认条挂出来。"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 还没点名是哪一条（不猜、不删）",
                           "answer": "等学生点名"}],
                "options": [],
                "awaiting_choice": False,
            }

        # 走到这儿说明暂存里是**改课类提案**（增删单节课），
        # 清空类、删待办类已在上面处理掉（那两类都得赶在这个函数前头，它会清空暂存）。
        # 需求③的备选方式：聊天里回"确认添加/确认删除"这类文字，同样触发后端执行。
        applied = apply_pending_timetable_change(session_id)
        if applied:
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已执行：{applied['summary']}")
            return {
                "module": module_key,
                "session_id": session_id,
                "answer": (
                    f"✅ 已按你的确认执行（{applied['summary']}）。"
                    f"周表现在共 {applied['after']} 门课"
                    f"（原来是 {applied['before']} 门）。"
                ),
                "trace": [{"step": 1, "phase": "✅ 学生确认", "answer": applied["summary"]}],
                "options": [],
                "awaiting_choice": False,
            }
        # 暂存里只有别的类型提案（比如时间候选）→ 清掉，交回模型按老规矩处理
        clear_pending(session_id)

    # 3a-bis) **候选卡确认分支**（人话：学生点【确认所选】，系统当场写入他选的那个时段）
    #     排在加待办提案分支之前——他既然已经点过卡片了，就不该再被问一遍"要不要加"。
    picked_now = _match_todo_pick(session_id, message)
    if picked_now:
        take_pending(session_id)
        _write_todo(picked_now)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", f"已加入日程：{picked_now['summary']}")
        return {
            "module": "planner",
            "session_id": session_id,
            "answer": (
                f"✅ 已加入日程：**{picked_now['title']}**｜{picked_now['date']}"
                f"（{picked_now['weekday']}）{picked_now['start']}-{picked_now['end']}。"
            ),
            "trace": [{"step": 1, "phase": "✅ 学生选定时段（系统写入）",
                       "answer": picked_now["summary"]}],
            "options": [],
            "awaiting_choice": False,
        }

    # 3a-c) **确认落空的兜底**：学生回"确认"，可暂存里已经没有待确认的提案。
    #     什么时候会走到这：① 清空课表的弹窗被学生点了【取消】（取消=作废，本来就该这样）；
    #     ② 提案过了有效期；③ 服务重启过（暂存是内存态）；④ 学生刷新了页面。
    #     实测这种情况交给模型，模型会顺嘴撒谎——"我重新帮你出一次提案"（根本没出）、
    #     "请你在弹出的确认卡上点确认"（根本没有卡）、"回我一句「确认清空」就会执行"
    #     （回什么都不会执行）。学生照做三连失败，正是"卡片没显示、确认不删、删除失败"的来历。
    #     如果会话最近聊的就是清空课表，最贴心的做法是把弹窗**重新请出来**（只出提案不写库，
    #     硬约束不变：真删仍要学生点弹窗按钮）；其余情况不动，继续交给模型正常聊天，
    #     免得把"好的/嗯"这种日常应答也误伤成"没有待确认事项"。
    if is_confirmation(message) and not peek_pending(session_id):
        recent = [m for m in get_conversation(session_id)[-6:]
                  if m.get("role") == "assistant"]
        # 匹配要收着点：只认系统自己写进会话的标记（"已生成清空提案"），
        # 或明确说到"清空课表/清空全部课程"的句子。"清空缓存"这类闲聊词不认，
        # 免得学生随口应一声"好的"，弹出一个莫名其妙的清空弹窗。
        talked_clear = any(
            "已生成清空提案" in (m.get("content") or "")
            or ("清空" in (m.get("content") or "")
                and ("课表" in (m.get("content") or "") or "课程" in (m.get("content") or "")))
            for m in recent)
        if talked_clear:
            proposal = _parse_clear_proposal()
            if proposal is not None:
                save_pending(session_id, [proposal])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成清空提案：{proposal.get('summary', '')}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        "🗑️ 上一次的确认条可能已经关掉了，我又把它请了出来——"
                        "在下面这条确认条上点【确认清空】执行，点【取消】就什么都不变。\n"
                        "（也可以直接在聊天框回一句「确认删除」，一样会执行。）"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 确认落空 → 重新挂出清空确认条",
                               "answer": "提案已过期/被取消，重新出提案"}],
                    "options": [proposal],
                    "awaiting_choice": True,
                }
            # 周表已经是空的：没什么可清。
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "周表本来就是空的")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": "周表本来就是空的，一门课都没有，没有课可清空 🗑️",
                "trace": [{"step": 1, "phase": "🗑️ 周表已为空",
                           "answer": "没有课可清，直接告知学生"}],
                "options": [],
                "awaiting_choice": False,
            }
        # 加待办的确认落空：学生上一句加待办的原话没被确定性分支接住
        # （比如「周二下午两点到三点游泳」这种中文报时，老解析器认不出时间），
        # 掉给模型嘴上说"搞定！已经正式写进待办啦"——schedule 模块连一个写入
        # 工具都没有，日程里其实什么都没有。照清空课表的样子把话捞回来：
        # 找到学生最近那句加待办的原话，重新走一遍确定性解析，
        # 出得了提案就重新挂确认条；出不了就继续交给模型正常聊。
        #
        # 但有个前提：最近几轮**没真的写进去过**。学生加完待办、回一句"好的"，
        # 话里也带确认词，这时再挂一次确认条、他再点一下就是重复写两条。
        # 判据就是系统自己写进会话的那句回执（"已加入日程"）。
        #
        # 这一段跟"模型那一路"的兜底**共用 `_recent_add_request`**：
        # 两边要问的是同一个问题（学生最近想加的是哪件事），
        # 各写一份迟早会走样，症状还特别像"回确认能补条、说别的补不出来"。
        recent_msgs = get_conversation(session_id)[-6:]
        already_written = _already_written_recently(session_id)
        add_req = _recent_add_request(session_id)
        proposal = parse_add_todo(add_req) if add_req else None
        from_butler = False
        auto_filled = False      # 时间是不是系统替学生挑的（话术要区分，见下）
        # 学生原话也解析不出来？那就捞**管家自己写的那句"提案"**。
        # 这正是被截屏投诉的那一幕：管家在文字里写
        # 「好，那我按这个出个提案：- 任务：健身 - 时间：周一 16:30~18:00 …
        #   提案这就发给你，点一下【确认】就入库了！」
        # 界面上却连一个按钮都没有——因为它压根没调工具，暂存是空的。
        # 学生回"可以"，系统就替它把这张确认条挂出来（写库仍要学生点按钮）。
        # 只认固定格式（"任务/事项/标题：" 配 "时间/时段："），闲聊里捞不出东西就返回 None，
        # 宁可这条不挂，也不从闲聊里瞎猜一个待办出来。
        # 判据要问的是"**这场商量是不是还没落地**"。
        # 这句话看着绕，可问错了两版都出事，两版的病历都留在这儿：
        #   · 第一版问"最近这几轮有没有写过库"（`already_written`，整段挡掉）——太粗。
        #     只要附近出现过一次「已加入日程」，往后哪怕管家又给了一份干净方案、
        #     学生老老实实回一句「确认添加」，也照样一个字都捞不回来 → 掉到 3c
        #     反问他「好——**要安排的是什么事**？」。对着一份已经写好的提案问
        #     "要做什么"，正是报障那一幕（截图里管家的提案白纸黑字挂在那儿）。
        #   · 第二版问"这句话是不是系统自己写的账"（跳过回执句）——粒度对了，
        #     可漏了后半截：**提案本身也会过期**。一条提案就算已经被学生点完、
        #     库都写好了，它的文字照样留在历史里；只跳回执、不往前看的 starting
        #     线，就会把这份**（早已落实过的）旧提案**重新捞出来挂一遍，
        #     他再点一下就是重复写两条（第六批那条断言守的就是它）。
        #
        # 正解是划一条**起跑线**：找到最近一次写库回执的位置，只在它**之后**
        # 的那些话里找方案。回执之前的提案，在这一幕之前已经执行过了，不许翻回去。
        receipt_at = [i for i, m in enumerate(recent_msgs)
                      if m.get("role") == "assistant"
                      and any(k in (m.get("content") or "")
                              for k in _WRITTEN_RECEIPTS)]
        started = (receipt_at[-1] + 1) if receipt_at else 0
        if proposal is None:
            for m in reversed(recent_msgs[started:]):
                if m.get("role") != "assistant":
                    continue
                text = m.get("content") or ""
                # 挂卡标记（"已生成待办确认："）也不是方案，别从它里头抠。
                if _is_system_note(text):
                    continue
                guessed = parse_todo_from_reply(text)
                if guessed is not None:
                    proposal, from_butler = guessed, True
                    break
        # 还有第三种：学生原话只说了"哪天"、没说"几点"（「那你帮我加一个健身在周四」），
        # 管家上一轮又只是在文字里客气了一句（既没调工具、格式也对不上）——
        # 前两路都解析不出完整提案。这时**别再让他空等**：
        # 照 3c 分支的正解，查空档、把候选时段摊出来，让他打勾。
        # 这正是截图那一幕：学生回「确认」→ 界面上什么都没有、日程里也没写进去。
        #
        # 注意顺序：先出**二选一卡**（本轮规格：先问"你自己定 / 我帮你挑"），
        # 再退回"摊候选"，最后才退回"替他挑一个"的旧路子——
        # 旧路子只在卡都出不来时才用（比如标题没了），聊胜于无。
        if proposal is None and not already_written and add_req:
            mode_card = todo_mode_proposal(add_req)
            if mode_card is not None:
                return _offer_response(
                    session_id, mode_card, message,
                    _mode_answer(mode_card,
                                 lead="刚才那次可能没接上——这件事咱们再对一次，"
                                      "先说清时间怎么定：\n\n"),
                    "🗓️ 确认落空 → 重新挂出二选一卡（先问时间怎么定）")
            slots_card = todo_slots_proposal(add_req)
            if slots_card is not None:
                return _offer_response(
                    session_id, slots_card, message,
                    _slots_answer(slots_card,
                                  lead="刚才那次可能没接上，我把空着的时间段重新列一遍——\n\n"),
                    "🗓️ 确认落空 → 重新摊出候选时段（让打勾）")
            guessed = auto_todo_proposal(add_req)
            if guessed is not None:
                proposal, from_butler, auto_filled = guessed, True, True
        if proposal is not None:
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant",
                                f"已生成待办确认：{proposal['summary']}")
            if auto_filled:
                # 时间是系统挑的，得说明白——否则学生以为自己说过这个点。
                answer = (
                    "📝 刚才那次可能没接上，我把确认条又挂了出来——"
                    f"**{proposal['date']}（{proposal['weekday']}）"
                    f"{proposal['start']}-{proposal['end']} 是空着的**，"
                    f"就把 **{proposal['title']}** 排在这儿了。\n"
                    "点【确认加入】执行；直接回一句「确认」也一样。\n"
                    "这个点不合适，说一句「改成晚上七点到八点」就行。"
                )
                phase = "🗓️ 确认落空 → 学生只说了哪天，系统挑空档补出确认条"
            else:
                # 方案是从**管家自己那句话**里捞回来的（from_butler）→ 得说清是
                # "它只在文字里写了方案、卡片没跟上"。笼统一句"刚才那次可能没接上"
                # 学生听不懂：他看到的正是"它说要有提案，可我这儿没有弹窗"。
                lead = ("📝 我上一条只在文字里写了方案，**确认条没跟上**——我补一张：\n\n"
                        if from_butler else
                        "📝 刚才那次可能没接上，我把确认条又挂了出来——")
                answer = (
                    lead
                    + f"要不要把 **{proposal['title']}** 排进日程？"
                    f"{proposal['date']}（{proposal['weekday']}）"
                    f"{proposal['start']}-{proposal['end']}。\n"
                    "点【确认加入】执行；直接回一句「确认」也一样。"
                )
                phase = ("📝 确认落空 → 从管家文字里的方案捞回确认条" if from_butler
                         else "📝 确认落空 → 重新挂出待办确认条")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": answer,
                "trace": [{"step": 1, "phase": phase,
                           "answer": proposal["summary"]}],
                "options": [proposal],
                "awaiting_choice": True,
            }

        # 删待办的确认落空：跟加待办对称的一支。
        # 场景：学生上一句「把游泳那条待办删掉」，系统出了确认条；可学生刷新了页面
        # （暂存是内存态，一刷就空），再回一句「确认」——此时照旧把**学生原话**捞回来
        # 重新解析、重新挂确认条。"说了删、确认没删"的老毛病不该在这儿复现。
        rm_req = "" if already_written else next(
            (m.get("content") or "" for m in reversed(get_conversation(session_id)[-8:])
             if m.get("role") == "user"
             and not is_confirmation(m.get("content") or "")
             and (wants_remove_todo(m.get("content") or "")
                  or wants_remove_todo_loose(m.get("content") or ""))),
            "")
        if rm_req:
            res_rm = resolve_todo_remove(rm_req)
            if res_rm.get("status") == "ok":
                rm_prop = res_rm["proposal"]
                save_pending(session_id, [rm_prop])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成删待办提案：{rm_prop['summary']}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        "🗑️ 刚才那次可能没接上，我把确认条又挂了出来——"
                        f"要删的是 **{rm_prop['title']}**｜{rm_prop['date']}"
                        f"（{rm_prop['weekday']}）{rm_prop['start']}-{rm_prop['end']}。\n"
                        "点【确认删除】执行；直接回一句「确认」也一样。"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 确认落空 → 重新挂出删待办确认条",
                               "answer": rm_prop["summary"]}],
                    "options": [rm_prop],
                    "awaiting_choice": True,
                }

    # 3b) **确定性清空分支**（人话：学生明说"课表全删了"，直接出弹窗，不劳模型判断）
    #    跟上面"一句确认即落库"是同一个思路：能由代码定死的，就别交给模型。
    #    实测同一句话，模型时而是调 propose_clear_timetable，时而是反问"你确定要全删吗"，
    #    学生被绕回来，链路当场断掉——清空意图本来就明明白白，该由系统接管。
    #    这里只负责"出提案 + 弹窗"，**一个字节都不写库**；真删要等学生点弹窗上的【确认】。
    if wants_clear_timetable(message):
        proposal = _parse_clear_proposal()
        if proposal is None:
            # 周表本来就是空的：一句话说清楚就完事。
            # 实测这个状态会掉回模型，模型就来一句"这个操作影响比较大，我先跟你确认一下…"
            # 还列四条问题——学生只看到"弹窗不来、卡片不出"，还以为系统坏了。
            # 空表没得可清，本来就没得确认，交给模型只会多绕一圈。
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "周表本来就是空的")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    "周表本来就是空的，一门课都没有，没有课可清空 🗑️\n"
                    "想往课表里加课的话：点一下「📤 上传课表」传一份文件，"
                    "或者直接跟我说「周一加一节高等数学，教三-201」就行。"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 周表已为空",
                           "answer": "没有课可清，直接告知学生"}],
                "options": [],
                "awaiting_choice": False,
            }
        else:
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已生成清空提案：{proposal.get('summary', '')}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"🗑️ 已为你生成**清空 {proposal.get('clear_count', 0)} 门课**的确认条"
                    f"（就在下面这条消息里），点【确认清空】才会执行，"
                    f"点【取消】就什么都不变。\n"
                    f"（也可以直接在聊天框回一句「确认删除」，一样会执行。）"
                ),
                "trace": [{"step": 1, "phase": "🧹 生成清空提案（系统判定）",
                           "answer": str(proposal.get("summary") or "")}],
                "options": [proposal],
                "awaiting_choice": True,
            }

    # 3c) **确定性加待办分支**（人话：学生说"把 X 安排到今天 19:00-20:30"，
    #     日期时刻由系统算、确认卡由系统出，学生点卡片或回一句"确认"才真写）
    #     为什么不能交给路由和模型：实测同一句"帮我把今天的『复习线性代数』安排到 19:00 到 20:30"，
    #     路由时而定 planner、时而定 schedule、时而定 admin——只有 planner 手里有写待办的工具，
    #     于是客服这边时说"已经加上"、时说"你回确认了，但我这边还没生成待办提案"，
    #     学生刷新日程也看不到东西。这条链路不能碰运气。
    #     跟清空课表一个规矩：这里**只出提案，一个字节都不写库**。
    #
    #     先把"学生是不是在补上一轮追问的信息"算出来：
    #     补充句往往只有时间（"下午两点到三点"），不满足 wants_add_todo 的"得带日期"，
    #     光看这一句会被当成闲聊掉回模型——那里就是"搞定！已经写进"的老家。
    conv = get_conversation(session_id)
    prev_user = next((m.get("content") or "" for m in reversed(conv)
                      if m.get("role") == "user"), "")
    asking_add = any("加待办缺细节" in (m.get("content") or "")
                     for m in conv[-4:] if m.get("role") == "assistant")

    # 3c-pre) **学生嫌系统挑的点不合适、自己报了个时刻** → 换时间重新出条。
    #     背景：学生只说了"哪天"时，系统会替他挑一段并说明"这个点不合适你说个点"。
    #     他真说了（"下午两点到三点"、"改成晚上七点到八点"、"改成周六"），
    #     就得接住——不然那句"你说个点"等于放空炮，学生会觉得"我说了它当没听见"。
    #     只认"纯时间短句"或带"改成/换到"这类词的话，免得把一句新的下单吃掉。
    held_add = _pending_todo(session_id)
    if held_add and not is_confirmation(message) \
            and (is_add_todo_followup(message) or wants_retime_todo(message)):
        retimed = retime_todo_proposal(held_add, message)
        if retimed is not None:
            return _offer_response(
                session_id, retimed, message,
                (f"📝 好，按你说的改成 **{retimed['date']}（{retimed['weekday']}）"
                 f"{retimed['start']}-{retimed['end']}**——"
                 f"要不要把 **{retimed['title']}** 排在这儿？\n"
                 f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。"),
                "🕘 按学生说的点换时间 → 重出确认条")

    # 3c-pre2) **学生刚看到一串候选、在聊天里自己报了个点** → 就着他给的点出条。
    #     为什么要有这一支：候选卡摊出来的是"几个时间让你挑"，学生的答法有两种——
    #     ① 在确认条上打勾（走弹框按钮，前端直连 /api/todos/batch）；
    #     ② **直接在聊天里说**（"改成晚上七点到八点"、"那就周六吧"）。
    #     ② 是最自然的，接不住就掉回模型——模型只会说"已经帮你排好啦"，日程里空的。
    #     判据用 is_slot_answer：只认"纯时间/纯日期"的句子，"周六加个游泳"那种
    #     换了事情的说法不算，它得走下面的正常加待办链路重新解析标题。
    held_slots = _pending_todo_slots(session_id)
    if held_slots and not is_confirmation(message) and is_slot_answer(message):
        fb = next((s.get("date") or "" for s in (held_slots.get("slots") or [])
                   if isinstance(s, dict)), "")
        one = parse_slot_text(message, held_slots.get("title") or "待办", fb)
        if one is not None:
            return _offer_response(
                session_id, one, message,
                (f"📝 好，那 **{one['title']}** 就定在 "
                 f"**{one['date']}（{one['weekday']}）{one['start']}-{one['end']}**。\n"
                 f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。"),
                "🕘 学生在候选卡下面自己报了时间 → 出单条确认条")

    # 3c-pre3) **学生在聊天里回"你自己定 / 你帮我挑"** → 就着他选的那条路出卡。
    #     为什么必须有这一支：两选一卡上的按钮是最好走的路（前端直接打
    #     /api/todos/mode），但学生**更习惯直接在聊天框里说一句**——
    #     「你帮我定吧」「我自己有时间」。这两句一个字都不含时间，
    #     掉回模型那边就是又一轮自由发挥（实测模型会说"好的，方案A记下了"，
    #     然后反问他"游泳馆周二中午开不开"——答完回"确认"，它说"我按你说的提上去"，
    #     结果连一张确认条都没有，学生只能追问"确认条呢"）。
    #     ⚠️ 必须排在 3c 前面：`is_mode_answer` 认的"帮我安排"里含 `_ADD_INTENT` 的
    #     "安排"，排在后面就永远轮不到它。
    held_mode = _pending_todo_mode(session_id)
    if held_mode and not is_confirmation(message) and is_mode_answer(message):
        mode = parse_mode_answer(message)
        if mode is not None:
            new_card = mode_to_card(held_mode, mode)
            if new_card is None:
                # 两条路都出不来（那天/那几天真排不下）→ 如实说，别硬凑一个时间
                clear_pending(session_id)
                title = held_mode.get("title") or "待办"
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", f"{title}排不下")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (f"😥 **{title}** 这几天实在插不进去了——课和已经排好的事"
                               f"把空档都占了。\n换个日子，或者你说个具体钟点，"
                               f"我看看能不能挤一挤。"),
                    "trace": [{"step": 1, "phase": "🗓️ 二分卡两条路都排不下",
                               "answer": "如实告知，不硬凑时间"}],
                    "options": [],
                    "awaiting_choice": False,
                }
            if mode == "ai":
                # **核心**：他让系统替他安排，那就真去规划——并把"为什么排在这儿"说清
                ans = (f"🤖 好，我替你挑了：**{new_card['title']}** 排在 "
                       f"**{new_card['date']}（{new_card['weekday']}）"
                       f"{new_card['start']}-{new_card['end']}**。\n"
                       f"{new_card.get('reason') or ''}\n"
                       f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。")
                phase = "🤖 学生选了「你帮我挑」→ 系统规划出一段（单条确认条）"
            else:
                ans = _slots_answer(new_card, lead="🙋 好，时间你自己定——")
                phase = "🙋 学生选了「我自己定」→ 列出候选时段（让打勾）"
            return _offer_response(session_id, new_card, message, ans, phase)

    # 3c-pre4) **单条确认条已经挂在那儿，可学生说"我自己挑"** → 换出候选卡让他勾。
    #     为什么必须有这一支：第六轮改版把第一站改成了"直接敲定"，
    #     于是"我自己定"那颗按钮从第一站消失了——可"**进行列举、由我打勾**"
    #     是他明确要过的（学生原话，见第九批）。效率是默认，但不能把这条口子焊死：
    #     默认我替你定，你要自己挑就说一声，摊开让你勾。
    #     ⚠️ 只认"敲定出来的那条"（`auto` 标记）：学生自己报了钟点换出来的条
    #     不该再被摊一堆候选盖掉——他已经说清楚要哪个点了。
    held_auto = _pending_todo(session_id)
    if held_auto and held_auto.get("auto") and not is_confirmation(message) \
            and _SELF_PICK_RE.search(message or ""):
        slots = todo_slots_proposal(
            f"{held_auto.get('date') or ''} {held_auto.get('title') or ''}".strip())
        if slots is not None:
            return _offer_response(
                session_id, slots, message,
                _slots_answer(slots, lead="🙋 好，那时间你自己挑——"),
                "🙋 他要自己挑 → 摊出候选时段（让打勾）")

    # 3c-pre5) **语义兜底**：上面那些规则一条都没接住 → 先查样本库、再问一次意图。
    #     学生原话：「**很多的字都是接不住的，你现在能接住的都是我测试给的**」。
    #     词表是穷举不完的（planner.py 30 多张表、router 50 多个词，全是子串匹配），
    #     所以规则落空时**不再直接掉给模型自由发挥**，而是先问 app/agent/intent.py：
    #       ③ 样本库里有没有"以前成功办过的近似说法"（零成本、离线可用）；
    #       ② 没有再问一次 LLM，**只读意图**（它拿不到写库接口，排期/出卡/写库
    #          仍在系统这侧，见那个文件的分层图）。
    #     只有规则真接不住才走到这儿，常规说法零 API 调用、零延迟。
    #     两道都没定 → `_sem` 留空，照旧掉给模型（现状行为，不倒退）。
    _sem = None
    _sem_intent = None
    # 「规则判了 add_todo，可这句话里带着明显的查询词」→ 让语义层**复核**一次。
    #     实测那一幕：学生说「我现在都有啥安排」，`_ADD_INTENT` 里有"安排"两个字，
    #     子串匹配分不清这里的"安排"是**名词**（日程安排）还是动词（帮我安排），
    #     于是判成下单，卡片上会写着「我现都有啥」——他想查待办，系统却要给他加一条。
    #     这不是"再补一条规则"能解决的（多义词是关键词表的天生盲区），
    #     所以交给语义层复核：**只有当它跟规则结论不一致时**才否决规则，
    #     判得一样就照旧走，不额外改变任何行为。
    _rule_hit = _rule_intent(message)
    _rule_add_veto = False
    _need_review = (_rule_hit == INTENT_ADD_TODO
                    and _ASKING_RE.search(message or ""))
    if (_rule_hit is None or _need_review) and not is_confirmation(message):
        try:
            _sem = await understand(message, get_conversation(session_id))
        except Exception:
            _sem = None          # 语义层挂了也不能让对话崩，掉回老路
        _sem_intent = (_sem or {}).get("intent") or None
        if _need_review and _sem_intent and _sem_intent != INTENT_ADD_TODO:
            _rule_add_veto = True

    def _sem_done(resp: dict | None) -> dict | None:
        """语义层命中、并且真的出了卡 → 把这句原话学进样本库（越用越懂）。

        只在**办成了**的时候记。模型自由发挥那一句不算——那会把错误意图学进去。
        """
        if resp is not None and _sem_intent:
            try:
                remember(message, _sem_intent, _sem or {})
            except Exception:
                pass
        return resp

    # 3d) **读我已有待办（纯读，不写库、不弹窗）**
    #     学生说「我的代办呢」「看看我的代办」「待办列表」「代办显示不出来」这类话，
    #     没有任何"加/删"意图，就是想看一眼现在有哪些待办。
    #     规矩跟别处一致：能由系统算死的就别交给模型——模型会**凭空编**一堆
    #     "你目前有 3 条待办"（其实一个字都没读库）。这里直接 list_todos() 真查，
    #     查到什么说什么；一条都没有也如实讲。
    #     ⚠️ 位置必须在 3c-ter 之前：「代办显示不出来」既像"看不见 X"、又该走"读列表"，
    #     但学生真正要的是"你把我的待办念给我听"——读出来比一句"确实还没有"更有用。
    if wants_list_todo(message) or _sem_intent == INTENT_LIST_TODO:
        todos = list_todos()
        if not todos:
            answer = ("📋 你目前还没有任何待办。\n"
                      "说一句「加个游泳，周四 19:00-20:30」，我就给你出确认条。")
        else:
            todos_sorted = sorted(
                todos,
                key=lambda x: (x.get("date") or "9999-99-99", x.get("start") or "00:00"))
            lines = [f"📋 你目前的待办（共 {len(todos)} 条）："]
            for t in todos_sorted:
                wd = t.get("weekday") or _weekday_name(t.get("date") or "")
                lines.append(
                    f"· {t.get('title', '')}｜{t.get('date', '')}"
                    f"（{wd}）{t.get('start', '')}-{t.get('end', '')}")
            answer = "\n".join(lines)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", f"已列出待办：{len(todos)} 条")
        return _sem_done({
            "module": "planner",
            "session_id": session_id,
            "answer": answer,
            "trace": [{"step": 1, "phase": "📋 列出待办（系统读取）",
                       "answer": f"{len(todos)} 条"}],
            "options": [],
            "awaiting_choice": False,
        })

    # 3c-ter) **「看不见 X」的报障**——学生不是要加新事，是**问在不在**。
    #     实测那一幕（截图）：学生说「我现在没有看见日程显示周二游泳代办项目啊」，
    #     这句话哪个确定性分支都不认，整句掉给模型，它就开始**编原因**：
    #     「基本可以确定是系统推送出了问题……得让管理端那边看一下」，
    #     还让学生"再刷新一次，二选一"——系统明明能真查库，学生日程操作
    #     又默认自主（场景边界：不提管理端、不说权限不足），这一段全是反面教材。
    #     规矩：系统先**核实**，真查库，然后按查到什么说什么：
    #       ① 待办里有 → 如实告诉他哪天几点（他说的那天没有、别处有时也要讲清）；
    #       ② 课表里有（他找的是课，不是待办）→ 指给他看；
    #       ③ 都没有、但最近说过要加 → 那条**还没写进日程**，确认条补挂出来；
    #       ④ 都没有、最近也没说过 → 如实说"还没有"，要加说一声。
    #     全程不诊断"推送出了问题"、不提管理端、不让学生"二选一"。
    missing = missing_thing_of(message)
    if missing is not None:
        title, day = missing["title"], missing["date"]
        # 他没点名，就拿"最近要加的那件事"当默认答案——十有八九问的就是它
        if not title:
            req0 = _recent_add_request(session_id)
            rt0 = todo_title_of(req0) if req0 else "待办"
            title = rt0 if rt0 != "待办" else ""

        if title:
            todos = [t for t in list_todos()
                     if _planner_name_match(title, t.get("title") or "")]
            here = [t for t in todos if not day or t.get("date") == day]
            # 他说的那天没排、可别处有一条同名的 → 也要讲清（人话：他可能记错天了）
            if not here and todos and day:
                ot = todos[0]
                append_conversation(session_id, "user", message)
                append_conversation(
                    session_id, "assistant",
                    f"已核实：你说的那天没排，别处有一条——{ot.get('summary') or ''}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"👀 你说的{_cn_day(day)}没排这一条，倒是 **{ot.get('date')}"
                        f"（{ot.get('weekday') or _cn_day(ot.get('date') or '')}）** "
                        f"有一条：**{ot.get('title')}** "
                        f"{ot.get('start')}-{ot.get('end')}。\n"
                        "想在那天也排一条，说一句「帮我周二加个游泳」这样的，"
                        "我按新的重排一张。"
                    ),
                    "trace": [{"step": 1, "phase": "👀 查日程（系统核实）",
                               "answer": f"那天没排；{ot.get('summary') or ''} 有一条"}],
                    "options": [],
                    "awaiting_choice": False,
                }
            if here:
                td = here[0]
                wd_name = td.get("weekday") or _cn_day(td.get("date") or "")
                elsewhere = ""
                if day and len(todos) > len(here):
                    ot = todos[len(here)]
                    elsewhere = (f"\n（你说的那天没排，倒是 **{ot.get('date')}"
                                 f"（{ot.get('weekday') or _cn_day(ot.get('date') or '')}"
                                 f"）** 有一条同名的。）")
                append_conversation(session_id, "user", message)
                append_conversation(
                    session_id, "assistant",
                    f"已核实：待办在日程里——{td.get('summary') or ''}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"👀 在的，日程里排着呢：**{td.get('title')}**｜"
                        f"{td.get('date')}（{wd_name}）"
                        f"{td.get('start')}-{td.get('end')}。{elsewhere}\n"
                        "面板没刷出来的话，切一下周表/月表那一格就能看到。"
                    ),
                    "trace": [{"step": 1, "phase": "👀 查日程（系统核实）",
                               "answer": f"待办在：{td.get('summary') or ''}"}],
                    "options": [],
                    "awaiting_choice": False,
                }
            # 不是待办，找的会不会是**课**？（「怎么没有周一的高数」）
            wd = _iso_wd(day) if day else None
            courses = [c for c in ((get_timetable_data() or {}).get("courses") or [])
                       if _planner_name_match(title, c.get("course") or "")]
            here_c = [c for c in courses if not day or c.get("day") == wd]
            if here_c:
                c0 = here_c[0]
                append_conversation(session_id, "user", message)
                append_conversation(
                    session_id, "assistant",
                    f"已核实：那是课表里的课——{DAY_NAMES.get(c0.get('day'), '')} "
                    f"{c0.get('start')}-{c0.get('end')} {c0.get('course')}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"👀 那是**课**，在周表里：{DAY_NAMES.get(c0.get('day'), '')}"
                        f"{c0.get('start')}-{c0.get('end')} "
                        f"{c0.get('course')}@{c0.get('location') or ''}。\n"
                        "周表那一栏直接能看到；要往某天加一条相关的待办，说一声就行。"
                    ),
                    "trace": [{"step": 1, "phase": "👀 查课表（系统核实）",
                               "answer": f"课在：{DAY_NAMES.get(c0.get('day'), '')} "
                                         f"{c0.get('start')}-{c0.get('end')}"}],
                    "options": [],
                    "awaiting_choice": False,
                }

        # 日程里真没有 → 是不是那条提案压根没确认成？最近说过要加的话，补条出来
        req = _recent_add_request(session_id)
        if not req and asking_add and prev_user:
            req = prev_user
        prop = None
        if req:
            prop = parse_add_todo(req)
            if prop is None:
                mc = todo_mode_proposal(req)
                prop = mode_to_card(mc, "ai") if mc else None
        if prop is not None:
            day_note = f"（你说的{_cn_day(day)}）" if day else ""
            return _offer_response(
                session_id, prop, message,
                (f"👀 查了日程{day_note}，**这一条还没写进去**——"
                 "刚才那次没确认成。确认条给你补上了：\n\n"
                 f"要不要把 **{prop['title']}** 排进日程？"
                 f"{prop['date']}（{prop['weekday']}）"
                 f"{prop['start']}-{prop['end']}。\n"
                 "点【确认加入】就写进去，回一句「确认」也一样。"),
                "👀 他说看不见 → 核实确实没写入 → 补挂确认条")

        append_conversation(session_id, "user", message)
        append_conversation(
            session_id, "assistant",
            f"已核实：日程里没有这一条（{title or '没点名'}）")
        return {
            "module": "planner",
            "session_id": session_id,
            "answer": (
                f"👀 查了日程，**确实还没有**"
                f"「{title or '这条'}」{f'（{_cn_day(day)}）' if day else ''}。\n"
                "要加的话说一句「帮我加个什么什么」，我马上排好给你确认。"
            ),
            "trace": [{"step": 1, "phase": "👀 查日程（系统核实）",
                       "answer": "没有这一条，如实说"}],
            "options": [],
            "awaiting_choice": False,
        }

    # —— 「卡片呢 / 怎么没弹卡」：学生在找上一轮的确认卡 ——
    # 实测原话：「卡片呢」——被 router 分给 lostfound（LLM 把"卡"联想成校园卡），
    # 模型还嘴上说"卡片挂出来了"，界面上一张卡都没有。规则：
    #   · 话很短、在找卡 → 暂存里有提案就**原样重挂**（不重新生成、更不写库）；
    #   · 暂存是空的 → 如实说"没有挂着的卡"，绝不让模型凭空编一张。
    _FIND_CARD_WORDS = ("卡片", "弹卡", "卡呢", "没卡", "弹窗", "弹框", "确认条")
    # ⚠️ 别把"报障"当成"找卡"：实测原话「**没有收到弹窗**」里也有"弹窗"两个字，
    # 可它不是在问"上一张卡去哪了"，而是**投诉那张卡压根没生成**——
    # 那一幕的正解在下面的「补条兜底」：从模型那段自相矛盾的话里把 16:00-17:30
    # 捞出来、重新挂一张卡。被这里截走的话，学生只能收到一句"手上没有挂着的卡"，
    # 报障那一幕就又白修了（第十一批测试钉的就是它）。
    _CARD_NEVER_ARRIVED = ("没有收到", "没收到", "收不到", "没弹出来", "没出来",
                           "没有出现", "没出现", "看不见", "没看见")
    if (_sem_intent == INTENT_FIND_CARD
            or (len(message) <= 12 and any(w in message for w in _FIND_CARD_WORDS)
                and "吗" not in message
                and not any(w in message for w in _CARD_NEVER_ARRIVED))):
        _held = peek_pending(session_id) or {}
        _held_opts = [o for o in (_held.get("options") or []) if isinstance(o, dict)]
        append_conversation(session_id, "user", message)
        if _held_opts:
            return _sem_done({
                "module": "planner",
                "session_id": session_id,
                "answer": "📑 卡还在呢——就是下面这条，勾好/点【确认】就办。",
                "trace": [{"step": 1, "phase": "📑 学生找卡 → 原样重挂暂存里的提案",
                           "answer": _held.get("summary") or "已重挂"}],
                "options": _held_opts,
                "awaiting_choice": True,
            })
        return _sem_done({
            "module": "planner",
            "session_id": session_id,
            "answer": ("📑 现在手上没有挂着的卡。说一句「加个什么什么」"
                       "（比如「我要周六去吃火锅」），我马上把确认条给你挂出来。"),
            "trace": [{"step": 1, "phase": "📑 学生找卡 → 暂存为空，如实说",
                       "answer": "暂存里没有提案"}],
            "options": [],
            "awaiting_choice": False,
        })

    if (not _rule_add_veto
            and (wants_add_todo(message) or (asking_add and is_add_todo_answer(message)))
            or _sem_intent == INTENT_ADD_TODO):
        # 追问后的补充回答（学生先说"帮我安排周二的游泳"，再补"下午两点到三点"）：
        # 补充那句里没有标题、也没有日期，单独解析会把标题弄丢——
        # 把上一句原话拼回来一起算。识别标记就是下面追问分支写进会话历史的那句"加待办缺细节"。
        # 语义层那一路（`_sem_intent`）：它把口语翻成了"加个吃火锅，76分钟，工作日"
        # 这种**解析器认得的规范话**（`to_canonical`），所以照旧喂给同一套老解析，
        # 不用再抄一份"从 JSON 造卡片"的逻辑。
        if _sem_intent == INTENT_ADD_TODO and _sem:
            blob = to_canonical(message, _sem)
        else:
            blob = (prev_user + "，" + message) if (asking_add and prev_user) else message
        proposal = None
        if asking_add and prev_user:
            proposal = parse_add_todo(prev_user + "，" + message)
        if proposal is None:
            proposal = parse_add_todo(blob)
        if proposal is not None:
            # 学生**自己报了准点** → 就着他给的这个点出条，一条就够。
            # 不必再摊候选：他已经说清要哪个点了，再摊三个等于让他重挑一遍。
            return _sem_done(_offer_response(
                session_id, proposal, message,
                (f"📝 要不要把 **{proposal['title']}** 排进日程？"
                 f"{proposal['date']}（{proposal['weekday']}）"
                 f"{proposal['start']}-{proposal['end']}。\n"
                 f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。"),
                "📝 生成待办提案（系统判定）"))

        # 学生没说时间（「那你帮我加一个健身在周四」「加个健身」「帮我加个游泳」）。
        #
        # 这一档的规格**改过两次**，三版都记在这儿，免得后人再翻回去：
        #   · 第一版：系统**替他挑一个**时间、出一张"就排在这儿了"的单条确认条。
        #     理由是"不许反问学生几点"（投诉原话：「没有帮我想时间，是我问了才说的」）。
        #     方向对，但做法太死——学生回：
        #     「**我定的太严了，你改一下，由 ai 帮我去挑选合适时间，进行列举**……
        #      由我打勾，进行增加」。
        #   · 第二版：**摊开几个空档让他自己打勾**（todo_slots 卡 + /api/todos/batch）。
        #     这回方向也对了，但又漏了学生后半句：
        #     「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
        #      不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定）**」。
        #     不分这两种，一律先摊候选 —— 对一个"随便，你看着办"的学生，
        #     摊三个时间段等于又把决定权推回去了。
        #   · **这一版**：先出**二选一卡**（todo_mode，什么都不定），
        #     学生点"我自己定" → 候选卡（勾 / 其他自填）；
        #     点"你帮我挑" → 系统真去规划一段（`plan_todo_slot`，卡片上写清为什么排这儿）。
        #
        # 不变的两条铁律：
        #   ① 这里**只出提案，一个字节都不写库**；挑不出来的照样如实说，绝不硬凑一个时间。
        #   ② 反问学生"几点到几点"依然禁止——他去查空档是他的正事。
        #
        # ⚠️ 还有第三件必须做的事：**先确认他真说了"要做什么事"**。
        #    学生原话：「**要识别啥才是真的事情，不是随便拿那一句话就去当代办加入日程了**」。
        #    实测「大概一个小时帮我安排时间」被 `_pick_title` 抠成「大概小时帮我时间」，
        #    还照样挂出了一张候选卡让他勾时间——可连要做什么都还不知道，挑时间往哪写？
        #    `todo_mode_proposal` 里已经过了 `_pick_title` 的判真关，判不出事名返回 None，
        #    就落到下面的追问分支去问"要安排什么事"。
        # ⚡ 第六轮改版（学生原话：「**不需要先问，直接去安排，重复的确认太麻烦，
        #    直接敲定结果，主打效率**」）：以前这里先出一张"你自己定 / 我帮你挑"的
        #    二选一卡（"要区分两种"是第十批的规格），可真用起来，**这一轮问答本身
        #    就是他嫌麻烦的东西**——他要的是结果。现在直接敲定：
        #    `todo_mode_proposal` 照旧算（它里头有过标题判真和时长解析，丢不得），
        #    但**不再把二选一卡递出去**，就地走"你帮我挑"那条路——
        #    系统从真实空档里挑一段，出一张"就排在这儿"的单条确认条，
        #    卡上写清为什么排这儿、想换怎么说。写库仍要他点【确认加入】——
        #    效率提上去，一步都不越权。
        #    直接挑不出（那几天都排满）→ 退回候选卡让他勾；连事名都判不出
        #    → 才落到下面的追问。每一级都比上一级多问一句，能不问就不问。
        # ⚡ 第七轮改版（学生原话：「**现在就是在时间安排上，将多种合理时间提出让我挑选，
        #    也是弹窗里进行选择**」，见 2026-09-25 对话）：第六轮的"系统先替他敲定唯一一个"
        #    又被打回了——学生要的是**从多个合理时段里自己挑**，不是系统替他定。
        #    所以把主路径从 `mode_to_card(mode_card, "ai")`（单条）换成
        #    `mode_to_card(mode_card, "self")`（多段候选卡）：系统只负责列出空档、
        #    学生自己打勾（可勾多个、可"其他时间"自填），写库仍走 /api/todos/batch，
        #    一步不越权。只有当**连一个空档都排不出**（那几天全满）时，
        #    才退回第六轮的"系统单条 AI 敲定兜底"。每一级都比上一级少替学生做主。
        # 这一句里没说清要做什么，但学生最近说过 → 从上下文把事名**继承**过来。
        # 实测那一幕（截图）：学生说「我想去游泳，帮我安排时间」，管家客气了一轮
        # 「你想安排在哪天？还是我直接帮你按本周的空闲时间找找？」，学生回
        # 「这周安排一个时间」——这句话本身没有"游泳"，系统却反问他
        # 「要安排的是什么事」。他刚刚才说过！规则（学生原话）：
        # 「**禁止反复追问、不要多余客套啰嗦**」「人性化自动检索，直接给方案」。
        # 继承源用 `_recent_add_request`：它要求那句原话本身带着"安排/加"的意图、
        # 不是确认词、且最近没真的写过库——不会把闲聊里随便一个词抓来当事名
        # （「要识别啥才是真的事情」这条红线不破）。
        if todo_title_of(blob) == "待办":
            inherited = _recent_add_request(session_id)
            if inherited:
                inh_title = todo_title_of(inherited)
                if inh_title != "待办":
                    merged = f"{inherited}，{message}"
                    # ⚠️ 合并完还得核对一次：抠出来的必须**还是这一个事名**。
                    # 不核对的话，「帮我安排一下健身」+「再安排一个」会糊成
                    # 「健身再」——两句话被揉成一个谁也没说过的怪名字挂出去。
                    # 对不上就说明这一句不是在接着上一句说（可能是另一件事），
                    # 那就别硬凑，照旧问清楚。
                    if todo_title_of(merged) == inh_title:
                        blob = merged

        mode_card = todo_mode_proposal(blob)
        if mode_card is not None:
            # 本轮（第七轮）规格：学生没定时间 → **先摊开多个候选时段让他自己挑**，
            # 他要的就是"多种合理时间提出让我挑选，在弹窗里选"（见 2026-09-25 对话）。
            # 不再由系统先替他敲定唯一一个——上一版那样等于又替他做了主，来回换。
            # 能列出空档就出候选卡（多段、可勾多个、可"其他时间"自填）；
            # 实在一个空档都排不出（那几天全满）→ 才退回系统单条 AI 敲定兜底。
            # 写库仍要他点【加入日程】，一步不越权。
            slots = mode_to_card(mode_card, "self")
            if slots is not None:
                # 开场那句带上学生自己选的性格腔调（没选就退回下面这句默认引导语）
                _lead = _slots_lead(persona_key) or (
                    "⏰ 给你找了几个空着的时间段，挑方便的勾上（可勾多个）：\n\n")
                return _sem_done(_offer_response(
                    session_id, slots, message,
                    _slots_answer(slots, lead=_lead),
                    "🙋 学生没定时间 → 摊出多个候选时段（让他打勾挑选，本轮规格）"))
            # 连一个空档都排不出来（那几天全满）→ 退回系统单条 AI 敲定兜底
            picked = mode_to_card(mode_card, "ai")
            if picked is not None:
                return _sem_done(_offer_response(
                    session_id, picked, message,
                    (f"⏰ 直接帮你定好了：**{picked['title']}** 排在 "
                     f"**{picked['date']}（{picked['weekday']}）"
                     f"{picked['start']}-{picked['end']}**。\n"
                     f"{picked.get('reason') or ''}\n"
                     "点【确认加入】就写进日程。这个点不合适，说一句"
                     "「改成周四晚上七点到八点」这样的话，我按新的重出一张。"),
                    "⚡ 候选空档都排不出 → 系统单条 AI 敲定兜底"))

        # 出不了卡。两类情况，话术不能混为一谈（todo_missing_advice 分得清）：
        #   ① 学生根本没说要加什么事（只有时长、或只有"帮我安排"）→ 问他要加什么；
        #   ② 学生说了哪天、可**那天真排不进**（候选/规划都试过了）
        #      → 要如实说"这天满了"，别让他一遍遍补"几点几点"。
        #
        # 也**不能**把话交给模型——实测它会回"搞定！已经正式写进你的待办啦"，
        # 日程里其实什么都没有（schedule 模块连一个写入工具都没有，纯属嘴甜）。
        # 照删课的规矩：缺什么就追问什么，标记"加待办缺细节"写进历史，
        # 学生下一句补充由上面的合并逻辑接着算。
        #
        # ⚠️ 这个标记只用于**内部接续**（下一轮把上一句原话拼回来一起解析），
        #    它本身**绝不能出现在学生看到的回答里**。学生截屏投诉过一句光秃秃的
        #    「加待办缺细节」——那是内部状态码漏到了对话框里，他完全不知道要干嘛。
        #    学生看到的永远是 `todo_missing_advice` 拼出来的人话。
        clear_pending(session_id)
        missing = pick_todo_missing(blob)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", "加待办缺细节")
        return {
            "module": "planner",
            "session_id": session_id,
            "answer": todo_missing_advice(blob),
            "trace": [{"step": 1, "phase": "🤔 加待办缺细节（系统追问）",
                       "answer": f"缺{missing}，先问清楚再出提案"}],
            "options": [],
            "awaiting_choice": False,
        }

    # 3c-bis) **确定性加课分支**（人话：学生说"周一加一节体育，19:00-20:40，体育馆"）
    #     跟加待办同一个道理——配了密钥之后跑的是真模型，模型最爱说"已经加上了"，
    #     结果周表里什么都没多。这里由系统算出新课表（整表替换的提案），
    #     卡片上点【确认】才写入，学生看得见自己点头了什么。
    if wants_add_course(message):
        proposal = parse_add_course(message)
        if proposal is not None:
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已生成加课提案：{proposal.get('summary', '')}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"🗓️ 要不要往周表里加一节？\"{proposal.get('summary', '')}\"。"
                    f"下面点一下【确认】我才写；点【取消】周表原封不动。"
                ),
                "trace": [{"step": 1, "phase": "🗓️ 生成加课提案（系统判定）",
                           "answer": str(proposal.get("summary") or "")}],
                "options": [proposal],
                "awaiting_choice": True,
            }
        # 算不出是哪一天（比如学生只说"加一节毛概，10:00 到 11:40"）。
        # 这种情况下**不能**把话交给模型——它多半回一句"已经加上了"，学生再去周表里找。
        clear_pending(session_id)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", "加课缺星期")
        return {
            "module": "planner",
            "session_id": session_id,
                "answer": (
                    "这门课要加在**星期几**？把星期说一下（比如「周三加一节《毛概》，"
                    "10:00-11:40」），我算出新课表给你确认，你点头我才写进周表。"
                ),
            "trace": [{"step": 1, "phase": "🤔 加课缺星期（系统追问）",
                       "answer": "学生没说星期，先问清楚再出提案"}],
            "options": [],
            "awaiting_choice": False,
        }

    # 3g) **确定性删待办分支**（规格：跟删课同一套规矩——先列清楚，再动手，学生点头才算数）
    #
    # 为什么必须由系统接管：删待办**不可逆**，而模型手里原先有一个真能删的 remove_todo，
    # 规矩只写在提示层（"必须先确认"）。提示是软的，模型口语一变就可能绕过去——
    # 学生没点头就删掉，找不回来。所以两件事一起做：
    #   ① 写工具从工具箱里撤掉（模型只能拿到提案，删不动库）；
    #   ② 判定和列清单也收归系统，三种情况各有各的走法：
    #      · 点名了唯一一条 → 出确认条（原样念一遍标题+日期+时段），等学生点头
    #      · 命中好几条 / 只说"明天那条" → **列清单问是哪一条，列完就停**，绝不猜
    #      · 一条都没命中 → 如实说，绝不编一条出来删
    # 注意这一段**必须排在删课（3f）前面**：学生说"把周二那条**待办**删掉"也带了"周二"，
    # 让删课先接就会跑到课表那条路上追问"想删哪一节"——问错人了。
    # ⚠️ "学生在回答是哪一条吗" 只看**紧挨着的上一句**。
    # 早先写成"最近 4 条 assistant 里有这个标记就算"，结果标记会一直赖在窗口里：
    # 学生先删了一条待办、隔两轮又说「去掉周二的高数」，这句话还会被当成"在挑待办"，
    # 于是落到"没找到这条待办、一条都没删"——**课表那条路压根没机会接手**。
    # 判据收紧成"上一句就是我那句追问"，就不存在这个串味问题。
    _last_bot = next((m.get("content") or "" for m in reversed(get_conversation(session_id))
                      if m.get("role") == "assistant"), "").strip()
    asking_rm_todo = _last_bot == "删待办缺细节"
    rm_intent = (wants_remove_todo(message) or wants_remove_todo_loose(message)
                 or _sem_intent == INTENT_REMOVE_TODO)
    if rm_intent or asking_rm_todo:
        # 学生是不是在回答"是哪一条"（回一句"第二条"或"游泳那条"）
        followup = asking_rm_todo and not rm_intent
        held = _pending_remove_candidates(session_id)
        if followup:
            res = resolve_todo_remove_reply(message, held)
        else:
            res = resolve_todo_remove(message)
        status = res.get("status")

        if status == "ok":
            proposal = res["proposal"]
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant",
                                f"已生成删待办提案：{proposal['summary']}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"🗑️ 要删的是这一条：**{proposal['title']}**｜{proposal['date']}"
                    f"（{proposal['weekday']}）{proposal['start']}-{proposal['end']}。\n"
                    "确认条就在下面这条消息里，点【确认删除】我才删；"
                    "点【取消】日程原封不动。"
                    "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 生成删待办提案（系统判定）",
                           "answer": proposal["summary"]}],
                "options": [proposal],
                "awaiting_choice": True,
            }

        # 命中好几条：**列清单，问是下面哪一条，列完就停**。
        # 这一支就是规格第 2 条——"不许猜"。清单同时记进暂存，
        # 学生回"第二条"时系统接得住（见上面的 resolve_todo_remove_reply）。
        if status in ("many", "unclear") and res.get("todos"):
            todos = res["todos"]
            save_pending(session_id, [{"kind": "todo_remove_candidates",
                                       "todos": todos, "summary": "待删候选"}])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "删待办缺细节")
            head = ("你说的这条对上了好几条待办，我不敢替你挑 🗑️"
                    if status == "many" else "没太看明白你指哪一条 🗑️")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"{head}\n\n{render_todo_remove_list(todos)}\n\n"
                    "是下面哪一条？回我一个名字（或序号，比如「第二条」），"
                    "我再把确认条挂出来——**你点头之前我一条都不会删。**"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 删待办命中多条（列清单，不猜）",
                           "answer": f"候选 {len(todos)} 条，等学生点名"}],
                "options": [],
                "awaiting_choice": False,
            }

        # 一条都没命中：如实说。绝不从"没找到"里编一条出来删。
        #
        # 但有一类句子要放行：它其实是在说**课**（"去掉周二的高数"），只是
        # 顺手触发了删待办的开关。这种情况不能抢答"我没找到这条待办"——
        # 那会让课表那条路（3f）压根没机会接手，学生只会觉得"我想删课，它跟我扯待办"。
        # 放它过去，交给删课分支去追问/出卡。
        if not wants_remove_course(message):
            clear_pending(session_id)
            day = res.get("day")
            where = f"{day}（{_weekday_of(day)}）" if day else ""
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "删待办：没找到符合条件的")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"🗑️ 我在{where or '日程里'}没找到叫这个的待办，一条都没删。\n"
                    "你可以：① 说得再具体点（哪一天 + 叫什么）；"
                    "② 直接问我「那天排了什么」，我列给你看。"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 删待办没找到（如实说，不猜）",
                           "answer": "没有匹配的待办，什么都不删"}],
                "options": [],
                "awaiting_choice": False,
            }

    # 3f) **确定性删课分支**：跟清空/加课同一个思路，"删除周一第一节课"这种话
    #     学生说得明明白白，剩下"新课表长什么样"该由服务端算，不该赌模型调不调工具。
    #     实测真模型会**只在文字里给个预览、让学生回「确认」**，可它压根没调出提案工具
    #     ——暂存里什么都没有，学生回了确认也是白回：弹窗不来、确认不删、删除失败。
    #
    #     两种进路：
    #     a) 这句话本身带着删课意图（"删除周一第一节课"）；
    #     b) **系统上一轮刚追问过"想删哪一节"**，学生补一句"周一的第一节"——
    #        这句话里没有"删除"两个字，wants_remove_course 认不出来，
    #        但它是对追问的回答，必须接着处理。不接住的话又掉回模型，
    #        模型会嘴上说"删课提案已经生成啦"（根本没生成），弹窗照样不来。
    #        识别标记就是追问分支写进会话历史的那句"删课缺细节"。
    recent = [m for m in get_conversation(session_id)[-4:] if m.get("role") == "assistant"]
    asking_remove = any("删课缺细节" in (m.get("content") or "") for m in recent)
    if wants_remove_course(message) or asking_remove:
        proposal = parse_remove_course(message)
        if proposal is not None:
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已生成删课提案：{proposal.get('summary', '')}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"🗑️ 要删的是这一节：\"{proposal.get('summary', '')}\"。"
                    f"确认条就在下面这条消息里，点【确认变更】我才写；"
                    f"点【取消】周表原封不动。"
                ),
                "trace": [{"step": 1, "phase": "🗑️ 生成删课提案（系统判定）",
                           "answer": str(proposal.get("summary") or "")}],
                "options": [proposal],
                "awaiting_choice": True,
            }
        # 解析不出来（没说星期 / 当天好几节课没说哪节）→ 系统追问，不交给模型
        clear_pending(session_id)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", "删课缺细节")
        return {
            "module": "planner",
            "session_id": session_id,
            "answer": (
                "想删哪一节，说得更具体一点：\n"
                "· 说星期 + 节次，比如「删除周二第二节」；\n"
                "· 或直接说课名，比如「去掉周五的心理学选修」。\n"
                "我算出新课表给你确认，你点头我才写进周表。"
            ),
            "trace": [{"step": 1, "phase": "🤔 删课缺细节（系统追问）",
                       "answer": "没说清是哪节，先问清楚再出提案"}],
            "options": [],
            "awaiting_choice": False,
        }

    # 4) 组装引擎并跑 ReAct 循环（会按需调用工具）
    engine = AgentEngine(system_prompt=prompt, tools=tools, session_id=session_id)

    # 之前聊过的内容（让 Agent 记得住上一轮商量到哪了）
    history = get_conversation(session_id)

    # 规划模块 + 没配密钥时，走确定性的离线 Mock 助手。
    # 这样「查空档 → 给候选 → 学生勾选 → 写入」这套交互不用大模型也能完整演示，
    # 评委没看到密钥也不影响看效果。
    if module_key == "planner" and not get_llm_config()["api_key"]:
        result = mock_planner(message, history, persona_key)
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", result["answer"])
        return {
            "module": module_key,
            "session_id": session_id,
            "answer": result["answer"],
            "options": _remember_todo_options(session_id, result.get("options", [])),
            "awaiting_choice": result.get("awaiting_choice", False),
            # 离线 Mock 没有真实思考轨迹；学生端也本就不展示轨迹
            "trace": [],
        }

    # 大模型可能因为"没配 Key / Key 无效 / 网络不通"失败。
    # 与其让前端收到一个看不懂的 500，不如把原因说成人话，直接显示在对话里。
    try:
        result = await engine.run(message, history=history)
    except LLMError as e:
        # 已知原因：密钥或网络问题，提示用户怎么补救
        return {
            "module": module_key,
            "session_id": session_id,
            "answer": f"⚠️ {e}",
            "trace": [{"step": 1, "phase": "❌ 调用大模型失败", "answer": str(e)}],
        }
    except Exception as e:  # 兜底：任何意外都不该让演示现场崩掉
        if DEBUG:
            import traceback
            traceback.print_exc()  # 开了 DEBUG 就打印完整堆栈，方便排查
        return {
            "module": module_key,
            "session_id": session_id,
            "answer": f"⚠️ 助手处理时出了点问题：{type(e).__name__}：{e}",
            "trace": [{"step": 1, "phase": "❌ 内部错误", "answer": f"{type(e).__name__}: {e}"}],
        }

    # 模型这轮**没调工具**、只在文字里念了方案（甚至自称"提案已发出"）→ 系统替它补条。
    #
    # 这就是"没有收到弹窗"那一幕（学生截屏投诉）：
    #   学生说的不是"确认"（"没有收到弹窗"），所以 3a-c 那一支**根本不会跑**；
    #   模型这轮又忘了调 propose_todo_tool，只在文字里写了
    #   「**待办提案** - 事项：游泳 - 时间：周二 16:00~17:30 … 提案已发出，请点确认条上的【确认】」
    #   界面上于是连一个按钮都没有 —— 学生看到的是"它说发了，可我没收到弹窗"。
    #
    # 3a-c 那条兜底（捞方案补条）以前**只挂在"学生回确认"那一支里**，
    # 于是同一件事从别的入口进来就没人接。这里把它接到模型这一路上：
    # 只要它自称发过提案，系统就照它的意思把卡补出来（写库仍要学生点按钮）。
    model_opts = list(result.get("options") or [])
    final_answer = result["answer"]
    final_trace = result["trace"]
    if not model_opts:
        recent_add_req = _recent_add_request(session_id)
        rescued = rescue_proposal_from_reply(result["answer"], recent_add_req)
        if rescued is not None:
            model_opts = [rescued]
            # 它那段文字在这一刻是自相矛盾的（前面说"还没生成出来"、后面说"已发出"），
            # 整段换成系统话术，别让学生去猜哪句是真的。
            final_answer = _rescue_answer(rescued)
            final_trace = list(result["trace"] or []) + [{
                "step": 1,
                "phase": "🛟 模型没调工具 → 系统从它的话里补出确认条",
                "answer": rescued.get("summary", ""),
            }]
        elif claims_proposal_sent(result["answer"]) and (
                has_concrete_span(result["answer"]) or recent_add_req):
            # 它自称"已经提交/已经加入"了，可方案**捞不出来**（正文里没有可认的
            # 任务名或时间），系统补不出卡。这句话留着比没有更坏 ——
            # 学生会以为安排好了、跑去等，而库里一条都没有。
            # 删不掉这份假话，那就**如实纠正**并告诉他下一步怎么说。
            #
            # 两道闸门（纠正是有代价的——会把一段本来没问题的回答换掉）：
            #   · 它这段话里得带**具体时段**（`08:00~09:00`），
            #     否则"你点确认后系统才会入库"这种流程解释也会被误伤；
            #   · 或者学生最近确实说过要加什么（那这句"提交了"多半是在说他那件事）。
            final_answer = _no_card_answer()
            final_trace = list(result["trace"] or []) + [{
                "step": 1,
                "phase": "⚠️ 模型自称已提交但系统补不出卡 → 如实纠正，不让学生空等",
            }]

    # 写进会话历史的是**学生最终看到的那一句**（补条之后的话术）。
    # 以前这一步在补条之前，结果刷新页面看到的是模型那句"提案已发出"（底下没按钮），
    # 而本次会话里看到的是系统补出来的条——两边对不上。
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", final_answer)

    # 4) 返回最终回答 + 思考轨迹（学生端不展示轨迹，但规划模块的候选选项要带回前端）
    return {
        "module": module_key,
        "session_id": session_id,
        "answer": final_answer,
        "trace": final_trace,
        "options": _remember_todo_options(session_id, model_opts),
        "awaiting_choice": result.get("awaiting_choice", False) or bool(model_opts),
    }


# ============ 管理员侧接口（发布通知 + 接收学生消息） ============

@app.post("/api/student-message")
async def student_message(req: Request):
    """学生端每轮对话后调用，把消息记下来，供管理员接收（人话：学生说的话先存档）。"""
    body = await req.json()
    item = add_message(body.get("module", ""), body.get("message", ""), body.get("answer", ""))
    return {"ok": True, "id": item["id"]}


@app.get("/api/admin/messages")
async def admin_messages():
    """管理员端拉取学生消息收件箱（倒序，最新在前）。"""
    return {"messages": list_messages()}


# ============ 工单（学生确认上报的问题 → 管理端可见的正式记录） ============

@app.post("/api/workorders")
async def workorder_create(req: Request):
    """提交一条工单（人话：面板表单提交的；聊天里由助手调 submit_work_order 工具落库）。"""
    body = await req.json()
    kind = (body.get("kind") or "").strip()
    if not kind:
        return JSONResponse({"error": "问题类型不能为空"}, status_code=400)
    item = add_workorder(kind=kind, desc=(body.get("desc") or "").strip(),
                         source=body.get("source") or "panel")
    return {"ok": True, "workorder": item}


@app.get("/api/workorders")
async def workorder_list():
    """管理端查看全部工单（倒序）。"""
    return {"workorders": list_workorders()}


@app.post("/api/workorders/{wo_id}/status")
async def workorder_set_status(wo_id: str, req: Request):
    """管理员处理工单后更新状态（待处理/处理中/已解决）。"""
    body = await req.json()
    status = (body.get("status") or "").strip()
    if status not in ("待处理", "处理中", "已解决"):
        return JSONResponse({"error": "status 只能是 待处理/处理中/已解决"}, status_code=400)
    item = update_workorder_status(wo_id, status)
    if not item:
        return JSONResponse({"error": "工单不存在"}, status_code=404)
    return {"ok": True, "workorder": item}


@app.post("/api/admin/notify")
async def admin_notify(req: Request):
    """管理员发布一条通知，可以挂一个附件（人话：比如把放假安排表一起发出去）。"""
    body = await req.json()
    title = (body.get("title") or "").strip()
    content = (body.get("content") or "").strip()

    # 附件：前端会把 /api/upload 返回的 {id, name} 传过来。
    # 这里要校验 id 真的存在，不能让管理员挂上一个查无此文件的"幽灵附件"。
    attachment = None
    raw_attach = body.get("attachment")
    if raw_attach and raw_attach.get("id"):
        meta = get_upload_meta(raw_attach["id"])
        if not meta:
            return JSONResponse({"error": "附件不存在或已被删除，请重新上传"}, status_code=400)
        attachment = {"id": meta["id"], "name": meta["name"]}

    # 允许"只有附件"的通知（比如直接甩一份表格），所以三种内容有一个就行
    if not (title or content or attachment):
        return JSONResponse({"error": "标题、内容、附件至少要填一个"}, status_code=400)

    item = add_notice(title, content, attachment)
    return {"ok": True, "notice": item}


@app.get("/api/notices")
async def notices():
    """列出已发布通知（人话：学生端以后也能看，这里先把接口备好）。"""
    return {"notices": list_notices()}


# ============ 管理控制台：知识库零代码维护 ============

@app.get("/api/admin/kb")
async def admin_kb_list():
    """列出知识库全部条目（人话：管理台知识库表格的数据源）。"""
    return {"entries": list_kb()}


@app.post("/api/admin/kb")
async def admin_kb_add(req: Request):
    """新增一条知识库条目。"""
    body = await req.json()
    q = (body.get("question") or "").strip()
    a = (body.get("answer") or "").strip()
    if not q or not a:
        return JSONResponse({"error": "问题和答案不能为空"}, status_code=400)
    kws = [k.strip() for k in (body.get("keywords") or "").split(",") if k.strip()]
    item = add_kb_entry(q, a, kws)
    return {"ok": True, "entry": item}


@app.put("/api/admin/kb/{index}")
async def admin_kb_update(index: int, req: Request):
    """按序号修改一条知识库条目。"""
    body = await req.json()
    q = (body.get("question") or "").strip()
    a = (body.get("answer") or "").strip()
    kws = [k.strip() for k in (body.get("keywords") or "").split(",") if k.strip()]
    try:
        item = update_kb_entry(index, q, a, kws)
    except IndexError:
        return JSONResponse({"error": "条目不存在"}, status_code=404)
    return {"ok": True, "entry": item}


@app.delete("/api/admin/kb/{index}")
async def admin_kb_delete(index: int):
    """按序号删除一条知识库条目。"""
    ok = delete_kb_entry(index)
    if not ok:
        return JSONResponse({"error": "条目不存在"}, status_code=404)
    return {"ok": True}


# ============ 管理控制台：客服人格配置 ============

@app.get("/api/persona")
async def persona_get():
    """读取当前客服人格。"""
    return {"persona": get_persona()}


@app.post("/api/persona")
async def persona_set(req: Request):
    """保存客服人格（人话：管理员改了客服人设，下次对话立即生效）。"""
    body = await req.json()
    name = (body.get("name") or "").strip() or "小园"
    role = (body.get("role") or "").strip()
    tone = (body.get("tone") or "").strip()
    rules = (body.get("rules") or "").strip()
    item = set_persona(name, role, tone, rules)
    return {"ok": True, "persona": item}


@app.get("/api/student-personas")
async def student_personas_get():
    """学生端可选的三套助手性格（人话：学生自己挑跟哪种腔调的助手聊天）。

    这是**学生助手**的性格，和驿站侧管理员配的客服人格（persona.json）是两回事。
    """
    return {"personas": STUDENT_PERSONAS}


# ============ 文件上传（给 AI 助手读资料 / 给通知挂附件） ============

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """接收一个上传文件，提取成文字存档（人话：让用户把资料交给 AI 读）。

    同时保留原件，这样通知的附件还能让学生下载回去。
    """
    data = await file.read()
    # 三道校验：空文件、过大、不支持的格式，都要给出说得清楚的提示
    if not data:
        return JSONResponse({"error": "这个文件是空的"}, status_code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES // 1024 // 1024
        return JSONResponse({"error": f"文件太大了，单个上限 {mb}MB"}, status_code=413)
    if not ext_supported(file.filename):
        # 用 unsupported_reason 而不是写死的文案：老版 Office 会得到
        # 「另存为 .docx」这种能照着做的指引，而不是一句笼统的"不支持"
        return JSONResponse(
            {"error": unsupported_reason(file.filename)},
            status_code=415,
        )

    try:
        text = extract_text(file.filename, data)
    except UploadError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    item = save_upload(file.filename, text, raw=data)
    return {"ok": True, "file": item}


@app.get("/api/uploads")
async def uploads_list():
    """列出上传到过的文件（倒序）。"""
    return {"files": list_uploads()}


@app.get("/api/uploads/{fid}")
async def uploads_content(fid: str):
    """查看某个文件提取出的文字（人话：AI 眼里的这份资料长什么样）。"""
    content = get_upload(fid)
    if content is None:
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    meta = get_upload_meta(fid) or {}
    return {"id": fid, "name": meta.get("name", ""), "content": content}


@app.get("/api/uploads/{fid}/download")
async def uploads_download(fid: str):
    """下载文件原件（人话：通知附件要能原样下载回去）。"""
    path = get_upload_raw_path(fid)
    if not path:
        return JSONResponse({"error": "文件不存在，或当时没有保留原件"}, status_code=404)
    meta = get_upload_meta(fid) or {}
    return FileResponse(path, filename=meta.get("name") or "file")


# ============ 管理控制台：在线配置大模型 API ============

def _mask(key: str) -> str:
    """把密钥打码后再给前端（人话：只露前后几个字符，中间用星号盖住）。

    密钥是敏感信息：网页可能会被人瞄到、也可能被浏览器插件抓走，
    所以"够不够返回"这件事宁可保守——管理员不需要看到完整 Key 才能改 Key。
    """
    if not key:
        return ""
    if len(key) <= 10:
        return key[0] + "*" * (len(key) - 1)
    return f"{key[:6]}{'*' * 8}{key[-4:]}"


@app.get("/api/admin/settings")
async def settings_get():
    """读取当前生效的 API 配置。注意：密钥是**打码后**返回的。"""
    cfg = get_llm_config()
    return {
        "settings": {
            "api_key": _mask(cfg["api_key"]),
            "base_url": cfg["base_url"],
            "model": cfg["model"],
        },
        "source": cfg["source"],        # 告诉管理员当前这套值是从哪来的
        "configured": bool(cfg["api_key"]),
    }


@app.post("/api/admin/settings")
async def settings_set(req: Request):
    """保存 API 配置（人话：管理员在线改，改完对话立即用新的，不用重启）。"""
    body = await req.json()
    patch: dict[str, str] = {}

    # 关键防呆：前端回传的密钥可能是打码串（含星号）。
    # 这种情况说明管理员没改过这一项，千万别把星号存进去当真密钥用。
    key = (body.get("api_key") or "").strip()
    if key and "*" not in key:
        patch["api_key"] = key
    if "base_url" in body:
        patch["base_url"] = (body.get("base_url") or "").strip()
    if "model" in body:
        patch["model"] = (body.get("model") or "").strip()

    set_settings(patch)
    cfg = get_llm_config()
    return {
        "ok": True,
        "message": "已保存，下一次对话立即生效（不用重启服务）",
        "source": cfg["source"],
        "configured": bool(cfg["api_key"]),
    }


@app.post("/api/admin/settings/test")
async def settings_test():
    """拿当前配置真的去问大模型一句（人话：配完点一下就知道通不通）。"""
    cfg = get_llm_config()
    if not cfg["api_key"]:
        return {"ok": False, "message": "还没配置密钥，请先填写 API Key"}

    try:
        # 问个极短的问题，目的只是验证鉴权和网络，
        # 顺便把模型名回显出来，方便确认连的是不是自己想用的那个
        client = DeepSeekClient()
        await client.chat([{"role": "user", "content": "回复两个字：正常"}])
    except LLMError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": f"连通正常 · 模型 {cfg['model']} · 配置来源：{cfg['source']}"}


# ============ 学生个人库：周表（课程）与待办 ============

@app.get("/api/timetable")
async def timetable_get():
    """读周表课程（人话：日程页周视图的课程格子数据源）。"""
    data = get_timetable_data()
    return {"courses": data.get("courses", []), "updated_at": data.get("updated_at", "")}


@app.post("/api/timetable/clear")
async def timetable_clear(req: Request):
    """清空整个周表 —— **唯一**的清空入口，只能由前端确认弹窗的按钮触发。

    为什么把它单独拆出来，而不是让 AI 调？
        需求里的硬约束：严禁 AI 模型自己执行数据库删除，删除动作必须来自
        前端按钮触发后端。所以这里做成"必须带上一份待确认的清空提案"，
        而那份提案只有在学生真的在弹窗上点了【确认】之后才会被前端回传。
        模型既没有调用这个接口的工具，就算它想（比如嘴上说"已清空"），
        没有前端这一下，库里一门课都不会少。

    :param session_id: 前端当前会话，用来校验"确实有学生刚点了确认"
    :param confirm: 必须显式为 True——前端按钮点了才会传
    """
    from app.agent.pending import take_pending
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    if body.get("confirm") is not True:
        # 学生点了弹窗上的【取消】——后端把手上那份待确认提案一并作废。
        # 不作废会留个后门：取消之后后端还存着一张提案，谁再调一次这个接口照样能清库，
        # 学生以为已经放弃了，课却还是没了。
        if body.get("abandon") is True:
            take_pending(sid)
            return {"ok": True, "abandoned": True, "removed": 0}
        return JSONResponse({"error": "清空必须由前端确认弹窗触发"}, status_code=400)

    # 执行逻辑跟"聊天里回确认文字"共用同一段（_execute_clear），
    # 免得两个入口各写一份、改了一处忘了另一处。
    done = _execute_clear(sid)
    if done is None:
        return JSONResponse(
            {"error": "没有待确认的清空提案，请先在对话里让助手出一张清空确认弹窗"},
            status_code=400)
    return {
        "ok": True,
        "count": 0,
        "removed": done["removed"],
        "summary": done["summary"],
    }


@app.post("/api/timetable/apply")
async def timetable_apply(req: Request):
    """课表修改的**确定性写入口**（人话：学生点确认卡后前端直接调这里，不经过 AI）。

    为什么写入不经过 AI？之前 AI 在学生确认后要自己重新构造整表 JSON 再调写入工具，
    模型一旦没调工具、重构出错或嘴上说"已删除"，就会出现"确认了但数据没变"。
        现在提案（AI 出）和执行（这里收）分开，确认即写入，结果可预期。
    """
    from app.modules.planner import validate_courses
    body = await req.json()
    courses = body.get("courses")
    if not isinstance(courses, list) or not courses:
        return JSONResponse({"error": "课程列表为空或格式不对"}, status_code=400)
    cleaned, errors = validate_courses(courses)
    if errors:
        return JSONResponse(
            {"error": "有课程没通过校验，请重新生成提案", "detail": errors[:8]},
            status_code=400)
    save_timetable(cleaned)
    # 学生已经在界面上点了卡片 → 标记已执行，避免后面在聊天框再回一句"确认"时重复写一遍
    sid = (body.get("session_id") or "").strip()
    if sid:
        from app.agent.pending import mark_applied
        mark_applied(sid)
        # 同待办一条理：学生是点了卡片才写进来的，这话要留进历史，
        # 不然刷新一下"✅ 课表已更新"那句没了，学生又纳闷到底改没改。
        append_conversation(sid, "assistant", f"课表已更新：共 {len(cleaned)} 门课")
    return {"ok": True, "count": len(cleaned), "courses": cleaned}


@app.get("/api/todos")
async def todos_list(date: str | None = None):
    """列待办。带 date 查某一天，不带就全给（前端自己按日期分组）。"""
    return {"todos": list_todos(date)}


@app.get("/api/todos/month")
async def todos_month(month: str = ""):
    """按日期聚合某个月的待办（人话：月视图要给每天标"有几项待办"）。"""
    if not month:  # 没传月份就默认当月
        month = datetime.date.today().strftime("%Y-%m")
    return {"month": month, "days": list_todos_in_month(month)}


@app.post("/api/todos")
async def todo_add(req: Request):
    """手动加一条待办（人话：不走 AI 也能自己往日程里塞一件事）。"""
    body = await req.json()
    date = (body.get("date") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    title = (body.get("title") or "").strip()
    if not (date and start and end and title):
        return JSONResponse({"error": "标题、日期、开始与结束时间都要填"}, status_code=400)
    item = add_todo(title, date, start, end,
                    note=(body.get("note") or "").strip(),
                    category=(body.get("category") or "").strip())
    # 学生是点卡片上的【确认加入】才走到这里的，这话得进会话历史——
    # 否则一刷新页面，那两条"✅ 已加入日程"就消失了，学生又会以为刚才没加上。
    sid = (body.get("session_id") or "").strip()
    if sid:
        append_conversation(sid, "assistant", f"已加入日程：{item.get('title', title)}"
                            f"｜{date}（{_weekday_of(date)}）{start}-{end}")
    return {"ok": True, "todo": item}


@app.post("/api/todos/mode")
async def todo_pick_mode(req: Request):
    """学生在二选一卡上点了"我自己定"或"你帮我挑"（人话：时间由谁定，先问清）。

    学生原话：「**要区分两种，一种是我有时间规划了，一种是我没有时间规划
    让他帮我安排，不要一上来就询问详细时间，先给弹窗，（有时间规划）
    （还没有，你帮我定），用户选择后……**」

    · mode="self" → 出**候选时段卡**（几段空档摊开，他自己勾 / 在「其他时间」里自己写）；
    · mode="ai"   → **系统真去规划**（`plan_todo_slot`：按他说的时长、避开课和已排的事，
      挑一段并写清"为什么排在这儿"），出单条确认条。

    这个接口**一个字节都不写库**：它只把"接下来那张卡"换掉。
    真正落库还是老路子——学生勾完点【加入日程】走 /api/todos/batch，
    或者点【确认加入】走 /api/todos。
    """
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    mode = (body.get("mode") or "").strip()
    if mode not in ("self", "ai"):
        return JSONResponse({"error": "mode 只能是 self 或 ai"}, status_code=400)
    if not sid:
        return JSONResponse({"error": "缺少会话标识"}, status_code=400)
    card = _pending_todo_mode(sid)
    if card is None:
        # 暂存里没有这张卡（刷新过、或者上一轮已经选过了）→ 如实说，别凭空造一张
        return JSONResponse(
            {"error": "这张卡已经过期了——你再跟管家说一句「帮我加个 XX」就行"},
            status_code=404)
    new_card = mode_to_card(card, mode)
    if new_card is None:
        title = card.get("title") or "待办"
        return JSONResponse(
            {"error": f"「{title}」这几天实在插不进去——课和已排好的事把空档都占了。"
                      f"换个日子，或者你说个具体钟点。"},
            status_code=409)
    # 换卡：暂存里把新那张挂上（旧的二选一卡就此作废——它的问题已经答完了）
    save_pending(sid, [new_card])
    label = "系统替你规划的时间" if mode == "ai" else "你自己挑时间"
    append_conversation(sid, "assistant", f"〔{label}〕{new_card.get('summary', '')}")
    return {"ok": True, "mode": mode, "pending": new_card}


def _weekday_of(date: str) -> str:
    """日期转"周几"（人话：历史里那句人话别写出来一串看不懂的日期格式）。"""
    try:
        from app.modules.planner import _weekday_name
        return _weekday_name(date)
    except Exception:
        return ""


@app.post("/api/todos/batch")
async def todo_add_batch(req: Request):
    """学生勾完候选时段、点【加入日程】按的就是这儿（人话：一次写进他勾中的那几段）。

    为什么另开一个"批量"接口，而不是让前端循环调 `POST /api/todos`：
      · **空档复核要在同一批里逐条做**。候选是上一轮算出来的，中间学生可能自己又
        往同一天加了一条；循环调用时每条各自判断，看不见"同一批里刚写进去的那条"，
        两条就会压在同一个点上。这里逐条写、逐条复查，后一条能看见前一条。
      · **回执要合成一句**。"已加入日程：健身｜周六 09:00-10:30 等 2 项"
        比两行零散回执好读，也让 3a-c 的"确认落空"兜底能靠它判断"最近真的写过"。
      · 前端只发一次请求，失败就是整体失败，不会出现"勾了三个、写进去一个半"。

    写权限依然在系统这侧：模型手里没有这个接口，它最多只能出提案。
    """
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    title = (body.get("title") or "").strip() or "待办"
    note = (body.get("note") or "").strip()
    raw_slots = body.get("slots") if isinstance(body.get("slots"), list) else []
    other = (body.get("other") or "").strip()

    # 1) 先把前端递来的勾选项归一化（去重、丢掉字段不全的）
    chosen, seen = [], set()
    for s in raw_slots:
        if not isinstance(s, dict):
            continue
        d = (s.get("date") or "").strip()
        st = (s.get("start") or "").strip()
        en = (s.get("end") or "").strip()
        if not (d and st and en) or (d, st) in seen:
            continue
        seen.add((d, st))
        chosen.append((d, st, en))

    if not chosen and not other:
        # 一个都没勾、也没自填 —— 这不是错误，是学生手滑点了按钮。
        # 说清楚该干什么，比返回一个冷冰冰的 400 有用。
        return JSONResponse(
            {"error": "还没选时间——勾一个，或者在「其他时间」里写一个"}, status_code=400)

    added, skipped = [], []

    def _write(d: str, st: str, en: str, t: str) -> bool:
        """写一条（先复核空档）。返回是否真的写进去了。"""
        if not slot_is_free(d, st, en):
            skipped.append({
                "date": d, "start": st, "end": en,
                "label": f"{d}（{_weekday_of(d)}）{st}-{en}",
                "reason": "这段时间已经被课或别的安排占了",
            })
            return False
        added.append(add_todo(t or title, d, st, en, note=note))
        return True

    # 2) 逐条写他勾中的那几段（后一条能看见前一条，所以不会自己撞自己）
    for d, st, en in chosen:
        _write(d, st, en, title)

    # 3) 「其他时间」里自填的那一行——**认不出来就如实说，不瞎猜一个时间**
    unparsed = ""
    if other:
        fb = chosen[0][0] if chosen else ""
        if not fb:
            # 学生一段都没勾、只写了「其他」——兜底那天取候选卡里最靠前的那天，
            # 这样他写「晚上七点到八点」（只有钟点、没写哪天）也能落地。
            held = peek_pending(sid) or {}
            for o in held.get("options") or []:
                if isinstance(o, dict) and o.get("kind") == "todo_slots":
                    fb = next((s.get("date") or "" for s in (o.get("slots") or [])
                               if isinstance(s, dict)), "")
                    break
        prop = parse_slot_text(other, title, fb)
        if prop is None:
            unparsed = other
        else:
            key = (prop["date"], prop["start"])
            if key in seen:
                skipped.append({"date": prop["date"], "start": prop["start"],
                                "end": prop["end"], "label": other,
                                "reason": "这个点你上面已经勾过了"})
            else:
                seen.add(key)
                _write(prop["date"], prop["start"], prop["end"], prop.get("title") or title)

    # 4) 回执写进会话历史 —— 学生一点完就刷新页面的话，
    #    聊天里还得留着"我确实加过"，不然他又会怀疑刚才没写进去。
    if added and sid:
        from app.agent.pending import mark_applied
        mark_applied(sid)      # 标记那张候选卡已执行，别再被"确认"重复触发一次
        shown = "、".join(f"{i.get('date', '')}（{_weekday_of(i.get('date', ''))}）"
                          f"{i.get('start', '')}-{i.get('end', '')}" for i in added[:3])
        tail = "" if len(added) <= 3 else f" 等 {len(added)} 项"
        append_conversation(sid, "assistant", f"已加入日程：{title}｜{shown}{tail}")

    if added:
        parts = [f"✅ 已加入日程：**{title}**"]
        for i in added:
            d = i.get("date", "")
            parts.append(f"　· {d}（{_weekday_of(d)}）{i.get('start', '')}-{i.get('end', '')}")
        if skipped:
            parts.append("下面这几段没写进去（已经排了别的）：")
            parts += [f"　· {s['label']}——{s['reason']}" for s in skipped]
        if unparsed:
            parts.append(f"「其他」里那句我没看懂时间：**{unparsed}**——"
                         f"换个写法试试，比如「周六下午三点到四点」。")
        message = "\n".join(parts)
    elif skipped:
        message = ("这几段都没写进去（已经排了别的安排）：\n"
                   + "\n".join(f"　· {s['label']}——{s['reason']}" for s in skipped)
                   + "\n换一段，或者在「其他时间」里自己写一个。")
    else:
        message = (f"「其他」里那句我没认出时间：**{unparsed}**——"
                   f"换个写法试试，比如「周六下午三点到四点」。")

    return {
        "ok": bool(added),
        "title": title,
        "count": len(added),
        "added": added,
        "skipped": skipped,
        "unparsed": unparsed,
        "message": message,
    }


@app.put("/api/todos/{tid}")
async def todo_update(tid: str, req: Request):
    """改一条待办（目前主要用来标记完成 / 取消完成）。"""
    body = await req.json()
    patch = {k: v for k, v in body.items() if k in ("title", "date", "start", "end", "note", "status")}
    item = update_todo(tid, patch)
    if not item:
        return JSONResponse({"error": "待办不存在"}, status_code=404)
    return {"ok": True, "todo": item}


@app.delete("/api/todos/{tid}")
async def todo_delete(tid: str, req: Request):
    """删一条待办（人话：只有学生点了【确认删除】/【删除这条】才会走到这里）。

    写入权在系统这一侧：模型连这个接口都碰不到——它手里只有只读的提案工具
    （propose_todo_remove），删待办是**学生点头之后由系统执行**的，不可逆的事不交给模型。

    body 里可以带 session_id。带了就把这次删除**记进会话历史**——跟"已加入日程"
    是同一个用意：学生点完刷新页面，聊天里还留着"我确实删过这条"，
    不会又怀疑自己刚才没删成。
    """
    body = {}
    try:
        body = await req.json()
    except Exception:
        body = {}
    item = get_todo(tid)
    if not delete_todo(tid):
        return JSONResponse({"error": "待办不存在"}, status_code=404)
    sid = (body.get("session_id") or "").strip() if isinstance(body, dict) else ""
    if sid and item:
        date = item.get("date", "")
        append_conversation(
            sid, "assistant",
            f"已删除待办：{item.get('title', '待办')}｜{date}（{_weekday_of(date)}）"
            f"{item.get('start', '')}-{item.get('end', '')}")
    return {"ok": True}


@app.get("/api/conversation/{sid}")
async def conversation_get(sid: str):
    """读回一段会话的历史（人话：学生刷新页面后，聊天记录要还在）。

    为什么必须有它：学生端之前从不做这件事——刷新一下，聊天区就只剩下开场白，
    学生刚跟管家商量的"排了什么时间"整个消失，只剩日程里那几条他自己也记不清的记录。
    聊过的内容本来就被 append_conversation 好好存着，只是没人去读它。
    """
    sid = (sid or "").strip()
    if not sid or len(sid) > 64:
        return {"messages": []}
    return {"messages": list(get_conversation(sid))}


@app.get("/api/pending/{sid}")
async def pending_get(sid: str):
    """读当前待确认的那张提案（人话：给**独立的确认页面**取数用）。

    为什么要单独给一个接口：确认弹窗做成了独立的 UI 页面（/confirm），
    它跟聊天页不是同一段前端代码，得有个地方把"这次要改什么"取出来渲染。

    只读接口——这里只会把提案**念一遍**，一个字节都不写库。
    真正落库仍然只有那几个写入口（/api/timetable/apply、/api/todos、
    /api/timetable/clear），而且都得由页面上的按钮或确认文字触发。
    """
    sid = (sid or "").strip()
    if not sid or len(sid) > 64:
        return {"pending": None}
    entry = peek_pending(sid)
    if not entry or entry.get("applied"):
        return {"pending": None}
    # 已经执行过的单张卡不再显示（避免重复写入）
    opts = [o for o in (entry.get("options") or [])
            if isinstance(o, dict) and not o.get("applied")]
    if not opts:
        return {"pending": None}
    return {"pending": opts[-1], "session_id": sid}


@app.post("/api/chat/reset")
async def chat_reset(req: Request):
    """清空一段会话（人话：聊跑偏了或想重开一局时用）。

    学生是自己点了界面上的【清空记录】才走到这儿的——
    跟清空课表一个道理：删数据这件事只能由学生亲手点的按钮触发，AI 碰不到。

    为什么顺手清掉待确认提案：会话都不要了，那张卡还挂在暂存里，
    学生手滑再回一句「确认」，系统就会照着一张早已无人认领的提案去写库。
    """
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    if not sid or len(sid) > 64:
        return {"ok": True, "removed": 0}
    before = len(get_conversation(sid))
    clear_conversation(sid)
    clear_pending(sid)
    return {"ok": True, "removed": before}


# ============ 学生端卡片视图：快递/外卖只读展示 ============
# 这两个接口只做"把结构化数据读给前端面板看"这一件事：
# AI 对话仍走 /api/chat + 各模块工具，读写逻辑一概不经过这里。

@app.get("/api/express")
async def express_view():
    """学生端快递卡片的数据源（只读，状态标签已按到件天数实时算好）。"""
    return {"packages": express_packages_view()}


@app.get("/api/takeout")
async def takeout_view():
    """学生端外卖卡片的数据源（只读）。"""
    return {"orders": takeout_orders_view()}


def _render(name: str) -> HTMLResponse:
    """读取 static 目录下的某个页面并返回（人话：统一的"吐页面"小工具）。

    带 no-store 头是故意的：告诉浏览器"这页别缓存，每次都要新的"。
    不然演示时改完 HTML，刷新看到的还是旧页面（甚至空白），很耽误事。
    """
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
async def login_page():
    """登录页：选择身份（学生 / 管理员）。"""
    return _render("login.html")


@app.get("/student", response_class=HTMLResponse)
async def student_page():
    """学生端主页（四模块）。"""
    return _render("student.html")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page():
    """学生助手设置页：改助手名字、挑聊天性格、返回登录等都在这里。"""
    return _render("settings.html")


@app.get("/confirm", response_class=HTMLResponse)
async def confirm_page():
    """**独立的变更确认页面**（人话：确认 UI 本身就是一个单独的 UI 页面）。

    需求里点名"弹窗为单独 ui 页面"，所以确认这件事不再塞在聊天页的 DOM 里，
    而是单独一个页面：
      · 可以直接打开 /confirm?session=xxx 单独看"这次要改什么"；
      · 学生端聊天里出提案时，把这个页面用 iframe 载进**确认条**
        （同一份 UI、同一套按钮），确认条贴在消息下面，不弹全屏遮罩。

    它自己不写库，只调后端那几个写入口；点【取消】则什么都不调。
    """
    return _render("confirm.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """管理员端（发布通知 + 接收学生消息 + 知识库维护 + 客服人格配置）。"""
    return _render("admin.html")


@app.get("/station", response_class=HTMLResponse)
async def station_page():
    """驿站/商户端（用配置的客服人格回复咨询 + 主动推送取件提醒）。"""
    return _render("station.html")


@app.get("/plan", response_class=HTMLResponse)
async def plan_page():
    """学生个人日程（周表看课程与待办，月表看哪天有几件事）。"""
    return _render("plan.html")


# 把 static 目录挂到 /static，方便以后放图片、脚本等静态资源
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
