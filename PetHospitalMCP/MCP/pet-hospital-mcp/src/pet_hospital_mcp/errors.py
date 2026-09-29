"""Structured error model for the Pet Hospital MCP service.

All tool failures surface to the model through a single shaped payload:

    {
      "error": {
        "code": "ERROR_CODE",
        "message": "human readable explanation",
        "details": {... optional context ...}
      }
    }

Six error codes are defined to cover every failure mode the spec calls out.
`ToolError` from the SDK 2.x is used so `result.is_error` is True and the
payload is forwarded to the model for self-correction.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------

VALIDATION_ERROR = "VALIDATION_ERROR"
"""Input failed schema / enum / range validation."""

BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
"""The Go backend did not respond in time, even after retries."""

BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
"""Could not establish a connection to the Go backend."""

BACKEND_API_ERROR = "BACKEND_API_ERROR"
"""Backend returned a non-2xx HTTP status with a parseable envelope."""

BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
"""Backend response was not valid JSON or did not match the data model."""

INTERNAL_ERROR = "INTERNAL_ERROR"
"""Unexpected failure inside the MCP service itself."""

ALL_ERROR_CODES: frozenset[str] = frozenset(
    {
        VALIDATION_ERROR,
        BACKEND_TIMEOUT,
        BACKEND_UNAVAILABLE,
        BACKEND_API_ERROR,
        BACKEND_INVALID_RESPONSE,
        INTERNAL_ERROR,
    }
)


# ---------------------------------------------------------------------------
# Structured error payload (Pydantic)
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Single shaped error entry.

    `details` is an arbitrary JSON object for context that helps the model
    recover (which field failed validation, what status code came back, etc.).
    Sensitive values are scrubbed before this is built; see `logging_config`.
    """

    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, Any] = Field(
        default_factory=dict, description="Optional structured context."
    )

    model_config = {"extra": "forbid"}


class ErrorEnvelope(BaseModel):
    """Top-level error wrapper, matching the spec."""

    error: ErrorDetail

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_error_payload(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical error dict to embed in a ToolError.

    Returning a plain dict keeps the helper trivially serialisable and lets
    callers (tools, REST client) share the same shape without coupling to
    Pydantic internals. Any non-JSON-native value inside `details` is
    coerced via `str` so error construction itself never raises.
    """
    if code not in ALL_ERROR_CODES:
        # Defensive: never let an unknown code leak; remap to INTERNAL_ERROR.
        code = INTERNAL_ERROR
    safe_details = json.loads(json.dumps(details or {}, default=str))
    envelope = ErrorEnvelope(
        error=ErrorDetail(code=code, message=message, details=safe_details)
    )
    return envelope.model_dump(mode="json")


def raise_tool_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> ToolError:
    """Construct a `ToolError` carrying the structured error payload.

    The payload is JSON-encoded into the ToolError message so the SDK 2.x
    surfaces it via `result.content` while `result.is_error` is True. The
    model reads the payload and can decide how to recover.
    """
    payload = build_error_payload(code, message, details=details)
    return ToolError(json.dumps(payload, ensure_ascii=False))
