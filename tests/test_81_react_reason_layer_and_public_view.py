# -*- coding: utf-8 -*-
"""#60 的护栏：轨迹要**读起来是 ReAct**（判定层进 trace + 结果收在写侧 + Observation 折单行限高）。

现场：58 号让 `🔧 调用工具` / `📦 工具结果` 能露出来之后，用户看完那 10 行还是说
"没看到在哪体现 ReAct"。露的只有 Act + Observe，**Reason 那半截是空的**
（`engine.py` 的 `🤔 思考` 本来就没有内容字段，空标记又被前端过滤掉了）。

施工单 §〇 的前提我逐条复算过（盘点脚本 `D:\\campustime-scratch\\inv60.py`，
25 只工具在真实副本上各调一次），**两处与单据不一致，本单按实测钉**：

① §2.2 说 `engine.py` 有**两处**要换成 `public_view`。实测进 trace 的只有一处
   （`engine.py:223-230`）；另一处 `:211-217` 是回灌给模型的 `content: str(result)`，
   那一路**必须保持原文** —— 「候选卡已经挂在下面…严禁说已经写好了」正是模型下一步的依据。
   把它一起洗掉不是隐私问题，是把循环改坏 ⇒ C 批两头各钉一条（V2 打其中一头）。
② §四.1 验收写"前三行是 🧭 判定"，可 `read`（🧠 我把这句听成…）本来就占轨迹第 1 行
   —— 单据 §1.1 自己写着"把它作为 step 0 的一条插在轨迹最前面"，两节互相矛盾。
   实测口径：**第 1 行 🧠，紧跟 🧭 若干行，再往下才是引擎的 step**。D4 钉这个次序。
③ 其余核上：`_reason_steps` 那 11 个入参在 `main.py:1758-1800` 一律**无条件**赋值
   （不存在"某条分支才算、别处读到 UnboundLocal"的坑）。

五批的强度不一样，别看混：
  A 形状与边界（读源码）：三处改动各自钉在**唯一**的位置；`read` 那三个覆盖点一个字没动。
  B `public_view` 全工具扫描：sandbox 里真调 6 个模块的每一只工具（实测 23 只，喂真数据、真参数），
    再拿 inv60 抓下来的**逐字** payload 走一张定点对照表（含三条"本来就没内脏词"的控制句）。
  C 引擎的**两路分叉**：一个剧本假货驱动一整轮 —— 同一次工具调用，trace 里是收过的那份，
    模型手里还是原文。两头都要红才算守住（只钉一头，另一头可以随便坏）。
  D 判定层 `reasons`：直调 `_reason_steps` 走全触发矩阵（每条触发都配一条"没触发就不该出现"），
    再打一次真 `/api/chat` 看轨迹头部；外加一条"什么都没判出来就必须是空"的控制。
  E 前端：node 真跑页面里的 `_oneLine` 与 `renderTrace`（复用 test_79 那个 runner，不抄第二份），
    再核那条限高 CSS 的**两半** —— 只有 `max-height` 没有 `overflow-y` 等于把证据裁掉。
"""
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (PROJ_DIR, Checker, make_client, sandbox, title,  # noqa: E402
                     write_express, write_takeout)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
ENGINE = os.path.join(PROJ_DIR, "app", "agent", "engine.py")
PVIEW = os.path.join(PROJ_DIR, "app", "agent", "public_view.py")
STUDENT = os.path.join(PROJ_DIR, "app", "static", "student.html")

TODAY = dt.date.today()
D_PLUS = (TODAY + dt.timedelta(days=2)).isoformat()

# ⚠️ 这张表**故意不从 `public_view._INTERNAL` 导入**：拿被测代码自己的名单去验它自己，
#    等于"它判什么我就验什么"—— 谁把那张表清空，护栏一起绿。
#    这里逐条来自 inv60.txt 抓到的**原始 payload** 与施工单 §2.1 点名的那几个词。
BANNED = (
    "前端", "确认条", "候选卡", "严禁", "不许", "请把", "告诉用户", "告诉学生", "告诉模型",
    "文字里也", "系统提示", "工具说明", "相关度", "原文第", "按相关度排序", "重新调用",
    "请照", "入参", "字段名", "不要擅自", "标了出处", "换个关键词", "已经挂在下面",
    "__proposal__", "__user_view__", '"hint"', '"status"', "list_day_todos",
    "_sem", "module_key", "intent", "deepseek",
)

# 内脏词命中就报出来（B2/D3 共用）
def _hits(text):
    return [w for w in BANNED if w in text]


# 技术底座词：与 `tests/test_38_student_no_tech_leak.py` 的 `FORBIDDEN` **同一口径**（那里是权威表）。
# 这里故意抄一份而不是 import：D 批要钉的是"本单新写的这几行"，
# 拿既有那份表的口径再压一遍新文案，才叫这一单自己的护栏（test_38 全站扫是第二道）。
TECH_BANNED = ("大模型", "DeepSeek", "deepseek", "glm-", "智谱", "OpenAI",
               "API Key", "api_key", "密钥", "管理控制台", "/admin", "模型")


# ---------------------------------------------------------------- 扫真工具（B 批用）
# 参数名 → 一个说得过去的入参（照 inv60.py 那张表；缺了就传空串，工具自己的围栏会兜住）
ARG_SEED = {
    "day": "周三", "weekday": "周三", "date": D_PLUS, "start": "14:00", "end": "16:00",
    "title": "复习数学", "name": "复习数学", "task_name": "做题", "when": "复习数学",
    "need_minutes": 120, "min_minutes": 120, "minutes": 120, "need_hours": 2,
    "query": "图书馆", "text": "图书馆几点关门", "keyword": "复习",
    "code": "A-1234", "pickup_code": "A-1234", "order_id": "T1", "staff": "",
    "note": "", "prefer_day": "周二", "kind": "快递", "desc": "测试", "op": "update",
    "course": "高等数学", "new_start": "10:00", "new_end": "11:40",
    "new_course": "高等数学", "new_location": "教三301", "todo_id": "",
    "change_summary": "把高数挪到十点", "ids_json": "[]",
    "options_json": json.dumps([{"title": "做题", "date": D_PLUS,
                                 "start": "14:00", "end": "16:00"}], ensure_ascii=False),
    "courses_json": json.dumps([{"day": 1, "start": "08:00", "end": "09:40",
                                 "course": "高等数学", "location": "教三301"}], ensure_ascii=False),
}

TIMETABLE = [
    {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
    {"day": 1, "start": "10:05", "end": "11:45", "course": "大学英语", "location": "外语楼204"},
    {"day": 3, "start": "14:00", "end": "15:40", "course": "数据结构", "location": "机房B"},
    {"day": 5, "start": "16:00", "end": "17:40", "course": "概率论", "location": "教一203"},
]


def _sweep():
    """真调每一只工具，返回 [(module, name, 原始对象, str(原始对象)), ...]（在 sandbox 里跑）。

    为什么不能只拿 fixture 打表：fixture 是我抄的，抄的时候就会照着自己脑子里的形状抄；
    真调一遍才知道**今天这些工具往外吐的到底是什么**（#57 那两处偏离就是这么发现的）。
    ⚠️ 第 3 项存的是**原对象**而不是字符串：引擎传的是 `public_view(fn_name, result)`，
       `get_weekly_timetable` 那种返回 list 的工具，喂 `str(list)` 进去测的是另一条路
       （Python repr 用单引号，`_asobj` 根本解不动）—— 那样测出来的"通过"是假的。
    """
    import inspect

    from app import store
    from app.main import REGISTRY

    store.set_current_student("admin1")
    out = []
    for key, entry in REGISTRY.items():
        builder = entry[1] if isinstance(entry, (tuple, list)) else None
        if not callable(builder):
            continue
        tools = builder() or {}
        for name, tool in tools.items():
            fn = getattr(tool, "func", None)
            if fn is None:
                continue
            try:
                params = list(inspect.signature(fn).parameters)
                kwargs = {p: ARG_SEED.get(p, "") for p in params}
                raw = tool.run(**kwargs)
            except Exception as e:                       # 工具自己崩了也要记下形状
                raw = f"<调用失败 {type(e).__name__}: {e}>"
            out.append((key, name, raw, str(raw)))
    return out


# ---------------------------------------------------------------- inv60 抓下来的逐字 payload
def _j(obj):
    return json.dumps(obj, ensure_ascii=False)


PV_FIXTURES = [
    # (名字, 原始 result, 必须留下的事实, 必须消失的东西)
    ("propose_slots",
     "已向前端展示 1 个候选时间段，等学生确认。",
     ["1 个候选时间段"], ["前端", "学生确认"]),

    ("propose_todo_remove（空手回来）",
     _j({"status": "empty",
         "hint": "没找到符合条件的待办。如实告诉学生'没找到'，"
                 "可以用 list_day_todos 看一下那天到底排了什么，或者问学生是哪一天哪一条。"
                 "**不许编一条出来删**。"}),
     ["没找到"], ["不许", "list_day_todos", '"status"', '"hint"']),

    ("propose_todo_tool（新增提案）",
     _j({"__proposal__": {"kind": "todo_add", "title": "复习数学", "date": D_PLUS,
                          "start": "14:00", "end": "16:00", "weekday": "周一", "minutes": 120,
                          "summary": "复习数学｜" + D_PLUS + "（周一）14:00-16:00",
                          "hint": "确认后才会写入。**严禁**在文字里也说已经加好了。"}}),
     ["复习数学", "14:00-16:00"], ["严禁", "确认条", "__proposal__", "文字里也"]),

    ("propose_todo_slots_tool（候选时段提案）",
     _j({"__proposal__": {"kind": "todo_slots", "title": "复习数学",
                          "slots": [{"date": D_PLUS, "weekday": "周一", "start": "09:00",
                                     "end": "10:30", "minutes": 90, "label": "周一 09:00-10:30"}],
                          "hint": "候选卡已经挂在下面，**严禁**说已经排好了。"}}),
     ["复习数学"], ["候选卡", "严禁", "__proposal__"]),

    ("propose_clear_timetable（清空提案）",
     _j({"__proposal__": {"kind": "timetable_clear", "summary": "清空全部 14 门课",
                          "courses": [], "clear_count": 14}}),
     ["清空全部 14 门课"], ["__proposal__"]),

    ("get_weekly_timetable（list，念出来就是 repr）",
     [{"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
      {"day": 1, "start": "10:05", "end": "11:45", "course": "大学英语", "location": "外语楼204"},
      {"day": 3, "start": "14:00", "end": "15:40", "course": "数据结构", "location": "机房B"}],
     ["共 3 条", "高等数学", "周一"], ["{'day'", "course"]),

    ("find_free_slots（list，日期+星期+区间）",
     [{"date": D_PLUS, "weekday": "周一", "start": "11:45", "end": "14:00", "minutes": 135},
      {"date": D_PLUS, "weekday": "周一", "start": "15:40", "end": "22:00", "minutes": 380}],
     ["共 2 条", "11:45-14:00"], ["{'date'", "minutes"]),

    ("list_day_todos（每项自带一句人话 info）",
     [{"info": D_PLUS + "（周一）目前没有安排待办"}],
     ["目前没有安排待办"], ["{'info'", "info\":"]),

    # ↓↓↓ 三条**控制句**：原始 payload 里一个内脏词都没有 —— 它们考的是"别把好人一起打了"
    ("get_day_courses（本来就是人话，多行）",
     "周三 共 3 门课：\n- 08:00~09:40 数据结构（机房B）\n- 14:00~15:40 操作系统（教三402）\n"
     "- 16:00~17:40 概率论与数理统计（教一203）",
     ["数据结构", "机房B", "操作系统", "概率论"], ["\n"]),

    ("list_pending_pickup（本来就是人话，含取件码）",
     "你有 2 件待取：\n- ⚠️【滞留 9 天】《算法导论》图书｜取件码 A-1234｜菜鸟驿站-北区｜请尽快取，避免退回！\n"
     "- ⚠️【滞留 35 天】运动鞋一双｜取件码 B-5678｜菜鸟驿站-北区｜请尽快取，避免退回！",
     ["A-1234", "《算法导论》图书", "B-5678"], ["\n"]),

    ("工具自己的参数围栏（`Tool.run` 的拒绝句）",
     "调用 propose_slots 时参数不对：missing options_json。请照工具说明重新调用。",
     ["propose_slots"], ["请照", "工具说明", "重新调用"]),

    ("search_kb（检索语料里全是给模型的指令）",
     "问答条目里没有直接对应的问题，但管理员上传的文档里有这些段落：\n\n"
     "在文档里找到以下相关段落（按相关度排序，可能来自多份文档，每段都标了出处；"
     "不同文档说法不一致时要把两种都讲出来，不要擅自选一个）：\n\n"
     "[1]《学生资助与考试周规定-测试样本2.txt》第 6 块（原文第 4061 字起，相关度 3.09）：\n"
     "补办学生证需缴纳工本费二十元。\n\n可以在下一轮换个关键词再查一次。",
     ["补办学生证", "二十元"], ["按相关度排序", "标了出处", "相关度", "原文第",
                              "不要擅自", "换个关键词", "以下"] ),

    # ① 机制钉子：工具作者最懂怎么念，给了就直接用（目前没有工具用它）
    ("__user_view__ 那条机制",
     _j({"__user_view__": "这周查到 4 门课", "raw": "给模型的一长串"}),
     ["这周查到 4 门课"], ["__user_view__", "raw"]),
]


def batch_a_shape():
    c = Checker()
    title("A. 形状与边界：三处改动各钉一个唯一位置，`read` 那三个覆盖点没被伸手")
    main_src = open(MAIN, encoding="utf-8").read()
    eng = open(ENGINE, encoding="utf-8").read()
    stu = open(STUDENT, encoding="utf-8").read()

    c.check("A1 `_reason_steps` 与 `_read_step` 各只定义一处（同一件事抄两份 = 两份真相）",
            main_src.count("def _reason_steps(") == 1
            and main_src.count("def _read_step(") == 1)
    # ⚠️ 这里一律用 `find()` 不用 `index()`：锚点没了要的是**一条红**，不是一个崩（崩了后面几批看不到数）
    i_read = main_src.find('meta["read"] = _read_step(')
    i_reas = main_src.find('meta["reasons"] = _reason_steps(')
    c.check("A2 记录点只有一处、而且紧跟在 `read` 之后（`_chat_impl` 有几十个 return，"
            "逐个补必漏 —— 记在 meta 里、出口拼一次才算覆盖全部）",
            main_src.count('meta["reasons"] = _reason_steps(') == 1 and -1 < i_read < i_reas)
    c.check("A3 `read` 那 3 个覆盖点一个字没动（§七.2：`reasons` 与 `read` 互相独立，"
            "覆盖 `read` 不该影响它）",
            main_src.count('meta["read"] =') == 4     # 1 处初记 + 3 处覆盖
            and main_src.count('meta["reasons"] =') == 1)
    c.check("A4 出口两头各拼一次，而且判空**不挂在 `read` 上**"
            "（挂在 `read` 上 = `read` 万一没设，reasons 跟着静默消失，症状最难查）",
            'head = ([read] if isinstance(read, dict) else []) + reasons' in main_src
            and "if head and isinstance(resp, dict):" in main_src)
    c.check("A5 `reasons` 每条都不占轮次（`step: 0`）且档名统一是 🧭 判定 —— "
            "占轮次就会把引擎那三步的编号顶歪",
            '"step": 0, "phase": "🧭 判定"' in main_src)
    c.check("A6 引擎里 `public_view` 只出现在 trace 那一处；回灌给模型的那一路仍是原文 "
            "`content: str(result)`（§〇①：单据说改两处是错的，改两处就把循环改坏了）",
            eng.count("public_view(fn_name, result)") == 1
            and '"content": str(result),' in eng
            and "public_view" not in eng.split('"content": str(result)')[0].split("messages.append")[-1])
    c.check("A7 原始 600 字**只进服务端日志**，而且过 DEBUG 闸（塞进 trace 等于把内脏又送到浏览器）",
            'if DEBUG:' in eng and 'print(f"[tool-raw] {fn_name}: {str(result)[:600]}")' in eng
            and '"result": str(result)[:600]' not in eng)
    c.check("A8 不碰思维链：全项目没有 `reasoning_content`（§六.1：不上 reasoner，CoT 不可复现）",
            "reasoning_content" not in eng
            and "reasoning_content" not in open(os.path.join(PROJ_DIR, "app", "llm", "gateway.py"),
                                                encoding="utf-8").read())
    # 前端那两处：折行函数 + 限高容器
    c.check("A9 `_oneLine` 全页只定义一处、而且**真的用在工具结果那一句上**"
            "（定义了不用 = 抓来跑的不是真在跑的那行）",
            stu.count("function _oneLine(") == 1
            and 'an += (an ? " → " : "") + _oneLine(t.result);' in stu)
    c.check("A10 行装进了 `.trace-body` 这个**容器**，`<summary>` 留在容器外面"
            "（限高加在 `.trace` 上会让标题跟着一起滚走）",
            stu.count('<div class="trace-body">') == 1
            and '<summary>🧠 我是这么想的</summary>' in stu)
    css = re.search(r"\.trace \.trace-body \{([^}]*)\}", stu)
    c.check("A11 那条限高 CSS **两半都在**：`max-height: 240px` 与 `overflow-y: auto`"
            "（只有限高没有滚动 = 把要证明的东西裁掉）",
            css is not None and "max-height: 240px" in css.group(1)
            and "overflow-y: auto" in css.group(1), (css.group(1) if css else "没找到那条规则")[:80])
    trace_rule = re.search(r"\n\s*\.trace \{([^}]*)\}", stu)
    c.check("A12 `.trace` 自己**没有** max-height（限高只能加在容器上）",
            trace_rule is not None and "max-height" not in trace_rule.group(1),
            (trace_rule.group(1).strip()[:70] if trace_rule else "没找到 .trace 规则"))
    return c


def batch_b_public_view():
    c = Checker()
    title("B. `public_view`：真调每一只工具 + inv60 逐字 payload 对照表")
    from app.agent.public_view import MAX_CHARS, public_view

    with sandbox():
        # ⚠️ 种数据这件事**必须走产品自己的写函数**，不能只写 `student/timetable.json` 那个老路径：
        #    克隆里红的那条（`get_day_courses` 是单行）就是这么来的 —— harness 的 `write_timetable()`
        #    写的是**共享桶**，而 #32 之后工具读的是**按学号那一份**（`student/admin1/timetable.json`）。
        #    本机为什么绿？因为 `sandbox()` 是 copytree 真实数据，而开发机上 admin1 那份课表**是用户手测出来的**
        #    ⇒ 断言读的是"我这台机器上碰巧有什么"，不是"我种的什么"（4o3 那一族的又一实例，这次栽在写入路径上）。
        from app import store

        store.set_current_student("admin1")
        store.save_timetable(TIMETABLE)          # 进 admin1 那一份，两台机器上形状一致
        write_express([{"id": "X1", "owner": "admin1", "item": "《算法导论》图书",
                        "station": "菜鸟驿站-北区", "pickup_code": "A-1234",
                        "arrived_at": (TODAY - dt.timedelta(days=9)).isoformat(),
                        "status": "待取"}])
        write_takeout([{"id": "T1", "owner": "admin1", "shop": "麦当劳",
                        "items": "麦辣鸡腿堡套餐", "eta": "12:30",
                        "pickup_point": "宿舍楼外卖柜 A 区", "pickup_code": "A1",
                        "status": "配送中"}])
        # 待办同理：`seed_todos()` 写的也是共享桶，身份设成 admin1 之后 `list_day_todos` 读不到它
        store.add_todo("复习数学", D_PLUS, "14:00", "15:40")

        rows = _sweep()
        mods = {m for m, _, _, _ in rows}
        names = {n for _, n, _, _ in rows}
        # B0 先自证"这一批确实跑了、而且扫到了整族"—— 空循环会让下面几条一起假绿
        c.check("B0 扫描自证：6 个模块、20 只以上工具真的各调了一次（少于这个数 = 扫描本身坏了）",
                len(mods) >= 6 and len(names) >= 20,
                f"模块 {len(mods)} 个 / 工具 {len(names)} 只")

        # ⚠️ B0-bis 也是**犯了才补的**：外卖那两只有三轮扫出来的是 `Tool.run` 的围栏句
        #    （我 fixture 里把 `items` 写成 `item` ⇒ KeyError ⇒ "调用 list_my_orders 时出错了…"），
        #    这种样本喂进 `public_view` 一样"没有内脏词、也不超长"，于是 B1/B2/B3 全绿 ——
        #    绿的是一只根本没跑通的工具。⇒ 自种的几只必须先证明"跑通了"。
        errs = sorted({n for _, n, _, s in rows
                       if s.startswith("调用 ") and ("时出错了" in s or "时参数不对" in s)})
        seeded = ("get_day_courses", "list_my_packages", "list_pending_pickup",
                  "list_my_orders", "list_active_orders")
        c.check("B0-bis 我拿来当正控的那五只不许扫成围栏句（拿错误串当被测样本 = 下面几条全在量空气）",
                not [n for n in seeded if n in errs],
                f"围栏句：{[n for n in seeded if n in errs]} ／ 全扫描里 {len(errs)} 只这样（模型传错参数那一族，正常）")

        bad, empty, nl, over = [], [], [], []
        for m, n, obj, s in rows:
            view = public_view(n, obj)
            h = _hits(view)
            if h:
                bad.append(f"{m}.{n}:{h}")
            if s.strip() and not view.strip():
                empty.append(f"{m}.{n}")
            if "\n" in view:
                nl.append(f"{m}.{n}")
            if len(view) > MAX_CHARS:
                over.append(f"{m}.{n}({len(view)})")
        c.check("B1 全扫描：`public_view` 的输出里一个内脏词都没有（名单逐条来自原始 payload）",
                not bad, f"命中 {len(bad)} 只：{bad[:4]}")
        c.check("B2 全扫描：原始非空 → 给人那份也不许空（**打码打成空 = 把证据一起打掉**，"
                "#58 花一整单找回来的就是这类证据）",
                not empty, f"收成空的：{empty[:6]}")
        c.check("B3 全扫描：给人那份里没有换行、也不超 240 字（写侧折行 + 限长，前端只管限高）",
                not nl and not over, f"有换行 {nl[:4]} / 超长 {over[:4]}")
        wr = sum(1 for _, _, _, s in rows if "\n" in s)
        # ⚠️ 这一条**第一版写成了全局计数**（`wr >= 6`），在严格克隆里当场红：
        #    克隆只有 tracked 的那份 `kb.json`，而 `search_kb` / 驿站那几只工具的行数跟着**数据**走
        #    （本机 7 只、克隆 5 只）。⇒ 正控要钉在**自己种的数据**上，不是钉在环境给的数字上。
        #    现在改成逐只点名：这五只的返回形状由上面 `store.save_timetable` / `write_express` /
        #    `write_takeout` 决定，与本机那份未提交的数据无关；哪天它们变成单行，红的是真的坏了。
        mine = {"get_day_courses", "list_my_packages", "list_pending_pickup",
                "list_my_orders", "list_active_orders"}
        single = sorted({n for _, n, _, s in rows if n in mine and "\n" not in s})
        c.check("B3-bis 我自种的那五只工具，原始返回**确实**是多行（全是单行的话 B3 的『没有换行』就是白过的）",
                not single and len({n for _, n, _, _ in rows if n in mine}) == 5,
                f"单行的：{single} / 全局带换行 {wr} 只")
        clean = [(m, n) for m, n, _, s in rows if s.strip() and not _hits(s)]
        c.check("B4 对照（正控）：扫描里确实有一批工具**原始就没有**内脏词 —— "
                "没有这一组，B1/B2 会把「全都空」也判成通过",
                len(clean) >= 5, f"原始干净的工具 {len(clean)} 只：{[n for _, n in clean][:6]}")

        miss = []
        for name, raw, keep, drop in PV_FIXTURES:
            view = public_view(name, raw)
            lost = [k for k in keep if k not in view]
            leaked = [w for w in drop if w in view]
            if lost or leaked:
                miss.append(f"{name} 丢{lost} 漏{leaked}")
        c.check(f"B5 逐字对照表 {len(PV_FIXTURES)} 条：该留的事实都留着、该消失的都消失了",
                not miss, "；".join(miss[:3]))

        ctrl = [nm for nm, raw, _, _ in PV_FIXTURES
                if not [w for w in BANNED if w in str(raw)]]
        c.check(f"B6 控制句在场：表里 {len(ctrl)} 条原始 payload 本来就干净（"
                "没有这一组，把全部输出清空也能骗过 B1/B5）",
                len(ctrl) >= 2, str(ctrl))

        long_raw = "空档很多的一段话。" * 60
        view = public_view("whatever", long_raw)
        c.check("B7 长文本按 240 字截断并带省略号（不截会把那条轨迹行顶成整屏）",
                len(view) <= MAX_CHARS + 1 and view.endswith("…"), f"len={len(view)}")

        c.check("B8 解不动的字符串就当纯文本走，不猜（`{不是合法 JSON` 这种形状实测存在）",
                "{不是合法的 JSON" in public_view("whatever", "{不是合法的 JSON，别猜"),
                repr(public_view("whatever", "{不是合法的 JSON，别猜"))[:60])
    return c


class ForkedLLM:
    """剧本：第一轮要求调 `propose_slots`（返回值整句都是给模型的指令），第二轮好好回答。

    为什么必须有它：B 批证的是"函数自己会收"，证不了**引擎真的把收过的那份送进 trace、
    同时把原文送进模型**。同一次调用两头各看一眼才算数（唯一判据是模型那一侧看到了什么）。
    """

    def __init__(self, candidates):
        self.turn = 0
        self.tool_msgs = []
        self.candidates = candidates

    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
        self.turn += 1
        self.tool_msgs = [dict(m) for m in messages if m.get("role") == "tool"]
        if self.turn == 1:
            return {"content": "", "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "propose_slots",
                    "arguments": json.dumps(
                        {"options_json": json.dumps(self.candidates, ensure_ascii=False)},
                        ensure_ascii=False)}}]}
        return {"content": "周二下午有整段空档，挑一段？"}


def batch_c_engine_fork():
    c = Checker()
    title("C. 引擎那一处：**给人那份进 trace，原文回灌给模型**（两路必须分叉）")
    import tempfile

    from app.agent.engine import AgentEngine
    from app.modules.planner import build_tools

    cand = [{"title": "做题", "date": D_PLUS, "start": "14:00", "end": "16:00"}]
    llm = ForkedLLM(cand)
    engine = AgentEngine(llm=llm, tools=build_tools(), system_prompt="测试",
                         session_id="t60-fork")
    # ⚠️ 这一批**必须套沙箱**，而且不是洁癖：`engine.py:249-250` 在 run() 收尾会 `save_pending`。
    #    第一版没套，于是 `admin1|t60-fork` 这一键真被写进了用户的 `app/data/pending.json`
    #    （写完才在核数据账时看见 ⇒ 已清掉，只删我自己那一键、他的那条逐键比对过没动）。
    #    "跑跑引擎而已"恰恰是最容易漏的那类：它不通过 `/api/chat`，也不碰 harness 那几个
    #    带 `_require_sandbox()` 的种子函数，所以一路没有任何一道闸拦它。
    with sandbox():
        c.check("C-环境 这一批确实跑在沙箱里（引擎自己会 `save_pending`，真实 data 一个字都不许被测试写进去）",
                (os.environ.get("CAMPUSTIME_DATA_DIR") or "").startswith(tempfile.gettempdir()),
                f"CAMPUSTIME_DATA_DIR={os.environ.get('CAMPUSTIME_DATA_DIR')}")
        asyncio.run(engine.run("帮我看看哪天下午空"))

        rows = [t for t in engine.trace if (t.get("phase") or "").startswith("📦")]
        c.check("C0 这一轮确实走到了工具结果那一步（没走到，下面三条全是空谈）",
                len(rows) >= 1, f"trace {len(engine.trace)} 行 / 📦 {len(rows)} 行")
        got = rows[0].get("result") if rows else ""
        c.check("C1 trace 里是**收过的那一份**：写着「1 个候选时间段」，「前端」「学生确认」一个字不剩",
                "1 个候选时间段" in str(got) and not _hits(str(got)), repr(str(got))[:80])
        raw = next((str(m.get("content")) for m in llm.tool_msgs
                    if "候选时间段" in str(m.get("content"))), "")
        c.check("C2 而模型手里**还是原文**（「已向前端展示…等学生确认」是它下一步的依据）",
                "已向前端展示" in raw and "等学生确认" in raw, repr(raw)[:80])
    c.check("C3 分叉没碰捕获：`engine.options` 仍然是那一张候选（改了展示副本不能顺手把卡改没）",
            engine.options == cand, str(engine.options)[:80])
    c.check("C4 整条 trace 里没有 600 字的原始语料（原始只进服务端日志）",
            not any("已向前端展示" in str(t.get("result") or "") for t in engine.trace))
    return c


REASON_CASES = [
    # (人话名字, 关键 kwargs, 必须出现的子串, 必须不出现的子串)
    ("生理状态否决", dict(physio=True), "状态句", []),
    ("宣布办完了 → 不按新增", dict(done_claim=True, sem_intent="done_todo",
                                   rule_hit="add_todo", sem={"intent": "done_todo"}),
     "宣布", []),
    ("周期落不成一条", dict(period_block=True), "周期", []),
    ("规则想办事但这句在问事", dict(rule_veto=True), "以语义层为准", []),
    ("一整段材料", dict(is_material=True), "只从这段里", []),
    ("提问信号 → 问事", dict(asks=True, sem_intent="chat", sem={"intent": "chat"}),
     "提问信号", []),
    ("在问答栏说话", dict(qa_view=True, rule_hit="add_todo"), "问答这一栏", []),
    ("规则层认得这种说法", dict(rule_hit="add_todo"), "不用再猜一次", []),
    ("语义层判成闲聊", dict(sem={"intent": "chat"}, sem_intent="chat"), "按聊天答", []),
    ("压根没叫语义层（兜底的实话）", dict(sem=None), "没叫语义层参与", []),
]


def batch_d_reasons():
    c = Checker()
    title("D. 判定层的「为什么」：全触发矩阵 + 真接口的轨迹头部")
    from app.main import _reason_steps

    base = dict(rule_hit=None, asks=False, qa_view=False,
                rule_veto=False, period_block=False, done_claim=False, physio=False,
                is_material=False, sem=None, sem_intent=None)
    all_lines, wrong = [], []
    for name, extra, want, avoid in REASON_CASES:
        kw = dict(base)
        kw.update(extra)
        rows = _reason_steps(**kw)
        text = " / ".join(str(r.get("answer")) for r in rows)
        all_lines.append(text)
        ok = (rows and want in text and all(w not in text for w in avoid)
              and all(r.get("step") == 0 and r.get("phase") == "🧭 判定" for r in rows))
        if not ok:
            wrong.append(f"{name}→{repr(text)[:50]}")
        c.check(f"D 触发了就该有这一条：{name}", ok, repr(text)[:70])

    # 控制：什么都没判出来（模型给了结论但没触发任何一档）→ 必须是空，不许硬凑一句
    ctrl = _reason_steps(**dict(base, sem={"intent": "query"}, sem_intent="query"))
    c.check("D-控制 一条都没触发时必须是**空**（宁少不假：这里多写一句就是编造判定过程）",
            ctrl == [], str(ctrl)[:80])
    # 控制2（真机撞出来的那类假话）：语义层**真参与**了判定，就不能再说"不用再猜一次"（那句的
    # 前提是 `sem is None` = 真的没往外问过）。
    # 判据从"这行有没有出现"升级成"这行出现得对不对"—— 第一版只钉了前者，
    # 于是 `rule_hit` 与 `sem` 同时存在时它照样绿，可那句话已经是假的了。
    lie = _reason_steps(**dict(base, rule_hit="add_todo",
                               sem={"intent": "add_todo"}, sem_intent="add_todo"))
    c.check("D-控制2 语义层真参与判定时，不许写出「不用再猜一次」（问过了却说没问 = 轨迹里写假话）",
            not any("不用再猜一次" in str(r.get("answer")) for r in lie), str(lie)[:110])
    # 控制3：否决那条不许承诺结果 —— 真机上"以语义层为准"之后，引擎照样提议了一张确认条。
    veto = _reason_steps(**dict(base, rule_veto=True, sem={"intent": "chat"}, sem_intent="chat"))
    c.check("D-控制3 否决那条只陈述『这一句不走哪条路』，不承诺『不会出卡』"
            "（第一版写了'这一句不出卡'，被真机那句打脸）",
            bool(veto) and not any("不出卡" in str(r.get("answer")) for r in veto), str(veto)[:130])
    many = _reason_steps(**dict(base, physio=True, period_block=True, rule_veto=True,
                                is_material=True, sem=None))
    c.check("D-上限 几道否决同时成立也只出 3 条（超过就是在刷屏，不是在读轨迹）",
            len(many) <= 3, f"出了 {len(many)} 条")
    c.check("D-口径 全矩阵的输出里没有内脏词、也没有模块名（这些句子是给学生和评委看的）",
            not [w for line in all_lines for w in _hits(line)], str(
                [w for line in all_lines for w in _hits(line)])[:120])
    # ⚠️ 这一条是**犯了之后才补的**：第一版写的是「这一句没问模型」「…→ 不用问模型」，
    #    读起来完全正常，却踩了本仓那条红线（学生屏幕上不暴露技术底座）——
    #    `test_01` 与 `test_38` 各红一条把它抓了出来（那两条是既有护栏，不是本单写的）。
    #    所以这里照 `test_38.FORBIDDEN` 同一口径**再钉一遍**，而且钉在**新写的这批文案**上：
    #    既有护栏管得住全仓，但"这一单新加的那三行"该由这一单的护栏自己守住。
    tech = [w for line in all_lines for w in TECH_BANNED if w in line]
    c.check("D-口径2 判定层那些句子里不出现技术底座词（模型 / 密钥 / API Key / 管理控制台…）",
            not tech, str(tech)[:120])

    with sandbox():
        client = make_client()
        d = client.post("/api/chat", json={"message": "我饿了", "session_id": "s60-reason"}).json()
        tr = [t for t in (d.get("trace") or []) if isinstance(t, dict)]
        heads = [str(t.get("phase") or "") for t in tr[:4]]
        # ⚠️ 这里钉的是**实测次序**（§〇②）：`read` 占第 1 行，🧭 判定跟在它后面。
        #     单据 §四.1 写"前三行是 🧭 判定"，与 §1.1 自己的"插在轨迹最前面"矛盾；
        #     按单据字面钉会红，而且红的是断言不是产品。
        c.check("D4 真接口：轨迹第 1 行是 🧠 听成了什么，其后紧跟 🧭 判定（次序按实测）",
                tr and str(tr[0].get("phase") or "").startswith("🧠")
                and any(h.startswith("🧭") for h in heads[1:]), str(heads)[:110])
        c.check("D5 真接口：那句「我饿了」触发的正是状态句那一条（不是随便哪句凑上去的）",
                any("状态句" in str(t.get("answer") or "") for t in tr
                    if str(t.get("phase") or "").startswith("🧭")), str(heads)[:110])
        c.check("D6 真接口：🧭 那些条不占轮次（step 必须是 0，前端不画编号）",
                all(int(t.get("step") or 0) == 0 for t in tr
                    if str(t.get("phase") or "").startswith("🧭")))
        c.check("D7 真接口：判定层的这些条里同样没有内脏词，也没有技术底座词（写侧与出口都收过一遍）",
                not [w for t in tr if str(t.get("phase") or "").startswith("🧭")
                     for w in _hits(str(t.get("answer") or "")) +
                     [x for x in TECH_BANNED if x in str(t.get("answer") or "")]])
    return c


def _slice_fn(src, head):
    """从 `head` 起抠出整个函数（大括号配对）—— 与 test_79 同一手法，抠**页面自己那份**。"""
    i = src.index(head)
    depth, j = 0, src.index("{", i)
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1] + "\n"
        j += 1
    raise AssertionError("函数没配对到大括号：" + head)


def batch_e_frontend():
    c = Checker()
    title("E. 前端：node 真跑 `_oneLine` / `renderTrace`（复用 test_79 那个 runner，不抄第二份）")
    src = open(STUDENT, encoding="utf-8").read()
    if not shutil.which("node"):
        c.check("E0 本机没有 node —— 这一批只能靠 node 真跑，不许退回读源码判语义", False)
        return c

    try:
        fn = _slice_fn(src, "function _oneLine(")
    except (ValueError, AssertionError) as e:      # 函数没落地 / 被人整段删掉：要一条红，不是一个崩
        c.check("E0 页面里有 `_oneLine` 这个折行函数（没有 = 本单 C 还没落地）", False, str(e)[:120])
        return c
    js = fn + 'process.stdout.write(_oneLine(process.argv[1]));'
    multi = "查到 4 门课：\n\n  周一 1-2\n周一 3-4   周五\n\n  周三 5-6"
    p = subprocess.run(["node", "-e", js, multi], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    folded = p.stdout or ""
    c.check("E1 `_oneLine` 真跑：换行折成 ` · `、连续空白压掉，结果里没有换行",
            p.returncode == 0 and "\n" not in folded and " · " in folded
            and "  " not in folded, repr(folded)[:110])
    c.check("E1-bis 而且这条**就是页面在跑的那一行**：`renderTrace` 里工具结果过 `_oneLine`"
            "（定义了不用 = 前面验的是空气）",
            'an += (an ? " → " : "") + _oneLine(t.result);' in src)

    import test_79_trace_rendered as t79     # 复用同一个 node bundle，别再抄一份
    trace = [
        {"step": 0, "phase": "🧭 判定", "answer": "只是状态句 → 按聊天答，不推销待办"},
        {"step": 1, "phase": "🔧 调用工具", "action": "get_weekly_timetable", "args": {}},
        {"step": 1, "phase": "📦 工具结果", "action": "get_weekly_timetable",
         "result": "查到 4 门课\n周一 1-2\n周三 3-4"},
        {"step": 2, "phase": "💡 最终回答", "answer": "周二最空"},
    ]
    try:
        out = t79.render(trace, "周二最空")
    except AssertionError as e:
        c.check("E2 node 跑通 `renderTrace`（跑不通 = 从页面抠出来的那段坏了）", False, str(e)[:150])
        return c
    c.check("E2 #58 不回退：🔧 / 📦 仍然画得出来（这一单只加密度，不许把证据藏回去）",
            "🔧" in out and "📦" in out and "get_weekly_timetable" in t79._seen(out))
    c.check("E3 Observation 折单行：那一行里 4 门课那三条换行变成了一个 ` · ` 分隔的行",
            "查到 4 门课 · 周一 1-2 · 周三 3-4" in t79._seen(out), repr(out[-220:])[:200])
    c.check("E4 限高加在容器上：`<summary>` 在 `.trace-body` **外面**（在里面的话标题会跟着滚走）",
            out.index("trace-body") > out.index("<summary>"))
    c.check("E5 #58 的去重还在：💡 与气泡正文同文时**整条不画**（新加的容器不许把它顶回来）",
            out.count("周二最空") == 0, f"出现 {out.count('周二最空')} 次")
    # ⚠️ "期望 0 次"必须配一条"期望非 0"的正控：把渲染函数整个删掉也能骗过上面那条。
    diff = t79.render(trace, "周二下午有整段空档，挑一段？")     # 正文与 💡 那行**不同文**
    c.check("E5-bis 正控：正文与 💡 不同文时那一行**照画**（否则 E5 的 0 次是假的）",
            diff.count("周二最空") == 1 and "💡" in diff, f"出现 {diff.count('周二最空')} 次")
    row = re.search(r"<div class=\"trace-row\">[^<]*🧭[^<]*</div>", out)
    c.check("E6 🧭 判定那条画得出来，而且**没有编号**（`step: 0` 前端不画 `.st`）—— "
            "它要是带上了轮次数字，评委就会以为那是引擎的第一步",
            row is not None and "class=\"st\"" not in row.group(0),
            (row.group(0)[:120] if row else "没找到 🧭 那一行"))
    # ⚠️ E6 吃的是**手摆的** trace，所以它只验前端"0 不画号"，验不到生产侧真的写 0
    #    （V10 把 `step: 0` 改成 1，E6 纹丝不动 —— 这就是"断言没牙"的读数，别混过去）。
    #    E7 补的正是这一环：拿 `_reason_steps` **今天产出的那些行**直接送进渲染器。
    from app.main import _reason_steps
    live = _reason_steps(rule_hit="add_todo", asks=False, qa_view=False,
                         rule_veto=False, period_block=False, done_claim=False, physio=True,
                         is_material=False, sem=None, sem_intent=None)
    live_html = t79.render(live + [{"step": 1, "phase": "🔧 调用工具", "action": "plan_task",
                                    "args": {}}], "随便一句正文")
    lrow = re.search(r"<div class=\"trace-row\">[^<]*🧭[^<]*</div>", live_html)
    c.check("E7 生产→渲染一路通：`_reason_steps` 今天产出的那几行，画出来仍是 0 编号 + 🧭 档名",
            lrow is not None and "class=\"st\"" not in lrow.group(0)
            and "状态句" in t79._seen(lrow.group(0)),
            (lrow.group(0)[:130] if lrow else "没找到 🧭 那一行"))
    return c


def main():
    global code
    print("test_81_react_reason_layer_and_public_view：#60 轨迹读起来要像 ReAct")
    print("=" * 60)
    rc = 0
    for name, fn in (("A 批（形状与边界）", batch_a_shape),
                     ("B 批（public_view 全工具扫描）", batch_b_public_view),
                     ("C 批（引擎两路分叉）", batch_c_engine_fork),
                     ("D 批（判定层 reasons）", batch_d_reasons),
                     ("E 批（node 真跑前端）", batch_e_frontend)):
        # ⚠️ 用 max() 不用 `or`：`or` 会在第一批变红时短路，后面几批根本不跑（#55 踩过）
        rc = max(rc, fn().summary(name))
    code = rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
