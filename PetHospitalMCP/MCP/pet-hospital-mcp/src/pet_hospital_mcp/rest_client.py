"""Async REST client for the upstream Go Pet Hospital API.

The client is the only place that knows about httpx; everything above
this layer (tools, server) only sees the parsed `RestResponse` so we can
map network/HTTP/JSON errors into our six structured error codes without
leaking SDK internals to the MCP caller.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

import httpx

from . import errors


@dataclass
class RestResponse:
    """Normalised result of a backend call.

    On success: `ok=True`, `data` holds the parsed response body.
    On failure: `ok=False`, `error_*` fields describe the structured error.
    """

    ok: bool
    data: dict[str, Any] | None = None
    status_code: int | None = None
    error_code: str = ""
    error_message: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, data: dict[str, Any], status_code: int = 200) -> "RestResponse":
        return cls(ok=True, data=data, status_code=status_code)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> "RestResponse":
        return cls(
            ok=False,
            status_code=status_code,
            error_code=code,
            error_message=message,
            error_details=dict(details or {}),
        )


class PetHospitalClient:
    """Thin async wrapper around httpx for GET /api/v1/pets.

    Transient failures (5xx, connect errors, timeouts) are retried with a
    small backoff. 4xx responses are not retried — they represent a
    definitive client-side decision from the Go service.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._retries = max(0, retries)
        self._backoff = max(0.0, retry_backoff_seconds)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "PetHospitalClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        await self.aclose()

    async def list_pets(self, params: Mapping[str, Any]) -> RestResponse:
        """Call GET /api/v1/pets with the supplied query params.

        `params` must already be validated; this layer just forwards them
        (dropping any None values, as the Go backend treats absent and empty
        the same) and translates failures into `RestResponse.failure`.
        """
        query = {k: v for k, v in params.items() if v is not None}
        attempt = 0
        last_response: RestResponse
        while True:
            attempt += 1
            last_response = await self._call_once(query)
            if last_response.ok:
                return last_response
            # Retry only transient failures, and only if attempts remain.
            if last_response.error_code in (
                errors.BACKEND_TIMEOUT,
                errors.BACKEND_UNAVAILABLE,
                errors.BACKEND_API_ERROR,
            ) and attempt <= self._retries:
                if self._backoff > 0:
                    await asyncio.sleep(self._backoff)
                continue
            return last_response

    async def _call_once(self, query: Mapping[str, Any]) -> RestResponse:
        try:
            resp = await self._client.get("/api/v1/pets", params=query)
        except httpx.TimeoutException as exc:
            return RestResponse.failure(
                errors.BACKEND_TIMEOUT,
                f"Backend timed out: {exc.__class__.__name__}",
                details={"url": f"{self._base_url}/api/v1/pets"},
            )
        except httpx.ConnectError as exc:
            return RestResponse.failure(
                errors.BACKEND_UNAVAILABLE,
                f"Backend unavailable: {exc.__class__.__name__}",
                details={"url": f"{self._base_url}/api/v1/pets"},
            )
        except httpx.HTTPError as exc:
            # Anything else from httpx (read error, protocol error, ...)
            # is treated as an internal transport failure.
            return RestResponse.failure(
                errors.BACKEND_UNAVAILABLE,
                f"HTTP transport error: {exc.__class__.__name__}",
                details={"url": f"{self._base_url}/api/v1/pets"},
            )

        if resp.status_code >= 400:
            body_text = resp.text
            parsed: dict[str, Any] | None = None
            try:
                parsed = resp.json()
            except (json.JSONDecodeError, ValueError):
                pass
            message = (
                f"Backend returned HTTP {resp.status_code}"
                + (f": {parsed.get('message', '')}" if parsed and isinstance(parsed, dict) else "")
            )
            return RestResponse.failure(
                errors.BACKEND_API_ERROR,
                message,
                status_code=resp.status_code,
                details={"body": body_text[:500]},
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return RestResponse.failure(
                errors.BACKEND_INVALID_RESPONSE,
                f"Backend returned non-JSON body: {exc.__class__.__name__}",
                status_code=resp.status_code,
                details={"body": resp.text[:500]},
            )

        if not isinstance(data, dict):
            return RestResponse.failure(
                errors.BACKEND_INVALID_RESPONSE,
                "Backend response was not a JSON object",
                status_code=resp.status_code,
                details={"body_type": type(data).__name__},
            )

        return RestResponse.success(data, status_code=resp.status_code)


# Convenience module-level singleton accessor. Tools that need a shared
# client (e.g. the production server) can call `get_default_client()`; tests
# construct their own `PetHospitalClient` with a MockTransport.
_default_client: PetHospitalClient | None = None


def get_default_client() -> PetHospitalClient | None:
    return _default_client


def set_default_client(client: PetHospitalClient | None) -> None:
    global _default_client
    _default_client = client
