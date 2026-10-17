"""#38：只开放名录里的学号（+ 签名 cookie 两层）—— 护栏。

用户 2026-10-17 20:54 原话：「**我现在要求的是只注册两个账号，输入别的学号进不去**」。
在此之前 `/api/login` **只校验格式**：任何 1~20 位字母/数字都能进，随手打一个 `abc`
就多一个「学生」、多一个空库、多一条谁都看不懂的会话 —— 今天 `student/` 下那几个
空目录就是这么攒出来的。

三道判据各管各的，本文件按这三道分三批：
  格式（`_STUDENT_ID_RE`）→ 400：学号会被拼进**文件路径**，这关是路径安全；
  名录（`is_registered`）  → 403：这个学号开没开通；**错误里绝不回名录**；
  签名（`_clean_student`）  → 挡「开发者工具里手改 cookie 冒充名录内的人」。

⚠️ 性质要说清：这道门挡得住「手输学号」，配了密钥也挡得住「手改 cookie」，
   但**挡不住**拿到密钥的人 —— 它仍然不是鉴权，只是分区（#32 同一条界限）。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title, write_accounts

KNOWN = "admin1"
STRANGER = "abcxyz"


def _write_raw_accounts(text):
    """直接写/删沙箱里的名录文件（缺失、坏 JSON、现读不缓存这三种情况都要它）。"""
    p = data_path("accounts.json")
    if text is None:
        if os.path.exists(p):
            os.remove(p)
        return
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def _whoami(client):
    return client.get("/api/whoami").json().get("student")


def batch_a_roster():
    """A 批 · 名录这道门本身。"""
    c = Checker()
    title("A. 名录：只放名单里的学号进来（403 不回名单，400 先说格式）")
    with sandbox():
        write_accounts([KNOWN, "admin2"])
        cli = make_client()
        from app import store

        c.check("A1 名录里的认得、名录外的不认（判据只有一份：is_registered）",
                store.is_registered(KNOWN) is not None and store.is_registered(STRANGER) is None, "")

        r = cli.post("/api/login", json={"student": STRANGER})
        c.check("A2 名单外 ⇒ 403（不是 400，也不是先进去再说）", r.status_code == 403,
                str(r.status_code) + " " + r.text[:50])
        blob = r.text + json.dumps(list(r.headers.items()), ensure_ascii=False)
        c.check("A2-bis 错误里不许回名单（把名录打在错误页上等于告诉别人试这两个）",
                KNOWN not in blob and "admin2" not in blob, blob[:70])
        c.check("A2-ter 失败那一次不发 cookie（没认过的身份不许先塞进浏览器）",
                "set-cookie" not in {k.lower() for k in r.headers}, "")

        ok = cli.post("/api/login", json={"student": KNOWN})
        ck = ok.headers.get("set-cookie", "")
        c.check("A3 名录内 ⇒ 200 且 Set-Cookie 带 Path=/（不写就只在当前路径带得上，/api/* 会裸奔）",
                ok.status_code == 200 and KNOWN in ck and "Path=/" in ck, repr(ck)[:70])

        for bad, why in [("", "空"), ("   ", "全是空格"), ("a b", "带空格"),
                         ("x" * 21, "超 20 位"), ("../../etc/passwd", "路径穿越")]:
            resp = cli.post("/api/login", json={"student": bad})
            c.check("A4 格式不对先说格式（" + why + "）⇒ 400，不许混成 403「没开通」",
                    resp.status_code == 400, why + " 回了 " + str(resp.status_code))

        _write_raw_accounts(None)
        c.check("A5 名录文件缺失 ⇒ list_accounts() 是空表，且任何学号都进不去（**不是全放行**）",
                store.list_accounts() == []
                and cli.post("/api/login", json={"student": KNOWN}).status_code == 403, "")
        _write_raw_accounts("{ 这不是 JSON ")
        c.check("A6 名录被改坏 ⇒ 同 A5（坏文件当全放行是最坏的那种 fallback：看着正常，门其实开着）",
                store.list_accounts() == []
                and cli.post("/api/login", json={"student": KNOWN}).status_code == 403, "")
        with open(data_path("accounts.json"), "w", encoding="utf-8") as f:
            json.dump({"accounts": [{"sid": "admin3", "name": "临时加的"}]}, f, ensure_ascii=False)
        c.check("A7 名录每次现读：改完文件不重启，同一个客户端登 admin3 就该进（缓存就得处理何时失效）",
                cli.post("/api/login", json={"student": "admin3"}).status_code == 200, "")
    return c


def batch_b_signed_cookie():
    """B 批 · 签名 cookie：配了才生效，没配就退回本单之前的行为。"""
    c = Checker()
    title("B. 签名 cookie：手改的那一枚要认不出来")
    with sandbox():
        write_accounts([KNOWN, "admin2"])
        cli = make_client()
        from app import main as m

        os.environ.pop("CAMPUSTIME_COOKIE_SECRET", None)
        r0 = cli.post("/api/login", json={"student": KNOWN})
        ck0 = r0.headers.get("set-cookie", "")
        c.check("B1 没配密钥 ⇒ 行为与本单之前逐字相同（一枚明文，老测试不整批变红）",
                ck0.startswith("ct_student=" + KNOWN + ";") or ck0.startswith("ct_student=" + KNOWN + ","),
                repr(ck0)[:70])
        c.check("B1-bis 没密钥时明文 cookie 照样认（这条就是「不配也没砸现有链路」的证据）",
                _whoami(make_client(headers={"Cookie": "ct_student=" + KNOWN})) == KNOWN, "")

        secret = "probe-cookie-secret-0123456789"
        os.environ["CAMPUSTIME_COOKIE_SECRET"] = secret
        try:
            signed = m._sign_student(KNOWN)
            c.check("B2 配了密钥 ⇒ 签发的是「学号.签名」，签名里不含密钥本身",
                    signed.startswith(KNOWN + ".") and len(signed) > len(KNOWN) + 8
                    and secret not in signed, signed[:30])
            ck1 = cli.post("/api/login", json={"student": KNOWN}).headers.get("set-cookie", "")
            c.check("B2-bis 登录回执里那一枚就是签过的", ("ct_student=" + signed) in ck1, repr(ck1)[:70])
            c.check("B2-ter 拿签过的值访问 ⇒ 认这个身份",
                    _whoami(make_client(headers={"Cookie": "ct_student=" + signed})) == KNOWN, "")
            c.check("B3 手改成明文的别人学号 ⇒ 当没身份（名单 + 签名叠起来要挡的就是这一下）",
                    _whoami(make_client(headers={"Cookie": "ct_student=admin2"})) == "", "")
            c.check("B3-bis 改了学号没重签 ⇒ 同样当没身份",
                    _whoami(make_client(headers={"Cookie": "ct_student=admin2." + "0" * 16})) == "", "")
            c.check("B3-ter 认不出之后落到**老位置**，不是别人的目录（不许认不出就挑一个）",
                    not os.path.exists(data_path("student", "admin2", "todos.json")), "")
            c.check("B4 名录与签名是两层：签过但名单外的学号照样不认（这里手工签一个名单外的）",
                    _whoami(make_client(headers={"Cookie": "ct_student=" + m._sign_student(STRANGER)})) == "",
                    "")
        finally:
            os.environ.pop("CAMPUSTIME_COOKIE_SECRET", None)
        c.check("B5 撤掉密钥后立刻回到明文那套（能不能回退，这条就是判据）",
                _whoami(make_client(headers={"Cookie": "ct_student=" + KNOWN})) == KNOWN, "")
    return c


def batch_c_shape():
    """C 批 · 形状：一处判据、比签的姿势、密钥不许写死、空名录要喊出来。"""
    c = Checker()
    title("C. 形状：判据一处、两层各一处、启动要把名单空了喊出来")
    mn = open("app/main.py", encoding="utf-8").read()
    st = open("app/store.py", encoding="utf-8").read()
    lg = open("app/static/login.html", encoding="utf-8").read()

    c.check("C1 名录判据只定义在 store 一处，main 里两处都用它（登录 + 中间件），"
            "而且 main 不许自己 open/读那份文件（那句提示里的文件名不算读文件）",
            st.count("def is_registered(") == 1 and st.count("def list_accounts(") == 1
            and mn.count("is_registered(") == 2
            and "open(\"accounts.json\"" not in mn and "_read(\"accounts.json\"" not in mn
            and st.count('_read(ACCOUNTS_FILE') == 1,
            "main 里用了 " + str(mn.count("is_registered(")) + " 处")
    c.check("C2 空名录与坏文件都落到空表，不许出现 or 兜底放行那种写法",
            "or []" in st[st.index("def list_accounts("):st.index("def is_registered(")]
            and ".get(\"accounts\") or []" in st, "")
    c.check("C3 比签用 compare_digest（== 会把对不对变成响应时间能读出来的东西）",
            "hmac.compare_digest(" in mn and "mac ==" not in mn and "mac == " not in mn, "")
    c.check("C4 密钥只从环境变量读、代码里没有写死的值；启动那条 WARNING 在（全站锁死得能被发现）",
            'os.environ.get("CAMPUSTIME_COOKIE_SECRET")' in mn
            and "_COOKIE_SECRET =" not in mn
            and "_check_accounts" in mn and "任何学号都登不进去" in mn, "")
    c.check("C5 登录页接得住 403：错误要说出来且不跳页（不然「进不去」变成「进去之后是空的」）",
            "进不去：" in lg and lg.count('fetch("/api/login"') == 1, "")
    login_seg = mn[mn.index('@app.post("/api/login"'):mn.index('@app.post("/api/logout"')]
    c.check("C6 播种没被顺手加回来：/api/login 那段里不许有 copy / seed，也不许留着 shutil",
            "shutil" not in mn and "copy" not in login_seg and "seed" not in login_seg, "")
    acc = json.load(open("app/data/accounts.json", encoding="utf-8"))
    rows = acc.get("accounts") or []
    sids = [str(r.get("sid") or "") for r in rows]
    c.check("C7 名录是 tracked 的、每条有 sid、恰好两个号（用户要的就是只注册两个账号）",
            bool(rows) and all(s.strip() for s in sids) and len(rows) == 2, str(sids))
    c.check("C7-bis 名录里没有随手打的测试号（1 / 2 / 3 那种遗骸不许变成账号）",
            set(sids) == {"admin1", "admin2"}, str(sids))
    c.check("C8 中间件那一句仍是全项目唯一的 set 点（加了名单，不许把身份挪到别处再设一遍）",
            mn.count("set_current_student(") == 1, str(mn.count("set_current_student(")) + " 处")
    return c


def main():
    print("\n" + "=" * 60)
    print("test_64_accounts：只开放名录里的学号 + 签名 cookie（#38）")
    print("=" * 60)
    code = 0
    code |= batch_a_roster().summary("A 批（名录：403 不回名单、400 先说格式）")
    code |= batch_b_signed_cookie().summary("B 批（签名 cookie：手改的认不出来）")
    code |= batch_c_shape().summary("C 批（形状：一处判据、密钥不写死、空名录要喊）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
