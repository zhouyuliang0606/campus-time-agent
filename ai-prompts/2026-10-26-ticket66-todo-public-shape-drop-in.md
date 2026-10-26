# 66 · 待办对外形状缺键：读侧一律兜底，id 缺了不进返回

> 施工单原件。**收口期最后一单**，做完冻代码。
> 前置：`#65` 已落地（`45447a3`），全量 `5078 / 0`（87 批）。

---

## 〇 现场事实（全部现查，行号对 `45447a3`）

### 0.1 那「6 处」的准确坐标

`app/modules/planner.py:1313-1322`：

```python
def list_day_todos(date: str) -> list:
    """列某天已安排的待办（人话：看看这天已经塞了些什么）。"""
    todos = list_todos(date)
    if not todos:
        return [{"info": f"{date}（{_weekday_name(date)}）目前没有安排待办"}]
    return [{
        "id": t["id"],           # ← 1316
        "title": t["title"],     # ← 1317
        "date": t["date"],       # ← 1318
        "start": t["start"],     # ← 1319
        "end": t["end"],         # ← 1320
        "status": t["status"],   # ← 1321
    } for t in todos]
```

### 0.2 同族还有第二处，也硬取同一批键（这一条是本单的主要发现）

`app/modules/planner.py:1374-1382`（`list_todos_range`，#63 新加的那把）：

```python
row = by_id.get(t["id"])                                          # 1374
if row is None:
    row = {"id": t["id"], "title": t["title"], "date": iso,       # 1376 ×2
           "start": t["start"], "end": t["end"], "status": t["status"],   # 1377 ×3
           "dates": [iso]}
    ...
    by_id[t["id"]] = row                                          # 1382
```

**7 处硬取**（`id` 重复用 3 次）。所以：**6 个不同的键，两处实现，合计 13 个硬取点。**

为什么这件事必须先说：`tests/test_84_todo_range_tool_granularity.py:216` 的 B6 那条护栏，判据是「`list_todos_range` 的字段形状与 `list_day_todos` **一致**」。只改前一处、不改后一处，B6 当场变成一句假话 —— 而 #63 立这把 tool 的理由恰恰是「判据只此一份」。

### 0.3 写入侧：形状是保证的，而且只有两个写入口

| 写入口 | 位置 | 造出的键 |
|---|---|---|
| `add_todo` | `store.py:1355` | `id`(uuid4 hex[:8]) / `title` / `date` / `start` / `end` / `note` / `category` / `status`("planned") / `created_at` |
| `add_todos_batch` | `store.py:1409` | 同上 8 个（`status` 同样写死 `"planned"`） |

除这两个之外，`todos.json` 的其它写入都是**改已有记录**（`store.py:1484` / `:1533` 那种 `data["todos"][i] = t`），不产生新形状。

### 0.4 读侧：loader 原样返回，一个键都不补

`store.py:1428 list_todos()` → `_sread("todos.json", …).get("todos", [])` → 只做 `todo_occurs_on` 过滤和排序，**没有 normalize、没有缺键回填**。

### 0.5 `status` 确实是后加字段 ⇒ 老记录缺它是有历史的

`store.py:190`（`append_conversation` 的 `ref` 字段注释）：

> 归档工单时要靠它找到这条镜像一起收起 —— 认字符串…只是给加这个字段之前登进去的老记录兜的，两条都在，**不批量迁移文件（同 #27 那个 `status` 的处理）**。

那句「同 #27 那个 `status` 的处理」意思是：**`status` 也是后来才加的**，加之前落盘的待办记录里没有这个键。

### 0.6 真实数据当前是干净的（所以这一单不是救火单）

```
app/data/student/admin1/todos.json  共 3 条  缺键：无
```

3 条记录，`id/title/date/start/end/status` 一个都不缺。

### 0.7 缺键时现在会发生什么

`t["status"]` → `KeyError` → 冒泡到 FastAPI → **HTTP 500** → 学生端一句「服务出错了」。

而 `list_day_todos` 不是页面渲染，它是**挂在 `build_tools()` 里给模型的工具**（注册在 `planner.py:5940`）。所以它崩掉的实际后果是：模型这一轮的工具调用失败，`_trace` 里落一条报错，而模型很可能拿着这条报错**去编一句答案**。

---

## 一、口径：选 A（读侧一律兜底）

| 选项 | 内容 | 现状 |
|---|---|---|
| A | 读侧一律兜底，缺键按默认值算 | ← **本单采用** |
| B | 写入方保证形状，读侧允许硬取 | 现状 |

### 为什么不是 B（三条，任一条单独成立）

1. **规矩已经成文，这 6 处是唯一例外。** `store.py:56 owner_of_row()` 写着「与 `status` / `archived` 同一做法：**不批量迁移文件**」；`status`(`:190`)、`trail`(`:299`)、`ref`(`:357`)、`purpose`(`:944`) 五处历史字段全按 A 做。留 13 个硬取点按 B，下一个改代码的人不知道该信哪边 —— **规矩和实现打架，比自己偷偷有 bug 更贵**。

2. **B 不是「探针」，它是故障。** 探针要有接收人、要有日志、要说清「这是告警不是崩溃」。这里是本地 JSON 文件，缺键 → 500。**没有人能从一条 500 里读出「你的数据缺了 status」。** 而 B 号称能「当场暴露被手改的数据」，实际暴露的对象只有一个人：正在演示的你。

3. **B 的代价是必然会发生的，A 的代价是可以补的。** 已经写明「将来真导入一次外部待办就会红在屏幕上」；而 A 补掉的是读展示层这个**最不该承担校验的地方**。校验该在写入时做 —— 那件事写侧已经在做了（0.3）。而「数据被手改过」要暴露，正确位置是**启动自检**（见 §五 的另立单），不是让学生端替我们当报警器。

### 一条要提前承认的代价

A 之后，「数据被手改过」这件事**在界面上不再可见**，它会变成一个安静的空值。这是选 A 明码标价的成本，**所以 §五 那条「启动自检另立单」不是可选项、是这笔账的另一半**。

---

## 二、修法：抽一处翻译，两处共用

### 2.1 新增 `_todo_public_row()`

放在 `list_day_todos` 上方（`planner.py:1311` 之前），紧邻调用它的两处：

```python
def _todo_public_row(t: dict, date: str) -> dict:
    """待办记录 → 给模型/前端的对外形状（**这一族只此一处翻译**）。

    为什么兜底而不是硬取（#66）：`status` 是后来才加的字段（`store.py:190`「同 #27 那个
    `status` 的处理」），加它之前落盘的记录没有这个键；`add_todo` / `add_todos_batch`
    两个写入口保证形状，但**手改过的文件不保证**。硬取一个缺掉的键不会「报警」，
    它只会让这一整轮工具调用变成 500 —— 而工具调用失败之后，模型会拿那条报错去编话。

    形状这半句与 `store.owner_of_row` 同一套规矩：老记录没这个键按默认值兜，
    **不批量迁移文件**（每迁移一次，就多一次改坏真实数据的机会）。

    ⚠️ `id` 不在这里兜 —— 缺 id 的记录由调用方拦掉，见 `_todo_has_id`。
    """
    return {
        "id": str(t.get("id") or ""),
        "title": str(t.get("title") or ""),
        "date": str(date or ""),
        "start": str(t.get("start") or ""),
        "end": str(t.get("end") or ""),
        "status": str(t.get("status") or ""),
    }
```

**`date` 由调用方传入，不从记录里取** —— 这不是偷懒，是两处本来就不一样：

| 调用方 | `date` 传什么 | 依据 |
|---|---|---|
| `list_day_todos` | `str(t.get("date") or "")` | 既有行为就是记录自己的日期（锚点）；**改成传参数 `date` 会变行为**，不许顺手改 |
| `list_todos_range` | `iso`（落到的那天） | `test_84` B3 钉死了：「每行的 `date` 是**落到的那天**，不是记录里那个锚点」 |

### 2.2 `id` 缺了不进返回 —— 这是 A 的唯一例外

```python
def _todo_has_id(t: dict) -> bool:
    """这条待办有没有 `id`。缺了就不往外交（#66）。

    为什么不兜成空串：`id` 在界面上是**操作句柄**（勾完成 / 删除 / 改期都按它回传），
    兜成 `""` 之后所有缺 id 的记录会挤到同一个 key 上 —— `list_todos_range` 里
    `by_id[""]` 那一行会把好几件事**并成一条**（`row["dates"].append` 一路加下去），
    前端勾一条会连带勾掉别的。**静默错乱比崩溃贵得多**，所以宁可这一条不出。
    """
    return bool(str(t.get("id") or "").strip())
```

`list_day_todos` 改完：

```python
def list_day_todos(date: str) -> list:
    """列某天已安排的待办（人话：看看这天已经塞了些什么）。"""
    todos = list_todos(date)
    if not todos:
        return [{"info": f"{date}（{_weekday_name(date)}）目前没有安排待办"}]
    rows = [_todo_public_row(t, str(t.get("date") or "")) for t in todos if _todo_has_id(t)]
    dropped = len(todos) - len(rows)
    if dropped:
        # 说出去，不静默：模型看得见才会跟学生解释，而不是回一句「这天没有安排」
        rows.append({"info": f"另有 {dropped} 条待办没有 id，列不出来（数据可能被手改过）"})
    return rows
```

`list_todos_range` 改完（只动 `1374-1382` 那一段）：

```python
for t in todos:
    if not todo_occurs_on(t, iso):
        continue
    if not _todo_has_id(t):
        continue                      # ← 缺 id 直接跳过，别进 by_id[""]
    row = by_id.get(t["id"])
    if row is None:
        row = _todo_public_row(t, iso)
        row["dates"] = [iso]
        rep = str(t.get("repeat") or "")
        if rep and rep != REPEAT_NONE:
            row["repeat"] = rep
        by_id[t["id"]] = row
        order.append(row)
    else:
        row["dates"].append(iso)
```

`list_todos_range` 里**缺 id 的那条不再说话**（不像 `list_day_todos` 加 info 行）：
它的返回可能很长，中间插一句 info 会打断「一条一行」那个契约；而且这条路径是模型一次查一段，
少一条的可见损失由 `list_day_todos` 那边承担。

### 2.3 三个键的默认值是「可见的降级」，不是掩盖

| 键 | 兜底 | 缺了这一条在界面上长什么样 | 有没有风险 |
|---|---|---|---|
| `title` | `""` | 一行只有时间的空标题 | 难看，但看得出是空的 |
| `date` | `""` | 排序沉到最前（`sorted` 里空串最小） | 可见 |
| `start` / `end` | `""` | 显示成 `- 15:30` / `15:30-` | 可见 |
| `status` | `""` | 与 `todo_done_on` 的 `str(t.get("status") or "") == "done"` 天然一致：**缺键 = 没完成（宁可不勾）** | 方向是安全的那一边 |

---

## 三、验证

### 3.1 变异（3 笔，**每一笔都必须打在判据上游**）

施工前先想清楚：这次的判据是「喂一条缺键的记录进去，函数不崩、形状还对」。
所以变异要打在**兜底那一行**上 —— 打在过滤之后（比如改 `rows.append` 那句）就是哑弹，
`rc=0` 但一个断言都不会红。

| # | 变异动作 | 必须红的断言 |
|---|---|---|
| V1 | `_todo_public_row` 里 `"status": str(t.get("status") or "")` 改回 `t["status"]` | 「缺 status 的记录照样能列出来」那条，报 `KeyError` |
| V2 | `list_day_todos` 里去掉 `if _todo_has_id(t)` | 「缺 id 的记录不出现在返回里」那条 |
| V3 | `list_todos_range` 里把 `_todo_public_row(t, iso)` 换回手写 6 键字典 + 删掉 `if not _todo_has_id(t)` | B6「形状与 `list_day_todos` 一致」**和**「缺 id 不并成一行」两条同时红 |

V3 是这次最该跑的一笔：它同时证明「抽函数真的消除了第二份翻译」和「`by_id[""]` 那个坑真的被堵住」。

### 3.2 真机（新 sid，不复用固定 sid）

1. 备份 `app/data/student/admin1/todos.json`，手工删掉某一条的 `"status"`、再删掉另一条的 `"id"`
2. 起 8000，`/student` 登 admin1，问「今天有什么待办」
3. 必须同时满足：
   - 不出现 500、不掉进兜底话术
   - 缺 `status` 那条**照常列出**，且显示为未完成（不显示已勾）
   - 缺 `id` 那条**不在列表里**，且返回里有一句「另有 1 条待办没有 id」
   - 勾另一条的完成 → 只勾掉那一条（证明没有并到 `by_id[""]` 上）
4. 恢复备份

### 3.3 回归

全量 `tests/run_all.py`：基线 **5078 / 0（87 批）**，本单加 3 条护栏后应为 **5081 / 0**。
少一项或多一项失败都要停下来查，不许改基线数字了事。

---

## 四、护栏（新建 `tests/test_86_todo_public_shape.py`）

只写三条，每条都要配一条**非空正控**（期望为空必须同时证明「该有东西的时候有东西」，否则三条一起假绿）：

| 项 | 判据 |
|---|---|
| C1 | 缺 `status` / `start` / `end` / `title` 的记录，`list_day_todos` 照样返回、且这几个键是 `""`；**正控**：同一条补回 `status` 之后返回里那个值就是补回来的值 |
| C2 | 缺 `id` 的记录**不出现**在返回里；**正控**：给它补一个 id 之后立刻出现 |
| C3 | `list_todos_range` 的两条缺 id 记录**不许并成一行**（`len(got)` 等于有 id 的条数）；**正控**：都补上 id 之后条数翻倍 |

> 写护栏时注意本机踩过的坑：`render((case,),)` 那种多套一层括号，会让三条「期望空串」的断言一起假绿 —— 所以 C1/C2/C3 每条都按「缺键一份 + 补全一份」成对写。

---

## 五、不做的事

1. **不批量迁移 `app/data/**/*.json`。** 成文规矩，本单只改读侧。缺键的记录就让它缺着。
2. **不在读侧写 WARN 日志冒充探针。** 真要「数据被手改过当场暴露」，那是**启动自检**：一个 `/api/health/data` 扫一遍四个 loader 的产物、报「哪条记录缺哪些键」。**另立一单**，不进这一单 —— 它是可观测性，不是形状问题，混在一起会让这一单从 40 行变成 150 行。
3. **不动 `list_todos_in_month`**（`store.py:1440`）以及日历那几处。它们不硬取这 6 个键，各有自己的判据，不在本次扫描的 6 处里。
4. **不改 `list_day_todos` 的 `date` 语义。** 现在返回的是记录自己的日期（锚点），`list_todos_range` 返回的是落到的那天。两者不一样是**既有设计**（B3 钉的就是 range 这一头），本单只把它们收进同一个函数，不顺手统一 —— 统一是行为变更，得单独一单、单独真机。
5. **不碰 `add_todo` / `add_todos_batch`。** 写侧形状是对的，这一单一个字都不改。

---

## 六、给执行方的三句话

1. **这一单不是救火。** 真实数据 3 条 0 缺键、全量 5078/0 全绿，**现在不修也不会在演示里炸**。它的价值是把「将来导一次外部数据就红屏」这条路提前堵掉 —— 所以它是收口期的**最后一单，做完就冻代码**，不要再顺手加功能。
2. **6 个键、13 个硬取点、两处实现。** 只改 `list_day_todos` 那 6 行是半成品：`list_todos_range` 里 `by_id[t["id"]]` 会把所有缺 id 的记录并成同一行，前端勾一条连带勾掉别的 —— **那是比崩溃更贵的静默错乱**。
3. **`id` 是兜底的唯一例外。** 其余 5 个键缺了按默认值（可见的降级），`id` 缺了整条不进返回、还要在 `list_day_todos` 里明说一句「另有 N 条没有 id」。判据就这一条，别在别处再解释一遍。

---

### 落地记录（施工方填）

- 笔数 / 哈希：
- 变异实跑结果（V1 / V2 / V3 各红在哪一行）：
- 真机：sid = ____，四问四答原文：
- 全量：____ / ____（____ 批）
