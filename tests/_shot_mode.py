"""截图验收：先问"时间怎么定" + AI 去规划 + 识别真的事情。

复刻的就是学生第三轮那七张截图 + 一段话：
    「**要区分两种，一种是我有时间规划了，一种是我没有时间规划让他帮我安排，
      不要一上来就询问详细时间，先给弹窗，（有时间规划）（还没有，你帮我定），
      用户选择后，针对没时间……**」
    「**核心是 ai 帮我安排时间，ai 去规划时间，然后这个加入代办，
      要识别啥才是真的事情，不是随便拿那一句话就去当代办加入日程了**」

五段截图：
    21_mode_ask   —— 学生「帮我加个游泳，大概一个小时」→ 先给**二选一弹窗**
    22_mode_ai    —— 点「还没定，你帮我挑」→ 系统**规划出一段**，并说清为什么排这儿
    23_mode_done  —— 点【确认加入】→ 日程里真出现《游泳》，面板当场刷新
    24_mode_self  —— 另一条路：点「时间我自己定」→ 换出候选卡（他自己挑）
    25_bad_title  —— 「大概一个小时帮我安排时间」→ 该**追问"要安排什么事"**，不出卡

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

            # ① 有事情名、只是还没定时间 → **第一站是二选一卡**，不是问他几点
            ask(page, "帮我加个游泳，大概一个小时")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "21_mode_ask")
            fr = page.frame_locator(".cf-inline .cf-frame").last
            body1 = fr.locator("#cfBody").inner_text().replace("\n", " / ")
            n_modes = fr.locator(".mode-btn").count()
            print("  二选一卡：", body1[:180])
            print("  两条路按钮数：", n_modes)

            # ② 他点「还没定，你帮我挑」→ 系统真去规划一段，并写清为什么排这儿
            fr.locator('.mode-btn[data-mode="ai"]').click()
            page.wait_for_timeout(2000)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "22_mode_ai")
            body2 = fr.locator("#cfBody").inner_text().replace("\n", " / ")
            has_why = fr.locator(".why").count()
            print("  规划结果：", body2[:200])
            print("  「为什么排这儿」那一块：", has_why)
            planned = "✅ 系统给出了一个点" if fr.locator("#cfOk").count() else "❌ 没出条"

            # ③ 点【确认加入】→ 真落库 + 日程面板当场刷新
            fr.locator("#cfOk").click()
            page.wait_for_timeout(2800)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "23_mode_done")
            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            done = "✅ 点确认后真写进去了" if any(t.startswith("游泳") for t in todos) \
                else "❌ 点了没写进去"

            # ④ 另一条路：他选「时间我自己定」→ 换成候选卡（同一张卡里就地变）
            ask(page, "周四加个健身")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=20000)
            page.wait_for_timeout(1800)
            fr2 = page.frame_locator(".cf-inline .cf-frame").last
            print("  第二条路的二选一卡：", fr2.locator("#cfBody").count(), "（有卡）")
            fr2.locator('.mode-btn[data-mode="self"]').click()
            page.wait_for_timeout(2000)
            page.locator(".cf-inline").last.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "24_mode_self")
            n_slots = fr2.locator(".slot-cb").count()
            print("  换成候选卡：勾选框", n_slots, "个 /",
                  fr2.locator("#cfBody").inner_text().replace("\n", " / ")[:150])
            self_ok = "✅ 他自己定就摊候选" if n_slots >= 2 else "❌ 没摊出候选"

            # ⑤ 只说了时长、没说做什么事 → **追问"要安排什么事"**，一张卡都不出
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

            ok = (n_modes >= 2 and has_why >= 1 and done and self_ok
                  and no_card == "✅ 没拿半句话当代办" and asked_thing.startswith("✅"))
            print(f"\n  {'✅ 先问方式' if n_modes >= 2 else '❌ 没给二选一'} / "
                  f"{'✅ 有规划理由' if has_why >= 1 else '❌ 没说为什么'} / "
                  f"{done} / {self_ok} / {no_card} / {asked_thing}")
            print("  " + ("✅ 二选一 → 两条路 → 规划/候选 → 落库即刷新，整条通了"
                          if ok else "❌ 还有一环没通"))
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
