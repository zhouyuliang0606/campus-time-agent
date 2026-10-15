"""#18 单子 2：学生报的快递/外卖问题**流到驿站** —— 接口、文案、以及新增免鉴权面的边界。

三件要钉的事：
  1. 驿站两个口子能用、改的就是同一份 `workorders.json`（两个视图不许各说各话）；
  2. **新增免鉴权面不许顺手把管理端口也带开** —— 管理端那两个接口仍必须要口令（A 批倒数两条专打这个）；
  3. 文案是**一整族**：施工单只列了 3 处，实测 24 处 / 9 个文件，而且里面有两类语义 ——
     「报障去向」该改成驿站；而「拿上报当挡箭牌」那两处禁的是模型真机说过的原话
     （「我帮你反馈给管理端」），**只能两个词都禁**：把 管理端 换成 驿站
     等于把那句被禁的原话放回许可区，闸就漏了。
"""
import json
import pathlib

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

# 去向类说法（学生看得见的那种"要报给谁"），全站只该剩驿站
DEST_WORDS = ("上报给管理端", "上报管理端", "推送管理端", "不上报管理端", "立案提交至管理端")


def _wo_rows():
    with open(data_path("workorders.json"), encoding="utf-8") as f:
        return json.load(f)


def _one(wo_id):
    return next((w for w in _wo_rows() if w.get("id") == wo_id), {})


def batch_a_station_api():
    c = Checker()
    title("A. 驿站侧接口：能列、能改状态，而且改的就是同一份台账")
    with sandbox():
        client = make_client()
        r = client.post("/api/workorders", json={"kind": "疑似丢件", "desc": "一箱书",
                                                 "source": "panel"})
        wo_id = r.json()["workorder"]["id"]

        lst = client.get("/api/station/workorders")
        ids = [w.get("id") for w in (lst.json().get("workorders") or [])]
        c.check("驿站能列（不带头、不要口令也读得到，演示级），刚报的那条在列表里",
                lst.status_code == 200 and wo_id in ids, f"{lst.status_code} / {len(ids)} 条")

        up = client.post(f"/api/station/workorders/{wo_id}/status", json={"status": "处理中"})
        c.check("驿站改状态 → workorders.json 里那条真的变了（不是只回个 ok）",
                up.status_code == 200 and _one(wo_id).get("status") == "处理中",
                f"{up.status_code} / 台账里={_one(wo_id).get('status')}")

        again = client.get("/api/station/workorders")
        got = next((w for w in (again.json().get("workorders") or [])
                    if w.get("id") == wo_id), {})
        c.check("两个视图读的是同一份台账：状况一致，不会各说各话",
                got.get("status") == "处理中", str(got.get("status")))

        bad = client.post(f"/api/station/workorders/{wo_id}/status", json={"status": "已关闭"})
        nf = client.post("/api/station/workorders/nosuch99/status", json={"status": "已解决"})
        c.check("非法状态 400、不存在的工单 404（驿站口不因为是新的就放松校验）",
                bad.status_code == 400 and nf.status_code == 404,
                f"{bad.status_code} / {nf.status_code}")
        c.check("被拒的那次没把台账写脏",
                _one(wo_id).get("status") == "处理中", "状态被改坏了")

        # ⚠️ 本单最容易出的事故：新加一个不要口令的驿站口，顺手把管理端那两个也改成不要口令。
        # 这里必须 `admin_token=None` —— 不是笔误：`make_client()` 的**默认值就自带口令**
        # （第一版我直接 make_client() 打"无口令"，结果两条断言以"200/200"红给我自己看：
        #  测的根本不是无口令那条路，正是指南 4i「测的必须是走到的」）。
        anon = make_client(admin_token=None)
        no1 = anon.get("/api/workorders")
        no2 = anon.post(f"/api/workorders/{wo_id}/status", json={"status": "已解决"})
        c.check("管理端那两个接口仍然要口令（新增免鉴权面不许顺手带开旧端口）",
                no1.status_code in (401, 403) and no2.status_code in (401, 403),
                f"{no1.status_code} / {no2.status_code}")
        c.check("没口令那次确实没写进去（写了就是严重回归）",
                _one(wo_id).get("status") == "处理中", str(_one(wo_id).get("status")))
        c.check("驿站口相反：不带口令也读得到（这就是它存在的意义 —— 驿站没有管理口令）",
                anon.get("/api/station/workorders").status_code == 200, "")
    return c


def batch_b_wording():
    c = Checker()
    title("B. 文案整族：去向改驿站，但挡箭牌那两个词都得继续禁着")
    files = [p for p in pathlib.Path("app").rglob("*.py")] + \
            [p for p in pathlib.Path("app").rglob("*.html")]
    hits = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            # 允许留下的只有"黑名单原话"那一类：它们禁的是模型真说过的句子，
            # 把 管理端 换成 驿站 就等于把那句原话放回许可区（闸反而漏了）——
            # 判据是行内有"禁止/挡箭牌/同样禁"这类上下文，不是"这行看着像注释"。
            if any(k in line for k in ("禁止", "挡箭牌", "同样禁")):
                continue
            hits += [f"{f}:{i} {w}" for w in DEST_WORDS if w in line]
    c.check("去向类说法全站不再出现（黑名单里的原话按上下文放行，别的都算漏改）",
            not hits, "; ".join(hits[:2])[:130])

    main_src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    c.check("学生看得见的问句改成驿站（要不要上报驿站、先问一句要不要上报给驿站）",
            "要不要**上报驿站**立案跟进" in main_src and "先问一句「要不要上报给驿站？」" in main_src,
            "")
    conf = pathlib.Path("app/static/confirm.html").read_text(encoding="utf-8")
    stu = pathlib.Path("app/static/student.html").read_text(encoding="utf-8")
    c.check("确认卡标题、回执文案、学生端提示三处都跟着改（漏一处界面就自相矛盾）",
            'title: "上报驿站"' in conf and "工单已上报驿站" in conf and "上报驿站提案" in stu, "")

    planner = pathlib.Path("app/modules/planner.py").read_text(encoding="utf-8")
    sched = pathlib.Path("app/modules/schedule.py").read_text(encoding="utf-8")
    # 这两处断言的字面量里带 ASCII 双引号（那是模型原话的形状），所以整条用单引号包
    c.check("挡箭牌那两处仍是两个词都禁：原话「管理端」留着，换名后的「驿站」也加进黑名单",
            '我帮你反馈给管理端' in planner
            and '换成"让驿站看一下/我帮你反馈给驿站"同样禁止' in planner
            and "（换成驿站也一样禁）" in sched, "")

    st = pathlib.Path("app/static/station.html").read_text(encoding="utf-8")
    c.check("驿站端有这块 UI：列表 + 状态按钮 + 与管理台同节奏的 5 秒轮询",
            "/api/station/workorders" in st and "WO_NEXT" in st and "待处理问题" in st
            and "setInterval(loadWorkorders, 5000)" in st, "")
    c.check("渲染学生写的话一律走 esc()（全站唯一转义来源，防 XSS）",
            "esc(w.desc ||" in st and "esc(w.kind ||" in st, "")
    return c


def batch_c_shape():
    c = Checker()
    title("C. 形状：状态校验只有一份实现，免鉴权是明写的不是漏的")
    src = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    c.check("状态白名单只有一处，两个入口共用同一个实现",
            src.count("_WO_STATUSES = ") == 1
            and src.count("return _workorder_status_response(wo_id, body)") == 2,
            f"共用 {src.count('return _workorder_status_response(wo_id, body)')} 处")
    seg = src[src.index("# ============ 驿站侧接口"):src.index('@app.post("/api/admin/notify"')]
    c.check("驿站那一段明写「演示级不挂口令 + 上线要补驿站口令」（诚实标注，不是忘了）",
            "不挂管理口令" in seg and "上线" in seg, "")
    c.check("驿站那两个路由确实没挂 verify_admin（挂了就不是驿站能用的口）",
            "verify_admin" not in seg, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_52_station_workorders：驿站接活（接口 + 文案整族 + 免鉴权边界）")
    print("=" * 60)
    code |= batch_a_station_api().summary("A 批（接口）")
    code |= batch_b_wording().summary("B 批（文案整族）")
    code |= batch_c_shape().summary("C 批（形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
