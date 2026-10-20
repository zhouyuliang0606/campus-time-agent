"""#37：工单要有归属、学生要看得见**真**进度、快递/外卖那两栏要真点得开。

用户 2026-10-17 20:54 三句原话：
  「现在新账号是空的，我上报外卖没了」（附管理端截图：那条通篇没有学号）
  「这个东西做了吗，我感觉可以做一下，不然不知道进度」
  「现在快递外卖那两栏是打不开的」

三条根因，本文件按三条分三批：
  A 入口 —— 侧栏唯一的点击委托挂在 `#sbNav` 上，而「快递 / 外卖」那两项在 `#sbMore > #sbMoreBody`
             里（不在 `#sbNav` 里），顶部四张模块卡又被 CSS 永久隐藏 ⇒ **一条能走的路都没有**；
  B 归属 —— `add_workorder` / `add_message` 从来不记是谁（身份体系 #32 早就在了，
             代码里那句"没有学生身份体系"是过期注释）；
  C 进度 —— 学生端那块读 localStorage：聊天上报根本不写它、状态写死"已提交"、
             而且是浏览器级的（换个账号看到上一个人的工单）。

⚠️ 起点自种：`sandbox()` 的 SCRATCH 名单里**有 `workorders.json`**（沙箱里它是空的），
   所以"老数据没归属"这件事必须自己摆出来，不能指望用户本机那 8 条。
"""
import json
import os

from _harness import (Checker, data_path, make_client, sandbox, title,
                      write_accounts)

SID_A, SID_B = "admin1", "admin2"


def _rows():
    p = data_path("workorders.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def _by_id(rows, wo_id):
    return next((w for w in rows or [] if w.get("id") == wo_id), None)


def batch_a_entry_wired():
    """A 批 · 入口（形状）：委托必须在 `#sidebar` 上，而且全页只许一条。"""
    c = Checker()
    title("A. 侧栏入口：委托挂在 #sidebar 上，快递/外卖才点得开")
    stu = open("app/static/student.html", encoding="utf-8").read()

    n_side = stu.count('getElementById("sidebar").addEventListener("click"')
    n_nav = stu.count('getElementById("sbNav").addEventListener("click"')
    c.check("A1 唯一的导航点击委托挂在 #sidebar（整个侧栏）上，#sbNav 上一条都不许留",
            n_side >= 1 and n_nav == 0, f"sidebar {n_side} 处 / sbNav {n_nav} 处")
    c.check("A1-bis 委托只有一条（两条并存 = switchNav 跑两遍 = 每个面板发两遍请求）",
            n_side == 1, f"{n_side} 处")
    c.check("A2 选择器带 [data-nav]：「更多服务 ▾」那个 toggle 也是 .sb-item，不能被当成导航",
            '.closest(".sb-item[data-nav]")' in stu, "")
    c.check("A3 那两项确实在 #sbMoreBody 里（前提变了 A1 的修法就没意义，所以两头都钉）",
            stu.index('id="sbMoreBody"') < stu.index('data-nav="express"')
            < stu.index('data-nav="takeout"'), "")
    c.check("A4 顶部那四张模块卡仍是永久隐藏的（本单没顺手删那块死代码，也不许指望它兜底）",
            "#modules { display: none !important; }" in stu, "")
    c.check("A5「让管家跟进」绑的是工单号（点错行就把别的单子报了）",
            'data-wo="${esc(w.id)}"' in stu and "b.dataset.wo" in stu, "")
    return c


def batch_b_ownership():
    """B 批 · 归属：两条写入口都得记 owner，而且只能由系统在 store 取。"""
    c = Checker()
    title("B. 工单 / 学生消息按 cookie 记归属（两条写入口、一处判据、老数据不回填）")
    with sandbox():
        write_accounts([SID_A, SID_B])
        # 先摆一条"归属这件事还没上线时"的老工单：**没有 owner 这个键**（不是空串）
        with open(data_path("workorders.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "legacy1", "ts": "2026-10-17 20:49:09", "kind": "外卖丢失",
                        "desc": "", "source": "chat", "status": "待处理"}], f, ensure_ascii=False)

        from app import store
        from app.modules import express

        store.set_current_student(SID_A)
        w1 = store.add_workorder("疑似丢件", "store 那条")
        tool_said = express.submit_work_order("外卖丢失", "工具那条")   # 模型只有 kind/desc
        store.set_current_student("")
        w3 = store.add_workorder("包裹破损", "无身份那条")

        c.check("B1 归属在 store 层取：设了身份之后 add_workorder 落的 owner 就是那个学号",
                w1.get("owner") == SID_A, str(w1)[:80])
        rows = _rows()
        tool_row = _by_id(rows, None) or next((r for r in rows if r.get("desc") == "工具那条"), None)
        c.check("B2 AI 那条工具路径也记上了归属（**最容易漏**：它根本不经过 /api/workorders）",
                tool_row is not None and tool_row.get("owner") == SID_A, str(tool_row)[:80])
        c.check("B2-bis 工具的回话里带工单号（学生与模型都拿得到凭据，不是报了就没影）",
                "工单" in tool_said and (tool_row or {}).get("id", "___") in tool_said,
                tool_said[:70])
        c.check("B3 没身份时 owner 是空串 —— 不是 demo，也不是随便挑一个学号",
                w3.get("owner") == "", repr(w3.get("owner")))

        ca = make_client(headers={"Cookie": "ct_student=" + SID_B})
        rp = ca.post("/api/workorders", json={"kind": "取件码异常", "desc": "面板那条",
                                             "source": "panel"})
        panel_row = _by_id(_rows(), (rp.json().get("workorder") or {}).get("id"))
        c.check("B4 两条路径各报一条，owner 分别是两个学号（不是同一个人替全校立案）",
                panel_row is not None and panel_row.get("owner") == SID_B
                and (tool_row or {}).get("owner") == SID_A, str(panel_row)[:80])
        c.check("B4-bis 双写那条学生消息也带归属（工单与消息两本账都认得人）",
                any((m.get("owner") == SID_B and m.get("kind") == "report")
                    for m in store.list_messages(include_archived=True)), "")
        legacy = _by_id(_rows(), "legacy1")
        c.check("B5 老数据**不回填**（回填=编造证据）：跑完这一轮，那条老工单仍然没有 owner 这个键",
                "owner" not in (legacy or {}), str(legacy)[:80])
    return c


def batch_c_student_progress():
    """C 批 · 学生看得见真进度：只回自己的、状态是活的、localStorage 那本账删干净。"""
    c = Checker()
    title("C. /api/my/workorders：只回自己的，状态跟着管理员的改动走")
    with sandbox():
        write_accounts([SID_A, SID_B])
        with open(data_path("workorders.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "legacy9", "ts": "2026-10-17 20:53:25", "kind": "外卖丢失",
                        "desc": "", "source": "chat", "status": "待处理"}], f, ensure_ascii=False)
        cli_a = make_client(headers={"Cookie": "ct_student=" + SID_A})
        cli_b = make_client(headers={"Cookie": "ct_student=" + SID_B})
        anon = make_client()

        ra = cli_a.post("/api/workorders", json={"kind": "快递没收到", "desc": "A 的单子",
                                                  "source": "panel"})
        cli_b.post("/api/workorders", json={"kind": "疑似丢件", "desc": "B 的单子",
                                            "source": "panel"})
        da = [w.get("desc") for w in cli_a.get("/api/my/workorders").json().get("workorders") or []]
        db = [w.get("desc") for w in cli_b.get("/api/my/workorders").json().get("workorders") or []]
        c.check("C1 A 只看见 A 那条、B 只看见 B 那条（接口层面分人，不是前端藏）",
                da == ["A 的单子"] and db == ["B 的单子"], f"A={da} B={db}")
        c.check("C2 **无身份返回空列表，绝不回落到全量**（这条不挂口令，回落一次就是当众摊开）",
                anon.get("/api/my/workorders").json().get("workorders") == [], "")

        wid = (ra.json().get("workorder") or {}).get("id")
        st = cli_b.post("/api/workorders/" + str(wid) + "/status", json={"status": "处理中"})
        row = _by_id(cli_a.get("/api/my/workorders").json().get("workorders"), wid)
        c.check("C3 管理员改了状态，学生那边就是「处理中」（钉的是前端把状态写死那个旧病）",
                st.status_code == 200 and row is not None and row.get("status") == "处理中",
                f"{st.status_code} {str(row)[:60]}")

        cli_a.post("/api/workorders/" + str(wid) + "/status", json={"status": "已解决"})
        row2 = _by_id(cli_a.get("/api/my/workorders").json().get("workorders"), wid)
        c.check("C4 已解决 ⇒ 自动归档（#29）之后学生**仍然看得见**这一条（管理端收起 ≠ 学生看不见）",
                row2 is not None and row2.get("archived") and row2.get("status") == "已解决",
                str(row2)[:70])

        from app import store
        allrows = store.list_workorders(owner=None, include_archived=True)
        mine = store.list_workorders(owner=SID_A, include_archived=True)
        legacy = store.list_workorders(owner="", include_archived=True)
        c.check("C5 owner=None（全量）与 owner=空串（只要没归属的）两种语义分开",
                len(mine) == 1 and len(allrows) == 3 and len(legacy) == 1
                and all((w.get("owner") or "") == "" for w in legacy),
                f"全量 {len(allrows)} / A 的 {len(mine)} / 没归属 {len(legacy)}")

    stu = open("app/static/student.html", encoding="utf-8").read()
    c.check("C6 前端不再有 localStorage 那本账（campustime_workorders 一个引用都不许剩）",
            "campustime_workorders" not in stu and "myWorkOrders" not in stu,
            f"残留 {stu.count('campustime_workorders')} 处")
    c.check("C6-bis 那块只读一个接口（一处来源；再加分支就是第二本账）",
            stu.count('"/api/my/workorders"') == 1, f"代码里 {stu.count(chr(34) + '/api/my/workorders' + chr(34))} 处")
    c.check("C7 聊天里点【确认上报】之后立刻刷一次那块列表（不刷就是「刚报的单子不存在」）",
            "void renderWorkOrders().catch" in stu[stu.index("work_order_report"):], "")
    c.check("C8 提交先上报服务端、成功了才刷新；失败把原因说出来（红线 24 同族：请求回来了 ≠ 办成了）",
            'id="woStatus"' in stu and "没提交成功" in stu, "")
    c.check("C9 加载失败不许报成「暂无工单」（用户那句「我上报外卖没了」就是这么来的）",
            "刷新或重新登录再试" in stu and "工单列表没能加载" in stu, "")
    # C10 钉的是"状态这一列来自接口字段"，不是前端自己写死 —— 旧实现写着 `已提交` 三个字，
    # 管理员改成"处理中"它也纹丝不动（用户那句"不然不知道进度"）。
    row_seg = stu[stu.index("const row = (w) =>"):]
    c.check("C10 那一行的状态取的是 w.status（不许写死「已提交」），样式也按 w.status 映射",
            "${esc(w.status" in row_seg[:600] and "WO_CLS[w.status]" in stu
            and "已提交</span>" not in stu, "")
    return c


_OWNER_TAKE = "current_student() if owner is None else"


def _owner_takers(src: str) -> list:
    """`store.py` 里「owner 由 store 自己兜底取值」分别落在**哪几个函数**里。

    判据从"数它出现几次"换成"点名是哪几个函数"：#43 那条老账（计数式护栏随写入口增减必然漂）
    在 #45-B 又应了一次验 —— 给 `save_upload` 加归属之后 2→3，D2 当场红，而那笔是对的。
    点名版本红了会直接把集合打出来，一眼分得清是"多了一个写入口"还是"取值被挪到调用方去了"。
    """
    names, cur = [], ""
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("def ") or s.startswith("async def "):
            cur = s.split("(")[0].replace("async def", "").replace("def", "").strip()
        if _OWNER_TAKE in ln and cur and cur not in names:
            names.append(cur)
    return sorted(names)


def batch_d_shape():
    """D 批 · 形状：口令边界不动、判据不抄第二遍、两张表都显示归属、那句承诺还在。"""
    c = Checker()
    title("D. 形状：/api/workorders 的口令不动，归属与过滤各只有一处")
    mn = open("app/main.py", encoding="utf-8").read()
    st = open("app/store.py", encoding="utf-8").read()
    adm = open("app/static/admin.html", encoding="utf-8").read()
    sta = open("app/static/station.html", encoding="utf-8").read()
    ex = open("app/modules/express.py", encoding="utf-8").read()
    cfm = open("app/static/confirm.html", encoding="utf-8").read()

    c.check("D1 全量台账那条接口照旧挂 verify_admin（不许为了学生端把口令去掉）",
            '@app.get("/api/workorders", dependencies=[Depends(verify_admin)])' in mn, "")
    seg = mn[mn.index("async def my_workorders"):]
    c.check("D1-bis 学生那条不收学号参数、不挂口令：身份只来自 current_student()（cookie）",
            "include_archived: bool = True" in seg[:200] and "who = current_student()" in seg
            and "verify_admin" not in seg[:400] and 'body.get("student")' not in seg[:1200], "")
    takers = _owner_takers(st)
    c.check("D2 owner 的兜底取值只在 store 的**写入口**里（点名，不数个数）：工单 / 学生消息 / 上传台账（#45-B）",
            takers == ["add_message", "add_workorder", "save_upload"], f"实际落在：{takers}")
    w_calls = [ln.strip() for ln in mn.splitlines()
               if any(k in ln for k in ("save_upload(", "add_workorder(", "add_message(")) and "owner=" in ln]
    c.check("D2-ter 调用方**只许**把 store 自己算出来的那个 `owner=owner` 递过去（公共文档那档 = 空串）；"
            "递学号、递 body 里的字段就是「前端说什么算什么」",
            not [x for x in w_calls if "owner=owner" not in x], f"这些调用点递了别的：{w_calls}")
    c.check("D2-bis 两条写入口都不许自己塞 owner（塞了就是「前端说什么算什么」）",
            "owner" not in mn[mn.index("add_workorder(kind=kind"):mn.index("add_workorder(kind=kind") + 200]
            and "owner" not in ex[ex.index("add_workorder(kind=kind"):ex.index("add_workorder(kind=kind") + 200],
            "")
    c.check("D3 过滤判据只写一处（list_workorders 里那一句），路由里不许再筛一遍",
            st.count("if owner is not None:") == 1 and "owner=who" in mn
            and 'w.get("owner")' not in mn, "")
    _own = 'w.owner || "未登记"'
    c.check("D4 管理端与驿站端都显示归属，缺失时写「未登记」（不是空白、也不是编一个学号）；"
            "#39-C 之后驿站那块是两处（未结表 + 已处理历史），历史里更得认得出是谁的案子",
            adm.count(_own) == 1 and sta.count(_own) == 2,
            "admin {} / station {}".format(adm.count(_own), sta.count(_own)))
    c.check("D5 那句「进度可在『我提交过的工单』里看到」还在（兑现由 C 批钉，措辞不许悄悄改成别的话）",
            "我提交过的工单" in ex and "我提交过的工单" in cfm, "")
    c.check("D6 那句过期注释不许还当成现状写着（「已知缺口、本单不解决」必须消失，"
            "旧说法只能以「以前写着」的形式留在纠正里）",
            "已知缺口、本单不解决" not in mn and "那句话过期了" in mn, "")
    return c


def main():
    print("\n" + "=" * 60)
    print("test_63_my_workorders：工单有归属 + 学生看得见真进度 + 入口真接得上（#37）")
    print("=" * 60)
    code = 0
    code |= batch_a_entry_wired().summary("A 批（侧栏入口的形状）")
    code |= batch_b_ownership().summary("B 批（两条写入口都记归属，老数据不回填）")
    code |= batch_c_student_progress().summary("C 批（/api/my/workorders + 删掉假账）")
    code |= batch_d_shape().summary("D 批（形状：口令边界不动、判据一处）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
