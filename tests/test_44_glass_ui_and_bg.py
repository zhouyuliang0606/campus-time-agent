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
    ck.check("默认档写在初始 HTML 上（半透 + 精修开）",
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
    ck.check("背景三档齐全（2 图 + 不用图）",
             all(k in src for k in ['label:"雾松林"', 'label:"银河"',
                                    'label:"不用图（回到改版前）"']))
    # 撤掉的两档是**因为许可核不了**（见 NOTICE.md）：断言它们不在，
    # 免得哪天有人从备份里把图捞回来、档也顺手加回来，测试还全绿。
    ck.check("「星空」「草坪」两档已摘除（许可不明的图不许悄悄回来）",
             'label:"星空"' not in src and 'label:"草坪"' not in src,
             [k for k in ("星空", "草坪") if ('label:"%s"' % k) in src])
    for key in ["ct_bg", "ct_gx_scrim", "ct_gx_vm", "ct_gx_bd", "ct_gx_core", "ct_gx_fx"]:
        ck.check("localStorage 键 %s 被使用" % key, key in src)
    ck.check("偏好只写 CSS 变量，不碰接口",
             "setProperty(t[1]" in src and "gxVal('ct_bg'" in src)
    # 换图必须把四个旋钮与动效都交回该图自己的反解默认值，
    # 否则会在银河上沿用雾松林的薄遮罩 —— 那是调性打架，不是偏好
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
    # 断言写成"留下的两张各有不同的反解默认值 + 撤掉的那两组数值不许还在"，
    # 而不是数四个具体值在不在 —— 前者是规则，后者只是当时那四张图的快照（4c）。
    ck.check("留下的两张图各有不同的反解默认值，撤掉的两组数值不许还留着",
             'vm:".31", scrim:".20"' in src and 'vm:".40", scrim:".12"' in src
             and 'vm:".62"' not in src and 'vm:".10"' not in src,
             [v for v in ('.62', '.10') if ('vm:"%s"' % v) in src])


def batch_gate(ck):
    """G. 总闸与旋钮：设置页每一项都必须真有对应效果，否则它是个会说谎的控件。"""
    title("G. 玻璃总闸 / 四档 / 精修层 / 四个旋钮")
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
    # 精修层：勾选框必须真有对应规则
    ck.check("精修层存在且键在 data-gx-refine 上", 'data-gx-refine="on"]' in src)
    ck.check("设置页标签写的是「精修」，且界面里不再出现「去土」字样",
             " 精修（强调色收口 + 字阶拉开）" in src and "去土（" not in src)
    ck.check("精修确实动了字阶（23px 标题）",
             re.search(r'data-gx-refine="on"\] \.topbar \.t \{ font-size: 23px', src) is not None)
    ck.check("精修确实收了强调色（状态标签改 2px 色杠）",
             "inset 2px 0 0" in src)
    # 漏白兜底：设置页/弹窗那几处表单的浅色是**内联样式**，类规则压不动，只能 !important；
    # 这条规则必须一直挂在 html.gx-glass 上，否则「不用图」那一档的浅色原样就被带跑了。
    LEAK = '[style*="border:1px solid #d8def0"]'
    ck.check("内联浅色的表单在深色下被兜住（background 带 !important）",
             re.search(r'html\.gx-glass \[style\*="border:1px solid #d8def0"\] \{[^}]*background:[^;}]*!important', src) is not None)
    ck.check("兜底规则全部挂在总闸下（出现次数 = 带 html.gx-glass 前缀的次数）",
             src.count(LEAK) == src.count("html.gx-glass " + LEAK), "%d / %d" % (src.count(LEAK), src.count("html.gx-glass " + LEAK)))
    ck.check("页面里确实还有内联浅色的表单（这条兜底不是空转）",
             src.count("border:1px solid #d8def0") - src.count(LEAK) >= 8,
             "内联 %d 处" % (src.count("border:1px solid #d8def0") - src.count(LEAK)))
    # 四个旋钮 + 五个偏好键
    for key in ["ct_gx_scrim", "ct_gx_vm", "ct_gx_bd", "ct_gx_core", "ct_gx_lv", "ct_gx_fx", "ct_gx_refine"]:
        ck.check("偏好键 %s 有读写" % key, key in src)
    ck.check("四个旋钮都进了 GX_TUNES", src.count('"--gx-') >= 4)
    # 换图必须把旋钮交回该图自己的反解默认值，否则会在银河上沿用雾松林的薄遮罩
    ck.check("换图时清掉四个旋钮（resetTunes）",
             "resetTunes" in src and "ct_gx_scrim','ct_gx_vm','ct_gx_bd','ct_gx_core'" in src)
    # 四张登记过的图 + 不用图，五档选择
    # 判据打在**引用形式**上（`src:"/static/bg/x.jpg"`），不是"文件名字符串出没出现过"——
    # 注释里写一句"space.jpg 已撤"是历史记录，不该把测试绊红（同 4c）。
    refs = [x for x in ("forest.jpg", "deep.jpg", "dusk.jpg", "milky.jpg", "space.jpg", "park.jpg")
            if 'src:"/static/bg/' + x + '"' in src]
    ck.check("背景表里真正指向图片的只有登记过的四行",
             refs == ["forest.jpg", "deep.jpg", "dusk.jpg", "milky.jpg"] and 'none:' in src, refs)
    # 反解的 vm 是**这张图自己的遮罩成本**：自生成的两张本来就沉，照抄银河的 .40 会把背景糊死，
    # 而页面看着"还行"、没人会去查 —— 只能钉在表上。
    for name in ("deep.jpg", "dusk.jpg"):
        m = re.search(r'src:"/static/bg/' + re.escape(name) + r'".*?vm:"(\.\d\d)"', src)
        ck.check("%s 的反解 vm ≤ .20（比银河那档省一半，别照抄回去）" % name,
                 m is not None and float(m.group(1)) <= .20, m.group(0) if m else None)
    ck.check("「不用图」的语义是撤总闸，不是黑底",
             "root.classList.toggle('gx-glass', on)" in src)
    # 出处上界面：档名后带小标，选中那档再给一行署名文案。
    # 为什么非要放进界面：用户问的是「银河那张要署名啥意思」，而答案只写在 NOTICE 里的话，
    # 会去拧这个开关的人永远看不到 —— 许可是选择的一部分，不是选择的脚注。
    ck.check("五档各带 lic 与 credit 两个字段",
             src.count('lic:"') >= 5 and src.count('credit:"') >= 5,
             "%d / %d" % (src.count('lic:"'), src.count('credit:"')))
    ck.check("渲染器真的用上了（小标 + 选中档的署名文案，且过 esc）",
             'class="bg-lic"' in src and "esc(curBg.credit)" in src
             and "getElementById('setBgLic')" in src)
    ck.check("银河那档在界面上直接写明「需署名」", 'lic:"CC BY 需署名"' in src)
    ck.check("两张自生成档各自写明 AI 生成", src.count('lic:"AI 生成"') == 2)
    ck.check("「不用图」也有一句说明它到底关掉什么（不许留空文案）",
             'credit:"撤掉整层玻璃' in src)
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
    BGS = ["forest.jpg", "milky.jpg", "deep.jpg", "dusk.jpg"]   # 草坪/星空 2026-10-12 因许可不明删除，见 NOTICE.md
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


def batch_notice(ck):
    """H. 素材登记闸：`app/static/bg/` 里**每一张图**都得在 NOTICE.md 登记过，且许可明确。

    为什么要有这道闸：这次的洞就是"顺手贴两张图进来、没人问过许可"，
    而当时所有测试都绿（E 批只验文件可达，可达 ≠ 有权用）。
    仓库要公开、要交比赛，这类债只能靠测试拦，不能靠人记得住。
    """
    title("H. 素材登记闸（bg/ 里不许出现没登记或许可不明的图）")
    notice = read(os.path.join(PROJ_DIR, "NOTICE.md"))
    files = sorted(f for f in os.listdir(BG_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    ck.check("背景目录里有图可查（这道闸不是空转）", len(files) >= 1, files)
    for name in files:
        row = next((ln for ln in notice.splitlines() if ln.startswith("|") and name in ln), None)
        ck.check("NOTICE 登记了 %s" % name, row is not None, "表里找不到这一行")
        if row:
            cols = [c.strip() for c in row.strip().strip("|").split("|")]
            lic = cols[2] if len(cols) > 2 else ""
            ck.check("%s 的许可列是明确的（不许「未确认」）" % name,
                     lic and ("未确认" not in lic) and ("未知" not in lic), lic)
    # 反向：已经删掉的图不许还躺在目录里
    for gone in ("park.jpg", "space.jpg"):
        ck.check("%s 已从仓库删除" % gone, gone not in files,
                 "文件还在（备份在仓库外，别放回来）")


# ============ I 批：/plan 也吃背景图（2026-10-31，用户截图「这个学生的页面没有吃到背景图」）============
# 这页是**浅色页**（深字 #1f2733），所以玻璃档做的是"浅色磨砂"：只把不透明白底换成半透明白，
# 字色一个都不动。判据因此不是 G 批那条"换底必须换字"，而是两条新的：
#   ① **每一条不透明的表面都必须被接管**（漏一条就是一块白板浮在照片上，比看不见更难解释）；
#   ② 接管之后**字仍要读得出来** —— 半透明白压在照片暗部与亮部两种底上都算 ≥4.5。
# ⚠️ 对比度是从 plan.html 的 CSS 里**现读现算**的，不在测试里重抄一遍色值（重抄=第二本账）。
PLAN = os.path.join(STATIC, "plan.html")
BGJS = os.path.join(STATIC, "bg.js")
PHOTO_DARK = "#0e2019"    # 副本 8061 实测：照片压在两层半透明底上的合成色（暗部）
PHOTO_BRIGHT = "#6f7d55"  # 同一张图的极亮档（与 G 批同一把尺子）


def _plan_pairs(text):
    """plan.html → (基线规则表, 玻璃段规则表)，键都按展开逗号后的选择器归一。"""
    head = text[:text.index("</style>")]
    head = re.sub(r"/\*.*?\*/", "", head, flags=re.S)
    base, glass = {}, {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", head):
        for s in sel.split(","):
            s = " ".join(s.split())
            if not s or s.startswith("@"):
                continue
            (glass if s.startswith("html.gx-glass") else base).setdefault(
                s.replace("html.gx-glass ", "").strip(), []).append(body)
    return base, glass


def _plan_ratio(text, selector, bg_photo=PHOTO_DARK):
    """这个表面在玻璃档下的实际对比度：现读它的 background 与生效字色，半透明先合成到照片上。"""
    base, glass = _plan_pairs(text)
    gbody = " ".join(glass.get(selector, []))
    m = re.search(r"background:\s*(?:linear-gradient\([^,]+,\s*)?(rgba\([^)]+\)|#[0-9a-fA-F]{6})", gbody)
    if not m:
        return None, None
    tokens = {}
    fill = _resolve(m.group(1), tokens)
    if fill is None:
        return None, None
    eff = _over(fill, bg_photo)
    # 字色：这条规则自己写的 > 基线同选择器写的 > body 那句全局色。⚠️ 必须认 3 位缩写：header 写的是 color: #fff，只认 6 位会漏成"这页没写字色"，
    #    然后退回 body 的深字 ⇒ 把"白字压深蓝"算成"深字压深蓝"，方向整个反（第一版就这么误判了 header 那条）。
    for src in (gbody, " ".join(base.get(selector, [])), " ".join(base.get("body", []))):
        mm = re.search(r"(?:^|;)\s*color:\s*(#[0-9a-fA-F]{3,6})\b", ";" + src)
        if mm:
            return _ratio(_hex6(mm.group(1)), eff), eff
    return None, None


def _hex6(c):
    """#fff → #ffffff（_lum/_ratio 只吃 6 位）。"""
    c = c.lstrip("#")
    return "#" + ("".join(x * 2 for x in c) if len(c) == 3 else c)



def batch_plan_glass(ck):
    title("I. /plan 的背景图接线（浅色磨砂：接管完整 + 字还读得出来）")
    src = read(PLAN)
    js = read(BGJS)
    st = read(STUDENT)

    ck.check("I1 plan.html 引用了 glass.css 与 bg.js，且 <link> 排在内联样式之后",
             'href="/static/glass.css"' in src and 'src="/static/bg.js"' in src
             and src.index("</style>") < src.index('/static/glass.css"'))
    ck.check("I2 背景图节点 #gxBg 紧跟 <body>、带 aria-hidden（装饰图不进朗读顺序）",
             src.index("<body>") < src.index('id="gxBg"') < src.index("<header>")
             and 'class="gx-backdrop" id="gxBg"' in src and 'aria-hidden="true"' in src)

    base, glass = _plan_pairs(src)
    opaque = {s for s, bs in base.items()
              if any(re.search(r"background:\s*(#[0-9a-fA-F]{3,6}|linear-gradient)", b) for b in bs)}
    missed = sorted(s for s in opaque if s not in glass)
    ck.check("I3 基线每一条不透明表面都被玻璃段接管（漏 %d 条：%s）" % (len(missed), missed[:6]),
             not missed, "不透明面 %d 条 / 接管 %d 条" % (len(opaque), len(opaque) - len(missed)))
    ck.check("I3-bis 正控：这条判据不是空转（基线确实有 ≥12 条不透明表面）",
             len(opaque) >= 12, "%d 条" % len(opaque))

    for sel, label in ((".card", "卡片正文"), (".day", "周表格子"), (".tab", "页签文字"),
                       (".blk.course", "课程条"), (".blk.todo", "待办条"), (".blk.done", "已完成条"),
                       ("header", "顶栏标题（白字压在蓝色半透明渐变上）")):
        r_dark, eff_d = _plan_ratio(src, sel, PHOTO_DARK)
        r_bright, eff_b = _plan_ratio(src, sel, PHOTO_BRIGHT)
        ck.check("I4 %s %-34s 暗部 %.2f:1 / 亮部 %.2f:1 都 ≥ 4.5" % (label, sel, r_dark or -1, r_bright or -1),
                 r_dark is not None and r_bright is not None and r_dark >= 4.5 and r_bright >= 4.5,
                 "%s / %s" % (eff_d, eff_b))

    # 反向对照：这页为什么不跟学生端共用那层深色玻璃 —— 深字压在 --surface 上就是 G 批那个病。
    dark_eff = _over(("rgba", (16, 36, 28, .52)), PHOTO_DARK)
    r_wrong = _ratio("#1f2733", dark_eff)
    ck.check("I5 反控：把卡片底换成学生端那层深色 --surface，同一句深字只有 %.2f:1（< 1.5 ⇒ 上面那阈值分得出好坏）" % r_wrong,
             r_wrong < 1.5, "%s → %.2f:1" % (dark_eff, r_wrong))

    ck.check("I6 「不用图」是摘总闸不是黑底（classList.toggle('gx-glass', …)）",
             "root.classList.toggle('gx-glass', !!on)" in src)
    ck.check("I6-bis 关掉总闸这页就是原来那套浅色（基线 body 底色没被动）",
             "background: #f4f6fb" in " ".join(base.get("body", [])))
    ck.check("I7 背景表两处逐项相等（bg.js ↔ student.html 的 GX_BGS，谁也不许拼路径）",
             sorted(re.findall(r'^\s*(\w+):\s*"(/static/bg/[\w.]+)"', js, re.M))
             == sorted(re.findall(r'(\w+):\s*\{[^}]*?src:"(/static/bg/[\w.]+)"', st))
             and len(re.findall(r'^\s*(\w+):\s*"', js, re.M)) == 4,
             "bg.js 四条 / GX_BGS 引用 %d 条" % len(re.findall(r'src:"/static/bg/', st)))
    # 这页没有学生端那 12 行 token 表（那在 student.html 的内联样式里），所以玻璃段里
    # 一旦写 var(--surface)/var(--bg)，在这页就是**未定义变量** ⇒ 整条声明失效、表面变透明板。
    gsec = " ".join(" ".join(bs) for bs in _plan_pairs(src)[1].values())
    ck.check("I8 右上角不再写「← 登录」（人已经登录了还写着登录）、两个入口的 href 没动",
             "← 登录" not in src and "换个身份" in src
             and 'href="/"' in src and 'href="/student"' in src)
    ck.check("I9 玻璃段没引用这页没有的 token（var( 出现 %d 次）" % gsec.count("var("),
             "var(" not in gsec, gsec[:90])


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



# ============ G 批：玻璃档下"换了底色就必须换字色"（2026-10-31，问答 chip 看不见那一条） ============
# 判据不是"某个 token 对比度够不够"，而是**这颗按钮实际用的那一对 color/background 够不够** ——
# 第一版写成前者，变异 M2（把字色换回写死的深字）当场只红 G2 不红 G3，等于量化闸是空的。
def _css_split(text):
    """剔掉注释后按 `html.gx-glass {` 切成基线段与玻璃段两半。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    style = text[:text.index("</style>")]
    gi = style.index("html.gx-glass {")
    return style[:gi], style[gi:]


def _css_rules(css):
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        key = " ".join(sel.split())
        if not key or key.startswith("@"):
            continue
        out.setdefault(key, []).append(body)
    return out


def _decl(bodies, prop):
    return any(re.search(r"(^|;)\s*%s\s*:" % prop, b) for b in bodies)


def _plain(sel):
    return sel.replace("html.gx-glass ", "").strip()


def _glass_tokens(text):
    """玻璃段那 12 行变量表 → {token 名: 值}（值可能是 #hex，也可能是 rgba(...)）。"""
    _, glass = _css_split(text)
    blk = glass[:glass.index("}")]
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", blk))


def _resolve(val, tokens):
    """把声明值翻成一个可算的颜色：('hex', '#rrggbb') 或 ('rgba', (r,g,b,a))；解不出 → None。"""
    val = val.strip()
    m = re.match(r"var\((--[\w-]+)\)", val)
    if m:
        val = tokens.get(m.group(1), "").strip()
    m = re.match(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*(1|0?\.\d+)\)", val)
    if m:
        return ("rgba", tuple(int(m.group(i)) for i in (1, 2, 3)) + (float(m.group(4)),))
    m = re.match(r"#[0-9a-fA-F]{6}$", val)
    if m:
        return ("hex", val)
    return None


def _over(fg, hex_bg):
    """rgba 叠在纯色上合成 #rrggbb；已经是 hex 的原样返回。"""
    if fg[0] == "hex":
        return fg[1]
    r, g, b, a = fg[1]
    base = [int(hex_bg.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(round(c * a + bb * (1 - a)) for c, bb in zip((r, g, b), base))


def _lum(hexc):
    h = hexc.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _pair_ratio(text, selector, hover=False):
    """这颗按钮**实际用的那一对**颜色算出来的对比度。

    返回 (玻璃档下的对比度, 基线字色压在玻璃档底色上的对比度)；后者就是"改之前"的读数。
    底色是半透明的，先合成到 --bg 再算 —— 不合成会把 .52 的 alpha 当成 52 用（第一版踩过）。
       ⚠️ 这里只合成**一层**（判据偏乐观约 0.4）：真机上这颗按钮套在 .view 里，两层同色半透明底叠加，
       副本 8061 的 getComputedStyle 实测 color=rgb(185,207,194)、合成底 #0e2019 = 10.30:1；
       把星空图按中位/亮部/极亮三档亮度代入重算落在 7.51~10.30:1，全都 ≥ 4.5；
       旧字色 #1f2733 在同四种背景上是 1.02~1.22:1 —— 这就是截图里"看不见"的量化值。
    """
    base, glass = _css_split(text)
    tokens = _glass_tokens(text)
    bg = _resolve("var(--bg)", tokens)
    rules = _css_rules(glass)
    key = selector + (":hover" if hover else "")
    decls = " ".join(rules.get(key, []))
    m_bg = re.search(r"background:\s*([^;]+)", decls)
    m_fg = re.search(r"(?:^|;)\s*color:\s*([^;]+)", ";" + decls)
    if not m_bg or not m_fg:
        return None, None
    bcol = _resolve(m_bg.group(1), tokens)
    fcol = _resolve(m_fg.group(1), tokens)
    if bcol is None or fcol is None:
        return None, None
    eff = _over(bcol, bg[1])
    r_now = _ratio(fcol[1], eff) if fcol[0] == "hex" else None
    # 基线字色（同一选择器在基线段里写的那个值）压在玻璃档底色上 = 改之前的读数
    base_decls = " ".join(_css_rules(base).get(selector, []))
    m_base = re.search(r"(?:^|;)\s*color:\s*([^;]+)", ";" + base_decls)
    r_old = None
    if m_base:
        bc = _resolve(m_base.group(1), tokens)
        if bc and bc[0] == "hex":
            r_old = _ratio(bc[1], eff)
    return r_now, r_old


def _bg_without_fg(text):
    """玻璃段里改了 background、却没在任何一层给这个选择器设 color 的那些。

    为什么这是判据：基线段的配色是"近白底 + 深字"（`.faq-btn` 自己写着 color:#1f2733），
    玻璃段把底换成 --surface（深色半透明）之后，深字**不会**自动翻 —— CSS 不报错、
    页面照样开，只是那行字压在深色上看不见。:622 那条"凡把 --brand 当底色的地方文字跟着翻"
    是同一个规矩，只是当时漏了用 --surface 当底的这两颗。
    """
    base, glass = _css_split(text)
    B, G = _css_rules(base), _css_rules(glass)
    has_base_color = {_plain(s) for s, bs in B.items() if _decl(bs, "color")}
    has_glass_color = {_plain(s) for s, gs in G.items() if _decl(gs, "color")}
    bad = set()
    for s, gs in G.items():
        if not _decl(gs, "background"):
            continue
        k = _plain(s)
        if k in has_base_color and k not in has_glass_color:
            bad.add(k)
    return sorted(bad)


def batch_glass_contrast(ck):
    title("G. 玻璃档换了底色必须同时换字色（问答 chip 看不见那一条的形状）")
    src = read(STUDENT)
    bad = _bg_without_fg(src)
    ck.check("G1 玻璃段里没有\"换了 background 却没换 color\"的选择器（违规：%s）" % (bad or "无"),
             not bad, str(bad))
    glass = _css_split(src)[1]
    btn = re.search(r"\.faq-btn\s*\{([^}]*)\}", glass)
    body = btn.group(1) if btn else ""
    ck.check("G2 .faq-btn 在玻璃段里 background 与 color 成对（旧写法不许回来）",
             "background: var(--surface)" in body and re.search(r"color:\s*var\(--", body) is not None,
             body.strip()[:96])
    r_now, r_old = _pair_ratio(src, ".faq-btn")
    ck.check("G3 这颗 chip 实际用的那对颜色：对比度 %.2f:1 ≥ 4.5（WCAG AA）" % (r_now or -1),
             r_now is not None and r_now >= 4.5, "%.2f" % (r_now or -1))
    ck.check("G4 同一颗按钮换回基线那句深字只有 %.2f:1（< 1.5 ⇒ 上面那个阈值分得出好坏）" % (r_old or -1),
             r_old is not None and r_old < 1.5, "%.2f" % (r_old or -1))
    r_hov, _ = _pair_ratio(src, ".faq-btn", hover=True)
    ck.check("G5 hover 态那一对也算了：对比度 %.2f:1 ≥ 4.5" % (r_hov or -1),
             r_hov is not None and r_hov >= 4.5, "%.2f" % (r_hov or -1))
    pr_now, pr_old = _pair_ratio(src, ".probe-out")
    ck.check("G6 同形第二处 .probe-out（诊断面板）也过：现在 %.2f:1、改之前 %.2f:1" % (pr_now or -1, pr_old or -1),
             pr_now is not None and pr_now >= 4.5 and pr_old is not None and pr_old < 2.5,
             "%.2f / %.2f" % (pr_now or -1, pr_old or -1))

def main():
    ck = Checker()
    batch_structure(ck)
    batch_tokens(ck)
    batch_icons(ck)
    batch_pref(ck)
    batch_gate(ck)
    batch_http(ck)
    batch_notice(ck)
    batch_glass_contrast(ck)
    batch_plan_glass(ck)
    batch_reply_text_untouched(ck)
    return ck.summary("test_44 玻璃层与背景偏好")


if __name__ == "__main__":
    sys.exit(main())
