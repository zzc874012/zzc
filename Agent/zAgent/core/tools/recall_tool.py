"""recallContext Function Calling 工具：让 LLM 能检索已归档的原始上下文。"""

from __future__ import annotations

from pathlib import Path

from .. import context as _context
from .base import Tool


class RecallToolset:
    """提供 recallContext 工具，依赖项目根目录来定位 .agents/context/ 归档。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root).resolve()

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="recallContext",
                description=(
                    "检索此前压缩归档的原始上下文内容。当你发现压缩摘要缺少某些历史细节、"
                    "需要回忆之前讨论的具体内容时使用。\n"
                    "- 传 id（可选）可限定只查某个归档编号；不传则在所有归档中搜索。\n"
                    "- text 是必须的关键词，用于全文匹配。\n"
                    "返回匹配片段、所在归档编号和位置。结果较多时只返回前若干条。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "可选，归档编号（压缩时显示的那个时间戳，例如 '20261022-153000'）",
                        },
                        "text": {
                            "type": "string",
                            "description": "必需，要查找的关键词或文本",
                        },
                    },
                    "required": ["text"],
                },
                handler=self.recall,
            ),
        ]

    def recall(self, text: str, id: str | None = None) -> str:
        results = _context.search_archives(self._project_root, text, archive_id=id)
        if not results:
            archives = _context.list_archives(self._project_root)
            if id:
                return f"未在归档 {id} 中找到 '{text}'。" + (
                    f" 注意：该归档文件不存在。现有归档：{[a['id'] for a in archives[:5]]}"
                    if not archives
                    else ""
                )
            return (
                f"未找到 '{text}' 的匹配。"
                + (
                    f" 现有归档共 {len(archives)} 个：{[a['id'] for a in archives[:5]]}。"
                    if archives
                    else " 还没有任何归档文件。"
                )
            )
        # 格式化返回：每条包含 archive_id / role / seq / snippet，总长度限制
        lines: list[str] = [f"共找到 {len(results)} 条匹配，以下为前 {min(10, len(results))} 条：\n"]
        for r in results[:10]:
            lines.append(
                f"【归档 {r['archive_id']} · {r['role']} · 位置 seq={r['seq']} / 第 {r['hit_line']} 行】"
            )
            lines.append(f"  {r['snippet']}")
            lines.append("")
        return "\n".join(lines).strip()
