"""增删查改 对话流程自测（人话：把"加 / 删 / 查 / 改"四条学生待办链路各跑一遍，
规则认得的、规则接不住（语义层兜底）的两种说法都测，逐条断言真写库 / 真删除 / 真改期）。

不启 uvicorn，用 fastapi 的 TestClient 直接打真实 app；语义层用打桩的假模型，
所以结果确定、不烧真 key、不联网。数据走 sandbox 副本，不碰演示数据。
"""
import os
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from _harness import Checker, sandbox, make_client, title


class _FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    async def chat(self, messages, tools=None, tool_choice="auto"):
        return {"content": self._reply}


class _LLMPatch:
    """上下文管理器：把语义层的大模型换成打桩的，离开时还原。

    intent.py 在 classify_with_llm 里用 `from app.config import ...` /
    `from app.llm.client import ...` **按调用时**读这两个模块的属性，
    所以只改这两个源模块的属性就能让语义层在"无真 key"的沙箱里走打桩路径。
    """

    def __init__(self, reply, with_key=True):
        self.reply = reply
        self.with_key = with_key

    def __enter__(self):
        import app.config as _cfg
        import app.llm.client as _client
        self._old_c = _client.DeepSeekClient
        self._old_g = _cfg.get_llm_config
        _client.DeepSeekClient = lambda *a, **k: _FakeLLM(self.reply)
        key = "test-key" if self.with_key else ""
        _cfg.get_llm_config = lambda: {"api_key": key, "base_url": "x", "model": "m"}
        return self

    def __exit__(self, *exc):
        import app.config as _cfg
        import app.llm.client as _client
        _client.DeepSeekClient = self._old_c
        _cfg.get_llm_config = self._old_g
        return False


def _run():
    c = Checker()
    client = make_client()
    # 自测从干净日程起步：演示数据里偶尔会被手测/真机跑过的会话污染
    # （比如 live 真机验证往真实 todos.json 写过一条 瑜伽），清掉才不会因为
    # "同名好几条"把断言从「唯一命中」误判成「many」。
    from app.store import _swrite
    _swrite("todos.json", {"todos": []})

    def chat(sid, msg, module="planner"):
        r = client.post("/api/chat", json={"message": msg, "session_id": sid, "module": module})
        return r.status_code, (r.json() if r.status_code == 200 else {})

    def write_state():
        from app.store import list_todos
        return list_todos()

    def find(todos, title, date=None):
        for t in todos:
            if t.get("title") == title and (date is None or t.get("date") == date):
                return t
        return None

    # ───────────────────────── 增 ADD ─────────────────────────
    title("增（ADD）：规则可识别 + 规则接不住")
    # ADD-R1 规则："加个健身，周四 19:00-20:30"
    sid = "add-rule"
    code, resp = chat(sid, "加个健身，周四 19:00-20:30")
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("ADD-R1 出卡(awaiting_choice)", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    c.check("ADD-R1 卡片含待办(todo_add/todo_slots)", kinds & {"todo_add", "todo_slots"}, str(kinds))
    c.check("ADD-R1 标题=健身", any("健身" in (o.get("title") or "") for o in opts))
    # 确认 → 真写库
    code, resp = chat(sid, "确认")
    c.check("ADD-R1 确认回执含『已加入日程』", "已加入日程" in (resp.get("answer") or ""), (resp.get("answer") or "")[:20])
    todos = write_state()
    c.check("ADD-R1 真写进日程(健身/周四)", find(todos, "健身") is not None, f"当前{todos}")

    # ADD-S1 语义（规则接不住）："过两天想去剪个头发" → 模型回 add_todo + 时间
    sid = "add-sem"
    with _LLMPatch('{"intent":"add_todo","title":"剪头发","minutes":0,"day_hint":"周五","start":"15:00","end":"16:00","confidence":0.92}'):
        code, resp = chat(sid, "过两天想去剪个头发")
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("ADD-S1 语义层出卡", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    c.check("ADD-S1 卡片含待办", kinds & {"todo_add", "todo_slots"}, str(kinds))
    c.check("ADD-S1 标题=剪头发", any("头发" in (o.get("title") or "") for o in opts))
    code, resp = chat(sid, "确认")
    c.check("ADD-S1 确认回执含『已加入日程』", "已加入日程" in (resp.get("answer") or ""))
    todos = write_state()
    c.check("ADD-S1 真写进日程(剪头发)", find(todos, "剪头发") is not None)

    # ───────────────────────── 查 LIST ─────────────────────────
    title("查（LIST）：规则可识别 + 规则接不住")
    # 先用一个带数据的会话
    sid = "list-mix"
    chat(sid, "加个跑步，周三 18:00-19:00")
    chat(sid, "确认")
    # LS-R1 规则："我的待办呢"
    code, resp = chat(sid, "我的待办呢")
    c.check("LS-R1 是纯读(无卡片)", not resp.get("awaiting_choice"), str(resp.get("awaiting_choice")))
    c.check("LS-R1 列出待办", "待办" in (resp.get("answer") or "") and "跑步" in (resp.get("answer") or ""))
    # LS-S1 语义（之前踩坑那句）："我现在都有啥安排"
    with _LLMPatch('{"intent":"list_todo","title":"","confidence":0.95}'):
        code, resp = chat(sid, "我现在都有啥安排")
    c.check("LS-S1 语义层走查(无卡片)", not resp.get("awaiting_choice"))
    c.check("LS-S1 列出待办(含跑步)", "跑步" in (resp.get("answer") or ""), (resp.get("answer") or "")[:30])

    # ───────────────────────── 删 REMOVE ─────────────────────────
    title("删（REMOVE）：点名删 + 没点名(盲删) + 语义层")
    # RM-R1 点名删：前面 add-rule 会话里有「健身/周四」
    sid = "add-rule"
    code, resp = chat(sid, "删掉健身")
    opts = resp.get("options") or []
    c.check("RM-R1 出删除确认卡", any(o.get("kind") == "todo_remove" for o in opts), str({o.get('kind') for o in opts}))
    code, resp = chat(sid, "确认")
    c.check("RM-R1 确认回执含『已删除』", "已删除" in (resp.get("answer") or ""), (resp.get("answer") or "")[:20])
    todos = write_state()
    c.check("RM-R1 真从日程移除(健身)", find(todos, "健身") is None)
    # RM-S1 盲删（没点名，指刚写的那条）：唱krv
    sid = "rm-blind"
    chat(sid, "加个唱krv，周日 20:00-21:00")
    chat(sid, "确认")
    code, resp = chat(sid, "不去了删了吧")  # 规则能接（含"删了"），从写库回执找回名字
    opts = resp.get("options") or []
    c.check("RM-S1 盲删也能出确认卡", any(o.get("kind") == "todo_remove" for o in opts), str({o.get('kind') for o in opts}))
    code, resp = chat(sid, "确认")
    todos = write_state()
    c.check("RM-S1 盲删真移除(唱krv)", find(todos, "唱krv") is None)
    # RM-S2 语义层删（规则接不住）："我决定不去瑜伽了"
    sid = "rm-sem"
    chat(sid, "加个瑜伽，周二 19:00-20:00")
    chat(sid, "确认")
    with _LLMPatch('{"intent":"remove_todo","title":"瑜伽","confidence":0.9}'):
        code, resp = chat(sid, "我决定不去瑜伽了")
    opts = resp.get("options") or []
    c.check("RM-S2 语义层出删除卡", any(o.get("kind") == "todo_remove" for o in opts))
    code, resp = chat(sid, "确认")
    todos = write_state()
    c.check("RM-S2 语义层删真移除(瑜伽)", find(todos, "瑜伽") is None)

    # ───────────────────────── 改 RETIME ─────────────────────────
    title("改（RETIME）：待确认卡换时间 + 已排待办改期")
    # RT-R1 规则（待确认卡）：加了健身卡未确认 → "改成周六"
    sid = "rt-pending"
    chat(sid, "加个健身，周四 19:00-20:30")  # 出卡，不确认
    code, resp = chat(sid, "改成周六")        # 规则 wants_retime_todo
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("RT-R1 待确认卡换时间出卡", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    c.check("RT-R1 仍是待办卡", kinds & {"todo_add", "todo_slots"}, str(kinds))
    # 新日期应是周六
    import datetime as _dt
    sat = (lambda d: d)(None)
    # 取最近的周六日期字符串，验证提案 date 的星期
    def _is_sat(date_str):
        try:
            y, m, d = map(int, date_str.split("-"))
            return _dt.date(y, m, d).weekday() == 5
        except Exception:
            return False
    c.check("RT-R1 新日期是周六", any(_is_sat(o.get("date") or "") for o in opts if o.get("kind") in ("todo_add", "todo_slots")))

    # RT-S2 语义层（已排待办改期）：先把瑜伽排到周四，再说"把瑜伽改到周五"。
    # 用"把瑜伽改到周五"而不是"把周四那个瑜伽挪到周五"——后者会和样本库里的
    # 种子句"把周四那个健身挪到周五"模糊撞上（Dice≈0.44），把缓存的"健身"盖掉瑜伽。
    sid = "rt-existing"
    chat(sid, "加个瑜伽，周四 19:00-20:00")
    chat(sid, "确认")  # 真写进：瑜伽/周四
    with _LLMPatch('{"intent":"retime_todo","title":"瑜伽","day_hint":"周五","confidence":0.93}'):
        code, resp = chat(sid, "把瑜伽改到周五")
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("RT-S2 语义层出改期卡", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    c.check("RT-S2 仍是待办卡", kinds & {"todo_add", "todo_slots"}, str(kinds))
    # 期望：提案日期是周五，且带 old_todo_id（确认后旧的那条周四应消失）
    friday_opts = [o for o in opts if o.get("kind") in ("todo_add", "todo_slots") and _is_fri(o.get("date") or "")]
    c.check("RT-S2 新日期是周五", bool(friday_opts), str([o.get("date") for o in opts]))
    has_old = any(o.get("old_todo_id") for o in friday_opts)
    c.check("RT-S2 提案带 old_todo_id(可回删旧条)", has_old)
    if has_old:
        code, resp = chat(sid, "确认")
        todos = write_state()
        old_gone = find(todos, "瑜伽", date=_thu()) is None
        new_there = find(todos, "瑜伽", date=_fri()) is not None
        c.check("RT-S2 确认后旧(周四)移除", old_gone)
        c.check("RT-S2 确认后新(周五)落库", new_there)

    # RT-S3 纯语义（规则接不住的改期说法）："瑜伽换周五"——话里没有"改成/挪到"等词，
    # 规则认不到，只能靠语义层兜回 retime_todo，照样要能出改期卡并真改期。
    sid = "rt-sem"
    chat(sid, "加个吉他练习，周三 18:00-19:00")
    chat(sid, "确认")
    with _LLMPatch('{"intent":"retime_todo","title":"吉他练习","day_hint":"周日","confidence":0.92}'):
        code, resp = chat(sid, "吉他练习换周日")
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("RT-S3 纯语义出改期卡", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    sun_opts = [o for o in opts if o.get("kind") in ("todo_add", "todo_slots") and _is_sun(o.get("date") or "")]
    c.check("RT-S3 新日期是周日", bool(sun_opts), str([o.get("date") for o in opts]))
    has_old3 = any(o.get("old_todo_id") for o in sun_opts)
    c.check("RT-S3 提案带 old_todo_id", has_old3)
    if has_old3:
        code, resp = chat(sid, "确认")
        todos = write_state()
        c.check("RT-S3 确认后旧(周三)移除", find(todos, "吉他练习", date=_wed()) is None)
        c.check("RT-S3 确认后新(周日)落库", find(todos, "吉他练习", date=_sun()) is not None)

    # RT-S4 回归：live 真实踩坑那一句「把周四那个瑜伽挪到周五」。
    # 这条话会被样本库种子「把周四那个健身挪到周五」撞上（Dice≈0.7），
    # 把缓存的"健身"盖掉真名字"瑜伽"；改期提案按"健身"找库会落空 → 旧代码回「没找到」。
    # 修复后：语义/样本给的名字没命中，就退回从学生原句里抠名字（瑜伽）再试。
    sid = "rt-collide"
    from app.store import _swrite
    _swrite("todos.json", {"todos": []})   # 清掉演示数据自带的 瑜伽，免得 many
    chat(sid, "加个瑜伽，周四 19:00-20:00")
    chat(sid, "确认")  # 真写进：瑜伽/周四
    # LLM 不帮（回 chat），逼出"样本碰撞 → 退回原句抠名"这条真实路径
    with _LLMPatch('{"intent":"chat","confidence":0.9}'):
        code, resp = chat(sid, "把周四那个瑜伽挪到周五")
    opts = resp.get("options") or []
    kinds = {o.get("kind") for o in opts}
    c.check("RT-S4 碰撞句也出改期卡", resp.get("awaiting_choice") is True, str(resp.get("awaiting_choice")))
    c.check("RT-S4 仍是待办卡", kinds & {"todo_add", "todo_slots"}, str(kinds))
    fri_opts = [o for o in opts if o.get("kind") in ("todo_add", "todo_slots") and _is_fri(o.get("date") or "")]
    c.check("RT-S4 新日期是周五(非误判健身)", bool(fri_opts), str([o.get("date") for o in opts]))
    has_old4 = any(o.get("old_todo_id") for o in fri_opts)
    c.check("RT-S4 提案带 old_todo_id", has_old4)
    if has_old4:
        code, resp = chat(sid, "确认")
        todos = write_state()
        old_gone4 = find(todos, "瑜伽", date=_thu()) is None
        new_there4 = find(todos, "瑜伽", date=_fri()) is not None
        c.check("RT-S4 确认后旧(周四)移除", old_gone4)
        c.check("RT-S4 确认后新(周五)落库", new_there4)

    rc = c.summary("增删查改自测")
    return rc


def _is_fri(date_str):
    try:
        y, m, d = map(int, date_str.split("-"))
        return __import__("datetime").date(y, m, d).weekday() == 4
    except Exception:
        return False


def _is_sun(date_str):
    try:
        y, m, d = map(int, date_str.split("-"))
        return __import__("datetime").date(y, m, d).weekday() == 6
    except Exception:
        return False


def _thu():
    return _next_weekday(3)


def _fri():
    return _next_weekday(4)


def _wed():
    return _next_weekday(2)


def _sun():
    return _next_weekday(6)


def _next_weekday(target):
    import datetime as _dt
    today = _dt.date.today()
    off = (target - today.weekday()) % 7 or 7
    return (today + _dt.timedelta(days=off)).isoformat()


if __name__ == "__main__":
    with sandbox():
        sys.exit(_run())
