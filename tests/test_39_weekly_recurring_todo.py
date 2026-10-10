"""每周重复待办（「每个周天都写作业」）专项护栏。

起因（2026-10-10 用户两张截图 + 一句「是判断出错了」）：
  「每个周天都加一个写作业的代办」   → 标题被洗成「每个都写作业」，三个候选全挤在同一天
  「你没理解我的画，每一个周天，所有周天」→ 标题成了「没理解我的画每所有每个都写作业」
用户选的方案是 **B：真·长期**（一条记录、每周都出现），不是再铺 N 条。

⚠️ 与「一周三次」的分工必须守住，两件事不是一件事：
    「一周三次」= 数量频次、没有固定星期几  → 仍然铺 N 条（test_35 钉着，本文件不碰它）
    「每个周天」= 固定星期几 + 无限重复      → 一条 repeat=weekly + weekday
所以 F 批专门拿"一周三次"当对照组：它要是被顺手改成重复，这里当场红。

判据一律"钉驱动条件 + 真发"，不钉关键词（上一轮三条假绿就是这么来的）。
"""
import datetime
import io
import json

from _harness import Checker, make_client, sandbox, title
from app.store import set_settings   # ⚠️ 在 app.store，不在 _harness（踩过：那里没有这个名字）

STUDENT = "app/static/student.html"
PLAN = "app/static/plan.html"
CONFIRM = "app/static/confirm.html"


def _sun(offset_weeks=0):
    """下一个周日（+N 周）的 ISO 日期。"""
    d = datetime.date.today()
    d += datetime.timedelta(days=(6 - d.weekday()) % 7 or 7)
    return (d + datetime.timedelta(weeks=offset_weeks)).isoformat()


def batch_a_recognition():
    c = Checker()
    title("A 批 · 认与不认：只有带周期冠词 + 点名星期几才算每周重复")
    from app.modules.planner import weekly_repeat_day

    yes = {"每个周天都加一个写作业的代办": 7, "每周日都写作业": 7,
           "以后每个周天晚上写作业": 7, "每一个周天都跑步": 7,
           "所有周六去图书馆": 6, "每星期一交周报": 1, "每个礼拜天写作业": 7}
    for t, want in yes.items():
        c.check(f"认：{t} → 周{want}", weekly_repeat_day(t) == want, weekly_repeat_day(t))
    no = {"周日去健身": "单日说法，不能判成永久", "下周六爬山": "点了具体一周的周六",
          "我一周要去三次健身房": "数量频次，走铺 N 条那条路",
          "每周都交周报": "没点哪天，替他挑一天=替他做主",
          "每个周末去爬山": "周末=六+日两天，一条记录表达不了",
          "今天写作业": "今天"}
    for t, why in no.items():
        c.check(f"不认：{t}（{why}）", weekly_repeat_day(t) is None, weekly_repeat_day(t))
    c.check("空输入不炸", weekly_repeat_day("") is None and weekly_repeat_day(None) is None)
    return c


def batch_b_title_and_veto():
    c = Checker()
    title("B 批 · 标题必须干净，吐槽句不许当待办名")
    from app.modules.planner import _looks_like_correction, _pick_title, _title_ok

    clean = {"每个周天都加一个写作业的代办": "写作业",
             "每周日都写作业": "写作业",
             "每个周日加一个写作业的待办": "写作业",
             "以后每个周天晚上写作业": "写作业"}
    for t, want in clean.items():
        got = _pick_title(t)
        c.check(f"标题擦干净：{t} → 「{want}」", got == want, f"实得「{got}」")
    for t in clean:
        c.check(f"框架词不许留在标题里：{t}",
                not any(w in _pick_title(t) for w in ("每个", "都", "以后", "周")), _pick_title(t))
    c.check("「每个周末」不许擦成「末去爬山」这种半截词",
            "末" not in _pick_title("每个周末去爬山"), _pick_title("每个周末去爬山"))

    bad = "你没理解我的画，每一个周天，所有周天"
    t = _pick_title(bad)
    c.check("纠错句判不出合法标题（宁可不接单）", not _title_ok(t), f"擦出来是「{t}」")
    c.check("吐槽否决认得这类说法",
            all(_looks_like_correction(x) for x in
                ("没理解我的话", "你说的不是这个", "我说的意思", "重新来", "答非所问")),
            "")
    c.check("正常事项名不被误否决",
            not any(_looks_like_correction(x) for x in ("写作业", "交实验报告", "健身房", "取快递")), "")
    c.check("接单闸门认得没有动作词的重复句（「每周日都写作业」）",
            __import__("app.modules.planner", fromlist=["x"]).wants_add_todo(bad) is False
            and __import__("app.modules.planner", fromlist=["x"]).wants_add_todo("每周日都写作业") is True)
    return c


def batch_c_store_model():
    c = Checker()
    title("C 批 · 数据模型：一条记录每个星期几都算出现，完成按天记")
    from app import store as S

    with sandbox():
        one = S.add_todo("交作业", _sun(0), "14:00", "15:00")
        wk = S.add_todo("写作业", _sun(0), "09:00", "10:30", repeat="weekly")
        c.check("一次性待办不带新字段（老形状一字不变）",
                "repeat" not in one and "done_dates" not in one, sorted(one))
        c.check("重复待办带 repeat/weekday/done_dates",
                wk.get("repeat") == "weekly" and wk.get("weekday") == 7
                and wk.get("done_dates") == [], wk)
        c.check("本周日算出现", S.todo_occurs_on(wk, _sun(0)))
        c.check("下周日也算出现", S.todo_occurs_on(wk, _sun(1)))
        c.check("周六不算出现", not S.todo_occurs_on(wk, _sun(0)[:-1] + "6")
                or S.iso_weekday_of(_sun(0)[:-1] + "6") != 7)
        past = (datetime.date.fromisoformat(_sun(0)) - datetime.timedelta(days=7)).isoformat()
        c.check("锚点之前不算出现（不把过去的周补出来）", not S.todo_occurs_on(wk, past), past)
        got = [t["id"] for t in S.list_todos(_sun(1))]
        c.check("list_todos(date) 会把重复项展开到那一天", wk["id"] in got, got)
        c.check("一次性待办不会串到别的日子", one["id"] not in [t["id"] for t in S.list_todos(_sun(1))])

        S.set_todo_done_on(wk["id"], _sun(0), True)
        after = S.get_todo(wk["id"])
        c.check("勾掉本周 → 记在 done_dates 上", after["done_dates"] == [_sun(0)], after["done_dates"])
        c.check("本周算完成", S.todo_done_on(after, _sun(0)))
        c.check("下周不算完成（勾一次 ≠ 永远勾完）", not S.todo_done_on(after, _sun(1)))
        c.check("整条 status 没被改成 done（否则第二周就消失了）",
                after["status"] == "planned", after["status"])
        c.check("下周照样出现在日程里",
                wk["id"] in [t["id"] for t in S.list_todos(_sun(1))])
        S.set_todo_done_on(wk["id"], _sun(0), False)
        c.check("撤销本周完成能撤回", S.get_todo(wk["id"])["done_dates"] == [])
        S.set_todo_done_on(one["id"], _sun(0), True)
        c.check("一次性待办带 on 也照旧翻 status", S.get_todo(one["id"])["status"] == "done")

        junk = S.add_todo("测试", _sun(0), "10:00", "11:00", repeat="daily")
        c.check("不认识的 repeat 值退回一次性（不写脏字段）", "repeat" not in junk, junk.get("repeat"))
        legacy = {"id": "old", "title": "老数据", "date": _sun(0), "status": "planned"}
        c.check("缺新字段的老记录照样判（不 KeyError）",
                S.todo_occurs_on(legacy, _sun(0)) and not S.todo_done_on(legacy, _sun(0)))
        grp = S.list_todos_in_month(_sun(0)[:7])
        hit_days = [d for d, ts in grp.items() if wk["id"] in [t["id"] for t in ts]]
        # 锚点起算，这个月里还剩几个周日就是几个（10 月：11/18/25 三个；10/4 在锚点之前不算）
        want = sum(1 for k in range(6)
                   if (datetime.date.fromisoformat(_sun(0))
                       + datetime.timedelta(weeks=k)).month == int(_sun(0)[5:7]))
        c.check("月聚合里每个（锚点之后的）周日都有这条", len(hit_days) == want,
                f"实际 {sorted(hit_days)}，应有 {want} 个")
        c.check("月聚合不把锚点之前的周日算进来",
                all(d >= _sun(0) for d in hit_days), sorted(hit_days))
    return c


def batch_d_end_to_end():
    c = Checker()
    title("D 批 · 端到端：说一句话 → 一条每周重复 → 三个周日都看得见")
    with sandbox():
        set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
        cli = make_client()
        r = cli.post("/api/chat", json={"message": "每个周天都加一个写作业的代办",
                                        "session_id": "t39-d", "module": "planner"})
        d = r.json()
        cards = [o for o in (d.get("options") or []) if isinstance(o, dict)]
        card = cards[0] if cards else {}
        c.check("接口 200 且出了候选卡", r.status_code == 200 and card.get("kind") == "todo_slots",
                f"{r.status_code} {str(d)[:90]}")
        c.check("卡上带 repeat=weekly", card.get("repeat") == "weekly", card.get("repeat"))
        c.check("标题是「写作业」（不是「每个都写作业」）", card.get("title") == "写作业", card.get("title"))
        sl = card.get("slots") or []
        c.check("候选段全部落在同一个星期几（周日）",
                bool(sl) and all((s.get("weekday") or "").find("周") == 0 and "日" in (s.get("weekday") or "")
                                 or s.get("weekday") == "周日" for s in sl),
                [s.get("label") for s in sl])
        c.check("聊天回话讲的是每周重复、且没承诺'只排这一周'",
                "每周重复" in (d.get("answer") or "") or "每个" in (d.get("answer") or ""),
                (d.get("answer") or "")[:70])
        c.check("回话里不许出现'没有自动重复这回事'（对重复卡那是假话）",
                "没有自动重复这回事" not in (d.get("answer") or ""), (d.get("answer") or "")[-60:])

        one = sl[0]
        w = cli.post("/api/todos/batch", json={"session_id": "t39-d", "title": card.get("title"),
                                               "slots": [{"date": one["date"], "start": one["start"],
                                                          "end": one["end"]}],
                                               "repeat": "weekly"})
        b = w.json()
        c.check("勾一段只写 1 条", b.get("count") == 1, b.get("count"))
        item = (b.get("added") or [{}])[0]
        c.check("落库那条是 weekly + weekday=7", item.get("repeat") == "weekly"
                and item.get("weekday") == 7, item)
        nxt = (datetime.date.fromisoformat(item["date"]) + datetime.timedelta(days=7)).isoformat()
        seen = cli.get("/api/todos", params={"date": nxt}).json().get("todos", [])
        c.check("下一个周日 GET /api/todos 能看见它", item["id"] in [t["id"] for t in seen],
                [t["title"] for t in seen])
        p = cli.put(f"/api/todos/{item['id']}", json={"status": "done", "on": item["date"]}).json()
        c.check("带 on 的完成只记那一天", p.get("todo", {}).get("done_dates") == [item["date"]],
                str(p)[:90])
        c.check("下周那条还在（没被整条关掉）",
                item["id"] in [t["id"] for t in cli.get("/api/todos", params={"date": nxt}).json()["todos"]])
    return c


def batch_e_three_layers_synced():
    c = Checker()
    title("E 批 · 三处判据必须同步（后端 / 学生端 / plan 页）")
    st = io.open(STUDENT, encoding="utf-8").read()
    pl = io.open(PLAN, encoding="utf-8").read()
    cf = io.open(CONFIRM, encoding="utf-8").read()
    c.check("student.html 有 todoOccurs/todoDoneOn",
            "function todoOccurs(" in st and "function todoDoneOn(" in st)
    c.check("plan.html 有同名同规则的判据",
            "function todoOccurs(" in pl and "function todoDoneOn(" in pl)
    # 钉**调用点**的旧写法。⚠️ 不能整文件搜 `t.date === dIso` —— 那句正是 todoOccurs 内部
    # 对一次性待办的正确判据，它本来就该出现一次（这条一开始写错，红在了自己身上）。
    STALE = ("schTodos.filter(t => t.date", "todos.filter(t => t.date",
             "byDate[t.date]", "(byDate[t.date] = ")
    for name, src in (("student.html", st), ("plan.html", pl)):
        left = [ln.strip()[:70] for ln in src.splitlines() if any(k in ln for k in STALE)]
        c.check(f"{name} 的渲染调用点没有残留'按日期精确相等'", not left, left[:2])
        c.check(f"{name} 里 todoOccurs 的调用点至少 3 处（周表/月表/下钻）",
                src.count("todoOccurs(") >= 4, src.count("todoOccurs("))
    c.check("student.html 周表用 todoOccurs 过滤待办",
            "schTodos.filter(t => todoOccurs(t, dIso))" in st)
    c.check("confirm.html 把 repeat 一路发给 /api/todos/batch",
            "repeat: p.repeat" in cf)
    c.check("重复待办在日程上有可视标记（不然学生不知道它为什么每周都冒出来）",
            "🔁" in st and "🔁" in pl)
    c.check("勾完成时前端带上那一天", 'data-on=' in pl and 'body.on=on' in st.replace(" ", ""))
    return c


def batch_f_old_path_untouched():
    c = Checker()
    title("F 批 · 对照组：「一周三次」仍然是铺 N 条，没被改成每周重复")
    from app.modules.planner import todo_slots_proposal
    card = todo_slots_proposal("我一周要去三次健身房")
    c.check("repeat 字段为空", (card or {}).get("repeat", "") == "", (card or {}).get("repeat"))
    c.check("仍然预选 3 段", (card or {}).get("preselect") == 3, (card or {}).get("preselect"))
    days = {s.get("date") for s in (card or {}).get("slots") or []}
    c.check("候选落在不同日子（一天一段）", len(days) >= 3, days)
    c.check("标题仍是「健身房」", (card or {}).get("title") == "健身房", (card or {}).get("title"))
    return c


def main():
    results = [batch_a_recognition(), batch_b_title_and_veto(), batch_c_store_model(),
               batch_d_end_to_end(), batch_e_three_layers_synced(), batch_f_old_path_untouched()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 每周重复待办总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
