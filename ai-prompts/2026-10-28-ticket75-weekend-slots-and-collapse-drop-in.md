# 75 · 周末篮球这一条动线上的两个口子：不认「周末」+ 两张确认条叠在一起

> 施工单原件。**演示前必修**（学生/评委随口一句「周末安排打篮球」就中）。
> 前置：`a696394`。本单只动 planner 的候选时段生成 + 提案收敛，不碰判定层、不碰样本库。

---

## 〇 现场事实（全部现跑）

### 0.1 「周末」被当成噪声擦掉，约束丢了

```
todo_slots_proposal('周末打篮球')    → 周三 / 周四 / 周五   ✗
todo_slots_proposal('周末 打篮球')   → 周三 / 周四 / 周五   ✗
todo_slots_proposal('这周末 打篮球')  → 周三 / 周四 / 周五   ✗
todo_slots_proposal('周六 打篮球')   → 周六 / 周六 / 周六   ✓
todo_slots_proposal('周末')         → None（连卡都不出）
```

根因一行（`planner.py:474`）：

```python
workday_mode = any(w in t for w in ("平时", "工作日"))
```

**只有"不排周末"，没有"只排周末"。**「周末」本身在擦除列表里（`:2988`），被当成时间词擦干净，
`candidate_slots` 于是一路从今天往后找最近空档 —— 今天周三，就给周三四五。

### 0.2 模型路径也没救：它落回同一个函数

学生开了 Agent 模式后，模型确实调了工具（实测轨迹）：

```
🔧 find_free_slots          {'date': '2026-10-31', 'min_minutes': 90}
🔧 find_free_slots          {'date': '2026-11-01', 'min_minutes': 90}
🔧 propose_todo_slots_tool  {'title': '打篮球', 'when': '周末'}
```

但 `propose_todo_slots_tool` 的最后一步是（`planner.py:6532`）：

```python
card = todo_slots_proposal(blob)
```

**绕了一圈落回同一个不认「周末」的函数。** 所以"模型改过卡"是真的，"卡还是错的"也是真的 ——
这不是渲染问题，是数据源同一个洞。

### 0.3 真机截图：两张 `todo_add` 叠在一起

学生原话：「开了 agent，然后修复好了，但是弹窗的问题」。截图上是两张确认条：

```
要不要把这件事排进日程？点【确认加入】我才会写：
  打篮球   2026-10-31（周六）16:00-17:00  约 60 分钟
  [确认加入]
  打篮球   2026-11-01（周日）16:00-17:00  约 60 分钟
  [确认加入]  [不要了]
```

前端（`student.html:1544`）：

```js
const adds = options.filter(o => o.kind === "todo_add");
```

**每条 `todo_add` 各渲染一张卡**，于是叠两张、按钮也各来一份。
模型给周六、周日各开一次 `propose_todo_tool`，options 里就是两条。

### 0.4 附带一个更麻烦的：屏幕两张、系统只认一张

`_remember_todo_options` 最后是 `save_pending(session_id, picks)` —— **覆盖**语义。
所以学生点**第一张**的【确认加入】时，暂存里挂的是**最后一张**，两边对不上。
这一条不单独修也会被 0.3 的收口顺带解决，但验证时必须点名。

---

## 一、修法 A：`candidate_slots` 加 `weekend_mode`

`planner.py:474` 附近，与 `workday_mode` 对称：

```python
workday_mode = any(w in t for w in ("平时", "工作日"))
weekend_mode = any(w in t for w in ("周末", "双休"))
```

`:517` 那条跳过要成对加：

```python
if workday_mode and _iso_weekday(day) >= 6:
    continue                     # 「平时/工作日」不排周末（现状，不许动）
if weekend_mode and _iso_weekday(day) < 6:
    continue                     # 「周末」只排周六周日（新增）
```

### ⚠️ `days_ahead` 必须一起放宽（这是最容易漏的一处）

默认 `days_ahead=5`，今天周三刚好能覆盖到周日；**如果今天是周日**，下一个周末在 6 天后，
整段会被跳过 ⇒ **一张卡都出不来**（学生看到的是"它说凑不出时间"）。

```python
if weekend_mode:
    days_ahead = max(days_ahead, 7)
```

### 不要动的地方

- `span_only_proposal:613` 那条「说了平时/工作日却没报哪天 ⇒ 兜底日落在周末要往后挪」**不动**：
  那是"挪到工作日"，周末方向不需要挪（`weekend_mode` 下挑出来的本来就是周末）。
- `unsupported_period` 收的是「**每个**周末 / 每周末」那类周期句，与本次"这周末安排一次"不是一回事，**不动**。

---

## 二、修法 B：同一轮里同名的多张 `todo_add` 收敛成一张

**不做前端截断**（只渲染最后一张会丢信息，且和"系统只认最后一张"这件事继续错位）。
在后端出口收口：`_remember_todo_options` 之前，把**同 title 且 kind 都是 `todo_add` 且 ≥2 条**的
合并成**一张 `todo_slots`**：

```python
# slots 的形状照抄 todo_slots 卡（前端 TodoSlots 那一支渲染它）
slots = [{"date": o["date"], "weekday": o.get("weekday") or "",
          "start": o["start"], "end": o["end"],
          "minutes": int(o.get("minutes") or 0),
          "label": f"{o['date']}（{o.get('weekday') or ''}）{o['start']}-{o['end']}"}
         for o in same_title_adds]
card = {"kind": "todo_slots", "title": title, "slots": slots,
        "hint": "", "preselect": 0, "repeat": ...,
        "summary": f"{title}｜{len(slots)} 个候选时段"}
```

三条约束：

1. **只收同名**。title 不同的两张（真的同时加两件事）必须各留一张 —— 否则会吃掉一件事。
2. **`repeat` 取第一条非空的**（模型有时在第一张上带 weekly）。
3. 收敛后的卡走 `todo_slots` 那条既有的暂存分支（`_remember_todo_options:1478` 已支持），
   **不用新开一种 kind**。

---

## 三、验证

### 3.1 判据（缺一条都不算过）

| # | 场景 | 断言 |
|---|---|---|
| T1 | `todo_slots_proposal('周末打篮球')` | 全部落在**周六/周日**，且 ≥3 段 |
| T2 | `todo_slots_proposal('周六 打篮球')` | 仍全是**周六**（现状，不许改坏） |
| T3 | `todo_slots_proposal('我平时想去跑步')` | **一段周末都没有**（正控，`workday_mode` 没被改坏） |
| T4 | 一轮里两条同名 `todo_add`（周六 16:00 / 周日 16:00） | `options` 里只剩**一张** `todo_slots`，`slots` = 2 段，暂存里也是这一张 |
| T5 | 一轮里两条**不同名**的 `todo_add` | 仍是两张（证明 §二 第 1 条约束真的在） |

### 3.2 变异（每条都要能变红，打在判据上游）

| # | 变异 | 期望 |
|---|---|---|
| V1 | 删掉 `if weekend_mode and _iso_weekday(day) < 6: continue` | **T1 立刻红**（回到周三四五） |
| V2 | 把 `days_ahead = max(days_ahead, 7)` 删掉，并把 `clock.today()` 打桩成周日 | T1 红（一张卡都出不来） |
| V3 | 收敛时把"同名"条件去掉（改成"全部合并"） | **T5 红**（两件事被合成一张） |

⚠️ V2 需要打桩日期，别用"跑一遍看看"代替 —— 今天周三，不打桩这一条永远是绿的。

### 3.3 回归

- 全量（**本机 >15 分钟，别设 timeout**），与 5176/0 逐位对
- 点名不许掉：`test_35`（周频次）、`test_39`（每周重复）、`test_40`（时间规格）、`test_42`（周期否决）、`test_84`（工具粒度）、`test_90`（补充句）
- 前端实测一次：一张收敛后的 `todo_slots` 卡上，**勾选/【加入日程】/「其他时间」自填**三处都要能用

---

## 四、不做的事

1. **不碰样本库**（学生明确说不想做这一块）。
2. **不在前端截断只渲染最后一张** —— 理由见 §二开头：会丢信息，且和暂存的覆盖语义继续错位。
3. **不改模型的工具选择**（它调 `propose_todo_tool` 两次不算错，由后端收口兜住）。改提示词属另一单，
   而且这类"让模型别这么调"的改法只能降概率，消不掉。
4. **不动 `workday_mode`**（T3 是它的守门人）。

---

## 五、给执行方的三句

1. **§一 的 `days_ahead` 放宽不能省**。只加 `weekend_mode` 不改它，今天能过、周日就空白，
   而这种"某天才坏"的 bug 在演示里最难解释。
2. **V1 和 V3 必须各自能红**。V1 守日期、V3 守"别把两件事合成一件"，两条各管一个方向；
   只做 V1 的话，收敛那段写成"全合并"也能全绿 —— 而学生会发现两件事变成了一张卡。
3. **T4 要连暂存一起断言**（`peek_pending` 里就一张）。只断言 `options` 一张、
   而暂存里还躺着两条，学生点【确认加入】时照样错位。
