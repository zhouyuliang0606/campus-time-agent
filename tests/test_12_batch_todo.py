"""批量待办：学生对着清单说「都删了 / 全部删掉 / 一起删了吧」= 一次全办。

这一批钉住一个真实报障（学生截图 + 原话「去思考学生表达的意思，不是字面上的，
要连接上下文猜测」）：
  系统刚列了 2 条候选，学生回「都删了」，屏幕却冒出**整张日程**（11 条）。

根因：「都删了」里的"都"是**指代**——接着上一句清单说"你列的那几条我全都要删"。
  可旧写法只按名字/序号解析，`_pick_todo_name("都删了") == ""` → 当成"没点名"
  → `resolve_todo_remove` 拿**全部**待办当候选 → 端出整张日程。

对应改动：
  planner._ALL_WORDS / _wants_all_candidates / todo_remove_batch_proposal /
  todo_done_batch_proposal / resolve_todo_remove_reply / resolve_todo_done_reply；
  main 的 "all" 分支 + 批量确认执行块（回一句"确认"就全删/全标记）；
  confirm.html 的 todo_remove_batch / todo_done_batch 两张卡；student.html 挂卡分支。

红线没破：批量卡只是**提案**，学生点【确认…全部】或回一句"确认"之前，一条都不动。
"""
from _harness import Checker, make_client, sandbox, title


def _seed_colliding():
    """沙箱里铺一组：一对撞时间的（甲/乙 同天同时段）+ 一条不撞的（丙）。"""
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    d = "2026-10-03"
    add_todo("甲", d, "14:00", "15:00")
    add_todo("乙", d, "14:00", "15:00")   # 跟甲同一时段 → 撞
    add_todo("丙", d, "20:00", "20:30")   # 不撞
    return d


def batch_a_unit():
    """A 单元：「都」类说法的识别（该认的认，别的不许误认）。"""
    title("A. 「都/全部/一起/剩下的」= 全选（认指代，别乱认）")
    c = Checker()
    from app.modules.planner import _wants_all_candidates

    for s in ("都删了", "都删掉", "全部删掉", "全都删了", "一起删了吧",
              "剩下的都不要了", "都不要了", "统统删掉", "这两个都不要"):
        c.check(f"「{s}」→ 认作全选", _wants_all_candidates(s), f"返回 {_wants_all_candidates(s)}")
    for s in ("第二条", "23", "游泳那条", "删掉游泳", "不要", ""):
        c.check(f"「{s}」→ 不算全选", not _wants_all_candidates(s),
                f"返回 {_wants_all_candidates(s)}")
    c.check("超长句子不当全选（防误伤）",
            not _wants_all_candidates("都" + "啊" * 40))
    return c


def batch_b_reply_unit():
    """B 单元：reply 解析器——有清单时说「都」回 all；点名字仍然听名字。"""
    title("B. 回「都」→ all（带清单）；点名字/序号不受影响")
    c = Checker()
    from app.modules.planner import resolve_todo_remove_reply, resolve_todo_done_reply

    cands = [{"id": "a", "title": "游泳", "date": "2026-10-03", "start": "14:00", "end": "15:00"},
             {"id": "b", "title": "交电费", "date": "2026-10-03", "start": "19:00", "end": "19:30"}]

    r = resolve_todo_remove_reply("都删了", cands)
    c.check("「都删了」→ status=all 且带上那 2 条",
            r["status"] == "all" and [x["id"] for x in r["todos"]] == ["a", "b"],
            f'{r.get("status")} / {len(r.get("todos") or [])}')
    r2 = resolve_todo_done_reply("都完成了", cands)
    c.check("标记完成同样接得住「都」",
            r2["status"] == "all" and len(r2["todos"]) == 2, r2.get("status"))

    # 序号/名字的老路不能被"都"抢走
    r3 = resolve_todo_remove_reply("第一条", cands)
    c.check("回序号仍走序号（不受「都」影响）",
            r3["status"] == "ok" and r3["proposal"]["title"] == "游泳",
            r3.get("proposal", {}).get("title"))
    r4 = resolve_todo_remove_reply("都删了", None)
    c.check("手里没清单时说「都」→ 不硬造 all（没有「都」的所指）",
            r4.get("status") != "all", r4.get("status"))
    return c


def batch_c_propose_e2e():
    """C 单元：端到端——列清单 → 回「都删了」→ 出**一张批量卡**，且一条都还没删。"""
    title("C. 列清单 → 回「都删了」→ 批量确认卡（此时一条都没删）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_colliding()
        from app.store import list_todos

        sid = "bt-1"
        d1 = client.post("/api/chat", json={
            "message": "删除撞时间的代办", "module": "planner", "session_id": sid}).json()
        ans1 = d1.get("answer") or ""
        c.check("第一步列出撞时间的那对（甲/乙），不列丙",
                "甲" in ans1 and "乙" in ans1 and "丙" not in ans1,
                ans1.replace("\n", " / ")[:90])
        c.check("列清单阶段不出确认条", not (d1.get("options") or []))

        d2 = client.post("/api/chat", json={
            "message": "都删了", "module": "planner", "session_id": sid}).json()
        opts = d2.get("options") or []
        kinds = [o.get("kind") for o in opts]
        ans2 = d2.get("answer") or ""
        c.check("回「都删了」→ 正好一张批量卡",
                kinds == ["todo_remove_batch"], str(kinds))
        c.check("批量卡上就是刚才那 2 条（不是整张日程）",
                bool(opts) and opts[0].get("count") == 2
                and {i.get("title") for i in opts[0].get("items", [])} == {"甲", "乙"},
                str(opts[0].get("summary")) if opts else "无卡")
        c.check("卡上把每一条都原样念出来（先列清楚再动手）",
                bool(opts) and len(opts[0].get("items") or []) == 2
                and all(i.get("date") and i.get("start") for i in opts[0]["items"]))
        c.check("**没有**再端出整张日程（坏代码会列 3 条/冒出丙）",
                "丙" not in ans2 and "小组会议" not in ans2, ans2.replace("\n", " ")[:90])
        c.check("还没确认 → 库里一条没少（红线：没点头不动手）",
                len(list_todos()) == 3, f"现在 {len(list_todos())} 条")
    return c


def batch_d_confirm_e2e():
    """D 单元：端到端——回一句「确认」→ 批量卡上的那 2 条真被删，不撞的那条留着。"""
    title("D. 回「确认」→ 批量执行（真删；不撞的那条原封不动）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_colliding()
        from app.store import list_todos

        sid = "bt-2"
        client.post("/api/chat", json={"message": "删除撞时间的代办",
                                       "module": "planner", "session_id": sid})
        client.post("/api/chat", json={"message": "都删了", "module": "planner",
                                       "session_id": sid})
        before = len(list_todos())
        r = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        ans = r.get("answer") or ""
        left = {t.get("title") for t in list_todos()}
        c.check("确认前一条没少", before == 3, f"{before}")
        c.check("回「确认」后那 2 条真删了", left == {"丙"}, str(left))
        c.check("回执如实报数（说了删几条）", "2" in ans, ans.replace("\n", " ")[:80])
        c.check("回执里点名了删的是哪两条", "甲" in ans and "乙" in ans,
                ans.replace("\n", " ")[:80])
    return c


def batch_e_done_e2e():
    """E 单元：标记完成的批量版（同样的「都」）。"""
    title("E. 标记完成：回「都完成」→ 批量卡 → 确认后两条都置 done")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_colliding()
        from app.store import list_todos

        sid = "bt-3"
        client.post("/api/chat", json={"message": "把撞时间的待办标记完成",
                                       "module": "planner", "session_id": sid})
        d = client.post("/api/chat", json={"message": "都完成",
                                           "module": "planner", "session_id": sid}).json()
        kinds = [o.get("kind") for o in (d.get("options") or [])]
        c.check("回「都完成」→ 一张批量标记卡", kinds == ["todo_done_batch"], str(kinds))
        c.check("确认前状态还没变（都是 planned）",
                all(t.get("status") != "done" for t in list_todos()))
        r = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        done = {t.get("title") for t in list_todos() if t.get("status") == "done"}
        c.check("确认后撞时间那两条都置为完成", done == {"甲", "乙"}, str(done))
        c.check("不撞的「丙」没被误标记",
                [t.get("status") for t in list_todos() if t.get("title") == "丙"] == ["planned"])
        c.check("回执如实报数", "2" in (r.get("answer") or ""),
                (r.get("answer") or "").replace("\n", " ")[:80])
    return c


def batch_f_guard():
    """F 回归：手里没清单时「都删了」不许凭空批量；没点名也不许端出整张日程当卡。"""
    title("F. 守卫：没清单时说「都」不硬造批量卡")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_colliding()
        from app.store import list_todos

        d = client.post("/api/chat", json={"message": "都删了", "module": "planner",
                                           "session_id": "bt-4"}).json()
        kinds = [o.get("kind") for o in (d.get("options") or [])]
        c.check("一上来就说「都删了」→ 不出批量卡（没有「都」的所指）",
                "todo_remove_batch" not in kinds, str(kinds))
        c.check("也没真删任何东西", len(list_todos()) == 3, f"{len(list_todos())}")
    return c


def _seed_dupes():
    """沙箱里铺一组**重复**的：两条一模一样的「甲」+ 一条不重复的「丙」。

    这样"撞时间"筛出来的候选就是那两条甲 —— 正好用来验「每个删一个」（去重）。
    """
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    d = "2026-10-03"
    add_todo("甲", d, "14:00", "15:00")
    add_todo("甲", d, "14:00", "15:00")   # 跟上面那条一模一样 → 重复
    add_todo("丙", d, "20:00", "20:30")
    return d


def batch_g_dedupe():
    """G 单元：「每个删一个」是**去重**，不是全删（学生截图："脑子有问题"就是这一处）。"""
    title("G. 「每个删一个/去重」→ 每组留一条，别听成全删")
    c = Checker()
    from app.modules.planner import (_wants_one_each, _wants_all_candidates,
                                     _one_per_group, resolve_todo_remove_reply)

    # ① 认词：分配量词不该被当成"全都要"
    for s in ("每个删一个", "每组删一个", "各删一个", "每样去掉一个",
              "重复的删掉一个", "去重", "把重复的删一个", "只留一个就行"):
        c.check(f"「{s}」→ 认作去重", _wants_one_each(s), f"返回 {_wants_one_each(s)}")
    for s in ("都删了", "全部删掉", "第一条", "23", "游泳那条"):
        c.check(f"「{s}」→ 不算去重", not _wants_one_each(s), f"返回 {_wants_one_each(s)}")
    c.check("**关键回归**：「每个删一个」不再被当成全选（旧代码 all=True → 全删）",
            not _wants_all_candidates("每个删一个"),
            f"all={_wants_all_candidates('每个删一个')}")

    # ② 分组：每组留最早写入的那条，只交出多出来的
    six = []
    for i, (t_, s_, e_) in enumerate([("游泳", "14:00", "15:00"), ("游泳", "14:00", "15:00"),
                                      ("交电费", "19:00", "19:30"), ("交电费", "19:00", "19:30"),
                                      ("晨读", "07:00", "07:30"), ("晨读", "07:00", "07:30")]):
        six.append({"id": str(i), "title": t_, "date": "2026-10-03", "start": s_, "end": e_})
    dup = _one_per_group(six)
    c.check("三对重复 → 交出 3 条（每组一条）", len(dup) == 3, f"{len(dup)}")
    c.check("交出来的是每组**后**写入的那条（留先来的）",
            [x["id"] for x in dup] == ["1", "3", "5"], str([x["id"] for x in dup]))
    c.check("一组只有一条时不动它（他说的是「删一个」不是「删掉它」）",
            _one_per_group([{"id": "x", "title": "独一无二", "date": "d",
                             "start": "1", "end": "2"}]) == [])

    # ③ 解析：去重归去重、全选归全选
    r = resolve_todo_remove_reply("每个删一个", six)
    c.check("回「每个删一个」→ dedup 且只带 3 条",
            r["status"] == "dedup" and len(r["todos"]) == 3, r.get("status"))
    r2 = resolve_todo_remove_reply("都删了", six)
    c.check("回「都删了」→ 仍是 all 且是全部 6 条（没被去重抢走）",
            r2["status"] == "all" and len(r2["todos"]) == 6, r2.get("status"))
    return c


def batch_h_dedupe_e2e():
    """H 单元：端到端——重复的两条 → 「每个删一个」→ 挂 1 条卡 → 确认后只删掉多出来的那条。"""
    title("H. 端到端去重：重复 2 条 → 每个删一个 → 只删 1 条，另一条留着")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_dupes()
        from app.store import list_todos

        sid = "ded-1"
        client.post("/api/chat", json={"message": "删除撞时间的代办",
                                       "module": "planner", "session_id": sid})
        d = client.post("/api/chat", json={"message": "每个删一个",
                                           "module": "planner", "session_id": sid}).json()
        opts = d.get("options") or []
        kinds = [o.get("kind") for o in opts]
        c.check("回「每个删一个」→ 一张批量卡", kinds == ["todo_remove_batch"], str(kinds))
        c.check("卡上就 1 条（重复里多出来的那条），不是把两条都端出来",
                bool(opts) and opts[0].get("count") == 1,
                str(opts[0].get("summary")) if opts else "无卡")
        c.check("回话说明了规则（留先写进去的那条）",
                "留" in (d.get("answer") or ""), (d.get("answer") or "")[:80])
        c.check("还没确认 → 3 条一条没少", len(list_todos()) == 3, f"{len(list_todos())}")

        r = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        left = [t.get("title") for t in list_todos()]
        c.check("确认后只删掉 1 条（剩 2 条）", len(left) == 2, str(left))
        c.check("留下的是重复里的另一条 + 不重复的丙",
                sorted(left) == ["丙", "甲"], str(sorted(left)))
        c.check("回执如实报数（删 1 条）", "1" in (r.get("answer") or ""),
                (r.get("answer") or "").replace("\n", " ")[:70])

        # 手里没清单时说去重 → 不许凭空造卡
        d2 = client.post("/api/chat", json={"message": "每个删一个", "module": "planner",
                                            "session_id": "ded-2"}).json()
        c.check("没清单时说「每个删一个」→ 不出批量卡",
                "todo_remove_batch" not in [o.get("kind") for o in (d2.get("options") or [])])
    return c


def main():
    results = [
        batch_a_unit(),
        batch_b_reply_unit(),
        batch_c_propose_e2e(),
        batch_d_confirm_e2e(),
        batch_e_done_e2e(),
        batch_f_guard(),
        batch_g_dedupe(),
        batch_h_dedupe_e2e(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 批量待办（都删了/全部删掉/一起删了吧）总账：通过 {total_pass} 项，"
          f"失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
