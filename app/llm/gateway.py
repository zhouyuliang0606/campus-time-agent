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


class _NoStream(Exception):
    """内部暗号：这个客户端没有 `chat_stream`，`stream()` 走非流式退路时用它（不当错误看）。"""


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

def _redact(text: str, *secrets: str | None, limit: int = 300) -> str:
    """把异常文本里可能夹带的密钥抹掉，**再**截断。

    为什么要专门抹：失败时 `str(e)` 会被原样写进 `llm_usage.jsonl` 的 error 字段，
    而那张账是管理台能读、也可能被贴进截图的。各家 SDK 的报错爱把请求上下文
    （URL、甚至 header）拼进异常文本，一旦带上 `Authorization: Bearer sk-…`，
    学生自带的密钥就永久躺在磁盘上了。
    顺序不能反：**先抹再截**——先截 300 字符可能正好把密钥切一半，
    剩下那半截既匹配不上原始串、又足够被人拼回去。
    """
    out = str(text or "")
    for s in secrets:
        if s and len(s) >= 8:
            out = out.replace(s, "***密钥已隐去***")
    return out[:limit]


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
        max_tokens: int | None = None,
    ) -> dict:
        """发一次对话，返回标准化后的消息；无论成功失败都记一笔账。

        `max_tokens` 只是**往下透传**（网关不许自己给某一层加限制），语义见
        `DeepSeekClient.chat`：现在只有判定层会传，引擎与润色不传。
        """
        ov = override if override is not None else self.override
        client = self._get_client()
        model = self._current_model(ov)
        started = time.perf_counter()
        status = "error"
        err = ""
        p = c = t = 0
        try:
            msg = await client.chat(messages, tools=tools, tool_choice=tool_choice,
                                    override=ov, max_tokens=max_tokens)
            # 立刻取用量：这两行之间不能有 await，否则并发下会读到别人的数字。
            usage = getattr(client, "last_usage", None)
            model = getattr(client, "last_model", "") or model or "unknown"
            p, c, t = _tokens(usage)
            out = normalize_message(msg)
            status = "ok"
            return out
        except Exception as e:
            # 先隐去"这次真正用的那把 key"，再截断（见 _redact 的说明）。
            # 取的是生效配置而不是 override 本身：学生没带 key 时泄露的可能是服务器那把。
            student_key = (ov or {}).get("api_key")
            try:
                from app.config import get_llm_config

                live = get_llm_config(override=ov).get("api_key")
            except Exception:
                live = student_key
            err = _redact(str(e), live, student_key)
            raise
        finally:
            # 记账是旁路：这里再出任何问题都不许往上冒（store 里还有一层兜底）
            try:
                log_llm_usage({
                    # ts 不写：store.log_llm_usage 会用当前时间补上（写了空串反而占位）
                    "user": self.user,
                    # 这次花的是谁的 Key：student = 学生自带，server = 服务器/管理后台那把。
                    # ⚠️ 只记来源标签，**绝不记 Key 本身** —— 用量流水是要给管理员看的，
                    # 而学生那把钥匙的承诺就是"用完即弃、不落任何文件"（test_34 钉这条）。
                    # 有了这一列，管理员才能回答"这台机器今天替别人打了多少请求"。
                    "key_source": "student" if (ov or {}).get("api_key") else "server",
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


    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        override: dict | None = None,
        max_tokens: int | None = None,
    ):
        """流式出口：逐块 yield `{"k":"token","v":文字}`，抽干后 yield `{"k":"message","v":整条消息}`。

        与 `chat` 同口径的四件事（缺一条就是新 bug）：
          · **记账**：同 user / key_source / latency_ms，usage 靠上游末块（`include_usage`）；
          · **标准化**：整条消息照样过 `normalize_message`（各家工具调用长相不同，引擎只认一种）；
          · **错误**：带 `kind` 的 `LLMError` 往上抛，由请求方翻成一句人话，这里不静默；
          · **不反射地址**：错误消息过 `_redact`，密钥一律隐去。

        降级只有一种情况：一块都还没吐出来、且失败原因是"这个上游不会流式"
        （`upstream` / `bad_format`）→ 退回非流式 `chat`，整段当一块吐。
        已经吐出过内容就不退 —— 否则同一句话会在学生屏幕上出现两遍。
        """
        ov = override if override is not None else self.override
        client = self._get_client()
        model = self._current_model(ov)
        started = time.perf_counter()
        status = "error"
        err = ""
        p = c = t = 0
        emitted = False
        try:
            try:
                if not hasattr(client, "chat_stream"):
                    # 这个客户端不会流式（后台测连通塞的假货、以及全仓那批只有 chat 的桩都在这里退）。
                    # ⚠️ 这是**明写的退路**，不是把签名漂移咽掉：退了照样整段给出、一个字不缺，
                    #    而且 F/E 批专门钉着它（要钉的签名一致性在 test_48 那族断言上）。
                    raise _NoStream
                async for frag in client.chat_stream(messages, tools=tools,
                                                     tool_choice=tool_choice,
                                                     override=ov, max_tokens=max_tokens):
                    if frag:
                        emitted = True
                        yield {"k": "token", "v": frag}
                # 生成器抽干了才读这两样：中途 break 出去，last_message 只是半截
                usage = getattr(client, "last_usage", None)
                model = getattr(client, "last_model", "") or model or "unknown"
                p, c, t = _tokens(usage)
                msg = normalize_message(getattr(client, "last_message", None)
                                        or {"role": "assistant", "content": ""})
            except _NoStream:
                msg = normalize_message(await client.chat(
                    messages, tools=tools, tool_choice=tool_choice, override=ov,
                    max_tokens=max_tokens))
                usage = getattr(client, "last_usage", None)
                p, c, t = _tokens(usage)
                if msg.get("content"):
                    yield {"k": "token", "v": msg["content"]}
            except Exception as e:
                if emitted or getattr(e, "kind", "") not in ("upstream", "bad_format"):
                    raise
                msg = normalize_message(await client.chat(
                    messages, tools=tools, tool_choice=tool_choice, override=ov,
                    max_tokens=max_tokens))
                usage = getattr(client, "last_usage", None)
                p, c, t = _tokens(usage)
                if msg.get("content"):
                    yield {"k": "token", "v": msg["content"]}
            status = "ok"
            yield {"k": "message", "v": msg}
        except Exception as e:
            # 与 chat 那份同款的隐密钥逻辑（两处各写一遍是因为改谁都可能漏另一处；
            # 真要合并得先动 chat，本刀明令不动 chat）
            student_key = (ov or {}).get("api_key")
            try:
                from app.config import get_llm_config

                live = get_llm_config(override=ov).get("api_key")
            except Exception:
                live = student_key
            err = _redact(str(e), live, student_key)
            raise
        finally:
            try:
                log_llm_usage({
                    "user": self.user,
                    "key_source": "student" if (ov or {}).get("api_key") else "server",
                    "model": model or "unknown",
                    "prompt_tokens": p,
                    "completion_tokens": c,
                    "total_tokens": t,
                    "status": status,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "stream": 1,
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
    max_tokens: int | None = None,
) -> dict:
    """一次性调用的便捷入口（人话：不想自己 new 网关时用这个）。

    内部调用（语义判定、润色、后台测连通）走这里，user 传功能名。
    """
    gw = ModelGateway(user=user, override=override, client=client)
    return await gw.chat(messages, tools=tools, tool_choice=tool_choice,
                         override=override, max_tokens=max_tokens)


async def stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    user: str = "",
    override: dict | None = None,
    client=None,
    max_tokens: int | None = None,
):
    """流式便捷入口（与模块级 `chat` 对称）。**目前只有引擎这一路会用**。

    ⚠️ 语义判定、润色、后台测连通**不许挪到这里**：判定只要一个几十 token 的 JSON，
    流式对它零收益（那 4.2 秒在首字之前，不在首字之后），还会把 test_48 那套
    "看请求体/看等了多久"的断言全数作废。
    """
    gw = ModelGateway(user=user, override=override, client=client)
    async for ev in gw.stream(messages, tools=tools, tool_choice=tool_choice,
                              override=override, max_tokens=max_tokens):
        yield ev
