"""「代办」三类口语的意图接住回归（人话：学生用口语说待办，不能掉给模型编答案）。

这一批钉的是三句之前会"不弹弹窗 / 掉进模型编瞎话"的口语：
  1. 「我要加代办」—— 只有加的意图、没给具体内容 → 系统接住，问"要加什么事"，不谎称已加；
  2. 「我的代办呢 / 看看我的代办」—— 纯读，系统真查 list_todos() 念出来，不编"你有 3 条"；
  3. 「代办显示不出来」—— 整张列表看不见，系统把待办念出来（比回"确实还没有"有用）；
  附带：「健身显示不出来」—— 具体某件事，走"看不见 X"分支真查库核实。

另外把空待办的情况也钉住：一句待办都没有时，回答"还没有任何待办"，不报错。
"""
import datetime

from _harness import Checker, make_client, sandbox, title

code = 0


def seed_todos(today):
    """往沙箱里写两条待办，当作"学生之前已经有的安排"。"""
    from app.store import add_todo
    add_todo("健身", today, "19:00", "20:00")
    add_todo("交电费", today, "10:00", "10:30")


def test_todo_intent_gaps():
    """三句口语 + 空列表，全部由系统接住。"""
    title("1. 代办口语意图接住（系统判定，不丢给模型）")
    c = Checker()
    from app.modules.planner import wants_add_todo, wants_list_todo

    today = datetime.date.today().isoformat()

    # —— 单元层：两个开关本身得认得这些话 ——
    c.check("wants_add_todo 认得「我要加代办」",
            wants_add_todo("我要加代办"))
    c.check("wants_add_todo 认得「加个代办」",
            wants_add_todo("加个代办"))
    c.check("wants_list_todo 认得「我的代办呢」",
            wants_list_todo("我的代办呢"))
    c.check("wants_list_todo 认得「看看我的代办」",
            wants_list_todo("看看我的代办"))
    c.check("wants_list_todo 认得「代办显示不出来」",
            wants_list_todo("代办显示不出来"))

    with sandbox():
        client = make_client()
        seed_todos(today)

        # ① 「我要加代办」：接住，追问要加什么事，不谎称已加、不弹卡
        r = client.post("/api/chat", json={
            "message": "我要加代办", "session_id": "g1-add"})
        d = r.json()
        c.check("「我要加代办」落到了 planner（没掉模型）",
                d.get("module") == "planner", d.get("module", ""))
        c.check("回答在问要加什么事",
                "什么事" in (d.get("answer") or ""), (d.get("answer") or "")[:40])
        c.check("没谎称已经加上",
                "已经加上" not in (d.get("answer") or "")
                and "已加入" not in (d.get("answer") or ""))
        c.check("没凭空弹确认卡（options 为空）", not (d.get("options") or []))

        # ② 「我的代办呢」：系统真查库，把已有的念出来
        r = client.post("/api/chat", json={
            "message": "我的代办呢", "session_id": "g2-list"})
        d = r.json()
        c.check("「我的代办呢」被系统读取（module=planner）",
                d.get("module") == "planner", d.get("module", ""))
        c.check("把已播种的待办念出来了（健身 / 交电费）",
                "健身" in (d.get("answer") or "")
                and "交电费" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])
        c.check("纯读：没弹确认卡", not (d.get("options") or []))

        # ②变体 「看看我的代办」
        r = client.post("/api/chat", json={
            "message": "看看我的代办", "session_id": "g3-list"})
        d = r.json()
        c.check("「看看我的代办」同样念出待办",
                "健身" in (d.get("answer") or "")
                and "交电费" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])

        # ③ 「代办显示不出来」：整张列表看不见 → 走读列表分支，念出来
        r = client.post("/api/chat", json={
            "message": "代办显示不出来", "session_id": "g4-show"})
        d = r.json()
        c.check("「代办显示不出来」走了读列表（module=planner）",
                d.get("module") == "planner", d.get("module", ""))
        c.check("把待办念出来（而不是回'确实还没有'）",
                "健身" in (d.get("answer") or ""), (d.get("answer") or "")[:60])

        # 附带：具体某件事「健身显示不出来」→ 走"看不见 X"分支真查库
        r = client.post("/api/chat", json={
            "message": "健身显示不出来", "session_id": "g5-miss"})
        d = r.json()
        c.check("「健身显示不出来」被当具体报障核实（module=planner）",
                d.get("module") == "planner", d.get("module", ""))
        c.check("核实后告知健身确实在日程里",
                "在" in (d.get("answer") or "")
                and "健身" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])

    # —— 空列表分支：清空沙箱里的待办，验证"还没有任何待办" ——
    title("2. 空待办时如实说「还没有任何待办」")
    with sandbox():
        client = make_client()
        from app.store import delete_todo, list_todos
        for t in list_todos():
            delete_todo(t["id"])
        r = client.post("/api/chat", json={
            "message": "我的待办呢", "session_id": "g6-empty"})
        d = r.json()
        c.check("清空后回答'还没有任何待办'",
                "还没有任何待办" in (d.get("answer") or ""),
                (d.get("answer") or "")[:40])
        c.check("空列表也不报错（module=planner）",
                d.get("module") == "planner", d.get("module", ""))

    code = c.summary("待办口语意图")


if __name__ == "__main__":
    test_todo_intent_gaps()
