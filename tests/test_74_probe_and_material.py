# -*- coding: utf-8 -*-
"""#50 的护栏：意图诊断面板关不掉 + 材料轨迹说假话 + 全角标点读不出时间。

三件出自同一张真机截图（2026-10-21 16:37），但根因在三处不同的地方，所以按批钉：

F（50-C，**这次唯一影响功能的**）中文输入法下「19：30-21：30」是默认打法，而所有时间正则
  都按半角冒号写 ⇒ 整条钟点读不出 → 材料被判"没有时间" → 回一句「里面没有一条写着日期或钟点」
  （那句还在说假话：日期它明明读到了）。修法一处：`planner._FW2HW` + `_normalize_clock` 开头一刀
  + 材料入口洗整段（日期走 `_pick_date`，不过 `_normalize_clock`）。
G（50-B）`_read_step` 在 1757 行就跑了，材料分支两千行之后才 return，中间没人覆写 ⇒
  折叠的轨迹写着「听成 问事」而回话是材料分支的固定文案 —— **诊断说 A、实际走 B**。
H（50-A）`classifyProbe` 只会 `add("show")`，全文件没有一处 remove ⇒ 点开就永久占着输入框下面，
  用户原话「点了不能关掉，好像按钮没啥用」。纯前端，只能静态钉，但必须钉。

⚠️ 日期一律 `today + N` 现算，绝不写死：写死就是挂账 #69 那族 —— 跨过午夜它变成"过去"，
   而"不许排在过去的日子"会把整条材料丢掉。本轮实测真踩到（同一句隔一夜结果就不同）。
"""
import datetime
import json
import os

from _harness import Checker, make_client, sandbox, title

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(PROJ, "app", "main.py")
PLANNER = os.path.join(PROJ, "app", "modules", "planner.py")
STUDENT = os.path.join(PROJ, "app", "static", "student.html")

code = 0


def _md(days):
    """今天 + days 天，写成材料里那种「10月25日」。"""
    d = datetime.date.today() + datetime.timedelta(days=days)
    return f"{d.month}月{d.day}日", d.strftime("%Y-%m-%d")


def _full(s):
    """数字与冒号换全角（模拟从 Word / 微信粘过来的料）。"""
    return "".join(chr(ord(ch) + 0xFEE0) if "0" <= ch <= "9"
                   else ("：" if ch == ":" else ch) for ch in s)


def _block(days=(3, 4), full=False):
    d1, d2 = _md(days[0])[0], _md(days[1])[0]
    t = (f"下周考试安排：\n1. 离散数学 {d1} 19:30-21:30 教三301\n"
         f"2. 高等数学 {d2} 20:00-21:00 教二205")
    return _full(t) if full else t


def batch_f_fullwidth():
    c = Checker()
    title("F. 50-C：全角冒号/破折号/数字（真机那份材料的主因）")
    from app.modules import material, planner

    iso1 = _md(3)[1]

    half = planner.parse_time_spec(f"{_md(3)[0]} 19:30-21:30 离散数学")
    full = planner.parse_time_spec(f"{_md(3)[0]} 19：30-21：30 离散数学")
    c.check("E1 全角冒号的钟点读得出来（主因本身）",
            full.get("start") == "19:30" and full.get("end") == "21:30" and full.get("clock_ok"),
            f"start={full.get('start')} end={full.get('end')}")

    bad = []
    for name, t in [("全角破折号", f"{_md(3)[0]} 19:30—21:30 离散数学"),
                    ("全角数字", _full(f"{_md(3)[0]} 19:30-21:30 离散数学")),
                    ("全角冒号+破折号", f"{_md(3)[0]} 19：30—21：30 离散数学"),
                    ("全角波浪号", f"{_md(3)[0]} 19：30～21：30 离散数学")]:
        sp = planner.parse_time_spec(t)
        if not (sp.get("start") == "19:30" and sp.get("end") == "21:30"):
            bad.append(f"{name}={sp.get('start')}-{sp.get('end')}")
    c.check("E2 同一族四种写法都读得出（**end 不许静默丢**，修前它就是只丢 end 然后按 60 分钟排）",
            not bad, "、".join(bad))

    items, missing = material.extract_deterministic(_block(full=True))
    it_half, miss_half = material.extract_deterministic(_block())
    c.check("E3 全角整段材料 → 出 2 条、日期钟点都对（真机那份就是死在这一步）",
            len(items) == 2
            and items[0]["date"] == iso1 and items[0]["start"] == "19:30"
            and items[0]["end"] == "21:30",
            f"{len(items)} 条 / items={items}")
    c.check("E3b 全角与半角的 missing **也得一致**（只钉 items 的话，全角专属的漏项会悄悄溜过去）",
            missing == miss_half, f"全角={missing} 半角={miss_half}")
    c.check("E4 半角那份**逐字不变**（新加的规范化不许动老路径）",
            json.dumps(it_half, ensure_ascii=False, sort_keys=True)
            == json.dumps(items, ensure_ascii=False, sort_keys=True),
            f"半角={it_half}")
    c.check("E4b 半角本身也没被改坏：parse_time_spec 与全角给同一个 start",
            half.get("start") == full.get("start") == "19:30", f"{half.get('start')}")

    src = open(PLANNER, encoding="utf-8").read()
    # ⚠️ 判"某个写法不许出现"一律**只看代码行**：解释"为什么不用它"的注释里必然带着那个词，
    #    按整份文件查字符串 = 自己红自己（E8b 与 E10 本轮各撞了一次）。
    code_lines = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    c.check("E8 全角表只许有一份（调用点再抄一张 = 第二份真相，施工单 §六.3）",
            src.count("str.maketrans") == 1 and src.count("_FW2HW = ") == 1,
            f"maketrans {src.count('str.maketrans')} 处")
    c.check("E8b 没偷偷用 NFKC 整句归一（那会连书名号/顿号一起改，标题抽取跟着变 = 另一族 bug 的入口）",
            "unicodedata" not in code_lines,
            "查的是**调用**，不是单词 —— 注释里写着它正是为了说明不许用")

    from app.modules.planner import has_concrete_span
    c.check("E9 两处**不在 normalize 下游**的消费者得自己认全角（has_concrete_span 这一处）",
            has_concrete_span("已经排好了，19：30—21：30")
            and not has_concrete_span("你点确认后系统才会入库"),
            "全角时段没认出来 / 或把解释流程那句误认成时段")

    # ⚠️ 判"某句文案不许再出现"必须**只看代码行**：我改文案时写的注释正好把旧那句引用了一遍，
    #    按整份文件查字符串 = 自己造出一处假红（本轮实测撞到的）。
    mn_all = open(MAIN, encoding="utf-8").read()
    mn_code = "\n".join(ln for ln in mn_all.splitlines() if not ln.strip().startswith("#"))
    c.check("E10 失败文案不许再说假话（缺一样 ≠ 两样都没有）",
            "没有一条写着日期或钟点" not in mn_code
            and "没有一条同时写着日期和钟点" in mn_code,
            "那句假话又回到回话里了")
    return c


def batch_g_material_trace():
    c = Checker()
    title("G. 50-B：材料分支的轨迹必须说材料")
    with sandbox():
        client = make_client()
        r = client.post("/api/chat", json={"message": _block(), "session_id": "t74-g"})
        d = r.json()
    opts = d.get("options") or []
    phases = " ".join(str(s.get("phase") or "") for s in d.get("trace") or [])
    c.check("前提先钉住：这句真的走材料分支（出的是多选卡）—— 红在前提不算红在结论",
            bool(opts) and opts[0].get("kind") == "todo_add_batch",
            f"kind={opts[0].get('kind') if opts else '没出卡'}")
    # ⚠️ 别只查「一段材料」四个字：第二步 `_MATERIAL_PHASE` 是「📋 认出**一段材料** → …」，
    #    它天然带着这四个字 —— 把覆写整句删掉，只查字面的断言照样绿（变异 M2 实测撞到的）。
    #    要钉的是"听成什么"那一句。
    c.check("E5 轨迹里有一句「我把这句听成 **一段材料**」，且没有「听成 问事」",
            "听成 **一段材料**" in phases and "听成 **问事**" not in phases
            and "听成 问事" not in phases, phases[:160])
    src = open(MAIN, encoding="utf-8").read()
    c.check("E5b 文案只有一份（定义 1 处 + 覆写点 1 处 = 两个出口共用；各写一份字面量迟早漂移）",
            src.count("_MATERIAL_READ_STEP") == 2 and src.count('"一段材料"') == 0,
            f"出现 {src.count('_MATERIAL_READ_STEP')} 次")
    # ⚠️ 别用 `src.find("if _is_material:")` 当锚点 —— 全文件有**好几处**同名判据，
    #    第一处是路由那一带，切出来的段里根本没有这两个 return（我第一版就是这么红给自己看的）。
    at = src.find("meta[\"read\"] = dict(_MATERIAL_READ_STEP)")
    r1 = src.find("return _material_empty_answer")
    r2 = src.find("return _material_answer(")
    c.check("E5c 覆写在**两个出口之前**（放在 return 之后就永远不生效，而且没人会发现）",
            at != -1 and -1 < r1 < at + 900 and -1 < r2 < at + 900 and at < r1 and at < r2,
            f"覆写@{at} 空出口@{r1} 出卡出口@{r2}")
    return c


def batch_h_probe():
    c = Checker()
    title("H. 50-A：意图诊断面板要能关（纯前端，只能静态钉）")
    src = open(STUDENT, encoding="utf-8").read()
    c.check("E6 `probeOut` 既有 add(\"show\") 也有 remove(\"show\")（以前只有开、没有关）",
            'classList.add("show")' in src and 'classList.remove("show")' in src,
            "全文件找不到收起那一步")
    i = src.find("function closeProbe")
    c.check("E6b 收起必须**清空正文**（只藏不清，下次点开会闪一眼上一句的结果）",
            i != -1 and 'probeBody").innerHTML = ""' in src[i:i + 700], "")
    c.check("E7 面板里有 probeClose、用 addEventListener 绑定、没有内联 onclick（esc.js 禁区）",
            'id="probeClose"' in src
            and 'getElementById("probeClose").addEventListener("click", closeProbe)' in src
            and 'onclick="closeProbe' not in src, "")
    j = src.find("async function classifyProbe")
    seg = src[j:j + 2600]
    c.check("E7b 请求在途时不切换（关了回包还往里写 = 自己又弹出来），且 finally 里摘掉 busy",
            'out.dataset.busy === "1") return' in seg and "delete out.dataset.busy" in seg, "")
    c.check("E7c 换栏目时顺手收起（不然它把上一栏的结论挂在新一栏的输入框下面）",
            "closeProbe()" in src[src.find("function switchNav"):src.find("function switchNav") + 700],
            "")
    cp = src[src.find("function closeProbe"):src.find("async function classifyProbe")]
    cf = src[src.find("async function classifyProbe"):]
    c.check("E7d 那颗按钮带 aria-pressed 且随开/关翻面（键盘与读屏用户得知道它是开关，不是一个动作）",
            'id="probeBtn" aria-pressed="false"' in src
            and 'btn.setAttribute("aria-pressed", "true")' in cf
            and 'probeBtn").setAttribute("aria-pressed", "false")' in cp, "")
    c.check("E7e 结果写进 probeBody、× 那颗住在 probe-head 里常驻（每次重写结果都顺手重拼 ×，迟早漏一次）",
            'id="probeBody"' in src and 'body.innerHTML' in src and src.count("probeClose") >= 2, "")
    return c


BATCHES = [("F 批（50-C 全角标点）", batch_f_fullwidth),
           ("G 批（50-B 材料轨迹）", batch_g_material_trace),
           ("H 批（50-A 面板可收起）", batch_h_probe)]


def main():
    global code
    print("test_74_probe_and_material：#50 三件（全角时间 / 材料轨迹 / 诊断面板）")
    print("=" * 60)
    for name, fn in BATCHES:
        rc = fn().summary(name)
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
