"""截图验收：🧭 意图诊断（学生端那颗按钮）——只读预览，不写库、不进聊天历史。

要证明的就一件事（对应定稿《CampusTime-意图抽取路由提示词-定稿.md》第 2 节）：
**学生端能真的调到 `/classify` 并拿到结果**，而且它与 `/api/chat` **并存**——
点它不会写日程、也不会在聊天里多出东西。

留两张截图：
    13_probe_query  —— 问句（"图书馆几点开门"）→ 显示"这是在**问**"、类型/意图/依据
    14_probe_todo   —— 办事（"帮我加个游泳周四19:00"）→ 显示"让你**办事**"、add_todo
再核对两件事（这才是重点）：
    · 待办条数、聊天消息条数，点按钮前后**完全一致**；
    · 页面本身没被改坏（按钮在同一行、结果条只在点击后出现）。

⚠️ 跑在 e2e 的临时数据副本上（`_e2e_env.e2e_server` 会在临时目录复制一份 app/data
并单独起一台服务器），演示数据一个字节都不动。
⚠️ 这个副本**保留 settings.json**（就是演示用的模型密钥），所以在真机的有 key 环境里，
语义层那一档是真的会跑的——诊断结果会显示 `依据：语义层…`；没 key 的机器上会显示
「规则层预检」或「样本库」，两种都算过（这一批不依赖模型）。
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..'))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print('\u26a0\ufe0f 本脚本需要 Playwright（可选）：'
          'pip install playwright && python -m playwright install chromium')
    print('   它是可选端到端/截图测试；run_all.py 主自检测试套件不依赖它，零额外安装即可跑。')
    _sys.exit(0)

import pathlib
import sys


from _e2e_env import e2e_server

OUT = pathlib.Path(__file__).resolve().parent / "_shots"


def shot(page, name):
    OUT.mkdir(exist_ok=True)
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p))
    print(f"  📷 {p}")


def _demo_state(page):
    """读一下"点按钮前/后"两份可比的账：待办条数 + **聊天那条会话**的消息条数。

    ⚠️ 会话 id 要用**页面自己那个**（`sessionId()` 存在 localStorage 的
    `campustime_session` 里），不能拿脚本里造的 `SID` 去查——那是一条**从没聊过**
    的会话，永远是 0 条，于是"聊天链路还通着"这条断言必然假红（踩过）。
    """
    return page.evaluate("""async () => {
        const t = await (await fetch('/api/todos')).json();
        const sid = localStorage.getItem('campustime_session') || '';
        let msgs = 0;
        if (sid) {
            const c = await (await fetch('/api/conversation/' + encodeURIComponent(sid))).json();
            msgs = (c.messages || []).length;
        }
        return { todos: (t.todos || []).length, msgs };
    }""")


def _wait_probe(page):
    """等诊断条**真的写出结果**（不再显示"正在看它怎么理解…"）。

    ⚠️ 不要用固定 `wait_for_timeout(800)` 凑——有 key 的机器上这一步要真调一次模型，
    机器忙起来一秒多很正常，固定等待会让断言偶发假红（看着像功能坏了，其实是没等够）。
    """
    page.wait_for_function(
        """() => {
            const el = document.getElementById('probeOut');
            return !!el && el.classList.contains('show')
                   && !el.textContent.includes('正在看');
        }""",
        timeout=30000,
    )


def main():
    with e2e_server() as srv:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 1280, "height": 900})

            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))   # JS 报错就是页面坏了
            page.goto(srv.url + "/student", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            # 页面必须真的把按钮渲染出来（改坏了这里会直接挂）
            btn = page.locator("#probeBtn")
            assert btn.count() == 1 and btn.is_visible(), "🧭 意图诊断 按钮没渲染出来"
            print("  ✅ 按钮在（#probeBtn）")
            before = _demo_state(page)
            print(f"  点按钮前的账：待办 {before['todos']} 条、会话 {before['msgs']} 条")

            # ---------- ① 问句 ----------
            page.fill("#text", "图书馆几点开门")
            btn.click()
            page.wait_for_selector("#probeOut.show", timeout=15000)
            _wait_probe(page)
            t1 = page.locator("#probeOut").inner_text()
            print("  诊断结果：", t1.replace("\n", " / ")[:120])
            assert "问" in t1 or "办事" in t1, f"结果没写出判定：{t1[:80]}"
            shot(page, "13_probe_query")

            # ---------- ② 办事 ----------
            page.fill("#text", "帮我加个游泳周四19:00")
            btn.click()
            _wait_probe(page)
            t2 = page.locator("#probeOut").inner_text()
            print("  诊断结果：", t2.replace("\n", " / ")[:120])
            shot(page, "14_probe_todo")

            # ---------- ③ 红线：这个口子只读 ----------
            after = _demo_state(page)
            print(f"  点按钮后的账：待办 {after['todos']} 条、会话 {after['msgs']} 条")
            assert after == before, f"诊断按钮竟然动了数据：{before} → {after}"
            print("  ✅ 待办与会话条数分毫未变（只读，没写库、没进历史）")

            # 聊天链路照旧：按钮是**新增**的，没替代 /api/chat
            page.fill("#text", "我都有啥待办来着")
            page.click("#send")
            page.wait_for_timeout(3000)
            st = _demo_state(page)
            assert st["msgs"] > after["msgs"], "点了诊断按钮之后聊天链路不通了"
            print(f"  ✅ /api/chat 照旧可用（会话消息 {after['msgs']} → {st['msgs']} 条）")

            assert not errs, f"页面有 JS 报错：{errs[:2]}"
            print("  ✅ 页面无 JS 报错")
            b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
