# -*- coding: utf-8 -*-
"""#105：撞车的触发词**逐条剔**，不再一条撂倒整份定义（但剔完不够数照样拒）。

用户 2026-11-01 23:47 在后勤报修页输「教室报修」，收到 400，报错念的是
「空调不制冷」—— 那五个字他从头到尾没打过（是模型自己编的一条），
而那份定义里另外 4 条干干净净。这单把两件事分开：
  · 「多写了一句废话」（本单：剔掉那条，剩下的能建就用）
  · 「把同一句话交给两个窗口」（#54 的 E8/E9：剔完只剩 1 条 → 仍然拒，**一条都不许改它们**）

七行情形表（§3.1 那张）逐条跑 —— 这单最容易写错的地方是**第一行**：
`SERVICE_FIELDS["triggers"] = (1, 10)` 允许只写 1 条，门槛若写成裸的
`len(kept) < TRIGGER_MIN_KEEP`，一类正在用的合法定义会被误拒（M6 钉这一条）。

⚠️ 起手全部自种。撞车判据读的是 `list_services()`，仓库里那份演示数据今天多了什么，
   明天就会决定这几行跑不跑得过。
"""
import json

from _harness import Checker, data_path, sandbox, title

code = 0
PKGLOSS = {"id": "pkgloss", "name": "快递/外卖问题处理", "triggers": ["快递丢了"],
           "collect_fields": ["单号或取件码"], "reply_template": "已登记", "table": "workorders"}
LOST = {"id": "x1", "name": "失物招领", "triggers": ["我丢了饭卡", "捡到东西交哪"],
        "collect_fields": ["物品描述"], "reply_template": "已登记。", "table": "lostfound"}
DORM = {"id": "5e702308", "name": "宿舍报修", "triggers": ["宿舍灯坏了", "水管漏水怎么报修",
          "空调不制冷", "门锁坏了找谁", "报修要多久"],
        "collect_fields": ["房间号"], "reply_template": "已登记。", "table": "repair"}
# 模型对「教室报修」的原话输出（施工单 §0.2，真跑一次拿到的那 5 条）
CLASSROOM = {"name": "教室报修", "triggers": ["教室灯坏了", "投影仪打不开", "空调不制冷",
             "桌椅损坏要修", "多媒体设备故障"],
             "collect_fields": ["教室编号", "故障设备"], "reply_template": "已登记报修。",
             "table": "repair"}


def _seed(items):
    json.dump(items, open(data_path("services.json"), "w", encoding="utf-8"), ensure_ascii=False)


def _defn(base, **kw):
    d = dict(base)
    d.update(kw)
    return d


def batch_a_seven_rows():
    c = Checker()
    title("A. 七行情形表：撞了就剔，剔完再数（第一行是那把刀的口）")
    with sandbox():
        from app import store as S
        _seed([dict(PKGLOSS), dict(LOST), dict(DORM)])

        clean, why = S.validate_service_def(dict(CLASSROOM))
        c.check("T1 5 条撞 1 条 → **过**，且留下的正好 4 条、不含「空调不制冷」"
                "（施工单 §0.4：这服务建起来完全能用，只是少一句本来就归宿舍的说法）",
                clean is not None and len(clean["triggers"]) == 4
                and "空调不制冷" not in clean["triggers"],
                f"{(clean or {}).get('triggers')} {(why or '')[:50]}")
        c.check("T1-bis 剔完剩下的 4 条**互不撞**（不是「剔出一条就顺手把另一条也放过」）",
                clean is not None and all(
                    not any(t2 in t1 or t1 in t2 for t1 in clean["triggers"] for t2 in
                            [str(x) for x in (DORM["triggers"] + LOST["triggers"] + PKGLOSS["triggers"])]
                            if t1 and t2) for t1 in clean["triggers"]),
                "没取到" if clean is None else "")

        one = S.validate_service_def({"name": "钥匙招领", "triggers": ["丢了钥匙在哪领"],
                                      "collect_fields": ["物品描述"], "reply_template": "已登记。",
                                      "table": "lostfound"})
        c.check("T7 只写 1 条、什么都没撞 → **过**（`SERVICE_FIELDS` 的下限就是 1，这是正在用的老路）",
                one[0] is not None and len(one[0]["triggers"]) == 1, (one[1] or "")[:70])
        two = S.validate_service_def({"name": "热水卡", "triggers": ["热水卡没钱了", "热水卡在哪里充"],
                                      "collect_fields": ["宿舍楼栋"], "reply_template": "已登记。",
                                      "table": "repair"})
        c.check("表①②行：1 条不撞 / 2 条不撞 —— 两道闸都不许把它们挡住",
                two[0] is not None and len(two[0]["triggers"]) == 2, (two[1] or "")[:70])

        e8, why8 = S.validate_service_def(_defn(LOST, name="校园卡",
                                               triggers=["我丢了饭卡了啊", "捡到东西交别处"]))
        c.check("T2 / 表④行：2 条剔 1 剩 1 → **拒**，而且回话点名撞了哪一项"
                "（这就是 E8/E9 保持绿的机制，本单不改那两条）",
                e8 is None and "撞了" in why8 and "失物招领" in why8 and "只剩 1 条" in why8,
                (why8 or "")[:100])
        e9, why9 = S.validate_service_def(_defn(LOST, name="包裹",
                                                triggers=["我快递丢了", "捡到东西交别处"]))
        c.check("表⑤行：撞的是内置那项 → 同样拒，`why` 里点名「快递/外卖问题处理」",
                e9 is None and "快递/外卖问题处理" in why9, (why9 or "")[:90])
        allout, why10 = S.validate_service_def(_defn(LOST, name="全撞",
                                                    triggers=["我丢了饭卡", "捡到东西交哪"]))
        c.check("表⑥行：两条全撞、剔到 0 条 → 拒（`只剩 0 条` 那句要说得出口，不许回空串）",
                allout is None and "只剩 0 条" in why10 and why10.strip() != "", (why10 or "")[:90])
        three, why11 = S.validate_service_def(_defn(LOST, name="三条剔一",
                                                   triggers=["我丢了饭卡了啊", "捡到东西交别处",
                                                             "水杯落在教室"]))
        c.check("表⑦行：3 条剔 1 剩 2 → 过（门槛是 2，不是 3）",
                three is not None and three["triggers"] == ["捡到东西交别处", "水杯落在教室"],
                f"{(three or {}).get('triggers')} {(why11 or '')[:60]}")
    return c


def batch_b_wording():
    c = Checker()
    title("B. 拒的时候那句人话：不许再给一个永远走不通的建议")
    with sandbox():
        from app import store as S
        _seed([dict(PKGLOSS), dict(LOST), dict(DORM)])
        msgs = []
        for body in (CLASSROOM["triggers"],
                     ["我丢了饭卡了啊", "捡到东西交别处"],
                     ["空调不制冷", "宿舍灯坏了"],
                     ["快递丢了", "外卖丢失"]):
            _, why = S.validate_service_def(_defn(LOST, name="话术样本", triggers=body))
            if why:
                msgs.append(why)
        c.check("T5-a 任何一条 `why` 里都不许再出现「更长的说法」"
                "（判据是双向子串，加长必然把被占的词包进来 —— 那是个给不出结果的建议）",
                len(msgs) >= 3 and not any("更长的说法" in m for m in msgs),
                f"取了 {len(msgs)} 句")
        c.check("T5-b 每一句都改成了「换个不含…」这个方向，并带上被占的那个词眼",
                all("不含" in m for m in msgs), (msgs[0] if msgs else "")[:90])
        c.check("T5-c 建议那半句是**方向**，不是替管理员编好的具体说法"
                "（原话那种「比如「空调不制冷登记」「空调不制冷怎么查」」两条自己就撞 —— "
                "判据是双向子串，加长必然把被占的词包进来）",
                all("不同的地方" in m and "怎么查」" not in m and "登记」" not in m for m in msgs),
                (msgs[0] or "")[-50:])
        c.check("T5-d 剔完放行时**一句都不说**（`why` 是空串，界面走「已建成」那条正常路）",
                S.validate_service_def(dict(CLASSROOM))[1] == "", "")
    return c


def batch_c_match():
    c = Checker()
    title("C. 剔掉之后这句话归谁：仍归宿舍报修（这不是缺陷，施工单 §3.3-2 明写别当 bug 修）")
    with sandbox():
        from app import store as S
        _seed([dict(PKGLOSS), dict(LOST), dict(DORM)])
        clean, _ = S.validate_service_def(dict(CLASSROOM))
        c.check("C0 先确认那份定义过了（下面三条都读这一份；没过就该**报红**而不是崩在这里）",
                clean is not None, "validate 回 None")
        if clean:
            S.add_service(clean, dept="后勤")
        hit = S.match_service_by_trigger("空调不制冷")
        c.check("C1 「空调不制冷」没有被谁抢走 —— 建了教室报修之后这句话仍归宿舍报修",
                (hit or {}).get("id") == "5e702308", str((hit or {}).get("name")))
        hit2 = S.match_service_by_trigger("投影仪打不开")
        c.check("C2 教室报修那 4 条照旧命中（剔一条不是把整项服务改没了）",
                (hit2 or {}).get("name") == "教室报修", str((hit2 or {}).get("name")))
        c.check("C3 落库那份就是剔完的 4 条（不是送进去的 5 条）",
                len((S.get_service((clean or {}).get("id") or "") or {}).get("triggers") or []) == 4,
                str((S.get_service((clean or {}).get("id") or "") or {}).get("triggers")))
    return c


def main():
    global code
    print("\n" + "=" * 60)
    print("test_105_trigger_clash_trim：#105 撞车逐条剔 + 剔完够数才放行")
    print("=" * 60)
    for fn, name in ((batch_a_seven_rows, "A 批（七行情形表）"),
                     (batch_b_wording, "B 批（拒的话术方向）"),
                     (batch_c_match, "C 批（剔完之后这句话归谁）")):
        code |= fn().summary(name)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
