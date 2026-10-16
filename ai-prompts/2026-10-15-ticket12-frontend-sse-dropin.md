# 12 · 前端 SSE 接入（让学生端真正"看见"流式）

> 给落地 AI（Qoder）的 drop-in 施工单。**只读这文件，不要改其它 prompt。**
> 这是 **07 文档前端部分的精确落地版**——07 的后端（SSE 出口 / 共享 client / Accept 协商）**已经部署**，
> 但 `student.html` 两处 `/api/chat` 的 fetch **从没改成 SSE**（仍 `.json()`、没发 `Accept` 头、没 `getReader`），
> 所以 `_wants_sse`（main.py:1191）一直返回 False、走 JSON 老路。**这就是"实测没感觉到流式"的根因。**
> 本刀**只动前端**，后端一字不改（已就绪）。

---

## 一、目标（一句话）
让 `student.html` 两处 `/api/chat` 请求**主动声明 `Accept: text/event-stream`** 并用 `getReader` 逐块读，
把后端已经能吐的 token 一个个显进气泡；收到 `done` 再用守卫后的权威答案定稿、渲染确认卡。

---

## 二、后端 SSE 契约（已部署，前端照此解析，别改后端）
- 触发条件：`_wants_sse(req)` 只认请求头 `Accept` 含 `text/event-stream`（main.py:1191-1199）。无此头 → 仍回老 JSON（plan/station 前端 + 227 处测试不动）。
- 事件格式：标准 SSE，**每行一个 `data:`，空行分隔**。前端只认 `k` 字段：
  - `{"k":"token","v":"片段"}` —— 引擎原始回答的增量片段（预览）。
  - `{"k":"trace","v":{...}}` —— 可选思考步骤；**权威轨迹已在 `done.v.trace` 里**，过程中可忽略。
  - `{"k":"done","v":{answer, options, awaiting_choice, trace, module, session_id}}` —— **守卫修正后的权威答案，与老 JSON 的 `data` 完全同构**。
  - `{"k":"error","v":{"answer":"友好报错"}}` —— 流式中途失败，前端据此整句替换，不留半截。
- 时序保证（main.py:1226-1265）：后台把整条 `_chat_impl`（含 :3478-3680 那族 claim 守卫）跑完，
  返回后才发 `done`。**`done.v.answer` 已是守卫后版本**，前端必须用 `done.v.answer` 覆盖流式预览的 token 文本。

---

## 三、改动范围（只动 `app/static/student.html`）

| 落点（已核对当前代码） | 改什么 | 性质 |
|---|---|---|
| 新增 `streamChat(body, handlers)` 辅助函数（插在 `showThinking` 定义之后、`sendMsg` 之前，约 :1778 后） | 一个 fetch+getReader+SSE 解析的复用器，三处回调 `onToken/onDone/onError` | 新增 |
| `sendMsg`（:1801-1818 那一段 `.json()` 块） | 改 SSE：先占位空气泡 → token 增量塞气泡 → `done` 重建气泡+卡片 | 改 |
| 上传课表自 fetch 路径（:2129-2135 那一段 `.json()` 块） | 改 SSE：只更新状态栏文本（与改前行为一致，不渲染气泡） | 改 |

**不动**：后端 `main.py` / `client.py` / `engine.py`（07 已部署）；`plan.html` / `station.html`（仍走 JSON）；
`/classify` 端点（student.html:2353，只读判定、不进聊天）；任何 `.json()` 测试。

---

## 四、各落点改法（可直接粘贴）

### 4.1 新增 `streamChat`（贴在 `function showThinking(show){...}` 之后）

```js
// ---- SSE 流式读取：把 /api/chat 的 token 逐块推进回调 ----
// 后端已部署协商式 SSE（main.py:1191 _wants_sse）：请求带 Accept: text/event-stream 才流式，
// 否则仍回老 JSON。本函数只负责"读流 + 分发"，不改任何后端逻辑。
// 事件协议见 prompt 07 §5：token / trace / done / error。
async function streamChat(body, handlers) {
  // handlers: { onToken(v), onDone(v), onError(v) }
  let resp;
  try {
    resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(body),
    });
  } catch (e) {
    handlers.onError && handlers.onError({ answer: "哎呀，网络好像开小差了，请稍后再试～" });
    return;
  }
  // 非 200（框架级 422/500，或请求体非法被后端以 JSON 400 回）：按老路径拿 error
  if (!resp.ok) {
    try {
      const j = await resp.json();
      if (j && j.error) {
        handlers.onError && handlers.onError({ answer: "哎呀，管家这边遇到了点小状况，请稍后再试或换个说法～" });
        return;
      }
    } catch (e) {}
    handlers.onError && handlers.onError({ answer: "哎呀，管家这边遇到了点小状况，请稍后再试或换个说法～" });
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let gotDone = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE 以空行（\n\n）分隔事件；一个 block 内可能有多行
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        block.split("\n").forEach(function (line) {
          const m = line.match(/^data:\s?(.*)$/);
          if (!m) return;
          let ev;
          try { ev = JSON.parse(m[1]); } catch (e) { return; }
          if (!ev || !ev.k) return;
          if (ev.k === "token") { handlers.onToken && handlers.onToken(ev.v); }
          else if (ev.k === "done") { gotDone = true; handlers.onDone && handlers.onDone(ev.v); }
          else if (ev.k === "error") { handlers.onError && handlers.onError(ev.v); }
          // trace 事件：done.v.trace 已带权威轨迹，过程中忽略即可
        });
      }
    }
    buf += decoder.decode(); // 冲刷末尾残留字节
  } catch (e) {
    if (!gotDone) handlers.onError && handlers.onError({ answer: "哎呀，网络好像开小差了，请稍后再试～" });
  }
}
```

### 4.2 重写 `sendMsg` 的取数块（替换 :1801-1818）

> 删除原来从 `const resp = await fetch("/api/chat", {` 到 `}`（含 `if (data.error){...} else {...}` 整段），
> 替换为下面这段。注意：**先建一条空 bot 占位气泡**，token 直接往里塞；`showThinking(true)` 在 :1785 已设置，
> 在 `onDone`/`onError`/收尾处关掉。

```js
        // 先占位一条空 bot 气泡，流式 token 直接往里塞（首字即显，体感从"卡死"变"在想"）
        const el = addMsg(botName(), "", "bot", null, null);
        const bubble = el.querySelector(".bubble");
        const scrollLog = function () {
          log.scrollTop = log.scrollHeight;
          const stage = log.closest(".stage");
          if (stage) stage.scrollTop = stage.scrollHeight;
        };
        await streamChat(body, {
          onToken: function (v) { if (v) { bubble.textContent += v; scrollLog(); } },
          onError: function (v) {
            showThinking(false);
            bubble.innerHTML = richText((v && v.answer) || "哎呀，管家这边遇到了点小状况，请稍后再试或换个说法～");
          },
          onDone: function (d) {
            showThinking(false);
            // done.v 是守卫修正后的权威答案（与老 JSON 的 data 同构）；用 richText 重建气泡，
            // 完全镜像 addMsg(student.html:1011)：module 标签 + richText 正文 + 默认展开的 trace
            const whoHtml = '<div class="who">' + esc(botName()) +
              (d.module ? '<span class="tag">' + esc(d.module) + "</span>" : "") + "</div>";
            const bodyHtml = '<div class="bubble">' + richText(d.answer || "") + "</div>";
            const traceHtml = (d.trace && d.trace.length && traceOn()) ? renderTrace(d.trace, d.answer) : "";
            el.innerHTML = whoHtml + bodyHtml + traceHtml;
            scrollLog();
            // 规划模块回了候选 → 渲染成可勾选卡片（学生点卡片才真正写入日程）
            if (d.options && d.options.length) renderOptions(el, d.options);
            // 课表可能在刚才这一轮里被改了（学生直接回"确认"），正在看的日程面板要刷成最新
            const sched = document.getElementById("view-schedule");
            if (sched && sched.style.display !== "none") loadScheduleView();
          },
        });
        showThinking(false); // 收尾安全网：无论走哪条分支，思考指示器都关掉
```

### 4.3 重写上传课表自 fetch 路径（替换 :2129-2135）

> 与改前**行为严格一致**：只更新状态栏 `st.textContent`，不渲染 bot 气泡（提案卡由 confirm iframe 轮询 `/api/pending` 呈现，本路径不动它）。

```js
        let settled = false;
        await streamChat(body, {
          onToken: function () {},  // 上传课表这条只更新状态栏，不渲染气泡（与改前一致）
          onError: function () { st.textContent = "读取课表时出了点小状况，请重试或换个说法～"; },
          onDone: function (d) {
            settled = true;
            st.textContent = (d.options || []).length
              ? "管家已给出课表提案，请在下方确认条上点【确认修改】"
              : (d.answer || "已请求管家读取课表");
          },
        });
        if (!settled) st.textContent = "已请求管家读取课表";
```

---

## 五、护栏清单（改的时候一条都不能破）
1. **后端零改动**：`main.py` / `client.py` / `engine.py` 的 SSE 已就绪，本刀只动 `student.html`。
2. **`done` 覆盖优先**：`onDone` 必须用 `done.v.answer` 重建气泡，**覆盖**流式预览 token（`_no_card_answer()` 等守卫改写时，预览是引擎原话、done 才是权威版）。
3. **两族守卫不破**：守卫在后端 `:3478-3680` 已跑完才发 `done`；前端只是消费，不重判。
4. **确认卡不丢**：`renderOptions(el, d.options)` 必须在 `onDone` 里调，且挂在 `el`（消息元素）上，与改前一致。
5. **降级不白屏**：`onError` 把气泡整句换成一句人话；网络断 / 流中途断（`gotDone` 为假）也走 `onError`。
6. **module 标签 + trace 不丢**：`onDone` 重建 HTML 时，必须带 `d.module` 标签与 `d.trace`（默认展开），与 `addMsg` 输出逐字等价，否则"学生核对听错没"的凭据消失。
7. **历史不重复写**：本前端不写会话历史（后端 `/api/conversation` 恢复，见 student.html:2414-2430）；token 预览阶段不写、done 阶段也不写——和改前一致，无需额外处理。

---

## 六、纪律（给实现 AI 的红线）
- **整族扫**：全仓搜 `/api/chat` 的前后端调用方，确认只有 :1801 与 :2129 两处学生端 fetch 需要改；
  `plan.html:499` / `station.html:201` / 227 处 `.json()` 测试**一律不动**（它们不发 Accept 头，仍走 JSON，行为不变）。
- **不引入新通道**：复用现有 `_wants_sse` 协商，别把 `/api/chat` 改成默认 SSE（会弄红那三族消费者）。
- **变异测试**：构造四种输入验证前端不崩——① 正常加待办（字渐进 + 确认卡照弹）；② 纯问答（只有渐进文本、无卡）；③ 断网 / 错 key（`onError` 出人话、无白屏无半句残留）；④ 守卫改写答案（如"刚才那句"回声触发 `_no_card_answer`），确认 `done.answer` 是修正版、且覆盖了预览 token。
- **真机复验**：浏览器发一句加待办，看字是否一个个冒出来（首字 < 4s，之前整链路 ≈10s）；确认卡点【确认】仍能写库；`llm_usage.jsonl` 仍有记录。
- **回滚**：最小回滚 = 把 4.2 / 4.3 两处 fetch 改回 `await resp.json()`（去掉 Accept 头与 getReader），后端 SSE 分支因无请求触发而自然闲置，无需回退后端。

---

## 七、验证清单（交付前自测）
- [ ] 浏览器发一句"帮我加个明早 8 点跑步的待办"：字**逐个**出现，约 1~3s 出首字（之前要等 ≈10s 整段弹）。
- [ ] 确认卡（`options`）在回答后正常弹出，点【确认】仍能写库；`done.answer` 与守卫修正一致。
- [ ] 纯问答（如"图书馆几点关门"）：只有渐进文本、无确认卡。
- [ ] 断网 / 填错 key：前端显示友好报错，无白屏、无半句残留。
- [ ] 刷新页面：历史仍在（后端恢复），且本轮流式回答也在。
- [ ] `plan.html` / `station.html` / 227 处 `.json()` 测试：全绿（它们没发 Accept 头，走 JSON 老路，不受影响）。
- [ ] 守卫验证：构造让 claim 守卫触发的输入，确认 `done.answer` 是修正版而非引擎原话。
- [ ] 后端 `llm_usage.jsonl` 流式调用仍记了 latency/usage（含 `user="内部:语义判定"`）。
