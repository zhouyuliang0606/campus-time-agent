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
from app.store import (all_chunks, can_read_upload, get_upload, get_upload_meta,
                       list_uploads, visible_uploads)

MODULE_KEY = "files"

# 读文件时单次最多给模型多少字。
# 设上限是为了防止一个几万字的长文件把上下文和 token 都撑爆。
READ_LIMIT = 6000


def list_uploaded_files() -> list[dict]:
    """列出**这个学生自己**上传过的文件（人话：看看手头有哪些资料可以翻）。

    返回每个文件的 id、文件名、字数、上传时间。要读内容就用返回的 id。
    ⚠️ #45-B：以前这里直接 `list_uploads()` ⇒ 学生 2 说一句「我有哪些文件」
    就拿到学生 1 的课表原件 id 和文件名，接一句「读一下它」就是整篇内容。
    """
    files = visible_uploads()
    # 只给最新的 20 个，避免一次塞太多占位
    return [
        {"id": f["id"], "name": f["name"], "chars": f["chars"], "ts": f["ts"]}
        for f in files[:20]
    ]


def read_uploaded_file(file_id: str) -> str:
    """按 id 读取某个上传文件的全部内容（人话：把这份资料翻开给 AI 看）。

    :param file_id: 文件 id，从 list_uploaded_files 的结果里取
    ⚠️ #45-B：入口第一句就判归属，且**不过的时候回"找不到"而不是"无权访问"** ——
    后者等于向对方确认"这个 id 背后确实有一份东西"，那本身就是泄露。
    """
    if not can_read_upload(get_upload_meta(file_id or "")):
        return (
            f"找不到 id 为 {file_id} 的文件。请先用 list_uploaded_files 看看"
            f"当前有哪些文件，确认 id 后再读。"
        )
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
DOC_TOP_K = 4        # 给**模型**最多几块。再多就是把整本手册抄进上下文
                     # （#42-B 把施工单说的"提到 6~8"实测过：加了来源配额之后，4 与 6 在
                     #  「通则+例外」「两份闭馆时间」两类料上选出来的块**一模一样**（脚本
                     #  `probe42b_k.py`）⇒ 这条没采纳。真正的牙在 `pick_diverse` 和
                     #  "模型那一路不再被 `FAQ_DOC_HITS` 先砍到两块"。）
DOC_PER_SOURCE = 2   # 来源配额：同一份文档最多占几块（两份文档才答得全时，别让一份霸榜）
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


def doc_hits(query: str) -> list[dict]:
    """检索知识文档，返回命中的块（按相关度排好，每条 = {fid,name,i,text,start,score}）。

    这是**给程序用的那一份**。`faq.search_kb` 要合并两路检索，它判断的是"有没有查到"，
    所以得拿这个结构化结果去判空 —— 不能去嗅 `search_docs` 那段给人看的话：
    那段一共有三种"没查到"的说法（空查询 / 一份文档都没有 / 有文档但没命中），
    靠子串匹配迟早漏一支（施工单 §2.1 那版就是漏了"空查询"，会把
    「这句里没有能用来检索的词」当成命中拼进回答）。
    """
    q = _tokenize(query)
    if not q:
        return []
    chunks = list(all_chunks())
    if not chunks:
        return []
    scores = _bm25(q, [_tokenize(c["text"]) for c in chunks])
    hits = [(s, c) for s, c in zip(scores, chunks) if s > 0]
    # 平手时：块越靠前、文档越新优先（稳定排序，靠 list_uploads 本来就是新的在前）
    order = {f["id"]: i for i, f in enumerate(list_uploads())}
    hits.sort(key=lambda x: (-x[0], order.get(x[1]["fid"], 1 << 30), x[1]["i"] or 0))
    return [{**c, "score": s} for s, c in hits]


def pick_diverse(hits: list[dict], limit: int, per_source: int = DOC_PER_SOURCE) -> list[dict]:
    """从**已按相关度排好**的块里挑 `limit` 块，但一份文档最多占 `per_source` 席。

    为什么要配额（#42-B，实测不是猜，料就是 `tests/test_70_multi_doc_merge.py` 那份）：
    一份 3796 字 / 5 块的《办事手册》把「补办学生证……缴纳工本费二十元」在六个章节各写一遍，
    一份 905 字 / 2 块的《困难生资助细则》把例外只写在一处 —— 问「补办学生证 工本费」时
    BM25 的全局名次实测是 `手册#2 5.59 / 手册#3 5.34 / 手册#1 5.04 / 手册#4 4.98 / … / 细则#1 第 6 名`，
    **前四名全是同一份手册**：例外那句既进不了学生那两格，也进不了模型那四格
    （`search_kb` 的返回里连「免收」都没有，学生只听到"交二十元"）。
    ⇒ 问题不在"k 太小"，在**k 里没有来源这一维**：把 4 提到 8 也照样被一份啰嗦的手册霸榜，
    同时把预算全喂给重复内容（这一点单独实测过：加配额后 4 与 6 选出的块一模一样）。

    挑法（两遍，都只按名次，不引第二个打分）：
    ① 每份先拿它**最好的一块**，各份按这一块的排名先后排队 —— 保证"每份都发声"；
    ② 预算没花完，再按全局名次往下补次好的块，同一份补到 `per_source` 为止。
    ⚠️ **不硬凑**：只有一份文档相关时结果就只有那一份的块，不会为"看起来有两份"去拉无关文档。
    ⚠️ 也**不自砍**：全场只有一份文档时配额没有对手，这时直接给到 `limit` 块 ——
       硬卡"同一份最多 2 块"会让单文档的问题比改之前**少**拿到两块（实测从 4 掉到 2），
       那是为了一个不存在的竞争者砍掉正主的内容。
    """
    if limit <= 0 or not hits:
        return []
    if len({str(h.get("fid") or h.get("name") or "") for h in hits}) <= 1:
        return list(hits[:limit])
    rank = {id(h): i for i, h in enumerate(hits)}   # 名次按 doc_hits 的原顺序（含它的平手 tie-break）
    firsts: list[dict] = []
    seen: set[str] = set()
    for h in hits:
        fid = str(h.get("fid") or h.get("name") or "")
        if fid in seen:
            continue
        seen.add(fid)
        firsts.append(h)
        if len(firsts) >= limit:
            break
    picked = list(firsts)
    counts = {str(h.get("fid") or h.get("name") or ""): 1 for h in picked}
    picked_ids = {id(h) for h in picked}
    for h in hits:
        if len(picked) >= limit:
            break
        if id(h) in picked_ids:
            continue
        fid = str(h.get("fid") or h.get("name") or "")
        if counts.get(fid, 0) >= per_source:
            continue
        picked.append(h)
        picked_ids.add(id(h))
        counts[fid] = counts.get(fid, 0) + 1
    picked.sort(key=lambda h: rank[id(h)])
    return picked


def _best_window(text: str, query: str, cap: int) -> str:
    """从一块里挑**最贴这个问题的那一句**做起点，往后取 `cap` 个字。

    为什么不是干脆地砍前 cap 字：一块最多六百多字（`CHUNK_SIZE` 600 + 重叠 80），
    答案那句常常在中间 —— 砍前 320 字会把答案剪掉，于是"合并检索"又把答案弄丢了
    （这单要修的正是这类洞）。
    选句子的依据很笨但可核：哪一句跟查询共有的词多，就从哪一句开始；**一样多时选更长的那句**。
    """
    if cap <= 0 or len(text) <= cap:
        return text
    qt = set(_tokenize(query))
    sents = [s for s in re.split(r"(?<=[。！？!?；;\n])", text) if s.strip()]
    best, best_hit = None, 0
    for s in sents:
        n = len(qt & set(_tokenize(s)))
        if n > best_hit:
            best, best_hit = s, n
        elif n == best_hit and n > 0 and len(s) > len(best or ""):
            # 平手时为什么偏向长句：实测问「困难生补办学生证要交多少钱」，
            # 章标题「第 1 章 补办学生证」与答案句「……综合服务大厅申请，缴纳工本费二十元，
            # 五个工作日办结。」跟查询都共有 **4** 个二元组 —— 按先来者赢就停在标题上，
            # 往后 320 字全是垫话，答案那句被截在窗外（选块选对了、摘录却把它剪掉，
            # 等于#42-B 要修的洞只补了一半）。标题不带答案，长句带。
            best = s
    if not best:                       # 一句都不沾词（查询是英文/数字时可能）→ 退回取开头
        return text[:cap]
    start = text.index(best)
    return text[start:start + cap]


def format_doc_hits(hits: list[dict], per_chunk: int = 0, query: str = "",
                    limit: int = DOC_TOP_K) -> str:
    """把命中的块排成"给人/给模型看"的那段文字（带出处）。

    :param per_chunk: 每块最多留多少字，0 = 不截。
        直答那一路（日程栏问校园信息 → 不经模型，把这段原样贴进气泡）必须截，
        不然几块两千多字原文会整块砸进对话气泡里。
    :param limit: 这一路最多给几块，**选块走 `pick_diverse`**（同一份不超过 `DOC_PER_SOURCE` 块）——
        直接 `hits[:limit]` 会让一份啰嗦的手册占满名额，把另一份里的例外那句挤出去（#42-B）。
    """
    lines = ["在文档里找到以下相关段落（按相关度排序，可能来自多份文档，每段都标了出处；"
             "不同文档说法不一致时要把两种都讲出来，不要擅自选一个）："]
    for rank, c in enumerate(pick_diverse(hits, limit), 1):
        body = _best_window(c["text"], query, per_chunk)
        cut = len(body) < len(c["text"])
        lines.append(
            f"[{rank}]《{c['name']}》第 {int(c['i'] or 0) + 1} 块"
            f"（原文第 {int(c['start'] or 0) + 1} 字起，相关度 {c['score']:.2f}）："
            f"\n{body}" + ("\n……（这一段只截了贴问题的那部分，全文用 read_uploaded_file 看这份文件）"
                            if cut else "")
        )
    return "\n\n".join(lines)


def format_doc_hits_for_student(hits: list[dict], per_chunk: int, query: str = "",
                                *, limit: int) -> str:
    """把命中的块排成**给学生看**的那段：只有《文档名》+ 正文片段。

    为什么要第二个视图（#41-A）：日程栏那条「知识库直答」不经模型，
    把 `search_kb` 的返回值**原样贴进气泡**。上面那个模型视图里的
    「（原文第 X 字起，相关度 Y）」「第 N 块」是**给模型定位用的**元数据 ——
    学生看见只觉得系统在背数据库（同 `test_38` 那一族：技术底座不许漏到学生端）。
    文档名要留：学生看得懂，也算出处（他去翻那份文件找得到）。

    :param limit: 学生这一路的块数预算（比模型那一路小得多，气泡得看得完），
        但**配额和模型那一路是同一个函数**：两块也尽量是"两份各一块"，
        而不是同一份手册的头两块（#42-B 实测「补办学生证多少钱」就是这样丢掉例外的）。
    """
    lines = []
    for c in pick_diverse(hits, limit):
        body = _best_window(c["text"], query, per_chunk)
        cut = len(body) < len(c["text"])
        lines.append(f"《{c['name']}》里是这么写的：{body}"
                     + ("……（后面还有，这段只取了贴着你问题的那部分）" if cut else ""))
    return "\n\n".join(lines)


def search_docs(query: str) -> str:
    """工具：在学生上传的文档里检索相关段落（人话：不去翻整本书，只捞出讲这句的那几页）。

    :param query: 学生的问题或其中的关键词
    """
    if not _tokenize(query):
        return "这句里没有能用来检索的词，请换成具体关键词再试（比如「宿舍 限电」「补办 学生证」）。"
    if not list(all_chunks()):
        return (
            "目前还没有可检索的文档（要么没上传过文件，要么这些文件还没建索引）。"
            "可以用 list_uploaded_files 看看有哪些文件；确实没有就照实告诉用户没找到，不要编。"
        )
    hits = doc_hits(query)
    if not hits:
        return (
            f"在上传的文档里没检索到「{query}」相关内容。"
            "请照实说没找到，或建议换个关键词/直接看原文，不要凭常识替这份文件编内容。"
        )
    return format_doc_hits(hits)


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
                "⚠️ 结果**可能来自多份文档**（比如一份写通则、另一份写例外）："
                "回答要把几份的内容合起来说，并分别说清各条出自哪份文件；"
                "两份说法不一致时（一份 22:30 闭馆、另一份 23:30）要把两种都讲出来、说明各自条件，"
                "**不要擅自挑一个当准**。"
                "一次只捞到一半信息，就换一组关键词**再调一次本工具**（词法检索换个说法就可能查不到），"
                "仍然没检索到就照实说没找到，绝不替这份文件编内容。"
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
