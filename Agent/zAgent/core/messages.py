"""OpenAI 兼容消息结构的构造函数，避免各处手写字典键名。"""

from __future__ import annotations

from typing import Any, Dict

ChatMessage = Dict[str, Any]


def system_message(text: str) -> ChatMessage:
    return {"role": "system", "content": text}


def user_message(text: str) -> ChatMessage:
    return {"role": "user", "content": text}


def assistant_message(content: str | None, tool_calls: list[dict[str, Any]] | None = None) -> ChatMessage:
    message: ChatMessage = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def tool_message(tool_call_id: str, name: str, content: str) -> ChatMessage:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": name,
        "content": content,
    }
