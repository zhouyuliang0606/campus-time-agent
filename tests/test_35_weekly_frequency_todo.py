"""「一周三次」这类频次待办：认出次数 → 一次排 N 段 → 前 N 段替他勾上。

需求出处（2026-10-08）：用户「日程的长久代办没法实现，我说一周去三次健身房，增加进代办」。
真机复现到的行为是：整句「我一周要去三次健身房」被当成**一条**待办的名字挂出来，
"三次"这个数量直接丢了。用户选的规格是**展开成 N 条**（不是给待办加 repeat 字段）：
一周 N 次 = 这一周里排 N 段、一天一段、前 N 段默认勾上，点确认走 /api/todos/batch 写 N 条。

本文件钉的是这个规格的四条边界：
  ① 频次认得准，**不该认的不认**（「一天三次」吃药、「第一次上课」节次、「两次课」是课）；
  ② 频次不进标题（「健身房」而不是整句话），但**擦完没名字了要退回不擦**（「一星期跑两回」）；
  ③ 卡片形状：preselect = N、一天一段不重复、排不满 N 段时 hint 如实说排到几段；
  ④ 端到端：/api/chat 出卡 → /api/todos/batch 写 N 条，**且这 N 条落在 N 个不同日子**。

⚠️ 空断言防护：D 批先证"这一轮真的出了候选卡"再核字段（test_34 的教训——
没出卡时 `all([])` 是真，绿得毫无意义）。
"""
import datetime

from _harness import Checker, title, make_client, sandbox

CONFIRM = "app/static/confirm.html"


def batch_a_parse():
    c = Checker()
    title("A 批 · 频次解析：认该认的，不硬认不该认的")
    from app.modules.planner import weekly_times

    for text, want in [("我一周要去三次健身房", 3), ("每周3次跑步", 3),
                       ("一星期跑两回", 2), ("每礼拜两趟游泳", 2),
                       ("一周一次大扫除", 1), ("一周去五次图书馆", 5),
                       ("这周内安排四次复习", 4)]:
        got = weekly_times(text)
        c.check(f"{text!r} → {want}", got == want, f"实际 {got}")

    for text in ["一天吃三次药",          # 按天铺开 = 替他造 3 条没说过的
                 "周四下午第一次上课",    # 节次，不是频次
                 "这周还有两次课",        # 课，不是待办
                 "每周八次健身",          # >7，判是听错了
                 "忙了一周",              # 时长，没有次数
                 "周三去健身",            # 星期几，不是频次
                 "明天下午三点开会"]:
        got = weekly_times(text)
        c.check(f"不该认：{text!r}", got is None, f"实际认成了 {got}")
    return c


def batch_b_title():
    c = Checker()
    title("B 批 · 频次不进标题；擦到没名字时退回不擦（别把卡片弄没了）")
    from app.modules.planner import _pick_title

    for text, want in [("我一周要去三次健身房", "健身房"),
                       ("每周3次跑步", "跑步"),
                       ("一周去五次图书馆", "图书馆"),
                       ("加个待办：一周去两次牙医", "牙医")]:
        got = _pick_title(text)
        c.check(f"{text!r} 的标题是「{want}」", got == want, f"实际「{got}」")

    # 退化路径：「一星期跑两回」擦完频次只剩一个"跑"字（判真嫌短）。
    # 这时必须退回**不擦频次**，宁可名字丑（「星期跑两回」）也别返回"待办"——
    # 返回待办 = 卡片出不来 = 学生被反问"要安排的是什么事"，他明明说了。
    got = _pick_title("一星期跑两回")
    c.check("擦到没名字时退回不擦频次（不返回「待办」）",
            got != "待办" and len(got) >= 2, f"实际「{got}」")
    # 无频次的句子照旧（本文件不许改动老行为）
    c.check("无频次语句标题不受影响", _pick_title("周四去健身") == "健身")

    # 擦除那条正则必须跟解析那条一样保守：「第一次」是节次、「两次课」是课。
    # 为什么单测正则而不走 _pick_title：整句里「这周要去三次，第一次周四」这种混写，
    # _FREQ_RE 先命中"三次"才会开擦，此时若无 `(?<!第)`，"第一次"会被一起擦成"第"。
    from app.modules.planner import _COUNT_UNIT_RE
    c.check("「第一次」不被当频次擦掉",
            _COUNT_UNIT_RE.sub(" ", "第一次约朋友") == "第一次约朋友",
            "节次被擦掉了")
    c.check("「两次课」不被当频次擦掉",
            _COUNT_UNIT_RE.sub(" ", "这周还有两次课") == "这周还有两次课",
            "课被擦掉了")
    return c


def batch_c_card_shape():
    c = Checker()
    title("C 批 · 候选卡形状：预选 N 段、一天一段、排不满时如实说")
    from app.modules.planner import todo_slots_proposal

    card = todo_slots_proposal("我一周要去三次健身房")
    c.check("出了卡", card is not None, "None = 候选卡根本没凑出来，后面全部无从核对")
    if card:
        slots = card.get("slots") or []
        pre = int(card.get("preselect") or 0)
        c.check("preselect = 3（学生报了三次）", pre == 3, f"实际 {pre}")
        c.check("候选段数 ≥ 预选数（得多给几段备选让他换）", len(slots) >= pre,
                f"{len(slots)} 段 / 预选 {pre}")
        days = [s.get("date") for s in slots]
        c.check("一天一段：候选里没有同一天排两次",
                len(set(days[:pre])) == pre, f"前 {pre} 段日期 {days[:pre]}")
        c.check("hint 里写明了次数，不笼统一句『挑一个』",
                "3 次" in card.get("hint", "") and "勾" in card.get("hint", ""),
                card.get("hint", ""))
        c.check("summary 带「已勾 N 段」（日志/回执看得见预选）",
                "已勾 3 段" in card.get("summary", ""), card.get("summary", ""))

    # 报了哪天 → 频次模式让位：「周四一周三次」本身就矛盾（三段全挤周四），
    # 宁可退回"只排那天"的老路子，也别替他造"周四连跑三趟"。
    one = todo_slots_proposal("周四一周三次健身房")
    c.check("指定了哪天 → 不预选多段（矛盾句退回单日候选）",
            one is None or int(one.get("preselect") or 0) == 0,
            one and one.get("preselect"))

    # 无频次的老行为一个字都不能变
    plain = todo_slots_proposal("去健身")
    c.check("没报频次 → preselect=0（老行为不变）",
            plain is None or int(plain.get("preselect") or 0) == 0,
            plain and plain.get("preselect"))
    return c


def batch_c2_shortfall():
    c = Checker()
    title("C2 批 · 排不满 N 段时如实说（沙箱）——不许谎称「按一周七次排好了」")
    from app.modules.planner import todo_slots_proposal
    with sandbox():
        cli = make_client()
        today = datetime.date.today()
        # 把接下来几天整段占满（08:00-22:00）→ "一周七次"根本排不满
        for off in range(6):
            d = (today + datetime.timedelta(days=off)).isoformat()
            cli.post("/api/todos", json={"title": f"占位{off}", "date": d,
                                         "start": "08:00", "end": "22:00"})
        card = todo_slots_proposal("一周去七次健身")
        # 全占满时返回 None 也是如实（调用方会说"插不进去"）；出一张谎报 7 段的卡才是不行
        c.check("要么没有卡、要么不谎报段数",
                card is None or int(card.get("preselect") or 0) < 7,
                card and card.get("preselect"))
        if card:
            hint = card.get("hint") or ""
            pre = int(card.get("preselect") or 0)
            slots = card.get("slots") or []
            c.check("preselect 只等于真排出来的段数（不谎报 7）",
                    1 <= pre <= len(slots) and pre < 7, f"pre={pre} 段数={len(slots)}")
            c.check("hint 明说「只排得出 N 段」，不写「按这一周给你挑了 7 段」",
                    "只排得出" in hint and "你说一周 7 次，先按这一周" not in hint, hint)
    return c


def batch_d_e2e():
    c = Checker()
    title("D 批 · 端到端：/api/chat 出卡 → /api/todos/batch 写 N 条（沙箱，零污染真实数据）")
    with sandbox():
        cli = make_client()
        r = cli.post("/api/chat", json={"message": "我一周要去三次健身房，帮我加进待办",
                                        "session_id": "p35-e2e", "module": "planner"})
        c.check("接口 200", r.status_code == 200, r.text[:200])
        d = r.json() if r.status_code == 200 else {}
        cards = [o for o in (d.get("options") or []) if o.get("kind") == "todo_slots"]
        # 先证"真的出了卡"——没出卡时下面每条 all([]) 都会假绿
        c.check("这一轮真的出了候选时段卡", bool(cards),
                "没出卡 = 频次链路整个没跑起来（看 answer：" + str(d.get("answer"))[:80] + "）")
        if not cards:
            return c
        card = cards[0]
        slots = card.get("slots") or []
        pre = int(card.get("preselect") or 0)
        c.check("聊天回话里说清了「一周 3 次」并勾了 3 段",
                "一周 3 次" in (d.get("answer") or "") and pre == 3,
                f"preselect={pre}")
        c.check("回话如实说明只管这一周（不假装会自动重复）",
                "没有自动重复" in (d.get("answer") or ""), (d.get("answer") or "")[-120:])

        picked = [{"date": s["date"], "start": s["start"], "end": s["end"]} for s in slots[:pre]]
        before = len(cli.get("/api/todos").json().get("todos") or [])
        w = cli.post("/api/todos/batch", json={"title": card.get("title") or "健身",
                                               "slots": picked, "session_id": "p35-e2e"})
        c.check("批量写入 200", w.status_code == 200, w.text[:200])
        todos = cli.get("/api/todos").json().get("todos") or []
        c.check(f"确实多写出 {pre} 条", len(todos) - before == pre,
                f"before={before} after={len(todos)}")
        mine = [t for t in todos if t.get("title") == (card.get("title") or "健身")]
        c.check("写出的这几条落在不同日子（一周三次≠一天三练）",
                len({t.get("date") for t in mine}) == pre,
                str([t.get("date") for t in mine]))
        c.check("日期都在今天之后（没排到过去）",
                all((t.get("date") or "") >= datetime.date.today().isoformat() for t in mine),
                str([t.get("date") for t in mine]))
    return c


def batch_e_frontend():
    c = Checker()
    title("E 批 · 前端真按 preselect 打勾（不是后端勾了、界面还是空的）")
    src = open(CONFIRM, encoding="utf-8").read()
    c.check("confirm.html 读了 payload 里的 preselect", "p.preselect" in src)
    c.check("预选那几行的 checkbox 带 checked、行带 on 类",
            '${i < pre ? " checked" : ""}' in src
            and 'class="slot${i < pre ? " on" : ""}' in src, "预选没接到 DOM 上")
    c.check("preselect 越界被夹回 [0, 段数]（后端给错也不炸）",
            "Math.min(Number(p.preselect) || 0, ss.length)" in src)
    # 勾选数决定按钮上的"+N 项"，预选必须一起算进去，否则显示 0 项、点不动
    c.check("按钮计数走 pickedSlots()（读的是 checked 状态，预选的天然算在内）",
            "function pickedSlots()" in src and "cb.checked" in src)
    # 全站唯一一份转义（test_33 立的规矩）：新写的这段不许自带第二份
    c.check("这段没自己拼未转义的 label",
            'esc(s.label' in src, "候选时段 label 直插 HTML 了")
    return c


def main():
    results = [batch_a_parse(), batch_b_title(), batch_c_card_shape(),
               batch_c2_shortfall(), batch_d_e2e(), batch_e_frontend()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 频次待办（一周 N 次）总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
