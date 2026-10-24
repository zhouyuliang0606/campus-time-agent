# -*- coding: utf-8 -*-
"""#59 的护栏：暂存里的「候选清单」不许被当成卡恢复出来。

现场（施工单 §〇，八条前提我逐条复算过，七条对、一条不完整 —— 见下）：
学生说「删待办」→ 命中好几条 → 系统列清单并把清单记进**暂存**；学生回「1」→ 出删除提案，
此时 `pending_out = [提案, 候选清单]`（**辅助项排在最后**），但**响应**只回 `options:[提案]`
⇒ 刷新前一切正常。刷新后走 `/api/pending/{sid}`，那里写的是 `opts[-1]` ——
取到的正是那张**不是卡**的候选清单 → 前端没有这个 kind 的分支 → 掉进「候选时间段」兜底循环
→ 按 `{date,start,end,title}` 拼出三个 `undefined` 的勾选卡 → 点【确认所选】发出
`确认 undefined undefined undefined 待办`。

施工单 §〇 七条我逐条复算过：**六条对**（行号、两份 kind 的形状、前端 grep 0 命中、兜底循环是乱码制造者都核上了），
第 7 条（后端把这句话当点头）在真机那一步核。本单相对单据的三处不同，都有实测背书：

1. **范围**：单据只点了 `todo_remove_candidates`，整族扫下来 `todo_done_candidates`（标记完成那一族）同形同病
   ⇒ 排除表两份都在（V2 = 只留一份，B5/B6 当场红）。
2. **§二 那一版不能用**：单据写的是「带 kind 就跳过」，实测会把 `todo_pick` 与 `todo_remove` 提案一起判成
   "画不出来"（`todo_pick` **只有**这个循环会画，刷新后靠它才能勾）⇒ 改成按**时间形状**判；
   证据 = 变异 V3（装上单据那一版）在 C 批与 A6 上红。
3. **引用的那句原文不对**：§〇.6 抄成 `确认 undefined undefined undefined 待办`，
   模板是 `${o.start}-${o.end}`，实测送出的是 `确认 undefined undefined-undefined 待办`（C7 钉的是跑出来的那串）。

另记两条**读出来的**事实，别靠猜：
- 画卡那行外面有 `esc()` ⇒ 它**不印**字面 undefined，印的是一张三个字段全空、标题兜成「待办」的卡（C6）；
  字面 undefined 只出现在**发送**那一行（没有 esc，C7）。所以「乱码卡」和「乱码消息」是两个不同的成因。
- 提案的 `date` 可以是空串（库里那条待办本来没日子），所以"是不是卡"**不能**用有没有时间来判 ——
  B2 钉的是形状字段（清单带 `todos`、卡带 `summary`），时间形状只归前端那道闸管。

三批：
  A 形状与边界（读源码，钉"辅助项还在暂存里""两处消费者仍从整表找""兜底判据是时间形状"）；
  B 行为（TestClient 走**真实两回合**造出截图那个形状，再打 `/api/pending`）；
  C 前端兜底那句判据（node 真跑从 student.html 抠出来的那一行，不是复刻一份）。
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import PROJ_DIR, Checker, make_client, sandbox, seed_todos, title  # noqa: E402

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
STUDENT = os.path.join(PROJ_DIR, "app", "static", "student.html")
ESC_JS = os.path.join(PROJ_DIR, "app", "static", "esc.js")
PENDING_PY = os.path.join(PROJ_DIR, "app", "agent", "pending.py")

# 三条同名字体的待办：让「删待办」一定命中多条（清单就是这么来的）
SEED = [
    {"title": "复习数学", "date": "", "start": "14:00", "end": "15:00"},
    {"title": "复习英语", "date": "", "start": "15:00", "end": "16:00"},
    {"title": "复习语文", "date": "", "start": "16:00", "end": "17:00"},
]


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:200]}"
    return r.json()


def _pending_of(client, sid):
    return client.get(f"/api/pending/{sid}").json()


def _pending_file_kinds():
    """直接核账本：返回 {会话键: [kind, ...]}（B6-bis 要用它证明辅助项**还在**）。"""
    p = os.path.join(os.environ["CAMPUSTIME_DATA_DIR"], "pending.json")
    data = json.load(open(p, encoding="utf-8"))
    out = {}
    for key, entry in (data or {}).items():
        if isinstance(entry, dict):
            out[key] = [o.get("kind") for o in (entry.get("options") or []) if isinstance(o, dict)]
    return out


def two_turn(sid):
    """真实两回合：「删待办」→「1」，返回 (第一回合, 第二回合, 回合1后的暂存, 回合2后的暂存, client)。

    为什么要走真的路由而不是手摆 dict 进暂存：这张单的根就是**响应与暂存分叉**，
    手摆只能证明"我写的形状会被取错"，走真的路由才能证明"产品真的会写出那个形状"。
    """
    client = make_client()
    seed_todos(SEED)
    d1 = _post(client, "/api/chat", {"message": "删除复习", "session_id": sid, "module": "planner"})
    p1 = _pending_of(client, sid)
    d2 = _post(client, "/api/chat", {"message": "1", "session_id": sid, "module": "planner"})
    p2 = _pending_of(client, sid)
    return d1, d2, p1, p2, client


def batch_a_shape():
    c = Checker()
    title("A. 形状与边界：辅助项留在暂存里，但不许被选中当卡")
    src = open(MAIN, encoding="utf-8").read()
    stu = open(STUDENT, encoding="utf-8").read()

    c.check("A1 `pending_get` 不再裸取 `opts[-1]`（位置约定正是这一单的根）",
            "cards[-1]" in src and "return {\"pending\": opts[-1], \"session_id\": sid}" not in src)
    c.check("A2 排除表**整族覆盖**：删待办与标记完成两份候选清单都在（施工单只点了一个）",
            re.search(r"todo_remove_candidates[^\n]*\n?.*todo_done_candidates", src) is not None
            or (src.count('"todo_remove_candidates"') >= 1 and src.count('"todo_done_candidates"') >= 1
                and re.search(r"_PENDING_AUX_KINDS\s*=\s*.{0,120}todo_remove_candidates.{0,120}todo_done_candidates",
                              src, re.S) is not None))
    c.check("A3 两个 family 的候选清单**都还在往暂存里写**（§五.1：排除不是删除）—— "
            "这里故意不用「恰好三处」那种计数式钉子（它自己会撞自己），"
            "「确实写进去了、而且留在账上」由 B0-a / B3 / B6-bis 三条行为断言钉",
            src.count('{"kind": "todo_remove_candidates"') >= 1
            and src.count('{"kind": "todo_done_candidates"') >= 1)
    c.check("A4 那份 :3723 的 [提案, 清单] 追加顺序还在（本单不动写入侧，只动取卡）",
            "pending_out = [proposal, {\"kind\": \"todo_remove_candidates\"" in src)
    c.check("A5 两个消费者仍从**整表**找 kind（不是跟着改成找 [-1]）",
            'for o in entry.get("options") or []:' in src
            and src.count('o.get("kind") == "todo_remove_candidates"') == 1
            and src.count('o.get("kind") == "todo_done_candidates"') == 1)
    # 前端：兜底循环的判据必须是"能不能画"，不是"有没有 kind"
    c.check("A6 兜底循环只收有时间形状的 option（按 kind 一刀切会杀掉 todo_pick —— 那是回归）",
            re.search(r"const drawable = options\.filter\(o => o && o\.date && o\.start && o\.end\);", stu) is not None)
    c.check("A6-bis 而且循环遍历的确实是那份过滤结果（抓来跑的那行 = 真在跑的那行）",
            "drawable.forEach(" in stu and "options.forEach(o => {\n        const card = document.createElement(\"label\");"
            not in stu)
    guard = 'if (!drawable.length) {'
    btn = 'const btn = document.createElement("button");'
    i_guard = stu.find(guard)
    # ⚠️ 从**闸的位置往后**找那颗按钮：`const btn = document.createElement("button");` 全页有十几处，
    # 从 0 开始 find 到的是别的分支那颗，比对出来的先后关系是假的（第一版就是这么红的）。
    i_btn = stu.find(btn, i_guard)
    c.check("A7 画不出来时说人话，并且**不摆**【确认所选】（那道闸必须赶在这颗按钮之前 return）",
            0 < i_guard < i_btn and "return;" in stu[i_guard:i_btn]
            and "确认所选" in stu[i_btn:i_btn + 260],
            f"闸在第 {i_guard}，其后第一颗按钮在第 {i_btn}")
    c.check("A8 还原入口在 `pending` 为空时静默返回（不许冒一句『这张卡还挂着』却底下没卡）",
            "if (!p || !p.kind) return;" in stu)
    c.check("A9 本单没伸手：确认 iframe 那条路、`save_pending` 的覆盖语义、TTL 与身份键都没动",
            "def save_pending(session_id: str, options: list) -> None:" in open(PENDING_PY, encoding="utf-8").read()
            and "_TTL = 3 * 3600" in open(PENDING_PY, encoding="utf-8").read()
            and '"/api/pending/{sid}"' in src)
    return c


def batch_b_endpoint():
    c = Checker()
    title("B. 行为：TestClient 走真实两回合，再打恢复接口")
    with sandbox():
        d1, d2, p1, p2, client = two_turn("s59-repro")
        opts1 = (p1.get("pending") or {})
        opts2 = (p2.get("pending") or {})

        # 先把"产品确实写出了那个分叉"钉住 —— 不然下面几条是在测一个不存在的现场
        c.check("B0-a 第一回合确实回『删待办缺细节』并把候选清单记进暂存（响应里没有卡）",
                ("对上了好几条" in (d1.get("answer") or "") or "没太看明白" in (d1.get("answer") or ""))
                and not (d1.get("options") or []),
                f"answer={str(d1.get('answer'))[:46]} options={len(d1.get('options') or [])}")
        c.check("B0-b 第二回合：响应只回一张提案，暂存里却是 [提案, 清单]（这一条就是根）",
                len(d2.get("options") or []) == 1
                and (d2.get("options") or [{}])[0].get("kind") == "todo_remove",
                f"resp options={[o.get('kind') for o in (d2.get('options') or [])]}")

        c.check("B1 刷新后恢复出来的是**那张删除提案**，不是候选清单（改前正是这里红：拿到 todo_remove_candidates）",
                opts2.get("kind") == "todo_remove", f"实际拿到 kind={opts2.get('kind')}")
        c.check("B2 恢复接口交出去的必须是一张**卡**（有 summary/title，且没有清单那份特有的 `todos` 字段）"
                "—— 「有时间形状」这条不能当判据：库里那条待办没日子的话，提案的 date 本来就是空串",
                "todos" not in opts2 and bool(opts2.get("summary") or opts2.get("title")),
                f"拿到的 keys={sorted(opts2.keys())}")
        c.check("B3 只挂着候选清单时（还没点名那一档）恢复接口回 None —— 前端宁可不画也不画错",
                not opts1 or opts1.get("kind") != "todo_remove_candidates",
                f"回合1后 pending kind={opts1.get('kind')}")

        # 反向对照：排除法不许误伤真卡
        from app.agent.pending import save_pending
        pick = {"kind": "todo_pick", "title": "复习数学", "date": "2026-10-26",
                "start": "14:00", "end": "15:00", "summary": "复习数学｜14:00-15:00"}
        save_pending("s59-pick", [pick])
        got = _pending_of(client, "s59-pick").get("pending") or {}
        c.check("B4 对照：`todo_pick` 是真卡，必须照旧取得到（辅助项排除法把它挡住就是回归）",
                got.get("kind") == "todo_pick", f"得到 {got.get('kind')}")

        done_cand = {"kind": "todo_done_candidates", "todos": [{"title": "复习数学"}], "summary": "待标记候选"}
        save_pending("s59-done", [done_cand])
        c.check("B5 整族：标记完成那份候选清单同样不许被当卡取出来（施工单没点这一处）",
                not (_pending_of(client, "s59-done").get("pending") or {}))

        mixed = {"kind": "todo_remove", "title": "复习英语", "date": "2026-10-27",
                 "start": "15:00", "end": "16:00", "summary": "复习英语｜15:00-16:00"}
        save_pending("s59-mix", [mixed, done_cand])
        got2 = _pending_of(client, "s59-mix").get("pending") or {}
        kinds = [v for v in _pending_file_kinds().values() if "todo_remove" in v]
        c.check("B6 [真卡, 辅助项] 混着挂时取到真卡（不是清单）",
                got2.get("kind") == "todo_remove", f"得到 {got2.get('kind')}")
        c.check("B6-bis 直接核账本：那张辅助项**确实还留在暂存里**（排除 ≠ 删除，差一个字就修反了）",
                any("todo_done_candidates" in v for v in kinds), f"账本里带 todo_remove 的键={kinds}")

        # 刷新后再说「第二条」仍接得住 —— 这才是"辅助项留在暂存里"的唯一理由
        d3 = _post(client, "/api/chat", {"message": "第二条", "session_id": "s59-repro", "module": "planner"})
        got3 = [g or {} for g in (d3.get("options") or [])]
        c.check("B7 两回合之后回「第二条」仍能出新提案（删了辅助项就断在这一步）",
                any(g.get("kind") == "todo_remove" for g in got3),
                f"answer={str(d3.get('answer'))[:50]} options={[g.get('kind') for g in got3]}")
    return c


def _slice_stmt(src, head):
    """从页面里抠出**一整行**语句（以分号结尾）。抠不到就直接抛，不许静默返回空。"""
    m = re.search(re.escape(head) + r".*?;", src, re.S)
    if not m:
        raise AssertionError("那句判据不在页面里（或换了写法）：" + head)
    return m.group(0) + "\n"


def batch_c_frontend():
    c = Checker()
    title("C. 前端兜底那句判据：node 真跑 student.html 里那一行")
    if not shutil.which("node"):
        c.check("C0 本机没有 node —— 这一批只能靠 node 跑，不许退回读源码判语义", False)
        return c
    src = open(STUDENT, encoding="utf-8").read()
    try:
        line = _slice_stmt(src, "const drawable = options.filter(")
    except AssertionError as e:      # 改前就该红在这里（红，不是崩 —— 崩了后面几批看不到数）
        c.check("C0 页面里有那句「能不能画」的判据（没有 = 本单还没落地）", False, str(e)[:120])
        return c
    # 跑**页面自己那一行**，喂真 payload；断言的是"哪几条被收进兜底循环"。
    js = ("const f = (options) => { " + line + " return drawable.map(o => o.kind || '(无kind)'); };"
          "process.stdout.write(JSON.stringify(f(JSON.parse(process.argv[1]))));")
    payloads = [
        ("候选清单（remove）", [{"kind": "todo_remove_candidates", "todos": [{"title": "复习数学"}], "summary": "待删候选"}]),
        ("候选清单（done）", [{"kind": "todo_done_candidates", "todos": [{"title": "复习数学"}], "summary": "待标记候选"}]),
        ("todo_pick 真卡", [{"kind": "todo_pick", "title": "复习数学", "date": "2026-10-26",
                          "start": "14:00", "end": "15:00"}]),
        ("todo_remove 提案", [{"kind": "todo_remove", "title": "复习数学", "date": "2026-10-26",
                            "start": "14:00", "end": "15:00"}]),
        ("没有 kind 的老形状", [{"title": "复习数学", "date": "2026-10-26", "start": "14:00", "end": "15:00"}]),
    ]
    for name, payload in payloads:
        p = subprocess.run(["node", "-e", js, json.dumps(payload, ensure_ascii=False)],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        if p.returncode != 0:
            c.check(f"C {name}：node 跑挂了", False, (p.stderr or "")[:200])
            continue
        got = json.loads(p.stdout)
        want_drawable = not name.startswith("候选清单")
        c.check(f"C 兜底循环{'应该' if want_drawable else '不该'}画这一条：{name}"
                f"（{'画得出来，有时间形状' if want_drawable else '画不出来 = 三个 undefined'}）",
                (len(got) == 1) == want_drawable, f"drawable={got}")

    # C6/C7：闸挡掉的到底是什么东西 —— 用**页面里那两行原文**跑一遍辅助项，各钉一件事。
    #   C6 画的那行外面有 esc()，所以它**不印**字面 undefined（esc 把 undefined 变成空串），
    #      它印出的是「<b>待办</b>（） - · 约 ? 分钟」—— 三个字段全空、标题靠兜底撑着的一张卡，
    #      学生截图里那张"乱码卡"就是这个形状（实测出来的，不是我推测的）。
    #   C7 点【确认所选】发出去的那句才是字面 `确认 undefined undefined undefined 待办` ——
    #      那一行没有 esc()，模板把 undefined 原样拼了进去，后端只能回"没找到符合条件的"。
    #   两行都留在页面里（它们没错，错的是"什么都往这里送"），所以这两条钉的是**为什么需要这道闸**。
    tpl = _slice_stmt(src, "`<b>${esc(o.title || \"待办\")}</b>`")
    msg_line = _slice_stmt(src, "const msg = picked.map(")
    esc_src = open(ESC_JS, encoding="utf-8").read()
    aux = {"kind": "todo_remove_candidates", "todos": [{"title": "复习数学"}], "summary": "待删候选"}
    head = "const window={};\n" + esc_src + "\nglobalThis.esc=window.esc;\nconst o = JSON.parse(process.argv[1]);\n"
    p2 = subprocess.run(["node", "-e", head + "process.stdout.write(String(" + tpl.rstrip().rstrip(";") + "));",
                         json.dumps(aux, ensure_ascii=False)],
                        capture_output=True, text=True, encoding="utf-8", timeout=30)
    drawn = p2.stdout or ""
    c.check("C6 那行模板碰到辅助项会画出一张「什么信息都没有」的卡（标题兜成‘待办’、日期时间全空）",
            p2.returncode == 0 and "<b>待办</b>" in drawn and "约 ? 分钟" in drawn
            and "复习数学" not in drawn and "14:00" not in drawn, drawn[:150])

    p3 = subprocess.run(["node", "-e",
                         "const picked=[JSON.parse(process.argv[1])];\n" + msg_line +
                         "process.stdout.write(msg);",
                         json.dumps(aux, ensure_ascii=False)],
                        capture_output=True, text=True, encoding="utf-8", timeout=30)
    sent = p3.stdout or ""
    # ⚠️ 单据 §〇.6 把这句抄成「确认 undefined undefined undefined 待办」（三个空格分开）；
    #    模板是 `${o.start}-${o.end}`，所以**实测**是 `确认 undefined undefined-undefined 待办`。
    #    差一个连字符不影响结论（照样是"没找到"），但钉断言只能钉跑出来的那一串，不能钉引用。
    c.check("C7 点【确认所选】发出去的那句确实是「确认 undefined undefined-undefined 待办」"
            "（那一行没有 esc，undefined 原样进消息）",
            p3.returncode == 0 and sent.strip() == "确认 undefined undefined-undefined 待办", repr(sent[:80]))
    return c


def main():
    global code
    print("test_80_pending_aux_not_a_card：#59 暂存里的候选清单被当成卡恢复出来")
    print("=" * 60)
    rc = 0
    for name, fn in (("A 批（形状与边界）", batch_a_shape),
                     ("B 批（TestClient 真实两回合）", batch_b_endpoint),
                     ("C 批（node 跑页面那句判据）", batch_c_frontend)):
        # ⚠️ 用 max() 不用 `or`：`or` 会在第一批变红时短路，后面几批根本不跑（#55 踩过）
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
