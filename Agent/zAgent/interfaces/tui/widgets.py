"""TUI 展示组件：只负责渲染，不包含任何 Agent 逻辑。"""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Static

ROLE_TITLES = {
    "user": "你",
    "assistant": "AI",
    "tool": "工具",
    "warning": "提示",
    "error": "错误",
    "system": "系统",
}


class MessageBubble(Vertical):
    """一条对话消息（标题 + 可流式更新的正文）。"""

    def __init__(self, role: str, text: str = "") -> None:
        super().__init__(classes=f"bubble {role}")
        self.role = role
        self._text = text
        # 直接持有正文控件，未完成挂载时也能安全更新（流式输出需要）
        self._body = Static(Text(text), classes="bubble-body")

    def compose(self) -> ComposeResult:
        yield Label(ROLE_TITLES.get(self.role, self.role), classes="bubble-title")
        yield self._body

    def set_body(self, text: str) -> None:
        self._text = text
        self._body.update(Text(text))


class ChatLog(VerticalScroll):
    """对话区：按顺序追加消息气泡并保持滚动到底部。"""

    def add_message(self, role: str, text: str = "") -> MessageBubble:
        bubble = MessageBubble(role, text)
        self.mount(bubble)
        self.call_after_refresh(self.scroll_to_bottom)
        return bubble

    def scroll_to_bottom(self) -> None:
        self.scroll_end(animate=False)


class StatusBar(Static):
    """底部状态栏，用颜色区分空闲 / 忙碌 / 出错。"""

    def __init__(self, text: str = "", *, id: str | None = None) -> None:
        super().__init__(Text(text), id=id)
        self.status = text

    def set_status(self, text: str, kind: str = "info") -> None:
        self.status = text
        self.update(Text(text))
        for name in ("info", "busy", "error"):
            self.remove_class(name)
        self.add_class(kind)
