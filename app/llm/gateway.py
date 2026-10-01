"""自研模型反代网关（人话：全项目**唯一**一个跟大模型说话的出口）。

为什么要有这一层？
以前是"谁想问模型，谁就自己去 new 一个 DeepSeekClient"——engine 一个、
语义层一个、润色层一个、后台测连通一个。这样有三个麻烦：
  ① 换模型要改代码（管理台改配置只改到了一半的路）；
  ② 各家返回的"工具调用"长得不一样，谁接谁自己兜，兜漏了就静默失灵；
  ③ 花了多少 token、失败了多少次，**没人知道**。
网关把这三件事收在一处：读配置 → 转发上游 → 标准化工具调用 → 记账。

设计边界（别把它做成大而全的东西）：
  - 网关**只是出口**，不碰业务，不做路由表，不改判定层/执行层的分工；
  - **记账失败绝不能影响对话**（try/finally 里记，store 那边再兜一层）；
  - **标准化失败只告警，不改行为**（原样往上抛，上层该怎么走还怎么走）。
"""
import json
import time
import uuid

from app.store import log_llm_usage


# ============ 一、跨厂商工具调用标准化 ============
# 目标：不管上游是 OpenAI/DeepSeek/国产直通，还是 Claude、Gemini，
# 出去的都长这样（OpenAI 风格）：
#   {"id": "call_xxx", "type": "function",
#    "function": {"name": "查空档", "arguments": "{\"date\":\"2026-10-03\"}"}}
# ⚠️ arguments 必须是 **JSON 字符串**，不是对象——上层（engine）按字符串解析，
#    给个 dict 会让它拿到 None，然后整轮工具调用无声无息地失效。


def _to_json_str(args) -> str:
    """把参数统一成 JSON 字符串（dict/list 序列化，None 给 {}，其余转字符串）。"""
    if args is None:
        return "{}"
    if isinstance(args, (dict, list)):
        try:
            return json.dumps(args, ensure_ascii=False)
        except Exception:
            return "{}"
    return str(args)


def _make_call(cid, name, args) -> dict:
    """造一个 OpenAI 风格的工具调用块。id 缺失时现编一个（上层要用 id 回传结果）。"""
    return {
        "id": cid or f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": name or "", "arguments": _to_json_str(args)},
    }


def normalize_tool_call(raw) -> dict | None:
    """把**一个**任意厂商的工具调用块，统一成 OpenAI 风格；认不出来返回 None。

    四种常见长相：
      ① Gemini：{"functionCall": {"name": ..., "args": {...}}}
      ② Claude：{"type": "tool_use", "id": ..., "name": ..., "input": {...}}
      ③ OpenAI/DeepSeek/国产直通：{"id":..., "type":"function",
                                   "function": {"name":..., "arguments": ...}}
      ④ 部分国产模型平铺：{"name": ..., "arguments": ...}
    """
    if not isinstance(raw, dict):
        return None
    # ① Gemini
    fc = raw.get("functionCall")
    if isinstance(fc, dict):
        return _make_call(raw.get("id") or fc.get("id"), fc.get("name"), fc.get("args"))
    # ② Claude
    if raw.get("type") == "tool_use":
        return _make_call(raw.get("id"), raw.get("name"), raw.get("input"))
    # ③ OpenAI 风格（也覆盖"块里还套着 functionCall"的情况）
    fn = raw.get("function")
    if isinstance(fn, dict):
        return _make_call(raw.get("id"), fn.get("name"), fn.get("arguments"))
    # ④ 平铺
    if raw.get("name"):
        return _make_call(raw.get("id"), raw.get("name"), raw.get("arguments"))
    return None


def _flatten_content(content) -> tuple[str, list]:
    """处理"正文是块数组"的情况（Claude/Gemini 常见）。

    Claude: {"content": [{"type":"text","text":"..."}, {"type":"tool_use",...}]}
    Gemini: {"parts": [{"text": "..."}, {"functionCall": {...}}]}
    返回 (拼好的文字, 顺手捡到的工具调用块列表)。
    """
    if not isinstance(content, list):
        return (content if isinstance(content, str) else ("" if content is None else str(content))), []
    texts, calls = [], []
    for blk in content:
        if isinstance(blk, dict):
            if isinstance(blk.get("functionCall"), dict) or blk.get("type") == "tool_use":
                calls.append(blk)
            elif blk.get("text") is not None:
                texts.append(str(blk["text"]))
            elif blk.get("content") is not None:
                texts.append(str(blk["content"]))
        elif isinstance(blk, str):
            texts.append(blk)
    return "".join(texts), calls


def normalize_message(message) -> dict:
    """把模型回的一条消息标准化（人话：让上层只认一种长相）。

    ⚠️ 这里是**只改结构、不改语义**的翻译层：认不出来就原样返回并告警，
    绝不擅自丢字段、绝不把"没工具调用"变成"有"。
    """
    if not isinstance(message, dict):
        return message
    try:
        out = dict(message)
        text, block_calls = _flatten_content(out.get("content"))
        if isinstance(out.get("content"), list):
            out["content"] = text

        raw_calls = out.get("tool_calls")
        raw_list = raw_calls if isinstance(raw_calls, list) else []
        # Gemini 有的把调用放在 parts 里
        parts = out.get("parts")
        if isinstance(parts, list):
            raw_list = raw_list + [b for b in parts if isinstance(b, dict)]

        calls = []
        for item in list(raw_list) + list(block_calls):
            one = normalize_tool_call(item)
            if one:
                calls.append(one)
        if calls:
            out["tool_calls"] = calls
        # 一个都没认出来但原本有 -> 原样返回（保住老行为），只在下面告警
        elif raw_list or block_calls:
            print("[网关告警] 工具调用块没能标准化，已原样上抛，请检查上游返回格式")
        return out
    except Exception as e:  # 标准化失败只告警，绝不影响对话
        print(f"[网关告警] 消息标准化失败，原样返回：{e}")
        return message


# ============ 二、用量换算 ============

def _tokens(usage) -> tuple[int, int, int]:
    """从各家 usage 里抠出 (prompt, completion, total)。

    OpenAI/DeepSeek：prompt_tokens / completion_tokens / total_tokens
    Claude：input_tokens / output_tokens
    Gemini：promptTokenCount / candidatesTokenCount
    """
    if not isinstance(usage, dict):
        return 0, 0, 0

    def num(*keys):
        for k in keys:
            v = usage.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
        return 0

    p = num("prompt_tokens", "input_tokens", "promptTokenCount")
    c = num("completion_tokens", "output_tokens", "candidatesTokenCount")
    t = num("total_tokens", "totalTokenCount") or (p + c)
    return p, c, t


# ============ 三、网关本体 ============

class ModelGateway:
    """模型出口（人话：谁想问模型，都得从这里过）。

    :param user: 这条调用记在谁头上。学生对话传**会话 id**；
                 内部调用（语义判定、润色、后台测连通）传**功能名**。
    :param override: 学生端自定义的 API 配置，优先于服务器配置
    :param client: 底层客户端，默认 DeepSeekClient；测试可以塞假货进来
    """

    def __init__(self, user: str = "", override: dict | None = None, client=None,
                 client_factory=None) -> None:
        self.user = user or "system"
        self.override = override
        self._client = client
        self._client_factory = client_factory

    # 为什么**按需 import**而不是写在模块顶部：模块顶部 `from X import Y` 是导入时绑定，
    # 测试/运行时换客户端的补丁会看不见（本项目踩过：语义层因此"安静地"退回关键词表）。
    def _get_client(self):
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        from app.llm.client import DeepSeekClient

        self._client = DeepSeekClient()
        return self._client

    def _current_model(self, override: dict | None) -> str:
        """本次要用的模型名（后台改 model 字段即换模型，零代码）。"""
        try:
            from app.config import get_llm_config

            return str(get_llm_config(override=override).get("model") or "")
        except Exception:
            return ""

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        override: dict | None = None,
    ) -> dict:
        """发一次对话，返回标准化后的消息；无论成功失败都记一笔账。"""
        ov = override if override is not None else self.override
        client = self._get_client()
        model = self._current_model(ov)
        started = time.perf_counter()
        status = "error"
        err = ""
        p = c = t = 0
        try:
            msg = await client.chat(messages, tools=tools, tool_choice=tool_choice, override=ov)
            # 立刻取用量：这两行之间不能有 await，否则并发下会读到别人的数字。
            usage = getattr(client, "last_usage", None)
            model = getattr(client, "last_model", "") or model or "unknown"
            p, c, t = _tokens(usage)
            out = normalize_message(msg)
            status = "ok"
            return out
        except Exception as e:
            err = str(e)[:300]
            raise
        finally:
            # 记账是旁路：这里再出任何问题都不许往上冒（store 里还有一层兜底）
            try:
                log_llm_usage({
                    # ts 不写：store.log_llm_usage 会用当前时间补上（写了空串反而占位）
                    "user": self.user,
                    "model": model or "unknown",
                    "prompt_tokens": p,
                    "completion_tokens": c,
                    "total_tokens": t,
                    "status": status,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": err,
                })
            except Exception:
                pass


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    user: str = "",
    override: dict | None = None,
    client=None,
) -> dict:
    """一次性调用的便捷入口（人话：不想自己 new 网关时用这个）。

    内部调用（语义判定、润色、后台测连通）走这里，user 传功能名。
    """
    gw = ModelGateway(user=user, override=override, client=client)
    return await gw.chat(messages, tools=tools, tool_choice=tool_choice, override=override)
