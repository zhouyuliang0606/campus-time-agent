"""#25 的后续：管理控制台也改成三栏（左栏点档、正文只在中间滚、口令条常驻顶栏）。

这一页与驿站端有两点本质不同，护栏就围绕这两点：
  ① **一半的卡是表单**（通知 / 人格 / API / 知识库），所以"切视图只动 display、DOM 不重建"
     在这里不是洁癖而是防数据丢失；也因此 **API 那一档不许进门就现拉** ——
     `loadApiSettings()` 是把值**写回输入框**的，一进就拉等于把管理员刚敲的一半抹掉。
  ② **每一档都吃管理口令**，所以口令条放顶栏常驻，不放进某一档（放进档里 = 切个档满屏"读取失败"却没人知道为什么）。
两种角标语义也不同：红点=有活要干（未结工单），灰计数=条数（这页没有"已读"状态，不许谎称未读）。
"""
import pathlib
import re

from _harness import Checker, title

code = 0

F = pathlib.Path("app/static/admin.html")

# 各卡里的 id：这轮只搬容器，一个都不许改（JS 全是 getElementById 硬绑）
MUST_KEEP_IDS = [
    "sbNav", "navTitle", "adminToken", "saveToken", "tokenStatus",
    "ntitle", "ncontent", "nfile", "attachStatus", "publish", "nstatus", "noticeList",
    "pname", "prole", "ptone", "prules", "savePersona", "pstatus",
    "akey", "abase", "amodel", "saveApi", "testApi", "apistatus", "apiNow",
    "usageMeta", "usageTiles", "usageModels", "usageRecent", "refreshUsage", "clearUsage", "usageStatus",
    "kq", "ka", "kk", "kbSave", "kbCancel", "kbList",
    # #40 新增「文档知识库」那一档的四个绑点（两个按钮 + 状态行 + 表格容器）
    "docfile", "refreshDocs", "docStatus", "docList",
    # #64 新增「Agent 工作流」那一档：JS 里 getElementById 硬绑的十二个绑点。
    # 这张表的作用就是"改 id 等于把那张卡废了"—— 那十二个名字任何一个被改名，JS 不会报错，
    # 只会安静地画不出东西（本项目反复吃过这种静默）。
    "traceMeta", "traceModule", "traceMode", "traceTools", "traceQ", "traceGo", "traceNew",
    "traceList", "traceDetail", "refreshTrace", "clearTrace", "traceStatus",
    "woBox", "refresh", "inbox", "woNum", "msgNum",
]
VIEWS = ["wo", "repair", "inbox", "notice", "kb", "docs", "usage", "trace", "api", "persona"]


def code_of(text: str) -> str:
    """负向断言一律先剔注释（本仓第七次踩这条之后立的规矩）。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(ln.split("//")[0] for ln in t.splitlines())


def batch_a_shell():
    c = Checker()
    title("A. 外壳：旧容器连 CSS 删净、每档都有视图、id 一个不少、配色没抄错")
    st = F.read_text(encoding="utf-8")
    c.check("六件套结构在（shell/sidebar/topbar/stage/main + nav-view 那套命名，与驿站端同构）",
            all(('class="' + k + '"') in st or ("." + k + " {") in st
                for k in ("shell", "sidebar", "main", "topbar", "stage", "nav-view")), "")
    gone = [k for k in ("<header", 'class="wrap"', ".wrap {", "header h1", "header a {", "header {")
            if k in st]
    c.check("header 与 .wrap 连四条 CSS 一起删（HTML 与样式两处都不许留孤儿）", not gone, "、".join(gone))
    missing = [i for i in MUST_KEEP_IDS if ('id="' + i + '"') not in st]
    c.check("MUST_KEEP_IDS 那张表里 getElementById 硬绑的 id 一个都不许丢（改 id 等于把那张卡废了；"
            "有几条以那张表为准，标签里不数数——数数迟早自己撞自己）",
            not missing, "缺：" + "、".join(missing))
    views = re.findall(r'id="view-([a-z]+)"', st)
    navs = re.findall(r'data-nav="([a-z]+)"', st)
    c.check("左栏档位与视图容器**一一对应**（多一档没地方显示、少一档点了没反应；"
            "名字从 VIEWS 那份表算，断言里不写「九档/十档」—— #64 加一档时这种数必然过期）",
            sorted(views) == sorted(navs) == sorted(VIEWS), f"views={views} navs={navs}")
    c.check("每块 view 里只装原来那张卡，工单/收件箱这两张**不许有第二份**（搬家最容易漏删旧的那份，"
            "重复 id 会让 JS 只认第一个，另一份成永远刷不新的死表格）",
            all(st.count('id="' + i + '"') == 1 for i in ("woBox", "inbox", "refresh")), "")
    c.check("配色走管理端自己的橙红，没把驿站那套青绿抄进来（#17b8a6 整页不许出现）",
            "#17b8a6" not in st and "#ff7a59" in st and "#d94b68" in st, "")
    c.check("没塞新依赖：只引 esc.js",
            st.count("<script src=") == 1 and "esc.js" in st
            and not any(k in st for k in ("glass.css", "<symbol")), "")
    return c


def batch_b_switch():
    c = Checker()
    title("B. 切视图：只 display 切、表单不重建，API 那档不许进门就拉")
    st = F.read_text(encoding="utf-8")
    sw = st[st.index("function switchNav(view)"):st.index('id="sbNav"')] if 'id="sbNav"' in st else ""
    if not sw:
        sw = st[st.index("function switchNav(view)"):st.index('document.getElementById("sbNav")')]
    c.check("切视图靠 classList，不 innerHTML 重建（这页一半是表单，重建一次就是一次数据丢失）",
            'v.classList.remove("active")' in sw and 'nv.classList.add("active")' in sw
            and "innerHTML" not in sw, "")
    c.check("CSS 两条 display 规则齐：默认藏、active 显",
            ".nav-view { display: none; }" in st and ".nav-view.active { display: block; }" in st, "")
    # 负向半边先剔注释：我 switchNav 里那句"为什么 api 档不现拉"的说明正好引用了这个名字
    #（本仓第七次同类绊红，规矩是死的：不许拿自己的注释当代码判红）
    sw_code = code_of(sw)
    # (档位 → loader) 从 switchNav 里**自己 parse 出来**，不写死清单：
    # 写死八对的那一版在 #64 加第九档时不会红，只会安静地少覆盖一档 —— 那正是这仓反复记的那种假绿。
    loaded = dict(re.findall(r'view === "(\w+)"\)\s*(load\w+)\(\)', sw_code))
    c.check("API 那一档**不在**进入时现拉（loadApiSettings 会把值写回输入框，一进就拉=抹掉刚敲的一半）",
            "loadApiSettings" not in sw_code and 'view === "api"' not in sw_code
            and 'document.getElementById("akey").value =' in st,
            "switchNav 里出现了 loadApiSettings")
    c.check("每一档（API 除外）进档都现拉一次，而且拉的是**那一档自己的** loader —— "
            "名单从 VIEWS 算，不写死八对（#64 加「Agent 工作流」这一档，写死的清单会静默少覆盖一档；"
            "「文档知识库」那档最重也照样点开才拉，不在开场预载）",
            not [v for v in VIEWS if v != "api" and v not in loaded]
            and loaded.get("wo") == "loadWorkOrders"      # 两个名字推不出来的，逐条点名
            and loaded.get("inbox") == "loadMessages"
            and loaded.get("trace") == "loadTrace"
            and len(loaded) == len([v for v in VIEWS if v != "api"]),
            f"switchNav 里的 (档 → loader) = {loaded}")
    c.check("顶栏标题查表，不是每档一个 if 各写一遍（NAV_TITLE 只有一份，且每个档位都在里面 —— "
            "加了左栏档位忘了标题表，切过去就退回「管理控制台」那个默认值，这种半截最不容易被发现）",
            't.textContent = NAV_TITLE[view]' in sw and st.count("const NAV_TITLE") == 1
            and not [v for v in VIEWS if not re.search(r'\b%s\w*:' % v,
                                                       st[st.index("const NAV_TITLE"):st.index("function switchNav")])],
            "")
    c.check("开场默认停在「学生工单」，且那一档的初始加载**只有一处**（switchNav 已经拉过，"
            "再在 init 里补一遍就是同一件事两份）",
            'switchNav("wo");' in st and 'view-wo" class="nav-view active"' in st
            and re.search(r'loadNotices\(\); loadPersona\(\); loadKB\(\); loadMessages\(\); loadApiSettings\(\);', st) is not None,
            "")
    return c


def batch_c_badges():
    c = Checker()
    title("C. 两种角标：顺序 + 语义（红=有活干，灰=条数）")
    st = code_of(F.read_text(encoding="utf-8")).replace("\r\n", "\n")
    full = F.read_text(encoding="utf-8").replace("\r\n", "\n")
    lw = full[full.index("async function loadWorkOrders()"):full.index("async function loadMessages()")]
    lm = full[full.index("async function loadMessages()"):full.index("async function loadUsage()")]
    # ⚠️ 顺序钉**不认变量名**：#29 归档把那两张表的局部变量从 list/msgs 改成了 all，
    #    照原样钉 `if (!list.length)` 就当场 ValueError 崩掉（连 ❌ 都不打，退出码 1 ——
    #    判"绿"只看 ❌ 会把崩当成过，这一轮我就这么骗了自己一次）。认形状：`if (!<任何东西>.length)`。
    def _gate(seg, name):
        m = re.search(r"if \(!\w+\.length\)", seg)
        assert m, name + "：这段里找不到「空列表就 return」那道闸 —— 它被改掉了？"
        return m.start()

    c.check("红点画在「空列表就 return」之前（放后面则一条不剩时红点永远挂在上一轮的数上）",
            lw.index("paintWoBadge(") < _gate(lw, "loadWorkOrders"),
            f"paint@{lw.index('paintWoBadge(')} 闸@{_gate(lw, 'loadWorkOrders')}")
    cat = lw[lw.index("} catch (e) {"):]
    c.check("工单读不到时把红点摘掉（拿上一分钟的数喊「还有几条」比不喊更坏）",
            "paintWoBadge(null)" in cat, "")
    c.check("消息计数同样画在早退之前",
            lm.index("paintMsgCount(") < _gate(lm, "loadMessages"), "")
    c.check("红与灰是两套类（.sb-num 带 .show / .sb-cnt 带 .hide），不许合成一个「万能角标」糊语义",
            ".sb-num.show { display: inline-block; }" in full and ".sb-cnt.hide { display: none; }" in full, "")
    c.check("未结判据与驿站端同一个写法（status !== 已解决），两页各一套迟早算出两个数",
            'w.status !== "已解决"' in lw, "")
    c.check("窄屏收文字那句必须放过**两个**角标（特异度压过 .show 就会把红点一起藏掉 —— 驿站端真机抓到过）",
            ".sb-item span:not(.ico):not(.sb-num):not(.sb-cnt)" in full, "")
    return c


def batch_d_token_bar():
    c = Checker()
    title("D. 口令条与既有安全语义：常驻顶栏、只落 sessionStorage、封装仍是一份")
    st = F.read_text(encoding="utf-8")
    bar = st[st.index('<div class="topbar">'):st.index('<div class="stage">')]
    c.check("口令条三段（输入 / 按钮 / 状态）都在顶栏里 —— 左栏每一档都吃它，藏进某一档=切个档满屏读取失败",
            'id="adminToken"' in bar and 'id="saveToken"' in bar and 'id="tokenStatus"' in bar, "")
    c.check("口令仍然只进 sessionStorage（关标签就清、不落盘），不许这轮顺手改成 localStorage 存长期",
            'sessionStorage.setItem(TOKEN_KEY, v)' in st and "localStorage" not in code_of(st), "")
    n_hdr = st.count('headers["X-Admin-Token"]')
    c.check("X-Admin-Token 的加法仍只有一份封装（adminFetch），十几个接口不许各抄一遍 header",
            n_hdr == 1, f"{n_hdr} 处")
    c.check("锁着时暂停轮询那句没被搬丢（免得每 5 秒往控制台喷一次 401）",
            "const _poll = (fn) => () => { if (!adminLocked) fn().catch(() => {}); };" in st, "")
    c.check("#inbox 原来那句 max-height:360px 整族删净（外壳自带滚动，两处都限就是套两层滚动条）",
            "max-height: 360px" not in st, "")
    c.check("口令条的提示指向「两个来源」（服务端设的环境变量 / 启动横幅那串），"
            "不许再只指 .env —— .env 被 .gitignore 收了、云上部署根本没有这个文件（#94 就是把这句纠掉的；"
            "#99 把原来同在这句话里的 403 那支删了，见下面 P3）",
            "启动横幅" in bar and "[管理口令]" in st and "在 .env 加一行" not in st,
            "bar 含横幅=%s ｜全页 .env 加一行残留=%s" % ("启动横幅" in bar, "在 .env 加一行" in st))
    # #96 的两条：窄屏不许"失明"，且被拒/成功两根轴各要有一非文字反馈。
    # ⚠️ 这个文件里 `@media (max-width: 720px)` 有**两处**（第 48 行是 .grid2、第 203 行是三栏外壳），
    #    第一版取 index() 抓到了前面那处 ⇒ 报"块里没有 #tokenStatus"的假红。取最后一处，
    #    并要求块里含 .sidebar —— 万一以后有人调换 CSS 顺序，这条会指出来而不是又去读错的块。
    sel = "@media (max-width: 720px)"
    i = st.rindex(sel)
    depth, j = 0, st.index("{", i)
    while j < len(st):
        if st[j] == "{":
            depth += 1
        elif st[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    narrow = st[i:j]
    rule = narrow.split("#tokenStatus")[-1][:140] if "#tokenStatus" in narrow else ""
    c.check("窄屏那档（含 .sidebar 的那一处）对口令条是**配对**的：块里必须留一条 #tokenStatus 规则"
            "（整条删掉=手机上那行状态彻底没了），而那条规则里不许写 display:none"
            "（#96 原来就是 none，等于让人盲填口令）—— 要收只收宽度",
            ".sidebar" in narrow and "#tokenStatus" in narrow
            and "display: none" not in rule and "max-width" in rule,
            "块里有 .sidebar=%s｜有 #tokenStatus=%s｜那条规则=%r" % (
                ".sidebar" in narrow, "#tokenStatus" in narrow, rule[:60]))
    red = st.count('getElementById("adminToken").style.borderColor = "#e0414a"')
    clear = st.count('getElementById("adminToken").style.borderColor = ""')
    c.check("被拒与成功**两支各有一根**不依赖文字的反馈（少第一根=窄屏被拒毫无反应，少第二根=进去了框还红着）",
            red == 1 and clear == 1, "拒绝那支=%d 处｜恢复那支=%d 处" % (red, clear))

    # ---- #99：口令对了，状态条必须说"成功" ----
    # ⚠️ 票面 P1 写的是「『正在重新拉取』的**同一函数内**必须有成功文案」——实测在 L0 修法下**不成立**：
    #    pending 那句在 initTokenBar 的 click handler 里，成功文案在 adminFetch 里，是两个函数。
    #    §一 自己也解释了为什么不能放同一个：点按钮那一刻七个 loader 还没回，放那儿就是"定时报成功"（M1/M5）。
    #    所以判据拆成两端各钉一次：pending 还在（正控）+ 成功文案只在 adminFetch + click handler 不许抢先宣布。
    def _fn_body(src, head):
        i = src.index(head)
        j = src.index(") {", i) + 2   # 参数表里可能有 `opts = {}`，不能取第一个 `{`
        depth = 0
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return src[i:j + 1]
            j += 1
        return ""

    js = code_of(st)
    bar_fn = _fn_body(st, "function initTokenBar")
    fetch_fn = _fn_body(st, "async function adminFetch")
    pending = "正在重新拉取"
    # 「有效 / 生效」两个词是票面验收口径（状态里要含其一）；在 click handler 里出现它 = 还没回包就宣布成功
    claims = ("有效", "生效")
    c.check("P1 状态条两端各钉一次：pending 那句仍在 click handler 里（**正控**：它不在=判据对着空气转），"
            "成功文案在 adminFetch 里，而 click handler 自己一个字都不许提「有效 / 生效」"
            "（M1 的定时报成功、M5 的直接改文案都死在这一条上）",
            pending in bar_fn and any(k in fetch_fn for k in claims)
            and not any(k in bar_fn for k in claims),
            "handler 含 pending=%s｜adminFetch 含成功词=%s｜handler 抢先宣布成功=%s"
            % (pending in bar_fn, [k for k in claims if k in fetch_fn],
               [k for k in claims if k in bar_fn]))
    i_gate = fetch_fn.find("if (resp.status === 401")
    i_http = fetch_fn.find('throw new Error("HTTP "')
    i_ok = fetch_fn.find('if (getAdminToken()) setTokenStatus("✅ 口令有效')
    i_ret = fetch_fn.find("return resp;")
    i_first = fetch_fn.find("setTokenStatus(")
    c.check("P2 回写落在**成功路径**上：401/403 那支之后、非 2xx 抛异常之后、return 之前，"
            "且自带 getAdminToken() 那道保险（没填口令时不许出现「口令有效」）；"
            "整个封装里**第一次** setTokenStatus 也得在闸之后（M2 那种「进门先报成功」就死在这半句）",
            -1 < i_gate < i_http < i_ok < i_ret and i_gate < i_first,
            "401@%d http@%d 成功回写@%d return@%d｜首个 setTokenStatus@%d"
            % (i_gate, i_http, i_ok, i_ret, i_first))
    dead = "没设管理口令"
    c.check("P3 那支走不到的 403 话术连它的错误解释一起删净（admin_guard 自 #94 起只回 401；"
            "而那句把『没配口令』说成服务端坏了，现在是错的）—— 负向判据自己带一个 must-hit 样本",
            dead not in js and "被服务端拒绝" not in js and "口令不对" in js and (dead in (js + dead)),
            "JS（剔注释）里 403 那句=%d 处｜401 那句=%d 处" % (js.count(dead), js.count("口令不对")))
    c.check("话术里的状态码跟着实际响应走（那道闸 401/403 都收，硬写『（401）』就会把 403 说成 401）",
            "resp.status === 403" not in js or '口令不对（" + resp.status' in js,
            "闸收 403=%s｜话术用 resp.status=%s" % ("resp.status === 403" in js,
                                                  '口令不对（" + resp.status' in js))
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_57_admin_shell：管理控制台三栏外壳 + 顶栏口令条 + 两种角标")
    print("=" * 60)
    code |= batch_a_shell().summary("A 批（外壳与 id）")
    code |= batch_b_switch().summary("B 批（切视图与 API 档例外）")
    code |= batch_c_badges().summary("C 批（角标语义与顺序）")
    code |= batch_d_token_bar().summary("D 批（口令条与安全语义）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
