# This prevents DEBUG logs from appearing on stdout during module-level
# initialization (e.g., template_loader.TemplateLoader() logs at DEBUG level).
from loguru import logger

logger.remove()

from typing import Optional  # noqa: E402

import typer  # noqa: E402

from basic_memory.cli.container import CliContainer, set_container  # noqa: E402
from basic_memory.config import init_cli_logging  # noqa: E402


def version_callback(value: bool) -> None:
    """Show version and exit."""
    if value:
        import basic_memory

        # Trigger: no installed distribution, so the version is only the release fallback.
        # Why: that number is identical across every build between releases, so printing it bare
        # would claim a precision we do not have. Outcome: the reader is told to distrust it.
        suffix = "" if basic_memory.__version_from_metadata__ else " (source tree; not installed)"
        typer.echo(f"Basic Memory version: {basic_memory.__version__}{suffix}")
        raise typer.Exit()


# invoke_without_command: bare `bm` is a verb, not a usage error — the callback
# renders the project board when no subcommand was named (GAPS U37).
app = typer.Typer(name="basic-memory", invoke_without_command=True)


@app.callback()
def app_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Basic Memory - Local-first personal knowledge management."""

    # Trigger: a `brief` invocation — the session-start hook's front door.
    # Why: `brief` writes markdown to stdout that a harness splices straight into
    # an agent's context, and everything here — logging setup, the container — can
    # raise SystemExit on a malformed config (ConfigManager reports bad JSON that
    # way). A broken config must degrade to an empty brief rather than abort a
    # session start.
    # Outcome: run that setup best-effort, swallowing (Exception, SystemExit) so a
    # broken config surfaces only inside the verb's own fail-open guard. Skip
    # global init. KeyboardInterrupt is left to propagate.
    if ctx.invoked_subcommand == "brief":
        try:
            init_cli_logging()
            container = CliContainer.create()
            set_container(container)
        except (Exception, SystemExit):
            pass
        return

    # Initialize logging for CLI (file only, no stdout)
    init_cli_logging()

    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = CliContainer.create()
    set_container(container)

    # Run initialization for commands that don't use the API
    # Skip for 'mcp' command - it has its own lifespan that handles initialization
    # Skip for API-using commands (status, sync, etc.) - they handle initialization via deps.py
    # Skip for 'reset' command - it manages its own database lifecycle
    # Skip for 'man' - it only copies packaged files; a broken local database
    # must not block installing the offline docs
    # Skip for 'mine' - it reads Claude Code transcripts and never touches the
    # database, so paying for initialization would be the whole cost of the verb
    # Skip for the record read verbs 'ls', 'show' and 'path' - they reach the
    # repository layer directly and their own bootstrap calls
    # ensure_project_registry, so initialize_app here would be a second, slower
    # copy of work they already do (the shape 'doctor' and 'project' use).
    # Skip for 'undo' for the same reason: it restores files with git and then
    # reindexes them through its own bootstrap.
    # Skip for the record write verbs 'new', 'edit', 'done' and 'mark': each one
    # builds the local write stack, whose `direct_note_writer` already opens the
    # database and calls ensure_project_registry.
    # ('brief' returns above, before this point.)
    skip_init_commands = {
        # 'board' builds on the record-read direct path, whose own bootstrap
        # calls ensure_project_registry — same shape as 'ls' (bare `bm` reaches
        # it through the callback below and skips this gate by being None).
        "board",
        "doctor",
        "done",
        "edit",
        "history",
        "ls",
        "man",
        "mark",
        "mcp",
        "mine",
        "new",
        "path",
        "show",
        "status",
        "sync",
        # 'types' reaches the registry through `direct_project_refs`, which opens
        # the database and calls ensure_project_registry itself — running
        # initialize_app first was a second, slower copy of that work, and it was
        # the one call left that still bootstrapped a project at ~/basic-memory
        # under a native verb (GAPS U15).
        "types",
        "project",
        "config",
        "tool",
        "reset",
        "reindex",
        "undo",
        "watch",
        # 'web' covers all three spellings, because the gate keys on the group.
        # The server opens the database in its own lifespan, once, for the life
        # of the process — running initialize_app first would be a second copy
        # of that work before the server has even bound a socket. `web install`
        # and `web uninstall` must not open it at all: they write and remove a
        # unit file, and a broken index must never stop an operator from setting
        # the server up or taking it down (GAPS U41).
        "web",
    }
    if (
        not version
        and ctx.invoked_subcommand is not None
        and ctx.invoked_subcommand not in skip_init_commands
    ):
        from basic_memory.services.initialization import ensure_initialization

        ensure_initialization(container.config)

    # Bare `bm`: render the project board (GAPS U37). Routed to the same verb
    # `bm board` names, called directly with explicit arguments — the decorated
    # function's Typer defaults are OptionInfo objects, not values.
    if ctx.invoked_subcommand is None:
        from basic_memory.cli.commands.board import board

        board(project=None, quiet=False)


## import
# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer(help="Import Conversations from Claude JSON export.")
import_app.add_typer(claude_app, name="claude")
