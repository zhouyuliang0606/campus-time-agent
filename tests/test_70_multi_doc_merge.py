"""#42-B 的护栏：**几份文档拼起来才完整**的答案，不许只答一半。

用户 2026-10-19 的追问（施工单 §六）：「如果上传了多个文本，几个文本的答案合一起才是
学生提问的答案怎么办」。实测出来的病形状（就是本文件 A 批那份料）：
一份 3796 字 / 5 块的《办事手册》把「补办学生证……缴纳工本费二十元」在六个章节各写一遍，
一份 905 字 / 2 块的《资助细则》把"困难生工本费予以免收"只写在一处 —— 问「补办学生证 工本费」
BM25 名次实测 `手册#2 5.59 / 手册#3 5.34 / 手册#1 5.04 / 手册#4 4.98 / … / 细则#1 第 6 名`，
**前四名全是同一份手册**：改之前不但学生那两格（`FAQ_DOC_HITS`）拿不到例外，
连模型那四格（`DOC_TOP_K`）也拿不到 —— `search_kb` 的返回里连「免收」都没有，学生只听到"交二十元"。

第二个洞是真发时才露出来的（A6）：选块把两份文档都带上了，摘录却把答案句截在窗外 ——
章标题「第 1 章 补办学生证」和答案句跟查询**各沾 4 个二元组**，`_best_window` 平手取先来者，
于是从标题往后截 320 字，整段都是垫话。`_best_window` 的平手判据因此改成"平手选长句"。

四批：A 选块与摘录（纯函数不经模型 ⇒ 稳）、B 两个视图 + 真发、C 提示词四处同步、D 形状。
`DOC_TOP_K` 没有按施工单提到 6~8：加了配额之后 4 与 6 选出的块**一模一样**（`probe42b_k.py`），
而 A4-c 钉着"不配额的裸取前 4 名照样丢例外" ⇒ 起作用的是配额与"模型那一路不再被先砍成两块"，
不是把名额多给几块。
"""
import ast
import json
import os
import re
import shutil

from _harness import PROJ_DIR, Checker, data_path, make_client, sandbox, title

code = 0

# 料里四句"必须同时出现才算答全"的事实，各自只写在一处
RULE_FACT = "缴纳工本费二十元"          # docA 通则（四个章节各写一遍）
EXCEPT_FACT = "工本费予以免收"          # docB 例外（只写一处）
NORMAL_CLOSE = "二十二点三十分"         # docC 平时闭馆
EXAM_CLOSE = "二十三点三十分"           # docD 考试周闭馆

DOC_A = "docA-办事手册.txt"
DOC_B = "docB-资助细则.txt"
DOC_C = "docC-图书馆开放时间.txt"
DOC_D = "docD-考试周延长开放通知.txt"
DOC_E = "docE-校园网计费说明.txt"

# 每份文档用**自己那一套**说明句。共用垫句会让"问网络"也匹配上手册，
# G11（不硬凑）就再也测不出来了 —— 夹具的形状就是断言的一半（K1-四 那一课）。
_FILL_A = ("受理窗口在工作日八点半到十一点半开放，中午休息，特殊情况以当日公告为准。"
           "办理时需要携带本人有效证件，委托他人代办的还要出具委托书和双方证件。"
           "本章所列事项均不收取押金，办结后可在校内办事大厅电子屏查询进度和办理结果。"
           "涉及跨校区业务的两个窗口都可以受理，不必回到学籍所在校区排队。"
           "材料不齐全的当场一次性告知缺哪几样，补齐后按排队顺序继续办理，无需重新预约。")
_FILL_B = ("资助认定工作每学年组织一次，由班级评议、院系审核、学校公示三步完成。"
           "建档户与低保家庭学生自动进入认定库，本学年内无需重复提交纸质材料。"
           "资助款项按月发放至本人校园卡账户，发放明细可在学生服务平台在线查询。"
           "对认定结果有异议的，可以在公示期内向院系资助联络员书面提出复核。")
_FILL_C = ("图书馆各阅览室开放时段以门口公告牌为准，自习区座位实行自助选座不预留。"
           "借还书机分布在一层与三层，校园卡余额不足时无法完成续借操作。"
           "研究间与研讨间需要在前台预约，凭校园卡刷卡进入，逾期自动释放。"
           "闭馆前半小时停止入场，请提前收拾物品以免被请出阅览区。")
_FILL_D = ("教务排课与调课通知发布在教务系统，选课结果以系统内的记录为准。"
           "调课申请由任课教师提交，学院教学秘书审核后生效，学生不必另行办理。"
           "考试安排以教务处公示的考场表为准，不因临时调课而变动考场分配。"
           "考试周期间自习场所实行延长开放，延长时段内只保留主入口并设安检。")
_FILL_E = ("校园网账号随学号开通，毕业当年六月自动注销，欠费将暂停上网权限。"
           "带宽套餐每学期初在信息化办公室网站公示，改套餐次月生效。"
           "自助充值机位于食堂一层与宿舍区门厅，支持扫码与校园卡两种付款方式。"
           "网络故障报修请拨信息化服务热线，工作日两小时内响应。")


def _docs():
    """五份文档：A 把通则重复写在六个章节（占名额），B/C/D 各拿一条别处没有的事实，E 是无关项。

    料的尺寸是照着"病形状能复现"配的（A1-前置 / A2 钉着它）：真实手册就是同一件事在好几个
    章节各交代一遍，而细则只在某一处写了例外 —— 于是全局名次前几名全是手册。
    """
    chapters = ["补办学生证", "校园一卡通挂失与补办学生证", "火车票优惠卡（补办学生证时一并办理）",
                "学生证信息更正与补办学生证", "毕业生补办学生证", "出国交换生补办学生证"]
    a = ["《学生事务办事手册》"]
    for n, topic in enumerate(chapters, 1):
        a.append(f"第 {n} 章 {topic}")
        a.append(_FILL_A + _FILL_A)
        a.append(f"{topic}的，由本人到行政楼一层综合服务大厅申请，{RULE_FACT}，五个工作日办结。")
        a.append(_FILL_A)
    a.append("附则 本手册由学生处负责解释，未尽事项另行通知。" + _FILL_A)
    doc_a = "\n".join(a) + "\n"

    doc_b = ("《家庭经济困难学生资助实施细则》\n第一章 认定范围 " + _FILL_B + _FILL_B +
             "\n第二章 证件补办费用 " + _FILL_B + _FILL_B +
             f"\n已认定为家庭经济困难的学生（含建档户与低保家庭）补办学生证的{EXCEPT_FACT}，"
             "凭认定表到资助中心加盖免费章后办理。" + _FILL_B +
             "\n第三章 发放与复核 " + _FILL_B + "\n")

    doc_c = ("《图书馆开放时间》\n第一节 开放 " + _FILL_C +
             f"\n图书馆平时开放至{NORMAL_CLOSE}闭馆。\n第二节 座位与预约 " + _FILL_C + "\n")

    doc_d = ("《考试周延长开放通知》\n一 考试安排 " + _FILL_D +
             f"\n自本通知发布之日起，考试周期间图书馆延长开放至{EXAM_CLOSE}闭馆。"
             "\n二 其他事项 " + _FILL_D + "\n")

    secs = ["账号与欠费", "自助充值", "套餐与资费", "故障报修", "覆盖范围与终端", "费用发票"]
    e = ["《校园网计费说明》"]
    for n, s in enumerate(secs, 1):
        e.append(f"第 {n} 节 {s}")
        e.append(_FILL_E + _FILL_E)
        e.append(f"{s}的说明：校园网账号欠费时可在自助充值机充值，每账号每月充值限额五十元。")
    e.append("附 本说明由信息化办公室负责解释。" + _FILL_E)
    doc_e = "\n".join(e) + "\n"
    return {DOC_A: doc_a, DOC_B: doc_b, DOC_C: doc_c, DOC_D: doc_d, DOC_E: doc_e}


def _wipe_uploads():
    """把沙箱里的文档全清掉，只留本单自种的五份。

    为什么必须清：`sandbox()` 是**复制真实数据**，不是空库 —— 开发机上管理员真传过手册，
    干净克隆里一份都没有，"前两名同属一份文档"这种几何断言就会两边结果不同
    （指南红线 18 那一族，#41 那次就是被 `kb.json` 绊的）。清完再种，两边才是同一份料。
    """
    json.dump({"files": []}, open(data_path("uploads.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    up = data_path("uploads")
    if os.path.isdir(up):
        shutil.rmtree(up)
    os.makedirs(up, exist_ok=True)


def _wipe_kb():
    json.dump({"entries": []}, open(data_path("kb.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _seed_docs():
    from app import store
    for name, body in _docs().items():
        store.save_upload(name, body, raw=body.encode("utf-8"), purpose="kb")


def _env():
    """清库 + 种料（每批进来都先摆成同一份赛前状态）。"""
    _wipe_kb()
    _wipe_uploads()
    _seed_docs()


def _say(cli, module, msg, sid):
    body = {"message": msg, "session_id": sid}
    if module:
        body["module"] = module
    d = cli.post("/api/chat", json=body).json()
    phases = " | ".join(str(t.get("phase") or "") for t in (d.get("trace") or []))
    return (d.get("answer") or ""), phases


def _srcs(rendered: str) -> list:
    """从渲染结果里数来源（模型视图 `[N]《文件》第 M 块`、学生视图 `《文件》里是这么写的`）。"""
    return re.findall(r"《(.+?)》", rendered)


def batch_a_pick():
    c = Checker()
    title("A. 选块：来源配额（这张单子真正的那一刀）")
    from app import store
    from app.modules import files
    from app.modules.faq import FAQ_DOC_HITS

    with sandbox():
        _env()
        ids = {f["name"]: f["id"] for f in store.list_uploads()}
        ca, cb = store.list_chunks(ids[DOC_A]), store.list_chunks(ids[DOC_B])
        n_rule = sum(1 for x in ca if RULE_FACT in x["text"])

        # ── 前置：先钉**料的几何**。夹具做不出病形状，后面所有断言都是自证（K1-前置 那一课）
        c.check("A1-前置 料到位：docA ≥4 块、其中 ≥2 块写着通则（一份手册把同一件事交代了好几遍）",
                len(ca) >= 4 and n_rule >= 2, f"docA {len(ca)} 块 / 含通则 {n_rule} 块")
        c.check("A1-二 料到位：docB 的例外只出现在**一块**里（真实细则就是只写一处）",
                sum(1 for x in cb if EXCEPT_FACT in x["text"]) == 1, f"docB {len(cb)} 块")

        hits = files.doc_hits("补办学生证 工本费")
        rank = {id(h): i for i, h in enumerate(hits)}
        top2 = [h["name"] for h in hits[:2]]
        c.check("A2 病形状还在（不加配额就必然复发）：BM25 全局前两名**同属一份文档**",
                len(top2) == 2 and set(top2) == {DOC_A},
                str([(h["name"][:4], int(h["i"]) + 1, round(h["score"], 2)) for h in hits[:4]]))
        c.check("A2-bis 例外那块排在**前 `DOC_TOP_K` 名之外** —— 所以救它的是配额，不是把 k 调大"
                "（施工单说的「提到 6~8」实测照样拿不到它）",
                min(rank[id(h)] for h in hits if h["name"] == DOC_B) >= files.DOC_TOP_K,
                f"例外那块在第 {min(rank[id(h)] for h in hits if h['name'] == DOC_B) + 1} 名")

        # ── 学生预算（两块）也要两份都发声
        picked = files.pick_diverse(hits, FAQ_DOC_HITS)
        c.check("G10 只给两块的名额时也必须**两份各一块**（配额不是'多给几块'，是给来源让位）",
                len(picked) == 2 and {h["name"] for h in picked} == {DOC_A, DOC_B},
                str([(h["name"][:4], int(h["i"]) + 1) for h in picked]))
        c.check("G8-检索 这两块合起来就是完整答案（通则 + 例外，分别来自两份文档）",
                any(RULE_FACT in h["text"] for h in picked) and
                any(EXCEPT_FACT in h["text"] for h in picked))

        # ── 配额的上限那头 + 名次不乱
        full = files.pick_diverse(hits, files.DOC_TOP_K)
        per: dict = {}
        for h in full:
            per[h["name"]] = per.get(h["name"], 0) + 1
        c.check("A4 模型预算内同一份文档最多占 `DOC_PER_SOURCE` 块（其余名额让给别的来源）",
                all(n <= files.DOC_PER_SOURCE for n in per.values()) and
                len(full) <= files.DOC_TOP_K and len(per) >= 2, str(per))
        c.check("A4-bis 配额之后名次没被打乱（渲染那句『按相关度排序』才不是假话）",
                [rank[id(h)] for h in full] == sorted(rank[id(h)] for h in full),
                str([rank[id(h)] for h in full]))
        c.check("A4-c 配对：同一份料**不配额的直接取前 `DOC_TOP_K` 名**就没有例外那块"
                "（这条就是 M5/M7 的牙：证明起作用的是配额，不是把名额多给两块）",
                not any(h["name"] == DOC_B for h in hits[:files.DOC_TOP_K]) and
                any(h["name"] == DOC_B for h in full),
                f"裸取前 {files.DOC_TOP_K} 名来源={sorted({h['name'][:4] for h in hits[:files.DOC_TOP_K]})}")

        # ── G11 不硬凑 / 单来源不自砍
        c.check("A1-三 料到位：docE ≥3 块且都写着「报修/热线」这件事（下面 G11 拿它当「只有一份相关」）",
                sum(1 for x in store.list_chunks(ids[DOC_E]) if "报修" in x["text"]) >= 3,
                f"docE {len(store.list_chunks(ids[DOC_E]))} 块")
        net = files.doc_hits("报修 热线 响应")
        got_net = files.pick_diverse(net, files.DOC_TOP_K)
        c.check("G11 只有一份文档沾得上时不硬凑第二份（来源数=1，别的文档一块都没有）",
                {h["name"] for h in got_net} == {DOC_E} and len(net) >= 3,
                str(sorted({h["name"] for h in net})))
        c.check("G11-bis 单来源时**也不自砍**：给到 `DOC_TOP_K` 块，不被'同一份最多 2 块'掐住"
               "（那条上限是为竞争者设的，没有对手时砍掉的就是正主的内容）",
               len(got_net) == min(files.DOC_TOP_K, len(net)) and len(got_net) > files.DOC_PER_SOURCE,
               f"docE 命中 {len(net)} 块 → 取 {len(got_net)} 块")
        c.check("A5 边界：limit<=0 或空表都回空列表（不抛、不把整本手册倒出来）",
                files.pick_diverse(hits, 0) == [] and files.pick_diverse([], 4) == [])

        # ── 第二个洞：选块选对了，摘录却把答案句截在窗外（真发实测到的，见 _best_window 的注释）
        title_txt = ("第 1 章 补办学生证\n" + "受理窗口在工作日八点半到十一点半开放，中午休息。" * 3 +
                     "补办学生证的，由本人到行政楼一层综合服务大厅申请，缴纳工本费二十元，五个工作日办结。")
        q2 = "困难生补办学生证要交多少钱"
        win = files._best_window(title_txt, q2, 60)
        c.check("A6 摘录起点：跟查询**共有词一样多**时选长的那句"
               "（章标题「第 1 章 补办学生证」与答案句都沾 4 个二元组，按先来者赢就把答案截在窗外）",
                win.startswith("补办学生证的，") and RULE_FACT in win, win[:40])
        other = ("带宽套餐的资费每学期初在信息化办公室网站公示。"
                 "自助充值机位于食堂一层与宿舍区门厅，支持扫码与校园卡两种付款方式，"
                 "欠费将暂停上网权限，网络故障报修请拨信息化服务热线，工作日两小时内响应，"
                 "逾期由系统自动注销账号并通知本人补交欠费。")
        c.check("A6-bis 对照：沾词**更多**的那句照旧赢 —— 长度只在平手时说话，不许把长度写成主判据",
                files._best_window(other, "带宽 套餐 资费 公示", 20).startswith("带宽套餐"),
                files._best_window(other, "带宽 套餐 资费 公示", 20)[:20])
    return c


def batch_b_views():
    c = Checker()
    title("B. 两个视图与真发：拼起来才完整的答案，学生要一次看全")
    from app.modules.faq import answer_for_student, search_kb

    with sandbox():
        _env()

        # ── 模型视图（G8 的检索那一半）
        sk = search_kb("补办学生证 工本费")
        c.check("G8 模型视图同时带出两份文档的段落（改之前这里连「免收」都没有）",
                RULE_FACT in sk and EXCEPT_FACT in sk and DOC_A in sk and DOC_B in sk,
                f"{len(sk)} 字 / 来源 {sorted(set(_srcs(sk)))}")
        c.check("G8-bis 每一块都带着自己的出处（模型要靠它按来源标注，也才复查得动）",
                sk.count("块（原文第") >= 2, f"出现 {sk.count('块（原文第')} 次")

        # ── 学生视图（日程栏直答那一路：不经模型，屏幕上看到什么就是什么）
        st = answer_for_student("补办学生证 工本费")
        c.check("G8-学 学生视图两段都在、分别指到两份文档（真机那条气泡的口径）",
                RULE_FACT in st and EXCEPT_FACT in st and DOC_A in st and DOC_B in st,
                f"{len(st)} 字 / 来源 {sorted(set(_srcs(st)))}")
        bad = [w for w in ("相关度", "原文第", "字起") if w in st]
        c.check("G8-学bis 学生视图仍然不带定位元数据（#41-A 的口径不许被这次改动洗掉）",
                not bad, f"漏出来的：{bad}")
        c.check("G8-学三 学生视图没被模型的预算带长（仍是两块截窗，气泡要看得完）",
                len(st) < 1200 and st.count("》里是这么写的") == 2, f"{len(st)} 字")

        # ── G9 冲突：两份文件各说一个闭馆时间，两个都要在场
        cf = search_kb("图书馆几点闭馆")
        c.check("G9 两份文档说法不一致时两段都进上下文（谁也没被对方挤掉）",
                NORMAL_CLOSE in cf and EXAM_CLOSE in cf and DOC_C in cf and DOC_D in cf,
                f"来源 {sorted(set(_srcs(cf)))}")
        st_cf = answer_for_student("图书馆几点闭馆")
        c.check("G9-学 学生视图同样两份都在",
                NORMAL_CLOSE in st_cf and EXAM_CLOSE in st_cf, st_cf[:80])

        # ── 真发：日程栏那句问话走的是"翻知识库直答"，学生一条气泡看全
        cli = make_client()
        ans, phases = _say(cli, "planner", "困难生补办学生证要交多少钱", "t70a")
        c.check("B1-前置 真发这句确实走到了知识库那一路（测的必须是走到的）",
                "翻知识库" in phases, phases[:90])
        c.check("G8-真 直答里通则与例外**同时**出现（学生不必再问第二遍）",
                RULE_FACT in ans and EXCEPT_FACT in ans, ans[:160])
        ans2, phases2 = _say(cli, "planner", "图书馆什么时候闭馆", "t70b")
        c.check("B2-前置 冲突对那句也真走到直答那一路", "翻知识库" in phases2, phases2[:90])
        c.check("G9-真 冲突对真发在一条气泡里说全（平时 / 考试周两个时间都出现）",
                NORMAL_CLOSE in ans2 and EXAM_CLOSE in ans2, ans2[:160])
        ans3, ph3 = _say(cli, "faq", "补办学生证 工本费", "t70c")
        c.check("B3 对照：问答栏问同一句不许把定位元数据漏给学生（也不许崩）",
                "相关度" not in ans3 and "原文第" not in ans3, f"{ph3[:50]}｜{ans3[:80]}")
    return c


def batch_c_prompts():
    c = Checker()
    title("C. 提示词四处同步（只改选块不改提示，模型照样只答一半）")
    import app.main as m
    from app.modules import faq, files

    d_docs = files.build_tools()["search_docs"].description
    d_kb = faq.build_tools()["search_kb"].description
    sp = faq.SYSTEM_PROMPT
    fh = m.FILE_HINT
    intro = files.format_doc_hits([])      # 零命中时剩下的就是那句开场白

    c.check("C1 `search_docs` 工具描述要求**合并多份 + 分别标出处**（模型读的是这句，不是我们的注释）",
            "多份文档" in d_docs and "合起来" in d_docs and "分别" in d_docs, d_docs[:70])
    c.check("C2 `search_docs` 描述里：不一致时两个都讲、不许擅自选一个",
            "不一致" in d_docs and "不要擅自挑一个" in d_docs)
    c.check("C3 `search_docs` 描述允许**换关键词再调一次本工具**（多跳那一刀就靠这句）",
            "再调一次本工具" in d_docs)
    c.check("C4 `search_kb` 描述同样有合并与再查，但保留「不用再换别的工具查一遍」"
            "（#40 补2 那句是防瞎试的，不能被这次改动顺手删掉）",
            "多份文档" in d_kb and "再调一次本工具" in d_kb and "不用再换别的工具" in d_kb,
            d_kb[:70])
    c.check("C5 `faq.SYSTEM_PROMPT` 要求合并 + 分别标出处 + 冲突两个都讲",
            "多份文档" in sp and "合起来" in sp and "不要擅自挑一个" in sp)
    c.check("C6 `faq.SYSTEM_PROMPT` 把「换词再查」与「换工具再查」分开了"
            "（允许换 query 重查，仍禁止换工具；那句『两边都没查到』的契约保留）",
            "再调一次 search_kb" in sp and "不许换别的工具" in sp and "两边都没查到" in sp)
    c.check("C7 全站共用的 `FILE_HINT` 也同步了（别的模块问文档时同样要知道结果可能来自好几份）",
            "好几份文件" in fh and "换一组关键词再检索一次" in fh, fh[:80])
    c.check("C8 检索结果的开场白说了「可能来自多份文档」与不一致时怎么办（这句每轮都进模型上下文）",
            "可能来自多份文档" in intro and "不要擅自选一个" in intro, intro[:80])
    c.check("C9 引擎没动：多轮检索靠已有的 `max_steps = 6`（没有为这一单新加一套机制）",
            "max_steps = 6" in open(os.path.join(PROJ_DIR, "app", "agent", "engine.py"),
                                    encoding="utf-8").read())
    return c


def batch_d_shape():
    c = Checker()
    title("D. 形状（判据一律 AST + 真发，不 grep 注释）")
    files_tree = _tree("app/modules/files.py")
    faq_tree = _tree("app/modules/faq.py")

    mdl = _func(files_tree, "format_doc_hits")
    stu = _func(files_tree, "format_doc_hits_for_student")
    c.check("D1 两个视图**都**经 `pick_diverse` 选块（配额只有一处，视图各自给预算）",
            "pick_diverse" in _calls_in(mdl) and "pick_diverse" in _calls_in(stu),
            f"模型={sorted(_calls_in(mdl))}｜学生={sorted(_calls_in(stu))}")
    c.check("D2 学生视图的块数是**必填**参数（默认值一旦等于模型那份，4 块原文就会贴进气泡）",
            _kwarg_required(stu, "limit"), _sig(stu))

    parts = _func(faq_tree, "_kb_parts")
    c.check("D3 `_kb_parts` 不再给文档那一路先砍一刀（以前 `[:FAQ_DOC_HITS]` 连模型一起卡成两块）",
            not _slices_with(parts, "FAQ_DOC_HITS"), _subscripts(parts))
    with sandbox():
        _env()
        c.check("D3-bis 而且它**真没砍**：同一句查询，`_kb_parts` 交出去的块数多于学生那一路渲染的段数"
                "（钉的是行为，不是'源码里还有没有那行'——后者会被注释绊红）",
                _kbparts_more_than_student())
    c.check("D4 三个预算常量各只有一份定义（DOC_TOP_K / DOC_PER_SOURCE / FAQ_DOC_HITS）",
            _assign_count(files_tree, "DOC_TOP_K") == 1 and
            _assign_count(files_tree, "DOC_PER_SOURCE") == 1 and
            _assign_count(faq_tree, "FAQ_DOC_HITS") == 1)
    c.check("D5 `DOC_PER_SOURCE` 是 `pick_diverse` 的默认参数（改一处全站生效，没抄进函数体）",
            "DOC_PER_SOURCE" in _defaults_of(files_tree, "pick_diverse"),
            str(_defaults_of(files_tree, "pick_diverse")))
    return c


# ---------------- AST 小工具 ----------------

def _tree(rel):
    return ast.parse(open(os.path.join(PROJ_DIR, *rel.split("/")), encoding="utf-8").read())


def _func(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _calls_in(fn):
    out = set()
    if not fn:
        return out
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _subscripts(fn):
    return [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Subscript)] if fn else []


def _slices_with(fn, const):
    """函数体里有没有形如 `xxx[:const]` 的切片（有就是它自己砍了块数）。"""
    if not fn:
        return False
    return any(isinstance(n, ast.Subscript) and const in ast.unparse(n.slice)
               for n in ast.walk(fn))


def _assign_count(tree, name):
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    n += 1
    return n


def _defaults_of(tree, fname):
    fn = _func(tree, fname)
    if not fn:
        return set()
    out = set()
    for d in list(fn.args.defaults) + [x for x in fn.args.kw_defaults if x]:
        out |= {n.id for n in ast.walk(d) if isinstance(n, ast.Name)}
    return out


def _sig(fn):
    return ast.unparse(fn.args) if fn else "<找不到函数>"


def _kwarg_required(fn, arg):
    """`arg` 是不是**仅关键字且无默认值**（必填）。

    为什么钉这个：学生视图的 `limit` 以前默认是 `DOC_TOP_K` —— 那就是"模型预算"漏进
    学生气泡的口子，少一个调用点传参就悄悄把四块原文贴给学生。必填之后新增调用点必须显式表态。
    """
    if not fn:
        return False
    for kw, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if kw.arg == arg:
            return d is None
    return False


def _kbparts_more_than_student():
    from app.modules.faq import FAQ_DOC_HITS, _kb_parts, answer_for_student
    hits = _kb_parts("补办学生证 工本费")[1]
    segs = answer_for_student("补办学生证 工本费").count("》里是这么写的")
    return len(hits) > segs == FAQ_DOC_HITS


def main():
    global code
    print("\n" + "=" * 60)
    print("test_70_multi_doc_merge：#42-B 多份文档拼答案")
    print("=" * 60)
    for fn, name in ((batch_a_pick, "A 批（选块与配额）"),
                     (batch_b_views, "B 批（两个视图与真发）"),
                     (batch_c_prompts, "C 批（提示词同步）"),
                     (batch_d_shape, "D 批（形状）")):
        rc = fn().summary(name)
        code = code or rc
    print("\n退出码：", code)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
