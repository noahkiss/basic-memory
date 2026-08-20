"""What a record-writing verb hands the accepted-note path (verbs items E and F).

`bm new`, `bm edit`, `bm done` and `bm mark` all need the same four things, and
none of them exist anywhere else in the tree:

- **A note whose file path is chosen, not derived.** `EntitySchema.file_path` is
  computed from `directory` + `safe_title` (`schemas/base.py`), which cannot
  produce `<type-dir>/<id>--<slug>.md`. `RecordNote` states the path instead, so
  a record lands where `.forked/schema.md` §8 says it lands and keeps that path
  when its title later changes.
- **Frontmatter written as text, not as a metadata dict.** The `permalink` a
  record declares is honoured byte-for-byte only when it arrives inside the
  note's own frontmatter block (`services/note_preparation.py`), and
  `permalink == id` is the identity the whole record schema rests on (§2).
- **An id nothing else holds.** `vocabulary/ids.py` allocates against a
  *synchronous* predicate; the only honest collision check is a database lookup,
  so the loop lives here and reuses that module's draw, its attempt count, and
  its error.
- **Identity verified after resolution** (GAPS T9/T10): BM's resolver matches on
  title and file path too, so a row that came back is not by itself the row that
  was asked for.

Nothing here prints, and nothing here decides policy — which types may be
edited, what a status means, what a verb says afterwards all belong to the verb.

**Imported late, on purpose.** `cli/main.py` imports every command module on
every invocation, so the verbs import this module inside their command bodies:
it pulls the Pydantic schema layer, which the `--version` floor and the pure-read
verbs must not pay for (AGENTS.md, "Measured baseline").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import yaml
from pydantic import computed_field

from basic_memory.project_marker import resolve_cli_project
from basic_memory.schemas.base import Entity as EntitySchema

from basic_memory.vocabulary.ids import (
    MAX_ID_ATTEMPTS,
    SEPARATOR,
    TYPE_DIRS,
    IdAllocationError,
    is_record_id,
    new_record_id,
    record_slug,
)
from basic_memory.vocabulary.model import DEFAULT_VOCABULARY, Vocabulary

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.models import Project

# The heading a record's relations live under (`.forked/schema.md` §5/§12).
RELATIONS_HEADING = "## Relations"

# One outgoing edge: the relation type, and the id of the record it points at.
type Relation = tuple[str, str]

# The type an undeclared proposal is filed as — W4's escape hatch, and the one
# type a governed project cannot do without (GAPS E2).
INBOX_TYPE = "inbox"

# What a record says its content came from when a human authored it at the
# prompt (VERBS_PLAN D7). A real `file#L1-L4` reference is what the migration
# workflow passes; making `--source` mandatory would only make every interactive
# write invent one.
DEFAULT_SOURCE = "cli"

# A project with no `vocabulary.yml` is not governed, so the checker never runs
# and every record it holds is written unchecked (GAPS W4). Stated once per
# write, because silence here reads as approval.
UNGOVERNED_NOTICE = (
    "note: this project declares no vocabulary, so records are written "
    "unchecked — run 'bm types' to see what a governed project declares"
)


class RecordResolutionError(LookupError):
    """No record in the project carries the requested id."""


class RecordNote(EntitySchema):
    """A note whose file path is stated rather than derived from its title.

    Two rules need this and neither can be met by the base schema:

    - a new record lands at `<type-dir>/<id>--<slug>.md` (`.forked/schema.md` §8),
      which no title-derived path can produce;
    - an edited record keeps the path it was created at, because the file name
      carries an id that other files link by, while the title is mutable on
      every type `bm edit` accepts.
    """

    # Project-relative, POSIX-style — the form `Entity.file_path` is stored and
    # compared in everywhere in this tree.
    record_file_path: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def file_path(self) -> str:
        return self.record_file_path


@dataclass(frozen=True, slots=True)
class WriteProject:
    """The project one write lands in, resolved once."""

    name: str
    external_id: str
    project_id: int
    path: str


@dataclass(frozen=True, slots=True)
class ExistingRecord:
    """One already-written record, located and identity-verified."""

    record_id: str
    entity_external_id: str
    note_type: str
    file_path: str
    title: str
    metadata: Mapping[str, Any]

    @property
    def status(self) -> str | None:
        value = self.metadata.get("status")
        return value if isinstance(value, str) else None


# --- Which project a write lands in ---


# What a write says when the registry holds no project it can land in. Naming
# `--governed` is the point of the line: an ungoverned project writes records
# unchecked (GAPS W4), so the message that creates a user's first project should
# not steer them into the shape `bm doctor` will then complain about. Nothing
# bootstraps a project on this path any more (GAPS U15) — this message is the
# whole recovery, so it has to be runnable as written.
NO_PROJECT_MESSAGE = "no project — run 'bm project add <name> --governed'"


def write_project_name(explicit: Optional[str]) -> str:
    """The project a record write goes to: `--project`, marker, then the default.

    Writes keep `project_marker`'s chain, which ends at the default project.
    Reads gave that tail up (GAPS W5-C) and cover every project when nothing
    pins them — a write cannot, because it needs one home.

    Raises ValueError when nothing names a project, which an empty registry is
    the only way to reach. `MarkerError` is a ValueError too, so one `except`
    at the verb covers an unusable marker as well.
    """
    name = resolve_cli_project(explicit)
    if not name:
        raise ValueError(NO_PROJECT_MESSAGE)
    return name


# --- Where a record's file goes ---


def record_directory(note_type: str) -> str:
    """The directory records of ``note_type`` live in.

    The closed seven get their plural directory from `vocabulary/ids.py`. A type a
    human added to a project's `vocabulary.yml` has no entry there and gets a
    directory under its own name: a declared type is legal to write (GAPS W4), so
    refusing it a home would make the extension mechanism unusable, and inventing
    a plural would guess at English.
    """
    return TYPE_DIRS.get(note_type, note_type)


def record_path(note_type: str, record_id: str, title: str) -> str:
    """Where a new record lands: ``<type-dir>/<id>--<slug>.md`` (§8)."""
    return f"{record_directory(note_type)}/{record_id}{SEPARATOR}{record_slug(title)}.md"


@dataclass(frozen=True, slots=True)
class ResolvedType:
    """What one requested type resolves to on the write path (GAPS W4, U25)."""

    # The type the record is written and stamped as — never an alias.
    note_type: str
    # The undeclared name an inbox record proposes, when the hatch fired.
    proposed_type: str | None = None
    # The alias the writer typed, when one resolved. Notice material only.
    alias_of: str | None = None


def declared_types(vocabulary: Vocabulary | None) -> tuple[str, ...]:
    """The types a project can be measured against.

    An ungoverned project has no declared list, so the closed set is the only
    yardstick it has.
    """
    return vocabulary.types if vocabulary is not None else tuple(TYPE_DIRS)


def resolve_note_type(
    requested: str, vocabulary: Vocabulary | None, *, project: str
) -> ResolvedType:
    """Resolve the requested type: declared name, alias, or the inbox hatch.

    A declared type wins outright (the parser refuses an alias that shadows
    one, so the two never compete). An alias resolves to its canonical type and
    the record stamps that type, never the alias (GAPS U25) — the vocabulary
    stays closed; the reaching just lands. An ungoverned project resolves
    against the default aliases the same way it is measured against the closed
    set.

    Everything else is the W4 escape hatch, never an error: the record is filed
    as `inbox` carrying `proposed-type: <requested>`, which is what makes
    `bm doctor`'s "N inbox records propose type X" report non-empty. Agents
    propose a type; only a human enables one.

    Trigger: the project's vocabulary declares no `inbox` type (GAPS E2).
    Why: the hatch files the record as `inbox`, and the checker then rejects a
        type the project does not declare — so the write would fail one layer
        down with a message about `inbox`, a type the author never asked for.
        Writing it anyway is worse: the content would land unchecked or not at
        all, which is the drop the hatch exists to prevent.
    Outcome: refuse here, before an id is spent or a file is written, naming the
        project and the fix. The vocabulary is the human's to shape, so the verb
        states the consequence rather than the tree forbidding the edit.
    """
    allowed = declared_types(vocabulary)
    if requested in allowed:
        return ResolvedType(note_type=requested)

    aliases = vocabulary.aliases if vocabulary is not None else DEFAULT_VOCABULARY.aliases
    target = aliases.get(requested)
    # The parser guarantees a target is declared; the guard is for the
    # ungoverned path, where the default aliases meet the closed set instead of
    # a validated file.
    if target is not None and target in allowed:
        return ResolvedType(note_type=target, alias_of=requested)

    if INBOX_TYPE not in allowed:
        raise ValueError(
            f"'{requested}' is not a type project '{project}' declares, and its vocabulary "
            f"declares no '{INBOX_TYPE}' type to file the proposal as — add "
            f"'{INBOX_TYPE}' to its vocabulary.yml or pick a declared type; "
            f"run 'bm types' to see the set"
        )
    return ResolvedType(note_type=INBOX_TYPE, proposed_type=requested)


# --- Editing a body by hand ---

# `$VISUAL` before `$EDITOR`: the long-standing convention is that `$VISUAL`
# names a full-screen editor and `$EDITOR` a line editor, and a full-screen one
# is what a markdown body wants.
_EDITOR_VARS = ("VISUAL", "EDITOR")


def body_from_editor(text: str) -> str:
    """Open the user's editor on ``text`` and return what they saved.

    Written out rather than taken from `click.edit`: this tree depends on typer,
    which does not re-export `edit`, and reaching past it into click would add an
    undeclared dependency for one call.

    The temporary file carries a `.md` suffix so the editor highlights the note
    as markdown. With no editor configured there is nothing to open, so the text
    comes back unchanged — a verb that silently wrote nothing would be worse.
    """
    import os
    import shlex
    import subprocess
    import tempfile
    from pathlib import Path

    editor = next((os.environ[name] for name in _EDITOR_VARS if os.environ.get(name)), "")
    if not editor:
        return text

    with tempfile.NamedTemporaryFile("w+", suffix=".md", encoding="utf-8", delete=False) as handle:
        handle.write(text)
        scratch = Path(handle.name)
    try:
        # check=True: an editor that exited non-zero did not save what the caller
        # meant, and writing the pre-edit text back would discard their work
        # silently. The verb turns the error into one stderr line.
        subprocess.run([*shlex.split(editor), str(scratch)], check=True)
        return scratch.read_text(encoding="utf-8")
    finally:
        scratch.unlink(missing_ok=True)


# --- The edges a record carries (GAPS U14) ---


def relation_line(relation: Relation) -> str:
    """One `## Relations` bullet: the form the markdown parser reads as an edge."""
    name, target = relation
    return f"- {name} [[{target}]]"


def parse_relations(values: Sequence[str]) -> tuple[Relation, ...]:
    """Turn every `--rel <type>:<id>` argument into the edge it writes.

    Shape only. Whether the type is one the project declares and whether the
    target exists are both questions about a project, and they are answered where
    the project is open — here there is neither a vocabulary nor a session.

    The id is checked against `is_record_id` for the reason `--supersedes` checks
    it: the edge is written as `[[<value>]]` and resolves by permalink, so a value
    that cannot be a permalink lands as a dangling relation that reads as a real
    edge until `bm doctor` reports it (GAPS E1).
    """
    relations: list[Relation] = []
    for value in values:
        name, separator, target = value.partition(":")
        name, target = name.strip(), target.strip()
        if not separator or not name or not target:
            raise ValueError(f"--rel takes '<type>:<id>', got '{value}'")
        if not is_record_id(target):
            raise ValueError(f"--rel takes a record id after the type, got '{target}'")
        relations.append((name, target))
    return tuple(relations)


def check_relation_types(
    relations: Sequence[Relation], vocabulary: Vocabulary | None, *, project: str
) -> None:
    """Refuse a relation type the project's vocabulary does not declare.

    An absent vocabulary means ungoverned, never "use the defaults" (GAPS W4), so
    there is nothing to measure the edge against and it is written unchecked —
    the rule `bm mark` already follows for a status.

    **Enforced at the flag, not as a `vocabulary/checker.py` rule.** The checker's
    relation rules read a record's *parsed* relation types, and an inline
    `[[…]]` anywhere in a body parses to a `links_to` relation
    (`markdown/plugins.py`). A checker rule over that list would therefore reject
    every note whose body links to another note — the closed vocabulary governs
    what a verb may *write*, not how prose may link.
    """
    if vocabulary is None:
        return
    for name, _ in relations:
        if name not in vocabulary.relations:
            raise ValueError(
                f"'{name}' is not a relation type project '{project}' declares. "
                f"Allowed values: {', '.join(vocabulary.relations)}."
            )


def append_relations(body: str, relations: Sequence[Relation]) -> str:
    """Add relation bullets to a body, under its `## Relations` heading.

    The heading is created at the end of the body when the record has none, which
    is where `record_markdown` puts it. When one is already there the bullets join
    it rather than starting a second section — two `## Relations` headings in one
    file is not a shape anything in this tree writes or expects to read.

    A bullet the body already carries is skipped: `bm edit --rel` run twice is a
    re-run, not a request for a duplicate edge.
    """
    lines = body.rstrip().splitlines()
    wanted = [
        relation_line(relation)
        for relation in relations
        if relation_line(relation) not in {line.strip() for line in lines}
    ]
    if not wanted:
        return body

    span = relations_span(lines)
    if span is None:
        return "\n".join([*lines, "", RELATIONS_HEADING, *wanted]) + "\n"

    # Insert at the end of the existing section rather than at the end of the
    # file: a hand-edited record may carry prose after its relations, and a
    # bullet stranded under a later heading is not an edge that section owns.
    _, end = span
    return "\n".join([*lines[:end], *wanted, *lines[end:]]) + "\n"


def carry_relations(previous: str, replacement: str) -> str:
    """Keep a record's `## Relations` section across a wholesale body replacement (GAPS U17).

    `bm edit --body` replaces the prose. The edges live in that same body as
    bullets under `## Relations`, so a replacement that said nothing about them
    used to drop every one — silently, because a relation that no longer exists
    is not a dangling relation and `bm doctor` cannot see it. An edit that
    restates the prose is not a statement about the edges, which is the rule
    `--rel` already follows in the other direction by appending rather than
    replacing.

    Trigger: the replacement body already carries a `## Relations` heading.
    Why: the caller wrote the section by hand, so it says what they mean, and
        carrying the old one over would leave two headings in one file — a shape
        nothing in this tree writes or reads.
    Outcome: the replacement stands as written and nothing is carried.

    The carried section lands at the end of the new prose. Its position in the
    old body is not preserved, because the prose it sat between is gone.
    """
    prose = replacement.rstrip().splitlines()
    if relations_span(prose) is not None:
        return replacement

    span = relations_span(previous.rstrip().splitlines())
    if span is None:
        return replacement

    start, end = span
    section = previous.rstrip().splitlines()[start:end]
    return "\n".join([*prose, "", *section] if prose else section) + "\n"


def relations_span(lines: Sequence[str]) -> tuple[int, int] | None:
    """The half-open line range a body's `## Relations` section occupies, or None.

    The section runs from its heading to its last bullet. Any other non-blank
    line — a later heading, or prose — ends it, so a record that carries text
    after its edges keeps that text outside the span.
    """
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == RELATIONS_HEADING), None
    )
    if start is None:
        return None
    end = start + 1
    for index in range(start + 1, len(lines)):
        if _is_bullet(lines[index]):
            end = index + 1
        elif lines[index].strip():
            break
    return start, end


def _is_bullet(line: str) -> bool:
    """True for a list item, which is the only line shape a relations section holds."""
    return line.lstrip().startswith("- ")


# --- What a record's file says ---


def record_markdown(
    frontmatter_fields: Mapping[str, str],
    body: str,
    *,
    relations: Sequence[Relation] = (),
) -> str:
    """Render one record's file: its frontmatter block, its body, its relations.

    The frontmatter is written as *text* rather than handed over as a metadata
    dict because that is the only input the permalink resolver honours verbatim
    (`services/note_preparation.py`): a permalink it derives instead would be
    slugified and project-prefixed, and `permalink == id` would stop being true.

    Key order is the caller's insertion order and never sorted. Byte-stable
    serialization is a GAPS W3 requirement, not a nicety — without it every
    touch is a spurious diff and the note history is noise before it exists.
    """
    block = yaml.safe_dump(dict(frontmatter_fields), sort_keys=False, allow_unicode=True)
    sections = [f"---\n{block}---", body.strip()]
    if relations:
        lines = "\n".join(relation_line(relation) for relation in relations)
        sections.append(f"{RELATIONS_HEADING}\n{lines}")
    # One blank line between sections and one trailing newline: the file is
    # compared byte-for-byte by the history, so its shape is fixed here.
    return "\n\n".join(part for part in sections if part) + "\n"


# --- Resolution against the database ---


async def resolve_write_project(session: "AsyncSession", project_name: str) -> WriteProject:
    """Look up the project a write lands in.

    Raises ValueError for a name the registry does not hold: a request that
    cannot be scoped is an addressing failure, never an empty result
    (`docs/OUTPUT_CONTRACT.md` rule 5).
    """
    from basic_memory.repository.project_repository import ProjectRepository

    project: Optional["Project"] = await ProjectRepository().get_by_name(session, project_name)
    if project is None:
        raise ValueError(f"Project not found: '{project_name}'")
    return WriteProject(
        name=project.name,
        external_id=project.external_id,
        project_id=project.id,
        path=project.path,
    )


async def allocate_record_id(session: "AsyncSession", project_id: int, note_type: str) -> str:
    """Draw a record id no note in this project already claims.

    `vocabulary/ids.py` owns the draw, the attempt count and the error; only the
    collision check lives here, because it is a database lookup and that
    module's `allocate_record_id` takes a synchronous predicate. The permalink
    column is what is checked: `permalink == id` byte-for-byte is the record
    schema's identity rule (§2), so a taken permalink is a taken id.

    ``note_type`` is the canonical type the write resolved — the id's prefix
    carries it (U30), so the hatch's inbox records draw `inbox-…` ids and an
    alias write draws the id of the type it stamped.
    """
    from basic_memory.repository.entity_repository import EntityRepository

    repository = EntityRepository(project_id=project_id)
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = new_record_id(note_type)
        if not await repository.permalink_exists(session, candidate):
            return candidate
    raise IdAllocationError(
        f"could not allocate a free record id in {MAX_ID_ATTEMPTS} attempts; "
        "that many collisions means the collision check is wrong, not that the "
        "draw was unlucky"
    )


async def record_exists(session: "AsyncSession", project_id: int, record_id: str) -> bool:
    """True when this project holds a record with exactly that id (GAPS E1).

    `permalink_exists` rather than `resolve_record`: the caller only needs to
    know whether the id names something, and this skips loading the entity's
    observations and relations to answer it. `permalink == id` byte-for-byte
    (§2), so a permalink query *is* an id query — no title can match it.
    """
    from basic_memory.repository.entity_repository import EntityRepository

    return await EntityRepository(project_id=project_id).permalink_exists(session, record_id)


async def resolve_record(
    session: "AsyncSession", project: WriteProject, record_id: str
) -> ExistingRecord:
    """Locate one record by id in one project, verifying identity (GAPS T9/T10).

    Raises `RecordResolutionError` when the project holds no record with that
    permalink. A row whose permalink is merely *similar* — the title match BM's
    resolver legitimately makes — is not-found, never a near-match: a confidently
    wrong record is worse than no record.
    """
    from basic_memory.repository.entity_repository import EntityRepository

    entity = await EntityRepository(project_id=project.project_id).get_by_permalink(
        session, record_id
    )
    if entity is None or entity.permalink != record_id:
        raise RecordResolutionError(record_id)
    return ExistingRecord(
        # `record_id`, not `entity.permalink`: the guard above proves they are
        # equal, and the column is nullable in the model.
        record_id=record_id,
        entity_external_id=entity.external_id,
        note_type=entity.note_type,
        file_path=entity.file_path,
        title=entity.title,
        metadata=dict(entity.entity_metadata or {}),
    )
