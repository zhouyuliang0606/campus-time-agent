"""驿站/商户侧客服模块（人话：这是"商户视角"的 AI 客服——用管理员配置的"人格"回复学生咨询，
并基于快递台账，主动推送取件提醒和滞留预警）。

它和 student 端的"快递查询"共用同一份 express.json 台账，但立场不同：
- 学生端：查"我的"快递
- 驿站端：用客服人格，对外回复咨询 + 主动提醒取件
这正是原方案里"用自定义人格智能回复咨询，与短信/平台互补而非替代"的落地。
"""
from app.agent.tools import Tool
from app.modules.express import get_by_code, list_pending_pickup
from app.store import get_persona

MODULE_KEY = "station"


def build_system_prompt() -> str:
    """动态拼接系统提示：基础设定 + 当前客服人格（人话：让客服按管理员配的人设说话）。"""
    p = get_persona()
    return f"""你是{p['name']}，{p['role']}。
你的人设语气：{p['tone']}。
你的服务准则：{p['rules']}

工作场景（驿站/商户侧 AI 客服）：
1. 学生来问快递，用工具查台账，用上面的人设语气回复；
2. 可以主动列出"待取+滞留"的快递，替商户给同学发取件提醒、滞留预警（用你的人设语气写提醒文案）；
3. 取件码、物流以台账为准，绝不编造；
4. 用中文，亲切自然。"""


def build_tools() -> dict[str, Tool]:
    """工具复用快递台账（express 模块的函数），体现"几个模块共用一份数据"。"""
    return {
        "list_pending_pickup": Tool(
            name="list_pending_pickup",
            description="列出待取+滞留的快递（用于主动推送取件提醒/滞留预警）。无参数。",
            parameters={"type": "object", "properties": {}},
            func=list_pending_pickup,
        ),
        "get_by_code": Tool(
            name="get_by_code",
            description="凭取件码查询某件快递。入参 pickup_code 如 'B-5678'。",
            parameters={
                "type": "object",
                "properties": {
                    "pickup_code": {"type": "string", "description": "取件码，如 B-5678"}
                },
                "required": ["pickup_code"],
            },
            func=get_by_code,
        ),
    }
