"""文件操作工具组：路径沙箱 + 10 个文件系统工具。"""

from __future__ import annotations

import mimetypes
import os
import shutil
from pathlib import Path

from .base import Tool


class Workspace:
    """把相对路径解析到工作目录内，并阻止任何越界访问。"""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()

    def resolve(self, path: str | os.PathLike[str]) -> Path:
        raw = Path(str(path)).expanduser()
        candidate = raw if raw.is_absolute() else self.root / raw
        resolved = candidate.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise PermissionError(f"路径 {path} 超出工作目录 {self.root}")
        return resolved


class FileOpsToolset:
    """文件操作业务实现，只依赖 pathlib / shutil，与 LLM 协议无关。"""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="getCwd",
                description="获取当前工作目录（所有文件操作只允许在该目录内进行）",
                parameters={"type": "object", "properties": {}},
                handler=self.get_cwd,
            ),
            Tool(
                name="listDir",
                description="列出指定目录下所有文件和子目录",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径，默认为当前工作目录"}
                    },
                    "required": ["path"],
                },
                handler=self.list_dir,
            ),
            Tool(
                name="getFileType",
                description="获取指定文件的文件类型（目录 / 扩展名 / MIME 类型）",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件或目录路径"}},
                    "required": ["path"],
                },
                handler=self.get_file_type,
            ),
            Tool(
                name="readFile",
                description="获取指定文件的内容，仅支持纯文本格式（如 .txt/.py/.md/.json/.ini 等）",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "文件路径"}},
                    "required": ["path"],
                },
                handler=self.read_file,
            ),
            Tool(
                name="createFile",
                description="根据指定的目录和文件名创建新文件，可写入初始纯文本内容",
                parameters={
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "目标目录"},
                        "filename": {"type": "string", "description": "新文件名"},
                        "content": {"type": "string", "description": "文件初始内容，默认为空"},
                    },
                    "required": ["directory", "filename"],
                },
                handler=self.create_file,
            ),
            Tool(
                name="editFile",
                description="编辑指定文件的内容（整体覆盖写入），仅支持纯文本格式",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "新的完整内容"},
                    },
                    "required": ["path", "content"],
                },
                handler=self.edit_file,
            ),
            Tool(
                name="createDir",
                description="创建新目录（支持一次创建多级目录）",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "新目录路径"}},
                    "required": ["path"],
                },
                handler=self.create_dir,
            ),
            Tool(
                name="removeDir",
                description="删除指定目录，支持删除非空目录（递归删除目录下所有内容，谨慎使用）",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "要删除的目录路径"}},
                    "required": ["path"],
                },
                handler=self.remove_dir,
            ),
            Tool(
                name="removeFile",
                description="删除指定的文件",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "要删除的文件路径"}},
                    "required": ["path"],
                },
                handler=self.remove_file,
            ),
            Tool(
                name="rename",
                description="重命名文件或目录（也可用于移动到同工作目录内的其他位置）",
                parameters={
                    "type": "object",
                    "properties": {
                        "src": {"type": "string", "description": "原路径"},
                        "dst": {"type": "string", "description": "新路径"},
                    },
                    "required": ["src", "dst"],
                },
                handler=self.rename,
            ),
        ]

    def get_cwd(self) -> str:
        return str(self.workspace.root)

    def list_dir(self, path: str = ".") -> str:
        target = self.workspace.resolve(path)
        if not target.is_dir():
            raise NotADirectoryError(f"目录不存在: {path}")
        entries = sorted(os.listdir(target))
        if not entries:
            return "(空目录)"
        return "\n".join(
            f"[{'目录' if (target / name).is_dir() else '文件'}] {name}" for name in entries
        )

    def get_file_type(self, path: str) -> str:
        target = self.workspace.resolve(path)
        if target.is_dir():
            return "目录"
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        ext = target.suffix.lower().lstrip(".") or "无扩展名"
        mime = mimetypes.guess_type(str(target))[0] or "未知"
        return f"扩展名: .{ext} / MIME 类型: {mime}"

    def read_file(self, path: str) -> str:
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return "错误: 该文件不是纯文本格式，仅支持纯文本文件"

    def create_file(self, directory: str, filename: str, content: str = "") -> str:
        target = self.workspace.resolve(Path(directory) / filename)
        if target.exists():
            raise FileExistsError(f"文件已存在: {target}")
        target.write_text(content, encoding="utf-8")
        return f"已创建文件: {target}"

    def edit_file(self, path: str, content: str) -> str:
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        target.write_text(content, encoding="utf-8")
        return f"已更新文件: {target}"

    def create_dir(self, path: str) -> str:
        target = self.workspace.resolve(path)
        if target.exists():
            raise FileExistsError(f"目录已存在: {target}")
        target.mkdir(parents=True)
        return f"已创建目录: {target}"

    def remove_dir(self, path: str) -> str:
        target = self.workspace.resolve(path)
        if target == self.workspace.root:
            raise PermissionError("不能删除工作目录本身")
        if not target.is_dir():
            raise NotADirectoryError(f"目录不存在: {path}")
        shutil.rmtree(target)
        return f"已删除目录: {target}"

    def remove_file(self, path: str) -> str:
        target = self.workspace.resolve(path)
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {path}")
        target.unlink()
        return f"已删除文件: {target}"

    def rename(self, src: str, dst: str) -> str:
        source = self.workspace.resolve(src)
        destination = self.workspace.resolve(dst)
        if not source.exists():
            raise FileNotFoundError(f"{src} 不存在")
        if destination.exists():
            raise FileExistsError(f"{dst} 已存在")
        shutil.move(str(source), str(destination))
        return f"已重命名: {src} -> {dst}"
