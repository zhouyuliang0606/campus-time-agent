"""第十六批测试：**错别字** 与 **思考过程**（学生 2026-09-30 报障的那两条）。

学生报障原话（附了一张截图）：
    ① 「没有展示思考过程」
    ② 「没有修改错别字」

截图里那一幕：他打的是「**中文**12点的车」，卡片标题却是「**中文**的车」——
系统把一句谁也没说过的话**自信地**写进了日程。查下来是三处叠加：

  a) **样本库模糊命中会把别句的字段搬过来**（最严重的一个，顺手一起修了）：
     「我想去健身，帮我安排个时间」撞上「我想去游泳，帮我安排个时间」(0.73)
     → 事名被照抄成「游泳」。学生说健身，系统回他游泳。
     根因：模糊匹配（阈值 0.42）只配拿来定**路线**（module/intent），
     字段（事名/时长/钟点）必须让模型现读。
  b) **模型的钟点被丢掉**：`to_canonical` 原来只在 start 和 end **都有**时才拼，
     学生十有八九只说一个起点 → 那段钟点被静默丢掉，退回归则层重新抠。
  c) **规则层不认识错别字**：「中午」是时间词、擦得掉；「中文」不是，就整串留下了。

外加一条本来没被发现的：
  d) 前端**从来没有渲染过 `trace`** —— 后端一直在算、也一直在返回，没人显示。
     （`.trace` 的样式是遗留的，`addMsg` 里那句注释写着"学生端不展示思考轨迹"。）

分六批：
  A 错别字纠正（规则层，离线就能纠）
  B 样本库不再张冠李戴（半命中只借路线、全命中才复用字段）
  C 钟点不再丢失（to_canonical）
  D 思考过程真的回了、且写了"听成什么"
  E 红线：这一批的改动**一个字节都不许写库**
  F 报了钟点、只是没说哪天（就着他报的那个点出条，不再摊一屏候选把他刚说的点埋掉）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (Checker, data_path, make_client, patch_llm, sandbox,  # noqa: E402
                      title)

_FIX = "错别字没纠 → 看 planner._fix_typo_time_words（它挂在 _normalize_clock 里）"


def _c(c, name, ok, actual="", fix=""):
    """打一分（人话：过了记一笔；没过就把"实际是什么 + 该去改哪儿"一起打出来）。

    为什么要带 `fix`：失败报告最怕只有"期望 vs 实际"——看报告的人还得自己猜该动哪里。
    每条用例的 fix 是**写用例时**就写好的，不是事后由谁现编的
    （跟 `test_intent_classify` 里的 `fix_hint` 同一个做法）。
    """
    if ok:
        c.check(name, True, str(actual))
    else:
        c.check(f"{name} ｜实际={actual!r} ｜该去改哪儿：{fix}", False)
    return bool(ok)


def _todo_ids():
    """当前待办的 id 列表（人话：红线要盯的就是它一个字都不许变）。"""
    p = data_path("student", "todos.json")
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    items = data if isinstance(data, list) else (data.get("todos") or [])
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("id") is not None:
            out.append(str(it["id"]))
    return sorted(out)


def _set_samples(items):
    """把样本库钉成指定内容（直接写文件，测的就是"它真被读到"这件事）。"""
    from app.agent import intent as _it
    _it._save_samples(list(items or []))


def _llm_json(intent="add_todo", title="", module="planner", conf=0.9,
              start="", day_hint=""):
    return json.dumps({"module": module, "intent": intent, "title": title,
                       "minutes": 0, "day_hint": day_hint, "date": "", "start": start,
                       "end": "", "confidence": conf}, ensure_ascii=False)


# ── A. 错别字纠正（纯规则，离线）────────────────────────────────────────────
def batch_typo(c):
    title("A. 错别字：形近笔误要被认成那个时间词（离线也纠）")
    from app.modules.planner import (_fix_typo_time_words as fix, _normalize_clock,
                                     _pick_title)

    # A1 报障那一幕本身。
    # ⚠️ 必须走 **`_normalize_clock`**（主链路真正会经过的那个口），不能直接调 `fix()` ——
    #    直接调只证明"这个函数写得对"，证明不了"它真的被接上了"。
    #    反向验证（把接线拆掉）时就是这么抓到 A1/A4 两条其实咬不住信号的。
    got = _normalize_clock("中文12点的车")
    _c(c, "A1 「中文12点的车」经主链路纠成「中午12点的车」", "中午12点的车" in got, got, _FIX)

    # A2 ★ 不变量：纠错后必须跟"本来就写对了"走同一条路。
    #    这一条比 A1 更重要——只把字改对、行为却不一样，那是另一种错。
    _c(c, "A2 纠错后与写对时**行为完全一致**（都抠不出事名 → 反问）",
       _pick_title("中文12点的车") == _pick_title("中午12点的车"),
       f"{_pick_title('中文12点的车')!r} vs {_pick_title('中午12点的车')!r}",
       "纠错只能改「写法」，不能改后续处理路径。两句话在系统眼里必须是同一句话。")

    # A3 别的常见笔误
    _c(c, "A3 「上物十点开会」→ 上午十点开会，事名抠得出「开会」",
       fix("上物十点开会") == "上午十点开会" and _pick_title("上物十点开会") == "开会",
       f"{fix('上物十点开会')!r} / {_pick_title('上物十点开会')!r}", _FIX)
    _c(c, "A4 「下物三点的会」与写对的「下午三点的会」等价（也走主链路）",
       _pick_title("下物三点的会") == _pick_title("下午三点的会")
       and "下午" in _normalize_clock("下物三点的会"),
       f"{_normalize_clock('下物三点的会')!r} / {_pick_title('下物三点的会')!r}", _FIX)

    # A5 ★ 误伤闸门：这三条都是"同首字 + 只差一字"的正经词，一个字都不许动。
    #    实测「上班」和「上午」只差一个字——不拦的话「加个上班打卡」会变成「打卡」，
    #    悄悄少一个词，比报错还难发现。
    for bad, why in (("上班三点打卡", "「上班」不是「上午」的笔误"),
                     ("天才计划", "「天才」不是「今天」的笔误（首字都不同）"),
                     ("加个上课的提醒", "「上课」不是「上午」的笔误")):
        _c(c, f"A5 不误伤：{why}", fix(bad) == bad, fix(bad),
                "误伤=从学生的事名里**偷偷删掉一个词**。判据要收紧："
                "同长度 + 首字相同 + 只差一字 + **必须处在时间语境**（后面紧跟钟点/星期）。")

    # A6 ★ 时间词之间也互为"只差一字"（今天/今晚/今夜）→ 不许连环改。
    #    实测没拦之前，「今田晚上有课」被连环改成「今夜里上午课」，整句话烂掉。
    _c(c, "A6 不连环改：「今天12点开会」保持原样", fix("今天12点开会") == "今天12点开会",
            fix("今天12点开会"), "先拦「本来就是时间词」再谈纠正，否则 今天→今晚→今夜 一路改下去。")
    _c(c, "A7 周一~周天不许被纠成周末",
            fix("周一三点开会") == "周一三点开会", fix("周一三点开会"), _FIX)

    # A8 无钟点语境时不猜（宁可漏纠）
    _c(c, "A8 没有钟点语境就不猜（「明夭交电费」保持原样）",
            fix("明夭交电费") == "明夭交电费", fix("明夭交电费"),
            "取向是宁可漏纠：拿不准时留给语义层（有 key 时它会纠，实测「会义」→「开会」）。")
    return c.summary("A 批（错别字）")


# ── B. 样本库不再张冠李戴 ───────────────────────────────────────────────────
def batch_sample(c, client):
    title("B. 样本库：模糊命中只借「路线」，字段必须现读")
    import app.agent.intent as it

    swim = {"text": "我想去游泳，帮我安排个时间", "intent": "add_todo",
            "title": "游泳", "module": "planner"}
    try:
        _set_samples([swim])
        # B1 半命中：字段必须是空的（不复用别句的事名）
        hit = it.match_sample("我想去健身，帮我安排个时间")
        _c(c, "B1 半命中认得出来（路线有了）", bool(hit) and hit.get("intent") == "add_todo",
                json.dumps(hit, ensure_ascii=False)[:70] if hit else "None", "")
        _c(c, "B1b 但**事名不许照抄**（不能把「游泳」搬给「健身」）",
                bool(hit) and not (hit.get("title") or ""),
                (hit or {}).get("title"), "学生说健身、系统回游泳 —— 比「没听懂」严重得多。")
        _c(c, "B1c 半命中标了 route_only（排查时看得出只借了路线）",
                bool(hit) and hit.get("cache") == "route_only",
                (hit or {}).get("cache"), "")

        # B2 有 key 时继续问模型 → 事名由模型现读
        restore = patch_llm(_llm_json(title="健身"), with_key=True)
        try:
            r = client.post("/classify", json={"message": "我想去健身，帮我安排个时间"})
            d = r.json()
            got = ((d.get("detail") or {}).get("semantic") or {}).get("title")
            _c(c, "B2 半命中后仍问模型 → 事名是「健身」（不是缓存里的「游泳」）",
                    got == "健身", got, "classify() 对**要事名**的意图必须继续问模型。")
        finally:
            restore()

        # B3 全命中（逐字相同）→ 字段可以复用（这才是缓存的本意）
        _set_samples([swim])
        hit2 = it.match_sample("我想去游泳，帮我安排个时间")
        _c(c, "B3 逐字相同 = 全命中，事名可以复用（省一次 API）",
                bool(hit2) and hit2.get("title") == "游泳" and hit2.get("cache") == "full",
                f"{(hit2 or {}).get('title')!r}/{ (hit2 or {}).get('cache')!r}",
                "样本库是语义层的缓存：**同一句话**才谈复现，否则只能借路线。")

        # B4 日期永不进缓存（今天的"周四"到下周就是别的日子）
        it.remember("帮我把自行车链条上点油" + str(time.time())[-5:], "add_todo",
                    {"title": "上链条油", "date": "2026-10-01", "start": "12:00"})
        last = it.load_samples()[-1]
        _c(c, "B4 缓存记钟点、但**不记日期**（日期会过期）",
                last.get("start") == "12:00" and not last.get("date"),
                f"start={last.get('start')!r} date={last.get('date')!r}",
                "记了日期，下次读出来就是个错日子。day_hint（「周四」）是相对说法，可以记。")
    finally:
        _set_samples([])
    return c.summary("B 批（样本库）")


# ── C. 钟点不再被丢掉 ───────────────────────────────────────────────────────
def batch_canonical(c):
    title("C. 规范话：只说了一个起点，钟点也不许丢")
    from app.agent.intent import to_canonical
    from app.modules.planner import parse_add_todo

    sem = {"title": "坐车", "start": "12:00", "end": "", "minutes": 0,
           "day_hint": "今天", "date": ""}
    blob = to_canonical("中文12点的车", sem)
    _c(c, "C1 只有 start（没有 end）时也拼进规范话", "12:00" in blob, repr(blob),
            "原来写的是 `if start and end` —— 只给起点的话整段钟点**静默消失**，"
            "模型明明读懂了 12 点，最后还是靠规则层去抠。")
    p = parse_add_todo(blob)
    _c(c, "C2 规范话能解析出 12:00（不再摊一堆候选让学生重挑）",
            bool(p) and p.get("start") == "12:00", json.dumps(p, ensure_ascii=False)[:80] if p else None,
            "学生自己报了准点就该照那个点出条 —— 再摊三个等于让他重挑一遍。")
    return c.summary("C 批（规范话）")


# ── D. 思考过程真的回来了 ───────────────────────────────────────────────────
def batch_trace(c, client):
    title("D. 思考过程：回包里要有，而且写清「把这话听成了什么」")
    _set_samples([])

    # ── 走语义层那句（规则层认不出「中文12点的车」，所以会去问模型）──
    restore = patch_llm(_llm_json(title="坐车"), with_key=True)
    try:
        d = client.post("/api/chat", json={"sid": "trace-b1",
                                           "message": "中文12点的车"}).json()
        tr = d.get("trace") or []
        first = (tr[0].get("phase") or "") if tr else ""
        _c(c, "D1 回包 trace 第一条就是「我把这句听成…」", first.startswith("🧠 我把这句听成"),
           first[:40], "后端一直在算 trace，是**前端从来没渲染**（`.trace` 样式是遗留的）。")
        _c(c, "D2 写了「办事」这个结论", "办事" in first, first[:60], "")
        _c(c, "D3 写了依据是哪一档判的", "依据：" in first, first[:60],
           "依据（样本库/语义层/规则层）是排查的钥匙：一眼看出这次花没花 API。")
        _c(c, "D4 语义层读出事名时点名它", "事名读成了「坐车」" in first, first[:80],
           "事名是卡片上最容易出错、学生最想核对的一项。")

        # ── 规则层那句话：**根本不会叫语义层**（常规下单句零 API 调用）──
        #    轨迹里必须如实说"规则层"，而且**不许**说"事名还没读出来"
        #    （卡片上明明写着 —— 那是假话，比不说更坏）。
        d2 = client.post("/api/chat", json={"sid": "trace-b2",
                                            "message": "帮我加个游泳周四19:00"}).json()
        f2 = ((d2.get("trace") or [{}])[0].get("phase") or "")
        _c(c, "D5 规则层判的句子，如实写「办事」+「规则层」",
           "办事" in f2 and "规则层" in f2, f2[:70],
           "常规下单句是**故意不问模型**的（省一次 API）；只看语义层字段会把它报成「问事」。")
        _c(c, "D6 规则层那条路不许谎报「事名还没读出来」", "事名" not in f2, f2[:70],
           "事名是主链路自己从原话里抠的，轨迹里说没读出来就是自相矛盾。")

        # ── 追问分支：它**不走** _sem_done，是"多 return 必漏"的实证；
        #    所以"听成什么"是在唯一出口统一补的（main.chat 包着 _chat_impl）。
        d3 = client.post("/api/chat", json={"sid": "trace-b3",
                                            "message": "帮我把中文12点的车记一下"}).json()
        tr3 = d3.get("trace") or []
        phases3 = [(t.get("phase") or "") for t in tr3]
        _c(c, "D7 确认这一句真的走的是「追问」分支",
           any("追问" in p for p in phases3), phases3[:4],
           "不确认的话这条断言可能被别的分支蒙混过关，测不出「多 return 会漏」。")
        _c(c, "D8 追问分支也带上了听成什么",
           bool(phases3) and phases3[0].startswith("🧠 我把这句听成"), phases3[:2],
           "这一条正是学生说错话时最该看到解释的地方 —— 一条都不能漏。")
    finally:
        restore()

    # ── 问答分支（另一条完全不同的路）。⚠️ 这里必须**关掉密钥**：
    #    FakeLLM 会把意图 JSON 当成问答的答案回出去，整个请求被劫持到 planner，
    #    根本到不了 faq 那条路（第一次跑就是这么假红的）。
    restore = patch_llm(_llm_json(title="游泳"), with_key=False)
    try:
        d4 = client.post("/api/chat", json={"sid": "trace-b4", "message": "图书馆几点开门"}).json()
        tr4 = d4.get("trace") or []
        f4 = ((tr4[0].get("phase") or "") if tr4 else "")
        _c(c, "D9 问答分支也带上了", f4.startswith("🧠"), f4[:50] or "没有 trace", "")
        _c(c, "D10 问事时**不**啰嗦事名（它用不上事名）", "事名" not in f4, f4[:70],
           "答一句校园规定也提「事名没读出来」，那是自己给自己找疑。")
    finally:
        restore()
        _set_samples([])
    return c.summary("D 批（思考过程）")


# ── F. 报了钟点、只是没说哪天 ──────────────────────────────────────────────
def batch_span_only(c, client):
    title("F. 学生报了准点、只是没说哪天 —— 就着他报的那个点出条")
    import datetime as _dt

    from app.modules.planner import _now_minutes, span_only_proposal, to_minutes

    # F1 报障那一幕的另一半：事名对了、钟点也在，只缺"哪天"。
    #    原来 `parse_add_todo` 的契约是"日期和时刻缺一个就返回 None" ——
    #    于是 12 点被丢掉、改去摊一屏候选，而候选里**根本没有他说的那个点**。
    p = span_only_proposal("加个坐车，12:00")
    _c(c, "F1 只有钟点也能出条（不再一律摊候选）",
       bool(p) and p.get("title") == "坐车" and p.get("start") == "12:00",
       p and {k: p.get(k) for k in ("title", "date", "start", "end")},
       "接的是 planner.span_only_proposal（原来叫 _span_only_proposal，只给自填那一档用）。")

    # F2 已经过去的钟点要顺延到明天：不给他排一段"已经过去的 12 点"。
    #    期望值**跟着现在几点算**，不写死日期 —— 否则跨过 12:00 这条就红。
    today = _dt.date.today().isoformat()
    tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
    want = today if to_minutes("12:00") > _now_minutes() else tomorrow
    _c(c, "F2 今天这个点已经过了就顺延到明天", bool(p) and p.get("date") == want,
       p and p.get("date"), "顺延规则跟 plan_todo_slot 里那条是同一个口径，不另立一套。")

    # F3 连钟点都没有 → **不猜**（返回 None，让调用方去摊候选 / 追问）。
    #    这一条是给"兜底不许变成臆造"立的桩。
    _c(c, "F3 连钟点都没有就不猜（返回 None）", span_only_proposal("加个坐车") is None,
       span_only_proposal("加个坐车"), "")

    # F4 只给起点 → 默认排 1 小时（跟单条卡那条路同一个默认）。
    _c(c, "F4 只给起点 → 默认排 1 小时", bool(p) and p.get("end") == "13:00",
       p and p.get("end"), "")

    # ── F5 端到端：**样本库全命中的形状**（有 title/start、没有 day_hint）──
    #    这正是学生那句被学进样本库之后的样子，也是线上真实走的那条路。
    _set_samples([])
    restore = patch_llm(_llm_json(title="坐车", start="12:00"), with_key=True)
    try:
        before = _todo_ids()
        d = client.post("/api/chat", json={"sid": "span-f1",
                                           "message": "中文12点的车"}).json()
        ans = d.get("answer") or ""
        opts = d.get("options") or []
        _c(c, "F5a 出的是一条确认条，**不是**一屏候选时段",
           len(opts) == 1 and opts[0].get("kind") == "todo_add"
           and "挑方便的勾上" not in ans,
           f"options={len(opts)} kind={opts[0].get('kind') if opts else None}",
           "摊候选等于让他把自己刚说过的钟点重挑一遍，而候选里没有那个点。")
        _c(c, "F5b 这条就是 12:00（今天已过则明天 12:00）",
           bool(opts) and opts[0].get("start") == "12:00"
           and opts[0].get("date") in (today, tomorrow),
           opts and {k: opts[0].get(k) for k in ("title", "date", "start", "end")}, "")
        after = _todo_ids()
        _c(c, "F5c 仍然只是提案：待办一条没多", before == after,
           f"{len(before)} → {len(after)} 条", "红线：出卡不写库，学生点确认才写。")
    finally:
        restore()
        _set_samples([])

    # ── F6 边界：**规则层那一路不吃这个兜底** ──
    #    这句有"帮我加"这个动作词，走的是规则层；里面的"12点"未必是这件事的时间
    #    （「三点下课，帮我加个买水果」会被 _pick_span 读成 03:00）。
    #    交不出日期时摊候选更安全 —— 宁可漏，不可替学生办错事。
    restore = patch_llm(_llm_json(title="坐车"), with_key=False)
    try:
        d2 = client.post("/api/chat", json={"sid": "span-f2",
                                            "message": "帮我加个12点的车"}).json()
        ans2 = d2.get("answer") or ""
        o2 = d2.get("options") or []
        _c(c, "F6 规则层那句话不吃这个兜底：它如实**问哪一天**，不臆造一个日期",
           "哪一天" in ans2 and not o2,
           f"answer={ans2[:40]!r} options={len(o2)}",
           "「三点下课，帮我加个买水果」里的三点未必是这件事的时间"
           "（_pick_span 会读成 03:00）——交不出日期时问一句/摊候选更安全。")
    finally:
        restore()
        _set_samples([])
    return c.summary("F 批（报了钟点没说哪天）")


# ── E. 红线：不写库 ────────────────────────────────────────────────────────
def batch_redline(c, client):
    title("E. 红线：这一批的改动一个字节都不许写库")
    _set_samples([])
    restore = patch_llm(_llm_json(title="游泳"), with_key=True)
    try:
        before = _todo_ids()
        for m in ("中文12点的车", "帮我把中文12点的车记一下", "我想去健身，帮我安排个时间"):
            client.post("/classify", json={"message": m})
        client.post("/api/chat", json={"sid": "tr-red", "message": "帮我加个游泳周四19:00"})
        after = _todo_ids()
        _c(c, "E1 诊断 + 出卡都没写库（待办 id 完全一致）", before == after,
                f"{len(before)} → {len(after)}",
                "只出提案、学生点确认才写 —— 这是从第一天就守的红线，任何改动都不许破。")
        _c(c, "E2 /classify 仍然自证不写库",
                client.post("/classify", json={"message": "把游泳删了"}).json()
                ["detail"]["wrote_anything"] is False, False, "")
    finally:
        restore()
        _set_samples([])
    return c.summary("E 批（红线）")


def main():
    rc = 0
    with sandbox():
        client = make_client()
        rc |= batch_typo(Checker())
        rc |= batch_sample(Checker(), client)
        rc |= batch_canonical(Checker())
        rc |= batch_span_only(Checker(), client)
        rc |= batch_trace(Checker(), client)
        rc |= batch_redline(Checker(), client)
    return rc


def test_typo_and_trace():
    """给 pytest 用（不装 pytest 也能跑：直接 `python tests/test_16_typo_trace.py`）。"""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
