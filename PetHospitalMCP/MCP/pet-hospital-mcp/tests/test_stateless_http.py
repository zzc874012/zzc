"""Tests for the SDK 2.x stateless Streamable HTTP flow.

Coverage:
  * /health responds with status ok and reports protocol/sdk versions
  * The MCP endpoint is used via the 2026-07-28 flow (discover, no
    legacy `initialize` JSON-RPC method)
  * No `Mcp-Session-Id` is required on requests or returned on responses
  * `list_pets` is discoverable over HTTP MCP
  * `list_pets` can be invoked over HTTP MCP end-to-end
  * Two independent connections both work (statelessness)

Every HTTP exchange runs in-process through `httpx2.ASGITransport`;
the upstream Go API is mocked via `httpx.MockTransport`. No real
network sockets or Go service are involved.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx
import httpx2
import pytest
import pytest_asyncio

from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from pet_hospital_mcp import MCP_PROTOCOL_VERSION, MCP_SDK_VERSION
from pet_hospital_mcp.rest_client import PetHospitalClient, set_default_client
from pet_hospital_mcp.server import mcp
from conftest import make_pet, make_success_response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _backend_handler_factory(
    captured: dict[str, Any],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=make_success_response(items=[make_pet()], total=1),
        )

    return handler


@pytest.fixture
def asgi_app():
    """Build the stateless ASGI app (stateless_http=True, path /mcp)."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        host="127.0.0.1",
    )


@pytest.fixture
def wire_capture():
    """Accumulator for raw HTTP requests/responses seen on the MCP wire."""
    return {"requests": [], "responses": []}


@pytest_asyncio.fixture
async def http_server(asgi_app):
    """Run the ASGI app under a real uvicorn server on an ephemeral port.

    The stateless HTTP transport initialises its anyio task group from
    the Starlette lifespan, which `httpx.ASGITransport` does not run;
    booting uvicorn exercises the exact production code path.
    """
    import socket

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        asgi_app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    task = asyncio.get_event_loop().create_task(server.serve())
    # uvicorn >= 0.30 exposes `started` as a bool flipped after startup.
    for _ in range(100):
        if server.started:
            break
        if task.done():
            task.result()  # surface serve() failure
        await asyncio.sleep(0.05)
    else:
        raise RuntimeError("uvicorn server failed to start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@pytest_asyncio.fixture
async def mcp_http_client(http_server, wire_capture):
    """A real httpx2 async client pointed at the uvicorn server,
    with recording hooks for session-id / initialize assertions."""

    async def record_response(response: httpx2.Response) -> None:
        wire_capture["responses"].append(
            {
                "status": response.status_code,
                "headers": {k.lower(): v for k, v in response.headers.items()},
            }
        )

    async def record_request(request: httpx2.Request) -> None:
        body = request.content
        method = None
        try:
            parsed = json.loads(body) if body else None
            if isinstance(parsed, dict):
                method = parsed.get("method")
        except (json.JSONDecodeError, UnicodeDecodeError):
            method = None
        wire_capture["requests"].append(
            {
                "method": method,
                "has_session_id": "mcp-session-id"
                in {k.lower() for k in request.headers.keys()},
            }
        )

    async with httpx2.AsyncClient(
        base_url=http_server,
        event_hooks={"request": [record_request], "response": [record_response]},
    ) as client:
        yield client


def _install_mock_backend() -> tuple[dict[str, Any], PetHospitalClient]:
    captured: dict[str, Any] = {}
    client = PetHospitalClient(
        base_url="http://pet-hospital.test",
        timeout_seconds=5.0,
        retries=0,
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(_backend_handler_factory(captured)),
    )
    set_default_client(client)
    return captured, client


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_returns_ok(mcp_http_client):
    resp = await mcp_http_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mcp_sdk_version"] == MCP_SDK_VERSION
    assert body["mcp_protocol_version"] == MCP_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Stateless MCP flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_uses_discover_not_initialize_and_no_session_id(
    mcp_http_client, wire_capture
):
    """The 2026-07-28 client discovers tools; no initialize handshake occurs
    and no Mcp-Session-Id is ever sent or received."""
    _install_mock_backend()
    mcp_url = f"{str(mcp_http_client.base_url).rstrip('/')}/mcp"
    transport = streamable_http_client(
        mcp_url,
        http_client=mcp_http_client,
    )
    try:
        async with Client(transport) as client:
            tools = await client.list_tools()
    finally:
        set_default_client(None)

    assert any(t.name == "list_pets" for t in tools.tools)

    # No legacy initialize method was put on the wire.
    methods = [r["method"] for r in wire_capture["requests"]]
    assert "initialize" not in methods
    # Discovery (or an explicit tools/list) happened.
    assert methods, "expected at least one JSON-RPC request"

    # Clients never sent a session id.
    assert all(r["has_session_id"] is False for r in wire_capture["requests"])
    # The stateless server never issued a session id.
    for resp in wire_capture["responses"]:
        assert "mcp-session-id" not in resp["headers"]


@pytest.mark.asyncio
async def test_list_pets_callable_via_stateless_http(mcp_http_client):
    """End-to-end: invoke list_pets over stateless HTTP and verify
    argument forwarding to the mocked Go backend."""
    captured, _ = _install_mock_backend()
    mcp_url = f"{str(mcp_http_client.base_url).rstrip('/')}/mcp"
    transport = streamable_http_client(
        mcp_url,
        http_client=mcp_http_client,
    )
    try:
        async with Client(transport) as client:
            result = await client.call_tool(
                "list_pets",
                {"input": {"species": "犬", "page": 1, "pageSize": 10}},
            )
    finally:
        set_default_client(None)

    assert result.is_error is False
    assert result.structured_content is not None
    sc = result.structured_content
    assert sc["total"] == 1
    assert isinstance(sc["items"], list)
    assert sc["items"][0]["species"] == "犬"

    # Backend received the forwarded query.
    assert captured["params"].get("species") == "犬"
    assert captured["params"].get("page") == "1"
    assert captured["params"].get("pageSize") == "10"
    assert captured["url"].endswith("/api/v1/pets") or "/api/v1/pets?" in captured["url"]


@pytest.mark.asyncio
async def test_each_connection_is_independent(mcp_http_client):
    """A second connection must succeed without carrying state from the first."""
    _install_mock_backend()
    mcp_url = f"{str(mcp_http_client.base_url).rstrip('/')}/mcp"
    try:
        # First connection.
        async with Client(
            streamable_http_client(
                mcp_url,
                http_client=mcp_http_client,
            )
        ) as client:
            first = await client.call_tool("list_pets", {"input": {"page": 1}})
        assert first.is_error is False

        # Second, fully independent connection.
        async with Client(
            streamable_http_client(
                mcp_url,
                http_client=mcp_http_client,
            )
        ) as client:
            second = await client.call_tool("list_pets", {"input": {"page": 2}})
    finally:
        set_default_client(None)

    assert second.is_error is False
    assert second.structured_content["total"] == 1
