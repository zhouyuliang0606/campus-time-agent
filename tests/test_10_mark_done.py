"""「标记完成」改成系统接管 + 问答栏「前往我的日程」按钮 + 删待办回归。

这一批钉住 Request R 的三件事：
  A) 标记完成系统接管（加 3 确定性分支 + 确认条，撤掉模型直调）
       · 点名唯一一条 → 出确认条（原样念一遍），学生没点头前一个字节都不改；
       · 没说清是哪条 → 列清单问是哪一条，列完就停，绝不猜；
       · 一条都没命中 → 如实说，绝不编一条出来标记；
       · 模型手里只剩只读的 propose_todo_done，真改状态只发生在学生点【确认完成】之后。
  B) 问答栏说"删 / 标记完成待办" → 与日程栏**同样**出确认条（2026-10-09：
       原先的"只给一颗前往我的日程按钮"已随全站一条对话流拆掉，不再拦学生。
  C) 删待办在「我的日程」栏的端到端回归（系统接管那套规矩不能被改坏）。
"""
import datetime as _dt
import json as _json

from _harness import Checker, make_client, sandbox, title


def _seed_todos(today, d_near, d_far):
    """往沙箱里写几条待办，当作学生之前的安排。

    先清空沙箱里所有待办（含演示数据继承自 REAL_DATA_DIR 的），
    避免演示周里恰好也有「游泳/交电费」落在同一天、把"唯一命中"误判成"命中多条"。
    """
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    add_todo("游泳", d_near, "14:00", "15:00")
    add_todo("交电费", d_near, "19:00", "19:30")
    add_todo("晨读", d_far, "07:00", "07:30")


def batch_a_mark_done_unit():
    """A 单元层：开关与解析三分支。"""
    title("A. 标记完成：判定开关 + 解析三分支（系统判定，不丢给模型）")
    c = Checker()
    from app.modules.planner import resolve_todo_done, wants_done_todo

    today = _dt.date.today().isoformat()
    d_near = (today and _dt.date.today() + _dt.timedelta(days=4)).isoformat()
    d_far = (_dt.date.today() + _dt.timedelta(days=5)).isoformat()

    with sandbox():
        client = make_client()
        _seed_todos(today, d_near, d_far)

        # —— ① 判定开关：认得各种"标记完成"的口语 ——
        c.check("「把游泳的待办标记完成」算标记完成",
                wants_done_todo("把游泳的待办标记完成"))
        c.check("「勾掉交电费」算标记完成", wants_done_todo("勾掉交电费"))
        c.check("「把晨读标记完成」算标记完成",
                wants_done_todo("把晨读标记完成"))
        c.check("「游泳做完了」精准对上一条待办 → 算标记完成",
                wants_done_todo("游泳做完了"))
        # 让路：删待办 / 加待办优先
        c.check("「把游泳删了」**不算**标记完成（那是删）",
                not wants_done_todo("把游泳删了"))
        c.check("「帮我安排完成作业」**不算**标记完成（那是加）",
                not wants_done_todo("帮我安排完成作业"))

        # —— ② 解析层：唯一 = ok；多条 = many（绝不替学生挑）；没有 = empty ——
        r = resolve_todo_done("把交电费标记完成")
        c.check("点名唯一一条 → 解析成一张标记完成提案",
                r["status"] == "ok" and r["proposal"]["title"] == "交电费",
                _json.dumps(r.get("proposal"), ensure_ascii=False)[:100])
        c.check("提案里带上了那条的编号（系统靠它落库）",
                bool(r["proposal"].get("todo_id")))
        c.check("提案里的 summary 是『标题｜日期（周几）时段』的模样",
                "交电费" in r["proposal"]["summary"]
                and "19:00-19:30" in r["proposal"]["summary"],
                r["proposal"]["summary"])
        c.check("那天有两条、又没点名 → many（要去列清单）",
                resolve_todo_done(f"标记完成 {d_near} 的待办")["status"] == "many",
                [x["title"] for x in
                 resolve_todo_done(f"标记完成 {d_near} 的待办")["todos"]])
        c.check("没找到就是 empty，绝不硬凑一条",
                resolve_todo_done("标记完成读研申请")["status"] == "empty")
    return c


def batch_b_toolbox():
    """A 工具箱：标记完成只剩只读提案工具，模型没有改状态的接口。"""
    title("B. 工具箱：propose_todo_done 只读，update_todo_status 已撤出")
    c = Checker()
    from app.modules import planner as pl
    tools = pl.build_tools()
    c.check("工具箱里有 propose_todo_done（只出确认条）",
            "propose_todo_done" in tools, list(tools))
    c.check("真能改状态的 update_todo_status 已经撤掉（模型碰不到写接口）",
            "update_todo_status" not in tools)
    desc = tools["propose_todo_done"].description
    c.check("说明书里写清『只出确认条、一个字节都不改』",
            "确认条" in desc and ("一个字节都不改" in desc or "写状态" in desc))
    c.check("说明书里写清命中多条要列清单、不许猜",
            "列" in desc and "不许猜" in desc)
    return c


def batch_c_mark_done_e2e():
    """A 端到端：出卡 → 学生没点头不改；回「确认」→ 系统真置 done；别的待办不动。"""
    title("C. 标记完成端到端：确认条原样念一遍 / 没点头不动 / 确认才置 done")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.store import get_todo, list_todos

    today = _dt.date.today().isoformat()
    d_near = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()

    with sandbox():
        client = make_client()
        _seed_todos(today, d_near, d_near)  # 游泳, 交电费 @ d_near ; 晨读 @ d_far

        # —— ① 出确认条：原样念一遍、不写库（用「交电费」，唯一一条） ——
        sid = "done-1"
        r1 = client.post("/api/chat", json={
            "message": "把交电费的待办标记完成", "module": "planner", "session_id": sid,
        })
        d1 = r1.json()
        opts = d1.get("options") or []
        c.check("回了一张标记完成确认条",
                any(o.get("kind") == "todo_done" for o in opts),
                _json.dumps(opts[:1], ensure_ascii=False)[:110])
        c.check("确认条上把那条**原样念了一遍**（标题 + 日期 + 时段）",
                bool(opts) and opts[0].get("title") == "交电费"
                and opts[0].get("date") == d_near
                and opts[0].get("start") == "19:00" and opts[0].get("end") == "19:30",
                (opts[0].get("summary") if opts else "无"))
        c.check("话里说的是「点确认完成我才改」，不是「已经完成了」",
                "确认完成" in (d1.get("answer") or "")
                and "已经标记完成" not in (d1.get("answer") or ""),
                (d1.get("answer") or "")[:60])
        # 关键红线：学生还没点头，状态必须还是 pending（不是 done）
        jf = next((t for t in list_todos(d_near) if t["title"] == "交电费"), None)
        c.check("**学生还没点头**：这条待办状态还是 pending，没被偷偷置 done",
                jf is not None and jf.get("status") != "done",
                (jf or {}).get("status"))
        c.check("标记完成提案进了暂存（学生回「确认」时系统找得到它）",
                any(o.get("kind") == "todo_done"
                    for o in (peek_pending(sid) or {}).get("options") or []))

        # —— ② 学生回一句「确认」→ 系统真置 done（只有这一条路能改状态） ——
        r2 = client.post("/api/chat", json={"message": "确认", "session_id": sid})
        d2 = r2.json()
        jf2 = get_todo(jf["id"]) if jf else None
        c.check("回「确认」之后那条状态真的变成 done",
                jf2 is not None and jf2.get("status") == "done",
                (jf2 or {}).get("status"))
        c.check("回答说的是「已标记为完成」而不是「搞定」",
                "已标记为完成" in (d2.get("answer") or ""),
                (d2.get("answer") or "")[:50])
        c.check("同一天的另一条（游泳）没被误改",
                any(t["title"] == "游泳" and t.get("status") != "done"
                    for t in list_todos(d_near)),
                [t["title"] + "/" + t.get("status", "") for t in list_todos(d_near)])
        c.check("标记回执进了会话历史（刷新后还看得见）",
                any("已标记完成" in (m.get("content") or "")
                    for m in __import__("app.store", fromlist=["get_conversation"])
                    .get_conversation(sid)))

        # —— ③ 命中好几条：列清单，问是哪一条，列完就停，一个都不改 ——
        sid2 = "done-2"
        r3 = client.post("/api/chat", json={
            "message": f"标记完成 {d_near} 的待办", "module": "planner", "session_id": sid2,
        })
        d3 = r3.json()
        ans3 = d3.get("answer") or ""
        c.check("命中好几条时**不出确认条**（出条就等于替学生挑了）",
                not (d3.get("options") or []),
                _json.dumps(d3.get("options"), ensure_ascii=False)[:80])
        c.check("把那天的待办**列了出来**（红线：清单）",
                "交电费" in ans3 and "19:00-19:30" in ans3,
                ans3.replace("\n", " / ")[:120])
        c.check("列完就问「是哪一条」", "哪一条" in ans3,
                ans3.replace("\n", " / ")[:60])
        # 列清单这个动作本身只读：交电费保持 done（②里置的）、游泳保持 pending
        c.check("列了清单也没改任何东西（交电费仍 done、游泳仍 pending）",
                any(t["title"] == "交电费" and t.get("status") == "done"
                    for t in list_todos(d_near))
                and any(t["title"] == "游泳" and t.get("status") != "done"
                        for t in list_todos(d_near)),
                [t["title"] + "/" + t.get("status", "") for t in list_todos(d_near)])
        # 学生用序号点名 → 系统接得住，出确认条（清单第一条=晨读）
        r4 = client.post("/api/chat", json={"message": "第一条", "session_id": sid2})
        o4 = r4.json().get("options") or []
        c.check("学生回序号点名，系统接得住并出确认条",
                any(o.get("kind") == "todo_done" for o in o4),
                _json.dumps(o4[:1], ensure_ascii=False)[:110])
        c.check("点中的就是清单里第一条（晨读）",
                bool(o4) and o4[0].get("title") == "晨读",
                (o4[0].get("title") if o4 else "无"))

        # 学生再回「确认」→ 点名的那条（晨读）置 done，另一条（游泳）不动
        r5 = client.post("/api/chat", json={"message": "确认", "session_id": sid2})
        cdr = next((t for t in list_todos(d_near) if t["title"] == "晨读"), None)
        swim = next((t for t in list_todos(d_near) if t["title"] == "游泳"), None)
        c.check("点名确认后晨读变成 done",
                cdr is not None and cdr.get("status") == "done",
                (cdr or {}).get("status"))
        c.check("没点名的另一条（游泳）仍 pending，没被误改",
                swim is not None and swim.get("status") != "done",
                (swim or {}).get("status"))

        # —— ④ 一条都没命中：如实说，不编、不改 ——
        sid3 = "done-3"
        r6 = client.post("/api/chat", json={
            "message": "标记完成读研申请", "module": "planner", "session_id": sid3,
        })
        d6 = r6.json()
        c.check("没找到就如实说（不谎称已标记）",
                "没找到" in (d6.get("answer") or "")
                and "已标记" not in (d6.get("answer") or ""),
                (d6.get("answer") or "")[:60])
        c.check("没找到时一条都没改（游泳仍 pending、交电费仍 done）",
                any(t["title"] == "游泳" and t.get("status") != "done"
                    for t in list_todos(d_near))
                and any(t["title"] == "交电费" and t.get("status") == "done"
                        for t in list_todos(d_near)),
                [t["title"] + "/" + t.get("status", "") for t in list_todos(d_near)])
    return c


def batch_d_qa_goto_schedule():
    """D 问答栏：说删 / 标记完成待办 → **和日程栏一模一样地出确认条**（不再拦、不再跳转）。

    2026-10-09 改口径。这里原来钉的是"问答栏不办待办，只给一颗『前往我的日程』按钮"，
    理由是"确认条只在日程栏目才有"。那个前提随着全站合成一条对话流已经不成立了：
    卡片显示在同一条流里、走的也是同一套确认条，而**学生说话时心里没有"栏目"这个概念**。
    拦截拆掉之后这批改钉"不分栏目一致"——③ 那条 kind 相等才是这条改动的正身。
    """
    title("D. 问答栏待办操作 → 与日程栏同样出确认条（不拦截、不静默失败、不真写）")
    c = Checker()
    from app.store import list_todos

    today = _dt.date.today().isoformat()
    d_near = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()

    with sandbox():
        client = make_client()
        _seed_todos(today, d_near, d_near)

        def kinds(msg, module, sid):
            d = client.post("/api/chat", json={"message": msg, "module": module,
                                               "session_id": sid}).json()
            return [o.get("kind") for o in (d.get("options") or [])], d

        # ① 问答栏说"删待办" → 出删除确认条，且**真的没删**
        k1, d1 = kinds("把游泳的待办删掉", "faq", "qa-del")
        c.check("问答栏说删待办 → 回删除确认条（不再是跳转按钮）",
                "todo_remove" in k1 or "todo_remove_batch" in k1, str(k1)[:120])
        c.check("问答栏不再出现「前往我的日程」按钮",
                "goto_schedule" not in k1, str(k1)[:120])
        c.check("问答栏说删待办 → **没有静默失败，也没有真删**（待办还在）",
                any(t["title"] == "游泳" for t in list_todos(d_near)),
                [t["title"] for t in list_todos(d_near)])

        # ② 问答栏说"标记完成" → 同样出确认条，且**没改状态**
        k2, d2 = kinds("把游泳标记完成", "faq", "qa-done")
        c.check("问答栏说标记完成 → 回确认条而不是跳转",
                "todo_done" in k2 or "todo_done_batch" in k2, str(k2)[:120])
        swim = next((t for t in list_todos(d_near) if t["title"] == "游泳"), None)
        c.check("问答栏说标记完成 → 状态没被动（还是 pending）",
                swim is not None and swim.get("status") != "done")

        # ③ 同一句话在两个栏目必须走同一条路 —— 这才叫"语境打通了"
        k_pl, _ = kinds("把游泳的待办删掉", "planner", "qa-del-planner")
        c.check("「把游泳的待办删掉」问答栏与日程栏回同一种卡",
                k1 == k_pl, f"faq={k1} planner={k_pl}")

        # ④ 普通问答不该被误伤：正常问句还是走 faq 答，不该弹待办操作卡
        k3, d3 = kinds("图书馆几点关门", "faq", "qa-ok")
        c.check("普通问答（图书馆几点关门）不弹待办操作卡",
                not any(k in ("todo_remove", "todo_done", "goto_schedule") for k in k3),
                str(k3)[:80] + " | " + str(d3.get("answer"))[:60])
    return c


def batch_e_delete_regression():
    """C 删待办在「我的日程」栏的端到端回归（系统接管规矩不能被改坏）。"""
    title("E. 删待办端到端回归（「我的日程」栏：出条 / 没点头不删 / 确认才删）")
    c = Checker()
    from app.agent.pending import peek_pending
    from app.store import list_todos

    today = _dt.date.today().isoformat()
    d_near = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()

    with sandbox():
        client = make_client()
        _seed_todos(today, d_near, d_near)

        # ① 出删待办确认条、原样念一遍、不写库
        sid = "del-reg"
        r1 = client.post("/api/chat", json={
            "message": "把游泳的待办删掉", "module": "planner", "session_id": sid,
        })
        d1 = r1.json()
        opts = d1.get("options") or []
        c.check("回了一张删待办确认条",
                any(o.get("kind") == "todo_remove" for o in opts),
                _json.dumps(opts[:1], ensure_ascii=False)[:110])
        c.check("确认条把那条原样念了一遍（标题 + 日期 + 时段）",
                bool(opts) and opts[0].get("title") == "游泳"
                and opts[0].get("date") == d_near
                and opts[0].get("start") == "14:00" and opts[0].get("end") == "15:00")
        c.check("**学生还没点头**：待办一条都没少",
                any(t["title"] == "游泳" for t in list_todos(d_near)),
                [t["title"] for t in list_todos(d_near)])
        c.check("删待办提案进了暂存",
                any(o.get("kind") == "todo_remove"
                    for o in (peek_pending(sid) or {}).get("options") or []))

        # ② 回「确认」→ 真删，同天另一条不动
        r2 = client.post("/api/chat", json={"message": "确认", "session_id": sid})
        c.check("回「确认」之后那条真没了",
                not any(t["title"] == "游泳" for t in list_todos(d_near)),
                [t["title"] for t in list_todos(d_near)])
        c.check("同一天的另一条没被误删",
                any(t["title"] == "交电费" for t in list_todos(d_near)))
        c.check("回答说的是「已删除」",
                "已删除" in (r2.json().get("answer") or ""),
                (r2.json().get("answer") or "")[:50])
    return c


def batch_f_completion_phrases():
    """F 批 · 「X完了」这一族**完成声明**要认出来（用户 2026-10-12：
    我说「今天健身完了」，它理解成问事了，只口头回复一句）。

    整族扫出来的缺口比那一句大：软动词表收了"做完/完成了/办完/搞定"，
    偏偏漏了最口语的光杆「完了」，于是「今天健身完了」「我健身完了」全掉给模型。
    收进来之后又冒出一个反向问题：「健身累完了」是在**说累**、不是宣布做完 ——
    所以「完了」这一颗单独要求紧贴事名。两个方向都在这儿钉住。
    """
    ck = Checker()
    title("F 批 · 完成声明：「今天健身完了」不是问句，也不该被当成吐槽")
    with sandbox():
        from app.store import add_todo
        from app.modules.planner import wants_done_todo
        today = _dt.date.today().isoformat()
        for t in ("健身", "跑步", "写作业"):
            add_todo(t, today, "19:00", "20:00")

        YES = ["今天健身完了", "健身完了", "我健身完了", "今天健身完了。",
               "健身做完了", "写作业做完了", "跑步完成了", "把健身标记完成", "勾掉跑步"]
        for s in YES:
            ck.check("认成标记完成｜%s" % s, wants_done_todo(s) is True, wants_done_todo(s))

        NO = ["健身完了吗", "我作业写完了没", "今天健身怎么样",     # 问句：是在问，不是宣布
              "健身累完了", "忙完了", "说完了",                   # 状态/吐槽：没有紧贴事名的「完了」
              "帮我安排完成作业", "游泳做完了"]                    # 撞新增意图 / 库里没这条待办
        for s in NO:
            ck.check("不许误判｜%s" % s, wants_done_todo(s) is False, wants_done_todo(s))

        # 已知缺口照实钉住（不是"应该为真"，是"现在为假、原因写清楚"）：
        # 「跑完了」对不上《跑步》—— `_todo_name_match` 是整名包含，不做词干/语序还原。
        # 放宽它会同一条链影响删待办的点名，本轮没动，先钉住现状别让它悄悄变。
        ck.check("已知缺口｜「跑完了」仍对不上《跑步》（词干匹配没做，动它会牵连删除）",
                 wants_done_todo("跑完了") is False, wants_done_todo("跑完了"))
    return ck




def main():
    results = [
        batch_a_mark_done_unit(),
        batch_b_toolbox(),
        batch_c_mark_done_e2e(),
        batch_d_qa_goto_schedule(),
        batch_e_delete_regression(),
        batch_f_completion_phrases(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 标记完成/问答栏/删待办回归 总账：通过 {total_pass} 项，"
          f"失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
