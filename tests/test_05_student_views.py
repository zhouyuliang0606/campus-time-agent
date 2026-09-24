"""第五批测试：学生端「卡片点击 → 主面板视图切换」相关能力。

覆盖四件事：
1. /api/express 返回结构化快递列表，状态标签（待取/滞留/已取）已实时算好；
2. /api/takeout 返回结构化外卖订单列表；
3. /student 页面包含主面板与四个视图的关键标记，且不再往聊天里塞「已切换」系统消息；
4. 回归护栏：既有接口（课表/待办/性格/健康检查）一个都没被破坏。

注意：快递/外卖模块读的是 app/data 下的演示台账（模块相对路径，不走数据沙箱），
这两个接口是纯只读，测试不会污染任何数据。
"""
import sys

from _harness import Checker, sandbox, make_client, title


def test_express_view_api():
    title("1. 快递卡片视图接口 /api/express")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/api/express")
        c.check("接口返回 200", r.status_code == 200)
        pkgs = (r.json() or {}).get("packages", [])
        c.check("演示台账里有快递记录", len(pkgs) >= 1, f"共 {len(pkgs)} 件")
        first = pkgs[0] if pkgs else {}
        for f in ("id", "item", "station", "pickup_code", "arrived_at"):
            c.check(f"快递含字段 {f}", f in first, str(first.get(f)))
        labels = {p.get("label") for p in pkgs}
        c.check("状态标签在 待取/滞留/已取 之内", labels <= {"待取", "滞留", "已取"}, str(labels))
        c.check("每件都有实时算好的滞留天数 stay_days",
                all(isinstance(p.get("stay_days"), int) for p in pkgs))
        # 状态逻辑与 AI 工具同源：已取的件标签必须是「已取」
        taken = [p for p in pkgs if p.get("status") == "已取"]
        c.check("status=已取 的件 label 也为 已取",
                all(p.get("label") == "已取" for p in taken), f"{len(taken)} 件已取")
    return c.summary("第一批（快递视图接口）")


def test_takeout_view_api():
    title("2. 外卖卡片视图接口 /api/takeout")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/api/takeout")
        c.check("接口返回 200", r.status_code == 200)
        orders = (r.json() or {}).get("orders", [])
        c.check("演示台账里有外卖订单", len(orders) >= 1, f"共 {len(orders)} 单")
        first = orders[0] if orders else {}
        for f in ("id", "shop", "items", "eta", "status", "pickup_point"):
            c.check(f"订单含字段 {f}", f in first, str(first.get(f)))
        statuses = {o.get("status") for o in orders}
        c.check("状态都在外卖模块认识的范围里",
                statuses <= {"制作中", "配送中", "待取", "已完成"}, str(statuses))
    return c.summary("第二批（外卖视图接口）")


def test_student_panel_markup():
    title("3. 学生页主面板标记（视图切换交互）")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/student")
        c.check("/student 返回 200", r.status_code == 200)
        html = r.text
        c.check("页面带 no-store 防缓存", "no-store" in r.headers.get("cache-control", ""))
        # 主面板与四个视图都在
        c.check("有主面板容器 #panel", 'id="panel"' in html)
        for vid in ("view-schedule", "view-express", "view-takeout", "view-faq"):
            c.check(f"有视图 {vid}", f'id="{vid}"' in html)
        # 课表安排视图：周表 + 月表切换
        c.check("周/月切换按钮（tabWeek/tabMonth）", 'id="tabWeek"' in html and 'id="tabMonth"' in html)
        c.check("周表容器 schWeek 与月表容器 schMonth", 'id="schWeek"' in html and 'id="schMonth"' in html)
        # 快递视图：列表 + 工单表单与记录
        c.check("快递列表容器 expList", 'id="expList"' in html)
        c.check("工单表单（woType/woDesc/woSubmit）",
                all(f'id="{x}"' in html for x in ("woType", "woDesc", "woSubmit")))
        c.check("工单记录容器 woList（localStorage 持久）",
                'id="woList"' in html and "campustime_workorders" in html)
        # 外卖视图
        c.check("外卖列表容器 tkList", 'id="tkList"' in html)
        # 校园问答视图：高频问题按钮墙
        c.check("问答按钮墙 faqGrid + faq-btn 样式", 'id="faqGrid"' in html and "faq-btn" in html)
        # 点击逻辑：不再往聊天里塞「已切换到」的系统消息，改为切换面板
        c.check("旧版「已切换到」纯文本提示已移除", "已切换到" not in html)
        c.check("selectModule 走 showView 切面板", "showView(VIEW_OF[key])" in html)
        c.check("再点一次卡片可收起面板（collapsePanel）", "collapsePanel" in html)
        c.check("每个视图都有收起按钮", html.count('class="close"') == 4,
                f"共 {html.count('class=' + chr(34) + 'close' + chr(34))} 个")
        # 数据源引用
        c.check("前端拉 /api/express 与 /api/takeout",
                "/api/express" in html and "/api/takeout" in html)
        c.check("课表视图拉 /api/timetable 与 /api/todos",
                "/api/timetable" in html and '"/api/todos"' in html)
    return c.summary("第三批（页面标记）")


def test_regression_existing_apis():
    title("4. 回归护栏：既有接口没有被这次改动破坏")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/api/health")
        c.check("健康检查 200", r.status_code == 200)
        mods = (r.json() or {}).get("modules", [])
        c.check("模块注册表没多没少（6 个）", sorted(mods) ==
                sorted(["schedule", "faq", "express", "takeout", "station", "planner"]), str(mods))
        c.check("周表接口仍 200", client.get("/api/timetable").status_code == 200)
        c.check("待办接口仍 200", client.get("/api/todos").status_code == 200)
        c.check("性格接口仍 200", client.get("/api/student-personas").status_code == 200)
        # 对话接口的基本形状没变（无密钥时也不会裸 500）
        rc = client.post("/api/chat", json={"message": "你好", "module": "faq", "session_id": "rv1"})
        c.check("对话接口仍正常返回", rc.status_code == 200 and "answer" in rc.json())
    return c.summary("第四批（回归护栏）")


if __name__ == "__main__":
    code = 0
    code |= test_express_view_api()
    code |= test_takeout_view_api()
    code |= test_student_panel_markup()
    code |= test_regression_existing_apis()
    sys.exit(code)
