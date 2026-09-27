"""意图路由器（人话：用户一句话进来，先判断它归哪个模块管——课表？问答？驿站？）。

这是"共享 Agent 引擎"里的"协调"一环：不同业务模块共用一套引擎，
但每个模块有自己的工具箱和系统设定。router 先分好类，再交给对应模块去跑。

⚠️ 2026-09-27 重做：**类型判定归语义层，关键词表退成兜底**。
    以前这里是「`_QUICK_MAP` 关键词 → 样本库 → LLM」，关键词表排第一。
    可那张表是 50 多个 `if keyword in text` 的朴素子串匹配，学生换个说法就全落空，
    剩下的全指望 LLM 临场联想——实测「卡片呢」被"卡"字带跑成失物招领、
    「我要去哪里取快递」（在校园问答栏里问的）被"取快递"带成快递模块，
    而加待办那条分支又是**不分模块**的，于是学生收到一张待办卡。

    现在把顺序倒过来（**语义优先、规则兜底**）：
      ① 调用方已经判好的结果（同一句只判一次，省一次调用）
      ② 样本库：以前真办成功过的说法，命中就沿用当时的类型（零成本、离线可跑）
      ③ 语义层：`app/agent/intent.py`，一次调用同时给出 module + intent
      ④ `_QUICK_MAP` 关键词兜底：只有 ②③ 都没结果才用（没配 key / 调不通 / 离线）
      ⑤ 还不行回 `faq`——**答一句，比替学生办错一件事安全**
"""
from app.agent.intent import (
    DEFAULT_MODULE, MODULE_KEYS, classify_with_llm, match_sample, module_hint,
)
from app.llm.client import DeepSeekClient

# 模块白名单只有一份，定义在 app/agent/intent.py（判定层的主场），这里转出来给外部用。
# 依赖方向是 router → intent，单向、不成环。
__all__ = ["MODULE_KEYS", "Router", "_QUICK_MAP"]

# 关键词兜底表：命中就直接归类。**位置从"第一"降到"最后"**——
# 只在语义层拿不到结果（没配 key、离线、调不通）时才轮到它，它不再是主判据。
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
    """类型分类器（人话：输入一句话，输出一个模块关键字）。"""

    def __init__(self, llm: DeepSeekClient | None = None) -> None:
        # 允许注入一个现成客户端（测试 / 自定义配置）。**不注入就别在这儿建**：
        # 模块顶部那句 `from app.llm.client import DeepSeekClient` 是**导入时绑定死的**，
        # 导入之后再打桩 / 换配置都传不进来（实测：导入时建死的客户端收不到补丁，
        # 于是"语义层优先"的分支悄悄退回了关键词表）。交给判定层按需建就没这问题。
        self.llm = llm

    async def route(self, user_input: str, override: dict | None = None,
                    sem: dict | None = None) -> str:
        """返回模块关键字（如 "schedule"）。拿不准时回 `faq`。

        `override` 是学生端自己填的 API 配置（透传给模型那一档，这样服务器
        没配密钥、学生填了自己 Key 的环境也能走真分类）。

        `sem` 是调用方**已经拿到的判定结果**（`intent.classify` 的返回）。
        传进来就直接用，不再重复问一次——同一句话的判定只该发生一次。
        """
        # ① 调用方已经判好了（module 合法才算数）
        if (sem or {}).get("module") in MODULE_KEYS:
            return sem["module"]

        # ② 样本库：零成本、离线也能用，先问它
        try:
            hit = match_sample(user_input)
            if hit:
                mod = hit.get("module") or module_hint(hit.get("intent"))
                if mod in MODULE_KEYS:
                    return mod
        except Exception:
            pass

        # ③ 语义层：一次调用同时拿到 module 和 intent（只读分类，碰不到执行层）
        try:
            fresh = await classify_with_llm(user_input, override=override,
                                            client=self.llm)
            if (fresh or {}).get("module") in MODULE_KEYS:
                return fresh["module"]
        except Exception:
            pass

        # ④ 关键词兜底（旧的那张 50 多词表，位置从"第一"降到"最后"）
        text = (user_input or "").lower()
        for keyword, module in _QUICK_MAP.items():
            if keyword in text:
                return module

        # ⑤ 什么都不知道 → 问答模块。答一句总比替他办错一件事安全。
        return DEFAULT_MODULE
