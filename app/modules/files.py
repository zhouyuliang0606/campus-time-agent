"""文件助手模块（人话：给 AI 一双"读用户资料"的眼睛）。

上传功能如果只是把文件存起来，那 AI 根本不知道里面写了什么，等于白传。
这个模块提供两个工具，让 Agent 能：
  1. 先看看有哪些上传过的文件（list_uploaded_files）
  2. 再挑一个把内容读进来（read_uploaded_file）

只有"读了真实内容再回答"，才算真的用上了用户上传的资料。
"""
from app.agent.tools import Tool
from app.store import get_upload, list_uploads

MODULE_KEY = "files"

# 读文件时单次最多给模型多少字。
# 设上限是为了防止一个几万字的长文件把上下文和 token 都撑爆。
READ_LIMIT = 6000


def list_uploaded_files() -> list[dict]:
    """列出用户上传过的文件（人话：看看手头有哪些资料可以翻）。

    返回每个文件的 id、文件名、字数、上传时间。要读内容就用返回的 id。
    """
    files = list_uploads()
    # 只给最新的 20 个，避免一次塞太多占位
    return [
        {"id": f["id"], "name": f["name"], "chars": f["chars"], "ts": f["ts"]}
        for f in files[:20]
    ]


def read_uploaded_file(file_id: str) -> str:
    """按 id 读取某个上传文件的全部内容（人话：把这份资料翻开给 AI 看）。

    :param file_id: 文件 id，从 list_uploaded_files 的结果里取
    """
    content = get_upload(file_id or "")
    if content is None:
        return (
            f"找不到 id 为 {file_id} 的文件。请先用 list_uploaded_files 看看"
            f"当前有哪些文件，确认 id 后再读。"
        )
    if len(content) > READ_LIMIT:
        return (
            content[:READ_LIMIT]
            + f"\n\n……（文件较长，已展示前 {READ_LIMIT} 字，全文共 {len(content)} 字）"
        )
    return content


def build_tools() -> dict[str, Tool]:
    """文件读取工具箱。"""
    return {
        "list_uploaded_files": Tool(
            name="list_uploaded_files",
            description=(
                "列出用户已经上传过的文件，返回 id、文件名、字数和上传时间。"
                "当用户提到'我上传的文件/资料/附件/表格/课表'，"
                "或问题明显需要看某个文件才能回答时，先用这个工具确认有哪些文件。"
            ),
            parameters={"type": "object", "properties": {}},
            func=list_uploaded_files,
        ),
        "read_uploaded_file": Tool(
            name="read_uploaded_file",
            description=(
                "按 id 读取某个上传文件的正文内容。"
                "先用 list_uploaded_files 拿到 id，再用这个工具读取。"
                "回答必须基于读到的真实内容，不要凭空猜测文件内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_id": {
                        "type": "string",
                        "description": "文件 id，形如 a1b2c3d4e5f6",
                    }
                },
                "required": ["file_id"],
            },
            func=read_uploaded_file,
        ),
    }
