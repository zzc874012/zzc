"""Tests for backend 4xx/5xx handling.

The MCP service must translate Go backend HTTP failures into the
`BACKEND_API_ERROR` structured error code, surface the backend's
message if available, and never leak stack traces.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pet_hospital_mcp import errors
from pet_hospital_mcp.tools import ListPetsInput, list_pets
from conftest import make_error_envelope


@pytest.mark.asyncio
async def test_backend_400_returns_backend_api_error(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json=make_error_envelope(code=400, message="invalid species"),
        )

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(species="犬"), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_API_ERROR
    assert "HTTP 400" in payload["error"]["message"]
    assert "invalid species" in payload["error"]["message"]
    assert payload["error"]["details"]["status_code"] == 400


@pytest.mark.asyncio
async def test_backend_404_returns_backend_api_error(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=make_error_envelope(code=404, message="not found"))

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_API_ERROR
    assert payload["error"]["details"]["status_code"] == 404


@pytest.mark.asyncio
async def test_backend_500_returns_backend_api_error(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json=make_error_envelope(code=500, message="internal server error"),
        )

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_API_ERROR
    assert payload["error"]["details"]["status_code"] == 500
    assert "internal server error" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_backend_4xx_with_non_json_body_still_yields_backend_api_error(
    make_mock_client,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, content=b"<html>error</html>")

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    # 4xx with non-JSON is still a backend API error (not invalid response)
    assert payload["error"]["code"] == errors.BACKEND_API_ERROR
    assert payload["error"]["details"]["status_code"] == 422


@pytest.mark.asyncio
async def test_backend_502_returns_backend_api_error(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json=make_error_envelope(code=502, message="bad gateway"))

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_API_ERROR
    assert payload["error"]["details"]["status_code"] == 502
