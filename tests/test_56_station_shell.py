"""#25 单子 S1：驿站端三栏外壳 + 【清空】（只改 station.html，所以护栏只能钉形状与顺序）。

这个文件守的四类事，都是"下一位改一半就会坏"的形状：
  ① 外壳与 id：业务块换了容器，但 15 个 getElementById 硬绑的 id 一个不许动；
  ② 切视图的语义：只用 display 切、DOM 不重建（重建就等于把正在填的表单与临时建议抹掉）；
  ③ 角标那笔补偿账：待处理从首屏挪进左栏后，"别的视图也看得见有几条没结"必须成立
     —— 它挂在两个细节上：paintWoCount 要在"签名没变就 return"那道闸**之前**，
     以及窄屏收文字那条 CSS 的特异度不许把红点一起抹掉（真机 529px 实测抓到的）；
  ④ 【清空】的边界：清的是屏幕上的对话，**不是台账**。
     S1 那一版「不许调 /api/chat/reset」的前提是"station 每轮都是新 session、清服务端是个谎"；
     #30（2026-10-17）把会话固定成一段之后，这个前提**反了** —— 服务端真的存着这段对话，
     于是清空必须两边一起清（见 F 批），只擦屏幕就成了"演一遍清干净"。
  ⑤ 会话载体（F 批）：**sessionStorage**，不是学生端那个 localStorage。
"""
import pathlib
import re

from _harness import Checker, title

code = 0

ST = pathlib.Path("app/static/station.html")

# ⚠️ 负向断言（"某串不许出现"）一律先剔注释再比 —— 本轮我自己又栽了一次：
#    注释里写着"不调 /api/chat/reset"、"不用原生 confirm()"，拿全文去禁就是把说明书当代码判红
#    （本仓第七次；规矩那条在指南：断言剔注释再比）。
def code_of(text: str) -> str:
    """剔掉 HTML 注释、CSS 块注释、JS 行注释，只留可执行的部分。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    t = "\n".join(ln.split("//")[0] for ln in t.splitlines())
    return t

# 移动时不许改动的 id（施工单 3.3 末那张清单，逐个核）
MUST_KEEP_IDS = [
    "personaBox", "refreshPersona", "woList", "woCount", "refreshWo",
    "svcList", "svcCount", "refreshSvc", "svcIdea", "svcGo",
    "chips", "log", "file", "attach", "text", "send",
]


def batch_a_shell():
    c = Checker()
    title("A. 外壳：旧容器彻底删干净、新结构齐、id 一个不少")
    st = ST.read_text(encoding="utf-8")
    c.check("三栏外壳六件套都在（shell/sidebar/main/topbar/stage/composer）",
            all(('class="' + k + '"') in st or ("." + k + " {") in st
                for k in ("shell", "sidebar", "main", "topbar", "stage", "composer")), "")
    gone = [k for k in ("<header", 'class="wrap"', ".wrap {", "header h1", "header p")
            if k in st]
    c.check("header 与 .wrap 连 CSS 一起删（HTML 和样式两处都不许留孤儿）",
            not gone, "、".join(gone))
    missing = [i for i in MUST_KEEP_IDS if ('id="' + i + '"') not in st]
    c.check("15+ 个 getElementById 硬绑的 id 一个都不许丢（改 id 等于把那块功能删了）",
            not missing, "缺：" + "、".join(missing))
    views = re.findall(r'id="view-([a-z]+)"', st)
    navs = re.findall(r'data-nav="([a-z]+)"', st)
    c.check("左栏四颗按钮与四块视图一一对应（多一颗没地方显示、少一颗点了没反应）",
            sorted(views) == sorted(navs) == ["chat", "persona", "svc", "wo"],
            f"views={views} nav={navs}")
    c.check("标题表 NAV_TITLE 的键与按钮 data-nav 同一批（两份表各说各话 = 点进去顶栏还是上一块的名字）",
            set(re.findall(r'(\w+):\s*"[^"]+"', st[st.index("const NAV_TITLE"):st.index("const NAV_TITLE") + 200])) >=
            {"chat", "wo", "svc", "persona"}, "")
    c.check("主色还是驿站那套青绿，没把学生端的蓝抄过来（抄错色是这类改造最常见的整族不一致）",
            "#4f7cff" not in st and "#17b8a6" in st, "")
    c.check("没塞新依赖：只引 esc.js，没 glass.css、没 SVG 精灵、没框架",
            st.count('<script src=') == 1 and "esc.js" in st
            and not any(k in st for k in ("glass.css", "<symbol", "react", "vue")), "")
    c.check("图标用 emoji，不给学生端那套 <use> 引 symbol（引精灵 = 凭空 20 行死代码）",
            "💬" in st and "🛠️" in st and "🧩" in st and "🎭" in st, "")
    return c


def batch_b_switch():
    c = Checker()
    title("B. 切视图：只 display 切、DOM 不重建、输入区只跟「对话」")
    st = ST.read_text(encoding="utf-8")
    sw = st[st.index("function switchNav(view)"):st.index('id="sbNav"')] if 'id="sbNav"' in st else ""
    if not sw:
        sw = st[st.index("function switchNav(view)"):st.index("document.getElementById(\"sbNav\")")]
    c.check("切视图靠 classList（active / no-chatbox），不是 innerHTML 重建",
            "classList.remove(\"active\")" in sw and "nv.classList.add(\"active\")" in sw
            and "innerHTML" not in sw, "")
    c.check("CSS 里两条 display 规则齐：默认藏、active 显（DOM 全程不重建才谈得上\"切回来还在\"）",
            ".nav-view { display: none; }" in st and ".nav-view.active { display: block; }" in st, "")
    c.check("只有「对话」留输入框：toggle no-chatbox 那句 + 隐藏 composer 的 CSS 都在",
            'document.body.classList.toggle("no-chatbox", view !== "chat")' in sw
            and "body.no-chatbox .composer { display: none !important; }" in st, "")
    c.check("进哪块就现拉一次（wo→loadWorkorders / svc→svcTick / persona→loadPersona），"
            "别让人对着上一分钟的数据看",
            'if (view === "wo") loadWorkorders();' in sw and 'else if (view === "svc") svcTick();' in sw
            and 'else if (view === "persona") loadPersona();' in sw, "")
    c.check("顶栏标题跟着换（NAV_TITLE 查表，不是四份 if 各写一遍）",
            "t.textContent = NAV_TITLE[view]" in sw, "")
    c.check("开场默认停在「对话」（上报靠左栏角标兜底）",
            'switchNav("chat");' in st, "")
    return c


def batch_c_badge():
    c = Checker()
    title("C. 角标这笔补偿账（顺序 + 窄屏特异度，真机各抓到过一条）")
    st = ST.read_text(encoding="utf-8").replace("\r\n", "\n")
    lw = st[st.index("async function loadWorkorders()"):st.index('document.getElementById("refreshWo")')]
    i_paint = lw.index("paintWoCount(list.length, open)")
    i_gate = lw.index("if (sig === woSig || woBusy) return;")
    c.check("paintWoCount 必须在「签名没变就 return」那道闸**之前**"
            "（放后面则清单不变时角标永不更新 —— 而跨视图看见未结条数正是挪进左栏后要补的账）",
            i_paint < i_gate, f"paint@{i_paint} gate@{i_gate}")
    c.check("「共 N 条，未结 M 条」与角标收成一处 paintWoCount（原来在早退/正常两支各抄一遍）",
            st.count("function paintWoCount(total, open)") == 1
            and st.count("未结 ${open} 条") == 1,
            f"抄了 {st.count('未结 ${open} 条')} 遍")
    c.check("0 条不挂红点（open > 0 才 show），免得天天报警",
            'num.classList.toggle("show", open > 0)' in st, "")
    cat = lw[lw.index("} catch (e) {"):]
    c.check("读失败那一路：文案改「读取失败」+ 角标收掉 + 签名复位，三件都在 catch 这一段",
            'woCount").textContent = "读取失败"' in cat and 'remove("show")' in cat
            and "woSig = null" in cat, "")
    # 真机在 529px 抓到：收文字那句把角标一起 display:none 掉了（特异度 0,2,1 压过 .sb-num.show 的 0,2,0）
    c.check("窄屏收文字那句必须**放过角标**（`:not(.sb-num)`），红点是这次改造的补偿机制本身",
            ".sb-item span:not(.ico):not(.sb-num)" in st
            and ".sb-item span:not(.ico), .sb-foot" not in st, "")
    c.check("窄屏那条 .sb-num.show 自己声明 display（别只靠 position 把它捞回来）",
            ".sb-num.show { display: inline-block; position: absolute;" in st, "")
    return c


def batch_d_clear():
    c = Checker()
    title("D. 【清空】：清屏幕、不碰台账，也不假装清了服务端")
    st = ST.read_text(encoding="utf-8")
    stc = code_of(st)
    c.check("一颗按钮 + 一句确认（自绘弹框，不用原生 confirm：连点几次会被浏览器拦）",
            'id="clearChat"' in st and 'id="clearMask"' in st
            and "confirm(" not in stc and "window.alert" not in stc, "")
    c.check("界面上必须把「台账不受影响」说出口（不然管理员不敢点）",
            "台账不受影响" in st and "台账一条没动。" in st, "")
    # ⚠️ 这条在 #30 **改了口**（旧版钉的是"不许调 /api/chat/reset"，理由是"每轮都是新 session，
    #     清服务端是个谎"）。会话固定之后那个前提反了：服务端真的存着这段对话，
    #     只擦屏幕反而是另一种谎。新判据在 F 批（顺序、失败不擦、换新 id 三条）。
    #     改口记在这儿，是为了下次它红的时候不必从头查一遍是不是回归。
    c.check("旧版那条「不去调 /api/chat/reset」已按 #30 改口：会话固定后**必须**调它（F 批钉顺序与失败分支）",
            "/api/chat/reset" in stc, "")
    cl = st[st.index("function doClearChat()"):st.index('document.getElementById("clearChat")')]
    c.check("清空只动 #log 那一块，不动任何台账节点",
            "while (log.firstChild)" in cl and "log.appendChild(greetEl)" in cl
            and "woList" not in cl and "svcList" not in cl, "")
    c.check("清完把快捷操作恢复成默认 5 条（上传资料后会被换成 3 条，不清就永远停在建议态）",
            "renderChips(CHIPS);" in cl, "")
    c.check("整块清干净再放回开场白（原来那种「留着第一条、删掉其余」会每次留下一条回执，越清越攒）",
            cl.index("while (log.firstChild)") < cl.index("log.appendChild(greetEl)"), "")
    esc_ = code_of(st[st.index("function openClearDialog()"):st.index("function closeClearDialog()")])
    c.check("弹框计数走 textContent（不为一个加粗开 innerHTML 的口子）",
            'document.getElementById("clearSummary").textContent =' in esc_ and "innerHTML" not in esc_, "")
    c.check("关掉的路有三条：取消按钮、点遮罩、Esc（演示时手停在哪儿都可能）",
            'getElementById("clearCancel").addEventListener("click", closeClearDialog)' in st
            and "if (e.target === clearMask) closeClearDialog()" in st
            and 'e.key === "Escape"' in st, "")
    c.check("快捷操作只有一份实现（renderChips 定义一处，上传那段也用它，没有第二处手写 chips）",
            st.count("function renderChips(") == 1 and "chipsBox" not in stc
            and 'renderChips(["照这份清单写一版取件提醒"' in st, "")
    return c


def batch_e_live_evidence():
    c = Checker()
    title("E. 本轮真机取到的实测值（写死当基线，下次改坏了这些数字就该问为什么）")
    st = ST.read_text(encoding="utf-8")
    # 529px 视口下实测：sidebar 64px、页面不滚、#log 内部滚 2583>255、输入框底 521<=535
    c.check("外壳用 100vh + overflow:hidden 把自己关在视口里（页面不滚靠的是这两句，不是运气）",
            ".shell { display: flex; height: 100vh; overflow: hidden; }" in st
            and ".stage { flex: 1; overflow-y: auto;" in st, "")
    c.check("「对话」这块 flex 撑满、#log 自己滚（施工单原样 `#log{max-height:none}` 会让内容往外撑，"
            "它自己那条「#log 内部滚」的验收就不成立 —— 这里按实测改成 flex）",
            "#view-chat.active { display: flex; flex-direction: column; height: 100%; }" in st
            and "flex: 1; min-height: 0; overflow-y: auto;" in st, "")
    c.check("#log 旧的 max-height:440px 整族删净（外壳自带滚动，两处都限高就变成套两层滚动条）",
            "max-height: 440px" not in st, "")
    return c


def batch_f_fixed_session():
    """#30：驿站端固定一段会话（用户 2026-10-17 澄清「驿站是店员，学生不碰这台机器」⇒ 串味顾虑解除）。

    这一批最容易被写成"钉一句 session_id 完事"，那正好放过两种半吊子：
    ①会话固定了但【清空】还只擦屏幕（F5 全捞回来 = 清了跟没清一样）；
    ②用了 localStorage（两个标签串成一段对话，正是柜台最常见的用法）。
    所以顺序、载体、换新 id 三样分别钉，并配一条"旧写法不许留"。
    """
    c = Checker()
    title("F. 固定会话：sessionStorage 载体 + 清空两边一起清（#30）")
    st = ST.read_text(encoding="utf-8")
    stc = code_of(st)

    c.check("stationSid() 定义一处，且 /api/chat 的请求体真的带上它（不带 = 白固定）",
            stc.count("function stationSid()") == 1
            and "module: \"station\", session_id: stationSid()" in stc, "")
    c.check("载体必须是 **sessionStorage**（不是学生端那个 localStorage）："
            "这台机器是店员的，关窗即「这次值班结束」；而 localStorage 同源共享会把两三个标签串成一段对话",
            "sessionStorage.getItem(STATION_SID_KEY)" in stc and "localStorage" not in stc,
            f"localStorage 出现 {stc.count('localStorage')} 次")
    c.check("选 sessionStorage 的理由写在注释里（不写，下一个人会「顺手统一成 localStorage」）",
            "localStorage 同源共享" in st and "值班结束" in st, "")
    c.check("新 id 的生成只有一份（前缀 st 便于在 sessions.json 里一眼分清）",
            stc.count("function newStationSid()") == 1
            and stc.count('"st" + Math.random()') == 1, "")

    # 下面这几条一律用 find() + 位置比较：锚点找不到时要写成 ❌，不许抛 ValueError 把整批打断
    ic = stc.find("async function doClearChat()")
    cl = stc[ic:] if ic >= 0 else ""
    cut = cl.find("\n    }")
    if cut > 0:
        cl = cl[:cut]
    i_reset, i_wipe, i_fail = cl.find("/api/chat/reset"), cl.find("while (log.firstChild)"), cl.find("if (!ok) {")
    c.check("清空是**先清服务端**：/api/chat/reset 的位置在擦屏幕之前"
            "（反了就是「清失败但屏幕已经擦了」，学生以为清干净了）",
            ic >= 0 and 0 <= i_reset < i_wipe, f"reset@{i_reset} 擦屏@{i_wipe}")
    c.check("清失败那条分支**直接 return**，屏幕一个字都不动，并且话说清「原样保留」",
            0 <= i_fail < i_wipe and "return;" in cl[i_fail:] and "原样保留" in cl, "")
    c.check("清成功必须**换一个新的 session id**（不换 = F5 把历史整个捞回来，学生端那轮的原病）",
            "sessionStorage.setItem(STATION_SID_KEY, newStationSid())" in cl, "")
    c.check("旧那份『只擦屏幕、不调 reset』的 doClearChat 不许留着（必须是 async 版本，两份并存早晚有人改错那份）",
            "async function doClearChat()" in stc and "\n    function doClearChat()" not in stc, "")
    c.check("清空回执要说清两件事：开了一段新的 + 台账一条没动（前者不提，店员会以为还是同一段）",
            "并已开一段新的" in cl and "台账一条没动" in cl, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_56_station_shell：驿站端三栏外壳 + 【清空】")
    print("=" * 60)
    code |= batch_a_shell().summary("A 批（外壳与 id）")
    code |= batch_b_switch().summary("B 批（切视图语义）")
    code |= batch_c_badge().summary("C 批（角标补偿账）")
    code |= batch_d_clear().summary("D 批（清空边界）")
    code |= batch_e_live_evidence().summary("E 批（真机基线）")
    code |= batch_f_fixed_session().summary("F 批（固定会话 + 清空两边一起清）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
