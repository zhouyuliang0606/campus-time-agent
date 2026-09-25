"""第四批测试：学生助手「性格 + 可勾选候选 + 语音」相关能力（Req F）。

覆盖四件事：
1. /api/student-personas 返回三套学生可选性格，字段齐全；
2. 规划模块在无密钥时走 Mock 助手：提议候选 → options 非空、awaiting_choice=True；
3. 学生在候选卡上勾一段、点【加入日程】→ 系统真的把待办写进日程；
4. 性格确实注入到回答：pal 的回答带「宝」、pro 的回答带「空档如下」；
5. 引擎能捕获 propose_slots 工具的参数，作为 options 带回前端（不依赖真实大模型）。

全部跑在数据沙箱里，绝不碰你的真实演示数据。
"""
import asyncio
import json
import os

from _harness import Checker, sandbox, make_client, data_path, title


def _seed_timetable():
    """往沙箱数据目录写一份示例周表（演示种子数据里周表是空的，
    Mock 规划助手需要先有周表才能查空档）。

    注意：app.store.DATA_DIR 在首次 import 时定死，跨多个 sandbox() 块会过期，
    所以这里手动把它指回当前沙箱目录；并用 store 自己的 save_timetable 落盘
    （它会按 {"courses": [...]} 格式写到 DATA_DIR/student/ 下，与读取路径一致）。
    """
    import app.store as _store
    _store.DATA_DIR = os.environ.get("CAMPUSTIME_DATA_DIR") or _store.DATA_DIR
    sample = [
        {"day": d, "start": "08:00", "end": "09:40",
         "course": f"课程{d}", "location": "教学楼"}
        for d in range(1, 6)
    ]
    _store.save_timetable(sample)


def test_persona_endpoint():
    title("1. 学生助手性格接口")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/api/student-personas")
        c.check("接口返回 200", r.status_code == 200)
        personas = (r.json() or {}).get("personas", [])
        c.check("返回三套性格", len(personas) == 3, f"实际 {len(personas)}")
        keys = [p.get("key") for p in personas]
        c.check("三个 key 是 senpai/pal/pro", keys == ["senpai", "pal", "pro"], str(keys))
        for p in personas:
            ok = all(p.get(k) for k in ("key", "name", "emoji", "desc", "style"))
            c.check(f"性格 {p.get('key')} 字段齐全", ok)
    return c.summary("第一批（性格接口）")


def test_mock_propose_and_confirm():
    title("2. 规划 Mock：提议候选 → 学生确认 → 写入日程")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_timetable()  # Mock 需要先有周表才能查空档

        # 进规划模块提一件要做的事（无密钥 → 走确定性的 Mock 助手）
        r1 = client.post("/api/chat", json={
            "message": "我要复习线代", "module": "planner", "session_id": "pf1",
        })
        c.check("提议接口 200", r1.status_code == 200)
        d1 = r1.json()
        c.check("回了候选时间段 options", bool(d1.get("options")), f"数量 {len(d1.get('options', []))}")
        c.check("等待学生勾选 awaiting_choice=True", d1.get("awaiting_choice") is True)
        opts = d1.get("options") or []
        first = opts[0] if opts else {}
        # 第七轮规格（学生原话：「将多种合理时间提出让我挑选，也是弹窗里进行选择」）：
        # 没说几点时出的是 **todo_slots 候选卡**——一张卡里装着好几段，
        # date/start/end 在 `slots[]` 里，不在卡的顶层（顶层只有 title 和说明）。
        # 所以这里按新形状核对，别再用老的单条（todo_add）形状去取字段。
        c.check("出的是「挑时间」候选卡 todo_slots", first.get("kind") == "todo_slots",
                first.get("kind"))
        c.check("候选卡上带着事名（不是谁也没说过的半句话）",
                first.get("title") == "复习线代", first.get("title"))
        slots = first.get("slots") or []
        c.check("候选卡里摊出了时间段", len(slots) >= 1, f"段数 {len(slots)}")
        slot = slots[0] if slots else {}
        for f in ("date", "start", "end"):
            c.check(f"候选时段含字段 {f}", bool(slot.get(f)), str(slot.get(f)))

        # 模拟学生在弹框里勾一段、点【加入日程】（前端直连 /api/todos/batch）
        # —— 不再走"回一句「确认：日期 起-止 标题」"那条老路：候选卡上还没勾，
        # 系统不会替他挑一个写进去（写进去的是真安排，不能替他做主）。
        if slot:
            r2 = client.post("/api/todos/batch", json={
                "session_id": "pf1", "title": first.get("title") or "复习线代",
                "slots": [{"date": slot["date"], "start": slot["start"],
                           "end": slot["end"]}],
            })
            d2 = r2.json()
            c.check("勾选的这一段真的写进去了",
                    len(d2.get("added") or []) == 1,
                    json.dumps(d2, ensure_ascii=False)[:90])
            c.check("回执里报了这一条（学生看得见加了什么）",
                    "已加入日程" in (d2.get("message") or "")
                    and "复习线代" in (d2.get("message") or ""),
                    (d2.get("message") or "")[:40])

            # 真去待办列表里看，确认落库了
            todos = (client.get("/api/todos").json() or {}).get("todos", [])
            titles = [t.get("title") for t in todos]
            c.check("待办列表里出现了这条",
                    any("复习线代" in (t or "") for t in titles), str(titles[:3]))
    return c.summary("第二批（提议→勾选→写入）")


def test_persona_injection():
    title("3. 性格腔调真的注入了（Mock 路径可验证）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_timetable()  # 有周表才能看到性格腔调的差异（否则都走「先上传」）

        def propose(persona):
            r = client.post("/api/chat", json={
                "message": "我要复习高数", "module": "planner",
                "session_id": "pf_" + (persona or "x"), "persona": persona,
            })
            return (r.json() or {}).get("answer", "")

        pal = propose("pal")
        pro = propose("pro")
        senpai = propose("senpai")
        c.check("暖心朋友(pal) 带「宝」", "宝" in pal, pal[:24])
        c.check("高效干练(pro) 带「空档如下」", "空档如下" in pro, pro[:24])
        c.check("三种性格回答互不相同", len({pal, pro, senpai}) == 3)

        # 非法 key 被忽略：传个乱七八糟的，应退化成默认（学长）腔调，不报错
        bad = client.post("/api/chat", json={
            "message": "我要复习英语", "module": "planner", "session_id": "pf_bad", "persona": "hacker",
        })
        c.check("非法性格 key 不 500", bad.status_code == 200 and "error" not in (bad.json() or {}))
    return c.summary("第三批（性格注入）")


def test_engine_captures_propose_slots():
    title("4. 引擎捕获 propose_slots → 前端拿到结构化候选（无需真实大模型）")
    c = Checker()
    with sandbox():
        from app.agent.engine import AgentEngine
        from app.modules.planner import build_tools as planner_tools

        fake_options = [
            {"id": "x1", "title": "复习线代", "date": "2026-09-24", "weekday": "周三",
             "start": "15:40", "end": "17:10", "minutes": 90},
        ]
        # 注册真实的时间规划工具箱，propose_slots 才被引擎识别并捕获参数
        engine = AgentEngine(system_prompt="测试", tools=planner_tools())

        # 不真连大模型：把 llm.chat 换成"只调一次 propose_slots 工具"的桩
        async def fake_chat(messages, tools=None):
            return {"tool_calls": [{
                "id": "call_1",
                "function": {
                    "name": "propose_slots",
                    "arguments": json.dumps({"options_json": json.dumps(fake_options)}),
                },
            }]}
        engine.llm.chat = fake_chat

        result = asyncio.run(engine.run("帮我排复习线代", history=[]))
        c.check("引擎捕获到 options", result.get("options") == fake_options)
        c.check("awaiting_choice 置真", result.get("awaiting_choice") is True)
        c.check("返回里同时带 answer 和 trace", "answer" in result and "trace" in result)
    return c.summary("第四批（引擎捕获候选）")


def test_student_page_markup():
    title("5. 学生端页面已带上新 UI（设置入口 / 正在思考 / 语音 / 候选卡片）")
    c = Checker()
    with sandbox():
        client = make_client()
        # 不需要进沙箱写数据，纯检查页面 HTML 是否包含新增元素
        r = client.get("/student")
        c.check("学生端页面 200", r.status_code == 200)
        html = r.text or ""
        c.check("学生端有「设置」入口指向 /settings", "/settings" in html)
        c.check("有语音按钮 id=mic", 'id="mic"' in html)
        c.check("有「正在思考…」提示", "正在思考" in html)
        c.check("接入了 Web Speech 语音识别", "webkitSpeechRecognition" in html or "SpeechRecognition" in html)
        c.check("候选卡片样式 opt-card 已定义", ".opt-card" in html)
        c.check("有「确认所选」按钮文案", "确认所选" in html)
        # 学生端**不应**再渲染思考轨迹：检查真正的「渲染文案」已移除
        # （注释里提到这个词不算，所以精确匹配带 🧠 的那句渲染串）
        c.check("学生端不再渲染思考轨迹文案", "🧠 Agent 思考轨迹" not in html)
        c.check("页面带 no-store 防缓存", "no-store" in (r.headers.get("cache-control", "") or "")
                or "no-store" in (r.headers.get("Cache-Control", "") or ""))
    return c.summary("第五批（学生端页面 UI）")


def test_settings_page_markup():
    title("6. 设置页：助手名字 + 性格选项 + 返回登录/返回学生端")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/settings")
        c.check("设置页 200", r.status_code == 200)
        html = r.text or ""
        c.check("有「助手名字」设置项", "助手名字" in html)
        c.check("名字存到 localStorage(campustime_name)", "campustime_name" in html)
        c.check("有「助手性格」设置项", "助手性格" in html)
        c.check("从 /api/student-personas 拉性格", "/api/student-personas" in html)
        c.check("含「返回登录」入口", "返回登录" in html and 'href="/"' in html)
        c.check("含「返回学生端」入口", "返回学生端" in html and 'href="/student"' in html)
        c.check("设置页也带 no-store", "no-store" in (r.headers.get("cache-control", "") or "")
                or "no-store" in (r.headers.get("Cache-Control", "") or ""))
    return c.summary("第六批（设置页 UI）")


if __name__ == "__main__":
    fails = 0
    fails += test_persona_endpoint()
    fails += test_mock_propose_and_confirm()
    fails += test_persona_injection()
    fails += test_engine_captures_propose_slots()
    fails += test_student_page_markup()
    fails += test_settings_page_markup()
    import sys
    sys.exit(1 if fails else 0)
