# -*- coding: utf-8 -*-
"""#113：「第N节」以前按作息表算，学生看的是「那天第N门课」—— 第二节指错课、第三节问不出来。

旧口径（`planner.py:55` `_PERIOD_START = {1:08:00, 2:08:00, 3:10:00, 4:10:00, ...}`）是
"两小节一门课"的作息表；库里的课表是"一门课 100 分钟"。两边对不上的后果（实测，admin1 真课表）：

| 说法 | 旧口径给什么 | 学生要什么 |
|---|---|---|
| 删除周二第二节 | 08:00《线性代数》 ❌ | 10:05《体育》 |
| 删除周二第三节 | None（追问） ❌ | 19:00《大学英语》 |
| 删除周二第一节 | 08:00《线性代数》 ✅ | **巧合对**（第一门刚好 08:00） |

**第二节比第三节危险**：第三节只是问不出来，第二节是**给了一张写着别的课的确认卡**，
学生手快点下去就删错。改法：口径换成"那天按开课时间排序的第 N 门"（`_course_by_period`），
**不动 `_PERIOD_START`** —— 那张表在 `period_span` → `candidate_slots`（排待办"第一节课的位置"）
那一头是对的，它要的是一个**时间段**，不是"哪一门课"。

为什么这里要自己种课表、不拿 admin1 的真课表当判据：
① 真课表是用户自己传上来的，本机绿、干净克隆红（同 #110 那条）；
② 这里要的形状真课表给不全 —— 需要「第二门不在作息表整点上」(10:50) 与「同名两门」两种靶子。

两条单据没写、我实测补上的：
  · **§四.3 那句「『下午第二节』现在会走 ③ 猜课名 → 追问」不成立** —— 改前给 08:00《线性代数》、
    改后给 10:50《体育》，两边都不追问。本单口径"第N门"不分上下午，T9 把这个**现状**钉住并写明
    它是待拍板项（要加"半天不符就追问"的守卫，得连 T9 一起改口）。
  · §2.4 说 #110 的测试只有三处要改口，实测**四处** —— T2-bis 也钉着"第二节 = 08:00"。

自己踩到的一个坑，记在这条判据为什么长这样：T7 第一版用 `items[::-1]`（倒序）打乱存储顺序 ——
三门课倒过来**中间那门还在中间**，M2（去掉排序）当场变成空操作、T7 假绿。
改成"轮转一格"才真的把每个下标都挪走（这是 #110 那轮"变异打在值域外面"的同一个病，换个形态又犯了一次）。
"""
from _harness import Checker, make_client, sandbox, title, write_timetable

# 自种课表：故意埋三种靶子 —— 第二门不在作息表整点上(10:50)、同名两门(周三/周四)、那天没课(周六)
PLAN = {
    2: [("08:00", "线性代数"), ("10:50", "体育"), ("19:00", "大学英语")],
    3: [("08:00", "数据结构"), ("10:05", "数据结构"), ("14:00", "操作系统")],
    4: [("08:00", "高等数学"), ("10:05", "计算机网络"), ("14:00", "大学英语"),
        ("19:00", "高等数学")],
}


def _seed(shuffled: bool = False):
    def _end(start: str) -> str:
        h, m = int(start[:2]), int(start[3:])
        return f"{h + 1:02d}:{m:02d}"          # 每门课 100 分钟取整到 +1 小时即可，本单不看 end

    rows = []
    for day, items in PLAN.items():
        # ⚠️ 打乱要用**轮转**，不能用倒序：3 门课倒过来中间那门还在中间 ——
        #   第一版我用 `items[::-1]`，M2（不排序）那笔变异当场变成空操作、T7 假绿。
        seq = (items[1:] + items[:1]) if shuffled else items
        for start, name in seq:
            rows.append({"day": day, "start": start, "end": _end(start), "course": name,
                         "location": "A101"})
    return rows


def batch_a_nth_course():
    c = Checker()
    title("A. 「第N节」= 那天第N门（T1~T6）")
    with sandbox():
        write_timetable(_seed())
        from app.modules.planner import parse_remove_course as PR

        def go(s):
            try:
                r = PR(s)
                return (r or {}).get("summary") if r else None, ""
            except Exception as e:                    # noqa: BLE001 - 崩要变成一条红
                return None, f"{type(e).__name__}: {e}"

        s, b = go("删除周二第二节")
        c.check("T1 ★命门：第二节指**第二门**（10:50 体育），不再指着第一门",
                not b and s == "删除周二 10:50 的《体育》", b or s)
        s2, b2 = go("删除周二第三节")
        c.check("T2 第三节不再空转：19:00 大学英语（旧口径这里直接追问）",
                not b2 and s2 == "删除周二 19:00 的《大学英语》", b2 or str(s2))
        s3, b3 = go("删除周二第一节")
        c.check("T3 第一节没退化（旧口径它本来就是对的，属巧合）",
                not b3 and s3 == "删除周二 08:00 的《线性代数》", b3 or str(s3))
        s4, b4 = go("删除周二第四节")
        c.check("T4 ★防兜底的闸：那天只有 3 门，说第四节要追问 —— "
                "不许 `min(n, len)-1` 静默删最后一门，也不许拿第一门顶替（越界直接 IndexError 更不行）",
                not b4 and s4 is None, b4 or str(s4))
        s5, b5 = go("删除周三第二节")
        s6, b6 = go("删除周四第四节")
        c.check("T5 ★关键正控：同名两门靠 start 分得开（周三两门《数据结构》、周四两门《高等数学》）"
                " —— 只按课名匹配的话这两条会随机落到另一节",
                not (b5 or b6) and s5 == "删除周三 10:05 的《数据结构》"
                and s6 == "删除周四 19:00 的《高等数学》", f"{b5}{b6} {s5} | {s6}")
        s7, b7 = go("删除周六第二节")
        c.check("T6 那天一门课都没有 → None（不瞎猜）", not b7 and s7 is None, b7 or str(s7))
    return c


def batch_b_order_and_boundaries():
    c = Checker()
    title("B. 排序键、边界与现状登记（T7~T10）")
    with sandbox():
        from app.modules.planner import parse_remove_course as PR

        # ★ 先把库里那份**轮转**种一遍（每项往前挪一格）：现数据恰好是时间序，不打乱的话
        #   "不排序"这个变异是空操作。倒序不行 —— 三门课倒过来中间那门还在中间。
        write_timetable(_seed(shuffled=True))
        try:
            r2 = PR("删除周二第三节")
            r = PR("删除周二第二节")
            s = (r or {}).get("summary") if r else None
            s2 = (r2 or {}).get("summary") if r2 else None
            boom = ""
        except Exception as e:                        # noqa: BLE001
            s, s2, boom = None, None, f"{type(e).__name__}: {e}"
        c.check("T7 ★排序键必须是 start：把存储顺序轮转（19:00 挪到最前）后，"
                "第二节仍指 10:50、第三节仍指 19:00 —— 少了 `sorted(key=start)`，"
                "它会照着数组下标数，第二节落到 19:00、第三节落到 08:00",
                not boom and s == "删除周二 10:50 的《体育》"
                and s2 == "删除周二 19:00 的《大学英语》", boom or f"{s} | {s2}")

        write_timetable(_seed())

        def go(s):
            try:
                r = PR(s)
                return (r or {}).get("summary") if r else None, ""
            except Exception as e:                    # noqa: BLE001
                return None, f"{type(e).__name__}: {e}"

        s8, b8 = go("删除周二第三节『大学英语』")
        c.check("T8 引号点名 + 节次：两处都指同一门时按这一门出卡（start 必须跟着带）",
                not b8 and s8 == "删除周二 19:00 的《大学英语》", b8 or str(s8))
        s9, b9 = go("删除周三第二节『数据结构』")
        c.check("T8-bis 引号点名的是**同名两门**时，靠第二门那个 start 才分得开（10:05 不是 08:00）",
                not b9 and s9 == "删除周三 10:05 的《数据结构》", b9 or str(s9))
        s10, b10 = go("删除周二下午第二节")
        c.check("T9 现状登记：『下午第二节』也按'那天第二门'给（10:50 体育）—— 编号不分上下午。"
                "⚠️ 施工单 §四.3 声称这种话会追问，**实测不成立**（改前给 08:00 那门，也没追问）。"
                "要不要加'半天不符就追问'的守卫等用户拍板；加的话这一条要一起改口",
                not b10 and s10 == "删除周二 10:50 的《体育》", b10 or str(s10))
        s11, b11 = go("删除第二节")
        c.check("T10 没说哪天仍追问（#110 那条纪律没被本单带松）",
                not b11 and s11 is None, b11 or str(s11))
    return c


def batch_c_no_collateral():
    c = Checker()
    title("C. 没牵动别人（§四.1/§四.2 那张表还在给排待办用）")
    with sandbox():
        write_timetable(_seed())
        from app.modules import planner as PL

        ps = PL.period_span("第一节课的位置")
        c.check("C1 `period_span` 照旧回**时间段**（08:00~09:40）—— 本单没动那张表，"
                "排待办要的是一个窗，不是'哪一门课'", ps == ("08:00", "09:40"), str(ps))

        # 用 AST 数"代码里的读取"，不许拿字符串搜 —— 本单新增的注释与 docstring 里
        # 也写着 `_PERIOD_START` 这个名字，文本搜会把它们当成读取点（我自己第一版就是这么红的）。
        import ast
        tree = ast.parse(open(PL.__file__, encoding="utf-8").read())
        loads = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in ("_PERIOD_START", "_PERIOD_SPAN") \
                    and isinstance(node.ctx, ast.Load):
                loads.setdefault(node.id, []).append(node.lineno)
        c.check("C2/C3 `_PERIOD_START` 现在**没有任何代码读它**（改前唯一的读者就是删课 ② 层）—— "
                "钉这条是因为这张表留在文件里就像还作数，下一轮很可能又有人拿它反查课名。"
                "单据 §四.1 说别动它，这里只钉事实、不删表（删不删等用户拍板）",
                loads.get("_PERIOD_START", []) == [], str(loads.get("_PERIOD_START")))
        _spans = loads.get("_PERIOD_SPAN", [])
        _lines = open(PL.__file__, encoding="utf-8").read().splitlines()
        c.check("C3-bis `_PERIOD_SPAN` 照旧**只有 `period_span` 里那一次**读取 ⇒ 排待办那一路没被本单牵动"
                "（钉'恰好一处 + 就在那函数体内'，不写行号 —— 行号会漂）",
                len(_spans) == 1 and "return _PERIOD_SPAN.get" in _lines[_spans[0] - 1],
                f"{_spans} → {(_lines[_spans[0] - 1].strip() if len(_spans) == 1 else '')[:52]}")

        client = make_client()
        from app import store as S
        n_full = len(S.get_timetable())
        try:
            d = client.post("/api/chat", json={
                "message": "删除周二第二节", "module": "planner", "session_id": "s113-a"}).json()
            boom = ""
        except Exception as e:                        # noqa: BLE001
            d, boom = {}, f"{type(e).__name__}: {e}"
        opts = d.get("options") or []
        c.check("C4 端到端：学生原话一句就出卡，卡上写的是 10:50《体育》（不是 08:00 那门）",
                not boom and any(o.get("kind") == "timetable_change" for o in opts)
                and "10:50" in str(opts[-1].get("summary", ""))
                and "体育" in str(opts[-1].get("summary", "")),
                boom or (d.get("answer") or "")[:80])
        c.check("C4-bis 出卡阶段周表没动（本函数只算；走查全程点【取消】就一个字不写）",
                len(S.get_timetable()) == n_full, f"{len(S.get_timetable())} vs {n_full}")
    return c


if __name__ == "__main__":
    code = 0
    for fn in (batch_a_nth_course, batch_b_order_and_boundaries, batch_c_no_collateral):
        code |= fn().summary(f"test_113 {fn.__name__} ")
    raise SystemExit(code)
