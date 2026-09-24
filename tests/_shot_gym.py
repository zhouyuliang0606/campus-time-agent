"""截图验收：管家只在文字里写「提案」，界面上到底有没有按钮。

复现的就是学生那张截图：日程面板里管家回——
    好，那我按这个出个提案：
    - 任务：健身
    - 时间：周一 16:30~18:00
    - 范围：本周
    提案这就发给你，点一下【确认】就入库了！
—— 文案里说得像提案已经发了，可界面上**连一个按钮都没有**。

怎么复现：那段话是**历史**里的（页面从 /api/conversation 捞回来渲染），
所以先用 srv.seed_conversation 把它种进副本，再用 localStorage 把 session id
定成同一个，页面加载出来就是那一幕。学生接着回一句「可以」，
要求：确认条出现在消息下方 → 点【确认加入】→ 日程里真能看到《健身》。

三段截图：
    6_gym_before —— 光有文字、没有按钮（报障时的样子）
    7_gym_confirm —— 回一句「可以」后，确认条挂出来了
    8_gym_done   —— 点完【确认加入】，日程里出现《健身》

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotgym"

# 截图里管家那段原文，逐字抄下来
BUTLER_TEXT = (
    "好，那我按这个出个提案：\n"
    "- 任务：健身\n"
    "- 时间：周一 16:30~18:00\n"
    "- 范围：本周\n"
    "提案这就发给你，点一下【确认】就入库了！"
)


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        # 种下"管家只在文字里写了方案"的那段历史
        srv.seed_conversation(SID, [
            {"role": "user", "content": "帮我安排一下健身"},
            {"role": "assistant", "content": BUTLER_TEXT},
        ])

        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            # 页面一进来就把 session id 定死，跟上面种的那段历史对上
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)

            # 切到日程面板（学生报障时就停在这儿）
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            shot(page, "6_gym_before")

            bars_before = page.locator(".cf-inline").count()
            print(f"  回「可以」之前，确认条数量：{bars_before}（报障时的样子就是 0）")

            box = page.locator("#text").first
            box.click()
            box.fill("可以")
            box.press("Enter")

            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1500)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "7_gym_confirm")

            fr = page.frame_locator(".cf-inline .cf-frame")
            print("  确认条内容：", fr.locator("#cfBody").inner_text().replace("\n", " / ")[:140])
            print("  按钮：", fr.locator("#cfOk").inner_text(), "/",
                  fr.locator("#cfCancel").inner_text())

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            # 9/28 是**下周**一，面板默认停在"本周"，得往后翻一周才看得见这条待办，
            # 不然截图里空空的，看不出"真的写进去了"。
            page.locator("#schNext").click()
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "8_gym_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("健身")]
            print("  ✅ 写进去了" if hit else "  ❌ 日程里还是没有《健身》")
            b.close()
            return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(main())
