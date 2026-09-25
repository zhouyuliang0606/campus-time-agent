"""截图验收：「改完自动刷新日程…每次都不弹确认的框」——确认条＋周表＋月表。

复现的是学生那张截图（报障原话：「**改完自动刷新日程，和删除课表一个逻辑，
都是在日程做改变，每次都不弹确认的框**」）。那一幕三轮：

    我：   周四早上吧
    管家： 提案好了，就一条：
           **周四 08:00~09:00 健身（1 小时）**
           确认的话，回我一句「确认添加」就行，系统会帮你入库。想换时间也随时说。
    我：   确认
    管家： 收到，你回「确认」后由系统执行入库，我这边不直接改数据。
           稍等确认条生效就好。周四 08:00~09:00 健身，练完记得吃口早饭再上课。

又是"卡片没挂、库没写"。两个断点（第四轮修掉）：

  ① `claims_proposal_sent` 不认「提案好了」「系统会帮你入库」「确认添加」
     这三个说法 → 兜底第一步就不启动（现在都认）；
  ② 就算认了，任务名「健身」是**跟在时段后面**的（`08:00~09:00 健身`），
     前两路（「」引号 / 把 X 加到）都落在盲区（现在补了第三路）。

四段截图：
    33_span_before —— 报障时的样子（只有文字、没有按钮，确认条数量 0）
    34_span_card   —— 回一句「确认」后，确认条挂出来了
    35_span_week   —— 点【确认加入】→ **周表**里周四那一栏多出「08:00-09:00 健身」
    36_span_month  —— 切到**月表**，10/1 那一格里**直接**就能看到「08:00-09:00 健身」

⚠️ 36 那张盯的是**月表格子里的顺序**（第四轮改的）：格子只放得下 2~3 条，
以前是"课程在前、待办在后"，课多的那天刚加的待办会被折进「还有 N 项…」，
学生看到就成了"我加完了、日程里却没有"。现在**待办在前、课程在后**，
刚加的那条第一眼就在。所以这一版不再需要"点开那一天"才看得见 ——
但仍然点一下，顺带把下面 `#schDayDetail` 的当天待办也念出来当第二处证据。

⚠️ 「周四早上吧」和管家那段方案是**种进历史**的（跟学生截图一致），
   学生那一句「确认」是真发出去的。这一步走系统确定性链路（从它的话里捞回方案
   补条），**一次都不问模型**，所以没有密钥也照样跑。

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"
SID = "shotspan"
DAY = "2026-10-01"        # 周四（9/28 是下周一，10/1 就是下周四）

# 截图里管家那两段原文，逐字抄下来
BUTLER_PROPOSAL = (
    "提案好了，就一条：\n\n"
    "**周四 08:00~09:00 健身（1 小时）**\n\n"
    "确认的话，回我一句「确认添加」就行，系统会帮你入库。想换时间也随时说。"
)
BUTLER_RECEIPT = (
    "收到，你回「确认」后由系统执行入库，我这边不直接改数据。"
    "稍等确认条生效就好。\n\n周四 08:00~09:00 健身，练完记得吃口早饭再上课。"
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
            {"role": "user", "content": "帮我加个健身"},
            {"role": "assistant", "content": "好，加在哪天？"},
            {"role": "user", "content": "周四早上吧"},
            {"role": "assistant", "content": BUTLER_PROPOSAL},
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
            shot(page, "33_span_before")
            print(f"  回「确认」之前，确认条数量：{page.locator('.cf-inline').count()}"
                  f"（报障时的样子就是 0；下面也没有任何待办）")

            box = page.locator("#text").first
            box.click()
            box.fill("确认")
            box.press("Enter")

            page.wait_for_selector(".cf-inline .cf-frame", state="visible",
                                   timeout=40000)
            page.wait_for_timeout(1800)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            shot(page, "34_span_card")

            fr = page.frame_locator(".cf-inline .cf-frame")
            print("  确认条内容：",
                  fr.locator("#cfBody").inner_text().replace("\n", " / ")[:160])

            fr.locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(1000)
            # 10/1 是**下周**四，面板默认停在"本周"（9/21~9/27），
            # 得往后翻一周才看得见这条待办，不然截图里空空的。
            page.locator("#schNext").click()
            page.wait_for_timeout(1200)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            shot(page, "35_span_week")
            week = page.evaluate("""() => {
                const el = document.querySelector('#schWeek, .week-grid, #schBody');
                return el ? el.innerText.replace(/\\n+/g, ' | ') : '';
            }""")
            print("  周表里（已翻到下周）：", week[:220])

            page.locator("#tabMonth").click()
            page.wait_for_timeout(800)
            # 月表默认停在"这个月"（2026-09），而这条待办在 10/1 ——
            # 切到月表后 `schMonthOffset` 还是 0，得再点一次右箭头翻到 10 月，
            # 不然 `[data-date="2026-10-01"]` 这个格子压根不在 DOM 里。
            page.locator("#schNext").click()
            page.wait_for_timeout(1200)
            day_cell = page.locator(f'#schMonth .d[data-date="{DAY}"]')
            day_cell.wait_for(state="visible", timeout=15000)
            # 这一格**里面**直接写了什么 —— 这就是"待办优先"改动的直接证据。
            cell_text = day_cell.inner_text().replace("\n", " / ")
            print("  月表 10/1 那一格直接显示的是：", cell_text)
            day_cell.click()
            page.wait_for_timeout(800)
            detail = page.evaluate("""() => {
                const el = document.getElementById('schDayDetail');
                return el ? el.innerText.replace(/\\n+/g, ' | ') : '';
            }""")
            print("  点开那天后，月历下面的当天待办：", detail[:120])
            # 截图要把**那一格**放在画面中间。
            # `scroll_into_view_if_needed()` 在这儿不管用 —— 月历 5 行，格子在第一行，
            # 它一进来就在"可见"范围内（哪怕只露一角），于是不滚，截出来格子在画面外
            # （上一版就是这么拍到"从 5 号开始"的）。用 scrollIntoView({block:'center'})
            # 强制居中，才保证这格真的在图上。
            page.evaluate(
                f"""() => {{
                    const el = document.querySelector('#schMonth .d[data-date="{DAY}"]');
                    if (el) el.scrollIntoView({{block: 'center'}});
                }}""")
            page.wait_for_timeout(500)
            shot(page, "36_span_month")

            todos = page.evaluate("""async () => {
                const d = await (await fetch('/api/todos')).json();
                return (d.todos || []).map(t => t.title + '@' + t.date + ' ' + t.start + '-' + t.end);
            }""")
            print("  日程里的待办：", todos)
            hit = [t for t in todos if t.startswith("健身") and DAY in t]
            # 三处都得对上：① 卡片真的挂出来了 ② 周表有 ③ **月表格子里直接就有**
            #（③ 是第四轮改的"待办优先"，以前得点开那天才看得到）
            ok = bool(hit) and "健身" in week and "健身" in cell_text and "健身" in detail
            print("  ✅ 卡片出来了、周表不翻点击就看得见、月表格子里直接就有" if ok
                  else "  ❌ 卡片没出来 / 周表月表里没显示（或月表格子里仍看不到待办）")
            b.close()
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
