# 意图抽取 / 路由接口测试报告（`POST /classify`）

> 本文件由 `tests/test_intent_classify.py` 自动生成 —— 改测试再跑一遍就会刷新，
> 不用手工维护（也不会对不上）。
>
> 跑法：`python tests/test_intent_classify.py`（或 `pytest tests/test_intent_classify.py`）
> ｜本次：共 62 项检查，通过 62，失败 0。
>
> 判定层是「样本库 → 语义层 → 规则兜底」三档，本报告每条都注明了**是哪一档判的**（`detail.source`），失败时能直接定位到该改哪一档。

## 一、通过用例

| 用例 | 检查项 | 结果 |
| --- | --- | --- |
| A1 | HTTP 200 | ✅ |
| A1 | is_question | ✅ |
| A1 | module | ✅ |
| A1 | intent | ✅ |
| A2 | HTTP 200 | ✅ |
| A2 | is_question | ✅ |
| A2 | module | ✅ |
| A2 | intent | ✅ |
| A3 | HTTP 200 | ✅ |
| A3 | is_question | ✅ |
| A3 | module | ✅ |
| A3 | intent | ✅ |
| B1 | HTTP 200 | ✅ |
| B1 | is_question | ✅ |
| B1 | module | ✅ |
| B1 | intent | ✅ |
| B2 | HTTP 200 | ✅ |
| B2 | is_question | ✅ |
| B2 | module | ✅ |
| B2 | intent | ✅ |
| C1 | HTTP 200 | ✅ |
| C1 | is_question | ✅ |
| C1 | module | ✅ |
| C1 | intent | ✅ |
| C2 | HTTP 200 | ✅ |
| C2 | is_question | ✅ |
| C2 | module | ✅ |
| C2 | intent | ✅ |
| C3 | HTTP 200 | ✅ |
| C3 | is_question | ✅ |
| C3 | module | ✅ |
| C3 | intent | ✅ |
| C4 | HTTP 200 | ✅ |
| C4 | module | ✅ |
| C4 | intent | ✅ |
| D1 | HTTP 200 | ✅ |
| D1 | is_question | ✅ |
| D1 | module | ✅ |
| D1 | intent | ✅ |
| D2 | HTTP 200 | ✅ |
| D2 | module | ✅ |
| D2 | intent | ✅ |
| E1 | 没配 key 也不崩（HTTP 200） | ✅ |
| E1 | 没 key 时 module 仍是合法值 | ✅ |
| E1 | 没 key 时不许假装是语义层判的 | ✅ |
| E2 | 样本库命中（source=sample） | ✅ |
| E2 | 样本库命中时类型按意图推出 planner | ✅ |
| E2 | intent 取自样本库 | ✅ |
| E3 | 规则层判出 add_todo（source=rule） | ✅ |
| E3 | 意图已定 → 类型跟着意图走（planner，不是默认 faq） | ✅ |
| E3 | intent 取自规则层 | ✅ |
| F1 | 空 message → 400（不猜一个意图出来） | ✅ |
| F1 | 400 时五个键仍在（前端不用判空） | ✅ |
| F2 | 模型自造的意图不认（不许它发明一种意图） | ✅ |
| F2 | 自造意图时 module 仍是合法值 | ✅ |
| F3 | confidence<0.6 → 不采用（source 不是 llm） | ✅ |
| F3 | 不采用时 confidence 回 0.0 | ✅ |
| F4 | history 条数如实回显（detail.history_used=2） | ✅ |
| F4 | history 传非列表也不许崩（当成空） | ✅ |
| F5 | 回包自证：wrote_anything=false | ✅ |
| F5 | 调完待办一条没多、一条没少（id 完全一致） | ✅ |
| F5 | 也没往会话历史里塞东西 | ✅ |

## 二、失败用例

（本次没有失败的检查项 ✅）

## 三、失败原因（期望 vs 实际）

（无失败，故无差异）

## 四、修复思路（来自用例自带的 `fix_hint`）

（无失败，无需修复。这一块的内容来自每条用例自带的 `fix_hint` 字段，
**不是事后由模型现场编的**：写用例的时候就把「这条挂了该去改哪儿」
写在旁边，失败报告直接读它。）

---

## 附：本次覆盖的用例清单

| 用例 | 学生原话 | 期望（要点） |
| --- | --- | --- |
| A1 | 图书馆几点开门 | is_question=True、module=faq、intent=chat |
| A2 | 我要去哪里取快递 | is_question=True、module=faq、intent=chat |
| A3 | 我明天什么时候没课 | is_question=True、module=schedule、intent=chat |
| B1 | 帮我加个游泳周四19:00 | is_question=False、module=planner、intent=add_todo |
| B2 | 我想去游泳，帮我安排个时间 | is_question=False、module=planner、intent=add_todo |
| C1 | 把游泳删了 | is_question=False、module=planner、intent=remove_todo |
| C2 | 把游泳标记完成 | is_question=False、module=planner、intent=done_todo |
| C3 | 把周四那个健身挪到周五 | is_question=False、module=planner、intent=retime_todo |
| C4 | 我都有啥待办来着 | module=planner、intent=list_todo |
| D1 | 我要去哪里取凯迪 | is_question=True、module=faq、intent=chat |
| D2 | 把游永删了 | module=planner、intent=remove_todo |

其余为 E 组（离线三档）与 F 组（结构 / 白名单 / 不写库红线），断言写在 `batch_offline` / `batch_structure` 里。
