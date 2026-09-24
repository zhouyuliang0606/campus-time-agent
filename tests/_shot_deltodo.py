"""截图验收：删待办——先列清楚，再动手，学生点头才算数。

学生那张报障话的原话：
    「删待办】跟删课是同一套规矩：先列清楚，再动手，学生点头才算数。
     1. 学生说"删掉待办/取消那条安排"：**先把要删的那条原样念给学生听**，
        等他回一句确认或点确认条；
     2. 学生没说清是哪条（"删掉明天那条"、明天有好几条）：**不许猜**，
        把那天的待办列出来，问"是下面哪一条"，列完就停；
     3. 学生说的是课（"去掉周二的高数"）：归课表那条路，别拿待办去套。」

这个脚本按 1 → 2 → 3 走一遍，留四张截图：
    9_del_list     —— 命中好几条：清单列出来、问"是下面哪一条"（一条都没删）
    10_del_confirm —— 学生点名（序号）：确认条挂出来，把那条**原样念一遍**
    11_del_done    —— 点【确认删除】之后，日程里那条真没了、别的一条没动
    12_del_course  —— 说的是课：走课表那条路，不是删待办

跑在临时数据副本上，演示数据一个字节都不动。
"""
import datetime
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotdel"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def main():
    day = (datetime.date.today() + datetime.timedelta(days=4)).isoformat()
    # 同一天两条，专门制造"没说清是哪条"的局面
    todos = [
        {"id": "shot0001", "title": "游泳", "date": day, "start": "14:00", "end": "15:00",
         "note": "", "category": "", "status": "planned", "created_at": "2026-09-25 10:00:00"},
        {"id": "shot0002", "title": "交电费", "date": day, "start": "19:00", "end": "19:30",
         "note": "", "category": "", "status": "planned", "created_at": "2026-09-25 10:05:00"},
    ]

    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos(todos)
        srv.seed_conversation(SID, [])

        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 950})
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(700)

            box = page.locator("#text").first

            def ask(msg):
                box.click()
                box.fill(msg)
                box.press("Enter")
                page.wait_for_timeout(1800)

            # ---------- ② 没说清是哪条 → 列清单，问"是下面哪一条" ----------
            ask("删掉那天的待办")
            page.wait_for_timeout(500)
            page.locator(".msg.bot").last.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "9_del_list")
            text = page.locator(".msg.bot").last.inner_text()
            print("  管家列出来的清单：", text.replace("\n", " / ")[:170])
            left = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title);
            }""")
            print("  此刻待办（**一条都不该少**）：", left)
            assert "游泳" in left and "交电费" in left, "列清单这一步就不该删东西！"

            # ---------- ① 学生点名 → 原样念一遍 + 确认条 ----------
            ask("游泳那条")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1200)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "10_del_confirm")
            fr = page.frame_locator(".cf-inline .cf-frame")
            print("  确认条内容：", fr.locator("#cfBody").inner_text().replace("\n", " / ")[:150])
            print("  按钮：", fr.locator("#cfOk").inner_text(), "/",
                  fr.locator("#cfCancel").inner_text())
            still = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title);
            }""")
            print("  点确认之前：", still)
            assert "游泳" in still, "学生还没点头就删了！"

            # ---------- 点【确认删除】→ 真删，且只删这一条 ----------
            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(800)
            page.locator("#schNext").click()          # 翻到下一天那周，让那条待办露出来
            page.wait_for_timeout(1000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "11_del_done")
            after = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title);
            }""")
            print("  删完待办：", after)
            assert "游泳" not in after, "点确认之后没删掉"
            assert "交电费" in after, "误删了同一天的另一条！"
            print("  ✅ 该删的删了，不该动的一条没动")

            # ---------- ③ 说的是课 → 归课表那条路 ----------
            ask("去掉周二的线性代数")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1200)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "12_del_course")
            fr2 = page.frame_locator(".cf-inline .cf-frame")
            print("  课表那条路的确认条标题：", fr2.locator("#cfTitle").inner_text(),
                  "| 内容：", fr2.locator("#cfBody").inner_text().replace("\n", " / ")[:100])
            tts = page.evaluate("""async () => {
                const d = await (await fetch('/api/timetable')).json();
                return (d.courses || []).length;
            }""")
            print("  周表门数（没点确认，应该还是原来的数）：", tts)
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
