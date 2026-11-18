# -*- coding: utf-8 -*-
"""给《作品介绍文档》抓界面截图（真跑真截，不摆拍）。

用法（服务必须已经在 8000 上跑着）：
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    .venv\\Scripts\\python.exe docs\\take_intro_shots.py
    .venv\\Scripts\\python.exe docs\\build_intro_doc.py

为什么这些图必须现截，不能"找张旧图凑"
--------------------------------------
`build_tech_doc.py` 那份 27 页技术文档里**一张图都没有**（全是表和文字），
而比赛表单第 5 项要界面图。用旧截图的风险是"图上写的和现在跑的"对不上 ——
本项目在这上面栽过（技术文档里手抄的路由数漂过一次，见 build_intro_doc 的开头）。

几张图各自要证明什么（不是"好看"，是"可核对"）
---------------------------------------------
01 登录页       —— 三个身份入口，学生只填学号（这不是鉴权，是把每人的库分开）
02 确认卡       —— 一句话进、一张卡出；卡上把事名/日期/时段摊开，点确认才写库
03 多选候选卡   —— 多段需求摊成一张卡，按钮上直接写"（2 项）"
04 周视图       —— 课程按时间铺开，空档一眼可见
05 思考过程     —— "我是这么想的"：听成办事还是问事、依据哪一档，判定可核
06 管理端知识库 —— 一条问题一条答案，改完立刻生效
07 Agent 工作流 —— 每一轮一行、工具调用与流水线状态可回看（就是上面那几次对话）
08 驿站端       —— 一句话生成一项服务

⚠️ 顺序有讲究：02 与 03 之间要**清空聊天**。
第一轮如果先问"复习线性代数"，紧接着问"打乒乓球"，判定层会把事名带成上一轮那个，
截出来的 03 上写着"复习线性代数"（实拍踩过：2026-11-18）。清空后 03 才是干净的一轮。
"""
import asyncio
import os

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

BASE = "http://127.0.0.1:8000"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intro_shots")
SID = "admin1"          # app/data/accounts.json 里已开通的演示学号
ADMIN_TOKEN = "123456"  # app/config.py 的 DEMO_ADMIN_TOKEN（设了环境变量就换成你自己的）


async def shot(page, fn):
    p = os.path.join(OUT, fn)
    await page.screenshot(path=p)
    print("%-34s %6.1f KB" % (fn, os.path.getsize(p) / 1024))


async def fresh_student(ctx):
    """新开一页 → 登录 → 清空聊天记录，给一个干净的会话。"""
    page = await ctx.new_page()
    page.set_default_timeout(25000)
    await page.goto(BASE + "/", wait_until="load")
    await page.wait_for_timeout(600)
    await page.click("#asStudent")
    await page.fill("#sidInput", SID)
    await page.click("#sidGo")
    await page.wait_for_url("**/student", timeout=20000)
    await page.wait_for_timeout(2200)
    try:
        await page.click("#clearChat")
        await page.wait_for_timeout(500)
        await page.click("#chatResetOk")
        await page.wait_for_timeout(1800)
    except Exception as e:  # noqa: BLE001
        print("清空聊天失败（继续）:", e)
    return page


async def say(page, text, want_card=True):
    await page.fill("#text", text)
    await page.click("#send")
    if want_card:
        try:
            await page.wait_for_selector(".cf-inline", timeout=35000)
            print("OK 卡片出现：", text)
        except PWTimeout:
            print("!! 35s 内没等到卡片：", text)
    await page.wait_for_timeout(3000)


async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 950},
                                        device_scale_factor=2, locale="zh-CN")

        # ---------- 01 登录页 ----------
        p0 = await ctx.new_page()
        p0.set_default_timeout(20000)
        await p0.goto(BASE + "/", wait_until="load")
        await p0.wait_for_timeout(1200)
        await shot(p0, "01-登录页.png")
        await p0.close()

        # ---------- 03 多选候选卡（干净会话，第一句就是多段排期）----------
        page = await fresh_student(ctx)
        await say(page, "这周安排两次打乒乓球，一次两小时")
        await shot(page, "03-学生端-多选候选卡.png")
        await page.close()

        # ---------- 02 确认卡 + 05 思考过程 ----------
        page = await fresh_student(ctx)
        await say(page, "复习线性代数排到周四 19:00-20:30")
        await shot(page, "02-学生端对话-确认卡.png")
        try:
            tr = await page.query_selector_all("details.trace")
            if tr:
                last = tr[-1]
                await last.scroll_into_view_if_needed()
                if await last.get_attribute("open") is None:
                    await last.click()
                await page.wait_for_timeout(1200)
                await shot(page, "05-思考过程-可核对.png")
        except Exception as e:  # noqa: BLE001
            print("05 失败:", e)

        # ---------- 04 我的日程 周视图 ----------
        try:
            await page.goto(BASE + "/plan", wait_until="load")
            await page.wait_for_timeout(3500)
            await shot(page, "04-我的日程-周视图.png")
        except Exception as e:  # noqa: BLE001
            print("04 失败:", e)
        await page.close()

        # ---------- 06 管理端知识库 / 07 Agent 工作流 ----------
        try:
            admin = await ctx.new_page()
            admin.set_default_timeout(25000)
            await admin.goto(BASE + "/admin", wait_until="load")
            await admin.fill("#adminToken", ADMIN_TOKEN)
            await admin.click("#saveToken")
            await admin.wait_for_timeout(2200)
            await admin.click('[data-nav="kb"]')
            await admin.wait_for_timeout(2200)
            await shot(admin, "06-管理端-知识库与模型配置.png")
            await admin.click('[data-nav="trace"]')
            await admin.wait_for_timeout(2800)
            await shot(admin, "07-管理端-Agent工作流轨迹.png")
            await admin.close()
        except Exception as e:  # noqa: BLE001
            print("06/07 失败:", e)

        # ---------- 08 驿站端 ----------
        try:
            st = await ctx.new_page()
            st.set_default_timeout(25000)
            await st.goto(BASE + "/station", wait_until="load")
            await st.wait_for_timeout(2500)
            try:
                await st.click('[data-nav="svc"]')
                await st.wait_for_timeout(2200)
            except Exception:  # noqa: BLE001
                pass
            await shot(st, "08-驿站端-低代码服务.png")
            await st.close()
        except Exception as e:  # noqa: BLE001
            print("08 失败:", e)

        await browser.close()


asyncio.run(main())
