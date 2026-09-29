# 标记完成改为系统接管 + 问答栏待办操作导航（Request R）

> 日期：2026-09-29
> 关联代码 commit：edbc9fd / 545ae7b / 986f1b0 / 2c281af / c76b262 / f02d078
> 关联测试：tests/test_10_mark_done.py（46 项全过）

## 一、用户原话 Prompt（摘录）

> 标记完成改成系统接管（加 3g 确定性分支+确认卡，撤掉模型直调）；
> 删待办在「我的日程」栏端到端实测一遍 + 补回归测试；
> 问答栏说删/改待办时，给个"前往我的日程"按钮而非静默失败

（其中"3g"是"3 个"确定性分支的笔误——对齐删待办已有的 ok/many/empty 三分支。）

## 二、AI 研判

### 1. 为什么"标记完成"必须系统接管
老版本 planner 的工具箱里有一个**真能改状态**的 `update_todo_status` 写工具，
只靠提示层一句"必须先确认"拦着。提示是软约束，学生口语一变模型就可能绕过去——
没点头就把待办置成已完成，学生还以为没办完，复盘时才发现已经"完成"。

结论：照搬删待办的套路，**两件事一起做**——
① 写工具从工具箱撤掉（模型只拿到只读的 `propose_todo_done`，改不了任何状态）；
② 判定与列清单收归系统，三种情况各有走法（规格里的"3 个确定性分支"）：
   · 点名唯一一条 → 出确认条（原样念标题+日期+时段），等学生点头
   · 命中多条/没说清 → **列清单问是哪一条，列完就停**，绝不猜
   · 一条没命中 → 如实说，绝不编一条出来标记

### 2. 三个子任务如何落地
- **子任务 1（系统接管）**：`intent.py` 新增 `INTENT_DONE` 正式意图；`planner.py`
  撤掉 `update_todo_status` 工具、新增 `propose_todo_done` 只读工具 + `resolve_todo_done`
  等三分支判定；`main.py` 新增 3h 确定性分支（**必须排在删待办 3g 之前**，
  否则"周二那条待办标记完成"会被误判成删除）；确认执行块只在学生回"确认"时由系统调
  `update_todo_status(done)`。
- **子任务 2（端到端实测 + 回归）**：`tests/test_10_mark_done.py` 覆盖标记完成三分支、
  问答栏导航、删待办端到端；并跑真实 HTTP 后端（`/api/chat` + `/api/todos`）
  直播验证一遍。
- **子任务 3（问答栏不静默失败）**：`main.py` 在 `_explicit_module == "faq"` 时识别到
  删/改待办意图，返回 `options=[{kind:"goto_schedule",label:"前往我的日程"}]`，
  前端渲染蓝色按钮切到日程栏；既不静默失败，也不真删。

### 3. 自踩自修：`_pick_todo_name` 残词
`resolve_todo_done("标记完成 2026-10-03 的待办")` 一度返回 `empty` 且 `name="标记完成"`——
因为"标记完成"这组**动作词**没被剥离，被当成了待办名。补 `_DONE_TODO_INTENT +
_DONE_TODO_VERB + ("标记","完成")` 进剥离表后，正确返回 `many` 候选。

## 三、具体改动

| 文件 | 改动 |
|---|---|
| `app/agent/intent.py` | 新增 `INTENT_DONE="done_todo"` 常量 + `KNOWN_INTENTS` 白名单 + `INTENT_MODULE` 映射 planner；系统提示补说明；加种子样本 |
| `app/modules/planner.py` | 撤 `update_todo_status` 工具，加只读 `propose_todo_done`；新增 `wants_done_todo`/`resolve_todo_done`/`_todo_done_proposal`/`parse_done_todo`/`render_todo_done_list`/`resolve_todo_done_reply`；修 `_pick_todo_name` 残词剥离 |
| `app/main.py` | 新增 3h 确定性标记完成分支（ok/many/empty）；标记完成确认执行块；问答栏（`faq`）删/改待办改返回 `goto_schedule` 选项 |
| `app/static/confirm.html` | `KIND` 字典加 `todo_done` 条目（✅ 标记为完成），apply 走 `PUT /api/todos/{tid}` 带 `{status:"done"}` |
| `app/static/student.html` | 我的日程渲染 `todo_done` 卡（单条弹确认页/多条逐张按钮）+ 问答栏 `goto_schedule` 蓝色"前往我的日程"按钮 |
| `tests/test_10_mark_done.py`（新增） | A 标记完成单元 / B 工具箱 / C 标记完成端到端 / D 问答栏导航 / E 删待办回归，共 46 项 |

## 四、验证

### 1. 单元测试（tests/run_all.py）
- `test_10_mark_done.py`：**通过 46 项，失败 0 项**（A–E 五批）。
- 全量套件：**通过 717 项，失败 1 项**（`test_06_add_things.py` 两处断言，
  属日期相对导致的演示数据污染，**早于本次改动、与 Request R 无关**，已单独记录）。

### 2. 直播端到端（真实 HTTP 后端，14/14 通过）
对运行中的 `http://127.0.0.1:8000` 走真实接口，用唯一标题测试待办、跑完即清理：
- ✅ 我的日程栏"把 X 标记完成" → 出 `todo_done` 确认条，点名正确待办
- ✅ 确认前状态仍为 `planned`（系统没提前动库）
- ✅ 回"确认"后状态变 `done`，文案含"已标记为完成"
- ✅ 我的日程栏"删掉 X" → 出 `todo_remove` 确认条，确认前还在
- ✅ 回"确认"后待办被删除，文案含"已删除"
- ✅ 问答栏"帮我删掉 X 这条待办" → 返回 `goto_schedule`，**未真删**
- ✅ 普通问答（"图书馆几点关门"）不弹"前往我的日程"

## 五、遗留 / 备注
- 全量套件里 `test_06_add_things.py` 的 2 个失败是前置的日期污染问题
  （计算出的 `d_near` 与演示种子待办撞日 + 硬编码"周二=2026-09-29"在某些运行日失真），
  **非本次改动引入**。本次的 `test_10` 已独立、完整地覆盖删待办回归，可作为该路径的护栏。
  建议另行排期修 `test_06` 的日期相对化（不在本次 scope）。
