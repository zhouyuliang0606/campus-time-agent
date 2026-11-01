# 106 · 服务定义建出来就锁死了 —— 后勤的既不能改也不能删，驿站能删不能改

> 施工单。改 `store.py` 一处（加一个排除参数）+ `main.py` 两个路由 + 两个前端页各加按钮。
> 起因：用户 2026-11-01 23:57 说「增加服务后不能删除/改」「宿舍报修把『报修要多久』写进关键词，想改改不了」。

---

## 〇 现场事实（全部实测，可复跑）

### 0.1 用户想做的动作，一个字都做不到

「宿舍报修」现在这五条触发词（`app/data/services.json`，id `5e702308`，`dept=后勤`）：

```
宿舍灯坏了 / 水管漏水怎么报修 / 空调不制冷 / 门锁坏了找谁 / 报修要多久
```

第 5 条**是个问句**，不是报修动作。用户想删掉它 —— 找不到任何入口。

### 0.2 现状矩阵：三种动作、两个端、只有一格是通的

| 端 | 建 | **改** | 删 |
|---|---|---|---|
| 驿站端（`station.html`，`dept=驿站`） | ✅ `POST /api/station/services` | ❌ | ✅ `DELETE /api/station/services/{sid}`（#39-A） |
| 管理/后勤端（`admin.html`，`dept=后勤`） | ✅ `POST /api/admin/services` | ❌ | ❌ |

后勤这项为什么连删除都没有：驿站端那个 DELETE 有 `_dept_guard`（`main.py:5483`）——
后勤的服务在驿站端点了会 403；而驿站端的服务列表本来就按 `dept_of(s) == "驿站"` 过滤
（`main.py:5449`），后勤服务在那边**根本不显示**。
⇒ **后勤建的服务，两个端都没有它的删除按钮。** 用户看到的「宿舍报修」正好是这一类。

### 0.3 后端只有这几口（`grep "@app" app/main.py | grep -i service`）

```
5441  GET    /api/station/services
5451  POST   /api/station/services
5466  DELETE /api/station/services/{sid}
5493  GET    /api/station/services/{sid}/records
5525  POST   /api/station/services/{sid}/records
5548  POST   /api/station/services/{sid}/records/{rid}/status
5563  DELETE /api/station/services/{sid}/records/{rid}
5714  GET    /api/admin/service-records
5742  POST   /api/admin/service-records/{sid}/{rid}/status
5751  POST   /api/admin/service-records/{sid}
5777  POST   /api/admin/services            ← 只有这一个，没有 PUT / DELETE
```

**全项目对「服务定义」没有一条 PUT。** 定义一旦落库，`triggers` / `collect_fields` /
`reply_template` 就再也动不了 —— 想改只能删了重建，而重建会换一个 `id`，
`custom/<旧id>.json` 里那些已登记的记录就跟新服务脱钩了。

### 0.4 对照：同一个管理页，知识库有完整 CRUD

`admin.html` 里知识库那套是齐的（`main.py:5800-5826`）：

```
POST   /api/admin/kb          新增
PUT    /api/admin/kb/{index}  改
DELETE /api/admin/kb/{index}  删
```

同一个页面、同一个管理员、同一种「低代码资产」，知识库能改能删，服务定义只能建。
**不是能力不够，是这条路当初只写了半截。**

### 0.5 「报修要多久」这条触发词是真的会出事故

`match_service_by_trigger` 是子串匹配，谁最长谁接。实测：

| 学生说 | 被谁接走 | 后果 |
|---|---|---|
| 报修要多久 | 宿舍报修 | 出**登记卡**（填楼栋/房间号/故障描述/联系方式） |
| 报修要多久才能来 | 宿舍报修 | 同上 |
| 宿舍报修要多久 | 宿舍报修 | 同上 |

他要的是**一句答复**，拿到的是**一张要填四格的表单**。
这条词留着，等于把「问进度」永久改判成「报新修」。

### 0.6 ⚠️ 直接把 POST 改成 PUT 会「自己撞自己」—— 这就是为什么不能顺手改

拿用户想做的那个编辑（从 5 条里删掉「报修要多久」，其余不动）直接喂给现在的
`validate_service_def`，实测：

```
=== 不排除自己（现在的 validate_service_def） ===
  拒 —— 剔完剩 0 条
  [('宿舍灯坏了', '宿舍报修', '宿舍灯坏了'), ('水管漏水怎么报修', '宿舍报修', '水管漏水怎么报修')]

=== 排除自己（编辑必须这样） ===
  过 —— 保留 4 条 / 剔 0 条
```

同一个文件里那道**重名闸**也一样：

```
不排除自己：name 在 taken 里 = True  → 会说「已经有一个叫「宿舍报修」的服务了」
排除自己后：name 在 taken 里 = False
```

`validate_service_def` 是照「新增」写的 —— 它拿全表跟自己比。
编辑一条已存在的定义时，它的每一条触发词都跟自己的那一条完全一样（互为子串），
名字也跟自己重名。**不加排除参数，PUT 永远存不进去，而且报的还是「撞了」。**

---

## 一、修法

### §1.1 `validate_service_def` 加 `exclude_id`（这是整单的地基）

改 `store.py:1835` 的签名与两处取数：

```python
def validate_service_def(obj: dict, exclude_id: str = "") -> tuple:
    """候选服务定义过白名单。返回 (规范化后的定义, 错误原因)，错误时定义是 None。

    :param exclude_id: 编辑时传**这条自己的 id**。#106：新增那条路不用传；
        编辑时不传就会"自己撞自己" —— 自己的 5 条触发词跟自己互为子串（剔完 0 条 → 拒），
        自己的名字跟自己重名（→ 说「已经有一个叫…的服务了」）。
        **判据只有这一个参数在变，别为编辑再写一份校验。**
    """
```

两处取数各改一行（`store.py:1858` / `:1868`）：

```python
    rows = list_services()
    # #106：编辑自己时把自己从"已被占用"里拿掉 —— 否则下面两道闸都在跟它自己较劲。
    rows = [s for s in rows if not exclude_id or str(s.get("id")) != exclude_id]
    taken = {str(s.get("name") or "") for s in rows}
```

```python
    existing = rows        # 上面已经排除过自己了，这里别再调一次 list_services()
```

> ⚠️ 现在这两处是各调一次 `list_services()`（`:1858` 和 `:1868`）。
> 改成复用同一个 `rows` 变量，否则排除逻辑要抄两遍 —— 抄两遍必漏一处。

### §1.2 后端加编辑：一个共用函数 + 两个路由

`store.py` 加一个写口（紧挨着 `delete_service`）：

```python
def update_service(sid: str, patch: dict) -> dict | None:
    """就地改一份**已经过校验**的服务定义，保留 id / created_at / dept。

    为什么不复用 `add_service`：那是 append，会多出一条同名服务，而旧的那条还在。
    `dept` 与 `created_at` 一律**不跟着 patch 走** —— 归属由建它的人那一刻定死了，
    改触发词不该顺手改部门（那是越权）。id 同理：台账 `custom/<id>.json` 是靠它对上的。
    """
    rows = list_services()
    for i, s in enumerate(rows):
        if str(s.get("id")) != str(sid):
            continue
        merged = {**s, **{k: v for k, v in patch.items() if k in SERVICE_FIELDS},
                  "id": s.get("id"), "created_at": s.get("created_at"),
                  "dept": s.get("dept")}
        rows[i] = merged
        _write(SERVICES_FILE, rows)
        return merged
    return None
```

`main.py` 加两个路由（放在 `admin_service_create` 后面）：

```python
async def _apply_service_edit(sid: str, body: dict, dept_scope: str):
    """改一份服务定义 —— 管理端与驿站端**共用这一份**（判据只写一次）。

    :param dept_scope: 调用端能管哪一档。管理端传 "后勤"，驿站端传 "驿站"。
        **不匹配就 403**，判据与 `_dept_guard` 同一条（`dept_of`）。
    :return: (service, None) 或 (None, JSONResponse)
    """
    svc = get_service(sid)
    if not svc:
        return None, JSONResponse({"error": "这项服务不存在（可能已经被删了）"}, status_code=404)
    if dept_of(svc) != dept_scope:
        return None, JSONResponse(
            {"error": f"「{svc.get('name')}」归{dept_of(svc)}处理，这边不能改。"}, status_code=403)
    if is_builtin_service(svc):
        return None, JSONResponse({"error": "内置那项不能改"}, status_code=400)
    draft = dict(svc)
    for k in ("name", "triggers", "collect_fields", "reply_template", "table"):
        if k in body:
            draft[k] = body[k]
    clean, why = validate_service_def(draft, exclude_id=str(sid))
    if not clean:
        return None, JSONResponse({"error": f"改不了：{why}"}, status_code=400)
    updated = update_service(str(sid), clean)
    return updated, None


@app.put("/api/admin/services/{sid}", dependencies=[Depends(verify_admin)])
async def admin_service_update(sid: str, req: Request):
    """改一项归后勤的服务定义（#106）。**改的是定义，不动 `custom/<sid>.json` 的台账。**"""
    body = await req.json()
    svc, err = await _apply_service_edit(sid, body if isinstance(body, dict) else {}, "后勤")
    if err:
        return err
    return {"ok": True, "service": svc}


@app.put("/api/station/services/{sid}")
async def station_service_update(sid: str, req: Request):
    """改一项归驿站的服务定义（#106）。不挂管理口令 —— 与同族那几个口同一个口子（#18）。"""
    body = await req.json()
    svc, err = await _apply_service_edit(sid, body if isinstance(body, dict) else {}, "驿站")
    if err:
        return err
    return {"ok": True, "service": svc}
```

`import` 处补一个名字（`main.py:43-45` 那一块已经在 import `validate_service_def` /
`add_service` / `delete_service` / `dept_of` / `is_builtin_service`，**只有 `update_service` 是新的**）：

```python
    delete_service, delete_custom_record, delete_notice, update_service,
```

### §1.3 后端加删除（管理端缺的那一口）

```python
@app.delete("/api/admin/services/{sid}", dependencies=[Depends(verify_admin)])
async def admin_service_delete(sid: str):
    """删一项归后勤的服务定义（#106）。

    口径与驿站端那口**逐字一致**（#39-A 定的）：只删定义、不动 `custom/<sid>.json`；
    内置那项硬拒；不存在回 400 + 一句人话（不是 404 —— 建错了再点一次，该看到"它已经没了"）。
    """
    svc = get_service(sid)
    if svc:
        err = _dept_guard(svc, "后勤")      # 带参数版，见下
        if err:
            return err
    if not delete_service(sid):
        return JSONResponse({"error": "删不掉：内置服务不许删，或者这项服务不存在"},
                            status_code=400)
    return {"ok": True}
```

`_dept_guard` 现在硬编码了「驿站」那一档（`main.py:5414-5420`），照抄一份就是红线 4/11。
**改成带参数的**，两个调用点各传自己那一档：

```python
def _dept_guard(svc: dict, want: str = "驿站"):
    """归不归这一端管的那道判据（403），`_station_only` 与两个删除口共用。

    #106：加 `want` 参数，管理端传「后勤」。
    ⚠️ 只加参数、**不改默认值**：驿站那五个调用点不传参就是原来的行为，一个都不用动，
       连报错文案都逐字不变（`want` 默认「驿站」→「驿站这边不能改」）。
    """
    if dept_of(svc) != want:
        return JSONResponse(
            {"error": f"「{svc.get('name')}」归{dept_of(svc)}处理，{want}这边不能改。"},
            status_code=403)
    return None
```

管理端那口调 `_dept_guard(svc, "后勤")`，驿站那五个调用点原样不动。

### §1.4 前端：后勤报修页每个服务卡加两颗按钮

`admin.html` 的 `loadRepair()` 里，`rpSvc` 那段（`+ "<h3>" + esc(g.name) + ...` 附近）
在 `<h3>` 右边补两颗按钮：

```js
          + '<span class="rpOps">'
          +   '<button class="btn" data-rp-edit="' + esc(String(g.id)) + '"'
          +     ' style="margin:0 6px 0 0;padding:4px 10px;font-size:12px;">编辑</button>'
          +   '<button class="btn" data-rp-del="' + esc(String(g.id)) + '"'
          +     ' style="margin:0;padding:4px 10px;font-size:12px;color:#cf1322;">删除</button>'
          + '</span>'
```

> **只有一个按钮类名**（已核实）：`admin.html:20` 定义的 `.btn`，没有 `ghost` / `danger`
> 这些变体。小尺寸写法照 `:315` 那颗「手动刷新」抄（`padding:4px 10px;font-size:12px`），
> 删除那颗再用 inline `color:#cf1322` —— 那是这一页已经在用的失败色（`rpSay(..., true)`）。

> `g.id` / `g.name` / `g.triggers` 都是从 `/api/admin/service-records?dept=后勤` 的 `groups`
> 里来的（`loadRepair` 现在就在读这几个字段，见 `:1581` 的 `pills` / `inputs`），**不用新接口**。

编辑对话框（用 `textarea`，**每行一条** —— 别做动态增删 input，那玩意儿在 5 秒轮询下会丢焦点）：

```js
    // #106：编辑只开「触发词 / 收集字段 / 回复模板」三项。
    // name 与 table 不开放：name 改了要重过重名闸、table 是台账归类，两者都不是"改错字"能解释的。
    async function rpEdit(id) {
      const cur = rpGroups.find(g => String(g.id) === String(id));
      if (!cur) return;
      const trig = await dlgPrompt({
        title: "编辑「" + (cur.name || "") + "」的触发词",
        hint: "一行一条。**学生说到这些话时，这句话就归这项服务**。"
            + "疑问句（「报修要多久」这种）不要写进来 —— 那是问事，写进来会把提问接成报修登记。",
        value: (cur.triggers || []).join("\n"),
        multiline: true,
      });
      if (trig === null) return;
      const payload = { triggers: trig.split("\n").map(s => s.trim()).filter(Boolean) };
      const resp = await adminFetch("/api/admin/services/" + encodeURIComponent(id), {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const d = await resp.json().catch(() => ({}));
      if (!resp.ok) { rpSay("❌ 没改成：" + ((d && d.error) || resp.status), true); return; }
      rpSay("已改「" + ((d.service && d.service.name) || cur.name) + "」，触发词 "
        + (((d.service || {}).triggers) || []).length + " 条。");
      loadRepair();
    }

    box.querySelectorAll("[data-rp-del]").forEach(b => b.addEventListener("click", async () => {
      const cur = rpGroups.find(g => String(g.id) === String(b.dataset.rpDel));
      if (!await dlgConfirm({
        title: "删除服务「" + ((cur && cur.name) || "") + "」？",
        body: "只删服务定义。它名下已登记的记录留在台账里，不受影响。",
        warn: "定义删了只能用那句话重新生成一份；已登记的记录不会跟着走，但新服务也接不上旧记录。",
        okText: "删除服务",
      })) return;
      const resp = await adminFetch("/api/admin/services/" + encodeURIComponent(b.dataset.rpDel),
        { method: "DELETE" });
      const d = await resp.json().catch(() => ({}));
      if (!resp.ok) { rpSay("❌ 没删成：" + ((d && d.error) || resp.status), true); return; }
      rpSay("已删除服务，台账未动。");
      loadRepair();
    }));
```

> **弹窗函数要新写**（已核实）：`admin.html` 里**没有** `dlgConfirm` / `dlgPrompt`
> （全文件 grep 零命中），只有 `station.html:749` 的 `stConfirm`。
> 所以这单要在 `admin.html` 里补两个小函数 —— **照 `stConfirm` 的形状抄**（单按钮 + 确认/取消，
> `.mask` + 回调），别新发明第三套样式。
> `loadRepair` 里把 `groups` 存一份到 `rpGroups`（现在只在局部变量 `groups` 里，弹窗拿不到）。

### §1.5 驿站端补同一颗「编辑」按钮

`station.html` 的 `loadServices` 里，服务卡（`el.innerHTML = ...`）补：

```js
              <button class="mini" data-svc-edit="${esc(String(s.id || ""))}">编辑</button>
```

接线与「删除服务」那颗并列，打 `PUT /api/station/services/{id}`，**编辑框里的话术与
`admin.html` 那份一字不差**（同一个提示语抄两遍就会漂 —— 抽成一个字符串常量放两边共用
做不到（两个 html 不共享 JS），所以两边都写成同一句话，并在注释里互相指认行号）。

> ⚠️ 驿站那边有个「5 秒轮询不许抹掉管理员正在填的字」的闸（`:783` 的 `svcSig`）。
> 编辑对话框是**独立弹窗**，不在这张卡的 DOM 里，所以不受那道闸影响；但保存后必须
> `loadServices(true)`（带 force），否则"改掉了但屏幕上还是旧的"。

---

## 二、判断与理由

### 2.1 为什么先修 `exclude_id`，而不是先写路由

0.6 那条实测：不修它，PUT 写出来也是**永远 400**，而报错是「撞了」——
接手的人会去查撞车逻辑，查到天亮也查不出来，因为撞的是它自己。
**地基没打就往上砌，砌出来的每一层都在骗人。**

### 2.2 为什么 `name` 不开放编辑

改 `name` 要重过重名闸、还要考虑「学生已经认得的那个名字突然变了」。
用户要改的是**触发词**，不是改名。这单只开三项（triggers / collect_fields / reply_template），
把 name / table 留给下一单（真要改名时得单独想「旧名字要不要留成别名」）。

### 2.3 为什么删除只删定义、不动台账

这是 #39-A 定下的口径，驿站端那口已经这么做了（`store.delete_service` 的 docstring）。
后勤这口**必须逐字一样** —— 两个端同一种操作两种后果，比没有这功能更坏。

代价要写在确认框里（§1.4 的 `warn`）：定义删了，记录还在，但新服务接不上旧记录。
这是真话，别美化。

### 2.4 为什么编辑也不迁移历史记录

`custom/<sid>.json` 里存的是 `{字段名: 值}` 的字典。改了 `collect_fields`
（比如把「房间号」改成「房间」），老记录里那个键还叫「房间号」，表单上就不再显示它。
迁移就得猜「房间号 → 房间」是同义改写，猜错比不迁移更坏。
**不迁移，但在编辑框的提示里写明**：老记录按旧字段名存着。

### 2.5 为什么「报修要多久」这类问句只提示、不硬拦

`§1.4` 的提示语写了「疑问句不要写进来」，但 `validate_service_def` 里**不加这条硬判据**。
理由：触发词里放问句有时是故意的（「丢东西了去哪找」）。
真正的病是 `match_service_by_trigger` **不看这句话是不是在问**，那是另一件事（见 §四.4）。
在这一单里硬拦，等于用一个新判据去补一个不相干的洞。

### 2.6 为什么驿站端的 PUT 不挂管理口令

沿用 #18 的演示级口子：同族的 `POST` / `DELETE` 都没挂。这单**不扩大它**——
但也要说清代价：这口改的是「学生说到什么话会被接走」，误用它能把整个对话路由改坏。
真要上线，这一族得一起补账号，那不是这单的活。

---

## 三、验证

### 3.1 判据

| # | 判据 | 文件 | 断言 |
|---|---|---|---|
| T1 | 编辑自己不撞自己 | `tests/test_106_service_edit.py` | `validate_service_def(定义, exclude_id="5e702308")` 对 0.6 那份改后定义 **过**；不传 `exclude_id` 时 **拒**（两条都要，证明这个参数真的有牙） |
| T2 | 重名闸也排除自己 | 同上 | 用自己原来的 name + `exclude_id` 校验 → **过**（不许说「已经有一个叫…」） |
| T3 | 编辑后 id / dept / created_at 不变 | 同上 | `update_service` 后这三项与改前逐字相等 |
| T4 | 编辑照样过撞车闸 | 同上 | 把「宿舍报修」的触发词改成含「快递丢了」→ **拒**，`why` 里含「撞了」 |
| T5 | 越权 403 | 同上 | `_apply_service_edit(sid后勤, ..., "驿站")` → 403 |
| T6 | 内置服务不许改 | 同上 | 对 `table=workorders` 那项 PUT → 400 |
| T7 | 删除只删定义 | 同上 | 先 `add_custom_record` 一条，再 DELETE → 定义没了、`custom/<sid>.json` 那条**还在** |
| T8 | 前端有按钮 | `tests/test_106_service_edit_ui.py` | `admin.html` 含 `data-rp-edit` 与 `data-rp-del`；`station.html` 含 `data-svc-edit` |
| T9 | 问句触发了不硬拦 | 同上 | 把「报修要多久」单独存进一个新服务（不撞任何现有词）→ **过**，不许因为它是问句被拒（这单只提示，不设判据） |

### 3.2 变异（都必须红）

| 变异 | 打在哪 | 期望 |
|---|---|---|
| M1 | 删掉 `exclude_id` 那行 `rows = [s for s in rows if ...]` | **T1 红**（改后定义被自己撞死） |
| M2 | `exclude_id` 只从 `taken` 里排除、不从 `existing` 里排除 | **T1 红**（重名过了、撞车还拒） |
| M3 | `update_service` 里把 `"id": s.get("id")` 改成 `"id": uuid.uuid4().hex[:8]` | **T3 红**（台账脱钩） |
| M4 | `_apply_service_edit` 里删掉 `dept_of(svc) != dept_scope` 那两行 | **T5 红** |
| M5 | `UPDATE` 时绕过 `validate_service_def` 直接 `update_service` | **T4 红**（撞车词能存进去） |
| M6 | 删除时顺手 `os.remove(custom/<sid>.json)` | **T7 红** |

⚠️ M2 是本单**最容易在实现里漏掉**的一条：两个闸一个用 `taken`（从 `rows` 来）、
一个用 `existing`（原来单独调 `list_services()`）。只改一处就只修好一半 ——
T1 会绿（撞车过了），T2 会红（重名还拒）。两条一起测才抓得住。

### 3.3 真机走查（3 分钟）

1. 后勤报修页 → 「宿舍报修」卡上应出现【编辑】【删除】。
2. 点【编辑】→ 弹窗里预填 5 行触发词 → 删掉「报修要多久」→ 保存 → 卡上 pills 变 4 个。
3. **学生端立刻**说「报修要多久」→ 应**不再**出登记卡（改成正常问答）。
   —— 这一步是整单的验收点：改定义能立刻影响对话，不用重启。
4. 学生端说「宿舍灯坏了」→ 仍出登记卡（其余 4 条没被误删）。
5. 点【删除】→ 确认 → 卡消失；去 `app/data/custom/5e702308.json` 看，**里面的记录还在**。

### 3.4 回归

```bash
cd /d/CampusTime && ./.venv/Scripts/python.exe -m pytest tests/ -q
```

重点：`test_75`（E 批 5 条撞车判据）、`test_65`（A3 钉着「删除不存在 → 400」的口径）、
`test_53`（`:239` 那条字符串断言钉着 `validate_service_def(draft)` 的**调用形状**——
加了个带默认值的第二参数，形状里 `validate_service_def(draft)` 这段仍在，应该不红；
跑一遍确认，红的话是它写得太死，改测试不改代码）。

---

## 四、不做的事

1. **不给服务定义加「版本/草稿/回滚」**。改错了再改回来就是，别把半截 CMS 钉上来。
2. **不开放 `name` / `table` 编辑**（理由见 §2.2）。
3. **不迁移历史记录**（理由见 §2.4）。
4. **不修「问句被当成触发词」这个根**。它该修的地方是 `match_service_by_trigger`
   那一层（先判这句在不在问），不是在这单里塞黑名单。**另立一单。**
5. **不动驿站端 DELETE 的口令口径**（理由见 §2.6）。
6. **不与 #105 合并**。#105 是「新增时怎么不撞」，#106 是「建完怎么改」——
   两单都碰 `validate_service_def`，合并会让变异互相污染（#105 的 M1/M2 和这单的 M1/M2 长得像但打的是不同行）。

---

## 五、给执行方的三句

1. **先做 §1.1，再做别的**。不修 `exclude_id`，PUT 写出来是永远 400，而且报「撞了」——
   查不出来，因为撞的是它自己。
2. `exclude_id` 要改**两处取数**（`taken` 和 `existing`），只改一处就只修好一半，见 M2。
3. 删除只删定义、不动台账，口径与驿站端那口**逐字一致**；确认框里把「新服务接不上旧记录」这句真话写上。

---

## 附：这单和 #105 是同一段代码的前后两半

`validate_service_def` 这个函数被两个人从两头拽：

* #105 从**新增**那头拽（撞了别整份毙）
* #106 从**编辑**那头拽（自己别跟自己撞）

两次都在同一个函数上开洞。如果哪天要重构它，**记得它现在服务三种调用**
（新增 / 编辑 / 测试直接调），并且有一个 `exclude_id` 参数是**编辑专用**的。
