"""base_url 的 SSRF 闸门：请求方可以自带连接目标，但**必须真过闸**，且不外泄地址。

起因（2026-10-09 审计）：`/api/chat` 与 `/api/classify` 都接受请求体里的
`model_config.base_url`，而 `llm/client.py` 把它直接拼成 POST 目标 —— 实测
填 `http://127.0.0.1:8000/api/admin/settings` 就能让服务器自己去打自己的内网端口，
填 `http://169.254.169.254/...` 在云上就够得到元数据服务，连不上时错误文本还会把
完整 URL 回显给请求方。带出去的 key 是请求者自己的，所以这洞的本质不是"密钥泄露"，
是**这台服务器被当成任意内网 HTTP 客户端**。

⚠️ 同日改过一次口，别再改回去：中午那版是"彻底不收请求方的 base_url"，
安全但把"学生自带 Key"做成了假功能（Key 是他的、地址是管理端的，两家不配套只会
吃一个看不懂的 401，实测把人逼到以为数据丢了）。现在收，但连接前那道闸对他一样。
所以本文件钉的重点从"字段有没有被丢掉"变成"**内网到底连不连得上**"——
丢字段是一种假的安全，拦连接才是。

本文件钉的是修完之后必须成立的七件事：
  A 校验函数本身（协议 / 内网 IP 字面量 / 保留域名 / 单标签名 / IPv6 回环）；
  B 两个入口都只留 {key, model, base_url}，多余字段不带过来；地址过不了第一层就整个作废；
  C 管理端写入也过同一道闸（这里填错会把**管理员的 key** 寄出去，比学生端严重）；
  D 回给请求方的错误文本不反射地址（完整原因只进服务端日志）；
  E 学生端能填地址、也能看见自己的 key 会被送到哪台机器；
  F 端到端：settings.json 里真躺着内网地址时（模拟闸门上线前的存量配置），
    连接前那一档要拦下，且响应里不出现那个地址；
  H 端到端（学生侧）：请求体里塞内网地址 → 一样拦下、不回显、不落盘他的 key；
    塞公网地址 → 真发到他的地址（用假 transport 取证，不碰网）。
"""
import io
import os

from _harness import Checker, make_client, sandbox, title
from app.store import set_settings

MAIN = "app/main.py"
CLIENT = "app/llm/client.py"
STUDENT = "app/static/student.html"


def batch_a_validator():
    c = Checker()
    title("A 批 · validate_base_url：该拦的拦住，该放的别误伤")
    from app.config import BaseUrlError, validate_base_url

    blocked = [
        "http://127.0.0.1:8000/api/admin/settings",   # 回环（实测那条）
        "http://localhost:11434/v1",                  # 本地 ollama
        "http://169.254.169.254/latest/meta-data",    # 云元数据
        "http://10.0.0.5/v1", "http://192.168.1.1/v1", "http://172.16.9.9/v1",
        "http://0.0.0.0/v1", "http://[::1]:80/v1",    # IPv6 回环
        "file:///c:/windows/win.ini", "ftp://x.example/y", "gopher://x/1",
        "http://metadata/v1", "http://nfs.internal/v1", "http://x.local/v1",
        "", "not a url",
    ]
    for u in blocked:
        try:
            got = validate_base_url(u)
            c.check(f"该拦：{u!r}", False, f"竟然放行了 → {got}")
        except BaseUrlError as e:
            c.check(f"该拦：{u!r}", True, str(e)[:60])

    for u in ["https://api.deepseek.com", "https://api.deepseek.com/", "https://open.bigmodel.cn/api/paas/v4"]:
        try:
            got = validate_base_url(u)
            c.check(f"该放：{u!r}", got.startswith("https://"), got)
        except BaseUrlError as e:
            c.check(f"该放：{u!r}", False, f"被误拦：{e}")

    # 尾部点（FQDN 写法）不该被当成另一个主机
    c.check("尾部点不影响放行（api.deepseek.com. 是同一个主机）",
            validate_base_url("https://api.deepseek.com.") == "https://api.deepseek.com.")
    # 校验失败必须抛，不能静默回落默认值（静默回落 = 管理员以为在用自定义端点）
    from app.config import BaseUrlError as _E
    raised = False
    try:
        validate_base_url("http://127.0.0.1:9")
    except _E:
        raised = True
    c.check("非法地址抛错而不是悄悄回落", raised)
    return c


def batch_b_entry_points():
    c = Checker()
    title("B 批 · 两个入口都只留 {key, model, base_url}，多余的字段不带过来")
    # ⚠️ 2026-10-09 这一批改过一次口。原先钉的是"override 的 base_url 被丢掉"，
    # 那让"学生自带 Key"变成假功能（Key 是他的、地址是管理端的，不配套就吃 401）。
    # 现在的契约：地址**收下**，但内网那一层在 client.py 真要发请求之前判 ——
    # 拦得住内网这件事由 F 批和 H 批用真执行来证，不是靠这里删字段来证。
    from app.config import get_llm_config, sanitize_override

    o = sanitize_override({"base_url": "http://127.0.0.1:8000", "model": "glm-4-flash",
                           "api_key": " sk-test-1 ", "evil_field": "x"})
    c.check("sanitize_override 收 base_url、trim key、保留 model、多余字段丢掉",
            o == {"api_key": "sk-test-1", "model": "glm-4-flash",
                  "base_url": "http://127.0.0.1:8000"}, str(o))
    c.check("地址缺协议按 https 补全（人都是这么省略的）",
            sanitize_override({"api_key": "k", "base_url": "api.deepseek.com"})["base_url"]
            == "https://api.deepseek.com")
    c.check("地址过不了第一层（file://）→ 整个 override 作废，不留半成品",
            sanitize_override({"api_key": "sk-x", "base_url": "file:///c:/windows/win.ini"}) is None)
    c.check("没带 key 的 override 整个作废（不留下半成品）",
            sanitize_override({"base_url": "http://x.example"}) is None)
    c.check("垃圾输入不炸（字符串/None/列表）",
            sanitize_override("x") is None and sanitize_override(None) is None
            and sanitize_override([]) is None)
    c.check("只有空串 key 也算没带", sanitize_override({"api_key": "   "}) is None)

    # 生效配置的三层优先级：学生的地址 > 管理后台 > .env；他没填的那项回落
    with sandbox():
        set_settings({"api_key": "sk-admin-fake", "base_url": "https://api.deepseek.com",
                      "model": "deepseek-chat"})
        cfg = get_llm_config(override={"api_key": "sk-student",
                                       "base_url": "http://127.0.0.1:8000"})
        c.check("override 的 base_url 会进到生效配置（内网由连接前那道闸拦，不在这层）",
                cfg["base_url"] == "http://127.0.0.1:8000", cfg["base_url"])
        c.check("但学生的 key 与模型名照旧生效",
                cfg["api_key"] == "sk-student", cfg["api_key"])
        c.check("学生只填 key 不填地址时，地址回落服务器配置",
                get_llm_config(override={"api_key": "sk-student"})["base_url"]
                == "https://api.deepseek.com")
        c.check("source 说的是「这笔账花的是谁的 Key」，不再谎称地址来自服务器",
                cfg["source"] == "学生端自定义 API", cfg["source"])

    src = io.open(MAIN, encoding="utf-8").read()
    c.check("main.py 里两处入口过的都是 sanitize_override",
            src.count("sanitize_override(body.get(") == 2,
            f"实际 {src.count('sanitize_override(body.get(')} 处")
    c.check("没有残留把请求体 base_url 直接当 override 的写法",
            'body.get("model_config") or None' not in src)
    return c


def batch_c_admin_write():
    c = Checker()
    title("C 批 · 管理端写入同一道闸（填错会把管理员的 key 寄出去）")
    with sandbox():
        cli = make_client(admin_token="t37-token")
        r = cli.post("/api/admin/settings", json={"base_url": "http://127.0.0.1:8000/x"})
        c.check("管理端存内网地址 → 400", r.status_code == 400, f"{r.status_code} {r.text[:80]}")
        c.check("400 里给的是能照着改的一句话",
                "接口地址" in r.text, r.text[:100])
        after = cli.get("/api/admin/settings").json().get("settings", {})
        c.check("非法值没有落盘", "127.0.0.1" not in str(after), str(after)[:100])
        ok = cli.post("/api/admin/settings", json={"base_url": "https://api.deepseek.com"})
        c.check("合法地址仍然存得进去（闸门别把正常配置也拦了）",
                ok.status_code == 200, ok.text[:80])
    return c


def batch_d_no_reflection():
    c = Checker()
    title("D 批 · 回给请求方的错误不反射地址")
    src = io.open(CLIENT, encoding="utf-8").read()
    c.check("connect 超时单独收短（探测的代价主要在连不上时等多久）",
            "httpx.Timeout(30.0, connect=3.0)" in src, "没找到拆分后的超时")
    c.check("HTTPStatusError 的分支不再把 httpx 的原文（含 URL）拼进消息",
            '大模型接口出错（HTTP {code}）：{e}' not in src)
    c.check("RequestError 分支不出现 {base_url}",
            'raise LLMError("连不上大模型服务' in src and "（{base_url}）" not in src)
    c.check("完整原因走服务端日志", 'print(f"[LLM] 连不上上游' in src)
    c.check("上游返回不是模型应答时不再裸 KeyError → 500",
            'data["choices"][0]["message"]' in src and "except (TypeError, KeyError, IndexError" in src)
    return c


def batch_e_upstream_visible():
    c = Checker()
    title("E 批 · 学生既能自己填地址，也看得见 key 会被送到哪台机器")
    with sandbox():
        set_settings({"api_key": "sk-admin-fake", "base_url": "https://api.deepseek.com/v1"})
        d = make_client().get("/api/upstream").json()
        c.check("接口只回主机名（不整串地址回给任意请求方）",
                d.get("host") == "api.deepseek.com", str(d))
        c.check("不含 key、不含路径", "sk-" not in str(d) and "/v1" not in str(d), str(d))
    src = io.open(STUDENT, encoding="utf-8").read()
    c.check("API 弹窗里有「接口地址」输入框（自带端点这条路是真通的，不是假的）",
            'id="mBase"' in src)
    c.check("保存时把 base_url 一起写回本地（只存 key 会让地址永远为空）",
            'base_url:document.getElementById("mBase").value.trim()' in src)
    c.check("文案明说内网/链路本地地址会被拒（把闸门的边界讲给学生听，别让他瞎试）",
            "169.254" in src and "会被直接拒掉" in src)
    c.check("服务器默认上游仍然显示出来（留空=用它）", "/api/upstream" in src and "mUpstream" in src)
    c.check("设置页显示「当前自定义发到哪个 host」——不显示就是替他把钥匙寄出去",
            src.count("cfg.base_url") >= 2 and "发到 " in src)
    c.check("不许再留「学生端不能改」这种已经不成立的文案",
            "学生端不能改" not in src)
    return c


def batch_f_end_to_end():
    c = Checker()
    title("F 批 · 端到端：存量内网地址也拦得住，且不回显地址")
    with sandbox():
        # 模拟"闸门上线前 settings.json 里已经躺着内网地址"（绕过 C 批的写入口）
        set_settings({"api_key": "sk-legacy", "base_url": "http://127.0.0.1:9/v1",
                      "model": "m"})
        cli = make_client()
        r = cli.post("/api/chat", json={"message": "你好呀", "session_id": "t37-f",
                                        "module": "chat"})
        body = str(r.json())
        c.check("接口没被打挂（200 + 一句人话，不是裸 500）",
                r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        c.check("连接前的闸门拦下了内网地址（必须是闸门那句，不是泛泛的「连不上」）",
                "不合规" in body, body[:120])
        c.check("响应里不出现那个内网地址",
                "127.0.0.1" not in body and ":9" not in body, body[:120])
    return c


def batch_g_two_tiers():
    c = Checker()
    title("G 批 · 两层校验：取配置那层不许把可用配置弄崩")
    from app.config import (BaseUrlError, check_base_url_shape, check_base_url_ssrf,
                            get_llm_config)

    # 第一层只管"能不能发请求"：localhost 是能发的
    c.check("第一层放行 http://localhost:11434/v1（本地 ollama 是合法配法）",
            check_base_url_shape("http://localhost:11434/v1") == "http://localhost:11434/v1")
    c.check("第一层仍拦 file://", _raises(check_base_url_shape, "file:///c:/x"))
    # 第二层才是 SSRF 那档
    for u in ["http://localhost:11434/v1", "http://127.0.0.1:8000", "http://[::1]/v1",
              "http://srv/v1"]:
        c.check(f"第二层拦 {u!r}", _raises(check_base_url_ssrf, u))
    c.check("开关打开后放行内网（CAMPUSTIME_LLM_ALLOW_PRIVATE）",
            check_base_url_ssrf("http://localhost:11434/v1", allow_private=True)
            == "http://localhost:11434/v1")

    # 最关键的一条：**取配置**这条路径不能被内网地址炸掉。
    # 真踩过：第一版把第二层放进 get_llm_config，结果 test_34（settings 里放着
    # http://srv/v1）每次调用都抛 BaseUrlError —— 安全做成了可用性事故。
    with sandbox():
        set_settings({"api_key": "sk-x", "base_url": "http://localhost:11434/v1", "model": "m"})
        try:
            cfg = get_llm_config()
            ok, err = cfg["base_url"] == "http://localhost:11434/v1", ""
        except BaseUrlError as e:
            ok, err = False, str(e)
        c.check("get_llm_config 读到内网地址不抛错（留给连接前那一层拦）", ok, err)
    return c


def batch_h_override_route():
    c = Checker()
    title("H 批 · 学生自带地址：内网真拦得住，公网真发得出去")
    # 这一批是这次改口的**正证**。B 批只证明"字段收下了"，那不等于安全；
    # 安全与否只能看"请求实际发到哪儿"，所以这里塞一个假的 httpx 把目标地址取出来。
    # 假 transport 同时是空断言防护：闸门被人删掉时，这里会记录到内网 URL → 立刻红，
    # 而不是"测试通过但洞还在"。
    import asyncio

    import app.llm.client as client_mod
    from app.llm.client import DeepSeekClient, LLMError

    sent = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"total_tokens": 7}}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None):
            sent["url"] = url
            sent["auth"] = (headers or {}).get("Authorization", "")
            return _Resp()

    real = client_mod.httpx.AsyncClient
    client_mod.httpx.AsyncClient = _FakeAsyncClient
    try:
        with sandbox():
            set_settings({"api_key": "sk-admin-fake",
                          "base_url": "https://open.bigmodel.cn/api/paas/v4",
                          "model": "glm-4-flash"})
            # ① 公网：必须发到学生填的那家，用学生的 key
            sent.clear()
            cli_pub = DeepSeekClient()
            asyncio.run(cli_pub.chat(
                [{"role": "user", "content": "x"}],
                override={"api_key": "sk-student-pub",
                          "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}))
            c.check("学生填的公网地址=实际请求地址（自带端点不是假的）",
                    str(sent.get("url", "")).startswith("https://api.deepseek.com/chat/completions"),
                    str(sent.get("url")))
            c.check("花的是他的 key，不是管理员那把",
                    sent.get("auth", "").endswith("sk-student-pub"), sent.get("auth"))
            c.check("模型名也是他填的（不是管理端那个 glm-4-flash）",
                    cli_pub.last_model == "deepseek-chat", str(cli_pub.last_model))

            # ② 内网：闸门要在**建连接之前**拦掉，所以假 client 一次都不该被调到
            sent.clear()
            msg = ""
            try:
                asyncio.run(DeepSeekClient().chat(
                    [{"role": "user", "content": "x"}],
                    override={"api_key": "sk-student-evil",
                              "base_url": "http://127.0.0.1:8000/api/admin/settings"}))
                msg = "竟然发出去了"
            except LLMError as e:
                msg = str(e)
            c.check("override 塞内网地址 → 请求根本没发出（不是发了再报错）",
                    "url" not in sent, f"假 client 记录到：{sent.get('url')}")
            c.check("给的是闸门那句（不是泛泛的「连不上」）", "不合规" in msg, msg[:100])
            c.check("错误文本不回显内网地址", "127.0.0.1" not in msg, msg[:100])

            # ③ 域名解析不到的地址也不该被当成"连不上就算了"放过去
            sent.clear()
            try:
                asyncio.run(DeepSeekClient().chat(
                    [{"role": "user", "content": "x"}],
                    override={"api_key": "sk-x", "base_url": "https://no-such-host.invalid/v1"}))
                out = "竟然发出去了"
            except LLMError as e:
                out = str(e)
            c.check("解析不了的域名同样不发（闸门那句，且不含主机名）",
                    "不合规" in out and "no-such-host" not in out, out[:100])
    finally:
        client_mod.httpx.AsyncClient = real

    # ④ 端到端 + 落盘检查：canary key 不许出现在任何文件里
    canary = "sk-CANARY-H37-8f2b"
    with sandbox():
        set_settings({"api_key": "sk-admin-fake", "base_url": "https://api.deepseek.com",
                      "model": "deepseek-chat"})
        cli = make_client()
        r = cli.post("/api/chat", json={"message": "你好呀", "session_id": "t37-h",
                                        "module": "chat",
                                        "model_config": {"api_key": canary,
                                                         "base_url": "http://127.0.0.1:9/v1"}})
        body = str(r.json())
        c.check("走学生自带地址时接口没被打挂（200 + 一句人话）",
                r.status_code == 200, f"{r.status_code} {r.text[:80]}")
        c.check("响应里出现的是闸门那句", "不合规" in body, body[:120])
        c.check("响应不反射内网地址", "127.0.0.1" not in body and ":9" not in body, body[:120])

        # 落盘面：沙箱里**每一个**文件都扫一遍（只扫 llm_usage 会漏进会话/消息那种地方）
        root = os.environ["CAMPUSTIME_DATA_DIR"]
        dirty, usage_lines = [], ""
        for dir_, _dirs, files in os.walk(root):
            for fn in files:
                try:
                    txt = io.open(os.path.join(dir_, fn), encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if canary in txt:
                    dirty.append(fn)
                if fn == "llm_usage.jsonl":
                    usage_lines += txt
        c.check("沙箱里没有任何文件留下他的 key", not dirty, f"出现在：{dirty}")
        c.check("响应体里也没回显他的 key", canary not in body, body[:120])
        c.check("流水记下了来源=student（管理员能看见这笔账花的是谁的 Key）",
                '"key_source": "student"' in usage_lines, usage_lines[-160:])
    return c


def _raises(fn, *a):
    try:
        fn(*a)
        return False
    except Exception:
        return True


def main():
    results = [batch_a_validator(), batch_b_entry_points(), batch_c_admin_write(),
               batch_d_no_reflection(), batch_e_upstream_visible(), batch_f_end_to_end(),
               batch_g_two_tiers(), batch_h_override_route()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== base_url SSRF 闸门总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
