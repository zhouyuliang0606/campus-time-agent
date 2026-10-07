"""快递/外卖丢失破损 → 出「确认上报」卡（上报工单不再被判成问事、不再没卡）。

报障原话（2026-10-07 学生）：「我说快递丢了，被判定为问事，后续都是问事，
没有出卡，也没有上传」。

根因：express 模块的「上报工单」只活在系统提示里（让模型先问"是否上报管理端"），
但没有确定性出卡环节——语义层把"快递丢了"判成 chat / 问事，模型于是回一长段查件
话术、从没出卡，学生要上报却找不到入口，更不会真去写工单。

修法（对应三处，与课表导入出卡同一套硬闸门模式）：
  · planner.py 新增 looks_like_work_order_report（认出"丢失破损 / 上报 / 工单"请求）；
  · main.py 加确定性分支：命中即直接出 work_order_report 卡（可取消的提案），
    学生点【确认上报】才由系统写工单（走 /api/workorders，守住"写库只归系统"红线）；
  · confirm.html 加 work_order_report 卡（可选补充描述），student.html 的 renderOptions 把它接到确认条。

⚠️ 本文件给假引擎：上报分支在模型之前就出卡，假引擎沉默才能钉死"第一句话就出卡"。
"""
from _harness import Checker, make_client, sandbox, title
from app.modules.planner import looks_like_work_order_report


class _FakeEngine:
    """恒定返回"只有文字、没有 options"的结果（钉死"模型没掺和"这条确定性路径）。"""

    def __init__(self, *a, **kw):
        pass

    async def run(self, message, history=None):
        return {"answer": _FakeEngine.answer, "trace": [
            {"step": 1, "phase": "💡 最终回答", "answer": _FakeEngine.answer}],
            "options": list(_FakeEngine.options or [])}


_FakeEngine.answer = ""
_FakeEngine.options = []


def _fake_model_env():
    """假配置 + 假客户端 + 假引擎，返回还原函数（同 test_23 的打桩姿势）。"""
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
        "message": message, "module": "express", "session_id": sid}).json()


def _wo_card(opts):
    return next((o for o in (opts or []) if isinstance(o, dict)
                 and o.get("kind") == "work_order_report"), None)


def batch_a_detector():
    c = Checker()
    title("1. 探测器：认出「丢失破损 / 上报 / 工单」，别把普通查件拖进来")
    c.check("「快递丢了」→ 疑似丢件", looks_like_work_order_report("快递丢了") == "疑似丢件")
    c.check("「我的快递丢失了」→ 疑似丢件", looks_like_work_order_report("我的快递丢失了") == "疑似丢件")
    c.check("「外卖遗失」→ 外卖丢失", looks_like_work_order_report("外卖遗失") == "外卖丢失")
    c.check("「包裹破损」→ 包裹破损", looks_like_work_order_report("包裹破损") == "包裹破损")
    c.check("「取件码异常」→ 取件码异常", looks_like_work_order_report("取件码异常") == "取件码异常")
    c.check("「上报管理员」→ 非 None", looks_like_work_order_report("上报管理员") is not None)
    c.check("「帮我上报」→ 非 None", looks_like_work_order_report("帮我上报") is not None)
    c.check("「提交工单」→ 非 None", looks_like_work_order_report("提交工单") is not None)
    # 反向：普通查件不算上报
    c.check("「我的快递到哪了」→ None", looks_like_work_order_report("我的快递到哪了") is None)
    c.check("「取件码多少」→ None", looks_like_work_order_report("取件码多少") is None)
    c.check("「今天天气怎么样」→ None", looks_like_work_order_report("今天天气怎么样") is None)
    return c


def batch_b_first_message_card():
    c = Checker()
    title("2. 主链路：说要上报 → 第一句话就出「确认上报」卡（不等模型）")

    # 2a 「快递丢了」
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ""      # 模型沉默 → 卡只能是系统出的
            d = _ask(client, "wo-1", "快递丢了")
        finally:
            restore()
        card = _wo_card(d.get("options"))
        c.check("「快递丢了」挂出 work_order_report 卡", card is not None,
                str([o.get("kind") for o in (d.get("options") or [])]))
        c.check("卡上 kind = 疑似丢件", card and card.get("wo_kind") == "疑似丢件",
                str(card.get("wo_kind") if card else None))
        c.check("回话指向确认条（提到确认上报）",
                "确认上报" in (d.get("answer") or ""), (d.get("answer") or "")[:60])
        tr = " / ".join((s.get("phase") or "") for s in (d.get("trace") or []))
        c.check("轨迹写「办事」不再写「问事」", "办事" in tr and "问事" not in tr, tr[:120])
        c.check("没点确认 → 工单列表没多（零污染）",
                len(client.get("/api/workorders").json().get("workorders") or []) == 0)

    # 2b 「外卖遗失」→ kind 外卖丢失
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ""
            d = _ask(client, "wo-2", "外卖遗失")
        finally:
            restore()
        card = _wo_card(d.get("options"))
        c.check("「外卖遗失」卡上 kind = 外卖丢失",
                card and card.get("wo_kind") == "外卖丢失",
                str(card.get("wo_kind") if card else None))

    # 2c 「上报管理员」也出卡
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ""
            d = _ask(client, "wo-3", "上报管理员")
        finally:
            restore()
        c.check("「上报管理员」挂出 work_order_report 卡",
                _wo_card(d.get("options")) is not None,
                str([o.get("kind") for o in (d.get("options") or [])]))

    # 2d 反向：普通查件不出上报卡（交给 express 模块正常答）
    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = "你有 4 条快递记录……"
            d = _ask(client, "wo-4", "我的快递到哪了")
        finally:
            restore()
        c.check("普通查件「我的快递到哪了」不出上报卡",
                _wo_card(d.get("options")) is None,
                str([o.get("kind") for o in (d.get("options") or [])]))
    return c


def batch_c_click_flow():
    c = Checker()
    title("3. 点卡上报：系统写工单 + 清掉暂存卡（写库只归系统）")

    with sandbox():
        client = make_client()
        restore = _fake_model_env()
        try:
            _FakeEngine.answer = ""
            d = _ask(client, "wo-5", "快递丢了")
        finally:
            restore()
        card = _wo_card(d.get("options"))
        c.check("先有卡", card is not None)

        # 模拟点【确认上报】：带 session_id 写工单
        r = client.post("/api/workorders", json={
            "kind": card["wo_kind"], "desc": "取件码B-5678找不到了",
            "source": "chat", "session_id": "wo-5"})
        j = r.json()
        c.check("点卡 → 工单创建成功", j.get("ok") is True, str(j))
        c.check("工单 source = chat（来自聊天确认条）",
                (j.get("workorder") or {}).get("source") == "chat")
        c.check("工单带补充描述", (j.get("workorder") or {}).get("desc") == "取件码B-5678找不到了")

        # 暂存卡应被清掉（不会再挂在 /api/pending 反复出现）
        pend = client.get("/api/pending/wo-5").json().get("pending")
        c.check("点卡后暂存清空（pending 为 null）", pend is None, str(pend))
    return c


def batch_d_frontend():
    c = Checker()
    title("4. 前端：确认条能渲染 work_order_report 卡，且点【确认上报】写工单")
    conf = open("app/static/confirm.html", encoding="utf-8").read()
    seg = conf[conf.find("work_order_report: {"): conf.find("work_order_report: {") + 1400]
    c.check("confirm.html 有 work_order_report 卡定义", "work_order_report:" in conf)
    c.check("卡上按钮叫【确认上报】", "确认上报" in seg)
    c.check("apply 走 /api/workorders（系统写库，不是模型）",
            "/api/workorders" in seg and "source: \"chat\"" in seg)
    c.check("卡上带可选补充描述输入框", "cfWoDesc" in seg)

    stu = open("app/static/student.html", encoding="utf-8").read()
    seg2 = stu[stu.find('kind === "work_order_report"'):]
    c.check("student.html renderOptions 接住 work_order_report → 走确认条",
            'kind === "work_order_report"' in stu and "openConfirm(" in seg2)
    return c


def main():
    results = [
        batch_a_detector(),
        batch_b_first_message_card(),
        batch_c_click_flow(),
        batch_d_frontend(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 快递/外卖上报工单出卡总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
