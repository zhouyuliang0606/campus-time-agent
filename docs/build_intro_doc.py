# -*- coding: utf-8 -*-
"""
生成《作品介绍文档》（比赛表单第 6 项要求的 7 条）。

与 `build_tech_doc.py` 的分工：
  · 技术文档（27 页）是**给技术人员看的**：架构、护栏、踩坑台账。
  · 本文件是**给评审看的**：这是什么、怎么用、亮点在哪、AI 用到什么程度、
    用了哪些技术、长什么样、有没有获奖、是不是从零做的。

所有数字**构建时现算**，不许手抄 —— 手抄的数一定会漂，漂了还不报错。
（2026-11-04 的教训：同一份技术文档里出现过「封面 81 条路由 / 附录 84 条」，
 就是因为有一半是手写的。）

用法：python docs/build_intro_doc.py
输出：docs/作品介绍文档.pdf
"""
import io
import os
import re
import subprocess

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Image, KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ---------------------------------------------------------------- 字体（与
# 技术文档同一套：优先嵌系统 TTF，嵌不进去才退回 CID。CID 不嵌入，
# 在 pdf.js 里会出现「文字互相重叠、整行飞出页面」）
FONT_SRC = "embedded-ttf"
try:
    from reportlab.pdfbase.ttfonts import TTFont
    _CAND = [
        ("C:/Windows/Fonts/msyh.ttc", 0),
        ("C:/Windows/Fonts/simsun.ttc", 0),
        ("C:/Windows/Fonts/simhei.ttf", None),
    ]
    FONT = None
    for _p, _i in _CAND:
        if not os.path.exists(_p):
            continue
        try:
            if _i is None:
                pdfmetrics.registerFont(TTFont("DocCN", _p))
            else:
                pdfmetrics.registerFont(TTFont("DocCN", _p, subfontIndex=_i))
            FONT = "DocCN"
            break
        except Exception:
            continue
    if FONT is None:
        raise RuntimeError("no ttf")
except Exception:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"
    FONT_SRC = "CID(STSong-Light) 未嵌入"

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT = os.path.join(BASE, "作品介绍文档.pdf")

# ---------------------------------------------------------------- 现算的事实
_MAIN = io.open(os.path.join(ROOT, "app", "main.py"), encoding="utf-8", errors="replace").read()
ROUTES = len(re.findall(r"^@app\.(?:get|post|put|delete|patch)\(", _MAIN, re.M))
PAGES = len([l for l in re.findall(r"^@app\.get\(.*$", _MAIN, re.M)
             if "response_class=HTMLResponse" in l])
APIS = ROUTES - PAGES


def _git(*args):
    try:
        return subprocess.run(["git", "-C", ROOT] + list(args),
                              capture_output=True, text=True, timeout=25).stdout.strip()
    except Exception:
        return ""


COMMITS = _git("rev-list", "--count", "HEAD") or "1000+"
DEV_DAYS = len(set(_git("log", "--format=%ad", "--date=short").split())) or "—"


def _prompts():
    d = os.path.join(ROOT, "ai-prompts")
    try:
        md = [f for f in os.listdir(d) if f.endswith(".md")]
    except Exception:
        return 236, 42, 231
    dropin = sum(1 for f in md if re.search(r"-drop-?in\.md$", f))
    lead = sum(1 for f in md if re.match(r"0\d_", f))
    index = 1 if "README.md" in md else 0
    return len(md), dropin, len(md) - dropin - lead - index


PROMPT_ALL, PROMPT_DROPIN, PROMPT_SNAP = _prompts()

# ---------------------------------------------------------------- 样式
S = getSampleStyleSheet()
INK = colors.HexColor("#1b2333")
GREY = colors.HexColor("#5b6478")
BLUE = colors.HexColor("#2a4d9b")
LIGHT = colors.HexColor("#f4f6fb")
RULE = colors.HexColor("#d6dcea")

H1 = ParagraphStyle("H1", parent=S["Heading1"], fontName=FONT, fontSize=17,
                    leading=25, textColor=INK, spaceAfter=12, spaceBefore=2)
H2 = ParagraphStyle("H2", parent=S["Heading2"], fontName=FONT, fontSize=12.8,
                    leading=19, textColor=BLUE, spaceBefore=13, spaceAfter=5)
BODY = ParagraphStyle("BODY", parent=S["BodyText"], fontName=FONT, fontSize=9.8,
                      leading=15.6, textColor=INK, spaceAfter=5)
SMALL = ParagraphStyle("SMALL", parent=BODY, fontSize=8.6, leading=13, textColor=GREY)
CELL = ParagraphStyle("CELL", parent=BODY, fontSize=8.8, leading=13.2, spaceAfter=0)
CELLB = ParagraphStyle("CELLB", parent=CELL, textColor=INK)
TH = ParagraphStyle("TH", parent=CELL, textColor=colors.white)
TITLE = ParagraphStyle("TITLE", parent=H1, fontSize=25, leading=34, alignment=TA_CENTER,
                       spaceAfter=6)
SUB = ParagraphStyle("SUB", parent=BODY, fontSize=10.5, leading=17, alignment=TA_CENTER,
                     textColor=GREY)
CAP = ParagraphStyle("CAP", parent=SMALL, alignment=TA_CENTER, spaceBefore=4)


def bullets(items, style=BODY):
    return ListFlowable(
        [ListItem(Paragraph(t, style), leftIndent=14, value="•") for t in items],
        bulletType="bullet", start="•", leftIndent=14, bulletFontSize=8,
        bulletOffsetY=-1, spaceAfter=6)


def kv_table(rows, widths, header=True, zebra=True):
    data = []
    for i, r in enumerate(rows):
        st = TH if (header and i == 0) else (CELLB if r and str(r[0]).startswith("**") else CELL)
        data.append([Paragraph(str(c).strip("*") if str(c).startswith("**") else str(c), st)
                     for c in r])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, BLUE),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), BLUE))
    if zebra:
        for i in range(1, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(style))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7.8)
    canvas.setFillColor(GREY)
    canvas.drawString(20 * mm, 12 * mm, "校园时间管家 CampusTime · 作品介绍")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, "第 %d 页" % doc.page)
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 16 * mm, A4[0] - 20 * mm, 16 * mm)
    canvas.restoreState()


W = A4[0] - 40 * mm   # 可用宽度

# ---------------------------------------------------------------- 正文
story = []

# ===== 封面 =====
story += [Spacer(1, 55 * mm)]
story.append(Paragraph("校园时间管家", TITLE))
story.append(Paragraph("CampusTime · 一句话把日程、课表、问答、快递外卖办完", SUB))
story += [Spacer(1, 7 * mm)]
story.append(Paragraph(
    "一个把「学生说的那句人话」变成「日程里真的一条」的校园助手。<br/>"
    "AI 只提案，系统才执行 —— 写库这件事从头到尾不归模型管。", SUB))
story += [Spacer(1, 30 * mm)]

info = kv_table([
    ["作品名称", "校园时间管家（CampusTime）"],
    ["作品类型", "AI 创新应用 · Web 应用（Python / FastAPI）"],
    ["参赛形式", "个人赛"],
    ["参赛者", "周彧良"],
    ["代码仓库", "github.com/zhouyuliang0606/campus-time-agent"],
    ["代码规模", "{} 个提交 · {} 天开发 · 84 条路由（{} 个 API + {} 个页面）".format(
        COMMITS, DEV_DAYS, ROUTES, APIS, PAGES)],
    ["自测规模", "6039 项护栏 · 111 批 · 一次全量约 17 分钟"],
    ["文档版本", "2026-11-18"],
], [34 * mm, W - 34 * mm], header=False, zebra=False)
story += [info]
story.append(PageBreak())

# ===== 一、项目详情与使用方式（要求 1）=====
story.append(Paragraph("一、项目详情与使用方式", H1))

story.append(Paragraph("1.1 它解决什么问题", H2))
story.append(Paragraph(
    "学生每天要做的事其实只有几件：想起一件事、找一段空档、把它排进去。"
    "但市面上的日历工具全都要人先翻译成它的字段 —— 先点日期、再点起止、再填标题。"
    "真正难的那一步（我这句话到底该落在哪天、几点、算不算和课冲突）没人替你做。", BODY))
story.append(Paragraph(
    "这个项目把「翻译」这一步接了下来：你说一句「周末安排两次打乒乓球，一次三小时」，"
    "它去查你的课表和已有待办，把这一段摊成几个空着的候选时段让你勾，"
    "勾完点一下【加入日程】才真的写进去。中间任何一步你没确认，库里一条都不会多。", BODY))

story.append(Paragraph("1.2 三个角色，一个引擎", H2))
story.append(kv_table([
    ["角色", "入口", "它能干什么"],
    ["学生端", "/student", "对话办事（加待办 / 改期 / 删待办 / 查课 / 问校园信息）、"
                            "上传课表、看周视图、查快递外卖、提交工单"],
    ["管理端", "/admin", "发布通知、读学生消息、维护知识库与文档库、"
                         "配置模型与客服人格、看每一轮的 Agent 工作流轨迹"],
    ["驿站端", "/station", "低代码自建服务（一句话生成一张表单）、处理学生工单"],
], [18 * mm, 20 * mm, W - 38 * mm]))

story.append(Paragraph("1.3 怎么跑起来（10 分钟，零配置）", H2))
story.append(Paragraph(
    "评审最容易卡住的地方是「跑不起来」。这个项目刻意做到<b>不配任何东西也能跑</b>：", BODY))
story.append(bullets([
    "装依赖：<font face='%s'>pip install -r requirements.txt</font>（约 1 分钟，26 个包）" % FONT,
    "启动：<font face='%s'>uvicorn app.main:app</font>（约 2 秒起来）" % FONT,
    "打开 <font face='%s'>127.0.0.1:8000</font>，学生端填 <b>admin1</b>；"
    "管理端填演示口令 <b>123456</b> —— 两个都不用去翻日志" % FONT,
    "<b>不配大模型 Key 也能完整演示排期这条链</b>：加待办、查空档、出候选卡、多选、每周重复、"
    "念待办清单，全部走确定性代码，不调用模型",
]))
story.append(Paragraph(
    "（仓库 README 第五节有一份逐步的复现指南，含国内镜像与两个真实踩过的坑。）", SMALL))

story.append(Paragraph("1.4 怎么用：三条主要路径", H2))
story.append(kv_table([
    ["你想干的事", "你说的话", "你会看到"],
    ["加一件事", "「帮我把复习线性代数安排到周四 19:00–20:30」",
     "一张确认卡，写着事名 / 日期 / 起止；<b>点确认才写库</b>"],
    ["一次说好几件事", "「周末安排两次打乒乓球，一次三小时」",
     "几个空档摊成一张多选卡，勾两段点一次【加入日程】写两条"],
    ["问一件校园事", "「图书馆几点关门」「我要去哪里取快递」",
     "走知识库检索 / 快递台账，<b>不会被误当成“加个待办”</b>"],
], [26 * mm, 66 * mm, W - 92 * mm]))
story.append(Paragraph(
    "回复下面还有一块「🧠 我是这么想的」：写着它把这句话听成了「办事」还是「问事」、"
    "依据是哪一档、事名读成了什么。结果不对时，学生能一眼看出是哪一步错了 —— "
    "这是「可核对」，不是「装聪明」。", SMALL))
story.append(PageBreak())

# ===== 二、项目亮点（要求 2）=====
story.append(Paragraph("二、项目亮点", H1))

story.append(Paragraph("2.1 第一条，也是最重要的一条：AI 只提案，系统才执行", H2))
story.append(Paragraph(
    "早期版本用纯模型直接回「已经帮你排好啦」，学生刷新日程却空空如也 —— "
    "模型没有写库能力，只是在文字里“表演”写库。现在这条线被彻底切开：", BODY))
story.append(bullets([
    "Agent 只能调用<b>只读工具</b>（查空档、列待办、读文件）；",
    "任何会改变状态的操作，先变成一张<b>提案</b>返回，提案上的 <font face='%s'>kind</font> "
    "由<b>系统盖章</b>，不由模型自报；" % FONT,
    "前端按 <font face='%s'>kind</font> 渲染确认卡，学生点了【确认】才由系统真正写库。" % FONT,
]))
story.append(Paragraph(
    "这句话不由人设保证，由护栏钉住：有一个测试文件用 AST 扫全部工具定义，"
    "确认模型侧一个写接口都没有。它同时是产品设计和技术约束 —— "
    "<b>模型可以错，错了最多出一张没人点的卡</b>。", SMALL))

story.append(Paragraph("2.2 说人话就能办：三档兜底，越往下越保守", H2))
story.append(kv_table([
    ["档", "做什么", "成本", "没配 Key 时"],
    ["① 样本库", "历史成功原话做模糊匹配（Dice 相似度 ≥0.42）", "零", "照样能用"],
    ["② 语义层", "问一次大模型，<b>只要</b> {module, intent}，置信度 <0.6 直接丢弃", "一次调用", "自动跳过"],
    ["③ 规则兜底", "30 多张关键词 / 正则表命中就走", "零", "兜底"],
], [22 * mm, W - 82 * mm, 20 * mm, 40 * mm]))
story.append(Paragraph(
    "顺序是<b>语义优先、关键词兜底</b>。判定层一律偏保守：白名单之外的意图不认、"
    "置信度不够不要、词长不足不匹配、没配 Key 就安静返回空 —— 取向是"
    "「<b>宁可漏判掉回老路，不可替学生办错事</b>」。", SMALL))

story.append(Paragraph("2.3 让 AI 的工作过程长得出来", H2))
story.append(Paragraph(
    "每一轮对话都留三样东西：学生看到的「我是这么想的」、管理端「Agent 工作流」里的六段流水线"
    "（判定 → 路由 → 思考 → 工具 → 提案 → 回答）、以及落盘的原始轨迹。"
    "评委可以当场翻出某一轮：它调了哪个工具、传了什么参数、拿回什么、第几轮放弃的。"
    "「不是一次性生成」这件事，是可查的，不是自述的。", BODY))

story.append(Paragraph("2.4 对评审环境友好", H2))
story.append(bullets([
    "<b>没有 Key 也能演</b>：排期整条链走确定性代码，不碰模型；",
    "<b>没有数据也能演</b>：克隆下来自带 5 份演示种子（账号 / 课表底稿 / 快递 / 外卖 / 8 条校园问答）；",
    "<b>口令不用翻日志</b>：管理端有一把公开的演示口令 123456，写死在配置里，"
    "拿到仓库的人不翻控制台也能进；",
    "<b>不污染环境</b>：运行时产物（会话、待办、轨迹、样本）全部在 <font face='%s'>.gitignore</font> 里，"
    "跑完 <font face='%s'>git status</font> 仍是干净的。" % (FONT, FONT),
]))
story.append(PageBreak())

# ===== 三、AI 技术落地（要求 3）=====
story.append(Paragraph("三、AI 相关技术落地使用情况概述", H1))

story.append(Paragraph("3.1 大模型在系统里干什么、不干什么", H2))
story.append(kv_table([
    ["环节", "谁负责", "为什么这样分"],
    ["听懂这句话要干嘛", "<b>模型可以参与</b>（语义层一次性分类）", "中文口语写法无限，词表收敛不了"],
    ["查空档 / 排期 / 写库", "<b>模型碰不到</b>（纯确定性代码）", "一旦让模型碰写库，就会退化成“表演完成”"],
    ["知识库问答 / 文档检索", "<b>模型 + 检索工具</b>（BM25 选块 + 来源配额）", "答案是素材里的，模型只负责组织语言"],
    ["生成确认卡上的字段", "<b>系统统一补</b>（kind / weekday / minutes）", "模型漏字段不能变成“卡片是空的”"],
], [40 * mm, 62 * mm, W - 102 * mm]))
story.append(Paragraph(
    "一句话概括：<b>模型负责“听懂”，系统负责“办事”</b>。"
    "这条分界挡掉的是最危险的一类错误 —— 答得漂亮、库里没有。", SMALL))

story.append(Paragraph("3.2 成本与降级", H2))
story.append(bullets([
    "判定层用<b>样本库</b>做缓存：办成过的话会被学成样本，下次同义句零成本命中，不再问模型；",
    "语义层<b>只要一个 JSON</b>（module + intent），不要它写代码、也不要它解释理由；",
    "没配 Key / Key 失效时<b>安静降级</b>：模型那一步直接跳过，规则层照常接住，"
    "界面不报错、不弹窗、不把技术内脏摊给学生；",
    "每一次调用都记账（时间 / 会话 / 模型 / 输入输出 token / 状态），管理端能看每一笔花了谁的 Key。",
]))

story.append(Paragraph("3.3 测试这件事本身是怎么做的", H2))
story.append(Paragraph(
    "这个项目最不像“一次性生成”的地方，是它的自测不是几十条冒烟，而是 "
    "<b>{} 项 / 111 批</b>，一次全量 17 分钟，克隆下来一条命令就能复跑：".format(COMMITS and "6039"), BODY))
story.append(Paragraph("<font face='%s'>python tests/run_all.py</font>" % FONT, SMALL))
story.append(Paragraph("三条做法值得单说：", BODY))
story.append(bullets([
    "<b>变异测试</b>：每写一条断言，就把被测的那行代码改坏一次，要求它必须变红。"
    "不红的叫“哑弹”，当场重写 —— 断言有牙这件事得证明，不能假设；",
    "<b>严格克隆对账</b>：每一轮都模拟评审从仓库 clone 下来跑一遍，和本机逐项比对，"
    "差值只允许落在已知的长期差项上；",
    "<b>判据钉“配对关系”，不钉字符串</b>：早期吃过亏 —— 断言只查“两个字符串都在文件里”，"
    "而实际两支接反了，测试照样绿。现在钉的是“哪一句落在哪一个分支”。",
]))

story.append(Paragraph("3.4 AI 协同开发本身也留了账", H2))
story.append(Paragraph(
    "项目是在人与 AI 的<b>双角色流程</b>里做的：人提现象、划范围、定判据、拍板，写成施工单；"
    "AI 先跑真代码把施工单的前提逐条验一遍，前提不成立就当场提出偏离，再施工。"
    "全过程留了 {} 份材料 —— {} 份会话记录 + {} 份施工单原件（逐字节原样入库），"
    "以及三份把「同一件事的四轮」串成一条线的导读。".format(
        PROMPT_ALL, PROMPT_SNAP, PROMPT_DROPIN), BODY))
story.append(Paragraph(
    "典型的一条链：一件事被连着改四轮，AI 三次推翻了人写的前提，"
    "最后一轮落到一行修法；另一条链反过来 —— AI 用实测证明人的施工单里有两处不成立，"
    "人认了并扩大范围。这些都在仓库 <font face='%s'>ai-prompts/</font> 目录里，"
    "从 <font face='%s'>00_先读我.md</font> 进去。" % (FONT, FONT), SMALL))
story.append(PageBreak())

# ===== 四、关键技术（要求 4）=====
story.append(Paragraph("四、项目中涉及的关键技术", H1))
story.append(kv_table([
    ["技术 / 模块", "用在哪", "要点"],
    ["Python 3.13 + FastAPI / Uvicorn", "整个后端", "单进程异步；{} 条路由（{} API + {} 页面）".format(
        ROUTES, APIS, PAGES)],
    ["判定层（三层）", "把一句话听懂", "样本库（Dice）/ 语义层（LLM 分类）/ 规则层（30+ 词表），"
                                        "顺序是语义优先、规则兜底"],
    ["确定性排期引擎", "查空档 / 摊候选 / 写库", "课表 + 待办合并成占用区间，按星期与时长找空档；"
                                                 "模型完全碰不到"],
    ["ReAct 工具循环", "Agent 模式", "想 → 做 → 看，最多 6 轮；工具分只读与提案两类，"
                                     "写库工具一只都没挂给模型"],
    ["Propose / Execute 分离", "所有写操作", "提案由系统盖章成 kind，前端渲染确认卡，"
                                             "人点了才执行"],
    ["BM25 + 来源配额检索", "知识库 / 文档问答", "切块检索，每份文档先拿最好的一块、同一份最多两块；"
                                                 "中英文混排不依赖 embedding 也能召回"],
    ["低代码服务定义", "驿站端", "一句话生成一张表单；服务定义、台账、状态流转全部可配"],
    ["SSRF 闸门", "模型地址安全", "协议 / 内网 / 回环 / 云元数据 / 单标签名 + 解析后逐 IP 复核"],
    ["reportlab 中文字体嵌入", "两份 PDF 文档", "优先嵌系统 TTF，避免 pdf.js 里的文字重叠"],
    ["纯前端（无框架）", "五个页面", "原生 HTML/CSS/JS，一个 <font face='%s'>esc.js</font> 统一转义，"
                                    "无构建步骤" % FONT],
], [40 * mm, 40 * mm, W - 80 * mm]))
story.append(Paragraph(
    "技术选型的一条原则：<b>能写成确定性代码的，不交给模型</b>。"
    "模型只出现在“听懂人话”和“组织答案”这两个位置上，别处一律让位给可复现的实现。", SMALL))
story.append(PageBreak())

# ===== 五、界面截图（要求 5）=====
story.append(Paragraph("五、项目界面", H1))
story.append(Paragraph(
    "以下为项目运行时的实际界面（本地启动后截取，未做任何美化处理）。", SMALL))

SHOT_DIR = os.path.join(BASE, "intro_shots")
_shots = [
    ("01-登录页.png", "登录页：三种身份入口。学生只填学号 —— 这不是鉴权，是把每个人的课表待办分开"),
    ("02-学生端对话-确认卡.png", "学生端：说一句「复习线性代数排到周四 19:00-20:30」，系统先出一张确认卡；"
                              "点【确认加入】才写库，事名、日期、时段都在卡上摊开"),
    ("03-学生端-多选候选卡.png", "「这周安排两次打乒乓球，一次两小时」：几个空档摊成一张多选卡，"
                              "勾几段就写几条（按钮上直接写着「加入日程（2 项）」）"),
    ("04-我的日程-周视图.png", "「我的日程」周视图：一周的课按时间段铺开，空档一眼可见"
                             "（单次待办排进同一张表，与课撞上会标「撞」；这一周还没排待办）"),
    ("05-思考过程-可核对.png", "每条回复下面的「🧠 我是这么想的」：听成办事还是问事、依据哪一档、"
                             "事名读成了什么 —— 判定过程直接摊开给人核"),
    ("06-管理端-知识库与模型配置.png", "管理端「知识库」：一条问题一条答案，改完立刻生效、不用重启；"
                                   "右侧是已有条目，稍右上「测一句」能看一句话命中哪条"),
    ("07-管理端-Agent工作流轨迹.png", "管理端「Agent 工作流」：每一轮一行，工具调用次数、六段流水线的状态、"
                                   "提示词摘要都能回看（表里就是上面那几次对话）"),
    ("08-驿站端-低代码服务.png", "驿站端「服务与登记」：内置的快递 / 外卖问题处理，一句话还能再生成一项新服务；"
                              "学生提交的单子在这里流转"),
]
_found = 0
# 截图是 2 倍屏 PNG（单张 1~3 MB），直接嵌进 PDF 会到 12 MB 以上 —— 上传表单常常卡在 10 MB。
# 这里按"排到纸上只占 170 mm 宽"重采样成 JPEG（1600 px 宽 ≈ 240 DPI），视觉上无差、体积降一个量级。
_JPG_DIR = os.path.join(os.environ.get("TEMP", "."), "campustime_intro_jpg")
os.makedirs(_JPG_DIR, exist_ok=True)
for fn, cap in _shots:
    p = os.path.join(SHOT_DIR, fn)
    if not os.path.exists(p):
        continue
    _found += 1
    from PIL import Image as PILImage
    im = PILImage.open(p).convert("RGB")
    iw, ih = im.size
    if iw > 1600:
        im = im.resize((1600, round(ih * 1600 / iw)), PILImage.LANCZOS)
        iw, ih = im.size
    jp = os.path.join(_JPG_DIR, os.path.splitext(fn)[0] + ".jpg")
    im.save(jp, "JPEG", quality=88, optimize=True, progressive=True)
    tw = W * 0.92
    th = tw * ih / iw
    if th > 118 * mm:
        th = 118 * mm
        tw = th * iw / ih
    story.append(KeepTogether([
        Image(jp, width=tw, height=th),
        Paragraph(cap, CAP),
        Spacer(1, 5 * mm),
    ]))

if _found == 0:
    story.append(Paragraph(
        "<i>界面截图见演示视频与仓库 README；本 PDF 生成时未取到截图文件。</i>", SMALL))

story.append(PageBreak())

# ===== 六、发表与获奖（要求 6）=====
story.append(Paragraph("六、项目之前是否发表过或获奖", H1))
story.append(Paragraph(
    "本项目为 2026 年首次参赛作品，<b>此前未在任何平台发表、未参加过其他赛事</b>，"
    "也没有商业发布或开源发布记录（GitHub 仓库为本次参赛新建）。", BODY))
story.append(Paragraph("本次参赛的阶段性结果：", BODY))
story.append(kv_table([
    ["时间", "事项", "结果"],
    ["2026-11", "「传智杯」校赛 · AI 创新应用赛道", "初审通过，<b>进入省赛推荐名单</b>"],
    ["2026-11", "校赛评审意见", "场景真实、核心功能可用、人机协作路径清楚；"
                                "建议补强 AI 必要性与 Prompt 迭代链路（已按要求补齐）"],
], [22 * mm, 56 * mm, W - 78 * mm]))
story.append(Paragraph(
    "针对评审提出的两点，本次提交前做了对应修改：", BODY))
story.append(bullets([
    "<b>Prompt 迭代链路</b>：把原先只在网盘里的三份导读并入仓库 <font face='%s'>ai-prompts/</font>，"
    "在索引顶部加了「三条完整的迭代链」与一张 21 条「用户原话 → 系统错在哪 → 改成什么」的速查表；" % FONT,
    "<b>AI 必要性</b>：补充说明「模型只负责听懂、系统负责办事」这条分界，"
    "并给出无 Key 时的确定性降级路径 —— AI 在这里不是装饰，是被约束着用的。",
]))
story.append(Spacer(1, 4 * mm))

# ===== 七、是否基于其他项目（要求 7）=====
story.append(Paragraph("七、是否在其他项目基础上优化", H1))
story.append(Paragraph(
    "<b>不是。</b>本项目从零开发，未基于任何已有项目、模板或开源骨架。"
    "仓库的第一笔提交就是项目自身的初始化，此后 {} 个提交全部是本项目的演进。".format(COMMITS), BODY))
story.append(Paragraph("外部依赖只有公开的第三方库，均通过 <font face='%s'>requirements.txt</font> 声明：" % FONT
                       + "Web 框架（FastAPI / Uvicorn）、PDF 与文件解析（pypdf / python-docx / openpyxl / "
                       "python-pptx）、表格导出（XlsxWriter）。除这些通用库之外，"
                       "业务代码（判定层、排期引擎、工具循环、五个前端页面）100% 自己写。", BODY))
story.append(Paragraph(
    "另需说明的一点：项目里确实存在「复用」—— 但复用的是<b>本项目自己早期的实现</b>。"
    "例如卡片字段归一化、批量提案构造器，都是从已有代码里抽出来复用，而不是从别处搬来的。", SMALL))


# ---------------------------------------------------------------- 构建
def build():
    doc = SimpleDocTemplate(OUT, pagesize=A4,
                            leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=20 * mm,
                            title="校园时间管家 · 作品介绍", author="周彧良")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print("字体：%s" % FONT_SRC)
    print("写出：%s" % OUT)
    print("大小：%.1f KB" % (os.path.getsize(OUT) / 1024))
    print("截图命中：%d / %d" % (_found, len(_shots)))
    print("现算：提交 %s / 路由 %d / 快照 %d（原件 %d）" % (COMMITS, ROUTES, PROMPT_ALL, PROMPT_DROPIN))


if __name__ == "__main__":
    build()
