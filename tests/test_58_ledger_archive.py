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
            "台账里那条没动，可放回" in page and "归档没成功：" in page, "")
    # ⚠️ 这一句在 #28 改过口：原来钉的是「归档时网络出问题了」。那条措辞被 #28 单子 2 判定为甩锅
    #     （收口之后这些 catch 还会接住 HTTP 404/500，再说"网络"就是把服务端的事推给网线）。
    #     记在这儿，是为了下次它红的时候不必从头查一遍是回归还是有人改回了措辞。
    # 真机第一版露馅的地方：工单归档级联改了收件箱，但那块表要等 5 秒轮询才对上 ——
    # 点完按钮的这几秒里"已经收起来的消息"还挂在对面那张表上。
    c.check("动完一张表立刻重画对面那张（级联的效果不许等轮询）",
            "if (alsoReload) alsoReload();" in page
            and '", loadMessages)' in page and '", loadWorkOrders)' in page, "")
    c.check("放回的回执也报镜像条数（只说「已放回 1 条」= 把级联那一半藏起来）",
            "连同 ${n} 条双写的学生消息一起放回" in page and "mirrored_messages" in page, "")
    return c


def _fn_body(text, name, tag):
    """按花括号配对截出一个函数体；截不出来直接抛，不返回空串装绿。"""
    key = "function " + name + "("
    if text.count(key) != 1:
        raise AssertionError(f"{tag}：函数锚点不唯一（{text.count(key)} 处）——{key}")
    i = text.index(key)
    j = text.index("{", i)
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError(f"{tag}：函数 {name} 的花括号没闭合，截不出函数体")


def _action_span(text, anchor, tag):
    """从某个动作的 fetch 那句起，**连着它自己的 catch 块**一起截出来。

    ⚠️ 必须把 catch 块包进窗口：这一族要看的就是"失败的那一支写了什么"。
    截到 `catch (` 就停（第一版就是这么写的），措辞断言永远看不到 catch 体，
    红的是断言自己，不是代码 —— 真机跑出来才发现这条假红。
    """
    n = text.count(anchor)
    if n != 1:
        raise AssertionError(f"{tag}：锚点不唯一（{n} 处）——{anchor[:48]}")
    i = text.index(anchor)
    j = text.find("catch (", i)
    if j < 0:
        raise AssertionError(f"{tag}：这一段没有 try/catch，锚点后头找不到 catch——{anchor[:48]}")
    k = text.index("{", j)
    depth = 0
    for p in range(k, len(text)):
        if text[p] == "{":
            depth += 1
        elif text[p] == "}":
            depth -= 1
            if depth == 0:
                return text[i:p + 1]
    raise AssertionError(f"{tag}：catch 块的花括号没闭合——{anchor[:48]}")


def batch_e_honest_failure():
    """#28：点了「归档」屏幕上却写着「已归档 1 条」——这一批钉的就是**谎报成功**这一族。

    事故的引信是判据选错了字段：非 2xx 时 FastAPI 回的是 {"detail":"Not Found"}，
    正文里**没有 error 字段**，于是 `if (r.error)` 走的是"没 error 就是成功"那一支。
    修法按项目自己的纪律走：判据只许一份 —— 管理端收口在 adminFetch 一处，
    驿站端那四处就地守门（本单不许给 station 抽公共 fetch 层，见 §3 单子 3 的警示）。
    """
    c = Checker()
    title("E. 非 2xx 不许当成功（#28 谎报成功收口）")
    adm_raw = open("app/static/admin.html", encoding="utf-8").read()
    stn_raw = open("app/static/station.html", encoding="utf-8").read()
    adm = _strip(adm_raw)
    stn = _strip(stn_raw)

    # ---- 引信本身：服务端那个形状（这是"实测的前提"，不是推测） ----
    with sandbox():
        cli = make_client()
        r = cli.post("/api/workorders/nope/this-route-does-not-exist", json={})
        body = r.json() or {}
        c.check("钉住前提：路由不存在时回的是「非 2xx + 正文里没有 error 字段」——"
                "所以只看 r.error 的调用方一定会把它当成功（改口的话写在这儿，下次红了一查就知道）",
                r.status_code == 404 and "error" not in body and "detail" in body,
                f"{r.status_code} {str(body)[:70]}")

    # ---- 单子 1：收口在 adminFetch 一处，且顺序不能反 ----
    # ⚠️ 一律用 find() 拿下标、把"找不到"写成一条 ❌，别用 index()：
    #     变异 M1 就是把那一整支删掉，index 会抛 ValueError 把整批打断——**崩不算红**（本仓教训 4f）。
    i401 = adm.find("resp.status === 401")
    iok = adm.find("if (!resp.ok)")
    itext = adm.find("resp.text()")
    c.check("非 2xx 在 adminFetch 这一处变成异常，而且排在 401/403 那支**之后**"
            "（顺序倒了就把「服务器没设口令 / 口令不对」那句专属话术盖掉了，人不知道该干什么）",
            i401 >= 0 and iok > i401 and 'throw new Error("HTTP " + resp.status' in adm,
            f"401@{i401} ok@{iok}")
    c.check("404 那句要把最可能的原因说出来（这次的真相就是服务还在跑旧代码），"
            "不能只甩一个 HTTP 404",
            "resp.status === 404" in adm and "多半是服务还在跑旧代码" in adm, "")

    # ---- 单子 1 的副作用：成功路径不许被多读一次正文 ----
    c.check("成功路径不许提前读正文：resp.text() 全页只许出现 1 次，且它在 if (!resp.ok) 里面"
            "（在函数开头顺手读一次，调用方的 resp.json() 就全报 body already read —— 那是把好路打坏）",
            itext >= 0 and adm.count("resp.text()") == 1 and 0 <= iok < itext,
            f"出现 {adm.count('resp.text()')} 次，位置 @{itext}")

    # ---- 单子 1 的边界：归档那条不许绕过收口自己去判 ----
    fab = _fn_body(adm, "bindArchButtons", "E 批")
    c.check("归档按钮的成功判据不许只有 if (r.error)：它必须走 adminFetch（判据只有收口那一份），"
            "也不许绕开收口自己 fetch",
            "adminFetch(" in fab and "fetch(" not in fab.replace("adminFetch(", "")
            and "if (r.error)" in fab, "")

    # ---- 补漏：非 2xx 变成异常之后，没有 catch 的动作点就是"点了没反应"换了个原因 ----
    # 施工单原话是"它们原有的 catch 会接住异常"，实测那 7 个动作函数**一个 catch 都没有**
    # （证据与口径见 ai-prompts/2026-10-17-ticket28-honest-failure.md）。
    for fn, word in (("publish", "发布没成功"), ("saveApi", "保存没成功"), ("testApi", "测试没成功"),
                     ("savePersona", "保存人设没成功"), ("saveKB", "存知识库没成功"),
                     ("deleteKB", "删除没成功"), ("clearUsage", "清空没成功")):
        body = _fn_body(adm, fn, "E 批")
        c.check(f"动作函数 {fn}() 就地接住异常并写明「{word}」"
                f"（收口后它抛的是异常不是 error 字段，不接就等于没回执）",
                "catch (" in body and word + "：" in body, f"体长 {len(body)}")
    wo_cb = _action_span(adm, '.mini[data-wo]', "E 批")
    c.check("工单【标为处理中/已解决】那个回调也接住了：它虽然写在 loadWorkOrders 的 try 里，"
            "但注册在 try 内、**执行在 try 外**（异步回调早跑出那个动态范围），不就地接就是没反应",
            "try {" in wo_cb and "catch (" in wo_cb and "状态没改成" in wo_cb, f"窗口 {len(wo_cb)} 字")
    lmsg = _action_span(adm, 'adminFetch("/api/admin/messages?include_archived=1")', "E 批")
    c.check("收件箱读不到时把灰计数摘掉、原因写在列表位置（挂着上一分钟的「共 N 条」也是谎报的一种）。"
            "⚠️ 位置有讲究：不许写在 msgArchStatus 上，那个节点是【归档/放回】回执待的地方，"
            "而本函数每 5 秒跑一次 —— 写上去等于每 5 秒把人刚看到的回执擦掉",
            "catch (" in lmsg and "paintMsgCount(null)" in lmsg and "收件箱读不到" in lmsg
            and "msgArchStatus" not in lmsg, "")

    # ---- 单子 3：驿站端那四处同形守门（就地补，没抽公共 fetch 层） ----
    for tag, anchor in (("改工单状态", 'encodeURIComponent(w.id || "") + "/status"'),
                        ("登记一条", 'encodeURIComponent(String(s.id || "")) + "/records"'),
                        ("改登记状态", 'encodeURIComponent(String(item.id || "")) + "/status"'),
                        ("生成服务", 'fetch("/api/station/services", {'),
                        # 施工单 §3 只点了 4 处，整族扫又找到一处同形状的：/api/chat 回 500 时
                        # 正文里只有 detail，`if (data.error)` 判不到 → 客服"回"了一句空话。
                        ("问客服一句话", 'body: JSON.stringify({ message: msg, module: "station" })')):
        span = _action_span(stn, anchor, "E 批·驿站端" + tag)
        c.check(f"驿站端「{tag}」这一处补了 HTTP 级守门，且 if (r.error) 退成了 else if（不再当兜底）",
                "!resp.ok" in span and "else if" in span and "httpFailReason(" in span,
                f"片段 {len(span)} 字")
    c.check("这一处守门只算原因、不改请求怎么发：全页只有 5 处 !resp.ok，且没有新增 fetch 包装函数"
            "（施工单 §3 明确不许顺手抽公共层）",
            stn.count("!resp.ok") == 5 and "function httpFailReason(" in stn
            and "async function stationFetch" not in stn, f"!resp.ok={stn.count('!resp.ok')}")

    # ---- 单子 2：措辞不许把服务端的事推给网线 ----
    c.check("两页的动作类回执里不再出现「网络出问题」这种说法"
            "（收口后这些 catch 还接得住 HTTP 404/500，再说网络就是甩锅）",
            "网络出问题" not in adm and "网络出问题" not in stn, "")
    c.check("聊天那一档的「网络或接口异常」留着：它接的是 /api/chat 的流式与断连，"
            "本来就分不清网络还是服务端，不算这一族的谎报（钉住边界，免得下次整族扫时被顺手改掉）",
            "网络或接口异常" in stn, "")
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
    code |= batch_e_honest_failure().summary("E 批（非 2xx 不许当成功）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
