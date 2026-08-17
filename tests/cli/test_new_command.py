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
    """The file `bm new` reported writing, taken from its payload line."""
    return Path(output.strip().splitlines()[0].split("  ")[-1])


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
    assert metadata["id"].startswith("tnd-")


def test_new_writes_the_type_date_with_its_provenance() -> None:
    """A date and its provenance travel together; nothing else gets a date."""
    project = seed_project(GOVERNED)

    result = runner.invoke(
        app, ["new", "task", "Rotate The Key", "--body", "Soon.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["opened"]
    assert metadata["date-source"] == "inline"
    assert metadata["date-confidence"] == "day"
    # `date-ref` is legal on the transcript and git rungs only; the checker
    # rejects it on `inline`, so the verb must never volunteer one.
    assert "date-ref" not in metadata
    assert metadata["status"] == "open"


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
    metadata = written_frontmatter(written_file(project, result.stdout))
    assert metadata["type"] == "inbox"
    assert metadata["proposed-type"] == "runbook"
    assert "'runbook' is not a type this project declares" in result.stdout
    assert str(project.path / "inbox") in result.stdout


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
    assert columns[0].startswith("tnd-")
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
