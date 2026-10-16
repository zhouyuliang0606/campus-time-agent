# 22 · 后续三单：低代码接判定层 / 请求体非 UTF-8 回 400 / 驿站改状态真机验证

> 承接 18 号。三件事性质不同：①功能 ②横切 bug ③验证规程。锚点已实核（2026-10-16）。

---

## 单子 4 —— 低代码接进判定层（学生端自动命中 → 确认卡）

### 目标
学生说「**我丢了饭卡**」→ 自动命中「失物招领」这项自定义服务 → 出**确认卡** → 学生确认 → 写 `custom_records.json`。
（这才是自定义服务"真正能用"的那条路；18 号单子 3 只做到"驿站端能建/能用"。）

### 现状锚点
- 判定层三档在 `app/agent/intent.py`：**样本库 → 语义层 `classify_with_llm` → 关键词兜底**；
- 意图白名单 `KNOWN_INTENTS`（`intent.py:54`）、**类型/module 白名单**（`intent.py:60+`）、`INTENT_MODULE`（`intent.py:87`）、模块注册表 `REGISTRY`（`main.py:150`）。

### 改哪段
1. **动态 module 白名单**：新增 `_custom_service_modules()` 读 `services.json` → 产出 `custom:<id>` 集合，并入"合法 module"判定（语义层给的 module 不在白名单就丢弃——这条**必须保持**）。
2. **让语义层看得见自定义服务**：把已建服务清单（name + triggers + 一句说明）拼进 `classify_with_llm` 的提示，让模型能返回 `module="custom:<id>"`（**仍只回 `{module, intent}`，不碰写库**）。
3. **关键词前置（确定性、省一次调用）**：从 `services.json` 建 `trigger → service_id` 映射，命中即路由；放在**关键词兜底那一档**，别打乱「样本库 → 语义」的既有顺序。
4. **`custom` 模块**：`REGISTRY` 加 `"custom"`（prompt 函数按 `service_id` 现读 `services.json` 拼）；工具 `propose_custom_record(service_id, fields)` → 出确认卡（走 `app/agent/pending.py`）→ 学生确认 → **系统**写 `custom_records.json`。
5. **前端**：学生端能渲染自定义服务的确认卡（**复用现有确认卡组件**，别新造）。

### 红线
- 判定层只判"归不归这项服务"，**执行/写库仍在系统侧**；模型拿不到写库工具。
- 语义层给的 `service_id` **必须在 `services.json` 里真实存在**，否则丢弃（防模型瞎编服务名）。

### 验证
`tests/test_54_custom_service_intent.py`：命中触发词 → module 正确；语义层返回不存在的 service_id → 丢弃；确认卡未点 → 不写库；点了 → 写 `custom_records.json`。
**含反向验证**（把 trigger 匹配打回"修复前"，应复现"自动命不中"）。

---

## 单子 5 —— 请求体非 UTF-8 → 400（横切收口）

### 现状（实核）
- `await req.json()` 全项目 **23 处，全在 `app/main.py`**；
- `app/main.py` **目前没有任何 `exception_handler`**；
- 非 UTF-8 body → `req.json()` 抛 `JSONDecodeError` / `UnicodeDecodeError` → 未捕获 → **500**。

### 改哪段（推荐「单点收口」，别改 23 处）
在 `app/main.py` 加**全局异常处理器**（一处生效全场）：

```python
import json as _json
from fastapi.responses import JSONResponse

@app.exception_handler(_json.JSONDecodeError)
@app.exception_handler(UnicodeDecodeError)
async def _bad_body(request, exc):
    return JSONResponse({"error": "请求体不是合法的 JSON（请用 UTF-8 编码）"}, status_code=400)
```

- **一个改动覆盖 23 个调用点**，不用去每个 `await req.json()` 包 try。
- 保留 FastAPI 对 `RequestValidationError` 的默认 422，二者不冲突。

### 可选（更显式，非必须）
抽一个 `async def json_body(req) -> dict`，失败抛 400，逐步替换 23 处。

### 验证
`tests/test_55_bad_body_400.py`：对 `/api/chat`、`/api/workorders`、`/api/student-message`、驿站两个新接口各发一段**非法 UTF-8 字节** → 断言全部 **400**（不是 500），且响应体是**人话**（不含堆栈）。
> ⚠️ **别断言成 422**——422 是"字段缺失"的错误码，这里是"解码失败"，语义不同。

---

## 单子 6 —— 驿站「改状态」正路径 · 真机验证规程（不动真实数据）

### 你的现状（已做，很好）
负路径复核 404/400/400、`workorders.json` **md5 未变**。缺的只是**正路径真机验**。

### 推荐做法：真机跑，但把数据指到"副本"（等同真机、又不碰真实数据）
1. `cp -r app/data <临时目录>/data`（快照真实数据）；
2. 起服务时设 `CAMPUSTIME_DATA_DIR=<临时目录>/data`（项目**本来就用这个环境变量**做测试隔离，`EXPLAIN.md` 五·补有述）；
3. 用**真实 HTTP**（curl 或真机浏览器）走一遍：建工单 → 驿站端改状态 → 复核状态**真变**；
4. 复核**真实** `app/data/workorders.json` 的 md5 **没变**；
5. 收尾删临时目录。

> 验的是"真服务 + 真路由 + 真 HTTP"，**只有数据是副本**——正路径证据拿到，又满足"不动真实数据"的原则。

### 备选（若你接受留一条无害自测工单）
直接建一条标注清楚的工单（`kind="自测-可删"`），改状态后不删——演示库里多一条待处理工单也无伤大雅。

### 证据留档
把**两次 md5**（真实数据目录）+ 正/负路径结果，贴进 `ai-prompts/` 快照，作为「驿站改状态」这条链的验收凭证。

---

## 三件事的优先级
1. **单子 5（400 收口）**：一处改动、收益横切 23 个接口、风险低 → **先做**。
2. **单子 6（验证规程）**：不改代码、补证据 → **顺手做**。
3. **单子 4（接判定层）**：真功能、工作量最大，但它是"自定义服务真正能用"的关键、直连**锁死的描述** → 排进驿站低代码主线。
