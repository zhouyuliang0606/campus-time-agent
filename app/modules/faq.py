"""校园问答 FAQ 模块（人话：把分散的校园信息收口到一个知识库，学生问啥先来这里找答案）。

它给 Agent 一个工具：search_kb（在知识库里检索）。
Agent 拿到学生问题 → 调 search_kb 找相关条目 → 用自然语言组织成清楚回答。
这正是"收口分散校园信息"这个卖点的落地：规则、地点、流程都集中维护，改一处全站生效。

⚠️ #40 补2 起，`search_kb` 查的是**两部分**：`kb.json` 里那些一问一答的条目，
加上管理员上传的整份文档（`files.doc_hits` 在切好的块里检索）。合在一个函数里而不是两个工具，
理由很实在：模型在学生端只会调 `search_kb` 这一个名字 —— 开两个入口时它只调前者，
于是"文档里明明写着图书馆在哪，问答却说知识库没有"（真机复现过）。
"""
from app.agent.tools import Tool
from app.store import read_data_file

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
#    模型那一路的预算改由 `files.DOC_TOP_K`（6）管，两边共用 `files.pick_diverse` 的来源配额。
FAQ_DOC_HITS = 2        # 学生直答气泡里最多几块文档段落（不经模型，太长没法看）
FAQ_DOC_CHARS = 320     # 每块最多留多少字

# ⚠️ 这句 miss 文案是**一份契约**：`main._kb_has_answer` 要判"知识库里到底有没有"，
#    `test_15` 的那组断言也按它认。要改字眼，连着改那边（`KB_MISS_PREFIX` 是唯一的这一处）。
KB_MISS_PREFIX = "知识库里没有找到"
_MISS = KB_MISS_PREFIX + "和「"


def _kb_parts(query: str) -> tuple[list[dict], list[dict]]:
    """两路检索的**共同那一步**：返回（问答条目命中, 文档命中）。

    渲染留给两个视图各自做（`search_kb` 给模型、`answer_for_student` 给学生，#41-A），
    判空也只看着这两个列表 —— 不许去嗅任何人话文案（施工单 §2.1 那版嗅 `search_docs`
    的文案，漏了"空查询"那一支，变异 M5 就是验这颗牙）。
    """
    from app.modules.files import doc_hits
    # 函数内导入，不是顶层：faq 与 files 顶层互相 import 会套住（planner._lev 同一写法）。
    # 方向是 faq → files 单向，files 只依赖 store，不成环（K5 钉着）。

    data = _load()
    q = (query or "").lower()
    hits = []
    for item in data["entries"]:
        # 中文没有空格，最稳的办法是：知识库条目的"关键词"里，有没有出现在用户问题里
        keywords = [k.lower() for k in item.get("keywords", [])]
        if any(kw and kw in q for kw in keywords):
            hits.append(item)
    # ★ 精确匹配落空 → 再按"打错字"找一遍（"图收馆几点关门" 也要能翻到图书馆那条）
    if not hits:
        for item in data["entries"]:
            keywords = [k.lower() for k in item.get("keywords", [])]
            if any(kw and _kb_fuzzy_hit(q, kw) for kw in keywords):
                hits.append(item)

    # 文档那一路：只在**真命中**时才有内容（空查询/没文档/没命中都回空表）。
    # 这里**不做块数裁剪**（#42-B）：以前顺手 `[:FAQ_DOC_HITS]` 一刀，把"学生气泡别太长"
    # 的限制加到了模型那一路头上 —— 两份文档才答得全的问题，模型只看见先挤进来的那一份。
    # 各视图自己的预算在渲染时给（模型 = `files.DOC_TOP_K`，学生 = `FAQ_DOC_HITS`），
    # 选块统一走 `files.pick_diverse`（同一份不超过它两块，保证每份相关文档都发声）。
    return hits, doc_hits(query)


def search_kb(query: str) -> str:
    """工具：在**校园知识库**里检索 —— 问答条目 + 管理员上传的文档，一个入口查全。

    这是**给模型看的那一份**（带"哪份文件第几块、原文第几字起、相关度"这些定位元数据）。
    不经模型、直接贴给学生的那一路请走 `answer_for_student`（#41-A）。

    为什么要在这一个函数里合两路（#40 补2）：学生端/日程栏的链路只要求模型调 `search_kb`，
    它不会想到还有第二个工具。两边各开一口时，模型只会调前者 ——
    真机复现：《学生手册》里明写「图书馆位于校园中轴北侧」，问答却回"知识库没有图书馆的信息"
    （那阵子 kb.json 恰好是空的，于是文档那条路根本没人走）。
    **kb.json 空不空不是关键，入口不合并这个洞就一直在。**
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


def answer_for_student(query: str) -> str:
    """日程栏那条「知识库直答」用的**学生视图**（人话：这句话不经模型，屏幕上看到什么就是什么）。

    为什么不能直接复用 `search_kb`（#41-A）：模型视图里那句
    「[1]《学生手册-测试样本.txt》第 1 块（原文第 1 字起，相关度 3.80）」是给模型定位用的元数据，
    原样贴进气泡就成了"系统当着学生的面背数据库"。这里只留《文档名》+ 正文片段，
    出处仍在（学生能顺着文件名去翻），元数据去掉。
    ⚠️ 两边都没有时回的还是那句 `_MISS` 开头的原文 —— 判据嗅的是它（见 `KB_MISS_PREFIX`），
       两个视图不许各写一句"没找到"。
    """
    from app.modules.files import format_doc_hits_for_student

    hits, docs = _kb_parts(query)
    if not hits and not docs:
        return _MISS + query + "」直接相关的内容。"

    parts = [f"问：{h['question']}\n答：{h['answer']}" for h in hits[:3]]
    if docs:
        parts.append(format_doc_hits_for_student(docs, per_chunk=FAQ_DOC_CHARS,
                                                 query=query, limit=FAQ_DOC_HITS))
    return "\n\n".join(parts)



def kb_has_answer(query: str) -> bool:
    """知识库里（问答条目 + 上传文档）**到底有没有**这句的答案。

    #41-B 起，`main._kb_has_answer` 用的就是这一句，判据看的是**两个列表空不空**，
    不再是"回话有没有以那句 miss 开头"。为什么值得单独开一个口子：
    同一判断抄两处、改一处漏一处是这项目反复吃过的（红线 12 那一族）——
    以前 faq 改一次措辞，main 那边就**静默**把"都没找到"当成"有答案"。
    """
    hits, docs = _kb_parts(query)
    return bool(hits or docs)


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
