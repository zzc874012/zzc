"""Web 界面后端：Starlette + SSE，零新增依赖。

只做三件事：
1. 提供静态页面（``static/index.html``）
2. ``/api/chat`` 接收用户消息，以 SSE 流式回传 Agent 事件
3. ``/api/cancel`` 与 ``/api/reset`` 映射 core 的 ``Agent.cancel()`` / ``Agent.reset()``

所有对话编排、工具执行、路径校验都在 core 里，本文件不复制任何业务规则。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ...core import AppConfig, build_agent
from .listener import QueueListener

STATIC_DIR = Path(__file__).resolve().parent / "static"
# 心跳间隔：避免代理/浏览器因长时间无数据而断开 SSE 连接
HEARTBEAT_INTERVAL = 15.0
# SSE 注释帧（以冒号开头），浏览器会忽略，仅用于保活
HEARTBEAT_FRAME = b": keep-alive\n\n"


def _sse_frame(payload: dict[str, Any]) -> bytes:
    """把一个事件编码成 SSE 帧（payload 内含 kind 便于前端分发）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class WebApp:
    """持有 Agent 与并发状态，构造 Starlette 应用。

    Agent 在同一个会话里维护消息历史，并非为并发设计，
    因此用 ``_turn_lock`` 保证同一时刻只有一轮对话在跑。
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.agent = build_agent(config)
        self._turn_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 路由装配
    # ------------------------------------------------------------------
    def create_app(self) -> Starlette:
        return Starlette(
            debug=False,
            routes=[
                Route("/", self.index),
                Route("/api/info", self.info, methods=["GET"]),
                Route("/api/chat", self.chat, methods=["POST"]),
                Route("/api/cancel", self.cancel, methods=["POST"]),
                Route("/api/reset", self.reset, methods=["POST"]),
            ],
        )

    async def index(self, request: Request) -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    async def info(self, request: Request) -> JSONResponse:
        """给前端展示模型、工作目录、可用工具等运行信息。"""
        return JSONResponse(
            {
                "model": self.config.llm.model,
                "base_url": self.config.llm.base_url,
                "workspace": str(self.config.agent.workspace),
                "max_tool_rounds": self.config.agent.max_tool_rounds,
                "tools": [tool["function"]["name"] for tool in self.agent.registry.schemas()],
            }
        )

    async def cancel(self, request: Request) -> JSONResponse:
        """中止当前轮对话，映射 ``Agent.cancel()``（线程安全）。"""
        self.agent.cancel()
        return JSONResponse({"ok": True})

    async def reset(self, request: Request) -> JSONResponse:
        """清空会话历史。若有轮次正在运行则拒绝，避免破坏消息一致性。"""
        if self._turn_lock.locked():
            return JSONResponse(
                {"ok": False, "message": "当前回复尚未结束，请先取消。"},
                status_code=409,
            )
        self.agent.reset()
        return JSONResponse({"ok": True})

    async def chat(self, request: Request) -> Response:
        """接收用户消息并以 SSE 流式返回事件。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "请求体必须是 JSON"}, status_code=400)

        text = str(body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "消息不能为空"}, status_code=400)

        return StreamingResponse(
            self._event_stream(text),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # 禁止 nginx 缓冲
            },
        )

    # ------------------------------------------------------------------
    # 事件流：后台线程跑 run_turn，本协程负责转发 + 保活
    # ------------------------------------------------------------------
    async def _event_stream(self, text: str) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        listener = QueueListener(loop)

        async def run_in_thread() -> None:
            """在工作线程执行阻塞的 run_turn，结束后务必放哨兵关闭事件流。"""
            try:
                await asyncio.to_thread(self.agent.run_turn, text, listener)
            finally:
                listener.finish()

        async with self._turn_lock:
            runner = asyncio.create_task(run_in_thread())
            try:
                async for payload in self._merge_events(listener.events()):
                    yield _sse_frame(payload)
            finally:
                # 客户端断开或异常时请求取消，避免工作线程继续空转
                if not runner.done():
                    self.agent.cancel()
                await asyncio.gather(runner, return_exceptions=True)

    @staticmethod
    async def _merge_events(source: AsyncIterator[dict[str, Any]]) -> AsyncIterator[Any]:
        """把事件流与心跳帧合并：模型长时间无输出时也定时发送注释帧保活。"""
        pending: asyncio.Task | None = None
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(source.__anext__())
                done, _ = await asyncio.wait(
                    {pending},
                    timeout=HEARTBEAT_INTERVAL,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:  # 超时：发一帧心跳，下轮继续等同一个 task
                    yield HEARTBEAT_FRAME
                    continue
                try:
                    item = pending.result()
                except StopAsyncIteration:
                    return
                finally:
                    pending = None
                yield item
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
