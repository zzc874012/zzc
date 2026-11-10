"""OpenAI 兼容接口的流式客户端（SSE 解析）。"""

from __future__ import annotations

import json
from typing import Any, Iterator, Sequence

import requests

from ..config import LLMSettings
from ..messages import ChatMessage
from .base import LLMError, StreamChunk, ToolCallDelta


class OpenAICompatibleClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        timeout: float = 120.0,
        session: requests.Session | None = None,
    ) -> None:
        self._settings = settings
        self._timeout = timeout
        self._session = session or requests.Session()

    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
    ) -> Iterator[StreamChunk]:
        url = f"{self._settings.base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": self._settings.model,
            "messages": list(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = list(tools)

        try:
            response = self._session.post(
                url,
                headers={"Authorization": f"Bearer {self._settings.api_key}"},
                json=payload,
                stream=True,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"请求模型服务失败: {exc}") from exc

        if not response.ok:
            detail = response.text[:500]
            response.close()
            raise LLMError(f"HTTP {response.status_code}: {detail}")

        try:
            for raw_line in response.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    payload_obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload_obj.get("choices") or []
                if not choices:
                    continue

                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                tool_calls = tuple(
                    ToolCallDelta(
                        index=item.get("index", 0),
                        id=item.get("id"),
                        name=(item.get("function") or {}).get("name"),
                        arguments=(item.get("function") or {}).get("arguments"),
                    )
                    for item in (delta.get("tool_calls") or [])
                )
                if content or tool_calls:
                    yield StreamChunk(content=content, tool_calls=tool_calls)
        finally:
            response.close()
