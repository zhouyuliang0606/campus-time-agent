"""`requirements.txt` 的 UTF-8 BOM —— 中文 Windows 复现护栏（test_30）。

**为什么会红**（2026-10-08 搬家到 `D:\\CampusTime` 后重建 venv 时撞上）：
本文件是 UTF-8 且**每行依赖上面都写了中文注释**（README:204 特意说明了这点）。
旧一代 pip 读 requirements 时先看 BOM，**没有 BOM 就按系统码页解**。
中文 Windows 的系统码页是 cp936(GBK)，于是 `pip install -r requirements.txt` 直接崩在这里：

    UnicodeDecodeError: 'gbk' codec can't decode byte 0xab in position 6
    decoding with 'cp936' codec failed

修法只加 3 个字节：文件头补 UTF-8 BOM，中文注释原样保留，跨平台都正常。

⚠️ **别把 BOM 当"垃圾字符"清理掉** —— 清掉就等于把这个 bug 装回去（旧 pip 上）。
所以本护栏除了钉字节，还钉 README 里那句解释（改文档的人看得见原因）。

⚠️ 第二条教训（2026-10-14，干净克隆 + 新 venv 实测）：**别摸 pip 的私有模块**。
原来 B 批是 `from pip._internal.utils.encoding import auto_decode`，pip 26 把那模块挪走了
（BOM 表现在在 `pip._internal.req.req_file.BOMS`），于是**装了新 pip 的机器上这条必红** ——
克隆态 15 条红里有 2 条纯属自找的，跟产品代码一点关系没有。
现在改走 pip 的公共 CLI（`--dry-run --no-index --no-deps`，不联网不装包）。
"""

import os
import subprocess
import sys
import tempfile

import _utf8  # noqa: F401  本文件自己也要能在中国 Windows 上跑（同一个坑）

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(PROJ_DIR, "requirements.txt")
README = os.path.join(PROJ_DIR, "README.md")
RUN_ALL = os.path.join(PROJ_DIR, "tests", "run_all.py")
HARNESS = os.path.join(PROJ_DIR, "tests", "_harness.py")
UTF8_MOD = os.path.join(PROJ_DIR, "tests", "_utf8.py")
BOM = b"\xef\xbb\xbf"


class Collector:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  ✅ {name}")
        else:
            self.failed += 1
            print(f"  ❌ {name}  {detail}")
        return ok

    def batch(self, label):
        print(f"\n=== {label} ===")


def _pip_reads(path):
    """让 pip **用它自己的公共 CLI** 去读这份清单，返回 (退出码, 合并输出)。

    ⚠️ 以前这里是 `from pip._internal.utils.encoding import auto_decode` —— 摸的是私有模块，
    pip 26 把它挪走了（现在那套 BOM 表在 `pip._internal.req.req_file.BOMS`），
    于是**装了新版 pip 的机器上这条断言必红**，2026-10-14 用干净克隆 + 新 venv 实测到：
    克隆态 15 条红里有 2 条就是这么来的，跟产品代码一点关系没有。
    公共 CLI 才是稳定接口，而且 `--dry-run --no-index --no-deps` 不联网、不装包（实测 1.2 秒），
    走的正是"读文件 → 解码 → 解析"那一段 —— 当初崩的就是这一段。
    """
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONUTF8"] = "0"          # 模拟中文 Windows 默认状态：让 pip 去吃系统码页，别让它替我们兜着
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--dry-run", "--no-index", "--no-deps", "-r", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _encoding_error(out: str) -> bool:
    return any(k in out for k in ("UnicodeDecodeError", "UnicodeEncodeError", "codec", "gbk", "cp936"))
def run():
    c = Collector()
    raw = open(REQ, "rb").read()
    # BOM 在不在都要能跑完剩下的批次：护栏要"红得清楚"，不能自己崩在后面批次前头
    body = raw[3:] if raw.startswith(BOM) else raw
    readme = open(README, encoding="utf-8").read()
    # 无 BOM 的副本放系统临时目录（**不进仓库**：既免得脏 git status，也免得哪天被误提交）
    NO_BOM_COPY = os.path.join(tempfile.gettempdir(), "campustime_test30_nobom_requirements.txt")
    with open(NO_BOM_COPY, "wb") as f:
        f.write(body)

    # A. 字节层：BOM 必须在，且只在文件头出现一次
    c.batch("A. 字节层：BOM 就在那儿")
    c.check("A1 文件头三个字节是 UTF-8 BOM", raw[:3] == BOM, f"实际 {raw[:3]!r}")
    c.check("A2 BOM 只出现在开头一次（没被复制进正文）", raw.count(BOM) == 1, f"出现 {raw.count(BOM)} 次")
    c.check("A3 去掉 BOM 后正文仍是合法 UTF-8", _is_utf8(body))
    c.check("A4 中文注释没被顺手改成英文（第一行仍是中文说明）",
            body.decode("utf-8", "replace").splitlines()[0].lstrip("# ").startswith("后端"),
            body.decode("utf-8", "replace").splitlines()[0])

    # B. pip 真的读一遍（走公共 CLI，不摸它的私有模块 —— 见 `_pip_reads` 那段）
    c.batch("B. pip 真的把这份清单读了一遍")
    rc, out = _pip_reads(REQ)
    c.check("B1 我们这份（带 BOM）pip 读通了：退出码 0、没有任何编码类报错",
            rc == 0 and not _encoding_error(out), f"rc={rc} / {(out.strip().splitlines() or [''])[-1][:90]}")
    c.check("B2 是真读到底，不是读到第一行就退（清单最后一项也报出来了）",
            "python-multipart" in out, out[-200:] if out else "（没输出）")
    # 反证一（与 pip 版本无关）：同一份内容去掉 BOM，按中文 Windows 的码页解必须炸
    c.check("B3 反向验证：把 BOM 拿掉、按 cp936(中文 Windows 码页) 解，必须炸",
            _must_fail_without_bom(body))
    # 反证二（与 pip 版本**有关**，所以断言的是"文档与事实不许互相打脸"）：
    # 实测 pip 24.2 读无 BOM 的这份会崩（UnicodeDecodeError / cp936），
    # pip 26 起改成先按 UTF-8 试，就不崩了。README 要是只写"没 BOM 一定装不上"，
    # 在新 pip 的机器上就成了吓唬人的假话 —— 所以两种情况下各有一句必须存在。
    rc_nb, out_nb = _pip_reads(NO_BOM_COPY)
    crashes = rc_nb != 0 and _encoding_error(out_nb)
    if crashes:
        c.check("B4 这台机器的 pip 仍按码页解（无 BOM 真崩）→ README 那句「BOM 别当垃圾清掉 + 码页」必须在",
                "BOM" in readme and "码页" in readme and ("清掉" in readme or "别删" in readme),
                f"pip 读无 BOM 副本 rc={rc_nb}")
    else:
        c.check("B4 这台机器的 pip 已经不靠 BOM（新版行为）→ README 必须写清这是版本差异，"
                "别把它说成任何机器都必崩",
                "26" in readme and ("兼容" in readme or "旧" in readme),
                f"pip 读无 BOM 副本 rc={rc_nb}（没崩）")

    # C. 内容一字未动（防止加 BOM 时偷改了依赖行）
    c.batch("C. 只管编码，不许顺手改内容")
    lines = [ln.strip() for ln in body.decode("utf-8", "replace").splitlines() if ln.strip()]
    pkgs = [ln.split("#")[0].strip() for ln in lines if not ln.startswith("#")]
    c.check("C1 依赖包仍是 9 个", len(pkgs) == 9, f"现在 {len(pkgs)} 个：{pkgs}")
    for want in ("fastapi", "uvicorn", "httpx", "python-dotenv", "pypdf",
                 "python-docx", "openpyxl", "python-pptx", "python-multipart"):
        c.check(f"C2 {want} 仍在清单里（版本约束没被动过）",
                any(p.lower().replace("_", "-").startswith(want) for p in pkgs), str(pkgs))
    c.check("C3 没有任何一行以 BOM 开头（只有文件头那一个）",
            all(not ln.startswith("\ufeff") for ln in body.decode("utf-8", "replace").splitlines()[1:]))

    # D. 原因要留在文档里，否则下一个人会把 BOM 当垃圾清掉
    c.batch("D. 文档里留了『为什么有 BOM』")
    c.check("D1 README 提到了 BOM", "BOM" in readme, "没找到 BOM 字样")
    c.check("D2 README 说明了不加会怎样（中文 Windows / GBK 装不上）",
            ("GBK" in readme or "gbk" in readme) and "中文" in readme)
    c.check("D3 本护栏文件存在（run_all 会自动扫到）", os.path.abspath(__file__).endswith(".py"))

    # E. 同一类坑的第二处：✅ 打在 GBK 控制台上会抛 UnicodeEncodeError，
    #    症状是"测试跑不起来"，不是"测试没过"——README 那条 `python tests/run_all.py` 直接中断
    c.batch("E. 自测在中文 Windows 控制台上打得出来")
    c.check("E1 tests/_utf8.py 存在（一份实现，不各抄一份）", os.path.exists(UTF8_MOD))
    run_all_src = open(RUN_ALL, encoding="utf-8").read()
    harness_src = open(HARNESS, encoding="utf-8").read()
    c.check("E2 run_all 固定了自己的输出流", "import _utf8" in run_all_src)
    c.check("E3 run_all 把编码传给子进程（每个 test_*.py 是单独进程）",
            "PYTHONIOENCODING" in run_all_src and "env=CHILD_ENV" in run_all_src)
    c.check("E4 _harness 也固定（28 个测试文件都 import 它）", "import _utf8" in harness_src)
    c.check("E5 三个自包含测试（不走 harness）各自也固定",
            all("import _utf8" in open(os.path.join(PROJ_DIR, "tests", n), encoding="utf-8").read()
                for n in ("test_24_confirm_render.py", "test_25_todos_panel_and_nav.py",
                          "test_30_requirements_bom.py")))

    return c


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _must_fail_without_bom(body: bytes) -> bool:
    """中文 Windows（cp936）/ 英文 Windows（cp1252）都解不动这份中文注释——任一码页解不动即算成立。"""
    for enc in ("cp936", "cp1252"):
        try:
            body.decode(enc)
        except UnicodeDecodeError:
            return True
    return False


def main():
    print("===== requirements.txt UTF-8 BOM / 中文 Windows 复现回归（test_30） =====")
    r = run()
    print(f"\n===== test_30 总账：通过 {r.passed} 项，失败 {r.failed} 项 =====")
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
