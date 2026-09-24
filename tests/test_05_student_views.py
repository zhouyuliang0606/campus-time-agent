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

from _harness import Checker, sandbox, make_client, title, REAL_DATA_DIR


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
        c.check("确认卡走 /api/timetable/apply 确定性写入（不经过 AI）",
                "/api/timetable/apply" in html)
        c.check("renderOptions 识别课表修改提案（timetable_change）",
                "timetable_change" in html)
        c.check("sendMsg 支持模块覆盖（导入固定走 planner）",
                "moduleOverride" in html)
        # 无密钥时 Mock 对导入请求要给出明确说明（而不是乱给候选时间段）
        rc = client.post("/api/chat", json={
            "message": "我上传了一份课表文件《课表.xlsx》（共 200 字）。"
                       "请读取它的内容并调用 propose_timetable_change 出确认卡。",
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
        c.check("规矩②工单：只有快递/外卖这类才两步走（先问是否上报、确认才推送）",
                "上报给管理端" in main_src and "绝不推送管理端" in main_src)
        c.check("规矩②补充：增减课程/增删待办属学生自主管理，一律不上报管理端",
                "一律不上报管理端" in main_src)
        c.check("规矩③隔离：确认前不写库、只影响当前学生",
                "学生确认之前数据库绝不会变" in main_src and "只影响当前学生" in main_src)
        c.check("规矩③补充：点弹窗是优先方式、回确认文字是备选方式，都由系统执行",
                "优先方式" in main_src and "备选方式" in main_src)
        c.check("规矩③补充：确认前禁止谎报已完成",
                "禁止说" in main_src and "已经加上了" in main_src)
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
        # 上一版提示里写过"不是你没有权限"，结果模型照样学会说"我没有权限"，
        # 甚至把学生指到不存在的删除按钮上。现在改成正向措辞，并加护栏断言。
        c.check("改课表要求：完整课表提案 → 确认卡 → 系统写入",
                "调整后的完整课表" in planner_src and "系统负责执行" in planner_src)
        c.check("明确告诉模型 propose_course_change 就在工具箱里、有权调用",
                "你完全有权调用它" in planner_src)
        # 光看源码不够——这些句子可能只出现在注释里。这里直接拿**真正会发给模型**
        # 的那段提示来查（拼好全局规矩后的 planner 提示），查不到才说明护栏到位。
        from app.main import ASSISTANT_RULES
        from app.modules.planner import build_system_prompt as planner_prompt
        sent_prompt = planner_prompt() + ASSISTANT_RULES
        for bad in ("我没有权限", "没有工具权限", "课表页面手动操作",
                    "麻烦你在", "点删除即可"):
            c.check(f"真正发给模型的提示里没有误导句「{bad}」", bad not in sent_prompt)
        c.check("propose_timetable_change 是修改课表唯一途径（工具已注册）",
                '"propose_timetable_change"' in planner_src)
        c.check("AI 工具箱已移除 import_timetable（写入权收归系统）",
                '"import_timetable": Tool' not in planner_src)
        c.check("引擎捕获提案参数 → 渲染确认卡",
                "propose_timetable_change" in (proj / "app" / "agent" / "engine.py").read_text(encoding="utf-8"))

        # —— /api/timetable/apply 确定性写入口 ——
        from app.store import save_timetable as _st
        from app.modules.planner import get_timetable
        orig_courses = list(get_timetable())  # 先存原表，测完写回（沙箱目录在进程内共享）
        ok_courses = [
            {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三301"},
            {"day": 3, "start": "14:00", "end": "15:40", "course": "数据结构", "location": "机房B"},
        ]
        ra = client.post("/api/timetable/apply", json={"courses": ok_courses})
        c.check("合法提案 apply 成功", ra.status_code == 200 and (ra.json() or {}).get("ok")
                and (ra.json() or {}).get("count") == 2)
        rt = client.get("/api/timetable")
        c.check("apply 后周表真的变了（确定性写入）",
                len((rt.json() or {}).get("courses", [])) == 2)
        rb = client.post("/api/timetable/apply", json={
            "courses": [{"day": 9, "start": "08:00", "end": "09:00", "course": "坏数据"}]})
        c.check("非法提案被拒（400 + 明细）", rb.status_code == 400)
        rc2 = client.post("/api/timetable/apply", json={"courses": []})
        c.check("空提案被拒", rc2.status_code == 400)
        # 把 apply 测试改掉的周表写回原样（沙箱目录在进程内是共享的，别祸及后面的批次）
        _st(orig_courses)

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


def test_course_change_proposals():
    """第八批：单课程级课表提案（服务端算新表，模型只说对哪节课做什么）。"""
    import json as _json
    import os
    import pathlib
    from app.modules.planner import propose_course_change, get_timetable
    from app.store import save_timetable

    title("8. propose_course_change：删/加/改课提案由服务端算新表")
    c = Checker()
    with sandbox():
        client = make_client()
        # 沙箱的 DATA_DIR 在进程内首次 import 时就固定、首个沙箱退出后即被删，
        # 所以这里主动把真实演示周表播种进当前生效的数据目录，保证有课可删
        seed = _json.load(open(os.path.join(REAL_DATA_DIR, "student", "timetable.json"),
                               encoding="utf-8"))
        save_timetable(seed["courses"])
        base_n = len(get_timetable())
        c.check("沙箱演示周表就绪", base_n >= 10, f"{base_n} 门课")

        # —— 删课：返回 __proposal__，新课表少一门且目标课消失 ——
        r = propose_course_change("remove", day=1, course="高等数学")
        p = _json.loads(r)["__proposal__"]
        c.check("删课提案带 __proposal__（kind/summary/courses 齐全）",
                p["kind"] == "timetable_change" and p["summary"] and isinstance(p["courses"], list))
        c.check("新课表少了一门", len(p["courses"]) == base_n - 1)
        c.check("周一高数已从提案里消失",
                not any(x["day"] == 1 and x["course"] == "高等数学" for x in p["courses"]))
        c.check("其余课程原样保留",
                any(x["day"] == 1 and x["course"] == "大学英语" for x in p["courses"]))

        # —— 模糊课名也能匹配 ——
        r2 = propose_course_change("remove", day=1, course="高数")
        c.check("模糊课名（高数→高等数学）也能出提案", "__proposal__" in r2)

        # —— 同名多节：构造同一天两节同名课，要求带 start 区分 ——
        from app.store import save_timetable
        cur = get_timetable()
        cur.append({"day": 1, "start": "18:00", "end": "19:40",
                    "course": "高等数学", "location": "教三-201"})
        save_timetable(cur)
        r3 = propose_course_change("remove", day=1, course="高等数学")
        c.check("同名多节被拦下并提示带 start", "start 参数区分" in r3, r3[:50])
        r3b = propose_course_change("remove", day=1, course="高等数学", start="18:00")
        p3b = _json.loads(r3b)["__proposal__"]
        c.check("带 start 后精准删除晚间那节",
                len(p3b["courses"]) == base_n and
                not any(x["day"] == 1 and x["start"] == "18:00" for x in p3b["courses"]))
        save_timetable([x for x in cur if not (x["day"] == 1 and x["start"] == "18:00")])

        # —— 找不到的课：返回这天现存课清单，帮模型自我纠正 ——
        r4 = propose_course_change("remove", day=5, course="不存在的课")
        c.check("没找到课时列出当天现存课", "这天现存的课" in r4)

        # —— 加课：写入提案 + 撞课拦截 ——
        r5 = propose_course_change("add", day=6, course="围棋入门",
                                   new_start="10:00", new_end="11:40", new_location="活动室")
        p5 = _json.loads(r5)["__proposal__"]
        c.check("加课提案新课表多一门", len(p5["courses"]) == base_n + 1)
        c.check("新课在周六 10:00",
                any(x["day"] == 6 and x["start"] == "10:00" for x in p5["courses"]))
        r6 = propose_course_change("add", day=1, course="冲突课",
                                   new_start="10:30", new_end="11:30")
        c.check("与现有课撞时间的加课被拦", "撞了" in r6)

        # —— 改课：时间/地点更新进提案 ——
        r7 = propose_course_change("update", day=2, course="体育",
                                   new_start="15:00", new_end="16:40")
        p7 = _json.loads(r7)["__proposal__"]
        tue_sport = [x for x in p7["courses"] if x["day"] == 2 and x["course"] == "体育"]
        c.check("改课后周二体育变成 15:00-16:40",
                tue_sport and tue_sport[0]["start"] == "15:00" and tue_sport[0]["end"] == "16:40")
        c.check("改课不改变总门数", len(p7["courses"]) == base_n)

        # —— 非法入参 ——
        c.check("非法 op 被拒", "op 只能是" in propose_course_change("boom", day=1))
        c.check("day 越界被拒", "越界" in propose_course_change("remove", day=9, course="高数"))

        # —— 模型爱把「周一」当数字传，这里要认下，不能崩 ——
        from app.modules.planner import _coerce_day, validate_courses
        c.check("_coerce_day 认中文星期", _coerce_day("周一") == 1 and _coerce_day("星期天") == 7)
        c.check("_coerce_day 认纯数字字符串", _coerce_day("3") == 3)
        c.check("_coerce_day 挡住 garbage", _coerce_day("第一节") is None)
        c.check("_coerce_day 不会把 True 当 1", _coerce_day(True) is None)
        rc, errs = validate_courses(
            [{"day": "周三", "start": "08:00", "end": "09:40", "course": "毛概"}])
        c.check("中文星期的课也能通过校验", not errs and rc and rc[0]["day"] == 3, str(errs))
        _, errs2 = validate_courses([{"day": 9, "start": "08:00", "end": "09:40", "course": "x"}])
        c.check("越界 day 仍被拦下", any("越界" in e for e in errs2), str(errs2))

        # —— 工具层错误围栏：参数写错不该把整轮对话搞崩 ——
        from app.agent.engine import _is_async
        from app.agent.tools import Tool

        def _is_tool_guarded(tool: "Tool") -> bool:
            """async 版本的围栏和同步版本要都存在，别漏了一个。"""
            import asyncio
            return all(hasattr(tool, n) for n in ("run", "run_async")) and (
                asyncio.iscoroutinefunction(tool.run_async))

        boom = Tool(name="boom", description="d", parameters={}, func=lambda **kw: 1 / 0)
        c.check("工具内部异常被兜住，返回人话而不是抛错",
                "调用 boom 时出错了" in boom.run() and isinstance(boom.run(), str))
        c.check("async 工具同样有围栏", _is_tool_guarded(boom))
        badk = Tool(name="bad", description="d", parameters={}, func=lambda nope=1: nope)
        c.check("参数名写错也返回提示", "参数不对" in badk.run(other=1))

        # —— 引擎通用捕获：源码含 __proposal__ 处理 ——
        eng = (pathlib.Path(__file__).resolve().parent.parent / "app" / "agent" / "engine.py") \
            .read_text(encoding="utf-8")
        c.check("引擎含通用 __proposal__ 捕获（结果即提案）", "__proposal__" in eng)
        c.check("引擎仍保留 propose_timetable_change 参数捕获", "propose_timetable_change" in eng)
    return c.summary("第八批（单课程级课表提案）")


def test_chat_confirm_applies_timetable():
    """第九批：学生回一句「确认」→ 系统确定性落库（这是踩过坑的那条链路）。

    背景（真实事故）：学生打了"确认"，AI 却回"我没有权限删除"，
    还让学生自己去课表页面找删除按钮——而界面上根本没有这个入口。
    所以这里要证明的是：**点头和执行之间不需要经过模型**。
    """
    import json as _json
    import os

    from app.agent.engine import AgentEngine
    from app.agent.pending import (
        clear_pending, is_confirmation, mark_applied, peek_pending, save_pending,
    )
    from app.modules.planner import apply_pending_timetable_change, get_timetable, propose_course_change
    from app.store import save_timetable

    title("9. 聊天框「确认」→ 系统执行（不依赖模型判断）")
    c = Checker()
    with sandbox():
        client = make_client()
        seed = _json.load(open(os.path.join(REAL_DATA_DIR, "student", "timetable.json"),
                               encoding="utf-8"))
        save_timetable(seed["courses"])
        base_courses = list(get_timetable())
        base_n = len(base_courses)
        c.check("沙箱演示周表就绪", base_n >= 10, f"{base_n} 门课")

        # —— ① 什么话算"点头" ——
        c.check("「确认」算点头", is_confirmation("确认"))
        c.check("「可以」「好的」也算", is_confirmation("可以") and is_confirmation("好的"))
        c.check("带标点不影响判断", is_confirmation("确认。"))
        c.check("" "「帮我看看周一第一节是什么」不算点头""", not is_confirmation("帮我看看周一第一节是什么"))
        c.check("" "「删掉第一节」不算点头（有新指令嫌疑）""", not is_confirmation("删掉第一节"))

        # —— ② 引擎出完提案会把卡存进待确认暂存 ——
        eng = AgentEngine(tools={}, session_id="sess-a")
        eng.options = [{"kind": "timetable_change", "summary": "测试提案", "courses": base_courses}]
        out = eng._finish("给你出一张确认卡")
        c.check("引擎收尾把提案存进暂存", peek_pending("sess-a") is not None)
        c.check("响应里带 awaiting_choice（前端据此知道在等学生）", out["awaiting_choice"] is True)
        clear_pending("sess-a")
        c.check("清空后暂存确实没了", peek_pending("sess-a") is None)

        # —— ③ 核心链路：发一句「确认」，课表真的变 ——
        save_pending("sess-c", [_json.loads(propose_course_change(
            "remove", day=1, course="高等数学"))["__proposal__"]])
        c.check("落库前暂存里确实有提案", peek_pending("sess-c") is not None)
        r = client.post("/api/chat", json={
            "message": "确认", "module": "planner", "session_id": "sess-c",
        })
        data = r.json()
        c.check("/api/chat 返回 200", r.status_code == 200)
        c.check("回答明确说已按确认执行", "已按你的确认执行" in (data.get("answer") or ""),
                (data.get("answer") or "")[:40])
        after = [x for x in get_timetable() if not (x["day"] == 1 and x["course"] == "高等数学")]
        c.check("周一高等数学真的被删了", len(after) == base_n - 1, f"{base_n} → {len(after)}")
        c.check("聊天确认走完后暂存已清空（不会重复写）", peek_pending("sess-c") is None)
        c.check("" "回答里不再出现「没有权限」这类话""", "没有权限" not in (data.get("answer") or ""))

        # —— ④ 界面点卡片写完之后，聊天再回一句「确认」不会重复执行 ——
        prop = _json.loads(propose_course_change("remove", day=1, course="程序设计基础"))["__proposal__"]
        save_pending("sess-d", [prop])
        mark_applied("sess-d")          # 模拟前端点了确认卡
        n_before = len(get_timetable())
        again = apply_pending_timetable_change("sess-d")
        c.check("已点过卡的提案不会再被聊天确认重复应用", again is None)
        c.check("周表门数没变", len(get_timetable()) == n_before)

        # —— ⑤ 同一轮两张卡：都要生效，不能被后一张覆盖掉前一张 ——
        # 情景：学生说"周二早上的课都去掉"，服务端出了两张卡
        p_a = _json.loads(propose_course_change("remove", day=2, course="线性代数"))["__proposal__"]
        p_b = _json.loads(propose_course_change("remove", day=4, course="大学英语"))["__proposal__"]
        n_expect = len(get_timetable()) - 2   # ③里已经删掉一门，这里要在这基础上接着减
        save_pending("sess-e", [p_a, p_b])
        res = apply_pending_timetable_change("sess-e")
        c.check("落库后暂存清空", peek_pending("sess-e") is None)
        n2 = len(get_timetable())
        c.check("两张提案卡都生效（总门数减 2）", n2 == n_expect, f"预期 {n_expect} 实际 {n2}")
        c.check("周二线代确实没了",
                not any(x["day"] == 2 and x["course"] == "线性代数" for x in get_timetable()))
        c.check("周四英语确实没了",
                not any(x["day"] == 4 and x["course"] == "大学英语" for x in get_timetable()))

        # —— ⑥ 学生聊别的，这一轮不许顺手改课表 ——
        #    注意：这里**不能**断言"线性代数还在"——⑤ 已经把它删掉了，
        #    那条断言自相矛盾，实测在基线代码上也是红的（是检查本身写错，不是代码坏了）。
        #    真正该守的是：这轮闲聊别顺手动了课表。
        save_pending("sess-g", [p_a])
        n_idle = len(get_timetable())
        client.post("/api/chat", json={
            "message": "周末有什么事吗", "module": "planner", "session_id": "sess-g",
        })
        c.check("聊别的这一轮不会顺手改课表", len(get_timetable()) == n_idle,
                f"{n_idle} → {len(get_timetable())} 门")

        # —— ⑦ 路由把「确认」分到别的模块时，也不能卡住执行 ——
        #    真实场景：学生没点模块卡片，直接打"确认"，路由往往把它分去 FAQ。
        #    确认卡就挂在聊天里，这时候必须照样执行，不能因为走错模块就说办不到。
        p_c = _json.loads(propose_course_change("remove", day=5,
                                                course="心理学选修"))["__proposal__"]
        save_pending("sess-i", [p_c])
        r = client.post("/api/chat", json={
            "message": "确认", "module": "faq", "session_id": "sess-i",
        })
        c.check("即使被路由到 FAQ 模块，一句确认照样执行",
                "已按你的确认执行" in (r.json().get("answer") or ""),
                (r.json().get("answer") or "")[:40])
        c.check("目标课程确实被删",
                not any(x["day"] == 5 and x["course"] == "心理学选修" for x in get_timetable()))

        # —— ⑧ 清会话之后，残留提案作废（隔很久再一句"确认"不会误改）——
        save_pending("sess-j", [p_a])
        client.post("/api/chat/reset", json={"session_id": "sess-j"})
        client.post("/api/chat", json={
            "message": "确认", "module": "planner", "session_id": "sess-j",
        })
        c.check("清过会话后回「确认」不会改课表",
                any(x["course"] == "大学英语" for x in get_timetable()))
    return c.summary("第九批（聊天确认 → 系统执行）")


def test_clear_timetable_flow():
    """第十批：清空整张课表 —— 提案 → 弹窗 → 前端按钮触发后端删除。

    需求里的五条硬约束逐条对应：
      1. 只出提案，AI 不许自己调删除接口、不许谎称已删；
      2. 触发提案后在前端弹确认弹窗（确认 / 取消）；
      3. 点确认由后端执行删除当前学生课表，点取消放弃；
      4. 删完自动刷新课表视图；
      5. 严禁模型自己删库，删除动作必须来自前端按钮触发后端。
    """
    import json as _json
    import os
    import pathlib

    from app.agent.pending import (clear_pending, peek_pending, save_pending,
                                   take_pending)
    from app.modules.planner import get_timetable, propose_clear_timetable
    from app.store import save_timetable as _st

    title("10. 清空课表：提案 → 弹窗 → 前端按钮触发后端删除")
    c = Checker()
    with sandbox():
        client = make_client()
        seed = _json.load(open(os.path.join(REAL_DATA_DIR, "student", "timetable.json"),
                               encoding="utf-8"))
        _st(seed["courses"])
        n0 = len(get_timetable())
        c.check("起始周表有课可清", n0 >= 10, f"{n0} 门课")

        # —— ① 出提案：只读，一个字节都不写库 ——
        raw = propose_clear_timetable(reason="学生要求清空")
        p = _json.loads(raw)["__proposal__"]
        c.check("清空提案 kind=timetable_clear（跟普通改课提案区分开）",
                p["kind"] == "timetable_clear")
        c.check("提案带上要清掉几门课（弹窗要显示）",
                p.get("clear_count") == n0, str(p.get("clear_count")))
        c.check("出提案这件事本身没动数据库", len(get_timetable()) == n0,
                f"仍 {len(get_timetable())} 门")
        c.check("文案里带上了学生的理由", "学生要求清空" in (p.get("summary") or ""))

        # —— ② 模型工具箱里没有任何删除接口，只有提案工具 ——
        from app.modules.planner import build_tools
        tb = build_tools()
        clear_tool = tb.get("propose_clear_timetable")
        c.check("工具箱注册了 propose_clear_timetable", clear_tool is not None)
        if clear_tool:
            c.check("该工具的说明书里点名不许谎称已删除",
                    "已经帮你清空" in (clear_tool.description or ""))
        # 只挑"真会写库"的工具：名字里带删/清/改，且不是 propose_ 开头的提案工具
        wrote = [k for k in tb
                 if any(w in k.lower() for w in ("delete", "clear", "remove", "drop", "purge"))
                 and not k.startswith("propose_") and "todo" not in k]
        c.check("工具箱里没有任何课表写入/删除类工具（模型够不着删除接口）",
                wrote == [], str(wrote))

        # —— ③ 聊天里回确认类文字 = 备选方式，同样要执行（需求③）——
        #     新规格：点弹窗【确认】是优先方式，在聊天框回"确认添加/确认删除"这类文字
        #     是**备选方式，同样触发本次变更提案的后端执行**。
        #     （早先这里为了保护"删空不可恢复"加了"必须点按钮"的闸门，
        #      结果学生回「确认」毫无反应，被当成"删除失败"投诉了一轮。闸门让位给规格。）
        save_pending("sess-clear", [p])
        r = client.post("/api/chat", json={
            "message": "确认删除", "module": "planner", "session_id": "sess-clear",
        })
        d = r.json()
        c.check("聊天回「确认删除」同样触发执行（备选方式）",
                len(get_timetable()) == 0, f"还剩 {len(get_timetable())} 门")
        c.check("执行完回复里说清了清掉几门",
                str(n0) in (d.get("answer") or ""), (d.get("answer") or "")[:60])
        c.check("执行完不再挂着提案（不会重复删）",
                peek_pending("sess-clear") is None)
        # 重新播种，后面几步还要用
        _st(seed["courses"])

        # —— ④ 没有前端这一下，后端接口必须拒绝 ——
        #     先把提案作废（模拟学生点了【取消】/ 提案过期 / 服务重启），
        #     修复后聊天确认会重新挂一张提案，不先作废的话接口是合法的。
        clear_pending("sess-clear")
        r2 = client.post("/api/timetable/clear", json={"session_id": "sess-clear"})
        c.check("没带 confirm=true 时后端拒绝清空", r2.status_code == 400,
                str(r2.status_code))
        r3 = client.post("/api/timetable/clear", json={
            "session_id": "sess-clear", "confirm": True})
        c.check("没有待确认提案时拒绝清空（防止被随手调用）", r3.status_code == 400,
                str(r3.status_code))
        c.check("两次被拒之后课表依然完好", len(get_timetable()) == n0)

        # —— ⑤ 前端按钮点了：后端才真正删，且只删当前学生的周表 ——
        save_pending("sess-clear", [p])
        r4 = client.post("/api/timetable/clear", json={
            "session_id": "sess-clear", "confirm": True})
        d4 = r4.json()
        c.check("点确认后接口返回 200", r4.status_code == 200, str(r4.status_code))
        c.check("返回里说明了清掉几门", d4.get("removed") == n0, str(d4.get("removed")))
        c.check("周表真的空了", len(get_timetable()) == 0)
        c.check("删除执行完，待确认提案被清掉（不会重复删）",
                peek_pending("sess-clear") is None)
        r5 = client.post("/api/timetable/clear", json={
            "session_id": "sess-clear", "confirm": True})
        c.check("重复点也不会二次伤害", r5.status_code == 400, str(r5.status_code))

        # —— ⑥ 空表再出提案：要告诉学生"本来就是空的" ——
        again = propose_clear_timetable()
        c.check("空表时提示没课可清，而不是硬出一张卡", "本来就是空的" in again, again[:40])

        # —— ⑦ 前端页面：确认 UI 是**单独一个页面**（/confirm），
        #     但按学生的要求**不单独弹窗**，而是做成一条确认条嵌在聊天流里 ——
        #     需求点名"弹窗为单独 ui 页面"，所以确认 UI 搬到 app/static/confirm.html；
        #     学生端出提案时动态建一条 .cf-inline 贴在消息下方，把 /confirm 用 iframe 载进来，
        #     并接住它回传的确认/取消结果。
        proj = pathlib.Path(__file__).resolve().parent.parent
        page = (proj / "app" / "static" / "student.html").read_text(encoding="utf-8")
        cf = (proj / "app" / "static" / "confirm.html").read_text(encoding="utf-8")
        for needle, why in (
            ('cfWrap.className = "cf-inline"', "确认条是内嵌容器（附着在聊天框上，不是全屏遮罩）"),
            ('(container || log).appendChild(cfWrap)', "确认条插在消息流里、那条消息的下面"),
            ('/confirm?session=', "确认条内容是 /confirm 这个单独的确认页面"),
            ('campustime-confirm', "接住独立页面回传的确认/取消结果"),
            ('d.type === "height"', "确认条按内容真实高度自适应（不留白、不挤出按钮）"),
            ('loadScheduleView()', "入库成功后自动刷新课表/待办面板"),
        ):
            c.check(why, needle in page)
        c.check("确认改课不再用全屏遮罩弹窗（学生明确要求过）",
                'id="ttClearMask"' not in page)
        for needle, why in (
            ('id="cfOk"', "独立页面上有【确认】按钮"),
            ('id="cfCancel"', "独立页面上有【取消】按钮"),
            ('body.embed', "被嵌进聊天流时切成紧凑样式（不居中、不留白）"),
            ('reportHeight', "把内容真实高度报给父页面，好让确认条贴合内容"),
            ('/api/timetable/clear', "清空走的是清空专用接口"),
            ('confirm: true', "删除必须由前端显式确认才发得出去"),
            ('/api/timetable/apply', "课表变更走确定性写入口"),
            ('/api/todos', "加待办走待办写入口"),
            ('tellParent("cancelled"', "点取消只是关掉，不会去调写入接口"),
        ):
            c.check(why, needle in cf)
        src = (proj / "app" / "main.py").read_text(encoding="utf-8")
        c.check("/confirm 路由存在（弹窗可单独打开验收）",
                '@app.get("/confirm"' in src)
        # 只读提案接口：独立页面靠它取"这次要改什么"
        c.check("只读提案接口 /api/pending/{sid} 存在",
                '@app.get("/api/pending/{sid}")' in src)

        # —— ⑧ 系统提示：只出提案、不许谎称已删、不许替学生决定 ——
        from app.main import ASSISTANT_RULES
        from app.modules.planner import build_system_prompt as planner_prompt
        sent = planner_prompt() + ASSISTANT_RULES
        c.check("提示里要求清空必须先出确认弹窗", "propose_clear_timetable" in sent)
        c.check("提示里点名不许宣布删除结果（不许谎称已删）", "不许宣布结果" in sent)
        c.check("提示里说明模型没有任何删除接口", "没有任何删除接口" in sent)
        # 上一轮的教训：把禁用原句写进提示反而会教会模型
        c.check("提示里没出现禁用原句本身", "我已经帮你清空了" not in sent)
    return c.summary("第十批（清空课表 → 前端确认 → 后端删除）")


def test_clear_intent_and_gate():
    """第十一批：清空意图的**确定性判定** + 删除接口的闸门。

    为什么要单独钉死这两块：
        「学生是不是要清空整张课表」以前交给模型判断，实测同一句话模型时调工具、时反问，
        链路会当场断掉。现在由 wants_clear_timetable 由代码定死；
        而 /api/timetable/clear 是唯一的删库入口，必须做到
        "没有前端这一下，谁也删不动"。
    """
    from app.modules.planner import wants_clear_timetable

    title("11. 清空意图判定与删除接口闸门")
    c = Checker()

    # —— ① 清空意图：该认的认，不该认的绝不误伤（删单节课绝不能被当成清表）——
    should_clear = [
        "把课表全部删除，一门都不留", "清空课表", "把课表清空", "课表全删了",
        "帮我把课表里的课都删掉", "帮我把全部课表删掉", "所有课程一门都不留",
        "整个课表都不要了",
    ]
    not_clear = [
        "帮我删掉周一第一节的高数", "把周一的高数删掉", "重新排一下课表",
        "我想调整一下课程时间", "帮我删除周一 08:00 的那节课", "这门课我不选了，删掉",
        "我的待办全部删除", "下周的考试全删掉", "今天的待办都删掉", "",
    ]
    for t in should_clear:
        c.check(f"认得出来：{t[:14]}", wants_clear_timetable(t) is True)
    for t in not_clear:
        c.check(f"不会误伤：{t[:14] if t else '(空串)'}", wants_clear_timetable(t) is False)

    import json as _json
    import os

    from app.agent.pending import peek_pending, save_pending
    from app.modules.planner import get_timetable, propose_clear_timetable
    from app.store import save_timetable as _st

    with sandbox():
        client = make_client()
        seed = _json.load(open(os.path.join(REAL_DATA_DIR, "student", "timetable.json"),
                               encoding="utf-8"))
        _st(seed["courses"])
        n0 = len(get_timetable())

        # —— ② 没出提案之前，删除接口必须拒绝 ——
        r = client.post("/api/timetable/clear", json={"session_id": "nope", "confirm": True})
        c.check("没有待确认提案时删除被拒", r.status_code == 400, f"HTTP {r.status_code}")

        # —— ③ 出了提案但不带 confirm，也删不动 ——
        prop = _json.loads(propose_clear_timetable())["__proposal__"]
        save_pending("sess-k", [prop])
        r2 = client.post("/api/timetable/clear", json={"session_id": "sess-k"})
        c.check("没点确认（不带 confirm）被拒", r2.status_code == 400, f"HTTP {r2.status_code}")
        c.check("拒绝之后课表一门没少", len(get_timetable()) == n0)

        # —— ④ 学生点【取消】= 后端作废提案，之后谁再调都删不动 ——
        r3 = client.post("/api/timetable/clear",
                         json={"session_id": "sess-k", "confirm": False, "abandon": True})
        c.check("取消后后端回执已作废", r3.json().get("abandoned") is True)
        c.check("作废的提案已被取走", peek_pending("sess-k") is None)
        r4 = client.post("/api/timetable/clear", json={"session_id": "sess-k", "confirm": True})
        c.check("取消后再调删除接口照样被拒", r4.status_code == 400, f"HTTP {r4.status_code}")
        c.check("取消之后课表原封不动", len(get_timetable()) == n0)

        # —— ⑤ 周表本来就是空的：要说清楚，不能把学生打发回模型反复确认 ——
        #    实测这个状态会掉回模型，模型回一句"这个操作影响比较大，我先跟你确认一下…"
        #    还列四条问题——学生只看到弹窗不来、卡片不出，以为是系统坏了。
        _st([])
        r7 = client.post("/api/chat", json={
            "message": "把课表全部删除，一门都不留", "module": "planner", "session_id": "sess-empty",
        }).json()
        answer = r7.get("answer") or ""
        c.check("空表时直接说明没有课可清", "本来就是空的" in answer, answer[:60])
        c.check("空表时不弹提案也不留待确认", not r7.get("options") and peek_pending("sess-empty") is None)

        # —— ⑥ 带上 confirm 才执行 ——
        #    重新播一份课表（⑤ 为了测空表分支已经把它清了），确认删除真的按报告的数字删
        _st(seed["courses"])
        n_now = len(get_timetable())
        save_pending("sess-m", [prop])
        r5 = client.post("/api/timetable/clear", json={"session_id": "sess-m", "confirm": True})
        c.check("点确认后删除成功", r5.json().get("removed") == n_now, str(r5.json())[:80])
        c.check("当前登录学生的周表被清空", len(get_timetable()) == 0)
        r6 = client.post("/api/timetable/clear", json={"session_id": "sess-m", "confirm": True})
        c.check("提案用掉后再调不会重复清（已无提案）", r6.status_code == 400)
    return c.summary("第十一批（清空意图判定 + 删除接口闸门）")


def test_confirm_ui_page_and_alt_confirm():
    """第十二批：弹窗是单独 UI 页面 + 聊天回确认文字（备选方式）也能执行。

    需求③说确认有两条路：点弹窗【确认】（优先）、在聊天框回"确认添加/确认删除"
    这类文字（备选）。需求还点名"弹窗为单独 ui 页面"。这一批把两件事都钉住。
    """
    import datetime
    import json as _json
    import os
    import pathlib

    from app.agent.pending import clear_pending, is_confirmation, save_pending
    from app.modules.planner import get_timetable
    from app.store import save_timetable as _st

    title("12. 独立确认页面 + 聊天确认文字（备选方式）")
    c = Checker()
    proj = pathlib.Path(__file__).resolve().parent.parent

    # —— ① 确认词判定：认得"确认+动作"，认不得提问和新指令 ——
    for yes in ("确认", "好的", "确认删除", "确认添加", "确认清空", "确认加入",
                "好的，加入吧", "确认删除周一第一节", "行，删除", "可以改"):
        c.check(f"「{yes}」算点头", is_confirmation(yes))
    for no in ("确认一下周一的课表是什么", "帮我看看周一第一节是什么",
               "明天加一节高数", "周末有什么安排吗", "删除周一的课", ""):
        c.check(f"「{no}」不算点头（不该被听成确认）", not is_confirmation(no))

    with sandbox():
        client = make_client()
        seed = _json.load(open(os.path.join(REAL_DATA_DIR, "student", "timetable.json"),
                               encoding="utf-8"))
        _st(seed["courses"])
        n0 = len(get_timetable())

        # —— ② 只读提案接口：独立确认页靠它取"这次要改什么"，且绝不写库 ——
        c.check("没有提案时返回空",
                client.get("/api/pending/nobody").json().get("pending") is None)
        prop = {"kind": "timetable_clear", "summary": "清空全部课程", "clear_count": n0}
        save_pending("sess-pending", [prop])
        d = client.get("/api/pending/sess-pending").json()
        c.check("有提案时读得回来", (d.get("pending") or {}).get("kind") == "timetable_clear")
        c.check("读提案不写库（周表门数不变）", len(get_timetable()) == n0,
                f"{n0} → {len(get_timetable())}")
        c.check("超长 session_id 不炸",
                client.get("/api/pending/" + "x" * 200).json().get("pending") is None)
        c.check("空 session_id 不炸",
                client.get("/api/pending/").status_code in (200, 404))

        # 写入过的提案不再展示（避免重复写）
        from app.agent.pending import mark_applied
        mark_applied("sess-pending")
        c.check("已执行的提案不再展示（不会重复写）",
                client.get("/api/pending/sess-pending").json().get("pending") is None)
        clear_pending("sess-pending")

        # —— ③ 备选方式：聊天回「确认添加」把待办真正写进日程 ——
        from app.store import list_todos
        today = datetime.date.today().isoformat()
        n_todo = len(list_todos(today))
        save_pending("sess-alt", [{
            "kind": "todo_add", "title": "背单词", "date": today,
            "start": "21:00", "end": "21:30", "weekday": "周四", "minutes": 30,
        }])
        r = client.post("/api/chat", json={
            "message": "确认添加", "module": "planner", "session_id": "sess-alt",
        })
        c.check("回「确认添加」把待办写进日程（备选方式）",
                any(t["title"] == "背单词" for t in list_todos(today)),
                (r.json().get("answer") or "")[:60])

        # —— ④ 备选方式：聊天回「确认删除」把课表变更提案写进周表 ——
        from app.modules.planner import propose_course_change
        raw = propose_course_change("remove", day=1, course="高等数学")
        p = _json.loads(raw)["__proposal__"]
        save_pending("sess-alt2", [p])
        r2 = client.post("/api/chat", json={
            "message": "确认删除", "module": "planner", "session_id": "sess-alt2",
        })
        c.check("回「确认删除」把课表变更写进周表（备选方式）",
                len(get_timetable()) == n0 - 1,
                f"{n0} → {len(get_timetable())} 门")
        c.check("回复里说清了执行结果", "已" in (r2.json().get("answer") or ""),
                (r2.json().get("answer") or "")[:60])

        # —— ⑤ 确定性删课分支：学生原话直接出弹窗，不再赌模型调不调工具 ——
        #     真模型最爱"只在文字里给个预览、让学生回「确认」"，可它压根没调出提案工具
        #     ——暂存里什么都没有，学生回了确认也是白回（弹窗不来、确认不删、删除失败）。
        #     ④ 已经把周一第一节删了，这里重新播种一份完整的，从头验"学生原话 → 弹窗"。
        _st(seed["courses"])
        n_full = len(get_timetable())
        client.post("/api/chat/reset", json={"session_id": "sess-rm"})
        from app.modules.planner import parse_remove_course, wants_remove_course
        c.check("「删除周一第一节课」被认成删课", wants_remove_course("删除周一第一节课"))
        c.check("「明天不上课」「把这条消息删了」不被误认",
                not wants_remove_course("明天不上课")
                and not wants_remove_course("把这条消息删了"))
        r3 = client.post("/api/chat", json={
            "message": "删除周一第一节课", "module": "planner", "session_id": "sess-rm",
        })
        d3 = r3.json()
        opts3 = d3.get("options") or []
        c.check("删课由系统出提案（弹窗确认，不再只回文字预览）",
                any(o.get("kind") == "timetable_change" for o in opts3),
                (d3.get("answer") or "")[:70])
        c.check("删的是周一第一节（高等数学 08:00）",
                bool(opts3) and "周一" in str(opts3[-1].get("summary", ""))
                and "高等数学" in str(opts3[-1].get("summary", "")),
                str(opts3[-1].get("summary", ""))[:60] if opts3 else "")
        c.check("出提案阶段周表没动", len(get_timetable()) == n_full,
                f"{n_full} → {len(get_timetable())}")
        # 点弹窗确认 → 写入
        card = next(o for o in opts3 if o.get("kind") == "timetable_change")
        client.post("/api/timetable/apply", json={
            "courses": card.get("courses"), "session_id": "sess-rm"})
        c.check("确认后真的少了一门", len(get_timetable()) == n_full - 1,
                f"{n_full} → {len(get_timetable())}")
        c.check("周一第一节的课没了",
                not any(c["day"] == 1 and c["start"] == "08:00" for c in get_timetable()))
        # 当天好几节课又没说哪节 → 系统追问，绝不瞎删
        r4 = client.post("/api/chat", json={
            "message": "删掉周三的课", "module": "planner", "session_id": "sess-rm2"})
        d4 = r4.json()
        c.check("「删掉周三的课」没说哪节时系统追问（不瞎删也不交模型）",
                not (d4.get("options") or []) and (
                    "具体" in (d4.get("answer") or "") or "节" in (d4.get("answer") or "")),
                (d4.get("answer") or "")[:60])
        c.check("追问这轮周表没动", len(get_timetable()) == n_full - 1,
                f"{n_full - 1} → {len(get_timetable())}")

        # —— ⑥ 追问后的补充回答：学生只说「周一的第一节」也要出弹窗 ——
        #     这句话里没有"删除"两个字，wants_remove_course 认不出来；
        #     不接住就会掉回模型，模型会嘴上说"删课提案已经生成啦"（根本没生成）。
        #     正确行为：上一轮系统刚追问过"想删哪一节"，这句补充必须接着处理。
        client.post("/api/chat/reset", json={"session_id": "sess-rm3"})
        _st(seed["courses"])
        r5 = client.post("/api/chat", json={
            "message": "删掉周一的课", "module": "planner", "session_id": "sess-rm3"})
        d5 = r5.json()
        c.check("周一有好几节课时先追问", not (d5.get("options") or []))
        r6 = client.post("/api/chat", json={
            "message": "周一的第一节", "module": "planner", "session_id": "sess-rm3"})
        d6 = r6.json()
        opts6 = d6.get("options") or []
        c.check("补充「周一的第一节」直接出删课弹窗（不再掉回模型）",
                any(o.get("kind") == "timetable_change" for o in opts6),
                (d6.get("answer") or "")[:70])
        c.check("提案就是周一 08:00 那节",
                "周一" in str(opts6[-1].get("summary", "")) and "08:00" in str(opts6[-1].get("summary", ""))
                if opts6 else False,
                str(opts6[-1].get("summary", ""))[:50] if opts6 else "")
    return c.summary("第十二批（独立确认页面 + 聊天确认备选方式 + 确定性删课）")


if __name__ == "__main__":
    code = 0
    code |= test_express_view_api()
    code |= test_takeout_view_api()
    code |= test_student_panel_markup()
    code |= test_regression_existing_apis()
    code |= test_plan_month_calendar()
    code |= test_timetable_import_entry()
    code |= test_persona_rules_and_workorders()
    code |= test_course_change_proposals()
    code |= test_chat_confirm_applies_timetable()
    code |= test_clear_timetable_flow()
    code |= test_clear_intent_and_gate()
    code |= test_confirm_ui_page_and_alt_confirm()
    sys.exit(code)
