"""Tests for backend timeout and connection failures.

httpx.TimeoutException → BACKEND_TIMEOUT
httpx.ConnectError      → BACKEND_UNAVAILABLE
other httpx.HTTPError   → BACKEND_UNAVAILABLE (defensive)
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pet_hospital_mcp import errors
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools import ListPetsInput, ListPetsResult, list_pets
from conftest import make_success_response


@pytest.mark.asyncio
async def test_timeout_yields_backend_timeout(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated read timeout", request=request)

    client = make_mock_client(handler, timeout=0.5)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_TIMEOUT
    assert "timed out" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_connect_error_yields_backend_unavailable(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_UNAVAILABLE
    assert "unavailable" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_read_error_yields_backend_unavailable(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("read failed", request=request)

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_UNAVAILABLE


@pytest.mark.asyncio
async def test_transient_failure_is_retried_then_succeeds():
    """retries=2 means up to 3 attempts total; attempt #3 returns 200."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError(
                f"temporary failure #{attempts['n']}", request=request
            )
        return httpx.Response(
            200, json=make_success_response(items=[], total=0)
        )

    transport = httpx.MockTransport(handler)
    client = PetHospitalClient(
        base_url="http://pet-hospital.test",
        timeout_seconds=5.0,
        retries=2,
        retry_backoff_seconds=0.0,
        transport=transport,
    )

    result = await list_pets(ListPetsInput(), client=client)
    assert isinstance(result, ListPetsResult)
    assert attempts["n"] == 3


@pytest.mark.asyncio
async def test_retry_budget_is_finite():
    """When every attempt fails, total attempts == retries + 1."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        raise httpx.ConnectError("down", request=request)

    transport = httpx.MockTransport(handler)
    client = PetHospitalClient(
        base_url="http://pet-hospital.test",
        timeout_seconds=5.0,
        retries=2,
        retry_backoff_seconds=0.0,
        transport=transport,
    )

    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_UNAVAILABLE
    assert attempts["n"] == 3
