"""Web 适配器专用的事件监听器：把 core 事件从工作线程投递到 asyncio 队列。

core 的 ``EventListener`` 只要求实现 ``on_event``，而 ``Agent.run_turn`` 是同步阻塞
调用，因此本模块承担唯一的职责：线程切换 + 事件序列化。
业务逻辑一律不在此处实现。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from typing import Any

from ...core import AgentEvent, EventListener

# 所有 Agent 事件都是冻结数据类，直接用 dataclasses.asdict 即可 JSON 序列化；
# 这里额外记录类型名，便于前端按 kind 分发渲染。
_EVENT_KINDS = {
    "TurnStarted": "turn_started",
    "TextDelta": "text_delta",
    "ToolCallRequested": "tool_requested",
    "ToolCallCompleted": "tool_completed",
    "TurnCompleted": "turn_completed",
    "TurnAborted": "turn_aborted",
    "ErrorRaised": "error",
}


def encode_event(event: AgentEvent) -> dict[str, Any]:
    """把一个 Agent 事件转换成可 JSON 序列化的字典。"""
    if not is_dataclass(event):
        return {"kind": "unknown", "data": {"repr": repr(event)}}
    payload = asdict(event)
    payload["kind"] = _EVENT_KINDS.get(type(event).__name__, type(event).__name__)
    return payload


class QueueListener(EventListener):
    """把 Agent 事件从后台线程安全地投递到 asyncio 事件循环。

    与 README 中的示例一致：``run_turn`` 在工作线程执行，监听器负责跨线程投递。
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_event(self, event: AgentEvent) -> None:
        """Agent 在后台线程调用此方法；使用 call_soon_threadsafe 切回事件循环。"""
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, encode_event(event))
        except RuntimeError:
            # 事件循环已关闭（例如浏览器断开后应用退出），忽略残留事件
            pass

    def finish(self) -> None:
        """标记事件流结束（放入 None 哨兵）。

        同样需要从工作线程调用，因此走线程安全路径。
        """
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        except RuntimeError:
            pass

    async def events(self):
        """异步迭代事件，遇到 None 哨兵结束。"""
        while True:
            item = await self._queue.get()
            if item is None:  # 本轮结束
                return
            yield item
