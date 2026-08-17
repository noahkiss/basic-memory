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
from typing import TYPE_CHECKING, Any, Mapping, Optional

import yaml
from pydantic import computed_field

from basic_memory.project_marker import resolve_cli_project
from basic_memory.schemas.base import Entity as EntitySchema

# Re-exported: the schema's supersession relation name lives with the rest of its
# fixed vocabulary, and the two record-writing verbs import it from this module.
from basic_memory.vocabulary.glossary import SUPERSEDES_RELATION
from basic_memory.vocabulary.ids import (
    MAX_ID_ATTEMPTS,
    SEPARATOR,
    TYPE_DIRS,
    IdAllocationError,
    new_record_id,
    record_slug,
)
from basic_memory.vocabulary.model import Vocabulary

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.models import Project

# The heading a record's relations live under (`.forked/schema.md` §5/§12).
RELATIONS_HEADING = "## Relations"

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
        raise ValueError("no project to write to — name one with --project or run 'bm project add'")
    return name


# --- Where a record's file goes ---


def record_directory(note_type: str) -> str:
    """The directory records of ``note_type`` live in.

    The closed six get their plural directory from `vocabulary/ids.py`. A type a
    human added to a project's `vocabulary.yml` has no entry there and gets a
    directory under its own name: a declared type is legal to write (GAPS W4), so
    refusing it a home would make the extension mechanism unusable, and inventing
    a plural would guess at English.
    """
    return TYPE_DIRS.get(note_type, note_type)


def record_path(note_type: str, record_id: str, title: str) -> str:
    """Where a new record lands: ``<type-dir>/<id>--<slug>.md`` (§8)."""
    return f"{record_directory(note_type)}/{record_id}{SEPARATOR}{record_slug(title)}.md"


def resolve_note_type(
    requested: str, vocabulary: Vocabulary | None, *, project: str
) -> tuple[str, str | None]:
    """Return the type to write and the type to record as *proposed*, if any.

    An unknown type is the W4 escape hatch, never an error: the record is filed
    as `inbox` carrying `proposed-type: <requested>`, which is what makes
    `bm doctor`'s "N inbox records propose type X" report non-empty. Agents
    propose a type; only a human enables one.

    An ungoverned project has no declared list, so the closed six are the only
    types it can be measured against.

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
    allowed = vocabulary.types if vocabulary is not None else tuple(TYPE_DIRS)
    if requested in allowed:
        return requested, None
    if INBOX_TYPE not in allowed:
        raise ValueError(
            f"'{requested}' is not a type project '{project}' declares, and its vocabulary "
            f"declares no '{INBOX_TYPE}' type to file the proposal as — add "
            f"'{INBOX_TYPE}' to its vocabulary.yml or pick a declared type; "
            f"run 'bm types' to see the set"
        )
    return INBOX_TYPE, requested


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


# --- What a record's file says ---


def record_markdown(
    frontmatter_fields: Mapping[str, str],
    body: str,
    *,
    supersedes: str | None = None,
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
    if supersedes is not None:
        sections.append(f"{RELATIONS_HEADING}\n- {SUPERSEDES_RELATION} [[{supersedes}]]")
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


async def allocate_record_id(session: "AsyncSession", project_id: int) -> str:
    """Draw a record id no note in this project already claims.

    `vocabulary/ids.py` owns the draw, the attempt count and the error; only the
    collision check lives here, because it is a database lookup and that
    module's `allocate_record_id` takes a synchronous predicate. The permalink
    column is what is checked: `permalink == id` byte-for-byte is the record
    schema's identity rule (§2), so a taken permalink is a taken id.
    """
    from basic_memory.repository.entity_repository import EntityRepository

    repository = EntityRepository(project_id=project_id)
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = new_record_id()
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
