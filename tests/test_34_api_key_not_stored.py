"""学生自带的 API Key：会上传是事实，但**不能落盘**（test_34）。

**守的是什么**：P0-4 核出来的是两件分开的事，别再混成一句"API Key 伪脱敏"。

1. 文案在说谎。设置页写「密钥仅保存在本机浏览器，不会上传服务器」，
   而 `student.html:1733` 每一轮对话都把 `{base_url, model, api_key}` 整份塞进
   `/api/chat` 的请求体。为什么会说谎：`app/config.py` 的 docstring 里就写着
   「仅存本机浏览器」——**服务端注释把"存储位置"写错，前端照抄，错就长成了两份**。
   （历史快照 `ai-prompts/2026-09-27-*.md` 里也有这句，那是当时的事实记录，**不改**。）
2. 落盘没有。这条原本是"读代码看起来没有"，B/C 批把它变成**跑出来的**：
   灌一把 canary Key 进真接口，然后把沙箱数据目录**逐文件逐字节**扫一遍。

所以修的是文案（A 批钉住，别让它长回去），验的是"不落盘"（B 批）。

**为什么不干脆改成前端直连模型**（那样 Key 真的不上服务器）：
这套 Agent 的循环在服务端跑（`app/agent/engine.py` 拿工具结果再回问模型），
直连等于把 ReAct 循环搬到浏览器里重写，还要每个模型厂商各自处理 CORS。
**"密钥会经过服务器"是这套架构的固有代价**，那就把它说清楚，而不是写一句好听的谎。

**最容易被漏的一条是 B4/C**：模型调用失败时，异常文本会原样进
`llm_usage.jsonl` 的 error 字段（那是管理台能读、还可能被贴进截图的一张账）。
各家 SDK 的报错爱把请求上下文拼进异常，一旦带上 `Authorization: Bearer sk-…`，
不落盘的承诺当场作废。所以网关里加了 `_redact()`：**先把密钥抹掉，再截 300 字符**
（顺序反了会把 Key 切成半截，半截既匹配不上、又够被人拼回去）。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import PROJ_DIR, Checker, sandbox, make_client, title  # noqa: E402

CANARY = "sk-CANARY-7f3a9b0c1d2e4f5a6b7c8d9e"
OTHER = "sk-CANARY-0badf00dbadbeef00deadbeef0"
SERVER_KEY = "sk-SERVER-11223344556677889900"
BASE_URL = "http://127.0.0.1:1/v1"          # 关着的端口：真失败，但不出本机
MSG = "我这周还有哪些空可以跑步？"           # 这句确定会走模型（换个说法可能命中确定性分支，就白测了）

seen = []          # 每次"发请求"时网关实际收到的 override


class Recorder:
    """假客户端：不联网，只记下这次拿到的 override 是什么。"""

    last_usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    last_model = "rec-model"

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        seen.append(dict(override or {}))
        return {"content": "好的。"}


class Exploder:
    """假客户端：失败，而且**把密钥写进异常文本**（模拟 SDK 爱回显请求上下文那类报错）。"""

    last_usage = None
    last_model = ""

    def __init__(self, embed_at: int = 0):
        self.embed_at = embed_at

    async def chat(self, messages, tools=None, tool_choice="auto", override=None):
        key = (override or {}).get("api_key") or ""
        text = "request failed: Authorization: Bearer " + key
        if self.embed_at:
            text = "x" * self.embed_at + text      # 把密钥推到 300 字符之后
        raise RuntimeError(text)


def chat_once(client_cls, key=None, server_key=None):
    """在数据沙箱里跑一轮"学生自带 Key"的对话。

    返回 (状态码, 响应文本, 沙箱里命中 canary 的文件, 网关收到的 override 列表,
          **本次新增**的账本行)。
    ⚠️ 账本必须算"新增几行"：sandbox() 会把真实的 llm_usage.jsonl 一起拷进来
    （它不在 SCRATCH 名单里），拿整个文件判会把历史行当成本次的结果。
    ⚠️ `server_key` 是给"学生**没**带 Key"那一轮用的：沙箱里没配服务器密钥时，
    planner 会走离线 Mock、模型一次都不调，那条断言就变成**空断言**
    （"没串号"通过的原因其实是"根本没跑"）。先把服务器那把配上，才跑得起来。
    """
    import app.llm.client as _client

    # 先把 app.main 整个导完再动补丁：`router.py` 在**类定义时**就要解析
    # `llm: DeepSeekClient | None` 这个注解，替换得太早会炸在导入上
    # （报的还是 "unsupported operand type(s) for |"，跟密钥半毛钱关系没有，最难查）。
    import app.main  # noqa: F401
    old = _client.DeepSeekClient
    if client_cls is not None:
        # 换的是**类**：网关内部走的是 `DeepSeekClient()`，塞实例进去会变成
        # "实例不可调用"，异常在 try 之外抛出，账本一行都不记 —— 看着像"没泄密也没记账"，
        # 其实是整条路径没跑到（B0/D1 就是为堵这个而写的）。
        _client.DeepSeekClient = client_cls
    try:
        with sandbox() as tmp:
            if server_key:
                from app.store import set_settings

                set_settings({"api_key": server_key, "base_url": "http://srv/v1",
                              "model": "srv-model"})
            del seen[:]
            pre = _usage_count(tmp)
            body = {"message": MSG, "session_id": "p04-probe", "module": "planner"}
            if key:
                body["model_config"] = {"base_url": BASE_URL, "model": "probe-model",
                                        "api_key": key}
            r = make_client().post("/api/chat", json=body)
            return (r.status_code, r.text, _scan(tmp, (CANARY, OTHER)),
                    [s.get("api_key") for s in seen], _usage_rows(tmp)[pre:])
    finally:
        _client.DeepSeekClient = old


def _usage_rows(tmp):
    p = os.path.join(tmp, "llm_usage.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in io.open(p, encoding="utf-8") if x.strip()]


def _usage_count(tmp):
    return len(_usage_rows(tmp))


def _scan(root, needles):
    hits = []
    for r, _dirs, files in os.walk(root):
        for fn in files:
            try:
                data = io.open(os.path.join(r, fn), "rb").read()
            except Exception:
                continue
            for n in needles:
                if n.encode() in data:
                    hits.append(os.path.relpath(os.path.join(r, fn), root))
    return hits


def _read(*parts):
    return io.open(os.path.join(PROJ_DIR, *parts), encoding="utf-8").read()


# ============ A 批：文案不许再说谎，链路本身也不许偷偷变 ============

def batch_words(ck):
    title("A. 文案与链路（读源码）")
    stu = _read("app", "static", "student.html")
    bad = ["不会上传服务器", "仅存本机浏览器", "密钥仅保存", "（仅存本机）"]
    ck.check("A1 前端不再出现「不会上传 / 仅存本机」这类说法",
             not [w for w in bad if w in stu],
             "｜还在：" + ", ".join(w for w in bad if w in stu))
    ck.check("A2 新文案把「每轮对话都会上传」说清楚（不是含糊成「保存在本地」）",
             "每次对话都会把它发给服务器" in stu and "不落盘" in stu)
    ck.check("A3 新文案还提醒了明文 HTTP 的风险（这是真风险，别只写半句）",
             "明文 HTTP" in stu or "抓包" in stu)
    srv = _read("app", "main.py") + _read("app", "config.py")
    ck.check("A4 服务端注释一起改口（谎话最初就是从这儿长到前端的）",
             "仅存本机浏览器" not in srv,
             "｜config/main 里仍写着「仅存本机浏览器」")
    ck.check("A5 链路没被偷偷改掉：/api/chat 仍读 body.model_config 当 override",
             'body.get("model_config")' in srv)
    ck.check("A6 前端仍把整份 {base_url,model,api_key} 随对话上传（文案的前提）",
             "body.model_config = mc" in stu and "if (mc && mc.api_key)" in stu)


# ============ B 批：灌 canary，扫沙箱（跑出来的，不是读出来的） ============

def batch_no_store(ck):
    title("B. canary Key 不落盘（真打接口）")
    st, text, hits, keys, rows = chat_once(Recorder, CANARY)
    ck.check("B0 这一轮真的走到了模型（不然 B1~B5 全是空断言）",
             bool(keys), f"HTTP {st} · 模型被调 {len(keys)} 次")
    ck.check("B1 canary 确实上传到了服务端（网关收到的就是它）",
             all(k == CANARY for k in keys))
    ck.check("B2 沙箱数据目录逐文件扫过：没有 canary", not hits, "｜命中：" + ", ".join(hits))
    ck.check("B3 响应体不回显 canary（前端拿到什么不重要，重要的是别把它抄进历史）",
             CANARY not in text)
    ck.check("B4 账本本次有新增行（说明这条链路真的记了账，B5 不是空过）",
             len(rows) > 0, f"新增 {len(rows)} 行")
    ALLOWED_FIELDS = {"ts", "user", "model", "prompt_tokens", "completion_tokens",
                      "total_tokens", "status", "latency_ms", "error"}
    extra = sorted({k for r in rows for k in r} - ALLOWED_FIELDS)
    ck.check("B5 账本行的字段集合没有越界（多一个字段就是多一个泄密面）",
             not extra, "｜多出：" + ", ".join(extra) + "｜现有：" +
             ", ".join(sorted({k for r in rows for k in r})))
    ck.check("B6 账本行的**值**里也不含 canary（字段名干净不等于内容干净）",
             not [r for r in rows if CANARY in json.dumps(r, ensure_ascii=False)])
    ok7, why7 = _failsafe_clean()
    ck.check("B7 失败路径也不落盘：不打桩、真去连一个关着的端口，异常文本进账本那条路扫过",
             ok7, why7)


def _failsafe_clean():
    """B7：不打桩，让 httpx 真的连不上 → 异常文本进 error 字段，那条路最容易漏。"""
    st, text, hits, _keys, rows = chat_once(None, CANARY)
    if not rows:
        return False, f"HTTP {st}，但账本没新增行（这条没跑到东西）"
    if CANARY in text:
        return False, "响应体回显了 canary"
    if hits:
        return False, "落盘命中：" + ", ".join(hits)
    if any(CANARY in json.dumps(r, ensure_ascii=False) for r in rows):
        return False, "error 字段里带着 canary"
    return True, f"{len(rows)} 行 error 已扫，只留下 {BASE_URL}（诊断信息，允许）"


# ============ C 批：_redact 本体（先抹再截） ============

def batch_redact(ck):
    title("C. 异常文本里的密钥先隐去再截断")
    from app.llm.gateway import _redact

    ck.check("C1 函数级：异常文本内嵌 key → 抹成占位串",
             CANARY not in _redact("Authorization: Bearer " + CANARY, CANARY)
             and "密钥已隐去" in _redact("Authorization: Bearer " + CANARY, CANARY))
    in_win = _redact("y" * 100 + " Bearer " + CANARY, CANARY)
    ck.check("C2 密钥完整落在 300 窗口**以内**时也抹掉了（证明不是靠截断蒙对的）",
             CANARY not in in_win and "密钥已隐去" in in_win, f"｜长度 {len(in_win)}")
    # 让密钥**正好跨过 300 边界**（前面垫 272 个字符 + " Bearer "，key 从第 280 个开始）：
    # 垫少了 key 整个在窗口内（C2 已覆盖），垫多了截断直接把它切没 ——
    # 两种都验不出"先抹再截"这个顺序，只有跨边界那一种，先截后抹会漏出前半截 Key。
    straddle = _redact("z" * 272 + " Bearer " + CANARY, CANARY)
    ck.check("C2b 先抹再截：密钥跨 300 边界时不留下任何前缀（先截后抹就会漏出半截 Key）",
             CANARY[:8] not in straddle and len(straddle) <= 300,
             "｜尾巴：" + repr(straddle[-24:]))
    plain = "连不上大模型服务（http://x/v1），All connection attempts failed"
    ck.check("C3 不含密钥的普通错误文本原样保留（不许把诊断信息也吃了）",
             _redact(plain, CANARY) == plain)
    ck.check("C4 base_url **允许**留在错误里（管理员要靠它判断学生连的是哪个地址）",
             BASE_URL in _redact("连不上 " + BASE_URL, CANARY, None))
    ck.check("C5 太短的串（<8 字符）不当密钥抹，免得把正常词吃掉",
             "abc123" in _redact("错误里出现 abc123 这个词", "abc123"))
    # 端到端那条：让假客户端抛"带 key 的异常"，看落到账本里的是什么
    st, _t, hits, _k, rows = chat_once(Exploder, CANARY)
    ck.check("C6 端到端：假客户端抛「异常文本里带着 key」之后，账本 error 里没有 key",
             bool(rows) and not any(CANARY in (r.get("error") or "") for r in rows),
             f"新增 {len(rows)} 行")
    ck.check("C7 端到端：抹掉的痕迹留在了 error 里（不是整条被清空）",
             any("密钥已隐去" in (r.get("error") or "") for r in rows),
             "｜样例：" + repr((rows[0].get("error") if rows else ""))[:120])
    ck.check("C8 端到端：canary 仍然没落进任何文件", not hits, "｜命中：" + ", ".join(hits))


# ============ D 批：两个学生不串号 ============

def batch_isolation(ck):
    title("D. 各用各的 Key，不串")
    _st, _t, hits_a, keys_a, _r = chat_once(Recorder, CANARY)
    _st2, _t2, hits_b, keys_b, _r2 = chat_once(Recorder, OTHER)
    ck.check("D1 A 那轮的网关只看到 A 的 key", bool(keys_a) and all(k == CANARY for k in keys_a),
             f"{len(keys_a)} 次")
    ck.check("D2 B 那轮的网关只看到 B 的 key（没有继承上一轮）",
             bool(keys_b) and all(k == OTHER for k in keys_b), f"{len(keys_b)} 次")
    _st, _t, hits, keys, _rows = _no_override_run()
    ck.check("D3 不带 model_config 的一轮：模型确实被调到了（服务器那把生效）",
             bool(keys), f"{len(keys)} 次")
    ck.check("D4 那一轮的 override 里没有前两个人的 key（也没把服务器 key 当学生 key 传下去）",
             all(k not in (CANARY, OTHER) for k in keys),
             "｜看到：" + ", ".join(str(k)[-6:] for k in keys))
    ck.check("D5 三轮的 canary 都没落盘", not hits_a and not hits_b and not hits,
             "｜命中：" + ", ".join(set(hits_a) | set(hits_b) | set(hits)))


def _no_override_run():
    """学生没带 Key 的那一轮：先把服务器密钥配上，否则 planner 走离线 Mock、模型一次都不调。"""
    return chat_once(Recorder, None, server_key=SERVER_KEY)


# ============ E 批：管理侧那把（伪脱敏的另一面） ============

def batch_admin_mask(ck):
    title("E. 管理侧那把（伪脱敏的另一面：打码串被原样存回去）")
    from app.main import _mask

    m = _mask(SERVER_KEY)
    ck.check("E1 打码串不含完整原 key，且带星号", SERVER_KEY not in m and "*" in m, f"→ {m}")

    with sandbox():
        from app.store import get_settings, set_settings

        set_settings({"api_key": SERVER_KEY, "base_url": "http://srv/v1", "model": "srv-model"})
        cli = make_client()
        shown = cli.get("/api/admin/settings").json().get("settings", {}).get("api_key", "")
        ck.check("E2 GET /api/admin/settings 回的是打码串，不是文件里的明文",
                 SERVER_KEY not in shown and "*" in shown, f"→ {shown}")
        cli.post("/api/admin/settings", json={"api_key": shown, "model": "another-model"})
        ck.check("E3 把打码串原样 POST 回去**不会**把真 key 覆盖成星号（只改 model 不该毁掉 key）",
                 get_settings().get("api_key") == SERVER_KEY,
                 "→ 现在存的是：" + str(get_settings().get("api_key"))[:16])
        ck.check("E4 同一次保存里 model 确实改了（防呆不该把整个 patch 一起丢掉）",
                 get_settings().get("model") == "another-model")
        cli.post("/api/admin/settings", json={"api_key": "sk-brand-new-key-9999"})
        ck.check("E5 带真 key（不含星号）时正常覆盖",
                 get_settings().get("api_key") == "sk-brand-new-key-9999")
        # 与学生的"不落盘"对照：管理员这把**是按设计落盘的**（settings.json，.gitignore 内）。
        # 这条不是夸它好，是钉住"两套语义别被后人一刀切"：把管理侧也改成不落盘，
        # 后台配完重启就丢；把学生侧改成落盘，就是别人的 Key 被人家用掉。
        cfg_txt = io.open(os.path.join(os.environ["CAMPUSTIME_DATA_DIR"], "settings.json"),
                          encoding="utf-8").read()
        ck.check("E6 管理侧 key 落在 settings.json（gitignore 内），与学生侧「用完即弃」是两套语义",
                 "sk-brand-new-key-9999" in cfg_txt)
        ig = _read(".gitignore")
        ck.check("E7 那份文件确实在 .gitignore 里（否则 E6 就变成了「把 key 提交上去」）",
                 "app/data/settings.json" in ig)


def main():
    ck = Checker()
    batch_words(ck)
    batch_no_store(ck)
    batch_redact(ck)
    batch_isolation(ck)
    batch_admin_mask(ck)
    return ck.summary("test_34 学生自带 Key 的处理")


if __name__ == "__main__":
    sys.exit(main())
