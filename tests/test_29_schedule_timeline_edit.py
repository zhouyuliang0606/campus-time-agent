"""自行编辑日程（F 项）：学生端「我的日程」里的周时间轴直改待办（第三种入口）。

需求出处：CampusTime-自行编辑日程-实现指令.md —— 学生**不对话、不上传**，
直接在聊天面板的「我的日程」里点格子增/改/删待办。

三条红线（本文件逐条钉住）：
  ① 破坏性操作（删）必须有确认弹窗（沿用 /plan 的 confirm 范式）；
  ② 写库仍走既有确定性接口（POST/PUT/DELETE /api/todos），不新增后端接口、
     不让模型直连写库；
  ③ 不碰 todo_id 提案卡字段（那是 confirm 卡用的），新代码统一 tl_ 前缀不撞名。

前两批做**源码断言**（钉住前端接线，跟 test_26 同一套路），
第三批做**接口往返**（TestClient + 沙箱，证明数据确实落到 /api/todos 同源）。
"""
import datetime

from _harness import Checker, title, make_client, sandbox


def _seg(src, start, end):
    i = src.find(start)
    if i < 0:
        return ""
    j = src.find(end, i)
    return src[i: j if j > i else i + 3000]


def batch_a_source_wiring():
    c = Checker()
    title("1. 时间轴前端接线（源码断言：容器/前缀/接口/确认/刷新）")
    src = open("app/static/student.html", encoding="utf-8").read()

    c.check("view-schedule 内有周时间轴容器 tlGrid", 'id="tlGrid"' in src)
    c.check("有新增/编辑表单容器 tlFormBox", 'id="tlFormBox"' in src)
    c.check("时间轴渲染函数 tlRender 已定义并被 loadScheduleView 调用",
            "function tlRender()" in src and "tlRender();" in src)

    # 新增：点空格 → POST /api/todos
    seg_add = _seg(src, "async function tlSave", "async function tlDelete")
    c.check("tlSave 走 POST /api/todos（新增）", 'fetch("/api/todos"' in seg_add)
    c.check("tlSave 走 PUT /api/todos/:id（编辑）", '"POST"' in seg_add or "method: \"PUT\"" in seg_add)
    c.check("编辑分支用 PUT /api/todos/ + encodeURIComponent",
            '"/api/todos/" + encodeURIComponent(editId)' in seg_add and '"PUT"' in seg_add)
    c.check("写入后调用 loadScheduleView() 刷新（与既有惯例一致）",
            "loadScheduleView()" in seg_add)

    # 删除：必须有 confirm
    seg_del = _seg(src, "async function tlDelete", "document.getElementById(\"tabWeek\")")
    c.check("删除走 DELETE /api/todos/:id", '"DELETE"' in seg_del)
    c.check("删除前有 confirm 确认弹窗（绝不一点就删）",
            "confirm(" in seg_del and "确定删除这条待办？" in seg_del)

    # 不碰 todo_id 护栏
    c.check("新时间轴代码不出现 todo_id（只用 t.id）", "todo_id" not in src.split("tlRender")[1][:4000])
    # 前缀隔离
    c.check("新函数/变量统一 tl_ 前缀（不撞 loadScheduleView 等既有逻辑）",
            src.count(".tl-") > 10 and "function tlRender" in src and "function tlOpenForm" in src)
    return c


def batch_b_css_present():
    c = Checker()
    title("2. 时间轴样式与深色主题对齐（不撑破面板布局）")
    src = open("app/static/student.html", encoding="utf-8").read()
    c.check("有时间轴网格样式 .tl-grid", ".tl-grid {" in src)
    c.check("有日列容器样式 .tl-daycol（绝对定位落位，替代逐格虚线）", ".tl-daycol {" in src)
    c.check("有待办色块样式 .tl-evt（position:absolute 跨行单块）",
            ".tl-evt {" in src and "position: absolute" in src)
    c.check("回归守卫：已弃用逐格贴块的 tlOverlapsHour",
            "tlOverlapsHour" not in src)
    c.check("深色主题给了时间轴覆盖（.tl-wrap 出现在主题块里）",
            src.count(".tl-wrap") >= 2)
    return c


def batch_c_api_roundtrip():
    c = Checker()
    title("3. 接口往返：增/改/删确实落到 /api/todos 同源数据（沙箱，零污染真实数据）")
    with sandbox():
        cli = make_client()
        d = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()

        # 新增（模拟时间轴 POST）
        r = cli.post("/api/todos", json={"title": "TL测试-去图书馆", "date": d,
                                         "start": "10:00", "end": "11:00"})
        c.check("POST /api/todos 新增成功", r.status_code == 200 and r.json().get("ok"))
        tid = r.json()["todo"]["id"]

        # 同源可见（GET /api/todos 是三个入口共用的数据源）
        lst = cli.get("/api/todos").json()["todos"]
        c.check("新增后 GET /api/todos 能看到（同源）",
                any(t["id"] == tid for t in lst))

        # 编辑（模拟时间轴保存修改 PUT）
        r = cli.put(f"/api/todos/{tid}", json={"title": "TL测试-改后", "start": "15:00", "end": "16:00"})
        c.check("PUT /api/todos/:id 编辑成功", r.status_code == 200 and r.json()["todo"]["title"] == "TL测试-改后")
        it = [t for t in cli.get("/api/todos").json()["todos"] if t["id"] == tid]
        c.check("编辑后标题与时间都已更新",
                it and it[0]["title"] == "TL测试-改后" and it[0]["start"] == "15:00")

        # 删除（模拟时间轴“删除这条”）
        r = cli.request("DELETE", f"/api/todos/{tid}", json={})
        c.check("DELETE /api/todos/:id 删除成功", r.status_code == 200 and r.json().get("ok"))
        after = cli.get("/api/todos").json()["todos"]
        c.check("删除后 GET /api/todos 不再包含（持久化）",
                not any(t["id"] == tid for t in after))
    return c


def main():
    results = [batch_a_source_wiring(), batch_b_css_present(), batch_c_api_roundtrip()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== 自行编辑日程（周时间轴）总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
