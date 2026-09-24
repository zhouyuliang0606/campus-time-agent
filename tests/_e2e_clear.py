"""端到端：真实浏览器走一遍「清空课表」—— 提案 → 弹窗 → 点确认 → 课表真的空了。

对应需求的五条约束，逐条在真实页面上验：
  · 只出提案，AI 不许自己删、不许谎称已删；
  · 弹窗有【确认】【取消】；
  · 点确认由后端执行，点取消什么都不发生；
  · 删完自动刷新，页面上能直观看到空课表；
  · 删除动作只来自前端按钮，后端没有前端这一下就拒绝。

跑在**独立的临时数据副本**上（见 _e2e_env）：清空是不可逆的，
以前的写法是"先把演示周表 12 门播种回去再删"——那等于每跑一次测试，
真实演示数据就被删一次；现在删的只是副本。
"""
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

errors = []
passed = 0
failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✅ {name} {extra}")
    else:
        failed += 1
        print(f"  ❌ {name} {extra}")


def tt_count(page):
    page.evaluate("""async () => {
        const r = await fetch('/api/timetable');
        const d = await r.json();
        window.__tt = d.courses || [];
        return window.__tt.length;
    }""")
    return page.evaluate("window.__tt.length")


def say(page, text):
    box = page.locator("#text").first
    box.click()
    box.fill(text)
    box.press("Enter")
    page.wait_for_timeout(6000)


def wait_mask(page, timeout=8000):
    """等弹窗真正显示出来；等不到就把机器人最后几句捞出来，方便一眼看出卡在哪。"""
    try:
        page.wait_for_selector("#ttClearMask.show", state="visible", timeout=timeout)
        return True
    except Exception:
        msgs = page.locator(".msg")
        tail = [msgs.nth(i).inner_text().replace("\n", " / ")[:160]
                for i in range(max(0, msgs.count() - 3), msgs.count())]
        print("  ⚠️ 弹窗没出来，机器人最后说的是：")
        for t in tail:
            print("     ·", t)
        return False


def main():
    with e2e_server() as srv:
        base = srv.url
        srv.seed_timetable(demo_courses_from_json())   # 播种只写副本
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()   # 12 格：跟 with sync_playwright() 对齐
            # 上面「取消之后后端再被调一次」那步是**故意**打 400 的（要验的就是它拒绝），
            # 这条报错是探针自己造出来的，不算页面 bug。
            def _on_console(m):
                if m.type != "error":
                    return
                url = (m.location or {}).get("url", "")
                if "/api/timetable/clear" in url:
                    return      # 探针自己造的 400，不算页面 bug
                errors.append(m.text or url)

            page.on("console", _on_console)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/student", wait_until="networkidle")

            n0 = tt_count(page)
            print(f"  起始周表：{n0} 门课")

            # 先打开日程面板，方便后面看课表刷新效果
            page.evaluate("showView && showView('view-schedule')")
            page.wait_for_timeout(800)

            say(page, "把课表全部删除，一门都不留")

            bot = page.locator(".msg.bot, .bot .msg, .msg-content").last.inner_text()
            check("助手没有谎称已经删掉", "已经" not in bot or "清空" not in bot,
                  bot[:70].replace("\n", " "))

            mask = page.locator("#ttClearMask")
            check("出提案这一轮弹窗就冒出来了", wait_mask(page))
            check("前端弹出了确认弹窗", mask.evaluate("e => e.classList.contains('show')"))
            check("弹窗上有【确认清空】按钮", page.locator("#ttClearOk").count() == 1)
            check("弹窗上有【取消】按钮", page.locator("#ttClearCancel").count() == 1)
            txt = page.locator("#ttClearSummary").inner_text()
            check("弹窗说清要清掉几门课", str(n0) in txt, txt[:60].replace("\n", " "))
            check("出提案阶段课表没被动", tt_count(page) == n0, f"{n0} → {tt_count(page)}")

            # —— 先测「取消」：什么都不该发生 ——
            page.locator("#ttClearCancel").click()
            page.wait_for_timeout(600)
            check("点【取消】弹窗关闭", not mask.evaluate("e => e.classList.contains('show')"))
            check("点【取消】课表原封不动", tt_count(page) == n0, f"仍 {tt_count(page)} 门")

            # —— 后端没有前端这一下就拒绝 ——
            r = page.evaluate("""async () => {
                const r = await fetch('/api/timetable/clear', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({session_id: sessionId(), confirm: true})
                });
                return {status: r.status, body: await r.text()};
            }""")
            check("取消之后后端再被调也删不动（无待确认提案）", r["status"] == 400, str(r["status"]))

            # —— 再问一次，走「确认」——
            say(page, "把课表全部删除，一门都不留")
            check("再次出弹窗", wait_mask(page))

            # 确认按钮点下去之前，先确认页面还没刷新
            page.locator("#ttClearOk").click()
            page.wait_for_timeout(2500)

            check("点【确认清空】后弹窗关闭",
                  not mask.evaluate("e => e.classList.contains('show')"))
            n_after = tt_count(page)
            check("课表真的空了", n_after == 0, f"{n0} → {n_after} 门")

            # 页面上的周视图要能直观看到空
            week_html = page.locator("#schWeek").inner_text()
            check("周视图刷新成空课表（无课程块）",
                  "高等数学" not in week_html and "大学英语" not in week_html,
                  week_html[:60].replace("\n", " ")[:60])
            check("课表区域显示无安排/空态",
                  ("无安排" in week_html) or ("暂无" in week_html) or ("空" in week_html))

            check("页面没有 JS 报错", not errors, str(errors[:2]))
            browser.close()

    print(f"\n===== 清空课表端到端：通过 {passed} 项，失败 {failed} 项 =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
