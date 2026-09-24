"""端到端测试的小环境（人话：给 e2e 单独开一份"干净的数据副本 + 一台独立服务器"）。

为什么要有它？
    e2e 是真的开浏览器点按钮，所以必须有一台真服务器；
    但服务器默认读写的是 app/data —— 那正是要拿去演示的那份数据。

    之前 e2e 直接打 127.0.0.1:8000（真实数据目录），跑一次就烂一次演示数据：
       · 周表被写成 2 门课、清空链路那次更狠，12 门直接删到 0 门；
       · 待办里堆出 20 多条「晨读」「看论文」「复习线性代数」；
       · 更麻烦的是它还会污染 test_06 的沙箱——沙箱是"复制当时的 app/data"，
         一份烂数据被复制进去，第二批断言就开始偶发失败，看着像代码不稳、其实是数据脏。

    现在改成：临时复制一份 app/data → 让服务器只认这份副本 → 测完连人带目录一起删掉。
    演示数据从此谁也碰不了，测试也能随跑随干净。

用法：
    from _e2e_env import e2e_server

    with e2e_server() as srv:
        page.goto(srv.url + "/student")       # 页面里所有 /api/xxx 都打到副本上
        srv.seed_timetable([...])             # 想播种什么，写进副本，别碰真文件
"""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = os.path.dirname(TESTS_DIR)
REAL_DATA_DIR = os.path.join(PROJ_DIR, "app", "data")

# 这里**故意不删任何文件**，跟 _harness.sandbox 那套"从零开始"的做法相反。
# 为什么：e2e 要验的是真实环境下的完整行为，而 app/data/settings.json 里
# 就放着演示用的模型密钥——早先照抄 sandbox 的清单把它删了，
# e2e 服务器一启动就"还没配置大模型密钥"，走 faq 兜底，
# 删课那条依赖模型的链路当场 5 项全红，看着像代码回归其实是环境缺了东西。
# 副本测完整个删掉，所以真实演示数据依旧一个字节都不动。


def _python_exe() -> str:
    """挑一个**装了 fastapi 的解释器**来起服务器。

    系统 python 常常没装项目依赖（会报 No module named 'fastapi'），
    项目自带的 .venv 里才有；所以优先用 .venv，找不到再退回当前解释器。
    """
    venv_py = os.path.join(PROJ_DIR, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv_py):
        return venv_py
    venv_py = os.path.join(PROJ_DIR, ".venv", "bin", "python")
    if os.path.exists(venv_py):
        return venv_py
    return sys.executable


def _free_port() -> int:
    """找一个此刻没人占用的端口（人话：抢个空位儿，免得跟别的进程打架）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class e2e_server:
    """一台"只认临时数据副本"的测试服务器，配合 with 使用。"""

    def __init__(self, port: int | None = None):
        self.port = port or _free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    # ---- 进出 with ----
    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="campustime-e2e-")
        self.data = os.path.join(self.tmp, "data")
        shutil.copytree(REAL_DATA_DIR, self.data)

        log = os.path.join(self.tmp, "server.log")
        env = dict(os.environ)
        env["CAMPUSTIME_DATA_DIR"] = self.data
        # creationflags=CREATE_NO_WINDOW：Windows 上别弹一个黑窗口出来
        self.proc = subprocess.Popen(
            [_python_exe(), "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=PROJ_DIR, env=env, stdout=open(log, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._wait_ready()
        return self

    def __exit__(self, *exc):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        print("\n[e2e 临时数据目录已删除，演示数据没被碰过]")
        return False  # 不吞异常

    # ---- 服务控制 ----
    def stop(self):
        if getattr(self, "proc", None) and self.proc.poll() is None:
            for _ in range(20):
                self.proc.terminate()
                time.sleep(0.1)
                if self.proc.poll() is not None:
                    break
            else:
                self.proc.kill()          # 打死循环也一样收掉，别留僵尸进程
            self.proc.wait(timeout=5) if hasattr(self.proc, "wait") else None
        self.proc = None

    def _wait_ready(self, timeout: float = 60.0):
        """等服务器真的能应答（人话：别一进来就开测，服务器还没爬起来呢）。"""
        import urllib.request
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"服务器进程已退出，日志：{self.tmp}/server.log\n{last}")
            try:
                with urllib.request.urlopen(self.url + "/api/timetable", timeout=1.5) as r:
                    if r.status == 200:
                        return
            except Exception as e:       # 还没起来 / 端口还没监听，都算"再等等"
                last = str(e)
                time.sleep(0.4)
        raise RuntimeError(f"服务器 {self.url} 迟迟没就绪，最后错误：{last}")

    # ---- 播种数据（只写副本） ----
    def seed_timetable(self, courses: list, updated: str = "2026-09-24 22:30:00"):
        """把一份周表写进**副本**数据目录（等价于"把演示数据恢复成这样"）。"""
        p = os.path.join(self.data, "student", "timetable.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"updated_at": updated, "courses": courses}, f,
                      ensure_ascii=False, indent=2)

    def seed_todos(self, todos: list):
        """把一份待办写进**副本**数据目录。"""
        p = os.path.join(self.data, "student", "todos.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"todos": todos}, f, ensure_ascii=False, indent=2)

    def seed_conversation(self, sid: str, msgs: list):
        """把一段聊天历史写进**副本**数据目录（人话：替学生"先聊过几句"）。

        为什么需要它：有些报障的场景**只发生在历史里**——比如管家上一轮只在
        文字里写了一段「任务：健身 / 时间：周一 16:30~18:00」，界面上没有按钮。
        要在浏览器里复现这一幕，得先让页面加载出那段历史（页面是从
        /api/conversation/<sid> 把记录捞回来渲染的），再让学生接一句「可以」。
        msgs 形如 [{"role": "user", "content": "..."}]。

        注意：前端的 session id 存在 localStorage 里，是随机生成的；
        截图脚本要先用 page.add_init_script 把它定成这里传的 sid，两边才对得上。
        文件路径跟 store.py 保持一致：会话存在**学生库** student/sessions.json
        （早先写到了 data/sessions.json，服务器读的是 student/ 那份，
        结果页面历史一片空白，白折腾一轮）。
        """
        p = os.path.join(self.data, "student", "sessions.json")
        data = {"sessions": {}}
        if os.path.exists(p):
            try:
                data = json.loads(open(p, encoding="utf-8").read()) or {"sessions": {}}
            except Exception:
                data = {"sessions": {}}
        data.setdefault("sessions", {})[sid] = list(msgs)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @property
    def demo_courses_path(self) -> pathlib.Path:
        return pathlib.Path(self.data) / "student" / "timetable.json"


def demo_courses_from_json() -> list:
    """按 app/data/courses.json 还原出一份完整演示周表（day 换成 1=周一 那种写法）。

    用它而不是硬编码一张表：courses.json 是演示数据的源头，
    改了源头测试不必跟着改，也不会出现"测试里的表跟页面上的表对不上"。
    """
    raw = json.loads(open(os.path.join(REAL_DATA_DIR, "courses.json"),
                          encoding="utf-8").read())
    day = {"周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5,
           "周六": 6, "周日": 7, "周天": 7}
    return [{"day": day.get(c["day"], 1), "start": c["start"], "end": c["end"],
             "course": c["name"], "location": c["location"]} for c in raw["courses"]]
