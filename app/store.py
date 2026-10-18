"""简单文件存储（人话：把学生消息和通知存成 JSON 文件，管理员端能读、能发）。

为什么用文件而不是数据库？演示阶段要的就是"看得懂、改得动"，
每个文件就是一张表：student_messages.json = 学生消息表，notices.json = 通知表。
真上线时把这里换成数据库即可，接口不变。
"""
import contextvars
import datetime
import json
import os
import uuid

# 怎么把长文字切成小块是 upload.py 的事（它本来就管"把文件变成文字"），store 只负责把块存下来。
# 顶层导入是安全的：upload.py 自己只用标准库，那几个办公文档库都在函数里才导入。
from app.upload import CHUNK_SIZE, chunk_text

# ============ 当前请求的学生身份（#32） ============
# 只放"这次请求是谁"这一件事，而且**唯一写入口是 main.bind_student 那个中间件**。
# 为什么是 ContextVar 而不是模块级全局变量：全局变量在并发请求下会被后来者盖掉，
# 出现"A 的请求读到 B 的学号"这种偶发串号（本仓的并发写盘那条是同一个病根）。
# 默认空串 = 没有身份 ⇒ `_spath` 走老位置 `student/`，测试与未登录都靠它兜底。
_CURRENT_STUDENT: contextvars.ContextVar[str] = contextvars.ContextVar("current_student", default="")


def set_current_student(sid: str) -> None:
    """每个请求进来设一次（只有中间件调它，别在业务函数里再设一遍——那是"半路换人"）。"""
    _CURRENT_STUDENT.set(sid or "")


def current_student() -> str:
    """当前请求的学生学号；没登录 / 测试 / 演示脚本里就是空串。"""
    return _CURRENT_STUDENT.get()

# ============ 台账（快递 / 外卖）的「归属人」（#36） ============
# 这两份演示台账原先整份**没有 owner**：
#   · 驿站问「现在有哪些快递还没取走」只能回一张没有归属人的全局表 —— 店员看不出「该催谁」；
#   · 学生端 `list_my_packages()` 名字叫「我的」、函数体却全量遍历 —— 学生 1 看得见学生 2 的取件码。
# 所以「归属」先在数据里补上，而**读它的判据只许这一处**（下面的 `visible_rows`）。
DEMO_OWNER = "demo"


def timetable_saved() -> bool:
    """这个人的 `timetable.json` 在不在（人话：他**传过**课表没有）。

    为什么要有它（#35）：撤掉登录播种之后，新账号是**空库**，界面上得区分
    「从没传过」和「传过又清空了」——这两种空要说的话不一样。
    这个信息只有服务端知道（文件在他自己的个人库目录里），所以由这儿判、
    `GET /api/timetable` 带出去，**前端不许自己猜**。路径仍走 `_spath` 那一处接缝（按学号分）。
    """
    return os.path.exists(_spath("timetable.json"))


def owner_of_row(row: dict) -> str:
    """这一件归谁（学号）。老记录没有 owner 的按 demo —— 与 `status` / `archived` 同一做法：
    **不批量迁移文件**（每迁移一次，就多一次改坏真实数据的机会）。
    """
    return str(row.get("owner") or DEMO_OWNER)


def current_owner() -> str:
    """当前该按谁的口径读台账：有身份按身份，没身份（测试 / 演示脚本 / 没登录）按 demo。

    ⚠️ 身份来源只有 #32 那一份 —— `current_student()` 是中间件按 cookie 设的 ContextVar。
       这里**不另开来源**（不自己读 cookie、不从请求体里猜）：两处来源就会打架，
       而打架的结果永远是「谁读都算数」。
    """
    return current_student() or DEMO_OWNER


# ============ 账号名单（#38：只开放登记过的学号） ============
# 「只校验格式」= 任何 1~20 位字母数字都能进：随手打一个 `abc` 就多一个"学生"、
# 多一个空库、多一条谁都看不懂的会话 —— 今天 `student/` 下那几个空目录就是这么来的。
# ⚠️ 这道门挡得住"手输学号"，**挡不住"手改 cookie"**；后者由 #38 的签名 cookie 管
#    （`main._clean_student`）。两层判据各一处，别指望这一层去管那一层的事。
ACCOUNTS_FILE = "accounts.json"


def list_accounts() -> list:
    """已开通的学号名录（人话：谁能进这个系统）。

    ⚠️ **每次现读**，不缓存：演示前改完 JSON 直接重登就生效，不用重启服务；
       缓存一份就得处理"改完什么时候失效"，那是白送一类 bug。
    ⚠️ 文件不存在 / 被改坏 ⇒ 返回**空列表**（= 谁都进不来），不是"全部放行"。
       坏文件当全放行是安全上最坏的那种fallback：看起来一切正常，门其实开着。
       空名单这件事由 `main._check_accounts()` 在启动时喊出来。
    """
    try:
        return list(_read(ACCOUNTS_FILE, {"accounts": []}).get("accounts") or [])
    except (json.JSONDecodeError, AttributeError):
        return []


def is_registered(sid: str) -> str | None:
    """这个学号开通了就返回它那份名录项，否则 None（`/api/login` 是唯一判点）。

    返回整条而不只是 bool：将来"显示名字 / 按人限流"要用，调用方不必再读第二遍文件。
    """
    want = str(sid or "")
    for a in list_accounts():
        if str(a.get("sid") or "") == want:
            return a
    return None


def visible_rows(rows, *, staff: bool = False) -> list[dict]:
    """台账过滤 —— **过滤判据只有这一处**（`packages_view` / `list_my_*` / `get_by_code` 都调它）。

    · 学生口径（默认）：只留 owner == 当前口径 的那些；
    · 店员口径（`staff=True`）：看全店 —— 驿站要按人分组才知道该催谁，这是它办件必需的可见范围。

    ⚠️ `staff=True` 放行的是**包裹**，不是「这个人的所有数据」：学生的日程 / 课表 / 待办仍然
       归 #34 那道端级闸门管（`main._station_surface`），这一条不许顺手扩大范围。
    """
    out = list(rows or [])
    if staff:
        return out
    who = current_owner()
    return [r for r in out if owner_of_row(r) == who]


# 数据目录默认是 app/data；但可以用环境变量指到别处。
# 为什么留这个开关：跑自动化测试时，让测试写一份临时副本，
# 这样测试再怎么折腾（写假密钥、传文件）都碰不到真实演示数据。

# 数据目录**每次调用时**重新读环境变量，不在导入时定死。
# 为什么这一点很要紧：测试沙箱的做法是"先切目录、再 import app.xxx"，
# 而 Python 的模块一旦导入就缓存在内存里。早先 DATA_DIR 在导入时定死，
# 结果第一批沙箱之后所有写操作都还指着真实演示数据——
# 测试跑完 data/student/timetable.json 被改成 13 门、todos.json 里堆着十几条测试待办，
# 看着像"测试偶发失败"，其实是测试一直在偷偷改演示数据。
_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def _data_dir() -> str:
    """当前生效的数据目录（人话：每次都重新问一遍环境变量，别信缓存）。"""
    return os.environ.get("CAMPUSTIME_DATA_DIR") or _DEFAULT_DATA_DIR


# 留个默认值给外部瞄一眼用，别再拿它拼路径——拼路径一律走 _data_dir()。
DATA_DIR = _data_dir()


def _path(name: str) -> str:
    return os.path.join(_data_dir(), name)


def _read(name: str, default):
    """读一个 JSON 文件；没有就返回默认值（人话：拿不到就给个空列表）。"""
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def read_data_file(name: str, default):
    """给各模块自己读「数据目录下的某份 JSON」（人话：**路径每次现算**，别在模块顶层拼死）。

    为什么要有它（#36，与红线 23 同根）：`express.py` / `takeout.py` 原先在**导入时**把
    `_DATA_PATH` 拼成「源码旁边的 data/」，于是沙箱把 `CAMPUSTIME_DATA_DIR` 换掉之后，
    它们照旧读真实台账 —— 表现是「想给测试种一份 1 号同学的快递，根本种不进去」，
    而这些断言其实一直在量用户那份演示底稿（本机与干净克隆还可能是两个数）。
    """
    return _read(name, default)


def _write(name: str, data) -> None:
    """写一个 JSON 文件（人话：把数据落盘）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def add_message(module: str, message: str, answer: str, kind: str = "chat",
                ref: str = "", owner: str | None = None) -> dict:
    """记一条学生消息（人话：学生说了啥、在哪个模块、管家怎么答的，存进收件箱）。

    `kind` 是给"这条是从哪儿来的"打的标签：`chat`=学生端每轮自己存档的对话，
    `report`=工单立案时**双写**的那条（管理端「学生工单」和「学生消息」两个视图都要看得见）。
    默认 `"chat"` 是刻意的：`/api/student-message` 那个旧调用点一个字没改，行为也不该变。

    `ref` 只有 `report` 那一路会带：**指向立案那条工单的 id**。
    归档工单时要靠它找到这条镜像一起收起 —— 认字符串（回话里那句「工单 xxx」）只是
    给加这个字段之前登进去的老记录兜的，两条都在，不批量迁移文件（同 #27 那个 `status` 的处理）。
    """
    msgs = _read("student_messages.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "module": module or "",
        "message": message,
        "answer": answer or "",
        "kind": kind or "chat",
        # 谁报的：**默认由系统取当前身份**（#37）。无身份就是空串 —— 不是 "demo"，更不许编一个。
        "owner": current_student() if owner is None else str(owner),
    }
    if ref:
        item["ref"] = str(ref)
    msgs.append(item)
    _write("student_messages.json", msgs)
    return item


def list_messages(kind: str | None = None, include_archived: bool = False) -> list:
    """列出学生消息，倒序（最新在前），方便管理员看。

    给了 `kind` 就只返回那一路；**没 kind 字段的老条目按 `chat` 算** ——
    它们本来就是对话，直接丢掉会让管理端那张表"突然少几行"，那是最容易被当成 bug 的现象。

    `include_archived`（#29 归档）：默认**不含**已归档的。过滤只写在这一处 ——
    管理端两张表与驿站端都从这儿读，一处藏一处不藏就会变成"同一条东西两个地方看见的不一样"。
    """
    msgs = _read("student_messages.json", [])
    if not include_archived:
        msgs = [m for m in msgs if not m.get("archived")]
    if kind is None:
        return list(reversed(msgs))
    return list(reversed([m for m in msgs
                          if (m.get("kind") or "chat") == kind]))


# ============ 工单（人话：学生确认上报的问题，管理员能在管理端看到的正式记录） ============
# 注意区别：student_messages 是"学生消息存档"，工单是**学生确认后**才生成的问题上报。
# 按学生助手的人设规矩：普通日常对话不上报驿站，只有工单才进管理端视图
#（2026-10-16 #18 单子 2：报障去向改成驿站；后半句仍然对 —— 工单在管理端也看得见，是监督位）。

def add_workorder(kind: str, desc: str, source: str = "chat",
                  owner: str | None = None) -> dict:
    """记一条工单（人话：学生确认要上报的问题，正式立案给管理员处理）。

    :param kind: 问题类型，如 "疑似丢件" / "外卖丢失" / "包裹破损"
    :param desc: 学生的补充描述
    :param source: 来源，"chat"=聊天里跟管家说并确认的 / "panel"=快递面板表单提交的
    :param owner: 报这条的学号。**默认 None = 系统自己取 `current_student()`**，
        两条写入口（`/api/workorders` 与 AI 的 `submit_work_order` 工具）就自动都有了归属，
        调用方一个字都不用改。为什么不许调用方传：
          ① 那个工具只拿到 kind/desc，让它传 owner 等于让**模型决定"我是谁"**；
          ② body 里的字段谁都能改，前端传 owner = 任何人都能冒充别人立案。
        无身份时是空串（不是 "demo"，也不编一个学号）。
    """
    items = _read("workorders.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "kind": kind or "其他问题",
        "desc": desc or "",
        "source": source,
        "status": "待处理",
        "owner": current_student() if owner is None else str(owner),
    }
    items.append(item)
    _write("workorders.json", items)
    return item


def list_workorders(include_archived: bool = False, owner: str | None = None) -> list:
    """列出工单，倒序（最新在前）。已归档的默认不出现（判据同 `list_messages`）。

    :param owner: `None` = **不过滤**（管理端 / 驿站端要全量，行为一个字不变）；
        传学号 = 只要这个人报的（学生端「我提交过的工单」，#37）；
        传空串 `""` = 只要**没归属的老单**。
    ⚠️ `None` 与 `""` 的语义必须分开，判据写成 `if owner:` 就把"老单"那一档永远查不出来了
       （而它正是我们判断"归属是从哪天开始落的"唯一证据）。过滤只写在这一处。
    """
    items = _read("workorders.json", [])
    if owner is not None:
        items = [w for w in items if (w.get("owner") or "") == owner]
    if not include_archived:
        items = [w for w in items if not w.get("archived")]
    return list(reversed(items))


def list_archived_workorders() -> list:
    """被 #29 收起的那几条工单（#39-C：驿站也要看得见"已处理"的历史）。

    为什么在 store 而不是路由里挑：`test_58` 钉着"main 里不许出现按 archived 过滤列表的写法"——
    **过滤判据一处，视图只管画**。两处各挑一遍，将来归档加一档（比如"已关闭"）就会有一边漏。
    """
    return [w for w in list_workorders(include_archived=True) if w.get("archived")]


def update_workorder_status(wo_id: str, status: str) -> dict | None:
    """管理员处理工单后改状态（待处理 / 处理中 / 已解决）。"""
    items = _read("workorders.json", [])
    for it in items:
        if it.get("id") == wo_id:
            it["status"] = status
            _write("workorders.json", items)
            return it
    return None


def set_message_archived(mid: str, archived: bool = True) -> dict | None:
    """把一条学生消息**归档**（收起来）或放回。

    ⚠️ 这是**软删**：文件里那条一个字节都不少，只多/去掉 `archived` 与 `archived_at`。
    工单与消息都是"学生报过什么"的证据，硬删掉就再也对不上账（评审问起来查不到就是查不到）。
    """
    msgs = _read("student_messages.json", [])
    for m in msgs:
        if str(m.get("id") or "") == str(mid):
            _stamp(m, archived)
            _write("student_messages.json", msgs)
            return m
    return None


def set_workorder_archived(wo_id: str, archived: bool = True) -> tuple | None:
    """归档一条工单，并**把双写那条学生消息一起收起**。

    返回 `(工单, 一起收起的消息条数)`；找不到那条工单返回 None。
    为什么是一个函数而不是两个接口：`add_workorder` 那边是**同一个函数里写两张表**（决策A 的双写），
    收起也必须在这一个函数里做 —— 拆成两个口让前端各调一次 = 漏一次就静默留一条孤儿消息。
    老记录认那条镜像只有"回话里那句「工单 {id}」"可认（`ref` 字段是这轮才加的），
    两支都留着，且不迁移文件（同 #27 那个 `status` 的处理）。
    """
    items = _read("workorders.json", [])
    hit = None
    for it in items:
        if str(it.get("id") or "") == str(wo_id):
            _stamp(it, archived)
            hit = it
            break
    if hit is None:
        return None
    _write("workorders.json", items)
    msgs = _read("student_messages.json", [])
    changed = 0
    for m in msgs:
        if str(m.get("ref") or "") == str(wo_id) or f"工单 {wo_id}" in (m.get("answer") or ""):
            _stamp(m, archived)
            changed += 1
    if changed:
        _write("student_messages.json", msgs)
    return hit, changed


def _stamp(item: dict, archived: bool) -> None:
    """打/摘归档标记（一处实现，两张表共用，免得两份写法各漂一半）。"""
    if archived:
        item["archived"] = True
        item["archived_at"] = _now()
    else:
        item.pop("archived", None)
        item.pop("archived_at", None)


def add_notice(title: str, content: str, attachment: dict | None = None) -> dict:
    """发一条通知（人话：管理员写一条通知，存进通知表）。

    :param attachment: 可选的附件信息，形如 {"id": "...", "name": "xxx.pdf"}
                       （人话：通知可以挂个文件，比如放假安排表）
    """
    ns = _read("notices.json", [])
    item = {
        "id": uuid.uuid4().hex[:8],
        "ts": _now(),
        "title": title,
        "content": content,
        "attachment": attachment,  # 没有附件时就是 None，前端按字段判断即可
    }
    ns.append(item)
    _write("notices.json", ns)
    return item


def list_notices() -> list:
    """列出通知，倒序。"""
    return list(reversed(_read("notices.json", [])))


def delete_notice(nid: str) -> bool:
    """撤回一条已发布的通知（#39-D：发错别字、发错人群，以前只能眼看着它挂着）。

    两处刻意不做：
      ① **不动它挂的附件**：`uploads/<id>` 那份原件还留着。通知是"这句话不该说"，
         文件本身是另一份资产，跟着一起删等于替管理员做了删除文件的决定（真删文件要找回来，
         而撤回这条动作的可逆性预期是"再发一遍就行"）。
      ② 不做软删/隐藏标记：通知表里没有 status/archived 那一套，加一档就是给这张表
         新开第二份状态词表（本项目为"两份状态表"付过几次钱）。要"撤回后又出现"就再发一条。
    """
    rows = _read("notices.json", [])
    if not isinstance(rows, list):
        return False
    kept = [n for n in rows if str(n.get("id")) != str(nid)]
    if len(kept) == len(rows):
        return False
    _write("notices.json", kept)
    return True


# ============ 知识库管理（管理员零代码维护，faq 模块直接吃这份数据） ============

def list_kb() -> list:
    """列出知识库全部条目。"""
    return _read("kb.json", {"entries": []}).get("entries", [])


def add_kb_entry(question: str, answer: str, keywords: list) -> dict:
    """新增一条知识库条目（人话：管理员在后台加一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    item = {"question": question, "answer": answer, "keywords": keywords or []}
    entries.append(item)
    _write("kb.json", data)
    return item


def update_kb_entry(index: int, question: str, answer: str, keywords: list) -> dict:
    """按序号修改一条知识库条目（人话：管理员编辑一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    if index < 0 or index >= len(entries):
        raise IndexError("知识库条目不存在")
    entries[index] = {"question": question, "answer": answer, "keywords": keywords or []}
    _write("kb.json", data)
    return entries[index]


def delete_kb_entry(index: int) -> bool:
    """按序号删除一条知识库条目（人话：管理员删掉一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    if index < 0 or index >= len(entries):
        return False
    entries.pop(index)
    _write("kb.json", data)
    return True


# ============ 客服人格配置（管理员配置，驿站/商户客服模块吃这份数据） ============

# 默认人格：管理员没配过时先用这个
DEFAULT_PERSONA = {
    "name": "小园",
    "role": "校园驿站客服助手",
    "tone": "热情、耐心、亲切，像邻家学姐",
    "rules": "回复简洁；涉及取件码/物流以台账为准，不编造；遇到不会的引导到驿站前台。",
}


def get_persona() -> dict:
    """读取当前客服人格（人话：驿站客服用这个语气说话）。"""
    return _read("persona.json", DEFAULT_PERSONA)


def set_persona(name: str, role: str, tone: str, rules: str) -> dict:
    """保存客服人格（人话：管理员改了客服的人设，下次对话立即生效）。"""
    p = {"name": name, "role": role, "tone": tone, "rules": rules}
    _write("persona.json", p)
    return p


# ============ 运行时设置（管理台零代码配置的 AI 接口参数） ============
# 注意：这个文件里可能有真实的 API Key，所以它必须放进 .gitignore，
# 并且读取给前端看的时候要脱敏（由 main.py 负责掩码）。

def get_settings() -> dict:
    """读取管理台保存的设置（人话：管理员在后台填的 AI 接口参数）。

    返回空字典表示"管理员没在后台配过"，此时 config 层会回落到 .env。
    """
    return _read("settings.json", {})


def set_settings(data: dict) -> dict:
    """保存管理台设置（人话：把管理员填的接口参数存档，config 层每次会来读）。

    用"增量更新"而不是整体覆盖：管理员只想改模型名时，不至于把 Key 冲掉。
    """
    current = get_settings()
    # 空字符串视为"没填"，不落盘，这样它会自动回落到 .env 的默认值
    for k, v in data.items():
        if isinstance(v, str) and not v.strip():
            current.pop(k, None)
        else:
            current[k] = v
    _write("settings.json", current)
    return current


# ============ 上传文件（给 AI 助手读的资料） ============
# 正文存在 uploads/<id>.txt，元信息存在 uploads.json。
# 分开存是为了让 uploads.json 保持能人肉阅读，不会被大段正文撑爆。

# ⚠️ 上传目录**每次现算**，不在导入时定死（文件头那条 DATA_DIR 的老坑，这里是同一个根）：
#    原来写的是 `UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")`，模块一导入就把路径焊死在真实
#    `app/data/uploads/` 上 —— 于是测试沙箱（CAMPUSTIME_DATA_DIR 指到临时目录）里上传的文件
#    **仍然落进真实目录**。`_harness` 的 SCRATCH 里一直列着 "uploads"，说明沙箱从头就假设它被隔离，
#    只有写入方没做到：2026-10-16 实测两轮全量往真实目录里堆了 30 个测试文件才发现。
#    拼路径一律走 `_upload_dir()`，下面那个名字只给人瞄一眼默认值。
def _upload_dir() -> str:
    return os.path.join(_data_dir(), "uploads")


UPLOAD_DIR = _upload_dir()
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 单个文件上限 5MB，防止演示机被撑爆


def _upload_body_path(fid: str) -> str:
    """提取出来的文字放在这里（AI 读的就是这份）。"""
    return os.path.join(_upload_dir(), fid + ".txt")


def save_upload(filename: str, content: str, raw: bytes | None = None) -> dict:
    """保存一个上传文件（人话：既留原件供下载，也提取文字给 AI 读）。

    为什么要存两份？
    - 原件：通知的附件要能让学生下载回去（丢了就只剩摘要，体验不完整）
    - 文字：AI 真正能"看懂"的形态

    :param raw: 文件原始字节；不传就只有文字、没有原件可下载
    """
    os.makedirs(_upload_dir(), exist_ok=True)
    fid = uuid.uuid4().hex[:12]
    # 1) 提取出的文字
    with open(_upload_body_path(fid), "w", encoding="utf-8") as f:
        f.write(content)
    # 2) 原件（保留原始后缀，下载回去才打得开）
    ext = os.path.splitext(filename or "")[1].lower()
    if raw is not None:
        with open(os.path.join(_upload_dir(), fid + ext), "wb") as f:
            f.write(raw)
    # 3) 分块索引：上传当场建好，免得"传完了还得去后台点一下才查得到"（#40）。
    #    ⚠️ 块数**不写进 uploads.json**：那份表是台账，写了就变成两个地方各说一份块数
    #    （重建索引后 uploads.json 里那个旧数字就开始说谎）。列表页要块数，
    #    现读 `<fid>.chunks.json` 就行 —— 单一来源，也就不会有"状态对不上"那种账。
    save_chunks(fid, chunk_text(content))

    item = {
        "id": fid,
        "name": filename,
        "size": len(raw) if raw is not None else len(content.encode("utf-8")),
        "ext": ext,
        "has_raw": raw is not None,
        "ts": _now(),
        "chars": len(content),
        "preview": content[:200],  # 列表页给个预览，不用真的再读一遍文件
    }
    data = _read("uploads.json", {"files": []})
    data.setdefault("files", []).append(item)
    _write("uploads.json", data)
    return item


def list_uploads() -> list:
    """列出所有上传文件，倒序（最新在前）。"""
    return list(reversed(_read("uploads.json", {"files": []}).get("files", [])))


def get_upload(fid: str) -> str | None:
    """读某个上传文件提取出的文字；找不到返回 None。"""
    try:
        with open(_upload_body_path(fid), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def get_upload_meta(fid: str) -> dict | None:
    """查某个文件的元信息（名字/后缀等），下载原件时要靠它还原文件名。"""
    for f in _read("uploads.json", {"files": []}).get("files", []):
        if f.get("id") == fid:
            return f
    return None


def get_upload_raw_path(fid: str) -> str | None:
    """取原件在磁盘上的路径；没有原件（比如只有文字）或文件不存在时返回 None。"""
    meta = get_upload_meta(fid)
    if not meta or not meta.get("has_raw"):
        return None
    p = os.path.join(_upload_dir(), fid + (meta.get("ext") or ""))
    return p if os.path.exists(p) else None


# ============ 上传文档的分块索引（#40：让长文档的后半本也查得到） ============
# 为什么单独存一份 .chunks.json，而不是检索时现切？
# 现切当然也跑得动，但那样每问一句都要把全部文档重切一遍；上传时切一次、
# 以后只是读，代价小得多。参数改了想重切，管理端有【重建索引】。
def _upload_chunks_path(fid: str) -> str:
    """分块存放位置（跟 extract 出来的 .txt 同目录、同 id，一眼看得出是一对）。"""
    return os.path.join(_upload_dir(), fid + ".chunks.json")


def save_chunks(fid: str, chunks: list[dict]) -> None:
    """存一份文档的分块（人话：把手册拆成的卡片放回同一个抽屉）。

    不缩进：这份文件是给机器读的，几百块正文带上缩进白多几十 KB。
    """
    os.makedirs(_upload_dir(), exist_ok=True)
    with open(_upload_chunks_path(fid), "w", encoding="utf-8") as f:
        json.dump({"chunk_size": CHUNK_SIZE, "chunks": chunks}, f, ensure_ascii=False)


def list_chunks(fid: str) -> list[dict]:
    """读某文档的分块；没建过索引（老数据）或文件坏了都回空表。

    坏文件回空表而不是抛异常：检索工具的处境是"这份文档查不到"，
    管理员看见的状态是"未建索引"，点一下【重建索引】就好了。
    """
    try:
        with open(_upload_chunks_path(fid), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    rows = data.get("chunks") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def all_chunks():
    """摊开所有文档的所有块，给检索用。产出 {fid, name, i, text, start}。

    每次现扫（demo 规模几百块，读几个小 JSON 而已）。真嫌慢再加内存缓存，
    按 uploads.json 的 mtime 失效 —— 那是换函数体，接口不用动。
    """
    for f in list_uploads():
        fid = str(f.get("id") or "")
        if not fid:
            continue
        for c in list_chunks(fid):
            yield {
                "fid": fid,
                "name": f.get("name") or "",
                "i": c.get("i"),
                "text": c.get("text") or "",
                "start": c.get("start") or 0,
            }


def reindex_upload(fid: str) -> int | None:
    """按已存下来的 .txt 重新分块（改了分块参数、或老文档补建索引时用）。

    返回块数；**找不到这个文件时返回 None** —— 调用方要分得清
    "没这份文档"和"这份文档切出了 0 块"，两者对管理员说的话完全不同。
    """
    text = get_upload(fid)
    if text is None:
        return None
    chunks = chunk_text(text)
    save_chunks(fid, chunks)
    return len(chunks)


def upload_notice_refs(fid: str) -> list[dict]:
    """这份上传文件被哪几条通知挂着（删文档前那道闸要用）。

    只认 `attachment.id`：通知里存的就是 {id, name}，别拿文件名去猜（同名文件不止一份）。
    """
    fid = str(fid or "")
    refs = []
    for n in list_notices():
        att = n.get("attachment") or {}
        if att.get("id") == fid:
            refs.append({"id": n.get("id"), "title": n.get("title") or "（无标题）"})
    return refs


def delete_upload(fid: str) -> bool:
    """彻底删掉一份上传文档：文字 + 原件 + 分块索引 + uploads.json 里那条。

    两道守门，都故意留在 store 层（放在接口层就等于绕过去只要换个调用方）：
      ① 还挂在通知上 → **不删**。不许替管理员把一条通知的附件抽走（#39-D 定过的口径），
         真删了的表现是通知列表里那条点开下载 404，学生看见的是坏链接。
      ② uploads.json 里没这条 id → False。
    ⚠️ 这是**真删、不可逆**，跟"归档"那种软删不是一回事，措辞与实现都不许互借。
    """
    fid = str(fid or "")
    if not fid or upload_notice_refs(fid):
        return False
    meta = get_upload_meta(fid)
    if meta is None:
        return False

    files = _read("uploads.json", {"files": []}).get("files") or []
    kept = [f for f in files if str(f.get("id")) != fid]
    if len(kept) == len(files):
        return False
    _write("uploads.json", {"files": kept})

    for p in (_upload_body_path(fid), _upload_chunks_path(fid), get_upload_raw_path(fid)):
        if not p:
            continue
        try:
            os.remove(p)
        except OSError:
            # 只有文字、没有原件的老数据本来就没这个文件；表里那条已经删掉了，目标达成。
            # 别为了这种"文件早就不在"把整次删除报错——管理员会以为没删掉，再点一次还是错。
            pass
    return True


# ============ 学生个人库：周表（课程）+ 待办 + 会话历史 ============
# 单独一个 student/ 目录，是因为这属于"某个学生的个人数据"，
# 跟上面的公共知识库、站点台账性质不同，以后做多用户时天然按用户隔离。

# 会话历史最多保留多少条消息（一问一答算两条）。
# 留太少会记不住刚才商量好的安排，留太多又白烧 token，20 条够用。
MAX_HISTORY = 20


def student_root() -> str:
    """个人库根目录 `<data>/student` —— 老位置那份**演示底稿**就在这儿（登录播种只读它）。"""
    return os.path.join(_data_dir(), "student")


def student_dir(sid: str) -> str:
    """某个学号的个人库目录；没有学号就是根目录（老位置）。

    ⚠️ 这是给"按人分目录"这件事用的（登录播种、测试核对路径）；
    业务代码**拼文件路径仍然只许走 `_spath`**，别在这儿再拼一遍名字 —— 那就会变成第二份判据。
    """
    return os.path.join(student_root(), sid) if sid else student_root()


def _spath(name: str) -> str:
    """学生个人库里某个文件的路径。

    ⚠️ **按学号分**：`student/<学号>/timetable.json`；**没有学号时退回老位置 `student/`**。
    兜底那半条不是偷懒：50 多个护栏文件、既有演示数据、以及"没登录直接打开 /student"
    都走无学号这条路 —— 去掉它这群会一起红（`test_59` B 批钉这条）。
    分人之后课表 / 待办 / 会话历史**自动全部分开**，因为它们的读写只有 `_sread`/`_swrite` 两个调用点，
    而这两个都从这里取路径 —— 这就是"接缝只有一处"的价值：**别在别处再拼一遍 `student/`**。

    学号是谁定的？浏览器每次请求带的 cookie（`main.bind_student` 中间件设进 ContextVar）。
    这**不是鉴权**（学号谁都能填），只解决"同一台机器换账号时数据不共用"。
    用 ContextVar 而不是模块级全局变量：全局变量在并发请求下会互相盖掉，出现
    "A 的请求读到 B 的学号"这种偶发串号 —— 与"并发写盘丢更新"同一个病根。
    """
    base = student_root()
    who = _CURRENT_STUDENT.get()
    return os.path.join(base, who, name) if who else os.path.join(base, name)


def _sread(name: str, default):
    """读学生个人库里的一个 JSON 文件。"""
    try:
        with open(_spath(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _swrite(name: str, data) -> None:
    """写学生个人库里的一个 JSON 文件。"""
    os.makedirs(os.path.dirname(_spath(name)), exist_ok=True)
    with open(_spath(name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---- 周表：学生每周固定的课程 ----

def get_timetable() -> list:
    """读周表全部课程（人话：这周哪天第几节课上什么）。

    每条形如 {"day": 1, "start": "08:00", "end": "09:40",
              "course": "高等数学", "location": "教三301"}
    day 用 1=周一 … 7=周日。
    """
    return _sread("timetable.json", {"courses": []}).get("courses", [])


def get_timetable_data() -> dict:
    """读周表整份数据（含"上次更新时间"，前端要显示课表是什么时候导入的）。"""
    return _sread("timetable.json", {"updated_at": "", "courses": []})


def save_timetable(courses: list) -> dict:
    """整体覆盖保存周表（人话：学生在聊天里传课表后，AI 整理好存进来）。"""
    data = {"updated_at": _now(), "courses": courses}
    _swrite("timetable.json", data)
    return data


# ---- 待办：被安排进具体日期和时间段的任务 ----

# 重复待办的三个新字段（2026-10-10 加，用户选了"真·长期"这条路线）：
#   repeat    : "" = 一次性（老数据全是这个）；"weekly" = 每周重复；"daily" = 每天的习惯
#   weekday   : 1..7（周一=1）。只在 repeat == "weekly" 时有意义，是"每周落在哪一天"
#   done_dates: ["2026-10-11", ...] 已经完成过的那些日子
#
# ⚠️ 为什么"完成"要按天记、而不是复用 status：
#    一条每周日的待办，勾掉本周**不等于**以后都不用做。整条 status=done 会让它
#    第二周就消失 —— 那是最容易被当成 bug 的行为。所以：
#      一次性待办 → 看 status（planned/done，老逻辑一字未动）
#      重复待办   → 看 done_dates 里有没有那一天
#    这也是为什么 repeat/weekday/done_dates 都给了"空默认值"：老记录少这三个键
#    照样跑，不需要迁移数据文件（app/data 是真实演示数据，不迁移就是最好的处理）。

REPEAT_NONE = ""
REPEAT_WEEKLY = "weekly"
# 每天类习惯（2026-10-13 用户 Prompt 02 拍板）：「我每天都要背单词」= 一条记录、天天出现。
# 它和 weekly 的**分工不是展开长度，是出没出现在日历上**：
#   weekly —— 有固定星期几 + 钟点，是"那天的时间承诺"，照旧画进日程；
#   daily  —— 没有指定哪天，画进日历就是"以后每一天都占一格"，学生要的是打卡不是格子。
# 所以 daily 属于**习惯类**：只进代办清单，日历（学生端周/月视图、/plan、月表角标）一律不画。
REPEAT_DAILY = "daily"
# 「这条是不是习惯（不进日历）」这个判据**只写这一处**。前端 `isHabit` 是它的翻译，
# 别再写出第二份 `todoOccurs` 的变体（接手指南红线 11b：同一个判断抄几份就是同一个病）。
HABIT_REPEATS = (REPEAT_DAILY,)
# 「完成按天记（done_dates）而不是翻整条 status」的档位。判据只写这一处：
# `todo_done_on` 与 `set_todo_done_on` 都问它 —— 漏掉一个，勾掉"今天"就会把
# 一条习惯**永久**划掉（明天它不出现了），那是最容易被当成 bug 的行为。
DAY_LOGGED_REPEATS = (REPEAT_WEEKLY, REPEAT_DAILY)
# 每周重复往后展开的上限（月）。三处必须一起改：后端 `weekly_horizon`、
# `student.html` 的 weeklyLimit、`plan.html` 的 weeklyLimit —— 只改一边就是
# "接口说那天有、日程上画不出来"（2026-10-10 定下这条规矩，见接手指南 §4 第 9 条）。
# daily 沿用**同一把尺子**收口（不是另起一套）：不封顶的话，一条"每天"能算到 2099 年。
WEEKLY_HORIZON_MONTHS = 3
REPEAT_VALUES = (REPEAT_NONE, REPEAT_WEEKLY, REPEAT_DAILY)


def is_past_date(date: str) -> bool:
    """这个日子是不是已经过去了（人话：**过去的日子不许再往上排待办**）。

    为什么要单独一处写、还要四处都查（用户 2026-10-11 明令）：
      · 语义层偶尔吐出一个**陈年日期**（真机实测：「晚上八点开始值日」被给了
        `2023-04-18`），确定性分支照收 → 卡片挂出去了，学生刷新一看**永远找不到**；
      · 卡片挂着过夜，学生第二天点【确认加入】 → 写进去的是一条过去的待办，
        它再也不会出现在"今天/ upcoming"里。
    所以**出卡那一路**和**写库那一路**都要拦，而判据只能有一份。
    格式不对（空串、"昨天"这种没解析干净的）返回 False：不在这里管，
    交给调用方自己的必填校验，免得这道闸顺手把别的错误也吞掉。
    """
    try:
        return datetime.date.fromisoformat((date or "").strip()) < datetime.date.today()
    except ValueError:
        return False


def iso_weekday_of(date: str) -> int | None:
    """把 "2026-10-11" 换成 1..7（周一=1）。解析不了回 None，不抛错。"""
    from datetime import date as _date

    try:
        y, m, d = (int(x) for x in str(date).split("-"))
        return _date(y, m, d).isoweekday()
    except (ValueError, TypeError):
        return None


def weekly_horizon(anchor: str = "") -> str:
    """每周重复**最多往后铺到那一天**（含这一天）。

    为什么必须有上限：一条 `repeat=weekly` 的记录只有一个锚点，却代表"以后每个周六"。
    不加收口，它就是"直到世界尽头的每个周六" —— 月表翻到 2099-01 也给你画出东西来
    （test_03 那条红一直钉在这儿，而产品确实离谱：学生从没答应过要一直做到 2099 年）。

    基准取 `max(今天, 这条的锚点)`，两个方向都不能吞数据：
      · 锚点在过去或今天 → 从今天起算 N 个月（翻到上限之外的整月应当是空的）；
      · 锚点在未来（「明年六月起每个周六」）→ 从锚点起算，不然这一条**一天都看不见**。
    ⚠️ 只管**每周重复的推断展开**。一次性待办的 `date` 是学生明确写下的日子，
      再远也照原样出现 —— 上限收的是"系统替他推出来的将来"，不是他写的数。
    """
    base = datetime.date.today()
    try:
        a = datetime.date.fromisoformat((anchor or "").strip())
        if a > base:
            base = a
    except ValueError:
        pass
    return _add_months(base, WEEKLY_HORIZON_MONTHS).isoformat()


def _add_months(day: datetime.date, months: int) -> datetime.date:
    """加 n 个**日历月**，那天在目标月不存在就贴到目标月最后一天。

    11-30 加 3 个月 = 2-28（不是 3-2）：Python 的 `date(y, m+3, d)` 会直接溢到下一月，
    那种"多加了 33 天"的写法在月末生成的锚点上会一天差一个月。
    """
    y, m = divmod(day.year * 12 + day.month - 1 + months, 12)
    m += 1                                   # divmod 给的是 0..11，月份从 1 数
    last = (datetime.date(y + (m == 12), (m % 12) + 1, 1)
            - datetime.timedelta(days=1)).day
    return datetime.date(y, m, min(day.day, last))


def is_habit(t: dict) -> bool:
    """这条待办是不是「习惯」——**只进代办清单、不画进日历**的那一类（判据只此一份）。

    新增一档"不进日历"的 repeat 时，只往 `HABIT_REPEATS` 里加一个值：
    日历那几处（学生端周/月视图、/plan、后端月表）都是问这一句，不在那儿各写一份展开规则。
    """
    return str((t or {}).get("repeat") or "") in HABIT_REPEATS


def todo_occurs_on(t: dict, date: str) -> bool:
    """这条待办**在不在这一天**（人话：重复的事每个周期都会再出现一次）。

    判据只有一处，前端/撞档/面板都该是同一套逻辑的翻译，别在别处再抄一遍 `t.date == date`。
    每周重复那份"以后每个周六"是**推断**出来的，所以它有个头：见 `weekly_horizon`。
    ⚠️ 这里返回 True 只代表"这一天它是要做的一件事"，**不代表要画进日历** ——
    daily 是习惯，天天都返回 True，日历那侧靠 `is_habit` 滤掉，不靠这里撒谎。
    """
    if not t:
        return False
    rep = str(t.get("repeat") or "")
    anchor = str(t.get("date") or "")
    if rep == REPEAT_DAILY:
        # 锚点之前不算（"从下周一开始每天"），往后同样用 weekly 那把尺子收口
        return str(anchor) <= str(date) <= weekly_horizon(anchor)
    if rep != REPEAT_WEEKLY:
        return str(t.get("date") or "") == str(date)
    wd = t.get("weekday") or iso_weekday_of(anchor)
    if iso_weekday_of(date) != wd:
        return False
    # 锚点之前不算出现：学生说"从下周日开始"，不该把已经过去的周日也算进去
    if str(date) < anchor:
        return False
    # 往后也只铺到上限那天：翻到四个月以后的月表应当是空的
    return str(date) <= weekly_horizon(anchor)


def todo_done_on(t: dict, date: str) -> bool:
    """这一天它算不算已经完成（重复待办按天判，一次性待办看 status）。"""
    if not t:
        return False
    if str(t.get("repeat") or "") in DAY_LOGGED_REPEATS:
        # 习惯/每周都按天记：勾掉今天 ≠ 以后都不用做
        return str(date) in [str(x) for x in (t.get("done_dates") or [])]
    return str(t.get("status") or "") == "done"


def add_todo(title: str, date: str, start: str, end: str,
             note: str = "", category: str = "",
             repeat: str = REPEAT_NONE, weekday: int = 0) -> dict:
    """新增一条待办（人话：把商量好的安排写进日程）。

    :param date: 日期，形如 "2026-09-25"；repeat=weekly 时它是**第一次出现**的那天（锚点）
    :param start / end: 起止时间，形如 "14:00" / "15:30"
    :param repeat: "" 一次性 / "weekly" 每周重复 / "daily" 每天的习惯（见 `HABIT_REPEATS`）
    :param weekday: 1..7；不传就从 date 推出来（"每个周天"这类话里日子已经定了）；daily 用不上
    """
    data = _sread("todos.json", {"todos": []})
    if str(repeat or "") not in REPEAT_VALUES:
        repeat = REPEAT_NONE
    item = {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "date": date,
        "start": start,
        "end": end,
        "note": note,
        "category": category,
        "status": "planned",       # planned（待办） / done（已完成）—— 一次性待办用这个
        "created_at": _now(),
    }
    if repeat == REPEAT_DAILY:
        # 习惯不需要 weekday（它每天都是那天），但必须给 done_dates ——
        # 少了这个空列表，勾掉今天时那一路读不到键，会去翻整条 status（明天就没了）。
        item["repeat"] = repeat
        item["done_dates"] = []
    elif repeat == REPEAT_WEEKLY:
        wd = int(weekday) if weekday else (iso_weekday_of(date) or 0)
        item["repeat"] = repeat
        item["weekday"] = wd if 1 <= wd <= 7 else 0
        item["done_dates"] = []
    data.setdefault("todos", []).append(item)
    _swrite("todos.json", data)
    return item


def list_todos(date: str | None = None) -> list:
    """列出待办；给了 date 就只看那一天（**含重复/习惯落到那天的**，见 `todo_occurs_on`），按开始时间排序。

    ⚠️ 这里是"这一天有哪些事"，不是"日历上画什么"：习惯（daily）照样在列，
    要画日历的调用方自己按 `is_habit` 滤（`list_todos_in_month` 就是那么做的）。
    """
    todos = _sread("todos.json", {"todos": []}).get("todos", [])
    if date:
        todos = [t for t in todos if todo_occurs_on(t, date)]
    return sorted(todos, key=lambda t: (t.get("date", ""), t.get("start", "")))


def list_todos_in_month(prefix: str) -> dict:
    """按日期聚合某个月的待办（人话：给月视图算每天的待办数量）。

    :param prefix: 形如 "2026-09"
    :return: {"2026-09-24": [待办1, 待办2], ...}
    ⚠️ 2026-10-10 改：原来按 `date.startswith(prefix)` 分桶，那条写法**装不下重复待办**
    （一条记录只有一个 date，却要在月里出现 4~5 次）。现在改成"逐天问在不在"，
    一次性待办的归属跟以前完全一样。
    """
    from datetime import date as _date, timedelta

    grouped: dict[str, list] = {}
    try:
        y, m = (int(x) for x in str(prefix).split("-")[:2])
        day = _date(y, m, 1)
    except (ValueError, TypeError):
        return grouped
    todos = [t for t in list_todos() if not is_habit(t)]
    # ⚠️ 这一行滤的是**习惯**：`todo_occurs_on` 对 daily 天天都返回 True（那是"今天要做的
    # 一件事"的真话），而这个函数只服务日历月表的格子。习惯不进日历（用户 2026-10-13 拍板），
    # 所以在取数这一侧滤掉，而不是叫 `todo_occurs_on` 对 daily 撒谎 —— 后者会把代办角标一起算没。
    while day.month == m:
        iso = day.isoformat()
        hit = [t for t in todos if todo_occurs_on(t, iso)]
        if hit:
            grouped[iso] = sorted(hit, key=lambda t: t.get("start", ""))
        day += timedelta(days=1)
    return grouped


def get_todo(tid: str) -> dict | None:
    """按 id 查一条待办。"""
    for t in _sread("todos.json", {"todos": []}).get("todos", []):
        if t.get("id") == tid:
            return t
    return None


def update_todo(tid: str, patch: dict) -> dict | None:
    """改动一条待办的部分字段（比如把状态改成 done）。"""
    data = _sread("todos.json", {"todos": []})
    for i, t in enumerate(data.get("todos", [])):
        if t.get("id") == tid:
            t.update(patch)
            data["todos"][i] = t
            _swrite("todos.json", data)
            return t
    return None


def done_date_for(t: dict, on: str = "") -> str:
    """勾"完成"该记在**哪一天**（判据只写这一处，两个确认入口都问它）。

    传了日子就照他说的记；没传就记今天 —— 但今天不在它的出现日里
    （锚点还没到 / 已经过了 3 个月上限）就退到锚点那天，
    别把完成记到一个根本没安排过的日子上。
    """
    if on:
        return str(on)
    today = datetime.date.today().isoformat()
    return today if todo_occurs_on(t, today) else str((t or {}).get("date") or "")


def set_todo_done_on(tid: str, date: str, done: bool = True) -> dict | None:
    """把"完成/撤销完成"**落到具体某一天**。

    为什么单独一个函数而不是让调用方直接 patch status：
    重复待办的"这周做完了"和"以后都不做了"是两件事，前者只能记在那一天上
    （见 `done_dates`）；一次性待办没有"哪一天"的概念，仍旧翻 status。
    ⚠️ 2026-10-13 改：按天记的那一档（weekly/daily）**没传 date 也要按天记**
      （退到 `done_date_for` 算出来的那天）。原来"没传就整条翻 status"这条老规矩
      对它们是**空操作**：读侧一律问 `done_dates`，status 写了没人读 ——
      真机上"背单词完成了"点完确认，明天那条照旧挂着，就是这个洞。
    一次性待办（repeat 为空）行为一字未变。
    """
    data = _sread("todos.json", {"todos": []})
    for i, t in enumerate(data.get("todos", [])):
        if t.get("id") != tid:
            continue
        if str(t.get("repeat") or "") in DAY_LOGGED_REPEATS:
            days = [str(x) for x in (t.get("done_dates") or [])]
            d = done_date_for(t, date)
            if done and d not in days:
                days.append(d)
            elif not done:
                days = [x for x in days if x != d]
            t["done_dates"] = sorted(days)
            # 整条 status 对重复待办没有意义，但别让它留着"done"骗人：
            # 只要还有没完成过的周，这条就该是 planned。这里不做"是否全做过"的推断，
            # 判完成一律走 todo_done_on(t, date)。
            t["status"] = "planned"
        else:
            t["status"] = "done" if done else "planned"
        data["todos"][i] = t
        _swrite("todos.json", data)
        return t
    return None


def delete_todo(tid: str) -> bool:
    """删一条待办。"""
    data = _sread("todos.json", {"todos": []})
    todos = data.get("todos", [])
    keep = [t for t in todos if t.get("id") != tid]
    if len(keep) == len(todos):
        return False
    data["todos"] = keep
    _swrite("todos.json", data)
    return True


# ---- 会话历史：让 Agent 记得住上一轮商量到哪了 ----

def get_conversation(sid: str) -> list:
    """读某个会话的历史消息（只返回最近 MAX_HISTORY 条）。"""
    data = _sread("sessions.json", {"sessions": {}})
    msgs = data.get("sessions", {}).get(sid, [])
    return msgs[-MAX_HISTORY:]


def append_conversation(sid: str, role: str, content: str) -> None:
    """往会话里追加一条消息。"""
    data = _sread("sessions.json", {"sessions": {}})
    sessions = data.setdefault("sessions", {})
    sessions.setdefault(sid, []).append({"role": role, "content": content})
    # 只留最近这些条，防止文件无限长大
    sessions[sid] = sessions[sid][-MAX_HISTORY:]
    _swrite("sessions.json", data)


def clear_conversation(sid: str) -> None:
    """清空某个会话（人话：前端需要"重新开始一段对话"时用）。"""
    data = _sread("sessions.json", {"sessions": {}})
    data.get("sessions", {}).pop(sid, None)
    _swrite("sessions.json", data)


# ============ 模型用量记账（JSONL 流水） ============
# 为什么用 JSONL 而不是 JSON 数组：记账是"只追加"的流水，
# 一行一条，写坏了也只丢一行，不会让 json.load 把整份统计一起崩掉。
# 硬要求：**记账失败绝不能影响对话**——下面每个函数内部都自己吞异常，
# 网关那边是 try/finally 包着，这里再兜一层底。

USAGE_FILE = "llm_usage.jsonl"


def _usage_path() -> str:
    """模型流水账的位置。

    `CAMPUSTIME_USAGE_FILE` 有值就直取它（绝对路径），否则跟着数据目录走 ——
    数据目录本身每次调用重读 env，所以测试沙箱（`CAMPUSTIME_DATA_DIR`）一改，账本就跟着进临时目录。
    那第一条 env 是给**另一种场景**准备的：跑测试但**没进沙箱**的那几批
    （润色/网关自检那种直接记一笔的），光靠数据目录隔离不住，会把桩模型 `m` 写进真实账本
    （2026-10-15 实测真实账本里 760 笔 `model:"m"`，630 笔来自话术润色那族）。
    ⚠️ 生产不设这个 env，所以生产记账仍落在数据目录下那份账本（管理端读的就是它，
    test_50 B 批专门钉这条回落 —— 把生产一起隔离掉等于让管理端静默失明）。
    """
    return os.environ.get("CAMPUSTIME_USAGE_FILE") or _path(USAGE_FILE)


def log_llm_usage(record: dict) -> None:
    """追加一条模型调用流水（人话：这次调用谁发起的、用了哪个模型、
    花了多少 token、成功没有、耗时多久）。

    这个函数**对外承诺不抛异常**：记账是旁路，对话才是主线。
    """
    try:
        # 建的是**账本所在的那个目录**：env 指到别处时，`_data_dir()` 那个目录可能根本不该建
        os.makedirs(os.path.dirname(_usage_path()) or ".", exist_ok=True)
        row = dict(record or {})
        row.setdefault("ts", _now())
        with open(_usage_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_llm_usage(limit: int = 0) -> list:
    """读流水（limit>0 时只返回**最近** limit 条，按写入顺序）。

    坏行直接跳过——一条脏数据不该毁掉整份统计。
    """
    rows = []
    try:
        with open(_usage_path(), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        return []
    except Exception:
        return rows
    if limit and limit > 0:
        rows = rows[-limit:]
    return rows


def _as_int(v) -> int:
    """把 token 数安全地变成整数（None / 空串 / "123" 都要能扛住）。"""
    try:
        return int(v)
    except Exception:
        return 0


def summarize_llm_usage(limit_recent: int = 50) -> dict:
    """汇总用量（人话：给管理台那张"用量统计"卡片算好所有数字）。

    返回结构：
      total_calls / ok_calls / error_calls
      prompt_tokens / completion_tokens / total_tokens
      avg_latency_ms（只算成功的那些，失败往往很快，混进来会把均值拉低）
      users（去重后的调用方个数）
      models: [{model, calls, tokens, share}]   share 是百分比(0-100)，按调用量降序
      recent: 最近 limit_recent 条流水，**最新在前**
      last_ts: 最后一条的时间，没有流水时是空串
    """
    rows = read_llm_usage()
    total = len(rows)
    ok = err = 0
    p_tok = c_tok = 0
    lat_sum = lat_n = 0
    by_model = {}
    users = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "")
        if status == "ok":
            ok += 1
        else:
            err += 1
        pt = _as_int(r.get("prompt_tokens"))
        ct = _as_int(r.get("completion_tokens"))
        if not pt and not ct:
            tt = _as_int(r.get("total_tokens"))
            p_tok += tt
        else:
            p_tok += pt
            c_tok += ct
        if status == "ok":
            lt = _as_int(r.get("latency_ms"))
            if lt > 0:
                lat_sum += lt
                lat_n += 1
        m = str(r.get("model") or "unknown")
        slot = by_model.setdefault(m, {"model": m, "calls": 0, "tokens": 0})
        slot["calls"] += 1
        slot["tokens"] += _as_int(r.get("total_tokens")) or (pt + ct)
        u = r.get("user")
        if u:
            users.add(str(u))
    models = sorted(by_model.values(), key=lambda x: (-x["calls"], x["model"]))
    for slot in models:
        slot["share"] = round(slot["calls"] * 100.0 / total, 1) if total else 0.0
    recent = rows[-(limit_recent or 0):] if (limit_recent and limit_recent > 0) else []
    recent = list(reversed(recent))
    return {
        "total_calls": total,
        "ok_calls": ok,
        "error_calls": err,
        "prompt_tokens": p_tok,
        "completion_tokens": c_tok,
        "total_tokens": p_tok + c_tok,
        "avg_latency_ms": int(lat_sum / lat_n) if lat_n else 0,
        "users": len(users),
        "models": models,
        "recent": recent,
        "last_ts": str(rows[-1].get("ts") or "") if rows else "",
    }


# ============ 驿站低代码：一句话生成的「服务定义」与它的登记记录（#18 单子 3） ============
# 三件事先想清楚，注释留在这儿，因为这是本项目**创新点落地**的那块，评审一定会问：
#   ① 模型在这里只产**数据**（一份服务定义），绝不产代码 —— 这条路没有 eval/exec/动态导入，
#      生成物只能过白名单校验、再经确定性接口生效（红线 4「模型碰不到写库」的延伸）。
#   ② 每个字段都要白名单校验（未知字段 / 超长 / 带尖括号一律拒）：
#      "能新增服务"如果不带校验，等于"能往别人页面里注入任意字符串"。
#   ③ 服务定义是**运行时数据**（与 intent_samples.json 同性质）：内置种子写在代码里，
#      文件删了能重新长出来；驿站管理员现场试出来的句子也不进仓库。

SERVICES_FILE = "services.json"
CUSTOM_DIR = "custom"

# 登记记录的**新记录初始态** —— 与工单共用同一套状态词（合法值的唯一来源是 main._WO_STATUSES）。
# 这里只写这一个字面量：由护栏钉住"它就是那三态里的第一个"，跨层不许各写一套状态表。
CUSTOM_STATUS_NEW = "待处理"

# 第 1 项内置服务就是原来那句"快递/外卖问题处理" —— 它现在也走同一张表，
# 这样"低代码新增"不是特例，而是这套结构的第 2、第 3 项。
_SEED_SERVICES = [
    {
        "id": "pkgloss",
        "name": "快递/外卖问题处理",
        "triggers": ["快递丢了", "包裹没找到", "外卖丢失", "取件码查不到"],
        "collect_fields": ["单号或取件码", "大概时间", "丢失地点"],
        "reply_template": "已登记，驿站会按你留的信息去找，找到后放驿站等你取。",
        "table": "workorders",
        "created_at": "内置",
    },
]

# 字段白名单：不在表里的键一律拒（"多给一个字段"不能变成"多存一个字段"）
SERVICE_FIELDS = {
    "name": (1, 20),
    "triggers": (1, 10),
    "collect_fields": (1, 8),
    "reply_template": (1, 200),
    "table": (1, 20),
}
SERVICE_ITEM_MAX = 24          # 列表里每一项的字符上限
CUSTOM_VALUE_MAX = 60          # 一条登记里每个字段值的上限


def list_services() -> list:
    """服务定义列表；文件不在或坏了就用内置种子（删了也能长回来，不靠提交数据）。"""
    rows = _read(SERVICES_FILE, None)
    if not isinstance(rows, list) or not rows:
        return [dict(s) for s in _SEED_SERVICES]
    return rows


def get_service(sid: str) -> dict | None:
    return next((s for s in list_services() if s.get("id") == sid), None)


def _bad_service_field(key: str, val) -> str:
    """单字段校验，返回错误原因（空串 = 通过）。"""
    lo, hi = SERVICE_FIELDS[key]
    if isinstance(val, list):
        if not (lo <= len(val) <= hi):
            return f"{key} 数量要在 {lo}~{hi} 条之间"
        for v in val:
            if not isinstance(v, str) or not v.strip():
                return f"{key} 里每一项都得是非空文字"
            if len(v) > SERVICE_ITEM_MAX:
                return f"{key} 里有超过 {SERVICE_ITEM_MAX} 字的项"
            if "<" in v or ">" in v:
                # 前端本来就一律 esc()（全站唯一转义来源），这里再拦一道：
                # 带标签的字符串不该有机会进到任何展示路径
                return f"{key} 里不许带尖括号"
        return ""
    if not isinstance(val, str) or not val.strip():
        return f"{key} 要写成非空文字"
    if not (lo <= len(val) <= hi):
        return f"{key} 长度要在 {lo}~{hi} 之间"
    if "<" in val or ">" in val:
        return f"{key} 里不许带尖括号"
    return ""


def validate_service_def(obj: dict) -> tuple:
    """候选服务定义过白名单。返回 (规范化后的定义, 错误原因)，错误时定义是 None。

    为什么是"拒"而不是"尽力修好"：这份定义是**模型按驿站管理员一句话生成**的，
    它多塞字段、把整段描述塞进 name、顺手写一段 HTML，都不该被默默接受 ——
    存进去之后每一个读它的界面都要重新踩一次坑。
    """
    if not isinstance(obj, dict):
        return None, "服务定义得是一个对象"
    extra = [k for k in obj if k not in SERVICE_FIELDS and k != "id"]
    if extra:
        return None, "有不认识的字段：" + "、".join(sorted(extra)[:4])
    clean: dict = {}
    for key in SERVICE_FIELDS:
        val = obj.get(key)
        if isinstance(val, list):
            val = [str(v).strip() for v in val if str(v).strip()]
        elif isinstance(val, str):
            val = val.strip()
        why = _bad_service_field(key, val)
        if why:
            return None, why
        clean[key] = val
    taken = {str(s.get("name") or "") for s in list_services()}
    if clean["name"] in taken:
        return None, f"已经有一个叫「{clean['name']}」的服务了，换个名字或先删掉它"
    clean["id"] = (str(obj.get("id") or "").strip() or uuid.uuid4().hex[:8])
    clean["created_at"] = _now()
    return clean, ""


def add_service(defn: dict) -> dict:
    """存一份**已经过校验**的服务定义（校验只在 validate_service_def 一处，别写第二份规则）。"""
    rows = list_services()
    rows.append(defn)
    _write(SERVICES_FILE, rows)
    return defn


def is_builtin_service(svc) -> bool:
    """内置那项「快递/外卖问题处理」——判据是 `table`，**不是 id**。
    #39-A 之前这个字面量在 `match_service_by_trigger` 里孤本一份；现在删与路由共用一条：
    两处各写各的，将来加第二个 workorders 型服务就只会有一边认它。
    """
    return str((svc or {}).get("table") or "") == "workorders"


def delete_service(sid: str) -> bool:
    """删一份服务**定义**（#39-A：低代码只长不删，管理员建错了没法收）。

    三条不许顺手改的地方：
      ① **内置项拒删**：它是"学生说快递丢了 → 出工单上报卡"那条链路的路由标记
         （见 `match_service_by_trigger`），删了那条链路就断（test_54 钉着）。
         前端同时不给按钮，两道都要 —— 后端那道才是真的，前端只是让人不必点。
      ② **只动 services.json，不动 `custom/<sid>.json`**：删定义 ≠ 删历史台账。
         台账是运营记录，没了找不回来；而定义删错了下一句话就能再建一项。
      ③ 删完不许把列表清空：`list_services()` 在文件为空时会回**内置种子**，
         于是"删到空"表现成"东西又长回来了"。①那道拒删顺带把这个形状堵死
         （内置项永远留在表里），所以这里不再单独兜底。
    """
    if is_builtin_service(get_service(sid)):
        return False
    rows = list_services()
    kept = [s for s in rows if str(s.get("id")) != str(sid)]
    if len(kept) == len(rows):
        return False
    _write(SERVICES_FILE, kept)
    return True


# ── 低代码服务在学生端的"名字"（#22 单子 4：接进判定层）───────────────────
# module 用 `custom:<服务id>` 这种带前缀的写法，而不是复用 lostfound/repair 那种固定键：
# 判定层给的 service_id **必须回查 services.json 才算数**，模型编一个名字就直接丢弃 ——
# 否则"模型说这句归哪个服务管"就成了新的入口，红线 1 白守。
CUSTOM_MODULE_PREFIX = "custom:"


def custom_module(sid: str) -> str:
    """服务 id → 判定层用的 module 名。"""
    return f"{CUSTOM_MODULE_PREFIX}{sid}"


def service_of_module(module: str) -> dict | None:
    """module 名 → 服务定义。不是低代码模块、或**服务不存在**，一律回 None。"""
    m = (module or "").strip()
    if not m.startswith(CUSTOM_MODULE_PREFIX):
        return None
    return get_service(m[len(CUSTOM_MODULE_PREFIX):])


def is_custom_module(module: str) -> bool:
    return service_of_module(module) is not None


def match_service_by_trigger(text: str) -> dict | None:
    """关键词兜底那一档：这句话里出现了哪个服务的触发词（**最长的那个**算赢）。

    只读 `services.json`，零成本、离线也能跑。
    ⚠️ 内置那项「快递/外卖问题处理」(table=workorders) **不参与**这句到卡片的自动路由：
    它描述的是"上报工单"那条**已有链路**（工单落 workorders.json、由驿站处理），
    而这里命中了会去挂"登记一条"的卡、写 custom/<id>.json —— 两套台账不是一回事。
    真机上「快递丢了」必须照旧出工单上报卡，这条由 test_54 钉着。
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    best: tuple | None = None
    for svc in list_services():
        if is_builtin_service(svc):
            continue
        for trig in (svc.get("triggers") or []):
            s = str(trig or "").strip().lower()
            if s and s in t and (best is None or len(s) > best[0]):
                best = (len(s), svc)
    return best[1] if best else None


def list_custom_records(sid: str) -> list:
    """某个自定义服务下已登记的记录（倒序）。"""
    rows = _read(os.path.join(CUSTOM_DIR, f"{sid}.json"), [])
    return list(reversed(rows)) if isinstance(rows, list) else []


def add_custom_record(sid: str, fields: dict, svc: dict | None = None,
                      status: str = "") -> dict:
    """登记一条到 `custom/<service_id>.json`。

    ⚠️ 这条路径**只允许确定性接口调用**（驿站管理员点【登记】这个明确动作）。
    不给模型注册任何写这里的工具 —— 一注册，"低代码生成的服务"就自带写库能力了。

    `status` 是**流程状态**、不是学生填的内容：默认由系统给「待处理」，
    它绝不出现在 `collect_fields` 里（进了会被 `_custom_field_errors` 判成 stray 字段直接 400）。
    """
    svc = svc or get_service(sid) or {}
    want = [str(x) for x in (svc.get("collect_fields") or [])]
    clean: dict = {}
    for k in want:
        v = str((fields or {}).get(k) or "").strip()
        clean[k] = v[:CUSTOM_VALUE_MAX]
    item = {"id": uuid.uuid4().hex[:8], "ts": _now(), "service": sid,
            "name": svc.get("name") or "", "fields": clean,
            "status": str(status or CUSTOM_STATUS_NEW)}
    rows = _read(os.path.join(CUSTOM_DIR, f"{sid}.json"), [])
    if not isinstance(rows, list):
        rows = []
    # `_write` 只保证数据根目录在，子目录得自己建（第一次登记时 custom/ 还不存在）
    os.makedirs(_path(CUSTOM_DIR), exist_ok=True)
    rows.append(item)
    _write(os.path.join(CUSTOM_DIR, f"{sid}.json"), rows)
    return item


def delete_custom_record(sid: str, rid: str) -> bool:
    """删一条登记记录（#39-B）。**真删**，不是软删。

    为什么这里不像工单那样做归档：登记记录没有"归档"那一档（`status` 只有
    待处理/处理中/已解决，与工单共用 `_WO_STATUSES`），台账里留一条没处理的旧登记
    只是让下一班同学再点一次。所以前端必须二次确认、措辞直接说"删了台账里就没了"。

    ⚠️ `sid` 会被拼进文件路径 —— **调用方必须先确认这项服务存在**（`get_service`）。
       这一层不做那个校验（与 `list_custom_records` / `add_custom_record` 同一分工：
       路由认服务，store 只管那份文件），别在两个地方各校验一遍，但要有一处真的拦住。
    """
    path = os.path.join(CUSTOM_DIR, f"{sid}.json")
    rows = _read(path, [])
    if not isinstance(rows, list):
        return False
    kept = [x for x in rows if str(x.get("id")) != str(rid)]
    if len(kept) == len(rows):
        return False
    _write(path, kept)
    return True


def update_custom_record_status(sid: str, rid: str, status: str) -> dict | None:
    """改一条登记记录的处理状态；找不到那条记录返回 None。

    形状与 `update_workorder_status` 一致（读全量 → 改一条 → 整份写回），
    老记录**没有** `status` 键也照改 —— 写进去就把它补上了，不做批量迁移。
    不建子目录：能读到 `rows` 就说明 `custom/` 已经在（读不到时 `_read` 给默认 `[]`，那一支直接 None）。
    """
    path = os.path.join(CUSTOM_DIR, f"{sid}.json")
    rows = _read(path, [])
    if not isinstance(rows, list):
        return None
    for it in rows:
        if str(it.get("id") or "") == str(rid):
            it["status"] = status
            _write(path, rows)
            return it
    return None


def clear_llm_usage() -> int:
    """清空流水（人话：管理员点"清空"按钮）。返回被清掉多少条。"""
    n = len(read_llm_usage())
    try:
        with open(_usage_path(), "w", encoding="utf-8"):
            pass
    except Exception:
        return 0
    return n
