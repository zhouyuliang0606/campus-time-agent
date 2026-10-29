# 86 · 给引擎一只手：`propose_todo_change(op=retime, auto_slot)`

> 施工单原件。基准 **HEAD 待查**（#84 已施工）。
> 优先级 **P0** —— 排在 #85 之前，也是这一批里**唯一"加了就能办成事"**的一张。
> 学生原话：「引擎手上没有能改待办的工具，所以它查完只能反问"你说的是哪一条"。加工具吧，应该不麻烦，我已经有课表的修改工具」。
>
> **本单复活 #78 的设计，但改一处关键：加 `auto_slot`。** 没有它，引擎拿到工具之后仍然会反问"你想改到几点"。

---

## 〇 现场事实

### 0.1 引擎的能力矩阵：只缺一格

把 `build_tools()`（`main.py:2516` 挂给 planner 模块）+ `build_scheduling_tools()`
（`planner.py:6532`，`main.py:2576` 再补一轮）合起来，引擎实际拿到 13 个工具：

| 动作 | 引擎有吗 | 工具名 |
|---|---|---|
| 查课表 / 空档 / 待办 | ✅ | `get_weekly_timetable` / `find_free_slots` / `list_day_todos` / `list_todos_range` |
| **加**待办（单条/多段/模式） | ✅ | `propose_todo_tool` / `propose_todo_slots_tool` / `propose_todo_mode_tool` / `propose_slots` |
| **删**待办 | ✅ | `propose_todo_remove` |
| 标记完成 | ✅ | `propose_todo_done` |
| **改**课表（增/删/改） | ✅ | `propose_course_change(op=add\|remove\|update)` |
| 导入 / 清空课表 | ✅ | `propose_timetable_change` / `propose_clear_timetable` |
| **改待办时间** | ❌ **一格空着** | 无 |

**学生说"我已经有课表的修改工具"，就是 `propose_course_change`。**
课表侧是**一个 `op` 式工具管三件事**；待办侧是一排平行工具，**唯独没有"改"**。

### 0.2 所以 #84 之后它"只能反问"，不是它不想办

#84 把改期办不成的句子交给了引擎。引擎拿到的处境是：

```
查 list_day_todos      → 6 条，能看见「游泳 15:00-16:00」
想改              → 手上没有改期工具
⇒ 只能说："你说的是哪一条？你想改到几点？"
```

**它查到了、也算得出撞了谁，但手里没有那只能改的手。**
那 4 句「查出冲突并指名」正好是这个形状 —— **不是能力不够，是缺一件工具。**

### 0.3 78 号单当时砍掉，现在前提变了

当时砍的三条理由：

| 当时的理由 | 现在 |
|---|---|
| 分镜没有改期场景，演示不经过 | **#84 让改期进了演示路径** —— 学生随口说一句"撞时间了"就到这儿 |
| 要动三处，是能把 5273 全绿打回红的改动 | 这几天 #80/#81/#82/#84 连续施工，那条流程已经跑顺，回归是可靠的安全网 |
| 76/77 还没真机验收，再叠改动无法归因 | 验收做过，基线清楚 |

**三条全变了。** 这不是"当时判错"，是**前提被 #84 改了**——写快照时按这个说，别写成"早就该做"。

### 0.4 一件必须同时改的东西（78 号停工时量到的缺口）

`planner.py:4105 _TOOL_ECHO_RE` 列了 6 个工具名，用来认"模型把工具调用打成字"。
新工具**不加进去**，模型万一把它打成文本，那段 JSON 会直接端到学生面前（红线 8）。

---

## 一 修法

### 1.1 `planner.py`：先给 `build_retime_existing_proposal` 加一个开关

**为什么不复用它的现成行为**：#81 之后，"没给新时间"会返回 `unclear/no_time`。
确定性分支要这个（追问是对的，快且稳），但**引擎要的是"你替我挑"**。

同一份函数，两种策略 —— 用一个参数分开：

```python
def build_retime_existing_proposal(text: str, sem: dict | None = None,
                                   hint_title: str = "", auto_slot: bool = False) -> dict:
```

在 `begin` 那一档（锚点：`# 新时刻：语义层给的 > 话里抠出来的 > 换了个日子就挑空档 > 否则沿用旧时刻`），
**`auto_slot=True` 时**：没报钟点 + 日子没换 ⇒ 不要沿用旧时刻（那就是 #81 的空改动），
改成 `pick_free_slot(old_date, not_before=...)` 挑一段；挑不出来就往后再试一天，最多 3 天。

**默认 `False` = 现有行为逐字不变**（这是本单最关键的一条：`main.py:3727` 那条路一个字都不改）。

### 1.2 新工具（纯函数，**不需要 session_id**）

```python
def propose_todo_change_tool(op: str = "retime", title: str = "",
                             new_date: str = "", new_start: str = "",
                             new_end: str = "") -> str:
    """改一条**已经在日程里**的待办的时间（只出提案，不写库）。

    ⚠️ 它**不新建**。要加新的请用 propose_todo_tool / propose_todo_slots_tool。
    """
    if (op or "").strip() != "retime":
        return json.dumps({"ok": False,
                           "error": f"op={op} 还没做，目前只支持 retime"}, ensure_ascii=False)
    hint = (title or "").strip()
    # 把结构化参数拼回一句它认得的自然语言（这个函数本来就是按"学生原话"写的）
    parts = []
    if hint:
        parts.append(hint)
    if (new_date or "").strip():
        parts.append("挪到" + new_date.strip())
    if (new_start or "").strip():
        parts.append(new_start.strip() + ("-" + new_end.strip() if (new_end or "").strip() else ""))
    if not parts:
        return json.dumps({"ok": False, "error": "至少要给 title（改哪一条）"}, ensure_ascii=False)

    # ★ 必须把 title 走 `sem` 递进去，**不能**指望它从拼出来的这句话里再抠一次
    #   （原因见 §1.2-bis：`_pick_todo_name` 在「名字 + ISO 日期 + 时段」这种串上会抠出残渣）
    sem = {"title": hint} if hint else None
    res = build_retime_existing_proposal(" ".join(parts), sem, hint_title=hint,
                                         auto_slot=True)
    st = res.get("status")
    if st == "ok":
        return json.dumps({"__proposal__": res["proposal"]}, ensure_ascii=False)
    if st == "many":
        # 命中多条：**照清单原样列给他、问"是下面哪一条"，然后停**（同 propose_todo_done 的老规矩）
        return json.dumps({"ok": False, "need_pick": True,
                           "todos": [{"title": x.get("title"), "date": x.get("date"),
                                      "start": x.get("start"), "end": x.get("end")}
                                     for x in (res.get("todos") or [])][:6]},
                          ensure_ascii=False)
    if st == "empty":
        # 库里有东西只是没对上名字 → 说实话，别让他以为库里没有（#83 的口径）
        return json.dumps({"ok": False, "not_found": True,
                           "pool_n": res.get("pool_n", 0)}, ensure_ascii=False)
    # unclear / no_slot
    return json.dumps({"ok": False, "no_slot": True,
                       "reason": res.get("reason") or ""}, ensure_ascii=False)
```

### 1.2-bis 为什么 `title` 走 `sem`（**实测后的结论：是保险，不是判据**）

`build_retime_existing_proposal` 里名字是这么来的（`planner.py:5177-5185`）：

```python
name = (sem or {}).get("title") or ""
if name:
    if not looks_like_thing(_pick_title(name)):   # 模型给的名字不像话 → 才退回抠
        name = _pick_todo_name(t_clean)
else:
    name = _pick_todo_name(t_clean)               # ← sem 给 None 就落到这一支
```

工具拼出来的串是 `"<title> 挪到 <ISO日期> <起>-<止>"`，`sem=None` 时会落到 `else` 那支，
交给 `_pick_todo_name` —— 那是按**学生原话**写的函数，把我们自己拼的串喂进去是不是安全，
**不能靠猜。实测（2026-10-29，进程内直接调）：**

| 喂进去的 `t_clean` | `_pick_todo_name` | 对得上库里的「游泳」吗 |
|---|---|---|
| `游泳` | `游泳` | ✅ |
| `游泳  2026-11-02 18:00-19:00` | `游泳` | ✅ |
| `游泳  2026-11-02` | `游泳` | ✅ |
| **`游泳  18:00-19:00`**（只给钟点、没给日子） | **`游泳18001900`** ← 残渣 | ✅ 仍匹配 |
| `瑜伽  2026-11-03 07:30-08:30` | `瑜伽` | ✅ |
| `健身房 / 写作业 / 小组会议 / 看牙医 + 同日时` | 各自名字 | ✅ |

**结论和我一开始想的不一样：这条路是通的。**
只有"只给钟点"那一档抠出了残渣 `游泳18001900`，但 `_todo_name_match('游泳18001900', '游泳')` 返回 **True**
（它是包容式匹配），仍然命中；而且卡上的标题取的是 `old.get("title")`（`planner.py:5328`），
**残渣根本印不到卡片上**。

⇒ 所以 `sem={"title": hint}` **不是判据，是保险**：模型已经把名字当结构化参数传进来了，
那是全链路里最准的一份，走 `sem` 就完全不依赖 `_pick_todo_name` 的运气。
**它既然不花代价（三行），就留着** —— 但别在快照里把它写成"修掉了一个 bug"，它不是。

> 同一句话在确定性分支（`main.py:3727`）里仍然走抠名字那条路 —— 那里没有结构化参数，
> 只有学生原话，抠是唯一选择。**两条路的输入不同，别硬统一。**


**不需要 `partial` 绑 `session_id`**（78 号单里那个坑在这里不存在）：模型手上有对话历史，
它知道自己说的是哪条，直接把 `title` 传进来即可。
⇒ **不用改 `build_scheduling_tools()` 的签名**，也不用 `partial`，也就没有"绑定的形参混进 `parameters`
schema 被模型重复填"那个坑。

### 1.3 工具描述：把"不许用于新建"写死

```python
def propose_todo_change_tool_tool() -> Tool:
    return Tool(
        name="propose_todo_change",
        description=(
            "**改一条已经在日程里的待办的时间**（改期）。只出提案，不写库。\n"
            "title 传学生说的那条待办名（必填）；new_date/new_start/new_end 只在学生**说了**新时间时才填，"
            "格式 '2026-10-31' / '16:00'。\n"
            "⚠️ **学生只说了「撞了/改一下」没说新时间时，全都不填** —— 系统会自己挑一段排得下的，"
            "⛔ 不要反问『你想改到几点』（学生投诉原话：「没有帮我想时间，是我问了才说的」）。\n"
            "⛔ **它不会新建待办**。学生要加新的事，用 propose_todo_tool / propose_todo_slots_tool。\n"
            "⚠️ 命中不止一条时它返回候选清单 —— **照清单原样列给学生、问『是下面哪一条』，然后停**，"
            "不许猜、更不许说『已经改好了』。"
        ),
        parameters={...},   # title 必填，其余可选
        func=propose_todo_change_tool,
    )
```

### 1.4 挂载：只加一行

`planner.py:6532 build_scheduling_tools()` 里，跟三个 `propose_todo_*` 并排：

```python
    picked["propose_todo_change"] = propose_todo_change_tool_tool()
```

### 1.5 两处登记

| 位置 | 加什么 | 不加的后果 |
|---|---|---|
| `planner.py:4105 _TOOL_ECHO_RE` | 在组里加 `\|propose_todo_change` | 模型把它打成文本时，JSON 直接端到学生面前（红线 8） |
| `agent/readonly.py:43 WRITE_TOOL_NAMES` | `"propose_todo_change"` | 那张表是**反向断言**用的，漏登记 = 静默失去保护 |

---

## 二 判断与理由

### 2.1 为什么加 `auto_slot`，而不是教模型自己挑

模型自己挑也能写："查 `find_free_slots` → 挑一段 → 传 `new_start`"。但那是**三步**，
每步都可能跑偏（挑到撞课的、挑到过去的、忘了 `not_before`）。

`pick_free_slot` 是确定性代码，已经处理了"避开课和已有待办 / 按最长空档 / 不挑过去"。
**能由代码定死的，别交给模型**——这是这个项目一贯的做法，不是本单新发明。

### 2.2 为什么两条路的策略不同（确定性追问 vs 引擎替他挑）

| 走哪条 | 处境 | 该做什么 |
|---|---|---|
| 确定性分支（`main.py:3727`） | 这句话**没经过模型**，系统手上只有"没给新时间"这个事实 | **追问**（快、稳、学生补一句就办了） |
| 引擎（本单的工具） | 模型**已经查过**、知道撞了谁、也知道库里有哪些 | **替他挑**（他要的是"帮我想个时间"） |

**同一个"没给新时间"，两条路上想要的答案是相反的。** 用 `auto_slot` 一个参数分开，
而不是在函数里猜"现在是谁在调我"。

### 2.3 和 #85 的关系：**#85 先不做，等 #86 的效果**

#85 的判断是"提示词补半句，教它别反问"。现在看，那 4 句反问的**直接原因是没有工具**，
不是措辞不好。

- **#86 之后如果出卡了** → #85 整张作废
- **#86 之后仍然反问** → 那才是提示词的问题，#85 再上

**先给手，再教怎么用。反过来的话，教会了也没手可用。**

### 2.4 代价

- 引擎工具箱从 13 → 14 个工具，模型选择面变大一点点。可忽略。
- `auto_slot=True` 会多调 1~3 次 `pick_free_slot`（纯计算）。
- **唯一真实风险**：模型把它当成"新建"来用。所以 §1.3 那句"它不会新建"必须写死在描述里，
  而且 §3 的 T5 就是钉这个的。

---

## 三 验证

### 3.1 工具级（直接调函数）

| # | 调用 | 期望 |
|---|---|---|
| T1 | `op=retime, title=游泳`（库里有游泳，和课撞着） | 返回 `__proposal__`，**带新时段**，且和课不重叠 |
| T2 | `op=retime, title=游泳, new_date=2026-11-02, new_start=18:00, new_end=19:00` | 按他说的出卡，**不许自作主张改**；卡上标题必须是库里的「游泳」，**不许带 `1800` 这类残渣**（标题取自 `old.get("title")`，`:5328`） |
| T3 | `op=retime, title=不存在的东西` | `not_found: true`，`pool_n > 0` |
| T4 | `op=rename` | `ok: false`，说明只支持 retime（**不许静默当 retime 处理**） |
| T5 | `op=retime` 但不给 title | `ok: false`，提示要给名字 |
| T6 | `op=retime, title=待办`（模型传了个脏值），**夹具里待办 ≥2 条** | `looks_like_thing` 挡下它 → 退回抠名字 → 抠不出来 → `:5192` 把它当"没点名" → 池子=全部 ⇒ 期望 `need_pick` 带候选清单，**不许直接出卡**。（库里只有 1 条时会改那 1 条 —— 那是既有行为，本单不修，所以夹具必须 ≥2 条） |

### 3.2 端到端（这是本单真正要证明的）

| # | 场景 | 期望 |
|---|---|---|
| **E1** | 真机：`游泳撞时间了，帮我换一个时间` | **出改期卡**（`options` 非空、`old_todo_id` 有值），屏幕上**不再出现**「你说的是哪一条」式的反问 |
| E2 | 真机：`把游泳改到周三 18:00` | 出卡，时段就是他说的 |
| **E3 正控** | 真机：`帮我加个游泳` | 仍走**加待办**那条路（`propose_todo_tool`），**不许**被 change 抢走 |
| **E4 正控** | 确定性分支：`把游泳挪到周五`（不经过引擎） | 行为**逐字不变**（`auto_slot` 默认 False 没影响它） |

### 3.3 会顶红的既有判据（78 号停工时已点出，逐个改口）

| 位置 | 断言 | 改成 |
|---|---|---|
| `test_31` A3 | `len(tools) == 13` | **14** |
| `test_82` | `WRITE11` / `COUNTS["planner"] = 25` | 12 / 26 |

**这两个数是"账"，不是"期望"** —— 改它们的时候要在注释里写清"因为多了 propose_todo_change"，
别让人以为是放宽。

### 3.4 变异

| # | 变异 | 必须变红 |
|---|---|---|
| V1 | 把 `auto_slot=True` 改成 `False` | **E1 红**（引擎又只能反问 —— 这就是回到 #84 的状态） |
| V2 | 从 `_TOOL_ECHO_RE` 里删掉新工具名 | 工具名回显那条测试红 |
| V3 | 从 `WRITE_TOOL_NAMES` 里删掉 | 反向断言红 |
| V4 | 工具描述里删掉「它不会新建」那半句 | **E3 红**（模型拿它去加新待办）—— 若 E3 仍绿，说明这句删了没影响，那就在快照里写明"这句是保险，不是判据" |
| V5 | `sem={"title": hint}` 改回 `None`（或者删掉那个 `sem` 变量） | **实测：不会红**（§1.2-bis 那张表的 9 例全过）。这是**已知哑弹** —— 写在这里是为了让下一个人别拿它当护栏，也别以为删掉 `sem` 就"修掉了一处风险" |

### 3.5 回归

**E4 是本单的命门**：`auto_slot` 默认 False，确定性分支必须逐字不变。
变异：把默认值改成 `True`，E4 必须红（改期在普通模式下会开始自作主张换时段）。

---

## 四 不做的事

1. **不做 `op=rename`** —— 改名字是另一件事（而且「（周）健身房」那个脏名字的根因在事名残渣族，不是一个 rename 能盖住的）。
2. **不做 `op=change_duration`** —— 时长继承是另一个坑（`main.py:6339` 那一族）。
3. **不动 `build_scheduling_tools()` 的签名** —— 不需要 session（§1.2）。
4. **不改确定性分支（`main.py:3727`）的任何行为** —— E4 钉着。
5. **不做 #85** —— 等本单效果，见 §2.3。

---

## 五 给执行方的四句

1. **`auto_slot` 默认必须是 `False`。** 这个参数一旦默认 `True`，普通模式下的改期会开始"自作主张换时段"——
   学生说「挪到周五」，系统给他挑了个周五的 08:00，而他要的是原时段平移。**那是比"没工具"更坏的一种坏。**
2. **`title` 走 `sem` 递进去（§1.2-bis），但别把它当护栏。** 实测过了：`sem=None` 也能跑通，
   卡片标题取自库里那条（`:5328`），残渣印不上去。留 `sem` 只是因为它比"再抠一遍"更准、且零代价——
   **写快照时别记成"修掉了一个 bug"。**
3. **工具不拿 session，别去动 `build_scheduling_tools` 的签名。** 78 号单里那个 `partial` 绑 session 的写法
   在这里不需要（模型手上有对话历史，`title` 它自己填）。省掉它，也就省掉"绑定的形参混进 `parameters`
   被模型重复填 → `TypeError` 被吞成『参数不对』"那个最难查的坑。
4. **E1 和 E4 是一对，缺一不可。** E1 证明"引擎终于能办事"，E4 证明"普通模式没被这次改动碰到"。
   只跑 E1，很可能在 `build_retime_existing_proposal` 里留下一个默认值写错的隐患。
