"""Runtime configuration for the Pet Hospital MCP service.

All values come from environment variables so the same code runs in dev,
tests, and production without code changes. Sensible defaults are provided
for local teaching scenarios.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env_str(name: str, default: str) -> str:
    """Read a string env var, falling back to default if unset/empty."""
    value = os.environ.get(name, "").strip()
    return value if value else default


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to default on parse failure."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to default on parse failure."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration.

    Attributes:
        mcp_host: Bind address for the MCP Streamable HTTP server.
        mcp_port: Bind port for the MCP Streamable HTTP server.
        mcp_path: URL path the MCP endpoint is mounted on.
        pet_hospital_base_url: Base URL of the upstream Go REST API.
        backend_timeout_seconds: Per-request HTTP timeout to the Go backend.
        backend_retries: How many times to retry transient backend failures.
        backend_retry_backoff_seconds: Base sleep between retry attempts.
    """

    mcp_host: str
    mcp_port: int
    mcp_path: str
    pet_hospital_base_url: str
    backend_timeout_seconds: float
    backend_retries: int
    backend_retry_backoff_seconds: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build a Settings instance from the current environment.

    Cached so repeated calls are cheap; tests can call
    `get_settings.cache_clear()` after monkeypatching env vars.
    """
    return Settings(
        mcp_host=_env_str("MCP_HOST", "127.0.0.1"),
        mcp_port=_env_int("MCP_PORT", 8000),
        mcp_path=_env_str("MCP_PATH", "/mcp"),
        pet_hospital_base_url=_env_str(
            "PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080"
        ).rstrip("/"),
        backend_timeout_seconds=_env_float("BACKEND_TIMEOUT_SECONDS", 5.0),
        backend_retries=_env_int("BACKEND_RETRIES", 2),
        backend_retry_backoff_seconds=_env_float(
            "BACKEND_RETRY_BACKOFF_SECONDS", 0.25
        ),
    )


# Allowed enum values, mirrored from the Go backend's model package.
# Kept here so input validation stays aligned with the real backend
# without needing to query it at startup.
ALLOWED_SPECIES: frozenset[str] = frozenset(
    {"犬", "猫", "兔", "鸟", "仓鼠", "爬宠", "其他"}
)
ALLOWED_STATUS: frozenset[str] = frozenset(
    {"待就诊", "就诊中", "住院中", "已康复", "慢性病随访"}
)
ALLOWED_SORT_BY: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "ownerName",
        "species",
        "doctor",
        "disease",
        "status",
        "totalCost",
        "visitCount",
        "createdAt",
        "updatedAt",
    }
)
ALLOWED_ORDER: frozenset[str] = frozenset({"asc", "desc"})
