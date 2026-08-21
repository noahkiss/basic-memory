"""`bm web` — serve the read-only board, and install the unit that keeps it up.

Three verbs, and only one of them is used more than once:

- `bm web` runs the server in the foreground. That is what the systemd unit
  executes, and what a human runs to try it before installing anything.
- `bm web install` writes `bm-web.service` into the user's systemd directory,
  reloads, and starts it. A human runs this once per machine.
- `bm web uninstall` takes it back out.

**Nothing in this module may import fastapi, uvicorn, jinja2 or
`basic_memory.web` at module scope.** `cli/main.py` imports every command module
on every invocation, so an import here is an import on the fast path of every
other verb — the exact cost the native-command guard exists to keep off `bm ls`
(AGENTS.md, "Measured baseline"). The server's imports live inside the callback
that starts it, which pays them once, at boot, in a process that then runs for
weeks.

`install` and `uninstall` never open the database. They write and remove a text
file and talk to systemd; a broken index must not stop an operator from setting
the server up or taking it down.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from basic_memory.cli.app import app

# The board's port. Pinned rather than left to a framework default: a shared
# default is how two tools end up fighting over one socket on the same machine.
DEFAULT_WEB_PORT = 2749

# Localhost, always, by default. The board is every project on the machine with
# no authentication in front of it, so putting it on a routable interface is a
# decision the operator makes deliberately — with a reverse proxy or a tunnel,
# not with a flag they did not think about.
DEFAULT_WEB_HOST = "127.0.0.1"

UNIT_NAME = "bm-web.service"

# What a non-Linux machine gets. launchd is a later item; saying so is more
# useful than a stack trace about a missing `systemctl`.
UNSUPPORTED_PLATFORM = (
    "bm web install supports systemd user units only; "
    "run `bm web` in the foreground or add a launchd job"
)

web_app = typer.Typer(
    help="Serve the read-only board over every project on this machine.",
    # Bare `bm web` is the verb; the callback runs the server when no
    # sub-command was named.
    invoke_without_command=True,
)
app.add_typer(web_app, name="web")


def fail(message: str) -> typer.Exit:
    """One error line on stderr, exit 1, nothing on stdout (contract rule 6)."""
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- The unit file ---


def unit_directory() -> Path:
    """Where systemd looks for a user's own units.

    `$XDG_CONFIG_HOME` is honoured because systemd honours it: a machine that
    sets it and a tool that does not would write the unit somewhere systemd
    never reads, and the failure would look like "enable did nothing".
    """
    if config_home := os.getenv("XDG_CONFIG_HOME"):
        return Path(config_home) / "systemd" / "user"
    return Path.home() / ".config" / "systemd" / "user"


def render_unit(bm_executable: Path, port: int, home: Path) -> str:
    """The unit file's exact text.

    `Environment=HOME=` is not decoration: a user unit inherits a minimal
    environment, and every path `bm` resolves — the config directory, the store,
    the database — hangs off `HOME`. Without it the server would start against a
    different corpus than the shell does, silently.

    Pure and byte-exact so a test can assert the whole file rather than grep it.
    """
    return (
        "[Unit]\n"
        f"Description=bm web — Basic Memory board (localhost:{port})\n"
        "After=network.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"Environment=HOME={home}\n"
        f"ExecStart={bm_executable} web --host {DEFAULT_WEB_HOST} --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def resolve_executable() -> Path:
    """The `bm` the unit should exec.

    `shutil.which` first, because that is the binary the operator just typed and
    the one their PATH will keep resolving to. `sys.argv[0]` is the fallback for
    an install run out of a checkout or a venv that is not on PATH.

    The path is made absolute but **not** resolved through symlinks: a unit's
    `ExecStart` runs with systemd's PATH, not the shell's, so a relative entry
    would fail once the shell that wrote it exited — but the symlink is the
    stable spelling. A package manager's `bin/bm` points at a versioned
    install directory that the next upgrade deletes; writing the resolved
    target into the unit would break the service on every upgrade.
    """
    found = shutil.which("bm")
    candidate = Path(found if found else sys.argv[0]).absolute()
    if not candidate.is_file():
        raise fail(
            f"Error: cannot find the bm executable to run from the unit (looked at {candidate})"
        )
    return candidate


def run_systemctl(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Every `systemctl --user` call goes through here.

    One seam, so a test monkeypatches one function rather than the subprocess
    module — and so the `--user` flag cannot be forgotten at one call site.
    """
    return subprocess.run(
        ["systemctl", "--user", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def checked_systemctl(arguments: Sequence[str]) -> None:
    """Run systemctl and turn a non-zero exit into the contract's error shape."""
    result = run_systemctl(arguments)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else f"exit {result.returncode}"
        raise fail(f"Error: systemctl --user {' '.join(arguments)} failed: {reason}")


# --- The verbs ---


@web_app.callback()
def serve(
    ctx: typer.Context,
    host: Annotated[
        str,
        typer.Option("--host", help="Interface to bind. Localhost unless you mean otherwise."),
    ] = DEFAULT_WEB_HOST,
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = DEFAULT_WEB_PORT,
) -> None:
    """Serve the board in the foreground until interrupted.

    Read-only: no route writes anything, so this is safe to leave running beside
    a shell that is writing records. Exposing it beyond localhost is the
    operator's job — a reverse proxy or a tunnel, in front of this.
    """
    # A sub-command was named (`install`, `uninstall`); the callback's work is
    # only the bare spelling.
    if ctx.invoked_subcommand is not None:
        return

    # Deferred on purpose — see the module docstring. This is the one import in
    # the CLI tree allowed to pull fastapi, and it happens after dispatch.
    import uvicorn

    from basic_memory.web.app import create_app

    typer.echo(f"bm web listening on http://{host}:{port}/ — ctrl-c to stop")
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@web_app.command()
def install(
    port: Annotated[
        int, typer.Option("--port", help="Port to bake into the unit.")
    ] = DEFAULT_WEB_PORT,
    print_only: Annotated[
        bool,
        typer.Option("--print", help="Print the unit to stdout and write nothing."),
    ] = False,
) -> None:
    """Install and start the systemd user unit that serves the board.

    Idempotent: run it again after an upgrade and the unit is rewritten and the
    service restarted, rather than refused.
    """
    if sys.platform != "linux":
        raise fail(f"Error: {UNSUPPORTED_PLATFORM}")

    unit_text = render_unit(resolve_executable(), port, Path.home())
    if print_only:
        # Nothing is written and systemd is not spoken to: `--print` exists so an
        # operator can read the unit before letting the tool install it.
        typer.echo(unit_text, nl=False)
        return

    unit_path = unit_directory() / UNIT_NAME
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(unit_text, encoding="utf-8")

    checked_systemctl(["daemon-reload"])
    # Trigger: the service is already running, from an earlier install.
    # Why: `enable --now` on a running unit starts nothing, so an upgrade would
    #     leave the old process serving the old code with no sign anything was
    #     missed.
    # Outcome: restart it, so the unit just written is the one running.
    if run_systemctl(["is-active", "--quiet", UNIT_NAME]).returncode == 0:
        checked_systemctl(["restart", UNIT_NAME])
    else:
        checked_systemctl(["enable", "--now", UNIT_NAME])

    typer.echo(str(unit_path))
    typer.echo(f"http://{DEFAULT_WEB_HOST}:{port}/")


@web_app.command()
def uninstall() -> None:
    """Stop the board's unit, disable it, and remove the unit file."""
    if sys.platform != "linux":
        raise fail(f"Error: {UNSUPPORTED_PLATFORM}")

    unit_path = unit_directory() / UNIT_NAME
    # Nothing installed is a fact, not an error: `uninstall` twice in a row is
    # something an operator does, and the second run has nothing to report but
    # the state it found (contract rule 5).
    if not unit_path.is_file():
        typer.echo(f"no unit installed at {unit_path}")
        return

    checked_systemctl(["disable", "--now", UNIT_NAME])
    unit_path.unlink()
    checked_systemctl(["daemon-reload"])
    typer.echo(f"removed {unit_path}")
