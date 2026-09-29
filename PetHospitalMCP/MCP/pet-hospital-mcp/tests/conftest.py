"""Shared pytest fixtures for the Pet Hospital MCP test suite.

Tests never touch the real Go backend — every HTTP interaction is
mocked through `httpx.MockTransport`, so the suite is hermetic.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

# Make `src/` importable without installing the package.
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pet_hospital_mcp.rest_client import PetHospitalClient  # noqa: E402


# ---------------------------------------------------------------------------
# Sample data builders
# ---------------------------------------------------------------------------


def make_pet(
    *,
    id: str = "p-001",
    name: str = "旺财",
    species: str = "犬",
    owner_name: str = "张三",
    owner_phone: str = "13800138000",
    doctor: str = "李医生",
    disease: str = "感冒",
    status: str = "就诊中",
    total_cost: float = 120.5,
    visit_count: int = 1,
    records: list[dict[str, Any]] | None = None,
    charges: list[dict[str, Any]] | None = None,
    with_optional: bool = True,
) -> dict[str, Any]:
    """Build a pet dict matching the Go `model.Pet` JSON shape."""
    pet: dict[str, Any] = {
        "id": id,
        "name": name,
        "species": species,
        "ownerName": owner_name,
        "ownerPhone": owner_phone,
        "doctor": doctor,
        "disease": disease,
        "status": status,
        "totalCost": total_cost,
        "visitCount": visit_count,
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-02T00:00:00Z",
        "records": records if records is not None else [],
        "charges": charges if charges is not None else [],
    }
    if with_optional:
        pet.update(
            {
                "breed": "金毛",
                "gender": "公",
                "ageMonths": 24,
                "color": "金色",
                "chipNo": "CHIP-001",
                "ownerAddr": "北京市海淀区",
                "allergy": "无",
                "note": "",
            }
        )
    return pet


def make_success_response(
    items: list[dict[str, Any]] | None = None,
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 20,
    total_cost: float = 0.0,
) -> dict[str, Any]:
    """Build the Go `Resp{code,message,data,time}` envelope."""
    items = items if items is not None else []
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "items": items,
            "total": total if total is not None else len(items),
            "page": page,
            "pageSize": page_size,
            "totalPages": (
                (total if total is not None else len(items)) + page_size - 1
            )
            // page_size
            if page_size > 0
            else 1,
            "totalCost": total_cost,
        },
        "time": "2024-01-01T00:00:00Z",
    }


def make_error_envelope(
    code: int = 400,
    message: str = "bad request",
) -> dict[str, Any]:
    """Build the Go error-shaped envelope."""
    return {
        "code": code,
        "message": message,
        "data": None,
        "time": "2024-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


HandlerFn = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_mock_client() -> Callable[[HandlerFn, float], PetHospitalClient]:
    """Return a factory that builds a `PetHospitalClient` with a mock transport.

    The factory takes the request handler and an optional timeout override
    (tests for BACKEND_TIMEOUT pass a tiny timeout to force timeouts even
    when the handler responds).
    """

    def _build(handler: HandlerFn, timeout: float = 5.0) -> PetHospitalClient:
        transport = httpx.MockTransport(handler)
        return PetHospitalClient(
            base_url="http://pet-hospital.test",
            timeout_seconds=timeout,
            retries=0,  # most tests want to assert on a single attempt
            retry_backoff_seconds=0.0,
            transport=transport,
        )

    return _build


@pytest.fixture
def sample_pet() -> dict[str, Any]:
    return make_pet()


@pytest.fixture
def sample_success_envelope() -> dict[str, Any]:
    return make_success_response(items=[make_pet()])
