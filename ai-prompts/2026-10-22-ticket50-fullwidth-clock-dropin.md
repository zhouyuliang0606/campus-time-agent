# 50｜意图诊断面板关不掉 + 材料识别说假话（全角冒号）—— 三件一起收（drop-in）

> 来源：2026-10-21 16:37 用户真机截图，两句话：
> 「然后修改一下意图诊断，点了不能关掉，好像按钮没啥用」
> 截图同一屏还有一个更重的：粘了一份**明明写着日期和时间**的考试材料，
> 系统却回「里面**没有一条写着日期或钟点**」。
> 交付性质：给执行方（Qoder）的施工单。我不改代码。

---

## 一、现场（两件事，一件 UI 一件业务）

截图里一次出现三个问题：

| # | 现象 | 严重度 |
|---|---|---|
| A | 底部「我是这么想的」那一条出现后**再也关不掉**，按钮点第二下只是重新算一遍 | 中（演示时会一直挂在那儿） |
| B | 那条写「我把这句听成 **问事**（三档都没给出结论）」——可这句明明被**材料分支**接走了 | 中（自相矛盾，评委会问） |
| C | 材料里写着 `2026年10月21日` + `19:30-21:30`，却回「没有一条写着日期或钟点」 | **高**（等于功能没生效） |

**C 的根因我复现到了，而且不是"日期没认出"，是"全角冒号"：**

```bash
cd /d/CampusTime && .venv/Scripts/python.exe -c "
import sys,json; sys.stdout.reconfigure(encoding='utf-8')
from app import store; store.set_current_student('admin1')
from app.modules import planner, material
for t in ['考试时间 19:30-21:30','考试时间 19：30-21：30']:
    print(repr(t), json.dumps(planner.parse_time_spec(t),ensure_ascii=False)[:120])
print(material.extract_deterministic('考试日期 2026年10月21日\n考试时间 19：30-21：30'))
"
```

```
'考试时间 19:30-21:30'  → start=19:30 end=21:30 clock=true     ← 半角，正常
'考试时间 19：30-21：30' → start=null  end=null  clock=false    ← 全角，全瞎
材料（全角那版）→ ([], ['离散数学…考试'])                      ← 于是整段被判"没有时间"
```

中文输入法下 `：` 是默认标点，剪贴板里经常就是全角的。**日期那一半读得好好的**
（`_ABS_DATE_RE` 认 `10月21日`），瞎的只有钟点——而 `_mk_item()` 要求 date/start/end **三样都有**，
少一样就整条丢。所以**卡死点不在材料模块，在 `_pick_span`**。

---

## 二、50-A：意图诊断面板要能关

### 现状（`app/static/student.html`）

```js
async function classifyProbe() {
  const out = document.getElementById("probeOut");
  ...
  out.classList.add("show");          // ← 只会加，全文件没有一处 remove("show")
```

全仓 `probe-out` 只有一个 `add("show")`，没有任何 `remove`。
所以：**点开就永久占着输入框下面那条**，再点只是又跑一次请求（输入框空时它还回一句"先在输入框里写一句话"）。

### 修法：按钮改成 toggle，面板里再给一个 ×

```js
async function classifyProbe() {
  const out = document.getElementById("probeOut");
  const btn = document.getElementById("probeBtn");
  // 已经展开 → 再点一次就收起（这就是"按钮没啥用"的直接原因：它没有"收起"这个语义）
  if (out.classList.contains("show") && out.dataset.busy !== "1") {
    out.classList.remove("show");
    out.innerHTML = "";
    btn.setAttribute("aria-pressed", "false");
    return;
  }
  out.classList.add("show");
  btn.setAttribute("aria-pressed", "true");
  ...
}
```

面板内容前面加一颗关闭键（**别用内联 onclick**，`esc.js` 顶上那条）：

```js
out.innerHTML = '<button class="probe-x" id="probeClose" title="收起">✕</button>' + 正文;
out.querySelector("#probeClose").addEventListener("click", () => {
  out.classList.remove("show"); out.innerHTML = "";
  document.getElementById("probeBtn").setAttribute("aria-pressed", "false");
});
```

⚠️ 三条边界：
1. **请求在途时（`busy=1`）不该关**——关了回包还是会往里写，会出现"关掉了又自己弹出来"。
   所以 `busy` 期间按钮只提示"正在看…"、不切换状态。
2. 切到别的模块（`switchNav` / `resetLogForModule`）时**顺手收起**，别让它跟着聊天记录跑到别的栏。
3. 关闭必须把 `innerHTML` 清空，不只是 `display:none` —— 否则下次点开前会闪一眼上一次的结果。

---

## 三、50-B：材料分支的"我是这么想的"必须说材料，不许沿用"问事"

### 现状

`_chat_impl` 里 `meta["read"] = _read_step(...)` 在 **1757 行**，
而材料分支的 return 在 **2756 / 2758 行** —— 中间隔了一千行，
材料分支**没有覆盖** `meta["read"]`，于是 `/api/chat` 最后补的那一条还是语义层的结论：

> 🧠 我把这句听成 **问事**（三档都没给出结论，主链路会照原样答一句）

可它明明被材料分支接走了（回答里那句"📋 这段材料我看到了"就是材料分支的固定文案）。
**诊断说 A，实际走 B**，这比不给诊断更坏。

### 修法：两个 return 之前各设一句（跟 `work_order_report` 卡那处 `meta["read"]` 同一写法）

```python
# 位置：app/main.py 材料分支的两个出口（≈2756 / ≈2758）之前
meta["read"] = {
    "step": 0,
    "phase": "📋 我把这句听成 **一段材料**｜依据：规则层（字段行/编号行形状，"
             "材料判据挂在语义层之前）",
    "answer": "",
}
```

⚠️ 只在**材料分支**加，别在 `_read_step` 里加（那是复述层，不许塞新判定 —— 它自己的注释写着）。
⚠️ 用同一句给两个出口，别写两版（一道判断两处文案，迟早漂移）。可以提成一个模块级常量
`_MATERIAL_READ_STEP`，跟 `_MATERIAL_PHASE` 放一起。

---

## 四、50-C（主因）：全角冒号/破折号/全角数字要能读

### 修法：加在 `_normalize_clock` 的第一行（它是"跟时间沾边的规范化的唯一入口"）

`_normalize_clock()`（`planner.py:2431`）的 docstring 自己写着"它是**唯一入口**"：
抠标题、抠时段都从这儿过。所以只需在它最开始做一次全角→半角：

```python
# 全角 → 半角（只转"跟时间有关"的那些字符，别把整句都转了）
# 为什么必须有这一刀（2026-10-21 真机）：用户在中文输入法下粘「19：30-21：30」，
# 半角冒号的正则整条读不出钟点 → 材料被判"没有时间" → 回一句"里面没有一条写着日期或钟点"。
_FW2HW = str.maketrans("０１２３４５６７８９：－—～　", "0123456789:--~ ")

def _normalize_clock(t: str) -> str:
    t = (t or "").translate(_FW2HW)
    ...
```

### 实测（我在本机打补丁跑过，一行改动覆盖四种写法）

| 输入 | 打补丁前 | 打补丁后 |
|---|---|---|
| `考试时间 19：30-21：30` | start=null | **19:30 / 21:30** |
| `19：30—21：30`（全角破折号） | start=null | **19:30 / 21:30** |
| `１９：３０-２１：３０`（全角数字） | start=null | **19:30 / 21:30** |
| `2026年10月21日 19：30-21：30` | 只有日期 | **日期 + 时间都有** |
| 整段材料（全角版） | `([], ['离散数学…'])` | **`([{'离散数学…考试', 2026-10-21, 19:30, 21:30}], [])`** |

⚠️ **只转这张字符表里的**，不要 `unicodedata.normalize("NFKC", …)` 整句归一 ——
那会把书名号/括号/顿号一起变半角，`_fix_typo_time_words` 和标题残渣那几处判据全是按
中文标点写的，一次性全变会连带改掉标题抽取的结果（那是另一族 bug 的入口）。

⚠️ 另有两处**直接**吃原文的正则没经过 `_normalize_clock`，要一起看：
`planner.py:3482`、`planner.py:3651`（`_CONCRETE_SPAN_RE`）。
它们各自的调用点如果在 `_normalize_clock` 之后，就不用动；否则在调用点先过一次同一个 `translate`。
**不要**复制第二张字符表（`_FW2HW` 只许有一份）。

### 顺带：失败文案在说假话

```python
# app/main.py:1140（_material_empty_answer）
answer = ("📋 这段材料我看到了，但里面**没有一条写着日期或钟点**，"
```

实情是"**日期读到了，钟点没读出来**"——材料里明明白白写着 2026年10月21日。
最小改法（不动数据结构）：把「没有一条写着日期或钟点」改成
**「没有一条**同时**写着日期和钟点」**——这句在两种情况下都是真的，不会再说假话。

想说得更准（推荐，二选一即可）：
`extract_deterministic` 已经能拿到 `planner._pick_date(seg)`；
在第一档 `missing` 时顺手记一句"这几条**有日期没钟点**"，回话里带上日期。
⚠️ 要加就加到返回结构里（`missing` 从 `list[str]` 变成 `list[tuple]` 会波及其它调用点），
**别去嗅文案**（#41 那种"改一句措辞就静默失效"的坑不再踩第二次）。

---

## 五、护栏（`tests/test_76_probe_and_material.py`，编号按当时下一个空位）

| # | 断言 | 为什么 |
|---|---|---|
| E1 | `parse_time_spec("19：30-21：30")` 的 `start/end` 为 `19:30/21:30` | 全角主因 |
| E2 | `19：30—21：30`、`１９：３０-２１：３０` 同样能读 | 一族扫，不是只修一个字符 |
| E3 | 全角版整段材料 → `extract_deterministic` 出 **1 条**，date=`2026-10-21`、start=`19:30` | 真机形状闭环 |
| E4 | 半角版结果**与改之前逐字相同** | 别把老路径弄坏 |
| E5 | 材料分支的 trace 含「一段材料」、**不含**「问事」 | 50-B |
| E6 | 静态：`student.html` 里 `probeOut` 既有 `add("show")` 也有 `remove("show")` | 50-A 的牙（纯前端，只能静态钉） |
| E7 | 静态：面板里有 `probeClose` 且用 `addEventListener`，**没有** `onclick="` | `esc.js` 禁区 |

⚠️ E5 要真的走 `/api/chat`（`make_client` + 一段材料），别只断言 `meta` 字典 ——
`meta` 是中间产物，`/api/chat` 最后补 `read` 那一步才是学生看到的东西。

---

## 六、给执行方的三句

1. **C 是这次唯一影响功能的**，A/B 是"看着坏"。三件一起提交会混不清，**拆三笔**：
   `fix(时间): 全角冒号/破折号/全角数字` / `fix(轨迹): 材料分支覆写 read` / `ui(诊断): 面板可收起`。
2. **别顺手把 `_mk_item` 的三件要求放宽**（date+start+end）。它跟 `POST /api/todos` 的
   `if not (date and start and end and title)` 是同一口径，放一边就是"卡上摆着、勾完 400"。
3. **`_FW2HW` 只许一份**。三处正则要么都在它下游，要么在调用点过它 —— 不许各写一张表。
