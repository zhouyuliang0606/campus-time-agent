"""确认条渲染竞态回归测试（test_24）。

守住一个真实出过的 bug：后端明明出了 timetable_change 卡，前端却"没有弹卡"。
根因：confirm.html 的 render() 在 /api/pending 取不到提案（timetable_change 本就不进暂存）
时，立刻 tellParent("empty")，父页面据此 removeCfWrap() 把 iframe 撤了，
而此刻父页面正要通过 postMessage 把真卡递过来——卡被撤掉，看不见。

守三件事：
  ① 嵌在聊天框里（inFrame）且暂存取空时，先显示"加载中"，不要立刻 tellParent("empty")；
  ② 真正的 tellParent("empty") 必须延迟、且只在 !fromParent 时才发（给 postMessage 留时间）；
  ③ 独立打开（非 iframe）的原有行为保持不变（仍立即 tellParent("empty")）。
"""

import os

import _utf8  # noqa: F401  中文 Windows 的 GBK 控制台打不出 ✅，会让本文件一跑就中断

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIRM = os.path.join(PROJ_DIR, "app", "static", "confirm.html")


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


def run():
    c = Collector()
    with open(CONFIRM, encoding="utf-8") as f:
        src = f.read()

    # ① 嵌在聊天框里、暂存取空时显示"加载中"而非立刻撤条
    c.check("inFrame 空提案时显示「加载中」而非立即撤条",
            "正在加载确认条" in src, "缺少加载中占位，可能被改回立即 tellParent 空")

    # ② 真正的 tellParent('empty') 必须延迟、且门控在 !fromParent
    c.check("tellParent('empty') 被延迟并门控在 !fromParent",
            'if (!fromParent) tellParent("empty", {})' in src
            or "if (!fromParent) tellParent('empty', {})" in src,
            "缺少延迟/门控的 tellParent empty，race 会复发")

    # ②b 延迟用的是 setTimeout（不是同步发）
    c.check("延迟用的是 setTimeout（不是同步 tellParent）",
            "setTimeout(() => { if (!fromParent) tellParent" in src,
            "缺少 setTimeout 包裹")

    # ③ 独立打开（非 iframe）分支仍保留原行为：立即 tellParent("empty", {})
    c.check("独立打开分支仍立即 tellParent empty（行为不变）",
            'tellParent("empty", {})' in src,
            "独立分支的 tellParent empty 不见了")

    # ③b 不能让 inFrame 分支也同步发 tellParent empty（即两个分支不能合并成同步发）
    # 经过 inFrame 分支后应 return，不会落到下面的同步 tellParent；
    # 这里用"加载中"字符串出现在 tellParent empty 之前来间接守住。
    idx_loading = src.find("正在加载确认条")
    idx_sync = src.find('tellParent("empty", {})')
    c.check("inFrame 分支先 return，不会同步发 tellParent empty",
            idx_loading != -1 and (idx_sync == -1 or idx_loading < idx_sync),
            "疑似 inFrame 分支仍在同步发 empty（race 复发）")

    # ④ 报错**不许**把学生的输入区抹掉（2026-10-21 真机：登记卡被 400 拒之后
    #    `input[data-f]` 从 3 变 0，卡上还写着「可以再试一次」，可格子已经没了 ——
    #    再点一次仍是空字段，出不去。根因就是 showError 整块换了 bodyEl.innerHTML）。
    i = src.find("function showError")
    se = src[i:src.find("\nfunction ", i + 1)]
    c.check("showError 不许整块替换 bodyEl（登记卡的输入框、候选时段的勾选项都住在里面）",
            "bodyEl.innerHTML" not in se, "showError 里又出现 bodyEl.innerHTML =")
    c.check("改成「只往正文上面插一条」：先清旧的 .cfErr，再 insertBefore",
            ".cfErr" in se and "insertBefore" in se, se[:120].replace("\n", " "))
    j = src.find("function showDone")
    sd = src[j:src.find("\nfunction ", j + 1)]
    c.check("配对：成功那条（showDone）**该**整块换成回执 —— 只有失败这条要留格子",
            "bodyEl.innerHTML" in sd, "showDone 不再替换 body，回执会叠在旧卡上")

    return c


def main():
    print("===== 确认条渲染竞态回归（test_24） =====")
    r = run()
    print(f"\n===== test_24 总账：通过 {r.passed} 项，失败 {r.failed} 项 =====")
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
