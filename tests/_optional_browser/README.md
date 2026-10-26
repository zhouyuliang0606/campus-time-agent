# tests/_optional_browser/ —— 可选的真浏览器端到端 / 截图测试

## 这是什么

这一层是**可选的**端到端 / 截图证据脚本，全部依赖 [Playwright](https://playwright.dev/)
（真开一个 Chromium 浏览器去点页面、截证据图）。它们**不属于** `tests/run_all.py`
主自检套件——主套件零额外安装就能跑，评委不装 Playwright 也跑得到。

2026-10-12 把它们从 `tests/` 根目录挪进这里，并把每个脚本顶部加了
「缺 Playwright 就打印一句提示并 `exit(0)` 优雅跳过」的保护块，目的是：

* **让"评委零额外安装即可跑测试"的声明真实成立。** 主套件（`test_*.py`）只依赖
  `fastapi` 的 `TestClient`（相当于"假浏览器"），`run_all.py` 只 glob 根目录的
  `test_*.py`，这些以下划线开头的脚本既不在 glob 里、也更不在主套件进程里被 import。
* **避免仓库里躺着需要额外装包的脚本误导评委**：以前这些脚本就在 `tests/` 根，
  文档笼统说"零安装"，评委误跑会报 `ModuleNotFoundError: playwright`。现在物理隔离 +
  优雅跳过，误跑也只会看到一句提示、退出码 0。

## 依赖（仅这一层需要）

```bash
pip install playwright && python -m playwright install chromium
```

> 项目根 `requirements.txt` **不包含** Playwright——它只列运行时与接口测试必需的包。
> 这样主套件在干净环境里 `pip install -r requirements.txt` 后直接 `python tests/run_all.py`
> 就能全绿，不会被 Playwright 拽进来。

## 怎么跑

在**项目根目录**下，用仓库相对路径调起（因为脚本里用 `..` 解析项目根）：

```bash
python tests/_optional_browser/_e2e_add.py        # 端到端：加待办/加课 → 点确认 → 看得到
python tests/_optional_browser/_e2e_clear.py      # 端到端：清空课表
python tests/_optional_browser/_e2e_confirm.py    # 端到端：删课落库
python tests/_optional_browser/_e2e_clearchat.py  # 端到端：清理聊天记录

python tests/_optional_browser/_shot_confirm.py   # 截图：确认条长什么样
python tests/_optional_browser/_shot_span.py      # 截图：改完自动刷新日程
# ……其余 _shot_*.py 同理，详见 tests/README.md 的「端到端 / 截图」段落
```

每个脚本自己起服务器、自己备数据（用 `tests/_e2e_env.py` 把 `app/data` 复制到临时目录，
起一台只认副本的 uvicorn，测完连服务器带目录一起删），**不会动到演示数据**。
没装 Playwright 时它们会打印提示并以退出码 0 跳过。

## 文件清单

### 端到端（真开浏览器点按钮，`_e2e_*`）
| 文件 | 干什么 |
| --- | --- |
| `_e2e_add.py` | 加待办 / 加课：出卡 → 点确认 → 日程当场看得到 → 刷新不丢 |
| `_e2e_clear.py` | 清空课表：提案 → 确认条 → 确认 / 取消 → 课表真的空了 |
| `_e2e_confirm.py` | 删课：一句「确认」就由系统落库；确认条贴在聊天流里（不弹全屏遮罩） |
| `_e2e_clearchat.py` | 清理聊天记录：点 🧹 → 弹窗 → 取消无事 / 确认清空 → 刷新不再回来 |

### 截图证据（`_shot_*`，跑完在 `tests/_shots/` 留图）
| 文件 | 干什么 |
| --- | --- |
| `_shot_confirm.py` | 确认条外观：贴在消息下、底下一行【取消】【确认】 |
| `_shot_swim.py` | 「周二下午两点到三点游泳」→ 出确认条 → 点确认 → 待办里真有 |
| `_shot_gym.py` | 「没有确认按钮」那一幕：文字方案 → 回「可以」→ 确认条补挂 → 写完翻到下周 |
| `_shot_deltodo.py` | 删待办三步走：列清单问哪条 / 原样念一遍 / 点删只删那条 |
| `_shot_proactive.py` | 「没有确认、没帮我想时间」那一幕：二选一卡 → 摊候选 → 自报点 → 写完 |
| `_shot_slots.py` | 「由 ai 列举、由我打勾」那一幕：列 3 段空档 + 其他自填 → 两条一起落库 |
| `_shot_mode.py` | 规格沿革（第四版）：直接敲定单条确认条、带"为什么排这儿" |
| `_shot_popup.py` | 「没有收到弹窗」那一幕：逐字复现报障 → 补挂卡 → 点完写完 |
| `_shot_prosepopup.py` | 「没有代办页…没弹窗」那一幕：补条 → 周表/月表当场可见 |
| `_shot_inherit.py` | 「刚说过的事名没继承」那一幕：上下文继承事名、直接敲定 |
| `_shot_notsee.py` | 「没看见日程里有」那一幕：诚实承认未写 / 指到正确位置 / 不提管理端 |
| `_shot_where.py` | 「看不见 X」分支误伤问：不当成报障、不挡该接的加待办 |
| `_shot_span.py` | 「每次都不弹确认框」那一幕：补条 → 周表/月表当场可见 |
| `_shot_classify.py` | 意图分类「真模型 vs 关键词兜底」对照：同一句话分流结果截图 |
| `_shot_trace.py` | 「我是这么想的」trace 面板：判定/出卡卡在哪一档逐档讲清 |

## 注意

* 这些脚本走**真模型**的那几条（带 `⚠️ 走真模型` 标注的）需要配好大模型密钥；
  走**系统确定性链路**的那几条（如 `_shot_span.py`）一次都不问模型，没密钥也跑。
* `tests/_e2e_env.py` 是共享 harness，**不在本目录、留在 `tests/` 根**——主自检套件的
  `test_05` / `test_06` 也用它，且它只依赖 `fastapi`、不依赖 Playwright，所以主套件
  零额外安装照样用它。请勿把它挪进来，否则主套件会找不到它。
