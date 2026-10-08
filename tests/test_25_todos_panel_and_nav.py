"""待办面板字段 + 「上传文件」栏删除 —— 回归护栏（test_25）。

守两件刚核实并改动的事（依据《CampusTime-改造方案prompt.md》）：

1. **待办面板增删查改**（原报障 1.1）：`GET /api/todos` 返回的主键是 `id`，
   而 `loadTodosView` 原先写 `t.todo_id` → `data-id="undefined"` →
   勾选走 `PUT /api/todos/undefined`、删除走 `DELETE /api/todos/undefined`，**全 404**。
   已改回 `t.id`。
   ⚠️ 注意别一刀切全局替换：`todo_id` 在**提案卡**上是对的字段
   （`planner.py` 造的 `"todo_id": item.get("id")`），
   `confirm.html` / `main.py` 那些 `todo_id` **绝不能动**——
   本护栏只钉 `loadTodosView` 这一处（用 `t.id` 而非 `t.todo_id`）。

2. **「上传文件」独立栏已删除**（方案第 4 节 6 处）：
   `data-nav="upload"` / `navUpload` / `loadUploadView` / `upInput` / `upDrop` / `upList`
   应全部无命中；而「上传课表」那套（`uploadTtPanel` / `ttFilePanel` / `handleFile`，
   位于 view-schedule 内）必须**保留且可用**。
"""

import os

import _utf8  # noqa: F401  中文 Windows 的 GBK 控制台打不出 ✅，会让本文件一跑就中断

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUDENT = os.path.join(PROJ_DIR, "app", "static", "student.html")


class Collector:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            print(f"  ✅ {name}")
        else:
            self.failed += 1
            print(f"  ❌ {name}  {detail}")


def run():
    c = Collector()
    with open(STUDENT, encoding="utf-8") as f:
        src = f.read()

    # ---- ① 待办面板用对的主键 ----
    # 2026-10-08 XSS 收口后，`data-id` 那侧从 encodeURIComponent(t.id) 换成了 esc(t.id)
    # （属性上下文要的是转义引号，不是 URL 编码），encode 挪到真正拼 URL 的 fetch 处。
    # 所以这里**不能再钉 `encodeURIComponent(t.id)` 这个字面量**——钉住写法会把正确的
    # 重构判成回归。改钉语义：主键取自 t.id，且拼 URL 时确实 encode 过。
    head = src.find("async function loadTodosView")
    body = src[head:] if head >= 0 else ""
    c.check("待办面板用 t.id（不是 t.todo_id）",
            head >= 0 and "t.id" in body,
            "loadTodosView 没用 t.id，增删会打 /api/todos/undefined")
    c.check("待办面板增删打的是 encodeURIComponent(id) 过的 URL",
            body.count('"/api/todos/"+encodeURIComponent(id)') >= 2,
            "PUT/DELETE 没对 id 做 URL 编码（或那条路径改名了，护栏要跟着看）")
    c.check("待办面板写进 data-id 属性的 id 走 esc()",
            'data-id="\'+esc(t.id)' in body,
            "id 直插属性上下文，含引号的 id 会破属性")
    c.check("待办面板不再出现 t.todo_id",
            "t.todo_id" not in src,
            "loadTodosView 仍在用 t.todo_id（API 没这个字段）")

    # ---- ② 「上传文件」独立栏已删干净 ----
    for token in ("loadUploadView", 'data-nav="upload"', "navUpload",
                  "upInput", "upDrop", "upList"):
        c.check(f"已删除：{token}", token not in src,
                f"{token} 仍存在，删栏没删干净")

    # ---- ③ 「上传课表」必须保留 ----
    for token in ("uploadTtPanel", "ttFilePanel", "handleFile"):
        c.check(f"保留：{token}", token in src,
                f"{token} 不见了，「上传课表」会被误删")

    return c


def main():
    print("===== 待办面板字段 + 上传栏删除回归（test_25） =====")
    r = run()
    print(f"\n===== test_25 总账：通过 {r.passed} 项，失败 {r.failed} 项 =====")
    return 1 if r.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
