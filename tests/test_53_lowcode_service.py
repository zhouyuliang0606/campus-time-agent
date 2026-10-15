"""#18 单子 3：驿站低代码新增服务（创新点那块饼要能落地，也得能被守住）。

一句话能生成功能，听起来就是一道攻击面。所以这个文件的断言分三层，缺一层都不算做完：
  A 「模型只产数据」——生成的服务定义必须过白名单，任何不合规都不许进库（连半份都不行）；
  B 登记这条路径是**确定性写库**（管理员点按钮），字段按服务定义收口，多给的键不收；
  C 红线形状 —— 新增服务**不给模型加工具**（模型依旧碰不到写库）、没有 eval/exec/动态导入、
    渲染走 esc()、运行时数据不入库。

测试全程打桩模型，不打真网络：A 批要的就是"模型回什么形状"这几种输入，
拿真上游测既不稳也白烧额度。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

GOOD = {"name": "失物招领", "triggers": ["我丢了饭卡", "捡到东西交哪", "丢了校园卡"],
        "collect_fields": ["丢失物品", "丢失地点", "大概时间"],
        "reply_template": "已登记，找到后会通知你来驿站领。", "table": "lostfound"}


def _services():
    p = data_path("services.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def _patch_model(reply):
    """把模型换成"照剧本回一句"的假货（只给 chat，够用：这条路径本来就不是流式）。"""
    import app.llm.client as C
    import app.config as cfg

    class _M:
        last_usage = None
        last_model = "m"

        async def chat(self, messages, tools=None, tool_choice="auto", override=None,
                       max_tokens=None):
            self.max_tokens_seen = max_tokens
            return {"content": reply}

    old = (C.DeepSeekClient, cfg.get_llm_config)
    C.DeepSeekClient = lambda *a, **k: _M()
    cfg.get_llm_config = lambda *a, **k: {"api_key": "test-key", "base_url": "http://x",
                                          "model": "m"}

    def restore():
        C.DeepSeekClient, cfg.get_llm_config = old
    return restore


def batch_a_define_only_data():
    c = Checker()
    title("A. 一句话 → 服务定义：模型只产数据，不合规连半份都不许进库")
    with sandbox():
        client = make_client()
        r = _patch_model(json.dumps(GOOD, ensure_ascii=False))
        try:
            ok = client.post("/api/station/services", json={"description": "加个失物招领"})
            sid = (ok.json().get("service") or {}).get("id")
            rows = _services()
            c.check("合法定义 → 落库、字段齐、补了 id 与时间",
                    ok.status_code == 200 and len(rows) == 2
                    and rows[-1].get("name") == "失物招领" and bool(sid)
                    and rows[0].get("created_at"),
                    json.dumps(rows[:1], ensure_ascii=False)[:110])
            lst = client.get("/api/station/services").json().get("services") or []
            c.check("列表立刻能读到（内置那项还在，低代码是第 2 项，不是覆盖）",
                    any(x.get("id") == sid for x in lst)
                    and any(x.get("name") == "快递/外卖问题处理" for x in lst),
                    f"{len(lst)} 项")
        finally:
            r()

        # 模型爱在 JSON 外面套一段话，做法与判定层一致：抠出那个对象，抠不到才算失败
        # （名字换一个，否则会撞上第一条的"重名就拒"，那是另一条断言要管的事）
        GOOD2 = dict(GOOD, name="宿舍报修", table="repair")
        r = _patch_model("好的，我给您生成了一个：\n" + json.dumps(GOOD2, ensure_ascii=False)
                         + "\n您看合适吗？")
        try:
            wrapped = client.post("/api/station/services",
                                  json={"description": "再加一个宿舍报修"})
        finally:
            r()
        c.check("JSON 外面裹了客套话也能抠出来用（不因为多两个字就判失败）",
                wrapped.status_code == 200
                and (wrapped.json().get("service") or {}).get("name") == "宿舍报修",
                f"{wrapped.status_code} {str(wrapped.text)[:60]}")
        base = len(_services())   # 下面所有"不该落库"的断言都比着这个数，别写死 1

        # 每一例都换一个**没被用过的名字**：不这么做，"多塞字段/带尖括号"这些例会顺手撞上
        # "重名就拒"，于是白名单被拆掉（变异 L1/L2）它也照样回 400 —— 断言会因为错误的原因变绿。
        bad_cases = [
            ("模型回了一段散文没有 JSON", "这个想法很好，我建议叫失物招领。", 502),
            ("多塞一个不认识的字段", dict(GOOD, name="寄快递代办", exec="os.system('ls')"), 400),
            ("名字超长", dict(GOOD, name="失" * 30), 400),
            # 尖括号要用**短**的：写 <script>alert(1)</script> 会先撞上"单条不超过 24 字"那条规则，
            # 于是"拆掉尖括号检查"的变异（L2）照样 400 —— 又是"因为错误的原因变绿"。
            ("字段里带尖括号（想往界面里塞标签）",
             dict(GOOD, name="遗失物品登记", triggers=["<b>丢卡</b>", "丢卡", "捡到"]), 400),
            # 标量字段（name）那条检查是另一段代码，也得单独有一例，否则变异只拆列表分支就测不出
            ("服务名里带尖括号", dict(GOOD, name="<b>招领</b>"), 400),
            ("跟已有服务撞名", dict(GOOD, name="失物招领"), 400),
        ]
        for label, reply, want in bad_cases:
            rr = _patch_model(reply if isinstance(reply, str)
                               else json.dumps(reply, ensure_ascii=False))
            try:
                got = client.post("/api/station/services", json={"description": "再来一项"})
            finally:
                rr()
            c.check(f"{label} → {want}，且库里没多东西",
                    got.status_code == want and len(_services()) == base,
                    f"{got.status_code}，库内 {len(_services())} 项")

        empty = client.post("/api/station/services", json={"description": "   "})
        long = client.post("/api/station/services", json={"description": "长" * 300})
        c.check("空话 / 超长描述直接 400（不去打模型，也不写任何东西）",
                empty.status_code == 400 and long.status_code == 400 and len(_services()) == base,
                f"{empty.status_code}/{long.status_code}")

        # 没配密钥那条演示里必然会遇到：必须一句人话 + 不落库
        import app.config as cfg
        old_cfg = cfg.get_llm_config
        cfg.get_llm_config = lambda *a, **k: {"api_key": "", "base_url": "http://x", "model": "m"}
        try:
            nokey = client.post("/api/station/services", json={"description": "加个失物招领2"})
        finally:
            cfg.get_llm_config = old_cfg
        c.check("没有密钥：回一句人话、不落库、不白屏（管理台/驿站都不该看见半份服务）",
                nokey.status_code in (400, 502) and len(_services()) == base
                and bool((nokey.json() or {}).get("error")),
                f"{nokey.status_code} {str(nokey.text)[:40]}")

        anon = make_client(admin_token=None)
        c.check("驿站这几个口不挂管理口令（驿站没有口令），但服务这块也没顺手把管理端带开",
                anon.get("/api/station/services").status_code == 200
                and anon.get("/api/admin/usage").status_code in (401, 403),
                f"{anon.get('/api/station/services').status_code}/"
                f"{anon.get('/api/admin/usage').status_code}")
    return c


def batch_b_register_path():
    c = Checker()
    title("B. 登记一条：确定性写库，字段按服务定义收口")
    with sandbox():
        client = make_client()
        r = _patch_model(json.dumps(GOOD, ensure_ascii=False))
        try:
            sid = client.post("/api/station/services",
                              json={"description": "加个失物招领"}).json()["service"]["id"]
        finally:
            r()

        ok = client.post(f"/api/station/services/{sid}/records",
                         json={"fields": {"丢失物品": "蓝色饭卡", "丢失地点": "三食堂",
                                          "大概时间": "周二中午"}})
        p = data_path(os.path.join("custom", f"{sid}.json"))
        rows = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        c.check("登记成功就写进 custom/<服务id>.json，字段与所属服务都对",
                ok.status_code == 200 and len(rows) == 1
                and rows[0]["service"] == sid and rows[0]["fields"]["丢失物品"] == "蓝色饭卡",
                f"{ok.status_code} / {len(rows)} 条")
        c.check("回执用的是这个服务自己的话术（不是通用一句「已提交」）",
                "通知你来驿站领" in str(ok.json().get("reply") or ""),
                str(ok.json().get("reply"))[:40])

        miss = client.post(f"/api/station/services/{sid}/records",
                           json={"fields": {"丢失物品": "钥匙"}})
        stray = client.post(f"/api/station/services/{sid}/records",
                            json={"fields": {"丢失物品": "钥匙", "丢失地点": "图书馆",
                                             "大概时间": "今天", "admin_note": "顺手塞一条"}})
        after = json.load(open(p, encoding="utf-8"))
        c.check("必填字段缺一个就 400（不做「半条记录」）",
                miss.status_code == 400 and "丢失地点" in str(miss.json().get("error")),
                str(miss.json())[:60])
        c.check("多给的字段直接拒：往台账里塞任意键不能是低代码的副作用；台账条数没变",
                stray.status_code == 400 and len(after) == 1,
                f"{stray.status_code} / {len(after)} 条")

        longv = client.post(f"/api/station/services/{sid}/records",
                            json={"fields": {"丢失物品": "卡" * 200, "丢失地点": "门口",
                                             "大概时间": "刚"}})
        rows2 = json.load(open(p, encoding="utf-8"))
        c.check("超长的值就地截断（存储上限写在 store 里，不把 200 字原样存下）",
                longv.status_code == 200 and len(rows2[-1]["fields"]["丢失物品"]) <= 60,
                f"{len(rows2[-1]['fields']['丢失物品'])} 字")

        nf = client.post("/api/station/services/nosuch99/records", json={"fields": {}})
        nfget = client.get("/api/station/services/nosuch99/records")
        c.check("不存在的服务：登记 404、读记录也 404（不装「这里什么都没发生」）",
                nf.status_code == 404 and nfget.status_code == 404,
                f"{nf.status_code}/{nfget.status_code}")

        lst = client.get(f"/api/station/services/{sid}/records").json()
        c.check("记录列表倒序（最新在前，驿站值班想看的就是刚登的那条）",
                len(lst.get("records") or []) == 2
                and lst["records"][0]["fields"]["丢失物品"].startswith("卡"),
                f"{len(lst.get('records') or [])} 条")
    return c


def batch_c_redlines():
    c = Checker()
    title("C. 红线：不给模型加工具、不 eval、渲染转义、运行时数据不入库")
    import app.main as M

    names = set()
    for key, (_prompt, builder) in M.REGISTRY.items():
        try:
            names |= set((builder() or {}).keys())
        except Exception:
            pass
    bad = [n for n in names if any(w in n.lower() for w in
                                   ("custom", "service", "record", "add_", "write_", "save_"))]
    c.check("新增服务**不给模型注册任何工具**：模型侧看不到任何写库/登记类动作（红线 4）",
            not bad, "、".join(sorted(bad))[:80])

    src = open("app/main.py", encoding="utf-8").read()
    seg = src[src.index("# ============ 驿站低代码"):src.index('@app.post("/api/admin/notify"')]
    # 形状断言一律先剔注释行：我这段的边界说明里就写着"没有 eval / exec / importlib"，
    # 不剔注释就是拿自己的注释当代码判红（本项目第四次踩，规矩是死的：断言剔 # 行再比）
    seg_code = "\n".join(ln.split("#")[0] for ln in seg.splitlines())
    c.check("这条路上没有 eval / exec / 动态导入（生成物只能是数据）",
            not any(t in seg_code for t in ("eval(", "exec(", "importlib", "__import__")), "")
    c.check("写库前必过白名单（station_service_create 里没有 validate_service_def 就是漏闸）",
            "validate_service_def(draft)" in seg and "add_service(clean)" in seg, "")

    st = open("app/static/station.html", encoding="utf-8").read()
    c.check("驿站页把服务名/触发词/登记结果一律 esc() 后再进 DOM",
            "esc(s.name ||" in st and "esc(t)" in st and "esc(String(s.id" in st, "")
    # 这条是真机上撞出来的：5 秒一次的轮询把整张表单重建了，管理员刚敲的字凭空消失，
    # 【登记一条】那一瞬按钮被 detach，点了没反应，回执也一起没了。
    # 断言钉的是"驱动重建的那个条件"，不是"看起来加了个变量"。
    c.check("轮询先比清单签名：没变不重建 DOM，有人在填（焦点/有内容）也跳过这一轮",
            "sig === svcSig" in st and "input:focus, input:not(:placeholder-shown)" in st
            and "svcSig = sig" in st, "")
    # ⚠️ 别写 `"svcSig = null" in st` 就完事 —— 那句在 `let svcSig = null` 的声明里也有，
    # 拆掉 catch 里的复位照样绿（变异 V2 第一次就是这么蒙过去的）。要钉就钉"它在哪一段"。
    stn = st.replace("\r\n", "\n")
    c.check("读失败时签名复位，否则下一次好数据会因为「签名没变」而不渲染，报错条永远挂着",
            'cnt.textContent = "读取失败";\n        svcSig = null' in stn, "")

    # 起点干净这件事也得钉住：沙箱是"复制真实数据"，今天演示时管理员真建过一项叫
    # 「失物招领」的服务，明天这条测试就会因为**撞名**而红 —— 看着像功能坏了，其实是起点耦合。
    h = open("tests/_harness.py", encoding="utf-8").read()
    seg = h[h.index("SCRATCH = ("):h.index("def __enter__")]
    seg_code = "\n".join(ln.split("#")[0] for ln in seg.splitlines())
    c.check("沙箱把服务定义与登记台账当草稿清掉（store 会自动回到内置种子）",
            '"services.json"' in seg_code and '"custom"' in seg_code, "")
    gi = open(".gitignore", encoding="utf-8").read()
    c.check("服务定义与登记台账都不入库（运行时数据 + 隐私，内置种子在代码里）",
            "app/data/services.json" in gi and "app/data/custom/" in gi, "")
    import app.store as S
    c.check("内置第 1 项服务就是「快递/外卖问题处理」（低代码不是特例，是同一张表）",
            any(x.get("name") == "快递/外卖问题处理" for x in S._SEED_SERVICES), "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_53_lowcode_service：一句话生成服务 + 红线形状")
    print("=" * 60)
    code |= batch_a_define_only_data().summary("A 批（只产数据）")
    code |= batch_b_register_path().summary("B 批（登记写库）")
    code |= batch_c_redlines().summary("C 批（红线形状）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
