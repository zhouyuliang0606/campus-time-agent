"""管理接口那道门（test_32）：口令挡住了谁、又放行了谁。

**守的是什么**：2026-10-08 核查 P0-2 时发现，全项目**没有任何鉴权**——
`/api/admin/settings`（改模型密钥）、`/api/admin/usage`、`/api/admin/messages`（读走
全部学生消息）、`POST /api/persona`（改客服人设）这些接口，任何能连到端口的人
直接就能读、能写。这轮加了一道门：环境变量 `CAMPUSTIME_ADMIN_TOKEN`，
请求头 `X-Admin-Token`，由 `app/admin_guard.py` 校验，挂在管理侧接口上
（立案时是 14 条；涨到几条以 E2 那张**逐条承认**的清单为准，这里不数数 —— 数数迟早自己撞自己）。

**但门最容易犯的错不是没装，是装过了**：`POST /api/workorders` 是学生点
【确认上报】用的，`GET /api/persona` 是驿站页显示客服名字用的，
`/api/todos`、`/api/chat` 是学生自己的日程和对话。这些一旦挂上门，
学生侧整条线当场废掉，而管理台演示时又只看得到「后台还能用」。
所以这份护栏一半的项数在验「谁没被挡住」，跟验「谁被挡住了」同样重要。

⚠️ 没配口令时是**锁死**（403）而不是放行，专门有一批（C）钉这件事：
「没配就敞开」是新克隆的人最容易踩到的默认值——照着 README 启动，
那个状态恰好就是「没配」。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import Checker, PROJ_DIR, sandbox, title  # noqa: E402

SERVER_TOKEN = "s3cret-admin-token"

# 挂门的管理侧接口（逐条列在下面 ADMIN_ENDPOINTS；有几条以 E2 那张清单为准，别在这儿数）
ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/messages", None),
    ("GET", "/api/admin/kb", None),
    ("POST", "/api/admin/kb", {"question": "门测的问题", "answer": "门测的答案", "keywords": "门"}),
    ("PUT", "/api/admin/kb/0", {"question": "改一下", "answer": "再改一下", "keywords": ""}),
    ("DELETE", "/api/admin/kb/0", None),
    ("POST", "/api/admin/notify", {"title": "门测通知"}),
    ("POST", "/api/persona", {"name": "小园", "role": "测试", "tone": "平", "rules": ""}),
    ("GET", "/api/admin/settings", None),
    ("POST", "/api/admin/settings", {"api_key": "sk-test-gate", "model": "deepseek-chat"}),
    ("POST", "/api/admin/settings/test", None),
    ("GET", "/api/admin/usage", None),
    ("POST", "/api/admin/usage/clear", None),
    ("GET", "/api/workorders", None),
    ("POST", "/api/workorders/没有这条/status", {"status": "已解决"}),
    # 2026-10-18 #40：文档知识库那三条（列表 / 删除 / 重建索引）。删除与重切都指向一条
    # 不存在的 id —— 这一批只问"门挡不挡"，业务上回 400 正是我们要的（参数不对不等于门没挂）。
    ("GET", "/api/admin/docs", None),
    ("DELETE", "/api/admin/docs/nosuchdoc", None),
    ("POST", "/api/admin/docs/nosuchdoc/reindex", None),
    # 2026-10-22 #54：后勤那一档四条（台账 / 现场登记 / 改状态 / 建服务）。
    ("GET", "/api/admin/service-records", None),
    ("POST", "/api/admin/service-records/nosvc", {"fields": {"楼栋": "3"}}),
    ("POST", "/api/admin/service-records/nosuch/norec/status", {"status": "已解决"}),
    ("POST", "/api/admin/services", {"description": "门测：加一项服务"}),
]
# 其中这几个「带对口令后」不去实跑：/settings/test 真会往大模型发一次请求，
# 用一个假 key 打真网络既慢又不稳定，门该验的只是「没口令进不去」。
# #54 的【建服务】同性质 —— 它内部就走 `_draft_service` → 真打一次模型（而且开发机上
# .env 里是有真密钥的，实跑就会往沙箱里建出一项服务，那不是这道门要问的事）。
SKIP_WITH_TOKEN = {"/api/admin/settings/test", "/api/admin/services"}

# 学生侧：这些**不许**被口令挡住
STUDENT_OPEN = [
    ("GET", "/api/health", None),
    ("GET", "/api/persona", None),                     # 驿站页要显示客服名字
    ("GET", "/api/notices", None),
    ("GET", "/api/todos", None),
    ("GET", "/api/timetable", None),
    ("GET", "/api/uploads", None),
    ("POST", "/api/student-message", {"module": "faq", "message": "门测的话", "answer": "嗯"}),
    ("POST", "/api/workorders", {"kind": "快递丢了", "desc": "门测：学生上报的工单", "source": "panel"}),
]


def _client(server_token, call_token):
    """分开控制「服务器上配没配口令」和「这次请求带没带口令」——
    这四种组合里，(没配, 带了) 和 (配了, 没带) 是两条完全不同的失败路径。"""
    from fastapi.testclient import TestClient
    from app.main import app

    if server_token:
        os.environ["CAMPUSTIME_ADMIN_TOKEN"] = server_token
    else:
        os.environ.pop("CAMPUSTIME_ADMIN_TOKEN", None)
    headers = {"X-Admin-Token": call_token} if call_token is not None else {}
    return TestClient(app, headers=headers)


def _hit(client, method, path, body):
    return client.request(method, path, **({"json": body} if body is not None else {}))


def batch_admin_locked(c):
    title("A. 配了口令、但请求没带 → 管理侧一个都进不去")
    cl = _client(SERVER_TOKEN, None)
    blocked, leaked = [], []
    for method, path, body in ADMIN_ENDPOINTS:
        r = _hit(cl, method, path, body)
        if r.status_code in (401, 403):
            blocked.append(f"{method} {path}")
        else:
            leaked.append(f"{method} {path}→{r.status_code}")
    c.check(f"A1 管理侧 {len(ADMIN_ENDPOINTS)} 个接口全部被挡（401）",
            not leaked and len(blocked) == len(ADMIN_ENDPOINTS), f"漏网：{leaked}")
    r = _hit(cl, "GET", "/api/admin/messages", None)
    c.check("A2 挡的时候回的是人话，不是一个空 401", "口令" in str(r.json()), str(r.json())[:70])
    c.check("A3 错口令跟没口令一样被挡（不告诉人「差一点就对了」）",
            _client(SERVER_TOKEN, "wrong-token").get("/api/admin/kb").status_code == 401)

    title("A4. 带对口令 → 管理侧照常能用（门把自己人也锁死那是回归）")
    ok = _client(SERVER_TOKEN, SERVER_TOKEN)
    fails = []
    for method, path, body in ADMIN_ENDPOINTS:
        if path in SKIP_WITH_TOKEN:
            continue
        r = _hit(ok, method, path, body)
        # 业务上可能因为参数不对给 400/404（空表时删 0 号、改一条不存在的工单），
        # 那不算被门挡住：这道门只管 401/403 这两种结果。
        if r.status_code in (401, 403):
            fails.append(f"{method} {path}→{r.status_code}")
    c.check("带对口令后，没有一个管理接口还被门挡着", not fails, f"{fails}")


def batch_student_open(c):
    title("B. 学生侧：没口令也必须走得通（挂错了门比没门更糟）")
    cl = _client(None, None)   # 连服务器口令都不配，模拟「评委什么都没配就启动」
    stuck = []
    for method, path, body in STUDENT_OPEN:
        r = _hit(cl, method, path, body)
        if r.status_code in (401, 403):
            stuck.append(f"{method} {path}→{r.status_code}")
    c.check(f"B1 {len(STUDENT_OPEN)} 个学生接口在**完全没配口令**的服务器上全部照常",
            not stuck, f"被误挡：{stuck}")
    # 工单是「学生上报、管理员处理」两段式：上报开放、处理要口令
    r_post = _hit(cl, "POST", "/api/workorders",
                  {"kind": "快递丢了", "desc": "第二段：拿 id", "source": "panel"})
    wo_id = (r_post.json().get("workorder") or {}).get("id")
    if c.check("B2 学生上报工单真落进去了（拿到了 id）", bool(wo_id), f"HTTP {r_post.status_code}"):
        r_status = _hit(cl, "POST", f"/api/workorders/{wo_id}/status", {"status": "已解决"})
        c.check("B3 但**处理**工单要口令（同一条工单，学生能报、不能自己标已解决）",
                r_status.status_code in (401, 403), f"HTTP {r_status.status_code}")
        r_ok = _hit(_client(SERVER_TOKEN, SERVER_TOKEN), "POST",
                    f"/api/workorders/{wo_id}/status", {"status": "已解决"})
        c.check("B4 带口令的管理员改得动（回执确实是 已解决）",
                r_ok.status_code == 200
                and r_ok.json().get("workorder", {}).get("status") == "已解决",
                f"HTTP {r_ok.status_code}")


def batch_fail_closed(c):
    title("C. 服务器没配口令 → 管理接口锁死，不是当没这回事放行")
    cl = _client(None, None)
    r = _hit(cl, "GET", "/api/admin/kb", None)
    c.check("C1 没配口令时，请求**带了随便一串**也进不去",
            r.status_code == 403, f"HTTP {r.status_code}")
    c.check("C2 回话里直接告诉人去配哪个变量（别让人对着 403 猜）",
            "CAMPUSTIME_ADMIN_TOKEN" in str(r.json()), str(r.json())[:80])
    c.check("C3 服务器没配、请求带了口令 → 仍是 403（不是「口令对不上」那种蒙混）",
            _hit(_client(None, "whatever"), "GET", "/api/admin/usage", None).status_code == 403)
    c.check("C4 而学生侧在这种什么都没配的环境里完全不受影响",
            _hit(cl, "GET", "/api/todos", None).status_code == 200)


def batch_write_really_blocked(c):
    title("D. 门是拦写入的，不是只改个状态码（改前改后各看一眼数据）")
    from app.store import get_persona, list_kb
    locked = _client(SERVER_TOKEN, None)
    kb_before = len(list_kb())
    name_before = get_persona().get("name")
    _hit(locked, "POST", "/api/admin/kb", {"question": "被挡的条目", "answer": "X", "keywords": ""})
    _hit(locked, "POST", "/api/persona", {"name": "被改走的人设", "role": "r", "tone": "t", "rules": ""})
    c.check("D1 没口令想加知识库条目 → 条数一点没变", len(list_kb()) == kb_before,
            f"{kb_before} → {len(list_kb())}")
    c.check("D2 没口令想改客服人设 → 存的还是原来那套",
            get_persona().get("name") == name_before, str(get_persona().get("name")))

    gated = _client(SERVER_TOKEN, SERVER_TOKEN)
    _hit(gated, "POST", "/api/admin/kb", {"question": "带口令加的条目", "answer": "Y",
                                          "keywords": "门"})
    c.check("D3 带对口令就真加进去了（同一条请求，唯一差别是那个头）",
            len(list_kb()) == kb_before + 1, f"{kb_before} → {len(list_kb())}")


def _guarded_routes():
    """直接从 FastAPI 的路由表里问"哪些接口挂了这道门"。

    为什么不去数源码里 `dependencies=[Depends(verify_admin)]` 出现几次：
    第一版就是那么写的，结果数出 15 个——多出来的那一次是 main.py 里
    一段**解释这个字符串**的注释。数注释干什么呢。
    路由表是运行时的真相，挂没挂在它身上问它自己。
    """
    from app.admin_guard import verify_admin
    from app.main import app

    out = set()
    for r in app.routes:
        deps = getattr(getattr(r, "dependant", None), "dependencies", None) or []
        if any(getattr(d, "call", None) is verify_admin for d in deps):
            out |= {f"{m} {r.path}" for m in (getattr(r, "methods", None) or set()) if m != "HEAD"}
    return out


def batch_wiring(c):
    title("E. 接线与写法：门挂在哪儿、比对用的什么写法")
    guard = open(os.path.join(PROJ_DIR, "app", "admin_guard.py"), encoding="utf-8").read()
    mn = open(os.path.join(PROJ_DIR, "app", "main.py"), encoding="utf-8").read()
    c.check("E1 口令比对用 hmac.compare_digest，不是 ==（== 会被计时差一位位试出来）",
            "hmac.compare_digest(" in guard and "if given == expected" not in guard)

    expect = {
        "GET /api/admin/messages", "GET /api/workorders", "POST /api/workorders/{wo_id}/status",
        "POST /api/admin/notify", "GET /api/admin/kb", "POST /api/admin/kb",
        "PUT /api/admin/kb/{index}", "DELETE /api/admin/kb/{index}", "POST /api/persona",
        "GET /api/admin/settings", "POST /api/admin/settings", "POST /api/admin/settings/test",
        "GET /api/admin/usage", "POST /api/admin/usage/clear",
        # #29 归档：两张台账的"收起来"都只挂管理端。这条清单是**逐条承认**用的，
        # 新增一个挂门口必须在这儿留名（E2 就是为了让"顺手加个口忘了挂门/挂错面"当场红）。
        "POST /api/workorders/{wo_id}/archive", "POST /api/admin/messages/{mid}/archive",
        # 2026-10-18 #39-D：撤回通知是管理动作 ⇒ 也挂门（清单从 16 涨到 17 是**承认过的**，
        # 这条清单存在的理由就是"新增一个挂门口必须在这儿留名"，所以涨不是坏消息，悄悄涨才是）。
        "DELETE /api/admin/notices/{nid}",
        # 2026-10-18 #40：文档知识库三条（列表/删除/重建索引）⇒ 17 涨到 20，同样是承认过的。
        # 其中【删除】是本单唯一一条会把用户上传的文件**真删**的口，漏挂等于任何人都能清知识库。
        "GET /api/admin/docs", "DELETE /api/admin/docs/{fid}", "POST /api/admin/docs/{fid}/reindex",
        # 2026-10-20 #46-C：试检索探针。它是**只读**的（护栏 test_73 C9 拿整树指纹钉），
        # 但只读不等于免口令 —— 它回的是"这句学生问得到什么答案"，等于把知识库内容往外递一份，
        # 所以照样挂门。20 → 21 是**承认过的**涨项。
        "POST /api/admin/kb/probe",
        # 2026-10-22 #54：后勤那一档四条（台账 / 现场登记 / 改状态 / 建服务）⇒ 21 涨到 25，同样承认过。
        # 其中【现场登记】是施工单没列、但管理端界面那张登记表单**必须**有的写口
        # （驿站那个写口现在按部门拦，后勤的服务从那儿进直接 403）。
        # 【台账】虽然只读，但它把**全校**的登记内容往外递，与 kb/probe 同一条理由：只读不等于免口令。
        # ⚠️ 这四条必须落在 `/api/admin/*` 而不是驿站侧接口那一段：test_58 D 批钉着
        #    "驿站那一段（免口令面）里不许出现 `dependencies=[Depends(verify_admin)]`"，
        #    插错位置就是**把这道门的边界自己挪松了**。
        "GET /api/admin/service-records",
        "POST /api/admin/service-records/{sid}",
        "POST /api/admin/service-records/{sid}/{rid}/status",
        "POST /api/admin/services",
    }
    got = _guarded_routes()
    # 名字里的数字改成从清单自己算：以前写死"这 20 个"，加一条就得改两处（计数式判据自己咬自己那一族）
    c.check(f"E2 挂门的接口正好是这份清单里的 {len(expect)} 个（多一个少一个都算错 —— "
            "新加管理口必须在这儿留名）", got == expect,
            f"多挂：{sorted(got - expect)}｜漏挂：{sorted(expect - got)}")
    # #29 的另一半：归档**写口**只许在管理端。驿站端 `/api/station/*` 是免口令的（演示级），
    # 免口令还能收台账 = 任何人不用口令就能清工单，所以这条反向钉死。
    # 2026-10-18 #39-C 把判据从"段里不许出现 archive 这个词"换成"不许出现**写**口"：
    # 驿站多了一条**只读**的已处理历史（GET），词一样、性质不同 —— 数据没跨过端，
    # 只是标成已解决被收起之后，处理它的人不再看不见"那条去哪了"。
    # 收窄的同时补了变异 M10（把那条 GET 改成 POST 必须红），见 test_65 与指南红线 30。
    station_seg = mn[mn.index("# ============ 驿站侧接口"):mn.index('@app.post("/api/admin/notify"')] \
        if "# ============ 驿站侧接口" in mn else ""
    _station_deco = [ln.strip()[:60] for ln in station_seg.splitlines() if ln.startswith("@app.")]
    _station_writes = [ln for ln in _station_deco if "archive" in ln
                       and not ln.startswith('@app.get(')]
    c.check("E2b 驿站端那一段里**没有归档的写口**（免口令的面不许扩大到改台账；只读历史允许且只许 GET）",
            station_seg and not _station_writes
            and "set_workorder_archived" not in station_seg
            and 'dependencies=[Depends(verify_admin)]' not in station_seg
            and '@app.get("/api/station/workorders/archived")' in station_seg,
            f"station 段 {len(_station_deco)} 条路由，其中归档写口 {len(_station_writes)} 条：{_station_writes[:2]}")
    c.check("E3 学生上报工单那条没挂门（用户点名要保留开放）",
            "POST /api/workorders" not in got)
    c.check("E4 处理工单那条挂了（同一段里这两行最容易看走眼）",
            "POST /api/workorders/{wo_id}/status" in got)
    c.check("E5 学生侧写接口（日程 / 课表 / 聊天 / 上传）一个都没被挂上门",
            not ({("POST /api/todos"), ("PUT /api/todos/{tid}"), ("DELETE /api/todos/{tid}"),
                  ("POST /api/timetable/apply"), ("POST /api/timetable/clear"),
                  ("POST /api/chat"), ("POST /api/upload"), ("GET /api/persona")} & got),
            f"误挡：{sorted({('POST /api/todos'), ('POST /api/chat'), ('GET /api/persona')} & got)}")
    c.check("E6 代码里没有写死的口令（只在 .env / 环境变量里）",
            "CAMPUSTIME_ADMIN_TOKEN=" not in mn and "CAMPUSTIME_ADMIN_TOKEN=" not in guard
            and "s3cret" not in guard)
    adm = open(os.path.join(PROJ_DIR, "app", "static", "admin.html"), encoding="utf-8").read()
    c.check("E7 前端只有一份带口令的封装（adminFetch），管理接口都从它走",
            adm.count("async function adminFetch(") == 1
            and 'fetch("/api/admin/' not in adm.replace('adminFetch("/api/admin/', ""),
            f"adminFetch 出现 {adm.count('adminFetch(')} 次")
    c.check("E8 学生那几个页面没被顺手改（station 仍裸 fetch GET /api/persona）",
            'fetch("/api/persona")' in open(os.path.join(PROJ_DIR, "app", "static", "station.html"),
                                            encoding="utf-8").read())
    c.check("E9 口令只进 sessionStorage，不写 localStorage（关标签即清、不落盘）",
            "sessionStorage.setItem(TOKEN_KEY" in adm
            and "localStorage.setItem(TOKEN_KEY" not in adm)
    ex = open(os.path.join(PROJ_DIR, ".env.example"), encoding="utf-8").read()
    c.check("E10 .env.example 里留了这一项（新克隆的人照着填就知道要配）",
            "CAMPUSTIME_ADMIN_TOKEN" in ex)


def main():
    print("===== 管理接口口令门（test_32） =====")
    c = Checker()
    # 全程跑在沙箱里：D 批会真加一条知识库条目、A4 批会真改人设，都只能写在临时副本上
    with sandbox():
        batch_admin_locked(c)
        batch_student_open(c)
        batch_fail_closed(c)
        batch_write_really_blocked(c)
        batch_wiring(c)
    os.environ.pop("CAMPUSTIME_ADMIN_TOKEN", None)
    return c.summary("管理接口口令门 ")


if __name__ == "__main__":
    raise SystemExit(main())
