"""Pet Hospital MCP service.

A stateless MCP server (SDK 2.x, protocol 2026-07-28) that exposes the
Go Pet Hospital REST API to AI agents via the Streamable HTTP transport.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pet-hospital-mcp")
except PackageNotFoundError:  # pragma: no cover - dev installs
    __version__ = "0.1.0"

# Protocol / SDK version constants (single source of truth for tests + README).
MCP_SDK_VERSION = "2.0.0"
MCP_PROTOCOL_VERSION = "2026-07-28"

__all__ = ["MCP_SDK_VERSION", "MCP_PROTOCOL_VERSION", "__version__"]
