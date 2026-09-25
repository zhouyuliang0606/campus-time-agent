"""截图验收：学生只说了「哪天」，界面到底有没有把时间想出来、有没有确认条。

复刻的就是学生那张新截图里投诉的两句话：
    「**没有确认，没有帮我想时间是我问了才说的**」
场景：学生说「那你帮我加一个健身在周四」——
     · 时间不该反过来问他"几点到几点"，而该由系统查空档挑一段排上；
     · 界面上必须真的出现一张能点的确认条（不是只在文字里写一段方案）；
     · 回一句「确认」要能真写进日程。

四段截图（接在 8_gym_done 后面编号）：
    13_pick_before  —— 先说一句"只给了哪天"，看界面长什么样
    14_pick_confirm —— 系统自己挑好时间并挂出确认条（"我看 15:40-17:10 空着"）
    15_pick_done    —— 点完【确认加入】，日程里真出现《健身》
    16_pick_retime  —— 学生嫌这个点不合适、自己报了「下午两点到三点」→ 换成他的点

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotpick"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def ask(page, text):
    box = page.locator("#text").first
    box.click()
    box.fill(text)
    box.press("Enter")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        srv.seed_conversation(SID, [])

        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 980})
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(500)
            shot(page, "13_pick_before")

            # ① 只说"哪天" → **先给二选一卡**（本轮规格的第一站），不反问他几点
            ask(page, "那你帮我加一个健身在周四")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1500)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "14_pick_confirm")

            fr = page.frame_locator(".cf-inline .cf-frame")
            body = fr.locator("#cfBody").inner_text().replace("\n", " / ")
            print("  第一站（二选一卡）：", body[:160])
            print("  两条路按钮：", fr.locator(".mode-btn").count())
            ok_first = "没问" if "几点到几点" not in body else "还在问几点"

            # 他选「时间我自己定」→ 换成候选卡（这一步走的是系统确定性链路）
            fr.locator('.mode-btn[data-mode="self"]').click()
            page.wait_for_timeout(1800)
            print("  选了『我自己定』之后：",
                  fr.locator("#cfBody").inner_text().replace("\n", " / ")[:120])

            # ② 学生嫌这些点不合适、自己报一个 → 换成他说的
            ask(page, "下午两点到三点")
            page.wait_for_timeout(2500)
            bars = page.locator(".cf-inline").count()
            print(f"  报了自己的点之后，确认条数量：{bars}")
            page.locator(".cf-inline").last.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "16_pick_retime")
            fr2 = page.frame_locator(".cf-inline .cf-frame").last
            print("  换时间后的确认条：",
                  fr2.locator("#cfBody").inner_text().replace("\n", " / ")[:160])

            # ③ 点【确认加入】→ 真写进日程
            fr2.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(500)
            page.locator("#schNext").click()      # 周四在下周
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "15_pick_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("健身")]
            print(f"  {ok_first}；",
                  ("✅ 写进去了：" + hit[0]) if hit else "❌ 日程里还是没有《健身》")
            b.close()
            return 0 if (hit and ok_first == "没问") else 1


if __name__ == "__main__":
    sys.exit(main())
