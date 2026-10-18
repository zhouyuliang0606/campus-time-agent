"""#40 补2：`search_kb` 一次查全（问答条目 + 上传文档）的护栏。

用户 2026-10-18 真机：管理端传了《学生手册-测试样本.txt》（22 块，`search_docs` 查得到
「图书馆位于校园中轴北侧」），学生端问「图书馆在哪里」却回"知识库没有图书馆的信息"。
定性不是检索坏了，是**两个入口割裂** —— FAQ 那条链路只要求模型调 `search_kb`，
而 `search_kb` 只读 `kb.json`；文档被挡在门口。

五批：
  A 批 —— 合并后 search_kb 的四种形状（条目有/文档有/都有/都没有）；
  B 批 —— 边界不能被这条路绕过（非 kb 的文件、没建索引的文件，都进不来）；
  C 批 —— `_kb_has_answer` 那道路由判据没被文案改动换坏（它是按前缀嗅的，文案=契约）；
  D 批 —— 提示词与工具描述同步（只改代码不改提示，模型照旧以为"知识库=问答条目"）；
  E 批 —— 形状与"faq 不再导入时定死路径"（这条不修，测试就只能拿用户今天的数据当判据）。

⚠️ 起手全部自种：`kb.json` 与 `uploads/` 都按沙箱里自己造的那份算，
   **绝不读仓库里/用户机上那份真实知识库** —— 用户 2026-10-18 把自己那 11 条清成 0 条，
   当场让 test_01/test_15 红了 7 条（那是旧断言拿真实数据当起点，见指南红线 18/4o3）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, title

code = 0

# 一条独有的事实，只写在文档里、不写在问答条目里 —— 这样"命中"只可能是文档给的
DOC_FACT = "天文台位于西山顶，参观需提前三个工作日在天文馆公众号预约，每场三十人。"
ENTRY_FACT_Q, ENTRY_FACT_A = "图书馆几点关门", "开放到二十二点半。"

# ⚠️ 垫在答案前面的"无关守则"是**算过长度**的：七段 ≈ 420 字，答案句就落在它那一块的第 420 字起，
#    正好**越过 320 字的截窗上限**（`faq.FAQ_DOC_CHARS`）。
#    上一版料是"同一行重复 90 遍"，切块边界刚好压在答案句上（块内偏移 0），
#    于是 M4（截窗退回"从块头硬砍 cap 字"）换了照样全绿 —— 断言写成了死代码。
_DOC_PAD = ("本馆场馆使用守则适用于全校师生日常进入与登记，"
            "具体条款以当日公告与现场指引为准，节假日安排另行公布并同步至校内通知栏。\n")
_DOC_TAIL = "补充条款：如遇雨雪天气，场馆可能临时关闭，以当日公告为准。\n"


def _seed_entry(question, answer, keywords):
    """往**当前沙箱**的 kb.json 里塞一条问答条目。"""
    p = data_path("kb.json")
    d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"entries": []}
    d.setdefault("entries", []).append({"question": question, "answer": answer, "keywords": keywords})
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def _wipe_kb():
    """把沙箱里的 kb.json 清成空表。

    不wipe就等于**拿用户今天机上有什么条目当判据**：`sandbox()` 是 copytree 真实数据，
    他 2026-10-18 把自己那 11 条清了，本批一半断言就会以"看不懂的形状"红掉（条目侧）。
    """
    json.dump({"entries": []}, open(data_path("kb.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _seed_doc(name="校园天文台须知.txt", body=None, purpose="kb", index=True):
    from app import store
    text = body if body is not None else _DOC_PAD * 7 + DOC_FACT + "\n" + _DOC_TAIL * 4
    item = store.save_upload(name, text, raw=text.encode("utf-8"), purpose=purpose)
    if not index:      # 模拟"传了但没建索引"（#40 之前那份 .txt 没块）
        os.remove(data_path("uploads", item["id"] + ".chunks.json"))
    return item["id"]


def batch_a_shapes():
    c = Checker()
    title("A. 合并后的四种形状（哪一路有货就回哪一路，来源必须分得清）")
    with sandbox():
        from app import store
        from app.modules.faq import FAQ_DOC_CHARS, search_kb

        _wipe_kb()
        fid = _seed_doc()
        # ── K1-前置 先把**料的形状**钉住：答案句在块内的偏移要越过截窗上限。
        #    没有这条，K1-四 就成了自证（上一版 M4 正是这样溜过去的：块边界刚好压在答案句上）。
        mine = [c for c in store.list_chunks(fid) if DOC_FACT[:12] in c["text"]]
        off = mine[0]["text"].index(DOC_FACT[:12]) if len(mine) == 1 else -1
        c.check(f"K1-前置 料的几何：答案句只出现在 {len(mine)} 块里，块内偏移 {off} > 截窗上限 {FAQ_DOC_CHARS}"
                "（这一条不成立时 K1-四 判不了任何东西）",
                len(mine) == 1 and off > FAQ_DOC_CHARS, f"偏移={off} 上限={FAQ_DOC_CHARS}")

        # ── K1 条目表空 + 文档里有答案（真机那一条的形状）
        out = search_kb("天文台参观怎么预约")
        c.check("K1 条目一条没有、文档里写着 → 回的是文档内容，**不再**说『知识库里没有找到』"
                "（这一条就是用户真机撞到的）",
                not out.startswith("知识库里没有找到") and DOC_FACT in out, out[:60].replace("\n", " "))
        c.check("K1-bis 它明说这段来自『上传的文档』（不是伪装成问答条目）",
                "上传的文档" in out)
        c.check("K1-三 出处带得出（哪份文件第几块、原文第几字起）", "《校园天文台须知.txt》" in out and "块" in out)
        at, lbl = out.find(DOC_FACT[:12]), out.find("》")
        c.check("K1-四 摘录从**贴问题那一句**开头，不是从文件头硬砍（砍前 N 字会把答案剪掉）",
                at >= 0 and lbl >= 0 and at - lbl < 220, f"答案句离出处标签 {at - lbl} 字" if at >= 0 else "摘录里没有答案句")

        # ── K2 两边都有：条目在前、文档在后，两份都不丢
        _seed_entry(ENTRY_FACT_Q, ENTRY_FACT_A, ["图书馆", "关门"])
        out2 = search_kb("图书馆几点关门 天文台参观怎么预约")
        c.check("K2 条目与文档都命中 → 两段都在（条目在前、文档在后），谁也没盖住谁",
                ENTRY_FACT_A in out2 and DOC_FACT in out2 and out2.index(ENTRY_FACT_A) < out2.index(DOC_FACT),
                out2[:50].replace("\n", " "))
        head = out2.split("管理员上传的文档")[0]
        c.check("K2-bis 来源标得开：条目区是「Q:/A:」形状、文档区是「[n]《文件》第 N 块」形状"
                "（套成同一种，模型会把原文摘录当标准答案逐字复述）",
                "Q: 图书馆几点关门" in head and DOC_FACT not in head and "《" not in head,
                f"条目区 {len(head)} 字")
        c.check("K2-三 文档段最多带 FAQ_DOC_HITS 块（直答那一路不经模型，太长没法看）",
                out2.count("\n[") <= 2, f"块数={out2.count(chr(10) + '[')}")

        # ── K3 两边都没有 = 那句**一字不变**的旧文案（main._kb_has_answer 按前缀判）
        out3 = search_kb("食堂今晚吃什么")
        c.check("K3 两边都没查到 → 回的还是原来那句『知识库里没有找到和「…」直接相关的内容。』"
                "（这句是一份契约，改了那边就判错）",
                out3 == "知识库里没有找到和「食堂今晚吃什么」直接相关的内容。", repr(out3[:46]))
        c.check("K3-bis 没查到时不许夹『上传的文档里还有…』那种空段（M3 那版就会多一句废话）",
                "文档" not in out3 and "\n" not in out3, repr(out3[:60]))

        # ── K6 空查询 / 全标点：不能当成"文档命中"（施工单那版用子串判，会漏这一支）
        for label, q in (("空串", ""), ("只有标点", "？？？"), ("只有空格", "   ")):
            r = search_kb(q)
            c.check(f"K6 {label}查询 → 走『没找到』那句，不把『这句里没有能用来检索的词』当命中拼进来",
                    r == "知识库里没有找到和「" + q + "」直接相关的内容。", repr(r[:40]))

        # 长度上限（直答那一路不经模型，整段原样贴进气泡）
        _seed_doc(name="天文台开放细则.txt",
                  body=_DOC_PAD * 5 + "开放细则：天文台每场三十人，凭学生证入场，逾期名额自动释放。\n" * 8)
        both = search_kb("天文台参观怎么预约 每场三十人")
        c.check("K7 两篇文档同时命中时合并结果仍不过分长（块数封顶在 FAQ_DOC_HITS，直答气泡才看得完）",
                both.count("\n[") == 2 and len(both) < 1500,
                f"{len(both)} 字 / {both.count(chr(10) + '[')} 块")
        c.check("K7-bis 那两块的出处各指一份文件（合并后来源仍然分得开）",
                "《校园天文台须知.txt》" in both and "《天文台开放细则.txt》" in both)
    return c


def batch_b_boundaries():
    c = Checker()
    title("B. 这条路不能成为 #40补 那道 purpose 闸的后门")
    with sandbox():
        from app.modules.faq import search_kb
        from app import store

        _wipe_kb()
        stu = store.save_upload("2026秋季课表-软件工程25级3班.txt",
                                "周一第一节 高等数学 主教301。\n" * 60 + "秘密：张三的绩点是 3.9。\n",
                                raw="x".encode(), purpose="file")
        out = search_kb("张三的绩点是多少")
        c.check("K4 学生传的（purpose=file）即使写着独有事实，也不会从 search_kb 漏出来"
                "（判的是'回的就是那句一字不变的没找到'，不能判'句子不在回话里'——"
                "回话会把查询词复述一遍，那又是自证）",
                out == "知识库里没有找到和「张三的绩点是多少」直接相关的内容。", out[:50])
        c.check("K4-bis 它也没被建索引（合并这一路不改 #40补 那道闸，只是复用）",
                not os.path.exists(data_path("uploads", stu["id"] + ".chunks.json")))

        noindex = _seed_doc(name="没重切的旧文档.txt", body="旧文档独有句：观星台在下月一号迁址。",
                            index=False)
        c.check("K4-三 kb 文档但还没建索引 → search_kb 只能回没找到（不会去现读整份文件糊弄）",
                search_kb("观星台迁址").startswith("知识库里没有找到"))
        store.reindex_upload(noindex)
        c.check("K4-四 点过【重建索引】之后同一句就能查到了（状态与检索是同一份事实，不是两套账）",
                "观星台在下月一号迁址" in search_kb("观星台迁址"))

        # K5 无循环导入：**新进程**里单独先导 faq 再调，才测得出"faq 顶层不许碰 files"
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'%s');"
             "from app.modules.faq import search_kb;"
             "print(search_kb('天文台')[:12])" % PROJ_DIR],
            capture_output=True, text=True, cwd=PROJ_DIR, timeout=60)
        c.check("K5 新进程里先 import faq 再调 search_kb 不炸（files 是函数内导入，没成环）",
            "Traceback" not in (probe.stdout + probe.stderr)
            and (probe.stdout.startswith("知识库里没有") or "文档" in probe.stdout),
            (probe.stdout or probe.stderr)[:70].replace("\n", " "))
    return c


def batch_c_router_gate():
    c = Checker()
    title("C. `_kb_has_answer` 那道路由判据没被这次合并换坏")
    with sandbox():
        from app.main import _kb_has_answer

        # ⚠️ 必须先清条目表（这一批的 C1-一/二/三 判的都是"**空**知识库 + 有没有文档"）。
        #    本机跑是绿的，因为用户 2026-10-18 把自己那份 kb.json 清成了 0 条；
        #    **严格克隆里它是仓库里那 11 条** ⇒ C1-一 当场红（这次就是克隆抓出来的）。
        #    同一个 sandbox()，别的批都清了，只有这批漏 —— 漏的原因是我拿本机数据当过起点。
        _wipe_kb()
        c.check("C1-一 空知识库 + 没文档 → 仍然 False（问句该被别的分支接走，不该甩给知识库冷处理）",
                _kb_has_answer("校车在哪里坐") is False)
        _seed_doc()
        c.check("C1-二 文档里有答案 → True（判据现在量的是『知识库=条目+文档』这个新事实）",
                _kb_has_answer("天文台参观怎么预约") is True)
        c.check("C1-三 知识库与文档都没有的 → 还是 False（不许因为多了一条路就什么都答'有'）",
                _kb_has_answer("外星人驻扎在哪个楼") is False)
        # 那道门的存在理由：句子带"待办"时不靠知识库判断（main.py 里另有词表，这里只钉判据本身）
        _seed_entry("校车时刻表", "首班 7:20。", ["校车"])
        c.check("C1-四 条目里有『校车』→ 判据为 True（所以让不让路还得看句子里有没有『待办』，那是另一道闸）",
                _kb_has_answer("校车在哪里坐") is True
                and _kb_has_answer("我的校车待办显示不出来") is True)
        cli = make_client()
        # 用「要提前多久」这种一眼是问话的说法：写成「怎么预约」会被规则层当成"要加条待办"接走，
        # 那条路走不到这里，断言就成了自证（第一次就是这么红的）。
        d = cli.post("/api/chat", json={"message": "参观天文台要提前多久", "module": "planner"}).json()
        ans = d.get("answer") or ""
        phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
        c.check("C2-前置：这句确实走的是『📚 问的是校园信息 → 翻知识库』那条分支（测的必须是走到的）",
                "知识库" in phases, phases[:80])
        c.check("C2 日程栏那条『知识库直答』（不经模型）现在能把文档里的答案贴出来"
                "——这条正是用户真机那句的形状",
                DOC_FACT in ans, (ans[:60] + " ⋯" if ans else "空回答").replace("\n", " "))
        c.check("C2-bis 直答带出处标签（学生能判断这是手册原文还是它自己编的）",
                "《校园天文台须知.txt》" in ans)
        d2 = cli.post("/api/chat", json={"message": "外星人驻扎在哪个楼", "module": "planner"}).json()
        phases2 = " | ".join(str(t.get("phase") or "") for t in (d2.get("trace") or []))
        c.check("C2-三 反面对照：知识库与文档都没有答案的问法**不走**这条直答分支"
                "（否则就把一段'没找到'贴给学生了）",
                "翻知识库" not in phases2, phases2[:80] or "trace 为空")
    return c


def batch_d_prompts():
    c = Checker()
    title("D. 提示与工具描述同步（只改代码不改提示，模型照旧以为『知识库=问答条目』）")
    from app.modules import faq
    from app.modules.faq import build_tools

    sp = faq.SYSTEM_PROMPT
    c.check("D1 提示里写明知识库**由两部分**组成（条目 + 上传文档）",
            "两部分" in sp and "上传的文档" in sp)
    c.check("D2 提示第 1 条说 search_kb「同时」查两处（不写这句，模型仍会怀疑还有别的工具要试）",
            "同时" in sp and "search_kb" in sp)
    c.check("D3 提示保留了『没查到就别编』，并补了『也别猜是不是还要换工具查一遍』",
            "不要编造" in sp and "两边都没查到" in sp)
    d = build_tools()["search_kb"].description
    c.check("D4 工具描述也说了条目与文档一起查（模型看的是这句，不是我们的注释）",
            "上传的文档" in d and "条目表为空不代表知识库没有答案" in d.replace(" ", ""), d[:60])
    return c


def batch_e_shape():
    c = Checker()
    title("E. 形状（钉配对与代码位置，不钉字符串存在）")
    root = PROJ_DIR
    faq_src = open(os.path.join(root, "app", "modules", "faq.py"), encoding="utf-8").read()
    files_src = open(os.path.join(root, "app", "modules", "files.py"), encoding="utf-8").read()
    main_src = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()

    c.check("E1 faq 里那次 import 在**函数体内**（顶层互导就成环：faq→files→store，files 又不认 faq）",
            "from app.modules.files import doc_hits" in faq_src
            and any(ln.startswith("    from app.modules.files import") for ln in faq_src.splitlines()))
    c.check("E2 files 顶层不 import faq（方向单一，只有 faq → files）",
            "from app.modules.faq" not in files_src and "import faq" not in files_src)
    c.check("E3 判'有没有'用的是结构化的 doc_hits，不是嗅 `search_docs` 那段人话"
            "（那段一共有三种'没查到'的说法，子串匹配迟早漏一支 —— 施工单那版就漏了空查询）",
            "doc_hits(query)" in faq_src and "search_docs" not in faq_src)
    c.check("E4 文档段落只在真命中时拼进来（判的是 hits 列表空不空，不是文案）",
            "if not hits:\n        if not docs:" in faq_src or "if not docs:" in faq_src)

    # ── E5 判的是**行为**，不是形状。
    # 原本写成 `"_DATA_PATH =" not in faq_src`，结果被 `_load()` 的 docstring 绊红 ——
    # 那段注释里逐字抄了旧代码当"以前这里是什么"的证据。负向形状断言拿自己的注释当判据
    # 是本仓反复记过的那族（指南红线 18 的近亲），所以改成把驱动条件本身拧一遍：
    # 模块已经导入之后换数据目录，读取必须跟着走。跟不走的写法（导入时拼死路径）必红。
    title("E5. 路径每次现算：导入之后再换数据目录，`_load` 得跟着走（红线 23 那一族）")
    with sandbox():
        from app.modules import faq as faq_mod        # 就在这一行，路径要定死也定死了

        _wipe_kb()
        _seed_entry("甲沙箱的问题", "只有甲沙箱答得出这句。", ["甲沙箱"])
        first = faq_mod.search_kb("甲沙箱的问题")
        elsewhere = tempfile.mkdtemp(prefix="campustime-e5-")
        prev = os.environ["CAMPUSTIME_DATA_DIR"]
        os.environ["CAMPUSTIME_DATA_DIR"] = elsewhere
        try:
            moved = faq_mod.search_kb("甲沙箱的问题")
        finally:
            os.environ["CAMPUSTIME_DATA_DIR"] = prev
        shutil.rmtree(elsewhere, ignore_errors=True)
        c.check("E5-一 还在原沙箱时读得到自己种的那条（不然 E5-二 的'读不到'是蒙的）",
                "只有甲沙箱答得出这句" in first and not first.startswith("知识库里没有找到"),
                first[:46].replace("\n", " "))
        c.check("E5-二 换个空目录（模块不重新导入）就看不见那条了 → 路径是每次现算的"
                "（导入时定死的写法这一条必红 —— 而护栏就只能拿用户机上那份 kb.json 当起点）",
                "只有甲沙箱答得出这句" not in moved and moved.startswith("知识库里没有找到"),
                moved[:46].replace("\n", " "))

    seg = main_src[main_src.index("def _kb_has_answer"):main_src.index("def _rule_intent")]
    c.check("E6 main 那道路由判据仍按 miss 前缀嗅 —— 那这句文案就是契约，本批 K3 钉着一字不差",
            'startswith("知识库里没有找到")' in seg, seg[:40].replace("\n", " "))
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_67_kb_docs_merged：#40 补2 search_kb 一次查全（条目 + 上传文档）")
    print("=" * 60)
    for fn, name in ((batch_a_shapes, "A 批（合并后的四种形状）"),
                     (batch_b_boundaries, "B 批（不得绕过 purpose 边界）"),
                     (batch_c_router_gate, "C 批（路由判据没被换坏）"),
                     (batch_d_prompts, "D 批（提示与工具描述）"),
                     (batch_e_shape, "E 批（形状与路径）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
