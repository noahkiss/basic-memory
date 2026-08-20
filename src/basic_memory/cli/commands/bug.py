"""`bm bug` — file a bug report about bm itself (GAPS U34).

The verb an agent (or the user) reaches for the moment bm misbehaves: one
command, no project required, and the report lands in the configured
`bugs_dir` with the context a later reader needs — version, platform,
harness fingerprint, and the tail of the invocation log. Where that
directory syncs to is the user's business (`bugs_followup`); see
`basic_memory.bugs`.

Native and deliberately DB-free: a bug report must be writable when the
database is the thing that is broken.
"""

from __future__ import annotations

from typing import Annotated

import typer

from basic_memory.cli.app import app


@app.command(name="bug")
def bug(
    message: Annotated[
        str,
        typer.Argument(
            help='What happened, e.g. "bm undo restored the wrong file — expected X, got Y".'
        ),
    ],
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Print only the report path."),
    ] = False,
) -> None:
    """File a bug report about bm. Include the command, what you expected, what happened.

    A repeat of an already-reported failure bumps that report's count instead
    of creating a new file. Reports carry the last ~20 bm invocations from
    this machine's command log.
    """
    from basic_memory import bugs

    text = message.strip()
    if not text:
        typer.echo("Error: an empty report helps nobody — say what happened.", err=True)
        raise typer.Exit(1)

    settings = bugs.load_bug_config()
    try:
        path, created = bugs.write_report(text, command="bug", kind="reported", config=settings)
    except Exception as exc:  # noqa: BLE001 — the one verb where the failure IS the payload
        typer.echo(f"Error: could not write the report: {exc}", err=True)
        raise typer.Exit(1)

    typer.echo(str(path))
    if not quiet:
        if created:
            typer.echo("report filed — it is reviewed from the bugs directory, not from bm")
        else:
            typer.echo("known failure — bumped the existing report's count instead of a new file")
    bugs.run_followup(settings)
