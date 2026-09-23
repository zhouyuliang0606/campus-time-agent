"""测试用的小工具箱（人话：三个测试文件都要用的"计数器和还原器"）。

这里只有三样东西：
1. Checker —— 数一数这一步过了没，最后打印「通过 X 项，失败 Y 项」；
2. isolate  —— 测试会往 app/data/ 里写东西（比如假密钥、临时上传），
               跑完必须还原，绝不能把测试痕迹留在演示环境里；
3. make_client —— 造一个能直接调接口的"假浏览器"，不用真的启服务器。

为什么不用 pytest？演示项目希望评委在没装任何额外包的情况下也能跑：
只需要项目虚拟环境里的 fastapi，别的什么都不装。
"""
import os
import shutil
import sys

# 项目根目录 = tests/ 的上一级
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TESTS_DIR)
DATA_DIR = os.path.join(PROJ_DIR, "app", "data")

# 让测试文件能 import app.xxx（相当于把项目根目录加到"找模块的目录清单"里）
if PROJ_DIR not in sys.path:
    sys.path.insert(0, PROJ_DIR)


class Checker:
    """人话：一个打分员。check(这一项名字, 过了没)，最后自己汇总。"""

    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, cond, extra=""):
        """记一条结果。cond 为真算通过，为假算失败。"""
        if cond:
            self.passed += 1
            print(f"  ✅ {name} {extra}")
        else:
            self.failed += 1
            print(f"  ❌ {name} {extra}")
        return bool(cond)

    def summary(self, title=""):
        """打印汇总，并返回要给操作系统的退出码（0=全过，1=有失败）。"""
        head = f"\n===== {title}结果：通过 {self.passed} 项，失败 {self.failed} 项 ====="
        print(head)
        return 1 if self.failed else 0


def data_path(*parts):
    """拼出 app/data/ 下的绝对路径，比如 data_path('kb.json')。"""
    return os.path.join(DATA_DIR, *parts)


class isolate:
    """数据隔离（人话：先拍照，跑完冲印回去）。

    用法：
        with isolate("kb.json", "persona.json", "student"):
            ... 随便改 ...
    退出 with 时自动还原：原来有文件的还原内容，原来没有的删掉，
    这样测试既不会污染种子数据，也不会留下垃圾文件。
    """

    def __init__(self, *names):
        self.targets = [data_path(n) for n in names]
        # 备份放到系统临时目录旁边，避免污染项目目录
        self.bak_dir = os.path.join(TESTS_DIR, ".bak")
        self.backups = []  # [(原路径, 备份路径, 原来是否存在)]

    def __enter__(self):
        os.makedirs(self.bak_dir, exist_ok=True)
        for i, src in enumerate(self.targets):
            existed = os.path.exists(src)
            bak = os.path.join(self.bak_dir, f"{i}.bak")
            if existed:
                if os.path.isdir(src):
                    shutil.copytree(src, bak, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, bak)
            self.backups.append((src, bak, existed))
        return self

    def __exit__(self, *exc):
        for src, bak, existed in self.backups:
            if os.path.isdir(src):
                shutil.rmtree(src, ignore_errors=True)
            elif os.path.exists(src):
                os.remove(src)
            if existed:
                if os.path.isdir(bak):
                    shutil.copytree(bak, src, dirs_exist_ok=True)
                else:
                    shutil.copy2(bak, src)
                if os.path.isdir(bak):
                    shutil.rmtree(bak, ignore_errors=True)
                else:
                    os.remove(bak)
        # 备份目录空了就删掉
        if os.path.isdir(self.bak_dir) and not os.listdir(self.bak_dir):
            shutil.rmtree(self.bak_dir, ignore_errors=True)
        print("\n[测试数据已还原，演示环境没被污染]")
        return False  # 不吞掉异常


def make_client():
    """造一个"假浏览器"（人话：不用真的 uvicorn 启服务，也能调接口试）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def title(text):
    """打印分节标题，让输出像一份可读的检查清单。"""
    print(f"\n=== {text} ===")
