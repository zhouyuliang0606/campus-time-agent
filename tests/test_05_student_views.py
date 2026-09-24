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


def test_plan_month_calendar():
    title("5. /plan 月表系统日历化（每天格子直接列课程+待办）")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/plan")
        c.check("/plan 返回 200", r.status_code == 200)
        html = r.text
        c.check("月表网格 monthGrid 存在", 'id="monthGrid"' in html)
        c.check("格子里的课程/待办小条样式（mevt.course/mevt.todo）",
                "mevt.course" in html and "mevt.todo" in html)
        c.check("今天标注（tdy）", "tdy" in html)
        c.check("超出折叠提示（还有 N 项）", "还有" in html and "项…" in html)
        # 学生端面板的月表同步升级
        rs = client.get("/student")
        sh = rs.text
        c.check("学生面板月表也有今天标注", "tdy" in sh)
        c.check("学生面板月表用 evt 小条列课程+待办", 'class="evt ' in sh and "还有" in sh)
    return c.summary("第五批（月表日历化）")


def test_timetable_import_entry():
    title("6. 学生端课表导入入口（上传给管家 → 管家自动写周表）")
    c = Checker()
    with sandbox():
        client = make_client()
        r = client.get("/student")
        html = r.text
        c.check("面板有「上传课表」按钮与文件框",
                'id="uploadTtPanel"' in html and 'id="ttFilePanel"' in html)
        c.check("面板有导入状态提示条", 'id="ttPanelStatus"' in html)
        c.check("聊天上传后有「让管家读取这份课表」快捷入口", "让管家读取这份课表" in html)
        c.check("导入消息先要预览、确认后才写（预览确认制）",
                "预览清单" in html and "先不要写入周表" in html)
        c.check("导入消息让管家读文件并调 import_timetable",
                "read_uploaded_file" in html and "import_timetable" in html)
        c.check("sendMsg 支持模块覆盖（导入固定走 planner）",
                "moduleOverride" in html)
        # 无密钥时 Mock 对导入请求要给出明确说明（而不是乱给候选时间段）
        rc = client.post("/api/chat", json={
            "message": "我上传了一份课表文件《课表.xlsx》（共 200 字）。"
                       "请读取它的内容并调用 import_timetable 写进我的周表。",
            "module": "planner", "session_id": "ti1",
        })
        c.check("导入请求返回 200", rc.status_code == 200)
        d = rc.json() or {}
        c.check("无密钥时明确提示需要密钥", "密钥" in d.get("answer", ""), d.get("answer", "")[:40])
        c.check("不会误给候选时间段", d.get("options") == [])
    return c.summary("第六批（课表导入入口）")


def test_persona_rules_and_workorders():
    """第七批：学生助手人设四条规矩 + 工单上报链路（学生要求写进人设的行为）。"""
    import pathlib

    proj = pathlib.Path(__file__).resolve().parent.parent

    title("7. 助手人设规矩（输出/工单/隔离/定位）+ 工单上报链路")
    c = Checker()
    with sandbox():
        client = make_client()

        # —— 人设规矩真的写进了系统提示 ——
        main_src = (proj / "app" / "main.py").read_text(encoding="utf-8")
        c.check("全局人设规矩块 ASSISTANT_RULES 存在并拼进每个模块",
                "ASSISTANT_RULES" in main_src and "prompt = base_prompt + FILE_HINT + ASSISTANT_RULES" in main_src)
        c.check("规矩①输出要求：简洁/不暴露内部报错/不输出低俗内容",
                all(k in main_src for k in ("不冗长废话", "禁止把系统内部报错", "低俗")))
        c.check("规矩②工单：先问「是否上报给管理端」，确认才生成",
                "是否上报给管理端" in main_src and "普通日常对话、排课、规划待办一律不上报" in main_src)
        c.check("规矩③隔离：预览方案、确认前不写库、只影响当前学生",
                "预览方案" in main_src and "只影响当前学生" in main_src)
        c.check("规矩④定位：决定权在学生、上传文件只读不改",
                "决定权永远在学生本人" in main_src and "禁止修改或覆盖源文件" in main_src)

        # —— 工单上报规则进了快递/外卖模块提示，工具已注册 ——
        express_src = (proj / "app" / "modules" / "express.py").read_text(encoding="utf-8")
        takeout_src = (proj / "app" / "modules" / "takeout.py").read_text(encoding="utf-8")
        for name, s in (("快递", express_src), ("外卖", takeout_src)):
            c.check(f"{name}提示含上报规则（先问确认、没确认不许调工具）",
                    "先问一句：「是否上报给管理端？」" in s and "绝不许调用 submit_work_order" in s)
            c.check(f"{name}模块注册了 submit_work_order 工具", '"submit_work_order"' in s)

        # —— 规划模块：预览确认制写进提示与工具描述 ——
        planner_src = (proj / "app" / "modules" / "planner.py").read_text(encoding="utf-8")
        c.check("规划提示含【个人数据隔离 —— 预览确认制】",
                "【个人数据隔离 —— 预览确认制" in planner_src)
        c.check("改课表要求：预览 → 确认 → 整表写入",
                "调整后的完整课表" in planner_src and "先不要写入" not in planner_src)
        c.check("import_timetable 描述要求学生确认后才能调用",
                "等学生明确确认导入后才能调用" in planner_src)
        c.check("remove_todo 描述要求确认后才能删",
                "得到明确确认后才能调用" in planner_src)

        # —— 工单 API 链路：提交 → 管理端可见 → 改状态 ——
        r = client.post("/api/workorders", json={
            "kind": "疑似丢件", "desc": "3 天了驿站查不到件", "source": "panel"})
        c.check("POST /api/workorders 提交成功", r.status_code == 200 and (r.json() or {}).get("ok"))
        wo = (r.json() or {}).get("workorder", {})
        c.check("工单含 编号/时间/类型/描述/状态",
                all(wo.get(k) for k in ("id", "ts", "kind")) and wo.get("status") == "待处理")
        rl = client.get("/api/workorders")
        lst = (rl.json() or {}).get("workorders", [])
        c.check("管理端 GET /api/workorders 能看到这条工单",
                any(x["id"] == wo.get("id") for x in lst), f"共 {len(lst)} 条")
        rs = client.post(f"/api/workorders/{wo.get('id')}/status", json={"status": "已解决"})
        c.check("管理员改状态为 已解决", rs.status_code == 200
                and (rs.json() or {}).get("workorder", {}).get("status") == "已解决")
        c.check("非法状态被拒绝", client.post(
            f"/api/workorders/{wo.get('id')}/status", json={"status": "随便"}).status_code == 400)
        c.check("空类型被拒绝", client.post(
            "/api/workorders", json={"kind": " "}).status_code == 400)

        # —— 管理端页面有工单区块；学生端不再全量上报普通对话 ——
        admin_html = client.get("/admin").text
        c.check("管理端有「学生工单」区块并拉工单接口",
                "学生工单" in admin_html and "/api/workorders" in admin_html)
        student_html = client.get("/student").text
        c.check("学生端已移除普通对话全量上报（logToAdmin）", "logToAdmin" not in student_html)
        c.check("学生端报错改为友好话术（不暴露内部信息）",
                "网络好像开小差了" in student_html and "e.message" not in student_html.split("sendMsg")[1][:2000])
    return c.summary("第七批（人设规矩与工单）")


if __name__ == "__main__":
    code = 0
    code |= test_express_view_api()
    code |= test_takeout_view_api()
    code |= test_student_panel_markup()
    code |= test_regression_existing_apis()
    code |= test_plan_month_calendar()
    code |= test_timetable_import_entry()
    code |= test_persona_rules_and_workorders()
    sys.exit(code)
