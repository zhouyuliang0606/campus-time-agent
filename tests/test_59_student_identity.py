"""#32：学生身份 = 学号（演示级、不设密码），个人库**按学号分目录**，同一台机器换账号不共用。

用户 2026-10-17 原话：「学生账号不要两个机器，同一个机器退出登录后，切另一个账号，
课表代办属于个人数据库不能共用」。

四条规格，每条都有对应的批：
  A 批 —— 三个面（课表 / 待办 / 对话记忆）**真的**分开了（不是"看起来分开"）；
  B 批 —— 兜底（没学号走老位置）不许被删，白名单不许漏（学号会被拼进文件路径！）；
  C 批 —— 形状：set 身份只许一处、拼路径只许一处、cookie 的契约、文档不许隐瞒。

⚠️ 一句话性质：**这不是鉴权**。学号谁都能填，它只解决数据分区；
   真鉴权只有管理端口令那一条链（test_32 钉着，本文件不碰它）。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0


def _read_json(p, default):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def _todos_of(who=""):
    """读某个学号的待办；who 为空 = 老位置（演示公共库）。"""
    parts = ["student", "todos.json"] if not who else ["student", who, "todos.json"]
    return (_read_json(data_path(*parts), {"todos": []}) or {}).get("todos") or []


def _tt_of(who=""):
    parts = ["student", "timetable.json"] if not who else ["student", who, "timetable.json"]
    return (_read_json(data_path(*parts), {"courses": []}) or {}).get("courses") or []


def _sessions_keys_of(who=""):
    parts = ["student", "sessions.json"] if not who else ["student", who, "sessions.json"]
    d = _read_json(data_path(*parts), {"sessions": {}}) or {}
    return list((d.get("sessions") or {}).keys())


def _cli_as(sid):
    """带某个学号 cookie 的客户端（cookie 是身份的唯一载体；管理口令仍用 harness 默认那把）。"""
    return make_client(headers={"Cookie": f"ct_student={sid}"})


def batch_a_separated():
    c = Checker()
    title("A. 三个面都按人分：待办 / 周表 / 对话记忆")
    with sandbox():
        # ⚠️ 起点自己种老位置那份"底稿"：干净克隆里 `student/` 是**空的**（演示数据不入库），
        #    而首次登录播种拷的正是它 —— 不种就会出现"本机全绿、评委那台红"的那种假绿
        #    （A0/A2-bis 第一版在严格克隆里就是这么红的：登录 200，但什么都没拷出来）。
        base_todo = len(_todos_of(""))
        seed = make_client()
        seed.post("/api/todos", json={"title": "底稿待办（播种源）", "date": "2026-12-31",
                                       "start": "09:00", "end": "10:00"})
        seed.post("/api/timetable/apply", json={"courses": [
            {"day": 3, "start": "10:00", "end": "11:40", "course": "线性代数", "location": "教一"}]})
        # ⚠️ 比的是 **delta**，不是绝对条数：老位置在开发机上可能带着用户的演示待办、在干净克隆里是空的，
        #    写死 `== 1` 就是"本机绿、评委红"（4o3 那条同一个病）。周表可以写死 1 门 —— apply 是整份覆盖。
        c.check("A0-pre 播种源（老位置）拿到了内容：待办比基线多 1 条、周表整份是我种的这一门",
                len(_todos_of("")) == base_todo + 1 and len(_tt_of("")) == 1,
                f"待办 {base_todo}->{len(_todos_of(''))} 周表 {len(_tt_of(''))} 门")
        a = _cli_as("S001")
        b = _cli_as("S002")
        la = a.post("/api/login", json={"student": "S001"})
        lb = b.post("/api/login", json={"student": "S002"})
        c.check("A0 两个学号各自登录成功，并各有一份**自己目录里的**待办文件",
                la.status_code == 200 and lb.status_code == 200
                and os.path.exists(data_path("student", "S001", "todos.json"))
                and os.path.exists(data_path("student", "S002", "todos.json")),
                f"{la.status_code}/{lb.status_code}")

        # —— ① 待办：各写各的，互相看不见 ——
        n_a0, n_b0 = len(_todos_of("S001")), len(_todos_of("S002"))
        ra = a.post("/api/todos", json={"title": "只有 S001 的事", "date": "2026-12-31",
                                        "start": "15:00", "end": "16:00"})
        rb = b.post("/api/todos", json={"title": "只有 S002 的事", "date": "2026-12-31",
                                        "start": "17:00", "end": "18:00"})
        ta = [t.get("title") for t in _todos_of("S001")]
        t_b = [t.get("title") for t in _todos_of("S002")]
        c.check("A1 S001 写的那条只进 S001 的库、S002 写的那条只进 S002 的库（各 +1，不是同一条路）",
                ra.status_code == 200 and rb.status_code == 200
                and len(ta) == n_a0 + 1 and len(t_b) == n_b0 + 1
                and "只有 S001 的事" in ta and "只有 S001 的事" not in t_b
                and "只有 S002 的事" in t_b and "只有 S002 的事" not in ta,
                f"A={ta} B={t_b}")
        listed = [t.get("title") for t in a.get("/api/todos").json().get("todos") or []]
        c.check("A1-bis 接口读出来也只有自己那条（换人不是靠前端藏）",
                "只有 S001 的事" in listed and "只有 S002 的事" not in listed, str(listed)[:90])

        # —— ② 周表 ——
        # ⚠️ 不能拿"里面有没有某一门课"当判据：S002 的库是登录时从演示底稿**种**出来的，
        #    那 18 门课本就包含《高等数学》。要钉的是"S001 存课之后，S002 那份**一个字节没变**"。
        tt_b_before = open(data_path("student", "S002", "timetable.json"), encoding="utf-8").read() \
            if os.path.exists(data_path("student", "S002", "timetable.json")) else ""
        rtt = a.post("/api/timetable/apply", json={"courses": [
            {"day": 1, "start": "08:00", "end": "09:40", "course": "高等数学", "location": "教三"}]})
        c.check("A2 S001 存课**成功**（先证明这一步真走到了，再谈分没分开）",
                rtt.status_code == 200 and len(_tt_of("S001")) == 1,
                f"{rtt.status_code} {rtt.text[:70]}")
        tt_b_after = open(data_path("student", "S002", "timetable.json"), encoding="utf-8").read() \
            if os.path.exists(data_path("student", "S002", "timetable.json")) else ""
        c.check("A2-bis S001 存课之后，S002 那份周表**一个字节没变**（分的是文件，不是同一份里的两个 key）",
                tt_b_after == tt_b_before and bool(tt_b_before),
                f"S002 前后 {len(tt_b_before)}→{len(tt_b_after)} 字节")

        # —— ③ 对话记忆：两边用**同名** session id，也各落各的文件 ——
        sid = "shared-sess-1"
        a.post("/api/todos", json={"session_id": sid, "title": "带会话的一条",
                                   "date": "2026-12-31", "start": "19:00", "end": "20:00"})
        seen_a = a.get(f"/api/conversation/{sid}").json().get("messages") or []
        seen_b = b.get(f"/api/conversation/{sid}").json().get("messages") or []
        c.check("A3 同一个 session_id，A 读得到历史、B 读到空（对话记忆不共用）",
                len(seen_a) > 0 and len(seen_b) == 0, f"A={len(seen_a)} 条 B={len(seen_b)} 条")
        c.check("A3-bis 两份历史落在**两个不同文件**里（不是同一份 sessions.json 里的两个 key）",
                sid in _sessions_keys_of("S001") and sid not in _sessions_keys_of("S002"),
                f"A={_sessions_keys_of('S001')} B={_sessions_keys_of('S002')}")

        # —— ④ 重登不覆盖 ——
        before = json.dumps(_todos_of("S001"), ensure_ascii=False)
        c.check("A4 同一个学号再登录一次，库里那些条**一条没少**（重登把人家数据盖掉 = 事故）",
                a.post("/api/login", json={"student": "S001"}).status_code == 200
                and json.dumps(_todos_of("S001"), ensure_ascii=False) == before, "")

        c.check("A5 两个客户端交替请求，各自看到的都是自己的学号（ContextVar 没被并发盖掉）",
                a.get("/api/whoami").json().get("student") == "S001"
                and b.get("/api/whoami").json().get("student") == "S002"
                and a.get("/api/whoami").json().get("student") == "S001", "")

        # —— ⑤ 退出 = 只摘标签，不动数据 ——
        keep = len(_todos_of("S001"))
        out = a.post("/api/logout")
        anon = make_client()
        n_guest0 = len(_todos_of(""))
        w = anon.post("/api/todos", json={"title": "退出后写的一条", "date": "2026-12-31",
                                          "start": "20:00", "end": "21:00"})
        c.check("A6 退出只摘标签：之后的写入落回老位置，而 S001 那份**一条没少**（谁都不许清空数据）",
                out.json().get("ok") is True and w.status_code == 200
                and len(_todos_of("")) == n_guest0 + 1 and len(_todos_of("S001")) == keep,
                f"S001 {keep}->{len(_todos_of('S001'))}，老位置 {n_guest0}->{len(_todos_of(''))}")
    return c
def batch_b_fallback_and_whitelist():
    c = Checker()
    title("B. 兜底不破 + 学号白名单（它会被拼进文件路径）")
    with sandbox():
        anon = make_client()
        n0 = len(_todos_of(""))
        r = anon.post("/api/todos", json={"title": "没身份的一条", "date": "2026-12-31",
                                          "start": "09:00", "end": "10:00"})
        c.check("B1 **不带 cookie 仍读写老位置 student/**（这条一红就是 50 多个测试文件要陪葬）",
                r.status_code == 200 and len(_todos_of("")) == n0 + 1,
                f"{n0}->{len(_todos_of(''))}")
        c.check("B1-bis 没身份时 /api/whoami 回空串，前端据此标「未登录 · 演示公共库」",
                anon.get("/api/whoami").json().get("student") == "", "")

        root = data_path("student")
        dirs_before = {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))} \
            if os.path.isdir(root) else set()
        bad_ids = ["../../etc/passwd", "a/b", "a\\b", "", "  ", "S" * 21, "../S001", "带中文", "S 001"]
        for raw in bad_ids:
            resp = anon.post("/api/login", json={"student": raw})
            if resp.status_code != 400:
                c.check(f"B2 非法学号 {raw[:18]!r} → 400 一句人话", False, f"回了 {resp.status_code}")
        dirs_after = {d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))} \
            if os.path.isdir(root) else set()
        c.check("B2 上面那批非法学号**全部**回 400（不许默默回退到默认学生，也不许建目录）",
                dirs_after == dirs_before and all(
                    anon.post("/api/login", json={"student": x}).status_code == 400
                    for x in bad_ids),
                f"目录 {sorted(dirs_before)} → {sorted(dirs_after)}")
        c.check("B2-bis 数据目录之外没多出任何文件（路径穿越真被拦住了，不是只看状态码）",
                not os.path.exists(os.path.join(data_path(".."), "etc"))
                and not any(".." in d for d in dirs_after), f"student 下子目录 {sorted(dirs_after)}")

        # 手改 cookie 也要拦：只在 /api/login 校验 = 门锁只装了一半
        evil = make_client(headers={"Cookie": "ct_student=../../evil"}, admin_token="1234567890")
        rv = evil.post("/api/todos", json={"title": "带毒 cookie 的一条", "date": "2026-12-31",
                                           "start": "11:00", "end": "12:00"})
        c.check("B3 cookie 被手改成 `../../evil` 时：不当身份用、走兜底老位置、**不在数据目录外写文件**",
                rv.status_code == 200
                and any(t.get("title") == "带毒 cookie 的一条" for t in _todos_of(""))
                and not os.path.exists(os.path.join(data_path(), "..", "evil")),
                f"{rv.status_code} 老位置 {len(_todos_of(''))} 条")
        c.check("B3-bis 这种非法 cookie 下 whoami 也是空串（前端不会显示一个不存在的学号）",
                evil.get("/api/whoami").json().get("student") == "", "")

        ok = anon.post("/api/login", json={"student": "S-001_x"})
        c.check("B4 合法学号（含下划线/短横线）登录成功且 Set-Cookie 带 Path=/",
                ok.status_code == 200 and "ct_student=S-001_x" in ok.headers.get("set-cookie", "")
                and "Path=/" in ok.headers.get("set-cookie", ""),
                repr(ok.headers.get("set-cookie")))
        bad = anon.post("/api/login", json={"student": "../x"})
        c.check("B4-bis 失败那一次**不带** Set-Cookie（不合法就不许悄悄给人发身份）",
                bad.status_code == 400 and "set-cookie" not in {k.lower() for k in bad.headers},
                repr(bad.headers.get("set-cookie")))
    return c


def batch_c_shape():
    c = Checker()
    title("C. 形状：set 身份只一处、拼路径只一处、文档不许隐瞒")
    mn = open("app/main.py", encoding="utf-8").read()
    st = open("app/store.py", encoding="utf-8").read()
    stu = open("app/static/student.html", encoding="utf-8").read()
    lgn = open("app/static/login.html", encoding="utf-8").read()

    c.check("C1 全项目 `set_current_student(` 只出现在中间件那一处（业务里再设一次=半路换人）",
            mn.count("set_current_student(") == 1, f"main 里 {mn.count('set_current_student(')} 处")
    c.check("C1-bis store 里也只有定义那一处，且不许用 module 级可变全局变量存身份",
            st.count("set_current_student(") == 1 and "global _CURRENT_STUDENT" not in st
            and "contextvars.ContextVar(" in st, "")
    c.check("C2 拼个人库路径只有一处：`os.path.join(_data_dir(), \"student\"` 在 store 里恰好一次（student_root）",
            st.count('os.path.join(_data_dir(), "student"') == 1
            and "os.path.join(_data_dir(), \"student\", name)" not in st, "")
    import re as _re
    c.check("C2-bis main 里不许自己拼个人库路径（要目录就找 store 要：student_root/student_dir）"
            "—— 判据是「os.path.join 里出现 \"student\" 这个字面量段」，不是搜这个词（那是 JSON 键名，会误伤）",
            _re.search(r'os\.path\.join\([^)]*"student"', mn) is None
            and 'os.path.join(_data_dir(), "student"' not in mn, "")
    c.check("C3 白名单只有一份定义，且中间件与登录两处都用它（不是两处各写一份正则）",
            mn.count("_STUDENT_ID_RE = re.compile(") == 1
            and mn.count("_STUDENT_ID_RE.fullmatch(") == 2,
            f"fullmatch {mn.count('_STUDENT_ID_RE.fullmatch(')} 处")
    c.check("C4 中间件里也过一遍白名单：只在 /api/login 校验等于门锁只装了一半",
            "_clean_student(request.cookies.get(_STUDENT_COOKIE))" in mn, "")
    c.check("C5 「这不是鉴权」在代码与登录页两处都写在脸上（不许让人当安全边界）",
            "这不是鉴权" in mn and ("不设密码" in lgn or "只是把每个人的课表待办分开" in lgn), "")
    # 这一条是真机抓出来的：go() 写得好好的，【进入】那颗按钮忘了 addEventListener ——
    # 点下去**什么请求都不发**，语法检查、接口测试、读代码全看不出来。
    c.check("C5-bis 登录页三颗按钮都真的接了线（asStudent 展开 / sidGo 提交 / sidBack 返回），"
            "而且提交走的是 go() 这一个函数（不许再抄一份 fetch）",
            'document.getElementById("asStudent").addEventListener("click"' in lgn
            and 'document.getElementById("sidGo").addEventListener("click", go)' in lgn
            and 'document.getElementById("sidBack").addEventListener("click"' in lgn
            and lgn.count('fetch("/api/login"') == 1, "")
    c.check("C5-ter 登录失败**不偷偷放进去**：错误栏那条分支必须 return（不然带着没设上的身份就进了 /student）",
            'err.textContent = "进不去：' in lgn and "return;" in lgn[lgn.index("进不去："):], "")
    c.check("C6 学生端：顶栏有身份那一格 + 退出按钮；回到登录页只有两处（未登录引导 + 退出）",
            'id="whoChip"' in stu and 'id="logoutBtn"' in stu
            and stu.count('location.href = "/"') == 2,
            f"页面里 location.href 共 {stu.count('location.href')} 处")
    c.check("C6-bis 退出必须一起清掉 campustime_session（浏览器级的对话 id，留着就是把上一位的记忆递给下一位）",
            'localStorage.removeItem("campustime_student")' in stu
            and 'localStorage.removeItem("campustime_session")' in stu, "")
    c.check("C6-ter 未登录**不硬跳转**，只把那一格标灰并给一个去登录的入口"
            "（直接打开 /student 这条老路还得能用：README 复现、录屏、评委随手一敲）",
            "演示公共库" in stu and "chip.onclick" in stu, "")
    c.check("C7 播种只拷课表与待办，**不拷 sessions.json**（对话记忆是这个人说过什么）",
            'for name in ("todos.json", "timetable.json")' in mn and "sessions.json" not in
            mn[mn.index("def _seed_first_login"):mn.index("@app.post(\"/api/login\"")], "")
    ex = open("EXPLAIN.md", encoding="utf-8").read()
    c.check("C8 EXPLAIN 六那条「没有账号隔离」必须已回写成\"已做最小版\"，并照实写明不是鉴权 + 兜底保留",
            "已做最小版" in ex and "不是鉴权" in ex and "并发写盘" in ex, "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_59_student_identity：学生身份=学号，个人库按学号分开（#32）")
    print("=" * 60)
    code |= batch_a_separated().summary("A 批（三个面都按人分）")
    code |= batch_b_fallback_and_whitelist().summary("B 批（兜底 + 白名单 + 穿越）")
    code |= batch_c_shape().summary("C 批（形状与文档不隐瞒）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
