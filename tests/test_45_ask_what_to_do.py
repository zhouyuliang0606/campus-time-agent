"""「今晚安排点啥」这族：他在问『该做什么』，不是报一件要做的事 —— 不许建卡，但要给一句实在的建议。

用户原话（2026-10-14，我给的三个走法里他挑了第三个）：
    「**今晚安排点啥**」这种是问事情闲聊的，不入库
    → 定案：**不建卡，但给一句实在的建议**（读周表空档告诉他哪几段闲着，
      不替他决定做什么，也不往日程里写一条）。

修之前的三处漏（全部实测，现场消息见 B/C 批）：
  ① 事名判真放行了**疑问代词残渣**：`looks_like_thing("点啥")` 为真 →
     规则层直接摊候选卡，卡片标题印着「**点啥**」「**点什么**」「**啥比较好**」「**说我啥**」。
  ② 无密钥兜底那一档更糙：`_guess_title(msg)` 拿**整句话**当任务名，
     「干点啥好呢」「今天做点什么好」「我今晚该干点啥呢」「这周干啥」各自摊出 3 张候选，
     标题就是原话本身（学生早就驳回过这种写法：「不是随便拿那一句话就去当代办加入日程了」）。
  ③ 疑问尾词没擦：「今晚去健身怎么样」这句**确实是下单**，可卡上事名成了「健身怎么样」。

判据只留一份（指南红线 10）：疑问代词表 `_WH_ASK_WORDS` 定义在一处，
`_asking_what_to_do()`（这句是不是在问『做什么』）与 `looks_like_thing()`（这串能不能当事名）
都从它取 —— 两处各写一遍就是下一个「改一处漏三处」。
"""
import inspect
import re

from _harness import (SEED_COURSES, Checker, make_client, sandbox, title,
                      wipe_todos, write_timetable)

code = 0

# 在问『做什么』的说法（用户举的那句 + 同族正常说法，一句都不许建卡）
ASKING = [
    "今晚安排点啥", "明天安排点啥", "这周安排点啥呢", "晚上安排点什么",
    "安排点什么呢", "干点啥好呢", "今天做点什么好", "我今晚该干点啥呢",
    "你说我安排啥", "安排啥比较好", "今晚安排什么好", "这周干啥",
]

# 追问族：**没有疑问代词**、只是"说了要加、没说加什么"（含模糊量词「点事」）——
# 这一族该继续回「要安排的是什么事？」，不许被这次的判据一起改掉（那是另一档）。
FOLLOWUP = ["帮我安排点事", "安排点事给我", "你帮我安排", "这周安排一个时间"]

# 真正在下单的句子：判据改完之后**一张卡都不能少**（误伤比漏判更贵）
ORDERING = [
    ("帮我安排游泳", "游泳"),
    ("今晚去健身怎么样", "健身"),          # ← ③：尾词「怎么样」要擦掉，事名是「健身」
    ("安排一下写周报", "写周报"),
    ("记一下取快递", "取快递"),
    ("今晚安排去看电影", "看电影"),
    ("明天上午安排两小时复习", "复习"),
    ("周六下午三点爬山", "爬山"),
]


def batch_a_judge():
    """A 批 · 判据：哪些是问『做什么』，哪些是真下单；事名否决与尾词擦除。"""
    c = Checker()
    title("A. 判据：疑问代词不算事名、问『做什么』不判成下单")
    from app.modules import planner as P

    # ① 这族句子：事名必须抠成「待办」（=没说清），不许是「点啥」这类残渣
    for s in ASKING:
        c.check(f"「{s}」抠不出事名（判成没说清）",
                P.todo_title_of(s) == "待办", repr(P.todo_title_of(s)))
        c.check(f"「{s}」认出是在问『做什么』",
                P._asking_what_to_do(s) is True, "")
        # 驱动条件：加待办那扇门必须为它关上（`_ADD_INTENT` 里有"安排"两个字，
        # 不在 `wants_add_todo` 里先让路，3c 会抢在这档前面把候选卡挂出来）
        c.check(f"「{s}」不再判成加待办（3c 那道门必须关上）",
                P.wants_add_todo(s) is False, "")

    # ② 疑问尾词走**擦除**不是否决：句子确实是下单，只是名字脏
    c.check("「今晚去健身怎么样」事名擦成「健身」（不是「健身怎么样」，也不是「待办」）",
            P.todo_title_of("今晚去健身怎么样") == "健身",
            repr(P.todo_title_of("今晚去健身怎么样")))
    c.check("「这个点安排跑步咋样」擦成「跑步」",
            P.todo_title_of("这个点安排跑步咋样") == "跑步",
            repr(P.todo_title_of("这个点安排跑步咋样")))

    # ③ 不许误伤：真下单的句子判据必须放行
    for s, want in ORDERING:
        c.check(f"「{s}」照旧是下单，事名「{want}」",
                P.wants_add_todo(s) and P.todo_title_of(s) == want,
                repr(P.todo_title_of(s)))
        c.check(f"「{s}」不算在问『做什么』", P._asking_what_to_do(s) is False, "")

    # ④ 边界：「什么」在**中间**（书名/课名）不许当成问『做什么』。
    #     这条是判据写成"剥完裸串的首或尾"而不是"含疑问代词就否 / 抠不出事名就算"的原因：
    #     - 含就否会把《什么是爱》这类真名字挡掉（同「写好/排好」那条教训）；
    #     - 拿"抠不出事名"当判据会把「安排看《什么是时间管理》」误判成问事
    #       （它抠不出事名是**「时间」这个噪声词**一直挡着，不是学生在问做什么）。
    c.check("「安排看《什么是爱》」不许被疑问判据挡掉（『什么』在中间）",
            P._asking_what_to_do("安排看《什么是爱》") is False
            and "什么" in P.todo_title_of("安排看《什么是爱》"),
            repr(P.todo_title_of("安排看《什么是爱》")))
    c.check("「安排看《什么是时间管理》」也不算问『做什么』（抠不出事名另有原因）",
            P._asking_what_to_do("安排看《什么是时间管理》") is False,
            repr(P.todo_title_of("安排看《什么是时间管理》")))

    # ⑤ 追问族：没疑问代词的半句话**照旧走追问**，不许被这次的判据拽进建议档
    for s in FOLLOWUP:
        c.check(f"「{s}」不算问『做什么』（走原追问档）",
                P._asking_what_to_do(s) is False, "")
        c.check(f"「{s}」抠不出事名（「点事」这类模糊量词渣不许当名字）",
                P.todo_title_of(s) == "待办", repr(P.todo_title_of(s)))

    # ⑥ 两道排除（2026-10-14 全量跑出来的真冲突，不是假想）：
    #   · 在删东西的「把那个啥删了」也带疑问代词 —— 被这档截走，就成了
    #     "当着要删东西的人报空档"（test_15 那条红咬回来的）；
    #   · 「我现在都有啥安排」问的是**已经有什么**（查清单），不是"该做什么"
    #     （test_08 ⑥ 的复核前提也得留着）。
    c.check("「把那个啥删了」不算问『做什么』（他在删东西，不是让你出主意）",
            P._asking_what_to_do("把那个啥删了") is False
            and P._has_delete_intent("把那个啥删了") is True,
            repr(P.todo_title_of("把那个啥删了")))
    c.check("「我现在都有啥安排」不算问『做什么』（查现有清单另有档）",
            P._asking_what_to_do("我现在都有啥安排") is False
            and P._asking_what_to_do("有啥安排") is False, "")
    c.check("「安排点啥」这种没有「有」字的照常算",
            P._asking_what_to_do("安排点啥") is True, "")
    src = inspect.getsource(P)
    c.check("删意图那道判据只有一份（`wants_add_todo` 与这族共用 `_has_delete_intent`）",
            src.count("_has_delete_intent(t)") >= 2
            and src.count("or any(w in t for w in _REMOVE_TODO_INTENT)") == 1,
            f"调用 {src.count('_has_delete_intent(t)')} 处、内联抄本 "
            f"{src.count('or any(w in t for w in _REMOVE_TODO_INTENT)')} 处")

    # ⑦ 形状：疑问代词表只有一份，两个判据都从它取（不许各写一遍）
    c.check("疑问代词表只定义一处（`_WH_ASK_WORDS = `）",
            src.count("_WH_ASK_WORDS = ") == 1,
            f"{src.count('_WH_ASK_WORDS = ')} 处")
    _thing_body = src.split("def looks_like_thing")[1].split("def ")[0]
    _ask_body = src.split("def _asking_what_to_do")[1].split("def ")[0]
    _name_body = src.split("def _vague_ask_name")[1].split("def ")[0]
    c.check("两处判据都走同一个 `_vague_ask_name`（否决表没被抄第二份）",
            "_vague_ask_name" in _thing_body and "_vague_ask_name" in _ask_body,
            f"looks_like_thing={('_vague_ask_name' in _thing_body)} "
            f"_asking_what_to_do={('_vague_ask_name' in _ask_body)}")
    c.check("`_vague_ask_name` 取的就是那份表", "_WH_ASK_WORDS" in _name_body, "")
    return c


def batch_b_no_card():
    """B 批 · 端到端：这族句子一张卡都不许挂、一条都不许写，但得给出实在的空档建议。"""
    c = Checker()
    title("B. 走接口：不建卡、不写库，但回话里有真空档")
    from app.store import list_todos

    with sandbox():
        # ★ 起始状态自种（指南红线 18 定案）：这档回话的**内容**就是周表空档，
        #   不种就等于拿"你周四有多忙"当断言前提，本机与干净克隆会给出不同答案。
        write_timetable(SEED_COURSES)
        wipe_todos()
        cli = make_client()
        for i, s in enumerate(ASKING):
            d = cli.post("/api/chat", json={"message": s, "module": "planner",
                                            "session_id": "t45-ask-%d" % i}).json()
            opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
            ans = d.get("answer") or ""
            c.check(f"「{s}」不挂任何卡", not opts,
                    str([(o.get("kind"), o.get("title")) for o in opts]))
            c.check(f"「{s}」回话里给了具体钟点（实在的建议，不是空话）",
                    bool(re.search(r"\d{1,2}:\d{2}", ans)), ans.replace("\n", " ")[:70])
            c.check(f"「{s}」说清了没替他建卡",
                    any(w in ans for w in ("没替你", "没往日程", "不替你", "你说个名字")),
                    ans.replace("\n", " ")[:70])
        c.check("全程一条都没写进日程（库里还是自种的那 0 条）",
                len(list_todos()) == 0, f"{len(list_todos())} 条")

        # 两句排除档端到端也得站对边：在删东西的、在查清单的，都不许被建议档接走。
        # （「我现在都有啥安排」最后落到哪一档是另一件事：沙箱把样本库清了、
        #   `wants_list_todo` 又不认这句，于是落到追问档 —— 那属于"查清单判据覆盖不全"
        #   的老洞，文件末尾列了同族四条，等拍板。）
        for s, want in (("把那个啥删了", ("删",)),
                        ("我现在都有啥安排", ("安排",))):
            d = cli.post("/api/chat", json={"message": s, "module": "planner",
                                            "session_id": "t45-except"}).json()
            ans = d.get("answer") or ""
            opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
            c.check(f"「{s}」没被建议档截走（回话里不许出现「该做什么」那套）",
                    "该做什么" not in ans and not opts,
                    ans.replace("\n", " ")[:64])
            c.check(f"「{s}」回到它本来该去的档（话里出现 {want}）",
                    any(w in ans for w in want), ans.replace("\n", " ")[:64])

        # 真机复验查出的一条顺序问题（2026-10-14，用户那台机器）：样本库早就把
        # 「今晚安排点啥」学成了 list_todo，判定层第一档就命中它 → 学生看到的是
        # 「📋 你目前的待办（共 11 条）」。判据再准，**位置排错就白搭**。
        # 这里把同一条样本灌进沙箱再问一次，钉住"建议档排在列表档之前"这条驱动条件。
        from app.agent.intent import remember
        remember("今晚安排点啥", "list_todo", {})
        d = cli.post("/api/chat", json={"message": "今晚安排点啥", "module": "planner",
                                        "session_id": "t45-poison"}).json()
        ans = d.get("answer") or ""
        c.check("样本库把它学成 list_todo，也照样走建议档（不是一句念清单）",
                "该做什么" in ans and not (d.get("options") or []),
                ans.replace("\n", " ")[:70])
        c.check("灌了样本也没写库", len(list_todos()) == 0, f"{len(list_todos())} 条")

        # 追问族：同样不建卡，但答的是「要安排的是什么事？」（原有那一档，不是建议档）
        for i, s in enumerate(FOLLOWUP):
            d = cli.post("/api/chat", json={"message": s, "module": "planner",
                                            "session_id": "t45-followup-%d" % i}).json()
            opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
            ans = d.get("answer") or ""
            c.check(f"「{s}」不挂卡、回的是追问「要安排的是什么事」",
                    not opts and "要安排的是什么事" in ans,
                    ans.replace("\n", " ")[:60])

        # 同一条判据不许把下单族一起否掉：这些句子**必须照旧出卡**
        for i, (s, want) in enumerate(ORDERING):
            d = cli.post("/api/chat", json={"message": s, "module": "planner",
                                            "session_id": "t45-do-%d" % i}).json()
            opts = [o for o in (d.get("options") or []) if isinstance(o, dict)]
            c.check(f"「{s}」照旧出卡，卡上是「{want}」",
                    bool(opts) and any(o.get("title") == want for o in opts),
                    str([(o.get("kind"), o.get("title")) for o in opts]))
        c.check("下单族这一轮写库条数仍然为 0（只出卡、没确认）",
                len(list_todos()) == 0, f"{len(list_todos())} 条")
    return c


def batch_c_shape():
    """C 批 · 形状：这档的位置与出口（位置是真机实测出来的驱动条件）。"""
    c = Checker()
    title("C. 代码形状：排在列表档与 3c 之前，且它自己不出卡、不学样本")
    main_src = open("app/main.py", encoding="utf-8").read()
    i_ask = main_src.find("_asking_what_to_do(message)")
    i_list = main_src.find("_sem_intent == INTENT_LIST_TODO")
    i_add = main_src.find("wants_add_todo(message) or (asking_add")
    c.check("这档排在**列表档之前**",
            0 <= i_ask < i_list, f"建议档在 {i_ask}，列表档在 {i_list}")
    c.check("也排在 3c 加待办那道闸门之前（带「安排」两个字，排后面永远轮不到）",
            0 <= i_ask < i_add, f"建议档在 {i_ask}，3c 在 {i_add}")
    seg = main_src[i_ask:i_ask + 1200]
    c.check("这档的返回里 options 是空的（挂卡就等于把建议变成了替学生决定）",
            '"options": []' in seg, "")
    c.check("这档**不走 `_sem_done`**（`remember()` 就在里面，一句问话不许学成模板）",
            "_sem_done" not in seg, "")
    return c


# ── 同族但**没**在本文件里修的四条（2026-10-14 实测，等拍板）────────────────
# 它们是"问已经有什么"该走列表档的那一族，判据在 `wants_list_todo` 那边，
# 风险面比本次这族大，所以单列在这里，别说测试没提过：
#   「看看我的安排」      → 挂 todo_slots 卡，标题「看看我」
#   「我今天的安排满不满」 → 挂 todo_slots 卡，标题「我的满不满」
#   「我今天都有什么事」   → 兜底档三张卡，标题就是整句话
#   「有啥安排」         → 列表档（这条是对的，作对照记在这儿）


def main():
    global code
    print("\n" + "=" * 60)
    print("test_45_ask_what_to_do：问『做什么』不建卡")
    print("=" * 60)
    code |= batch_a_judge().summary("A 批（判据）")
    code |= batch_b_no_card().summary("B 批（端到端不建卡）")
    code |= batch_c_shape().summary("C 批（代码形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
