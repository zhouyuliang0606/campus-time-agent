"""截图验收：「没有代办页…没有出现弹窗」——确认条＋周表＋月表。

复现的是学生那张截图（报障原话：「**没有代办页，我要的是和更改课表一样的
在个人数据库的日程周表和月表显示，没有出现弹窗**」）。那一幕是三轮：

    我：   加
    管家： 好的，帮你把「游泳」加到周一待办里，时间就按咱们说的 16:00~17:30 来。
          确认一下：**周一 16:00 游泳（1.5 小时）**，对吧？你回个「确认」，系统就入库了。
    我：   确认
    管家： 已提交，等系统入库后周一待办里就会多出「游泳 16:00~17:30」这一条。

三件事全是假的：**卡片没挂、库没写、还说"已提交"**。

根因不是"信息不够"：把那段话拆开喂进去，任务名/日期/时段**三样全抠得出来**，
是 `parse_todo_from_reply` 死认「事项：/时间：」那种固定格式，散文式方案
一个字段都对不上 → 整个方案被判成"捞不出来" → 兜底两手空空 → 掉给模型 →
模型回了那句"已提交"。

四段截图：
    29_prose_before —— 报障时的样子（只有文字、没有按钮，确认条数量 0）
    30_prose_card   —— 回一句「确认」后，确认条挂出来了
    31_prose_week   —— 点【确认加入】→ **周表**里周一那一栏多出「16:00-17:30 游泳」
    32_prose_month  —— 切到**月表**，9/28 那格也能看到它

⚠️ 「加」和管家那段方案是**种进历史**的（跟学生截图一致），学生那一句「确认」
   是真发出去的。这一步走系统确定性链路（捞回方案补条），不依赖模型发挥。

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotprose"

# 截图里管家那段原文，逐字抄下来
BUTLER_TEXT = (
    "好的，帮你把「游泳」加到周一待办里，时间就按咱们说的 16:00~17:30 来。\n\n"
    "确认一下：**周一 16:00 游泳（1.5 小时）**，对吧？你回个「确认」，系统就入库了。"
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
        srv.seed_conversation(SID, [
            {"role": "user", "content": "加"},
            {"role": "assistant", "content": BUTLER_TEXT},
        ])

        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 940})
            page.add_init_script(
                f"localStorage.setItem('campustime_session', '{SID}');")
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)

            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(600)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(400)
            shot(page, "29_prose_before")
            print(f"  回「确认」之前，确认条数量：{page.locator('.cf-inline').count()}"
                  f"（报障时的样子就是 0；下面也没有任何待办）")

            box = page.locator("#text").first
            box.click()
            box.fill("确认")
            box.press("Enter")

            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=40000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "30_prose_card")

            fr = page.frame_locator(".cf-inline .cf-frame")
            print("  确认条内容：", fr.locator("#cfBody").inner_text().replace("\n", " / ")[:160])

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(1000)
            # 9/28 是**下周**一，面板默认停在"本周"（9/21~9/27），
            # 得往后翻一周才看得见这条待办，不然截图里空空的。
            page.locator("#schNext").click()
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "31_prose_week")
            week = page.evaluate("""() => {
                const el = document.querySelector('#schWeek, .week-grid, #schBody');
                return el ? el.innerText.replace(/\\n+/g, ' | ') : '';
            }""")
            print("  周表里（已翻到下周）：", week[:220])

            page.locator("#tabMonth").click()
            page.wait_for_timeout(1200)
            # 月表格子窄，每天只列前 2 项、多的折进「还有 N 项…」——
            # 9/28 那天有 3 门课，待办会被折进去。月表自己的做法是**点某一天**
            # 看当天待办（`renderSchDay`），所以这里点开 9/28 再截。
            day_cell = page.locator('#schMonth .d[data-date="2026-09-28"]')
            day_cell.click()
            page.wait_for_timeout(800)
            day_cell.scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            shot(page, "32_prose_month")
            month = page.evaluate("""() => {
                const el = document.querySelector('#schMonth, #monthGrid');
                return el ? el.innerText.replace(/\\n+/g, ' | ') : '';
            }""")
            detail = page.evaluate("""() => {
                const el = document.getElementById('schDayDetail');
                return el ? el.innerText.replace(/\\n+/g, ' | ') : '';
            }""")
            print("  月表里 9/28 那一格的当天待办：", detail[:120])
            print("  月表当年那格显示的是（前 2 项 + 折叠）：",
                  month[month.find("28"):month.find("28") + 60] if "28" in month else "(没找到)")
            month_ok = "游泳" in detail

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("游泳")]
            ok = bool(hit) and "游泳" in week and month_ok
            print("  ✅ 卡片出来了、周表看得到、月表当天也查得到" if ok
                  else "  ❌ 卡片没出来 / 或者周表月表里没显示")
            b.close()
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
