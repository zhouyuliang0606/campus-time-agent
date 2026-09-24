"""工具箱底座（人话：定义一个"工具"长什么样，以及怎么把它翻译成 DeepSeek 能理解的格式）。

在 Agent 里，"工具"就是 Agent 可以调用的函数，比如"查今天课表""算空闲时间"。
模型本身不会算这些，它只能"请求调用"，真正执行的是我们写的 Python 函数。
"""
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    """一个工具 = 名字 + 说明书(给模型看) + 真正的执行函数。

    :param name: 工具英文名，模型靠它来指定要调哪个
    :param description: 人话说明这个工具能干啥，模型靠它决定要不要调
    :param parameters: 入参的 JSON Schema 描述（类型、是否必填），模型照这个填参数
    :param func: 真正干活的 Python 函数（普通函数或 async 函数都行）
    """

    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]

    def to_schema(self) -> dict:
        """转成 DeepSeek 工具声明格式（人话：把工具"翻译"成模型能读懂的菜单项）。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, **kwargs) -> Any:
        """**带护栏地**执行一次工具。

        为什么要在工具外面套一层 try？
            实测模型会把 day 写成"周一"、把时间写成"第一节"，这类参数一错，
            底层 int()/split() 直接抛异常，整轮对话就变成一句"出了点问题"——
            学生什么都学不到，还以为系统坏了。
            围栏住之后，错误变成一句**能看懂的提示**回灌给模型，
            它下一轮自己改参数重试，学生这边毫发无损。
        """
        try:
            return self.func(**kwargs)
        except TypeError as e:
            # 最常见的：模型凑错了参数名（比如把 new_start 写成 newStart）
            return f"调用 {self.name} 时参数不对：{e}。请照工具说明重新调用。"
        except Exception as e:  # noqa: BLE001 —— 工具内部的意外一律兜住，不外泄堆栈
            return f"调用 {self.name} 时出错了（{type(e).__name__}：{e}）。" \
                   f"请换个参数重试，或换个说法问我。"

    async def run_async(self, **kwargs) -> Any:
        """和 run 一样带护栏，区别是等一个 async 工具。"""
        try:
            return await self.func(**kwargs)
        except TypeError as e:
            return f"调用 {self.name} 时参数不对：{e}。请照工具说明重新调用。"
        except Exception as e:  # noqa: BLE001
            return f"调用 {self.name} 时出错了（{type(e).__name__}：{e}）。" \
                   f"请换个参数重试，或换个说法问我。"
