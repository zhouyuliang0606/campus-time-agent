# 2026-10-12 · 软取消应优先作废**未点头的待办提案**，而不是进删除列清单

## 一、现象（用户截图）

1. 学生说 `明天去吃自助` → 系统正确出 `todo_slots` 候选时段卡，标题为 **吃自助**。  
2. 学生接着说 `算了不吃了` → 系统把它听成 **办事（删待办）**，然后列出日程里 10 条已有待办，问“是下面哪一条”。  

期望行为：`算了不吃了` 应取消**刚才那张还没点头的候选时段卡**，日程原封不动，系统回一句“好，吃自助先不加了”。

## 二、根因定位

这不是 `_REMOVE_TODO_SOFT` 加错了——`不吃了` 确实该被识别成删除/撤销意图（规则层已经接住）。  
问题是**执行层**没区分两种场景：

- **A. 取消一张尚未点头的提案**（todo_slots / todo_add / todo_mode）：库里还没这条，不需要“删待办”。  
- **B. 删除库里已存在的待办**：需要走 `resolve_todo_remove` → 确认条 → `delete_todo`。

当前代码在 `app/main.py` 的 remove-intent 分支（约 L2908 起）直接走 B，没先看暂存里是不是有一张**加待办类的活跃提案**。

相关位置：

- `app/main.py` `_pending_todo_slots()` L655、`todo_title_of()` 等工具。  
- `app/main.py` L2908 起 `if rm_intent or asking_rm_todo or answers_held:` 这段是执行入口。  
- `app/modules/planner.py` `_REMOVE_TODO_SOFT` L3586（本次不删不改，保留）。  
- `app/modules/planner.py` `todo_slots_proposal()` L632 返回的卡片结构：
  ```python
  {"kind": "todo_slots", "title": "吃自助", "slots": [...], ...}
  ```

## 三、修复策略

在 `main.py` 的 remove-intent 分支**最开头**加一道拦截：

> 如果学生说的是**软取消/盲删**（没点名、没指定日期、没指定“那条/那个”），  
> 并且暂存里正挂着一张**待确认的加待办提案**（todo_slots / todo_add / todo_mode），  
> 则直接清掉这张提案并友好回复，**不要进删除流程**。

### 3.1 拦截条件（必须同时满足）

1. 当前会话 `session_id` 的暂存里存在以下 kind 之一的活跃提案（`applied` 不为 true）：
   - `todo_slots`（候选时段卡）
   - `todo_add`（已定时、等你点头的确认条）
   - `todo_mode`（二选一卡：AI 帮我挑 / 我自己定）
2. 学生消息属于软取消/盲删：
   - `wants_remove_todo_loose(message)` 为真（含 `_REMOVE_TODO_SOFT` 词），或
   - 消息等于/包含 `取消`、`算了`、`不要`、`不用` 等通用取消词，且
   - `_is_blind_delete(message)` 为真（即：没有 `_pick_date`、没有 `_wants_colliding`、没有明确指向另一条待办的指代词/标题）。
3. （可选但推荐）消息里没有明确点名**另一条**待办。若消息里出现了当前提案的 `title`，优先判定为取消当前提案。

### 3.2 新增辅助函数建议

在 `app/main.py` 增加一个私有函数：

```python
def _active_add_proposal(session_id: str) -> dict | None:
    """返回暂存里那张还没点头的"加待办"提案（todo_slots / todo_add / todo_mode），没有则返回 None。"""
    entry = peek_pending(session_id) or {}
    for kind in ("todo_slots", "todo_add", "todo_mode"):
        for o in entry.get("options") or []:
            if isinstance(o, dict) and o.get("kind") == kind and not o.get("applied"):
                return o
    return None


def _looks_like_cancel_active_proposal(message: str, proposal: dict) -> bool:
    """学生这句话是不是在取消当前挂着的加待办提案。"""
    t = (message or "").strip()
    if not t:
        return False

    # 1) 必须首先是一条"撤销/删除"意图（含软词或通用取消词）
    from app.modules.planner import wants_remove_todo_loose
    soft_cancel_words = ("取消", "算了", "不要", "不用")
    if not (wants_remove_todo_loose(t) or any(w in t for w in soft_cancel_words)):
        return False

    # 2) 必须是"盲删"：没点名、没指定日期、没指定冲突筛选
    #    否则「把昨天那个删了」应走正常删除流程，不应取消当前提案。
    if not _is_blind_delete(t):
        return False

    # 3) 如果消息里明确出现了当前提案的 title，直接判定为取消它
    title = (proposal.get("title") or "").strip()
    if title and title in t:
        return True

    # 4) 纯通用软取消词（如「算了」「不要了」「取消」）也取消当前提案
    #    因为这种情况下没有别的明确目标
    return True
```

### 3.3 插入位置

在 `app/main.py` remove-intent 分支入口（约 L2908）之后、进入 `held_now = ...` 之前插入：

```python
    # ★ 软取消优先取消尚未点头的加待办提案，而不是去日程里删已有待办。
    #    场景：学生刚看到「吃自助」候选时段卡，回一句「算了不吃了」——
    #    他要撤的是这张还没点头的卡，不是去日程里找一条叫「吃自助」的删掉。
    #    必须先拦截，否则会列出所有已有待办问「是下面哪一条」。
    active_add = _active_add_proposal(session_id)
    if (active_add
            and _looks_like_cancel_active_proposal(message, active_add)):
        clear_pending(session_id)
        title = active_add.get("title") or "这件事"
        summary = active_add.get("summary") or title
        append_conversation(session_id, "user", message)
        append_conversation(session_id, "assistant", f"已取消待办提案：{summary}")
        return {
            "module": "planner",
            "session_id": session_id,
            "answer": (
                f"好，**{title}** 先不加了。"
                "如果你改变主意，随时再说一声就行 😊"
            ),
            "trace": [{"step": 1,
                       "phase": "🗑️ 学生取消尚未点头的待办提案，不写库也不删库",
                       "answer": summary}],
            "options": [],
            "awaiting_choice": False,
        }
```

### 3.4 边界行为

| 场景 | 期望结果 |
|---|---|
| 候选卡「吃自助」挂着，学生说 `算了不吃了` | 取消候选卡，日程不变，回“好，吃自助先不加了” |
| 确认条「游泳｜周三 19:00-20:00」挂着，学生说 `不要了` | 取消确认条，不写库 |
| 二选一卡「时间怎么定」挂着，学生说 `算了` | 取消二选一卡 |
| 没有活跃提案，学生说 `算了不吃了` | 按原流程走（可能列清单或闲聊，不变） |
| 候选卡「吃自助」挂着，学生说 `把昨天那个删了` | 不走取消，走正常删除流程（`_is_blind_delete` 为 false，因有日期/指代） |
| 候选卡「吃自助」挂着，学生说 `我要取消健身` | 若消息里 title 不是当前提案 title，仍走正常删除流程 |

## 四、测试要求

1. **在 `tests/test_15_oral_samples.py` 追加 K 批**（或新建 `test_16_active_proposal_cancel.py`）：
   - 判定层：`wants_remove_todo_loose("算了不吃了")` 仍为 True（不改规则层）。
   - 端到端：mock `peek_pending` 返回 `todo_slots` 卡（标题「吃自助」），输入「算了不吃了」，期望返回 `answer` 含「先不加了」、`options` 为空、暂存被 `clear_pending`。
   - 端到端：没有活跃提案时，「算了不吃了」仍走删除流程（与现有行为一致）。
   - 端到端：活跃提案标题为「游泳」，输入「把昨天那个删了」，期望**不**取消提案，仍走 `resolve_todo_remove`。

2. **变异测试**：
   - 摘掉 `_looks_like_cancel_active_proposal` 中 `_is_blind_delete` 检查 → 应红（证明不能误伤明确点名删除）。
   - 摘掉 `todo_mode`  kind → 应红（证明二选一卡也被覆盖）。

3. **全量回归**：跑 `test_15_oral_samples.py` 全部批次 + `test_todo_crud.py` 相关用例，确认红项数/构成与 HEAD 一致。

4. **真机复验**（沙箱）：
   - 步骤 1：`明天去吃自助` → 出现候选时段卡。
   - 步骤 2：`算了不吃了` → 回复“好，吃自助先不加了”，且没有列出任何已有待办。
   - 步骤 3：学生再说 `帮我加个明天19:00-20:00吃自助餐` → 正常出卡或写入。

## 五、纪律提醒

- **只改执行层，不要改判定层**：`_REMOVE_TODO_SOFT` / `wants_remove_todo_loose` 本轮不动，保持「规则层已经认出这是删除/撤销意图」的事实。  
- **判定/执行分离**：模型不出删除操作；取消提案也只是清暂存、不写库。  
- **整族扫**：改 `main.py` 后，检查 `wants_remove_todo`、`wants_remove_todo_loose`、`_is_blind_delete`、`_BLIND_DELETE_HINTS`、`todo_title_of` 是否还有同类场景漏判。  
- **注释例子须实测**：把截图里的 `明天去吃自助` / `算了不吃了` 作为注释里的实测用例写进代码。  
- **不要默认删除**：取消提案时只清暂存，**绝不**调用 `delete_todo`；只有学生确认删除已有待办时才写库。

## 六、后续可扩展（本轮不做，先记着）

- 当暂存里挂着 `todo_remove` / `todo_remove_batch` / `todo_done` / `todo_done_batch` 确认条时，学生回 `取消`/`算了`，应取消那张确认条（而不是去日程里删/标完成）。这属于同一类“取消当前活跃提案”，但作用对象不同，本轮先只覆盖加待办类提案。
