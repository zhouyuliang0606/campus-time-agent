"""总入口（人话：FastAPI 把上面所有能力接到网页接口上，是整个服务的"大门"）。

请求进来后的完整链路：
  前端说话 → /api/chat → router 判断意图 → 取对应模块的工具箱+系统提示
          → engine 跑 ReAct 循环（必要时调工具）→ 返回回答 + 思考轨迹
"""
import datetime
import asyncio
import json
import os
import re
import hashlib
import hmac
import uuid
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import clock      # #56-A
from app.admin_guard import verify_admin
from app.agent.engine import AgentEngine
from app.agent.intent import (
    DEFAULT_MODULE, INTENT_ADD_TODO, INTENT_CHAT, INTENT_CLEAR_TIMETABLE,
    INTENT_DONE, INTENT_FIND_CARD, INTENT_LIST_TODO, INTENT_MEANING,
    INTENT_REMOVE_TODO, INTENT_RETIME, SOURCE_MEANING,
    QUESTION_RE, asks_calendar_fact, classify, looks_like_question, physio_state_only,
    remember, to_canonical,
)
from app.agent.pending import (
    clear_pending, is_confirmation, mark_applied, peek_pending, save_pending, take_pending,
)
from app.agent.readonly import readonly_tools
from app.agent.router import Router
from app.config import (DEBUG, BaseUrlError, check_base_url_ssrf, check_config, get_llm_config,
                        sanitize_override)
from app.llm.client import LLMError
from app.llm.gateway import ModelGateway
from app.upload import UploadError, extract_text, ext_supported, unsupported_reason
from app.modules.schedule import SYSTEM_PROMPT as SCHEDULE_PROMPT, build_tools as schedule_tools
from app.store import (
    add_message, list_messages, add_notice, list_notices,
    list_services, get_service, validate_service_def, add_service,
    dept_of, dept_of_record, is_builtin_service, trail_of,
    list_custom_records, add_custom_record, service_of_module,
    update_custom_record_status,
    is_custom_module, match_service_by_trigger, custom_module,
    list_kb, add_kb_entry, update_kb_entry, delete_kb_entry, clean_split_words,
    get_persona, set_persona,
    get_settings, set_settings,
    summarize_llm_usage, clear_llm_usage,
    MAX_UPLOAD_BYTES,
    save_upload, list_uploads, visible_uploads, can_read_upload, get_upload, get_upload_meta, get_upload_raw_path,
    list_docs, upload_purpose, list_chunks, reindex_upload, delete_upload, upload_notice_refs,
    get_conversation, append_conversation, clear_conversation,
    get_timetable_data, list_todos, list_todos_in_month,
    add_todo, add_todos_batch, update_todo, delete_todo, get_todo, save_timetable,
    set_todo_done_on, done_date_for, DAY_LOGGED_REPEATS,
    REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY, is_past_date,
    add_workorder, list_workorders, update_workorder_status,
    set_workorder_archived, set_message_archived, list_archived_workorders,
    delete_service, delete_custom_record, delete_notice,
    set_current_student, current_student, is_registered, list_accounts, timetable_saved,
)
from app.modules.faq import SYSTEM_PROMPT as FAQ_PROMPT, build_tools as faq_tools
from app.modules.express import SYSTEM_PROMPT as EXPRESS_PROMPT, build_tools as express_tools
from app.modules.takeout import SYSTEM_PROMPT as TAKEOUT_PROMPT, build_tools as takeout_tools
# 学生端卡片视图的只读数据源（人话：给面板展示用的，不参与 AI 对话）
from app.modules.express import packages_view as express_packages_view
from app.modules.takeout import orders_view as takeout_orders_view
from app.modules.files import build_tools as files_tools
from app.modules.station import build_system_prompt as STATION_PROMPT, build_tools as station_tools
# 低代码服务（#22 单子 4）：提示与工具箱都**按那份运行时定义现算**，所以这里导入的是函数本身
from app.modules.custom import custom_prompt, custom_tools
# planner 也是"可调用提示"：每次对话都要把**今天的日期**动态拼进去，
# 否则学生说"明天"，AI 根本算不出是哪一天
from app.modules.planner import (
    DAY_NAMES,
    asks_campus_fact,
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
    span_only_proposal,
    mode_to_card,
    claims_proposal_sent,
    missing_thing_of,
    is_time_ask,                     # 42-A：「啥时候放假」这一族的时间问句形状
    _name_match as _planner_name_match,
    DAY_NAMES,
    _weekday_name,
    has_concrete_span,
    render_todo_remove_list,
    render_todo_done_list,
    rescue_proposal_from_reply,
    looks_like_tool_echo,
    textual_tool_card,
    resolve_todo_remove,
    resolve_todo_done,
    resolve_todo_remove_reply,
    resolve_todo_done_reply,
    slot_is_free,
    todo_mode_proposal,
    todo_slots_proposal,
    carry_freq,
    carry_period,
    unsupported_period,
    _asking_what_to_do,
    what_to_do_suggestion,
    _bare_clock_note,
    todo_remove_batch_proposal,
    todo_done_batch_proposal,
    wants_remove_todo,
    wants_remove_todo_loose,
    _REMOVE_TODO_SOFT,
    _DEL_ANY_RE,
    wants_done_todo,
    is_add_todo_answer,
    is_add_todo_followup,
    is_mode_answer,
    is_slot_answer,
    looks_like_timetable_import,
    looks_like_work_order_report,
    timetable_courses_from_reply,
    pick_todo_missing,
    retime_todo_proposal,
    todo_missing_advice,
    todo_title_of,
    wants_add_course,
    wants_add_todo,
    _pick_day,
    _pick_part,
    _pick_date,
    _pick_todo_indexes,
    _pick_todo_name,
    _todo_name_match,
    _wants_colliding,
    _wants_rechoose,
    wants_clear_timetable,
    wants_list_todo,
    wants_remove_course,
    wants_retime_todo,
    _todo_done_proposal,
    update_todo_status,
)
# 粘贴材料（通知 / 安排表 / 清单）的识别与提取（#43）。判据要在**语义层之前**跑，所以走顶层导入。
from app.modules.material import extract_deterministic, looks_like_material
from app.modules.student_persona import STUDENT_PERSONAS, persona_block, is_valid

# 项目根目录（本文件在 app/ 下，根目录是上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")

app = FastAPI(title="校园时间管家 CampusTime", version="0.1.0")
check_config()


def _check_accounts() -> None:
    """启动时喊一句"名单是空的"（人话：不然全站谁都进不去，还没有任何地方告诉你为什么）。

    #38 把"没开通就进不去"做成了**默认行为**，那"名单文件被删/被改坏"就是一次全站锁死 ——
    这种时候唯一能被发现的地方就是启动日志。判据仍然只有 `list_accounts()` 那一份。
    """
    if not list_accounts():
        print("[配置提醒] app/data/accounts.json 里一个学号都没有 —— 现在**任何学号都登不进去**。")
        print("          要么把学号写进去（形如 {\"accounts\": [{\"sid\": \"admin1\"}]}，改完不用重启），")
        print("          要么检查这个文件是不是被删了 / 改坏了（坏文件也按空名单算，宁可进不去也不全放行）。")


_check_accounts()


# —— 请求体解码失败的收口（全项目一处，2026-10-16 #22 单子 5）——
# 病根是真机撞出来的：从 Git Bash 的 curl 发一句中文过去，那壳按控制台码页（GBK）把字节发出去，
# `req.json()` 当场抛 UnicodeDecodeError → 未捕获 → **500 + 服务端整段 traceback**。
# 全项目 23 处 `await req.json()`（都在这份文件里）是同一个形状，逐个包 try 迟早有一份松，
# 所以走 Starlette 的异常处理器：**一处登记，处处生效**。
# 为什么是 400 不是 422：422 是"字段不合形"（结构化合件校验给的），
# 而这里是"这句我压根没解开"，语义不同，前端要的话术也不同。
# 鉴权不会被这层放宽：依赖（`verify_admin`）在函数体之前跑，
# 没口令就是 401，轮不到解码这一步 —— 那条由 test_55 B 批专门钉着。
@app.exception_handler(json.JSONDecodeError)
@app.exception_handler(UnicodeDecodeError)
async def _bad_request_body(request: Request, exc: Exception):
    return JSONResponse({"error": "请求体没能解开：要的是 UTF-8 编码的 JSON。"}, status_code=400)


# ============ 学生身份（#32：同一台机器退出登录换账号，个人库不共用） ============
# 一句话定性：**这不是鉴权**。学号谁都能填，它只是"我是谁"的一个标签，
# 解决的是**数据分区**：课表 / 待办 / 对话记忆按学号分开。真正的鉴权只有管理端口令那一条链。
_STUDENT_COOKIE = "ct_student"
# 学号会被**拼进文件路径** ⇒ 必须白名单，且只许白名单：
# `../../app/main.py`、`a\b`、带 `/` 的串全都能穿出个人库 = 任意文件读写。
_STUDENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def _cookie_secret() -> str:
    """签名用的密钥：**每次现读环境变量**，不在导入时定死（红线 23 那一族）。

    没配 = 不签名也不验签，行为与 #38 之前逐字相同 —— 这条刻意留着，
    否则 `tests/` 里那些直接塞 `Cookie: ct_student=...` 的用例会整批变红，
    而且"忘了配密钥"会从"退化成老行为"变成"全站谁都进不去"。
    """
    return (os.environ.get("CAMPUSTIME_COOKIE_SECRET") or "").strip()


def _sign_student(sid: str) -> str:
    """签发 cookie 值：`学号.签名`。没密钥就退回只写学号。"""
    secret = _cookie_secret()
    if not secret or not sid:
        return sid
    mac = hmac.new(secret.encode("utf-8"), sid.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
    return sid + "." + mac


def _clean_student(raw: str | None) -> str:
    """把外面来的学号收进白名单；不合规则的**一律当没身份**（空串），不猜、不回退到别人。

    两层，各挡一件事：
      · 格式白名单：学号会被**拼进文件路径**，`../../x` 这种必须挡（这是路径安全，不是账号存在性）；
      · 签名校验（配了密钥才有）：挡"开发者工具里把 `ct_student` 改成名单里别人的学号"。
        名单本身**不在这里判** —— 那是 `/api/login` 那一道的活（签发点只有一处，
        没签过的值本来就进不到"名单内"这个状态；这里再查一次名单只会多一次文件读）。
    """
    val = (raw or "").strip()
    if not val:
        return ""
    sid, _, mac = val.partition(".")
    if not _STUDENT_ID_RE.fullmatch(sid):
        return ""
    secret = _cookie_secret()
    if secret:
        expect = hmac.new(secret.encode("utf-8"), sid.encode("utf-8"), hashlib.sha256).hexdigest()[:16]
        # 比签用 compare_digest，不用 ==：差的长度/内容不该从响应时间里漏出去
        if not hmac.compare_digest(mac, expect):
            return ""
    # 名单也在这里判（#38 §2.1.2：登录与中间件**共用同一处**判据）。
    # 为什么两处都要：只查签名的话，"没开通的学号"仍然能靠一个旧 cookie 读到那份库；
    # 只查登录的话，cookie 是浏览器里可以手改的东西 —— 那就是"门锁只装了一半"（#32 同一个教训）。
    # 代价说清楚：这一下是**每个带 cookie 的请求**都现读一次 accounts.json（很小、不缓存，
    # 换来"改完名录立刻生效"）。真觉得贵，就把名录挪进 SQLite，别改成缓存。
    if not is_registered(sid):
        return ""
    return sid                                        # 没密钥时到这里 = 只认格式 + 名单


@app.middleware("http")
async def bind_student(request: Request, call_next):
    """每个请求进来设一次"当前这个请求是哪个学生"—— 全项目**唯一**一处 set。

    ⚠️ 三条要紧的：
      ① 必须在业务处理**之前**设好（`_spath` 在读写那一刻就要用到它）；
      ② 这里再过一遍白名单：cookie 是浏览器里可以被手改的东西，
         只在 `/api/login` 校验等于"门锁只装了一半"（非法值 ⇒ 当没登录，走老位置）；
      ③ 不许在业务函数里再 set 一次 —— 那是"半路换人"，比没分人更糟。
      值存在 ContextVar 里，所以并发请求各拿自己那份，不会互相盖。
    """
    set_current_student(_clean_student(request.cookies.get(_STUDENT_COOKIE)))
    return await call_next(request)


@app.post("/api/login")
async def student_login(req: Request):
    """学生登录（演示级）：**只认名单里的学号，没有密码**。

    返回里带上规范化的学号，前端存 localStorage 只为显示；**后端认的是 cookie**。
    `path="/"` 是必须的：不写就只在当前路径下带得上，学生端那些 `/api/...` 请求会裸奔。

    三道各管各的，顺序不许换（格式在前、名单在后）：
      ① 空 / 格式不对 ⇒ **400 说格式**（学号会拼进路径，这关是路径安全）；
      ② 格式对但没开通 ⇒ **403 说没开通**，且**错误里不回名单**；
      ③ 都过了才签发（配了密钥 ⇒ `学号.hmac`；没配 ⇒ 只写学号，等价于本单之前）。
    ⚠️ 这里**不再播种**（#35）：以前首次登录会拷一份老位置的演示底稿给新学号，
      用户真机的原话是「所有账号进去都自带这个代办，应该是库的问题」——
      它既看着像串数据，又多开了一条写库路径。新账号就该是**空库**，
      从【上传课表】开始（空状态引导见 `student.html` 的日程面板）。
    """
    try:
        body = await req.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "要的是 JSON：{\"student\": \"学号\"}"}, status_code=400)
    raw = str((body or {}).get("student") or "").strip()
    if not raw:
        return JSONResponse({"error": "请填写学号"}, status_code=400)
    if not _STUDENT_ID_RE.fullmatch(raw):
        # 不许"默默回退到默认学生"：那会让人以为登录成功了，其实写进了别人的库
        return JSONResponse({"error": "学号只能是 1~20 位字母、数字、下划线或短横线"}, status_code=400)
    if not is_registered(raw):
        # ⚠️ 不回名单：把 admin1/admin2 打在错误页上，等于告诉任何人"试这两个"。
        return JSONResponse({"error": "这个学号还没有开通，请联系管理员或核对后重试"}, status_code=403)
    resp = JSONResponse({"ok": True, "student": raw})
    resp.set_cookie(_STUDENT_COOKIE, _sign_student(raw), path="/", samesite="lax")
    return resp


@app.post("/api/logout")
async def student_logout():
    """退出登录：只把标签摘掉，**不清任何数据**（下次登进来课表待办都还在）。"""
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_STUDENT_COOKIE, path="/")
    return resp


@app.get("/api/whoami")
async def whoami():
    """当前身份（人话：学生端刷新后得知道"这是谁的库"，而不是靠 localStorage 里那份显示用副本）。"""
    return {"student": current_student()}

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


def _module_handled(module: str | None) -> bool:
    """「这个 module 有人接得住吗」——主链路两处判据**只写这一份**。

    两种合法：`REGISTRY` 里登记过的固定模块，或者低代码的 `custom:<真实存在的id>`。
    ⚠️ 这两处以前都直接写 `in REGISTRY`，低代码接进来之后必须一起改：
    只改一处会出现"路由给了 custom、主链路又不认"的中间态 ——
    那句话会被悄悄打回 faq，测试看着绿、真机答非所问（本项目最贵的那类 bug）。
    """
    if not module:
        return False
    return module in REGISTRY or service_of_module(module) is not None

# 统一追加给每个模块的"现在是哪天几点"。
# 为什么必须在这一层加、而不是各模块自己写：原先**只有 planner** 的提示里注了基准日期，
# 问答/课表/快递那几路没有 → 学生问一句「今天几号」，模型就拿训练数据里的日子现编，
# 本机同一晚连问两次编出两个（`2025-06-12 星期四`、`2026-07-08 星期三`，真的那天是
# 2026-10-12 周一）。对一个"时间管家"来说，这比答错更糟 —— 它答得理直气壮。
# ⚠️ 必须是**函数**、每次请求现算：写成模块级常量就在 import 那一刻定死了，
#   服务跨夜不停机，第二天还在拿昨天当"今天"（相对时间只许在用的那一刻算一次，红线 10 那一家族）。
def _now_block() -> str:
    now = clock.now()
    today = now.date()
    return (
        "\n\n【现在的真实时间】" + today.isoformat() + " "
        + DAY_NAMES.get(today.isoweekday(), "") + " " + now.strftime("%H:%M")
        + "\n学生说的「今天/明天/后天/周几/下周」一律以这一行为准换算。"
        "除了这一行，你不许自己猜日期，更不许编一个日子写进回答或字段里；"
        "换算不出来就照实说换算不出来。"
    )


# 统一追加给每个模块的"读资料"能力说明。
# 放在这里而不是逐个写进模块的 SYSTEM_PROMPT，是为了改一处所有模块都生效。
FILE_HINT = """
【你还具备读取资料的能力】
用户可能会上传文件（课表、通知、名单、表格等）。当用户的提问看起来需要参考某份文件，
或者你不确定有没有相关资料时：
1. 先调用 list_uploaded_files 看看手头有哪些文件；
2. 短文件用 read_uploaded_file 读取其中相关的文件内容；
   **长文件它不会给你正文，只会让你改用 search_docs —— 那就照它说的做，别反复调 read**；
3. 用户问的是文档里某一点（尤其是不记得写在哪一段），直接用 search_docs 按关键词检索，
   它会返回相关的几段原文并标明出处；
   ⚠️ 那几段**可能来自好几份文件**（一份写通则、另一份写例外）：要把内容合起来答、分别说清出自哪份，
   两份说法不一致时把两种都讲出来并说明各自条件，**不要擅自挑一个当准**；
   一次只查到一半信息，可以换一组关键词再检索一次（换个说法就可能查得到）；
4. 基于读到的**真实内容**作答——文件里没有的信息千万不要编造，宁可直接说没找到。
"""

# 学生端 AI 助手的统一人设规矩（学生要求写进人设的四条，全局生效）。
# 与 FILE_HINT 同理：改一处、所有模块都生效，不用去每个模块重复写。
ASSISTANT_RULES = """
【你的人设规矩 —— 所有场景都必须遵守】
1. 输出要求：回答简洁、贴合学生使用场景，不冗长废话；遇到乱码、损坏文件、异常输入，
   友好提示学生重试或换一份文件，**禁止把系统内部报错、堆栈、字段名直接暴露给学生**；
   同样**禁止透露自己是什么模型、谁训练的、用的哪家接口或密钥** ——
   被问"你是什么模型 / 你是不是 GPT / DeepSeek / 智谱"，只答
   「我是校园时间管家，底层的实现我不清楚」，既不编厂商也不编型号，也不反过来强调"我很先进"；
   绝不输出暴力、低俗、煽动类内容。
2. 上报工单：不是学生的每句话都上报驿站。**只有**学生提出快递丢失、外卖遗失这类
   需要管理员协助处理的问题时，才走这个流程，且必须两步：
     ① 先问一句「要不要上报给驿站？」；
     ② 学生明确确认上报，工单才推送到管理端收件箱。
   学生说不上报（或没表态）→ 只在学生这段对话里记着，**绝不推送驿站**。
   反过来：**增减课程、增删日常待办属于学生本人自主管理自己的日程，
   这类请求一律不上报驿站**，也别提"要先上报"这种话。
3. 个人数据隔离：每个学生的数据相互独立，你的操作只影响当前学生。你可以生成
   **增减课程、增删待办的变更提案**，但学生确认之前数据库绝不会变；
   修改课表走 propose_course_change / propose_timetable_change 提案
   （学生点确认条上的【确认】，或在聊天框回"确认添加/确认删除"这类确认文字，
   都由**系统**执行入库，不经过你）；
   **工具箱里没有任何写入工具**——落库不是你的活，你只出提案、如实转述系统的结果。
   ⛔ 日程修改只走提案这一条路。学生点确认条上的【确认】是优先方式，
      在聊天框回确认类文字是备选方式，两者都由**系统**写入。
   ⛔ 禁止说"自己办不到、权限不够、需要先上报管理端/上报驿站"这类话——
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
5. 先查再说：你手上有几个只读工具（查课表 / 查空档 / 查待办 / 查快递 / 查订单 / 查知识库 / 查文件）。
   需要事实就去查：查一次比猜一句强，学生看得见你查了什么。
   但**只在真需要事实时查**，查到就要用掉——为了凑步子连查三次同一件事，比不查更糟。
"""

# 「闲聊守则」（2026-09-30 补）：学生只是感叹/聊日常时，别把闲聊推销成待办。
# 与 FILE_HINT / ASSISTANT_RULES 同理——拼在**所有**学生端模块上，改一处全站生效。
# 病根：共享排程工具（propose_todo_tool 等）是注入**所有**模块的，学生说一句
# 「好累啊」，尾部模型手上正好有"出待办卡"的工具，顺手就来一句
# "要不要我帮你安排个休息时间？"——他只是在吐槽，屏幕上却冒出推销。
CHITCHAT_RULES = """
【闲聊守则 —— 学生只是在感叹/聊日常时，务必遵守】
1. 学生说累、说天气、说吃饭、吐槽心情这类**日常闲聊**，就像靠谱的学长一样
   自然回一两句，可以带一句关心（"累了就歇会儿，别硬撑"）；语气像同学说话，别端着。
2. ⛔ 闲聊时**绝不**主动提议帮他加待办、排时间、定计划；**绝不调用**
   propose_todo_tool / propose_todo_slots_tool / propose_todo_mode_tool
   这类排程/提案工具——除非学生**明确**说了"帮我加/记一下/安排某事"。
3. ⛔ 不许用"要不要我帮你安排个…？"这类话收尾**推销**待办。
4. 闲聊回复保持一两句，写完就停：不写小作文、不列清单、不问时间。
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


# ⚠️ #59：这两份"候选清单"**不是卡** —— 它们是学生回「第二条 / 换一条」时的依据，
# 挂在暂存里但没有 date/start/end，前端也没有对应的渲染分支。
# 恢复接口（`/api/pending/{sid}`）按这份表把它们**跳过**（是"不选中"，**不是删掉**：
# 删掉就等于把「第二条」那个出口砍了）。加新的辅助项请一并登记在这里。
_PENDING_AUX_KINDS = {"todo_remove_candidates", "todo_done_candidates"}


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


def _pending_todo_done(session_id: str) -> dict | None:
    """暂存里那张待确认的**标记完成**确认条（跟 _pending_todo_remove 是一对）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_done" and not o.get("applied"):
            return o
    return None


def _pending_todo_add_batch(session_id: str) -> dict | None:
    """暂存里那张待确认的**批量新增**卡（#43：从粘贴材料里提出的那几条）。

    单独开一个口子有三个用处，都不是"顺手":
    ① 挂在它身上的那句"要不要先处理那张卡"（`_holding_card` 那份名单）；
    ② 学生在文本框回"确认"时，这张卡**不许**被当成"全要"（要他在卡上勾）；
    ③ 刷新后把卡接回来的那条读接口（`/api/pending/{sid}`）本来就按 options 过筛，
       这里只是让"这张卡还在不在"有一个能问的地方。
    """
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_add_batch" and not o.get("applied"):
            return o
    return None


def _pending_todo_remove_batch(session_id: str) -> dict | None:
    """暂存里那张待确认的**批量删除**确认条（人话：学生说"都删了"时挂出来的那张）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_remove_batch" and not o.get("applied"):
            return o
    return None


def _pending_todo_done_batch(session_id: str) -> dict | None:
    """暂存里那张待确认的**批量标记完成**确认条（跟删除版一对）。"""
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_done_batch" and not o.get("applied"):
            return o
    return None


def _pending_todo_done_candidates(session_id: str) -> list:
    """暂存里那份"标记完成候选清单"（人话：上一轮列给学生看的那几条，按显示顺序）。

    跟 _pending_remove_candidates 一对——学生回"第二条"时系统接得住，
    用 resolve_todo_done_reply 把名字补上再出确认条，**绝不替他猜哪条**。
    """
    entry = peek_pending(session_id) or {}
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") == "todo_done_candidates":
            todos = o.get("todos")
            if isinstance(todos, list):
                return todos
    return []


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


# 「不去了」「算了」「取消」——删除/取消**动作**词。抠出的"标题"擦掉这些还
# 剩不下东西 = 学生根本没点名（「不去了删了吧」抠出来的是「不去了删」这种残渣）。
# ⚠️ 这张表擦的是**标题形**的动作词（`todo_title_of` 已经把句末那个「了」吃掉了），
#   跟 planner 那两张**句子里**的词表形状不一样 —— 所以以前在这儿手抄了一份，一抄就漂：
#   「不吃」「不做」「不要」都不在抄件里，于是 `_is_blind_delete("算了不吃了")` 判 False，
#   他刚说的「算了不吃了」既不算是盲删、也没有别的目标，只能去把整张日程列一遍
#   （2026-10-12 真机截图那一幕）。现在句子形那一半**从 `_REMOVE_TODO_SOFT` 推**
#   （去掉尾上的「了」就是标题形），以后加一颗软词这边自动跟上；
#   左边剩下的都是这张表独有的标题形说法（不去 / 改主意…），不复制整词表。
_DELETE_ACTION_WORDS = ("不去了", "不去", "取消", "算了", "删除", "删掉",
                        "删了", "删", "去掉", "移除", "退掉", "改主意") \
    + tuple(w[:-1] if w.endswith("了") else w for w in _REMOVE_TODO_SOFT)
# 学生用指代词点的那条（"把刚才那个删了"）——也算没点名，但指向同样明确。
_BLIND_DELETE_HINTS = ("这个", "那个", "它", "刚才", "刚加", "刚排", "这条", "那条", "上次")


def _delete_residue(text: str) -> str:
    """这句话"点到的东西"擦掉动作词之后还剩什么 —— **剩不下东西 = 他根本没点名**。

    抽成一个函数是因为同一个判断原先只长在 `_is_blind_delete` 里：
    新那道"作废还没点头的提案"要问的正是同一件事，抄第二份就会漂（红线 11b）。
    """
    title = todo_title_of(text)
    if not title or title == "待办":
        return ""
    stripped = title
    for w in _DELETE_ACTION_WORDS:
        stripped = stripped.replace(w, "")
    return stripped.strip("的吧了呀哦呢啊嗯")


def _is_blind_delete(text: str) -> bool:
    """是不是**没点名**的删除（人话：他知道删哪条，就是懒得说名字）。"""
    t = (text or "").strip()
    if not t:
        return False
    # 带了"哪一天"或"撞时间的/冲突的"这类**筛选信息** → 不是盲删，别拿"最近写入的那条"去顶。
    # 否则学生说"10月三号两个撞时间的待办删一下"，会被误当成"没点名的删除"，
    # 拿他最近加的那条来充数（截图实测：他明明说了 10-03 + 撞时间，却被塞了 09-27 的"写作业"）。
    if _pick_date(t) or _wants_colliding(t):
        return False
    if any(h in t for h in _BLIND_DELETE_HINTS):
        return True
    return not _delete_residue(t)


# 「还没点头的加待办提案」那几张卡（候选时段 / 已定时等确认 / 二选一 / 单条已定）。
# 共同点：**库里还没有这条**，学生反悔时要撤的是这张卡，不是日程里的某条待办。
_ADD_PROPOSAL_KINDS = ("todo_slots", "todo_add", "todo_mode", "todo_pick")


def _active_add_proposal(session_id: str) -> dict | None:
    """暂存里那张**还没点头**的加待办提案，没有就返回 None。

    `applied` 打在**条目**上、不在每张卡上（`pending.save_pending` 就写这一层），
    所以这里查的是条目 —— 照 option 查会一辈子查不到（那个字段压根不在卡上）。
    """
    entry = peek_pending(session_id) or {}
    if entry.get("applied"):
        return None
    for o in entry.get("options") or []:
        if isinstance(o, dict) and o.get("kind") in _ADD_PROPOSAL_KINDS:
            return o
    return None


def _cancels_active_proposal(message: str, proposal: dict) -> bool:
    """这句是不是在作废**那张还没点头的卡**（而不是要去日程里删一条）。

    真机截图那一幕：「明天去吃自助」出了候选时段卡，紧接着「算了不吃了」——
    他要撤的是这张刚挂出来的卡，可执行层不认这一层，跑去把整张日程列一遍问
    "是下面哪一条"。所以先问三件事：
      ① 话里**明说了「删」**？那是删库里的一条，别拿作废卡顶掉（「把那个删了」）；
      ② 报了日子 / 挑了"撞时间的"？那是一条新的删除指令，不作废；
      ③ 擦掉动作词之后**还剩东西**？剩下的是另一件事的名字（「我要取消健身」而卡上是
         「吃自助」）就别作废；剩下的正好是这张卡的事名（「吃自助餐不吃了」）才算。
    """
    t = (message or "").strip()
    if not t or _DEL_ANY_RE.search(t):
        return False
    if _pick_date(t) or _wants_colliding(t):
        return False
    # 「嗯」「确认」这类既没动作词、又短的句子不能算反悔（否则学生点个头都把卡撤了）
    if not any(w in t for w in _DELETE_ACTION_WORDS):
        return False
    residue = _delete_residue(t)
    if not residue:
        return True
    title = (proposal.get("title") or "").strip()
    return bool(title) and (residue in title or title in residue)


def _recent_written_todos(session_id: str, span: int = 6) -> list:
    """最近**真的写进日程**的待办 [(标题, 现在还在库里吗), ...]，按回执新→旧。

    为什么要有它：「不去了删了吧」这种没点名的删除，指的就是上一轮刚写入的
    那条——系统手里有这份账（自己写的「已加入日程：唱krv｜…」回执），
    不该回一句"没找到叫这个的待办"把他推去月表手动删（截图实测那一幕）。

    只认**写库回执**（_WRITTEN_RECEIPTS 里的"已加入日程"），
    不认"已生成待办确认"那种挂卡标记——挂了卡他没确认的不算真写进去。
    第二个元素核实的是**现在**还在不在库里：他要是已经手动删过，
    就该回"这条已经不在了"，而不是再挂一张删不掉的确认卡。
    """
    out, seen = [], set()
    in_db = {x.get("title") for x in list_todos()}
    for m in reversed(get_conversation(session_id)[-span:]):
        if m.get("role") != "assistant":
            continue
        content = (m.get("content") or "").replace("**", "")
        for mm in re.finditer(r"已加入日程[：:]\s*([^｜|\n]+)", content):
            title = mm.group(1).strip().rstrip("。").strip()
            if title and title not in seen:
                seen.add(title)
                out.append((title, title in in_db))
    return out


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


def _tt_confirm_answer(n_courses: int, source: str = "") -> str:
    """上传课表后那句回话：**只问"要不要改"，不逐门念课**。

    学生原话（2026-10-04）：「如果是上传课表就弹是否修改就行，不用每门课
    单独拎出来」。所以：
    - 模型的原话**不沿用**——它多半把十几门课又念了一遍（还可能自称"已导入"），
      而此刻真正要他动手的是下面那张卡；
    - 卡上也只给门数（见 `confirm.html` 的 `timetable_change`），
      清单他自己在文件里看过，确认后周表上也会一条条列出来。
    """
    src = f"（清单来自{source}）" if source else ""
    return (
        f"📄 课表读好了，共 {n_courses} 门课{src}。要不要用这份课表替换现在的周表？\n"
        "**点卡片上的【确认修改】**才写入；不想改就点【取消】，周表一个字都不会动。"
    )


def _tt_file_courses(message: str, session_id: str = "") -> list | None:
    """兜底的兜底：直接读**上传的那份文件**抠课程。

    学生原话（2026-10-04）：「解析出来了，卡没出来……由于是修改课表，
    我认为可以弹是否更改课表的卡」。模型回话的排版千奇百怪（列表体、表格、
    括号地点、一行多课），系统抠不干净时，**文件原文**才是最硬的依据——
    它已经躺在库里，不用再问模型一次。

    ⛔ 只按**点名**找文件（消息里的《文件名》，重试时从本会话最近几句里找），
    **绝不拿"最近那份文件"猜**——万一那是别的资料，挂出来的就是一张假卡。
    """
    name = None
    m = re.search(r"《([^》]+)》", message or "")
    if m:
        name = m.group(1).strip()
    else:
        # 「重新导入课表」这种重试没点名文件 → 回本会话最近几句话里找《…》
        for msg in reversed(get_conversation(session_id) or []):
            if (msg.get("role") or "") != "user":
                continue
            m2 = re.search(r"《([^》]+)》", msg.get("content") or "")
            if m2:
                name = m2.group(1).strip()
                break
    if not name:
        return None
    fid = None
    # 45-D：按**文件名**在全局桶里找是第二条绕过归属的路 —— 同一份课表被传过 14 次
    # （实测 `2026秋季课表-纯文本.txt` 在表里有 14 条），同名取"最新那条"，
    # 而 list_uploads() 是倒序 ⇒ 拿到的很可能是**别人**那份。换成学生口径的可见集。
    for f in (visible_uploads() or []):
        if (f.get("name") or "").strip() == name:
            fid = f.get("id")
            break
    if not fid:
        return None
    try:
        content = get_upload(fid)
    except Exception:
        return None
    return timetable_courses_from_reply(content or "")


def _tt_import_no_card_answer() -> str:
    """课表导入没出卡（模型没调工具、话里也抠不出课程清单）→ 如实说明。

    ⚠️ 绝不能掉进下面的待办补条：那会把话术里的「确认修改」当任务名，
    挂出「要不要把 确认修改 排进日程」的假卡（2026-10-03 学生截图实测）。
    """
    return (
        "⚠️ 先说实话：文件我读到了，但这次**没能把课程整理成确认卡**，"
        "周表还一个字都没动。\n\n"
        "你再发一次「**重新导入课表**」（或重新上传文件后点「📅 让管家读取这份课表」）"
        "我就再试一遍；还不行的话，把课程一行一条直接发给我也行——"
        "比如「周一 08:00-09:40 高等数学 教三301」。"
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


def _any_pending(session_id: str) -> bool:
    """这个会话里是否挂着**一张待确认的待办卡**（人话：上一轮留没留一条让学生点的条）。

    为什么单独立一个函数：它是**状态**判据，被两处判据共用 ——
      ① #62 的 Agent 模式短路（挂着卡时，这句话多半是在**回应那张卡**，不能交给模型）；
      ② #42-A 校历闸 / planner 栏知识库直答闸（挂着卡时不接管，别把学生的卡弄丢）。
    同一件事不许有两个写法：以前 ② 在就地写了个 `any(...)`，本单把它收成这一份。

    ⚠️ 名单必须**全**：`:929` 的 `_pending_todo_mode`（二选一卡）以前就漏在这里，
       于是"只挂着 modality 卡"时两处判据都当没卡 ⇒ 校历闸会把那张卡挤掉。
       `tests/test_83` 有一条**从源码里数出所有 `_pending_*` 单卡取器**再比这份名单的断言 ——
       以后新增一种卡却忘了加进来，那条会红，不会留下"卡在但看不见"。
    ⚠️ 覆盖范围是**待办那一族**：暂存里还能挂 `timetable_change` / `timetable_clear` /
       `work_order_report` 那几种（不是待办卡），本函数对它们回 False。
       那几种的"学生回确认"由短路点**之上**的 `3)` / `3a-c` 接住，卡也不会丢
       （`/api/pending/{sid}` 按 kind 过筛能接回来），所以本单不扩大到这里。
    """
    return any(f(session_id) for f in (
        _pending_todo, _pending_todo_remove, _pending_todo_done,
        _pending_todo_remove_batch, _pending_todo_done_batch, _pending_todo_slots,
        _pending_todo_add_batch, _pending_todo_mode,          # ← #62 补上的那条
    ))


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
    pre = int(card.get("preselect") or 0)
    lines = "\n".join(f"　· {s.get('label', '')}" for s in slots)
    if (card.get("repeat") or "") == "weekly":
        # 「每个周天都写作业」这张卡勾出来的是**一条每周重复**的待办，
        # 话必须跟"一周三次铺 N 条"划清：那一句在下面（"现版本没有自动重复这回事"）
        # 只对 preselect 那种情况成立，重复卡要是念它就成了假话。
        tail = ("勾一个固定的时间段，点【加入日程】我**只建一条**——"
                "以后每个那天它都会自己出现在日程里，不用你每周说一次。\n"
                "⚠️ 勾「完成」只算**当周**：这周做完了，下周它还在。不想要了就删掉那一条。")
        return (
            f"🔁 **每周重复** · **{title}**：{lead}"
            f"我在接下来的日子里挑了几个空着的时间段（避开了课和已有的安排），都落在同一个星期几：\n\n"
            f"{lines}\n\n{tail}\n"
            f"想换个钟点：在确认条的「其他时间」里自己写一个也行（比如「周日下午三点到四点」）。"
        )
    if (card.get("repeat") or "") == REPEAT_DAILY:
        # 「我每天都要背单词」勾出来的是**一条每天重复的习惯**（2026-10-13 Prompt 02）。
        # 话术有三件必须说清、少一件都会被他当成 bug：
        #   ① 只建一条（不是每天建一条）；② 勾「完成」只算**当天**（不是以后都不用做）；
        #   ③ 它**不画进日程格子**、只在「代办事项」那一栏出现 —— 这条最容易被误会成"没记上"。
        tail = ("勾一个固定的时间段，点【加入日程】我**只建一条**——"
                "以后**每天**它都会出现在你的「代办事项」里，不用你每天说一次。\n"
                "⚠️ 勾「完成」只算**当天**：今天做完了，明天它还在；不想做了就删掉那一条。\n"
                "习惯类**不画进日程格子**（不然每一天都占一格，越看越乱），在「代办事项」那一栏看它。")
        return (
            f"🔁 **每天** · **{title}**：{lead}"
            f"它以后每天都要做，所以下面哪一天挑一段都行（避开了课和已有的安排）：\n\n"
            f"{lines}\n\n{tail}\n"
            f"想换个钟点：在确认条的「其他时间」里自己写一个也行（比如「晚上八点到八点半」）。"
        )
    if pre:
        # 学生报了频次（"一周三次"）→ 前 pre 段已经替他勾上，话要说清，
        # 否则他以为系统把整周的安排都定了、或者还得自己再点三下。
        tail = (f"你说的是**一周 {pre} 次**，前面 {pre} 段我已经替你勾好了"
                f"（一天一段），确认条里点【加入日程】就一次写 {pre} 条。\n"
                f"想换日子：取消某一勾、改勾后面的备选段就行。\n"
                f"⚠️ 这只管**这一周**——下周要接着练，下周说一声"
                f"「还是一周三次」我再排一轮（现版本没有自动重复这回事）。")
    else:
        tail = ("下面的确认条里可以直接**打勾**——勾一个、或者勾几个都行，"
                "勾好点【加入日程】我就写进去。")
    return (
        f"{lead}"
        f"📝 **{title}** 这件事我替你看了几个空着的时间段（都避开了课和已有的安排）：\n\n"
        f"{lines}\n\n"
        f"{tail}\n"
        f"这几段都不合适？在确认条的「其他时间」里自己写一个也行"
        f"（比如「周六下午三点到四点」）。"
    )


# 「这句话像是在问」的粗信号。**判定层的地基，不是又一张关键词表**——
# 它只用来决定"要不要升级到语义层复核"，绝不用它下结论。多义词（"安排"既是名词
# ——日程安排、又是动词——帮我安排）是子串匹配的天生盲区，词表补多长都收敛不了；
# 真正拍板的是语义层（样本库 / LLM）。模式只有一份，定义在 app/agent/intent.py。
# 刻意**不列"吗/呢"之外的单个语气字**：「帮我安排个健身吗」是下单不是查询。
_ASKING_RE = QUESTION_RE

# 「问答/查询型」栏目：学生在这几个栏目里说话，**就是在问事**——
# 这时候规则要是还想"替他办件事"（加/删/改待办），先让语义层复核一次。
# planner 不在里面：那本来就是"办日程"的栏目，规则想办事是理所当然。
_QA_MODULES = frozenset({
    "faq", "schedule", "express", "takeout", "lostfound", "repair", "notice",
})


# 「这句话在说**自己的**日程/待办」的线索词（#41-B 收成一处）。
# 以前这两条闸门各写一遍同样的元组（报障那条的让路 + 知识库直答），
# 加一档就得记着改两处 —— 这正是本项目反复吃的"抄两份、漏一份"（红线 12 那一族）。
_SELF_SCHEDULE_WORDS = ("待办", "代办", "日程", "周表", "月表", "课表", "清单", "列表")


def _kb_has_answer(text: str, extra: str = "") -> bool:
    """这句话问的是**知识库里有的校园信息**吗（图书馆/校车/宿舍/奖学金…）。
    用来给确定性分支让路：学生问「校车在哪坐」，那是问校园信息，不该被
    "查日程"那条路当成"看不见某条待办"接走。查不到就返回 False——
    让原分支照旧处理，别把他的问题甩给知识库冷处理。

    ⚠️ #41-B 起判据走 `faq.kb_has_answer`（**两个列表空不空**），不再嗅回话文案。
       以前这里是 `startswith("知识库里没有找到")` —— 同一判断抄两处，faq 一改措辞
       这边就**静默**把"都没找到"当成"有答案"（红线 12 那一族）。

    :param extra: 模型为这句补的检索新词（#57-A）。**判空与渲染必须传同一个值**，
       传两次各算一次就会问两遍模型（缓存兜着，但那是运气不是设计），
       所以两个调用点都是"算一次 `extra`、两处都用它"。
    """
    try:
        from app.modules.faq import kb_has_answer   # 函数内导入：改动配置/打补丁都传入得去
        return kb_has_answer(text, extra)
    except Exception:
        return False


# 「知识库这一路」的响应形状，**只写一份**（#42-A 顺手收的：直答 / 查空老实说 / 校园事实让路
# 三处原来各拼一遍 dict + append_conversation，字段顺序一样、抄三份迟早不一致）。
def _kb_say(module_key: str, session_id: str, message: str, *, answer: str,
            note: str, phase: str, step_answer: str) -> dict:
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", note)
    return {
        "module": module_key,
        "session_id": session_id,
        "answer": answer,
        "trace": [{"step": 1, "phase": phase, "answer": step_answer}],
        "options": [],
        "awaiting_choice": False,
    }


# 两路都查空时给学生的那句（#41-B 定下来，#42-A 校园事实那一路复用同一句）
_KB_MISS_LINE = ("📚 知识库里没有查到这一条（问答条目和管理员上传的文档都翻了）。\n"
                 "这个我暂不清楚，建议问一下对应部门或看学校官网 —— 我不拿猜的答你。")

# ---- 粘贴材料 → 多条待办（#43）：卡片形状、出卡响应、拆不出时怎么说 ----

_MATERIAL_PHASE = "📋 认出一段材料 → 只从这段里提取待办（不结合上文、不编时间）"

# 材料分支**自己**把这句听成了什么 —— 轨迹里那句"我把这句听成 X"必须与实走的分支一致。
# 为什么单列一个常量、且在两个出口前各设一次：`_read_step` 那一步在 1757 行就跑了，
# 而材料分支在两千行之后才 return，中间没人覆写 → 语义层当初判的「问事」会一路带到屏幕上，
# 于是"诊断说 A、实际走 B"（2026-10-21 真机：粘了一份带日期的材料，回话是材料分支的固定文案，
# 折叠的轨迹却写着「我把这句听成 问事（三档都没给出结论）」）。
# 两个出口共用这一份：一道判断两处文案，迟早漂移。
_MATERIAL_READ_STEP = {
    "step": 0,
    "phase": "📋 我把这句听成 **一段材料**｜依据：规则层（字段行/编号行形状，"
             "材料判据挂在语义层之前）",
    "answer": "",
}


def _material_card(items: list, missing: list) -> dict:
    """多选确认卡：一行一件事，各自带勾选框（默认全勾，取消哪条就不写哪条）。

    为什么不复用 `todo_remove_batch` 那条确认条：删除/完成是"他刚看着清单说的全要"，
    而材料里哪几条真是他的事**系统不知道**（一份通知常把六个班排在一起）——
    让他在卡上勾，那才是他点头。
    """
    return {
        "kind": "todo_add_batch",
        "summary": f"从这段材料里提出 {len(items)} 条待办",
        "items": [{**it, "checked": True} for it in items],
        "missing": list(missing or []),
    }


def _material_answer(session_id: str, message: str, card: dict, answer: str) -> dict:
    """挂卡 + 组响应。

    不复用 `_offer_response`：那条漏斗①只按**单条**待办挂（`save_pending([card])` 之后
    的文案写着"已生成待办确认：{summary}"），②还带"表达不了的周期"那道否决 ——
    那是给「以后每周晨跑」这类**学生的排期要求**用的，而材料里的"每周/安排"是原文表格词，
    套过去会把整段材料否掉。
    """
    save_pending(session_id, [card])
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", card["summary"])
    return {"module": "planner", "session_id": session_id, "answer": answer,
            "trace": [{"step": 1, "phase": _MATERIAL_PHASE, "answer": card["summary"]}],
            "options": [card], "awaiting_choice": True}


def _material_lead(card: dict) -> str:
    """出卡那句：列出提了几条、哪些没能定下时间，并说清"勾完点按钮才算"。"""
    lines = [f"📋 这段材料里我提出 **{len(card['items'])} 条**要排的事"
             "（只按这段材料本身，没结合上文，也没补材料里没有的信息）："]
    for it in card["items"]:
        lines.append(f"· {it['title']}｜{it['date']} {it['start']}-{it['end']}")
    if card.get("missing"):
        lines.append("另外这几条**没能定下哪天几点**（材料里没写日期或钟点），我也没替它编："
                     + "、".join(m for m in card["missing"][:5])
                     + "。补一句时间，我就把它列进来。")
    if len(card["items"]) >= 20:
        lines.append("（一次最多列 20 条，多的请分行再发一次。）")
    lines.append("在下面这条卡上**取消**不想排的，再点【加入日程】——"
                 "没点之前日程一个字不动。")
    return "\n".join(lines)


def _material_empty_answer(session_id: str, message: str, missing: list) -> dict:
    """一条带时间的都没拆出来时怎么说：如实说拆不出，**不许**编一个今天上去。"""
    if missing:
        # 「没有一条写着日期或钟点」以前**在说假话**：材料里常常明明白白写着日期，
        # 缺的只是钟点（2026-10-21 真机那份就是「2026年10月21日」+ 全角「19：30」）。
        # 改成「没有一条**同时**写着日期和钟点」—— 两种情况（缺日期 / 缺钟点）下这句都是真的。
        answer = ("📋 这段材料我看到了，但里面**没有一条同时写着日期和钟点**，"
                  "所以一条也没往日程里写（我不替材料编时间）。\n"
                  "出现的事有几件：" + "、".join(m for m in missing[:6]) + "。\n"
                  "补一句哪天几点（比如「周三 19:00-21:00」），我就列成卡让你勾。")
    else:
        answer = ("📋 这段材料里我没认出「要安排的事」（既没有日期也没有钟点），所以一条也没写。\n"
                  "如果它是通知不是待办清单，你就当我读过；要排进去的话，说一句哪天几点做什么。")
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", "材料里没带日期，未提取待办")
    return {"module": "planner", "session_id": session_id, "answer": answer,
            "trace": [{"step": 1, "phase": _MATERIAL_PHASE, "answer": "没拆出带日期的条目"}],
            "options": [], "awaiting_choice": False}


def _campus_fact_ask(text: str) -> bool:
    """校历类 + 时间问句（#42-A）：**这句该知识库答，不该由日程那几档接手**。

    三个条件同时成立才算，宁可漏不可抢（跟 #41 那道闸同一口径）：
      · 命中校历词表（`intent.CALENDAR_FACT_WORDS`，与 router 兜底同一份）；
      · 是问"什么时候发生"那一族（`planner.is_time_ask`）——所以「我报到那天有什么课」
        这种问自己日程的不算（它不在"什么时候"的形状里，且真报障由别的分支接）；
      · 句子里没有"说自己日程"的词（「我的待办 / 周表 / 课表」那一族，`_SELF_SCHEDULE_WORDS`）。
    """
    return (asks_calendar_fact(text) and is_time_ask(text)
            and not any(w in text for w in _SELF_SCHEDULE_WORDS))


# Agent 模式（#61-C）：演示用开关 —— 强制走 ReAct 引擎、跳掉所有"替模型抢话"的确定性快路。
# 默认关：真机上确定性快路更快、更不会胡说，那是产品该有的样子；
# 只有演示/评审时才需要"每一句都看得见多轮循环"。
_AGENT_MODE_DEFAULT = (os.getenv("CAMPUSTIME_AGENT_MODE", "").strip().lower()
                       in ("1", "true", "on", "yes"))


def _agent_mode_on(body: dict) -> bool:
    """三处来源，优先级：请求体 > 环境变量 > 默认关。

    ⚠️ 别写 `bool(body.get("agent_mode"))` —— 前端传字符串 "false" 过来那也是真值，
       演示时"我明明关了它还在走 Agent"就是这么来的。
    """
    v = body.get("agent_mode")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes")
    return _AGENT_MODE_DEFAULT


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


def _period_reject_answer(word: str) -> str:
    """「每月/每个周末/下个月…」这类周期的那句回话（**只写一份**）。

    三个出口都要用它（判定层的早退、`_offer_response` 这道漏斗、模型那一路的出口），
    抄三份迟早走样 —— 一处改了另一处没改，学生就会看到两种说法。
    ⚠️ 「每天」2026-10-13 起**不再是**要被拦的说法了（`REPEAT_DAILY` 一条习惯，只进代办），
       所以这里能给的出路从两条变成三条；少写一条就是当面对学生说"每天办不了"，
       而系统其实办得了。
    """
    return (
        f"🚫 「{word}」这种安排我**记不了**——一条日程只能落在**某一天**、"
        f"**每周固定的某一天**，或者**每一天**（后两种都只建一条，以后自己出现）。\n"
        f"换这三种说法我马上就能办：\n"
        f"· 「每周一背单词」→ 只建一条，以后每个周一自己冒出来；\n"
        f"· 「每天都要背单词」→ 只建一条，以后每天出现在你的**代办清单**里（不占日程格子）；\n"
        f"· 「这一周要背三次」→ 在这一周里给你铺三段，你打勾。\n"
        f"只想排一次的话，说个日子（比如「今天背单词」），我立刻出确认条。")


def _period_reject_response(session_id: str, message: str, word: str) -> dict:
    """把上面那句话组装成响应：**不出卡、不挂暂存、不写库**。"""
    answer = _period_reject_answer(word)
    append_conversation(session_id, "user", message)
    append_conversation(session_id, "assistant", answer)
    return {
        "module": "planner",
        "session_id": session_id,
        "answer": answer,
        "trace": [{"step": 1, "phase": f"🚫 「{word}」这种周期接不了 → 如实说并给两种能办的说法",
                   "answer": "没出卡，也没把这句学进去"}],
        "options": [],
        "awaiting_choice": False,
    }


def _offer_response(session_id: str, card: dict, message: str,
                    answer: str, phase: str) -> dict:
    """把一张待办提案挂进暂存 + 组装响应（人话：出条这件事只写一遍）。

    抽出来是因为现在有**三个出口**都要挂条：学生说「加个健身」（3c）、
    学生在候选卡下面报了准点（3c-pre）、系统刚问过一轮而学生答了"确认"（3a-c 兜底）。
    抄三份迟早走样——比如一处忘了写 `save_pending`，
    学生点了按钮后端找不到提案，现象又是"点了没反应"。

    ⚠️ 这里同时是**所有确定性出卡路径的唯一漏斗**，所以"表达不了的周期"这道否决
    必须在这儿再收一次口（2026-10-11 真机补）：判定层那条否决要求"某一档判成 add_todo"，
    可「以后每月晨跑」这句两档都没出声、却照样被"学生报了事名没定时间"那一档接走，
    挂出了一张**把"每月"扔掉**的二选一卡。判据看的是**学生原话**（`message`），
    不是卡片字段 —— 语义层重拼之后周期字样早没了。
    （「以后每天晨跑」当年也在这个名单里，2026-10-13 起"每天"表达得了，它走 daily 那条支。）
    """
    _per = unsupported_period(message or "")
    if _per:
        return _period_reject_response(session_id, message, _per)
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
    """把一张待办确认卡真正写进日程（人话：系统落库，模型碰不到）。

    `repeat` 从卡上带过来（「每个周天都写作业」那张候选卡是 weekly）。
    这条路径以前只挑 5 个字段、连 category 都丢，所以**新字段必须在这里也接一次**，
    否则聊天里点【确认】写进去的就还是普通一次性待办。
    """
    rep = (card.get("repeat") or "").strip()
    if rep not in (REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY):
        rep = REPEAT_NONE
    return add_todo(
        card.get("title") or "待办", card.get("date") or "", card.get("start") or "",
        card.get("end") or "", note=card.get("note", ""), repeat=rep,
    )


@app.get("/api/health")
async def health():
    """健康检查（人话：不依赖大模型密钥，用来确认服务正常启动了）。"""
    return {"status": "ok", "modules": list(REGISTRY.keys())}


@app.get("/api/upstream")
async def upstream_info():
    """学生端要显示的一句话：**你的请求会被送到哪台服务器**。

    只回主机名——不回 key，也不回完整 URL（路径里可能带租户/部署信息）。
    为什么必须给：2026-10-09 关掉"学生自带 base_url"之后，学生填的 key 会被发到
    **管理端配的那个上游**。不告诉他发到哪儿，等于让他把 key 寄给一台没跟他说过的主机。
    """
    try:
        base = get_llm_config()["base_url"]
        host = (base.split("//", 1)[-1].split("/", 1)[0] or "").split("@")[-1]
    except Exception:
        host = ""          # 读不到就不显示，别拿一个猜出来的地址骗学生
    return {"host": host}


def _read_step(sem: dict | None, sem_intent: str | None,
               rule_hit: str | None) -> dict:
    """把"判定层是怎么理解这句话的"做成一条思考轨迹（人话）。

    为什么要把这一步**露出来**（2026-09-30 学生报障：「没有展示思考过程」）：
    学生看到的只有结果。一旦结果不对（他打的是「中文12点的车」，
    卡片上却写着「中文的车」），他**无从判断是哪一步错了** ——
    是事名抠错了？时间读错了？还是压根没听懂？
    把"听成什么 + 凭什么"写在轨迹最前面，他自己一眼就能归因，
    也能拿它判断该不该点【确认加入】。

    ⚠️ 它只**复述**已经算好的结论（`sem` / `sem_intent` / `rule_hit`），
       不重新判定一次 —— 不许在这儿出现第二套判定逻辑（那必然跟主链路漂移）。
    """
    # "问"还是"办"：语义层说了算；它没开口（没配 key / 把握太低 / **压根没叫它**）
    # 就用规则层预检的结论。
    # ⚠️ 别只看 `sem_intent`：常规下单句（「帮我加个游泳周四19:00」）是**故意不叫**
    #    语义层的（`_need_review` 为假，零 API 调用），这时候只看 `sem_intent`
    #    会把一句明白的"办事"报成"问事"——轨迹里写假话比不写更坏。
    intent = sem_intent or rule_hit
    verdict = "问事" if (not intent or intent == INTENT_CHAT) else "办事"
    # 依据：哪一档判的。样本库/语义层是 `sem` 自己标的；
    # 它俩都没给结论时，看规则层预检有没有认出来。
    src = (sem or {}).get("source")
    if src not in SOURCE_MEANING:
        src = "rule" if rule_hit else "none"
    msg = f"我把这句听成 **{verdict}**"
    if intent and intent != INTENT_CHAT:
        msg += f"（{INTENT_MEANING.get(intent, intent)}）"
    msg += f"｜依据：{SOURCE_MEANING.get(src, src)}"
    # 事名只在"它真要用到事名、而且**是语义层读出来的**"时才提（加/删/改/完成）：
    #   · 答一句校园规定也提"事名没读出来"，那是自己给自己找疑；
    #   · 规则层那条路上 `sem` 是 None（没叫模型），事名是主链路自己从原话里抠的，
    #     在这儿说"还没读出来"更是胡说 —— 卡片上明明写着。
    # 这是卡片上最容易出错、学生最想核对的一项，所以由语义层给出时单独点出来。
    if sem and intent in (INTENT_ADD_TODO, INTENT_REMOVE_TODO, INTENT_RETIME, INTENT_DONE):
        _t = ((sem or {}).get("title") or "").strip()
        msg += f"｜事名读成了「{_t}」" if _t else "｜事名还没读出来"
    return {"step": 0, "phase": "🧠 " + msg, "answer": ""}


def _reason_steps(*, rule_hit: str | None, asks: bool, qa_view: bool,
                  rule_veto: bool, period_block: bool, done_claim: bool, physio: bool,
                  is_material: bool, sem: dict | None, sem_intent: str | None) -> list[dict]:
    """判定层的「为什么」，最多 3 条（#60-A）—— 与 `_read_step` 互补，不重复它。

    `_read_step` 那句只说**听成了什么、依据哪一档**；评委接着要问的是
    **"凭什么"和"你否决了什么"** —— 一条 ReAct 轨迹里没有这两样，看着就还是"工具流水"。

    ⚠️ 这里**不是模型的思维链**，一个字都不许编（施工单 §六.2）。
       每一条都只准陈述判定层**当场真的算出来过的东西**：
       `rule_hit` / `asks` / `qa_view` / 四道否决 / "这次没叫模型"。
       所以每条的触发条件都比文案严 —— 判不出来就少一行，宁少不假。
    ⚠️ 文案给学生和评委看：不写 `_sem`、`module_key`、`faq`、`planner` 这类内脏词
       （意图人话名走 `INTENT_MEANING`，与 `_read_step` 同一份表，不另建第二份）。
    """
    out: list[str] = []

    # ① 否决类最值钱：它说明"两个信号打架时，系统按哪个、为什么"
    # ⚠️ 措辞只准陈述**这一句真的关掉了哪条路**，不准承诺"结果一定长什么样"：
    #    真机那句「帮我看看周一到周三哪天课最少，然后那天排两小时做题」就是被这条否决接走的
    #    （语义层判"问事"），可后面引擎查完空档照样**提议**了一张确认条 ——
    #    写"这一句不出卡"就是在轨迹里说假话（单据 §七 的第三条坑，撞上了同一个症状）。
    if physio:
        out.append("只是状态句（像「我饿了」）→ 不把它当「要办事」，这一句不给出待办卡的工具")
    if done_claim and sem_intent == INTENT_DONE and rule_hit == INTENT_ADD_TODO:
        out.append("这句是在宣布「办完了」→ 不按新增处理，免得把已做的事又加一条")
    if period_block:
        out.append("「每月 / 每个周末」这类周期我落不成一条待办 → 先问清楚，不替你猜")
    if rule_veto:
        out.append("规则层想办事，可这句在问事 → 以语义层为准，规则层那几条直接办事的路这一句不走")
    if is_material:
        out.append("这是一整段材料 → 只从这段里提取时间，不拿上一轮上下文去猜")

    # ② 归类依据：说得出"凭什么归到这一类"才写，说不出就留给 ③
    #    （没留"按模块名归类"那一条 —— `MODULE_MEANING` 的值是"课表/时间安排/复习计划"这种
    #     给模型看的关键词，塞进学生屏幕就是另一种内脏。）
    if not out:
        if asks and sem_intent:
            out.append("句子里有提问信号 → 这句先按「问事」答；真要改日程，还得走确认那一步")
        elif qa_view and rule_hit:
            out.append("你在问答这一栏说话 → 规则层想办的事要再复核一遍才认")
        elif rule_hit and sem is None:
            # 多钉了一个 `sem is None`：这句的"没往外问过"只有**真的没叫语义层**时才算事实
            out.append(f"规则层认得这种说法（{INTENT_MEANING.get(rule_hit, rule_hit)}）→ 不用再猜一次")
        elif sem and sem_intent == INTENT_CHAT:
            out.append("没有办事的说法、也没有时间线索 → 按聊天答")

    # ③ 兜底的一句实话：语义层这次**没参与判定**（没配 Key / 规则层已经认得 / 材料先拦下）
    # ⚠️ 这一族的话术**不能出现"模型"二字**（红线：学生屏幕上不暴露技术内脏，
    #    `test_38` 的 FORBIDDEN 里就有它；第一版写"这一句没问模型"被 test_01/test_38 当场抓回去）。
    if not out and sem is None:
        out.append("这一句判定没叫语义层参与 → 只依据规则层认出来的说法，少一次可能的猜错")

    return [{"step": 0, "phase": "🧭 判定", "answer": line} for line in out[:3]]


def _schedule_tools_allowed(module_key: str, sem_intent: str | None, message: str) -> bool:
    """判断当前语境该不该把出待办卡的只读工具交给模型。

    原则：planner 主业就是排日程，始终给；其他模块只有语义层或规则层
    明确判成 add_todo 才给，否则模型会把闲聊/问候/模块名也当成待办名挂卡。
    """
    # ★ 纯生理状态感叹（"我饿了""好累啊"）：一个排程工具都不给。
    #   这正是"闲聊被推销成待办"的入口——学生只是在说状态（见 CHITCHAT_RULES）。
    if physio_state_only(message):
        return False
    if module_key == "planner":
        return True
    if sem_intent == INTENT_ADD_TODO:
        return True
    return wants_add_todo(message)


# ============ 学生侧话术（同一件故障，两种听众） ============
# 后台那套话（"HTTP 401 / API Key 无效 / 去管理控制台改「API 配置」"）是给管理员看的，
# 他知道去哪儿修；**学生不需要、也不该在对话里看到这套字** —— 那是把产品拆开给人看内脏。
# 所以异常带 `kind`（机器标签），这里按 kind 挑一句人话，全项目就这一处映射。
# ⚠️ 两条纪律：
#   1. 别用 message 关键字猜 kind（改一个字就漏），要加新错就顺手补 kind + 这里补一行；
#   2. 翻译只发生在**发给学生的这一刻**，用量流水（app/data/llm_usage.jsonl）里
#      照旧存技术原文 —— 管理员看不见真实原因，这道门就白设了。
_STUDENT_LLM_WORDS = {
    "no_key": "我这边现在答不上来——负责回答的服务还没接通。稍后再问一次，或者叫管理员看一下。",
    "auth": "负责回答的服务说这次的授权不对（可能过期了）。这句我答不了，叫管理员核对一下。",
    "rate_limited": "负责回答的服务这会儿太忙，被限流了。稍等十几秒再问我一次。",
    "unreachable": "我联系不上负责回答的服务，可能是网络问题。稍后再试，或者叫管理员看一下。",
    "blocked_address": "回答服务的地址配置不合规（默认不允许指向内网地址），这句我答不了。",
    "bad_format": "回答服务给我的东西不对劲（地址可能填错了），这句我答不了。",
    "upstream": "回答服务出了点状况，这句我答不了。稍后再问我一次。",
    "unknown": "我这会儿答不上这句，稍后再问我一次。",
}


def _say_llm_error(e: Exception) -> str:
    """把一次调用失败翻成**学生能懂、且不带技术内脏**的一句话。"""
    kind = getattr(e, "kind", "unknown")
    return "⚠️ " + _STUDENT_LLM_WORDS.get(kind, _STUDENT_LLM_WORDS["unknown"])


def _wants_sse(req: Request) -> bool:
    """客户端**明确**要 SSE 才流式（`Accept: text/event-stream`），默认仍回今天那份 JSON。

    为什么不无条件改成 SSE（施工单 §4.4 是直接改）：`/api/chat` 的读方不止学生端一处 ——
    `plan.html:499`（课表导入）、`station.html:201`（快递客服）都是 `await r.json()`，
    测试里更有 **227 处** `post("/api/chat", ...).json()`（35 个文件）。
    无条件换出口 = 一次把这三族全弄红；协商 = 新增一条分支，老路一个字不动。
    """
    return "text/event-stream" in (req.headers.get("accept") or "")


# 队列里的"结束"记号。不用 None：None 也可能是合法事件，撞上了就提前收尾（看不见的截断）
_SSE_END = object()


@app.post("/api/chat")
async def chat(req: Request):
    """对话入口（两条出口：**默认 JSON**，客户端点名要 SSE 才流式）。

    **本函数只干两件事：选出口 + 补一条"我把这句听成什么"再交出去。**
    真正的对话逻辑全在 `_chat_impl` 里 —— 它有一千多行、几十个 return，
    每条分支各回各的卡。想让"听成什么"**一条都不漏**，只能在这儿统一补：
    挨个 return 去加必然漏（实测就漏了"加待办缺细节（追问）"那条，
    而那正是学生说错话时最该看到解释的一条）。

    `meta` 是 `_chat_impl` 回填中间结论的小本子（目前只有判定层的 `read`）。
    用参数传而不是全局变量/上下文变量：同一时刻可能有多个请求在跑，
    全局量会串（A 学生的判定被写进 B 学生的回复）。

    SSE 那条出口的**时序是本刀唯一的风险**（施工单 §4.4 原话"最易写错处"）：
    `token`/`trace` 只是**引擎原话的预览**，`done.v` 必须是**跑完全套 claim 守卫之后**的权威答案
    （`_no_card_answer()` 那一族"它自称已提交、其实库里没有"的纠正）。
    所以这里不是"一条生成器从头 yield 到尾"，而是**后台把完整的 `_chat_impl` 跑完（事件顺手推进队列）
    → 它返回了才发 done**：守卫与 done 的先后由代码结构保证，不靠"记得写在后面"。
    """
    if not _wants_sse(req):
        return await _chat_json(req)

    # ⚠️ **必须在开始响应之前把请求体读干净**（这行不是冗余，是本刀第一个真机 bug）：
    #    响应一开始往外发，uvicorn 之后给这个连接的 `receive()` 就只有 `http.disconnect`，
    #    后台任务里再 `await req.json()` 会当场抛 ClientDisconnect → 整句话被当成空请求，
    #    学生看到的是"这句话我没听懂"。读一次就缓存进 `req._body`，
    #    下面 `_chat_impl` 里那句 `await req.json()` 拿的是缓存，不会读第二遍。
    try:
        await req.body()
    except Exception:
        pass        # 读不动也照样往下走：_chat_impl 会按"请求体不合法"回 400 那句人话

    q: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict) -> None:
        await q.put(ev)

    async def _run() -> None:
        try:
            resp = await _chat_json(req, emit=emit)
            if isinstance(resp, dict):
                await q.put({"k": "done", "v": resp})
            else:
                # 走到这儿是"请求体本身没发对"那一族（400）。JSON 出口有状态码可回，
                # 流式这边已经 200 了，只能给一句人话 —— 至少不白屏、不留半句。
                await q.put({"k": "error", "v": {"answer": "这句话我没听懂，麻烦再说一遍。"}})
        except Exception as e:
            # 流式中途失败：**绝不**留半截当没事（前端据此把气泡整句换成一句人话）
            await q.put({"k": "error", "v": {"answer": _say_llm_error(e)}})
        finally:
            await q.put(_SSE_END)

    task = asyncio.create_task(_run())

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev is _SSE_END:
                    break
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            # 学生中途关掉页面 → 这里会被取消；那个还在跑、还在烧额度的后台任务得收掉
            if not task.done():
                task.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream; charset=utf-8",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


async def _chat_json(req: Request, emit=None):
    """JSON 出口的本体：跑 `_chat_impl` + 统一补"听成什么"。SSE 那条也复用它（两路口径必然一致）。

    `emit` 只在 SSE 时有值，一路传到引擎的 `on_event`；**默认 None = 行为与改前逐字相同**。
    """
    meta: dict = {}
    resp = await _chat_impl(req, meta, emit=emit)
    read = meta.get("read")
    # #60-A：出口一次拼上「为什么」那几条（`read` 在前、`reasons` 跟在后面，都在引擎轨迹之前）
    reasons = [r for r in (meta.get("reasons") or []) if isinstance(r, dict)]
    # ⚠️ 判空条件不能挂在 `read` 上：那样 `read` 万一没设，reasons 会**跟着一起静默消失**
    #    （症状正好是"补了 reasons 但屏幕上没有"，最难查）。两条各自成立才各自上。
    head = ([read] if isinstance(read, dict) else []) + reasons
    if head and isinstance(resp, dict):
        try:
            tr = [t for t in (resp.get("trace") or []) if isinstance(t, dict)]
            resp["trace"] = head + tr
        except Exception:
            pass
    return resp


async def _run_agent_loop(*, module_key, session_id, message, prompt, tools,
                          persona_key, model_config, emit):
    """组装引擎 + 跑 ReAct 循环 + 收尾补条 + 出口（原先 `_chat_impl` 的 4) 那一段，原样搬过来）。

    #61-C 抽出来的唯一目的是**让它有两个调用点**：正常走到这儿、或 Agent 模式跳过快路直接到这儿。
    纪律：这段代码全项目只有这一份，两个调用点参数形状完全一致 —— 不许复制第二份出来各改各的。
    """
    # 4) 组装引擎并跑 ReAct 循环（会按需调用工具）
    engine = AgentEngine(
        system_prompt=prompt, tools=tools, session_id=session_id,
        llm_override=model_config,
    )

    # 之前聊过的内容（让 Agent 记得住上一轮商量到哪了）
    history = get_conversation(session_id)

    # 规划模块 + 没配密钥时，走确定性的离线 Mock 助手。
    # 这样「查空档 → 给候选 → 学生勾选 → 写入」这套交互不用大模型也能完整演示，
    # 评委没看到密钥也不影响看效果。
    # 注意：若学生端自带了有效 API（model_config 有 key），就跳过 Mock，走真模型。
    if module_key == "planner" and not get_llm_config(override=model_config)["api_key"]:
        # ⚠️ 这条 Mock 出口**自己 return**，走不到下面那道出口复核 ——
        # 但它恰恰是评委没配密钥时看到的那一幕：「每天背30个单词」在这儿会摊出三段
        # 候选，等于当着人面前把"每天"扔掉。判据跟别处同一道（`unsupported_period`）。
        _per_mock = unsupported_period(message)
        if _per_mock:
            return _period_reject_response(session_id, message, _per_mock)
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
        if emit is None:
            # 非流式那条：**调用形状与改前逐字相同**。测试里有 14 处假引擎写的是
            # `async def run(self, message, history=None)`，多传一个参数就是 TypeError
            # ——而 TypeError 会被下面那支 `except Exception` 咽成"这句没答成"，
            # 红得一塌糊涂却看不出原因（刀 3 的假货签名教训，别再吃第二遍）。
            result = await engine.run(message, history=history)
        else:
            result = await engine.run(message, history=history, on_event=emit)
    except LLMError as e:
        # 已知原因：密钥或网络问题。**日志和管理台留技术原文，学生只看到一句人话**
        print(f"[LLM] 本轮对话没答成（kind={getattr(e, 'kind', '?')}）：{e}")
        return {
            "module": module_key,
            "session_id": session_id,
            "answer": _say_llm_error(e),
            "trace": [{"step": 1, "phase": "❌ 没能联系上负责回答的服务",
                       "answer": _say_llm_error(e)}],
        }
    except Exception as e:  # 兜底：任何意外都不该让演示现场崩掉
        if DEBUG:
            import traceback
            traceback.print_exc()  # 开了 DEBUG 就打印完整堆栈，方便排查
        print(f"[ERROR] /api/chat 兜底捕获：{type(e).__name__}: {e}")
        return {
            "module": module_key,
            "session_id": session_id,
            "answer": _say_llm_error(e),
            "trace": [{"step": 1, "phase": "❌ 这一步没办成", "answer": _say_llm_error(e)}],
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
    final_trace = list(result.get("trace") or [])
    tt_source = ""   # 兜底补卡时课程清单的出处（"管家回话" / "文件原文"），只在回话里带一句

    # ── 出卡环节诊断（visible in 学生端「🧠 我是这么想的」面板）──
    # 即使最后没出卡，学生也能在这块看到卡在哪一档：
    #   ① 这句有没有被识别成「课表导入」  ② 模型自己有没有调提案工具
    #   ③ 从回话抠出几门 / 从文件原文抠出几门  ④ 最终为什么没出卡
    _tt_diag = []
    tt_detected = looks_like_timetable_import(message)
    if tt_detected:
        _tt_diag.append({
            "step": 1,
            "phase": "🔍 课表导入探测：命中（识别成「读课表文件导入」）",
            "answer": f"这轮回答里带了 {len(model_opts)} 个候选",
        })
    elif "课表" in (message or ""):
        # 提了"课表"却没命中正则 → 典型"说法逃过探测"，单独留痕提醒
        _tt_diag.append({
            "step": 1,
            "phase": "🔍 课表导入探测：未命中（提到课表但说法逃过正则）",
            "answer": "被当成普通/待办请求处理；若本意是导入课表，请用「上传课表」模板按钮（文案含 read_uploaded_file，必命中）",
        })

    if tt_detected and not model_opts:
        # 课表导入请求走**自己的**兜底，绝不进下面的待办补条——
        # 那会把模型话术里的「确认修改」当任务名，挂出
        # 「要不要把 确认修改 排进日程」的假卡（2026-10-03 学生截图实测）。
        # 正路是：模型读了文件却忘了调 propose_timetable_change、只在文字里
        # 念了一遍课程清单 → 系统把清单抠回来，自己把「确认修改」卡挂出来
        # （写库仍然只在学生点卡之后，执行层不经过模型）。
        tt_courses = timetable_courses_from_reply(result.get("answer") or "")
        tt_src = "管家回话"
        tt_from_file = _tt_file_courses(message, session_id)
        _tt_diag.append({
            "step": 2,
            "phase": "🔍 从这轮回答里抠课程",
            "answer": f"回话里抠出 {len(tt_courses or [])} 门可识别课程",
        })
        _tt_diag.append({
            "step": 3,
            "phase": "🔍 从文件原文抠课",
            "answer": (f"文件原文抠出 {len(tt_from_file or [])} 门"
                       if tt_from_file else "未找到挂到本会话的课表文件，或文件非文本/表格格式（图片、PDF 截图都抠不出）"),
        })
        # 两路都抠，取**更全**的那份：学生那份文件原文（一天一行、；串多门）
        # 经常比模型回话完整；反过来模型能替他把"第几节"换算成钟点。
        # 宁可多列——清单全在卡上，学生看了再决定点不点确认。
        if tt_from_file and (not tt_courses or len(tt_from_file) > len(tt_courses)):
            tt_courses, tt_src = tt_from_file, "文件原文"
        if tt_courses:
            model_opts = [{
                "kind": "timetable_change",
                # 卡上只给门数：学生要的是"要不要改"，不是把十几门课再念一遍
                # （2026-10-04 学生原话「不用每门课单独拎出来」）。
                "summary": f"导入课表（共 {len(tt_courses)} 门课）",
                "courses": tt_courses,
            }]
            tt_source = tt_src
            _tt_diag.append({
                "step": 4,
                "phase": "🛟 这轮回答没给结构化课表 → 系统从话里补出「确认修改」卡",
                "answer": f"最终采用：{tt_src}，共 {len(tt_courses)} 门 → 出卡",
            })
        else:
            final_answer = _tt_import_no_card_answer()
            _tt_diag.append({
                "step": 4,
                "phase": "⚠️ 课表导入没出卡 → 如实说明，绝不挂假卡",
                "answer": "回答和文件两路都读不出课程：可能是文件没被真读到、是图片型 PDF、或排版不是表格/纯文本行",
            })
    elif tt_detected and model_opts:
        # 模型自己调了 propose_timetable_change，引擎已给出卡，这里只统一回话
        _tt_diag.append({
            "step": 2,
            "phase": "✅ 引擎自己出了「确认修改」卡（系统只统一回话）",
            "answer": f"timetable_change 卡数 = {len([o for o in model_opts if o.get('kind') == 'timetable_change'])}",
        })
    elif not model_opts:
        recent_add_req = _recent_add_request(session_id)
        # ★ 模型有时**不调工具，而是把工具调用当文本打出来**（2026-10-11 真机：学生看到
        #   `propose_todo_tool / {"title": "写作业", "when": "2024-01-05 15:00~18:00"}`）。
        #   这一幕同时踩两条线：卡没出来（学生白等），而且把内部机制端到了学生面前。
        #   两步一起做：① 认出这个形状就**替它执行这一次调用**（同一件函数，参数校验、
        #   空档检查、周期否决一道不少 —— "打成字"和"真调用"结果必须一致）；
        #   ② 无论执行得出执行不出，**那段文本都不许回显给学生**（换成系统话术）。
        _echo = looks_like_tool_echo(result["answer"] or "")
        if _echo:
            echoed = textual_tool_card(result["answer"])
            if echoed is not None:
                model_opts = [echoed]
                final_answer = _rescue_answer(echoed)
            else:
                final_answer = _no_card_answer()
            final_trace = list(result.get("trace") or []) + [{
                "step": 1,
                "phase": "🛟 它把工具调用打成了字 → 系统替它执行这一次（那段文本不回显）",
                "answer": (echoed or {}).get("summary") or "那段调用没能变成卡片，已如实说明",
            }]
        else:
            rescued = rescue_proposal_from_reply(result["answer"], recent_add_req)
            if rescued is not None:
                model_opts = [rescued]
                # 它那段文字在这一刻是自相矛盾的（前面说"还没生成出来"、后面说"已发出"），
                # 整段换成系统话术，别让学生去猜哪句是真的。
                final_answer = _rescue_answer(rescued)
                final_trace = list(result["trace"] or []) + [{
                    "step": 1,
                    "phase": "🛟 这轮回答没给结构化结果 → 系统从话里补出确认条",
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
                    "phase": "⚠️ 回话说已提交但系统补不出卡 → 如实纠正，不让学生空等",
                }]

    # ★ 模型这一路出口再收一次口（判据同 `_offer_response`、同 `unsupported_period`）。
    #   工具里那道 `period_reject` 看的是**模型自己传的参数** —— 它把「每个月剪一次头发」
    #   改写成"剪头发 + 某个日子"就绕过去了（2026-10-11 真机实测：卡照样挂出来）。
    #   这里拿**学生原话**判：句子要的周期表达不了，这一路生成的待办卡**一张都不留**，
    #   回话换成那句如实说明（暂存里也就没有卡，学生点不到、也就不会写库）。
    _per_out = unsupported_period(message)
    if _per_out:
        # ⚠️ 判"是不是待办卡"不能只看 `kind`：无密钥时那段兜底（`mock_planner`）摊出来的
        # 候选**根本没有 kind 字段**（真机上这一段是模型跑的，形状也一样），
        # 只挑 `kind.startswith("todo")` 就等于放过了这条路 —— 2026-10-11 一批真机句子打出来的。
        # 所以反过来写：**只留课表那几种明确的非待办卡**，其余一律撤下。
        _non_todo = ("timetable_change", "timetable_clear", "course_add")
        _keep = [o for o in model_opts if str(o.get("kind") or "") in _non_todo]
        if len(_keep) != len(model_opts):
            model_opts = _keep
            final_answer = _period_reject_answer(_per_out)
            final_trace = final_trace + [{
                "step": 1,
                "phase": f"🚫 出口复核：「{_per_out}」这种周期接不了 → 撤下这一路生成的待办卡",
                "answer": "没挂卡，也没写库"}]

    # 把出卡环节的诊断拼回 trace（学生端「我是这么想的」面板可见，即使没出卡也能定位）
    final_trace = final_trace + _tt_diag

    # 上传课表 → 只要一张「是否修改」卡，回话统一成一句"要不要改"。
    # 模型自己调了 propose_timetable_change 时最爱在文字里把十几门课再念一遍，
    # 那句原话必须换掉（学生原话：不用每门课单独拎出来）——
    # 清单他自己在文件里看过，确认后周表上也会一条条列出来。
    if looks_like_timetable_import(message):
        _tt_opts = [o for o in model_opts if o.get("kind") == "timetable_change"]
        if _tt_opts:
            _n = len(_tt_opts[-1].get("courses") or [])
            final_answer = _tt_confirm_answer(_n, tt_source)

    # 最后一道"不端内脏上桌"的保险（红线 8）：上面的分支都处理过了，但模型想法随时会变——
    # 只要**最终要发给学生的这句话**里还留着"工具名 + JSON"那个形状，就换掉它。
    # 宁可回一句"这一步没办成"，也不能让学生看见系统内部怎么调用的。
    if looks_like_tool_echo(final_answer or ""):
        final_answer = _no_card_answer()
    # 同一类"内部记账的话被学生听见"：系统往会话历史里写过「已生成待办确认：…」这种
    # **挂卡标记**（它存在的意义就是让"读历史"的那几支认出"这条已经挂过卡"，见
    # `_is_system_note`）。模型读到这句会**原样复读**给学生 ——
    # 2026-10-11 真机：连着两次说「那就周六吧」，第二次回答就只剩这句标记，
    # 卡也没有。看着像"它答非所问"，实际是它把我们的账本念出来了。
    if _is_system_note(final_answer or ""):
        # 这句是系统自己写进历史里的账（"已生成待办确认：…"），不是回给学生的话。
        # 直接回 `_no_card_answer()` 那句"刚才说已提交不作数"其实对不上 ——
        # 它没自称提交过，只是念了一句账目，所以另写一句，别把不相干的纠正扣上去。
        final_answer = ("⚠️ 刚才那句是我自己记账的话，不是说给你听的 —— 这一轮我没办成什么。\n"
                        "你再说一遍要加什么（比如「帮我加个游泳」），我重新出一张确认条；"
                        "点【确认加入】或回一句「确认」才会真写进日程。")

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

async def _chat_impl(req: Request, meta: dict, emit=None):
    """对话主体（人话：前端把用户的话发到这里，返回回答 + Agent 思考轨迹）。

    前端可以显式带 module 字段（如 {"message": "...", "module": "express"}）直接锁定模块，
    方便主页四个模块各自独立对话；不带的则交给 router 自动判断意图。
    """
    try:
        body = await req.json()
    except Exception:
        # 请求体不是合法 JSON / 不是 UTF-8（实测 Windows 下 `curl -d` 发中文发的是 GBK，
        # 直接抛到框架外面就是一个 500）。回 400 并说清是编码问题——
        # 500 只会让前端显示"网络异常"，谁也看不出是这一句。
        return JSONResponse({"error": "请求体不是合法的 JSON（请确认按 UTF-8 编码）"},
                            status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "请求体必须是一个 JSON 对象"}, status_code=400)
    # #61-C：开关**只在这里取一次**，后面全读这个局部量（两处各算 = 第二个 12b）。
    _agent_mode = _agent_mode_on(body)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 学生端选的性格（前端随对话发来；只有合法 key 才生效，否则忽略）
    persona_key = body.get("persona")
    if not is_valid(persona_key):
        persona_key = None

    # 学生端在对话框里自己填的 API：存在浏览器 localStorage，**每轮对话随请求上传**
    # （是服务器拿它去调模型，不是前端直连）。用完即弃：只活在这一次请求里，
    # 不写 settings.json、不进任何数据文件（test_34 钉住）。
    # ⚠️ 这里过 `sanitize_override`：只留 {api_key, model, base_url} 三个字段，其余一概不带过来。
    # base_url 在这一层只判"能不能用"（协议 + 有主机名）；**内网那一层不在这里判**，
    # 判它在 `llm/client.py` 真要发请求之前（config.check_base_url_ssrf），
    # 学生传来的地址和管理后台填的地址走的是同一个闸门。为什么不在这里判：
    # 抛错会把整轮对话打断，而"填了个没法用的地址"应该是退回服务器配置，不是 500。
    model_config = sanitize_override(body.get("model_config"))

    # 会话 id：前端带上，服务端就能把几轮对话串起来。
    # 没带就服务端生成一个并在响应里返回，前端存起来下次继续用。
    # ⚠️ 位置在"判定层"之前：下面要按会话历史问一次语义层，得先有它。
    session_id = (body.get("session_id") or "").strip() or uuid.uuid4().hex[:12]

    # ── 0) 判定层：先把这句话**听懂**（归哪个类型 + 想干什么），再往下走 ──────
    #  2026-09-27 重做。此前"归哪个模块"是 `router._QUICK_MAP` 那 50 多个
    #  `if keyword in text` 说了算，"想干什么"另有一条 30 多张表的规则链，
    #  两处各判各的、互不通气——于是学生在**校园问答**栏里问
    #  「我要去哪里取快递」，关键词表看见"取快递"就把话分给了快递模块，
    #  而加待办那条分支又是**不分模块**的，结果给他出了一张待办卡。
    #  现在合成一件事：**先分"问"还是"办"，再定类型**（app/agent/intent.py）。
    #    · 问（…在哪 / 几点 / 能不能 / 有没有 / 怎么走）→ 一律不判"要办事"；
    #    · 想问就按问的**内容**归模块（问规定地点流程→faq，问自己的快递→express…）；
    #    · 只有真想把它排进日程才算 add_todo，归 planner。
    #  顺序是"语义优先、关键词兜底"：样本库（零成本、离线）→ 语义层（一次调用）
    #  → 关键词表。三道都落空时**行为跟以前一模一样**（不倒退）。
    #  代价说明：只在"规则没接住"或"规则想办事、可这话像是在问"时才多问一次，
    #  常规下单句（"加个健身"）依旧零 API 调用。
    _explicit_module = body.get("module")
    # 显式栏目也要过同一道"有人接得住"的判据（`_module_handled`）：
    # 低代码那一路的前端可以直接带 `module: "custom:<id>"` 说话，那是合法的；
    # 但**不存在的 id 一样进不来** —— 显式指定不等于免检。
    if not _module_handled(_explicit_module):
        _explicit_module = None

    _rule_hit = _rule_intent(message)
    _asks = looks_like_question(message)
    # 已经在"问答型栏目"里说话，本身就是一个强类型信号（比如「校园问答」那一栏）。
    # 低代码那一路也算这类栏目：它不是 planner，规则层要是想替学生排日程，一样得先复核。
    _qa_view = (_explicit_module in _QA_MODULES
                or service_of_module(_explicit_module) is not None)
    _need_review = bool(_rule_hit) and (_asks or _qa_view)
    _sem = None
    # ★ 粘贴材料（#43）先认出来，**连语义层都不 call**：
    #   ① 几百字的材料喂给 classify 是一次白花花的调用，结论还不稳（实测判"问事"）；
    #   ② 更要紧的是 classify 带着上一轮历史 —— 用户那次就是被"上文在问图书馆"带走的。
    #   ⚠️ 课表粘贴那一路的让路写在判据**里面**（`material.looks_like_material`），不写在这里：
    #   同一判断只放一处，两处各写一半迟早漏一个。
    _is_material = looks_like_material(message)
    if (_rule_hit is None or _need_review) and not is_confirmation(message) and not _is_material:
        try:
            _sem = await classify(message, get_conversation(session_id),
                                  override=model_config)
        except Exception:
            _sem = None          # 语义层挂了也不能让对话崩，掉回老路
    _sem_intent = (_sem or {}).get("intent") or None
    _sem_module = (_sem or {}).get("module") or None
    # ★ 生理状态句的**确定性否决**（2026-09-30 补，见 intent.physio_state_only）。
    #   规则层对「我饿了」是干净的（`wants_add_todo` 返回 False），可线上**语义层
    #   把它判成了 add_todo**，一张写着「饿」的待办卡就挂到了学生眼前——他只是在说状态。
    #   提示词里补例子只能降低概率、消不掉（模型是非确定性的），所以这里加一条跟
    #   问句守卫同款的**硬否决**：纯状态感叹 → 强制按 chat 走，不出卡、不推销。
    _physio = physio_state_only(message)
    if _physio:
        # 例外：学生正在补上一轮"要安排什么事"的追问——那句状态话是在**回答**，
        # 不是自言自语，不能拦（拦了追问就原地打转）。标记见 `asking_add`。
        try:
            _recent = get_conversation(session_id)[-4:]
            if any("加待办缺细节" in (m.get("content") or "")
                   for m in _recent if m.get("role") == "assistant"):
                _physio = False
        except Exception:
            pass
    if _physio:
        if _sem:            # 只改语义层给的**结论**，不无中生有造一份
            _sem = dict(_sem, intent=INTENT_CHAT, module=DEFAULT_MODULE)
        _sem_intent = INTENT_CHAT
        _sem_module = DEFAULT_MODULE
    # 规则判了"要办事"、可这句话在问事（或者人就在问答栏里）→ **以语义层为准**。
    # 只在两者结论**不一致**时才否决规则；判得一样就照旧走，不额外改变任何行为。
    # 判据刻意收窄成两种**正面肯定**（宁可漏判、不可替学生办错事）：
    #   · 这句话本身就带提问信号（`_asks`；「我要去哪里取快递」就是这类），或者
    #   · 语义层明确说 `chat`——即"他什么都没想办，就是在问/在闲聊"。
    # 不收"只要结论不同就否决"：样本库是模糊匹配，偶尔会认错一句近似的说法，
    # 而否决一张**本来就该出**的卡（"帮我安排周二的游泳"）比漏判更让学生恼火。
    _rule_veto = bool(_need_review and _sem_intent and _sem_intent != _rule_hit
                      and (_asks or _sem_intent == INTENT_CHAT))
    # ★ 「每月 / 每个周末 / 下个月」这类周期的**不分层否决**（判据见 planner.unsupported_period）。
    #   规则层从来不接它，可**样本库/语义层能单独判成 add_todo**并出卡、还把这句学进样本库；
    #   于是学生说"以后每月"，拿到的是一条"某一天一次"的待办（实测就是这么翻车的）。
    #   这里只算"拦不拦"，怎么如实跟他说、给哪几种能办的说法，在下面那条确定性分支里。
    #   ⚠️ 「每天」2026-10-13 起从这个名单里**出去了**（一条 daily 习惯表达得了，只进代办不进日历）。
    _period = unsupported_period(message)
    _period_block = bool(_period) and (
        _rule_hit == INTENT_ADD_TODO or _sem_intent == INTENT_ADD_TODO)
    # ★ 「去完图书馆了」这类**宣布办完了**的句子：学习层/语义层不许把它单独送进"新增"。
    #   跟上面周期那条是同一个病（红线 12：样本库能单独接单，规则层却从来不认）。
    #   本机实测的铁证在 app/data/intent_samples.json（gitignored）里躺着一条
    #     {"text":"去完图书馆了","intent":"add_todo","hits":1}
    #   —— 当年模型判错、还真出了卡，于是 `remember()` 把这句**按错的意图学死了**；
    #   现在它每次都以 1.0 **全命中**回 add_todo，加待办分支（就在下面几百行处）
    #   抢在 3h 标记完成之前接管，弹出一张标题写着「完图书馆」的卡（学生截图）。
    #   处理照 `_physio` 那一档的老规矩：**改语义层给的结论，不无中生有造一份**——
    #   把 intent 纠正成 done_todo，于是 ①加待办那条分支进不来，
    #   ②3h 按确定性路径接管，③思考轨迹也不会写着"办事（加待办）"却干着别的事。
    # ⚠️ 只纠 `_sem_intent == INTENT_ADD_TODO` 这一种：规则层自己认得的话
    #   （`wants_add_todo` 为真）压根进不了这条 —— `wants_done_todo` 见 add 就让路。
    _done_claim = wants_done_todo(message)
    if _done_claim and _sem_intent == INTENT_ADD_TODO:
        if _sem:
            _sem = dict(_sem, intent=INTENT_DONE)
        _sem_intent = INTENT_DONE
    # 「我把这句听成什么」——交给外面的 `chat()` 统一插进思考轨迹（见 `_read_step`）。
    # 在这儿只**记下结论**，不重新判定：`_sem` / `_sem_intent` / `_rule_hit` 就是判定层
    # 刚算出来的那三个，照抄即可。
    # ⚠️ 之所以要绕这一下：这个函数有**几十个 return**（每条分支各回各的卡），
    #    想在每个 return 上都补一句必然漏几个 —— 实测就漏了"加待办缺细节（追问）"
    #    那条（学生报障那类句子的必经之路）。记在 meta 里、出口补一次，覆盖全部。
    meta["read"] = _read_step(_sem, _sem_intent, _rule_hit)
    # ⚠️ #60-A：`reasons` 与 `read` **互相独立** —— 下面那几个覆盖 `read` 的分支点（追问档 / 材料档 / 兜底档）一个都不动：
    #    它们改的是"听成了什么"那一句，而 `reasons` 记的是"凭什么、否决了什么"，被覆盖就会变成第二份真相。
    #    同样只在这里记下结论、出口拼一次（`_chat_json`）—— 这函数有几十个 return，逐个补必漏。
    meta["reasons"] = _reason_steps(
        rule_hit=_rule_hit, asks=_asks, qa_view=_qa_view,
        rule_veto=_rule_veto, period_block=_period_block, done_claim=_done_claim,
        physio=_physio, is_material=_is_material, sem=_sem, sem_intent=_sem_intent,
    )

    # （这里曾经有一道"问答栏不许办待办"的拦截：学生在校园问答里说删/改/标记完成，
    #  系统只回一句『这件事要在「我的日程」里办哦』+ 一颗跳转按钮。）
    # 2026-10-09 拆掉。它的前提已经不成立：
    #  · 前提一"卡片和确认条只在日程栏目才有"——全站合成一条对话流之后（student.html
    #    sessionId 只认一个 key），卡片在哪儿出的都显示在同一条流里，也都有确认条；
    #  · 前提二"在问答栏办了会误操作"——写库永远要学生点确认条，跟他在哪个栏目说话无关，
    #    这条红线由 test_31（模型没有写工具）+ 提案/执行分离保证，不靠拦学生。
    # 结果：同一句"把看牙医那个待办删了"，之前在问答栏被挡、在日程栏能办——
    # 而学生说话时**心里没有"栏目"这个概念**。下面的确定性分支不分模块，照原样接管。

    # 1) 归哪个模块管：**显式栏目 > 语义层 > 关键词兜底**。
    #    学生在哪个栏目说话，本身就是最强的类型信号；他没指定时才轮到判定层。
    module_key = _explicit_module or _sem_module
    if not (module_key and _module_handled(module_key)):
        module_key = await router.route(message, override=model_config, sem=_sem)
    if not _module_handled(module_key):
        module_key = DEFAULT_MODULE
    # ★ 材料一律归日程这侧（#43）。用户那次是**收起面板后**粘的：前端不带 module
    #   （`student.html:1954` 只在 `currentModule` 有值时才带，`:2040` 收起面板把它置空），
    #   于是这句全交给判定层，长文本被判成"问事" → 落到 faq → 模型拿着上文去答。
    #   放在这里（而不是判定层里面）是因为：这是**形状**判断，不是语义判断，
    #   它不该跟着模型心情漂。
    if _is_material:
        module_key = "planner"

    # 1a) 触发词是**硬信号**（红线 5：硬信号得单独提前，光靠判定层判对不够）。
    #     为什么光有 `router` 那一档不够：配了密钥时语义层几乎总会给出一个合法 module
    #     （`faq` 也算合法），于是 1460 那次 `router.route()` **根本不会被调用** ——
    #     触发词档只在"没密钥 / 判定层落空"时才生效，真机上能不能命中全看模型心情，那不叫确定性。
    #     但它也不许抢：只在"这句还没被认成别的事要办"（意图为空或 chat）时接管，
    #     并且问句一律不接管（复用 `looks_like_question`，不另写判据）。
    if not is_custom_module(module_key) and not looks_like_question(message):
        _eff_intent = _sem_intent or _rule_hit
        if _eff_intent in (None, INTENT_CHAT):
            _svc_hit = match_service_by_trigger(message)
            if _svc_hit:
                module_key = custom_module(_svc_hit["id"])

    # ★ 端边界（2026-10-17 #34）：**驿站端那台机器是店员的** —— 他没有"我的待办"，也没有"我的课表"，
    #   学生个人库既不该读、也不该写。这个判据**只在这里算一次**，下面两处读它：
    #     ① 该不该把"会挂出待办卡"的排程工具交给模型；② 确定性分支区走不走。
    #   不在两处各写一遍 `module_key == "station"`：同一判据抄两遍就会漏一处（红线 4/11），
    #   而漏掉的那一处恰好就是这次 P0 的形状。
    _station_surface = module_key == "station"

    # 1b) 低代码那一路（#22 单子 4）：module 是 `custom:<服务id>`，**它的"注册表"就是 services.json**。
    #     提示和工具箱都按这份运行时数据现算 —— 管理员改了定义，下一句就生效，不用重启；
    #     而且这里能进来越过这道闸，本身就说明 service_id 是真实存在的（`_module_handled` 回查过）。
    _custom_svc = service_of_module(module_key)
    if _custom_svc:
        prompt_src = lambda s=_custom_svc: custom_prompt(s)          # noqa: E731
        build_tools = lambda s=_custom_svc: custom_tools(s)          # noqa: E731
    else:
        prompt_src, build_tools = REGISTRY.get(module_key, (DEFAULT_PROMPT, lambda: {}))
    base_prompt = prompt_src() if callable(prompt_src) else prompt_src
    # 统一拼上"你会读文件"的说明 + 学生助手人设规矩 + 闲聊守则，每个模块都生效
    prompt = base_prompt + _now_block() + FILE_HINT + ASSISTANT_RULES + CHITCHAT_RULES
    # 学生端选了性格，且不是驿站模块（驿站用管理员配的人格），就把性格腔调拼进去。
    # ⚠️ 低代码服务**也不叠学生性格**：这项服务的语气是驿站定下来的（"登记员"），
    #    再叠一层"热情活泼/冷淡"只会让同一项服务在不同学生面前长得不一样，
    #    管理员那句"登记完回什么话"也就不作数了。
    if persona_key and module_key != "station" and not _custom_svc:
        prompt += "\n" + persona_block(persona_key)

    # 工具箱 = 模块自己的工具 + 通用的文件读取工具。
    # 把"读文件"做成通用工具而不是复制进每个模块，
    # 以后新增模块会自动带上这个能力，不用重复实现。
    tools = build_tools()
    tools.update(files_tools())
    # 把"查空档 + 把待办变成能点的确认条"的只读工具只交给确实要办日程的语境：
    #   · planner 模块主业就是排日程，始终拥有这些工具；
    #   · 其他模块：只有语义层或规则层明确判成 add_todo 才给，
    #     否则模型会把闲聊/问候/模块名也当成待办名挂卡。
    #     实测：faq 栏里问"你是谁"，模型却把"校园问答"当待办名、用旧提案的时间出了张确认卡。
    # 这些工具本身只读，但挂出确认条就是"代办"行为，必须对齐真实意图。
    # 驿站端一个排程工具都不给（#34）：这些工具本身只读，但**挂出确认条就是"代办"行为**，
    # 而店员那句"帮我加个铅球比赛"根本不是在排他自己的日程。
    if not _station_surface and _schedule_tools_allowed(module_key, _sem_intent, message):
        tools.update(scheduling_tools())

    # ★ 端级早分流（2026-10-17 P0，#34）：**驿站端没有"我的待办 / 我的课表"这回事**。
    #   真机事故：店员在 /station 说「我的代办」，被下面"列出待办"那条确定性分支接走，
    #   直接 `list_todos()` 把**学生个人待办**念给了店员（截图那句「📋 你目前的待办（共 1 条）」）。
    #   这不只是"看到"：同一个形状还能写 —— 加/删/改待办、改课表那些分支判据全是
    #   "这句话长什么样"，**没有一处问"谁在说"**（`/api/chat` 的任何调用方都能递这句话）。
    #
    #   为什么整段圈起来，而不是给二十多个分支各加一句 `and module_key != "station"`：
    #   那就是同一个判据抄二十遍（红线 4/11），漏一处就再串一次数据；
    #   而课表那条 `looks_like_timetable_import` 是**硬信号**、位置在最前面，最容易漏。
    #   ⇒ 端的边界靠**走不到**来保证，不靠"记得加 if"。
    #   驿站端从这里直接落到下面 4) 组装引擎那一步，由 station 的 agent 回答
    #   （它的工具箱里只有两个查询工具 —— 本来就没有写库能力，也就没有第二份 agent 调用代码）。
    #
    #   ⚠️ 只认 `station` 这一个端。`custom:<id>` 低代码那条是**学生端**用的（学生说"我丢了饭卡"
    #     要出登记卡），跟着拦进去就把那件功能废了；管理端走自己的接口，不在这儿。
    #   判据在上面算成 `_station_surface`，这里只读它（同一件事不许有两个写法）。
    if not _station_surface:
        # ★ #61-A 只读工具跨栏挂载：读不算"代办"，多给模型几只手不会多写一个字节。
        #   护栏有三道：① 只挂 READONLY_SPEC 里那 15 个（写侧一个都进不来，见 readonly.py）；
        #              ② `setdefault` 不覆盖模块自己那份 —— 说明书归模块维护，
        #                 谁熟这块业务谁来写，跨栏那份只补"这个栏里本来没有"的手；
        #              ③ 驿站端一只手都不挂：那 15 个里有 9 个是**学生个人数据**
        #                 （我的快递/我的订单/我的待办/我的课表），店员看见就是串数据（#34 那条血）。
        #                 ⇒ 写在闸门**圈内**，而不是在圈外再补一句 `if not _station_surface`：
        #                   那样 `_chat_impl` 顶层就有两道同款闸门，test_61 C1 钉的"恰好一处"会红，
        #                   更要紧的是"端的边界靠走不到来保证"（#34 的原话）多了一条破口。
        #   病根（实测）：工具箱按**模块**切，学生这句落到 faq 栏，他手里就没有
        #   `get_weekly_timetable` / `find_free_slots` / `list_day_todos` —— 模型想 Act，无手可动，
        #   只能凭上下文瞎猜或反问。
        for _n, _t in readonly_tools().items():
            tools.setdefault(_n, _t)

        # 2.5) **课表导入：第一句话就直接出「确认修改」卡**（2026-10-07 学生报障）
        #   现象：上传课表那句被判成「问事」（语义层给了 chat、module=schedule），
        #   第一句没弹卡；第二次追问反而弹了张**代办**卡——
        #   没被课表接走的话就掉进待办补条（那张假卡的老路）。
        #   学生的要求很明确：**第一次就直接改课表**。
        #
        #   判据：规则层明说（`looks_like_timetable_import` 命中）= **硬信号**，
        #       语义层猜的「chat / 问事」= 弱信号 → 以规则为准。
        #       跟 `physio_state_only` 是同款的确定性硬闸门，
        #       只是一个强制按 chat 走（不出卡），这个强制按办事走（直接出卡）。
        #
        #   位置必须在**所有待办分支之前**——分支顺序本身就是判定，
        #   放在后面就会被待办补条抢走（那正是"第二次弹代办卡"的成因）。
        #   只有真能从文件原文抠出课程才直接出卡；抠不出就照旧交给模型，不抢它的活
        #   （模型还能替他把"第几节"换算成钟点，那条路原样保留）。
        if looks_like_timetable_import(message):
            _tt_direct = _tt_file_courses(message, session_id)
            if _tt_direct:
                _tt_card = {
                    "kind": "timetable_change",
                    # 卡上只给门数：学生要的是"要不要改"，不是把十几门课再念一遍
                    # （2026-10-04 学生原话「不用每门课单独拎出来」）。
                    "summary": f"导入课表（共 {len(_tt_direct)} 门课）",
                    "courses": _tt_direct,
                }
                save_pending(session_id, [_tt_card])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                     f"已生成课表修改提案：{_tt_card['summary']}")
                # 这句规则层明说是"办事"，别再被写成"问事"——轨迹里写假话比不写更坏。
                meta["read"] = {
                    "step": 0,
                    "phase": "🧠 我把这句听成 **办事**（导入课表）｜依据：规则层（课表导入，硬信号）",
                    "answer": "",
                }
                return {
                    "module": "planner",
                    "session_id": session_id,
                    # 回话一律走 `_tt_confirm_answer`：跟下面模型那条路用的是同一句话，
                    # 免得"第一句话直接出卡"和"模型出卡"两种走法说法不一致
                    # （也才能带上「（清单来自文件原文）」这句出处）。
                    "answer": _tt_confirm_answer(len(_tt_direct), "文件原文"),
                    "trace": [{"step": 1,
                               "phase": "📄 课表导入：系统直接读文件出卡（第一句话就出，不用二次判断）",
                               "answer": f"文件原文抠出 {len(_tt_direct)} 门课"}],
                    "options": [_tt_card],
                    "awaiting_choice": True,
                }

        # 2.6) **快递/外卖丢失破损 → 第一句话就出「确认上报」卡**（2026-10-07 学生报障）
        #   现象：学生说"快递丢了 / 上报管理员"，被语义层判成「问事」，助手只回一长段
        #   查件话术、从没出卡，学生要上报却找不到入口（"没有出卡，也没有上传"）。
        #   学生的要求很明确：**说要上报就直接出卡**。
        #
        #   判据：规则层 `looks_like_work_order_report` 命中 = **硬信号**（上报/工单/丢失破损），
        #       语义层猜的「chat / 问事」= 弱信号 → 以规则为准（同款 timetable 硬闸门）。
        #   位置必须在**所有待办分支之前**——先出卡，不被待办补条抢走。
        #   卡是可取消的提案：学生点【确认上报】才写工单（走 /api/workorders，系统写库，
        #   不经过模型，守住"写库只归系统"的红线）；点【取消】什么都不做。
        _wo_kind = looks_like_work_order_report(message)
        # ★ 2026-10-16 #24 决策②：**自定义服务优先于通用工单**（红线 21 的下半场）。
        #   「报修」这个词在 `_WORK_ORDER_RE` 里是硬信号，而它恰好也是很多自建服务的正题
        #   （宿舍报修、设施报修…）。前面刚把这句归到 `custom:<宿舍报修>`，这里再劫持走，
        #   管理员建的服务就永远接不到活 —— 通用兜底不该盖过"用户明确说要接管这句话"的服务。
        #   没有自定义服务接管时（`module_key` 不是 custom）行为一字不变：照旧出上报卡。
        if _wo_kind and not is_custom_module(module_key):
            _wo_card = {
                "kind": "work_order_report",
                "summary": f"上报驿站：{_wo_kind}",
                "wo_kind": _wo_kind,
                "desc": "",
            }
            save_pending(session_id, [_wo_card])
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant",
                                 f"已生成工单上报提案：{_wo_card['summary']}")
            # 这句规则层明说是"办事"（上报工单），别再被写成"问事"——轨迹里写假话比不写更坏。
            meta["read"] = {
                "step": 0,
                "phase": "🧠 我把这句听成 **办事**（上报工单）｜依据：规则层（快递/外卖问题上报，硬信号）",
                "answer": "",
            }
            return {
                "module": "express",
                "session_id": session_id,
                "answer": (
                    "📮 听起来你的快递/外卖可能出了问题。要不要**上报驿站**立案跟进？\n"
                    "下面这条确认条点【确认上报】就会生成工单（也可填补充描述）；"
                    "点【取消】就不上报，数据一点都不会动。"
                ),
                "trace": [{"step": 1,
                           "phase": "📮 上报工单：系统直接出卡（不走语义判断）",
                           "answer": f"问题类型 = {_wo_kind}"}],
                "options": [_wo_card],
                "awaiting_choice": True,
            }

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
                # 改期：旧的那条要挪走（删旧 + 加新，不是就地改字段，方便追溯）。
                # 旧 id 只在改期提案里带着，普通加待办没有这一项，所以不影响别的路径。
                _old_id = picked.get("old_todo_id")
                if _old_id:
                    try:
                        delete_todo(_old_id)
                    except Exception:
                        pass
                # 用 .get() 而不是 picked['summary']：提案里的字段少一个就 500 了，
                # 学生只会看到一句"网络开小差了"。摘要本来就是可选的，自己拼一句就行。
                summary = (picked.get("summary")
                           or f"{picked.get('title', '待办')}｜{picked.get('date', '')}"
                              f"{picked.get('start', '')}-{picked.get('end', '')}")
                _retimed = bool(_old_id)
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"{'已改期' if _retimed else '已加入日程'}：{summary}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        (f"✅ 已改期：**{picked.get('title', '待办')}**｜{picked.get('date', '')}"
                         f"（{picked.get('weekday', '')}）"
                         f"{picked.get('start', '')}-{picked.get('end', '')}，"
                         f"旧的那段已经挪走了。"
                         if _retimed else
                         f"✅ 已加入日程：**{picked.get('title', '待办')}**｜{picked.get('date', '')}"
                         f"（{picked.get('weekday', '')}）"
                         f"{picked.get('start', '')}-{picked.get('end', '')}。"
                         )
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

            # **批量删除的确认**：学生说"都删了"时挂的那张条，他回一句"确认"就在这里执行。
            # 跟单条同一个道理：delete_todo 只有走到这里才会被调用，模型碰不到。
            # 逐条删、并**如实报数**（有几条已经不在了就说几条，不假装全删了）。
            rb = _pending_todo_remove_batch(session_id)
            if rb:
                take_pending(session_id)
                items = [x for x in (rb.get("items") or []) if isinstance(x, dict)]
                gone, ok_titles = [], []
                for it in items:
                    tid = it.get("todo_id") or ""
                    if tid and get_todo(tid) is not None:
                        delete_todo(tid)
                        ok_titles.append(it.get("title") or "待办")
                    else:
                        gone.append(it.get("title") or "待办")
                append_conversation(session_id, "user", message)
                summary = rb.get("summary") or f"共 {len(items)} 条"
                # 这条回执跟单条删除用的是同一个前缀（_WRITTEN_RECEIPTS 靠它认出"真删过了"）
                append_conversation(session_id, "assistant",
                                    f"已删除待办：{summary}"
                                    + (f"；另有 {len(gone)} 条已经不在了" if gone else ""))
                lines = "\n".join(f"· **{t}**" for t in ok_titles)
                extra = (f"\n\n（另有 {len(gone)} 条本来就已经不在日程里了："
                         f"{'、'.join(gone)}）") if gone else ""
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        (f"✅ 已按你的确认删掉 {len(ok_titles)} 条：\n{lines}"
                         if ok_titles else "🗑️ 这几条已经都不在日程里了，没有重复删。")
                        + extra
                    ),
                    "trace": [{"step": 1, "phase": "✅ 学生确认（系统批量删除）",
                               "answer": f"删 {len(ok_titles)} 条、已在库里 {len(gone)} 条"}],
                    "options": [],
                    "awaiting_choice": False,
                }

            # **标记完成的确认**：学生回一句"确认"，把上一轮那张【确认完成】真正执行掉。
            # 跟删待办同一个道理——执行权在系统手里，模型连改状态的接口都碰不到。
            # 只有走到这一段，update_todo_status(done) 才会被调用到。
            done_prop = _pending_todo_done(session_id)
            if done_prop:
                take_pending(session_id)
                target = get_todo(done_prop.get("todo_id") or "")
                if target is None:
                    # 那条已经不在了（比如刚被删、或库里压根没有）→ 如实说，不假装标记。
                    append_conversation(session_id, "user", message)
                    append_conversation(session_id, "assistant", "那条待办已经不在了")
                    return {
                        "module": "planner",
                        "session_id": session_id,
                        "answer": (
                            f"✅ 这条待办已经不在日程里了（可能刚才已经删掉了），"
                            f"不用再标记完成。\n刚才那条是：**{done_prop.get('title', '待办')}**｜"
                            f"{done_prop.get('date', '')}（{done_prop.get('weekday', '')}）"
                            f"{done_prop.get('start', '')}-{done_prop.get('end', '')}。"
                        ),
                        "trace": [{"step": 1, "phase": "✅ 待办已不存在", "answer": "不重复标记"}],
                        "options": [],
                        "awaiting_choice": False,
                    }
                # 按天记的那一档（weekly/daily）不能整条翻 status —— 读侧一律问 done_dates，
                # 翻 status 是空操作：真机"背单词完成了"点完确认，明天那条照旧挂着（2026-10-13 查到）。
                # 记在哪一天由 `store.done_date_for` 一处定（今天算出现就记今天，否则记锚点），
                # 这里不再自己猜；一次性待办走同一句也对：set_todo_done_on 内部按档位分流。
                rep = str(target.get("repeat") or "")
                logged = rep in DAY_LOGGED_REPEATS
                # 日子不传 —— 判据只写在 `store.done_date_for` 一处，这里只复述它算出来的那天
                day = done_date_for(target, "") if logged else (done_prop.get("date") or "")
                set_todo_done_on(done_prop.get("todo_id"), "")
                summary = (done_prop.get("summary")
                           or f"{done_prop.get('title', '待办')}｜{done_prop.get('date', '')}"
                              f"{done_prop.get('start', '')}-{done_prop.get('end', '')}")
                append_conversation(session_id, "user", message)
                # 回执是"真的标记过了"的凭据（跟删待办对称），刷新后还看得见。
                append_conversation(session_id, "assistant", f"已标记完成：{summary}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        # 习惯/每周的完成是**记在某一天的**，回执必须说清记的是哪天、
                        # 并且讲明"它还会出现" —— 不然他以为勾这一次就到底了。
                        (f"✅ 已标记为完成：**{done_prop.get('title', '待办')}**｜"
                         f"{day} 这一勾已记下，"
                         + ("明天它还会出现在「代办事项」里（习惯按天记）。"
                            if rep == REPEAT_DAILY else
                            "下周同一个日子它还会出现（每周记一次）。"))
                        if logged else
                        f"✅ 已标记为完成：**{done_prop.get('title', '待办')}**｜"
                        f"{done_prop.get('date', '')}（{done_prop.get('weekday', '')}）"
                        f"{done_prop.get('start', '')}-{done_prop.get('end', '')}。"
                    ),
                    "trace": [{"step": 1, "phase": "✅ 学生确认（系统标记完成）",
                               "answer": summary}],
                    "options": [],
                    "awaiting_choice": False,
                }

            # **批量标记完成的确认**：学生说"都完成了"时挂的那张条，在这里执行。
            db = _pending_todo_done_batch(session_id)
            if db:
                take_pending(session_id)
                items = [x for x in (db.get("items") or []) if isinstance(x, dict)]
                gone, ok_titles = [], []
                for it in items:
                    tid = it.get("todo_id") or ""
                    if tid and get_todo(tid) is not None:
                        set_todo_done_on(tid, "")   # 同上：按天记的那档别翻整条（哪天由 done_date_for 定）
                        ok_titles.append(it.get("title") or "待办")
                    else:
                        gone.append(it.get("title") or "待办")
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已标记完成：{db.get('summary') or '共 %d 条' % len(items)}")
                lines = "\n".join(f"· **{t}**" for t in ok_titles)
                extra = (f"\n\n（另有 {len(gone)} 条已经不在日程里了："
                         f"{'、'.join(gone)}）") if gone else ""
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        (f"✅ 已按你的确认把 {len(ok_titles)} 条标记为完成：\n{lines}"
                         if ok_titles else "✅ 这几条已经都不在日程里了，没有重复标记。")
                        + extra
                    ),
                    "trace": [{"step": 1, "phase": "✅ 学生确认（系统批量标记完成）",
                               "answer": f"标记 {len(ok_titles)} 条"}],
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

        # ★ #61-C Agent 模式：从这里往下全是"替模型抢话"的分支，整段跳掉，直接去找引擎。
        #
        #   为什么不插在 `if not _station_surface:` 那道闸上（把条件改成 `and not _agent_mode`）：
        #   那会把上面**红线三件**一起关掉 ——
        #     2.5 课表导入卡 / 2.6 上报卡（产品承诺的硬信号）
        #     3 确认执行 / 3a-bis 候选卡确认（安全红线：AI 只提案、写库必须学生点头）
        #     3a-c 确认落空兜底（诚实性：暂存没了就得如实说，不能让模型编"我再帮你出一次"）
        #   Agent 模式要关的是"抢话"，不是"红线"。
        #   所以短路点选在这三件之后、3b 之前 —— 上面一行代码都不用搬。
        #
        #   代价（要说清）：Agent 模式下"课表全删了"「把X排到今天19:00」这类话不再走确定性分支，
        #   改由模型调 propose_clear_timetable / propose_todo_tool。写入仍然要学生点确认
        #   —— 安全性不变，只是路径变长（这正是演示想让人看见的）。
        #
        # ★ #62：判据补上"**状态**"这一维 —— 只看 `_agent_mode` 会连六支**回应卡片 / 处理刚上传的
        #   材料**的分支一起关掉（`3c-pre` / `pre2` / `pre3` / `pre4` 与 `_is_material` 那一支）。
        #   那几支的判据是 `held_*` / `_is_material`（"上一轮留下了什么"），跟学生这句怎么说无关，
        #   所以它们不是"抢话"。而它们的注释里逐条记着掉回模型的**实测**后果：
        #     「我说了它当没听见」「已经帮你排好啦，可日程里是空的」「连一张确认条都没有」。
        #   ⇒ 手上挂着卡、或有待处理的材料 = 这句话是在回应它 = 走确定性；
        #      手上干净 = 这是一句新的话 = 交给模型，评委才看得见多轮。
        #
        #   为什么不在这里读 `_holding_card`：它在下面 `:3210` 那一处才算，而 2971→3210 之间
        #   有几个分支会改写暂存（换卡、重挂）—— 把计算上提会让那道闸读到**分支执行前**的状态，
        #   今天看不出差因为那些分支都 return 了，但那属于"靠巧合成立"。⇒ 各算各的，
        #   两处都在自己的正确时刻取数（判据本身仍只有一份：都走 `_any_pending`）。
        #
        #   代价（说清）：挂着一张卡时，学生哪怕说了句完全无关的闲话，这一句也会走快路
        #   （可能被某个分支接走）。可接受 —— 那时候演示的重点已经是那张卡本身。
        if _agent_mode and not _any_pending(session_id) and not _is_material:
            return await _run_agent_loop(
                module_key=module_key, session_id=session_id, message=message,
                prompt=prompt, tools=tools, persona_key=persona_key,
                model_config=model_config, emit=emit,
            )

        # 3b) **确定性清空分支**（人话：学生明说"课表全删了"，直接出弹窗，不劳模型判断）
        #    跟上面"一句确认即落库"是同一个思路：能由代码定死的，就别交给模型。
        #    实测同一句话，模型时而是调 propose_clear_timetable，时而是反问"你确定要全删吗"，
        #    学生被绕回来，链路当场断掉——清空意图本来就明明白白，该由系统接管。
        #    这里只负责"出提案 + 弹窗"，**一个字节都不写库**；真删要等学生点弹窗上的【确认】。
        if wants_clear_timetable(message) and not _rule_veto:
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
            # 报的是 1..6 点又没说上下午 → **在这儿就把这句问清楚**，别掉回模型。
            # 掉回去的代价实测过（2026-10-11）：模型自己"替他挑了下午三点"，
            # 还顺手把 `propose_todo_tool {json}` 当成回话打出来 —— 一次错时间 + 一次内脏外露。
            # 候选卡留着不撤：他补一句「下午三点」照样走上面那条正路。
            _ask = _bare_clock_note(message)
            if _ask:
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", _ask)
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": _ask,
                    "trace": [{"step": 1, "phase": "🕐 这个钟点没报上下午 → 问清楚，不替他猜",
                               "answer": "候选卡照旧挂着"}],
                    "options": [],
                    "awaiting_choice": False,
                }

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
                if mode == "ai" and new_card.get("kind") != "todo_slots":
                    # **核心**：他让系统替他安排，那就真去规划——并把"为什么排在这儿"说清
                    ans = (f"🤖 好，我替你挑了：**{new_card['title']}** 排在 "
                           f"**{new_card['date']}（{new_card['weekday']}）"
                           f"{new_card['start']}-{new_card['end']}**。\n"
                           f"{new_card.get('reason') or ''}\n"
                           f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。")
                    phase = "🤖 学生选了「你帮我挑」→ 系统规划出一段（单条确认条）"
                elif mode == "ai":
                    # 报了"一周三次"又要系统替他挑 → 挑出来的不是一段而是 N 段，
                    # 走的还是候选卡（前 N 段已勾上）。文案不能写"时间你自己定"——那不是他选的。
                    ans = _slots_answer(new_card, lead="🤖 好，我按你说的次数替你挑好了——")
                    phase = "🤖 「你帮我挑」+ 报了频次 → 系统挑出 N 段（候选卡，已预选）"
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

        # 3c-pre5) **语义兜底的收尾**（判定本身已经在 `0) 判定层` 做完了，这里只留回执）。
        #     `_sem` / `_sem_intent` / `_sem_module` / `_rule_veto` 都在那一处算好，
        #     所以下面每条确定性分支都能用上同一次判定——**同一句话只判一次**。
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

        # ★ 「今晚安排点啥」这类**问该做什么**的句子：给一句实在的空档建议，**不建卡、不写库**。
        #   用户 2026-10-14 定的做法（原话：「这种是问事情闲聊的，不入库」；三个走法里他挑了
        #   "不建卡，但给一句实在的建议"）。
        #   判据只有一份（`planner._asking_what_to_do`：句里带疑问代词 **且** 剥掉时间/动作后
        #   剩下的那串本身就是疑问串；在删东西的、在查现有清单的两类当场排除）。
        #   「你帮我安排」这种**没疑问代词**的半句话不在这儿——它照旧走 3c 的追问档，
        #   那句「要安排的是什么事？」有 test_06 十五批钉着。
        #   ⚠️ 位置必须在**列表档之前**（2026-10-14 真机实测出来的顺序问题）：开发机的样本库
        #   早就把「今晚安排点啥」学成了 list_todo，排到列表档后面就永远轮不到这档，
        #   学生看到的是一句「📋 你目前的待办（共 11 条）」——他没在看清单，他在问该做什么。
        #   ⚠️ 也**不走 `_sem_done`**（同下面周期否决那条的理由）：`_sem_done` 里面是
        #   `remember()` 学样本那一步 —— 把一句问话学成模板，下次近似说法照着错。
        # ★ 粘贴材料（#43）：一段通知 / 安排表 / 清单 —— 先认形状，再只从这段里提取，出**多选卡**。
        #   位置在这两档**之前**：`_asking_what_to_do` 那条判据太宽（材料里常带"安排/时间"这种字样，
        #   整段被剥成疑问串就会中），而 #42-A 那道闸管的是 40 字以内的问句 —— 两件事，别串。
        #   写库仍然不在这里：只挂卡，勾完点【加入日程】由 `/api/todos/items_add` 写（红线：模型只出提案）。
        if _is_material:
            # 轨迹要说实话：这一路是**材料分支**接走的，不是语义层那句"问事"。
            meta["read"] = dict(_MATERIAL_READ_STEP)
            _mt_items, _mt_missing = extract_deterministic(message)
            if not _mt_items:
                return _material_empty_answer(session_id, message, _mt_missing)
            _mt_card = _material_card(_mt_items, _mt_missing)
            return _material_answer(session_id, message, _mt_card, _material_lead(_mt_card))

        # ★ 校历类问题（#42-A）：「啥时候放假 / 什么时候开学」既不是"我该做什么"、也不是"列日程"，
        #   它是**知识库**的活 —— 查得到就直答，查不到就老实说没有。
        #   用户 2026-10-19 真机那句被回成"我不替你决定 + 这几段是空着的"，缺的就是这道闸；
        #   判据本体在 `_campus_fact_ask`（校历词表 + 时间问句 + 没有说自己日程的词）。
        #   ⚠️ 必须排在 `_asking_what_to_do` **之前**：那档的判据太宽（`startswith("啥")`），
        #   排它后面就等于让它先抢走（施工单 §2.3 同一句）。
        #   手上挂着待确认的卡时不接管（跟 3i 同一条闸）—— 别把学生的卡弄丢（#33 那一族）。
        # #62：这份名单收进 `_any_pending`（同一份判据，与 Agent 模式短路共用一个函数），
        #        原来这里就地写的 `any(...)` 漏了 `_pending_todo_mode` ⇒ 只挂二选一卡时下面那道
        #        校历闸会把它挤掉（#33 那一族"把学生的卡弄丢"）。取值时刻没动：仍在闸门前一行。
        _holding_card = _any_pending(session_id)
        if _campus_fact_ask(message) and not _holding_card:
            from app.modules.faq import answer_for_student, query_expansion
            if _kb_has_answer(message):
                return _kb_say(module_key, session_id, message,
                               answer="📚 " + answer_for_student(message),
                               note="校园问答：知识库直答",
                               phase="📚 问的是校园信息 → 翻知识库（按台账答，不现编）",
                               step_answer="知识库命中")
            # ★ 原句查不到 → 补一组同义/别名再查一次（#57-A）。
            #   ⚠️ 这一跳挂在**"本来要回『没查到』"那一支里面**，不是检索之前（施工单 §2.1
            #   说的是"进入检索之前先取改写"，这里按实测把它挪后了一格，两条理由都是硬的）：
            #   ① 命中那一路就此与改前**逐字相同**、也快掉 1.5 秒 —— 改写只可能救"漏的"，
            #      不该碰"对的"：补词进了 BM25 就会参与重排，把已经答对的问题选出另一批块，
            #      那是这一单真正的风险面，把它关进"反正已经答不上来"那一支里最省。
            #   ② 日程/闲聊那句根本走不到这儿（`_campus_fact_ask` 在外层就拦掉了），
            #      所以施工单 §三.2 担心的"扩词把 planner 的活抢走"在这条路上结构性地不成立。
            #   失败/没 Key 回空串 → 直接落回原来那句"老实说没查到"，行为与没有这一档时相同。
            _kb_x = await query_expansion(message, model_config)
            if _kb_x and _kb_has_answer(message, extra=_kb_x):
                return _kb_say(module_key, session_id, message,
                               answer="📚 " + answer_for_student(message, extra=_kb_x),
                               note="校园问答：知识库直答（换个说法才查到）",
                               phase="📚 原句查不到 → 补同义/别名再查一次（命中）",
                               step_answer="知识库命中（改写）")
            return _kb_say(module_key, session_id, message, answer=_KB_MISS_LINE,
                           note="校园问答：知识库里没有这一条",
                           phase="📚 问的是校园信息 → 知识库里没有这一条（老实说没查到）",
                           step_answer="知识库未命中")

        if _asking_what_to_do(message):
            answer, _free = what_to_do_suggestion(message)
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", answer)
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": answer,
                "trace": [{"step": 1, "phase": "🤔 这句在问『该做什么』→ 报空档，不替他决定",
                           "answer": "没出卡、没写库，也没把这句学进去"}],
                "options": [],
                "awaiting_choice": False,
            }

        # 3d) **读我已有待办（纯读，不写库、不弹窗）**
        #     学生说「我的代办呢」「看看我的代办」「待办列表」「代办显示不出来」这类话，
        #     没有任何"加/删"意图，就是想看一眼现在有哪些待办。
        #     规矩跟别处一致：能由系统算死的就别交给模型——模型会**凭空编**一堆
        #     "你目前有 3 条待办"（其实一个字都没读库）。这里直接 list_todos() 真查，
        #     查到什么说什么；一条都没有也如实讲。
        #     ⚠️ 位置必须在 3c-ter 之前：「代办显示不出来」既像"看不见 X"、又该走"读列表"，
        #     但学生真正要的是"你把我的待办念给我听"——读出来比一句"确实还没有"更有用。
        if (wants_list_todo(message) and not _rule_veto) or _sem_intent == INTENT_LIST_TODO:
            todos = list_todos()
            if not todos:
                fallback = ("📋 你目前还没有任何待办。\n"
                            "说一句「加个游泳，周四 19:00-20:30」，我就给你出确认条。")
                facts = {"intent": "list_todos", "count": 0, "todos": []}
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
                fallback = "\n".join(lines)
                # 交给模型润色的是**结构化事实**（系统 list_todos() 真查出来的），模型只复述、不改事实
                facts = {
                    "intent": "list_todos", "count": len(todos),
                    "todos": [{"title": t.get("title", ""),
                               "date": t.get("date", ""),
                               "weekday": t.get("weekday") or _weekday_name(t.get("date") or ""),
                               "start": t.get("start", ""),
                               "end": t.get("end", "")} for t in todos_sorted],
                }
            # ★ 架构升级 · 试点 1：系统查出列表 → 模型说人话（润色）。
            #   失败 / 超时 / 没配 key → 原样退回上面的模板话术（离线兜底，现象与迁移前一致）。
            #   模型只复述、不决定事实；列表数据始终来自 list_todos()，与迁移前逐字一致。
            from app.agent.polish import polish_reply
            answer = await polish_reply(facts, fallback, override=model_config)
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

        # 3d-ret) **改期（已经排进日程的待办想换个时间）**——"把周四那个健身挪到周五"。
        #     跟删待办同一套规矩：只出提案、学生点头才动；而且是"删旧 + 加新"，
        #     不是就地改字段，这样一旦新时间不合适还能追溯旧的那条。
        #     规则认得（含"改成/挪到"等词）或语义层兜回来的 retime_todo 都走这儿；
        #     前提：手上没有挂着的待确认卡（那种是 3c-pre 那一档，已在前面处理过了）。
        if ((wants_retime_todo(message) and not _rule_veto)
                or _sem_intent == INTENT_RETIME) and not held_add:
            from app.modules.planner import build_retime_existing_proposal, _weekday_name as _wd2
            _rt = build_retime_existing_proposal(
                message, _sem if _sem_intent == INTENT_RETIME else None)
            _rt_status = _rt.get("status")
            if _rt_status == "ok":
                _rt_prop = _rt["proposal"]
                save_pending(session_id, [_rt_prop])
                # 提案纠正过名字（如样本库把"瑜伽"误记为"健身"）时，把纠正后的真名字
                # 写回语义层字段——否则 remember() 会把错的"健身"学进样本库，越错越深。
                if _sem is not None and _rt_prop.get("title"):
                    _sem = dict(_sem)
                    _sem["title"] = _rt_prop["title"]
                _sem_done(_rt_prop)
                return _offer_response(
                    session_id, _rt_prop, message,
                    (f"📝 好，把 **{_rt_prop['title']}** 从 "
                     f"{_rt.get('old_label', '原时间')} 改到 "
                     f"**{_rt_prop['date']}（{_rt_prop['weekday']}）"
                     f"{_rt_prop['start']}-{_rt_prop['end']}**——\n"
                     # 改的是习惯/每周那条就得说一句"还是每天/每周"：
                     # 这一条是删旧加新，他不看到这句就会以为长期那半没了。
                     + (f"它**仍然是每天**都要做的事（只改了钟点，勾完成还是只算当天）。\n"
                        if _rt_prop.get("repeat") == REPEAT_DAILY else
                        (f"它**仍然是每周{_rt_prop['weekday']}**都要做的事（只改了钟点）。\n"
                         if _rt_prop.get("repeat") == REPEAT_WEEKLY else ""))
                     + f"下面点【确认加入】我就改好（旧的那段会挪走）。"
                     f"回一句「确认」也一样。"),
                    "🕘 改期提案（系统判定）")
            if _rt_status == "many":
                _rt_lines = "\n".join(
                    f"· **{x.get('title', '')}**｜{x.get('date', '')}"
                    f"（{_wd2(x.get('date', ''))}）"
                    f"{x.get('start', '')}-{x.get('end', '')}"
                    for x in _rt.get("todos", [])[:6])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", "改期缺细节")
                return {
                    "module": "planner", "session_id": session_id,
                    "answer": ("🕘 你想改的是哪一条？我找到好几条同名的：\n\n"
                               f"{_rt_lines}\n\n回我一个（比如「周四那条」），我帮你改。"),
                    "trace": [{"step": 1, "phase": "🕘 改期遇到多条同名（不猜）",
                               "answer": "等学生点名"}],
                    "options": [], "awaiting_choice": False,
                }
            if _rt_status == "empty":
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", "没找到要改的待办")
                return {
                    "module": "planner", "session_id": session_id,
                    "answer": "📋 没找到那条待办——你先加进日程，或者告诉我它叫什么、在哪天？",
                    "trace": [{"step": 1, "phase": "🕘 改期未找到目标", "answer": "请学生确认名字"}],
                    "options": [], "awaiting_choice": False,
                }
            # unclear：新时间认不出来
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "改期时间没说清")
            return {
                "module": "planner", "session_id": session_id,
                "answer": "🕘 新时间我没听清——说清楚要改到哪天几点？比如「改到周五 15:00-16:00」。",
                "trace": [{"step": 1, "phase": "🕘 改期新时间缺失", "answer": "请学生补时间"}],
                "options": [], "awaiting_choice": False,
            }

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

        # ★ 让路：这句话问的其实是**校园知识库里有的东西**（"校车在哪坐"）→ 交给知识库，
        #   别拿"查日程"去兜。
        #   实测那一幕：学生问「校车在哪坐」，`_MISSING_RE` 里的「在哪」把它当成报障，
        #   抠出一个根本不存在的待办名"校车坐"，回「查了日程，确实还没有「校车坐」」——
        #   他问的是校车在哪发车、几点发车，系统却在自己的日程里翻。
        # ⚠️ 让路的判据（两条同时成立才让，宁可漏也不可把真报障让出去）：
        #   ① 话里没有"待办/日程/周表/课表/清单"这类**说自己日程**的词
        #      （"我的校车待办没显示"仍然是报障，得由这条确定性分支接）；
        #      —— 这张词表全站只有 `_SELF_SCHEDULE_WORDS` 一处（以前这里和 3i 各抄一份）。
        #   ② 知识库里**真的有答案**（查不到就让这条分支照旧查日程，别把话甩冷宫）。
        # 注意：这句**只负责"让路"**。"两路都查空也别回'你的日程里没有'"那半件事不在这儿做 ——
        # 那要等这条分支**真的核问过日程与周表、什么都没找到**之后才成立（见下面第 ④ 档），
        # 在这儿抢答会把「高等数学在哪上」这种"问自己的课"一起吞掉（#41 全量当场抓出来的）。
        _missing_now = missing_thing_of(message)
        missing = _missing_now
        if (_missing_now is not None and not any(w in message for w in _SELF_SCHEDULE_WORDS)
                and _kb_has_answer(message)):
            missing = None
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

            # ④ 都没有 —— 先分一件事（#41-B）：这句**本来就不是在说自己的日程**，
            #    而是拿「在哪 / 几点 / 怎么」在问校园信息，只是知识库里恰好没有。
            #    那种场合回"你的日程里没有"是走错了听众（实测：问「外星人驻扎在哪个楼」，
            #    屏幕回「查了日程，确实还没有『外星人驻扎个楼』」），所以就地老实说"没查到"。
            #    ⚠️ 两条位置讲究，都是踩过才知道的：
            #    ① 放在**这里**（日程与周表都核问过、确实没有）而不是放在让路那道 ——
            #       抢在核实前面会把「怎么没有周一的高数」这种"问自己的课"一起吞掉
            #       （全量当场抓出来的：那条课在周表里，正当问题被回成"知识库里没查到"）。
            #    ② 不再限定 `module_key == "planner"`：整段确定性分支本来就圈在 station 之外
            #       （#34 那道端级闸门），问答栏（faq）走进这条分支时同样不该听到日程话术 ——
            #       它没配密钥时该看到的降级提示，只在"知识库**有**答案"那一档才会走到模型那一路，
            #       而那一档由上面的让路①接走、根本到不了这里。
            if asks_campus_fact(message):
                return _kb_say(module_key, session_id, message, answer=_KB_MISS_LINE,
                               note="校园问答：知识库里没有这一条",
                               phase="📚 问的是校园信息 → 知识库里没有这一条（老实说没查到）",
                               step_answer="知识库未命中")

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

        # ★ 「每月 / 每个周末 / 下个月」这一类周期说法：**任何一档判成加待办都不许出卡**
        #   （「每天」2026-10-13 起不在这儿了 —— 它现在表达得了：一条 daily 习惯，只进代办不进日历）
        #   （2026-10-11 补，判据在 planner.unsupported_period）。
        #   病根不是规则层读错了 —— 规则层从来不接这一类，是**样本库/语义层能绕过它单独接单**：
        #   用户原话「在下个月的每个周末三点开始大约三个小时」被样本库判成 add_todo、
        #   事名学成「活动」，最后排出一条 15:00-18:00 的孤零零待办。
        #   他要的是"以后每个周末"，拿到的却是"某一天一次"，而且这条还**学进样本库**了，
        #   以后每次近似说法都照着错。所以这里做两件事：
        #   ① 一处不分层的否决（规则/样本库/语义层哪档判的都拦）；
        #   ② 这条分支**不走 `_sem_done`**（它就是 `remember()` 学样本的那一步），
        #      否掉的句子绝不许再学进去 —— 否则同一个错会自己繁殖。
        #   只做这两件，不去"听懂每月/每个周末"：一条日程只能落**某一天**、**每周固定某天**或**每天**，
        #   硬扩表达等于替学生造他没说要的几十条安排（判据同 `weekly_repeat_day` 的取舍）。
        if _period_block:
            return _period_reject_response(session_id, message, _period)

        # ⚠️ 末尾的 `and not wants_add_course(message)` 是「加课优先于加待办」的硬闸门：
        # 学生说"周一早上加一节高数课"时，语义层（样本库/大模型）很可能把它判成 add_todo，
        # 于是这句话会先进到上面的加待办分支、被当成待办塞进日程——正是报障那一幕。
        # 加课没有独立的语义意图（白名单里没有 add_course），纯靠 wants_add_course 这条
        # 确定性分支接管；所以只要它认得，加待办这边就必须让路，绝不抢。
        #
        # 开头的 `not _rule_veto`：规则想"加一条"，可语义层判它**不是**要办事
        # （多半在问事：在校园问答栏问「我要去哪里取快递」），就以语义层为准——
        # 否则那张卡会写着「哪取快递」挂到学生眼前，而他只是想知道去哪儿取。
        # 末尾的 `and not _physio`：**纯生理状态感叹（"我饿了""好累啊"）任何情况下
        #   都不许进这条分支** —— 哪怕语义层硬判了 add_todo。这是他的一条安全绳，
        #   判据见 `intent.physio_state_only`（生理词 + 无行动词 + 没报钟点 + 短句）。
        if ((not _rule_veto
                and (wants_add_todo(message) or (asking_add and is_add_todo_answer(message))))
                or _sem_intent == INTENT_ADD_TODO) \
                and not _physio and not wants_add_course(message):
            # 追问后的补充回答（学生先说"帮我安排周二的游泳"，再补"下午两点到三点"）：
            # 补充那句里没有标题、也没有日期，单独解析会把标题弄丢——
            # 把上一句原话拼回来一起算。识别标记就是下面追问分支写进会话历史的那句"加待办缺细节"。
            # 语义层那一路（`_sem_intent`）：它把口语翻成了"加个吃火锅，76分钟，工作日"
            # 这种**解析器认得的规范话**（`to_canonical`），所以照旧喂给同一套老解析，
            # 不用再抄一份"从 JSON 造卡片"的逻辑。
            # 语义层那一路：把口语翻成"加个吃火锅，76分钟，工作日"这种**解析器认得的规范话**
            # （`to_canonical`），照旧喂给同一套老解析——不另抄一份"从 JSON 造卡片"的逻辑。
            # ⚠️ 但**规则明明认得这句话时不能这么替**：规范话是拿语义字段拼的，字段里没有的
            #    信息会整段丢掉——"帮我安排周二的游泳"里的"周二"就不在语义字段里，一替下去
            #    候选时段会摊到一整周（实测踩过）。所以只在规则读不出来时才用规范话兜。
            if _sem_intent == INTENT_ADD_TODO and _sem and not wants_add_todo(message):
                blob = carry_period(carry_freq(to_canonical(message, _sem), message), message)
            else:
                blob = (prev_user + "，" + message) if (asking_add and prev_user) else message
            proposal = None
            if asking_add and prev_user:
                proposal = parse_add_todo(prev_user + "，" + message)
            if proposal is None:
                proposal = parse_add_todo(blob)
            if proposal is None and _sem_intent == INTENT_ADD_TODO and _sem:
                # ★ 2026-09-30 补：语义层/样本库说了"这是件要办的事"、这句话里也**报了钟点**，
                #   可就是**没说哪天** —— 学生报障那句「中文12点的车」正是这一档。
                #   `parse_add_todo` 的契约是"日期和起始时刻缺一个就返回 None"，于是
                #   12 点被丢掉、改去摊一堆候选时段；而摊出来的候选里**根本没有他说的那个点**，
                #   等于让他把自己刚说过的钟点重挑一遍。
                #   就着他报的那个点出条、一条就够（本文件下面那句注释早就这么写了，
                #   只是"没说哪天"这一档一直没人接）。见 planner.span_only_proposal：
                #   兜底日期用今天，今天这个点已经过了就顺延到明天。
                # ⚠️ **只在语义层这一路兜**：规则层那句里的时间未必是这件事的时间
                #   （「三点下课，帮我加个买水果」会被读成 03:00），交不出日期时摊候选更安全。
                proposal = span_only_proposal(blob, (_sem.get("title") or "").strip())
            if proposal is not None:
                # 学生**自己报了准点** → 就着他给的这个点出条，一条就够。
                # 不必再摊候选：他已经说清要哪个点了，再摊三个等于让他重挑一遍。
                # ★ 架构升级 · 试点 2：确认卡字段 / 暂存 / 写库路径**一个字节都不改**，
                #   只把下面那段模板话术交给模型润色（超时/失败/没 key → 原样退回模板）。
                #   模型只复述事实，不许新增、不许说"已办好"（承诺词守卫在 polish 里）。
                _wd_bare = (proposal.get("weekday") or "").replace("周", "") or "那天"
                _card_fallback = (
                    f"📝 要不要把 **{proposal['title']}** 排进日程？"
                    f"{proposal['date']}（{proposal['weekday']}）"
                    f"{proposal['start']}-{proposal['end']}。\n"
                    # 学生说了"每个周六"就得在回话里把这件事确认一遍 —— 只报一个日子，
                    # 他会以为就排这一天，下周那天不再出现（2026-10-11 真机就是这么掉的）。
                    + (f"这是**每周{_wd_bare}**都要做的事，我只建一条："
                       f"以后每个周{_wd_bare}它自己会出现在日程里。\n"
                       if proposal.get("repeat") == "weekly" else "")
                    # 「每天都要背单词 + 报了准点」同理：不说清"每天"和"不进日程"，
                    # 他会以为就排这一天，第二天在日程里找不到，判成系统没记上。
                    + (f"这是**每天**都要做的事，我只建一条："
                       f"以后每天它都会出现在你的「代办事项」里（习惯不占日程格子）。\n"
                       if proposal.get("repeat") == "daily" else "")
                    + f"下面点一下【确认加入】我就写进去，回一句「确认」也一样。")
                # ★ 卡片类出口**不润色**（2026-10-14 第一刀 B，用户点的"同类出口不一致"）。
                #   上面那段 `_card_fallback` 就是系统按事实拼好的话 —— 连「这是每周六都要做的事，
                #   我只建一条」这种最容易丢的说明都在里面，交给模型复述它只会把回复拖慢 3~4 秒
                #   （实测这一档 3223ms），换不到任何信息量。
                #   长列表出口（列待办）仍走润色，全仓只剩那一处润色调用。
                #   ⚠️ 摘的是**位置**，不是守卫：承诺词/JSON 守卫在 polish 内部（另一处仍生效）、
                #   引擎 claim 守卫与出口内脏保险都不许跟着删（test_46 D 批钉着四条）。
                _card_answer = _card_fallback
                return _sem_done(_offer_response(
                    session_id, proposal, message, _card_answer,
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
            # 算不出提案只有两种可能：缺星期，或说了星期却连"上午/下午/晚上"都没给。
            # 这种情况下**不能**把话交给模型——它多半回一句"已经加上了"，学生再去周表里找。
            # 缺哪样就追哪样，别一股脑只问星期（说了星期、只差时间的会被问蒙）。
            clear_pending(session_id)
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "加课缺细节")
            if _pick_day(message) is None:
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
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    "星期我记下了，这门课想排在**几点**？说个钟点（「10:00 到 11:40」），"
                    "或者说「上午/下午/晚上」也行，我算出新课表给你确认，你点头我才写进周表。"
                ),
                "trace": [{"step": 1, "phase": "🤔 加课缺时间（系统追问）",
                           "answer": "学生说了星期却没给时间，先问清楚再出提案"}],
                "options": [],
                "awaiting_choice": False,
            }

        # 3h) **确定性标记完成分支**（人话：学生说"把游泳标记完成""勾掉交电费"——
        #     系统接管：先列清楚是哪一条，出确认条，学生点头才算数，**模型碰不到改状态的接口**）
        #
        # 为什么必须系统接管：老版本模型手里有一个真能改状态的 update_todo_status 工具，
        # 规矩只写在提示层（"必须先确认"）。提示是软的，模型口语一变就可能绕过去——
        # 学生没点头就把待办置成已完成，他还在以为没办完，复盘时才发现已经"完成"了。
        # 所以两件事一起做（跟删待办一个模子）：
        #   ① 写工具从工具箱撤掉（planner 里只留只读的 propose_todo_done，出不了任何状态）；
        #   ② 判定和列清单收归系统，三种情况各有各的走法（规格里的"3 确定性分支"）：
        #      · 点名了唯一一条 → 出确认条（原样念一遍标题+日期+时段），等学生点头
        #      · 命中好几条 / 没说清是哪条 → **列清单问是哪一条，列完就停**，绝不猜
        #      · 一条都没命中 → 如实说，绝不编一条出来标记
        # ⚠️ 必须排在删待办（3g）前面：学生说"把周二那条**待办**标记完成"也带"周二"，
        #     让删待办先接会误判成"要删"，跑偏。标记完成不是删除，顺序摆正。
        # ⚠️ "学生在回答是哪一条吗" 也只看**紧挨着的上一句**（同删待办的串味教训）。
        _last_bot = next((m.get("content") or "" for m in reversed(get_conversation(session_id))
                          if m.get("role") == "assistant"), "").strip()
        asking_done = _last_bot == "标记完成缺细节"
        done_intent = ((_done_claim and not _rule_veto)
                       or _sem_intent == INTENT_DONE)
        # ★★ 先算"这句是不是在回答**删待办**那份候选清单"，再决定要不要让标记完成接。
        #
        # 真机踩到的坑（学生回「游泳」，系统却弹出一张"标记完成"卡）：上一句是删待办的
        # 候选清单（"1. 游泳 2. 游泳"），学生回名字是在**点名**；可**语义层**把「游泳」
        # 单独一句判成了 done_todo（光看这两个字，它确实更像"办完了"）。而标记完成分支
        # 排在上面（3h 在 3g 前），于是这句被它抢走 —— 判定层再怎么按上下文解析都没机会跑。
        #
        # 判据分两层，只让**弱信号**（语义层猜的 done）让位：
        #   · 规则层明说这是标记完成（"把游泳标记完成/勾掉…"，且没被 _rule_veto 否掉）
        #     → 硬信号，还是标记完成接（别让一个"都"字/一份清单把它盖掉）；
        #   · 只有语义层判 done（主语就俩字名字）→ 弱信号，让给删待办。
        _rm_held_now = _pending_remove_candidates(session_id)
        _rm_asking = _last_bot == "删待办缺细节"
        _rm_answers_held = bool(_rm_held_now) and (
            bool(_pick_todo_indexes(message, len(_rm_held_now)))
            or (_pending_todo_remove(session_id) is not None and _wants_rechoose(message)))
        _rm_reply_pending = _rm_asking or _rm_answers_held
        _done_hard = _done_claim and not _rule_veto
        if (done_intent or asking_done) and not (_rm_reply_pending and not _done_hard):
            # 学生是不是在回答"是哪一条"（回一句"第二条"/"23"/"游泳那条"）。
            # 同删待办：不能再 and not done_intent——语义层会把「第二条」判成某类办事意图，
            # 关掉 followup 就会退化成"没找到"。resolve_todo_done_reply 自带名字兜底。
            followup = asking_done
            held = _pending_todo_done_candidates(session_id)
            if followup:
                res = resolve_todo_done_reply(message, held)
            else:
                res = resolve_todo_done(message)
            status = res.get("status")

            if status == "ok":
                proposal = res["proposal"]
                save_pending(session_id, [proposal])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成标记完成提案：{proposal['summary']}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"✅ 要标记为完成的是这一条：**{proposal['title']}**｜{proposal['date']}"
                        f"（{proposal['weekday']}）{proposal['start']}-{proposal['end']}。\n"
                        "确认条就在下面这条消息里，点【确认完成】我才改状态；"
                        "点【取消】这条待办原封不动。"
                        "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                    ),
                    "trace": [{"step": 1, "phase": "✅ 生成标记完成提案（系统判定）",
                               "answer": proposal["summary"]}],
                    "options": [proposal],
                    "awaiting_choice": True,
                }

            # 学生对着刚列出的清单说"都完成了 / 全标记完成" —— 同删待办：
            # 接着上一句指代"你列的那几条我都要办"，一次点头、一次办完。
            if status == "all" and res.get("todos"):
                todos = res["todos"]
                proposal = todo_done_batch_proposal(todos)
                save_pending(session_id, [proposal])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成标记完成提案：{proposal['summary']}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"✅ 好，你说的是**上面那 {proposal['count']} 条全都要标记完成**。"
                        "我把它们一条条列在下面的确认条上了——"
                        "点【确认完成全部】我才改状态；点【取消】原封不动。"
                        "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                    ),
                    "trace": [{"step": 1,
                               "phase": "✅ 学生说「都」→ 生成批量标记完成提案（系统判定）",
                               "answer": proposal["summary"]}],
                    "options": [proposal],
                    "awaiting_choice": True,
                }

            # 命中好几条：**列清单，问是下面哪一条，列完就停**。
            # 这一支就是规格第 2 条——"不许猜"。清单同时记进暂存，
            # 学生回"第二条"时系统接得住（见上面的 resolve_todo_done_reply）。
            if status in ("many", "unclear") and res.get("todos"):
                todos = res["todos"]
                save_pending(session_id, [{"kind": "todo_done_candidates",
                                           "todos": todos, "summary": "待标记候选"}])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant", "标记完成缺细节")
                head = ("你说的这条对上了好几条待办，我不敢替你挑 ✅"
                        if status == "many" else "没太看明白你指哪一条 ✅")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"{head}\n\n{render_todo_done_list(todos)}\n\n"
                        "是下面哪一条？回我一个名字（或序号，比如「第二条」），"
                        "我再把确认条挂出来——**你点头之前我一条都不会动。**"
                    ),
                    "trace": [{"step": 1, "phase": "✅ 标记完成命中多条（列清单，不猜）",
                               "answer": f"候选 {len(todos)} 条，等学生点名"}],
                    "options": [],
                    "awaiting_choice": False,
                }

            # 一条都没命中：如实说。绝不从"没找到"里编一条出来标记。
            clear_pending(session_id)
            day = res.get("day")
            where = f"{day}（{_weekday_of(day)}）" if day else ""
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", "标记完成：没找到符合条件的")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (
                    f"✅ 我在{where or '日程里'}没找到叫这个的待办，什么都没动。\n"
                    "你可以：① 说得再具体点（哪一天 + 叫什么）；"
                    "② 直接问我「那天排了什么」，我列给你看。"
                ),
                "trace": [{"step": 1, "phase": "✅ 标记完成没找到（如实说，不猜）",
                           "answer": "没有匹配的待办，什么都不改"}],
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
        # ⚠️ `_last_bot` / `asking_rm_todo` / `held_now` / `answers_held` 已在上面
        #    （3h 标记完成分支之前）算好：那边要先知道"这句是不是在回答删候选清单"，
        #    才能决定让不让标记完成接。这里直接复用，别再算第二遍——两处各算一遍，
        #    将来改一处就会悄悄不一致（同一个判据两个说法是这类 bug 的老家）。
        asking_rm_todo = _rm_asking
        rm_intent = (((wants_remove_todo(message) or wants_remove_todo_loose(message))
                      and not _rule_veto)
                     or _sem_intent == INTENT_REMOVE_TODO)
        # ★ 手里还攥着上一句那份候选清单时，"报个序号 / 说声换一条"就是在回答我们——
        #   跟上一句是不是那句追问**无关**：清单指的是"我们刚列过"，而裸序号离开清单
        #   根本没有别的主人。少了这一条，系统替他挑过一条之后他说"不对，换一条/第二条"
        #   就没人接得住（卡上的出口是断的，他会觉得"它自己定了就不听我"）。
        # ⚠️ 收紧两处，免得把别的意图串味进来：
        #   · 报序号 —— 只认**整句基本就是个序号**（"2"/"第二条"/"23"），由 _pick_todo_indexes 把住；
        #   · 说"不对" —— 只在**刚挂过一张删待办卡**时才算（说明他是在否掉那张卡）。
        held_now = _rm_held_now
        answers_held = _rm_answers_held
        # ★ 软取消优先作废**还没点头的加待办提案**，别去日程里翻一条出来删。
        #   真机那一幕（用户截图）：「明天去吃自助」出了候选时段卡《吃自助》，
        #   他紧接着说「算了不吃了」—— 库里**压根还没有这条**，可执行层把这句听成
        #   "删一条待办"，于是端出整张日程问他"是下面哪一条"。他要的是把这张卡撤掉。
        #   这一支只清暂存：**一个字节都不写库，也不删库**（判定/执行分离照旧）。
        # ⚠️ 只认 `_ADD_PROPOSAL_KINDS` 那几种 = 天然避开了"他正在回答删待办那份清单"：
        #   暂存每个会话**只有一个位置**（`save_pending` 整份覆盖），挂着 `todo_remove_candidates`
        #   的时候这里拿到的就是 None，不需要再叠一层 `asking_rm_todo / answers_held` 的判断
        #   （早先按施工单加了那道，实测是**死条件** —— 永远为真，白占一行还让人以为它在把关）。
        _active_add = _active_add_proposal(session_id)
        if _active_add is not None and _cancels_active_proposal(message, _active_add):
            _back_out = (_active_add.get("title") or "").strip() or "这件事"
            _back_out_summary = _active_add.get("summary") or _back_out
            clear_pending(session_id)
            append_conversation(session_id, "user", message)
            append_conversation(session_id, "assistant", f"已取消待办提案：{_back_out_summary}")
            return {
                "module": "planner",
                "session_id": session_id,
                "answer": (f"好，**{_back_out}** 先不加了 —— 那张卡我撤了，日程一个字都没动。\n"
                           "改主意了再说一声就行。"),
                "trace": [{"step": 1,
                           "phase": "🗑️ 学生反悔：作废还没点头的待办提案（不写库也不删库）",
                           "answer": _back_out_summary}],
                "options": [],
                "awaiting_choice": False,
            }
        if rm_intent or asking_rm_todo or answers_held:
            # 学生是不是在回答"是哪一条"（回一句"第二条"/"23"/"游泳那条"）。
            # ⚠️ 只看"上一句是不是我那句追问"，**不能再 and not rm_intent**：
            #    真机上语义层会把「第二条」判成 remove_todo（实测 confidence 0.85），
            #    一旦因此把 followup 关掉，这句就被当成一次全新的删除指令 → 报"没找到"
            #    （学生截图吐槽"记忆有问题"就是这么来的）。resolve_todo_remove_reply
            #    内部已经"先按序号、再按名字"兜底，直接走它最稳。
            #
            # ⚠️ 再加一种：学生手里**有清单**、又**明确报了个序号**（"2"/"第二条"/"23"）、
            #    或在刚挂过删待办卡时说"不对/换一条"——这两种都是在回答我们，
            #    跟上一句是不是那句追问无关（见上面 answers_held 的注释）。
            held = held_now if held_now else _pending_remove_candidates(session_id)
            followup = asking_rm_todo or answers_held
            # 「不去了删了吧」这种**没点名**的删除 → 十有八九指的就是刚写入的那条。
            # 系统手里有这份账（自己写的写库回执），把名字补上再走同一条解析路，
            # 出卡、确认、落库全部沿用原有代码——不另抄一份"从回执造删卡"的逻辑。
            probe = message
            _blind_recent = None
            if not followup and _is_blind_delete(message):
                _blind_recent = _recent_written_todos(session_id)
                _still = [t for t, ok in _blind_recent if ok]
                _gone = [t for t, ok in _blind_recent if not ok]
                # ★ 回执里记着的**就那一条**、而它现在已经不在库里（他自己删过）→
                #   先如实说"已经不在"，**不要**跑去解析、更不要端出整张日程。
                #   放在这里（解析之前）是因为：他压根没点名，解析只会拿到"全部待办"
                #   然后回一句"对上了好几条，我不敢替你挑"——学生刚删掉的那条，
                #   在他听来就是系统装作不知道（这是在**有账可查**的前提下说的，
                #   不是猜：回执是系统自己写的）。
                if _gone and not _still and not wants_remove_course(message):
                    clear_pending(session_id)
                    append_conversation(session_id, "user", message)
                    append_conversation(session_id, "assistant", "删待办：已经不在了")
                    return {
                        "module": "planner",
                        "session_id": session_id,
                        "answer": (f"🗑️ 「{_gone[0]}」已经不在日程里了——"
                                   "可能刚才已经删掉（或在月表里手动删过）。"
                                   "现在日程里一条都没少，不用再操作。"),
                        "trace": [{"step": 1,
                                   "phase": "🗑️ 没点名的删除 → 那条已经不在库里，如实说",
                                   "answer": f"{_gone[0]} 已不在"}],
                        "options": [],
                        "awaiting_choice": False,
                    }
                if len(_still) == 1:
                    probe = f"删除{_still[0]}"
            if followup:
                res = resolve_todo_remove_reply(message, held)
            else:
                res = resolve_todo_remove(probe)
            status = res.get("status")

            if status == "ok":
                proposal = res["proposal"]
                # 手上有清单（不管是**系统替他挑的**那条，还是他**自己报序号**挑的那条）
                # 就把这份清单一并留在暂存里 —— 他说"换一条"/"第二条"时还有人接。
                # 只留提案的话，"不对"两个字就会掉回大模型瞎猜（**出口就断了**）。
                # （早先只在 picked_by 时留。可"换一条"这个出口跟他怎么挑的没关系：
                #   自己报了序号照样可能挑错，把他困在卡上只能取消，是同一类毛病。）
                pending_out = [proposal]
                if held:
                    pending_out = [proposal, {"kind": "todo_remove_candidates",
                                              "todos": list(held), "summary": "待删候选"}]
                save_pending(session_id, pending_out)
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成删待办提案：{proposal['summary']}")
                # 挑的依据必须让学生**在卡外面也看得见**：不写出来，他会产生虚假确定感
                # ——不看卡就点确认，那跟"直接删"没区别。
                why = ""
                if proposal.get("picked_by"):
                    why = f"\n（{proposal.get('note', '')}）"
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"🗑️ 要删的是这一条：**{proposal['title']}**｜{proposal['date']}"
                        f"（{proposal['weekday']}）{proposal['start']}-{proposal['end']}。"
                        + why + "\n"
                        "确认条就在下面这条消息里，点【确认删除】我才删；"
                        "点【取消】日程原封不动。"
                        "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                    ),
                    "trace": [{"step": 1, "phase": "🗑️ 生成删待办提案（系统判定）",
                               "answer": proposal["summary"]}],
                    "options": [proposal],
                    "awaiting_choice": True,
                }

            # 学生说「每个删一个 / 每组删一个 / 重复的删掉一个」——他是要**去重**：
            # 每组重复的只留一条，不是把整张清单清空。这两句话字面上都带量词，
            # 混在一起就会把"去重"听成"全删"（学生截图批注"脑子有问题"）。
            # 规则在回话里说清楚（留最早写入的那条），卡上把要删的逐条列出来 —— 他点头才动。
            if status == "dedup" and res.get("todos"):
                todos = res["todos"]
                proposal = todo_remove_batch_proposal(todos)
                save_pending(session_id, [proposal])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成删待办提案：{proposal['summary']}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"🗑️ 明白，你是要**去重**：同一组重复的只留一条，多出来的这 "
                        f"{proposal['count']} 条删掉。\n"
                        "（每组留的是**先写进去的那条**；要留另一条的话，"
                        "取消之后直接说序号也行。）\n"
                        "我把要删的一条条列在下面的确认条上了——"
                        "点【确认删除全部】我才删；点【取消】日程原封不动。"
                        "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                    ),
                    "trace": [{"step": 1,
                               "phase": "🗑️ 学生说「每个删一个」→ 生成去重删除提案（系统判定）",
                               "answer": proposal["summary"]}],
                    "options": [proposal],
                    "awaiting_choice": True,
                }

            # 学生对着刚列出的清单说"都删了 / 全删掉 / 一起删了吧" —— 这是在**接着上一句
            # 指代**"你列的那几条我全都要删"，不是"没说名字"。所以该**一次点头、一次删完**：
            # 挂一张批量确认条，上面把每一条原样念清楚。
            # 旧写法把这句话当"没点名"，端出**整张日程**（学生截图：列了 2 条，
            # 他回「都删了」，屏幕却冒出 11 条 —— "上下文连不上"就是这一处）。
            # ⚠️ 红线没破：这张条只是**提案**，他点【确认删除全部】之前一条都不会删。
            if status == "all" and res.get("todos"):
                todos = res["todos"]
                proposal = todo_remove_batch_proposal(todos)
                save_pending(session_id, [proposal])
                append_conversation(session_id, "user", message)
                append_conversation(session_id, "assistant",
                                    f"已生成删待办提案：{proposal['summary']}")
                return {
                    "module": "planner",
                    "session_id": session_id,
                    "answer": (
                        f"🗑️ 好，你说的是**上面那 {proposal['count']} 条全都要删**。"
                        "我把它们一条条列在下面的确认条上了——"
                        "点【确认删除全部】我才删；点【取消】日程原封不动。"
                        "（也可以直接在聊天框回一句「确认」，一样会执行。）"
                    ),
                    "trace": [{"step": 1,
                               "phase": "🗑️ 学生说「都」→ 生成批量删除提案（系统判定）",
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
                # 没点名的那几种情形，都比"没找到"有用（截图实测：学生被这句推去
                # 月表手动删——可他明明刚在对话里加过，账就在回执里）：
                if _blind_recent is not None:
                    _still = [t for t, ok in _blind_recent if ok]
                    _gone = [t for t, ok in _blind_recent if not ok]
                    if len(_still) > 1:
                        # 最近写入的好几条都还在 → 列出来让他点名（绝不替他猜哪条）
                        todos = [x for x in list_todos() if x.get("title") in _still]
                        save_pending(session_id, [{"kind": "todo_remove_candidates",
                                                   "todos": todos,
                                                   "summary": "待删候选"}])
                        append_conversation(session_id, "user", message)
                        append_conversation(session_id, "assistant", "删待办缺细节")
                        return {
                            "module": "planner",
                            "session_id": session_id,
                            "answer": ("你最近刚排了这几条，要删的是哪一条？\n\n"
                                       f"{render_todo_remove_list(todos)}\n\n"
                                       "回我名字或序号就行——**你点头之前我一条都不会删。**"),
                            "trace": [{"step": 1,
                                       "phase": "🗑️ 没点名的删除 → 列出最近写入的几条让他点名",
                                       "answer": f"候选 {len(todos)} 条"}],
                            "options": [],
                            "awaiting_choice": False,
                        }
                    # （「已经不在了」那一支已挪到解析**之前**：他没点名时，解析只会
                    #   拿回"全部待办"，等走到这儿再说"已经不在"就晚了 —— 见上面。）
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

        # 3i) **知识库直答**（人话：问的是校园信息，就别让大模型现编）
        #     真机实测出来的漏子：在**日程栏**问「校车在哪里坐」，所有确定性分支都不认
        #     （它既不是加待办也不是删待办），最后掉给大模型——大模型手里没知识库，
        #     回了一句"我这边查不到，你去看学校公众号"，可知识库里**明明躺着校车时刻表**。
        #     所以位置很有讲究：放在**所有确定性分支之后、交给模型之前**——
        #     该接的都接过了才轮到它，绝不会从待办操作手里抢话。
        #     四道闸门缺一不可（宁可漏给模型，也不许在学生的待办操作上乱插嘴）：
        #       ① 手上没有待确认的卡（有卡时他多半在跟卡对话，别把卡弄丢）；
        #       ② 这句话不带"待办/日程/课表"这类词（"我的校车待办显示不出来"是报障）；
        #       ③ 没有任何改数据的意图（加/删/改待办、加/删课、清空课表）；
        #       ④ 它**像问句**，且知识库**真的**答得出来——答不出就老实交给模型
        #          （#41-B 之后，"像校园事实但两路都空"那种在上面报障分支里就地老实说了，不绕到这儿）。
        #          #57-A 起这一条分**两段**：原句查不到时先补一组同义/别名再查一次，还查不到才交给模型。
        #       ⑤ 贴给学生的是 `answer_for_student`（**学生视图**，#41-A）：
        #          模型视图里那句「第 N 块（原文第 X 字起，相关度 Y）」是给模型定位用的元数据，
        #          不经模型直接贴进气泡就成了"当学生的面背数据库"。文档名保留，元数据去掉。
        #     ⚠️ 只给**日程栏（planner）**用：校园问答栏本来就有自己的知识库链路，
        #       接管它反而会盖掉那一栏"没配密钥"的降级提示（test_01 就是这么被撞红的）。
        #       这里补的正是**日程栏问校园信息没人管**的这个洞。
        #     （`_holding_card` 在 #42-A 那道闸那里算的，同一份、不抄第二遍）
        if (module_key == "planner"
                and not _holding_card
                and not any(w in message for w in _SELF_SCHEDULE_WORDS)
                and not (wants_add_todo(message) or wants_remove_todo(message)
                         or _done_claim or wants_add_course(message)
                         or wants_remove_course(message)
                         or wants_clear_timetable(message))
                and looks_like_question(message)):
            from app.modules.faq import answer_for_student, query_expansion
            # ⚠️ 判空从上面那条 `and` 链里挪进来了（#57-A）：它留在链上，"查不到"就没有落脚的地方
            #   —— 改写那一档必须插在"原句查不到"之后、"交给模型"之前，只能做成嵌套的 if。
            #   四条便宜的闸门照旧在链上：**先过闸、再问模型**，日程/报障/挂着卡的话一个字都不多问。
            if _kb_has_answer(message):
                return _kb_say(module_key, session_id, message,
                               answer="📚 " + answer_for_student(message),
                               note="校园问答：知识库直答",
                               phase="📚 问的是校园信息 → 翻知识库（按台账答，不现编）",
                               step_answer="知识库命中")
            # 与上面校园问答栏那一档同一个改写器、同一条规矩（#57-A，本体在 `faq.query_expansion`）：
            # 只在"本来要回没查到"时问一次，任何失败回空串 → 照旧交给模型，不拖住学生。
            _kb_x = await query_expansion(message, model_config)
            if _kb_x and _kb_has_answer(message, extra=_kb_x):
                return _kb_say(module_key, session_id, message,
                               answer="📚 " + answer_for_student(message, extra=_kb_x),
                               note="校园问答：知识库直答（换个说法才查到）",
                               phase="📚 原句查不到 → 补同义/别名再查一次（命中）",
                               step_answer="知识库命中（改写）")

    # 4) 组装引擎并跑 ReAct 循环（#61-C：整段抽到 `_run_agent_loop`，这里只留一次调用）
    return await _run_agent_loop(
        module_key=module_key, session_id=session_id, message=message,
        prompt=prompt, tools=tools, persona_key=persona_key,
        model_config=model_config, emit=emit,
    )


# ============ 判定层只读接口（意图抽取 / 路由诊断） ============

@app.post("/classify")
async def classify_probe(req: Request):
    """意图抽取 + 路由的**只读**接口（人话：只回答"这句话我理解成什么"，一个字节都不写）。

    它跟 `/api/chat` **并存**，不替代聊天主链路：聊天照旧走 `/api/chat`，
    这个口子只是把**判定层**的中间结果摊开给人看——"它到底听成了什么"。
    演示时可以拿它单独展示意图识别能力；出问题时也能一眼看出是哪一档判的。

    ⚠️ 三样东西**必须合并**，少一个就答不全（这是本接口存在的全部理由）：
      · `looks_like_question(message)` → `is_question`
        （**`classify()` 不返回它**——它只回 module/intent/title/…，
         少了这一项，"纯问句 → is_question=true"这类用例根本没法验）
      · `Router().route(...)`  → 最终 `module`（内部还会走样本库 / 关键词兜底）
      · `intent.classify(...)` → `intent` / `confidence` / **是哪一档判的**（`source`）
    三处都是**项目已有的判定逻辑**，这里**不写第二套**——两本账一定漂移。

    ⚠️ `classify` 与 `Router().route` **都是 async**，必须 await；
    而且 `route` 要传 `sem=`：同一句话只判一次（不传的话它会再问一次模型，
    白花一次调用，还可能给出"module 和 intent 不是同一次判定"的怪结果）。

    请求体：`{"message": str, "module"?: str, "history"?: list, "model"?: dict}`
      · `module` 是**客户端所在栏目**（跟 `/api/chat` 里那个 module 同义），
        只回显在 `detail` 里供对照，**不用它覆盖判定结果**；
      · `model` 是学生端自带的 API 配置（跟 `/api/chat` 一致），可省。
    返回：`{"is_question", "module", "intent", "confidence", "detail"}`
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse(
            {"is_question": False, "module": "", "intent": "", "confidence": 0.0,
             "detail": {"error": "message 不能为空"}},
            status_code=400)

    history = body.get("history")
    history = history if isinstance(history, list) else []
    # 第二个能注入 override 的入口（/api/chat 是第一个）。**两个都得过 sanitize_override**，
    # 漏一个等于闸门没关——这条就是当初只改一处会踩的坑。
    override = sanitize_override(body.get("model"))

    # ① 语义层 / 样本库（一次调用，同时给 module + intent；没 key 会自动跳过）
    sem = await classify(message, history=history, override=override)
    # ② 粗判"这是问还是办"（classify 不返回它，必须单独调）
    is_question = looks_like_question(message)
    # ③ 语义层没给结论时，回落到**规则层预检**（`_rule_intent`，就是主链路判断
    #    "要不要升级到语义层"用的那一份）。这里只把它**展示**出来，不改任何行为。
    #    ⚠️ 它按设计不需要跟各分支完全一致（见它的 docstring），所以 source 记成 "rule"，
    #    前端会照这个注明"规则层预检"，不让人误以为是最终裁决。
    rule_intent = None if sem else _rule_intent(message)
    # ④ 最终类型：把语义结果 + 已确定的意图一起喂进去，不再重复判定
    #    （只传 sem 的话，「帮我加个游泳周四19:00」会因为句子里没有关键词而落到 faq，
    #      出现 intent=add_todo、module=faq 这种自相矛盾的一对——冒烟时就撞见了）
    module = await Router().route(message, override=override, sem=sem,
                                  intent_hint=rule_intent)
    intent = ((sem or {}).get("intent") or rule_intent
              or (INTENT_CHAT if is_question else ""))
    source = ((sem or {}).get("source") or ("rule" if rule_intent else "none"))

    detail = {
        "is_question": is_question,
        "source": source,                       # sample（样本库）/ llm / rule / none
        "source_note": SOURCE_MEANING.get(source, source),
        "module_of_client": (body.get("module") or "").strip(),
        "semantic": sem,                        # None = 这一档没生效（没 key / 把握太低）
        "matched_sample": (sem or {}).get("matched") or "",
        "history_used": len([m for m in history if isinstance(m, dict)]),
        "wrote_anything": False,                # 说清楚：这个口子不写库
    }
    return {"is_question": is_question, "module": module, "intent": intent,
            "confidence": float((sem or {}).get("confidence") or 0.0),
            "detail": detail}


# ============ 管理员侧接口（发布通知 + 接收学生消息） ============
#
# 这一段里凡是 `dependencies=[Depends(verify_admin)]` 的，都要带请求头
# X-Admin-Token（口令在 .env 的 CAMPUSTIME_ADMIN_TOKEN，代码里没有写死值）。
# 划界的原则只有一条：**管理员才做得到的事**才挂门。
# 下面这两个不挂，是有意为之，不是漏了——
#   · POST /api/student-message  学生端每轮自己存档对话，学生没有口令
#   · POST /api/workorders       学生发现问题点【确认上报】，挂门等于把这条链掐了
# GET /api/persona 也不挂（驿站页要显示客服名字），只有 POST 改人格的要挂。

@app.post("/api/student-message")
async def student_message(req: Request):
    """学生端每轮对话后调用，把消息记下来，供管理员接收（人话：学生说的话先存档）。"""
    body = await req.json()
    item = add_message(body.get("module", ""), body.get("message", ""), body.get("answer", ""))
    return {"ok": True, "id": item["id"]}


@app.get("/api/admin/messages", dependencies=[Depends(verify_admin)])
async def admin_messages(include_archived: bool = False):
    """管理员端拉取学生消息收件箱（倒序，最新在前）。

    `include_archived=1` 才会带上已归档的那些 —— 默认收起（#29）。过滤本身在
    `store.list_messages` 那一处，不在这里再判一遍（两处各判 = 迟早一处藏一处不藏）。
    """
    return {"messages": list_messages(include_archived=include_archived)}


# ============ 工单（学生确认上报的问题 → 管理端可见的正式记录） ============

@app.post("/api/workorders")
async def workorder_create(req: Request):
    """提交一条工单（人话：面板表单提交的；聊天里由确认条点【确认上报】落库）。

    聊天来源（source="chat"）会带上 session_id：学生点卡上报成功后，把那条
    work_order_report 确认卡从暂存里清掉，避免它一直挂在 /api/pending 里反复出现。
    """
    body = await req.json()
    kind = (body.get("kind") or "").strip()
    if not kind:
        return JSONResponse({"error": "问题类型不能为空"}, status_code=400)
    item = add_workorder(kind=kind, desc=(body.get("desc") or "").strip(),
                         source=body.get("source") or "panel")
    # 决策A（#18 单子 1）：**双写在同一个函数里** —— 工单落一条，学生消息那边也补一条，
    # 管理端「🛠️ 学生工单」和「📥 学生消息」两个视图都看得见，不用前端各调一次接口
    #（前端调两处 = 两本账，漏一处就静默少一条，是本项目反复栽过的形状）。
    # 归属（#37）：`add_workorder` / `add_message` 都按 **cookie 里的当前学号**记 `owner`
    #   （#32 的 ContextVar，判据在 store 那一处，不从 body、不从学生原话里抠）。
    #   ⚠️ 这条注释以前写着"没有学生身份体系，认不出是谁报的"——**身份体系已经有了，那句话过期了**。
    #   老记录（那之前立案的）没有 owner，界面上显示「未登记」，**不许回填**（回填=编造证据）。
    add_message(module="workorder",
                message=f"{kind}｜{(body.get('desc') or '').strip() or '（无补充描述）'}",
                answer=f"已立案（工单 {item['id']}），状态：{item.get('status') or '待处理'}",
                kind="report", ref=item["id"])
    # 聊天里点卡上报的：清掉那张确认卡（面板表单提交没有 session，不受影响）
    sid = (body.get("session_id") or "").strip()
    if sid and (body.get("source") or "panel") == "chat":
        try:
            clear_pending(sid)
        except Exception:
            pass
    return {"ok": True, "workorder": item}


@app.get("/api/my/workorders")
async def my_workorders(include_archived: bool = True):
    """学生查**自己**报过的工单（人话：快递面板那块「我提交过的工单」，进度的唯一来源）。

    ⚠️ 三条红线，都是这几天刚付过学费的形状：
      ① **靠 cookie 认人，不收任何"学号"参数**：参数谁都能改，cookie 才是这个人当下的身份
         （#32 的中间件已经把它放进 ContextVar，这里只读，不另开来源）。
      ② **无身份时返回空列表，绝不回落到全量**：这条接口不挂口令，回落一次就等于
         把全校的报障摊给任何打开页面的人看（#34 刚为"驿站不能读学生待办"打过一次架）。
      ③ 默认**带已归档**：对管理端"归档"是收起，对学生那是"这事结了"，他自己那份历史该看得全 ——
         看不见才会以为"我的单子被删了"，那是又一次"点了没反应"。
    """
    who = current_student()
    if not who:
        return {"workorders": []}
    return {"workorders": list_workorders(include_archived=include_archived, owner=who)}


_WO_STATUSES = ("待处理", "处理中", "已解决")


def _age_hours(ts: str) -> float | None:
    """从"报的那会儿"到现在过了多少小时 —— **后端算这一次**（#54 纪律 4）。

    为什么不在前端 `new Date() - ts`：跨天/时区/夏令时都会算错，演示当天换台机器就显示成
    负天数；而且后端哪天换了存储格式，前端那一堆减法全废。
    算不出来（没有时刻 / 格式不对 / 时间在将来）一律回 **None**，前端拿 None 就不显示这句 ——
    宁可不显示，也不许显示一个猜出来的数。
    """
    try:
        t = datetime.datetime.strptime(str(ts), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    h = (clock.now() - t).total_seconds() / 3600.0
    return round(h, 1) if h >= 0 else None


def _age_text(h) -> str:
    """把"等了多久"翻成一句人话（#54 纪律 4：连这句话都在后端翻，前端不许自己除 24）。

    算不出来就回空串，前端那句「已经等了…」整条不出现 —— 宁可不显示，
    也不显示一个猜出来的数。
    """
    if h is None:
        return ""
    if h < 1:
        return "不到 1 小时"
    if h < 48:
        return f"{int(h)} 小时"
    return f"约 {int(round(h / 24.0))} 天"


@app.get("/api/my/reports")
async def my_reports(include_archived: bool = True):
    """学生自己的上报（#54）：**工单 + 自建服务登记**合成一张表，附进度。

    为什么要合并：学生眼里"我报过的事"就一件，不该让他先去快递栏、再去别的栏找第二本账。
    两个源形状不同（工单 = kind/desc，登记 = fields 字典），所以这里**归一成一个固定形状**，
    前端只写一套渲染：`{source, id, sid, title, detail, status, ts, dept,
                      trail, progressed, age_hours}`。

    ⚠️ 三条红线与 `GET /api/my/workorders` **逐条一致**（那张口是刚付过学费的形状）：
       ① 认人只靠 cookie（`current_student()`），不读任何"学号"参数；
       ② 无身份 → 直接回空表（**不许**落成 demo 口径，回落一次就是把全校报障摊给路人）；
       ③ 默认带归档（对管理端"归档"是收起，对学生那是"这事结了"）。
    ⚠️ `owner` 为空的记录**一律不出现在这里**：那是驿站/后勤现场录的条目，
       不排掉就成了"每个学生都看到同一批别人的单子"。
    ⚠️ `dept` 是给学生的**去向**（该去问驿站还是后勤），不是内部排障用的表名；
       工单这一路的承接方就是驿站（内置那项 `table=workorders` 就是它的台账），
       所以这里如实写驿站 ——「帮我报修电梯」这类被 `报修` 硬信号接走的句子目前**确实**在工单里，
       分流改的是品类措辞、不是它的去向（#54 §六 的边界，见本轮证据单）。
    """
    who = current_student()
    if not who:
        return {"items": []}
    items = []
    for w in list_workorders(include_archived=include_archived, owner=who):
        tr = trail_of(w)
        st = str(w.get("status") or "")
        # 已结的不算"等了多久"（它不再等人了）：回 None ⇒ age_text 空串 ⇒ 那句不出现
        _h = _age_hours(w.get("ts")) if st != "已解决" else None
        items.append({"source": "workorder", "id": str(w.get("id") or ""), "sid": "",
                      "title": str(w.get("kind") or "其他问题"),
                      "detail": str(w.get("desc") or ""),
                      "status": st, "ts": str(w.get("ts") or ""),
                      "dept": "驿站",
                      "trail": tr, "progressed": bool(tr),
                      "age_hours": _h, "age_text": _age_text(_h)})
    for svc in list_services():
        if is_builtin_service(svc):
            continue
        sid = str(svc.get("id") or "")
        for r in list_custom_records(sid):
            v = _record_view(r)
            if v["owner"] != who:
                continue
            _h = _age_hours(v.get("ts")) if v.get("status") != "已解决" else None
            items.append({"source": "service", "id": v.get("id") or "", "sid": sid,
                          "title": str(r.get("name") or svc.get("name") or "登记"),
                          "detail": "、".join(f"{k}：{x}" for k, x in (v.get("fields") or {}).items()
                                              if str(x or "").strip()),
                          "status": v.get("status") or "", "ts": v.get("ts") or "",
                          "dept": v["dept"], "trail": v["trail"], "progressed": v["progressed"],
                          "age_hours": _h, "age_text": _age_text(_h)})
    items.sort(key=lambda x: x["ts"], reverse=True)
    return {"items": items}


def _workorder_status_response(wo_id: str, body: dict):
    """改工单状态这件事的**唯一实现**，管理端与驿站两个入口共用。

    两处各写一遍校验就是两本账：将来加一档"已关闭"只会改到一个地方，另一个入口
    就悄悄把合法值判成 400（这类"两份状态表"的账本项目已经吃过几次）。

    ⚠️ 「已解决」现在还驱动归档（#29 用户拍板：办完就收起来，不许在表里越堆越多）。
    联动只许写在这**一处**：store 那边只改状态，编排不许散到两个路由里、更不许搬进 store。
    反向也必须管：从「已解决」改回未结时自动放回 —— 只做正向，误标一次就得去归档区捞人。
    """
    status = (body.get("status") or "").strip()
    if status not in _WO_STATUSES:
        return JSONResponse({"error": "status 只能是 待处理/处理中/已解决"}, status_code=400)

    # 先取"从哪来、到哪去"：联动要看旧状态**和旧归档态**，只看新值就会谎报动作
    # （本来就已收起的，再调一次归档并不能让它"更收起"，回执却写着"已收起"= 又一次谎报）。
    before_status, before_archived = "", False
    for w in list_workorders(include_archived=True):
        if str(w.get("id") or "") == str(wo_id):
            before_status = w.get("status") or ""
            before_archived = bool(w.get("archived"))
            break

    item = update_workorder_status(wo_id, status)
    if not item:
        return JSONResponse({"error": "工单不存在"}, status_code=404)

    auto_archived = auto_unarchived = False
    mirrored = 0
    if status == "已解决" and not before_archived:
        got = set_workorder_archived(wo_id, True)     # 级联镜像就在这一个函数里，别在这儿再调消息口
        if got:
            auto_archived, item, mirrored = True, got[0], got[1]
    elif status != "已解决" and before_status == "已解决" and before_archived:
        got = set_workorder_archived(wo_id, False)
        if got:
            auto_unarchived, item = True, got[0]

    # 三个字段是给前端**说人话**用的：这一行会凭空消失（或凭空回来），
    # 不写清楚就是"点了没反应"那一族的第二种形态。
    return {"ok": True, "workorder": item,
            "auto_archived": auto_archived,
            "auto_unarchived": auto_unarchived,
            "mirrored_messages": mirrored}


@app.get("/api/workorders", dependencies=[Depends(verify_admin)])
async def workorder_list(include_archived: bool = False):
    """管理端查看全部工单（倒序）。已归档的默认收起（#29），`include_archived=1` 才带出来。"""
    return {"workorders": list_workorders(include_archived=include_archived)}


@app.post("/api/workorders/{wo_id}/archive", dependencies=[Depends(verify_admin)])
async def workorder_archive(wo_id: str, req: Request):
    """归档 / 放回一条工单 —— **软删**：台账里那条一个字节不少，只多/摘 `archived`。

    为什么是"管理端独有 + 软删"这两条（用户 2026-10-16 拍板）：
      · 工单与消息都是"学生报过什么"的证据，硬删就再也对不上账；
      · 驿站端 `/api/station/*` 是**免口令**的（演示级，见下面那段注释），
        免口令还能收台账 = 任何人不用口令就能清工单，这道口子不开。
    双写那条学生消息在 `set_workorder_archived` 里**一起**收起（不在这里再调一次消息接口）。
    """
    body = await req.json()
    archived = bool(body.get("archived", True))
    hit = set_workorder_archived(wo_id, archived)
    if not hit:
        return JSONResponse({"error": "工单不存在"}, status_code=404)
    item, mirrored = hit
    return {"ok": True, "workorder": item, "mirrored_messages": mirrored}


@app.post("/api/admin/messages/{mid}/archive", dependencies=[Depends(verify_admin)])
async def message_archive(mid: str, req: Request):
    """归档 / 放回一条学生消息（同 #29：软删、要口令）。

    ⚠️ 只动这一条，**不会反过来去动它引用的工单** ——
    删镜像消息不该让案底消失；要连案底一起收起，走上面那条工单的归档口。
    """
    body = await req.json()
    archived = bool(body.get("archived", True))
    item = set_message_archived(mid, archived)
    if not item:
        return JSONResponse({"error": "这条消息不存在"}, status_code=404)
    return {"ok": True, "message": item}


@app.post("/api/workorders/{wo_id}/status", dependencies=[Depends(verify_admin)])
async def workorder_set_status(wo_id: str, req: Request):
    """管理员处理工单后更新状态（待处理/处理中/已解决）。"""
    body = await req.json()
    return _workorder_status_response(wo_id, body)


# ============ 驿站侧接口（#18 单子 2：学生报的快递/外卖问题**流到驿站**） ============
# ⚠️ 演示级：下面两个口子**不挂管理口令** —— 做法照 `/api/persona` 的 GET（驿站页要能直接显示），
#    不是漏了鉴权。本机演示人人可看 ≠ 公网可以这样开：真要上线得给驿站补一道自己的口令/账号，
#    跟"学生端没有账号体系"是同一件待办（EXPLAIN.md 六已列）。
# 为什么仍然读写同一份 workorders.json（而不是搬去"驿站专存"）：
#   ① 写库路径只有一条，"谁处理"是视图与状态的事，不是存储的事；
#   ② 管理端留一份监督，正是 18 号单子留给用户拍板那一问 —— 要"只落驿站"不用改存储，
#      把管理端那两个入口摘掉即可；现在**保留**，因为删它才是产品决定，且管理端那些护栏测试依赖它。

@app.get("/api/station/workorders")
async def station_workorder_list():
    """驿站看到的报障列表（倒序，最新在前）。

    #39-C 顺带带一个 `archived_count`：标「已解决」会被 #29 自动收起，
    驿站那块表**没有**管理端那行「🗄 已归档 N 条」，于是办完一条就地消失 ——
    用户那句"管理端能找到处理完的历史记录，驿站没有"正身是这一处。
    这里只给**条数**不给明细：这个接口 5 秒轮询一次，不该每轮都把历史整包搬一遍。
    """
    rows = list_workorders()
    return {"workorders": rows, "archived_count": len(list_archived_workorders())}


@app.get("/api/station/workorders/archived")
async def station_workorder_archived():
    """驿站那行「🗄 已处理 N 条」点开后要的明细 —— **只有读，没有写**。

    ⚠️ 这条 GET 是这一单唯一碰"归档"的驿站接口，也是 `test_58` D 批那条钉子改口的原因：
       那条钉的是**免口令面不许出现归档口**（谁不用口令就能收台账 = 事故），
       本单加的是同一份台账的**只读历史视图** —— 驿站本来就是这些案子的处理人，
       数据没跨过端，只是以前办完就看不见去哪了。写的那道（`/archive`）照旧只在管理端、照旧挂口令。
    ⚠️ **不放回**：把它改回「处理中」走既有的 `POST /api/station/workorders/{id}/status`，
       反向的 `auto_unarchived` 在 `_workorder_status_response` 那一处（#29）。
    挑"哪些算已处理"也不在这儿：那在 `store.list_archived_workorders()` 一处（路由不筛列表）。
    """
    return {"workorders": list_archived_workorders()}


@app.post("/api/station/workorders/{wo_id}/status")
async def station_workorder_set_status(wo_id: str, req: Request):
    """驿站处理完改状态 —— 与管理端同一个 store 函数，两个视图不会各说各话。"""
    body = await req.json()
    return _workorder_status_response(wo_id, body)


# ============ 驿站低代码：一句话 → 一项新服务（#18 单子 3，创新点落地） ============
# 边界只有一条，但它决定这个功能能不能留：
#   **模型只生成"服务定义"这份数据，绝不生成可执行代码。**
#   生成物必须过 store.validate_service_def 的白名单，只有过校验的才进 services.json；
#   之后所有动作都走固定路径（驿站点【登记】→ 确定性接口 → 写 custom/<id>.json）。
#   全项目这条路没有 eval / exec / importlib，也不给生成的服务注册任何写库工具 ——
#   "服务能扩展"如果不配这套闸，就等于"任何人说一句话就能往这台服务器里塞代码"。

_SERVICE_PROMPT = """你在为一个校园驿站系统按管理员的一句话定义一项新服务。
只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码围栏。字段必须是这些（一个都不能多）：
  "name": 服务名，不超过 12 个字，如「失物招领」
  "triggers": 学生会怎么说这件事，3~6 条短语
  "collect_fields": 登记时要向学生收集的字段名，2~5 条，如「丢失地点」「物品描述」
  "reply_template": 登记完成后回给学生的一句话
  "table": 台账归类，一个词，如 lostfound / repair
例子（只抄结构，内容按管理员那句话来）：
{"name":"失物招领","triggers":["我丢了饭卡","捡到东西交哪"],"collect_fields":["丢失物品","丢失地点","大概时间"],"reply_template":"已登记，找到后会通知你来驿站领。","table":"lostfound"}
"""


def _extract_json_obj(raw: str) -> dict | None:
    """从模型回话里抠出那一个 JSON 对象（做法照判定层：抠不出来就当失败，不猜）。"""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


async def _draft_service(idea: str):
    """那 20 行"一句话 → 一份过白名单的服务定义"，驿站端与管理端**共用一份**（#54）。

    返回 `(clean, 错误响应)`：错误时 clean 是 None，错误响应已经是 JSONResponse，调用方直接 return。
    为什么抽出来：不抽就得在管理端再抄一份 —— 抄一份等于下一版改 `_SERVICE_PROMPT` 时
    只改一处，另一处静默停在旧 prompt 上（这项目"抄两份漏一份"那族病最贵的一次）。
    ⚠️ 失败一律**不落库**：没有密钥 / 模型没回出 JSON / 字段不合法，都回一句人话，
       不许出现"存了半份定义、界面上看得见但登记必炸"。
    """
    idea = (idea or "").strip()
    if not idea:
        return None, JSONResponse({"error": "先说一句你想加什么服务"}, status_code=400)
    if len(idea) > 200:
        return None, JSONResponse({"error": "这句话太长了（限 200 字），说短一点更准"}, status_code=400)
    try:
        gateway = ModelGateway(user="内部:低代码建服务")
        # max_tokens 上限：定义就那么点大，模型开始写散文就该硬截（同判定层那一手的用法）
        reply = await gateway.chat(
            [{"role": "system", "content": _SERVICE_PROMPT},
             {"role": "user", "content": f"管理员的话：{idea}"}],
            max_tokens=400,
        )
    except LLMError as e:
        print(f"[低代码] 生成服务定义失败（kind={getattr(e, 'kind', '?')}）：{e}")
        return None, JSONResponse({"error": _say_llm_error(e)}, status_code=502)
    draft = _extract_json_obj((reply or {}).get("content") or "")
    if not draft:
        return None, JSONResponse({"error": "管家这次没给出规规矩矩的服务定义，请换个说法再试一次。"},
                                  status_code=502)
    clean, why = validate_service_def(draft)
    if not clean:
        return None, JSONResponse({"error": f"生成的定义不合规矩：{why}"}, status_code=400)
    return clean, None


def _dept_guard(svc: dict):
    """归不归驿站管的那道判据（403），`_station_only` 与删服务那口共用（判据只写一处）。"""
    if dept_of(svc) != "驿站":
        return JSONResponse(
            {"error": f"「{svc.get('name')}」归{dept_of(svc)}处理，驿站这边不能改。"},
            status_code=403)
    return None


def _station_only(sid: str):
    """驿站端管辖判据：这项服务归驿站吗。返回 `(svc, 错误响应)`，错误时 svc 是 None（#54）。

    用它的五个口都是**要碰那份台账文件**的（读记录 / 登记 / 改状态 / 删记录）：
    服务不在表里就绝不该去动文件系统（`sid` 会拼进 `custom/<sid>.json` 这个路径），
    所以这里 404 拦在前面。
    ⚠️ 403 而不是 404：服务**确实存在**，只是不归你管。
       回 404 会让驿站管理员以为自己建的服务消失了，然后去重建一份（真的会重建，实测过）。
    ⚠️ 为什么必须在后端拦：`/api/station/*` 全部**免口令**（既有设计，理由在那段注释里，
       本单不动鉴权模型）。前端隐藏只挡得住手滑，挡不住任何知道 sid 的请求 ——
       而 sid 就印在驿站端界面上。所以：**免口令可以接受，跨部门不行**。
    """
    svc = get_service(sid)
    if not svc:
        return None, JSONResponse({"error": "没有这项服务"}, status_code=404)
    return svc, _dept_guard(svc)


@app.get("/api/station/services")
async def station_services_list():
    """驿站端服务列表 —— **只列归驿站的**（#54：后勤的报修服务不进驿站台账，反之亦然）。

    判据用 `store.dept_of`，不在这里再写一遍 `svc.get("dept") == ...`：
    老定义没有那个键，直接取值的比较会把它们静默判成"不是驿站"，驿站当场少一项服务。
    """
    return {"services": [s for s in list_services() if dept_of(s) == "驿站"]}


@app.post("/api/station/services")
async def station_service_create(req: Request):
    """管理员一句话 → 生成一份服务定义 → 过白名单 → 落库成为可用服务。

    ⚠️ 驿站端建出来的**一律归驿站**，而且**不读请求体里的 dept**（`dept` 由路由注入，
       见 `store.add_service`）。这一条是权限的根：驿站能建后勤的服务
       ⇒ 驿站能给自己开后勤的台账，§三 那七个口的 403 就白拦了。
    """
    body = await req.json()
    clean, err = await _draft_service((body.get("description") or "") if isinstance(body, dict) else "")
    if err:
        return err
    return {"ok": True, "service": add_service(clean, dept="驿站")}


@app.delete("/api/station/services/{sid}")
async def station_service_delete(sid: str):
    """删一份服务**定义**（#39-A：低代码只能一句句长，建错了没法收）。

    只删定义、不动 `custom/<sid>.json` 的历史台账（判据与理由都在 `store.delete_service` 那一处）。
    内置那项（`table=workorders`）后端硬拒 —— 前端不给按钮只是让人不必点，这道才是真的。
    #54 再加一道：**不归驿站的服务也硬拒**（403，判据 `_dept_guard`，与同族那五个口同一份）。

    ⚠️ 这里**不用** `_station_only`：那个口的"服务不在表里"回 404（因为那几个口紧接着就要去碰
       那份台账文件），而删定义这一口对"不存在"的既有口径是 400 + 一句"内置不许删或不存在"
       （#39-A 定的，test_65 A3 钉着）—— 建错了再点一次删除，用户看到的该是"它已经没了"，
       不是"从来没存在过"。

    ⚠️ 与同族三个口一样**不挂管理口令**（驿站页没有口令输入框，这是 #18 就定下的演示级口子，
       限制与代价写在 `#18 单子 2` 那段注释里）。本单没扩大它：这里删的是可重建的定义，
       不是台账数据；真要上线时给驿站补自己的账号，那一手归 #38 之后那件待办。
    """
    svc = get_service(sid)
    if svc:
        err = _dept_guard(svc)
        if err:
            return err
    if not delete_service(sid):
        return JSONResponse({"error": "删不掉：内置服务不许删，或者这项服务不存在"}, status_code=400)
    return {"ok": True}


@app.get("/api/station/services/{sid}/records")
async def station_service_records(sid: str):
    """某项服务已登记的记录（服务不存在 → 404，不返回空列表装"什么都没发生"）。

    #54：不归驿站的服务 → 403（`_station_only`），别以为读接口无害：
    读得到别人的台账就是"跨部门看得见"，而 sid 就印在界面上。
    """
    svc, err = _station_only(sid)
    if err:
        return err
    return {"records": list_custom_records(sid)}


def _custom_field_errors(svc: dict, fields) -> str:
    """「这条登记能不能落库」的唯一判据 —— 驿站端与学生端**共用一份**。

    两处各写一遍迟早一份松（本项目反复吃过），所以收在这儿：
    不是字典 / 缺必填 / 多给键（往台账里塞任意键不能是低代码的副作用）。
    返回一句人话；空串表示可以写。
    """
    if not isinstance(fields, dict):
        return "登记内容得是一组字段"
    want = [str(x) for x in (svc.get("collect_fields") or [])]
    missing = [k for k in want if not str(fields.get(k) or "").strip()]
    if missing:
        return "这些信息还没填：" + "、".join(missing)
    stray = [k for k in fields if k not in want]
    if stray:
        return "这些字段不属于这项服务：" + "、".join(sorted(stray)[:4])
    return ""


@app.post("/api/station/services/{sid}/records")
async def station_service_record(sid: str, req: Request):
    """登记一条（人话：管理员在驿站端点【登记】，系统按服务定义把字段存进台账）。

    这是**确定性写库**：调用方是点了按钮的人，不是模型 —— 所以它不进确认漏斗，
    但也因此字段要在这里判干净：服务不存在 404、不归驿站 403、必填字段一个不许少、值过长就地截断。

    ⚠️ `owner=""` 是**显式**传的（#54）：驿站是店员的机器，现场录入没有学生身份，
       不传就会去取 `current_student()` —— 而驿站那个会话是固定的（#30），
       结果是"驿站每录一条，都算在某个学生头上"，那是冒充，不是缺数据。
    """
    svc, err = _station_only(sid)
    if err:
        return err
    body = await req.json()
    why = _custom_field_errors(svc, body.get("fields") if isinstance(body, dict) else None)
    if why:
        return JSONResponse({"error": why}, status_code=400)
    item = add_custom_record(sid, body["fields"], svc=svc, owner="")
    return {"ok": True, "record": item,
            "reply": str(svc.get("reply_template") or "已登记。")}


@app.post("/api/station/services/{sid}/records/{rid}/status")
async def station_service_record_status(sid: str, rid: str, req: Request):
    """驿站把一条登记记录标成"处理中/已解决"。

    三态与工单**共用同一份词表** `_WO_STATUSES`（不新开第二张状态表）——
    加一档"已关闭"只需要改那一处，驿站端两块表跟着一起变。
    ⚠️ 校验顺序是**先 400（状态非法）后 403/404（服务/记录不存在）**，照 `_workorder_status_response` 来的：
    同族两个口顺序不一致，将来只改一处就会出现"同一个坏请求一个回 400、一个回 404"。
    免鉴权与既有的 `/api/station/*` 同一档（演示级，理由与限制写在那段注释里）。
    """
    body = await req.json()
    _svc, err = _station_only(sid)
    return _custom_record_status_response(sid, rid, body, err)


@app.delete("/api/station/services/{sid}/records/{rid}")
async def station_record_delete(sid: str, rid: str):
    """删一条登记记录（#39-B）。**真删** —— 台账里那条就没了，找不回来。

    ⚠️ 顺序照同族那几个口来，但这里有个必须守住的理由：**先认服务（存在 + 归驿站），再碰文件**。
       `sid` 会被拼进 `custom/<sid>.json` 这个路径，服务表里没有这个 id 就根本不该去动文件系统
       （`list_custom_records` / `add_custom_record` 也是同一分工：路由认服务，store 只管那份文件）。
       少这一道，`sid=../../x` 这类值就成了"往数据目录外面找文件"的入口。
    """
    _svc, err = _station_only(sid)
    if err:
        return err
    if not delete_custom_record(sid, rid):
        return JSONResponse({"error": "这条登记记录不存在（也可能已经被删了）"}, status_code=400)
    return {"ok": True}


@app.post("/api/custom/records")
async def custom_record_confirm(req: Request):
    """学生点了确认卡 → **系统**把这条登记写进 `custom/<服务id>.json`（#22 单子 4 的执行端）。

    这条链路是"自定义服务真正能用"的那一半：判定层认得出、模型出得了卡，
    但**写入这一步没有一行经过模型** —— 模型手里那个 `propose_custom_record` 只会产卡。

    三道硬判据，缺一道这道口就是开放的写入口：
      1. **暂存里必须真有一张这张服务、这个会话的 `custom_record` 卡**。
         凭空 POST 一个 service_id + fields 是登记不进去的：那张"纸"是系统的授权凭据。
      2. 字段按那份服务定义收口（与学生/驿站同一份判据 `_custom_field_errors`）。
      3. 服务不存在 → 404；卡已经不在了 → 一句人话的 409，**不假装登记成功**。
    """
    body = await req.json()
    sid = str(body.get("session_id") or "").strip()
    service_id = str(body.get("service") or "").strip()
    svc = get_service(service_id)
    if not svc:
        return JSONResponse({"error": "没有这项服务"}, status_code=404)

    entry = peek_pending(sid) if sid else None
    cards = [o for o in ((entry or {}).get("options") or [])
             if isinstance(o, dict) and o.get("kind") == "custom_record"
             and str(o.get("service") or "") == service_id]
    if not cards:
        # 卡没了 = 超时、或者已经点过一次。这里绝不能"那就直接写一条"——
        # 那等于这张口在没有提案的情况下也能写库，红线 1 当场作废。
        return JSONResponse({"error": "那张确认条已经不在了（超时了或已经登记过）。"
                                      "再跟我说一次这件事，我重新挂一张。"},
                            status_code=409)
    why = _custom_field_errors(svc, body.get("fields"))
    if why:
        return JSONResponse({"error": why}, status_code=400)

    item = add_custom_record(service_id, body["fields"], svc=svc)
    # 一次点确认 = 一张卡用掉。整个会话的暂存清空（与工单那条同一收尾方式，
    # 免得下一句"确认"又把这这张卡当成还没办的再写一遍）。
    clear_pending(sid)
    return {"ok": True, "record": item,
            "reply": str(svc.get("reply_template") or "已登记。")}


@app.post("/api/admin/notify", dependencies=[Depends(verify_admin)])
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


@app.delete("/api/admin/notices/{nid}", dependencies=[Depends(verify_admin)])
async def admin_notice_delete(nid: str):
    """撤回一条通知（#39-D）。发错别字、发错人群，以前只能眼看着它挂着。

    路径挂在 `/api/admin/*` 且**必须带 `verify_admin`**：撤回是管理动作，
    漏挂就等于把"任何人可改管理端数据"敞开（护栏 `test_65` D2 钉着这条，
    反向那一条——不带令牌必须被拦——才是它真正防的东西）。
    只删 `notices.json` 里这一条，**不动它挂的附件原件**（理由见 `store.delete_notice`）。
    """
    if not delete_notice(nid):
        return JSONResponse({"error": "通知不存在（也可能已经撤回过）"}, status_code=400)
    return {"ok": True}


# ── #54：管理端接后勤（自建服务的台账 / 改状态 / 建服务）───────────────────────
# 为什么要单独三个带口令的口，而不是让管理端复用驿站那几个免口令的：
#   ① #54 之后驿站口按**部门**拦，后勤的服务从那儿进直接 403 —— 界面表现就是"点了没反应"；
#   ② 管理端是监督位，读的是全量（`?dept=` 空 = 全部），把它接到只给驿站看的口上，
#      等于把"监督"降级成"另一个部门的窗口"。
def _record_view(row: dict) -> dict:
    """一条登记记录给管理端/学生端看的归一层（#54）。**只读，不改文件**。

    三件事在这里算好，视图不许各算一遍（红线：进度类展示一律读后端算好的值）：
      · `dept` —— 按 `store.dept_of_record`（有快照用快照，没有才回查定义）；
      · `trail` —— 只给**真发生过**的变更，老记录就是空表（不合成假历史）；
      · `progressed` —— 有没有真 trail。前端拿它决定"画三步条"还是"一行状态 + 创建时间"，
        缺这个旗标就会给老记录画出一条全是灰点、一格时间都没有的假进度条。
    """
    out = dict(row or {})
    tr = trail_of(row)
    out["dept"] = dept_of_record(row)
    out["owner"] = str((row or {}).get("owner") or "")
    out["trail"] = tr
    out["progressed"] = bool(tr)
    out["updated_at"] = str((row or {}).get("updated_at") or (tr[-1].get("ts") if tr else ""))
    return out


def _custom_record_status_response(sid: str, rid: str, body: dict, jurisdiction_err=None):
    """改一条登记记录的状态 —— 驿站与管理端两个入口**共用这一份**。

    与工单那侧的 `_workorder_status_response` 同一个理由：两处各写一遍校验就是两本账，
    加一档状态只改一处，另一处会把合法值悄悄判成 400。
    :param jurisdiction_err: 调用方（驿站口）算好的管辖错误响应，原样透传；管理端没有
        跨部门限制，不传。⚠️ 顺序必须是 **400（状态非法）先于 403/404** ——
        同族两个口顺序不一致，就会出现"同一个坏请求一个回 400、一个回 404"。
    """
    status = ((body or {}).get("status") or "").strip()
    if status not in _WO_STATUSES:
        return JSONResponse({"error": "status 只能是 " + "/".join(_WO_STATUSES)},
                            status_code=400)
    if jurisdiction_err is not None:
        return jurisdiction_err
    if not get_service(sid):
        return JSONResponse({"error": "没有这项服务"}, status_code=404)
    item = update_custom_record_status(sid, rid, status)
    if not item:
        return JSONResponse({"error": "这条登记记录不存在"}, status_code=404)
    return {"ok": True, "record": _record_view(item)}


@app.get("/api/admin/service-records", dependencies=[Depends(verify_admin)])
async def admin_service_records(dept: str = ""):
    """自建服务的登记台账（管理端「后勤报修」那一档的数据源）。

    :param dept: 空 = 全部（默认值，给"排查"用）；传 `后勤` / `驿站` 就只那一档。
    ⚠️ **不含内置项**：内置那项（`table=workorders`）的账在 `workorders.json`，
       已经由「学生工单」那一档管 —— 判据用 `store.is_builtin_service`，
       别在这儿再写一次 `table == "workorders"`（那字面量全库只许有一处读它）。
    顺带把 `triggers` / `collect_fields` 一起给：管理端要照着定义画登记表单，
    少了这两样就得再打一次服务列表口（两份数据两次读，中间那份定义变了就是错位）。
    """
    want = (dept or "").strip()
    groups = []
    for svc in list_services():
        if is_builtin_service(svc):
            continue
        if want and dept_of(svc) != want:
            continue
        rows = [_record_view(r) for r in list_custom_records(str(svc.get("id") or ""))]
        live = [r for r in rows if str(r.get("status") or "") != "已解决"]
        groups.append({"id": svc.get("id"), "name": svc.get("name") or "",
                       "dept": dept_of(svc), "table": svc.get("table") or "",
                       "triggers": svc.get("triggers") or [],
                       "collect_fields": svc.get("collect_fields") or [],
                       "open": len(live), "total": len(rows), "records": rows})
    return {"groups": groups}


@app.post("/api/admin/service-records/{sid}/{rid}/status", dependencies=[Depends(verify_admin)])
async def admin_service_record_status(sid: str, rid: str, req: Request):
    """后勤改状态。与驿站那个口同一份实现（`_custom_record_status_response`）：
    同一张状态词表、同一个"先 400 后 404"的顺序，只是不带部门判据。
    """
    body = await req.json()
    return _custom_record_status_response(sid, rid, body)


@app.post("/api/admin/service-records/{sid}", dependencies=[Depends(verify_admin)])
async def admin_service_record_create(sid: str, req: Request):
    """后勤自己登记一条（电话打进来的报修、巡楼看到的坏点 —— 没有学生参与）。

    ⚠️ 这条是施工单没列、但界面那一栏**必须要**的口：#54 §四.3 让管理端每组挂一个
       「登记一条」表单，而驿站那个写口如今按部门拦 —— 后勤的服务从驿站口进直接 403，
       表单填完点【登记】就是"没反应"。所以管理端得有自己的写口，字段判据与驿站共用
       `_custom_field_errors`（同一份"这条登记能不能落库"，不许两份）。
    ⚠️ `owner=""` 显式递：后勤现场录的没有学生身份，不递就会取到 `current_student()`
       （管理台那个会话是登录着的），变成"管理员替某个学生报了一条修"。
    """
    svc = get_service(sid)
    if not svc:
        return JSONResponse({"error": "没有这项服务"}, status_code=404)
    if is_builtin_service(svc):
        # 内置那项的账是工单（workorders.json），不走这份台账 —— 与驿站端同一句理由
        return JSONResponse({"error": "这一项是内置的工单链路，请走「学生工单」那一档"}, status_code=400)
    body = await req.json()
    why = _custom_field_errors(svc, body.get("fields") if isinstance(body, dict) else None)
    if why:
        return JSONResponse({"error": why}, status_code=400)
    item = add_custom_record(sid, body["fields"], svc=svc, owner="")
    return {"ok": True, "record": _record_view(item),
            "reply": str(svc.get("reply_template") or "已登记。")}


@app.post("/api/admin/services", dependencies=[Depends(verify_admin)])
async def admin_service_create(req: Request):
    """管理端建服务：**一律归后勤**（#54 分流的下半场 —— 后勤要能自己建报修服务）。

    与驿站端共用 `_draft_service`（那句话 → 定义 → 白名单那 20 行只有一份）。
    ⚠️ `dept` 由**路由**注入，请求体里传了也不读：能读就等于任何人都能给自己开别的部门的台账。
    """
    body = await req.json()
    clean, err = await _draft_service(
        (body.get("description") or "") if isinstance(body, dict) else "")
    if err:
        return err
    return {"ok": True, "service": add_service(clean, dept="后勤")}


# ============ 管理控制台：知识库零代码维护 ============

@app.get("/api/admin/kb", dependencies=[Depends(verify_admin)])
async def admin_kb_list():
    """列出知识库全部条目（人话：管理台知识库表格的数据源）。"""
    return {"entries": list_kb()}


@app.post("/api/admin/kb", dependencies=[Depends(verify_admin)])
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


@app.put("/api/admin/kb/{index}", dependencies=[Depends(verify_admin)])
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


@app.delete("/api/admin/kb/{index}", dependencies=[Depends(verify_admin)])
async def admin_kb_delete(index: int):
    """按序号删除一条知识库条目。"""
    ok = delete_kb_entry(index)
    if not ok:
        return JSONResponse({"error": "条目不存在"}, status_code=404)
    return {"ok": True}


@app.post("/api/admin/kb/probe", dependencies=[Depends(verify_admin)])
async def admin_kb_probe(req: Request):
    """试检索探针（人话：管理员自己验一下"这句话学生问得到答案吗"，#46-C）。

    为什么要有它：以前唯一的验法是切到学生端问一句 —— 又慢，又把"查不到"变成一件**要靠猜**的事
    （是这条条目没命中？是被文档那一路盖掉了？还是这条根本没进检索面？）。

    ⚠️ 三条要紧的：
    ① **不另写一套检索**：直接调学生端那同一个 `faq._kb_parts()`。探针自己算一遍，
       就等于亲手造出"探针说命中、学生说没有"那种自相矛盾（本项目反复吃过的亏）；
    ② **只读**：不写 `kb.json`、不进聊天历史、不计模型流水（护栏 C3 拿整目录指纹钉住这件事）；
    ③ `student_view` 是 `answer_for_student()` 的原样输出 —— 也就是**学生气泡里真会出现的那段话**
       （#41-A 之后那份不带"相关度 3.80 / 原文第几字起"这些给模型定位的元数据），
       不是给模型看的那一份。
    """
    from app.modules.faq import _kb_parts, answer_for_student

    body = await req.json()
    q = str(body.get("query") or "").strip()
    if not q:
        # 空话不许"成功地查到 0 条"：那与真查不到长得一模一样（#41 那一族的老形状）
        return JSONResponse({"error": "先写一句要试查的话"}, status_code=400)

    entries, docs = _kb_parts(q)
    # 命中的条目要报**序号**（管理员得知道去编辑哪一条）。`_kb_parts` 读的是刚解析出来的字典，
    # 不是 `list_kb()` 里那批 ⇒ 只能按内容回查位置；内容完全相同的两条可互换，报第一条。
    def _key(r):
        return (str(r.get("question")), str(r.get("answer")),
                tuple(str(k) for k in (r.get("keywords") or [])))

    pos = {}
    rows = list_kb()
    for i, r in enumerate(rows):
        pos.setdefault(_key(r), i)
    out_entries = [{"index": pos.get(_key(e), -1),
                    "question": e.get("question"),
                    "keywords": e.get("keywords") or []} for e in entries]
    return {
        "query": q,
        "entries": out_entries,
        "docs": [{"name": d.get("name"), "i": d.get("i"), "score": d.get("score")} for d in docs],
        "hit": bool(out_entries or docs),
        "student_view": answer_for_student(q),
    }


# ============ 知识库拆词：一句话 → 模型按句意给检索关键词（#55-B，只产草稿） ============
# 为什么必须让模型来拆、规则拆不了（实测，不是偏好）：
#   `kb_head_words("游泳馆价格")` 只能吐出**整句** ['游泳馆价格']（没有虚词可劈、也没有滑窗可用），
#   而「考试周图书馆开到几点」派生出 12 个碎片（含「试周图」「馆开到」这种非词）。
#   管理员要的是"这条该用什么词才搜得到学生那句话"——这件事只有按句意判断才做得对。
# ⚠️ 这是**全项目唯一允许模型碰关键词**的地方，而且它仍然只产草稿：模型不知道学校有几栋楼，
#    也不知道学生管游泳馆叫「泳池」还是「游泳池」，所以结果必须回给管理员看、让他改，不许直接落库。
_KB_SPLIT_PROMPT = """你是校园助手的知识库整理员。管理员会给你一条「学生可能问的问题」，你只负责拆出**用来检索的关键词**。

一、判断顺序
1. 先判这句问的是**哪个对象**：游泳馆 / 图书馆 / 校车 / 补办学生证 / 宿舍报修…
2. 再判问的是这个对象的**哪个方面**：价格、开放时间、地点、流程、电话…

二、关键词怎么给
- 对象词必须有，而且要给**学生可能用的别的说法**：全称 + 常用简称 + 口语叫法
  （游泳馆 → 游泳、泳池；补办学生证 → 学生证、补办）
- 方面词**只在它能区分同类条目时**才给：同一条目多半只有一条，通常不需要。
- 每个词 2~8 个字，一共 2~6 个。
- **禁止**把问题原句整串当关键词（这是最常见也最没用的错）。
- **禁止**给任何问题都会有的泛词：多少钱、怎么、如何、几点、时间、电话、流程、价格、收费、能不能、可以。

三、输出
只输出一个 JSON，不要解释、不要 markdown 代码块：
{"keywords": ["…", "…"]}

示例 1
问题：游泳馆价格
{"keywords": ["游泳馆", "游泳", "泳池"]}

示例 2
问题：考试周图书馆开到几点
{"keywords": ["图书馆", "考试周", "开放时间"]}

示例 3
问题：补办学生证要多久
{"keywords": ["学生证", "补办学生证", "补办"]}
"""


@app.post("/api/admin/kb/split", dependencies=[Depends(verify_admin)])
async def admin_kb_split(req: Request):
    """一句话 → 模型按句意拆出的**检索关键词**（#55-B）。只产建议，**不写库**。

    ⚠️ 失败一律 502 + 一句人话，**不许回退到 `derive_kb_keywords`/`kb_head_words`**：
       那等于"模型挂了就悄悄换成固定格式拆"，管理员看不出区别，却再也得不到句意结果
       （而规则那一路对「游泳馆价格」只会还回整句 —— 正是这条链路要治的那个 bug）。
    ⚠️ 也不许"成功但拆出一堆泛词"就当结果用：`store.clean_split_words` 那一道闸负责把
       原句整串与泛词丢掉，丢光就报错（判据跟着词表走，所以它在 store 不在这里）。
    :param question: 一句话，≤100 字（长到超出的不是"一个学生问题"，是段落，该进文档知识库）
    """
    body = await req.json()
    q = str((body or {}).get("question") or "").strip() if isinstance(body, dict) else ""
    if not q:
        return JSONResponse({"error": "先把问题写进去，我按它拆词"}, status_code=400)
    if len(q) > 100:
        return JSONResponse({"error": "这句话太长了（限 100 字），问题写成一句话更准"}, status_code=400)
    try:
        gateway = ModelGateway(user="内部:知识库拆词")     # 「用量统计」里单列一行
        # max_tokens 上限：拆词就那么点大，给大了模型开始解释"为什么这么拆"（同判定层那一手）
        reply = await gateway.chat(
            [{"role": "system", "content": _KB_SPLIT_PROMPT},
             {"role": "user", "content": f"问题：{q}"}],
            max_tokens=120,
        )
    except LLMError as e:
        print(f"[拆词] 失败（kind={getattr(e, 'kind', '?')}）：{e}")
        return JSONResponse({"error": _say_llm_error(e)}, status_code=502)
    words, why = clean_split_words(q, _extract_json_obj((reply or {}).get("content") or ""))
    if not words:
        return JSONResponse({"error": why}, status_code=502)
    return {"ok": True, "keywords": words}


# ============ 管理控制台：文档知识库（**只收管理员在这一档传的知识文档**） ============
# 跟上面那份 kb.json（一问一答的条目）是两条线：这边是**整份文件**，切成块存着
# （uploads/<fid>.chunks.json），AI 靠 search_docs 在里面检索。
# ⚠️ 边界（#40 补）：`uploads.json` 本身是一个全局桶 —— 学生传的课表、通知附件、驿站批次清单
# 都在这同一份表里。这一档只认其中 `purpose == "kb"` 的那批：
# 不然学生的个人课表会先以**文件名**泄一次（管理端一眼看见「2026秋季课表-软件工程25级3班」），
# 再被点一下【重建索引】就变成谁都查得到的"知识"。
# 数据层已经不给非 kb 的文件建索引（`store.save_upload`），这里这两条口再各判一次，是为了把"为什么不行"说清。


def _doc_gate(fid: str):
    """这一档的门口判一次：该放行就回 None，不该放行就把话说清楚（JSONResponse）。

    ⚠️ 三种拒绝各有各的话，别合成一句"操作失败"：
      没这条 / 不是知识文档 / （删除那条还有一档：挂在通知上，见下面）。
    顺序也定在这儿：**先认存在、再认归属** —— 归属要查 meta，meta 不在就只能说"没这条"。
    """
    if get_upload_meta(fid) is None:
        return JSONResponse({"error": "文档不存在（可能已经删掉了）"}, status_code=400)
    if upload_purpose(fid) != "kb":
        return JSONResponse(
            {"error": "这不是知识文档，不能在这里操作 —— 学生上传的课表等个人文件不在这张表里"},
            status_code=400,
        )
    return None


@app.get("/api/admin/docs", dependencies=[Depends(verify_admin)])
async def admin_docs_list():
    """列出知识文档：文件名 / 字数 / 块数 / 索引状态 / 时间。

    只走 `list_docs()`（purpose=="kb"）—— 不是 `list_uploads()`：后者是"上传桶里有啥"，
    把那个桶整个抖给管理端就是 #40 补要修的那件事（学生文件全暴露在列表里）。
    块数**现读索引文件**，不是从 uploads.json 抄的（那份表里根本没存这个数，
    理由写在 store.save_upload）—— 抄来的数字在重建索引之后就会开始说谎。
    """
    docs = []
    for f in list_docs():
        n = len(list_chunks(f.get("id") or ""))
        docs.append({
            "id": f.get("id"),
            "name": f.get("name") or "",
            "chars": f.get("chars") or 0,
            "chunks": n,
            "indexed": n > 0,      # 老数据（#40 之前传的）这里是 false，点【重建索引】补上
            "ts": f.get("ts") or "",
        })
    return {"docs": docs}


@app.post("/api/admin/docs/{fid}/reindex", dependencies=[Depends(verify_admin)])
async def admin_doc_reindex(fid: str):
    """按已经存下来的文字重新切块（改了分块参数、或给老文档补建索引用）。

    "没这份文档"与"这不是知识文档"都回 **400 不是 404**：404 在管理端那段闸门（adminFetch）里
    会被翻成「接口不存在 —— 多半是服务在跑旧代码」，而这里接口是存在的，缺的是那条记录。
    同一个理由见 #39-D 的撤回通知。
    """
    blocked = _doc_gate(fid)
    if blocked is not None:
        return blocked
    n = reindex_upload(fid)
    if n is None:
        return JSONResponse({"error": "文档不存在（可能已经删掉了）"}, status_code=400)
    return {"ok": True, "chunks": n}


@app.delete("/api/admin/docs/{fid}", dependencies=[Depends(verify_admin)])
async def admin_doc_delete(fid: str):
    """删掉一份知识文档，连带它的分块索引（#40）。

    这是**真删**：文字、原件、索引、表里那条一起没，找不回来。
    两道闸：① 不是知识文档 → 拒（学生的上传文件不许从这一档删，那是另一件事、另一套确认话术）；
    ② 还挂在通知上 → 拒（#39-D 定过的口径）—— 抽走附件之后，学生点开那条通知看见的是个坏链接，
    而这不在管理员这次点动的预期里。这里只回话，**不动那些通知**（要撤通知去「发布通知」那档）。
    """
    blocked = _doc_gate(fid)
    if blocked is not None:
        return blocked
    refs = upload_notice_refs(fid)
    if refs:
        names = "、".join((r["title"] or "（无标题）")[:20] for r in refs[:3])
        return JSONResponse(
            {
                "error": (
                    f"这份文件还挂在 {len(refs)} 条通知上（{names}"
                    f"{' 等' if len(refs) > 3 else ''}），请先撤回或改掉那些通知再删文件"
                )
            },
            status_code=400,
        )
    if not delete_upload(fid):
        return JSONResponse({"error": "文档不存在（也可能已经删过一次）"}, status_code=400)
    return {"ok": True}


# ============ 管理控制台：客服人格配置 ============

@app.get("/api/persona")
async def persona_get():
    """读取当前客服人格。"""
    return {"persona": get_persona()}


@app.post("/api/persona", dependencies=[Depends(verify_admin)])
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
async def upload_file(file: UploadFile = File(...), purpose: str = Form("file"),
                      public_kb: str = Form("")):
    """接收一个上传文件，提取成文字存档（人话：让用户把资料交给 AI 读）。

    同时保留原件，这样通知的附件还能让学生下载回去。

    `purpose`（#40 补）：这一份是"给某句对话用的材料"（默认 file）还是"进知识库的文档"（kb）。
    只有 kb 会被切块、被 search_docs 检索、列在管理端「文档知识库」里 ——
    因为 `uploads.json` 是一个全局桶，不分开就会把学生的课表当成公共知识（文件名先泄一次）。
    ⚠️ 已知边界（照实写在这儿）：这个值是**客户端传来的表单字段**，
    而 `/api/upload` 是学生/管理员共用的免口令口（通知附件也走它，另开一条带口令的口就会长出两套上传行为）。
    所以理论上有人自己标 `purpose=kb` 把他的文件放进那张表 —— 放进来的是**他自己的**内容，
    不是别人的隐私；这一版按"边界清楚 + 一套上传口"取舍，要不要收紧成"只有带管理口令的请求才认 kb"归产品决定。
    非法值由 `store.norm_purpose` 统一退回 file（白名单只有那一处，别在这儿再抄一份）。
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

    # #45-B：这份文件**属于谁**。判"公共"要同时满足两条（purpose=kb **且** 来自管理端那一档的
    # `public_kb=1`）—— 只认 purpose 是不够的：它是客户端带来的表单字段（上面那段已知边界写着），
    # 而"把某个人的私有文件挂到公共检索里"这件事不该由传的人自己点头。
    # 不传 public_kb 又标了 kb 的那些（含未登录演示）落到 `current_student()`，
    # 管理员在已登录学生的浏览器里开管理端也算学生 ⇒ 那份不会变成公共知识（护栏 B8 钉着）。
    owner = "" if (purpose == "kb" and str(public_kb or "") == "1") else None
    item = save_upload(file.filename, text, raw=data, purpose=purpose, owner=owner)
    return {"ok": True, "file": item}


@app.get("/api/uploads")
async def uploads_list():
    """列出上传到过的文件（倒序）。

    ⚠️ #45-B：这里返回的是**当前这个人的**可见集（`visible_uploads()`），不是整个上传桶。
    以前这一句是 `list_uploads()` 全量倒出 —— 那条口**免口令、不认 cookie**，
    也就是说评委不用说话、直接把浏览器地址栏敲成 `/api/uploads`，
    就能拿到全站 35 份文件的 id + 文件名（一半是别人的课表原件名），
    再顺 `/api/uploads/{id}` 或 `/download` 读全文。这是本单里最直接的一条泄露路。
    """
    return {"files": visible_uploads()}


@app.get("/api/uploads/{fid}")
async def uploads_content(fid: str):
    """查看某个文件提取出的文字（人话：AI 眼里的这份资料长什么样）。"""
    if not can_read_upload(get_upload_meta(fid)):
        # 说"找不到"而不是"无权访问"：后者等于把"这里确实有一份"告诉他了。
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    content = get_upload(fid)
    if content is None:
        return JSONResponse({"error": "文件不存在"}, status_code=404)
    meta = get_upload_meta(fid) or {}
    return {"id": fid, "name": meta.get("name", ""), "content": content}


@app.get("/api/uploads/{fid}/download")
async def uploads_download(fid: str):
    """下载文件原件（人话：通知附件要能原样下载回去）。"""
    meta = get_upload_meta(fid) or {}
    if not can_read_upload(meta):
        return JSONResponse({"error": "文件不存在，或当时没有保留原件"}, status_code=404)
    path = get_upload_raw_path(fid)
    if not path:
        return JSONResponse({"error": "文件不存在，或当时没有保留原件"}, status_code=404)
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


@app.get("/api/admin/settings", dependencies=[Depends(verify_admin)])
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


@app.post("/api/admin/settings", dependencies=[Depends(verify_admin)])
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
        # 管理端这条也要过同一道闸门。理由比学生端那条更硬：
        # 这里填进去的地址会拿**管理员自己的 key** 去连，填错一个内网地址
        # 就是把主密钥寄到那台机器上。存之前先判，别等第一次对话才炸。
        want = (body.get("base_url") or "").strip()
        try:
            patch["base_url"] = check_base_url_ssrf(want) if want else ""
        except BaseUrlError as e:
            return JSONResponse({"ok": False, "error": f"接口地址不合规矩：{e}"}, status_code=400)
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


@app.post("/api/admin/settings/test", dependencies=[Depends(verify_admin)])
async def settings_test():
    """拿当前配置真的去问大模型一句（人话：配完点一下就知道通不通）。"""
    cfg = get_llm_config()
    if not cfg["api_key"]:
        return {"ok": False, "message": "还没配置密钥，请先填写 API Key"}

    try:
        # 问个极短的问题，目的只是验证鉴权和网络，
        # 顺便把模型名回显出来，方便确认连的是不是自己想用的那个
        # 走网关（唯一出口），这样"测连通"这一次调用也会进用量统计，
        # 管理员能直接看到它用的是哪个模型、通没通。
        gateway = ModelGateway(user="内部:后台连通测试")
        await gateway.chat([{"role": "user", "content": "回复两个字：正常"}])
    except LLMError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": f"连通正常 · 模型 {cfg['model']} · 配置来源：{cfg['source']}"}


# ============ 管理控制台：模型用量统计 ============
# 数据源就是网关写的那条 JSONL 流水（app/data/llm_usage.jsonl）。
# 这里只做"读 + 汇总 + 清空"，**不提供写接口**——账只能由网关记，
# 免得哪天有人手改出一个跟真实调用对不上的漂亮数字。

@app.get("/api/admin/usage", dependencies=[Depends(verify_admin)])
async def usage_get(limit: int = 50):
    """用量汇总（人话：管理台那张"用量统计"卡片的数据源）。

    limit 控制"最近调用"那张表返回多少条（默认 50，上限 200，别把浏览器撑死）。
    """
    limit = max(1, min(int(limit or 50), 200))
    try:
        data = summarize_llm_usage(limit_recent=limit)
    except Exception as e:
        # 统计挂了也不能让整张管理台白屏：给一份全零的空壳，前端照常渲染
        data = {"total_calls": 0, "ok_calls": 0, "error_calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "avg_latency_ms": 0, "users": 0, "models": [], "recent": [],
                "last_ts": "", "error": str(e)}
    data["ok"] = True
    return data


@app.post("/api/admin/usage/clear", dependencies=[Depends(verify_admin)])
async def usage_clear():
    """清空用量流水（人话：演示前想从零开始计数就点它）。

    只清流水，**不动任何待办/课表/会话**。返回清掉了多少条，前端好给个明确回执。
    """
    n = clear_llm_usage()
    return {"ok": True, "cleared": n, "message": f"已清空 {n} 条调用记录"}


# ============ 学生个人库：周表（课程）与待办 ============

@app.get("/api/timetable")
async def timetable_get():
    """读周表课程（人话：日程页周视图的课程格子数据源）。

    `uploaded`（#35）：**他传过课表没有** —— 撤掉登录播种之后新账号是空库，
    界面要分清「还没传」与「传过又清空了」。判据只有 `store.timetable_saved()` 这一处，
    前端拿不到文件系统，所以由这儿带出去。**只加字段，原有结构一字不动**（向后兼容）。
    """
    data = get_timetable_data()
    return {"courses": data.get("courses", []), "updated_at": data.get("updated_at", ""),
            "uploaded": timetable_saved()}


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
        month = clock.today().strftime("%Y-%m")
    return {"month": month, "days": list_todos_in_month(month)}


def _card_close_guard(sid: str, body: dict) -> str:
    """「点卡片上的确认按钮写库」这条路的**幂等判据 —— 唯一一份**，三个入口共用。

    返回空串 = 可以写；返回一句话 = **这张卡刚刚已经办过，别再写一遍**（那句直接回给学生）。

    为什么需要它（2026-10-17 真机，用户原话）：
    「我先前加入一个代办，点击确认后，退出去刷新回来，又弹了一次卡，再点击确认重新加了一个相同代办」。
    根因不在读接口 —— `/api/pending/{sid}` 一直只回"未 applied"的卡；
    是 `/api/todos`（以及 PUT/DELETE 那两个）**写完从来不销卡**，卡于是刷新后复活、再点就再写一条。
    课表 `/api/timetable/apply` 与批量 `/api/todos/batch` 早就写了 `mark_applied`，这一路是**漏了两个入口**，
    不是机制缺失。

    为什么还要认 `from_card`：`/api/todos` 这几个口不只卡片在用
    （代办栏的勾选框、面板上的删除、以及 50 多个护栏测试都直接打它们）。
    无条件销卡会把学生手里**另一张**没点头的卡一起作废 —— 那是误杀，
    后果比这个 bug 更难受（他回头说"确认删除"，系统答"没有待确认的提案"）。

    ⚠️ 已知限制（照实写，别当已经原子）：两个请求**真正同时**到达时（双击落在同一毫秒），
    仍有一个极小窗口两条都写进去 —— 本仓现在一处锁都没有（`读全量→改→整份写回`，见 EXPLAIN 六 并发写盘）。
    这一档挡的是"人类的重复点击"与"刷新后重点"，不是并发安全。
    """
    if not sid or not (body or {}).get("from_card"):
        return ""
    entry = peek_pending(sid)
    if not entry:
        return ""
    if entry.get("applied"):
        return "这条刚刚已经办过了，不用再点一次。"
    return ""


def _close_card(sid: str, body: dict) -> None:
    """写库**成功之后**销卡（唯一一处打标记的写法，形状与 /api/timetable/apply 一致）。

    ⚠️ 顺序是有讲究的：guard 必须在写之前问、这里必须在写成功之后打。
    反过来（先销卡再写）会变成"写失败了卡也没了，学生点了个寂寞"。
    """
    if sid and (body or {}).get("from_card"):
        mark_applied(sid)


@app.post("/api/todos")
async def todo_add(req: Request):
    """手动加一条待办 / 点【确认加入】把提案卡落库（两条路共用这个口，靠 from_card 分清）。"""
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    already = _card_close_guard(sid, body)      # ← 写之前问：这张卡是不是已经办过了
    if already:
        return {"ok": True, "already": True, "message": already}
    date = (body.get("date") or "").strip()
    start = (body.get("start") or "").strip()
    end = (body.get("end") or "").strip()
    title = (body.get("title") or "").strip()
    if not (date and start and end and title):
        return JSONResponse({"error": "标题、日期、开始与结束时间都要填"}, status_code=400)
    if is_past_date(date):
        # 「代办不能加到过去的日期」（用户 2026-10-11）。写入口这一层是最后一道：
        # 出卡那四条路都查过同一份判据，但手动加、改期、陈旧卡片过夜都可能递进旧日子。
        return JSONResponse({"error": f"{date} 已经过去了，不能往过去的日子里排待办"},
                            status_code=400)
    rep = (body.get("repeat") or "").strip()
    if rep not in (REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY):
        rep = REPEAT_NONE
    item = add_todo(title, date, start, end,
                    note=(body.get("note") or "").strip(),
                    category=(body.get("category") or "").strip(),
                    repeat=rep, weekday=int(body.get("weekday") or 0))
    # 改期经按钮确认：写新之前先把旧的那条挪走（删旧 + 加新）。
    # old_todo_id 只在改期提案里带着，普通加待办不会传，不影响别的路径。
    _old_id = (body.get("old_todo_id") or "").strip()
    if _old_id:
        try:
            delete_todo(_old_id)
        except Exception:
            pass
    # 学生是点卡片上的【确认加入】才走到这里的，这话得进会话历史——
    # 否则一刷新页面，那两条"✅ 已加入日程"就消失了，学生又会以为刚才没加上。
    if sid:
        append_conversation(sid, "assistant", f"已加入日程：{item.get('title', title)}"
                            f"｜{date}（{_weekday_of(date)}）{start}-{end}")
    _close_card(sid, body)      # ← 写完销卡。不销就是"刷新后卡复活 → 再点加第二条相同的"（#33 那一幕）
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
    # 「每个周天都写作业」这类卡勾出来的段，要写成**一条每周重复**的待办（2026-10-10）；
    # 「每天都要背单词」同理落一条 daily（2026-10-13）。
    # 白名单在这里、也在 store.add_todo 里各挡一次：认不得的值一律退回一次性，
    # 免得前端哪天传个新字样进来，库里就躺着一个没人会读的字段。
    repeat = (body.get("repeat") or "").strip()
    if repeat not in (REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY):
        repeat = REPEAT_NONE
    raw_slots = body.get("slots") if isinstance(body.get("slots"), list) else []
    other = (body.get("other") or "").strip()

    # 1) 先把前端递来的勾选项归一化（去重、丢掉字段不全的）
    chosen, seen = [], set()
    dropped_past = 0
    for s in raw_slots:
        if not isinstance(s, dict):
            continue
        d = (s.get("date") or "").strip()
        st = (s.get("start") or "").strip()
        en = (s.get("end") or "").strip()
        if not (d and st and en) or (d, st) in seen:
            continue
        # 卡片是上一轮算出来的，**过夜之后再点**日子就可能已经过了；
        # 语义层偶尔还会直接吐一个陈年日期（真机实测 2023-04-18）。
        # 写进去的是一条永远不会出现在日程里的记录 —— 这一道在写入口再拦一次
        # （出卡那一路四条都查过，判据是同一份 `store.is_past_date`）。
        if is_past_date(d):
            dropped_past += 1
            continue
        seen.add((d, st))
        chosen.append((d, st, en))

    if not chosen and dropped_past and not other:
        return JSONResponse(
            {"error": f"你勾的那几段日子已经过去了（{dropped_past} 段），"
                      f"重新问一句要哪天，我再给你算"}, status_code=400)

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
        added.append(add_todo(t or title, d, st, en, note=note, repeat=repeat))
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


_HHMM_RE = re.compile(r"^\d{1,2}:\d{2}$")
_BATCH_ITEMS_MAX = 20      # 与 `material.extract_deterministic` 的截断同一个数


@app.post("/api/todos/items_add")
async def todo_items_add(req: Request):
    """学生在那张**多选材料卡**上勾完、点【加入日程】按的就是这儿（#43）。

    和 `/api/todos/batch` 的分工（两条都留着，不合成一条）：
      · `/api/todos/batch` = **一件事 × N 个候选时段**（他从时段里挑几个方便的）；
      · 这里 = **N 件不同的事**，各自带日期与时段（材料里一行一件）。
      合成一条就得在函数体里分两种 payload，而两条的校验规则本来就不同
      （那边"缺字段的时段"是丢掉那条继续写，这边**任何一条不合法整批拒**）。

    ⚠️ 为什么整批拒而不是"坏一条跳一条"：学生看到的是一张卡。
      写进去一半比一条都不写更难解释 —— 他会以为全排上了，回头才发现少几条；
      报错时点名第几条，他改那一条就行。
    ⚠️ 过去的日子算"不合法"（用户 2026-10-11 那条明令：过去的日子不许排待办），
      和单条 / 批量两个口用的是同一份判据 `store.is_past_date`。
    """
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    already = _card_close_guard(sid, body)      # 写完刷新又点一次？（#33 那一幕）
    if already:
        return {"ok": True, "already": True, "message": already}

    rows = body.get("items") if isinstance(body.get("items"), list) else []
    if not rows:
        return JSONResponse({"error": "没收到要写的条目——在这张卡上勾几条再点【加入日程】。"},
                            status_code=400)
    if len(rows) > _BATCH_ITEMS_MAX:
        return JSONResponse({"error": f"一次最多写 {_BATCH_ITEMS_MAX} 条"
                                      "（材料很长就分几次，我也好逐条对）。"},
                            status_code=400)

    bad: list = []
    clean: list = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            bad.append(f"第 {i} 条不是一条待办")
            continue
        title = str(row.get("title") or "").strip()
        date = str(row.get("date") or "").strip()
        start = str(row.get("start") or "").strip()
        end = str(row.get("end") or "").strip()
        if not (title and date and start and end):
            bad.append(f"第 {i} 条缺标题、日期或起止时间")
            continue
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            bad.append(f"第 {i} 条的日期不是一天（{date}）")
            continue
        if not (_HHMM_RE.match(start) and _HHMM_RE.match(end)):
            bad.append(f"第 {i} 条的时间不像钟点（{start}-{end}）")
            continue
        if is_past_date(date):
            bad.append(f"第 {i} 条排在 {date}，那天已经过去了")
            continue
        clean.append({"title": title[:40], "date": date, "start": start, "end": end,
                      "note": str(row.get("note") or "").strip()[:80]})
    if bad:
        return JSONResponse({"error": "这批一条也没写进去：" + "；".join(bad)
                                     + "。改一下再点，我不写半批。"}, status_code=400)

    added, skipped = add_todos_batch(clean)
    _close_card(sid, body)      # 写完销卡：不销就是"刷新复活 → 再点写第二遍"（#33）
    parts = [f"{t.get('title')}｜{t.get('date')}（{_weekday_of(t.get('date') or '')}）"
             f"{t.get('start')}-{t.get('end')}" for t in added]
    message = ("✅ 已加入日程：" + "；".join(parts) if parts
               else "这些条日程里本来就有了，没重复写。")
    if skipped and parts:
        message += f"\n另外 {len(skipped)} 条已经在了（同标题同一天同时间），跳过了。"
    if sid:
        append_conversation(sid, "assistant", message.replace("\n", " "))
    return {"ok": bool(added), "added": added, "count": len(added),
            "skipped": skipped, "message": message}


@app.put("/api/todos/{tid}")
async def todo_update(tid: str, req: Request):
    """改一条待办（目前主要用来标记完成 / 取消完成）。

    ⚠️ "完成"有两种问法，别混着用：
      · **带 `on`（哪天）** —— 把完成落到那一天上：每周重复/每天的习惯记进 `done_dates`，
        一次性待办仍旧翻 status。这是给"这周做完了"用的。
      · 不带 `on` —— 一次性待办整条翻 status；
        按天记的那一档（weekly/daily）**2026-10-13 起也按天记**（退到 `done_date_for` 那天）。
        原来这里"不带 on 就整条翻 status"对重复待办是个**空操作**：
        读侧一律问 `done_dates`，status 写了没人读，学生点完确认第二天照旧挂着。
      为什么必须按天记：一条每周日的待办，学生勾掉本周**不等于以后都不用做**；
      整条 done 会让它第二周就从日程里消失，那是最容易被当成 bug 的行为。
    分流只写在 `store.set_todo_done_on` 一处，这个入口不再自己判档位。
    """
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    already = _card_close_guard(sid, body)      # ← 写之前问（#33：完成卡刷新后复活会被再点一次）
    if already:
        return {"ok": True, "already": True, "message": already}
    on = (body.get("on") or "").strip()
    if "status" in body:
        done = str(body.get("status") or "").lower() in ("done", "true", "1")
        item = set_todo_done_on(tid, on, done)
        if not item:
            return JSONResponse({"error": "待办不存在"}, status_code=404)
        _close_card(sid, body)
        return {"ok": True, "todo": item, "on": on}
    patch = {k: v for k, v in body.items() if k in ("title", "date", "start", "end", "note", "status")}
    item = update_todo(tid, patch)
    if not item:
        return JSONResponse({"error": "待办不存在"}, status_code=404)
    _close_card(sid, body)
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
    if not isinstance(body, dict):
        body = {}
    sid = (body.get("session_id") or "").strip()
    already = _card_close_guard(sid, body)      # ← 写之前问（#33：删除卡刷新后复活，再点会删到同名另一条）
    if already:
        return {"ok": True, "already": True, "message": already}
    item = get_todo(tid)
    if not delete_todo(tid):
        return JSONResponse({"error": "待办不存在"}, status_code=404)
    if sid and item:
        date = item.get("date", "")
        append_conversation(
            sid, "assistant",
            f"已删除待办：{item.get('title', '待办')}｜{date}（{_weekday_of(date)}）"
            f"{item.get('start', '')}-{item.get('end', '')}")
    _close_card(sid, body)
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
    # ⚠️ #59：这里以前直接取 `opts[-1]`，靠的是"暂存里最后那一项就是卡"这个**位置约定**。
    # 而删待办点名那一支写进去的是 `[提案, 候选清单]`（清单留在后面接「第二条」），
    # 于是刷新后取到的正是那张**不是卡**的清单 → 前端没有它的分支 → 掉进候选时段渲染器
    # → 三个 `undefined` 的乱码卡，点【确认所选】发出「确认 undefined undefined undefined 待办」。
    # 判据从"位置"改成"身份"：辅助项照样留在暂存里（学生回「第二条」还要用），只是不许被选中当卡。
    cards = [o for o in opts if o.get("kind") not in _PENDING_AUX_KINDS]
    if not cards:
        return {"pending": None}
    return {"pending": cards[-1], "session_id": sid}


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
