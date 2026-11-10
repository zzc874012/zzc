"""上下文归档与关键词检索：纯本地 JSONL 文件，无向量数据库。

归档文件命名：<YYYYMMDD-HHMMSS>.jsonl
每条记录一个 JSON 对象，包含原始 message 的 role / content / tool_calls / tool_call_id / name，
外加 seq（自增序号）和 timestamp（ISO 时间戳）。

Token 估算：本阶段不用 tiktoken，简单启发式：
- 中文汉字约 1 token/字
- 英文 / 数字 / 标点约 4 chars/token
- 整体折中用 2 chars/token 近似，足够阈值判断
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Iterable

ARCHIVE_DIRNAME = ".agents"
CONTEXT_SUBDIR = "context"
DEFAULT_CONTEXT_WINDOW = 128 * 1024  # 128k
WARNING_THRESHOLD = 0.8  # 80%
# 估算系数：混合中英文场景下的经验值
_CHARS_PER_TOKEN = 2


# ---------------------------------------------------------------------------
# 路径 / Token 估算
# ---------------------------------------------------------------------------
def archive_root(project_root: Path) -> Path:
    """返回 <project_root>/.agents/context/（自动创建）。"""
    root = Path(project_root).resolve() / ARCHIVE_DIRNAME / CONTEXT_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def estimate_tokens(messages: Iterable[dict[str, Any]]) -> int:
    """估算一组 messages 的 token 数：把每条消息的 content / tool_calls 参数 / tool 返回全部算进去。"""
    total_chars = 0
    for msg in messages:
        content = msg.get("content") or ""
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            # 多模态 content（本阶段暂未实际用到，但防御性处理）
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(str(part.get("text", "")))
        tool_calls = msg.get("tool_calls") or []
        for call in tool_calls:
            fn = call.get("function") or {}
            total_chars += len(fn.get("name", "")) + len(fn.get("arguments", ""))
        if msg.get("role") == "tool":
            # tool 消息的 tool_call_id 很短，忽略
            pass
    # System Prompt 等固定头部大约 500-1000 token，粗略加成
    return max(1, int(total_chars / _CHARS_PER_TOKEN))


def estimate_system_prompt_tokens(system_text: str, tools_schemas: Iterable[dict[str, Any]]) -> int:
    """System Prompt + 工具定义的 token 估算。"""
    tool_json = json.dumps(list(tools_schemas), ensure_ascii=False) if tools_schemas else ""
    return max(1, int((len(system_text) + len(tool_json)) / _CHARS_PER_TOKEN))


def format_k(value: int | float) -> str:
    """把 token 数格式化成 '12.3k' 形式。"""
    if value < 1000:
        return f"{int(value)}"
    return f"{value / 1024:.1f}k"


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------
def _archive_filename() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".jsonl"


def create_archive(project_root: Path, messages: list[dict[str, Any]]) -> tuple[str, Path]:
    """把原始 messages 写进新归档文件。返回 (archive_id, file_path)。

    archive_id 就是去掉 .jsonl 后缀的文件名，例如 '20261022-153000'。
    """
    root = archive_root(project_root)
    filename = _archive_filename()
    archive_path = root / filename
    # 避免同一秒内重复；冲突时加 .2 / .3
    counter = 2
    while archive_path.exists():
        archive_path = root / f"{filename.removesuffix('.jsonl')}.{counter}.jsonl"
        counter += 1
    with archive_path.open("w", encoding="utf-8") as f:
        for seq, msg in enumerate(messages):
            record = {
                "seq": seq,
                "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
                **msg,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    archive_id = archive_path.stem
    return archive_id, archive_path


def list_archives(project_root: Path) -> list[dict[str, Any]]:
    """列出所有归档文件，按时间倒序。"""
    root = archive_root(project_root)
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl"), reverse=True):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        out.append({
            "id": path.stem,
            "path": str(path),
            "size_bytes": size,
        })
    return out


# ---------------------------------------------------------------------------
# 关键词检索
# ---------------------------------------------------------------------------
def search_archives(
    project_root: Path,
    keyword: str,
    *,
    archive_id: str | None = None,
    max_results: int = 15,
    window_chars: int = 120,
) -> list[dict[str, Any]]:
    """在归档里搜索关键词，返回最多 max_results 条片段。

    每条包含：archive_id / role / seq / hit_line / snippet（含前后 window_chars 窗口）。
    """
    if not keyword.strip():
        return []
    kw = keyword.strip().lower()
    results: list[dict[str, Any]] = []
    root = archive_root(project_root)

    if archive_id:
        target = root / f"{archive_id}.jsonl"
        files = [target] if target.is_file() else []
    else:
        files = list(root.glob("*.jsonl"))

    for path in files:
        try:
            with path.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    if kw not in line.lower():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = record.get("content") or ""
                    if isinstance(content, list):
                        content = " ".join(
                            (p.get("text", "") if isinstance(p, dict) else str(p))
                            for p in content
                        )
                    haystack = content
                    # tool_calls 参数也查
                    for call in record.get("tool_calls") or []:
                        fn = call.get("function") or {}
                        haystack += " " + fn.get("name", "") + " " + fn.get("arguments", "")
                    idx = haystack.lower().find(kw)
                    if idx < 0:
                        continue
                    start = max(0, idx - window_chars)
                    end = min(len(haystack), idx + len(kw) + window_chars)
                    snippet = haystack[start:end]
                    if start > 0:
                        snippet = "…" + snippet
                    if end < len(haystack):
                        snippet = snippet + "…"
                    results.append({
                        "archive_id": path.stem,
                        "role": record.get("role", "?"),
                        "seq": record.get("seq"),
                        "hit_line": line_no,
                        "snippet": snippet,
                    })
                    if len(results) >= max_results:
                        return results
        except OSError:
            continue
    return results
