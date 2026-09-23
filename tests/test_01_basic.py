"""第一批测试：健康检查、四个页面、知识库增删改、客服人格、通知、无密钥降级。

人话：这一组测的是"骨架"——服务能不能起、页面打不打得开、
管理员在后台改的东西是不是真的存下来了、没配密钥时会不会崩。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import Checker, make_client, sandbox, title  # noqa: E402

# with sandbox() 包住整段测试：所有读写都发生在临时副本上，真实数据一动不动
with sandbox():
    client = make_client()
    c = Checker()

    title("1. 健康检查与页面路由")
    r = client.get("/api/health")
    body = r.json()
    c.check("健康检查", r.status_code == 200 and body["status"] == "ok", f"模块={body['modules']}")
    c.check("注册表中含 station 模块", "station" in body["modules"])
    c.check("注册表中含 planner 模块", "planner" in body["modules"])
    c.check("登录页", client.get("/").status_code == 200)
    c.check("学生端页", client.get("/student").status_code == 200)
    c.check("管理员端页", client.get("/admin").status_code == 200)
    c.check("驿站端页", client.get("/station").status_code == 200)
    c.check("我的日程页", client.get("/plan").status_code == 200)

    title("2. 知识库读取")
    r = client.get("/api/admin/kb")
    entries = r.json()["entries"]
    c.check("列出知识库", r.status_code == 200 and len(entries) > 0, f"共 {len(entries)} 条")

    title("3. 知识库增删改 (CRUD)")
    new = client.post(
        "/api/admin/kb",
        json={"question": "临时测试问题", "answer": "临时测试答案", "keywords": "临时,测试"},
    )
    c.check("新增条目", new.status_code == 200 and new.json()["ok"])
    c.check("关键词按逗号拆分", new.json()["entry"]["keywords"] == ["临时", "测试"])
    c.check("空问题应被拒绝", client.post("/api/admin/kb", json={"question": "", "answer": "x"}).status_code == 400)

    idx = len(client.get("/api/admin/kb").json()["entries"]) - 1
    upd = client.put(f"/api/admin/kb/{idx}", json={"question": "改后问题", "answer": "改后答案", "keywords": "改"})
    c.check("修改条目", upd.status_code == 200 and upd.json()["entry"]["question"] == "改后问题")
    c.check("越界修改返回 404", client.put("/api/admin/kb/9999", json={"question": "a", "answer": "b"}).status_code == 404)
    c.check("删除条目", client.delete(f"/api/admin/kb/{idx}").status_code == 200)
    c.check("越界删除返回 404", client.delete("/api/admin/kb/9999").status_code == 404)
    after = client.get("/api/admin/kb").json()["entries"]
    c.check("条目数还原", len(after) == len(entries), f"{len(entries)} -> {len(after)}")

    title("4. 客服人格配置")
    r = client.get("/api/persona")
    p = r.json()["persona"]
    c.check("读取默认人格", r.status_code == 200 and p["name"] == "小园", f"名字={p['name']}")
    r = client.post("/api/persona", json={"name": "小站", "role": "商户客服", "tone": "幽默", "rules": "不编造"})
    c.check("保存人格", r.status_code == 200 and r.json()["persona"]["name"] == "小站")
    p2 = client.get("/api/persona").json()["persona"]
    c.check("人格已持久化", p2["tone"] == "幽默", f"语气={p2['tone']}")
    c.check(
        "空名字回退默认值",
        client.post("/api/persona", json={"name": "", "role": "a", "tone": "b", "rules": "c"}).json()["persona"]["name"] == "小园",
    )

    title("5. 学生消息与通知（旧接口回归）")
    c.check(
        "上报学生消息",
        client.post("/api/student-message", json={"module": "express", "message": "hi", "answer": "yo"}).status_code == 200,
    )
    c.check("管理员收件箱", isinstance(client.get("/api/admin/messages").json()["messages"], list))
    c.check("发布通知", client.post("/api/admin/notify", json={"title": "t", "content": "c"}).json()["ok"])
    c.check("空通知被拒绝", client.post("/api/admin/notify", json={"title": "", "content": ""}).status_code == 400)
    c.check("学生端能读到通知", client.get("/api/notices").json()["notices"])

    title("6. 无 API Key 时的优雅降级（不能给裸 500）")
    r = client.post("/api/chat", json={"message": "图书馆几点关门", "module": "faq"})
    ok = r.status_code == 200
    answer = (r.json().get("answer") or "") if ok else ""
    c.check("对话不再返回 500", ok, f"HTTP {r.status_code}")
    c.check("给出看得懂的中文提示", "⚠️" in answer and "DEEPSEEK_API_KEY" in answer, answer[:80])
    c.check("trace 里也记录了失败原因", bool(r.json().get("trace")) if ok else False)

    sys.exit(c.summary("第一批（骨架）"))
