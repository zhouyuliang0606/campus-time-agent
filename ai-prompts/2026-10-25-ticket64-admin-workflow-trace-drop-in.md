# 64｜管理端「Agent 工作流」视图：把每一轮的推理链落盘并画出来（drop-in）

> 目标：演示时**评委问一句 → 管理端 2 秒内出现这一轮 → 点开就能指着讲**
> 「它把这句话判成了什么、走了哪条路、调了哪几个工具、每步拿到什么」。
>
> 这不是"工作流编排"（那是另一回事，会撞"写库必须学生点头"的红线）。
> 这是**只读的可观测性**：同一个 Agent，换一个视角看它。

---

## 〇 现场事实（2026-10-25，HEAD = b36c8ad，实测）

### 1. 现在管理端**看不到**任何 Agent 轨迹，因为轨迹根本没落盘

| 存了什么 | 字段 | 有轨迹吗 |
|---|---|---|
| `app/data/sessions.json` | `{role, content}` | **没有** |
| `app/data/llm_usage.jsonl` | `ts / user / model / prompt_tokens / completion_tokens / latency_ms / status / error` | **没有**（连 module 都没有） |
| `/api/admin/messages` | 学生主动发的消息收件箱（`store.list_messages`） | **没有** |

⇒ trace 只活在 HTTP 响应里，前端渲染完就没了。**管理端现在想看也看不到，不是没做页面，是没数据。**

### 2. `_chat_json` 是唯一的汇聚点（这一条决定本单只改一处）

```
main.py:1664  @app.post("/api/chat")  → 只做"选出口"
            ├─ 1685  return await _chat_json(req)                    ← JSON 出口
            └─ 1704  resp = await _chat_json(req, emit=emit)         ← SSE 出口
main.py:1736  async def _chat_json(req, emit=None):
                1742  resp = await _chat_impl(req, meta, emit=emit)  ← 几十个 return 全在里面
                1748  head = [read] + reasons                        ← 已经在这里统一拼过一次
                1752  resp["trace"] = head + tr
                1755  return resp                                    ← ★ 落盘点就在这前面
```

`:1744` 那句注释自己写着「这函数有几十个 return，逐个补必漏」——
**落盘同理，所以只在 `_chat_json` 的 `return resp` 前记一笔，一处覆盖全部。**

### 3. 项目里已有 JSONL 追加的标准写法（照抄，不新发明）

`store.py:1600 log_llm_usage` —— 三条要点：
① 独立 env 覆盖路径（`CAMPUSTIME_USAGE_FILE`）给**不进沙箱的测试**用；
② 内部 `except: pass`，**对外承诺不抛异常**（"记账是旁路，对话才是主线"）；
③ 追加写，坏行跳过。

### 4. `resp` 里已经够用了

`resp` 是 dict 时含 `module / session_id / answer / trace / options / awaiting_choice`。
⇒ **不需要改 `_chat_impl` 的几十个 return**，落盘所需的东西在出口处全都有。
只差两个：`owner`（哪个学生）与 `agent_mode`（这一轮是不是 Agent 模式），
这两样从 `meta` 拿（`meta` 是 `_chat_impl` 的小本子，已经有 `read` / `reasons`）。

---

## A 落盘（`app/store.py` + `main.py` 各一处）

### A1. `store.py`：照 `log_llm_usage` 的样子加三个函数

放在 `clear_llm_usage()` 之后。

```python
# ============ Agent 轨迹流水（#64：管理端「Agent 工作流」页的数据源） ============
# 为什么另起一个文件、不塞进 llm_usage.jsonl：
#   那本是"模型调用账"（一次调用一行、给用量统计看），这本是"一轮对话一行、给演示看"。
#   两种粒度混在一份文件里，统计和回放都会各算各的。
TRACE_FILE = "trace_log.jsonl"
TRACE_ROW_MAX = 64 * 1024       # 单行上限，超了就砍 trace（宁可少记，不能让日志把一个轮次撑爆）


def _trace_path() -> str:
    """轨迹流水的位置（`CAMPUSTIME_TRACE_FILE` 覆盖，理由同 `_usage_path`）。"""
    return os.environ.get("CAMPUSTIME_TRACE_FILE") or _path(TRACE_FILE)


def log_trace(record: dict) -> None:
    """追加一轮对话的轨迹。**对外承诺不抛异常**——旁路，对话才是主线。"""
    try:
        os.makedirs(os.path.dirname(_trace_path()) or ".", exist_ok=True)
        row = dict(record or {})
        row.setdefault("ts", _now())
        row.setdefault("id", uuid.uuid4().hex[:12])
        line = json.dumps(row, ensure_ascii=False)
        if len(line) > TRACE_ROW_MAX:
            # 砍 trace 尾部，保住这一轮的"骨架"（判定 + 前几步）
            row["trace"] = (row.get("trace") or [])[:20]
            row["truncated"] = True
            line = json.dumps(row, ensure_ascii=False)
        with open(_trace_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_trace_log(limit: int = 100, *, module: str = "",
                   agent_mode: str = "", only_tools: bool = False,
                   q: str = "") -> list:
    """读最近 limit 轮（**新的在前**）。

    筛选放在这里、不放在路由里（项目老规矩：判据只写一处，两处各判迟早一处藏一处不藏）。
    返回的每一行**去掉完整 trace**，只留摘要 —— 列表页不需要几万字，
    要看细节走 `get_trace_row(id)`。
    """
    rows: list = []
    try:
        with open(_trace_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    if module:
        rows = [r for r in rows if str(r.get("module") or "") == module]
    if agent_mode in ("1", "0"):
        want = agent_mode == "1"
        rows = [r for r in rows if bool(r.get("agent_mode")) is want]
    if only_tools:
        rows = [r for r in rows if int(r.get("tool_calls") or 0) > 0]
    if q:
        rows = [r for r in rows if q in str(r.get("answer") or "")
                or q in json.dumps(r.get("trace") or [], ensure_ascii=False)]
    rows = rows[-max(1, int(limit or 100)):]
    rows.reverse()
    return [{k: v for k, v in r.items() if k != "trace"} for r in rows]


def get_trace_row(row_id: str) -> dict | None:
    """按 id 取一整轮（含完整 trace）。"""
    try:
        with open(_trace_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if str(r.get("id") or "") == str(row_id):
                    return r
    except Exception:
        return None
    return None


def clear_trace_log() -> int:
    """清空轨迹流水，返回清掉的行数（管理端"清空"按钮用，跟用量清理同款）。"""
    n = 0
    try:
        with open(_trace_path(), encoding="utf-8") as f:
            n = sum(1 for ln in f if ln.strip())
        open(_trace_path(), "w", encoding="utf-8").close()
    except Exception:
        pass
    return n
```

> `store.py:12` 已有 `import uuid`，**不用加**。

### A2. `main.py`：`meta` 回填两样（`_chat_impl` 里，紧挨着 `:2026 _agent_mode = ...`）

```python
    # #64：出口落盘要用"谁在问"和"是不是 Agent 模式"，在算出来的地方就地记进小本子
    #      （跟 `meta["read"]` 同一个用法：判定在各处做一次，出口统一取）。
    meta["agent_mode"] = _agent_mode
    meta["owner"] = current_owner()          # 驿站/管理端可能是空串，那就记空，不编
```

⚠️ `current_owner()` 定义在 `store.py:62`，但 **`main.py` 现在没有 import 它**
（`main.py:268` 走的是 `set_current_student(...)` 那条）。所以要在 `main.py:42` 那个
`from app.store import (...)` 的括号里补上一个 `current_owner`，否则这里是 `NameError`。
这个错会在**每一条** `/api/chat` 上炸，测试能抓到，但别等它抓。

### A3. `main.py`：出口落盘（`_chat_json` 的 `return resp` 之前，你唯一的落盘点）

```python
    # ★ #64：这一轮"Agent 怎么走的"记一笔 —— 管理端「Agent 工作流」页的数据源。
    #   为什么只在这儿写：`_chat_json` 是唯一汇聚点（JSON 与 SSE 两条出口、`_chat_impl`
    #   几十个 return 全从这儿出去）。跟上面拼 `read`/`reasons` 同一个道理，一处覆盖全部。
    #   存**原始** trace 而不是学生视图：管理端要看的是"模型实际拿到了什么"，
    #   学生那份已被 `public_view` 收掉内部词、截到 120 字，看它等于看二手。
    if isinstance(resp, dict):
        try:
            _tr = [t for t in (resp.get("trace") or []) if isinstance(t, dict)]
            # 每条 result 收到 1200 字：管理端够看，又不至于一轮就几十 KB。
            # 学生端的 120 字是"别让人看内脏"，这里是"别把日志撑爆"，两个理由，两个数。
            _tr = [dict(t, result=(str(t["result"])[:1200] + "…"))
                   if isinstance(t.get("result"), str) and len(t["result"]) > 1200
                   else t for t in _tr]
            _acts = [str(t.get("action")) for t in _tr if t.get("action")]
            log_trace({
                "sid": str(resp.get("session_id") or ""),
                "owner": str(meta.get("owner") or ""),
                "module": str(resp.get("module") or ""),
                "agent_mode": bool(meta.get("agent_mode")),
                # 每调一次工具在 trace 里占**两条**（"调用工具" + "工具结果"），
                # 所以真实调用次数是 action 条数的一半 —— 别把它当次数直接印出去。
                "tool_calls": len(_acts) // 2,
                "tools": _acts,
                "steps": len(_tr),
                "answer_len": len(str(resp.get("answer") or "")),
                "options_n": len(resp.get("options") or []),
                "trace": _tr[:60],
            })
        except Exception:
            pass      # 落盘是旁路：它挂了也绝不能让这一句话失败
```

**为什么套两层 `try`**：`log_trace` 内部已经吞异常了，但**拼 record 这一层也会挂**
（`resp["trace"]` 里混进不可 JSON 化的东西、`meta` 不是 dict 等）。旁路要"在任何情况下都不影响主线"。

---

## B 管理端接口（三个，全 `verify_admin`）

`main.py`，挂在 `/api/admin` 那一族旁边。

```python
@app.get("/api/admin/trace", dependencies=[Depends(verify_admin)])
async def admin_trace(limit: int = 100, module: str = "", agent_mode: str = "",
                      only_tools: bool = False, q: str = ""):
    """Agent 工作流列表（只读）。**不含完整 trace**，点开才取。"""
    return {"rows": read_trace_log(limit=limit, module=module,
                                   agent_mode=agent_mode,
                                   only_tools=only_tools, q=q),
            "modules": sorted({str(r.get("module") or "") for r in read_trace_log(limit=500)}) ,}


@app.get("/api/admin/trace/{row_id}", dependencies=[Depends(verify_admin)])
async def admin_trace_one(row_id: str):
    """取某一轮的完整轨迹（只读）。"""
    row = get_trace_row(row_id)
    if not row:
        return JSONResponse({"error": "这一轮找不到了（可能已被清空）"}, status_code=404)
    return {"row": row}


@app.post("/api/admin/trace/clear", dependencies=[Depends(verify_admin)])
async def admin_trace_clear():
    """清空轨迹流水（只清这本旁路日志，不碰任何业务数据）。"""
    return {"cleared": clear_trace_log()}
```

⚠️ **`/api/admin/trace/clear` 与 `/api/admin/trace/{row_id}` 的路由顺序**：静态段 `clear` 要先注册，
否则 `row_id` 会把 `clear` 当 id 吞掉（这类"顺序即判据"的坑本项目已经踩过）。
**把这个接口放到 `{row_id}` 那条之前。**

---

## C 管理端页面（`app/static/admin.html`，新增一个页签）

### C1. 列表（默认视图）

一行一轮，新的在上：

| 列 | 内容 |
|---|---|
| 时间 | `ts` 的 `HH:MM:SS` |
| 学生 | `owner`（空就显示「—」） |
| 模块 | `module` |
| 模式 | `agent_mode` 为真时一枚 `Agent` 小标（**用页面已有的那套线性 SVG，不要用 emoji**：`⚙`(\u2699) 与 `🧭`(\U0001F9ED) 都在 `test_44` 的 `RETIRED` 下线清单里，上一轮已经栽过一次） |
| 工具 | `tool_calls` 次 |
| 轨迹 | `steps` 行 |
| 摘要 | `answer_len` 字 + 前 40 字（列表接口没回 answer 正文，取不到就只显字数） |

筛选条：模块下拉（用接口回的 `modules`）+ 模式（全部/Agent/普通）+ 「只看调过工具的」勾选 + 关键词。

### C2. 详情（点开一行）

**顶部一条流水线**，这是演示时手指要指的地方：

```
[ ① 判定 ] → [ ② 路由 ] → [ ③ 思考 ] → [ ④ 工具 ] → [ ⑤ 提案 ] → [ ⑥ 回答 ]
```

点亮规则（按 trace 各步的 `phase` 归类，**不做分支 ID**，理由见"不做的事"）：

| 段 | 命中条件 |
|---|---|
| ① 判定 | `phase` 含 `我把这句听成` |
| ② 路由 | `phase` 含 `🧭` |
| ③ 思考 | `phase` 含 `🤔` |
| ④ 工具 | 有 `action` 字段的步 |
| ⑤ 提案 | `options_n > 0` |
| ⑥ 回答 | `phase` 含 `💡` |

下面接时间轴，每步一行，按类型上色；`🔧 调用工具` 那行带 `args` 展开，
`📦 工具结果` 那行带 `result` 全文折叠（默认展开 —— 演示时不用再点一次）。

**这一屏就是"Agent 的工作流"**，比学生端那份强在三点：① 全量不被截断；② 有工具名和参数；
③ 能同时看到"判定 → 路由 → 工具"三段的关系，学生端只有一条竖线。

### C3. 实时（演示的关键，别省）

演示时最有效的一句是"你刚问的那句，这儿已经有了"。做法：
**每 3 秒查一次 `/api/admin/trace?limit=20`，对比首行 `id`，变了就刷列表并提示一行「+1 轮」。**

- 不引入 WebSocket（成本不值）
- 页面不在前台时不轮询（`document.hidden`），省电也省日志
- 轮询只在「Agent 工作流」页签激活时开，切走就停

---

## D 演示怎么用（写进单子，因为这是本单的目的）

**主剧本（约 90 秒）**：

1. 学生端问定稿句「帮我看看周一到周三哪天课最少，然后那天排两小时做题」
2. 切到管理端「Agent 工作流」→ 2 秒后这轮出现在最上面 → 点开
3. 指着流水线讲：**判定**（听成"问事"、依据是语义层）→ **路由**（给了什么否决依据）→
   **思考**（3 次）→ **工具**（`get_weekly_timetable` / `find_free_slots` …）→ **提案**（1 张卡）→ **回答**
4. 回学生端点【确认】→ 再刷新 → **多出第二轮**：0 工具、3 行，`phase` 是「📋 按学生勾选写入」那一族
   ⇒ 这一对比正好说明红线：**写入是系统做的，不是模型做的**

**对比剧本（约 40 秒，讲 Agent 模式用）**：
同一句「我这周有什么待办」问两次（一次不开 Agent 模式、一次开），列表里两行并排：
`3 行 / 0 工具` vs `22 行 / 8 次`。不用解释，数字自己会说。

> ⚠️ 但第二个剧本有个已知缺陷先修：#63 说的"一周待办被逐日查 7 次"。
> 修之前拿它演示，评委会看到同一把工具连点七次 —— **先做 #63，再演这个对比**。

---

## 验证

### 变异（打在判据上游）

| 编号 | 变异 | 期望 |
|---|---|---|
| Y1 | 删掉 A3 那段落盘 | 管理端列表**空**，真机用例必须红（证明落盘真的在起作用，而不是读了别的东西） |
| Y2 | `read_trace_log` 的 `module` 筛选注释掉 | 按模块筛选用例必须红（防止"筛选是装饰"） |
| Y3 | `tool_calls = len(_acts) // 2` 改成 `len(_acts)` | 断言"调 2 次工具显示 2"的用例必须红（**这条是防"把调用和结果两条算成两次"那个高价错误**） |
| Y4 | `log_trace` 里的 `except: pass` 改成 `raise` | 用一份**不可写**的 `CAMPUSTIME_TRACE_FILE` 跑一轮 → 对话**必须照常成功**（旁路不许拖垮主线） |
| Y5 | `clear` 路由挪到 `{row_id}` 之后 | `/api/admin/trace/clear` 返回 404 或被当 id（顺序即判据） |

### 真机

1. 学生端问一句 → 3 秒内管理端出现一行，`module` / `agent_mode` / `tool_calls` 与实际一致。
2. 点开详情：流水线六段的点亮情况与 `trace` 内容对得上（人工核对一轮即可）。
3. 学生点【确认】→ 新增一轮，`tool_calls=0`（**证明写入路径不经过模型**，这条是红线取证）。
4. 未登录直接访问 `/api/admin/trace` → 401/403（跟其它 admin 接口一致）。
5. 落盘失败演练：把 `CAMPUSTIME_TRACE_FILE` 指到一个不存在的盘符，跑一轮 → 对话正常、日志无 traceback。
6. **演示前置检查**：`trace_log.jsonl` 在跑测试时**不许**被写进真实数据目录
   （照 `CAMPUSTIME_USAGE_FILE` 那条老账：测试里给沙箱设 `CAMPUSTIME_TRACE_FILE`，
   否则跑一遍全量就污染了演示用的台账）。

### 回归

- 全量 `pytest`（基线见 #62/#63 收工时的总数）。`_chat_json` 是每条 `/api/chat` 都过的路径，
  **任何与它对不齐的断言都要逐条看**。
- **性能**：`_chat_json` 每轮多一次文件追加（打开+写+关）。实测 p50 3.7s 的链路上这是毫秒级，
  但要在报告里给出加之前/之后的 `latency` 对比，别口头说"可以忽略"。

---

## 不做的事

1. **不做"重放 / 重跑"按钮**。重跑会走写库分支，那是本单最不该碰的东西。
2. **不给 trace 加"分支 ID"**。`_chat_impl` 有几十个 `return`，逐个加标记必漏
   （`_chat_json` 的注释已经为此吃过一次亏）。**用 `phase` 归类**，归不上就原样显示，
   宁可少一段高亮，不要一份会漏的 ID 表。
3. **不改学生端的轨迹渲染**。学生那份是"别让人看内脏"，管理端这份是"就是要看内脏"，
   两个视图各司其职，不要为了统一把它们合成一个。
4. **不引入数据库**。继续 JSONL 追加（理由同 `log_llm_usage`：写坏了只丢一行）。
5. **不做工作流编排**（拖拽节点那种）。那是另一件事，而且会撞红线，见本单开头。
6. **不加实时 WebSocket**。3 秒轮询足够，别为演示引入第二个协议栈。

---

## 给执行方的三句

1. **落盘点只有一个：`_chat_json` 的 `return resp` 之前。** 别去 `_chat_impl` 里那几十个
   `return` 各加一遍 —— 那句注释「逐个补必漏」就是上一次的学费。`resp` 在出口处已经齐了。
2. **`tool_calls` 是 `action` 条数的一半。** 引擎每调一次工具会记**两条**（调用 + 结果），
   直接把条数印成"调了 16 次"会当众说错话。Y3 那条变异就是钉这个的。
3. **存原始 trace，不存 `public_view` 那份。** 管理端要看"模型实际拿到了什么"，
   学生端那份已经被收过内部词、截到 120 字。两个视图的取舍不同，别为了"少存点"把原始也收掉。
