"""驿站/商户侧客服模块（人话：这是"商户视角"的 AI 客服——用管理员配置的"人格"回复学生咨询，
并基于快递台账，主动推送取件提醒和滞留预警）。

⚠️ 读者是**店员**（#36）：台账按同学分组给他看（谁该被催），而不是对着他说「你有几件待取」。

它和 student 端的"快递查询"共用同一份 express.json 台账，但立场不同：
- 学生端：查"我的"快递
- 驿站端：用客服人格，对外回复咨询 + 主动提醒取件
这正是原方案里"用自定义人格智能回复咨询，与短信/平台互补而非替代"的落地。
"""
from functools import partial

from app.agent.tools import Tool
from app.modules.express import get_by_code, list_pending_by_student
from app.store import get_persona

# 店员口径：取件码要在**全店**的件里查（`get_by_code` 默认只在这个人的件里查，#36）。
# 把"这是店员"写在这一处就够了 —— 别到 `get_by_code` 里去判 `module_key`：
# 数据层不该知道谁在调它（认端是 #34 那道闸门的事，抄两遍就会打架）。
_lookup_for_staff = partial(get_by_code, staff=True)

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
4. 用中文，亲切自然；
5. 你只处理**快递/外卖/驿站服务**的事。学生的**个人日程、待办、课表**不属于你的职责，
   你也查不到（这边没有那个数据）：有人问起，就客气说明
   「学生个人日程我这边看不到，请用学生端查」，**不要猜、不要编、不要拿快递台账凑答案**。
6. 看这个界面的是**驿站店员**，不是跟你对话的学生。所以按"店员能拿去干活"的口径说：
   谁的件、几件、哪件滞留多久、该催谁。**不要**对学生腔 ——
   「你有 N 件待取」「请尽快取，避免退回」「建议你抽空去北区驿站一趟」这些都是说给学生听的，
   店员看了只会莫名其妙（要提醒谁，就把那位的学号和取件码写清楚）。"""


def build_tools() -> dict[str, Tool]:
    """工具复用快递台账（express 模块的函数），体现"几个模块共用一份数据"。"""
    return {
        "list_pending_by_student": Tool(
            name="list_pending_by_student",
            description=("列出**全店**还没取的快递，按同学分组：谁名下几件、哪件滞留多少天、"
                         "取件码与驿站。用来判断该催谁、该给谁发提醒。无参数。"),
            parameters={"type": "object", "properties": {}},
            func=list_pending_by_student,
        ),
        "get_by_code": Tool(
            name="get_by_code",
            description="凭取件码查询某一件快递（店员口径：全店都查得到，回话里带归属人）。"
                        "入参 pickup_code 如 'B-5678'。",
            parameters={
                "type": "object",
                "properties": {
                    "pickup_code": {"type": "string", "description": "取件码，如 B-5678"}
                },
                "required": ["pickup_code"],
            },
            func=_lookup_for_staff,
        ),
    }
