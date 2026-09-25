"""截图验收：「看不见 X」的报障——系统先核实，不甩锅不二选一。

复现学生截图里那一幕：学生说「我现在没有看见日程显示周二游泳代办项目啊」，
管家以前会掉给模型，回一段"系统推送出了问题/得让管理端看一下/你二选一"。
修好之后系统**真查库**，然后按查到什么说什么：

    ① 说过要加、没写入 → 诚实承认「还没写进去」+ 确认条补挂出来；
    ② 点完确认、真写进去了 → 「在的」，告诉他哪天几点；
    ③ 找的其实是课 → 指到周表。

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


def send(page, text):
    box = page.locator("#text").first
    box.click()
    box.fill(text)
    box.press("Enter")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(500)

            # —— ① 说过要加（第一轮出过卡）→ 报障 → 补挂确认条 ——
            send(page, "我想去游泳，帮我安排时间")
            page.wait_for_timeout(2500)

            send(page, "我现在没有看见日程显示周二游泳代办项目啊")
            page.wait_for_timeout(2500)
            shot(page, "41_notsee_card")
            txt = page.evaluate("document.getElementById('log').innerText")
            ok1 = ("游泳" in txt and "还没写进去" in txt
                   and "管理端" not in txt and "推送" not in txt)
            print(f"  {'✅' if ok1 else '❌'} 报障换来确认条（诚实承认还没写入），"
                  "全程没提管理端/推送")

            # 点【确认加入】→ 真写进日程
            fr = page.frame_locator(".cf-inline .cf-frame")
            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)

            # —— ② 真写进去了再问一遍 → 「在的」 ——
            send(page, "我现在怎么还是没看到游泳")
            page.wait_for_timeout(2500)
            shot(page, "42_notsee_found")
            txt = page.evaluate("document.getElementById('log').innerText")
            ok2 = "在的" in txt and "游泳" in txt
            print(f"  {'✅' if ok2 else '❌'} 真写入后 → 如实说「在的」+ 哪天几点")

            # —— ③ 找的是课 → 指到周表 ——
            send(page, "怎么没有周一的高数")
            page.wait_for_timeout(2500)
            shot(page, "43_notsee_course")
            txt = page.evaluate("document.getElementById('log').innerText")
            ok3 = "高等数学" in txt and "周表" in txt
            print(f"  {'✅' if ok3 else '❌'} 找的是课 → 指到周表")

            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
