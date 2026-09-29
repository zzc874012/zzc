"""MCP tools namespace for the Pet Hospital service.

New tools are added by dropping a module here and registering it in
`server.py` — REST client, logging, and error conventions are shared.
"""

from .list_pets import (
    TOOL_DESCRIPTION,
    TOOL_NAME,
    ListPetsInput,
    ListPetsResult,
    ListPetsToolInput,
    list_pets,
    run_list_pets_tool,
)

__all__ = [
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "ListPetsInput",
    "ListPetsResult",
    "ListPetsToolInput",
    "list_pets",
    "run_list_pets_tool",
]
