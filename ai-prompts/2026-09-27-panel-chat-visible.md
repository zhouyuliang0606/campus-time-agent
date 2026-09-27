# 2026-09-27 回归修复：面板模式下聊天区不可见

## 用户反馈
> 固定了左边，我在日程发送消息后看不见对话，选择文件和代办事项可以不需要有对话框，那个只是看的

## AI 研判
- 上一步让模块卡片（日程/快递/外卖）也走 `switchNav`，而 `switchNav` 给 `chatStage` 加了 `panel-mode` 类。
- 旧 CSS 里 `.panel-mode #log, .panel-mode #chips, .panel-mode .thinking, … { display: none !important; }` 会把**整个聊天区藏掉**——于是日程里发消息后对话看不见。这正是"看不见对话"的根因。
- 用户期望：日程/快递/外卖 = 既能看面板、又能和管家对话并看到对话；代办/上传/设置 = 只是看，本就是独立 `nav-view`（chatStage 不激活，自然没有对话框）。后者其实已经满足，无需改。

## 具体改动（仅 student.html）
- CSS：`panel-mode` 下不再隐藏 `#log` / `#chips` / `.thinking`，只隐藏首页级组件（`.notices` / `#modules` / `#hint`）。面板在聊天区上方，对话区保留并可见。
- `addMsg`：追加消息后再把外层 `.stage` 滚动容器拉到底（`log.closest('.stage').scrollTop = scrollHeight`），面板模式下新消息也保证可见。
- 后端无需改动。

## 验证
- 提取 `<script>` 用 `new Function` 语法校验：SYNTAX OK。
- 本机 8000 服务已返回含新 CSS 的文件。

## 关联 commit
- 本批 1 个代码 commit（student.html）
- 1 个 Prompt 快照 commit（本文件 + README 追加条目）
