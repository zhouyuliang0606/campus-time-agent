"""把标准输出固定成 UTF-8（人话：让 ✅ / ❌ / ▶ 打得出来）。

为什么需要这个文件：本项目的测试结果里全是 ✅ / ❌，而中文 Windows 控制台
默认码页是 GBK(cp936)。`print("✅ ...")` 在那种机器上不是"显示成乱码"，
是**直接抛 UnicodeEncodeError 把测试打断**——报错看起来像测试自己坏了，
其实是打印坏了。评委/同学照着 README 敲 `python tests/run_all.py` 就会撞上。

用法：每个测试入口 `import _utf8`（导入即生效），不需要每个文件抄一遍判断逻辑。
"""
import sys


def force_utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 老版本 Python 或被重定向成非文本流：做不到就算了，不影响测试逻辑
            pass


force_utf8_streams()
