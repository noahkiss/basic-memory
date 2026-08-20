"""`bm new` — the verb that writes a record (VERBS_PLAN item E).

Every test here drives the **real** caller path: the real Typer command, the
real local write stack, a real database, a real file on disk, and the real
vocabulary funnel. Nothing is stubbed, because every claim is about what the
verb actually produces — the id it allocates, the frontmatter it writes, the
file it lands, and what it refuses.

Projects are seeded **store-derived** (`store/<external_id>/`), which is what
`bm project add` now creates, so the write path takes its normal branch rather
than the off-store one.
"""

import asyncio
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers `new` on the app; the tests read its constants too.
from basic_memory.cli.commands import new as new_command
from basic_memory.store.history import store_path
from basic_memory.vocabulary.model import DEFAULT_VOCABULARY, VOCABULARY_FILENAME

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

GOVERNED = "governed"
UNGOVERNED = "ungoverned"
# A governed project whose human removed `inbox` from its vocabulary (GAPS E2).
NO_INBOX = "no-inbox"


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run from a directory with no `.bm.yml` above it.

    The write chain walks up from cwd, so a marker anywhere above the checkout
    would silently repoint every unpinned test at another project.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """Keep the ONNX embedding stack off the write path.

    `semantic_search_enabled` defaults to True wherever fastembed is importable,
    and every note write would then pay for a model load. Embeddings are covered
    by the semantic suites, which turn it on deliberately.
    """
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")


# --- Reaching the $EDITOR branch ---
#
# Both fixtures are duplicated in `test_record_write_commands.py` rather than
# shared through `conftest.py`: they belong to two test modules, not to every CLI
# test, and the conftest is owned by another lane of this phase.


@pytest.fixture
def stdin_looks_like_a_terminal(monkeypatch):
    """Make `sys.stdin.isatty()` true inside `CliRunner`, so the editor branch runs.

    `CliRunner` installs its own stdin, which reports no terminal — so the branch
    that opens `$EDITOR` is otherwise unreachable from a test.

    The class is typer's, not click's: typer ships its own `_NamedTextIOWrapper`
    and its runner installs that one, so patching click's does nothing at all
    (measured — `type(sys.stdin) is click.testing._NamedTextIOWrapper` is False
    inside a typer `CliRunner`). Importing it by name is deliberate: if typer
    renames it, this fixture raises rather than quietly re-testing the
    non-terminal path and reporting a pass.
    """
    from typer.testing import _NamedTextIOWrapper

    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


@pytest.fixture
def fake_editor(tmp_path, monkeypatch):
    """Point `$EDITOR` at a script that writes a known body, and return it.

    A real executable, run as a real subprocess: a stubbed editor would prove
    only that the stub was called, not that the temp file, the `$EDITOR` word
    split, and the read-back all work.
    """
    script = tmp_path / "fake-editor.sh"
    script.write_text('#!/bin/sh\nprintf "edited by the editor\\n" > "$1"\n', encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", str(script))
    return script


# --- Seeding ---


@dataclass(frozen=True)
class SeededProject:
    """One project seeded into the registry, and where its notes live."""

    name: str
    external_id: str
    path: Path


def seed_project(
    name: str,
    *,
    governed: bool = True,
    areas: tuple[str, ...] = (),
    types: tuple[str, ...] = DEFAULT_VOCABULARY.types,
) -> SeededProject:
    """Register one store-derived project, optionally governed by a vocabulary.

    ``types`` is settable because a human's vocabulary is theirs to shape, and one
    of the shapes it can take is "no `inbox`" (GAPS E2).

    Written straight into the `project` table rather than through
    `bm project add`: that command routes through the in-process ASGI app and
    costs seconds, and what it produces — a `store/<external_id>/` path plus a
    `vocabulary.yml` — is exactly what this builds.
    """

    async def _seed() -> SeededProject:
        import uuid

        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            external_id = str(uuid.uuid4())
            home = store_path() / external_id
            home.mkdir(parents=True, exist_ok=True)
            async with db.scoped_session(session_maker) as session:
                await ProjectRepository().create(
                    session,
                    {
                        "name": name,
                        "external_id": external_id,
                        "path": str(home),
                        "is_active": True,
                        "is_default": False,
                    },
                )
            if governed:
                (home / VOCABULARY_FILENAME).write_text(
                    yaml.safe_dump(
                        {
                            "types": list(types),
                            "statuses": list(DEFAULT_VOCABULARY.statuses),
                            "areas": list(areas),
                            "review_months": DEFAULT_VOCABULARY.review_months,
                            "fields": {},
                        },
                        sort_keys=False,
                    ),
                    encoding="utf-8",
                )
            return SeededProject(name=name, external_id=external_id, path=home)
        finally:
            await db.shutdown_db()

    return asyncio.run(_seed())


def written_file(project: SeededProject, output: str) -> Path:
    """The file `bm new` reported writing, taken from its payload line.

    The payload carries a **project-relative** path (GAPS U11), so the project's
    home is joined back on here — the verb no longer prints a path that differs
    between machines.
    """
    return project.path / output.strip().splitlines()[0].split("  ")[-1]


def create_record(project: str, note_type: str, title: str) -> str:
    """Write one record with `bm new` and return its id, for another one to link to."""
    result = runner.invoke(app, ["new", note_type, title, "-b", "seeded", "-p", project, "--quiet"])
    assert result.exit_code == 0, result.output
    return result.stdout.split()[0]


def indexed_relations(project: SeededProject, record_id: str) -> list[tuple[str, str]]:
    """Every outgoing edge the index holds for one record, as (type, target).

    Only *resolved* edges are returned — a row with no target entity points at
    nothing, which is the dangling relation `bm doctor` reports rather than a
    connection between two records.

    The target is read off the resolved entity's permalink, not off the relation's
    `to_name` column: resolution rewrites `to_name` to the target's **title**
    (`indexing/relation_resolution.py`, `target_name=resolved_entity.title`), so
    the column holds a human-readable name and the identity lives on the row it
    resolved to. `permalink == id` byte-for-byte (§2), so the permalink is the id.
    """

    async def _read() -> list[tuple[str, str]]:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.entity_repository import EntityRepository
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            async with db.scoped_session(session_maker) as session:
                registered = await ProjectRepository().get_by_name(session, project.name)
                assert registered is not None
                entity = await EntityRepository(project_id=registered.id).get_by_permalink(
                    session, record_id
                )
                assert entity is not None
                return [
                    (relation.relation_type, target.permalink)
                    for relation in entity.outgoing_relations
                    if (target := relation.to_entity) is not None and target.permalink is not None
                ]
        finally:
            await db.shutdown_db()

    return asyncio.run(_read())


def written_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the frontmatter block of a record the verb just wrote."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("---\n", 2)[1])


# --- What it writes ---


@pytest.mark.parametrize(
    ("note_type", "directory"),
    [
        ("task", "tasks"),
        ("plan", "plans"),
        ("guide", "guides"),
        ("finding", "findings"),
        ("profile", "profiles"),
        ("state", "states"),
        ("inbox", "inbox"),
    ],
)
def test_new_creates_a_record_of_each_type(note_type: str, directory: str) -> None:
    """Every declared type writes a file at `<type-dir>/<id>--<slug>.md` (§8)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", note_type, "A Backup Question", "--body", "Body text.", "-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    record_id = result.stdout.split()[0]
    on_disk = project.path / directory / f"{record_id}--a-backup-question.md"
    assert on_disk.is_file()
    assert "Body text." in on_disk.read_text(encoding="utf-8")


def test_new_prints_a_store_relative_path() -> None:
    """GAPS U11: the payload names the file the way the history subject line does.

    The absolute path was the one field guaranteed to differ between machines,
    which made any captured `bm new` output non-portable. `bm path <id>` is still
    the way to get the absolute one, so nothing is lost.
    """
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "finding", "Backups Failed", "-b", "Disk full.", "-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    record_id, note_type, path = result.stdout.splitlines()[0].split("  ")
    assert note_type == "finding"
    assert path == f"findings/{record_id}--backups-failed.md"
    # The store home is what U11 removed, so its absence is the claim.
    assert str(project.path) not in result.stdout
    assert (project.path / path).is_file()


def test_new_writes_a_permalink_equal_to_the_id() -> None:
    """`permalink == id`, byte for byte — the identity every edge binds to (§2)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "state", "Disk Usage", "--body", "89% full.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["permalink"] == metadata["id"]
    assert metadata["id"] == result.stdout.split()[0]
    assert metadata["id"].startswith("state-")  # U30: the type word is the prefix


def test_new_writes_the_type_date_with_its_provenance() -> None:
    """A date and its provenance travel together; nothing else gets a date.

    The stamped rung is `inferred`, not `inline` (GAPS U1): bm read this date off
    a clock, and `inline` claims the source text carried it.
    """
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "task", "Rotate The Key", "--body", "Soon.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["opened"] == date.today().isoformat()
    assert metadata["date-source"] == "inferred"
    assert metadata["date-confidence"] == "day"
    # `date-ref` is legal on the transcript and git rungs only; the checker
    # rejects it on `inferred`, so the verb must never volunteer one.
    assert "date-ref" not in metadata
    assert metadata["status"] == "open"


# --- Dates the writer states (GAPS U1) ---


def test_new_writes_a_stated_event_date_and_its_source() -> None:
    """A finding can carry the day its subject happened, not the day it was filed.

    This is U1's whole point: before it, a decision taken twelve days earlier was
    written with today's date and `date-source: inline` to assert it came from the
    source text.
    """
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        [
            "new",
            "finding",
            "Backups Failed",
            "-b",
            "Under a memory limit.",
            "--event-date",
            "2026-08-05",
            "--date-source",
            "inline",
            "--date-confidence",
            "exact",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["event-date"] == "2026-08-05"
    assert metadata["date-source"] == "inline"
    assert metadata["date-confidence"] == "exact"


def test_new_plan_carries_a_status_and_a_plan_id(  # GAPS U38
) -> None:
    """A plan opens the way a task does: status `open`, `opened` stamped, plan- id."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "plan", "Uplevel The App", "--body", "1. [[task-x]]", "-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    record_id = result.stdout.split()[0]
    assert record_id.startswith("plan-")
    on_disk = next((project.path / "plans").glob(f"{record_id}--*.md"))
    text = on_disk.read_text(encoding="utf-8")
    assert "status: open" in text
    assert "opened:" in text


def test_new_writes_a_stated_opened_date_on_a_task() -> None:
    """`--opened` is the task's own date field, and it takes the same provenance."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        [
            "new",
            "task",
            "Move The Backups",
            "-b",
            "x",
            "--opened",
            "2026-07-26",
            "--date-source",
            "mtime",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["opened"] == "2026-07-26"
    assert metadata["date-source"] == "mtime"
    # Unstated confidence still defaults, because a date without one is refused.
    assert metadata["date-confidence"] == "day"


def test_new_writes_a_stated_review_by_instead_of_the_default() -> None:
    """`--review-by` beats the `review_months` stamp, and is written exactly once."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "guide", "How To Restore", "-b", "x", "--review-by", "2026-09-01"]
        + ["-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    path = written_file(project, result.stdout)
    assert path.read_text(encoding="utf-8").count("review-by:") == 1
    assert written_frontmatter(path)["review-by"] == "2026-09-01"


def test_new_writes_a_date_ref_on_a_ref_bearing_rung() -> None:
    """`git` and `transcript` point at evidence, so `--date-ref` records which."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        [
            "new",
            "finding",
            "Reversed In Review",
            "-b",
            "x",
            "--event-date",
            "2026-08-05",
            "--date-source",
            "git",
            "--date-ref",
            "9e4f3c8c",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["date-source"] == "git"
    assert metadata["date-ref"] == "9e4f3c8c"


def test_new_refuses_a_stated_date_with_no_date_source() -> None:
    """A stated date without its rung would get the default one, which is a lie.

    Positive control in the same test: the identical command with
    `--date-source` writes the record, so the refusal is about the missing rung.
    """
    project = seed_project(GOVERNED)
    stated = ["new", "finding", "Dated", "-b", "x", "--event-date", "2026-08-05"]

    result = runner.invoke(app, [*stated, "-p", GOVERNED])

    assert result.exit_code == 1
    assert "--date-source is required with a stated date" in result.stderr
    assert "event-date" in result.stderr
    assert result.stdout.strip() == ""
    assert list((project.path / "findings").glob("*.md")) == []

    allowed = runner.invoke(app, [*stated, "--date-source", "inline", "-p", GOVERNED, "--quiet"])
    assert allowed.exit_code == 0, allowed.output
    assert written_frontmatter(written_file(project, allowed.stdout))["event-date"] == "2026-08-05"


def test_new_refuses_a_date_flag_the_type_does_not_carry() -> None:
    """`--opened` is a task's date field; a finding uses `event-date` (§2)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "finding", "Wrong Field", "-b", "x", "--opened", "2026-08-05"]
        + ["--date-source", "inline", "-p", GOVERNED],
    )

    assert result.exit_code == 1
    assert "--opened writes 'opened', which only a plan or task carries" in result.stderr
    assert "this record is a finding" in result.stderr
    assert result.stdout.strip() == ""
    assert list((project.path / "findings").glob("*.md")) == []


def test_new_refuses_a_review_by_on_a_type_that_has_none() -> None:
    """`review-by` belongs to a finding and a guide; a state is kept current instead."""
    seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "state", "Now", "-b", "x", "--review-by", "2026-09-01", "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "--review-by writes 'review-by', which only a finding or guide carries" in result.stderr
    assert "this record is a state" in result.stderr
    assert result.stdout.strip() == ""


def test_new_refuses_provenance_on_a_type_with_no_date_field() -> None:
    """There is nowhere to put a date on a guide, so there is nothing to source."""
    seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "guide", "How To", "-b", "x", "--date-source", "inline", "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "--date-source records where a date came from" in result.stderr
    assert "a guide carries no date field" in result.stderr
    assert result.stdout.strip() == ""


def test_new_refuses_a_malformed_date() -> None:
    """A date is `YYYY-MM-DD`, and a day the calendar does not have is not one."""
    seed_project(GOVERNED)

    for value in ("05-08-2026", "2026-8-5", "20260805", "2026-02-30", "yesterday"):
        result = runner.invoke(
            app,
            ["new", "finding", "Bad Date", "-b", "x", "--event-date", value]
            + ["--date-source", "inline", "-p", GOVERNED],
        )
        assert result.exit_code == 1, value
        assert f"--event-date takes a date as YYYY-MM-DD, got '{value}'" in result.stderr
        assert result.stdout.strip() == ""


def test_new_refuses_a_date_source_off_the_ladder() -> None:
    """The five rungs are fixed by the schema; `--date-confidence` is the same shape."""
    seed_project(GOVERNED)

    source = runner.invoke(
        app,
        ["new", "finding", "Off Ladder", "-b", "x", "--event-date", "2026-08-05"]
        + ["--date-source", "vibes", "-p", GOVERNED],
    )
    assert source.exit_code == 1
    assert "--date-source takes one of inline, transcript, git, mtime, inferred" in source.stderr

    confidence = runner.invoke(
        app,
        ["new", "task", "Off Ladder", "-b", "x", "--date-confidence", "roughly", "-p", GOVERNED],
    )
    assert confidence.exit_code == 1
    assert "--date-confidence takes one of exact, day, month, unknown" in confidence.stderr


def test_new_refuses_a_ref_bearing_rung_with_no_ref_and_a_ref_without_one() -> None:
    """`--date-ref` and the two rungs that need it are required together, both ways."""
    seed_project(GOVERNED)
    stated = ["new", "finding", "Evidence", "-b", "x", "--event-date", "2026-08-05"]

    missing = runner.invoke(app, [*stated, "--date-source", "transcript", "-p", GOVERNED])
    assert missing.exit_code == 1
    assert "--date-ref is required" in missing.stderr

    forbidden = runner.invoke(
        app, [*stated, "--date-source", "inline", "--date-ref", "9e4f3c8c", "-p", GOVERNED]
    )
    assert forbidden.exit_code == 1
    assert "--date-ref points at evidence to re-open" in forbidden.stderr


def test_new_writes_a_file_that_ends_with_exactly_one_newline() -> None:
    """GAPS U2: a record file is line-oriented, whatever the body's own shape is.

    Three bodies that each broke it differently: none, a body with no newline of
    its own, and a body with several. The relations block is the interesting case
    — it is the last section a record can have, so it is the one that used to
    leave the file unterminated.
    """
    project = seed_project(GOVERNED)

    for title, body in (("No Body", ""), ("Bare", "one line"), ("Padded", "one line\n\n\n")):
        result = runner.invoke(app, ["new", "state", title, "-b", body, "-p", GOVERNED, "--quiet"])
        assert result.exit_code == 0, result.output
        written = written_file(project, result.stdout).read_bytes()
        assert written.endswith(b"\n"), (title, written[-40:])
        assert not written.endswith(b"\n\n"), (title, written[-40:])

    # A record whose last section is `## Relations` — the shape U2 reproduced on.
    first = runner.invoke(app, ["new", "finding", "Old", "-b", "a", "-p", GOVERNED, "--quiet"])
    assert first.exit_code == 0, first.output
    second = runner.invoke(
        app,
        ["new", "finding", "New", "-b", "b", "--supersedes", first.stdout.split()[0]]
        + ["-p", GOVERNED, "--quiet"],
    )
    assert second.exit_code == 0, second.output
    successor = written_file(project, second.stdout).read_bytes()
    assert successor.endswith(b"]]\n"), successor[-40:]


def test_new_defaults_source_to_cli() -> None:
    """`source` is required on every record, and D7 defaults it rather than inventing a ref."""
    project = seed_project(GOVERNED)

    result = runner.invoke(app, ["new", "state", "Now", "-b", "x", "-p", GOVERNED, "--quiet"])
    assert result.exit_code == 0, result.output
    assert written_frontmatter(written_file(project, result.stdout))["source"] == "cli"

    stated = runner.invoke(
        app,
        ["new", "state", "Then", "-b", "x", "--source", "NOTES.md#L4", "-p", GOVERNED, "--quiet"],
    )
    assert stated.exit_code == 0, stated.output
    assert written_frontmatter(written_file(project, stated.stdout))["source"] == "NOTES.md#L4"


def test_new_leaves_review_by_to_the_write_path() -> None:
    """`review-by` is stamped once, by the preparer — never written twice (W5 item 1)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        [
            "new",
            "finding",
            "Backups Fail",
            "-b",
            "Under a memory limit.",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    path = written_file(project, result.stdout)
    assert path.read_text(encoding="utf-8").count("review-by:") == 1
    assert written_frontmatter(path)["review-by"]


def test_new_records_a_supersedes_relation_on_a_finding() -> None:
    """The successor carries the edge; the predecessor is never touched (§5)."""
    project = seed_project(GOVERNED)

    first = runner.invoke(
        app, ["new", "finding", "Old Answer", "-b", "a", "-p", GOVERNED, "--quiet"]
    )
    assert first.exit_code == 0, first.output
    predecessor = first.stdout.split()[0]

    second = runner.invoke(
        app,
        [
            "new",
            "finding",
            "New Answer",
            "-b",
            "b",
            "--supersedes",
            predecessor,
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert second.exit_code == 0, second.output
    body = written_file(project, second.stdout).read_text(encoding="utf-8")
    assert f"- supersedes [[{predecessor}]]" in body
    # The predecessor's own file is unchanged: no `superseded-by` was written back.
    assert "superseded" not in written_file(project, first.stdout).read_text(encoding="utf-8")


# --- Edges the writer states (GAPS U14) ---


def test_new_writes_every_rel_and_the_index_resolves_them() -> None:
    """`--rel` is repeatable, and each edge indexes as a resolved relation.

    The resolved target is the claim, not just the bullet: a relation row with no
    target entity is a dangling edge that `bm doctor` reports, so a row pointing
    at the record whose id was typed is what proves the write *connected* two
    records rather than leaving a link that merely looks like one.
    """
    project = seed_project(GOVERNED)
    source = create_record(GOVERNED, "finding", "Where It Came From")
    sibling = create_record(GOVERNED, "guide", "How To Do It")

    result = runner.invoke(
        app,
        [
            "new",
            "task",
            "Do The Thing",
            "-b",
            "x",
            "--rel",
            f"derived_from:{source}",
            "--rel",
            f"relates_to:{sibling}",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert result.exit_code == 0, result.output
    record_id = result.stdout.split()[0]
    body = written_file(project, result.stdout).read_text(encoding="utf-8")
    assert f"- derived_from [[{source}]]" in body
    assert f"- relates_to [[{sibling}]]" in body

    indexed = indexed_relations(project, record_id)
    assert ("derived_from", source) in indexed
    assert ("relates_to", sibling) in indexed


def test_new_supersedes_is_the_same_write_as_rel_supersedes() -> None:
    """`--supersedes <id>` is sugar for `--rel supersedes:<id>` (GAPS U14).

    One edge-writing path, so the two flags cannot drift into writing two
    different bullets for the same claim.
    """
    project = seed_project(GOVERNED)
    predecessor = create_record(GOVERNED, "finding", "Old Answer")

    sugar = runner.invoke(
        app,
        [
            "new",
            "finding",
            "By Flag",
            "-b",
            "b",
            "--supersedes",
            predecessor,
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )
    general = runner.invoke(
        app,
        [
            "new",
            "finding",
            "By Rel",
            "-b",
            "b",
            "--rel",
            f"supersedes:{predecessor}",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert sugar.exit_code == 0, sugar.output
    assert general.exit_code == 0, general.output
    line = f"- supersedes [[{predecessor}]]"
    assert line in written_file(project, sugar.stdout).read_text(encoding="utf-8")
    assert line in written_file(project, general.stdout).read_text(encoding="utf-8")


def test_new_reads_the_body_from_stdin() -> None:
    """`--body -` takes the body from stdin, the way `git commit -F -` does (D11)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "guide", "How To Restore", "--body", "-", "-p", GOVERNED, "--quiet"],
        input="From a pipe.\n",
    )

    assert result.exit_code == 0, result.output
    assert "From a pipe." in written_file(project, result.stdout).read_text(encoding="utf-8")


def test_new_opens_the_editor_when_no_body_is_given(
    stdin_looks_like_a_terminal, fake_editor
) -> None:
    """With a terminal and no `--body`, the body comes from `$EDITOR` (D11).

    This branch is unreachable from a plain `CliRunner` invocation — its stdin
    reports no terminal — so it went untested until the fixtures above forced it.
    A real script runs, so `body_from_editor`'s whole path is exercised: the temp
    file, the `$EDITOR` split, the subprocess, and the read-back.
    """
    project = seed_project(GOVERNED)

    result = runner.invoke(app, ["new", "guide", "How To Restore", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "edited by the editor" in written_file(project, result.stdout).read_text(
        encoding="utf-8"
    )


def test_new_with_no_editor_configured_writes_an_empty_body(
    stdin_looks_like_a_terminal, monkeypatch
) -> None:
    """No `$EDITOR` and no `$VISUAL` means there is nothing to open, so the body is empty.

    Positive control for the test above: the record is still written. Refusing
    the write here would make the verb unusable on a machine with no editor set.
    """
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    project = seed_project(GOVERNED)

    result = runner.invoke(app, ["new", "state", "Bodyless", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    written = written_file(project, result.stdout)
    assert written.is_file()
    assert written_frontmatter(written)["id"] == result.stdout.split()[0]


# --- The escape hatch and the ungoverned case ---


def test_new_files_an_unknown_type_as_inbox_and_says_so() -> None:
    """An undeclared type is W4's escape hatch: filed as inbox, proposing itself."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "runbook", "Restart The Thing", "-b", "steps", "-p", GOVERNED]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.split()[1] == "inbox"
    # U30: the hatch stamps inbox, so the id says inbox- — never the proposal.
    assert result.stdout.split()[0].startswith("inbox-")
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["type"] == "inbox"
    assert metadata["proposed-type"] == "runbook"
    # The notice names the declared set inline (GAPS U25): the writer learns
    # what would have landed as itself without running a second command.
    assert "no type 'runbook' — filed as inbox proposing it" in result.stdout
    assert "types: task, plan, guide, finding, profile, state, note" in result.stdout
    assert "bm types for detail" in result.stdout
    assert f"inbox/{metadata['id']}--restart-the-thing.md" in result.stdout


def test_new_refuses_an_undeclared_type_when_the_vocabulary_declares_no_inbox() -> None:
    """GAPS E2: with no `inbox` to file the proposal as, the verb says so and stops.

    Before this, the hatch filed the record as `inbox` and the checker rejected it
    one layer down, with a message about a type the author never asked for. The
    positive control is the same project's declared type, which still writes — so
    the refusal is about the missing `inbox`, not about the project being governed.
    """
    project = seed_project(NO_INBOX, types=("task", "guide"))

    result = runner.invoke(
        app, ["new", "runbook", "Restart The Thing", "-b", "steps", "-p", NO_INBOX]
    )

    assert result.exit_code == 1
    assert f"is not a type project '{NO_INBOX}' declares" in result.stderr
    assert "declares no 'inbox' type" in result.stderr
    assert "bm types" in result.stderr
    assert result.stdout.strip() == ""
    # Nothing written anywhere: the refusal precedes the write, not follows it.
    assert list(project.path.rglob("*.md")) == []

    allowed = runner.invoke(app, ["new", "task", "Real Work", "-b", "x", "-p", NO_INBOX, "--quiet"])
    assert allowed.exit_code == 0, allowed.output
    assert written_file(project, allowed.stdout).is_file()


def test_new_on_an_ungoverned_project_writes_and_notices() -> None:
    """An absent vocabulary means unchecked, never "use the defaults" (GAPS W4)."""
    project = seed_project(UNGOVERNED, governed=False)
    assert not (project.path / VOCABULARY_FILENAME).exists()

    result = runner.invoke(app, ["new", "task", "Unchecked Work", "-b", "x", "-p", UNGOVERNED])

    assert result.exit_code == 0, result.output
    assert written_file(project, result.stdout).is_file()
    assert "declares no vocabulary" in result.stdout
    assert "bm types" in result.stdout


# --- What it refuses ---


def test_new_rejects_a_relation_type_the_project_does_not_declare() -> None:
    """The relation type is closed vocabulary, the same as the record type (GAPS U14).

    The positive control is the same project, the same target, and a declared
    type — so the refusal is about the word, not about the flag being unusable.
    """
    project = seed_project(GOVERNED)
    target = create_record(GOVERNED, "finding", "A Cause")

    refused = runner.invoke(
        app, ["new", "task", "A Task", "-b", "x", "--rel", f"caused_by:{target}", "-p", GOVERNED]
    )

    assert refused.exit_code == 1
    assert "'caused_by' is not a relation type project 'governed' declares" in refused.stderr
    assert "relates_to, derived_from, part_of, supersedes" in refused.stderr
    assert refused.stdout.strip() == ""

    allowed = runner.invoke(
        app,
        [
            "new",
            "task",
            "A Task",
            "-b",
            "x",
            "--rel",
            f"relates_to:{target}",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )
    assert allowed.exit_code == 0, allowed.output
    assert f"- relates_to [[{target}]]" in written_file(project, allowed.stdout).read_text(
        encoding="utf-8"
    )


def test_new_rejects_a_rel_target_that_does_not_exist() -> None:
    """An edge to a record that is not there is a typo every time (GAPS E1, U14)."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app,
        ["new", "task", "A Task", "-b", "x", "--rel", "relates_to:tnd-aaaa1111", "-p", GOVERNED],
    )

    assert result.exit_code == 1
    assert "--rel names 'tnd-aaaa1111'" in result.stderr
    assert result.stdout.strip() == ""
    # Refused before the write, not after it.
    assert list((project.path / "tasks").glob("*.md")) == []


@pytest.mark.parametrize("value", ["relates_to", "relates_to:", ":tnd-aaaa1111", "relates_to:note"])
def test_new_rejects_a_malformed_rel_flag(value: str) -> None:
    """`--rel` takes `<type>:<id>`, and an id that could not be a permalink is not one."""
    seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "task", "A Task", "-b", "x", "--rel", value, "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "--rel takes" in result.stderr
    assert result.stdout.strip() == ""


def test_new_rejects_supersedes_on_a_type_that_cannot_supersede() -> None:
    """Supersession is legal on a finding only, and the funnel is what says so (§5)."""
    project = seed_project(GOVERNED)
    seeded = runner.invoke(
        app, ["new", "finding", "A Finding", "-b", "a", "-p", GOVERNED, "--quiet"]
    )
    assert seeded.exit_code == 0, seeded.output
    predecessor = seeded.stdout.split()[0]

    result = runner.invoke(
        app, ["new", "task", "A Task", "-b", "x", "--supersedes", predecessor, "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "supersede" in result.stderr
    assert result.stdout.strip() == ""
    assert list((project.path / "tasks").glob("*.md")) == []


def test_new_rejects_a_supersedes_target_that_does_not_exist() -> None:
    """A well-formed id that names nothing is a typo, and it is refused (GAPS E1).

    The funnel's supersession rule judges the *type* of the record being written,
    not whether the target exists — so without this guard the edge is written,
    the command exits 0, and `bm doctor` reports the dangling relation much
    later. The positive control is in the same test: the identical command with a
    real predecessor succeeds and writes the edge, so the refusal is about the
    target's existence and nothing else.
    """
    project = seed_project(GOVERNED)

    missing = runner.invoke(
        app,
        [
            "new",
            "finding",
            "A Successor",
            "-b",
            "x",
            "--supersedes",
            "tnd-aaaa1111",
            "-p",
            GOVERNED,
        ],
    )

    assert missing.exit_code == 1
    assert "--supersedes names 'tnd-aaaa1111'" in missing.stderr
    assert f"not a record in project '{GOVERNED}'" in missing.stderr
    assert missing.stdout.strip() == ""
    assert list((project.path / "findings").glob("*.md")) == []

    # Positive control: a real predecessor, same command, and the edge lands.
    first = runner.invoke(
        app, ["new", "finding", "Old Answer", "-b", "a", "-p", GOVERNED, "--quiet"]
    )
    assert first.exit_code == 0, first.output
    predecessor = first.stdout.split()[0]

    found = runner.invoke(
        app,
        [
            "new",
            "finding",
            "A Successor",
            "-b",
            "x",
            "--supersedes",
            predecessor,
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert found.exit_code == 0, found.output
    body = written_file(project, found.stdout).read_text(encoding="utf-8")
    assert f"- supersedes [[{predecessor}]]" in body


def test_new_rejects_a_supersedes_target_from_another_project() -> None:
    """Supersession is an edge inside one project, so a foreign id names nothing here.

    Records are project-scoped and a wikilink resolves within the project, so an
    id that exists elsewhere would still land as a dangling relation. It is
    refused for the same reason a typo is.
    """
    seed_project(GOVERNED)
    other = seed_project("other")

    elsewhere = runner.invoke(
        app, ["new", "finding", "Elsewhere", "-b", "a", "-p", other.name, "--quiet"]
    )
    assert elsewhere.exit_code == 0, elsewhere.output
    foreign = elsewhere.stdout.split()[0]

    result = runner.invoke(
        app,
        ["new", "finding", "A Successor", "-b", "x", "--supersedes", foreign, "-p", GOVERNED],
    )

    assert result.exit_code == 1
    assert f"--supersedes names '{foreign}'" in result.stderr
    assert result.stdout.strip() == ""


def test_new_rejects_a_supersedes_value_that_is_not_a_record_id() -> None:
    """A target that cannot be a permalink would land as a dangling relation."""
    seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "finding", "A Finding", "-b", "x", "--supersedes", "some-note", "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "--supersedes takes a record id" in result.stderr
    assert result.stdout.strip() == ""


def test_new_rejects_an_area_the_project_does_not_declare() -> None:
    """`area` is a closed per-project vocabulary; the funnel enforces it (§3)."""
    seed_project(GOVERNED, areas=("ops",))

    allowed = runner.invoke(
        app, ["new", "state", "Fine", "-b", "x", "--area", "ops", "-p", GOVERNED, "--quiet"]
    )
    assert allowed.exit_code == 0, allowed.output

    result = runner.invoke(
        app, ["new", "state", "Not Fine", "-b", "x", "--area", "invented", "-p", GOVERNED]
    )

    assert result.exit_code == 1
    assert "invented" in result.stderr
    assert result.stdout.strip() == ""


def test_new_on_an_unknown_project_exits_one() -> None:
    """An unaddressable write is a failure, not a record written somewhere else."""
    seed_project(GOVERNED)

    result = runner.invoke(app, ["new", "task", "Homeless", "-b", "x", "-p", "no-such-project"])

    assert result.exit_code == 1
    assert "Project not found: 'no-such-project'" in result.stderr
    assert result.stdout.strip() == ""


# --- What it prints ---


def test_new_prints_one_row_a_count_line_and_an_affordance() -> None:
    """Contract rules 1-4: identifier first, count on its own line, hints last."""
    seed_project(GOVERNED)

    result = runner.invoke(app, ["new", "task", "Print Me", "-b", "x", "-p", GOVERNED])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    columns = lines[0].split("  ")
    assert columns[0].startswith("task-")  # U30
    assert columns[1] == "task"
    assert lines[1] == "1 record"
    assert lines[-1] == new_command.NEW_AFFORDANCE


def test_quiet_drops_the_affordance_and_keeps_the_payload() -> None:
    """Rule 7: `--quiet` removes the commentary and leaves the payload alone."""
    seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "task", "Quiet Please", "-b", "x", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 2
    assert lines[1] == "1 record"


# --- Aliases (GAPS U25) ---


def test_new_resolves_an_alias_and_stamps_the_canonical_type() -> None:
    """`bm new decision` writes a finding — the record never carries the alias."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "decision", "Store Is Central", "-b", "why", "-p", GOVERNED]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.split()[1] == "finding"
    # U30: the id carries the CANONICAL word, so an alias never reaches it.
    assert result.stdout.split()[0].startswith("finding-")
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["type"] == "finding"
    assert "proposed-type" not in metadata
    assert "finding recorded (alias: decision is an alias for finding)" in result.stdout


def test_new_alias_resolves_in_an_ungoverned_project_too() -> None:
    """Ungoverned projects are measured against the closed set, aliases included."""
    seed_project(UNGOVERNED, governed=False)

    result = runner.invoke(app, ["new", "todo", "Do The Thing", "-b", "x", "-p", UNGOVERNED])

    assert result.exit_code == 0, result.output
    assert result.stdout.split()[1] == "task"
    assert "task recorded (alias: todo is an alias for task)" in result.stdout


def test_new_quiet_hides_the_alias_notice_but_the_type_still_resolves() -> None:
    """--quiet drops the teaching line; the payload line still names the type."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "idea", "Maybe Someday", "-b", "x", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.split()[1] == "inbox"
    assert "alias" not in result.stdout
    assert written_frontmatter(written_file(project, result.stdout))["type"] == "inbox"


def test_new_a_declared_type_beats_the_default_alias_of_the_same_name() -> None:
    """A project that promoted 'decision' to a type writes 'decision' records.

    The parser drops the default alias when its name is declared, so the write
    takes the type outright and no alias notice appears.
    """
    project = seed_project("decision-type", types=("task", "decision", "inbox"))

    result = runner.invoke(app, ["new", "decision", "Promoted", "-b", "x", "-p", "decision-type"])

    assert result.exit_code == 0, result.output
    assert result.stdout.split()[1] == "decision"
    assert written_frontmatter(written_file(project, result.stdout))["type"] == "decision"
    assert "alias" not in result.stdout
