"""模型润色 / 结果复述（架构升级：模型提案 → 系统裁决）。

一句话：把"系统算好的事实"交给模型**说人话**，而不是直接回一句硬编码模板。

## 为什么要有它
现状里 chat() 的确定性分支命中后，回的是一句**写死的模板话术**
（「📝 要不要把 **X** 排进日程？…」）—— 事实都对，但听着"呆"。
本模块承担流水线的**最后一步**（步骤 5）：系统把事实交给模型润色成人话。
**前面几步（提案、校验、出确认卡、执行写库）一个字节都不改**：
写库依旧系统独占，模型手里没有任何写接口。

## 三条红线（不可破）
1. **模型只复述、不决定事实**：facts 是系统算好的，模型一个字都不许新增/猜测/编造。
2. **失败即降级**：没配 key / 超时 / 报错 / 空回复 / 承诺词违规 —— 一律**原样退回
   调用方给的模板话术**（fallback），用户侧无感知、绝不报错白屏。
3. **承诺词守卫**：润色文本里若出现"已经帮你 / 写好了 / 已删除 / 安排好了"这类**已完成**
   的字眼，而本轮系统**没有**对应的已执行写操作 —— 判为违规，退回模板。
   （写操作必须等学生点确认；模型嘴上说"办好了"是最坑人的幻觉。）
4. **结果必须是自然语言**：模型若回成一坨 JSON / 数组（跑偏了，或测试里语义层那份
   假回复被同一次打桩复用过来）—— 判为无效，退回模板。润色绝不吐 JSON。

## 补丁可见性（踩过的坑）
客户端一律**在函数内** import（现在是 `from app.llm.gateway import ModelGateway`，
网关内部再按需 import 客户端）。
若放到模块顶部，就成了"导入时绑定"—— 测试的 `patch_llm` 打不进来，
润色链路会**安静地**打真网络（沙箱里就是"没 key → 抛异常 → 降级"，
于是"测试全绿、真机照样错"）。同理，`get_llm_config` 也函数内 import。
"""
import asyncio
import json

# 润色调用的时延阈值（秒）：超了就退回模板，绝不因为一次润色把整条回复拖住。
# 给 4 秒——润色是"锦上添花"，不值得让学生干等。
POLISH_TIMEOUT = 4.0

# 承诺词：润色文本里出现这些、而本轮**没有**已执行写操作 → 判违规。
# 收的是"清晰表达某个动作已经完成"的词；不跟"会/将/将为你"这类未来式。
_PROMISE_WORDS = (
    "已经帮你", "已帮你", "已经帮您", "已帮您",
    "帮你加好", "帮你排好", "帮你写好", "帮你放好",
    "写好了", "已经写好", "已写好", "已写入", "已经写入",
    "已加入", "已经加入", "已添加", "已经添加", "已记录", "已经记录",
    "已删除", "已经删除", "已删掉", "已经删掉",
    "安排好了", "已经安排", "已经安排好",
    "搞定了", "已完成", "已经完成", "已办好", "已经办好",
)

_POLISH_SYSTEM = (
    "你是校园时间管家。下面给你一份**系统已经算好的事实**（JSON）。"
    "你的唯一任务：把它用自然、口语、简短的中文**复述**给学生。\n"
    "铁律（违反任何一条都是严重错误）：\n"
    "① 只许复述事实里**已有**的信息，一个字都不许新增、不许猜测、不许编造；\n"
    "② 不许说「已经帮你 / 已经写好 / 已加入 / 已删除 / 安排好了」这类**已完成**的话 —— "
    "任何写操作都要学生点了确认之后才发生；\n"
    "③ 不要输出 JSON、不要加标题、不要堆砌表情符号；\n"
    "④ 事实里的每一项（列表条目、时间、标题）都要**原样保留、一个不漏**；\n"
    "⑤ 不要反问学生、不要写额外的推销话术，直给就好。"
)


def has_promise(text: str) -> bool:
    """这句话里有没有"我已替你办好"的承诺词。"""
    return any(w in (text or "") for w in _PROMISE_WORDS)


def llm_available(override: dict | None = None) -> bool:
    """现在配了 key 吗（服务器配置或学生端自定义都算）。没配就走离线兜底。"""
    try:
        from app.config import get_llm_config          # 函数内 import：看得到测试补丁
        return bool((get_llm_config(override=override) or {}).get("api_key"))
    except Exception:
        return False


async def polish_reply(facts: dict, fallback: str, *, override: dict | None = None,
                       timeout: float = POLISH_TIMEOUT, executed: bool = False) -> str:
    """把系统算好的事实交给模型说人话；**任何问题都退回 fallback**。

    :param facts:    系统算好的结构化事实（列表 / 卡片字段 / 成功与否）
    :param fallback: 降级话术（= 迁移前的模板文案；离线/失败时原样返回）
    :param override: 学生端自定义 API 配置（优先于服务器配置）
    :param timeout:  润色时延阈值，超时即降级
    :param executed: 本轮**是否真有**已执行的写操作（决定承诺词是否放行）
    :return: 润色后的话术，或 fallback。
    """
    # 没配 key：不必去打扰接口，直接降级（离线兜底，功能不丢）
    if not llm_available(override):
        return fallback
    try:
        from app.llm.gateway import ModelGateway      # 函数内 import：看得到测试补丁
        # 也走网关：润色同样是一次真实的大模型调用，别让它从用量统计里漏掉。
        gateway = ModelGateway(user="内部:话术润色", override=override)
        msg = await asyncio.wait_for(
            gateway.chat(
                [
                    {"role": "system", "content": _POLISH_SYSTEM},
                    {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
                ],
                override=override,
            ),
            timeout=timeout,
        )
        text = ((msg or {}).get("content") or "").strip()
    except Exception:
        # 超时 / LLMError（没 key、401、网络不通）/ 任何意外 —— 一律降级
        return fallback
    if not text:
        return fallback
    # 模型该回"人话"，回成一坨 JSON / 数组说明它跑偏了（或测试里语义层的那份假回复
    # 被同一次打桩复用到这里）→ 判为无效，退回模板。**润色结果永远是自然语言。**
    if text[:1] in ("{", "["):
        return fallback
    # 承诺词守卫：嘴上"办好了"、可系统这轮并没有写操作 → 退回模板
    if has_promise(text) and not executed:
        return fallback
    return text
