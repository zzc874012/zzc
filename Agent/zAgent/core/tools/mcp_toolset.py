"""把 MCP Client 暴露的工具动态注册到 ToolRegistry。

ToolRegistry.execute 会统一捕获 handler 抛出的异常并转为 ToolResult(ok=False)，
所以 MCP handler 只需要正常 raise MCPClientError；ToolResult.output 里就是可行动的错误摘要。
"""

from __future__ import annotations

from ..mcp_client import MCPClientManager
from .base import Tool


class MCPToolset:
    """根据 .mcp.json 配置动态拼装 MCP 工具。"""

    def __init__(self, manager: MCPClientManager) -> None:
        self._manager = manager

    def tools(self) -> list[Tool]:
        out: list[Tool] = []
        for t in self._manager.all_tools():
            out.append(Tool(
                name=t.llm_name,
                description=t.description,
                parameters=t.parameters,
                handler=self._make_handler(t.llm_name),
            ))
        return out

    def _make_handler(self, llm_name: str):
        def handler(**kwargs):
            return self._manager.call(llm_name, kwargs)
        handler.__name__ = f"mcp_{llm_name}"
        return handler

    # ------------------------------------------------------------------
    # 元信息：启动时/界面上展示连接状态，供 CLI / TUI / Web 层调用
    # ------------------------------------------------------------------
    def server_summaries(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for info in self._manager.servers.values():
            out.append({
                "name": info.name,
                "type": info.config.get("type"),
                "connected": info.last_error is None,
                "tool_count": len(info.tools),
                "error": info.last_error,
            })
        return out
