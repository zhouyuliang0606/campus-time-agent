"""简单文件存储（人话：把学生消息和通知存成 JSON 文件，管理员端能读、能发）。

为什么用文件而不是数据库？演示阶段要的就是"看得懂、改得动"，
每个文件就是一张表：student_messages.json = 学生消息表，notices.json = 通知表。
真上线时把这里换成数据库即可，接口不变。
"""
import datetime
import json
import os
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


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


def add_notice(title: str, content: str) -> dict:
    """发一条通知（人话：管理员写一条通知，存进通知表）。"""
    ns = _read("notices.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "title": title,
        "content": content,
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
