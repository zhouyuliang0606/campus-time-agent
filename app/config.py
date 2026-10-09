"""配置模块（人话：统一从 .env 读取密钥和开关，全项目都来这里拿，代码里不写死任何密码）。"""
import ipaddress
import os
import re
import socket
import time
from urllib.parse import urlsplit

from dotenv import load_dotenv

# load_dotenv() 会读取项目根目录下的 .env 文件，把里面的内容变成环境变量
# 这样我们下面用 os.getenv 就能拿到密钥，而密钥本身不在代码里
load_dotenv()

# —— DeepSeek 大模型相关配置 ——
# 你的 API Key：从环境变量读。如果没填，就是空字符串（调用时会报错提示你去填）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
# 接口地址，DeepSeek 兼容 OpenAI 格式，默认就是这个
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# 使用的模型名，deepseek-chat 是通用 V3 模型
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# —— 调试开关 ——
# DEBUG=true 时，Agent 的"思考轨迹"会打印出来，方便演示和排查问题
DEBUG = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

# —— 管理接口口令 ——
# 全项目只在这里拿，admin_guard 负责校验；代码里没有任何写死的口令。
ADMIN_TOKEN_ENV = "CAMPUSTIME_ADMIN_TOKEN"


def get_admin_token() -> str:
    """管理接口的口令（人话：从 .env / 环境变量读，没配就是空串 = 管理接口全锁）。

    为什么每次现读而不是像上面那样定成常量：
      · 测试要在同一个进程里换口令、试"没配口令"那条路（改了 .env 得重启才生效很难用）；
      · 读一次环境变量的开销可以忽略，但"没配 = 不开门"这个默认值必须随时算得出来。
    """
    return (os.getenv(ADMIN_TOKEN_ENV) or "").strip()


# ============ base_url 的 SSRF 闸门（全项目唯一一处判"能不能连这里"）============
#
# 为什么要这么一道闸：`base_url` 决定的是**这台服务器往哪儿发 HTTP 请求**。
# 2026-10-09 实测过：不设限的时候，请求方填 `http://127.0.0.1:8000/api/admin/settings`
# 就能让服务器自己去打自己的内网端口，填 `http://169.254.169.254/...` 就能在云上够元数据服务；
# 而且连不上时的错误文本会把完整 URL 回显给请求方 —— 状态码 + 时延 + 回显，
# 合起来就是一台内网探测器。带出去的 key 是请求者自己的，所以这洞的本质不是"密钥泄露"，
# 是"服务器被当成任意内网 HTTP 客户端"。
#
# 三条设计选择，都是权衡后定的，别随手改：
#  ① **不合法就抛错，绝不静默回落默认值**。这个函数被二十来处调用，
#     悄悄回落会让管理员以为在用自定义端点、实际打到别处——比报错难查得多。
#  ② 学生端那条路已经彻底不收 base_url（见 main.py 的 sanitize_override），
#     所以这里实际管的是"管理后台填的"和".env 里的"。仍然要管：
#     管理端填错或被改的地址，会把**管理员的 key** 送出去，比学生那条严重。
#  ③ DNS 解析结果按 60 秒缓存。不缓存的话每次调用都要现解析（这函数调得勤），
#     而解析是在事件循环里做的同步动作。缓存的代价写在下面的已知残余风险里。
class BaseUrlError(ValueError):
    """这个地址不该连（协议不对 / 是内网 / 域名解析出来是内网）。"""


_ALLOWED_SCHEMES = ("http", "https")
# 内网专用的单标签名与保留后缀（`http://metadata/`、`http://nfs.internal/` 这类）
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost", ".localdomain", ".home.arpa")
# 不允许出现的网段：回环、私有、链路本地（含云元数据 169.254.169.254）、
# CGNAT、组播/保留、以及"这个网络"0/8。IPv6 同理。
_BLOCKED_NETS = tuple(ipaddress.ip_network(c) for c in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/29", "192.168.0.0/16", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4",
)) + tuple(ipaddress.ip_network(c) for c in (
    "::1/128", "fc00::/7", "fe80::/10", "::/128",
))
_IP_CACHE: dict[str, tuple[float, str]] = {}      # host -> (过期时间, 错误信息；"" = 放行)
_IP_CACHE_TTL = 60.0
_IPV6_ZONE_RE = re.compile(r"%")


def _ip_is_blocked(ip: str) -> bool:
    """这个 IP 是不是内网/保留地址。解析不出来的（怪格式）一律当危险。"""
    try:
        addr = ipaddress.ip_address(_IPV6_ZONE_RE.split(ip)[0].strip("[]"))
    except ValueError:
        return True
    return any(addr in net for net in _BLOCKED_NETS)


def _private_allowed() -> bool:
    """这台部署是不是**有意**在用内网/本地的模型服务。

    默认 False（拦）。要放开就设环境变量 `CAMPUSTIME_LLM_ALLOW_PRIVATE=1`。
    为什么必须有这个开关：这个产品是本地跑的演示，拿 ollama 起在
    `http://localhost:11434/v1` 是**最常见的一种合法配法**；
    一刀切拦内网会让这种部署每次对话直接炸，那不是安全，是把自己关在门外。
    """
    return (os.getenv("CAMPUSTIME_LLM_ALLOW_PRIVATE") or "").strip().lower() in ("1", "true", "yes")


def check_base_url_shape(url: str) -> str:
    """第一层：这地址**能不能用来发请求**（协议 + 有主机名）。不合法抛 BaseUrlError。

    这层可以放在 `get_llm_config()` 里判——它拦的是"根本没法用"的输入（`file://`、
    空串、没有主机名），不会误伤任何正常配置。
    """
    s = (url or "").strip()
    if not s:
        raise BaseUrlError("接口地址是空的")
    try:
        parts = urlsplit(s)
    except ValueError as e:
        raise BaseUrlError(f"接口地址没法解析：{e}") from e
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BaseUrlError(f"接口地址只允许 http/https，收到的是 {parts.scheme or '(没有协议)'}")
    if not parts.hostname:
        raise BaseUrlError("接口地址里没有主机名")
    return s.rstrip("/")


def check_base_url_ssrf(url: str, *, allow_private: bool | None = None, resolve: bool = True) -> str:
    """第二层：这个地址**该不该连**（内网 / 回环 / 链路本地 / 云元数据 / 保留域名）。

    只在两个地方用：管理端写入时（早报错，别存进去）、以及真要发请求之前（`llm/client.py`）。
    **不放进取配置的路径**——那里抛错会让存量配置或本地 ollama 直接 500，
    等于把安全做成了可用性事故。要放行内网走 `CAMPUSTIME_LLM_ALLOW_PRIVATE=1`。

    ⚠️ 已知残余风险（写清楚，别以为已经防全）：
      · **DNS rebinding 没完全防住**。这里是"先解析校验、再由 httpx 自己解析一次连接"，
        两次解析之间可以换答案（TOCTOU）。真要钉死得把已校验的 IP 固定进连接层
        （自定义 transport，或 host 换成 IP + 带 Host/SNI 头）。本轮不做：
        能配地址的只剩管理员本人，威胁模型已经从"任意请求方"缩到"管理员自己"。
      · 不挡非常规端口（`http://公网IP:6379/` 这类过闸），同上理由。
    """
    s = check_base_url_shape(url)
    parts = urlsplit(s)
    if allow_private is None:
        allow_private = _private_allowed()
    if allow_private:
        return s
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise BaseUrlError(f"接口地址指向内网保留域名：{host}")
    if "." not in host:
        raise BaseUrlError(f"接口地址得是个完整域名，收到的是单标签名：{host}")
    try:
        ipaddress.ip_address(host)
        is_ip = True
    except ValueError:
        is_ip = False
    if is_ip:
        if _ip_is_blocked(host):
            raise BaseUrlError(f"接口地址不能是内网/保留 IP：{host}")
        return s
    if not resolve:
        return s
    now = time.time()
    hit = _IP_CACHE.get(host)
    if not (hit and hit[0] > now):
        try:
            infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme.lower() == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
        except OSError as e:
            # 底层错误（e）只进日志、不进抛出的消息：这条最终可能回到请求方眼前。
            print(f"[base_url] 域名解析失败 {host}: {e}")
            err = f"接口地址的域名解析不了：{host}"
            _IP_CACHE[host] = (now + 10.0, err)
            raise BaseUrlError(err) from e
        ips = {i[4][0] for i in infos}
        bad = sorted(i for i in ips if _ip_is_blocked(i))
        _IP_CACHE[host] = (now + _IP_CACHE_TTL,
                           "" if not bad else f"接口地址的域名解析到了内网/保留 IP：{host} → {', '.join(bad)}")
        hit = _IP_CACHE[host]
    if hit[1]:
        raise BaseUrlError(hit[1])
    return s


# 兼容旧名字：以前只有一个入口，测试与调用方都叫它 validate_base_url。
def validate_base_url(url: str, *, resolve: bool = True) -> str:
    """= `check_base_url_ssrf`（两层都过）。"""
    return check_base_url_ssrf(url, resolve=resolve)


def sanitize_override(raw) -> dict | None:
    """把请求方递来的"自定义 API"收敛成**只可能影响 key 与模型名**的那部分。

    丢掉 `base_url` 是这个函数的全部意义：连到哪儿由服务器决定（见上面那段 SSRF 说明）。
    没带 api_key 的一律当没这回事——既不该生效，也不该被留在任何结构里。
    所有读请求体里 override 的地方都必须过这个函数，别在调用点各抄一份字段挑选。
    """
    if not isinstance(raw, dict) or not raw.get("api_key"):
        return None
    out: dict[str, str] = {"api_key": str(raw["api_key"]).strip()}
    if not out["api_key"]:
        return None
    model = str(raw.get("model") or "").strip()
    if model:
        out["model"] = model[:64]
    return out


def get_llm_config(override: dict | None = None) -> dict:
    """获取当前真正生效的大模型配置（人话：管理后台配了就听它的，没配就用 .env 的）。

    优先级：**连到哪儿（base_url）只由服务器侧决定**——管理后台的 settings.json > .env；
    学生端自定义的 override 只能决定**用哪把 key、用哪个模型名**。

    ⚠️ 2026-10-09 改：以前 override 里的 `base_url` 也生效（"学生自带端点"），
    那等于把"这台服务器往哪儿发 HTTP 请求"交给请求方 —— 实测能被用来打内网
    （SSRF，细节见 `validate_base_url` 上面那段）。现在这条路彻底关掉：
    **override 里的 base_url 看都不看**。入口层（main.sanitize_override）先丢一次，
    这里再丢一次，是为了将来有人新写一个调用方直接构造 override 时，闸门还在。

    带来的可见后果（必须让学生知道，所以学生端要把当前上游域名显示出来）：
    学生自带 key 时，这把 key 会被发到**管理端配的那个地址**。
    管理端配的是智谱、学生拿 DeepSeek 的 key，就会吃一个 401 —— 这是设计如此，
    不是 bug；反过来若不显示上游，就等于替学生把 key 寄到没跟他说过的那台机器。

    为什么做成一个函数而不是几个常量？
    因为管理员在后台改完配置希望立刻生效。每次调用重新读一次文件，
    开销可以忽略，但换来了"改完不用重启服务"这个很值钱的体验。
    """
    # 放在函数内导入：避免 config 这个底层模块在启动时就依赖 store
    from app.store import get_settings

    s = get_settings()
    # base_url / model 允许只覆盖其中一个，另一个回落到默认值。
    # ⚠️ 这里只过**第一层**（协议 + 有主机名），不过内网那一层。
    # 原因不是偷懒：本函数被二十来处调用，包括启动自检和管理台读配置。
    # 在取配置的路径上拦内网 = 存量脏配置、或"本地 ollama + localhost"这种
    # **完全合法的配法**会让每一次对话直接抛错（实测把 test_34 打崩过）。
    # 内网那一层放在"管理端写入时"与"真要发请求之前"两处判（见 check_base_url_ssrf）。
    base = check_base_url_shape(s.get("base_url") or DEEPSEEK_BASE_URL)
    model_default = s.get("model") or DEEPSEEK_MODEL

    # 学生自带 key：用他的 key 与模型名，但地址仍是服务器这一侧的
    if override and override.get("api_key"):
        return {
            "api_key": override["api_key"],
            "base_url": base,
            "model": override.get("model") or model_default,
            "source": "学生端自定义 API（地址走服务器配置）",
        }

    key = s.get("api_key") or DEEPSEEK_API_KEY
    if s.get("api_key"):
        source = "管理后台配置"
    elif DEEPSEEK_API_KEY:
        source = ".env 环境变量"
    else:
        source = "未配置"
    return {"api_key": key, "base_url": base, "model": model_default, "source": source}


def check_config() -> None:
    """启动时检查密钥是否配置（人话：没填 Key 就友好提醒，别让程序莫名其妙崩）。"""
    if not get_llm_config()["api_key"]:
        print("[配置提醒] 还没配置大模型密钥。两种配法任选其一：")
        print("          1) 复制 .env.example 为 .env，在里面填 DEEPSEEK_API_KEY")
        print("          2) 启动后进管理控制台的「API 配置」卡片在线填（不用重启）")
        print("[配置提醒] 没有 Key 时，接口会返回友好提示，不会崩溃。")
