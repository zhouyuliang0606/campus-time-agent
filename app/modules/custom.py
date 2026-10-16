"""驿站低代码服务在学生端的模块（#22 单子 4）。

判定层认得出 `custom:<服务id>` 之后（`app/agent/intent.py::module_ok`），得有个地方**接住**这句话。
这个模块就是那个"接住"：

  ① 把服务定义拼成系统提示（**每次现读** `services.json`，管理员改了下一句就生效，不用重启）；
  ② 给模型唯一一个"出卡"工具 `propose_custom_record`。

⚠️ 这里守着本项目最要紧的一条边界，而且低代码把它逼到了极限：
   **模型手里从头到尾没有一个写入动作。** `propose_custom_record` 只是把一张
   「要不要登记这一条」的纸交给系统暂存（`app/agent/pending.py`），学生点了确认之后，
   由 `main.py` 里那段**系统代码**（`/api/custom/records`）才写进 `custom/<id>.json`。
   低代码之所以敢开放给管理员，靠的就是这条边界 ——
   他能加"这项服务收什么、登记完回什么话"，**加不出**"谁来写库、写到哪张表"。

   另一半边界在判定层：语义层给的 service_id 一律回查 `services.json`，编的就丢弃
   （`store.service_of_module`），所以模型也没法靠猜一个 id 来指定写哪本台账。
"""
from __future__ import annotations

import json

from app import store
from app.agent.tools import Tool

CARD_KIND = "custom_record"


def _clean_fields(service: dict, raw) -> dict:
    """按服务定义收字段：**多给的键不收，长的截断，没说的留空**。

    规则与驿站端那条确定性登记完全同源（都用 `collect_fields` + `CUSTOM_VALUE_MAX`），
    不许出现"卡上三个字段、落库五个键"那种两本账。
    """
    want = [str(f) for f in (service.get("collect_fields") or [])]
    src = raw if isinstance(raw, dict) else {}
    out: dict = {}
    for k in want:
        v = str(src.get(k) or "").strip()
        out[k] = v[: store.CUSTOM_VALUE_MAX]
    return out


def _summary(service: dict, fields: dict) -> str:
    got = [f"{k}：{v}" for k, v in fields.items() if v]
    if not got:
        return f"登记「{service.get('name') or '这项服务'}」（信息还没填）"
    return f"登记「{service.get('name') or '这项服务'}」：" + "；".join(got[:4])


def make_card(service: dict, fields) -> dict:
    """把一份（可能是残缺的）登记内容变成一张**待确认的卡**。

    残缺是允许的、也是常态：学生只说「我丢了饭卡」，地点和时间他还没说 ——
    卡片上那两格留空，确认页会让他补填（**系统不许替他编**）。
    """
    clean = _clean_fields(service, fields)
    return {
        "kind": CARD_KIND,
        "service": str(service.get("id") or ""),
        "name": str(service.get("name") or ""),
        "fields": clean,
        # 确认页按这个顺序渲染输入框（不靠 dict 顺序，也防止前端自己猜一份字段表）
        "field_order": [str(f) for f in (service.get("collect_fields") or [])],
        "summary": _summary(service, clean),
        # 落库之后回给学生的那句话，用的是**服务自己定义**的话术
        "reply": str(service.get("reply_template") or "已登记。"),
    }


def custom_prompt(service: dict) -> str:
    """这项服务的系统提示（每次现算：管理员改完定义立刻生效）。"""
    name = service.get("name") or "未命名服务"
    fields = [str(f) for f in (service.get("collect_fields") or [])]
    trig = [str(t) for t in (service.get("triggers") or [])][:6]
    return f"""你是校园驿站「{name}」这项服务的登记员。
这项服务是驿站管理员自己开出来的，学生会这样说它：{('、'.join(trig)) or '（管理员没给触发说法）'}。
登记一条需要这些信息：{('、'.join(fields)) or '（没配字段）'}。

你**只有一个动作**：调用 `propose_custom_record`，把一张「要不要登记这一条」的确认条挂出来。
- 学生说清楚了几项就填几项，**没说的就留空**（确认页会让他补），
  不许替他编一个地点、时间、电话；
- 字段名必须严格用上面那几项，别自己发明新字段（多给的键系统会丢掉）；
- 学生如果只是**在问**（"失物去哪儿领""要带什么"），就直接回答他，**别挂卡**；
- ⛔ 严禁说"已经登记好了""已经通知你来领了"——学生点【确认】之前，台账里一个字都没有。
  话到嘴边也只能说"确认条挂在下面了，点一下才登记"。
"""


def custom_tools(service: dict) -> dict:
    """这项服务给模型的工具箱 —— **就一件，而且它不写库**。

    为什么不开第二个工具（比如"直接登记"）：那等于把红线 1 的口子还给模型。
    低代码服务是运行时数据，今天能给失物招领开写入，明天就能给任何一句
    管理员随口生成的定义开写入 —— 那已经不是"扩展服务"，是"扩展权限"。
    """
    sid = str(service.get("id") or "")

    def propose_custom_record(fields: dict | str | None = None) -> str:
        """出卡，不落库：返回值里的 `__proposal__` 会被引擎抓走变成确认条。"""
        if isinstance(fields, str):
            try:
                fields = json.loads(fields)
            except Exception:
                fields = None
        card = make_card(service, fields)
        if not card["service"]:
            return json.dumps({"error": "这项服务没有 id，登记不了",
                               "hint": "别挂卡，如实告诉学生这项服务坏了，请管理员重建。"},
                              ensure_ascii=False)
        empty = [k for k, v in card["fields"].items() if not v]
        hint = ("确认条已经挂在下面了。学生点【确认登记】才会写进台账（那是系统做的，不是你）；"
                "点【取消】就一个字都不写。"
                + (f"这几项还没信息：{('、'.join(empty))} —— 让学生在确认条上补，"
                   "不许自己编，也不许替他猜。" if empty else "")
                + "⛔ 严禁说已经登记好了。")
        return json.dumps({"__proposal__": card, "you_told_student": hint},
                          ensure_ascii=False)

    props = {str(f): {"type": "string"} for f in (service.get("collect_fields") or [])}
    return {
        "propose_custom_record": Tool(
            name="propose_custom_record",
            description=(f"为学生挂出一张「{service.get('name') or '这项服务'}」的登记确认条"
                         "（只是提案，**不会**写库；学生点确认才由系统写入）。"
                         "fields 里只能填这项服务规定的字段名，没听到的留空字符串。"),
            parameters={
                "type": "object",
                "properties": {
                    "fields": {"type": "object", "properties": props,
                               "description": "从学生这句话里读到的登记信息，键必须与上面一致"},
                },
                "required": ["fields"],
            },
            func=propose_custom_record,
        ),
    }
