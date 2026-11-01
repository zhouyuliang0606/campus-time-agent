# 2026-11-01 · #98 学生端外观偏好按学号分开 —— 同一台浏览器换账号不再互相继承背景

用户原话只有一句：「学生两个账号为什么一个改了背景另一个也改了，要分开」
性质：**纯前端偏好存储**（`app/static/student.html`、`plan.html`、`login.html`），不碰接口、不碰权限、不碰数据文件。
起手 `HEAD = 0c72d11`（#97 收口），工作区干净；本单四笔代码/测试 + 本快照。
本轮还有一个**实机才翻出来的回归**（§〇.6 → §一.2）：静态判据与 node 抽段全绿，浏览器里一登录就 ReferenceError。

## 〇 前提实测（动手前逐条量）

| 前提 | 实测 |
|---|---|
| localStorage 是**按 origin** 的，不是按账号 | ✅ 这是根因本身：同一浏览器里两个学号共用一份 `localStorage`，裸键 `ct_bg` 必然互相继承 |
| 外观键共 8 个 | ✅ `ct_bg / ct_gx_scrim / ct_gx_vm / ct_gx_bd / ct_gx_core / ct_gx_lv / ct_gx_fx / ct_gx_refine`（`GX_SID_KEYS` 逐字核过） |
| 学号有三个来源，权威是 cookie | ✅ `/api/whoami`（cookie `ct_student`）权威；`campustime_student` 是登录页写的**同步提示**（首屏不等 whoami 就能套对背景）；登录页路由是 `/`（`/login` 返回 404，写脚本时踩过） |
| 背景档 | ✅ `GX_BGS` 五档：`forest`（雾松林）/`deep`（深空）/`dusk`（星野）/`milky`（银河，CC BY）/`none`；撤掉的 `park`/`space` 由白名单查表回落 `forest`，不拼路径 |
| 裸键写点只在设置面板 | ✅ 换图那支写 `ct_bg`、四个旋钮与动效各一处；读点统一过 `gxVal`；`plan.html` 自带 `planBgKey()` |
| `test_44` 有一条锚点会被本单顶红 | ✅ 原文取**第一处**出现的 `applyBackdrop();`；#98 在 `gxSetSid` 里加了一处（缩进 6 空格、位置在定义之前）⇒ 改成按缩进认顶层调用（4 空格），见 §二.3 |
| 作用域 | ❌ **`gxSetSid` 定义在三栏外壳 IIFE 里**（`(function(){` 起于 `student.html:3123`、`})();` 收于 `:3679`），而 `initStudentIdentity` 在 `:2070` 的顶层 —— 跨层直呼在静态文本上毫无异样 |

## 一、改了什么

### 1. `db509e3` 分键 + 老键认领 + whoami 复套（3 文件 70+/13-）

- `student.html`：`GX_SID_KEYS` + `gxSid`（先同步读 `campustime_student`）+ `gxKey(k) = k + '@' + 学号`（没学号才用裸键）；
  读写全过 `gxKey`；`gxAdopt()` 把老裸键**认领给第一位打开它的学号后删掉**（不删的话下一位没有自己的键、又会回落上一位那份）；
  `gxSetSid(sid)` 换键后 `gxAdopt()` + `applyBackdrop()`，同一个学号不白刷。
- whoami 成功才复套：`known` 只在 `try` 里置真；`if (known) { 回写显示钥匙; 换键 }` —— 把「请求挂了」当「没身份」会把背景打回默认、还会认错下一位的键。
- `plan.html`：`planBgKey()` 同一格式 + `planAdopt()` 同样认领；`applyPlanBackdrop` 走分键。
- `login.html`：注释改口（登录页只写 `campustime_student`、只清 `campustime_session`）。

### 2. `b97d53e` 实机回归：窗口钩子（1 文件 7/2）

浏览器第一次走位就红：`pageerror: gxSetSid is not defined`，顶栏永远停在「👤 正在确认身份…」，
`localStorage.campustime_student = admin1`、cookie `ct_student` 都在 ⇒ 不是身份没拿到，是换键那行把后面标身份那几行一起带走了。

改法：IIFE 内定义后面只露一个钩子 `window.ctGxSetSid = gxSetSid;`，whoami 那层改走
`if (typeof window.ctGxSetSid === "function") window.ctGxSetSid(sid);`。**别的一概不往外挂。**
（`gxAdopt/gxKey/gxVal/applyBackdrop` 都不露 —— 它们只被壳内调用。）

## 二、护栏

### 1. `c6fd6cf` 新建 `tests/test_99_appearance_per_student.py`（三层，37 项）

- **A 批（student.html 静态，15）**：键名拼学号、读写都过分键、裸写扫描（带 must-hit 正控）、认领只认第一位、认领后删裸键、没学号不搬、`known` 只在 try、复套在 `if (known)` 块内、成功回写显示钥匙、换人重套顺序。
- **B 批（plan/login 静态，7）**：同一键格式、`planAdopt` 搬完删、裸读只许认领一处、三页都不许 `localStorage.clear()`（带正控）、登录页写 `campustime_student`。
- **C 批（node 行为，15）**：把页里两段代码**现抽出来**，假 localStorage 下跑两名学生来回切：认领/删除/读自己的/搬不覆盖/写只落自己的/换人回落默认/重套一次/切回还是自己的/没学号读裸键；`/plan` 四条。
- `test_44` 一条锚点改口（不增减项数，仍是 203）：按缩进认**顶层** `applyBackdrop();`，并要求在定义之后。

### 2. `72603ab` 补作用域判据（test_99 37→40）

- A11 改钉 `window.ctGxSetSid(sid)`（恰一处、在 `if (known)` 里）；
- **A11b**：括号配平认三栏 IIFE 区间（`_shell_span`，尾部校验必须紧跟 `)();`），要求「`gxSetSid` 定义在壳里 ⇒ 壳外不许出现裸 `gxSetSid(`（词边界，`window.ctGxSetSid` 不算）」，并钉钩子那行必须在定义之后；
- **A11b 正控**：同一支判据喂「壳内定义、壳外裸叫」的样本必须报 1（否则是空判据）；
- C 批改成经 `window.ctGxSetSid(...)` 换人（钩子没了/挂错函数会在行为层直接红），补 C15「窗口钩子就是 gxSetSid」。

### 3. 登记：C 批照不到的盲区

C 批把代码段**抽出来单独 eval**，作用域差在那一层不存在 ⇒ 跨 IIFE 直呼在这里照样能跑。
这就是为什么实机报错而 40 项全绿。模块 docstring 已写明「C 批不冒充作用域判据，那条由 A11b 兜着」。

## 三、变异（9 笔，零哑弹；原文一律 `git show HEAD:`，跑完按字节还原并 md5 核）

| 变异 | 红 |
|---|---|
| M1 `gxKey` 不拼学号 | A1、C3、C5、C8、C9 |
| M2 换键钩子移出 `if (known)` | A11 |
| M3 认领后不删裸键 | A8、C2、C5、C6、C10 |
| M4 `/plan` 读裸键 | B2、B4、C13 |
| M5 `gxVal` 不调 `gxKey` | A4、C3、C9 |
| M6 设置面板写裸键 | A5 |
| M7 不露窗口钩子 | A11b、C15、C6~C10 |
| M8 壳外直呼 `gxSetSid`（实机原样） | A11、A11b |
| M9 钩子挂成 `gxAdopt` | A11b、C15、C6~C10 |

M7/M9 的静态牙与运行时牙分开登记：A11b/C15 是静态，C6~C10 是行为；M8 只红两条**作用域**判据（这正是它要防的那一类）。

## 四、真机（副本服务 8071 + 副本数据 `n98data`，不碰真实 `app/data`）

- 起服务：`CAMPUSTIME_DATA_DIR=D:/campustime-scratch/n98data`，端口轮询就绪；副本数据由真实 `app/data` 现拷。
- **两学号走位（Playwright headless，同一 context = 同一 origin）12/12**：
  1. 登录前先种老裸键 `ct_bg=deep` → admin1 登录**认领**成 `ct_bg@admin1=deep`、裸键删掉、首屏套深空；
  2. admin1 从**设置面板**换星野 → 只写 `ct_bg@admin1`；
  3. 退出换 admin2 → `ct_bg@admin2` 为空、首屏是默认雾松林（没继承星野）；
  4. admin2 换银河 → 只动自己的键；
  5. 切回 admin1 → 仍是星野，admin2 的银河没被覆盖；
  6. `/plan` 套的是 admin1 的星野；
  7. **全程 console error 0**。
- 真实 `app/data`：起手与收尾同一个函数取指纹，**466 文件 / `b81877718668`** 前后一致。
- 副本目录拆除前扫过四类敏感模式（`sk-`长串 / token 变量名 / key=值 / 6 位数字串）：
  只有 `settings.json` 一处 `sk-` 长串（用户自己的 Key，副本随目录一起删、不进任何交付物）；6 位串是日期/id 一类误报。
  副本服务日志含自举口令横幅 ⇒ 日志与 pid 文件一并删除，未入库。`n98browser.txt`（12 行 PASS）留在 scratch。

## 五、全量与严格克隆

- **本机**：`通过 5686 项，失败 4 项（共 100 批）`，stderr **0 字节**；rc=1（有红即 1）。
- **严格克隆**：tracked-only worktree @ `72603ab`（无 `.env`）`通过 5682 项，失败 4 项（100 批）`，stderr 0 字节。
- 两侧差 **4 项**，逐项只落在 `test_86`「C 批（数据）」**6↔2**（运行时样本库不在提交里，长期差项）。
- 4 条红两侧逐字相同：`test_95_retime_weekend.py` 的 A6 / A9 / D3 / D3-c（挂账 #69 一族，日期跨午夜撞成周日）⇒ **存量、不是回归**。
- 项数配得起来：#97 收口 5650 项 → #98 +40（`test_99` A 17 + B 7 + C 16；`test_44` 条数不变）= 5690 = 5686 通过 + 4 红；克隆侧 5686 − 4（test_86 差项）= 5682。
- ⚠️ 本机那一跑起于 19:32，期间**另一窗口**在 19:36 把它的五处改动提交并推送为 `40a069f`（口令/README/admin 窄屏，与本单无关）。
  本机读数因此带着它未提交的那版文件；但总项数只 +40、恰好只等于 `test_99` ⇒ 其余文件计数没变。

## 六、偏离 / 遗留 / 待拍板

1. **偏离**：C 批（抽段 eval）天生看不见作用域 —— 已写进 docstring 并用 A11b 兜住，不假装 C 批覆盖了它。
2. **脚本侧踩坑（不是产品缺陷）**：走位脚本原先直接调 `loadSettingsView()`（IIFE 局部函数，页面外叫不到）⇒ 改成点侧栏「设置」再点背景档，走学生真实路径。这条和 `gxSetSid` 是同一类错误，正好反证 IIFE 边界是真的。
3. **L2 仍挂账等拍**：43 行已不再生效的玻璃 CSS 与 `test_44` J 批 37 条判据删不删（#97 起就挂着的同一条）。
4. **挂账 #30 的另一半**：除 `/plan`、`/station` 以外的页面要不要接玻璃层/背景图，仍等你定。
5. **#96 建议**：施工单原件没入库（含 3 处与你本机真实口令逐字节相同的 6 位数字串），建议把那把口令换掉。
6. **只有你能做的**：演示视频/录屏仍缺；推送以 `origin/master` 为准（19:45 现查 `origin/master = 40a069f`，本单四笔已在其中；本快照提交后需你再推一次）。
