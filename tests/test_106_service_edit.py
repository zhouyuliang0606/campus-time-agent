"""#106：服务定义建完就锁死 —— 编辑（两端共用一份判据）、后勤删除、自己别撞自己。

用户 2026-11-01 23:57 原话：「增加服务后不能删除/改」「宿舍报修把『报修要多久』
写进关键词，想改改不了」。施工单 `106_服务定义建完就锁死_后勤不能改不能删_drop-in.md`。

三批：
  A 批 —— `validate_service_def(..., exclude_id=)`：编辑一条已存在的定义时，
          **名字**与**触发词**两道闸都必须先把它自己从"已被占用"里拿掉；
          不传 exclude_id 时照旧拒（证明这个参数真的有牙，不是摆设）。
  B 批 —— 两端 PUT / 管理端 DELETE 的整条路：越权 403、内置 400、删除只删定义不动台账。
  C 批 —— 现场那条真实定义（5e702308 宿舍报修）端到端走一遍：
          去掉「报修要多久」能存进去，存完学生端的触发词匹配也确实换了。

⚠️ 起手全部自种（services.json / custom 都在 sandbox 里写），不读仓库里那份演示数据。
"""
import json
import os

from _harness import Checker, data_path, make_client, sandbox, title

code = 0

PKGLOSS = {"id": "pkgloss", "name": "快递/外卖问题处理", "dept": "驿站",
           "triggers": ["快递丢了", "外卖丢了"], "collect_fields": ["物品描述"],
           "reply_template": "已登记", "table": "workorders",
           "created_at": "2026-10-22 11:14:52"}
DORM = {"id": "5e702308", "name": "宿舍报修", "dept": "后勤", "table": "repair",
        "triggers": ["宿舍灯坏了", "水管漏水怎么报修", "空调不制冷", "门锁坏了找谁", "报修要多久"],
        "collect_fields": ["宿舍楼栋", "房间号", "故障描述", "联系方式"],
        "reply_template": "已登记报修，维修师傅会尽快上门处理。",
        "created_at": "2026-10-22 11:14:52"}
LOST = {"id": "x1", "name": "失物招领", "dept": "驿站", "table": "lostfound",
        "triggers": ["丢了饭卡"], "collect_fields": ["物品描述"],
        "reply_template": "已登记", "created_at": "2026-10-22 11:14:52"}


def _seed_services(items):
    json.dump(items, open(data_path("services.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _seed_records(sid, rows):
    os.makedirs(data_path("custom"), exist_ok=True)
    json.dump(rows, open(data_path("custom", f"{sid}.json"), "w", encoding="utf-8"),
              ensure_ascii=False)


def _read_json(p, default):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else default


def _draft(svc, **patch):
    """候选定义只取五个白名单键 —— 与 `main._apply_service_edit` 同一份规则（#106 实测）：
    `dict(svc)` 会把 dept / created_at 带进去，被白名单当"不认识的字段"拒掉。"""
    d = {k: svc.get(k) for k in ("name", "triggers", "collect_fields", "reply_template", "table")}
    d["id"] = svc.get("id")
    d.update(patch)
    return d


def batch_a_exclude_self():
    c = Checker()
    title("A. 编辑自己别跟自己撞（exclude_id 的两道闸）")
    with sandbox():
        from app import store as S
        _seed_services([dict(PKGLOSS), dict(DORM)])
        edited = _draft(DORM, triggers=[t for t in DORM["triggers"] if t != "报修要多久"])

        clean, why = S.validate_service_def(dict(edited))
        c.check("T1-a 不传 exclude_id → 拒（自己的名字跟自己重名；证明这个参数真有牙）",
                clean is None and "已经有一个叫「宿舍报修」" in why, why[:70])
        clean, why = S.validate_service_def(edited, exclude_id="5e702308")
        c.check("T1-b 传 exclude_id → 过，且只删掉了一条触发词",
                clean is not None and len(clean["triggers"]) == 4, why[:70])

        # 只改名字、触发词一个字不动：这是"重名闸排不排自己"最干净的一刀
        renamed = _draft(DORM, name=DORM["name"])
        _, why2 = S.validate_service_def(renamed, exclude_id="5e702308")
        c.check("T2 名字原样不动 + exclude_id → 过（重名闸不再说「已经有一个叫…」）",
                why2 == "", why2[:70])

        before = S.get_service("5e702308")
        merged = S.update_service("5e702308", {"triggers": ["宿舍灯坏了"], "dept": "驿站",
                                               "id": "hacked01"})
        after = S.get_service("5e702308") or {}
        c.check("T3 update_service 保留 id / dept / created_at（patch 里塞的 id/dept 不算数）",
                merged is not None and bool(after)
                and after["id"] == before["id"] and after["dept"] == "后勤"
                and after["created_at"] == before["created_at"]
                and after["triggers"] == ["宿舍灯坏了"],
                f"{after.get('id')}/{after.get('dept')}/{after.get('created_at')}")

        clash = _draft(DORM, triggers=["快递丢了"])
        clean4, why4 = S.validate_service_def(clash, exclude_id="5e702308")
        c.check("T4 编辑照样过撞车闸（撞内置 pkgloss 的词 → 拒，回话点名撞了哪一项）",
                clean4 is None and "撞了" in why4 and "快递/外卖问题处理" in why4, why4[:80])

        q = {"name": "报修进度咨询", "triggers": ["报修要多久才有人来"],
             "collect_fields": ["楼栋"], "reply_template": "已登记，会转给维修师傅。",
             "table": "repair"}
        clean9, why9 = S.validate_service_def(q)
        c.check("T9 问句当触发词**不被硬拦**（这单只提示、不设判据；硬拦要改的是匹配层，另立一单）",
                clean9 is not None, why9[:70])
    return c


def batch_b_routes():
    c = Checker()
    title("B. 两端 PUT / 管理端 DELETE（越权 403、内置 400、删定义不动台账）")
    with sandbox():
        cli = make_client()
        _seed_services([dict(PKGLOSS), dict(DORM), dict(LOST)])

        r = cli.put("/api/station/services/5e702308", json={"triggers": ["宿舍灯不亮了"]})
        disk = _read_json(data_path("services.json"), [])
        dorm = next(s for s in disk if s["id"] == "5e702308")
        c.check("T5 驿站端改后勤的服务 → 403，且**磁盘上那条一个字没动**",
                r.status_code == 403 and dorm["triggers"] == DORM["triggers"],
                f"{r.status_code} {r.json().get('error', '')[:40]}")

        r = cli.put("/api/admin/services/x1", json={"triggers": ["捡到饭卡怎么交"]})
        c.check("T5-b 管理端改驿站的服务 → 403（两个端各管各的，同一条 dept 判据）",
                r.status_code == 403, f"{r.status_code} {r.json().get('error', '')[:50]}")

        r = cli.put("/api/station/services/pkgloss", json={"triggers": ["快递没了"]})
        c.check("T6 内置那项（table=workorders）→ 400「内置那项不能改」",
                r.status_code == 400 and "内置" in (r.json().get("error") or ""),
                f"{r.status_code} {r.text[:60]}")
        r = cli.put("/api/admin/services/pkgloss", json={"triggers": ["快递没了"]})
        c.check("T6-b 管理端碰内置那项 → 403（先被部门闸拦住：它归驿站）",
                r.status_code == 403, f"{r.status_code} {r.text[:60]}")

        r = cli.put("/api/admin/services/nosuch", json={})
        c.check("T6-c 改不存在的服务 → 404 一句人话",
                r.status_code == 404 and "不存在" in (r.json().get("error") or ""),
                f"{r.status_code} {r.text[:60]}")

        # 路由**真的调了** §1.1 那道校验（T4 只证明校验函数有牙；绕过它的写口一样有牙才叫护栏）。
        # 只删 store 那半条或只删路由那半条，两边各红一条 —— 变异 M5 打的就是这一条。
        r = cli.put("/api/admin/services/5e702308", json={"triggers": ["快递丢了"]})
        disk_now = next(s for s in _read_json(data_path("services.json"), [])
                        if s["id"] == "5e702308")
        c.check("T4-bis 编辑口走的是同一份校验：撞内置的词 → 400「撞了」，磁盘上那五条一个字没动",
                r.status_code == 400 and "撞了" in (r.json().get("error") or "")
                and disk_now["triggers"] == DORM["triggers"],
                f"{r.status_code} {r.text[:70]}")

        ok = cli.put("/api/admin/services/5e702308",
                     json={"triggers": ["宿舍灯坏了", "水管漏水怎么报修", "空调不制冷", "门锁坏了找谁"]})
        d = ok.json().get("service") or {}
        c.check("T7-a 管理端改自己的服务 → 200，返回体就是改后的定义",
                ok.status_code == 200 and d.get("id") == "5e702308"
                and d.get("triggers") == ["宿舍灯坏了", "水管漏水怎么报修", "空调不制冷", "门锁坏了找谁"],
                f"{ok.status_code} {len(d.get('triggers') or [])} 条")

        _seed_records("5e702308", [{"id": "r1", "ts": "2026-11-01 00:00:00",
                                    "service": "5e702308", "name": "宿舍报修",
                                    "fields": {"房间号": "301"}, "status": "待处理"}])
        rm = cli.delete("/api/admin/services/5e702308")
        left = [s["id"] for s in _read_json(data_path("services.json"), [])]
        rows = _read_json(data_path("custom", "5e702308.json"), None)
        c.check("T7-b 管理端删后勤的服务 → 200；定义没了、**它名下的台账一条不少**",
                rm.status_code == 200 and "5e702308" not in left
                and isinstance(rows, list) and len(rows) == 1,
                f"{rm.status_code} left={left} 台账={rows and len(rows)} 条")
        again = cli.delete("/api/admin/services/5e702308")
        c.check("T7-c 再删一次 → 400（不谎报 ok=True，口径与驿站端逐字一致）",
                again.status_code == 400 and not again.json().get("ok"), repr(again.text[:60]))

        r = cli.delete("/api/admin/services/x1")
        c.check("T7-d 管理端删驿站的服务 → 403（别的部门的东西不归这儿删）",
                r.status_code == 403, f"{r.status_code} {r.text[:50]}")
    return c


def batch_c_real_definition():
    c = Checker()
    title("C. 现场那条定义（宿舍报修）端到端：去掉「报修要多久」")
    with sandbox():
        from app import store as S
        cli = make_client()
        _seed_services([dict(PKGLOSS), dict(DORM)])

        before = S.match_service_by_trigger("报修要多久")
        c.check("C0 改前：「报修要多久」确实被宿舍报修接走（施工单 §0.5 那条事故）",
                (before or {}).get("id") == "5e702308", str((before or {}).get("name")))

        r = cli.put("/api/admin/services/5e702308",
                    json={"triggers": ["宿舍灯坏了", "水管漏水怎么报修", "空调不制冷", "门锁坏了找谁"]})
        after = S.match_service_by_trigger("报修要多久")
        still = S.match_service_by_trigger("宿舍灯坏了")
        c.check("C1 改后立刻生效（不用重启）：「报修要多久」不再被接走",
                r.status_code == 200 and after is None, f"{r.status_code} {str(after)[:50]}")
        c.check("C2 其余四条照旧命中（不是把整项服务改没了）",
                (still or {}).get("id") == "5e702308", str((still or {}).get("name")))
        c.check("C3 id 没变（台账 custom/5e702308.json 还接得上）",
                (S.get_service("5e702308") or {}).get("id") == "5e702308", "")
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_106_service_edit：#106 服务定义可改（两端）+ 后勤可删")
    print("=" * 60)
    for fn, name in ((batch_a_exclude_self, "A 批（exclude_id 的两道闸）"),
                     (batch_b_routes, "B 批（两端 PUT / 管理端 DELETE）"),
                     (batch_c_real_definition, "C 批（现场定义端到端）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
