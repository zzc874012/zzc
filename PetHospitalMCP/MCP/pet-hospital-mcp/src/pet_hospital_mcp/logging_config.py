"""Structured JSON logging with recursive PII masking.

The spec requires every log line to carry at least:
    timestamp, tool_name, params, status, duration_ms

and to recursively scrub these fields (snake_case included):
    ownerPhone, ownerAddr, chipNo

We use the standard library `logging` plus a JSON formatter so no extra
dependency is needed. Tests assert on parsed log records rather than
regex matching strings.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

# Field names that must be masked wherever they appear (case-insensitive),
# including nested keys. Both camelCase and snake_case variants are listed.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "ownerphone",
        "owner_phone",
        "owneraddr",
        "owner_addr",
        "chipno",
        "chip_no",
    }
)

_MASK = "***REDACTED***"


def mask_value(value: Any) -> Any:
    """Replace any sensitive value with the redaction token."""
    return _MASK


def _mask_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        lower = str(key).lower()
        if lower in SENSITIVE_KEYS:
            out[key] = _MASK
            continue
        if isinstance(value, Mapping):
            out[key] = _mask_mapping(value)
        elif isinstance(value, list):
            out[key] = _mask_list(value)
        else:
            out[key] = value
    return out


def _mask_list(items: Iterable[Any]) -> list[Any]:
    masked: list[Any] = []
    for item in items:
        if isinstance(item, Mapping):
            masked.append(_mask_mapping(item))
        elif isinstance(item, list):
            masked.append(_mask_list(item))
        else:
            masked.append(item)
    return masked


def redact(params: Any) -> Any:
    """Recursively redact sensitive fields in any nested structure."""
    if isinstance(params, Mapping):
        return _mask_mapping(params)
    if isinstance(params, list):
        return _mask_list(params)
    return params


class JsonFormatter(logging.Formatter):
    """Emit each record as a single JSON object line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Pull optional structured fields attached via `extra=`.
        for field in (
            "tool_name",
            "params",
            "status",
            "duration_ms",
            "error_code",
            "error_message",
        ):
            value = getattr(record, field, None)
            if value is not None:
                if field == "params":
                    value = redact(value)
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Safe to call once at startup; subsequent calls replace handlers so
    we never get duplicate lines.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


class ToolCallLogger:
    """Context manager that emits a structured log line per tool call.

    Usage:
        with ToolCallLogger("list_pets", params) as log:
            result = ...
            log.ok(result)
    """

    def __init__(self, tool_name: str, params: Mapping[str, Any]) -> None:
        self.tool_name = tool_name
        self.params: dict[str, Any] = dict(params)
        self.start = time.perf_counter()
        self._status = "ok"
        self._extra: dict[str, Any] = {}

    def __enter__(self) -> "ToolCallLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        duration_ms = int((time.perf_counter() - self.start) * 1000)
        if exc_val is not None:
            self._status = "error"
        self._emit(duration_ms)

    def ok(self, result: Any = None) -> None:
        self._status = "ok"
        if result is not None:
            self._extra["result_summary"] = _summarize(result)

    def fail(self, error_code: str, error_message: str) -> None:
        self._status = "error"
        self._extra["error_code"] = error_code
        self._extra["error_message"] = error_message

    def _emit(self, duration_ms: int) -> None:
        logger = logging.getLogger("pet_hospital_mcp.tool")
        logger.info(
            "tool_call",
            extra={
                "tool_name": self.tool_name,
                "params": self.params,
                "status": self._status,
                "duration_ms": duration_ms,
                **self._extra,
            },
        )


def _summarize(result: Any) -> Any:
    """Produce a compact summary for the result log field."""
    if isinstance(result, dict):
        # Strip item payloads to avoid leaking data; keep counts.
        summary: dict[str, Any] = {}
        for key, value in result.items():
            if key == "items" and isinstance(value, list):
                summary[key] = f"<{len(value)} items>"
            else:
                summary[key] = value
        return summary
    return result
