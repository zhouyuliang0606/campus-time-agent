"""`requirements.txt` 的 UTF-8 BOM —— 中文 Windows 复现护栏（test_30）。

**为什么会红**（2026-10-08 搬家到 `D:\\CampusTime` 后重建 venv 时撞上）：
本文件是 UTF-8 且**每行依赖上面都写了中文注释**（README:204 特意说明了这点）。
pip 读 requirements 用的是 `pip._internal.utils.encoding.auto_decode()`：
它先看 BOM，**没有 BOM 就按系统码页解**。中文 Windows 的系统码页是 cp936(GBK)，
于是 `pip install -r requirements.txt` 直接崩在这里：

    UnicodeDecodeError: 'gbk' codec can't decode byte 0xab in position 6
    decoding with 'cp936' codec failed

README 第 2 节「装依赖」就是这条命令，**评委在中文机器上照抄会在第一步卡死**，
比少一个演示视频更糟（视频是加分项，装不上是复现失败）。

修法只加 3 个字节：文件头补 UTF-8 BOM。`auto_decode` 认出 BOM 后走 `utf-8-sig`，
中文注释原样保留，跨平台都正常。

⚠️ **别把 BOM 当"垃圾字符"清理掉** —— 清掉就等于把这个 bug 装回去。
所以本护栏除了钉字节，还钉 README 里那句解释（改文档的人看得见原因）。
"""

import os
import subprocess
import sys

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


def _decode_like_pip(data: bytes):
    """跑 pip 自己的解码函数（不是我们自己猜），在子进程里跑以免吃到本进程的编码设置。"""
    code = (
        "import sys,json;"
        "d=open(sys.argv[1],'rb').read();"
        "from pip._internal.utils.encoding import auto_decode;"
        "print(json.dumps({'ok':True,'text':auto_decode(d)[:40]}))"
    )
    try:
        r = subprocess.run([sys.executable, "-c", code, REQ], capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return False, (r.stderr or "").strip().splitlines()[-1] if r.stderr else "解码失败"
    import json

    try:
        return True, json.loads(r.stdout.strip())["text"]
    except Exception:  # noqa: BLE001
        return False, r.stdout[:120]


def run():
    c = Collector()
    raw = open(REQ, "rb").read()
    # BOM 在不在都要能跑完剩下的批次：护栏要"红得清楚"，不能自己崩在后面批次前头
    body = raw[3:] if raw.startswith(BOM) else raw

    # A. 字节层：BOM 必须在，且只在文件头出现一次
    c.batch("A. 字节层：BOM 就在那儿")
    c.check("A1 文件头三个字节是 UTF-8 BOM", raw[:3] == BOM, f"实际 {raw[:3]!r}")
    c.check("A2 BOM 只出现在开头一次（没被复制进正文）", raw.count(BOM) == 1, f"出现 {raw.count(BOM)} 次")
    c.check("A3 去掉 BOM 后正文仍是合法 UTF-8", _is_utf8(body))
    c.check("A4 中文注释没被顺手改成英文（第一行仍是中文说明）",
            body.decode("utf-8", "replace").splitlines()[0].lstrip("# ").startswith("后端"),
            body.decode("utf-8", "replace").splitlines()[0])

    # B. 真跑 pip 的解码路径（故障就发生在这里）
    c.batch("B. pip 的真实解码路径：auto_decode")
    ok, text = _decode_like_pip(raw)
    c.check("B1 pip 的 auto_decode 不再抛异常（原报障就是它崩）", ok, text)
    c.check("B2 BOM 被认出来并剥掉，正文从注释开始（不是从乱码锘开始）",
            ok and text.startswith("#"), repr(text[:12]))
    # 反证：同一份内容去掉 BOM，按中文 Windows 的码页解必须失败——证明 BOM 不是摆设
    c.check("B3 反向验证：把 BOM 拿掉、按 cp936(中文 Windows 码页) 解，必须炸",
            _must_fail_without_bom(body))

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
    readme = open(README, encoding="utf-8").read()
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
