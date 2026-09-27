# 2026-09-27 对话框仅保留在「校园问答」与「日程」

## 用户反馈
> 对话框只在校园问答和日程保留，因为只有这两个需要用到，校园问答的初始欢迎提示词没改

## AI 研判
- 上一步让快递/外卖也保留聊天区（`panel-mode` 不再隐藏 `#log`），但用户只要"校园问答 + 日程"两个模块有对话框，其余（快递/外卖/代办/上传/设置）纯查看。
- 关键事实：`.composer`（输入框/发送/📎/🎤/🧹）是 `.main` 里的全局元素，不在 `chatStage` 内，所以对所有模块都常驻——之前代办/上传其实也带着输入框，只是聊天区被藏了。
- "校园问答的初始欢迎提示词没改"：把 `MODULE_GREETING.chat` 改回最初那句原文（"你好，我是你的校园时间管家 👋 …"，含中文引号），保证一字未动。

## 具体改动（仅 student.html）
- 新增 CSS：`body.no-chatbox` 下隐藏 `.composer` / `#log` / `#chips` / `.thinking`。
- `switchNav`：按 `chatMod = (view==="chat"||view==="schedule")` 切换 `document.body.no-chatbox`；仅 chatMod 为 true 时才 `resetLogForModule`（换开场白+历史）。快递/外卖仍走 `panel-mode` 显示面板，但被 `no-chatbox` 把对话框整体藏掉，变成纯查看。
- `MODULE_GREETING.chat` 改为原始欢迎词字面量；`applyGreeting` 兼容"字符串/函数"两种写法。
- 后端无需改动。

## 验证
- 提取 `<script>` 用 `new Function` 语法校验：SYNTAX OK（中途因 chat 字符串里混用 ASCII 双引号报错过，已改用中文引号修复）。
- 本机 8000 服务已返回含 `no-chatbox` 的文件。

## 关联 commit
- 本批 1 个代码 commit（student.html）
- 1 个 Prompt 快照 commit（本文件 + README 追加条目）
