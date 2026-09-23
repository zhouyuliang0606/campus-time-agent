"""意图路由器（人话：用户一句话进来，先判断它归哪个模块管——课表？问答？驿站？）。

这是"共享 Agent 引擎"里的"协调"一环：不同业务模块共用一套引擎，
但每个模块有自己的工具箱和系统设定。router 先分好类，再交给对应模块去跑。
"""
import json

from app.llm.client import DeepSeekClient

# 所有模块的关键字白名单：router 分类时只许返回这些之一
MODULE_KEYS = ["schedule", "faq", "station", "lostfound", "repair", "notice", "admin"]

# 给大模型看的分类说明书（人话：只让它输出一个 JSON 告诉我归属哪个模块）
_SYSTEM_PROMPT = """你是校园助手的意图分类器。根据用户的话，只输出一个 JSON：{"module": "..."}。
可选 module 含义：
- schedule：课表/时间安排/复习计划/空闲时间
- faq：校园规定/地点/办事流程等问答
- station：取快递/外卖/驿站/取件码/滞留
- lostfound：丢东西/捡到东西/失物招领
- repair：设施报修/东西坏了/维修
- notice：公告/通知
- admin：管理知识库/配置客服人格
只输出 JSON，不要任何解释文字。"""

# 关键词兜底：命中就直接归类，省一次大模型调用，也更稳
_QUICK_MAP = {
    "取快递": "station", "外卖": "station", "驿站": "station", "取件": "station", "快递": "station",
    "课表": "schedule", "复习": "schedule", "排任务": "schedule", "没课": "schedule", "空闲": "schedule",
    "丢": "lostfound", "捡": "lostfound", "失物": "lostfound",
    "报修": "repair", "维修": "repair", "坏了": "repair",
    "公告": "notice", "通知": "notice",
    "知识库": "admin", "人格": "admin",
}


class Router:
    """意图分类器（人话：输入一句话，输出一个模块关键字）。"""

    def __init__(self, llm: DeepSeekClient | None = None) -> None:
        self.llm = llm or DeepSeekClient()

    async def route(self, user_input: str) -> str:
        """返回模块关键字（如 "schedule"）。拿不准时默认回 "faq"。"""
        text = user_input.lower()

        # 1) 先走关键词兜底（快、稳、零成本）
        for keyword, module in _QUICK_MAP.items():
            if keyword in text:
                return module

        # 2) 兜底用大模型分类（处理说不清楚的口语）
        try:
            msg = await self.llm.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ]
            )
            data = json.loads(msg.get("content", "{}"))
            module = data.get("module", "faq")
            return module if module in MODULE_KEYS else "faq"
        except Exception:
            # 任何异常都别让程序崩，默认走问答模块
            return "faq"
