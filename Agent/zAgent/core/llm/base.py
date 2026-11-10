"""LLM 客户端抽象：core 只依赖这些协议，便于替换模型供应商。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Protocol, Sequence

from ..messages import ChatMessage


class LLMError(RuntimeError):
    """网络或模型服务异常。"""


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str | None = None


@dataclass(frozen=True)
class StreamChunk:
    content: str = ""
    tool_calls: tuple[ToolCallDelta, ...] = ()


class LLMClient(Protocol):
    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
    ) -> Iterator[StreamChunk]:
        """流式返回增量内容与工具调用分片。"""
