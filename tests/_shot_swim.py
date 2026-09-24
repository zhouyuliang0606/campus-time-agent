"""截图验收：学生说的「周二下午两点到三点游泳」到底走不走系统。

复现的是学生截图里那条链路：管家回「搞定！已经正式写进你的待办啦 ✅」，日程里空空如也。
现在要求：系统出确认条（14:00-15:00）→ 学生点【确认加入】→ 待办里立刻能看到。

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(500)

            box = page.locator("#text").first
            box.click()
            box.fill("帮我安排周二下午两点到三点游泳")
            box.press("Enter")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1500)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "4_swim_confirm")

            fr = page.frame_locator(".cf-inline .cf-frame")
            body = fr.locator("#cfBody").inner_text()
            print("  确认条内容：", body.replace("\n", " / ")[:120])
            print("  按钮：", fr.locator("#cfOk").inner_text(), "/", fr.locator("#cfCancel").inner_text())

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(800)
            shot(page, "5_swim_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  待办：", todos)
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
