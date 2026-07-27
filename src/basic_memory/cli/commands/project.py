"""Command module for basic-memory project management."""

import json
import os
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import get_project_info, run_with_cleanup
from basic_memory.config import ConfigManager
from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.clients import ProjectClient
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink

console = Console()

# Create a project subcommand
project_app = typer.Typer(help="Manage multiple Basic Memory projects")
app.add_typer(project_app, name="project")


def format_path(path: str) -> str:
    """Format a path for display, using ~ for home directory."""
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)  # pragma: no cover
    return path


def make_bar(value: int, max_value: int, width: int = 40) -> Text:
    """Create a horizontal bar chart element using Unicode blocks."""
    if max_value == 0:
        return Text("░" * width, style="dim")
    filled = max(1, round(value / max_value * width)) if value > 0 else 0
    bar = Text()
    bar.append("█" * filled, style="cyan")
    bar.append("░" * (width - filled), style="dim")
    return bar


@project_app.command("list")
def list_projects(
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
) -> None:
    """List Basic Memory projects."""

    async def _list_projects() -> ProjectList:
        async with get_client() as client:
            return await ProjectClient(client).list_projects()

    try:
        config = ConfigManager().config
        result = run_with_cleanup(_list_projects())

        indexed_by_permalink: dict[str, ProjectItem] = {
            generate_permalink(project.name): project for project in result.projects
        }
        configured_names_by_permalink = {
            generate_permalink(project_name): project_name for project_name in config.projects
        }

        # Trigger: a project in config.projects was not returned by the project index.
        # Why: without a fallback such a project is invisible in `bm project list`,
        #   yet `bm project add` reads the DB and reports it already exists (#1003).
        #   The two commands must agree on whether a configured project exists.
        # Outcome: seed a row from the config entry so the project still renders.
        row_names_by_permalink = {
            permalink: project.name for permalink, project in indexed_by_permalink.items()
        }
        for permalink, project_name in configured_names_by_permalink.items():
            row_names_by_permalink.setdefault(permalink, project_name)

        default_permalink = (
            generate_permalink(config.default_project) if config.default_project else None
        )

        project_rows: list[dict] = []
        for permalink in sorted(
            row_names_by_permalink, key=lambda key: row_names_by_permalink[key]
        ):
            project_name = row_names_by_permalink[permalink]
            indexed_project = indexed_by_permalink.get(permalink)
            configured_name = configured_names_by_permalink.get(permalink)
            entry = config.projects.get(configured_name) if configured_name else None

            if indexed_project is not None:
                path = format_path(indexed_project.path)
            elif entry and entry.path:
                path = format_path(entry.path)
            else:
                path = ""

            project_rows.append(
                {
                    "name": project_name,
                    "permalink": permalink,
                    "path": path,
                    "is_default": permalink == default_permalink,
                }
            )

        # --- JSON output ---
        if json_output:
            print(json.dumps({"projects": project_rows}, indent=2, default=str))
            return

        # --- Rich table output ---
        table = Table(title="Basic Memory Projects")
        # Name must never lose its width: it is the identifier the user passes to
        # --project, and a row that renders as a bare path cannot be acted on.
        # Path used to be no_wrap, which let one long path claim the whole line and
        # squeeze every other column — Name included — to zero width in a
        # default-width terminal. "fold" alone wraps the path and leaves room.
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Path", style="yellow", overflow="fold")
        table.add_column("Default", style="magenta")

        for row_data in project_rows:
            table.add_row(
                row_data["name"],
                row_data["path"],
                "[X]" if row_data["is_default"] else "",
            )

        console.print(table)

        # Trigger: config.json registers a project that the project index did not return.
        # Why: config.json is this registry's source of truth — synchronize_projects
        #   creates index rows from it and deletes rows absent from it — so a project
        #   missing from the index is drift in the derived index, not a missing
        #   project. The row seeded above for #1003 renders such a project exactly
        #   like a healthy one, which hid the drift entirely: the project resolves
        #   for routing but has no indexed content behind it.
        # Outcome: name the drifting projects rather than letting the two registries
        #   disagree silently, and point at the command that reconciles them.
        unindexed = [
            project_name
            for permalink, project_name in sorted(configured_names_by_permalink.items())
            if permalink not in indexed_by_permalink
        ]
        if unindexed:
            console.print(f"[yellow]Configured but not indexed: {', '.join(unindexed)}[/yellow]")
            console.print(
                "[dim]These projects are in config.json with no row in the project "
                "index. Run 'bm reindex' to reconcile.[/dim]"
            )
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error listing projects: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: str = typer.Argument(..., help="Path to the project directory"),
    set_default: bool = typer.Option(False, "--default", help="Set as default project"),
) -> None:
    """Add a new project.

    Example:
        bm project add research ~/Documents/research
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

    async def _add_project():
        async with get_client() as client:
            data = {"name": name, "path": resolved_path, "set_default": set_default}
            return await ProjectClient(client).create_project(data)

    try:
        result = run_with_cleanup(_add_project())
        console.print(f"[green]{result.message}[/green]")

        # Trigger: the service made the new project the default without being asked —
        #     it is the first project, or it repaired a default named by neither
        #     config.json nor the database.
        # Why: the default is what every unqualified command targets, and a silent
        #     move means the next `bm` invocation writes somewhere the user did not
        #     choose. `bm project remove` then refuses, citing a default nobody set.
        # Outcome: the move is stated, with the command to put it back.
        if result.default and not set_default:
            console.print(f"[yellow]'{name}' is now the default project.[/yellow]")
            console.print("[dim]Change it with: bm project default <name>[/dim]")
    except Exception as e:
        console.print(f"[red]Error adding project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
    delete_notes: bool = typer.Option(
        False, "--delete-notes", help="Delete project files from disk"
    ),
) -> None:
    """Remove a project."""

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
        console.print(f"[green]{result.message}[/green]")
    except Exception as e:
        # str() of httpx transport errors is often empty (#1034) — never print a blank error.
        console.print(f"[red]Error removing project: {str(e) or repr(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as CLI default"),
) -> None:
    """Set the default project used as fallback when no project is specified."""

    async def _set_default():
        async with get_client() as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.set_default(target_project.external_id)

    try:
        result = run_with_cleanup(_set_default())
        console.print(f"[green]{result.message}[/green]")
    except Exception as e:
        console.print(f"[red]Error setting default project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("move")
def move_project(
    name: str = typer.Argument(..., help="Name of the project to move"),
    new_path: str = typer.Argument(..., help="New absolute path for the project"),
) -> None:
    """Move a project to a new filesystem location.

    This updates the project's configured path in the local database.
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

    async def _move_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            project_info = await project_client.resolve_project(name)
            return await project_client.update_project(
                project_info.external_id, {"path": resolved_path}
            )

    try:
        result = run_with_cleanup(_move_project())
        console.print(f"[green]{result.message}[/green]")

        # Show important file movement reminder
        console.print()  # Empty line for spacing
        console.print(
            Panel(
                "[bold red]IMPORTANT:[/bold red] Project configuration updated successfully.\n\n"
                "[yellow]You must manually move your project files from the old location to:[/yellow]\n"
                f"[cyan]{resolved_path}[/cyan]\n\n"
                "[dim]Basic Memory has only updated the configuration - your files remain in their original location.[/dim]",
                title="Manual File Movement Required",
                border_style="yellow",
                expand=False,
            )
        )

    except Exception as e:
        console.print(f"[red]Error moving project: {str(e)}[/red]")
        raise typer.Exit(1)


@project_app.command("ls")
def ls_project_command(
    name: str = typer.Option(..., "--name", help="Project name to list files from"),
    path: str = typer.Argument(None, help="Path within project (optional)"),
) -> None:
    """List files in a project.

    Examples:
      bm project ls --name research
      bm project ls --name research subfolder
    """

    def _list_local_files(project_path: str, subpath: str | None = None) -> list[str]:
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

        files: list[str] = []
        for file_path in sorted(target_dir.rglob("*")):
            if file_path.is_file():
                size = file_path.stat().st_size
                relative = file_path.relative_to(project_root).as_posix()
                files.append(f"{size:10d} {relative}")

        return files

    try:
        # Get project info
        async def _get_project():
            async with get_client() as client:
                projects_list = await ProjectClient(client).list_projects()
                for proj in projects_list.projects:
                    if generate_permalink(proj.name) == generate_permalink(name):
                        return proj
                return None

        project_data = run_with_cleanup(_get_project())
        if not project_data:
            console.print(f"[red]Error: Project '{name}' not found[/red]")
            raise typer.Exit(1)

        files = _list_local_files(project_data.path, path)

        if files:
            heading = f"\n[bold]Files in {name}"
            if path:
                heading += f"/{path}"
            heading += ":[/bold]"
            console.print(heading)
            for file in files:
                console.print(f"  {file}")
            console.print(f"\n[dim]Total: {len(files)} files[/dim]")
        else:
            prefix = f"[yellow]No files found in {name}"
            console.print(prefix + (f"/{path}" if path else "") + "[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@project_app.command("info")
def display_project_info(
    name: str = typer.Argument(..., help="Name of the project"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
):
    """Display detailed information and statistics about the current project."""
    try:
        # Get project info
        info = run_with_cleanup(get_project_info(name))

        if json_output:
            print(json.dumps(info.model_dump(), indent=2, default=str))
        else:
            # --- Left column: Knowledge Graph stats ---
            left = Table.grid(padding=(0, 2))
            left.add_column("metric", style="cyan")
            left.add_column("value", style="green", justify="right")

            left.add_row("[bold]Knowledge Graph[/bold]", "")
            left.add_row("Entities", str(info.statistics.total_entities))
            left.add_row("Observations", str(info.statistics.total_observations))
            left.add_row("Relations", str(info.statistics.total_relations))
            left.add_row("Unresolved", str(info.statistics.total_unresolved_relations))
            left.add_row("Isolated", str(info.statistics.isolated_entities))

            # --- Right column: Embeddings ---
            right = Table.grid(padding=(0, 2))
            right.add_column("property", style="cyan")
            right.add_column("value", style="green")

            right.add_row("[bold]Embeddings[/bold]", "")
            if info.embedding_status:
                es = info.embedding_status
                if not es.semantic_search_enabled:
                    right.add_row("[green]●[/green] Semantic Search", "Disabled")
                else:
                    right.add_row("[green]●[/green] Semantic Search", "Enabled")
                    if es.embedding_provider:
                        right.add_row("  Provider", es.embedding_provider)
                    if es.embedding_model:
                        right.add_row("  Model", es.embedding_model)
                    # Embedding coverage bar
                    if es.total_indexed_entities > 0:
                        coverage_bar = make_bar(
                            es.total_entities_with_chunks,
                            es.total_indexed_entities,
                            width=20,
                        )
                        count_text = Text(
                            f" {es.total_entities_with_chunks}/{es.total_indexed_entities}",
                            style="green",
                        )
                        bar_with_count = Text.assemble("  Indexed  ", coverage_bar, count_text)
                        right.add_row(bar_with_count, "")
                    right.add_row("  Chunks", str(es.total_chunks))
                    if es.reindex_recommended:
                        right.add_row(
                            "[yellow]●[/yellow] Status",
                            "[yellow]Reindex recommended[/yellow]",
                        )
                        if es.reindex_reason:
                            right.add_row("  Reason", f"[yellow]{es.reindex_reason}[/yellow]")
                    else:
                        right.add_row("[green]●[/green] Status", "[green]Up to date[/green]")

            # --- Compose two-column layout (content-sized, NOT Layout) ---
            columns = Table.grid(padding=(0, 4), expand=False)
            columns.add_row(left, right)

            # --- Note Types bar chart (top 5 by count) ---
            bars_section = None
            if info.statistics.note_types:
                sorted_types = sorted(
                    info.statistics.note_types.items(), key=lambda x: x[1], reverse=True
                )
                top_types = sorted_types[:5]
                max_count = top_types[0][1] if top_types else 1

                bars = Table.grid(padding=(0, 2), expand=False)
                bars.add_column("type", style="cyan", width=16, justify="right")
                bars.add_column("bar")
                bars.add_column("count", style="green", justify="right")

                for note_type, count in top_types:
                    bars.add_row(note_type, make_bar(count, max_count), str(count))

                remaining = len(sorted_types) - len(top_types)
                bars_section = Group(
                    "[bold]Note Types[/bold]",
                    bars,
                    f"[dim]+{remaining} more types[/dim]" if remaining > 0 else "",
                )

            # --- Footer ---
            current_time = (
                datetime.fromisoformat(str(info.system.timestamp))
                if isinstance(info.system.timestamp, str)
                else info.system.timestamp
            )
            footer = (
                f"[dim]{format_path(info.project_path)}  "
                f"default: {info.default_project}  "
                f"{current_time.strftime('%Y-%m-%d %H:%M')}[/dim]"
            )

            # --- Assemble dashboard ---
            parts: list = [columns, ""]
            if bars_section:
                parts.extend([bars_section, ""])
            parts.append(footer)
            body = Group(*parts)

            console.print(
                Panel(
                    body,
                    title=f"[bold]{info.project_name}[/bold]",
                    subtitle=f"Basic Memory {info.system.version}",
                    expand=False,
                )
            )

    except typer.Exit:
        raise
    except Exception as e:  # pragma: no cover
        typer.echo(f"Error getting project info: {e}", err=True)
        raise typer.Exit(1)
