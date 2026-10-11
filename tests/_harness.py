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

import _utf8  # noqa: F401  导入即把 stdout/stderr 固定成 UTF-8（中文 Windows 打不出 ✅ 的坑）

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
    #
    # ★ `intent_samples.json` 是 2026-10-11 加进来的：它是**系统自己学出来的**样本库
    #   （每处理一句学生的话就长一条），不是种子数据。以前它跟着 copytree 进沙箱，
    #   于是"用户在浏览器里手测一轮"就能把全量基线改红 —— 实测两条：
    #   手测灌进四条含「图书馆」的 add_todo 样本之后，
    #   `match_sample("图书馆几点关门")` 以 dice 0.444 ≥ 阈值 0.42 抢走了一句**提问**
    #   （`test_01`、`test_08` 各红一条），而测试根本没改过任何东西。
    #   清掉它不影响判定：内置种子 `_SEED_SAMPLES` 写在代码里，新实例照样有例句可依。
    # ⚠️ `student/todos.json` 还没进来（那是要不要顺带修 7 条存量红的大刀，见指南红线 18）。
    SCRATCH = ("settings.json", "uploads.json", "uploads", "notices.json",
               "student_messages.json", "workorders.json", "intent_samples.json")

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


# 测试用的管理口令。真实环境里这串来自 .env 的 CAMPUSTIME_ADMIN_TOKEN，
# 由 app/admin_guard.py 逐个请求校验；测试在这里定死一个，让那 40 处
# 本来就合法的管理接口调用不必各自想起来带 header（"忘了带"和"该被拒"
# 长得一模一样，那种测试自己就会骗自己）。真要验"没口令被拒"，
# 像 test_32 那样显式传 admin_token=None，别改这里。
ADMIN_TOKEN = "test-admin-token"


def make_client(headers: dict | None = None, admin_token: str | None = ADMIN_TOKEN):
    """造一个"假浏览器"（人话：不用真的 uvicorn 启服务，也能调接口试）。

    默认带管理口令：`admin_token=None` 就一个头都不带，用来测"没口令进不进得去"。
    """
    import os

    from fastapi.testclient import TestClient
    from app.main import app

    if admin_token:
        # 服务端每次请求现读环境变量，所以这里补上就行，不用重开进程
        os.environ["CAMPUSTIME_ADMIN_TOKEN"] = admin_token
        hdrs = {"X-Admin-Token": admin_token}
    else:
        os.environ.pop("CAMPUSTIME_ADMIN_TOKEN", None)
        hdrs = {}
    hdrs.update(headers or {})
    return TestClient(app, headers=hdrs)


class FakeLLM:
    """假的模型客户端（人话：照着剧本回一句 JSON，不真联网）。

    为什么必须有它：判定层（`app/agent/intent.py` 的语义层）要吃模型的输出，
    而真模型每次回的措辞都在飘——不钉住它，意图相关的那几批测试**每次结果都不一样**，
    根本没法归档成"通过 N 项"的稳定报告。
    """

    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        return {"content": self._reply}


def patch_llm(reply: str, with_key: bool = True):
    """把大模型客户端换成打桩的，返回恢复函数（人话：临时换个假师傅）。

    `with_key=False` 是另一件重要的事：把 `get_llm_config` 打桩成"没有密钥"，
    用来验证**离线也能兜底**那条路（没 key → 语义层自动跳过 → 样本库/关键词顶上）。

    ⚠️ 打桩打的是 `app.llm.client.DeepSeekClient` 与 `app.config.get_llm_config`
    这两个**模块属性**：`intent.classify_with_llm` 与 `router` 都是**在函数内部**
    `from ... import` 的，所以改模块属性看得见；要是谁把它们挪到模块顶部导入，
    这里就**静默失效**了（补丁传不进去，语义层照旧打真网络——见 test_08 踩过的坑）。
    """
    import app.config as _cfg
    import app.llm.client as _client
    old_client, old_cfg = _client.DeepSeekClient, _cfg.get_llm_config
    _client.DeepSeekClient = lambda *a, **k: FakeLLM(reply)
    if with_key:
        _cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                               "model": "m"}
    else:
        _cfg.get_llm_config = lambda *a, **k: {"api_key": "", "base_url": "http://x",
                                               "model": "m"}

    def _restore():
        _client.DeepSeekClient = old_client
        _cfg.get_llm_config = old_cfg
    return _restore


def title(text):
    """打印分节标题，让输出像一份可读的检查清单。"""
    print(f"\n=== {text} ===")
