# 2026-10-27 · #70 收尾四小笔：一条噪音、两句断言、一笔引用（#69 冻结后的第一张更正单）

> 施工单原件：`2026-10-27-ticket70-test49-stderr-mute-readme-claims-drop-in.md`（原文照入，md5
> `c1d28dafb511e007b624e69ff1db194e`，与 `D:\Workbuddy\2026-10-12-21-36-55\prompts\70_…_drop-in.md` 逐字节相同）。
> **本单不碰 `app/`、不碰 `app/static/`、不碰任何既有判据** —— 只动 `tests/test_49_streaming_sse.py` 与 `ai-prompts/README.md`。
> 前置 HEAD：`08db609`（开工前 `git log -1` 复现，未漂）。

## 〇 前提实测（五处与单据不同，写在最前面）

| # | 单据写的是 | 实测（`08db609` 上现查） | 结论 |
|---|---|---|---|
| 1 | §0.1 truncate 剧本 `:147-155`、`log_message` `:166-167`、`_serve_stream` `:176-179`、创建点 `:177` | 全部一字不差（`close_connection` 在 `:151`、`wfile.close()` 在 `:152`）；全仓 `ThreadingHTTPServer` 在 `test_49` **只有 `:177` 这一处** | 成立 |
| 2 | §0.1 那条链的第四个环节 | 基线 stderr 逐字节读出来就是这个形状：`socketserver.py:692 process_request_thread` → `:362 finish_request` → `http/server.py:436 handle` → `:425 handle_one_request` → `self.wfile.flush()` → `ValueError: I/O operation on closed file.` | 成立 |
| 3 | §3.1 正控两条在 `:360` / `:362` | 实为 **`:361` / `:363`**（改前）；本单落地后为 `:384` / `:386` | 行号漂一行，内容对得上 |
| 4 | §0.2 那三句「每个快照都包含 … commit 哈希」站不住，并给了现数命令 | 命令原样复跑：**137 份快照 / 25 份一个哈希都没有 / 最晚 `2026-10-13` / 最薄 `2026-09-27-panel-chat-visible.md` 1557 字节** —— 四个数逐一对上。补两笔没量的：那 25 份跨 **10 个日期**（`09-26` 1 份、`09-27` 5 份、`10-07` 6 份…），`10-14` 起 51 份**每份至少 3 个哈希** | 成立（且比单据更细） |
| 5 | §0.3 后缀 13 / 15 / 1 = 29，老约定只筛到 14；§0.4 `#68` 原件在仓库里零命中 | `ls *-dropin.md *-drop-in.md *-brief.md` = **29**；`ls *-dropin.md *-brief.md` = **14**；`ls ai-prompts/ | grep -c 68` = **0** | 成立 |
| 6 | §一.2/§3.3 让在 README 里写反查命令 `git log --oneline -- <文件名>` | **这条命令照抄即死**：`student.html` / `planner.py` / `main.py` / `store.py` 四个裸名一律 **0 行**，加前导星号才有 **103 / 98 / 151 / 45 行** —— 那些文件都在 `app/` 子目录下 | **不成立，已按实测改写**（见 §一.2） |
| 7 | §一.4 草稿说「`69` 的快照里也逐条复核过」四条批示 | 只复核了**三条**（①`dropped` 零消费者 / ②month 无页面调用 / ④B4 计数判据）；③ `main.py:3415` 在 69 的快照里 **grep 零命中** —— 因为它按批示**根本没做** | **不成立，已按实测改写**（见 §一.4） |

## 一 四笔改动

| # | 落点（改后行号） | 改了什么 |
|---|---|---|
| ① | `tests/test_49_streaming_sse.py:25` | 补 `import sys`（文件头原来只有 asyncio/http.server/json/os/threading/time，漏了会 `NameError`） |
| ① | `tests/test_49_streaming_sse.py:177-196` | 新增 `_QuietServer(http.server.ThreadingHTTPServer)`，`handle_error`（`:192-196`）**只对 `ValueError` 且话里带 `closed file` 早退**，其余照旧 `super().handle_error(...)` |
| ① | `tests/test_49_streaming_sse.py:200` | 创建点 `http.server.ThreadingHTTPServer(...)` → `_QuietServer(...)`（全仓唯一一处） |
| ② | `ai-prompts/README.md:338-348`（原 `:330-332`） | 「**每个**快照都包含…commit 哈希」换成有时间前提的实话 + 反查命令 + 可复跑的现数命令 |
| ③ | `ai-prompts/README.md:263-265`（原 `:263` 一行 → 三行） | 后缀约定补第三种写法 `-drop-in`，并写明「早期到 `#50` 用前者、`#54` 起用后者、不改名」的理由 |
| ④ | `ai-prompts/README.md:321-326`（新插入） | 「几份没入库的」那条 blockquote 列表末尾追加 `68` 号：判断单、无自己的快照、四条里三条落在 `69`、**第四条按批示没做** |

**没做的三件（都是单据明令的）**：不动 `truncate` 分支本身（`self.wfile.close()` 是它模拟断流的唯一手段）；
不给 `tests/test_47_shared_async_client.py:83` 的 `_CountingServer` 加同样的静音；不回填那 25 份的哈希。
**也没选另一条路**：`68` 号原件没有入库（选了"补说明"，就没有两头都做）。

## 二 静音这一笔的验证：改前 / 改后 / 六笔变异

改前先抓靶子（单据 §3.1），跑的是 `.venv` 的 python，不是系统 python：

| 阶段 | rc | stderr | `Exception occurred…` | `I/O operation on closed file` |
|---|---|---|---|---|
| 改前（`08db609` 原文件） | 0 | **1223 字节** | 1 | 1 |
| 改后（窄静音） | 0 | **0 字节** | 0 | 0 |

**非空正控（不能省的那步）**：改后 B 批仍 **10 项全绿**，且两条只在 `truncate` 之后才有值的断言拿到的都是实测值 ——
「中途断线：已吐的那一块留着」= `['食堂']`（**非空**，证明真跑到了 truncate），
「中途断线要把错误上抛」= `unreachable`。A/C/D/E/F 五批计数与改前逐批相同（9/10/6/6/4/9 = 44 项，全绿）。

变异六笔（runner：`D:\campustime-scratch\mut70.py`；日志：`D:\campustime-scratch\mut70_logs\`）：

| # | 变异 | 实测 | 说明什么 |
|---|---|---|---|
| V1 | 静音分支里把 `return` 换成 `super().handle_error(...)` | stderr **2446 字节 / Exception×2** | 重现。为什么是 2 不是 1：这句打完**还落到下面那句** `super()` ⇒ 打两遍。是变异形状造成的，不是行为差异 |
| V2 | 创建点换回 `http.server.ThreadingHTTPServer` | stderr **1223 字节 / Exception×1** —— **与改前基线逐字节等长** | 真回退；证明替换点唯一、且必须是这个类 |
| V3 | 去掉 `isinstance(exc, ValueError)`，只留字符串判据 | rc=0、stderr 0 字节 | 与原文**同解** ⇒ 单看这条判不出"冗余"还是"有效"（见 V5/V6） |
| V3b | 改成 `if exc is not None:`（=宽静音 `pass` 的等价物） | rc=0、stderr 0 字节 | 与窄版、与 V3 全不可分 ⇒ **V1~V3 区分不了窄静音和宽静音**，这正是单据说"只有 V4 值钱"的意思 |
| **V4** | 窄静音 + `_chunk()` 开头抛 `AttributeError("probe")` | stderr **含** `File "…test_49_streaming_sse.py", line 65, in _chunk` + `AttributeError: probe` | **没被吞** ⇒ 窄版不误伤真故障 ✅（单据点名的必做项） |
| V5 | V4 的探针换成 `RuntimeError("I/O operation on closed file (probe)")`，**并去掉 `isinstance`** | rc=1、stderr 5651 字节，但 **`RuntimeError` 出现 0 次** —— 只剩客户端那条 `RemoteProtocolError` → `LLMError` 链 | 服务端根因**被误吞**，排查时只看得到"连不上大模型" |
| V6 | 同一颗 `RuntimeError("…closed file…")` 探针，**保留 `isinstance`** | stderr 里 `RuntimeError: I/O operation on closed file (probe)` 打出、`in _chunk` 帧在 | 与 V5 唯一差别就是那个 `isinstance` ⇒ **它不冗余** |

### 但 V3 那一行单据期望是错的 ——「冗余但无害」实测不成立

单据 §3.2 写 V3「仍绿（说明判据冗余但无害）」。它"仍绿"是对的，"无害"是错的：
**V5/V6 这一对把 `isinstance` 单独拎出来当变量**，结果是同一条非 `ValueError`、话里带 `closed file` 的异常，
保留 `isinstance` 打得出来（V6）、去掉它就被吞（V5）。所以：

- `isinstance(exc, ValueError)` 是**唯一**挡住"别的异常类型、恰好话里带 closed file"被误静音的闸门；
- 单据只设计了"去掉它、看还在不在"（V3），那颗探针是 `AttributeError("probe")` —— 话里**没有** `closed file`，
  于是字符串判据本来就拦不住它，V3 自然同解。**要否证"冗余"，得造一条话里带 `closed file` 的非 ValueError**，
  这才是 V5/V6 存在的理由。
- 顺带一条：V4/V5/V6 三笔 rc 都是 1（测试崩了），这是探针变异应有的样子，**不是**"静音把断言弄红了" ——
  判静音只看 stderr 有没有那段 traceback，判回归看 §三 的全量。

## 三 回归与范围

- 全量：见 §五 的账（跑完本单**项目数一条都不该变**）。
- `tests/test_47_shared_async_client.py` 单跑 rc=0、stderr **0 字节**，且 `git diff` 对它**一字未动** ——
  它没有 truncate 剧本、不制造这个异常，所以"只改 `test_49`"这句是真的。
- 范围核（`git diff --numstat`，本单实际输出）：
  `tests/test_49_streaming_sse.py` **24/1**（那 1 处删除就是被替换的创建点那一行）、
  `ai-prompts/README.md` **34/4**（三处文字换句 `20/4` ＋ 索引两条纯插入 `14/0`；那 4 处删除是被替换掉的旧句子，
  不是删了别人的行 —— 索引两个插入点各自核过邻居完整）。`app/` 与 `app/static/` **0 处**。
- 真实数据：跑全量前后各取一次 `app/data` 指纹（465 个文件），改动 **0 个**。

## 四 这一单里被实测推翻的，是**单据**的两句

写进交付档案不是为了挑刺，是因为这两句如果照抄落地就成了**新的不实陈述**：

1. **反查命令**（§一.2 要往 README 里写的那句）。单据给的是 `git log --oneline -- <快照里出现的文件名>`，
   而四个真实文件名按裸名查一律 0 行。这句一旦照抄，评委照做的结果是"查不到 ⇒ 这快照在瞎写"——
   **比原来那句「每个快照都包含哈希」更糟**，因为它给了个可执行的假承诺。README 里现在写的是
   `git log --oneline -- '*student.html'`（103 行），并把"星号不能省"的理由一并写明。
2. **「`69` 的快照里也逐条复核过」**（§一.4 补账那句）。实际只三条，第四条 `main.py:3415`
   在 69 里一个字都没有 —— 因为它按批示没做。**照抄就变成"四条都收掉了"，而账上明明挂着一条。**
   补账那条现在明写「第四条按批示没做，它要等一个先回答『缺 id 的记录算不算一道待办』的产品决定」。

第三句不算推翻、算补充：`isinstance` 那半（§二）。单据把它标成"冗余但无害"，实测它**是载荷**。

## 五 账

- 全量：**5125 / 0（共 89 批）**，rc=0 —— 与 #69 冻结时**逐项相同**，本单项目数一条都没变（不是 5126，也没有哪批少跑）。
- **全量那一跑的 stderr 现在是 0 字节** —— 这是本单唯一"看得见"的变化，也是它存在的全部理由。
  改前我没留全量 stderr 的字节数（只留了 `test_49` 单跑那份：1223 字节 / `Exception`×1），
  但上一轮全量屏幕上刷到末尾那条 traceback 就是它，来源同一处。
- `test_47` 单跑：rc=0、stderr **0 字节**、文件一字未动（`git diff` 对它是空的）。
- 真实 `app/data`：全量前后各一次指纹（465 个文件），**改动 0 个**。
- 严格克隆（tracked-only，= 评委 clone 到的那一棵树）：见 §七，跑的是测试提交落地之后的树。
- 提交：本单四笔合成一笔测试提交 + 一笔文档提交（快照 + 原件 + 索引两条），提交信息点明「只动 `tests/` + `ai-prompts/`，产品代码零改动」。
- 未推：**297 笔 @ `08db609`（13:2x 现查）** —— 本单两笔落地后必然 +2，引用前现查（`git rev-list --count origin/master..HEAD`）；推送由用户自己做。
- 8000：**不重启** —— 本单 `app/` 与 `app/static/` 零改动，跑着的进程吃的还是 #69 冻结版；重启它只会让"已知的状态"变成未知。


## 六 冻结口径核

#69 立的冻结：允许提交 / 推送 / 改文档 / 发现不实另立更正单；不允许改 `app/**.py`、`app/static/`、
动 `tests/` 既有判据（要改另开单）。本单是那张"另开的单"，且：

- `app/` 与 `app/static/` **一字未动**（`git diff --numstat` 只有两个文件，见 §三）；
- `tests/` 里**没有改任何一条既有判据** —— 加的是一个只服务假上游的服务器子类，44 条断言逐批计数与改前相同；
- 改的两句 README 都是**对外承诺的措辞**，不是判据。

⇒ 冻结没破。下一单如果还要动 `tests/` 或 `app/`，仍然得先有单子。

## 七 严格克隆（评委 clone 到的那一棵树）与提交链

克隆用 `git worktree add --detach`（tracked-only），建在测试提交 `0e84abb` 上；其后那笔只加
`ai-prompts/` 三个文档，不参与任何测试。**跑满 89 批没在克隆里重做**（#69 已经在那棵树上核过总账，
本单没动任何判据），本单要在克隆上回答的只有一个问题：**评委拿到干净的一棵树，屏幕上还有没有那段红。**

| 检查 | 实测 |
|---|---|
| 必须**没有**的运行时/敏感路径（`.env`、`settings.json`、`trace_log.jsonl`、`llm_usage.jsonl`、`intent_samples.json`、`student/`） | 全部不存在 ✓ |
| 必须**有**的交付项（`run_local.bat`、`demo_timetable/`、`tests/_optional_browser/`、69 号原件） | 全部在 ✓ |
| `git ls-files app/data` | **5** 个种子 ✓ |
| `tests/test_49_streaming_sse.py` 单跑 | rc=0、stderr **0 字节**、六批 **9/10/6/6/4/9 = 44 项全绿**（与本机逐批同计数） |
| `tests/test_47_shared_async_client.py` 单跑 | rc=0、stderr 0 字节 |

⇒ **评委侧**：clone → `python tests/run_all.py` → 屏幕上不再出现那条 `ValueError: I/O operation on closed file`。

提交链（本单两笔）：

| 哈希 | 内容 |
|---|---|
| `0e84abb` | `test(#70)` 假上游收尾噪音静音（`24/1`，只动 `tests/test_49_streaming_sse.py`） |
| （本快照所在那一笔） | `docs(#70)` 本快照 + 施工单原件 + README 三处文字与索引两条 —— 哈希写不进自己正在被提交的文件，要核就 `git log -1` 现查 |

