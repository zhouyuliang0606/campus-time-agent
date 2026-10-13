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
MAIN = "app/main.py"
PLAN = "app/static/plan.html"
CONFIRM = "app/static/confirm.html"
PLANNER = "app/modules/planner.py"


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
           "所有周六去图书馆": 6, "每星期一交周报": 1, "每个礼拜天写作业": 7,
           # —— 倒装语序（2026-10-13 用户 Prompt 01：「周天早上去健身房每周」）——
           "周天早上去健身房每周": 7, "周日早上去健身房每周": 7,
           "星期一去图书馆每周": 1, "礼拜三交作业每星期": 3,
           "每周天早上去健身房": 7}          # 正装对照组，确认没被倒装那条改坏
    for t, want in yes.items():
        c.check(f"认：{t} → 周{want}", weekly_repeat_day(t) == want, weekly_repeat_day(t))
    no = {"周日去健身": "单日说法，不能判成永久", "下周六爬山": "点了具体一周的周六",
          "我一周要去三次健身房": "数量频次，走铺 N 条那条路",
          "每周都交周报": "没点哪天，替他挑一天=替他做主",
          "每个周末去爬山": "周末=六+日两天，一条记录表达不了",
          "今天写作业": "今天",
          # 倒装句的反面：句尾那个「每」各有主人，不许被"周日重复"抢走
          "周天去跑步每周两次": "频次（每周两次），不是每个周日",
          "周天去健身每月": "每月归 unsupported_period 的硬闸门，这里返回 None",
          "下周日去健身房每周": "带「下」的是具体某天，不是长期"}
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

        # ⚠️ 探针值不能用 "daily"（2026-10-13 起它是**合法值**了：一条每天的习惯）。
        junk = S.add_todo("测试", _sun(0), "10:00", "11:00", repeat="monthly")
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
    c.check("student.html 周表用 todoOccurs 过滤待办（且习惯那一档被 isHabit 挡在格子外面）",
            "schTodos.filter(t => !isHabit(t) && todoOccurs(t, dIso))" in st)
    c.check("confirm.html 把 repeat 一路发给 /api/todos/batch",
            "repeat: p.repeat" in cf)
    # 改期那张卡（todo_add / todo_pick）走的是 POST /api/todos，不在 batch 那条上 ——
    # 少带 repeat 就正好把"删旧 + 加新"里的长期洗掉（F 组那两条行为断言验的是结果，这条钉形状）。
    c.check("confirm.html 两处【确认加入】都把 repeat 发给 /api/todos",
            cf.count("repeat: p.repeat") == 3, cf.count("repeat: p.repeat"))
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


def _last_on_or_before(weekday: int, iso: str) -> str:
    """`iso` 那天（含）之前最后一个"星期几 = weekday"的日子。"""
    d = datetime.date.fromisoformat(iso)
    while d.isoweekday() != weekday:
        d -= datetime.timedelta(days=1)
    return d.isoformat()


def batch_g_weekly_horizon():
    """G 批 · 每周重复往后**只铺 3 个日历月**（2026-10-13 用户指令：给展开加个头）。

    没有头的 weekly 等于"直到世界尽头的每个周日"：月表翻到 2099-01 也画得出东西，
    而学生从没答应过要做到那一年（test_03 那条「查空月份返回空」就是红在这儿）。
    上限这把尺子有三份实现（`store.weekly_horizon` / student.html / plan.html，
    接手指南 §4 第 9 条要求同步），所以这一批既钉边界、也钉三份是不是同一把尺子。
    """
    c = Checker()
    title("G 批 · 展开上限：3 个日历月之内才算出现，之外一律没有")
    from app import store as S

    with sandbox():
        wk = S.add_todo("每周跑步", _sun(0), "07:00", "08:00", repeat="weekly")
        lim = S.weekly_horizon(wk["date"])
        # ⚠️ 边界必须钉**两侧**，而且"里面"那一侧要用**跟上限无关的绝对日子**：
        #   上一版这里写的是"上限那天所在的那个同星期几仍然算出现" —— 那个日期是从
        #   `weekly_horizon()` 自己算出来的，上限改成 0 个月它也跟着缩，断言永远为真（自证式断言）。
        #   变异 V2（上限=0 个月）就是这么躲过去的。
        c.check("四周后那个同星期几还算出现（绝对日子，上限缩到零就会红）",
                S.todo_occurs_on(wk, _sun(4)), _sun(4))
        c.check("半年后那个同星期几不算（远于任何 3 个月的上限）",
                not S.todo_occurs_on(wk, _sun(26)), _sun(26))
        # 紧挨着上限那一格的一侧一格：钉的是"切在 3 个月"，不是"切在大概三个多月"
        last_in = _last_on_or_before(wk["weekday"], lim)
        first_out = (datetime.date.fromisoformat(last_in)
                     + datetime.timedelta(weeks=1)).isoformat()
        c.check("上限之内最后一个同星期几仍然算出现（边界内侧）",
                S.todo_occurs_on(wk, last_in), f"锚点 {wk['date']} → 上限 {lim}，查 {last_in}")
        c.check("再往后一个周期就不算了（边界外侧）",
                not S.todo_occurs_on(wk, first_out), first_out)
        c.check("2099 年那个同星期几不再出现（原先就是红在这儿）",
                not S.todo_occurs_on(wk, "2099-01-04"))
        c.check("按天查询也走同一套（读侧只有一份规则，上限自动继承）",
                wk["id"] not in [t["id"] for t in S.list_todos(first_out)]
                and wk["id"] in [t["id"] for t in S.list_todos(last_in)],
                first_out)

        # 月末锚点：加 3 个**日历月**，落不进就贴到目标月最后一天。
        # ⚠️ 挑的锚点必须让"3 个日历月"和"90 天"算出**不同**的日子，否则这条钉不住
        #   真正写错成 `today + timedelta(days=90)` 的那种实现
        #   （11-30、12-31 两个锚点两种算法恰好相等，变异 V4 因此在上一版红 0 条）。
        c.check("月末锚点贴到目标月最后一天（2026-11-30 + 3 月 = 2027-02-28）",
                S.weekly_horizon("2026-11-30") == "2027-02-28",
                S.weekly_horizon("2026-11-30"))
        c.check("2027-01-31 + 3 月 = 2027-04-30（90 天那套会算成 05-01）",
                S.weekly_horizon("2027-01-31") == "2027-04-30",
                S.weekly_horizon("2027-01-31"))
        c.check("2027-05-31 + 3 月 = 2027-08-31（90 天那套会算成 08-29）",
                S.weekly_horizon("2027-05-31") == "2027-08-31",
                S.weekly_horizon("2027-05-31"))
        # ⚠️ 这三个锚点都得在**未来**：`weekly_horizon` 取的是 max(今天, 锚点)，
        #   写了个过去的日子（上一版的 2026-05-31）算出来是"今天 + 3 月"，
        #   断言当场红 —— 那是我在测自己的误解，不是在测代码。
        c.check("过去的锚点按今天起算（max(今天, 锚点) 这一条要说清楚）",
                S.weekly_horizon("2020-01-01") == S.weekly_horizon(),
                S.weekly_horizon("2020-01-01"))
        c.check("跨年的锚点也算对",
                S.weekly_horizon("2026-12-31") == "2027-03-31",
                S.weekly_horizon("2026-12-31"))

        # 锚点在**未来**的那条：上限跟着锚点走，否则这一天都看不见
        far = S.add_todo("以后的周六", "2027-06-05", "09:00", "10:00", repeat="weekly")
        c.check("远期锚点：从锚点起算，锚点那天照样出现（上限不能吞掉将来才开始的安排）",
                S.todo_occurs_on(far, "2027-06-05"), S.weekly_horizon("2027-06-05"))
        c.check("但它也照样有头（锚点 + 3 个月之后一样没有）",
                not S.todo_occurs_on(far, "2027-12-25"))

        # 一次性待办：日子再远也照原样在。上限收的是"替他推出来的将来"，不是他写的数。
        once = S.add_todo("毕业礼", "2099-01-04", "10:00", "11:00")
        c.check("一次性待办不受上限影响", S.todo_occurs_on(once, "2099-01-04"))

        # 三份实现的常量必须是同一个数（值的一致另外用 node 把两份 JS 抠出来对照过，
        # 见 D:\\campustime-scratch\\chk_horizon_three.py —— 评委机器不一定有 node，
        # 所以测试里只钉"字面上的同一把尺子"）
        st = io.open(STUDENT, encoding="utf-8").read()
        pl = io.open(PLAN, encoding="utf-8").read()
        for name, src in (("student.html", st), ("plan.html", pl)):
            c.check(f"{name} 有同一把尺子（常量 3，且 todoOccurs 真的拿上限去比）",
                    "WEEKLY_HORIZON_MONTHS = 3" in src
                    and "dIso <= weeklyLimit(t.date)" in src,
                    src.count("weeklyLimit("))
        c.check("后端那份的常量也是 3（三份不许各写一个数）",
                S.WEEKLY_HORIZON_MONTHS == 3, S.WEEKLY_HORIZON_MONTHS)
    return c


def batch_h_inverted_word_order():
    """H 批 · 倒装语序「周天早上去健身房每周」端到端（2026-10-13 用户 Prompt 01）。

    这一批一半是钉行为，一半是钉**施工单里两条被实测否掉的前提** ——
    那两条不写下来，下一轮还会有人把它们抄回来：
      ① 它给的正则把两种语序写进**同一个** pattern、两个分支都用同名命名组 `wd`，
         Python 直接 `redefinition of group name 'wd' as group 2` —— 粘进去 planner 整个 import 失败；
      ② 改成两个组名之后能编译，但倒装那条要求「周X」**紧贴**「每」，
         在它自己举的例句上（中间隔着"早上去健身房"）仍然返回 None；
      ③ 它的「修改 3」（给标题擦除再加一条倒装分支）是多余的：
         正装那条的星期字本来就可选，「每周」两字单独就能擦，
         前半「周X」归日子那一路擦 —— 实测标题已经是干净的「健身房」。
    """
    c = Checker()
    title("H 批 · 倒装「周X…每周」：端到端 + 施工单三条前提的实测结论")
    from app.modules import planner as P

    with sandbox():
        # ① 端到端：倒装句落的必须也是"一条每周重复"，不是某一天一次
        next_sun = _sun(0)
        for s, title_want in (("每周天早上去健身房", "健身房"),
                              ("周天早上去健身房每周", "健身房"),
                              ("周日晚上跑步每周", "跑步")):
            prop = P.auto_todo_proposal(s) or P.parse_add_todo(s)
            c.check("端到端落的是每周重复｜%s" % s,
                    bool(prop) and prop.get("repeat") == "weekly"
                    and prop.get("title") == title_want
                    and prop.get("date") == next_sun,
                    f"{(prop or {}).get('repeat')}/{(prop or {}).get('title')}"
                    f"/{(prop or {}).get('date')}")
        # ② 同一件事换个语序说，落卡必须一模一样（两种说法不该有两种结果）
        a = P.auto_todo_proposal("每周天早上去健身房") or {}
        b = P.auto_todo_proposal("周天早上去健身房每周") or {}
        c.check("正装与倒装落卡一致",
                (a.get("repeat"), a.get("title"), a.get("date"))
                == (b.get("repeat"), b.get("title"), b.get("date")),
                f"{(a.get('title'), a.get('date'))} vs {(b.get('title'), b.get('date'))}")
        # ③ 候选卡那一路也带上 repeat（少带一次，勾完就退化成单日）
        card = P.todo_slots_proposal("周天去健身房每周")
        c.check("摊候选卡也带 repeat=weekly",
                bool(card) and card.get("repeat") == "weekly"
                and card.get("title") == "健身房",
                f"{(card or {}).get('kind')}/{(card or {}).get('repeat')}")
        # ④ 标题擦除早就够了 —— 钉住这条，免得有人照施工单「修改 3」再补一条不命中的分支
        c.check("倒装句的标题不需要新擦除分支（现在就是干净的「健身房」）",
                P._pick_title("周天早上去健身房每周") == "健身房",
                P._pick_title("周天早上去健身房每周"))
        c.check("日子也照样认（前半「周天」归日子那一路擦）",
                P._pick_date("周天早上去健身房每周") == next_sun,
                P._pick_date("周天早上去健身房每周"))
        # ⑤ 形状：两种语序是**两条正则**（同一个 pattern 里两个同名组编译不过），
        #    而且正装先查、倒装兜底。⚠️ 实测过：我试的 5 句里没找到"换个顺序答案就变"的句子
        #    （倒装那条要求「每」在星期几**之后**，「周三开会每周五汇报」它压根不命中，
        #     「每周三上课，周日去健身房每周」两条都给 3）—— 所以这条是**形状钉**，
        #     钉的是"更严的那条先查、宽的那条只兜底"这个结构，不许被无声调换。
        src = io.open(PLANNER, encoding="utf-8").read()
        i_pos = src.find("_REPEAT_WEEKDAY_RE.search(t)")
        i_inv = src.find("_REPEAT_WEEKDAY_INV_RE.search(t)")
        c.check("形状：正装先查、倒装兜底（顺序不改结果，但这条结构不许无声调换）",
                0 < i_pos < i_inv, (i_pos, i_inv))
        i_def = src.find("_REPEAT_WEEKDAY_INV_RE = re.compile(")
        body = src[i_def:i_def + 700]
        c.check("形状：倒装那条允许两段之间隔内容（要求紧邻的版本在它自己例句上不命中）",
                r"[\s\S]*?" in body, "倒装正则里没有跨内容的那一段")
        c.check("形状：「每周两次」这类频次被堵住（周/星也进拦截表）",
                P.weekly_repeat_day("周天去跑步每周两次") is None
                and P.weekly_repeat_day("周日晚上跑步每周") == 7,
                "每周两次 与 每周 的区分")
    return c


def batch_i_daily_habit():
    """I 批 · 每天类习惯（2026-10-13 用户 Prompt 02）。

    产品口径是用户拍板的两条，断言全部围着它们写：
      ① 「每天/天天/每日」落成**一条** repeat=daily，不再是一句"我记不了"；
      ② 习惯**只进代办清单、一律不画进日历**（学生原话「这种任务完成不了，
         我也不往日程堆，太多了乱」）；红角标 = 今天还没勾掉的条数。

    ⚠️ 施工单 §三.1 那句「摘掉 `_UNSUPPORTED_PERIOD` 三项就彻底放开」是**错的**：
      实测摘掉之后 `wants_add_todo("我每天都要背单词")` 仍然是 False ——
      拦它的不是否决表，是接单开关里没有 daily 这一档。
      所以 D 组专门钉这一条：只改否决表会得到"不拦了，但也没接"。
    """
    c = Checker()
    title("I 批 · 每天类习惯：认得、只建一条、只进代办不画日历")
    from app.modules import planner as P
    from app.modules.planner import (daily_repeat, wants_add_todo, unsupported_period,
                                     weekly_repeat_day, repeat_of, parse_time_spec, _pick_title)
    from app.store import (REPEAT_DAILY, REPEAT_WEEKLY, REPEAT_VALUES, add_todo,
                           HABIT_REPEATS, DAY_LOGGED_REPEATS, is_habit, list_todos,
                           list_todos_in_month,
                           set_todo_done_on, todo_done_on, todo_occurs_on)

    today = datetime.date.today().isoformat()
    tmr = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    # ==== A. 认与不认 ====
    for s in ("我每天都要背单词", "天天跑步", "每日英语打卡", "以后每天晨跑", "每一天都背单词"):
        c.check(f"认：{s}", daily_repeat(s) and not weekly_repeat_day(s),
                f"daily={daily_repeat(s)} weekly={weekly_repeat_day(s)}")
    for s, why in (("今天背单词", "具体一天，不是每天"), ("明天跑步", "同上"),
                   ("昨天没睡好", "过去 + 不是每天"), ("每周日都写作业", "那是 weekly 的活"),
                   ("每个周天都加一个写作业的代办", "点名星期几，优先 weekly"),
                   ("隔一天去跑步", "跳日子表达不了")):
        c.check(f"不认 daily：{s}（{why}）", not daily_repeat(s), daily_repeat(s))
    c.check("「每周几 + 每天」同时出现时 weekly 赢（repeat_of 只有一份判据）",
            repeat_of("每周三上课，每天都要背单词") == REPEAT_WEEKLY,
            repeat_of("每周三上课，每天都要背单词"))

    # ==== B. 施工单那条错前提：只摘否决表不够，接单开关必须有分支 ====
    c.check("否决表里已经没有「每天/天天/每日」（摘掉了）",
            not any(w in ("每天", "天天", "每日") for w in _UNSUPPORTED_NOW()),
            _UNSUPPORTED_NOW())
    c.check("但光摘否决表不会放行 —— 这条钉的是 wants_add_todo 的 daily 分支真的在生效",
            wants_add_todo("我每天都要背单词") is True)
    c.check("天天跑步 / 每日英语打卡 也照接",
            wants_add_todo("天天跑步") and wants_add_todo("每日英语打卡"))
    c.check("吐槽句不许被接成下单（「你每天都不回我消息」）",
            wants_add_todo("你每天都不回我消息") is False)
    c.check("问句不许被接（「每天几点起床最好」「每天都这么累有什么用」）",
            not wants_add_todo("每天几点起床最好") and not wants_add_todo("每天都这么累有什么用"))
    # ⚠️ 本轮改 wants_add_todo 时**自己造出来的回归**（变异 Y13 钉着）：
    #   「每天背单词完成了」既有"每天"又有"了"，新增那一档先接走 → 系统给他排一件
    #   叫「背单词完成」的每天任务，而他是在报告今天做完了。
    #   判据：周期那两档（weekly/daily）都要让路给已然（`_done_already_said`），
    #   这条闸只加在周期档上，别加到整函数头上 ——「每天背单词 晚上9点」照样要接单。
    c.check("已然句不许被当成新增：「每天背单词完成了」走完成那一族",
            wants_add_todo("每天背单词完成了") is False, wants_add_todo("每天背单词完成了"))
    c.check("对照：同一句去掉「了」就照旧是下单",
            wants_add_todo("每天背单词") is True)
    # 同一族的另一半：weekly 那档从 2026-10-10 起就有这个洞（本轮加 daily 时才撞出来，一起堵）。
    # 「每周日写作业完成了」以前也被"每周几 + 事名"那条新增分支抢走 → 给他新排一件「写作业完成」。
    c.check("同一族的另一半：「每周日写作业完成了」也不许当新增（weekly 档同样让路）",
            wants_add_todo("每周日写作业完成了") is False, wants_add_todo("每周日写作业完成了"))
    c.check("对照：weekly 去掉「了」照旧接单",
            wants_add_todo("每周日都写作业") is True)
    c.check("闸门没被顺手放宽：每月 / 每个周末 照旧拦",
            unsupported_period("每个月交一次电费") == "每个月"
            and unsupported_period("每个周末去爬山") == "每个周末",
            unsupported_period("每个月交一次电费"))
    for s in ("我每天都要背单词", "以后每天晨跑", "每日英语打卡"):
        c.check(f"标题擦干净：{s}", "每天" not in _pick_title(s)
                and "天天" not in _pick_title(s) and "每日" not in _pick_title(s), _pick_title(s))
    sp = parse_time_spec("我每天都要背单词 晚上9点")
    c.check("一次解析就定档：repeat=daily 且 weekday 为空（不猜星期几）",
            sp["repeat"] == REPEAT_DAILY and sp["weekday"] is None and sp["start"] == "21:00", sp)

    # ==== C. 存储层：一条记录、天天出现、按天记完成、有头 ====
    with sandbox():
        set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"})
        cli = make_client()
        item = add_todo("背单词", today, "21:00", "21:30", repeat=REPEAT_DAILY)
        c.check("落库带 repeat=daily + done_dates（少了后者，勾今天会把整条划掉）",
                item.get("repeat") == REPEAT_DAILY and "done_dates" in item, item)
        c.check("daily 不写 weekday（它每天都是那天）", not item.get("weekday"), item.get("weekday"))
        c.check("今天算出现 / 明天算出现", todo_occurs_on(item, today) and todo_occurs_on(item, tmr))
        c.check("锚点之前不算（「从下周一开始每天」不能把过去的日子也算上）",
                not todo_occurs_on(item, (datetime.date.today()
                                          - datetime.timedelta(days=1)).isoformat()))
        c.check("往后同样有头（2099 不该出现，沿用 weeklyLimit 那把尺子）",
                not todo_occurs_on(item, "2099-01-01"))
        set_todo_done_on(item["id"], today, True)
        back = cli.get("/api/todos").json()["todos"]
        got = [x for x in back if x["id"] == item["id"]][0]
        c.check("勾掉今天 → 今天 done、明天没 done（按天记）",
                todo_done_on(got, today) and not todo_done_on(got, tmr), got.get("done_dates"))
        c.check("整条 status 不能被翻成 done（明天它还得出现）",
                got.get("status") != "done", got.get("status"))
        # ==== C-bis. 「说出来勾掉」那一路（不是复选框）：查出来的老洞，weekly 同样中招 ====
        # 原写法是 `update_todo_status(id, "done")` + 读侧只看 done_dates ⇒ 空操作：
        # 学生点完确认，第二天那条照旧挂着。现在两条确认路径都走 `set_todo_done_on`。
        set_todo_done_on(item["id"], today, False)      # 撤回，重新用"说话"这条路勾
        r_done = cli.post("/api/chat", json={"message": "背单词完成了",
                                             "session_id": "t39-id", "module": "planner"})
        dcards = [o for o in (r_done.json().get("options") or []) if isinstance(o, dict)]
        c.check("说「背单词完成了」认出的是这条习惯",
                [o.get("kind") for o in dcards] == ["todo_done"], [o.get("kind") for o in dcards])
        r_ok = cli.post("/api/chat", json={"message": "确认",
                                           "session_id": "t39-id", "module": "planner"})
        after = [x for x in list_todos() if x["id"] == item["id"]][0]
        c.check("点确认之后今天真的记进 done_dates（老写法在这儿是空操作）",
                today in (after.get("done_dates") or []), after.get("done_dates"))
        c.check("回执要说清记的是哪天、而且明天还会出现（不许说得像到底了）",
                "已标记为完成" in (r_ok.json().get("answer") or "")
                and "明天" in (r_ok.json().get("answer") or ""), (r_ok.json().get("answer") or "")[:80])
        def _covers_2100():
            return any(s.get("start", "99:99") <= "21:00" and s.get("end", "") >= "21:30"
                       for s in P.find_free_slots(today, min_minutes=30))
        set_todo_done_on(item["id"], today, False)
        busy_before = _covers_2100()
        set_todo_done_on(item["id"], today, True)
        c.check("勾掉今天之后，21:00-21:30 这段不再替它占着（没勾时占、勾了就放开）",
                busy_before is False and _covers_2100() is True,
                f"没勾={busy_before} 勾了={_covers_2100()}")
        one_more = add_todo("取快递", today, "16:00", "17:00")
        set_todo_done_on(one_more["id"], "", True)
        c.check("一次性待办不传日子仍照旧翻 status（这次改动没碰它）",
                [x for x in list_todos() if x["id"] == one_more["id"]][0]["status"] == "done")
        # 确认卡（confirm.html 的 todo_done）发的是**不带 on 的 PUT** —— 老写法在这儿正好是空操作，
        # 所以这一条才是那颗牙（Y4 变异：把 `and date` 加回去，它当场红）。
        by_card = add_todo("打卡·走确认卡", today, "06:30", "07:00", repeat=REPEAT_DAILY)
        pu = cli.put(f"/api/todos/{by_card['id']}", json={"status": "done"})
        got2 = [x for x in list_todos() if x["id"] == by_card["id"]][0]
        c.check("不带 on 的 PUT 对习惯也按天记（确认卡那条发的就是这一种）",
                pu.status_code == 200 and today in (got2.get("done_dates") or [])
                and got2.get("status") == "planned",
                f"{got2.get('done_dates')} / {got2.get('status')}")
        c.check("一次性待办不受影响：status 照旧是判完成的依据",
                todo_done_on({"date": today, "status": "done"}, today)
                and not todo_occurs_on({"date": today, "repeat": ""}, tmr))
        c.check("判据收成两个集合，别再散着写 == weekly（HABIT / DAY_LOGGED）",
                HABIT_REPEATS == (REPEAT_DAILY,) and set(DAY_LOGGED_REPEATS) == {REPEAT_WEEKLY, REPEAT_DAILY}
                and set(REPEAT_VALUES) == {"", REPEAT_WEEKLY, REPEAT_DAILY})
        # ==== D. 日历一律不画习惯（后端这一侧只有一处取数：月表） ====
        grp = list_todos_in_month(today[:7])
        c.check("月表里找不到习惯（一天都不许出现，不然格子天天占）",
                not any(x.get("repeat") == REPEAT_DAILY for v in grp.values() for x in v),
                [x.get("title") for v in grp.values() for x in v if x.get("repeat") == REPEAT_DAILY])
        wk = add_todo("每周跑步", _sun(0), "07:00", "08:00", repeat=REPEAT_WEEKLY)
        grp2 = list_todos_in_month(_sun(0)[:7])
        c.check("对照：weekly 照旧画进日历（它是时间承诺，用户只把 daily 移出去了）",
                any(x.get("repeat") == REPEAT_WEEKLY for v in grp2.values() for x in v), wk.get("repeat"))
        c.check("is_habit 只认 daily，不认 weekly / 一次性",
                is_habit(item) and not is_habit(wk) and not is_habit({"repeat": ""}))

        # ==== E. 端到端：一句话 → daily 候选卡 → 勾一段只建一条 ====
        r = cli.post("/api/chat", json={"message": "我每天都要背单词",
                                        "session_id": "t39-i", "module": "planner"})
        d = r.json()
        cards = [o for o in (d.get("options") or []) if isinstance(o, dict)]
        card = cards[0] if cards else {}
        c.check("出的是候选卡（不是「记不了」那句）",
                card.get("kind") == "todo_slots" and card.get("repeat") == REPEAT_DAILY, card)
        c.check("话术讲清三件事：每天 / 只建一条 / 不进日程格子",
                all(k in (card.get("hint") or "") for k in ("每天", "只建一条", "代办")),
                (card.get("hint") or "")[:120])
        ans = d.get("answer") or ""
        c.check("回话不许出现「记不了」（这句现在办得了）", "记不了" not in ans, ans[:80])
        sl = (card.get("slots") or [{}])[0]
        w = cli.post("/api/todos/batch", json={"session_id": "t39-i", "title": card.get("title"),
                                               "slots": [{"date": sl.get("date"), "start": sl.get("start"),
                                                          "end": sl.get("end")}],
                                               "repeat": REPEAT_DAILY})
        b = w.json()
        c.check("勾一段只写 1 条，且落的是 daily",
                b.get("count") == 1 and (b.get("added") or [{}])[0].get("repeat") == REPEAT_DAILY, b)
        # ==== F. 改期这一族：换钟点不许把"每天/每周"洗掉 ====
        # 「把背单词改到晚上十点」走的是**删旧 + 加新**，新那条若不接住 repeat，
        # 习惯就只剩这一天（2026-10-13 实测到的第二个洞，同一族的 weekly 也一样）。
        for tag, title_, msg, sid, want in (
                ("daily", "预习", "把预习改到晚上十点", "t39-rt-d", REPEAT_DAILY),
                ("weekly", "钢琴课", "把钢琴课改到下午三点", "t39-rt-w", REPEAT_WEEKLY)):
            add_todo(title_, _sun(0), "14:00", "15:30", repeat=want)
            rr = cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                             "module": "planner"}).json()
            rc = [o for o in (rr.get("options") or []) if isinstance(o, dict)]
            c.check(f"[{tag}] 改期卡上还带着 repeat={want}",
                    [o.get("kind") for o in rc] == ["todo_add"] and rc[0].get("repeat") == want,
                    [(o.get("kind"), o.get("repeat")) for o in rc])
            c.check(f"[{tag}] 回话说清「仍然是」哪一档（改期是删旧加新，不说不像没动）",
                    ("仍然是每天" if want == REPEAT_DAILY else "仍然是每周")
                    in (rr.get("answer") or ""), (rr.get("answer") or "").replace("\n", " ")[:80])
            if rc:
                w = cli.post("/api/todos", json={"title": rc[0]["title"], "date": rc[0]["date"],
                                                 "start": rc[0]["start"], "end": rc[0]["end"],
                                                 "old_todo_id": rc[0].get("old_todo_id") or "",
                                                 "repeat": rc[0].get("repeat") or "",
                                                 "session_id": sid})
                got3 = [x for x in list_todos() if x["title"] == title_]
                c.check(f"[{tag}] 点【确认】之后库里那条**还是** {want}（旧的已删、新的只剩一天就是没接住）",
                        len(got3) == 1 and got3[0].get("repeat") == want,
                        [(x["id"], x.get("repeat"), x["date"]) for x in got3])

    # ==== G. 三份同步：判据一份 + 日历那几个点各滤一次 ====
    st = io.open(STUDENT, encoding="utf-8").read()
    pl = io.open(PLAN, encoding="utf-8").read()
    cf = io.open(CONFIRM, encoding="utf-8").read()
    mn = io.open(MAIN, encoding="utf-8").read()
    for name, src in (("student.html", st), ("plan.html", pl)):
        c.check(f"{name}：todoOccurs 只有**一个**定义（习惯也靠它，不另抄一份）",
                src.count("function todoOccurs") == 1, src.count("function todoOccurs"))
        c.check(f"{name}：todoOccurs 里有 daily 分支", 't.repeat === "daily"' in src)
        c.check(f"{name}：isHabit 是后端 HABIT_REPEATS 的翻译（表只有一份）",
                'const HABIT_REPEATS = ["daily"];' in src and "function isHabit" in src)
        c.check(f"{name}：todoDoneOn 对 daily 也按天记（勾一次≠整条完成）",
                't.repeat === "weekly" || t.repeat === "daily"' in src)
    c.check("student.html 三个日历渲染点都用 !isHabit 挡（周表/月表/下钻，少一处就是日程里冒出来）",
            st.count("!isHabit(t) && todoOccurs") == 3, st.count("!isHabit(t) && todoOccurs"))
    c.check("plan.html 同样三处", pl.count("!isHabit(t) && todoOccurs") == 3,
            pl.count("!isHabit(t) && todoOccurs"))
    # 没滤习惯的调用点**只允许有这一个**：红角标数的是"今天还要打卡的条数"，
    # 它必须把 daily 算进来。日历那几处全都得带 !isHabit（上面两条计数钉着）。
    c.check("student.html 不滤习惯的调用点只有角标那一个（别处漏一条=习惯钻进格子）",
            st.count("=> todoOccurs(") == 1 and "todoOccurs(t, todayIso)" in st,
            (st.count("=> todoOccurs("), "todoOccurs(t, todayIso)" in st))
    c.check("plan.html 没有不滤习惯的调用点（它只有日历，没有代办角标）",
            pl.count("=> todoOccurs(") == 0, pl.count("=> todoOccurs("))
    c.check("confirm.html 的候选卡把 daily 显出来（不显就是 surprise）",
            'p.repeat === "daily"' in cf and "每天重复" in cf)
    c.check("后端三处 repeat 白名单都认 daily（漏一处=前端传 daily 被退回一次性）",
            mn.count("(REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY)") == 3,
            mn.count("(REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY)"))
    # 角标：数的是"今天还没勾掉的"，必须含习惯，所以它问的是 todoOccurs 而不是被滤过的那份
    c.check("红角标在侧栏和代办标题各一颗，且数的是 todoOccurs（含习惯）",
            st.count('class="todo-badge"') == 2 and "todoOccurs(t, todayIso) && !todoDoneOn" in st,
            st.count('class="todo-badge"'))
    c.check("拉到待办的两处都刷角标（日程面板 + 代办列表）", st.count("updateTodoBadge(") == 3,
            st.count("updateTodoBadge("))
    return c


def _UNSUPPORTED_NOW():
    from app.modules.planner import _UNSUPPORTED_PERIOD
    return tuple(_UNSUPPORTED_PERIOD)


def main():
    results = [batch_a_recognition(), batch_b_title_and_veto(), batch_c_store_model(),
               batch_d_end_to_end(), batch_e_three_layers_synced(), batch_f_old_path_untouched(),
               batch_g_weekly_horizon(), batch_h_inverted_word_order(), batch_i_daily_habit()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 每周重复待办总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
