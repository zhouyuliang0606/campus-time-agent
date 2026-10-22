# -*- coding: utf-8 -*-
"""#56 的护栏：时区收口（`app/clock.py`）+ JS 语法闸并进收口 + 挂账 #69 的日期清零 + test_39 起始态。

四批按"这件事靠什么证明"分，不按文件分：

A 批 —— **收口的形状**。全项目取时间只剩一个入口，是"改了 24 处"这句话的唯一证据：
    用 AST 扫 `app/`，除 `clock.py` 自己以外不许再有裸 `datetime.now()` / `date.today()`。
    反向也要钉：6 处 `time.time()`（epoch 秒，算 TTL/缓存过期，与时区无关）不许被"顺手统一"掉，
    `planner` 的 `import datetime` 还要在（它另外要用 `timedelta`/`fromisoformat`）。
B 批 —— **时区真的按 `_TZ` 算**。本机就是 +8，改了看不出任何差别 ⇒ 只跑本机等于没验。
    正规做法是把 `_TZ` 临时换成别的偏移，看 `clock.now()` 跟不跟着动（施工单 §1.5 那条变异式验证）。
    另外钉住 `now()` 返回 **naive**：全项目的 `ts` 是字符串、各处 `strptime` 回来做减法，
    带进 tz-aware 那些减法当场 TypeError（`main._age_hours`、`express` 的天数差都是）。
C 批 —— **JS 闸**。病是"闸只查两个页面，另外五页没人看"，所以行为必须实测：
    临时造一个 `station.html` 少个引号的目录，闸要**报出它是哪个文件第几块**；
    空目录要 `exit 2` 说"这次没验到任何东西"（零个文件当通过，比红更危险）。
    还要钉 `run_all.py` 那边：调用它、失败计入总账、**没装 node 就打印跳过且不判失败**。
D 批 —— **#69 与起始态**。写死"将来"的日期跨过那天就成了过去，红线 17 会正确拒收，
    症状是"同一份代码昨天绿今天红，你没改任何东西"。这里钉两件事：③类清零、
    ①类（当输入用的固定日子）**不许被下一位顺手改成现算** —— 那些断言今天几号都成立，改了只是把
    它们变成"跟着时钟跑"，反而多了出错点。

⚠️ 本文件不联网、不写真实 `app/data`：C 批用的是 `tempfile` 里的假 static 目录。
"""
import ast
import datetime
import glob
import os
import re
import shutil
import subprocess
import tempfile

from _harness import Checker, sandbox, title

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOCK = os.path.join(PROJ, "app", "clock.py")
MAIN = os.path.join(PROJ, "app", "main.py")
STORE = os.path.join(PROJ, "app", "store.py")
PLANNER = os.path.join(PROJ, "app", "modules", "planner.py")
RUN_ALL = os.path.join(PROJ, "tests", "run_all.py")
JS_GATE = os.path.join(PROJ, "tests", "_check_static_js.js")
STATIC = os.path.join(PROJ, "app", "static")
TEST39 = os.path.join(PROJ, "tests", "test_39_weekly_recurring_todo.py")

# 施工单 §4.2 说的"名字里带这些词的日期常量，几乎一定是当将来用"
FUTUREISH = re.compile(r"FUTURE|NEXT|UPCOM|TARGET|DEADLINE|TOMORROW", re.I)
DATE_RE = re.compile(r"^20\d\d-\d\d-\d\d$")

code = 0


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _call_names(path):
    """把一个文件里所有调用的"点号名"收集起来（`datetime.date.today` 这种）。"""
    out = []
    for node in ast.walk(ast.parse(_read(path))):
        if isinstance(node, ast.Call):
            f, parts = node.func, []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            out.append(".".join(reversed(parts)))
    return out


def _date_assignments(path):
    """返回该文件里「赋值成字符串常量」的日期：[(行号, [变量名], 值)]（天然排除注释与 docstring）。"""
    rows = []
    for node in ast.walk(ast.parse(_read(path))):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        val = getattr(node, "value", None)
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        if not DATE_RE.match(val.value.strip()):
            continue
        targets = getattr(node, "targets", [getattr(node, "target", None)])
        rows.append((node.lineno, [t.id for t in targets if isinstance(t, ast.Name)], val.value))
    return rows


def batch_a_clock_shape():
    c = Checker()
    title("A. #56-A：取时间只剩一个入口（形状与两处反向钉）")
    from app import clock

    c.check("A1 `clock.py` 只 import 标准库（它被所有模块依赖，反向 import app.* 会成环）",
            all(not l.strip().startswith(("from app", "import app"))
                for l in _read(CLOCK).splitlines()),
            "有 app.* 的顶层 import")
    c.check("A2 偏移是**固定 +8**（不是系统时区、也不是 ZoneInfo —— 本机没装 tzdata，那个直接抛异常）",
            clock._TZ.utcoffset(None) == datetime.timedelta(hours=8), str(clock._TZ))
    c.check("A3 `now()` 返回 naive（全项目的 ts 都是字符串、各处减法要求 naive；带 tz 就 TypeError）",
            clock.now().tzinfo is None and clock.today() == clock.now().date(),
            repr(clock.now()))
    c.check("A4 `store._now()` 与 `clock.now_str()` 是同一个格式（落盘时间串只有一个样子；"
            "不比'两次调用相等'—— 那会在跨秒的一刻自己红）",
            re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", clock.now_str()) is not None
            and re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", store_now_shape()) is not None
            and "clock.now_str()" in _slice_fn(STORE, "def _now()"),
            clock.now_str() + " / " + store_now_shape())
    c.check("A5 `store._now()` 这个唯一落盘出口还在，实现已经换成 `clock.now_str()`",
            "def _now() -> str:" in _read(STORE)
            and "clock.now_str()" in _slice_fn(STORE, "def _now()"),
            _slice_fn(STORE, "def _now()")[:80])

    bare = []
    for p in glob.glob(os.path.join(PROJ, "app", "**", "*.py"), recursive=True):
        if os.path.samefile(p, CLOCK):
            continue                      # clock 自己就是那个出口，不算漏
        names = _call_names(p)
        for n in names:
            if n in ("datetime.datetime.now", "datetime.date.today", "datetime.datetime.utcnow"):
                bare.append("%s:%s" % (os.path.relpath(p, PROJ), n))
    c.check("A6 `app/` 里除 clock.py 之外没有第二处裸取时间（'24 处已收口'这句只能这么证明）",
            not bare, "、".join(bare[:5]))

    tt_sites = []
    for p in glob.glob(os.path.join(PROJ, "app", "**", "*.py"), recursive=True):
        n = sum(1 for x in _call_names(p) if x == "time.time")
        if n:
            tt_sites.append((os.path.relpath(p, PROJ), n))
    c.check("A7 反向钉：`time.time()` 那 6 处照旧（epoch 秒算 TTL/缓存过期，与时区无关；"
            "被'顺手统一'换掉只会多出 6 个出错点）",
            sum(n for _, n in tt_sites) == 6
            and sorted(os.path.basename(x) for x, _ in tt_sites) == ["config.py", "intent.py", "pending.py"],
            str(tt_sites))

    c.check("A8 调用点走 `clock.now()` 这种**属性访问**，不许 `from app.clock import now`"
            "（直接绑名字，测试/运行时替换不进去 —— 同 `main.py` 里那条'函数内导入'的老道理）",
            "from app.clock import" not in _read(MAIN) + _read(STORE) + _read(PLANNER), "")
    c.check("A9 `planner` 的 `import datetime` 还在（它还要 `timedelta`/`fromisoformat`，不是漏删）",
            "import datetime" in _read(PLANNER).split("def ")[0], "")
    return c


def store_now_shape():
    from app import store
    return store._now()


def _slice_fn(path, anchor):
    src = _read(path)
    i = src.index(anchor)
    j = src.find("\n\n", i)
    return src[i:j if j != -1 else len(src)]


def batch_b_tz_actually_used():
    c = Checker()
    title("B. #56-B：证明 `clock.now()` 真按 `_TZ` 算（本机 +8，光跑本机等于没验）")
    import app.clock as clock
    from app.main import _age_hours, _age_text, _now_block

    base = clock.now()
    for off, label in ((-5, "西五区"), (0, "UTC")):
        clock._TZ = datetime.timezone(datetime.timedelta(hours=off), "PROBE")
        try:
            moved = clock.now()
            delta_h = (base - moved).total_seconds() / 3600.0
            want = 8 - off
            c.check(f"B1 把 `_TZ` 换成 {label}（UTC{off:+d}），`clock.now()` 必须跟着差 {want} 小时"
                    f"（改坏成 `datetime.now()` 就一动不动 —— M1 的牙）",
                    abs(delta_h - want) < 0.02, f"实测差 {delta_h:.2f} 小时")
        finally:
            clock._TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")

    c.check("B2 还原之后与系统墙上时钟一致（本机就是 +8，偏差该在秒级）",
            abs((clock.now() - datetime.datetime.now()).total_seconds()) < 2, "")
    hour_local = int(base.strftime("%H"))
    clock._TZ = datetime.timezone(datetime.timedelta(hours=-5), "PROBE")
    try:
        moved_block = _now_block()
        hour_moved = int(re.search(r"\d{2}:\d{2}", moved_block).group()[:2])
    finally:
        clock._TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")
    c.check("B3 给模型那块「现在的真实时间」也跟着 `_TZ`（否则模型看到的'今天'与写库的'今天'是两个钟）",
            hour_moved != hour_local, f"本机 {hour_local:02d} 点 / 换 TZ 后 {hour_moved:02d} 点")
    c.check("B3-bis 还原后那块又回到本机钟点",
            int(re.search(r"\d{2}:\d{2}", _now_block()).group()[:2]) == hour_local, "")

    block = _now_block()
    c.check("B4 给模型那块提示的**格式**没变（`【现在的真实时间】YYYY-MM-DD 周X HH:MM` —— 改了模型那边对不上）",
            block.startswith("\n\n【现在的真实时间】")
            and re.search(r"【现在的真实时间】\d{4}-\d{2}-\d{2} 周[一二三四五六日天] \d{2}:\d{2}", block)
            is not None, block[:48].replace("\n", ""))

    ts = (clock.now() - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    c.check("B5 naive 减 naive 不 TypeError（`main._age_hours` 那条减法是全项目用 ts 的形状）",
            _age_hours(ts) == 3.0 and _age_text(_age_hours(ts)) == "3 小时", _age_hours(ts))
    c.check("B5-bis 对照：将来与垃圾串照旧回 None（宁可不显示，也不显示一个猜出来的数）",
            _age_hours((clock.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")) is None
            and _age_hours("不是时间") is None and _age_hours("") is None, "")
    from app.modules import express
    c.check("B6 express 那个 `today() - arrived` 的天数差照旧（两个 date 相减，同样要求 naive）",
            express._days_since(clock.today().isoformat()) == 0
            and express._days_since((clock.today() - datetime.timedelta(days=2)).isoformat()) == 2, "")
    return c


def batch_c_js_gate():
    c = Checker()
    title("C. #56-B：JS 语法闸 —— 不写死清单，且真的能报出坏页面")
    gate_src = _read(JS_GATE)
    run_src = _read(RUN_ALL)

    c.check("C1 闸里**没有写死的文件清单**（'只查 confirm + student 两页'就是这个病根）",
            'const files = ["app/static/confirm.html"' not in gate_src
            and "readdirSync" in gate_src, "")
    c.check("C2 扫的是目录本身（`.html` 全收），以后加页面自动被覆盖",
            'endsWith(".html")' in gate_src and 'endsWith(".js")' in gate_src, "")
    c.check("C3 外链脚本也在闸里（`esc.js` 是全站唯一的转义函数，它坏了每个页面都停摆）—— "
            "这条不查源码字样，交给下面 C9-bis 用真文件验",
            "jss" in gate_src, "")
    c.check("C4 一个文件都没扫到 ⇒ `exit 2` 说'这次没验到任何东西'（零个文件当通过，比红更危险）",
            "process.exit(2)" in gate_src and gate_src.count("process.exit(2)") >= 2,
            "exit(2) 出现 %d 次" % gate_src.count("process.exit(2)"))
    c.check("C5 `run_all.py` 开头调用闸，且失败计入总账 + 记进 failed_files（M3 的牙）",
            "run_js_gate()" in run_src and "js_fail" in run_src
            and re.search(r"total_pass, total_fail = js_pass, js_fail", run_src) is not None
            and re.search(r"if js_fail:\s*\n\s*failed_files\.append", run_src) is not None, "")
    c.check("C6 没装 node ⇒ 打印跳过并返回 (0, 0)：**不判失败**（评委机器可能只有 Python），"
            "但屏幕上必须写明'没有验'",
            re.search(r'if not node:\s*\n\s*print\("⚠️[^"]*没有验', run_src) is not None
            and re.search(r"if not node:[\s\S]{0,160}return 0, 0", run_src) is not None, "")
    c.check("C7 闸那份人话输出**不进**下面抠数字的循环（否则'语法 OK'那些行会被当成 0 项或乱加）",
            run_src.index("js_pass, js_fail = run_js_gate()") < run_src.index("for path in files:")
            and "项，失败" not in gate_src, "")

    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        c.check("C8 这台机器没有 node：行为三条**跳过**（静态形状 C1~C7 照旧全过才算这批评过）",
                True, "跳过 —— 报告里要写明这三条没验")
        return c

    # 行为一：坏引号必须被点名（把 `station.html` 那一页放进**临时目录**，绝不碰真页面）
    tmp = tempfile.mkdtemp(prefix="js56-")
    try:
        os.makedirs(os.path.join(tmp, "app", "static"))
        with open(os.path.join(tmp, "app", "static", "station.html"), "w", encoding="utf-8") as f:
            f.write("<html><script>\nvar a = '没关的引号;\nconsole.log(a);\n</script></html>")
        r = subprocess.run([node, JS_GATE], cwd=tmp, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        c.check("C9 真造一个坏引号的 station.html ⇒ 闸必须 rc≠0 **并点名是哪个文件**"
                "（写死两页清单的话这一条根本看不见 station.html）",
                r.returncode == 1 and "station.html" in out and "语法错误" in out,
                out.strip()[:120])

        # 行为一-bis：坏的是**外链 .js**（不是页面内联）—— 只扫 .html 的闸会放过它
        with open(os.path.join(tmp, "app", "static", "station.html"), "w", encoding="utf-8") as f:
            f.write("<html><script>var a = 1;</script></html>")
        with open(os.path.join(tmp, "app", "static", "esc.js"), "w", encoding="utf-8") as f:
            f.write("function broken( {\n  return 1\n")
        rb = subprocess.run([node, JS_GATE], cwd=tmp, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        outb = (rb.stdout or "") + (rb.stderr or "")
        c.check("C9-bis 坏的是 esc.js（外链本体）⇒ 闸也要 rc≠0 并点名 esc.js —— "
                "它坏了是**每个页面**的渲染停摆，而页面自己的内联 script 语法全对",
                rb.returncode == 1 and "esc.js" in outb and "语法错误" in outb,
                outb.strip()[:120])
        os.makedirs(os.path.join(tmp, "app2", "static"))
        with open(os.path.join(tmp, "app", "static", "station.html"), "w", encoding="utf-8") as f:
            f.write("<html><script>var a = 1;</script></html>")
        tmp2 = os.path.join(tmp, "empty")
        os.makedirs(os.path.join(tmp2, "app", "static"))
        r2 = subprocess.run([node, JS_GATE], cwd=tmp2, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        c.check("C10 空目录 ⇒ rc=2 并说'没有验到任何东西'（零个文件不许当通过）",
                r2.returncode == 2 and "没有验到" in ((r2.stdout or "") + (r2.stderr or "")),
                ((r2.stdout or "") + (r2.stderr or "")).strip()[:90])

        r3 = subprocess.run([node, JS_GATE], cwd=PROJ, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        out3 = (r3.stdout or "") + (r3.stderr or "")
        pages = len(glob.glob(os.path.join(STATIC, "*.html")))
        scripts = len(glob.glob(os.path.join(STATIC, "*.js")))
        c.check(f"C11 真实页面全过，且覆盖数就是当前的页数**与外链脚本数**（本机 {pages} 页 + {scripts} 个 .js）",
                r3.returncode == 0
                and ("覆盖 " + str(pages) + " 个页面 + " + str(scripts) + " 个外链脚本") in out3,
                out3.strip().splitlines()[-1][:110])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return c


def batch_d_dates_and_start_state():
    c = Checker()
    title("D. #56-C/D：写死'将来'的日期清零 + 起始态钉住（反向也要钉：①类不许被顺手改）")

    bad, fixed_ish = [], []
    for p in sorted(glob.glob(os.path.join(PROJ, "tests", "test_*.py"))):
        for lineno, names, val in _date_assignments(p):
            if any(FUTUREISH.search(n or "") for n in names):
                bad.append("%s:%d %s=%r" % (os.path.basename(p), lineno, names, val))
    c.check("D1 ③类清零：测试里不许再有「名字说它是将来」的写死日期（AST 扫赋值，注释与 docstring 不算）",
            not bad, "、".join(bad[:4]))

    src60 = _read(os.path.join(PROJ, "tests", "test_60_card_close.py"))
    src61 = _read(os.path.join(PROJ, "tests", "test_61_surface_boundary.py"))
    c.check("D2 那两颗现在真的是 `today + N` 现算（test_60 / test_61）",
            "FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()" in src60
            and "FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()" in src61,
            "")

    c.check("D3 反向钉：①类（当输入喂给 store/解析器的固定日子）**保持写死** —— "
            "那些断言今天几号都成立，改成现算只是把它们拖上时钟（施工单 §六.3）",
            '_D = "2026-10-03"' in _read(os.path.join(PROJ, "tests", "test_13_guess_evidence.py"))
            and 'd = "2026-10-03"' in _read(os.path.join(PROJ, "tests", "test_12_batch_todo.py")), "")

    src39 = _read(TEST39)
    live = [ln.strip() for ln in src39.splitlines()
            if "wipe_todos()" in ln and not ln.strip().startswith("#")]
    c.check("D4 test_39 那一行 `wipe_todos()` 是**活代码**（写成 `// #` 注释掉不算钉住 —— 红线 4c 那一族）",
            len(live) == 1, str(live)[:80])
    i = src39.index("seed_todos([{\"title\": \"起点探针\"")
    c.check("D5 而且是「先种再清、两头都验」：先证明探针读得到，再证明清得干净 —— "
            "只写清空 + 断言空，在这台本来没有匿名待办的机器上是白过的（M4 的牙）",
            "seed_todos" in src39[i:i + 400] and "起点探针" in src39[i:i + 400]
            and src39.index("wipe_todos()", i) > i, "")
    c.check("D6 `pin_now` 还能打桩（它是④类唯一的解法：钉住钟点再断言'今晚该出卡 / 排不下'）",
            "planner._now_minutes" in _read(os.path.join(PROJ, "tests", "_harness.py")), "")

    with sandbox():
        from app import store as S
        n_before = len(S.list_todos())
        from _harness import seed_todos, wipe_todos
        seed_todos([{"title": "形状探针", "date": "2099-01-04", "start": "09:00", "end": "10:00"}])
        after_seed = [t["title"] for t in S.list_todos()]
        wipe_todos()
        c.check("D7 helper 本身仍然可信（种得进、读得到、清得干净）—— "
                "test_39 与这批都靠它，它悄悄失效就是整族'起始态'白钉",
                after_seed == ["形状探针"] and S.list_todos() == [],
                f"沙箱继承到的 {n_before} 条 → 种完 {after_seed} → 清完 {len(S.list_todos())} 条")
    return c


BATCHES = [("A 批（取时间只有一个入口）", batch_a_clock_shape),
           ("B 批（时区真的被用上）", batch_b_tz_actually_used),
           ("C 批（JS 语法闸）", batch_c_js_gate),
           ("D 批（日期与起始态）", batch_d_dates_and_start_state)]


def main():
    global code
    print("test_77_clock_and_gate：#56 时区收口 / JS 闸并进收口 / #69 日期清零 / test_39 起始态")
    print("=" * 60)
    # ⚠️ 别写成 `code = code or fn()...`（`or` 短路：第一批一红后面几批根本不跑，见 test_76 同款教训）
    rc = 0
    for name, fn in BATCHES:
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
