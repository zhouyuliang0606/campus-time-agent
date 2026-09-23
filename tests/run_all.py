"""一键跑全部测试（人话：评委/同学拿到项目后，只需要敲这一条命令）。

用法（在项目根目录执行）：

    python tests/run_all.py

它会依次跑 tests/ 下所有 test_*.py，把每一批的结果打印出来，
最后给一个总账：一共多少项、过了多少、挂了多少。
有挂的项时，退出码是 1（方便接到别的自动化里）。

为什么每个文件单独开一个子进程跑？
因为数据目录是在程序启动那一刻定下来的，放同一个进程里会互相串味。
各跑各的，谁也不影响谁。
"""
import glob
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TESTS_DIR)


def main():
    # 按文件名排序，保证每次跑的顺序一致（01 骨架 -> 02 上传 -> 03 日程）
    files = sorted(glob.glob(os.path.join(TESTS_DIR, "test_*.py")))
    if not files:
        print("没找到任何 test_*.py")
        return 1

    print(f"准备跑 {len(files)} 批测试（工作目录：{PROJ_DIR}）\n")
    total_pass, total_fail = 0, 0
    failed_files = []

    for path in files:
        name = os.path.basename(path)
        print("=" * 60)
        print(f"▶ {name}")
        print("=" * 60)

        # 每个文件一个子进程，把输出原样打出来（这样失败时能一眼看到是哪一步）
        proc = subprocess.run(
            [sys.executable, path],
            cwd=PROJ_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out.rstrip())

        # 从"通过 X 项，失败 Y 项"这行汇总里抠出数字
        passed = failed = 0
        for line in out.splitlines():
            if "通过" in line and "失败" in line:
                try:
                    seg = line.split("通过")[1]
                    passed = int(seg.split("项")[0].strip())
                    failed = int(seg.split("失败")[1].split("项")[0].strip())
                except (IndexError, ValueError):
                    pass

        total_pass += passed
        total_fail += failed
        if failed or proc.returncode != 0:
            failed_files.append(name)

    print("\n" + "=" * 60)
    print(f"总账：通过 {total_pass} 项，失败 {total_fail} 项（共 {len(files)} 批）")
    if failed_files:
        print("有失败的文件：" + "、".join(failed_files))
        print("提示：往上看带 ❌ 的那一行，就是具体挂掉的步骤。")
    else:
        print("全部通过 ✅")
    print("=" * 60)
    return 1 if (total_fail or failed_files) else 0


if __name__ == "__main__":
    sys.exit(main())
