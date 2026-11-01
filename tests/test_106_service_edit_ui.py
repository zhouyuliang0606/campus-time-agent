"""#106 的前端入口：管理端「后勤报修」卡上的编辑/删除、驿站端服务卡上的编辑。

为什么单开一个文件：这一单的后端判据由 `test_106_service_edit.py` 钉着，
前端这几颗按钮**一个都不会被那些接口测试碰到** —— 接口全对而按钮没接上，
用户看到的仍然是"改不了"。所以这里按红线 29 的规矩钉**哪一段落在哪一个处理器里**，
不钉"文件里出现过某个词"。
"""
import os

from _harness import Checker, PROJ_DIR, title

STATION = os.path.join(PROJ_DIR, "app", "static", "station.html")
ADMIN = os.path.join(PROJ_DIR, "app", "static", "admin.html")

HINT = "疑问句（「报修要多久」这种）不要写进来"


def _src(p):
    return open(p, encoding="utf-8").read()


def batch_admin_page(c=None):
    c = c or Checker()
    title("T8-A 管理端：后勤报修卡的两颗按钮与它们的处理器")
    adm = _src(ADMIN)
    c.check("T8-A1 卡片里有【编辑触发词】【删除服务】两颗按钮（data-rp-edit / data-rp-del）",
            'data-rp-edit="' in adm and 'data-rp-del="' in adm, "")

    i = adm.find("async function rpEdit(id)")
    j = adm.find("function bindRepair(box)", i)
    seg = adm[i:j] if i >= 0 and j > i else ""
    c.check("T8-A2 编辑只递 triggers，打 PUT /api/admin/services/<id>",
            seg and "method: \"PUT\"" in seg
            and "/api/admin/services/" in seg and "JSON.stringify({ triggers:" in seg, "")
    c.check("T8-A3 编辑走自绘多行框（askPrompt + #promptMask），不用原生 prompt()",
            "await askPrompt({" in seg and 'id="promptMask"' in adm
            and "function askPrompt(" in adm and "window.prompt(" not in adm, "")
    c.check("T8-A4 提示语与驿站端一字不差（两个 html 不共享 JS，只能靠判据钉住）",
            HINT in seg and HINT in _src(STATION), "")

    k = adm.find("String(b.dataset.rpDel)")
    l = adm.find('rpSay("已删除服务，台账未动。")', k)
    del_seg = adm[max(k - 120, 0):(l + 80) if l > k else (k + 1600)]
    c.check("T8-A5 删除先过 askConfirm，确认框写明「只删定义、台账不动」+ 新服务接不上旧记录",
            "askConfirm({" in del_seg and "只删服务定义" in del_seg
            and "接不上旧记录" in del_seg and 'method: "DELETE"' in del_seg, "")
    c.check("T8-A6 改完/删完立刻 loadRepair()（不等下一轮 5 秒轮询）",
            "loadRepair();" in seg and "loadRepair();" in del_seg, "")
    c.check("T8-A7 rpGroups 里存着最近一次分组（弹窗要按 id 回查名字/触发词）",
            "rpGroups = groups;" in adm and "let rpGroups = [];" in adm, "")
    return c


def batch_station_page():
    c = Checker()
    title("T8-B 驿站端：服务卡上的【编辑触发词】")
    sta = _src(STATION)
    i = sta.find('edit.setAttribute("data-svc-edit"')
    j = sta.find("el.appendChild(edit)", i)
    seg = sta[i:j] if i >= 0 and j > i else ""
    c.check("T8-B1 自建服务卡上有编辑按钮（data-svc-edit）；内置那项在同一个 if 里被排除",
            'data-svc-edit' in sta and seg and 'String(s.table || "") !== "workorders"' in sta, "")
    c.check("T8-B2 打 PUT /api/station/services/<id>，只递 triggers",
            seg and "method: \"PUT\"" in seg
            and "/api/station/services/" in seg and "JSON.stringify({ triggers" in seg, "")
    c.check("T8-B3 走自绘多行框（stPrompt + #promptMask），不用原生 prompt()",
            "await stPrompt({" in seg and 'id="promptMask"' in sta
            and "function stPrompt(" in sta and "window.prompt(" not in sta, "")
    c.check("T8-B4 存完 loadServices(true)（不 force 就是「改掉了但屏幕上还是旧的」）",
            seg and "loadServices(true)" in seg, "")
    c.check("T8-B5 提示语与管理端一致（同一句话，见 T8-A4）",
            HINT in seg, "")
    return c


def main():
    code = 0
    print("\n" + "=" * 60)
    print("test_106_service_edit_ui：#106 前端两个入口")
    print("=" * 60)
    code |= batch_admin_page().summary("T8-A 批（管理端）")
    code |= batch_station_page().summary("T8-B 批（驿站端）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
