"""Orphans command - show entities with no relations in the knowledge graph."""

from typing import Annotated, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients.knowledge import KnowledgeClient
from basic_memory.mcp.project_context import get_active_project
from basic_memory.project_marker import resolve_cli_project
from basic_memory.schemas.v2.graph import GraphNode


async def run_orphans(project: Optional[str] = None) -> tuple[str, list[GraphNode]]:
    """Fetch entities that have no relations in the knowledge graph."""
    project = resolve_cli_project(project)

    async with get_client() as client:
        project_item = await get_active_project(client, project, None)
        entities = await KnowledgeClient(client, project_item.external_id).get_orphans()
        return project_item.name, entities


@app.command()
def orphans(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
):
    """Show entities that have no relations in the knowledge graph.

    Orphan entities have no incoming or outgoing connections. These may indicate
    newly created notes not yet linked to other entities, or notes that have had
    their relations removed.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        _, entities = run_with_cleanup(run_orphans(project))
    except (ValueError, ToolError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.error(f"Error fetching orphan entities: {exc}")
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Title leads the row: it is what a caller reads the listing for, and the
    # file path follows so the note can be opened without a second lookup.
    title_width = max((len(entity.title) for entity in entities), default=0)
    path_width = max((len(entity.file_path) for entity in entities), default=0)
    for entity in entities:
        typer.echo(
            f"{entity.title:<{title_width}}  {entity.file_path:<{path_width}}  "
            f"{entity.note_type or ''}".rstrip()
        )
    typer.echo(f"{len(entities)} orphans")
