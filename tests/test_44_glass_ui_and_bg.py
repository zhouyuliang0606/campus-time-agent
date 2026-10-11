"""玻璃质感层与背景偏好（test_44）。

**守的是什么**：2026-10-11 把 glass-ui-skill 的深色玻璃接到学生端，一共四笔提交
（公共层与素材 / 学生端接线 / 背景设置 / 图标下线）。这类"只动视觉层"的改动
最容易出两种事故，这份护栏分别对着它们：

1. **接线断掉但没人发现**：`glass.css` 挪走、`<link>` 顺序写反（在内联样式之前
   就盖不住旧规则）、背景图路径错或被 .gitignore 吃掉 —— 页面照样能打开，
   只是悄悄退回旧样式。E 批用真 HTTP 请求把这三条钉住。
2. **改配色时把 token 改名**：学生端 2026-10-02 那层已经把配色收进 `:root`，
   本次**只改值不改名**，全站 101 处 `var()` 因此自动跟上。一旦有人改名，
   那些引用会静默失效（CSS 不报错，只是变回初始值/继承值）。B 批钉键名与引用数。

⚠️ **C 批的范围是刻意收窄的，读它之前先看这条**：
它只扫「静态 HTML 的 chrome 区」（`<body>` 到 `esc.js` 之间、去掉注释行），
并且只比对**一份写死的下线字形清单**。它**不**断言"全站没有 emoji"——
说给学生听的回话文本里 `✅ 已经按你的确认改好了`、`🗓️ 先定个方式`、`👋 欢迎回来`
是**文案不是图标**，属于另一件事（红线 8 管的是技术内脏，不管 emoji），
所以 F 批反过来钉住它们还在，防止有人把文案一起删了。
本仓已经栽过三次"断言搜索范围大于它声称的范围"，这条边界写在这里就是为了不再栽第四次。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import PROJ_DIR, Checker, title  # noqa: E402

STATIC = os.path.join(PROJ_DIR, "app", "static")
STUDENT = os.path.join(STATIC, "student.html")
GLASS = os.path.join(STATIC, "glass.css")
BG_DIR = os.path.join(STATIC, "bg")

# 本次下线的"图标位" emoji（换成线性 SVG 的那些字形）
RETIRED = ["\U0001F5D3", "\u2705", "\u2699", "\u2795", "\U0001F4E6", "\U0001F371",
           "\U0001F4C5", "\U0001F4E2", "\u2753", "\U0001F4E4", "\U0001F4CB",
           "\U0001F4E8", "\U0001F916", "\U0001F9ED", "\U0001F4CE", "\U0001F3A4",
           "\U0001F9F9", "\U0001F9BD"]
# 回话文本里的 emoji：属于文案，本次一个字不许动
KEPT_IN_REPLY = ["\u2705", "\U0001F5D3", "\U0001F64B", "\U0001F916"]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def chrome_region(src):
    """静态 HTML 的 chrome 区：<body> 到 esc.js 引用之前，去掉 HTML 注释。"""
    start = src.index("<body>")
    end = src.index("esc.js")
    body = src[start:end]
    return re.sub(r"<!--.*?-->", "", body, flags=re.S)


def batch_structure(ck):
    title("A. 玻璃层接线（link 顺序 / 装饰节点 / 层级 / 主题类）")
    src = read(STUDENT)
    ck.check("glass.css 存在且非空", os.path.isfile(GLASS) and len(read(GLASS)) > 2000)
    ck.check("student.html 引用了 glass.css",
             'href="/static/glass.css"' in src)
    # 顺序是关键：放在内联 </style> 之前，同权重的旧规则就会反过来盖住新层
    ck.check("<link> 排在内联样式之后（否则盖不住旧规则）",
             src.index("</style>") < src.index('/static/glass.css"'))
    ck.check('背景图节点 #gxBg 在 <body> 开头',
             src.index('id="gxBg"') < src.index('class="shell"'))
    ck.check("veil 与动效层节点在位且标了 aria-hidden",
             'class="gx-veil" aria-hidden="true"' in src and 'id="gxStage" aria-hidden="true"' in src)
    ck.check(".shell 抬到内容层（backdrop-filter 才有可模糊的底层）",
             re.search(r"\.shell\s*\{[^}]*z-index:\s*2", src) is not None)
    ck.check("html 初始就挂着 rain 主题 + 玻璃总闸（不挂会先闪一下浅色）",
             'class="tk-rain gx-glass"' in src)
    ck.check("默认档写在初始 HTML 上（半透 + 去土开）",
             'data-gx-lv="2"' in src and 'data-gx-refine="on"' in src)
    g = read(GLASS)
    ck.check("公共层含 rain / space 两套 token", ".tk-rain{" in g and ".tk-space{" in g)
    ck.check("公共层含 prefers-reduced-motion 降级", "prefers-reduced-motion" in g)
    ck.check("降级时动效层整层撤掉", re.search(r"prefers-reduced-motion[^}]*\.gx-fx\{display:none", g, re.S) is not None)
    # 公共层不该塞页面专属选择器（那是各页面自己的事）
    for page_sel in [".card", ".sidebar", ".composer", ".dlg-head"]:
        ck.check("公共层不含页面专属选择器 %s" % page_sel,
                 re.search(r"(^|\})\s*%s\s*\{" % re.escape(page_sel), g) is None)


def batch_tokens(ck):
    title("B. token 只改值、没改名（101 处 var() 靠名字接上）")
    src = read(STUDENT)
    m = re.search(r":root\s*\{(.*?)\}", src, re.S)
    ck.check("找到 :root 块", m is not None)
    block = m.group(1) if m else ""
    for k in ["--bg", "--surface", "--line", "--line-strong", "--text", "--text-2",
              "--text-3", "--brand", "--brand-soft", "--brand-soft-2",
              "--radius", "--radius-lg", "--gap"]:
        ck.check("token 名还在：%s" % k, ("%s:" % k) in block)
    ck.check("新增 --brand-fg（主色当底色时的文字色）", "--brand-fg:" in block)
    ck.check("--brand-fg 确实被用上，不是摆着好看", "var(--brand-fg)" in src)
    n = len(re.findall(r"var\(--", src))
    ck.check("var() 引用量没塌（≥90，实测 %d）" % n, n >= 90)
    # 深色底上白字/浅底会糊：--brand 当 background 用的地方必须同时给文字色
    ck.check("主色当底的按钮已配亮字+投影", re.search(r"\.btn[^{]*\{[^}]*--brand", src, re.S) is not None)
    # 浅色基线**故意留在 :root** —— 「设置 → 背景 → 不用图」靠它原样还回去；
    # 深色值只能出现在 html.gx-glass 覆盖块里。两边必须成对存在，缺一即坏。
    # （值前有多个对齐空格，所以用正则而不是字面量匹配 —— 字面量会被排版差异绊倒）
    ck.check("浅色基线在 :root、深色值在 html.gx-glass 下（成对）",
             "#f7f8fa" in block and "html.gx-glass {" in src
             and re.search(r"--surface:\s+rgba\(16,36,28,\.52\)", src) is not None)


def batch_icons(ck):
    title("C. chrome 图标下线（只扫静态 chrome 区，不含注释与回话文本）")
    region = chrome_region(read(STUDENT))
    leaked = [e for e in RETIRED if e in region]
    ck.check("chrome 区不再出现已下线的图标 emoji（漏网：%s）" % (leaked or "无"),
             not leaked)
    src = read(STUDENT)
    ck.check("图标实例数成规模（≥20，实测 %d）" % src.count('<svg class="ic"'),
             src.count('<svg class="ic"') >= 20)
    ck.check("symbol 精灵表在位且 aria-hidden", '<defs>' in src and 'aria-hidden="true"><defs>' in src.replace('\n', ''))
    for sid in ["gi-cal", "gi-check", "gi-sliders", "gi-box", "gi-bowl",
                "gi-upload", "gi-help", "gi-mic", "gi-clip", "gi-bird"]:
        ck.check("symbol 定义齐全：%s" % sid, ('id="%s"' % sid) in src)
    ck.check("MODULES.ico 已改成 symbol id（不再是 emoji）",
             'ico: "gi-cal"' in src and 'ico: "U' not in src)
    ck.check("模块卡模板用 <use> 出图标", '<use href="#${m.ico}"' in src)
    ck.check("折叠指示不再是 Dingbat ➕/➖",
             "\u2795" not in src and "\u2796" not in src)
    ck.check("图标尺寸走 currentColor（公共层定义）",
             "stroke:currentColor" in read(GLASS))


def batch_pref(ck):
    title("D. 背景偏好：读写、默认值与动效联动")
    src = read(STUDENT)
    ck.check("背景五档齐全（4 图 + 不用图）",
             all(k in src for k in ['label:"雾松林"', 'label:"银河"', 'label:"星空"',
                                    'label:"草坪"', 'label:"不用图（回到改版前）"']))
    for key in ["ct_bg", "ct_gx_scrim", "ct_gx_vm", "ct_gx_bd", "ct_gx_core", "ct_gx_fx"]:
        ck.check("localStorage 键 %s 被使用" % key, key in src)
    ck.check("偏好只写 CSS 变量，不碰接口",
             "setProperty(t[1]" in src and "gxVal('ct_bg'" in src)
    # 换图必须把四个旋钮与动效都交回该图自己的反解默认值，
    # 否则会在亮草坪上沿用雾松林的薄遮罩 —— 那是调性打架，不是偏好
    ck.check("换图时清掉四个旋钮与动效（resetTunes）",
             "resetTunes" in src
             and "ct_gx_scrim','ct_gx_vm','ct_gx_bd','ct_gx_core','ct_gx_fx" in src)
    ck.check("reduced-motion 下连节点都不生成",
             "prefers-reduced-motion" in src and "buildGxFx" in src)
    ck.check("启动时先套背景再绘制（applyBackdrop 在引导段里）",
             src.index("applyBackdrop();") > src.index("function applyBackdrop"))
    # 鸟不能用 <use>：shadow 树里选不中 .wg，会得到一只不动的鸟
    ck.check("鸟是内联画的（不是 <use> 引 symbol）",
             'GX_BIRD' in src and '<use href="#gi-bird"' not in src)
    # 每档背景自带**反解**出来的 veil/遮罩默认值（不是拍脑袋的数）
    ck.check("每张图自带反解出的 veil/遮罩默认值（四张各不相同）",
             'vm:".31"' in src and 'vm:".40"' in src and 'vm:".10"' in src and 'vm:".62"' in src)


def batch_gate(ck):
    """G. 总闸与旋钮：设置页每一项都必须真有对应效果，否则它是个会说谎的控件。"""
    title("G. 玻璃总闸 / 四档 / 去土层 / 四个旋钮")
    src = read(STUDENT)
    glass = read(GLASS)
    # 总闸：深色值与补丁段全部挂在 html.gx-glass 下，关掉即整层失效
    ck.check(":root 保留浅色基线（关掉玻璃要能原样还回去）",
             re.search(r":root\s*\{[^}]*--surface:\s*#fff", src) is not None)
    ck.check("深色值改挂 html.gx-glass", re.search(r"html\.gx-glass\s*\{[^}]*--surface:\s*rgba\(16,36,28", src) is not None)
    i = src.index("深色玻璃补丁")
    j = src.index("@media (max-width: 720px)")
    block = src[i:j]
    ungated = [l.strip()[:48] for l in block.splitlines()
               if "{" in l and "html.gx-glass" not in l and not l.strip().startswith(("*", "/*", ":"))]
    ck.check("补丁段每条规则都在总闸下（漏网：%s）" % (ungated or "无"), not ungated)
    ck.check("装饰三层以总闸为开关（glass.css）",
             "html.gx-glass .gx-backdrop{display:block" in glass and "html.gx-glass .gx-veil{display:block" in glass)
    # 真·磨砂：面板必须有**未加前缀的** backdrop-filter，否则"整页玻璃"那档拧了没反馈。
    # ⚠️ 判据不能写 `"backdrop-filter" in block` —— `-webkit-backdrop-filter` 里也含这个
    #    子串，摘掉标准属性照样绿（本条变异实测过，第一次就是这么假绿的）。
    std_blur = re.findall(r"(?<!-webkit-)backdrop-filter:\s*blur", block)
    ck.check("面板上了未加前缀的 backdrop-filter（共 %d 处）" % len(std_blur), len(std_blur) >= 1)
    ck.check("同时带 -webkit- 前缀（Safari 要）",
             "-webkit-backdrop-filter: blur" in block or "-webkit-backdrop-filter:blur" in block)
    for lv, blur in [("0", "22px"), ("1", "12px"), ("2", "6px"), ("3", "2px")]:
        ck.check("档位 data-gx-lv=%s 有对应模糊（%s）" % (lv, blur),
                 ('data-gx-lv="%s"]' % lv) in block and blur in block)
    # 去土层：勾选框必须真有对应规则
    ck.check("去土层存在且键在 data-gx-refine 上", 'data-gx-refine="on"]' in src)
    ck.check("去土确实动了字阶（23px 标题）",
             re.search(r'data-gx-refine="on"\] \.topbar \.t \{ font-size: 23px', src) is not None)
    ck.check("去土确实收了强调色（状态标签改 2px 色杠）",
             "inset 2px 0 0" in src)
    # 四个旋钮 + 五个偏好键
    for key in ["ct_gx_scrim", "ct_gx_vm", "ct_gx_bd", "ct_gx_core", "ct_gx_lv", "ct_gx_fx", "ct_gx_refine"]:
        ck.check("偏好键 %s 有读写" % key, key in src)
    ck.check("四个旋钮都进了 GX_TUNES", src.count('"--gx-') >= 4)
    # 换图必须把旋钮交回该图自己的反解默认值，否则会在亮草坪上沿用雾松林的薄遮罩
    ck.check("换图时清掉四个旋钮（resetTunes）",
             "resetTunes" in src and "ct_gx_scrim','ct_gx_vm','ct_gx_bd','ct_gx_core'" in src)
    # 四张图 + 不用图，五档选择
    ck.check("背景五档齐全（4 图 + 不用图）",
             all(x in src for x in ['forest.jpg', 'milky.jpg', 'space.jpg', 'park.jpg', 'none:']))
    ck.check("「不用图」的语义是撤总闸，不是黑底",
             "root.classList.toggle('gx-glass', on)" in src)
    # 死规则检查：类名写错会让规则静默失效
    ck.check("已完成态选择器是真实的 .evt.todo.done",
             ".evt.todo.done {" in src and ".evt.done {" not in src)


def batch_http(ck):
    title("E. 真 HTTP：文件真的可达（路径写错/被 gitignore 都在这红）")
    sys.path.insert(0, PROJ_DIR)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    r = client.get("/student")
    ck.check("GET /student 200", r.status_code == 200, str(r.status_code))
    html = r.text
    ck.check("响应里带着 glass.css 链接", "/static/glass.css" in html)
    ck.check("响应里带着背景图节点", 'id="gxBg"' in html)
    r2 = client.get("/static/glass.css")
    ck.check("GET /static/glass.css 200", r2.status_code == 200, str(r2.status_code))
    BGS = ["forest.jpg", "milky.jpg", "space.jpg", "park.jpg"]
    for name in BGS:
        rr = client.get("/static/bg/" + name)
        ck.check("GET /static/bg/%s 200 且是图片" % name,
                 rr.status_code == 200 and rr.headers.get("content-type", "").startswith("image/"),
                 "%s / %s" % (rr.status_code, rr.headers.get("content-type")))
    for name in BGS:
        size = os.path.getsize(os.path.join(BG_DIR, name))
        ck.check("%s 体积在预算内（%d KB ≤ 230KB）" % (name, size // 1024), size <= 230 * 1024)
    ck.check("素材许可与署名已登记 NOTICE.md",
             os.path.isfile(os.path.join(PROJ_DIR, "NOTICE.md"))
             and "Guisard" in read(os.path.join(PROJ_DIR, "NOTICE.md")))


def batch_reply_text_untouched(ck):
    title("F. 反向：回话文本里的 emoji 是文案，本次不该被顺手删")
    src = read(STUDENT)
    ck.check("「✅ 已经按你的确认改好了」还在", "✅ 已经按你的确认改好了" in src)
    ck.check("「🗓️ 先定个方式」还在", "🗓️ 先定个方式" in src)
    ck.check("欢迎语气泡的 👋 还在", "👋 欢迎回来" in src)
    # 上传课表那颗按钮的 title 被 test_23 钉着，换图标时不能连 title 一起改
    ck.check("上传课表按钮的 title 文案未被动（test_23 钉着它）",
             "上传课表文件，管家读取后给出预览清单" in src)
    ck.check("完整日程入口的 href 未动（test_04 钉着）", 'href="/plan"' in src)


def main():
    ck = Checker()
    batch_structure(ck)
    batch_tokens(ck)
    batch_icons(ck)
    batch_pref(ck)
    batch_gate(ck)
    batch_http(ck)
    batch_reply_text_untouched(ck)
    return ck.summary("test_44 玻璃层与背景偏好")


if __name__ == "__main__":
    sys.exit(main())
