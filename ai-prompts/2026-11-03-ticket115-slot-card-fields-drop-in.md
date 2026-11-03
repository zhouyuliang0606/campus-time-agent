# 115 · 多候选卡上「星期」是空的、「约 ? 分钟」—— 模型拼的 JSON 缺两个字段，没人补

> 施工单。改两处：`planner.py` 新增一个 20 行的补字段函数 + `engine.py:180` 一行调用。
> **前端一行不用动**（前端是对的，它在照既定契约渲染，是上游没把字段给全）。
> 起因：用户 2026-11-03 20:58 截图 —— 多候选卡三行全是「2026-11-07（）… · 约 ? 分钟」。
> 用户原话：「如果弹窗是只渲染了一个打勾点…它就正常渲染。但如果我说周末帮我找两个时间，
> 然后他就会出上两个时间，这时候他渲染就失败了。我记得你以前是说他是掉了两次工具，
> 然后你改了一个下东西」—— 他记的那个"下东西"是 `#75` 的 `_collapse_same_title_adds`。
> **那个收敛对这条路从来没生效过**，见 §0.5。

---

## 〇 现场事实（`ct_student=admin1`，20:5x-21:0x 实测）

### 0.1 屏幕上是什么样

「勾选你想要的时间，点【确认所选】即可排入日程：」下面三行：

```
打篮球   2026-11-07（）  13:00-14:30 · 约 ? 分钟
打篮球   2026-11-08（）  10:30-12:00 · 约 ? 分钟
打篮球   2026-11-08（）  17:10-18:40 · 约 ? 分钟
```

两个洞：**括号里的星期是空的**、**「约 ? 分钟」**。

### 0.2 后端给前端的原始 JSON（`options` 每一项，原文抄录）

```json
{"title":"打篮球","date":"2026-11-07","start":"13:00","end":"14:30","note":"周六下午，课后整段空着"}
{"title":"打篮球","date":"2026-11-07","start":"17:10","end":"18:40","note":"周六傍晚，晚饭前"}
{"title":"打篮球","date":"2026-11-08","start":"10:30","end":"12:00","note":"周日上午，空档最长"}
```

**没有 `kind`，没有 `weekday`，没有 `minutes`。**

### 0.3 前端照着契约画，缺什么就露出什么

`app/static/student.html:1986`（过滤）和 `:1995-1997`（画）：

```js
const drawable = options.filter(o => o && o.date && o.start && o.end);
...
info.innerHTML =
  `<b>${esc(o.title || "待办")}</b>` +
  `<div class="meta">${esc(o.date)}（${esc(o.weekday || "")}）` +
  ` ${esc(o.start)}-${esc(o.end)} · 约 ${esc(o.minutes != null ? o.minutes : "?")} 分钟</div>`;
```

`o.weekday || ""` → 空括号；`o.minutes != null ? ... : "?"` → 问号。
**前端没有做错任何事**：它按 `date/weekday/start/end/minutes` 这套字段名取值，
而上游给的四个字段里只有三个对得上。

### 0.4 为什么会缺：模型自己拼的 JSON，工具契约只要求四个字段

这条链是 `propose_slots`，不是 `propose_todo_tool`：

| 位置 | 内容 |
|---|---|
| `planner.py:6464` | 提示词要求模型「挑出 **2-3 个**候选时间段，然后**必须调用 propose_slots 工具**」 |
| `planner.py:6640-6657` | 工具描述原文：`入参 options_json 是 JSON 数组，每条含 title(待办标题)、date(如2026-09-25)、start、end、可选 note` |
| 模型 | 照契约传 `[{title, date, start, end, note}]`，**它没错，契约就是这么写的** |
| `engine.py:173-181` | 捕获参数：`if isinstance(raw, list): self.options = raw` ← **原封不动，一个字段都不补** |
| `student.html:1986` | `date/start/end` 都在，通过过滤 |
| `student.html:1995` | `weekday`/`minutes` 是 `undefined` → 空括号 + 问号 |

**「两种模式下都一样」**：agent 模式和普通模式产出的 JSON 一字不差（同一句各跑一遍，
两边都是那三条、都缺这两个字段）。所以这不是 Agent 模式独有的毛病，
只是**只有多段的时候才显眼**（单段就一行，空括号扫一眼就过去了）。

### 0.5 用户记的那个"改了下东西"，对这条路从来没生效

`#75` 加的 `_collapse_same_title_adds`（`main.py:1587`）本该把同名的多张
合成一张 `todo_slots`，而 `todo_slots` 的每一段是 `build_todo_slots` 产的 ——
那份**带 `label`、`weekday`、`minutes`**，前端画得出来。

可它的分组条件是：

```python
        if not isinstance(o, dict) or o.get("kind") != "todo_add":
            continue
```

**`propose_slots` 这条路传进来的卡一个 `kind` 都没有** ⇒ 全被 `continue` 掉 ⇒
`groups` 是空的 ⇒ `merged` 是空的 ⇒ `if not merged: return options` **原样返回**。
模型调 3 次 `propose_slots`，屏幕上就是 3 条裸 JSON 画的裸卡，一张都没收敛。

**所以 `#75` 修的是 `propose_todo_tool` 那条路（它产标准 `todo_add`），
这条路从来没被管过。** 用户以为修过了，是因为当时那次的现场确实走的是 `propose_todo_tool`。

---

## 一、修法（两处，前端不动）

### 1.1 新增补字段函数

放在 `planner.py` 的 `propose_slots`（`:1586`）**前面**：

```python
def normalize_slot_cards(cards: list) -> list:
    """把模型自己拼的候选卡补成前端画得出来的形状（人话：缺的字段由系统算，不让模型编）。

    ⚠️ 为什么必须补：`propose_slots` 的入参是模型**自由拼的 JSON**，
    工具描述（`:6643`）只要求 title / date / start / end / 可选 note 四个字段。
    可前端 `student.html:1995` 画的是
    `date（weekday）start-end · 约 N 分钟` —— 两个字段一缺就成
    「2026-11-07（）」「约 ? 分钟」（学生截图，2026-11-03 20:58）。

    为什么不让模型自己填 weekday：它会写「星期六」，也会写「周六」、写「六」，
    三种写法前端都得认一遍；而**日期→星期是系统能算的事**，算比认便宜、也不会错。

    `kind` 为什么补成 `todo_add`：补上以后 `main.py:_collapse_same_title_adds`
    才认得出来，才把同名的多张收敛成一张 `todo_slots`（那一步产出的每段
    自带 `label`/`weekday`/`minutes`，前端那一支本来就是画得出来的）。
    不补 `kind` 的话收敛永远不发生，屏幕上就一直是"几张裸卡"（`:0.5`）。
    """
    out = []
    for c in cards or []:
        if not isinstance(c, dict):
            continue
        d = str(c.get("date") or "").strip()
        s0 = str(c.get("start") or "").strip()
        e0 = str(c.get("end") or "").strip()
        item = dict(c)
        item.setdefault("kind", "todo_add")
        if d and not item.get("weekday"):
            item["weekday"] = _weekday_name(d)
        if item.get("minutes") is None and s0 and e0:
            m = to_minutes(e0) - to_minutes(s0)
            if m > 0:                      # 算不出来就不填，留给前端那个 "?" —— 别编
                item["minutes"] = m
        if not item.get("title"):
            item["title"] = "待办"
        out.append(item)
    return out
```

### 1.2 让引擎走这一步

`app/agent/engine.py:173-181`：

```python
                        if fn_name == "propose_slots":
                            raw = args.get("options_json") or args.get("options")
                            if isinstance(raw, str):
                                try:
                                    raw = json.loads(raw)
                                except Exception:
                                    raw = None
                            if isinstance(raw, list):
                                self.options = raw
```

最后一行改成：

```python
                            if isinstance(raw, list):
                                # ★ #115：模型传的是**它自己拼的 JSON**，工具描述只要求
                                #   title/date/start/end 四个字段（`planner.py:6643`），
                                #   而前端要 `weekday` 和 `minutes` 才能画成
                                #   「2026-11-07（周六）· 约 90 分钟」。
                                #   缺的两个字段系统算得出来（日期→星期、起止→分钟数），
                                #   补在这里一处，前端一个字都不用改。
                                from app.modules.planner import normalize_slot_cards
                                self.options = normalize_slot_cards(raw)
```

> `from ... import` 写在函数里是有意的：`planner.py` 不 import `engine`（已核，`:19-28`
> 只 import `clock` / `agent.tools` / `agent.pending` / `store` / `student_persona`），
> 所以顶层 import 也不会成环；但放在这里跟同文件里
> `from app.agent.intent import looks_like_question` 那几处的写法一致，读的人不用来回翻。

### 1.3 （可选，不阻塞）收敛出来的卡给一句 hint

`build_todo_slots` 不给 `hint` 就是空串，`todo_slots` 分支会在上面留一行空白。
`main.py:1630` 那个调用补一句：

```python
            hint=(f"这 {len(cards)} 段都空着，勾要走的那几段，点【确认所选】才写进日程。"),
```

想做就做，不做也不影响本单过 —— 它只是难看，不是错。

---

## 二、为什么不在前端修

| 选项 | 代价 |
|---|---|
| 前端兜 `o.weekday`（用 `Date` 算） | 时区/跨年那类坑得在前端再踩一遍，而 `planner._weekday_name` 已经在库里用了几十处 |
| 前端兜 `o.minutes`（`end-start` 减） | 一样，`to_minutes` 那套解析（`19:00` / `7:00` / 中文数字）前端没有 |
| 前端在字段缺时**不画**这一行 | 更糟：学生说了「周末找两个」，屏幕上一条都不出，等于把话说吞了 |

真正的契约问题在**上游**：工具描述里写了"每条含 title/date/start/end"，
就该是「模型按这个说 → 系统补成前端要的 → 前端只管画」。
让前端替后端猜字段，等于把契约挪到一个更看不见的地方。

---

## 三、不动的东西

| 不动 | 理由 |
|---|---|
| `propose_todo_tool`（`planner.py:3814`） | 那是另一条路，它产的是标准卡（`build_todo_slots` / 带 `kind`），本单与它无关。**改它会碰 `#75` 的 D 批** |
| 工具描述的字（`planner.py:6643`） | 可以加一句"系统会补 weekday/minutes"，但**不许改成"模型必须传 weekday"** —— 那会让模型开始编星期，编错了比空着更糟 |
| 前端 `student.html:1986` 那个 `drawable` 过滤 | `#59` 刻意只验 `date/start/end` 三个字段（注释里写清了为什么不能改判据）。本单补的是上游字段，**不要动这个过滤** |
| `#114`（周末两次三小时排到周三） | 另一条线，已单独出单。本单不碰 |
| `note` 字段的用法 | 它现在只进 JSON、前端不画。想用起来是另一件事，别在本单顺手动 |

---

## 四、验证

### 4.1 真机（`ct_student=admin1`，**两种模式都要跑**）

| 句 | 期望 |
|---|---|
| 周末帮我找两个时间打篮球 | `options` 只有 **1 张**，`kind = "todo_slots"`，`slots` 每段都有 `weekday`（周六/周日）和 `minutes`（90），`label` 形如「周六（11/7）13:00-14:30」 |
| 周末帮我找两个时间打网球 | 同上（这次会是 4 段） |
| 帮我加个周四晚上的游泳（单段） | 一张 `todo_add` 卡，`weekday`/`minutes` 都在（**单段以前也是缺的，只是不显眼**） |

⚠️ 三个都要**开 agent 模式和关着各跑一次**：本单改的是 `engine.py`，只影响模型那一路；
但 `_collapse_same_title_adds` 那一步对两条路都生效，得确认普通模式没被带坏。

### 4.2 变异（三个都得有牙）

| 变异 | 必须红的断言 |
|---|---|
| M1：删掉 `item.setdefault("kind", "todo_add")` | 「`options` 只有 1 张」**红**（会退回成 3 张裸卡，这正是当前现场） |
| M2：删掉 `weekday` 那 3 行 | 「每段都有 weekday」**红** |
| M3：删掉 `minutes` 那 4 行 | 「每段都有 minutes」**红** |

⚠️ M1 是**本单的核心**：它红了才证明"收敛那一步靠的是 `kind`，而这条路以前没有 `kind`"。
M1 不红说明断言只验了字段没验条数，回去改断言。

### 4.3 回归

```bash
cd /d/CampusTime && unset NODE_OPTIONS http_proxy https_proxy HTTP_PROXY HTTPS_PROXY \
  && .venv/Scripts/python.exe -m pytest tests/test_91_weekend_and_collapse.py \
       tests/test_80_pending_aux_not_a_card.py tests/test_12_batch_todo.py \
       tests/test_06_add_things.py tests/test_43_live_batch_findings.py -q
```

- `test_91` 的 **D 批（同名收敛）** 是本单最容易碰坏的：它原来只在
  `propose_todo_tool` 那条路上验过，本单让 `propose_slots` 也进同一个收敛器，
  要确认那几条"两张 `todo_add` 收敛成一张"仍然绿。
- `test_80_pending_aux_not_a_card.py` 是 `#59` 那条 `drawable` 判据的护栏
  （全仓唯一出现 `drawable` 的测试文件，已核）。本单不碰那个过滤，它必须**逐字不动地绿**。

---

## 五、不做的事

- **不把 `weekday` 写进工具描述让模型传**。理由在 §1.1：星期是可算的，让模型算等于给一个
  能算对的字段开一条会算错的路。
- **不做"前端字段缺失就重排"**。缺什么补什么，别在下游再造一个"猜"的分支 ——
  那正是 `#59` 注释里说的「判据越写越松，最后什么都接得住、什么都画不对」。
- **不改 `drawable` 的判据**（`#59` 的红线）。加一个 `o.weekday && o.minutes` 进去
  会让现在这张卡**整张消失**，学生看到的是"上一轮那张卡没能在这一页还原出来"，
  比空括号更糟。
- **不顺手改 `note` 的显示**。`note` 是模型写给模型的（"课后整段空着"），
  给学生看要重新组织语言，属于展示层的另一单。

---

## 六、给执行方的三句话

1. **只改两处**：`planner.py` 加 `normalize_slot_cards`（20 行），`engine.py:180` 换成调它。
   前端一个字符都不动 —— 前端是对的。
2. **`kind` 那行别省**。它不是为了好看：`_collapse_same_title_adds` 靠 `kind == "todo_add"`
   才把多张合成一张 `todo_slots`，而这条路以前从来没带过 `kind`（§0.5），
   所以 `#75` 那个修法在这条路上一次都没生效过。
3. **M1 必须红**。它验的不是"字段补全了"，是"收敛真的发生了"。这条不红，说明你验的是
   表面现象（星期有了），而屏幕上还是三张卡 —— 那等于没修。
