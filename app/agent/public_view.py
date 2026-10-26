# -*- coding: utf-8 -*-
r"""把工具返回值收成**给学生看的那一份**（#60-B）。

为什么收在写侧而不是渲染侧（施工单 §2.1，我核过、同意）：
`engine.py:221` 那句 `str(result)[:600]` 送出去的是**写给模型读的语料** ——
里面混着「已向前端展示…」「严禁…」「可以用 list_day_todos 看一下…」这类操作说明。
在前端打码要随工具增长维护（必漏），而 `plan.html` / 日志导出以后还得各打一遍；
写侧只有这一个产出点，改一次三面都干净。

⚠️ 只改**给人那份**。同一个 `result` 还会走 `engine.py:210-214` 回灌给模型
（`content: str(result)`）—— 那一路**必须保持原样**：那些"指令句"正是模型下一步该怎么做的依据，
把它一起洗掉不是隐私问题，是把循环改坏了（护栏里有一条专门钉这个）。

分支顺序由 `D:\campustime-scratch\inv60.txt` 那张实测表决定（25 个工具、真实副本）：
  ① 工具自己给了 `__user_view__` → 直接用（机制留着，目前没有工具用它）；
  ② 结构化的：list → 「共 N 条：」+ 挑几项的人话；dict → `summary` / 候选数 / `title`；
     `{"status":"empty","hint":"…"}` 这种**只给模型看的纠错句**一个都不念；
     （`get_weekly_timetable` / `find_free_slots` / `list_day_todos` 是这一类 —— 它们本来就是 dict/list，
      念出来是 `[{'day': 1, …` 那种 repr）
  ③ 纯文本：**绝大多数工具走这条，而且它们本来就是人话**
     （`get_day_courses`「周三 共 3 门课：- 08:00~09:40 数据结构（机房B）」、快递/外卖/驿站那几只、
      `plan_task`、`search_kb` 的第一句）—— 所以这里**只剪掉指令片段，不改写、不摘要**，
      摘要会把信息丢光，那是把一件事修成另一件事；
  ④ 统一压空白 + 截断。

⚠️ 截断长度这里给 240，不是 120：`renderTrace`（#58）那一道 120 是"一行的视觉上限"，
在这儿再截一次会把"共 N 条 + 挑的三项"截回"共 N 条"，两边都截等于信息只留一次。
"""
import json
import re

MAX_CHARS = 240

# 出现即认为这一小句是写给模型/开发者看的，整段不念给学生。
# 判据是"这句话在指挥谁"，不是"这句话好不好看"：
_INTERNAL = (
    "前端", "确认条", "候选卡", "严禁", "不许", "请把", "告诉用户", "告诉模型", "文字里也",
    "系统提示", "以下是", "工具说明", "相关度", "原文第", "按相关度排序", "可以用 list_",
    "重新调用", "请照", "入参", "参数 ", "字段名", "不要擅自", "标了出处", "换个关键词",
)
# 第三人称的开发腔 → 换成对学生说话（只这一处做"改写"，因为整句都是壳、丢了就没事实了）
# 例：实测 `propose_slots` 的全部返回就是「已向前端展示 1 个候选时间段，等学生确认。」
_SCRUB = (
    ("已向前端展示", ""),
    ("等学生确认", "等你确认"),
    ("学生确认", "你确认"),
)
_SPLIT = re.compile(r"[。\n]+|；|;")
_WD = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _squash(s: str) -> str:
    s = re.sub(r"\s*\n+\s*", " · ", str(s or ""))
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    s = re.sub(r"^( · )+", "", s)
    return s[:MAX_CHARS] + ("…" if len(s) > MAX_CHARS else "")


def _scrub(text: str) -> str:
    out = str(text or "")
    for a, b in _SCRUB:
        out = out.replace(a, b)
    return out


def _strip_internal(text: str) -> str:
    """按句/行丢掉"在指挥模型"的那几段，其余原样留着。

    ⚠️ 顺序：**先剥开发腔的壳，再逐句过滤**。反过来做实测会把事实一起丢掉 ——
    `propose_slots` 的全部返回就是一句「已向前端展示 1 个候选时间段，等学生确认。」，
    先过滤的话整句因"前端"被丢，收完变成空串：内部词确实不见了，可那条"1 个候选时间段"也没了
    （打码打成空 = 把证据一起打掉了，#58 花一整单找回来的就是这类证据）。
    """
    out = []
    for piece in _SPLIT.split(_scrub(text)):
        p = piece.strip().lstrip("- ").strip()
        if not p or any(w in p for w in _INTERNAL):
            continue
        out.append(p)
    return _squash(" · ".join(out))


def _is_note(it) -> bool:
    """这一项是不是**纯说明行**（`{"info": …}` —— 一句话，不是一件东西）（#66 追记）。

    契约在两把待办工具那头：查空了得回一句人话给模型（回空列表它会自己编「你这周很空」），
    而那种行只有一个 `info` 键。
    判据只认"**只有** info 一个键"这种纯说明行：带别的键的记录即使也写了 info，
    它仍然是一件东西，照旧算条目 —— 别把"有 info 的条目"也顺手排除掉。
    """
    return isinstance(it, dict) and set(it) == {"info"}


def _item_line(it) -> str:
    """一项怎么念：优先取它自己写好的那句话，其次拼事实。"""
    if not isinstance(it, dict):
        return str(it).strip()
    for k in ("info", "summary", "label", "text", "message"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    day = it.get("weekday") or _WD.get(it.get("day"), "")
    what = str(it.get("course") or it.get("title") or it.get("name") or "")
    span = " ".join(x for x in (str(it.get("start") or ""), str(it.get("end") or "")) if x)
    if it.get("start") and it.get("end"):
        span = f"{it['start']}-{it['end']}"
    return " ".join(p for p in (str(day), what, span) if p)


def _asobj(result):
    """list/dict 直接用；字符串长得像 JSON 就试一次解析（实测有几只工具返回的是 JSON **字符串**）。"""
    if isinstance(result, (list, dict)):
        return result
    s = str(result or "").strip()
    if s[:1] in ("[", "{") and s[-1:] in ("]", "}"):
        try:
            return json.loads(s)
        except Exception:
            return None            # 解不动就当纯文本走 ③，不猜
    return None


def public_view(tool_name: str, result) -> str:
    obj = _asobj(result)

    # ① 工具作者给的那一份（目前没有工具用；留着是因为"最懂怎么念的是写工具的人"）
    if isinstance(obj, dict):
        uv = obj.get("__user_view__")
        if isinstance(uv, str) and uv.strip():
            return _strip_internal(uv)
        prop = obj.get("__proposal__")
        if isinstance(prop, dict):
            # 提案：只念"要办哪件事"，hint 里那些"严禁/文字里也"一个字都不出去
            uv2 = prop.get("__user_view__")
            if isinstance(uv2, str) and uv2.strip():
                return _strip_internal(uv2)
            line = prop.get("summary")
            if not line and isinstance(prop.get("slots"), list):
                # 候选时段那一族**没有** summary（实测：`{"kind":"todo_slots","title":"复习数学","slots":[…]}`），
                # 只报"几个候选"就把"是哪件事"丢了 —— 学生要核对的恰恰是那件事。
                _t = str(prop.get("title") or "").strip()
                line = (_t + "｜" if _t else "") + f"{len(prop['slots'])} 个候选时段"
            if not line and prop.get("title"):
                line = f"已生成提案：{prop['title']}"
            if not line and prop.get("count") is not None:
                line = f"{prop['count']} 项待确认"
            return _strip_internal(line or "已生成一份提案，等你确认")
        if obj.get("status") == "empty":
            # 实测：`propose_todo_remove/done` 空手回来时整段 hint 都是在教模型下一步怎么做
            return "没找到符合条件的那一条"
    if isinstance(obj, list):
        if not obj:
            return "查下来是空的"
        # #66 追记（2026-10-26 真机）：`{"info": …}` 是**一句话**，不是一件事 ——
        # 不许算进「共 N 条」，也不许占掉「挑三项」那三个位置。
        # 实测两处都撞上：一条待办 + 一句「另有 1 条待办没有 id」念成「共 3 条」（数错），
        # 而 `list_todos_range` 是把 notes 摆在**最前面**的，那三句一读，真条目一句都挤不进去。
        items = [it for it in obj if not _is_note(it)]
        notes = [it for it in obj if _is_note(it)]
        if not items:
            # 整份返回只有说明行（典型：查到的记录全缺 id）—— 直接念说明，
            # 不许念成「共 0 条」再补一句（那读起来像"查到了，但你看不见"）
            return _strip_internal("；".join(n for n in (_item_line(x) for x in notes) if n))
        lines = [ln for ln in (_item_line(it) for it in items[:3]) if ln]
        head = f"共 {len(items)} 条"
        body = head + ("：" + " / ".join(lines) if lines else "")
        tail = "".join(f"（{n}）" for n in (_item_line(x) for x in notes) if n)
        return _strip_internal(body + tail)

    # ③ 本来就是人话的那一大类：只剪指令片段
    return _strip_internal(str(result if result is not None else ""))
