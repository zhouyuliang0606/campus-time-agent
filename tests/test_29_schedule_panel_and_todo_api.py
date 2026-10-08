"""学生端「我的日程」周视图 = 逐日明细列表 + /api/todos 读写通路。

2026-10-08 用户「把这个周时间轴去掉，保留逐日明细」，本文件随之换口径：
  旧批 1/2 钉的是"时间轴前端接线 + 时间轴样式存在"，功能已删，留着只会永远红；
  现在钉的是**删干净**（无 tl 前缀容器/函数/样式残留）+ **删完逐日明细还完整**
  （周列表能渲染、tab 能切、前后翻周仍能刷新）+ **接口往返不变**。

为什么删功能还要留测试：这条写库通路（POST/PUT/DELETE /api/todos）同时服务 /plan
和待办面板，时间轴只是它的第三个调用方。调用方少一个，接口契约一个字都不能变。

三条红线沿用原文件（本文件逐条钉住）：
  ① 破坏性操作（删）必须有确认弹窗；
  ② 写库走既有确定性接口，不新增后端接口、不让模型直连写库；
  ③ 面板代码不碰 todo_id（那是 confirm 提案卡用的字段）。
"""
import datetime

from _harness import Checker, title, make_client, sandbox

SRC = "app/static/student.html"


def _seg(src, start, end):
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find(end, i)
    return src[i: j if j > i else i + 6000]


def batch_a_removed_clean_and_week_view_intact():
    c = Checker()
    title("1. 周时间轴删干净 + 逐日明细完好（源码断言）")
    src = open(SRC, encoding="utf-8").read()

    # —— 删干净：容器 / 函数 / 常量 / 样式四类残留都应为 0 ——
    for token in ('id="tlWrap"', 'id="tlGrid"', 'id="tlFormBox"', 'id="tlNote"', 'id="tlRange"',
                  'function tlRender', 'function tlOpenForm', 'tlSave', 'tlDelete',
                  'TL_START', 'TL_HOUR_H', '.tl-grid', '.tl-evt', '.tl-daycol'):
        c.check(f"无时间轴残留：{token}", token not in src, f"{SRC} 里还能搜到 {token}")

    # —— 逐日明细：容器 + 渲染函数 + 小标题都还在，且真的被调用 ——
    c.check("view-schedule 内有周列表容器 schWeek", 'id="schWeek"' in src)
    c.check("周列表渲染函数 renderSchWeek 已定义", "function renderSchWeek()" in src)
    # 必须钉在 setSchTab 这一段里：renderSchCurrent 也调 renderSchWeek()，
    # 只搜全文件"renderSchWeek();"会因为别处还有这句而永远绿（M2 变异实测不红）。
    seg_tab = _seg(src, "function setSchTab()", 'document.getElementById("schPrev")')
    c.check("setSchTab 切到周视图时确实渲染逐日明细",
            "renderSchWeek()" in seg_tab,
            "setSchTab 里没调 renderSchWeek()，切 tab 后列表是空的")
    c.check("逐日明细小标题存在且不提「时间轴下方」",
            'id="schWeekHead"' in src and "逐日明细" in src and "时间轴下方" not in src)

    # —— tab 与翻页：周视图/月表两档，翻周仍走同一条刷新路径 ——
    c.check("周视图/月表两个 tab 的监听都还在",
            'document.getElementById("tabWeek").addEventListener' in src
            and 'document.getElementById("tabMonth").addEventListener' in src)
    seg = src[src.find('document.getElementById("schPrev").addEventListener'):]
    c.check("前后翻周：周视图改 schWeekOffset、月表改 schMonthOffset 后统一 setSchTab",
            "schMonthOffset--" in seg[:400] and "schWeekOffset--" in seg[:400]
            and "setSchTab()" in seg[:400])
    c.check("setSchTab 里不再有 tlWrap 显隐", "tlWrap" not in src[src.find("function setSchTab"):])
    return c


def batch_b_panel_contract_unchanged():
    c = Checker()
    title("2. 面板契约不变：数据源同源、只读展示、不碰提案卡字段")
    src = open(SRC, encoding="utf-8").read()

    c.check("周列表数据仍取 /api/timetable + /api/todos（与 /plan 同源）",
            'fetch("/api/timetable")' in src and 'fetch("/api/todos")' in src)
    # 时间轴曾是面板里唯一"学生直接改待办时间"的入口；删掉后「我的日程」恢复只读定位，
    # 写库回到三处：聊天确认卡、待办面板勾选/删除、/plan 整页。
    seg = _seg(src, "// ---- ① 我的日程", "// 给管家的导入指令")
    c.check("「我的日程」面板代码段内没有任何写库 fetch（面板只读）",
            seg and not any(m in seg for m in ('method: "POST"', 'method: "PUT"', 'method: "DELETE"')),
            "面板段里出现了写接口调用，「我的日程」就不只是用来看的了")
    c.check("面板代码不出现 todo_id（那是 confirm 提案卡的字段）",
            "todo_id" not in src[src.find("function loadScheduleView"):src.find("function loadScheduleView") + 4000])
    c.check("回归守卫：已弃用逐格贴块的 tlOverlapsHour 没有复活",
            "tlOverlapsHour" not in src)
    return c


def batch_c_api_roundtrip():
    c = Checker()
    title("3. 接口往返：增/改/删确实落到 /api/todos 同源数据（沙箱，零污染真实数据）")
    with sandbox():
        cli = make_client()
        d = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()

        r = cli.post("/api/todos", json={"title": "明细测试-去图书馆", "date": d,
                                         "start": "10:00", "end": "11:00"})
        c.check("POST /api/todos 新增成功", r.status_code == 200 and r.json().get("ok"))
        tid = r.json()["todo"]["id"]

        lst = cli.get("/api/todos").json()["todos"]
        c.check("新增后 GET /api/todos 能看到（同源）",
                any(t["id"] == tid for t in lst))

        r = cli.put(f"/api/todos/{tid}", json={"title": "明细测试-改后", "start": "15:00", "end": "16:00"})
        c.check("PUT /api/todos/:id 编辑成功", r.status_code == 200 and r.json()["todo"]["title"] == "明细测试-改后")
        it = [t for t in cli.get("/api/todos").json()["todos"] if t["id"] == tid]
        c.check("编辑后标题与时间都已更新",
                it and it[0]["title"] == "明细测试-改后" and it[0]["start"] == "15:00")

        r = cli.request("DELETE", f"/api/todos/{tid}", json={})
        c.check("DELETE /api/todos/:id 删除成功", r.status_code == 200 and r.json().get("ok"))
        after = cli.get("/api/todos").json()["todos"]
        c.check("删除后 GET /api/todos 不再包含（持久化）",
                not any(t["id"] == tid for t in after))

        # 红线①：面板里剩下的那条删除路径（待办面板）必须仍然先 confirm
        src = open(SRC, encoding="utf-8").read()
        c.check("删除待办前仍有 confirm 确认弹窗（绝不一点就删）",
                "confirm(" in src and "确定删除这条待办？" in src)
    return c


def main():
    results = [batch_a_removed_clean_and_week_view_intact(),
               batch_b_panel_contract_unchanged(),
               batch_c_api_roundtrip()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 我的日程（逐日明细 + 待办接口）总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
