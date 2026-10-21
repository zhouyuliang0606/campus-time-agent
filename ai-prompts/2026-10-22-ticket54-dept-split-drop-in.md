# 54｜分流：服务定部门 + 驿站限权 + 管理端接后勤 + 学生「我的上报」（drop-in）

> 来源：2026-10-22 01:41 用户拍板四件 ——「**写分流 + 给管理加后勤 + 学生加上报 + 给驿站限制权限**」。
> 前提：用户已自建「电梯报修」`c8ef8355`（table=`repair`，触发词 5 条，collect_fields 4 个），且明确
> 「知道会进驿站，因为服务是在驿站端建的」——**这正是本单要治的病：系统没有"这项服务归谁"这个维度**。
> 交付性质：施工单，我不改代码。
> **本单取代 52 全文**（52 的 A/B/C 三节并入本单 §二/§四/§三.3），53 因用户已自行重建而作废，
> 51 里「触发词撞车不拦」那一半并入本单 §六。

---

## 〇、先对齐六条现场事实（全部实测，不是推断）

| # | 事实 | 依据 |
|---|---|---|
| 1 | 服务定义**没有"归谁处理"这个维度** | `SERVICE_FIELDS`（`store.py:1549`）只有 name/triggers/collect_fields/reply_template/table |
| 2 | 登记记录**不记是谁报的** | `add_custom_record`（`store.py:1720`）的 `item` 只有 id/ts/service/name/fields/status |
| 3 | 读记录**无任何过滤** | `list_custom_records(sid)`（`store.py:1714`）整份返回 |
| 4 | 驿站端**遍历全部服务**建表单 | `station.html:663 loadServices` 的 `list.forEach` 不区分内置/自建、不区分部门 |
| 5 | 管理端**一条自建服务接口都没调** | `grep "services" admin.html` 只命中一条 CSS 注释 |
| 6 | 学生端**零个自建服务接口** | `grep "services/\|/api/custom" student.html` 无命中 |
| 7 | **进度只有状态，没有时间** | `update_workorder_status:287` / `update_custom_record_status:1771` 都只写 `status`；真实数据 `{'status':'已解决'}` 里没有"何时解决的"，全库唯一带时间的进度字段是 `archived_at`（那是归档，不是处理） |

⇒ 现在的真实形态：电梯报修一旦有记录，**写进驿站台账、管理端看不见、学生提交完就再也找不到**。

**一个必须记住的时间点**：`app/data/custom/` 现在**没有 `c8ef8355.json`**（电梯报修零记录）。
所以本单**不需要迁移任何记录数据**，只有 2 处**服务定义**要补字段（见 §一.3）。

---

## 一、`dept`：服务归谁处理（`app/store.py`）

### 1.1 常量与兜底函数（放在 `is_builtin_service` 附近，`store.py:1636`）

```python
# 服务的承接部门。**只有两档**：驿站管快递/外卖，后勤管设施报修。
# 为什么不做成自由文本：dept 是**权限判据**（§三 的 403 全靠它），
# 自由文本等于给"越权"留了一个错字的口子（"后勤处" / "后勤保障处" 都能绕过相等判断）。
DEPTS = ("驿站", "后勤")
DEPT_DEFAULT = "驿站"


def dept_of(svc: dict | None) -> str:
    """这项服务归谁。**唯一判据**，不许在别处再写一次比较。

    兜底给"驿站"的理由（历史事实，不是偏好）：本单之前所有服务都是在驿站端建的，
    没有 dept 键的老定义归驿站，符合它们**实际发生过的归属**。
    ⚠️ 兜底只服务老数据。新建服务**必须在创建时定 dept**（§一.3），
       不要再靠兜底去猜——猜错的表现是"后勤的单子进了驿站台账"，而不是报错。
    """
    d = str((svc or {}).get("dept") or "").strip()
    return d if d in DEPTS else DEPT_DEFAULT
```

### 1.2 **不要**把 `dept` 加进 `SERVICE_FIELDS`

`SERVICE_FIELDS` 是**模型输出的白名单**（`_SERVICE_PROMPT` 生成的定义逐键过它，见 `validate_service_def:1597`）。
模型不知道学校有几个部门，让它填 dept 只会产出"后勤保障处""宿舍管理科"这种脏值，然后被 §三 的相等判断静默放行或误拒。

⇒ `dept` 由**路由层注入**，模型完全碰不到：

```python
# store.py:1556 附近，add_service 加一个参数（签名改动，调用点见 §一.3）
def add_service(clean: dict, dept: str = DEPT_DEFAULT) -> dict:
    ...
    row = {**clean, "dept": dept if dept in DEPTS else DEPT_DEFAULT, ...}
```

⚠️ `validate_service_def` **一个字不改**。它现在会因为 dept 不在 `SERVICE_FIELDS` 里而把它剔掉——
这是**对的行为**，不是 bug。别为了"保留 dept"去给它加白名单。

### 1.3 两处数据补字段 + 两个创建入口注入 dept

| 位置 | 改法 | 理由 |
|---|---|---|
| `app/data/services.json` 里的 `pkgloss` | 手工补 `"dept": "驿站"` | 文件存在时 `list_services()` 读文件，`_SEED_SERVICES` 不生效 |
| `_SEED_SERVICES` 里那条 `pkgloss`（`store.py:1530` 附近） | 同样补 `"dept": "驿站"` | 删了 services.json 会长回来，两处必须一致（抄一份漏一份是这项目的老病） |
| `app/data/services.json` 里的 `c8ef8355` | 手工补 `"dept": "后勤"` | 电梯报修归后勤，这是本单要立的规矩 |
| `POST /api/station/services`（`main.py:4571`） | `add_service(clean, dept="驿站")` | **驿站端建的一律归驿站**，请求体里就算传了 dept 也不读 |
| `POST /api/admin/services`（§四.1 新建） | `add_service(clean, dept="后勤")` | 管理端建的一律归后勤 |

⚠️ **驿站端不许建后勤的服务**：`station_service_create` 只从 `body` 读 `description`，**不要**加 `body.get("dept")`。
这一条是权限的根，写在注释里：驿站能建后勤的服务 ⇒ 驿站能给自己开后勤的台账。

---

## 二、`owner`：这条记录是谁报的（`app/store.py:1720`）

```python
def add_custom_record(sid: str, fields: dict, svc: dict | None = None,
                      status: str = "", owner: str | None = None) -> dict:
    """
    :param owner: 这条登记**是谁报的**。不传 = 取 `current_student()`；
        驿站/后勤在自己界面现场录的，调用方显式传 `""`（无归属，与 `save_upload` 的
        `owner=""` 同一表达）。
        ⚠️ 不接受调用方传任意学号 —— 理由同 `add_workorder:233`：body 谁都能改，
           能传就等于能冒充。身份只有 `current_student()` 一个来源。
    """
    ...
    item = {"id": ..., "ts": ..., "service": sid, "name": ...,
            "fields": clean, "status": str(status or CUSTOM_STATUS_NEW),
            "owner": current_student() if owner is None else str(owner or ""),
            # ★ 部门**快照**（见下）
            "dept": dept_of(svc),
            }
```

### 2.1 为什么记录里要存一份 `dept` 快照

`delete_service`（`store.py`）**只删定义、不动 `custom/<sid>.json`**（既有设计，理由在那段注释里）。
于是"服务被删了，历史台账还在"是**合法状态**。没有快照的话，那些记录的部门就再也算不出来了——
`list_custom_records` 只能拿到 sid，去 `get_service(sid)` 拿到 None，`dept_of(None)` 直接把它们判成驿站。

⇒ **部门跟着记录走**，与 #48 里"上报带 pkg 快照"是同一手法。历史记录没有 `dept` 键时，
读取端用 `str(row.get("dept") or dept_of(get_service(sid)))` 兜底，**不回填**（回填=编造历史归属）。

### 2.2 两个写入口分别传什么

| 入口 | 传什么 | 为什么 |
|---|---|---|
| `POST /api/custom/records`（`main.py:4706`，学生点确认卡） | 不传（默认 `current_student()`） | 学生报的，系统认人 |
| `POST /api/station/services/{sid}/records`（`main.py:4648`，人家自己点登记） | 显式 `owner=""` | 现场录入没有学生身份，硬取会取到 `demo` 冒充成某个学生 |
| 管理端新口（§四.2，后勤自己录） | 显式 `owner=""` | 同上 |

---

## 三、驿站限权：**后端拦，不靠前端藏**

### 3.1 驿站端四个接口整族加判据

位置全在 `main.py:4565~4695`，**一个不许漏**（这项目吃过"整族只改一处"的亏，见红线 12）：

| 接口 | 行号 | 加什么 |
|---|---|---|
| `GET /api/station/services` | `4565` | 返回 `[s for s in list_services() if dept_of(s) == "驿站"]` |
| `POST /api/station/services` | `4571` | 建出来的固定 `dept="驿站"`（§一.3）；**不读请求体里的 dept** |
| `DELETE /api/station/services/{sid}` | `4605` | `if dept_of(get_service(sid)) != "驿站": 403` |
| `GET /api/station/services/{sid}/records` | `4621` | 同上 403 |
| `POST /api/station/services/{sid}/records` | `4648` | 同上 403 |
| `POST .../records/{rid}/status` | `4667` | 同上 403 |
| `DELETE .../records/{rid}` | `4690` | 同上 403 |

统一写一个**就地小函数**，别在七个地方各写一遍比较：

```python
def _station_only(sid: str):
    """驿站端管辖判据：这项服务归驿站吗。返回 (svc, 错误响应)。

    ⚠️ 403 而不是 404：服务**确实存在**，只是不归你管。
       回 404 会让驿站管理员以为自己建的服务消失了，然后去重建一份（真的会重建，实测过）。
    """
    svc = get_service(sid)
    if not svc:
        return None, JSONResponse({"error": "没有这项服务"}, status_code=404)
    if dept_of(svc) != "驿站":
        return None, JSONResponse(
            {"error": f"「{svc.get('name')}」归{dept_of(svc)}处理，驿站这边不能改。"},
            status_code=403)
    return svc, None
```

**为什么必须是后端**：`/api/station/*` 全部免口令（既有设计，理由在那段注释里，本单不动鉴权模型）。
前端隐藏只挡得住手滑，挡不住任何知道 sid 的请求——而 sid 就印在驿站端界面上（`station.html:686` 显示 `id c8ef8355`）。
所以：**免口令可以接受，跨部门不行**。

### 3.2 驿站端界面只画归自己的

`station.html:663 loadServices`：

```js
const list = (d.services || []).filter(s => (s.dept || "驿站") === "驿站");   // 后端已过滤，这里再兜一道
```

⚠️ **同一个改动里必须做 52-C（内置项不给表单）**：`list.forEach` 里加
`if (s.table === "workorders") { ...只渲染说明，不建 form/按钮... }`。
现在内置项「快递/外卖问题处理」下面那个「登记一条」表单通向 `add_custom_record("pkgloss", …)`，
写的是 `app/data/custom/pkgloss.json` —— **工单里没有、学生看不到、也不按服务分组展示**，是第三本账。
两件事必须同批：只加 dept 过滤而留着内置项表单，驿站台账里就永远有一块谁都解释不清的数据。

### 3.3 驿站端要显示"这项归我"，不要再显示部门判断过程

`station.html:686` 那行 `台账 xxx · id xxx` 后面加 `· 归驿站`。**只是给学生/管理员看的确定性信息，不做交互**。
不要加"切换到后勤"之类的按钮——那等于把 403 变成一个点击就能触发的错误。

---

## 四、管理端接后勤（`admin.html` + 新接口）

### 4.1 两个带口令的接口

```python
@app.get("/api/admin/service-records", dependencies=[Depends(verify_admin)])
async def admin_service_records(dept: str = ""):
    """自建服务的登记台账（管理端那一档的数据源）。

    :param dept: 空 = 全部（默认值，给"排查"用）；传 `后勤` / `驿站` 就只那一档。
    ⚠️ **不含内置项**：内置那项的账在 workorders.json，已由「学生工单」那一档管。
       判据用 `store.is_builtin_service`，别在这儿再写一次 `table == "workorders"`。
    """
    want = (dept or "").strip()
    groups = []
    for svc in list_services():
        if is_builtin_service(svc):
            continue
        if want and dept_of(svc) != want:
            continue
        rows = list_custom_records(str(svc.get("id") or ""))
        live = [r for r in rows if str(r.get("status") or "") != "已解决"]
        groups.append({"id": svc.get("id"), "name": svc.get("name") or "",
                       "dept": dept_of(svc), "table": svc.get("table") or "",
                       "open": len(live), "total": len(rows), "records": rows})
    return {"groups": groups}
```

改状态也要一个带口令的口，**别让管理端去打免口令的驿站口**（打不过去了，§3.1 会 403）：

```python
@app.post("/api/admin/service-records/{sid}/{rid}/status", dependencies=[Depends(verify_admin)])
async def admin_service_record_status(sid: str, rid: str, req: Request):
    # 校验顺序照 station_service_record_status:4667 —— 先 400（状态非法）后 404（不存在）
    # 状态词表只认 _WO_STATUSES（main.py:4391，唯一来源），不许在这里抄第二份
    ...
```

### 4.2 管理端建服务（后勤自己建报修服务）

```python
@app.post("/api/admin/services", dependencies=[Depends(verify_admin)])
async def admin_service_create(req: Request):
    # 与 station_service_create:4571 **同一套**：读 description → _SERVICE_PROMPT → validate_service_def
    # ⇒ 把 4571 里那 20 行抽成 `_draft_service(idea)` 复用，不要在管理端再抄一份
    #   （抄一份 = 下一版改 prompt 时只改一处，另一处静默停在旧 prompt 上）
    return {"ok": True, "service": add_service(clean, dept="后勤")}
```

### 4.3 界面：侧栏加一档「🧰 后勤报修」

- `admin.html:186` 之后插入：
  `<div class="sb-item" data-nav="repair"><span class="ico">🧰</span><span class="lbl">后勤报修</span><span class="sb-cnt" id="repairNum"></span></div>`
  **放在 `wo` 下面**：后勤和驿站是并列的两条承接线，学生工单（驿站那类）在前、后勤报修在后，读起来就是分工。
- `admin.html:218` 之前插 `#view-repair` 面板（照 `view-wo` 的卡片骨架抄），内容：
  - 顶部：`共 N 项服务 · 未结 M 条`
  - 每组一个服务卡：服务名 + `归后勤` + 触发词 pill + 「登记一条」表单（后勤现场录入，`owner=""`）
  - 每组下面：未结列表（列最新 5 条，同 `station.html:831` 的既有上限）+ 已处理折叠
  - 每条**显示 `owner`**（有值就显示学号，空就显示「现场录入」）—— 后勤要能回拨找人
- 切换逻辑 `admin.html:1264` 那一处加 `repair` 分支，拉 `/api/admin/service-records?dept=后勤`。
- 角标 `#repairNum` = 未结总数，跟着 5 秒的收件箱轮询一起刷。

⚠️ 管理端 `adminFetch:434` 非 2xx 会抛异常 ⇒ 新载荷必须 `.catch()` 写进面板，别让一档空白（`loadKB:674` 吃过这个）。

---

## 五、学生端「我的上报」（`student.html` + 一个合并接口）

### 5.1 合并接口（新）

```python
@app.get("/api/my/reports")
async def my_reports():
    """学生自己的上报：**工单 + 自建服务记录**，合成一张表。

    为什么要合并：学生眼里"我报过的事"就一件，不该让他去两个栏目找。
    但两个源形状不同（工单 = kind/desc，服务记录 = fields 字典），
    所以这里**归一成一个固定形状**，前端不写第二套渲染：
      {source:"workorder"|"service", id, sid, title, detail, status, ts, dept}
    —— dept 也要给（学生要知道该去问驿站还是后勤）。

    ⚠️ 三条红线与 GET /api/my/workorders:4359 **逐条一致**：
       ① 认人只靠 cookie（`current_student()`），不读任何参数；
       ② 无身份 → 直接回空表（**不许**落成 demo 口径）；
       ③ 默认带归档（老单也要看得见）。
    ⚠️ `owner` 为空的老记录**一律不出现在这里**——否则每个学生都会看到同一批
       "现场录入"的单子，那是把别人的事摊给所有人看。
    """
```

### 5.2 界面：侧栏加一项「我的上报」

- 插入位置：`student.html:750`（代办事项）之后 —— 与"待办"同属"我的事"，且需要角标。
- `switchNav:2898` 里 `isPanel` 加上 `"reports"`（纯查看、无输入框），并加
  `else if(view==="reports") loadReportsView();`
- `NAV_TITLE` 加 `reports: "我的上报"`。
- 角标 `#reportBadge`：未结数（待处理 + 处理中），与 `#todoBadge` 同一套长相。
- 空态文案写清楚入口：`还没有上报过。快递丢了、电梯坏了，直接在对话里说一句就行。`
- 每条显示：`标题 · 归后勤/驿站 · 状态 · 时间`，`detail` 是 `fields` 拍平后的 `k：v`。

⚠️ 这里不加"撤销/删除"按钮：学生自己删上报，等于把人家已经派出去的单抽掉。要撤只能人工在端上删。

### 5.3 角标的刷新节奏 → **统一归 §5.6 管，本节不留第二套定时器**

`student.html:2771` 只有一个 `setInterval(loadNotices, 30000)`，它**只刷通知、不刷上报**。
"我的上报"的角标**不要搭那个定时器**：它的节奏固定 30 秒，而 §5.6 要求"学生正看着的那一栏 5 秒"，
挂上去等于把那个要求架空——而且角标和面板各管一个定时器，就是两处状态两种节奏，
迟早出现"角标变了、列表没变"（这项目的"抄两份漏一份"就是这么长出来的）。

⇒ 上报表和角标的刷新**只有一个来源**：§5.6 的 `syncReportPolling()`。

---

### 5.4 **进度不能只有状态，得有时间轴**（本节是补漏，原稿只写了"角标"，没写数据）

现状（实测，两个源一样）：

```python
# store.py:287  工单
def update_workorder_status(wo_id: str, status: str) -> dict | None:
    it["status"] = status                      # ← 只写状态，没有时间、没有操作人
# store.py:1771 自建记录
def update_custom_record_status(sid: str, rid: str, status: str) -> dict | None:
    it["status"] = status                      # ← 同上
```

真实数据佐证（`workorders.json` 现状）：

```
{'id': '4338a2bf', ..., 'status': '已解决', 'archived_at': '2026-10-22 01:05:26'}
```

—— 有「已解决」，但**没有"什么时候解决的"**。全库唯一带时间戳的进度字段是 `archived_at`
（`_stamp:343`，那是归档不是处理）。

后果三条，每条都会在答辩时被问：
1. 学生看到「已解决」不知道是哪天解决的，只知道自己报了；
2. 后勤看不出这条压了几天、平均处理时效多少；
3. 你说"全流程闭环"，评委问"处理时效在哪"，答案是一片空白。

#### 改法：加 `trail` 数组，**不要**加 `updated_at` + `resolved_at` 三个散字段

```python
def _append_trail(row: dict, status: str, by: str = "") -> None:
    """给一条记录追加一次状态变更（**原地改**）。全库唯一写"进度"的地方。

    为什么是数组而不是三个时间字段（created_at / updated_at / resolved_at）：
      · 三个字段只能表达"最后一次"，学生问「什么时候开始处理的」就没有答案；
      · 每加一档状态就得再加一个字段（处理中 ⇒ processing_at），正是"加一档改两处"那族病；
      · 处理时效（平均几小时解决）直接从这个数组算，不用再动结构。

    :param by: 谁改的。**目前一律传 `""`** —— 管理端只有口令、驿站端免口令，
        都没有"哪个人"这个身份。宁可留空显示「管理员」，也不许编一个名字。
        将来真加了账号，再往这里填，**不动结构**。
    ⚠️ 老记录不回填：没有 trail 的，读取端兜底成
        `[{"status": row.get("status"), "ts": row.get("ts"), "by": ""}]`
        —— 只有"创建"这一条，**不编造中间过程**（同 `status` / `archived` 那几单的一贯做法）。
    """
    row.setdefault("trail", []).append({"status": str(status), "ts": _now(), "by": str(by or "")})
    row["updated_at"] = row["trail"][-1]["ts"]
```

两个 update 函数各加一行（签名加 `by` 参数，**带默认值** ⇒ 现有调用点一个都不破）：

```python
# store.py:287
def update_workorder_status(wo_id: str, status: str, by: str = "") -> dict | None:
    ...
        it["status"] = status
        _append_trail(it, status, by)          # ★ 新增
# store.py:1771
def update_custom_record_status(sid: str, rid: str, status: str, by: str = "") -> dict | None:
    ...
        it["status"] = status
        _append_trail(it, status, by)          # ★ 新增
```

⚠️ **别在登记写入时补一条 trail**：`add_custom_record` 已经有 `ts` 了，创建那一刻的 trail
由读取端兜底合成（见上面 docstring），写库里存两份同义数据，将来必然对不上。

### 5.5 学生端「我的上报」画的是**进度**，不是一行状态字

每条记录渲染成三步条：

```
① 待处理 ── ② 处理中 ── ③ 已解决
  10-22 01:40          10-22 14:05
```

- 当前步高亮，**已走完的步显示 `trail` 里的时间**（没有就空着，不猜）；
- 未走到的步灰色、无时间；
- 状态词仍只认 `_WO_STATUSES`（`main.py:4391`），前端**不许**自己映射"处理中→进行中"之类的别名；
- `trail` 为空（老记录）时，退化成"一行状态 + 创建时间"，**不画一条全是灰点的假进度条**。

⚠️ 前端**不许**自己算"已经 3 天了"：跨天/时区/夏令时都会算错，而且后端将来换了存储就全错。
   要显示"等待 3 天"这种话，由 `/api/my/reports` 在返回里带上 `age_hours`（后端算一次）。

### 5.6 **两端同步口径**（用户要的"学生看见进度 + 管理端同步"，本节是它的机制）

先把一件事说清楚，免得实现方走偏：**这里不存在"两份数据要同步"**。

工单在 `workorders.json`、自建服务记录在 `custom/<sid>.json`，**学生视图和管理视图读的是同一份文件**
（学生走 `/api/my/reports` 按 owner 过滤，管理端走 `/api/admin/service-records` 按 dept 过滤）。
一份数据两个投影 ⇒ **天然一致**，不需要同步接口、不需要双向写、更不需要定时对账。

要解决的只有一件事：**什么时候重新读**。而现在这条路是断的：

| 事件 | 谁需要立刻知道 | 现在靠什么 | 结论 |
|---|---|---|---|
| 学生提交了上报 | 管理端 / 驿站 | `admin.html:1285` 5 秒轮询、`station.html:627` 5 秒轮询 | ✅ 能用 |
| 后勤改了状态 | **学生** | **没有任何轮询**（`student.html:2771` 那个 30 秒只刷 `loadNotices`） | ❌ **断的** |
| 后勤改了状态 | 管理端自己 | 5 秒轮询 | ⚠️ 改完那 5 秒里按钮还显示旧状态 |
| 学生提交成功 | 学生自己 | 无（停留在对话页，不切栏目就不重拉） | ❌ 学生看不到自己刚报的那条 |

⇒ **"学生看见进度"在现在这版里是零**，不是弱，是没有。这一节就是补它。

#### 5.7.1 学生端：激活栏目 5 秒，其他栏目 30 秒

```js
// student.html，挂在 switchNav:2898 那一套旁边
// 节奏为什么要分两档：
//   · 演示当天是"后勤点一下、学生那屏跟着变"，30 秒观众等不了；
//   · 但常驻 5 秒会让三端加起来每 5 秒打满一轮接口，用量和日志都被灌水。
//   ⇒ 只在**学生正看着的那一栏**用 5 秒，其余栏目回 30 秒（角标跟着那一档走）。
let reportTimer = null;
function syncReportPolling(view){
  const fast = (view === "reports");
  const ms = fast ? 5000 : 30000;
  if (reportTimer && reportTimer.ms === ms) return;   // 节奏没变就别重开（重开=清掉再建，会漏一拍）
  if (reportTimer) clearInterval(reportTimer.id);
  reportTimer = { ms: ms, id: setInterval(function(){
    // 标签页在后台就别打了（学生手机切走很常见），回来时 visibilitychange 会补一次
    if (document.hidden) return;
    loadReportBadge();
    if (currentView === "reports") loadReportsView();
  }, ms) };
}
```

三个必须一起做的点（少一个就还是"不同步"）：

1. **`switchNav` 切进 `reports` 时立刻拉一次**，不等第一个 5 秒。
   `else if(view==="reports"){ loadReportsView(); loadReportBadge(); }`
2. **提交上报成功后立刻重拉**。学生点完确认卡，屏幕上要当场多出一条「待处理」——
   这比任何角标都直接。挂在确认成功的回调里，别等轮询。
3. **`visibilitychange` 回来时补一次**。手机锁屏回来，定时器可能已经被节流，不补就是"打开一看还是昨天的"。

#### 5.7.2 管理端：改完状态立即重画，不等下一轮

`admin.html:1285` 的轮询是 5 秒，**改状态成功后自己再调一次 `loadRepairRecords()`**。
理由很实际：后勤连着处理三条，第 1 条改完到第 2 条之间那 5 秒里，界面上第 1 条还是旧状态，
管理员会以为没点到，于是点第二下 —— 后端是幂等的（重复写同状态），但**界面会闪**，
演示时看起来就是"卡了"。

顺带修一个既有小毛病：`_poll`（`admin.html:1283`）**没判 `document.hidden`**。
本单加的这一档（后勤报修）**要判**，别把既有那三档的毛病复制一份。

#### 5.7.3 明确**不做**实时推送

不给状态同步加 WebSocket / SSE。理由：
- 数据只有一份、变更频率极低（演示当天最多十几条），5 秒轮询的延迟远小于人眼看出来的阈值；
- 项目里 SSE 那条路（`/api/chat`）是为**流式输出**建的，复用它推状态要重构整条连接的语义，
  收益是"快 3 秒"，代价是给已经稳了的东西加一个新故障点。

⚠️ 但**演示脚本要按 5 秒写**：后勤点【已解决】→ 停两秒 → 学生那屏已经变了。
   不要说"实时同步"，说"5 秒内同步"——评委真去掐表时，说"实时"就是自己给自己挖坑。



### 5.7 对话里问进度 —— **需要你拍板，默认不做**

学生现在说「我报的电梯修好了吗」，系统答不上来：模型手里**没有查自己上报的工具**，
只能回一句"请去我的上报看"。

要做就得给模型加一个工具：

```python
# 形状（不给模型传任何参数 —— 参数一露，"查我的"就变成了"查别人的"）
list_my_reports() -> str
    """返回**当前学生**的上报及其进度（工单 + 自建服务记录）。
    无身份时返回"没查到你的上报"，**绝不返回 demo 的全量**。"""
```

**为什么标为待拍板**：它属于判定层（要注册工具、要模型决定什么时候调），
而这一单的其他部分全是确定性接口。混在一单里，出问题时不好定位是哪一半坏的。

⇒ 建议**单独下一单**（55），本单不动。你要现在就做，我就把它并进来。

---

## 六、前置修正：品类认错（**可单独裁掉，不影响 §一~§五**）

不做这一节，分流会做对一半：`帮我报修电梯` 仍会被 `_WORK_ORDER_RE` 的 `(上报|报修)` 一支接走，
`_work_order_kind` 兜底成 **疑似丢件**，写进 `workorders.json` ⇒ **归驿站**。
分流之后，"电梯坏了"进后勤、"帮我报修电梯"进驿站，同一个故障两个去处。

### 6.1 `_work_order_kind`（`planner.py:1681`）补一枝

```python
# 设施报修：电梯/水电/门锁/空调/宿舍设施 —— 这一族**不是丢件**，兜底成"疑似丢件"
# 会让后勤收到一条看不懂的单子（真机实测：电梯报修被标成"上报驿站：疑似丢件"）。
_FACILITY_RE = re.compile(r"(电梯|空调|水龙头|水管|门锁|灯|插座|马桶|下水道|洗衣机"
                          r"|热水器|宿舍.{0,4}(坏|修|报修))")
```

放在"破损/取件码/外卖"那几枝**之前**判——设施词一旦命中就不该再往下猜品类。

### 6.2 触发词撞车校验（原 51 的尾巴）

`validate_service_def:1597` 现在**只拦重名，不查触发词撞车**。补两条：
- 新服务的任一触发词与已有服务互为子串（`a in b or b in a`）⇒ 拒，并**告诉他是哪一项撞了**；
- 触发词 < 4 字 ⇒ 拒（`["快递"]` 实测能吸走「我有个快递」「快递破了」，还能劫持内置工单）。

⚠️ 报错要给建议，不能只说"不合规"：`触发词「快递」和已有服务「快递/外卖问题处理」的「快递丢了」撞了，改成更长的说法，比如「快递破损」。`

---

## 七、验证清单（不跑完不算交付）

### 7.1 变异测试（改坏必须红，改坏不红 = 测试没管到）

| 编号 | 改坏什么 | 期望 |
|---|---|---|
| M1 | `dept_of` 改成恒返回 `"驿站"` | 驿站判据测试必须红（后勤服务能被驿站读写） |
| M2 | 删掉 `_station_only` 的 403 分支 | 「驿站读后勤记录」用例必须红 |
| M3 | `add_custom_record` 的 owner 固定成 `""` | 「学生看到自己上报」用例必须红 |
| M4 | `/api/my/reports` 去掉"无身份回空" | 「无 cookie 调接口」用例必须红 |
| M5 | 记录不写 `dept` 快照 | 「服务删掉后历史记录仍归后勤」用例必须红 |
| M6 | `station_service_create` 读 `body["dept"]` | 「驿站建后勤服务」用例必须红 |
| M7 | `_append_trail` 只 append 不写 `updated_at` | 「进度时间」用例必须红（前端读到空时间会静默显示空白，比报错更难发现） |
| M8 | 读取端把没有 `trail` 的老记录**回填**成一条假历史 | 「老记录不回填」用例必须红（编造历史比没有历史更坏） |
| M9 | `syncReportPolling` 恒定 30 秒（去掉"看着的那栏 5 秒"） | 「后勤改完 5 秒内学生可见」用例必须红（这条是 §5.6 的全部意义，不红等于没做） |

### 7.2 真机（演示当天要走的顺序，一条都不能跳）

1. 学生 A 登录 → 说「电梯坏了」→ 出卡 → 确认 → 回复说"已登记"
2. **驿站端**：服务列表里**没有**「电梯报修」；`curl` 直接打 `/api/station/services/c8ef8355/records` → **403**
3. **管理端**：用口令进去 → 「后勤报修」→ 看到 1 条，`owner` 是学生 A 的学号
4. **学生端**：A 看「我的上报」→ 1 条「电梯报修 · 归后勤 · 待处理」，角标 1
5. 学生 B 登录 → 「我的上报」→ **空**，角标 0
6. **两个浏览器窗口并排**（学生 A 停在我的上报，后勤停在后勤报修）。后勤把状态改成「处理中」→ **掐表：学生那屏 5 秒内**进度条走到第 2 格、第 1 格显示时刻，角标仍是 1
7. 再改成「已解决」→ 学生那屏 5 秒内进度条满格、第 3 格显示时刻，角标归 0；**刷新页面后时间还在**（证明落盘了，不是前端临时算的）
8. 后勤**连续改三条**：每改完一条，管理端界面上那一条**立即**变成新状态（不等下一轮 5 秒），不许出现"点两下才生效"的观感
9. 学生端切到「日程」栏再切回「我的上报」→ **立刻**是最新数据（不等轮询）
10. 学生端在「我的上报」栏提交一条新上报 → 列表**当场**多出一条「待处理」，角标 +1（不等轮询）
11. 学生端把浏览器切走（另一标签 / 锁屏）20 秒再切回 → 数据是新的（`visibilitychange` 补刷生效）
12. 翻一条**老记录**（`bb16afc5.json` 里那条 `dbab9d17`，现在只有 `status` 没有 `trail`）→ 显示成「一行状态 + 创建时间」，**不许画出一条带时间的假进度条**
13. `curl` 不带 cookie 打 `/api/my/reports` → `{"items": []}`（不是 401、不是全量）
14. 驿站端「快递/外卖问题处理」那一项下面**没有**「登记一条」表单（§3.2）

### 7.3 回归（这几条以前修过，本单不许弄坏）

- `/api/my/workorders` 三条红线（cookie 认人 / 无身份回空 / 默认带归档）—— §5.1 的新口是照它抄的，抄错了就是新病
- `station.html` 的 5 秒轮询**不许**把管理员正在填的表单冲掉（`svcSig` 那道闸，:675）
- `_WO_STATUSES`（`main.py:4391`）仍是唯一状态词表，管理端新口不许自建第二份
- 免口令那三条路由（`/api/station/*`）**行为不变**，只是多一道"是不是驿站的服务"的判断

---

## 八、与 51/52/53 的归并关系（**请按这个销账**）

| 旧单 | 处理 |
|---|---|
| **52**（管理端登记视图 / 补 owner / 内置项不给表单） | **全文作废，内容并入本单** §二、§四、§3.2 |
| **53**（重建两项自建服务） | **作废**——用户已自行重建「电梯报修」`c8ef8355` |
| **51**（快递丢失被自建服务抢走 / 删重复服务 / 补词 / 拦撞车） | 前半已被用户删服务解决；**「拦撞车」并入本单 §六.2**；「补词」不需要了（`c8ef8355` 已建、`pkgloss` 没被改） |
| **49、50** | 与分流无关，各自独立 |
| **48**（快递外卖学生侧闭环） | 已交，本单 §5.1 的学生视图**要能容纳它**（工单那一路已在 `/api/my/workorders` 里） |

---

## 九、给执行侧的三条纪律

1. **整族扫**：`/api/station/*` 是七个口，§3.1 一张表全列了。少改一个 = 驿站能从那个口越权，**而且测试会全绿**（因为没人测那个口）。
2. **不许把"部门"做成自由文本**：`DEPTS` 是元组，`dept_of` 是唯一判据。任何地方写 `if svc.get("dept") == "后勤"` 都是错的——老定义没有这个键，会静默判成"不是后勤"，后勤的单子永远进不来。
3. **不要动鉴权模型**：`/api/station/*` 仍免口令，`/api/admin/*` 仍吃 `X-Admin-Token`。本单的"限制权限"是**按服务归属**拦，不是加口令——加口令会牵动 `station.html` 的全部 fetch（含流式那一路），风险远大于收益。
4. **进度类展示一律读后端算好的值**：`trail` 里的时间、"等了多久"、"有没有走完"，全由后端给。前端只负责画。任何在 JS 里 `new Date() - ts` 的写法，演示当天换个时区就会显示成负天数。
5. **一个屏幕只有一个定时器来源**：学生端"我的上报"的列表与角标共用 §5.6 的 `syncReportPolling`，不许再挂第二个 `setInterval`。两处各刷一半 ⇒ 迟早"角标变了列表没变"，而且查起来要同时看两个地方。
