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

# 意图 → 该交给哪个模块（router 关键词没命中时靠它，省一次 LLM 分类）
INTENT_MODULE = {
    INTENT_ADD_TODO: "planner", INTENT_REMOVE_TODO: "planner",
    INTENT_LIST_TODO: "planner", INTENT_FIND_CARD: "planner",
    INTENT_RETIME: "planner", INTENT_CLEAR_TIMETABLE: "planner",
}

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
    for k in ("title", "minutes", "day_hint"):
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
           "matched": best.get("text") or ""}
    return out if out["intent"] in KNOWN_INTENTS else None


# ── 语义层（② LLM 兜底）────────────────────────────────────────────────────
# 给模型看的说明：**只让它读懂，不让它动手**。
# 反复强调"只输出 JSON / 不许自己发明 intent"，是因为它一旦自由发挥，
# 就会像以前那样回一句"已经帮你排好啦"——而日程里什么都没有。
_INTENT_SYSTEM = """你是校园时间管家的**意图识别器**，只做一件事：判断学生这句话想干什么。

重要：你**不负责安排时间、也不负责写日程**，那些由系统另做。你只输出意图和读出来的字段。

可选 intent（**只能**从这里挑一个，不许自造）：
- add_todo：想把一件事加进日程/待办（说没说时间都算，比如"我想去撸串""帮我记一下交电费"）
- remove_todo：想删掉一条已有的待办
- list_todo：想看看自己有哪些待办
- find_card：在找刚才那张确认卡片/弹窗（"卡片呢""怎么没弹出来"）
- retime_todo：想把已经排好的某件事换个时间
- clear_timetable：想清空课表
- chat：以上都不是（闲聊、问校园规定、查课表、查快递、查外卖…）

判断要领：
- "我想问一下…""…几点关门""…在哪"这种**提问**是 chat，不是 add_todo。
- 只有学生真的想把一件事**排进日程**才算 add_todo。
- 拿不准就选 chat，系统会照常回答，不会办错事。

只输出一个 JSON，不要任何解释文字：
{"intent":"...","title":"要做的事，两到六个字，没有就空","minutes":0,"day_hint":"今天/明天/周一/工作日/周末/2026-09-30，没有就空","date":"YYYY-MM-DD，没有就空","start":"HH:MM，没有就空","end":"HH:MM，没有就空","confidence":0.0}

confidence 是你对自己判断的把握（0~1）。"""


async def classify_with_llm(text: str, history: list | None = None) -> dict | None:
    """问一次大模型：这句话是什么意图？（人话：让模型替正则把话听懂）

    **它拿不到任何写库接口，也碰不到排期算法**——返回的只是一串字段，
    真正的查空档/出卡/写库在 planner.py 里，跟模型无关。

    没配 key、调不通、吐的不是 JSON、intent 不在白名单、把握太低 → 一律返回 None，
    让调用方照旧往下走（宁可漏判，不可替学生办错事）。
    """
    t = (text or "").strip()
    if not t:
        return None
    try:
        from app.config import get_llm_config
        from app.llm.client import DeepSeekClient
        if not (get_llm_config().get("api_key") or "").strip():
            return None          # 没 key → 这一层不生效，样本库照旧兜
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
        reply = await DeepSeekClient().chat(msgs)
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
        "intent": intent, "title": title, "minutes": minutes,
        "day_hint": (data.get("day_hint") or "").strip(),
        "date": (data.get("date") or "").strip(),
        "start": (data.get("start") or "").strip(),
        "end": (data.get("end") or "").strip(),
        "confidence": round(conf, 2), "source": "llm",
    }


async def understand(text: str, history: list | None = None) -> dict | None:
    """两道兜底的统一入口（人话：规则接不住时问它）。

    顺序有讲究：**先查样本库（零成本、离线可用），再问 LLM（要花一次调用）**。
    样本库命中就省下这次调用；没命中才升级到语义层。
    两道都没有 → 返回 None，调用方照旧掉给模型自由发挥（现状行为，不倒退）。
    """
    hit = match_sample(text)
    if hit:
        return hit
    return await classify_with_llm(text, history)


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
