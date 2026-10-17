"""#36：演示台账带「归属人」—— 学生只看自己的件，驿站看全店但**按学号分组**。

用户 2026-10-17 19:34 原话：「最开始生成模拟的数据只有单个学生…**驿站如果查待取件，应该显示
1 同学没取的、2 同学的**，注意数据不能泄露，驿站不应该通过代办能读取学生信息」。

三段账分开算（这个文件就按三段钉）：
  A/B 段：**别人的件不许递给这个人**（学生端三个读入口 + 取件码那一档最容易漏）；
  C 段：驿站要看得见「该催谁」（全量 + 分组 + 店员口径的话）—— 这是它办件必需的可见范围；
  D/E 段：判据只有一份、演示底稿自己得说得通。

⚠️ 三条本仓规矩照办：
  ① 起点**自种**（`write_express` / `write_takeout`）—— `sandbox()` 是照抄真实台账，
     而这份底稿以后还会被人手改，认它的断言就是「两台机器两个结果」（红线 18）；
  ② 身份走 **cookie**（`_cli_as`），测的是走到的那条路（#32 那个中间件），不是自己改全局；
  ③ 负向断言看**输出**（那句学生腔是工具回话里念出来的），不是看代码里的字面量。
"""
import contextlib
import json
import re

from _harness import (Checker, make_client, sandbox, title,
                      write_express, write_takeout)

# 一份「两种口径都喂得到」的已知台账：
#   学号 1：两件未取（其中一件滞留）+ 一件已取
#   学号 2：两件未取（其中一件滞留）—— 它的码就是 A/B 段要的「别人的码」
#   demo  ：一件未取（没登录时的落点）
PACKAGES = [
    {"id": "X1", "owner": "1", "item": "样本甲", "station": "北区驿站",
     "pickup_code": "A-1001", "arrived_at": "2026-12-20", "status": "待取"},
    {"id": "X2", "owner": "1", "item": "样本乙", "station": "北区驿站",
     "pickup_code": "A-1002", "arrived_at": "2026-09-01", "status": "待取"},
    {"id": "X3", "owner": "1", "item": "样本丙", "station": "南区驿站",
     "pickup_code": "A-1003", "arrived_at": "2026-09-01", "status": "已取"},
    {"id": "X4", "owner": "2", "item": "别人的键盘", "station": "自提点",
     "pickup_code": "D-3344", "arrived_at": "2026-12-21", "status": "待取"},
    {"id": "X5", "owner": "2", "item": "别人的护肤品", "station": "顺丰",
     "pickup_code": "E-7788", "arrived_at": "2026-09-01", "status": "待取"},
    {"id": "X6", "owner": "demo", "item": "演示件", "station": "南区驿站",
     "pickup_code": "F-0001", "arrived_at": "2026-12-19", "status": "待取"},
]
ORDERS = [
    {"id": "Y1", "owner": "1", "shop": "甲店", "items": "套餐", "eta": "12:00",
     "status": "配送中", "pickup_point": "A 柜"},
    {"id": "Y2", "owner": "2", "shop": "乙店", "items": "柠檬水", "eta": "12:30",
     "status": "待取", "pickup_code": "B12", "pickup_point": "B 柜"},
    {"id": "Y3", "owner": "demo", "shop": "丙店", "items": "咖啡", "eta": "13:00",
     "status": "制作中", "pickup_point": "C 柜"},
]
MINE = ("A-1001", "A-1002", "A-1003")          # 学号 1 的三件（含已取）
OTHERS = ("D-3344", "E-7788")                  # 学号 2 的两件
DEMOS = ("F-0001",)                            # 无身份那份


def _cli_as(sid):
    """带某个学号 cookie 的客户端（身份的唯一载体是 cookie，#32）。"""
    return make_client(headers={"Cookie": f"ct_student={sid}"})


def _codes(pkgs):
    return [p.get("pickup_code") for p in pkgs]


@contextlib.contextmanager
def _as(sid):
    """函数级断言用的「当前是谁」：走 store 那一处 ContextVar，测完还原。

    ⚠️ 生产代码里唯一 set 点是 `main.bind_student` 那个中间件（`test_59` C1 钉着）；
       测试在**进程外**摆状态不违反它 —— 但必须还原，否则下一段断言顶着上一个学号跑。
    """
    from app import store
    prev = store.current_student()
    store.set_current_student(sid)
    try:
        yield
    finally:
        store.set_current_student(prev)


def batch_a_http_scopes():
    """A 段 · HTTP 三个口径（学生 1 / 学生 2 / 无身份）+ 返回形状不许变。"""
    c = Checker()
    title("A. /api/express 与 /api/takeout 按人分：1 看不见 2，没登录只看见 demo")
    with sandbox():
        write_express(PACKAGES)
        write_takeout(ORDERS)

        pk1 = _cli_as("1").get("/api/express").json().get("packages") or []
        c.check("A1 学号 1 拿到自己的 3 件（含已取那件），别人的件一条都没有",
                _codes(pk1) == list(MINE), f"实际 {len(pk1)} 件 {_codes(pk1)}")
        blob1 = json.dumps(pk1, ensure_ascii=False)
        c.check("A1-bis 整份回包里搜不到别人的取件码（连字段值都不许漏出去）",
                all(cd not in blob1 for cd in OTHERS), blob1[:60])

        pk2 = _cli_as("2").get("/api/express").json().get("packages") or []
        c.check("A2 反向钉：学号 2 只拿到自己那 2 件（防只按第一个学号写死）",
                _codes(pk2) == list(OTHERS), f"实际 {_codes(pk2)}")

        pkd = make_client().get("/api/express").json().get("packages") or []
        c.check("A3 没登录（测试 / 演示脚本 / 未带 cookie）只回 demo 那一份，**不回全量**",
                _codes(pkd) == list(DEMOS), f"实际 {_codes(pkd)}")

        o1 = _cli_as("1").get("/api/takeout").json().get("orders") or []
        od = make_client().get("/api/takeout").json().get("orders") or []
        c.check("A4 外卖台账同一道判据：学号 1 一单，无身份只有 demo 那一单",
                [o.get("id") for o in o1] == ["Y1"] and [o.get("id") for o in od] == ["Y3"],
                f"{[o.get('id') for o in o1]} / {[o.get('id') for o in od]}")

        first = (pk1 or [{}])[0]
        c.check("A5 返回形状没动（施工单 §六：还是 packages 那一层，字段齐，前端卡片不用跟着改）",
                all(k in first for k in ("id", "item", "station", "pickup_code",
                                         "arrived_at", "label")), str(sorted(first))[:70])
    return c


def batch_b_lookup_scopes():
    """B 段 · 函数级：取件码这一档最容易「顺手一查就读到别人」。"""
    c = Checker()
    title("B. 凭取件码查：学生查不到别人的，店员查得到而且看得见归属")
    with sandbox():
        write_express(PACKAGES)
        write_takeout(ORDERS)
        from app.modules import express, takeout

        with _as("1"):
            mine = express.list_my_packages()
            c.check("B1 学生口径的 list_my_packages 只念自己三件（别人的物品名一个字都不出）",
                    all(cd in mine for cd in MINE)
                    and "别人的键盘" not in mine and "别人的护肤品" not in mine,
                    mine.replace("\n", " ")[:60])
            c.check("B2 自己的码查得到", "样本甲" in express.get_by_code("A-1001"), "")
            miss = express.get_by_code("D-3344")
            c.check("B3 输别人的码要说「没找到」，不许把物品名念出来（取件码不是身份凭证）",
                    "没找到" in miss and "别人的键盘" not in miss, miss[:40])

        with _as("2"):
            c.check("B4 反向：学号 2 也查不到 1 的码 A-1002",
                    "没找到" in express.get_by_code("A-1002"), "")

        # 店员口径 = 驿站工具箱里绑好的那一个（station.py 用 partial 把 staff=True 固定住）
        from app.modules.station import build_tools
        got = build_tools()["get_by_code"].func(pickup_code="D-3344")
        c.check("B5 店员照码核对**别人名下**的件要查得到（快捷操作「查取件码」那颗就是这条路）",
                "别人的键盘" in got, got[:40].replace("\n", " "))
        c.check("B5-bis 而且回话里带一行归属 —— 店员得知道这是谁的件",
                "归属" in got and "2" in got, got.replace("\n", " ")[:60])

        with _as("1"):
            c.check("B6 外卖三个读入口同样按人（list_my_orders / list_active_orders / get_order）",
                    "柠檬水" not in takeout.list_my_orders()
                    and "没找到" in takeout.get_order("Y2")
                    and "乙店" not in takeout.list_active_orders(),
                    takeout.list_my_orders().replace("\n", " ")[:50])
        with _as("2"):
            c.check("B6-bis 反向：学号 2 看得见 Y2、看不见 Y1",
                    "柠檬水" in takeout.get_order("Y2") and "没找到" in takeout.get_order("Y1"), "")
    return c


def batch_c_staff_ledger():
    """C 段 · 驿站的分组台账：看得到全店、按学号分、说店员听得懂的话。"""
    c = Checker()
    title("C. 店员口径：全量 + 按学号分组 + 不许学生腔")
    with sandbox():
        write_express(PACKAGES)
        from app.modules import express

        out = express.list_pending_by_student()
        lines = out.split("\n")
        heads = [ln for ln in lines if ln.startswith("·")]
        c.check("C1 两组都在且各自带条数（1 同学 2 件未取 / 2 同学 2 件）",
                "· 1 同学｜2 件" in out and "· 2 同学｜2 件" in out, str(heads)[:80])
        c.check("C2 demo 那份不隐身，也不许念成 demo 同学那种怪话",
                any("demo（无身份演示件）" in ln for ln in heads), str(heads)[:60])
        c.check("C3 顺序稳定：按学号排序，同一个台账两次跑一模一样",
                out == express.list_pending_by_student() and heads == sorted(heads),
                " / ".join(heads)[:70])
        c.check("C4 已取的那件不许进这份台账", "A-1003" not in out, "")
        c.check("C5 滞留件带着滞留天数（店员靠这个决定先催谁）",
                re.search(r"滞留 \d+ 天", out) is not None,
                next((ln for ln in lines if "滞留" in ln), "")[:60])
        student_voice = ("你有", "你共有", "请尽快取", "你去取", "抽空去", "建议你", "避免退回")
        hit = [w for w in student_voice if w in out]
        c.check("C6 输出里不许出现学生腔（截图那句「建议这两天抽空去北区驿站一趟」就是这一族）",
                not hit, f"出现 {hit}")
        write_express([dict(p, status="已取") for p in PACKAGES])
        empty = express.list_pending_by_student()
        c.check("C7 全取完时说一句实话，不崩、也不硬凑一句提醒",
                "0 件" in empty or "没有待取" in empty, empty[:50])
    return c


def _code_only(src):
    """剔整行注释再比（负向断言不剔就会把说明书当代码判红，本仓反复踩过）。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))


def batch_d_shape():
    """D 段 · 形状：判据一处、路径每次现读、工具箱各归各位。"""
    c = Checker()
    title("D. 形状：一条判据、两处口径、没有第二份过滤")
    st = _code_only(open("app/store.py", encoding="utf-8").read())
    ex = _code_only(open("app/modules/express.py", encoding="utf-8").read())
    tk = _code_only(open("app/modules/takeout.py", encoding="utf-8").read())
    sta = _code_only(open("app/modules/station.py", encoding="utf-8").read())
    sta_raw = open("app/modules/station.py", encoding="utf-8").read()
    mn = open("app/main.py", encoding="utf-8").read()

    c.check("D1 归属判据各只定义一处，且都在 store（express / takeout 不许自己再写一份）",
            st.count("def owner_of_row(") == 1 and st.count("def current_owner(") == 1
            and st.count("def visible_rows(") == 1
            and "def visible_rows(" not in ex and "def visible_rows(" not in tk, "")
    c.check("D2 别处不许手写第二遍过滤：两个模块里都不出现 owner 相等那种自拼判据",
            'get("owner") ==' not in ex and 'get("owner") ==' not in tk
            and "owner_of_row" not in ex.replace("store.owner_of_row", ""),
            "只许调 store.visible_rows")
    n_filter = ex.count("store.visible_rows(") + tk.count("store.visible_rows(")
    c.check("D2-bis 两个模块的读入口全走 visible_rows（数一下有几处，少一处就是漏了一个口）",
            n_filter >= 7, f"{n_filter} 处")
    c.check("D3 台账路径不再在导入时拼死：两个模块都不许出现 dirname(__file__) 拼 data 那一行",
            "os.path.join(os.path.dirname(__file__)" not in ex
            and "os.path.join(os.path.dirname(__file__)" not in tk
            and "store.read_data_file(" in ex and "store.read_data_file(" in tk, "")
    tools_seg = ex.split("def build_tools")[-1]
    c.check("D4 店员那份分组台账只挂驿站：学生工具箱里不许有 list_pending_by_student",
            "list_pending_by_student" not in tools_seg and "list_pending_by_student" in sta, "")
    c.check("D5 学生那份「只看还没取的」没被顺手删掉（它是学生端的功能，两码事）",
            "list_pending_pickup" in ex, "")
    c.check("D6 驿站工具箱仍然只有两个查询、一个写入都没有（#34 同一条红线）",
            sta.count("Tool(") == 2 and not re.search(r"func=\w*(add|save|delete|write|set_)\w*", sta),
            f"Tool( {sta.count('Tool(')} 个")
    c.check("D7 店员口径写在 station 那一处（partial），数据层不认调用方（express 里不许判 module_key）",
            "partial(get_by_code, staff=True)" in sta and "module_key" not in ex, "")
    c.check("D8 提示词里有「驿站店员」那条受众说明（话术不是防线，缺了它还会说学生腔）",
            "驿站店员" in sta_raw and "学生腔" in sta_raw, "")
    c.check("D9 #34 那道端级闸门还在（本单不许把 staff 口径扩成「什么都能看」）",
            mn.count('_station_surface = module_key == "station"') == 1
            and "if not _station_surface:" in mn, "")
    return c


def batch_e_demo_data():
    """E 段 · 演示底稿自己得说得通（**只钉结构，不钉具体学号** —— 那两个名字用户还会改）。"""
    c = Checker()
    title("E. 演示台账（tracked 底稿）：每条有归属、码不重、至少两位各有未取件")
    for fname, key in (("app/data/express.json", "packages"), ("app/data/takeout.json", "orders")):
        rows = json.load(open(fname, encoding="utf-8")).get(key) or []
        c.check(f"E1 {fname}：每条都有非空 owner（没有归属就退化成一张全局表）",
                bool(rows) and all(str(r.get("owner") or "").strip() for r in rows),
                f"{len(rows)} 条")
        codes = [r.get("pickup_code") for r in rows if r.get("pickup_code")]
        c.check(f"E2 {fname}：取件码 / 取餐码互不重复（演示时码对应谁必须唯一）",
                len(codes) == len(set(codes)), str(codes))
        owners = {str(r.get("owner")) for r in rows if r.get("status") not in ("已取", "已完成")}
        c.check(f"E3 {fname}：至少两位同学各有未取的件（驿站分组演示才看得出来）",
                len(owners) >= 2, f"未取的归属 {sorted(owners)}")
    return c


def main():
    print("\n" + "=" * 60)
    print("test_62_express_owner：演示台账按人分 + 驿站按学号分组（#36）")
    print("=" * 60)
    code = 0
    code |= batch_a_http_scopes().summary("A 段（HTTP 三个口径 + 形状没动）")
    code |= batch_b_lookup_scopes().summary("B 段（凭码查：查不到别人的 / 店员查得到）")
    code |= batch_c_staff_ledger().summary("C 段（店员分组台账 + 不许学生腔）")
    code |= batch_d_shape().summary("D 段（形状：一条判据、路径现读、工具箱各归各位）")
    code |= batch_e_demo_data().summary("E 段（演示底稿的结构性质）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
