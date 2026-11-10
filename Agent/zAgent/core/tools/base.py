"""工具抽象与注册表：业务工具与 LLM 协议细节解耦。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str


@dataclass(frozen=True)
class Tool:
    """一个可供模型调用的工具。

    handler 接收与 parameters 对应的关键字参数，返回给模型的文本结果。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """按名称注册并执行工具，执行异常统一转换为错误文本。"""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        self.register_all(tools)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具名重复: {tool.name}")
        self._tools[tool.name] = tool

    def register_all(self, tools: Iterable[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"错误: 未知工具 {name}")
        try:
            output = tool.handler(**arguments)
        except KeyError as exc:
            return ToolResult(False, f"错误: 缺少参数 {exc}")
        except TypeError as exc:
            return ToolResult(False, f"错误: 参数不匹配 {exc}")
        except Exception as exc:  # 工具内部的业务异常同样反馈给模型
            return ToolResult(False, f"错误: {exc}")
        return ToolResult(True, str(output))
