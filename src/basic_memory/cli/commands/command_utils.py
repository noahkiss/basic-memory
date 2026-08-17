"""Helpers for the CLI verbs that route through the in-process API client.

Importing this module pulls the MCP client graph, so a native verb must not
import it. The event-loop helper every verb needs lives in `cli/runner.py`,
which imports nothing from `basic_memory.mcp` (GAPS.md T30).
"""

from typing import Optional

import typer

from rich.console import Console

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.project_context import get_active_project
from basic_memory.project_marker import resolve_cli_project

console = Console()


async def run_project_index(
    project: Optional[str] = None,
    force_full: bool = False,
    run_in_background: bool = True,
):
    """Run project indexing via API endpoint.

    Args:
        project: Optional project name
        force_full: If True, force a full scan bypassing watermark optimization
        run_in_background: If True, return immediately; if False, wait for completion
    """
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        project = resolve_cli_project(project)
        async with get_client() as client:
            project_item = await get_active_project(client, project, None)
            project_client = ProjectClient(client)
            data = await project_client.index(
                project_item.external_id,
                force_full=force_full,
                run_in_background=run_in_background,
            )
            # Background mode returns {"message": "..."}, foreground returns project-index counts.
            if "message" in data:
                console.print(f"[green]{data['message']}[/green]")
            else:
                total_files = data.get("total_files", 0)
                enqueued_files = data.get("enqueued_files", 0)
                enqueued_batches = data.get("enqueued_batches", 0)
                deleted_files = data.get("deleted_files", 0)
                console.print(
                    f"[green]Indexed {enqueued_files}/{total_files} files[/green] "
                    f"(batches: {enqueued_batches}, deleted orphans: {deleted_files})"
                )
    except (ToolError, ValueError) as e:
        console.print(f"[red]Index failed: {e}[/red]")
        raise typer.Exit(1)


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
