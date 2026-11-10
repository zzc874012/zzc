"""Textual TUI：把界面事件翻译成 core 调用，把 core 事件渲染到界面。

布局（Textual 8.x，没有 Sidebar/VSplit/CommandInput 组件，全部手工拼）：

    Horizontal
    ├── Vertical(id="sidebar")  Sidebar
    │   ├── Label               会话 / 系统状态
    │   ├── Collapsible × N     每个已连接的 MCP server 一张卡片
    │   ├── Static              AGENTS.md 徽章
    │   └── Static              当前会话信息
    └── Vertical                主区
        ├── Header
        ├── ChatLog(id="chat")
        ├── StatusBar(id="status")
        ├── Input(id="prompt")
        └── Footer
"""

from __future__ import annotations

import json
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

from ...core import (
    Agent,
    AgentEvent,
    AppConfig,
    ErrorRaised,
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
from .widgets import ChatLog, MessageBubble, StatusBar

RESULT_PREVIEW_LIMIT = 600


def _preview(text: str, limit: int = RESULT_PREVIEW_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]} …（共 {len(text)} 字符）"


class ThreadEventListener:
    """在后台线程中把 Agent 事件转交给 UI 线程处理。"""

    def __init__(self, app: "ZAgentApp") -> None:
        self._app = app

    def on_event(self, event: AgentEvent) -> None:
        try:
            self._app.call_from_thread(self._app.handle_agent_event, event)
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# 恢复会话 ModalScreen（Textual 8 有 ModalScreen 但没 Dialog，自造一个）
# ---------------------------------------------------------------------------
class ResumeSessionModal(ModalScreen[str | None]):
    """列出 .agents/sessions/ 里保存的会话，让用户上下键选、回车恢复。"""

    BINDINGS = [
        Binding("escape", "dismiss(cancel)", "取消"),
        Binding("enter", "confirm", "恢复选中"),
    ]

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("恢复会话 · ESC 取消", id="resume-title")
            if not self._sessions:
                yield Label("（暂无保存的历史会话）")
            else:
                list_items: list[ListItem] = []
                for s in self._sessions:
                    label = (
                        f"[{s['session_id']}] {s['title']}"
                        f"  ({s['message_count']} 条 · {s['last_active_at']})"
                    )
                    list_items.append(ListItem(label))
                yield ListView(*list_items, id="resume-list")
            yield Button("取消", variant="error", id="resume-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume-cancel":
            self.dismiss(None)

    def action_dismiss(self, _: str) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        list_view = self.query_one("#resume-list", ListView)
        if list_view is None:
            self.dismiss(None)
        idx = list_view.index or 0
        if 0 <= idx < len(self._sessions):
            self.dismiss(self._sessions[idx]["session_id"])
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# 主 App
# ---------------------------------------------------------------------------
class ZAgentApp(App[None]):
    CSS_PATH = "app.tcss"
    TITLE = "zAgent"
    SUB_TITLE = "LLM 文件操作智能体"
    # 强制浅色主题（覆盖 Textual 默认深色）
    DEFAULT_CSS = """
    Screen { background: #ffffff; color: #24292f; }
    Label { color: #24292f; }
    Static { color: #24292f; }
    Input { background: #ffffff; color: #24292f; }
    Header { background: #f6f8fa; color: #24292f; }
    Footer { background: #f6f8fa; color: #57606a; }
    ScrollBar { color: #d0d7de; }
    """
    BINDINGS = [
        Binding("ctrl+q", "quit", "退出"),
        Binding("ctrl+l", "clear_chat", "清空对话"),
        Binding("escape", "cancel_turn", "取消本轮"),
    ]

    # reactive 让 Sidebar 状态自动刷新
    mcp_state = reactive(dict, recompose=False)

    def __init__(self, agent: Agent, config: AppConfig) -> None:
        super().__init__()
        self.agent = agent
        self.config = config
        self._busy = False
        self._streaming_bubble: MessageBubble | None = None
        self._streaming_text = ""
        # 命令补全面板（Input.Changed 事件里动态生成，打 `/` 时显示）
        self._completion_label: Label | None = None
        self._completion_visible = False
        self._completion_matches: list[str] = []

    # ---- Compose 布局（Sidebar 左 / ChatLog 右）--------------------------
    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Sidebar", id="sb-title")
                yield Static("· model", id="sb-model")
                yield Static("· AGENTS.md: –", id="sb-agents")
                yield Static(id="sb-sessions-label")
                # MCP server 占位：on_mount 里根据 mcp_status 动态刷新
                yield Static(id="sb-mcp-placeholder")
                yield Static(id="sb-empty")
            with Vertical(id="main"):
                yield Header(show_clock=True)
                yield ChatLog(id="chat")
                yield StatusBar("就绪", id="status")
                yield Input(
                    placeholder="输入消息，Enter 发送；Esc 取消本轮，Ctrl+L 清空对话",
                    id="prompt",
                )
                # 命令补全面板（覆盖在 Input 上方，初始隐藏）
                yield Label("", id="completions", classes="completions")
                yield Footer()

    # ---- Mount / Unmount ------------------------------------------------
    def on_mount(self) -> None:
        self.sub_title = f"{self.config.llm.model} · {self.config.agent.workspace}"
        chat = self.query_one(ChatLog)
        chat.add_message(
            "system",
            f"模型: {self.config.llm.model}\n"
            f"工作目录: {self.config.agent.workspace}\n"
            f"可用工具: {len(self.agent.registry.schemas())} 个，"
            f"最多 {self.config.agent.max_tool_rounds} 轮工具调用\n"
            f"命令: /help 全部命令 · /compact 压缩上下文 · /new /save /resume /quit",
        )
        self.query_one("#prompt", Input).focus()
        self._refresh_usage_status("就绪")
        self._refresh_sidebar_static()

        # 启动 MCP 后台轮询（每秒一次，直到 loaded 或 error）
        self.set_interval(1.0, self._poll_mcp_status)

    def on_unmount(self) -> None:
        self.agent.cancel()

    # ---- Sidebar 刷新 ---------------------------------------------------
    def _refresh_sidebar_static(self) -> None:
        sb_model = self.query_one("#sb-model", Static)
        sb_model.update(f"· model: {self.config.llm.model}\n· registry: {len(self.agent.registry.schemas())} tools")

        sb_agents = self.query_one("#sb-agents", Static)
        if self.agent.agents_md_injected:
            sb_agents.update("· AGENTS.md ✅ 已注入")
        else:
            sb_agents.update("· AGENTS.md ❌ 未加载")

        sessions = _list_sessions_cli_style(self.agent)
        sessions_label = self.query_one("#sb-sessions-label", Static)
        if sessions:
            recent = "\n".join(
                f"· [{s['session_id'][-10:]}] {s['title'][:20]}"
                for s in sessions[:3]
            )
            sessions_label.update(f"最近会话（/resume <编号>）:\n{recent}")
        else:
            sessions_label.update("最近会话: （无）")

        # MCP 区域
        self._refresh_sidebar_mcp()

    def _refresh_sidebar_mcp(self) -> None:
        status = self.agent.mcp_status()
        placeholder = self.query_one("#sb-mcp-placeholder", Static)
        if status["state"] == "loading":
            placeholder.update("· MCP 加载中…")
        elif status["state"] == "error":
            placeholder.update(f"· MCP 加载失败: {status['error'][:80]}")
        else:
            servers = status["servers"]
            if not servers:
                placeholder.update("· MCP: （未配置 server）")
            else:
                lines = [f"· MCP servers ({len(servers)}):"]
                for srv in servers:
                    flag = "✅" if srv["connected"] else f"❌ {srv['error'][:40] if srv['error'] else ''}"
                    lines.append(f"  [{srv['name']}] {flag} ({srv['type']})")
                    for tool in srv["tools"][:3]:
                        lines.append(f"    · {tool}")
                    if len(srv["tools"]) > 3:
                        lines.append(f"    · … +{len(srv['tools']) - 3} more")
                placeholder.update("\n".join(lines))

    def _poll_mcp_status(self) -> None:
        """每秒轮询一次，后台 load 完后刷新 Sidebar + 追加工具。"""
        status = self.agent.mcp_status()
        if status["state"] in ("loaded", "error"):
            # 只在状态从 loading 转成终态时追加一次工具
            if status["state"] == "loaded":
                added = self.agent.refresh_tools_from_mcp()
                if added:
                    self.notify(f"MCP 已加载，追加工具: {', '.join(added[:3])}{'…' if len(added) > 3 else ''}", severity="information", timeout=3)
            self._refresh_sidebar_mcp()

    # ---- Input 事件：斜杠命令路由 + 补全面板 ---------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        """打 `/` 时弹出命令补全面板，实时过滤。"""
        text = event.value
        completions = self.query_one("#completions", Label)
        if text.startswith("/"):
            prefix = text[1:].lower()
            table = commands.register_all()
            matches = [f"/{name}" for name, cmd in table.items()
                       if name == cmd.name.lower() and name.startswith(prefix)]
            if matches:
                completions.update(" · ".join(matches))
                completions.add_class("visible")
            else:
                completions.update("（无匹配命令）")
                completions.add_class("visible")
        else:
            completions.update("")
            completions.remove_class("visible")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value
        if not text.strip():
            event.input.value = ""
            return

        stripped = text.strip()
        # 发送后隐藏补全面板
        completions = self.query_one("#completions", Label)
        completions.update("")
        completions.remove_class("visible")
        event.input.value = ""

        # ---- 斜杠命令统一路由 ----
        if stripped.startswith("/"):
            if stripped.lower().startswith("/resume") and not stripped.lower() in ("/resume", "/resume "):
                pass  # /resume <id> 走下面的 dispatch
            output = commands.dispatch(self.agent, stripped, None)

            # /resume 无参数 → 弹 ModalScreen
            if stripped.lower() == "/resume":
                self._open_resume_modal()
                return

            if output:
                chat = self.query_one(ChatLog)
                chat.add_message("system", output)
            self._refresh_usage_status("就绪")
            self._refresh_sidebar_static()
            self.query_one("#prompt", Input).focus()
            return

        # ---- 普通对话 ----
        if self._busy:
            self.notify("上一轮回复尚未结束，请稍候或按 Esc 取消", severity="warning", timeout=2)
            return
        self._busy = True
        self._streaming_bubble = None
        self._streaming_text = ""
        self._refresh_usage_status("思考中…", busy=True)
        self._run_turn(stripped)

    def _open_resume_modal(self) -> None:
        sessions = _list_sessions_cli_style(self.agent)
        self.push_screen(ResumeSessionModal(sessions), callback=self._on_resume_chosen)

    def _on_resume_chosen(self, session_id: str | None) -> None:
        if not session_id:
            return
        # commands._cmd_resume 已经在核心层实现了完整逻辑，但我们想让它在 TUI 里直接输出文本
        try:
            result = commands.dispatch(self.agent, f"/resume {session_id}", None)
            if result:
                chat = self.query_one(ChatLog)
                chat.add_message("system", result)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"恢复失败: {exc}", severity="error", timeout=3)
        self._refresh_sidebar_static()
        self._refresh_usage_status("就绪")

    # ---- 后台执行（Turn 和 Compact 都放后台线程，避免阻塞 UI） ---------
    @work(thread=True, exclusive=True, group="agent", exit_on_error=False)
    def _run_turn(self, text: str) -> None:
        try:
            self.agent.run_turn(text, ThreadEventListener(self))
        finally:
            try:
                self.call_from_thread(self._finish_turn)
            except RuntimeError:
                pass

    @work(thread=True, exclusive=True, group="agent", exit_on_error=False)
    def _run_compact(self) -> None:
        try:
            self.call_from_thread(self._busy_set, True)
            self.call_from_thread(lambda: self.set_status("压缩中（LLM 生成摘要）…", "busy"))
            self.call_from_thread(self._refresh_usage_status, "压缩中…", True)
            archive_id = self.agent.compact()
            self.call_from_thread(self._compact_success, archive_id)
        except LLMError as exc:
            self.call_from_thread(self._compact_fail, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._compact_fail, f"{exc.__class__.__name__}: {exc}")

    def _busy_set(self, v: bool) -> None:
        self._busy = v

    def _compact_success(self, archive_id: str) -> None:
        chat = self.query_one(ChatLog)
        chat.add_message("system", f"✅ 上下文已压缩，归档编号 {archive_id}。历史已替换为摘要。")
        self._busy = False
        self._refresh_usage_status("就绪")
        self.query_one("#prompt", Input).focus()

    def _compact_fail(self, message: str) -> None:
        chat = self.query_one(ChatLog)
        chat.add_message("error", f"❌ 压缩失败：{message}（原有上下文未动，可重试 /compact）")
        self._busy = False
        self._refresh_usage_status("出错", kind="error")
        self.query_one("#prompt", Input).focus()

    def _finish_turn(self) -> None:
        self._busy = False
        self._streaming_bubble = None
        self._streaming_text = ""
        self._refresh_usage_status("就绪")
        self._refresh_sidebar_static()
        self.query_one("#prompt", Input).focus()

    # ---- Token 使用量显示 -----------------------------------------------
    def _refresh_usage_status(self, label: str, busy: bool = False, kind: str = "info") -> None:
        current, window, percent, is_default = self.agent.context_usage
        default_note = "默认" if is_default else "配置"
        warn = " ⚠" if percent >= WARNING_THRESHOLD else ""
        status_text = (
            f"上下文 {format_k(current)} / {format_k(window)}({default_note}) "
            f"· {percent * 100:.0f}%"
            + warn
            + f" · {label}"
        )
        self.set_status(status_text, "busy" if busy else kind)
        if percent >= WARNING_THRESHOLD and not busy:
            self.notify(
                f"上下文使用量 {percent * 100:.0f}%，建议执行 /compact 压缩",
                severity="warning", timeout=5,
            )

    # ---- 事件处理 ------------------------------------------------------
    def handle_agent_event(self, event: AgentEvent) -> None:
        chat = self.query_one(ChatLog)
        if isinstance(event, TurnStarted):
            self._streaming_bubble = None
            chat.add_message("user", event.user_text)
        elif isinstance(event, TextDelta):
            if self._streaming_bubble is None:
                self._streaming_bubble = chat.add_message("assistant")
                self._streaming_text = ""
            self._streaming_text += event.text
            self._streaming_bubble.set_body(self._streaming_text)
            chat.scroll_to_bottom()
        elif isinstance(event, ToolCallRequested):
            self._streaming_bubble = None
            self._streaming_text = ""
            arguments = json.dumps(event.arguments, ensure_ascii=False)
            chat.add_message("tool", f"调用 {event.name}({arguments})")
            self.set_status(f"执行工具 {event.name}…", "busy")
        elif isinstance(event, ToolCallCompleted):
            result = "成功" if event.ok else "失败"
            chat.add_message("tool", f"{result} {event.name} → {_preview(event.result)}")
            self.set_status("思考中…", "busy")
        elif isinstance(event, TurnCompleted):
            self._streaming_bubble = None
            self._refresh_usage_status("就绪")
        elif isinstance(event, TurnAborted):
            self._streaming_bubble = None
            chat.add_message("warning", event.reason)
            self.set_status("已中止", "error")
        elif isinstance(event, ErrorRaised):
            self._streaming_bubble = None
            chat.add_message("error", event.message)
            self.set_status("出错", "error")

    def action_cancel_turn(self) -> None:
        if self._busy:
            self.agent.cancel()
            self.set_status("正在取消…", "busy")

    def action_clear_chat(self) -> None:
        if self._busy:
            self.notify("请先等待当前回复结束或按 Esc 取消", severity="warning", timeout=2)
            return
        self.query_one(ChatLog).remove_children()
        self.agent.reset()
        self.query_one(ChatLog).add_message("system", "对话已清空。")
        self._refresh_usage_status("就绪")
        self._refresh_sidebar_static()

    def set_status(self, text: str, kind: str = "info") -> None:
        self.query_one(StatusBar).set_status(text, kind)


def _list_sessions_cli_style(agent: Agent) -> list[dict[str, Any]]:
    store = agent.config.session.store_dir
    if not store:
        return []
    from ...core import sessions as _sessions  # late import 避免循环
    return _sessions.list_sessions(agent.project_root, store)
