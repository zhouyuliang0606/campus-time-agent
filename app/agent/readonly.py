"""只读工具的全集（人话：这些工具只"看"不"改"，所以哪个栏都能给模型几只）。

为什么单开一份、不直接 `tools.update(各模块 build_tools())`：
  · 那会把 `propose_*` 一起放进来 —— 写侧必须留在"意图对了才挂"的判据后面；
  · 模块之间有**同名不同实现**的工具（下面 `get_by_code` 那条），update 谁后谁赢。
所以这里用"名字 → 来源模块"的显式账本，不靠命名前缀猜。
"""
from app.agent.tools import Tool

# 名字 → 它该从哪个模块的工具箱里取。
# ⚠️ 必须写死来源，不能只写名字：
#    `get_by_code` 在 express 和 station **各有一份、同名、实现不同**
#    （express 那份只在这个学生自己的件里查；station 那份是店员查任意学生，
#     `station.py:20` 用 `partial(get_by_code, staff=True)` 绑死了店员口径）。
#    `schedule.py:215` 那段长注释记的就是同款事故的第一集：
#    以前 schedule 和 planner 各注册了一个 `find_free_slots`，入参还不一样，
#    `tools.update()` 后注册的赢，学生拿到「周三 不是合法日期」。
#    这是第二集。别再靠"名字唯一"这个约定。
READONLY_SPEC: dict[str, str] = {
    "get_day_courses":      "schedule",
    "plan_task":            "schedule",
    "get_weekly_timetable": "planner",
    "find_free_slots":      "planner",
    "list_day_todos":       "planner",
    "list_todos_range":     "planner",     # ← #63：查"一段"的粒度，不登记就只在 planner 栏有
    "search_kb":            "faq",
    "list_my_packages":     "express",
    "list_pending_pickup":  "express",
    "get_by_code":          "express",     # ← 驿站的同名份**故意不要**，见上
    "list_my_orders":       "takeout",
    "list_active_orders":   "takeout",
    "get_order":            "takeout",
    "list_uploaded_files":  "files",
    "read_uploaded_file":   "files",
    "search_docs":          "files",
}

# 反向断言用（测试里读，运行时不读）。写侧工具名全列在这儿，不靠前缀推断。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "submit_work_order",
    "propose_slots", "propose_todo_tool", "propose_todo_mode_tool",
    "propose_todo_slots_tool", "propose_todo_remove", "propose_todo_done",
    # ★ #86：新增的写侧提案工具**必须同时登记**（这份表是反向断言的地基，漏一条=静默失去保护）
    "propose_todo_change",
    "propose_clear_timetable", "propose_course_change", "propose_timetable_change",
    "propose_custom_record",
})


def _module_pool(module: str) -> dict[str, Tool]:
    """取某个模块自己的工具箱（人话：去那间"格子间"把书柜搬出来，先不挑）。

    ⚠️ `files` 不在 `REGISTRY` 里（实测 2026-10-25）：`REGISTRY` 只有六个可路由的对话栏，
       "读文件"那三个工具是 `main.py:1925` 每轮**额外** `tools.update(files_tools())` 挂上去的。
       所以这里必须分两路取 —— 只按 `REGISTRY` 取，那三条会 KeyError，
       而 `readonly_tools()` 里"缺了就跳过"的写法会让它们**静默不在池子里**（症状是
       "问答栏查不到文档"，看不出问题出在账本上）。
    """
    from app.main import REGISTRY          # 延迟 import：main 会 import 本模块，顶层相互引用会成环

    if module == "files":
        from app.modules.files import build_tools as files_build_tools
        return files_build_tools()
    builder = REGISTRY[module][1]
    return builder()


def readonly_tools() -> dict[str, Tool]:
    """把 READONLY_SPEC 里那些工具从各自模块取出来（人话：跨栏借书，只借"只读"那几本）。

    函数体每轮对话重建一次对象，**不加 lru_cache**：
    测试里有"改完模块再读池子"的场景，缓存住会让红点消失不掉（该红的必须红）。
    一次 `build_tools()` 就是几个 dict 字面量，成本可忽略。
    """
    pools: dict[str, dict[str, Tool]] = {}
    out: dict[str, Tool] = {}
    for name, mod in READONLY_SPEC.items():
        if mod not in pools:
            pools[mod] = _module_pool(mod)
        tool = pools[mod].get(name)
        if tool is not None:               # 缺了就跳过，不让一个改名炸掉整条链路
            out[name] = tool
    return out
