"""截图验收：「看不见 X」这条分支不许误伤"问时间"，也不许拿残片当名字。

上一轮（_shot_notsee.py）把「没看见 X」接住了，可它那批断言全是正向的
（只看"报障句有没有被接住"），漏掉另一半——实测这两句被吃掉：

    学生：「游泳在哪天来着」      （问时间，不是报障）
    系统：「👀 查了日程，**确实还没有**「游泳天来着」」

「在哪天」三个字既让正则判成了报障，又把「天来着」这截残片当成了事名。
这一组把修好之后的那一幕留下来：

    ① 问「在哪天」→ 走找空档那条路，不许回「确实还没有」，不许冒残片名；
    ② 正常加待办仍然照旧出卡（收紧之后没有把该接的也一起挡掉）。

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
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def send(page, text):
    box = page.locator("#text").first
    box.click()
    box.fill(text)
    box.press("Enter")


def main():
    with e2e_server() as srv:
        srv.seed_timetable(demo_courses_from_json())
        srv.seed_todos([])
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1200, "height": 900})
            page.goto(srv.url + "/student", wait_until="networkidle")
            page.wait_for_timeout(500)

            # —— ① 问时间（不是报障）→ 不许被当成"没排" ——
            send(page, "游泳在哪天来着")
            # 这一句第一次走 e2e 时需要算空档、出候选卡，响应较慢；
            # 等消息气泡真正出现且文本里不含报障关键词，再截图。
            page.wait_for_timeout(4500)
            shot(page, "44_where_ask")
            txt = page.evaluate("document.getElementById('log').innerText")
            # ⚠️ 只查**管家说的那一段**：学生原话「游泳在哪天来着」里本来就带着
            # "天来着"，拿整段日志去查等于拿用户输入当系统输出，永远红。
            reply = txt.replace("游泳在哪天来着", "")
            ok1 = ("确实还没有" not in reply and "查了日程" not in reply)
            print(f"  {'✅' if ok1 else '❌'} 问「在哪天」没被打成「确实还没有」")
            if not ok1:
                print(f"     管家回话：{reply.strip()[:160]}")
            page.evaluate("document.getElementById('log').scrollTop = 1e9")

            # —— ② 正常加待办仍然出卡（反向：收紧没挡住该接的） ——
            send(page, "帮我加个游泳")
            page.wait_for_timeout(2500)
            shot(page, "45_where_add")
            txt = page.evaluate("document.getElementById('log').innerText")
            ok2 = "游泳" in txt and "确认加入" in txt
            print(f"  {'✅' if ok2 else '❌'} 正常加待办照旧出确认条")
            if not ok2:
                print(f"     实际回话：{txt[-160:]}")

            b.close()
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
