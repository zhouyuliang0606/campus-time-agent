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

- `2026-09-25-pending-persist-fix.md` — 根治「确认落空 / AI 失忆体感」：待确认提案从纯内存改为内存+磁盘持久化（关联 2540843）
- `2026-09-25-todo-gaps.md` — 待办三类口语缺口修复（回答"先分析再提交再弹窗" + 修「我要加代办/我的代办呢/代办显示不出来」）
- `2026-09-25-todo-slots-pick.md` — 加待办「时间安排」改为多种合理时间弹窗挑选（关联 594952c）
- `2026-09-26-todo-slot-duration.md` — 候选时段按学生说的时长排（说「三个小时」就给 3 小时，不再一律 90 分钟；关联 3752064）
- `2026-09-26-todo-oral-and-findcard.md` — 口语下单识别 + 「平时」按工作日排 + 「卡片呢」找卡分支（关联 4dac426 / d2b3ea2 / 7164de7 / 30eeb8f / 0c56879 / 51174bb）
- `2026-09-26-intent-fallback-layer.md` — **架构级**：意图兜底层（样本库 + LLM 只读意图），把「听懂」和「执行」切开，根治"补不完的关键词表"（关联 ba022ce / ef401d6 / 0b886f0 / 98003d8）
- `2026-09-26-retime-crud-selfcheck.md` — 用户要求"增删查改自行测试一遍"：自测暴露「已排待办改期」接不住的真实缺口并接通 INTENT_RETIME 执行分支（关联 0dbbbea / e36a824 / 3d25bee / 0309b31）
- `2026-09-26-retime-live-collision-fix.md` — 收尾：真机"把周四那个瑜伽挪到周五"漏进大模型兜底，定位样本碰撞把"瑜伽"误记"健身"+旧日误判，堵住并补 RT-S4 回归（关联本批 4 个 commit）
- `2026-09-26-add-course-as-todo-fix.md` — **加课被当代办**双重根因：wants_add_course 漏接"上午/下午/晚上"时段词 + 一条把加课记成 add_todo 的污染样本，加课硬闸门堵漏 + 第十八批回归（关联本批 4 个 commit）
- `2026-09-26-week-grid-overlap-fix.md` — 学生截图报「重叠了」：纯前端 CSS，`repeat(7, 1fr)` 的 1fr 下限是 min-content、又被 nowrap 长串撑到 121px，七列总宽超容器 47px 导致列互相压住；改 `minmax(0, 1fr)` + 折行 + 网格项 min-width:0，两页同步（关联本批 3 个 commit）
- `2026-09-26-time-conflict-sidebyside.md` — 「数据结构和小组会议时间一样重合了」：真凶是周五 `心理学选修`(课) 与 `小组会议`(待办) 同时段真冲突，绝对定位无避让导致后画者盖住先画者；改「冲突分组 + 同组并排分栏」（lanes=1 退化成原样），列表式面板改加 clash 红边 + 「撞」角标（关联本批 3 个 commit）
- `2026-09-27-student-three-column.md` — 学生端三栏外壳重构（侧边栏/主体/输入区）+ 学生端自定义模型 API 透传（config/client/engine/router/main 一路 override；关联 6b5a953 / 3a1d24f / d96eb29）
- `2026-09-27-per-module-history.md` — 底部发送不再拽回校园问答（固定左侧导航）+ 每个模块独立聊天历史 + 每个模块适配各自开场白（sessionId 按模块 key、chat 沿用全局以兼容 plan.html 与测试；关联本批 1 个代码 commit）
- `2026-09-27-panel-chat-visible.md` — 回归修复：上一步让卡片走 switchNav 触发 panel-mode 把聊天区(display:none)藏掉；改为 panel-mode 只隐藏首页组件、保留聊天区，并在 addMsg 滚动外层 .stage（关联本批 1 个代码 commit）
- `2026-09-27-chatbox-chat-schedule-only.md` — 对话框仅保留在「校园问答+日程」：快递/外卖/代办/上传/设置纯查看；no-chatbox 整体隐藏 composer+#log；校园问答欢迎词恢复最初原文（关联本批 1 个代码 commit）
- `2026-09-27-time-span-mixed-format.md` — 截图报「下午四点到晚上7点」被截成 1 小时 + 标题残留「到7点」：`_pick_span` 两条旧正则接不住中英混写（归一成"下午4:00到晚上7点"后退化成单起点→默认补 1 小时）；改统一 `_SPAN_RE` + 新增 `_period_hour` 两端点各看各的时段词，`_strip_title_noise` 补擦「N点」残片与孤立连接词（关联本批 1 个代码 commit）
- `2026-09-27-campus-qa-kb-routing.md` — 「校园问答」问「我要去哪里取快递」却出了「哪取快递」候选卡：`wants_add_todo` 的 `_GO_INTENT_RE` 把"我要去…哪里/怎么"这类**不带问号的问句**当成下单（只挡了 吗/？/?）；且 3c 加待办分支**不按模块拦**，所以单靠前端锁模块拦不住。改：后端补疑问词闸门 `_GO_QUESTION_RE`（不列"什么/几点"以免误伤真下单）+ 前端校园问答栏默认锁 `module=faq`，问题直接进 `search_kb`（关联本批 2 个代码 commit）
- `2026-09-27-type-judgment-redo.md` — **架构级**：学生反馈「你只是修了这一个，其他类型的区分没改」——上一轮只补了一句的闸门，类型的**判定方式**没动。"归哪个模块"仍由 `router._QUICK_MAP` 50 多个子串匹配说了算、"想干什么"另有 30 多张表，两处互不通气。这次把**类型也收进判定层**，与意图做同一次判定，顺序倒成「**语义优先、关键词兜底**」（样本库 → LLM 一次出 {module,intent} → `_QUICK_MAP` → faq）；`/api/chat` 的判定层前移到最前、五条确定性分支共用同一次判定，否决闸门 `_rule_add_veto` → `_rule_veto` 全分支让路；新增 `QUESTION_RE`/`looks_like_question`/`_QA_MODULES`（先分"问/办"，粗信号只决定要不要复核、不下结论）。含两处自踩自修：规范话替原话会把"周二"整段丢掉（候选摊满一整周）、`Router` 导入时建死客户端导致"语义优先"悄悄退回关键词表（关联本批 4 个代码 commit）
- `2026-09-27-github-push-and-contributor-identity.md` — **交付链路**：把 188 个细粒度提交推上公开仓库。三重独立卡点（WorkBuddy 自带代理 `127.0.0.1:54437` 拦 GitHub → 改用自有 VPN 代理 `7890`；仓库尚未创建 → `POST /user/repos` 建库并把默认分支 main 改回 master；GCM 从没登录过会弹 GUI 卡死 → 用 `credential.helper=` 空值清空列表 + `store` 助手，PAT 不进仓库）。另查明「Contributors 显示 zhouto」的成因：**commit 里只有 name/email 两行纯文本，GitHub 只按 email 反查账号、完全忽略 name**，而 `zhouyig@163.com` 是 2017 年绑给 zhouto 的老邮箱；历史哈希一字不动，只把后续提交身份改为 `zhouyuliang0606` 的免回复邮箱（关联本批 1 个 docs commit）
- `2026-09-27-docs-sync.md` — **交付物同步**：体检发现三份交付文档全部停在 09-24，落后于 09-26 判定层与 09-27 类型判定重做两次架构级改动（README 还写着「100 项自检」，实测已是 **1052 项**）。改前先采事实基线（文件清单/API 数/测试项数全部由命令实测），再分四个独立 commit 分别重写 README、更新 EXPLAIN、重写技术文档生成脚本并重新出 PDF、修正 intent.py 顶部与实现矛盾的 docstring。**顺带修掉两个渲染缺陷**：① 代码块原用 Courier 字体、块内中文渲染成空白 → 改 CJK 字体；② 34 个字符（`— · – • ✅`）不在 GB2312 内、STSong-Light 静默吞掉不报错 → 逐字符校验并换成等价字符。技术文档实测 **10 页**（要求 ≤30 页），新增请求时序 / 38 个 API 清单 / 数据文件两个附录（关联本批 4 个 commit）
- （后续每次开发会话追加新文件……）

## 评审怎么看

每个快照都包含：用户原话 Prompt（摘录）、AI 的代码研判与决策、具体改动文件、
测试护栏结果与 commit 哈希。把任意快照文件与其 commit 对照，即可还原那一步的协同过程。
