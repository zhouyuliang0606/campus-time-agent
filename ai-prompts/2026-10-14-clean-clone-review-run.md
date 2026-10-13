# 2026-10-14 · 以评委身份跑一遍：干净克隆 + 独立新 venv，13 条红的账算清了

## 一、用户原话

> 我的意思是新开一个 venv 去测试，按照评委的角度，克隆完代码去运行

上一轮我只在自己的 dev venv 里跑，那次算"改完之后没坏"，不算"别人拿得到跑得通"。这一轮照评委的路径重做一遍。

## 二、按 README 的顺序一步步走（每一步都记了实际输出）

```bash
git clone D:/CampusTime  →  D:\campustime-review-clone\CampusTime      # HEAD = 1c6b943
python -m venv .venv                                                     # Python 3.13.0（与开发同一代）
.venv\Scripts\python.exe -m pip install -r requirements.txt              # 27 个包装上，无报错
.venv\Scripts\python.exe tests/run_all.py                                # 44 批
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt && pytest
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8010             # 起服务 + 打一句对话
```

装依赖这一步在**中文 Windows 上没有崩**（`requirements.txt` 带 BOM 那 3 个字节还在，
克隆出来也还在）—— 这条本来就是 test_30 存在的理由，先记一句"仍然成立"。

起服务也通：`/student` 200、`/api/chat`「我每天都要背单词」出 `todo_slots / repeat=daily`，
候选段照常摊开。同时看到克隆里 **待办 0 条、课表 0 门**（下面第三节的全部根因）。

## 三、克隆跑出来的 13 条红，全部指向同一个原因

```
总账：通过 3083 项，失败 13 项（共 44 批）      ← 修 test_30 之前是 3080/15
有失败的文件：test_06、test_20_polish、test_23、test_31、test_40
```

按文件数下来是 `test_06×5 + test_20×3 + test_23×2 + test_31×1 + test_40×2 = 13`，
每一条的现场消息都带着同一句话的形状：

| 现场 | 说明 |
| --- | --- |
| 「你现在周表还是空的。先去『我的日程』上传一下课表…」 | 断言指望有周表好摊候选，克隆里周表是空的 |
| 「📋 你目前还没有任何待办。」/ 库 0 条 | 断言指望库里有待办可列 |
| `C7 沙箱里没有课程数据，撞课这一项没法验（如实报红而不是假装通过）` | **这条是当年特意留的诚实红** —— 它不许在没数据时装绿 |
| 「环境里找不到一个 19:00-21:00 被占的日子（②无法验）」 | 撞档用例需要一节被占的晚上，克隆里没有 |

根因一句话：**`app/data/student/**`（课表 + 待办）没进仓库**，而这几批测试**读的是真实演示数据**。
`.gitignore` 之外没别的机关 —— 评委 `git clone` 出来就是空库，本机却全绿（除了这 13 条）。

`git archive HEAD` 那次估算的"克隆态 15 红"是对的，但当时没拆开看，
把两类不同的红混成了一类（见第四节）。

## 四、其中 2 条根本不是数据问题，是我自己的护栏在摸 pip 的私有模块

```
❌ B1 pip 的 auto_decode 不再抛异常  ModuleNotFoundError: No module named 'pip._internal.utils.encoding'
❌ B2 BOM 被认出来并剥掉…            'ModuleNotFou'
```

test_30 的 B 批当年写的是 `from pip._internal.utils.encoding import auto_decode` —— 私有模块。
pip 26 把它挪走了（BOM 表现在在 `pip._internal.req.req_file.BOMS`），
所以**任何装了较新 pip 的机器上这两条必红**，和产品代码一点关系没有。
dev venv 是 Python 3.13.0 自带的 pip 24.2，所以本机一直绿 —— 典型"我只在自己的环境里验过"。

改成走 pip 的**公共 CLI**：`pip install --dry-run --no-index --no-deps -r requirements.txt`
（不联网、不装包，实测 1.2 秒），走的正是当初崩掉的那段"读文件 → 解码 → 解析"。

顺带把两条版本事实量出来（同一份内容、只差 BOM 那 3 个字节，`PYTHONUTF8=0` 模拟中文 Windows 默认态）：

```
pip 24.2  读带 BOM   → rc=0
pip 24.2  读无 BOM   → rc=2  UnicodeDecodeError: 'gbk' codec ...  decoding with 'cp936' codec failed
pip 26.2.1  读带 BOM   → rc=0
pip 26.2.1  读无 BOM   → rc=0     ← 新版改成先按 UTF-8 试，不崩了
```

所以 README 原来那句"没 BOM 一定装不上"在 pip 26 上是**过强的话**。
BOM 仍然留着（兼容旧 pip，而新装 Python 附带的就是旧的那一代；对新版零副作用），
但文档改成写明版本边界，并新增 **B4**：先看这台机器的 pip 到底崩不崩，
再要求 README 里对应那句实话必须在 —— **文档与事实不许互相打脸**。

变异（`mutate_Z*.py`，逐笔字节还原一致）：

| 变异 | 咬回 |
| --- | --- |
| Z1 把 BOM 摘掉 | A1/A2 红 + **B1/B2 红**（本机 pip 24.2 真崩在 cp936 上） |
| Z2 中文注释改英文 | A4 红 |
| Z4 抽掉清单最后一项 | B2/C1/C2 红（"真读到底"这条有牙） |
| Z5 把 CLI 参数写错（`--dryrun`） | B1/B2 红 —— 专验"门自己没通电时会不会假装绿" |
| Z6 擦掉 README 整段 BOM 说明 | B4 + D1 红 |
| Z3（只删警告块第一句） | **红 0 —— 是我这一笔变异太弱**（后面段落里"BOM/码页"还在，断言合理没红），补 Z6 才有牙 |

## 五、两侧数字（写构成，不写光秃秃的数）

| 环境 | 结果 | 红的构成 |
| --- | --- | --- |
| 干净克隆 + 新 venv（pip 26.2.1） | `3083 通过 / 13 失败` | test_06×5、test_20×3、test_23×2、test_31×1、test_40×2 —— **全是"没有演示数据"一条因** |
| 本机 dev venv（pip 24.2） | `3081 通过 / 13 失败` | test_06×9、test_23×2、test_40×2（他此刻正在自己机器上测，红数随真实数据漂） |

同一份代码、两处红数不一样但**条数巧合都是 13** —— 这就是为什么"报数必须同时报哪几个文件、各几条"。

## 六、这一轮没动的（等一个决定）

13 条数据红要不要清、怎么清 —— 两条路（#42 记着）：

1. **让这几批测试自己在沙箱里种数据**（我倾向这条）：红线批（`test_31` 的 C7 撞课）本来就不该依赖某个人的真实课表，
   种一份"最小可验"的 fixture 反而更可控；代价是要挨个改这几批的前置。
2. 把演示课表/待办入库：一条命令就绿，但这仓库要公开交比赛，真实课表含学院/教室/节次，
   而且 `demo_timetable/` 当初定的就是永不提交。

在这条没定之前，README 里「预期总账：失败 0 项」那句仍然是**假话**（它只在我这台机器上近似成立）。
`pytest` 那道门也就照样是红的 —— 它如实转述 run_all，不替任何人粉饰。

## 七、commit

`fd9fa00`（test_30 改走 pip 公共 CLI + README 版本边界 + B4）+ 本快照。
上一轮相关：`ef79372`（pytest 门）、`1c6b943`（那一份快照）。
