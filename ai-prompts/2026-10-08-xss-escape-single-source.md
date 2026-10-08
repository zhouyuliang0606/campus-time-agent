# 2026-10-08 XSS 收口：转义只留一份，并且承认它有管不到的地方

## 用户 Prompt（摘录）

> 3. XSS：student.html:966 的 who/module、:2698 的 data-key 未转义直进 innerHTML；
> 全项目 6+ 份 escape 实现标准不一（多数不转双引号）。
> 修复：统一一份覆盖属性上下文的转义函数，全量审计 innerHTML。

## AI 研判与决策

先把"标准不一"到底差在哪查清楚，因为它决定了"统一"要统一成什么。仓库里实际有 **7 份**实现
（不是 6 份），三档深浅：

| 实现 | 位置 | 转义表 |
|---|---|---|
| `escapeHtml` ×4 | `admin.html:496`、`settings.html:96`、`station.html:138`、`student.html:1664` | `& < >` —— **引号一个不转** |
| `esc` ×2 | `confirm.html:166`、`plan.html:215` | `& < > "` —— 漏单引号 |
| `escQ` ×1 | `student.html:1773`（`escapeHtml` 外面再补一层 `"`） | `& < > "` —— 同样漏单引号 |

所以：**没有一份转过单引号**，而且 4 份连双引号都不转。用户说的"多数不转双引号"是准的，
真实后果是属性位置（`title="..."`、`value="..."`、`data-key="..."`）**长期等于没设防**——
文本上下文漏 `<` 才算破口，属性上下文漏引号才是破口，而这正好是那 4 份唯一在做的事之外。
`escQ` 那份最有说服力：作者显然已经发现 `escapeHtml` 挡不住属性，于是在同一个文件里
又抄了一份补丁——**同一个洞三种深浅，还互相以为对方补过了**。

判据因此定成"一份函数同时管住文本与带引号属性"，而不是"给每处先判断该用哪个函数"：
`& < > " '` 五个全转。让调用点去判断"我这处在不在引号里"是把洞重新发回去。

**审计口径**（这是"全量审计 innerHTML"唯一有意义的做法）：自写扫描器（`D:\campustime-scratch\audit_escapes.py`，
不属于仓库），模板字符串与 `+` 拼接两种写法都扫，命中条件是"**访问了外部数据的属性、又没走转义函数**"，
不是简单 grep `${`。粗筛 125 处 → 逐条看数据来源 → 其中 **21 处是真裸点**，其余是同名表达式已经
包在 `esc()` 里、页面自己的常量表（`MODULES`、`WD`、`tiles`）、数组长度与日期算术。
21 处全部补转义，并在护栏里逐条钉住（C 批 `REQUIRED` 21 条），剩下的"看着像但不是"进
`ALLOWED_RAW` 白名单，**每条必须写理由**，另加一条 E2「白名单不许攒用不到的条目」——
白名单只会烂掉，不会自己变干净。

**为什么不那么修**：

- ❌ 只把 `student.html:966` / `:2698` 两处点名位置补上。这两处确实是破口，但同一份数据
  （`who`、`module`、`p.key`）在另外 5 个页面各有一份不转引号的实现，补两处等于承认"6+ 份实现"这个事实。
- ❌ 保留 7 份、给它们加注释说明各自适用场景。适用场景差别（转不转引号）本来就是 bug 的成因，
  不是设计维度。
- ❌ 用 `textContent` 全面替代 `innerHTML`。方向对、代价不对：全站有几十处是"一小段结构化 HTML"
  （聊天泡、事件块、时间轴块），换掉要重写渲染层，本轮只修安全边界，不动 UI 逻辑。
- ❌ 拿 grep 当护栏。这个仓库已经反复验证 grep 会说谎（test_31 的 `propose_*` 因为 docstring 里
  写了"我不写库"被误判过）。转义护栏的 B 批因此**不读源码，用 node 真执行 `esc.js`**。
- ✅ 一份实现 + 一处引入 + 裸点补转义 + 把"转义救不了"的那类拆结构 + 专职护栏 + 反向验证。

### 这轮最大的收获：`esc()` 有一类位置根本救不了

`plan.html` 的待办按钮原本长这样：

```js
onclick="toggleTodo('${t.id}','${t.status}')"
```

这是**内联脚本上下文**：属性值先被 HTML 解析器解码（`&#39;` → `'`），再交给 JS 解析器当字符串字面量。
转义函数在这里是自欺——它转出来的实体正是被解码回来的那一步。

而且这条链是**真能走到**的，不用构造理想情况：
`POST /api/todos` 建一条待办 → `PUT /api/todos/{tid}` 把 `status` 写成 payload；
`main.py:3966` 的 patch 只按 **key** 白名单过滤（`title/date/start/end/note/status`），
**不校验取值**，payload 原样落库；`/plan` 渲染时把它塞进上面那个 onclick。
`app/static/plan.html` 里那两个按钮因此是本轮唯一"改结构"而不是"加转义"的修复：
改成 `data-act` / `data-id` / `data-status`（属性上下文，`esc()` 够用）+ 事件委托，
属性值不再经过 JS 解析器。顺带把 `fetch("/api/todos/" + id)` 补成 `encodeURIComponent(id)`。

同一条认识适用于 URL：`esc()` 不管 URL 上下文，拼 URL 用 `encodeURIComponent`，
读回 `dataset.x` 拿到的是**已解码的原值**，所以只在拼 URL 那一刻 encode（原来有一处
`encodeURIComponent` 写属性、`decodeURIComponent` 读回来再拼，绕了一圈还漏）。

## 具体改动

| 文件 | 改了什么 |
|---|---|
| `app/static/esc.js` | **新增**，全站唯一实现：`& < > " '` 全转；docstring 写清"它管不到内联 JS 与 URL" |
| 6 个页面 | 删掉 7 份本地实现，各加一行 `<script src="/static/esc.js"></script>`（`login.html` 无内联脚本，不引入、也不算违规）；调用点统一 `esc(`（改口计数 admin 23 / student 64+10 / station 6 / settings 3 / confirm·plan 换名） |
| `app/static/student.html` | `addMsg` 的 `${who}`/`${module}`、周表 `x.tm`、日程 `t.start/t.end`、快递 `p.label`/`p.stay_days`/`p.arrived_at`、外卖 `o.eta`、工单 `w.ts`、通知 `n.ts`/附件 id、设置页 `p.emoji` 与 persona `data-key`、`card.minutes`/`o.minutes` 补转义；`.todo-done`/`.todo-del` 属性写 `esc(t.id)`、fetch 才 encode（删掉 `decodeURIComponent` 往返）；`richText` 保持**先转义再还原 `**加粗**`** |
| `app/static/admin.html` | `m.ts`/`w.ts`/`n.ts` 补转义；`name` 兜底也要转（它会回落到原始 `m.module` 串）；附件 id 与工单 id 拼 URL 前 encode；`d.file.chars` 走 `Number()` |
| `app/static/plan.html` | 待办两个按钮拆掉内联 onclick（见上）；`${WD[...]}` 之外漏的 `t.start - t.end`、`it.tm`、`it.lead` 补 `esc` |
| `app/static/station.html` | `addMsg` 的 `${who}`/`${module}`、trace 的 `t.phase`/`t.action`/`t.answer`、上传文件名与字数 |
| `app/static/settings.html` | `${p.emoji}`（`name`/`desc`/`style` 本来就转了，emoji 是唯一漏的） |
| `tests/test_25_todos_panel_and_nav.py` | 判据改口：原来钉字面量 `encodeURIComponent(t.id)`，本轮把属性写入换成 `esc(t.id)`、encode 挪到 fetch，**钉写法会把正确的重构判成回归**；改成钉语义三条（主键取 `t.id`、`data-id` 走 `esc()`、PUT/DELETE 的 URL 走 encode），且都限定在 `loadTodosView` 函数体内 |
| `tests/test_33_escape_single_source.py` | **新增**护栏，24 项 / 5 批（A 唯一实现 / B 转义语义 / C 21 点位回归 / D 内联脚本位置 / E 属性文本裸插值） |

## 验证结果

- **新护栏**：`test_33` 24 项 / 0 失败。
- **反向验证**（`D:\campustime-scratch\reverse_p03.py`，三种退化各装回去一次，跑完当场还原）：
  - M1 从转义表删掉 `'"': "&quot;",` → **B1 / B2 / B3 三项红**；
  - M2 把 `${esc(module)}` 改回裸插值 → **C1 / C2 / E1 三项红**；
  - M3 把待办按钮改回内联 onclick → **D1 / D2 两项红**；
  - 基线与还原后均为 24/0，工作树无残留（`git status` 只剩既有的 `intent_test_report.md` 行尾噪声）。
- **B1/B2 曾被 M1 抓出假绿**（本轮第二个自我纠错）：删掉双引号那行后，JS 拼出的是
  `a&#39;bundefinedc&amp;d&lt;e&gt;f`——"裸字符全都消失了"于是通过。假绿的成因是
  替换表缺键时 JS 返回 `undefined` 而不是抛错，**"转成了 undefined"看起来像"被挡住了"**。
  修法是两条：B1 额外检查输出里不许有 `undefined`/`null` 残骸；B3 改成逐 payload
  **HTML 解码 round-trip**（`html.unescape(esc(p)) == p`），既防漏转也防多转、防把字符吃掉。
- **探针的"空断言"教训**（第一个自我纠错）：Playwright `.click()` 因元素不可见而超时，
  我却把"脚本没执行"当成通过——**没点到的按钮当然不会执行注入**，这是空断言。
  改成在 `page.evaluate` 里用 DOM 原生 `b.click()`，并新增「按钮确实点到了」
  +「点击有副作用（文案从『标记完成』翻成『撤销完成』）」两条：先证明处理函数真跑了，
  再谈"没执行"。补上这两条之后，改前那一版才露出 `raw_executed=[False, True]`
  ——**点一下按钮，注入的 JS 真的执行了**；改后 `raw_executed=[False, False]`、
  `raw_svgs=0`、`raw_onclick=0`，而按钮功能照常。
- **真机复验**（端口 8000，`CAMPUSTIME_ADMIN_TOKEN` 命令行注入，Playwright 打两条真实可达路径）：
  ① `POST /api/student-message` 的 `module` 字段 → `/admin` 收件箱（这个接口**有意不加口令**，
  学生端每轮对话都要调，所以任何能连到端口的人都能往管理员浏览器里塞字符串）；
  ② `POST /api/todos` + `PUT /api/todos/{id}` 的 `status` → `/plan` 按钮。
  判定只看两件事：payload 有没有真的执行（`window` 上的记号）、有没有真的生成元素（`svg`/加载失败的 `img`）；
  **页面上出现原始 payload 文本是期望行为**（当字显示出来就对了）。
  三处结果（admin 收件箱 / plan 待办 / student 待办+设置）全绿，`student` 那处还额外验了
  「性格卡片仍在」——`data-key` 加转义不能把选择器改坏。
- **全量自测**：`1923 项通过 / 3 项失败（共 34 批）`。3 项红为存量（test_06 两条、test_23 一条），
  与 P0-1 快照里记录的同一批；本轮修掉了 test_25 那条因写法变更而误报的红。
- **数据零改动**：探针前后 `app/data/` 128 个文件 md5 逐一比对**完全一致**；
  `grep -rln "9731" app/data/` 只命中 `intent_samples.json` 里一个时间戳数字（`1790399731.06…`），
  探针那条待办与消息按 id 精确删掉、`student_messages.json` 从备份还原，无 payload 残留。

## commit

- `d4589f6` 前端：七份转义实现合成一份（app/static/esc.js）
- `6ca8128` 安全：补上审计查出的裸插值，并把一个内联 onclick 的数据注入拆掉
- `881ed29` 测试：test_25 那条钉住旧写法的判据跟着转义收口改口
- `d0089c5` 测试：新增 test_33 钉住"转义只有一份、且真能挡住属性上下文"（24 项 / 5 批）
- 一处**口径更正**（`7ecdf78`，单独 commit）：`esc.js` 的注释原先写"七个页面各抄一份、其中 5 份不转引号"，
  复查后是**六个页面 7 份实现、4 份完全不转引号、7 份都不转单引号**；
  同一个错误也写在 `d4589f6` 的 commit message 里。历史不动（改本地 commit message 会让
  评审看到的提交序列跟当时做的位置不一致），改的是会继续被人当事实读的那份代码注释。
- 本快照与 `ai-prompts/README.md` 索引各一次独立提交

## 下一项待办（未开工）

P0-4：API Key 伪脱敏——`student.html` 文案写"密钥不上传服务器"，但每条聊天都把含
`api_key` 的 `model_config` 发给 `/api/chat`。本轮只收 XSS，不混做。
