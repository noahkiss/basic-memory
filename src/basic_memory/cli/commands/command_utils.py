"""utility functions for commands"""

import asyncio
from typing import Optional, TypeVar, Coroutine, Any

import typer

from rich.console import Console

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.mcp.project_context import get_active_project
from basic_memory.project_marker import resolve_cli_project

console = Console()

T = TypeVar("T")


def run_with_cleanup(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine with proper database cleanup.

    This helper ensures database connections are cleaned up before the
    event loop closes, preventing process hangs in CLI commands.

    Args:
        coro: The coroutine to run

    Returns:
        The result of the coroutine
    """
    # Deferred: basic_memory.db pulls SQLAlchemy + Alembic, which must not load
    # at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.index.local_schedulers import drain_background_tasks

    async def _with_cleanup() -> T:
        try:
            return await coro
        finally:
            # Note writes materialize inline, but the follow-up work they
            # scheduled (vector sync, relation resolution) is still deferred:
            # cancelling it at loop close would leave semantic search and
            # inbound wikilinks stale until a later reindex.
            await drain_background_tasks()
            await db.shutdown_db()

    try:
        return asyncio.run(_with_cleanup())
    except db.NewerSchemaError as e:
        # Every DB-touching CLI verb funnels through here, so one catch turns
        # "older build over a newer DB" into the contract's error shape —
        # message on its own line, exit 1 (GAPS T11, W20 rule 6).
        typer.echo(str(e), err=True)
        raise typer.Exit(1)


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
