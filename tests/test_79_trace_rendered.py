"""#58 的护栏：`renderTrace` 必须认 `action`/`args`/`result`，否则 ReAct 在界面上是隐形的。

现场（施工单 §〇，八条前提我逐条复算过，全对）：引擎记的是完整三段式
（`🤔 思考` / `🔧 调用工具`+action+args / `📦 工具结果`+action+result / `💡 最终回答`+answer），
而前端那句 `if (!hasAns && _BARE_TRACE_PHASES.indexOf(label) >= 0) return;` **只认 `answer`**
⇒ 中间两步被当成"空标记"整条扔掉，评委看到的"我是这么想的"是空的。
讽刺之处：**越依赖引擎，ReAct 越隐形** —— 只剩确定性快路那种单步能露脸。

三批的强度不一样，别看混：
  A 形状与边界（读源码，钉"只改一个函数""不抄第二份""engine/接口没被伸手"）；
  B **语义**：用 node 真的执行从 `student.html` 里抠出来的那份 `renderTrace`（+ 页面的 `richText`
    与 `app/static/esc.js` 的 `esc`），拿真返回值判 —— 与 `test_33` B 批同一手法。
    为什么不在 Python 里复刻一份判据来比：复刻的那份自己就可能抄错，那是拿会说谎的工具去验另一个。
  C 演示句**能不能走到引擎**（离线判据 `_rule_intent(那句) is None`）——
    B 批证的是"有 trace 就画得出来"，这一批证的才是"真会有一条多步 trace 进来"。
    快路哪天把那句截胡，界面上的 ReAct 就又没了，而 B 批全不会红。
"""
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import PROJ_DIR, Checker, sandbox, seed_todos, title, wipe_todos  # noqa: E402

STATIC = os.path.join(PROJ_DIR, "app", "static")
STUDENT = os.path.join(STATIC, "student.html")
ESC_JS = os.path.join(STATIC, "esc.js")
ENGINE = os.path.join(PROJ_DIR, "app", "agent", "engine.py")

# 完整一轮 ReAct 的形状，逐字照 `engine.py` 那四处 `_trace`（工具名/参数用真的那几个）
FULL_TRACE = [
    {"step": 1, "phase": "🤔 思考"},
    {"step": 1, "phase": "🔧 调用工具", "action": "get_weekly_timetable", "args": {}},
    {"step": 1, "phase": "📦 工具结果", "action": "get_weekly_timetable",
     "result": "[{\"day\": 3, \"start\": \"08:00\", \"course\": \"高等数学\"}]"},
    {"step": 2, "phase": "🔧 调用工具", "action": "find_free_slots",
     "args": {"days": "3,5", "need_minutes": 120}},
    {"step": 2, "phase": "📦 工具结果", "action": "find_free_slots",
     "result": "周三 14:00-16:00 空；周五 15:00-18:00 空"},
    {"step": 3, "phase": "💡 最终回答", "answer": "周三下午和周五下午都空，挑一段？"},
]
ANSWER = "周三下午和周五下午都空，挑一段？"


def _slice_fn(src, head):
    """从 `head` 起抠出整个函数（大括号配对）。抠不出来就直接报错，不许静默返回空串。"""
    i = src.index(head)
    depth, j = 0, src.index("{", i)
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1] + "\n"
        j += 1
    raise AssertionError("函数没配对到大括号：" + head)


def render_all(cases):
    """cases = [(trace, content), ...] → node 真跑出来的 HTML 片段列表（不改动一个字）。

    页面里 `esc` 来自外链 `esc.js`、`richText` 是页面内联的，所以两份都得给；
    `renderTrace` 里那两处 `…`/中文照常按 UTF-8 走。
    """
    # 先把自己的输入钉一遍：少一层括号（把 `(trace, content)` 整对当 trace 传进来）时
    # 下面那段 JS 不会报错，只会**安静地画出空串**，于是所有「期望非空」的断言一起红、
    # 所有「期望空」的断言一起**假绿**（本批第一版就是这么写的）。
    for trace, _ in cases:
        if not isinstance(trace, list) or not all(isinstance(t, dict) for t in trace):
            raise AssertionError("case 的形状坏了，trace 必须是 [{...}, ...]：拿这种输入去比 = 断言在量空气")
    src = open(STUDENT, encoding="utf-8").read()
    bundle = ("const fs=require('fs');"
              "const window={};"
              "eval(fs.readFileSync(process.argv[1],\"utf8\"));"
              "globalThis.esc=window.esc;"
              + _slice_fn(src, "function richText(")
              + re.search(r"const _BARE_TRACE_PHASES = \[.*?\];", src, re.S).group(0) + "\n"
              + _slice_fn(src, "function renderTrace(")
              + "const cs=JSON.parse(process.argv[2]);"
              "process.stdout.write(JSON.stringify(cs.map(c=>renderTrace(c[0],c[1]))));")
    if not shutil.which("node"):
        raise AssertionError("本机没有 node —— 这一批只能靠 node 真跑，不许退回读源码判语义")
    p = subprocess.run(["node", "-e", bundle, ESC_JS, json.dumps(cases)],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    if p.returncode != 0:
        raise AssertionError("node 跑挂：先怀疑从 student.html 抠出来的那段语法坏了（改坏 JS 正是本批要抓的）："
                             + (p.stderr or "")[:400])
    return json.loads(p.stdout)


def _seen(html):
    """把实体还原成屏幕上那一眼看到的字（`&quot;` → `"`）。
    断言「参数进没进屏」要比的是这个，不是原始 HTML —— 拿裸引号去 `in` 转义后的串，
    量的是 esc 的工作而不是渲染的结果。"""
    return html.replace("&quot;", '"').replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&")


def render(trace, content):
    """按 `renderTrace(trace, content)` 的原签名收两个参数。
    ⚠️ 别改回 `render(case)` 收元组：写成 `render((trace, content,),)` 多一层括号时
    JS 不报错、只画空串，于是「期望非空」的全红、「期望空」的全假绿（本批第一版就栽在这）。"""
    return render_all([(trace, content)])[0]


def batch_a_shape():
    c = Checker()
    title("A. 形状与边界：只改一个函数、不抄第二份、后端没被伸手")
    src = open(STUDENT, encoding="utf-8").read()
    lines = src.splitlines()

    defs = [i for i, ln in enumerate(lines) if re.search(r"function renderTrace\s*\(", ln)]
    c.check("A1 `renderTrace` 全页只定义一处（第二处 = 同一件事抄两份，改一份留一份）",
            len(defs) == 1, str([i + 1 for i in defs]))
    calls = [i for i, ln in enumerate(lines)
             if re.search(r"renderTrace\(", ln) and "function renderTrace" not in ln]
    c.check("A2 调用点也只有一处（施工单 §0.3 说 JSON 与 SSE 两条出口都汇到它 —— 真汇到才算修完两条路）",
            len(calls) == 1, str([i + 1 for i in calls]))
    body = _slice_fn(src, "function renderTrace(")
    c.check("A3 判「有没有内容」这一眼现在看三个字段：answer / action / result（本单的主修）",
            all(k in body for k in ("hasAction", "hasResult", "hasTool")))
    c.check("A4 那道过滤条件真的是「三个都没有才丢」，不是把名单清空蒙混（清空名单=「🤔 思考」也会画出来）",
            "if (!hasAns && !hasTool && _BARE_TRACE_PHASES.indexOf(label) >= 0) return;" in body)
    c.check("A5 `_BARE_TRACE_PHASES` 那三档一个都没删（「思考」该继续不画，画了就是编推理）",
            re.search(r"const _BARE_TRACE_PHASES = \[\"思考\", \"调用工具\", \"工具结果\"\];", src) is not None)
    c.check("A6 转义没被绕过：`action`/`args`/`result` 汇成一条串之后仍走 richText（红线 8/16 同一条纪律）",
            'richText(an)' in body and "innerHTML +" not in body)
    c.check("A6-bis 而且**没有**把 args 单独拼进 HTML（它是最容易漏的那一个：里面是模型给的任意键值）",
            "an += \"(\" + a + \")\"" in body and "richText(a)" not in body)
    c.check("A7 answer 那条的截断仍是 80 字（既有口径，别的测试钉着）；工具那条放宽到 120",
            "const cap = hasAns ? 80 : 120;" in body)
    c.check("A8 去重那条（②）没被顺手删：最终回答与气泡正文同文时不重复画",
            "if (an && body && an === body) return;" in body)

    eng = open(ENGINE, encoding="utf-8").read()
    c.check("E→A9 引擎侧一个字没改：四处 `_trace` 的形状还是 step/phase(+action/args/result/answer)",
            eng.count('await self._trace(') == 4
            and '"action": fn_name, "args": args' in eng
            and '"result": str(result)[:600]' in eng)
    c.check("A10 本单不动接口：`/api/chat` 的返回里没有新增字段（trace 本来就带着 action/args/result 出来了）",
            '"trace"' in open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read())
    return c


def batch_b_semantics():
    c = Checker()
    title("B. 语义：node 真跑页面里那份 renderTrace（不是读它的源码）")

    out = render(FULL_TRACE, ANSWER)
    seen = _seen(out)
    c.check("B1 一轮完整 ReAct 现在**画得出来**：调用工具那两行在，工具名也在（这就是评委要看的东西）",
            "🔧 调用工具" in out and "📦 工具结果" in out
            and "get_weekly_timetable" in out and "find_free_slots" in out, out[:120])
    c.check("B2 参数进了屏（`find_free_slots({\"days\":\"3,5\",...})`）—— 只有工具名没有参数，等于还是看不见在做什么",
            '"days":' in seen and "3,5" in seen and "need_minutes" in seen, seen[230:400])
    c.check("B3 工具的返回值进了屏（Observation）：「周三 14:00-16:00 空」这一串必须看得见",
            "周三 14:00-16:00 空" in out)
    c.check("B4 「🤔 思考」**仍然不画**（它真没内容；画出来 = 给用户三个看不懂的词，也 = 编推理）",
            "🤔 思考" not in out)
    c.check("B5 「💡 最终回答」与气泡正文同文 → 整条不画（既不重复也不留光秃秃的标签）",
            "💡 最终回答" not in out)
    c.check("B6 行数正好是中间那四步（2 调用 + 2 结果），既没多也没少",
            out.count('<div class="trace-row">') == 4, str(out.count('<div class="trace-row">')))
    c.check("B7 步骤号还在（1/1/2/2）—— 跨轮这件事本身就靠它显出来",
            ">1</span>" in out and ">2</span>" in out and out.count(">1</span>") == 2)
    c.check("B8 结果那行是「名字 → 片段」，不带参数（参数在上一行，重复一遍就是把轨迹撑长的旧病）",
            "📦 工具结果 — find_free_slots → 周三 14:00-16:00 空" in seen)

    # 反向：确定性快路那种"带 answer 的单步"必须照旧画出来（把对的修坏是这一族头号风险）
    det = render([{"step": 1, "phase": "✅ 学生确认", "answer": "把「复习」排进周三 14:00"}],
                 "已经帮你排好了")
    c.check("B9 对照：确定性快路那种**带 answer 的单步**照旧画（新判据没把它挤掉）",
            "✅ 学生确认" in det and "把「复习」排进周三 14:00" in det, det[:120])
    empty = render([], "随便一句")
    c.check("B10 空 trace → 空串（不许出现一个点开啥都没有的「我是这么想的」壳）", empty == "", repr(empty))
    only_think = render([{"step": 1, "phase": "🤔 思考"}], "答")
    c.check("B10-bis 只有一条「思考」时也仍是空串（整块不出现，不是出现一块空的）", only_think == "", repr(only_think))
    bogus = render([{"step": 1, "phase": "🤔 思考", "answer": "我猜用户在想别的"}], "答")
    c.check("B11 「不画思考」是因为它**真没内容**，不是把 phase 写死跳过 —— 塞给它 answer 照样画",
            "🤔 思考" in bogus and "我猜用户在想别的" in bogus, bogus[:120])
    only_action = render([{"step": 1, "phase": "🔧 调用工具", "action": "list_my_packages",
                           "args": {}}], "答")
    c.check("B12 只有 action、还没有 result（工具刚被叫出去那一刻）→ 画得出名字，**不留一个空箭头**",
            "list_my_packages" in only_action and "→" not in only_action, only_action[:120])
    c.check("B12-bis 空参数对象不画成 `name({})`（那是噪声，不是信息）", "({})" not in only_action)

    inj = render([{"step": 1, "phase": "🔧 调用工具", "action": "search_kb",
                   "args": {"query": '"><img src=x onerror=alert(1)>'}},
                  {"step": 1, "phase": "📦 工具结果", "action": "search_kb",
                   "result": "<script>alert(2)</script> 和 一段学生原话"},
                  {"step": 2, "phase": "🤔 思考"}], "答")
    c.check("B13 注入必须死在路上：args 与 result 里的标签全部转义（工具返回值里就是文档原文与学生原话）",
            "<img" not in inj and "<script" not in inj and "&lt;img" in inj and "&lt;script" in inj,
            inj[:150])
    c.check("B13-bis 连闭合属性的那颗引号也没放过：转义后的串里不存在 `\"><` 这个能起标签的边界",
            '"><img' not in inj and "&quot;&gt;&lt;img" in inj, inj[:200])

    long_res = render([{"step": 1, "phase": "📦 工具结果", "action": "list_day_todos",
                        "result": "空" * 300}], "答")
    # ⚠️ 尺子量的是**整行**（`工具名 → 片段` 一起算 120），不是"片段 120"。
    # 别把断言写成 `count("空") == 120`：那样量的是剪掉之后剩多少，前缀占掉几位就跟着变，
    # 是条会自己漂的断言（第一版就这么红的，代码没错）。
    row = re.search(r"📦 工具结果 — (.*?)</div>", _seen(long_res)).group(1)
    c.check("B14 工具那一条整行截到 120 字再加省略号（这块是给人扫一眼的，不是读第二遍正文）",
            len(row) == 121 and row.endswith("…"), f"整行 {len(row)} 字：{row[:24]}…")
    long_ans = render([{"step": 1, "phase": "✅ 某步", "answer": "字" * 200}], "答")
    c.check("B14-bis 但带 answer 的那条**仍是 80 字**（既有口径不许被顺手放宽：放宽等于把气泡复制进轨迹）",
            long_ans.count("字") == 80, str(long_ans.count("字")))
    # 去重那条（②）比的是"整行文字 == 气泡正文"，不是"这条是最终回答" —— 两头都得钉住：
    dup = render([{"step": 1, "phase": "📦 工具结果", "result": ANSWER}], ANSWER)
    c.check("B15 去重按**整行逐字等于正文**判：工具行只要拼出来就是正文本身，也照样不重复画",
            dup == "", repr(dup)[:80])
    keep = render([{"step": 1, "phase": "📦 工具结果", "action": "x", "result": ANSWER}], ANSWER)
    c.check("B15-bis 而带工具名的那条**不许被去重吃掉**（`名字 → ` 前缀让它不等于正文）。"
            "这是有意的：Observation 恰好被模型复述进答案时，抹掉它就是把 #58 刚找回来的证据又扔一遍",
            "📦 工具结果" in keep and "x → " in keep, keep[100:180])
    return c


DEMO_LINES = ("帮我看看这周哪些下午有空，找两小时复习",
              "我有哪些快递没取，取件码是多少")


def batch_c_demo_lines():
    """C 批：演示句**会不会被快路截胡**（截胡了就根本没有多步 trace，B 批全绿也白绿）。"""
    c = Checker()
    title("C. 演示句能不能走到引擎（离线判据，不联网）")
    import app.main as M

    with sandbox():
        seed_todos([])
        for line in DEMO_LINES:
            c.check(f"C 这句没有落在任何确定性快路上（`_rule_intent` 判不出来才轮得到引擎）：{line}",
                    M._rule_intent(line) is None, str(M._rule_intent(line)))
        # 对照：同一批里"明显该走快路"的那句必须仍被快路接走，
        # 否则上面那条 C 是因为"谁都判不出来"而真 —— 那等于整条链坏了。
        c.check("C-对照 快路本身没坏：「把我的待办列出来」仍被规则层接住（不是掉给模型）",
                M._rule_intent("把我的待办列出来") is not None)
    eng = open(ENGINE, encoding="utf-8").read()
    c.check("C2 引擎一轮最多 6 步、每步都记 thought/action/observation 三件事（演示时「多轮」是有可能的）",
            "max_steps = 6" in eng and "for step in range(1, max_steps + 1)" in eng)
    return c


def main():
    global code
    print("test_79_trace_rendered：#58 ReAct 轨迹被前端过滤（renderTrace 认 action/result）")
    print("=" * 60)
    rc = 0
    for name, fn in (("A 批（形状与边界）", batch_a_shape),
                     ("B 批（node 真跑 renderTrace）", batch_b_semantics),
                     ("C 批（演示句能不能走到引擎）", batch_c_demo_lines)):
        # ⚠️ 用 max() 不用 `or`：`or` 会在第一批变红时短路，后面几批根本不跑（#55 踩过）
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
