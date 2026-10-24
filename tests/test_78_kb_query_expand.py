"""#57 的护栏：知识库检索前的「查询改写」——只动输入、只在查不到时才问、失败静默回退。

施工单 §4.1 要三样东西，这份文件一样一样对着钉：
  ① 改写器输出形状（A 批，纯函数，不联网）；
  ② **一切失败路径与"没有这个功能"逐字相同**（B 批，假客户端打超时/坏 JSON/没 Key）；
  ③ 改前改后两张表（C/D 批：拿**固定的补词**重放评测里实测到的那批同义词，
     于是「泳池 → 游泳馆」这类召回增益不联网也能复跑）。

⚠️ 为什么补词是**抄实测**而不是现编：`eval57b.py` 那一轮真模型给「泳池多少钱一次」
   补的就是「游泳馆 游泳池 单次」；给「校医院几点上班」原本补了
   「开放时间 营业时间 就诊时间」，而知识库里**没有校医院这一条** —— 那句本该老实说
   "没查到"的话被答成了『食堂营业时间』。这一颗误命中把泛词闸逼了出来（A7/A9），
   现在同一句只剩「医务室 门诊」，仍然查不到 ⇒ 仍然老实说没查到（C2-bis）。
   拿真模型某一次的输出当"召回质量"的保证是错的：这里钉的是**链路与判据**，
   改前/改后的 hit@k 数字记在快照里（那是量出来的，不由测试来担保）。

范围（施工单 §五 + 本轮实测后收窄的一处，见快照的偏离单）：
   改写只挂在**"本来要回没查到"那一支**（命中那一路一次模型都不问、一个字都不变，D3 钉的就是它）；
   `search_kb`（给模型的那一路）与 `/api/admin/kb/probe`（管理端试检索）**都不扩**。
"""
import asyncio
import inspect
import json
import os
import time

from _harness import Checker, data_path, make_client, sandbox, seed_kb, title

code = 0

# 施工单 §2.2 的 few-shot：第三列是**闸之后**该活下来的词（本批实测值，不是设计稿）
FEWSHOT = [
    ("补办学生证多少钱", ["补办", "学生证", "工本费", "遗失", "丢了", "收费", "费用"],
     ["工本费", "遗失", "丢了"]),
    ("图书馆几点关门", ["图书馆", "几点", "关门", "自习室", "阅览室", "开放时间", "闭馆时间"],
     ["自习室", "阅览室"]),
    ("我的快递在哪取", ["快递", "取件", "在哪", "驿站", "取件点", "包裹", "取件码"],
     ["取件", "驿站", "取件点", "包裹", "取件码"]),
]
NOT_FACT = ("明天上午有什么课", "我今天有什么安排", "我饿了")

# 固定的自种条目表：故意一条靠「游泳馆」（学生说泳池）、一条手写关键词（图书馆）、
# 一条手写「校园网」（wifi 那一路的靶子）、一条手写「寒假时间」（校历那一档的靶子）。
ENTRIES = [
    ("游泳馆价格", "成人 20 元一次，学生凭卡 10 元。", []),
    ("图书馆开放时间", "周一至周五 8:00-22:00，考试周延长到 23:00。", ["图书馆"]),
    ("校园网密码怎么改", "在校园自助服务平台改。", ["校园网"]),
    ("寒假放假安排", "见校历：1 月 12 日起放寒假。", ["寒假时间"]),
]
DOC_LIFE = ("阅览室开放时间为每日八时至二十二时，闭馆前半小时停止入场，自习室与阅览室同址。\n"
            + "快递服务点（俗称驿站）位于东南门内侧，取件码短信在到件当日二十一时前发出。\n")

_EXPAND_MARK = "检索改写器"          # 提示词首行里的身份标记（分清"谁来问的"）
HIT = "再查一次（命中）"             # 两处调用点轨迹里的固定字样（断言认它，别认"改写"两个字）


class _FakeClient:
    """假模型客户端：按剧本回话，并**记下每一次是谁来问的**（系统提示词的头 40 字）。

    为什么要分"谁来问"：`/api/chat` 那一轮里判定层、润色、改写器都打这同一个桩，
    只数总调用次数的断言会把"判定层多问了一次"算到改写器头上（假信号）。
    """

    def __init__(self):
        self.reply = ""
        self.mode = "ok"          # ok / boom（抛 LLMError）/ slow（超过保险丝）
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        sys0 = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
        self.calls.append(sys0[:40])
        if self.mode == "boom":
            from app.llm.client import LLMError
            raise LLMError("上游不通", kind="upstream")
        if self.mode == "slow":
            await asyncio.sleep(0.25)
        return {"content": self.reply}

    @property
    def expand_calls(self):
        return [c for c in self.calls if _EXPAND_MARK in c]


_OLD = []


def use_fake(reply="", mode="ok", server_key=False):
    """打桩：返回 `(假客户端, 恢复函数)`。

    与 `_harness.patch_llm` 同一套做法 —— 打的是 `app.llm.client.DeepSeekClient` 与
    `app.config.get_llm_config` 这两个**模块属性**：`query_expansion` 里那两条 import 都在
    函数体内（顶层绑死就打不进来，见 `polish.py` 的踩坑记录）。
    ⚠️ 这里的 `get_llm_config` **认 override**，因为 B9 要验"服务器没 Key、学生自带 Key"那一支；
       `_harness.patch_llm` 那份不认，拿它打这一条会静默假绿。
    """
    import app.config as _cfg
    import app.llm.client as _client
    from app.modules import faq
    if not _OLD:
        _OLD.append((_client.DeepSeekClient, _cfg.get_llm_config))
    fake = _FakeClient()
    fake.reply, fake.mode = reply, mode
    _client.DeepSeekClient = lambda *a, **k: fake

    def _cfg_fn(*a, **k):
        ov = k.get("override") or {}
        return {"api_key": ov.get("api_key") or ("test-key" if server_key else ""),
                "base_url": ov.get("base_url") or "http://x", "model": "m"}

    _cfg.get_llm_config = _cfg_fn
    faq._EXPAND_CACHE.clear()          # 缓存是模块级的：不清就拿上一批的结果骗过这一批

    def restore():
        _client.DeepSeekClient, _cfg.get_llm_config = _OLD[0]
        faq._EXPAND_CACHE.clear()
    return fake, restore


def run(coro):
    return asyncio.run(coro)


def _obj(words, flag=True):
    return {"is_campus_fact_query": flag, "expanded_query": " ".join(words)}


def _usage_rows(user="内部:检索改写"):
    from app.store import read_llm_usage
    return [r for r in (read_llm_usage(limit=0) or []) if r.get("user") == user]


def batch_a_gate():
    """A 批：改写结果的闸（`store.clean_expand_words`，纯函数，不碰数据）。"""
    from app.store import clean_expand_words

    c = Checker()
    title("A. 闸：施工单那几条规则各自要有牙，判据是实测来的")

    c.check("A1 few-shot 1「补办学生证多少钱」：原句已有的「补办/学生证」不重复加、"
            "泛词「收费/费用」丢掉，剩下三个对象词活着（变异 M6 的靶子：去掉'已在原句'就红）",
            clean_expand_words("补办学生证多少钱", _obj(FEWSHOT[0][1])) == FEWSHOT[0][2])
    c.check("A2 few-shot 2「图书馆几点关门」：以「时间」结尾的方面词（开放时间/闭馆时间）丢掉",
            clean_expand_words("图书馆几点关门", _obj(FEWSHOT[1][1])) == FEWSHOT[1][2])
    c.check("A3 few-shot 3「我的快递在哪取」：「在哪」是虚词、快递在原句，都丢；驿站/取件码留着",
            clean_expand_words("我的快递在哪取", _obj(FEWSHOT[2][1])) == FEWSHOT[2][2])
    c.check("A4 错字归一出来的「图书馆」是**新词**，必须活着（这是改写器对错字唯一的收益，"
            "别被'已在原句'那条误伤 —— 「图收馆」里没有「图书馆」这三个字）",
            "图书馆" in clean_expand_words("图收馆在哪个楼",
                                          _obj(["图书馆", "在哪个楼", "位置", "自习室"])))
    c.check("A5 「不是校园事实问句」→ 一个字都不加（施工单 §三.2；变异 M1 的靶子）",
            all(clean_expand_words(q, _obj(["图书馆", "自习室"], flag=False)) == [] for q in NOT_FACT))
    c.check("A6 布尔回成字符串也放行（只认 `is True` 会让这一路**静默**永不生效，"
            "表现与没写这个功能一模一样）",
            clean_expand_words("泳池价格", {"is_campus_fact_query": "true", "expanded_query": "游泳馆"})
            == ["游泳馆"]
            and clean_expand_words("泳池价格", {"is_campus_fact_query": "True",
                                                "expanded_query": "游泳馆"}) == ["游泳馆"])
    c.check("A7 「营业时间/就诊时间」这类**方面词尾巴**丢掉 —— 实测那颗误命中的牙"
            "（`_KB_GENERIC` 里只有「时间」，整词相等拦不住四字短语）",
            clean_expand_words("校医院几点上班",
                               _obj(["医务室", "门诊", "开放时间", "营业时间", "就诊时间"]))
            == ["医务室", "门诊"])
    c.check("A7-bis 但尾巴前面那截**是原句里那个对象**时留着：「寒假什么时候放假」补出「寒假时间」"
            "必须放行（条目写的就是「寒假时间」）—— 一刀切「以方面词结尾就丢」会把这类正主的换说法"
            "一起杀掉，第一版就是这么漏掉 D4 那一句的",
            clean_expand_words("寒假什么时候放假", _obj(["寒假时间", "校历"])) == ["寒假时间", "校历"])
    c.check("A8 虚词**整词**丢（STOP 里的「什么时候/几点/请问」都掉了；「在哪里」表里只有「在哪」，"
            "整词不等就放行 —— 这是有意按整词比，不是漏网：加宽到子串就会把「图书馆在哪」这类"
            "真对象词一起吃掉）",
            clean_expand_words("宿舍热水", _obj(["什么时候", "几点", "在哪里", "请问", "热水表"]))
            == ["在哪里", "热水表"])
    c.check("A9 泛词整词丢（开放/今天/网址/断电 —— 这四个**只在 `_KB_GENERIC` 里**、"
            "不在尾巴表里：变异 M3 拆泛词闸时只有这一组能红，第一版拿「收费/办理/密码/位置」"
            "测，那四个同时也是尾巴，M3 全绿 = 这条断言当时是死代码）",
            clean_expand_words("图书馆多少钱",
                               _obj(["开放", "今天", "网址", "断电", "泳池"])) == ["泳池"])
    c.check("A9-bis 同一条里的对象词「泳池」活着（丢了它 #57 当场空转：条目写的是「游泳馆」）",
            clean_expand_words("游泳馆一次多少钱", _obj(["收费", "办理", "泳池"])) == ["泳池"])
    c.check("A10 纯数字丢掉（「12」会把任何带 12 的问句接走 —— 与泛词同一种放宽）",
            clean_expand_words("宿舍门牌", _obj(["12", "2026", "号楼"])) == ["号楼"])
    c.check("A11 长度 2~8 之外都丢（单字不成词、一长串不是词）",
            clean_expand_words("图书馆", _obj(["书", "图书馆自习室开放区域安排总表说明"])) == [])
    c.check("A12 尖括号在**洗标点之前**判（#55 那处修正的同一条：norm 会把 <b>x</b> 洗成能用的词，"
            "那是洗白不是拦下）",
            clean_expand_words("图书馆", {"is_campus_fact_query": True,
                                          "expanded_query": "<b>阅览室</b> 自习室"}) == ["自习室"])
    c.check("A13 词数上限 8：给 12 个只留前 8 个（超了**截断**不报错，施工单 §2.3；变异 M4 的靶子）",
            len(clean_expand_words("图书馆", _obj([f"阅览室{i:02d}" for i in range(12)]))) == 8)
    c.check("A14 字数预算 48：预算用完就截，剩下的词不要了。"
            "⚠️ 拿**七字**的词测（8 个就 56 字）—— 第一版用五字测试词，词数上限先绑住、"
            "预算那条永远走不到，M4-b 拆掉预算它照样全绿（死代码）",
            len("".join(clean_expand_words("图书馆", _obj([f"自习室{i:02d}号馆" for i in range(10)]))))
            <= 48)
    c.check("A15 分隔符三种写法同结果（空格 / 顿号 / JSON 数组）",
            clean_expand_words("图书馆", _obj(["阅览室", "自习室"]))
            == clean_expand_words("图书馆", {"is_campus_fact_query": True,
                                             "expanded_query": "阅览室、自习室"})
            == clean_expand_words("图书馆", {"is_campus_fact_query": True,
                                             "expanded_query": "阅览室,自习室"}))
    c.check("A16 大小写归一：原句里的 WiFi 与补出来的 wifi 算同一个词（不占预算、不重复）",
            clean_expand_words("WiFi 怎么连", _obj(["wifi", "无线网"])) == ["无线网"])
    c.check("A17 回话不是 JSON 对象 / 缺 expanded_query / 字段类型不对 → 空表（不抛）",
            clean_expand_words("图书馆", None) == []
            and clean_expand_words("图书馆", {"is_campus_fact_query": True}) == []
            and clean_expand_words("图书馆", {"is_campus_fact_query": True, "expanded_query": 7}) == []
            and clean_expand_words("图书馆", ["整个不是对象"]) == [])
    c.check("A18 只取 expanded_query：entities/synonyms 两个数组**不参与**（施工单 §2.3 明写）",
            clean_expand_words("图书馆", {"is_campus_fact_query": True, "entities": ["阅览室"],
                                          "synonyms": ["自习室"], "expanded_query": ""}) == [])
    c.check("A19 few-shot 五条示例还钉在提示词里（那是这个改写器唯一的规格说明，"
            "被人删掉就等于把验收口径改了）",
            all(f"输入：{q}" in _prompt_text() for q, _, _ in FEWSHOT)
            and "输入：明天上午有什么课" in _prompt_text())
    return c


def _prompt_text():
    from app.modules.faq import _QUERY_EXPAND_PROMPT
    return _QUERY_EXPAND_PROMPT


def batch_b_fallback():
    """B 批：一切失败路径必须"静默回退原 query"（施工单 §七.2 + 变异 M2/M3 的靶子）。"""
    from app.modules import faq

    c = Checker()
    title("B. 失败路径：没 Key / 超时 / 坏 JSON / 上游报错 —— 一律回空串，一个字不崩")
    q = "泳池多少钱一次？"

    fake, restore = use_fake(server_key=False)
    got = run(faq.query_expansion(q))
    c.check("B1 服务器没配 Key：连问都不问、回空串（「没有 Key 也能演示」那条口径）",
            got == "" and len(fake.expand_calls) == 0, str(fake.calls))
    restore()

    fake, restore = use_fake(reply="这个我不清楚", server_key=True)
    c.check("B2 模型回的不是 JSON（一句人话）→ 空串，不抛",
            run(faq.query_expansion(q)) == "" and len(fake.expand_calls) == 1)
    restore()

    fake, restore = use_fake(reply='{"is_campus_fact_query":tru', server_key=True)
    c.check("B3 半截 JSON（坏格式）→ 空串，不抛", run(faq.query_expansion(q)) == "")
    restore()

    fake, restore = use_fake(mode="boom", server_key=True)
    c.check("B4 上游报错（LLMError）→ 空串：报错**不许**冒到对话链路上（变异 M2 的靶子）",
            run(faq.query_expansion(q)) == "")
    restore()

    fake, restore = use_fake(mode="slow", server_key=True)
    saved = faq.QUERY_EXPAND_TIMEOUT
    faq.QUERY_EXPAND_TIMEOUT = 0.05
    t0 = time.perf_counter()
    got = run(faq.query_expansion(q))
    cost = time.perf_counter() - t0
    faq.QUERY_EXPAND_TIMEOUT = saved
    c.check("B5 超过保险丝 → 空串，而且**没有等满 0.25 秒**（超时是真的生效，不是形式上的 try）",
            got == "" and cost < 0.2, f"{round(cost * 1000)}ms")
    c.check("B5-bis 保险丝本身 ≤ 2.0 秒（调大它等于把兜底那一支重新拖回 4 秒；"
            "2026-10-24 实测 warm 一跳 506~1052ms、冷握手 1515ms 撞满 1.5s）",
            faq.QUERY_EXPAND_TIMEOUT <= 2.0, f"{faq.QUERY_EXPAND_TIMEOUT}s")
    restore()

    fake, restore = use_fake(reply=json.dumps({"is_campus_fact_query": True,
                                               "expanded_query": "游泳馆 游泳池 单次"},
                                              ensure_ascii=False), server_key=True)
    first = run(faq.query_expansion(q))
    second = run(faq.query_expansion(q))
    c.check("B6 成功过的那一句进缓存：第二次不再问模型，结果相同",
            first == second == "游泳馆 游泳池 单次" and len(fake.expand_calls) == 1,
            f"{len(fake.expand_calls)}")
    up = run(faq.query_expansion("泳池多少钱一次？"))
    c.check("B6-bis 缓存键归一（大小写/首尾空格不算两句话）：换一种写法也只问一次",
            up == first and len(fake.expand_calls) == 1, f"{len(fake.expand_calls)}")
    restore()

    fake, restore = use_fake(mode="slow", server_key=True)
    faq.QUERY_EXPAND_TIMEOUT = 0.05
    run(faq.query_expansion(q))
    run(faq.query_expansion(q))
    faq.QUERY_EXPAND_TIMEOUT = saved
    c.check("B7 失败**不进**缓存：管理员刚配好 Key 时同一句要立刻能用（这里两次都问了模型）",
            len(fake.expand_calls) == 2, f"{len(fake.expand_calls)}")
    restore()

    fake, restore = use_fake(reply=json.dumps({"is_campus_fact_query": True,
                                               "expanded_query": "游泳馆"}, ensure_ascii=False),
                             server_key=False)
    got = run(faq.query_expansion(q, {"api_key": "sk-学生自带"}))
    c.check("B8 服务器没 Key、学生自带 Key：这一照样要问，且花的是学生的钱（key_source=student）",
            got == "游泳馆" and len(fake.expand_calls) == 1)
    restore()

    with sandbox():
        n0 = len(_usage_rows())
        fake, restore = use_fake(server_key=False)
        run(faq.query_expansion("泳池多少钱一次？"))
        run(faq.query_expansion("图书馆闭馆吗"))
        n1 = len(_usage_rows())
        restore()
        c.check("B9 没 Key 时**一笔流水都不记**（不产生假 error 噪声，也不假装调过模型）",
                n1 == n0, f"{n0}->{n1}")

        fake, restore = use_fake(mode="boom", server_key=True)
        run(faq.query_expansion("泳池多少钱一次？"))
        rows = _usage_rows()
        restore()
        c.check("B10 真打了上游又失败：记一笔 status=error（这一单花的钱必须看得见）",
                bool(rows) and rows[-1].get("status") == "error",
                str(rows[-1].get("status") if rows else "没有流水"))

        fake, restore = use_fake(reply=json.dumps({"is_campus_fact_query": True,
                                                   "expanded_query": "游泳馆"}, ensure_ascii=False),
                                 server_key=True)
        run(faq.query_expansion("泳池多少钱一次？"))
        rows = _usage_rows()
        src = [r.get("key_source") for r in rows]
        restore()
        c.check("B11 用量里 user=内部:检索改写 这一行确实写进去了，且 key_source=server",
                bool(rows) and src[-1:] == ["server"], str(src[-1:]))

        c.check("B12 空问句不去问模型（也不写缓存）",
                run(faq.query_expansion("")) == "" and run(faq.query_expansion("   ")) == "")
        fake, restore = use_fake(reply=json.dumps({"is_campus_fact_query": True,
                                                   "expanded_query": "游泳馆"}, ensure_ascii=False),
                                 server_key=True)
        for i in range(140):
            run(faq.query_expansion(f"第{i}句无关问话图书馆"))
        c.check("B13 缓存有上限、会驱逐（进程不无限长；上限 128 是这一族"
                "「顺手写个无界 dict」的默认病）",
                len(faq._EXPAND_CACHE) <= faq._EXPAND_CACHE_MAX,
                f"{len(faq._EXPAND_CACHE)}/{faq._EXPAND_CACHE_MAX}")
        restore()
    return c


def batch_c_retrieval():
    """C 批：改写词进了检索面（两路各自的接法）+ 判空与渲染必须配对。"""
    from app.modules import faq
    from app.modules.files import _tokenize
    from app.store import save_upload

    c = Checker()
    title("C. 检索面：补的词只进匹配、不进文案；打错字那一圈不吃扩词")
    with sandbox():
        seed_kb(ENTRIES)
        save_upload("校园手册-生活篇.txt", DOC_LIFE, raw=DOC_LIFE.encode("utf-8"), purpose="kb")

        q = "泳池多少钱一次？"
        extra = "游泳馆 游泳池 单次"           # ← 2026-10-24 真模型给的就是这三个
        c.check("C1 改前形状：这句**两路都查不到**（学生看到的就该是「暂不清楚」）",
                faq.kb_has_answer(q) is False and faq._kb_parts(q) == ([], []))
        hits, docs = faq._kb_parts(q, extra)
        c.check("C2 改后形状：喂进实测补词 → 命中『游泳馆价格』那条（链路钉死，不靠模型心情）",
                [h["question"] for h in hits] == ["游泳馆价格"] and faq.kb_has_answer(q, extra))
        c.check("C2-bis 「校医院几点上班」补上泛词闸之后剩下的「医务室 门诊」仍查不到 "
                "→ 仍然老实说没查到（这条就是那颗误命中的反证）",
                faq.kb_has_answer("校医院几点上班", "医务室 门诊") is False)
        c.check("C3 配对：判空传了 extra、渲染也必须传 —— 不传就出现「说有、答起来没有」自打脸",
                faq.answer_for_student(q, extra).startswith("问：游泳馆价格")
                and faq.answer_for_student(q).startswith(faq.KB_MISS_PREFIX))
        out = faq.answer_for_student(q, extra)
        c.check("C4 补的词**一个字都不进**给学生看的文案（「单次」「游泳池」都没露出来）",
                "单次" not in out and "游泳池" not in out, out[:60])
        c.check("C5 miss 那句回显的还是**学生原句**（不是合并后的检索串）",
                q.rstrip("？") in faq.answer_for_student(q, "")
                or faq.answer_for_student(q, "").startswith(faq.KB_MISS_PREFIX))

        es, _d = faq._kb_parts("阅览处几点开", "图节馆 闭馆")
        c.check("C6 打错字那一圈**只看学生原句**，不吃补出来的词（「图节馆」与「图书馆」差一个字，"
                "若吃了扩词就会接走 —— 变异 M5 的靶子）", es == [], str(es))
        c.check("C6-bis 原句里真有手误时那一圈照旧管用（别把对的修坏）",
                [h["question"] for h in faq._kb_parts("图收馆几点开门")[0]] == ["图书馆开放时间"])

        from app.modules.files import doc_hits
        _e, d0 = faq._kb_parts("阅览室开到几点")            # 这句文档那一路**本来就有**块
        _e, d1 = faq._kb_parts("阅览室开到几点", "闭馆时间 图书馆")
        key = lambda rows: [(x["name"], x["i"], round(x["score"], 4)) for x in rows]
        c.check("C7 ★ 文档那一路**不许吃补词**（真机实测那颗答非所问的牙）：喂进「闭馆时间 图书馆」"
                "之后选出来的块与分数必须与原句那一次**逐字相同**；"
                "反向自证也做了 —— 真把补词喂进 `doc_hits`，结果就不一样（否则这条断言是空的）。"
                "8017 上「寒假什么时候放假」就是补词进 BM25 后被一块讲补考周的规定接走的"
                "（真实 30 个块里没有一处含 寒假/放假/开学/校历，贡献的是「时间」这种到处都是的二元组）",
                bool(d0) and key(d1) == key(d0)
                and key(doc_hits("阅览室开到几点 闭馆时间 图书馆")) != key(doc_hits("阅览室开到几点")),
                f"{key(d0)} vs {key(d1)}")

        hits2, _ = faq._kb_parts("图书馆几点关门？", "一句与原句毫无关系的长话 zzz")
        c.check("C8 原句**在前**是结构保证：补词再离谱也不许把原句的命中挤掉（变异：换成覆盖原句就红）",
                [h["question"] for h in hits2] == ["图书馆开放时间"])
        c.check("C8-bis extra 为空串 / 不传，两路结果逐字相同（= 与没有这个功能时一样）",
                faq._kb_parts("图书馆几点关门？") == faq._kb_parts("图书馆几点关门？", ""))

        c.check("C9 `_merged` 只加空格、不改原句（长度只会长不会短）",
                faq._merged("图书馆 几点", "") == "图书馆 几点"
                and faq._merged("图书馆", "  自习室  阅览室 ").endswith("自习室 阅览室"))
        c.check("C9-bis 合并串过的是同一个分词器，且补的词**只加词、不删词**"
                "（中文按二元组滑窗：「游泳馆」进来的是「游泳/泳馆」两个检索词，"
                "原句那批一个都没少）",
                set(_tokenize(q)) < set(_tokenize(faq._merged(q, "游泳馆 游泳池 单次")))
                and {"游泳", "泳馆"} <= set(_tokenize(faq._merged(q, "游泳馆"))))

        kb_before = open(data_path("kb.json"), "rb").read()
        fake, restore = use_fake(reply=json.dumps({"is_campus_fact_query": True,
                                                   "expanded_query": "游泳馆 游泳池 单次"},
                                                  ensure_ascii=False), server_key=True)
        run(faq.query_expansion(q))
        restore()
        c.check("C10 改写结果**不落库**：问过模型、检索过之后 kb.json 一个字节没变（施工单 §五.4）",
                open(data_path("kb.json"), "rb").read() == kb_before)

        c.check("C11 `search_kb`（给模型那一路）**不扩**：还是同步、还是只有一个入参（本单收窄的一处）",
                not inspect.iscoroutinefunction(faq.search_kb)
                and list(inspect.signature(faq.search_kb).parameters) == ["query"])
        c.check("C11-bis `query_expansion` 是 async，且 max_tokens=120 与超时都写在函数体里"
                "（施工单 §2.4 那两个参数；被删掉就是「改着改着不限长度」）",
                inspect.iscoroutinefunction(faq.query_expansion)
                and "max_tokens=120" in inspect.getsource(faq.query_expansion)
                and "timeout=QUERY_EXPAND_TIMEOUT" in inspect.getsource(faq.query_expansion))
    return c


def batch_d_chat():
    """D 批：两处调用点真发（走 /api/chat），并钉住"命中那一路一次模型都不问"。"""
    c = Checker()
    title("D. 真发：查不到才问模型；命中那一路与改前逐字相同")
    cli = make_client()               # ★ 必须在任何打桩**之前**（router 的类体注解要真客户端）

    def js(words, flag=True):
        return json.dumps({"is_campus_fact_query": flag, "expanded_query": " ".join(words)},
                          ensure_ascii=False)

    reply_swim = js(["游泳馆", "游泳池", "单次"])       # 实测那一轮给「泳池多少钱一次」的补词
    reply_cal = js(["寒假时间", "校历"])
    reply_clinic = js(["医务室", "门诊"])               # 泛词闸之后剩下的那两个（库里确实没有）
    reply_off = json.dumps({"is_campus_fact_query": False, "entities": [],
                            "synonyms": [], "expanded_query": ""}, ensure_ascii=False)

    with sandbox():
        seed_kb(ENTRIES)
        from app.store import save_upload
        save_upload("校园手册-生活篇.txt", DOC_LIFE, raw=DOC_LIFE.encode("utf-8"), purpose="kb")

        def say(msg, module="planner", sid="d57"):
            body = {"message": msg, "session_id": sid}
            if module:
                body["module"] = module      # ⚠️ 必须真发出去：日程栏那道闸认的是 `module_key == "planner"`
            d = cli.post("/api/chat", json=body).json()
            ans = d.get("answer") or ""
            phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
            return ans, phases, d

        # ⚠️ 判据认的是**轨迹里那句固定字样**（「再查一次（命中）」），不是"改写"两个字：
        #    第一版拿 "改写" 当锚点，四条一起假红 ——  phase 文案写的是"补同义/别名再查一次"。
        fake, restore = use_fake(reply=reply_swim, server_key=True)
        ans, ph, d = say("泳池多少钱一次？")
        c.check("D1 日程栏问「泳池多少钱一次？」：原句查不到 → 补同义词那一路接住，"
                "答案就是条目里那句价格",
                ans.startswith("📚") and "20 元" in ans and HIT in ph, f"{ph} / {ans[:40]}")
        c.check("D1-bis 这一句改写器只被叫了一次（判空与渲染共用同一份 extra，没问两遍）",
                len(fake.expand_calls) == 1, f"{len(fake.expand_calls)}")
        restore()

        fake, restore = use_fake(reply=reply_swim, server_key=True)
        ans, ph, d = say("图书馆几点关门？")
        c.check("D2 原句**就命中**的那句：轨迹仍是旧那一句「翻知识库（按台账答」，没换说法那一步",
                "翻知识库（按台账答" in ph and HIT not in ph, ph)
        c.check("D2-bis ★ 而且改写器**一次都没被叫**（这就是「只挂在查不到那一支」的行为判据；"
                "把它挪回检索之前，这条必红 —— 变异 M7）",
                len(fake.expand_calls) == 0, f"{len(fake.expand_calls)}")
        c.check("D2-ter 命中的内容与改前同形：条目答案在前、文档段落跟在后面",
                "8:00-22:00" in ans, ans[:50])
        restore()

        fake, restore = use_fake(reply=reply_clinic, server_key=True)
        ans, ph, d = say("校医院几点上班？")
        c.check("D3 日程栏问知识库里**真没有**的那条：补完还是查不到 → 不接管，交回模型"
                "（答不出就给模型，是这一档从 #41 就定下的口径）",
                HIT not in ph and not ans.startswith("📚"), f"{ph} / {ans[:36]}")
        c.check("D3-bis 但改写器**确实被叫过一次**（原句查不到才问，问了没查到就交回去 —— "
                "这一跳的代价实测 ~0.8 秒，记在快照的耗时一栏）",
                len(fake.expand_calls) == 1, f"{len(fake.expand_calls)}")
        restore()

        fake, restore = use_fake(reply=reply_cal, server_key=True)
        ans, ph, d = say("寒假什么时候放假", module="faq")
        c.check("D4 校园问答那一档（#42-A 那道闸）也接上了改写：靠「寒假时间」补中校历那条",
                HIT in ph and "1 月 12 日" in ans, f"{ph} / {ans[:40]}")
        c.check("D4-bis 这一句改写器同样只被叫了一次（两处调用点同一条规矩，没各写一份）",
                len(fake.expand_calls) == 1, f"{len(fake.expand_calls)}")
        restore()

        fake, restore = use_fake(reply=reply_clinic, server_key=True)
        ans, ph, d = say("寒假什么时候放假", module="faq")
        c.check("D5 同一档、补的词对不上：回的还是那句「没查到」（不许无中生有 —— "
                "这颗牙就是泛词闸逼出来的：真模型当时给「校医院几点上班」补的是"
                "「开放时间 营业时间 就诊时间」，被它接走的是『食堂营业时间』）",
                ans.startswith(faq_miss_prefix()) and HIT not in ph, f"{ph} / {ans[:36]}")
        restore()

        fake, restore = use_fake(reply=reply_off, server_key=True)
        ans, ph, d = say("寒假什么时候放假", module="faq")
        c.check("D5-bis 模型自己判「这不是校园事实」：一个字都没扩，回的还是那句没查到",
                ans.startswith(faq_miss_prefix()) and len(fake.expand_calls) == 1)
        restore()

        fake, restore = use_fake(reply=reply_off, server_key=True)
        ans, ph, d = say("明天上午有什么课？")
        c.check("D6 日程问话（模型也说 false）不被知识库接管：交回模型，不出现📚",
                HIT not in ph and not ans.startswith("📚"), f"{ph} / {ans[:36]}")
        c.check("D6-bis 这一句改写器被叫了 1 次 —— 摆出实测数字："
                "「有什么课」不在 `_SELF_SCHEDULE_WORDS` 里，所以确实多走了一跳；"
                "盯住这个数字，别让它长成「每句日程话都多一跳」",
                len(fake.expand_calls) == 1, f"{len(fake.expand_calls)}")
        restore()

        fake, restore = use_fake(reply=reply_swim, server_key=True)
        ans, ph, d = say("明天上午有什么课？")
        c.check("D7 边界如实钉（不是「应该这样」，是「现在这样」）：这一档对日程问话唯一的防线是"
                "模型那句 `is_campus_fact_query` —— 它要是给了 true 并补出名词，这句就会被知识库接走。"
                "真模型对四条反面句（明天有什么课/今天有什么安排/我饿了/加个跑步）都给了 false",
                HIT in ph and "游泳馆" in ans, f"{ph} / {ans[:36]}")
        restore()

        before = {p: open(data_path(p), "rb").read() for p in ("kb.json", "uploads.json")}
        fake, restore = use_fake(reply=reply_swim, server_key=True)
        say("泳池多少钱一次？", sid="d57b")
        after = {p: open(data_path(p), "rb").read() for p in ("kb.json", "uploads.json")}
        restore()
        c.check("D8 走完整条对话链路之后 kb.json / uploads.json 一个字节没变（红线：模型只出提案）",
                before == after)

        c.check("D9 管理端试检索探针**不扩**：`admin_kb_probe` 调 `_kb_parts(q)` 只传一个入参，"
                "函数里也不出现 query_expansion（§五.5 + C3 那笔「只读、不计流水」）",
                "_kb_parts(q)" in _main_src_admin_kb_probe()
                and "query_expansion" not in _main_src_admin_kb_probe())
    return c


def faq_miss_prefix():
    """那句"没查到"的开头（与 `main._KB_MISS_LINE` 同一份，测试从被测模块取，不抄）。"""
    from app.main import _KB_MISS_LINE
    return _KB_MISS_LINE[:8]


def _main_src_admin_kb_probe():
    import app.main as M
    return inspect.getsource(M.admin_kb_probe)


def batch_e_shape():
    """E 批：接线形状与"不做的事"（反向钉，防止下一位顺手把手伸进 BM25）。"""
    c = Checker()
    title("E. 形状与边界：两处调用点、一条顺序、四样不伸手")

    src = _src("app/main.py")
    lines = src.splitlines()

    call = [i for i, ln in enumerate(lines) if "await query_expansion(" in ln]
    c.check("E1 全仓只有**两处**调用点（校园问答栏 + 日程栏那道闸），抄第三处就是第四份账",
            len(call) == 2, str([i + 1 for i in call]))
    c.check("E2 两处都在「`if _kb_has_answer(message):`」之后（挂在「查不到」那一支里；"
            "顺序反了就变成每次都问模型）。窗口给 24 行 —— 上面那段注释就有七八行，"
            "窗太窄会把它挡在外面，判据就变成「注释有几行」了。"
            "⚠️ 前面必须加 `call and`：两处都摘掉时 `all([])` 是**真**，这条会变成"
            "「调用点没了也算顺序对」—— M8 第一轮就是这么漏过去的",
            bool(call) and all(any("_kb_has_answer(message):" in lines[j]
                                   for j in range(max(0, i - 24), i)) for i in call),
            str([i + 1 for i in call]))
    ask = [i for i, ln in enumerate(lines) if "if _campus_fact_ask(message)" in ln]
    what = [i for i, ln in enumerate(lines) if "if _asking_what_to_do(message)" in ln]
    c.check("E3 校园问答那一档仍在 `_asking_what_to_do` **之前**（#42-A 那条顺序约束没被这次改动挪掉）",
            ask and what and ask[0] < what[0], f"{ask[:1]} {what[:1]}")
    c.check("E4 两处调用点都把 extra 同时交给判空与渲染（`extra=_kb_x` 成对出现，不落单）",
            src.count("extra=_kb_x") == 4, str(src.count("extra=_kb_x")))

    import app.modules.files as F
    import app.modules.faq as Q
    import app.store as S
    c.check("E5 BM25 与分词**没被伸手**：`_bm25`/`_tokenize` 源码里不出现 extra/expand/改写",
            all(k not in inspect.getsource(F._bm25) + inspect.getsource(F._tokenize)
                for k in ("extra", "expand", "改写")))
    c.check("E6 条目判据没被伸手：`_entry_kws` 的入参与判据没变（本单只改喂进去的 query）。"
            "⚠️ 不能拿「源码里有没有 expand」当判据 —— 它本来就要调 `kb_expand` 补滑窗，"
            "那条牙会一路咬成假红（第一版就是这么红的）",
            list(inspect.signature(Q._entry_kws).parameters) == ["item", "typo"]
            and "extra" not in inspect.getsource(Q._entry_kws))
    c.check("E7 打错字那一圈的函数本体 `_kb_fuzzy_hit` 一个字没改（施工单 §五.3：它管错字、这单管同义）",
            "extra" not in inspect.signature(Q._kb_fuzzy_hit).parameters
            and not any(k in inspect.getsource(Q._kb_fuzzy_hit) for k in ("expand", "extra")))
    c.check("E8 词表只有一份：faq.py 不 import、也不另抄 `_KB_GENERIC`（判据跟着 store 走）",
            "_KB_GENERIC" not in inspect.getsource(Q) and "expand" in inspect.getsource(S))
    c.check("E9 两条闸是**两个函数**：#55 那条（存进条目的拆词）判据没被顺手共用或改掉"
            "（它的报错文案「模型没给出关键词」由 test_76 钉着）",
            S.clean_expand_words is not S.clean_split_words
            and "模型没给出关键词" in inspect.getsource(S.clean_split_words))
    c.check("E10 管理端界面上找不到这个能力（§五.5：学生侧透明即可）",
            "改写" not in _src("app/static/admin.html")
            and "expand" not in _src("app/static/admin.html").lower())
    c.check("E11 两处调用点都传了 `model_config`（学生自带的 Key 优先，不许悄悄花服务器的钱）",
            src.count("query_expansion(message, model_config)") == 2)
    return c


def _src(rel):
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *rel.split("/"))
    return open(p, encoding="utf-8").read()


BATCHES = (("A 批（闸的形状）", batch_a_gate),
           ("B 批（失败静默回退）", batch_b_fallback),
           ("C 批（检索面与配对）", batch_c_retrieval),
           ("D 批（真发两处调用点）", batch_d_chat),
           ("E 批（边界与反向钉）", batch_e_shape))


def main():
    global code
    print("test_78_kb_query_expand：#57 知识库查询改写（只动输入 / 查不到才问 / 失败静默回退）")
    print("=" * 60)
    # ⚠️ 别写成 `code = code or fn()...`（`or` 短路：第一批一红后面几批根本不跑，见 test_76 教训）
    rc = 0
    for name, fn in BATCHES:
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
