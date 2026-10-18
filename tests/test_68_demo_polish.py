"""#41 的护栏：演示前三处收口（直答文案 / 查空口径 / 坏文件 400）。

三批对应施工单三刀，每批都带**对照**（把正常路径修坏是最容易漏的一类红）：
  A 批 —— 检索与渲染分离：学生视图不许带模型用的元数据，模型视图不许丢出处；
  B 批 —— 两路都查空的校园问句不许被听成"查日程"，同时**真报障与列待办照旧**；
  C 批 —— 坏文件回 400 人话（四种格式实测都是 500），正常文件与老版格式不受影响。
判据一律 AST + 真发，不 grep 源码字符串（注释会骗人：test_67 的 E3 就被我自己的注释绊红过）。
"""
import io
import json
import os

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, seed_todos, title

code = 0

DOC_FACT = "天文台位于西山顶，参观需提前三个工作日在天文馆公众号预约，每场三十人。"
# 问得出、但**这份文档里没有**的一句（用来验"两路都空"）
NO_DOC_Q = "图书馆的古籍阅览室在哪个楼"
DOC_NAME = "校园天文台须知.txt"
_PAD = ("本馆场馆使用守则适用于全校师生日常进入与登记，"
        "具体条款以当日公告与现场指引为准，节假日安排另行公布并同步至校内通知栏。\n")
_TAIL = "补充条款：如遇雨雪天气，场馆可能临时关闭，以当日公告为准。\n"


def _seed_doc(name=DOC_NAME, body=None, purpose="kb"):
    from app import store
    text = body if body is not None else _PAD * 7 + DOC_FACT + "\n" + _TAIL * 4
    return store.save_upload(name, text, raw=text.encode("utf-8"), purpose=purpose)["id"]


def _wipe_kb():
    json.dump({"entries": []}, open(data_path("kb.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _seed_entry(question, answer, keywords):
    p = data_path("kb.json")
    d = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"entries": []}
    d.setdefault("entries", []).append({"question": question, "answer": answer, "keywords": keywords})
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False)


def _chat(cli, msg, module="planner"):
    d = cli.post("/api/chat", json={"message": msg, "module": module}).json()
    phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
    return (d.get("answer") or ""), phases


def _tree(rel):
    import ast
    return ast.parse(open(os.path.join(PROJ_DIR, *rel.split("/")), encoding="utf-8").read())


def _calls_in(node):
    import ast
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _func(tree, name):
    import ast
    for n in ast.walk(tree):
        # AsyncFunctionDef 也要认：`/api/chat` 那条路由就是 async 的，
        # 只找 FunctionDef 会拿到 None，于是 D1 变成"函数体里没调用任何东西"这种假红。
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def batch_a_views():
    c = Checker()
    title("A. 两个视图：给学生的那份不带元数据，给模型的那份不丢出处")
    with sandbox():
        from app.modules.faq import answer_for_student, search_kb

        _wipe_kb()
        _seed_doc()

        # ── G1/G2 走真发那条路（日程栏知识库直答 = 不经模型，屏幕上看到什么就是什么）
        cli = make_client()
        # ⚠️ 这句特意说成「参观天文台要提前多久」：写成「怎么预约」会被规则层当成**要加待办**接走
        #    （第一次就是这么红的 —— 断言量的根本不是直答那一路，测的必须是走到的）。
        ans, phases = _chat(cli, "参观天文台要提前多久")
        c.check("A1-前置：这句确实走的是『翻知识库直答』那条分支（测的必须是走到的）",
                "翻知识库" in phases, phases[:90])
        bad = [w for w in ("相关度", "原文第", "字起", "块（", "第 1 块") if w in ans]
        c.check("G1 直答气泡里**不许出现**给模型定位用的元数据（相关度 / 原文第几字起 / 第 N 块）",
                not bad, f"漏出来的：{bad}｜{ans[:80]}")
        c.check("G2 直答仍要说清出自哪份文档（学生能顺着文件名去翻）",
                DOC_NAME in ans, ans[:70])
        c.check("A1-bis 学生视图也不许把模型视图那句开场白带进来（『在文档里找到以下相关段落（按相关度…）』）",
                "按相关度" not in ans and "出自第几块" not in ans)
        c.check("A1-三 直答长度仍受截窗约束（原文一千七百字不许整块贴出来）",
                len(ans) < 1200, f"{len(ans)} 字")

        # ── G3 模型视图的出处信息不许被"顺手统一"掉
        model = search_kb("天文台参观怎么预约")
        c.check("G3 模型视图里三样元数据都在（第 N 块 / 原文第几字起 / 相关度）—— 模型靠它引用与复查",
                "第 1 块" in model and "原文第" in model and "相关度" in model, model[:70])
        c.check("G3-bis 两个视图看的是**同一份检索结果**（都答得出那句话，不是各查各的）",
                DOC_FACT in model and DOC_FACT in ans)

        # ── 条目 + 文档都有时，学生视图两段都在、条目在前
        _seed_entry("图书馆几点关门", "开放到二十二点半。", ["图书馆", "关门"])
        s2 = answer_for_student("图书馆几点关门 天文台参观怎么预约")
        c.check("A3 条目 + 文档都命中时学生视图两段都在（条目在前、文档在后）",
                "开放到二十二点半" in s2 and DOC_FACT in s2
                and s2.index("开放到二十二点半") < s2.index(DOC_FACT), s2[:60])
        c.check("A3-bis 学生视图里条目用的是「问：/答：」而不是模型的『Q:/A:』（这是给人读的）",
                "问：图书馆几点关门" in s2 and "Q: 图书馆几点关门" not in s2)

        # ── 两边都没有：那句 miss 是契约，两个视图不许各写一句
        m1, m2 = search_kb("外星人驻扎在哪个楼"), answer_for_student("外星人驻扎在哪个楼")
        c.check("A4 两边都查空时两个视图回的是**同一句**（miss 文案只有一处定义）",
                m1 == m2 and m1.startswith("知识库里没有找到"), m1[:40])
    return c


def batch_b_routing():
    c = Checker()
    title("B. 两路都查空的校园问句，不许被听成『查日程』")
    with sandbox():
        _wipe_kb()
        _seed_doc()          # 文档里有天文台那句，但**没有**古籍阅览室那句
        cli = make_client()

        # ── G4 正主：日程栏问一件知识库根本没有的校园事
        ans, phases = _chat(cli, NO_DOC_Q)
        c.check("G4 日程栏问『…在哪个楼』而知识库两路都没有 → 老实说没查到（不再翻日程）",
                "知识库里没有查到这一条" in ans, ans[:90])
        c.check("G4-bis 回话里**不许**再出现『查了日程 / 确实还没有』那句日程话术",
                "查了日程" not in ans and "确实还没有" not in ans, ans[:70])
        c.check("G4-三 trace 里看得见这条分支（不是掉给模型现编的一句）",
                "没有这一条" in phases or "老实说没查到" in phases, phases[:100])

        # ── 对照一：真报障**不许**被让路抢走（这条是第一版没牙的那条，两处都改了）
        #    旧写法只断言"回话里没有那句『知识库里没有查到』" —— 把报障否决摘掉（变异 M6）照样全绿：
        #    那句判据既没钉"走到了哪一支"，也没钉"不许长得像知识库回答"。
        #    现在：① 库里真种一条待办（五种说法实测都落在这条分支，答案形状是稳定的）；
        #          ② 断言"回话里没有知识库那一路的形状" + "看得见报障那句实测话术"，一正一反配成对。
        seed_todos([{"title": "健身", "date": "2026-10-19", "start": "19:00", "end": "20:00"}])
        ans2, ph2 = _chat(cli, "我怎么没看到周一的健身")
        c.check("B1 对照：真报障（『怎么没看到』）仍由报障那条分支接 —— 回话里不许出现知识库那一路的形状",
                "📚" not in ans2 and "知识库里" not in ans2 and "问答条目" not in ans2,
                ans2[:80])
        c.check("B1-bis 而且看得见它走到的那支（『日程里排着呢』这句是核实分支的实测话术）",
                "日程里排着呢" in ans2 and "查日程" in ph2, f"{ans2[:50]}｜{ph2[:60]}")

        # ── 对照二：列待办照旧（G5）
        ans3, ph3 = _chat(cli, "我的待办有什么")
        c.check("G5 对照：『我的待办有什么』照旧是列待办，没被知识库这条抢走",
                "待办" in ans3 and "知识库里没有查到这一条" not in ans3, ans3[:80])

        # ── 对照三：让路①（知识库真有答案）没被换坏 —— 条目那一路
        ans4, ph4 = _chat(cli, "周一健身排在几点")
        c.check("B3 对照：句子带『周一…』这种时间指代时仍按各自的分支走（不被校园问句判据误抢）",
                "知识库里没有查到这一条" not in ans4, ans4[:80])

        # ── 文档有答案 → 走直答（不该被"没查到"那句抢掉）
        ans5, ph5 = _chat(cli, "参观天文台要提前多久")
        c.check("B4 文档里有答案时**不许**回这句『没有查到』（补2 的口径：文档也算知识库）",
                "没有查到这一条" not in ans5 and DOC_FACT[:12] in ans5, ans5[:80])

        # ── 问答栏（faq）不许被这条兜底盖掉它自己的链路
        ans6, ph6 = _chat(cli, NO_DOC_Q, module="faq")
        c.check("G4-四 问答栏里同一句**不走**这条日程栏兜底（那条栏有自己的知识与降级提示）",
                "知识库里没有查到这一条" not in ans6, ans6[:80])

        # ── 判据不嗅文案（#41-B 去重复的真正牙齿）
        from app.main import _kb_has_answer
        from app.modules import faq as faq_mod

        old_miss = faq_mod._MISS
        try:
            faq_mod._MISS = "完全没有这句："
            hit = _kb_has_answer("天文台参观怎么预约")
            miss = _kb_has_answer(NO_DOC_Q)
        finally:
            faq_mod._MISS = old_miss
        c.check("B5 把 miss 文案**换个字头**之后，路由判据的结果一字不变（判的是列表，不是文案）",
                hit is True and miss is False, f"有答案={hit} 没答案={miss}")
        c.check("B5-bis 换字头后 search_kb 确实回的是新那句（不然上面那条是自证）",
                faq_mod.search_kb(NO_DOC_Q) is not None)
    return c


def batch_c_badfiles():
    c = Checker()
    title("C. 坏文件回 400 + 人话（实测四种格式原先全都 500）")
    with sandbox():
        from fastapi.testclient import TestClient
        from app.main import app

        # ⚠️ `raise_server_exceptions=False`：这一批量的是"**路由没接住异常时学生拿到 HTTP 几**"。
        #    默认那一档会把 traceback 直接抛回测试进程 —— 于是变异 M9（把兜底整个摘掉，
        #    也就是本单修的那个 500）表现为"**崩了、一条 ❌ 都没有**"，这条护栏就等于没牙
        #    （2026-10-18 跑反向验证时就是这么暴露的）。
        cli = TestClient(app, raise_server_exceptions=False)
        CASES = [
            ("改名成 docx 的 txt", "垃圾.docx", "这其实是一段纯文本".encode("utf-8")),
            ("截断的 docx", "断.docx", b"PK\x03\x04" + b"\x00" * 40),
            ("改名成 xlsx 的 txt", "垃圾.xlsx", "纯文本冒充".encode("utf-8")),
            ("改名成 pptx 的 txt", "垃圾.pptx", "纯文本冒充".encode("utf-8")),
            ("改名成 pdf 的 txt", "垃圾.pdf", "纯文本冒充".encode("utf-8")),
        ]
        for name, fn, blob in CASES:
            r = cli.post("/api/upload", files={"file": (fn, io.BytesIO(blob),
                                                       "application/octet-stream")})
            body = r.text or ""
            c.check(f"G7 「{name}」→ 400 + 人话，不是 500 + traceback",
                    r.status_code == 400 and "读不出内容" in body
                    and "Traceback" not in body and "zip" not in body.lower(),
                    f"HTTP {r.status_code}｜{body[:60]}")

        ups_p = data_path("uploads.json")
        # 一条都没成功过 ⇒ 这份台账**根本没被创建**（比"建了但是空的"更干净）：两种都算过。
        ups = (json.load(open(ups_p, encoding="utf-8")).get("files") or []) if os.path.exists(ups_p) else []
        c.check("C1 抽不出内容的文件**一条都不落台账**（拒了就是拒了，不留半成品）",
                not any((f.get("name") or "").startswith("垃圾.") or (f.get("name") or "") == "断.docx"
                        for f in ups), f"台账 {len(ups)} 条（文件存在={os.path.exists(ups_p)}）")

        # ── G8 正常 docx 仍能抽（真造一份，不是拿 .txt 冒充）
        import docx
        d = docx.Document()
        d.add_paragraph("第1章 图书馆服务")
        d.add_paragraph("图书馆位于校园中轴北侧，共六层。")
        bio = io.BytesIO()
        d.save(bio)
        r = cli.post("/api/upload", files={"file": ("真手册.docx", bio.getvalue(),
                                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                     data={"purpose": "kb"})
        body = r.json() if r.status_code == 200 else {}
        c.check("G8 正常 docx 照旧抽得出正文（HTTP 200 + 字数不为零）",
                r.status_code == 200 and (body.get("file") or {}).get("chars", 0) > 10,
                f"HTTP {r.status_code}｜{str(body)[:80]}")

        # ── 老版格式那句"另存为"不许被新兜底盖掉。实测它是**路由那道后缀闸**挡的
        #    （`ext_supported` 不认 `.doc` ⇒ 415 + `unsupported_reason()` 那句指引），
        #    根本走不到 `extract_text` —— 所以这一条验的是"这单没把 415 那一路也吸进 400"。
        r2 = cli.post("/api/upload", files={"file": ("老版.doc", b"\xd0\xcf\x11\xe0junkjunkjunk",
                                                    "application/octet-stream")})
        b2 = r2.text or ""
        c.check("C2 老版 .doc 仍是 415 + 『另存为 .docx』那句指引（后缀闸挡的，没被统一成『读不出内容』）",
                r2.status_code == 415 and "另存为" in b2 and "读不出内容" not in b2,
                f"HTTP {r2.status_code}｜{b2[:60]}")

        # ── 空文件那条老话术没变
        r3 = cli.post("/api/upload", files={"file": ("空.txt", b"", "text/plain")})
        c.check("C3 空文件仍回原来那句『这个文件是空的』（这单没顺手改它）",
                r3.status_code == 400 and "是空的" in (r3.text or ""), (r3.text or "")[:60])
    return c


def batch_d_shape():
    c = Checker()
    title("D. 形状（AST 判接线，不 grep —— 注释里提一句不算调用）")
    main_tree = _tree("app/main.py")
    faq_tree = _tree("app/modules/faq.py")
    files_tree = _tree("app/modules/files.py")
    up_tree = _tree("app/upload.py")

    # 注意找的是 `_chat_impl` 不是 `chat`：`/api/chat` 那个入口只做协商（JSON / SSE），
    # 真正的分支体在 `_chat_impl` 里 —— 拿 `chat` 去要调用名，永远是空集（第一次就这么假红的）。
    body = _func(main_tree, "_chat_impl")
    main_calls = _calls_in(body) if body is not None else set()
    c.check("D1 日程栏那条直答调的是 `answer_for_student`（学生视图），不是 `search_kb`",
            body is not None and "answer_for_student" in main_calls and "search_kb" not in main_calls,
            f"_chat_impl 找到={body is not None}｜涉及的名字="
            f"{sorted(n for n in main_calls if 'answer' in n or 'search' in n)}")
    fn = _func(faq_tree, "answer_for_student")
    sk = _func(faq_tree, "search_kb")
    c.check("D2 两个视图共用同一份检索（函数体都调 `_kb_parts`），匹配逻辑没有第二份",
            fn is not None and sk is not None
            and "_kb_parts" in _calls_in(fn) and "_kb_parts" in _calls_in(sk),
            sorted(n for n in (_calls_in(fn) | _calls_in(sk)) if "kb" in n))
    stu = _func(files_tree, "format_doc_hits_for_student")
    c.check("D3 学生视图自己排版（不调模型视图），但共用 `_best_window`（截窗只有一处）",
            stu is not None and "format_doc_hits" not in _calls_in(stu)
            and "_best_window" in _calls_in(stu), sorted(_calls_in(stu)) if stu else "没有这个函数")
    ex = _func(up_tree, "extract_text")
    called_ex = _calls_in(ex) if ex is not None else set()
    c.check("D4 坏文件兜底包在**四个解析器外面那一层**（不是在某个解析器里各写一遍）",
            ex is not None and {"_docx_text", "_pdf_text", "_xlsx_text", "_pptx_text"} <= called_ex
            and "UploadError" in called_ex,
            sorted(n for n in called_ex if "text" in n or "Error" in n))
    c.check("D4-bis 收的是 `Exception` 而不是某个具体异常类（四种格式实测四种都漏）",
            "BadZipFile" not in called_ex and "PackageNotFoundError" not in called_ex)
    src = open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read()
    c.check("D5 「说自己日程的词」那张表全站只写一处（`_SELF_SCHEDULE_WORDS`）",
            src.count('_SELF_SCHEDULE_WORDS = (') == 1
            and src.count("for w in _SELF_SCHEDULE_WORDS") >= 2,
            f"定义 1 处、引用 {src.count('for w in _SELF_SCHEDULE_WORDS')} 处")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_68_demo_polish：#41 直答文案 / 查空口径 / 坏文件 400")
    print("=" * 60)
    for fn, name in ((batch_a_views, "A 批（两个视图）"),
                     (batch_b_routing, "B 批（查空别答日程）"),
                     (batch_c_badfiles, "C 批（坏文件 400）"),
                     (batch_d_shape, "D 批（形状）")):
        rc = fn().summary(f"{name}")
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
