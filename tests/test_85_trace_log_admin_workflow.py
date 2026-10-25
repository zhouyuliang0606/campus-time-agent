"""#64 的护栏：管理端「Agent 工作流」—— 每一轮的推理链落盘并画出来（只读可观测性）。

批次分工：

  A 形状：**落盘点只有一个**（`_chat_json` 的 `return resp` 之前；`_chat_impl` 那几十个 return 一个都不许补）；
        三条「会当众说错话」的规则各钉一条 —— 工具次数折半、result 收 1200 而学生视图仍收 120、
        单行上限按**字节**不是按字符；store 侧真正 open 流水的只有四处且都走 `_trace_path()`。
  B 行为：真打一句 → 流水恰好多一行且字段齐；`owner` 兜底是 `demo` 不是空串（施工单那条与实测不同）；
        `_trace_record` 用合成 resp 精确复现「两次调用 = 四条 action」与超长 result；
        超上限那一行必须打 `truncated`，且这条输入**字符数在上限之下、字节数在上限之上**
        （B12 原样喂的那行两种算法同解，分不出按字节还是按字符 —— 变异 Y6 逼出来的洞）；
        坏行跳过；**旁路不许拖主线**（写不进去时那句话照常 200，且那句请求自己包 try：
        让它报红而不是把整批打崩，崩≠这条判据有牙）。
  C 接口：三个口都要口令；`clear` 注册在 `{row_id}` 之前；列表不许带完整 trace；limit 夹住；
        三个口调完业务数据一个字节没动（「只读」两个字的证明）；也没有第四个「重放/重跑」口。
  D 前端：四处配对（左栏 / 容器 / 标题表 / switchNav）；3 秒轮询走 `_pollVisible` 且那一档不在前景就不打；
        六段流水的 ⑥ 带实测放宽的理由；新增那块不许用下线字形；**没有重放按钮**。
  E 登记：新加的三个挂门口要在 `test_32` 两份清单里留名；`test_57` 的档位表与 id 表要有这一档。
  E-bis 生产回落：另起一个干净解释器，不带 TRACE env、只给数据目录 → 流水落回数据目录那份。

⚠️ 「模型每轮拿到几只手」的数字在 `test_82` 钉、工具名在 `test_84` 钉 —— 本文件一个都不重复钉
   （同一个数两处各钉一遍就是两个会互相打脸的判据，这仓赔过好几次）。
"""
import ast
import hashlib
import json as _json
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import (PROJ_DIR, Checker, data_path, make_client, sandbox,  # noqa: E402
                      seed_todos, title)

MAIN = os.path.join(PROJ_DIR, "app", "main.py")
STORE = os.path.join(PROJ_DIR, "app", "store.py")
ADMIN = os.path.join(PROJ_DIR, "app", "static", "admin.html")
HARNESS = os.path.join(PROJ_DIR, "tests", "_harness.py")
T32 = os.path.join(PROJ_DIR, "tests", "test_32_admin_token_gate.py")
T57 = os.path.join(PROJ_DIR, "tests", "test_57_admin_shell.py")

# 施工单点名的那两颗下线字形之外，整份名单从 test_44 **抄一份**（不 import：
# 那份名单是它的证据，抄过来才看得出「我这一档没踩」，import 会把两批测试捆成一崩一起）。
RETIRED = ["\U0001F5D3", "\u2705", "\u2699", "\u2795", "\U0001F4E6", "\U0001F371",
           "\U0001F4C5", "\U0001F4E2", "\u2753", "\U0001F4E4", "\U0001F4CB",
           "\U0001F4E8", "\U0001F916", "\U0001F9ED", "\U0001F4CE", "\U0001F3A4",
           "\U0001F9F9", "\U0001F9BD"]


def read(path):
    return io.open(path, encoding="utf-8", errors="replace").read()


def tree(path):
    return ast.parse(read(path))


def func(t, name):
    for n in ast.walk(t):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def calls_in(node, fname):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == fname]


def route(t, path, verb):
    for n in ast.walk(t):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in n.decorator_list:
            if (isinstance(d, ast.Call) and getattr(d.func, "attr", "") == verb
                    and d.args and ast.unparse(d.args[0]).strip("'\"") == path):
                return n
    return None


def body_of(path, start_marker, end_marker):
    s = read(path)
    return s[s.index(start_marker):s.index(end_marker)]


def code_of(text):
    """负向断言先剔注释（本仓老规矩：不许拿自己的注释当代码判红）。"""
    t = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return "\n".join(ln.split("//")[0] for ln in t.splitlines())


def _md5(path):
    if not os.path.exists(path):
        return "MISSING"
    return hashlib.md5(io.open(path, "rb").read()).hexdigest()


def _lines(text):
    return sum(1 for ln in text.splitlines() if ln.strip())


def chat(client, msg, sid, agent=False, module="planner"):
    body = {"message": msg, "module": module, "session_id": sid}
    if agent:
        body["agent_mode"] = True
    return client.post("/api/chat", json=body)


# ===================== A. 形状 =====================

def batch_a_shape():
    c = Checker()
    title("A. 出口那一处 + 三条容易说错话的规则")
    mt = tree(MAIN)
    src = read(MAIN)
    cj, ci = func(mt, "_chat_json"), func(mt, "_chat_impl")
    tr = func(mt, "_trace_record")
    seg = body_of(MAIN, "★ #64", "async def _run_agent_loop")

    lt = calls_in(cj, "log_trace")
    last_return = max(n.lineno for n in ast.walk(cj) if isinstance(n, ast.Return))
    c.check("A1 落盘点只有一个：`_chat_json` 里 `log_trace(` 恰好一次，且写在 `return resp` **之前**",
            len(lt) == 1 and lt[0].lineno < last_return,
            f"log_trace 在行 {[n.lineno for n in lt]}，return@{last_return}")
    c.check("A2 `_chat_impl` 里一个 `log_trace` 都没有 —— 那几十个 return 逐个补必漏（施工单 §给执行方的 1）",
            not calls_in(ci, "log_trace"), f"{len(calls_in(ci, 'log_trace'))} 处")
    nret = len([n for n in ast.walk(ci) if isinstance(n, ast.Return)])
    c.check("A2-bis 上面那句不是空话：`_chat_impl` 的 return 实测就有几十条（少于 50 说明函数被拆过，落盘点要重新想）",
            nret >= 50, f"{nret} 条 return")
    cp = func(mt, "chat")
    nj = 0 if cp is None else len(calls_in(cp, "_chat_json"))
    c.check("A3 两条出口（JSON 与 SSE）都从 `_chat_json` 走 ⇒ 两种前端都会落盘，不会只记一半",
            nj == 2, f"/api/chat 里 `_chat_json(` 出现 {nj} 次")
    c.check("A4 落盘在 `isinstance(resp, dict)` 闸后面、自己套 try —— 旁路不许把 400 那种出口搞崩",
            "if isinstance(resp, dict):" in seg and "except Exception:" in seg, "")
    c.check("A5 记的那一行整体出自 `_trace_record`（三条规则抽出来才单测得动，别写回内联）",
            tr is not None and "log_trace(_trace_record(resp, meta))" in seg, "")
    c.check("A6 工具次数是**折半**算的（引擎每次调用写 🔧 与 📦 两条都带 action）—— 直接数条数会当众把 2 次说成 4 次",
            any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.FloorDiv) for n in ast.walk(tr)), "")
    body = "\n".join(src.splitlines()[tr.lineno - 1:tr.end_lineno])
    c.check("A7 result 上限是一个具名常量、这个函数里没混进学生端那 120 字（两把尺子两个理由，合并必有一头被将就）",
            "TRACE_RESULT_MAX = 1200" in src and "[:TRACE_RESULT_MAX]" in body and "[:120]" not in body, "")
    c.check("A8 摘要那一列有东西可显：answer_head 与它的上限常量都在（列表把 trace 剥了，不存这一截就只剩一个字数）",
            "TRACE_HEAD_MAX = 40" in src and '"answer_head"' in body, "")
    st = read(STORE)
    c.check("A9 单行上限按**字节**算：中文那本 JSON 的字符数只有字节的三分之一，按字符会把 4MB 当成 1.3MB 放过",
            'len(line.encode("utf-8")) > TRACE_ROW_MAX' in st, "退回 len(line) 就是这条要抓的")
    c.check("A10 `owner` 用的是 store 那一份口径（current_owner），不在出口这里另读 cookie 猜是谁",
            'meta["owner"] = current_owner()' in src, "")
    c.check("A11 meta 那两个键就在 `_agent_mode` 算出来那一处回填（两处各算 = 第二个 12b）",
            src.index('meta["agent_mode"] = _agent_mode') > src.index("_agent_mode = _agent_mode_on(body)"),
            "")

    sdt = tree(STORE)
    per = {}
    for fname in ("log_trace", "read_trace_log", "get_trace_row", "clear_trace_log"):
        f = func(sdt, fname)
        per[fname] = len([n for n in ast.walk(f) if isinstance(n, ast.Call)
                          and getattr(n.func, "id", "") == "open"
                          and n.args and "_trace_path" in ast.unparse(n.args[0])])
    c.check("A12 每一处 open 都走 `_trace_path()`：写 1 / 读列表 1 / 取一行 1 / 清空 2（先数行再截断），"
            "四个函数之外一处都没有",
            per == {"log_trace": 1, "read_trace_log": 1, "get_trace_row": 1, "clear_trace_log": 2},
            str(per))
    hard = [ln.strip()[:58] for ln in st.splitlines()
            if "trace_log.jsonl" in ln and not ln.strip().startswith("TRACE_FILE =")]
    c.check("A13 除了 TRACE_FILE 那行定义，别处不许再写死文件名（写死 = 绕开 env 隔离 = 污染演示台账）",
            not hard, " | ".join(hard)[:88])
    c.check("A14 建目录建的是**当前流水所在的那个目录**，不是 import 时缓存的 DATA_DIR（红线 23 那一族）",
            "os.makedirs(os.path.dirname(_trace_path())" in st, "")
    c.check("A15 没有 TRACE env 时回落到数据目录（管理端读的就是那份 —— 把生产一起隔离 = 这一页静默失明）",
            'os.environ.get("CAMPUSTIME_TRACE_FILE") or _path(TRACE_FILE)' in st, "")
    lg = func(sdt, "log_trace")
    c.check("A16 `log_trace` 对外承诺不抛异常（except 体里只有 pass）—— 记账是旁路，对话才是主线",
            any(isinstance(n, ast.ExceptHandler) and n.body and all(isinstance(b, ast.Pass) for b in n.body)
                for n in ast.walk(lg)), "")
    mixed = [fn for fn in ("log_trace", "read_trace_log", "get_trace_row", "clear_trace_log")
             if calls_in(func(sdt, fn), "log_llm_usage")]
    c.check("A17 这本流水与用量那本没混：四个 trace 函数里没有一个去调 log_llm_usage（AST 数调用，不数注释里的提及）",
            not mixed, f"混用：{mixed}")
    return c


# ===================== B. 行为 =====================

def batch_b_live():
    c = Checker()
    title("B. 真打一句进去：流水里到底有没有这一轮、长什么样")
    with sandbox():
        from app import store as S
        from app.main import _trace_record
        S.clear_trace_log()
        seed_todos([{"title": "写作业", "date": "2026-10-27", "start": "14:00", "end": "15:00"}])
        cl = make_client()
        n0 = len(S.read_trace_log(limit=500))
        r = chat(cl, "我的待办", "t64-list")
        rows = S.read_trace_log(limit=500)
        c.check("B1 一轮对话 = 流水里恰好多一行（不多记、不漏记）",
                r.status_code == 200 and len(rows) == n0 + 1, f"{n0} -> {len(rows)}")
        row = rows[0] if rows else {}
        need = ("sid", "owner", "module", "agent_mode", "tool_calls", "steps",
                "answer_len", "answer_head", "options_n", "ts", "id")
        c.check("B2 那一行的字段齐（缺哪一个，管理端就有一列是空的 —— 字段名一次钉死）",
                all(k in row for k in need), f"缺：{[k for k in need if k not in row]}")
        c.check("B3 sid 与 module 跟请求一致（回放时靠这两个字段认人认栏）",
                row.get("sid") == "t64-list" and row.get("module") == "planner",
                f"{row.get('sid')} / {row.get('module')}")
        c.check("B4 没带身份时 owner 记的是 demo，**不是空串**（施工单写「记空」，实测 store 那侧有兜底 —— 记下来防有人照单改）",
                row.get("owner") == "demo", repr(row.get("owner")))
        full = S.get_trace_row(row.get("id")) or {}
        tr = full.get("trace") or []
        c.check("B5 存的是**原始**那条链：判定那一行与本轮自己的步都在（只存学生视图那份等于看二手）",
                any("我把这句听成" in str(t.get("phase") or "") for t in tr) and len(tr) >= 2, f"{len(tr)} 步")
        c.check("B6 列表读出来的每行不带 trace、`get_trace_row` 才给全文（一屏 20 行 × 几万字会把管理页自己拖死）",
                all("trace" not in x for x in S.read_trace_log(limit=5))
                and "trace" in (S.get_trace_row(row.get("id")) or {}), "")
        chat(cl, "我这周有什么待办", "t64-agent", agent=True)
        ag = [x for x in S.read_trace_log(limit=99) if x.get("sid") == "t64-agent"]
        c.check("B7 agent_mode 跟着请求走（这一轮开了就是 True）—— 对比剧本要靠它把两行分开",
                len(ag) == 1 and ag[0].get("agent_mode") is True, f"{len(ag)} 行")
        c.check("B7-bis 普通模式那一轮记的是 False（不是缺字段，也不是 None）",
                row.get("agent_mode") is False, repr(row.get("agent_mode")))

        acts = [{"step": i, "phase": "🔧 调用工具", "action": "tool%d" % i, "args": {"a": 1}} for i in (1, 2)]
        acts += [{"step": i, "phase": "📦 工具结果", "action": "tool%d" % i, "result": "x" * 3000} for i in (1, 2)]
        rec = _trace_record({"session_id": "s", "module": "faq", "answer": "y" * 100,
                             "options": [{"kind": ""}], "trace": acts}, {"owner": "o", "agent_mode": True})
        c.check("B8 合成「两次调用」= 四条 action ⇒ tool_calls 必须是 2 而不是 4（变异 Y3 的靶子）",
                rec["tool_calls"] == 2 and len(rec["tools"]) == 4,
                f"tool_calls={rec['tool_calls']} tools={len(rec['tools'])}")
        c.check("B9 超长 result 收成 1200 字加一个省略号（学生视图那 120 字是另一把尺子，不在这里）",
                len(rec["trace"][2]["result"]) == 1201, str(len(rec["trace"][2]["result"])))
        c.check("B10 answer_head 是 40 字、answer_len 是全长（两个数各管一件事，别合成一个）",
                len(rec["answer_head"]) == 40 and rec["answer_len"] == 100,
                f'{len(rec["answer_head"])} / {rec["answer_len"]}')
        c.check("B11 判定/依据这些**不带 action** 的行没被算进工具次数（折半只对成对的行成立，混算就会少报）",
                _trace_record({"trace": [{"phase": "🧭 判定"}], "answer": "a"}, {})["tool_calls"] == 0, "")

        big = [{"step": i, "phase": "📦 工具结果", "action": "a", "result": "汉" * 900} for i in range(80)]
        S.log_trace({"sid": "big", "trace": big})
        got = [x for x in S.read_trace_log(limit=99) if x.get("sid") == "big"]
        back = S.get_trace_row(got[0]["id"]) if got else {}
        c.check("B12 超过单行上限的那一行被砍到 20 步并打上 truncated（宁可少记几步，不能让一句长回答撑爆整本）",
                bool(back) and back.get("truncated") is True and len(back.get("trace") or []) == 20,
                f"truncated={back.get('truncated')} 步={len(back.get('trace') or [])}")
        # ★ 变异 Y6 打出来的洞：上面那行 `big` 有 72k 字，按**字符**数也照样超上限 ——
        #   也就是说 B12 那两条根本分不出"按字节"和"按字符"两种写法（Y6 红了 A9 却没红 B12）。
        #   补一条只有按字节才成立的：中文那一行字符数在上限**之下**、字节数在上限**之上**。
        zh = {"sid": "中文行", "trace": [{"step": i, "phase": "汉字" * 150} for i in range(100)]}
        zline = _json.dumps(zh, ensure_ascii=False)
        cap = S.TRACE_ROW_MAX
        c.check("B12-tris 这条输入自己的形状先钉住：字符数在单行上限之下、字节数在上限之上"
                "（形状不成立下面那条就是在测空气，Y6 那一类改法会一路绿灯）",
                len(zline) < cap < len(zline.encode("utf-8")),
                f"字符={len(zline)} 字节={len(zline.encode('utf-8'))} 上限={cap}")
        S.log_trace(zh)
        zg = [x for x in S.read_trace_log(limit=99) if x.get("sid") == "中文行"]
        zback = S.get_trace_row(zg[0]["id"]) if zg else {}
        c.check("B12-tris2 那一行**必须**被砍（按字符数算它没超，所以这一条只有按字节才红得起来 —— Y6 的靶子）",
                bool(zback) and zback.get("truncated") is True and len(zback.get("trace") or []) == 20,
                f"truncated={zback.get('truncated')} 步={len(zback.get('trace') or [])}")
        S.log_trace({"sid": "small", "trace": [{"step": 1, "phase": "x"}]})
        sm = [x for x in S.read_trace_log(limit=99) if x.get("sid") == "small"]
        c.check("B12-bis 没超上限的行**不许**带 truncated 标（带了就是告诉人「这条被砍过」，那是谎）",
                bool(sm) and "truncated" not in (S.get_trace_row(sm[0]["id"]) or {}), "")
        with io.open(S._trace_path(), "a", encoding="utf-8") as f:
            f.write("这行不是 JSON\n\n")
        c.check("B13 流水里混进坏行与空行：读取整本跳过、不炸也不判空（一行坏不该毁掉整页）",
                len(S.read_trace_log(limit=99)) >= 3, str(len(S.read_trace_log(limit=99))))
        c.check("B14 四条筛法各管一件事：module / agent_mode / only_tools / q，且 q 能搜到 trace 里的字",
                all(S.read_trace_log(limit=99, module="planner"))
                and not S.read_trace_log(limit=99, module="nosuch")
                and all(x.get("agent_mode") for x in S.read_trace_log(limit=99, agent_mode="1"))
                and all(x.get("tool_calls", 0) > 0 for x in S.read_trace_log(limit=99, only_tools=True))
                and any(x.get("sid") == "big" for x in S.read_trace_log(limit=99, q="汉汉汉")), "")
        got = S.read_trace_log(limit=99)
        c.check("B15 新的在前、limit 真截了（列表第一行就是刚问的那句 —— 实时那一拍靠它）",
                got[0].get("ts", "") >= got[-1].get("ts", "") and len(S.read_trace_log(limit=1)) == 1, "")
        raw_lines = _lines(read(S._trace_path()))
        c.check("B16 清空回的是**文件里的非空行数**（含那条坏行 —— 报少了就是前端那句「已清掉 N 轮」说谎）",
                S.clear_trace_log() == raw_lines and S.read_trace_log(limit=99) == [], f"{raw_lines} 行")
    return c


def batch_b_bypass():
    c = Checker()
    title("B-bis. 旁路不许拖主线：写不进去的时候，那句话还是得答")
    with sandbox():
        seed_todos([{"title": "写作业", "date": "2026-10-27", "start": "14:00", "end": "15:00"}])
        cl = make_client()
        a0 = chat(cl, "我的待办", "t64-ok").json().get("answer")
        prev = os.environ.get("CAMPUSTIME_TRACE_FILE")          # 出去时**恢复**，不是 pop：
        os.environ["CAMPUSTIME_TRACE_FILE"] = PROJ_DIR          #   外面那层是进程级临时流水（pop 会让后面的批开始写别处）
        # ★ 这里必须自己接住异常：变异 Y11（两层保险一起拆）实测会让这一句**直接抛穿** _chat_json，
        #   不接就是"整批崩了"——崩只告诉你批次没了，不告诉你 B17 这条判据有没有牙。
        try:
            r = chat(cl, "我的待办", "t64-bad")
            a1, boom = r.json().get("answer"), ""
        except Exception as e:
            r, a1, boom = None, None, f"{type(e).__name__}: {e}"
        if prev is None:
            os.environ.pop("CAMPUSTIME_TRACE_FILE", None)
        else:
            os.environ["CAMPUSTIME_TRACE_FILE"] = prev
        c.check("B17 流水写不下去时这一句照常 200、答案与之前**逐字相同**（Y4 的靶子：旁路挂了不能拖主线）",
                boom == "" and r is not None and r.status_code == 200 and a0 == a1 and bool(a0),
                f"{boom or (str(r.status_code) + ' / ' + str(a1)[:40])}")
        c.check("B18 失败时也没在别处留下半成品文件（旁路失败就该安静，不该到处留临时垃圾）",
                not os.path.exists(os.path.join(PROJ_DIR, "trace_log.jsonl")), "")
    return c


def batch_b_sse():
    c = Checker()
    title("B-bis2. SSE 那条出口也要落同一行（两条出口共用一个汇聚点）")
    with sandbox():
        from app import store as S
        S.clear_trace_log()
        seed_todos([{"title": "写作业", "date": "2026-10-27", "start": "14:00", "end": "15:00"}])
        cl = make_client(headers={"Accept": "text/event-stream"})
        r = cl.post("/api/chat", json={"message": "我的待办", "module": "planner",
                                       "session_id": "t64-sse"})
        rows = S.read_trace_log(limit=99)
        c.check("B19 流式出口也记一行（学生端走 SSE 那一路要是漏了，症状是「屏上有、管理端没有」）",
                r.status_code == 200 and any(x.get("sid") == "t64-sse" for x in rows),
                f"status={r.status_code} 行={len(rows)}")
    return c


# ===================== C. 接口 =====================

def batch_c_api():
    c = Checker()
    title("C. 管理端三个口：挂门、只读、注册顺序")
    mt = tree(MAIN)
    src = read(MAIN)
    with sandbox():
        from app import store as S
        S.clear_trace_log()
        seed_todos([{"title": "写作业", "date": "2026-10-27", "start": "14:00", "end": "15:00"}])
        todo_file = data_path("student", "demo", "todos.json")
        md0 = _md5(todo_file)

        cl0 = make_client(admin_token=None)
        probe = [(m, p, cl0.request(m, p).status_code)
                 for m, p in (("GET", "/api/admin/trace"), ("GET", "/api/admin/trace/whatever"),
                              ("POST", "/api/admin/trace/clear"))]
        c.check("C1 没口令时三个口一个都进不去（401/403）—— 含那个只读列表：这本流水里是全校每一轮的原始轨迹",
                all(s in (401, 403) for _, _, s in probe), str(probe))

        cl = make_client()
        chat(cl, "我的待办", "t64-api")
        chat(cl, "我这周有什么待办", "t64-api2", agent=True)
        d = cl.get("/api/admin/trace?limit=20").json()
        rows = d.get("rows") or []
        c.check("C2 带口令：列表回 rows + modules，而且新的在上（下拉的候选就靠 modules 那一份）",
                d.get("ok") is True and len(rows) == 2 and rows and rows[0].get("sid") == "t64-api2"
                and "planner" in (d.get("modules") or []), f"{len(rows)} 行 modules={d.get('modules')}")
        c.check("C3 列表每行**不许**带完整 trace（一屏 20 轮 × 几万字会把管理页自己拖死，详情是点开才取）",
                all("trace" not in r for r in rows), "")
        rid = rows[0]["id"] if rows else ""
        one = cl.get("/api/admin/trace/" + rid).json()
        c.check("C4 详情那一轮带完整 trace（管理端要看的就是「模型实际拿到了什么」，不是学生那份二手）",
                isinstance(one.get("row"), dict) and isinstance(one["row"].get("trace"), list)
                and one["row"].get("sid") == "t64-api2", "")
        m404 = cl.get("/api/admin/trace/no-such-id")
        c.check("C5 不存在的 id 回 404、话说得清（找不到了），不是回一套 null 让前端画一张空详情",
                m404.status_code == 404 and "找不到" in str(m404.text), f"{m404.status_code} {m404.text[:60]}")
        c.check("C6 limit 被夹在 1~300（不许 limit=十万把整本流水一次性读进响应）",
                cl.get("/api/admin/trace?limit=99999").status_code == 200
                and "min(int(limit or 100), 300)" in src, "")
        body = src[src.index("async def admin_trace("):src.index("async def admin_trace_clear")]
        c.check("C7 筛选判据只在 store 一处：路由既不自己判 module 也不自己判 tool_calls，只把四个条件原样交给 read_trace_log",
                'r.get("module") ==' not in body and 'tool_calls' not in body.split("mods =")[0]
                and "module=module" in body and "agent_mode=agent_mode" in body
                and "only_tools=only_tools" in body and "q=q" in body, "")
        r = cl.post("/api/admin/trace/clear")
        c.check("C8 清空回的是行数 + 一句人话回执，并且下一次列表真的空了（前端不许自己猜清了几个）",
                r.status_code == 200 and r.json().get("cleared", 0) >= 2
                and "已清空" in r.json().get("message", "")
                and cl.get("/api/admin/trace").json().get("rows") == [], str(r.json()))
        c.check("C9 这三个口确实只读：演示台账的 md5 一次没变（「只读可观测性」这句话要能对得上红线 4）",
                _md5(todo_file) == md0, f"{md0[:8]} -> {_md5(todo_file)[:8]}")

    p_clear = route(mt, "/api/admin/trace/clear", "post")
    p_one = route(mt, "/api/admin/trace/{row_id}", "get")
    c.check("C10 clear 注册在 {row_id} **之前**（今天真正挡着的是方法不同；顺序是第二道，Y5 拿实测说话）",
            p_clear is not None and p_one is not None and p_clear.lineno < p_one.lineno,
            f"clear@{getattr(p_clear, 'lineno', None)} row@{getattr(p_one, 'lineno', None)}")
    c.check("C11 两个口的方法形状不重叠：detail 是 GET、clear 是 POST（把 clear 改成 GET 就会被当成一个 id）",
            p_clear is not None and p_one is not None
            and getattr(p_clear.decorator_list[0].func, "attr", "") == "post"
            and getattr(p_one.decorator_list[0].func, "attr", "") == "get", "")
    c.check("C12 这一族没有第四个口：不存在重放/重跑那种路径（重跑会走写库分支，正撞红线 4）",
            not re.findall(r'@app\.(?:get|post)\("/api/admin/trace[^"]*(?:replay|rerun)', src), "")
    return c


# ===================== D. 前端 =====================

def batch_d_front():
    c = Checker()
    title("D. 那一档前端：四处配对、轮询、下线字形、没有重放")
    st = read(ADMIN)
    c.check("D1 左栏有 data-nav 为 trace 那一档，且同一页就有它的视图容器（只加按钮没容器 = 点了没反应）",
            'data-nav="trace"' in st and 'id="view-trace"' in st, "")
    nav = st[st.index("const NAV_TITLE"):st.index("function switchNav")]
    sw = st[st.index("function switchNav"):st.index('document.getElementById("sbNav")')]
    c.check("D2 标题表里 trace 那一条落在表内（不是写在别处的同名字符串）",
            'trace: "Agent 工作流"' in nav, nav[:70].replace("\n", " "))
    c.check("D3 switchNav 里 trace 那一支**就是**调 loadTrace（点开才拉，与其余各档同构）",
            'view === "trace") loadTrace();' in sw, "")
    c.check("D4 轮询 3 秒、走 _pollVisible（标签页在后台就不打），而不是再发明一份计时器或 WebSocket",
            "setInterval(_pollVisible(loadTrace), 3000);" in st and "WebSocket" not in st, "")
    lt = st[st.index("async function loadTrace"):st.index("async function openTrace")]
    c.check("D5 而且 loadTrace 自己知道「这一档不在前景就别打」—— 切走就停，不靠外面那道守卫独扛",
            'view-trace' in lt and 'classList.contains("active")' in lt, "")
    blk = st[st.index("const TRACE_SEG_NAMES"):st.index("function traceFlowHtml")]
    c.check("D6 六段的名字一个不缺（演示时手指要一段一段点着讲）",
            all(k in blk for k in ("① 判定", "② 路由", "③ 思考", "④ 工具", "⑤ 提案", "⑥ 回答")), "")
    c.check("D7 命中条件用的是轨迹里**真有的字样**（写错一个字那一段就永远不亮），且第⑥段带实测放宽的那个或",
            'has("我把这句听成")' in blk and 'has("🧭")' in blk and 'has("🤔")' in blk
            and "!!t.action" in blk and "options_n" in blk
            and 'has("💡")' in blk and "answer_len" in blk, "")
    # D8 的范围要跟 test_44 那条一致：它管的是**图标位/按钮位**（chrome），不管正文文案。
    #   这一档的正文里确实提到 🔧/📦 —— 那是在指轨迹里的标记名（跟 test_44 自己保留的 KEPT_IN_REPLY 同一类），
    #   把文案一起扫进来只会得到一种红：教人把断言删掉。JS 里的 has("🧭") 同理，那是**比对轨迹的字面量**。
    head = st[:st.index("<script")]
    nav_line = [ln for ln in head.splitlines() if 'data-nav="trace"' in ln]
    ico = re.findall(r'data-nav="trace"><span class="ico">(.).*?</span>', nav_line[0] if nav_line else "")
    ch = ico[0] if ico else ""
    c.check("D8 左栏那一档的图标位没用到下线字形（⚙/🧭 都在 test_44 那份名单里；这一档用的是 🧾）",
            bool(ch) and ch not in RETIRED, f"图标 = {ch!r}")
    vstart = head.index('id="view-trace"')
    vend = head.find('id="view-kb"', vstart)
    view = head[vstart:vend if vend > 0 else len(head)]
    btns = re.findall(r"<button[^>]*>([^<]{0,28})<", view)
    bad = [b.strip() for b in btns if any(x in RETIRED for x in b)]
    c.check("D8-bis 这一档的**按钮文案**里也没有下线字形（按钮是 chrome；正文里那两处 🔧/📦 是在指轨迹标记名，不是按钮）",
            not bad and len(btns) >= 2, f"按钮 {len(btns)} 颗，带下线字形的：{bad}")
    c.check("D9 Agent 那一枚是**纯文字芯片**：这页一份精灵表都没有（实测 <svg 零次）、也不许引 glass.css 与 <symbol，"
            "所以照单据那句「用页面已有的线性 SVG」做不到 —— 换成文字后语义没变",
            "<symbol" not in st and "glass.css" not in st and st.count("<svg") == 0
            and 'r.agent_mode ? "Agent" : "普通"' in st, f"本页 <svg 次数={st.count('<svg')}")
    js = st[st.index("Agent 工作流（#64）"):]          # 这一档自己的 JS（不是整页）
    c.check("D10 摘要那一列用的是 answer_head（列表接口把 trace 剥了；不存这一截那一列就只剩一个字数）",
            "answer_head" in js, "")
    c.check("D11 「工具」那一列旁边写清了次数不等于行数（这一列最容易被按行数出来读，一读就读错）",
            "🔧" in js and "📦" in js and "次数" in js, "")
    c.check("D12 点行进详情用的是事件委派（挂在容器上），不是给每行塞 onclick —— 后者是 test_33 D 批拦的那族",
            'getElementById("traceList").addEventListener("click"' in st and 'onclick="openTrace' not in st, "")
    # D13 钉的是**入口**，不是那两个词：页面上特意写了一句"没有【重放】按钮"来解释为什么，
    #      拿词去扫会把那句自己的说明判红（负向断言要指得到 affordance：按钮 / 绑点 / 路径）。
    c.check("D13 这一页没有重放/重跑的**入口**：没有那种按钮、没有那种 id、前端不请求那种路径"
            "（只读可观测性这条线不自己开洞；说明文案里出现这两个词是写给人看的，不算洞）",
            not re.search(r"<button[^>]*>[^<]{0,12}(重放|重跑)", st, flags=re.I)
            and not re.search(r'id="[^"]*(replay|rerun|TraceRun)[^"]*"', st, flags=re.I)
            and not re.search(r'adminFetch\("/api/admin/trace/[^"]*(replay|rerun)', st)
            and "重放" in st, "")
    # D14 是真机赔出来的一条（#64 快照 §真机）：口令刚填完，这一档**没被补拉**，界面上留着
    #   刚才那次 401 的框，而框里那句"3 秒后自动重试"救不了它 —— 这一档与文档那档的轮询都走
    #   `_pollVisible`，标签页在后台时**一次都不打**（实测 document.hidden=true，那个框能挂一整晚）。
    #   判据要成对：那张"点开才拉的档"的成对表里有 trace，且表里的 loader 与 switchNav 那一支**同名**
    #   （不同名 = 改了 switchNav 忘了改这张表，或反过来 —— 只钉一边等于换个坏法照样绿）。
    pair = re.search(r'\[\s*"trace"\s*,\s*(load\w+)\s*\]', st)
    swl = re.search(r'view === "trace"\)\s*(load\w+)\(\)', sw)
    c.check("D14 口令一填就补拉**在前景的那档**：成对表里有 trace、文档那档没被挤掉，"
            "且表里的 loader 与 switchNav 那一支同名",
            bool(pair) and bool(swl) and pair.group(1) == swl.group(1) == "loadTrace"
            and '["docs", loadDocs]' in st and '"view-" + pair[0]' in st,
            "表里=%s switchNav=%s" % (pair.group(1) if pair else None,
                                      swl.group(1) if swl else None))
    return c


# ===================== E. 登记 =====================

def batch_e_ledger():
    c = Checker()
    title("E. 别的护栏要知道这一族存在（挂门清单 / 档位表 / 测试隔离）")
    t32 = read(T32)
    c.check("E1 三个新口在 test_32 的挂门口清单里都留了名（不留名 = 那道门的 A1 根本没试过它们）",
            all(p in t32 for p in ('"/api/admin/trace"', '"/api/admin/trace/no-such-row"',
                                   '"/api/admin/trace/clear"')), "")
    c.check("E2 同一份文件那张「逐条承认」的 expect 清单也有三条（E2 就是为了让「加了口忘了留名」当场红）",
            all(f'"{m} {p}"' in t32 for m, p in (("GET", "/api/admin/trace"),
                                                 ("GET", "/api/admin/trace/{row_id}"),
                                                 ("POST", "/api/admin/trace/clear"))), "")
    t57 = read(T57)
    c.check("E3 test_57 的 VIEWS 里有 trace（档位与视图是一对的，删了它那边会替我们记住）",
            '"trace"' in t57[t57.index("VIEWS ="):], "")
    c.check("E4 新页签硬绑的那几个 id 也进了 test_57 的 MUST_KEEP_IDS（改 id = 那张卡静默报废，本项目栽过好几回）",
            all(k in t57 for k in ("traceList", "traceDetail", "traceModule", "clearTrace")), "")
    h = read(HARNESS)
    c.check("E5 沙箱把 TRACE env 管住了：进程级 setdefault + 沙箱内覆盖 + 出去时**恢复**（与 usage 同构）",
            h.count("CAMPUSTIME_TRACE_FILE") >= 4
            and 'setdefault("CAMPUSTIME_TRACE_FILE"' in h and "_prev_trace" in h,
            f"{h.count('CAMPUSTIME_TRACE_FILE')} 处")
    seg = h[h.index("SCRATCH = ("):h.index("def __enter__")]
    c.check("E6 trace 没被塞进 SCRATCH（它跟 usage 同性质：沙箱里要读得到自己写的那本，不是草稿）",
            "trace_log" not in seg, "")
    # E7 是 §一 那本账与 `.gitignore` 的**配对**判据：文件名在 store 里定义、忽略规则在仓库根上，
    #    两处各写一次就有一处会漂。漏掉的后果不是"测试红"，是把每一轮的原始轨迹（含工具结果原文与
    #    会话 id）提进公开仓库 —— 与 #41 那次"口令泄漏打码"同族，但量大得多。
    gi = read(os.path.join(PROJ_DIR, ".gitignore")).replace("\r\n", "\n")
    names = [ln.strip() for ln in gi.split("\n") if ln.strip() and not ln.startswith("#")]
    from app import store as S
    c.check("E7 `.gitignore` 排掉了 trace_log.jsonl（这本比用量账更敏感：每一轮的原始轨迹 + 会话 id；"
            "漏一行 = 谁 `git add .` 一次就把全校轨迹提进公开仓库）",
            any(x in ("/app/data/trace_log.jsonl", "app/data/trace_log.jsonl") for x in names),
            "忽略名单里的 jsonl：%s" % [x for x in names if x.endswith(".jsonl")])
    c.check("E7-bis 忽略的那一条与 store 里那个文件名**是同一个名字**（两处不同名 = 规则形同虚设）",
            any(x.endswith("/" + S.TRACE_FILE) or x == "app/data/" + S.TRACE_FILE for x in names),
            "TRACE_FILE=%s" % getattr(S, "TRACE_FILE", None))
    return c


def batch_e_fallback():
    c = Checker()
    title("E-bis. 生产侧不许被一起隔离掉：没有 TRACE env 时流水回落到数据目录")
    tmp = tempfile.mkdtemp(prefix="trace-prod-")
    env = dict(os.environ)
    env["CAMPUSTIME_DATA_DIR"] = tmp
    env.pop("CAMPUSTIME_TRACE_FILE", None)
    env["PYTHONIOENCODING"] = "utf-8"
    snippet = ("import app.store as S;"
               "S.log_trace({'sid':'生产回落探针','module':'planner','trace':[{'step':1,'phase':'x'}]});"
               "print(S._trace_path())")
    p = subprocess.run([sys.executable, "-X", "utf8", "-c", snippet], cwd=PROJ_DIR,
                       env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    wrote = os.path.join(tmp, "trace_log.jsonl")
    txt = read(wrote) if os.path.exists(wrote) else ""
    c.check("没有 TRACE env 时那一行写进**数据目录**里那份（管理端读的就是它 —— 把生产一起隔离等于让这一页静默失明）",
            p.returncode == 0 and os.path.exists(wrote) and _lines(txt) == 1,
            f"exit={p.returncode} 存在={os.path.exists(wrote)} 行={_lines(txt)} stderr={(p.stderr or '')[:80]}")
    c.check("它落盘的路径就是数据目录下那份（没有别的 env 悄悄改道）",
            os.path.normpath((p.stdout or "").strip()) == os.path.normpath(wrote),
            (p.stdout or "").strip() or repr((p.stderr or "")[:120]))
    return c


def main():
    print("test_85_trace_log_admin_workflow：#64 每一轮的推理链落盘 + 管理端「Agent 工作流」")
    print("=" * 60)
    code = 0
    for label, fn in (("A 批（形状）", batch_a_shape),
                      ("B 批（行为）", batch_b_live),
                      ("B-bis 批（旁路）", batch_b_bypass),
                      ("B-bis2 批（SSE）", batch_b_sse),
                      ("C 批（接口）", batch_c_api),
                      ("D 批（前端）", batch_d_front),
                      ("E 批（登记）", batch_e_ledger),
                      ("E-bis 批（生产回落）", batch_e_fallback)):
        try:
            rc = fn().summary(label)
        except Exception as e:                       # 一批崩不许拖着其余批次一起看不见数
            import traceback
            traceback.print_exc()
            print(f"  ❌ {label} 崩了：{type(e).__name__}: {e}")
            rc = 1
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
