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
3. search_kb 说"没有找到"时，诚实回"这个我暂不清楚"，并建议去对应部门或官网查询，绝对不要编造；
   —— 也不许自己去猜"是不是还得再换个工具查一遍"：它没查到就是两边都没查到。
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


FAQ_DOC_HITS = 2        # 合并进 search_kb 的文档段落最多几块（直答那一路不经模型，砸太长没法看）
FAQ_DOC_CHARS = 320     # 每块最多留多少字

# ⚠️ 这句 miss 文案是**一份契约**，两个地方在按它的前缀判断"知识库里到底有没有"：
#    `main._kb_has_answer`（决定「校车在哪坐」这类问句要不要被"查日程"那支接走）
#    与 `test_15` 的那组断言。要改字眼，连着改那一处判据，别只改这边。
_MISS = "知识库里没有找到和「"


def search_kb(query: str) -> str:
    """工具：在**校园知识库**里检索 —— 问答条目 + 管理员上传的文档，一个入口查全。

    为什么要在这一个函数里合两路（#40 补2）：学生端/日程栏的链路只要求模型调 `search_kb`，
    它不会想到还有第二个工具。两边各开一口时，模型只会调前者 ——
    真机复现：《学生手册》里明写「图书馆位于校园中轴北侧」，问答却回"知识库没有图书馆的信息"
    （那阵子 kb.json 恰好是空的，于是文档那条路根本没人走）。
    **kb.json 空不空不是关键，入口不合并这个洞就一直在。**
    """
    from app.modules.files import doc_hits, format_doc_hits
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

    # 文档那一路：只在**真命中**时才拼进来（空查询/一份文档都没有/没命中都回空表，见 doc_hits）
    docs = doc_hits(query)[:FAQ_DOC_HITS]

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



def build_tools() -> dict[str, Tool]:
    """把 search_kb 包装成 Agent 能调用的工具。"""
    return {
        "search_kb": Tool(
            name="search_kb",
            description=(
                "在校园知识库检索答案。这一处**同时查**管理员维护的问答条目与管理员上传的文档"
                "（手册/规定等），所以条目表为空不代表知识库没有答案，不用再换别的工具查一遍。"
                "入参 query 是学生的问题或关键词，如 '图书馆几点关门'。"
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
