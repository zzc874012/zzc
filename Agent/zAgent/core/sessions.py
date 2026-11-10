"""会话持久化：把完整 Agent.history 保存为独立 JSON，支持列出 / 恢复。

与 context 归档解耦：sessions 是"可恢复的完整会话快照"（带完整 history + 事件元信息），
context 归档是"压缩前的原始消息序列"（只保留 role/content/tool_calls）。

存储格式：
    <session_id>.json
    {
        "session_id": "20261104-150200",
        "title": "用户原始指令或自动生成",
        "created_at": "...",
        "last_active_at": "...",
        "message_count": 8,
        "history": [ {...}, ... ],   # 完整 ChatMessage 列表
        "system_prompt": "...",     # 当时的 system prompt，恢复时一起用
        "agents_md_injected": true  # 恢复时要不要再注入 AGENTS.md
    }
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
from pathlib import Path
from typing import Any

from .messages import ChatMessage, system_message


def _session_id() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def session_root(project_root: Path, store_dir: str = ".agents/sessions") -> Path:
    root = Path(project_root).resolve() / store_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_sessions(
    project_root: Path,
    store_dir: str = ".agents/sessions",
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出最近 limit 个会话（按 last_active_at 倒序）。"""
    root = session_root(project_root, store_dir)
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), reverse=True)[:limit * 4]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "session_id": data.get("session_id", path.stem),
            "title": data.get("title", "(无标题)"),
            "created_at": data.get("created_at", ""),
            "last_active_at": data.get("last_active_at", ""),
            "message_count": data.get("message_count", 0),
            "path": str(path),
        })
    out.sort(key=lambda x: x["last_active_at"] or x["created_at"], reverse=True)
    return out[:limit]


def save_session(
    project_root: Path,
    store_dir: str,
    history: list[ChatMessage],
    system_prompt: str,
    *,
    session_id: str | None = None,
    title: str | None = None,
) -> tuple[str, Path]:
    """把 history 落盘。session_id 已存在则在同一个文件上覆盖（更新 last_active_at）。"""
    root = session_root(project_root, store_dir)
    now = _dt.datetime.now().isoformat(timespec="seconds")

    # 已有同 session_id 则沿用 created_at / title
    existing_path: Path | None = None
    if session_id:
        existing_path = root / f"{session_id}.json"

    title = title or _guess_title(history) or "(无标题)"
    session_id = session_id or _session_id()
    path = existing_path or root / f"{session_id}.json"

    created_at = now
    if existing_path and existing_path.is_file():
        try:
            created_at = json.loads(existing_path.read_text(encoding="utf-8")).get("created_at", now)
        except (OSError, json.JSONDecodeError):
            pass

    payload = {
        "session_id": session_id,
        "title": title,
        "created_at": created_at,
        "last_active_at": now,
        "message_count": len(history),
        "history": copy.deepcopy(history),
        "system_prompt": system_prompt,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 维护 max_sessions 上限：超出时删最旧的
    return session_id, path


def load_session(project_root: Path, store_dir: str, session_id: str) -> dict[str, Any]:
    """读取指定 session_id 的完整快照。找不到抛 FileNotFoundError。"""
    path = session_root(project_root, store_dir) / f"{session_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"会话不存在: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def delete_session(project_root: Path, store_dir: str, session_id: str) -> None:
    path = session_root(project_root, store_dir) / f"{session_id}.json"
    if path.is_file():
        path.unlink()


def _guess_title(history: list[ChatMessage]) -> str:
    """取第一条 user 消息前 30 字做标题。"""
    for msg in history:
        if msg.get("role") == "user":
            content = msg.get("content") or ""
            if isinstance(content, str) and content.strip():
                title = content.strip().split("\n", 1)[0]
                return title[:40] + ("…" if len(title) > 40 else "")
    return ""
