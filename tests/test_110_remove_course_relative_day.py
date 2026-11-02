# -*- coding: utf-8 -*-
"""#110：学生说「删除**明天**早上第一节课」，系统只认「周二」——一句话整条路提前断了。

根因就一行（`parse_remove_course`）：`_pick_day` 只认周X，返回 None 就直接 `return None`，
后面四层课名解析一层都没跑，学生收到一句"想删哪一节"。
修法是**兜一层**：`_pick_day` 没认出来时，用全项目唯一的相对日期表 `_pick_date` 拿日子，
再换算成周几（换算用现成的 `_iso_weekday`，不再抄一份 `fromisoformat`）。

**这是补回归，不是加功能**：`636fc2e`（09-25 01:23）把删课从"模型调工具"改成确定性分支之前，
`main.py` 里压根没有删课分支（`git show 636fc2e^:app/main.py` 只剩一行提示词注释），
那句话原样交给模型，模型自己把「明天」换算成 day=2 —— 认得。改成代码认之后，
"认哪些说法"跟着 `_pick_day` 一起收窄了，而 `636fc2e` 同批的测试全是「周X」写法，一条都没照到。

三件事决定了这些断言怎么写：

① **日期一律现算**（`datetime.date.today()` 往后推），不许写死「周二」。
   写死的话过几天日历一翻就假红 —— 判据是「明天」和「明天的那个周X」**必须同解**（T6）。

② 起手**自种周表**，不绑任何人的真实课表。施工单 §3.1 让先 `set_current_student("admin1")`
   再跑，那是拿"用户今天传了什么课表"当判据的地基（本机绿、克隆红）；这里每天种两节带周几的
   名字，`_spath` 在没有身份时退回老位置 `student/`，沙箱里种进去就行。

③ 每次解析都走 `_go()`：**崩了要变成一条红，不许变成"整批没跑完"**。
   实测 M3（把换算那步的保险摘掉）第一版就是靠 TypeError 把 B 批打断的 ——
   报告是 `崩=True 红=[]`，差一点被读成"T7 没牙"。改造之后 M3 红 3 条（T5/T7/T12），零崩。

两条**已知哑弹**与一条**新补的闸**（都实测过，别照着施工单的表以为它们有牙）：
  · 施工单 M2「把兜底挪到 `_pick_day` 前面 ⇒ T4 红」—— **实测不红**。`_pick_date` 对「周一」回的是
    下一个周一，可那天的 `isoweekday()` 照样是 1：换算成"周几"这一步天生抹掉了是哪一周。
    顺序颠倒在这里没有任何可观测差别。
  · 施工单 M3 的**机制说反了**：它说"遇到『明晚』那种返回格式就崩"，实测 `_pick_date` 对
    「明晚/今晚」回的都是干净的 ISO（'2026-11-03'）。真会崩的是**日历上不存在的日子**
    （「2月30日」→ '2026-02-30'），换了这一发子弹 T7 才有牙。
  · 顶上那条"别塞进 `_pick_day`"要真量一次：M7 把兜底整个搬进 `_pick_day`，
    **16 条里只有 T9 红**（其余各条结果逐字相同）—— 所以 T9 不是装饰，它是那条红线的唯一哨兵。
    同理 T1-bis 必须额外要求"两边都非空"：M1 那一版两句都返回 None，`None == None` 照样绿。
"""
import datetime

from _harness import Checker, make_client, sandbox, title, write_timetable

WD = "一二三四五六日"


def _seed_week():
    """每天两节：08:00 与 10:00，名字里带周几 + 第几门。

    为什么一天两节而不是只种 08:00：②「第N节」和④「那天只有一节课」是两条不同的路，
    一天两节能把④关掉 —— 免得"没说哪节"也被反查蒙过去。
    ⚠️ #113 之后「第二节」= 那天**第二门**（10:00 那节），不再是"作息表第二节 = 08:00"。
    这一份种下去正好让"第N门"和旧"第N小节"两种口径**给出不同答案**，
    所以这几条断言同时是 #113 的回归靶（旧口径下 T3/T2-bis 会红）。
    """
    return ([{"day": d, "start": "08:00", "end": "09:00",
              "course": f"周{WD[d - 1]}第一门", "location": "A101"} for d in range(1, 8)]
            + [{"day": d, "start": "10:00", "end": "11:00",
                "course": f"周{WD[d - 1]}第二门", "location": "A102"} for d in range(1, 8)])


def _course(day: int) -> str:
    """那天**第一门**（08:00）叫什么。"""
    return f"周{WD[day - 1]}第一门"


def _second(day: int) -> str:
    """那天**第二门**（10:00）叫什么（#113：「第二节」现在指这一门）。"""
    return f"周{WD[day - 1]}第二门"


def _rel(day_off: int) -> int:
    return (datetime.date.today() + datetime.timedelta(days=day_off)).isoweekday()


def _go(fn, *a):
    """跑一次，把"抛异常"当成返回值带回来（文件头 ③：崩必须是一条红，不是一批没跑完）。"""
    try:
        return fn(*a), ""
    except Exception as e:                       # noqa: BLE001 - 这些断言要的就是"不许抛"
        return None, f"{type(e).__name__}: {e}"


def _sum(parse, sentence):
    """→ (summary 或 None, 崩溃说明)。断言一律写成 `not 崩 and summary 对`。"""
    r, boom = _go(parse, sentence)
    if boom:
        return None, boom
    return ((r or {}).get("summary") if isinstance(r, dict) else r), ""


def batch_a_relative_words():
    c = Checker()
    title("A. 「明天/今天/后天/明早」这些说法现在解析得出来了（T1~T3）")
    with sandbox():
        write_timetable(_seed_week())
        from app.modules.planner import parse_remove_course as PR

        tmr = _rel(1)
        s, boom = _sum(PR, "删除明天早上第一节课")
        c.check(f"T1 「删除明天早上第一节课」出提案，而且点的是**明天那天**（周{WD[tmr - 1]}）的课",
                not boom and bool(s) and s.startswith(f"删除周{WD[tmr - 1]}") and _course(tmr) in s,
                boom or (s or "None"))
        s2, b2 = _sum(PR, "删除明早第一节课")
        c.check("T1-bis 「删除明早第一节课」与「明天」**都出提案**且同一解"
                "（「明早」在词表里是单独一条，M6 钉这一族。两边都是 None 也算『相同』—— "
                "所以这条必须另外要求非空，M1 实测就是这么蒙过去的）",
                not b2 and bool(s) and bool(s2) and s2 == s, b2 or f"{s2!r} vs {s!r}")

        today_d, after_d = _rel(0), _rel(2)
        s3, b3 = _sum(PR, "删除今天第一节")
        c.check(f"T2 「删除今天第一节」→ 周{WD[today_d - 1]}（『今天』的偏移是 0，不是 1）",
                not b3 and bool(s3) and _course(today_d) in s3, b3 or str(s3))
        s4, b4 = _sum(PR, "删除后天上午第二节课")
        c.check(f"T2-bis 「删除后天…第二节课」→ 周{WD[after_d - 1]}（『后天』= +2）；"
                "#113 之后『第二节』指那天**第二门**（10:00），不是作息表第二节(08:00)",
                not b4 and bool(s4) and _second(after_d) in s4 and "10:00" in s4, b4 or str(s4))

        for word, n in (("周二", 2), ("周一", 1)):
            sa, ba = _sum(PR, f"删除{word}第一节")
            sb, bb = _sum(PR, f"删除{word}第二节")
            c.check(f"T3 「删除{word}第一/第二节」按**第几门**分开了（#113 改口："
                    "旧口径两节都换算到 08:00 同一门，学生说第二节拿到的是第一门的卡）",
                    not (ba or bb) and sa == f"删除{word} 08:00 的《{_course(n)}》"
                    and sb == f"删除{word} 10:00 的《{_second(n)}》" and sa != sb,
                    ba or bb or f"{sa!r} / {sb!r}")
    return c


def batch_b_pairing_and_red_lines():
    c = Checker()
    title("B. 同解 / 追问 / 红线（T4~T10）")
    with sandbox():
        write_timetable(_seed_week())
        from app.modules import planner as PL

        tmr = _rel(1)
        a, b1 = _sum(PL.parse_remove_course, "删除明天早上第一节课")
        b, b2 = _sum(PL.parse_remove_course, f"删除周{WD[tmr - 1]}早上第一节课")
        c.check("T6 ★回归闸：「明天」与「明天的那个周X」**逐字相同**"
                "（只测非 None 的话，周几换算写成 `.weekday()`（周一=0）也能蒙过去 —— "
                "M5 实测过：那一版出的是「删除周一…」，光看有没有出提案照样绿）",
                not (b1 or b2) and a is not None and a == b, f"{a} | {b} | {b1}{b2}")

        d0 = _rel(0)
        x, bx = _sum(PL.parse_remove_course, f"删除周{WD[d0 - 1]}第一节")
        c.check(f"T4 今天就是周{WD[d0 - 1]}：说「周{WD[d0 - 1]}第一节」指的仍是**今天这一条**"
                "（钉的是『别把 _pick_day 整段删了只留 _pick_date』—— 顺序颠倒本身它钉不住，"
                "见文件头 M2 那条哑弹说明）",
                not bx and x == f"删除周{WD[d0 - 1]} 08:00 的《{_course(d0)}》", bx or str(x))

        y, by = _sum(PL.parse_remove_course, "删除第一节课")
        c.check("T5 没说哪天仍然拒：「删除第一节课」→ None（不许拿今天瞎猜）",
                not by and y is None, by or str(y))
        p1, e1 = _sum(PL.parse_remove_course, "删除今天下午的课")
        p2, e2 = _sum(PL.parse_remove_course, "把明天的课删了")
        c.check("T10 「删除今天下午的课」「把明天的课删了」照旧追问"
                "（那天好几节又没说哪节，本单治的是『知道哪节、只是用相对日期说』）",
                not (e1 or e2) and p1 is None and p2 is None, f"{e1}{e2} {p1} {p2}")

        z, bz = _sum(PL.parse_remove_course, "删除2月30日第一节课")
        c.check("T7 日历上不存在的日子不许把请求打挂：「删除2月30日第一节课」→ None，**不是 ValueError**"
                "（`_pick_date` 不做日历校验，实测它回 '2026-02-30'；换算那一步的保险"
                "真正的用处在这儿，而不是施工单自己说的『明晚那种格式』）",
                z is None and not bz, bz or str(z))

        pd, _ = _go(PL._pick_day, "删除明天第一节")
        pd2, _ = _go(PL._pick_day, "删除周二第一节")
        c.check("T9 ★红线闸：兜底**没有**塞进 `_pick_day` —— 它照旧只认周X（它另外 4 个调用点是"
                "加课那一路 `wants_add_course` / `parse_add_course`、删课意图 `wants_remove_course`、"
                "main 的一处分支判据，给它加相对日期等于顺手改了那四处的语义）",
                pd is None and pd2 == 2, f"明天→{pd} 周二→{pd2}")

        far = datetime.date.today() + datetime.timedelta(days=30)
        fw = far.isoweekday()
        w, bw = _sum(PL.parse_remove_course, f"删除{far.isoformat()}第一节")
        c.check("T8 口径登记：任何**日期**说法（含绝对日期）都换算成那一天的周几 —— "
                "周表按周循环，没有『某一天』可删，只能删那条每周重复；"
                "以前这些句子一律追问，本单起会出提案（行为面扩大，照实钉住免得日后当 bug 改回去）",
                not bw and w == f"删除周{WD[fw - 1]} 08:00 的《{_course(fw)}》", bw or str(w))
    return c


def batch_c_end_to_end():
    c = Checker()
    title("C. 链路可达（函数级对 ≠ 分支到得了，#74 那一课）")
    with sandbox():
        write_timetable(_seed_week())
        client = make_client()
        from app import store as S
        n_full = len(S.get_timetable())

        tmr = _rel(1)

        def _post(msg, sid):
            try:
                return client.post("/api/chat", json={
                    "message": msg, "module": "planner", "session_id": sid}).json(), ""
            except Exception as e:               # noqa: BLE001 - 服务端崩也要变成一条红
                return {}, f"{type(e).__name__}: {e}"

        d, boom = _post("删除明天早上第一节课", "s110-a")
        opts = d.get("options") or []
        c.check("T11 学生原话一句就到提案：/api/chat 回的是删课提案卡，不是『想删哪一节』",
                not boom and any(o.get("kind") == "timetable_change" for o in opts)
                and _course(tmr) in str(opts[-1].get("summary", "")),
                boom or (d.get("answer") or "")[:80])
        c.check("T11-bis 出提案阶段周表一个字节没动（本函数只算，写库要等确认）",
                len(S.get_timetable()) == n_full, f"{len(S.get_timetable())} vs {n_full}")

        d2, boom2 = _post("删除第一节课", "s110-b")
        c.check("T12 没说哪天那句仍走追问档（兜底没有把拒的那一档一起放过）",
                not boom2 and not (d2.get("options") or []) and "想删哪一节" in (d2.get("answer") or ""),
                boom2 or (d2.get("answer") or "")[:80])
    return c


if __name__ == "__main__":
    code = 0
    for fn in (batch_a_relative_words, batch_b_pairing_and_red_lines, batch_c_end_to_end):
        code |= fn().summary(f"test_110 {fn.__name__} ")
    raise SystemExit(code)
