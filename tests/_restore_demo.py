"""把演示数据还原回"干净的一套"（12 门课）。

为什么要有这个脚本：app/data/student/timetable.json 是**被 gitignore 的运行时数据**，
端到端脚本、手点演示都会真的改它（删一门课、加一门课），跑几轮就脏了，
脏了以后 test_05 第八批会因为"周一高等数学已经不存在"直接炸。

还原的源头是 app/data/courses.json（演示课表的唯一真源），
不是硬编码一张表——源头改了这里不用跟着改。

用法（在项目根目录）：
    .venv/Scripts/python tests/_restore_demo.py
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _e2e_env import REAL_DATA_DIR, demo_courses_from_json  # noqa: E402


def main():
    cs = demo_courses_from_json()
    p = os.path.join(REAL_DATA_DIR, "student", "timetable.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"courses": cs}, f, ensure_ascii=False, indent=2)
    print(f"✅ 演示周表已还原：{len(cs)} 门课 -> {p}")
    for c in cs:
        print(f"   周{c['day']} {c['start']} {c['course']} @ {c['location']}")

    tp = os.path.join(REAL_DATA_DIR, "student", "todos.json")
    try:
        n = len(json.load(open(tp, encoding="utf-8")).get("todos", []))
    except Exception:
        n = "?"
    print(f"   待办保持原样：{n} 条（这一份不还原，手加的安排留着更真实）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
