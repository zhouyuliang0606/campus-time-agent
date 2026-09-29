"""定位待办：中文数字日期 + 「撞时间的」筛选 + 盲删守卫。

这一批钉住一个真实报障（学生截图）：
  学生说「10月三号两个撞时间的代办删一下」，系统却回「没找到」，
  甚至拿他最近加的那条来顶（比如 09-27 的"写作业"）。三个根因：
  A) 日期只认阿拉伯数字，「10月三号」（日是中文数字）被当成"没说日期"；
  B) "撞时间的""两个"这类修饰词没擦掉，被当成待办名 → 对不上任何待办 → 误报"没找到"；
  C) "没说清是哪条"时走的**盲删**分支，会把"最近写入的那条"补进来——
     可学生明明给了日期 + 筛选条件，这本就不是盲删。

对应改动：planner._CN_MD_RE / _num / _TODO_NAME_NOISE / _COLLIDE_WORDS /
         _wants_colliding / _colliding_subset；main._is_blind_delete 加筛选信息守卫。
"""
import datetime as _dt

from _harness import Checker, make_client, sandbox, title


def _seed(today, d, other):
    """往沙箱里写一组待办：一对"撞时间"的（同天同时段）+ 一条不撞的。

    先清空，免得继承自 REAL_DATA_DIR 的演示数据里恰好也有同名/同时段的条目。
    """
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    add_todo("甲", d, "14:00", "15:00")
    add_todo("乙", d, "14:00", "15:00")   # 跟"甲"同一时段 → 撞
    add_todo("丙", d, "20:00", "20:30")   # 不撞
    return


def batch_a_parse_unit():
    """A 单元：中文数字日期认得出；"撞时间的/两个"不当成待办名。"""
    title("A. 解析：中文数字日期 + 修饰词清洗")
    c = Checker()
    from app.modules.planner import (_pick_date, _pick_todo_name,
                                     _wants_colliding, _colliding_subset)

    year = _dt.date.today().year
    # 日的三种写法都该认成同一天（年月按今年算）
    c.check("「10月3号」认成日期", _pick_date("10月3号撞时间的待办删了") == f"{year}-10-03",
            _pick_date("10月3号撞时间的待办删了"))
    c.check("「10月三号」（日=中文数字）也认成同一天",
            _pick_date("10月三号两个撞时间的代办删一下") == f"{year}-10-03",
            _pick_date("10月三号两个撞时间的代办删一下"))
    c.check("「十月三日」（月日都是中文）也认得出",
            _pick_date("十月三日撞时间的待办删了") == f"{year}-10-03",
            _pick_date("十月三日撞时间的待办删了"))

    # 修饰词/量词不该留在待办名里
    c.check("「10月三号两个撞时间的代办」抠不出名字（都是修饰词）",
            _pick_todo_name("10月三号两个撞时间的代办删一下") == "",
            repr(_pick_todo_name("10月三号两个撞时间的代办删一下")))
    c.check("真名字还是好好的（「删掉10月3号那条游泳」→ 游泳）",
            _pick_todo_name("删掉10月3号那条游泳") == "游泳",
            repr(_pick_todo_name("删掉10月3号那条游泳")))

    # 「撞时间的」= 筛条件
    c.check("「撞时间的」被识别为筛条件", _wants_colliding("撞时间的待办删掉"))
    c.check("普通删除句不带筛条件", not _wants_colliding("删掉明天的待办"))
    pair = [{"date": "2026-10-03", "start": "14:00", "end": "15:00"},
            {"date": "2026-10-03", "start": "14:00", "end": "15:00"},
            {"date": "2026-10-03", "start": "20:00", "end": "20:30"}]
    c.check("挑出撞时间的那对（同天同时段），不撞的不算",
            len(_colliding_subset(pair)) == 2,
            len(_colliding_subset(pair)))
    c.check("孤零零一条不算撞", _colliding_subset(pair[2:]) == [])
    return c


def batch_b_blind_guard():
    """B 单元：带了日期 / 筛选信息 → 不算"盲删"，别拿最近写入的那条顶。"""
    title("B. 盲删守卫：给了日期或筛条件就不当盲删")
    c = Checker()
    from app.main import _is_blind_delete

    c.check("「删除撞时间的代办」不算盲删（给了筛条件）",
            _is_blind_delete("删除撞时间的代办") is False)
    c.check("「10月三号两个撞时间的代办删一下」不算盲删（给了日期）",
            _is_blind_delete("10月三号两个撞时间的代办删一下") is False)
    c.check("「删掉 2026-10-03 那条」不算盲删（给了日期）",
            _is_blind_delete("删掉 2026-10-03 那条") is False)
    # 真·盲删还得认（"这条/那个/刚加的"）
    c.check("「把刚才那条删了」仍然算盲删",
            _is_blind_delete("把刚才那条删了") is True)
    return c


def batch_c_e2e():
    """C 端到端：口述"撞时间的待办删一下"→ 列清单（不再"没找到"、不再拿最近写入的顶）。"""
    title("C. 端到端：撞时间删除 → 列清单；带近期写入也不误顶")
    c = Checker()
    from app.store import list_todos

    d = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()
    with sandbox():
        client = make_client()
        _seed(_dt.date.today().isoformat(), d, d)

        # ① 只给"撞时间的" → 只列撞的那对（甲/乙），不列"丙"
        sid = "tt-1"
        r1 = client.post("/api/chat", json={
            "message": "把撞时间的待办删掉", "module": "planner", "session_id": sid})
        d1 = r1.json()
        ans1 = (d1.get("answer") or "")
        c.check("没再报「没找到」（列了清单）",
                "没找到" not in ans1 and ("甲" in ans1 or "乙" in ans1),
                ans1.replace("\n", " / ")[:100])
        c.check("只列撞时间的那对，不撞的「丙」不在清单里",
                "丙" not in ans1)
        c.check("列清单阶段不出确认条（不替学生挑）",
                not (d1.get("options") or []))

        # ② 带近期写入（先在会话里加一条）→ 再发撞时间删除，**不能**拿它来顶
        sid2 = "tt-2"
        client.post("/api/chat", json={
            "message": "帮我加个锻炼 明天 13:00-14:00", "module": "planner",
            "session_id": sid2})
        client.post("/api/chat", json={"message": "确认", "session_id": sid2})
        r2 = client.post("/api/chat", json={
            "message": "删除撞时间的代办", "module": "planner", "session_id": sid2})
        ans2 = (r2.json().get("answer") or "")
        c.check("有近期写入时也不拿「锻炼」来顶（坏代码会出「锻炼」确认条）",
                "锻炼" not in ans2 and ("甲" in ans2 or "乙" in ans2),
                ans2.replace("\n", " / ")[:100])
    return c


def batch_d_index_unit():
    """D 单元：序号点名（第N条 / 光秃秃数字 / 连写 / 一堆数字）。"""
    title("D. 序号点名：第N条 · 纯数字 · 连写 · 越界")
    c = Checker()
    from app.modules.planner import (_pick_todo_indexes,
                                     resolve_todo_remove_reply, resolve_todo_done_reply)

    cands = [{"id": str(i), "title": f"T{i}", "date": "2026-10-03",
              "start": "14:00", "end": "15:00"} for i in range(1, 5)]
    c.check("「第二条」→ [2]", _pick_todo_indexes("第二条", 4) == [2])
    c.check("光秃秃「2」→ [2]", _pick_todo_indexes("2", 4) == [2])
    c.check("连写「23」→ [2,3]（一次点两条）", _pick_todo_indexes("23", 4) == [2, 3])
    c.check("「第2、3条」→ [2,3]", _pick_todo_indexes("第2、3条", 4) == [2, 3])
    c.check("「最后一条」→ [-1]", _pick_todo_indexes("最后一条", 4) == [-1])
    c.check("越界的「5」→ 空", _pick_todo_indexes("5", 4) == [])

    r = resolve_todo_remove_reply("第二条", cands)
    c.check("回「第二条」→ 出第 2 条确认条",
            r["status"] == "ok" and r["proposal"]["title"] == "T2",
            r.get("proposal", {}).get("title"))
    r2 = resolve_todo_remove_reply("23", cands)
    c.check("回「23」→ 不当名字乱找（列出 2 条、一次只能删一条）",
            r2["status"] == "many" and len(r2["todos"]) == 2, r2.get("status"))
    r3 = resolve_todo_remove_reply("随便说点啥", cands)
    c.check("手里有清单时乱答也不报「没找到」（回 unclear 且带上清单）",
            r3["status"] == "unclear" and len(r3["todos"]) == 4, r3.get("status"))
    r4 = resolve_todo_done_reply("2", cands)
    c.check("标记完成的序号回复同样接得住",
            r4["status"] == "ok" and r4["proposal"]["title"] == "T2",
            r4.get("proposal", {}).get("title"))
    return c


def batch_e_sem_guard():
    """E 回归：语义层把「第二条」误判成 remove_todo 时，followup 也不能被关掉。

    真机复现：模型对「第二条」返回 intent=remove_todo（confidence 0.85）。
    旧代码 followup = asking_rm_todo and not rm_intent → 被 rm_intent 顶掉 →
    这句当成"全新的删除指令"再解析一遍 → 报"没找到"（学生截图："记忆有问题"）。
    """
    title("E. 语义层误判「第二条」＝remove_todo 时，仍不丢候选清单")
    c = Checker()
    import app.main as M

    async def fake_classify(msg, conv, override=None):
        # 模拟真机：把"第二条 / 纯数字"判成 remove_todo
        if "第" in msg or msg.strip().isdigit():
            return {"module": "planner", "intent": "remove_todo",
                    "confidence": 0.85, "source": "llm"}
        return None

    d = (_dt.date.today() + _dt.timedelta(days=4)).isoformat()
    orig = M.classify
    M.classify = fake_classify
    try:
        with sandbox():
            client = make_client()
            _seed(_dt.date.today().isoformat(), d, d)
            sid = "sem-guard"
            client.post("/api/chat", json={
                "message": "把撞时间的待办删掉", "module": "planner", "session_id": sid})
            r = client.post("/api/chat", json={
                "message": "第二条", "module": "planner", "session_id": sid})
            dj = r.json()
            opts = dj.get("options") or []
            c.check("语义层误判下回「第二条」仍出确认条（没被当成新指令）",
                    any(o.get("kind") == "todo_remove" for o in opts), str(opts[:1])[:90])
            c.check("没有掉进「没找到」",
                    "没找到" not in (dj.get("answer") or ""),
                    (dj.get("answer") or "")[:60])
    finally:
        M.classify = orig
    return c


def main():
    results = [
        batch_a_parse_unit(),
        batch_b_blind_guard(),
        batch_c_e2e(),
        batch_d_index_unit(),
        batch_e_sem_guard(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 待办定位（中文日期/撞时间/盲删守卫）总账：通过 {total_pass} 项，"
          f"失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
