"""证据判决：系统什么时候可以**替他挑一条**，什么时候必须列清单。

背景（学生原话："去思考学生表达的意思，不是字面上的，要连接上下文猜测"）：
学生嫌"一律列清单"啰嗦，但删除**不可逆**，猜错代价他承担不起。

解法不是"猜得更准"，而是把"猜"限定在**证据够硬**的两处结构性事实上，
并且猜出来的东西**仍然只是提案**（卡 + 明确依据 + "换一条"出口）：

  S1 精确命中：名字**正好等于**某一条标题的只有一条
     （《游泳》和《游泳锻炼》都在库里，说"删掉游泳"→ 就是《游泳》，不是列清单）
  S2 等价组 ：候选在业务上**完全等价**（同名＋同日＋同时段＋同状态）
     → 删哪条学生看到的日程都一样，所以**不存在"猜错"**

两条都踩在"结构上确定"的事实上，不做相似度猜测——0.31 比 0.29 是噪声。

对应改动：planner 的 _equiv_key / _single_equiv_group / _earliest / _wants_rechoose /
         _todo_remove_proposal(note,picked_by,alternates) / resolve_todo_remove(pool=) /
         resolve_todo_remove_reply（把清单当上下文传进去）；
         main 的 followup 放宽（手里有清单 + 明确报序号）＋ 猜出来的卡保留候选清单。
"""
from _harness import Checker, make_client, sandbox, title

_D = "2026-10-03"


def _seed_three_pairs():
    """三对重复（游泳×2 / 交电费×2 / 晨读×2）——正是学生截图里的处境。"""
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("交电费", _D, "19:00", "19:30")
    add_todo("交电费", _D, "19:00", "19:30")
    add_todo("晨读", _D, "07:00", "07:30")
    add_todo("晨读", _D, "07:00", "07:30")


def batch_a_equiv_unit():
    """A 单元：等价判据本身（什么算"删哪条都一样"）。"""
    title("A. 等价组：同名 + 同日 + 同时段 + 同状态 才算等价")
    c = Checker()
    from app.modules.planner import _equiv_key, _single_equiv_group, _earliest

    A = {"id": "a1", "title": "游泳", "date": _D, "start": "14:00", "end": "15:00",
         "status": "planned", "created_at": "2026-09-29 19:03"}
    B = {**A, "id": "a2", "created_at": "2026-09-29 19:06"}
    C = {**A, "id": "c1", "start": "19:00", "end": "19:30"}
    D = {**A, "id": "a3", "status": "done"}

    c.check("两条全同 → 同一组", len(_single_equiv_group([A, B]) or []) == 2)
    c.check("同名但时段不同 → 不是一组", _single_equiv_group([A, C]) is None)
    c.check("同名但状态不同（planned/done）→ **不是**一组（删哪条不一样）",
            _single_equiv_group([A, D]) is None)
    c.check("只有一条 → 不算等价组（没有「多出来的」可言）",
            _single_equiv_group([A]) is None)
    c.check("空 → None", _single_equiv_group([]) is None)
    c.check("挑的是**先写进去的**那条", _earliest([B, A])["id"] == "a1",
            _earliest([B, A])["id"])
    c.check("判据含状态位", _equiv_key(A) != _equiv_key(D))
    return c


def batch_b_rechoose_unit():
    """B 单元：学生说"不对/换一条"要认得出（不然卡上的出口是断的）。"""
    title("B. 「不对/换一条」要认得出")
    c = Checker()
    from app.modules.planner import _wants_rechoose
    for s in ("不对", "不是", "不是这条", "换一条", "换一个", "重选", "再看看"):
        c.check(f"「{s}」→ 认作要重挑", _wants_rechoose(s))
    for s in ("确认", "第一条", "23", "游泳", "取消"):
        c.check(f"「{s}」→ 不算重挑", not _wants_rechoose(s))
    return c


def batch_c_pool_unit():
    """C 单元：把"候选清单"当上下文传进去后，判决在**清单内**生效。"""
    title("C. 只在候选清单里判：等价组敢挑、两组不同不猜")
    c = Checker()
    from app.modules.planner import resolve_todo_remove
    same = [{"id": "x1", "title": "游泳", "date": _D, "start": "14:00", "end": "15:00",
             "status": "planned", "created_at": "2026-09-29 19:03"},
            {"id": "x2", "title": "游泳", "date": _D, "start": "14:00", "end": "15:00",
             "status": "planned", "created_at": "2026-09-29 19:06"}]
    diff = [same[0], {**same[1], "id": "y1", "start": "19:00", "end": "19:30"}]

    r = resolve_todo_remove("游泳", same)
    p = r.get("proposal") or {}
    c.check("清单内两条等价 → ok（敢挑）", r.get("status") == "ok", r.get("status"))
    c.check("带了依据标记 duplicate_group", p.get("picked_by") == "duplicate_group")
    c.check("带上了另一份的 id（供'换一条'）", p.get("alternates") == ["x2"], str(p.get("alternates")))
    c.check("note 说明了「删哪条都一样」和挑哪条", bool(p.get("note")) and "一模一样" in p["note"],
            str(p.get("note"))[:60])
    c.check("挑的是先写进去的 x1", p.get("todo_id") == "x1", str(p.get("todo_id")))

    r2 = resolve_todo_remove("游泳", diff)
    c.check("清单内两条**不同**（时段不一样）→ many（不猜）",
            r2.get("status") == "many", r2.get("status"))
    return c


def batch_d_exact_unit():
    """D 单元：精确命中（《游泳》 vs《游泳锻炼》）。"""
    title("D. 名字精确等于的只有一条 → 就是它（不被'包含匹配'搅成列清单）")
    c = Checker()
    from app.modules.planner import resolve_todo_remove
    pool = [{"id": "e1", "title": "游泳", "date": _D, "start": "14:00", "end": "15:00",
             "status": "planned"},
            {"id": "e2", "title": "游泳锻炼", "date": _D, "start": "20:00", "end": "21:00",
             "status": "planned"}]
    r = resolve_todo_remove("删掉游泳", pool)
    p = r.get("proposal") or {}
    c.check("命中《游泳》而不是列清单", r.get("status") == "ok", r.get("status"))
    c.check("依据是精确命中 exact_title", p.get("picked_by") == "exact_title", str(p.get("picked_by")))
    c.check("选的确实是《游泳》", p.get("title") == "游泳", str(p.get("title")))
    c.check("note 说清了'正好是这个名字的只有它'",
            "正好" in (p.get("note") or ""), str(p.get("note"))[:50])
    return c


def batch_e_e2e_guess():
    """E 单元：端到端——列 6 条 → 点「游泳」→ 替他挑一条出卡（带依据），且**一条没删**。"""
    title("E. 端到端：列清单 → 点名字 → 出卡带依据（没确认一条不删）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "ge-1"
        d1 = client.post("/api/chat", json={"message": "删除撞时间的代办",
                                            "module": "planner", "session_id": sid}).json()
        c.check("第一步照旧列清单（三组不同 → 不猜）",
                "游泳" in (d1.get("answer") or "") and not (d1.get("options") or []),
                (d1.get("answer") or "").replace("\n", " ")[:70])

        d2 = client.post("/api/chat", json={"message": "游泳",
                                            "module": "planner", "session_id": sid}).json()
        opts = d2.get("options") or []
        ans = d2.get("answer") or ""
        c.check("回「游泳」→ 出确认卡（不再让他从两条里挑）",
                [o.get("kind") for o in opts] == ["todo_remove"], str([o.get("kind") for o in opts]))
        c.check("卡上带依据标记 duplicate_group",
                bool(opts) and opts[0].get("picked_by") == "duplicate_group")
        c.check("回话里把依据也写出来了（学生看得见「凭什么挑这条」）",
                "一模一样" in ans, ans.replace("\n", " ")[:90])
        c.check("**没确认 → 6 条一条没少**（红线）", len(list_todos()) == 6,
                f"{len(list_todos())}")

        # 纠错出口：说"换一条" → 回到清单（不执行）
        d3 = client.post("/api/chat", json={"message": "换一条",
                                            "module": "planner", "session_id": sid}).json()
        c.check("回「换一条」→ 重新列清单（不出确认卡）",
                not (d3.get("options") or [])
                and "游泳" in (d3.get("answer") or ""),
                (d3.get("answer") or "").replace("\n", " ")[:70])

        # 纠错出口：报序号 → 仍接得住（证明清单还留着）
        client.post("/api/chat", json={"message": "游泳", "module": "planner", "session_id": sid})
        d5 = client.post("/api/chat", json={"message": "第二条",
                                            "module": "planner", "session_id": sid}).json()
        c.check("替他挑过之后，回「第二条」仍能按序号选中（清单没丢）",
                [o.get("kind") for o in (d5.get("options") or [])] == ["todo_remove"],
                str([o.get("kind") for o in (d5.get("options") or [])]))
        c.check("全程没确认 → 仍然 6 条", len(list_todos()) == 6, f"{len(list_todos())}")
    return c


def batch_f_guard():
    """F 回归：证据不够硬时**不许**猜（否则等于把'不猜'的红线拆了）。"""
    title("F. 守卫：证据不够硬，仍然列清单不猜")
    c = Checker()
    with sandbox():
        client = make_client()
        from app.store import add_todo, delete_todo, list_todos
        for t in list_todos():
            delete_todo(t["id"])
        add_todo("游泳", _D, "14:00", "15:00")
        add_todo("游泳", _D, "19:00", "19:30")   # 同名但时段不同 → 真的分不清

        d = client.post("/api/chat", json={"message": "删掉游泳", "module": "planner",
                                           "session_id": "gf-1"}).json()
        c.check("同名两条、时段不同 → 列清单，不出确认卡（不猜）",
                not (d.get("options") or []) and "游泳" in (d.get("answer") or ""),
                (d.get("answer") or "").replace("\n", " ")[:70])

        # 库里没清单、也没有名字 → 更不许猜
        d2 = client.post("/api/chat", json={"message": "把明天那条删了", "module": "planner",
                                            "session_id": "gf-2"}).json()
        c.check("没点名、也没清单 → 绝不凭空出确认卡", not (d2.get("options") or []))

        # ★ 关键回归（这一版真踩过）：**只给筛条件、没点名**时，哪怕筛出来的整批完全等价，
        #   也不许替他挑一条——"删几条"这件事他还没说（他可能想删一整批）。
        #   写这条是因为：一开始把等价组判决放在"点了名"这道闸门**外面**，
        #   结果"删除撞时间的代办"直接出卡、后面「每个删一个」全被抢走（test_12 挂了 5 项）。
        for t in list_todos():
            delete_todo(t["id"])
        add_todo("甲", _D, "14:00", "15:00")
        add_todo("甲", _D, "14:00", "15:00")
        d3 = client.post("/api/chat", json={"message": "删除撞时间的代办", "module": "planner",
                                            "session_id": "gf-3"}).json()
        c.check("只给筛条件（没点名）→ 整批等价也**只列清单**，不替他挑",
                not (d3.get("options") or []) and "甲" in (d3.get("answer") or ""),
                (d3.get("answer") or "").replace("\n", " ")[:70])
    return c


def main():
    results = [
        batch_a_equiv_unit(),
        batch_b_rechoose_unit(),
        batch_c_pool_unit(),
        batch_d_exact_unit(),
        batch_e_e2e_guess(),
        batch_f_guard(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 证据判决（什么时候可以替他挑）总账：通过 {total_pass} 项，"
          f"失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
