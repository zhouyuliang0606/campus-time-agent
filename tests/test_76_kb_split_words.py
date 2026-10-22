# -*- coding: utf-8 -*-
"""#55 的护栏：知识库关键词三件事（A 认派生词 / C 清残渣 / B 让模型按句意拆）+ D 管理端那颗按钮。

用户 2026-10-22 拍板「abc 都要做；b 拆需要模型按句意来拆，不用后端固定格式拆」。
现场那两条 `kb.json` 正好是一对样本（`健身房价格` 关键词是手写的「健身房」，
`游泳馆价格` 是留空兜底存进去的**整句**），所以每一批都拿这两条当靶子。

A 批 —— `_entry_kws` 认得「存进去的就是 `kb_head_words` 那几个词」。
    不认它会怎样：那条被当成「管理员手写」，享受不到 `kb_expand` 的 3 字/2 字片段 ⇒
    实测「游泳多少钱」直接查不到（就是用户报的那个形状）。**老数据不用迁移**，认出来当场活。
    ⚠️ 两圈都要走（精确 + 打错字），而**打错字那一圈不许吃片段** —— 片段是 n-gram 噪声，
    给它编辑距离容错实测会把「今天有什么课」按「今天星」接走（test_73 A9-bis 钉过）。
    ⚠️ n2 那颗牙：判「是不是派生词」时右边必须 `lower()`。存的词是**带原始大小写**的
    （`_kb_row` 直接存 `kb_head_words` 的结果），查的那份 lower 过 —— 不 lower 就是永远不相等，
    A 整节静默失效，表现与没改一模一样。A8/A9 用一条带 `WiFi` 的条目把这件事钉成行为。
B 批 —— 词表：「什么时候」「有没有」进 `_KB_STOP`（它们是**劈词**用的），
    「开放」「多少钱」「多久」进 `_KB_GENERIC`（它们是**整词过滤**用的）。
    ⚠️ 「多久」绝不能进 `_KB_STOP`：实测「图书馆能待多久」会被劈成 `['图书馆能待']` ——
    残渣比整句更坏，整片至少带完整语义，残渣会被 `kb_expand` 拆成噪声去匹配。
C 批 —— `store.clean_split_words`：审核模型给的建议词那一道闸（放 store 是因为判据跟着词表走）。
    每一道都是实测过会出错的形状，尤其「词 == 原句」必须先去标点再比。
D 批 —— `POST /api/admin/kb/split`：只产建议、**不写库**；失败一律 502 + 一句人话，
    **不许回退到规则拆**（那等于「模型挂了就悄悄换成固定格式拆」，管理员看不出区别）。
E 批 —— 管理端那颗按钮：结果落回**可编辑的框**、失败看得见、按钮标题不许写「智能」。

⚠️ 全程打桩模型，不联网；`make_client()` 必须在任何 `patch_llm` **之前**调一次
   （`router.py` 的类体注解 `DeepSeekClient | None` 是导入时求值的，先打桩再首次 import 就是 TypeError）。
"""
import json
import os

from _harness import (Checker, data_path, make_client, patch_llm, sandbox, seed_kb,
                      title)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(PROJ, "app", "main.py")
STORE = os.path.join(PROJ, "app", "store.py")
ADMIN = os.path.join(PROJ, "app", "static", "admin.html")
FAQ = os.path.join(PROJ, "app", "modules", "faq.py")

# 施工单 §2.4 那两张表 = 验收用例。下面每一行的期望值都是 2026-10-22 本机跑出来的原样，
# 不是抄施工单（抄来过一次：那一版把「游泳馆什么时候开放」写成还剩「开放」，实际已被泛词吃掉）。
HEAD_CASES = [
    ("考试周图书馆什么时候开门", ["考试周图书馆"]),
    ("图书馆什么时候关门", ["图书馆"]),
    ("游泳馆什么时候开放", ["游泳馆"]),
    ("宿舍有没有热水", ["宿舍", "热水"]),
]

# §2.4 第二张表 + 我实测补的三道形状（尖括号顺序、纯数字、截断）。
# 期望值同样是实测：`(词表, 人话原因)`，成功时原因为空串。
SPLIT_CASES = [
    ("游泳馆价格", ["游泳馆", "游泳", "泳池", "价格", "多少钱", "游泳馆价格"],
     ["游泳馆", "游泳", "泳池"]),
    ("考试周图书馆开到几点", ["图书馆", "考试周", "开放时间", "几点", "时间"],
     ["图书馆", "考试周", "开放时间"]),
    ("补办学生证要多久", ["学生证", "补办学生证", "补办", "多久"],
     ["学生证", "补办学生证", "补办"]),
    ("宿舍报修电话", ["宿舍", "报修", "电话"], ["宿舍", "报修"]),
]
DROP_ALL = "模型这次只拆出了原句或泛词，换个说法再试一次，或者手动填关键词。"
NO_LIST = "模型没给出关键词，请手动填写。"

code = 0


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _hits(query):
    """走学生那同一条路（`_kb_parts` 的第一路 = 问答条目命中），不另写一套匹配。"""
    from app.modules import faq
    return faq._kb_parts(query)[0]


def _q_list(query):
    return [h["question"] for h in _hits(query)]


def _js_fn(src, name):
    """按大括号配对抠出一个 JS 函数体（判「哪一句落在哪一个分支」得先看准分支）。"""
    i = src.index("function " + name)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    return src[i:]


def _split(client, body):
    return client.post("/api/admin/kb/split", json=body,
                       headers={"Content-Type": "application/json"})


def _kb_json():
    with open(data_path("kb.json"), "rb") as f:
        return f.read()


def _usage_rows():
    p = data_path("llm_usage.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _split_usage_rows():
    """只数拆词那一路的流水。

    ⚠️ 不许拿「流水总行数」当判据：`sandbox()` 是整树拷贝真实 `app/data`，
    真实流水本来就有几百条（而且沙箱还会继承上一支测试写的），总数在两台机器上是两个数。
    """
    return [r for r in _usage_rows() if r.get("user") == "内部:知识库拆词"]


# ---------------------------------------------------------------- A 批

def batch_a_derived_branch():
    c = Checker()
    title("A. #55-A：`_entry_kws` 认得「存进去的就是派生词」（老条目不用迁移）")
    from app import store
    from app.modules import faq

    swim = {"question": "游泳馆价格", "answer": "单次30", "keywords": ["游泳馆价格"]}
    gym = {"question": "健身房价格", "answer": "一个月159，单次30", "keywords": ["健身房"]}

    c.check("A1 前提钉住：这条存的关键词**正好等于** kb_head_words 的输出（所以才可能是派生词）",
            store.kb_head_words("游泳馆价格") == ["游泳馆价格"]
            and [k.lower() for k in swim["keywords"]] == store.kb_head_words("游泳馆价格"),
            store.kb_head_words("游泳馆价格"))
    c.check("A2 精确那一圈认它是派生词 ⇒ 补片段（改前它被当成手写词，「游泳多少钱」直接查不到）",
            faq._entry_kws(swim) == store.kb_expand(["游泳馆价格"])
            and "游泳" in faq._entry_kws(swim) and len(faq._entry_kws(swim)) > 1,
            faq._entry_kws(swim))
    c.check("A3 打错字那一圈只回整片本身（片段不许吃编辑距离容错 —— 两圈同一个函数，改一处等于只改了一半）",
            faq._entry_kws(swim, typo=True) == ["游泳馆价格"],
            faq._entry_kws(swim, typo=True))
    c.check("A4 手写那条一个字都不扩（「健身房」这条修复前后都只用它自己那一个词）",
            faq._entry_kws(gym) == ["健身房"] and faq._entry_kws(gym, typo=True) == ["健身房"],
            faq._entry_kws(gym))

    with sandbox():
        # 直接**摆数据**而不是走 `add_kb_entry`：验的就是「存量那种整句关键词」自愈，
        # 走一遍 add 等于让代码自己把词写好，那测的就不是老数据了。
        seed_kb([("游泳馆价格", "单次30", ["游泳馆价格"]),
                 ("健身房价格", "一个月159，单次30", ["健身房"])])
        c.check("A5 老条目自愈（施工单 §5.2.1）：不点任何按钮、「游泳多少钱」就命中『游泳馆价格』",
                _q_list("游泳多少钱") == ["游泳馆价格"], _q_list("游泳多少钱"))
        c.check("A6 同一句多一个词也要稳（「游泳多少钱一次」）",
                _q_list("游泳多少钱一次") == ["游泳馆价格"], _q_list("游泳多少钱一次"))
        c.check("A7 对照：不许放宽成「任何问价钱的话」——「食堂多少钱」两条都不命中"
                "（靠的是泛词表把「价格/多少钱/多少」挡在派生词外面）",
                _hits("食堂多少钱") == [], _q_list("食堂多少钱"))

        wifi = store.add_kb_entry("WiFi密码是多少", "见每栋楼公告。", [])
        c.check("A8 存的形状带**原始大小写**（`_kb_row` 直接把 kb_head_words 的结果写进去）—— "
                "这一条是 A9 那两颗牙存在的前提",
                wifi["keywords"] == ["WiFi密码是"], wifi["keywords"])
        c.check("A9 比较那一边必须 lower（n2）：这条同样被认成派生词、享受补片段。"
                "去掉右边那个 `.lower()` 之后 `hand != heads` 永远成立 ⇒ 整节静默失效、"
                "表现与没改一模一样（代码不报错，行为回去了）",
                faq._entry_kws(wifi) == store.kb_expand(["wifi密码是"])
                and len(faq._entry_kws(wifi)) > 1, faq._entry_kws(wifi))
        c.check("A9-bis 那条的打错字圈照旧只回整片（lower 过，但不补片段）",
                faq._entry_kws(wifi, typo=True) == ["wifi密码是"],
                faq._entry_kws(wifi, typo=True))

        two = {"question": "游泳馆价格", "answer": "x", "keywords": ["游泳馆", "价格"]}
        c.check("A10 判据是**整表相等**不是子集：管理员手写「游泳馆, 价格」时按手写处理、不补片段"
                "（他的判断优先，哪怕其中一个词正好是派生结果里的）",
                faq._entry_kws(two) == ["游泳馆", "价格"]
                and faq._entry_kws(two, typo=True) == ["游泳馆", "价格"], faq._entry_kws(two))

        c.check("A11 边界如实钉住：口语叫法「泳池」派生不出来 ⇒ 这条查不到 —— "
                "**这正是 B 存在的理由**（模型才知道学生管游泳馆叫泳池），不是 A 的回归",
                _hits("泳池价格") == []
                and faq._entry_kws({"question": "游泳馆价格", "keywords": ["游泳馆", "游泳", "泳池"]})
                == ["游泳馆", "游泳", "泳池"], _q_list("泳池价格"))
        c.check("A11-bis 管理员把词改成那样之后「泳池价格」就命中了（A/B 分工：A 救存量，B 管以后）",
                _q_list("泳池价格") == [] and any(
                    k and k in "泳池价格" for k in ["游泳馆", "游泳", "泳池"]),
                "存的还是整句那份时查不到，换成模型给的那三个词才查得到")

    return c


# ---------------------------------------------------------------- B 批

def batch_c_wordlists():
    c = Checker()
    title("B. #55-C：停用词清残渣（劈词）+ 泛词补三项（整词过滤）—— 两张表是两个函数，别混")
    from app import store

    for q, want in HEAD_CASES:
        c.check(f"B1 残渣清掉：kb_head_words({q})", store.kb_head_words(q) == want,
                f"实际={store.kb_head_words(q)}")
    c.check("B2 反向钉「多久」**不许**进 `_KB_STOP`：劈词会把「图书馆能待多久」劈成"
            "`['图书馆能待']` —— 残渣比整句更坏（整片带完整语义，残渣会被 kb_expand 拆成噪声）。"
            "这条是防下一位「顺手补齐」（施工单 §2.3）",
            "多久" not in store._KB_STOP
            and store.kb_head_words("图书馆能待多久") == ["图书馆能待多久"],
            store.kb_head_words("图书馆能待多久"))
    c.check("B3 但「多久」要在 `_KB_GENERIC`（那里只丢整词、不改变任何劈词结果）—— "
            "模型给「学生证 / 补办学生证 / 补办 / 多久」这四个词时最后那个得被扔掉",
            "多久" in store._KB_GENERIC and "开放" in store._KB_GENERIC
            and "多少钱" in store._KB_GENERIC, sorted(store._KB_GENERIC))
    c.check("B4 「什么时候」「有没有」进的是 `_KB_STOP`（劈词用），不是泛词表",
            "什么时候" in store._KB_STOP and "有没有" in store._KB_STOP,
            store._KB_STOP[-10:])
    c.check("B5 `_STOP_RE` 与 `_KB_STOP` 同源（改词表必须让正则一起长）："
            "每一个停用词都在正则里，缺一个就是「词表改了、劈词没改」的静默失效",
            all(w in store._STOP_RE for w in store._KB_STOP)
            and store._STOP_RE.count("|") == len(store._KB_STOP) - 1,
            "词表 %d 项 / 正则 %d 支" % (len(store._KB_STOP), store._STOP_RE.count("|") + 1))
    c.check("B6 正则按长度降序拼（「什么时候」必须赶在「什么」前面匹配，否则先劈成「时候」留残渣）",
            store._STOP_RE.split("|").index("什么时候") < store._STOP_RE.split("|").index("什么"),
            store._STOP_RE[:60])
    c.check("B7 泛词表只管自动派生，不覆盖管理员：手写「开放」照样原样存",
            store._kb_row("图书馆什么时候开放", "九点开。", ["开放"])["keywords"] == ["开放"],
            store._kb_row("图书馆什么时候开放", "九点开。", ["开放"])["keywords"])
    c.check("B8 留空时存的仍是**词片**（不是十几个碎片）：管理端那一屏读得完，"
            "而「查」那一圈会自己补片段（A2）",
            store._kb_row("考试周图书馆什么时候开门", "延长两小时。", [])["keywords"]
            == ["考试周图书馆"],
            store._kb_row("考试周图书馆什么时候开门", "延长两小时。", [])["keywords"])
    c.check("B9 「存」与「查」同源：`_kb_row` 存的那串，正是 `_entry_kws` 认派生词时算的那串"
            "（两边各写一遍分词就是第二套实现）",
            [k.lower() for k in store._kb_row("宿舍有没有热水", "食堂旁。", [])["keywords"]]
            == [k.lower() for k in store.kb_head_words("宿舍有没有热水")]
            and faq_heads_match(store),
            store._kb_row("宿舍有没有热水", "食堂旁。", [])["keywords"])
    return c


def faq_heads_match(store):
    """`faq._entry_kws` 里算 heads 用的必须是 `store.kb_head_words`（不是又一套滑窗）。"""
    src = _read(FAQ)
    return "kb_head_words(" in src and "_KB_STOP" not in src and "i:i + 3" not in src


# ---------------------------------------------------------------- C 批

def batch_c_gate():
    c = Checker()
    title("C. #55-B 的闸：`store.clean_split_words`（审核模型给的建议词，每道都是实测错过的形状）")
    from app import store

    for q, raw, want in SPLIT_CASES:
        got, why = store.clean_split_words(q, {"keywords": raw})
        c.check(f"C1 六行表（施工单 §2.4）：{raw} → {want}", got == want and why == "",
                f"实际={got} 原因={why}")
    c.check("C2 原句带标点也要判得出「这就是原句」：模型把带问号的原句还回来时整条丢 ⇒ **报错**，"
            "不许存（一旦存进去，A 那一层也不会再救它 —— 那时 hand != kb_head_words）",
            store.clean_split_words("游泳馆价格？", {"keywords": ["游泳馆价格？"]})
            == ([], DROP_ALL), store.clean_split_words("游泳馆价格？", {"keywords": ["游泳馆价格？"]}))
    c.check("C3 只给泛词 ⇒ 报错，不许存空关键词（空的会走 `_kb_row` 兜底，又变回整句，绕一圈回原点）",
            store.clean_split_words("游泳馆价格", {"keywords": ["多少钱", "怎么", "如何"]})
            == ([], DROP_ALL),
            store.clean_split_words("游泳馆价格", {"keywords": ["多少钱", "怎么", "如何"]}))
    c.check("C4 尖括号在**去标点之前**看：模型回一个包着 HTML 标签的「游泳馆」时要整条丢掉。"
            "放在 norm 之后它被洗成「b游泳馆b」（5 字、不是泛词）就**混进来了** —— 施工单那一版就是这个顺序",
            store.clean_split_words("游泳馆价格", {"keywords": ["<b>游泳馆</b>"]})
            == ([], DROP_ALL),
            store.clean_split_words("游泳馆价格", {"keywords": ["<b>游泳馆</b>"]}))
    for bad, label in ((["游泳馆"], "不是 dict"), ({}, "没有 keywords 键"),
                       ({"keywords": []}, "空列表")):
        c.check(f"C5 回包形状不对就说形状不对（{label}）：回「模型没给出关键词，请手动填写。」",
                store.clean_split_words("游泳馆价格", bad if label != "不是 dict"
                                        else ["游泳馆"]) == ([], NO_LIST),
                store.clean_split_words("游泳馆价格",
                                        ["游泳馆"] if label == "不是 dict" else bad))
    c.check("C6 顺序按模型给的来，**不是**按长度排序（拿 `_kb_clean` 顶替就会变成「长的在前 + 截断」，"
            "那是自动派生的口径，不是审核建议词的口径）",
            store.clean_split_words("游泳馆价格", {"keywords": ["泳池", "游泳馆", "水域"]})[0]
            == ["泳池", "游泳馆", "水域"],
            store.clean_split_words("游泳馆价格", {"keywords": ["泳池", "游泳馆", "水域"]})[0])
    c.check("C7 上限 6 个：施工单 §3.2 给模型的约束是 2~6 个，闸门按同一把尺子截",
            store.clean_split_words("游泳馆价格", {"keywords": ["游泳馆", "泳池", "游泳",
                                                                "戏水", "水域", "池子", "水上"]})[0]
            == ["游泳馆", "泳池", "游泳", "戏水", "水域", "池子"],
            store.clean_split_words("游泳馆价格", {"keywords": ["游泳馆", "泳池", "游泳",
                                                                "戏水", "水域", "池子", "水上"]})[0])
    c.check("C8 长度闸 2~8：单字「馆」与整句长串都丢（长串会把一条放宽到一整段话）",
            store.clean_split_words("游泳馆价格", {"keywords": ["馆", "游泳池开放时间段安排表", "游泳"]})[0]
            == ["游泳"],
            store.clean_split_words("游泳馆价格", {"keywords": ["馆", "游泳池开放时间段安排表", "游泳"]})[0])
    c.check("C9 重复的去掉（模型爱把全称和简称写成同一个词两次，含标点差异）",
            store.clean_split_words("游泳馆价格", {"keywords": ["游泳馆", "游泳馆", "游泳"]})[0]
            == ["游泳馆", "游泳"],
            store.clean_split_words("游泳馆价格", {"keywords": ["游泳馆", "游泳馆", "游泳"]})[0])
    c.check("C10 纯数字不是检索关键词：模型回「12」这种时丢掉 —— 它会把任何带 12 的问句"
            "（「12点」「2026年」）都接走，与泛词同一种放宽（实测旧写法会留下「12」）",
            store.clean_split_words("游泳馆价格", {"keywords": ["12"]}) == ([], DROP_ALL)
            and store.clean_split_words("游泳馆价格", {"keywords": [None, 12, "2026", "游泳"]})[0]
            == ["游泳"],
            store.clean_split_words("游泳馆价格", {"keywords": ["12"]}))
    c.check("C11 闸门在 `store`（公开名），main 只是调用它 —— 判据跟着词表走，"
            "别在路由里再写一份 `_KB_STOP` 的判断",
            "def clean_split_words(" in _read(STORE)
            and "clean_split_words" in _read(MAIN)
            and "_KB_GENERIC" not in _read(MAIN),
            "store 有定义 / main 有调用 / main 不碰私有词表")
    return c


# ---------------------------------------------------------------- D 批

class _BoomClient:
    """把模型出口换成「一调就抛 LLMError」，验失败那一支（不联网）。"""

    def __init__(self, *a, **k):
        pass

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        from app.llm.client import LLMError
        raise LLMError("上游 500：测试造的模型故障", kind="上游")


def batch_d_endpoint():
    c = Checker()
    title("D. #55-B 的接口：只产建议、不写库、失败不回退规则拆")
    with sandbox():
        kb0 = _kb_json()
        cli = make_client()                     # ★ 先 import app.main，再打桩（见文件头 ⚠️）

        restore = patch_llm('{"keywords": ["游泳馆", "游泳", "泳池", "价格"]}')
        before = len(_split_usage_rows())
        r = _split(cli, {"question": "游泳馆价格"})
        body = r.json()
        c.check("D1 正常那一支：200 + 闸门过后的实体词（模型给的第 4 个「价格」是泛词，被丢了）",
                r.status_code == 200 and body.get("keywords") == ["游泳馆", "游泳", "泳池"],
                f"{r.status_code} {r.text[:90]}")
        c.check("D2 用量记一笔（「内部:知识库拆词」单列一行，管理员数得出拆了多少次）",
                len(_split_usage_rows()) == before + 1,
                f"{before} → {len(_split_usage_rows())}")
        c.check("D3 只产建议：**一个字节都不写库**（落库仍走 POST /api/admin/kb，模型没有写权）",
                _kb_json() == kb0, "kb.json 变了")
        restore()

        restore = patch_llm('{"keywords": ["游泳馆", "游泳", "泳池"]}')
        for q, want in (("", "先把问题写进去"), ("   ", "先把问题写进去"), (None, "先把问题写进去")):
            r = _split(cli, {} if q is None else {"question": q})
            c.check(f"D4 空问题回 400 + 一句人话（{q!r}）：不许「成功地拆出 0 个词」",
                    r.status_code == 400 and want in r.json().get("error", ""),
                    f"{r.status_code} {r.text[:80]}")
        r = _split(cli, {"question": "图" * 101})
        c.check("D5 超 100 字回 400 并说清为什么（那种不是「一个学生问题」，是段落，该进文档知识库）",
                r.status_code == 400 and "100" in r.json().get("error", ""),
                f"{r.status_code} {r.text[:90]}")
        r = _split(cli, {"question": "图" * 100})
        c.check("D5-bis 对照：正好 100 字放行（闸是「太长」，不是「非空」）",
                r.status_code == 200, f"{r.status_code} {r.text[:60]}")
        r = cli.post("/api/admin/kb/split", json=["游泳馆价格"])
        c.check("D6 body 不是对象（传了个数组）也不许 500：按「没写问题」回 400",
                r.status_code == 400, f"{r.status_code} {r.text[:80]}")
        r = cli.post("/api/admin/kb/split", data=b"\xff\xfe bad",
                     headers={"Content-Type": "application/json"})
        c.check("D6-bis 非法 UTF-8 走全局那道闸（#22）：400 一句人话，不是 traceback",
                r.status_code == 400 and "请求体" in r.json().get("error", ""),
                f"{r.status_code} {r.text[:90]}")
        restore()

        restore = patch_llm('{"keywords": ["游泳馆价格", "多少钱", "价格"]}')
        r = _split(cli, {"question": "游泳馆价格"})
        c.check("D7 模型只回原句 + 泛词 ⇒ 502 + 闸门那句人话，**回包里没有 keywords**"
                "（前端就不会把框清空）",
                r.status_code == 502 and "keywords" not in r.json()
                and r.json().get("error") == DROP_ALL, f"{r.status_code} {r.text[:110]}")
        c.check("D7-bis 这种「拆不出来」照样记一笔 ok（账记的是模型调用，不是拆词结果）",
                _split_usage_rows()[-1].get("status") == "ok", _split_usage_rows()[-1])
        restore()

        restore = patch_llm("我觉得可以用游泳馆这个词")
        r = _split(cli, {"question": "游泳馆价格"})
        c.check("D8 模型回的不是 JSON ⇒ 502 +「模型没给出关键词，请手动填写。」（不许 500）",
                r.status_code == 502 and r.json().get("error") == NO_LIST,
                f"{r.status_code} {r.text[:110]}")
        restore()

        import app.llm.client as _client
        old = _client.DeepSeekClient
        _client.DeepSeekClient = _BoomClient
        try:
            before = len(_split_usage_rows())
            r = _split(cli, {"question": "游泳馆价格"})
        finally:
            _client.DeepSeekClient = old
        c.check("D9 模型真不可用 ⇒ 502 + `_say_llm_error` 那句人话，"
                "且**不许回退到 `derive_kb_keywords`**（n5：那等于「模型挂了就悄悄换成固定格式拆」，"
                "管理员看不出区别，而规则对「游泳馆价格」只会还回整句 —— 正是这条链路要治的 bug）",
                r.status_code == 502 and "keywords" not in r.json()
                and "游泳馆价格" not in r.json().get("error", ""),
                f"{r.status_code} {r.text[:110]}")
        c.check("D9-bis 失败也记账（status=error）——「用量统计」里那行才是排查的入口",
                len(_split_usage_rows()) == before + 1
                and _split_usage_rows()[-1].get("status") == "error",
                _split_usage_rows()[-1])

        noauth = make_client(admin_token=None)
        r = _split(noauth, {"question": "游泳馆价格"})
        c.check("D10 不带头进不去（401/403）：它是管理员的自检工具，不是第二条学生入口",
                r.status_code in (401, 403), f"{r.status_code} {r.text[:80]}")
        c.check("D10-bis 而且那道门就写在这个函数**自己头上**（装饰器在 `async def` 之前那一行，"
                "不是隔壁函数的）—— 只看「这一句在文件里」会放过挂错位置的那种红",
                "dependencies=[Depends(verify_admin)]" in _decorator_of(MAIN, "admin_kb_split"),
                _decorator_of(MAIN, "admin_kb_split").strip()[:90])
        c.check("D11 只有一处拆词接口（别长第二个免口令的口子出来）",
                _read(MAIN).count('"/api/admin/kb/split"') == 1,
                _read(MAIN).count('"/api/admin/kb/split"'))
        c.check("D12 max_tokens 钉在 120（给大了模型开始解释「为什么这么拆」，同判定层那一手）",
                "max_tokens=120" in _slice(MAIN, "async def admin_kb_split"),
                "")
        c.check("D13 抠 JSON 复用 `_extract_json_obj`，函数体里没有第二套自造的解析"
                "（`json.loads` 直接吃模型回话是这条链路的经典崩法）",
                "_extract_json_obj(" in _slice(MAIN, "async def admin_kb_split")
                and "json.loads" not in _slice(MAIN, "async def admin_kb_split"),
                "")
        c.check("D14 提示词三条禁令都在：不许整句、不许泛词、只输出一个 JSON（少一条模型就开始写文章）",
                all(k in _KB_SPLIT_PROMPT() for k in ("禁止", "原句", "泛词", "只输出一个 JSON")),
                "")
        c.check("D14-bis 提示词里的两个数量约束与闸门那把尺子一致（2~8 字 / 一共 2~6 个）",
                "2~8" in _KB_SPLIT_PROMPT() and "2~6" in _KB_SPLIT_PROMPT(), "")
        c.check("D15 拆完之后整棵树指纹仍未变（连「失败那几次」也没写过任何数据）",
                _kb_json() == kb0, "kb.json 变了")
    return c


def _KB_SPLIT_PROMPT():
    from app import main as M
    return M._KB_SPLIT_PROMPT


def _decorator_of(path, func_name):
    """抠出「紧贴在这个函数头上的那一段装饰器」（从它往上找到的第一个 `@app` 起）。

    为什么单独写这个：`dependencies=[Depends(verify_admin)]` 写在**哪一个**函数头上，
    是"这条口挂没挂门"的判据；切片要是按"函数体往后"取，装饰器根本不在窗口里（我就红过一次）。
    """
    src = _read(path)
    i = src.index("async def " + func_name)
    j = src.rindex("\n@app", 0, i)
    return src[j + 1:i]


def _slice(path, anchor, back=0):
    """从「这一行开始」切到下一个 `@app` 之前（判某一句落在**哪一个路由**里）。

    ⚠️ 锚点必须带左括号或足够长：`async def admin_kb_split` 这种名字互为前缀的邻居
    （`admin_kb_split` vs 别的 `admin_kb_...`）用短锚点会切错窗口。
    """
    src = _read(path)
    i = src.index(anchor)
    j = src.find("\n@app", i + len(anchor))
    return src[max(0, i - back):j if j != -1 else len(src)]


# ---------------------------------------------------------------- E 批

def batch_e_admin_ui():
    c = Checker()
    title("E. #55-D 那颗按钮：结果落回可编辑的框、失败看得见、标题不许写「智能」")
    html = _read(ADMIN)
    fn = _js_fn(html, "splitKB")

    c.check("E1 按钮 + 绑定 + 输入框三样都在，且按钮写 type=button（否则它在表单里会顺手提交整张表）",
            'id="kbSplit"' in html and 'addEventListener("click", splitKB)' in html
            and 'id="kk"' in html and 'type="button">✨ 自动拆词' in html, "")
    # ⚠️ 取的是**按钮自己的文案**，不是整页：我这页有一行注释正好在解释"为什么不叫智能拆词"，
    #    按整页判「智能」有没有出现会被自己的注释绊红（红线 4c 那一族，这轮是第三次撞）。
    i_btn = html.index('id="kbSplit"')
    btn_label = html[i_btn:html.index("</button>", i_btn)]
    btn_label = btn_label[btn_label.index(">") + 1:]
    c.check("E2 按钮**自己的文案**是「✨ 自动拆词」，不带「智能」二字：管理员得能判断该不该改，"
            "「智能」是把判断替他做了",
            "自动拆词" in btn_label and "智能" not in btn_label and "✨" in btn_label,
            btn_label.strip())
    c.check("E3 结果落回**可编辑的关键词框**（`box.value = ...`），不直接落库："
            "模型只出提案这条红线在按钮上也要成立",
            "box.value = r.keywords.join" in fn and "/api/admin/kb/split" in fn,
            fn[:60])
    c.check("E4 而且函数体里没有第二个写库调用（不许「拆完顺手替我存一条」）",
            fn.count("adminFetch(") == 1 and '"/api/admin/kb"' not in fn,
            fn.count("adminFetch("))
    c.check("E5 `adminFetch` 回的是 Response ⇒ 必须 `.json()`（施工单那段是旧形态，照抄就永远读不到词）",
            "await (await adminFetch(" in fn and ")).json()" in fn.replace("\r", ""),
            fn[fn.index("const r ="):][:70])
    c.check("E6 失败句固定带「（可以手动填关键词）」并前缀 ❌（要能确定拿到的是句意结果还是根本没拿到）",
            'st.textContent = "❌ 拆词没成功："' in fn and "可以手动填关键词" in fn, "")
    c.check("E7 失败**不许**拿规则拆的结果顶上（函数体里没有第二个数据来源）",
            "/kb/split" in fn and "derive" not in fn and "keywords 兜底" not in fn, "")
    # ⚠️ 这两句必须是**活代码**：变异 n8 把它们前面加 `//` 之后，"文件里有没有这句话"照样成立
    #    —— 注释掉的语句算数，是红线 4c 那一族（我这轮第三次撞，第二次是自己写的注释）。
    def _live(needle):
        for ln in fn.splitlines():
            if needle in ln and not ln.strip().startswith("//"):
                return ln
        return ""

    dis, res = _live("btn.disabled = true"), _live("btn.disabled = false")
    c.check("E8 一次调用几秒：禁用 + finally 恢复成对，且两句都是活代码"
            "（只禁不还 = 按钮点一次就死；写成 `// btn.disabled = true` 也不算）",
            bool(dis) and bool(res) and "finally" in fn and fn.index(dis) < fn.index("try {"),
            (dis.strip() or "(没有活着的那一行)") + " | " + (res.strip() or "(没有活着的恢复行)"))
    c.check("E9 问题为空时**先早退**，一次模型都不发（那是白烧的钱，也是一条错位的错误提示）",
            'if (!q) {' in fn and fn.index('if (!q) {') < fn.index("adminFetch("), "")
    c.check("E10 成功那句把「要不要改」说在前面（「可以改，改完点「添加条目」才生效」）",
            "可以改" in fn and "添加条目" in fn, fn[fn.index('st.textContent = "✨ 拆出')][:50])
    c.check("E11 关键词行是 flex 容器：输入框 flex:1、按钮 flex:none（CSS 与 HTML 的**配对**，"
            "只钉「两个字符串都在文件里」救不了按钮浮到下一行）",
            ".kk-row { display: flex" in html and ".kk-row input { flex: 1" in html
            and html.index('<div class="kk-row">') < html.index('id="kk"')
            and html.index('id="kk"') < html.index('id="kbSplit"') < html.index("</div>", html.index('id="kbSplit"')),
            "")
    c.check("E12 面板那句提示与 label 说的是同一件事（都在指「先点拆词、留空只是按原文提取」）",
            "留空按问题原文提取" in html and "自动从问题里提取" not in html, "")
    return c


BATCHES = [("A 批（派生词那一支）", batch_a_derived_branch),
           ("B 批（两张词表各管各的）", batch_c_wordlists),
           ("C 批（模型建议词的闸门）", batch_c_gate),
           ("D 批（拆词接口）", batch_d_endpoint),
           ("E 批（管理端那颗按钮）", batch_e_admin_ui)]


def main():
    global code
    print("test_76_kb_split_words：#55 知识库关键词（派生词 / 词表 / 模型拆词）")
    print("=" * 60)
    # ⚠️ 别把这里写成 `code = code or fn().summary(name)`（本仓库几份护栏的模板那一行就这么写的）：
    #    `or` 会短路 —— 第一批一红，后面几批**根本不会跑**，输出看着"通过 13 项"其实是没测。
    #    变异验证要靠后面那几批抓红，被这一行吞掉就是"变异没生效"里最难查的一种。
    rc = 0
    for name, fn in BATCHES:
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
