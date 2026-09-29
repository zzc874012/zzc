"""Validation tests for `ListPetsInput`.

Each test asserts one rule from prompt-step1.md:
  * species/status/sortBy/order must be from the backend's enum
  * page >= 1
  * 1 <= pageSize <= 500
  * min/max are non-negative and min <= max
  * NaN/Infinity are rejected
  * unknown fields are forbidden
  * wrong types are rejected

These never touch the backend — they all fail before any HTTP call.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from pet_hospital_mcp.tools import ListPetsInput


def test_rejects_unknown_field():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(adapterOnly="nope")  # type: ignore[call-arg]
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_rejects_invalid_species():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(species="外星人")
    assert "species must be one of" in str(exc_info.value)


def test_rejects_invalid_status():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(status="飞走了")
    assert "status must be one of" in str(exc_info.value)


def test_rejects_invalid_sort_by():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(sortBy="random")
    assert "sortBy must be one of" in str(exc_info.value)


def test_rejects_invalid_order():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(order="random")
    assert "order must be one of" in str(exc_info.value)


def test_page_must_be_at_least_1():
    with pytest.raises(ValidationError):
        ListPetsInput(page=0)
    with pytest.raises(ValidationError):
        ListPetsInput(page=-1)


def test_pagesize_must_be_between_1_and_500():
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=0)
    with pytest.raises(ValidationError):
        ListPetsInput(pageSize=501)
    # boundaries are valid
    assert ListPetsInput(pageSize=1).pageSize == 1
    assert ListPetsInput(pageSize=500).pageSize == 500


def test_min_must_be_non_negative():
    with pytest.raises(ValidationError):
        ListPetsInput(min=-0.01)


def test_max_must_be_non_negative():
    with pytest.raises(ValidationError):
        ListPetsInput(max=-1.0)


def test_min_must_be_less_or_equal_to_max():
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(min=10.0, max=5.0)
    assert "min must be <= max" in str(exc_info.value)


def test_rejects_nan_min():
    """NaN fails Pydantic's `ge=0` numeric check (NaN is not >= 0)."""
    with pytest.raises(ValidationError):
        ListPetsInput(min=math.nan)


def test_rejects_nan_max():
    with pytest.raises(ValidationError):
        ListPetsInput(max=math.nan)


def test_rejects_infinity_min():
    """+inf passes ge=0 but our finite-validator rejects it post-hoc."""
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(min=math.inf)
    assert "min must be a finite number" in str(exc_info.value)


def test_rejects_infinity_max():
    """-inf fails ge=0; +inf passes ge=0 and is caught by our finite-check."""
    # Negative infinity is rejected by ge=0 (Pydantic numeric constraint).
    with pytest.raises(ValidationError):
        ListPetsInput(max=-math.inf)
    # Positive infinity is rejected by our model_validator.
    with pytest.raises(ValidationError) as exc_info:
        ListPetsInput(max=math.inf)
    assert "max must be a finite number" in str(exc_info.value)


def test_rejects_wrong_type_for_page():
    with pytest.raises(ValidationError):
        ListPetsInput(page="first")  # type: ignore[arg-type]


def test_rejects_wrong_type_for_min():
    with pytest.raises(ValidationError):
        ListPetsInput(min="free")  # type: ignore[arg-type]


def test_valid_input_passes_validation():
    """All valid enum values and ranges should round-trip through the model."""
    input_ = ListPetsInput(
        species="犬",
        status="就诊中",
        sortBy="totalCost",
        order="desc",
        page=1,
        pageSize=20,
        min=0.0,
        max=1_000_000.0,
    )
    query = input_.to_query()
    assert query["species"] == "犬"
    assert query["min"] == 0.0
    assert query["max"] == 1_000_000.0
    # None fields must NOT appear in the forwarded query.
    assert "q" not in query
    assert "name" not in query
