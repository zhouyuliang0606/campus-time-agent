"""#29：工单与学生消息的**归档**（软删）。

用户拍板三件：① 做成归档、不做硬删；② 双写那条镜像一起走；③ 只挂管理端。
这个文件就照着这三件钉，外加一条容易漏的：归档**不能改变条数** ——
"看不见"和"没了"是两回事，判据一律回到文件里数。

⚠️ 全程在 sandbox 里，真实台账一个字节不动（`_harness.sandbox` 那份 copytree）。
"""
import json
import os
import re

from _harness import Checker, data_path, make_client, sandbox, title

code = 0


def _rows(name):
    p = data_path(name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def _ids(lst):
    return [str(x.get("id") or "") for x in lst]


def batch_a_soft_not_hard():
    c = Checker()
    title("A. 归档是**软**的：列表收起，文件里那条一个字节不少")
    with sandbox():
        cli = make_client()
        wid = cli.post("/api/workorders", json={"kind": "疑似丢件", "desc": "护栏用的样本",
                                                "source": "panel"}).json()["workorder"]["id"]
        wo_before = len(_rows("workorders.json"))
        msg_before = len(_rows("student_messages.json"))

        r = cli.post(f"/api/workorders/{wid}/archive", json={})
        c.check("归档口回 200，回执里带 archived 与归档时刻",
                r.status_code == 200 and (r.json().get("workorder") or {}).get("archived") is True
                and bool((r.json().get("workorder") or {}).get("archived_at")), str(r.json())[:90])

        disk = [w for w in _rows("workorders.json") if w.get("id") == wid]
        c.check("文件里那条**还在**（这是与硬删的分界，也是「随时能放回」的前提）",
                len(disk) == 1 and disk[0].get("archived") is True, f"找到 {len(disk)} 条")
        c.check("条数没变：工单不多不少，消息也没被顺手删",
                len(_rows("workorders.json")) == wo_before
                and len(_rows("student_messages.json")) == msg_before,
                f"{wo_before}->{len(_rows('workorders.json'))}")

        default = _ids(cli.get("/api/workorders").json().get("workorders") or [])
        withp = _ids(cli.get("/api/workorders?include_archived=1").json().get("workorders") or [])
        c.check("默认列表看不见它；带 include_archived=1 又看得见（同一个判据在两处读口都成立）",
                wid not in default and wid in withp, f"default={len(default)} with={len(withp)}")
        c.check("归档后**不放回就一直在文件里**：连归档两次不许把那条归档两遍或多塞一条",
                cli.post(f"/api/workorders/{wid}/archive", json={}).status_code == 200
                and len([w for w in _rows("workorders.json") if w.get("id") == wid]) == 1, "")

        back = cli.post(f"/api/workorders/{wid}/archive", json={"archived": False}).json()
        after = [w for w in _rows("workorders.json") if w.get("id") == wid][0]
        c.check("放回是把标记**摘掉**（不是留一个 archived=false 在那儿 —— 判据用的是 not archived，"
                "留着假字段迟早有一天有人改成 'archived' in row）",
                "archived" not in after and "archived_at" not in after
                and (back.get("workorder") or {}).get("archived") is None, str(after)[:90])
        c.check("放回后默认列表又看见它", wid in _ids(cli.get("/api/workorders").json()["workorders"]), "")
    return c


def batch_b_cascade():
    c = Checker()
    title("B. 双写级联：新记录认 ref、老记录认回话里那句「工单 xxx」，两支都要")
    with sandbox():
        cli = make_client()
        wid = cli.post("/api/workorders", json={"kind": "外卖丢失", "desc": "级联样本",
                                                "source": "panel"}).json()["workorder"]["id"]
        fresh = [m for m in _rows("student_messages.json") if m.get("ref") == wid]
        c.check("立案时双写那条带上了 ref（指向工单 id）—— 这是级联能可靠认到的前提",
                len(fresh) == 1 and fresh[0].get("kind") == "report", str(fresh)[:110])

        # 手工塞一条"加 ref 之前立案"的老镜像：没有 ref，只有回话里那个编号
        rows = _rows("student_messages.json")
        rows.append({"id": "legacyx1", "ts": "2026-01-01 00:00:00", "module": "workorder",
                     "message": "老记录｜没带 ref", "kind": "report",
                     "answer": f"已立案（工单 {wid}），状态：待处理"})
        json.dump(rows, open(data_path("student_messages.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

        d = cli.post(f"/api/workorders/{wid}/archive", json={}).json()
        c.check("归档工单 → 两条镜像（带 ref 的 + 只认编号的）**一起**收起，回执如实报数",
                d.get("mirrored_messages") == 2, str(d)[:100])
        ids = _ids(cli.get("/api/admin/messages").json().get("messages") or [])
        mid = fresh[0]["id"]
        c.check("收件箱默认列表里这两条都不出现了", mid not in ids and "legacyx1" not in ids, "")
        withp = _ids(cli.get("/api/admin/messages?include_archived=1").json().get("messages") or [])
        c.check("但文件里、带参数的列表里它们都还在（收起≠删除）",
                mid in withp and "legacyx1" in withp
                and len([m for m in _rows("student_messages.json") if m.get("id") in (mid, "legacyx1")]) == 2,
                f"with={len(withp)}")

        cli.post(f"/api/workorders/{wid}/archive", json={"archived": False})
        ids2 = _ids(cli.get("/api/admin/messages").json().get("messages") or [])
        c.check("放回工单时那两条也一起回来（级联是双向的，只做单向就是半个功能）",
                mid in ids2 and "legacyx1" in ids2, "")

        # 反向：单归档一条消息，不许把它的工单也收掉
        cli.post(f"/api/admin/messages/{mid}/archive", json={})
        c.check("单条消息归档**不反带动工单**（删镜像不该让案底消失；要一起收就走工单那个口）",
                wid in _ids(cli.get("/api/workorders").json()["workorders"]), "")
        cli.post(f"/api/admin/messages/{mid}/archive", json={"archived": False})
    return c


def batch_c_gates():
    c = Checker()
    title("C. 谁能归档、边界怎么回")
    with sandbox():
        cli = make_client()
        wid = cli.post("/api/workorders", json={"kind": "包裹破损", "desc": "闸门样本",
                                                "source": "panel"}).json()["workorder"]["id"]
        c.check("未知工单 id → 404（不假装成功）",
                cli.post("/api/workorders/nope/archive", json={}).status_code == 404, "")
        c.check("未知消息 id → 404",
                cli.post("/api/admin/messages/nope/archive", json={}).status_code == 404, "")
        c.check("body 里没带 archived → 默认按「归档」办（按钮就是默认档）",
                cli.post(f"/api/workorders/{wid}/archive", json={}).status_code == 200
                and [w for w in _rows("workorders.json") if w.get("id") == wid][0].get("archived") is True, "")
        c.check("驿站端读口不出现归档的那条（驿站看到的=要办的）",
                wid not in _ids(cli.get("/api/station/workorders").json()["workorders"]), "")

        # ⚠️ 免口令这一段放最后做：make_client(admin_token=None) 会连服务端口令一起清掉
        cli.post(f"/api/workorders/{wid}/archive", json={"archived": False})   # 先放回，让"被拒后没写脏"可观察
        before = [w for w in _rows("workorders.json") if w.get("id") == wid][0]
        anon = make_client(admin_token=None)
        r = anon.post(f"/api/workorders/{wid}/archive", json={})
        after = [w for w in _rows("workorders.json") if w.get("id") == wid][0]
        c.check("免口令不能归档（401/403 都算拦住）—— 归档只挂管理端，"
                "驿站端那道面免口令，免口令还能收台账=谁都能清工单",
                r.status_code in (401, 403), str(r.status_code))
        c.check("被拒那次**一个字节没写**（拒了还落库是最坏的一种：状态悄悄变了）",
                "archived" not in before and "archived" not in after, str(after)[:80])
    return c


def _strip(text):
    """剔注释（HTML 块、CSS/JS 多行块、JS 行注释）—— 负向断言一律先剥再比。

    ⚠️ 别拿 `for ln: re.sub("/\\*.*?\\*/")` 那种逐行写法糊弄过去：本页那段说明原生 confirm 的注释
    是**跨行**的块注释，逐行正则一行都剥不掉，于是"不许出现 confirm(" 被自己的注释绊红（本仓第九次）。
    """
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(ln.split("//")[0] for ln in t.splitlines())


def batch_d_shape():
    c = Checker()
    title("D. 形状与前端：一份实现、按钮选择器、驿站端没有归档口")
    mn = open("app/main.py", encoding="utf-8").read()
    st = open("app/store.py", encoding="utf-8").read()
    page = open("app/static/admin.html", encoding="utf-8").read()
    stc = _strip(page)

    seg = mn[mn.index("# ============ 驿站侧接口"):mn.index('@app.post("/api/admin/notify"')]
    c.check("驿站端那一段里不许出现归档口（免鉴权面不扩大，与 test_32 E2b 同形各钉一遍）",
            "archive" not in seg, "")
    # 判据是"谁在挑东西"：main 里 `"archived"` 这个键**只许**出现在"从请求体读开关"那一处写法里，
    # 出现别的用法（在 main 里按它过滤/拼装）就说明收起的逻辑漂到了第二处。
    # （不能简单禁 "archived" 这个词 —— 参数名本身就叫 include_archived，那样是恒红。）
    occ = re.findall(r'^.*"archived".*$', mn, flags=re.M)
    stray = [ln.strip()[:70] for ln in occ if "body.get(" not in ln]
    c.check("过滤只写在 store 一处：main 里对 archived 的引用只许是读请求体那个开关",
            not stray and st.count("if not include_archived:") == 2
            and 'w.get("archived")' in st and 'm.get("archived")' in st,
            f"越界 {len(stray)} 处：{stray[:2]}")

    c.check("级联在 store 一个函数里做完（main 里不许出现「归档接口再调一次消息接口」那种两段式）",
            "set_message_archived" not in mn[mn.index("async def workorder_archive"):
                                             mn.index("async def message_archive")], "")
    c.check("确认框一份实现：askConfirm 定义 1 处，本页不再留原生 confirm(（剔注释后比）",
            page.count("function askConfirm(") == 1 and "confirm(" not in stc,
            "还留着原生 confirm" if "confirm(" in stc else "")
    c.check("状态按钮的选择器收成 .mini[data-wo] —— 这一片里有三种 .mini，"
            "笼统绑定会把状态处理挂到归档键上（dataset.st 是 undefined → 那个口回 400）",
            'querySelectorAll(".mini[data-wo]")' in page and "bindArchButtons(" in page
            and stc.count("bindArchButtons(") == 3, "")
    c.check("红点只数没归档的未结（收了还喊有 N 条活 = 按钮的效果被吞掉）",
            "live.filter(w => w.status" in page and "paintWoBadge(live." in page, "")
    c.check("消息那个灰计数同样只数 live，不拿全量凑数",
            "paintMsgCount(live.length)" in page, "")
    c.check("界面上把性质写在脸上：说清「归档=台账里那条没动、可放回」",
            "台账里那条没动，可放回" in page and "归档时网络出问题了" in page, "")
    # 真机第一版露馅的地方：工单归档级联改了收件箱，但那块表要等 5 秒轮询才对上 ——
    # 点完按钮的这几秒里"已经收起来的消息"还挂在对面那张表上。
    c.check("动完一张表立刻重画对面那张（级联的效果不许等轮询）",
            "if (alsoReload) alsoReload();" in page
            and '", loadMessages)' in page and '", loadWorkOrders)' in page, "")
    c.check("放回的回执也报镜像条数（只说「已放回 1 条」= 把级联那一半藏起来）",
            "连同 ${n} 条双写的学生消息一起放回" in page and "mirrored_messages" in page, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_58_ledger_archive：工单/消息归档（软删 + 双写级联 + 只挂管理端）")
    print("=" * 60)
    code |= batch_a_soft_not_hard().summary("A 批（软删不是硬删）")
    code |= batch_b_cascade().summary("B 批（双写级联两支都认）")
    code |= batch_c_gates().summary("C 批（闸门与边界）")
    code |= batch_d_shape().summary("D 批（形状与前端）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
