"""简单文件存储（人话：把学生消息和通知存成 JSON 文件，管理员端能读、能发）。

为什么用文件而不是数据库？演示阶段要的就是"看得懂、改得动"，
每个文件就是一张表：student_messages.json = 学生消息表，notices.json = 通知表。
真上线时把这里换成数据库即可，接口不变。
"""
import contextvars
import datetime
import json
import os
import re
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


def _append_trail(row: dict, status: str, by: str = "") -> None:
    """给一条记录追加一次状态变更（**原地改**）。全库唯一写"进度"的地方。

    为什么是数组、不是 `created_at/updated_at/resolved_at` 三个散字段：
      · 三个字段只能表达"最后一次"，学生问「什么时候开始处理的」就没有答案；
      · 每加一档状态就得再加一个字段（处理中 ⇒ processing_at），正是"加一档改两处"那族病；
      · 处理时效（平均几小时解决）直接从这个数组算，不用再动结构。

    :param by: 谁改的。**目前一律传 `""`** —— 管理端只有口令、驿站端免口令，
        都没有"哪个人"这个身份。宁可留空显示「管理员」，也不许编一个名字。
        将来真加了账号再往这里填，**不动结构**。
    ⚠️ 老记录不回填：没有 trail 的，由读取端兜底成"只有创建那一条"
        （见 `my_reports` / 驿站与后勤的台账渲染）—— **不编造中间过程**，
        与 `status` / `archived` 那几单的一贯做法相同（回填=编造历史）。
    """
    row.setdefault("trail", []).append({"status": str(status), "ts": _now(), "by": str(by or "")})
    # `updated_at` 与 trail 末条同时写：只 append 不写这一行，"最后动过"就永远是空的，
    # 而前端读到空是**静默显示空白**（比报错难发现，#54 变异 M7 钉的就是这一行）。
    row["updated_at"] = row["trail"][-1]["ts"]


def trail_of(row: dict) -> list:
    """这条记录**真发生过**的状态变更（读取端**唯一**取法）。

    老记录没有 `trail` ⇒ 回**空表**，绝不在这里合成一条"看起来像历史"的假记录：
    编造历史比没有历史更坏（#54 变异 M8 撞的就是这一处），要不要退化成
    "一行状态 + 创建时间"由**视图**决定，判据（有没有真发生过变更）只写在这儿。
    三张脸（管理端后勤、驿站台账、学生「我的上报」）都读它，各写一遍就会出现
    "这一栏有进度、那一栏没有"。
    """
    tr = (row or {}).get("trail")
    if not isinstance(tr, list):
        return []
    return [t for t in tr if isinstance(t, dict)]


def update_workorder_status(wo_id: str, status: str, by: str = "") -> dict | None:
    """管理员处理工单后改状态（待处理 / 处理中 / 已解决）。"""
    items = _read("workorders.json", [])
    for it in items:
        if it.get("id") == wo_id:
            it["status"] = status
            _append_trail(it, status, by)
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

# 检索时不该当关键词的虚词/疑问词（只列真正会出现在校园问句里的那些，别抄一整本停用词表）
# 「什么时候」「有没有」是 #55-C 实测补进来的：缺它们时「图书馆什么时候关门」劈出
# `['时候关门','图书馆']`、「宿舍有没有热水」整句不劈 —— 残渣比整句更坏（整句至少带完整语义，
# 「时候关门」会被滑窗拆成「时候关」「时关门」这种噪声去匹配）。
# ⚠️ **别把「多久」加进这张表**（它该在下面的 `_KB_GENERIC` 里）：这张表被 `_kb_split` 拿去
#    **劈词**，加了「多久」之后「图书馆能待多久」会劈成 `['图书馆能待']` —— 引入新残渣，更坏；
#    而 `_KB_GENERIC` 只被 `_kb_clean` 用来**过滤**（整词相等才丢），不改变任何劈词结果。
#    两边实测过：只加进 GENERIC 时「图书馆能待多久」的输出与加之前一模一样。
_KB_STOP = ("什么", "怎么", "怎样", "多少", "几点", "哪里", "在哪", "哪个", "哪些",
            "如何", "可以", "能不能", "是否", "请问", "一下", "的话",
            "什么时候", "有没有",
            "的", "了", "吗", "呢", "啊", "呀", "吧", "我", "你", "要")

# 两字的通用动宾/泛词：它们几乎能粘上任何问句（「食堂几点开门」也被「开门」粘上），
# 自动派生时丢掉；⚠️ **管理员手写的关键词不受这张表影响**（那是他的判断，不该被代码覆盖）。
# 「今天/明天/星期几」是本轮实测补进来的：派生词里留着「今天」的话，
# 任何带"今天"的问句都会把那条「今天星期几」拽进学生气泡 —— 这三字在任何问句里都不带话题。
# 后三个（#55-C，都是**实测漏网的**，不是推的）：
#   「开放」—— 表里已有「开门/关门」却偏偏没有它，「游泳馆什么时候开放」就占掉一个名额；
#   「多少钱」—— `_KB_STOP` 里只有「多少」，整词「多少钱」原样通过 ⇒ 这条被放宽到所有问价钱的句子；
#   「多久」—— 同上，而且它**只能**加在这张表里（理由见上面 `_KB_STOP` 那条 ⚠️）。
_KB_GENERIC = {"开门", "关门", "时间", "电话", "地点", "位置", "预约", "收费", "费用",
               "价格", "流程", "条件", "要求", "办理", "申请", "发车", "断电", "熄灯",
               "网址", "密码", "东西", "今天", "明天", "星期几",
               "开放", "多少钱", "多久"}

# 紧跟着上面那张表生成，顺序不能挪：这张表一改，这里就重算一遍（放在别处就是"改了词表、没改正则"
# 那种静默失效的形状 —— 表现是"改了等于没改"，最难查）。
_STOP_RE = "|".join(map(re.escape, sorted(_KB_STOP, key=len, reverse=True)))
_EDGE_RE = re.compile(r"^[在里与和或及等]+|[在里与和或及等]+$")


def _kb_windows(p: str) -> list:
    """一个词片补上 3 字、2 字的滑窗片段（短于 3 字的没什么可补）。"""
    if len(p) < 3:
        return [p]
    return [p] + [p[i:i + 3] for i in range(len(p) - 2)] + [p[i:i + 2] for i in range(len(p) - 1)]


def _kb_clean(words, limit: int) -> list:
    """去重 + 去虚词/泛词 + 长的在前，最后按 `limit` 截。"""
    seen, uniq = set(), []
    for w in sorted(words, key=len, reverse=True):
        if w and w not in seen and w not in _KB_STOP and w not in _KB_GENERIC:
            seen.add(w)
            uniq.append(w)
    return uniq[:limit]


def _kb_split(question: str) -> list:
    """按虚词把一句问题劈成**词片** —— 这套"分词"的全部实现就这一处，别在别处再写第二遍。"""
    q = re.sub(r"[^\w一-鿿]+", " ", str(question or "")).strip()
    out: list = []
    for w in (x for x in re.split(r"\s+", q) if x):
        if w in _KB_STOP:
            continue
        parts = [_EDGE_RE.sub("", p) for p in re.split(_STOP_RE, w)]
        parts = [p for p in parts if len(p) >= 2]
        out.extend(parts or ([w] if len(w) >= 2 else []))
    return _kb_clean(out, len(out) or 1)


def derive_kb_keywords(question: str, limit: int = 12, pieces: bool = False) -> list:
    """从问题原文里抽检索关键词（人话：没填关键词的条目，也能用学生说的原句查到）。

    病根（#46-A，用户原话「我添加进去……即使学生问的原句也识别不到」）：
    `faq` 那条判据只看 `keywords`，`question` 一个字符都不参与匹配，而管理端那一栏**可以留空**
    （`admin.html` 只校验问题/答案）⇒ 留空 = 这条永远不可达，面板却回一句「✅ 已存」。

    **不引分词库**（与 `files._tokenize` 同一取舍：零额外依赖也能跑起来）。
    三步：按虚词劈成词片 → 每个词片再补 3 字、2 字滑窗片段 → 去重去泛词、长的在前。
    为什么要滑窗：「考试周图书馆开到几点」劈出来是整片，学生却只会说「考试周图书馆几点关」
    —— 补了 3 字片段才含「图书馆」这种真正的实体词。

    ⚠️ `pieces=True`：**只要词片本身、不补滑窗片段**，因为「打错字」那一圈不许吃片段。
    这不是理论风险，是实测撞出来的（#46-A 第一版两圈都用全量派生）：
    条目「今天星期几」派生出「今天星」，而学生问「今天有什么课」里有个窗口「今天有」——
    差一个字 ⇒ `_kb_fuzzy_hit` 判命中 ⇒ 一句日程问话被知识库直答成「看日历」。
    这正是 `_kb_fuzzy_hit` 注释里那条「校车 vs 校长」真踩过的坑的**新来源**：
    手写的关键词是管理员挑过的实词，滑窗碎片是 n-gram 噪声，噪声不该享受编辑距离容错。

    实测（2026-10-20，本机跑的，不是设计稿）：
      「图书馆几点开门」→ 图书馆, 图书, 书馆      （pieces=True 只剩：图书馆）
      「校车在哪里坐、几点发车？」→ 校车
      「补办学生证多少钱」→ 补办学生证, 补办学, 办学生, 学生证, 补办, 办学, 学生, 生证
      「考试周图书馆开到几点」→ 考试周图书馆开到, 考试周, 试周图, 周图书, 图书馆, 书馆开,
        馆开到, 考试, 试周, 周图, 图书, 书馆   ← 十二项，第 13 项「馆开」被 `limit` 截掉
    ⚠️ 抄这几行进断言前**先跑一遍**：施工单里那一行就把 `书馆开` 写成了 `馆开`。
    """
    ps = _kb_split(question)
    return ps[:limit] if pieces else _kb_clean(ps + [x for p in ps for x in _kb_windows(p)], limit)


def kb_head_words(question: str, limit: int = 6) -> list:
    """一条问答条目**存进去的那几个词** = 词片本身（不存滑窗碎片）。

    三个理由，都不是审美：
    ① 管理端那一屏不会被十几个碎片刷满（施工单 §一.4 担心的那件事，这样连"显示前 6 个 + 后缀"都不用写）；
    ② 「打错字」那一圈只该吃**实词** —— 滑窗碎片是 n-gram 噪声，给它编辑距离容错就是把
       `derive_kb_keywords` 那条 ⚠️（「今天星」接走「今天有什么课」）重新放出来；
    ③ 存的那份与查的那份同源（`faq._entry_kws` 拿它去 `kb_expand`），管理员看得出这条靠什么命中。
    """
    return _kb_split(question)[:limit]


def kb_expand(words, limit: int = 12) -> list:
    """把一组词片补上滑窗片段 —— **精确匹配那一圈**用它（手写的与派生的走同一条路）。"""
    ws = [str(w).strip().lower() for w in (words or []) if str(w).strip()]
    return _kb_clean(ws + [x for w in ws for x in _kb_windows(w)], limit)


def clean_split_words(question: str, obj) -> tuple:
    """模型给的拆词结果过一遍闸（#55-B）。返回 `(可用词表, 不可用时的人话原因)`。

    为什么这个函数在 store、不在路由：判断要用的 `_KB_STOP` / `_KB_GENERIC` 是这里的词表，
    跟着词表走才不会出现"路由直接 import 别人的私有名"那种越界（红线：判据跟着数据走）。

    这里每一道都是**实测过会出错**的形状，不是防御性编程的仪式：
      · 模型偷懒时把原句还回来 —— 现在 `kb.json` 里那条 `["游泳馆价格"]` 就是这个 bug 的样子，
        所以"词 == 原句"必须丢；全丢就**报错**，不许存进去（存进去下一圈还是查不到）。
      · 泛词（`_KB_GENERIC`）会让这条放宽到所有含该词的问句（「食堂价格」也命中「游泳馆价格」），扔掉；
        但扔完一个不剩时报错，**不许存空关键词** —— 空的会走 `_kb_row` 兜底，又变回整句，绕一圈回原点。
      · 长度 2~8、尖括号：前端 `esc()` 之外再拦一道（同 `_bad_service_field` 的分工）。
    ⚠️ 比较前先 `norm()` 把标点吃掉：模型很爱在词后面带个问号（「游泳馆价格？」），
       而原句里也带一个 —— 不去掉就判不出"这就是原句"，而它一旦存进去，A 那一层**也不会再救它**
       （那时 `hand != kb_head_words` ⇒ 按手写处理），等于这个 bug 原地复活。
    ⚠️ 不要拿 `_kb_clean` 顶替这个函数：它会按长度排序并截断、还把 2 字词拿去和泛词比 ——
       那是给**自动派生**用的口径，与"审核模型给的建议词"不是一件事。
    """
    raw = obj.get("keywords") if isinstance(obj, dict) else None
    if not isinstance(raw, list) or not raw:
        return [], "模型没给出关键词，请手动填写。"

    def norm(s):
        return re.sub(r"[^\w一-鿿]+", "", str(s or "")).lower()

    q = norm(question)
    out, seen = [], set()
    for w in raw:
        w0 = str(w or "").strip()
        # 尖括号在**去标点之前**看：norm() 会把 `<b>x</b>` 洗成「bx b」，
        # 那样这条判据永远不咬（施工单那一版就是这个顺序），而"带标签的建议"
        # 该被丢掉、不该被洗白成另一个能存的词。
        if "<" in w0 or ">" in w0:
            continue
        w = norm(w0)
        if not (2 <= len(w) <= 8):
            continue
        # 纯数字（含下划线那种）不是"检索关键词"：模型偶尔会回 `["12"]` 这种，
        # 而它会把任何带 12 的问句（「12点」「2026年」）都接走 —— 与泛词同一种放宽。
        if w.isdigit():
            continue
        if q and w == q:                              # 原句整串（含标点差异）
            continue
        if w in _KB_STOP or w in _KB_GENERIC:         # 虚词 / 泛词
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    if not out:
        return [], "模型这次只拆出了原句或泛词，换个说法再试一次，或者手动填关键词。"
    return out[:6], ""


def list_kb() -> list:
    """列出知识库全部条目。"""
    return _read("kb.json", {"entries": []}).get("entries", [])


def _kb_row(question: str, answer: str, keywords: list) -> dict:
    """一条问答条目的落盘形状 —— **存的时候兜底**在这里（#46-A 两处接上的第一处）。

    为什么 add/update 合成一个：这条兜底是"存"那一半，"查"那一半在 `faq._entry_kws`。
    同一判断抄两处就会漏一处，而漏掉的后果不是报错，是那条**永远查不到**（静默死条目）。
    ⚠️ 只兜**空**的：管理员手写的关键词一个字都不动（含 `_KB_GENERIC` 里那些泛词 —— 那是他的判断）。
    """
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    return {"question": question, "answer": answer, "keywords": kws or kb_head_words(question)}


def add_kb_entry(question: str, answer: str, keywords: list) -> dict:
    """新增一条知识库条目（人话：管理员在后台加一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    item = _kb_row(question, answer, keywords)
    entries.append(item)
    _write("kb.json", data)
    return item


def update_kb_entry(index: int, question: str, answer: str, keywords: list) -> dict:
    """按序号修改一条知识库条目（人话：管理员编辑一条问答）。"""
    data = _read("kb.json", {"entries": []})
    entries = data.setdefault("entries", [])
    if index < 0 or index >= len(entries):
        raise IndexError("知识库条目不存在")
    entries[index] = _kb_row(question, answer, keywords)
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


# 上传文件的用途（#40 补）。白名单**只在 store 判一次**（norm_purpose），
# 接口层再判一遍就是两份各说一份、迟早漂移：
#   file      —— 默认。学生聊天里传的资料、通知附件、驿站批次清单……都是"某个人给某句话用的材料"
#   kb        —— 管理端「文档知识库」这一档传进来的知识文档：会被切块、进检索、列在管理端
#   timetable —— 留的枚举位（学生课表严格说该归这一档），目前没有调用方在传，行为等同 file
# 为什么非分不可：`uploads.json` 是一个全局桶，而 #40 把"AI 读得到"升级成了"谁都查得到"。
# 不分用途，学生的个人课表就和管理手册混在同一份知识库列表里被切块 ——
# 而且文件名先泄一次（管理端一眼看见「2026秋季课表-软件工程25级3班」）。
PURPOSES = ("file", "kb", "timetable")


def norm_purpose(purpose) -> str:
    """把用途夹进白名单：非法值一律退回 "file"（既不让任意字符串进库，更不让它混进 kb）。"""
    p = str(purpose or "").strip().lower()
    return p if p in PURPOSES else "file"


def save_upload(filename: str, content: str, raw: bytes | None = None,
                purpose: str = "file", owner: str | None = None) -> dict:
    """保存一个上传文件（人话：既留原件供下载，也提取文字给 AI 读）。

    为什么要存两份？
    - 原件：通知的附件要能让学生下载回去（丢了就只剩摘要，体验不完整）
    - 文字：AI 真正能"看懂"的形态

    :param raw: 文件原始字节；不传就只有文字、没有原件可下载
    :param purpose: 见 PURPOSES —— **只有 "kb" 会被切块进检索**，别的一概不建索引
    :param owner: 这份文件**属于谁**（#45-B）。不传 = 取当前请求方的学号；
        管理员那条上传口**显式传 `owner=""`**（那是公共知识，不属于任何学生）。
        ⚠️ 不接受"前端爱填谁就填谁"——理由与 `add_workorder` 的 owner 一样：
        body 里谁都能改，能填就等于能冒充。身份只有 `current_student()` 这一个来源。
    """
    purpose = norm_purpose(purpose)
    owner = current_student() if owner is None else str(owner or "").strip()
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
    # 3) 分块索引：知识文档在上传当场就切好块，免得"传完了还得去后台点一下才查得到"（#40）。
    #    ⚠️ 这道 purpose 闸是本单的关键，不是装饰：#40 最初版是"凡是上传就切块"，
    #    于是学生传的课表**不用谁点一下就已经变成人人可检索的"知识"**了（#40 补 §一 那条比施工单
    #    写的还严重一档）。不建索引 = 既进不了 search_docs，也省掉一份没用的文件。
    #    ⚠️ 块数**不写进 uploads.json**：那份表是台账，写了就是两个地方各说一份块数
    #    （重建索引后旧数字立刻说谎）。列表页要块数，现读 `<fid>.chunks.json` 就行。
    if purpose == "kb":
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
        "purpose": purpose,
        "owner": owner,             # #45-B：空串 = 公共知识文档（管理员传的）或无主老数据
    }
    data = _read("uploads.json", {"files": []})
    data.setdefault("files", []).append(item)
    _write("uploads.json", data)
    return item


def list_uploads() -> list:
    """列出所有上传文件，倒序（最新在前）。

    ⚠️ 这是**"上传桶里有啥"**，不过滤归属 —— 给管理端与内部重建索引用。
    学生面前那一屏/那句工具话术要用 `visible_uploads()`，别拿这份直接回。
    """
    return list(reversed(_read("uploads.json", {"files": []}).get("files", [])))


def can_read_upload(meta) -> bool:
    """当前请求方能不能读这份上传文件（#45-B，**判据只有这一处**）。

    一句话口径：**管理员传的公共知识人人可读；学生传的只有本人可读。**

    实测逼出来的两条，施工单 §二 里都没有：
    ① **被通知引用的那份要放行**。`notices.json` 里唯一那份附件
      （`数据结构实验报告样例.docx`）是 `purpose=None / owner=None` ——
      只按"无归属 ⇒ 必须 kb"判，学生点通知附件会直接变 404，
      等于把"附件能下载"这个已上线功能当 collateral damage 删掉。
      引用关系本来就存在（`upload_notice_refs`，删文档那道闸在用），
      所以判"管理员已发布给全体学生"不需要新字段、也不引入第二份真相。
    ② **无归属的老数据按 `demo` 口径放行**，不是全盘拒。
      `current_owner()` 的既有约定就是"没身份（测试 / 演示脚本 / 没登录）= demo"，
      `visible_rows` 也是这么处理无 owner 老行的。照单子里那句"一律不放行"改，
      未登录演示那一路（#35 的空状态引导、`test_59` B 批钉着）和一批测试会一起红 ——
      那不等于把隔离做严了，只是把"没登录的人"关在自己刚传的文件外面。
    ⚠️ 两条都不放宽**学生之间**：有 owner 的行只认本人，无 owner 的非 kb 行只认 demo 口径，
    所以 admin1 传的那份 admin2 读不到，反之亦然（`test_72` B 批逐条钉）。
    """
    if not meta:
        return False
    who = str(meta.get("owner") or "")
    if who:
        return who == current_owner()
    if upload_purpose(meta) == "kb":
        return True
    if upload_notice_refs(str(meta.get("id") or "")):
        return True
    return current_owner() == DEMO_OWNER


def visible_uploads() -> list:
    """学生口径的上传列表（工具「我有哪些文件」与 `/api/uploads` 都用这一份）。"""
    return [f for f in list_uploads() if can_read_upload(f)]


def list_docs() -> list:
    """只列**知识文档**（purpose=="kb"），倒序。

    这是管理端「文档知识库」那一张表的数据源。为什么单开一个函数而不给 `list_uploads` 加参数：
    `list_uploads` 的语义就是"上传桶里有啥"，学生问「我传的文件」要的是那一份；
    把两个口径塞进一个函数，下一次有人顺手加个默认过滤就会把学生那边筛没。
    """
    return [f for f in list_uploads() if upload_purpose(f) == "kb"]


def upload_purpose(meta) -> str:
    """这份上传文件是什么用途。老记录没这个键 ⇒ 按 "file" 处理（**不批量迁移**，同 status/archived 那两单）。"""
    if isinstance(meta, str):
        meta = get_upload_meta(meta)
    return norm_purpose((meta or {}).get("purpose"))


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

    ⚠️ 只吐知识文档（purpose=="kb"）：这是"检索面"的那道闸，和 `save_upload` 里"不给非 kb 建索引"
    是同一件事的两头 —— 一头保证磁盘上不会有学生课表的块，这一头保证**就算有**（#40 最初版留下的旧块、
    或手改文件）也进不了模型的检索结果。少一头都不算修完。
    ⚠️ #45-B 再叠一道 `can_read_upload`：kb 也可能带着归属（管理员在已登录学生的浏览器里
    从管理端传的文档，`current_student()` 不是空的），那种按"本人私有"处理，不进公共检索。
    """
    for f in list_uploads():
        if upload_purpose(f) != "kb" or not can_read_upload(f):
            continue
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

    返回块数；**回 None 有两种原因**：没这份文档 / 它不是知识文档。
    调用方（管理端那两条口）**必须自己先分开判**再走过来，好把话说准 ——
    "文档不存在"和"这不是知识文档，不能在这里操作"是两句完全不同的话。
    这里那道 purpose 闸是**兜底**：万一以后有人新加一个调用方忘了判，也不会把学生课表切成块。
    """
    if upload_purpose(fid) != "kb":
        return None
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
    """彻底删掉一份**知识文档**：文字 + 原件 + 分块索引 + uploads.json 里那条。

    三道守门，都故意留在 store 层（放在接口层就等于"换个调用方就能绕过去"）：
      ① 不是知识文档（purpose != "kb"）→ 不删。这一档的口只管管理员自己传进来的知识文档；
         学生的上传文件要删得另开一道口、配它自己的确认话术（本单没有这道口，别误以为这里有）。
      ② 还挂在通知上 → **不删**。不许替管理员把一条通知的附件抽走（#39-D 定过的口径），
         真删了的表现是通知列表里那条点开下载 404，学生看见的是坏链接。
      ③ uploads.json 里没这条 id → False。
    ⚠️ 这是**真删、不可逆**，跟"归档"那种软删不是一回事，措辞与实现都不许互借。
    """
    fid = str(fid or "")
    if not fid or upload_purpose(fid) != "kb":
        return False
    if upload_notice_refs(fid):
        return False
    meta = get_upload_meta(fid)
    if meta is None:
        return False

    # ⚠️ 三个路径**必须在改表之前算好**：`get_upload_raw_path` 内部要回查 uploads.json，
    #    而表里这条一删它就查不到 meta、只能回 None —— 于是原件永远删不掉，
    #    表现是"删过了但磁盘上还躺着一份原文件"（#40 最初版就是这个顺序，实测才抓到）。
    paths = (_upload_body_path(fid), _upload_chunks_path(fid), get_upload_raw_path(fid))

    files = _read("uploads.json", {"files": []}).get("files") or []
    kept = [f for f in files if str(f.get("id")) != fid]
    if len(kept) == len(files):
        return False
    _write("uploads.json", {"files": kept})

    for p in paths:
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


def add_todos_batch(rows: list) -> tuple:
    """一次写进学生勾中的**几条不同**待办（#43：从粘贴材料里提取的那一批）。

    为什么不循环调 `add_todo`：那样每条各读各写一次，
    于是"同一批里刚写进去的那条"对后一条**不可见** —— 学生把同一段材料粘两次、
    或者一次勾了两行同样的，就会连着落两条。这里读一次现状、整批去重、写一次。

    去重判据 = 同 `title` + 同 `date` + 同 `start`（都算过 `strip`），
    重复的**不报错也不写**，原样返回给调用方去说"这条日程里已经有了"。

    :return: `(写进去的条目, 被当作重复跳过的条)`
    """
    data = _sread("todos.json", {"todos": []})
    todos = data.setdefault("todos", [])
    have = {(str(t.get("title") or "").strip(), str(t.get("date") or "").strip(),
             str(t.get("start") or "").strip()) for t in todos}
    added, skipped = [], []
    for row in rows or []:
        title = str(row.get("title") or "").strip()
        date = str(row.get("date") or "").strip()
        start = str(row.get("start") or "").strip()
        end = str(row.get("end") or "").strip()
        key = (title, date, start)
        if key in have:
            skipped.append({"title": title, "date": date, "start": start, "end": end})
            continue
        have.add(key)
        item = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "date": date,
            "start": start,
            "end": end,
            "note": str(row.get("note") or "").strip(),
            "category": str(row.get("category") or "").strip(),
            "status": "planned",
            "created_at": _now(),
        }
        todos.append(item)
        added.append(item)
    if added:
        _swrite("todos.json", data)
    return added, skipped


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
        # #54：内置那项本来就是驿站的账（工单由驿站处理），显式写死而不是靠 dept_of 兜底 ——
        # 兜底只该管"老数据没这个键"，不该管"新定义也懒得写"。
        "dept": "驿站",
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
TRIGGER_MIN_LEN = 4            # 一条触发词至少几个字（#54 §六.2，理由在 validate_service_def）
CUSTOM_VALUE_MAX = 60          # 一条登记里每个字段值的上限

# ── 服务的承接部门（2026-10-22 #54：用户拍板"写分流 + 给驿站限制权限"）────────
# 只有两档：驿站管快递/外卖，后勤管设施报修。
# 为什么不做成自由文本：`dept` 是**权限判据**（/api/station/* 那七个口的 403 全靠它），
# 自由文本等于给"越权"留了一个错字的口子 ——「后勤处」「后勤保障处」都能绕过相等判断。
# ⚠️ 这段放在 `add_service` **之前**是硬的：那里的默认值 `dept=DEPT_DEFAULT` 在 def 时就要能取到。
#    （施工单说放在 `is_builtin_service` 附近，那个位置在它下游，照抄会 NameError。）
DEPTS = ("驿站", "后勤")
DEPT_DEFAULT = "驿站"


def dept_of(svc: dict | None) -> str:
    """这项服务归谁处理。**唯一判据**，不许在别处再写一次比较（红线：同一判据抄两遍必漏一处）。

    兜底给"驿站"的理由（历史事实，不是偏好）：#54 之前所有服务都是在驿站端建的，
    没有 dept 键的老定义归驿站，符合它们**实际发生过的归属**。
    ⚠️ 兜底只服务老数据。新建服务**必须在创建时定 dept**（`add_service` 的 dept 参数），
       不要再靠兜底去猜 —— 猜错的表现不是报错，是"后勤的单子安静地进了驿站台账"。
    ⚠️ 判据写成 `svc.get("dept") == "后勤"` 是错的：老定义没有这个键，会静默判成"不是后勤"。
    """
    d = str((svc or {}).get("dept") or "").strip()
    return d if d in DEPTS else DEPT_DEFAULT


def dept_of_record(row: dict) -> str:
    """一条**登记记录**归谁处理：优先读记录里的 dept 快照，没有才回查服务定义。

    为什么要先看快照（#54）：服务定义是可以删的（`delete_service` 只删定义、不动台账），
    定义没了 `get_service(sid)` 就是 None，那时按定义算会把整批历史记录静默判成驿站。
    回查只服务"快照之前写进去的老记录"，**不回填**（回填=编造历史归属）。
    """
    d = str((row or {}).get("dept") or "").strip()
    if d in DEPTS:
        return d
    return dept_of(get_service(str((row or {}).get("service") or "")))


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
    rows = list_services()
    taken = {str(s.get("name") or "") for s in rows}
    if clean["name"] in taken:
        return None, f"已经有一个叫「{clean['name']}」的服务了，换个名字或先删掉它"
    # 触发词的两道闸（#54 §六.2）。为什么以前只拦重名不够：
    #   `match_service_by_trigger` 是**子串**匹配（谁的触发词出现在这句里、且最长，谁接走这句），
    #   于是一个两三个字的宽词能吸走一整族句子 —— 实测触发词「快递」把「我有个快递」
    #   「快递破了」都抢进自建服务，还顺手把内置那条"快递丢了→出工单上报卡"的链路劫持了。
    #   撞车不是"不好看"，是**这句话归谁**当场变了，而且不报错、静默变。
    existing = list_services()
    short = [t for t in clean["triggers"] if len(t) < TRIGGER_MIN_LEN]
    if short:
        return None, (f"触发词「{short[0]}」太短了（不到 {TRIGGER_MIN_LEN} 个字）："
                      f"短词会吸走一大批不相干的句子。说得更具体一点，比如「{short[0]}丢了」这种带得上事的说法。")
    for other in existing:
        oname = str(other.get("name") or "")
        for oth in [str(x) for x in (other.get("triggers") or [])]:
            for t in clean["triggers"]:
                if t and oth and (t in oth or oth in t):
                    return None, (f"触发词「{t}」和已有服务「{oname}」的「{oth}」撞了"
                                  f"（两个互为子串，命中哪一边要看长度，等于把同一句话交给两个窗口）。"
                                  f"改个更长的说法，比如「{t}登记」「{t}怎么查」。")
    clean["id"] = (str(obj.get("id") or "").strip() or uuid.uuid4().hex[:8])
    clean["created_at"] = _now()
    return clean, ""


def add_service(defn: dict, dept: str = DEPT_DEFAULT) -> dict:
    """存一份**已经过校验**的服务定义（校验只在 validate_service_def 一处，别写第二份规则）。

    :param dept: 归哪个部门。**由调用它的路由决定，不由请求体决定** ——
        驿站端建的一律「驿站」、管理端建的一律「后勤」。
        为什么不进 `SERVICE_FIELDS`（那是模型的输出白名单）：模型不知道这学校有几个部门，
        让它填 dept 只会产出「后勤保障处」这种脏值，然后被相等判断静默放行或误拒；
        而 `validate_service_def` 把 dept 当"不认识的字段"剔掉**是对的行为**，不是 bug。
    """
    rows = list_services()
    rows.append({**defn, "dept": dept if dept in DEPTS else DEPT_DEFAULT})
    _write(SERVICES_FILE, rows)
    return rows[-1]


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
                      status: str = "", owner: str | None = None) -> dict:
    """登记一条到 `custom/<service_id>.json`。

    ⚠️ 这条路径**只允许确定性接口调用**（驿站管理员点【登记】这个明确动作）。
    不给模型注册任何写这里的工具 —— 一注册，"低代码生成的服务"就自带写库能力了。

    `status` 是**流程状态**、不是学生填的内容：默认由系统给「待处理」，
    它绝不出现在 `collect_fields` 里（进了会被 `_custom_field_errors` 判成 stray 字段直接 400）。

    :param owner: 这条登记**是谁报的**（#54）。不传 = 取 `current_student()`；
        驿站/后勤在自己界面现场录的，调用方显式传 `""`（无归属，与 `save_upload` 的
        `owner=""` 同一表达）。
        ⚠️ 不接受调用方传任意学号 —— 理由同 `add_workorder`：body 谁都能改，
           能传就等于能冒充。身份只有 `current_student()` 一个来源。
    :param dept 快照（写进 item，不是每次读时现算）：`delete_service` 只删定义、
        不动 `custom/<sid>.json`（既有设计），于是"服务没了、台账还在"是**合法状态**；
        没有快照，那些记录的部门就再也算不出来（`dept_of(None)` 会一律判成驿站）。
        与 #48「上报带 pkg 快照」同一手法。
    """
    svc = svc or get_service(sid) or {}
    want = [str(x) for x in (svc.get("collect_fields") or [])]
    clean: dict = {}
    for k in want:
        v = str((fields or {}).get(k) or "").strip()
        clean[k] = v[:CUSTOM_VALUE_MAX]
    item = {"id": uuid.uuid4().hex[:8], "ts": _now(), "service": sid,
            "name": svc.get("name") or "", "fields": clean,
            "status": str(status or CUSTOM_STATUS_NEW),
            "owner": current_student() if owner is None else str(owner or ""),
            "dept": dept_of(svc)}
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


def update_custom_record_status(sid: str, rid: str, status: str, by: str = "") -> dict | None:
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
            _append_trail(it, status, by)      # ★ #54：进度要有时间，两处 update 各加这一行
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
