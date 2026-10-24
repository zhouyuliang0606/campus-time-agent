# 61｜Agent 模式开关 + 只读工具全集 + 多轮 ReAct 真的能被看见（drop-in）

> 一单三件，按 A→B→C 顺序做，每件独立可提交、独立可回滚。
> B 是**验收不是实现**（60 号已经落地了，别重写一遍）。

---

## 〇 现场事实（2026-10-24 23:46 那棵树上实测，不是回忆）

**已经落地的，本单不碰**：

| 项 | 证据 |
|---|---|
| 58 号 A（轨迹认工具步） | `student.html` 的 `renderTrace` 已有 `hasAction` / `hasResult` 分支，`cap = hasAns ? 80 : 120` |
| 60 号 A（判定进轨迹） | `main.py:1495` `def _reason_steps(`、`:1851` `meta["reasons"] = _reason_steps(`、`:1690` 读出拼进出口 |
| 60 号 B（工具结果收成学生话） | `app/agent/public_view.py`，148 行，`engine.py:20` 已 import |
| 60 号 C（轨迹限高） | `renderTrace` 尾部 `.trace-body` + `step:0` 不画编号（注释写着"JS 里 0 是假值，正好顺带垫掉"） |
| 59 号（刷新后乱码卡） | `main.py:6236` `cards = [o for o in opts if o.get("kind") not in _PENDING_AUX_KINDS]` |

**这一单要解决的三件事，各自的根**：

1. **模型有手，但手被锁在别的格子间里。**
   工具箱是**按模块切**的：`REGISTRY[module] = (prompt, build_tools)`（`main.py:320-329`），
   `_chat_impl:1910` 只取当前模块那**一份**，再补 `files_tools()`、条件性补 `scheduling_tools()`。
   ⇒ 学生这句话落到 `faq` 栏，他手里就只有 `search_kb` + 三个文件工具，
   **没有** `get_weekly_timetable`、**没有** `find_free_slots`、**没有** `list_day_todos`
   —— 模型想 Act，无手可动，只能凭上下文瞎猜或反问。
   现有 26 个工具（去重后）里**只读的有 15 个**，它们本来就在那儿，只是挂不到该挂的地方。

2. **判定的结论已经进轨迹了（60 号），但本单要顺手验收它两条出口都在。**
   60 号只在 JSON 出口验过，SSE 那条（`emit` 非空）容易漏。

3. **确定性快路 = 2100 行，它才是"评委看不见 ReAct"的主因。**
   `_chat_impl` 1703–4339。快路在 `1957`（`if not _station_surface:`）里，
   引擎组装从 `4064` 开始，`:4105`/`:4107` 调 `engine.run`。
   ⇒ 大部分真机问句在 1957–4063 之间就被 `return` 掉了，**根本没走到引擎**。
   轨迹里自然只有判定那几条 🧭，没有 🤔/🔧/👀。

**两个必须知道的行号结构**（决定 C 项改成什么样）：

```
1957:        if not _station_surface:          ← 快路总闸（4 空格）
1969:            if looks_like_timetable_import(message):      2.5 课表导入（硬信号卡）
2019:            if _wo_kind and not is_custom_module(...):    2.6 上报卡（硬信号卡）
2056:            if is_confirmation(message) and peek_pending(session_id):   3 确认执行
2400:            picked_now = _match_todo_pick(session_id, message)          3a-bis 候选卡确认
2428:            if is_confirmation(message) and not peek_pending(...):      3a-c 确认落空兜底
   ↑ 2428 这个 if 块一直开到 2634 结束
2635:            # 3b) 确定性清空分支        ← ★ C 项的短路点插在这行之前
2640:            if wants_clear_timetable(...)
2698:~3066:      3c 加待办 / 3c-pre* / 3d 列待办 / 3d-ret 改期 / 3c-ter 看不见 X …
   ↑ 这些是"替模型抢话"的分支，正是 Agent 模式要关掉的
4064:    engine = AgentEngine(...)           ← 引擎组装，276 行，引 8 个外层局部量
4339:    _chat_impl 的最后一个 return
```

引擎那 276 行引用到的外层局部量**只有 8 个**：
`module_key` / `session_id` / `message` / `prompt` / `tools` / `persona_key` / `model_config` / `emit`。
（`history` 是段内自己造的。）这决定了 C 项不是大重构。

---

## A 只读工具全集：跨栏挂载，写侧一个都不许进

### 现状

只读工具 15 个，散在 5 个模块里，互不可见：

| 模块 | 只读工具 |
|---|---|
| `schedule` | `get_day_courses`、`plan_task`（只算计划不写库） |
| `planner` | `get_weekly_timetable`、`find_free_slots`、`list_day_todos` |
| `faq` | `search_kb` |
| `express` | `list_my_packages`、`list_pending_pickup`、`get_by_code` |
| `takeout` | `list_my_orders`、`list_active_orders`、`get_order` |
| `files` | `list_uploaded_files`、`read_uploaded_file`、`search_docs` |

写侧 / 提案侧 10 个：`submit_work_order` + 9 个 `propose_*`。

### 病根

挂载按"模块归属"切，不按"读/写"切。
**读**是安全操作（一个字节都不写库），把它也锁在模块里没有安全收益，只有"想 Act 无手可用"。

### 修法

**A1. 新建 `app/agent/readonly.py`**

```python
"""只读工具的全集（人话：这些工具只"看"不"改"，所以哪个栏都能给模型几只）。

为什么单开一份、不直接 `tools.update(各模块 build_tools())`：
  · 那会把 `propose_*` 一起放进来 —— 写侧必须留在"意图对了才挂"的判据后面；
  · 模块之间有**同名不同实现**的工具（下面 `get_by_code` 那条），update 谁后谁赢。
所以这里用"名字 → 来源模块"的显式账本，不靠命名前缀猜。
"""
from app.agent.tools import Tool

# 名字 → 它该从哪个模块的工具箱里取。
# ⚠️ 必须写死来源，不能只写名字：
#    `get_by_code` 在 express 和 station **各有一份、同名、实现不同**
#    （express 那份只在这个学生自己的件里查；station 那份是店员查任意学生）。
#    `schedule.py:215` 那段长注释记的就是同款事故的第一集：
#    以前 schedule 和 planner 各注册了一个 `find_free_slots`，入参还不一样，
#    `tools.update()` 后注册的赢，学生拿到「周三 不是合法日期」。
#    这是第二集。别再靠"名字唯一"这个约定。
READONLY_SPEC: dict[str, str] = {
    "get_day_courses":      "schedule",
    "plan_task":            "schedule",
    "get_weekly_timetable": "planner",
    "find_free_slots":      "planner",
    "list_day_todos":       "planner",
    "search_kb":            "faq",
    "list_my_packages":     "express",
    "list_pending_pickup":  "express",
    "get_by_code":          "express",     # ← 驿站的同名份**故意不要**，见上
    "list_my_orders":       "takeout",
    "list_active_orders":   "takeout",
    "get_order":            "takeout",
    "list_uploaded_files":  "files",
    "read_uploaded_file":   "files",
    "search_docs":          "files",
}

# 反向断言用（测试里读，运行时不读）。写侧工具名全列在这儿，不靠前缀推断。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "submit_work_order",
    "propose_slots", "propose_todo_tool", "propose_todo_mode_tool",
    "propose_todo_slots_tool", "propose_todo_remove", "propose_todo_done",
    "propose_clear_timetable", "propose_course_change", "propose_timetable_change",
    "propose_custom_record",
})


def readonly_tools() -> dict[str, Tool]:
    """把 READONLY_SPEC 里那些工具从各自模块取出来（人话：跨栏借书，只借"只读"那几本）。"""
    from app.main import REGISTRY          # 延迟 import：main 会 import 本模块，顶层相互引用会成环

    pools: dict[str, dict[str, Tool]] = {}
    out: dict[str, Tool] = {}
    for name, mod in READONLY_SPEC.items():
        if mod not in pools:
            builder = REGISTRY[mod][1]
            pools[mod] = builder()
        tool = pools[mod].get(name)
        if tool is not None:               # 缺了就跳过，不让一个改名炸掉整条链路
            out[name] = tool
    return out
```

> 函数体每轮对话重建一次对象。**不要**加 `functools.lru_cache`：测试里有改完模块再读的场景，
> 缓存住会让红点消失不掉。一次 `build_tools()` 就是几个 dict 字面量，成本可忽略。

**A2. `main.py:1935` 之后（`tools.update(scheduling_tools())` 那块之后）加一处挂载**

```python
    # ★ #61-A 只读工具跨栏挂载：读不算"代办"，多给模型几只手不会多写一个字节。
    #   护栏有三道：① 只挂 READONLY_SPEC 里那 15 个（写侧一个都进不来）；
    #              ② `setdefault` 不覆盖模块自己那份 —— 说明书归模块维护，
    #                 谁熟这块业务谁来写，跨栏那份只补"这个栏里本来没有"的手；
    #              ③ 驿站端不挂：那 15 个里有 9 个是**学生个人数据**
    #                 （我的快递/我的订单/我的待办/我的课表），店员看见就是串数据（#34 那条血）。
    if not _station_surface:
        for _n, _t in readonly_tools().items():
            tools.setdefault(_n, _t)
```

**A3. 提示词加一句引导**（`main.py` 的 `ASSISTANT_RULES` 常量里，紧跟"先查再说"那条）

```
你手上有几个只读工具（查课表 / 查空档 / 查待办 / 查快递 / 查订单 / 查知识库 / 查文件）。
需要事实就去查：查一次比猜一句强，学生看得见你查了什么。
但**只在真需要事实时查**，查到就要用掉——为了凑步子连查三次同一件事，比不查更糟。
```

### 为什么**不**顺手把写侧也放开

写侧工具挂出来就是"能出确认条"，而历史事故正是"闲聊时挂出待办卡"
（`_schedule_tools_allowed` 的注释里记着：faq 里问"你是谁"，模型把「校园问答」当待办名出了张卡）。
所以写侧仍然只走 `_schedule_tools_allowed` 那一道判据，本单不动它。
**只读全放开、写侧不放开**——这条界线是本项的核心，别在执行时"顺手帮我一起放开"。

### 风险与代价（执行前先看）

- 工具从平均 5 个涨到 20 个左右，**system 里的 tool schema 会胖一圈**。
  粗算 15 × ~110 字符 ≈ 1.6K 字符，每轮都随请求发一次。
  实测要求：`llm_usage.jsonl` 里同一条问句的 `prompt_tokens` 涨幅 **> 35% 就回来报**，别自己压。
- 模型拿到更多手会**多走一轮**。一轮 = 一次 LLM 调用（p50 3.7s 实测）。
  这条本身就是要的效果（评委看的就是多轮），但**闲聊句不许变慢**——
  验证里专门钉一条"问候语仍然是单轮"。

---

## B 判定层进轨迹：**验收，不实现**

60 号已经做完了。本项只做三条验收，**任何一行都别重写**：

- **B1** 两条出口都带 `reasons`：JSON 出口与 SSE 出口各跑一次，`trace` 里都要有 `step:0` 的 🧭 行。
  （60 号只在 JSON 那条验过。）
- **B2** `read` 与 `reasons` 互不覆盖：`grep -n 'meta\["read"\] =' app/main.py` 找出所有赋值点，
  **每一处后面都跟一次 `meta["reasons"] =`，或者干脆没有 `reasons`**——
  60 号的设计是"`reasons` 独立于 `read`，三个覆盖点不用动"。三处都跑一遍，别只看第一处。
- **B3** 轨迹首行不出现编号：`renderTrace` 里 `const st = t.step ? ...` 已经把 `0` 垫掉，
  但要确认 60 号 prepend 的那些项**确实带 `step: 0`**，而不是没写 `step` 字段（那会渲染成没有编号的普通行，
  看起来像"少了一步"的反面：像"多了一步没编号的"）。

B 项交付物 = 三条实测结论（通过/不通过 + 证据），**不改代码**。

---

## C Agent 模式开关：关掉 2100 行抢话，红线三件一行不动

### C1. 判据（`main.py` 顶部，`_rule_intent` 附近；全项目只此一处）

```python
# Agent 模式：演示用开关 —— 强制走 ReAct 引擎、跳掉所有"替模型抢话"的确定性快路。
# 默认关：真机上确定性快路更快、更不会胡说，那是产品该有的样子；
# 只有演示/评审时才需要"每一句都看得见多轮循环"。
_AGENT_MODE_DEFAULT = (os.getenv("CAMPUSTIME_AGENT_MODE", "").strip().lower()
                       in ("1", "true", "on", "yes"))


def _agent_mode_on(body: dict) -> bool:
    """三处来源，优先级：请求体 > 环境变量 > 默认关。

    ⚠️ 别写 `bool(body.get("agent_mode"))` —— 前端传字符串 "false" 过来那也是真值，
       演示时"我明明关了它还在走 Agent"就是这么来的。
    """
    v = body.get("agent_mode")
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes")
    return _AGENT_MODE_DEFAULT
```

在 `_chat_impl:1710` 拿到 `body` 之后就取一次，**只取一次**，后面全读这个局部量：

```python
    _agent_mode = _agent_mode_on(body)
```

### C2. 把引擎段抽成 `_run_agent_loop()`（**原样搬，不改一行逻辑**）

把 `main.py` 的 **4064–4339**（`# 4) 组装引擎…` 到 `_chat_impl` 最后一个 `return`）整段剪出来，
放到 `_chat_impl` **之前**，包成：

```python
async def _run_agent_loop(*, module_key, session_id, message, prompt, tools,
                          persona_key, model_config, emit):
    """组装引擎 + 跑 ReAct 循环 + 收尾补条 + 出口（原先 `_chat_impl` 的 4) 那一段）。

    #61-C 抽出来的唯一目的是**让它有两个调用点**：正常走到这儿、或 Agent 模式跳过快路直接到这儿。
    纪律：这段代码全项目只有这一份，两个调用点参数形状完全一致 —— 不许复制。
    """
    ...  4066–4339 原样照抄（行内所有 return 不动，它们本来就在函数末尾，语义一样）
```

原 `4064` 处改成：

```python
    # 4) 组装引擎并跑 ReAct 循环
    return await _run_agent_loop(
        module_key=module_key, session_id=session_id, message=message,
        prompt=prompt, tools=tools, persona_key=persona_key,
        model_config=model_config, emit=emit,
    )
```

> 落地检查：搬完先 `python -m pyflakes app/main.py`，报 `undefined name` 的就是漏传的外层变量。
> 参数**预计 8 个**；如果发现要传 15 个以上，说明剪早了或剪晚了（剪点应是 `# 4) 组装引擎` 那行）。

### C3. 短路点：插在 **2635 行（`# 3b) 确定性清空分支`）之前**，8 空格缩进

```python
        # ★ #61-C Agent 模式：从这里往下全是"替模型抢话"的分支，整段跳掉，直接去找引擎。
        #
        #   为什么不插在 `if not _station_surface:` 那道闸上（把条件改成
        #   `and not _agent_mode`）：那会把上面**红线三件**一起关掉 ——
        #     2.5 课表导入卡 / 2.6 上报卡（产品承诺的硬信号）
        #     3 确认执行 / 3a-bis 候选卡确认（安全红线：AI 只提案、写库必须学生点头）
        #     3a-c 确认落空兜底（诚实性：暂存没了就得如实说，不能让模型编"我再帮你出一次"）
        #   Agent 模式要关的是"抢话"，不是"红线"。
        #   所以短路点选在这三件之后、3b 之前 —— 上面一行代码都不用搬。
        #
        #   代价（要说清）：Agent 模式下"课表全删了"「把X排到今天19:00」这类话不再走确定性分支，
        #   改由模型调 propose_clear_timetable / propose_todo_tool。写入仍然要学生点确认
        #   —— 安全性不变，只是路径变长（这正是演示想让人看见的）。
        if _agent_mode:
            return await _run_agent_loop(
                module_key=module_key, session_id=session_id, message=message,
                prompt=prompt, tools=tools, persona_key=persona_key,
                model_config=model_config, emit=emit,
            )
```

**短路点为什么在这个位置的四个变量已就绪**：
`prompt`（1906）、`tools`（1924–1935）、`session_id`（1741）、`module_key`（1867 之后）——
2635 行处在它们全部算完之后，直接读即可。

### C4. 前端开关（学生端 `student.html`，一处）

- 设置/齿轮那块加一个勾选：**「Agent 模式（演示用：每句都走多轮推理，慢一点）」**，默认不勾。
  存 `localStorage.setItem("agent_mode", on ? "1" : "0")`。
- `/api/chat` 的请求体里加 `agent_mode: on`（**布尔，别传字符串**，虽然 C1 两种都收）。
- **轨迹面板上要有个可见的状态**：勾上时在输入框旁边显示一个小标 `🧭 Agent 模式`，
  否则演示时自己在不在这个模式全靠记忆，评委问起来没法当场证明。

### C5. Agent 模式**不**放宽写侧工具（重要，别自作聪明）

`_schedule_tools_allowed(...)` 原样不动。所以：
- 演示句落到 `planner` → 手最全（含 `propose_*`），能查能排；
- 落到 `faq` → 有 15 只只读手，**排不了**，会答"你周三最空，但我没法直接建待办"。

这条看起来是缺陷，实际是**故意的**：写侧挂载判据（意图对了才挂）正是
"闲聊被推销成待办卡"那条事故的修复，拿它换演示效果会把旧坑挖回来。
真要排，正确做法是让路由把那句判成 `planner` / `add_todo`，**不是**在 Agent 模式里放开写侧。

→ 所以验证里有一条硬要求：**演示句在 Agent 模式下必须落 `planner`**。落不到就回来报，别在现场改判据。

---

## 验证（三条都要，缺一条这单不算完）

### 变异测试（每项至少一条，变异打**在判据上游**）

| 编号 | 变异 | 期望 |
|---|---|---|
| M1 | `READONLY_SPEC` 里加一条 `"propose_todo_tool": "planner"` | 反向断言测试**必须红**（写侧混进只读全集） |
| M2 | 把 `READONLY_SPEC["get_by_code"]` 改成 `"station"` | 断言红（来源写死这道护栏在起作用）；若还绿，说明测试读的是名字不是来源 |
| M3 | `_agent_mode_on` 里 `isinstance(v, bool)` 改成 `return bool(v)` | 传 `"false"` 的用例**必须红**（字符串真值这个坑） |
| M4 | 删掉 C3 的短路块 | Agent 模式的用例必须红：演示句轨迹里工具步数掉到 0 |
| M5 | `main.py` 把 `setdefault` 改成 `update` | 若有模块自带同名工具，说明书被跨栏那份盖掉——断言"模块自己那份优先"必须红 |

> 变异纪律（#60 学到的）：变异必须打在**判据上游**。打在过滤条件下游的变异会跑出 `rc=0` 的哑弹，
> 看着通过、其实什么都没测。

### 真机（每天一次，用新 sid，别复用）

每次探针**必须新 sid**。复用固定 sid 会读到上一轮的暂存/历史，编出根本不存在的机制
（#60 那次"0 工具却答对取件码"就是这么被骗的）。

1. **默认模式（开关关）= 改前一字不差**：把改前录过的 5 句真机问句重跑，`answer`、`module`、
   `options` 数量、`trace` 条数逐项比对。**允许的唯一差异**是 `trace` 里多了 60 号那几条 🧭（它已落地）。
   有别的差异就是回归。
2. **Agent 模式 + 定稿演示句**「帮我看看周一到周三哪天课最少，然后那天排两小时做题」：
   - `module` 必须是 `planner`；
   - `trace` 里至少 **2 个** `action` 步、**2 个** `result` 步、**3 轮**以上；
   - 末尾一张确认卡；
   - 把 `trace` 原样打印出来人工看一遍：**有没有出现内部词**（"已向前端展示""确认条""proposal"）
     —— 有就是 60 号 B 的 `public_view` 漏了某个工具，补那个工具的 `__user_view__`，别在前端打码。
3. **Agent 模式 + 闲聊**「你好」「你是谁」：必须**单轮**（无工具调用），不许挂任何卡。
   这条是 M1 那类事故的守门员。
4. **Agent 模式 + 学生回「确认」**（先制造一张卡）：必须**当场执行、不进引擎**。
   轨迹里不该出现 🤔/🔧，只有确定性那条回执。这条证明 C3 的位置选对了。
5. **驿站端**（`/station`）：「我的代办」仍然拿不到学生数据，且 `readonly_tools` 一只手都没挂进去。

### 回归

- 全量 `pytest`。**预期会红的**：凡是断言"这个模块的工具箱恰好有 N 个工具"的用例。
  处理原则：**改测试不改代码**——它们钉的是"挂载清单"，而本单就是故意改挂载清单；
  但改的时候必须把新数量写死，不许改成 `assert len(tools) >= 1` 那种糊弄式放宽。
- `test_72` A 批（A3/A4）钉着"两边没有同名工具"：**本单会让 `get_by_code` 在两个模块里同时被挂出来**，
  但**不是**同一个工具箱里两份同名——一个是模块自己挂的、一个是跨栏补的，且 `setdefault` 保证只有一份。
  这条用例怎么处理，执行时看清楚它断言的是"工具箱里"还是"仓库里"；是后者就**回来报**，别自己改。

---

## 不做的事

1. **不动 `_station_surface` 那道闸**，驿站端行为零变化。
2. **不动写侧工具挂载判据**（`_schedule_tools_allowed`），不因为"演示方便"而放开。
3. **不重写 60 号**（`_reason_steps` / `public_view.py` / 轨迹限高），B 项只是验收。
4. **不把 `_chat_impl` 剩下那 1900 行分刀**。本单只剪出 4064–4339 这 276 行，
   分刀是另一单的事——两件事混在一次提交里，红了分不清是谁。
5. **不新增任何工具**。15 个只读工具全都在仓库里，本单只解决"挂不到"。
   要加新工具（比如"算连续空档"）另开单。
6. **不改存储层**（JSON→SQLite 那笔账单独算）。

---

## 给执行方的三句

1. **A 项是"借书"，不是"写书"**——只把已有的 15 个只读工具跨栏挂出去，一个 `Tool(...)` 都别新写；
   `get_by_code` 一定要写死取 `express` 那份，驿站那份同名不同实现，踩过一次了。
2. **C 项的短路点只有一个正确位置：2635 行（`# 3b)` 注释之前）**。
   往上挪会关掉红线三件（确认执行/硬信号卡/确认落空兜底），往下挪会漏掉 3b 清空分支；
   搬完之后 `pytest` 里"学生回确认必须当场执行"那条如果红了，就是位置错了，不是测试错了。
3. **B 项不许改代码**。60 号已经落地并且验过一次；本项交付的是三条结论和证据，
   发现不对**回来报**、不要顺手重写——上一单的 `public_view` 是被真机数据调出来的，
   凭感觉重写会把那些边界一起丢掉。
