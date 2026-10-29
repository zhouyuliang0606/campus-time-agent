"""#77 的护栏：改期时这句话没点名，就去看上文那一条 —— 但**只有唯一匹配才许看**。

施工单 §3.1/§3.2/§3.3 给了 T1~T6 / E1~E3 / P1~P4 十二条验收，本批全覆盖，另加六条
是落地时实测出来的、单据没写但会咬人的东西（写在这里而不是只写在快照里，理由见每条注释）：

  · **子串冲突**（库里有「健身」又有「健身房」）：`_recent_todo_title` 会命中两个 title ⇒ 返回空。
    方向是安全的那边，但它是"口径"而不是"随手的结果" —— 以后有人想改成"取最长匹配"必须先看这条。
  · **占位名**（标题就叫「待办」的脏条目）不许被当成上文提到的事。
  · **span 只看最近 8 条**：翻太远会把学生早就改了主意的旧句子捞出来。
  · hint 不许盖掉**这句话自己带的名字**（单据 §五.1，P1 那一条在链路级也钉一遍）。
  · 新 helper 与 `_recent_add_request` **不许合并**（单据 §四.4：那边捞"要加什么"，这边捞"指哪条"）。
  · 那句骗人的「我找到好几条同名的」要**反向钉住**（`app/` 里出现即红），防止有人手滑改回去。
  · **#86 追记**：S4 原来钉的是「签名恰好三个参数 `[text, sem, hint_title]`、默认值 2 个」，
    #86 给这一函数加了第四个参数（`auto_slot`）⇒ 总数被合法改动顶红，改口成
    「**前三个的位置与名字不许动** + **除 `text` 外每个参数都带默认值**」—— 那才是这条本来要防的事
    （老调用点按位置传、不许因为新参数炸），而且以后加第五个参数不会再红一次。

⚠️ 三条自省（都栽过）：
  1. 每条"不许继承"的负向断言都配一句"它照旧列候选/照旧改对的那条"的正控，否则把 hint 整个删掉也全绿。
  2. 链路级判定用**上一轮的会话内容**当输入，所以一律 `append_conversation` 先种好再问 ——
     别依赖前一个用例留下的状态（#62 那条"真机状态要早于启动种进去"的教训）。
  3. E1 那张卡的 `old_todo_id` 只断言**第二轮**（改期提案自己带链路）；第三轮"换点"会把它丢掉是
     **既有缺陷**，本单不修也不掩盖，快照 §挂账 有实测读数。
"""
import ast
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, make_client, sandbox, pin_now,   # noqa: E402
                      seed_todos, write_timetable)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

FOUR = [
    {"title": "健身房", "date": "2026-10-26", "weekday": "周一", "start": "19:00", "end": "20:30"},
    {"title": "做题", "date": "2026-10-27", "weekday": "周二", "start": "14:00", "end": "16:00"},
    {"title": "约会", "date": "2026-10-30", "weekday": "周五", "start": "15:40", "end": "18:40"},
    {"title": "打篮球", "date": "2026-10-31", "weekday": "周六", "start": "09:00", "end": "10:30"},
]
TWO_SAME = [
    {"title": "健身", "date": "2026-10-26", "weekday": "周一", "start": "19:00", "end": "20:30"},
    {"title": "健身", "date": "2026-10-28", "weekday": "周三", "start": "09:00", "end": "10:00"},
]
SUBSTR = [
    {"title": "健身", "date": "2026-10-26", "weekday": "周一", "start": "19:00", "end": "20:30"},
    {"title": "健身房", "date": "2026-10-28", "weekday": "周三", "start": "09:00", "end": "10:00"},
]
COURSE = [{"day": 3, "start": "10:00", "end": "11:30", "course": "专业课", "location": "A302"}]


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def ask(cli, msg, sid):
    return cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                       "module": "planner"}).json()


def cards(d):
    return [o for o in (d.get("options") or []) if isinstance(o, dict)]


def plant(sid, *lines):
    """把"上文"摆好（人话：这些是学生**之前**说过的话，本句才是现在问的那句）。"""
    from app.store import append_conversation
    for i, t in enumerate(lines):
        append_conversation(sid, "user" if i % 2 == 0 else "assistant", t)


# --------------------------------------------------------------------------- A 判据
def batch_a_hint():
    from app.modules import planner as P
    from app.main import _recent_todo_title
    from app.store import append_conversation
    print("\n=== A 批 · 上文捞名字那半（判据 + 三条单据没写的边界）===")
    c = Checker()
    with sandbox():
        write_timetable(COURSE)
        seed_todos(FOUR)
        append_conversation("a1", "user", "健身房 价格")
        append_conversation("a1", "assistant", "会员一个月三百")
        c.check("A1 T1 上文只提「健身房」一条 → 就认它", _recent_todo_title("a1") == "健身房",
                repr(_recent_todo_title("a1")))
        append_conversation("a2", "user", "健身房 和 打篮球 哪个空")
        c.check("A2 T2 上文同时提两条 → 空串（**这是本单唯一的安全阀**，V2 射它）",
                _recent_todo_title("a2") == "", repr(_recent_todo_title("a2")))
        append_conversation("a3", "user", "食堂 多少钱")
        c.check("A3 T3 上文提的事库里没有 → 空串（不许硬塞一条）",
                _recent_todo_title("a3") == "", repr(_recent_todo_title("a3")))
        c.check("A4 会话是空的 → 空串且不崩（`any([])` 是 False 那一支）",
                _recent_todo_title("a4-none") == "", repr(_recent_todo_title("a4-none")))
        # 单据没写：名字互为子串时命中两个 title ⇒ 返回空。方向安全，但要钉成口径而不是巧合。
        seed_todos(SUBSTR)
        append_conversation("a5", "user", "健身房 几点开门")
        c.check("A5 子串冲突（库里有「健身」又有「健身房」、上文只说「健身房」）→ **不继承**："
                "两个 title 都算命中，`!= 1` 把它挡住了。以后想改成「取最长匹配」是改口径，要新单据",
                _recent_todo_title("a5") == "", repr(_recent_todo_title("a5")))
        # 占位名（标题就叫「待办」的脏条目）不许被当成上文提到的事
        seed_todos([{"title": "待办", "date": "2026-10-29", "weekday": "周四",
                     "start": "09:00", "end": "10:00"}])
        append_conversation("a6", "user", "把待办挪一下")
        c.check("A6 标题是占位名「待办」的那条**不算**上文提到过（否则会把占位名递进下游当名字用）",
                _recent_todo_title("a6") == "", repr(_recent_todo_title("a6")))
        # span：只有最近 8 条算
        seed_todos(FOUR)
        sid = "a7"
        append_conversation(sid, "user", "健身房 价格")
        for i in range(9):
            append_conversation(sid, "assistant" if i % 2 else "user", f"第 {i} 句无关的话")
        c.check("A7 只往前看 `span` 条（默认 8）：第九句之前提的名字不许捞",
                _recent_todo_title(sid) == "", repr(_recent_todo_title(sid)))
        c.check("A7-bis 同一条会话按默认窗口取最后 8 条时确实还在窗口内 → 捞得到（证明 A7 红的是窗口、不是数据坏了）",
                _recent_todo_title(sid, span=20) == "健身房",
                repr(_recent_todo_title(sid, span=20)))

        # 函数级 T4/T5/T6/P4/P1
        # ★ #81 改口：`("换个时间", hint="健身房")` 在 #77 那版是 `ok` 且**新三元组 == 旧三元组**
        #   （既没说日子也没报钟点，两处"没给就沿用旧值"同时生效）⇒ 那是一张"原地不动"的卡，
        #   点确认等于删一条再加一条同样的（而且提案不带 note/category，那两个字段会被抹掉）。
        #   现在它落进 `unclear`：**追问，不出卡**。原断言的意图（"hint 接手的就是那一条"）
        #   改由 `todos[0]` 验，不靠 proposal —— 见施工单 §3.1 T1。
        r4 = P.build_retime_existing_proposal("换个时间", hint_title="健身房")
        t4 = (r4.get("todos") or [{}])[0]
        c.check("A8 (#81 改口) hint=健身房 + 这句没给新时间 → **unclear**，且认领的就是那条",
                r4.get("status") == "unclear" and t4.get("title") == "健身房"
                and t4.get("date") == "2026-10-26",
                str((r4.get("status"), t4.get("title"), t4.get("date"))))
        c.check("A8-bis (#81) 这个 unclear 带 `old_label`（main 那句追问要念旧值，不许自己再算一遍）"
                " —— 与另两种 unclear 来源（挑不出空档 / 钟点不合法）的 `todos:[]` 无 `old_label` 不同",
                (r4.get("old_label") or "").startswith("2026-10-26")
                and "19:00-20:30" in (r4.get("old_label") or "")
                and "proposal" not in r4,
                repr(r4.get("old_label")))
        c.check("A8-ter (#81 正控 T5) 报了**新钟点**就不是空改动：「健身房改到 20:00」仍 ok、日子不变",
                (lambda p: p.get("status") == "ok" and (p.get("proposal") or {}).get("date") == "2026-10-26"
                 and (p.get("proposal") or {}).get("start") == "20:00")(
                    P.build_retime_existing_proposal("健身房改到 20:00")),
                str((P.build_retime_existing_proposal("健身房改到 20:00").get("status"),)))
        c.check("A8-quater (#81 正控 T6) **换了日子没报钟点**也不是空改动：「约会挪到周日」仍 ok、日子变"
                "（约会本来在 10-30 周五；用「挪到周五」就是原地不动，测不到这一档）",
                (lambda r: r.get("status") == "ok"
                 and (r.get("proposal") or {}).get("date") != "2026-10-30")(
                    P.build_retime_existing_proposal("约会挪到周日")),
                str(P.build_retime_existing_proposal("约会挪到周日").get("status")))
        c.check("A8-quinquies (#81 §2.2) 报了**跟旧值一模一样的三元组**也算空改动："
                "「约会改到 15:40-18:40」→ unclear（这条就是判据不能放宽成「只查没说日子」的那块证据。"
                "注意只报 start 不算：「约会改到 15:40」会把 end 补成 16:40 ≠ 旧的 18:40，那是有效改动）",
                P.build_retime_existing_proposal("约会改到 15:40-18:40").get("status") == "unclear",
                str(P.build_retime_existing_proposal("约会改到 15:40-18:40").get("status")))
        c.check("A8-sexies (#81) 另一种 unclear 来源**没被本单改动**：「约会改到 16:00-16:00」"
                "仍是 `unclear` 且**不带 `old_label`**（main 那句「没听清」照旧，别替它编现状）",
                (lambda r: r.get("status") == "unclear" and "old_label" not in r
                 and r.get("todos") == [])(P.build_retime_existing_proposal("约会改到 16:00-16:00")),
                str(sorted(P.build_retime_existing_proposal("约会改到 16:00-16:00").keys())))
        r5 = P.build_retime_existing_proposal("换个时间")
        c.check("A9 T5 不传 hint → **逐字保持旧行为**：many + 四条全列（不许悄悄改成 empty/ok）",
                r5.get("status") == "many" and len(r5.get("todos", [])) == 4,
                str((r5.get("status"), len(r5.get("todos", [])))))
        c.check("A10 P4 老调用形状（只传两个参数）与新默认值同解：参数必须带默认值，别处接进来不踩空",
                P.build_retime_existing_proposal("换个时间", None).get("status") == "many", "")
        seed_todos(TWO_SAME)
        r6 = P.build_retime_existing_proposal("换个时间", hint_title="健身")
        c.check("A11 T6 库里两条同名 + hint=健身 → many（命中 2 条就是 2 条，不许替他挑一条）",
                r6.get("status") == "many" and len(r6.get("todos", [])) == 2,
                str((r6.get("status"), len(r6.get("todos", [])))))
        seed_todos(FOUR)
        rp1 = P.build_retime_existing_proposal("把做题挪到周日", hint_title="健身房")
        pp1 = rp1.get("proposal") or {}
        c.check("A12 P1（函数级）这句话自己带了名字 → hint **完全不参与**：改的是做题，不是健身房",
                rp1.get("status") == "ok" and pp1.get("title") == "做题",
                str((rp1.get("status"), pp1.get("title"))))
        rh = P.build_retime_existing_proposal("换个时间", None, "  约会  ")
        c.check("A13 hint 两边空白会被 strip 后用（传进来的形状不该影响判据），空串一律不参与"
                " —— #81 之后这句是空改动，所以「用的是哪个名字」改由 `todos[0]` 验，不再靠 proposal",
                (rh.get("status") == "unclear"
                 and (rh.get("todos") or [{}])[0].get("title") == "约会"
                 and P.build_retime_existing_proposal("换个时间", None, "   ").get("status") == "many"),
                str(((rh.get("status"), (rh.get("todos") or [{}])[0].get("title")),
                     P.build_retime_existing_proposal("换个时间", None, "   ").get("status"))))
    return c.summary("A 批（上文捞名字）")


# --------------------------------------------------------------------------- B 链路
def batch_b_link():
    print("\n=== B 批 · 端到端（E1 出改期卡 / E2·E3 文案拆两种）===")
    c = Checker()
    with sandbox():
        write_timetable(COURSE)
        seed_todos(FOUR)
        cli = make_client()
        with pin_now(9, 0):
            sid = "b-e1"
            plant(sid, "健身房 价格", "会员一个月三百")
            # ★ #81 换句（不是改期望）：原来这句「换个时间」在 #81 之后走追问、不出卡，
            #   拿它当"上文捞名字"的端到端证据就测不到了。换句必须**句子自己不带名字**（否则
            #   hint 不参与，A12 钉着），又要**给一个真改动**。施工单 §1.4 给的「改到 20:00」
            #   实测**不成立**（`_pick_todo_name` 把「20:00」当名字 ⇒ hint 不参与 ⇒ status=empty），
            #   所以这里给日子不给钟点：「挪到周日」⇒ 日子变、钟点由空档挑 ⇒ 有效改动。
            d = ask(cli, "挪到周日", sid)
            k = cards(d)
            c.check("B1 E1 上文只提「健身房」+ 这句「挪到周日」（#81 换句）→ 出**改期确认卡**（不再是四条清单）",
                    len(k) == 1 and k[0].get("kind") == "todo_add" and k[0].get("title") == "健身房"
                    and bool(k[0].get("old_todo_id")),
                    str([(o.get("kind"), o.get("title"), o.get("old_todo_id")) for o in k])[:130])
            c.check("B1-bis 那张卡里没有出现「同名的」那句，也不再倒出四条清单",
                    "同名的" not in (d.get("answer") or "")
                    and (d.get("answer") or "").count("·") == 0,
                    (d.get("answer") or "")[:90].replace("\n", " ⏎ "))
            c.check("B1-c 轨迹走的是改期那一支（🕘 改期提案），不是让渡/兜底",
                    any("改期" in str(t.get("phase") or "") for t in (d.get("trace") or [])),
                    str([t.get("phase") for t in (d.get("trace") or [])])[:120])
            n0 = len(__import__("app.store", fromlist=["list_todos"]).list_todos())
            c.check("B1-d 只出提案、**一个字节都不写库**（库里还是那四条）", n0 == 4, str(n0))

            # ★ #81 自己那一条：与 B1 **成对**存在。只有 B1 会漏掉空改动，只有 B1-e 会丢掉"捞名字"的验证。
            sid1e = "b-e1-noop"
            plant(sid1e, "健身房 价格", "会员一个月三百")
            d1e = ask(cli, "换个时间", sid1e)
            a1e = d1e.get("answer") or ""
            c.check("B1-e (#81) 同样有上文、但这句没给新时间 → **不出卡**、话术里念出旧值 19:00-20:30",
                    not cards(d1e) and "19:00-20:30" in a1e and "健身房" in a1e
                    and "没听清" not in a1e,
                    a1e.replace("\n", " ⏎ ")[:130])
            c.check("B1-e-bis (#81) 这一支仍是确定性：轨迹有「🕘 改期新时间缺失」、追问不挂暂存",
                    any("改期新时间缺失" in str(t.get("phase") or "") for t in (d1e.get("trace") or []))
                    and d1e.get("awaiting_choice") is False,
                    str([t.get("phase") for t in (d1e.get("trace") or [])])[:110])
            c.check("B1-e-ter (#81) 追问之后库里仍四条、也没多出一张「原地不动」的卡",
                    len(__import__("app.store", fromlist=["list_todos"]).list_todos()) == 4, "")

            # ★ #81 的另一半：`unclear` 有**两种来源**，话术必须各说各的。
            #   这一句是"钟点不合法"那一类（`todos:[]`、没有 `old_label`）——
            #   它**不许**被套进"念旧值"那句（那等于替学生编一个现状）。
            sid1f = "b-e1-unclear"
            d1f = ask(cli, "约会改到 16:00-16:00", sid1f)
            a1f = d1f.get("answer") or ""
            c.check("B1-f (#81 另一来源) 钟点不合法那句仍走「没听清」，**不念旧值、不出卡**",
                    not cards(d1f) and "没听清" in a1f and "15:40-18:40" not in a1f,
                    a1f.replace("\n", " ⏎ ")[:110])

            sid2 = "b-e2"
            d2 = ask(cli, "换个时间", sid2)
            ans2 = d2.get("answer") or ""
            c.check("B2 E2 新会话（无上文）→ 列候选，文案是「你要改的是哪一条？」，"
                    "**不许**出现「同名的」（那四条各不相同，那句在骗人）",
                    "同名的" not in ans2 and "你要改的是哪一条" in ans2
                    and ans2.count("·") == 4, ans2.replace("\n", " ⏎ ")[:120])
            c.check("B2-bis 这一支仍是确定性（options 为空、没让渡给引擎）",
                    not cards(d2) and d2.get("awaiting_choice") is False,
                    str([cards(d2), d2.get("awaiting_choice")])[:80])

            seed_todos(TWO_SAME)
            sid3 = "b-e3"
            d3 = ask(cli, "换个时间", sid3)
            ans3 = d3.get("answer") or ""
            c.check("B3 E3 两条**真同名**「健身」+ 没说名字 → 文案说「都叫「健身」」（这时这句话才是真的）",
                    "都叫「健身」" in ans3 and ans3.count("·") == 2, ans3.replace("\n", " ⏎ ")[:130])

            seed_todos(FOUR)
            sid4 = "b-p2"
            plant(sid4, "约会 和 做题 都要改", "你说改哪个")
            d4 = ask(cli, "换个时间", sid4)
            c.check("B4 P2 上文同时提两条 → 照旧列候选，**不许瞎猜**其中一条（链路级，跟 A2 同一把尺）",
                    "你要改的是哪一条" in (d4.get("answer") or "") and not cards(d4),
                    (d4.get("answer") or "").replace("\n", " ⏎ ")[:110])

            sid5 = "b-p1"
            plant(sid5, "健身房 价格", "会员一个月三百")
            d5 = ask(cli, "把做题挪到周日", sid5)
            k5 = cards(d5)
            c.check("B5 P1（链路级）上文全是健身房、这句自己点了「做题」→ 改的必须是做题",
                    len(k5) == 1 and k5[0].get("title") == "做题",
                    str([(o.get("kind"), o.get("title")) for o in k5])[:110])

            sid6 = "b-p3"
            d6 = ask(cli, "换个时间", sid6)
            c.check("B6 P3 新会话这句不报错、不返回「没找到」：many 走列候选（HTTP 200 由 client 保证）",
                    "没找到" not in (d6.get("answer") or "") and "·" in (d6.get("answer") or ""),
                    (d6.get("answer") or "").replace("\n", " ⏎ ")[:100])
    return c.summary("B 批（端到端）")


# --------------------------------------------------------------------------- C 正控
def batch_c_no_regression():
    print("\n=== C 批 · 正控（本单第一指标是误伤：既有链路一条不许改坏）===")
    c = Checker()
    with sandbox():
        write_timetable(COURSE)
        seed_todos(FOUR)
        cli = make_client()
        with pin_now(9, 0):
            for msg, want in (("把约会改到周六", "约会"), ("做题挪到周日", "做题")):
                d = ask(cli, msg, f"c-{msg}")
                k = cards(d)
                c.check(f"C1 带名字的改期「{msg}」照旧出卡、改的就是那条",
                        len(k) == 1 and k[0].get("title") == want,
                        str([(o.get("kind"), o.get("title")) for o in k])[:100])
            # 「换个时间」在**没提过任何事名**的会话里必须走列候选；上面 B2 已钉。
            # 这里钉两头的：库里两条以上时不许硬挑；库里只有一条时"直接改它"是 #77 之前就有的行为
            seed_todos([{"title": "约会", "date": "2026-10-30", "weekday": "周五",
                         "start": "15:40", "end": "18:40"},
                        {"title": "做题", "date": "2026-10-27", "weekday": "周二",
                         "start": "14:00", "end": "16:00"}])
            sid = "c-none"
            plant(sid, "食堂 多少钱", "窗口在二楼")
            d2 = ask(cli, "换个时间", sid)
            c.check("C2 两条待办、上文一条都没提 → 不许硬挑，照旧列候选（hint 返回空串）",
                    "你要改的是哪一条" in (d2.get("answer") or "") and not cards(d2),
                    (d2.get("answer") or "").replace("\n", " ⏎ ")[:110])
            from app.modules import planner as P
            seed_todos([{"title": "约会", "date": "2026-10-30", "weekday": "周五",
                         "start": "15:40", "end": "18:40"}])
            r_only = P.build_retime_existing_proposal("换个时间")
            c.check("C2-bis (#81 改口) 库里**只有一条** + 这句没给新时间 → 追问，不出那张「原地不动」的卡。"
                    "（#77 之前这里是 `ok` 且新值==旧值 —— 那条注释说的「让改成追问的人先看到一条红」"
                    "现在兑现了：红的是期望本身，不是判据坏了）",
                    r_only.get("status") == "unclear"
                    and (r_only.get("todos") or [{}])[0].get("title") == "约会"
                    and "proposal" not in r_only,
                    str((r_only.get("status"), (r_only.get("todos") or [{}])[0].get("title"))))
            r = P.build_retime_existing_proposal("换个时间", hint_title="约会")
            c.check("C3 (#81 改口) 库里就一条 + hint=约会 → 仍是 unclear，认领的是约会",
                    r.get("status") == "unclear"
                    and (r.get("todos") or [{}])[0].get("title") == "约会",
                    str((r.get("status"), (r.get("todos") or [{}])[0].get("title"))))
            c.check("C3-bis (#81 正控) 库里就一条、这句**报了新钟点** → 仍 ok（判据不许把有效改动拦掉）",
                    (lambda x: x.get("status") == "ok"
                     and (x.get("proposal") or {}).get("start") == "20:00")(
                        P.build_retime_existing_proposal("约会改到 20:00")),
                    str(P.build_retime_existing_proposal("约会改到 20:00").get("status")))
            c.check("C4 残渣一族没被本单改坏：「改一下时间」仍回没找到那一族（hint 救不到，见快照挂账）",
                    P.build_retime_existing_proposal("改一下时间").get("status") == "empty",
                    str(P.build_retime_existing_proposal("改一下时间").get("status")))
    return c.summary("C 批（正控）")


# --------------------------------------------------------------------------- S 静态
def batch_s_static():
    print("\n=== S 批 · 静态接线（形状与位置）===")
    c = Checker()
    mn = read("app/main.py")
    pn = read("app/modules/planner.py")
    tree = ast.parse(mn)
    names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    c.check("S1 `_recent_todo_title` 全项目定义恰好一处，且就放在 `_recent_add_request` 旁边（同一族）",
            names.count("_recent_todo_title") == 1 and names.count("_recent_add_request") == 1
            and names.index("_recent_todo_title") == names.index("_recent_add_request") + 1,
            str(names[names.index("_recent_add_request") - 1:names.index("_recent_todo_title") + 1]))
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_recent_todo_title"), None)
    seg = ast.get_source_segment(mn, fn) or ""
    c.check("S2 安全阀写死在判据里：函数体里有 `len(hit_titles) != 1` 与 `[-span:]`（V2/V5 的靶子）",
            "len(hit_titles) != 1" in seg and "[-span:]" in seg, seg[-120:])
    c.check("S2-bis span 有默认值 8 且**只有这一个默认参数**（调用点不传参数，改窗口只改一处）",
            "span: int = 8" in seg and len(fn.args.defaults) == 1
            and [a.arg for a in fn.args.args] == ["session_id", "span"],
            str((ast.unparse(fn.args.defaults), [a.arg for a in fn.args.args])))
    c.check("S3 调用点只有一处递 hint（`hint_title=_recent_todo_title(session_id)` 恰 1 处，V4 射它）",
            mn.count("hint_title=_recent_todo_title(session_id)") == 1,
            str(mn.count("hint_title=_recent_todo_title(session_id)")))
    c.check("S3-bis main 里带括号的 `build_retime_existing_proposal(` 恰好一处 —— 就是这一支的调用点"
            "（import 那行写的是名字、不带括号，所以我第一版按 2 去数是错的）",
            mn.count("build_retime_existing_proposal(") == 1,
            str(mn.count("build_retime_existing_proposal(")))
    pt = ast.parse(pn)
    lf = next((n for n in ast.walk(pt) if isinstance(n, ast.FunctionDef)
               and n.name == "build_retime_existing_proposal"), None)
    argnames = [a.arg for a in lf.args.args]
    c.check("S4 前三个参数仍叫 `text` / `sem` / `hint_title`、顺序没动（老调用点按位置传，"
            "改名或换序会炸），且除 `text` 外每个参数都带默认值 —— 后来加的参数"
            "（#77 的 hint_title、#86 的 auto_slot）都不许把老调用点炸掉。"
            "⚠️ #86 改口：原来钉 `argnames == [text, sem, hint_title]` 且 `defaults == 2`，"
            "签名合法长了第四个参数 ⇒ 钉**前三个的位置与名字** + **默认值覆盖率**，不钉参数总数",
            argnames[:3] == ["text", "sem", "hint_title"]
            and len(lf.args.defaults) == len(argnames) - 1,
            str((argnames, len(lf.args.defaults))))
    lseg = ast.get_source_segment(pn, lf) or ""
    c.check("S5 接手那一行有前置条件（`not name or name in (...)`）：hint 永远不许盖掉这句话自带的名字",
            "if not name or name in (" in lseg
            and lseg.index("if not name or name in (") < lseg.index("pool = list_todos()"),
            lseg[lseg.find("if not name or name in ("):lseg.find("if not name or name in (") + 90])
    c.check("S5-bis 那一行在 planner 里只出现一次（挂两处就会让 hint 参与两次判断）",
            lseg.count("name = (hint_title or \"\").strip()") == 1,
            str(lseg.count("name = (hint_title or \"\").strip()")))
    c.check("S6 那句骗人的话术已经没了，并被反向钉住（`我找到好几条同名的` 在 main/planner 里 0 处）",
            "我找到好几条同名的" not in mn and "我找到好几条同名的" not in pn, "")
    c.check("S7 两种 many 各有自己的文案：`都叫` 那一支与「你要改的是哪一条」都在，且 `len(_rt_titles) == 1` 恰一处",
            mn.count("len(_rt_titles) == 1") == 1 and "都叫" in mn and "你要改的是哪一条" in mn,
            str((mn.count("len(_rt_titles) == 1"), "都叫" in mn)))
    c.check("S8 新 helper 没去调 `_recent_add_request`（单据 §四.4：两件事不是一回事，别合并）",
            "_recent_add_request(" not in seg, seg[-80:])
    c.check("S9 占位名名单两份逐字相同（main 那份与 planner 那份）——**没有共享常量**，所以只能这样钉："
            "以后加一颗占位名要改两处，改一处就漂（挂账里给了共享常量的建议）",
            '"", "待办", "日程", "事项"' in seg and '", "待办", "日程", "事项")' in pn, "")
    return c.summary("S 批（静态接线）")


def main():
    # `Checker.summary()` 返回的是**退出码**（0=全过、1=有红），所以这里用 `|=` 累加，
    # 别写成 `if not fn(): code = 1` —— 那种写法把"全过"当成失败（我自己第一版就这么错的）。
    code = 0
    for fn in (batch_a_hint, batch_b_link, batch_c_no_regression, batch_s_static):
        try:
            code |= fn()
        except Exception as exc:      # noqa: BLE001
            print(f"❌ {fn.__name__} 崩了：{type(exc).__name__}: {exc}")
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
