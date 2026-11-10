"""loadSkill 工具：把 Skill 的完整内容按需加载进对话上下文。

设计原则：
- 工具本身只做参数适配，发现/路径解析/目录沙箱都在 core/skills.py 中实现
- 错误通过 SkillError 抛出，由 ToolRegistry 统一转换为 ok=False 的工具结果
  （与 RunCliToolset / WebSearchToolset 的错误处理风格保持一致）
"""

from __future__ import annotations

from ..skills import SkillCatalog
from .base import Tool


class SkillToolset:
    """把 SkillCatalog 封装为可供 LLM 调用的 loadSkill 工具。"""

    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog

    def tools(self) -> list[Tool]:
        return [
            Tool(
                name="loadSkill",
                description=(
                    "加载某个技能（Skill）的完整内容。只传 name 时返回该技能 SKILL.md 全文；"
                    "同时传 filename 时返回该技能目录下的附加文件内容（不会重复返回 SKILL.md）。"
                    "当任务与系统提示中列出的技能相关时，先调用本工具读取详细步骤再执行。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "技能名称，例如 '撰写通知'",
                        },
                        "filename": {
                            "type": "string",
                            "description": "可选。需要加载的附加文件：支持相对于技能目录的路径、"
                            "项目相对路径、绝对路径，或只给文件名（在技能目录内递归查找）。"
                            "省略时加载 SKILL.md 全文。",
                        },
                    },
                    "required": ["name"],
                },
                handler=self.load_skill,
            ),
            Tool(
                name="createSkill",
                description=(
                    "根据用户的自然语言要求创建一个新技能（Skill）："
                    "在项目 skills 目录下建立以 name 命名的子目录并写入 SKILL.md。"
                    "skillContext 必须符合 Agent Skills 规范：以 --- 包裹的 YAML Frontmatter 开头，"
                    "包含与 name 一致的 name 字段和非空 description 字段，其后是正文（使用步骤、规则、注意事项）。"
                    "同名技能已存在时会拒绝创建。references/、scripts/ 等附加目录和文件请再用文件操作工具补充。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "技能名称，也是子目录名：只能由小写字母、数字和连字符组成，"
                            "例如 'meeting-summary'",
                        },
                        "skillContext": {
                            "type": "string",
                            "description": "需要写入 SKILL.md 的完整内容（含 YAML Frontmatter 与正文）",
                        },
                    },
                    "required": ["name", "skillContext"],
                },
                handler=self.create_skill,
            ),
        ]

    def load_skill(self, name: str, filename: str | None = None) -> str:
        return self._catalog.load(name, filename)

    def create_skill(self, name: str, skillContext: str) -> str:
        return self._catalog.create(name, skillContext)
