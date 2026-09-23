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
