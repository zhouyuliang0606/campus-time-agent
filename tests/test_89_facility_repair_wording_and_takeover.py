"""#71 的护栏：设施报修**屏幕上那句话与栏目**必须跟着算出来的类型走；触发词硬信号必须真接管。

批次分工（变异跑法看「红的是哪几条名字」）：

  A 批 · 屏幕上那句话：`_wo_kind` 算出「设施报修」时，answer 不许再说「你的快递/外卖可能出了问题」，
      `module` 也不许写死 `express`。#54-J 当年修了卡上的 `kind` 字段，**没修学生看到的那一句**，
      于是「宿舍网络坏了要报修」（分镜 :188 的演示句）在屏幕上还是快递/外卖 —— 判据对、话错。
      ⚠️ 这一批全走 HTTP，因为要验的是**出口那句话**，不是函数返回值。
  B 批 · 判据本体：`_FACILITY_RE` 补「网络/宽带 + 故障态」之后，既不放过「网络报修」，
      也不误伤「网络课在哪个教室」；五种 `_wo_kind` 各归各家；`asks_repair_eta` 只认进度问句。
  C 批 · 接管条件：`main.py` 那一档从白名单（`in (None, INTENT_CHAT)`）改成黑名单
      （`not in _CUSTOM_YIELD_INTENTS`）。**这一批里 C3/C4 是功能测试** —— 本单真正的坑是
      「模型判对了 custom_record，反倒成为不接管的理由」，而那只在**配了密钥**时才看得见，
      沙箱里 `_sem_intent=None` 新老两种写法都通 ⇒ 只写静态断言就是拿假绿归档（单子 §一.0 自己点过这一口）。
      所以 C3 用 `patch_llm` 把语义层钉成 custom_record，逼出那条真机路径。

⚠️ 两条自省（都在这单里吃过）：
  1. **期望为空/不含某字样，必须配一份"它本该出现"的正控**（A3 / A5 / C5）。
  2. **别拿 `looks_like_question` 那两份同名的账当一份**：`app/agent/intent.py:189` 那份
     含「多久/几点/多少」，`app/modules/planner.py:4130` 那份只认 吗/？/? —— `main.py` 用的是前者。
     施工单 §0.2 的表按后者算，于是得出「报修要多久 不是问句」。B6 钉住这件事。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, make_client, patch_llm, sandbox  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def read(rel):
    p = os.path.join(ROOT, rel)
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def code_only(rel):
    """读源码但**去掉注释行**（本项目第五次教训：拿注释里的字符串当"代码里有"）。"""
    src = read(rel)
    return "\n".join(ln for ln in src.split("\n") if not ln.strip().startswith("#"))


def ask(cli, msg, sid):
    d = cli.post("/api/chat", json={"message": msg, "session_id": sid,
                                    "module": "planner"}).json()
    ph = [t.get("phase", "") for t in (d.get("trace") or []) if isinstance(t, dict)]
    return d, ph


def kinds(d):
    return [o.get("kind") for o in (d.get("options") or []) if isinstance(o, dict)]


def wo_kind_of(d):
    for o in (d.get("options") or []):
        if isinstance(o, dict) and o.get("kind") == "work_order_report":
            return o.get("wo_kind")
    return None


def seed_repair_service():
    """沙箱里摆一项**后勤自建**服务（触发词就用真机上那三个字，别造一个新词）。"""
    import app.store as S
    S.add_service({"id": "hq9", "name": "电梯报修", "triggers": ["电梯坏了", "电梯困人了"],
                   "collect_fields": ["楼栋"], "reply_template": "已登记报修。",
                   "table": "repair"}, dept="后勤")
    return S.service_of_module("custom:hq9")


# ---------------------------------------------------------------- A 批
def batch_a_answer_wording():
    c = Checker()
    with sandbox():
        cli = make_client(admin_token="t71-admin")

        d1, ph1 = ask(cli, "宿舍网络坏了要报修", "t71-facility")
        c.check("A1 设施报修那句屏幕上不许再说「快递/外卖」（判据算出 设施报修 ⇒ 话也要跟着换）",
                "快递" not in d1.get("answer", "") and "外卖" not in d1.get("answer", "")
                and "设施坏了" in d1.get("answer", ""),
                str(d1.get("answer", ""))[:70])
        c.check("A2 设施报修归 `repair` 栏，不许再写死塞进 `express`（快递/外卖那一栏）",
                d1.get("module") == "repair" and wo_kind_of(d1) == "设施报修",
                "module=%s kind=%s" % (d1.get("module"), wo_kind_of(d1)))
        d2, _ = ask(cli, "快递丢了怎么办", "t71-parcel")
        c.check("A3 **丢件那族照旧**（正控）：「快递丢了怎么办」仍出卡、仍归 express、"
                "话里仍说快递/外卖 —— A1 是**分派**不是删除，改错了方向这一条就红",
                "work_order_report" in kinds(d2) and d2.get("module") == "express"
                and "快递" in d2.get("answer", ""),
                "module=%s kinds=%s" % (d2.get("module"), kinds(d2)))
        d3, _ = ask(cli, "报修要多久", "t71-eta")
        c.check("A4 「报修要多久」是**问进度**，不许替学生挂上报卡（真机上它一边印「这句先按问事答」、"
                "一边就把卡挂出来了 —— 说法与做的事矛盾）",
                "work_order_report" not in kinds(d3), str(kinds(d3))[:60])
        d4, _ = ask(cli, "我的快递丢了吗", "t71-yesno")
        c.check("A5 是非问句照旧出卡（**正控**：A4 那道让路不许把闸口撑大 —— 问句 ≠ 问进度）",
                "work_order_report" in kinds(d4), str(kinds(d4))[:60])
        c.check("A6 轨迹里那句「依据」也跟着类型走（设施报修不许再写「快递/外卖问题上报」）",
                any("设施报修，硬信号" in p for p in ph1)
                and not any("快递/外卖问题上报" in p for p in ph1),
                str(ph1[:2])[:120])
    return c


# ---------------------------------------------------------------- B 批
def batch_b_judges():
    c = Checker()
    from app.modules.planner import (_FACILITY_RE, asks_repair_eta,
                                     looks_like_work_order_report as W)
    hit = [("网络报修", True), ("宿舍网络坏了要报修", True), ("宽带断了要报修", True),
           ("电梯坏了要报修", True), ("网络课在哪个教室", False), ("网络中心在哪", False)]
    ok = [(s, b, bool(_FACILITY_RE.search(s))) for s, b in hit]
    bad = [(s, want, got) for s, want, got in ok if want != got]
    c.check("B1 设施词表补了「网络/宽带 + 故障态」，且**没写成孤零零一个「网络」**"
            "（「网络课」「网络中心」不许算设施报修）",
            not bad, str(bad)[:90])
    kinds5 = {"宿舍网络坏了要报修": "设施报修", "快递丢了": "疑似丢件", "包裹破损了": "包裹破损",
              "取件码异常": "取件码异常", "外卖丢失了": "外卖丢失"}
    got5 = {s: W(s) for s in kinds5}
    c.check("B2 `_wo_kind` **五种**各归各家（第五种「设施报修」是 #54 加的，兜底不该再吞掉它）",
            all(got5[s] == k for s, k in kinds5.items()), str(got5)[:120])
    eta = {"报修要多久": True, "电梯坏了几天能修好": True, "快递丢了怎么办": False,
           "我的快递丢了吗": False, "电梯坏了": False}
    got_eta = {s: asks_repair_eta(s) for s in eta}
    c.check("B3 `asks_repair_eta` 只认**进度/时长**问句（「怎么办」「丢了吗」不算 —— 它们要的是入口）",
            all(got_eta[s] == v for s, v in eta.items()), str(got_eta)[:120])
    import ast
    tree = ast.parse(read("app/modules/planner.py"))
    body = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "looks_like_work_order_report":
            body = ast.get_docstring(node) or ""
    c.check("B4 docstring 与 `_work_order_kind` 的实际返回值对齐（五种都要在，别再写一份四种的旧账 —— "
            "#69 那两处撒谎注释同族。取 docstring 走 AST，不拿正则去猜缩进）",
            bool(body) and all(k in body for k in ("疑似丢件", "包裹破损", "取件码异常",
                                                   "外卖丢失", "设施报修")),
            body.replace("\n", " ")[:80])
    main_src = read("app/main.py")
    c.check("B5 出卡那一支的 `module` 与 `answer` **两处都**按类型分派（只改一处就是半个修复："
            "话对了栏目还错着，学生那条单子在快递栏里）",
            main_src.count('"repair" if _facility_wo else "express"') == 1
            and "_facility_wo = _wo_kind == \"设施报修\"" in code_only("app/main.py"), "")
    from app.agent.intent import looks_like_question as qi
    from app.modules.planner import looks_like_question as qp
    c.check("B6 全项目有两份同名 `looks_like_question`，`main.py` 用的是 intent 那一份"
            "（它认「多久」；planner 那份只认 吗/？/? —— 施工单 §0.2 的表按后者算才得出"
            "「报修要多久 不是问句」。这条钉的是**别把两份当一份**）",
            qi("报修要多久") is True and qp("报修要多久") is False
            and "from app.agent.intent import" in main_src
            and "looks_like_question" in main_src.split("from app.agent.intent import")[1][:400],
            "intent=%s planner=%s" % (qi("报修要多久"), qp("报修要多久")))
    import app.store as S
    with sandbox():
        S.add_service({"id": "sh1", "name": "短的", "triggers": ["电梯"],
                       "collect_fields": ["楼栋"], "reply_template": "已登记。",
                       "table": "repair"}, dept="后勤")
        S.add_service({"id": "lg1", "name": "长的", "triggers": ["电梯坏了"],
                       "collect_fields": ["楼栋"], "reply_template": "已登记。",
                       "table": "repair"}, dept="后勤")
        got = S.match_service_by_trigger("电梯坏了") or {}
        c.check("B7 触发词命中按**最长**算赢（「电梯」与「电梯坏了」都在库里时，这句必须归「长的」；"
                "改成「先命中的算赢」就会选到「短的」—— 那是把学生送到错的台账）",
                got.get("id") == "lg1", str(got)[:60])
    return c


# ---------------------------------------------------------------- C 批
def batch_c_takeover():
    c = Checker()
    gated = code_only("app/main.py")
    c.check("C1 接管条件已从白名单改成黑名单（旧写法 `_eff_intent in (None, INTENT_CHAT)` 一行都不许留）",
            "_eff_intent not in _CUSTOM_YIELD_INTENTS" in gated
            and "_eff_intent in (None, INTENT_CHAT)" not in gated, "")
    m = re.search(r"_CUSTOM_YIELD_INTENTS = \((.*?)\)", read("app/main.py"), re.S)
    names = re.findall(r"INTENT_[A-Z_]+", m.group(1)) if m else []
    c.check("C2 让位清单是**六个办事意图**、而且**不含 custom_record**"
            "（正控：清单真解析得出六项，不是被注释拼出来的空串）",
            len(names) == 6 and "INTENT_CUSTOM_RECORD" not in names
            and all(x in names for x in ("INTENT_ADD_TODO", "INTENT_REMOVE_TODO",
                                         "INTENT_RETIME", "INTENT_DONE",
                                         "INTENT_CLEAR_TIMETABLE", "INTENT_LIST_COURSE")),
            str(names)[:110])
    with sandbox():
        svc = seed_repair_service()
        c.check("C5 沙箱里那项「电梯报修」确实存在（**非空正控**：C3/C4 的接管/让路都要靠它，"
                "服务没种进去时 C3 会因为'没服务'而假绿）",
                bool(svc) and (svc or {}).get("name") == "电梯报修", str(svc)[:70])
        cli = make_client(admin_token="t71-admin")
        # 真机形状：模型把这句判成 custom_record（显式栏目是 planner，所以老写法在这里不接管）
        restore = patch_llm('{"intent":"custom_record","module":"custom:hq9","confidence":0.9}')
        try:
            d, ph = ask(cli, "电梯坏了", "t71-cr")
            c.check("C3 语义层给出 custom_record 时**触发词档必须接管**（真机就死在这一格："
                    "模型判对了反倒成为不接管的理由，学生只拿到一句「爬楼当锻炼」的闲聊）",
                    "custom:hq9" in (d.get("module") or "")
                    or any("电梯报修" in str(p) for p in ph),
                    "module=%s kinds=%s" % (d.get("module"), kinds(d)))
        finally:
            restore()
        restore = patch_llm('{"intent":"list_course","module":"planner","confidence":0.9}')
        try:
            d, _ = ask(cli, "电梯坏了", "t71-yield")
            c.check("C4 语义层判成**查课**（list_course）时不许被报修劫走 —— 这才是让位清单的牙。"
                    "⚠️ 这一条第一版我用的是 `add_todo`＋「…帮我加个待办」，实测**清单清空它照样绿**"
                    "（那句在更早的规则层分支就被待办那档接走了，根本到不了这道闸）⇒ 空判据，换成"
                    "真能走到闸口的意图。与 C3 配成一对：同是「电梯坏了」，custom_record 接管、"
                    "list_course 让路（#70 那条『判据要有一条必须命中的样本喂』）",
                    "custom:hq9" not in (d.get("module") or ""),
                    "module=%s kinds=%s" % (d.get("module"), kinds(d)))
        finally:
            restore()
    return c


def main():
    print("test_89_facility_repair_wording_and_takeover：#71 报修那句话 / 触发词接管")
    print("=" * 60)
    code = 0
    for label, fn in (("A 批（屏幕上那句话与栏目）", batch_a_answer_wording),
                      ("B 批（判据本体）", batch_b_judges),
                      ("C 批（接管条件）", batch_c_takeover)):
        try:
            rc = fn().summary(label)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[%s] 崩了：%s" % (label, e))
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
