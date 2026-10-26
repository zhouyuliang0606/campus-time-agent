# 69 · 代码收尾：冻代码前的最后一单

> **这一单不改任何行为。** 三项是把 #68 剩下的收掉（两处注释 + 一条护栏），
> 第二节是**推送前的只读体检**（可复跑），第三节是交付动作的取舍。做完之后代码冻结。
>
> 现状：HEAD `1241a66`｜全量 `5124 / 0`（89 批）｜**未推送 291 笔**｜工作区 4 个常态脏项。

---

## 〇 本单为什么存在

代码侧剩下的全是「说到位」的活，没有一行是功能：

| 项 | 性质 | 不做的后果 |
|---|---|---|
| ① `main.py:6029` 那句 docstring 不实 | 注释 | 下一个人会以为 `dropped` 真的"告诉了学生" |
| ② `/api/todos/month` 没写「无页面调用」 | 注释 | 下一个人会花半天找它的消费者，或者误删 |
| ③ `test_88` B4 是计数判据 | 护栏 | **假绿**：注释凑数 / 只改一路都照绿 |

三条都不是 bug，但**注释撒谎和护栏假绿，比 bug 更难发现** —— 因为没人会去查它们。

---

## 一、三项待施工

### 1.1 `app/main.py:6029`：把「没人知道」改成实话

**现在（`:6026-6030`）**：

```python
    ⚠️ #66 收尾：缺 `id` 的记录**不往外交**，判据与工具那两路同一个 `_todo_has_id`。
       前端这一路把 `id` 当**操作句柄**（勾完成 PUT / 删除 DELETE / 改期都按它回传），
       缺了会原样变成 `id=undefined`（或 `id=None`）打回来 —— 要么删不掉、要么命中的是
       另一条。少一条但把条数说在 `dropped` 里，别让"少了一条"没人知道。
```

最后半句**不成立**：`grep -rn "dropped" app/static/` **零命中**，页面不读它。

**改成**（只动最后一句，前面保留）：

```python
       另一条。
    ⚠️ #69 更正这句的下半截：原来写的是「把条数说在 `dropped` 里，别让"少了一条"没人知道」——
       **`dropped` 现在零消费者**（`app/static/` 里 grep 不到这个键），页面不读它，
       所以"没人知道"这件事**没有兑现**：它是留给排查 / 日志 / 将来报警用的一个数字。
       为什么不在页面显示：学生既不知道 `id` 是什么、也修不了它，多一行只会变成噪音。
       真代价是**页面会比工具少数几条、而且学生看不出来** —— 这笔账记在
       `开发者接手指南.md` §六（挂账），要还它得先给页面一个"数据不合规"的落点，
       不是在读接口上再加一个字段。
```

### 1.2 `app/main.py:6037`：`todos_month` 补一条事实

在现有 docstring（`:6038-6042`）末尾追加：

```python
    ⚠️ #69：**今天没有一处页面调这个路由**。学生端两处月历都是自己按天分桶
       （`student.html:2346` 取 `/api/todos`、`:2434` 用 `todoOccurs(t, dIso)` 在前端筛），
       全仓只剩 `test_03` 与 `docs/build_tech_doc.py` 引用这里。
       **留着不删**：收口期不删对外只读接口 —— 删它要改那两处引用，还要赌"没搜到的调用方"
       （演示脚本、有人翻 `/docs` 顺手试接口）；而它现在已与 `/api/todos` 同一个判据，
       留着零维护成本。顺带一句：`list_todos_in_month` 本身**不是**死代码
       （`test_39:155/601/606` 三处直接用）。
```

### 1.3 `tests/test_88_...py:169`：B4 从计数改 AST

**现在**：

```python
    c.check("B4 两路的判据是**同一个** `_todo_has_id`（在 main 里各写一遍 `t.get(\"id\")` 就会漂）",
            "from app.modules.planner import" in read("app/main.py")
            and read("app/main.py").count("_todo_has_id(") >= 2,
            str(read("app/main.py").count("_todo_has_id(")))
```

**问题是它数字符串**：注释里写一遍就 +1；两处调用都在 `todos_list` 里、`todos_month` 一次都没有，也凑够 2。

**改成**（同时把 `import ast` 加进文件头部——现在没有）：

```python
    # #69：原判据是 `count("_todo_has_id(") >= 2` —— 计数判据，注释里写一遍就能凑够，
    # 把两路砍成一路也照绿。改成数**真调用**（AST）：注释不是节点，天然排除；
    # 并且逐函数分开数，才拦得住"两次都在 /api/todos 里、month 那路一次都没有"。
    main_src = read("app/main.py")
    calls = {}
    for node in ast.walk(ast.parse(main_src)):
        # ⚠️ 两路都是 `async def`（`ast.AsyncFunctionDef`）—— 只认 `FunctionDef` 会得到空字典，
        #    而"空 == 空"的断言最容易假绿。第一次写就是这样，`{}` 直接红给我看。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in (
                "todos_list", "todos_month"):
            calls[node.name] = sum(
                1 for x in ast.walk(node)
                if isinstance(x, ast.Call) and getattr(x.func, "id", "") == "_todo_has_id")
    c.check("B4 两路**各调一次**同一个 `_todo_has_id`（判真调用，不是 count；"
            "注释凑数 / 只改一路 都拦得住）",
            calls == {"todos_list": 1, "todos_month": 1}, str(calls))

    hard = [ln.strip() for ln in main_src.split("\n")
            if not ln.strip().startswith("#") and 't.get("id")' in ln]
    c.check("B5 main 里没有**第二份** id 判据（`t.get(\"id\")` 在 main 一行都不许有："
            "两路各写一遍就是两份会漂的账）",
            not hard, str(hard[:3]))
    return c
```

> 参考实现留了一份在 `D:\campustime-scratch\wip68\test_88.py`（那是临时副本，撤回过；
> **别整份覆盖**，只取 B4/B5 那段，因为主工作区的 A/B/C 三批已被执行方复核改过）。

**§一.3 已经在临时副本上验过（跑完已撤回，主工作区没动）**：

| 变异 | 旧 count 判据 | 新 AST 判据 |
|---|---|---|
| V4 只砍 `todos_month`（`keep = rows`） | 看到 1 次 → 红 | 红 3 条（B2 / B2-bis / B4，B4 报 `{'todos_list': 1, 'todos_month': 0}`） |
| V5 在 `todos_list` 里加**注释** `_todo_has_id(` | 2 → 3，绿（碰巧） | **rc=0 全绿**（不被注释骗） |
| V6 真调用换成注释（删一处 + 注释补一处） | 看到 **2** → **被骗绿** | 红 3 条 |

V6 是这一项存在的全部理由。

---

## 二、冻结前体检（只读，可复跑）

**下面是我在 `1241a66` 上实际跑出来的**，复跑应当得到同样的数；对不上就停下来查，别改期望值。

| # | 命令 | 期望 | 我跑到的 |
|---|---|---|---|
| 1 | `git ls-files app/data/` | 只有 **5** 个种子文件 | `accounts.json` / `courses.json` / `express.json` / `kb.json` / `takeout.json` ✓ |
| 2 | `for f in .env app/data/settings.json app/data/trace_log.jsonl app/data/intent_samples.json app/data/student/; do git log --all --oneline -- "$f" \| wc -l; done` | 全 **0**（从没跟踪过） | 五个都是 0 ✓ |
| 3 | `git grep -n "sk-[A-Za-z0-9]"` | 只有占位符/假值 | `.env.example:7`、`README.md:346`、`ai-logs/07`、`ai-logs/09`、`test_02:55` 的 `sk-abcdefghijklmnop1234` —— 全是假的 ✓ |
| 4 | `git grep -nE "ghp_[A-Za-z0-9]{10,}\|github_pat_[A-Za-z0-9_]{10,}"` | **零命中** | 零 ✓ |
| 5 | `git remote -v` | 不含 token | `https://github.com/zhouyuliang0606/campus-time-agent.git` ✓ |
| 6 | `ls requirements.txt .env.example` | 两个都在 | 在（`requirements.txt` 首行有 BOM，`pip install -r` 不受影响）✓ |
| 7 | `tests/run_all.py` | `5124 / 0`（89 批）；做完本单应是 `5125 / 0` | ✓ |

**第 1 条为什么是关键**：`app/data/` 下现在有 `student/`、`uploads/`、`intent_samples.json`、
`llm_usage.jsonl`、`trace_log.jsonl`、`services.json`、`workorders.json` 等一堆运行时数据 ——
它们**全部**在 `.gitignore` 里，`git ls-files` 只剩 5 个种子。这条一旦不是 5，**停手，别推**。

---

## 三、交付动作（不属于代码，但收尾绕不开）

### 3.1 四个常态脏项怎么处理

| 项 | 结论 | 理由 |
|---|---|---|
| `M app/data/kb.json` | **入库** | 加了「游泳馆价格」一条、把「健身房价格」的 keywords 改成「健身房」。`.gitignore:55` 明确写着这是种子知识库、有意版本管理 |
| `?? demo_timetable/` | **入库** | 三份课表素材（`.xlsx` / `.txt` / `.docx`，共 56 KB）。演示「导课表 / 粘一段通知」那段要用，不进仓库换台机器就演不了 |
| `?? run_local.bat` | **入库** | 14 行启动脚本，评委双击即用。⚠️ 里面**硬编码了 Edge 路径**（`C:\Program Files (x86)\Microsoft\Edge\...`）—— 没有 Edge 的机器上那一句 `start` 会失败，但**不影响后面 uvicorn 起来**。入库时在注释里补一句这个事实，别让人以为"双击没反应"是服务坏了 |
| `?? tests/_optional_browser/` | **入库** | 168 KB，有 `README.md` 自己声明「可选、不属于 `tests/run_all.py` 主套件、评委不装 Playwright 也跑得到」。入库反而是"过程可复现"的证据 |

一笔提交，标签建议：`chore(交付): 演示课表素材 + 本地启动脚本 + 可选浏览器 e2e + 种子知识库补一条`

### 3.2 291 笔推送

- 一次性 `git push origin master`，**别拆**
- 远端是 HTTPS，要 PAT（不是账号密码）；本机推 GitHub 通常要走代理
- 推之前把 §二 那 7 条跑一遍（尤其第 1、2 条）

### 3.3 演示链走一遍（交之前）

`run_local.bat` 双击 → 开 `/student` → 按 README §474/475 那张表走。
口径：**用默认模式，不开 Agent 模式** —— 定稿演示句本来就出 11 行轨迹 / 6 次工具 / 3 轮。

---

## 四、冻结声明（本单落地后生效）

**允许**：提交、推送、改文档（README / 指南 / 单子）。

**不允许**（要改就另开单）：

- 改 `app/` 下任何 `.py`
- 改 `app/static/` 下任何页面
- 动 `tests/` 里既有判据（除本单 §一.3 那一处）
- "顺手"统一口径、重构、补 `.get`

理由：#66 之后连续三单（#66/#67/#68）都是"改一处、带出一处"，而每次都要重跑 89 批 5125 项。
**收口的价值不在多修一个 bug，在于"现在这个状态是已知的"。**

---

## 五、不做的事

1. **不做 `/api/health/data` 自检**（#67 §二.3 已记）—— 它是 `dropped` 零消费者和 `files.py` 5 处欠账的共同正解，但它是可观测性，不是正确性。
2. **不收 `files.py` 那 5 处硬取**（#68 §2.2）—— 崩比错便宜。
3. **不统一 `main.py:3415` 第四条路**（#68 §三）—— 行为变更，先回答「缺 id 算不算一道待办」。
4. **不动技术文档 PDF** —— 那是**另一单**（`docs/技术文档.pdf` 停在 9-30、11 页、上限 30 页，缺 #42~#64 那串迭代）。本单只管代码，别把两件事混一个 commit。
5. **不删 `/api/todos/month`**（#68 §2.2）。

---

## 六、给执行方的三句

1. **这三项一行行为都不改。** 如果你发现自己在改逻辑（不是注释、不是判据写法），说明你已经出了本单范围 —— 停下来另开单。
2. **§二 那 7 条体检在推送前必须复跑一遍。** 尤其第 1 条（`git ls-files app/data/` 必须是 5 个），它一旦变多，说明运行时数据被跟踪了，**推出去就收不回**。
3. **做完就冻。** 本单之后唯一该发生的改动是"发现不实另立更正单"，不是"顺手再优化一下"。

---

### 落地记录（施工方填）

- 笔数 / 哈希：
- §一 三项各自改在哪一行：
- 体检 7 条复跑结果（逐条填"同"或"不同，差在哪"）：
- 全量：____ / ____（____ 批）
