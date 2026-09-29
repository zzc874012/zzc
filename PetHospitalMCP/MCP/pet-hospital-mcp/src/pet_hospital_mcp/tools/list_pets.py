"""`list_pets` tool: forward validated filters to GET /api/v1/pets.

This module owns:
  * Pydantic input/output models matching the Go REST contract
  * The tool function registered with the SDK 2.x `MCPServer`

It deliberately keeps no business logic of its own — every filter the
model exposes maps 1:1 onto a Go query parameter. Future tools should
follow the same shape (model → rest_client → tool).
"""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, Field, ValidationError, model_validator

from .. import errors
from ..config import (
    ALLOWED_ORDER,
    ALLOWED_SORT_BY,
    ALLOWED_SPECIES,
    ALLOWED_STATUS,
)
from ..errors import ErrorEnvelope, raise_tool_error
from ..logging_config import ToolCallLogger
from ..rest_client import PetHospitalClient, RestResponse


# ---------------------------------------------------------------------------
# Sub-models mirroring the Go model package
# ---------------------------------------------------------------------------


class Treatment(BaseModel):
    """A single charge line (Go: `model.Treatment`)."""

    id: str
    item: str
    category: str
    amount: float
    doctor: str | None = None
    date: str
    note: str | None = None

    model_config = {"extra": "ignore"}


class MedicalRecord(BaseModel):
    """A single visit record (Go: `model.MedicalRecord`)."""

    id: str
    visitDate: str
    doctor: str
    diagnosis: str
    symptoms: str | None = None
    treatment: str | None = None
    prescription: list[str] | None = None
    weightKg: float | None = None
    temperature: float | None = None
    followUp: str | None = None
    charge: float
    createdAt: str

    model_config = {"extra": "ignore"}


class Pet(BaseModel):
    """A pet aggregate (Go: `model.Pet`).

    `records` and `charges` MAY arrive as `null` per the upstream JSON
    encoding — both are typed `| None` so we faithfully preserve that.
    """

    id: str
    name: str
    species: str
    breed: str | None = None
    gender: str | None = None
    ageMonths: int | None = None
    color: str | None = None
    chipNo: str | None = None
    ownerName: str
    ownerPhone: str
    ownerAddr: str | None = None
    doctor: str
    disease: str
    status: str
    allergy: str | None = None
    note: str | None = None
    records: list[MedicalRecord] | None = None
    charges: list[Treatment] | None = None
    totalCost: float
    visitCount: int
    createdAt: str
    updatedAt: str

    model_config = {"extra": "ignore"}


class ListPetsResult(BaseModel):
    """Successful response payload (Go: `store.Result.data`)."""

    items: list[Pet]
    total: int
    page: int
    pageSize: int
    totalPages: int
    totalCost: float

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


class ListPetsInput(BaseModel):
    """Input schema for the `list_pets` tool.

    Only the 14 query parameters supported by GET /api/v1/pets are exposed.
    Unknown fields are rejected (`extra="forbid"`) so the model can't sneak
    adapter-only parameters past validation.
    """

    q: str | None = Field(default=None, description="Cross-field fuzzy search.")
    name: str | None = Field(default=None, description="Pet name (fuzzy).")
    ownerName: str | None = Field(default=None, description="Owner name (fuzzy).")
    ownerPhone: str | None = Field(
        default=None, description="Owner phone (fuzzy). Sensitive — logged masked."
    )
    species: str | None = Field(
        default=None,
        description="Pet species (exact). One of: "
        + ", ".join(sorted(ALLOWED_SPECIES)),
    )
    doctor: str | None = Field(default=None, description="Doctor name (fuzzy).")
    disease: str | None = Field(default=None, description="Disease (fuzzy).")
    status: str | None = Field(
        default=None,
        description="Visit status (exact). One of: "
        + ", ".join(sorted(ALLOWED_STATUS)),
    )
    min: float | None = Field(
        default=None,
        ge=0,
        description="Minimum total cost (inclusive). Must be <= max.",
    )
    max: float | None = Field(
        default=None,
        ge=0,
        description="Maximum total cost (inclusive). Must be >= min.",
    )
    sortBy: str | None = Field(
        default=None,
        description="Sort key. One of: " + ", ".join(sorted(ALLOWED_SORT_BY)),
    )
    order: str | None = Field(
        default=None,
        description="Sort direction. One of: " + ", ".join(sorted(ALLOWED_ORDER)),
    )
    page: int | None = Field(default=None, ge=1, description="Page number, 1-based.")
    pageSize: int | None = Field(
        default=None,
        ge=1,
        le=500,
        description="Items per page (1-500).",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_enums_and_ranges(self) -> "ListPetsInput":
        # Enum checks (None is allowed — means "do not filter").
        if self.species is not None and self.species not in ALLOWED_SPECIES:
            raise ValueError(
                f"species must be one of: {', '.join(sorted(ALLOWED_SPECIES))}"
            )
        if self.status is not None and self.status not in ALLOWED_STATUS:
            raise ValueError(
                f"status must be one of: {', '.join(sorted(ALLOWED_STATUS))}"
            )
        if self.sortBy is not None and self.sortBy not in ALLOWED_SORT_BY:
            raise ValueError(
                f"sortBy must be one of: {', '.join(sorted(ALLOWED_SORT_BY))}"
            )
        if self.order is not None and self.order not in ALLOWED_ORDER:
            raise ValueError(
                f"order must be one of: {', '.join(sorted(ALLOWED_ORDER))}"
            )

        # NaN / Infinity rejection for cost bounds.
        for name, value in (("min", self.min), ("max", self.max)):
            if value is not None and (math.isnan(value) or math.isinf(value)):
                raise ValueError(f"{name} must be a finite number")

        # Range relationship: min <= max.
        if (
            self.min is not None
            and self.max is not None
            and self.min > self.max
        ):
            raise ValueError("min must be <= max")

        return self

    def to_query(self) -> dict[str, Any]:
        """Return the dict to forward to httpx (None values dropped)."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

TOOL_NAME = "list_pets"

TOOL_DESCRIPTION = """List pets from the Pet Hospital REST API (GET /api/v1/pets).

Use this tool when the user asks about pets in the hospital registry:
searching by name, owner, species, doctor, disease, visit status, or
total cost; sorting; or paginating through results.

Parameters (all optional, pass only what you need):
- q: cross-field fuzzy search
- name, ownerName, ownerPhone, doctor, disease: field-level fuzzy filters
- species: exact species (犬/猫/兔/鸟/仓鼠/爬宠/其他)
- status: exact visit status (待就诊/就诊中/住院中/已康复/慢性病随访)
- min, max: total cost range (inclusive, in 元)
- sortBy: one of id, name, ownerName, species, doctor, disease, status,
  totalCost, visitCount, createdAt, updatedAt
- order: asc or desc
- page: 1-based page number (>= 1)
- pageSize: items per page (1-500)

Returns an object with: items (array of Pet), total, page, pageSize,
totalPages, totalCost. On failure returns a structured error payload
with code/message/details so you can correct arguments and retry.

Examples:
- list all: {}
- first page of dogs sorted by total cost descending: {species: "犬", sortBy: "totalCost", order: "desc"}
- paginate: {page: 2, pageSize: 20}
"""


def _raise_from_rest_failure(rest: RestResponse) -> None:
    """Translate a RestResponse failure into a ToolError carrying our envelope."""
    raise raise_tool_error(
        rest.error_code or errors.INTERNAL_ERROR,
        rest.error_message or "Unknown backend failure",
        details={
            **rest.error_details,
            **({"status_code": rest.status_code} if rest.status_code is not None else {}),
        },
    )


def _unwrap_envelope(body: dict[str, Any]) -> dict[str, Any]:
    """Extract the `data` object from the Go `Resp{code,message,data,time}`.

    The Go service always wraps payloads in this envelope. Its success code
    mirrors the HTTP status (200; some builds emit 0), while a 4xx/5xx code
    or a missing/non-object `data` means the upstream contract was violated.
    """
    SUCCESS_CODES = {0, 200, "0", "200", None}
    code = body.get("code", 200)
    if code not in SUCCESS_CODES:
        message = str(body.get("message") or f"backend business code {code}")
        raise raise_tool_error(
            errors.BACKEND_API_ERROR,
            message,
            details={"backend_code": code},
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise raise_tool_error(
            errors.BACKEND_INVALID_RESPONSE,
            "Backend envelope 'data' was not a JSON object",
            details={"data_type": type(data).__name__},
        )
    return data


def _validate_response_data(data: dict[str, Any]) -> ListPetsResult:
    """Validate the unwrapped backend `data` against our output schema.

    A schema mismatch is BACKEND_INVALID_RESPONSE (not VALIDATION_ERROR),
    because the model's arguments were fine — the upstream returned a
    payload we can't trust.
    """
    try:
        return ListPetsResult.model_validate(data)
    except ValidationError as exc:
        raise raise_tool_error(
            errors.BACKEND_INVALID_RESPONSE,
            "Backend response did not match the expected data model",
            details={"validation_error": str(exc)},
        )


def validate_tool_input(raw: Any) -> ListPetsInput:
    """Validate raw MCP tool arguments against `ListPetsInput`.

    The registered tool function takes a plain dict (so the SDK does not
    perform its own signature validation); this function is the single
    place that turns invalid arguments into our unified
    `VALIDATION_ERROR` envelope instead of a raw Pydantic string.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise raise_tool_error(
            errors.VALIDATION_ERROR,
            "Tool arguments must be a JSON object",
            details={"received_type": type(raw).__name__},
        )
    try:
        return ListPetsInput.model_validate(raw)
    except ValidationError as exc:
        raise raise_tool_error(
            errors.VALIDATION_ERROR,
            "Invalid list_pets arguments",
            details={
                "validation_errors": exc.errors(
                    include_url=False, include_context=False
                )
            },
        )


async def list_pets(
    input: ListPetsInput,
    *,
    client: PetHospitalClient | None = None,
) -> ListPetsResult:
    """Business entry point: call GET /api/v1/pets with a validated input.

    The `client` kwarg is optional; in production the module-level default
    client is used, while tests inject a client backed by a MockTransport.
    """
    from ..rest_client import get_default_client

    params_log: dict[str, Any] = input.to_query()
    active_client = client or get_default_client()
    if active_client is None:
        raise raise_tool_error(
            errors.INTERNAL_ERROR,
            "REST client is not initialised",
        )

    with ToolCallLogger(TOOL_NAME, params_log) as log:
        rest = await active_client.list_pets(input.to_query())
        if not rest.ok:
            log.fail(rest.error_code, rest.error_message)
            _raise_from_rest_failure(rest)  # always raises

        data = _unwrap_envelope(rest.data or {})
        result = _validate_response_data(data)
        log.ok(result.model_dump(mode="json"))
        return result


# ---------------------------------------------------------------------------
# Registered-tool adapter
# ---------------------------------------------------------------------------


def _inject_input_json_schema(schema: dict[str, Any]) -> None:
    """Replace the generated dict schema with `ListPetsInput`'s full schema.

    Used via Pydantic ``json_schema_extra`` so the advertised MCP input
    schema documents every field, enum and range even though the tool
    function accepts a raw dict (validation happens in `validate_tool_input`).
    """
    schema.clear()
    schema.update(ListPetsInput.model_json_schema())


# SDK 2.x calling convention for a single Pydantic-shaped argument:
# the JSON schema nests the model under a property named after the
# parameter (`input`), and clients call with ``{"input": {...}}``.
ListPetsToolInput = Annotated[
    dict[str, Any] | None,
    Field(default=None, json_schema_extra=_inject_input_json_schema),
]


async def run_list_pets_tool(input: ListPetsToolInput = None) -> ListPetsResult:  # type: ignore[assignment]
    """The function actually registered with the MCPServer.

    It validates raw arguments itself (raising the unified VALIDATION_ERROR
    envelope on failure) and then delegates to `list_pets`.
    """
    validated = validate_tool_input(input)
    return await list_pets(validated)


# Re-export the structured error envelope type for callers/tests.
__all__ = [
    "ListPetsToolInput",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "ErrorEnvelope",
    "ListPetsInput",
    "ListPetsResult",
    "MedicalRecord",
    "Pet",
    "Treatment",
    "list_pets",
    "run_list_pets_tool",
    "validate_tool_input",
]
