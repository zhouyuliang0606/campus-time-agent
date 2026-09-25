# 2026-09-25 开发会话快照：确认落空 + "AI 失忆"体感的根治

> 类型：Bug 根因诊断 + 修复（学生端"确认待办写不进库"+ 刷新像失忆）
> 关联 commit：`b57f99c`（pending 内存→磁盘持久化）
> 验证：TestClient 冒烟（加待办→卡片→pending.json→模拟重启重读→确认写库→入库）全通过

---

## 一、用户给出的 Prompt（摘录，按时间）

1. 「你改完了，我刷新了，但是毛病一个没少（附截图：聊天里回「确认我收到了，但待办还没真正入库」）」
2. 「还有我感觉我的 ai 没有做记忆，有点上下文连不起来」
3. 「你看看是不是我对我的 agent 设置出了什么毛病」

---

## 二、AI 的研判与决策（关键推理链）

### 1. 先排除"agent 设置"和"没记忆"这两个误判
读 `app/main.py` 与 `app/store.py` 确认：
- 会话历史 `sessions.json` **本来就在落盘**，且 `/api/chat` 在 line 1774 `history = get_conversation(session_id)`、
  line 1796 `engine.run(message, history=history)` **真把历史喂给模型**。
  → "AI 没有记忆"不是代码级的记忆丢失。
- 真正的"失忆体感"来源：**可执行的提案（那张确认卡）是内存态，一重启/刷新就看不见了**，
  聊天文字还在，可"待办还没确认"这件事的载体没了，看起来就像 AI 忘了。

### 2. 定位"确认我收到了，但待办没入库"的真凶
读 `app/agent/pending.py` 第 22 行：`_PENDING: dict = {}` —— **纯内存**，
文件头注释自己写着"服务重启即清空——学生数据一个字节都没多写，安全"。
读 `app/main.py` 确认链路：
- 学生回"确认" → line 668 `if is_confirmation(message) and peek_pending(session_id)` 命中才由系统写库；
- 若 `peek_pending` 为空（内存态被清），就跳过确定性分支，最终掉回模型，
  模型手里没有写入工具，只能回一句"确认我收到了，但待办还没真正入库"（即截屏那句）。

**触发条件 = 本地开发时改完代码 `uvicorn --reload` 自动重启 / 手动重启**：
每次重启 `_PENDING` 清空 → 已出的确认卡消失 → 回"确认"写不进去 → 体感"毛病一个没少"。
这正好解释了用户"刷新后毛病还在"（他重启服务来加载新代码，却把待确认卡一起清掉了）。

### 3. 为什么之前"修三类口语缺口"没能解决
上一轮（`0f4852a`）只补了"意图识别"，让待办卡更容易被生成；
但**卡片生成后只要服务一重启就丢**，确认照样写不进库。根因在"提案暂存载体"而非"识别"。

### 4. 修复方向：把提案暂存从内存搬上磁盘
不重写成数据库（演示阶段保持"看得懂、改得动"），而是像 `sessions.json` 一样落盘：
- `save/take/mark/clear` 每次都写 `app/data/pending.json`；
- 读盘遵守与 `store.py` 同一约定：**每次重新读 `CAMPUSTIME_DATA_DIR` 环境变量、目录变更才重载**，
  保证多 `sandbox()` 测试互不串味、不碰真实演示数据；
- 保留 3 小时 TTL 过期清理。

---

## 三、具体改动（与快照同目录提交）

**`app/agent/pending.py`（重写）**
- 新增 `_pending_path()`：落盘路径 = `CAMPUSTIME_DATA_DIR` 或默认 `app/data` 下的 `pending.json`，**调用时**取环境变量；
- 新增 `_load()` / `_save()` / `_ensure_loaded()`：启动时与目录变更时从磁盘读回，`_ensure_loaded` 比对"上一次读取目录"避免跨 sandbox 串味；
- `save_pending / take_pending / mark_applied / clear_pending / _pending_entry` 全部在改动后 `_save()`；
- `CONFIRM_WORDS`、`_CONFIRM_RE`、`is_confirmation` 行为完全保留，仅因重写文件位置调整。

---

## 四、验证结果

```text
TestClient 冒烟（tests/_smoke_pending.py，临时）：
  加待办「加个游泳 周四 19:00-20:30」 → awaiting_choice=True, kind=todo_add  ✅
  pending.json 落盘、含该会话                                        ✅
  模拟重启（清空内存缓存）→ peek_pending 从磁盘命中 kind=todo_add     ✅
  回「确认」→ answer="✅ 已加入日程：游泳…"                          ✅
  GET /api/todos 真查到游泳                                          ✅
```
行为确认：
- 服务重启后，刷新页面确认卡仍从磁盘读出来，不再"失忆"；
- 回"确认"（打字或点按钮）都能稳定写库，不再掉回模型说"收到了但没入库"。

---

## 五、协同要点（给评审看的一句话）

模型全程不参与写库决策；这次把"提案暂存"从易失内存升级为磁盘持久化，
**重启/刷新都不丢卡**，从根上灭掉了"确认落空"与"AI 失忆"两个体感，
同时沿用 `store.py` 的"调用时读环境变量"约定，保证测试沙箱隔离。
