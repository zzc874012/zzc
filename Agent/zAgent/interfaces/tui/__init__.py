"""Textual TUI 适配器入口。"""

from __future__ import annotations

from ...core import AppConfig, build_agent


def run_tui(config: AppConfig) -> int:
    from .app import ZAgentApp

    ZAgentApp(build_agent(config), config).run()
    return 0


__all__ = ["run_tui"]
