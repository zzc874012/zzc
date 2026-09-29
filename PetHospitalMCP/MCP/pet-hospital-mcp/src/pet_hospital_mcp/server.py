"""SDK 2.x `MCPServer` assembly for the Pet Hospital MCP service.

Builds the `MCPServer`, registers the `list_pets` tool, exposes a
`/health` HTTP endpoint, and wires the Streamable HTTP ASGI app in
**stateless** mode per MCP 2026-07-28.
"""

from __future__ import annotations

from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import __version__ as _pkg_version
from . import MCP_PROTOCOL_VERSION, MCP_SDK_VERSION
from .config import Settings, get_settings
from .logging_config import configure_logging
from .rest_client import PetHospitalClient, set_default_client
from .tools import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    run_list_pets_tool,
)

# ---------------------------------------------------------------------------
# MCPServer construction
# ---------------------------------------------------------------------------

mcp: MCPServer = MCPServer(
    name="pet-hospital-mcp",
    title="Pet Hospital MCP",
    description=(
        "Stateless MCP service exposing the Go Pet Hospital REST API "
        "via the Streamable HTTP transport."
    ),
    instructions=TOOL_DESCRIPTION,
    version=_pkg_version,
)


# Register the tool adapter. Its `input` parameter carries the full
# ListPetsInput JSON schema (via json_schema_extra) while the function
# body performs validation itself so every failure uses our unified
# structured error envelope.
mcp.add_tool(
    run_list_pets_tool,
    name=TOOL_NAME,
    description=TOOL_DESCRIPTION,
)


# ---------------------------------------------------------------------------
# Custom HTTP routes
# ---------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> Response:
    """Liveness probe.

    The route is mounted on the same Starlette app as the MCP endpoint
    so a single port serves both. It does not call the Go backend —
    that is the job of `list_pets` itself.
    """
    return JSONResponse(
        {
            "status": "ok",
            "service": "pet-hospital-mcp",
            "version": _pkg_version,
            "mcp_sdk_version": MCP_SDK_VERSION,
            "mcp_protocol_version": MCP_PROTOCOL_VERSION,
        }
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None):
    """Build the stateless Starlette ASGI app.

    Side effects:
      * Configures structured JSON logging.
      * Creates a module-level `PetHospitalClient` so the registered tool
        can pick it up at call time.
      * Assembles the Streamable HTTP app in stateless mode.

    Tests bypass this and construct their own `MCPServer`/clients.
    """
    cfg = settings or get_settings()
    configure_logging()
    client = PetHospitalClient(
        base_url=cfg.pet_hospital_base_url,
        timeout_seconds=cfg.backend_timeout_seconds,
        retries=cfg.backend_retries,
        retry_backoff_seconds=cfg.backend_retry_backoff_seconds,
    )
    set_default_client(client)

    return mcp.streamable_http_app(
        streamable_http_path=cfg.mcp_path,
        stateless_http=True,
        host=cfg.mcp_host,
    )


def run() -> None:
    """Run the server with `mcp.run` for the simplest possible CLI."""
    import uvicorn  # noqa: F401  imported for its side effect on workers

    cfg = get_settings()
    mcp.run(
        transport="streamable-http",
        host=cfg.mcp_host,
        port=cfg.mcp_port,
        stateless_http=True,
    )


# `app` is created lazily so importing this module is side-effect free
# for tests; the ASGI app is built on first attribute access via __getattr__.
def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "mcp",
    "create_app",
    "run",
]
