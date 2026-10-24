# 59｜刷新后删待办卡变乱码：暂存里的「候选清单」被当成卡取出来了（drop-in）

> 现场：2026-10-24，截图实测。学生回「删待办」→ 系统列清单 → 回「1」→ **已生成删除待办提案** →
> **刷新页面** → 卡片变成乱码，点「确认所选」发出 `确认 undefined undefined undefined 待办` →
> 后端回「删待办：没找到符合条件的」。
> 先说一句结论：**库大概率没被删**，因为这句话走的是"没找到"分支；页面上显示还在，是真实的。

## 〇、现场事实（全链路，每一环都有行号）

| # | 位置 | 事实 |
|---|---|---|
| 1 | `main.py:3815` / `:3853` | 命中多条时，暂存里只放**候选清单**：`[{"kind":"todo_remove_candidates","todos":[...]}]`，回「删待办缺细节」 |
| 2 | `main.py:3723` | 学生点名后：`pending_out = [proposal]`；**手上有清单时追加** `→ [proposal, {"kind":"todo_remove_candidates", "todos": ..., "summary":"待删候选"}]` —— **辅助项排在最后** |
| 3 | `main.py:3747` | 但同一轮的**响应**只回 `"options": [proposal]` —— 所以**刷新前一切正常**（前端只拿到那一张真卡） |
| 4 | `main.py:6157` | `/api/pending/{sid}` 取 `opts[-1]` → 取到的正是**最后那个辅助清单**，不是提案 |
| 5 | `student.html` | `grep todo_remove_candidates app/static/*.html` = **0 命中**：前端没有这个 kind 的任何分支 |
| 6 | `student.html:1909-1934` | 所有分支都没接住 → 掉进最后的「候选时间段」兜底循环，它按 `{date,start,end,title}` 拼 → 全 `undefined`，`title` 兜底成「待办」 |
| 7 | `main.py` 删待办分支 | 收到 `确认 undefined ...` → `is_confirmation` 认它是点头 → 但匹配不到任何待办 → 「删待办：没找到符合条件的」 |

### 为什么"只有刷新后才坏"

- **刷新前**：卡来自**响应**的 `options`（只有 `[proposal]`）→ 走 `student.html:1624` 的 `todo_remove` 分支 → `openConfirm()` 正常。
- **刷新后**：卡来自**暂存**的 `opts[-1]`（辅助清单）→ 没有任何分支接它 → 掉进兜底循环 → 乱码。

> 本质是**响应与暂存分叉**：同一轮对话，响应里过滤成"只有卡"，暂存里多塞了一个"不是卡"的辅助项，
> 而恢复接口用「最后一个」这个位置约定去取卡 —— 位置约定一旦被辅助项破坏，取到的就是错的东西。

## 一、A（主修，后端）：恢复接口不许把辅助项当成卡

`main.py` 的 `pending_get`（`:6146-6157`）：

```python
    opts = [o for o in (entry.get("options") or [])
            if isinstance(o, dict) and not o.get("applied")]
    if not opts:
        return {"pending": None}
    return {"pending": opts[-1], "session_id": sid}
```

改成：

```python
    opts = [o for o in (entry.get("options") or [])
            if isinstance(o, dict) and not o.get("applied")]
    # ⚠️ #59：暂存里可能同时挂着**不是卡**的辅助项 —— `todo_remove_candidates`
    #    是"换一条 / 第二条"要用的候选清单，不是给学生点的卡。
    #    恢复接口以前直接取 opts[-1]，而 pending_out 的顺序恰好是 [提案, 候选清单]，
    #    刷新后取到的就是那张辅助清单 → 前端没有它的分支 → 掉进候选时段渲染器 → 乱码。
    #    ⚠️ 辅助项**必须留在暂存里**（学生回"换一条"还要用），只是不许被选中当卡。
    _NON_CARD_KINDS = {"todo_remove_candidates"}
    cards = [o for o in opts if o.get("kind") not in _NON_CARD_KINDS]
    if not cards:
        return {"pending": None}
    return {"pending": cards[-1], "session_id": sid}
```

> 为什么用"排除法"而不是"白名单"：白名单会在以后新增 kind 时**静默漏卡**（加了一种卡、恢复接口不认，刷新就没了）。
> 排除法只针对**明确已知的辅助项**，新增真卡不受影响。

## 二、B（同修，前端）：兜底循环不许吞未知 kind

`student.html:1909` 那个循环现在会把**任何**没被上面分支接住的 option 都当"候选时间段"画。这是这单乱码的直接制造者——**以后每加一种 kind，只要前端漏了分支，就必然变成一张三处 `undefined` 的卡**。

改法（两处，都在 `renderOptions` 里）：

```js
      const cards = [];
      options.forEach(o => {
        // ⚠️ #59：带 kind 却没被上面任何分支接住的，**不许**当候选时段画。
        //    兜底循环只负责"没有 kind 的老数据"，未知 kind 走下面那句人话。
        if (o && o.kind) return;
        ...
      });

      if (!cards.length) {
        const miss = document.createElement("div");
        miss.className = "opt-hint";
        miss.textContent = "⚠️ 上一轮那张卡没能在这一页还原出来——请把刚才那句话再说一遍，我重新出卡。";
        wrap.appendChild(miss);
        container.appendChild(wrap);
        return;                      // 不再画那个「✅ 确认所选」按钮
      }
```

## 三、C（可选，但建议）：让"是不是卡"变成显式字段，别靠位置

位置约定（`opts[-1]`）本身就是这单的根。建议给辅助项打标：

```python
{"kind": "todo_remove_candidates", "card": False, "todos": ..., "summary": "待删候选"}
```

恢复接口优先判 `card is not False`。这样以后再有辅助项，**加的人一眼就知道要标**，不依赖"记得把它放前面"。
（不在本单必做；A 的排除法已经能止住当前这处。）

## 四、验证

### 4.1 复现（改前必须红）

1. 学生端说「删待办」→ 回「删待办缺细节」；
2. 回「1」→ 出「🗑️ 要删的是这一条：…」+ 确认条；
3. **刷新页面** → 改前：出现一张写着「待办」、`undefined` 的勾选卡；
4. 勾上点「确认所选」→ 聊天里出现 `确认 undefined undefined undefined 待办` → 回「删待办：没找到符合条件的」。

### 4.2 改后必须满足

- 刷新后**仍然出现确认条**（不是乱码卡），文案里**一个 `undefined` 都不许有**；
- 点【确认删除】→ 真删 + 日程面板当场刷新（`loadScheduleView()`）；
- 刷新后再回「换一条」→ 仍能接住（证明 `todo_remove_candidates` **还在**暂存里，只是没被当卡）。

### 4.3 变异（改坏必须红）

- 把 A 的 `_NON_CARD_KINDS` 去掉 → 复现步骤第 3 步回到乱码卡 → 应红；
- 把 B 的 `if (o.kind) return;` 去掉 → 任何未知 kind 都能画出乱码卡 → 应红；
- 顺手把辅助项从暂存里**删掉** → "换一条 / 第二条"接不住 → 应红（这是最容易"修过头"的一处）。

## 五、不做的事

1. **不把 `todo_remove_candidates` 从暂存里删掉**——它是"换一条/第二条"的唯一依据，删了等于砍掉一个出口；
2. 不动 `pending` 的身份键（`<口径>|<session_id>`）与 TTL；
3. 不动确认条 iframe（`/confirm`）那条路——它一直是好的；
4. 不顺手改 `save_pending` 的"整份覆盖"语义（一轮一卡是刻意的）。

## 六、给执行方的三句

1. 这单是**两处小改**（`pending_get` 取卡 + `renderOptions` 兜底），别扩大战场。
2. 辅助项要**留着但不能当卡**——"排除"不是"删除"，这两件事差一个字就修反了。
3. 验收就三步：刷新后无 `undefined`、确认能真删、刷新后"换一条"仍接得住。
