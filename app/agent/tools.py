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
