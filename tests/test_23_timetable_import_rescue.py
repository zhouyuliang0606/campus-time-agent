"""课表导入的出卡兜底：模型没调 propose_timetable_change 也不许断链、更不许挂假卡。

报障原话（2026-10-03 学生截图）：上传课表文件《2026秋季课表-纯文本.txt》后，
管家只在文字里发了课程表格、「确认修改」卡没来；更糟的一次还触发了待办补条兜底，
把话术里的「确认修改」当成任务名，挂出「要不要把 确认修改 排进日程」的假卡。

根因两层：
  ① 前端 timetableImportMsg 说"先不要写入周表，等我确认后再导入"，
     模型把这句读成"先别出卡"——于是只发文字清单、不调 propose_timetable_change；
  ② main.py 的待办补条兜底不认识"导入课表"这种请求，模型一自称发过提案它就捞，
     捞出来的"任务名"是话术里的「确认修改」。

修法（对应三处）：
  · student.html 模板点名让模型调 propose_timetable_change（卡就是预览清单）；
  · planner.py 新增 looks_like_timetable_import / timetable_courses_from_reply
    （认出导入请求 + 从文字表格里抠回课程清单）；
  · main.py 的兜底分路：导入请求走课表补卡 / 如实说明，绝不进待办补条。

⚠️ 本文件必须给假配置 + 假引擎：沙箱清了 settings.json，不喂假 key 的话
   planner 会在 `mock_planner` 提前返回（skill 坑表：探针验错地方 = 走了离线 Mock），
   根本到不了出卡兜底；假引擎则是为了钉死"模型没调工具"这条确定性路径。
"""
from _harness import (Checker, SEED_COURSES, make_client, sandbox, title,
                      wipe_todos, write_timetable)
from app.modules.planner import looks_like_timetable_import, timetable_courses_from_reply

# 前端 timetableImportMsg 模板（照抄，改模板时要连这里一起改）
_IMPORT_MSG = (
    "我上传了一份课表文件《2026秋季课表-纯文本.txt》（共 140 字）。"
    "请先用 read_uploaded_file 读取内容，把解析出的课程整理成**预览清单**给我看"
    "（星期、时间、课程名、地点；\"第几节\"这类表述按学校常见上课时间换算成 HH:MM，"
    "day 用 1=周一 到 7=周日）。先不要写入周表，等我确认后再导入。"
)

# 模型"读了文件却不出卡"时爱发的表格（2026-10-03 真机复现原文的形状）
_TABLE_ANSWER = (
    "解析出来了，共 2 门课：\n\n"
    "| 星期 | 时间 | 课程 | 地点 |\n|---|---|---|---|\n"
    "| 周一 | 08:00–09:40 | 高等数学 | 教三-301 |\n"
    "| 周五 | 14:00–15:40 | 体育 | 大操场 |\n\n"
    "先给你看看，需要导入再说。"
)

# 截图里那次的另一种形状：自称发过提案 + 话术里有「确认修改」+ 具体时段
# —— 旧代码在这句话上挂出过「确认修改 | 2026-10-05 08:00-09:40」的假待办卡
_CLAIM_ANSWER = (
    "读到了，文件里时间已经是 HH:MM，直接整理如下：\n"
    "| 星期 | 时间 | 课程 | 地点 |\n|---|---|---|---|\n"
    "| 周一 | 08:00-09:40 | 高等数学 | 教三-301 |\n\n"
    "已经把「确认修改」卡发给你了，点确认就可以导入（周一 08:00-09:40）。"
)


class _FakeEngine:
    """恒定返回"只有文字、没有 options"的结果（钉死"模型没调工具"这条路径）。

    `options` 也做成类属性，好让 3-ter 那批钉住另一条路：
    模型**自己调了** propose_timetable_change（options 里已有卡），
    但文字里还是把课念了一遍 —— 那种回话也必须被换成一句"要不要改"。
    """

    def __init__(self, *a, **kw):
        pass

    async def run(self, message, history=None):
        return {"answer": _FakeEngine.answer, "trace": [
            {"step": 1, "phase": "💡 最终回答", "answer": _FakeEngine.answer}],
            "options": list(_FakeEngine.options or [])}


_FakeEngine.answer = ""
_FakeEngine.options = []


def _fake_model_env():
    """假配置 + 假客户端（语义层不再真联网）+ 假引擎，返回还原函数。

    ⚠️ app.main 顶部的 get_llm_config 是导入时绑定的，只改 app.config 看不见，
       必须连 app.main 名字一起打（本项目踩过两次的老坑）。
    """
    import app.main as M
    import app.config as _cfg
    import app.llm.client as _client
    _FakeEngine.answer = ""
    _FakeEngine.options = []
    old = (M.AgentEngine, M.get_llm_config, _cfg.get_llm_config, _client.DeepSeekClient)
    M.AgentEngine = _FakeEngine
    M.get_llm_config = lambda *a, **k: {"api_key": "test-key",
                                        "base_url": "http://x", "model": "m"}
    _cfg.get_llm_config = M.get_llm_config
    _client.DeepSeekClient = lambda *a, **k: None

    def _restore():
        M.AgentEngine, M.get_llm_config, _cfg.get_llm_config, _client.DeepSeekClient = old
    return _restore


def _ask(client, sid, message):
    return client.post("/api/chat", json={
        "message": message, "module": "planner", "session_id": sid}).json()


def _tt_card(opts):
    return next((o for o in (opts or []) if isinstance(o, dict)
                 and o.get("kind") == "timetable_change"), None)


def _todo_card(opts):
    return next((o for o in (opts or []) if isinstance(o, dict)
                 and o.get("kind") == "todo_add"), None)


def batch_a_detector():
    c = Checker()
    title("1. 探测器：认出导入课表的请求，别把闲聊/加待办拖进来")
    c.check("前端模板那句话 → True", looks_like_timetable_import(_IMPORT_MSG) is True)
    c.check("「重新导入课表」→ True", looks_like_timetable_import("重新导入课表") is True)
    c.check("「上传课表」→ True", looks_like_timetable_import("上传课表") is True)
    # 学生截图 2026-10-06：自然语言，中间带"了"/"并"等字
    c.check("「我上传了课表…请管家读取并给我预览清单」→ True",
            looks_like_timetable_import(
                "我上传了课表《2026秋季课表-纯文本.txt》，请管家读取并给我预览清单") is True)
    c.check("「请解析一下这份课表」→ True",
            looks_like_timetable_import("请解析一下这份课表") is True)
    c.check("「把这份课表读取一下」→ True",
            looks_like_timetable_import("把这份课表读取一下") is True)
    c.check("下单句「帮我加个游泳周四19:00」→ False",
            looks_like_timetable_import("帮我加个游泳周四19:00") is False)
    c.check("闲聊「今天天气怎么样」→ False",
            looks_like_timetable_import("今天天气怎么样") is False)
    c.check("只说「课表」不提文件/导入 → False",
            looks_like_timetable_import("明天周二有课表吗") is False)
    c.check("只说上传文件、没说课表 → False",
            looks_like_timetable_import("帮我上传个文件") is False)
    return c


def batch_b_parser():
    c = Checker()
    title("2. 解析器：把模型念的文字表格抠回课程清单")
    r = timetable_courses_from_reply(_TABLE_ANSWER)
    c.check("en dash 表格抠出 2 门", r is not None and len(r) == 2, str(r))
    c.check("第一条：周一 08:00-09:40 高等数学@教三-301",
            r and r[0] == {"day": 1, "start": "08:00", "end": "09:40",
                           "course": "高等数学", "location": "教三-301"})
    c.check("第二条：周五 14:00-15:40 体育@大操场",
            r and r[1] == {"day": 5, "start": "14:00", "end": "15:40",
                           "course": "体育", "location": "大操场"})

    r2 = timetable_courses_from_reply(
        "| day | 星期 | 时间 | 课程 | 地点 |\n|---|---|---|---|---|\n"
        "| 1 | 周一 | 08:00-09:40 | 高数 | 教三301 |")
    c.check("day 数字列与「周一」同现：数字格不抢课名",
            r2 and r2[0]["course"] == "高数" and r2[0]["day"] == 1, str(r2))

    r3 = timetable_courses_from_reply("| 周二 | 08：00到09：40 | 大学英语 | 文外201 |")
    c.check("全角冒号 + 「到」连接也认",
            r3 and r3[0]["day"] == 2 and r3[0]["start"] == "08:00"
            and r3[0]["end"] == "09:40" and r3[0]["course"] == "大学英语")

    r4 = timetable_courses_from_reply(
        "2026秋季学期课表\n\n周一 08:00-09:40 高等数学 教三-301\n周五 14:00-15:40 体育 大操场")
    c.check("纯文本行（学生文件原样）也认", r4 is not None and len(r4) == 2, str(r4))

    r5 = timetable_courses_from_reply("| 周六 | 10:00-11:40 | 体育 |")
    c.check("没有地点列也能抠（location 留空）",
            r5 and r5[0]["location"] == "" and r5[0]["course"] == "体育")

    # 学生真文件那两种写法（2026-10-04 补）：一天一行用「；」串好几门 + 列表体带括号地点
    r7 = timetable_courses_from_reply(
        "周一：08:00-09:40 高等数学 教三301；10:05-11:45 大学英语 外语楼204；"
        "14:00-15:40 数据结构 机房B")
    c.check("一天一行用「；」串三门 → 三门全抠出来", r7 is not None and len(r7) == 3, str(r7))
    c.check("其中第一门是 周一 08:00-09:40 高等数学@教三301",
            r7 and r7[0] == {"day": 1, "start": "08:00", "end": "09:40",
                             "course": "高等数学", "location": "教三301"})
    c.check("第三门是 数据结构@机房B（不是被吞掉）",
            r7 and r7[2]["course"] == "数据结构" and r7[2]["location"] == "机房B")

    r8 = timetable_courses_from_reply(
        "- **周二**：08:00-09:40 线性代数（教二108）｜晚上 19:00-20:40 大学英语（外语楼204）")
    c.check("列表体 + 「课名（地点）」括号写法 → 课名与地点分开",
            r8 and len(r8) == 2 and r8[0]["course"] == "线性代数"
            and r8[0]["location"] == "教二108", str(r8))
    c.check("时段词「晚上」不会被当成教室",
            r8 and r8[1]["course"] == "大学英语" and r8[1]["location"] == "外语楼204"
            and r8[1]["start"] == "19:00", str(r8))

    r6 = timetable_courses_from_reply(
        "| 周一 | 08:00-09:40 | 体育 | 大操场 |\n| 周一 | 08:00-09:40 | 体育 | 大操场 |")
    c.check("一模一样的两行去重成一条", r6 is not None and len(r6) == 1)

    c.check("通篇垃圾 → None（上层必须如实说，不许硬凑）",
            timetable_courses_from_reply("今天天气不错，课表改天再说。") is None)
    c.check("只有时间没有星期 → None",
            timetable_courses_from_reply("| 08:00-09:40 | 高数 |") is None)
    c.check("「第1-2节」没换算成钟点 → None",
            timetable_courses_from_reply("| 周一 | 第1-2节 | 高数 | 教三 |") is None)
    c.check("空文本 → None", timetable_courses_from_reply("") is None)
    return c


def batch_c_rescue_card():
    c = Checker()
    title("3. 主链路：导入请求 + 模型只发文字 → 系统补出「确认修改」卡")
    with sandbox():
        client = make_client()
        before = client.get("/api/timetable").json()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _TABLE_ANSWER
            d = _ask(client, "tt-1", _IMPORT_MSG)
        finally:
            restore()
        card = _tt_card(d.get("options"))
        c.check("options 里挂出 timetable_change 卡", card is not None,
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("卡上是完整课程清单（2 门）", card and len(card.get("courses") or []) == 2)
        c.check("课程内容对（周一 08:00 高等数学）",
                card and card["courses"][0]["course"] == "高等数学"
                and card["courses"][0]["day"] == 1)
        c.check("回话换成系统话术（提到确认修改卡）",
                "确认修改" in (d.get("answer") or ""), (d.get("answer") or "")[:50])
        c.check("轨迹写明是系统补的卡",
                any("补出「确认修改」卡" in (s.get("phase") or "")
                    for s in (d.get("trace") or [])))
        # 学生原话（2026-10-04）：「不用每门课单独拎出来」——回话只问"要不要改"
        c.check("回话只问要不要改",
                "要不要用这份课表替换现在的周表" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])
        c.check("回话里不许逐门念课（课名不在回话里）",
                not any(x in (d.get("answer") or "") for x in ("高等数学", "体育")),
                (d.get("answer") or "")[:60])
        after = client.get("/api/timetable").json()
        c.check("没点确认 → 周表一个字没动",
                before.get("courses") == after.get("courses"))
    return c


def batch_c2_file_source():
    c = Checker()
    title("3-bis. 文件原文兜底：模型回话抠不干净时，直接读库里那份文件")

    # 学生 2026-10-04 原话：「解析出来了，卡没出来……由于是修改课表，
    # 我认为可以弹是否更改课表的卡」——模型把清单念成一行多课的列表体，
    # 系统抠不全；这时文件原文（一天一行、；串多门）才是硬依据。
    FILE_TEXT = (
        "周一：08:00-09:40 高等数学 教三301；10:05-11:45 大学英语 外语楼204；"
        "14:00-15:40 数据结构 机房B\n"
        "周二：08:00-09:40 线性代数 教二108；晚上 19:00-20:40 大学英语 外语楼204\n")
    PARTIAL_REPLY = ("解析出来了：\n"
                     "- **周一**：08:00-09:40 高等数学（教三301）｜"
                     "10:05-11:45 大学英语（外语楼204）\n"
                     "- **周二**：08:00-09:40 线性代数（教二108）\n"
                     "（清单就这些）")

    with sandbox():
        client = make_client()
        client.post("/api/upload", files={"file": ("导入用课表.txt", FILE_TEXT.encode("utf-8"))})
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = PARTIAL_REPLY
            d = _ask(client, "tt-5", "我上传了一份课表文件《导入用课表.txt》（共 120 字）。"
                                     "请先用 read_uploaded_file 读取内容，整理成预览清单给我看。")
        finally:
            restore()
        card = _tt_card(d.get("options"))
        n = len(card.get("courses") or []) if card else 0
        c.check("回话只念了 3 门 → 卡上补齐到 5 门（取更全的文件原文）", n == 5,
                f"实际 {n} 门")
        c.check("晚上那节（19:00 大学英语）也在卡上",
                card and any(x["start"] == "19:00" and x["course"] == "大学英语"
                             for x in card["courses"]))
        c.check("回话里写明清单来自文件原文",
                "文件原文" in (d.get("answer") or ""), (d.get("answer") or "")[:60])

    # ⛔ 反向：消息没点名文件 → 不许拿"最近那份文件"猜（那可能是别的资料）
    with sandbox():
        client = make_client()
        client.post("/api/upload", files={"file": ("完全无关的资料.txt",
                                                   "学生会章程第一条……".encode("utf-8"))})
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = "我读了文件，里面没有课程信息。"
            d = _ask(client, "tt-6", "重新导入课表")
        finally:
            restore()
        c.check("没点名文件时绝不拿最近那份文件瞎猜 → 零卡",
                not (d.get("options") or []),
                str([o.get("kind") for o in (d.get("options") or [])]))
    return c


def batch_c3_no_course_list():
    c = Checker()
    title("3-ter. 上传课表只弹「是否修改」：模型自己出了卡，回话也不许念清单")

    # 学生原话（2026-10-04）：「如果是上传课表就弹是否修改就行，不用每门课单独拎出来」。
    # 最常见的一幕其实是**模型很乖地调了 propose_timetable_change**，
    # 但文字里还是把十几门课又念了一遍 —— 学生看到的是一整屏重复清单。
    VERBOSE = ("课表解析完成，共 3 门课：\n"
               "- 周一 08:00-09:40 高等数学 教三301\n"
               "- 周二 10:05-11:45 大学英语 外语楼204\n"
               "- 周三 14:00-15:40 数据结构 机房B\n"
               "请确认是否导入。")
    COURSES = [
        {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
        {"day": 2, "start": "10:05", "end": "11:45", "course": "大学英语", "location": "外语楼204"},
        {"day": 3, "start": "14:00", "end": "15:40", "course": "数据结构", "location": "机房B"},
    ]

    with sandbox():
        client = make_client()
        before = client.get("/api/timetable").json()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = VERBOSE
            _FakeEngine.options = [{"kind": "timetable_change",
                                    "summary": "导入 2026 秋季课表",
                                    "courses": COURSES}]
            d = _ask(client, "tt-7", _IMPORT_MSG)
        finally:
            restore()
        card = _tt_card(d.get("options"))
        ans = d.get("answer") or ""
        c.check("模型自己出的卡照旧在（没被兜底换掉）",
                card is not None and len(card.get("courses") or []) == 3,
                str(len(card.get("courses") or [])) if card else "无卡")
        c.check("课程清单原样留在卡里（写库要用）",
                card and card["courses"][0]["course"] == "高等数学")
        c.check("回话换成一句「要不要改」",
                "要不要用这份课表替换现在的周表" in ans, ans[:60])
        c.check("回话里不许出现课名（不逐门念）",
                not any(x in ans for x in ("高等数学", "大学英语", "数据结构")), ans[:80])
        c.check("门数还在回话里", "3 门课" in ans, ans[:60])
        after = client.get("/api/timetable").json()
        c.check("没点确认 → 周表一个字没动",
                before.get("courses") == after.get("courses"))

    # 反向：不是上传课表（普通加课请求）→ 不许动模型的回话
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = "好，我把周三下午那节加上去，你看看对不对。"
            _FakeEngine.options = [{"kind": "timetable_change",
                                    "summary": "加一节数据结构",
                                    "courses": COURSES[:1]}]
            d = _ask(client, "tt-8", "帮我周三下午加一节数据结构")
        finally:
            restore()
        c.check("非上传请求：模型回话原样保留（不被换成导入话术）",
                "我把周三下午那节加上去" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])
    return c


def batch_c4_first_message_direct():
    c = Checker()
    title("3-quater. 第一句话就直接出课表卡：不等模型，也不许被待办抢走")

    # 报障原话（2026-10-07 学生）：「上传课表被当成了问事所以没有弹卡，
    # 第二次追问是代办弹了」——两处病，一副药：
    #   ① 规则层明说「导入课表」= 硬信号，语义层猜的 chat / 问事必须让位；
    #   ② 分支要排在**所有待办分支之前**，否则等到「第二句」就被待办补条接走了。
    # 钉的是「第一句话」，所以假引擎这次**一个字都不说**：
    # 模型还没开口，卡就必须已经挂出来了。
    FILE_TEXT = (
        "周一：08:00-09:40 高等数学 教三301；10:05-11:45 大学英语 外语楼204\n"
        "周二：08:00-09:40 线性代数 教二108\n")

    with sandbox():
        client = make_client()
        client.post("/api/upload", files={
            "file": ("2026秋季课表-纯文本.txt", FILE_TEXT.encode("utf-8"))})
        before = client.get("/api/timetable").json()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ""      # 模型沉默 → 卡只能是系统出的
            d = _ask(client, "tt-7", _IMPORT_MSG)
        finally:
            restore()
        card = _tt_card(d.get("options"))
        n = len(card.get("courses") or []) if card else 0
        c.check("第一句话就挂出 timetable_change 卡（模型还没开口）", card is not None,
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("卡上是文件原文里的 3 门课", n == 3, f"实际 {n} 门")
        c.check("回话写明清单来自文件原文", "文件原文" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])
        tr = " / ".join((s.get("phase") or "") for s in (d.get("trace") or []))
        c.check("轨迹写「办事」不再写「问事」", "办事" in tr and "问事" not in tr, tr[:120])
        c.check("第一句话绝不出代办卡（「第二次弹代办」的反面）",
                _todo_card(d.get("options")) is None,
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("没点确认 → 周表一个字没动",
                before.get("courses") == client.get("/api/timetable").json().get("courses"))

    # ⛔ 反向：说的是课表但**没上传过任何文件** → 不许硬凑一张空卡，
    #    该交回模型那条路（模型还能替他把「第几节」换算成钟点）。
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = "你还没传文件呢，传一份我就能帮你排。"
            d = _ask(client, "tt-8", "帮我导入课表《还没传的课表.txt》")
        finally:
            restore()
        c.check("文件不存在 → 不抢模型的活，零卡", not (d.get("options") or []),
                str([o.get("kind") for o in (d.get("options") or [])]))
    return c


def batch_d_no_fake_todo_card():
    c = Checker()
    title("4. 反向三连：假待办卡必须绝迹，老的待办补条不许被误伤")

    # 4a 原始 bug 钉死：导入请求 + 自称发过提案的话术 → 绝不许出现 todo_add
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _CLAIM_ANSWER
            d = _ask(client, "tt-2", _IMPORT_MSG)
        finally:
            restore()
        c.check("截图那幕不再挂「确认修改」假待办卡", _todo_card(d.get("options")) is None,
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("同一幕补出了正经课表卡（表格可抠）",
                _tt_card(d.get("options")) is not None)

    # 4b 话术可抠不出清单时：不许挂假卡，改如实说明
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ("已经把「确认修改」卡发给你了，"
                                  "点确认就可以导入（周一 08:00-09:40）。")
            d = _ask(client, "tt-3", _IMPORT_MSG)
        finally:
            restore()
        c.check("抠不出清单 → 没有任何卡", not (d.get("options") or []),
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("回话如实说没出卡",
                "没能把课程整理成确认卡" in (d.get("answer") or ""),
                (d.get("answer") or "")[:60])

    # 4c 老的待办补条不许被这轮改坏：非导入请求照旧出待办卡
    _TODO_PROSE = ("**待办提案** - 事项：健身 - 时间：周二 16:00~17:30\n"
                   "提案已发出，请点确认条上的【确认】。")
    with sandbox():
        # ★ 起始状态自己种（2026-10-14 整族扫）：这一档出的**候选时段个数**吃周表
        #   —— 实测本机真表 1 个段、种子表 3 个段。不种就等于把"我周四有多忙"写进断言。
        write_timetable(SEED_COURSES)
        wipe_todos()
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _TODO_PROSE
            d = _ask(client, "tt-4", "今晚去健身怎么样")
        finally:
            restore()
        kinds = [o.get("kind") for o in (d.get("options") or []) if isinstance(o, dict)]
        c.check("非导入请求照旧出待办卡（todo_add 或候选时段卡都算）",
                "todo_add" in kinds or "todo_slots" in kinds, str(kinds))
        c.check("卡上的事名来自学生说的「健身」",
                any("健身" in (o.get("title") or "")
                    for o in (d.get("options") or []) if isinstance(o, dict)), str(kinds))
        c.check("非导入请求绝不挂课表卡", _tt_card(d.get("options")) is None)
        # ⚠️ 2026-10-14 实测改口：这句话**已经走不到补条**了 —— 学生说了"今晚"
        #   （哪天有了、钟点没有），规则层直接摊候选时段（`todo_slots`），
        #   模型那段话一个字没参与。这是 TimeSpec 那一轮的既定规格（判据见 test_40 D 批），
        #   不是回归。所以"补条那条路没被改坏"必须另起一个**真走得到**的用例：4c-bis。
        c.check("（记录规格）「今晚去健身怎么样」走的是候选时段，不是补条",
                "todo_slots" in kinds, str(kinds))

    # 4c-bis 补条那一档本身：规则层没接住的话，系统才从模型回话里补出 todo_add
    with sandbox():
        write_timetable(SEED_COURSES)
        wipe_todos()
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _TODO_PROSE
            # 「帮我看看今晚」—— 规则层没接（既不是加待办也不是"该做什么"那一族，
            # 2026-10-14 test_45 新加的判据不放行它），
            # 这一轮才真的落到「回答没给结构化结果 → 从话里补条」那一档。
            d = _ask(client, "tt-4b", "帮我看看今晚")
        finally:
            restore()
        todo = _todo_card(d.get("options"))
        c.check("补条那一档照旧走得通：todo_add + 健身 + 16:00-17:30",
                todo is not None and todo.get("title") == "健身"
                and todo.get("start") == "16:00" and todo.get("end") == "17:30",
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("trace 写明是系统「补出确认条」（不是模型自己调的工具）",
                any("补出确认条" in (s.get("phase") or "") for s in (d.get("trace") or [])),
                [s.get("phase") for s in (d.get("trace") or [])])
        c.check("补条这一路也绝不挂课表卡", _tt_card(d.get("options")) is None)
    return c


def batch_e_frontend_template():
    c = Checker()
    title("5. 前端模板：点名工具，不再说\"先别写入\"")
    src = open("app/static/student.html", encoding="utf-8").read()
    i, j = src.find("function timetableImportMsg"), src.find("上传课表文件，管家读取后给出预览清单")
    seg = src[i: j if i < j else i + 2000]
    c.check("模板点名 propose_timetable_change", "propose_timetable_change" in seg)
    c.check("模板仍要求先 read_uploaded_file 读文件", "read_uploaded_file" in seg)
    c.check("误导句「先不要写入周表，等我确认后再导入」已删",
            "先不要写入周表，等我确认后再导入" not in seg)
    c.check("模板不许教学生等一句没有分支接的「确认导入」",
            "确认导入" not in seg)
    # 学生原话：不用每门课单独拎出来 —— 模板要明说"别在回话里把课念一遍"
    c.check("模板不许把课一门门念出来", "不要把课一门门再念一遍" in seg)
    return c


def batch_f_diagnostics():
    c = Checker()
    title("6. 出卡环节诊断：即使没出卡，学生也能从 trace 看出卡在哪一档")

    def _trace_text(d):
        return " || ".join(f"{s.get('phase', '')} / {s.get('answer', '')}"
                           for s in (d.get("trace") or []))

    # 6a 命中 + 成功出卡：trace 要列全「探测→回话抠课→文件抠课→出卡」
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _TABLE_ANSWER
            d = _ask(client, "tt-d1", _IMPORT_MSG)
        finally:
            restore()
        t = _trace_text(d)
        c.check("命中：trace 含「探测：命中」", "探测：命中" in t, t[:120])
        # 文案对不上（2026-10-14 查）：9be6537「轨迹文案去掉技术内脏」把这档改名成
        # 「从这轮回答里抠课程」（不对学生说"模型"），断言当时没跟着改，一直红着。
        c.check("命中：trace 含「从这轮回答里抠课程」", "从这轮回答里抠课程" in t, t[:120])
        c.check("命中：trace 含「从文件原文抠课」", "从文件原文抠课" in t, t[:120])
        c.check("命中：trace 含「出卡」结论", "出卡" in t, t[:200])
        c.check("命中：同时确实挂出了 timetable_change 卡",
                _tt_card(d.get("options")) is not None)

    # 6b 命中但抠不出课 → 如实没出卡，trace 写明「没出卡」
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = "今天天气不错，课表改天再说。"
            d = _ask(client, "tt-d2", _IMPORT_MSG)
        finally:
            restore()
        t = _trace_text(d)
        c.check("没出卡：trace 含「没出卡 → 如实说明」", "没出卡" in t, t[:200])
        c.check("没出卡：确实没有任何卡", not (d.get("options") or []),
                str([o.get("kind") for o in (d.get("options") or [])]))

    # 6c 逃过正则（提了课表但说法不匹配）→ trace 标「未命中」，并提示用模板按钮
    # 注意：像「看看我的课表」这类会被样本库误判成「看待办列表」提前返回，
    # 那属于路由层问题（会显示"列出待办"而非课表诊断）；这里选一个能走到出卡环节的措辞。
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = _TABLE_ANSWER
            d = _ask(client, "tt-d3", "我传了课表你帮看看")
        finally:
            restore()
        t = _trace_text(d)
        c.check("逃过正则：trace 含「探测：未命中（提到课表但说法逃过正则）」",
                "未命中（提到课表但说法逃过正则）" in t, t[:200])
        c.check("逃过正则：被当成普通请求，未挂课表卡",
                _tt_card(d.get("options")) is None)
    return c


def main():
    results = [
        batch_a_detector(),
        batch_b_parser(),
        batch_c_rescue_card(),
        batch_c2_file_source(),
        batch_c3_no_course_list(),
        batch_c4_first_message_direct(),
        batch_d_no_fake_todo_card(),
        batch_e_frontend_template(),
        batch_f_diagnostics(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 课表导入出卡兜底总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
