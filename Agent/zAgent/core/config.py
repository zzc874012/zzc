"""配置层：负责读取与校验运行参数，不依赖任何界面框架。

新增 [compact] / [session] / [agents_md] 可选段；所有硬编码参数外部化，
缺失时用 Python 常量兜底。
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# ---- 默认值常量（config.ini 缺失字段时兜底） ----
DEFAULT_MAX_TOOL_ROUNDS = 8
DEFAULT_REQUEST_TIMEOUT = 120.0
DEFAULT_CONTEXT_WINDOW = 128 * 1024
DEFAULT_COMPACT_THRESHOLD = 0.80         # 80% 窗口时警告
DEFAULT_COMPACT_SUMMARY_TOKENS = 5000   # 压缩摘要目标 token 数
DEFAULT_COMPACT_PROMPT_PATH = ""         # 空则使用内置 PROMPT；给路径可覆盖
DEFAULT_SESSION_STORE_DIR = ".agents/sessions"
DEFAULT_SESSION_MAX = 50                 # 最多保留多少历史会话
DEFAULT_AGENTS_MD_PATH = "AGENTS.md"     # 相对项目根目录

CONFIG_FILENAME = "config.ini"


# ---------------------------------------------------------------------------
# 配置类（全部 frozen，不可变；改动须重新 load）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class AgentSettings:
    workspace: Path
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT
    # None 表示使用模型默认窗口 128k；显式传入则用于阈值判断 / TUI 显示
    context_window_tokens: int | None = None


@dataclass(frozen=True)
class SearchSettings:
    baidu_api_key: str = ""


@dataclass(frozen=True)
class CompactSettings:
    """上下文压缩相关参数。"""
    threshold: float = DEFAULT_COMPACT_THRESHOLD
    summary_target_tokens: int = DEFAULT_COMPACT_SUMMARY_TOKENS
    # 空字符串用内置 PROMPT；否则指向一个 prompt 模板文件（可含 {project_root} 占位）
    prompt_file: str = DEFAULT_COMPACT_PROMPT_PATH


@dataclass(frozen=True)
class SessionSettings:
    """会话持久化相关参数。"""
    # 存储目录：相对项目根目录；空字符串则不做自动保存
    store_dir: str = DEFAULT_SESSION_STORE_DIR
    max_sessions: int = DEFAULT_SESSION_MAX


@dataclass(frozen=True)
class AgentsMDSettings:
    """AGENTS.md 自动加载参数。"""
    # 相对项目根目录的文件名；空字符串则禁用自动加载
    path: str = DEFAULT_AGENTS_MD_PATH


@dataclass(frozen=True)
class AppConfig:
    llm: LLMSettings
    agent: AgentSettings
    search: SearchSettings = SearchSettings()
    compact: CompactSettings = CompactSettings()
    session: SessionSettings = SessionSettings()
    agents_md: AgentsMDSettings = AgentsMDSettings()


class ConfigError(RuntimeError):
    """配置缺失或非法时抛出。"""


# ---------------------------------------------------------------------------
# 查找与加载
# ---------------------------------------------------------------------------
def default_config_path() -> Path:
    """配置查找顺序：环境变量 ZAGENT_CONFIG -> 当前工作目录 config.ini -> 默认模板位置。"""
    env_path = os.environ.get("ZAGENT_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return Path.cwd() / CONFIG_FILENAME


def config_exists(path: Path | None = None) -> bool:
    path = path or default_config_path()
    return path.is_file()


def default_config_content() -> str:
    """当用户没有 config.ini 时，生成一个带注释的模板。"""
    return (
        "# zAgent 配置模板 —— 修改后重启生效\n"
        "#\n"
        "# 说明：\n"
        "# [llm]    必需：LLM 接口信息；api_key / base_url / model 三项必填\n"
        "# [agent]  必需：工作目录 / 最大工具轮数；workspace 会被 load_config 的参数覆盖\n"
        "# [search] 可选：百度搜索 API Key；缺失也能启动，搜索工具会返回配置缺失提示\n"
        "# [compact] 可选：上下文压缩阈值与摘要目标\n"
        "# [session] 可选：会话持久化目录与历史上限\n"
        "# [agents_md] 可选：项目内 AGENTS.md 的相对路径（首次注入一次，后续复用）\n"
        "# ==============================================================\n"
        "\n"
        "[llm]\n"
        "api_key = sk-replace-me\n"
        "base_url = https://api.siliconflow.cn/v1\n"
        "model = tencent/Hy4-preview\n"
        "\n"
        "[agent]\n"
        "workspace = c:/Users/Lenovo/Desktop/LLM\n"
        "max_tool_rounds = 8\n"
        "request_timeout = 120\n"
        "# context_window_tokens = 131072   # 注释掉则默认 128k\n"
        "\n"
        "[search]\n"
        "# baidu_api_key = \n"
        "\n"
        "[compact]\n"
        "threshold = 0.80\n"
        "summary_target_tokens = 5000\n"
        "# prompt_file =                    # 注释掉则用内置压缩 prompt\n"
        "\n"
        "[session]\n"
        "store_dir = .agents/sessions\n"
        "max_sessions = 50\n"
        "\n"
        "[agents_md]\n"
        "path = AGENTS.md\n"
    )


def load_config(
    *,
    config_path: str | os.PathLike[str] | None = None,
    workspace: str | os.PathLike[str] | None = None,
    max_tool_rounds: int | None = None,
) -> AppConfig:
    """加载配置并返回不可变的 AppConfig。

    如果 config.ini 不存在，抛出 ConfigError；由调用方决定是否先写入模板再提示用户重启。
    """
    path = Path(config_path).expanduser() if config_path else default_config_path()
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")

    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise ConfigError(f"无法读取配置文件: {path}")
    if not parser.has_section("llm"):
        raise ConfigError(f"配置文件缺少 [llm] 段: {path}")

    section = parser["llm"]
    api_key = section.get("api_key", "").strip()
    base_url = section.get("base_url", "").strip()
    model = section.get("model", "").strip()
    if not api_key:
        raise ConfigError("[llm] 缺少 api_key")
    if not base_url:
        raise ConfigError("[llm] 缺少 base_url")
    if not model:
        raise ConfigError("[llm] 缺少 model")

    root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    if not root.is_dir():
        raise ConfigError(f"工作目录不存在: {root}")

    rounds = max_tool_rounds
    if rounds is None:
        rounds = parser.getint(
            "agent", "max_tool_rounds",
            fallback=DEFAULT_MAX_TOOL_ROUNDS,
        )
    if rounds < 1:
        raise ConfigError("max_tool_rounds 必须为正整数")

    request_timeout = parser.getfloat(
        "agent", "request_timeout", fallback=DEFAULT_REQUEST_TIMEOUT,
    )
    context_window = (
        parser.getint("agent", "context_window_tokens")
        if parser.has_section("agent") and parser.has_option("agent", "context_window_tokens")
        else None
    )

    # [search] 段可选
    if parser.has_section("search"):
        search_key = parser["search"].get("baidu_api_key", "").strip()
    else:
        search_key = ""

    # [compact] 段可选
    threshold = parser.getfloat("compact", "threshold", fallback=DEFAULT_COMPACT_THRESHOLD)
    if not 0.05 <= threshold <= 1.0:
        raise ConfigError("compact.threshold 必须在 0.05 ~ 1.0 之间")
    summary_target = parser.getint(
        "compact", "summary_target_tokens", fallback=DEFAULT_COMPACT_SUMMARY_TOKENS,
    )
    prompt_file = parser.get(
        "compact", "prompt_file", fallback=DEFAULT_COMPACT_PROMPT_PATH,
    ).strip()

    # [session] 段可选
    session_store_dir = parser.get(
        "session", "store_dir", fallback=DEFAULT_SESSION_STORE_DIR,
    ).strip()
    session_max = parser.getint(
        "session", "max_sessions", fallback=DEFAULT_SESSION_MAX,
    )

    # [agents_md] 段可选
    agents_md_path = parser.get(
        "agents_md", "path", fallback=DEFAULT_AGENTS_MD_PATH,
    ).strip()

    return AppConfig(
        llm=LLMSettings(api_key=api_key, base_url=base_url, model=model),
        agent=AgentSettings(
            workspace=root,
            max_tool_rounds=rounds,
            request_timeout=request_timeout,
            context_window_tokens=context_window,
        ),
        search=SearchSettings(baidu_api_key=search_key),
        compact=CompactSettings(
            threshold=threshold,
            summary_target_tokens=summary_target,
            prompt_file=prompt_file,
        ),
        session=SessionSettings(
            store_dir=session_store_dir,
            max_sessions=session_max,
        ),
        agents_md=AgentsMDSettings(path=agents_md_path),
    )
