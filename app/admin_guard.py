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

没配口令时怎么办
----------------
**锁死**（403），不是放行。"没配就敞开"意味着新克隆的人一启动就等于没加这道门，
而 README 里的复现步骤恰好就是"启动"——那种默认值不叫安全，叫运气。
`get_admin_token()` 每次现读环境变量，所以配好 `.env` 重启即生效，不用改代码。

⚠️ 一个容易写错的点：比对口令要用 `hmac.compare_digest`，不能用 `==`。
`==` 会在第一个不匹配的字节就返回，理论上能被计时差把口令一位位试出来。
本项目不往外网暴露、这个威胁很轻，但既然写门就按对的写法写，成本是零。
"""
import hmac

from fastapi import Header, HTTPException

from app.config import ADMIN_TOKEN_ENV, get_admin_token

# 前端请求头名字（admin.html 里那一个 fetch 封装统一带上它）
TOKEN_HEADER = "X-Admin-Token"

_NOT_CONFIGURED = (
    f"管理接口已锁定：服务器未配置口令 {ADMIN_TOKEN_ENV}。"
    f"请在项目根目录的 .env 里加一行 {ADMIN_TOKEN_ENV}=你自己的口令 后重启服务"
    "（口令不要提交进仓库）。"
)
_UNAUTHORIZED = "管理口令不对或未提供（请求头 X-Admin-Token）。"


def verify_admin(x_admin_token: str | None = Header(default=None, alias=TOKEN_HEADER)) -> None:
    """FastAPI 依赖：`dependencies=[Depends(verify_admin)]` 挂到接口上即可。

    通过就什么都不返回；不通过直接抛 HTTPException，函数体永远不进业务逻辑。
    """
    expected = get_admin_token()
    if not expected:
        # 服务器自己没配口令：宁可全锁，也不"当没这回事"放行
        raise HTTPException(status_code=403, detail=_NOT_CONFIGURED)
    given = (x_admin_token or "").strip()
    if not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
