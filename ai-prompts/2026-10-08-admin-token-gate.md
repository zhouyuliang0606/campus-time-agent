# 2026-10-08 管理侧加门：写权限收口之后，接口层还全是敞开的

## 用户 Prompt（摘录）

> 2. 写接口零鉴权 + 数据外泄：`/api/admin/settings`、`/api/admin/messages`、`/api/admin/kb`、
> `/api/workorders` 等接口全部无鉴权，任何能访问端口的人都能读走学生消息、改走密钥。
> 修复：加 `ADMIN_TOKEN` + `verify_admin` 依赖，给写接口挂鉴权；
> 学生"确认上报"工单的 `POST /api/workorders` 保留开放。

（同一轮里用户还列了 P0-3 XSS、P0-4 API Key 伪脱敏，本轮只做 P0-2，不混做。）

## AI 研判与决策

先认账：这一条说的是实情，而且比"写接口"三个字更宽——`GET /api/admin/messages`
能**读走全部学生消息**，`GET /api/admin/settings` 能把当时配置的模型密钥打码串之外的一切
配置关系暴露出来，`POST /api/admin/settings` 更是可以直接把项目的模型换成攻击者自己的账号。
"零代码维护"这个卖点的隐含前提是**只有管理员在点后台那个按钮**，这个前提此前没被任何代码保证。

**边界怎么划，是这一轮唯一真正要动脑的地方**（划错比不划更糟）：

- 管理侧读写（学生消息、用量、知识库、API 配置、人格、工单列表与处理、发通知）→ 要口令。
- 学生自己做的事（对话、日程、课表、上报工单 `POST /api/workorders`、存档自己说的话
  `POST /api/student-message`、驿站页显示客服名 `GET /api/persona`）→ **不要口令**。
  学生没有口令，也不该有。这条线一旦破，演示当场就废，而破坏是**单向的**：
  挂多了，管理台看起来照常能用，学生侧的坏要到有人真去点驿站才发现。
  所以 `tests/test_32` 有一半项数在验"谁没被挡住"，和验"谁被挡住了"同等重要。

**没配口令时锁死（403），不是放行**。这是本轮第二个决策，也是我认为最值得留档的一个：
`fail-open`（没配就当这功能没开）对新克隆的人极不友好——照 README 启动，那个状态**恰好就是"没配"**，
于是这道门在"接手者第一次跑起来"这个最关键时刻等于不存在，跟没修一样。
`fail-closed` 的代价（空面板看着像程序坏了）用 README 第 4 步那段说明还掉，见 `40b8ef4`。

**比对用 `hmac.compare_digest`，不用 `==`**。`==` 在第一个不匹配字节就返回，理论上可被计时差
逐位猜出口令。本项目不对公网暴露、这个威胁很轻，但既然写门就按对的写法写，成本是零——
而且这条写在 `admin_guard.py` 的 docstring 里，下一个改这里的人不会顺手"简化"回去。

**为什么不那么修**：

- ❌ 只给写接口挂门、`GET` 全放。数据外泄那半条就白修了：`GET /api/admin/messages`
  是这一条里损失最大的一项，它根本不需要"写"权限就能拖库。
- ❌ 上 session/cookie 或 JWT。这个项目没有登录体系，也不该为一个演示后台长出账号体系；
  一个环境变量 + 一个请求头是此刻够用的最小解，多出来的复杂度是给未来的需求付的债。
- ❌ 把口令写死在一个默认值里"方便演示"。等于把门做成装饰，且一定会被提交进公开仓库。
- ❌ 在 `admin.html` 里藏口令。前端任何常量都是公开的；改成一进页面由管理员手输、
  只存 `sessionStorage`（关标签即清），不落 localStorage。
- ✅ 一个 `admin_guard.py` + 一个 `Depends` + 一份前端封装 + 一份专职护栏，四件事各一个 commit。

## 具体改动

| 文件 | 改了什么 |
|---|---|
| `app/admin_guard.py` | **新增**。`verify_admin` 依赖：读 `X-Admin-Token`，与 `get_admin_token()` 做 `hmac.compare_digest`；服务器没配口令 → 403 + 一句怎么配的人话，口令不对 → 401。整个 docstring 在写"边界怎么划"，不给后人留误改的空间 |
| `app/config.py` | `ADMIN_TOKEN_ENV = "CAMPUSTIME_ADMIN_TOKEN"` 常量 + `get_admin_token()` **每次现读环境变量**（不是 import 时缓存），这样测试能在同一进程里换口令验两种失败路径 |
| `app/main.py` | 14 个管理侧接口挂 `dependencies=[Depends(verify_admin)]`；段首写明哪三个接口**有意不挂**及原因（`POST /api/student-message`、`POST /api/workorders`、`GET /api/persona`） |
| `.env.example` | 加 `CAMPUSTIME_ADMIN_TOKEN=在这里填一个只有你知道的口令` + 三行说明（不填=管理侧全锁、学生侧照常） |
| `app/static/admin.html` | 顶部「🔒 管理口令」卡片；全页**唯一一份** `adminFetch()` 封装（自动带头、401/403 → `adminLocked=true`），15 处管理接口改走它；轮询统一包 `_poll(fn)`，锁上之后不再空转刷错误；`/api/upload`、`/api/notices`、`GET /api/persona` 保持裸 fetch |
| `tests/_harness.py` | `make_client(headers=None, admin_token=ADMIN_TOKEN)`：默认带测试口令，传 `""` 即模拟"没口令的人"；同时设/清 `CAMPUSTIME_ADMIN_TOKEN` |
| `tests/test_32_admin_token_gate.py` | **新增**护栏，25 项 / 5 批（A 带口令能通 / B 不带口令进不去且**真没写成** / C 没配口令时锁死 / D 学生侧照常 / E 结构自证） |

## 验证结果

- **新护栏**：`test_32` 25 项 / 0 失败。
- **E2 为什么从"数源码"改成"问路由表"**：第一版我数 `main.py` 里
  `dependencies=[Depends(verify_admin)]` 这个字面量出现几次，得 15、比实际多一个——
  因为**我写的那段解释性注释里就有这串字面量**。断言的口径本身在说谎，这类误报以后会让人
  直接改成 `>= 14` 之类的松断言。改成 introspect `app.routes`：遍历 `r.dependant.dependencies`，
  看 `call is verify_admin`，精确得到 14 个，注释、字符串、死代码一律干扰不了。
- **B 批不只验"进不去"，还验"没写成"**：401 之后回头查知识库条数**不变**、
  工单状态**没被改**——只断状态码的护栏挡不住"报了错但已经写了"。
- **反向验证**（两种变异，分别红在不同批）：
  1. 把 14 个 `Depends` 全删 → B（不带口令进不去）、C（没配口令锁死）、E2（恰 14 个）三批红；
  2. **多加一道门**：给 `POST /api/workorders`（学生确认上报）也挂上 →
     D 批红、E3/E4 红。这条是我特意做的：护栏只会喊"漏挂"而不会喊"多挂"的话，
     它就只教会下一个人别漏，教不会他别过界，而过界在这一轮里是更贵的错。
     两次变异都已还原。
- **全量自测**：`1897 项通过 / 3 项失败（共 33 批）`（上轮 1872/32，+25 = test_32）。
  3 项红为存量，与本轮无关（test_06 两项、test_23 一项，上轮已用回退 HEAD 证明过）。
- **真机复验**（端口 8000，浏览器实测，非 curl）：
  1. `/admin` **未填口令** → 五个面板显示占位、口令条给出「🔒 口令不对（401）：管理口令不对或未提供
     （请求头 X-Admin-Token）。」，没有任何面板泄漏数据；
  2. 填入口令 → 知识库、用量、API 配置（打码串）、收件箱全部照常渲染，
     发通知、改人设这些操作没有回归；
  3. `/student` 对话、日程、课表、报工单全流程 Console **零报错**——学生侧确实不需要口令；
  4. 收尾停服务，`app/data` 下运行数据逐个 md5 与探针前备份比对**全一致**，演示数据零改动。

## commit

- `137c7e8` 安全：给管理侧接口挂上口令门（`CAMPUSTIME_ADMIN_TOKEN` + `X-Admin-Token`）
- `49a66ab` 管理台：顶部加口令条，十几个管理接口改走一份 `adminFetch` 封装
- `c0798ba` 测试：新增 `test_32` 钉住这道门（25 项 / 5 批，含"挂多了"也要红）
- `40b8ef4` docs(README)：补管理口令的复现步骤
- 本快照与 `ai-prompts/README.md` 索引各一次独立提交

## 下一项待办（未开工）

用户列的 P0-3（XSS：`student.html:966` 的 `who`/`module`、`:2698` 的 `data-key` 未转义直进
`innerHTML`，且全项目 6+ 份 `escape` 实现标准不一、多数不转双引号）与
P0-4（API Key 伪脱敏：文案说"密钥不上传服务器"，实现里每条聊天都把含 `api_key` 的
`model_config` 发到 `/api/chat`）尚未动。

另记一笔仓库级不实陈述，已单独报告、未获批改动：README「验证功能有没有坏」那段写的是
「失败 0 项」，与实际 3 项存量红不符；写权限那一轮也提到过。要么修那 3 项，要么改那句，
不该让它继续挂着。
