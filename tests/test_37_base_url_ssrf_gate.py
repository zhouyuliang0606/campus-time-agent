"""base_url 的 SSRF 闸门：连接目标不许由请求方决定。

起因（2026-10-09 审计）：`/api/chat` 与 `/api/classify` 都接受请求体里的
`model_config.base_url`，而 `llm/client.py` 把它直接拼成 POST 目标 —— 实测
填 `http://127.0.0.1:8000/api/admin/settings` 就能让服务器自己去打自己的内网端口，
填 `http://169.254.169.254/...` 在云上就够得到元数据服务，连不上时错误文本还会把
完整 URL 回显给请求方。带出去的 key 是请求者自己的，所以这洞的本质不是"密钥泄露"，
是**这台服务器被当成任意内网 HTTP 客户端**。

本文件钉的是修完之后必须成立的六件事：
  A 校验函数本身（协议 / 内网 IP 字面量 / 保留域名 / 单标签名 / IPv6 回环）；
  B 两个入口都不再接受 base_url（**漏一个等于没关**，所以两处都钉）；
  C 管理端写入也过同一道闸（这里填错会把**管理员的 key** 寄出去，比学生端严重）；
  D 回给请求方的错误文本不反射地址（完整原因只进服务端日志）；
  E 学生端能看到"key 会被送到哪台机器"（关掉自带端点之后的诚实义务）；
  F 端到端：settings.json 里真躺着内网地址时（模拟闸门上线前的存量配置），
    连接前那一档要拦下，且响应里不出现那个地址。

⚠️ 空断言防护：每条都先确认"真的走到了这一步"，再核字段。
"""
import io

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
    title("B 批 · 两个入口都不接受请求方的 base_url")
    from app.config import get_llm_config, sanitize_override

    o = sanitize_override({"base_url": "http://127.0.0.1:8000", "model": "glm-4-flash",
                           "api_key": " sk-test-1 "})
    c.check("sanitize_override 丢掉 base_url、trim key、保留 model",
            o == {"api_key": "sk-test-1", "model": "glm-4-flash"}, str(o))
    c.check("没带 key 的 override 整个作废（不留下半成品）",
            sanitize_override({"base_url": "http://x.example"}) is None)
    c.check("垃圾输入不炸（字符串/None/列表）",
            sanitize_override("x") is None and sanitize_override(None) is None
            and sanitize_override([]) is None)
    c.check("只有空串 key 也算没带", sanitize_override({"api_key": "   "}) is None)

    # 真正的判据：把内网地址塞进 override，生效的 base_url 必须是服务器侧那一个
    with sandbox():
        set_settings({"api_key": "sk-admin-fake", "base_url": "https://api.deepseek.com",
                      "model": "deepseek-chat"})
        cfg = get_llm_config(override={"api_key": "sk-student",
                                       "base_url": "http://127.0.0.1:8000"})
        c.check("override 里的 base_url 对最终结果毫无影响",
                cfg["base_url"] == "https://api.deepseek.com", cfg["base_url"])
        c.check("但学生的 key 与模型名照旧生效",
                cfg["api_key"] == "sk-student", cfg["api_key"])
        c.check("source 里说清了地址来自服务器配置",
                "地址走服务器配置" in cfg["source"], cfg["source"])

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
    title("E 批 · 学生看得见「我的 key 会被送到哪台机器」")
    with sandbox():
        set_settings({"api_key": "sk-admin-fake", "base_url": "https://api.deepseek.com/v1"})
        d = make_client().get("/api/upstream").json()
        c.check("只回主机名", d.get("host") == "api.deepseek.com", str(d))
        c.check("不含 key、不含路径", "sk-" not in str(d) and "/v1" not in str(d), str(d))
    src = io.open(STUDENT, encoding="utf-8").read()
    c.check("学生端 API 弹窗里已经没有 Base URL 输入框", 'id="mBase"' not in src)
    c.check("弹窗改成显示上游主机", "/api/upstream" in src and "mUpstream" in src)
    c.check("文案明说 key 会送到那个上游、且学生改不了",
            "你的 Key 会被送到上面那个上游地址" in src)
    c.check("旧记录里残留的 base_url 不再被显示成「还在生效」",
            "cfg.base_url" not in src)
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


def _raises(fn, *a):
    try:
        fn(*a)
        return False
    except Exception:
        return True


def main():
    results = [batch_a_validator(), batch_b_entry_points(), batch_c_admin_write(),
               batch_d_no_reflection(), batch_e_upstream_visible(), batch_f_end_to_end(),
               batch_g_two_tiers()]
    total_pass = sum(r.passed for r in results)
    total_fail = sum(r.failed for r in results)
    print(f"\n===== base_url SSRF 闸门总账：通过 {total_pass} 项，失败 {total_fail} 项 =====")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
