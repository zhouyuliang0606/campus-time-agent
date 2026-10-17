"""#33：点卡片确认 → 写库**必须销卡**，否则刷新后卡复活、再点就重复写入。

用户 2026-10-17 真机原话：
  「我先前加入一个代办，点击确认后，退出去刷新回来，又弹了一次卡，再点击确认重新加了一个相同代办」。

定性（本文件的存在理由）：
  读接口 `/api/pending/{sid}` 一直只回"未 applied"的卡 —— 它没错；
  错的是 `POST /api/todos`、`PUT /api/todos/{tid}`、`DELETE /api/todos/{tid}` 三个写入口
  **从来不销卡**。课表 `/api/timetable/apply` 与批量 `/api/todos/batch` 早就写了 `mark_applied`，
  所以这是"漏了入口"，不是机制缺失。

⚠️ 三条本仓老规矩在这个文件里都照着办了：
  ① 起点自己种（`seed_todos`）—— `sandbox()` 是**照抄真实演示数据**，本机与干净克隆是两种条数（红线 18）；
  ② 判据一律"读盘数条数"，不看状态码就下结论；
  ③ 形状/位置断言先剔注释，且**不认变量名只认形状**。
"""
import json
import os
import re

from _harness import Checker, data_path, make_client, sandbox, seed_todos, title

FUTURE = "2026-12-31"          # is_past_date 那一档要过得去（今天 2026-10-17）
SRC_STU = "app/static/student.html"
SRC_CFM = "app/static/confirm.html"
SRC_MAIN = "app/main.py"


def _todos():
    p = data_path("student", "todos.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8")).get("todos") or []


def _ids():
    return [t.get("id") for t in _todos()]


def _strip(text):
    """剔注释（HTML 块 + JS 块注释 + JS 行注释）—— 负向断言一律先剥再比。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(ln.split("//")[0] for ln in t.splitlines())


def _pending(cli, sid):
    return cli.get(f"/api/pending/{sid}").json().get("pending")


def batch_a_three_entrances():
    """A 批 · 三个入口各钉一遍：写完卡就没了，再点不重复写。"""
    c = Checker()
    title("A. 三个写入口：写完销卡 + 重复点不重复写")

    from app.agent.pending import save_pending

    with sandbox():
        cli = make_client()

        # —— ① POST /api/todos（点【确认加入】，用户踩的就是这条）——
        seed_todos([])
        sid = "t60-add"
        save_pending(sid, [{"kind": "todo_add", "title": "护栏样本甲",
                            "date": FUTURE, "start": "15:00", "end": "16:00"}])
        r1 = cli.post("/api/todos", json={"session_id": sid, "title": "护栏样本甲",
                                          "date": FUTURE, "start": "15:00", "end": "16:00",
                                          "from_card": True}).json()
        c.check("① 卡片确认写成功：库里正好 1 条，且这次**不许**被当成已办过",
                r1.get("ok") is True and not r1.get("already") and len(_todos()) == 1,
                f"实际 {len(_todos())} 条 {str(r1)[:60]}")
        c.check("① 写完 /api/pending 回 null（用户那一幕的正身：刷新后卡不该再回来）",
                _pending(cli, sid) is None, "")
        r2 = cli.post("/api/todos", json={"session_id": sid, "title": "护栏样本甲",
                                          "date": FUTURE, "start": "15:00", "end": "16:00",
                                          "from_card": True}).json()
        c.check("① 同一张卡再点一次：回 already + 一句人话，**库里还是 1 条**（多加一条就是用户报的 bug）",
                r2.get("already") is True and bool(r2.get("message")) and len(_todos()) == 1,
                f"条数 {len(_todos())} {str(r2)[:60]}")

        # —— ② PUT /api/todos/{tid}（点【确认完成】）——
        seed_todos([{"title": "护栏样本乙", "date": FUTURE, "start": "15:00", "end": "16:00"}])
        tid = _ids()[0]
        sid2 = "t60-done"
        save_pending(sid2, [{"kind": "todo_done", "todo_id": tid, "title": "护栏样本乙",
                             "date": FUTURE, "start": "15:00", "end": "16:00"}])
        p1 = cli.put(f"/api/todos/{tid}", json={"session_id": sid2, "status": "done",
                                                "from_card": True}).json()
        c.check("② 完成卡：写成功 + 卡销掉（PUT 原来连 session_id 都不读，这次一起补上）",
                p1.get("ok") is True and _pending(cli, sid2) is None, str(p1)[:70])
        p2 = cli.put(f"/api/todos/{tid}", json={"session_id": sid2, "status": "done",
                                                "from_card": True}).json()
        c.check("② 再点一次：回 already，不许翻回未完成、也不许报「待办不存在」（那是把一个谎报换成另一个）",
                p2.get("already") is True
                and (_todos()[0].get("status") == "done" or _todos()[0].get("done_dates")),
                str(p2)[:70])

        # —— ③ DELETE /api/todos/{tid}（点【确认删除】）——
        seed_todos([{"title": "护栏样本丙", "date": FUTURE, "start": "15:00", "end": "16:00"},
                    {"title": "别的那一条", "date": FUTURE, "start": "18:00", "end": "19:00"}])
        tid3, other = _ids()[0], _ids()[1]
        sid3 = "t60-del"
        save_pending(sid3, [{"kind": "todo_remove", "todo_id": tid3, "title": "护栏样本丙",
                             "date": FUTURE, "start": "15:00", "end": "16:00"}])
        d1 = cli.request("DELETE", f"/api/todos/{tid3}",
                         json={"session_id": sid3, "from_card": True}).json()
        c.check("③ 删除卡：那一真被删掉 + 卡销掉",
                d1.get("ok") is True and tid3 not in _ids() and _pending(cli, sid3) is None,
                str(d1)[:70])
        d2 = cli.request("DELETE", f"/api/todos/{tid3}",
                         json={"session_id": sid3, "from_card": True}).json()
        c.check("③ 再点一次：回 already（**不是 404**），而且**没删到另一条**"
                "（再删一次最坏的情况是删掉同名的另一件事）",
                d2.get("already") is True and other in _ids() and len(_todos()) == 1,
                f"剩下 {len(_todos())} 条 {str(d2)[:60]}")
    return c


def batch_b_no_collateral():
    """B 批 · 副作用边界：非卡路径不销卡；写失败不销卡；批量只销最后那一下；没点过的照样回来。"""
    c = Checker()
    title("B. 副作用边界（销卡只认 from_card，且只在写成功之后）")

    from app.agent.pending import save_pending

    with sandbox():
        cli = make_client()

        # B1 手里挂着一张**别的**卡，面板手动加一条（带 session_id、不带 from_card）→ 不许误杀
        seed_todos([])
        sid = "t60-keep"
        save_pending(sid, [{"kind": "todo_remove", "todo_id": "whatever", "title": "挂在手里的卡"}])
        cli.post("/api/todos", json={"session_id": sid, "title": "面板手动加的一条",
                                     "date": FUTURE, "start": "09:00", "end": "10:00"}).json()
        back = _pending(cli, sid)
        c.check("B1 带 session_id 但不带 from_card 的写入**不许销卡**"
                "（误杀比这个 bug 更难受：他回头说「确认删除」，系统答「没有待确认的提案」）",
                back and back.get("title") == "挂在手里的卡", str(back)[:80])

        # B2 连 session_id 都不带（50 多个护栏文件都走这条）→ 照旧能写
        r = cli.post("/api/todos", json={"title": "没会话的写入", "date": FUTURE,
                                         "start": "11:00", "end": "12:00"}).json()
        c.check("B2 不带 session_id 的写入照旧成功（兜底不许被这次改动削掉）",
                r.get("ok") is True and len(_todos()) == 2, str(r)[:70])

        # B3 写**失败**的那次不许把卡销了（校验 400 / 404）
        sid4 = "t60-fail"
        save_pending(sid4, [{"kind": "todo_add", "title": "写不成的卡"}])
        bad = cli.post("/api/todos", json={"session_id": sid4, "from_card": True,
                                           "title": "缺日期", "date": "", "start": "", "end": ""})
        c.check("B3 校验失败（400）→ 卡还在：销卡只许发生在写成功之后，"
                "不然学生点了个寂寞、卡也回不来",
                bad.status_code == 400 and _pending(cli, sid4) is not None, str(bad.status_code))
        nf = cli.request("DELETE", "/api/todos/nosuch99",
                         json={"session_id": sid4, "from_card": True})
        c.check("B3-bis 404（那条根本不存在）→ 卡也**不许**被销",
                nf.status_code == 404 and _pending(cli, sid4) is not None, str(nf.status_code))

        # B4 原设计"刷新不丢卡"没被修坏
        sid5 = "t60-restore"
        save_pending(sid5, [{"kind": "todo_add", "title": "还没点的卡", "date": FUTURE,
                             "start": "15:00", "end": "16:00"}])
        c.check("B4 没点过的卡照样回得来（「刷新不丢卡」是原设计，不许一起修没）",
                _pending(cli, sid5) is not None, "")

        # B5 批量卡：中间几条不带 from_card，**最后一条**才销 —— 每条都带就会把批量删成一条
        seed_todos([{"title": "批量甲", "date": FUTURE, "start": "13:00", "end": "14:00"},
                    {"title": "批量乙", "date": FUTURE, "start": "14:00", "end": "15:00"}])
        a, b = _ids()[0], _ids()[1]
        sid6 = "t60-batch"
        save_pending(sid6, [{"kind": "todo_remove_batch",
                             "items": [{"todo_id": a}, {"todo_id": b}]}])
        ra = cli.request("DELETE", f"/api/todos/{a}", json={"session_id": sid6}).json()
        mid = _pending(cli, sid6)
        rb = cli.request("DELETE", f"/api/todos/{b}",
                         json={"session_id": sid6, "from_card": True}).json()
        c.check("B5 批量逐条执行：两条**都真删掉**（第二条不许被幂等判据当成「已经办过」而跳过）",
                ra.get("ok") is True and not ra.get("already") and rb.get("ok") is True
                and len(_todos()) == 0, f"剩 {len(_todos())} 条")
        c.check("B5-bis 中间那次（不带 flag）不许提前销卡，最后那一下才销整张卡",
                mid is not None and _pending(cli, sid6) is None, "")
    return c


def batch_d_shape():
    """D 批 · 形状：判据一份、顺序对、前端 flag 只挂在卡上、那句反了的注释改对。"""
    c = Checker()
    title("D. 形状与前端（判据只有一份；flag 只在卡上）")
    mn = open(SRC_MAIN, encoding="utf-8").read()
    stu = _strip(open(SRC_STU, encoding="utf-8").read())
    cfm = _strip(open(SRC_CFM, encoding="utf-8").read())

    c.check("幂等判据只有一份：`_card_close_guard` 定义 1 处、调用 3 处（POST/PUT/DELETE 各一次）",
            mn.count("def _card_close_guard(") == 1 and mn.count("_card_close_guard(sid, body)") == 3,
            f"调用 {mn.count('_card_close_guard(sid, body)')} 处")
    c.check("销卡只有一份：`_close_card` 定义 1 处、调用 4 处（PUT 有两个成功分支）",
            mn.count("def _close_card(") == 1 and mn.count("_close_card(sid, body)") == 4,
            f"调用 {mn.count('_close_card(sid, body)')} 处")
    c.check("全仓库真正打 applied 标记的只有 3 处：课表确认、批量加、以及 _close_card 这一处",
            mn.count("mark_applied(") == 3, f"实际 {mn.count('mark_applied(')} 处")

    for fn, write in (("todo_add", "add_todo("), ("todo_update", "set_todo_done_on("),
                      ("todo_delete", "delete_todo(")):
        seg = mn[mn.index("def " + fn + "("):]
        seg = seg[:seg.index("\n@app.")] if "\n@app." in seg else seg
        c.check(f"{fn}()：判据问在写之前（{write}）",
                seg.index("_card_close_guard(sid, body)") < seg.index(write), "")
        c.check(f"{fn}()：销卡在写之后（先销后写 = 写失败了卡也没了，学生点了个寂寞）",
                seg.index(write) < seg.index("_close_card(sid, body)"), "")

    c.check("学生端三处卡片确认按钮都带 from_card（加 / 完成 / 删除）",
            stu.count("from_card: true") == 3, f"实际 {stu.count('from_card: true')} 处")
    c.check("确认页四处单条卡带 from_card，两个批量循环只在**最后一条**带",
            cfm.count("from_card: true") == 4 and cfm.count("from_card: i === items.length - 1") == 2,
            f"单条 {cfm.count('from_card: true')} / 批量 {cfm.count('from_card: i === items.length - 1')}")

    # 代办栏那两个快捷按钮（勾选=PUT、删除=DELETE）**不是卡**：带上去就会误杀挂着的提案。
    # ⚠️ 逐行取、逐行剔注释：整段切片当窗口会两头绊 —— 一是被上面别的注释切掉锚点，
    #    二是把隔壁卡片代码里的 from_card 一起数进来（本仓"注释绊红"的老形状）。
    lines = open(SRC_STU, encoding="utf-8").read().splitlines()
    panel_writes = [ln.split("//")[0] for ln in lines
                    if 'method:"PUT"' in ln and "/api/todos/" in ln
                    or 'method:"DELETE"' in ln and "/api/todos/" in ln]
    c.check("代办栏的勾选/删除那两处**不带** from_card（它们不是卡）——"
            "取不到那两行也算红：说明写法被改了，判据得跟着重写",
            len(panel_writes) == 2 and all("from_card" not in ln for ln in panel_writes),
            f"找到 {len(panel_writes)} 行")

    c.check("收到 already 时当成功处理、把后端那句话原样说出来（学生端三处卡片 + 一处父页面 = 4）",
            stu.count("d.already") == 4 and "r.already" in cfm,
            f"学生端 {stu.count('d.already')} 处")
    c.check("already 那一支排在**所有类型分支之前**"
            "（否则父页面会照着 kind 再报一次「已加入日程」，把「其实没新写入」盖掉）",
            'if (d.kind === "custom_record")' in stu
            and stu.index("if (d.already) {") < stu.index('if (d.kind === "custom_record")'), "")

    # §3.4 那句因果反了的注释：这条比的是**注释正文**，所以一律用原文（剔完注释再比就恒红了）
    stu_raw = open(SRC_STU, encoding="utf-8").read()
    c.check("旧那句「点过的不会再回来，写库路径一行都没碰」已经删掉（留着它下一轮还会有人信）",
            "写库路径一行都没碰" not in stu_raw, "")
    c.check("换成了带前提的正确说法：判重成立的条件是「写库那条路必须销卡」，并指到本文件",
            "写库那条路必须销卡" in stu_raw and "test_60_card_close" in stu_raw, "")
    return c


def main():
    print("\n" + "=" * 60)
    print("test_60_card_close：点卡确认写库必须销卡（#33 重复写入）")
    print("=" * 60)
    code = 0
    code |= batch_a_three_entrances().summary("A 批（三个入口各销一次）")
    code |= batch_b_no_collateral().summary("B 批（不误杀 / 写失败不销 / 批量只销最后一条）")
    code |= batch_d_shape().summary("D 批（形状：判据一份、flag 只在卡上、注释改口）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
