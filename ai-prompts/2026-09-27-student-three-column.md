# AI 对话快照 · 2026-09-27 · 学生端三栏外壳重构 + 学生端自定义模型 API

> 用户要把学生端 `student.html` 从「单栏堆叠」改成「三栏」布局，并希望模型选择
> 能让学生**自己填 API**（连服务器没配密钥也能跑真模型）。同时明确：管理端 / 驿站
> 保留为独立角色页，不并入侧边栏；设置入口「侧边栏常驻 + 点头像弹窗」两者都要。

## 用户原话 / 需求

- 「页面拆成三块，左侧侧边栏：顶端登录学生用户名和头像，设置，日程，校园问答，
  代办事项，上传文件。你觉得设置是单独放出来在侧边还是通过点击头像进入可以看见。
  中间主体对话气泡，学生提问，agent 回复。底部输入区域，文本输入框，上传文件按钮，
  模型选择（里面可以自己添加 api），粘贴大段文件文本。」
- 确认项（AskUserQuestion 结论）：
  - 设置入口 → **两者结合**：侧边栏常驻「设置」+ 点头像弹「账号信息 / 设置 / 退出」。
  - 管理端 / 驿站 → **都保留独立角色页**，顶部链接进，不并入侧边栏。
  - 用户名 / 头像 → **本地档案 + 预设头像画廊 + 上传**，密钥仅存本机浏览器。
- 「快递 / 外卖」不在五个主项里 → 收进侧边栏「更多服务 ▾」折叠组，功能保留。

## AI 研判（架构决策）

1. **外壳 vs 业务逻辑分离**：原 `student.html` 有 ~1600 行、1140+ 行成熟 JS（sendMsg /
   openConfirm / 日程周月表 / 快递外卖视图 / FAQ / 语音 / 历史恢复）。判定层重写风险高，
   故采用「**外壳重写 + 原 `<script>` 整段不动**」：只替换最外层 DOM 骨架与新增一段
   交互 IIFE，1140 行聊天/确认/排期逻辑一行不改，杜绝回归。

2. **模型覆盖的红线不变**：执行层（查空档 / 排期 / 出卡 / 写库）永远系统独占，模型
   碰不到写库接口。学生填的 API 只改变「**用哪个模型回答**」，不改变「**谁执行写入**」——
   写入权仍在后端确认条，符合项目最硬的设计原则。

3. **覆盖优先级**：学生端 `model_config`（自己填的 key）> 管理后台 `settings.json` >
   `.env`。服务器没密钥时，学生也能用自己的 key 跑真模型；`mock_planner` 离线兜底分支
   改用 override 判定 key，学生自带 key 时跳过 Mock。

## 代码改动

### 1. `app/static/student.html`（外壳三栏 + 新交互，391 增 / 37 删）

- **骨架**：`<header>`+`<div class="wrap">` → `<div class="shell">` 内含
  `<aside class="sidebar">`（头像档案 + 导航 + 更多服务折叠 + 头像弹窗 + 页脚）与
  `<main>`（topbar + stage + composer）。原 `chatResetMask` / `inputbar` 搬入 `main`，
  新增 `#navTodos` / `#navUpload` / `#navSettings` / `#modalMask`。
- **导航切换**：`switchNav(view)` 处理 chat / schedule / express / takeout / todos /
  upload / settings 七态；schedule/express/takeout 用原 `showView` 复用成熟面板，
  panel-mode 时隐藏聊天相关 UI、只显示 `#panel`。
- **档案头像**：本地 `localStorage` 存昵称 + 头像索引；预设 12 个 emoji 头像画廊 +
  FileReader 上传自定义头像（base64 存本机）。
- **模型选择**：底部 `modelSel` 下拉 + 「添加 / 修改 API」弹窗，填 base_url / model /
  api_key，存 `localStorage.ct_model_cfg`；发送时 `sendMsg` 把 `model_config` 随请求带出。
- **代办 / 上传 / 设置视图**：代办走 `PUT /api/todos/{id}` 标记完成、`DELETE` 删除；
  上传走拖拽 + `POST /api/upload`；设置含助手名 / 性格（拉 `/api/student-personas`）/
  模型 API。
- **CSS**：`.shell` flex 100vh 三栏，侧边栏 232px，composer 固定底部；窄屏 ≤720px
  侧边栏收成图标条；`.panel-mode` 规则控制聊天↔面板互斥显示。

### 2. 后端 `model_config` 透传（config / client / engine / router / main）

- `app/config.py` `get_llm_config(override=None)`：override 带有效 key 时直接返回它，
  优先级最高。
- `app/llm/client.py` `DeepSeekClient._config(override)` / `chat(..., override=None)`：
  透传给 `get_llm_config`，现用现取、后台改完即时生效不变。
- `app/agent/engine.py` `AgentEngine(..., llm_override=None)`：存 `self.llm_override`，
  `run` 里 `self.llm.chat(..., override=self.llm_override)`。
- `app/agent/router.py` `route(..., override=None)`：LLM 分类那一档透传 override，
  没服务器密钥也能走学生 key 做语义路由。
- `app/main.py` `/api/chat`：读 `body.model_config`（无有效 key 则置 None）→ 传
  `AgentEngine(llm_override=model_config)` 与 `router.route(override=model_config)`；
  `mock_planner` 分支改用 `get_llm_config(override=model_config)` 判 key。

## 验证

- **结构**：`student.html` div 开合计数 159/159 平衡；新增 JS 经 `node --check` 语法通过；
  `sendMsg / sessionId / escapeHtml` 等被引用函数均存在于原脚本。
- **后端编译**：`py_compile` 五个文件全部通过。
- **覆盖链路（单测级）**：monkeypatch `httpx.AsyncClient`，`AgentEngine(llm_override=
  {base_url, model, api_key})` 跑 `run()`，断言**发出的 HTTP 请求** Authorization /
  base_url / model 全部等于学生自定义值 → **PASS**（override 真正抵达大模型调用）。
- **接口冒烟（本地起 uvicorn :8000）**：
  - `GET /student` → 200，含 `class="shell"` 三栏骨架与全部导航项。
  - `POST /api/chat` 各类消息 → 200，无 500 崩溃（路由 / 意图兜底 / Mock 均优雅）。
  - `GET /api/todos`、`/api/uploads`、`/api/student-personas` → 200（新视图依赖的接口齐备）。
- **非回归**：`module_key` 重路由行为、预存的「看不见 X / 查 X」意图分支均未被本改动触动
  （`git diff app/main.py` 仅 13 增 3 删，集中在 model_config 透传）。

## Commit

- `5b793fd` `app/config.py` `app/llm/client.py` `app/agent/engine.py` — 后端透传学生端自定义 API 覆盖
- `705992e` `app/main.py` `app/agent/router.py` — /api/chat 与 router 接入 model_config 覆盖
- `c125753` `app/static/student.html` — 学生端三栏外壳重构（侧边栏 / 主体 / 输入区）
- `ai-prompts/2026-09-27-student-three-column.md` — 本快照（与 README 索引同一 commit）
