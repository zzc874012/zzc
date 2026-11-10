"""命令行工具：让 Agent 能够调用本地已安装的 CLI 程序。

设计原则：
- 使用 subprocess.run 以 shell=False 执行，避免命令注入；参数通过列表逐项传递
- 合理的执行超时（默认 30s），防止长驻进程阻塞对话
- 对 stdout/stderr 和命令行回显做长度截断，避免撑爆 LLM 上下文
- 执行层错误（未找到命令 / 超时 / 权限）通过抛异常让 ToolRegistry 标记 ok=False，
  与 WebSearchToolset 的错误处理风格保持一致
- 程序返回非零退出码属于正常执行结果，仍返回 ok=True，由 LLM 根据退出码自行判断
- 不实现白名单/黑名单/审批：本阶段面向受控教学实验环境，命令以运行 zAgent 的当前用户权限执行
"""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Tool

_DEFAULT_TIMEOUT = 30.0          # 秒
_MAX_OUTPUT_CHARS = 4000         # stdout 或 stderr 单项上限
_HEAD_CHARS = 2500               # 截断时保留的头部长度
_TAIL_CHARS = 1200               # 截断时保留的尾部长度
_MAX_CMDLINE_CHARS = 200         # 命令回显行的最大长度


# ---------------------------------------------------------------------------
# 自定义异常：用于把详细错误消息传给 ToolRegistry
# ---------------------------------------------------------------------------
class RunCliError(RuntimeError):
    """RunCli 工具在子进程启动/执行层面的错误。

    handler 抛出 RunCliError 后，ToolRegistry.execute 会捕获并构造
    ToolResult(ok=False, str(exc))，保持与 WebSearchToolset 一致的错误处理语义。
    """


class RunCliToolset:
    """把 subprocess.run 封装为可供 LLM 调用的 RunCli 工具。"""

    def __init__(self, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Tool 注册
    # ------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="RunCli",
                description=(
                    "执行本地命令行程序（CLI）并返回其标准输出、标准错误和退出码。"
                    "适合调用 git/python/node/npm/系统工具等已安装的命令。"
                    "参数通过列表逐项传递，不拼接 shell 字符串，因此不需要对参数加引号或转义。"
                    "注意：PowerShell 内置别名（如 ls、dir）和 .bat/.cmd 批处理文件在 shell=False 下"
                    "可能找不到，请改用底层可执行文件名（如 git、python、cmd）。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "要执行的程序名或路径（例如 'python'、'git'、'node'）。"
                            "可以是已在 PATH 中的命令，或绝对路径。",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "传递给程序的参数列表。"
                            "每个元素是一个独立参数，不需要加引号或转义空格。"
                            "例如 ['-c', 'print(1+2)'] 执行 python -c print(1+2)。",
                        },
                    },
                    "required": ["command", "args"],
                },
                handler=self.run_cli,
            ),
        ]

    # ------------------------------------------------------------------
    # 实际执行逻辑
    # ------------------------------------------------------------------
    def run_cli(self, command: str, args: list[str]) -> str:
        """handler 入口：接收 command + args，返回给 LLM 的格式化结果文本。

        执行层错误（未找到命令 / 超时 / 权限）通过 RunCliError 抛出，
        ToolRegistry.execute 会捕获并返回 ok=False。
        程序返回非零退出码仍视为工具正常执行，返回 ok=True 并在文本中体现退出码。
        """
        # 1. 参数校验
        command = (command or "").strip()
        if not command:
            raise ValueError("command 参数不能为空")
        if not isinstance(args, list):
            raise ValueError("args 必须是字符串数组")

        # 2. 执行子进程
        try:
            result = subprocess.run(
                [command, *args],
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout,
            )
        except FileNotFoundError as exc:
            raise RunCliError(
                f"[RunCli] 未找到程序 '{command}'。\n"
                f"可能原因：该命令未安装、不在 PATH 中，或拼写错误。\n"
                f"提示：Windows 上 PowerShell 内置别名（如 ls）不是独立可执行文件，"
                f"请改用底层程序名（如 cmd /c dir）。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RunCliError(
                f"[RunCli] 超时: 命令在 {self._timeout}s 内未完成，已被强制终止。\n"
                f"被终止命令: {command} {' '.join(args)}\n"
                f"可能需要调整超时时间，或检查该命令是否在等待用户输入。"
            ) from exc
        except PermissionError as exc:
            raise RunCliError(
                f"[RunCli] 权限错误: 无法执行 '{command}'。\n详情: {exc}"
            ) from exc
        except OSError as exc:
            raise RunCliError(
                f"[RunCli] 系统错误: 执行 '{command}' 失败。\n详情: {exc}"
            ) from exc

        # 3. 格式化输出（含截断）
        return self._format_result(result, command, args)

    # ------------------------------------------------------------------
    # 输出格式化 & 截断
    # ------------------------------------------------------------------
    def _format_result(
        self,
        result: subprocess.CompletedProcess[str],
        command: str,
        args: list[str],
    ) -> str:
        cmdline_raw = f"{command} {' '.join(args)}"
        cmdline_display = self._truncate_cmdline(cmdline_raw)

        stdout, stdout_truncated = self._truncate_output(result.stdout or "")
        stderr, stderr_truncated = self._truncate_output(result.stderr or "")

        # 标记截断情况
        truncation_notes: list[str] = []
        if cmdline_display != cmdline_raw:
            truncation_notes.append(
                f"命令行原始长度 {len(cmdline_raw)} 字符，已截断"
            )
        if stdout_truncated:
            truncation_notes.append(
                f"stdout 原始长度 {len(result.stdout or '')} 字符，已截断"
            )
        if stderr_truncated:
            truncation_notes.append(
                f"stderr 原始长度 {len(result.stderr or '')} 字符，已截断"
            )

        lines: list[str] = [
            f"[RunCli] 命令: {cmdline_display}",
            f"退出码: {result.returncode}",
        ]
        if truncation_notes:
            lines.append("截断说明: " + "; ".join(truncation_notes))
        lines.append("")

        if stdout.strip():
            lines.append("--- stdout ---")
            lines.append(stdout.rstrip())
        else:
            lines.append("--- stdout (空) ---")
        lines.append("")

        if stderr.strip():
            lines.append("--- stderr ---")
            lines.append(stderr.rstrip())
        else:
            lines.append("--- stderr (空) ---")

        return "\n".join(lines).rstrip()

    @staticmethod
    def _truncate_cmdline(cmdline: str) -> str:
        """截断命令行回显，避免超长 -c 脚本撑爆头部。"""
        if len(cmdline) <= _MAX_CMDLINE_CHARS:
            return cmdline
        keep_head = _MAX_CMDLINE_CHARS - len("...")
        return cmdline[:keep_head] + "..."

    @staticmethod
    def _truncate_output(text: str) -> tuple[str, bool]:
        """对 stdout/stderr 做头尾保留截断。返回 (处理后的文本, 是否发生截断)。"""
        if len(text) <= _MAX_OUTPUT_CHARS:
            return text, False

        head = text[:_HEAD_CHARS]
        tail = text[-_TAIL_CHARS:]
        total = len(text)
        gap = (
            f"\n...[截断: 原始输出 {total} 字符，"
            f"中间省略 {total - _HEAD_CHARS - _TAIL_CHARS} 字符]...\n"
        )
        return head + gap + tail, True
