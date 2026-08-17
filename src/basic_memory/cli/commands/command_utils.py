"""Helpers for the CLI verbs that route through the in-process API client.

Importing this module pulls the MCP client graph, so a native verb must not
import it. The event-loop helper every verb needs lives in `cli/runner.py`,
which imports nothing from `basic_memory.mcp` (GAPS.md T30).
"""

import typer

from rich.console import Console

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.project_context import get_active_project

console = Console()


async def get_project_info(project: str):
    """Get project information via API endpoint."""
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        async with get_client() as client:
            project_item = await get_active_project(client, project, None)
            return await ProjectClient(client).get_info(project_item.external_id)
    except (ToolError, ValueError) as e:
        console.print(f"[red]Project info failed: {e}[/red]")
        raise typer.Exit(1)
