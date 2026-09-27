# AI 协同开发 Prompt 快照 / AI Collaboration Prompt Snapshots

本目录提交**开发全过程的 AI 对话历史 Prompt 快照**，用于「AI 创新应用挑战赛」评审，
证明项目是在**人与 AI 协同、细粒度迭代**下完成的，而非一次性生成。

## 约定（与比赛要求一一对应）

| 比赛要求 | 本目录的做法 |
|---|---|
| 保留完整、细粒度 Git 提交历史（禁止一次性提交） | 每个修复点/功能点单独一个 commit；本目录每个快照文件也单独提交，不与代码混在一个 commit |
| 提交 AI 对话历史 Prompt 快照 | 每次开发会话一个 `YYYY-MM-DD-<主题>.md`，记录「用户 Prompt → AI 研判 → 改动 → 验证」 |
| 附 README 复现指南 | 见仓库根目录 `README.md` |
| 演示视频（5–8 分钟，含 AI 协同过程） | 见 `docs/演示视频分镜.md`（用户本地录屏用，沙箱无法录屏） |
| 技术文档（PDF ≤30 页） | 见 `docs/技术文档.pdf`（由 `docs/build_tech_doc.py` 生成） |

## 文件清单

- `2026-09-25-pending-persist-fix.md` — 根治「确认落空 / AI 失忆体感」：待确认提案从纯内存改为内存+磁盘持久化（关联 b57f99c）
- `2026-09-25-todo-gaps.md` — 待办三类口语缺口修复（回答"先分析再提交再弹窗" + 修「我要加代办/我的代办呢/代办显示不出来」）
- `2026-09-25-todo-slots-pick.md` — 加待办「时间安排」改为多种合理时间弹窗挑选（关联 6deb74e）
- `2026-09-26-todo-slot-duration.md` — 候选时段按学生说的时长排（说「三个小时」就给 3 小时，不再一律 90 分钟；关联 5c8d275）
- `2026-09-26-todo-oral-and-findcard.md` — 口语下单识别 + 「平时」按工作日排 + 「卡片呢」找卡分支（关联 4755ebb / f8b7ed3 / 959c890 / 88685d7 / d453557 / 63c8a25）
- `2026-09-26-intent-fallback-layer.md` — **架构级**：意图兜底层（样本库 + LLM 只读意图），把「听懂」和「执行」切开，根治"补不完的关键词表"（关联 896d6ff / 57c9c4a / e757b98 / 3bb7600）
- `2026-09-26-retime-crud-selfcheck.md` — 用户要求"增删查改自行测试一遍"：自测暴露「已排待办改期」接不住的真实缺口并接通 INTENT_RETIME 执行分支（关联 e153f84 / 1e3639b / bf2a538 / 0905fa1）
- `2026-09-26-retime-live-collision-fix.md` — 收尾：真机"把周四那个瑜伽挪到周五"漏进大模型兜底，定位样本碰撞把"瑜伽"误记"健身"+旧日误判，堵住并补 RT-S4 回归（关联本批 4 个 commit）
- `2026-09-26-add-course-as-todo-fix.md` — **加课被当代办**双重根因：wants_add_course 漏接"上午/下午/晚上"时段词 + 一条把加课记成 add_todo 的污染样本，加课硬闸门堵漏 + 第十八批回归（关联本批 4 个 commit）
- `2026-09-26-week-grid-overlap-fix.md` — 学生截图报「重叠了」：纯前端 CSS，`repeat(7, 1fr)` 的 1fr 下限是 min-content、又被 nowrap 长串撑到 121px，七列总宽超容器 47px 导致列互相压住；改 `minmax(0, 1fr)` + 折行 + 网格项 min-width:0，两页同步（关联本批 3 个 commit）
- `2026-09-26-time-conflict-sidebyside.md` — 「数据结构和小组会议时间一样重合了」：真凶是周五 `心理学选修`(课) 与 `小组会议`(待办) 同时段真冲突，绝对定位无避让导致后画者盖住先画者；改「冲突分组 + 同组并排分栏」（lanes=1 退化成原样），列表式面板改加 clash 红边 + 「撞」角标（关联本批 3 个 commit）
- `2026-09-27-student-three-column.md` — 学生端三栏外壳重构（侧边栏/主体/输入区）+ 学生端自定义模型 API 透传（config/client/engine/router/main 一路 override；关联 5b793fd / 705992e / c125753）
- `2026-09-27-per-module-history.md` — 底部发送不再拽回校园问答（固定左侧导航）+ 每个模块独立聊天历史 + 每个模块适配各自开场白（sessionId 按模块 key、chat 沿用全局以兼容 plan.html 与测试；关联本批 1 个代码 commit）
- `2026-09-27-panel-chat-visible.md` — 回归修复：上一步让卡片走 switchNav 触发 panel-mode 把聊天区(display:none)藏掉；改为 panel-mode 只隐藏首页组件、保留聊天区，并在 addMsg 滚动外层 .stage（关联本批 1 个代码 commit）
- `2026-09-27-chatbox-chat-schedule-only.md` — 对话框仅保留在「校园问答+日程」：快递/外卖/代办/上传/设置纯查看；no-chatbox 整体隐藏 composer+#log；校园问答欢迎词恢复最初原文（关联本批 1 个代码 commit）
- `2026-09-27-time-span-mixed-format.md` — 截图报「下午四点到晚上7点」被截成 1 小时 + 标题残留「到7点」：`_pick_span` 两条旧正则接不住中英混写（归一成"下午4:00到晚上7点"后退化成单起点→默认补 1 小时）；改统一 `_SPAN_RE` + 新增 `_period_hour` 两端点各看各的时段词，`_strip_title_noise` 补擦「N点」残片与孤立连接词（关联本批 1 个代码 commit）
- `2026-09-27-campus-qa-kb-routing.md` — 「校园问答」问「我要去哪里取快递」却出了「哪取快递」候选卡：`wants_add_todo` 的 `_GO_INTENT_RE` 把"我要去…哪里/怎么"这类**不带问号的问句**当成下单（只挡了 吗/？/?）；且 3c 加待办分支**不按模块拦**，所以单靠前端锁模块拦不住。改：后端补疑问词闸门 `_GO_QUESTION_RE`（不列"什么/几点"以免误伤真下单）+ 前端校园问答栏默认锁 `module=faq`，问题直接进 `search_kb`（关联本批 2 个代码 commit）
- （后续每次开发会话追加新文件……）

## 评审怎么看

每个快照都包含：用户原话 Prompt（摘录）、AI 的代码研判与决策、具体改动文件、
测试护栏结果与 commit 哈希。把任意快照文件与其 commit 对照，即可还原那一步的协同过程。
