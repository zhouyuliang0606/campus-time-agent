"""#66 的护栏：待办对外形状缺键时怎么办 —— 读侧兜底，但 `id` 缺了整条不出。

批次分工（编号在标题里，变异跑法要看"红的是哪几条名字"）：

  A 形状（静态）：**这一族只此一处翻译** —— 两个工具函数里对记录 `t` 的读下标必须为 0、
      六键字典字面量只许出现在 `_todo_public_row` 里、两个 helper 各只有一个定义、
      两处 `date` 的语义**故意不同**（锚点日 / 落到的那天），不许被"顺手统一"。
  B 行为 · 缺键的降级：缺 `status`/`start`/`end`/`title` 的记录照常被列出来，且值是 `""`；
      `status` 缺 ⇒ 按"没完成"算（方向是安全的那一边）。每条都配一份补全后的正控。
  C 行为 · 缺 `id`：这条记录不出现在返回里，并且**要明说丢了几条**；
      `list_todos_range` 里两条缺 id 的**不许并成一行**（那是 A 的唯一例外的真正代价）。
  D 边界：这两类行怎么念给学生/模型；缺 `date` 的记录走不到翻译那一步（既有行为）；
      这批测试不脏真实数据目录，且"覆盖写"的 helper 在沙箱外必须被拒。

⚠️ 两个反"自己骗自己"的设计：
  1. **每次调用都成对拿 `(行, 异常标记)`**（`call_rows`）。缺键的输入正是"会抛 KeyError"的那种，
     放任它把整批打崩，红名单是空的 —— 崩只说明批次没了、不说明判据有牙（#64 的 Y11 那一课）。
     异常折成一个字符串交给断言判 ⇒ 红了还说得出是哪一个键缺的。
  2. `test_84` 的 B6 判的是「返回行的**键集合** ⊇ 那六个」—— 把翻译退回手写六键字典（键一模一样）
     它照旧绿。所以 A 批钉的是「翻译只有一份**实现**」（位置判据）。
     ⇒ 同一件事在两处各钉一遍 = 两份会互相打脸的账；这里要的是 B6 挡不住的那一半。
"""
import ast
import hashlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, _require_sandbox, data_path, sandbox,  # noqa: E402
                      title)

PLANNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "modules", "planner.py")
SIX = ("id", "title", "date", "start", "end", "status")
DAY = "2026-10-26"          # 固定日子：这批断言与墙上时钟无关（数据是自种的）


class _Del:
    pass


_DEL = _Del()


def read(path):
    return io.open(path, encoding="utf-8").read().replace("\r\n", "\n")


def func_of(src, name):
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def seed_raw(rows):
    """把**沙箱里**的 `todos.json` 覆盖成这批**原样**记录（故意缺键就得用它）。

    为什么不用 `seed_todos`：那个 helper 按 `add_todo` 的形状把缺的键补齐 —— 用它永远种不出
    "缺 status 的记录"，本单的行为断言会全部走不到（= 假绿）。
    ⚠️ 它同样是整份覆盖写 ⇒ 必须过 `_require_sandbox`：漏在沙箱外调用就把用户真在用的库清了。
    """
    dest = _require_sandbox("seed_raw()")
    p = os.path.join(dest, "student", "todos.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"todos": rows}, f, ensure_ascii=False)
    return rows


def good(**over):
    row = {"id": "k1", "title": "做题", "date": DAY, "start": "14:00", "end": "16:00",
           "status": "planned", "note": "", "category": "", "created_at": "2026-01-01 00:00:00"}
    for k, v in over.items():
        if v is _DEL:
            row.pop(k, None)
        else:
            row[k] = v
    return row


def call_rows(fn):
    """跑一次取行，返回 `(行, 异常标记)` —— **不许让异常把整批打崩**（见文件开头 1.）。"""
    try:
        return fn() or [], ""
    except Exception as e:                        # noqa: BLE001 —— 判据要的就是"它抛不抛"
        return [], "%s: %s" % (type(e).__name__, e)


def items_of(rows):
    """返回里那些**真是待办**的行（`{"info": …}` 那种说明行滤掉，`test_84` 也这么滤）。"""
    return [r for r in rows if isinstance(r, dict) and "info" not in r]


def infos_of(rows):
    return [str(r.get("info") or "") for r in rows if isinstance(r, dict) and "info" in r]


def one_of(rows):
    """第一条待办行（没有就给个空 dict，让断言自己红，别让探针 IndexError 崩掉整批）。"""
    return (items_of(rows) or [{}])[0]


def why(exc):
    return "异常=%s" % (exc or "无")


def batch_a_shape():
    c = Checker()
    title("A. 形状：这一族只此一处翻译，两处 date 语义各自保留")
    src = read(PLANNER)
    for _i, name in enumerate(("list_day_todos", "list_todos_range"), 1):
        fn = func_of(src, name)
        hard = [(x.lineno, x.slice.value) for x in ast.walk(fn)
                if isinstance(x, ast.Subscript) and isinstance(x.ctx, ast.Load)
                and isinstance(x.slice, ast.Constant) and getattr(x.value, "id", "") == "t"]
        c.check("A%d `%s` 里对记录 `t` 的读下标为 0（全部走 `_todo_public_row`）" % (_i, name),
                not hard, str(hard))
    dicts = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef):
            for x in ast.walk(n):
                if isinstance(x, ast.Dict):
                    keys = {k.value for k in x.keys if isinstance(k, ast.Constant)}
                    if set(SIX) <= keys:
                        dicts.append((n.name, x.lineno))
    c.check("A3 六键字典字面量**只出现在 `_todo_public_row` 里**（有第二处就是第二份翻译，必漂）",
            bool(dicts) and all(nm == "_todo_public_row" for nm, _l in dicts), str(dicts))
    c.check("A4 `_todo_has_id` / `_todo_public_row` 各只有一个定义",
            src.count("def _todo_has_id(") == 1 and src.count("def _todo_public_row(") == 1,
            "%d / %d" % (src.count("def _todo_has_id("), src.count("def _todo_public_row(")))
    day_src = ast.get_source_segment(src, func_of(src, "list_day_todos")) or ""
    rng_src = ast.get_source_segment(src, func_of(src, "list_todos_range")) or ""
    c.check("A5 两处 `date` 的语义**各自保留**：day 那支传记录自己的锚点日、range 那支传落到的那天"
            "（统一它是行为变更，得另开一单）",
            '_todo_public_row(t, str(t.get("date") or ""))' in day_src and "_todo_public_row(t, iso)" in rng_src,
            "day 里 %s ｜ range 里 %s" % (
                [l.strip() for l in day_src.split("\n") if "_todo_public_row(" in l][:1],
                [l.strip() for l in rng_src.split("\n") if "_todo_public_row(" in l][:1]))
    c.check("A6 缺 id 的计数在**按天的循环之外**（挂进循环里，一条习惯落到两天就被报成「另有 2 条」）",
            "for t in todos" in rng_src and rng_src.index("_todo_has_id(t)") < rng_src.index("while day <= d1"),
            "")
    c.check("A7 `list_day_todos` 仍是挂在 build_tools 里给模型的那只手（本单没把它摘下来）",
            '"list_day_todos": Tool(' in src and "func=list_day_todos" in src, "")
    return c


def batch_b_fallback():
    c = Checker()
    title("B. 行为 · 缺键是**可见的降级**，不是崩溃")
    from app.modules.planner import list_day_todos, _todo_public_row
    from app import store

    with sandbox():
        seed_raw([good(status=_DEL)])
        rows, exc = call_rows(lambda: list_day_todos(DAY))
        c.check("B1 缺 `status` 的记录照常被列出来（改前这里是 KeyError ⇒ 模型那一轮工具调用整个失败）",
                not exc and len(items_of(rows)) == 1 and one_of(rows).get("title") == "做题",
                "%s ｜ %s" % (why(exc), str(rows)[:90]))
        r0 = one_of(rows)
        c.check("B2 兜出来的值是空串，不是 None（None 会被 f-string 念成「None」摆到屏幕上）",
                r0.get("status") == "" and all(isinstance(r0.get(k), str) for k in SIX),
                "%s ｜ %s" % (why(exc), str(r0)[:110]))

        seed_raw([good(status="done")])
        rows2, exc2 = call_rows(lambda: list_day_todos(DAY))
        c.check("B3 正控：同一条把 status 补回来，返回里就是补回来的那个值（兜底没把真值吃掉）",
                not exc2 and one_of(rows2).get("status") == "done",
                "%s ｜ %s" % (why(exc2), str(items_of(rows2))[:80]))

        seed_raw([good(status=_DEL, done_dates=[]), good(id="k2", title="约会")])
        rows3, exc3 = call_rows(lambda: list_day_todos(DAY))
        raw = [x for x in store.list_todos(DAY) if x.get("title") == "做题"]
        c.check("B4 `status` 缺 = 按**没完成**算（与 `store.todo_done_on` 同方向：宁可不勾，别替学生勾掉）",
                not exc3 and len(items_of(rows3)) == 2 and len(raw) == 1
                and store.todo_done_on(raw[0], DAY) is False,
                "%s ｜ %d 行；done_on=%s" % (
                    why(exc3), len(items_of(rows3)),
                    store.todo_done_on(raw[0], DAY) if raw else "取不到那条"))

        seed_raw([good(id="k3", start=_DEL, end=_DEL, title=_DEL)])
        rows4, exc4 = call_rows(lambda: list_day_todos(DAY))
        r4 = one_of(rows4)
        c.check("B5 缺 `start`/`end`/`title` 也一样列出来，三个位置都是空串（一行只有日子的难看形状）",
                not exc4 and len(items_of(rows4)) == 1
                and r4.get("start") == "" and r4.get("end") == "" and r4.get("title") == "",
                "%s ｜ %s" % (why(exc4), str(rows4)[:110]))

        seed_raw([good()])
        rows5, exc5 = call_rows(lambda: list_day_todos(DAY))
        c.check("B6 正控：补全之后 `start` 就是原值（`.get` 的兜底没顺手改写有值的记录）",
                not exc5 and one_of(rows5).get("start") == "14:00", why(exc5))

        empty = _todo_public_row({}, "")
        c.check("B7 翻译函数对一条**什么都没有**的记录也回得到六键、全是字符串（不靠调用方运气）",
                sorted(empty) == sorted(SIX) and all(isinstance(empty[k], str) for k in SIX),
                str(empty)[:110])
    return c


def batch_c_id():
    c = Checker()
    title("C. 行为 · 缺 id：整条不出，并且要说得出丢了几条")
    from app.modules.planner import list_day_todos, list_todos_range

    with sandbox():
        seed_raw([good(id=_DEL), good(id="k9", title="有句柄的那条")])
        rows, exc = call_rows(lambda: list_day_todos(DAY))
        c.check("C1 缺 id 的那条**不在**返回里（有 id 的那条照常在）",
                not exc and [r["id"] for r in items_of(rows)] == ["k9"],
                "%s ｜ %s" % (why(exc), str(items_of(rows))[:100]))
        c.check("C2 但返回里有一句「另有 1 条待办没有 id」—— 不静默少列"
                "（模型看得见才会跟学生解释，而不是回一句『这天没有安排』）",
                any("另有 1 条" in s and "没有 id" in s for s in infos_of(rows)),
                str(infos_of(rows))[:110])

        seed_raw([good(id="k8"), good(id="k9", title="有句柄的那条")])
        rows2, exc2 = call_rows(lambda: list_day_todos(DAY))
        c.check("C3 正控：给它补上 id 之后立刻出现，且那句说明跟着消失",
                not exc2 and sorted(r["id"] for r in items_of(rows2)) == ["k8", "k9"]
                and not infos_of(rows2), str(rows2)[:120])

        seed_raw([good(id=_DEL, title="甲"), good(id=_DEL, title="乙"), good(id="k7", title="丙")])
        rng, rex = call_rows(lambda: list_todos_range(DAY, "2026-10-27"))
        c.check("C4 `list_todos_range` 里两条缺 id 的**不许并成一行**"
                "（`by_id[\"\"]` 那个形状会把几件事并成一件，比崩溃贵）",
                not rex and len(items_of(rng)) == 1 and one_of(rng).get("id") == "k7",
                "%s ｜ %s" % (why(rex), str(items_of(rng))[:110]))
        c.check("C5 range 那一支也要说得出丢了几条（走 #63 就有的 notes 栏，不混进条目行）",
                any("另有 2 条" in s for s in infos_of(rng)), str(infos_of(rng))[:120])

        seed_raw([good(id="a", title="甲"), good(id="b", title="乙"), good(id="k7", title="丙")])
        rng2, rex2 = call_rows(lambda: list_todos_range(DAY, "2026-10-27"))
        c.check("C6 正控：三条都有句柄时**就是三行**（上面那个「1 行」不是恒真）",
                not rex2 and sorted(r["id"] for r in items_of(rng2)) == ["a", "b", "k7"],
                "%s ｜ %s" % (why(rex2), str([r["id"] for r in items_of(rng2)])[:90]))

        # 窗口拉到 14 天：这条缺 id 的周历记录会**落到两天** ⇒ 计数若挂在循环里就报「另有 2 条」
        seed_raw([good(id=_DEL, title="每周游泳", date="2026-10-21", start="19:00", end="20:00",
                       repeat="weekly", weekday=3)])
        once, oexc = call_rows(lambda: list_todos_range(DAY, "2026-11-08"))
        c.check("C7 同一条缺 id 的记录在 14 天窗口里落到两天，也只报「另有 1 条」（计数不挂循环）",
                not oexc and any("另有 1 条" in s for s in infos_of(once))
                and not any("另有 2 条" in s for s in infos_of(once)),
                "%s ｜ %s" % (why(oexc), str(infos_of(once))[:110]))
    return c


def batch_d_edge():
    c = Checker()
    title("D. 边界：这两类行怎么念，以及这批测试不脏真实数据")
    from app.agent.public_view import _item_line
    from app.modules.planner import list_day_todos
    from app.store import todo_occurs_on

    c.check("D1 `_item_line` 优先读 `info` ⇒ 那句「另有 N 条没有 id」会原样成为模型/学生看见的一行",
            _item_line({"info": "另有 1 条待办没有 id，列不出来（数据可能被手改过）"}).startswith("另有 1 条"),
            _item_line({"info": "另有 1 条待办没有 id"})[:60])
    line = _item_line({"id": "x", "title": "", "start": "", "end": "", "status": ""})
    c.check("D2 空 title / 空钟点的那一行不会拼出「None」这种字样（可见的降级 ≠ 假数据）",
            "None" not in line, repr(line)[:60])
    c.check("D3 缺 `date` 的记录走不到翻译那一步（`todo_occurs_on` 先把它挡在门外）—— 既有行为，本单不改",
            todo_occurs_on({"id": "z", "title": "没日子", "start": "10:00", "end": "11:00"}, DAY) is False,
            "")

    real = data_path("student", "todos.json")
    before = _snap(real)
    wrote = False
    with sandbox():
        seed_raw([good(status=_DEL), good(id=_DEL)])
        rows, exc = call_rows(lambda: list_day_todos(DAY))
        wrote = os.path.exists(data_path("student", "todos.json"))
    c.check("D4 这批真打之后**真实** `student/todos.json` 的存在性与内容一字未变（沙箱不许脏演示台账）",
            _snap(real) == before and wrote and not exc,
            "%s → %s；沙箱里写过=%s；%s" % (before, _snap(real), wrote, why(exc)))
    c.check("D5 `seed_raw()` 在沙箱外会被拒（整份覆盖写的 helper 漏一次就毁一次真实库）",
            _refused_outside_sandbox(), "在沙箱外调用应当抛错")
    return c


def _snap(p):
    try:
        return (True, hashlib.md5(open(p, "rb").read()).hexdigest()[:10])
    except OSError:
        return (False, "NO-FILE")


def _refused_outside_sandbox():
    saved = os.environ.pop("CAMPUSTIME_DATA_DIR", "")
    try:
        seed_raw([good()])
        return False
    except Exception:
        return True
    finally:
        if saved:
            os.environ["CAMPUSTIME_DATA_DIR"] = saved


def main():
    print("test_87_todo_public_shape：#66 待办对外形状缺键 —— 读侧兜底、缺 id 不出")
    print("=" * 60)
    code = 0
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（缺键降级）", batch_b_fallback),
                      ("C 批（缺 id）", batch_c_id),
                      ("D 批（边界与自污）", batch_d_edge)):
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
