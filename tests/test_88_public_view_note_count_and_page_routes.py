"""#66 追记的护栏：`public_view` 别把说明行当条目数；页面那两路也要滤缺 id。

批次分工（变异跑法看「红的是哪几条名字」）：

  A 批 · 计数：`{"info": …}` 是**一句话**，不是一件事 —— 不许算进「共 N 条」，
      也不许占掉「挑三项」那三个位置。`list_todos_range` 是把 notes 摆在**最前面**的，
      所以第二个坑（说明句把真条目挤出前三）比第一个坑更容易在真机上看见。
  B 批 · 页面两路：`GET /api/todos` 与 `GET /api/todos/month` 与工具那两路**同一个判据**，
      缺 `id` 的不往外交、条数说在 `dropped` 里。每条都配一份「补上 id 就该出现」的正控，
      否则「永远不返回」也能让断言全绿。
  C 批 · 文案：`#64` 那句「这一页存的是原始轨迹」是**反的**（`engine.py:229` 写的就是
      `public_view` 那份）。这里钉的不是「文案好看」，是「不许写回去」。

⚠️ 这批的两条自省（2026-10-26 定）：
  1. **期望为空必须配非空正控**（A5 / B3）。只断言「不含某字样」的检查，在一个永远返回空串的
     实现上照样全绿 —— 那是最坏的一种通过。
  2. **判据要打在源头**：A 批直接喂 `public_view` 的入参，不去渲染层抓字符串；
     B 批直接调路由函数，不去起服务再解析 —— 起服务会让这一批从 2 秒变 20 秒，
     而它测的东西跟 HTTP 无关。
"""
import asyncio
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, _require_sandbox, data_path, sandbox,  # noqa: E402
                      title)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DAY = "2026-10-26"          # 固定日子：断言与墙上时钟无关
MONTH = "2026-10"
NOTE = "另有 1 条待办没有 id，没列进来（数据可能被手改过）"


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def seed_raw(rows):
    """把**沙箱里**的 `todos.json` 覆盖成原样记录（故意缺键就得用它，见 test_87 同名 helper）。"""
    dest = _require_sandbox("seed_raw()")
    p = os.path.join(dest, "student", "todos.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"todos": rows}, f, ensure_ascii=False)
    return rows


def good(**over):
    row = {"id": "k1", "title": "做题", "date": DAY, "start": "14:00", "end": "16:00",
           "status": "planned", "note": "", "category": "", "created_at": "2026-01-01 00:00:00"}
    row.update(over)
    return row


def pv(result, tool="list_todos_range"):
    from app.agent.public_view import public_view
    try:
        return public_view(tool, result), ""
    except Exception as e:                        # noqa: BLE001
        return "", "%s: %s" % (type(e).__name__, e)


def route(fn, *a, **kw):
    """调一个 async 路由函数（不起服务：这批测的不是 HTTP）。"""
    try:
        return asyncio.run(fn(*a, **kw)), ""
    except Exception as e:                        # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, e)


# ── A 批 · 计数 ──────────────────────────────────────────────────────────────
def batch_a_count():
    c = Checker()
    title("A. 计数：说明行不是一件事，也不许占掉「挑三项」的位置")

    txt, exc = pv([{"id": "k1", "title": "做题", "date": DAY, "start": "14:00",
                    "end": "16:00", "status": "planned"}, {"info": NOTE}])
    c.check("A1 一条真条目 + 一句说明 ⇒ 念「共 1 条」，不是「共 2 条」",
            "共 1 条" in txt and "共 2 条" not in txt, repr(txt[:90]) + " " + exc)
    c.check("A1-bis 那句说明**还在**（滤掉计数不等于把话吞掉 —— 学生得知道少了一条）",
            "没有 id" in txt, repr(txt[:120]))

    # `list_todos_range` 的实际形状：notes 在**最前面**（`planner.py:1436` `return notes + order`）。
    # ⚠️ 样本必须让两种坏写法**不同解**（#64 那条教训）：原来只喂「1 句说明 + 2 条条目」，
    # 那时 `obj[:3]` 与 `items[:3]` 取到的是同一批 ⇒ V8 那笔变异（挑三项退回 `obj[:3]`）一条都红不出来。
    rows2 = [{"id": "k%d" % n, "title": "第%d件" % n, "date": DAY,
              "start": "%02d:00" % n, "end": "%02d:40" % n, "status": "planned"}
             for n in (1, 2, 3, 4)]
    txt2, exc2 = pv([{"info": NOTE}] + rows2)
    c.check("A2 notes 排在最前时，「挑三项」取的必须是**条目前三**（第四件该被挤出去 —— "
            "说明句一占位，真条目就少一句）",
            all(("第%d件" % n) in txt2 for n in (1, 2, 3)) and "第4件" not in txt2,
            repr(txt2[:160]) + " " + exc2)
    c.check("A2-bis 计数只数条目（四件 ⇒ 「共 4 条」，说明行不算），且那句说明仍挂在尾巴上",
            "共 4 条" in txt2 and "没有 id" in txt2, repr(txt2[:80]))

    txt3, exc3 = pv([{"info": NOTE}])
    c.check("A3 整份返回只有说明行 ⇒ 直接念说明，**不许**念成「共 0 条」再补一句"
            "（那读起来像「查到了，但你看不见」）",
            "共 0 条" not in txt3 and "没有 id" in txt3, repr(txt3[:90]) + " " + exc3)

    txt4, exc4 = pv([{"id": "k1", "title": "做题", "info": "自带一句说明的条目"}])
    c.check("A4 带 `info` **但还有别的键**的行仍算一件东西（`_is_note` 只认「只有 info」的纯说明行）",
            "共 1 条" in txt4, repr(txt4[:90]) + " " + exc4)

    from app.agent.public_view import _is_note
    c.check("A5 判据本身：纯说明行 True / 带 id 的行 False（**正控**：只测 True 那一半，"
            "把 `_is_note` 写成 `return True` 也能全绿）",
            _is_note({"info": "x"}) is True and _is_note({"info": "x", "id": "k1"}) is False
            and _is_note({"title": "做题"}) is False and _is_note("x") is False,
            "%s/%s/%s/%s" % (_is_note({"info": "x"}), _is_note({"info": "x", "id": "k1"}),
                             _is_note({"title": "做题"}), _is_note("x")))

    txt6, _ = pv([])
    c.check("A6 空列表仍是「查下来是空的」（既有行为没被这次改动碰坏）",
            txt6 == "查下来是空的", repr(txt6))
    return c


# ── B 批 · 页面两路 ──────────────────────────────────────────────────────────
def batch_b_routes():
    c = Checker()
    title("B. 页面两路：缺 id 的不往外交，条数说在 dropped 里")

    from app.main import todos_list, todos_month

    with sandbox():
        seed_raw([good(id="k1", title="做题"), good(id="k2", title="背书")])
        ok, exc0 = route(todos_list, None)
        c.check("B0 正控：两条都带 id 时两条都在、`dropped` 是 0（不然 B1 的「少了一条」可能一直是少的）",
                bool(ok) and len(ok.get("todos") or []) == 2 and ok.get("dropped") == 0,
                "%s %s" % (ok, exc0))

        seed_raw([good(id="k1", title="做题"),
                  {"title": "手改出来的野记录", "date": DAY, "start": "10:00", "end": "11:00",
                   "status": "planned"}])       # ← 缺 id
        got, exc = route(todos_list, None)
        titles = [t.get("title") for t in (got or {}).get("todos") or []]
        c.check("B1 `GET /api/todos` 缺 id 的那条**不返回**（前端拿它当操作句柄：缺了会变成 "
                "`id=undefined` 打回来，删错或删不掉）",
                titles == ["做题"], "%s %s" % (titles, exc))
        c.check("B1-bis 条数如实说在 `dropped` 里（少一条但不许没人知道）",
                (got or {}).get("dropped") == 1, repr((got or {}).get("dropped")))

        mon, exc2 = route(todos_month, MONTH)
        day_rows = ((mon or {}).get("days") or {}).get(DAY) or []
        c.check("B2 `GET /api/todos/month` 缺 id 的也不进格子、`dropped` 同样计数",
                len(day_rows) == 1 and (mon or {}).get("dropped") == 1,
                "%d 行 / dropped=%s / %s" % (len(day_rows), (mon or {}).get("dropped"), exc2))

        seed_raw([{"title": "全都不合规", "date": DAY, "start": "10:00", "end": "11:00"}])
        mon2, exc3 = route(todos_month, MONTH)
        c.check("B2-bis 某天全被滤空时那一天**从表里去掉**（留一个空列表会让格子显示「有 0 项」这种自相矛盾）",
                DAY not in ((mon2 or {}).get("days") or {}) and (mon2 or {}).get("dropped") == 1,
                "%s %s" % ((mon2 or {}).get("days"), exc3))

        seed_raw([good(id="k9", title="补上 id 的野记录")])
        again, exc4 = route(todos_list, None)
        c.check("B3 补上 id 之后立刻出现（**正控**：证明「不返回」是判据生效，不是这一路永远空）",
                [t.get("title") for t in (again or {}).get("todos") or []] == ["补上 id 的野记录"]
                and (again or {}).get("dropped") == 0,
                "%s %s" % (again, exc4))

    c.check("B4 两路的判据是**同一个** `_todo_has_id`（在 main 里各写一遍 `t.get(\"id\")` 就会漂）",
            "from app.modules.planner import" in read("app/main.py")
            and read("app/main.py").count("_todo_has_id(") >= 2,
            str(read("app/main.py").count("_todo_has_id(")))
    return c


# ── C 批 · 文案 ──────────────────────────────────────────────────────────────
def batch_c_wording():
    c = Checker()
    title("C. 文案：#64 那句「存的是原始轨迹」是反的，不许写回去")

    admin = read("app/static/admin.html")
    c.check("C1 管理端那句「这一页存的是原始轨迹」已经不在（`engine.py:229` 写的就是 "
            "`public_view` 那份，原文那句话方向反了）",
            "这一页存的是原始轨迹" not in admin, "已存在" if "这一页存的是原始轨迹" in admin else "")
    c.check("C2 改口之后**说清了依据**（指向 `engine.py:229` 那次 `public_view` 调用，"
            "而不是只说一句「不是二手」）",
            "public_view" in admin and "engine.py:229" in admin, "")
    c.check("C3 也说明了原文在哪儿能看到（`DEBUG` + `engine.py:221`，不落这本账）",
            "DEBUG" in admin and "engine.py:221" in admin, "")

    snap = read("ai-prompts/2026-10-25-ticket64-admin-workflow-trace.md")
    body = [ln.strip() for ln in snap.split("\n") if ln.strip().startswith("- 存的是")]
    c.check("C4 快照 §一 那条**正文**已改口（原话只许出现在 ⚠️ 更正块里当引文 —— "
            "已提交的快照不改结论、要改就明写更正，别让它看起来像一直都对）",
            len(body) == 1 and "`public_view` 收过的那份" in body[0]
            and "原始 trace" not in body[0] and "2026-10-26 更正" in snap, str(body)[:140])

    t85 = read("tests/test_85_trace_log_admin_workflow.py")
    c.check("C5 `test_85` B5 的标题不再宣称「存的是原始那条链」（它的判据只查「判定行在 + 至少 2 步」，"
            "从来没查过工具结果是原文）",
            "存的是**原始**那条链" not in t85 and "**不**钉「工具结果是原文」" in t85, "")
    return c


def main():
    print("test_88_public_view_note_count_and_page_routes：#66 追记 —— 说明行不算条目 / 页面两路滤缺 id")
    print("=" * 60)
    code = 0
    for label, fn in (("A 批（计数）", batch_a_count),
                      ("B 批（页面两路）", batch_b_routes),
                      ("C 批（文案）", batch_c_wording)):
        try:
            rc = fn().summary(label)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[%s] 崩了：%s" % (label, e))
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
