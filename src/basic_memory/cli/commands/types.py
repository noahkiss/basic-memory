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

Scope follows GAPS W5-C: ``--project`` > nearest ``.bm.yml`` > every project,
one section each. The registry default retired from this read path — a
vocabulary report that silently covered one of five projects would teach an
agent the wrong rules for the four it did not mention.

Imports stay narrow: the verb needs a project's ``external_id``, so it pays for
the database, but nothing here may pull the API, the MCP tool layer, fastapi, or
dateparser onto its path (AGENTS.md, "Measured baseline";
``tests/cli/test_native_command_import_guard.py`` enforces it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.direct import direct_project_refs
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError
from basic_memory.vocabulary.glossary import (
    field_meaning,
    picking_question,
    relation_meaning,
    status_meaning,
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


def render_relations(vocabulary: Vocabulary) -> list[str]:
    """The edges this project allows, each with what writing it claims (GAPS U14).

    Printed with prose rather than as a bare list, unlike `statuses` and `areas`:
    a status name says what it means, and `derived_from` versus `relates_to` is
    exactly the choice an agent gets wrong without one line of guidance.
    """
    lines = ["relations"]
    if not vocabulary.relations:
        lines.append(f"  {NONE_DECLARED}")
        return lines

    width = max(len(name) for name in vocabulary.relations)
    for name in vocabulary.relations:
        meaning = relation_meaning(name)
        lines.append(f"  {name:<{width + LABEL_GAP}}{meaning}" if meaning else f"  {name}")
    return lines


def render_statuses(vocabulary: Vocabulary) -> list[str]:
    """The statuses this project declares, then prose for the ones that need it.

    The list stays one line, because status names are short and a reader scans
    them as a set. Only a name the glossary explains earns a line of its own —
    today that is `shelved` alone (GAPS U23), so a project that never declared it
    prints exactly what it printed before.
    """
    lines = ["statuses", f"  {', '.join(vocabulary.statuses) or NONE_DECLARED}"]
    explained = [name for name in vocabulary.statuses if status_meaning(name)]
    if not explained:
        return lines

    width = max(len(name) for name in explained)
    lines.extend(f"  {name:<{width + LABEL_GAP}}{status_meaning(name)}" for name in explained)
    return lines


def render(project_name: str, vocabulary: Vocabulary, path_note: str = "") -> str:
    """Render the whole report: type sections, then the rest of the vocabulary.

    ``path_note`` names the file the section came from. A pinned run leaves it
    empty and carries the path in its closing affordance instead; a roll-up puts
    it in each heading, because one trailing line cannot name five files.
    """
    heading = f"Record types for project '{project_name}'."
    if path_note:
        heading = f"Record types for project '{project_name}' ({path_note})."
    sections: list[list[str]] = [[heading]]
    sections.extend(render_type(name) for name in vocabulary.types)

    field_meanings = render_field_meanings(vocabulary.types)
    if field_meanings:
        sections.append(field_meanings)

    sections.append(render_statuses(vocabulary))
    sections.append(["areas", f"  {', '.join(vocabulary.areas) or NONE_DECLARED}"])
    sections.append(render_relations(vocabulary))
    sections.append(
        [
            "review-by",
            f"  A finding or a guide written without one gets "
            f"{vocabulary.review_months} months out.",
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
            help="Project to read. Defaults to .bm.yml, then every project.",
        ),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the next-step hints."),
    ] = False,
) -> None:
    """Explain the record types each project allows, and the fields each one carries.

    Read from each project's own vocabulary file, so a type a human added shows
    up here and a type they removed does not. Reports every project unless
    `--project` or a `.bm.yml` above the working directory pins one.
    """
    # Deferred: the vocabulary reader pulls PyYAML, which has no business on the
    # path of a command that does not read a vocabulary file.
    from basic_memory.vocabulary.model import load_vocabulary, vocabulary_path

    try:
        scope = resolve_read_scope(project)
    except MarkerError as exc:
        raise fail(f"Error: {exc}")

    try:
        refs = run_with_cleanup(direct_project_refs(scope.project))
        vocabularies = [(ref, load_vocabulary(ref.external_id)) for ref in refs]
    except typer.Exit:
        raise
    # ValueError covers both addressing failures here: an unknown project name,
    # and VocabularyError (a ValueError subclass) from a malformed file. A
    # malformed vocabulary is never degraded to "ungoverned" — see model.py.
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    # Trigger: an unscoped run against an empty registry.
    # Why: contract rule 5 — a well-scoped request whose answer is "nothing there"
    #      is a result, not a failure.
    # Outcome: state it and exit 0.
    if not refs:
        typer.echo("no projects registered")
        emit_notices(scope, quiet=quiet, command="types")
        return

    # A pinned run prints exactly what it always did, closing with the one path a
    # reader would edit. A roll-up cannot name five files in one trailing line, so
    # each section carries its own path in the heading instead (contract rule 4:
    # the payload holds the facts, the affordance holds the next step).
    pinned = scope.project is not None
    for position, (ref, vocabulary) in enumerate(vocabularies):
        if position:
            typer.echo("")
        path = vocabulary_path(ref.external_id)
        # Trigger: the project has no vocabulary.yml.
        # Why: an absent file means "not governed", never "use the defaults" (GAPS
        #     W4, decided 2026-08-10). Printing the default six here would teach an
        #     agent a vocabulary that nothing enforces.
        # Outcome: say so and move on; the file to create is named below.
        if vocabulary is None:
            note = f" ({path})" if not pinned else ""
            typer.echo(f"Project '{ref.name}' declares no record vocabulary{note}.")
            continue
        typer.echo(render(ref.name, vocabulary, "" if pinned else str(path)))

    if not quiet:
        if pinned and vocabularies[0][1] is None:
            typer.echo(f"Create {vocabulary_path(refs[0].external_id)} to declare one.")
        elif pinned:
            typer.echo(
                f"\nEdit {vocabulary_path(refs[0].external_id)} "
                "to add a type, status, area, relation, or field."
            )
        else:
            typer.echo(
                "\nEdit a project's vocabulary.yml to add a type, status, area, relation, or field."
            )
    emit_notices(scope, quiet=quiet, command="types")
