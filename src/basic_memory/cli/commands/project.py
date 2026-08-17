"""Command module for basic-memory project management."""

import os
from pathlib import Path

import typer

from basic_memory.cli.app import app
from basic_memory.cli.direct import direct_project_service
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink

# Create a project subcommand
project_app = typer.Typer(help="Manage multiple Basic Memory projects")
app.add_typer(project_app, name="project")


def format_path(path: str) -> str:
    """Format a path for display, using ~ for home directory."""
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)  # pragma: no cover
    return path


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


async def fetch_project_list() -> ProjectList:
    """Fetch the project registry via the direct service path.

    Native read commands talk to the service layer directly instead of routing
    through the in-process FastAPI app — the ASGI path costs ~2.5 CPU-seconds
    and ~100 MB per invocation just in imports (GAPS.md T18).
    """
    service = await direct_project_service()
    projects = await service.list_projects()
    default_project = await service.get_default_project_name()
    return ProjectList(
        projects=[
            ProjectItem(
                id=project.id,
                external_id=project.external_id,
                name=project.name,
                path=project.path,
                is_default=project.is_default or False,
            )
            for project in projects
        ],
        default_project=default_project,
    )


@project_app.command("list")
def list_projects(
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """List Basic Memory projects."""

    try:
        result = run_with_cleanup(fetch_project_list())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error listing projects: {e}")

    projects = sorted(result.projects, key=lambda project: project.name)
    # Name is the identifier callers pass to --project, so it leads the row and
    # keeps a fixed width; nothing else may push it out of column one.
    name_width = max((len(project.name) for project in projects), default=0)
    for project in projects:
        marker = "  (default)" if project.is_default else ""
        typer.echo(f"{project.name:<{name_width}}  {format_path(project.path)}{marker}")
    typer.echo(f"{len(projects)} projects")

    # The listing covers every project, so the notice does too — no marker walk,
    # because the payload ignored one (see `cli/notices.py`).
    emit_notices(ReadScope(project=None, origin="unscoped"), quiet=quiet, command="project list")


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: str = typer.Argument(..., help="Path to the project directory"),
    set_default: bool = typer.Option(False, "--default", help="Set as default project"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Add a new project.

    Example:
        bm project add research ~/Documents/research
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _add_project():
        async with get_client() as client:
            data = {"name": name, "path": resolved_path, "set_default": set_default}
            return await ProjectClient(client).create_project(data)

    try:
        result = run_with_cleanup(_add_project())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error adding project: {e}")

    typer.echo(result.message)

    # Trigger: the service made the new project the default without being
    #     asked — it is the first project in an empty registry.
    # Why: the default is what every unqualified command targets, and a silent
    #     move means the next `bm` invocation writes somewhere the user did not
    #     choose. `bm project remove` then refuses, citing a default nobody set.
    # Outcome: the move is stated, with the command to put it back.
    if result.default and not set_default and not quiet:
        typer.echo(f"'{name}' is now the default project.")
        typer.echo("Change it with: bm project default <name>")


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
    delete_notes: bool = typer.Option(
        False, "--delete-notes", help="Delete project files from disk"
    ),
) -> None:
    """Remove a project."""
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _remove_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.delete_project(
                target_project.external_id, delete_notes=delete_notes
            )

    try:
        result = run_with_cleanup(_remove_project())
    except typer.Exit:
        raise
    except Exception as e:
        # str() of httpx transport errors is often empty (#1034) — never print a blank error.
        raise fail(f"Error removing project: {str(e) or repr(e)}")

    typer.echo(result.message)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as CLI default"),
) -> None:
    """Set the project that bm uses when a command names no project."""
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _set_default():
        async with get_client() as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.set_default(target_project.external_id)

    try:
        result = run_with_cleanup(_set_default())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error setting default project: {e}")

    typer.echo(result.message)


@project_app.command("move")
def move_project(
    name: str = typer.Argument(..., help="Name of the project to move"),
    new_path: str = typer.Argument(..., help="New absolute path for the project"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Point a project at a new location on disk.

    The command updates the stored path only. It moves no files.
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _move_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            project_info = await project_client.resolve_project(name)
            return await project_client.update_project(
                project_info.external_id, {"path": resolved_path}
            )

    try:
        result = run_with_cleanup(_move_project())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error moving project: {e}")

    typer.echo(result.message)

    # The command moves configuration only; the files stay where they were, so
    # a caller that stops reading here would leave the project pointing at an
    # empty directory.
    if not quiet:
        typer.echo("Only the configuration moved — the files are still in the old location.")
        typer.echo(f"Move the project files to: {resolved_path}")


@project_app.command("ls")
def ls_project_command(
    name: str = typer.Option(..., "--name", help="Project name to list files from"),
    path: str = typer.Argument(None, help="Path within project (optional)"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """List files in a project.

    Examples:
      bm project ls --name research
      bm project ls --name research subfolder
    """

    def _list_local_files(project_path: str, subpath: str | None = None) -> list[tuple[str, int]]:
        project_root = Path(project_path).expanduser().resolve()
        target_dir = project_root

        if subpath:
            requested = Path(subpath)
            if requested.is_absolute():
                raise ValueError("Path must be relative to the project root")
            target_dir = (project_root / requested).resolve()
            if not target_dir.is_relative_to(project_root):
                raise ValueError("Path must stay within the project root")

        if not target_dir.exists():
            raise ValueError(f"Path not found: {target_dir}")
        if not target_dir.is_dir():
            raise ValueError(f"Path is not a directory: {target_dir}")

        return [
            (file_path.relative_to(project_root).as_posix(), file_path.stat().st_size)
            for file_path in sorted(target_dir.rglob("*"))
            if file_path.is_file()
        ]

    async def _get_project():
        projects_list = await fetch_project_list()
        for proj in projects_list.projects:
            if generate_permalink(proj.name) == generate_permalink(name):
                return proj
        return None

    try:
        project_data = run_with_cleanup(_get_project())
        if not project_data:
            raise fail(f"Error: Project '{name}' not found")
        files = _list_local_files(project_data.path, path)
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error: {e}")

    path_width = max((len(relative) for relative, _ in files), default=0)
    for relative, size in files:
        typer.echo(f"{relative:<{path_width}}  {size}")
    typer.echo(f"{len(files)} files")

    # `--name` is mandatory here, so the read is pinned by the verb's own shape.
    emit_notices(
        ReadScope(project=project_data.name, origin="flag"), quiet=quiet, command="project ls"
    )


@project_app.command("info")
def display_project_info(
    name: str = typer.Argument(..., help="Name of the project"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
):
    """Show the settings, the counts, and the system details for one project."""
    # Deferred for the reason above: `project info` is the one read here that
    # routes through the API client, and it must not tax the native verbs.
    from basic_memory.cli.commands import command_utils

    try:
        info = run_with_cleanup(command_utils.get_project_info(name))
    except typer.Exit:
        raise
    except Exception as e:  # pragma: no cover
        raise fail(f"Error getting project info: {e}")

    statistics = info.statistics
    system = info.system

    typer.echo("Project")
    typer.echo(f"name: {info.project_name}")
    typer.echo(f"path: {format_path(info.project_path)}")
    typer.echo(f"default project: {info.default_project}")

    typer.echo("")
    typer.echo("Statistics")
    typer.echo(f"entities: {statistics.total_entities}")
    typer.echo(f"observations: {statistics.total_observations}")
    typer.echo(f"relations: {statistics.total_relations}")
    typer.echo(f"unresolved relations: {statistics.total_unresolved_relations}")
    typer.echo(f"isolated entities: {statistics.isolated_entities}")
    for note_type, count in sorted(
        statistics.note_types.items(), key=lambda item: (-item[1], item[0])
    ):
        typer.echo(f"note type {note_type}: {count}")

    embeddings = info.embedding_status
    if embeddings:
        typer.echo("")
        typer.echo("Embeddings")
        typer.echo(
            f"semantic search: {'enabled' if embeddings.semantic_search_enabled else 'disabled'}"
        )
        if embeddings.semantic_search_enabled:
            typer.echo(f"provider: {embeddings.embedding_provider or ''}")
            typer.echo(f"model: {embeddings.embedding_model or ''}")
            typer.echo(
                f"indexed entities: {embeddings.total_entities_with_chunks}"
                f"/{embeddings.total_indexed_entities}"
            )
            typer.echo(f"chunks: {embeddings.total_chunks}")

    typer.echo("")
    typer.echo("System")
    typer.echo(f"version: {system.version}")
    typer.echo(f"database: {system.database_path}")
    typer.echo(f"database size: {system.database_size}")
    typer.echo(f"timestamp: {system.timestamp}")

    # A stale vector index answers semantic queries from content that no longer
    # exists, so the recommendation is a notice with the command that clears it.
    if embeddings and embeddings.reindex_recommended and not quiet:
        reason = f" — {embeddings.reindex_reason}" if embeddings.reindex_reason else ""
        typer.echo(f"Reindex recommended{reason}")
        typer.echo(f"Run 'bm reindex --project {info.project_name}' to rebuild the index.")

    # The argument is mandatory, so this read is pinned by the verb's own shape.
    emit_notices(
        ReadScope(project=info.project_name, origin="flag"), quiet=quiet, command="project info"
    )
