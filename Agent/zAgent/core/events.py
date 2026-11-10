"""Agent 与界面之间的事件契约。

core 层只负责产生事件，任何界面（TUI / Web / Desktop / CLI）通过实现
EventListener 消费事件，因此 core 不需要了解界面技术栈。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Union


@dataclass(frozen=True)
class TurnStarted:
    """一轮对话开始（用户消息已进入历史）。"""

    user_text: str


@dataclass(frozen=True)
class TextDelta:
    """模型流式输出的增量文本。"""

    text: str


@dataclass(frozen=True)
class ToolCallRequested:
    """模型请求调用某个工具。"""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolCallCompleted:
    """工具执行完毕，result 为反馈给模型的文本。"""

    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    result: str
    ok: bool


@dataclass(frozen=True)
class TurnCompleted:
    """一轮对话正常结束。"""

    assistant_text: str


@dataclass(frozen=True)
class TurnAborted:
    """一轮对话被取消或达到工具调用上限。"""

    reason: str


@dataclass(frozen=True)
class ErrorRaised:
    """可展示给用户的错误，不中断进程。"""

    message: str


AgentEvent = Union[
    TurnStarted,
    TextDelta,
    ToolCallRequested,
    ToolCallCompleted,
    TurnCompleted,
    TurnAborted,
    ErrorRaised,
]


class EventListener(Protocol):
    def on_event(self, event: AgentEvent) -> None:
        """消费一个 Agent 事件。"""


class CallbackListener:
    """把回调函数适配为 EventListener。"""

    def __init__(self, callback: Callable[[AgentEvent], None]) -> None:
        self._callback = callback

    def on_event(self, event: AgentEvent) -> None:
        self._callback(event)


class NullListener:
    """默认监听器，丢弃所有事件。"""

    def on_event(self, event: AgentEvent) -> None:
        return None
