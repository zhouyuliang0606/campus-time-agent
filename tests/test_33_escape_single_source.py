"""转义只有一份，而且那份要管得住属性位置（test_33）。

**守的是什么**：2026-10-08 核查 P0-3 时发现全站有 **7 份**各自抄的转义实现
（admin/settings/station/student 各一份 `escapeHtml`，confirm/plan 各一份 `esc`，
student 还另有一份 `escQ` 专补双引号）。其中 5 份只转 `& < >`、**不转引号** ——
而 `${x}` 塞进 `title="…"`、`value="…"`、`data-key="…"` 这种属性位置时，
不转引号就等于把属性值的右引号提前关掉，后面接 `onmouseover=…` 就成了事件处理器。
更糟的是"哪份管哪块"没人说得清，于是同一个页面里既有转了的点也有裸点：
`student.html` 的 `addMsg(who, …, module)` 就是裸的，而 `module` 来自后端返回。

**这份护栏分成两种强度，别混起来看**：
- A/C/D 是**结构与点位**断言（读源码）。它们能钉住"别抄第二份""别把这几个点改回裸的"。
- B 是**语义**断言：**用 node 真的执行 app/static/esc.js**，拿真返回值判。
  为什么不照抄一份 Python 实现来对比？照抄的那份本身就可能抄错，
  那是"用一个会说谎的工具去验另一个"（同一个坑在 test_30 里踩过：
  那里是在子进程里调 pip 自己的 `auto_decode()`，而不是复刻解码逻辑）。
  本机没有 node 时 B 批**明确打印是退回方案**（只核对替换表覆盖），不假装真跑过。

⚠️ E 批的扫描是"新裸点会红"，不是"证明没有裸点"：
判据是**HTML 上下文形状**（`="…${x}…"` 与 `>…${x}…<`），命中的每一条都必须在
`ALLOWED_RAW` 里写下理由。它抓不到的是"把数据拼进 JS 字符串"那种位置 ——
那属于 D 批的范围，两批合起来才盖住属性 + 内联脚本两种上下文。
"""
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import PROJ_DIR, Checker, title  # noqa: E402

STATIC = os.path.join(PROJ_DIR, "app", "static")
ESC_JS = os.path.join(STATIC, "esc.js")

PAGES = ["admin.html", "confirm.html", "plan.html", "settings.html",
         "station.html", "student.html"]


def read(name):
    return io.open(os.path.join(STATIC, name), encoding="utf-8").read()


# ============ B 批：真跑 esc.js ============

def run_esc(payloads):
    """用 node 执行**那份真实文件**，返回 esc(payload) 的字符串列表。

    页面里是浏览器跑的，这里能拿到的最接近替身就是 node：esc.js 是
    `(function(global){…})(window)`，给它一个假 window 就能原样跑出来。
    """
    js = (
        "const fs=require('fs');"
        "const window={};"
        "eval(fs.readFileSync(process.argv[1],'utf8'));"
        "const out=JSON.parse(process.argv[2]).map(v=>window.esc(v));"
        "process.stdout.write(JSON.stringify(out));"
    )
    r = subprocess.run(["node", "-e", js, ESC_JS, json.dumps(payloads)],
                       capture_output=True, text=True, timeout=60, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:400])
    return json.loads(r.stdout)


def batch_semantics(ck):
    title("B 批 · 转义语义（B 批是**执行** esc.js，不是读它的源码）")
    payloads = [
        '<svg onload="window.__x=1">',
        '"><img src=x onerror=alert(1)>',
        """a'b"c&d<e>f""",
        "&amp;<script>",
        None,
    ]
    node = shutil.which("node")
    if not node:
        # 退回方案：只核对替换表覆盖 5 个字符。这**不是**行为验证，所以照实说。
        src = read_js(ESC_JS)
        table = set(re.findall(r'["\']([<&>"\'-])["\']\s*:\s*["\']&', src))
        ck.check("B1（退回表核对）替换表覆盖 & < > \" '",
                 {"&", "<", ">", '"', "'"} <= table,
                 "｜本机没装 node，只核对了转义表、没真跑 esc.js")
        ck.check("B2（退回表核对）null 走空串", "s == null ? \"\" : s" in src)
        return
    out = run_esc(payloads)
    # 光看"危险字符没了"是不够的：把表里某条删掉，JS 会拼出字符串 "undefined"，
    # 字符确实不见了，但转义器已经坏了 —— 反向验证 M1 就是这么撞出来的。
    # 所以 B1 同时要求"没残留裸字符"和"没吐出 undefined/null 这种残骸"。
    bad = [o for o in out if re.search(r"""[<>"']""", o) or re.search(r"undefined|null", o)]
    ck.check("B1 五种危险字符全部被转义（5 个 payload 无一残留裸字符，也没 undefined 残骸）",
             not bad, "｜残留：" + repr(bad[:1]))
    ck.check("B2 双引号与单引号都转成了实体（旧 5 份实现漏的就是这两个）",
             "&quot;" in out[2] and "&#39;" in out[2], "｜" + out[2])
    ck.check("B3 转义后 HTML 解码能还原原样（没多转、没漏转、没删字符）",
             all(html.unescape(o) == p for o, p in zip(out[:4], payloads[:4])))
    ck.check("B4 null/undefined 转成空串，不是打印出 'null'", out[4] == "",
             "｜got " + repr(out[4]))
    ck.check("B5 & 也被转义（否则 &lt; 这类实体会被二次解码）",
             out[3].startswith("&amp;"), "｜" + out[3])


def read_js(path):
    return io.open(path, encoding="utf-8").read()


# ============ A 批：唯一实现 ============

DEF_PATTERNS = [
    r"function\s+escapeHtml\s*\(",
    r"function\s+esc\s*\(",
    r"const\s+escapeHtml\s*=",
    r"const\s+escQ\s*=",
    r"const\s+esc\s*=\s*\(?s\)?\s*=>",
    r"\.replace\(/\[&<>]",
]


def batch_single_source(ck):
    title("A 批 · 全站只有这一份转义实现")
    src = read_js(ESC_JS)
    ck.check("A1 esc.js 存在且把 esc 挂到 window", "global.esc = esc" in src)
    for name in ("escapeHtml", "escQ"):
        hits = [f for f in PAGES if re.search(r"\b" + name + r"\s*\(", read(f))]
        ck.check(f"A2 页面里不再出现 {name}( 调用（历史名字清零）", not hits,
                 "｜还在用：" + ",".join(hits))
    reimplemented = []
    for f in PAGES:
        s = read(f)
        for pat in DEF_PATTERNS:
            if re.search(pat, s):
                reimplemented.append(f + " «" + pat + "»")
    ck.check("A3 六个页面里没有任何一份本地转义实现", not reimplemented,
             "｜发现：" + "; ".join(reimplemented))
    ck.check("A4 页面里不再残留旧实现的替换表写法（/[&<>] 那种）",
             not any(".replace(/[&<>]" in read(f) for f in PAGES))
    missing = [f for f in PAGES if read(f).count('src="/static/esc.js"') != 1]
    ck.check("A5 每个页面恰好引入一次 /static/esc.js", not missing,
             "｜不对：" + ",".join(missing))
    late = []
    for f in PAGES:
        s = read(f)
        if s.find('src="/static/esc.js"') > s.find("<script>"):
            late.append(f)
    ck.check("A6 引入位置在自己的内联脚本**之前**（否则一进来就 esc is not defined）",
             not late, "｜晚了：" + ",".join(late))
    # login.html 没有内联脚本，也不渲染任何外部数据 → 不该被"每页都要引入"误伤
    ck.check("A7 login.html 不引入 esc.js（它没有内联脚本，规则不误伤）",
             'src="/static/esc.js"' not in read("login.html"))


# ============ C 批：审计点位回归 ============

# 每一条都是 2026-10-08 审计里**真的漏过**的点，写成"现在必须长这样"。
# 判据用"该点位当前以 esc(...)/encodeURIComponent(...) 包裹"的精确子串，
# 所以有人改回裸插值当场就红，报的也正是那一条。
REQUIRED = [
    ("student.html", "${esc(who)}", "聊天泡的 who（昵称来自 localStorage）"),
    ("student.html", "${esc(module)}", "聊天泡的 module 标签（**来自后端返回**）"),
    ("student.html", "'+esc(p.key)+'", "设置页性格卡片的 data-key（属性位置，来自接口）"),
    ("student.html", "${esc(p.label)}", "快递状态标签（接口数据，旧版裸进 innerHTML）"),
    ("student.html", "${esc(p.arrived_at)}", "快递到件时间（接口数据）"),
    ("student.html", "${esc(o.eta)}", "外卖预计时间（接口数据）"),
    ("student.html", "${esc(w.ts)}", "工单时间戳（接口数据）"),
    ("student.html", "${esc(n.ts)}", "通知时间戳（接口数据）"),
    ("student.html", "${esc(x.tm)}", "周表里的时间段（由课程起止时间拼出来）"),
    ("student.html", "${esc(t.start)}-${esc(t.end)}", "某天待办的起止时间（接口数据）"),
    ("station.html", "${esc(who)}", "驿站页聊天泡 who"),
    ("station.html", "${esc(module)}", "驿站页 module 标签（后端返回）"),
    ("settings.html", "${esc(p.emoji || \"\")}", "性格卡片 emoji（接口数据）"),
    ("admin.html", "${esc(name)}", "收件箱模块名（兜底会回落到原始 module 串）"),
    ("admin.html", "${esc(m.ts)}", "收件箱时间戳"),
    ("admin.html", "${esc(w.ts)}", "工单时间戳"),
    ("admin.html", "${esc(n.ts)}", "通知时间戳"),
    ("plan.html", "${esc(t.start)} - ${esc(t.end)}", "当天待办起止时间"),
    ("plan.html", "${esc(it.tm)}", "周表块的时间段"),
    ("student.html", "encodeURIComponent(n.attachment.id)", "通知附件下载链接的 id"),
    ("admin.html", "encodeURIComponent(b.dataset.wo)", "工单状态接口的 id 拼路径"),
]


def func_body(src, head):
    """从 `function addMsg(` 起截到它自己那一段结束（下一个独占一行的 `}`）。

    为什么不用定长窗口：addMsg 里塞着好几段中文注释，400 字根本盖不到
    `el.innerHTML = html`，测试就会因为"没截到"而红 —— 那是测试自己的 bug，
    不是代码的问题。
    """
    i = src.find(head)
    if i < 0:
        return ""
    j = re.search(r"\n    \}\n", src[i:])
    return src[i:i + j.start()] if j else src[i:i + 3000]


def batch_pinned_sites(ck):
    title("C 批 · 审计查出的点位，逐个钉住（改回裸插值当场红）")
    bad = []
    for f, needle, why in REQUIRED:
        if needle not in read(f):
            bad.append(f + " 缺 " + needle + "（" + why + "）")
    ck.check(f"C1 {len(REQUIRED)} 个曾裸插值的点位现在都带转义", not bad,
             "｜回退：" + "; ".join(bad[:3]))
    # 后端返回的 module 是唯一一条"学生侧写、管理员侧读"的通道，值得单独一条
    body = func_body(read("student.html"), "function addMsg(")
    ck.check("C2 student 的 addMsg 整段里没有任何裸 ${who}/${module} 残留",
             bool(body) and "${who}" not in body and "${module}" not in body
             and "el.innerHTML" in body)
    body2 = func_body(read("station.html"), "function addMsg(")
    ck.check("C3 station 的 addMsg 同上",
             bool(body2) and "${who}" not in body2 and "${module}" not in body2
             and "el.innerHTML" in body2)


# ============ D 批：内联 JS 上下文红线 ============

# `onclick="fn('${x}')"` 这种位置，esc() **救不了**：属性值先被 HTML 解码
# （&#39; → '）再交给 JS 解析，所以转义过的引号照样能把 JS 字符串关掉。
# 唯一的解法是不把数据写进内联脚本。这里允许剩下的 onclick 只带**循环下标**
# （纯数字，不是外部数据）。
ONCLICK = re.compile(r'onclick="([^"]*)"')
ALLOWED_ONCLICK_EXPR = re.compile(r"^[a-z]$|^\d+$")


def batch_inline_js(ck):
    title("D 批 · 内联脚本位置不装数据（转义在这里帮不上忙）")
    offenders = []
    for f in PAGES:
        for m in ONCLICK.finditer(read(f)):
            body = m.group(1)
            for e in re.findall(r"\$\{([^{}]*)\}", body):
                if not ALLOWED_ONCLICK_EXPR.match(e.strip()):
                    offenders.append(f + ": " + e.strip()[:40])
    ck.check("D1 onclick 里的插值只有循环下标（不是数据）", not offenders,
             "｜越界：" + "; ".join(offenders[:3]))
    plan = read("plan.html")
    ck.check("D2 plan.html 的待办按钮改成了 data-* + 事件委托",
             'data-act="toggle"' in plan and "querySelectorAll(\"button[data-act]\")" in plan)
    ck.check("D3 待办 id 拼进 URL 前一律 encodeURIComponent",
             '"/api/todos/" + encodeURIComponent(id)' in plan
             and '"/api/todos/" + id' not in plan)
    st = read("student.html")
    ck.check("D4 student 待办勾选/删除按钮的 id 先 encode 再拼 URL",
             '"/api/todos/"+encodeURIComponent(id)' in st
             and 'decodeURIComponent(cb.dataset.id)' not in st)
    ck.check("D5 richText 是**先转义再还原加粗**（顺序反了等于自己开 XSS 口子）",
             bool(re.search(r"return esc\(s\)\.replace\(/\\*\\*", read("student.html"))))


# ============ E 批：HTML 上下文扫描（新裸点会红） ============

ATTR = re.compile(r'=\s*"?\$\{([^"]{1,140}?)\}"')
ATTR_CONCAT = re.compile(r'="\s*\+\s*([^+;]{1,90}?)\s*\+\s*"')
TEXT = re.compile(r'>\s*\$\{([^<]{1,140}?)\}\s*<')
# 命中即认为"这个表达式自己带了转义/只产数字"
SAFE = re.compile(r"\b(esc|richText|_num|Number|String|toFixed|encodeURIComponent|Math\.max)\s*\(")

# 审计里逐条看过、判定"不是外部数据"的点。加新条目必须写理由，
# 而且**宁可写窄**：漏写理由的那条会让扫描当场红，比默默放行好。
ALLOWED_RAW = {
    # 自己上一行拼好的 HTML 片段（内部每一段都已转义）
    "att": "上一行自己拼的通知附件 <a>，内部已 esc",
    "why": "上一行自己拼的说明行，内部已 esc",
    "rows": "批量确认条的逐行 HTML，内部已 esc",
    "html": "showDone 的正文由 doneText 拼，只含数字与 <b>",
    "list": "候选时段的 label 列表，内部已 esc",
    "inner": "月历/周列表格子里先拼好的 HTML，内部已 esc",
    "title": "tooltip 文本，上一行已 esc(...)",
    # 页面自己的常量表
    "m.ico": "MODULES 是页面内写死的图标常量",
    "m.name": "MODULES 是页面内写死的模块名常量",
    "w": "WD = ['周一'…'周日'] 页面常量",
    "t.cls || \"\"": "用量格子 class，来自本文件 tiles 字面量",
    "t.n": "tiles 里的值，全部过 _num()",
    "t.k": "tiles 里写死的中文标签",
    # 数字/日期片段
    "i": "map 的下标",
    "n": "计数",
    "cs.length": "数组长度",
    "dIso": "iso()/isoD() 自己算出来的 YYYY-MM-DD",
    # 两侧都是字面量的三元
    "d.configured ? \"✅ 已配置\" : \"⚠️ 尚未配置密钥\"": "两个中文字面量",
    "w.source === \"panel\" ? \"面板表单\" : \"聊天确认\"": "两个字面量",
    "r.status === \"ok\" ? \"成功\" : \"失败\"": "两个字面量",
    # 2026-10-10 每周重复改版：plan.html 的下钻行先把完成状态算进局部变量 done，
    # 所以裸插值从 `t.status === "done" ? …` 换成了 `done ? …` —— 两条旧条目按 E2 的规矩删掉，
    # 换进来的是同样"只有字面量"的新写法。
    "done ? \"done\" : \"\"": "class 名字面量",
    "done ? \"撤销本周完成\" : \"标记本周完成\"": "按钮文案字面量",
    "t.repeat === \"weekly\" ? \"🔁 \" : \"\"": "一个图标字面量，不含数据",
    "x.clash ? \" clash\" : \"\"": "class 名",
}
DATE_MATH = re.compile(r"^\{?d\.get(Month|Date)\(\) \+? ?1?\}$")


def allowed(expr):
    e = expr.strip()
    if e in ALLOWED_RAW:
        return True
    if re.fullmatch(r"[\w.$\[\]\+\-*/() ]*", e) and re.search(r"\.length$|- ?\d+$", e):
        return True          # 数组长度、length 减常量
    if e.startswith("WD[") or e.startswith("MODULES["):
        return True          # 页面常量表
    if "getMonth()" in e or "getDate()" in e:
        return True          # Date 取月/日，只会是数字
    if re.fullmatch(r"\(r && r\.\w+\) \|\| [\w.() ]*\.length", e):
        return True          # 接口返回的计数
    if re.fullmatch(r"\(p\.courses \|\| \[\]\)\.length", e):
        return True
    if "(b.items || []).length" in e or "items.length" in e:
        return True
    if e.endswith(".length"):
        return True
    return False


def batch_scan(ck):
    title("E 批 · 属性/文本上下文里的裸插值（新出现的一律红）")
    fresh = []
    for f in PAGES + ["login.html"]:
        s = read(f)
        for rx in (ATTR, ATTR_CONCAT, TEXT):
            for m in rx.finditer(s):
                e = m.group(1).strip()
                if SAFE.search(e) or allowed(e):
                    continue
                fresh.append(f + ": " + e[:50])
    ck.check("E1 属性/文本位置的裸插值只剩白名单里那些（每条都有理由）",
             not fresh, "｜新增未审：" + "; ".join(fresh[:4]))
    # 白名单本身也不能烂掉：写一条没人核对的"豁免"比不加更危险
    src_all = "".join(read(f) for f in PAGES)
    dead = [k for k in ALLOWED_RAW if k not in src_all]
    ck.check("E2 白名单没有攒下用不到的条目（条目失效就该删）",
             len(dead) <= 3, "｜已消失：" + ", ".join(dead[:5]))
    ck.check("E3 危险字符在属性位置的历史写法（[&<>] 少引号）不再出现",
             not any(re.search(r"replace\(/\[&<>\]/", read(f)) for f in PAGES))


def main():
    ck = Checker()
    batch_single_source(ck)
    batch_semantics(ck)
    batch_pinned_sites(ck)
    batch_inline_js(ck)
    batch_scan(ck)
    return ck.summary("test_33 转义只有一份")


if __name__ == "__main__":
    sys.exit(main())
