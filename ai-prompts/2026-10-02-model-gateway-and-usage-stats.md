# 2026-10-02 · 自研模型反代网关 + 用量统计（设计稿，零代码改动）

## 用户 Prompt（原话）
> 「我想给那个项目加一个反代，在管理页面能看见时间用户，模型名称，输入输出token，不同模型占比，状态，简单的总量统计，同时在切换模型后，能通过问我的中转，直接使用，不需要去额外写代码适配别的模型」

随后用户补了一张完整链路图（原话摘录）：
> 用户输入 → A层【认知层 Agent】→ 请求你自研的模型反代网关（你写的FastAPI接口）
> 【自研反代网关】读取后台选中模型的配置（地址、密钥）→ 转发请求给上游模型API
> → 统一标准化tool_calls返回JSON结构 → 返回统一格式结果给A层
> → B层【控制/闸门层】读写判断，写操作弹出提案确认 → C层【执行层】RAG检索、数据库读写
> → 捕获异常，回传给A层模型继续多轮思考 → 模型输出回答

过程纠偏（原话）：
> 「不改代码啊你忘了？我要思路确认先，然后整理prompt」
> ——AI 曾按第一版理解直接动手改代码，被用户叫停。

## 过程记录（含一次叫停与回退，诚实留痕）
- AI 初版按"把 base_url 指向外部中转"的理解直接动了 6 个文件
  （store / client / engine / intent / polish / main），被用户叫停后 `git checkout` 全部回退，
  `git status` 恢复干净——**本轮代码零改动**。
- 改为"方案先行"：先对照代码核实现状 → 列设计分块 → 用户逐项拍板 → 整理成规范文档，等待执行窗口落地。

## 研判（对照代码核实）
- 全项目模型调用收口在 `app/llm/client.py` `DeepSeekClient.chat()`，走标准 OpenAI 兼容协议
  （`POST {base_url}/chat/completions` + `model` 字段）；`get_llm_config()` 优先级
  学生 override > settings.json > .env，管理端「API 配置」卡片已能在线改 api_key / base_url / model。
- **用户链路图里的 B/C 层已经存在，不用写**：B 层闸门（读写判断 + 写操作弹 pending 提案确认）、
  C 层执行（tools / store / kb.json 的 RAG+DB）、以及"异常回传模型继续多轮"——全部在
  `engine.py` ReAct 循环 + `pending` 确认条里已有。**真正要新写的只有 A 层的"自研反代网关"**。
- 网关是**唯一出口**，因此"反代"与"用量统计"合**一个部件**：转发、标准化、记账三件事在网关里一次做完，
  业务层（engine / intent.classify / polish_reply）只换调用入口，消费逻辑零改动。

## 决策记录（用户逐项拍板，均选推荐项）
第一轮（功能形态）：
1. 模型切换 = 管理端只改「模型名」字段（不做"模型预设"快捷 UI）；
2. 用量看板"用户"列 = 按学生会话 session_id（能看每个学生的调用量）；
3. 中转地址/模型名先占位，之后在管理端填真实值。

第二轮（链路图升级后）：
1. 网关形态 = **同进程内**新增 `app/llm/gateway.py` 模块（`ModelGateway` 包裹 `DeepSeekClient`），不拆独立服务；
2. tool_calls 标准化 = **跨厂商统一**（Claude `tool_use` / Gemini `functionCall` / 国产兼容格式 → 统一 OpenAI 风格）；
3. 模型配置 = **单一生效模型**，后台改 `model` 字段即切换（不做多模型路由表）。

## 统一 tool_calls 规范（全项目唯一认知，归一在网关内完成）
目标结构（OpenAI 风格）：`{id, type:"function", function:{name, arguments 为 JSON 字符串}}`；
- OpenAI / DeepSeek / 国产兼容：校验后透传，`arguments` 为对象则转字符串；
- Claude `tool_use` block：`{id: block.id, function:{name: block.name, arguments: JSON.stringify(block.input)}}`；
- Gemini `functionCall`：`{id: 网关生成 call_<uuid>, function:{name, arguments: JSON.stringify(call.args)}}`；
- 兜底：`id` 缺则生成、`arguments` 缺则 "{}"、解析失败保留原样打 warning（绝不丢调用）。

## 交付物（本轮）
- 规范文档：`D:/Workbuddy/2026-09-20-18-56-18/CampusTime-反代与用量统计-prompt.md`
  （含四块设计、`llm_usage.jsonl` 字段约定、`summarize` 返回结构、安全兜底、非目标、验收标准），
  按项目惯例"直接粘到另一任务窗口落地"。
- 设计要点：网关四职责（读配置 / 转发 / 标准化 / 记账，`try/finally` 成功失败都记）；
  用量写 `app/data/llm_usage.jsonl`（ts / user / model / 输入输出token / status / latency_ms / error）；
  管理端新增「用量统计」卡片（6 汇总卡 + 各模型占比 + 最近调用表 + 清空 + 5 秒自动刷新）；
  接口 `GET /api/admin/usage`、`POST /api/admin/usage/clear`。

## 状态与验收口径（待执行窗口落地后回填）
- 本轮代码零改动；未动 `docs/技术文档.pdf`。
- 落地后验收关键项：真实对话一条进 usage（user=session_id、token 正确）；
  桩测非 OpenAI 风格 tool_calls → engine 收到统一结构且加待办出卡行为不变；
  后台改 model 字段即换模型（零代码）；拔 key 记 status=error 不崩。

## 关联
- 本快照为**设计稿阶段**留痕，无代码 commit；执行窗口落地后在此回填关联 commit 哈希。
