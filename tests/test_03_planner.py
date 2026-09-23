"""第三批测试：学生个人日程（周表 / 空档 / 待办 / 月表 / 会话历史 / 路由）。

人话：这一组测的是"学生那张表"——课表能不能导入、AI 找空档会不会撞课、
待办能不能插进课间、月表上每天有几项、多轮商量靠什么记住上下文。

注意：涉及大模型对话的部分，在没有 Key 的环境下只能验证到"路由正确 +
失败时优雅降级"，真正的多轮商量要配了 Key 才有意义。
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, make_client, sandbox, title  # noqa: E402

# 会改 timetable.json / todos.json / 会话文件，全部关进沙箱
with sandbox():
    client = make_client()
    c = Checker()

    # 这几个 import 必须放在沙箱里：数据目录在 import 那一刻就定下来了
    from app.store import append_conversation, clear_conversation, get_conversation  # noqa: E402
    from app.modules.planner import (  # noqa: E402
        add_todo_tool, find_free_slots, import_timetable, update_todo_status,
    )

    TODAY = datetime.date.today().isoformat()

    title("1. 导入周表")
    wd = datetime.date.today().isoweekday()
    courses = [
        {"day": wd, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
        {"day": wd, "start": "10:00", "end": "11:40", "course": "线性代数", "location": "教二205"},
        {"day": wd, "start": "14:00", "end": "15:40", "course": "大学英语", "location": "外语楼"},
    ]
    msg = import_timetable(json.dumps(courses, ensure_ascii=False))
    c.check("导入课程成功", "已更新" in msg, msg)
    r = client.get("/api/timetable")
    c.check("接口读回周表", len(r.json()["courses"]) == 3, f"{len(r.json()['courses'])} 门")
    c.check("带 updated_at", bool(r.json().get("updated_at")))
    c.check("非法 day 被拒", "越界" in import_timetable('[{"day":9,"start":"08:00","end":"09:00"}]'))
    c.check("时间倒挂被拒", "不合法" in import_timetable('[{"day":1,"start":"10:00","end":"09:00"}]'))

    title("2. 查空档（应避开已排课程）")
    slots = find_free_slots(TODAY, 60)
    c.check("返回若干空档", bool(slots) and "error" not in slots[0], f"{len(slots)} 段")
    if slots and "start" in slots[0]:
        overlaps = [
            s for s in slots
            if not (s["end"] <= "08:00" or s["start"] >= "15:40"
                    or ("09:40" <= s["start"] and s["end"] <= "10:00")
                    or ("11:40" <= s["start"] and s["end"] <= "14:00"))
        ]
        c.check("空档不与课程重叠", not overlaps, str(overlaps[:1]))
    c.check("查不存在的日期有提示", "不是合法日期" in str(find_free_slots("不是日期", 60)))

    title("3. 待办写入与防撞课")
    r1 = add_todo_tool("复习高数第三章", TODAY, "19:00", "20:30")
    c.check("写入空档的待办成功", "已加入日程" in r1, r1[:40])
    r2 = add_todo_tool("写实验报告", TODAY, "08:30", "10:00")
    c.check("与课程冲突被拒绝", "撞了" in r2, r2[:40])
    c.check("非法时间被拒", "不合法" in add_todo_tool("x", TODAY, "10:00", "09:00"))

    title("4. 空档随待办动态变化")
    after = find_free_slots(TODAY, 60)
    c.check(
        "19:00-20:30 已不再空闲",
        all(not (s.get("start", "") <= "19:00" and s.get("end", "") >= "20:30") for s in after),
        f"剩 {len(after)} 段",
    )

    title("5. 接口层：待办增删改查")
    allt = client.get("/api/todos").json()["todos"]
    c.check("列出待办", len(allt) >= 1, f"{len(allt)} 项")
    c.check("按日期过滤", bool(client.get(f"/api/todos?date={TODAY}").json()["todos"]))
    r = client.post("/api/todos", json={"title": "交作业", "date": TODAY, "start": "21:00", "end": "21:30"})
    c.check("手动新增待办", r.status_code == 200 and r.json()["ok"])
    t2 = r.json()["todo"]["id"]
    c.check("缺字段被拒 400", client.post("/api/todos", json={"title": "x"}).status_code == 400)
    c.check("标记完成", client.put(f"/api/todos/{t2}", json={"status": "done"}).json()["todo"]["status"] == "done")
    c.check("改状态用工具也行", "已更新" in update_todo_status(t2, "planned"))
    c.check("删除待办", client.delete(f"/api/todos/{t2}").json()["ok"])
    c.check("删不存在的 404", client.delete("/api/todos/nope").status_code == 404)

    title("6. 月表数据（月视图角标的数据源）")
    tmr = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    mon = datetime.date.today().strftime("%Y-%m")

    # 先量一次"加之前有多少项"，再加两条，最后比对增量。
    # 这样不管演示数据里本来有没有待办，结论都成立（测试用例要能独立复现）。
    base = len(client.get(f"/api/todos/month?month={mon}").json()["days"].get(tmr, []))
    add_todo_tool("取快递", tmr, "12:00", "12:30")
    add_todo_tool("开会", tmr, "16:00", "17:00")

    m = client.get(f"/api/todos/month?month={mon}").json()
    c.check("按月聚合返回 days", isinstance(m["days"], dict), f"共 {len(m['days'])} 天有待办")
    got = m["days"].get(tmr, [])
    c.check("明天多出 2 项（角标数据）", len(got) == base + 2, f"{base} -> {len(got)}")
    c.check("新加的两项都在里面", {"取快递", "开会"} <= {t["title"] for t in got})
    c.check("今天至少 1 项", len(m["days"].get(TODAY, [])) >= 1)
    c.check("查空月份返回空", client.get("/api/todos/month?month=2099-01").json()["days"] == {})

    title("7. 会话历史（多轮商量的前提）")
    sid = "test-session-1"
    clear_conversation(sid)
    c.check("初始为空", get_conversation(sid) == [])
    append_conversation(sid, "user", "我要复习高数")
    append_conversation(sid, "assistant", "建议今晚 19:00-20:30，可以吗？")
    hist = get_conversation(sid)
    c.check("记下两轮", len(hist) == 2 and hist[0]["role"] == "user" and hist[1]["role"] == "assistant")
    c.check("内容正确", "19:00" in hist[1]["content"])
    for i in range(30):
        append_conversation(sid, "user", f"消息{i}")
    c.check("上限 20 条不会无限涨", len(get_conversation(sid)) == 20, f"实际 {len(get_conversation(sid))}")
    clear_conversation(sid)
    c.check("清空会话", get_conversation(sid) == [])
    c.check("重置接口可用", client.post("/api/chat/reset", json={"session_id": sid}).json()["ok"])

    title("8. planner 路由与降级")
    r = client.post("/api/chat", json={"message": "我有个待办要排时间", "module": "planner", "session_id": "s1"})
    body = r.json()
    c.check("命中 planner 模块", body.get("module") == "planner", body.get("module"))
    c.check("无 Key 时仍不 500", r.status_code == 200)
    c.check("回传 session_id", body.get("session_id") == "s1", body.get("session_id"))
    r2 = client.post("/api/chat", json={"message": "上传课表"})
    c.check("未指定 module 时按关键词走 planner", r2.json().get("module") == "planner", r2.json().get("module"))
    r3 = client.post("/api/chat", json={"message": "随便聊聊"})
    c.check("不生成 session 也能跑", bool(r3.json().get("session_id")))

    sys.exit(c.summary("第三批（学生日程）"))
