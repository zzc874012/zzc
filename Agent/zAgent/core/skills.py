"""Agent Skills 的发现与按需加载。

目录约定（项目根目录 = 程序启动时的工作目录）::

    <项目根目录>/skills/<技能>/SKILL.md
    <项目根目录>/skills/<技能>/...其他附加文件

设计原则：
- 初始化只解析 SKILL.md 顶部 YAML Frontmatter 的 name / description
- SKILL.md 正文与附加文件只有在 LLM 调用 loadSkill 时才被读取，避免占用上下文
- 所有文件读取都限制在该技能目录内部：相对路径依次按「技能目录 -> 项目根目录」解析，
  绝对路径同样做目录归属校验，防止路径穿越读取其他文件
- Frontmatter 使用宽松解析：兼容 `key:value`（冒号后无空格）与全角引号，
  因此不依赖 PyYAML
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SKILLS_DIRNAME = "skills"
SKILL_FILENAME = "SKILL.md"
_FRONTMATTER_DELIMITER = "---"
_QUOTE_CHARS = "\"'“”‘’"
_METADATA_MAX_LINES = 100  # Frontmatter 最多扫描行数，避免把正文当 YAML 解析
# Skill 名称规范：小写字母、数字、连字符（如 meeting-summary）
_NAME_PATTERN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


class SkillError(RuntimeError):
    """技能不存在或文件不可读取时抛出，由 ToolRegistry 转换为 ok=False 的工具结果。"""


@dataclass(frozen=True)
class Skill:
    """一个技能：只保存元数据与目录，正文按需读取。"""

    name: str
    description: str
    directory: Path

    @property
    def skill_file(self) -> Path:
        return self.directory / SKILL_FILENAME


class SkillCatalog:
    """技能目录：初始化时发现元数据，运行期按需加载内容。"""

    def __init__(
        self,
        skills: Iterable[Skill] = (),
        *,
        project_root: str | os.PathLike[str] | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path.cwd().resolve()
        )
        self._skills: dict[str, Skill] = {}
        for skill in skills:
            # 同名技能保留扫描顺序靠前的那个
            self._skills.setdefault(skill.name, skill)

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    @classmethod
    def discover(cls, project_root: str | os.PathLike[str] | None = None) -> "SkillCatalog":
        """扫描 <项目根目录>/skills 下各子目录的 SKILL.md 元数据。

        没有 skills 目录、缺少 SKILL.md、或缺少 name/description 的子目录都会被跳过，
        保证启动过程不会因单个技能不完整而失败。
        """
        root = (
            Path(project_root).expanduser().resolve()
            if project_root
            else Path.cwd().resolve()
        )
        skills: list[Skill] = []
        skills_dir = root / SKILLS_DIRNAME
        if skills_dir.is_dir():
            for entry in sorted(skills_dir.iterdir()):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                skill = _load_skill_metadata(entry)
                if skill is not None:
                    skills.append(skill)
        return cls(skills, project_root=root)

    # ------------------------------------------------------------------
    # 查询与 System Prompt 片段
    # ------------------------------------------------------------------
    @property
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    def get(self, name: str) -> Skill | None:
        return self._skills.get((name or "").strip())

    def system_prompt_section(self) -> str:
        """生成注入 System Prompt 的技能清单；没有技能时返回空字符串。"""
        if not self._skills:
            return ""
        lines = ["You have access to the following skills:"]
        for skill in self._skills.values():
            lines.append(f"- '{skill.name}': {skill.description}")
        lines.append("")
        lines.append(
            "Use the 'loadSkill' tool to load the full instructions of a skill when needed."
        )
        lines.append(
            "You can also load additional files referenced by a skill "
            "by passing the 'filename' parameter to 'loadSkill'."
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------
    def create(self, name: str, content: str) -> str:
        """在 <项目根目录>/skills/<name>/ 下创建新技能的 SKILL.md。

        - name 必须是小写字母、数字、连字符格式
        - content 必须是符合 Agent Skills 规范的完整 SKILL.md 文本
          （以 --- 包裹的 YAML Frontmatter 开头，含 name / description，后接正文）
        - 同名技能或目录已存在时拒绝创建，避免意外覆盖
        创建成功后立即注册进目录，loadSkill 本次会话即可读取。
        """
        name = (name or "").strip()
        if not _NAME_PATTERN.fullmatch(name):
            raise SkillError(
                f"非法技能名称: {name!r}。"
                "名称只能由小写字母、数字和连字符组成（如 'meeting-summary'）。"
            )

        directory = (self.project_root / SKILLS_DIRNAME / name).resolve()
        if name in self._skills or directory.exists():
            raise SkillError(
                f"技能 '{name}' 已存在（目录: {directory}），不会覆盖。\n"
                "如需调整，请用文件工具直接修改其中的文件，或换一个名称。"
            )

        fields = _parse_frontmatter_text(content or "")
        fm_name = fields.get("name", "").strip()
        fm_description = fields.get("description", "").strip()
        if not fm_name or not fm_description:
            raise SkillError(
                "skillContext 不符合 Agent Skills 规范：必须以 --- 包裹的 YAML "
                "Frontmatter 开头，且包含非空的 name 和 description 字段，例如：\n"
                "---\n"
                f"name: {name}\n"
                "description: <一句话说明该技能能做什么>\n"
                "---\n"
                "<正文：使用步骤、规则、注意事项...>"
            )
        if fm_name != name:
            raise SkillError(
                f"Frontmatter 中的 name（'{fm_name}'）必须与工具参数 name（'{name}'）一致。"
            )

        directory.mkdir(parents=True)
        skill_file = directory / SKILL_FILENAME
        skill_file.write_text(content, encoding="utf-8")
        self._skills[name] = Skill(name=name, description=fm_description, directory=directory)
        return (
            f"已创建技能 '{name}': {skill_file}\n"
            "如需 references/、scripts/ 等目录或其他辅助文件，请继续使用文件操作工具创建。"
        )

    # ------------------------------------------------------------------
    # 按需加载
    # ------------------------------------------------------------------
    def load(self, name: str, filename: str | None = None) -> str:
        """返回技能正文或附加文件内容。

        只给 name 时返回 SKILL.md 全文；给出 filename 时只读取该附加文件，
        不会再附带 SKILL.md。
        """
        skill = self.get(name)
        if skill is None:
            raise SkillError(self._unknown_skill_message(name))

        target = (filename or "").strip()
        if not target:
            return self._read_text(skill.skill_file, SKILL_FILENAME)
        return self._read_extra_file(skill, target)

    def _read_extra_file(self, skill: Skill, filename: str) -> str:
        raw = Path(filename).expanduser()

        # 1. 绝对路径：直接校验目录归属
        if raw.is_absolute():
            return self._read_checked(skill, raw, filename)

        # 2. 技能目录相对路径
        direct = skill.directory / raw
        if direct.exists():
            return self._read_checked(skill, direct, filename)

        # 3. 项目相对路径（例如 skills/notice/references/xxx.md）
        project_relative = self.project_root / raw
        if project_relative.exists():
            return self._read_checked(skill, project_relative, filename)

        # 4. 只给文件名：在该技能目录内递归查找
        if len(raw.parts) == 1:
            return self._read_recursive_match(skill, raw.name)

        raise SkillError(self._not_found_message(skill, filename))

    def _read_recursive_match(self, skill: Skill, filename: str) -> str:
        matches: list[Path] = []
        for path in skill.directory.rglob("*"):
            resolved = _resolve_safely(path)
            if resolved is None or not _is_inside(skill.directory, resolved):
                continue
            if resolved.is_file() and path.name.lower() == filename.lower():
                matches.append(path)
        matches.sort()

        if not matches:
            raise SkillError(self._not_found_message(skill, filename))
        if len(matches) > 1:
            candidates = "\n".join(
                f"- {path.relative_to(skill.directory).as_posix()}" for path in matches
            )
            raise SkillError(
                f"技能 '{skill.name}' 目录内存在 {len(matches)} 个名为 '{filename}' 的文件，"
                f"请从以下候选路径中选择一个，并用 filename 参数重新调用 loadSkill:\n"
                f"{candidates}"
            )
        return self._read_text(matches[0], filename)

    def _read_checked(self, skill: Skill, path: Path, display: str) -> str:
        resolved = _resolve_safely(path)
        if resolved is None or not _is_inside(skill.directory, resolved):
            raise SkillError(
                f"拒绝读取: {display} 超出技能 '{skill.name}' 的目录范围。\n"
                f"只允许读取该技能目录内的文件: {skill.directory}"
            )
        return self._read_text(resolved, display)

    @staticmethod
    def _read_text(path: Path, display: str) -> str:
        if path.is_dir():
            raise SkillError(f"'{display}' 是目录，无法作为文件读取")
        if not path.is_file():
            raise SkillError(f"文件不存在: {display}")
        try:
            return path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise SkillError(f"读取文件失败: {display} ({exc})") from exc

    @staticmethod
    def _not_found_message(skill: Skill, filename: str) -> str:
        return (
            f"在技能 '{skill.name}' 目录中找不到文件: {filename}\n"
            f"相对路径按技能目录或项目根目录解析，也可只提供文件名进行递归查找。\n"
            f"只允许读取该技能目录内的文件: {skill.directory}"
        )

    def _unknown_skill_message(self, name: str) -> str:
        available = "、".join(f"'{skill.name}'" for skill in self._skills.values())
        return f"未知技能: {name}。可用技能: {available or '（无）'}"


# ----------------------------------------------------------------------
# Frontmatter / 路径工具
# ----------------------------------------------------------------------
def _load_skill_metadata(directory: Path) -> Skill | None:
    """读取 SKILL.md 的 Frontmatter；缺少必需字段时返回 None。"""
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        return None
    fields = _read_frontmatter(skill_file)
    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name or not description:
        return None
    return Skill(name=name, description=description, directory=directory.resolve())


def _read_frontmatter(path: Path) -> dict[str, str]:
    """解析文件顶部 `---` 包裹的 Frontmatter，只读需要的行数。"""
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            lines = [handle.readline() for _ in range(_METADATA_MAX_LINES + 1)]
    except (OSError, UnicodeDecodeError):
        return {}
    return _parse_frontmatter_lines(lines)


def _parse_frontmatter_text(text: str) -> dict[str, str]:
    """解析一段文本顶部的 Frontmatter（用于创建前的规范校验）。"""
    return _parse_frontmatter_lines(text.lstrip("﻿").splitlines(keepends=True))


def _parse_frontmatter_lines(lines: Iterable[str]) -> dict[str, str]:
    """宽松解析 `---` 包裹的 Frontmatter：兼容 key:value 无空格与全角引号。"""
    iterator = iter(lines)
    if next(iterator, "").strip() != _FRONTMATTER_DELIMITER:
        return {}
    fields: dict[str, str] = {}
    for line in iterator:
        stripped = line.strip()
        if stripped == _FRONTMATTER_DELIMITER:
            break
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        if key:
            fields[key] = _unquote(value.strip())
    return fields


def _unquote(value: str) -> str:
    """去掉 ASCII / 全角引号，兼容 `description:“xxx”` 这种写法。"""
    if len(value) >= 2 and value[0] in _QUOTE_CHARS and value[-1] in _QUOTE_CHARS:
        return value[1:-1].strip()
    return value


def _resolve_safely(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_inside(root: Path, target: Path) -> bool:
    return target == root or root in target.parents
