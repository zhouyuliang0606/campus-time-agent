# 60｜让轨迹读起来是 ReAct：判定层进 trace + result 收写侧 + Observation 折单行（drop-in）

> 现场：2026-10-24。58 号修完 A 之后，轨迹能看见了（`action`/`result` 都露）；但用户看完 10 行仍说
> **"没看到在哪体现 ReAct"**——结论：**只露了 Act + Observe，Reason 那半截是空的**。
> 本单补三件：① 判定层进 trace（补 Reason）；② `result` 收在写侧（内部词不外泄）；③ Observation 折单行 + 限高（不收起）。
> ⚠️ 本单**在 58 的 `renderTrace` 之上改**，不要从旧版重来。

## 〇、现场事实（实测）

| 事实 | 证据 |
|---|---|
| Act/Observe 已能露 | 58 号 A；`engine.py:145-147`（调用工具带 action+args）、`:216-223`（工具结果带 result） |
| **Reason 为空** | `engine.py:128` 的 `🤔 思考` 没有内容字段；空标记被前端过滤；**判定层的结论根本没进 trace** |
| `result` 是写给模型的语料 | 工具返回值里含「已向前端展示…」「确认条」这类**给模型的操作说明** |
| 10 行是平铺的 | 轮次只能靠 `step` 数字自己脑补 |
| 演示句已定 | 「帮我看看周一到周三哪天课最少，然后那天排两小时做题」→ 10 行 / 3 工具 / 跨 3 轮 / 末尾 1 张确认卡 |
| 58 的两组候选句**不成立** | planner 首选句被 **KB 直答**接走；express 两句模型一次 `list_pending_pickup` 就够，天然到不了 ≥2 step |

**为什么看不出 ReAct**：ReAct 是 `Reason → Act → Observe`，现在只有后两段。那 10 行读起来是"工具调用流水"，
不是"想→做→看→再想"。**评委的直觉是对的。**

## 一、A（补 Reason）：判定层进 trace

### 1.1 现状

判定层（规则命中 / 语义分类 / 三种否决）在 `_chat_impl` 里算完就只留一个结论：
`main.py:1779` `meta["read"] = _read_step(_sem, _sem_intent, _rule_hit)`，
出口 `_chat_json`（`:1625-1630`）把它作为 **step 0** 的一条插在轨迹最前面。

一条「我把这句听成什么」不够——它只说明"听成了什么"，没说明**为什么**、也没说明**否决了哪些**。
评委要看的恰恰是后者。

### 1.2 修法：加一个 `reasons` 列表，不动 `read`

**① 新增 `_reason_steps()`**（放 `_read_step` 旁边）：

```python
def _reason_steps(*, rule_hit, asks, qa_view, need_review,
                  sem, sem_intent, sem_module, physio) -> list[dict]:
    """判定层的「为什么」，最多 3 条，每条 = 一条可复现的推理依据（step 0，不占轮次）。

    ⚠️ 这里**不是**模型的思维链，别编。它只陈述判定层真的算出过的东西：
       规则命中了什么 / 语义层判成什么 / 哪条否决生效。
    ⚠️ 只在**真的判过**时才出条目：规则没命中、语义层也没调（`sem is None`）时返回 []。
    ⚠️ 文案是给学生/评委看的，不许写 `_sem`、`module_key`、`faq` 这种内脏词。
    """
```

条目形状与现有 `read` 一致（`{"step": 0, "phase": "🧭 判定", "answer": "..."}`），
`step: 0` 前端不画编号（`student.html:1060` 那条约定），正好当"开场的前置说明"。

**文案口径（写"为什么"，不写"做了什么"）**：

| 好 | 坏 |
|---|---|
| `含「周一到周三」+「排两小时」→ 归日程：要查课表再出时段` | `进入 planner 模块` |
| `问的是"哪天课最少"，不是要删改 → 不判为办事` | `intent = query` |
| `「我饿了」是状态句 → 强制按聊天走，不出卡`（只在真的触发时出） | `physio_state_only = True` |

**② 在第 1779 行旁边记下来**：

```python
    meta["read"] = _read_step(_sem, _sem_intent, _rule_hit)
    meta["reasons"] = _reason_steps(
        rule_hit=_rule_hit, asks=_asks, qa_view=_qa_view, need_review=_need_review,
        sem=_sem, sem_intent=_sem_intent, sem_module=_sem_module, physio=_physio,
    )
```

**③ 出口一次拼上**（`_chat_json`，`:1625-1630`）：

```python
    read = meta.get("read")
    reasons = [r for r in (meta.get("reasons") or []) if isinstance(r, dict)]
    if isinstance(resp, dict):
        tr = [t for t in (resp.get("trace") or []) if isinstance(t, dict)]
        head = ([read] if read else []) + reasons
        resp["trace"] = head + tr
```

### 1.3 为什么这么放

`_chat_impl` 有**几十个 return**，在每个 return 上补一句必然漏（`1775-1778` 的注释已经踩过这个坑）。
记在 `meta` 里、出口补一次，覆盖全部。`read` 的 3 个覆盖点（`:1908` / `:1955` / `:2777`）**不用动**——
`reasons` 与 `read` 独立，覆盖 `read` 不影响它。

## 二、B（收写侧）：`result` 不许原样给学生看

### 2.1 现状

`engine.py:216-223` 直接 `"result": str(result)[:600]`。而 `result` 是**工具的返回语料，写给模型读的**：
里面有「已向前端展示…」「确认条」「请照工具说明重新调用」这类指令句。现在 58 把它露给学生了，于是内脏外泄。

**在渲染侧打码是错的方向**：打码表要随工具增长维护（必漏）；`plan.html`/`station.html`/日志导出以后还得各打一遍。
写侧只有一个产出点，能真正把"给学生那份"和"给模型那份"分叉。

### 2.2 修法：新增 `app/agent/public_view.py`

```python
def public_view(tool_name: str, result) -> str:
    """把工具返回值收成**一句给人看的话**（人话：把给模型的语料翻译成人话）。

    优先级：
      ① 工具自己给了 `__user_view__`（dict 里的字符串）→ 直接用（工具作者最懂怎么念）；
      ② 是 JSON：列表 → 「共 N 条：a、b、c」；dict → 先找 summary/message/title/count 这几个键；
      ③ 都不是 → str(result)；
      ④ 最后统一 `_squash()`：**按句/行丢掉内部指令句**、压空白、截断 120 字。
    """
```

`engine.py` 两处跟着改：

```python
                    await self._trace({
                        "step": step, "phase": "📦 工具结果", "action": fn_name,
                        "result": public_view(fn_name, result),   # ← 只放给人看的那份
                    })
```

**raw 不进响应**：原始 `str(result)[:600]` 只在 `DEBUG=true` 时 `print()` 到服务端日志，
不塞 trace —— 塞进去等于把内脏又送到浏览器。

## 三、C（不收起）：Observation 折单行 + 限高

收起等于把要证明的东西藏起来，和"让评委看见"正相反。**改密度，不改可见性。**

`renderTrace`（58 之后那版）里加一个折行函数，并只对工具结果用：

```js
    // 把多行 result 折成一行：换行变分隔点，连续空白压成一个
    function _oneLine(s) {
      return String(s == null ? "" : s)
        .replace(/\s*\n+\s*/g, " · ")
        .replace(/[ \t]{2,}/g, " ")
        .trim();
    }
```

工具结果那一步：`String(t.result).trim()` → `_oneLine(t.result)`（`args` 不用折，它本来就是一行 JSON）。

再把 rows 包进一个可滚动的容器（`max-height` 加在**容器**上，不要加在 `.trace` 上，否则 summary 会跟着滚）：

```js
      return '<details class="trace" open><summary>🧠 我是这么想的</summary>' +
        '<div class="trace-body">' + rows.join("") + "</div></details>";
```

```css
    /* #60：轨迹块限高，内部滚动 —— 不收起，但也不许把页面顶爆 */
    .trace .trace-body { max-height: 240px; overflow-y: auto; }
```

## 四、验证

### 4.1 真机（就用定好的那句）

说「帮我看看周一到周三哪天课最少，然后那天排两小时做题」，轨迹必须长这样：

```
🧭 判定 — 含「周一到周三」+「排两小时」→ 归日程：先查课表再出时段
🧭 判定 — 问的是"哪天课最少"，不是在删改 → 不按办事处理
1  🔧 调用工具 — get_weekly_timetable({...})
1  📦 工具结果 — 查到 4 门课 · 周一 1-2 · 周三 3-4 · ...
2  🔧 调用工具 — find_free_slots({...})
2  📦 工具结果 — 周二 14:00-18:00 空 · ...
3  🔧 调用工具 — propose_slots({...})
3  📦 工具结果 — 3 个候选时段
（末尾一张确认卡）
```

验收四条：① 前三行是 `🧭 判定`；② Observation **每步只有一行**，且不含「确认条 / 前端 / 请照…」；
③ 轨迹块高度 ≤ 240px（超出内部滚动）；④ 确认卡仍在末尾。

### 4.2 变异（改坏必须红）

- 不拼 `meta["reasons"]` → 前三行消失 → 应红；
- `public_view` 直接 `return str(result)` → 内部词重现 → 应红；
- `_oneLine` 不折换行 → 轨迹块超过 240px → 应红；
- 只加 `max-height` 不加 `overflow-y` → 内容被裁掉、看不见 → 应红。

### 4.3 回归

58 的两条不能回退：`🔧`/`📦` 必须仍然可见；`💡 最终回答` 必须仍被去重（不重复气泡正文）。

## 五、更正 58 的演示句（58 已提交，按规矩不改快照，以本节为准）

| 58 里写的 | 实测 |
|---|---|
| planner 首选「帮我看看这周哪些下午有空，找两小时复习」 | ❌ 被 **KB 直答**接走，没进引擎 |
| express「我有哪些快递没取，取件码是多少」 | ❌ 模型一次 `list_pending_pickup` 就够，跨不到 ≥2 轮 |

**改用的定稿句**：「帮我看看周一到周三哪天课最少，然后那天排两小时做题」
（10 行 / 3 工具 / 跨 3 轮 / 末尾 1 张确认卡）。

**教训写死**：演示句**必须实测数 `step`**，不能从工具清单"推导"它会是多步——工具多不等于这次会调多个。

## 六、不做的事

1. **不上 `deepseek-reasoner`**：`gateway.py` 不解析 `reasoning_content`，且 CoT 不可复现（评委追问"为什么这次这样"答不了）；
2. **不编 CoT**：`🧭 判定` 只写判定层真算出来的东西；
3. **不在前端打码**：内部词收在写侧（工具产出那一层）；
4. **不默认收起轨迹**：收起 = 藏证据；
5. **不改引擎循环 / `max_steps`**；
6. **不改已提交的 58 快照**：候选句的更正写在本单第五节。

## 七、这轮踩到的三个坑（写下来，别再踩）

| 坑 | 现象 | 判据/规矩 |
|---|---|---|
| `render((case,),)` 多套一层括号 | 三条"期望空串"的断言一起假绿 | **"期望为空"必须配一条"非空"正控**——否则函数返回 `None` 也绿 |
| 变异 V3 放在过滤条件**下游** | `rc=0` 的哑弹（变异根本没执行） | 变异必须打在**判据上游**；自查："把被测代码整段删掉，这条测试还绿吗？" |
| 探针复用固定 `sid` | "0 工具却答对取件码"被编出一个错误机制 | **每次探针新 sid**；历史/暂存会替你答题 |

## 八、给执行方的三句

1. 三处改动都很小，但**别顺手改引擎循环、别动 58 的 `renderTrace` 主体**（只加 `_oneLine` 与 `trace-body`）。
2. `reasons` 与 `read` **互相独立**：`read` 那 3 个覆盖点一个都别碰。
3. 验收看那四条：`🧭` 三行、Observation 单行无内脏词、块高 ≤240px、确认卡还在。
