"""Database management commands."""

# PEP 563 lazy annotations let signatures reference IndexProgress without importing
# the indexing stack at module load; reset/reindex import their heavy database and
# indexing dependencies at call time so CLI startup stays fast (#886).
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

import psutil
import typer
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.config import ConfigManager

console = Console()


def _is_basic_memory_mcp(cmdline: list[str]) -> bool:
    """Heuristic: does this argv represent a `basic-memory mcp` server?

    The MCP server can be launched any of:
      basic-memory mcp
      bm mcp                                  # entrypoint alias from pyproject.toml
      python -m basic_memory.cli.main mcp     # module form
      uv run basic-memory mcp / uv run bm mcp # uv wrappers
      /abs/path/to/{bm,basic-memory}[.exe] mcp

    A reliable match needs both signals:
      1. "mcp" appears as an exact argv token (not "mcp-foo").
      2. Some argv token names the basic-memory entrypoint — either by
         hyphen/underscore form, or as a `bm` script (covers `/usr/local/bin/bm`,
         `bm.exe`, etc. via Path.stem).
    """
    if "mcp" not in cmdline:
        return False
    for arg in cmdline:
        if "basic-memory" in arg or "basic_memory" in arg:
            return True
        # Try both POSIX and Windows path interpretations so a test on
        # macOS still recognizes `C:\\...\\bm.exe`, and a real Windows
        # run still recognizes `/usr/local/bin/bm`. Path() alone uses
        # the host OS, which gives wrong stems for foreign separators.
        if PurePosixPath(arg).stem == "bm" or PureWindowsPath(arg).stem == "bm":
            return True
    return False


def _find_live_mcp_processes() -> list[tuple[int, str]]:
    """Return (pid, joined_cmdline) for live `basic-memory mcp` processes.

    Why this exists (issue #765):
        On POSIX, `Path.unlink()` removes the directory entry but the inode
        survives as long as any process holds the file open. A `bm reset`
        run while Claude Desktop (or another MCP client) is alive will
        therefore "succeed" — but the still-running MCP keeps reading the
        old, now-invisible memory.db inode and returns phantom rows. On
        Windows the OS naturally raises PermissionError on `unlink()`, so
        the bug is POSIX-specific. We detect proactively to give the same
        error experience on every platform before doing damage.

    The current process is excluded so this can be called from inside a
    `bm reset` invocation. NoSuchProcess / AccessDenied are swallowed
    because process tables race with the scan and we don't want a
    transient permission error to mask a real zombie.
    """
    me = os.getpid()
    matches: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = proc.info.get("pid")
            if pid is None or pid == me:
                continue
            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue
            if _is_basic_memory_mcp(cmdline):
                matches.append((pid, " ".join(cmdline)))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return matches


def _abort_if_mcp_processes_alive() -> None:
    """Refuse `bm reset` while basic-memory MCP processes are still running.

    See _find_live_mcp_processes for the underlying POSIX-vs-Windows
    rationale. Prints a per-PID list and platform-appropriate cleanup
    instructions, then exits non-zero so destructive work never starts.
    """
    zombies = _find_live_mcp_processes()
    if not zombies:
        return

    console.print("[red]Refusing to reset:[/red] basic-memory MCP processes are still running.")
    console.print(
        "[yellow]On macOS/Linux these would keep reading the deleted memory.db inode "
        "and return phantom search results (see #765).[/yellow]"
    )
    for pid, cmd in zombies:
        console.print(f"  PID {pid}: {cmd}")
    console.print("\n[bold]How to clean up:[/bold]")
    console.print("  1. Quit Claude Desktop and any other MCP clients.")
    if os.name == "nt":
        console.print(
            "  2. Verify nothing remains: "
            "[green]Get-CimInstance Win32_Process | "
            "Where-Object {$_.CommandLine -like '*basic-memory*mcp*'}[/green]"
        )
    else:
        console.print("  2. Verify nothing remains: [green]pgrep -fa 'basic-memory mcp'[/green]")
    console.print("  3. Re-run [green]bm reset[/green].")
    raise typer.Exit(1)


async def _unflushed_note_content(session_maker) -> list[tuple[str, str, str]]:
    """(project_name, file_path, status) for note_content rows not yet on disk.

    While a row is pending/writing/failed, the database row is the ONLY copy
    of that note — the markdown file has not been written (T12). Everything
    else in the database is derivable from the files; these rows are not.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from basic_memory import db

    try:
        async with db.scoped_session(session_maker) as session:
            result = await session.execute(
                text(
                    "SELECT p.name, n.file_path, n.file_write_status "
                    "FROM note_content n JOIN project p ON p.id = n.project_id "
                    "WHERE n.file_write_status IN ('pending', 'writing', 'failed') "
                    "ORDER BY p.name, n.file_path"
                )
            )
            return [(row[0], row[1], row[2]) for row in result.fetchall()]
    except OperationalError as e:
        # Reset reads run with ensure_migrations=False (T11), so a fresh DB may
        # not have these tables — and then cannot hold unflushed notes either.
        # Anything else (locked, I/O error) must still refuse loudly.
        if "no such table" in str(e):
            return []
        raise


async def _flush_unflushed_note_content(app_config) -> list[tuple[str, str, str]]:
    """Flush accepted-but-unwritten notes to disk; return what still remains.

    Runs the same per-project recovery sweep as startup and `bm reindex`
    (recover_project_materializations), then re-queries. A non-empty return
    means flushing failed and deleting the database would destroy content.
    """
    from basic_memory import db
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import recover_project_materializations

    # ensure_migrations=False: this DB is about to be deleted, and migrating it
    # can be fatal — a newer build's stamp raises NewerSchemaError, whose own
    # advertised way out is this very reset (GAPS T11).
    _, session_maker = await db.get_or_create_db(
        db_path=app_config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
        ensure_migrations=False,
    )
    rows = await _unflushed_note_content(session_maker)
    if not rows:
        return []

    affected_projects = {project_name for project_name, _, _ in rows}
    project_repository = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        projects = await project_repository.get_active_projects(session)
    for project in projects:
        if project.name in affected_projects:
            await recover_project_materializations(project, session_maker)

    return await _unflushed_note_content(session_maker)


def _abort_or_warn_unflushed(unflushed: list[tuple[str, str, str]], force: bool) -> None:
    """Refuse to reset over unrecoverable note content, unless forced."""
    if not unflushed:
        return

    console.print(
        "[red]Refusing to reset:[/red] accepted note writes have not reached disk "
        "and could not be flushed. Deleting the database would destroy them:"
    )
    for project_name, file_path, status in unflushed:
        console.print(f"  [cyan]{project_name}[/cyan]/{file_path} [yellow]({status})[/yellow]")
    if not force:
        console.print(
            "\nFix the write errors (see logs), or re-run with [green]--force[/green] "
            "to reset anyway and lose this content."
        )
        raise typer.Exit(1)
    console.print("[yellow]--force given: continuing; the content above is lost.[/yellow]")


@dataclass(slots=True)
class EmbeddingProgress:
    """Typed CLI progress payload for embedding backfills."""

    entity_id: int
    completed: int
    total: int


async def _reindex_projects(app_config):
    """Reindex all projects in a single async context.

    This ensures all database operations use the same event loop,
    and proper cleanup happens when the function completes.
    """
    # Deferred: SQLAlchemy, repositories, and the indexing stack load only when a
    # reindex actually runs, not on every CLI start (#886).
    from basic_memory import db
    from basic_memory.repository import ProjectRepository
    from basic_memory.services.initialization import ensure_project_registry
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )

    try:
        await ensure_project_registry(app_config)

        # Get database session (migrations already run if needed)
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository()
        async with db.scoped_session(session_maker) as session:
            projects = await project_repository.get_active_projects(session)

        for project in projects:
            console.print(f"  Indexing [cyan]{project.name}[/cyan]...")
            logger.info(f"Starting project index for project: {project.name}")
            result = await run_local_project_index_for_project(
                project,
                runtime_factory=LocalProjectIndexRuntimeFactory(),
                force_full=True,
            )
            logger.info(
                "Project index completed",
                project_name=project.name,
                total_files=result.total_files,
                enqueued_files=result.enqueued_files,
                enqueued_batches=result.enqueued_batches,
                deleted_files=result.deleted_files,
            )
    finally:
        # Clean up database connections before event loop closes
        await db.shutdown_db()


async def _snapshot_registry(app_config) -> tuple[list[dict], Optional[str]]:
    """Read the project registry out of the database before it is deleted.

    The database owns the registry (GAPS B2), so dropping the file drops the
    project list with it. Reset is an index rebuild, not a de-registration, so
    the rows are captured here and rewritten after migrations.
    """
    from sqlalchemy.exc import OperationalError

    from basic_memory import db
    from basic_memory.repository import ProjectRepository

    try:
        # ensure_migrations=False: same reasoning as _flush_unflushed_note_content —
        # never migrate (or refuse over) a database the reset is about to delete.
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
            ensure_migrations=False,
        )
        try:
            async with db.scoped_session(session_maker) as session:
                projects = await ProjectRepository().get_active_projects(session)
        except OperationalError as e:
            # Fresh unmigrated DB (T11: reset reads skip migrations): no project
            # table means no registry to snapshot. Anything else still raises.
            if "no such table" in str(e):
                return [], None
            raise
        rows = [
            {
                "name": project.name,
                "path": project.path,
                "permalink": project.permalink,
                "external_id": project.external_id,
                "is_active": True,
            }
            for project in projects
        ]
        default_name = next((p.name for p in projects if p.is_default), None)
        return rows, default_name
    finally:
        await db.shutdown_db()


async def _restore_registry(app_config, rows: list[dict], default_name: Optional[str]) -> None:
    """Rewrite the captured registry rows into the freshly migrated database."""
    from basic_memory import db
    from basic_memory.repository import ProjectRepository

    repository = ProjectRepository()
    try:
        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        async with db.scoped_session(session_maker) as session:
            for row in rows:
                created = await repository.create(session, row)
                if row["name"] == default_name:
                    await repository.set_as_default(session, created.id)
    finally:
        await db.shutdown_db()


@app.command()
def reset(
    reindex: bool = typer.Option(
        False, "--reindex", help="Read the files on disk again after the reset"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Skip both pre-flight checks. The command then resets while "
            "basic-memory MCP processes run, and while accepted notes have "
            "not reached disk. The second case loses those notes. Use this "
            "only in automated work where you know neither case applies."
        ),
    ),
):  # pragma: no cover
    """Delete the local database and create it again.

    The markdown files on disk are not changed.
    """
    # Deferred: SQLAlchemy and the db module load only when a reset actually
    # runs, not on every CLI start (#886).
    from sqlalchemy.exc import OperationalError

    from basic_memory import db

    console.print(
        "[yellow]Note:[/yellow] This deletes the index database. Markdown files on "
        "disk are not affected; notes accepted but not yet written to disk are "
        "flushed first, and the reset refuses if any cannot be flushed.\n"
        "Use [green]bm reset --reindex[/green] to automatically rebuild the index afterward."
    )
    if typer.confirm("Reset the database index?"):
        # Pre-flight: refuse to proceed if MCP processes still hold the DB
        # file open. POSIX would silently let us unlink the inode while
        # they keep reading it; Windows would error here anyway. See
        # _find_live_mcp_processes for the full story. --force is the
        # documented escape hatch for scripted/CI runs.
        if not force:
            _abort_if_mcp_processes_alive()

        logger.info("Resetting database...")
        config_manager = ConfigManager()
        app_config = config_manager.config

        # T12 guard: while a note_content row is pending/writing/failed, the
        # database holds the only copy of that note. Flush to disk before any
        # file is unlinked; refuse (listing what would be lost) if flushing
        # leaves anything behind. --force proceeds and accepts the loss.
        unflushed = run_with_cleanup(_flush_unflushed_note_content(app_config))
        _abort_or_warn_unflushed(unflushed, force)

        # The registry lives in the database being deleted; capture it first.
        registry_rows, registry_default = run_with_cleanup(_snapshot_registry(app_config))

        # Get database path
        db_path = app_config.app_database_path

        # Delete the database file and WAL files if they exist
        for suffix in ["", "-shm", "-wal"]:
            path = db_path.parent / f"{db_path.name}{suffix}"
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Deleted: {path}")
                except OSError as e:
                    console.print(
                        f"[red]Error:[/red] Cannot delete {path.name}: {e}\n"
                        "The database may be in use by another process (e.g., MCP server).\n"
                        "Please close Claude Desktop or any other Basic Memory clients and try again."
                    )
                    raise typer.Exit(1)

        # Create a new empty database, then rewrite the captured registry
        try:
            run_with_cleanup(db.run_migrations(app_config))
        except OperationalError as e:
            if "disk I/O error" in str(e) or "database is locked" in str(e):
                console.print(
                    "[red]Error:[/red] Cannot access database. "
                    "It may be in use by another process (e.g., MCP server).\n"
                    "Please close Claude Desktop or any other Basic Memory clients and try again."
                )
                raise typer.Exit(1)
            raise

        run_with_cleanup(_restore_registry(app_config, registry_rows, registry_default))
        console.print("[green]Database reset complete[/green]")

        if reindex:
            # No empty-registry branch: _reindex_projects bootstraps the registry
            # first, so even a reset on a fresh install has a project to index.
            console.print("Rebuilding search index...")
            # Note: _reindex_projects has its own cleanup, but run_with_cleanup
            # ensures db.shutdown_db() is called even if _reindex_projects changes
            run_with_cleanup(_reindex_projects(app_config))
            console.print("[green]Reindex complete[/green]")


@app.command()
def reindex(
    embeddings: bool = typer.Option(
        False, "--embeddings", "-e", help="Rebuild the vector embeddings; needs semantic search"
    ),
    search: bool = typer.Option(False, "--search", "-s", help="Rebuild the full-text search index"),
    full: bool = typer.Option(
        False,
        "--full",
        help="Read every file again, not only the files that changed",
    ),
    project: str = typer.Option(
        None, "--project", "-p", help="Reindex one project. The default is every project"
    ),
):  # pragma: no cover
    """Rebuild the search index and the vector embeddings, without deleting the database.

    If you give no flag, the command rebuilds both. If semantic search is off, it
    rebuilds the search index only. Use --search or --embeddings to rebuild one side.
    Use --full to read every file again and to embed every eligible note again.

    Examples:
        bm reindex                  # Project index + embeddings
        bm reindex --full           # Full project index + full re-embed
        bm reindex --embeddings     # Only rebuild vector embeddings
        bm reindex --search         # Only run project index
        bm reindex --full --search  # Full project index only
        bm reindex --full --embeddings  # Full re-embed only
        bm reindex -p claw --full   # Full reindex for only the 'claw' project
    """
    # If neither flag is set, do both
    if not embeddings and not search:
        embeddings = True
        search = True

    config_manager = ConfigManager()
    app_config = config_manager.config

    if embeddings and not app_config.semantic_search_enabled:
        console.print(
            "[yellow]Semantic search is not enabled.[/yellow] "
            "Set [cyan]semantic_search_enabled: true[/cyan] in config to use embeddings."
        )
        embeddings = False
        if not search:
            raise typer.Exit(0)

    run_with_cleanup(
        _reindex(app_config, search=search, embeddings=embeddings, full=full, project=project)
    )


async def _reindex(
    app_config,
    *,
    search: bool,
    embeddings: bool,
    full: bool,
    project: str | None,
):
    """Run reindex operations."""
    # Deferred: SQLAlchemy, repositories, and the indexing stack load only when a
    # reindex actually runs, not on every CLI start (#886).
    from basic_memory import db
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )
    from basic_memory.repository import EntityRepository, ProjectRepository
    from basic_memory.repository.search_repository import create_search_repository
    from basic_memory.services.initialization import (
        ensure_project_registry,
        recover_project_materializations,
    )
    from basic_memory.services.search_service import SearchService
    from basic_memory.services.file_service import FileService
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.entity_parser import EntityParser

    try:
        await ensure_project_registry(app_config)

        _, session_maker = await db.get_or_create_db(
            db_path=app_config.database_path,
            db_type=db.DatabaseType.FILESYSTEM,
        )
        project_repository = ProjectRepository()
        async with db.scoped_session(session_maker) as session:
            projects = await project_repository.get_active_projects(session)

        if project:
            projects = [p for p in projects if p.name == project]
            if not projects:
                console.print(f"[red]Project '{project}' not found.[/red]")
                raise typer.Exit(1)

        for proj in projects:
            console.print(f"\n[bold]Project: [cyan]{proj.name}[/cyan][/bold]")

            if search:
                # Trigger: the project-index scan below reconciles deletes against
                # the filesystem, and a crash can leave an accepted note stuck
                # mid-materialization with its markdown file never written.
                # Why: scanning first would treat the missing file as a delete and
                # destroy the entity plus its accepted content — data loss of an
                # acknowledged write.
                # Outcome: run the same per-project recovery sweep as startup so
                # stuck materializations are re-driven to disk before the scan.
                await recover_project_materializations(proj, session_maker)

                search_mode_label = "full project index" if full else "project index"
                console.print(
                    f"  Rebuilding full-text search index ([cyan]{search_mode_label}[/cyan])..."
                )
                result = await run_local_project_index_for_project(
                    proj,
                    runtime_factory=LocalProjectIndexRuntimeFactory(),
                    force_full=full,
                    # The full-text search rebuild must never embed: the explicit
                    # embeddings phase below owns vector (re)builds. Passing the CLI
                    # flag here would double-embed on a full reindex — the inline
                    # project-index sync, then reindex_vectors discarding and
                    # rebuilding it — so callers pay the embedding cost twice.
                    embeddings=False,
                )
                console.print(
                    "  [dim]project index: "
                    f"{result.total_files} observed, "
                    f"{result.enqueued_files} indexed, "
                    f"{result.deleted_files} deleted, "
                    f"{result.enqueued_batches} batches[/dim]"
                )

                console.print("  [green]done[/green] Full-text search index rebuilt")

            if embeddings:
                embedding_mode_label = "full rebuild" if full else "incremental sync"
                console.print(
                    f"  Building vector embeddings ([cyan]{embedding_mode_label}[/cyan])..."
                )
                entity_repository = EntityRepository(project_id=proj.id)
                search_repository = create_search_repository(
                    session_maker, project_id=proj.id, app_config=app_config
                )
                project_path = Path(proj.path)
                entity_parser = EntityParser(project_path)
                markdown_processor = MarkdownProcessor(entity_parser, app_config=app_config)
                file_service = FileService(project_path, markdown_processor, app_config=app_config)
                search_service = SearchService(
                    search_repository,
                    entity_repository,
                    file_service,
                    session_maker=session_maker,
                )

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    console=console,
                ) as progress:
                    task = progress.add_task("  Embedding entities...", total=None)

                    def on_progress(entity_id, index, total):
                        embedding_progress = EmbeddingProgress(
                            entity_id=entity_id,
                            completed=index,
                            total=total,
                        )
                        # Trigger: repository progress now reports terminal entity completion.
                        # Why: operators need to see finished embedding work rather than
                        # entities merely entering prepare.
                        # Outcome: the CLI bar advances steadily with real completed work.
                        progress.update(
                            task,
                            total=embedding_progress.total,
                            completed=embedding_progress.completed,
                        )

                    stats = await search_service.reindex_vectors(
                        progress_callback=on_progress,
                        force_full=full,
                    )
                    progress.update(task, completed=stats["total_entities"])

                console.print(
                    f"  [green]done[/green] Embeddings complete: "
                    f"{stats['embedded']} entities embedded, "
                    f"{stats['skipped']} skipped, "
                    f"{stats['errors']} errors"
                )

        console.print("\n[green]Reindex complete![/green]")
    finally:
        await db.shutdown_db()
