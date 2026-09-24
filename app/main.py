"""总入口（人话：FastAPI 把上面所有能力接到网页接口上，是整个服务的"大门"）。

请求进来后的完整链路：
  前端说话 → /api/chat → router 判断意图 → 取对应模块的工具箱+系统提示
          → engine 跑 ReAct 循环（必要时调工具）→ 返回回答 + 思考轨迹
"""
import datetime
import json
import os
import uuid
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.engine import AgentEngine
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
    add_todo, update_todo, delete_todo, save_timetable,
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
    build_system_prompt as PLANNER_PROMPT,
    build_tools as planner_tools,
    mock_planner,
    parse_add_course,
    parse_add_todo,
    wants_add_course,
    wants_add_todo,
    wants_clear_timetable,
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
2. 上报工单：不是学生的每句话都上报管理端。只有学生提出外卖丢失、快递问题这类
   **需要管理员处理**的情况时，先问一句「是否上报给管理端？」，学生明确确认后才生成工单；
   普通日常对话、排课、规划待办一律不上报。
3. 个人数据隔离：每个学生的数据相互独立，你的操作只影响当前学生。你可以生成
   **修改课表、增删待办的预览方案**，但学生确认之前数据库绝不会变；
   修改课表走 propose_course_change / propose_timetable_change 提案（学生点确认卡、
   或在聊天框回一句"确认"，都由**系统**写入，不经过你）；
   add_todo_tool 等写入工具必须等学生点头后才能调用。
   ⛔ 修改课表只走提案这一条路：学生点确认卡、或在聊天框回一句"确认"，
      都由**系统**写入。禁止说"自己办不到、权限不够"这类话（出提案的工具本来就在工具箱里），
      也禁止让学生去界面上手动删课（界面上没有那个入口，
      说了就是把学生指到死路，学生只会回一句"我怎么找不到"）。
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


def _pending_todo(session_id: str) -> dict | None:
    """暂存里那张待确认的待办卡（人话：学生上一轮刚看到的确认卡）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_add" and not o.get("applied"):
            return o
    return None


def _remember_todo_options(session_id: str, options: list) -> list:
    """把候选时段记进暂存（人话：学生点【确认所选】时，系统要自己认出是哪一张）。

    为什么必须记下来：学生点完卡片，前端回发的是
    "确认 2026-09-24 19:00-20:30 背单词"——这句话交给路由，会随机落到
    planner / schedule / admin 中的任意一个，落到别处就没人写这条待办。
    系统手里有这份候选，就能自己认出"就是它"，直接落库。
    """
    picks = []
    for o in options or []:
        if not (isinstance(o, dict) and o.get("date") and o.get("start") and o.get("end")):
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
        card["title"], card["date"], card["start"], card["end"],
        note=card.get("note", ""),
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
        picked = _pending_todo(session_id)
        if picked:
            take_pending(session_id)
            _write_todo(picked)
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已加入日程：{picked['summary']}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"✅ 已加入日程：**{picked['title']}**｜{picked['date']}"
                    f"（{picked['weekday']}）{picked['start']}-{picked['end']}。"
                ),
                "trace": [{"step": 1, "phase": "✅ 学生确认（系统写入）",
                           "answer": picked["summary"]}],
                "options": [],
                "awaiting_choice": False,
            }
        # **清空课表回「确认」不算执行**（删空不可恢复，必须学生在弹窗上亲手点按钮）。
        # 但绝不能因此把提案一丢了事、掉回模型——实测模型会顺嘴撒谎：
        # "我重新帮你出一次提案"（根本没出）、"请在弹出的确认卡上点确认"（弹窗早关了）、
        # "回我一句「确认清空」就会执行"（回什么都不会执行）。
        # 学生照着做三连失败：卡片没显示、确认没删、删除失败——就是这个来的。
        # 这里做成确定性：把**同一张提案**重新请出来（弹窗再次出现），并把规矩讲明白。
        entry_now = peek_pending(session_id) or {}
        clear_held = [o for o in entry_now.get("options") or []
                      if isinstance(o, dict) and o.get("kind") == "timetable_clear"]
        if clear_held:
            proposal = _parse_clear_proposal()
            if proposal is not None:
                save_pending(session_id, [proposal])   # 原提案没动过周表，换张新的继续挂着
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成清空提案：{proposal.get('summary', '')}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        "🗑️ 清空课表这一步，聊天里回「确认」不作数——"
                        "删空不可恢复，必须在弹窗上亲手点【确认清空】才执行。<br>"
                        "弹窗已经再次为你打开了，点它上面的按钮就行。"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 重新弹出清空确认弹窗",
                               "answer": "聊天确认不算数，必须点弹窗按钮"}],
                    "options": [proposal],
                    "awaiting_choice": True,
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
        applied = apply_pending_timetable_change(session_id)
        # 清空课表是例外：必须学生亲手点弹窗上的【确认】，
        # 在聊天框回一句"确认"不算——删空是不可恢复的操作，多一道人工闸门。
        if applied and applied.get("kind") != "timetable_clear":
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
                        "🗑️ 上一次的清空弹窗可能已经关掉了，我又把它请了出来——"
                        "请在弹窗上点【确认清空】执行，点【取消】就什么都不变。<br>"
                        "（清空课表这一步只能在弹窗上按按钮，聊天里回「确认」不作数。）"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 确认落空 → 重新弹出清空弹窗",
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
                    "周表本来就是空的，一门课都没有，没有课可清空 🗑️<br>"
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
                    f"🗑️ 已为你生成**清空 {proposal.get('clear_count', 0)} 门课**的确认弹窗，"
                    f"在弹窗上点【确认清空】才会执行，点【取消】就什么都不变。"
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
    if wants_add_todo(message):
        proposal = parse_add_todo(message)
        if proposal is not None:
            save_pending(session_id, [proposal])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已生成待办确认：{proposal['summary']}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"📝 要不要把 **{proposal['title']}** 排进日程？"
                    f"{proposal['date']}（{proposal['weekday']}）{proposal['start']}-{proposal['end']}。"
                    f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。"
                ),
                "trace": [{"step": 1, "phase": "📝 生成待办提案（系统判定）",
                           "answer": proposal["summary"]}],
                "options": [proposal],
                "awaiting_choice": True,
            }
        # 系统拼不出日期/时刻（学生没说清）→ 旧的候选时段那套照旧，但候选要进暂存，
        # 否则学生点完卡片回发的"确认 日期 起-止 标题"依旧会被路由甩到别的模块、照样写不进去
        clear_pending(session_id)

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

    # 把本轮对话记进会话历史，下一轮才能接着聊
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", result["answer"])

    # 4) 返回最终回答 + 思考轨迹（学生端不展示轨迹，但规划模块的候选选项要带回前端）
    return {
        "module": module_key,
        "session_id": session_id,
        "answer": result["answer"],
        "trace": result["trace"],
        "options": _remember_todo_options(session_id, result.get("options", [])),
        "awaiting_choice": result.get("awaiting_choice", False),
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
    from app.modules.planner import get_timetable
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

    entry = take_pending(sid) or {}
    proposals = [o for o in (entry.get("options") or [])
                 if isinstance(o, dict) and o.get("kind") == "timetable_clear"]
    if not proposals:
        return JSONResponse(
            {"error": "没有待确认的清空提案，请先在对话里让助手出一张清空确认弹窗"},
            status_code=400)

    before = get_timetable()
    save_timetable([])   # 当前登录学生本人的周表，整体清空
    return {
        "ok": True,
        "count": 0,
        "removed": len(before),
        "summary": str(proposals[-1].get("summary") or "清空课表"),
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


def _weekday_of(date: str) -> str:
    """日期转"周几"（人话：历史里那句人话别写出来一串看不懂的日期格式）。"""
    try:
        from app.modules.planner import _weekday_name
        return _weekday_name(date)
    except Exception:
        return ""


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
async def todo_delete(tid: str):
    """删一条待办。"""
    if not delete_todo(tid):
        return JSONResponse({"error": "待办不存在"}, status_code=404)
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
