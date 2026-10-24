"""校园问答 FAQ 模块（人话：把分散的校园信息收口到一个知识库，学生问啥先来这里找答案）。

它给 Agent 一个工具：search_kb（在知识库里检索）。
Agent 拿到学生问题 → 调 search_kb 找相关条目 → 用自然语言组织成清楚回答。
这正是"收口分散校园信息"这个卖点的落地：规则、地点、流程都集中维护，改一处全站生效。

⚠️ #40 补2 起，`search_kb` 查的是**两部分**：`kb.json` 里那些一问一答的条目，
加上管理员上传的整份文档（`files.doc_hits` 在切好的块里检索）。合在一个函数里而不是两个工具，
理由很实在：模型在学生端只会调 `search_kb` 这一个名字 —— 开两个入口时它只调前者，
于是"文档里明明写着图书馆在哪，问答却说知识库没有"（真机复现过）。
"""
import asyncio
import json
import re

from app.agent.tools import Tool
from app.store import read_data_file, kb_head_words, kb_expand, clean_expand_words

MODULE_KEY = "faq"

SYSTEM_PROMPT = """你是校园时间管家学生端的「校园问答」助手。
你掌握一份校园知识库（图书馆、食堂、校车、校医院、宿舍规定、奖学金、网络等）。
⚠️ 这份知识库由**两部分**组成：管理员维护的问答条目，**以及管理员上传的文档**（学生手册、规定汇编等）。
所以不要因为"问答条目那部分是空的"就说"知识库没有这个信息" —— `search_kb` 一次把两部分都查。
工作原则：
1. 学生问校园相关问题，先调用 search_kb 在知识库里检索（它**同时**查问答条目和上传文档）；
2. 找到相关内容，用自然语言组织成清楚、友好的回答，可补充一点贴心提示；
   引用文档里的内容时说清出自哪份文件（它会标"《文件》第 N 块"），别把原文整段贴给学生；
   ⚠️ 检索结果**可能来自多份文档**（一份写通则、另一份写例外或补充）：要把几份的内容**合起来**答、
   分别标出处；两份说法不一致时（一份写 22:30 闭馆、另一份写 23:30）要把两种都讲出来并说清各自条件，
   **不要擅自挑一个当准** —— 只答一半和答错一样糟；
3. search_kb 说"没有找到"时，先想想是不是**换个说法**再查更靠得住（词法检索认字面，
   问"清场"查不到写"闭馆"的那条）：可以换一组关键词**再调一次 search_kb**；
   仍然没有就诚实回"这个我暂不清楚"，并建议去对应部门或官网查询，绝对不要编造；
   —— 但**不许换别的工具**去猜：它没查到就是两边都没查到（问答条目和上传文档它一次都翻了）。
4. 用中文，语气亲切。"""


def _load() -> dict:
    """读取知识库（人话：把 kb.json 变成字典）。

    ⚠️ 路径**每次现算**，不在导入时拼死（红线 23 那一族的最后一处）：
    以前这里是 `_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "kb.json")`
    + `open(_DATA_PATH)`，于是沙箱换了 `CAMPUSTIME_DATA_DIR` 它照样读仓库里那份 ——
    "问答条目 + 上传文档都命中"这类断言根本没法自种（只能拿用户今天手测到什么当起点）。
    `schedule.py` 里 `courses.json` 还是那个旧写法，本单没顺手改（同一族、下一单处理）。
    """
    data = read_data_file("kb.json", {"entries": []})
    entries = data.get("entries") if isinstance(data, dict) else None
    return {"entries": entries} if isinstance(entries, list) else {"entries": []}


def _kb_fuzzy_hit(q: str, kw: str) -> bool:
    """关键词**打错一个字**也算命中（人话：「图收馆」也能找到"图书馆"）。

    做法：在问句里按关键词的长度滑一遍窗口，只要有一处跟关键词相差 ≤1 个字就算命中。
    ⚠️ 只补在精确匹配**落空**之后，且只差一个字（长词两个），不搞"最像的那条"排序猜测。

    ⚠️⚠️ **两个字的关键字不许模糊匹配**（`n < 3` 直接放弃）。原因很实在：
    中文两字词差一个字就是**另一个词**——「校车」和「校长」编辑距离也是 1，
    结果"校长办公室电话多少"会翻出校车时刻表（真踩过，学生问校长电话，
    屏幕冒出校车发车时间）。三个字差一个（「图收馆」→「图书馆」）才像手误。
    """
    from app.modules.planner import _lev       # 放到函数里导入，免得模块互相套住

    kw = kw.lower()
    n = len(kw)
    # 两个字太短，一个字之差就是另一个词（校车/校长）→ 不猜
    if n < 3:
        return False
    tol = 1 if n <= 4 else 2
    for i in range(len(q) - n + 1):
        win = q[i:i + n]
        if _lev(win, kw) > tol:
            continue
        sw, sk = set(win), set(kw)
        if (2 * len(sw & sk) / (len(sw | sk) or 1)) >= 0.5:
            return True
    return False


# ⚠️ #42-B 起它只卡**学生那一路**（日程栏直答气泡），不再卡模型那一路：
#    以前 `_kb_parts` 先按它砍一刀，于是 `search_kb` 最多只拿到两块 —— 实测问「补办学生证多少钱」，
#    两块全是同一份手册的重复章节，另一份《资助细则》里"困难生免收"那条例外一个字都没进上下文。
#    模型那一路的预算改由 `files.DOC_TOP_K`（4）管，两边共用 `files.pick_diverse` 的来源配额。
FAQ_DOC_HITS = 2        # 学生直答气泡里最多几块文档段落（不经模型，太长没法看）
FAQ_DOC_CHARS = 320     # 每块最多留多少字

# ⚠️ 这句 miss 文案是**一份契约**：`main._kb_has_answer` 要判"知识库里到底有没有"，
#    `test_15` 的那组断言也按它认。要改字眼，连着改那边（`KB_MISS_PREFIX` 是唯一的这一处）。
KB_MISS_PREFIX = "知识库里没有找到"
_MISS = KB_MISS_PREFIX + "和「"


def _entry_kws(item: dict, typo: bool = False) -> list:
    """这条问答条目**实际拿来匹配的词**（#46-A 两处接上的第二处：查的时候也兜底）。

    ⚠️ 为什么不只接 `store._kb_row` 那一处：老数据里那批"关键词留空"的死条目**不用迁移**，
    当场就能活过来 —— 存量条目表里 question 有内容、keywords 是空数组的那些，
    管理员不会去重存一遍，只接"存"那半等于只救以后加的。
    ⚠️ **精确与"打错字"那两圈都要走这里**：施工单只点了精确那一处（`:107`），
    可 `:113` 那圈同样只看 `keywords` —— 空关键词的条目在两圈里都是死的，
    改一处等于没改（"图收馆几点关门"那种就落在第二圈）。

    三档各是什么：
    - **管理员手写的关键词**：两圈都按他写的样子原样用。⚠️ 不补片段 —— 给他补上 3 字片段
      等于代码悄悄把「校园网密码」这条放宽到任何带「校园网」的问句，那是覆盖他的判断（施工单 §一 明令不许）。
    - **派生出来的词片**（关键词为空、或存的就是 `kb_head_words` 那几个）：精确那圈补片段（`kb_expand`）——
      换说法也要能命中（「考试周图书馆几点关」里没有整片「考试周图书馆开到」，靠的就是补出的「图书馆」）；
      打错字那圈**只用词片本身**：滑窗碎片是 n-gram 噪声，给它编辑距离容错实测会把「今天有什么课」
      按「今天星」接走，一句日程问话被直答成「看日历」（`_kb_fuzzy_hit` 那条「校车 vs 校长」老坑的新来源）。
    """
    hand = [str(k).strip().lower() for k in (item.get("keywords") or []) if str(k).strip()]
    q = str(item.get("question") or "")
    heads = [str(k).lower() for k in kb_head_words(q)]
    # 「派生词」有两种样子（#46-I 那件没落地的账，本轮补上）：关键词为空，
    # 或者**存进去的就是 `kb_head_words` 那几个** —— 后者是 `_kb_row` 兜底的必然结果：
    # 「游泳馆价格」这种没有虚词可劈的短语，兜底出来就是**原句整串**。
    # 不认这一支，这条就被当成"管理员手写"，享受不到 `kb_expand` 的 3 字/2 字滑窗，
    # 实测「游泳多少钱」「游泳多少钱一次」直接查不到（改前查不到的正是这两句）。
    # ⚠️ 两边都必须 `lower()` 后再比：`hand` 已经小写过，右边不小写就永远不相等 ——
    #    那不会报错，只会让这一支**静默失效**，表现与没改一模一样（变异 n2 钉的就是它）。
    # ⚠️ 判据是整表 `==`，不是"派生词是手写的子集"：管理员手写 `["游泳馆","价格"]` 时
    #    他的判断优先，一个字都不许代码替他扩。
    auto = (not hand) or hand == heads
    if hand and not auto:
        return hand
    return heads if typo else kb_expand(heads)


def _merged(query: str, extra: str = "") -> str:
    """检索用的那一份：**原句在前**，模型补的新词接在后面（#57-A）。

    为什么原句必须拼在前面、而不是"用模型改写的结果替换原句"：
    施工单 §2.2 规则 A（原句里的实质词全部保留）靠 prompt 去请求是不可靠的 ——
    模型一旦漏抄一个实词，那一个词就再也匹配不上了。原句在前，等于把这条
    从"请求模型照做"变成**结构上只能做加法**：新词只会多、一个都不会少。
    反过来若让改写覆盖原句，那就不再是"补召回"，是"换了一个问题去查"。

    ⚠️ 这个串**只进匹配**，不进任何给人看的文案：miss 那句回显、条目的 Q/A、
       文档名都还是用原句（见 `search_kb` / `answer_for_student`）。
    """
    e = " ".join(str(extra or "").split())
    return f"{query} {e}" if e else str(query or "")


def _kb_parts(query: str, extra: str = "") -> tuple[list[dict], list[dict]]:
    """两路检索的**共同那一步**：返回（问答条目命中, 文档命中）。

    渲染留给两个视图各自做（`search_kb` 给模型、`answer_for_student` 给学生，#41-A），
    判空也只看着这两个列表 —— 不许去嗅任何人话文案（施工单 §2.1 那版嗅 `search_docs`
    的文案，漏了"空查询"那一支，变异 M5 就是验这颗牙）。

    :param extra: 模型为这句问话补的**检索用新词**（空格分隔，#57-A）。
       默认空 = 与没有这个功能时逐字相同（施工单 §五.4：不落库、不改判据，只改喂进去的词）。
       ⚠️ 它**只进条目那一路**；文档那一路照旧只看原句，理由在下面 `doc_hits(q_raw)` 那条 ⚠️⚠️。
    """
    from app.modules.files import doc_hits
    # 函数内导入，不是顶层：faq 与 files 顶层互相 import 会套住（planner._lev 同一写法）。
    # 方向是 faq → files 单向，files 只依赖 store，不成环（K5 钉着）。

    data = _load()
    q = _merged(query, extra).lower()
    q_raw = (query or "").lower()
    hits = []
    for item in data["entries"]:
        # 中文没有空格，最稳的办法是：知识库条目的"关键词"里，有没有出现在用户问题里
        # （关键词为空时 `_entry_kws` 会退回从问题原文派生 —— 见 #46-A）
        keywords = _entry_kws(item)
        if any(kw and kw in q for kw in keywords):
            hits.append(item)
    # ★ 精确匹配落空 → 再按"打错字"找一遍（"图收馆几点关门" 也要能翻到图书馆那条）
    #   ⚠️ 这一圈**只看学生原句**（`q_raw`），不吃补出来的词（#57-A）：
    #   这一圈的判据是"原句里有没有一处跟关键词只差一个字" —— 那是**手误**。
    #   模型补的同义词不是手误，拿它们去滑窗容错，等于给改写结果再叠一层模糊放宽：
    #   草堆变长 = 窗口变多 = 蹭上的机会变多，而补的词本来就多是 2~3 字的短词。
    if not hits:
        for item in data["entries"]:
            keywords = _entry_kws(item, typo=True)
            if any(kw and _kb_fuzzy_hit(q_raw, kw) for kw in keywords):
                hits.append(item)

    # 文档那一路：只在**真命中**时才有内容（空查询/没文档/没命中都回空表）。
    # 这里**不做块数裁剪**（#42-B）：以前顺手 `[:FAQ_DOC_HITS]` 一刀，把"学生气泡别太长"
    # 的限制加到了模型那一路头上 —— 两份文档才答得全的问题，模型只看见先挤进来的那一份。
    # 各视图自己的预算在渲染时给（模型 = `files.DOC_TOP_K`，学生 = `FAQ_DOC_HITS`），
    # 选块统一走 `files.pick_diverse`（同一份不超过它两块，保证每份相关文档都发声）。
    #
    # ⚠️⚠️ 这一路**不吃补词**（`doc_hits(q_raw)`，不是 `doc_hits(q)`）—— 2026-10-24 真机量出来的：
    #   真实数据里 30 个块没有一处含「寒假/放假/开学/校历」，学生问「寒假什么时候放假」本该
    #   老实说"没查到"；补词「放假时间 寒假时间」进到 BM25 之后，贡献的是 **「时间」这个二元组**
    #   —— 它到处都是 ⇒ 一块讲**补考周与考试周闭馆**的段落得分 >0 被选中，气泡就成了
    #   「《…规定》里是这么写的：考试周结束后第一周为补考周…」。**答非所问，比"没查到"更糟。**
    #   条目那一路没这个问题：它是 `关键词 in 问话` 的字面闸，补进来的必须是某个条目**写着的**
    #   那串字才算数（泳池→「游泳馆」），不存在"沾一个通用字就上榜"这条路。
    #   ⇒ 补词只补条目那一路。施工单 §一 说"两路同时受益"，这一半被实测否证（记在偏离单）。
    return hits, doc_hits(q_raw)


def search_kb(query: str) -> str:
    """工具：在**校园知识库**里检索 —— 问答条目 + 管理员上传的文档，一个入口查全。

    这是**给模型看的那一份**（带"哪份文件第几块、原文第几字起、相关度"这些定位元数据）。
    不经模型、直接贴给学生的那一路请走 `answer_for_student`（#41-A）。

    为什么要在这一个函数里合两路（#40 补2）：学生端/日程栏的链路只要求模型调 `search_kb`，
    它不会想到还有第二个工具。两边各开一口时，模型只会调前者 ——
    真机复现：《学生手册》里明写「图书馆位于校园中轴北侧」，问答却回"知识库没有图书馆的信息"
    （那阵子 kb.json 恰好是空的，于是文档那条路根本没人走）。
    **kb.json 空不空不是关键，入口不合并这个洞就一直在。**

    ⚠️ 这一路**不做查询改写**（#57-A 有意收窄，与直答那两档不同）：它在 ReAct 循环里面，
       模型手上已经有 `search_kb` 这个工具，查不到时它会**带着整轮上下文**换词再调一次
       （系统提示词第 3 条明令它这么做）—— 那比我在这里盲扩一次更准；
       而每次工具调用再加一跳模型请求，是把首字延迟往后再推 1.5 秒。
    """
    from app.modules.files import format_doc_hits

    hits, docs = _kb_parts(query)

    if not hits:
        if not docs:
            return _MISS + query + "」直接相关的内容。"
        return ("问答条目里没有直接对应的问题，但管理员上传的文档里有这些段落：\n\n"
                + format_doc_hits(docs, per_chunk=FAQ_DOC_CHARS, query=query))

    lines = ["在知识库中找到以下相关内容（问答条目）："]
    for h in hits[:3]:  # 最多给 3 条，避免信息过载
        lines.append(f"Q: {h['question']}\nA: {h['answer']}")
    if docs:
        # 条目命中也附上文档：手册里那一段往往比旧条目更细（"共六层、借二十册、30 天"这类）。
        # 但**小标题必须写清来源**：条目是管理员写的标准答案、文档是原文摘录，
        # 混成一样的 Q/A 形状，模型就会把摘录当成逐字标准答案去复述。
        lines.append("—— 管理员上传的文档里还有这些段落（原文摘录，出处已标） ——\n"
                     + format_doc_hits(docs, per_chunk=FAQ_DOC_CHARS, query=query))
    return "\n\n".join(lines)


def answer_for_student(query: str, extra: str = "") -> str:
    """日程栏那条「知识库直答」用的**学生视图**（人话：这句话不经模型，屏幕上看到什么就是什么）。

    为什么不能直接复用 `search_kb`（#41-A）：模型视图里那句
    「[1]《学生手册-测试样本.txt》第 1 块（原文第 1 字起，相关度 3.80）」是给模型定位用的元数据，
    原样贴进气泡就成了"系统当着学生的面背数据库"。这里只留《文档名》+ 正文片段，
    出处仍在（学生能顺着文件名去翻），元数据去掉。
    ⚠️ 两边都没有时回的还是那句 `_MISS` 开头的原文 —— 判据嗅的是它（见 `KB_MISS_PREFIX`），
       两个视图不许各写一句"没找到"。

    :param extra: 同 `_kb_parts`（#57-A）。**必须与判空那次传同一个值**，否则
       `kb_has_answer` 说"有"、这里却说"没有找到"—— 那是当学生面前自己打自己（`test_78` C 批钉配对）。
       补进来的词**不参与文档那一路的选块**（只看原句），也不出现在给学生看的文案里。
    """
    from app.modules.files import format_doc_hits_for_student

    hits, docs = _kb_parts(query, extra)
    if not hits and not docs:
        return _MISS + query + "」直接相关的内容。"

    parts = [f"问：{h['question']}\n答：{h['answer']}" for h in hits[:3]]
    if docs:
        # `query=` 传**原句**（不是合并串）：块本身就是按原句选的（见 `_kb_parts` 那条 ⚠️），
        # `_best_window` 只是挑"从哪一句开始截"，跟着选块的那把尺子走才对得上。
        parts.append(format_doc_hits_for_student(docs, per_chunk=FAQ_DOC_CHARS,
                                                 query=query, limit=FAQ_DOC_HITS))
    return "\n\n".join(parts)



def kb_has_answer(query: str, extra: str = "") -> bool:
    """知识库里（问答条目 + 上传文档）**到底有没有**这句的答案。

    #41-B 起，`main._kb_has_answer` 用的就是这一句，判据看的是**两个列表空不空**，
    不再是"回话有没有以那句 miss 开头"。为什么值得单独开一个口子：
    同一判断抄两处、改一处漏一处是这项目反复吃过的（红线 12 那一族）——
    以前 faq 改一次措辞，main 那边就**静默**把"都没找到"当成"有答案"。
    """
    hits, docs = _kb_parts(query, extra)
    return bool(hits or docs)


# ============ 查询改写（#57：没有 embedding，用"补同义词"补召回） ============
# 为什么是"改输入"而不是"换算法"（施工单 §〇 定的两条不动，现场核过）：
#   两路检索都是**把 query 当字符串吃进去**的 —— 文档那路过 `files._tokenize` 再进 BM25，
#   条目那路的判据是 `关键词 in 学生这句话`。在这一步之前把词补上，条目那一路直接受益，
#   而 BM25 的公式、来源配额、条目判据一个字节都不用动。
#   ⚠️ 但**只有条目那一路吃补词**（原因写在 `_kb_parts` 里那条 ⚠️⚠️：真机实测，
#   「寒假什么时候放假」补出「放假时间」后，BM25 认的是 **「时间」这个二元组** ——
#   它到处都是，于是一块讲补考周的段落被拽进气泡，答非所问。施工单 §一 说的"两路同时受益"，
#   这一半被实测否证。）
# 为什么不引 embedding：本作品的口径是"没有 Key 也能演示"（README）+ 零额外依赖，
#   向量化两样都破坏。真要上 hybrid 排序，按 `files.py:76` 预留的骨架做，不在这一单。

QUERY_EXPAND_TIMEOUT = 1.5      # 这一跳的保险丝（秒）：超了就照原句查，绝不把学生拖住
_EXPAND_CACHE_MAX = 128         # 进程内缓存条数（同一句只问一次模型，超了丢最老的）

_EXPAND_CACHE: dict[str, str] = {}

# 提示词正文照施工单 §2.2。四条真机示例（含「图收馆」那条错字）留在里面不是装饰：
# few-shot 是这个"改写器"唯一的规格说明，改它等于改验收口径。
_QUERY_EXPAND_PROMPT = """你是校园知识库的「检索改写器」。你的唯一任务：把学生的一句问话，改写成适合「字面检索（BM25）」的检索词集合。

铁律（违反即失败）：
1. 只做检索改写，不回答问题、不解释、不寒暄、不输出任何校园事实。
2. 不许臆造学生没提到的楼名、金额、日期、人名、部门；只允许「抽取原句实体」和「补同义/别名」。
3. 输出必须是严格 JSON，不要 markdown 代码块，不要任何多余文字。

改写规则：
A. 原句里的实质词全部保留（它们本来就该被检索到）。
B. 为每个概念补 2~3 个同义/别名，因为检索是按字面重合算分的：
   - 场所：图书馆/自习室/阅览室、宿舍/寝室/公寓、食堂/饭堂、驿站/快递站/取件点
   - 动作：补办/遗失/丢了/怎么办、申请/办理/登记、查询/查看
   - 费用：多少钱/收费/费用/价格/工本费
   - 证件/网络：一卡通/校园卡、学生证、校园网/网络/WiFi
   - 部门：教务/教务处、后勤/后勤处、保卫处/安保
C. 口语可归一到档内更正式的说法（「几点关门」→「开放时间 闭馆时间」），但不得改变语义。
D. 不加无区分度的泛词（「学校」「同学」「请问」这类不许加）。
E. 明显的错别字可归一到正确词（「图收馆」→「图书馆」）；拿不准就别改。

输出 JSON（字段固定）：
{
  "is_campus_fact_query": true 或 false,
  "entities": ["核心名词"],
  "synonyms": ["同义或别名"],
  "expanded_query": "用空格分隔的一串检索词"
}
若 is_campus_fact_query=false（问的是日程/闲聊/情绪/报障），则三个数组/串留空。

示例：
输入：补办学生证多少钱
输出：{"is_campus_fact_query":true,"entities":["学生证","补办","工本费"],"synonyms":["遗失","丢了","学生证补办","收费","费用"],"expanded_query":"补办 学生证 工本费 遗失 丢了 收费 费用"}

输入：图书馆几点关门
输出：{"is_campus_fact_query":true,"entities":["图书馆"],"synonyms":["自习室","阅览室","开放时间","闭馆时间"],"expanded_query":"图书馆 几点 关门 自习室 阅览室 开放时间 闭馆时间"}

输入：我的快递在哪取
输出：{"is_campus_fact_query":true,"entities":["快递","取件"],"synonyms":["驿站","取件点","包裹","取件码"],"expanded_query":"快递 取件 在哪 驿站 取件点 包裹 取件码"}

输入：图收馆在哪个楼
输出：{"is_campus_fact_query":true,"entities":["图书馆"],"synonyms":["自习室","阅览室","位置","在哪"],"expanded_query":"图书馆 在哪个楼 位置 在哪 自习室 阅览室"}

输入：明天上午有什么课
输出：{"is_campus_fact_query":false,"entities":[],"synonyms":[],"expanded_query":""}
"""


def _json_obj(raw: str) -> dict | None:
    """模型回话里抠出那一个 JSON 对象；抠不出来回 None（当失败，不猜）。

    ⚠️ 全仓这已经是第三处同形解析（`main._extract_json_obj`、判定层 `intent` 各一份）。
    不复用它们的，两个理由都是硬的：`app.main` 顶层就 import 了 faq（`main.py:65`），
    faq 反向 import main 是循环导入；intent 那份绑着判定层的意图表，不是通用工具。
    真要收成一处得先动路由或判定层 —— 超出本单"只动输入"的边界，已记进快照的遗留账。
    """
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


async def query_expansion(query: str, override: dict | None = None) -> str:
    """学生的一句问话 → 适合字面检索的**新词**（空格分隔；没得补就回空串）。

    空串 = "这次没扩"，调用方拿原句照查 —— 它**不是错误**（施工单 §七.2：
    改写是增强项，不许变成新的故障点）。所以这函数没有一条路会抛：
      没配 Key / 超时 / 上游报错 / 回的不是 JSON / JSON 里没 expanded_query
      / 拆出来的词全被闸丢掉 —— 一律静默回空串，检索结果与"没这个功能"逐字相同。

    :param override: 学生端自带的 API 配置（与润色、判定层同一口径：自带的优先，
                     用量流水里 `key_source` 才分得清花的是谁的钱）。
    """
    key = (query or "").strip().lower()
    if not key:
        return ""
    if key in _EXPAND_CACHE:
        return _EXPAND_CACHE[key]
    try:
        from app.agent.polish import llm_available    # 函数内 import：看得到测试补丁
        if not llm_available(override):
            return ""        # 没 Key 连问都不问：离线演示一个字都不变，也不白记一笔 error 流水
        from app.llm.gateway import ModelGateway      # 同上（顶层绑死就打不进补丁，见 polish 的说明）
        gw = ModelGateway(user="内部:检索改写", override=override)
        reply = await asyncio.wait_for(
            gw.chat([{"role": "system", "content": _QUERY_EXPAND_PROMPT},
                     {"role": "user", "content": query}],
                    override=override, max_tokens=120),
            timeout=QUERY_EXPAND_TIMEOUT)
    except Exception:
        # 超时 / LLMError / 任何意外。不缓存 —— 管理员刚配好 Key 时同一句得立刻能用。
        return ""

    raw = reply.get("content") if isinstance(reply, dict) else reply
    extra = " ".join(clean_expand_words(query, _json_obj(str(raw or ""))))
    _EXPAND_CACHE[key] = extra          # 只有真问到模型才落缓存（含"问到了但确实没得扩"）
    while len(_EXPAND_CACHE) > _EXPAND_CACHE_MAX:
        _EXPAND_CACHE.pop(next(iter(_EXPAND_CACHE)))
    return extra


def build_tools() -> dict[str, Tool]:
    """把 search_kb 包装成 Agent 能调用的工具。"""
    return {
        "search_kb": Tool(
            name="search_kb",
            description=(
                "在校园知识库检索答案。这一处**同时查**管理员维护的问答条目与管理员上传的文档"
                "（手册/规定等），所以条目表为空不代表知识库没有答案，不用再换别的工具查一遍。"
                "入参 query 是学生的问题或关键词，如 '图书馆几点关门'。"
                "⚠️ 返回的段落**可能来自多份文档**（一份写通则、另一份写例外或补充）："
                "要把几份的内容合起来回答，并分别说清出自哪份文件；两份不一致时把两种都讲出来"
                "并说明各自条件，不要擅自选一个。"
                "一次只查到一半信息时，可以**换一组关键词再调一次本工具**"
                "（比如把「清场」换成「闭馆」）—— 换工具不必，换词可以。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "学生的问题或关键词"}
                },
                "required": ["query"],
            },
            func=search_kb,
        ),
    }
