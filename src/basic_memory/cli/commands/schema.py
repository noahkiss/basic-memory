"""Schema management CLI commands for Basic Memory.

Provides CLI access to schema validation, inference, and drift detection.
Registered as a subcommand group: `bm schema validate`, `bm schema infer`, `bm schema diff`.

The three `render_*_report` functions are the single rendering of each report
(docs/OUTPUT_CONTRACT.md v2): both these commands and `bm tool schema-*` call
them, so the two entry points cannot drift apart.
"""

from collections.abc import Sequence
from typing import Annotated, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.project_registry import lookup_project
from basic_memory.project_marker import resolve_cli_project

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).

schema_app = typer.Typer(help="Schema management commands")
app.add_typer(schema_app, name="schema")


def _resolve_project_name(project: Optional[str]) -> Optional[str]:
    """Resolve project name: CLI argument > .bm.yml marker > registry default."""
    if project is not None:
        project_name, _ = lookup_project(project)
        if not project_name:
            typer.echo(f"No project found named: {project}", err=True)
            raise typer.Exit(1)
        return project_name
    return resolve_cli_project(None)


# --- Rendering helpers ---


def _aligned(rows: Sequence[Sequence[str]]) -> list[str]:
    """Join each row into a line, padding every column to a common width.

    Alignment only — the contract forbids box-drawing tables (rule 1).
    """
    if not rows:
        return []
    widths = [max(len(cell) for cell in column) for column in zip(*rows)]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows
    ]


def _rendered_reason(report: dict) -> bool:
    """Print the empty-answer reason, if any, and report whether it stood in for a payload.

    An empty answer is a result, not a failure (contract rule 5). The schema
    tools set `reason` only on paths that carry no payload, so a reason always
    replaces the rendering rather than accompanying it.
    """
    reason = report.get("reason")
    if not reason:
        return False
    print(reason)
    return True


def _validation_status(result: dict) -> str:
    if not result.get("passed", True):
        return "fail"
    return "warn" if result.get("warnings") else "pass"


def render_validate_report(report: dict, quiet: bool = False) -> None:
    """Render a validation report to stdout.

    `quiet` is accepted for a uniform renderer signature; validation emits no
    notices or affordances, so it changes nothing here.
    """
    if _rendered_reason(report):
        return

    results = report.get("results", [])
    rows = [(result.get("note_identifier", ""), _validation_status(result)) for result in results]
    for line, result in zip(_aligned(rows), results):
        print(line)
        for warning in result.get("warnings", []):
            print(f"  warning: {warning}")
        for error in result.get("errors", []):
            print(f"  error: {error}")

    print(
        f"{report.get('valid_count', 0)}/{report.get('total_notes', 0)} valid, "
        f"{report.get('warning_count', 0)} warnings, {report.get('error_count', 0)} errors"
    )


def render_infer_report(report: dict, quiet: bool = False) -> None:
    """Render an inference report to stdout."""
    if _rendered_reason(report):
        return

    notes_analyzed = report.get("notes_analyzed", 0)
    # Zero notes carries no `reason` from the tool, but it is the same kind of
    # empty answer: nothing to infer from, exit 0.
    if notes_analyzed == 0:
        print(f"No notes found with type: {report.get('note_type', '')}")
        return

    for line in _aligned(
        [
            (freq["name"], freq["source"], str(freq["count"]), f"{freq['percentage']:.0%}")
            for freq in report.get("field_frequencies", [])
        ]
    ):
        print(line)

    suggested_schema = report.get("suggested_schema") or {}
    if suggested_schema:
        print("Suggested schema:")
        for key, value in suggested_schema.items():
            print(f"  {key}: {value}")

    print(f"{notes_analyzed} notes analyzed")

    if not quiet:
        print(
            "--save not yet implemented. "
            f"Copy the schema above into schema/{report.get('note_type', '')}.md"
        )


def _render_drift_section(heading: str, rows: Sequence[Sequence[str]]) -> None:
    """Print a drift section, or nothing when it is empty — absence is the signal."""
    if not rows:
        return
    print(f"{heading} ({len(rows)}):")
    for line in _aligned(rows):
        print(f"  {line}")


def render_diff_report(report: dict, quiet: bool = False) -> None:
    """Render a drift report to stdout.

    `quiet` is accepted for a uniform renderer signature; drift emits no
    notices or affordances, so it changes nothing here.
    """
    if _rendered_reason(report):
        return

    new_fields = report.get("new_fields", [])
    dropped_fields = report.get("dropped_fields", [])
    cardinality_changes = report.get("cardinality_changes", [])

    if not (new_fields or dropped_fields or cardinality_changes):
        print(f"No drift detected for {report.get('note_type', '')} schema.")
        return

    def field_rows(fields: Sequence[dict]) -> list[tuple[str, str, str]]:
        return [
            (field["name"], f"{field['percentage']:.0%} of notes", field["source"])
            for field in fields
        ]

    _render_drift_section("New fields", field_rows(new_fields))
    _render_drift_section("Dropped fields", field_rows(dropped_fields))
    # Cardinality changes arrive as prose already led by the field name
    # (picoschema/diff.py), so they are one column, not three.
    _render_drift_section("Cardinality changes", [(change,) for change in cardinality_changes])


# --- Commands ---


@schema_app.command()
def validate(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Note path or note type to validate"),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    strict: bool = typer.Option(False, "--strict", help="Exit with error on validation failures"),
):
    """Validate notes against their schemas.

    TARGET can be a note path (e.g., people/ada-lovelace.md) or a note type
    (e.g., person). If omitted, validates all notes that have schemas.

    Use --strict to exit with error code 1 if any validation errors are found.
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_validate as mcp_schema_validate

    try:
        project_name = _resolve_project_name(project)

        # Heuristic: if target contains / or ., treat as identifier; otherwise as note type
        note_type, identifier = None, None
        if target:
            if "/" in target or "." in target:
                identifier = target
            else:
                note_type = target

        result = run_with_cleanup(
            mcp_schema_validate(
                note_type=note_type,
                identifier=identifier,
                project=project_name,
                output_format="json",
            )
        )

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)

        if "error" in result:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)

        render_validate_report(result)

        # --strict turns "the notes are wrong" into a failing exit for callers
        # that gate on it; the report itself already rendered on stdout.
        if strict and result.get("error_count", 0) > 0:
            typer.echo(f"strict: {result['error_count']} errors", err=True)
            raise typer.Exit(1)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema validate: {e}")
            typer.echo(f"Error during schema validate: {e}", err=True)
            raise typer.Exit(1)
        raise


@schema_app.command()
def infer(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to analyze (e.g., person, meeting)"),
    ],
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    threshold: float = typer.Option(
        0.25, "--threshold", help="Minimum frequency for optional fields (0-1)"
    ),
    save: bool = typer.Option(False, "--save", help="Save inferred schema to schema/ directory"),
    quiet: bool = typer.Option(False, "--quiet", help="Drop notices and affordances"),
):
    """Infer schema from existing notes of a type.

    Analyzes all notes with the given type and suggests a Picoschema
    definition based on observation and relation frequency.

    Fields present in 95%+ of notes become required. Fields above the
    threshold (default 25%) become optional. Fields below threshold are excluded.
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_infer as mcp_schema_infer

    try:
        project_name = _resolve_project_name(project)

        result = run_with_cleanup(
            mcp_schema_infer(
                note_type=note_type,
                threshold=threshold,
                project=project_name,
                output_format="json",
            )
        )

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)

        if "error" in result:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)

        # The --save affordance is the only notice here, so it is the only
        # thing --quiet has to drop; without --save there is nothing to say.
        render_infer_report(result, quiet=quiet or not save)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema infer: {e}")
            typer.echo(f"Error during schema infer: {e}", err=True)
            raise typer.Exit(1)
        raise


@schema_app.command()
def diff(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to check for drift"),
    ],
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
):
    """Show drift between schema and actual usage.

    Compares the existing schema definition against how notes of that type
    are actually structured. Identifies new fields,
    dropped fields, and cardinality changes.
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_diff as mcp_schema_diff

    try:
        project_name = _resolve_project_name(project)

        result = run_with_cleanup(
            mcp_schema_diff(
                note_type=note_type,
                project=project_name,
                output_format="json",
            )
        )

        # output_format="json" guarantees a dict return
        assert isinstance(result, dict)

        if "error" in result:
            typer.echo(f"Error: {result['error']}", err=True)
            raise typer.Exit(1)

        render_diff_report(result)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            logger.error(f"Error during schema diff: {e}")
            typer.echo(f"Error during schema diff: {e}", err=True)
            raise typer.Exit(1)
        raise
