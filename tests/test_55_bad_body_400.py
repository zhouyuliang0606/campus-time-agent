"""#22 单子 5：请求体坏掉时，接口该回一句人话，不该甩堆栈。

病根是实打实撞出来的：我用 Git Bash 的 curl 往驿站「改状态」那个口发了一句中文，
那壳按控制台码页（GBK）把字节发出去 → `req.json()` 抛 `UnicodeDecodeError` →
**500 Internal Server Error + 服务端整段 traceback**。全项目 23 处 `await req.json()`
（都在 `app/main.py`）都是同一个形状，改 23 处不如收一处。

这个文件钉四件事：
  A 三类坏 body（非 UTF-8 字节 / 不是 JSON 的文字 / 截断的 JSON / 空 body）→ 400，
    而且响应体是**人话**（不许有 Traceback、文件路径、行号 —— 那是技术内脏，红线 8）；
  B 顺手放宽的东西：鉴权**不能被坏 body 抢在前面**（无口令 + 坏 body 打到管理端还是 401）；
    正常 JSON 的路照旧通；404 还是 404（handler 不是"什么都变 400"）；流式那条路也别挂死；
  C 形状：handler 只有一处收口（两处注册），不许长出"每个接口自己包 try"的第二套做法。

⚠️ 400 与 422 是两回事：**422 = 字段不合形**，**400 = 这句我根本没法解码**。
施工单专门提醒别断言成 422，这里就照 400 钉。
"""
import json
import os
import re

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

BAD_UTF8 = b"\xce\xbb\xca\xfd\xbe\xdd"      # GBK 的"未数据"，按 UTF-8 解不出来
NOT_JSON = "这不是 JSON，只是一句中文".encode("utf-8")     # 合法的 UTF-8，但不是 JSON
TRUNCATED = b'{"title": "\xe9\x9d\xa2\xe5\x8c\x85'   # 合法 UTF-8、JSON 截断（缺收尾引号与花括号）
EMPTY = b""

STACK_WORDS = ("Traceback", "File \"", "line ", "Exception", "JSONDecodeError",
               "UnicodeDecodeError", "Internal Server Error")


def _raw(client, method, path, body, headers=None):
    """发**原始字节**，并把"异常逃到客户端"也当成一种结果记下来。

    修复前 TestClient 默认 `raise_server_exceptions=True`，异常会直接抛出来打断测试；
    那就等于真机上浏览器看到的 500 —— 这里把它归成 ("raised", 异常名)，
    断言仍然能红，而不是整个文件崩掉。
    """
    try:
        r = client.request(method, path, content=body,
                           headers=headers or {"Content-Type": "application/json"})
        return (r.status_code, r.text)
    except Exception as e:  # noqa: BLE001  这里的"崩"就是要被抓住的对象
        return ("raised", f"{type(e).__name__}: {e}")


def _looks_clean(text: str) -> bool:
    """响应体里不该出现堆栈/内部异常名（这类文案一出现，学生/驿站端就看见技术内脏了）。"""
    return not any(w in text for w in STACK_WORDS)


def batch_a_bad_bodies():
    c = Checker()
    title("A 批 · 坏 body 一律 400，回的是人话不是堆栈")
    # 覆盖三类入口：学生端三个、驿站端两个、管理端两个 —— 一处收口就该处处生效
    # ⚠️ 登记那个口用**真实存在的**服务 id（`pkgloss` 是内置种子）：
    #    修复前实测「不存在的服务 + 坏 body」是 404（路由先判存在性，再轮到解码），
    #    那是**对的**，不能拿它来当"该 400"的样本。
    paths = [
        ("POST", "/api/chat"),
        ("POST", "/api/student-message"),
        ("POST", "/api/workorders"),
        ("POST", "/api/station/workorders/abc/status"),
        ("POST", "/api/station/services"),
        ("POST", "/api/station/services/pkgloss/records"),
        ("POST", "/api/admin/notify"),
    ]
    cases = [("非 UTF-8 字节（GBK 那种）", BAD_UTF8), ("不是 JSON 的一句中文", NOT_JSON),
             ("截断的 JSON", TRUNCATED), ("空 body", EMPTY)]
    with sandbox():
        client = make_client()      # 带口令：管理端那个口也要能走到"解码"这一步
        for label, body in cases:
            bad = []
            for method, path in paths:
                status, text = _raw(client, method, path, body)
                if status != 400 or not _looks_clean(str(text)):
                    bad.append(f"{path} -> {status} {str(text)[:50]}")
            c.check(f"{label} → 七个口全回 400 且不是堆栈", not bad, "；".join(bad[:3]))

        # 单独立一条：400 的那句话必须是**看得懂的**（不是空 body，也不是 FastAPI 的 {"detail": ...}）
        status, text = _raw(client, "POST", "/api/station/workorders/abc/status", BAD_UTF8)
        human = ""
        if status == 400:
            try:
                human = str(json.loads(text).get("error") or "")
            except Exception:  # noqa: BLE001  解不出就算这条没通过，不该把测试打断
                human = ""
        c.check("400 里带一句人话（自己的 error 字段，不是 FastAPI 的 detail）",
                status == 400 and bool(human) and "detail" not in str(text),
                f"{status} {str(text)[:60]}")
        # 坏 body 不该写进任何东西
        p = data_path("workorders.json")
        rows = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        c.check("坏 body 不产生任何副作用（工单表里每条都还是完整对象）",
                all(isinstance(r, dict) and r.get("kind") for r in rows),
                f"{len(rows)} 条")
    return c


def batch_b_no_relax():
    c = Checker()
    title("B 批 · 不许顺手放宽：鉴权在解码之前，404 还是 404，正常路径照旧通")
    with sandbox():
        # 1) 无口令 + 坏 body 打到管理端：必须是 401，不是 400
        anon = make_client(admin_token=None)
        status, text = _raw(anon, "POST", "/api/admin/notify", BAD_UTF8)
        c.check("没口令 + 坏 body → 仍然 401（解码发生在鉴权之后，坏 body 不是免检通道）",
                status in (401, 403), f"{status} {str(text)[:60]}")

        # 2) 正常 JSON 照旧通（handler 不能把合法请求一起打死）
        ok = anon.post("/api/workorders", json={"kind": "快递丢了", "desc": "书包没找到"})
        p = data_path("workorders.json")
        rows = json.load(open(p, encoding="utf-8")) if __import__("os").path.exists(p) else []
        c.check("合法 JSON 建工单照旧成功（收口没顺手改坏正路径）",
                ok.status_code == 200 and len(rows) >= 1,
                f"{ok.status_code} / {len(rows)} 条")

        # 3) 不存在的路径仍是 404 —— 全局 handler 不等于"什么错都变 400"
        status, text = _raw(anon, "POST", "/api/nosuch-endpoint", BAD_UTF8)
        c.check("不存在的路径 → 404（没被坏 body 的 400 顶掉）",
                status == 404, f"{status} {str(text)[:50]}")

        # 4) 合法 body + 不存在的服务 → 仍 404（这是应用自己给的判据，别被 400 混掉）
        nf = anon.post("/api/station/services/nosuch99/records", json={"fields": {}})
        c.check("合法 JSON + 不存在的服务 → 仍 404（400 只留给解码失败）",
                nf.status_code == 404, f"{nf.status_code}")

        # 5) 流式那条路：坏 body 不许让 SSE 半开挂死
        sse = _raw(anon, "POST", "/api/chat", BAD_UTF8,
                   headers={"Content-Type": "application/json",
                            "Accept": "text/event-stream"})
        c.check("流式出口遇到坏 body：不挂死、不 500（要么一句 error，要么 400）",
                isinstance(sse[0], int) and sse[0] in (200, 400),
                f"{sse[0]} {str(sse[1])[:60]}")
    return c


def batch_c_shape():
    c = Checker()
    title("C 批 · 形状：收口只有一处，第二套做法不许长出来")
    src = open("app/main.py", encoding="utf-8").read()
    # 剔注释再比（老规矩：我自己的注释里就写着 "exception_handler"）
    code_only = "\n".join(ln.split("#")[0] for ln in src.splitlines())
    n = len(re.findall(r"@app\.exception_handler\(", code_only))
    c.check("注册了全局处理器，且只有这一处收口（两类解码错误 = 2 行）",
            n == 2, f"{n} 处")
    c.check("两类都盖住：JSONDecodeError 与 UnicodeDecodeError",
            "JSONDecodeError" in code_only and "UnicodeDecodeError" in code_only, "")
    c.check("返回体走 JSONResponse + 400，不是 500、也不是 422",
            'status_code=400' in code_only and "JSONResponse" in code_only, "")
    # 病根那 23 处调用点：既没被换成 20 个 try（第二套做法），也没少掉
    calls = code_only.count("await req.json()")
    c.check("解码点还是一个（await req.json()），没长出第二套包法",
            calls >= 20, f"{calls} 处调用")
    # 收口必须只在一个模块里。别的地方再注册一份 = 两份规则迟早不一致。
    # （`chat()` 自己那处 try 不算第二套：流式出口必须在返回响应之前把 body 读干净，
    #   那是"半开流"的问题，跟"解码失败回什么码"是两件事，A/B 批已经把它的行为钉住了。）
    import glob

    others = []
    for fp in glob.glob("app/**/*.py", recursive=True):
        if fp.endswith("main.py"):
            continue
        body = open(fp, encoding="utf-8", errors="replace").read()
        if "exception_handler" in body:
            others.append(fp)
    c.check("全局处理器只在 main.py 一处，别的模块没长第二份",
            not others, "、".join(others)[:80])
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_55_bad_body_400：坏请求体回人话，不甩堆栈")
    print("=" * 60)
    code |= batch_a_bad_bodies().summary("A 批（坏 body 400）")
    code |= batch_b_no_relax().summary("B 批（不许放宽）")
    code |= batch_c_shape().summary("C 批（形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
