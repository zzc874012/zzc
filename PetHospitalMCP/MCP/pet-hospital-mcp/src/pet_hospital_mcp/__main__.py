"""Run the Pet Hospital MCP service with `python -m pet_hospital_mcp`.

Example:
    python -m pet_hospital_mcp
    MCP_HOST=0.0.0.0 MCP_PORT=9000 python -m pet_hospital_mcp
"""

from __future__ import annotations

import uvicorn

from .config import get_settings
from .logging_config import configure_logging
from .server import create_app


def main() -> None:
    """Entry point used by `python -m pet_hospital_mcp` and the script hook."""
    settings = get_settings()
    configure_logging()
    # create_app() also builds the module-level REST client pointed at the
    # configured Go backend and assembles the stateless Streamable HTTP app.
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
