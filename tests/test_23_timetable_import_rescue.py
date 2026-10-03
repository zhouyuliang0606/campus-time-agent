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
from _harness import Checker, make_client, sandbox, title
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
    """恒定返回"只有文字、没有 options"的结果（钉死"模型没调工具"这条路径）。"""

    def __init__(self, *a, **kw):
        pass

    async def run(self, message, history=None):
        return {"answer": _FakeEngine.answer, "trace": [
            {"step": 1, "phase": "💡 最终回答", "answer": _FakeEngine.answer}],
            "options": []}


_FakeEngine.answer = ""


def _fake_model_env():
    """假配置 + 假客户端（语义层不再真联网）+ 假引擎，返回还原函数。

    ⚠️ app.main 顶部的 get_llm_config 是导入时绑定的，只改 app.config 看不见，
       必须连 app.main 名字一起打（本项目踩过两次的老坑）。
    """
    import app.main as M
    import app.config as _cfg
    import app.llm.client as _client
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
    c.check("下单句「帮我加个游泳周四19:00」→ False",
            looks_like_timetable_import("帮我加个游泳周四19:00") is False)
    c.check("闲聊「今天天气怎么样」→ False",
            looks_like_timetable_import("今天天气怎么样") is False)
    c.check("只说「课表」不提文件/导入 → False",
            looks_like_timetable_import("明天周二有课表吗") is False)
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
        after = client.get("/api/timetable").json()
        c.check("没点确认 → 周表一个字没动",
                before.get("courses") == after.get("courses"))
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

    # 4c 老的待办补条不许被这轮改坏：非导入请求照旧能补出待办确认条
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ("**待办提案** - 事项：健身 - 时间：周二 16:00~17:30\n"
                                  "提案已发出，请点确认条上的【确认】。")
            d = _ask(client, "tt-4", "今晚去健身怎么样")
        finally:
            restore()
        todo = _todo_card(d.get("options"))
        c.check("非导入请求照旧走待办补条（健身卡还在）",
                todo is not None and todo.get("title") == "健身",
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("非导入请求绝不挂课表卡", _tt_card(d.get("options")) is None)
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
    return c


def main():
    results = [
        batch_a_detector(),
        batch_b_parser(),
        batch_c_rescue_card(),
        batch_d_no_fake_todo_card(),
        batch_e_frontend_template(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 课表导入出卡兜底总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
