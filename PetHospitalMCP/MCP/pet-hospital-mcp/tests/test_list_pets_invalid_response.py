"""Tests for malformed backend responses.

These cover the BACKEND_INVALID_RESPONSE error code:
  * body is not valid JSON
  * body is JSON but not an object
  * body is a JSON object whose `data` does not match our model

Distinct from BACKEND_API_ERROR (HTTP 4xx/5xx with a parseable envelope):
these tests get HTTP 200 with a body we can't trust.
"""

from __future__ import annotations

import json

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pet_hospital_mcp import errors
from pet_hospital_mcp.tools import ListPetsInput, list_pets
from conftest import make_success_response


@pytest.mark.asyncio
async def test_non_json_body_yields_backend_invalid_response(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json-at-all")

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_INVALID_RESPONSE
    assert "non-JSON" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_json_array_body_yields_backend_invalid_response(make_mock_client):
    def handler(request: httpx.Request) -> httpx.Response:
        # A JSON array is not the expected object envelope.
        return httpx.Response(200, json=[{"items": []}])

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_INVALID_RESPONSE
    assert "not a JSON object" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_data_missing_items_yields_backend_invalid_response(make_mock_client):
    """Body is an object but not the Go envelope (no object `data`)."""
    def handler(request: httpx.Request) -> httpx.Response:
        # Missing items/total/etc.
        return httpx.Response(200, json={"items": "not-a-list"})

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_INVALID_RESPONSE
    assert "data" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_envelope_data_wrong_shape_yields_backend_invalid_response(
    make_mock_client,
):
    """Envelope is present but `data` doesn't match ListPetsResult."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "message": "ok",
                "data": {"items": "not-a-list"},
                "time": "2024",
            },
        )

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_INVALID_RESPONSE
    assert "data model" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_data_missing_required_fields_yields_backend_invalid_response(
    make_mock_client,
):
    """Pet is missing `name` — schema validation must fail."""
    pet = {
        "id": "p-1",
        # name intentionally omitted
        "species": "犬",
        "ownerName": "x",
        "ownerPhone": "y",
        "doctor": "d",
        "disease": "z",
        "status": "就诊中",
        "totalCost": 0.0,
        "visitCount": 0,
        "createdAt": "2024",
        "updatedAt": "2024",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_success_response(items=[pet], total=1))

    client = make_mock_client(handler)
    with pytest.raises(ToolError) as exc_info:
        await list_pets(ListPetsInput(), client=client)

    payload = json.loads(str(exc_info.value))
    assert payload["error"]["code"] == errors.BACKEND_INVALID_RESPONSE
    assert "data model" in payload["error"]["message"]
    assert "validation_error" in payload["error"]["details"]


@pytest.mark.asyncio
async def test_extra_pet_fields_are_ignored_not_error(make_mock_client):
    """Unknown fields on Pet sub-objects are ignored (extra='ignore')."""
    pet = {
        "id": "p-1",
        "name": "x",
        "species": "犬",
        "ownerName": "x",
        "ownerPhone": "y",
        "doctor": "d",
        "disease": "z",
        "status": "就诊中",
        "totalCost": 0.0,
        "visitCount": 0,
        "createdAt": "2024",
        "updatedAt": "2024",
        "unknownField": "ignored",  # extra field
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_success_response(items=[pet], total=1))

    client = make_mock_client(handler)
    result = await list_pets(ListPetsInput(), client=client)

    assert len(result.items) == 1
