"""测试用的小工具箱（人话：三个测试文件都要用的"打分员 + 沙箱"）。

这里有四样东西：
1. Checker  —— 数一数这一步过了没，最后打印「通过 X 项，失败 Y 项」；
2. sandbox  —— 把 app/data 复制一份到临时目录，让测试只写那份副本。
               这是最关键的一件事：测试会保存假密钥、传临时文件，
               绝不能把痕迹留在你的演示环境里；
3. make_client —— 造一个"假浏览器"，不用真的启服务器也能调接口；
4. seed_todos / wipe_todos / write_timetable —— 往**当前沙箱**里摆一份已知的起始数据。
               `sandbox()` 只是复制真实数据，不是空库，所以依赖起始状态的断言必须自己摆桌子
               （见 `sandbox.SCRATCH` 上面那段注释与指南红线 18）。

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

# ==== 进程级隔离模型流水账（#47，2026-10-15 实测后定的做法）====
# 为什么必须在 **import _harness 时**就装，而不是只往 sandbox() 里加：
# `store._path()` 每次调用都重读数据目录 env，所以**沙箱内本来就不碰真实账本**
# （探针实测：切了 `CAMPUSTIME_DATA_DIR` 再记一笔，真实 `llm_usage.jsonl` 一行没多）。
# 真漏的是**没包 sandbox() 的那几批** —— 真实账本里 760 笔桩模型 `m`，
# 按 user 一分：`内部:话术润色` 630、`内部:流式自检` 56、`内部:语义判定` 40、`内部:测试` 30、
# `ssrf-probe` 4，全是直接调网关/客户端就记账的那种批。默认值不在进程层换掉，那 760 笔的源头治不到。
# 沙箱内再把它指到沙箱自己那份（读账本的那几个测试语义零变化 —— test_50 A 批两头都钉了）。
USAGE_SANDBOX_DIR = tempfile.mkdtemp(prefix="campustime-usage-")
os.environ.setdefault("CAMPUSTIME_USAGE_FILE",
                      os.path.join(USAGE_SANDBOX_DIR, "llm_usage.jsonl"))


def _drop_usage_dir():
    shutil.rmtree(USAGE_SANDBOX_DIR, ignore_errors=True)


atexit.register(_drop_usage_dir)


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
    #
    # ⚠️ `student/todos.json` / `student/timetable.json` **故意不在这里**（2026-10-14 定案）：
    #   一刀切清空会让"得有数据才能验『删之前不动』"那一批反而红。
    #   替代做法是**谁依赖起始状态谁自种**：`seed_todos()` / `wipe_todos()` /
    #   `write_timetable()`（都只能在沙箱内调用，见 `_require_sandbox`）。
    #   写新断言前记住这条：`sandbox()` 是"复制真实数据"，**不是空库** ——
    #   不种就等于是拿用户今天手测到什么来判测试。
    SCRATCH = ("settings.json", "uploads.json", "uploads", "notices.json",
               "student_messages.json", "workorders.json", "intent_samples.json",
               # 低代码那两项也是同一性质：演示时管理员真会建一项叫「失物招领」的服务，
               # 带进沙箱就会让 test_53 里"合法定义该落库"因为**撞名**而红 —— 看着像功能坏了，
               # 其实是测试拿用户今天手测到什么当起点。删掉后 store 会自动回到内置种子。
               "services.json", "custom")

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
        # 账本也跟着沙箱走：读得到（copytree 带进来的那份 + 本批新写的），
        # 但一个字节都不落到真实账本上。存一下外面的值，出去时**恢复**而不是 pop ——
        # 外面那层是进程级临时账本（见文件头），pop 掉会让沙箱外的批又开始往真实文件写。
        self._prev_usage = os.environ.get("CAMPUSTIME_USAGE_FILE")
        os.environ["CAMPUSTIME_USAGE_FILE"] = os.path.join(dest, "llm_usage.jsonl")
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
        if getattr(self, "_prev_usage", None) is not None:
            os.environ["CAMPUSTIME_USAGE_FILE"] = self._prev_usage
            self._prev_usage = None
        print("\n[临时数据目录已删除，真实演示数据没被碰过]")


class pin_now:
    """把 planner 眼里的"现在"钉到某个钟点（with 这一段里有效），用来去掉**墙上时钟耦合**。

    为什么需要这个：带「今晚」的句子要出候选时段卡，得同时满足"替他挑的那一段 ≥ 90 分钟"
    和"起手不早于 now+30 分钟"。于是晚上八点半以后跑测试，今晚剩下的窗口不足 90 分钟，
    **卡一张也出不来** —— 同一份代码白天绿、夜里红，看着像回归。
    2026-10-16 实测：20:38 起 `test_23` 红 3 条、`test_45` 红 2 条，
    拿改动之前的提交 `b00b635` 单跑一模一样 → 坐实是**断言自己依赖几点几分跑的**，不是功能坏了。

    用法要成对（只钉"白天该出卡"那一头，"把挑时段整个关掉"也能蒙过去）：
    钉 19:00 验出卡，再钉一次 21:40 验它**老实说排不下**。
    """

    def __init__(self, hour: int, minute: int = 0):
        self.minutes = hour * 60 + minute
        self._saved = None
        self._planner = None

    def __enter__(self):
        from app.modules import planner
        self._planner = planner
        self._saved = planner._now_minutes
        planner._now_minutes = lambda: self.minutes
        return self.minutes

    def __exit__(self, *exc):
        if self._planner is not None and self._saved is not None:
            self._planner._now_minutes = self._saved
        self._saved = None
        return False   # 不吞异常


def data_path(*parts):
    """拼出**当前生效**数据目录下的路径，比如 data_path('kb.json')。

    注意：必须在 sandbox() 之后调用，否则拿到的还是真实数据目录
    （读真实数据的场景见 test_05，那儿要的是演示数据本来的样子）。
    """
    base = os.environ.get("CAMPUSTIME_DATA_DIR") or REAL_DATA_DIR
    return os.path.join(base, *parts)


def _require_sandbox(what: str) -> str:
    """种数据前先看一眼是不是在沙箱里 —— 不在就直接拒绝。

    为什么要有这道闸：这几个 helper 是**整份覆盖写**，在沙箱外调用等于把
    用户真实在用的演示数据清掉（他原话「每次修改都给我设置搞没了」就是这么来的）。
    """
    dest = os.environ.get("CAMPUSTIME_DATA_DIR")
    if not dest:
        raise RuntimeError(f"{what} 只能在 with sandbox(): 里调用（现在没有沙箱，会写到真实数据目录）")
    return dest


def seed_todos(todos: list) -> list:
    """把**当前沙箱**的待办覆盖成这一份已知清单（人话：测试自己摆自己的赛前状态）。

    为什么要有它（2026-10-14 整族扫）：`sandbox()` 是"复制真实数据"，不是空库 ——
    同一批"数一数待办 / 找一节空课"的断言因此在两台机器上是两个世界：
    开发机上库里 11 条（用户刚手测过），干净克隆里 0 条，
    本机 13 条红、克隆 15 条红，其中一大半纯属这个（指南红线 18 记的就是这件事）。
    **自种已知数据 = 两边都得绿**，而且比"清空"更强：断言要的"有几条可列"也照样成立。

    每项最少写 `title/date/start/end`，其余按 `store.add_todo` 的形状补齐；
    想留重复档就直接写 `repeat/weekday/done_dates`。返回写进去的那份（便于断言）。
    """
    import json

    dest = _require_sandbox("seed_todos()")
    full = []
    for i, t in enumerate(todos or []):
        item = {
            "id": t.get("id") or f"seed{i:04d}",
            "title": t.get("title", "待办"),
            "date": t.get("date", ""),
            "start": t.get("start", ""),
            "end": t.get("end", ""),
            "note": t.get("note", ""),
            "category": t.get("category", ""),
            "status": t.get("status", "planned"),
            "created_at": t.get("created_at", "2026-01-01 00:00:00"),
        }
        for k in ("repeat", "weekday", "done_dates"):
            if k in t:
                item[k] = t[k]
        full.append(item)
    p = os.path.join(dest, "student", "todos.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"todos": full}, f, ensure_ascii=False, indent=2)
    return full


def wipe_todos() -> list:
    """库里一条待办都没有（`seed_todos([])` 的别名，读代码时一眼看懂意图）。"""
    return seed_todos([])


def seed_kb(rows) -> int:
    """把**当前沙箱**的问答条目表 `kb.json` 覆盖成这一份已知条目。

    `rows` 是 `[(问题, 答案, [关键词]), ...]`；`seed_kb([])` = 清空。

    为什么要有它（2026-10-22 实测）：`sandbox()` 是整树拷贝真实 `app/data`，
    所以**用户在管理端加过什么，每个沙箱就带着什么**。他那次把「健身房价格」的关键词
    收窄成「健身」，`test_07` 那句「健身显示不出来」立刻被知识库接走、不再走报障核实分支
    —— 一条断言在量谁的数据，这次轮到 KB。
    ⚠️ 整份覆盖写 ⇒ 只许在沙箱里跑：先过 `_require_sandbox()`，否则一句误调用
    就把用户真实那份条目表清了（他原话「每次修改都给我设置搞没了」是这条闸的来历）。
    """
    _require_sandbox("seed_kb()")
    import json
    with open(data_path("kb.json"), "w", encoding="utf-8") as f:
        json.dump({"entries": [{"question": q, "answer": a, "keywords": list(k)}
                               for q, a, k in rows]}, f, ensure_ascii=False)
    return len(rows)


# 一份"两种断言都喂得到"的已知周表：只有周六晚 19:00-21:00 一节《晚课·社团》。
# 为什么长成这样：有的断言要"至少有一节课能撞"（撞课、被占的晚上、从模型回话里抠课），
# 有的要"我点的钟点那天是空的"（15:00 / 07:00 必须真给出来）—— 一门晚课 + 其余全空，
# 两边同时成立，而且**不依赖任何人的真实课表**（本机/克隆两种环境同一个结果）。
#   要"一整份像真的课表"请用 `_e2e_env.demo_courses_from_json()`（源头是仓库里的
#   `app/data/courses.json`，12 门课，克隆下来一模一样）；本文件这份只保证
#   "有一个被占的晚上 + 其余全空"，够排空档那类断言用，别拿它当课表内容断言的靶子。
SEED_COURSES = [{"day": 6, "start": "19:00", "end": "21:00",
                 "course": "晚课·社团", "location": "体育馆"}]


def write_timetable(courses: list, updated: str = "2026-01-01 00:00:00") -> list:
    """把**当前沙箱**的周表覆盖成这一份已知课程（形状与 `store.save_timetable` 一致）。

    为什么"覆盖"而不是"追加"：断言里那些"周六下午三点该给 15:00""晚上七点被课占了"
    只在**知道那天有什么课**的前提下才成立；真实课表一改，断言就跟着变红（或被假绿掩盖）。
    """
    import json

    dest = _require_sandbox("write_timetable()")
    p = os.path.join(dest, "student", "timetable.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"updated_at": updated, "courses": courses}, f,
                  ensure_ascii=False, indent=2)
    return courses


def write_express(packages: list, student: str = "测试台账") -> list:
    """把**当前沙箱**的快递台账覆盖成这一份（人话：测试自己摆"谁有哪几件"）。

    形状与 `app/data/express.json` 一致（顶层 `{"student", "packages"}`，每条带 `owner`）。
    ⚠️ 只写沙箱：`_require_sandbox` 不在沙箱里就直接拒绝 —— 这份是 tracked 的演示底稿，
       写错了就是一次真实数据事故（同 `seed_todos` 那道闸的理由）。
    """
    import json

    dest = _require_sandbox("write_express()")
    p = os.path.join(dest, "express.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"student": student, "packages": list(packages or [])}, f,
                  ensure_ascii=False, indent=2)
    return packages


def write_takeout(orders: list, student: str = "测试台账") -> list:
    """把**当前沙箱**的外卖台账覆盖成这一份（同 `write_express`）。"""
    import json

    dest = _require_sandbox("write_takeout()")
    p = os.path.join(dest, "takeout.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"student": student, "orders": list(orders or [])}, f,
                  ensure_ascii=False, indent=2)
    return orders


# 测试用的管理口令。真实环境里这串来自 .env 的 CAMPUSTIME_ADMIN_TOKEN，
# 由 app/admin_guard.py 逐个请求校验；测试在这里定死一个，让那 40 处
# 本来就合法的管理接口调用不必各自想起来带 header（"忘了带"和"该被拒"
# 长得一模一样，那种测试自己就会骗自己）。真要验"没口令被拒"，
# 像 test_32 那样显式传 admin_token=None，别改这里。
ADMIN_TOKEN = "test-admin-token"


def write_accounts(sids) -> list:
    """把**当前沙箱**的账号名录覆盖成这些学号（#38：只有名录里的学号登得进 `/api/login`）。

    为什么要有它：名单校验一上，测试里那些 `S001 / S002` 之类的学号如果不写进名录，
    登录直接 403 —— 那时红的是"赛前状态没摆好"，不是隔离坏了（红线 18 同一个病，只是这次多一道门）。
    真实名录 `app/data/accounts.json` 是 tracked 的（admin1/admin2），**别拿它当测试起点**。
    """
    import json

    dest = _require_sandbox("write_accounts()")
    rows = [{"sid": str(s), "name": "测试 " + str(s), "created_at": "测试"} for s in (sids or [])]
    with open(os.path.join(dest, "accounts.json"), "w", encoding="utf-8") as f:
        json.dump({"accounts": rows}, f, ensure_ascii=False, indent=2)
    return rows


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

    # ⚠️ 签名要跟 `app.llm.client.DeepSeekClient.chat` 保持一致（刀 3 起了 max_tokens 就是这样）。
    #    漏一个新参数不会在这里报错，而是被判定层的 `except Exception` 吞成"模型没答"→ 回 None，
    #    于是依赖语义层的测试**以错误的理由**变绿或变红 —— 比直接崩难查得多。
    async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                   max_tokens=None):
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


CHAT_FUNCS = ("_chat_impl", "_run_agent_loop")


def chat_nodes(tree):
    """聊天主体的函数节点（人话：**"主链路"从 #61-C 起是两个函数，不是一个**）。

    为什么要有它：`_chat_impl` 里那段"组装引擎 + 跑 ReAct + 出口补条"（原 4) 那一段）
    被整段抽成了 `_run_agent_loop`，好让"正常走到这儿"和"Agent 模式跳过快路"两个调用点
    共用**同一份**引擎代码。于是一批按 AST 钉"这一句落在哪个函数里"的断言会认错窝 ——
    它们要钉的其实是"聊天主体里有没有这段逻辑"，不是"这段逻辑恰好在 `_chat_impl` 里"。
    各测试自己写一遍这个名单 = 第三个判据抄两遍（红线 4/11），所以收在这儿。

    用法：`for n in chat_nodes(tree): ...` 或 `chat_src(tree)`（两段的源码拼一起）。
    ⚠️ 返回顺序固定为 CHAT_FUNCS 的顺序；**少一个就直接报错**，不要静默返回一个 ——
       静默少一个正好是"断言全绿但其实只查了一半"的那种假绿。
    """
    import ast

    found = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name in CHAT_FUNCS}
    missing = [k for k in CHAT_FUNCS if k not in found]
    if missing:
        raise AssertionError(f"main.py 里找不到聊天主体的函数：{missing}"
                             "（被改名/被合并了 —— 用这个 helper 的断言全都在找它，先修这里）")
    return [found[k] for k in CHAT_FUNCS]


def chat_src(tree):
    """聊天主体两段的源码拼成一份（人话：判"这句在不在主链路里"，不判它在哪个函数里）。"""
    import ast

    return "\n".join(ast.unparse(n) for n in chat_nodes(tree))
