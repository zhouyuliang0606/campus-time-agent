# -*- coding: utf-8 -*-
"""生成《校园时间管家 CampusTime 技术文档》PDF（≤30 页，中文）。

运行：<managed python> docs/build_tech_doc.py
依赖：reportlab——只有重新生成本 PDF 才需要它（requirements.txt 里没有，跑产品不必装；本机 .venv 实装 5.1.0）。中文用内置 Adobe CJK 字体 STSong-Light，无需外部字体文件。
输出：docs/技术文档.pdf

版本：2026-10-30（同步判定层 / 执行层分离、类型判定重做、提交身份重写、
        错别字纠正与思考过程透明化，其后至 10-30 再推进两次架构级迭代；970 提交 / 5555 项测试）
"""
import io
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, ListFlowable, ListItem
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

# 注册中文 CID 字体（无需外部 ttf）
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
FONT = "STSong-Light"

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "技术文档.pdf")
# 路由条数现读 app/main.py，附录 A 的行数与它对账（手抄的数会过期，对账不会）
_MAIN_PY = os.path.join(os.path.dirname(BASE), "app", "main.py")
_SRC = io.open(_MAIN_PY, encoding="utf-8", errors="replace").read()
ROUTE_COUNT = len(re.findall(r"^@app\.(?:get|post|put|delete|patch)\(", _SRC, re.M))
PAGE_COUNT = len([l for l in re.findall(r"^@app\.get\(.*$", _SRC, re.M)
                  if "response_class=HTMLResponse" in l])
API_COUNT = ROUTE_COUNT - PAGE_COUNT


styles = getSampleStyleSheet()
def S(name, **kw):
    kw.setdefault("fontName", FONT)
    return ParagraphStyle(name, parent=styles["Normal"], **kw)

title_st   = S("t", fontSize=22, leading=28, alignment=TA_CENTER, spaceAfter=6)
sub_st     = S("s", fontSize=12, leading=16, alignment=TA_CENTER, textColor=colors.HexColor("#555555"))
h1_st      = S("h1", fontSize=15, leading=20, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a3c6e"))
h2_st      = S("h2", fontSize=12.5, leading=17, spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#234b86"))
body_st    = S("b", fontSize=10.5, leading=16, spaceAfter=5, alignment=TA_LEFT)
# 注意：代码块也用 CJK 字体――原先用 Courier 时块内的中文会渲染成空白，这里统一成 STSong-Light
code_st    = S("c", fontSize=9, leading=13, backColor=colors.HexColor("#f4f4f4"),
              borderPadding=4, spaceAfter=6)
small_st   = S("sm", fontSize=9, leading=13, textColor=colors.HexColor("#666666"))
cell_st    = S("cell", fontSize=9.2, leading=13)
cellb_st   = S("cellb", fontSize=9.2, leading=13, textColor=colors.HexColor("#1a3c6e"))

def P(t, st=body_st): return Paragraph(t, st)
def H1(t): return Paragraph(t, h1_st)
def H2(t): return Paragraph(t, h2_st)
def CODE(t): return Paragraph(t.replace("\n", "<br/>"), code_st)

def bullets(items, st=body_st):
    return ListFlowable(
        [ListItem(P(i, st), leftIndent=8) for i in items],
        bulletType="bullet", start="・", leftIndent=14, spaceAfter=4)

def table(data, col_w, header=True):
    t = Table(data, colWidths=col_w, hAlign="LEFT")
    style = [
        ("FONTNAME", (0,0), (-1,-1), FONT),
        ("FONTSIZE", (0,0), (-1,-1), 9.2),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1 if header else 0), (-1,-1), [colors.white, colors.HexColor("#f3f6fb")]),
        ("TOPPADDING", (0,0), (-1,-1), 3),
        ("BOTTOMPADDING", (0,0), (-1,-1), 3),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
    ]
    if header:
        style += [("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1a3c6e")),
                  ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                  ("FONTNAME", (0,0), (-1,0), FONT)]
    t.setStyle(TableStyle(style))
    return t

story = []

# ===== 封面 =====
story += [Spacer(1, 40*mm),
          P("校园时间管家 CampusTime", title_st),
          P("技术文档（AI 创新应用挑战赛 ・ 个人赛提交版）", sub_st),
          Spacer(1, 6*mm),
          P("判定层 / 执行层分离 ・ Propose/Execute 确定性架构 ・ AI 协同细粒度开发", sub_st),
          Spacer(1, 30*mm)]
meta = [
    ["项目", "CampusTime / 校园时间管家 Agent"],
    ["定位", "校园 AI 服务助手（学生活端 / 驿站商户侧 / 管理控制台）"],
    ["技术栈", "Python + FastAPI + 原生 HTML/JS + 自写轻量 ReAct Agent + DeepSeek"],
    ["仓库", "github.com/zhouyuliang0606/campus-time-agent（Public）"],
    ["规模", "74 个 API + 7 个页面（共 81 条路由） / 970 个细粒度 commit（截至 2026-10-30） / 153 份 Prompt 快照 + 43 份施工单原件"],
    ["自检", "tests/run_all.py 全绿：通过 5555 项，失败 0 项（共 99 批）"],
    ["文档版本", "2026-10-30"],
]
story += [table([[P(k, cellb_st), P(v, cell_st)] for k,v in meta], [30*mm, 125*mm], header=False),
          PageBreak()]

# ===== 摘要 =====
story += [H1("摘要"),
    P("校园时间管家是一个把分散的校园信息（课表、待办、快递外卖、知识问答）收口到同一个 AI 助手的项目。"
      "它有两条互锁的地基："),
    bullets([
        "<b>Propose / Execute 分离</b>：Agent 只产出「提案」（read-only 工具），"
        "学生没在确认卡上点确认之前，系统一个字节都不写库；确认卡的显示由<b>系统盖章的 kind 标记</b>驱动，"
        "而非模型自觉。",
        "<b>判定层 / 执行层分离</b>：把「听懂人话」和「动手执行」彻底切开。"
        "判定层（样本库 → 语义层 → 关键词兜底）负责把一句话听懂并归类，可以换、可以兜底、可以交给大模型；"
        "执行层（查空档、排期、出卡、写库）由系统独占，模型永远碰不到写入接口。",
    ]),
    P("本文档说明系统架构、这两条核心设计的成因与落地、2026-09-26 起至 10-30 的五次架构级迭代，"
      "以及 AI 协同开发与交付流程。全部代码以细粒度 commit 提交，开发对话以 Prompt 快照形式随代码入库，"
      "可逐提交还原协同过程。"),
    H2("提交清单对照"),
    table([
        ["比赛要求", "本项目落地情况"],
        ["Public 仓库 + 细粒度提交历史", "GitHub Public，970 个独立 commit（截至 2026-10-30），禁止一次性打包"],
        ["AI 对话历史 Prompt 快照", "ai-prompts/ 共 197 个 .md = 153 份 Prompt 快照 + 43 份施工单原件/节选 + 1 份索引，与代码分开提交"],
        ["README 复现指南", "仓库根 README.md（环境 / Key 两种配法 / 启动 / 自检 / 演示话术）"],
        ["5-8 分钟演示视频（含 AI 协同）", "docs/演示视频分镜.md（逐镜 + 解说词，本地录屏）"],
        ["技术文档 PDF ≤30 页", "本文件"],
        ["开源协议", "MIT License（仓库根 LICENSE）"],
    ], [60*mm, 95*mm]),
    PageBreak()]

# ===== 1 背景 =====
story += [H1("1. 项目背景与定位"),
    P("学生在校园里要同时盯课表、待办、取件、各类咨询，信息散落在不同系统。校园时间管家用「一个助手」把这些收口，"
      "分三层："),
    bullets([
        "<b>学生活端（旗舰）</b>：课表时间规划 + 校园问答 + 快递外卖查询。学生说一句话（如「把游泳排到周四 19:00」），"
        "系统算出提案、弹确认卡，点确认才写进日程。",
        "<b>驿站 / 商户侧</b>：监听快递外卖台账变动，主动推送取件提醒与滞留预警，用自定义人格智能回复咨询。",
        "<b>管理控制台</b>：零代码维护知识库、客服人格，并在线配置大模型本身。",
    ]),
    P("三层共享同一个 Agent 引擎（判定 + 协调执行）+ 知识库 + 大模型，避免重复实现。"),
]

# ===== 2 技术栈 =====
story += [H1("2. 技术栈与运行环境"),
    table([
        ["层", "选型", "说明"],
        ["后端", "Python + FastAPI", "把函数变成网页可调用接口；uvicorn 启动"],
        ["前端", "原生 HTML / JS", "无需打包工具，不引入前端框架；确认卡走内嵌 /confirm 页面"],
        ["大模型", "DeepSeek API", "Key 走环境变量 / .env 或管理后台，不写进代码"],
        ["Agent", "自写轻量 ReAct 循环", "不套重框架；确定性分支优先于模型"],
        ["存储", "JSON 文件（app/data）", "由 CAMPUSTIME_DATA_DIR 指向，测试用临时副本"],
        ["测试", "FastAPI TestClient + 自写 Checker", "无需 pytest，评委零额外安装即可跑"],
    ], [22*mm, 45*mm, 88*mm]),
    H2("运行（复现摘要）"),
    CODE("pip install -r requirements.txt\n"
         "cp .env.example .env   # 填入 DEEPSEEK_API_KEY（或启动后在 /admin 在线填，免重启）\n"
         "uvicorn app.main:app --reload\n"
         "# 浏览器打开 http://127.0.0.1:8000\n"
         "python tests/run_all.py   # 全量自检：通过 5555 项，失败 0 项（共 99 批）"),
]

# ===== 3 架构 =====
story += [H1("3. 系统总体架构"),
    P("一次「学生说话 → 出提案 → 确认写库」的请求流："),
    CODE("学生输入\n"
         "  └─> /api/chat\n"
         "        ├─ 【判定层】先分「问 / 办」，再定模块\n"
         "        │     ① 样本库模糊匹配  →  ② 语义层问一次 LLM 只要 {module,intent}\n"
         "        │     ③ 关键词表兜底        （前端也可显式指定 module）\n"
         "        ├─ 确定性分支(3/3a/3b/3c/3d/3c-ter)  命中即出提案，不调模型\n"
         "        │     ├─ is_confirmation+peek_pending  学生回「确认」→ 系统写/删库\n"
         "        │     ├─ wants_clear_timetable         出清空提案+确认卡\n"
         "        │     ├─ wants_add_todo               出待办提案+确认卡\n"
         "        │     ├─ wants_list_todo              纯读，列出待办\n"
         "        │     └─ missing_thing_of             『看不见 X』真查库核实\n"
         "        └─ 都没命中 → 模型(ReAct) 用 read-only 工具产出提案\n"
         "提案 = options[]，每条带系统盖章的 kind\n"
         "  └─> 前端 renderOptions 按 kind 渲染确认卡\n"
         "        └─> 学生点确认 → 系统写库（模型全程不参与）"),
    P("关键点：<b>写库的唯一入口是系统侧的确认动作</b>，模型无论走哪条路都碰不到写入接口。"
      "这是「学生没点头就宣布已经删了/加了」这一类事故的硬防线。"),
    H2("3.1 一次完整请求的时序（以「帮我把游泳排到周四」为例）"),
    table([
        ["阶段", "发生什么", "谁在做", "写库?"],
        ["① 判定", "先分「问 / 办」，再定模块；同一次判定给出 {module=planner, intent=add_todo}",
         "判定层（样本库 → 语义层 → 兜底）", "否"],
        ["② 算账", "读周表，避开已有课程与待办，摊出 2-3 个可行时段",
         "执行层（planner 确定性代码）", "否"],
        ["③ 出卡", "写成 options[]，系统盖上 kind，存入待确认暂存柜（TTL 3 小时）",
         "执行层", "<b>否</b>"],
        ["④ 确认", "前端按 kind 渲染确认卡；学生点【确认】", "前端 + 系统", "<b>此时才写</b>"],
        ["⑤ 回执", "从暂存柜取出提案执行，回「已安排」，周表/月表立刻可见", "执行层", "已写"],
    ], [18*mm, 82*mm, 55*mm, 20*mm]),
    P("注意第 ① 步和第 ③ 步是<b>两件独立的事</b>：判定层只负责「这句话想干什么」，"
      "它给出的结论会被喂给执行层那套老代码，而<b>它自己一步都跨不进执行层</b>。"),
]

# ===== 4 Propose/Execute =====
story += [H1("4. 核心设计一：Propose / Execute 分离"),
    H2("为什么"),
    P("早期用纯模型直接回「已经帮你排好啦」，学生刷新日程却空空如也――模型没有写库能力，只是在文字里「表演」写库。"
      "把「说要做」和「真做了」混在一起，学生无法区分，是最伤信任的故障。"),
    H2("怎么做"),
    bullets([
        "Agent 只调用 <b>read-only 工具</b>（查空档、列待办、读文件）。任何会改变状态的操作，"
        "都先变成一张「提案」返回，绝不在对话轮里直接落库。",
        "提案由<b>系统盖章 kind</b>（如 todo_add / todo_remove / todo_mode / todo_slots / timetable_clear）。"
        "前端只按 kind 渲染对应确认卡――显示与否由系统决定，不由模型自报。",
        "提案先存进<b>待确认暂存柜</b>（app/agent/pending.py：内存 + 磁盘双写、3 小时 TTL）。"
        "学生点【确认】后系统才取出来执行；点【取消】或过期则一切不变。",
    ]),
    P("「给 Agent 的提案做标记、前端识别后渲染」的落地形态是 kind，且必须由系统盖，不能由模型盖"
      "（否则「显不显示弹窗」的决定权又回到模型手里，正是「经常不弹弹窗」的成因）。"),
]

# ===== 5 判定层/执行层 =====
story += [H1("5. 核心设计二：判定层 / 执行层分离"),
    P("本项目最重要的一条设计，也是它和「随便套个 LLM 问答」的根本区别。"),
    H2("5.1 它要解决的问题"),
    P("学生在报障时说了一句一针见血的话："),
    CODE("「很多的字都是接不住的，你现在能接住的都是我测试给的。」"),
    P("过去每报一次障就往词表里加一条：<b>planner.py 里堆到 30 多张</b>关键词/正则表，"
      "<b>router._QUICK_MAP 还有 50 多个</b>硬编码词，全是 <i>if keyword in text</i> 这种朴素子串匹配。"
      "这是在<b>拿字符串匹配干语义理解的活</b>――中文口语的写法是无限的，"
      "「你说一句我补一词」永远慢一拍，词表加多长都收敛不了。"),
    H2("5.2 解法：把「听懂」和「执行」切开"),
    CODE("┌─ 判定层（可换、可兜底、可以交给模型）────────────────────┐\n"
         "│  ① 样本库   历史成功原话 Dice 模糊匹配（零成本、离线可跑）      │\n"
         "│  ② 语义层   问一次大模型，只要一个 JSON {module, intent}       │\n"
         "│  ③ 规则兜底 30 多张词表 + _QUICK_MAP（只在 ①② 都没结果时才轮）│\n"
         "└────────────────────────────────────────────────────────┘\n"
         "┌─ 执行层（系统独占，模型永远碰不到）──────────────────────┐\n"
         "│  查空档 ・ 排期 ・ 出候选卡 ・ 写库                            │\n"
         "└────────────────────────────────────────────────────────┘"),
    H2("5.3 为什么这条分离没有破坏 Propose / Execute 的地基"),
    P("语义层那个大模型干的活<b>只有一件</b>：把这句话听懂，输出结构化意图。"
      "它拿不到任何写库接口，也碰不到排期算法。拿到 JSON 之后，查空档、摊候选、出卡、写库"
      "走的<b>还是原来那套确定性代码</b>。所以「AI 只提案、系统才执行」一条不破――"
      "变的只是「听懂人话」这一步不再由正则独占。"),
    H2("5.4 2026-09-27：顺序倒成「语义优先、关键词兜底」"),
    table([
        ["档", "做什么", "成本", "没配 Key 时"],
        ["① 样本库", "历史成功原话 Dice 模糊匹配（阈值 0.42，词长 <4 不匹配）", "零", "照样能用"],
        ["② 语义层", "问一次 LLM，只要 {module, intent}，置信度 <0.6 丢弃", "一次调用", "自动跳过"],
        ["③ 规则兜底", "词表命中就走", "零", "兜底"],
    ], [22*mm, 78*mm, 22*mm, 33*mm]),
    P("此前是「关键词第一、语义兜底」，结果是<b>判定权仍握在词表手里</b>，"
      "学生一换说法就掉回模型。倒过来之后，类型（module）与意图（intent）由<b>同一次判定</b>一起给出，"
      "`_QUICK_MAP` 从「第一判据」降为「兜底」。"),
    H2("5.5 为什么语义层可以反过来复核规则"),
    P("规则判了「要办事」，可这句话其实是个问句，或者人就在问答型栏目里（校园问答 / 快递 / 外卖，不含 planner）――"
      "这时让语义层复核一次。但否决闸门只在<b>两种正面肯定</b>下才生效（这句带提问信号，或语义层明确说 chat），"
      "<b>不是「结论不同就否决」</b>：样本库是模糊匹配，误否决一张本来就该出的卡，比漏判更让学生恼火。"
      "多义词（「安排」既是名词又是动词）是关键词表的天生盲区，补词补不好，用这招才治得住。"),
    P("<b>取向：宁可漏判掉回老路，不可替学生办错事。</b>"
      "白名单之外不收、置信度不够不要、没配 Key 就安静返回 None。"),
]

# ===== 6 确定性路由 =====
story += [H1("6. 确定性意图路由（/api/chat）"),
    P("为减少「模型自由发挥」导致的不可控，能在代码里定死的意图都收归确定性分支，模型只处理真正开放的问答。"),
    table([
        ["分支", "触发", "系统行为", "写库?"],
        ["确认执行", "is_confirmation + 有 pending", "按暂存提案写/删库", "是（确认后）"],
        ["清空课表", "wants_clear_timetable", "出清空提案 + 确认卡", "否（等确认）"],
        ["加/删/改待办", "wants_add_todo / remove / retime", "算时间 + 出确认卡", "否（等确认）"],
        ["读待办", "wants_list_todo", "list_todos() 真查并念出", "否（纯读）"],
        ["看不见 X", "missing_thing_of", "真查库核实，在/不在如实说", "否"],
    ], [26*mm, 46*mm, 60*mm, 23*mm]),
    P("「不弹弹窗」的根因通常是：一句话没被任何确定性分支接住、掉进模型，模型只回纯文本（无 options/kind），"
      "确认卡自然不来。因此<b>扩大确定性分支的覆盖面</b>是修复此类问题的主战场；"
      "而扩大覆盖面靠的是判定层，不是继续加正则。"),
]

# ===== 7 关键迭代 =====
story += [H1("7. 关键迭代：五次架构级改动（2026-09-26 → 10-30）"),
    P("这里挑的是<b>改变「判定层 / 执行层」边界</b>的那五次；其余较大的功能与工程改动"
      "（知识库分块检索 #40、流式 #07、时区收口 #56、可观测性 #64）"
      "在第 8/10 节与 ai-prompts/ 索引里逐单可查。"),
    H2("7.1 第一次：引入判定层（09-26）"),
    P("问题：学生反馈「很多的字都是接不住的，你现在能接住的都是我测试给的」。"
      "研判确认 planner.py 已堆 30 多张词表、router 里 50 多个硬编码词，"
      "属于「用字符串匹配干语义理解的活」，加词无法收敛。"),
    table([
        ["文件", "改动"],
        ["app/agent/intent.py", "新增：意图白名单 + 问句识别 + 样本库（Dice 模糊匹配）+ 语义层（LLM 只读意图）"],
        ["app/agent/router.py", "关键词落空时先查样本库，再问语义层"],
        ["app/main.py", "/api/chat 接入语义兜底，规则接不住时不再直接掉模型"],
        ["tests/test_08_intent_fallback.py", "新增第八批护栏，覆盖样本匹配、语义闸门、降级路径"],
    ], [42*mm, 113*mm]),
    H2("7.2 第二次：类型判定整体重做（09-27）"),
    P("<b>起因</b>：学生截图反馈「校园问答」栏问「我要去哪里取快递」竟出了待办候选卡。"
      "第一次修复只堵了那一句话，学生随即指出「<b>你只是修了这一个，其他类型的区分没改</b>」――"
      "问题不在那一句，而在「归哪个模块」的判定方式本身。"),
    P("<b>研判</b>：「归哪个模块」由 router._QUICK_MAP 的 50 多个子串匹配说了算，"
      "「想干什么」另由 30 多张表负责，<b>两处互不通气</b>。于是出现「模块是校园问答、意图却是加待办」这种自相矛盾的判定。"),
    P("<b>改动</b>：把<b>类型也收进判定层</b>，与意图做同一次判定，顺序倒成「语义优先、关键词兜底」；"
      "/api/chat 的判定层前移到最前，五条确定性分支共用同一次判定；否决闸门全分支让路；"
      "新增 QUESTION_RE / looks_like_question / _QA_MODULES――<b>先分「问 / 办」，粗信号只决定要不要复核、不下结论</b>。"),
    P("<b>两次自踩自修（值得记录）</b>："),
    bullets([
        "语义层把口语翻成规范话喂老解析器时，<b>规范话把原话里的信息丢了</b>"
        "（「帮我安排周二的游泳」里的「周二」不在语义字段中 → 候选摊满一整周）。"
        "修正：规则认得的句子要用<b>原话</b>解析，规范话只在规则读不出来时兜。",
        "模块顶部 <i>from X import Y</i> 是<b>导入时绑定</b>的：Router 在 __init__ 里建死客户端后，"
        "打桩/换配置都传不进来，语义层会<b>安静地</b>退回关键词表（异常被 except 吞掉）。"
        "修正：按需在函数内 import 才看得到补丁。",
    ]),
    H2("7.3 第三次：错别字纠正与思考过程透明化（09-30）"),
    P("<b>起因</b>：学生报障截图一句话――「<b>没有展示思考过程，没有修改错别字</b>」。"
      "他打的是「中文12点的车」（「中文」是「中午」的笔误），管家回的卡片写着「中文的车」，"
      "错别字一字未改，而且「12点」也整段丢了。"),
    P("<b>研判</b>：这句话只有两条，但每条都是一整类问题。查下去发现三个独立根因，"
      "其中第一个与「错别字」无关，却比错别字严重得多："),
    table([
        ["层", "问题", "后果"],
        ["样本库", "模糊匹配<b>命中就把整条字段抄走</b>", "「我想去健身」撞上「…游泳…」(0.73) → 卡片写「游泳」；"
         "「练会儿钢琴」撞「…吉他」(0.82) → 「练吉他」。<b>学生说 A，系统回 B，且看不出错在哪</b>"],
        ["规范话", "to_canonical 只拼 start and end", "学生只说一个起点（「12点的车」）→ 整个钟点被悄悄丢掉 → "
         "规范话退回原话，模型明明读懂了也白读"],
        ["规则层", "_normalize_clock 只认「读音对、写法对」的口语", "「中文」不是时间词、擦标题擦不掉 → 整串留在标题里"],
    ], [22*mm, 58*mm, 75*mm]),
    P("<b>改动</b>："),
    bullets([
        "<b>样本库命中分两档</b>（_SIM_FULL = 0.9）：几乎一字不差才算「全命中」、可复用字段；"
        "0.42~0.9 只是「像」，<b>只借 module/intent、字段一律清空</b>，要事名的意图继续问模型现读。"
        "模糊匹配只配定路线，不配定内容。",
        "<b>to_canonical 补 elif start</b>：单独的起点也拼进规范话（end 缺失交给 planner 按默认时长补）。",
        "<b>规则层新增 _fix_typo_time_words</b>（时间词的形近笔误纠正），接在 _normalize_clock 开头，"
        "一处生效两条路（抠标题 + 抠时段）。<b>三道闸门</b>缺一不可：",
    ]),
    bullets([
        "① 位置上是时间词的先原样收下 ―― 不然会<b>连环改</b>：「今田晚上有课」→「今夜里上午课」"
        "（今天 / 今晚 / 今夜互相都只差一个字）。",
        "② 长得像时间词的正经词不许动（_NOT_TIME_WORDS + 星期几）―― 「上班」和「上午」只差一字，"
        "不拦就会把「加个上班打卡」纠成「上午」再擦掉、变成「打卡」，<b>悄悄少一个词</b>，比报错难发现得多。",
        "③ 必须处在时间语境（后面紧跟钟点或星期）才纠 ―― 取向是<b>宁可漏纠</b>："
        "没钟点的笔误（「明夭交电费」）交给语义层，这条兜底只为「离线演示也别把错字写进日程」。",
    ]),
    P("<b>思考过程怎么露出来</b>：/api/chat 改成<b>薄壳</b>，主体移入 _chat_impl(req, meta)，"
      "在出口统一插一条「我把这句听成 <b>办事 / 问事</b>｜依据：哪一档｜事名读成了什么」。"
      "为什么必须单出口 ―― _chat_impl 有一千多行、<b>几十个 return</b>，挨个 return 去加必然漏"
      "（实测就漏了「追问」那条，而那正是学生说错话时最该看到解释的）。"
      "meta 走参数而不用全局量：同一时刻可能有多个请求在跑，全局量会把 A 学生的判定写进 B 学生的回复。"),
    P("学生端把轨迹<b>默认摊开</b>（可在设置里关）――这是学生核对「它有没有听错」的唯一凭据，"
      "并在渲染时过滤三处噪声：空的 ReAct 流程标记、与正文重复的「最终回答」、超长的 answer 截断。"
      "顺带把 /classify 的 source_note 与思考轨迹统一到同一张「人话表」（原来各写一张，必然有一处会漂）。"),
    P("<b>补漏：只报了钟点、没说哪天</b>。改完、1398 项全绿之后，拿学生那句原话在真机上再走一遍，"
      "发现<b>只修对了标题、时间还是丢的</b>――回的是「挑方便的勾上」一屏候选时段，"
      "而里面根本没有他说的 12 点。根因是 parse_add_todo 的契约「日期和起始时刻缺一个就返回 None」，"
      "可「只报了钟点」不等于「没说清时间」。<b>最讽刺的是同一个函数下面几行早就写着该怎么做</b>："
      "「学生自己报了准点 → 就着他给的这个点出条，一条就够」，只是这一档一直没人接。"
      "修法：把原来只给「自填时间」用的 span_only_proposal 对外，语义层加待办分支在解析失败时再试它一次；"
      "<b>只在语义层这一路兜，规则层故意不动</b>（那里的钟点未必是这件事的时间）。"
      "这也印证了一条验收习惯：<b>「全绿」不等于「做对了」，一定要用报障原话在真机再走一遍</b>。"),
    H2("7.4 第四次：规则层学会「交出去」（10-28，#76 / #77）"),
    P("<b>起因</b>：规则层的词表越堆越厚，但有一类句子它<b>永远堆不完</b>——"
      "确定性分支算不出来的说法，它要么硬判出一个错的分类，要么装没听见直接掉进模型。"),
    P("<b>#76 让渡口</b>：先盘清「系统会什么」（能力清单），落在<b>缺口</b>上的句子才交给引擎去查，不再硬判；"
      "顺手把不会的那些记进缺口账（intent_gaps.jsonl）。"
      "关键取向：<b>让渡的前提是知道自己不会什么</b>——没有能力清单的让渡，等于把所有句子都推给模型。"),
    P("<b>#77 改期接上文</b>：学生只说「换个时间」时不报事名。"
      "修法是从上文<b>最近且唯一</b>提到的那条待办接住名字；名字抠不出来就<b>不把全部待办摊开</b>——"
      "列二十条让他自己挑，是把选择成本丢回给他。"),
    H2("7.5 第五次：改期从「说假话」到「能办事」（10-28 ~ 10-30，#80 / #81 / #82 / #84 / #86 / #87）"),
    P("这一族是<b>同一个问题的六个截面</b>：学生说要改期，系统要么编一句「已经帮你改好了」，"
      "要么沉默，要么真去改却改坏。六张单按「先说实话、再办成事」的顺序推："),
    table([
        ["单", "症状", "修法"],
        ["#80", "没出确认卡却让学生「去勾选」", "提示词拆条 + 出口兜底；判据挂在<b>模型原文</b>上，不挂在最终布尔值上"],
        ["#81", "新值等于旧值的「空改动」也出卡", "改判「结果三元组是否相同」，并进已有的 unclear 分支，不另造状态"],
        ["#82", "「周末」说了等于没说", "接 _WEEKEND_WORDS 生成候选日；不动 _pick_date（它有 34 处调用方）"],
        ["#84", "改不动就沉默", "empty 出口带 pool_n，把「我没认出来」和「库里真没有」分开说"],
        ["#86", "引擎手上没有能改待办的手", "新增 propose_todo_change(op=retime)；auto_slot 默认 False"],
        ["#87", "同名两条没有出口 / 过去的钟点没有闸", "todo_date <b>只收窄不覆盖</b>；工具层拦 past_slot"],
    ], [12*mm, 45*mm, 78*mm]),
    P("这一节的价值不在「修了六个 bug」，在于它把「<b>说假话 → 说真话 → 能办事</b>」这段推进留在了记录里："
      "#80 / #81 / #82 / #84 全在把「系统以为自己办到了」改成「如实说办没办到」，"
      "#86 / #87 才是真去办。<b>顺序反了，多出来的能力只会让假话说得更像。</b>"),
    H2("7.6 验证"),
    bullets([
        "新增/扩充回归：第八批覆盖类型判定重做（问句识别、语义压过关键词、有/无 Key 双路、端到端不出卡）。",
        "把学生截图里那两句原话（「我要去哪里取凯迪」「我要去哪里取快递」）<b>钉成永久回归</b>，防止复发。",
        "全量 tests/run_all.py：<b>通过 5555 项，失败 0 项（共 99 批）</b>。",
    ]),
]

# ===== 8 测试 =====
story += [H1("8. 测试策略"),
    P("测试不依赖 pytest，只用项目虚拟环境已有的 FastAPI TestClient + 自写 Checker，评委零额外安装即可跑。"),
    table([
        ["批次", "覆盖内容"],
        ["test_01 基础", "健康检查、页面路由、知识库 CRUD、客服人格、通知、没配密钥时不裸 500"],
        ["test_02 上传", "上传与边界防护、掩码回显与「掩码串不被写回」防呆、docx/xlsx/pptx 正文与表格解析"],
        ["test_03 规划", "周表导入与校验、空档避课、待办防撞课、月表聚合、会话历史上限"],
        ["test_04 人设", "三套性格接口、规划 Mock「提议→确认→写入」闭环、性格腔调真注入"],
        ["test_05 学生视图", "只读接口数据契约、主面板四视图标记、既有接口回归护栏"],
        ["test_06 加东西", "提案工具直调、日程面板提案、沙箱隔离"],
        ["test_07 待办意图", "三类「代办」口语走系统分支、空列表分支"],
        ["test_08 意图兜底", "样本库匹配、语义闸门、**类型判定整体重做**、残卡字样永久回归"],
        ["test_09 增删查改", "点名删/盲删、改期换时间、语义层直出卡、确认后落库与旧条移除"],
        ["test_10 标记完成", "标记完成改由系统接管（撤掉模型直调写工具）、三分支 ok/many/empty、问答栏不再静默失败"],
        ["test_11 待办定位", "「第N条 / 裸数字 / 连写23 → [2,3] / 最后一条」、有清单时永不返回 empty、撞时间批量删"],
        ["test_12 批量删", "「都删了」指代上一句清单、批量确认条（一张条一次点头）、「每个删一个」去重（与全删相反）"],
        ["test_13 猜测与证据", "上下文四级台阶、只认结构性事实（精确同名 / 候选业务等价）、不做相似度猜测"],
        ["test_14 语义强弱", "语义层单独猜的 done 是弱信号（让给删待办）、规则层硬信号照旧；打桩必须打到 app.main 模块名"],
        ["test_15 口语样例", "20 句真人口语自主探测（软删/错字/语气词/指代/抱怨/知识库错字）+ 沙箱假绿的暴露"],
        ["test_16 错别字与轨迹", "时间词形近笔误的三道闸门、样本库两档命中、规范话不丢起点、思考轨迹必在（含追问/问答两分支）、只报钟点没说哪天也出条"],
        ["test_intent_classify", "判定层诊断口 /classify 62 项：问/办、模块与意图同判、有/无 Key 双路、只读红线"],
    ], [34*mm, 121*mm]),
    bullets([
        "每个测试文件在<b>独立子进程 + 临时数据副本</b>（sandbox）中运行，绝不污染真实演示数据。",
        "CI 式总账：run_all.py 汇总每批「通过 X 项，失败 Y 项」，有失败退出码为 1。",
        "血泪教训已固化为自检手法：<b>沙箱只套一半等于没套</b>。曾有一个测试上半段露在沙箱外，"
        "每跑一次全量就往真实暂存文件写一张确认卡，界面上凭空多出待办。"
        "现在跑测试前后给几个运行时文件算 md5 对比，必须一字不变。",
    ]),
    CODE("python tests/run_all.py\n"
         "# 总账：通过 5555 项，失败 0 项（共 99 批）全部通过 "),
]

# ===== 9 AI 协同 =====
story += [H1("9. AI 协同开发与交付流程"),
    P("本项目全程在人与 AI 协同下完成。为满足「细粒度提交 + Prompt 快照」要求，约定："),
    bullets([
        "每做一步逻辑改动就<b>单独 commit</b>，绝不在最后一次性打包（本文档版本日为 <b>970 个</b>，此后持续增加）。",
        "每次开发会话写一份 <b>Prompt 快照</b>（ai-prompts/YYYY-MM-DD-&lt;主题&gt;.md），"
        "记录「<b>用户 Prompt → AI 研判 → 改动 → 验证</b>」四段。",
        "快照文件<b>与代码分开提交</b>（单独 commit），评审把任意快照与其 commit 对照即可还原那一步协同过程。",
        "当前 <b>153 份快照</b>（另有 43 份施工单原件/节选，ai-prompts/ 合计 197 个 .md），索引见 ai-prompts/README.md。",
    ]),
    H2("9.1 交付链路本身也被记录"),
    P("仓库推送到 GitHub 的过程同样留了快照（ai-prompts/2026-09-27-github-push-and-contributor-identity.md）："
      "三重独立卡点（代理拦截 → 仓库未创建 → 凭据缺失）的定位与解法，"
      "以及「提交归属到底由什么决定」的取证过程――<b>commit 里只有 name 和 email 两行自由文本，"
      "GitHub 只按 email 反查账号、完全忽略 name</b>。这类基础设施环节的踩坑与推演，"
      "同样属于「AI 协同过程」的一部分。"),
    H2("9.2 协同模式的两个特征"),
    bullets([
        "<b>先取证再动手</b>：每次改动前先读代码、跑测试、考证具体是哪一行在起作用，"
        "避免「症状相同就套上次的修法」（同一句报障在本项目报过 4 次，每次根因都不同）。",
        "<b>架构级判断与补丁级修复分开</b>：当学生指出「你只是修了这一个」时，"
        "不是再补一个正则，而是回到判定方式本身重做（见第 7 节）。",
    ]),
]

# ===== 10 复现 =====
story += [H1("10. 复现指南（README 摘要）"),
    bullets([
        "环境：Python 3.10+（本项目在 3.13 上开发与测试），pip install -r requirements.txt。",
        "密钥（两种任选）：① 启动后在 /admin 的「API 配置」在线填，<b>立刻生效免重启</b>；"
        "② cp .env.example .env 填入 DEEPSEEK_API_KEY。settings.json 已在 .gitignore 排除。",
        "<b>没配 Key 也能跑</b>：自动切到确定性 Mock 模式，排程交互、卡片渲染、知识库问答等离线链路照常演示。",
        "启动：uvicorn app.main:app --reload，打开 http://127.0.0.1:8000。",
        "自检：python tests/run_all.py（临时数据副本，不碰演示数据），预期 5555 项全绿。",
    ]),
    H2("演示话术（都能直接试）"),
    table([
        ["你说", "应该看到"],
        ["帮我把复习线性代数安排到周四 19:00-20:30", "出确认卡；点了确认日程里才出现（没确认不写）"],
        ["我的代办呢", "直接念出真实待办列表（真查库，不是编的）"],
        ["把周四那个瑜伽挪到周五", "出改期卡，确认后旧条目移除、新条目落位"],
        ["健身显示不出来", "系统真去查库核实，如实回答「在的 / 不在」"],
        ["我要去哪里取快递", "走校园问答检索知识库回答，<b>不会</b>被误当成「加个待办」"],
    ], [58*mm, 97*mm]),
]

# ===== 11 局限 =====
story += [H1("11. 局限与后续"),
    bullets([
        "<b>图片/扫描件读不出字</b>：需要接 OCR；目前只支持 txt / pdf / docx / xlsx / pptx 的文字层。",
        "<b>账号体系是演示级</b>：个人库已按学号分目录（app/data/student/&lt;学号&gt;/），"
        "但仍然没有密码与鉴权——身份靠登录时选学号，兜底分支不许删。",
        "<b>长资料走检索而不是整篇直读</b>：上传文档会切块并建 BM25 索引，问答只带命中的块；"
        "整篇直读仍有 6000 字上限，超过就改走 search_docs（不是悄悄截断）。",
        "<b>确定性覆盖面仍可扩大</b>：判定层的语义闸门偏保守（宁漏不误），个别口语说法仍会掉回模型反问。",
        "<b>未配大模型密钥时的降级路径会说错话</b>：离线 mock 对部分输入给出与事实不符的回答"
        "（实测「改期办不成」那句被回成「你周表还是空的」）。演示机已配 Key，正常路径不经过它。",
        "<b>改期不携带备注与分类</b>：改期是「删旧 + 加新」，确认后原待办的 note / category 会丢。",
        "<b>「加一节 ×× 课」在个别句式上会落到加待办</b>：规则判据 wants_add_course 实测为真，"
        "问题出在分支先后顺序（已知缺陷，未在本版修）。",
        "<b>加待办工具不收 repeat</b>：propose_todo_tool 无法一次创建「每周/每天」的重复待办"
        "（重复待办要用候选卡那条路）。",
        "<b>只读工具的查询区间由模型自定</b>：跨周提问时可能只查了一段就答「没有」，"
        "与改期工具的全库视野不完全一致。",
        "<b>演示视频需本地录屏</b>：逐镜脚本见 docs/演示视频分镜.md。",
    ]),
    P("模型仅在开放问答时启用；高可靠操作（写 / 删 / 改）一律系统执行，这条边界不会为了效果而放松。"),
]

# ===== 附录 A =====
# 附录 A 的行名单独成表：条数是判据，不是散文（见下面的 assert）
ROUTES = [
    ["接口", "作用"],
    ["GET /api/health", "健康检查"],
    ["POST /api/chat", "主对话入口：判定层 + 确定性分支 + 模型兜底"],
    ["POST /api/chat/reset", "重置某个会话的历史"],
    ["GET /api/conversation/{sid}", "读会话历史（前端刷新后恢复聊天记录）"],
    ["GET /api/pending/{sid}", "读待确认提案――前端据此渲染确认卡，<b>只读不执行</b>"],
    ["GET /api/timetable", "读学生周表"],
    ["POST /api/timetable/apply", "应用课表（整表替换，需先经过预览确认）"],
    ["POST /api/timetable/clear", "清空课表"],
    ["GET /api/todos", "读待办列表"],
    ["GET /api/todos/month", "月表聚合视图"],
    ["POST /api/todos", "新增待办（确认后）"],
    ["POST /api/todos/batch", "批量写入待办"],
    ["PUT /api/todos/{tid}", "修改待办"],
    ["DELETE /api/todos/{tid}", "删除待办（确认后）"],
    ["POST /api/todos/mode", "保存日程展示模式"],
    ["GET /api/express", "快递只读视图（含实时状态标签），供卡片面板用"],
    ["GET /api/takeout", "外卖只读视图，供卡片面板用"],
    ["GET /api/student-personas", "三套学生助手性格预设"],
    ["POST /api/student-message", "学生每轮对话上报（管理员收件箱数据源）"],
    ["GET /api/notices", "学生端读通知"],
    ["POST /api/upload", "上传文件并提取文字"],
    ["GET /api/uploads", "上传文件列表"],
    ["GET /api/uploads/{fid}", "读某个文件的提取文字（AI read_uploaded_file 走这里）"],
    ["GET /api/uploads/{fid}/download", "下载上传原件"],
    ["GET /api/workorders", "读学生工单"],
    ["POST /api/workorders", "提交工单（学生确认上报后才生成）"],
    ["POST /api/workorders/{wo_id}/status", "修改工单状态（管理端）"],
    ["GET /api/persona", "读驿站客服人格"],
    ["POST /api/persona", "写驿站客服人格"],
    ["GET /api/admin/kb", "列出知识库"],
    ["POST /api/admin/kb", "新增知识库条目（零代码维护）"],
    ["PUT /api/admin/kb/{index}", "修改知识库条目"],
    ["DELETE /api/admin/kb/{index}", "删除知识库条目"],
    ["GET /api/admin/settings", "读大模型配置（<b>密钥掩码输出</b>）"],
    ["POST /api/admin/settings", "写大模型配置（<b>掩码串防呆</b>，不覆盖真密钥）"],
    ["POST /api/admin/settings/test", "在线连通自检"],
    ["POST /api/admin/notify", "发布通知（支持附件）"],
    ["GET /api/admin/messages", "学生消息收件箱"],
    ["POST /classify", "判定层诊断口（<b>只读</b>）：摊开 is_question / module / intent / confidence / 是哪一档判的"],
    # —— 身份与登录 ——
    ["POST /api/login", "学生登录：只认 accounts.json 名单里的学号，没有密码"],
    ["POST /api/logout", "退出登录：只把标签摘掉，不清任何数据（下次登进来课表待办都还在）"],
    ["GET /api/whoami", "当前身份：刷新后仍知道「这是谁的库」，不靠前端那份显示用副本"],
    ["GET /api/upstream", "学生端要显示的那一句话：你的请求会被送到哪台服务器"],
    # —— 学生自己的上报 ——
    ["GET /api/my/workorders", "学生查自己报过的工单（快递面板那块「我提交过的工单」进度的唯一来源）"],
    ["GET /api/my/reports", "工单 + 自建服务登记合成一张表，附进度（#54）"],
    # —— 归档（软删，#29）——
    ["POST /api/workorders/{wo_id}/archive", "归档 / 放回一条工单：台账一个字节不少，只多/摘 archived"],
    ["POST /api/admin/messages/{mid}/archive", "归档 / 放回一条学生消息（软删，要口令）"],
    # —— 驿站与后勤（#54 分流 / #39 可删）——
    ["GET /api/station/workorders", "驿站看到的报障列表（倒序，最新在前）"],
    ["GET /api/station/workorders/archived", "驿站「已处理 N 条」点开后的明细（只有读，没有写）"],
    ["POST /api/station/workorders/{wo_id}/status", "驿站处理完改状态——与管理端同一个 store 函数，两个视图不会各说各话"],
    ["GET /api/station/services", "驿站端服务列表：只列归驿站的（后勤的报修不进驿站台账，反之亦然）"],
    ["POST /api/station/services", "一句话 → 服务定义 → 过白名单 → 落库成为可用服务"],
    ["DELETE /api/station/services/{sid}", "删一份服务定义（#39-A：低代码只能一句句长，建错了得能收）"],
    ["GET /api/station/services/{sid}/records", "某服务已登记的记录（服务不存在回 404，不返回空列表装「什么都没发生」）"],
    ["POST /api/station/services/{sid}/records", "登记一条：按服务定义把字段存进台账"],
    ["POST /api/station/services/{sid}/records/{rid}/status", "把一条登记记录标成处理中 / 已解决"],
    ["DELETE /api/station/services/{sid}/records/{rid}", "删一条登记记录（#39-B，真删——台账里那条就没了）"],
    ["POST /api/custom/records", "学生点了确认卡后，<b>系统</b>把这条登记写进自建服务台账"],
    ["GET /api/admin/service-records", "自建服务的登记台账（管理端「后勤报修」那一档的数据源）"],
    ["POST /api/admin/service-records/{sid}", "后勤自己登记一条（电话报修、巡楼看到的坏点，没有学生参与）"],
    ["POST /api/admin/service-records/{sid}/{rid}/status", "后勤改状态：与驿站那个口同一份实现"],
    ["POST /api/admin/services", "管理端建服务：一律归后勤（#54 分流的下半场）"],
    # —— 通知可撤回 ——
    ["DELETE /api/admin/notices/{nid}", "撤回一条已发通知（#39-D：发错字、发错人群，以前只能眼看着挂着）"],
    # —— 知识库与文档（#40 / #46 / #55）——
    ["POST /api/admin/kb/probe", "试检索探针：管理员自己验「这句话学生问得到答案吗」"],
    ["POST /api/admin/kb/split", "一句话 → 模型按句意拆出的检索关键词（只产建议，不写库）"],
    ["GET /api/admin/docs", "知识文档列表：文件名 / 字数 / 块数 / 索引状态 / 时间"],
    ["POST /api/admin/docs/{fid}/reindex", "按已经存下来的文字重新切块、给老文档补建索引"],
    ["DELETE /api/admin/docs/{fid}", "删一份知识文档，连带它的分块索引"],
    # —— 用量与轨迹（#64）——
    ["GET /api/admin/usage", "用量汇总（管理台那张「用量统计」卡片的数据源）"],
    ["POST /api/admin/usage/clear", "清空用量流水（演示前想从零计数）"],
    ["GET /api/admin/trace", "Agent 工作流列表（只读，不含完整轨迹，点开才取）"],
    ["GET /api/admin/trace/{row_id}", "取某一轮的完整轨迹（只读）"],
    ["POST /api/admin/trace/clear", "清空轨迹流水：只清这本旁路日志，不碰任何业务数据"],
    # —— 待办 ——
    ["POST /api/todos/items_add", "学生在多选材料卡上勾完、点【加入日程】的落库口（#43）"],
    # —— 页面路由（HTML）——
    ["GET /", "登录页：选择身份（学生 / 管理员）"],
    ["GET /student", "学生端主页（四模块）"],
    ["GET /plan", "学生个人日程（周表看课程与待办，月表看哪天有几件事）"],
    ["GET /settings", "学生助手设置页：改助手名字、挑聊天性格、返回登录"],
    ["GET /confirm", "<b>独立的变更确认页面</b>（确认 UI 本身就是一个单独的页面）"],
    ["GET /admin", "管理员端（通知 + 学生消息 + 知识库 + 人格 + 用量 + 轨迹）"],
    ["GET /station", "驿站 / 商户端"],
]
assert len(ROUTES) - 1 == ROUTE_COUNT, \
    "附录 A 列了 %d 条，app/main.py 实际有 %d 条路由" % (len(ROUTES) - 1, ROUTE_COUNT)

story += [H1("附录 A：路由与接口清单（%d 条 = %d 个 API + %d 个页面路由）" % (ROUTE_COUNT, API_COUNT, PAGE_COUNT)),
    P("下表逐条列出全部路由（<b>只读接口绝不写库</b>；写库接口都要求携带系统侧的确认动作）。"
      "条数由本脚本现读 app/main.py 的路由装饰器得到，与本表行数当场对账——手抄的数会过期，对账不会。"),
    # 说明列用 Paragraph（原来塞裸字符串：实测 10 行超出列宽，最长 117mm > 93mm，
    # 表格里会被裁掉 / 压到隔壁列）⇒ 让它自己折行，路径列 58mm、说明列 111mm（页面正文宽 170mm）
    table([ROUTES[0]] + [[P(x, cell_st) for x in r] for r in ROUTES[1:]],
          [58*mm, 111*mm]),
]

# ===== 附录 B =====
story += [H1("附录 B：数据文件"),
    H2("B.1 种子数据（有意入库，管理台可零代码增改删）"),
    table([
        ["文件", "内容"],
        ["app/data/courses.json", "<b>示例课表 12 节，产品已不读它</b>：周表实际来自 student/&lt;学号&gt;/timetable.json（store.py:1180），45-A 起 schedule.py 也不读了，现在只有测试拿它当种子（tests/_e2e_env.py 的 demo_courses_from_json，test_05/test_06 与几份 e2e/截图脚本都从它还原周表）；里面的 semester 字段全代码零引用。<b>别按它判断演示机上看到的课表</b>，那份在个人库里。"],
        ["app/data/kb.json", "校园知识库（图书馆/食堂/校车/校医院/宿舍/奖学金…）"],
        ["app/data/express.json", "演示快递台账（含一件「滞留 5 天」件，用于预警演示）"],
        ["app/data/takeout.json", "演示外卖订单（制作中/配送中/待取）"],
        ["app/data/accounts.json", "开放的学生账号名单（#38：只认名单、没有密码）"],
    ], [52*mm, 103*mm]),
    H2("B.2 运行时数据（<b>不入库</b>，已在 .gitignore 设防）"),
    table([
        ["文件", "内容", "为什么排除"],
        ["settings.json", "管理后台填的模型密钥/地址/模型", "含真实密钥"],
        ["persona.json", "驿站客服人格", "运行时数据"],
        ["notices.json", "管理员发布的通知", "运行时数据"],
        ["student_messages.json", "学生对话上报记录", "含隐私"],
        ["uploads.json + uploads/", "上传文件元信息与实体", "含隐私"],
        ["workorders.json", "学生工单", "含隐私"],
        ["pending.json", "待确认提案暂存（TTL 3 小时）", "运行时状态"],
        ["intent_samples.json", "判定层样本库（累积的成功判定样本）", "运行时数据"],
        ["intent_gaps.jsonl", "判定层的缺口账（#76：让渡给引擎时记下的原话）", "运行时数据"],
        ["services.json", "低代码自建服务的定义（内置种子写在 store.py）", "运行时数据"],
        ["custom/", "自建服务的登记台账", "含隐私"],
        ["llm_usage.jsonl", "每次大模型调用的用量流水", "里面写着会话 id，属隐私"],
        ["trace_log.jsonl", "Agent 工作流轨迹（#64）", "含会话 id，比用量账更敏感"],
        ["student/&lt;学号&gt;/timetable.json", "该学生的周表（#32 之后按学号分库）", "个人数据"],
        ["student/&lt;学号&gt;/todos.json", "该学生的待办", "个人数据"],
        ["student/&lt;学号&gt;/sessions.json", "该学生的会话历史（最多 20 条）", "个人数据"],
    ], [42*mm, 68*mm, 45*mm]),
    P("数据目录可用环境变量 <b>CAMPUSTIME_DATA_DIR</b> 指向别处――这正是自动化测试的数据沙箱开关："
      "测试把 app/data 复制到临时目录再跑，跑完删掉，真实演示数据一个字节都不动。"),
    Spacer(1, 4*mm),
    P("― 文档结束 ―", small_st),
]

doc = SimpleDocTemplate(OUT, pagesize=A4,
                        leftMargin=20*mm, rightMargin=20*mm,
                        topMargin=18*mm, bottomMargin=16*mm,
                        title="校园时间管家 CampusTime 技术文档")
doc.build(story)
print("PDF 已生成：", OUT)
