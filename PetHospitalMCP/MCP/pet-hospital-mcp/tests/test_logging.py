"""Tests for structured logging and recursive PII redaction.

Spec requirements verified here:
  * every tool-call log line carries timestamp/tool_name/params/status/duration_ms
  * ownerPhone / ownerAddr / chipNo (camelCase and snake_case) are
    recursively masked in nested objects and arrays
  * full sensitive values never appear in log output
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from pet_hospital_mcp.logging_config import JsonFormatter, redact
from pet_hospital_mcp.rest_client import PetHospitalClient
from pet_hospital_mcp.tools import ListPetsInput, list_pets
from conftest import make_pet, make_success_response


def test_redact_masks_sensitive_camel_case_keys():
    data = {
        "ownerPhone": "13800138000",
        "ownerAddr": "北京市海淀区",
        "chipNo": "CHIP-001",
        "name": "旺财",
    }
    out = redact(data)
    assert out["ownerPhone"] == "***REDACTED***"
    assert out["ownerAddr"] == "***REDACTED***"
    assert out["chipNo"] == "***REDACTED***"
    # Non-sensitive fields are preserved.
    assert out["name"] == "旺财"


def test_redact_masks_sensitive_snake_case_keys():
    data = {
        "owner_phone": "13800138000",
        "owner_addr": "addr",
        "chip_no": "chip",
    }
    out = redact(data)
    assert out["owner_phone"] == "***REDACTED***"
    assert out["owner_addr"] == "***REDACTED***"
    assert out["chip_no"] == "***REDACTED***"


def test_redact_is_recursive_in_nested_objects_and_arrays():
    data = {
        "outer": [
            {"ownerPhone": "1"},
            {"nested": {"chipNo": "c", "keep": 1}},
        ],
        "ownerAddr": "a",
    }
    out = redact(data)
    assert out["outer"][0]["ownerPhone"] == "***REDACTED***"
    assert out["outer"][1]["nested"]["chipNo"] == "***REDACTED***"
    assert out["outer"][1]["nested"]["keep"] == 1
    assert out["ownerAddr"] == "***REDACTED***"


def test_redact_passes_through_scalars():
    assert redact("x") == "x"
    assert redact(1) == 1
    assert redact(None) is None


def test_json_formatter_emits_required_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="pet_hospital_mcp.tool",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tool_call",
        args=(),
        exc_info=None,
    )
    record.tool_name = "list_pets"
    record.params = {"ownerPhone": "13800138000", "species": "犬"}
    record.status = "ok"
    record.duration_ms = 42

    payload: dict[str, Any] = json.loads(formatter.format(record))
    assert "timestamp" in payload
    assert payload["tool_name"] == "list_pets"
    assert payload["status"] == "ok"
    assert payload["duration_ms"] == 42
    # Sensitive value is masked inside the logged params.
    assert payload["params"]["ownerPhone"] == "***REDACTED***"
    assert payload["params"]["species"] == "犬"
    assert "13800138000" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_successful_tool_call_emits_structured_log(caplog):
    """A real tool call should log one tool_call record with all required fields."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=make_success_response(
                items=[make_pet(owner_phone="13900139000")],
                total=1,
            ),
        )

    client = PetHospitalClient(
        base_url="http://pet-hospital.test",
        timeout_seconds=5.0,
        retries=0,
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )

    with caplog.at_level(logging.INFO, logger="pet_hospital_mcp.tool"):
        await list_pets(
            ListPetsInput(ownerPhone="13900139000"),
            client=client,
        )

    tool_records = [r for r in caplog.records if r.message == "tool_call"]
    assert tool_records, "expected a tool_call log record"
    rec = tool_records[-1]
    assert rec.tool_name == "list_pets"
    assert rec.status == "ok"
    assert isinstance(rec.duration_ms, int)
    # The raw phone was captured as a param...
    assert rec.params.get("ownerPhone") == "13900139000"
    # ...but the formatter must redact it in the emitted log line.
    formatter = JsonFormatter()
    line = formatter.format(rec)
    assert "***REDACTED***" in line
    assert "13900139000" not in line
    # Result item payloads are summarised, not dumped wholesale.
    assert "北京市海淀区" not in line
