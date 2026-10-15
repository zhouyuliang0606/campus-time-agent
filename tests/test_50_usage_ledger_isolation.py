"""#47：测试的模型记账不许写进真实账本，但**生产侧的记账必须照样写**。

动手前先实测（`D:\\campustime-scratch\\probe47.py`），**施工单 §0 的诊断是错的**，所以修法也不同：
  · 它说"账本写出路径没有跟着沙箱重定向" —— 实测不成立：`store._path()` 是**每次调用重读 env** 的
    （`app/store.py:16-35`，那注释就是上一轮为此写的），把 `CAMPUSTIME_DATA_DIR` 切走再记一笔，
    真实 `llm_usage.jsonl` 行数**没动**，笔落在临时目录那份里。
  · 真实账本里 model=="m" 的有 **760 笔**（不是 56），按 user 标签一分就露馅了：
    `内部:话术润色` 630 / `内部:流式自检` 56（test_49 A 批）/ `内部:语义判定` 40（test_48 A 批）/
    `内部:测试` 30 / `ssrf-probe` 4 —— **全是"没包 sandbox() 的那几批"写的**。
    所以只往 sandbox 里加 env（施工单步骤 B）治不到这 760 笔的来源，必须加**进程级默认隔离**：
    `_harness` 一被 import 就把账本指到临时文件，沙箱内再覆盖成 `dest/llm_usage.jsonl`（测试语义零变化）。
  · 另一半同样重要：**不许把生产记账一起隔离掉**。管理端那张「模型/用量」表就靠这本账，
    测试环境把 env 设死、生产忘记回落 = 管理端从此看不见任何调用，而且是静默的。
    B 批专门钉这个回落（用"把数据目录指到临时目录 + 清掉 usage env"来验，不碰真实文件）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

from _harness import Checker, sandbox, title

code = 0
REAL_LEDGER = os.path.join("app", "data", "llm_usage.jsonl")
PROBE_USER = "隔离探针:沙箱外"


def _lines(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for ln in f if ln.strip())


def batch_a_leak_stops():
    c = Checker()
    title("A. 病根：没包 sandbox 的那几批，记账不许落到真实账本")
    import app.store as S

    env = os.environ.get("CAMPUSTIME_USAGE_FILE")
    c.check("前提：import _harness 之后就有了进程级账本覆盖，且不指向 app/data",
            bool(env) and os.path.abspath(REAL_LEDGER) != os.path.abspath(env or ""),
            f"CAMPUSTIME_USAGE_FILE={env!r}")
    proc_default = env

    n0 = _lines(REAL_LEDGER)
    S.log_llm_usage({"user": PROBE_USER, "model": "m", "prompt_tokens": 1,
                     "completion_tokens": 1, "total_tokens": 2, "status": "ok",
                     "latency_ms": 1})
    n1 = _lines(REAL_LEDGER)
    c.check("沙箱**外**记一笔 → 真实 llm_usage.jsonl 行数不变（这条就是 #47 的病根）",
            n1 == n0, f"真实账本 {n0} → {n1} 行")

    rows = S.read_llm_usage()
    mine = [r for r in rows if r.get("user") == PROBE_USER]
    # ⚠️ 不能只数"读到了"：修之前这笔就在**真实账本**里，read_llm_usage 照样读得到 ——
    #    那是一条会因为"没隔离"而通过的假绿。必须同时要求"读的不是真实账本那份"。
    c.check("那笔确实落在进程级临时账本里（读到的那份不是真实账本）",
            len(mine) == 1 and os.path.normpath(S._usage_path())
            != os.path.normpath(os.path.abspath(REAL_LEDGER)),
            f"{len(mine)} 笔，账本在 {S._usage_path()}")

    # 沙箱内的语义必须和今天一样：读得到、clear 只清沙箱那份
    with sandbox():
        S.clear_llm_usage()
        c.check("沙箱里 clear 之后读到 0 笔（test_22 / test_49 B 批靠这个数行）",
                S.read_llm_usage() == [], f"{len(S.read_llm_usage())} 笔")
        S.log_llm_usage({"user": "沙箱内探针", "model": "m", "prompt_tokens": 2,
                         "completion_tokens": 2, "total_tokens": 4, "status": "ok",
                         "latency_ms": 1})
        got = S.read_llm_usage()
        c.check("沙箱内记一笔读得到，且账本就落在沙箱目录里（不是进程级那份）",
                len(got) == 1 and got[0]["user"] == "沙箱内探针"
                and os.path.abspath(S._usage_path()) ==
                os.path.abspath(os.path.join(os.environ["CAMPUSTIME_DATA_DIR"],
                                             "llm_usage.jsonl")),
                os.path.basename(S._usage_path()))
        c.check("沙箱内那一通写删，真实账本还是没动",
                _lines(REAL_LEDGER) == n1, f"{_lines(REAL_LEDGER)} 行")

    # 沙箱**退出之后**才是真正容易漏的地方：退出时如果是 pop 而不是恢复，
    # 进程级那份默认就没了 → 沙箱外的下一笔又会掉回真实账本。
    # 所以这里不比较"变量等于我自己刚赋的值"（那是在测测试代码，V4 变异就是这么全绿的），
    # 而是**再写一笔**看真实账本动不动。
    n_before2 = _lines(REAL_LEDGER)
    S.log_llm_usage({"user": PROBE_USER + "-2", "model": "m", "prompt_tokens": 1,
                     "completion_tokens": 1, "total_tokens": 2, "status": "ok",
                     "latency_ms": 1})
    c.check("沙箱退出后再记一笔，真实账本照样不动（退出必须恢复，不能 pop）",
            _lines(REAL_LEDGER) == n_before2,
            f"{n_before2} → {_lines(REAL_LEDGER)} 行，env={os.environ.get('CAMPUSTIME_USAGE_FILE')!r}")

    # 覆盖优先级：env 指哪就打哪
    tmp = tempfile.mkdtemp(prefix="usage-env-")
    target = os.path.join(tmp, "elsewhere.jsonl")
    old = os.environ.get("CAMPUSTIME_USAGE_FILE")
    os.environ["CAMPUSTIME_USAGE_FILE"] = target
    c.check("CAMPUSTIME_USAGE_FILE 指哪儿就写哪儿（绝对路径直取）",
            os.path.abspath(S._usage_path()) == os.path.abspath(target), S._usage_path())
    if old is None:
        os.environ.pop("CAMPUSTIME_USAGE_FILE", None)
    else:
        os.environ["CAMPUSTIME_USAGE_FILE"] = old
    shutil.rmtree(tmp, ignore_errors=True)
    return c


def batch_b_production_fallback():
    c = Checker()
    title("B. 生产侧不许被一起隔离掉：没有 usage env 时，账本回落到数据目录")
    # 做法：**起一个干净解释器**，把 CAMPUSTIME_DATA_DIR 指到临时目录、并保证没有 usage env。
    # 这样"回落"这条生产路径被真写了一次，却一个字节都不碰仓库里的真实账本
    #（顺手也避免和正在跑的 8000 抢同一个文件）。
    tmp = tempfile.mkdtemp(prefix="usage-prod-")
    env = dict(os.environ)
    env["CAMPUSTIME_DATA_DIR"] = tmp
    env.pop("CAMPUSTIME_USAGE_FILE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    snippet = (
        "import app.store as S;"
        "S.log_llm_usage({'user':'生产回落探针','model':'deepseek-chat','prompt_tokens':3,"
        "'completion_tokens':4,'total_tokens':7,'status':'ok','latency_ms':5});"
        "print(S._usage_path())"
    )
    p = subprocess.run([sys.executable, "-c", snippet], cwd=os.getcwd(),
                       env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    wrote = os.path.join(tmp, "llm_usage.jsonl")
    printed = (p.stdout or "").strip()
    c.check("没有 usage env 时，记账写回**数据目录**里那份（管理端读的就是它）",
            p.returncode == 0 and os.path.exists(wrote) and _lines(wrote) == 1,
            f"exit={p.returncode} 那份存在={os.path.exists(wrote)} 行数={_lines(wrote)}")
    c.check("它落盘的路径就是数据目录下那份（没有别的 env 悄悄改道）",
            os.path.normpath(printed) == os.path.normpath(wrote),
            printed or repr((p.stderr or "")[:120]))
    return c


def batch_c_shape():
    c = Checker()
    title("C. 形状：账本路径只有 _usage_path 一个出口，别处不许硬编码")
    src = open("app/store.py", encoding="utf-8").read()
    code_lines = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    c.check("真正 open 账本的只有 log/read/clear 三处，且都走 _usage_path()",
            code_lines.count("open(_usage_path()") == 3,
            f"{code_lines.count('open(_usage_path()')} 处")
    hard = [ln for ln in code_lines.splitlines()
            if "llm_usage.jsonl" in ln and not ln.strip().startswith("USAGE_FILE =")]
    c.check("除了 USAGE_FILE 那一行定义，别处不许再写死文件名（写死=绕过隔离）",
            not hard, " | ".join(h.strip()[:60] for h in hard)[:90])
    c.check("建目录用的是**当前账本所在目录**，不是 import 时缓存的 DATA_DIR",
            "os.makedirs(os.path.dirname(_usage_path())" in code_lines, "")
    harn = open("tests/_harness.py", encoding="utf-8").read()
    c.check("进程级默认隔离在 _harness 被 import 时就装上（sandbox 之外那几批才治得到）",
            harn.count("CAMPUSTIME_USAGE_FILE") >= 3,
            f"出现 {harn.count('CAMPUSTIME_USAGE_FILE')} 次")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_50_usage_ledger_isolation：测试笔不进真实账本，生产记账照常")
    print("=" * 60)
    code |= batch_a_leak_stops().summary("A 批（病根）")
    code |= batch_b_production_fallback().summary("B 批（生产回落）")
    code |= batch_c_shape().summary("C 批（形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
