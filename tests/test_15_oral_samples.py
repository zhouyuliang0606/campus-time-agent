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
import datetime as _dt
import io

from _harness import Checker, make_client, sandbox, title

_D = "2026-10-06"
# K 批要的是"**明天**去吃自助"那种还没落库的安排，日子必须往后取：
# 落在过去的日子会被 `store.is_past_date` 那道闸正当拒掉（N 批钉的），测试就测不到路过了。
_D2 = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
MAIN_FILE = "app/main.py"


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


def batch_j_change_mind():
    """J. **反悔刚提的那件事**：「算了不吃了」不许掉给模型当闲聊（2026-10-12 用户指令）。

    场景是他自己走出来的：先提「吃自助餐」，紧接着来一句「算了不吃了」——
    这句字面上一个"删"字都没有，而软词表里当时只有「不去了/不要了/不搞了」，
    于是 `wants_remove_todo` 不认、`wants_add_todo` 也不认（没有加字），
    三档判定一路落空 → 模型当闲聊回一句"没关系，那就不吃了吧"，
    那条待办**还留在日程上**。学生心里的账是"我撤掉了"，屏幕上的账是"还挂着"。

    补的是 `_REMOVE_TODO_SOFT` 两颗词（不吃了 / 不做了），沿用同一套让路判定，
    **没有**给语义层加提示（项目纪律：规则接得住就别问模型）。
    ⚠️ 这张表同时是 `wants_add_todo` 开头的让路开关，加一颗词的代价写在 ④。
    """
    c = Checker()
    title("J. 反悔刚提的事：「算了不吃了」= 删待办（不许回一句闲聊）")
    with sandbox():
        client = make_client()
        from app.modules.planner import wants_add_todo, wants_remove_todo
        from app.store import add_todo, delete_todo, list_todos

        for t in list_todos():
            delete_todo(t["id"])
        add_todo("吃自助餐", _D, "12:00", "13:00")

        # ① 判定层：这句一个"删"字都没有，也必须认出是删待办（规则档，不问模型）
        #    后三颗是用户浏览器里的**原话**（`sfacc53a8c8` 那段会话），不是编的：
        #    「不吃了，删掉吧」当时回的是「删待办：没找到符合条件的」——
        #    因为「删掉吧」认得、可抠出来的名字是残渣；一句报障话被回成"没这条"。
        for s in ("算了不吃了", "不吃了", "不做了", "算了不做了", "算了，不吃了",
                  "算了不去了", "不要了", "不去了",
                  "不吃了，删掉吧", "算了不吃了，删掉吧", "吃自助餐不吃了", "那个不吃了"):
            c.check("判定层认成删待办｜%s" % s, wants_remove_todo(s) is True,
                    wants_remove_todo(s))
        # 「规则接得住就别问模型」得有个可查的说法：`_rule_intent` 是"要不要升级到语义层"
        # 那道预检，它认出 remove_todo，这句就**一次 API 都不花**。
        from app.main import _rule_intent
        from app.agent.intent import INTENT_REMOVE_TODO
        for s in ("算了不吃了", "不吃了", "不做了"):
            c.check("规则档就接住（不升级到语义层）｜%s" % s,
                    _rule_intent(s) == INTENT_REMOVE_TODO, _rule_intent(s))
        # 光「算了」历史刻意不收（多半是"算了不说这个了"），本轮也不收
        for s in ("算了", "算了不说这个了", "算了随便吧"):
            c.check("光「算了」不许被拽进删除｜%s" % s, wants_remove_todo(s) is False,
                    wants_remove_todo(s))

        # ② 端到端：库里只有一条 → 原样念一遍出确认条，不是"我没办成什么"
        d = _say(client, "oral-j1", "算了不吃了")
        o = (d.get("options") or [{}])[0]
        c.check("「算了不吃了」→ 删待办确认条，念的就是刚提的那条《吃自助餐》",
                _kinds(d) == ["todo_remove"] and o.get("title") == "吃自助餐",
                f"{_kinds(d)} | {o.get('title')}")
        c.check("回话里不许出现「我没办成 / 没找到」那种白话",
                "没找到" not in (d.get("answer") or ""),
                (d.get("answer") or "").replace("\n", " ")[:60])
        c.check("学生还没点头 → 一条都没删", len(list_todos()) == 1,
                f"{len(list_todos())}")

        # ②b 他那句原话「不吃了，删掉吧」：当时"删掉吧"认得、名字却是残渣 →
        #     回「删待办：没找到符合条件的」。现在必须同样落到《吃自助餐》那条确认条。
        d1b = _say(client, "oral-j1b", "不吃了，删掉吧")
        c.check("「不吃了，删掉吧」→ 也是那条确认条（不许再回「没找到符合条件的」）",
                _kinds(d1b) == ["todo_remove"]
                and (d1b.get("options") or [{}])[0].get("title") == "吃自助餐"
                and "没找到" not in (d1b.get("answer") or ""),
                f"{_kinds(d1b)} | {(d1b.get('answer') or '')[:44]}")

        # ③ 库里两条时**不猜**：列清单问是哪一条
        add_todo("跑步", _D, "19:00", "20:00")
        d3 = _say(client, "oral-j3", "算了不吃了")
        c.check("库里两条 → 列清单问是哪一条（不替他挑）",
                not _kinds(d3) and ("哪一条" in (d3.get("answer") or "")
                                    or "不敢替你挑" in (d3.get("answer") or "")),
                f"{_kinds(d3)} | {(d3.get('answer') or '')[:50]}")
        c.check("列完清单仍然一条都没删", len(list_todos()) == 2, f"{len(list_todos())}")

        # ④ 这张表的代价（照实钉住，不是"应该为真"）：软词是 `wants_add_todo` 的让路开关，
        #    所以"一句里两件事"会被挡掉新增那一件 —— 「这周不吃了，帮我加个体检复查」。
        #    「不去了」早就是这个形状，本轮没让它变大，但把它写成数字，别没人知道。
        d4 = _say(client, "oral-j4", "这周不吃了，帮我加个体检复查")
        c.check("已知取舍｜一句里同时说「不吃了 + 帮我加X」时，新增那件被让路挡掉",
                wants_add_todo("这周不吃了，帮我加个体检复查") is False
                and "体检复查" not in [t["title"] for t in list_todos()],
                f"add={wants_add_todo('这周不吃了，帮我加个体检复查')} "
                f" titles={[t['title'] for t in list_todos()]} kinds={_kinds(d4)}")

        # ⑤ 形状：新词必须真的进了那张表（三处引用它的分支自动生效，别再各自加词）
        from app.modules import planner as _P
        c.check("形状：两颗新词在表里，而且只有这一份表",
                "不吃了" in _P._REMOVE_TODO_SOFT and "不做了" in _P._REMOVE_TODO_SOFT
                and len(_P._REMOVE_TODO_SOFT) == 9,
                _P._REMOVE_TODO_SOFT)
        c.check("形状：死条目不留——「算了不吃了」不该单独进表（它是「不吃了」的超串）",
                "算了不吃了" not in _P._REMOVE_TODO_SOFT, _P._REMOVE_TODO_SOFT)
    return c


def batch_k_cancel_held_proposal():
    """K. 「算了不吃了」对着**还没点头的那张卡**：作废提案，别去日程里翻一条来删。

    用户施工单里的截图那一幕：先说「明天去吃自助」→ 系统出候选时段卡《吃自助》
    （库里**还没有这条**），他接着说「算了不吃了」→ 系统把这句听成"删一条待办"，
    端出整张日程问「是下面哪一条」。要撤的是那张刚挂出来的卡，不是日程里的东西。

    ⚠️ 这一批同时钉住施工单里那个**照抄会失效**的条件：它要求 `_is_blind_delete(message)` 为真，
    可 `main._DELETE_ACTION_WORDS` 是第四份手抄的撤销词表，里面没有「不吃/不做/不要」——
    「算了不吃了」当场判 False，那道拦截对截图里那句**一辈子不生效**（死分支）。
    现在那份词表从 `planner._REMOVE_TODO_SOFT` 推，⑦ 组把这件事钉住。
    """
    c = Checker()
    title("K. 反悔对着还没点头的卡：作废提案、不写库也不删库")
    with sandbox():
        client = make_client()
        from app.agent.pending import peek_pending
        from app.modules import planner as _P
        from app.store import add_todo, delete_todo, list_todos

        import app.main as M

        def seed():
            for t in list_todos():
                delete_todo(t["id"])
            add_todo("图书馆", _D2, "14:00", "15:00")
            add_todo("跑步", _D2, "19:00", "20:00")
            add_todo("健身", _D2, "09:00", "10:00")

        def hold_card(sid):
            """挂出一张《吃自助》候选时段卡，返回第一张卡的 kind/title。"""
            d = _say(client, sid, "明天去吃自助")
            o = (d.get("options") or [{}])[0]
            return o.get("kind"), o.get("title"), d

        # ① 五种软取消说法，卡挂着 → 都作废这张卡，库里一个字不动
        for n, msg in enumerate(("算了不吃了", "不吃了", "不要了", "算了", "取消")):
            sid = f"oral-k1-{n}"
            seed()
            kind, ctitle, _ = hold_card(sid)
            base = sorted(t["id"] for t in list_todos())
            c.check("前提：挂出来的确实是候选时段卡《吃自助》｜%s" % msg,
                    kind == "todo_slots" and ctitle == "吃自助", f"{kind}/{ctitle}")
            d = _say(client, sid, msg)
            o = (d.get("options") or [{}])[0]
            c.check("软取消作废那张卡｜%s" % msg,
                    not _kinds(d) and "先不加了" in (d.get("answer") or ""),
                    f"{_kinds(d)} | {(d.get('answer') or '')[:40]}")
            c.check("念的是卡上的事名《吃自助》，不是日程里的某条｜%s" % msg,
                    "吃自助" in (d.get("answer") or ""), (d.get("answer") or "")[:44])
            c.check("不写库也不删库｜%s" % msg,
                    sorted(t["id"] for t in list_todos()) == base,
                    f"{len(list_todos())} 条")
            c.check("暂存确实被清了（下次说一句新的不会被旧卡顶掉）｜%s" % msg,
                    peek_pending(sid) is None, peek_pending(sid))

        # ①b 另外三种"还没点头"的卡：二选一（todo_mode）/ 确认条（todo_add）/ 单条已定（todo_pick）。
        #     这里用 `save_pending` 直接挂卡：真机上这几张卡各自要什么话术才出、不是本轮决定的，
        #     要验的是**这一支把 kinds 认全了** —— 少认一种，那种卡上的反悔就漏进删除流程
        #     （施工单 §四.2 点名的正是这一条：摘掉 todo_mode 应该红）。
        from app.agent.pending import save_pending
        from app.modules.planner import todo_mode_proposal

        hand_made = [
            todo_mode_proposal("帮我加个游泳，大概一个小时"),
            {"kind": "todo_add", "title": "看电影", "date": _D2, "weekday": "周二",
             "start": "19:00", "end": "20:30", "minutes": 90,
             "summary": "看电影｜%s（周二）19:00-20:30" % _D2},
            {"kind": "todo_pick", "title": "看书", "date": _D2, "start": "20:00",
             "end": "21:00", "minutes": 60, "summary": "看书｜%s 20:00-21:00" % _D2},
        ]
        for n, card in enumerate(hand_made):
            if not isinstance(card, dict):
                c.check("前提：%s 那张卡造得出来" % (n + 1), False, card)
                continue
            sid = f"oral-k1b-{n}"
            seed()
            save_pending(sid, [card])
            base = sorted(t["id"] for t in list_todos())
            d = _say(client, sid, "不要了")
            c.check("反悔认全这种卡｜%s" % card.get("kind"),
                    not _kinds(d) and "先不加了" in (d.get("answer") or "")
                    and peek_pending(sid) is None
                    and sorted(t["id"] for t in list_todos()) == base,
                    f"{_kinds(d)} | {(d.get('answer') or '')[:40]}")

        # ② 明说「删」/ 点了另一件事的名字 → 不许作废那张卡（还是走删除流程）
        for n, (msg, why) in enumerate((
                ("把昨天那个删了", "明说删"),
                ("我要取消健身", "点名的是《健身》，不是卡上的《吃自助》"),
                ("不吃了，删掉吧", "说了删 → 交回删除流程（本轮取舍写在 J 批）"),
        )):
            sid = f"oral-k2-{n}"
            seed()
            hold_card(sid)
            d = _say(client, sid, msg)
            c.check("这句不该作废卡｜%s（%s）" % (msg, why),
                    "先不加了" not in (d.get("answer") or ""),
                    (d.get("answer") or "").replace("\n", " ")[:46])

        # ③ 「确认」是点头，不是反悔 —— 五颗软词里挑一颗最像的当反面都不许误伤
        sid = "oral-k3"
        seed()
        hold_card(sid)
        d = _say(client, sid, "确认")
        c.check("「确认」不许被听成反悔（点头就是点头）",
                "先不加了" not in (d.get("answer") or ""),
                (d.get("answer") or "").replace("\n", " ")[:46])
        # ④ 「嗯」「好的」这种短应答不能把卡撤了
        for n, msg in enumerate(("嗯", "好的")):
            sid = f"oral-k4-{n}"
            seed()
            hold_card(sid)
            d = _say(client, sid, msg)
            c.check("短应答｜%s」不该作废那张卡" % msg,
                    "先不加了" not in (d.get("answer") or ""),
                    (d.get("answer") or "").replace("\n", " ")[:40])

        # ⑤ **没有卡**挂着时说「算了不吃了」→ 照旧走删除流程（这一轮没改它）
        sid = "oral-k5"
        seed()
        d = _say(client, sid, "算了不吃了")
        c.check("没挂卡时行为不变（仍走删待办那条路，列清单不猜）",
                "先不加了" not in (d.get("answer") or "")
                and ("哪一条" in (d.get("answer") or "")
                     or "不敢替你挑" in (d.get("answer") or "")),
                (d.get("answer") or "").replace("\n", " ")[:46])

        # ⑥ 已经**写进库**的那条：回执路不能被这条抢走（test_08 ⑦ 同一个形状）
        sid = "oral-k6"
        seed()
        _say(client, sid, "加个明天19:00到20:00游泳")
        _say(client, sid, "确认")
        wrote = [t for t in list_todos() if t["title"] == "游泳"]
        c.check("前提：那条《游泳》确实写进库了", len(wrote) == 1, f"{len(wrote)}")
        d = _say(client, sid, "不去了删了吧")
        c.check("没点头的卡已经不在了 → 回执路接手，定位到刚写入的《游泳》",
                _kinds(d) == ["todo_remove"]
                and (d.get("options") or [{}])[0].get("title") == "游泳",
                f"{_kinds(d)} | {(d.get('options') or [{}])[0].get('title')}")

        # ⑦ 形状：标题形动作词从软词表**推**出来，不再手抄第四份
        stems = tuple(w[:-1] if w.endswith("了") else w for w in _P._REMOVE_TODO_SOFT)
        missing = [w for w in stems if w not in M._DELETE_ACTION_WORDS]
        c.check("形状：每颗软词的标题形都在 `_DELETE_ACTION_WORDS` 里（漂移过一次就再发一次）",
                not missing, f"缺 {missing}")
        c.check("形状：`_is_blind_delete` 与新判据共用同一份残渣计算（不抄第二份）",
                "_delete_residue" in M._is_blind_delete.__code__.co_names
                and "_delete_residue" in M._cancels_active_proposal.__code__.co_names,
                M._is_blind_delete.__code__.co_names)

        # ⑧ 形状：作废那一段必须排在删待办分支入口**之前**（排在后面永远进不来）
        src = io.open(MAIN_FILE, encoding="utf-8").read()
        i_cancel = src.find("_active_add = _active_add_proposal(session_id)")
        i_rm = src.find("if rm_intent or asking_rm_todo or answers_held:")
        c.check("形状：作废提案那段在删待办分支入口之前", 0 < i_cancel < i_rm,
                (i_cancel, i_rm))
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
        batch_j_change_mind(),
        batch_k_cancel_held_proposal(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 口语样例（去重/全删/单条/纠正/模糊/省略/错别字 + 知识库）总账："
          f"通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
