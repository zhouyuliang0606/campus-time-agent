# 63｜只读工具的"粒度"要跟学生的提问粒度对齐（一周待办别逐日查）（drop-in）

> 起源：实测 #61 的 Agent 模式，「我这周有什么待办」变成 **8 次工具调用 / 22 行轨迹**，
> 因为 `list_day_todos` 只能查**一天**。模型没有"查一段"的手，只能把七天挨个点一遍。
> 这是 #61-A 把只读工具跨栏挂出来之后**才暴露**的（以前这句被确定性分支一次 `list_todos()` 接走，
> 工具粒度的问题看不见）。

---

## 〇 现场事实（2026-10-25，HEAD = b36c8ad，8022 沙箱副本实测）

同一句「我这周有什么待办」，两种模式的对照：

| | 轨迹 | 实际工具调用 | 耗时 | 答案 |
|---|---|---|---|---|
| Agent 关 | 3 行 | 0 次（确定性直答） | 1.2s | 「目前没有待办事项。」 |
| Agent 开 | **22 行** | **8 次** | **3.9s** | 内容对，但先翻了 8 遍 |

那 8 次是：

```
🔧 get_weekly_timetable {}
🔧 list_day_todos {"date": "2026-10-25"}   → null
🔧 list_day_todos {"date": "2026-10-26"}   → null
🔧 list_day_todos {"date": "2026-10-27"}   → null
🔧 list_day_todos {"date": "2026-10-28"}   → null
🔧 list_day_todos {"date": "2026-10-29"}   → null
🔧 list_day_todos {"date": "2026-10-30"}   → null
🔧 list_day_todos {"date": "2026-10-31"}   → null
```

七次返回**全是 `null`**，长得一模一样。评委看到的是"同一个工具连点七遍"，
不是"这个 Agent 会规划"。这是演示的**减分项**，不是加分项。

### 病根：工具粒度和提问粒度不一致

`app/modules/planner.py:1309`

```python
def list_day_todos(date: str) -> list:
    """列某天已安排的待办（人话：看看这天已经塞了些什么）。"""
    todos = list_todos(date)          # ← 传了 date，就只取那天
```

而底层**早就有全量、也有按月**：

```
app/store.py:1428  def list_todos(date: str | None = None) -> list      # 不传 date = 全部
app/store.py:1440  def list_todos_in_month(prefix: str) -> dict         # 按月
```

⇒ 能力在底层是齐的，**只是没包成一个"查一段"的工具交到模型手上**。
确定性分支 `3d`（`main.py:3265` 起）一次 `list_todos()` 就答完了 —— 同一条产品能力，
两条路差 7 倍调用，就是因为工具集只切出了"单日"这一个粒度。

### 顺带记一笔（本单不处理）

Agent 模式下同一句的 `module` 会漂：【我的课表全删了】关时 `planner`、开时 `schedule`。
因为 `3b` 那条确定性分支被跳过后，路由按语义重判了。演示时同句话的模块标签会变，
属于"交给模型"的自然结果，**不改**，但要知道有这么回事。

---

## A 新增一个"查一段"的只读工具

### A1. `app/modules/planner.py`，紧挨 `list_day_todos` 之后

```python
def list_todos_range(start: str, end: str) -> list:
    """列 start~end（含两端，YYYY-MM-DD）之间已安排的待办。

    为什么要有它：`list_day_todos` 只能查一天，学生问"这周/这个月有什么安排"时，
    模型只能把每一天挨个点一遍 —— 实测一次问句变成 7 次调用、轨迹 22 行，
    看着像在乱翻。底层 `store.list_todos()` 本来就能一次取全量，
    这一支只是**按范围筛**，不引入任何新的读写。
    """
    start, end = (start or "").strip(), (end or "").strip()
    if not start or not end:
        return [{"info": "要查一段时间的待办，得给我起止日期（如 2026-10-25 到 2026-10-31）"}]
    if end < start:                                   # 学生说反了也别报错，换过来就是了
        start, end = end, start
    todos = [t for t in list_todos() if start <= (t.get("date") or "") <= end]
    if not todos:
        return [{"info": f"{start} ~ {end} 之间没有安排任何待办"}]
    todos.sort(key=lambda t: (t.get("date") or "", t.get("start") or ""))
    return [{
        "id": t["id"], "title": t["title"], "date": t["date"],
        "start": t["start"], "end": t["end"], "status": t["status"],
    } for t in todos]
```

`list_todos()` 在 `planner.py` 里已经是 import 进来的（`list_day_todos` 就是这么调的），**不加 import**。

### A2. `build_tools()` 里挂上（`planner.py:5824` 那个字典，紧跟 `list_day_todos` 之后）

```python
        "list_todos_range": Tool(
            name="list_todos_range",
            description=(
                "查**一段时间内**已安排的待办（含起止两天）。学生问「这周 / 这个月有什么安排」"
                "「周三到周五有没有事」时用**这一个**，不要一天一天调 `list_day_todos`。"
                "入参 start、end 都是日期，格式如 2026-10-25。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "起始日期，如 2026-10-25"},
                    "end": {"type": "string", "description": "结束日期（含），如 2026-10-31"},
                },
                "required": ["start", "end"],
            },
            func=list_todos_range,
        ),
```

### A3. `list_day_todos` 的说明书补一句边界（不改实现）

现有 description（`planner.py:5824` 那个字典里）加半句：

```
"…只看**这一天**。要查一段（这周/这个月）用 `list_todos_range`，别逐日调。"
```

### A4. 登记进跨栏账本（否则别的栏仍然拿不到它）

`app/agent/readonly.py` 的 `READONLY_SPEC` 里加一条：

```python
    "list_todos_range":     "planner",
```

**这一条不能漏**：#61-A 之后只读工具的"能挂到哪个栏"完全由这本账决定，
新工具不登记 = 只有 planner 栏有它，学生换个栏问同一句话又要逐日翻。

---

## B 提示词里点一句"别逐日"

`app/main.py` 的 `ASSISTANT_RULES` 第 5 条（#61-A 新加的那条"需要事实就去查"）后面接一句，
**不新起一条**：

```
查"一段时间"的待办（这周、这月、周三到周五）用 `list_todos_range` 一次查完，
不要一天一天调 `list_day_todos` —— 连点七遍同一把工具，学生看到的是你在乱翻。
```

---

## 验证

### 变异（打在判据上游）

| 编号 | 变异 | 期望 |
|---|---|---|
| X1 | `readonly.py` 里删掉 `"list_todos_range": "planner"` | 「faq 栏问这周安排」用例必须红（拿不到这把工具） |
| X2 | `list_todos_range` 的 `end < start` 交换那两行删掉 | 反着传日期的用例必须红（返回空而不是换过来） |
| X3 | B 项那句提示词删掉、A3 那句说明书边界也删掉 | 真机「我这周有什么待办」的工具次数应**回升**到 ≥5（证明这两句不是装饰） |

### 真机（同一句，改前改后对比）

1. **Agent 模式 + 「我这周有什么待办」**：
   - 改前：8 次调用 / 22 行轨迹 / 3.9s（本单〇节的实测）
   - 改后目标：**≤ 2 次调用 / ≤ 10 行轨迹**，且 `list_day_todos` **一次都不出现**
2. **Agent 模式 + 「周三到周五有没有事」**：只调 `list_todos_range` 一次。
3. **有数据的场景**（先造 3 条不同日期的待办再问）：三天的都要列出来，顺序按日期升序。
4. **非 Agent 模式回归**：`3d` 确定性分支那条路**一个字都不该变**（它本来就用 `list_todos()`，
   不经过新工具）。用同一句跑改前改后，`answer` 与 `trace` 条数应一致。
5. **`find_free_slots` 不受影响**：它内部也用 `list_todos(date)`，本单没动它。

### 回归

基线是 #62 收工时的总数（#61 是 4859）。新增用例让总数上升；**下降就要查**。

---

## 不做的事

1. **不删 `list_day_todos`**。`find_free_slots`、3c-pre 那些分支还在按单日用它，删了就是断手。
2. **不改 `store.list_todos` 的签名**。它已经能收 `date=None`，够用了。
3. **不动 `3d` 确定性分支**。它在非 Agent 模式下答案更快更稳，本单不改它。
4. **不合并 `get_weekly_timetable`**（查课表）和 `list_todos_range`（查待办）—— 两件事，
   合并成一个"查一切"的工具只会让模型更难选。
5. **不顺手给 `list_todos_range` 加"按状态筛""按标题搜"**。粒度对齐到提问就够了，
   参数每多一个，模型选错的概率就高一档。

---

## 给执行方的三句

1. **别把这单做成"再加一堆工具"**。只加**一个** `list_todos_range`，理由是"底层能力现成、
   只是没切出这个粒度"，不是"多给模型一只手"——后者是 #61-A 的事，已经做完了。
2. **A4 的账本登记别漏**。新工具不进 `READONLY_SPEC`，这次修的就只在 planner 栏生效，
   换个栏问同一句话又会逐日翻，等于白修。
3. **X3 那条变异是来证明"提示词不是装饰"的**。如果删掉提示词后工具次数没回升，
   说明模型本来就会选 `list_todos_range`（说明书写得够清楚），那 B 项可以不做 ——
   这种"少写一段代码"的结论也算通过，但要拿数据说话。
