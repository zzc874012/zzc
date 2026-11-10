"""斜杠命令注册表：统一维护命令名 / 处理函数 / 帮助文本 / 别名。

handler 签名：
    (agent, args_text: str, listener) -> str | None
返回 None 表示命令无输出（handler 自己负责事件/界面通知），返回 str 表示直接打印给界面。
"""

from __future__ import annotations

import copy
import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import context as _context
from . import sessions as _sessions
from .compact import COMPACTION_PROMPT
from .config import CompactSettings, SessionSettings
from .messages import system_message


@dataclass(frozen=True)
class Command:
    name: str
    handler: Any   # Callable[[Agent, str, EventListener | None], str | None]
    help: str
    aliases: tuple[str, ...] = ()


def register_all() -> dict[str, Command]:
    """返回 {lowercase_name_or_alias: Command} 字典。CLI / TUI 共用同一份注册表。"""
    table: list[Command] = [
        Command("help", _cmd_help, "显示所有可用命令"),
        Command("usage", _cmd_usage, "显示当前上下文 token 使用量"),
        Command("compact", _cmd_compact, "手动触发上下文压缩，归档 + 替换为 LLM 摘要"),
        Command("new", _cmd_new, "新建会话（当前会话会先保存）"),
        Command("save", _cmd_save, "手动保存当前会话快照到 .agents/sessions/"),
        Command("list", _cmd_list, "列出最近保存的会话（/list sessions）"),
        Command("resume", _cmd_resume, "恢复某个历史会话，替换当前 history；不传 id 则列出可选"),
        Command("quit", _cmd_quit, "保存会话并退出 zAgent", aliases=("exit",)),
        Command("clear", _cmd_clear, "清空当前对话历史（不保存，慎用）"),
        Command("agents", _cmd_agents, "查看当前已加载的技能列表"),
        Command("tools", _cmd_tools, "列出当前可用的所有 Function Calling 工具"),
        Command("recall", _cmd_recall, "搜索历史归档（相当于调用 recallContext 工具）"),
        Command("mcp", _cmd_mcp, "显示当前已连接的 MCP servers 及工具"),
        Command("version", _cmd_version, "显示 zAgent 版本信息"),
    ]
    out: dict[str, Command] = {}
    for cmd in table:
        out[cmd.name.lower()] = cmd
        for alias in cmd.aliases:
            out[alias.lower()] = cmd
    return out


# ---------------------------------------------------------------------------
# handler 实现（所有 handler 内部 import，避免循环引用）
# ---------------------------------------------------------------------------
def _cmd_help(agent, args_text, listener) -> str:
    cmds = register_all()
    lines = ["/help — 所有可用命令："]
    for name, cmd in cmds.items():
        if name != cmd.name.lower():
            continue  # 别名跳过，只显示主名
        alias_str = f"  (别名: {', '.join(cmd.aliases)})" if cmd.aliases else ""
        lines.append(f"  /{cmd.name}{alias_str}\n      {cmd.help}")
    return "\n".join(lines)


def _cmd_usage(agent, args_text, listener) -> str:
    from . import format_k
    current, window, percent, is_default = agent.context_usage
    default_note = "（默认 128k）" if is_default else ""
    return (
        f"当前上下文: {format_k(current)} / {format_k(window)}{default_note} "
        f"({percent * 100:.1f}%)"
    )


def _cmd_compact(agent, args_text, listener) -> str | None:
    from .llm.base import LLMError
    try:
        archive_id = agent.compact(listener)
    except LLMError as exc:
        return f"压缩失败：{exc}"
    return f"✅ 上下文已压缩，归档编号 {archive_id}。历史已替换为摘要。"


def _cmd_new(agent, args_text, listener) -> str:
    _auto_save(agent)
    # reset + 重新注入 AGENTS.md（如果存在）
    agent.reset()
    return "已新建会话（当前会话已自动保存）。"


def _cmd_save(agent, args_text, listener) -> str:
    session_id = _auto_save(agent)
    return f"✅ 会话已保存，编号 {session_id}" if session_id else "（无对话历史，跳过保存）"


def _auto_save(agent) -> str | None:
    """内部工具：如果 session.store_dir 非空且 history 非空则保存并返回 session_id。"""
    cfg = agent.config.session
    if not cfg.store_dir or not agent.history:
        return None
    sid, _ = _sessions.save_session(
        project_root=Path(str(agent.config.agent.workspace)),
        store_dir=cfg.store_dir,
        history=agent.history,
        system_prompt=agent._system_prompt,  # noqa: SLF001 — 包内私有可接受
    )
    return sid


def _cmd_list(agent, args_text, listener) -> str:
    store = agent.config.session.store_dir
    if not store:
        return "session.store_dir 未配置，历史会话功能已禁用。"
    sessions = _sessions.list_sessions(agent.config.agent.workspace, store)
    if not sessions:
        return "暂无保存的历史会话。"
    lines = [f"最近 {len(sessions)} 个会话："]
    for i, s in enumerate(sessions, 1):
        lines.append(
            f"  {i}. [{s['session_id']}] {s['title']}  "
            f"(消息 {s['message_count']} 条 · 最后活跃 {s['last_active_at']})"
        )
    lines.append("")
    lines.append("输入 /resume <编号或 session_id> 恢复，例如 /resume 1 或 /resume 20261104-150200")
    return "\n".join(lines)


def _cmd_resume(agent, args_text, listener) -> str | None:
    """恢复会话。有 /resume ModalScreen 时（TUI）直接弹窗，CLI 解析参数。"""
    store = agent.config.session.store_dir
    if not store:
        return "session.store_dir 未配置，历史会话功能已禁用。"
    stripped = (args_text or "").strip()
    sessions = _sessions.list_sessions(agent.config.agent.workspace, store)
    if not sessions:
        return "暂无保存的历史会话，无法恢复。"
    if not stripped:
        # 无参数 → 列出来让用户选（CLI 打印，TUI 由调用方拦截走 ModalScreen）
        return _cmd_list(agent, args_text, listener) + "\n请输入 /resume <编号或 session_id>"

    # 参数可以是编号（1..n）或 session_id
    target = None
    if stripped.isdigit():
        idx = int(stripped) - 1
        if 0 <= idx < len(sessions):
            target = sessions[idx]
    else:
        target = next((s for s in sessions if s["session_id"] == stripped), None)

    if target is None:
        return f"找不到会话: {stripped}（可用 /list 查看）"

    try:
        data = _sessions.load_session(agent.config.agent.workspace, store, target["session_id"])
    except FileNotFoundError as exc:
        return str(exc)

    # 把 history 完整替换；原 session 先保存
    _auto_save(agent)
    restored = data.get("history", [])
    agent.reset()
    agent._messages.extend(copy.deepcopy(restored))  # noqa: SLF001
    return (
        f"✅ 已恢复会话 [{target['session_id']}] {target['title']}\n"
        f"   包含 {target.get('message_count', len(restored))} 条消息。"
    )


def _cmd_clear(agent, args_text, listener) -> str:
    agent.reset()
    return "对话已清空（不保存历史）。"


def _cmd_quit(agent, args_text, listener) -> str:
    sid = _auto_save(agent)
    return f"退出 zAgent。" + (f" 会话已自动保存为 {sid}。" if sid else "")


def _cmd_agents(agent, args_text, listener) -> str:
    cmds = agent.skills.system_prompt_section()
    return cmds or "当前项目未发现任何 Skill（skills/ 目录为空或缺失 SKILL.md）。"


def _cmd_tools(agent, args_text, listener) -> str:
    lines = [f"当前可用 Function Calling 工具（共 {len(agent.registry.schemas())} 个）："]
    for t in agent.registry.schemas():
        fn = t["function"]
        desc = (fn.get("description") or "")[:60]
        lines.append(f"  • {fn['name']}: {desc}")
    return "\n".join(lines)


def _cmd_recall(agent, args_text, listener) -> str:
    text = (args_text or "").strip()
    if not text:
        return "用法：/recall <关键词>"
    # 直接调用 RecallToolset 里的 handler
    from .tools.recall_tool import RecallToolset
    handler = RecallToolset(agent._project_root).tools()[0].handler  # noqa: SLF001
    return handler(text=text)


def _cmd_mcp(agent, args_text, listener) -> str:
    out = [f"MCP Server 状态（来自 .mcp.json，project root = {agent._project_root}）:"]
    servers = agent.mcp.servers
    if not servers:
        out.append("  (未配置任何 MCP server)")
        return "\n".join(out)
    for name, info in servers.items():
        ok = info.last_error is None
        status = "✅ 已连接" if ok else f"❌ 连接失败：{info.last_error or '未知'}"
        out.append(f"\n  [{name}]  {status}  ·  type={info.config.get('type', '?')}")
        if ok:
            for t in info.tools:
                desc = t.description[:70]
                out.append(f"    - {t.llm_name}: {desc}")
    return "\n".join(out)


def _cmd_version(agent, args_text, listener) -> str:
    return "zAgent · version 0.2（MCP / Skills / Compaction / Sessions）"


# ---------------------------------------------------------------------------
# 辅助：把裸命令字符串路由到 handler（CLI / TUI 共用）
# ---------------------------------------------------------------------------
_COMMANDS = None


def dispatch(agent, raw_line: str, listener) -> str | None:
    """路由一条斜杠命令；非斜杠开头返回 None（让调用方自己处理）。"""
    global _COMMANDS
    if _COMMANDS is None:
        _COMMANDS = register_all()
    stripped = raw_line.strip()
    if not stripped.startswith("/"):
        return None
    # 切分出命令名和剩余参数
    parts = stripped[1:].split(None, 1)
    if not parts:
        return None
    name = parts[0].lower()
    args_text = parts[1] if len(parts) > 1 else ""
    cmd = _COMMANDS.get(name)
    if cmd is None:
        return f"未知命令 /{parts[0]}。输入 /help 查看可用命令。"
    return cmd.handler(agent, args_text, listener)
