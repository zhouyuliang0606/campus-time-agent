"""截图验收：候选时段——AI 列几段、学生打勾、其他自填、落库即刷新日程。

复刻的就是学生那条驳回（截图 + 原话）：
    「我定的太严了，你改一下，由 ai 帮我去挑选合适时间，**进行列举**，
      采用和删除课表时同样的弹框，内容变成那几个时间的选择或者其他
      （这个选项可以由我来自行补充），**由我打勾**，进行增加，
      **增加确认完立刻刷新日程**」

四段截图（接在 16_pick_retime 后面编号）：
    17_slots_ask    —— 学生说「那你帮我加一个健身在周四」，看它列了什么
    18_slots_self   —— 在弹框的「其他时间」里自己写一句（学生要的"自行补充"）
    19_slots_done   —— 打勾 + 点【加入日程】，日程里真出现《健身》
    20_slots_again  —— 只回一句「确认」而不勾选：一条都不写，把候选条再挂出来

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotslots"


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
            page = b.new_page(viewport={"width": 1200, "height": 1020})
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(500)

            # ① 只说"哪天" → **先出二选一卡**（本轮规格的第一站）；
            #    他选「时间我自己定」→ 才摊出那天的几段空档让他挑。
            ask(page, "那你帮我加一个健身在周四")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)

            fr = page.frame_locator(".cf-inline .cf-frame").last
            print("  第一站（二选一卡）：",
                  fr.locator("#cfBody").inner_text().replace("\n", " / ")[:160])
            n_modes = fr.locator(".mode-btn").count()
            print("  两条路按钮：", n_modes)
            fr.locator('.mode-btn[data-mode="self"]').click()
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "17_slots_ask")
            print("  选了『我自己定』之后：",
                  fr.locator("#cfBody").inner_text().replace("\n", " / ")[:160])

            body = fr.locator("#cfBody").inner_text().replace("\n", " / ")
            n_slots = fr.locator(".slot-cb").count()
            has_other = fr.locator("#cfOther").count()
            print(f"  勾选框 {n_slots} 个 / 「其他时间」输入框 {has_other} 个 / "
                  f"按钮：{fr.locator('#cfOk').inner_text()}")
            listed = "✅ 列了几段" if n_slots >= 2 else "❌ 只给了一个点"
            other_ok = "✅ 有自填口子" if has_other >= 1 else "❌ 没有「其他」"

            # ② 「其他时间」里自己写一句 + 勾一段
            fr.locator("#cfOther").fill("周日早上八点到九点")
            page.wait_for_timeout(600)
            print("  自填之后按钮：", fr.locator("#cfOk").inner_text())
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "18_slots_self")

            fr.locator(".slot-cb").nth(0).check()
            page.wait_for_timeout(400)
            print("  勾一段后按钮：", fr.locator("#cfOk").inner_text())

            # ③ 点【加入日程】→ 两条一起落库 + 日程面板当场刷新
            fr.locator("#cfOk").click()
            page.wait_for_timeout(2800)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            page.locator("#schNext").click()      # 周四/周日都在下周
            page.wait_for_timeout(1400)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "19_slots_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("健身")]
            both = "✅ 勾的那段 + 自填的那段都写进去了" if len(hit) >= 2 else \
                   f"❌ 只写进去 {len(hit)} 条"
            conv = page.evaluate("""async () => {
                const sid = localStorage.getItem('campustime_session');
                const d = await (await fetch('/api/conversation/' + sid)).json();
                return (d.messages || []).map(m => m.role + ':' + m.content);
            }""")
            receipt = [m for m in conv if "已加入日程" in m]
            print("  回执：", receipt[-1][:90] if receipt else "（没有回执）")

            # ④ 只回「确认」但不勾 → 一条都不写，把候选条再挂一遍
            ask(page, "加个游泳")
            page.wait_for_timeout(2200)
            ask(page, "确认")
            page.wait_for_timeout(2200)
            page.locator(".cf-inline").last.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "20_slots_again")
            fr2 = page.frame_locator(".cf-inline .cf-frame").last
            print("  再挂一遍的内容：",
                  fr2.locator("#cfBody").inner_text().replace("\n", " / ")[:140])
            todos2 = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title);
            }""")
            guarded = "✅ 没瞎写" if "游泳" not in todos2 else "❌ 没勾也写了"
            b.close()

            ok = (n_slots >= 2 and has_other >= 1 and len(hit) >= 2
                  and bool(receipt) and "游泳" not in todos2)
            print(f"\n  {listed} / {other_ok} / {both} / {guarded}")
            print("  " + ("✅ 候选列举 → 打勾 → 其他自填 → 落库即刷新，整条通了"
                          if ok else "❌ 还有一环没通"))
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
