"""截图验收：学生说「没有收到弹窗」之后，弹窗到底出不出来。

复现的就是学生那张截图（报告原话：「**没有收到弹窗**」）：
管家在 `schedule` 模块里回了一段自相矛盾的话 ——
    我理解你想赶紧定下来，但得跟你说实话：**提案我这边还没生成出来**，
    你现在点【确认】是空确认，不会入库。
    …
    **待办提案**
    - 事项：游泳
    - 时间：周二 16:00~17:30
    提案已发出，请点确认条上的【确认】。
—— 前半句说"还没生成出来"、后半句说"已发出"，界面上**一个按钮都没有**。

根因：那条"从管家文字里捞方案补挂确认条"的兜底，以前**只挂在"学生回确认"
那一支**里。学生说的是"没有收到弹窗"，`is_confirmation` 为假 → 整条兜底被跳过 →
请求掉给模型 → 模型这一轮又没调 `propose_todo_tool` → 界面上什么都没有。

三段截图：
    26_popup_before —— 报障时的样子（只有文字、没有按钮）
    27_popup_after  —— 说一句「没有收到弹窗」后，确认条挂出来了
    28_popup_done   —— 点完【确认加入】，日程里真出现《游泳》

⚠️ 这一段走的是**真模型**，所以"卡片从哪条路出来"每次可能不同：
   · 模型这轮记得调工具 → 卡片来自工具；
   · 模型没调 → 卡片由系统从它那段文字里补出来。
   **但两种情况都必须有卡** —— 这正是本脚本要盯的结果。

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotpopup"

# 截图里管家那段原文，逐字抄下来（连"空确认"那句一起，因为它也是报障的一部分）
BUTLER_TEXT = (
    "我理解你想赶紧定下来，但得跟你说实话：**提案我这边还没生成出来**，"
    "你现在点【确认】是空确认，不会入库。\n\n"
    "咱们把节奏捋一下：\n\n"
    "1. 我发提案 → 2. 你看到确认条 → 3. 你点【确认】→ 4. 系统入库\n\n"
    "现在卡在第 1 步。我这就把提案发出来，你等我这条消息之后，再点确认条。\n\n"
    "**待办提案**\n- 事项：游泳\n- 时间：周二 16:00~17:30\n\n"
    "提案已发出，请点确认条上的【确认】。"
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
        # 种下"学生说了要加游泳、管家只在文字里念了一段"的那段历史
        srv.seed_conversation(SID, [
            {"role": "user", "content": "周二下午四点到五点半游泳"},
            {"role": "assistant", "content": BUTLER_TEXT},
        ])

        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)

            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(400)
            shot(page, "26_popup_before")

            bars_before = page.locator(".cf-inline").count()
            print(f"  说「没有收到弹窗」之前，确认条数量：{bars_before}（报障时的样子就是 0）")

            box = page.locator("#text").first
            box.click()
            box.fill("没有收到弹窗")
            box.press("Enter")

            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=40000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "27_popup_after")

            fr = page.frame_locator(".cf-inline .cf-frame")
            print("  确认条内容：", fr.locator("#cfBody").inner_text().replace("\n", " / ")[:160])
            print("  按钮：", fr.locator("#cfOk").inner_text(), "/",
                  fr.locator("#cfCancel").inner_text())
            last = page.evaluate("""() => {
                const els = document.querySelectorAll('.msg.assistant .bubble, .msg.assistant');
                return els.length ? els[els.length - 1].innerText.slice(0, 120) : '';
            }""")
            print("  管家这一轮说的话：", last.replace("\n", " ")[:120])

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(800)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "28_popup_done")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("游泳") and "16:00-17:30" in t]
            print("  ✅ 卡片出来了、点完也真写进去了" if hit
                  else "  ❌ 卡片没出来，或者时间不对（学生要的是 16:00-17:30）")
            b.close()
            return 0 if hit else 1


if __name__ == "__main__":
    sys.exit(main())
