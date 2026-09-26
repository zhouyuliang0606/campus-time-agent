# AI 对话快照 · 2026-09-26（续）· 周表卡片「重叠了」：CSS Grid 七列横向溢出

> 学生截图报障：日程面板里 `14:00–15:30/15:40` 那一行的三张卡片互相压住，
> 原话只有三个字——**「重叠了」**。这是一处纯前端的 CSS 布局缺陷，与 AI/业务逻辑无关。

## 用户原话 / 截图

- 原话：`重叠了` + 一张截图。
- 截图内容：日程（周表）面板的一行里，`程序设计基础 14:00-15:40 · 机房-A`、
  `数据结构 14:00-15:40 · 机房-B`、`小组会议：项目分工 14:00-15:30` 三张卡片
  边缘交叠 / 被裁切；下方 `毛概 16:00-17:40 · 教一-101` 也明显偏右。

## AI 研判（读代码定位根因）

先按截图反查是哪一列：对照 `/api/timetable` 实时数据——

| 截图内容 | 对应 | 判定 |
|---|---|---|
| 程序设计基础 14:00-15:40 · 机房-A | day 1 与 day 3 都有 | — |
| 数据结构 14:00-15:40 · 机房-B | 仅 day 4 | — |
| 毛概 16:00-17:40 · 教一-101 | 仅 day 3 | → 与 day 4 相邻 |
| 小组会议：项目分工 14:00-15:30 | 种子待办，dated 2026-09-25（周五 = day 5） | → 与 day 4 相邻 |

也就是说截图正好是 **day3 / day4 / day5 三列挨在一起**。再读 `renderSchWeek`：

```js
let html = '<div class="wk">';            // grid: repeat(7, 1fr)
days.forEach(d => {
  ...
  html += `<div><div class="wd ...">…</div><div class="day ...">${inner}</div></div>`;
});
```

`.wk` 是七列栅格，每列是一个包着 `.wd` + `.day` 的普通 `<div>`；卡片 `.evt` 的样式是：

```css
.evt { font-size: 11px; border-left: 3px solid; border-radius: 4px;
       padding: 2px 5px; margin-top: 3px; overflow: hidden;
       white-space: nowrap; text-overflow: ellipsis; }   /* ← 关键 */
```

**根因：`1fr` 的隐式最小宽度是 `auto`（= min-content）。**
`grid-template-columns: repeat(7, 1fr)` 里的 `1fr` 并不是"可以缩到任意小"，
它的下限是内容的最小不可断宽度。卡片内容是
`<span class="tm">14:00-15:40</span> 机房-A` 且 `white-space: nowrap`，
整行不可断 → 该列 min-content ≈ 20 字符 × ~6px + padding/border ≈ **121px**，
而七列均分只有 (836 − 6×6) / 7 ≈ **114.3px**。

于是每列都被撑到 121px：
**七列总宽 = 121×7 + 6×6 = 883px，超出面板 836px 共 47px**
→ 每列相对栅格向右溢出，压到相邻列 → 学生看到的「重叠」。

（顺带：`.wk` 的网格项（每天那个外层 `<div>`）没有 `min-width: 0`，
即便改了 `minmax` 也还会因继承 `auto` 下限继续撑。）

## 代码改动

### 1. `app/static/student.html`（学生端周表 / 月表）

```css
/* before */
.wk   { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
.mcal { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; }
.evt  { ... white-space: nowrap; text-overflow: ellipsis; }

/* after */
.wk   { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.wk > div { min-width: 0; }                       /* 网格项也要放开下限 */
.mcal { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 5px; }
.wk .day { ... overflow: hidden; }
.mcal .d { ... overflow: hidden; }
.evt  { ... white-space: normal; overflow-wrap: anywhere; line-height: 1.35; }
```

- `minmax(0, 1fr)`：把列的下限显式压到 0，列宽才真正均分。
- `.wk > div { min-width: 0 }`：网格项自身也继承 `auto` 下限，必须一起放开。
- `.evt` 由 `nowrap` 改为可折行：窄列里长课名/地点宁可换行，也不撑破格子。
- `@media (max-width: 600px)` 里那条 `repeat(2, 1fr)` 同步改成 `minmax(0, 1fr)`。
- 代码里留了注释说明"为什么不能写回 `1fr`"，防止日后被改回去。

### 2. `app/static/plan.html`（完整日程页）

`.week`（`56px + repeat(7, 1fr)`）与 `.month`（`repeat(7, 1fr)`）是同一处坑，
一并改 `minmax(0, 1fr)` + 网格项 `min-width: 0` + `.mevt` 折行，保持两页一致。

### 3. `tests/test_05_student_views.py`（新增第十三批回归）

结构性护栏（防止声明被改回去）：
- `student.html`：`.wk` / `.mcal` 必须用 `minmax(0, 1fr)`；`.wk > div` 有 `min-width: 0`；
  `.evt` 允许折行；**页面上不得再出现 `repeat(7, 1fr)`**；窄屏那条也是 `minmax`。
- `plan.html`：`.week` / `.month` 同上；`.mevt` 允许折行；不得有 `repeat(7, 1fr)`。
- 几何校验：按 836px 容器、6px 间距算，七列总宽必须**恰好等于**容器宽（零溢出）。

## 验证

- `tests/test_05_student_views.py` 第十三批：**13/13 通过**；全文件 13 批 **0 失败**。
- 几何复算（改前/改后）：

| | 列宽 | 七列总宽 | 溢出 |
|---|---|---|---|
| 改前 `repeat(7,1fr)` + nowrap | 121.0px（被 min-content 撑） | 883px | **+47px（重叠）** |
| 改后 `minmax(0,1fr)` + 折行 | 114.3px（正确均分） | 836px | **0px** |

- 真机：`/student` 与 `/plan` 返回的 HTML 里已是 `minmax(0, 1fr)`（分别 5 / 2 处），
  且两页均无 `repeat(7, 1fr)` 残留。页面带 `Cache-Control: no-store`，刷新即生效，无需清缓存。

## Commit

- `39bc851` `app/static/student.html` — 周/月七列网格溢出（学生端）
- `cd5bdd1` `app/static/plan.html` — 完整日程页同一处网格溢出
- `6713b67` `tests/test_05_student_views.py` — 第十三批网格溢出/重叠回归
- `ai-prompts/2026-09-26-week-grid-overlap-fix.md` — 本快照（与 README 索引同一 commit）
