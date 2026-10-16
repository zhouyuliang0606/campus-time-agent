# 25｜驿站端改三栏布局 + 加【清空】按钮（drop-in 施工单）

> 交付物性质：**给实现 AI（Qoder）的落地施工单**，只改一个文件 `app/static/station.html`。
> 我不改 CampusTime 代码，本文所有代码块都是可直接粘贴的成品。
> 用户原话锚点：「**没有清除按钮，堆积起来不好看，布局做成和学生端类似的，左边按钮打开，不要现在上下的不停翻**」（附 3 张驿站端页面截图）

---

## 0. 一句话需求

驿站端 `/station` 现在是**一条竖着往下堆**的页面（人格卡 → 待处理问题 → 服务与登记 → 快捷操作 → 对话流 → 输入框），
聊一会儿就得上下翻；而且**聊出来的内容没有清空入口**，越堆越长。
要改成 **student.html 那种三栏外壳**：左边一排按钮，点哪个看哪块，正文只在中间那块换；对话区自己滚，输入框钉在底下；再补一颗【清空】。

---

## 1. 现状实核（改之前先对齐事实，别凭印象改）

文件 `app/static/station.html`，共 **554 行**，单文件内联 `<style>` + `<script>`，无外部 CSS 依赖（只引了 `/static/esc.js`）。

| 位置 | 现状 | 行号 |
|---|---|---|
| `header` 整条渐变头 | 标题 + 「← 返回登录」（float:right）+ 一行副标题 | 13-18 / 124-127 |
| `.wrap` 容器 | `max-width:900px; margin:18px auto` —— 全部内容都塞在这一个容器里 | 19 / 129 |
| 人格卡 `#personaBox` + `#refreshPersona` | 第一块，占满整宽 | 131-138 |
| 待处理问题 `.wo`：`#woList` `#woCount` `#refreshWo` | 第二块 | 141-147 |
| 服务与登记 `.wo`：`#svcIdea` `#svcGo` `#svcList` `#svcCount` `#refreshSvc` | 第三块 | 150-160 |
| 提示行 `.hint` + 快捷操作 `#chips` | 第四、五块 | 162-163 |
| 对话流 `#log`（自带开场白那条 `.msg.bot`） | 第六块，`min-height:300px; max-height:440px; overflow-y:auto` | 50-54 / 165-170 |
| 输入区 `.inputbar`：📎 `#attach` + `#text` + `#send` | 第七块，**只有 📎，没有清空** | 70-85 / 172-177 |
| 三条 5 秒轮询 | `setInterval(loadWorkorders, 5000)`（304）、`setInterval(svcTick, 5000)`（469） | — |

**关键事实（决定怎么改）：**

1. **对话历史只活在 DOM 里。** `sendMsg()`（492-512）请求体是 `{ message, module:"station" }`，**从不带 `session_id`**；而 `main.py:1366` 是
   `session_id = (body.get("session_id") or "").strip() or uuid.uuid4().hex[:12]`
   —— 也就是**每一轮都是全新会话**。前端也没有任何 restoreHistory 逻辑。
   ⇒ 所以「清空对话」在这个文件里是**纯前端动作**（清 `#log`），不需要、也**不应该**去调 `/api/chat/reset`（拿一个空/新 session 去清，什么也清不掉，只会让后人误以为清过了）。**这一点必须在代码注释里写明**，否则下一个 AI 会自作聪明补一次 reset 调用。
2. 学生端 `student.html` 的三栏外壳是 `.shell / .sidebar / .main / .topbar / .stage / .composer` + `switchNav(view)`（2646-2688 那段 IIFE），`.nav-view` 只靠 `display:none/block` 切换 —— **DOM 全程不重建**。这是本单要抄的结构，但**不抄它的 `.panel-mode` / `#panel` / 异步 CSS 玻璃层**（驿站端没有那些东西，引进来就是一坨死代码）。
3. 学生端主色是蓝 `#4f7cff`，**驿站端主色是青绿 `#17b8a6`**。侧栏高亮必须走青绿，**别把学生端那套蓝色抄过来**（抄错色是这类改造最常见的"整族不一致"）。

---

## 2. 目标形态

```
┌────────────┬──────────────────────────────────────────────┐
│ 🏪 驿站/商户端│  对话                          [← 返回登录]  │  ← topbar
│            ├──────────────────────────────────────────────┤
│ 💬 对话  ◀ │  （点左栏换这一块，只有中间在换）              │
│ 🛠️ 待处理 3│  ……#log 自己滚………                         │
│ 🧩 服务与登记│                                              │
│ 🎭 客服人格 │                                              │  ← stage（flex:1, 自己滚）
│            ├──────────────────────────────────────────────┤
│            │  📎  🗑清空  [ 输入框 ]              [发送]     │  ← composer 钉底
│ 校园时间管家·驿站端│                                        │
└────────────┴──────────────────────────────────────────────┘
```

- **左栏只有 4 颗按钮**（对话 / 待处理问题 / 服务与登记 / 客服人格），点开就换中间那块，**正文只在中间滚**。
- **「待处理问题」按钮上带未结角标**：学生上报的新问题在别的视图下也能一眼看见 —— 这是把"待处理"从首屏挪进左栏之后**必须补的补偿机制**。
- 默认停在 **💬 对话**（驿站的主日常是答疑；上报靠角标兜底）。
- 「客服人格」从首屏大卡片降成一颗按钮，内容原样搬过去。
- 底部输入区**只在「对话」视图出现**（其他三块纯查看，出现输入框只会让人误以为那块能聊天）。

---

## 3. 单子 S1（必做）：三栏外壳 + 清空按钮

### 3.1 删掉什么（整族扫，别留孤儿）

- `<header>…</header>` 整块（124-127）→ 标题与副标题分别搬到左栏品牌区 / 对话视图的 `.hint`；「← 返回登录」搬到 `.topbar`。
- `.wrap` 容器（129 / 178 的收尾 `</div>`）→ 换成 `.shell`；`.wrap` 这条 CSS（19）一并删。
- CSS 里 `header {…}`、`header h1`、`header p` 三条（13-18）→ **必须一起删**（孤儿 CSS 是"改一半"的典型现场）。
- `.persona`、`.wo`、`.svc`、`.chip`、`.msg`、`.trace`、`.tag`、`.attach` 这些**保留原样不动**，只是换容器。

### 3.2 新增 CSS（粘到 `<style>` 末尾，青绿配色）

```css
/* ============ 三栏外壳（对齐 student.html 的结构与命名，配色走驿站青绿） ============ */
.shell { display: flex; height: 100vh; overflow: hidden; }
.sidebar {
  width: 224px; flex: none; background: #fff; border-right: 1px solid #eef0f6;
  display: flex; flex-direction: column; padding: 12px; gap: 4px;
  height: 100vh; overflow-y: auto;
}
.sb-brand {
  display: flex; align-items: center; gap: 10px; padding: 10px;
  border-radius: 12px; background: linear-gradient(135deg, #17b8a6, #2f9e6f);
  color: #fff; margin-bottom: 10px;
}
.sb-brand .t { font-size: 14px; font-weight: 600; }
.sb-brand .s { font-size: 11px; opacity: .9; }
.sb-nav { display: flex; flex-direction: column; gap: 2px; }
.sb-item {
  display: flex; align-items: center; gap: 10px; padding: 11px 12px;
  border-radius: 10px; cursor: pointer; font-size: 14px; color: #4a5366;
  user-select: none; border: 1px solid transparent;
}
.sb-item:hover { background: #f4f6fb; }
.sb-item.active { background: #e8f8f6; color: #0e8f80; font-weight: 600; border-color: #bfe9e3; }
.sb-item .ico { width: 18px; text-align: center; font-size: 16px; }
/* 未结角标：走站内「待处理」同一个红（#fff1f0/#cf1322），别新造一套颜色 */
.sb-num {
  display: none; margin-left: auto; font-size: 11px; line-height: 16px;
  background: #fff1f0; color: #cf1322; border-radius: 9px; padding: 0 7px;
}
.sb-num.show { display: inline-block; }
.sb-foot { margin-top: auto; font-size: 11px; color: #aab1c2; padding-left: 6px; }

.main { flex: 1; display: flex; flex-direction: column; min-width: 0; height: 100vh; }
.topbar {
  display: flex; align-items: center; gap: 10px; padding: 10px 16px;
  border-bottom: 1px solid #eef0f6; background: #fff;
}
.topbar .t { font-size: 15px; font-weight: 600; color: #1f2733; }
.topbar .grow { flex: 1; }
.tb-link { font-size: 13px; color: #0e8f80; text-decoration: none; background: #e8f8f6; padding: 5px 12px; border-radius: 8px; }
.tb-link:hover { background: #d9f3ef; }
.stage { flex: 1; overflow-y: auto; padding: 16px; }

/* 视图切换：只靠 display 切，DOM 全程不重建 */
.nav-view { display: none; }
.nav-view.active { display: block; }

/* 对话区：外壳自带滚动，所以 #log 自己不再限高（原来 max-height:440px 要撤掉） */
#log { min-height: 240px; max-height: none; }
.inputbar { margin-top: 0; }
.composer { border-top: 1px solid #eef0f6; background: #fff; padding: 10px 16px 14px; }
body.no-chatbox .composer { display: none !important; }

/* 清空按钮：跟 📎 同尺寸同气质，不抢【发送】的注意力 */
.clear {
  padding: 0 12px; border: 1px solid #d8def0; background: #fff; color: #8a6d3b;
  border-radius: 10px; cursor: pointer; font-size: 12.5px; white-space: nowrap;
}
.clear:hover { background: #fff7ec; border-color: #f3d9b0; }

/* 确认框（自绘）：不用原生 confirm()——演示时连点几次会被浏览器拦「阻止此页面创建更多对话框」 */
.mask { display: none; position: fixed; inset: 0; background: rgba(20,26,45,.45); z-index: 200; align-items: center; justify-content: center; }
.mask.show { display: flex; }
.dlg { background: #fff; border-radius: 14px; width: min(420px, 92vw); padding: 18px; box-shadow: 0 20px 60px rgba(20,26,45,.28); }
.dlg-head { font-size: 15px; font-weight: 600; color: #1f2733; margin-bottom: 8px; }
.dlg-body { font-size: 13.5px; color: #4a5366; line-height: 1.7; }
.dlg-warn { background: #fff7e6; border: 1px dashed #ffd591; border-radius: 9px; padding: 9px 11px; font-size: 12.5px; color: #8a6d3b; margin-top: 8px; }
.dlg-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.dlg-btn { border: none; border-radius: 9px; padding: 9px 16px; font-size: 13px; cursor: pointer; }
.dlg-btn.ghost { background: #f0f3fa; color: #4a5366; }
.dlg-btn.danger { background: #e24b4a; color: #fff; }

/* 窄屏（笔记本演示也可能被窗口挤）：侧栏收成 64px 只留图标，抄 student.html 的做法 */
@media (max-width: 720px) {
  .sidebar { width: 64px; padding: 10px 8px; }
  .sb-brand .txt, .sb-item span:not(.ico), .sb-foot { display: none; }
  .sb-item { justify-content: center; position: relative; }
  .sb-num.show { position: absolute; top: 3px; right: 2px; margin-left: 0; }
}
```

> 图标用 **emoji**（💬 🛠️ 🧩 🎭），不引 student.html 那 20 个 `<symbol>` SVG —— 驿站端全文风格本来就是 emoji（📦🔔⚠️🔍🎭🛠️🧩），引一套 SVG 精灵是给这个页面凭空加 20 行死代码。

### 3.3 新 HTML 骨架（替换 `<body>` 里 `header` + `.wrap` 那整段）

```html
<div class="shell">
  <aside class="sidebar" id="sidebar">
    <div class="sb-brand">
      <span style="font-size:18px;">🏪</span>
      <div class="txt">
        <div class="t">驿站/商户端</div>
        <div class="s">校园时间管家</div>
      </div>
    </div>
    <nav class="sb-nav" id="sbNav">
      <div class="sb-item active" data-nav="chat"><span class="ico">💬</span>对话</div>
      <div class="sb-item" data-nav="wo"><span class="ico">🛠️</span>待处理问题<span class="sb-num" id="woNum"></span></div>
      <div class="sb-item" data-nav="svc"><span class="ico">🧩</span>服务与登记</div>
      <div class="sb-item" data-nav="persona"><span class="ico">🎭</span>客服人格</div>
    </nav>
    <div class="sb-foot">校园时间管家 · 驿站端</div>
  </aside>

  <main class="main">
    <div class="topbar">
      <div class="t" id="navTitle">对话</div>
      <span class="grow"></span>
      <a class="tb-link" href="/">← 返回登录</a>
    </div>

    <div class="stage">
      <!-- ① 对话：快捷操作 + 对话流 -->
      <div id="view-chat" class="nav-view active">
        <div class="hint">用自定义人格回复咨询 · 主动推送取件提醒与滞留预警。点下面的快捷操作让客服干活，或直接提问。</div>
        <div class="chips" id="chips"></div>
        <div id="log">
          <div class="msg bot">
            <div class="who">客服</div>
            <div class="bubble">你好，我是这里的 AI 客服 👋 我可以查台账帮你答复同学，也能主动给还没取件的同学发提醒。</div>
          </div>
        </div>
      </div>

      <!-- ② 待处理问题（#18 单子 2）：原样搬进来，只换容器 -->
      <div id="view-wo" class="nav-view">
        <div class="wo">
          <div class="woHead">🛠️ 待处理问题
            <span class="cnt" id="woCount">加载中…</span>
            <button class="refresh" id="refreshWo" style="float:right;font-size:12px;">刷新</button>
          </div>
          <div id="woList"><div class="woEmpty">正在读台账…</div></div>
        </div>
      </div>

      <!-- ③ 服务与登记（低代码 + #24 决策① 的登记条目）：原样搬进来 -->
      <div id="view-svc" class="nav-view">
        <div class="wo">
          <div class="woHead">🧩 服务（可低代码新增）
            <span class="cnt" id="svcCount">加载中…</span>
            <button class="refresh" id="refreshSvc" style="float:right;font-size:12px;">刷新</button>
          </div>
          <div class="svcAdd">
            <input id="svcIdea" placeholder="说一句话就行，例如：加一个失物招领，学生丢了饭卡可以登记" />
            <button id="svcGo">生成服务</button>
          </div>
          <div id="svcList"><div class="woEmpty">正在读服务清单…</div></div>
        </div>
      </div>

      <!-- ④ 客服人格：原样搬进来，只是从首屏大卡降成一颗按钮的内容 -->
      <div id="view-persona" class="nav-view">
        <div class="persona">
          <div class="left" id="personaBox"><div class="nm">加载中…</div></div>
          <div><button class="refresh" id="refreshPersona">刷新人格</button></div>
        </div>
      </div>
    </div><!-- /stage -->

    <!-- 输入区钉在底部：只有「对话」视图用它（其余视图 body 带 no-chatbox，整块隐藏） -->
    <div class="composer">
      <div class="inputbar">
        <input type="file" id="file" style="display:none;" />
        <button class="attach" id="attach" title="上传资料给客服读">📎</button>
        <button class="clear" id="clearChat" title="清空对话记录（左栏台账一条不动）">🗑 清空</button>
        <input id="text" placeholder="问点什么，或让我写提醒文案…" />
        <button id="send">发送</button>
      </div>
    </div>
  </main>
</div><!-- /shell -->
```

**移动时不许改动的部分**（id 一个都不能变，否则下面那些既有逻辑全断）：
`#personaBox` `#refreshPersona` `#woList` `#woCount` `#refreshWo` `#svcList` `#svcCount` `#refreshSvc` `#svcIdea` `#svcGo` `#chips` `#log` `#file` `#attach` `#text` `#send`。

### 3.4 新增 JS：视图切换 + 未结角标

粘在 `loadPersona()` 定义之前即可（`switchNav` 会被下面的调用引用，函数声明提升，位置不敏感）：

```js
// ---------- 三栏外壳：左栏点开哪块，中间就只显示哪块 ----------
// 只用 display 切，DOM 全程不重建 —— 所以：
//   · 上传后临时换成的那 3 条建议问题，切走再切回来**还在**（不用重画）；
//   · 「服务与登记」里正在填的表单不会被切视图抹掉（原来 5 秒轮询那两条防抖逻辑照旧生效）。
const NAV_TITLE = { chat: "对话", wo: "待处理问题", svc: "服务与登记", persona: "客服人格" };

function switchNav(view) {
  // 只有「对话」需要输入框，其余三块纯查看 —— 整块隐藏 composer，免得人以为那块能聊天
  document.body.classList.toggle("no-chatbox", view !== "chat");
  document.querySelectorAll(".nav-view").forEach(v => v.classList.remove("active"));
  const nv = document.getElementById("view-" + view);
  if (nv) nv.classList.add("active");
  document.querySelectorAll(".sb-item").forEach(b => b.classList.toggle("active", b.dataset.nav === view));
  const t = document.getElementById("navTitle");
  if (t) t.textContent = NAV_TITLE[view] || "对话";
  // 进哪块就现拉一次，别让人对着上一分钟的数据看（5 秒轮询照旧在跑，这里只是"立刻刷一下"）
  if (view === "wo") loadWorkorders();
  else if (view === "svc") svcTick();
  else if (view === "persona") loadPersona();
}

document.getElementById("sbNav").addEventListener("click", e => {
  const item = e.target.closest(".sb-item");
  if (item) switchNav(item.dataset.nav);
});

switchNav("chat");   // 开场停在对话
```

### 3.5 改 `loadWorkorders()`：角标与计数收成一处

原逻辑把 `cnt.textContent = ...` 在**早退分支和正常分支各写了一遍**（254-259），现在又要多一个角标 —— 再抄一遍就是"同一判断抄三处"。
按项目纪律收成一个小函数，**在早退判断之前调用一次**：

```js
// 「共 N 条，未结 M 条」+ 左栏角标：一个判断只写一处
// （原来这句在早退分支和正常分支各抄了一份，加角标就会变成三份）
function paintWoCount(total, open) {
  const cnt = document.getElementById("woCount");
  cnt.textContent = total ? `共 ${total} 条，未结 ${open} 条` : "暂无";
  const num = document.getElementById("woNum");
  if (num) {
    num.textContent = open;                            // 只放数字，窄屏绝对定位在图标右上角
    num.classList.toggle("show", open > 0);            // 0 条时不挂红点，别天天报警
  }
}
```

`loadWorkorders()` 里把原来那两处 `cnt.textContent = ...` 删掉，改成：

```js
const open = list.filter(w => w.status !== "已解决").length;
paintWoCount(list.length, open);        // ← 早退前先画：状态没变也要保证角标是最新的
const sig = JSON.stringify(list);
if (sig === woSig || woBusy) return;    // 后面这段原样保留
```

> 顺序很关键：`paintWoCount` **必须在 `if (sig === woSig || woBusy) return;` 之前**。
> 放到后面的话，清单没变化时角标永远不更新 —— 而"角标要能在别的视图看见"正是这次改造的补偿机制。
> （`catch` 分支里原有的 `cnt.textContent = "读取失败"` 保留，顺手也把角标收掉：`num.classList.remove("show")`。）

### 3.6 新增【清空】按钮的完整逻辑

```js
// ---------- 清空对话记录 ----------
// ⚠️ 这里清的是**聊天流**，不是台账。
//    · /api/chat 在 station 这条路上每轮都是新的随机 session（main.py:1366，
//      前端从不传 session_id），所以对话历史**只活在 #log 这个 DOM 里** ——
//      清空 = 把 #log 清回刚打开页面的样子，不需要也不应该去调 /api/chat/reset
//      （拿一个空/新 session 去清，什么也清不掉，只会让后人以为"服务端也清了"）。
//    · 待处理问题、服务与登记、学生登记条目**一条都不动** —— 界面上必须把这句说出来，
//      否则管理员会以为【清空】把学生上报的问题也删了，那就没人敢点了。
const greetEl = log.querySelector(".msg.bot");     // 开场白那条，清完留着
const clearMask = document.getElementById("clearMask");

function clearMsgCount() {
  const n = log.querySelectorAll(".msg").length;
  return greetEl ? Math.max(0, n - 1) : n;         // 减去开场白
}

function openClearDialog() {
  const n = clearMsgCount();
  document.getElementById("clearSummary").innerHTML =
    `当前这段对话里有 <b>${n} 条</b>消息。` +
    (n <= 0 ? "现在还只有开场白，暂时没什么可清的。"
            : "清空之后它们就都消失了，找不回来。");
  clearMask.classList.add("show");
  document.getElementById("clearCancel").focus();
}
function closeClearDialog() { clearMask.classList.remove("show"); }

function doClearChat() {
  if (greetEl) {
    Array.from(log.children).forEach(el => { if (el !== greetEl) el.remove(); });
  }
  renderChips(CHIPS);      // 快捷操作恢复成默认 5 条（上传资料后会被换成 3 条建议问题）
  addMsg("系统", "对话已清空。左边「待处理问题」「服务与登记」里的台账一条没动。", "sys");
  closeClearDialog();
}

// 把原第 197-204 行那段 chips 渲染抽成函数，好让清空后能复原
function renderChips(list) {
  const box = document.getElementById("chips");
  box.innerHTML = "";
  list.forEach(c => {
    const el = document.createElement("div");
    el.className = "chip " + (c.cls || "");
    el.textContent = c.t;
    el.addEventListener("click", () => sendMsg(c.q));
    box.appendChild(el);
  });
}
renderChips(CHIPS);

document.getElementById("clearChat").addEventListener("click", openClearDialog);
document.getElementById("clearCancel").addEventListener("click", closeClearDialog);
document.getElementById("clearOk").addEventListener("click", doClearChat);
clearMask.addEventListener("click", e => { if (e.target === clearMask) closeClearDialog(); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && clearMask.classList.contains("show")) closeClearDialog();
});
```

配套的对话框 DOM（放在 `.shell` 之后的 `</body>` 前）：

```html
<div id="clearMask" class="mask">
  <div class="dlg" role="dialog" aria-modal="true">
    <div class="dlg-head">🗑 清空对话记录</div>
    <div class="dlg-body">
      <p id="clearSummary" style="margin:0 0 8px;line-height:1.6;"></p>
      <div class="dlg-warn">
        删掉的只是<b>屏幕上这段聊天记录</b>，清完从新的一段开始。<br />
        <b>台账不受影响</b>：待处理问题、服务登记条目、快递数据一条都不会变。
      </div>
    </div>
    <div class="dlg-foot">
      <button class="dlg-btn ghost" id="clearCancel">取消</button>
      <button class="dlg-btn danger" id="clearOk">清空记录</button>
    </div>
  </div>
</div>
```

> 两处小重构必须同批做，否则会残留"同一件事两份实现"：
> ① `chips` 渲染抽成 `renderChips()`，**`handleFile()` 里那 3 条建议问题也改用它**（现在是手写的一段重复代码，530-537）；
> ② `#chips` 那句 `const chipsBox = document.getElementById("chips")` 若只被 `handleFile` 用，就一并删掉，统一走 `renderChips`。

---

## 4. 单子 S2（**建议做，但请用户拍板**）：驿站端固定一个 session

**为什么提：** 现在 `station.html` 不传 `session_id`，`main.py:1366` 每轮补一个随机 id。两个后果：

1. **客服没有跨轮记忆**。问"这个学生的问题处理到哪了"，下一句"那第二个呢"它接不住（每轮都是新会话，`get_conversation()` 返回空）。
2. **垃圾会话堆积**：每一轮都会往 `app/data/sessions.json` 里追加一个只含 2 条消息的新会话，跑得越多越长（`MAX_HISTORY` 只限单会话长度，不限会话数量）。

**改法（很小）：**

```js
// 驿站这台机器固定一个会话 id，客服就有了上下文记忆
const SID_KEY = "campustime_station_session";
function stationSid() {
  let s = localStorage.getItem(SID_KEY);
  if (!s) { s = "st" + Math.random().toString(16).slice(2, 12); localStorage.setItem(SID_KEY, s); }
  return s;
}
// sendMsg 的请求体：
body: JSON.stringify({ message: msg, module: "station", session_id: stationSid() })
```

**并且此时【清空】必须跟着升级**（两件事同生共死，不能只做一半）：

```js
// 对话历史现在真的在服务端了 → 先清服务端，成功了再清屏幕
const r = await fetch("/api/chat/reset", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_id: stationSid() }),
});
const d = await r.json().catch(() => ({}));
if (!r.ok || d.ok === false) {
  addMsg("系统", "清空没成功，聊天记录原样保留，请再试一次。", "sys");
  closeClearDialog();
  return;
}
localStorage.setItem(SID_KEY, "st" + Math.random().toString(16).slice(2, 12));  // 换新 id，F5 不复活
// …然后再走 3.6 里清 DOM + 恢复 chips 那两步
```

**为什么标注"要拍板"：** 这会让驿站客服**开始记得上一句**（更自然、更像个真客服，也顺手止住 `sessions.json` 的垃圾堆积），但它确实**改变了现有的演示行为**。
建议：先做 S1 上真机，看一眼"加了记忆之后"的表现是否如预期（会不会把上一位学生的问题带到下一位身上 —— 驿站端是一台公用机器，这个风险要实测，不能靠推断）。

---

## 5. 顺手账（可选，1 行）

`EXPLAIN.md:268-269` 还写着「**驿站页面上看不见学生登记进来的条目** —— 页面只摆了登记表单，没摆列表」。
但 `station.html:401-433` 已经有 `loadRecords()` 了（#24 决策① 已落地，只是文档没跟上）。
顺手把那半句改成"已做（#24 决策①）"，只留「报修被工单硬分支劫持」那条 —— **文档和代码对不上，演示前被问到就是一枪**。

---

## 6. 给实现 AI 的纪律

1. **只改 `app/static/station.html` 一个文件。** 不许碰后端、不许碰 `/api/*`、不许碰 `student.html` / `admin.html` / `login.html`。
2. **id 全部保持原样**（3.3 末列了清单）。前端是 `getElementById` 硬绑的，改个 id 就等于把功能删了。
3. **整族扫**：删 `header` 就要连它的 3 条 CSS 一起删；改 chips 渲染就要连 `handleFile()` 里那段重复实现一起收。
4. **注释里的行号/例子必须实测过**再写（本单已核 `main.py:1366`、`station.html` 全部行号、`student.html:2646-2688`）。
5. **不许往 `station.html` 里塞新依赖**（不引 glass.css、不引 SVG 精灵、不引框架）。这个页面的价值是"一个文件、打开就能演示"。
6. 改完**不要顺手"优化"**轮询节奏、加密钥、加登录 —— 那些是别的单子。

---

## 7. 真机验证清单（Playwright，`channel="msedge"`，逐条断言，别只说"看起来正常"）

| # | 断言 | 通过标准 |
|---|---|---|
| 1 | 外壳结构 | `.sidebar` 存在；`.sb-item` 恰好 **4** 个 |
| 2 | 点击切视图 | 点「🛠️ 待处理问题」→ `#view-wo` 有 `active`、`#view-chat` 无；`document.body.classList.contains('no-chatbox') === true`；`getComputedStyle(document.querySelector('.composer')).display === 'none'` |
| 3 | **不用上下翻（用户核心诉求）** | `document.documentElement.scrollHeight <= window.innerHeight + 1`；且当聊天超过一屏时 `#log.scrollHeight > #log.clientHeight`（内容在**内部**滚） |
| 4 | 输入框钉底 | 在「对话」里发 8 条消息后，`#text` 仍在视口内（`getBoundingClientRect().bottom <= innerHeight`），发送照常工作 |
| 5 | 清空按钮存在且可用 | 先发 3 条 → 点「🗑 清空」→ 弹框文案显示"**3 条**" → 点【清空记录】→ `#log.querySelectorAll('.msg').length === 2`（开场白 + 一条系统回执） |
| 6 | 清空不动台账 | 清空前后 `GET /api/station/workorders` 与 `GET /api/station/services/<id>/records` 的返回**逐字节相同** |
| 7 | chips 复原 | 上传一个 txt（`#file`）→ chips 变 3 条建议 → 清空 → `#chips .chip` 长度回到 **5** |
| 8 | 角标 | 造一条未结工单 → ≤5s 内 `#woNum` 出现且文本为未结条数；**切到「对话」视图后仍可见**；把工单改成"已解决"后 ≤5s 内角标消失 |
| 9 | 改状态没被 detach | 在「待处理问题」点【改成「处理中」】→ 状态变化，控制台无 `Node is detached from document`（原注释 251-253 记的那条坑） |
| 10 | 切视图不丢临时内容 | 上传后那 3 条建议问题 → 切到「服务与登记」再切回「对话」→ **还是那 3 条**（证明是 display 切换而非重建） |
| 11 | 窄屏 | 窗口设 700px 宽 → 侧栏宽度 64px、`.sb-brand .txt` 不可见、正文没被挤坏；恢复宽度后样式复原 |
| 12 | 人格仍活 | 在「客服人格」点【刷新人格】→ 卡片重新拉 `/api/persona`，且系统消息里出现"已重新读取客服人格配置" |

> 跑完把 12 条结果（实测值，不是"OK"）贴回来。
> 另：前端改动**不需要**跑 pytest，但如果实现过程中碰了任何 `.py`，就必须全量复跑并把结果贴回。

---

## 8. 本单明确不动的东西

- `/api/chat`、`/api/station/*`、`main.py`、`station.py` 一行不改。
- `student.html` / `admin.html` / `login.html` 不改（三栏外壳是**抄结构**，不是抽公共文件 —— 现在两个页面各自内联，抽公共 CSS 是另一单的事，别顺手做）。
- 待处理问题/服务登记/学生登记条目这些**业务块只换容器，逻辑一行不改**。
- 五份施工单（18/22/24 等）里的条目照旧，本单不覆盖、不前移。
