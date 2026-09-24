"""端到端：真实浏览器里走一遍「删课 → 学生回一句确认 → 课表真的少一门」。

这是为那条踩过坑的链路做的回归验证：学生打"确认"之后，AI 曾经回一句
"我没有权限删除"，还让学生去点一个根本不存在的删除按钮。
现在要求：**一句确认就能落库**，而且落库前后数据要对得上。

跑在独立数据副本上（见 _e2e_env）：它会真的删一门课，
早先这套脚本打的是真实 8000 服务，删的就是要拿去演示的那张表。
"""
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

BASE = ""
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


def timetable_count(page):
    page.evaluate("""async () => {
        const r = await fetch('/api/timetable');
        const d = await r.json();
        window.__tt = d.courses || [];
        return window.__tt.length;
    }""")
    return page.evaluate("window.__tt.length")


def main():
    with e2e_server() as srv:
        BASE = srv.url
        srv.seed_timetable(demo_courses_from_json())   # 播种只写副本
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/student", wait_until="networkidle")

            n0 = timetable_count(page)
            print(f"  起始周表：{n0} 门课")

            # 找到聊天输入框，发一句删课需求
            box = page.locator("#text").first
            if box.count() == 0:
                box = page.locator("textarea, input[type=text]").first
            box.click()
            box.fill("把周一第一节的高等数学去掉")
            box.press("Enter")
            page.wait_for_timeout(6000)

            bot_text = page.locator(".msg.bot, .bot .msg, .msg-content").last.inner_text()
            check("助手没有说「办不到」（旧 bug 话术）",
                  not any(k in bot_text for k in ("我没有权限", "没有工具权限", "手动操作", "麻烦你")))
            check("助手确实说出了要删哪一节", "高等数学" in bot_text, bot_text[:60].replace("\n", " "))

            # 确认入口有两种形态：独立确认页面（弹窗，优先）/ 内嵌卡片（多张提案时）
            mask_shown = "show" in (page.locator("#ttClearMask").get_attribute("class") or "")
            cards = page.locator(".opt-card").count()
            check("界面上出现了确认入口（弹窗或确认卡）", mask_shown or cards >= 1,
                  f"弹窗={mask_shown} 卡片={cards} 张")
            if mask_shown:
                check("弹窗是单独一个 UI 页面（iframe 载 /confirm）",
                      "/confirm" in (page.locator("#cfFrame").get_attribute("src") or ""))
            n_before = timetable_count(page)
            check("出卡之后周表还没被动（确认前不写库）", n_before == n0, f"{n0} → {n_before}")

            # —— 关键一步：不点弹窗按钮，直接在聊天框回一句「确认」（需求③备选方式）——
            #     弹窗是全屏遮罩，开着的时候输入框点不到，所以先按 Esc 把它收起来。
            #     对改课类提案来说，Esc 只是收起弹窗，提案还挂在后端——正好用来验备选方式。
            if mask_shown:
                page.keyboard.press("Escape")
                page.wait_for_timeout(600)
                check("Esc 能收起弹窗（不点按钮也能继续聊）",
                      "show" not in (page.locator("#ttClearMask").get_attribute("class") or ""))
            box.click()
            box.fill("确认删除")
            box.press("Enter")
            page.wait_for_timeout(5000)

            answer = page.locator(".msg.bot, .bot .msg, .msg-content").last.inner_text()
            check("一句确认就被系统执行了", "已按你的确认执行" in answer,
                  answer[:60].replace("\n", " "))
            check("回答里没有「我没有权限」", "没有权限" not in answer)

            n_after = timetable_count(page)
            gone = page.evaluate(
                "!window.__tt.some(c => c.day === 1 && c.course === '高等数学')")
            check("周表真的少了一门", n_after == n0 - 1, f"{n0} → {n_after}")
            check("周一高等数学确实没了", gone)

            check("页面没有 JS 报错", not errors, str(errors[:2]))
            browser.close()

    print(f"\n===== 端到端结果：通过 {passed} 项，失败 {failed} 项 =====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
