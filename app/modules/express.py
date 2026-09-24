"""快递模块（学生端视角）（人话：同学查自己的快递——有什么待取、取件码多少、有没有滞留要尽快取）。

它给 Agent 三个工具：
  1) list_my_packages   —— 看我所有的快递
  2) list_pending_pickup—— 只看还没取的（待取 + 滞留），主动提醒滞留件
  3) get_by_code        —— 凭取件码查某一件

注意：这里是从"收件同学"视角查。后面还会做"驿站/商户侧"（监听台账、主动推送、人格回复），那是另一视角。
"""
import datetime
import json
import os

from app.agent.tools import Tool

_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "express.json")

MODULE_KEY = "express"

# 到件超过这个天数还没取，就算"滞留"，要重点提醒
STAY_DAYS_WARN = 3

SYSTEM_PROMPT = """你是校园时间管家学生端的「快递助手」。
你帮同学查看自己的快递状态：有哪些待取、取件码多少、在哪个驿站、有没有滞留（到件很久没取）需要尽快取。
工作原则：
1. 学生问快递，先调工具拿准确数据，绝对不要瞎编取件码；
2. 看到"滞留"件（到件超过 3 天没取），要主动提醒尽快取，避免被退回；
3. 已取的件不用再提醒；
4. 用中文，语气像宿管阿姨一样热心、清楚。

【上报工单规则 —— 必须遵守】
学生反映快递丢失、破损、取件码异常等**需要管理员处理**的问题时：
- **先问一句：「是否上报给管理端？」**，等学生明确说"上报/是/确认"后，才调用 submit_work_order 生成工单；
- 学生没确认之前，**绝不许调用 submit_work_order**，也不许暗示已经上报了；
- 只是随口吐槽、查询状态、问怎么取件的，不需要上报，正常回答即可。"""


def submit_work_order(kind: str, desc: str = "") -> str:
    """生成一条工单提交至管理端（人话：学生**明确确认上报后**才调用这个立案）。

    管理员在管理端（/admin）能看到这条工单记录。学生没确认前不许调用。
    """
    from app.store import add_workorder
    item = add_workorder(kind=kind, desc=desc, source="chat")
    return (
        f"✅ 工单已上报管理端（编号 {item['id']}）：{item['kind']}"
        + (f" —— {item['desc']}" if item["desc"] else "")
        + "。管理员会尽快处理，处理进度可在快递面板的「我提交过的工单」里看到。"
    )


def _load() -> dict:
    with open(_DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _days_since(date_str: str) -> int:
    """算某件快递到件到现在过了几天（人话：用来判断是不是滞留了）。"""
    try:
        arrived = datetime.date.fromisoformat(date_str)
        return (datetime.date.today() - arrived).days
    except Exception:
        return 0


def _status_label(pkg: dict) -> str:
    """把一条快递的状态算清楚：已取 / 待取 / 滞留。"""
    if pkg.get("status") == "已取":
        return "已取"
    days = _days_since(pkg.get("arrived_at", ""))
    return "滞留" if days >= STAY_DAYS_WARN else "待取"


def list_my_packages() -> str:
    """工具①：列出我所有的快递。"""
    data = _load()
    pkgs = data.get("packages", [])
    if not pkgs:
        return "你目前没有快递记录。"
    lines = [f"你共有 {len(pkgs)} 条快递记录："]
    for p in pkgs:
        label = _status_label(p)
        flag = "⚠️滞留" if label == "滞留" else label
        lines.append(
            f"- [{flag}] {p['item']}｜取件码 {p['pickup_code']}｜{p['station']}｜到件 {p['arrived_at']}"
        )
    return "\n".join(lines)


def list_pending_pickup() -> str:
    """工具②：只看还没取的（待取 + 滞留），并重点标出滞留件。"""
    data = _load()
    pending = [p for p in data.get("packages", []) if _status_label(p) in ("待取", "滞留")]
    if not pending:
        return "你目前没有待取的快递，都取完啦 🎉"
    lines = [f"你有 {len(pending)} 件待取："]
    for p in pending:
        label = _status_label(p)
        if label == "滞留":
            days = _days_since(p.get("arrived_at", ""))
            lines.append(
                f"- ⚠️【滞留 {days} 天】{p['item']}｜取件码 {p['pickup_code']}｜{p['station']}｜请尽快取，避免退回！"
            )
        else:
            lines.append(
                f"- 【待取】{p['item']}｜取件码 {p['pickup_code']}｜{p['station']}"
            )
    return "\n".join(lines)


def get_by_code(pickup_code: str) -> str:
    """工具③：凭取件码查某一件快递。"""
    data = _load()
    for p in data.get("packages", []):
        if p.get("pickup_code", "").upper() == pickup_code.upper():
            label = _status_label(p)
            return (
                f"找到啦：{p['item']}\n状态：{label}\n取件码：{p['pickup_code']}\n"
                f"驿站：{p['station']}\n到件：{p['arrived_at']}"
            )
    return f"没找到取件码为 {pickup_code} 的快递，请核对一下。"


def packages_view() -> list[dict]:
    """学生端卡片视图用的结构化快递列表（只读，人话：给前端面板直接展示用）。

    AI 对话仍然走上面的三个工具；这里只是把同一份数据整理好递给前端，
    状态标签复用 _status_label——滞留判断逻辑只有这一份，不会两处打架。
    """
    out = []
    for p in _load().get("packages", []):
        q = dict(p)
        q["label"] = _status_label(p)
        q["stay_days"] = _days_since(p.get("arrived_at", ""))
        out.append(q)
    return out


def build_tools() -> dict[str, Tool]:
    return {
        "list_my_packages": Tool(
            name="list_my_packages",
            description="列出该同学所有的快递（含已取/待取/滞留）。无参数。",
            parameters={"type": "object", "properties": {}},
            func=list_my_packages,
        ),
        "list_pending_pickup": Tool(
            name="list_pending_pickup",
            description="列出还没取的快递（待取+滞留），并重点提醒滞留件。无参数。",
            parameters={"type": "object", "properties": {}},
            func=list_pending_pickup,
        ),
        "get_by_code": Tool(
            name="get_by_code",
            description="凭取件码查询某一件快递。入参 pickup_code 如 'B-5678'。",
            parameters={
                "type": "object",
                "properties": {
                    "pickup_code": {"type": "string", "description": "取件码，如 B-5678"}
                },
                "required": ["pickup_code"],
            },
            func=get_by_code,
        ),
        "submit_work_order": Tool(
            name="submit_work_order",
            description=(
                "把学生确认上报的问题立案提交至管理端。**必须先问学生「是否上报给管理端」"
                "并得到明确确认后才能调用**；学生没确认前严禁调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "问题类型，如 疑似丢件/包裹破损/取件码异常"},
                    "desc": {"type": "string", "description": "学生的补充描述，可留空"},
                },
                "required": ["kind"],
            },
            func=submit_work_order,
        ),
    }
