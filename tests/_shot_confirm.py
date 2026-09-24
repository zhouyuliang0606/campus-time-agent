"""截图验收：确认条到底是不是"贴在聊天框上的一小条"。

不是测试，是给人看的证据——跑完会在 tests/_shots/ 下留三张图：
  1_confirm_bar.png   出提案后，确认条贴在消息下面（不是全屏遮罩）
  2_after_cancel.png  点【取消】之后确认条消失、聊天照常
  3_after_ok.png      点【确认】之后确认条消失、给出执行回执

跑在临时数据副本上，演示数据一个字节都不动。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import demo_courses_from_json, e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"  📷 {p}")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(500)

            # ① 加待办 → 出确认条
            box = page.locator("#text").first
            box.click()
            box.fill("帮我把今天的『复习线性代数』安排到 19:00 到 20:30")
            box.press("Enter")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=15000)
            page.wait_for_timeout(1500)
            page.locator(".cf-inline").first.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "1_confirm_bar")

            bar = page.locator(".cf-inline").first.bounding_box()
            log_box = page.locator("#log").bounding_box()
            inp = page.locator("#text").first.bounding_box()
            print(f"  确认条 y={int(bar['y'])} h={int(bar['height'])} / "
                  f"消息流 y={int(log_box['y'])}..{int(log_box['y'] + log_box['height'])} / "
                  f"输入框 y={int(inp['y'])}")
            inside = page.evaluate("""() => {
                const b = document.querySelector('.cf-inline');
                const l = document.getElementById('log');
                return !!(b && l && l.contains(b));
            }""")
            print(f"  确认条在消息流里面：{inside}")
            print(f"  全屏遮罩数量：{page.locator('.mask.show').count()}")

            # ② 点【取消】
            page.frame_locator(".cf-inline .cf-frame").locator("#cfCancel").click()
            page.wait_for_timeout(900)
            shot(page, "2_after_cancel")

            # ③ 再来一次，点【确认加入】
            box.click()
            box.fill("帮我把今天的『复习线性代数』安排到 19:00 到 20:30")
            box.press("Enter")
            page.wait_for_selector(".cf-inline .cf-frame", state="visible", timeout=15000)
            page.wait_for_timeout(1500)
            page.frame_locator(".cf-inline .cf-frame").locator("#cfOk").click()
            page.wait_for_timeout(2500)
            page.locator(".msg").last.scroll_into_view_if_needed()
            page.wait_for_timeout(300)
            shot(page, "3_after_ok")
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
