"""学生看得见的那段文字里，不许出现技术内脏（模型 / 厂商 / 密钥 / 后台入口）。

起因（2026-10-09 用户提的）：
> 对话中不能暴露使用模型

审计到的真实暴露面有三类，全都在**学生屏幕上**：
  1. 调用失败时 `answer = f"⚠️ {e}"` —— 直接把"大模型拒绝了请求（HTTP 401）：API Key 无效
     或已过期"、"进管理控制台的「API 配置」"这种运维话术念给学生；
  2. 「思考过程」里的轨迹文案（`phase`）十几条写着"不等模型判断""从模型回话抠课"
     "模型自称已提交"——思考过程是本项目的卖点，学生默认看得见；
  3. 判定层"依据"那一栏的 `SOURCE_MEANING["llm"]` = "语义层（问了一次大模型，只做分类）"，
     由 student.html 的 `dt.source_note || dt.source` 渲染进对话。

⚠️ 这个文件**不用 grep 找字符串**（本项目踩过：grep 会被注释骗）。
所有静态判据都走 AST：只取字典里 `phase` / `answer` / `summary` 这几个键的**字符串常量值**，
注释和 docstring 天然不在里面。剩下的判据靠真发请求。

同样重要的一条：**翻译只发生在发给学生的那一刻**。用量流水（管理员看的）必须照旧
存技术原文，不然这道门等于把管理员也一起蒙了 —— 所以 E 批专门钉这一点。
"""
import ast
import io
import json
import os

from _harness import Checker, make_client, sandbox, title
from app.store import set_settings

MAIN = "app/main.py"
CLIENT = "app/llm/client.py"
GATEWAY = "app/llm/gateway.py"
INTENT = "app/agent/intent.py"
STUDENT = "app/static/student.html"

# 学生屏幕上出现任何一个都算暴露
FORBIDDEN = ["大模型", "DeepSeek", "deepseek", "glm-", "智谱", "OpenAI",
             "API Key", "api_key", "密钥", "管理控制台", "/admin", "模型"]


def _dict_string_values(tree, keys):
    """AST 里所有 {phase|answer|summary: "字符串"} 的值（f-string 的固定片段也算）。"""
    out = []

    def flat(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(flat(v) if isinstance(v, ast.FormattedValue) else flat(v)
                           for v in node.values)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return flat(node.left) + flat(node.right)
        return ""

    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value in keys:
                    s = flat(v)
                    if s:
                        out.append((getattr(n, "lineno", 0), s))
    return out


def _parse(rel):
    return ast.parse(io.open(rel, encoding="utf-8").read())


def _llm_error_kinds(rel):
    """AST 扫 client.py：所有 `raise LLMError(..., kind="x")` 里出现过的 kind 字面量。

    为什么走 AST 而不是正则：正则会把注释/docstring 里的示例也算进来（本项目栽过），
    而这里要的恰恰是"**运行时真会抛出的那批标签**"。
    """
    kinds = set()
    for n in ast.walk(_parse(rel)):
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "LLMError":
            for kw in n.keywords:
                if kw.arg == "kind" and isinstance(kw.value, ast.Constant):
                    kinds.add(str(kw.value.value))
    return kinds


def batch_a_translation_table():
    c = Checker()
    title("A 批 · 学生侧翻译层：每种失败都有一句人话，且那句里不带技术词")
    from app.llm.client import LLMError
    from app.main import _STUDENT_LLM_WORDS, _say_llm_error

    used = _llm_error_kinds(CLIENT)
    c.check("client.py 真的给异常打了 kind 标签（不是靠猜 message 关键字）",
            len(used) >= 7, f"AST 扫到 {len(used)} 个：{sorted(used)}")
    missing = sorted(k for k in used if k not in _STUDENT_LLM_WORDS)
    c.check("每个 kind 都有对应话术（漏一个 = 学生吃到通用那句，没人知道出了新岔子）",
            not missing, f"缺：{missing}")
    c.check("翻译层遇到没登记的 kind 时退回通用话术而不是抛错（演示现场不能崩）",
            _say_llm_error(LLMError("随便什么", kind="没登记过"))
            == "⚠️ " + _STUDENT_LLM_WORDS["unknown"])

    bad = []
    for kind, text in _STUDENT_LLM_WORDS.items():
        for w in FORBIDDEN:
            if w in text:
                bad.append(f"{kind} 含「{w}」")
    c.check("所有学生话术里没有一个技术内脏词", not bad, "｜" + "; ".join(bad))
    c.check("每句都还告诉学生该怎么办（重试 / 叫管理员 / 手动安排），不是干巴巴一句失败",
            all(("再问" in t or "再试" in t or "管理员" in t or "手动" in t or "答不了" in t)
                for t in _STUDENT_LLM_WORDS.values()),
            "｜".join(t[:14] for t in _STUDENT_LLM_WORDS.values()))
    return c


def batch_b_static_literals():
    c = Checker()
    title("B 批 · AST 取证：main/intent 里所有会渲染给学生的 phase/answer 都不含禁词")
    offenders = []
    for rel, extra_keys in ((MAIN, ("phase", "answer", "summary")),
                            (INTENT, ("llm", "sample", "rule", "none"))):
        tree = _parse(rel)
        for line, s in _dict_string_values(tree, tuple(set(extra_keys))):
            hit = [w for w in FORBIDDEN if w in s]
            if hit:
                offenders.append(f"{rel}:{line} 含{hit}：{s[:52]}")
    c.check("没有任何 phase/answer/依据 文案写着「模型/大模型/API Key/管理控制台/密钥/…/admin」",
            not offenders, "｜" + " ; ".join(offenders[:4]))

    # 反向自查：这条 AST 扫描真的看得见东西吗？（空断言防护）
    seen = _dict_string_values(_parse(MAIN), ("phase",))
    c.check("扫描器确实取到了轨迹文案（不是扫了个空集合还报绿）",
            len(seen) >= 25, f"只取到 {len(seen)} 条")
    src = io.open(INTENT, encoding="utf-8").read()
    c.check("判定层「依据」那句改成了学生能懂的话",
            "语义层（这一步做了一次语义判断" in src and "问了一次大模型" not in src)
    return c


def batch_c_e2e_broken_backend():
    c = Checker()
    title("C 批 · 真发：后台三种坏法，学生收到的那句话都不能露内脏")
    from app.llm.client import LLMError
    from app.main import _say_llm_error

    cases = [
        ("no_key", {}, "还没配置大模型密钥"),
        ("auth", {"api_key": "sk-dead", "base_url": "https://api.deepseek.com",
                  "model": "deepseek-chat"}, "大模型拒绝了请求"),
        ("blocked_address", {"api_key": "sk-x", "base_url": "http://127.0.0.1:8000/x",
                             "model": "m"}, "接口地址不合规"),
    ]
    with sandbox():
        for kind, settings, technical in cases:
            set_settings(settings)
            cli = make_client()
            r = cli.post("/api/chat", json={"message": "帮我加个待办：交实验报告",
                                            "session_id": f"t38-{kind}", "module": "chat"})
            d = r.json()
            shown = json.dumps({"answer": d.get("answer"), "trace": d.get("trace")},
                               ensure_ascii=False)
            leaked = [w for w in FORBIDDEN if w in shown]
            c.check(f"{kind}：接口没崩（200），学生看到一句人话",
                    r.status_code == 200, f"{r.status_code} {str(r.text)[:60]}")
            c.check(f"{kind}：返回体（含思考过程）里不含技术内脏词",
                    not leaked, f"露出：{leaked}｜{shown[:90]}")
            # 技术原文别丢：管理员要靠它定位。这里核的是"源头那条技术文案还在 client.py 里"，
            # 也就是收口发生在**发给学生的那一刻**，不是把源头改没了（那样管理员也瞎了）。
            tech_src = io.open(CLIENT, encoding="utf-8").read()
            c.check(f"{kind}：源头技术文案没被删掉（管理员还要靠它定位）",
                    technical in tech_src, technical[:40])
    return c


def batch_d_identity_and_admin():
    c = Checker()
    title("D 批 · 人设红线 + 管理员仍然看得见真原因")
    tree = _parse(MAIN)
    rules = ""
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "ASSISTANT_RULES":
                    rules = ast.unparse(n.value)
    c.check("全局人设里写了「禁止透露自己是什么模型/谁训练的/哪家接口」",
            "禁止透露自己是什么模型" in rules and "谁训练" in rules, rules[:60])
    c.check("并给了被问时的具体答法（不许编厂商，也不许摆一句「我很先进」）",
            "底层的实现我不清楚" in rules and "不编厂商" in rules)
    src = io.open(MAIN, encoding="utf-8").read()
    c.check("这条红线拼在**所有**学生端模块的提示词上（不是逐个模块各写一遍）",
            "base_prompt + FILE_HINT + ASSISTANT_RULES" in src)

    g = io.open(GATEWAY, encoding="utf-8").read()
    c.check("网关记的仍是技术原文（管理员的用量表能定位问题）",
            "err = _redact(str(e)" in g, "网关没按 str(e) 记")
    m = src
    c.check("学生出口不再直接用 str(e) 当话术",
            'f"⚠️ {e}"' not in m and '"answer": str(e)' not in m)
    return c


def batch_e_greeting_and_ui():
    c = Checker()
    title("E 批 · 开头语换成欢迎型，且不再宣称「选一个模块开始」")
    src = io.open(STUDENT, encoding="utf-8").read()
    c.check("首屏欢迎语提到的是「直接说一句」，不是让学生找入口",
            "欢迎回来" in src and "直接说一句" in src)
    c.check("旧那句「选一个模块开始」全站消失（三个栏目已共用一条流，留着就是骗人）",
            "选一个模块开始" not in src)
    c.check("问答栏开场也是欢迎型", "欢迎来聊" in src)
    c.check("日程栏（落地页）开场带上了能干什么（空档摊给你挑）",
            "我把空档摊给你挑" in src or "空着的时间段摊给你挑" in src)
    # 设置面板那一格仍然如实写着"模型与 API"——那是学生自己配 Key 的地方，
    # 藏起来才是骗人。这条钉住边界：**对话**不暴露，**配置界面**照旧说清楚。
    c.check("配置界面不许被顺手一起改掉（那里必须说清楚在配什么）",
            "模型与 API" in src)
    return c


def main():
    results = [batch_a_translation_table(), batch_b_static_literals(),
               batch_c_e2e_broken_backend(), batch_d_identity_and_admin(),
               batch_e_greeting_and_ui()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 学生侧不暴露技术底座结果：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
