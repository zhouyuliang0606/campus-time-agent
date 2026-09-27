"""意图兜底层（人话：规则接不住时，还有两道兜底：样本库 + 语义层）。

为什么要有这一层
================
学生报障原话：「**很多的字都是接不住的，你现在能接住的都是我测试给的**」。

这句话点破了要害。过去每报一次就往词表里加一条（`_ADD_INTENT` 加个词、
`_GO_INTENT_RE` 认一种句式、main.py 再来一张 `_FIND_CARD_WORDS`）——
`planner.py` 里已经堆到 **30 多张**关键词/正则表，`router._QUICK_MAP` 还有
**50 多个**硬编码词，全是 `if keyword in text` 这种朴素子串匹配。
这是在**拿字符串匹配干语义理解的活**，词表加多长都收敛不了：
中文口语的写法是无限的，而"你说一句我补一词"永远慢一拍。

这一层换了个思路：**把「听懂人话」和「动手执行」彻底切开**。

    ┌─ 判定层（可换、可兜底、可以交给模型）──────────────┐
    │  ① 规则：30 张词表，命中就走，零成本零延迟          │
    │  ② 语义：问一次 LLM，只要回 JSON 意图   ← 本文件   │
    │  ③ 样本库：历史成功原话模糊匹配         ← 本文件   │
    └──────────────────────────────────────────────────┘
    ┌─ 执行层（系统独占，模型永远碰不到）────────────────┐
    │  查空档 · 排期 · 出候选卡 · 写库                    │
    └──────────────────────────────────────────────────┘

② 那一层让 LLM 干的活**只有一件**：把这句话听懂、输出结构化意图。
它拿不到任何写库接口，也碰不到排期算法——拿到 JSON 之后，查空档、摊候选、
出卡、写库走的**还是 planner.py 那套老代码**。所以「AI 只提案、系统才执行」
这条架构主张一条不破；变的只是"听懂人话"这一步不再由正则独占。

成本：只有规则接不住才往下走一层，常规说法**零 API 调用、零延迟**。
没配 key 就自动跳过 ②，③ 样本库照样离线兜底（演示不依赖 key）。
"""
from __future__ import annotations

import json
import os
import re
import time

# ── 意图白名单 ────────────────────────────────────────────────────────────
# 语义层只许从这几项里挑一个。它吐出别的（或者吐了个新词）一律当 chat，
# **绝不让模型自己发明一种意图**——那是执行权的口子，堵死在这里。
INTENT_ADD_TODO = "add_todo"          # 想把一件事加进日程（说没说时间都算）
INTENT_REMOVE_TODO = "remove_todo"    # 想删掉一条待办
INTENT_LIST_TODO = "list_todo"        # 想看看自己有哪些待办
INTENT_FIND_CARD = "find_card"        # 在找刚才那张确认卡 / 弹窗
INTENT_RETIME = "retime_todo"         # 想把已排好的某件事换个时间
INTENT_CLEAR_TIMETABLE = "clear_timetable"   # 想清空课表
INTENT_CHAT = "chat"                  # 以上都不是

KNOWN_INTENTS = (
    INTENT_ADD_TODO, INTENT_REMOVE_TODO, INTENT_LIST_TODO,
    INTENT_FIND_CARD, INTENT_RETIME, INTENT_CLEAR_TIMETABLE, INTENT_CHAT,
)

# ── 类型（module）白名单 ─────────────────────────────────────────────────────
# 「这句话归哪个类型/模块管」和「想干什么（intent）」现在是**同一次判定**一起给出的：
# 主判定这一层（样本库 → LLM），`router._QUICK_MAP` 那 50 多个关键词只当**兜底**。
# 放这里而不是 router.py，是为了让 router 反向复用（依赖方向 router → intent，单向、不成环）。
MODULE_KEYS = ("schedule", "faq", "express", "takeout", "planner",
               "lostfound", "repair", "notice", "station", "admin")

# 每个类型（module）的人话说明。**只写一份**：语义层的提示词、router 的对照表、
# 文档里的说明，都从这儿取——省得三处各写一份、改一处忘两处。
MODULE_MEANING = {
    "schedule": "课表/时间安排/复习计划/空闲时间",
    "faq": "校园规定/地点/办事流程等问答（答案从知识库检索）",
    "express": "学生查自己的快递/取件码/滞留",
    "takeout": "学生查外卖订单/取餐点/待取",
    "planner": "学生个人日程（给待办排时间、周表月表、加/删/改待办）",
    "lostfound": "丢东西/捡到东西/失物招领",
    "repair": "设施报修/东西坏了/维修",
    "notice": "公告/通知",
    "station": "驿站商户侧（监听台账/主动推送/客服人格）",
    "admin": "管理知识库/配置客服人格",
}

# 拿不准时的默认类型。挑 faq 而不是 planner：**答一句，比替学生办错一件事安全**。
DEFAULT_MODULE = "faq"

# 意图 → 该交给哪个模块。语义层没给 module（或给了个不认得的）时靠它兜住，
# 保证判定层永远吐得出一个合法类型，调用方不用再判空。
INTENT_MODULE = {
    INTENT_ADD_TODO: "planner", INTENT_REMOVE_TODO: "planner",
    INTENT_LIST_TODO: "planner", INTENT_FIND_CARD: "planner",
    INTENT_RETIME: "planner", INTENT_CLEAR_TIMETABLE: "planner",
    INTENT_CHAT: DEFAULT_MODULE,
}


def module_hint(intent: str | None, fallback: str = DEFAULT_MODULE) -> str:
    """由意图推类型（人话：不知道归哪个模块管，就看它想干什么）。

    加/删/改/看清单一律归 planner——"想动自己的日程"这件事，类型是确定的；
    只有 chat（什么都没想干 / 只是在问）才需要看**问的内容**，那由语义层给 module。
    """
    got = INTENT_MODULE.get(intent or "")
    return got if got in MODULE_KEYS else (
        fallback if fallback in MODULE_KEYS else DEFAULT_MODULE)


# ── 「这句话是在问，还是在让我办事」────────────────────────────────────────
# 这是判定层的**地基**，不是又一张关键词表：多义词（"安排"既是名词又是动词）
# 是子串匹配的天生盲区，词表补多长都收敛不了。所以这里只给一个**粗信号**，
# 用它决定"要不要升级到语义层复核"，**绝不用它下结论**——真正拍板的是语义层。
# 故意取宽：宁可疑一下、多问一次，也别漏判（漏判的代价是规则继续替学生办错事）。
QUESTION_RE = re.compile(
    r"吗|呢|谁|哪|是不是|能不能|可不可以|要不要|行不行|好不好|对不对|"
    r"咋|怎么|如何|为什么|为啥|几点|多少|多久|多长时间|多远|"
    r"有没有|啥|什么|干嘛|干啥|看看|查查")
_QUESTION_TAIL_RE = re.compile(r"[？?]\s*$")


def looks_like_question(text: str) -> bool:
    """粗判"这像是在问"（人话：先分它是问我事、还是让我办事）。

    它只决定"要不要复核"，所以宽一点是安全的（最坏多问一次语义层），
    而窄一点会漏掉口语问句、让关键词表继续替学生办错事。
    """
    t = (text or "").strip()
    if not t:
        return False
    return bool(_QUESTION_TAIL_RE.search(t) or QUESTION_RE.search(t))

# ── 样本库（③ 离线兜底）────────────────────────────────────────────────────
# 系统每成功处理一句学生的话，就把这句原话连同判定的意图存下来（自增长）。
# 下次碰到一句规则接不住的话，先来这儿做**模糊匹配**——命中就沿用那条意图。
# 它只能覆盖"说过的"，泛化不如语义层，但**零成本、离线可跑、且越用越准**，
# 同时还是语义层的缓存：LLM 判定过的说法存进来，下次连 API 都不用调。
_SAMPLES_FILE = "intent_samples.json"
_SAMPLES_MAX = 400          # 上限，免得无限膨胀成一个谁也读不完的垃圾堆
# 相似度阈值。中文短句的字符二元组 Dice 系数，实测：
#   同义改写（"我要去吃火锅" / "我想吃火锅"）≈ 0.5~0.7
#   无关两句（"我要去吃火锅" / "图书馆几点关门"）≈ 0.1~0.2
# 定 0.42 偏保守：宁可漏（往下走语义层），不可错（替学生办错事）。
_SIM_THRESHOLD = 0.42
# 太短的话不做模糊匹配：字少的时候 Dice 会虚高（"加个" 能跟 "加个健身" 算到 0.5+），
# 一句话里少一个字意思就全变了，风险比收益大。
_MIN_MATCH_LEN = 4

# 内置种子：新装的实例也有几条例句可依。别在这里堆词——堆多了它就成了
# 又一张关键词表，违背这一层的初衷。放**句式样板**，剩下的交给相似度泛化。
_SEED_SAMPLES = [
    {"text": "我要平时去吃火锅，76分钟", "intent": INTENT_ADD_TODO,
     "title": "吃火锅", "minutes": 76, "day_hint": "工作日"},
    {"text": "我想去游泳，帮我安排个时间", "intent": INTENT_ADD_TODO,
     "title": "游泳", "minutes": 0, "day_hint": ""},
    {"text": "我打算找个空档练会儿吉他", "intent": INTENT_ADD_TODO,
     "title": "练吉他", "minutes": 0, "day_hint": ""},
    {"text": "把周四那个健身挪到周五", "intent": INTENT_RETIME,
     "title": "健身", "minutes": 0, "day_hint": ""},
    {"text": "卡片呢，怎么没弹出来", "intent": INTENT_FIND_CARD,
     "title": "", "minutes": 0, "day_hint": ""},
    {"text": "我都有啥待办来着", "intent": INTENT_LIST_TODO,
     "title": "", "minutes": 0, "day_hint": ""},
    {"text": "把课表整个清掉重来", "intent": INTENT_CLEAR_TIMETABLE,
     "title": "", "minutes": 0, "day_hint": ""},
]

_PUNCT_RE = re.compile(r"[\s，,。！!？?、；;：:~\-—『』「」《》\"'“”‘’()（）\[\]【】]")


def _norm(s: str) -> str:
    """归一化：去标点空格，只留可比的字面。"""
    return _PUNCT_RE.sub("", (s or "").strip()).lower()


def _bigrams(s: str) -> set:
    """字符二元组集合（"吃火锅" → {吃火, 火锅}）。单字句退化成它自己。"""
    s = _norm(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} or ({s} if s else set())


def _dice(a: str, b: str) -> float:
    """Dice 系数：0 = 毫不相干，1 = 一字不差。"""
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return 2 * len(A & B) / (len(A) + len(B))


def _samples_path() -> str:
    """样本库落盘路径：**每次调用都重读环境变量**，不在导入时定死。

    跟 pending.py 同一个约定：测试里会连开好几个 sandbox()、每次换一个
    CAMPUSTIME_DATA_DIR，定死了就会串数据（上一个沙箱学的句子跑进下一个）。
    """
    base = os.environ.get("CAMPUSTIME_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data"
    )
    return os.path.join(base, _SAMPLES_FILE)


def load_samples() -> list:
    """读出样本库（内置种子 + 运行期学到的）。读不到就只回种子，不报错。"""
    try:
        with open(_samples_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict) and s.get("text")]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return list(_SEED_SAMPLES)


def _save_samples(samples: list) -> None:
    """写回磁盘。写失败不该让对话崩——内存里这一轮照样能用。"""
    try:
        path = _samples_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def remember(text: str, intent: str, fields: dict | None = None) -> None:
    """把一句"系统判对、也办成了"的话记进样本库（人话：让它越用越懂你）。

    只在**真办成了**的时候调（出了卡、或者真写进了日程），
    别在"模型自由发挥了一句"之后调——那会把错误的意图学进去。
    """
    t = (text or "").strip()
    if not t or intent not in KNOWN_INTENTS or intent == INTENT_CHAT:
        return
    samples = load_samples()
    key = _norm(t)
    for s in samples:
        if _norm(s.get("text") or "") == key and s.get("intent") == intent:
            s["hits"] = int(s.get("hits") or 0) + 1
            s["at"] = time.time()
            _save_samples(samples)
            return
    item = {"text": t, "intent": intent, "at": time.time(), "hits": 1}
    for k in ("title", "minutes", "day_hint", "module"):
        v = (fields or {}).get(k)
        if v:
            item[k] = v
    samples.append(item)
    # 超上限：按"命中次数少的、老的"先丢，把常用的留住
    if len(samples) > _SAMPLES_MAX:
        samples.sort(key=lambda s: (int(s.get("hits") or 0), float(s.get("at") or 0)),
                     reverse=True)
        samples = samples[:_SAMPLES_MAX]
    _save_samples(samples)


def match_sample(text: str) -> dict | None:
    """在样本库里模糊找一句最像的（人话：这句话以前是不是成功办过）。

    返回带 `source="sample"` 的意图字典，找不到返回 None（交给语义层）。
    """
    t = (text or "").strip()
    if len(_norm(t)) < _MIN_MATCH_LEN:
        return None
    best, best_score = None, 0.0
    for s in load_samples():
        st = s.get("text") or ""
        if len(_norm(st)) < _MIN_MATCH_LEN:
            continue
        score = _dice(t, st)
        if score > best_score:
            best, best_score = s, score
    if best is None or best_score < _SIM_THRESHOLD:
        return None
    out = {"intent": best.get("intent"), "title": best.get("title") or "",
           "minutes": int(best.get("minutes") or 0),
           "day_hint": best.get("day_hint") or "", "date": "", "start": "",
           "end": "", "confidence": round(best_score, 2), "source": "sample",
           "matched": best.get("text") or "",
           # 学的时候如果记下了 module 就用它，没记下就按意图推——
           # 类型判定跟意图一样，都得从这一层出来，调用方不用自己猜。
           "module": (best.get("module") or module_hint(best.get("intent")))}
    return out if out["intent"] in KNOWN_INTENTS else None


# ── 语义层（② LLM 兜底）────────────────────────────────────────────────────
# 给模型看的说明：**只让它读懂，不让它动手**。
# 反复强调"只输出 JSON / 不许自己发明"是因为它一旦自由发挥，
# 就会像以前那样回一句"已经帮你排好啦"——而日程里什么都没有。
#
# 2026-09-27 重做：**类型（module）也归这一层判**。
#   以前类型是 `router._QUICK_MAP` 那 50 多个 `if keyword in text` 说了算，
#   而"归哪个模块"和"想干什么"本来是同一件事的两面——分开判就会互相打架：
#   学生问「我要去哪里取快递」（在校园问答栏里），关键词表看见"取快递"就把话
#   塞给快递模块，加待办那条分支又是**不分模块**的，于是给他出了一张待办卡。
#   现在两件事一次问清：先分"这是问还是办"，再给 module 和 intent。
_MODULE_LINES = "\n".join(f"- {k}：{v}" for k, v in MODULE_MEANING.items())

_INTENT_SYSTEM = f"""你是校园时间管家的**类型与意图识别器**，只做一件事：把学生这句话归类。

重要：你**不负责安排时间、不负责写日程、也不负责回答**，那些由系统另做。
你只输出两样：这句话归哪个类型管（module）、他想干什么（intent）。

可选 module（**只能**从这里挑一个，不许自造）：
{_MODULE_LINES}

可选 intent（**只能**从这里挑一个，不许自造）：
- add_todo：想把一件事**排进日程/待办**（说没说时间都算，比如"我想去撸串""帮我记一下交电费"）
- remove_todo：想删掉一条已有的待办
- list_todo：想看看自己有哪些待办
- find_card：在找刚才那张确认卡片/弹窗（"卡片呢""怎么没弹出来"）
- retime_todo：想把已经排好的某件事换个时间
- clear_timetable：想清空课表
- chat：以上都不是（闲聊、问校园规定、查课表、查快递、查外卖…）

判断要领（这几条是**闸门**，请当硬规则执行）：
- **第一步先分"问"还是"办"**：学生在**问**（…在哪 / 几点 / 怎么走 / 有哪些 / 是不是 / 能不能 / 有没有）
  → intent 一律 `chat`，module 按**问的内容**给（问规定地点流程→faq；问自己的快递→express；
  问自己的外卖→takeout；问课表/什么时候没课→schedule）。
  **问句绝不算 add_todo**——哪怕句子里出现了"取快递""安排""预约"这种词。
- **只有学生真的想把一件事排进日程**（"帮我安排…""加个…""记一下我要…"）才算 add_todo，module 给 `planner`。
- remove_todo / list_todo / retime_todo / clear_timetable 一律 module 给 `planner`。
- 拿不准时：intent 选 `chat`、module 选 `faq`。系统会照常答他一句，**不会替他办错事**。

只输出一个 JSON，不要任何解释文字：
{{"module":"...","intent":"...","title":"要做的事，两到六个字，没有就空","minutes":0,"day_hint":"今天/明天/周一/工作日/周末/2026-09-30，没有就空","date":"YYYY-MM-DD，没有就空","start":"HH:MM，没有就空","end":"HH:MM，没有就空","confidence":0.0}}

confidence 是你对自己判断的把握（0~1）。"""


async def classify_with_llm(text: str, history: list | None = None,
                            override: dict | None = None,
                            client=None) -> dict | None:
    """问一次大模型：这句话归哪个类型、想干什么？（人话：让模型替关键词表把话听懂）

    **它拿不到任何写库接口，也碰不到排期算法**——返回的只是一串字段，
    真正的查空档/出卡/写库在 planner.py 里，跟模型无关。

    `override` 是学生端自己填的 API 配置（原样透传给客户端）；
    `client` 允许调用方传一个现成的客户端（router 用，省得重复构造）。

    没配 key、调不通、吐的不是 JSON、intent 不在白名单、把握太低 → 一律返回 None，
    让调用方照旧往下走（宁可漏判，不可替学生办错事）。
    """
    t = (text or "").strip()
    if not t:
        return None
    try:
        from app.config import get_llm_config
        from app.llm.client import DeepSeekClient
        if not (get_llm_config(override=override).get("api_key") or "").strip():
            return None          # 没 key → 这一层不生效，样本库照旧兜
        client = client or DeepSeekClient()
    except Exception:
        return None

    # 带上一两句最近的话：学生上一轮说"我想去吃火锅"、这一轮说"就周四吧"，
    # 光看这一句模型也猜不出来。只带最近 4 条，别把整个历史都塞进去。
    tail = [m for m in (history or []) if isinstance(m, dict)][-4:]
    msgs = [{"role": "system", "content": _INTENT_SYSTEM}]
    for m in tail:
        role = m.get("role") if m.get("role") in ("user", "assistant") else "user"
        content = (m.get("content") or "").strip()
        if content:
            msgs.append({"role": role, "content": content[:200]})
    if not any(m["content"] == t for m in msgs[1:] if m["role"] == "user"):
        msgs.append({"role": "user", "content": t})

    try:
        reply = await client.chat(msgs, override=override)
    except Exception:
        return None

    raw = (reply or {}).get("content", "") if isinstance(reply, dict) else str(reply)
    m = re.search(r"\{[^{}]*\}", raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    intent = (data.get("intent") or "").strip()
    if intent not in KNOWN_INTENTS:
        return None                       # 模型自造了一个意图 → 不认，当没听懂
    try:
        conf = float(data.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < 0.6:
        return None                       # 它自己都没把握，就别替学生做主
    try:
        minutes = int(data.get("minutes") or 0)
    except (TypeError, ValueError):
        minutes = 0

    # 类型：白名单之外的一律不认（模型偶尔会顺着"卡"字想成校园卡、给出个野模块名），
    # 这时**按意图推一个**——加待办必归 planner，纯 chat 才回落到 faq。
    module = (data.get("module") or "").strip()
    if module not in MODULE_KEYS:
        module = module_hint(intent)

    title = (data.get("title") or "").strip()
    # 「我要去吃火锅」→ title 得真像一件事才行。模型偶尔会把"吃火锅"写成
    # "去吃火锅一顿"或者干脆把整句抄回来，这道闸门沿用 planner 的判真逻辑，
    # 判不过就当没读到标题（由下面的解析照旧处理），**不硬塞一个怪名字进卡片**。
    if intent == INTENT_ADD_TODO and title:
        try:
            from app.modules.planner import _pick_title, looks_like_thing
            cleaned = _pick_title(title)
            if cleaned and cleaned != "待办" and looks_like_thing(cleaned):
                title = cleaned
            else:
                title = ""
        except Exception:
            title = ""

    return {
        "module": module, "intent": intent, "title": title, "minutes": minutes,
        "day_hint": (data.get("day_hint") or "").strip(),
        "date": (data.get("date") or "").strip(),
        "start": (data.get("start") or "").strip(),
        "end": (data.get("end") or "").strip(),
        "confidence": round(conf, 2), "source": "llm",
    }


async def classify(text: str, history: list | None = None,
                   override: dict | None = None) -> dict | None:
    """判定层的**统一入口**（人话：这句话归哪个类型、想干什么，一趟问出来）。

    顺序是"**语义优先、关键词兜底**"，这是 2026-09-27 那次重做的要点：
      ① 样本库：历史真办成功过的说法做模糊匹配   ← 零成本、离线可跑，兼作 ② 的缓存
      ② 语义层：问一次 LLM（只读分类）          ← 要花一次调用，没配 key 自动跳过
    两道都没定 → None。调用方自己决定怎么兜（router 会退到关键词表，
    main.py 会退回"原来那条确定性分支"，**行为不倒退**）。

    返回的字典里 `module` 和 `intent` 都是**已经验过白名单**的，拿来即用。
    """
    hit = match_sample(text)
    if hit:
        return hit
    return await classify_with_llm(text, history, override=override)


async def understand(text: str, history: list | None = None,
                     override: dict | None = None) -> dict | None:
    """`classify` 的别名（保留旧名字，老调用点不用改）。"""
    return await classify(text, history, override=override)


def to_canonical(text: str, sem: dict | None) -> str:
    """把语义层读出来的字段，拼回一句**解析器认得的规范话**。

    为什么不直接拿字段去构造卡片：planner.py 里那套
    `parse_add_todo` / `todo_mode_proposal` / `candidate_slots` 已经把
    "抠标题、认日期、认时长、认平时周末、查空档"全做对了，抄第二份迟早走样。
    所以这里把语义层当一个**翻译器**：口语 → 规范写法，再喂给老解析器。

    例：{"intent":"add_todo","title":"吃火锅","minutes":76,"day_hint":"工作日"}
        → "加个吃火锅，76分钟，工作日"
    """
    if not sem:
        return text
    bits = []
    title = (sem.get("title") or "").strip()
    if title:
        bits.append(f"加个{title}")
    minutes = int(sem.get("minutes") or 0)
    if minutes > 0:
        bits.append(f"{minutes}分钟")
    date = (sem.get("date") or "").strip()
    if date:
        bits.append(date)
    elif (sem.get("day_hint") or "").strip():
        bits.append(sem["day_hint"].strip())
    start = (sem.get("start") or "").strip()
    end = (sem.get("end") or "").strip()
    if start and end:
        bits.append(f"{start}-{end}")
    if not bits:
        return text
    return "，".join(bits)
