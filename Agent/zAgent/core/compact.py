"""上下文压缩：把历史消息送给 LLM 生成摘要，用摘要替换旧历史。

压缩流程（经验约束）：
1. 深拷贝 messages 作为回滚备份
2. 归档到 .agents/context/<timestamp>.jsonl（归档成功才继续）
3. 构造压缩 Prompt，把历史转成可发给 LLM 的文本（tool 消息要转成自然语言描述，避免再给 LLM 发 tool role）
4. 调 LLM（不带 tool schemas，压缩期禁止工具，让模型只能输出纯文本摘要）
5. 成功：把摘要作为两条消息插入（一条 system 级摘要 + 一条 assistant 级归档说明），清空原 history
6. 失败：恢复备份，不丢任何历史

本模块不依赖 Agent，只接受 LLMClient + messages + system_prompt，便于单元测试。
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .context import create_archive
from .llm.base import LLMError, LLMClient, StreamChunk, ToolCallDelta
from .messages import (
    ChatMessage,
    assistant_message,
    system_message,
    user_message,
)


COMPACTION_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


def compact_messages(
    client: LLMClient,
    project_root: Path,
    system_prompt: str,
    history: list[ChatMessage],
) -> tuple[list[ChatMessage], str]:
    """执行压缩。返回 (压缩后的 history, 归档编号)。失败时抛出 LLMError，history 不变。

    流程里先归档再调 LLM：归档必须成功（否则直接 raise）；LLM 失败则回滚 history。
    """
    # 经验：深拷贝做回滚备份
    snapshot = copy.deepcopy(history)

    # 归档（原始 messages，完整保留所有 role / tool 调用）
    archive_id, _archive_path = create_archive(project_root, history)

    # 构造压缩请求：把 tool 消息转成自然语言，避免 LLM 收到非法 tool role
    compression_user = _history_as_text(history)
    compaction_messages = [
        system_message(COMPACTION_PROMPT),
        user_message(compression_user),
    ]

    try:
        summary_text = _collect_llm_text(client, compaction_messages)
    except LLMError:
        # LLM 调用失败 → 回滚（虽然我们没改 history，但显式兜底更安全）
        raise

    if not summary_text.strip():
        raise LLMError("压缩失败：LLM 返回了空摘要")

    # 构造替换后的 history：一条 system 摘要 + 一条 assistant 归档说明
    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    archive_note = (
        f"此前上下文已于 {timestamp} 完成压缩，原始上下文保存在编号为 {archive_id} 的归档文件中。"
        f"如需查找历史信息，请使用 recallContext 工具。"
    )

    new_history: list[ChatMessage] = [
        system_message(f"[上下文压缩摘要]\n{summary_text}"),
        assistant_message(archive_note),
    ]
    return new_history, archive_id


def _collect_llm_text(client: LLMClient, messages: list[ChatMessage]) -> str:
    """调 LLM（不带 tool schemas，压缩期禁止工具），收集全部文本输出。"""
    stream = client.stream_chat(messages, tools=[])
    try:
        chunks = list(stream)
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    text = "".join(chunk.content or "" for chunk in chunks)
    # 经验：有些 LLM 模型在 tools=[] 时仍可能返回 tool_calls（例如部分实现不严格），
    # 忽略 tool_calls，只取 content；如果整个 content 为空说明模型出错
    return text


def _history_as_text(history: list[ChatMessage]) -> str:
    """把原始 history 转成给压缩 LLM 的纯文本。tool 消息要转成自然语言描述。"""
    lines: list[str] = []
    for msg in history:
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                (p.get("text", "") if isinstance(p, dict) else str(p)) for p in content
            )

        if role == "system":
            continue  # 原 system prompt 不参与压缩（我们有自己的压缩 prompt）
        if role == "user":
            lines.append(f"[用户] {content}")
        elif role == "assistant":
            parts: list[str] = []
            if content:
                parts.append(content)
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                parts.append(
                    f"[请求调用工具 {fn.get('name', '?')}] 参数: {fn.get('arguments', '')}"
                )
            lines.append(f"[AI] {' | '.join(parts)}")
        elif role == "tool":
            lines.append(f"[工具 {msg.get('name', '?')}] 返回: {content[:1500]}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n\n".join(lines)
