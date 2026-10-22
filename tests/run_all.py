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
import shutil
import subprocess
import sys

import _utf8  # noqa: F401  导入即生效：中文 Windows 的 GBK 控制台打不出 ✅/▶，会直接打断自测

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TESTS_DIR)

# 每个 test_*.py 是单独进程跑的，光固定父进程的流管不到它们 → 用环境变量传下去
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run_js_gate():
    """静态页内联 JS 的语法闸（#56-B）。返回 `(通过项, 失败项)`。

    为什么要有它：页面是 HTML，Python 只把它当字符串往外发。JS 里一个引号写错，
    后端几千项测试**全绿**，而学生打开页面是一片空白 —— 那是最坏的一种"测试通过"。

    为什么放在最前面：它 1 秒跑完，而后面几十批要几分钟。语法错了先看见，
    别让人等完几分钟才发现"页面根本打不开"。

    ⚠️ **找不到 node 就跳过并打印，算 0 项 0 失败，不许判失败**：
       评委机器上很可能只有 Python（这个项目一开始就定了"只依赖 venv 里已有的包"）。
       "因为缺一个可选工具就整批红"会让整条 run_all 失去可用性 —— 那不叫严格，叫没法用。
       但跳过**必须写在总账里**（第一行那句 ⚠️），没验到的东西要让人知道没验到。
    """
    js = os.path.join(TESTS_DIR, "_check_static_js.js")
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        print("⚠️ 没找到 node，跳过 JS 语法闸 —— 这一项这次**没有验**\n")
        return 0, 0
    print("=" * 60)
    print("▶ JS 语法闸（app/static/*.html 的内联 script + 外链 .js）")
    print("=" * 60)
    proc = subprocess.run([node, js], cwd=PROJ_DIR, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=CHILD_ENV)
    out = ((proc.stdout or "") + (proc.stderr or "")).rstrip()
    if out:
        print(out)
    ok = (proc.returncode == 0)
    print(("✅ JS 语法闸通过" if ok else "❌ JS 语法闸失败（上面有具体文件与块号）") + "\n")
    # 闸自己那份输出**不进**下面"从『通过 X 项』抠数字"的循环（它印的是人话，没有那个格式），
    # 只把结果并进总数 —— 少一个解析口子就少一处将来对不上数的地方。
    return (1, 0) if ok else (0, 1)


def main():
    # 按文件名排序，保证每次跑的顺序一致（01 骨架 -> 02 上传 -> 03 日程）
    files = sorted(glob.glob(os.path.join(TESTS_DIR, "test_*.py")))
    if not files:
        print("没找到任何 test_*.py")
        return 1

    print(f"准备跑 {len(files)} 批测试（工作目录：{PROJ_DIR}）\n")
    js_pass, js_fail = run_js_gate()
    total_pass, total_fail = js_pass, js_fail
    failed_files = []
    if js_fail:
        failed_files.append("_check_static_js.js")

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
            env=CHILD_ENV,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        print(out.rstrip())

        # 从"通过 X 项，失败 Y 项"这行汇总里抠出数字。
        # ⚠️ 要把**每一批**都加起来，不能只留最后一行：
        # 一个测试文件里往往有好几批（test_05 有十二批），
        # 早先这里是覆盖式的（passed = ...），于是"总账"只算了每个文件最后一批，
        # 数字比实际小一大截——看着像"就这么多测试"，其实是漏统计。
        passed = failed = 0
        for line in out.splitlines():
            if "通过" in line and "失败" in line:
                try:
                    seg = line.split("通过")[1]
                    passed += int(seg.split("项")[0].strip())
                    failed += int(seg.split("失败")[1].split("项")[0].strip())
                except (IndexError, ValueError):
                    pass

        total_pass += passed
        total_fail += failed
        if failed or proc.returncode != 0:
            failed_files.append(name)

    print("\n" + "=" * 60)
    # 「批」的口径：JS 语法闸验过就单独算一批（跳过就不计，别把没验的说成验过的）
    batches = len(files) + (1 if (js_pass or js_fail) else 0)
    print(f"总账：通过 {total_pass} 项，失败 {total_fail} 项（共 {batches} 批）")
    if failed_files:
        print("有失败的文件：" + "、".join(failed_files))
        print("提示：往上看带 ❌ 的那一行，就是具体挂掉的步骤。")
    else:
        print("全部通过 ✅")
    print("=" * 60)
    return 1 if (total_fail or failed_files) else 0


if __name__ == "__main__":
    sys.exit(main())
