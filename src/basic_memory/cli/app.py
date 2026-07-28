# This prevents DEBUG logs from appearing on stdout during module-level
# initialization (e.g., template_loader.TemplateLoader() logs at DEBUG level).
from loguru import logger

logger.remove()

from typing import Optional  # noqa: E402

import typer  # noqa: E402

from basic_memory.cli.container import CliContainer, set_container  # noqa: E402
from basic_memory.config import init_cli_logging  # noqa: E402
import logfire  # noqa: E402


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


app = typer.Typer(name="basic-memory")


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

    command_name = ctx.invoked_subcommand or "root"

    # Trigger: a `brief` invocation — the session-start hook's front door.
    # Why: `brief` writes markdown to stdout that a harness splices straight into
    # an agent's context, and everything here — logging setup (Logfire loads
    # config), the span, the container — can raise SystemExit on a malformed
    # config (ConfigManager reports bad JSON that way). A broken config must
    # degrade to an empty brief rather than abort a session start.
    # Outcome: run that setup best-effort, swallowing (Exception, SystemExit) so a
    # broken config surfaces only inside the verb's own fail-open guard. Skip
    # global init. KeyboardInterrupt is left to propagate.
    if ctx.invoked_subcommand == "brief":
        try:
            init_cli_logging()
            ctx.with_resource(
                logfire.span(
                    f"cli.command.{command_name}",
                    entrypoint="cli",
                    command_name=command_name,
                )
            )
            container = CliContainer.create()
            set_container(container)
            # uvloop must own the event-loop policy before `brief` runs its query
            # through asyncio.run(), or a Postgres backend hits the asyncpg
            # engine-dispose race (#831/#877). No-op for SQLite, so brief startup
            # stays light.
            from basic_memory.db import maybe_install_uvloop

            maybe_install_uvloop(container.config)
        except (Exception, SystemExit):
            pass
        return

    # Initialize logging for CLI (file only, no stdout)
    init_cli_logging()
    ctx.with_resource(
        logfire.span(
            f"cli.command.{command_name}",
            entrypoint="cli",
            command_name=command_name,
        )
    )

    # --- Composition Root ---
    # Create container and read config (single point of config access)
    container = CliContainer.create()
    set_container(container)

    # Trigger: Postgres backend resolved at CLI startup, before any asyncio.run().
    # Why: uvloop must own the event-loop policy before the loop is created so the
    # asyncpg engine-dispose race (#831/#877) cannot fire. No-op for SQLite.
    # Outcome: subsequent asyncio.run() calls in CLI commands use uvloop on Postgres.
    from basic_memory.db import maybe_install_uvloop

    maybe_install_uvloop(container.config)

    # Run initialization for commands that don't use the API
    # Skip for 'mcp' command - it has its own lifespan that handles initialization
    # Skip for API-using commands (status, sync, etc.) - they handle initialization via deps.py
    # Skip for 'reset' command - it manages its own database lifecycle
    # Skip for 'man' - it only copies packaged files; a broken local database
    # must not block installing the offline docs
    # ('brief' returns above, before this point.)
    skip_init_commands = {
        "doctor",
        "man",
        "mcp",
        "status",
        "sync",
        "project",
        "config",
        "tool",
        "reset",
        "reindex",
        "watch",
    }
    if (
        not version
        and ctx.invoked_subcommand is not None
        and ctx.invoked_subcommand not in skip_init_commands
    ):
        from basic_memory.services.initialization import ensure_initialization

        ensure_initialization(container.config)


## import
# Register sub-command groups
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")

claude_app = typer.Typer(help="Import Conversations from Claude JSON export.")
import_app.add_typer(claude_app, name="claude")
