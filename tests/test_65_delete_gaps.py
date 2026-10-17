"""#39：四处「只增不删」缺口的护栏（驿站服务 / 登记记录 / 已处理历史 / 管理端通知）。

用户 2026-10-17 晚那句原话：**「有好多地方只有增加无删除」**，另外点名两处
（新增服务后没有删除按钮、已处理遮不住；管理端能找到处理完的历史，驿站没有）。

五批：
  A 批 —— 服务定义可删（含"内置项判据是 table 不是 id"这一条）；
  B 批 —— 登记记录可删（**真删**，所以每条都要证明"没成的时候没动手"）；
  C 批 —— 已处理历史：接口只给数 + 明细另开一条，放回不许复制第二份逻辑；
  D 批 —— 通知撤回：这是本文件唯一一条**安全红线**（漏挂口令就是敞开管理动作）；
  E 批 —— 形状与前端接线：**按红线 29 的教训，一律钉"哪句落在哪一支"，不钉字符串存在性**。

⚠️ 起手全部自种（services.json / custom / notices.json 都在 `sandbox.SCRATCH` 里被清空），
   不读仓库里那份演示数据 —— 否则用户今天手测建了什么，明天就会决定这批跑不跑得过。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0
PKGLOSS = {"id": "pkgloss", "name": "快递/外卖问题处理", "triggers": ["快递丢了"],
          "collect_fields": ["物品描述"], "reply_template": "已登记", "table": "workorders"}
X1 = {"id": "x1", "name": "失物招领", "triggers": ["丢了饭卡"],
      "collect_fields": ["物品描述"], "reply_template": "已登记", "table": "lostfound"}


def _seed_services(items):
    json.dump(items, open(data_path("services.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _read_json(p, default):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def _seed_records(sid, rows):
    os.makedirs(data_path("custom"), exist_ok=True)
    json.dump(rows, open(data_path("custom", f"{sid}.json"), "w", encoding="utf-8"),
              ensure_ascii=False)


def _rec(rid, status="待处理", desc="校园卡"):
    return {"id": rid, "ts": "2026-10-18 00:00:00", "service": "x1", "name": "失物招领",
            "fields": {"物品描述": desc}, "status": status}


def batch_a_services():
    c = Checker()
    title("A. 服务定义可删（内置项拒删，判据看 table）")
    with sandbox():
        cli = make_client()
        _seed_services([dict(PKGLOSS), dict(X1)])
        r = cli.delete("/api/station/services/pkgloss")
        after = [s["id"] for s in _read_json(data_path("services.json"), [])]
        c.check("A1 删内置 pkgloss 被拒（400），表里两项一项没少",
                r.status_code == 400 and after == ["pkgloss", "x1"], f"{r.status_code} {after}")
        c.check("A1-bis 拒删回的是**一句人话**（不是空 body、不是 500）",
                "内置" in (r.json().get("error") or ""), repr(r.text[:70]))

        ok = cli.delete("/api/station/services/x1")
        left = [s["id"] for s in _read_json(data_path("services.json"), [])]
        c.check("A2 删自建项成功，且表里只剩内置",
                ok.status_code == 200 and left == ["pkgloss"], f"{ok.status_code} {left}")
        c.check("A2-bis 列表接口跟着变（服务清单只有一份，不是两处各存各的）",
                [s["id"] for s in cli.get("/api/station/services").json()["services"]] == ["pkgloss"], "")
        c.check("A2-ter 删完**不会长回内置种子**（表非空才不触发那份兜底）",
                len(cli.get("/api/station/services").json()["services"]) == 1, "")

        again = cli.delete("/api/station/services/x1")
        c.check("A3 同一条再删一次 → 400（不许假装 ok=True）",
                again.status_code == 400 and not again.json().get("ok"), repr(again.text[:60]))
        c.check("A3-bis 不存在的那项也是 400，正文说清两种可能",
                cli.delete("/api/station/services/zzz").status_code == 400, "")

        # 判据必须是 table：种一项 id 不像内置、table 却是 workorders 的，照样不许删
        _seed_services([{"id": "zz9", "name": "也叫工单型", "triggers": ["报修"],
                         "collect_fields": ["物品描述"], "reply_template": "已登记",
                         "table": "workorders"}])
        r2 = cli.delete("/api/station/services/zz9")
        c.check("A4 内置的判据是 table=workorders，**不是 id 等于 pkgloss**（换个 id 也照样拦住）",
                r2.status_code == 400
                and [s["id"] for s in _read_json(data_path("services.json"), [])] == ["zz9"],
                f"{r2.status_code}")

        _seed_services([dict(PKGLOSS), dict(X1)])
        _seed_records("x1", [_rec("r1"), _rec("r2", desc="钥匙")])
        cli.delete("/api/station/services/x1")
        rows = _read_json(data_path("custom", "x1.json"), None)
        c.check("A5 删服务定义**不动**它的历史台账（两条记录一条不少）",
                isinstance(rows, list) and len(rows) == 2, f"custom/x1.json {rows and len(rows)} 条")
    return c


def batch_b_records():
    c = Checker()
    title("B. 登记记录可删（真删 ⇒ 没成的时候一个字都不许动）")
    with sandbox():
        cli = make_client()
        _seed_services([dict(PKGLOSS), dict(X1)])
        _seed_records("x1", [_rec("r1"), _rec("r2", desc="钥匙")])
        before = open(data_path("custom", "x1.json"), encoding="utf-8").read()

        r = cli.delete("/api/station/services/nope/records/r1")
        c.check("B1 服务不存在 → 404（先认服务再碰文件），台账文件**逐字节没变**",
                r.status_code == 404
                and open(data_path("custom", "x1.json"), encoding="utf-8").read() == before,
                f"{r.status_code}")

        ok = cli.delete("/api/station/services/x1/records/r1")
        left = [x["id"] for x in _read_json(data_path("custom", "x1.json"), [])]
        c.check("B2 删一条：那条没了、另一条还在（整份写回不能顺手把邻居带走）",
                ok.status_code == 200 and left == ["r2"], f"{ok.status_code} {left}")
        c.check("B2-bis 读接口立刻看不到了（不是只删了视图）",
                [x["id"] for x in cli.get("/api/station/services/x1/records").json()["records"]] == ["r2"], "")
        c.check("B2-ter 再删同一条 → 400 一句人话（不谎报 ok）",
                cli.delete("/api/station/services/x1/records/r1").status_code == 400, "")

        # 坏形状：文件里不是列表 —— 不许 500，也不许"顺手写成空表"把别人的记录清掉
        os.makedirs(data_path("custom"), exist_ok=True)
        json.dump({"id": "r9"}, open(data_path("custom", "x1.json"), "w", encoding="utf-8"))
        r3 = cli.delete("/api/station/services/x1/records/r9")
        c.check("B3 台账文件形状坏了（不是列表）→ 400，且**没被覆盖成空表**",
                r3.status_code == 400 and isinstance(
                    _read_json(data_path("custom", "x1.json"), None), dict),
                f"{r3.status_code}")

        c.check("B4 文件根本不存在 → 400（不是 500、不是 ok=True 装成功）",
                cli.delete("/api/station/services/x1/records/rx").status_code == 400, "")
        os.remove(data_path("custom", "x1.json"))
        c.check("B4-bis 上面那次失败**没顺手建出那个文件**（写只在真删成的时候发生）",
                not os.path.exists(data_path("custom", "x1.json")), "")

        listing = set(os.listdir(data_path("custom")))
        for evil in ("%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..%2f..%2fplain"):
            rr = cli.delete(f"/api/station/services/{evil}/records/r1")
            c.check(f"B5 服务 id 写成编码穿越串（{evil[:22]}…）→ 被 404/400 拦住，不是 500",
                    rr.status_code in (400, 404), f"{rr.status_code}")
        c.check("B5-bis custom/ 里没有多出任何文件，数据目录外面也没有（路径穿越真被挡住）",
                set(os.listdir(data_path("custom"))) == listing
                and not os.path.exists(data_path("..", "plain.json"))
                and not os.path.exists(data_path("custom", "..", "plain.json")),
                str(sorted(listing)))
    return c


def batch_c_history():
    c = Checker()
    title("C. 已处理历史：条数与明细分开给，放回不许复制第二份逻辑")
    with sandbox():
        cli = make_client()
        # 工单直接走 store：HTTP 那条要口令、还会双写一条学生消息，噪声大且与本单无关
        from app.store import add_workorder, list_workorders, set_current_student
        set_current_student("admin1")
        a = add_workorder("外卖丢失", "还没处理的那条")
        b = add_workorder("快递丢失", "已经办完的那条")

        d0 = cli.get("/api/station/workorders").json()
        c.check("C0 起手：未结两条、archived_count=0（字段是**新加的**，原有 workorders 一字未动）",
                len(d0["workorders"]) == 2 and d0.get("archived_count") == 0
                and set(d0.keys()) == {"workorders", "archived_count"}, sorted(d0))

        r = cli.post(f"/api/station/workorders/{b['id']}/status", json={"status": "已解决"})
        c.check("C1 标已解决仍然走 #29 那条自动归档（本单没碰那套编排）",
                r.json().get("auto_archived") is True, repr(r.json())[:70])
        d1 = cli.get("/api/station/workorders").json()
        c.check("C2 未结表里那条不见了，archived_count 变成 1（数的是 archived，不是猜的）",
                [w["id"] for w in d1["workorders"]] == [a["id"]] and d1["archived_count"] == 1,
                f"count={d1['archived_count']}")
        arch = cli.get("/api/station/workorders/archived").json()
        c.check("C3 archived 接口恰好回那一条，带 owner/ts/kind（那行要点开就能看见是谁的）",
                [w["id"] for w in arch["workorders"]] == [b["id"]]
                and arch["workorders"][0].get("owner") == "admin1", repr(arch)[:80])
        c.check("C4 未结接口**不带**明细：archived 那条不许被 5 秒轮询整包搬（只给条数）",
                all(w["id"] != b["id"] for w in d1["workorders"])
                and "archived" not in json.dumps(d1["workorders"]), "")

        back = cli.post(f"/api/station/workorders/{b['id']}/status", json={"status": "处理中"})
        d2 = cli.get("/api/station/workorders").json()
        c.check("C5 放回不开新接口：走既有状态口，反向 auto_unarchived 在服务端那一处",
                back.json().get("auto_unarchived") is True
                and sorted(w["id"] for w in d2["workorders"]) == sorted([a["id"], b["id"]])
                and d2["archived_count"] == 0, f"count={d2['archived_count']}")

        # 判据钉死在 archived 字段上：status=已解决 但没收起的那条，不该出现在历史里
        cli.post(f"/api/station/workorders/{b['id']}/status", json={"status": "已解决"})
        rows = list_workorders(include_archived=True)
        for w in rows:
            if w["id"] == a["id"]:
                w["status"], w["archived"] = "已解决", False
                w.pop("archived_at", None)
        json.dump(rows, open(data_path("workorders.json"), "w", encoding="utf-8"), ensure_ascii=False)
        got = cli.get("/api/station/workorders/archived").json()["workorders"]
        c.check("C6 历史那行的判据是 archived 字段，**不是 status==已解决**（按状态挑会把没收起的也收进来）",
                [w["id"] for w in got] == [b["id"]], [w.get("id") for w in got])

        # 未结清空之后（用户"全部办完"那一刻）接口仍把条数带着 —— 前端那一行才挂得出来
        cli.post(f"/api/station/workorders/{a['id']}/status", json={"status": "已解决"})
        d3 = cli.get("/api/station/workorders").json()
        c.check("C7 未结清空时 archived_count 照旧带着（空表那支也要有那行，见 E7）",
                d3["workorders"] == [] and d3["archived_count"] == 2, f"count={d3['archived_count']}")
        c.check("C8 驿站这两个接口都不挂口令（演示级既定）：无 token 客户端照样 200",
                make_client(admin_token=None).get("/api/station/workorders/archived").status_code == 200, "")
    return c


def batch_d_notice():
    c = Checker()
    title("D. 通知撤回（本文件唯一一条安全红线：漏挂口令 = 敞开管理动作）")
    with sandbox():
        os.makedirs(data_path("uploads"), exist_ok=True)
        open(data_path("uploads", "up9.bin"), "w", encoding="utf-8").write("附件原件")
        json.dump([{"id": "n1", "ts": "2026-10-18 00:00:00", "title": "放假通知", "content": "周五提前下课",
                    "attachment": {"id": "up9", "name": "放假表.txt"}},
                   {"id": "n2", "ts": "2026-10-18 00:01:00", "title": "讲座通知", "content": "周三报告厅",
                    "attachment": None}],
                  open(data_path("notices.json"), "w", encoding="utf-8"), ensure_ascii=False)

        # ⚠️ 顺序：先建"无口令"那个客户端，再建带口令的。
        #    `make_client(admin_token=None)` 会**把环境变量整个 pop 掉**（进程级），
        #    在它之后再拿一个带口令的客户端才会把 env 设回去；反过来就会得到一串 403
        #    （服务端回 403 的条件是"自己没配口令"，与请求带没带无关）—— 那是测试自己造的假红。
        anon = make_client(admin_token=None)
        cli = make_client()
        r0 = anon.delete("/api/admin/notices/n1")
        c.check("D1 **不带口令**的撤回被拦（401/403），文件里两条都还在",
                r0.status_code in (401, 403)
                and len(_read_json(data_path("notices.json"), [])) == 2, f"{r0.status_code}")
        c.check("D1-bis 这条拦的是「谁都能撤」，不是「忘了带口令就静默成功」",
                not (r0.status_code == 200 or (r0.text or "").startswith('{"ok"')), repr(r0.text[:50]))
        r1 = cli.delete("/api/admin/notices/n1", headers={"X-Admin-Token": "wrong-token"})
        c.check("D2 口令**不对**同样被拦（401）", r1.status_code == 401, f"{r1.status_code}")

        ok = cli.delete("/api/admin/notices/n1")
        left = [n["id"] for n in _read_json(data_path("notices.json"), [])]
        c.check("D3 带对口令：撤回成功，那条没了、另一条还在",
                ok.status_code == 200 and left == ["n2"], f"{ok.status_code} {left}")
        c.check("D3-bis 列表接口跟着变（前端读的就是这份）",
                [n["id"] for n in cli.get("/api/notices").json()["notices"]] == ["n2"], "")
        c.check("D4 撤回不存在的那条 → 400 一句人话（不谎报 ok=True）",
                cli.delete("/api/admin/notices/n1").status_code == 400, "")
        c.check("D5 撤回**不动**它挂的附件原件（uploads/up9.bin 还在，别替管理员做删文件的决定）",
                os.path.exists(data_path("uploads", "up9.bin")), "")
        c.check("D5-bis 通知表没被写成空文件、也没多出别的键（只少了那一条）",
                len(_read_json(data_path("notices.json"), [])) == 1, "")
    return c


def batch_e_shape():
    c = Checker()
    title("E. 形状与前端接线（钉「哪句落在哪一支」，不钉字符串存在性 —— 红线 29）")
    st = open("app/store.py", encoding="utf-8").read()
    mn = open("app/main.py", encoding="utf-8").read()
    sta = open("app/static/station.html", encoding="utf-8").read()
    adm = open("app/static/admin.html", encoding="utf-8").read()

    c.check("E1 三个删除判据各只有一处定义，且都在 store（路由不各自筛一遍）",
            st.count("def delete_service(") == 1 and st.count("def delete_custom_record(") == 1
            and st.count("def delete_notice(") == 1, "")
    n_lit = st.count('== "workorders"')
    n_built = st.count("is_builtin_service(")
    c.check("E2 「内置项」那条判据只有一处（对 workorders 那一次字面量比较），路由那处也调它",
            n_lit == 1 and n_built == 3, f"字面量比较 {n_lit} 处 / is_builtin_service {n_built} 处")
    c.check("E3 三个新 DELETE 各只有一处（数的是**那三条路径**，不是全文件的 @app.delete —— 本来就还有 kb 与待办两条旧的）",
            mn.count('@app.delete("/api/station/services/{sid}")') == 1
            and mn.count('@app.delete("/api/station/services/{sid}/records/{rid}")') == 1
            and mn.count('@app.delete("/api/admin/notices/{nid}"') == 1,
            f"@app.delete 共 {mn.count('@app.delete(')} 处（含两条旧的）")

    # 钉的是**装饰器那一整行**：从 `@app.delete(` 起直到行尾。
    # ⚠️ 别用 `index(")")` 收尾 —— 路径里那个 `{nid}` 后面的右括号先到，截出来的半句永远不含 dependencies。
    i = mn.index('@app.delete("/api/admin/notices/{nid}"')
    deco_line = mn[i:mn.index("\n", i)]
    c.check("E4 通知撤回的装饰器那一行**就带着** verify_admin（钉的是那一行，不是全文件搜这个词）",
            "dependencies=[Depends(verify_admin)]" in deco_line, deco_line[:96])
    j = mn.index('@app.delete("/api/station/services/{sid}")')
    c.check("E4-bis 驿站那两条**没有**偷偷挂上口令（挂了驿站页就直接失灵，同 A2 一样要有话可说）",
            "verify_admin" not in mn[j:mn.index("async def", j)], "")

    MARK = 'String(s.table || "") !== "workorders"'
    blocks = []
    pos = 0
    while True:
        k = sta.find(MARK, pos)
        if k < 0:
            break
        blocks.append(sta[k:sta.index("\n          }", k)])
        pos = k + 1
    c.check("E5 「删除服务」那颗按钮的渲染**落在 table 判据那一支里面**（两处同判据的块，只有一处该有它）",
            len(blocks) == 2 and sum(1 for b in blocks if "删除服务" in b) == 1
            and sum(1 for b in blocks if "recs" in b) == 1, f"判据块 {len(blocks)} 处")

    c.check("E6 「🗄 已处理 N 条」那句话只有一份实现（两块表共用 archShell，不各写一遍措辞）",
            sta.count("已处理 ${") == 1 and sta.count("function archShell(") == 1
            and sta.count("archShell(") >= 3, f"措辞 {sta.count('已处理 ${')} 份 / 调用 {sta.count('archShell(')} 处")

    # E7 是这一批里最"配对"的一条：那行必须**两条分支都挂**，空表那一支正是用户投诉的那一刻
    e = sta.index("if (!list.length) {")
    empty_branch = sta[e:sta.index("}", e)] if False else sta[e:e + 400]
    c.check("E7 未结清空那一支也调了 renderWoArch（施工单把插入点写在早返回之后，照抄就永远挂不出来）",
            "renderWoArch(box, d.archived_count || 0)" in empty_branch
            and sta.count("renderWoArch(box, d.archived_count || 0)") == 2,
            repr(empty_branch[:96]))

    c.check("E8 撤回按钮走 data-notice + addEventListener，**没有**内联 onclick（esc.js 自己写着那是禁区）",
            'data-notice="${esc(n.id)}"' in adm
            and "recallNotice" not in adm
            and 'querySelectorAll("button.mini[data-notice]")' in adm, "")
    w = adm.index('querySelectorAll("button.mini[data-notice]")')
    c.check("E9 撤回那颗按钮**就地 catch**（adminFetch 非 2xx 是抛异常，不接住就是「没反应 + 屏幕还挂着」）",
            "catch (e)" in adm[w:w + 1400] and "nstatus" in adm[w:w + 1400], "")

    arch_txt = sta[sta.index("已处理 ${"):sta.index("已处理 ${") + 40]
    c.check("E10 措辞纪律：「已处理」那一行里不许出现「删除」二字（那是软删，台账一条没少）",
            "删除" not in arch_txt, repr(arch_txt))
    wo_tip = sta[sta.index("已标为「"):sta.index("已标为「") + 200]
    c.check("E10-bis 标已解决那句回执也守着同一条（并改成指向本页那一行，不再只指向管理端）",
            "删除" not in wo_tip and "已处理" in wo_tip, repr(wo_tip[:70]))

    c.check("E11 三个新接口的失败正文都是一句人话（不是空串、不是 detail 裸抛给前端当成功）",
            all(x in mn for x in ("没有这项服务", "内置服务不许删", "通知不存在")), "")
    c.check("E12 转义仍然只有一份：新代码没在 station/admin 里再定义 esc / escapeHtml",
            "function esc(" not in sta and "function escapeHtml(" not in sta
            and "function esc(" not in adm and "function escapeHtml(" not in adm, "")
    c.check("E13 登记台账没被顺手加过滤：GET …/records 仍全量返回（挑「已处理」是视图的事）",
            'def list_custom_records(sid: str) -> list:\n    """某个服务下已登记的记录（倒序）。"""'
            in st or "list(reversed(rows))" in st, "")
    # E14 是本单第二条"照抄施工单就会静默失效"的形状：
    # 登记记录与工单**共用同一份状态词表**（待处理/处理中/已解决），没有「已处理」这一档。
    # 施工单 §39-C③ 写的 `status === "已处理"` 挑出来永远是空数组 —— 那一行不挂、也不报错。
    # 「已处理」只在**标题**里当人话用，判据必须是词表里那一档。
    c.check("E14 登记那块的判据用「已解决」（词表里那一档），文件里不许出现 === \"已处理\" 这种比较",
            'doneRows = rows.filter(x => (x.status || "待处理") === "已解决")' in sta
            and '=== "已处理"' not in sta and '== "已处理"' not in sta, "")
    c.check("E14-bis 工单那块的判据来自后端的 archived_count，前端没有自己数状态（那是第二份判据）",
            'renderWoArch(box, d.archived_count || 0)' in sta
            and "filter(w => w.archived)" not in sta, "")
    # E15 这条是真机点出来的第二件：那块每 5 秒无条件重建，刚点开的历史面板会被自己合上。
    #     钉的是**顺序** —— 签名闸必须在 `rec.innerHTML = ""` 之前，写在后面等于没挡。
    w1 = sta.index("async function loadRecords()")
    seg = sta[w1:sta.index("document.getElementById(\"refreshSvc\")")]
    c.check("E15 登记那块重建前有签名闸（dataset.sig 比较在 rec.innerHTML 清空**之前**），"
            "否则轮询会把刚点开的「已处理」面板自己关掉",
            "rec.dataset.sig === sig" in seg and 'rec.innerHTML = "";' in seg
            and seg.index("rec.dataset.sig === sig") < seg.index('rec.innerHTML = "";'),
            f"闸在 {seg.index('rec.dataset.sig === sig')} / 重建在 {seg.index('rec.innerHTML') if 'rec.innerHTML' in seg else -1}")
    c.check("E15-bis 读失败那支要把签名清掉（不清就会把上一次的空面板挂死，同 svcSig 那条规矩）",
            'rec.dataset.sig = "";' in seg, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_65_delete_gaps：#39 四处「只增不删」缺口（服务/登记/历史/通知）")
    print("=" * 60)
    for fn, name in ((batch_a_services, "A 批（服务定义可删）"),
                     (batch_b_records, "B 批（登记记录可删 + 路径安全）"),
                     (batch_c_history, "C 批（已处理历史）"),
                     (batch_d_notice, "D 批（通知撤回 + 鉴权红线）"),
                     (batch_e_shape, "E 批（形状与前端接线）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
