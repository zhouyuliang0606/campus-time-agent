"""校园问答 FAQ 模块（人话：把分散的校园信息收口到一个知识库，学生问啥先来这里找答案）。

它给 Agent 一个工具：search_kb（在知识库里检索）。
Agent 拿到学生问题 → 调 search_kb 找相关条目 → 用自然语言组织成清楚回答。
这正是"收口分散校园信息"这个卖点的落地：规则、地点、流程都集中维护，改一处全站生效。
"""
import json
import os

from app.agent.tools import Tool

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kb.json")

MODULE_KEY = "faq"

SYSTEM_PROMPT = """你是校园时间管家学生端的「校园问答」助手。
你掌握一份校园知识库（图书馆、食堂、校车、校医院、宿舍规定、奖学金、网络等）。
工作原则：
1. 学生问校园相关问题，先调用 search_kb 在知识库里检索；
2. 找到相关条目，用自然语言组织成清楚、友好的回答，可补充一点贴心提示；
3. 知识库没有的，诚实说"这个我暂不清楚"，并建议去对应部门或官网查询，绝对不要编造；
4. 用中文，语气亲切。"""


def _load() -> dict:
    """读取知识库（人话：把 kb.json 变成字典）。"""
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def search_kb(query: str) -> str:
    """工具：在校园知识库里按关键词检索，返回最相关的内容（人话：像在手册里翻目录）。"""
    data = _load()
    q = (query or "").lower()
    # 把查询拆成词，只要知识库条目里命中任一关键词就收进来
    hits = []
    for item in data["entries"]:
        hay = (item["question"] + " " + " ".join(item.get("keywords", []))).lower()
        # 任一长度>=2 的词命中，或整句包含，就算相关
        matched = any(k in hay for k in q.split() if len(k) >= 2) or q in hay
        if matched:
            hits.append(item)

    if not hits:
        return "知识库里没有找到和「" + query + "」直接相关的内容。"

    lines = ["在知识库中找到以下相关内容："]
    for h in hits[:3]:  # 最多给 3 条，避免信息过载
        lines.append(f"Q: {h['question']}\nA: {h['answer']}")
    return "\n\n".join(lines)


def build_tools() -> dict[str, Tool]:
    """把 search_kb 包装成 Agent 能调用的工具。"""
    return {
        "search_kb": Tool(
            name="search_kb",
            description="在校园知识库检索答案。入参 query 是学生的问题或关键词，如 '图书馆几点关门'。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "学生的问题或关键词"}
                },
                "required": ["query"],
            },
            func=search_kb,
        ),
    }
