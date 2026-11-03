# 2026-11-03 · #115 多候选卡上「星期」是空的、「约 ? 分钟」，而且没有确认条弹窗、勾两条只生效一条

> 施工单：`D:/Workbuddy/2026-10-12-21-36-55/prompts/115_多候选卡星期和分钟是空的_模型拼的JSON没补字段_drop-in.md`
> （原件逐字节入库：`2026-11-03-ticket115-slot-card-fields-drop-in.md`，14281 字节、275 行、
> md5 `a7182068f720c6dead74b585aa2f11dc`，与原件逐字节一致）。
> 起因：用户 2026-11-03 20:58 截图 —— 三行全是「2026-11-04（）11:45-13:45 · 约 ? 分钟」，
> 底下是【确认所选】而不是确认条。
> 他补的两句原话把范围定住了：「**主要是这个弹窗，它有时候渲染成功，有的时候失败**」；
> 上下文粘住那一族他自己处理（「我没有做长期记忆，只做了短记忆……我现在正在改」），本单**不碰**。
> 照旧规矩：先把单据的前提逐条跑真代码验一遍，再动手，改动往小里改。

---

## 〇 前提实测（先跑真代码，再动手）

### 0.1 单据说的形状缺口是真的，实测值

把截图那三条**原样**（模型交出来的就是这四个字段）喂进去，改前：

```
{"title":"去图书馆","date":"2026-11-04","start":"11:45","end":"13:45","note":"..."}
```

没有 `kind`、没有 `weekday`、没有 `minutes`。前端 `student.html:1984-1985` 取的是
`o.weekday || ""` 和 `o.minutes != null ? … : "?"` ⇒ 空括号 + 问号。**前端没做错任何事。**

### 0.2 「时好时坏」不是渲染时机，是模型挑了哪只工具（本轮新增的一条实测，单据没写）

用户这句「有时候成功有时候失败」比单据的标题更准。数了他自己的 `app/data/trace_log.jsonl`
（只读，795 笔）里所有出了加待办卡的轮次：

| 模型调的工具 | 轮数 | 交出来的形状 | 弹窗 |
|---|---|---|---|
| `propose_slots` | **24** | 裸 JSON，无 kind（options_n 全是 2/3/4） | **出不来** |
| `propose_todo_tool` | 10 | 带 kind | 出得来 |
| `propose_todo_slots_tool` | 6 | 带 kind | 出得来 |
| `propose_todo_change` | 1 | 带 kind | 出得来 |

提示词里两只功能重叠的工具**同时挂着**、还各写了一句"必须"：`planner.py:6593`
「**propose_slots** … 提完候选后必须调用它」和 `:6595`「**propose_todo_tool**：你的第一站」。
模型这次挑 A 下次挑 B ⇒ 24 坏 17 好，坏的全是同一只工具。**这是结构性的，不是随机的。**

⚠️ 顺带更正我在报告里先前的一个说法：我一度把它归成"Agent 模式那条路"。数据不支持——
最后 12 轮全是 `agent_mode=False` 照样走 `propose_slots`、照样出不来弹窗
（有 Key 时 `main.py:5086` 那个兜底也会进引擎，跟开关无关）。单据 §0.4 那句
「两种模式下都一样」是对的。

### 0.3 §0.5「#75 的收敛对这条路从来没生效」——核完成立

`_collapse_same_title_adds`（`main.py:1587`）的分组条件是
`if not isinstance(o, dict) or o.get("kind") != "todo_add": continue`（`:1610`）。
裸卡一个 kind 都没有 ⇒ 全被 continue ⇒ `groups` 空 ⇒ `if not merged: return options` 原样返回。
**#75 修的是 `propose_todo_tool` 那条路，`propose_slots` 这条路一次都没被管过。**

### 0.4 「勾两条只生效一条」是同一条链的下游（单据标题没覆盖到的那一半）

没 kind ⇒ 前端每一档 `filter(o.kind === "X")` 全筛空 ⇒ 穿透到 `student.html:1956`
`#59` 留的 `drawable` 老内联勾选框 ⇒ `:1999` 把勾选拼成**一句聊天**发出去：

```
确认 2026-11-07 10:00-13:00 打乒乓球
确认 2026-11-08 10:30-13:30 打乒乓球
```

`is_confirmation`（`pending.py:220`）不认这种多行带日期时间的话；
走到 `_match_todo_pick`（`main.py:1718`）时 `parse_add_todo` 只抠**一组**日期时间
⇒ 只写第一条，第二条无声消失。回执那句 `2026-11-07（）` 是 `_todo_card:1748` 的
`pick.get("weekday") or ""` —— 引擎那份裸卡从来没带 weekday。

实测他那一轮（`sid=s08baec9e21`，20:25:14 → 20:25:18）：`options_n=2`、
`tools=[find_free_slots×4, propose_slots×2]`，下一句 `tool_calls=0` 却只回了一条回执。

### 0.5 课表操作**没有**同样的毛病（用户猜"好像也一样"，实测只中一半）

`propose_course_change` / `propose_timetable_change` 返回 `__proposal__:{kind:"timetable_change",…}`，
`engine.py:194/203` 抓得到 kind、前端 `:1835 tts` 分支接得住 ⇒ **单纯课表提案是好的**。
只有"同一轮里既调 `propose_slots` 又调课表提案"才会出事：`engine.py:211-213`
`same_kind = all(o.get("kind") == prop.get("kind") for o in self.options if o.get("kind"))`
—— 裸卡被 `if o.get("kind")` 滤空 ⇒ `all([])` 恒为真 ⇒ 两种混进同一个数组，
前端命中任一分支就 `return`，另一批被丢掉。补上 kind 之后这个空生成器也不再被骗。

---

## 一、改了什么（两处，前端一个字没动）

1. **`app/modules/planner.py` 新增 `normalize_slot_cards(cards)`**（放在 `propose_slots` 前面）：
   逐条补 `kind="todo_add"` / `weekday`（日期→星期，系统算）/ `minutes`（止−起）/ 缺事名补「待办」，
   非字典项丢掉。
2. **`app/agent/engine.py:180`** 把 `self.options = raw` 换成 `self.options = normalize_slot_cards(raw)`。

`git diff --stat`：**engine.py +10/−1、planner.py +43、tests/test_04 一处改口、新增 test_115**。
前端、提示词、工具清单、`_collapse_same_title_adds` 本体、`drawable` 判据 —— 全没动。

### 1.1 两处偏离单据（都是实测逼出来的，不是审美）

| # | 单据写法 | 实测 | 改成 |
|---|---|---|---|
| D1 | `if d and not item.get("weekday"): item["weekday"] = _weekday_name(d)` | `_weekday_name` 对 `2026-11-32`、`2026-11-5` 这类认不出的日期回的是**「未知」**（`:106` 的 `DAY_NAMES.get(..., "未知")`）—— 填上去等于替系统编一个它不知道的星期 | 加 `_iso_weekday(d)` 为真才填，认不出来就**留空**（A6 钉，M6 打在这：去掉这道闸 A6 红） |
| D2 | `if m > 0: item["minutes"] = m` | `to_minutes("99:00")` **不报错**、算出 5940 ⇒ 起止写歪时减法照样得正数，填进去是「约 5340 分钟」的假话 | 收成 `0 < m <= 24*60`（A5 钉，M5 打在这） |

### 1.2 `kind` 补成 `todo_add` 而不是新造一个 kind

单据 §1.1 那句"不开新 kind"是对的，实测再补一层理由：补成 `todo_add` 之后
① `_collapse_same_title_adds` 第一次接得上这条路（同名几张并成一张 `todo_slots`）；
② `_remember_todo_options` 的 `:1677` 那句 `if o.get("kind") and o.get("kind") != "todo_add": continue`
仍然放行 ⇒ 暂存照旧记成 `todo_pick`，**跟改前逐字一致**，没有多开一条出口。

---

## 二、护栏 `tests/test_115_slot_card_fields.py`（30 项，四批）

| 批 | 项数 | 钉什么 |
|---|---|---|
| A | 14 | 补字段本体：kind / weekday（**两组不同日期各算各的**）/ minutes / 起止写反不填 / 99:00 不填 / 坏日期不填「未知」/ 已有值不覆盖 / 空标题补「待办」/ 非字典丢掉 / 空与 None 不崩 / 不改调用方那份 |
| B | 7 | 收敛真的发生了：三条同名 → **1 张 `todo_slots`**、每段带 label/weekday/minutes、label 念的是那一天、**暂存与渲染同源**、单条不收敛、不同名不合并、裸卡原样进去仍是三张（B1 的反证） |
| C | 4 | **驱动真引擎**（`AgentEngine` + 脚本化 `ModelGateway`）跑一轮 `propose_slots`：捕获到的每条都带 kind/weekday/minutes，且这一轮确实调到了那只工具（否则 C3 是空跑）；外加 `self.options = raw` 已消失 |
| D | 5 | 红线：`drawable` 判据逐字未动、老【确认所选】那条还在、工具描述没被改成"模型必须传 weekday"、收敛器分组条件未动、`propose_slots` 仍在写侧名单里 |

### 2.1 改口一条：`test_04` 第四批

原来是 `result.get("options") == fake_options` —— 钉的是"一字不改原样退回"，
而"原样"恰恰是本轮要修掉的 bug 形状。按**本意**（引擎把工具参数捕获成 options 带回去）改成钉两头：
① 模型给的事实（id/title/date/start/end/minutes）一条没丢；② 补出来的那条带 `kind`。
改口后该批 3 项 → **4 项，全绿**。

### 2.2 变异九笔，零哑弹

| 变异 | 打在哪 | 红的项 | 命中的目标判据 |
|---|---|---|---|
| M1 删掉补 kind | planner | 7 | **B1**（退回三张裸卡，正是当前现场） |
| M2 删掉补 weekday | planner | 5 | A2 |
| M3 删掉补 minutes | planner | 4 | A3 |
| M4 weekday 写成常量「周六」 | planner | 3 | **A2-bis**（正控：换一组日期它得跟着变） |
| M5 minutes 去掉上限 | planner | 2 | **A5**（正控：D2 那道闸） |
| M6 weekday 无条件填（照单据原样） | planner | 1 | **A6**（正控：D1 那道闸） |
| M7 收敛改成无条件 | main | 1 | **B4** |
| M8 引擎改回 `self.options = raw` | engine | 3 | C1 |
| M9 原地改传入的那份 | planner | 1 | **A10** |

跑完三个文件 sha256 自比一致（`04098268149a` / `09649df994b7` / `a4492e1f07af`），无残留。

### 2.3 本轮自己踩出来的两条（都不是推演）

1. **M7 第一版是结构性哑弹**：只拆 `if len(members) < 2: continue` 完全不红 ——
   `_collapse_same_title_adds` 在 `:1606` 有一句 `if not options or len(options) < 2: return options`，
   单条**提前返回**，压根走不到 members 那一行。改成"两道守卫一起拆"才是"有人把收敛改成无条件"这个真实场景。
2. **A10 第一版是死判据**：写成 `"kind" not in bare(3,4,5)[0]` —— `bare()` 每次调用**现造一份新列表**，
   验的根本不是传进去那个对象，M9 照样绿。必须先把那份列表存下来再断言。这条改完 M9 才有牙。
3. 变异脚本自己差点把 `planner.py` 留在变异态：还原那行写成了写回"被改过的那份 buffer"。
   发现后按 `git diff` 复原，并把还原改成写回进循环前那份字节 —— 这也是"红得比预期少要查"的又一例。

---

## 三、真机走查（`TestClient` 进程内，不起服务、不动真实数据）—— 12 项 0 失败

| 步 | 做什么 | 实测 |
|---|---|---|
| W1 | 三条裸卡走真引擎捕获 → 过 `_remember_todo_options` | 屏幕上**只剩一张** `todo_slots`；三段 weekday 分别是那一天（周三/周五/周六）、minutes 都在、label 都在 ⇒ 前端 `slotsCards` 那一支接得住 ⇒ `openConfirm` 出确认条 |
| W2 | 在弹框里勾两条 → `POST /api/todos/batch` | 接口 `ok`，库里**真的两条**，日期就是勾的那两天 |
| W3 | 只有一段 | 留**一张 `todo_add`**（没被包成 todo_slots），weekday/minutes 也在 |
| W4 | 混合批次（同一轮裸卡 + 课表提案） | 引擎手里每条都带 kind ⇒ `same_kind` 那个空生成器不再被骗 |
| W5 | 老那条多行「确认 …\n确认 …」直接发聊天 | 出的是**批量卡**（列出两条、一个字不写），不是改前那种"回一句已加入、其实只写一条" |

⚠️ W5 这条要照实说：它**没有**把那句多行聊天变成"写两条"。老 `drawable` 那条通路本身还在
（本单不动前端，单据 §三 也明令不许改 `drawable` 判据），改的是**上游形状** ⇒
正常流程里学生已经不会再用那条老路（他看到的是确认条）。
留 W5 是为了钉住"就算有人从别处发来这种多行句子，也不许出现回话与落库条数对不上"。

## 四、回归与严格克隆

| | 读数 |
|---|---|
| 本机全量 | **通过 5928 项，失败 1 项（共 108 批）** |
| 严格克隆（tracked-only worktree @ `7b96837`） | **通过 5924 项，失败 1 项（共 108 批）** |
| 逐项差值 | **只落 `test_86`「C 批（数据）」6 ↔ 2** —— 运行时样本库不在提交里，是 #74 以来的长期差项，不是回归 |
| 两侧的红 | **同形同字**：`test_95` C 批 P1「把游泳挪到周五」→ `unclear` |

项数配得起来：#111 收口 5897 → +30（新文件 `test_115`，四批 ⇒ 批数 107→108）→ +1（`test_04` 那条改口从 3 项变 4 项）= **5928** ✓。

⚠️ 那唯一一笔红**不是 #115 引入**，是 #113/#111 都登记过的存量：`test_95` 用 `date=_iso(3)` 种待办，
今天周二 ⇒ 3 天后正好就是要挪去的周五 ⇒ "挪到它本来就在的那一天"，系统回诚实追问是对的，
红在**断言自己日历耦合**（`#69` 那一族，一行改法，等拍板）。诊断轮（改口前）是 5925/3，
多出来的两笔正是 `test_04` 与 `test_81` 那两条"钉原样退回"的既有判据。

## 五、提交

- `71928e0` fix(#115)：`planner.py` 新增 `normalize_slot_cards` + `engine.py` 捕获处改调它
- `7b96837` test(#115)：新文件 `test_115_slot_card_fields.py`（4 批 30 项）+ `test_04`/`test_81` 各一条改口
- 本 docs 笔：快照 + 施工单原件入库 + `ai-prompts/README.md` 两条 + `tests/README.md` 现值与清单行

动了 `app/*.py` ⇒ 8000 收尾重启。

## 六、遗留 / 待拍板（本单没动）

1. **`propose_slots` 与 `propose_todo_slots_tool` 两只工具功能重叠**，提示词里各写了一句"必须"
   （`planner.py:6593` / `:6595`）⇒ 模型有得选就会漂。本单选的是"挑哪个都对"（补形状），
   另一条路是"没得选"（把 `propose_slots` 从 planner 工具组摘掉）—— 后者要连带动提示词三处、
   `readonly.py:41` 那张写侧名单、以及 `test_94`/`test_82` 的相关断言，面大一圈，要不要做等他定。
2. **`note` 字段**（模型写给模型看的"课后整段空着"）仍然只进 JSON、前端不画。用起来是展示层的另一单。
3. **§1.3 单据那条"可选 hint"**（收敛出来的 `todo_slots` 给一句说明）没做 —— 它只是留一行空白，不难看也不错。

## 七、真实数据账

`app/data` 519 个文件、指纹 `3f514d326626`（本轮起点，与 #111 收口时的 515/`a9dfbe744988` 不同，
差的是用户今天自己测出来的）。本轮**一个字节都没写进真实数据**，两条证据：
`app/data/pending.json` 里**没有任何含 `115` 的会话键**（74 个键全是 `admin1|…` 的真实会话与历史探针号），
`app/data/student/admin1/todos.json` 里**没有**测试种的「占位」。
21:33:40 那条「健身房｜2026-11-07」是用户在浏览器上自己改期改出来的。

`app/data/kb.json` 仍是他在制品（20:19:40 那条「自行车维修点在那里」），**照旧不提交、不碰**。

⚠️ 记一条本轮踩到的纪律：`test_81` 那个文件的注释里早就写着"引擎 `run()` 收尾会 `save_pending`，
这一批**必须套沙箱**"—— 我给 test_115 写 C 批时照着套了，但**变异脚本本身**跑的是同一套引擎路径，
第一版没核这一条；事后专门回查真实 `pending.json` 才确认没漏（键名一个 `115` 都没有）。
"跑跑引擎而已"是最容易漏的那类：它不通过 `/api/chat`，也不碰 harness 那几个带 `_require_sandbox()`
的种子函数，所以一路没有任何一道闸拦它。

