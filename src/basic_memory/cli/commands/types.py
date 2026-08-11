"""`bm types` — explain the record types this project allows (GAPS W19 item 4).

Two sources meet here, and keeping them apart is the whole point of the verb:

- **The list of types comes from the project's live ``vocabulary.yml``.** Printing
  a hardcoded six would drift the first time a human adds a type or declares a
  field, which is the failure W19 item 4 names outright.
- **The prose comes from ``vocabulary/glossary.py``**, keyed by type name, and
  every lookup tolerates a miss. A type the glossary does not know still appears
  under its bare name; a type the glossary knows but the project does not declare
  never appears at all.

An ungoverned project — one with no ``vocabulary.yml`` — is a *result*, not an
error (output contract rule 5): say so plainly and exit 0.

Imports stay narrow: the verb needs a project's ``external_id``, so it pays for
the database, but nothing here may pull the API, the MCP tool layer, fastapi, or
dateparser onto its path (AGENTS.md, "Measured baseline";
``tests/cli/test_native_command_import_guard.py`` enforces it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.direct import direct_project_ref
from basic_memory.project_marker import MarkerError, resolve_cli_project
from basic_memory.vocabulary.glossary import (
    field_meaning,
    picking_question,
    type_fields,
    type_summary,
)

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.vocabulary.model import Vocabulary

# What a type declared only in the project's file gets instead of a summary.
# Saying "no description" is honest; inventing one would be the drift the verb
# exists to prevent.
UNKNOWN_TYPE_NOTE = "This project declares this type. bm carries no description for it."

NONE_DECLARED = "(this project declares none)"

# Two spaces after the widest label, so the values line up in one column
# (contract rule 1: alignment only, no box drawing).
LABEL_GAP = 2


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- Render ---


def render_type(name: str) -> list[str]:
    """One type's section: heading, what it is for, and the fields it carries."""
    question = picking_question(name)
    lines = [f"{name} — {question}" if question else name]

    summary = type_summary(name)
    if summary is None:
        lines.append(f"  {UNKNOWN_TYPE_NOTE}")
        return lines
    lines.append(f"  {summary}")

    fields = type_fields(name)
    if fields is None:
        return lines
    lines.append(f"  required  {', '.join(fields.required)}")
    if fields.optional:
        lines.append(f"  optional  {', '.join(fields.optional)}")
    return lines


def used_field_names(type_names: tuple[str, ...]) -> list[str]:
    """Every field the listed types use, in first-seen order, without repeats.

    Field meanings print once in their own section rather than under each type:
    ``id``/``permalink``/``title``/``source`` are on all six, and repeating four
    sentences six times buys nothing.
    """
    seen: dict[str, None] = {}
    for name in type_names:
        fields = type_fields(name)
        if fields is None:
            continue
        for field_name in (*fields.required, *fields.optional):
            seen.setdefault(field_name, None)
    return list(seen)


def render_field_meanings(type_names: tuple[str, ...]) -> list[str]:
    """The ``fields`` section: one field per line, name first."""
    names = [name for name in used_field_names(type_names) if field_meaning(name) is not None]
    if not names:
        return []

    width = max(len(name) for name in names)
    lines = ["fields"]
    lines.extend(f"  {name:<{width + LABEL_GAP}}{field_meaning(name)}" for name in names)
    return lines


def render_declared_fields(vocabulary: Vocabulary) -> list[str]:
    """The extras this project declared, with their kind and any enum values."""
    lines = ["declared fields"]
    if not vocabulary.fields:
        lines.append(f"  {NONE_DECLARED}")
        return lines

    names = sorted(vocabulary.fields)
    width = max(len(name) for name in names)
    for name in names:
        declared = vocabulary.fields[name]
        kind = declared.kind
        if declared.values:
            kind = f"{kind}: {', '.join(declared.values)}"
        lines.append(f"  {name:<{width + LABEL_GAP}}{kind}")
    return lines


def render(project_name: str, vocabulary: Vocabulary) -> str:
    """Render the whole report: type sections, then the rest of the vocabulary."""
    sections: list[list[str]] = [[f"Record types for project '{project_name}'."]]
    sections.extend(render_type(name) for name in vocabulary.types)

    field_meanings = render_field_meanings(vocabulary.types)
    if field_meanings:
        sections.append(field_meanings)

    sections.append(["statuses", f"  {', '.join(vocabulary.statuses) or NONE_DECLARED}"])
    sections.append(["areas", f"  {', '.join(vocabulary.areas) or NONE_DECLARED}"])
    sections.append(
        [
            "review-by",
            f"  Defaults to {vocabulary.review_months} months out on a finding and on a guide.",
        ]
    )
    sections.append(render_declared_fields(vocabulary))

    # Contract rule 3: the count closes the listing, on its own line.
    sections.append([f"{len(vocabulary.types)} types"])
    return "\n\n".join("\n".join(section) for section in sections)


# --- Verb ---


@app.command()
def types(
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Project to read. Defaults to .bm.yml, then the default project.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the next-step hints."),
    ] = False,
) -> None:
    """Explain the record types this project allows, and the fields each one carries.

    Read from the project's own vocabulary file, so a type a human added shows
    up here and a type they removed does not.
    """
    # Deferred: the vocabulary reader pulls PyYAML, which has no business on the
    # path of a command that does not read a vocabulary file.
    from basic_memory.vocabulary.model import load_vocabulary, vocabulary_path

    try:
        project_name = resolve_cli_project(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        # A None name here means the chain found no default; the direct helper
        # asks the registry again after bootstrapping it.
        ref = run_with_cleanup(direct_project_ref(project_name))
        vocabulary = load_vocabulary(ref.external_id)
    except typer.Exit:
        raise
    # ValueError covers both addressing failures here: an unknown project name,
    # and VocabularyError (a ValueError subclass) from a malformed file. A
    # malformed vocabulary is never degraded to "ungoverned" — see model.py.
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    path = vocabulary_path(ref.external_id)

    # Trigger: the project has no vocabulary.yml.
    # Why: an absent file means "not governed", never "use the defaults" (GAPS
    #     W4, decided 2026-08-10). Printing the default six here would teach an
    #     agent a vocabulary that nothing enforces.
    # Outcome: say so, name the file that would change it, exit 0.
    if vocabulary is None:
        typer.echo(f"Project '{ref.name}' declares no record vocabulary.")
        if not quiet:
            typer.echo(f"Create {path} to declare one.")
        return

    typer.echo(render(ref.name, vocabulary))
    if not quiet:
        typer.echo(f"\nEdit {path} to add a type, status, area, or field.")
