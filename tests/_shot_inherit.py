"""截图验收：刚说过的事名要**继承**下来（报障图 2 那一幕）。

复现学生截图里的三轮：

    学生：我想去游泳，帮我安排时间
    管家：你想安排在哪天？还是我直接帮你按本周的空闲时间找找？
    学生：这周安排一个时间        ← 这一句没有"游泳"
    系统：要安排的是什么事？      ❌ 报障：他刚刚才说过

修好之后：第三句的事名从上下文继承过来 → **直接敲定一段、出单条确认条**，
不许再反问。第二张截图是点【确认加入】之后，日程里真的多出《游泳》。

另外一个半句话的场景也截一张：只说「你帮我找一个合适的时间」——
系统不许把「找合适」这种残渣印到卡片上，只问"要安排的是什么事"。

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

            # —— 图 2 那一幕：说了事名 → 回一句只带时间的话 ——
            send(page, "我想去游泳，帮我安排时间")
            page.wait_for_timeout(2500)
            shot(page, "37_inherit_ask")

            send(page, "这周安排一个时间")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible",
                                   timeout=20000)
            page.wait_for_timeout(1500)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "38_inherit_card")

            fr = page.frame_locator(".cf-inline .cf-frame")
            body = fr.locator("#cfBody").inner_text()
            print("  确认条内容：", body.replace("\n", " / ")[:160])
            ok = "游泳" in body
            print(f"  {'✅' if ok else '❌'} 卡片上是「游泳」（事名继承成功）")

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(800)
            shot(page, "39_inherit_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date
                                              + ' ' + t.start + '-' + t.end);
            }""")
            print("  待办：", todos)
            print(f"  {'✅' if any('游泳' in t for t in todos) else '❌'}"
                  " 点完确认，日程里真出现了《游泳》")

            # —— 半句话：不许把「找合适」这种残渣印到卡片上 ——
            send(page, "你帮我找一个合适的时间")
            page.wait_for_timeout(2500)
            shot(page, "40_half_sentence")
            # 聊天区在 student.html 里叫 #log（不是 #chat）；
            # 而且刚刚切到了日程视图，直接 inner_text() 会因为元素不可见而超时，
            # 所以用 JS 把文字取回来。
            txt = page.evaluate("document.getElementById('log').innerText")
            bad = "找合适" in txt
            print(f"  {'✅' if not bad else '❌'}"
                  " 半句话没被当成事件名（卡片上不会出现「找合适」）")

            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
