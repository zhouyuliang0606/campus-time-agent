"""截图验收：**思考过程**（每条回复下面的「🧠 我是这么想的」）。

要证明的三件事（对应学生 2026-09-30 报障「没有展示思考过程」）：
  ① 助手回复下面**真的画出了**轨迹块（标题「🧠 我是这么想的」），而且默认是展开的；
  ② 轨迹里**写了"把这句听成什么"**——这是学生核对"它有没有听错"的唯一凭据；
  ③ 设置里那个开关**真能关掉**（关掉之后不再画）——尊重原来"别吓到非技术评委"的顾虑。

留三张截图：
    15_trace_todo   —— 办事（"帮我加个游泳周四19:00"）→ 🧠 + 卡片轨迹
    16_trace_query  —— 问句（"图书馆几点开门"）→ 🧠 + 问答轨迹
    17_trace_off    —— 关掉开关后 → 干净的样子

⚠️ 跑在 e2e 的临时数据副本上（`_e2e_env.e2e_server` 会复制一份 app/data 并单独起服务器），
   演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def send(page, text):
    page.fill("#text", text)
    page.click("#send")
    page.wait_for_timeout(4000)          # 有 key 时要等一次真模型调用


def last_trace(page):
    """最后一条助手消息里的轨迹块文本（没有就回空串）。"""
    return page.evaluate("""() => {
        const blocks = document.querySelectorAll('#log .trace');
        if (!blocks.length) return '';
        return blocks[blocks.length - 1].innerText;
    }""")


def last_msg_count(page):
    return page.evaluate("() => document.querySelectorAll('#log .msg').length")


def main():
    with e2e_server() as srv:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1280, "height": 1000})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(srv.url + "/student", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            # ---------- ① 办事：轨迹必须画出来，且带"听成什么" ----------
            send(page, "帮我加个游泳周四19:00")
            t1 = last_trace(page)
            print("  轨迹：", t1.replace("\n", " / ")[:150])
            assert t1, "助手回复下面**没有**轨迹块（思考过程没展示出来）"
            assert "我是这么想的" in t1, f"轨迹块的标题不对：{t1[:60]}"
            assert "我把这句听成" in t1, f"轨迹里没写「听成了什么」：{t1[:80]}"
            # 默认必须是**展开**的：收起来等于又没展示（学生报障的就是"看不到"）
            opened = page.evaluate(
                "() => { const b = document.querySelectorAll('#log .trace');"
                " return b.length ? b[b.length-1].open : false; }")
            assert opened, "轨迹块默认是收起的 —— 那跟没展示一样"
            print("  ✅ 轨迹块画出来了、默认展开、写了「听成什么」")
            shot(page, "15_trace_todo")

            # ---------- ② 问句：另一条完全不同的路也要有 ----------
            n0 = last_msg_count(page)
            send(page, "图书馆几点开门")
            assert last_msg_count(page) > n0, "发第二句之后没有新消息"
            t2 = last_trace(page)
            print("  轨迹：", t2.replace("\n", " / ")[:150])
            assert t2 and "我把这句听成" in t2, f"问答那条路没带轨迹：{t2[:80]}"
            print("  ✅ 问答分支也带上了")
            shot(page, "16_trace_query")

            # ---------- ③ 开关：关掉之后不再画 ----------
            # 走真实路径：点左侧「⚙️ 设置」→ 渲染出开关 → 取消勾选。
            # （不直接写 localStorage —— 那样测的是脚本自己，不是页面上的开关。）
            # ⚠️ 用 nth(4) 而不是 click：`.sb-item` 同时被侧栏和手机底部栏使用，
            #    选择器会命中两个元素，严格模式下必然报"resolved to 2 elements"。
            _navs = page.locator(".sb-item")
            assert _navs.nth(4).inner_text().strip().endswith("设置"), \
                f"第 5 个导航不是「设置」：{_navs.nth(4).inner_text()[:20]}"
            _navs.nth(4).click()
            page.wait_for_timeout(600)
            cb = page.locator("#setTrace")
            assert cb.count() == 1, "设置页里没有「展示思考过程」这个开关"
            assert cb.is_checked(), "开关默认状态应该是**开**（学生要的默认展示）"
            cb.uncheck()
            page.wait_for_timeout(200)
            assert page.evaluate("() => localStorage.getItem('campustime_trace')") == "0", \
                "开关没写进本机设置"
            _navs.nth(0).click()          # 回到「校园问答」，输入框才在
            page.wait_for_timeout(500)
            n1 = last_msg_count(page)
            send(page, "我都有啥待办来着")
            n2 = last_msg_count(page)
            assert n2 > n1, "关掉开关之后消息都不出了（那是改坏了，不是关掉）"
            t3 = last_trace(page)
            print("  关掉后最后一条的轨迹：", (t3 or "（没有轨迹块）")[:60])
            assert not t3 or "我把这句听成" not in t3, \
                "开关关掉了，可思考过程还在画 —— 开关是摆设"
            print("  ✅ 开关真的能关（关掉后不再画轨迹）")
            shot(page, "17_trace_off")

            assert not errs, f"页面有 JS 报错：{errs[:2]}"
            print("  ✅ 页面无 JS 报错")
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
