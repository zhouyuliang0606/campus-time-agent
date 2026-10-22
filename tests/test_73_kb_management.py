# -*- coding: utf-8 -*-
"""#46 的护栏：知识库「加了却查不到」这条链要闭环（条目匹配原文 + 面板不静默 + 能自己试查）。

用户 2026-10-20 的原话：「知识库管理的模块……走不通」「我添加进去，切块是我手动切的，会读不到，
即使学生问的原句也识别不到」。复算之后病根是三件不同的事，本文件按批钉：

A（46-A）**匹配判据只看 `keywords`，`question` 一个字符都不参与**，而管理端那一栏可以留空
  （只校验问题/答案）⇒ 留空 = 这条永远不可达，面板还回一句「✅ 已存」。
  修法是"存的时候兜底 + 查的时候兜底"两处都接（只接一处：救不了老数据 / 或列表里看不出靠什么命中）。
B（46-B）**「知识库」面板读失败是静默的**：`loadKB` 没 catch，而 `adminFetch` 非 2xx 会抛 ⇒
  页面停在上一次的样子；首次加载失败时屏幕上一直挂着「知识库还是空的」——
  口令没设 / 口令不对 / 接口 500 看起来全都像"库里没有东西"。同页 `loadDocs` 是有 catch 的。
C（46-C）**管理员没有"验一下能不能查到"的入口**：只能切到学生端问一句，"查不到"变成一件靠猜的事。

⚠️ 全程不依赖大模型：A 批打的是 `store` / `faq._kb_parts` 这两层，C 批打的是 `/api/admin/kb/probe`。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, title  # noqa: E402

code = 0


def _src(rel: str) -> str:
    return open(os.path.join(PROJ_DIR, rel), encoding="utf-8").read()


def _strip(src: str) -> str:
    """剥掉 `#` 注释再判"有没有这段代码"——我自己写的注释里正好在解释旧写法（红线 4c 那一族）。"""
    out = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(ln.split("#")[0])
    return "\n".join(out)


def _seed_kb(entries):
    with open(data_path("kb.json"), "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False)


def _hits(query):
    from app.modules import faq
    return faq._kb_parts(query)[0]


# 派生结果**全部是 2026-10-20 本机跑出来的原样**（施工单 §一 那张表里
# 「考试周图书馆开到几点」那一行把 `书馆开` 写成了 `馆开`，照抄就是一条永远对不上的断言）。
DERIVE_CASES = [
    ("图书馆几点开门", ["图书馆", "图书", "书馆"]),
    ("宿舍晚上几点断电？", ["宿舍晚上", "宿舍晚", "舍晚上", "宿舍", "舍晚", "晚上"]),
    ("校车在哪里坐、几点发车？", ["校车"]),
    ("游泳馆怎么预约", ["游泳馆", "游泳", "泳馆"]),
    ("补办学生证多少钱", ["补办学生证", "补办学", "办学生", "学生证", "补办", "办学", "学生", "生证"]),
    ("考试周图书馆开到几点", ["考试周图书馆开到", "考试周", "试周图", "周图书", "图书馆", "书馆开",
                              "馆开到", "考试", "试周", "周图", "图书", "书馆"]),
]


def batch_a_derive():
    c = Checker()
    title("A. 46-A：关键词留空也能用原句查到（存的一兜 + 查的一兜）")
    from app import store
    from app.modules import faq

    for q, want in DERIVE_CASES:
        got = store.derive_kb_keywords(q)
        c.check(f"A1 派生输出实测钉住：{q}", got == want, f"实际={got}")

    c.check("A1-bis 词片口径（`pieces=True`，打错字那一圈用的）只剩整片",
            store.derive_kb_keywords("图书馆几点开门", pieces=True) == ["图书馆"]
            and store.derive_kb_keywords("考试周图书馆开到几点", pieces=True) == ["考试周图书馆开到"],
            store.derive_kb_keywords("考试周图书馆开到几点", pieces=True))

    with sandbox():
        # 顺序是有讲究的：**先跑检索那三条**（表里只有游泳馆那一条），再往里加别的条目。
        # 反过来的话 A7 会被自己加的那条污染过 —— 我第一版就是这么红的：
        # A4 故意存了一条手写关键词含「开门」的，于是「食堂几点开门」被它接走，
        # 看着像泛词表失效，实际是我的料放错了位置。
        _seed_kb([])
        a1 = store.add_kb_entry("游泳馆怎么预约", "游泳馆在体育馆西侧，公众号上预约。", [])
        c.check("A2 存的时候兜底：留空存进去的是**词片**（不是十几个碎片，管理端那一屏才读得完）",
                a1["keywords"] == ["游泳馆"], a1["keywords"])
        c.check("A5 原句能查到（用户最初那句投诉）", len(_hits("游泳馆怎么预约")) == 1, _hits("游泳馆怎么预约"))
        c.check("A6 换个说法也查到（靠派生补的片段，不是靠运气）",
                len(_hits("游泳馆预约")) == 1 and len(_hits("体育馆里的游泳馆几点开")) == 1,
                len(_hits("游泳馆预约")))
        # 对照必须**拿带泛词的条目**来验：第一版这里只摆了『游泳馆』那条就问「食堂几点开门」，
        # 看着像对照，其实把 `_KB_GENERIC` 整张清空也不会红（变异 N3 就是这么被抓出来的）。
        store.add_kb_entry("图书馆几点开门", "平时到二十二点三十分。", [])
        c.check("A7 对照：「食堂几点开门」不许被『图书馆几点开门』接走（靠的是派生时丢掉泛词「开门」）",
                len(_hits("食堂几点开门")) == 0, _hits("食堂几点开门"))
        c.check("A7-bis 同一条换个话题问照样要命中（别把闸写成「这条永远查不到」）",
                len(_hits("图书馆在哪里")) == 1, len(_hits("图书馆在哪里")))

        store.add_kb_entry("宿舍晚上几点断电", "十一点半熄灯。", [])
        u1 = store.update_kb_entry(1, "宿舍晚上几点断电", "十一点半熄灯。", [])
        c.check("A3 修改那条也兜底（只接 add 不接 update = 改了一半，编辑一次就把词弄回空的）",
                u1["keywords"] == ["宿舍晚上"], u1["keywords"])
        h1 = store.add_kb_entry("校园网密码怎么改", "在自助平台改。", ["校园网", "开门"])
        c.check("A4 手写的关键词一个字都不动（泛词表只管自动派生，不覆盖管理员的判断）",
                h1["keywords"] == ["校园网", "开门"], h1["keywords"])

        # 打错字那一圈（施工单只点了精确那一处，实测第二圈同样只看 keywords）
        _seed_kb([{"question": "图书馆几点开门", "answer": "七点半至二十二点半。", "keywords": []}])
        c.check("A8 存量那种空关键词的老条目**不用迁移**，当场活过来（查的那一兜才是救老数据的）",
                len(_hits("图书馆几点开门")) == 1 and len(_hits("图书馆在哪里")) == 1,
                len(_hits("图书馆在哪里")))
        c.check("A9 打错字那一圈同样接住了（「图收馆」不含任何派生词 ⇒ 只有第二圈能命中）",
                len(_hits("图收馆几点关门")) == 1, _hits("图收馆几点关门"))
        c.check("A9-bis 但那一圈只吃**词片**：片段不许享受编辑距离容错"
                "（否则「今天星期几」的片段「今天星」会把「今天有什么课」接走）",
                faq._entry_kws({"question": "今天星期几", "keywords": []}, typo=True) == ["今天星期几"]
                and "今天星" in faq._entry_kws({"question": "今天星期几", "keywords": []}),
                faq._entry_kws({"question": "今天星期几", "keywords": []}, typo=True))

        _seed_kb([{"question": "今天星期几", "answer": "看日历。", "keywords": []}])
        c.check("A10 「今天有什么课」不许被『今天星期几』那条接走（时间泛词实测补进表里的那一颗牙）",
                len(_hits("今天有什么课")) == 0, _hits("今天有什么课"))
        c.check("A10-bis 对照：这条没被判死，问『这周星期几』照样查到",
                len(_hits("这周星期几")) == 1, _hits("这周星期几"))

        # #55-A 改口（这条原来是 `keywords=["校园网密码"]`，而那一串**恰好等于**
        # `kb_head_words("校园网密码怎么改")` ⇒ 按 #55 的规则它就是"派生词"，会被补片段。
        # 数据里没有"这词是谁写的"这个信息，二者不可兼得 —— 所以把"真手写"的夹具换成一份
        # 派生结果里不会出现的清单（两个词，其中一个是代码拆不出来的说法），
        # 另加一条把这个边界**钉住**（A11-bis），别让下一位当新 bug 去"修"。
        hand = {"question": "校园网密码怎么改", "answer": "B", "keywords": ["校园网", "自助平台"]}
        c.check("A11 手写的关键词**不被补成片段**（给他补片段等于代码悄悄把范围撑大，那是覆盖他的判断），"
                "也不被泛词表删掉（「密码」在 `_KB_GENERIC` 里，手写就用）",
                faq._entry_kws(hand) == ["校园网", "自助平台"], faq._entry_kws(hand))
        one = {"question": "校园网密码怎么改", "answer": "B", "keywords": ["校园网密码"]}
        c.check("A11-bis 边界（#55-A 的代价，如实钉住）：手写那一个词**正好等于**整片派生结果时，"
                "数据里没有任何东西能区分这两种来源 ⇒ 它按派生处理、享受补片段。这不是回归，"
                "是「整表相等」这条判据的另一面（要区分只能给条目加一个「词是谁写的」字段）",
                faq._entry_kws(one) == store.kb_expand(["校园网密码"])
                and "校园网" in faq._entry_kws(one) and len(faq._entry_kws(one)) > 1,
                faq._entry_kws(one))
        c.check("A11-ter 同一份词在「打错字」那一圈**只用整片本身**（滑窗碎片不许吃编辑距离容错）",
                faq._entry_kws(one, typo=True) == ["校园网密码"], faq._entry_kws(one, typo=True))

    bare = _strip(_src("app/modules/faq.py"))
    c.check("A12 整族扫：`faq` 里读 `keywords` 的那两圈都改走 `_entry_kws`，没有第三处各写一版",
            bare.count("item.get(\"keywords\"") == 1 and bare.count("_entry_kws(item") >= 2,
            "get(keywords) %d 处 / _entry_kws %d 处"
            % (bare.count("item.get(\"keywords\""), bare.count("_entry_kws(item")))
    c.check("A13 派生只有一份实现（在 `store`）：`faq` 里没有第二套滑窗/停用词",
            "_KB_STOP" not in bare and "i:i + 3" not in bare and "derive_kb_keywords" not in bare,
            "faq 里出现 _KB_STOP=%s 滑窗=%s" % ("_KB_STOP" in bare, "i:i + 3" in bare))
    st = _strip(_src("app/store.py"))
    c.check("A14 兜底两处都接上了（`_kb_row` 存 + `_entry_kws` 查），且 `_kb_row` 是 add/update 共用的那一个",
            st.count("def _kb_row") == 1 and st.count("_kb_row(question, answer, keywords)") == 2,
            "_kb_row 定义 %d 处、调用 %d 处" % (st.count("def _kb_row"),
                                               st.count("_kb_row(question, answer, keywords)")))
    html = _src("app/static/admin.html")
    # A15 从"钉那句话"改成"钉这一行说了哪两件事"（#55 §3.4）。
    # 原来那句「留空则自动从问题里提取」在承诺一件规则层对**短语**做不到的事：
    # 实测「游泳馆价格」没有虚词可劈，提取结果就是原句整串 —— 管理员照它留空，
    # 条目存进去关键词=原句，学生换个说法（「游泳多少钱」）就查不到。
    # ⚠️ 取的是**带「关键词」+「留空」的那一行 <label>**，不是整页也不是第一处「留空」：
    #    这页有三处「留空」（API Key 那栏、关键词 label、输入框 placeholder），
    #    按整页判存在 or 按"第一处带留空的 label"取都会量错地方 —— 我第一版就取到了 API Key 那行。
    label = next((ln for ln in html.splitlines()
                  if "<label>" in ln and "关键词" in ln and "留空" in ln), "")
    c.check("A15 关键词那一栏的说明写清了两件事：留空只**按问题原文**提取，想要实体词建议先点「自动拆词」"
            "（不写清，下一个人还会以为留空最省事）",
            "问题原文" in label and "拆词" in label and "自动从问题里提取" not in label,
            "那一行是：" + (label.strip() or "(整页没有带「留空」的 <label>)"))
    c.check("A16 畸形输入不许炸（空串 / 只有标点 / None）；数字那种照旧当词片（不断言它长什么样，只断不抛）",
            store.derive_kb_keywords("") == [] and store.derive_kb_keywords("？？？") == []
            and store.derive_kb_keywords(None) == [] and store.kb_head_words("2026年社团报名") != []
            and store.derive_kb_keywords(12345) != [],
            [store.derive_kb_keywords(x) for x in ("", "？？？", None)]
            + [store.kb_head_words("2026年社团报名")])
    return c


def _js_fn(src: str, name: str) -> str:
    """按大括号配对抠出一个 JS 函数体（判"哪一句落在哪一个分支"得先看准分支）。"""
    i = src.index("function " + name)
    j = src.index("{", i)
    depth, k = 0, j
    while k < len(src):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
        k += 1
    raise AssertionError("没找到函数体：" + name)


def _catch_branch(body: str) -> str:
    """抠出 `catch (e) { ... }` 那一支（配对断言要判的是**这一支**里写了什么）。"""
    i = body.index("catch")
    j = body.index("{", i)
    depth, k = 0, j
    while k < len(body):
        if body[k] == "{":
            depth += 1
        elif body[k] == "}":
            depth -= 1
            if depth == 0:
                return body[j:k + 1]
        k += 1
    return ""


def batch_b_panel():
    c = Checker()
    title("B. 46-B：「知识库」面板读失败不许静默（同页文档那档早就有 catch，这一档没有）")
    html = _src("app/static/admin.html")
    kb = _js_fn(html, "loadKB")
    cb = _catch_branch(kb)
    c.check("B1 `loadKB` 有 try/catch（`adminFetch` 非 2xx 是**会抛**的，没人接就是停在上一次的样子）",
            "try {" in kb and "catch (e)" in kb, "函数体里没有 catch")
    # ↓ 三条是**配对**：光看"两个字符串都在文件里"护不住"哪一句落在哪一支"（#35 那一课）
    c.check("B2 失败那一支说的是「读不到」，并且**不许**说「还是空的」",
            "读不到" in cb and "还是空的" not in cb, cb[:120])
    c.check("B2-bis 空表那一支才说「还是空的」（两支互换 = 把 401 报成「库里没东西」）",
            "还是空的" in kb and "还是空的" not in cb)
    c.check("B3 失败时状态条也说话（屏幕上只有一张空卡片，用户不会想到去查口令）",
            'st.textContent = "❌ 读知识库失败：' in cb, cb[-90:])
    c.check("B4 上面写清了这句只在「读成功、表里真没条目」时才出现（注释与判据同一条）",
            "只许在" in kb and "配对判据" in kb)
    ed = _js_fn(html, "editKB")
    c.check("B5 `editKB` 也补了 catch（裸 .then() 读失败时表单**一字不动**，看着像「点了没反应」）",
            ".catch(e =>" in ed, ed[:80])
    c.check("B5-bis 而且它不许拿 undefined 去填表单（那会把表单清成空，一保存就写进库）",
            "已经不在了" in ed and "loadKB();" in ed, ed[:100])
    c.check("B6 「刷新列表」这颗按钮存在、且绑的就是 loadKB（读失败时手上得有个重试的东西）",
            'id="refreshKb"' in html
            and 'getElementById("refreshKb").addEventListener("click", loadKB)' in html,
            "按钮或绑定缺一个")
    doc = _js_fn(html, "loadDocs")
    c.check("B7 对照：这一档现在与文档那档**同一种待遇**（两边都有 catch，别再长歪）",
            "catch (e)" in doc and "catch (e)" in kb)

    with sandbox():
        _seed_kb([])
        # 头上带错口令、环境里那把是对的 ⇒ 服务端逐请求比 header，才会 401。
        # （⚠️ 别写成 `admin_token="错的"`：那个参数是**同时**改 env 与 header 的，
        #  两边一起改掉就等于把口令换了，永远 200 —— 我第一版就在这条上假红了一次。）
        bad = make_client(headers={"X-Admin-Token": "000000-not-real"})
        r = bad.get("/api/admin/kb")
        c.check("B8 触发条件是真的：口令不对时这一口回 401（`adminFetch` 就是在这儿抛的）",
                r.status_code == 401, f"{r.status_code} {r.text[:60]}")
        ok = make_client()                                  # 默认带测试口令（`_harness.ADMIN_TOKEN`）
        body = ok.get("/api/admin/kb").json()
        c.check("B8-bis 对照：真读通时回的是 {entries: []} —— 「空表」与「读不到」两种回包形状不同，"
                "前端才有得分两件事说", list(body) == ["entries"] and body["entries"] == [],
                str(body)[:70])
    c.check("B3-bis 读成功那一支把上一句「读不到」擦掉了（留着就等于拿旧错误充当现在的状态）",
            'st.textContent = "";' in kb and kb.index('st.textContent = "";') > kb.index("catch (e)"),
            kb[kb.index("const entries"):][:80])
    c.check("B9 面板开头那段把两档的分界写清了：这一档不切块、留空也能提词"
            "（用户最初那句「切块是我手动切的」就是被两个面板长得像骗的）",
            "不需要切块" in html and "留空也行" in html and "文档知识库" in html)
    return c


def _tree_fp(root: str) -> str:
    """整目录递归指纹（验"只读"只能这么验：逐文件比字节，不是"看着没写"）。"""
    import hashlib
    items = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            p = os.path.join(dirpath, fn)
            with open(p, "rb") as f:
                items.append((os.path.relpath(p, root), hashlib.md5(f.read()).hexdigest()))
    return hashlib.md5(repr(sorted(items)).encode()).hexdigest()


def batch_c_probe():
    c = Checker()
    title("C. 46-C：管理员能自己试一句（探针走的是学生那同一个函数，且只读）")
    from app import store

    mn = _src("app/main.py")
    i = mn.index("async def admin_kb_probe")
    body = mn[i:mn.index("@app", i + 10)]
    c.check("C1 探针没另写一套检索：调的就是学生端那个 `_kb_parts`，函数体里没有第二个匹配循环",
            "_kb_parts(q)" in body and "for item in" not in body and "in q for kw" not in body,
            body[:70])
    c.check("C2 探针不碰模型、不写库（函数体里既没有 llm/ask，也没有 _write/add_/save_）",
            not any(w in body for w in ("llm", "ask_", "chat(", "_write(", "add_kb_entry",
                                        "save_pending", "update_kb_entry")),
            [w for w in ("llm", "ask_", "_write(", "add_kb_entry") if w in body])
    probe = _js_fn(_src("app/static/admin.html"), "probeKB")
    c.check("C3 前端那一格走 textContent、不拼 innerHTML（条目与文档名都是用户可控的，红线 24 那一族）"
            "（判据看的是 `.innerHTML` 这个**写法**，不是单词 —— 我自己那句注释里正好写着这个单词）",
            "out.textContent" in probe and ".innerHTML" not in probe, probe[:70])
    c.check("C3-bis 前端有 catch（读不到就照实说，别再犯 46-B 那条病）"
            "且结果区含「学生会看到」那一行（探针的价值就在「学生真看到什么」这一行）",
            "catch (e)" in probe and "student_view" in probe, probe[:70])
    c.check("C3-ter 输入框、按钮、结果区、绑定四样都在",
            all(x in _src("app/static/admin.html") for x in
                ('id="kbProbeQ"', 'id="kbProbeBtn"', 'id="kbProbeOut"',
                 'getElementById("kbProbeBtn").addEventListener("click", probeKB)')))

    with sandbox():
        _seed_kb([{"question": "游泳馆怎么预约", "answer": "公众号上预约，每人每天两个时段。",
                   "keywords": ["游泳馆"]}])
        store.add_kb_entry("图书馆几点开门", "平时到二十二点三十分。", [])
        cli = make_client()
        r = cli.post("/api/admin/kb/probe", json={"query": "图书馆几点关门"})
        d = r.json()
        c.check("C4 命中形状：条目那条要报得出**序号**（管理员得知道去编辑哪一条），question 也对得上",
                r.status_code == 200 and len(d["entries"]) == 1
                and d["entries"][0]["index"] == 1 and d["entries"][0]["question"] == "图书馆几点开门",
                str(d)[:120])
        c.check("C5 hit 是真的从两个列表算出来的（不是嗅文案）：这一句命中 ⇒ true",
                d["hit"] is True, d.get("hit"))
        sv = d["student_view"] or ""
        c.check("C6 `student_view` 是学生气泡里那份：带答案与出处，**不许**带「相关度 / 原文第几字起」这类给模型的元数据",
                "二十二点三十分" in sv and "相关度" not in sv and "原文第" not in sv, sv[:110])
        empty = cli.post("/api/admin/kb/probe", json={"query": "   "})
        c.check("C7 空话回 400（不许「成功地查到 0 条」—— 那与真查不到长得一样，#41 那一族的老形状）",
                empty.status_code == 400 and "error" in empty.json(),
                f"{empty.status_code} {empty.text[:60]}")
        nohit = cli.post("/api/admin/kb/probe", json={"query": "外星人驻扎在哪个楼"})
        c.check("C7-bis 对照：两路都查不到时 hit=false、条目与文档都是空表（前端才说得出「没查到」）",
                nohit.json()["hit"] is False and nohit.json()["entries"] == []
                and nohit.json()["docs"] == [], str(nohit.json())[:110])
        bad = make_client(headers={"X-Admin-Token": "000000-not-real"})
        c.check("C8 探针挂口令（它是「管理员自检」，不是第二条学生入口）",
                bad.post("/api/admin/kb/probe", json={"query": "图书馆"}).status_code == 401)
        before = _tree_fp(data_path(""))
        cli.post("/api/admin/kb/probe", json={"query": "图书馆几点关门"})
        cli.post("/api/admin/kb/probe", json={"query": "游泳馆怎么预约"})
        c.check("C9 **只读**：连打两次探针之后，沙箱数据目录整树指纹一字未动",
                _tree_fp(data_path("")) == before, "指纹变了")
    return c


BATCHES = [("A 批（46-A 关键词派生）", batch_a_derive),
           ("B 批（46-B 面板不静默）", batch_b_panel),
           ("C 批（46-C 试检索探针）", batch_c_probe)]


def main():
    global code
    print("test_73_kb_management：#46 知识库管理闭环（派生 / 面板不静默 / 能试查）")
    print("=" * 60)
    for name, fn in BATCHES:
        rc = fn().summary(name)
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
