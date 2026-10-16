"""#18 单子 1：学生工单上报时"工单 + 学生消息"**双写**，让管理端两个视图都看得见。

为什么放后端：前端只调一次 `/api/workorders`，双写在系统侧完成 ——
两处各写一遍就是两本账（本项目忌"第二份"已经栽过很多次），前端漏一处就静默少一条。

已知的缺口只写进注释、不在本单解决：没有学生身份体系，所以这条消息**认不出是谁报的**，
内容也只是"类型｜描述"而不是完整上下文。本单保证的是"能看见报了什么"，账号体系是另一件事
（`EXPLAIN.md` 六已列）。

⚠️ 向后兼容是这里最容易碰坏人的地方：`student_messages.json` 里的**老条目没有 `kind` 字段**，
`/api/student-message`（学生端每轮自己存档）也不传 kind。所以：
  · `add_message` 的第 4 个参数默认 `"chat"`；
  · `list_messages()` 不传 kind 时**照旧返回全部**；
  · 按 kind 过滤时，缺字段的旧条目按 `chat` 算（不是丢掉、也不算成 report）。
A 批第 6 条就是专门拿一条"没有 kind 的老数据"来撞这个的。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0


def _read_raw(name):
    p = data_path(name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _legacy_path():
    return data_path("student_messages.json")


def _latest_report():
    """取最近一条 kind=report 的消息；**一条都没有时返回 {}**。

    写成 next(...) 而不是 reports[-1]：护栏该逐项报红，不该在第二条断言上直接崩
    （崩了后面几条一个都跑不到，看起来像"红一条"其实是"红一片没测到"）。
    """
    rows = [x for x in _read_raw("student_messages.json") if x.get("kind") == "report"]
    return rows[-1] if rows else {}


def batch_a_dual_write():
    c = Checker()
    title("A. 上报一次 = 工单 +1 且 学生消息 +1（两条都要在，字段要齐）")
    import app.store as S

    with sandbox():
        client = make_client()

        # 0) 先埋一条**没有 kind 的老数据**，后面所有断言都带着它跑（向后兼容不是嘴上说的）
        legacy = {"id": "legacy01", "ts": "2026-01-01 00:00:00", "module": "faq",
                  "message": "旧条目：没有 kind 字段", "answer": "嗯"}
        with open(_legacy_path(), "w", encoding="utf-8") as f:
            json.dump([legacy], f, ensure_ascii=False)

        w0 = len(_read_raw("workorders.json"))
        m0 = len(_read_raw("student_messages.json"))
        r = client.post("/api/workorders", json={"kind": "疑似丢件",
                                                 "desc": "周四取的那箱书没找到",
                                                 "source": "panel"})
        w1, m1 = len(_read_raw("workorders.json")), len(_read_raw("student_messages.json"))
        c.check("上报一次：工单 +1 **且** 学生消息 +1（两个视图都看得见）",
                r.status_code == 200 and w1 == w0 + 1 and m1 == m0 + 1,
                f"工单 {w0}→{w1}，消息 {m0}→{m1}")

        wo = _read_raw("workorders.json")[-1]
        msg = _latest_report()
        c.check("补写的那条：kind=report、module=workorder，内容带类型与描述",
                msg.get("kind") == "report" and msg.get("module") == "workorder"
                and "疑似丢件" in msg.get("message", "")
                and "周四取的那箱书没找到" in msg.get("message", ""),
                json.dumps(msg, ensure_ascii=False)[:130])
        c.check("回执那句话说清立案号与状态（管理员扫一眼就知道走到哪一步）",
            wo.get("id", "") in msg.get("answer", "")
                and "待处理" in msg.get("answer", ""),
                msg.get("answer", ""))

        # 1) 普通聊天不许双写：人设规矩是"普通对话不上报"
        m2 = len(_read_raw("student_messages.json"))
        w2 = len(_read_raw("workorders.json"))
        client.post("/api/chat", json={"message": "今天天气不错", "module": "chitchat",
                                       "session_id": "t51-chat"})
        c.check("反向：一句闲聊不打工单也不补消息（普通对话不上报的规矩没被冲掉）",
                len(_read_raw("student_messages.json")) == m2
                and len(_read_raw("workorders.json")) == w2,
                f"消息 {m2}→{len(_read_raw('student_messages.json'))}，"
                f"工单 {w2}→{len(_read_raw('workorders.json'))}")

        # 2) 学生端自己存档那条走默认 kind=chat（旧调用点一个字没改，必须照旧能用）
        r3 = client.post("/api/student-message", json={"module": "express", "message": "hi",
                                                        "answer": "yo"})
        newest = _read_raw("student_messages.json")[-1]
        c.check("向后兼容：/api/student-message 不传 kind，默认记成 chat（旧调用点不受影响）",
                r3.status_code == 200 and newest.get("kind") == "chat",
                json.dumps(newest, ensure_ascii=False)[:110])

        # 3) 过滤与老数据
        all_msgs = S.list_messages()
        reports = S.list_messages("report")
        chats = S.list_messages("chat")
        c.check("list_messages() 不传 kind 照旧返回全部（管理端那张表不许突然少行）",
                len(all_msgs) == len(_read_raw("student_messages.json")),
                f"{len(all_msgs)} 条")
        c.check("按 kind 过滤：report 只回 report，且**没有 kind 的老条目算 chat**（不算 report、也不丢）",
                all(x.get("kind") == "report" for x in reports)
                and any(x.get("id") == "legacy01" for x in chats),
                f"report {len(reports)} 条，chat {len(chats)} 条")

        # 4) 聊天来源也要双写（确认条点【确认上报】那条路）
        m4 = len(_read_raw("student_messages.json"))
        client.post("/api/workorders", json={"kind": "外卖丢失", "desc": "",
                                             "source": "chat", "session_id": "t51-x"})
        rep = _latest_report()
        c.check("聊天来源同样双写，缺描述时用占位而不是留空（管理端不该看到一条空的）",
                len(_read_raw("student_messages.json")) == m4 + 1
                and "（无补充描述）" in rep.get("message", ""),
                rep.get("message", ""))

        # 5) 错误路径不许半个双写
        m5 = len(_read_raw("student_messages.json"))
        w5 = len(_read_raw("workorders.json"))
        bad = client.post("/api/workorders", json={"kind": "", "desc": "没类型"})
        c.check("kind 为空回 400，且**两个文件都不许多**（不许写了一半）",
                bad.status_code == 400 and len(_read_raw("student_messages.json")) == m5
                and len(_read_raw("workorders.json")) == w5,
                f"status={bad.status_code} 消息 {m5}→{len(_read_raw('student_messages.json'))}")
    return c


def batch_b_shape():
    c = Checker()
    title("B. 形状：双写只在**一个函数**里，而且这条路不许碰模型")
    main_src = open("app/main.py", encoding="utf-8").read()
    seg = main_src[main_src.index("async def workorder_create"):main_src.index("@app.get(\"/api/workorders\"")]
    c.check("add_workorder 与 add_message 在同一个函数里（缺一个就是两本账）",
            "add_workorder(" in seg and "add_message(" in seg, "")
    c.check("这条上报路径不调大模型（写库漏斗的入口不该反过来依赖模型输出）",
            "gateway" not in seg and ".chat(" not in seg and "engine" not in seg, "")
    store_src = open("app/store.py", encoding="utf-8").read()
    c.check("add_message 的第 4 个参数有默认值（不传就是 chat，向后兼容写在签名上）",
            'answer: str, kind: str = "chat"' in store_src, "")
    c.check("list_messages 支持按 kind 过滤且默认 None（默认行为不许变）；"
            "#29 加了 include_archived 之后**两个默认值都得是「不改变老行为」的那一侧**",
            "def list_messages(kind: str | None = None, include_archived: bool = False) -> list:" in store_src,
            store_src[store_src.index("def list_messages"):][:78])
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_51_report_dual_write：工单 + 学生消息双写")
    print("=" * 60)
    code |= batch_a_dual_write().summary("A 批（双写）")
    code |= batch_b_shape().summary("B 批（形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
