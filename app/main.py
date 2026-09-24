"""总入口（人话：FastAPI 把上面所有能力接到网页接口上，是整个服务的"大门"）。

请求进来后的完整链路：
  前端说话 → /api/chat → router 判断意图 → 取对应模块的工具箱+系统提示
          → engine 跑 ReAct 循环（必要时调工具）→ 返回回答 + 思考轨迹
"""
import datetime
import os
import uuid
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.engine import AgentEngine
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
    add_todo, update_todo, delete_todo,
)
from app.modules.faq import SYSTEM_PROMPT as FAQ_PROMPT, build_tools as faq_tools
from app.modules.express import SYSTEM_PROMPT as EXPRESS_PROMPT, build_tools as express_tools
from app.modules.takeout import SYSTEM_PROMPT as TAKEOUT_PROMPT, build_tools as takeout_tools
from app.modules.files import build_tools as files_tools
from app.modules.station import build_system_prompt as STATION_PROMPT, build_tools as station_tools
# planner 也是"可调用提示"：每次对话都要把**今天的日期**动态拼进去，
# 否则学生说"明天"，AI 根本算不出是哪一天
from app.modules.planner import build_system_prompt as PLANNER_PROMPT, build_tools as planner_tools

# 项目根目录（本文件在 app/ 下，根目录是上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")

app = FastAPI(title="校园时间管家 CampusTime", version="0.1.0")
check_config()

# —— 模块注册表（人话：每个模块在这里登记一下，路由命中后就能取用）——
# 学生端四个模块：课表安排 / 校园问答 / 快递 / 外卖。以后加驿站侧、管理台照此加一行。
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

router = Router()


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

    # 1) 若前端显式指定了模块且合法，直接用；否则交给 router 自动分类意图
    module_key = body.get("module")
    if not (module_key and module_key in REGISTRY):
        module_key = await router.route(message)

    # 2) 取该模块的系统提示和工具箱（提示可能是函数，按需调用以拼入最新人格）
    prompt_src, build_tools = REGISTRY.get(module_key, (DEFAULT_PROMPT, lambda: {}))
    base_prompt = prompt_src() if callable(prompt_src) else prompt_src
    # 统一拼上"你会读文件"的说明，每个模块因此都能利用用户上传的资料
    prompt = base_prompt + FILE_HINT

    # 工具箱 = 模块自己的工具 + 通用的文件读取工具。
    # 把"读文件"做成通用工具而不是复制进每个模块，
    # 以后新增模块会自动带上这个能力，不用重复实现。
    tools = build_tools()
    tools.update(files_tools())

    # 3) 组装引擎并跑 ReAct 循环（会按需调用工具）
    engine = AgentEngine(system_prompt=prompt, tools=tools)

    # 会话 id：前端带上，服务端就能把几轮对话串起来。
    # 没带就服务端生成一个并在响应里返回，前端存起来下次继续用。
    session_id = (body.get("session_id") or "").strip() or uuid.uuid4().hex[:12]

    # 之前聊过的内容（让 Agent 记得住上一轮商量到哪了）
    history = get_conversation(session_id)

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

    # 4) 返回最终回答 + 思考轨迹（前端可展示 Agent 怎么一步步想的）
    return {
        "module": module_key,
        "session_id": session_id,
        "answer": result["answer"],
        "trace": result["trace"],
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
    return {"ok": True, "todo": item}


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


@app.post("/api/chat/reset")
async def chat_reset(req: Request):
    """清空一段会话（人话：聊跑偏了或想重开一局时用）。"""
    body = await req.json()
    sid = (body.get("session_id") or "").strip()
    if sid:
        clear_conversation(sid)
    return {"ok": True}


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
