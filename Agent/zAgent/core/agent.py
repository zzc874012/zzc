"""Agent 编排层：对话循环、工具调用、上下文压缩，不含任何界面代码。"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from . import sessions as _sessions
from .compact import COMPACTION_PROMPT, compact_messages
from .config import AppConfig
from .events import (
    ErrorRaised,
    EventListener,
    NullListener,
    TextDelta,
    ToolCallCompleted,
    ToolCallRequested,
    TurnAborted,
    TurnCompleted,
    TurnStarted,
)
from .llm.base import LLMError, LLMClient, StreamChunk, ToolCallDelta
from .llm.openai_compat import OpenAICompatibleClient
from .mcp_client import MCPClientError, MCPClientManager
from .messages import (
    ChatMessage,
    assistant_message,
    system_message,
    tool_message,
    user_message,
)
from .skills import SkillCatalog
from .tools.base import ToolRegistry
from .tools.cli_tool import RunCliToolset
from .tools.file_ops import FileOpsToolset, Workspace
from .tools.mcp_toolset import MCPToolset
from .tools.recall_tool import RecallToolset
from .tools.skill_tool import SkillToolset
from .tools.web_search import WebSearchToolset

_BASE_SYSTEM_PROMPT = "You are zAgent, an assistant that completes tasks with the provided tools."


class _ToolCallAccumulator:
    """把 SSE 中按 index 分片的 tool_calls 拼装成完整调用。"""

    def __init__(self) -> None:
        self._slots: dict[int, dict[str, str]] = {}

    def add(self, delta: ToolCallDelta) -> None:
        slot = self._slots.setdefault(delta.index, {"id": "", "name": "", "arguments": ""})
        if delta.id:
            slot["id"] += delta.id
        if delta.name:
            slot["name"] += delta.name
        if delta.arguments:
            slot["arguments"] += delta.arguments

    def entries(self) -> list[tuple[str, str, str]]:
        return [
            (slot["id"], slot["name"], slot["arguments"])
            for _, slot in sorted(self._slots.items())
        ]

    def as_assistant_tool_calls(self) -> list[dict[str, Any]]:
        return [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in self.entries()
        ]


class Agent:
    """一个会话内的 Agent，负责多轮对话与工具调用。"""

    def __init__(
        self,
        config: AppConfig,
        *,
        client: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        skills: SkillCatalog | None = None,
        mcp: MCPClientManager | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.config = config
        self.client = client or OpenAICompatibleClient(
            config.llm, timeout=config.agent.request_timeout
        )
        # 项目根目录：默认是程序启动时的工作目录；测试 / 特殊场景可显式传入
        self.project_root = Path(project_root).resolve() if project_root else Path.cwd().resolve()

        # Skills 发现（同步很快，且只扫元数据）
        self.skills = skills if skills is not None else SkillCatalog.discover()

        # ---- MCP：后台线程加载，让 TUI 先出 ----
        self.mcp = MCPClientManager(self.project_root) if mcp is None else mcp
        self._mcp_loaded = threading.Event()     # 置位 = 加载已结束（无论成功失败）
        self._mcp_error: str | None = None
        self._mcp_thread: threading.Thread | None = None
        if mcp is None:
            self._mcp_thread = threading.Thread(target=self._load_mcp_async, daemon=True)
            self._mcp_thread.start()

        if registry is None:
            tools = FileOpsToolset(Workspace(config.agent.workspace)).tools()
            tools += RunCliToolset().tools()
            tools += WebSearchToolset(config.search.baidu_api_key).tools()
            tools += SkillToolset(self.skills).tools()
            tools += RecallToolset(self.project_root).tools()
            # MCPToolset 不在这里注册——后台线程 load 好之后再追加，避免第一次是空列表
            registry = ToolRegistry(tools)
        self.registry = registry

        self._system_prompt = _build_system_prompt(self.skills)
        self._messages: list[ChatMessage] = []
        self._cancel = threading.Event()
        self._last_usage: tuple[int, int, float, bool] | None = None

        # ---- 会话持久化 ----
        self._session_id: str | None = None
        self._agents_md_injected = False
        # 首次注入 AGENTS.md（一次注入，全程复用；除非 reset()）
        self._inject_agents_md_once()

    # ------------------------------------------------------------------
    # MCP 异步加载钩子
    # ------------------------------------------------------------------
    def _load_mcp_async(self) -> None:
        """后台线程：加载 .mcp.json → connect → list_tools。
        全部失败或中途异常都只记录，不抛错到主线程。"""
        try:
            self.mcp = MCPClientManager.load(self.project_root)
        except MCPClientError as exc:
            self._mcp_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            self._mcp_error = f"{exc.__class__.__name__}: {exc}"
        finally:
            self._mcp_loaded.set()

    def mcp_status(self) -> dict[str, Any]:
        """UI 层用来轮询的轻量状态；TUI 拿到后刷新 Sidebar MCP 区域。"""
        if not self._mcp_loaded.is_set():
            return {"state": "loading"}
        if self._mcp_error:
            return {"state": "error", "error": self._mcp_error, "servers": []}
        servers = [
            {
                "name": name,
                "type": info.config.get("type"),
                "connected": info.last_error is None,
                "tool_count": len(info.tools),
                "tools": [t.llm_name for t in info.tools],
                "error": info.last_error,
            }
            for name, info in self.mcp.servers.items()
        ]
        return {"state": "loaded", "servers": servers}

    def refresh_tools_from_mcp(self) -> list[str]:
        """后台加载完成后，把 MCPToolset 动态工具追加进 registry；跳过与现有工具重名的。
        返回本次实际追加的工具名列表，UI 可用来在 Sidebar 做"新工具已就绪"提示。"""
        added: list[str] = []
        if not self._mcp_loaded.is_set() or self._mcp_error:
            return added
        existing = {t.name for t in self.registry._tools.values()}  # noqa: SLF001
        for tool in MCPToolset(self.mcp).tools():
            if tool.name in existing:
                continue
            try:
                self.registry.register(tool)
                added.append(tool.name)
            except ValueError:
                pass
        return added

    def wait_mcp(self, timeout: float = 5.0) -> None:
        """阻塞直到 MCP 加载完成；UI 层一般不调用（靠 mcp_status 轮询）。"""
        self._mcp_loaded.wait(timeout)

    # ------------------------------------------------------------------
    # AGENTS.md 一次性注入
    # ------------------------------------------------------------------
    def _inject_agents_md_once(self) -> None:
        if self._agents_md_injected:
            return
        cfg = self.config.agents_md
        if not cfg.path:
            return
        path = self.project_root / cfg.path
        if not path.is_file():
            return
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return
        if not content.strip():
            return
        # 作为一条 system message 塞进 history 头部（在用户第一条之前）
        self._messages.insert(0, system_message(
            f"[项目指南 AGENTS.md]\n{content.strip()}"
        ))
        self._agents_md_injected = True

    # ------------------------------------------------------------------
    # 对外属性：上下文使用情况
    # ------------------------------------------------------------------
    @property
    def context_usage(self) -> tuple[int, int, float, bool]:
        from . import context as _ctx
        window = (
            self.config.agent.context_window_tokens
            if self.config.agent.context_window_tokens is not None
            else _ctx.DEFAULT_CONTEXT_WINDOW
        )
        is_default = self.config.agent.context_window_tokens is None
        current = self._estimate_current_tokens()
        percent = current / window if window else 0.0
        return current, window, percent, is_default

    def _estimate_current_tokens(self) -> int:
        from . import context as _ctx
        sys_tokens = _ctx.estimate_system_prompt_tokens(
            self._system_prompt, self.registry.schemas()
        )
        hist_tokens = _ctx.estimate_tokens(self._messages)
        return sys_tokens + hist_tokens

    @property
    def history(self) -> list[ChatMessage]:
        return list(self._messages)

    @property
    def agents_md_injected(self) -> bool:
        """项目内 AGENTS.md 是否已注入到本次会话 history。"""
        return self._agents_md_injected

    def reset(self) -> None:
        self._messages.clear()
        self._last_usage = None
        self._session_id = None
        self._agents_md_injected = False
        self._inject_agents_md_once()  # reset 后仍要重新注入

    def cancel(self) -> None:
        """请求中止当前轮对话，可在其他线程调用。"""
        self._cancel.set()

    # ------------------------------------------------------------------
    # 会话持久化：run_turn 结束后自动更新，/new 换新 session_id
    # ------------------------------------------------------------------
    def _auto_save_now(self) -> None:
        store = self.config.session.store_dir
        if not store or not self._messages:
            return
        try:
            sid, _ = _sessions.save_session(
                project_root=self.project_root,
                store_dir=store,
                history=self._messages,
                system_prompt=self._system_prompt,
                session_id=self._session_id,
            )
            self._session_id = sid
        except Exception:  # noqa: BLE001 — 自动保存失败不影响主流程
            pass

    # ------------------------------------------------------------------
    # 压缩
    # ------------------------------------------------------------------
    def compact(self, listener: EventListener | None = None) -> str:
        """手动触发上下文压缩。返回归档编号。失败时抛 LLMError，history 不变。"""
        listener = listener or NullListener()
        snapshot = copy.deepcopy(self._messages)
        # 用 config 里的 summary_target_tokens 替换 PROMPT 里的占位提示（简单替换就行）
        summary_prompt = COMPACTION_PROMPT.replace(
            "约 5,000 Tokens 的 Markdown 摘要",
            f"约 {self.config.compact.summary_target_tokens} Tokens 的 Markdown 摘要",
        )
        try:
            self._messages, archive_id = compact_messages(
                client=self.client,
                project_root=self.project_root,
                system_prompt=summary_prompt,
                history=self._messages,
            )
        except Exception as exc:  # noqa: BLE001 — 回滚 history
            self._messages = snapshot
            listener.on_event(ErrorRaised(f"压缩失败：{exc}"))
            raise
        self._last_usage = self.context_usage
        # 压缩后 session 要换 id（避免老 session 残留旧 history 元信息）
        self._session_id = None
        self._auto_save_now()
        return archive_id

    # ------------------------------------------------------------------
    # 主对话循环
    # ------------------------------------------------------------------
    def run_turn(self, user_text: str, listener: EventListener | None = None) -> str:
        """执行一轮完整对话，返回助手最终文本（异常或中止时可能为空）。"""
        listener = listener or NullListener()
        self._cancel.clear()

        # 阈值检查：超过 80% 窗口且用户没显式用 /compact → 拒绝
        from . import context as _ctx
        current, window, percent, is_default = self.context_usage
        if percent >= self.config.compact.threshold:
            text_stripped = user_text.strip()
            if not text_stripped.startswith("/compact"):
                default_note = "（默认 128k）" if is_default else ""
                listener.on_event(ErrorRaised(
                    f"上下文已使用 {_ctx.format_k(current)} / "
                    f"{_ctx.format_k(window)}{default_note} ({percent * 100:.1f}%)，"
                    f"请先执行 /compact 压缩历史。"
                ))
                return ""

        self._messages.append(user_message(user_text))
        listener.on_event(TurnStarted(user_text))

        text = ""
        for _ in range(self.config.agent.max_tool_rounds):
            try:
                text, accumulator, cancelled = self._stream_round(listener)
            except LLMError as exc:
                listener.on_event(ErrorRaised(str(exc)))
                break
            except Exception as exc:  # 网络 / 服务异常统一转成事件
                message = str(exc) or exc.__class__.__name__
                listener.on_event(ErrorRaised(message))
                break

            if cancelled:
                if text:
                    self._messages.append(assistant_message(text))
                listener.on_event(TurnAborted("已取消"))
                break

            tool_calls = accumulator.as_assistant_tool_calls()
            if not tool_calls:
                self._messages.append(assistant_message(text))
                listener.on_event(TurnCompleted(text))
                break

            self._messages.append(assistant_message(text, tool_calls))
            self._execute_tool_calls(accumulator, listener)
        else:
            listener.on_event(
                TurnAborted(f"工具调用轮数已达上限（{self.config.agent.max_tool_rounds} 轮）")
            )

        self._last_usage = self.context_usage
        # 完成一轮后自动保存当前会话快照
        self._auto_save_now()
        return text

    # ------------------------------------------------------------------
    # 内部：流式请求 + 工具执行
    # ------------------------------------------------------------------
    def _stream_round(
        self, listener: EventListener
    ) -> tuple[str, _ToolCallAccumulator, bool]:
        text = ""
        accumulator = _ToolCallAccumulator()
        messages = [system_message(self._system_prompt), *self._messages]
        stream = self.client.stream_chat(messages, self.registry.schemas())
        try:
            for chunk in stream:
                if self._cancel.is_set():
                    return text, accumulator, True
                self._consume_chunk(chunk, listener, accumulator)
                if chunk.content:
                    text += chunk.content
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        return text, accumulator, self._cancel.is_set()

    @staticmethod
    def _consume_chunk(
        chunk: StreamChunk,
        listener: EventListener,
        accumulator: _ToolCallAccumulator,
    ) -> None:
        if chunk.content:
            listener.on_event(TextDelta(chunk.content))
        for delta in chunk.tool_calls:
            accumulator.add(delta)

    def _execute_tool_calls(
        self, accumulator: _ToolCallAccumulator, listener: EventListener
    ) -> None:
        for call_id, name, raw_arguments in accumulator.entries():
            arguments = _parse_arguments(raw_arguments)
            listener.on_event(ToolCallRequested(call_id, name, arguments))
            result = self.registry.execute(name, arguments)
            listener.on_event(
                ToolCallCompleted(call_id, name, arguments, result.output, result.ok)
            )
            self._messages.append(tool_message(call_id, name, result.output))


def _build_system_prompt(skills: SkillCatalog) -> str:
    section = skills.system_prompt_section()
    return f"{_BASE_SYSTEM_PROMPT}\n\n{section}" if section else _BASE_SYSTEM_PROMPT


def _parse_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_agent(config: AppConfig) -> Agent:
    """组装默认 Agent（文件操作工具 + OpenAI 兼容客户端）。"""
    return Agent(config)
