"""#117：学生一次点了好几条 → 挂**一张**批量确认条（点一次办完），不是一屏两张卡点两次。

起因（用户 2026-11-03 21:34 截图）：系统列了周六 3 条待办，他回「**1和2**」，
屏幕冒出**两张** `todo_remove` 卡、两个【删除这条】按钮 —— 得点两次。
trace 里那轮 `tools` 是 4 个名字 = 模型自己调了两次 `propose_todo_remove`（一次一条），
说明这句**根本没进确定性分支**。

根因两处（同一个逻辑在两个族里抄了两遍）：
  · `resolve_todo_remove_reply` / `resolve_todo_done_reply` 把"点中好几条"降级成 `many`
    （注释原话「一次只能删一条（不猜、不批量）」），上层只能把清单再列一遍反问"你要删哪一条"
    —— 他刚说了两条、系统反问一条 = 原地打转，于是掉给模型；
  · `main.py` 两支 `many` 之间没有"点名几条"这条路，
    而 `todo_remove_batch_proposal` / `todo_done_batch_proposal` 全仓只被 `status == "all"`（"都删"）
    调过 —— "我挑中这几条"没有对应 status，永远走不到现成的批量卡。

对应改动：两族各返回 `status="pick"`；`main.py` 各加一支 `pick` 分派（各挂一张批量条）；
`_pick_todo_indexes` 的「第N条」捕获补「第」字，并**另加一趟按量词的逐条 findall** 取并集
（单据 §1.3 只补字符类**达不到它自己写的期望**，见 A 批那两条 ★ 断言）。

红线没破：`pick` 出来的还是**提案**，学生点【确认删除全部】/【确认完成全部】之前一条都不动。
前端一行没动（F 批钉住两族批量卡的渲染分支还在）。
"""
import ast
import datetime

from _harness import Checker, chat_nodes, make_client, sandbox, seed_todos, title


def _d(off: int = 3) -> str:
    return (datetime.date.today() + datetime.timedelta(days=off)).isoformat()


def _seed3():
    """周六那三件事（照用户截图造）：打乒乓球 / 健身房 / 背30个单词。"""
    d = _d(3)
    seed_todos([{"id": "p1", "title": "打乒乓球", "date": d, "start": "10:00", "end": "13:00"},
                {"id": "p2", "title": "健身房", "date": d, "start": "17:10", "end": "18:10"},
                {"id": "p3", "title": "背30个单词", "date": d, "start": "19:00", "end": "20:30"}])
    return d


def _seed3same():
    """三条**同名**（标记完成那一族要用同名才列得出清单）。"""
    d = _d(3)
    seed_todos([{"id": "r1", "title": "夜跑", "date": d, "start": "07:00", "end": "08:00"},
                {"id": "r2", "title": "夜跑", "date": d, "start": "18:00", "end": "19:00"},
                {"id": "r3", "title": "夜跑", "date": d, "start": "20:00", "end": "21:00"}])
    return d


def _cands():
    return [{"id": "a", "title": "甲", "date": "2026-10-03", "start": "14:00", "end": "15:00"},
            {"id": "b", "title": "乙", "date": "2026-10-03", "start": "19:00", "end": "19:30"},
            {"id": "c", "title": "丙", "date": "2026-10-03", "start": "20:00", "end": "20:30"}]


def batch_a_indexes():
    """A 单元：一次点好几条 —— 序号解析（单据 §0.6 那张表逐条 + 不误伤）。"""
    title("A. 「1和2」「第一条和第二条」都取全（不误伤别的写法）")
    c = Checker()
    from app.modules.planner import _pick_todo_indexes as P

    for say, want in (("1和2", [1, 2]), ("1、2", [1, 2]), ("第1和第2条", [1, 2]),
                      ("第2、3条", [2, 3]), ("23", [2, 3]), ("1和2和3", [1, 2, 3])):
        c.check(f"「{say}」→ {want}", P(say, 3) == want, str(P(say, 3)))
    # ★ 单据 §0.6 点名要修的那两种中文连写：§1.3 只补「第」字**做不到**（懒捕获在第一个
    #   「条」处就收口了，「条」不在捕获字符类里），实测两种都只回 [1] ——
    #   学生说了两条、系统只挑一条还不吭声（status=ok 直接挂单条卡），比吞头那个更糟。
    #   所以本单在补字符类之外**另加一趟按量词的逐条 findall**，这两条断言就是那趟的存在理由。
    for say in ("第一条和第二条", "第1条和第2条", "第一条、第二条"):
        c.check(f"★「{say}」→ [1,2]（单据 §1.3 的期望，光补字符类达不到）",
                P(say, 3) == [1, 2], str(P(say, 3)))
    # 正控：单条与越界的老行为不许被这趟 findall 带跑
    for say, want in (("第二条", [2]), ("第1条", [1]), ("2", [2]), ("最后一条", [-1]),
                      ("5", []), ("", [])):
        c.check(f"正控「{say}」→ {want}（没被多对一趟带跑）", P(say, 3) == want, str(P(say, 3)))
    # 不误伤：量词必须挨着数字才算序号
    for say in ("第1次跑完第2圈", "把第3节课改了", "第2页和第三页"):
        c.check(f"不误伤「{say}」→ 空", P(say, 3) == [], str(P(say, 3)))
    # 已知不做（单据 §1.3 末尾）：区间说法跳过中间那条。照实钉住现状，将来要治先拆这条。
    c.check("已知不处理区间：「第1条到第3条」→ [1,3]（跳过 2，本单不治）",
            P("第1条到第3条", 3) == [1, 3], str(P("第1条到第3条", 3)))
    return c


def batch_b_two_families():
    """B 单元：两个族都要回 `pick`（单据只点了删的那半，完成那半是同一份逻辑抄的第二遍）。"""
    title("B. 两族对称：点多条 → pick（单条/「都」/模糊 不许跟着变）")
    c = Checker()
    from app.modules.planner import resolve_todo_done_reply, resolve_todo_remove_reply

    cand = _cands()
    # 「23」按**每一位**取序号（2、3）→ 乙、丙，不是甲、乙 —— 这条顺手钉住"连写数字按屏上顺序取"
    for say, want in (("1和2", ["a", "b"]), ("第一条和第二条", ["a", "b"]),
                      ("第1条和第2条", ["a", "b"]), ("23", ["b", "c"])):
        r = resolve_todo_remove_reply(say, cand)
        d = resolve_todo_done_reply(say, cand)
        c.check(f"删：「{say}」→ pick + 正好那几条",
                r["status"] == "pick" and [x["id"] for x in r["todos"]] == want,
                f"{r.get('status')} {[x['id'] for x in r.get('todos') or []]}")
        c.check(f"完成：「{say}」→ pick（跟删那一族同一个形状）",
                d["status"] == "pick" and [x["id"] for x in d["todos"]] == want,
                f"{d.get('status')} {[x['id'] for x in d.get('todos') or []]}")
    # 正控（单据 §3.3）：这四类删法一条都不许跟着改
    r1 = resolve_todo_remove_reply("第二条", cand)
    c.check("正控「第二条」→ 仍是单条 ok（全项目最常用的路）",
            r1["status"] == "ok" and r1["proposal"]["title"] == "乙", r1.get("status"))
    r2 = resolve_todo_remove_reply("都删了", cand)
    c.check("正控「都删了」→ 仍是 all（跟 pick 共用一个构造器，容易一起改坏）",
            r2["status"] == "all" and len(r2["todos"]) == 3, r2.get("status"))
    r3 = resolve_todo_done_reply("都完成了", cand)
    c.check("正控「都完成了」→ 仍是 all", r3["status"] == "all" and len(r3["todos"]) == 3,
            r3.get("status"))
    six = _cands() + [{"id": "d", "title": "甲", "date": "2026-10-03",
                       "start": "14:00", "end": "15:00"},
                      {"id": "e", "title": "乙", "date": "2026-10-03",
                       "start": "19:00", "end": "19:30"},
                      {"id": "f", "title": "丙", "date": "2026-10-03",
                       "start": "20:00", "end": "20:30"}]
    r4 = resolve_todo_remove_reply("每个删一个", six)
    c.check("正控「每个删一个」→ 仍是 dedup（去重那条路不动）",
            r4["status"] == "dedup", r4.get("status"))
    r5 = resolve_todo_remove_reply("没在清单上的名字", cand)
    c.check("正控乱答 → 仍是 unclear 带整份清单（没被 pick 抢走）",
            r5["status"] == "unclear" and len(r5["todos"]) == 3, r5.get("status"))
    r6 = resolve_todo_remove_reply("1和2", None)
    c.check("手里没清单时说「1和2」→ 不硬造 pick", r6.get("status") != "pick", r6.get("status"))
    return c


def batch_c_remove_e2e():
    """C 真机链路（删）：列 3 条 → 回「1和2」→ **一张**批量条 → 确认后那条 3 还留着。"""
    title("C. 删待办：「1和2」→ 一张 todo_remove_batch（此时一条没删）→ 确认才删")
    c = Checker()
    with sandbox():
        client = make_client()
        d = _seed3()
        from app.store import list_todos

        sid = "p117-1"
        d1 = client.post("/api/chat", json={"message": f"删掉{d}的待办",
                                            "module": "planner", "session_id": sid}).json()
        c.check("第一步列 3 条、**不**出确认条",
                not (d1.get("options") or []) and "打乒乓球" in (d1.get("answer") or ""),
                str([o.get("kind") for o in (d1.get("options") or [])]))

        d2 = client.post("/api/chat", json={"message": "1和2",
                                           "module": "planner", "session_id": sid}).json()
        opts = d2.get("options") or []
        kinds = [o.get("kind") for o in opts]
        ans = d2.get("answer") or ""
        c.check("★ 命门：回「1和2」→ **正好一张**卡（旧幕挂两张、要他点两次）",
                kinds == ["todo_remove_batch"], str(kinds))
        c.check("卡上就是那两条（丙没被捎上）",
                bool(opts) and opts[0].get("count") == 2
                and {i.get("title") for i in opts[0].get("items") or []} == {"打乒乓球", "健身房"},
                str(opts[0].get("summary")) if opts else "无卡")
        c.check("每条原样念清楚（日期钟点都在）",
                bool(opts) and all(i.get("date") and i.get("start")
                                   for i in opts[0].get("items") or []))
        c.check("话术说的是「你点的是这 2 条」，不是「我不敢替你挑」（不再反问哪一条）",
                "2 条" in ans and "不敢替你挑" not in ans, ans.replace("\n", " ")[:70])
        c.check("点头之前一条没删（红线）", len(list_todos()) == 3, f"{len(list_todos())}")

        r = client.post("/api/chat", json={"message": "确认", "session_id": sid}).json()
        left = {t["title"] for t in list_todos()}
        c.check("回「确认」→ 那两条真删了、第三条原封不动",
                left == {"背30个单词"}, str(left))
        c.check("回执如实报数（2 条）", "2" in (r.get("answer") or ""),
                (r.get("answer") or "").replace("\n", " ")[:60])
    return c


def batch_d_done_e2e():
    """D 真机链路（完成）：同一幕走标记完成那一族（单据没点这半，这里是它的对称面）。"""
    title("D. 标记完成：「1和2」→ 一张 todo_done_batch → 确认才置 done")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed3same()
        from app.store import list_todos

        sid = "p117-2"
        d1 = client.post("/api/chat", json={"message": "把夜跑标记完成",
                                            "module": "planner", "session_id": sid}).json()
        c.check("三条同名 → 列清单、不出卡",
                not (d1.get("options") or []) and "不敢替你挑" in (d1.get("answer") or ""),
                (d1.get("answer") or "").replace("\n", " ")[:50])

        d2 = client.post("/api/chat", json={"message": "1和2",
                                            "module": "planner", "session_id": sid}).json()
        kinds = [o.get("kind") for o in (d2.get("options") or [])]
        c.check("★ 完成那一半同样一张卡", kinds == ["todo_done_batch"], str(kinds))
        c.check("点两条≠全办：卡上是 2 条、不是那 3 条",
                (d2.get("options") or [{}])[0].get("count") == 2,
                str((d2.get("options") or [{}])[0].get("count")))
        c.check("点头之前状态没变", all(t.get("status") != "done" for t in list_todos()))

        client.post("/api/chat", json={"message": "确认", "session_id": sid})
        st = {t["id"]: t.get("status") for t in list_todos()}
        c.check("确认后前两条置 done、第三条还是 planned（没顺手替他全办）",
                st == {"r1": "done", "r2": "done", "r3": "planned"}, str(st))
    return c


def batch_e_cn_numerals_e2e():
    """E 真机链路：中文数字连写「第一条和第二条」也走同一张卡（专打 A 批那两条 ★）。"""
    title("E. 「第一条和第二条」→ 同样一张批量条（不是单条卡）")
    c = Checker()
    with sandbox():
        client = make_client()
        d = _seed3()
        from app.store import list_todos

        sid = "p117-3"
        client.post("/api/chat", json={"message": f"删掉{d}的待办",
                                       "module": "planner", "session_id": sid})
        r = client.post("/api/chat", json={"message": "第一条和第二条",
                                           "module": "planner", "session_id": sid}).json()
        opts = r.get("options") or []
        c.check("中文连写 → 一张批量卡（只补字符类会退化成一张单条卡、悄悄丢掉第二条）",
                [o.get("kind") for o in opts] == ["todo_remove_batch"],
                str([o.get("kind") for o in opts]))
        c.check("卡上是 2 条：打乒乓球 + 健身房",
                bool(opts) and opts[0].get("count") == 2
                and {i.get("title") for i in opts[0].get("items") or []} == {"打乒乓球", "健身房"},
                str(opts[0].get("summary")) if opts else "无卡")
        c.check("此时一条都没删", len(list_todos()) == 3, f"{len(list_todos())}")
    return c


def batch_f_wiring():
    """F 静态：两支分派**成对**存在、各用各的构造器、落在聊天主体里；与 many 判据互斥；前端一行没动。"""
    title("F. 接线成对（两族一支不少）+ 前端未动（批量卡渲染分支还在）")
    c = Checker()
    import os

    import app.main as m
    import app.modules.planner as pl

    mtree = ast.parse(open(m.__file__, encoding="utf-8").read())
    ptree = ast.parse(open(pl.__file__, encoding="utf-8").read())

    def _consts(node):
        return [x.value for x in ast.walk(node) if isinstance(x, ast.Constant)]

    def _calls(body):
        return [x.func.id for st in body for x in ast.walk(st)
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Name)]

    picks = []
    for n in ast.walk(mtree):
        if isinstance(n, ast.If) and "pick" in _consts(n.test) and "status" in ast.unparse(n.test):
            picks.append((n.lineno, _calls(n.body)))
    c.check("main.py 里有**两支** pick 分派（删 + 完成，一支不缺；少一支就是只修了半张单）",
            len(picks) == 2, str(picks))
    c.check("★ 配对：一支用 todo_remove_batch_proposal、另一支用 todo_done_batch_proposal"
            "（拿错构造器 = 说删却弹完成的卡）",
            sorted(sum([[k for k in cs if k.endswith("_batch_proposal")] for _, cs in picks], []))
            == ["todo_done_batch_proposal", "todo_remove_batch_proposal"],
            str(picks))

    # ★ 这条替换掉了单据 §1.2 那句"插在 many 之后就永远轮不到"—— **实测不成立**：
    #   many 的判据是 `status in ("many","unclear")`，里面没有 `pick`，两支对调后
    #   屏幕上照样是一张批量卡。真正要钉的是**这个不重叠**（M8 把 "pick" 塞进 many 的
    #   元组，两条都红：行为回到"再列一遍清单"、静态也看见判据被撞）。
    #   认判据本身（`_consts(n.test)`），不认"这个 kind 在文件里第一次出现"那种会认错地方的写法。
    many_tests = [n for n in ast.walk(mtree) if isinstance(n, ast.If)
                  and "many" in _consts(n.test) and "unclear" in _consts(n.test)]
    c.check(f"每族各有一支 many/unclear 清单支（本单靠它**没**把 pick 吞进去）",
            len(many_tests) == 2, str([n.lineno for n in many_tests]))
    c.check("★ 判据互斥：many 支的条件里不许出现 \"pick\"（出现=点名几条又被降级成反问哪一条）",
            all("pick" not in _consts(n.test) for n in many_tests),
            str([ast.unparse(n.test)[:60] for n in many_tests]))

    rets = {}
    for n in ast.walk(ptree):
        if isinstance(n, ast.FunctionDef) and n.name in ("resolve_todo_remove_reply",
                                                        "resolve_todo_done_reply"):
            got = [x.value for x in ast.walk(n) if isinstance(x, ast.Return)
                   and isinstance(x.value, ast.Dict)]
            rets[n.name] = [k for k in
                            ([v.value for k, v in zip(x.keys, x.values)
                              if isinstance(k, ast.Constant) and k.value == "status"
                              and isinstance(v, ast.Constant)] for x in got) if "pick" in k]
    c.check("★ 配对：planner 两族的 resolver **各**返回一次 pick（抄了两遍就得改两遍）",
            len(rets) == 2 and all(len(v) == 1 for v in rets.values()), str(rets))

    # 分派支必须落在**聊天主体**里（挂在没人调的函数上 = 断言全绿、屏幕上还是两张卡）
    spans = []
    for n in chat_nodes(mtree):
        end = max(getattr(x, "lineno", n.lineno) for x in ast.walk(n))
        spans.append((n.name, n.lineno, end))
    inside = [sp for sp in spans for ln, _ in picks if sp[1] <= ln <= sp[2]]
    c.check("两支 pick 分派都在聊天主体（`_chat_impl`/`_run_agent_loop`）里，不是挂在死函数上",
            len(inside) == 2, str(inside))

    old = []
    for root, dirs, files in os.walk(os.path.dirname(m.__file__)):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", "data")]
        for fn in files:
            if fn.endswith(".py"):
                p = os.path.join(root, fn)
                txt = open(p, encoding="utf-8", errors="replace").read()
                for w in ("一次只能删一条", "一次只能改一条"):
                    if w in txt:
                        old.append((os.path.basename(p), w))
    c.check("旧决定从整棵 app/ 里清干净了：「一次只能删一条」「一次只能改一条」归零",
            not old, str(old))

    html = open(os.path.join(os.path.dirname(m.__file__), "static", "student.html"),
                encoding="utf-8").read()
    c.check("前端一行没动：两族批量卡的渲染分支还在（本单只改后端）",
            'o.kind === "todo_remove_batch"' in html and 'o.kind === "todo_done_batch"' in html)
    c.check("前端那句「有 N 条对上了你说的话」照旧留着（单据 §四：不改它，只让点名几条不再走到它）",
            "条对上了你说的话" in html)
    return c


BATCHES = (batch_a_indexes, batch_b_two_families, batch_c_remove_e2e,
           batch_d_done_e2e, batch_e_cn_numerals_e2e, batch_f_wiring)


def main():
    c = Checker()
    for b in BATCHES:
        r = b()
        c.passed += r.passed
        c.failed += r.failed
    print(c.summary(f"#117 一次点几条 → 一张批量确认条（删 + 完成两族）"))
    return c


if __name__ == "__main__":
    raise SystemExit(0 if main().failed == 0 else 1)
