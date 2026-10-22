# 56｜时区收口 + JS 语法闸并进收口 + 时钟耦合排查 + test_39 钉起始态（drop-in）

> 来源：2026-10-22 20:21 用户拍板「**出单，和时间的 a 出一起去做**」。
> 四件事放一张单，因为它们都动"跑测试时看到什么"，分开做会互相干扰判断。
> 执行顺序：**§二（JS 闸，30 分钟）→ §一（时区，1~2 小时）→ §三（test_39，10 分钟）→ §四（#69，半天）**。
> 顺序的理由：JS 闸最快、防的是"页面白屏"；时区改完会让所有"日期相关"的测试结果重算一次，
> 必须排在 #69 排查**之前**，否则分不清一个红是时钟耦合还是时区改错。

---

## 〇、三条现场事实（实测，其中一条推翻了我之前的建议）

| # | 事实 | 依据 |
|---|---|---|
| 1 | **`ZoneInfo("Asia/Shanghai")` 在本机直接抛异常** | `ZoneInfoNotFoundError: No time zone found with key Asia/Shanghai` —— Windows 没有 IANA 时区库，本项目 `.venv` 也没装 `tzdata`。**上一轮我建议的写法在这台机器上跑不起来** |
| 2 | `datetime.timezone(timedelta(hours=8), "CST")` 可用且钟面与系统一致 | 实测 `now(+8)=2026-10-22 20:22:06.249328+08:00`，`now()=…249335`，去 tz 后可正常相减 |
| 3 | **test_39 清空真实待办后仍 191 项全绿** | 把 `admin1/todos.json` 置成 `{"todos": []}` 再跑，191/0；加 4 条（含一条落在下周日 10-25）也是 191/0 |

⇒ 事实 1 说明：**方案要改成固定 +8 时区**，不是 `ZoneInfo`。
⇒ 事实 3 说明：test_39 那条"会自己红"**没复现**，它是"结构上有可能"，不是"已经坏了"。

---

## 一、A：时区收口（新建 `app/clock.py`，全项目 24 处换成它）

### 1.1 为什么必须做

现在全项目取时间都是 `datetime.datetime.now()` —— 它跟着**操作系统时区**走。
本机是 UTC+8 所以看不出问题；一旦部署到默认 UTC 的云服务器：

- 20:00 会显示成 12:00
- 22 点以后的请求会跨到"前一天"，模型说"今天是 10-21"，日程整体错一天
- `_kb_stop`/候选时段/上报时间全都跟着错，而且**没有任何报错**，全是静默错

### 1.2 `app/clock.py`（新文件，全文）

```python
"""全项目**唯一**的取时间入口（#56）。

为什么单独开一个文件而不是各处 `datetime.now()`：
  ① `datetime.now()` 跟**操作系统时区**走。本机 UTC+8 看不出问题，
     部署到默认 UTC 的云主机会差 8 小时，22 点后连"今天"都错一天，而且是静默的。
  ② 同一件事有 24 个写法，就是 24 个可能漏改的地方（红线 12 那一族）。

⚠️ **不用 `ZoneInfo("Asia/Shanghai")`**：Windows 没有 IANA 时区库，
   本项目 `.venv` 也没装 `tzdata`，实测直接 `ZoneInfoNotFoundError`（2026-10-22 本机跑过）。
   固定 +8 是对的：中国 1991 年后不实行夏令时，全年 UTC+8，没有例外。
"""
import datetime

# 命名 CST 只是给人看的标签，判断依据是那个 timedelta
_TZ = datetime.timezone(datetime.timedelta(hours=8), "CST")


def now() -> datetime.datetime:
    """此刻（**东八区钟面值，naive**）。

    ⚠️ 故意 `.replace(tzinfo=None)`，这不是疏忽：
       全项目的时间都是 naive —— `ts` 存成 `"%Y-%m-%d %H:%M:%S"` 字符串，
       各处再 `strptime` 回来做减法（`main.py:4407` 的 `now() - t`、
       `express.py:62` 的 `today() - arrived` 都是）。
       混进 tz-aware，这些减法会**当场 TypeError**。
       要时区就在这一层定死，**不要在任何一个调用点带 tz**。
    """
    return datetime.datetime.now(_TZ).replace(tzinfo=None)


def today() -> datetime.date:
    """东八区的今天。"""
    return now().date()


def now_str() -> str:
    """落盘用的时间串（`ts` / `created_at` 的统一格式）。"""
    return now().strftime("%Y-%m-%d %H:%M:%S")
```

**这个模块只 import 标准库**，不许 import 任何 `app.*` —— 它会被所有模块依赖，反向依赖会成环。

### 1.3 要改的 24 处（精确清单，AST 扫出来的）

| 文件 | 处数 | 具体 | 改成 |
|---|---|---|---|
| `app/store.py` | 4 | `L176`（`_now()` 内部）、`L1153`、`L1182`、`L1406` | `clock.now_str()` / `clock.today()` |
| `app/modules/planner.py` | 15 | `L246`（now）、`L392 L468 L594 L601 L602 L717 L720 L997 L999 L1152 L1471 L2610 L2612 L2629`（today） | `clock.now()` / `clock.today()` |
| `app/main.py` | 3 | `L353`（`_now_block`）、`L4407`（`_ago_hours`）、`L5652` | `clock.now()` / `clock.today()` |
| `app/agent/intent.py` | 1 | `L482`（给模型算"今天几号"） | `clock.now()` |
| `app/modules/express.py` | 1 | `L62` | `clock.today()` |

**不改的 6 处 `time.time()`**（`intent.py:362/365`、`pending.py:66/129/190`、`config.py:160`）：
那是 epoch 秒，用来算 TTL / 缓存过期，**与时区无关**。改了没有任何好处，反而多 6 个出错点。

### 1.4 三条改法上的硬要求

1. **一律 `from app import clock` 然后 `clock.now()`，不许 `from app.clock import now`**。
   直接 import 名字的话，打补丁/测试里替换不了（项目里 `main.py:1057` 那条
   "函数内导入：改动配置/打补丁都传入得去"就是同一个道理）。
2. **`store._now()` 保留**（它是落盘时间的唯一出口），只把它的实现换成 `clock.now_str()`。
   别把 `store._now()` 删掉改成散调 `clock.now_str()` —— 那会把"一个出口"变成 24 个。
3. **`planner.py` 顶上已经有 `import datetime`**，保留（它还用 `datetime.date.fromisoformat` 等）；
   只把取"现在"的那几处换掉。**不许顺手把 `datetime` 整个删掉**。

### 1.5 验证：**不能只在本机验**（本机就是 +8，改了看不出任何差别）

必须做这条**变异式验证**：

```python
# 临时把 _TZ 改成西五区，看 clock.now() 会不会跟着变
import app.clock as clock, datetime
before = clock.now()
clock._TZ = datetime.timezone(datetime.timedelta(hours=-5), "EST")
after = clock.now()
assert (before - after).total_seconds() > 12 * 3600   # 差 13 小时才对（+8 与 -5）
```

能跟着变 ⇒ 它确实是按 `_TZ` 算的，不是跟着系统走。**这条过了才算时区改对。**

---

## 二、B：JS 语法闸并进收口（**先做这一节**）

### 2.1 病在哪

`run_all.py` 只做一件事：`glob("tests/test_*.py")` 逐个开子进程跑。**它完全不碰 JS。**

页面是 HTML，Python 只把它当字符串 `_render` 出去。所以：

> JS 里一个引号写错 ⇒ 后端上千项测试全绿 ⇒ 学生打开页面**一片空白**

`tests/_check_static_js.js` 这个脚本早就存在，但它有两个问题：

| 问题 | 现状 |
|---|---|
| **要手跑** | `run_all.py` 里一个字都没提到它 |
| **只查 2 个页面** | 脚本里写死 `["confirm.html", "student.html"]`。全站 7 个页面，**漏了 `admin` `station` `plan` `login` `settings`** |

而 #54 刚改的正好是 `student` / `station` / `admin` —— **其中两个在漏掉的名单里**。

### 2.2 改 `tests/_check_static_js.js`：不许写死清单

```js
// 校验静态页内联 JS 的语法（页面是 HTML，node --check 直接读不了，先抽 <script> 内容）
const fs = require("fs");
const path = require("path");

// ⚠️ **不许写死文件清单**（#56 的起因）：以前这里固定两个文件，
//    后来加了 station.html / admin.html 却没人回来补，于是"闸是全绿的、页面是坏的"。
//    以后加页面自动被覆盖。
const DIR = "app/static";
const files = fs.readdirSync(DIR).filter(f => f.endsWith(".html")).sort().map(f => path.join(DIR, f));

// 一个页面都没有 = 路径写错了（比如从 tests/ 里跑）。这时"全部通过"是假绿，必须报错。
if (!files.length) {
  console.log("❌ " + DIR + " 下一个 .html 都没找到，路径不对，这次没有验到任何东西");
  process.exit(2);
}
```

其余逻辑不动（`new Function(code)` 那一套），只把"通过/失败"的汇总口径保持 `process.exit(1)`。

### 2.3 改 `run_all.py`：开头调一次

```python
import shutil

def run_js_gate() -> tuple[int, int]:
    """静态页内联 JS 的语法闸（#56-B）。返回 (通过项, 失败项)。

    为什么放在最前面：它 1 秒就跑完，而后面 40 多个文件要跑几分钟。
    语法错了先看见，别让人等完再发现"页面根本打不开"。

    ⚠️ **找不到 node 就跳过并打印，算 0 项 0 失败，不许判失败**：
       评委机器上很可能只有 Python（这个项目一开始就定了"只依赖 venv 里已有的包"）。
       "因为缺一个可选工具就整批红"会让整条 run_all 失去可用性 —— 那不叫严格，叫没法用。
       但跳过**必须写在总账里**，不许静默：没验到的东西要让人知道没验到。
    """
    js = os.path.join(TESTS_DIR, "_check_static_js.js")
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        print("⚠️ 没找到 node，跳过 JS 语法闸 —— 这一项这次**没有验**\n")
        return 0, 0
    print("▶ JS 语法闸（app/static/*.html 的内联 script）")
    proc = subprocess.run([node, js], cwd=PROJ_DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    out = ((proc.stdout or "") + (proc.stderr or "")).rstrip()
    if out:
        print(out)
    ok = (proc.returncode == 0)
    print(("✅ JS 语法闸通过" if ok else "❌ JS 语法闸失败（上面有具体文件与块号）") + "\n")
    return (1, 0) if ok else (0, 1)
```

`main()` 里：

```python
js_pass, js_fail = run_js_gate()
total_pass, total_fail = js_pass, js_fail
...
if js_fail:
    failed_files.append("_check_static_js.js")
```

⚠️ **JS 闸的输出不要经过下面那段"从『通过 X 项』抠数字"的循环**——
它打印的是人话（"块1 语法 OK"），不含那个格式，但为了不再多一个解析口子，
**在循环外面单独 print**，只把结果并进 `total_pass/total_fail`。

---

## 三、D：test_39 把起始状态钉死（10 分钟，一行）

### 3.1 先说清楚：这不是修 bug

实测（§〇 事实 3）：清空真实待办、加 4 条待办，test_39 **两种情况都是 191 项全绿**。
它的断言基本在验"提案返回的卡"和自己 `add_todo` 造进沙箱的数据，不读库里原有条数。

但**结构性风险是真的**：`_harness.sandbox()` 是把 `app/data` **复制**一份，不是空库
（它自己的 docstring 就写着"依赖起始状态的断言必须自己摆桌子"）。
现在不敏感 ≠ 以后改断言也不敏感。加一行把这个事实**钉死**：

```python
from _harness import Checker, make_client, sandbox, title, wipe_todos   # 加 wipe_todos

    with sandbox():
        wipe_todos()      # #56-D：起始状态钉成空 —— 沙箱是"复制真实数据"，不是空库。
                          # 现在它不依赖库里原有条数（2026-10-22 实测：清空后仍 191 绿），
                          # 但留着这句话，才能保证"以后有人加了依赖真实数据的断言"时
                          # 不会静默变成"看运气"。
```

⚠️ **加完必须重跑，确认仍是 191 项全绿**。如果红了，说明**真的有一条断言在读真实数据** ——
那是发现了一颗地雷，要把它改成自己 `add_todo` 造数据，**不是把 `wipe_todos()` 删掉**。

### 3.2 另外 41 个"开了沙箱没摆桌子"的文件怎么办

`grep` 出来 42 个测试文件开了 `sandbox()` 却一次没调 `seed_todos/wipe_todos/write_timetable`。
**不要一刀切全加**：里面大多数根本不碰待办/课表（测鉴权、测 400、测转义的），加了纯噪声。

处理规则，按这个顺序判：

1. 文件里出现 `list_todos(` / `/api/todos` / `list_todos_in_month` / `get_timetable` 的 → **要看**
2. 断言里出现 `len(...)` / `[0]` / `not in` 且对象来自上面那些读接口的 → **必须加**
3. 只测接口状态码、错误消息、静态文件内容的 → **不加**

`test_39` 属于第 1 类（它有 `list_todos`），但实测不敏感，所以只加 `wipe_todos()` 钉住即可。

---

## 四、C：挂账 #69（时钟耦合）整族排查

### 4.1 它是什么

测试里写死了日期、或依赖"现在几点"：

- 写死**将来**的日期（`FUTURE = "2026-12-31"`）⇒ 过了那天，"将来"变成"过去"，断言红
- 依赖**当前时刻**（候选时段要"今天还没过的时间"）⇒ 上午跑绿、晚上跑红

症状是最难查的那种：**同一份代码，昨天绿今天红，你根本没改东西。**

已经修过的先例（注释里明写"#69 那一族"，照它们的写法改）：

| 文件 | 位置 | 修法 |
|---|---|---|
| `test_43_live_batch_findings.py` | `:198` | 改成 `today + N` 现算 |
| `test_61_surface_boundary.py` | `:30` | `FUTURE = "2026-12-31"` —— 这是**反例形态**，要盯 |
| `test_68_demo_polish.py` | `:150` | 现算 |
| `test_71_material_batch.py` | `:28` | `today + N` |
| `test_74_probe_and_material.py` | `:15` | **"日期一律 `today + N` 现算，绝不写死"** —— 这是标准写法 |

### 4.2 **不要**看到日期就改：三种性质要分清

AST 扫出来 42 个测试文件的代码里有日期字面量（已排除注释与 docstring）。但只有第三种有害：

| 类型 | 例子 | 处理 |
|---|---|---|
| ① 当**输入**喂给解析器 | `parse_time_spec("2026-10-03")`、`add_todo(..., "2026-10-03", ...)` | **不用改**，今天几号结果都一样 |
| ② **故意写远未来** | `test_39` 里的 `2099-01-01`、`2099-01-04` | **不用改**，而且这是正确做法（永远不过期） |
| ③ 当**将来**用 | `FUTURE = "2026-12-31"`、`next_week = "2026-11-02"` | **必须改成** `today + N` |
| ④ 依赖**当前时刻** | "候选要落在今天还没过的时间" | **必须改成**从 `clock.now()` 推一个宽松窗口 |

### 4.3 先扫一遍，再动手（别一个个文件翻）

```python
"""找出测试里"声明成常量"的日期字面量（#69 的候选，① / ② 会被一起列出来，人工分）。"""
import ast, glob, os, re
DATE = re.compile(r"20\d\d-\d\d-\d\d")

for p in sorted(glob.glob("tests/test_*.py")):
    src = open(p, encoding="utf-8").read()
    tree = ast.parse(src)
    # 只看**赋值**（模块级常量、函数内变量），不看单独成句的 docstring
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        val = getattr(node, "value", None)
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        if not DATE.search(val.value):
            continue
        names = [t.id for t in getattr(node, "targets", [getattr(node, "target", None)])
                 if isinstance(t, ast.Name)]
        print(f"  {os.path.basename(p)}:{node.lineno}  {names} = {val.value!r}")
```

跑出来之后按 §4.2 分类。**变量名里带 `FUTURE / NEXT / UPCOMING / TARGET / DEADLINE` 的优先看**
——那几乎一定是"当将来用"。

**判据一句话**：这个日期如果明天跑会怎样？
- 结论一样 → ① 或 ②，放过
- 结论变了 → ③ 或 ④，必须改

### 4.4 改法模板

```python
# ❌ 坏的
FUTURE = "2026-12-31"
c.check("跨年后的日程还在", has_todo(FUTURE))

# ✅ 好的（现算，永远在将来）
def future(days=30):
    return (clock.today() + datetime.timedelta(days=days)).isoformat()

# ✅ 也好的（确实需要一个"极远的将来"做边界，写 2099）
FAR_FUTURE = "2099-12-31"   # 边界值，永远不会过期；不要写 2026-12-31
```

---

## 五、验证清单

### 5.1 变异（改坏必须红）

| 编号 | 改坏什么 | 期望 |
|---|---|---|
| M1 | `clock.now()` 改成 `datetime.datetime.now()`（不带 `_TZ`） | §1.5 那条"换时区幅度"用例必须红 —— 证明它真的在读 `_TZ` |
| M2 | `_check_static_js.js` 恢复写死 2 个文件 | 「station.html 有语法错时要报出来」用例必须红 |
| M3 | `run_all.py` 里 JS 闸失败不计入 `total_fail` | 「造一个 JS 语法错，总账必须显示失败」用例必须红 |
| M4 | 删掉 `wipe_todos()` 并往真实数据塞一条撞日期的待办 | 「test_39 在脏起始数据下仍绿」用例必须红 |

### 5.2 真机

1. `cd /d/CampusTime && ./.venv/Scripts/python.exe tests/run_all.py`
   → 输出里**第一块就是"▶ JS 语法闸"**，且总账里那一项算 1 项通过
2. **故意把 `station.html` 里某个引号删掉** → 重跑 → **必须红，并指出是哪个文件第几块**
3. 把那引号补回去 → 重跑 → 全绿
4. 没装 node 的机器上跑（或临时把 PATH 里的 node 拿掉）→ 打印"⚠️ 没找到 node，跳过"，**总账仍然出得来、不算失败**
5. §1.5 的时区幅度验证（临时把 `_TZ` 改成 `-5`，`clock.now()` 要差 13 小时）
6. 学生端问「今天几号」→ 与系统日期一致，星期几对
7. 学生端问「明天有什么安排」→ 日期是明天（不是后天）
8. `test_39` 加完 `wipe_todos()` 后重跑 → 仍 **191 项全绿**

### 5.3 回归（这几条以前修过，本单不许弄坏）

- `store._now()` 仍然是落盘时间的唯一出口（别的地方不许直接 `clock.now_str()`）
- `_now_block()` 的输出格式不变：`2026-10-22 周四 20:25`（改了模型那边对不上）
- `main.py:4407` 的 `now() - t` 不许 TypeError（`t` 是 naive，`clock.now()` 也必须是 naive）
- `express.py:62` 的 `today() - arrived`（两个 date 相减）同理
- 全量跑完 42 个文件，红的数量**不比改之前多**（改之前先记一个基线！）

---

## 六、不做的事

1. **不加外部时间 API**（用户 20:07 提过）。实测国内只有苏宁那家能用（0.4 秒），
   而它治不了真正的病（时区），还会让"模型看到的时间"和"记录写的时间"变成两个钟。
   要加固就做"启动时校准一次 → 缓存偏移量"，仍然只有一个钟，且不在请求链路里。
2. **不把 `time.time()` 那 6 处换掉**：epoch 秒与时区无关，改了只会多出错点。
3. **不批量重写 42 个文件的日期**：① ② 类改了没有任何收益，还会把"故意写 2099"这种
   正确写法改坏。只改 ③ ④ 两类，并且改完必须跑一遍印证"红的不比之前多"。
4. **不给 JS 闸加"必须装 node"的硬依赖**：评委机器上可能只有 Python。
