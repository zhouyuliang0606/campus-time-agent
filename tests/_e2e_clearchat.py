"""端到端：真实浏览器走一遍「清理与 AI 客服的记录」。

要验的四件事（都是学生投诉过的原话场景）：
  · 页面上得有【清空记录】的入口，学生点得着；
  · 点下去先弹确认窗，【取消】什么都不发生；
  · 点【清空记录】记录才真没，而且**刷新之后不会又冒出来**
    （早先那个老毛病：清了跟没清一个样，一按 F5 旧记录全回来了）；
  · 清理范围只限聊天记录，课表和待办一根汗毛都不少。

跑在独立的临时数据副本上（见 _e2e_env），演示数据一个字节都不动。
"""
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import e2e_server

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


def msg_count(page):
    return page.locator("#log .msg").count()


def sid(page):
    return page.evaluate("localStorage.getItem('campustime_session')")


def say(page, text):
    box = page.locator("#text").first
    box.click()
    box.fill(text)
    box.press("Enter")
    page.wait_for_timeout(6000)


def open_reset(page):
    page.locator("#clearChat").click()
    page.wait_for_timeout(400)


def wait_mask(page, timeout=6000):
    try:
        page.wait_for_selector("#chatResetMask.show", state="visible", timeout=timeout)
        return True
    except Exception:
        n = msg_count(page)
        tail = [page.locator("#log .msg").nth(i).inner_text().replace("\n", " / ")[:160]
                for i in range(max(0, n - 3), n)]
        print("  ⚠️ 弹窗没出来，页面最后显示的是：")
        for t in tail:
            print("     ·", t)
        return False


def main():
    with e2e_server() as srv:
        base = srv.url
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/student", wait_until="networkidle")
            page.wait_for_timeout(800)

            # —— ① 入口得在，学生点得着 ——
            check("输入框旁边有【清空记录】按钮", page.locator("#clearChat").count() == 1)
            check("按钮有解释性提示",
                  "清空" in (page.locator("#clearChat").get_attribute("title") or ""))

            sid_before = sid(page)
            n0 = msg_count(page)
            check("刚打开时只有开场白", n0 == 1, f"{n0} 条")

            # —— ② 先跟管家聊几句，把记录堆起来 ——
            say(page, "帮我看下这周的空档")
            n1 = msg_count(page)
            check("聊过一轮后记录多了", n1 > n0, f"{n0} → {n1} 条")

            # 顺手记下周表和待办数量，稍后要断言"清理不动它们"
            page.evaluate("""async () => {
                const [t, d] = await Promise.all([
                    fetch('/api/timetable').then(r => r.json()),
                    fetch('/api/todos?date=' + new Date().toISOString().slice(0,10)).then(r => r.json())
                        .catch(() => ({items: []}))
                ]);
                window.__tt = (t && t.courses) ? t.courses.length : -1;
                window.__td = (d && (d.todos || [])) ? ((d.todos || []).length) : -1;
            }""")
            tt_before = page.evaluate("window.__tt")
            td_before = page.evaluate("window.__td")

            # —— ③ 点按钮弹窗，先测【取消】——
            open_reset(page)
            check("点按钮弹出确认窗", wait_mask(page))
            summary = page.locator("#chatResetSummary").inner_text()
            check("弹窗说清要清掉几条", str(n1 - 1) in summary, summary[:60].replace("\n", " "))
            # 注意路径要收窄到 #chatResetMask 里：页面上还有一张"清空课表"的弹窗，
            # 里面也有 .dlg-warn，按 class 抓 .first 抓到的是**另一张卡**上的文字。
            warn = page.locator("#chatResetMask .dlg-warn").inner_text()
            check("弹窗说明课表日程不受影响", "不受影响" in warn, warn.replace("\n", " ")[:60])
            check("弹窗上有【取消】", page.locator("#chatResetCancel").count() == 1)
            check("弹窗上有【清空记录】", page.locator("#chatResetOk").count() == 1)

            page.locator("#chatResetCancel").click()
            page.wait_for_timeout(500)
            check("点【取消】弹窗关闭",
                  not page.evaluate("document.getElementById('chatResetMask').classList.contains('show')"))
            check("点【取消】记录一条没少", msg_count(page) == n1, f"仍 {msg_count(page)} 条")

            # Esc 也要能关
            open_reset(page)
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
            check("按 Esc 也能关掉弹窗",
                  not page.evaluate("document.getElementById('chatResetMask').classList.contains('show')"))

            # —— ④ 再点一次，走【清空记录】——
            open_reset(page)
            check("再次弹出确认窗", wait_mask(page))
            page.locator("#chatResetOk").click()
            page.wait_for_timeout(2000)

            check("点【清空记录】弹窗关闭",
                  not page.evaluate("document.getElementById('chatResetMask').classList.contains('show')"))
            # 清完之后屏幕上该剩两条：开场白 + 一句"已经帮你清掉了"的回执。
            # 早先这里写成"只剩开场白 1 条"，结果红了一条——那是算漏了回执那句，
            # 不是前端出 bug。现在改成盯"旧消息没了"这个本质，条数也一并写清楚。
            n_after = msg_count(page)
            body_now = page.locator("#log").inner_text()
            check("旧的那句聊天内容已经从屏幕上消失", "看下这周的空档" not in body_now,
                  body_now.replace("\n", " ")[:60])
            check("屏幕上只剩下开场白和清空的回执", n_after == 2, f"{n1} → {n_after} 条")
            check("会话 id 换了（不换的话刷新又把旧的捞回来了）",
                  sid(page) != sid_before, f"{sid_before} → {sid(page)}")

            # —— ⑤ 最关键：刷新之后旧记录不许回来 ——
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1200)
            check("刷新后聊天记录没有回来", msg_count(page) == 1, f"{msg_count(page)} 条")
            body = page.locator("#log").inner_text()
            check("旧的那句话确实找不回来了", "看下这周的空档" not in body,
                  body.replace("\n", " ")[:60])

            # —— ⑥ 课表和待办不受影响 ——
            page.evaluate("""async () => {
                const [t, d] = await Promise.all([
                    fetch('/api/timetable').then(r => r.json()),
                    fetch('/api/todos?date=' + new Date().toISOString().slice(0,10)).then(r => r.json())
                        .catch(() => ({items: []}))
                ]);
                window.__tt2 = (t && t.courses) ? t.courses.length : -1;
                window.__td2 = (d && (d.todos || [])) ? ((d.todos || []).length) : -1;
            }""")
            check("课表一门没少", page.evaluate("window.__tt2") == tt_before,
                  f"{tt_before} → {page.evaluate('window.__tt2')} 门")
            check("待办一条没少", page.evaluate("window.__td2") == td_before,
                  f"{td_before} → {page.evaluate('window.__td2')} 条")

            # 清空完还能接着聊，新记录要能正常落库
            say(page, "帮我看下这周的空档")
            check("清空后还能接着聊，新记录正常出现", msg_count(page) > 1, f"{msg_count(page)} 条")

            check("页面没有 JS 报错", not errors, str(errors[:2]))
            browser.close()

    print(f"\n===== 清理聊天记录端到端：通过 {passed} 项，失败 {failed} 项 =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
