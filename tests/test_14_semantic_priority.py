"""上下文优先序：正在等「删待办」的回复时，语义层的弱信号不许把话抢走。

背景（真机实测，学生回「游泳」系统却弹出一张"标记完成"卡）：
上一句系统刚列了删待办的候选清单（"1. 游泳 2. 游泳 …"），学生回一个名字是在**点名**；
可**语义层**只看这两个字，判成了 `done_todo`（"办完了"）。而标记完成分支排在
删待办分支**前面**，于是这句被它接走——判定层再怎么按上下文解析都没机会跑。

判据分强弱两层，只让**弱信号**让位：
  · 规则层明说"这是标记完成"（"把游泳标记完成"）→ 硬信号，标记完成还是接；
  · 只有语义层猜 done（主语就俩字名字）→ 弱信号，让给删待办。

⚠️ 这里必须**把语义层替成一个假货**才能测出来：沙箱里没有密钥，
   真实语义层是静默返回 None 的——不替，这个 bug 在测试里永远看不到
   （"测试全绿、真机照样错"就是这么来的）。

对应改动：main.py 3h 标记完成分支的入口闸门（`_rm_reply_pending and not _done_hard`），
         以及 3g 复用同一批判据（不再算第二遍）。
"""
from _harness import Checker, make_client, sandbox, title

_D = "2026-10-03"

# 故意把这些话都判成 done_todo —— 断言的是"删待办的上下文优先"，
# 跟模型猜什么无关（真机上它就是把「游泳」猜成了 done）。
_FAKE_DONE = ("游泳", "交电费", "晨读", "换一条", "第二条")


class fake_semantic:
    """上下文管理器：把 main 里的语义层换成"见名字就判 done"的假货。

    ⚠️ 要打补丁到 **app.main 这个模块的名字上**，不能打 app.agent.intent.classify：
       模块顶部的 `from X import Y` 是**导入时绑定**的，换原模块里的函数看不出来
       （这个坑在本项目里踩过：语义层于是"安静地"退回关键词表，异常被 except 吞掉）。
    """

    def __enter__(self):
        import app.main as M

        async def _fake(message, convo=None, override=None):
            if (message or "").strip() in _FAKE_DONE:
                return {"module": "planner", "intent": "done_todo", "source": "fake"}
            return None

        self._old = M.classify
        M.classify = _fake
        return self

    def __exit__(self, *exc):
        import app.main as M
        M.classify = self._old
        return False


def _seed_three_pairs():
    """三对重复（游泳×2 / 交电费×2 / 晨读×2）——学生截图里的处境。"""
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("交电费", _D, "19:00", "19:30")
    add_todo("交电费", _D, "19:00", "19:30")
    add_todo("晨读", _D, "07:00", "07:30")
    add_todo("晨读", _D, "07:00", "07:30")


def _ask(client, sid, msg):
    return client.post("/api/chat", json={"message": msg, "module": "planner",
                                          "session_id": sid}).json()


def _kinds(d):
    return [o.get("kind") for o in (d.get("options") or [])]


def batch_a_weak_done_loses():
    """A 主案：清单摆着 → 回名字 → 走**删待办**（不因语义层猜 done 就跑偏）。"""
    title("A. 有删候选清单时，语义层猜的 done 不许抢单")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "sp-a"
        d1 = _ask(client, sid, "删除撞时间的代办")
        c.check("第一步照旧列清单（不猜）",
                not (d1.get("options") or []) and "游泳" in (d1.get("answer") or ""),
                (d1.get("answer") or "").replace("\n", " ")[:60])

        with fake_semantic():
            d2 = _ask(client, sid, "游泳")
            c.check("语义层判 done 也不许抢 → 出的必须是**删待办**卡，不是标记完成卡",
                    _kinds(d2) == ["todo_remove"], str(_kinds(d2)))
            c.check("回话是删待办的口气（不是「要标记为完成的是这一条」）",
                    "标记为完成" not in (d2.get("answer") or ""),
                    (d2.get("answer") or "").replace("\n", " ")[:70])
            c.check("没确认 → 6 条一条没少（红线）", len(list_todos()) == 6,
                    f"{len(list_todos())}")
    return c


def batch_b_hard_done_still_wins():
    """B 守卫：学生**明说**"标记完成"时，标记完成还是要接（别把硬信号也压掉）。"""
    title("B. 明说「标记完成」→ 硬信号仍然赢（只是弱信号让位）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "sp-b"
        _ask(client, sid, "删除撞时间的代办")          # 先把删候选清单摆上
        with fake_semantic():
            d = _ask(client, sid, "把游泳标记完成")
            c.check("出的是标记完成的口气（✅），不是删待办卡",
                    "✅" in (d.get("answer") or "") and "todo_remove" not in _kinds(d),
                    str(_kinds(d)) + " | " + (d.get("answer") or "").replace("\n", " ")[:50])
            c.check("同名两条 → 列清单不猜（还是那条老规矩）",
                    not (d.get("options") or []))
            c.check("没确认 → 状态没被改（6 条都在、都是 planned）",
                    len(list_todos()) == 6
                    and all(t.get("status") != "done" for t in list_todos()))
    return c


def batch_c_rechoose_wins():
    """C：系统替他挑过一条后说「换一条」→ 回到清单（语义层猜 done 也拦不住）。"""
    title("C. 卡上的出口「换一条」不能被语义层堵死")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "sp-c"
        _ask(client, sid, "删除撞时间的代办")
        with fake_semantic():
            d2 = _ask(client, sid, "游泳")
            c.check("先出卡（准备条件）", _kinds(d2) == ["todo_remove"], str(_kinds(d2)))
            d3 = _ask(client, sid, "换一条")
            c.check("回「换一条」→ 重新列清单，不出卡",
                    not (d3.get("options") or []) and "游泳" in (d3.get("answer") or ""),
                    (d3.get("answer") or "").replace("\n", " ")[:60])
            c.check("全程没确认 → 6 条一条没少", len(list_todos()) == 6)
    return c


def batch_d_index_wins():
    """D：报序号 → 按序号选中（清单还在，没被标记完成分支吃掉）。"""
    title("D. 报序号仍然按序号选中")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()

        sid = "sp-d"
        _ask(client, sid, "删除撞时间的代办")
        with fake_semantic():
            d = _ask(client, sid, "第二条")
            c.check("回「第二条」→ 出删待办卡（按序号选中）",
                    _kinds(d) == ["todo_remove"], str(_kinds(d)))
    return c


def batch_e_hard_remove_wins():
    """E：清单摆着时又说了句**明确要删**的（"把游泳删掉"）→ 还是删待办接。"""
    title("E. 明确要删的话，照旧归删待办")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()

        sid = "sp-e"
        _ask(client, sid, "删除撞时间的代办")
        with fake_semantic():
            d = _ask(client, sid, "把游泳删掉")
            c.check("出删待办卡（不被 done 抢）", _kinds(d) == ["todo_remove"],
                    str(_kinds(d)))
    return c


def batch_f_guard_no_context():
    """F 守卫：**没有**删候选清单时，「游泳」该按标记完成走（别把 done 分支关死）。"""
    title("F. 没有删待办上下文 → 标记完成照旧受理")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()

        with fake_semantic():
            d = _ask(client, "sp-f", "游泳")
            c.check("语义层说 done、又没别的上下文 → 走标记完成（列清单问是哪条）",
                    "✅" in (d.get("answer") or "") and not _kinds(d),
                    (d.get("answer") or "").replace("\n", " ")[:60])
    return c


def main():
    results = [
        batch_a_weak_done_loses(),
        batch_b_hard_done_still_wins(),
        batch_c_rechoose_wins(),
        batch_d_index_wins(),
        batch_e_hard_remove_wins(),
        batch_f_guard_no_context(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 上下文优先序（谁抢单）总账：通过 {total_pass} 项，"
          f"失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
