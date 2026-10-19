"""粘贴进来的**材料**（通知 / 安排表 / 清单）→ 可确认的待办条目（#43）。

用户 2026-10-19 12:57 真机：在日程栏粘了一段考试通知（几百字、多行、"项目 内容"那种表），
系统把它当一句普通问话交给了模型 —— 模型手里带着上一轮"图书馆几点关门"的历史，
于是拿"图书馆 22:30 闭馆"去答一段考试安排。实测（`probe43_repro.py`）这一版偶尔读对、
偶尔被上文带走：**同一段材料的结果取决于模型那一轮怎么想，这就是不能交给它的原因**。
本模块给材料一条确定性出口：认出它 → 只从它自己里面提取 → 交给多选确认卡（写库由系统做）。

为什么不复用 `asks_campus_fact`（#42-A）：那条是**40 字以内**的问句闸（`planner.py:3978`），
材料恰恰是长文本 —— 两件事，别共用一条判据。
"""
import re

from app.modules import planner

# 结构化字段词：成组出现基本就是"一段要处理的通知/清单"，不是一句提问。
# 「考试」和「日期」「时间」「地点」各算一个 —— 真实通知里它们总是同段共现。
_MATERIAL_FIELDS = ("项目", "内容", "科目", "课程", "考试", "日期", "时间", "地点",
                    "监考", "对象", "安排", "议程", "参会", "负责人",
                    "通知", "截止", "报名", "事项")

# 疑问主干：句子里有这些，就说明他在**问**，不是在递材料（宁可漏不可抢）。
_QUESTION_RE = re.compile(r"[？?]|吗$|呢$|什么|怎么|怎样|几点|多久|哪|是否|能不能|可不可以")

# 编号 / 项目符号 / 分号：清单的三种写法（"1. xxx" "① xxx" "- xxx" "一、xxx"）
_LIST_MARKER_RE = re.compile(r"^\s*(?:\d+[.、)）]|[①-⑩]|[-•·*]|[一二三四五六七八九十]+[、.)）])")
# 同一形状，但**逐原始行**判（`split_material` 会把行首编号吃掉，之后就看不见了）
_NUMBERED_LINE_RE = re.compile(r"^\s*(?:\d+[.、)）]|[①-⑩]|[-•·*]|[一二三四五六七八九十]+[、.)）])", re.M)
_SPLIT_RE = re.compile(r"[\n；;]+")

# 周课表的两个形状要素：**只有星期**（周X，没有几月几日）+ 一天以上
_WEEKDAY_RE = re.compile(r"(?:周|星期|礼拜)[一二三四五六日天]")
# 绝对日期：待办要落在具体某一天，材料里没写具体日子就不是"这几件事"而是"每周都这样"
_ABS_DATE_RE = re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*[日号]|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")


def _looks_like_weekly_grid(text: str, segs: list[str]) -> bool:
    """这段是不是**周课表**（每周重复的课），而不是"某几天要做的几件事"。

    为什么必须单独挡掉（实测 `probe43_shapes.py` / `probe43_tt_e2e.py`，2026-10-19）：
    裸粘的课表 —— 不管 markdown 还是 Excel 复制的纯文本 —— 既是多行、又满是
    "课程/时间/地点"，材料判据的形状三条**一条不落全命中**，于是出了一张
    `todo_add_batch` 卡，上面写着 `|||高等数学|教三301|`、`周高等数学教三301`
    这种残渣标题，还把每周重复的课**钉死到了 2026-10-26 这一天**。
    学生想导入课表，换来一张"点一下就污染日程"的卡 —— 这比 #43 之前那句掉进 faq
    更坏（掉 faq 什么都没写，这张卡点【加入日程】就真写进去）。

    三条同时成立才算课表（宁可漏不可抢）：
      ① ≥2 段挂着"周X/星期X"；
      ② 全文**一个绝对日期都没有**（"10月21日"或"2026-10-21"）—— 有日子就是"那天的事"；
      ③ 没有行首编号 / 项目符号 —— 「1. 周三班会 2. 周五交作业」那种是**待办清单**（test_71 A4）。
    放弃的那一半：「周三交实验报告\n周五交论文」这种用星期写、不带编号的两行待办会被让出去
    （回到 faq）。换的原因是**方向上的不对称**：漏了学生再问一句就行，抢了是往他日程里写脏数据。
    """
    if _ABS_DATE_RE.search(text) or _NUMBERED_LINE_RE.search(text):
        return False
    return sum(1 for seg in segs if _WEEKDAY_RE.search(seg)) >= 2


def looks_like_material(text: str) -> bool:
    """这段是"材料"（要提取的输入），不是"问题 / 指令"。三条同时成立才算。

    ① 够长（>60 字）**或** 有多段（换行 / 分号切出来 ≥2 段，两行起）或带编号 / 项目符号；
    ② 命中至少 **3 个**结构化字段词（1~2 个可能只是碰巧提到"时间""地点"），
       **或者**有 ≥2 行各自带日期（那种"一行一件事"的清单没有字段词，但同样是材料 —— 实测
       「10月21日 19:30-21:30 离散数学考试 / 10月22日 …」三个字段词都不沾）；
    ③ 不像一句问句（不带问号、句里没有疑问主干）。
    ⚠️ 第③条是硬否决：一段材料里出现"怎么/几点"多半是他顺手把问题也粘进来了，
       那种时候让他先问清楚比系统猜更安全（同 41/42 的"宁可漏不可抢"口径）。
    """
    t = (text or "").strip()
    if not t:
        return False
    # 分隔符算"多段"要看**切完几段**，不是数 `\n`：聊天输入框是 `input[type=text]`，
    # 浏览器粘多行会把换行整个吃掉（前端把换行换成"；"再发），所以这里得连"；"一起认。
    segs = split_material(t)
    long_or_multi = len(t) > 60 or len(segs) >= 2 or bool(_LIST_MARKER_RE.search(t))
    if not long_or_multi:
        return False
    dated_lines = sum(1 for seg in segs if planner._pick_date(seg))
    if sum(1 for w in _MATERIAL_FIELDS if w in t) < 3 and dated_lines < 2:
        return False
    if _QUESTION_RE.search(t):
        return False
    # ⚠️ 课表粘贴**不是**材料：它也是多行、也满是"课程/时间/地点"，形状判据一样会命中，
    # 但它有自己那条支（`looks_like_timetable_import` → 出 `timetable_change` 卡，test_23 钉着）。
    # 让给它一次，比在自己支里猜课程表强（全量第一次跑就是这么抓出来的抢线）。
    if planner.looks_like_timetable_import(t):
        return False
    # ⚠️ 周课表**靠形状**也要让路，不能只靠 `looks_like_timetable_import`：那句认的是
    # "上传/读取/导入 + 课表"这种**文字**，而裸粘的课表一个字都没提（实测 tt_import=False），
    # 就会一路被判成材料、把课钉成某一天的待办（`_looks_like_weekly_grid` 的实测记录）。
    if _looks_like_weekly_grid(t, segs):
        return False
    return True


def split_material(text: str) -> list[str]:
    """切成段：换行 / 分号先切，再吃掉行首编号；去空段。"""
    out: list[str] = []
    for raw in _SPLIT_RE.split(text or ""):
        seg = _LIST_MARKER_RE.sub("", raw).strip(" \t\u3000")
        seg = re.sub(r"\s+", " ", seg)
        if seg:
            out.append(seg)
    return out


def _pairs_from_flat(seg: str) -> list[tuple[str, str]]:
    """一行到底的写法：`考试科目 离散数学 考试日期 10月21日 …` 从一个 `<input>` 里粘过来。

    为什么必须有这一半（浏览器实测，2026-10-19）：聊天输入框是 `input[type=text]`，
    浏览器粘多行文本时会**直接吃掉换行**（103 字那份粘进去剩 46 字，`\n` 一个不剩，
    字段词还被粘在上一句尾巴上："项目 内容考试科目 离散数学考试日期 …"）。
    只按行切的那一半在这条路上等于没实现 —— 所以还要能在**一行内**按字段词切开。
    """
    hits = [(seg.find(w), w) for w in _MATERIAL_FIELDS if w in seg]
    hits = sorted({(p, w) for p, w in hits})
    out: list[tuple[str, str]] = []
    for i, (pos, key) in enumerate(hits):
        # 键 = 字段词前面那句的尾巴也一起看（"…离散数学" 里的 科目 才是键，值在后面）
        nxt = hits[i + 1][0] if i + 1 < len(hits) else len(seg)
        val = seg[pos + len(key):nxt].strip(" \t，,、;；。")
        if val and not _is_header(key, val):
            out.append((key, val))
    return out


def _field_pair(seg: str) -> tuple[str, str] | None:
    """`考试科目 离散数学` / `考试时间：19:30-21:30` → ("考试科目", "离散数学")。

    只认**以已知字段词开头**的那一行（后面跟冒号或空格）。
    这是"表"的形状；清单里的一行（"10月21日 交作业"）不是，走别的支。
    """
    m = re.match(r"^\s*([^\s：:，,。;；]{2,12})\s*(?:[：:]\s*|\s+)(.+)$", seg)
    if not m:
        return None
    key, val = m.group(1), m.group(2).strip()
    if not val or not any(w in key for w in _MATERIAL_FIELDS):
        return None
    return key, val


def _is_header(key: str, val: str) -> bool:
    """表头行（"项目 内容"）：值自己就是一个字段词 ⇒ 那是列名，不是内容。"""
    return val in _MATERIAL_FIELDS


# 事名字段的优先级：这一列的值就是"那件事叫什么"
_NOUN_FIELDS = ("科目", "课程", "项目", "事项", "议程", "任务", "内容")
# 事件词：字段键里若带着它，就拼到标题后面（"考试科目" + 离散数学 → 离散数学考试）
_EVENT_WORDS = ("考试", "补考", "会议", "讲座", "培训", "值班", "答辩", "面试", "报名", "检查")


def _title_from_fields(fields: list[tuple[str, str]]) -> str:
    for noun in _NOUN_FIELDS:
        for key, val in fields:
            if noun in key:
                head = key.replace(noun, "").strip()
                ev = next((e for e in _EVENT_WORDS if e in head or e in key), "")
                # 键里那串"考试/会议…"是事件本身，值才是事名 —— 拼起来才是学生认的那件事
                return f"{val}{ev}" if ev and ev not in val else val
    return fields[0][1] if fields else ""


_EMPTY_BRACKET_RE = re.compile(r"[（(\[【]\s*[)）\]】]")
# 残渣标题的形状：竖线（表格分隔符没擦干净）或整条标题其实就是个日期/钟点。
# 实测「关于召开本学期第二次班会的通知：时间：2026年10月23日 15:00-16:00 …」经表那条支
# 拿到的标题就是 `2026年10月23日 15:00-16:00` —— 卡上写着一串日期，学生看不出这是哪件事，
# 点【加入日程】却真能写进去。这种宁可不写（落进 missing，让他补一句"这是班会"）。
_RESIDUE_TITLE_RE = re.compile(r"[|｜]|^\s*(?:20\d\d|\d{1,2}\s*月|\d{1,2}[:：]|\d{1,2}\s*[日号])")


def _clean_title(title: str) -> str:
    """擦掉提取留下的空括号与尾随标点。

    实测「周三下午两点班会（14:00-15:30）」经 `parse_add_todo` 拿到的标题是 **`班会（）`**
    —— 钟点被从括号里剥走，括号壳留在标题上（红线 21 那族残渣）。
    卡上写着"班会（）"，学生第一眼看到的是"系统坏了"，所以这里收一次。
    """
    t = (title or "").strip()
    t = _EMPTY_BRACKET_RE.sub("", t)
    return t.strip(" \t\u3000-—、,，。:：")


def _mk_item(title: str, ts: dict) -> dict | None:
    """拼一条**排得下来**的条目；缺日期或缺起止就返回 None（绝不替材料编一个时间）。

    要求 `date` **且** `start` **且** `end`，是因为这个产品的待办本来就要占一段时间：
    手动加那一条口（`POST /api/todos`）也是 `if not (date and start and end and title)` 拒的。
    卡片能勾、点了能写，两边口径得一样 —— 否则卡上摆着一条，学生勾完点按钮换来一句 400。
    """
    title = _clean_title(title)
    date = (ts or {}).get("date")
    start = (ts.get("start") or "").strip()
    end = (ts.get("end") or "").strip()
    if not title or not date or not start or not end:
        return None
    if _RESIDUE_TITLE_RE.search(title):
        return None
    return {"title": title[:40], "date": date, "start": start, "end": end}


def extract_deterministic(text: str) -> tuple[list[dict], list[str]]:
    """不叫模型，能从材料里拆出多少算多少：返回（带日期的条目, 只有标题没时间的说法）。

    两种形状分开处理（实测两种都常见）：
    A) **一张表**（"考试科目 离散数学 / 考试日期 … / 考试时间 …"）—— 字段词行 ≥2 且
       能自己带日期的非字段行 <2 ⇒ 合成**一条**，日期时间从**整段**取
       （实测 `parse_time_spec(整段)` 直接给 2026-10-21 / 19:30-21:30，比逐行再拼稳）。
    B) **一份清单**（一行一件事）⇒ 每行走一遍 `planner.parse_add_todo`。
    ⚠️ 表里的标题**不取 `_pick_title`**：实测它对整段材料给出 `'目内容'`
       （把"项目"的"项"当噪音剥了，红线 21 那族残渣），卡上就会写着"目内容"。
    """
    segs = split_material(text)
    if not segs:
        return [], []

    fields: list[tuple[str, str]] = []
    loose: list[str] = []
    for seg in segs:
        pair = _field_pair(seg)
        if pair and not _is_header(*pair):
            fields.append(pair)
            continue
        flat = _pairs_from_flat(seg)
        if len(flat) >= 2:                 # 一行里写着好几组"字段 值" ⇒ 它还是一张表（浏览器粘法）
            fields.extend(flat)
        else:
            loose.append(seg)

    items: list[dict] = []
    missing: list[str] = []

    dated_loose = [s for s in loose if planner._pick_date(s)]
    if len(fields) >= 2 and len(dated_loose) < 2:
        ts = planner.parse_time_spec(" ".join(v for _, v in fields))
        item = _mk_item(_title_from_fields(fields), ts)
        if item:
            items.append(item)
        else:
            missing.append(_title_from_fields(fields))
    else:
        for seg in loose or segs:
            prop = planner.parse_add_todo(seg) if not _field_pair(seg) else None
            if prop and prop.get("kind") == "todo_add":
                item = _mk_item(prop.get("title") or "", prop)
                if item:
                    items.append(item)
                    continue
            if _field_pair(seg):                    # 表里那些"监考老师/注意事项"：不是待办
                continue
            missing.append(seg[:40])

    dedup: list[dict] = []
    seen = set()
    for it in items:
        k = (it["title"], it["date"], it["start"])
        if k in seen:
            continue
        seen.add(k)
        dedup.append(it)
    return dedup[:20], [m for m in missing if m][:20]
