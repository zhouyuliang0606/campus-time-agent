# 2026-10-07 快递/外卖丢失破损 → 出「确认上报」卡

## 用户 Prompt（原话摘录）

> 上报管理员，我说快递丢了，被判定为问事，后续都是问事，没有出卡，也没有上传

## AI 研判

真机 `/api/chat` 复现「快递丢了」：

- `module` 被路由到 `express`，但语义层把这句判成 `intent=chat`（`/classify` 实测 `is_question=false, intent=chat, confidence=0.75`），轨迹第一档写的是「🧠 我把这句听成 **问事**」；
- express 模块的「上报工单」只活在 **系统提示词**里（让模型先问"是否上报管理端"），**没有确定性出卡环节**；
- 结果：模型回一长段查件话术（列出 4 条快递、问"是上面哪一件没找到"），**从没出卡**，学生要上报却找不到入口，更不会真去写工单——与报障「没有出卡，也没有上传」逐字吻合。

根因不是判定层没接到 express 模块，而是 **express 模块里"上报工单"这件事没有出卡通道**：它指望模型自觉问一句"是否上报"，但模型把"快递丢了"当闲聊查件，链路在"问事"就断了。

修法与上一轮「课表导入第一句话出卡」是**同一套硬闸门模式**（规则层硬信号压过语义层弱信号、分支排在所有待办分支之前、卡是系统写的提案而非模型写库）。

## 改动（三处代码 + 一处测试，分开提交）

**1. `app/modules/planner.py`** — 新增 `looks_like_work_order_report(message)`
- 正则 `_WORK_ORDER_RE` 刻意放宽：命中「快递/包裹/外卖/取件码 + 丢失/破损/异常/没收到」或「上报/工单/报修」即视为上报请求；
- `_work_order_kind()` 从原话估问题类型（`包裹破损` / `取件码异常` / `外卖丢失` / 默认 `疑似丢件`），供卡上与工单 `kind` 使用；
- 普通查件（「我的快递到哪了」「取件码多少」）不含这些词 → 返回 `None`，照旧走 express 模块查件，不受此分支影响。

**2. `app/main.py`** — 插一条确定性分支（在 timetable 硬闸门之后、所有确认/待办分支之前）
- `looks_like_work_order_report(message)` 命中 → 直接出 `work_order_report` 卡（`save_pending` 暂存 + `options` 返回），`meta["read"]` 写「办事（上报工单）｜依据：规则层硬信号」，**不再写问事**；
- 回话换成一句"要不要上报管理端，点确认条上的【确认上报】"，不再念一长段查件清单；
- 卡是可取消的提案：点【确认上报】才写工单（走 `/api/workorders`，系统写库，守住"写库只归系统"红线），点【取消】零污染。
- `/api/workorders` POST 增加 `session_id` 入参：聊天来源（`source="chat"`）上报成功后 `clear_pending(sid)`，把那条确认卡从暂存清掉，避免它一直挂在 `/api/pending` 反复出现。

**3. `app/static/confirm.html`** — KIND 表新增 `work_order_report` 卡
- 标题「上报管理端」、按钮「确认上报」；渲染含**可选补充描述**文本框（`#cfWoDesc`）；
- `apply()` POST `/api/workorders`，带 `kind`/`desc`/`source:"chat"`/`session_id`；`doneText` 回显工单编号与类型。

**4. `app/static/student.html`** — `renderOptions` 接住 `work_order_report`
- 单张 → `openConfirm`（走确认条，与 timetable_change 同路）；多张 → 列卡片让学生挑。

**5. `tests/test_27_express_lost_report_card.py`**（新增，29 项 / 4 批）
- 探测器单测：8 句上报短语命中、3 句普通查件不命中；
- 主链路：假引擎沉默时「快递丢了 / 外卖遗失 / 上报管理员」第一句话就出 `work_order_report` 卡、轨迹写「办事」、未点确认工单数为 0；普通查件「我的快递到哪了」不出上报卡；
- 点卡流程：POST `/api/workorders`（带 session）创建工单（`source=chat`、带补充描述）、`/api/pending` 变 null；
- 前端源码断言：confirm.html 有该卡定义且 apply 走 `/api/workorders`、student.html 接住并走确认条。

## 验证

- 真机（重启后端后）`/api/chat`「快递丢了」→ `options=[{kind:work_order_report, wo_kind:疑似丢件}]`，`awaiting_choice=True`，轨迹首档「办事（上报工单）」；
- 模拟点卡 POST `/api/workorders` → 工单 `id`/`source=chat`/`status=待处理` 落库，`/api/pending` 清空，`/api/workorders` 列表可见（管理端能跟进）；
- 测试残渣清理：自测误建的工单 `6a9da3cb` 与 `repro_express_*` 会话已先备份再精确 pop，演示数据零污染。

## 关联 commit

（本轮：代码 / 测试 / 文档快照 三个独立 commit，未推送 —— 本地 PAT 文件 0 字节）
