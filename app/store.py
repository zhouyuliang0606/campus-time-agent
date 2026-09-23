"""简单文件存储（人话：把学生消息和通知存成 JSON 文件，管理员端能读、能发）。

为什么用文件而不是数据库？演示阶段要的就是"看得懂、改得动"，
每个文件就是一张表：student_messages.json = 学生消息表，notices.json = 通知表。
真上线时把这里换成数据库即可，接口不变。
"""
import datetime
import json
import os
import uuid

# 数据目录默认是 app/data；但可以用环境变量指到别处。
# 为什么留这个开关：跑自动化测试时，让测试写一份临时副本，
# 这样测试再怎么折腾（写假密钥、传文件）都碰不到真实演示数据。
DATA_DIR = os.environ.get("CAMPUSTIME_DATA_DIR") or os.path.join(os.path.dirname(__file__), "data")


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def _read(name: str, default):
    """读一个 JSON 文件；没有就返回默认值（人话：拿不到就给个空列表）。"""
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _write(name: str, data) -> None:
    """写一个 JSON 文件（人话：把数据落盘）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_message(module: str, message: str, answer: str) -> dict:
    """记一条学生消息（人话：学生说了啥、在哪个模块、管家怎么答的，存进收件箱）。"""
    msgs = _read("student_messages.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "module": module or "",
        "message": message,
        "answer": answer or "",
    }
    msgs.append(item)
    _write("student_messages.json", msgs)
    return item


def list_messages() -> list:
    """列出学生消息，倒序（最新在前），方便管理员看。"""
    return list(reversed(_read("student_messages.json", [])))


def add_notice(title: str, content: str, attachment: dict | None = None) -> dict:
    """发一条通知（人话：管理员写一条通知，存进通知表）。

    :param attachment: 可选的附件信息，形如 {"id": "...", "name": "xxx.pdf"}
                       （人话：通知可以挂个文件，比如放假安排表）
    """
    ns = _read("notices.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "title": title,
        "content": content,
        "attachment": attachment,  # 没有附件时就是 None，前端按字段判断即可
    }
    ns.append(item)
    _write("notices.json", ns)
    return item


def list_notices() -> list:
    """列出通知，倒序。"""
    return list(reversed(_read("notices.json", [])))


# ============ 知识库管理（管理员零代码维护，faq 模块直接吃这份数据） ============

def list_kb() -> list:
    """列出知识库全部条目。"""
    return _read("kb.json", {"entries": []}).get("entries", [])


def add_kb_entry(question: str, answer: str, keywords: list) -> dict:
    """新增一条知识库条目（人话：管理员在后台加一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    item = {"question": question, "answer": answer, "keywords": keywords or []}
    entries.append(item)
    _write("kb.json", data)
    return item


def update_kb_entry(index: int, question: str, answer: str, keywords: list) -> dict:
    """按序号修改一条知识库条目（人话：管理员编辑一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    if index < 0 or index >= len(entries):
        raise IndexError("知识库条目不存在")
    entries[index] = {"question": question, "answer": answer, "keywords": keywords or []}
    _write("kb.json", data)
    return entries[index]


def delete_kb_entry(index: int) -> bool:
    """按序号删除一条知识库条目（人话：管理员删掉一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    if index < 0 or index >= len(entries):
        return False
    entries.pop(index)
    _write("kb.json", data)
    return True


# ============ 客服人格配置（管理员配置，驿站/商户客服模块吃这份数据） ============

# 默认人格：管理员没配过时先用这个
DEFAULT_PERSONA = {
    "name": "小园",
    "role": "校园驿站客服助手",
    "tone": "热情、耐心、亲切，像邻家学姐",
    "rules": "回复简洁；涉及取件码/物流以台账为准，不编造；遇到不会的引导到驿站前台。",
}


def get_persona() -> dict:
    """读取当前客服人格（人话：驿站客服用这个语气说话）。"""
    return _read("persona.json", DEFAULT_PERSONA)


def set_persona(name: str, role: str, tone: str, rules: str) -> dict:
    """保存客服人格（人话：管理员改了客服的人设，下次对话立即生效）。"""
    p = {"name": name, "role": role, "tone": tone, "rules": rules}
    _write("persona.json", p)
    return p


# ============ 运行时设置（管理台零代码配置的 AI 接口参数） ============
# 注意：这个文件里可能有真实的 API Key，所以它必须放进 .gitignore，
# 并且读取给前端看的时候要脱敏（由 main.py 负责掩码）。

def get_settings() -> dict:
    """读取管理台保存的设置（人话：管理员在后台填的 AI 接口参数）。

    返回空字典表示"管理员没在后台配过"，此时 config 层会回落到 .env。
    """
    return _read("settings.json", {})


def set_settings(data: dict) -> dict:
    """保存管理台设置（人话：把管理员填的接口参数存档，config 层每次会来读）。

    用"增量更新"而不是整体覆盖：管理员只想改模型名时，不至于把 Key 冲掉。
    """
    current = get_settings()
    # 空字符串视为"没填"，不落盘，这样它会自动回落到 .env 的默认值
    for k, v in data.items():
        if isinstance(v, str) and not v.strip():
            current.pop(k, None)
        else:
            current[k] = v
    _write("settings.json", current)
    return current


# ============ 上传文件（给 AI 助手读的资料） ============
# 正文存在 uploads/<id>.txt，元信息存在 uploads.json。
# 分开存是为了让 uploads.json 保持能人肉阅读，不会被大段正文撑爆。

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 单个文件上限 5MB，防止演示机被撑爆


def _upload_body_path(fid: str) -> str:
    """提取出来的文字放在这里（AI 读的就是这份）。"""
    return os.path.join(UPLOAD_DIR, fid + ".txt")


def save_upload(filename: str, content: str, raw: bytes | None = None) -> dict:
    """保存一个上传文件（人话：既留原件供下载，也提取文字给 AI 读）。

    为什么要存两份？
    - 原件：通知的附件要能让学生下载回去（丢了就只剩摘要，体验不完整）
    - 文字：AI 真正能"看懂"的形态

    :param raw: 文件原始字节；不传就只有文字、没有原件可下载
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fid = uuid.uuid4().hex[:12]
    # 1) 提取出的文字
    with open(_upload_body_path(fid), "w", encoding="utf-8") as f:
        f.write(content)
    # 2) 原件（保留原始后缀，下载回去才打得开）
    ext = os.path.splitext(filename or "")[1].lower()
    if raw is not None:
        with open(os.path.join(UPLOAD_DIR, fid + ext), "wb") as f:
            f.write(raw)

    item = {
        "id": fid,
        "name": filename,
        "size": len(raw) if raw is not None else len(content.encode("utf-8")),
        "ext": ext,
        "has_raw": raw is not None,
        "ts": _now(),
        "chars": len(content),
        "preview": content[:200],  # 列表页给个预览，不用真的再读一遍文件
    }
    data = _read("uploads.json", {"files": []})
    data.setdefault("files", []).append(item)
    _write("uploads.json", data)
    return item


def list_uploads() -> list:
    """列出所有上传文件，倒序（最新在前）。"""
    return list(reversed(_read("uploads.json", {"files": []}).get("files", [])))


def get_upload(fid: str) -> str | None:
    """读某个上传文件提取出的文字；找不到返回 None。"""
    try:
        with open(_upload_body_path(fid), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def get_upload_meta(fid: str) -> dict | None:
    """查某个文件的元信息（名字/后缀等），下载原件时要靠它还原文件名。"""
    for f in _read("uploads.json", {"files": []}).get("files", []):
        if f.get("id") == fid:
            return f
    return None


def get_upload_raw_path(fid: str) -> str | None:
    """取原件在磁盘上的路径；没有原件（比如只有文字）或文件不存在时返回 None。"""
    meta = get_upload_meta(fid)
    if not meta or not meta.get("has_raw"):
        return None
    p = os.path.join(UPLOAD_DIR, fid + (meta.get("ext") or ""))
    return p if os.path.exists(p) else None


# ============ 学生个人库：周表（课程）+ 待办 + 会话历史 ============
# 单独一个 student/ 目录，是因为这属于"某个学生的个人数据"，
# 跟上面的公共知识库、站点台账性质不同，以后做多用户时天然按用户隔离。

STUDENT_DIR = os.path.join(DATA_DIR, "student")

# 会话历史最多保留多少条消息（一问一答算两条）。
# 留太少会记不住刚才商量好的安排，留太多又白烧 token，20 条够用。
MAX_HISTORY = 20


def _spath(name: str) -> str:
    """学生个人库里某个文件的路径。"""
    return os.path.join(STUDENT_DIR, name)


def _sread(name: str, default):
    """读学生个人库里的一个 JSON 文件。"""
    try:
        with open(_spath(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _swrite(name: str, data) -> None:
    """写学生个人库里的一个 JSON 文件。"""
    os.makedirs(STUDENT_DIR, exist_ok=True)
    with open(_spath(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---- 周表：学生每周固定的课程 ----

def get_timetable() -> list:
    """读周表全部课程（人话：这周哪天第几节课上什么）。

    每条形如 {"day": 1, "start": "08:00", "end": "09:40",
              "course": "高等数学", "location": "教三301"}
    day 用 1=周一 … 7=周日。
    """
    return _sread("timetable.json", {"courses": []}).get("courses", [])


def get_timetable_data() -> dict:
    """读周表整份数据（含"上次更新时间"，前端要显示课表是什么时候导入的）。"""
    return _sread("timetable.json", {"updated_at": "", "courses": []})


def save_timetable(courses: list) -> dict:
    """整体覆盖保存周表（人话：学生在聊天里传课表后，AI 整理好存进来）。"""
    data = {"updated_at": _now(), "courses": courses}
    _swrite("timetable.json", data)
    return data


# ---- 待办：被安排进具体日期和时间段的任务 ----

def add_todo(title: str, date: str, start: str, end: str,
             note: str = "", category: str = "") -> dict:
    """新增一条待办（人话：把商量好的安排写进日程）。

    :param date: 日期，形如 "2026-09-25"
    :param start / end: 起止时间，形如 "14:00" / "15:30"
    """
    data = _sread("todos.json", {"todos": []})
    item = {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "date": date,
        "start": start,
        "end": end,
        "note": note,
        "category": category,
        "status": "planned",       # planned（待办） / done（已完成）
        "created_at": _now(),
    }
    data.setdefault("todos", []).append(item)
    _swrite("todos.json", data)
    return item


def list_todos(date: str | None = None) -> list:
    """列出待办；给了 date 就只看那一天，自动按开始时间排序。"""
    todos = _sread("todos.json", {"todos": []}).get("todos", [])
    if date:
        todos = [t for t in todos if t.get("date") == date]
    return sorted(todos, key=lambda t: (t.get("date", ""), t.get("start", "")))


def list_todos_in_month(prefix: str) -> dict:
    """按日期聚合某个月的待办（人话：给月视图算每天的待办数量）。

    :param prefix: 形如 "2026-09"，按日期字符串前缀匹配
    :return: {"2026-09-24": [待办1, 待办2], ...}
    """
    grouped: dict[str, list] = {}
    for t in list_todos():
        d = t.get("date", "")
        if d.startswith(prefix):
            grouped.setdefault(d, []).append(t)
    return grouped


def get_todo(tid: str) -> dict | None:
    """按 id 查一条待办。"""
    for t in _sread("todos.json", {"todos": []}).get("todos", []):
        if t.get("id") == tid:
            return t
    return None


def update_todo(tid: str, patch: dict) -> dict | None:
    """改动一条待办的部分字段（比如把状态改成 done）。"""
    data = _sread("todos.json", {"todos": []})
    for i, t in enumerate(data.get("todos", [])):
        if t.get("id") == tid:
            t.update(patch)
            data["todos"][i] = t
            _swrite("todos.json", data)
            return t
    return None


def delete_todo(tid: str) -> bool:
    """删一条待办。"""
    data = _sread("todos.json", {"todos": []})
    todos = data.get("todos", [])
    keep = [t for t in todos if t.get("id") != tid]
    if len(keep) == len(todos):
        return False
    data["todos"] = keep
    _swrite("todos.json", data)
    return True


# ---- 会话历史：让 Agent 记得住上一轮商量到哪了 ----

def get_conversation(sid: str) -> list:
    """读某个会话的历史消息（只返回最近 MAX_HISTORY 条）。"""
    data = _sread("sessions.json", {"sessions": {}})
    msgs = data.get("sessions", {}).get(sid, [])
    return msgs[-MAX_HISTORY:]


def append_conversation(sid: str, role: str, content: str) -> None:
    """往会话里追加一条消息。"""
    data = _sread("sessions.json", {"sessions": {}})
    sessions = data.setdefault("sessions", {})
    sessions.setdefault(sid, []).append({"role": role, "content": content})
    # 只留最近这些条，防止文件无限长大
    sessions[sid] = sessions[sid][-MAX_HISTORY:]
    _swrite("sessions.json", data)


def clear_conversation(sid: str) -> None:
    """清空某个会话（人话：前端需要"重新开始一段对话"时用）。"""
    data = _sread("sessions.json", {"sessions": {}})
    data.get("sessions", {}).pop(sid, None)
    _swrite("sessions.json", data)
