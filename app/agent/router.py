"""意图路由器（人话：用户一句话进来，先判断它归哪个模块管——课表？问答？驿站？）。

这是"共享 Agent 引擎"里的"协调"一环：不同业务模块共用一套引擎，
但每个模块有自己的工具箱和系统设定。router 先分好类，再交给对应模块去跑。
"""
import json

from app.llm.client import DeepSeekClient

# 所有模块的关键字白名单：router 分类时只许返回这些之一
MODULE_KEYS = ["schedule", "faq", "express", "takeout", "station",
               "planner", "lostfound", "repair", "notice", "admin"]

# 给大模型看的分类说明书（人话：只让它输出一个 JSON 告诉我归属哪个模块）
_SYSTEM_PROMPT = """你是校园助手的意图分类器。根据用户的话，只输出一个 JSON：{"module": "..."}。
可选 module 含义：
- schedule：课表/时间安排/复习计划/空闲时间
- faq：校园规定/地点/办事流程等问答
- express：学生查自己的快递/取件码/滞留
- takeout：学生查外卖订单/取餐点/待取
- station：驿站商户侧（监听台账/主动推送/人格回复）
- planner：学生个人日程（上传课表生成周表、给待办排时间、月表查看）
- lostfound：丢东西/捡到东西/失物招领
- repair：设施报修/东西坏了/维修
- notice：公告/通知
- admin：管理知识库/配置客服人格
只输出 JSON，不要任何解释文字。"""

# 关键词兜底：命中就直接归类，省一次大模型调用，也更稳
_QUICK_MAP = {
    # 学生个人日程（要排在"课表""复习"这些通用词**前面**：
    # 否则"上传课表"会被 schedule 先截走，导入流程就走不到了）
    "待办": "planner", "日程": "planner", "todo": "planner",
    "上传课表": "planner", "导入课表": "planner", "课表导入": "planner",
    "周表": "planner", "月表": "planner", "加到日程": "planner", "排进": "planner",
    # 学生端·快递
    "取快递": "express", "取件码": "express", "驿站取": "express", "快递": "express", "取件": "express",
    # 学生端·外卖
    "外卖": "takeout", "点餐": "takeout", "配送": "takeout", "取餐": "takeout", "订单": "takeout",
    # 学生端·课表
    "课表": "schedule", "复习": "schedule", "排任务": "schedule", "没课": "schedule", "空闲": "schedule",
    # 学生端·问答
    "图书馆": "faq", "食堂": "faq", "校车": "faq", "校医院": "faq", "宿舍": "faq", "奖学金": "faq", "校园网": "faq", "学生证": "faq",
    # 驿站商户侧
    "驿站": "station", "商户": "station", "客服": "station", "取件提醒": "station",
    # 拓展
    "丢": "lostfound", "捡": "lostfound", "失物": "lostfound",
    "报修": "repair", "维修": "repair", "坏了": "repair",
    "公告": "notice", "通知": "notice",
    "知识库": "admin", "人格": "admin",
}


class Router:
    """意图分类器（人话：输入一句话，输出一个模块关键字）。"""

    def __init__(self, llm: DeepSeekClient | None = None) -> None:
        self.llm = llm or DeepSeekClient()

    async def route(self, user_input: str, override: dict | None = None) -> str:
        """返回模块关键字（如 "schedule"）。拿不准时默认回 "faq"。

        override 是学生端自定义的 API 配置，透传给大模型分类那一档，
        这样没服务器密钥但学生填了自己 Key 的环境，路由也能走真模型。
        """
        text = user_input.lower()

        # 1) 先走关键词兜底（快、稳、零成本）
        for keyword, module in _QUICK_MAP.items():
            if keyword in text:
                return module

        # 2) 再查**样本库**（零成本，离线也能用）
        #    为什么要有这一档：上面那张 `_QUICK_MAP` 是 50 多个硬编码词的子串匹配，
        #    学生换个说法就全落空，直接掉到 LLM 分类——而 LLM 会**联想**：
        #    实测「卡片呢」被它顺着"卡"字想成校园卡，分去了失物招领，
        #    界面上一张确认卡都没有（学生原话：「后面就不弹卡了」）。
        #    样本库里存的是"以前真办成功过的说法"，命中就沿用当时那个模块，
        #    既不用调 API，也比让模型临场发挥稳。
        #    ⚠️ 这里**不调 LLM**：路由每句话都要走一次，加一次调用太贵；
        #       LLM 那一档留给 /api/chat 里的意图层（只在规则全落空时才问）。
        try:
            from app.agent.intent import INTENT_MODULE, match_sample
            hit = match_sample(user_input)
            if hit and hit.get("intent") in INTENT_MODULE:
                return INTENT_MODULE[hit["intent"]]
        except Exception:
            pass

        # 3) 兜底用大模型分类（处理说不清楚的口语）
        try:
            msg = await self.llm.chat(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_input},
                ],
                override=override,
            )
            data = json.loads(msg.get("content", "{}"))
            module = data.get("module", "faq")
            return module if module in MODULE_KEYS else "faq"
        except Exception:
            # 任何异常都别让程序崩，默认走问答模块
            return "faq"
