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

⚠️ 一个容易写错的点：比对口令要用 `hmac.compare_digest`，不能用 `==`。
`==` 会在第一个不匹配的字节就返回，理论上能被计时差把口令一位位试出来。
本项目不往外网暴露、这个威胁很轻，但既然写门就按对的写法写，成本是零。
"""
import hmac

from fastapi import Header, HTTPException

from app.config import ADMIN_TOKEN_ENV, get_admin_token

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
    if not hmac.compare_digest(given, get_admin_token()):
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
