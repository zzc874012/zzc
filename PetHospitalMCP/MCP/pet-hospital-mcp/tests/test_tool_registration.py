"""Tests for MCP tool registration, name, and JSON Schema.

These tests use the in-memory SDK 2.x `Client` against the live `mcp`
server object from `pet_hospital_mcp.server` (no HTTP involved), so
they verify the registration surface independently of the network
transport.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mcp import Client

from pet_hospital_mcp import errors
from pet_hospital_mcp.rest_client import PetHospitalClient, set_default_client
from pet_hospital_mcp.server import mcp
from pet_hospital_mcp.tools import TOOL_NAME
from conftest import make_pet, make_success_response


def _mock_client(handler):
    transport = httpx.MockTransport(handler)
    return PetHospitalClient(
        base_url="http://pet-hospital.test",
        timeout_seconds=5.0,
        retries=0,
        retry_backoff_seconds=0.0,
        transport=transport,
    )


def _extract_error_payload(result) -> dict[str, Any]:
    """Pull the JSON error envelope out of a failed CallToolResult."""
    text = result.content[0].text if result.content else ""
    marker = f"Error executing tool {TOOL_NAME}: "
    if marker in text:
        text = text.split(marker, 1)[1]
    return json.loads(text)


@pytest.mark.asyncio
async def test_list_pets_tool_is_registered_with_expected_name():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = [t.name for t in tools.tools]
    assert TOOL_NAME in names
    assert names.count(TOOL_NAME) == 1


@pytest.mark.asyncio
async def test_list_pets_tool_has_input_schema_with_supported_params():
    """SDK 2.x nests a single model argument under a property called `input`."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
    tool = next(t for t in tools.tools if t.name == TOOL_NAME)
    assert tool.input_schema is not None

    input_prop = tool.input_schema["properties"]["input"]
    # The injected schema is ListPetsInput's full JSON schema.
    props: dict[str, Any] = input_prop.get("properties", {})
    expected = {
        "q",
        "name",
        "ownerName",
        "ownerPhone",
        "species",
        "doctor",
        "disease",
        "status",
        "min",
        "max",
        "sortBy",
        "order",
        "page",
        "pageSize",
    }
    assert expected.issubset(set(props.keys()))
    # No adapter-only fields should leak through.
    assert "adapterOnly" not in props
    # Unknown fields are forbidden by the input model.
    assert input_prop.get("additionalProperties") is False

    # Nullable optionals are encoded as anyOf[numeric-schema, null].
    def numeric_branch(schema: dict[str, Any]) -> dict[str, Any]:
        if "anyOf" in schema:
            return next(b for b in schema["anyOf"] if b.get("type") != "null")
        return schema

    page_schema = numeric_branch(props["page"])
    size_schema = numeric_branch(props["pageSize"])
    assert page_schema.get("minimum") == 1
    assert size_schema.get("minimum") == 1
    assert size_schema.get("maximum") == 500


@pytest.mark.asyncio
async def test_list_pets_tool_has_output_schema():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    tool = next(t for t in tools.tools if t.name == TOOL_NAME)
    assert tool.output_schema is not None
    props = tool.output_schema.get("properties", {})
    for required in ("items", "total", "page", "pageSize", "totalPages", "totalCost"):
        assert required in props, f"output schema missing {required}"


@pytest.mark.asyncio
async def test_call_list_pets_via_inmemory_client_returns_structured_output():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=make_success_response(items=[make_pet()], total=1),
        )

    set_default_client(_mock_client(handler))
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                TOOL_NAME, {"input": {"species": "犬"}}
            )
    finally:
        set_default_client(None)

    assert result.is_error is False
    assert result.structured_content is not None
    sc = result.structured_content
    assert sc["total"] == 1
    assert isinstance(sc["items"], list)
    assert sc["items"][0]["id"] == "p-001"


@pytest.mark.asyncio
async def test_call_list_pets_with_no_arguments_succeeds():
    """Empty arguments are valid (every filter is optional)."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=make_success_response(items=[], total=0)
        )

    set_default_client(_mock_client(handler))
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(TOOL_NAME, {})
    finally:
        set_default_client(None)

    assert result.is_error is False
    assert result.structured_content["total"] == 0


@pytest.mark.asyncio
async def test_call_list_pets_with_invalid_enum_returns_unified_error():
    """Invalid enum must come back as our VALIDATION_ERROR envelope."""
    set_default_client(_mock_client(lambda r: httpx.Response(200, json={})))
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                TOOL_NAME, {"input": {"species": "外星人"}}
            )
    finally:
        set_default_client(None)

    assert result.is_error is True
    assert result.structured_content is None
    payload = _extract_error_payload(result)
    assert payload["error"]["code"] == errors.VALIDATION_ERROR
    assert "Invalid list_pets arguments" in payload["error"]["message"]
    assert "validation_errors" in payload["error"]["details"]


@pytest.mark.asyncio
async def test_call_list_pets_with_unknown_field_returns_unified_error():
    set_default_client(_mock_client(lambda r: httpx.Response(200, json={})))
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                TOOL_NAME, {"input": {"adapterOnly": "x"}}
            )
    finally:
        set_default_client(None)

    assert result.is_error is True
    payload = _extract_error_payload(result)
    assert payload["error"]["code"] == errors.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_call_list_pets_with_bad_range_returns_unified_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    set_default_client(_mock_client(handler))
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                TOOL_NAME, {"input": {"min": 100, "max": 10}}
            )
    finally:
        set_default_client(None)

    assert result.is_error is True
    payload = _extract_error_payload(result)
    assert payload["error"]["code"] == errors.VALIDATION_ERROR
