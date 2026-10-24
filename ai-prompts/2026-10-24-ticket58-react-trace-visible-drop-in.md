# 58｜评委"体现不出 ReAct"：前端把 Action/Observation 整条过滤了 + 演示句只有一步（drop-in）

> 现场：2026-10-24，评委反馈「体现不出 ReAct」。实测结论：**引擎是标准 ReAct，跑得好好的；是前端渲染把它一条不剩地扔了。**
> 这不是 UI 审美问题，是**功能可见性 bug**——轨迹为空，评委唯一能判断"有没有 ReAct"的证据就没了。
> 活跃版 `D:/CampusTime`，含 #56、57。

## 〇、现场事实（全部实测，不是推演）

### 0.1 引擎侧：记的是完整的三段式

| 位置 | 记了什么 |
|---|---|
| `app/agent/engine.py:126-127` | `max_steps = 6` + `for step in range(1, max_steps + 1)`：真的循环 |
| `:128` | `{"step": n, "phase": "🤔 思考"}` —— **没有内容字段**（chat 模型不吐 CoT） |
| `:145-147` | `{"step": n, "phase": "🔧 调用工具", "action": fn_name, "args": args}` |
| `:216-223` | `{"step": n, "phase": "📦 工具结果", "action": fn_name, "result": str(result)[:600]}` |
| `:229` | `{"step": n, "phase": "💡 最终回答", "answer": answer}` |

→ Action / Observation 的数据**齐全**（工具名、参数、返回值都在）。

### 0.2 前端侧：判据只认 `answer`

`app/static/student.html:1048-1058`：

```js
const _BARE_TRACE_PHASES = ["思考", "调用工具", "工具结果"];
...
const hasAns = t.answer != null && String(t.answer).trim() !== "";
if (!hasAns && _BARE_TRACE_PHASES.indexOf(label) >= 0) return;   // ①
...
if (an && body && an === body) return;                            // ②
```

逐条对下来：

| phase | 有什么 | 判据结果 | 实际 |
|---|---|---|---|
| `🤔 思考` | 只有 phase | `!hasAns` 且在表里 | **丢弃**（这个确实没内容，合理） |
| `🔧 调用工具` | `action` + `args` | `!hasAns` 且在表里 | **丢弃**（有内容，判据没看） |
| `📦 工具结果` | `action` + `result` | `!hasAns` 且在表里 | **丢弃**（有内容，判据没看） |
| `💡 最终回答` | `answer` | `an === body` | **丢弃**（去重，合理） |

→ `rows` 全空 → `return ""`（`:1067`）→ **「🧠 我是这么想的」整块根本不出现**。

`1042` 行那句注释「ReAct 的空流程标记没有内容」**只对"思考"成立**，套到后两步是错的——它们的内容在 `action`/`result`，不叫 `answer`。

### 0.3 两条出口都会经过同一个函数

- JSON 出口：`onDone` → `addMsg(botName(), d.answer, "bot", d.module, d.trace)`（`:2125`）
- SSE 出口：协议注释写「`{"k":"trace",...}`（权威轨迹在 done 里，过程忽略）」（`:2028`），最后同样走 `:2125`
- `addMsg` 里唯一调渲染的地方是 `:1090` → `renderTrace(trace, content)`

→ **只改 `renderTrace` 一个函数，两条路都修好**，不需要碰后端。

### 0.4 为什么"确定性分支"看起来反而正常（也正因为这个才误导人）

`main.py` 里有几十处合成单步轨迹，例如 `:1079` / `:1290` / `:1920`：

```python
"trace": [{"step": 1, "phase": "✅ 学生确认", "answer": card.get("summary", "")}]
```

这些**带 `answer`** ⇒ 能渲染。但它只有一步，评委看到的是「系统做了个操作」，不是「Agent 想→做→看」。

> 一句话总结这个 bug 的讽刺之处：**越依赖引擎，ReAct 越隐形。** 引擎那条路被过滤光了，只剩确定性快路能露脸。

## 一、A（主修）：`renderTrace` 必须认 `action` / `result`

### 1.1 改哪一处

只改 `app/static/student.html` 的 `renderTrace()`（`:1049-1070`）。**后端、引擎、接口一律不动。**

### 1.2 替换代码（整段）

```js
    const _BARE_TRACE_PHASES = ["思考", "调用工具", "工具结果"];
    function renderTrace(trace, content) {
      const body = String(content == null ? "" : content).trim();
      const rows = [];
      trace.forEach(function (t) {
        if (!t || !t.phase) return;
        const label = String(t.phase).replace(/[^\u4e00-\u9fa5A-Za-z0-9]/g, "");
        const hasAns = t.answer != null && String(t.answer).trim() !== "";
        // ①-b（#58）：工具那两步的内容在 action / args / result 里，**不在 answer 里**。
        // 旧判据只认 answer，于是「调用工具 / 工具结果」永远被当成"空标记"扔掉——
        // 一条完整的 Reason→Act→Observe 循环就是这么整块消失的。
        const hasAction = t.action != null && String(t.action).trim() !== "";
        const hasResult = t.result != null && String(t.result).trim() !== "";
        const hasTool = hasAction || hasResult;
        if (!hasAns && !hasTool && _BARE_TRACE_PHASES.indexOf(label) >= 0) return;  // ①
        let an = "";
        if (hasAns) {
          an = String(t.answer).trim();
        } else if (hasTool) {
          // 工具这两步自己排：工具名(参数) → 结果
          an = hasAction ? String(t.action) : "";
          if (t.args && typeof t.args === "object") {
            let a = "";
            try { a = JSON.stringify(t.args); } catch (e) { a = ""; }
            if (a && a !== "{}") an += "(" + a + ")";
          }
          if (hasResult) an += (an ? " → " : "") + String(t.result).trim();
        }
        if (an && body && an === body) return;                            // ②
        const cap = hasAns ? 80 : 120;                                    // ③ 工具结果给宽一点
        if (an.length > cap) an = an.slice(0, cap) + "…";
        const st = t.step ? '<span class="st">' + esc(String(t.step)) + "</span>" : "";
        rows.push('<div class="trace-row">' + st +
          richText(String(t.phase)) + (an ? " — " + richText(an) : "") +
          "</div>");
      });
      if (!rows.length) return "";
      return '<details class="trace" open><summary>🧠 我是这么想的</summary>' +
        rows.join("") + "</details>";
    }
```

### 1.3 边界（三条，都不许顺手改）

1. **「🤔 思考」仍然不画**：它真的没有内容，画出来就是三个看不懂的词。不要为了"看起来有推理"去编一句。
2. **`answer` 的 80 字截断保持**：可能有测试钉着；工具那两步放宽到 120，是新增的。
3. **`action` / `result` 必须走 `richText` 转义**：`result` 里可能带学生原话或文档原文，直接塞 `innerHTML` 就是 XSS（与 `1039` 行同一条纪律）。

## 二、B（演示）：演示句必须触发 ≥2 次工具调用

### 2.1 现状

修完 A 之后，只调一个工具的问题仍然只显示「一次调用」，循环感不够。而且**大多数句子会被确定性快路截胡**（`main.py` 那几十处单步 `trace`），根本不进引擎。

### 2.2 选句方向（工具名已核实）

**首选—planner 三连**（`planner.build_tools()` `:5824` 起）：

`get_weekly_timetable`（`:5827`）→ `find_free_slots`（`:5833`）→ `propose_slots`（`:5852`）

候选句：「**帮我看看这周哪些下午有空，找两小时复习**」

**备选—express 两连**（`express.py:183/189/195`）：

`list_my_packages` → `get_by_code`

候选句：「**我有哪些快递没取，取件码是多少**」

### 2.3 验收口径（硬指标，不看截图好不好看）

在 `/api/chat` 的返回里数 `trace`：

- `action` 至少出现 **2 个不同工具名**，或带 `action` 的记录 **≥3 条**；
- 且这些 `step` 跨 **≥2 轮**（`step` 出现两个以上不同值）。

达不到就换句，别改代码去凑。

### 2.4 可选（不在本单必做）

加一个「深度模式」开关：请求体带 `deep: true` 时**跳过确定性快路、强制走引擎**。好处是演示可控；代价是要动路由分支，风险明显高于 A。**除非 2.2 怎么选句都被截胡，否则不做。**

## 三、验证

### 3.1 变异（改坏必须红）

- 把 `hasTool` 从 `if` 里去掉 → 轨迹恢复成空 → 应红；
- `args`/`result` 不经过 `richText` → 注入 `<img onerror>` 应红；
- 把 `🤔 思考` 硬画成有内容 → 出现"编造推理" → 应红（这条是纪律，不是功能）。

### 3.2 真机

1. 启动服务，打开 `/student`；
2. 发 2.2 的候选句；
3. 要求看到：`🔧 调用工具 — get_weekly_timetable(...)` → `📦 工具结果 — ...` → `🔧 调用工具 — find_free_slots(...)` → … → 气泡答案；
4. 若界面仍无轨迹，先看 `localStorage.campustime_trace` 是不是被关了（`traceOn()` 默认开）。

### 3.3 回归

`renderTrace` 只被 `:1090` 调用，改动面收敛；跑一遍全量测试，重点盯任何断言"轨迹里不许出现某串"的用例。

## 四、不做的事（写下来免得下一位以为是漏）

1. **不换 `deepseek-reasoner`**：`gateway.py` 目前不解析 `reasoning_content`，换模型要连着改网关，收益不抵风险；
2. **不改引擎循环 / `max_steps`**：ReAct 本身是对的，动它只会引入新问题；
3. **不给「思考」编内容**：没有 CoT 就说没有，Action+Observation 已经是可验证的 ReAct 证据；
4. **不动确定性分支**：它们的行为是对的（不幻觉、可复现），本单只解决"看不见"。

## 五、给执行方的三句

1. 只改 `renderTrace` 一个函数，**不要碰 `engine.py` 和后端接口**。
2. `action` / `result` 一律经 `richText`，不许直接拼 HTML。
3. 验收看 `trace` 里 `action` 的个数和 `step` 的跨度，不看截图观感。

## 六、顺带给答辩用的一句话

> 「我们的 ReAct 循环在 `engine.py`，最多 6 轮 Reason→Act→Observe。界面上每一条 `🔧 调用工具 / 📦 工具结果` 就是一轮的证据——它调了什么工具、传了什么参数、拿回什么，全部可复现。我们没有用 LangChain，循环只有几十行，就是让评委能直接读。」
