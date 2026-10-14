"""上传课表的「出卡通道」：每一条自己打 /api/chat 的前端路径，都必须把卡渲染出来。

报障原话（2026-10-07 学生）：「第一次不弹第二次追问才弹」。

根因不在判定层（后端第一句就出了卡），而在**前端旁路**：
「我的日程」面板的「📤 上传课表」按钮不走 sendMsg，而是自己 fetch /api/chat，
拿到 d.options 后只更新了一行状态文字、没调 renderOptions ——
后端明明出了「确认修改」卡，前端把卡悄悄丢了。学生再追问一次，
追问走 sendMsg（那里有 renderOptions），卡才出来。

第二个坑：iframe 从兜底高度（170px）长高到真实高度时会把底部撑出可视区，
卡挂出来了学生不滚动也看不见——跟"没弹卡"是一回事。

本文件全部做**源码断言**（不启浏览器）：钉住"旁路必须渲染 options"与
"确认条长高后必须滚进视野"这两件事，以后任何一条新旁路都不许再漏。
"""
from _harness import Checker, title


def _seg(src: str, start: str, end: str) -> str:
    """取 start 之后到 end 之间的源码片段（找不到就返回空串，让断言自己红）。"""
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find(end, i)
    return src[i: j if j > i else i + 3000]


def batch_a_panel_upload_renders_card():
    c = Checker()
    title("1. 「我的日程」上传课表：自己 fetch /api/chat 也必须把卡挂出来")
    src = open("app/static/student.html", encoding="utf-8").read()
    seg = _seg(src,
               'getElementById("ttFilePanel").addEventListener("change"',
               'getElementById("ttFilePanel").value = ""')
    c.check("找到上传课表的 change 处理器", bool(seg))
    c.check("该路径必须把 options 交给 renderOptions（否则卡被静默丢弃）",
            "renderOptions(" in seg)
    c.check("管家那条消息要接住 addMsg 的返回值（renderOptions 要挂在它下面）",
            "= addMsg(botName()" in seg)
    c.check("拿到卡时提示语必须指向确认条，不再是「请在下方聊天里确认」",
            "请在下方确认条上点【确认修改】" in seg and
            seg.count("请在下方聊天里确认后再导入") == 0)
    return c


def batch_b_other_chat_bypasses():
    c = Checker()
    title("2. 其余自己打 /api/chat 的旁路：逐个核对有没有丢卡")
    plan = open("app/static/plan.html", encoding="utf-8").read()
    c.check("plan.html 上传课表路径渲染了 timetable_change（showTtConfirm）",
            'o.kind === "timetable_change"' in plan and "showTtConfirm(" in plan)
    # 主聊天通道本身不许被改坏：sendMsg 里必须有 renderOptions
    stu = open("app/static/student.html", encoding="utf-8").read()
    seg = _seg(stu, "async function sendMsg", "finally {")
    # 2026-10-15 刀 07 起 sendMsg 改成 SSE：变量从 `data` 改叫 `d`（done 信封），
    # 渲染仍在 done 回调里，所以这条跟着改名 —— 不变量是"options 一定被渲染出来"，
    # 而不是"某个变量叫什么"。（同类的"断言文案与改名不同步"账：test_23 那条 6a。）
    c.check("sendMsg 主通道仍把 options 渲染出来",
            "renderOptions(el, d.options)" in seg)
    c.check("而且是在 done 回调里渲染（不是拿流式预览当定稿）",
            "onDone: function (d)" in seg and "renderOptions(el, d.options)" in seg.split("onDone:")[1])
    return c


def batch_c_confirm_in_viewport():
    c = Checker()
    title("3. 确认条长高后必须滚进视野（不然挂了也等于没挂）")
    src = open("app/static/student.html", encoding="utf-8").read()
    seg = _seg(src, 'd.type === "height"', "}")
    c.check("height 消息处理里要有 scrollIntoView",
            "scrollIntoView(" in seg)
    c.check("iframe 高度仍按内容真实高度调整（不回退兜底值）",
            "Math.min(560" in seg)
    return c


def main():
    results = [
        batch_a_panel_upload_renders_card(),
        batch_b_other_chat_bypasses(),
        batch_c_confirm_in_viewport(),
    ]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 出卡通道总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
