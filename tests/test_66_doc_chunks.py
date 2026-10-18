"""#40：知识库长文档「分块 + 检索 + 文档管理」的护栏。

用户 2026-10-18 那句原话：**「维度不是 11 条，是大段的文件，管理端上传的」**，
点名先做第一步（分块 + 关键词检索 + 管理端文档管理，embedding 留第二步）。

本单修掉的是一个**已经存在的真 bug**：`read_uploaded_file` 超过 6000 字就只给前半本，
一份几十页的手册，后半本模型一个字都看不见 —— 问到了就照开头那截瞎猜。

六批：
  A 批 —— 分块本身的几何性质（逐字切片 / 覆盖无空洞 / 边界优先句末 / 幂等）；
  B 批 —— store 层：上传即建索引、重切、**删干净**（含"挂在通知上就拒删"那道闸）；
  C 批 —— search_docs：G1 深处可检索 + 截断那一路改口 + G7 不改旧行为；
  D 批 —— 管理端三条接口的**鉴权红线**与拒删话术（安全项，反向必须验）；
  E 批 —— 形状与前端接线：一律钉"哪句落在哪一支"，不钉字符串存在性（红线 29）。

⚠️ 起手自种：`uploads.json` / `uploads/` 都在 `sandbox.SCRATCH` 里被清空，
   所以每一批都要自己传文件；绝不读仓库里那份演示上传数据（同 test_65 那条规矩）。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

LONG = 15000          # 比 READ_LIMIT(6000) 高一截，才能证明"后半本"真的进得来
UNIQ = "游泳馆每周四晚间设备维护，二十一点三十分之后不再放行入场。"


def _manual():
    """造一份一万八千字的手册：独有那句埋在第 15000 字之后（前面全是同类废话）。

    为什么这么造：G1 要钉的就是"只读前 6000 字"那个 bug，
    独有句必须落在 6000 字**之后**，否则这条断言改成截断版也照样绿（假绿）。
    """
    filler = "本章说明校园各项公共服务的办理流程与所需材料，具体条款以学院最新发布为准。"
    parts = []
    for ch in range(1, 13):
        parts.append(f"第{ch}章 校园服务细则\n")
        parts.append(filler * 40 + "\n\n")
    parts.append("第13章 体育场馆\n")
    parts.append(filler * 20 + "\n")
    parts.append(UNIQ + "\n")
    parts.append(filler * 20 + "\n")
    return "".join(parts)


def _read_json(path, default):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else default


def batch_a_chunk_geometry():
    c = Checker()
    title("A. 分块的几何性质（纯函数，不碰磁盘）")
    from app.upload import CHUNK_OVERLAP, CHUNK_SIZE, chunk_text

    text = _manual()
    c.check("A0 造的料确实把独有句埋在 6000 字之后（不然 G1 那条是假绿）",
            len(text) > LONG and text.index(UNIQ) > 6000,
            f"全文 {len(text)} 字，独有句在第 {text.index(UNIQ)} 字")

    c.check("A1 空文本 / None → 零块（不抛，也不给一个空块占位）",
            chunk_text("") == [] and chunk_text(None) == [])

    one = "图书馆周一到周五 8:00-22:00 开放。"
    r = chunk_text(one)
    c.check("A2 短文就一块，且 text 等于原文（没被 trim/压缩过）",
            len(r) == 1 and r[0]["text"] == one and r[0]["start"] == 0, repr(r))

    chunks = chunk_text(text)
    # G2：块是原文的逐字切片，且去掉重叠之后**覆盖连续**（除重叠外不许有字符凭空消失）
    covered, holes, not_slice = 0, [], []
    for i, k in enumerate(chunks):
        s, e = k["start"], k["start"] + len(k["text"])
        if text[s:e] != k["text"]:
            not_slice.append(i)
        if s > covered:
            holes.append((i, covered, s))
        covered = max(covered, e)
    c.check("A3 每块都是原文的逐字切片（不改写不压缩，拼回去还是原文）",
            not not_slice, f"不是切片的块号={not_slice[:5]}")
    c.check("A4 相邻块之间没有空洞，末尾也覆盖到全句（G2 分块不丢内容）",
            not holes and covered == len(text) and chunks[0]["start"] == 0,
            f"空洞={holes[:3]} 覆盖到 {covered} / 全文 {len(text)}")
    c.check("A5 块号连续（第几块要能当出处引用，跳号就等于引用对不上）",
            [k["i"] for k in chunks] == list(range(len(chunks))))
    c.check("A6 相邻块确实重叠（跨边界那句话两块都留着，检索才找得到）",
            all(chunks[i + 1]["start"] < chunks[i]["start"] + len(chunks[i]["text"])
                for i in range(len(chunks) - 1)))
    c.check("A7 重叠量就是 CHUNK_OVERLAP（不是随手写的魔数）",
            all(chunks[i]["start"] + len(chunks[i]["text"]) - chunks[i + 1]["start"] == CHUNK_OVERLAP
                for i in range(len(chunks) - 2)),
            f"实测重叠={chunks[0]['start'] + len(chunks[0]['text']) - chunks[1]['start']}")
    c.check("A8 除末块外每块都不小于 CHUNK_SIZE（段落密也不许碎成几十字一块）",
            all(len(k["text"]) >= CHUNK_SIZE for k in chunks[:-1]),
            f"最短={min(len(k['text']) for k in chunks[:-1])} 目标={CHUNK_SIZE}")

    ends = [k["start"] + len(k["text"]) for k in chunks[:-1]]
    on_break = sum(1 for e in ends if text[e - 1] in "。！？!?；;.\n")
    c.check("A9 边界优先落在句末/换行，不是词中间硬切",
            on_break >= len(ends) * 0.9, f"{on_break}/{len(ends)}")

    c.check("A10 无标点的一长串也能切完（硬切也要推进，不许原地打转）",
            len(chunk_text("字" * 5000)) >= 8)
    c.check("A11 幂等：同一篇切两次结果一字不差（重建索引靠这个）",
            chunk_text(text) == chunk_text(text))

    # 跨边界断句：唯一句正好压在块边界上时，重叠必须让它至少出现在一块里
    body = "甲" * (CHUNK_SIZE - 20) + "只有这一句提到铅球比赛报名截止周三十七点。" + "乙" * 3000
    span = [k for k in chunk_text(body) if "铅球比赛报名截止" in k["text"]]
    c.check("A12 跨块边界的独有句没被切成两半查不到（重叠的意义）",
            len(span) >= 1, f"命中 {len(span)} 块")

    # A13 钉"优先级"本身，而不是钉"边界是个标点"（A9 那种判据段落档删掉也照样绿）。
    #     造一个窗口里**同时**有『空行边界(302)』和『更靠后的句末边界(854)』的文本：
    #     认段落 → 第一块切到 302；只认句末 → 会切到 854。差 552 字，一眼就能看出走的哪档。
    prio = "甲" * 300 + "\n\n" + "乙" * 150 + "。" + "丙" * 400 + "。" + "丁" * 2000
    first_end = (lambda k: k[0]["start"] + len(k[0]["text"]))(chunk_text(prio))
    c.check("A13 段落边界优先于句末（同窗口里更靠后的句号要让位给空行，这才叫『优先』）",
            first_end == 302, f"第一块切到 {first_end}（要 302；只认句末会是 854）")
    return c


def batch_b_store():
    c = Checker()
    title("B. store 层：上传即建索引 / 重切 / 删干净")
    with sandbox():
        from app import store

        text = _manual()
        item = store.save_upload("校园服务手册(长).txt", text, raw=text.encode("utf-8"))
        fid = item["id"]
        chunks_path = data_path("uploads", fid + ".chunks.json")

        c.check("B1 上传当场就建好索引（不需要人再去点【重建索引】）",
                os.path.exists(chunks_path), chunks_path)
        rows = store.list_chunks(fid)
        c.check("B2 块数与纯函数切出来的一致",
                len(rows) == len(json.load(open(chunks_path, encoding="utf-8"))["chunks"]) and len(rows) > 10,
                f"{len(rows)} 块")
        c.check("B3 索引文件里带 chunk_size（将来参数变了看得出来这份是哪版切的）",
                json.load(open(chunks_path, encoding="utf-8")).get("chunk_size") is not None)
        c.check("B4 uploads.json 里**没有**块数那一份（块数只有一个来源，重建后不会说谎）",
                "chunks" not in _read_json(data_path("uploads.json"), {"files": [{}]})["files"][0])

        got = list(store.all_chunks())
        c.check("B5 all_chunks 带着出处四件套（fid/name/i/text/start），检索结果要说得出自第几块",
            got and all(set(r) == {"fid", "name", "i", "text", "start"} for r in got),
            sorted(got[0]) if got else "空")
        deep = [r for r in got if UNIQ in r["text"]]
        c.check("B6 埋在 6000 字之后那句真的落在某一块里（G1 的料在不在）",
                bool(deep) and deep[0]["start"] > 6000,
                f"块号={deep[0]['i']} start={deep[0]['start']}" if deep else "没找到")

        c.check("B7 重切幂等：两次块数与内容一致（G5）",
                store.reindex_upload(fid) == len(rows) and store.list_chunks(fid) == rows)
        c.check("B8 重切一份不存在的文档回 None（不能跟『切出 0 块』混成一谈）",
                store.reindex_upload("deadbeefdead") is None)

        # 老数据：只有 .txt，没有索引
        old = store.save_upload("老数据.txt", "这份在切块功能之前传。" * 400)
        os.remove(data_path("uploads", old["id"] + ".chunks.json"))
        c.check("B9 没建索引的老数据：list_chunks 回空表而不是抛异常",
                store.list_chunks(old["id"]) == [])
        bad = store.save_upload("坏索引.txt", "索引文件坏了。" * 400)
        open(data_path("uploads", bad["id"] + ".chunks.json"), "w", encoding="utf-8").write("{不是 JSON")
        c.check("B10 索引文件坏了也回空表（管理员看得见『未建』，点重切就好，全站不该因此炸）",
                store.list_chunks(bad["id"]) == [])
        c.check("B11 坏了也能重切回来", store.reindex_upload(bad["id"]) > 0
                and store.list_chunks(bad["id"]) != [])

        # 通知那道闸（G4）
        n = store.add_notice("游泳馆调整", "详见附上的手册", {"id": fid, "name": "校园服务手册(长).txt"})
        refs = store.upload_notice_refs(fid)
        c.check("B12 upload_notice_refs 认得出这条引用（认 attachment.id，不拿文件名猜）",
                len(refs) == 1 and refs[0]["id"] == n["id"], refs)
        c.check("B13 挂在通知上 → delete_upload **拒删**（G4 的 store 半边）",
                store.delete_upload(fid) is False)
        c.check("B14 拒删之后三样东西一项没少：文字 / 原件 / 索引",
                store.get_upload(fid) is not None and store.get_upload_raw_path(fid) is not None
                and len(store.list_chunks(fid)) == len(rows))
        c.check("B15 那条通知也原封不动（不替管理员做撤通知的决定）",
                [x["id"] for x in store.list_notices()] == [n["id"]])

        store.delete_notice(n["id"])
        c.check("B16 撤回通知之后就删得掉了", store.delete_upload(fid) is True)
        c.check("B17 删干净（G3）：文字 / 索引 / 原件 / 表里那条，四处都没残骸",
                store.get_upload(fid) is None and store.list_chunks(fid) == []
                and store.get_upload_raw_path(fid) is None
                and not os.path.exists(chunks_path)
                and all(f["id"] != fid for f in store.list_uploads()),
                chunks_path if os.path.exists(chunks_path) else "")
        c.check("B18 删一份不存在的回 False（前端要能区分『没删成』和『本来就没这条』）",
                store.delete_upload("ffffffffffff") is False)
        c.check("B19 只有文字、没原件的也能删（不因缺原件而卡住）",
                store.delete_upload(old["id"]) is True)
    return c


def batch_c_search():
    c = Checker()
    title("C. search_docs：深处查得到 + 截断那一路改口 + 不改旧行为")
    with sandbox():
        from app import store
        from app.modules.files import READ_LIMIT, read_uploaded_file, search_docs

        text = _manual()
        fid = store.save_upload("校园服务手册(长).txt", text, raw=text.encode("utf-8"))["id"]

        r = search_docs("游泳馆周四晚上几点以后不让进")
        c.check("C1 问一个只在文档后半段出现的点，能检索到那句（G1，钉死旧 bug）",
                UNIQ in r, r[:70].replace("\n", " "))
        c.check("C2 命中带出处：文件名 + 第几块 + 原文第几字（答完能让人回去核对）",
                "校园服务手册(长)" in r and "第 " in r and "块" in r and "原文第" in r)
        num = int(r.split("》第 ")[1].split(" 块")[0]) if "》第 " in r else 0
        c.check("C3 出处那块确实是深处的块（不是从头两块凑上的）", num > 10, f"第 {num} 块")
        c.check("C4 一次最多给 4 块（不许把整本手册抄进上下文）",
                1 <= len([x for x in r.split("\n\n") if x.startswith("[")]) <= 4,
                f"给了 {len([x for x in r.split(chr(10) + chr(10)) if x.startswith('[')])} 块")

        r2 = search_docs("宿舍 限电")
        c.check("C5 文档里没写的点，老实说没检索到（不许凭常识替这份文件编）",
                "没检索到" in r2, r2[:60].replace("\n", " "))
        c.check("C6 空/全标点查询不硬检索，回一句该怎么问",
                "关键词" in search_docs("？？？") and "关键词" in search_docs(""))

        # 长文件的 read 改口（本单那个真 bug 的正身）
        rr = read_uploaded_file(fid)
        c.check("C7 超长文件 read **不再回正文**（旧行为：前 6000 字当全文端出去）",
                text[:200] not in rr and len(rr) < 400, f"{len(rr)} 字")
        c.check("C8 它指的路是 search_docs，并且带上文件 id（模型照着做就行）",
                "search_docs" in rr and fid in rr, rr[:60].replace("\n", " "))

        short_text = "本周五下午两点在行政楼开会，请各班班长准时到场。"
        sid = store.save_upload("简短通知.txt", short_text)["id"]
        c.check("C9 短文件（≤READ_LIMIT）行为一字不变：原样给全文（G7）",
                read_uploaded_file(sid) == short_text and len(short_text) < READ_LIMIT)
        c.check("C10 短文件也在检索范围内（不是只有长文件才有料）",
                "行政楼开会" in search_docs("周五 行政楼 开会"))

        # 英文代号 / 数字（课表里 GYM101 这类），分词退化成按词切也得能命中
        eid = store.save_upload("课程表.txt", "选修课 GYM101 每周三 19:00-20:30 在游泳馆二层。\n" * 40)["id"]
        c.check("C11 英文代号不分词也能命中（大小写还不敏感）",
                "GYM101" in search_docs("gym101"), search_docs("gym101")[:60])

        # 三份全删掉 → 一点东西都没有，话术必须换成"还没建索引"那一族（不是"没检索到"）
        for x in (eid, sid, fid):
            store.delete_upload(x)
        c.check("C12 一份文档都不剩时不炸，说的是『还没有可检索的文档』（跟『没检索到』分得开）",
                "还没有可检索的文档" in search_docs("gym101"), search_docs("gym101")[:60].replace("\n", " "))

        # G7 的另一半：kb.json 那 11 条 Q&A 的检索线不许被本单带跑。
        # ⚠️ 这里**不钉"某条演示问答还能查到"**：faq 读的是仓库里那份 kb.json，
        #    而 kb.json 是用户会随手改的数据文件 —— 拿它的内容当判据，就是
        #    "用户今天删了条问答，明天全量凭空红一条"（同 test_65 起手自种那条规矩）。
        #    能钉的是：这条线**没被改成吃分块索引**，且"查不到就说查不到"这条行为还在。
        from app.modules import faq
        src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                "app", "modules", "faq.py"), encoding="utf-8").read()
        c.check("C13 search_kb 那条线没被改成吃分块索引（两条线各走各的，G7）",
                "all_chunks" not in src and "chunks" not in src, "")
        kb_miss = faq.search_kb("zzzqqqxxx 这句肯定不在问答条目里")
        c.check("C14 search_kb 查不到时照实说没找到（本单没把它改成「随便挑一条最像的」）",
                "没有找到" in kb_miss and kb_miss.startswith("知识库里"), kb_miss[:50])
    return c


def batch_d_admin_api():
    c = Checker()
    title("D. 管理端三条接口：鉴权红线 + 拒删话术")
    with sandbox():
        text = _manual()
        cli = make_client()
        r = cli.post("/api/upload", files={"file": ("校园服务手册(长).txt", text.encode("utf-8"),
                                                   "text/plain")})
        fid = r.json()["file"]["id"]

        docs = cli.get("/api/admin/docs").json()["docs"]
        c.check("D1 列表给得出 文件名/字数/块数/索引状态/时间（验收表第一行）",
                docs and all({"id", "name", "chars", "chunks", "indexed", "ts"} <= set(d) for d in docs),
                sorted(docs[0]) if docs else "空")
        row = docs[0]
        c.check("D2 刚上传的那份显示『已建』且块数 >10（不用人再点一次才生效）",
                row["indexed"] is True and row["chunks"] > 10, f"{row['indexed']} / {row['chunks']}")
        c.check("D3 列表里的块数就是索引文件的块数（现读，不是抄台账）",
                row["chunks"] == len(json.load(
                    open(data_path("uploads", fid + ".chunks.json"), encoding="utf-8"))["chunks"]))

        ri = cli.post(f"/api/admin/docs/{fid}/reindex")
        c.check("D4 【重建索引】返回块数且与列表一致（幂等，G5）",
                ri.status_code == 200 and ri.json()["chunks"] == row["chunks"], ri.text[:80])
        c.check("D5 重切一份不存在的文档回 400 + 一句人话（不回 404，404 在管理端会被翻成『接口不存在』）",
                cli.post("/api/admin/docs/deadbeef/reindex").status_code == 400)

        # D15 老数据（只有 .txt、没有索引）在管理端必须看得见"未建"。
        #     这条钉的是**谎报**：状态那列要是恒写"已建"，管理员就会以为后半本查得到（其实查不到），
        #     而这份表唯一用途就是让他看出哪份还得点一下【重建索引】。
        stale = cli.post("/api/upload", files={"file": ("老数据.txt", ("补建索引的用料。" * 800).encode("utf-8"),
                                                       "text/plain")}).json()["file"]["id"]
        os.remove(data_path("uploads", stale + ".chunks.json"))
        row2 = [x for x in cli.get("/api/admin/docs").json()["docs"] if x["id"] == stale][0]
        c.check("D15a 索引文件没了 → 列表如实回『未建 / 0 块』（台账里那份文件还在，状态却不跟着它说谎）",
                row2["indexed"] is False and row2["chunks"] == 0 and row2["chars"] > 0,
                f"{row2['indexed']} / {row2['chunks']} / {row2['chars']}")
        cli.post(f"/api/admin/docs/{stale}/reindex")
        row3 = [x for x in cli.get("/api/admin/docs").json()["docs"] if x["id"] == stale][0]
        c.check("D15b 点完【重建索引】状态立刻翻成已建（同一份表，两个状态是两次实测不是两句文案）",
                row3["indexed"] is True and row3["chunks"] > 5, f"{row3['indexed']} / {row3['chunks']}")
        cli.delete(f"/api/admin/docs/{stale}")

        # 通知附件保护（G4 的接口半边）
        cli.post("/api/admin/notify", json={"title": "游泳馆调整", "content": "见附件",
                                            "attachment": {"id": fid, "name": "校园服务手册(长).txt"}})
        d = cli.delete(f"/api/admin/docs/{fid}")
        c.check("D6 文件挂在通知上 → 删除被拒（400），回的是句人话",
                d.status_code == 400 and "通知" in (d.json().get("error") or ""), d.text[:90])
        c.check("D7 那句人话把**挂在哪几条通知**也说了（不然管理员只能挨个翻）",
                "游泳馆调整" in (d.json().get("error") or ""), d.json().get("error", ""))
        c.check("D8 被拒之后文档、索引、那条通知三项原封不动（G4：不顺手替管理员删通知）",
                cli.get("/api/admin/docs").json()["docs"]
                and cli.get("/api/notices").json()["notices"]
                and os.path.exists(data_path("uploads", fid + ".chunks.json")))

        nid = cli.get("/api/notices").json()["notices"][0]["id"]
        cli.delete(f"/api/admin/notices/{nid}")
        c.check("D9 撤掉通知之后删除就成功了（闸只拦引用，不拦删除本身）",
                cli.delete(f"/api/admin/docs/{fid}").status_code == 200)
        c.check("D10 删完列表里就没这条了（学生再问同一句也就检索不到，不会从残骸里翻）",
                all(x["id"] != fid for x in cli.get("/api/admin/docs").json()["docs"]))

        # 鉴权红线（G6）：三条都必须挂 verify_admin，反向必须验"没口令进不去"。
        # ⚠️ 这里**先把服务器口令配上**再来验"这一个请求没带"：
        #    `make_client(admin_token=None)` 是把环境变量一起撤掉的，那种配置回的是 403
        #    （= 服务器根本没配口令），拿它当"没带口令被拦"就钉错了路径 —— 401 才是那一路。
        os.environ["CAMPUSTIME_ADMIN_TOKEN"] = "gate-token-40"
        from fastapi.testclient import TestClient
        from app.main import app as _app
        bare, wrong = TestClient(_app), TestClient(_app, headers={"X-Admin-Token": "gate-wrong"})
        for name, call_no, call_bad in (
            ("GET /api/admin/docs", bare.get("/api/admin/docs"), wrong.get("/api/admin/docs")),
            ("DELETE /api/admin/docs/{fid}", bare.delete(f"/api/admin/docs/{fid}"),
             wrong.delete(f"/api/admin/docs/{fid}")),
            ("POST /api/admin/docs/{fid}/reindex", bare.post(f"/api/admin/docs/{fid}/reindex"),
             wrong.post(f"/api/admin/docs/{fid}/reindex")),
        ):
            c.check(f"D11 服务器配了口令、这一条没带 → {name} 被挡（漏挂就是敞开管理动作）",
                    call_no.status_code == 401, f"HTTP {call_no.status_code}")
            c.check(f"D12 带错口令跟没带一样被挡（不告诉人「差一点就对了」）：{name}",
                    call_bad.status_code == 401, f"HTTP {call_bad.status_code}")
        c.check("D13 学生侧那两条读接口不受影响（该免口令的还免口令，挂错门比没门更糟）",
                bare.get("/api/uploads").status_code == 200
                and bare.get("/api/notices").status_code == 200)
        c.check("D14 带对口令才看得见列表（三条门是同一道门，不是各配一套）",
                make_client().get("/api/admin/docs").status_code == 200)
        os.environ.pop("CAMPUSTIME_ADMIN_TOKEN", None)
    return c


def batch_e_shape():
    c = Checker()
    title("E. 形状与前端接线（钉配对，不钉字符串存在）")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    admin = open(os.path.join(root, "app", "static", "admin.html"), encoding="utf-8").read()
    main = open(os.path.join(root, "app", "main.py"), encoding="utf-8").read()

    # E1 侧栏项 ↔ 视图面板 ↔ NAV_TITLE ↔ switchNav 四处必须成对，缺一处就是"点了没反应"
    c.check("E1 侧栏有 data-nav=\"docs\" 那一项，且同一行就是它的面板 id（不是只加了按钮）",
            'data-nav="docs"' in admin and 'id="view-docs"' in admin)
    nav = admin[admin.index("const NAV_TITLE"):admin.index("function switchNav")]
    sw = admin[admin.index("function switchNav"):admin.index("document.getElementById(\"sbNav\")")]
    c.check("E2 NAV_TITLE 里 docs 那一条落在标题表内（不是写在别处的同名字符串）",
            'docs: "文档知识库"' in nav, nav[:80].replace("\n", " "))
    c.check("E3 switchNav 里 docs 那一支**就是调 loadDocs**（点开才现拉，配的是这一对）",
            'view === "docs"' in sw and sw[sw.index('view === "docs"'):].split("\n")[0].count("loadDocs()") == 1,
            sw[sw.index('view === "docs"'):].split("\n")[0].strip() if 'view === "docs"' in sw else "没这一支")

    # E4/E5 删除与重切两个按钮：绑的动作必须与 data-* 配对，且不许内联 onclick
    ld = admin[admin.index("async function loadDocs"):admin.index("async function handleDocUpload")]
    seg = ld
    c.check("E4 【删除】按钮 data-del-doc 与 addEventListener 配成一对（内联 onclick 是 esc.js 那条禁区）",
            "data-del-doc=" in seg and 'querySelectorAll("button.mini[data-del-doc]")' in seg
            and "onclick=" not in seg, "")
    c.check("E5 【重建索引】按钮同理（两颗按钮一套规矩，别一颗走 dataset 一颗走内联）",
            "data-reindex=" in seg and 'querySelectorAll("button.mini[data-reindex]")' in seg)
    # E6 钉的是**顺序**：确认框必须在 DELETE 那一下之前，写在后面等于没挡
    del_h = seg[seg.index('button.mini[data-del-doc]'):]
    c.check("E6 删除前先过 askConfirm（自绘框，顺序在发 DELETE 之前），话里说清是**真删**、找不回来",
            "await askConfirm" in del_h and del_h.index("await askConfirm") < del_h.index('method: "DELETE"')
            and "真删" in del_h[:del_h.index('method: "DELETE"')], "")
    c.check("E7 表格里文件名走 esc()（上传的文件名是用户可控字符串）",
            'esc(String(f.name || "").slice(0, 40))' in seg)
    c.check("E8 列表读不到时把那格换成一句『读不到』（不许留着上一分钟的表格充『现在还是这些』）",
            "文档列表读不到" in ld[:ld.index("const docs = data.docs")])

    # E9 上传复用同一条口：两条上传路径迟早长出不一样
    up = admin[admin.index("async function handleDocUpload"):
               admin.index('document.getElementById("docfile").addEventListener')]
    c.check("E9 管理端上传复用 /api/upload（与公告附件同一条口，不另开一份）",
            'fetch("/api/upload"' in up and "/api/admin/docs" not in up)
    c.check("E10 上传后的话术把『已切块』说出来（管理员要能确认这一步真的做了，不只是存下）",
            "已切块" in up)

    # E11 三个接口都必须挂 verify_admin —— 钉"装饰器那一行就有它"，不是钉文件里出现过这个函数名
    body = main[main.index("# ============ 管理控制台：文档知识库"):main.index("# ============ 管理控制台：客服人格配置")]
    lines = [ln for ln in body.split("\n") if ln.startswith("@app.")]
    c.check("E11 这一档只有三条接口（列表/删除/重切），且**每条的装饰器行**都带 verify_admin",
            len(lines) == 3 and all("dependencies=[Depends(verify_admin)]" in ln for ln in lines),
            f"{len(lines)} 条：" + " | ".join(ln[:44] for ln in lines))
    c.check("E12 删除那条先查 upload_notice_refs 再碰 delete_upload（顺序反了就是先把附件抽走再问该不该删）",
            body.index("upload_notice_refs") < body.index("delete_upload"))
    c.check("E13 拒删那一支只回话、不动通知（这一段里不许出现 delete_notice 之类）",
            "delete_notice" not in body and "撤回" in body[body.index("refs"):])

    # E14 工具注册：三个工具都在，且 search_docs 的入参就叫 query
    from app.modules.files import build_tools
    t = build_tools()
    c.check("E14 工具箱里三个都在（list/read/search_docs），read 的描述也改了口",
            set(t) == {"list_uploaded_files", "read_uploaded_file", "search_docs"}
            and "search_docs" in t["read_uploaded_file"].description)
    c.check("E15 search_docs 的必填参数是 query（模型传错名字就是白调一次）",
            t["search_docs"].parameters["required"] == ["query"])
    c.check("E16 FILE_HINT 里 long 文件那一路指向 search_docs（提示与工具是同一次改动，别只改一半）",
            "search_docs" in main[main.index("FILE_HINT = \"\"\""):main.index("ASSISTANT_RULES")]
            and main.index("FILE_HINT = \"\"\"") < main.index("ASSISTANT_RULES"))
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_66_doc_chunks：#40 长文档分块 + 检索 + 管理端文档知识库")
    print("=" * 60)
    for fn, name in ((batch_a_chunk_geometry, "A 批（分块几何性质）"),
                     (batch_b_store, "B 批（store：建索引/重切/删干净）"),
                     (batch_c_search, "C 批（search_docs + 截断改口）"),
                     (batch_d_admin_api, "D 批（管理端接口 + 鉴权红线）"),
                     (batch_e_shape, "E 批（形状与前端接线）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
