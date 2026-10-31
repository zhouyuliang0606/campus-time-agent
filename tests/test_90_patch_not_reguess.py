"""#73 的护栏：补充/纠正句必须"只补明说的几项"，不许再整句重猜。

批次分工（跑变异时看**红的是哪几条名字**，别看总数）：

  A 批 · 链路（HTTP，两句一轮）：学生看着卡改次数/时长之后，卡上的**事名、次数、单次
      时长三项同时**保住。#73 的核心事故是"修好次数、弄坏事名"，所以每条都同时钉三项 ——
      只验一项就会放过另一半（单据 §3.1 的原话，实测也确实各会漏一边）。
  B 批 · 时长四级优先（函数级）：T4 故意把卡上 duration 设成 30、学生这句说 60，
      **期望 60** —— 这样"哪一级生效"也被钉住；两个数一样就白测。
  C 批 · 卡片存参数：`build_todo_slots` 有没有真把 times/duration/origin 写进卡，
      以及**普通分支**（不说"周"、说了具体哪天那一条）有没有传 times。
  D 批 · 补丁判据：`parse_todo_patch` 的窄口 + 空补丁 + §一.4 的三条判据与新单否决。
  E 批 · 事名判真门：计数残渣（每次/三次/次每次）不算名字，而「二次元」「第一次」
      「下次」这些真名字**不许被误杀**（这是 `fullmatch` 而不是 `search` 的唯一证据）。
  S 批 · 静态接线：堵「(c) 的假绿」——函数级全绿不代表调用点把 `duration` 传下去了。

⚠️ 三条自省（都在这一单里踩过或单据点过）：
  1. **`weekly_times` 全局行为一个字都没放宽**（§四.2）。窄口只开在 `parse_todo_patch` 里，
     所以 D4 用「我去了三次图书馆」这条**陈述句**钉住它没被波及。
  2. **期望为空/不含某字样必须配一份"它本该出现"的正控**（A5 / D3 / E3）。
  3. A 批依赖沙箱那个空周表学生；断言全部写成**性质**（times/preselect/minutes/title），
     不写死段数 —— 段数会随库里的课和待办漂。
"""
import datetime
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, make_client, sandbox  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

c = Checker()


def _next_sat() -> str:
    """今天**之后**的下一个周六（不含今天）。

    「改成周六」的解析基准是今天：今天周三就取本周六，今天正好周六就取下一周。
    以前这里把期望写死成 `"2026-10-31"`，走到那天（2026-10-31 恰好是周六）就永久红 ——
    断言不该知道日历停在哪一页。`% 7 or 7` 就是"本周六已过/正是当天 ⇒ 取下一周"。
    """
    t = datetime.date.today()
    return (t + datetime.timedelta(days=(5 - t.weekday()) % 7 or 7)).strftime("%Y-%m-%d")


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def ask(cli, msg, sid):
    d = cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                    "module": "planner"}).json()
    return d


def one_card(d):
    opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
    return opts[0] if opts else None


def cards(d):
    return [o for o in (d.get("options") or []) if isinstance(o, dict)]


def slot_minutes(card):
    """没卡的时候返回空列表，**不许抛异常**（抛了就把后面几条判据一起顶掉，
    变异跑出来是"崩"而不是"红"——这两种样子在 rc 上根本分不开）。"""
    return sorted({s.get("minutes") for s in ((card or {}).get("slots") or [])})


def held(cli, sid):
    """读暂存那一张卡（走接口，和确认页看到的是同一份）。"""
    d = cli.get("/api/pending/%s" % sid).json()
    entry = d.get("pending") or d
    opts = [o for o in ((entry or {}).get("options") or []) if isinstance(o, dict)]
    return opts[0] if opts else None


with sandbox():
    from app.modules import planner as P

    T1a = "帮我在下周安排三次健身，一次三小时"
    T1b = "帮我在下周安排三次健身，一次一小时"
    T2 = "是改成三次，每次一小时"
    T3 = "不对是一次三小时"

    print("=== A 批 · 链路：三项同时保住（事名 / 次数 / 单次时长）===")
    sid = "t73-a1"
    d1 = ask(cli := make_client(admin_token="t73"), T1b, sid)
    k1 = one_card(d1)
    c.check("A1 第一轮就正常：5 段里预勾 3、每段 60 分钟、事名「健身」"
            "（后面每一轮的基准，这一条红了说明基准数据变了，别去改断言）",
            k1 is not None and k1.get("kind") == "todo_slots"
            and k1.get("times") == 3 and k1.get("preselect") == 3
            and slot_minutes(k1) == [60] and k1.get("title") == "健身",
            "card=%s" % (k1 and {x: k1.get(x) for x in
                                 ("kind", "title", "times", "preselect")}))
    d2 = ask(cli, T2, sid)
    k2 = one_card(d2)
    c.check("A2 **T2 全链路**：看着卡说「是改成三次，每次一小时」之后，"
            "次数仍 3、预勾仍 3、每段 60、事名仍是「健身」（不是「健身是每次」也不是「每次」）",
            k2 is not None and k2.get("times") == 3 and k2.get("preselect") == 3
            and slot_minutes(k2) == [60] and k2.get("title") == "健身",
            "card=%s" % (k2 and {x: k2.get(x) for x in ("title", "times", "preselect")}))
    c.check("A3 这一句**不是**回「没找到那条待办」：那是按名字改期那条路截走补充句的样子"
            "（卡还没写库，按名字必然找不到，学生那句纠正当场蒸发）",
            k2 is not None and "没找到那条待办" not in d2.get("answer", ""),
            d2.get("answer", "")[:60])

    sid = "t73-a4"
    ask(cli, T1a, sid)
    d5 = ask(cli, "一次一小时", sid)
    k5 = one_card(d5)
    c.check("A4 **T1 全链路**：①`一次三小时` ②`一次一小时` → 时长跟着变 60，"
            "而次数仍 3、事名仍「健身」（拼接上一句整句会把 180 顶回来，这里钉的是"
            "「只拼事名、不拼整句」那一步）",
            k5 is not None and slot_minutes(k5) == [60]
            and k5.get("times") == 3 and k5.get("title") == "健身",
            "card=%s min=%s" % (k5 and (k5.get("title"), k5.get("times")), slot_minutes(k5)))

    sid = "t73-a6"
    ask(cli, T1b, sid)
    d6 = ask(cli, T3, sid)
    k6 = one_card(d6)
    c.check("A5 **T3 全链路**（正控性质：时长该**变长**）：①`一次一小时` ②`不对是一次三小时` "
            "→ 每段 180、次数仍 3、事名仍「健身」。方向反过来验一遍，"
            "免得「时长跟着改」其实只是「永远取第一个匹配」",
            k6 is not None and slot_minutes(k6) == [180]
            and k6.get("times") == 3 and k6.get("title") == "健身",
            "card=%s min=%s" % (k6 and (k6.get("title"), k6.get("times")), slot_minutes(k6)))

    print("\n=== B 批 · 时长四级优先（函数级 T4/T5）===")
    r4 = P.parse_slot_text("下周三，每次一小时", "游泳", "2026-10-28", 30)
    c.check("B1 T4：卡上 duration=30、学生这句说「一小时」→ 取**这一句的 60**"
            "（第 2 级压过第 3 级；两个数故意设成不一样，设成一样这条就白测）",
            r4 is not None and r4.get("minutes") == 60 and r4.get("title") == "游泳"
            and r4.get("start") == "08:00",
            "r=%s" % (r4 and {k: r4.get(k) for k in ("title", "start", "minutes")}))
    r4b = P.parse_slot_text("下周三", "游泳", "2026-10-28", 30)
    c.check("B2 第 3 级独立生效：这一句什么都没报时长 → 用**卡上的 30**（不是 90 兜底）",
            r4b is not None and r4b.get("minutes") == 30,
            "r=%s" % (r4b and {k: r4b.get(k) for k in ("title", "start", "end", "minutes")}))
    r4c = P.parse_slot_text("下周三", "游泳", "2026-10-28")
    c.check("B3 第 4 级仍是 90 兜底（正控：前两级都没有时才落到 AUTO_SLOT_LENGTH）",
            r4c is not None and r4c.get("minutes") == 90,
            "r=%s" % (r4c and r4c.get("minutes")))
    card60 = {"kind": "todo_add", "title": "健身", "date": "2026-10-28",
              "start": "19:00", "end": "20:00", "minutes": 60}
    r5 = P.retime_todo_proposal(card60, "改成周六")
    c.check("B4 T5：只换日子、没说几点 → 长度**沿用卡上那条 60**（以前落到 90，"
            "学生改的是日子、长度被系统顺手加长了 30 分钟）",
            r5 is not None and r5.get("minutes") == 60 and r5.get("title") == "健身"
            and r5.get("date") == _next_sat(),
            "r=%s" % (r5 and {k: r5.get(k) for k in ("title", "date", "start", "end",
                                                     "minutes")}))
    r5b = P.retime_todo_proposal(card60, "改成周六 21:00-22:30")
    c.check("B5 第 1 级最高：这句自己给了起止（21:00-22:30=90）→ 谁都不许覆盖它",
            r5b is not None and r5b.get("minutes") == 90 and r5b.get("start") == "21:00",
            "r=%s" % (r5b and {k: r5b.get(k) for k in ("start", "end", "minutes")}))

    print("\n=== C 批 · 卡片存次数/时长/事名源头 ===")
    cc = P.todo_slots_proposal(T1b)
    c.check("C1 候选卡上有 `times`/`duration`/`origin` 三个键，值分别 = 3 / 60 / 「健身」"
            "（没有这三键，下一轮就无处可补 —— 这是本单的地基）",
            cc is not None and cc.get("times") == 3 and cc.get("duration") == 60
            and cc.get("origin") == "健身",
            "card=%s" % (cc and {k: cc.get(k) for k in ("times", "duration", "origin")}))
    cn = P.todo_slots_proposal("每天背单词")
    c.check("C2 daily/weekly 那两条 `times=0` 是对的：每周一次不是「这批要排几次」，"
            "别把 repeat 的话头当成次数",
            cn is not None and cn.get("times") in (0, None) and cn.get("repeat") == "daily",
            "card=%s" % (cn and {k: cn.get(k) for k in ("times", "repeat")}))
    cw = P.todo_slots_proposal("每个周六上午游泳")
    c.check("C3 weekly 那条同样 times=0、repeat=weekly（两处一起钉，防止只改一支）",
            cw is not None and cw.get("times") in (0, None) and cw.get("repeat") == "weekly",
            "card=%s" % (cw and {k: cw.get(k) for k in ("times", "repeat")}))
    cp = P.todo_slots_proposal("周四加个两小时的自习")
    c.check("C4 **普通分支也传 times**（单据 §五.1 那句）：说了具体哪天那一条走的是 :1052，"
            "以前 times 在这条路上被整个丢掉",
            cp is not None and "duration" in cp and "times" in cp and "origin" in cp,
            "keys=%s" % sorted((cp or {}).keys()))

    print("\n=== D 批 · 补丁判据（parse_todo_patch + §一.4 三条 + 新单否决）===")
    pa = P.parse_todo_patch(T2)
    c.check("D1 「是改成三次，每次一小时」只交出两项：times=3、duration=60；"
            "date/start/end/title 一律 None（他没提的，不许替他重算）",
            pa["times"] == 3 and pa["duration"] == 60 and pa["date"] is None
            and pa["start"] is None and pa["title"] is None,
            "patch=%s" % pa)
    pe = P.parse_todo_patch("那个再说吧")
    c.check("D2 什么都没报的句子 → 补丁判空（`todo_patch_empty`），"
            "它不配覆盖卡上任何东西，也不该被当成补充句",
            all(v is None for v in pe.values()) and P.todo_patch_empty(pe),
            "patch=%s" % pe)
    c.check("D3 **`weekly_times` 的全局行为没被放宽**（正控性质：这一句仍读不出频次）："
            "「我去了三次图书馆」是陈述句，不是「一周三次」",
            P.weekly_times("我去了三次图书馆") is None
            and P.parse_todo_patch("我去了三次图书馆")["times"] is None,
            "weekly=%s patch_times=%s" % (P.weekly_times("我去了三次图书馆"),
                                          P.parse_todo_patch("我去了三次图书馆")["times"]))
    c.check("D4 窄口自己认得省略写法（上一句说过「周」，这一句只报个数）："
            "「改成三次」→ 3；但「下周三次健身」这种**片里还挂着别的字**的不算补丁",
            P.parse_todo_patch("改成三次")["times"] == 3
            and P.parse_todo_patch("我下周三次健身")["times"] is None,
            "改成三次=%s 我下周三次健身=%s" % (P.parse_todo_patch("改成三次")["times"],
                                       P.parse_todo_patch("我下周三次健身")["times"]))
    c.check("D5 补丁里混着**另一个名字**时不认次数（「三次，去游泳」= 像要换事），"
            "免得把一句新单当成给旧卡加次数",
            P.parse_todo_patch("三次，去游泳")["times"] is None,
            "patch=%s" % P.parse_todo_patch("三次，去游泳"))
    c.check("D6 裸钟点不进补丁（1..6 点没说上下午，判据与 `span_only_proposal` 同一条）："
            "宁可问一句，也不替他猜下午三点",
            P.parse_todo_patch("三点开始")["start"] is None,
            "patch=%s" % P.parse_todo_patch("三点开始"))
    import app.main as M
    c.check("D2-bis 「今天天气不错」这种闲聊会**报出一个日子**（`_pick_date` 认「今天」），"
            "所以挡住它的不是「补丁判空」而是新单否决 —— 这一条钉住否决真的在看名字",
            M._patch_is_new_order("今天天气不错", "健身") is True
            and P.parse_todo_patch("今天天气不错")["date"] is not None,
            "patch=%s" % P.parse_todo_patch("今天天气不错"))
    c.check("D2-ter 「周六加个游泳」（卡上是健身）必须被否决成**新单**（§一.4 的 ③）",
            M._patch_is_new_order("周六加个游泳", "健身") is True, "")
    c.check("D2-quater 「是改成三次，每次一小时」**不许**被否决（它就是 T2）："
            "先看语气、再看名字，顺序反了就把自己的测试判死了",
            M._patch_is_new_order("是改成三次，每次一小时", "健身") is False, "")
    m1 = P.patched_todo_blob("健身", "一次一小时")
    c.check("D7 `patched_todo_blob` 拼接幂等：谁包含谁就取长的那个，"
            "不许把同一句粘两遍（粘两遍会让 `_pick_title` 的切段档多一个假名）",
            m1 == "一次一小时，健身" and P.patched_todo_blob("健身", "健身") == "健身"
            and P.patched_todo_blob("", "一次一小时") == "一次一小时",
            "blob=%r" % m1)
    rt = P.retitle_todo_card({"kind": "todo_add", "title": "每次", "date": "2026-10-28",
                              "start": "19:00", "end": "20:00", "weekday": "周三",
                              "minutes": 60, "summary": "旧"}, "健身")
    c.check("D8 事名铁律的落点：`retitle_todo_card` 换名之后 **summary 跟着重算**"
            "（屏幕上那句和卡上那个字段不能两份真相）",
            rt["title"] == "健身" and rt["summary"].startswith("健身｜")
            and "每次" not in rt["summary"],
            "card=%s" % {k: rt.get(k) for k in ("title", "summary")})
    rs = P.retitle_todo_card(cc, "游泳")
    c.check("D9 候选卡换名走 `build_todo_slots` 重造：times/duration/preselect 一个都不能掉"
            "（掉成 0 就是「换了个名字、次数没了」那种二次事故）",
            rs.get("title") == "游泳" and rs.get("times") == cc.get("times")
            and rs.get("duration") == cc.get("duration")
            and rs.get("preselect") == cc.get("preselect"),
            "before=%s after=%s" % (cc.get("times"), rs.get("times")))

    print("\n=== E 批 · 计数残渣不算事名（判真门，第三处重抠的守门人）===")
    blocked = [s for s in ("每次", "三次", "一次", "两回", "次每次", "三次每次")
               if not P._title_ok(s)]
    c.check("E1 「每次/三次/一次/两回/次每次/三次每次」全部不许当名字"
            "（实测候选卡上写过「每次」，学生以为系统没听懂）",
            len(blocked) == 6, "放行=%s" % [s for s in ("每次", "三次", "一次", "两回",
                                                        "次每次", "三次每次")
                                            if P._title_ok(s)])
    legal = [s for s in ("二次元", "第一次", "下次") if P._title_ok(s)]
    c.check("E2 但「二次元」「第一次」「下次」必须**放行** —— 这是整串匹配而不是 search 的唯一证据："
            "改成 search 这一条立刻红（V6 的靶子）",
            len(legal) == 3, "误杀=%s" % [s for s in ("二次元", "第一次", "下次")
                                          if not P._title_ok(s)])
    c.check("E3 真名字照旧认（正控：这道门不能把「值日/游泳/背单词」也挡掉，"
            "否则「没卡」就变成「一张卡都不出」）",
            all(P._title_ok(s) for s in ("值日", "游泳", "背单词"))
            and P._pick_title("周五晚上七点到九点值日") == "值日",
            "值日=%r" % P._pick_title("周五晚上七点到九点值日"))
    c.check("E4 判据开在 `_title_ok` 上、不是开在某个调用点上："
            "整句档与切段档共用这一道门（打偏到 `span_only_proposal` 那行会变成"
            "「一句只有时间的话，系统一张卡都不出」）",
            "_COUNT_RESIDUE_RE" in read("app/modules/planner.py")
            and "not _COUNT_RESIDUE_RE.fullmatch(t)" in read("app/modules/planner.py"),
            "")

    print("\n=== S 批 · 静态接线（堵「(c) 的假绿」）===")
    main_src = read("app/main.py")
    calls = re.findall(r"parse_slot_text\(([^()]*(?:\([^()]*\)[^()]*)*)\)", main_src)
    nonempty = [x for x in calls if x.strip()]
    arity = [len(re.split(r",(?![^()]*\))", x)) for x in nonempty]
    c.check("S1 `parse_slot_text(` 每一处调用都传满 4 个参数（≥3 个逗号）——"
            "函数签名对了、内部逻辑对了，**坏在调用点没把卡上的 duration 递下去**，"
            "这种病函数级断言一条也抓不到",
            nonempty and all(n >= 4 for n in arity),
            "调用点参数数=%s" % arity)
    c.check("S2 候选卡那一处确实传的是 `held_slots.get(\"duration\")`（不是写死 0）："
            "把它删成三个参数，B1/B2 照样绿、真机学生看到的是「他刚说一小时，系统排了 90 分钟」",
            'held_slots.get("duration")' in main_src, "")
    gate = re.search(r"if \(\(_is_patch73\s*\n?\s*or \(", main_src)
    c.check("S3 入口闸上确实挂了 `_is_patch73`：那句「一次一小时」既没意图词、规则层也读不出，"
            "不加这一条它根本走不到补丁分支（这是「判据与编辑点不在同一条路上」那一坑）",
            gate is not None, "")

    c.summary("#73 补齐缺口")
