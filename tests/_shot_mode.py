"""截图验收：直接敲定时间 + AI 规划 + 识别真的事情。

规格沿革（四版，学生每次表态都记着）：
    · 第三版「要区分两种……先给弹窗」（二选一卡）→
    · **第四版（现在）**：学生原话「**不需要先问，直接去安排，重复的确认太麻烦，
      直接敲定结果，主打效率**」——第一站就是系统从真实空档里挑好的**单条**确认条，
      卡上带"为什么排这儿"；嫌点不合适，报个新点就换（`retime` 那条路）。
    · 「识别啥才是真的事情」不变：说不出事名照样追问，绝不拿半句话当代办名。

四段截图：
    21_mode_ask    —— 学生「帮我加个游泳，大概一个小时」→ **直接出单条确认条**（不再先问怎么定）
    22_mode_done   —— 点【确认加入】→ 日程里真出现《游泳》，面板当场刷新
    23_mode_direct —— 「周四加个健身」→ 只说了哪天，同样直接敲定周四那段
    25_bad_title   —— 「大概一个小时帮我安排时间」→ 该**追问"要安排什么事"**，不出卡

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotmode"


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

            # ① 有事情名、只是还没定时间 → **第一站就是敲定好的单条确认条**
            #    （不再先问"你自己定 / 我帮你挑"那一轮）
            ask(page, "帮我加个游泳，大概一个小时")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "21_mode_ask")
            fr = page.frame_locator(".cf-inline .cf-frame").last
            body1 = fr.locator("#cfBody").inner_text().replace("\n", " / ")
            has_why = fr.locator(".why").count()
            n_modes = fr.locator(".mode-btn").count()
            print("  直接敲定的确认条：", body1[:180])
            print("  「为什么排这儿」块数：", has_why,
                  "／二选一按钮数（应为 0）：", n_modes)
            asked = "✅ 第一轮就出了单条" if fr.locator("#cfOk").count() else "❌ 没出条"

            # ② 点【确认加入】→ 真落库 + 日程面板当场刷新
            fr.locator("#cfOk").click()
            page.wait_for_timeout(2800)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "22_mode_done")
            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            done = "✅ 点确认后真写进去了" if any(t.startswith("游泳") for t in todos) \
                else "❌ 点了没写进去"

            # ③ 只说了哪天 → 同样直接敲定（那天的那段空档）
            ask(page, "周四加个健身")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").last.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "23_mode_direct")
            fr2 = page.frame_locator(".cf-inline .cf-frame").last
            body3 = fr2.locator("#cfBody").inner_text().replace("\n", " / ")
            print("  只说哪天的敲定结果：", body3[:160])
            direct = "✅ 说了哪天也直接敲定" if fr2.locator("#cfOk").count() else "❌ 没出条"

            # ④ 只说了时长、没说做什么事 → **追问"要安排什么事"**，一张卡都不出
            ask(page, "大概一个小时帮我安排时间")
            page.wait_for_timeout(2600)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
            shot(page, "25_bad_title")
            last_bot = page.evaluate("""() => {
                const el = document.querySelectorAll('.msg.bot .bubble');
                return el.length ? el[el.length - 1].innerText : '';
            }""")
            print("  管家的回答：", last_bot.replace("\n", " / ")[:160])
            no_card = "✅ 没拿半句话当代办" if "加待办缺细节" not in last_bot else \
                "❌ 漏出内部标记了"
            asked_thing = "✅ 问的是「要安排什么事」" if "什么事" in last_bot else \
                "❌ 没问到点上"
            b.close()

            ok = (asked.startswith("✅") and has_why >= 1 and n_modes == 0
                  and done.startswith("✅") and direct.startswith("✅")
                  and no_card == "✅ 没拿半句话当代办" and asked_thing.startswith("✅"))
            print(f"\n  {asked} / "
                  f"{'✅ 有规划理由' if has_why >= 1 else '❌ 没说为什么'} / "
                  f"{'✅ 不再多问一轮' if n_modes == 0 else '❌ 还有二选一'} / "
                  f"{done} / {direct} / {no_card} / {asked_thing}")
            print("  " + ("✅ 直接敲定 → 落库即刷新 → 只说哪天照样一步到位，整条通了"
                          if ok else "❌ 还有一环没通"))
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
