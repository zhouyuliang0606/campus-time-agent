# 2026-10-08 中文 Windows 复现坑：pip 装不上依赖 + 自测跑不起来

## 用户 Prompt（摘录）

> D:\CampusTime 我搬好了，这是地址，你看看还缺啥

> 后续开发每次改动要进行细粒度 git 提交，以及写 prompt 快照 / GitHub、Gitee 代码仓库
> （须 Public 或提供评审权限），保留完整、细粒度 Git 提交历史（禁止一次性提交），
> 提交 AI 对话历史 Prompt 快照；附 README 复现指南；另提交 5—8 分钟演示视频
> （MP4，1080p，须含 AI 协同过程复现展示）及技术文档（PDF，不超过 30 页）——这是比赛要求

> 开发者指南只是给你看的，让你接手这个项目的，你去调

## AI 研判与决策

搬完目录后要做的第一件事不是加功能，是**确认「复现指南」这条硬交付物真的能在别人机器上跑通**。
重建 venv 时当场撞上第一条，跑自测时撞上第二条，两条是**同一个根因**：

**代码里的中文没问题，出问题的是"读文件/写终端"这两条路按系统码页走。**

1. `pip install -r requirements.txt` 直接崩。pip 读依赖清单用的是
   `pip._internal.utils.encoding.auto_decode()`，它的顺序是「先认 BOM → 再看 PEP263 声明 →
   都没有就按 `locale.getpreferredencoding()` 解」。本文件没有 BOM、中文注释也不含
   `coding:` 声明，于是中文 Windows 上按 cp936 解 → `UnicodeDecodeError: 'gbk' codec can't
   decode byte 0xab in position 6`。**评委照着 README 第 2 节复现，第一步就装不上依赖**——
   这比缺一个演示视频更糟（视频是加分项，装不上是复现失败）。
2. `python tests/run_all.py` 在第一行 `print("▶ ...")` 就抛
   `UnicodeEncodeError: 'gbk' codec can't encode character '\u25b6'`。
   症状极具迷惑性：**看着像这套自测坏了，其实只是终端打印坏了**。

**为什么不那么修**：

- ❌ 把中文注释翻译成英文。能绕，但项目通篇注释是中文（README 第 204 行还特意说明"每行依赖上面
  都写了中文注释"），这是给人读的交付物的一部分；为了一个 3 字节的 BOM 抹掉它不值。
- ❌ 只在 README 写一句"请先 `set PYTHONUTF8=1`"。把环境要求推给评委，等于没修。
- ❌ 每个测试文件里各抄一份 `sys.stdout.reconfigure(...)`。仓库自己在 2026-10-07 就吃过这个亏
  （"话术函数只有一个，别抄第二份"），所以收成一份 `tests/_utf8.py`。
- ✅ BOM + 输出流固定，两处都只加"读/写口"上的约束，**一个业务逻辑字节都不动**。

另外顺手核实了两件事（未动，只记录）：

- `run_local.bat` 仍写 8080（README 与接手指南都是 8000），但接手指南第 105 行明确说它
  属"未跟踪的临时文件，别误提交"→ **按纪律不动它、不提交**，留给用户处置。
- README 第 238 行"预期总账：失败 0 项"与现状（3 项存量红）不符，属活文档数字问题，
  本轮不混进来改（一个缺口一个 commit）。

## 具体改动

| 文件 | 改了什么 |
|---|---|
| `requirements.txt` | 文件头补 UTF-8 BOM（3 字节）；**内容与行数一字未动** |
| `tests/_utf8.py` | **新增**：`force_utf8_streams()`，导入即生效；`errors="replace"`，失败也不影响测试逻辑 |
| `tests/run_all.py` | `import _utf8`；新增 `CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}` 并传给 `subprocess.run(env=...)`（每个 `test_*.py` 是独立进程，光固定父进程管不到它们） |
| `tests/_harness.py` | `import _utf8`（覆盖 31 个测试文件里的 28 个） |
| `tests/test_24_confirm_render.py`、`tests/test_25_todos_panel_and_nav.py` | 自包含、不走 harness → 各补一行 `import _utf8` |
| `tests/test_30_requirements_bom.py` | **新增**护栏，26 项 / 5 批（A 字节层 / B pip 真实解码路径 / C 内容对账 / D 文档留原因 / E 打印守得住） |
| `README.md` | 第 2 节后补一段「⚠️ 请存成带 BOM 的 UTF-8」，写清为什么、以及谁来钉住它 |

## 验证结果

- **反向验证一（拆 BOM）**：`A1 / A2 / B1 / B2` 变红，且 B1 报的就是原故障
  `decoding with 'cp936' codec failed`；装回 BOM → 26 项全过、0 失败。
- **反向验证二（拆 run_all 的两道守卫）**：`E2 / E3` 变红；装回 → 反转。
- **B 批不是自说自话**：它是在子进程里调用 **pip 自己的** `auto_decode()`，而不是我们照抄一份解码逻辑。
- **真实命令验收**（不设任何环境变量）：`python tests/run_all.py` →
  **通过 1851 项 / 失败 3 项（31 批）**。1851 = 上一轮 1825 + 本批 26，分毫不差；
  3 项红为存量（`test_06` 两项 + `test_23` 一项），已用**旧目录 + 旧 venv + Python 3.13.14 A/B 重跑，
  红项逐条一致**证明与本轮无关（也与《开发者接手指南》第 111 行写的基线一致）。
- **单文件独立跑**（接手指南第 36 行推荐的那种跑法）：`test_24` 5/0、`test_25` 11/0、`test_29` 22/0，
  全部在 GBK 控制台上打得出来。

## 本批提交（细粒度，按纪律拆开）

| 哈希 | 内容 |
|---|---|
| `d9f385d` | 修复：requirements.txt 存为带 BOM 的 UTF-8 |
| `d45852b` | 修复：自测在中文 Windows 控制台上跑不起来（新增 `tests/_utf8.py` 等 5 文件） |
| `dd69964` | 测试：test_30 钉住两处编码守卫（26 项 / 5 批，含反向验证） |
| `dc320de` | 文档：README 补「中文 Windows 编码」说明 |
| 本文件 | 协同快照（单独提交，不与代码混） |

## 附：本轮同时完成的仓库搬迁核查（非代码改动）

- `D:\Workbuddy\CampusTime` → `D:\CampusTime` 为 **`git clone`**，不是复制：代码 335 提交、
  HEAD、`git status` 全部无损；但 gitignore 掉的运行数据（样本库 `intent_samples.json`、
  学生对话史、上传原件、用量流水）和 `tests/_shots/` 全缺，已逐项补齐并与旧目录**逐字节对账一致**。
- `origin` 被 clone 设成了旧目录本地路径，已改回 GitHub 地址。
- 旧目录在搬迁后又产生了一个提交 `da74b9f`（《开发者接手指南》），已 `merge --ff-only` 并齐，
  现两边同为 336 提交——**不留两个活目录**，否则评审要看的提交历史会分叉。
- 推送未做（PAT 文件 0 字节 + 代理未开），按用户指示留到最后。
