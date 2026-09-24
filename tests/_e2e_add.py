"""端到端：复现并验收用户报的三个现象
  ① 说了加课/加待办 → 回复已经加上 → 日程看不到 → 刷新仍没有
  ② 聊天记录刷新后没了

  这次跑在**独立的临时数据副本**上（见 _e2e_env），演示数据一个字节都不会被改。
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

from _e2e_env import e2e_server

BASE = ""          # 运行时由 e2e_server 填上
ROOT = pathlib.Path(__file__).resolve().parent.parent

# 起手只放两门课 —— 加课要能"多出一门"才看得出效果，
# 满课的表点了确认也看不出变化。这张表只写进临时副本，演示数据不受影响。
COURSES = [
    {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三-201"},
    {"day": 2, "start": "08:00", "end": "09:40", "course": "线性代数", "location": "教三-301"},
]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("  ✅ " if ok else "  ❌ ") + name + (("  → " + str(detail)) if detail else ""))


def snap(pg):
    return pg.evaluate("""async () => {
        const tt = await (await fetch('/api/timetable')).json();
        const td = await (await fetch('/api/todos')).json();
        return { n: tt.courses.length, names: tt.courses.map(c => c.course),
                 todos: (td.todos || []).map(t => (t.title||'') + '@' + (t.date||'') + ' ' + (t.start||'')) };
    }""")


def main():
    with e2e_server() as srv:
        BASE = srv.url
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.on("console", lambda m: errs.append("console:" + (m.text or "")) if m.type == "error" else None)

            # 清掉上一个 session 的聊天记录（临时数据目录本来就是新的，
            # 但浏览器可能还留着旧的 localStorage 里的 session id，会影响"刷新后还在"的判断）
            pg.goto(BASE + "/student", wait_until="networkidle")
            pg.evaluate("localStorage.clear()")
            srv.seed_timetable(COURSES)          # 播种只写副本，不碰演示数据
            srv.seed_todos([])
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(800)

            def ask(text, wait=24):
                """发一句话，等**新的**那条回答冒出来（含卡片）再收工。

                不能只看"最后一条是不是管家"——上一轮的管家消息还杵在那儿，会当场误判成已回复。
                """
                n0 = pg.locator(".msg").count()
                box = pg.locator("#text").first
                box.click(); box.fill(text); box.press("Enter")
                for _ in range(wait):
                    pg.wait_for_timeout(500)
                    n1 = pg.locator(".msg").count()
                    if n1 <= n0:
                        continue
                    last = pg.locator(".msg").nth(n1 - 1).inner_text()
                    if "正在思考" in last or last.strip().endswith("…"):
                        continue
                    pg.wait_for_timeout(700)      # 卡片是同一条消息里再挂上去的，多等一拍
                    break
                return pg.locator(".msg").nth(pg.locator(".msg").count() - 1).inner_text()

            def show_schedule():
                for sel in ("#schWeek", "#ttWeek", ".week-grid"):
                    if pg.locator(sel).count():
                        return pg.locator(sel).first.inner_text()
                return ""

            # 出提案后唤起**确认条**：内容是 /confirm 独立页面，但嵌在聊天流里
            # （学生要求：不要单独弹一个全屏弹窗，要附着在聊天框上）。
            # 一轮出好几张时才退回内嵌卡片。点确认要两种形态都认得。
            def confirm_now():
                bar = pg.locator(".cf-inline .cf-frame")
                if bar.count():
                    pg.wait_for_timeout(1200)      # 等独立页面把提案渲染好
                    pg.frame_locator(".cf-inline .cf-frame").locator("#cfOk").click()
                    pg.wait_for_timeout(2500)
                    return True
                if pg.locator(".confirm-btn").count():
                    pg.locator(".confirm-btn").first.click()
                    pg.wait_for_timeout(3000)
                    return True
                return False

            def has_entry():
                return (pg.locator(".cf-inline .cf-frame").count() > 0
                        or pg.locator(".confirm-btn").count() > 0)

            print("\n======== ① 加待办：说一句 → 出卡 → 点确认 → 日程当场看得到 ========")
            ans = ask("帮我把今天的『复习线性代数』安排到 19:00 到 20:30")
            check("回复里给出了确认入口（不再只是嘴上说已加）", "确认加入" in ans or has_entry(), ans[:90].replace("\n", " "))
            check("确认前没写库（提案归提案）", len(snap(pg)["todos"]) == 0, snap(pg)["todos"])
            confirm_now()
            s = snap(pg)
            check("点确认后写进待办", any("复习线性代数" in t for t in s["todos"]), s["todos"])
            check("日程面板当场合能看到", "复习线性代数" in show_schedule(), show_schedule()[:80].replace("\n", " "))

            print("\n======== ② 加课：出卡 → 点确认 → 周表当场刷新 ========")
            n0 = snap(pg)["n"]
            ask("周五再加一节『心理学选修』，教二-110，14:00 到 15:40")
            check("加课给出了确认入口（弹窗或卡片）", has_entry())
            confirm_now()
            s2 = snap(pg)
            check("点确认后周表多了一门课", s2["n"] == n0 + 1, f"{n0} → {s2['n']} 门 {s2['names']}")
            check("周视图里能看到新课", "心理学选修" in show_schedule(), show_schedule()[:100].replace("\n", " "))

            print("\n======== ③ 聊天记录：刷新后还在 ========")
            n_before = pg.locator(".msg").count()
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(2000)
            n_after = pg.locator(".msg").count()
            check("刷新后聊天记录没丢", n_after >= n_before - 1, f"刷新前 {n_before} 条 → 刷新后 {n_after} 条")

            print("\n======== ④ 刷新后数据仍在（学生最在意的：到底加上没有）========")
            s3 = snap(pg)
            check("刷新后待办仍在", any("复习线性代数" in t for t in s3["todos"]), s3["todos"])
            check("刷新后周表仍是多出来那门课", "心理学选修" in " ".join(s3["names"]), s3["names"])

            print("\n【页面报错】", errs[:3] or "无")
            b.close()
        return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
