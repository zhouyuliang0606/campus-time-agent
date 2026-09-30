"""第二十批测试：意图抽取 / 路由的**只读**接口 `POST /classify`。

对应那份《CampusTime-意图抽取路由提示词-定稿.md》。它要的东西就一句话：
**把判定层的中间结果摊开给人看**——"这句话它听成了什么"。

为什么要单独一个接口，而不是让学生端从 `/api/chat` 的返回里猜：
`/api/chat` 的回包是"最终回答 + 选项卡"，判定结果**被埋在流程里**，
一句"帮我加个游泳"到底被听成加待办还是闲聊，只能从它后面干了什么反推。
这个口子把 `is_question / module / intent / confidence / 是哪一档判的` 一次给全。

⚠️ 三样东西**必须合并**（少一个就答不全，也是这一批的重点断言）：
  · `looks_like_question()` → `is_question`（**`classify()` 不返回它**）
  · `Router().route()`      → `module`（内部还有样本库 / 关键词兜底）
  · `intent.classify()`     → `intent` / `confidence` / `source`
真正要防的是"有人图省事在接口里另写一套判定"——两本账一定会漂移。

⚠️ **LLM 必须打桩**（`_harness.patch_llm`）：判定层的语义层吃模型输出，
真模型每次措辞都在飘，不打桩这几批测试**每次结果都不一样**，根本没法归档成报告。
桩是共享的（test_08 原来本地定义了一份，已抽到 `_harness.py`，两边用的是同一份）。

⚠️ 样本库也要**钉住**：`intent_samples.json` 是本机"每办成一句就学一句"攒出来的
（gitignore 掉了），沙箱会把它一起复制进来 —— 不钉住的话，"这句话该走 LLM 档"
的断言会被一条本机学来的样本悄悄顶掉（source 变成 sample），机器一换结果就变。
所以每组开头都 `_set_samples(...)`。

跑法（两种都支持，结果一样）：
    ./.venv/Scripts/python.exe tests/test_intent_classify.py     # 与 run_all.py 一致
    pytest tests/test_intent_classify.py -q                      # 需要另装 pytest

跑完会生成 `intent_test_report.md`（四块固定结构：通过 / 失败 / 失败原因 / 修复思路）。
⚠️ 报告**刻意不写时间戳**：同样的结果重复跑，产出同样的文件 —— 否则每次跑完全量
工作区都多一条无意义的 diff。
"""
import json
import os

from _harness import (Checker, FakeLLM, PROJ_DIR, make_client, patch_llm, sandbox,
                      title)

REPORT_PATH = os.path.join(PROJ_DIR, "intent_test_report.md")

# 每条检查项的结果都记这儿，最后统一出报告（报告要含"期望 vs 实际"和用例自带的修复提示）
RESULTS = []

# ── 假模型回包：语义层该吐什么，就照着给什么 ────────────────────────────────
def _llm(module, intent, title="", conf=0.95, minutes=0, extra=""):
    """拼一个假回包（人话：给打桩的模型一份剧本）。"""
    body = {"module": module, "intent": intent, "title": title,
            "minutes": minutes, "confidence": conf}
    return json.dumps(body, ensure_ascii=False)


_CHAT_FAQ = _llm("faq", "chat")
_CHAT_SCHED = _llm("schedule", "chat")
_ADD = _llm("planner", "add_todo", title="游泳", minutes=90)
_REMOVE = _llm("planner", "remove_todo", title="游泳")
_DONE = _llm("planner", "done_todo", title="游泳")
_RETIME = _llm("planner", "retime_todo", title="健身")
_LIST = _llm("planner", "list_todo")


def _set_samples(items):
    """把样本库钉成指定内容（`[]` = 空库，逼语义层那条路出场）。

    直接写文件而不是打桩 `match_sample`：要测的正是**这个文件真被读到**这件事
    （它是"越用越懂"的落地），打桩就把被测对象换掉了。
    """
    from app.agent import intent as _it
    _it._save_samples(list(items or []))


def _get(d, path):
    """按 "detail.source" 这种点号路径取值（少了哪层就回 None，不抛异常）。"""
    cur = d
    for part in (path or "").split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _record(c, cid, name, ok, expected="", actual="", fix=""):
    """记一条结果：既打给终端，也留给报告。"""
    c.check(f"[{cid}] {name}", ok, "" if ok else f"期望 {expected!r} / 实际 {actual!r}")
    RESULTS.append({"id": cid, "name": name, "ok": bool(ok), "expected": expected,
                    "actual": actual, "fix": fix})


def _run_case(c, client, case):
    """跑一条用例：打完补丁 → 打接口 → 逐项比对期望。"""
    cid = case["id"]
    restore = patch_llm(case["llm"], with_key=case.get("with_key", True)) \
        if case.get("llm") is not None else None
    try:
        _set_samples(case.get("samples", []))
        r = client.post("/classify", json={
            "message": case["msg"],
            "module": case.get("module", "planner"),
        })
        ok_http = r.status_code == case.get("status", 200)
        _record(c, cid, f"HTTP {case.get('status', 200)}", ok_http,
                case.get("status", 200), r.status_code, case.get("fix", ""))
        if not ok_http:
            return
        d = r.json()
        for path, want in (case.get("expect") or {}).items():
            got = _get(d, path)
            _record(c, cid, path, got == want, want, got, case.get("fix", ""))
    finally:
        if restore:
            restore()


# ── 用例表（每条自带 fix_hint：失败时报告直接读它，不是现场编）────────────────
CASES = [
    # ① 纯问句：不管句子里出现了什么词，都**不是**下单
    {"id": "A1", "msg": "图书馆几点开门", "llm": _CHAT_FAQ,
     "expect": {"is_question": True, "module": "faq", "intent": "chat"},
     "fix": "纯问句必须 is_question=true。若为 false，查 looks_like_question 的 "
            "QUESTION_RE 是否覆盖「几点」；若 intent 不是 chat，说明这一档没走语义层"
            "或回包被写死了。"},
    {"id": "A2", "msg": "我要去哪里取快递", "llm": _CHAT_FAQ,
     "expect": {"is_question": True, "module": "faq", "intent": "chat"},
     "fix": "本接口最容易翻车的一条：句子里有「取快递」，但它是**问句**。"
            "期望 module=faq / intent=chat。若判成 add_todo，先查语义层提示词里"
            "「第一步先分问/办」那道闸门，再查 /classify 是不是只调了 Router().route"
            "（关键词表会把话塞给 express）。"},
    {"id": "A3", "msg": "我明天什么时候没课", "llm": _CHAT_SCHED,
     "expect": {"is_question": True, "module": "schedule", "intent": "chat"},
     "fix": "问的是自己的课表 → module=schedule、intent=chat。若落到 faq，"
            "多半是语义层没生效（LLM 没打桩 / 补丁打在了错的对象上）。"},

    # ② 加待办
    {"id": "B1", "msg": "帮我加个游泳周四19:00", "llm": _ADD,
     "expect": {"is_question": False, "module": "planner", "intent": "add_todo"},
     "fix": "intent 必须是 add_todo 且 module=planner。若 module=faq，是语义层没生效、"
            "走到了关键词兜底 —— 检查 /classify 有没有把规则层已判定的意图作为 "
            "intent_hint 传给 Router().route（router 的第 ④ 档）。"},
    {"id": "B2", "msg": "我想去游泳，帮我安排个时间", "llm": _ADD,
     "expect": {"is_question": False, "module": "planner", "intent": "add_todo"},
     "fix": "「安排」是多义词（名词/动词），子串匹配接不住，只能靠语义层。"
            "若 intent=chat，检查打桩是否真的生效（`from X import Y` 是导入时绑定，"
            "补丁要打在模块属性上，intent/router 都是函数内导入所以看得见）。"},

    # ③ 删 / 标记完成 / 改期 / 列清单
    {"id": "C1", "msg": "把游泳删了", "llm": _REMOVE,
     "expect": {"is_question": False, "module": "planner", "intent": "remove_todo"},
     "fix": "删除类统一 module=planner。若 intent 判成 done_todo，说明语义层提示词里"
            "「删除 / 完成」的分界不清（这两个动作在库里是完全不同的后果）。"},
    {"id": "C2", "msg": "把游泳标记完成", "llm": _DONE,
     "expect": {"is_question": False, "module": "planner", "intent": "done_todo"},
     "fix": "必须是 done_todo：**不是删除、也不是新增**（删了找不回来，"
            "标记完成还留着记录）。若判成 remove_todo，去改语义层提示词里 "
            "done_todo 那一行的说明。"},
    {"id": "C3", "msg": "把周四那个健身挪到周五", "llm": _RETIME,
     "expect": {"is_question": False, "module": "planner", "intent": "retime_todo"},
     "fix": "「挪到」= 改期，不是新增。若判成 add_todo，语义层会把它当第二件事加进日程"
            "（同一样东西排两遍）。"},
    {"id": "C4", "msg": "我都有啥待办来着", "llm": _LIST,
     "expect": {"module": "planner", "intent": "list_todo"},
     "fix": "纯读类意图也要能判出来（学生只想看一眼）。若判成 chat，会让大模型"
            "凭空编一份待办清单出来。"},

    # ④ 错别字：只影响实体，不影响"这是问还是办"
    {"id": "D1", "msg": "我要去哪里取凯迪", "llm": _CHAT_FAQ,
     "expect": {"is_question": True, "module": "faq", "intent": "chat"},
     "fix": "带错别字的问句同样不许算下单：错别字只影响实体（凯迪→快递），"
            "不影响「这是问句」这个判断。若 intent=add_todo，说明有人在 /classify 里"
            "另写了一套判定 —— 必须复用 looks_like_question + classify。"},
    {"id": "D2", "msg": "把游永删了", "llm": _REMOVE,
     "expect": {"module": "planner", "intent": "remove_todo"},
     "fix": "「游永」是「游泳」的错别字，意图由语义层判（它只认 intent，不认标题），"
            "所以不该受影响。若这一条挂了，检查白名单是否把回包里的 intent 拒了。"},
]


def batch_cases(c, client):
    """A–D：正面用例（打桩模型 + 空样本库，逼语义层那一档出场）。"""
    title("A–D. 正面用例：问 / 办 / 删 / 完成 / 改期 / 列清单 / 错别字")
    for case in CASES:
        _run_case(c, client, case)


def batch_offline(c, client):
    """E：离线三档（没配 key 时的兜底 —— 演示不依赖密钥就靠它）。"""
    title("E. 离线也能用：没 key 不崩 / 样本库命中 / 规则层意图先行")
    # E1 没 key：不许崩，类型仍要是合法值，且不许假装是语义层给的
    restore = patch_llm(_CHAT_FAQ, with_key=False)
    try:
        _set_samples([])
        r = client.post("/classify", json={"message": "图书馆几点开门"})
        d = r.json()
        _record(c, "E1", "没配 key 也不崩（HTTP 200）", r.status_code == 200, 200,
                r.status_code,
                "离线是**演示的前提**：评委机器上很可能没配密钥。这个口子不许 500。")
        from app.agent.intent import MODULE_KEYS
        _record(c, "E1", "没 key 时 module 仍是合法值", d.get("module") in MODULE_KEYS,
                f"in {MODULE_KEYS}", d.get("module"), "同上")
        _record(c, "E1", "没 key 时不许假装是语义层判的",
                _get(d, "detail.source") != "llm", "!= llm", _get(d, "detail.source"),
                "source 要如实反映是哪一档：没 key 就不可能是 llm。")
    finally:
        restore()

    # E2 样本库：零成本、离线，而且是语义层的缓存（它才是"越用越懂"的落地）
    restore = patch_llm(_CHAT_FAQ, with_key=False)
    try:
        _set_samples([{"text": "把游泳标记完成", "intent": "done_todo", "title": "游泳"}])
        d = client.post("/classify",
                        json={"message": "把游泳标记完成"}).json()
        _record(c, "E2", "样本库命中（source=sample）", _get(d, "detail.source") == "sample",
                "sample", _get(d, "detail.source"),
                "样本库是**零成本、离线可跑**那一档，也是语义层的缓存。"
                "若这里没命中，检查 match_sample 的阈值（0.42）与最短长度（4 字）。")
        _record(c, "E2", "样本库命中时类型按意图推出 planner",
                d.get("module") == "planner", "planner", d.get("module"),
                "match_sample 会带上当时的 module；没记下就按 module_hint(intent) 推。")
        _record(c, "E2", "intent 取自样本库", d.get("intent") == "done_todo",
                "done_todo", d.get("intent"), "同上")
    finally:
        restore()

    # E3 规则层意图先行：没 key、空样本库时，规则层判出的"办事意图"要能定类型
    restore = patch_llm(_CHAT_FAQ, with_key=False)
    try:
        _set_samples([])
        d = client.post("/classify",
                        json={"message": "帮我加个游泳周四19:00"}).json()
        _record(c, "E3", "规则层判出 add_todo（source=rule）",
                _get(d, "detail.source") == "rule", "rule", _get(d, "detail.source"),
                "三档兜底都要能单独站出来：这一档就是规则层预检 `_rule_intent`。")
        _record(c, "E3", "意图已定 → 类型跟着意图走（planner，不是默认 faq）",
                d.get("module") == "planner", "planner", d.get("module"),
                "这是接口自洽的底线：intent=add_todo 却报 module=faq 会让人以为判定坏了。"
                "修法是 Router().route 的「意图先行」档（intent_hint），"
                "位置必须在关键词表**之前**。")
        _record(c, "E3", "intent 取自规则层", d.get("intent") == "add_todo",
                "add_todo", d.get("intent"), "同上")
    finally:
        restore()


def batch_structure(c, client):
    """F：结构、闸门与红线（这个口子**不许写库**）。"""
    title("F. 结构与红线：空消息 / 白名单闸门 / 把握太低 / 一个字节都不写")
    from app.agent.intent import MODULE_KEYS, INTENT_CHAT, KNOWN_INTENTS

    # F1 空 message → 400，而且结构要完整（前端不用判空字段）
    r = client.post("/classify", json={"message": "   "})
    d = r.json()
    _record(c, "F1", "空 message → 400（不猜一个意图出来）", r.status_code == 400, 400,
            r.status_code, "空消息没有意图可言，宁可说清楚，也不许瞎判一个。")
    _record(c, "F1", "400 时五个键仍在（前端不用判空）",
            all(k in d for k in ("is_question", "module", "intent", "confidence", "detail")),
            "五个键齐全", list(d.keys()),
            "回包结构要稳定：前端拿的是同一份字段，400 也不该缺键。")

    # F2 语义层自造意图 → 白名单闸门拦下
    restore = patch_llm(_llm("planner", "delete_everything", conf=0.99))
    try:
        _set_samples([])
        d = client.post("/classify", json={"message": "把库里东西都清了"}).json()
        _record(c, "F2", "模型自造的意图不认（不许它发明一种意图）",
                d.get("intent") not in ("delete_everything",)
                and d.get("intent") in KNOWN_INTENTS or d.get("intent") == "",
                f"in {KNOWN_INTENTS} 或空", d.get("intent"),
                "语义层只许从白名单里挑。它吐出新的意图词一律当没听懂 —— "
                "那是执行权的口子，堵死在 intent.py 的 KNOWN_INTENTS。")
        _record(c, "F2", "自造意图时 module 仍是合法值",
                d.get("module") in MODULE_KEYS, f"in {MODULE_KEYS}", d.get("module"),
                "类型必须永远给出一个合法值，调用方不用再判空。")
    finally:
        restore()

    # F3 把握太低 → 不采用（它自己都没把握，就别替学生做主）
    restore = patch_llm(_llm("planner", "add_todo", title="游泳", conf=0.3))
    try:
        _set_samples([])
        d = client.post("/classify", json={"message": "要不那个啥吧"}).json()
        _record(c, "F3", "confidence<0.6 → 不采用（source 不是 llm）",
                _get(d, "detail.source") != "llm", "!= llm", _get(d, "detail.source"),
                "低把握回包在 classify_with_llm 里就该被丢掉（<0.6 返回 None），"
                "让老路照旧兜。")
        _record(c, "F3", "不采用时 confidence 回 0.0", d.get("confidence") == 0.0,
                0.0, d.get("confidence"), "同上")
    finally:
        restore()

    # F4 历史原样透传
    restore = patch_llm(_CHAT_FAQ)
    try:
        _set_samples([])
        hist = [{"role": "user", "content": "我想去游泳"},
                {"role": "assistant", "content": "好，什么时候？"}]
        d = client.post("/classify", json={"message": "周四吧",
                                           "history": hist}).json()
        _record(c, "F4", "history 条数如实回显（detail.history_used=2）",
                _get(d, "detail.history_used") == 2, 2, _get(d, "detail.history_used"),
                "带上下文是判定层的既有能力（上一句说游泳、这一句说「周四吧」），"
                "接口要把它透传下去，别吞掉。")
        _record(c, "F4", "history 传非列表也不许崩（当成空）",
                client.post("/classify", json={"message": "早上好",
                                               "history": "乱传的"}).status_code == 200,
                200, "见实际", "只用文档里写的类型是理想情况，接口要容错。")
    finally:
        restore()

    # F5 红线：这个口子只读，一个字节都不写库
    from app.store import list_todos
    from app.agent.pending import peek_pending
    before = sorted(t.get("id") for t in list_todos())
    conv_before = client.get("/api/conversation/classify-probe").json()
    restore = patch_llm(_ADD)
    try:
        _set_samples([])
        d = client.post("/classify", json={"message": "帮我加个游泳周四19:00",
                                           "session_id": "classify-probe"}).json()
        _record(c, "F5", "回包自证：wrote_anything=false",
                _get(d, "detail.wrote_anything") is False, False,
                _get(d, "detail.wrote_anything"), "同下")
    finally:
        restore()
    after = sorted(t.get("id") for t in list_todos())
    _record(c, "F5", "调完待办一条没多、一条没少（id 完全一致）", before == after,
            before, after,
            "这是架构红线：`update_todo_status` / `delete_todo` 只有确认后才许被调。"
            "诊断接口更不能写库 —— 它连提案都不该生成。")
    conv_after = client.get("/api/conversation/classify-probe").json()
    _record(c, "F5", "也没往会话历史里塞东西",
            (conv_before.get("messages") or []) == (conv_after.get("messages") or []),
            "无新增", len(conv_after.get("messages") or []),
            "会话历史只在真的聊过之后才该变；诊断调用不算聊过。")


# ── 报告（四块固定结构）──────────────────────────────────────────────────────
def write_report(results, path=REPORT_PATH):
    """把结果写成固定四块的 Markdown（通过 / 失败 / 失败原因 / 修复思路）。

    刻意**不写时间戳**：同样的结果重复跑要在 git 里产出同样的文件。
    """
    passed = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    out = [
        "# 意图抽取 / 路由接口测试报告（`POST /classify`）",
        "",
        "> 本文件由 `tests/test_intent_classify.py` 自动生成 —— 改测试再跑一遍就会刷新，",
        "> 不用手工维护（也不会对不上）。",
        ">",
        f"> 跑法：`python tests/test_intent_classify.py`（或 `pytest tests/test_intent_classify.py`）",
        f"> ｜本次：共 {len(results)} 项检查，通过 {len(passed)}，失败 {len(failed)}。",
        ">",
        "> 判定层是「样本库 → 语义层 → 规则兜底」三档，本报告每条都注明了**是哪一档判的**"
        "（`detail.source`），失败时能直接定位到该改哪一档。",
        "",
        "## 一、通过用例",
        "",
    ]
    if passed:
        out += ["| 用例 | 检查项 | 结果 |", "| --- | --- | --- |"]
        out += [f"| {r['id']} | {r['name']} | ✅ |" for r in passed]
    else:
        out.append("（本次没有通过的检查项）")
    out += ["", "## 二、失败用例", ""]
    if failed:
        out += ["| 用例 | 检查项 | 结果 |", "| --- | --- | --- |"]
        out += [f"| {r['id']} | {r['name']} | ❌ |" for r in failed]
    else:
        out.append("（本次没有失败的检查项 ✅）")

    out += ["", "## 三、失败原因（期望 vs 实际）", ""]
    if failed:
        out += ["| 用例 | 检查项 | 期望 | 实际 |", "| --- | --- | --- | --- |"]
        out += [f"| {r['id']} | {r['name']} | `{r['expected']}` | `{r['actual']}` |"
                for r in failed]
    else:
        out.append("（无失败，故无差异）")

    out += ["", "## 四、修复思路（来自用例自带的 `fix_hint`）", ""]
    if failed:
        seen = set()
        for r in failed:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out += [f"**{r['id']}** — {r['fix']}", ""]
    else:
        out.append("（无失败，无需修复。这一块的内容来自每条用例自带的 `fix_hint` 字段，")
        out.append("**不是事后由模型现场编的**：写用例的时候就把「这条挂了该去改哪儿」")
        out.append("写在旁边，失败报告直接读它。）")

    out += ["", "---", "",
            "## 附：本次覆盖的用例清单", "",
            "| 用例 | 学生原话 | 期望（要点） |", "| --- | --- | --- |"]
    for case in CASES:
        want = "、".join(f"{k}={v}" for k, v in (case.get("expect") or {}).items())
        out.append(f"| {case['id']} | {case['msg']} | {want} |")
    out += ["", "其余为 E 组（离线三档）与 F 组（结构 / 白名单 / 不写库红线），"
                "断言写在 `batch_offline` / `batch_structure` 里。", ""]

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    os.replace(tmp, path)     # 原子替换：读的人不会看到写了一半的报告
    return path


def _run_all():
    """跑完所有批次，返回 (Checker, 报告路径)。"""
    RESULTS.clear()
    c = Checker()
    with sandbox():
        client = make_client()
        batch_cases(c, client)
        batch_offline(c, client)
        batch_structure(c, client)
    path = write_report(RESULTS)
    print(f"\n[测试报告已写入 {os.path.relpath(path, PROJ_DIR)}]")
    return c, path


# ── pytest 入口（装了就自动收集；没装也不影响 run_all.py）────────────────────
def test_intent_classify():
    """pytest 收集入口：一个脚本能跑、pytest 也能跑。"""
    c, _ = _run_all()
    assert c.failed == 0, f"意图抽取接口有 {c.failed} 项失败，详见 {REPORT_PATH}"


def main():
    c, _ = _run_all()
    print(f"\n===== 第二十批（意图抽取/路由接口 /classify）结果：通过 {c.passed} 项，"
          f"失败 {c.failed} 项 =====")
    return 1 if c.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
