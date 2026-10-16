# #47 修复 drop-in：sandbox() 不隔离 llm_usage.jsonl，测试笔污染真实账本

> 落点：`tests/_harness.py`（sandbox 机制）+ `app/store.py`（`log_llm_usage` / `_usage_path`）
> 性质：**测试基建修复**，不动任何业务 prompt、不动判定/路由/润色逻辑。
> 交付给：Qoder（或落地 AI）。本文件只给方案 + 护栏，不在此改代码。

---

## 0. 病根一句话

`sandbox()` 把真实 `app/data` 整体复制进临时目录（`_harness.py:104 shutil.copytree(REAL_DATA_DIR, dest)`），
但 `store.DATA_DIR` 在 import 时就定死（`test_04_persona_options.py:23` 注释已坐实"跨多个 sandbox() 块会过期"），
**账本写出路径没有跟着重定向到临时副本**。于是所有走 `patch_llm`（`_harness.py:286-290` 把 `get_llm_config` 打桩成 `{"model":"m"}`）
的测试，都会把 `"model":"m"` 的假模型调用记账写进**真实** `app/data/llm_usage.jsonl`。

后果：管理端「模型占比」榜首是 "m"（56 笔），纯测试 artifact；真实用户走真模型名，不受影响。

---

## 1. 修法（两步，缺一不可）

### 步骤 A —— 让账本路径支持 env 覆盖（核心阻断）

在 `app/store.py`：
- 新增一个 env 读取：`os.environ.get("CAMPUSTIME_USAGE_FILE")`。
- 改 `_usage_path()`：若该 env 有值，直接返回它（绝对路径）；否则维持原 `_path(USAGE_FILE)`。
- `log_llm_usage` 的 `makedirs` 要基于新路径的目录建（`os.makedirs(os.path.dirname(_usage_path()), exist_ok=True)`），
  因为临时文件可能落在 sandbox 的 data 目录里，目录已存在，但要稳妥。

### 步骤 B —— sandbox() 内设置该 env 指向临时账本

在 `tests/_harness.py` 的 `Sandbox.__enter__`：
- 在 `os.environ["CAMPUSTIME_DATA_DIR"] = dest` 之后，追加：
  `os.environ["CAMPUSTIME_USAGE_FILE"] = os.path.join(dest, "llm_usage.jsonl")`
- 在 `__exit__` / `_clean` 里 `os.environ.pop("CAMPUSTIME_USAGE_FILE", None)`（和现有 `CAMPUSTIME_DATA_DIR` 的 pop 并列）。

> 选 env 覆盖而非改 `DATA_DIR` 的原因：`DATA_DIR` 在 import 时定死、跨 sandbox 块会过期，是已知的坑；
> 单独给账本一条 env 覆盖最干净、不牵动其它读写路径。

---

## 2. 清存量（先备份！）

真实账本 `app/data/llm_usage.jsonl` 里现有 ~56 笔 `"model":"m"`。清法（让 Qoder 写一个小脚本或一步命令，**先 cp 备份**）：

1. 备份：`cp app/data/llm_usage.jsonl app/data/llm_usage.jsonl.bak-2026xxxx`
2. 过滤掉 `"model":"m"` 的行，原地回写（保留其它真实调用记录）。
3. 验证：重新读账本，确认无 `"model":"m"`，且行数 = 原行数 − 56。

> ⚠️ 不要在未备份时直接 `clear_llm_usage()` 全清——那会把真实的 1448 笔调用记录也一起抹掉，管理端历史就空了。
> 只删 "m"，保留真模型。

---

## 3. 护栏（落地 AI 必读，违反即打回）

1. **不破判定/路由/润色**：本修复只动账本写出路径 + sandbox env，绝不碰 `intent.py` / `router.py` / `polish.py` / 任何 `SYSTEM_PROMPT`。
2. **整族扫**：改 `store._usage_path` 前，先 grep 所有调用 `_usage_path()` / `read_llm_usage` / `log_llm_usage` / `clear_llm_usage` 的地方，
   确认没有别处硬编码了真实路径（重点查 `test_22` `test_34` `test_37` `test_48` `test_49`）。
3. **真机复验（必做）**：
   - 跑 `test_22_model_gateway_usage` / `test_34_api_key_not_stored` / `test_37_base_url_ssrf_gate` / `test_48_classify_budget` / `test_49_streaming_sse`，
     确认它们仍能读账本、仍全绿（它们读的是 sandbox 内的 `llm_usage.jsonl`，应不受影响）。
   - 跑完测试后，**再**检查真实 `app/data/llm_usage.jsonl`：确认**没有新增 `"model":"m"`**（复发阻断生效的证据）。
   - 启动 app 跑一条真实对话，确认真实模型名（如 deepseek-chat）**有**被记进账本（env 覆盖不影响非沙箱场景）。
4. **管理端复验**：登录管理端看「模型占比」，确认 "m" 已消失、真实模型占榜首。
5. **注释例子须实测**：在 `_usage_path` / sandbox 加的注释，凡提到"测试笔不再进真实账本"，必须跑一遍 test_49 后核真实 jsonl 真的没新增 "m" 才写进去。

---

## 4. 给评审/用户的人话说明（可贴进交付物）

> "m" 是测试桩用的假模型名，仅在本地跑测试套件时出现，会被记入模型调用账本。
> 现已让测试沙箱把账本隔离到临时文件，并清掉历史残留，管理端「模型占比」只反映真实模型调用。
> 真实用户请求不受影响。
