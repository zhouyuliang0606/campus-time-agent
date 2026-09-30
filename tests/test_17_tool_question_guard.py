"""第十七批测试：堵死"模型直调工具把问句/废话弄成待办卡"的残留缺口（收口误判·任务一）。

背景
====
规则路径 `wants_add_todo` 已有 `_GO_QUESTION_RE` 守卫，但模型能直接调
`propose_todo_tool` / `propose_todo_slots_tool` / `propose_todo_mode_tool`
（这三个都在 `build_scheduling_tools()` 里注入所有模块），那条路没有问句/非下单守卫，
问句、废话仍可能被弄成待办卡。本次把这道否决也加进函数体，做到"统一守卫"。

验证（钉死）
==========
  · 规则路径：wants_add_todo 对三个问句都返回 False；
  · 模型直调路径：propose_todo_tool / propose_todo_slots_tool / propose_todo_mode_tool
    收到问句都返回 error（不带 __proposal__），不出待办卡；
  · 正常加待办（"帮我加个游泳周四19:00" / propose_todo_tool(游泳,周四,19:00-20:00)）
    该出卡仍出卡。

全部跑在数据沙箱里，绝不碰你的真实演示数据。
"""
import json as _json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, title


def test_rule_path_rejects_questions():
    title("1. 规则路径：问句一律不判成下单")
    c = Checker()
    from app.modules.planner import wants_add_todo
    for q in ("我要去哪里取快递", "我想问一下交电费怎么操作", "图书馆几点开门"):
        got = wants_add_todo(q)
        c.check(f"「{q}」→ wants_add_todo 为 False（规则守卫挡住）", not got, str(got))
    # 正常加待办仍判成下单——原有能力不受影响
    c.check("「帮我加个游泳周四19:00」→ wants_add_todo 为 True（原有能力不受影响）",
            wants_add_todo("帮我加个游泳周四19:00"))
    return c.summary("第一批（规则路径）")


def test_tool_path_rejects_questions():
    title("2. 模型直调路径：三个问句都不出待办卡")
    c = Checker()
    from app.modules.planner import (propose_todo_tool, propose_todo_slots_tool,
                                     propose_todo_mode_tool)

    QUESTIONS = ("我要去哪里取快递", "我想问一下交电费怎么操作", "图书馆几点开门")
    for q in QUESTIONS:
        # propose_todo_tool：问句 + 顺手给个日期（模拟模型"硬凑"一个时间也想出卡）
        out = _json.loads(propose_todo_tool(title=q, date="周三"))
        c.check(f"propose_todo_tool(「{q}」+日期) → 返回 error、不出卡",
                "error" in out and "__proposal__" not in out,
                _json.dumps(out, ensure_ascii=False)[:80])

        # propose_todo_slots_tool：只传 title（问句）
        out = _json.loads(propose_todo_slots_tool(title=q))
        c.check(f"propose_todo_slots_tool(「{q}」) → 返回 error、不出卡",
                "error" in out and "__proposal__" not in out,
                _json.dumps(out, ensure_ascii=False)[:80])

        # propose_todo_mode_tool：只传 title（问句）
        out = _json.loads(propose_todo_mode_tool(title=q))
        c.check(f"propose_todo_mode_tool(「{q}」) → 返回 error、不出卡",
                "error" in out and "__proposal__" not in out,
                _json.dumps(out, ensure_ascii=False)[:80])
    return c.summary("第二批（模型直调路径）")


def test_tool_path_rejects_junk_title():
    title("3. 模型直调路径：事名说不清也不出卡（交追问分支）")
    c = Checker()
    from app.modules.planner import propose_todo_tool
    # 没说清要加什么事（只甩了个"待办"/动作词）→ 必须拒出卡，让系统去追问
    for bad in ("待办", "加个", "安排一下", "帮我安排"):
        out = _json.loads(propose_todo_tool(title=bad, date="周四"))
        c.check(f"propose_todo_tool(事名=「{bad}」) → 返回 error、不出卡",
                "error" in out and "__proposal__" not in out,
                _json.dumps(out, ensure_ascii=False)[:80])
    return c.summary("第三批（事名判真）")


def test_tool_path_still_adds():
    title("4. 正常加待办：模型直调路径仍正常出卡")
    c = Checker()
    from app.modules.planner import propose_todo_tool
    out = _json.loads(propose_todo_tool(title="游泳", date="周四",
                                       start="19:00", end="20:00"))
    prop = out.get("__proposal__") or {}
    c.check("propose_todo_tool(游泳,周四,19:00-20:00) → 出了待办确认条",
            prop.get("kind") == "todo_add", _json.dumps(out, ensure_ascii=False)[:80])
    c.check("卡片事名是「游泳」（不是半句话）", prop.get("title") == "游泳", prop.get("title"))
    c.check("卡片时间对（19:00-20:00）",
            prop.get("start") == "19:00" and prop.get("end") == "20:00",
            f"{prop.get('start')}-{prop.get('end')}")
    return c.summary("第四批（正常加待办）")


if __name__ == "__main__":
    fails = 0
    fails += test_rule_path_rejects_questions()
    fails += test_tool_path_rejects_questions()
    fails += test_tool_path_rejects_junk_title()
    fails += test_tool_path_still_adds()
    sys.exit(1 if fails else 0)
