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
    ) -> None:
        self.llm = llm or DeepSeekClient()
        self.tools: dict[str, Tool] = tools or {}
        self.system_prompt = system_prompt
        # trace 记录每一步在干嘛，前端可以展示成"思考轨迹"，是演示 Agent 推理的关键
        self.trace: list[dict] = []

    def _schemas(self) -> list[dict] | None:
        """把所有工具翻译成模型能用的格式；没有工具就返回 None（模型只用文字回答）。"""
        if not self.tools:
            return None
        return [t.to_schema() for t in self.tools.values()]

    async def run(self, user_input: str, context: dict | None = None) -> dict:
        """跑一轮 ReAct，返回 {answer: 最终回答, trace: 思考轨迹}。

        :param user_input: 用户说的话
        :param context: 可选背景信息（比如当前用户、当前周次），会作为系统提示补充
        """
        # 1) 组装对话历史：系统设定 + 可选背景 + 用户问题
        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append(
                {
                    "role": "system",
                    "content": "已知背景信息：" + json.dumps(context, ensure_ascii=False),
                }
            )
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
                        # 真正执行工具函数；async 就 await，普通就直接调
                        if _is_async(tool.func):
                            result = await tool.func(**args)
                        else:
                            result = tool.func(**args)

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
            return {"answer": answer, "trace": self.trace}

        # 6) 超过步数限制仍没结论
        return {
            "answer": "我思考了几轮还是没完全理清楚，能换个说法、或给多一点信息吗？",
            "trace": self.trace,
        }
