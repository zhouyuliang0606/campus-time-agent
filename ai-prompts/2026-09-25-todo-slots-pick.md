# Prompt 快照：加待办「时间安排」改为多种合理时间弹窗挑选

- 日期：2026-09-25
- 关联代码提交：`6deb74e`
- 模块：学生端「加待办」确定性链路 · 时间安排子环节

---

## 一、用户原话 Prompt（摘录）

> 可以，现在增加减少已经可以做到了，现在就是在时间的安排上，将多种合理时间提出让我挑选，也是弹窗里进行选择

背景：本轮之前「加待办没说时间」走的是**第六轮**逻辑——系统直接替学生敲定唯一一个时段
（`todo_add` 单条确认条），把决定权又揽回自己手里。学生明确要的是**从多个合理时段里自己挑**，
而不是被系统定死。

---

## 二、AI 研判

1. **现象**：学生说「帮我加个健身」（没说几点）时，旧链路先出 `todo_mode` 二选一卡，再强制走
   `mode_to_card(...,"ai")` → `plan_todo_slot` 出**单条**确认条。只给一个点 = 替他做了主，
   与「让我挑选」诉求冲突。
2. **已有能力可复用**：`todo_slots_proposal` / `build_todo_slots` / `mode_to_card(...,"self")`
   早已能产出 `kind="todo_slots"` 的**多段候选卡**，前端（student.html ①-quater 块）也早已支持
   在确认条里打勾 + 「其他时间」自填，点【加入日程】走 `/api/todos/batch` 写库。
   也就是说——**不需要新增架构，只需把主路径的优先级翻转**。
3. **铁律不变**：这里只出提案，一个字节都不写库；学生勾完点按钮才落库；系统即使想兜底也只能
   在「连一个空档都排不出」时退回单条 AI 敲定，且仍需学生确认。
4. **回归风险点**：说准点（「帮我加个游泳 周四 19:00-20:00」）的那条路在 3c 分支里走
   `parse_add_todo` 提前 return，不受影响；改动只动「没说时间」的分支，需冒烟确认单条路径没坏。

---

## 三、具体改动

| 文件 | 改动 |
|---|---|
| `app/main.py`（3c 分支，原 1524–1544 行） | 主路径由 `mode_to_card(mode_card,"ai")` 改为先试 `mode_to_card(mode_card,"self")`；仅当候选卡为 None（那几天全满）才退回 `mode_to_card(mode_card,"ai")` 单条兜底。回话文案改为「给你找了几个空着的时间段，挑方便的勾上（可勾多个）」。 |
| `app/main.py`（3c 分支上方演进注释） | 新增「第七轮改版」段落，记录本轮为何从单条敲定退回多段候选，避免后人误读把注释当现状。 |
| `app/modules/planner.py` `mode_to_card` | self 路径 `todo_slots_proposal(text)` 的 `max_slots` 由默认 3 提到 4，挑选余量更大。 |

改动核心（节选，main.py）：

```python
mode_card = todo_mode_proposal(blob)
if mode_card is not None:
    # 第七轮：先摊开多个候选时段让学生自己挑
    slots = mode_to_card(mode_card, "self")
    if slots is not None:
        return _offer_response(
            session_id, slots, message,
            _slots_answer(slots, lead="⏰ 给你找了几个空着的时间段，挑方便的勾上（可勾多个）：\n\n"),
            "🙋 学生没定时间 → 摊出多个候选时段（让他打勾挑选，本轮规格）")
    # 连一个空档都排不出 → 退回系统单条 AI 敲定兜底
    picked = mode_to_card(mode_card, "ai")
    if picked is not None:
        return _offer_response(...)
```

---

## 四、验证（TestClient 冒烟，隔离数据目录 `CAMPUSTIME_DATA_DIR`）

| 场景 | 输入 | 期望 | 结果 |
|---|---|---|---|
| 1 多段候选 | 「帮我加个健身」 | options 含 `todo_slots` 且段数 ≥2 | ✅ 出 `todo_slots` 卡，**4 段**：周六 9/26 09:00-10:30、周日 9/27 19:00-20:30、周一 9/28 14:00-15:30、周二 9/29 09:00-10:30 |
| 2 勾选入库 | 勾第一时段 → POST `/api/todos/batch` | 写库成功 | ✅ `ok=True`，库里待办 1 条（标题=健身） |
| 3 无回归 | 「帮我加个游泳 周四 19:00-20:00」 | 仍出单条 `todo_add` | ✅ options 含 `todo_add`，未被多段逻辑污染 |

结论：学生端加待办「时间安排」环节已满足「将多种合理时间提出让我挑选，弹窗里进行选择」，
且说准点的旧路径保持单条确认不退化。

---

## 五、与比赛交付的对应

- 本快照对应提交 `6deb74e`，与代码分离提交，符合「细粒度 Git 提交历史 + 提交 AI 对话 Prompt 快照」。
- 端到端写库护栏（`/api/todos/batch` + `mark_applied`）在前序提交已落地，本轮仅调整提案生成策略，
  不触碰写权限边界（AI 仍只出提案，落库始终由学生确认触发）。
