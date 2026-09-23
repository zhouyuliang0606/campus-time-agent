"""上传文件解析（人话：把用户传上来的各种格式，统一变成"一串 AI 能读的纯文字"）。

为什么单独拎成一个文件？
因为"读文件"这件事跟"存文件"（store.py）、"对外接口"（main.py）都不是一回事，
单独放一层，以后想加 Word/Excel 支持，只改这里就行，别的地方不用动。
"""
import io
import os

# 明确支持的纯文本后缀（照 md/csv 这类直接按文本读就行）
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".log",
    ".yml", ".yaml", ".xml", ".html", ".htm", ".ini", ".cfg",
    ".py", ".js", ".java", ".c", ".cpp", ".sql", ".rst",
}


class UploadError(Exception):
    """上传文件处理失败（人话：这文件搞不定，原因写给用户看）。"""


def _ext(name: str) -> str:
    """取文件名后缀，统一转小写方便比较。"""
    return os.path.splitext(name or "")[1].lower()


def ext_supported(name: str) -> bool:
    """这个文件后缀咱们能不能处理（PDF 和上面那堆文本类都可以）。"""
    return _ext(name) in TEXT_EXT or _ext(name) == ".pdf"


def supported_desc() -> str:
    """给前端/报错文案用的一句话说明。"""
    return "txt / md / csv / json / log 等文本文件，以及 PDF"


def extract_text(filename: str, data: bytes) -> str:
    """把一个上传文件变成纯文本。

    :param filename: 原始文件名（靠后缀判断怎么解析）
    :param data: 文件原始字节
    :return: 提取出的文字
    """
    if _ext(filename) == ".pdf":
        text = _pdf_text(data)
    else:
        text = _plain_text(data)

    text = text.strip()
    if not text:
        raise UploadError(
            f"没能从 {filename} 里读到任何文字。如果是扫描版 PDF（整页都是图片），"
            f"需要先做 OCR 识别，本作品暂不支持。"
        )
    return text


def _plain_text(data: bytes) -> str:
    """纯文本文件：按常见中文/国际编码依次试，都失败就忽略非法字符硬解。"""
    for enc in ("utf-8", "gbk", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # 都解不出来也不能让用户卡住，至少把能认的字拿出来
    return data.decode("utf-8", errors="ignore")


def _pdf_text(data: bytes) -> str:
    """PDF：用 pypdf 逐页抽取文字。"""
    try:
        from pypdf import PdfReader
    except ImportError:  # 没装依赖时给个明确指引，别甩一堆看不懂的报错
        raise UploadError("读取 PDF 需要额外依赖，请先执行：pip install pypdf") from None

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)
