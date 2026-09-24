"""测试用的小工具箱（人话：三个测试文件都要用的"打分员 + 沙箱"）。

这里只有三样东西：
1. Checker  —— 数一数这一步过了没，最后打印「通过 X 项，失败 Y 项」；
2. sandbox  —— 把 app/data 复制一份到临时目录，让测试只写那份副本。
               这是最关键的一件事：测试会保存假密钥、传临时文件，
               绝不能把痕迹留在你的演示环境里；
3. make_client —— 造一个"假浏览器"，不用真的启服务器也能调接口。

为什么不用 pytest？演示项目希望评委在没装任何额外包的情况下也能跑：
只需要项目虚拟环境里已有的 fastapi，别的什么都不装。
"""
import atexit
import os
import shutil
import sys
import tempfile
import warnings

# 关掉第三方库的过时警告，免得满屏噪声盖住真正的 ✅ / ❌ 结果。
# 注意 starlette 这条虽然叫 StarletteDeprecationWarning，实际继承自 UserWarning，
# 所以按类别过滤抓不到它，只能按消息内容过滤。
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

# 项目根目录 = tests/ 的上一级
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TESTS_DIR)
REAL_DATA_DIR = os.path.join(PROJ_DIR, "app", "data")

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
        print(f"\n===== {title}结果：通过 {self.passed} 项，失败 {self.failed} 项 =====")
        return 1 if self.failed else 0


class sandbox:
    """数据沙箱（人话：给测试单独准备一份"练习用的数据"）。

    做法：把真实的 app/data 整个复制到一个临时目录，
    然后告诉程序"以后数据都放这儿"（靠 CAMPUSTIME_DATA_DIR 这个环境变量）。
    测试再怎么折腾都只改那份副本，退出 with 时整个临时目录删掉。

    用法（不用刻意包住 import，但包着更省心）：
        with sandbox():
            from app.main import app
            ...

    app/store.py 现在每次读写都重新读一遍环境变量，所以一个进程里连开
    好几个 sandbox() 也各管各的，不会互相串味——早期版本是导入时定死，
    第二批之后所有写入都会落到真实演示数据上。
    """

    # 这几样是"运行时痕迹"，不是种子数据：密钥配置、上传的文件、通知、学生消息。
    # 每次测试从零开始，免得上一次测试的假密钥把这一次的结果带偏。
    SCRATCH = ("settings.json", "uploads.json", "uploads", "notices.json",
               "student_messages.json", "workorders.json")

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="campustime-test-")
        dest = os.path.join(self.tmp, "data")
        shutil.copytree(REAL_DATA_DIR, dest)
        for name in self.SCRATCH:
            p = os.path.join(dest, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)
        os.environ["CAMPUSTIME_DATA_DIR"] = dest
        # 万一测试中途崩了，退出进程时也保证删掉，不留垃圾
        atexit.register(self._clean)
        return dest

    def __exit__(self, *exc):
        self._clean()
        atexit.unregister(self._clean)
        return False  # 不吞掉异常，该报错还是报错

    def _clean(self):
        if getattr(self, "tmp", None) and os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CAMPUSTIME_DATA_DIR", None)
        print("\n[临时数据目录已删除，真实演示数据没被碰过]")


def data_path(*parts):
    """拼出**当前生效**数据目录下的路径，比如 data_path('kb.json')。

    注意：必须在 sandbox() 之后调用，否则拿到的还是真实数据目录
    （读真实数据的场景见 test_05，那儿要的是演示数据本来的样子）。
    """
    base = os.environ.get("CAMPUSTIME_DATA_DIR") or REAL_DATA_DIR
    return os.path.join(base, *parts)


def make_client():
    """造一个"假浏览器"（人话：不用真的 uvicorn 启服务，也能调接口试）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def title(text):
    """打印分节标题，让输出像一份可读的检查清单。"""
    print(f"\n=== {text} ===")
