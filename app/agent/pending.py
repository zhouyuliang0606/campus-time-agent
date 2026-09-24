"""待确认提案的**会话级暂存**（人话：AI 上一轮出的确认卡，先替学生"记着"）。

为什么要有这个文件？
    实测踩过的坑：学生已经说"确认"了，但 AI 有时回一句"我没有权限删除"，
    甚至让学生自己去课表页面找删除按钮——那里根本没有入口，学生当场懵掉。
    根因是"学生确认后该怎么执行"这件事依赖模型自己判断，模型一犯迷糊链路就断。

怎么修？
    把"出提案"和"执行"都变成**系统行为**，不依赖模型：
      1. AI 出确认卡时，提案顺手存进这里（按 session_id 分开，学生之间互不干扰）；
      2. 学生回一句"确认"时，/api/chat 先做确定性判断——
         如果这句话是个"确认"，而且暂存里确实有课表提案，就**由系统直接写入**；
      3. 学生说别的（新需求），暂存立刻清空，不会出现"隔了十分钟才确认"的误改。

注意：这里只存在内存里，服务重启即清空——学生数据一个字节都没多写，安全。
"""
import time
from typing import Any

# session_id -> {"options": [...], "at": 时间戳, "applied": 是否已在界面上被点过}
_PENDING: dict[str, dict[str, Any]] = {}

# 超过这个秒数就算过期，避免一张很老的卡片在几天后被"确认"掉
_TTL = 3 * 3600

# 学生回一句"确认"时常见的说法（人话：这些话都算点头）
# 说明：这里**故意只收短促的同意**，像"删""去掉""可以吗"这类有歧义的
# （可能是新指令、也可能是反问）一律不收，免得把"再问一遍"听成"确认"。
CONFIRM_WORDS = frozenset({
    "确认", "确认一下", "确定的", "对", "对的", "好", "好的", "行", "同意", "可以", "可以的",
    "没问题", "没问题了", "嗯", "嗯嗯", "ok", "okay", "yes", "yep",
    "就这样", "就这个", "就这么办",
})


def save_pending(session_id: str, options: list) -> None:
    """把本轮生成的提案存进暂存（每轮换新的，旧的不要）。"""
    if not session_id or not options:
        return
    _PENDING[session_id] = {
        "options": list(options),
        "at": time.time(),
        "applied": False,
    }


def peek_pending(session_id: str) -> dict | None:
    """只看一眼暂存里有什么，**不取走**（确认卡渲染时用）。"""
    return _pending_entry(session_id)


def take_pending(session_id: str) -> dict | None:
    """取出并清空某个会话的暂存（确认后落库时用，保证只执行一次）。"""
    entry = _pending_entry(session_id)
    if entry is not None:
        _PENDING.pop(session_id, None)
    return entry


def mark_applied(session_id: str) -> None:
    """学生在界面上点了确认卡 → 标记"已落库"，避免再被引用的聊天确认重复写一次。

    标记要落到**每一张卡**上（而不是只记在会话层）：执行时是按卡过滤的，
    只标会话的话同一批卡还会被再写一遍。
    """
    entry = _PENDING.get(session_id)
    if not isinstance(entry, dict):
        return
    entry["applied"] = True
    opts = entry.get("options")
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, dict):
                o["applied"] = True


def clear_pending(session_id: str) -> None:
    """清空暂存（学生聊了别的，旧的提案就作废）。"""
    _PENDING.pop(session_id, None)


def _pending_entry(session_id: str) -> dict | None:
    """内部：取暂存，顺手清理过期项。"""
    if not session_id:
        return None
    entry = _PENDING.get(session_id)
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("at") or 0) > _TTL:
        _PENDING.pop(session_id, None)
        return None
    return entry


def is_confirmation(text: str) -> bool:
    """判断学生这句话是不是"点头"（人话：只有短促的同意才算，长句子不算）。

    为什么要这么严格？"帮我看看周一第一节是什么"里也带"确认"两个字，
    但那显然是在问问题不是在同意。只认短句，误判就少了。
    """
    t = (text or "").strip().lower()
    t = t.strip("。！？!?，,、；;：: \n\t~")
    return t in CONFIRM_WORDS
