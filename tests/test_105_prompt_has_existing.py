# -*- coding: utf-8 -*-
"""#105 §1.2 / §1.4：建服务时把**已有的说法**列给模型看，被剔掉的那条只进日志不进界面。

§1.1（撞了就剔）在 `test_105_trigger_clash_trim.py`；那一半是**止损**，这一半才是治根：
`_SERVICE_PROMPT` 原文没有一个字提到已有服务（实测 `main.py:5353-5363`），
模型写「空调不制冷」不是犯错，是它压根不知道这个词被占了 ——
**判据要求模型避开一个它看不见的东西，那是判据的问题。**

三条判据的形状（照红线 29：钉"哪一句落在哪一支"，不钉字符串存在性）：
  T4   清单函数自己列了什么、服务表为空时列不列；
  T4-c 截断规则是真的（每项 5 条 / 总共 12 项）—— prompt 是要花钱的；
  T6   跑一遍 `_draft_service`：**发给模型的 system 里带着清单**，
       而**返回给界面的那段 JSON 里没有**被剔的那个词（它只出现在 stdout 那一行日志里）。
"""
import contextlib
import io
import json

from _harness import Checker, data_path, make_client, sandbox, title

code = 0
PKGLOSS = {"id": "pkgloss", "name": "快递/外卖问题处理", "triggers": ["快递丢了"],
           "collect_fields": ["单号或取件码"], "reply_template": "已登记", "table": "workorders"}
DORM = {"id": "5e702308", "name": "宿舍报修", "triggers": ["宿舍灯坏了", "水管漏水怎么报修",
          "空调不制冷", "门锁坏了找谁", "报修要多久"],
        "collect_fields": ["房间号"], "reply_template": "已登记。", "table": "repair"}
CLASSROOM = {"name": "教室报修", "triggers": ["教室灯坏了", "投影仪打不开", "空调不制冷",
             "桌椅损坏要修", "多媒体设备故障"],
             "collect_fields": ["教室编号", "故障设备"], "reply_template": "已登记报修。",
             "table": "repair"}


def _seed(items):
    json.dump(items, open(data_path("services.json"), "w", encoding="utf-8"), ensure_ascii=False)


def batch_a_block_shape():
    c = Checker()
    title("A. `_existing_trigger_block()`：列得出已有说法、空表不硬凑、长表会截断")
    with sandbox():
        from app import main as M
        _seed([dict(PKGLOSS), dict(DORM)])
        blk = M._existing_trigger_block()
        c.check("T4-a 清单里**逐条列出**已有服务与它们的说法（含「宿舍报修」与那条被占的「空调不制冷」）",
                "宿舍报修" in blk and "空调不制冷" in blk and "快递/外卖问题处理" in blk,
                f"{len(blk)} 字")
        c.check("T4-b 说明那句写的是「不许重复」**并且把'加长也不行'讲清了**"
                "（只说别重复，模型就会去写「空调不制冷登记」—— 判据是双向子串，那照样撞）",
                "不许" in blk and "空调不制冷登记" in blk, blk[:60])
        dorm_line = next((ln for ln in blk.splitlines() if "宿舍报修" in ln), "")
        c.check("T4-c 每项最多 5 条：宿舍报修自己那 5 条列全了，但不会出现第 6 条",
                dorm_line.count(" / ") == 4, dorm_line[:70])

        _seed([dict(PKGLOSS, triggers=["快递丢了", "a", "b", "c", "d", "e", "f", "g"])])
        blk2 = M._existing_trigger_block()
        line = next((ln for ln in blk2.splitlines() if "快递/外卖问题处理" in ln), "")
        c.check("T4-d 每项截断到 5 条（内置那项有 8 条词，也只列 5 条）—— 服务建到第 30 个时"
                "正确答案是去做服务名检索，不是往 prompt 里塞整张表",
                line.count(" / ") == 4, line[:80])

        many = [dict(PKGLOSS)] + [
            {"id": f"s{i}", "name": f"服务{i}", "triggers": [f"说法{i}-{k}" for k in range(6)],
             "collect_fields": ["物品描述"], "reply_template": "已登记。", "table": "lostfound"}
            for i in range(20)]
        _seed(many)
        blk3 = M._existing_trigger_block()
        listed = [ln for ln in blk3.splitlines() if ln.startswith("  ")]
        c.check("T4-e 总共最多 12 项（种 21 项也只列 12 行）", len(listed) == 12, f"{len(listed)} 行")

        old = M.list_services
        M.list_services = lambda: []
        try:
            empty = M._existing_trigger_block()
        finally:
            M.list_services = old
        c.check("T4-f 表里一项都没有 → 返回空串（不许拼出一段「【已有的服务】」后面跟空白，"
                "那是给模型的噪声）", empty == "", repr(empty[:30]))
    return c


def batch_b_draft_flow():
    c = Checker()
    title("B. 走一遍建服务：清单进了 prompt，被剔的那条只进日志")
    with sandbox():
        from app import main as M
        _seed([dict(PKGLOSS), dict(DORM)])
        seen = {}

        class _CapGateway:
            def __init__(self, *a, **k):
                pass

            async def chat(self, messages, **k):
                seen["system"] = messages[0]["content"]
                return {"content": json.dumps(CLASSROOM, ensure_ascii=False)}

        old = M.ModelGateway
        M.ModelGateway = _CapGateway
        cli = make_client()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                r = cli.post("/api/admin/services", json={"description": "教室报修"})
        finally:
            M.ModelGateway = old
        log = buf.getvalue()
        body = json.dumps(r.json(), ensure_ascii=False)
        svc = r.json().get("service") or {}

        c.check("T6-a 建成（不再因为模型编的一条废话吃 400）",
                r.status_code == 200 and svc.get("name") == "教室报修",
                f"{r.status_code} {body[:70]}")
        c.check("T6-b **发给模型的那条 system 消息里带着已有服务的清单**（不是只把函数写出来没人调）",
                "宿舍报修" in seen.get("system", "") and "空调不制冷" in seen.get("system", "")
                and seen["system"].startswith(M._SERVICE_PROMPT[:20]),
                f"system {len(seen.get('system', ''))} 字")
        c.check("T6-c 返回给界面的那段 JSON 里**不含**「空调不制冷」—— 那五个字不是管理员说的，"
                "成功之后再念一遍只会让他以为漏了什么（施工单 §2.3）",
                "空调不制冷" not in body, body[:70])
        c.check("T6-d 但日志里那一行有：`[低代码] 剔掉 1 条撞车触发词：[...]`（剔了谁必须能查）",
                "[低代码] 剔掉 1 条撞车触发词" in log and "空调不制冷" in log, log.strip()[:80])
        c.check("T6-e 落库那份是剔完的 4 条（界面与磁盘一致，不是只在响应里少一条）",
                svc.get("triggers") == ["教室灯坏了", "投影仪打不开", "桌椅损坏要修", "多媒体设备故障"],
                json.dumps(svc.get("triggers"), ensure_ascii=False)[:80])
        disk = [s for s in json.load(open(data_path("services.json"), encoding="utf-8"))
                if s.get("name") == "教室报修"]
        c.check("T6-f 磁盘上那条也是 4 条，且 `dept` 是路由定的「后勤」（请求体里传什么都不算）",
                len(disk) == 1 and len(disk[0]["triggers"]) == 4 and disk[0].get("dept") == "后勤",
                json.dumps(disk and disk[0].get("dept"), ensure_ascii=False))

        # 正控那一支：**什么都没剔**的时候不许打那行日志 —— 否则日志里全是空汇报，
        # 真被撞掉一条时反而找不到。同一个桩换个"干净定义"再走一遍。
        clean_def = {"name": "失物登记", "triggers": ["捡到手表交哪"], "collect_fields": ["物品描述"],
                     "reply_template": "已登记。", "table": "lostfound"}

        class _CleanGateway(_CapGateway):
            async def chat(self, messages, **k):
                seen["system"] = messages[0]["content"]
                return {"content": json.dumps(clean_def, ensure_ascii=False)}

        M.ModelGateway = _CleanGateway
        buf2 = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf2):
                r2 = cli.post("/api/admin/services", json={"description": "捡到手表交哪"})
        finally:
            M.ModelGateway = old
        c.check("T6-g 一条都没剔 → 200 且**不打**那行日志（`dropped` 为空就不该汇报）",
                r2.status_code == 200 and "[低代码] 剔掉" not in buf2.getvalue(),
                repr(buf2.getvalue()[:70]))
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_105_prompt_has_existing：#105 §1.2 清单进 prompt + §1.4 剔掉的只进日志")
    print("=" * 60)
    for fn, name in ((batch_a_block_shape, "A 批（清单函数自己的形状）"),
                     (batch_b_draft_flow, "B 批（建服务整条链路）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
