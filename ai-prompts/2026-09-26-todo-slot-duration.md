# Prompt 快照：候选时段按学生说的时长排（说「三个小时」就给 3 小时）

- 日期：2026-09-26
- 关联代码提交：`3752064`（承接 `594952c` 第七轮「多段候选卡」）
- 模块：学生端「加待办」确定性链路 · 时间安排子环节

---

## 一、用户原话 Prompt（摘录）

> 我要的三个小时，两次拉出来都是 90 分钟

背景：上一轮（`594952c`）已把「加待办没说时间」从"系统替他敲定单条"改成"摊开多个候选时段让他挑"。
但候选卡上每一段仍是固定的 90 分钟（`AUTO_SLOT_LENGTH`），学生明确说了「三个小时」却拿到 90 分钟，
等于"听了他的事名，没听他的时长"。

---

## 二、AI 研判

1. **定位时长在哪被丢掉**：单条 AI 敲定那条路（`plan_todo_slot`）本来就认时长——
   `want = minutes or wanted_minutes(t) or AUTO_SLOT_LENGTH`，并把 `length=want` 传给 `pick_free_slot`。
   问题出在**候选卡那条路**：`candidate_slots` → `_day_slots` 里，空档筛选写死
   `find_free_slots(date, min_minutes=AUTO_SLOT_MINUTES)`（60）、段长度写死 `begin + AUTO_SLOT_LENGTH`（90），
   `prefer` 里带着的"三个小时"根本没被读。
2. **修法**：把 `wanted_minutes(t)` 解析出的时长一路传下去。
   - `_day_slots` 新增 `length` 参数；`min_needed = length`，段长度也用 `length`。
     空档不够长就直接跳过——**绝不硬凑**（这是本项目一贯红线）。
   - `candidate_slots` 里 `want = wanted_minutes(t) or AUTO_SLOT_LENGTH`，
     上下限对齐 `plan_todo_slot`（5 分钟 ~ 8 小时），再传给两处 `_day_slots` 调用。
3. **不碰的东西**：写库仍只走 `/api/todos/batch`，AI 仍只出提案，落库仍要学生勾完点头。

---

## 三、具体改动

| 文件 | 改动 |
|---|---|
| `app/modules/planner.py` `_day_slots` | 新增 `length: int = AUTO_SLOT_LENGTH`；`min_needed = length`，`find_free_slots(min_minutes=min_needed)`，`finish = min(begin + length, ...)`，过滤条件改为 `< min_needed`。 |
| `app/modules/planner.py` `candidate_slots` | 新增 `want = wanted_minutes(t) or AUTO_SLOT_LENGTH`（夹到 5~480 分钟）；两处 `_day_slots(...)` 调用都带上 `length=want`。 |
| `tests/test_06_add_things.py` | 见下方「测试同步」。 |

改动核心（节选，planner.py）：

```python
# candidate_slots
want = wanted_minutes(t) or AUTO_SLOT_LENGTH      # "大概三小时" → 180
want = max(5, min(int(want), 8 * 60))
...
_day_slots(picked_day, prefer, limit=max_slots, length=want)
_day_slots(day, prefer, limit=1, rotate=off, length=want)

# _day_slots
min_needed = length
free = [s for s in find_free_slots(date, min_minutes=min_needed) if s.get("start")]
...
finish = min(begin + length, to_minutes(s["end"]), phi)
if finish - begin < min_needed:
    continue
```

---

## 四、验证

冒烟（后台服务 `EVmzm3`，重启后生效）：

| 输入 | 结果 |
|---|---|
| 「帮我安排出去玩三个小时」 | `todo_slots` 卡，**4 段全是 180 分钟**：周六 9/26 14:00-17:00、周日 9/27 19:00-22:00、周一 9/28 19:00-22:00、周二 9/29 14:00-17:00 ✅ |
| 「帮我安排游泳」（没说时长） | 仍按默认 90 分钟出 4 段候选，未受影响 ✅ |

全量回归：`tests/run_all.py` → **904/904 通过，失败 0 项（共 7 批）**。

---

## 五、测试同步（这一轮最花时间的地方）

上一轮 `594952c` 把主路径改成多段候选卡后，`test_06_add_things.py` 里一批断言仍停在
"直接敲定单条（todo_add + auto）"的旧规格，本次一并更新：

1. **形态断言**：「只说了哪天」「连哪天都没说」等 → 断言 `kind == "todo_slots"`，
   并额外校验"候选都落在他说的那天""每一段都真的空着"。
2. **落库方式**：候选卡回一句「确认」**不该**写库——系统不知道他要哪一段，替他挑就越权。
   测试改为模拟"勾第一段 + `POST /api/todos/batch`"。
3. **「没写库」类断言从绝对值改成前后快照对比**：演示待办里本来就有
   「游泳 2026-09-29 19:00-20:30」，写 `not any(title=="游泳")` 必然红（实测复现）。
4. **删待办场景的顺序依赖**：⑤ 真删掉了「游泳」，⑥ 却要求那天仍有两条 → ⑥ 前补回；
   ⑧「确认落空捞回原话」改用库里唯一的「瑜伽」，避免命中多条。

---

## 六、与比赛交付的对应

- 本快照对应提交 `3752064`，与代码分离提交，符合「细粒度 Git 提交历史 + 提交 AI 对话 Prompt 快照」。
- 红线未破：AI 只出提案，落库始终由学生勾选确认触发；排不进就如实说，不硬凑时间。
