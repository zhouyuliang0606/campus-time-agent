"""ReAct Agent 引擎（人话：这是 Agent 的"大脑循环"——先想、再做、看结果、再想，直到能回答）。

ReAct = Reason(推理) + Act(行动)。
流程像人解题：
  1. 看用户问题 + 已有信息，先"想"下一步怎么办；
  2. 如果需要查东西/算东西，就"调用一个工具"；
  3. 拿到工具结果，再"想"下一步；
  4. 信息够了，就给出最终自然语言回答。

我们不套 LangChain 之类的框架，这个循环就几十行，评委一眼能看明白。
"""
import asyncio
import json
from typing import Any, Callable

from app.agent.tools import Tool
from app.llm.client import DeepSeekClient


def _is_async(fn: Callable[..., Any]) -> bool:
    """判断一个函数是不是 async（人话：能不能用 await 等它）。"""
    return asyncio.iscoroutinefunction(fn)


class AgentEngine:
    """轻量 ReAct 引擎（人话：把"模型 + 工具箱 + 系统设定"组合起来，跑上面的循环）。"""

    def __init__(
        self,
        llm: DeepSeekClient | None = None,
        tools: dict[str, Tool] | None = None,
        system_prompt: str = "",
        session_id: str = "",
    ) -> None:
        self.llm = llm or DeepSeekClient()
        self.tools: dict[str, Tool] = tools or {}
        self.system_prompt = system_prompt
        # 会话 id：本轮生成的确认卡从这里传给"待确认暂存"，
        # 学生下一句回"确认"时系统据此找到那张卡（跟模型判断无关，纯确定性）
        self.session_id = session_id
        # trace 记录每一步在干嘛，前端可以展示成"思考轨迹"，是演示 Agent 推理的关键
        self.trace: list[dict] = []
        # 规划模块调 propose_slots 时，把候选结构化选项暂存这里，最后带给前端
        self.options: list[dict] = []

    def _schemas(self) -> list[dict] | None:
        """把所有工具翻译成模型能用的格式；没有工具就返回 None（模型只用文字回答）。"""
        if not self.tools:
            return None
        return [t.to_schema() for t in self.tools.values()]

    async def run(self, user_input: str, context: dict | None = None,
                  history: list[dict] | None = None) -> dict:
        """跑一轮 ReAct，返回 {answer: 最终回答, trace: 思考轨迹}。

        :param user_input: 用户这次说的话
        :param context: 可选背景信息（比如当前用户、当前周次），会作为系统提示补充
        :param history: 之前聊过的若干轮 [{role: user/assistant, content: ...}]。
                       带上它 Agent 才知道"上一轮我们商量到哪了"——比如提完一个
                       时间安排建议后，学生说"可以"，它得知道"可以"指的是哪个方案。
        """
        # 1) 组装对话历史：系统设定 + 可选背景 + 过往对话 + 本次问题
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append(
                {
                    "role": "system",
                    "content": "已知背景信息：" + json.dumps(context, ensure_ascii=False),
                }
            )
        # 只接收 user / assistant 两种角色的纯文字消息。
        # 中间的工具调用过程不回溯 —— 少了些细节，但换来稳定（不会出现
        # tool_call_id 对不上的报错），而且最终答案里本就包含结论。
        if history:
            for h in history:
                role = h.get("role")
                if role in ("user", "assistant") and h.get("content"):
                    messages.append({"role": role, "content": h["content"]})
        messages.append({"role": "user", "content": user_input})

        # 最多思考 6 轮，防止模型死循环（人话：想太久就停下，给个兜底回答）
        max_steps = 6
        for step in range(1, max_steps + 1):
            self.trace.append({"step": step, "phase": "🤔 思考"})

            # 2) 让模型基于当前历史回一句（可能含 tool_calls）
            msg = await self.llm.chat(messages, tools=self._schemas())

            # 3) 如果模型想调用工具
            if msg.get("tool_calls"):
                # 把模型"我想调工具"这句原样存进历史（DeepSeek 要求工具结果要对应上）
                messages.append(msg)
                for tc in msg["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    # 模型给的参数可能是字符串形式的 JSON，安全解析
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except Exception:
                        args = {}

                    self.trace.append(
                        {"step": step, "phase": "🔧 调用工具", "action": fn_name, "args": args}
                    )

                    tool = self.tools.get(fn_name)
                    if not tool:
                        result = f"错误：没有名为 {fn_name} 的工具"
                    else:
                        # 真正执行工具函数（跑的是 Tool.run，里面带错误围栏：
                        # 模型传错参数也不会把整轮对话搞崩，只会拿到一句看得懂的提示）
                        result = (await tool.run_async(**args)) if _is_async(tool.func) \
                            else tool.run(**args)

                        # 规划模块用 propose_slots 把候选"交给前端渲染成卡片"：
                        # 抓它的结构化参数，作为响应里的 options 带回去
                        if fn_name == "propose_slots":
                            raw = args.get("options_json") or args.get("options")
                            if isinstance(raw, str):
                                try:
                                    raw = json.loads(raw)
                                except Exception:
                                    raw = None
                            if isinstance(raw, list):
                                self.options = raw

                        # 课表修改提案：同样抓参数给前端渲染「确认修改」卡。
                        # 学生点确认后由前端直接调 /api/timetable/apply 写入——
                        # 写入权不在模型手里，从根上杜绝"嘴上说删了、数据没变"。
                        elif fn_name == "propose_timetable_change":
                            raw = args.get("courses_json")
                            if isinstance(raw, str):
                                try:
                                    raw = json.loads(raw)
                                except Exception:
                                    raw = None
                            if isinstance(raw, list) and raw:
                                self.options = [{
                                    "kind": "timetable_change",
                                    "summary": str(args.get("change_summary") or "修改课表"),
                                    "courses": raw,
                                }]

                        # 通用"结果即提案"：工具返回带 __proposal__ 的 JSON（如
                        # propose_course_change，新课表由服务端算好），直接变确认卡。
                        # 同一轮模型可能连出多张提案（如"周二早上的课"有两节），都保留让学生挑。
                        if isinstance(result, str) and "__proposal__" in result:
                            try:
                                parsed = json.loads(result)
                                prop = parsed.get("__proposal__") if isinstance(parsed, dict) else None
                                if isinstance(prop, dict):
                                    # 只有"课表提案"之间才谈得上并存（多张修改卡叠加着落库）。
                                    # 候选时间段（kind 为空）和清空提案不参与叠加，各占各的位置，
                                    # 免得一张清空弹窗把学生还没选的时间候选给顶掉。
                                    same_kind = all(
                                        o.get("kind") == prop.get("kind")
                                        for o in self.options if o.get("kind"))
                                    if self.options and same_kind:
                                        self.options = self.options + [prop]
                                    else:
                                        self.options = [prop]
                            except Exception:
                                pass

                    # 4) 把工具结果作为"tool"角色消息回灌给模型，让它继续想
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": str(result),
                        }
                    )
                    self.trace.append(
                        {
                            "step": step,
                            "phase": "📦 工具结果",
                            "action": fn_name,
                            "result": str(result)[:600],
                        }
                    )
                # 拿到工具结果，进入下一轮"再想"
                continue

            # 5) 模型没调工具，说明它觉得可以直接回答了
            answer = msg.get("content") or "（模型没有返回内容，请稍后再试）"
            self.trace.append({"step": step, "phase": "💡 最终回答", "answer": answer})
            return self._finish(answer)

        # 6) 超过步数限制仍没结论
        return self._finish("我思考了几轮还是没完全理清楚，能换个说法、或给多一点信息吗？")

    def _finish(self, answer: str) -> dict:
        """收尾：把本轮生成的提案交给上层暂存。

        为什么要存？学生下一句很可能就是一句"确认"。这一步如果指望模型自己判断，
        它就会回"我没有权限删除"，链路当场断掉。存下来之后由系统执行，跟模型无关。
        """
        from app.agent.pending import save_pending
        save_pending(self.session_id, self.options)
        return {
            "answer": answer,
            "trace": self.trace,
            "options": self.options,
            "awaiting_choice": bool(self.options),
        }
