"""命令行适配器：与 TUI 共用同一 core，可作为新增界面的最小实现参考。"""

from __future__ import annotations

import json
import sys

from ..core import (
    AgentEvent,
    AppConfig,
    ErrorRaised,
    EventListener,
    LLMError,
    TextDelta,
    ToolCallCompleted,
    ToolCallRequested,
    TurnAborted,
    TurnCompleted,
    TurnStarted,
    WARNING_THRESHOLD,
    build_agent,
    commands,
    format_k,
)

RESULT_PREVIEW_LIMIT = 300


class ConsoleListener(EventListener):
    """把 Agent 事件直接打印到终端。"""

    def on_event(self, event: AgentEvent) -> None:
        if isinstance(event, TurnStarted):
            print("AI: ", end="", flush=True)
        elif isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCallRequested):
            arguments = json.dumps(event.arguments, ensure_ascii=False)
            print(f"\n[调用工具] {event.name}({arguments})")
        elif isinstance(event, ToolCallCompleted):
            status = "成功" if event.ok else "失败"
            print(f"[工具{status}] {_preview(event.result)}")
            print("AI: ", end="", flush=True)
        elif isinstance(event, TurnCompleted):
            print()
        elif isinstance(event, TurnAborted):
            print(f"\n[中止] {event.reason}")
        elif isinstance(event, ErrorRaised):
            print(f"\n[错误] {event.message}")


def _preview(text: str, limit: int = RESULT_PREVIEW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]} …（共 {len(text)} 字符）"


def print_history(messages: list[dict]) -> None:
    print("\n========== 对话记录 ==========")
    for message in messages:
        role = message["role"]
        if role == "user":
            print(f"You: {message['content']}")
        elif role == "assistant":
            if message.get("content"):
                print(f"AI: {message['content']}")
            for call in message.get("tool_calls") or []:
                function = call["function"]
                print(f"AI 请求调用工具: {function['name']}({function['arguments']})")
        elif role == "tool":
            print(f"Tool({message.get('name')}): {message['content']}")
    print("==============================\n")


def _print_usage(agent) -> None:
    current, window, percent, is_default = agent.context_usage
    default_note = "（默认 128k）" if is_default else ""
    print(
        f"上下文: {format_k(current)} / {format_k(window)}{default_note} "
        f"({percent * 100:.1f}%)"
        + (" ⚠ 接近阈值，请执行 /compact" if percent >= WARNING_THRESHOLD else "")
    )


def run_cli(config: AppConfig) -> int:
    agent = build_agent(config)
    listener = ConsoleListener()
    print(f"zAgent CLI · 模型 {config.llm.model} · 工作目录 {config.agent.workspace}")
    print("命令: /help 全部命令 · /compact 压缩上下文 · /usage token 使用量 · /save /resume /quit")
    print()
    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        stripped = prompt.strip()
        if not stripped:
            continue
        if stripped.lower() in ("exit", "quit"):
            break

        # ---- 斜杠命令统一路由 ----
        if stripped.startswith("/"):
            output = commands.dispatch(agent, stripped, listener)
            if output:
                print(output)
            continue

        agent.run_turn(prompt, listener)
    print_history(agent.history)
    _print_usage(agent)
    return 0
