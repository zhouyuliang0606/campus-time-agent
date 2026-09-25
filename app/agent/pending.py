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

⚠️ 这一版的关键改动：**暂存从"纯内存"改成"内存 + 磁盘"**。
    旧版只活在内存里（见下面 `_load` 之前的注释），服务一重启（本地开发时
    改完代码 `uvicorn --reload` 自动重启、或手动重启）就清空——学生刚看到的
    那张确认卡瞬间没了，再回一句"确认"系统不知道在确认什么，于是掉回模型，
    模型只能回一句"确认我收到了，但待办还没真正入库"（这句就是截屏里那句）。
    现在每次改动都落盘到 `app/data/pending.json`（和会话历史 `sessions.json`
    同目录），服务重启、页面刷新都不再丢卡：刷新后确认卡重新从磁盘读出来，
    "AI 像失忆了"的体感也随之消失。

    磁盘读写跟 store.py 同一个约定：**每次都重新读 CAMPUSTIME_DATA_DIR 环境变量**，
    不在导入时定死——这样自动化测试里连开好几个 sandbox() 也各管各的，
    不会把 A 测试的待确认卡漏给 B 测试（早期版本就是导入时定死才串味）。
"""
import json
import os
import re
import time
from typing import Any

# session_id -> {"options": [...], "at": 时间戳, "applied": 是否已在界面上被点过}
# 内存里这份是热数据（读写都走它），磁盘上的 pending.json 是冷备份（切换数据目录后读回来）。
_PENDING: dict[str, dict[str, Any]] = {}

# 记录"上一次是从哪个数据目录读进来的"，目录一变就重新加载，保证各 sandbox 互不串味。
_PENDING_LOADED_DIR: str | None = None

# 超过这个秒数就算过期，避免一张很老的卡片在几天后被"确认"掉
_TTL = 3 * 3600

# 落盘文件名（与 store.py 的 sessions.json 同目录）
_PENDING_FILE = "pending.json"


def _pending_path() -> str:
    """暂存落盘路径：和会话历史同一个 data 目录，且**每次调用都重新读环境变量**，
    不在导入时定死——这样自动化测试先切 CAMPUSTIME_DATA_DIR 再 import，
    落盘也会跟着指到测试目录，不会偷偷改到真实演示数据。"""
    base = os.environ.get("CAMPUSTIME_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data"
    )
    return os.path.join(base, _PENDING_FILE)


def _load() -> dict:
    """从磁盘把暂存读回来（人话：服务启动/重启后、或切到新数据目录后，把学生那张
    还没确认的卡接上）。顺手清掉已经过期的，别让几天前的老卡复活。"""
    try:
        with open(_pending_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            now = time.time()
            return {
                sid: entry for sid, entry in data.items()
                if isinstance(entry, dict)
                and now - float(entry.get("at") or 0) <= _TTL
            }
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _ensure_loaded() -> None:
    """保证 `_PENDING` 是当前数据目录里的最新内容。

    为什么不做成"导入时读一次"：测试里会连开好几个 sandbox()，每个 sandbox 换一个
    CAMPUSTIME_DATA_DIR；如果导入时定死，后一个 sandbox 里还能翻出前一个的待确认卡。
    这里每次都比对"当前目录"和"上次读取的目录"，目录一变就重新读——和 store.py
    的"每次读写重新读环境变量"是同一个套路。
    """
    global _PENDING, _PENDING_LOADED_DIR
    cur = _pending_path()
    if _PENDING_LOADED_DIR != cur:
        _PENDING = _load()
        _PENDING_LOADED_DIR = cur


def _save() -> None:
    """把内存里的暂存写回磁盘（人话：任何一次"出卡 / 确认 / 清空"都顺手落盘）。
    落盘失败（权限、磁盘满）也不该让对话崩——内存态仍然有效，只是这次重启会丢。"""
    try:
        path = _pending_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_PENDING, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def save_pending(session_id: str, options: list) -> None:
    """把本轮生成的提案存进暂存（每轮换新的，旧的不要）。"""
    if not session_id or not options:
        return
    _ensure_loaded()
    _PENDING[session_id] = {
        "options": list(options),
        "at": time.time(),
        "applied": False,
    }
    _save()


def peek_pending(session_id: str) -> dict | None:
    """只看一眼暂存里有什么，**不取走**（确认卡渲染时用）。"""
    _ensure_loaded()
    return _pending_entry(session_id)


def take_pending(session_id: str) -> dict | None:
    """取出并清空某个会话的暂存（确认后落库时用，保证只执行一次）。"""
    _ensure_loaded()
    entry = _pending_entry(session_id)
    if entry is not None:
        _PENDING.pop(session_id, None)
        _save()
    return entry


def mark_applied(session_id: str) -> None:
    """学生在界面上点了确认卡 → 标记"已落库"，避免再被引用的聊天确认重复写一次。

    标记要落到**每一张卡**上（而不是只记在会话层）：执行时是按卡过滤的，
    只标会话的话同一批卡还会被再写一遍。
    """
    _ensure_loaded()
    entry = _PENDING.get(session_id)
    if not isinstance(entry, dict):
        return
    entry["applied"] = True
    opts = entry.get("options")
    if isinstance(opts, list):
        for o in opts:
            if isinstance(o, dict):
                o["applied"] = True
    _save()


def clear_pending(session_id: str) -> None:
    """清空暂存（学生聊了别的，旧的提案就作废）。"""
    _ensure_loaded()
    if _PENDING.pop(session_id, None) is not None:
        _save()


def _pending_entry(session_id: str) -> dict | None:
    """内部：取暂存，顺手清理过期项。"""
    if not session_id:
        return None
    entry = _PENDING.get(session_id)
    if not isinstance(entry, dict):
        return None
    if time.time() - float(entry.get("at") or 0) > _TTL:
        _PENDING.pop(session_id, None)
        _save()
        return None
    return entry


# 学生回一句"确认"时常见的说法（人话：这些话都算点头）
# 说明：这里**故意只收短促的同意**，像"删""去掉""可以吗"这类有歧义的
# （可能是新指令、也可能是反问）一律不收，免得把"再问一遍"听成"确认"。
CONFIRM_WORDS = frozenset({
    "确认", "确认一下", "确定的", "对", "对的", "好", "好的", "行", "同意", "可以", "可以的",
    "没问题", "没问题了", "嗯", "嗯嗯", "ok", "okay", "yes", "yep",
    "就这样", "就这个", "就这么办",
})


# 需求③的**备选方式**：学生没点弹窗、而是在聊天框回确认类文字，同样要触发执行。
# 这些是不带宾语就会觉得别扭的说法——"确认删除""确认添加""确认清空""好的，加入吧"。
# 写法刻意收着：必须以同意词开头 + 紧跟一个动作词，尾巴最多再带 10 个字
# （好让学生说"确认删除周一第一节"也能认），
# 这样"确认一下周一的课表是什么"这种**提问**就不会被误判成点头。
_CONFIRM_RE = re.compile(
    r"^(确认|确定|好的|好|行|同意|可以|ok)[，,、\s]{0,2}"
    r"(添加|加入|加上|加|删除|删掉|删|清空|清掉|修改|改|变更|安排|排上|执行|提交|上报"
    r"|就这样|就行|吧|了|的)"
    r".{0,10}$"
)


def is_confirmation(text: str) -> bool:
    """判断学生这句话是不是"点头"（人话：短促的同意，或"确认+动作"这种）。

    为什么要这么严格？"帮我看看周一第一节是什么"里也带"确认"两个字，
    但那显然是在问问题不是在同意。只认短句 + "确认+动作"，误判就少了。
    """
    t = (text or "").strip().lower()
    t = t.strip("。！？!?，,、；;：: \n\t~")
    if t in CONFIRM_WORDS:
        return True
    return bool(_CONFIRM_RE.match(t))
