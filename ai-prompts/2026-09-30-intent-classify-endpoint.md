# 2026-09-30 意图抽取路由接口（POST /classify）

## 一、用户 Prompt（原话）

> @`D:/Workbuddy/2026-09-20-18-56-18/CampusTime-意图抽取路由提示词-定稿.md`

（那份定稿本身的用法说明是「直接粘到另一任务窗口执行，本窗口只做方案与验证」，
用户把它贴进这个窗口 = **就在这个仓库里执行**。定稿要点：新增 `POST /classify`、
学生端加预览调用、扩测试（每条自带 `fix_hint`）、LLM 必须 mock、生成固定格式报告；
并划了三条红线：不许另写一套判定、不许在测试里重定义 `_patch_llm`、
失败报告的"修复思路"不许由模型现场编。）

## 二、AI 研判（动手前先核对"已验证事实"，两处已经漂了）

定稿写于 09-20，这个仓库这十天动过两次架构级重做。核对结果：

| 定稿写的 | 现在的事实 | 影响 |
|---|---|---|
| `intent.classify()` / `Router().route()` 直接调用 | **两个都是 `async`**（09-27 判定层重做时改的） | 接口里必须 `await`，定稿的示例写法照抄会拿到协程对象 |
| 测试用 pytest 收集 | 项目**刻意不用 pytest**（`_harness.py` 写着：想让评委"不装任何额外包也能跑"） | 文件要**两种跑法都支持**：脚本模式（接 `run_all.py`）+ pytest 收集入口 |
| 「项目没有 `/classify`」 | 属实 ✅ | 新建 |
| `_patch_llm` 在 `test_08:48` 本地定义 | 属实 ✅（第 48 行原样还在） | 抽到 `_harness.py` 共享 |
| 不许另写判定、必须三者合并 | 属实 ✅ 且已按此实现 | `looks_like_question` + `Router().route` + `intent.classify` |

另外补了一条定稿没想到的：**`Router()` 在"语义层没结果"时会落到 `_QUICK_MAP` 关键词表**，
于是「帮我加个游泳周四19:00」会报出 `intent=add_todo` 却 `module=faq` —— 诊断接口
给一对自相矛盾的答案比不答还糟（见下"自踩自修"）。

## 三、改动

**`tests/_harness.py`** — 新增 `FakeLLM` / `patch_llm`（test_08 本地那份搬过来）。
**`tests/test_08_intent_fallback.py`** — 改名别名导入，二十多处调用点一字未改。
**`app/agent/router.py`** — `route()` 新增 `intent_hint`：判定顺序变成
`① 调用方判定 → ② 样本库 → ③ 语义层 → ④ 意图先行 → ⑤ 关键词兜底 → ⑥ faq`。
**`app/main.py`** — 新增 `POST /classify`（只读）。
**`app/static/student.html`** — 输入区加「🧭 意图诊断」按钮 + 结果条（默认隐藏）。
**`tests/test_intent_classify.py`** — 62 项，9 组用例（A–F），每条自带 `fix_hint`。
**`tests/_shot_classify.py`** — 真浏览器截图验收（2 张图 + 只读红线断言）。
**`intent_test_report.md`** — 测试自动生成的四块固定结构报告。

## 四、验证

| 项目 | 结果 |
|---|---|
| 全量回归（`run_all.py`） | **1368 通过 / 0 失败（16 批）** |
| 新批（脚本模式） | 62/62 ✅ |
| 新批（`pytest tests/test_intent_classify.py`） | `1 passed` ✅（pytest 是本次为满足定稿验收才装进 `.venv` 的，`.venv` 已 gitignore，仓库无影响） |
| 反向验证 | 撤掉 `intent_hint` → E3 **红 1 项**；撤掉 `looks_like_question` → A1/A2/A3/D1 **红 4 项**；还原后回绿 |
| 真机（有密钥，语义层真跑） | 图书馆→faq/chat ✅ ｜ 取快递→express/chat ✅ ｜ 加游泳→planner/add_todo ✅ ｜ 删写作业→remove_todo ✅ ｜ 标记完成→done_todo ✅ ｜ 凯迪（错字）→chat ✅；**待办 4 条 id 前后一致** |
| 真机前端（Playwright） | 按钮渲染 ✅ ｜ 问句/办事两种结果都对 ✅ ｜ 点按钮前后待办与会话条数**分毫未变** ✅ ｜ 聊天链路照旧可用 ✅ ｜ 无 JS 报错 ✅ |

## 五、自踩自修（诚实记录）

1. **接口给出的答案自相矛盾**：`intent=add_todo` 却 `module=faq`（关键词表兜底把
   "没有关键词的办事句"甩给了默认的问答模块）。修法是给 Router 加「意图先行」一档，
   **位置必须在关键词表之前** —— 意图是"他想干什么"，比"句子里出现了哪个词"强。
2. **截图脚本两处假红**（都不是产品问题，是我脚本写错）：
   · 用脚本里造的 `SID` 去查会话消息数，而 UI 用的是 localStorage 里的会话 id
     → 永远查到 0 条，"聊天链路还通着"必然假红。改成从页面里读 `campustime_session`。
   · 用固定 `wait_for_timeout(800)` 等诊断结果，有 key 的机器上这一步要真调一次模型，
     机器一忙就等不够 → 结果条还停在"正在看它怎么理解…"。改成 `wait_for_function`
     等它真写出结果（这正是项目里记过的坑：**固定等待不够，要等条件**）。
3. **提交信息被 shell 吃掉**：消息里用了反引号（`route()`、`_QUICK_MAP`）→ 被当成
   命令替换执行掉，提交信息出现"改法： 新增可选参数"这种缺字。改用
   `git commit -F - <<'MSG'` heredoc 修正。**教训：提交信息里不要用反引号/ASCII 引号，
   要么用 heredoc。**

## 六、留给后续的两件事

1. **裸跑 `pytest` 会误收其它测试文件**：`test_01`~`test_15` 是"脚本式"（结尾
   `sys.exit()`），pytest 收集它们会报 collection error。所以 pytest 那条路要**带上
   文件路径**：`pytest tests/test_intent_classify.py`。真要全量请用 `python tests/run_all.py`。
2. **交付文档里的数字又过时了**：`docs/技术文档.pdf` 里"38 个 API"现在是 **39 个**，
   测试项数也已变成 **1368**（它是限期快照，允许带日期数字，但差值太大会被评审看出来）。
   需要时重跑 `docs/build_tech_doc.py`，记住 **重生成前逐字符校验 GB2312**
   （STSong-Light 走 `UniGB-UCS2-H`，集外字符会**静默渲染成空白**、不报错）。

## 七、关联 commit

- `ae18d20` ① 抽 `_patch_llm` 到 `tests/_harness.py` 共享
- `0ee0069` ② Router 增加「意图先行」一档
- `38555d2` ③ 新增 `POST /classify`
- `60d7eb6` ④ 学生端加「🧭 意图诊断」按钮
- `4858b42` ⑤ 新增 `tests/test_intent_classify.py`（62 项）+ `_shot_classify.py`
- `da7b552` ⑥ 归档 `intent_test_report.md`
- （本快照单独一个 commit，不与代码混在一起）
