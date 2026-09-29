"""口语样例回归：学生**真会说出口**的那批话（去重 / 全删 / 单条 / 纠正 / 模糊 / 省略 / 错别字）+ RAG 知识库问答。

为什么要单独一批：前面几批（test_12/13/14）是把**每一种意图**各钉一条标准说法。
可学生的嘴不是标准说法——他会说「每个删一个」「都删了」「第二条」「不是这个，是交电费」，
还会把「游泳」打成「游永」、把「删掉」打成「删悼」。这一批专门喂这些**人话**，
每条都写成"一个完整的小对话"（前情 → 这句 → 看结果），而不是只测一句话。

红线贯穿全批：**只要学生没点头，一条都不许删。**
每一批最后都核对库里的条数，等于把"猜测"和"执行"这两件事彻底分开。

⚠️ 沙箱没有密钥，语义层静默返回 None，所以这一批**只测确定性分支**（删/改/读）。
   语义层那条路怎么测，见 `test_14_semantic_priority.py`（它把语义层替成了假货）。
"""
from _harness import Checker, make_client, sandbox, title

_D = "2026-10-06"


def _seed_three_pairs():
    """三对重复 + 时间互相交叠（这样"撞时间的"能一次捞出 6 条，正是学生截图里的处境）。"""
    from app.store import add_todo, delete_todo, list_todos
    for t in list_todos():
        delete_todo(t["id"])
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("游泳", _D, "14:00", "15:00")
    add_todo("交电费", _D, "14:30", "15:30")
    add_todo("交电费", _D, "14:30", "15:30")
    add_todo("晨读", _D, "14:00", "15:00")
    add_todo("晨读", _D, "14:00", "15:00")


def _say(client, sid, msg):
    return client.post("/api/chat", json={"message": msg, "module": "planner",
                                          "session_id": sid}).json()


def _kinds(d):
    return [o.get("kind") for o in (d.get("options") or [])]


def _batch_titles(d):
    o = (d.get("options") or [{}])[0]
    return [i.get("title") for i in (o.get("items") or [])]


# ─────────────────────────────────────────────────────────────────────────────
def batch_a_dedupe():
    """A. 每组重复删掉一条（去重）——三种说法都要认得。"""
    title("A. 「每个删一个 / 每组删一个 / 重复的删掉一个」= 去重，不是全删")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos
        for i, word in enumerate(("每个删一个", "每组删一个", "重复的删掉一个")):
            sid = f"oral-a{i}"
            _say(client, sid, "删除撞时间的代办")
            d = _say(client, sid, word)
            c.check(f"「{word}」→ 出**去重**批量卡（3 条，每组一条）",
                    _kinds(d) == ["todo_remove_batch"] and len(_batch_titles(d)) == 3,
                    f"{_kinds(d)} {_batch_titles(d)}")
            c.check(f"「{word}」→ 没确认，6 条一条没删（红线）",
                    len(list_todos()) == 6, f"{len(list_todos())}")
    return c


def batch_b_delete_all():
    """B. 全部删掉——接着上一句清单的"都/全"。"""
    title("B. 「都删了 / 全清了」= 你列的这几条全都要")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos
        for i, word in enumerate(("都删了", "全清了", "一起删了吧")):
            sid = f"oral-b{i}"
            _say(client, sid, "删除撞时间的代办")
            d = _say(client, sid, word)
            c.check(f"「{word}」→ 出**全删**批量卡（6 条）",
                    _kinds(d) == ["todo_remove_batch"] and len(_batch_titles(d)) == 6,
                    f"{_kinds(d)} {len(_batch_titles(d))}")
            c.check(f"「{word}」→ 没确认，6 条一条没删", len(list_todos()) == 6)
    return c


def batch_c_delete_one():
    """C. 删掉指定单条——点名 / 口语 / 报序号，三种都要准。"""
    title("C. 删掉指定单条（点名 / 口语 / 报序号）")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        d = _say(client, "oral-c0", "把游泳删了")
        c.check("「把游泳删了」→ 游泳的确认卡",
                _kinds(d) == ["todo_remove"] and (d["options"][0]["title"]) == "游泳",
                f"{_kinds(d)}")

        d2 = _say(client, "oral-c1", "帮我把交电费去掉呗")
        c.check("「帮我把交电费去掉呗」→ 交电费（语气词「呗」不污染名字）",
                _kinds(d2) == ["todo_remove"] and d2["options"][0]["title"] == "交电费",
                str(d2["options"][0]["title"] if d2.get("options") else ""))
        c.check("卡上的依据里名字是干净的「交电费」（不是「把交电费呗」）",
                "把交电费呗" not in (d2["options"][0].get("note") or ""),
                (d2["options"][0].get("note") or "")[:40])

        _say(client, "oral-c2", "删除撞时间的代办")
        d3 = _say(client, "oral-c2", "第二条")
        c.check("「第二条」→ 按序号选中（清单还记着）",
                _kinds(d3) == ["todo_remove"], f"{_kinds(d3)}")
        c.check("全程没确认 → 6 条一条没删", len(list_todos()) == 6, f"{len(list_todos())}")
    return c


def batch_d_correction():
    """D. 纠正对话——「不对」要能推翻；「不是这个，是X」要按新名字切换。"""
    title("D. 纠正：说「不对」能重来，说「是X」就照 X 办")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "oral-d1"
        _say(client, sid, "删除撞时间的代办")
        _say(client, sid, "游泳")
        d = _say(client, sid, "不对")
        c.check("「不对」→ 撤销刚才那张卡、重新列清单（不出卡）",
                not _kinds(d) and "游泳" in (d.get("answer") or ""), f"{_kinds(d)}")

        sid2 = "oral-d2"
        _say(client, sid2, "删除撞时间的代办")
        _say(client, sid2, "游泳")
        d2 = _say(client, sid2, "不是这个，是交电费")
        c.check("「不是这个，是交电费」→ **按新名字切换**到交电费（不是傻傻重列）",
                _kinds(d2) == ["todo_remove"]
                and d2["options"][0]["title"] == "交电费",
                f"{_kinds(d2)} "
                f"{d2['options'][0]['title'] if d2.get('options') else ''}")
        c.check("依据里的名字是「交电费」（不是「是交电费」）",
                "是交电费" not in (d2["options"][0].get("note") or ""),
                (d2["options"][0].get("note") or "")[:40])
        c.check("纠正全程没确认 → 6 条一条没删", len(list_todos()) == 6)
    return c


def batch_e_vague():
    """E. 模糊口语——听不出是哪条时，**不许乱删**，也不许跑去加待办。"""
    title("E. 模糊口语：认不出是哪条 → 列清单问，绝不乱删、绝不跑去下单")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos
        for i, word in enumerate(("把那个啥删了", "上次那个事不要了", "帮我去掉那个重复的")):
            sid = f"oral-e{i}"
            d = _say(client, sid, word)
            ans = d.get("answer") or ""
            c.check(f"「{word}」→ **不许**出加待办卡（别跑去给他排空档）",
                    "todo_add" not in _kinds(d) and "todo_slots" not in _kinds(d)
                    and "空档" not in ans, f"{_kinds(d)} {ans[:40]}")
            c.check(f"「{word}」→ 列清单问是哪一条（不是直接删、也不是干巴巴说没找到）",
                    "游泳" in ans and not _kinds(d), ans.replace("\n", " ")[:50])
            c.check(f"「{word}」→ 没确认，6 条一条没删", len(list_todos()) == 6)
    return c


def batch_f_ellipsis():
    """F. 省略句——"都删了""第二条"这种没主语的话，靠上一句补。"""
    title("F. 省略句：没主语的话靠上一句清单补")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        sid = "oral-f1"
        _say(client, sid, "删除撞时间的代办")
        d = _say(client, sid, "都删了")
        c.check("「都删了」（三个字，没主语）→ 接住上一句那份清单，出全删卡",
                _kinds(d) == ["todo_remove_batch"] and len(_batch_titles(d)) == 6,
                f"{_kinds(d)}")

        sid2 = "oral-f2"
        _say(client, sid2, "删除撞时间的代办")
        d2 = _say(client, sid2, "再删一条")
        c.check("「再删一条」→ 说不清是哪条 → 列清单（不猜）",
                not _kinds(d2) and "游泳" in (d2.get("answer") or ""),
                f"{_kinds(d2)}")
        c.check("全程没确认 → 6 条一条没删", len(list_todos()) == 6)
    return c


def batch_g_typos():
    """G. 自然对话（带错别字，系统自己认）——打错字也要找得到，还要**说清是猜的**。"""
    title("G. 错别字：打错字也要找得到，且必须写明「我当你是打错字了」")
    c = Checker()
    with sandbox():
        client = make_client()
        _seed_three_pairs()
        from app.store import list_todos

        d = _say(client, "oral-g1", "把游永删了")          # 游泳 → 游永
        c.check("「把游永删了」→ 认出是《游泳》（不是「没找到」）",
                _kinds(d) == ["todo_remove"] and d["options"][0]["title"] == "游泳",
                f"{_kinds(d)}")
        c.check("卡上写明「我是按最接近的找的」（不许假装精确命中）",
                d["options"][0].get("picked_by") == "fuzzy_name"
                and "最接近" in (d["options"][0].get("note") or ""),
                (d["options"][0].get("note") or "")[:50])

        d2 = _say(client, "oral-g2", "删悼交电费")           # 删掉 → 删悼
        c.check("「删悼交电费」→ 认成**删除**（不是跑去加待办排空档）",
                _kinds(d2) == ["todo_remove"] and d2["options"][0]["title"] == "交电费",
                f"{_kinds(d2)}")
        d3 = _say(client, "oral-g3", "把晨读删叼")            # 删掉 → 删叼
        c.check("「把晨读删叼」→ 认成删除晨读",
                _kinds(d3) == ["todo_remove"] and d3["options"][0]["title"] == "晨读",
                f"{_kinds(d3)}")

        d4 = _say(client, "oral-g4", "游泳删掉把")            # 倒装 + 句尾"把"
        c.check("「游泳删掉把」（倒装）→ 还是游泳，名字不带「把」",
                _kinds(d4) == ["todo_remove"] and d4["options"][0]["title"] == "游泳"
                and "游泳把" not in (d4["options"][0].get("note") or ""),
                (d4["options"][0].get("note") or "")[:40])
        c.check("错别字全程没确认 → 6 条一条没删", len(list_todos()) == 6)
    return c


# ─────────────────────────────────────────────────────────────────────────────
def batch_h_kb():
    """H. RAG 知识库问答——问的是校园信息，就别拿"查日程"去兜。"""
    title("H. RAG 知识库：问校园信息 → 翻知识库，不被「查日程/加待办」抢走")
    c = Checker()
    with sandbox():
        from app.modules.faq import search_kb

        # ① 知识库该答出来的：每条问句都要翻到对应的那一问
        cases = [
            ("图书馆几点关门", "图书馆"),
            ("校车在哪里坐", "校车"),
            ("宿舍晚上几点断电", "断电"),
            ("食堂营业到几点", "食堂"),
            ("校医院在哪", "校医院"),
            ("怎么申请奖学金", "奖学金"),
            ("学生证丢了怎么补办", "学生证"),
            ("校园网怎么连", "校园网"),
            ("快递点在哪", "快递"),
            ("教务系统网址是多少", "教务"),
        ]
        for q, must in cases:
            ans = search_kb(q)
            c.check(f"问「{q}」→ 知识库翻到含「{must}」的那条",
                    must in ans and not ans.startswith("知识库里没有找到"),
                    ans[:40].replace("\n", " "))

        # ② 打错字也要翻得到（"图收馆" → 图书馆）
        c.check("「图收馆几点关门」（错别字）→ 也能翻到图书馆",
                "图书馆" in search_kb("图收馆几点关门"),
                search_kb("图收馆几点关门")[:40].replace("\n", " "))
        # ③ 知识库真没有的 → 诚实说没有，绝不编
        c.check("「校长办公室电话多少」→ 诚实说没找到（不许编）",
                search_kb("校长办公室电话多少").startswith("知识库里没有找到"),
                search_kb("校长办公室电话多少")[:30])

        # ④ 走接口：问校园信息不该被"查日程那条路"吃成"确实还没有 X"
        #    （判据本身也单独钉一条——接口那条在沙箱里没密钥，答的是"没配密钥"，
        #     容易变成什么都没验证就绿了）
        from app.main import _kb_has_answer
        c.check("判据本身：「校车在哪里坐」→ 知识库**有**答案（所以不该被查日程接走）",
                _kb_has_answer("校车在哪里坐") is True)
        # ⚠️ 这一条是**故意**留成 True 的，也是那道门存在的理由：这句话里真有"校车"
        #    两个字，知识库当然答得出校车时刻表——可学生说的是"我的校车**待办**显示不出来"，
        #    是在报障。所以让不让路不能只看"知识库有没有答案"，还得看句子里
        #    有没有"待办/日程"这类词（见 main.py 那道门）。只测前者会漏掉这一整类。
        c.check("判据本身：「我的校车待办显示不出来」→ 知识库**答得出**（校车两字在里头）"
                "——所以那道门必须再看句子里有没有「待办」",
                _kb_has_answer("我的校车待办显示不出来") is True)
        c.check("判据本身：「校长办公室电话多少」→ 知识库没有答案（绝不拿校车糊弄）",
                _kb_has_answer("校长办公室电话多少") is False)
        client = make_client()
        d = _say_faq(client, "oral-h1", "校车在哪里坐")
        c.check("接口问「校车在哪里坐」→ 不被「查日程」接走（这句里没有待办/日程/周表）",
                "确实还没有" not in (d.get("answer") or "")
                and "校车坐" not in (d.get("answer") or ""),
                (d.get("answer") or "")[:50].replace("\n", " "))
        # ⑤ 反过来：真说自己日程的报障，**不能**被知识库抢走
        d2 = _say_faq(client, "oral-h2", "我的校车待办显示不出来")
        c.check("「我的**校车待办**显示不出来」→ 仍是报障（知识库不该抢）",
                "待办" in (d2.get("answer") or "")
                or "还没有" in (d2.get("answer") or ""),
                (d2.get("answer") or "")[:50].replace("\n", " "))
    return c


def batch_i_real_machine_gaps():
    """I. **真机才暴露**的两处（沙箱没密钥时这两条会"假绿"，必须单独钉住）。"""
    title("I. 真机才看得见：打错字只捞到一条也要声明 / 问校园信息不许交给模型现编")
    c = Checker()
    with sandbox():
        client = make_client()
        from app.store import add_todo, delete_todo, list_todos

        # ① 库里**只有一条**时打错字：也要写明"我当你是打错字了"
        #    （真机实测：这一支早先走的是"唯一命中"，卡上一个字都不提，
        #     学生以为本来就有一条叫这个的，看都不看就点确认）
        for t in list_todos():
            delete_todo(t["id"])
        add_todo("写作业", _D, "19:00", "20:00")
        d = _say(client, "oral-i1", "把写做业删了")
        o = (d.get("options") or [{}])[0]
        c.check("只捞到一条时打错字 → 仍写明「我当你是打错字了」（不许假装精确命中）",
                _kinds(d) == ["todo_remove"] and o.get("picked_by") == "fuzzy_name"
                and "打错字" in (o.get("note") or ""),
                f"{o.get('picked_by')} | {(o.get('note') or '')[:30]}")

        # ② 日程栏问校园信息 → 翻知识库直答，别掉给模型去现编
        d2 = _say(client, "oral-i2", "校车在哪里坐")
        ans2 = d2.get("answer") or ""
        c.check("日程栏问「校车在哪里坐」→ **翻知识库**答出来（不是模型现编）",
                "在知识库中找到" in ans2 and "校车" in ans2 and not _kinds(d2),
                ans2.replace("\n", " ")[:60])
        # ③ 反过来：带"待办"两个字的报障句，知识库不许抢
        d3 = _say(client, "oral-i3", "我的校车待办显示不出来")
        c.check("「我的校车**待办**显示不出来」→ 知识库不抢（那是报障）",
                "在知识库中找到" not in (d3.get("answer") or ""),
                (d3.get("answer") or "").replace("\n", " ")[:50])
        # ④ 带删除意图的句子（哪怕里面有"校车"两字）也不许被知识库抢
        d4 = _say(client, "oral-i4", "把校车删了")
        c.check("「把校车删了」→ 走删待办那条路（知识库不许抢）",
                "在知识库中找到" not in (d4.get("answer") or ""),
                (d4.get("answer") or "").replace("\n", " ")[:50])
        c.check("全程没确认 → 1 条没删", len(list_todos()) == 1, f"{len(list_todos())}")
    return c


def _say_faq(client, sid, msg):
    return client.post("/api/chat", json={"message": msg, "module": "faq",
                                          "session_id": sid}).json()


def main():
    results = [
        batch_a_dedupe(),
        batch_b_delete_all(),
        batch_c_delete_one(),
        batch_d_correction(),
        batch_e_vague(),
        batch_f_ellipsis(),
        batch_g_typos(),
        batch_h_kb(),
        batch_i_real_machine_gaps(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 口语样例（去重/全删/单条/纠正/模糊/省略/错别字 + 知识库）总账："
          f"通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
