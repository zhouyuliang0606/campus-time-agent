"""管理接口的一道门（人话：不是所有接口都该对着全世界敞开）。

为什么要有这个文件
------------------
2026-10-08 之前，全项目**没有任何鉴权**：`/api/admin/settings`、`/api/admin/usage`、
`/api/admin/kb`、`/api/workorders` 这些接口，任何能访问到端口的人都能直接读、直接写——
包括读走全部学生消息、改走客服人设、把模型密钥换成他自己的。
"零代码维护"这个卖点，前提得是**只有管理员在点这个按钮**。

边界怎么划（这条最重要，别划错）
------------------------------
- **管理侧的读写**（学生消息、用量、知识库、API 配置、人格、工单列表与处理、发通知）
  → 要口令。
- **学生自己做的事**（对话、日程、课表、上报工单 `POST /api/workorders`）→ **不要口令**。
  学生没有口令，也不该有；这条产品线一破，演示当场就废。
  所以 `POST /api/workorders` 是**故意开放**的，别"顺手"给它也挂上。

没配口令时怎么办（2026-11-01 #94 改了口径）
----------------------------------------
**现场自举一个随机口令，并把那串打在启动横幅里**（`config.announce_admin_token()`）。
以前是"锁死 403"，理由是"没配就敞开叫运气"——这句仍然成立，但旧做法同时把
**新克隆的人关在门外**：`.env` 被 .gitignore 收了、不进仓库，所以"照 README 启动"恰好就是
"没配"，他就算把自己的口令填得一字不差也进不去（服务器那侧根本没有可比对的口令）。
自举是这两件事的交集：门照旧挡陌生人（没那串 = 401），
而钥匙是**这次启动独有的随机值**，不是仓库里人人可读的默认口令；重启换一个，
公网裸跑也猜不到。设了环境变量就以它为准（自举让位，也不回显）。
`get_admin_token()` 仍每次现读环境变量 ⇒ 配好 `.env` 重启即生效，不用改代码。

再加一把**公开的演示口令**（2026-11-01 #96，`config.DEMO_ADMIN_TOKEN`）
--------------------------------------------------------------
上面那套"照横幅抄 16 个字符"对**愿意翻日志的人**够用，但评委/临时接手的人会在抄错一个字符上卡住，
而演示只给 10 分钟。于是没配环境变量时，`config.DEMO_ADMIN_TOKEN` 那串（值只写在 `config` 那一处）也放行。
**这不是疏忽，是权衡后的口径**，所以把依据写在代码里（别当成漏写）：
后台回显的 `api_key` 走 `main.py` 的 `_mask`（只露前 6 后 4，短 Key 只露 1 位），
拿到演示口令的人**读不到完整 Key**；最坏后果是把 Key 覆盖掉、把人设/知识库改坏 ⇒ 演示哑火。
⚠️ 它**仍然能读到**学生消息、用量流水、Agent 轨迹这些后台数据，也能改 base_url ——
而 base_url 一改，下一次调用就把**存着的那把真 Key 发往新地址**（`main.py` 自己在注释里写了这个风险，
SSRF 闸门只拦内网/元数据/单标签名，外网地址是放行的，那是"学生自带 Key"功能的地基）。
⇒ 所以这把公开钥匙**只配用在"克隆出来是空库的演示实例"上**；
一旦这台机器挂着真实 `app/data` 或真 Key（他的云实例、开发机都是），
就设一个 `CAMPUSTIME_ADMIN_TOKEN` —— 演示口令当场失效，不用改代码。
设了没设，判据只有这一条，故意不加"只认回环来源"那类判断（容器与反代下会误判，见 `verify_admin`）。

⚠️ 一个容易写错的点：比对口令要用 `hmac.compare_digest`，不能用 `==`。
`==` 会在第一个不匹配的字节就返回，理论上能被计时差把口令一位位试出来。
本项目不往外网暴露、这个威胁很轻，但既然写门就按对的写法写，成本是零。
"""
import hmac

from fastapi import Header, HTTPException

from app.config import DEMO_ADMIN_TOKEN, admin_token_configured, get_admin_token

# 前端请求头名字（admin.html 里那一个 fetch 封装统一带上它）
TOKEN_HEADER = "X-Admin-Token"

_UNAUTHORIZED = "管理口令不对或未提供（请求头 X-Admin-Token）。"


def verify_admin(x_admin_token: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
    """FastAPI 依赖：`dependencies=[Depends(verify_admin)]` 挂到接口上即可。

    通过就什么都不返回；不通过直接抛 HTTPException，函数体永远不进业务逻辑。

    2026-11-01 #94 起这里**只有 401 一种拒绝**：以前"服务器没配口令"走 403（全锁），
    而 `get_admin_token()` 现在空值时自举随机口令 ⇒ 那条 403 不再有触发的路径，
    留着它就是一段永远不会走到的代码（还让人误以为"没配 = 有个特殊状态要处理"）。
    """
    given = (x_admin_token or "").strip()
    if hmac.compare_digest(given, get_admin_token()):
        return                                  # 环境变量那串 / 自举出的随机串，两条老路一个字没动
    # 演示口令（#96）：**只在部署者没设环境变量时**认这一把。设了就以他为准，不给公开串留后门。
    # 判据只有一条"有没有设环境变量"，故意不加"只认回环来源"那类判断 ——
    # 容器里 client 是 172.x、反代在回环上转发，都会误判成"外面"，多一层判断就多一层判错的机会。
    if given and not admin_token_configured() and hmac.compare_digest(given, DEMO_ADMIN_TOKEN):
        return
    raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
