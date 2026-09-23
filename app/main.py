"""总入口（人话：FastAPI 把上面所有能力接到网页接口上，是整个服务的"大门"）。

请求进来后的完整链路：
  前端说话 → /api/chat → router 判断意图 → 取对应模块的工具箱+系统提示
          → engine 跑 ReAct 循环（必要时调工具）→ 返回回答 + 思考轨迹
"""
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agent.engine import AgentEngine
from app.agent.router import Router
from app.config import check_config
from app.modules.schedule import SYSTEM_PROMPT as SCHEDULE_PROMPT, build_tools as schedule_tools
from app.modules.faq import SYSTEM_PROMPT as FAQ_PROMPT, build_tools as faq_tools
from app.modules.express import SYSTEM_PROMPT as EXPRESS_PROMPT, build_tools as express_tools
from app.modules.takeout import SYSTEM_PROMPT as TAKEOUT_PROMPT, build_tools as takeout_tools

# 项目根目录（本文件在 app/ 下，根目录是上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")

app = FastAPI(title="校园时间管家 CampusTime", version="0.1.0")
check_config()

# —— 模块注册表（人话：每个模块在这里登记一下，路由命中后就能取用）——
# 学生端四个模块：课表安排 / 校园问答 / 快递 / 外卖。以后加驿站侧、管理台照此加一行。
REGISTRY: dict[str, tuple[str, Any]] = {
    "schedule": (SCHEDULE_PROMPT, schedule_tools),
    "faq": (FAQ_PROMPT, faq_tools),
    "express": (EXPRESS_PROMPT, express_tools),
    "takeout": (TAKEOUT_PROMPT, takeout_tools),
}
# 没命中任何模块时的兜底提示
DEFAULT_PROMPT = "你是校园时间管家，一个友好、靠谱的校园 AI 助手。"

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

    # 2) 取该模块的系统提示和工具箱
    prompt, build_tools = REGISTRY.get(module_key, (DEFAULT_PROMPT, lambda: {}))
    # 3) 组装引擎并跑 ReAct 循环（会按需调用工具）
    engine = AgentEngine(system_prompt=prompt, tools=build_tools())
    result = await engine.run(message)
    # 4) 返回最终回答 + 思考轨迹（前端可展示 Agent 怎么一步步想的）
    return {
        "module": module_key,
        "answer": result["answer"],
        "trace": result["trace"],
    }


def _render(name: str) -> HTMLResponse:
    """读取 static 目录下的某个页面并返回（人话：统一的"吐页面"小工具）。"""
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
        return HTMLResponse(f.read())


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
    """管理员端（发布通知 + 接收学生消息）。"""
    return _render("admin.html")


# 把 static 目录挂到 /static，方便以后放图片、脚本等静态资源
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
