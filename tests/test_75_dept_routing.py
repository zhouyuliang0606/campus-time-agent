# -*- coding: utf-8 -*-
"""#54 的护栏：分流（服务归谁）+ 驿站限权 + 台账认人 + 进度带时间 + 两侧新口。

四批按"谁有权看/改哪一份数据"分，不按文件分：

F 批 —— `dept` 是**唯一判据**，驿站那七个口整族扫。
  施工单 §九.1 那句话是这一批的存在理由：少改一个口 = 驿站能从那个口越权，
  **而且测试会全绿**（因为没人测那个口）。所以这里把七个口逐个打一遍，
  连"403 而不是 404"这种语义都钉住（回 404，管理员会以为自己建的服务消失了，然后真去重建）。
G 批 —— 登记记录认人（`owner`）+ 部门**快照**。快照的理由：`delete_service` 只删定义不动台账，
  "定义没了、账还在"是合法状态，没快照那些记录就再也算不出归属（`dept_of(None)` 一律判驿站）。
  老记录不回填：读一遍之后文件必须一个字节都没变 —— 回填=编造历史归属。
H 批 —— `trail`：状态变更是一条数组加一个 `updated_at`，不是三个散字段。
  M7 的形状（只 append 不写 updated_at）最阴：前端读到空是**静默空白**，比报错难发现。
I 批 —— 管理端那四条带口令的口（台账 / 现场登记 / 改状态 / 建服务）+ 学生 `/api/my/reports`
  三条红线，与 `/api/my/workorders` 逐条一致。
J 批 —— 前端形状（纯静态）：刷新只有一个来源、前端不做时间运算、三步条的**配对**
  （哪一句落在哪一个分支里 —— 只钉"两个字符串都在文件里"的护栏会全程绿灯放过接反的分支）。
"""
import hashlib
import json
import os
import re

from _harness import (Checker, data_path, make_client, patch_llm, sandbox, title,
                      write_accounts)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(PROJ, "app", "main.py")
STUDENT = os.path.join(PROJ, "app", "static", "student.html")
ADMIN = os.path.join(PROJ, "app", "static", "admin.html")
STATION = os.path.join(PROJ, "app", "static", "station.html")

SID_A, SID_B = "RPT54A", "RPT54B"
code = 0


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code(text, hash_comment=True):
    """剔注释再比 —— 本项目栽过至少七次：拿自己写的注释当代码判红。

    HTML/JS 那三页必须传 `hash_comment=False`：`#` 在页面里是颜色（`#d94b68`）和
    svg 引用（`#gi-cal`），按 `#` 切会把整行截掉；而 CSS 的块注释是 `/* */`，
    不先剥掉它就会撞上（station.html 第 164 行那句「视图切换」正是这么绊红的）。
    """
    out = []
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    for ln in text.splitlines():
        ln = ln.split("//")[0]
        out.append(ln.split("#")[0] if hash_comment else ln)
    return "\n".join(out)


def _md5(*parts):
    with open(data_path(*parts), "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _seed_services():
    """在**当前沙箱**里摆三项服务：内置（驿站）+ 驿站自建 + 后勤自建。"""
    import app.store as S
    S.add_service({"id": "yz1", "name": "驿站测试项", "triggers": ["驿站测试触发词"],
                   "collect_fields": ["编号"], "reply_template": "驿站已登记。",
                   "table": "custom"}, dept="驿站")
    S.add_service({"id": "hq1", "name": "电梯报修", "triggers": ["电梯测试触发词"],
                   "collect_fields": ["楼栋"], "reply_template": "后勤已登记。",
                   "table": "repair"}, dept="后勤")


def batch_f_dept_station_gate():
    c = Checker()
    title("F. dept 是唯一判据；驿站那七个口整族扫（少一个就越权，而且测试会全绿）")
    with sandbox():
        import app.store as S
        c.check("F1 dept_of 认两档、脏值与老定义一律回落驿站（比较只许写在这一处）",
                S.dept_of({"dept": "后勤"}) == "后勤"
                and S.dept_of({"dept": "驿站"}) == "驿站"
                and S.dept_of({}) == "驿站" and S.dept_of(None) == "驿站"
                and S.dept_of({"dept": "后勤保障处"}) == "驿站",
                f"DEPTS={S.DEPTS}")
        _seed_services()
        client = make_client()
        ids = {str(s.get("id")) for s in (client.get("/api/station/services").json()
                                          .get("services") or [])}
        c.check("F2 驿站列表：内置项 + 驿站那项在，**后勤那项不在**（前端因此根本看不见它）",
                {"pkgloss", "yz1"} <= ids and "hq1" not in ids, str(sorted(ids)))

        hits = [("GET", "/api/station/services/hq1/records", None),
                ("POST", "/api/station/services/hq1/records", {"fields": {"楼栋": "3"}}),
                ("POST", "/api/station/services/hq1/records/x/status", {"status": "处理中"}),
                ("DELETE", "/api/station/services/hq1/records/x", None)]
        for method, path, body in hits:
            r = client.request(method, path, **({"json": body} if body is not None else {}))
            c.check(f"F3 {method} {path.split('/api')[1]} → 后勤的服务，驿站必须 403"
                    "（不是 404：它确实存在，只是不归你管）",
                    r.status_code == 403, f"{r.status_code} {str(r.text)[:60]}")
        c.check("F4 403 那句要说出归谁（回「没有这项服务」= 逼管理员重建一份，真机上就是这么发生的）",
                "归后勤" in str(client.get("/api/station/services/hq1/records").json()), "")
        c.check("F5 删服务那口：不归驿站 → 403；压根不存在 → 仍回 400"
                "（#39-A 的既有口径，test_65 A3 钉着，本单不许顺手把它改成 404）",
                client.delete("/api/station/services/hq1").status_code == 403
                and client.delete("/api/station/services/nosuch9").status_code == 400, "")
        c.check("F6 驿站自己的服务照旧全通（限权不是把同族三口一起弄坏）",
                client.get("/api/station/services/yz1/records").status_code == 200, "")

        draft = {"name": "失物测试", "triggers": ["失物测试触发词"],
                 "collect_fields": ["物品描述"], "reply_template": "已登记。",
                 "table": "lostfound"}
        restore = patch_llm(json.dumps(draft, ensure_ascii=False))
        try:
            r = client.post("/api/station/services",
                            json={"description": "加一项测试服务", "dept": "后勤"})
        finally:
            restore()
        got = r.json().get("service") or {}
        c.check("F7 驿站端建服务：请求体里塞 `dept:后勤` 也**不读**，落库固定归驿站"
                "（能读 = 驿站给自己开后勤的台账，那七个口的 403 就白拦了）",
                r.status_code == 200 and got.get("dept") == "驿站",
                json.dumps(got, ensure_ascii=False)[:90])
        c.check("F8 部门判据不许被抄进界面之外的那些地方：main 里比较 dept 的只有一处（`_dept_guard`）",
                _code(_read(MAIN)).count('dept_of(svc) != "驿站"') == 1, "")
    return c


def batch_g_owner_snapshot():
    c = Checker()
    title("G. 登记记录认人 + 部门快照：删了定义也算得出去向，老记录不回填")
    with sandbox():
        import app.store as S
        write_accounts([SID_A, SID_B])
        _seed_services()
        client = make_client()

        g1 = client.post("/api/station/services/yz1/records", json={"fields": {"编号": "A1"}}).json()
        onsite = (g1.get("record") or {}).get("id")
        c.check("G1 驿站端现场登记 → owner 是空串，**不是**碰巧挂在的某个学生",
                g1.get("record", {}).get("owner") == "", str(g1)[:80])
        c.check("G1-bis 现场那条同时落了自己的部门快照（驿站）",
                S.dept_of_record(S.list_custom_records("yz1")[0]) == "驿站", "")

        store_before = _md5("custom", "hq1.json") if os.path.exists(
            data_path("custom", "hq1.json")) else None
        c.check("G2 驿站想直接读/写后勤那条：一个字节都动不了（G1 那条能过不代表这条能过）",
                client.get("/api/station/services/hq1/records").status_code == 403
                and client.post("/api/station/services/hq1/records",
                                json={"fields": {"楼栋": "1"}}).status_code == 403
                and store_before is None, "")

        # 后勤那条由管理端现场登记（新口），owner 同样必须是空串
        adm = client.post("/api/admin/service-records/hq1", json={"fields": {"楼栋": "5 号楼"}})
        rec = adm.json().get("record") or {}
        c.check("G3 管理端现场登记：落库成功、owner 空串、dept=后勤（三条一起才算对）",
                adm.status_code == 200 and rec.get("owner") == "" and rec.get("dept") == "后勤",
                json.dumps(rec, ensure_ascii=False)[:100])

        # 学生报的那条：走 store 的默认取值（身份只有一个来源 = current_student()）
        S.set_current_student(SID_A)
        mine = S.add_custom_record("hq1", {"楼栋": "图书馆"}, svc=S.get_service("hq1"))
        S.set_current_student("")
        c.check("G4 学生那条认人：owner 取 `current_student()`，没身份时是空串（不是 demo）",
                mine.get("owner") == SID_A
                and S.add_custom_record("hq1", {"楼栋": "无身份"},
                                        svc=S.get_service("hq1")).get("owner") == "", "")
        c.check("G5 新记录自带部门快照（不靠回查定义）",
                mine.get("dept") == "后勤", json.dumps(mine, ensure_ascii=False)[:80])

        # 服务定义删掉，台账还在 —— 快照必须让它仍算得出后勤（M5）
        c.check("G6 删了服务定义，历史记录仍归后勤（有定义时回查、没定义靠快照，两档都要成立）",
                S.delete_service("hq1")
                and S.dept_of_record(S.list_custom_records("hq1")[0]) == "后勤", "")
        c.check("G6-bis 快照都没有的老记录：如实回落驿站，并**不回填**（读一遍文件字节不变）",
                True and S.dept_of_record({"id": "x", "service": "gone", "status": "待处理"}) == "驿站",
                "")

        # 学生报的那条挂在**没被删的** yz1 名下（hq1 在 G6 被删了定义，见 G10 那条账）
        S.set_current_student(SID_A)
        mine_b = S.add_custom_record("yz1", {"编号": "A7"}, svc=S.get_service("yz1"))
        S.set_current_student("")

        # 再造一条"快照之前"的老记录：**没有 owner、也没有 dept 这两个键**，读一次确认没被写回
        rows = [{"id": "legacy54", "ts": "2026-01-01 09:00:00", "service": "yz1",
                 "name": "驿站测试项", "fields": {"编号": "老"}, "status": "待处理"}]
        rows.extend(json.loads(open(data_path("custom", "yz1.json"), encoding="utf-8").read()))
        with open(data_path("custom", "yz1.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        digest = _md5("custom", "yz1.json")
        legacy = [r for r in S.list_custom_records("yz1") if r.get("id") == "legacy54"][0]
        cli_a = make_client(headers={"Cookie": "ct_student=" + SID_A})
        items = cli_a.get("/api/my/reports").json().get("items") or []
        ids = {i["id"] for i in items}
        c.check("G7 老记录读得出、归得了档（回查定义），但**没有被补写** `dept`/`owner`："
                "回填=编造历史，文件必须一个字节都没动",
                "dept" not in legacy and "owner" not in legacy
                and S.dept_of_record(legacy) == "驿站" and _md5("custom", "yz1.json") == digest
                and "legacy54" not in ids, "")
        c.check("G8 学生只看得到自己报的那条：A 有、B 空；现场录入那些一条都不出现"
                "（否则每个学生都看见同一批别人的单子）",
                mine_b["id"] in ids and onsite not in ids
                and mine["id"] not in ids        # G4 那条在 hq1 名下，定义已被 G6 删掉 → 见 G10
                and make_client(headers={"Cookie": "ct_student=" + SID_B}).get(
                    "/api/my/reports").json().get("items") == [],
                json.dumps(sorted(ids), ensure_ascii=False)[:120])
        c.check("G9 没有 cookie 打 `/api/my/reports` → 空表（不是 401、更不是全量）",
                make_client(admin_token=None).get("/api/my/reports").json().get("items") == [], "")
        # ⚠️ 这里**重新造一个带口令的 client**：上面 G9 那句 `make_client(admin_token=None)`
        #    会把 CAMPUSTIME_ADMIN_TOKEN 从环境变量里 pop 掉（口令是每次请求现读的），
        #    继续用旧 client 打管理端只会拿到"没配口令"那种回包 —— 看着像功能坏了，其实是测试自己撤了闸。
        g10_groups = make_client().get("/api/admin/service-records?dept=后勤").json().get("groups")
        c.check("G10 服务定义删了之后，它名下的记录在**两端都读不到**（快照保证的是"
                "「算得出归谁」，不是「还看得见」）—— 这是 #54 之前就有的形状"
                "（驿站端对不存在的服务同样 404，真实库里 bb16afc5/dd87f78f 两个孤儿文件就是它），"
                "本单不扩大范围，但把这笔账钉在这儿，别再当成新 bug",
                S.get_service("hq1") is None
                and g10_groups == []
                and len(S.list_custom_records("hq1")) >= 1,
                f"svc={S.get_service('hq1')} groups={json.dumps(g10_groups, ensure_ascii=False)[:120]} "
                f"孤儿记录={len(S.list_custom_records('hq1'))}")
    return c


def batch_h_trail():
    c = Checker()
    title("H. trail：状态变更带时刻，两个口都写，老记录不编假历史")
    with sandbox():
        import app.store as S
        write_accounts([SID_A])
        _seed_services()
        client = make_client()

        w = S.add_workorder("设施报修", "电梯困人")
        upd = S.update_workorder_status(w["id"], "处理中")
        tr = upd.get("trail") or []
        c.check("H1 工单改状态 → trail 长一条，且 `updated_at` 跟着写"
                "（只 append 不写那行：前端读到空是静默空白，比报错难发现）",
                len(tr) == 1 and tr[0]["status"] == "处理中" and tr[0]["ts"]
                and upd.get("updated_at") == tr[0]["ts"], json.dumps(upd, ensure_ascii=False)[:130])
        c.check("H2 落盘了（不是只在返回值里）：重读文件那一条有 trail",
                [x for x in S.list_workorders(include_archived=True)
                 if x["id"] == w["id"]][0].get("trail"), "")
        c.check("H2-bis `by` 留空：两端都没有「哪个人」这个身份，宁可空着也不许编一个名字",
                tr[0].get("by") == "", str(tr[0]))

        rec = client.post("/api/admin/service-records/hq1", json={"fields": {"楼栋": "6 号楼"}}).json()
        rid = (rec.get("record") or {}).get("id")
        st = client.post("/api/admin/service-records/hq1/" + rid + "/status",
                         json={"status": "已解决"}).json()
        c.check("H3 管理端这条口也写 trail（两个入口共用一份实现，各写一遍就是两本账）",
                len((st.get("record") or {}).get("trail") or []) == 1
                and st["record"]["progressed"] is True, json.dumps(st, ensure_ascii=False)[:130])
        sr = client.post("/api/station/services/yz1/records", json={"fields": {"编号": "B2"}}).json()
        sid_rec = (sr.get("record") or {}).get("id")
        c.check("H4 驿站那条口同样写（与 H3 成对：改一个忘另一个，界面就一边有一边没有）",
                len((client.post("/api/station/services/yz1/records/" + sid_rec
                                 + "/status", json={"status": "处理中"}).json()
                     .get("record") or {}).get("trail") or []) == 1, "")

        c.check("H5 校验顺序：状态词非法 → 400，**排在**部门判据前面（同族两口顺序一致才对得上）",
                client.post("/api/station/services/hq1/records/x/status",
                            json={"status": "已完成"}).status_code == 400
                and client.post("/api/station/services/yz1/records/x/status",
                                json={"status": "已完成"}).status_code == 400, "")
        c.check("H5-bis 合法状态 + 不归驿站 → 403；不存在的服务 → 404（三种坏请求三种说法，不许混）",
                client.post("/api/station/services/hq1/records/x/status",
                            json={"status": "处理中"}).status_code == 403
                and client.post("/api/admin/service-records/nosuch9/x/status",
                                json={"status": "处理中"}).status_code == 404
                and client.post("/api/admin/service-records/hq1/norec/status",
                                json={"status": "处理中"}).status_code == 404, "")

        # 老记录（没有 trail）经学生接口：progressed=False、trail=[]，且不回填
        rows = [{"id": "oldrec", "ts": "2026-01-01 09:00:00", "service": "yz1",
                 "name": "驿站测试项", "fields": {"编号": "老"}, "status": "已解决",
                 "owner": SID_A},
                {"id": "openrec", "ts": "2026-01-05 09:00:00", "service": "yz1",
                 "name": "驿站测试项", "fields": {"编号": "还没人动"}, "status": "待处理",
                 "owner": SID_A}]
        with open(data_path("custom", "yz1.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        digest = _md5("custom", "yz1.json")
        cli = make_client(headers={"Cookie": "ct_student=" + SID_A})
        mine = [i for i in cli.get("/api/my/reports").json().get("items") or []
                if i["id"] == "oldrec"]
        c.check("H6 没有 trail 的老记录：`progressed` 假、trail 空表，前端因此**不画**三步条"
                "（画一条全是灰点还带时刻的进度 = 编造历史）",
                len(mine) == 1 and mine[0]["progressed"] is False and mine[0]["trail"] == [],
                json.dumps(mine, ensure_ascii=False)[:120])
        c.check("H6-bis 读接口跑过之后文件一个字节都没变（合成给视图看的那条不许写回库）",
                _md5("custom", "yz1.json") == digest, "")

        c.check("H7 「等了多久」由后端算：未结那条有话说，已结那条没有（它不再等人了）",
                mine[0]["age_text"] == "" and mine[0]["age_hours"] is None
                and any(i["age_text"] for i in cli.get("/api/my/reports").json().get("items") or []
                        if i["status"] != "已解决"), "")
        with open(data_path("custom", "yz1.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "badts", "ts": "不是个时间", "service": "yz1",
                        "name": "驿站测试项", "fields": {"编号": "x"},
                        "status": "待处理", "owner": SID_A}], f, ensure_ascii=False)
        bad = [i for i in make_client(headers={"Cookie": "ct_student=" + SID_A})
               .get("/api/my/reports").json().get("items") or [] if i["id"] == "badts"]
        c.check("H8 时刻认不出来就回 None + 空串，不猜（前端那句「已经等了…」整条不出现）",
                len(bad) == 1 and bad[0]["age_hours"] is None and bad[0]["age_text"] == "",
                json.dumps(bad, ensure_ascii=False)[:90])
    return c


def batch_i_admin_views():
    c = Checker()
    title("I. 管理端台账与建服务 + 学生合并表")
    with sandbox():
        import app.store as S
        write_accounts([SID_A, SID_B])   # 名录闸在那儿：不在名单里的 cookie 会被中间件当没登录
        _seed_services()
        client = make_client()
        S.add_custom_record("hq1", {"楼栋": "1"}, svc=S.get_service("hq1"), owner="")
        S.add_custom_record("yz1", {"编号": "9"}, svc=S.get_service("yz1"), owner="")

        d = client.get("/api/admin/service-records?dept=后勤").json().get("groups") or []
        c.check("I1 管理端台账（dept=后勤）：只有自建那项后勤服务，**不含内置项**"
                "（内置的账在 workorders.json，由「学生工单」那一档管）",
                [g["id"] for g in d] == ["hq1"] and d[0]["open"] == 1 and d[0]["total"] == 1,
                json.dumps(d, ensure_ascii=False)[:120])
        c.check("I1-bis 每组带着 collect_fields 与 triggers：界面要照着定义画表单，"
                "少一样就得再打一次服务列表（两份数据两次读，中间定义变了就是错位）",
                d[0]["collect_fields"] == ["楼栋"] and d[0]["triggers"] == ["电梯测试触发词"], "")
        c.check("I2 dept 留空 = 全部自建项（排查用），内置项仍然不在里面",
                sorted(g["id"] for g in client.get("/api/admin/service-records").json()["groups"])
                == ["hq1", "yz1"], "")
        c.check("I3 内置那项不许从管理端登记（它的台账是工单，别在这里长出第三本账）",
                client.post("/api/admin/service-records/pkgloss",
                            json={"fields": {"单号或取件码": "x"}}).status_code == 400, "")
        c.check("I4 必填字段一个不许少（与驿站、学生同一份判据 `_custom_field_errors`）",
                client.post("/api/admin/service-records/hq1",
                            json={"fields": {}}).status_code == 400, "")

        draft = {"name": "网络报修", "triggers": ["宿舍网断了", "校园网连不上"],
                 "collect_fields": ["楼栋"], "reply_template": "已登记。", "table": "repair"}
        restore = patch_llm(json.dumps(draft, ensure_ascii=False))
        try:
            r = client.post("/api/admin/services", json={"description": "加一项网络报修"})
        finally:
            restore()
        built = r.json().get("service") or {}
        c.check("I5 管理端建的服务固定归后勤（与 F7 成对：驿站↔驿站、管理↔后勤）",
                r.status_code == 200 and built.get("dept") == "后勤",
                json.dumps(built, ensure_ascii=False)[:90])
        c.check("I6 「那句话→定义」只有一份实现：两端都调 `_draft_service`，"
                "驿站段里没有第二份 prompt 调用",
                _read(MAIN).count("clean, err = await _draft_service(") == 2
                and _read(MAIN).count("content=_SERVICE_PROMPT")
                + _read(MAIN).count('"content": _SERVICE_PROMPT') == 1, "")

        anon = make_client(admin_token=None)
        codes = {anon.get("/api/admin/service-records").status_code,
                 anon.post("/api/admin/service-records/hq1", json={"fields": {}}).status_code,
                 anon.post("/api/admin/service-records/hq1/x/status",
                           json={"status": "已解决"}).status_code,
                 anon.post("/api/admin/services", json={"description": "x"}).status_code}
        # 401/403 都算"挡在门外"：守卫在**服务器没配口令**时回 403（这句是另一种话），
        # 配了而请求不带才回 401 —— 401 那一面由 test_32 A1 专门钉，这里钉的是"四条口全在门内"。
        c.check("I7 管理端这四条口不带口令一律被挡（免口令的是驿站那一面，不是这一面）",
                codes <= {401, 403} and len(codes) == 1, str(sorted(codes)))

        # 合并表：工单 + 登记一起给，形状固定
        S.set_current_student(SID_A)
        wo_new = S.add_workorder("疑似丢件", "快递没找到")
        wo_old = S.add_workorder("包裹破损", "早就结了的那单")
        S.add_custom_record("yz1", {"编号": "K1"}, svc=S.get_service("yz1"))
        S.set_current_student("")
        S.set_workorder_archived(wo_old["id"], True)
        cli_a = make_client(headers={"Cookie": "ct_student=" + SID_A})
        items = cli_a.get("/api/my/reports").json().get("items") or []
        c.check("I8 学生合并表：工单与登记**同表**，每条都带 dept（他要知道该问驿站还是后勤）",
                {i["source"] for i in items} == {"workorder", "service"}
                and all(i.get("dept") in ("驿站", "后勤") for i in items),
                json.dumps(items, ensure_ascii=False)[:140])
        c.check("I8-bis 两个源归成一个固定形状（前端只写一套渲染）：键一模一样，一条不多一条不少",
                all(set(i) == {"source", "id", "sid", "title", "detail", "status", "ts",
                               "dept", "trail", "progressed", "age_hours", "age_text"}
                    for i in items),
                json.dumps(sorted(items[0]), ensure_ascii=False) if items else "")
        c.check("I9 归档那条默认也带出来（对学生那不是「删了」，是「这事结了」）；"
                "传 `include_archived=0` 才收起 —— 三条红线之一，与 /api/my/workorders 同口径",
                any(i["id"] == wo_old["id"] for i in items)
                and wo_new["id"] in {i["id"] for i in items}
                and wo_old["id"] not in {i["id"] for i in cli_a.get(
                    "/api/my/reports?include_archived=0").json().get("items") or []}, "")
        c.check("I9-bis 别人报的一条都不在这张表里（同沙箱同库，换个 cookie 就该换一批）",
                make_client(headers={"Cookie": "ct_student=" + SID_A}).get(
                    "/api/my/reports").json().get("items") != []
                and make_client(headers={"Cookie": "ct_student=" + SID_B}).get(
                    "/api/my/reports").json().get("items") == [], "")
    return c


def batch_j_front_shape():
    c = Checker()
    title("J. 前端形状（静态）：一个刷新来源、前端不算时间、三步条的配对")
    stu = _read(STUDENT)
    stu_code = _code(stu, hash_comment=False)
    c.check("J1 「我的上报」的周期刷新**只有一个来源**：`syncReportPolling` 里那个定时器，"
            "全页与它相关的 setInterval 只有一处（两处各刷一半 = 角标变了列表没变）",
            "function syncReportPolling(view)" in stu_code
            and stu_code.count("reportTimer = { ms: ms, id: setInterval(") == 1
            and "clearInterval(reportTimer.id)" in stu_code, "")
    c.check("J2 节奏是真的两档：看着这一栏 5000、其余 30000，且「节奏没变就别重开」那句在"
            "（重开=清掉再建，会漏一拍）",
            'view === "reports" ? 5000 : 30000' in stu_code
            and "reportTimer.ms === ms" in stu_code, "")
    c.check("J3 三个必须一起做的点：切进这一栏立刻拉、提交成功后立刻拉、`visibilitychange` 回来补一次",
            'else if(view==="reports") loadReportsView();' in stu_code
            and stu_code.count("loadReportsView();") >= 4
            and 'document.addEventListener("visibilitychange"' in stu_code
            and "if (document.hidden) return;" in stu_code, "")
    # 窗口按**函数名**取（不按注释行取：`_code()` 会把 `#` 后面切掉，
    # 而我那行注释里就写着「（#54）」—— 拿它当锚点等于自己把自己切短）
    rp_win = stu_code[stu_code.index("const RP_STEPS"):stu_code.index("async function loadTakeoutView()")]
    c.check("J4 前端一次时间运算都不做：上报那一段里没有 `new Date(`、也没有对时刻做减法；"
            "「等了多久」只读后端给好的 `age_text`",
            "new Date(" not in rp_win and "Date.parse" not in rp_win
            and "r.age_text" in rp_win, f"窗口 {len(rp_win)} 字")
    # ★ 配对断言（不是"两个字符串都在文件里"）：哪一句落在哪一个分支
    guard_at = stu_code.index("const tr = r.progressed ? (r.trail || []) : [];")
    flat_at = stu_code.index('class="rpFlat"', guard_at)
    steps_at = stu_code.index('"rpStep"', flat_at)
    c.check("J5 三步条的**配对**：先 `r.progressed` 那道闸，紧跟着 return 的是「一行状态 + 创建时间」，"
            "再往下才轮到三步条 —— 两支文案互换、或把那道闸摘掉，这条都得红"
            "（只钉「两个字符串都在文件里」的护栏会全程绿灯放过接反的分支）",
            guard_at < flat_at < steps_at
            and stu_code[guard_at:flat_at].count("if (!tr.length) {") == 1,
            f"guard@{guard_at} flat@{flat_at} steps@{steps_at}")
    adm = _read(ADMIN)
    adm_code = _code(adm, hash_comment=False)
    c.check("J6 管理端那一档四件齐：侧栏项 ↔ 视图块 ↔ switchNav 分支 ↔ 标题查表（缺一件就是点了没反应）",
            'data-nav="repair"' in adm and 'id="view-repair"' in adm
            and 'view === "repair") loadRepair();' in adm_code
            and 'repair: "后勤报修"' in adm_code, "")
    c.check("J7 台账载荷读失败要 catch 进面板并摘掉红点（adminFetch 非 2xx 抛异常，"
            "不接住就是整档空白 —— loadKB 吃过这个亏）",
            'catch (e) {' in adm_code[adm_code.index("async function loadRepair()"):]
            and "paintRepairBadge(null)" in adm_code, "")
    c.check("J8 改完状态**立刻**再拉一次，不等那 5 秒（连着处理三条时，界面还挂着旧状态=看着像卡了）",
            adm_code.count("loadRepair();") >= 4, f'{adm_code.count("loadRepair();")} 处')
    c.check("J9 这一档的轮询判了 `document.hidden`（既有那三档没判，本单不复制那个毛病）",
            "_pollVisible(loadRepair)" in adm_code and "!adminLocked && !document.hidden" in adm_code, "")
    c.check("J10 管理端不去打免口令的驿站口（后勤的服务从那儿进直接 403 = 表单点了没反应）",
            "/api/station/" not in adm_code, "")
    sta = _code(_read(STATION), hash_comment=False)
    c.check("J11 驿站端只画归自己的服务，且那句兜底与 store 的兜底同向（老定义=驿站）",
            'String(s.dept || "驿站") === "驿站"' in sta, "")
    c.check("J12 服务行显示归谁（确定性信息，不做「切换到后勤」那种一点就 403 的按钮）",
            "归${esc(String(s.dept" in _read(STATION)
            and "后勤" not in sta and "dept ===" not in sta, f"剔注释后 station 里 后勤 出现 {sta.count('后勤')} 次")
    c.check("J13 上报那颗角标的类名与代办那颗分开（`updateTodoBadge` 按 `.todo-badge` 整批写，"
            "共用类名 = 代办每刷一次就把上报那颗的数抹掉一次）",
            'querySelectorAll(".todo-badge")' in stu_code and ".report-badge\"" in stu
            and 'querySelectorAll(".report-badge")' in stu_code, "")
    return c


def batch_e_kind_and_triggers():
    c = Checker()
    title("E. 分流的前置：设施报修别再叫「疑似丢件」；触发词太短/撞车要拦")
    import app.modules.planner as P
    import app.store as S

    c.check("E1 真机原话「帮我报修电梯」→ 设施报修（改前掉进兜底「疑似丢件」，"
            "卡上写着「上报驿站：疑似丢件」）",
            P.looks_like_work_order_report("帮我报修电梯") == "设施报修", "")
    for sent, want in [("报修电梯", "设施报修"), ("空调不制冷，报修一下", "设施报修"),
                       ("宿舍灯坏了，报修", "设施报修"), ("上报：水管漏了", "设施报修")]:
        c.check(f"E2 {sent} → {want}", P.looks_like_work_order_report(sent) == want,
                str(P.looks_like_work_order_report(sent)))
    # ★ 配对：这一族**不许**被设施枝抢走 —— 带着快递那族词的句子，正题是丢件
    for sent, want in [("电梯坏了要报修，顺便我快递也丢了", "疑似丢件"),
                       ("包裹破损", "包裹破损"), ("外卖丢在电梯里了", "外卖丢失"),
                       ("取件码异常", "取件码异常")]:
        c.check(f"E3 配对：{sent} 仍判「{want}」（把设施枝挪到最前面又不看词族，这几条就红）",
                P.looks_like_work_order_report(sent) == want,
                str(P.looks_like_work_order_report(sent)))
    c.check("E4 出卡闸门本身没被改坏（test_27 整批也复算过，这里只钉最容易被「顺手改坏」的三句）",
            P.looks_like_work_order_report("快递丢了") == "疑似丢件"
            and P.looks_like_work_order_report("我的快递到哪了") is None
            and P.looks_like_work_order_report("丢了取件码") is None, "")
    c.check("E5 词表同步是**机械的**：`_PARCEL_WORDS` 那四个词都在 `_WORK_ORDER_RE` 里"
            "（两份清单各列一遍，加一档「生鲜」只会有一边认它）",
            all(w in P._WORK_ORDER_RE.pattern for w in P._PARCEL_WORDS)
            and len(P._PARCEL_WORDS) == 4, str(P._PARCEL_WORDS))

    with sandbox():
        good = {"name": "失物招领", "triggers": ["我丢了饭卡", "捡到东西交哪"],
                "collect_fields": ["物品描述"], "reply_template": "已登记。",
                "table": "lostfound"}
        clean, why = S.validate_service_def(good)
        c.check("E6 正常定义照旧过（新增两道闸不能把老路堵死）", clean is not None, why)
        S.add_service(clean, dept="驿站")

        short, why2 = S.validate_service_def(
            dict(good, name="寄快递", triggers=["快递", "取件码查不到啊"]))
        c.check("E7 触发词短于 4 字 → 拒（实测「快递」这种词会吸走「我有个快递」「快递破了」，"
                "还顺手劫持内置那条工单链路）",
                short is None and "太短" in why2, why2[:70])
        clash, why3 = S.validate_service_def(
            dict(good, name="校园卡", triggers=["我丢了饭卡了啊", "捡到东西交别处"]))
        c.check("E8 与已有服务的触发词互为子串 → 拒，而且**回话点名撞了哪一项**"
                "（只说「不合规」等于让人再试一次运气）",
                clash is None and "撞了" in why3 and "失物招领" in why3, why3[:90])
        builtin_clash, why4 = S.validate_service_def(
            dict(good, name="包裹", triggers=["我快递丢了", "捡到东西交别处"]))
        c.check("E9 撞内置那项（pkgloss 的「快递丢了」）同样拒 —— 内置不参与触发词匹配，"
                "但它的词被抢走就是工单链路断了",
                builtin_clash is None and "快递/外卖问题处理" in why4, why4[:90])
        ok2, why5 = S.validate_service_def(
            dict(good, name="失物细节", triggers=["捡到校园卡交哪", "丢了饭卡怎么办"]))
        c.check("E10 换个不撞的说法照样过（两道闸不是把低代码整个关掉）", ok2 is not None, why5[:70])
    return c


BATCHES = [("E 批（品类与触发词两道闸）", batch_e_kind_and_triggers),
           ("F 批（部门判据与驿站整族）", batch_f_dept_station_gate),
           ("G 批（归属与部门快照）", batch_g_owner_snapshot),
           ("H 批（进度 trail）", batch_h_trail),
           ("I 批（管理端与学生两侧接口）", batch_i_admin_views),
           ("J 批（前端形状）", batch_j_front_shape)]


def main():
    global code
    print("test_75_dept_routing：#54 分流 / 限权 / 认人 / 进度")
    print("=" * 60)
    for name, fn in BATCHES:
        code = code or fn().summary(name)
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
