"""应用入口：解析命令行参数并选择界面实现。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .core import (
    ConfigError,
    config_exists,
    default_config_content,
    load_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zAgent", description="zAgent：LLM 文件操作智能体")
    parser.add_argument(
        "--ui",
        choices=("tui", "cli", "web"),
        default="tui",
        help="界面类型，默认 tui",
    )
    parser.add_argument("--config", help="配置文件路径，默认当前目录 config.ini")
    parser.add_argument("--workspace", help="允许文件操作的工作目录，默认当前目录")
    parser.add_argument(
        "--max-tool-rounds",
        type=int,
        dest="max_tool_rounds",
        help="单轮对话最多工具调用轮数，默认读取配置文件（8）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web 界面监听地址，默认 127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=8000, help="Web 界面监听端口，默认 8000"
    )
    return parser


def _create_default_config_and_notify(config_path: Path, ui: str) -> int:
    """当 config.ini 不存在时，写入模板并提示用户修改后重启。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config_path.write_text(default_config_content(), encoding="utf-8")
    except OSError as exc:
        print(f"无法写入 config.ini 模板: {exc}", file=sys.stderr)
        return 3

    _print_setup_guide(config_path, ui)

    # TUI 额外弹一个 ModalScreen 提醒（Textual 8 支持）
    if ui == "tui":
        try:
            return _run_setup_dialog(config_path)
        except Exception as exc:  # noqa: BLE001 — 任何 Dialog 异常都退回到 CLI 提示
            print(f"（启动提示对话框失败，回退到文本提示: {exc}）", file=sys.stderr)

    return 3  # 退出码 3 = 配置未就绪，引导用户重启


def _print_setup_guide(config_path: Path, ui: str) -> None:
    print("\n" + "=" * 60, file=sys.stderr)
    print("  zAgent 首次启动 —— config.ini 模板已生成", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f" 位置: {config_path}", file=sys.stderr)
    print("", file=sys.stderr)
    print(" 里面已经填了 mock 的 api_key / base_url / model，", file=sys.stderr)
    print(" 请把 [llm] 段的三项改成你真实可用的值后", file=sys.stderr)
    print(" 重新运行即可。其他段（[agent] / [compact] / [session] / [agents_md]）", file=sys.stderr)
    print(" 全部带默认值，不修改也能启动。", file=sys.stderr)
    print("=" * 60, file=sys.stderr)


def _run_setup_dialog(config_path: Path) -> int:
    """用 Textual ModalScreen 弹一个「配置已生成，请修改后重启」的对话框，用户看完点击按钮退出。"""
    from textual.screen import ModalScreen
    from textual.containers import Vertical
    from textual.widgets import Button, Label

    class SetupDialog(ModalScreen[None]):
        def compose(self):
            with Vertical(id="setup-dialog"):
                yield Label("zAgent 首次启动", id="setup-title")
                yield Label(
                    "config.ini 模板已生成，请修改后重启才能正常工作。\n"
                    f"位置：{config_path}",
                    id="setup-body",
                )
                yield Button("好的，我去修改", variant="primary", id="confirm")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.app.exit()

        CSS = """
        ModalScreen { background: rgba(36, 41, 47, 0.35); }
        #setup-dialog {
            width: 60; height: 12; padding: 1 2;
            background: #ffffff; border: thick #d0d7de; border-radius: 6px; margin: 2;
        }
        #setup-title { text-align: center; text-style: bold; color: #24292f; border-bottom: solid 1px #d0d7de; padding-bottom: 1; margin-bottom: 1; }
        #setup-body { color: #24292f; height: auto; }
        #confirm { dock: bottom; width: 100%; background: #0969da; color: #ffffff; border: solid 1px #0969da; }
        """

    from textual.app import App as _TextualApp

    class _DialogHost(_TextualApp[None]):
        CSS = """
        Screen { background: #ffffff; color: #24292f; }
        Label { color: #24292f; }
        Button { background: #f6f8fa; color: #24292f; border: solid 1px #d0d7de; }
        """

        def on_mount(self) -> None:
            self.push_screen(SetupDialog())

    _DialogHost().run(headless=False)
    return 3


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 配置路径判定：先看 --config 参数，否则用当前目录 config.ini
    if args.config:
        config_path = Path(args.config).expanduser()
    else:
        # 跟 core.config.default_config_path() 对齐（忽略 ZAGENT_CONFIG env，以显式或 CWD 为准）
        config_path = Path.cwd() / "config.ini"

    # ---- 没 config.ini → 生成模板并提示 ----
    if not config_path.is_file():
        return _create_default_config_and_notify(config_path, args.ui)

    try:
        config = load_config(
            config_path=args.config,
            workspace=args.workspace,
            max_tool_rounds=args.max_tool_rounds,
        )
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2

    if args.ui == "cli":
        from .interfaces.cli import run_cli

        return run_cli(config)

    if args.ui == "web":
        from .interfaces.web import run_web

        return run_web(config, host=args.host, port=args.port)

    from .interfaces.tui import run_tui

    return run_tui(config)
