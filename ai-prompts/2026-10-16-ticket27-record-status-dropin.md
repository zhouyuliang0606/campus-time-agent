# 27｜驿站「登记条目」加处理状态（drop-in 施工单）

> 拍板来源：用户 2026-10-16 选 **选项 A**（加，且与工单共用三态）。
> 交付物性质：**给实现 AI（Qoder）的落地施工单**。我不改 CampusTime 代码，下面代码块可直接粘。
> 前置实核（全部来自当前代码，非记忆）：`store.py:1026 add_custom_record` / `store.py:123 update_workorder_status` /
> `main.py:3975 _WO_STATUSES` / `main.py:3978 _workorder_status_response` / `main.py:4102,4129 驿站登记两个口` /
> `main.py:4148 /api/custom/records`（学生确认登记）/ `station.html:401-433 loadRecords`。

---

## 0. 先说清一件事：与本单和 25 号（驿站三栏）的顺序关系——我上一轮的警告**过头了**

我在 26 号写过"状态字段必须排在 25 号之后，否则同一段 DOM 改两遍"。**这条是错的**，按代码纠正：

- 登记条目那几行**不是 HTML 里写死的**，是 `station.html:401-433` 的 `loadRecords()` **用 JS 建出来的**（`createElement` + `textContent`）。
- 25 号做的事是**搬容器**（把 `#svcList` 那块 `.wo` 从竖排页面挪进左栏的 `#view-svc`）——**一行都不碰 `loadRecords()`**。
- ⇒ 两件事**没有强耦合**，谁先谁后都不会白干。
- **唯一的真实耦合点**：如果 25 号改了 `#svcList` 这个 id（或把 `.recs` 换个类名），`loadRecords()` 里的选择器 `#svcList .recs` 要跟着改 —— 这是"两处各说各话"的老坑，**加一条形状断言把它钉住**（见护栏 §4 第 6 条）。

⇒ 所以本单**可以现在做**，不必等 25。上一轮那句警告我记进 26 号的更正里。

---

## 1. 要达成什么

驿站端「服务与登记」里，每条**登记条目**现在只有一行字：

```
2026-10-16 17:33:16｜丢失物品：校园卡；丢失地点：三食堂
```

加完之后：

```
2026-10-16 17:33:16｜丢失物品：校园卡；丢失地点：三食堂
[待处理]  [改成「处理中」]              ← 状态芯片 + 一个按钮，与工单那块长得一模一样
```

**为什么值得做**：用户自己建的正是「电梯报修」这类服务 —— 登记完，管理员在页面上**没有任何"处理完了"的动作可做**，演示时问一句"然后呢"就卡住。

---

## 2. 设计（三条硬约束，别越线）

1. **状态是流程、不是内容** ⇒ **不进 `collect_fields`**。
   一进就会连锁两件事：`_custom_field_errors`（main.py:4123）会把 `status` 判成"不属于这项服务"的 stray 键直接 400；而且让模型按管理员那句话生成状态词 = **N 份服务定义各带一套状态表**，前端还得写 N 套逻辑（本仓反复吃过"两份状态表"的亏，`main.py:3981-3982` 那段注释就是为它写的）。
2. **合法值清单只有一份**：继续用 `main.py:3975 _WO_STATUSES = ("待处理", "处理中", "已解决")`，**不新开白名单**。
   ⚠️ `tests/test_52_station_workorders.py:129` 已经钉着 `src.count("_WO_STATUSES = ") == 1`（词表只许定义一处）—— 本单只能**引用它**，加第二处会当场红。
3. **状态由系统给**：新记录落库时就是「待处理」，不需要任何人填。
   store 层会出现**一个**字面量（初始态），用护栏断言钉住它与 `_WO_STATUSES[0]` 一致 —— 这是"跨层引用同一个词表"的最小写法。

---

## 3. 施工单（三处代码 + 一处前端）

### 3.1 `app/store.py`

**(a) 文件顶部**（挨着其它数据常量）加一个常量，并在 `add_custom_record` 里用它：

```python
# 登记记录的**新记录初始态** —— 与工单共用同一套状态词（main._WO_STATUSES）。
# 这里只写这一个字面量：合法值清单的唯一来源仍是 main._WO_STATUSES，
# 由 tests 的形状断言钉住「这个字面量必须是那三态里的第一个」（见 test_53 新批）。
CUSTOM_STATUS_NEW = "待处理"
```

**(b) `add_custom_record`（store.py:1026）** —— 只加 `status` 参数与一行落库，**其余一个字不动**：

```python
def add_custom_record(sid: str, fields: dict, svc: dict | None = None,
                      status: str = "") -> dict:
    """登记一条到 `custom/<service_id>.json`。

    ⚠️ 这条路径**只允许确定性接口调用**（驿站管理员点【登记】这个明确动作）。
    不给模型注册任何写这里的工具 —— 一注册，"低代码生成的服务"就自带写库能力了。

    `status` 是**流程状态**、不是学生填的内容：默认由系统给「待处理」，
    绝不会出现在 `collect_fields` 里（进了会被 `_custom_field_errors` 判成 stray 直接 400）。
    """
    ...
    item = {"id": uuid.uuid4().hex[:8], "ts": _now(), "service": sid,
            "name": svc.get("name") or "", "fields": clean,
            "status": str(status or CUSTOM_STATUS_NEW)}
```
> 注意 `_custom_field_errors` 那条 stray 检查**不用改**：它判的是 `fields` 里的键，`status` 在 `item` 顶层、与 `fields` 平级，天然进不去 —— 这正是不把状态塞进 `fields` 的好处。

**(c) 新增改状态函数** —— 形状**照抄** `update_workorder_status`（store.py:123）"读全量 → 改一条 → 整份写回"：

```python
def update_custom_record_status(sid: str, rid: str, status: str) -> dict | None:
    """改一条登记记录的处理状态；找不到那条记录返回 None。

    形状与 `update_workorder_status` 一致（读全量 → 改一条 → 整份写回），
    老记录**没有** `status` 键也照改 —— 写进去就把它补上了，不做批量迁移。
    """
    path = os.path.join(CUSTOM_DIR, f"{sid}.json")
    rows = _read(path, [])
    if not isinstance(rows, list):
        return None
    for it in rows:
        if str(it.get("id") or "") == str(rid):
            it["status"] = status
            _write(path, rows)
            return it
    return None
```
> 不用 `os.makedirs`：能读到 `rows` 就说明文件在（`_read` 读不到时会返回默认 `[]`，那一支直接 None）。

### 3.2 `app/main.py`

**(a) import 补一个名字**（main.py:41 那一行那组里）：`update_custom_record_status`。

**(b) 新增接口** —— 紧跟 `POST /api/station/services/{sid}/records`（main.py:4129-4145）后面：

```python
@app.post("/api/station/services/{sid}/records/{rid}/status")
async def station_service_record_status(sid: str, rid: str, req: Request):
    """驿站把一条登记记录标成"处理中/已解决"。

    三态与工单**共用同一份白名单** `_WO_STATUSES`（不再新开一张状态表）——
    加一档"已关闭"只需要改那一处，驿站端两块表跟着一起变。
    ⚠️ 免鉴权与既有的 `/api/station/*` 同一档（演示级，理由与限制写在上面那段注释里）。
    """
    body = await req.json()
    status = (body.get("status") or "").strip()
    if status not in _WO_STATUSES:
        return JSONResponse({"error": "status 只能是 " + "/".join(_WO_STATUSES)},
                            status_code=400)
    if not get_service(sid):
        return JSONResponse({"error": "没有这项服务"}, status_code=404)
    item = update_custom_record_status(sid, rid, status)
    if not item:
        return JSONResponse({"error": "这条登记记录不存在"}, status_code=404)
    return {"ok": True, "record": item}
```
> **校验顺序（先 400 后 404）是照 `_workorder_status_response`（main.py:3984-3989）来的**，不是随手排的：同族两个口顺序不一致，将来只改一处就会出现"同一个坏请求一个回 400、一个回 404"。护栏里把顺序也钉一条。

### 3.3 `app/static/station.html` —— `loadRecords()` 里那一行

现在（:427-430）是一行纯文字：

```js
const line = document.createElement("div");
line.className = "recRow";
line.textContent = (item.ts || "") + "｜" + (bits.join("；") || "（没填内容）");
rec.appendChild(line);
```

改成"文字 + 状态芯片 + 按钮"（**全部 createElement/textContent，学生填的内容一个字都不进 innerHTML**）：

```js
const line = document.createElement("div");
line.className = "recRow";
// ⚠️ 先把正文写进 textContent，**再** append 子节点。
//    反过来（append 完再赋 textContent）会把刚加进去的按钮整块擦掉 —— 这是本仓常见的一脚。
line.textContent = (item.ts || "") + "｜" + (bits.join("；") || "（没填内容）");

// 状态 + 改状态：与上面「待处理问题」那块**共用同一个 WO_NEXT 和同一套 .st 样式**，
// 驿站端两块表的词与长得都一样，不会出现"工单叫已解决、登记叫已处理"这种自己都对不上的情况。
const st = item.status || "待处理";      // 老记录没有 status 键 → 显示"待处理"（不迁移文件）
const tail = document.createElement("div");
tail.style.cssText = "margin-top:3px;display:flex;gap:6px;align-items:center;";
const chip = document.createElement("span");
chip.className = woCls(st);              // 复用 :237 已有的函数
chip.textContent = st;
const sbtn = document.createElement("button");
sbtn.textContent = "改成「" + (WO_NEXT[st] || "待处理") + "」";
sbtn.addEventListener("click", async () => {
  sbtn.disabled = true;
  try {
    const r = await (await fetch("/api/station/services/" + encodeURIComponent(sid)
      + "/records/" + encodeURIComponent(String(item.id || "")) + "/status", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: WO_NEXT[st] || "待处理" }),
    })).json();
    if (r.error) addMsg("系统", "改状态没成功：" + r.error, "sys");
  } catch (e) {
    addMsg("系统", "改状态时网络出问题了", "sys");
  }
  svcTick();    // 改完立刻重读，节奏与工单那块（loadWorkorders）保持一致
});
tail.appendChild(chip);
tail.appendChild(sbtn);
line.appendChild(tail);
rec.appendChild(line);
```

**顺带一句**：`loadRecords()` 里 `rows.slice(0, 5)` 现在只列最新 5 条（:423），加状态后**这 5 条之外的记录点不到按钮** —— 本轮**不动**这个上限（改成可翻页是另一件事），但要在注释里写明这个已知边界，别让下一位以为"全部都能改"。

### 3.4 文档（一处）

`EXPLAIN.md` 六 里，把 Qoder 上一轮记的那条"登记条目只读、要加状态得先给定义加状态"换成下面这段（用户拍板 A）：

```
- ~~**登记条目只有记录、没有处理状态**~~ —— **2026-10-16 已做**：每条登记记录带 `status`
  （待处理/处理中/已解决，**与工单共用同一份词表 `_WO_STATUSES` 与同一份校验**），
  驿站端点一下改状态。状态由**系统**在登记时给（默认"待处理"），**不进 `collect_fields`** ——
  它是流程不是内容，让模型生成就等于每份服务定义各带一套状态表、前端各写一套逻辑。
  **还没做**：删/改已登记的内容（现在只能改状态）；登记列表只列最新 5 条，超出的点不到按钮。
```

---

## 4. 护栏（扩 `tests/test_53_lowcode_service.py`，新开一批）

| # | 钉什么 | 要点 |
|---|---|---|
| 1 | **新记录初始态** | 建服务 → 驿站端登记一条 → 落库那条 `status == "待处理"`（钉"系统给初始态"这一步真的发生了） |
| 2 | 三态全走通 | 待处理→处理中→已解决，每次读回 `custom/<sid>.json` 都变了（不是只看接口回 200） |
| 3 | 负路径 ①③ | 未知 `sid` → 404；非法 `status`（`"已关闭"` / `""` / 缺键）→ 400 |
| 4 | 负路径 ② | 未知 `rid` → 404（**且服务存在**，否则测的是第 3 条那支） |
| 5 | **校验顺序** | 拿一个"服务不存在 + status 也非法"的请求 → 必须是 **400**（顺序变了这条会红） |
| 6 | **老记录不迁移** | 沙箱里手工往 `custom/<sid>.json` 塞一条**没有 status 键**的记录 → ① GET 不报错、② 直接改状态能成功并把它补上（这条最容易被漏，而真实台账里现在**全是**老记录） |
| 7 | 跨层词表一致 | `store.CUSTOM_STATUS_NEW == main._WO_STATUSES[0]`；且 `main.py` 里 `_WO_STATUSES = ` 出现次数**仍为 1**（和 test_52:129 同形） |
| 8 | 状态进不去 fields | `add_custom_record` 落库的 `fields` 里没有 `status` 键；带 `status` 去登记 → 400 stray（保住"状态不是内容"这条边界） |
| 9 | **容器 id 对得上** | `station.html` 里 `loadRecords()` 的选择器字符串，与 HTML 中 `#svcList` / `.recs` 的定义**必须互相找得到**（照 README 里 notices 那次"id 少一个字母就白屏"的先例，钉住 25 号搬容器时不会把两边拆开） |
| 10 | 免鉴权面没扩大 | 这条新口免口令（与 `/api/station/*` 同档），**管理端口仍要口令**（照 test_52 已有的反向断言，加一条同形） |

**变异（每笔都要有牙）**：M1 把初始态写死成 `"已解决"`（第 1 条红）／M2 让 `update_custom_record_status` 只返回不写库（第 2 条红）／M3 把 `_WO_STATUSES` 换成 `("待处理","处理中","已解决","已关闭")` 并放宽判断（第 7 条红）／M4 把 `status` 塞进 `collect_fields`（第 8 条红）／M5 把顺序换成先查 sid 后查 status（第 5 条红）。**逐笔字节还原**。

---

## 5. 真机验证（照 22 号单子 6 的规程，别碰用户的 8000）

1. `cp -r app/data` 到临时目录 → `CAMPUSTIME_DATA_DIR` 指过去 → **另起一个端口**（8010）跑。
2. 驿站端：建一项服务（或复用已有的）→ 登记一条 → 那条上出现「[待处理] [改成「处理中」]」→ 点一下 → 变「处理中」；再点 → 「已解决」。
3. **老记录那一支**：先把副本里某条记录的 `status` 键删掉（模拟真实台账现状）→ 页面显示"待处理"、按钮能把它改成功。
4. 关掉副本服务 → 核**真实** `app/data/custom/*.json` 与 `workorders.json` 指纹前后逐字一致 → 删临时目录。
5. 证据留档（截图或 DOM 断言结果），写进快照。

> 提醒：`browser-use` 的合成 `click` 有时**不回请求**（22 号那轮踩过），判据先看网络请求有没有发出，再用 `el.click()` 判别。

---

## 6. 不许碰的

- 不动 `_WO_STATUSES` 的定义与内容（`main.py:3975`）；不动 `_workorder_status_response`；不动工单那条链。
- 不动 `add_custom_record` 里 `fields` 的收口逻辑（`_custom_field_errors` 一行不改）。
- 不动 25 号那一族（驿站三栏布局）—— 它是另一单，用户已明确缓一缓。
- 不新开状态白名单、不新增"已关闭"等第四档（要加是**另一件**：先改 `_WO_STATUSES` 一处，再看两块表）。
