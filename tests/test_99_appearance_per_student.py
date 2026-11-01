# -*- coding: utf-8 -*-
"""#98 的护栏：学生端外观偏好按学号分开（同一台机器换账号，各看各的背景）。

现场：A 把背景改成深空，退出登录换 B 登录 —— B 也是深空。
根因不在服务端：localStorage 是**浏览器级**的，`ct_bg` / `ct_gx_*` 早先一律写裸键，
于是第二位继承上一位。本单把外观键收进 `ct_bg@<学号>`，并把老裸键认领给第一位打开它的学号。

三层判据：
  A 批（student.html 静态）：键名拼学号、读写都过 gxKey、老键认领后删掉、
     whoami **请求成功**才复套（把「请求挂了」当「没身份」会把背景打回默认、还会认错下一位的键）。
  B 批（plan.html / login.html 静态）：/plan 读同一套键并同样认领；三个页面都不许整片
     `localStorage.clear()`（外观偏好已按学号存，整片清会连别的偏好一起弄丢）。
  C 批（node 行为）：把页面里的这两段代码**现抽出来**，在假 localStorage 下跑一遍。
     静态写法对、行为不对（比如 gxKey 写了但 gxVal 不调它）也能抓住。

⚠️ C 批抽的是**当前工作副本**（不是 git 里的版本），所以 A/B 写法的变异也会打到 C 的行为上。
⚠️ C 批抽出来单独 eval —— **看不见作用域**：一段代码引用另一段里的名字（跨 IIFE 直呼）
   在这里照样能跑，在浏览器里却是 ReferenceError（#98 实机就是这么翻的）。那条由 A11b
   的括号配平判据兜着，C 批不冒充它。
⚠️ 没有 node 时 C 批打印跳过说明并返回 0（那批不计入 run_all 的总账）。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker    # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

BARE_SET = re.compile(r"setItem\(\s*['\"](?:ct_bg|ct_gx_[a-z]+)['\"]")
BARE_GET = re.compile(r"getItem\(\s*['\"]ct_bg['\"]\s*\)")


def read(rel):
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read().replace("\r\n", "\n")


def _fn_body(src, header):
    """按花括号配平取函数体（不解析字符串；这几个函数里没有含花括号的字符串）。"""
    i = src.find(header)
    if i < 0:
        return ""
    j = src.find("{", i)
    depth = 0
    for k in range(j, len(src)):
        ch = src[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[j:k + 1]
    return ""


def _slice(src, start, end, keep_end=True):
    i = src.find(start)
    if i < 0:
        raise ValueError("找不到起点：" + start)
    j = src.find(end, i)
    if j < 0:
        raise ValueError("找不到终点：" + end)
    return src[i:j + len(end)] if keep_end else src[i:j]


SHELL_MARK = "// ================= 三栏外壳交互（新增） ================="


def _shell_span(src):
    """三栏外壳那层 IIFE 的 {...} 区间（起点取它之后第一个 `{`）。

    括号配平是**朴素版**（不跳字符串/注释）：这个文件的字符串里没有落单的花括号，
    真出现漂移时 `_stray_gxset_calls` 的尾部校验（必须紧跟 `)();`）会把它暴露成红，
    而不是悄悄算错范围。
    """
    i = src.find(SHELL_MARK)
    if i < 0:
        return None
    j = src.find("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return (j, k)
    return None


def _stray_gxset_calls(src):
    """外壳**之外**的 `gxSetSid(` 次数；`window.ctGxSetSid(` 不算（词边界挡掉）。

    返回 None 表示连壳都没认出来（判据要按红处理，不能当 0）。
    """
    sp = _shell_span(src)
    if sp is None or src[sp[1] + 1:sp[1] + 5] != ")();":
        return None
    return sum(1 for m in re.finditer(r"(?<![\w.$])gxSetSid\(", src)
               if not (sp[0] < m.start() < sp[1]))


def batch_a_student():
    c = Checker()
    h = read("app/static/student.html")

    c.check("A0 #98 的说明块还在（键名带学号这件事在代码里留了话）",
            "// ---------- 外观偏好按学号分开（#98） ----------" in h)
    c.check("A1 键名函数只这一份，而且真的拼了 `@学号`（没学号才用裸键）",
            h.count("function gxKey(k){ return gxSid ? (k + '@' + gxSid) : k; }") == 1,
            str(h.count("return gxSid ? (k + '@' + gxSid) : k;")))
    c.check("A2 首屏同步取学号：`campustime_student` 在脚本里现读一次（不等 whoami，首屏就能套对）",
            "gxSid = localStorage.getItem('campustime_student') || '';" in h)
    c.check("A3 老键认领在顶层就跑（不是等某个事件）：`gxAdopt();` 独占一行恰一处",
            len(re.findall(r"\n    gxAdopt\(\);\n", h)) == 1,
            str(len(re.findall(r"\n    gxAdopt\(\);\n", h))))

    c.check("A4 读走的都是分键：gxVal 用 getItem(gxKey(k))，且页面里没有一处 getItem('ct_bg'/'ct_gx_…') 裸读",
            h.count("function gxVal(k, d){ const v = localStorage.getItem(gxKey(k));") == 1
            and not BARE_GET.search(h),
            "gxVal=%d bare_get=%d" % (h.count("localStorage.getItem(gxKey(k))"), len(BARE_GET.findall(h))))
    c.check("A5 写也走分键：页面里没有一处 setItem('ct_bg'/'ct_gx_…') 的裸键写法",
            not BARE_SET.search(h), str(len(BARE_SET.findall(h))))
    c.check("A5正控 同一支正则对一句故意的裸写必须命中（否则 A5 是空判据）",
            bool(BARE_SET.search("localStorage.setItem('ct_bg','deep');")))

    c.check("A6 换图清旋钮那支：五个键的清单还在（test_44 钉的字面量不动），删除改走 gxKey",
            "['ct_gx_scrim','ct_gx_vm','ct_gx_bd','ct_gx_core','ct_gx_fx']"
            ".forEach(function(k){ localStorage.removeItem(gxKey(k)); })" in h)

    adopt = _fn_body(h, "function gxAdopt(){")
    c.check("A7 认领只认「第一位」：自己已经有分键就不覆盖（`=== null` 才写）",
            "if (localStorage.getItem(k + '@' + gxSid) === null) localStorage.setItem(k + '@' + gxSid, legacy);" in adopt)
    c.check("A8 认领完必须删裸键（不删的话下一位没有分键时会回落到上一位那份）",
            adopt.count("localStorage.removeItem(k);") == 1,
            str(adopt.count("localStorage.removeItem(k);")))
    c.check("A9 没有学号（演示公共库）不搬：函数体第一句就是 `if (!gxSid) return;`",
            adopt[1:].strip().startswith("if (!gxSid) return;"))

    ident = _fn_body(h, "async function initStudentIdentity() {")
    kpos = ident.find("known = true;")
    cpos = ident.find('catch (e) { sid = ""; }')
    c.check("A10 只有请求成功才认：`known = true` 恰一处，落在 try 里、不在 catch 里",
            ident.count("known = true;") == 1 and kpos >= 0 and cpos >= 0 and kpos < cpos,
            "known=%d kpos=%d cpos=%d" % (ident.count("known = true;"), kpos, cpos))
    gpos = ident.find("if (known) {")
    gend = ident.find("\n      }\n", gpos)
    inside = ident[gpos:gend] if gpos >= 0 and gend > gpos else ""
    c.check("A11 whoami 回来才复套：换键只走窗口钩子 `window.ctGxSetSid(sid)`，恰一处，"
            "且在 `if (known) {` 的块里（把请求失败当没身份 = 背景被打回默认 + 认错下一位的键）",
            ident.count("window.ctGxSetSid(sid)") == 1
            and "window.ctGxSetSid(sid)" in inside,
            "count=%d" % ident.count("window.ctGxSetSid(sid)"))
    # #98 实机回归：`gxSetSid` 定义在三栏 IIFE 里，`initStudentIdentity` 在它之前的顶层。
    # 直呼名字在静态文本上毫无异样（C 批抽出来单独 eval 也没事），浏览器里却是 ReferenceError，
    # 把后面标身份那几行一起带走（顶栏一直「正在确认身份…」）。所以钉的是**作用域**。
    defpos = h.find("function gxSetSid(sid){")
    sp = _shell_span(h)
    c.check("A11b `gxSetSid` 定义在三栏 IIFE 里 ⇒ 壳外不许直呼其名（ReferenceError），"
            "只许经 `window.ctGxSetSid`，且钩子就在定义后面",
            sp is not None and defpos > sp[0]
            and "window.ctGxSetSid = gxSetSid;" in h
            and h.index("window.ctGxSetSid = gxSetSid;") > defpos
            and _stray_gxset_calls(h) == 0,
            "span=%s def=%d hook=%s stray=%s"
            % (sp, defpos, "window.ctGxSetSid = gxSetSid;" in h, _stray_gxset_calls(h)))
    c.check("A11b正控 同一支判据对「壳内定义、壳外裸叫」必须报 1（否则这条是空判据）",
            _stray_gxset_calls(
                SHELL_MARK + "\n(function(){\n  function gxSetSid(s){}\n})();\n"
                "function init(){ gxSetSid('x'); }") == 1)
    c.check("A12 成功的身份也回写显示用的钥匙（否则下一位进来首屏还用老的）",
            "if (sid) localStorage.setItem('campustime_student', sid);"
            " else localStorage.removeItem('campustime_student');" in inside)

    setter = _fn_body(h, "function gxSetSid(sid){")
    c.check("A13 学号变了就换键重套（先 gxAdopt 再 applyBackdrop 各一次），同一个学号不白刷",
            setter.count("gxAdopt();") == 1 and setter.count("applyBackdrop();") == 1
            and setter.index("gxAdopt();") < setter.index("applyBackdrop();")
            and "if (sid === gxSid) return;" in setter)
    return c.summary("A 批（student.html 静态接线）")


def batch_b_plan_login():
    c = Checker()
    p = read("app/static/plan.html")
    l = read("app/static/login.html")
    s = read("app/static/student.html")

    c.check("B1 /plan 的键名与学生端同一格式（`ct_bg@学号`，没学号用裸键）",
            "return s ? ('ct_bg@' + s) : 'ct_bg';" in p)
    c.check("B2 /plan 读背景走 planBgKey()，不直接读裸键",
            p.count("localStorage.getItem(planBgKey())") == 1,
            str(p.count("localStorage.getItem(planBgKey())")))
    adopt = _fn_body(p, "function planAdopt(){")
    c.check("B3 /plan 也认领老键：裸键读到就搬、搬完删，且自己已有分键时不覆盖",
            adopt.count("localStorage.getItem('ct_bg')") == 1
            and adopt.count("localStorage.removeItem('ct_bg')") == 1
            and "if (localStorage.getItem(k) === null) localStorage.setItem(k, legacy);" in adopt)
    c.check("B4 裸键读在 /plan 里只有认领那一处（别处一律走 planBgKey）",
            len(BARE_GET.findall(p)) == 1,
            str(len(BARE_GET.findall(p))))
    c.check("B5 三个页面都不许 `localStorage.clear()`（会连按学号存的外观键和别的偏好一起清掉）",
            "localStorage.clear()" not in s and "localStorage.clear()" not in p
            and "localStorage.clear()" not in l)
    c.check("B5正控 同一支判据对一句故意的 clear 必须命中（否则 B5 是空判据）",
            "localStorage.clear()" in "x localStorage.clear();")
    c.check("B6 登录页登录成功要写 `campustime_student`（学生端首屏同步取的就是它）",
            'localStorage.setItem("campustime_student", d.student)' in l)
    return c.summary("B 批（plan.html / login.html 静态）")


C_NAMES = [
    "C1 老裸键被认领到 A（ct_bg / ct_gx_scrim）",
    "C2 认领后裸键已删（下一位不会继承）",
    "C3 A 读得到自己的值",
    "C4 认领是搬不是覆盖（A 自己的 ct_gx_lv 原样）",
    "C5 写只落到 A 的键",
    "C6 换 B：读不到 A 的背景（回落默认 forest）",
    "C7 换人必须重套一次背景",
    "C8 B 写的是 B 的键，A 没被动",
    "C9 切回 A：还是 A 的银河",
    "C10 没有学号：不读任何人的分键",
    "C11 /plan 键名与学生端同一格式",
    "C12 /plan 也认领了老键",
    "C13 /plan 首屏套的是 C 自己的图（applyPlanBackdrop 走分键）",
    "C14 /plan 没学号时读裸键",
    "C15 窗口钩子就是 gxSetSid（壳外只能走它换键）",
]

JS_TEMPLATE = r"""
const STU = __STU__;
const PLAN = __PLAN__;

function fakeLS(init) {
  const m = new Map(Object.entries(init || {}));
  return {
    getItem: k => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: k => { m.delete(k); },
  };
}
const results = [];
function chk(name, cond, extra) {
  results.push({ k: name, ok: !!cond, ex: extra === undefined ? '' : String(extra) });
}

let applied = 0;
globalThis.applyBackdrop = () => { applied++; };
globalThis.window = {};
globalThis.localStorage = fakeLS({
  campustime_student: 'A', ct_bg: 'deep', ct_gx_scrim: '.08', ct_gx_lv: '1',
});
try {
  eval(STU);
  chk('C1 老裸键被认领到 A（ct_bg / ct_gx_scrim）',
      localStorage.getItem('ct_bg@A') === 'deep' && localStorage.getItem('ct_gx_scrim@A') === '.08');
  chk('C2 认领后裸键已删（下一位不会继承）',
      localStorage.getItem('ct_bg') === null && localStorage.getItem('ct_gx_scrim') === null);
  chk('C3 A 读得到自己的值',
      gxVal('ct_bg') === 'deep' && gxVal('ct_gx_scrim') === '.08');
  chk('C4 认领是搬不是覆盖（A 自己的 ct_gx_lv 原样）',
      localStorage.getItem('ct_gx_lv@A') === '1');
  localStorage.setItem(gxKey('ct_bg'), 'milky');
  chk('C5 写只落到 A 的键',
      localStorage.getItem('ct_bg@A') === 'milky' && localStorage.getItem('ct_bg') === null);
  const before = applied;
  window.ctGxSetSid('B');
  chk('C6 换 B：读不到 A 的背景（回落默认 forest）',
      gxVal('ct_bg', 'forest') === 'forest', gxVal('ct_bg', 'forest'));
  chk('C7 换人必须重套一次背景', applied === before + 1, String(applied - before));
  localStorage.setItem(gxKey('ct_bg'), 'dusk');
  chk('C8 B 写的是 B 的键，A 没被动',
      localStorage.getItem('ct_bg@B') === 'dusk' && localStorage.getItem('ct_bg@A') === 'milky');
  window.ctGxSetSid('A');
  chk('C9 切回 A：还是 A 的银河', gxVal('ct_bg') === 'milky');
  window.ctGxSetSid('');
  chk('C10 没有学号：不读任何人的分键', gxVal('ct_bg', 'forest') === 'forest');
  chk('C15 窗口钩子就是 gxSetSid（壳外只能走它换键）',
      typeof window.ctGxSetSid === 'function' && window.ctGxSetSid === gxSetSid);
} catch (e) {
  console.log('@@ERR@@student: ' + e.message);
}

let imgSrc = null;
globalThis.document = {
  documentElement: { classList: { toggle: function () {} } },
  getElementById: function () {
    return {
      getAttribute: function () { return null; },
      setAttribute: function (k, v) { if (k === 'src') imgSrc = v; },
      removeAttribute: function () { imgSrc = null; },
    };
  },
};
globalThis.window.CT_BG_SRC = { forest: '/static/bg/forest.jpg', dusk: '/static/bg/dusk.jpg' };
globalThis.localStorage = fakeLS({ campustime_student: 'C', ct_bg: 'dusk' });
try {
  eval(PLAN);
  chk('C11 /plan 键名与学生端同一格式', planBgKey() === 'ct_bg@C', planBgKey());
  chk('C12 /plan 也认领了老键',
      localStorage.getItem('ct_bg@C') === 'dusk' && localStorage.getItem('ct_bg') === null);
  chk('C13 /plan 首屏套的是 C 自己的图（applyPlanBackdrop 走分键）',
      imgSrc === '/static/bg/dusk.jpg', String(imgSrc));
  globalThis.localStorage = fakeLS({});
  chk('C14 /plan 没学号时读裸键', planBgKey() === 'ct_bg', planBgKey());
} catch (e) {
  console.log('@@ERR@@plan: ' + e.message);
}

results.forEach(r => console.log('@@' + JSON.stringify(r)));
"""


def batch_c_behavior():
    c = Checker()
    node = shutil.which("node")
    if not node:
        print("  ⚠️ 跳过 C 批：这台机器没有 node（行为那 %d 项不计入总账）" % (len(C_NAMES) + 1))
        return c.summary("C 批（node 行为，已跳过）")
    try:
        stu = _slice(read("app/static/student.html"),
                     "// ---------- 外观偏好按学号分开（#98） ----------",
                     "function buildGxFx(", keep_end=False)
        plan = _slice(read("app/static/plan.html"),
                      "function planBgKey() {",
                      "    applyPlanBackdrop();")
    except ValueError as e:
        c.check("C0 两段代码抽得出来（页面结构没被改名）", False, str(e))
        return c.summary("C 批（node 行为）")

    js = JS_TEMPLATE.replace("__STU__", json.dumps(stu)).replace("__PLAN__", json.dumps(plan))
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "appearance98.js")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(js)
    try:
        st = subprocess.run([node, path], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    got, err = {}, ""
    for line in (st.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("@@ERR@@"):
            err = line[len("@@ERR@@"):].strip()
        elif line.startswith("@@"):
            try:
                item = json.loads(line[2:])
                got[item["k"]] = item
            except ValueError:
                pass
    for name in C_NAMES:
        item = got.get(name)
        ok = bool(item and item.get("ok"))
        extra = (item or {}).get("ex", "")
        if not ok and not extra:
            extra = err or "node 没给结果"
        c.check(name, ok, extra)
    c.check("C0 node 脚本跑通（退出码 0）", st.returncode == 0,
            "rc=%d %s" % (st.returncode, (st.stderr or "").strip()[:200]))
    return c.summary("C 批（node 行为）")


def main():
    code = 0
    ran = 0
    batches = (("A 批（student.html 静态接线）", batch_a_student),
               ("B 批（plan.html / login.html 静态）", batch_b_plan_login),
               ("C 批（node 行为）", batch_c_behavior))
    for label, fn in batches:
        ran += 1
        try:
            rc = fn()
        except Exception as e:      # 一批崩不能冒充「别的批通过」
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    # ⚠️ 别写成 `code = code or fn()`：`or` 短路会让第一批变红后面的批**根本不跑**。
    assert ran == len(batches), f"只跑了 {ran}/{len(batches)} 批"
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
