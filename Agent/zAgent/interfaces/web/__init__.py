"""Web 界面适配器入口（Starlette + SSE + 原生前端，零新增依赖）。"""

from __future__ import annotations

from ...core import AppConfig
from .app import WebApp

__all__ = ["run_web", "WebApp"]


def run_web(config: AppConfig, *, host: str = "127.0.0.1", port: int = 8000) -> int:
    """启动 Web 服务（阻塞，直到 Ctrl+C）。"""
    import uvicorn

    application = WebApp(config).create_app()
    print(f"zAgent Web · 模型 {config.llm.model} · 工作目录 {config.agent.workspace}")
    print(f"浏览器打开 http://{host}:{port}  （Ctrl+C 结束）")
    uvicorn.run(application, host=host, port=port, log_level="warning")
    return 0
