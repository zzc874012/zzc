"""AnythingLLM MCP Server - MVP

通过 MCP 2026-07-28 Streamable HTTP 暴露本机 AnythingLLM 第一个工作区的 AI 问答能力。
启动: python server.py
"""
from __future__ import annotations

import os

import httpx
from mcp.server import MCPServer

# ---------------------------------------------------------------------------
# 配置（环境变量，有默认值）
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("ANYTHINGLLM_BASE_URL", "http://localhost:3001").rstrip("/")
API_KEY = os.getenv("ANYTHINGLLM_API_KEY", "YOUR_ANYTHINGLLM_API_KEY")
MCP_HOST = os.getenv("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))

_headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

# 首次获取后缓存的第一个工作区 slug
_first_slug: str | None = None


async def _get_first_workspace_slug() -> str:
    """获取第一个工作区的 slug，模块级缓存避免重复请求。"""
    global _first_slug
    if _first_slug:
        return _first_slug
    async with httpx.AsyncClient(timeout=30) as c:
        resp = await c.get(f"{BASE_URL}/api/v1/workspaces", headers=_headers)
        resp.raise_for_status()
        data = resp.json()
        # 响应可能是 {"workspaces": [...]} 或直接是列表，兼容两种
        workspaces = data.get("workspaces", data) if isinstance(data, dict) else data
        if not workspaces:
            raise RuntimeError("AnythingLLM 中没有可用的工作区")
        _first_slug = workspaces[0]["slug"]
        return _first_slug


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = MCPServer(
    name="anythingllm-mcp",
    title="AnythingLLM MCP",
    description="通过 MCP 暴露本机 AnythingLLM 第一个工作区的 AI 问答能力。",
)


@mcp.tool()
async def chat_with_first_workspace(message: str) -> dict:
    """向 AnythingLLM 第一个工作区提问，返回 AI 答案和引用来源。

    Args:
        message: 要提问的问题内容。
    """
    slug = await _get_first_workspace_slug()
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.post(
            f"{BASE_URL}/api/v1/workspace/{slug}/chat",
            headers=_headers,
            json={"message": message, "mode": "chat"},
        )
        resp.raise_for_status()
        data = resp.json()
    return {
        "answer": data.get("textResponse", ""),
        "sources": data.get("sources", []),
        "workspace_slug": slug,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
    )
