# 18 · 落地施工单：决策A 双写 → 驿站接活 → 驿站低代码（创新点）

> 承接 16/17 号。创新描述已锁死 → **低代码新增服务必须真做**。三张单子按顺序给 Qoder，一张一提交。
> 每张单子：改哪段 / 为什么 / 验证。落地 AI 守纪律：**判定执行分离、模型碰不到写库、code/test/docs 三分提交**。

---

## 单子 1 —— 决策A：上报时"工单 + 学生消息"双写（小，先做）

### 改哪段
1. `app/store.py:58 add_message(module, message, answer)` → 加第 4 个参数 `kind: str = "chat"`，写进 item 的 `"kind"` 字段。（向后兼容：不传就是 `"chat"`，旧调用不受影响。）
2. `app/store.py:73 list_messages()` → 加可选参数 `kind: str | None = None`，给了就只返回该 kind（管理员端将来可分开显示）。
3. `app/main.py:3877 workorder_create()` → `add_workorder(...)` 成功后，**在同一个函数里**补一句：
   `add_message(module="workorder", message=f"{kind}｜{desc or '（无补充描述）'}", answer=f"已立案（工单 {item['id']}），状态：待处理", kind="report")`
   —— 效果：管理端「🛠️ 学生工单」出 1 条 + 「📥 学生消息」也出 1 条（你要的双显示）。

### ⚠️ 触发条件（用户 2026-10-16 明确拍板，**别搞错**）
**只在"学生主动说要上报"那一下写**——即和工单**同一个触发点**：确认卡点【确认上报】、或面板表单提交。
- ✅ 学生主动上报 → 工单 +1、学生消息 +1（双写）
- ❌ **普通对话不许写**（继续保持"前端已移除全量上报"，`EXPLAIN.md:168`）
- ❌ **学生上传文件时不许写**（上传 ≠ 上报；上传只是给 AI 读资料）
- 一句话：**触发者是"学生的上报动作"，不是"学生说了话"**。
- ℹ️ **出卡 ≠ 上报**（2026-10-16 用户实测认可）：学生说「**我外卖丢了**」→ **允许**当场弹一张上报确认卡（这只是**提案**，两个文件**一个都不写**）；**只有点了【确认上报】**才写工单 + 学生消息。所以「问题陈述 → 弹卡」是正常且想要的，"只在主动上报时才写"约束的是**写入**、不是**出卡**。
- ℹ️ 卡片文案里的"上报**管理端**"会随**单子 2** 改成"上报**驿站**"。

### 为什么放后端
单一入口、单一事实源：前端只调一次 `/api/workorders`，双写在系统侧完成，前端不用改两处、也不会漏写。

### 已知缺口（不阻塞，写进注释）
没有学生登录/身份体系 → 消息认不出"是谁报的"、内容会不全。**本单只保证"能看见消息内容"**，账号体系留到后面（`EXPLAIN.md` 六已列）。

### 验证
- 新增 `tests/test_51_report_dual_write.py`：
  - 正路径：`POST /api/workorders` → `workorders.json` +1 **且** `student_messages.json` +1、`kind=="report"`、字段齐；
  - **反面 1**：普通 `/api/chat` 一句闲聊 → `student_messages.json` **不增**；
  - **反面 2**：`POST /api/upload` 传一个文件（不带上报）→ `student_messages.json` **不增**；
  - **反面 3**：`/api/chat` 说了"我快递丢了"但**没点确认卡** → 两个文件都**不增**（没确认 = 没上报）。
- 回归：`test_01_basic.py:69`、`test_32_admin_token_gate.py:56`（它们直接打 `/api/student-message`）必须仍绿。

---

## 单子 2 —— 驿站接活：学生快递/外卖问题流到驿站

### 需求
学生报的快递/外卖问题**落到驿站**（不只是管理端），驿站能看、能改处理状态。

### 改哪段
1. **文案改名**（"上报**管理端**" → "上报**驿站**"，一并改，别漏）：
   - `main.py:203-207`（`ASSISTANT_RULES` 第 2 条）；
   - `main.py:1534` 卡片 `summary`：`f"上报管理端：{_wo_kind}"` → `f"上报驿站：{_wo_kind}"`；
   - `main.py:1552` 话术：`"要不要**上报管理端**立案跟进？"` → `"上报驿站"`。
   - ⚠️ `test_38_student_no_tech_leak.py` 用 **AST 扫话术表**、且钉"两边对齐" → 改了话术，**同步更新该测试里的期望表**。
2. **驿站侧接口**（新，不走 admin token）：
   - `GET /api/station/workorders` → `store.list_workorders()`；
   - `POST /api/station/workorders/{id}/status` → `store.update_workorder_status(id, status)`。
   - 先**照现有 `/api/persona` 的做法**（演示级、不挂口令），段首注释写明"演示级未鉴权，上线需补驿站口令"。
3. **驿站端 UI**：`station.html` 加「🛠️ 待处理问题」区块（列表 + 状态按钮），轮询跟学生消息收件箱一个节奏。

### 决策待你确认（先按"落驿站"做）
问题**只落驿站** / **驿站 + 管理端都可见**（管理端留一份监督）？默认按前者；若想双可见，工单照旧进 `workorders.json`，两端各自读，无需改存储。

### 验证
`tests/test_52_station_workorders.py`：驿站侧路由能列/能改状态、`workorders.json` 状态真变；文案断言（"上报管理端"全站消失）；真机 Playwright 走一遍"学生报障 → 驿站端看到 → 改状态"。

---

## 单子 3 —— 驿站低代码新增服务（**创新点，锁死的描述要做实**）

### 目标（对应创新描述）
驿站管理员**一句自然语言** → Agent 生成"服务定义" → 立刻成为一项**可用服务**（失物招领、设施报修…）。"快递/外卖问题处理"就是第 1 项内置服务。

### 数据
新增 `app/data/services.json`：`[{id, name, triggers[], collect_fields[], reply_template, table, created_at}]`。

### 改哪段
1. `store.py`：`add_service(def)` / `list_services()`（读写 `services.json`）。
2. `main.py`：
   - `POST /api/station/services`（body `{description}`）：**一次 LLM 调用**，要求**严格返回 JSON 服务定义**（照搬 `intent.py` 那套"只要 JSON、解析失败即丢弃"的做法）→ 白名单校验 → `add_service`。
   - `GET /api/station/services`：列出已建服务。
3. **动态注册**（复用 `REGISTRY`，`main.py:150-159` 的 prompt 本来就是**函数**，天生适配）：
   - 加一项 `"custom"`：prompt 函数按 `service_id` 现读 `services.json` 拼系统提示；
   - 工具 = 一个通用 `record_custom_item(service_id, fields)` → **出确认卡**（走 `app/agent/pending.py`），学生点确认后**由系统**写 `app/data/custom/<service_id>.json`。
4. `station.html`：加「＋ 新增服务」（输入一句话 → 展示生成的服务定义 → 保存）+「服务列表」+ 可现场"登记一条"试跑。

### ⛔ 安全与红线（本单最重要）
- **LLM 只生成"服务定义"这份数据，绝不生成可执行代码**；生成物只经**固定的确定性路径**（record → confirm → store）执行。**禁止 eval / exec / 动态导入生成代码**。
- 生成的**服务不能自带写库工具**——写库永远走系统确认卡，守住"模型碰不到写库"的地基（红线 4）。
- `services.json` 的生成字段**白名单校验**：拒绝未知字段、超长、HTML（配合 `esc.js` 单一转义来源，防 XSS）。

### 范围切分（先跑通最小可演示）
- **本单做**：① 驿站管理员一句话建服务 ② 驿站端立刻能"登记一条"（确认卡→写库）③ 服务列表可见。
- **增强（下一单）**：把自定义服务接进**判定层**（样本库/语义层，新 module `custom:<id>`），让学生端也能自动触发。

### 验证
`tests/test_53_lowcode_service.py`：一句话→服务定义合法 / 非法字段被拒 / 注册后可用 / 确认卡才写库 / **模型依然拿不到写库**（AST 钉+真跑）。快照 + 把 `EXPLAIN.md` 六「还没做」里对应那条划掉。

---

## 做完的证据链（比赛演示用）
`驿站管理员说一句"加个失物招领…" → 服务立刻出现在列表 → 现场登记一条（确认卡）→ 数据落库`
—— 全程零代码，这才是创新描述里"自生长"那张饼真正落地。
