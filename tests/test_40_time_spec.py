"""时间解析收口：一句话只解析**一次**成 TimeSpec，下游要么整体用、要么整体不用。

起因（2026-10-10 用户问「更严谨的时间段还能判断正确吗」）：
  「周五晚上七点到九点值日」→ 卡片给 15:40-17:40（钟点全丢）
  「每周三下午两点开会」  → 卡片给 19:00-20:30（钟点全丢）
而 `_pick_span` 单独测是**读得对的**（19:00-21:00 / 14:00）。所以病根不是"解析不出来"，
是**分层解析完没人把它们合起来用**：`candidate_slots` 只挑了时长、把钟点丢了；
`_day_slots` 拿 `prefer` 只用来给"上午/下午/晚上"三档排序。
每个函数单测都对，合起来错 —— 这种 bug 靠加正则永远修不完。

于是引入 `parse_time_spec()`：一次读全 {date, weekday, repeat, start, end, minutes, part, clock}，
`candidate_slots` / `_day_slots` 一律吃这个结构。本文件钉的就是"必须合起来用"这件事。

⚠️ 还有一条同等重要：**点了钟点却排不进去时必须说为什么**。
周五 19:00-20:40 有《班会》，系统改给 15:40 —— 判断是对的，
但不说原因，学生看到的就还是"你没听懂我的话"（这就是被投诉的那条）。
"""
import datetime
import io

import ast

from _harness import Checker, make_client, sandbox, title
from app.store import set_settings

PLANNER = "app/modules/planner.py"


def _free(iso, s, e):
    from app.modules import planner as P
    return P._span_free(iso, (s, e))


def _first_free_evening():
    for k in range(1, 15):
        d = (datetime.date.today() + datetime.timedelta(days=k)).isoformat()
        if _free(d, "19:00", "21:00"):
            return d
    return None


def _first_busy_evening():
    for k in range(1, 15):
        d = (datetime.date.today() + datetime.timedelta(days=k)).isoformat()
        if not _free(d, "19:00", "21:00"):
            return d
    return None


def _first_free_span(s, e):
    for k in range(1, 15):
        d = (datetime.date.today() + datetime.timedelta(days=k)).isoformat()
        if _free(d, s, e):
            return d
    return None


def batch_a_spec_fields():
    c = Checker()
    title("A 批 · parse_time_spec 一次读全（每个字段单独钉）")
    from app.modules.planner import parse_time_spec

    s = parse_time_spec("周五晚上七点到九点值日")
    c.check("点名日子 → date 有值（今年）", bool(s["date"]) and s["date"][:2] == "20", s["date"])
    c.check("点名钟点 → start=19:00", s["start"] == "19:00", s["start"])
    c.check("点名结束 → end=21:00", s["end"] == "21:00", s["end"])
    c.check("有起止 → minutes 按差算=120", s["minutes"] == 120, s["minutes"])
    c.check("档 = 晚上", s["part"] == "晚上", s["part"])
    c.check("clock 标记为真", s["clock"] is True)
    c.check("没说每周 → repeat 为空、weekday 为 None",
            s["repeat"] == "" and s["weekday"] is None, s)

    w = parse_time_spec("每周三下午两点开会")
    c.check("每周三 → weekday=3 且 repeat=weekly",
            w["weekday"] == 3 and w["repeat"] == "weekly", w)
    c.check("下午两点 → start=14:00、档=下午",
            w["start"] == "14:00" and w["part"] == "下午", w)
    c.check("「每周」不许被读成相对日期（date 不该被一周后污染）",
            w["date"] is None, w["date"])

    n = parse_time_spec("加个健身")
    c.check("什么都没点 → 全空，不猜",
            not n["start"] and not n["end"] and not n["date"]
            and n["clock"] is False and n["repeat"] == "", n)
    c.check("空输入不炸",
            parse_time_spec("")["clock"] is False and parse_time_spec(None)["repeat"] == "")
    z = parse_time_spec("中午吃饭")
    c.check("「中午」不硬归到上午或下午（跨档，宁可不说档）", z["part"] == "", z["part"])
    return c


def batch_b_clock_must_be_used():
    c = Checker()
    title("B 批 · 读到的钟点必须用满（这是这次重构的全部意义）")
    from app.modules import planner as P

    day = _first_free_span("15:00", "16:30")
    if day:
        # 故意选"下午三点"而不是"晚上七点"：19:00 正好是"晚上"档的默认起点，
        # 就算 pinned 那段没生效、退回三档默认，也会碰巧给出 19:00 —— 上一版就是这么假绿的。
        # 15:00 不是任何档的起点，只有"真的用了学生点的钟点"才会出现。
        slots = P.candidate_slots(f"{day} 下午三点复习", max_slots=3)
        c.check(f"点名 15:00（非档起点）→ 必须真给 15:00 那天那段（{day}）",
                any(s["start"] == "15:00" for s in slots),
                [(s["start"], s["end"]) for s in slots])
    else:
        c.check("环境里找不到一个 15:00-16:30 空着的日子（①无法验）", False, "看课表")

    day2 = _first_free_evening()
    if day2:
        slots2 = P.candidate_slots(f"{day2} 晚上七点到九点值日", max_slots=3)
        c.check("有起止的点名（七点到九点）→ 终点也必须是 21:00，不是默认 90 分钟",
                any(s["start"] == "19:00" and s["end"] == "21:00" for s in slots2),
                [(s["start"], s["end"]) for s in slots2])

    # ④ 每周重复 + 点名钟点：每一段都得是学生点的那个钟点（否则"每周"就白认了）
    rwd = P.todo_slots_proposal("每周六下午三点爬山")
    rs = (rwd or {}).get("slots") or []
    c.check("每周六下午三点 → 每段都是周六且 15:00 起",
            bool(rs) and all(s["start"] == "15:00" and s.get("weekday") == "周六" for s in rs),
            [(s.get("weekday"), s["start"]) for s in rs])
    c.check("这张卡仍然是 weekly 重复", (rwd or {}).get("repeat") == "weekly",
            (rwd or {}).get("repeat"))

    occ = _first_busy_evening()
    if occ:
        card = P.todo_slots_proposal(f"{occ} 晚上七点到九点值日")
        hint = (card or {}).get("hint", "")
        c.check(f"被占的那天（{occ}）不许硬排出 19:00-21:00",
                all(s["start"] != "19:00" or s["end"] != "21:00"
                    for s in (card or {}).get("slots") or []),
                [(s["start"], s["end"]) for s in (card or {}).get("slots") or []])
        c.check("但必须说一句排不进去、并带上学生点的那个钟点（不许默默换时间）",
                "排不进去" in hint and "19:00-21:00" in hint, hint[:110])
    else:
        c.check("环境里找不到一个 19:00-21:00 被占的日子（②无法验）", False, "看课表")

    s3 = P.candidate_slots("下周六下午两点爬山 大概三个小时", max_slots=2)
    c.check("「大概三个小时」→ 候选段按 180 分钟找，不是默认 90",
            any(x["minutes"] >= 175 for x in s3), [(x["start"], x["minutes"]) for x in s3])
    return c


def batch_c_no_split_reading():
    c = Checker()
    title("C 批 · 不许再回到「各读各的」（AST 钉代码形状）")
    src = io.open(PLANNER, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    c.check("parse_time_spec 存在", "parse_time_spec" in fn)

    def kw_calls(name, kw):
        node = fn.get(name)
        if node is None:
            return 0
        return sum(1 for n in ast.walk(node) if isinstance(n, ast.Call)
                   for k in n.keywords if k.arg == kw)

    calls = [n.func.id for n in ast.walk(fn["candidate_slots"])
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    c.check("candidate_slots 内部调了一次 parse_time_spec（不是又自己拼一套）",
            calls.count("parse_time_spec") == 1, calls.count("parse_time_spec"))
    n_spec = kw_calls("candidate_slots", "spec")
    c.check("candidate_slots 把 spec 传给了 _day_slots（单日/跨天两条路都传）",
            n_spec >= 2, f"spec= 出现 {n_spec} 次")
    c.check("_day_slots 接了 spec 参数",
            any(a.arg == "spec" for a in fn["_day_slots"].args.args))
    body = ast.unparse(fn["_day_slots"])
    c.check("_day_slots 真的按 spec 的钟点摆第一段（不是只读不用）",
            'spec.get("start")' in body.replace("'", '"') and "pinned" in body, body[:70])
    return c


def batch_d_e2e():
    c = Checker()
    title("D 批 · 端到端：说一句话，卡片上的时间就是那句话")
    day = _first_free_evening()
    with sandbox():
        set_settings({"api_key": "", "base_url": "https://api.deepseek.com", "model": "m"})
        cli = make_client()
        r = cli.post("/api/chat", json={"message": f"{day} 晚上七点到九点值日",
                                        "session_id": "t40-d", "module": "planner"})
        d0 = r.json()
        cards = [o for o in (d0.get("options") or []) if isinstance(o, dict)]
        card = cards[0] if cards else {}
        # 日子和钟点都点名时，系统直接出 `todo_add`（一个定好的时间，不再摊候选）；
        # 只点名其一时才出 `todo_slots` 候选卡。两种都要认 —— 判据是"时间等不等于学生说的那句"，
        # 不是"走的是哪张卡"。
        got = []
        if card.get("kind") == "todo_slots":
            got = [(s.get("start"), s.get("end")) for s in (card.get("slots") or [])]
        elif card.get("start"):
            got = [(card.get("start"), card.get("end"))]
        c.check("接口 200 且出了卡", r.status_code == 200 and bool(card), str(r.status_code))
        c.check(f"时间就是学生说的 19:00-21:00（{day}，卡型 {card.get('kind')}）",
                ("19:00", "21:00") in got, got)
        c.check("这次排进去了，所以不许出现「排不进去」那句",
                "排不进去" not in str(d0), str(d0)[:90])
    return c


def main():
    results = [batch_a_spec_fields(), batch_b_clock_must_be_used(),
               batch_c_no_split_reading(), batch_d_e2e()]
    tp = sum(r.passed for r in results)
    tf = sum(r.failed for r in results)
    print(f"\n===== 时间解析收口（TimeSpec）总账：通过 {tp} 项，失败 {tf} 项 =====")
    return 1 if tf else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
