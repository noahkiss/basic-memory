"""`bm orphans` — the notes nothing links to and that link to nothing.

Scope follows GAPS W5-C: `--project` > nearest `.bm.yml` > every project. The
registry default retired from this read path — an unmarked working directory now
reports every project, one section each, rather than one arbitrary project.
`orphans` is a report, and a report that silently covered one of five projects
is the failure W5-C exists to remove.
"""

from typing import Annotated, Optional, Sequence

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.schemas.v2.graph import GraphNode


async def run_orphans(
    projects: Optional[Sequence[str]] = None,
) -> list[tuple[str, list[GraphNode]]]:
    """Fetch entities with no relations, one entry per project in scope.

    ``projects`` names the projects to report. ``None`` means every registered
    project, which is what an unscoped read resolves to (GAPS W5-C).
    """
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and `cli/main.py` imports this module for every invocation —
    # a module-level import would put that cost on every native verb (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.mcp.clients.knowledge import KnowledgeClient
    from basic_memory.mcp.project_context import get_active_project

    # One client for every project in scope — reconnecting per project would pay
    # the ASGI setup cost once per section.
    async with get_client() as client:
        project_client = ProjectClient(client)

        if projects is None:
            listed = await project_client.list_projects()
            return [
                (item.name, await KnowledgeClient(client, item.external_id).get_orphans())
                for item in listed.projects
            ]

        results: list[tuple[str, list[GraphNode]]] = []
        for name in projects:
            project_item = await get_active_project(client, name, None)
            entities = await KnowledgeClient(client, project_item.external_id).get_orphans()
            results.append((project_item.name, entities))
        return results


def render_project(entities: list[GraphNode]) -> list[str]:
    """One project's rows and its count."""
    # Title leads the row: it is what a caller reads the listing for, and the
    # file path follows so the note can be opened without a second lookup.
    title_width = max((len(entity.title) for entity in entities), default=0)
    path_width = max((len(entity.file_path) for entity in entities), default=0)
    lines = [
        f"{entity.title:<{title_width}}  {entity.file_path:<{path_width}}  "
        f"{entity.note_type or ''}".rstrip()
        for entity in entities
    ]
    # Contract rule 3: the count closes the listing, on its own line.
    lines.append(f"{len(entities)} orphans")
    return lines


@app.command()
def orphans(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the status lines and next-step hints."),
    ] = False,
):
    """List the notes that have no links to or from other notes.

    A note is an orphan when nothing links to it and it links to nothing. A new
    note is often an orphan until you link it to the rest of the graph. Reports
    every project unless `--project` or a `.bm.yml` above the working directory
    pins one.
    """
    from basic_memory.cli.runner import run_with_cleanup

    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        scope = resolve_read_scope(project)
        projects = None if scope.project is None else [scope.project]
        reports = run_with_cleanup(run_orphans(projects))
    except (ValueError, ToolError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:  # pragma: no cover
        logger.error(f"Error fetching orphan entities: {exc}")
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Trigger: an unscoped run against an empty registry.
    # Why: contract rule 5 — a well-scoped request whose answer is "nothing there"
    #      is a result, and silence would read as a graph with no orphans in it.
    # Outcome: state it and exit 0.
    if not reports:
        typer.echo("no projects registered")
        emit_notices(scope, quiet=quiet, command="orphans")
        return

    # A pinned run prints exactly what it always did: rows, then the count. Only a
    # roll-up labels its sections, because only a roll-up has something to
    # disambiguate (contract rule 1).
    for position, (project_name, entities) in enumerate(reports):
        if position:
            typer.echo("")
        if scope.project is None:
            typer.echo(f"project: {project_name}")
        for line in render_project(entities):
            typer.echo(line)

    emit_notices(scope, quiet=quiet, command="orphans")
