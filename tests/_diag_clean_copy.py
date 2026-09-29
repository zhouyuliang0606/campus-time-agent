"""诊断脚本（**不是测试**，run_all.py 不会跑它）：证明 test_06 那批失败是**数据污染**。

为什么留着它：跑全量时会看到 test_06 有几项失败，看着像代码有问题。其实是
`app/data/student/todos.json` 里躺着几次"真机实测"留下的待办（当天日期），
而 test_06 里有按"最近几天"筛的断言——**演示数据脏了**，不是逻辑错。

用法（在项目根目录）：

    python tests/_diag_clean_copy.py

做法：把沙箱的"真实数据源"换成一份**清掉测试残留**的副本（只改副本里那份 json），
再跑一遍 test_06，对比失败数。副本用完即删，**真实 app/data 一个字节都不动**。
如果清完之后失败数明显下降，就说明差额全是被污染的那几项。
"""
import json
import os
import runpy
import shutil
import sys
import tempfile

TESTS = os.path.join(os.path.dirname(os.path.abspath(__file__)))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)
import _harness  # noqa: E402

tmp = tempfile.mkdtemp(prefix="clean-copy-")
dest = os.path.join(tmp, "data")
shutil.copytree(_harness.REAL_DATA_DIR, dest)

p = os.path.join(dest, "student", "todos.json")
raw = json.load(open(p, encoding="utf-8"))
todos = raw["todos"] if isinstance(raw, dict) and "todos" in raw else raw
kept = [t for t in todos if not (t.get("created_at") or "").startswith("2026-09-29")]
print(f"[诊断] 真实 {len(todos)} 条 → 副本保留 {len(kept)} 条，"
      f"丢掉 {[t.get('title') for t in todos if t not in kept]}")
if isinstance(raw, dict) and "todos" in raw:
    raw["todos"] = kept
else:
    raw = kept
with open(p, "w", encoding="utf-8") as f:
    json.dump(raw, f, ensure_ascii=False, indent=2)

_harness.REAL_DATA_DIR = dest
runpy.run_path(os.path.join(TESTS, "test_06_add_things.py"), run_name="__main__")
shutil.rmtree(tmp, ignore_errors=True)
