# -*- coding: utf-8 -*-
"""#86 的护栏：思考轨迹**一条都不藏** —— 引擎那头别丢话、界面这头别剪行也别限高。

现场（学生 2026-10-29 的两句）：
  · 「有个代办时间和课表冲突了」→ 屏上只有两行轨迹，而且**真的一次工具都没调**。
    那是判定层把这句听成了「问事」（挂账 #72、施工单 #78/#85），**本单不碰**；
    本单先把已有的东西全量显出来，才分得清「没查」和「查了但看不见」。
  · 走引擎的那几句：模型明明先说了「我先看一下你的课表和待办，确认是哪一条冲突了。」，
    界面上一个字都没有 ⇒ 两头各吃掉一半：
      ① 引擎：`🤔 思考` 行是 `_ask` **之前**发的空标记，那一轮的 `content` 整段丢；
      ② 前端：带 answer 的剪到 80 字、工具那两条剪到 120 字，外面再套一个 240px 的滚动框。

改前实测（四条前提各一支探针，读数在快照里）：
  · 真实 `trace_log.jsonl` 88 笔 / 473 条 ⇒ **27 条被剪**、最长一条 572 字；
  · 真实 Key 跑两句 ⇒ 调工具那一轮 content 有 23 字 / 11 字（两句**都有**）；
  · 走 `on_event` 的流式那一路（学生页真正走的那条）⇒ 那一轮还吐了 14 个 token 块，
    `engine.py` 原来那句「前面几步一个正文块都不会吐」被当场否证（G 批钉注释已改口）。

⚠️ 保留的两条不是漏改，各有断言：① 空标记不画（那一轮真没话时画出来就是编推理）、
   ② 与气泡正文逐字相等的那条不重复画。F4 是**一对**（带话的画 / 不带话的不画）——
   只钉一边等于没钉边界：把「全部显示」做成「把空壳也画出来」是本单最容易越过去的那条线。
"""
import ast
import io
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, sandbox                       # noqa: E402
import test_79_trace_rendered as T79                        # noqa: E402  （复用它的 node runner，不抄第二份）

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
STUDENT = os.path.join(ROOT, "app", "static", "student.html")
ADMIN = os.path.join(ROOT, "app", "static", "admin.html")
ENGINE = os.path.join(ROOT, "app", "agent", "engine.py")
THINK = "\U0001f914 思考"
SENT = "我先看一下你的课表和待办，确认是哪一条冲突了。"     # 探针实测到的原句（23 字）


def read(p):
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def title(s):
    print("\n=== " + s + " ===")


def asy(coro):
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def tc_round(text, names):
    """模型「一边说一边要调工具」那一轮的形状（网关组装完给引擎的就是这个）。"""
    return {"role": "assistant", "content": text,
            "tool_calls": [{"id": f"c{j}", "function": {"name": n, "arguments": "{}"}}
                           for j, n in enumerate(names)]}


def plain(text):
    return {"role": "assistant", "content": text}


def run_engine(script):
    """真 `AgentEngine.run` + 脚本好的 `_ask` 序列。

    不联网、不需要 Key：`tools` 留空 ⇒ 引擎拿不到工具，`📦 工具结果` 那行会写
    「错误：没有名为 X 的工具」—— 本批钉的是**思考行的内容与顺序**，工具跑不跑与这件事无关。
    打的是**实例属性**（`eng._ask`），不动类、不动磁盘（#71 的纪律：进程内换属性比改文件安全）。
    """
    from app.agent.engine import AgentEngine
    eng = AgentEngine(system_prompt="测试", session_id="t97", tools={})
    it = iter(script)

    async def fake_ask(self, messages):
        try:
            return next(it)
        except StopIteration:
            return plain("脚本用完了")

    eng._ask = types.MethodType(fake_ask, eng)
    return asy(eng.run("随便一句"))


def batch_e_engine():
    c = Checker()
    title("E. 引擎：模型那一轮自己说的话有没有进轨迹")
    with sandbox():
        r1 = run_engine([tc_round(SENT, ["get_weekly_timetable", "list_todos_range"]),
                         plain("你的周表还是空的。")])
        tr = r1.get("trace") or []
        ph = [str(t.get("phase")) for t in tr]
        th = [t for t in tr if str(t.get("phase")).startswith("\U0001f914")]
        c.check("E1 调工具那一轮的 content **逐字**进了 `🤔 思考` 行（改前这行是空的，界面上一个字都没有）",
                len(th) == 1 and th[0].get("answer") == SENT, str([t.get("answer") for t in th])[:120])
        c.check("E2 顺序对：思考行在**它那一轮第一条工具行之前**（先说要去看什么，再动手）",
                ph.index(THINK) < next(i for i, p in enumerate(ph) if p.startswith("\U0001f527")),
                str(ph))
        c.check("E3 那一轮同时调了两只工具 ⇒ 思考行只有**一条**（一步一句，不是每只工具复述一遍）",
                len(th) == 1 and sum(1 for t in tr if str(t.get("phase")).startswith("\U0001f527")) == 2,
                str(ph))

        r2 = run_engine([tc_round("", ["find_free_slots"]), plain("空表。")])
        tr2 = r2.get("trace") or []
        th2 = [t for t in tr2 if str(t.get("phase")).startswith("\U0001f914")]
        c.check("E4 那一轮**没话**时不许编：思考行还在、`answer` 是空串（前端 ① 会把它过滤掉，F4 钉这一对）",
                len(th2) == 1 and th2[0].get("answer") == "", str(th2))
        c.check("E4-bis 空着就是空着 —— 不许拿工具名/参数去填那句话（填了就是把系统的猜测定成模型的陈述）",
                "find_free_slots" not in (th2[0].get("answer") or ""), str(th2[0])[:120])

        r3 = run_engine([plain("今天没有课。")])
        tr3 = r3.get("trace") or []
        c.check("E5 直答那一轮**不许多出一条思考行**：同一句话已经由 `💡 最终回答` 带出去了，"
                "再抄一遍就是把轨迹撑长（改前这里是一条永远为空的标记，改后是 0 条 —— 两头都不是 2 条）",
                len(tr3) == 1 and str(tr3[0].get("phase")).startswith("\U0001f4a1"),
                str([t.get("phase") for t in tr3]))

        r4 = run_engine([tc_round("先看课表。", ["get_weekly_timetable"]),
                         tc_round("再看待办。", ["list_todos_range"]),
                         plain("周四那节高数和你的「做题」撞了。")])
        tr4 = r4.get("trace") or []
        th4 = [t.get("answer") for t in tr4 if str(t.get("phase")).startswith("\U0001f914")]
        c.check("E6 多轮：思考行条数 == 调工具的轮次数，且各轮那句**不串味**（第一步不能说成第二步的话）",
                th4 == ["先看课表。", "再看待办。"], str(th4))
        steps4 = [(t.get("step"), str(t.get("phase"))[:6]) for t in tr4]
        c.check("E6-bis step 号跟着轮次走（1 思考 / 1 调用 / 1 结果 / 2 思考 …）—— 跨轮这件事靠它显出来",
                [s for s, _ in steps4] == [1, 1, 1, 2, 2, 2, 3] or
                [s for s, p in steps4 if p.startswith("\U0001f914")] == [1, 2],
                str(steps4))
        c.check("E7 思考行的键集合仍是 {step, phase, answer}（前端只认这几个字段，多一个少一个都是接口变了）",
                set(th[0].keys()) == {"step", "phase", "answer"}, str(sorted(th[0].keys())))

    src = read(ENGINE)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run")
    emits = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_trace"]
    c.check("E8 `run` 里 `_trace` 调用点仍是**四处**（本单是把其中一处搬了位置，不是新造第五个出口）",
            len(emits) == 4, str(len(emits)))
    think_emits = [n for n in emits if THINK in ast.unparse(n)]
    ask = [n for n in ast.walk(fn)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "_ask"]
    c.check("E9 思考行的 emit 在 `msg = await self._ask(...)` **之后**（在之前就拿不到这一轮的话 —— 本单的因果关系就这一行）",
            len(think_emits) == 1 and len(ask) >= 1
            and think_emits[0].lineno > ask[0].lineno,
            str((think_emits[0].lineno if think_emits else None, ask[0].lineno if ask else None)))
    ifb = [n for n in ast.walk(fn) if isinstance(n, ast.If) and "tool_calls" in ast.unparse(n.test)]
    inside = any(any(m is think_emits[0] for m in ast.walk(n)) for n in ifb) if think_emits else False
    c.check("E10 而且它**在 `if msg.get('tool_calls')` 的体内** —— 放在体外就变成 E5 那条直答也多抄一行",
            inside, str([ast.unparse(n.test)[:40] for n in ifb]))
    bad = [ln for ln in src.split(chr(10)) if "都不会吐" in ln]
    c.check("E11 那句被否证的注释不许还当成事实写着：全文件只允许出现在**标了「否证」的那一行**里，"
            "而且不许再拿「test_49 A 批钉过」当出处（实测 grep 那个文件对这句话零命中）",
            len(bad) == 1 and "否证" in bad[0] and "test_49" not in bad[0],
            str([ln.strip()[:46] for ln in bad]))
    return c.summary("E 批（引擎：思考行带话）")


def batch_f_front():
    c = Checker()
    title("F. 前端：node 真跑页面里那份 renderTrace（不剪行、不限高，但空壳仍不画）")

    long_res = T79.render([{"step": 1, "phase": "\U0001f4e6 工具结果", "action": "list_day_todos",
                             "result": "空" * 400}], "答")
    row = re.search(r"\U0001f4e6 工具结果 — (.*?)</div>", T79._seen(long_res))
    c.check("F1 400 字的工具结果**一个字不剪**地进屏（改前这里剪到 120 字再补省略号）——"
            "这一行是「我查到了什么」的唯一凭据",
            row is not None and row.group(1).count("空") == 400, str(len(row.group(1)) if row else -1))
    c.check("F2 带 answer 的那条同样不剪（旧的 80 字口径已让位；「别把正文抄进轨迹」改由 ② 管，见 F6）",
            T79.render([{"step": 1, "phase": "✅ 某步", "answer": "字" * 200}], "答").count("字") == 200)

    tail = T79.render([{"step": 1, "phase": "\U0001f4e6 工具结果", "action": "x",
                        "result": "前面一句话" * 40 + "…"}], "答")
    tail_row = re.search(r"\U0001f4e6 工具结果 — (.*?)</div>", T79._seen(tail))
    c.check("F3 正控（负判据必须配一根会命中的轴）：文本**自己**就以省略号结尾时它还在 —— "
            "证明「行尾不许有省略号」抓的是渲染器加的那一颗，不是恒真",
            tail_row is not None and tail_row.group(1).endswith("…")
            and tail_row.group(1).count("句") == 40, str(tail_row.group(1)[-12:]) if tail_row else "")

    with_ans = T79.render([{"step": 1, "phase": THINK, "answer": SENT},
                           {"step": 1, "phase": "\U0001f527 调用工具", "action": "list_todos_range",
                            "args": {}}], "答")
    bare = T79.render([{"step": 1, "phase": THINK},
                       {"step": 1, "phase": "\U0001f527 调用工具", "action": "list_todos_range",
                        "args": {}}], "答")
    c.check("F4-a 思考行**带话** ⇒ 画出来（这才是本单要显出来的那句「我先看一下…」）",
            THINK in with_ans and SENT in T79._seen(with_ans), with_ans[:120])
    c.check("F4-b 思考行**不带话** ⇒ 照旧不画（「全部显示」不等于把空壳也画出来 —— 这一条守住的是边界）",
            THINK not in bare and bare.count("trace-row") == 1, str(bare.count("trace-row")))
    c.check("F4-c 两处的行数只差正好这一条（不是把整块弄坏、也不是多画了一行噪声）",
            with_ans.count("trace-row") == bare.count("trace-row") + 1,
            str((with_ans.count("trace-row"), bare.count("trace-row"))))

    dup = T79.render([{"step": 1, "phase": "\U0001f4a1 最终回答", "answer": "已经帮你排好了"}],
                     "已经帮你排好了")
    c.check("F5 ② 还在：与气泡正文逐字相等的那条不重复画（不剪行之后，这条更得钉着 —— 全量抄一遍正文比剪过的更吵）",
            dup == "", repr(dup)[:80])

    inj = T79.render([{"step": 1, "phase": "\U0001f4e6 工具结果", "action": "search_kb",
                       "result": "<script>alert(2)</script> " + "文档原文" * 40}], "答")
    c.check("F6 不剪行之后转义面变大了，重钉一次：320 字的 payload 里那两颗标签仍死在路上",
            "<script" not in inj and "&lt;script" in inj
            and inj.count("文档原文") == 40, inj[:110])
    return c.summary("F 批（前端 node 真跑）")


def batch_g_static():
    c = Checker()
    title("G. 静态与 CSS：剪行的写法一处都不留 + 整族扫其余会把行藏起来的地方")
    stu = read(STUDENT)
    body = T79._slice_fn(stu, "function renderTrace(")
    c.check("G1 `renderTrace` 里没有任何把行剪短的写法（不许 `slice`、也不许自己补省略号）"
            "—— 判据钉在**写法**上而不是某个字数（旧的 80/120 就是钉字数，改一次顶红一次）",
            ".slice(" not in body and '+ "\u2026"' not in body,
            [ln.strip()[:52] for ln in body.split(chr(10))
             if "slice" in ln or "\u2026" in ln][:2])
    c.check("G2 页面里 `renderTrace` 仍只定义一处、调用也只一处（JSON 与 SSE 两条出口汇到同一个它）",
            stu.count("function renderTrace(") == 1
            and len([1 for ln in stu.split(chr(10))
                     if re.search(r"renderTrace\(", ln) and "function renderTrace" not in ln]) == 1)

    seg = stu[stu.index(".trace {"):stu.index(".inputbar {")]
    c.check("G3 轨迹那一族（`.trace` → `.inputbar`）里**一处 max-height 都没有**（改前是 240px 那道限高）",
            "max-height" not in seg, str(seg.count("max-height")))
    n_all = stu.count("max-height")
    c.check("G4 正控：同一把尺子在页面**别处**量得到 max-height（聊天框 440px / 明细面板 430px / "
            "日志区），所以 G3 那条不是因为扫描范围为空才绿的",
            n_all >= 3 and "max-height: 440px" in stu, str(n_all))
    c.check("G5 拆的是限高、不是容器：`.trace-body` 那个 div 在页面里仍**只有一处**"
            "（#60-C 那条「标题不许跟着一起滚」靠的是这个结构，与本单的 240px 是两件事 —— "
            "两头各钉各的，别合并成一条）",
            stu.count('<div class="trace-body">') == 1,
            str(stu.count('<div class="trace-body">')))
    html = T79.render([{"step": 1, "phase": THINK, "answer": SENT}], "答")
    c.check("G5-bis 而且画出来的那块里 `<summary>` 确实在容器**前面**（运行时读数，不是读源码 —— "
            "限高拆掉之后标题若跟着一起滚，就会在这一块里露出来）",
            html.index("<summary>") < html.index("trace-body"), str(html[:60]))

    adm = read(ADMIN)
    tb = T79._slice_fn(adm, "function traceStepHtml(")
    c.check("G6 整族扫：管理端「Agent 工作流」那一头**本来就没剪行**（`esc(txt)` 全量 + 空标记自己写着"
            "「这一步只有个标记，没带话」）—— 本单把学生端对齐到同一口径，钉一句免得两头又漂开",
            ".slice(" not in tb and "esc(txt)" in tb,
            [ln.strip()[:48] for ln in tb.split(chr(10)) if "slice" in ln][:2])
    eng = read(ENGINE)
    c.check("G7 全项目仍然没有 `reasoning_content`（#60 §六.1：不上 reasoner、CoT 不可复现）。"
            "本单拿的是**消息正文** `content`，不是思维链 —— 这条上限没被本单挪动",
            "reasoning_content" not in eng, "")
    return c.summary("G 批（静态与 CSS 整族扫）")


def main():
    code = 0
    ran = 0
    batches = (("E 批（引擎）", batch_e_engine),
               ("F 批（前端 node 真跑）", batch_f_front),
               ("G 批（静态与 CSS）", batch_g_static))
    for label, fn in batches:
        ran += 1
        try:
            rc = fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  \u274c {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    # ⚠️ 别写成 `code = code or fn()`：`or` 短路会让第一批变红后面的批根本不跑（#82 踩过）
    assert ran == len(batches), f"只跑了 {ran}/{len(batches)} 批"
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
