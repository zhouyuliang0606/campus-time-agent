# 2026-11-01 · #97 驿站端的背景跟着学生端跑 —— 驿站保留纯色，一个键都不读

施工单原件：**同批入库** `2026-11-01-ticket97-station-pure-backdrop-drop-in.md`（源文件在写作侧
`D:\Workbuddy\2026-10-12-21-36-55\prompts\97_驿站端背景跟着学生端走_两团人共用一个键_drop-in.md`，
12834 字节 / CRLF；入库只把行尾转成 LF，正文逐字未改）。
性质：一处**纯前端**的行为收回 —— `app/static/station.html` 不再读学生端的外观键；`app/*.py`、门、
`plan.html`、`admin.html` 一律不动。起手 `HEAD = a7fdac2`（#99 收口），工作区干净。

用户口径两句，单子的 L1 是照第二句自己作废的：

> 「驿站为什么要和学生同步，学生改的东西是学生端的啊，你驿站学生又登不了」
> 「驿站保留纯的」—— 不是「驿站有自己的背景」，是**驿站不要背景**。

## 〇 前提实测（动手前逐条量，四条与单据有出入）

| 单据说法 | 我这侧的重量 |
|---|---|
| `station.html:433` 直接读学生端的键，且默认 `forest`（= 有背景图） | ✅ 对，逐字：`try { key = localStorage.getItem('ct_bg') \|\| 'forest'; } catch (e) { … }` |
| `:427` 注释写「与学生页 / 日程页读**同一个** localStorage 键 ct_bg」 | ✅ 对 |
| `:217` 注释写「设置 → 背景 → 不用图」摘掉总闸，而本页**没有设置页** | ✅ 对（本页「背景」三处全是注释，零 UI） |
| `test_56:209-218` F 批那条「有意的例外」 | ✅ 对（例外段落实际从 `:207` 的标签文字开始，判据本体在 `:213-218`） |
| §0.4：「`localStorage` 在脚本里只出现在**两行**（`:427` 注释、`:433` 代码）」 | ⚠️ **部分成立**：全脚本含这个字样的是 **5 行** —— `:427` / `:433` 之外还有 `:543`–`:545` 三行「为什么用 sessionStorage 不用 localStorage」的理由注释。但**代码里只有 `:433` 一行**；剔掉注释（test 里的 `code_of()` 就是干这个的）后 = **0** ⇒ 「改完可以零 localStorage」这个结论**不变**，严格版判据可行 |
| §0.5：`admin.html` 全文件 0 处 `ct_bg` / 「背景」 | ✅ 对（两个都数了，各 0） |
| §0.5：`plan.html:258` 读 `ct_bg` 是对的，本单不动 | ✅ 对（`:252` 是它的注释、`:258` 是代码） |
| §0.5：`test_44` J 批在 `693-797` | ⚠️ 函数体实为 **`693-783`**；`:797` 是 `main()` 里 `batch_station_glass(ck)` 那一行调用 |
| §五：基线 **5631/4** | ❌ **过期**：那是 #94/#96 之间的数。现基线 **5644 通过 / 4 失败（99 批 = 5648 项）**（#99 收口时的数，本单起手复核过） |
| §0.4 末：改完这页可以零 localStorage | ✅ 成立（见上：代码里本来就只有一行） |

行尾：`station.html` 工作副本 **CRLF**（1239 行 / 77261 字节）；`tests/test_56_station_shell.py`
工作副本与 HEAD blob 都是 **LF**（仓储约定，`core.autocrlf=true` 只在 Git 碰它时才提醒）。
两侧都用**字节**打补丁（`open(...,'rb')` + 显式 `\r\n`），不用文本模式 —— 文本模式会把 CRLF 悄悄翻成 LF。

## 一 改了什么（一笔 `9f92bc5`，`app/static/station.html` 4/3）

`applyStationBackdrop()` 里那三行换成两行：

```js
// 改前
let key = 'forest';
try { key = localStorage.getItem('ct_bg') || 'forest'; } catch (e) { /* 隐私模式读不到就用默认 */ }
const on = (key !== 'none') && window.CT_BG_SRC && window.CT_BG_SRC[key];

// 改后（连同两行理由注释）
const key = 'none';
const on = false;
```

- `on` 恒假 ⇒ `html.gx-glass` 永远不加 ⇒ `station.html:217-290` 那半段玻璃规则、`glass.css` 的
  `.tk-rain` token 与玻璃层**全部不生效**，页面回到基线浅色（`#f4f6fb` / `#1f2733`）。
- **为什么把 `window.CT_BG_SRC[key]` 和 `classList.toggle('gx-glass', !!on)` 那两段原样留着**：
  `test_56` A 批钉着 `bg.js` 必须先于 `window.CT_BG_SRC`（顺序判据），`test_44` J1 钉着这张表、
  J11 钉着 `toggle` 那一行。它们现在落在 `on` 恒假的不可达分支里 —— **行为没了、接线还在**，
  将来要开只改这一行。
- 没动：`#gxBg` 那张 `<img>`、`gx-veil`、`glass.css`、`bg.js`、值班那套 `sessionStorage`、顶栏返回。

## 二 护栏（一笔 `ef31a67`；`test_56` F 批 9 → 11 项，文件 43 项 0 失败）

- **收回，不是改口**：载体那条从 #93 的「配对判据」回到严格版 `and "localStorage" not in stc`；
  docstring 里 #93 那个「有意的例外」整段重写成「#97 收回了，理由：驿站是店员机器」。
  实测输出 `localStorage 出现 0 次；那几行=[]` —— 这里是**真的零**，不是被注释豁免。
- **F-x 正控**：`stc.count("sessionStorage") >= 2`（实测 3 处：getItem、setItem、注释外的引用各一）
  —— 防的是「把整个存储层删干净」也当通过。
- **F-y**：`ct_bg / ct_station_bg / ct_bg_scrim` 在脚本代码里一个都不许出现；
  三根正控 —— ①`src="/static/bg.js"` 还在（证的是「不读键」，不是「把 bg.js 删了」，
  删了 `test_44` J1 会红，那属于 L2 选项乙）②**我自己加的那一根**：同一个正则喂一句必中样本
  `localStorage.getItem('ct_bg')` 必须命中（防括号/转义写坏成死断言 —— 单据只给了第 ① 根，这是偏离项）。
- 模块 docstring ⑤ 与 F 批 docstring 同步改口；**断言本体之外的标签一个字都没动**。

## 三 变异（`D:\campustime-scratch\mut97.py`；原文 = 工作副本按字节备份，跑完逐字节还原）

基线先跑一遍（43/0、203/0）确认「红不是存量」。每笔都断言：锚点恰好命中 1 次、写盘后读回一致、
md5 变了、**变异文本真的落在测试读的那份文件里**（这就是「走到的」）。

| 变异 | `test_56` | `test_44` | 读法 |
|---|---|---|---|
| M1 读回 `localStorage.getItem('ct_bg')` + 还原 `on` 表达式 | **红 2**（载体严格版 + F-y） | 203/0 | 判据有牙；`test_44` 本来就不钉键名，不红是对的 |
| M2 `key` 不变、加一行 `getItem('ct_station_bg')` 兜底 | **红 2**（同上两条） | 203/0 | 换键也不许 —— 正是单据「不换键」那条 |
| M2b **不碰 localStorage**，只在代码里提一次 `'ct_bg'` | **红 1**（只 F-y） | 203/0 | 把 F-y 从载体那条里拆出来量：它的牙**不是**靠 `localStorage` 给的 |
| M3 `const on = (key !== 'none')` | 43/0 | 203/0 | **按设计不红**：`key` 恒 `'none'`，等价写法 |
| M4 `const on = window.CT_BG_SRC && window.CT_BG_SRC[key]` | 43/0 | 203/0 | **按设计不红**：`CT_BG_SRC['none']` 是 undefined ⇒ 假 |

还原后 md5 `eca56a88f1ea` 与备份一致、长度 77645 一致 ⇒ 零哑弹、零残留。
M3/M4 照单据特意登记：这单的正确形态有几种等价写法，判据只该钉「读不到学生端的键」和「页面是纯色」。

**真机那一笔（M1 的牙在屏幕上）**：把副本服务 serving 的那份 `station.html` 改成 M1 形状、
`ct_bg` 设成 `deep` ⇒ 变回老样子：`htmlClass="tk-rain gx-glass"`、`bodyBg rgba(0,0,0,0)`、
`#gxBg display=block src=/static/bg/deep.jpg`、字色 `rgb(234,243,236)`。还原文件后再读 ⇒ 纯色。

## 四 真机两步（副本服务 8067 + 副本数据；真实 `app/data` 一个字节没碰）

副本树 `D:\campustime-scratch\live97`（`app` 整树拷贝 + `app/data` 拷贝但**不含 `settings.json`**，
所以没有 Key），同一台浏览器、同一个源（localStorage 同源共享是这单唯一的暴露条件）：

| 步 | 操作 | 读数 |
|---|---|---|
| ① | `ct_bg=deep`（模拟学生端选了「深夜」）→ 刷新 `/station` | `htmlClass="tk-rain"`（**没有** `gx-glass`）、`bodyBg rgb(244,246,251)`、`bodyColor rgb(31,39,51)`、`#gxBg display=none / src=null / rect [0,0,0,0]`、`gx-veil display=none` |
| ② | `ct_bg=none`（「不用图」）→ 刷新 `/station` | 与 ① **逐字段相同** |

第 ② 步是关键：它证的是驿站**根本不参与**这个开关，不是「碰巧被设成同一个值」。
顺手核了单据 L0 末尾那条：`(400,6)` 处的元素是 `.topbar`，`#gxBg` 那一层 0×0、`gx-veil` 不显示
⇒ 纯色页顶部**没有白条/空白块**。控制台 0 条消息；探针只写了 `ct_bg` 一把键，收尾 `removeItem` 还原成
探针前的 `null`（源上还剩一把本来就有的 `campustime_session`）。
⚠️ 浏览器 surface 本轮依旧不可见（`NATIVE_BROWSER_VIEWPORT_UNAVAILABLE`），所以以上全是
DOM / CSSOM 读数，**没有截图**；视觉"好不好看"这单也没人问，问的是"有没有背景"。

副本拆干净：8067 已停（pid 267592）、`live97/` 整树删（删前扫过 `sk-` / `ghp_` / `Bearer` /
32+ 位十六进制，零命中；副本里也没有 `.env` 与 `settings.json`）。真实数据指纹
`ace5c1f02faf` / 466 文件，探针前后一致。

## 五 回归

- 本机全量 **通过 5646 项，失败 4 项（99 批 = 5650 项）**，stderr 0 字节。
- 严格克隆（tracked-only worktree @ `ef31a67`）**通过 5642 项，失败 4 项（5646 项）**。
- 两侧差 **4 项，逐项只落在 `test_86`「C 批（数据）」6↔2**（运行时样本库不在提交里，长期差项）；
  按文件逐一对：99 个文件里只有它一项不同。
- 那 4 条红两侧**逐字相同**，仍是 `test_95_retime_weekend.py` 的 A6 / A9 / D3 / D3-c
  （挂账 #69 那族：日期撞成周日），**不是回归**。
- 项数配得起来：#99 收口 5648 项 + 本单 `test_56` F 批 9→11 **+2** = **5650 项**
  = 5646 通过 + 4 失败；克隆 5650 − 4（数据批）= 5646 = 5642 + 4 ✓。
- 只改了 `app/static/` ⇒ **8000 没重启**（静态文件按请求读盘），`test_44` 203/0 不变。

## 六 L2：那半段玻璃规则怎么办（**今天不动，等拍**）

改完以后 `station.html:217-290` 那 43 行玻璃 CSS 与 `glass.css` 的 token 成了**永不生效的代码**，
`test_44` J 批 37 条判据守着的也是一组不会生效的效果。两种处置：

- **甲：留着** —— 43 行 CSS + 37 条判据是负债；好处是将来要开，只改 `const key` 那一行。
- **乙：连 J 批一起删** —— 干净，但 J6 暗/亮部 ≥4.5、J7 反控、J8 纱浓度那些**实测出来的对比度资产**一起没了，
  将来重做要重算。

演示日改行为就够了，删代码不产生可见收益、只产生回滚风险 ⇒ **本单不动**。

## 七 还开着的（编号，等他一句话）

1. L2 选甲还是乙（上面 §六）。
2. 学生端 `ct_bg` 那一层另有一单（**#98**：学生之间要各改各的）—— 与本单不冲突，`plan.html` 照旧读。
3. 挂账第 ④ 条还在：`admin.html` / `confirm.html` / `login.html` / `settings.html` 四页的玻璃档要不要接。
4. #96 那三条（演示口令能不能改 `base_url` / 公网实例没 env 要不要求拒启 / 演示数据脱敏）、
   #99 留的那把口令要不要换 —— 都还没动。
5. 更早的挂账原样继续：#88 四条、X1/X2/X1-bis、#141/#142/#143、#52、`test_95` 那 4 条存量红。
