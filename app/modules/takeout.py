"""外卖模块（学生端视角）（人话：同学查自己点的外卖——到哪了、啥时候好、去哪取）。

它给 Agent 三个工具：
  1) list_my_orders    —— 看我所有外卖订单
  2) list_active_orders—— 只看还没拿到手的（制作中/配送中/待取）
  3) get_order         —— 凭订单号查某一单

和"快递"一样，这是"点餐同学"视角。后面"驿站/商户侧"会做商家监听台账、主动推送那种。
"""
import json
import os

from app.agent.tools import Tool

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "takeout.json")

MODULE_KEY = "takeout"

# 这几个状态表示"还没到手"，需要提醒去取
ACTIVE = ("制作中", "配送中", "待取")

SYSTEM_PROMPT = """你是校园时间管家学生端的「外卖助手」。
你帮同学查看自己点的外卖：到哪一步了、预计几点好、去哪个取餐点拿、取餐码多少。
工作原则：
1. 学生问外卖，先调工具拿准确数据，别编取餐码和预计时间；
2. 状态是"待取/配送中"的，提醒同学记得去取，别放凉了或超时；
3. 用中文，语气轻松活泼一点。"""


def _load() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _fmt(p: dict) -> str:
    """把一条订单格式化成一行可读文本。"""
    code = f"｜取餐码 {p['pickup_code']}" if p.get("pickup_code") else ""
    return (
        f"- 【{p['status']}】{p['shop']}（{p['items']}）｜预计 {p['eta']}"
        f"｜取餐点：{p['pickup_point']}{code}"
    )


def list_my_orders() -> str:
    """工具①：列出我所有的外卖订单。"""
    data = _load()
    orders = data.get("orders", [])
    if not orders:
        return "你目前没有外卖订单。"
    lines = [f"你共有 {len(orders)} 单外卖："]
    for p in orders:
        lines.append(_fmt(p))
    return "\n".join(lines)


def list_active_orders() -> str:
    """工具②：只看还没拿到手的（制作中/配送中/待取）。"""
    data = _load()
    active = [p for p in data.get("orders", []) if p.get("status") in ACTIVE]
    if not active:
        return "你目前没有待取的外卖，都吃完啦 🍜"
    lines = [f"你有 {len(active)} 单还没到手："]
    for p in active:
        lines.append(_fmt(p))
    return "\n".join(lines)


def get_order(order_id: str) -> str:
    """工具③：凭订单号查某一单。"""
    data = _load()
    for p in data.get("orders", []):
        if p.get("id", "").upper() == order_id.upper():
            return "查到了：\n" + _fmt(p)
    return f"没找到订单号 {order_id} 的外卖，请核对一下。"


def orders_view() -> list[dict]:
    """学生端卡片视图用的结构化外卖列表（只读，数据与 AI 工具同一份）。"""
    return [dict(o) for o in _load().get("orders", [])]


def build_tools() -> dict[str, Tool]:
    return {
        "list_my_orders": Tool(
            name="list_my_orders",
            description="列出该同学所有的外卖订单。无参数。",
            parameters={"type": "object", "properties": {}},
            func=list_my_orders,
        ),
        "list_active_orders": Tool(
            name="list_active_orders",
            description="列出还没拿到手的外卖（制作中/配送中/待取）。无参数。",
            parameters={"type": "object", "properties": {}},
            func=list_active_orders,
        ),
        "get_order": Tool(
            name="get_order",
            description="凭订单号查询某一单外卖。入参 order_id 如 'T1'。",
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号，如 T1"}
                },
                "required": ["order_id"],
            },
            func=get_order,
        ),
    }
