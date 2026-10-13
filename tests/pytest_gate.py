"""pytest 的那道门：把 `pytest` 接到项目真正的自检入口上。

装完 pytest 的人第一反应是敲 `pytest`，所以这条命令必须给得出真话。
但它**不能**把那 44 份 `test_*.py` 当 pytest 用例来收，两个原因都是实测的：

1. 那些文件是**自带 main 的脚本**，其中三份（01 骨架 / 02 上传 / 03 学生日程）
   在模块层就 `sys.exit(...)` —— pytest 一 import 就 `INTERNALERROR: SystemExit`，
   现象是「整条命令崩掉、一行结果都没有」，比测试失败难看得多。
2. 就算加了守卫也不该同进程连跑：数据目录是**程序启动那一刻**定下来的，
   一个进程里跑几十批会互相串味（`run_all.py` 逐批开子进程正是为这件事，见它开头那段注释）。

所以这里只开一个子进程跑 `tests/run_all.py`，把总账那一行搬进断言消息。
配套见 `pytest.ini` 的 `python_files`：只收这一份，其余一份都不 import。

文件名**故意不带 `test_` 前缀** —— `run_all.py` 是按 `test_*.py` 抓文件的，
顶着前缀写就变成「门跑 run_all、run_all 又跑到门」的无限套娃。

⚠️ 单独点名文件仍然会崩（`pytest tests/test_01_basic.py` → 还是 INTERNALERROR）：
显式路径会绕过 `pytest.ini` 的 `python_files`。要看单批结果请用 `python tests/test_XX_*.py`。

历史一笔（2026-10-14 已清）：这条门曾经**一直红着** —— 本机全量 14 条存量失败
（`test_06`×10 + `test_23`×2 + `test_40`×2），根因是 `sandbox()` 复制真实演示数据，
断言读的是"谁的库里恰好有几条、周表上恰好有什么课"。修法是**让依赖起始状态的批次
在沙箱里自种一份已知数据**（`seed_todos` / `wipe_todos` / `write_timetable`），
不是把演示数据 commit 进仓库、也不是一刀切清空。现在干净克隆（连 `app/data/student/`
都没有）与开发机同为**失败 0 项** —— 它一红，就说明有人新写了条依赖起始状态却没自种的断言。
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join("tests", "run_all.py")
# 一整套跑下来是分钟级的，给足余量；真卡住了也要能看出是超时而不是断言挂
TIMEOUT_SECONDS = 1800


def test_run_all_suite_is_green():
    """`pytest` 等价于 `python tests/run_all.py`：退出码 0 才算过，总账原样贴出来。"""
    proc = subprocess.run(
        [sys.executable, ENTRY],
        cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=TIMEOUT_SECONDS)
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    total = next((ln for ln in reversed(lines) if "通过" in ln and "失败" in ln),
                 "（没找到总账那一行——多半是跑崩了）")
    reds = [ln for ln in lines if ln.startswith("❌")][:12]
    detail = "\n".join(reds) if reds else "（没列出带 ❌ 的行）"
    assert proc.returncode == 0, f"{total}\n挂掉的（最多列 12 条）：\n{detail}"
