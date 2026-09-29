"""Tests for the happy path of `list_pets`.

Goal: confirm the tool correctly forwards every supported filter,
sort, and pagination parameter to GET /api/v1/pets, parses the
Go `Resp.data` envelope, and returns a `ListPetsResult` matching
the contract.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
import pytest

from pet_hospital_mcp.tools import ListPetsInput, ListPetsResult, list_pets
from conftest import make_pet, make_success_response


@pytest.mark.asyncio
async def test_forward_all_filters_sort_and_pagination(make_mock_client):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=make_success_response(items=[make_pet()], total=1),
        )

    client = make_mock_client(handler)

    input_ = ListPetsInput(
        q="犬",
        name="旺财",
        ownerName="张三",
        ownerPhone="13800138000",
        species="犬",
        doctor="李医生",
        disease="感冒",
        status="就诊中",
        min=0.0,
        max=1000.0,
        sortBy="totalCost",
        order="desc",
        page=2,
        pageSize=15,
    )

    result = await list_pets(input_, client=client)

    # 1. URL is the expected endpoint.
    assert captured["path"] == "/api/v1/pets"
    assert captured["url"].startswith("http://pet-hospital.test/api/v1/pets?")

    # 2. Every filter the model accepted is forwarded verbatim.
    params = captured["params"]
    assert params["q"] == "犬"
    assert params["name"] == "旺财"
    assert params["ownerName"] == "张三"
    assert params["ownerPhone"] == "13800138000"
    assert params["species"] == "犬"
    assert params["doctor"] == "李医生"
    assert params["disease"] == "感冒"
    assert params["status"] == "就诊中"
    assert params["min"] == "0.0"
    assert params["max"] == "1000.0"
    assert params["sortBy"] == "totalCost"
    assert params["order"] == "desc"
    assert params["page"] == "2"
    assert params["pageSize"] == "15"

    # 3. Result is a typed ListPetsResult with the expected summary fields.
    assert isinstance(result, ListPetsResult)
    assert result.total == 1
    assert result.page == 1  # backend echoes the page it returned
    assert len(result.items) == 1
    pet = result.items[0]
    assert pet.id == "p-001"
    assert pet.name == "旺财"
    assert pet.species == "犬"
    assert pet.records == []
    assert pet.charges == []


@pytest.mark.asyncio
async def test_minimal_call_passes_no_query_params(make_mock_client):
    """Calling with only defaults should still hit /api/v1/pets (no params)."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=make_success_response(items=[], total=0))

    client = make_mock_client(handler)
    result = await list_pets(ListPetsInput(), client=client)

    assert captured["params"] == {}
    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_records_and_charges_null_are_preserved(make_mock_client):
    """The Go backend may encode records/charges as `null`; we must accept it."""
    pet = make_pet()
    pet["records"] = None
    pet["charges"] = None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_success_response(items=[pet], total=1))

    client = make_mock_client(handler)
    result = await list_pets(ListPetsInput(), client=client)

    assert len(result.items) == 1
    assert result.items[0].records is None
    assert result.items[0].charges is None


@pytest.mark.asyncio
async def test_substructures_parse(make_mock_client):
    """MedicalRecord and Treatment payloads parse into typed models."""
    pet = make_pet(
        records=[
            {
                "id": "r-1",
                "visitDate": "2024-01-01",
                "doctor": "李医生",
                "diagnosis": "感冒",
                "symptoms": "咳嗽",
                "treatment": "服药",
                "prescription": ["阿莫西林"],
                "weightKg": 5.2,
                "temperature": 38.5,
                "followUp": "3天后复查",
                "charge": 80.0,
                "createdAt": "2024-01-01T00:00:00Z",
            }
        ],
        charges=[
            {
                "id": "c-1",
                "item": "血常规",
                "category": "检查",
                "amount": 40.5,
                "doctor": "李医生",
                "date": "2024-01-01",
                "note": "正常",
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_success_response(items=[pet], total=1))

    client = make_mock_client(handler)
    result = await list_pets(ListPetsInput(), client=client)

    assert len(result.items) == 1
    p = result.items[0]
    assert p.records and p.records[0].diagnosis == "感冒"
    assert p.records[0].weightKg == pytest.approx(5.2)
    assert p.charges and p.charges[0].amount == pytest.approx(40.5)
    assert math.isfinite(result.totalCost)
