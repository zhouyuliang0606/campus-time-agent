"""文件助手模块（人话：给 AI 一双"读用户资料"的眼睛）。

上传功能如果只是把文件存起来，那 AI 根本不知道里面写了什么，等于白传。
这个模块提供三个工具，让 Agent 能：
  1. 先看看有哪些上传过的文件（list_uploaded_files）
  2. 挑一个把内容读进来（read_uploaded_file）—— 短文件直接读全文
  3. 长文件不去整篇读，而是在切好的块里检索相关那几块（search_docs）

只有"读了真实内容再回答"，才算真的用上了用户上传的资料。
"""
import math
import re

from app.agent.tools import Tool
from app.store import all_chunks, get_upload, list_uploads

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
        # ★ 这里原来是「截断：只给前 READ_LIMIT 字」，那是个真 bug（#40）：
        #   一份几万字的手册，后半本模型一个字都看不见，问到了就照开头那截瞎猜。
        #   现在改指路：长文件要走 search_docs 检索，别整篇读。
        #   ⚠️ 也**不再把开头那一截塞回去** —— 给了前半本反而更糟，
        #      模型会拿开头当全文来回答，比明说"读不下"更容易出错。
        return (
            f"这份文件较长（共 {len(content)} 字，一次读不完），不要再调 read_uploaded_file。"
            f"请改用 search_docs 检索你要问的那一点（文件 id={file_id}，"
            f"检索词用问题里的关键词，比如「宿舍 限电」）。"
        )
    return content


# ============ 文档检索（BM25 + 字符 bigram，不引第三方库） ============
# 为什么要它：上传的是大段文件而不是问答条目，一句"宿舍晚上几点断电"要能在几万字里
# 翻到那一小节。分块已经由 store 在上传时建好（uploads/<fid>.chunks.json），这里只负责找。
#
# ⚠️ 这段是**可替换的骨架**：第二步上 embedding 时，把打分那段换成"BM25 召回 + 向量重排"，
#    search_docs 的入参和返回形状都不用动（护栏 test_66 钉的是形状与"深处查得到"，不是算法）。
DOC_TOP_K = 4        # 最多给模型几块。再多就是把整本手册抄进上下文
BM25_K1 = 1.5        # 词频饱和：一个词出现 20 次不该比出现 2 次高十倍
BM25_B = 0.75        # 长度归一：长块不许光靠"字多、啥词都沾"取胜

# 英文/数字按词切，中文按"连续汉字段"切出来再做二元组
_TOKEN_RE = re.compile(r"[0-9a-z]+|[\u4e00-\u9fff]+")


def _tokenize(s: str) -> list[str]:
    """把一句话切成检索用的词。

    中文没有空格，最省事也最稳的一招是**字符二元组**滑窗："几点关门" → 几点/点关/关门。
    为什么不用 jieba 分词：本作品对评委的口径是"零额外依赖也能跑起来"，
    而二元组对付错别字反而更耐操——错一个字只丢一两个 bigram，不会整词丢掉。

    已知限制照实说：**单个汉字**（"猫"）切不出二元组，这种问法检索不到；
    两个字以上就正常。真遇到该问法，模型会照"没找到"那句回，不会瞎编。
    """
    out = []
    for run in _TOKEN_RE.findall((s or "").lower()):
        if len(run) == 1 or not ("\u4e00" <= run[0] <= "\u9fff"):
            out.append(run)          # 英文单词/数字，或就一个字符
            continue
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _bm25(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """给每篇"词的列表"打 BM25 分（0 分 = 一个词都没沾上）。"""
    n = len(docs)
    if not n:
        return []
    lengths = [len(d) for d in docs]
    avg = (sum(lengths) / n) or 1.0
    # 文档频次：每个词出现在多少篇里 → 决定它值多少钱（到处都是的词不值钱）
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    scores = []
    for d in docs:
        tf: dict[str, int] = {}
        for t in d:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(query_tokens):
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (BM25_K1 + 1)) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / avg))
        scores.append(s)
    return scores


def search_docs(query: str) -> str:
    """工具：在学生上传的文档里检索相关段落（人话：不去翻整本书，只捞出讲这句的那几页）。

    :param query: 学生的问题或其中的关键词
    """
    q = _tokenize(query)
    if not q:
        return "这句里没有能用来检索的词，请换成具体关键词再试（比如「宿舍 限电」「补办 学生证」）。"

    chunks = list(all_chunks())
    if not chunks:
        return (
            "目前还没有可检索的文档（要么没上传过文件，要么这些文件还没建索引）。"
            "可以用 list_uploaded_files 看看有哪些文件；确实没有就照实告诉用户没找到，不要编。"
        )

    scores = _bm25(q, [_tokenize(c["text"]) for c in chunks])
    hits = [(s, c) for s, c in zip(scores, chunks) if s > 0]
    # 平手时：块越靠前、文档越新优先（稳定排序，靠 list_uploads 本来就是新的在前）
    order = {f["id"]: i for i, f in enumerate(list_uploads())}
    hits.sort(key=lambda x: (-x[0], order.get(x[1]["fid"], 1 << 30), x[1]["i"] or 0))
    if not hits:
        return (
            f"在上传的文档里没检索到「{query}」相关内容。"
            "请照实说没找到，或建议换个关键词/直接看原文，不要凭常识替这份文件编内容。"
        )

    lines = ["在文档里找到以下相关段落（按相关度，出自第几块都已标明）："]
    for rank, (score, c) in enumerate(hits[:DOC_TOP_K], 1):
        lines.append(
            f"[{rank}]《{c['name']}》第 {int(c['i'] or 0) + 1} 块"
            f"（原文第 {int(c['start'] or 0) + 1} 字起，相关度 {score:.2f}）："
            f"\n{c['text']}"
        )
    return "\n\n".join(lines)


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
                "按 id 读取某个上传文件的正文内容，适合**短文件**（几千字以内）。"
                "先用 list_uploaded_files 拿到 id，再用这个工具读取。"
                "回答必须基于读到的真实内容，不要凭空猜测文件内容。"
                "文件太长时这个工具不会给你正文，只会让你改用 search_docs —— 别反复调它。"
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
        "search_docs": Tool(
            name="search_docs",
            description=(
                "在用户上传的文档里检索相关段落，返回最相关的几段原文并标明出自哪份文件第几块。"
                "**用户问的是上传文档里的内容时就先用它**（尤其是长文件、"
                "或者你不确定答案在文档的哪一部分），别把整本手册读进上下文再瞎找。"
                "入参 query 用问题里的关键词，如「宿舍 限电」「补办 学生证」。"
                "没检索到就照实说没找到，绝不替这份文件编内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索用的问题或关键词",
                    }
                },
                "required": ["query"],
            },
            func=search_docs,
        ),
    }
