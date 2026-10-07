"""「周六早上去图书馆」被摊成周三等多天的空档 —— 回归测试。

报障原话（2026-10-07 学生）：「我说周6早上去图书馆，底层要求去找四个不同时间，
所有找了周3等其他时间」。

根因：wants_add_todo("周六早上去图书馆") 原来返回 False（没有显式"加"动词，
"早上"是时段偏好不是准点 → _pick_span 为 None → 末位"日期+起止"条件不满足）。
于是语义层判成 add_todo 后，main.py 走了 `to_canonical(message)` 这条路——
它把"周六"这种**不在语义字段里**的日期整段丢掉（见 main.py 2322 行注释），
后面 candidate_slots 认不出周六，就从"今天"(周三)往后扫几天，列出周三/周四/周五/周六。

修法（planner.py 的 wants_add_todo 新增放行条件）：说了"哪天 + 要做什么事"、
但还没说几点（"周六早上去图书馆""周四去还书"）也是明确的加待办意图，必须接住。
接住后走 `blob = message` 原话，candidate_slots 只列周六。

本文件只测确定性纯函数（wants_add_todo / _pick_date / candidate_slots /
todo_slots_proposal），不涉及模型与 HTTP —— 钉死"说了周六就只列周六"这条红线。
"""
import datetime

from _harness import Checker, title
from app.modules.planner import (
    wants_add_todo,
    _pick_date,
    candidate_slots,
    todo_slots_proposal,
)


def _next_weekday_date(target_wd: int) -> str:
    """独立算出"下一个 target_wd（0=周一…6=周日）"，不依赖 _pick_date，避免环路。

    口径与 _pick_date 对齐：今天若正好是 target_wd 也返回今天（"周六去"在周六当天
    就是指今天）。
    """
    today = datetime.date.today()
    if today.weekday() == target_wd:
        delta = 0
    else:
        delta = (target_wd - today.weekday()) % 7
    return (today + datetime.timedelta(days=delta)).isoformat()


def test_saturday_add_todo_intent():
    title("1. 「周六早上去图书馆」必须被规则认作加待办（本轮修复的核心）")
    c = Checker()
    c.check("wants_add_todo 认出『周六早上去图书馆』",
            wants_add_todo("周六早上去图书馆"),
            f"wants_add_todo={wants_add_todo('周六早上去图书馆')}")
    c.check("wants_add_todo 认出『周四去还书』（同类：哪天+做事、没说几点）",
            wants_add_todo("周四去还书"))
    # 反向：问句 / 没说日期 / 查课 / 管理指令 仍不该被误认
    # （这几句带日期和事名，但没有"去/到/来"动作动词 —— 是在查、是在管，不是要排一件事）
    c.check("问句『周六去图书馆吗』不被认作加待办",
            not wants_add_todo("周六去图书馆吗"))
    c.check("没说日期的『去图书馆』不被认作加待办",
            not wants_add_todo("去图书馆"))
    c.check("查课问句『帮我看看周一第一节是什么课』不被认作加待办",
            not wants_add_todo("帮我看看周一第一节是什么课"))
    c.check("管理指令『标记完成 周日待办』不被认作加待办",
            not wants_add_todo("标记完成 周日待办"))
    return c.summary("第一批（加待办意图识别）")


def test_saturday_date_locked():
    title("2. 说了周六 → 候选空档只列周六，绝不往前/往后扫别的日子（本次报障的主因）")
    c = Checker()
    msg = "周六早上去图书馆"
    sat = _pick_date(msg)
    # 先独立确认 _pick_date 解出来的是周六
    c.check("_pick_date 解出的是周六",
            datetime.date.fromisoformat(sat).weekday() == 5,
            f"date={sat}")
    # 候选必须非空（周六当天要有空档才合理；这里只验证"有就对了"）
    slots = candidate_slots(msg)
    c.check("candidate_slots 至少列出一段空档", bool(slots),
            f"slots={[s['date'] for s in slots]}")
    # 关键回归点：每一段都得是周六，一个别的日子都不能有
    wrong_days = [s["date"] for s in slots if s["date"] != sat]
    c.check("所有候选都锁定在说的周六（无周三/周四/周五等别的日子）",
            not wrong_days,
            f"expected={sat} got={[s['date'] for s in slots]}")
    return c.summary("第二批（日期锁定）")


def test_saturday_proposal_shape():
    title("3. todo_slots_proposal：候选卡形态正确（多段可勾、标题不重复）")
    c = Checker()
    msg = "周六早上去图书馆"
    sat = _pick_date(msg)
    prop = todo_slots_proposal(msg, max_slots=4)
    c.check("返回了候选卡", prop is not None)
    if prop is not None:
        c.check("kind 是 todo_slots（多段候选、可勾，不是单条 todo_add）",
                prop.get("kind") == "todo_slots",
                f"kind={prop.get('kind')}")
        c.check("标题是『图书馆』、没有重复成『图书馆图书馆』",
                prop.get("title") == "图书馆",
                f"title={prop.get('title')!r}")
        c.check("候选至少 1 段、至多 4 段",
                1 <= len(prop.get("slots") or []) <= 4,
                f"n={len(prop.get('slots') or [])}")
        off_days = [s["date"] for s in (prop.get("slots") or [])
                    if s["date"] != sat]
        c.check("候选卡里每段都在周六", not off_days,
                f"days={[s['date'] for s in (prop.get('slots') or [])]}")
    return c.summary("第三批（候选卡形态）")


def test_no_regression_existing_intents():
    title("4. 反向验证：既有加待办写法没有被本轮改动误伤")
    c = Checker()
    for s in ("帮我加个游泳周四19:00", "我要去健身", "复习线性代数 周四下午三点",
              "帮我安排下周的数学复习", "记一下交电费 明天下午三点",
              "周六下午两点到三点去图书馆"):
        c.check(f"「{s}」仍认作加待办", wants_add_todo(s),
                f"wants_add_todo={wants_add_todo(s)}")
    return c.summary("第四批（反向验证）")


if __name__ == "__main__":
    fails = 0
    fails += test_saturday_add_todo_intent()
    fails += test_saturday_date_locked()
    fails += test_saturday_proposal_shape()
    fails += test_no_regression_existing_intents()
    sys_exit = __import__("sys").exit
    sys_exit(1 if fails else 0)
