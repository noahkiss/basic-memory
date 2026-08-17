"""`bm edit`, `bm mark`, `bm done` — the verbs that change a record (item F).

Real path throughout: the real Typer commands, the real write stack, a real
database, real files, the real vocabulary funnel and the real headline file.
Records under test are created by `bm new` rather than seeded into the table,
so every assertion here is about a record the tool itself wrote.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the modules registers the verbs these tests drive and exposes their
# constants. `records` is here for `bm path`, which the assertions use to find a
# record's file: registration must not depend on another test module importing it.
from basic_memory.cli.commands import new as new_command  # noqa: F401
from basic_memory.cli.commands import record_write, records  # noqa: F401
from basic_memory.services.headline import headline_path
from basic_memory.store.history import store_path
from basic_memory.vocabulary.model import DEFAULT_VOCABULARY, VOCABULARY_FILENAME

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

GOVERNED = "governed"
UNGOVERNED = "ungoverned"


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run from a directory with no `.bm.yml` above it, so nothing repoints scope."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """Keep the ONNX embedding stack off the write path; the semantic suites own it."""
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")


# --- Reaching the $EDITOR branch ---
#
# Duplicated from `test_new_command.py` rather than shared through `conftest.py`:
# they belong to two test modules, not to every CLI test, and the conftest is
# owned by another lane of this phase.


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
def appending_editor(tmp_path, monkeypatch):
    """Point `$EDITOR` at a script that *appends* to whatever it is handed.

    Appending rather than overwriting is the point: the record's current body has
    to survive, which is what proves `bm edit` opened the editor on the note as it
    stands rather than on an empty buffer.
    """
    script = tmp_path / "appending-editor.sh"
    script.write_text(
        '#!/bin/sh\nprintf "\\nappended by the editor\\n" >> "$1"\n', encoding="utf-8"
    )
    script.chmod(0o755)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", str(script))
    return script


# --- Seeding ---


@dataclass(frozen=True)
class SeededProject:
    """One store-derived project, registered and optionally governed."""

    name: str
    external_id: str
    path: Path


def seed_project(name: str = GOVERNED, *, governed: bool = True) -> SeededProject:
    """Register one project homed at `store/<external_id>/`, as `bm project add` does."""

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
                            "types": list(DEFAULT_VOCABULARY.types),
                            "statuses": list(DEFAULT_VOCABULARY.statuses),
                            "areas": [],
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


def create(project: str, note_type: str, title: str, body: str = "Original body.") -> str:
    """Write one record with `bm new` and return its id."""
    result = runner.invoke(
        app, ["new", note_type, title, "--body", body, "--project", project, "--quiet"]
    )
    assert result.exit_code == 0, result.output
    return result.stdout.split()[0]


def payload_path(output: str) -> Path:
    """The path a write verb reported, taken from its payload line."""
    return Path(output.strip().splitlines()[0].split("  ")[-1])


def frontmatter_of(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("---\n", 2)[1])


# --- bm edit ---


@pytest.mark.parametrize("note_type", ["guide", "profile", "state", "inbox"])
def test_edit_replaces_the_body_of_each_kept_current_type(note_type: str) -> None:
    """The four kept-current types are rewritten in place — that is what they are for (§4)."""
    seed_project()
    record_id = create(GOVERNED, note_type, "How To Restore")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "Replacement body.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = payload_path(result.stdout).read_text(encoding="utf-8")
    assert "Replacement body." in body
    assert "Original body." not in body


def test_edit_replaces_the_title_and_keeps_the_file_path() -> None:
    """The file name carries the id other records link by, so a title change does not move it."""
    seed_project()
    record_id = create(GOVERNED, "guide", "Old Title")
    before = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")

    result = runner.invoke(
        app, ["edit", record_id, "--title", "New Title", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    after = payload_path(result.stdout)
    assert after == before
    assert frontmatter_of(after)["title"] == "New Title"
    assert "Original body." in after.read_text(encoding="utf-8")


def test_edit_keeps_every_field_set_at_creation() -> None:
    """Only the title and the body move; `id`, `permalink`, `type` and `source` are set once (§4)."""
    seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")
    before = frontmatter_of(
        payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    )

    result = runner.invoke(app, ["edit", record_id, "-b", "New text.", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    after = frontmatter_of(payload_path(result.stdout))
    for field in ("id", "permalink", "type", "source", "review-by"):
        assert after[field] == before[field]


def test_edit_reads_the_body_from_stdin() -> None:
    """`--body -` takes the replacement from stdin (D11)."""
    seed_project()
    record_id = create(GOVERNED, "state", "Disk Usage")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "-", "-p", GOVERNED, "--quiet"], input="Piped body.\n"
    )

    assert result.exit_code == 0, result.output
    assert "Piped body." in payload_path(result.stdout).read_text(encoding="utf-8")


def test_edit_opens_the_editor_on_the_current_body(
    stdin_looks_like_a_terminal, appending_editor
) -> None:
    """With a terminal and no `--body`, `$EDITOR` opens on what the record says now (D11).

    A plain `CliRunner` stdin reports no terminal, so this branch went untested
    until the fixtures above forced it. The editor appends, so the original body
    surviving is the evidence that it was handed the record rather than a blank
    buffer — and it runs as a real subprocess, not a stub.
    """
    seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")

    result = runner.invoke(app, ["edit", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    body = payload_path(result.stdout).read_text(encoding="utf-8")
    assert "Original body." in body
    assert "appended by the editor" in body


def test_edit_with_a_title_only_leaves_the_editor_shut(
    stdin_looks_like_a_terminal, appending_editor
) -> None:
    """`--title` already states the change, so the body is not sent to `$EDITOR`.

    Positive control is the test above: with neither flag, the same fixtures do
    open the editor. Without this the one command would mean two different things
    depending on whether a terminal happened to be attached — the non-terminal
    branch changes only the title.
    """
    seed_project()
    record_id = create(GOVERNED, "guide", "Old Title")

    result = runner.invoke(
        app, ["edit", record_id, "--title", "New Title", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = payload_path(result.stdout).read_text(encoding="utf-8")
    assert "appended by the editor" not in body
    assert "Original body." in body
    assert frontmatter_of(payload_path(result.stdout))["title"] == "New Title"


def test_edit_on_a_task_names_done_and_mark() -> None:
    """A task is closed, not rewritten — and the refusal names the verb that does it (D12)."""
    seed_project()
    record_id = create(GOVERNED, "task", "Move The Backups")

    result = runner.invoke(app, ["edit", record_id, "-b", "different", "-p", GOVERNED])

    assert result.exit_code == 1
    assert f"bm done {record_id}" in result.stderr
    assert f"bm mark {record_id}" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_on_a_finding_names_supersession() -> None:
    """A finding is provisional evidence: correcting it in place destroys the record (§5)."""
    seed_project()
    record_id = create(GOVERNED, "finding", "What We Learned")

    result = runner.invoke(app, ["edit", record_id, "-b", "different", "-p", GOVERNED])

    assert result.exit_code == 1
    assert f"--supersedes {record_id}" in result.stderr
    assert result.stdout.strip() == ""
    assert "Original body." in payload_path(
        runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n"
    ).read_text(encoding="utf-8")


def test_edit_with_nothing_to_change_is_an_error() -> None:
    """A rewrite with no stated change is a no-op the caller cannot see (rule 5)."""
    seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")

    result = runner.invoke(app, ["edit", record_id, "-p", GOVERNED])

    assert result.exit_code == 1
    assert "nothing to change" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_refuses_a_record_that_only_matches_by_title() -> None:
    """The identity rule (T9/T10): a title match is not-found, never a near-match."""
    seed_project()
    create(GOVERNED, "guide", "tnd-eeee5555")

    result = runner.invoke(app, ["edit", "tnd-eeee5555", "-b", "x", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "no record 'tnd-eeee5555'" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_on_an_unknown_id_exits_one() -> None:
    """Positive control for the rule above: an id nothing holds is a failure, not an empty result."""
    seed_project()
    record_id = create(GOVERNED, "guide", "A Real Guide")

    missing = runner.invoke(app, ["edit", "tnd-zzzz9999", "-b", "x", "-p", GOVERNED])
    assert missing.exit_code == 1
    assert "no record 'tnd-zzzz9999'" in missing.stderr

    found = runner.invoke(app, ["edit", record_id, "-b", "x", "-p", GOVERNED, "--quiet"])
    assert found.exit_code == 0, found.output


# --- bm mark and bm done ---


def test_mark_sets_the_status() -> None:
    """`status` is the only field any verb changes after creation (D5)."""
    seed_project()
    record_id = create(GOVERNED, "task", "Move The Backups")

    result = runner.invoke(app, ["mark", record_id, "doing", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[0].split("  ") == [record_id, "task", "doing"]
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    assert frontmatter_of(path)["status"] == "doing"


def test_mark_leaves_the_body_byte_identical() -> None:
    """A frontmatter-only change must not reflow the note it did not edit."""
    seed_project()
    record_id = create(GOVERNED, "task", "Keep My Body", body="Line one.\n\nLine two.")
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    before = path.read_text(encoding="utf-8").split("---\n", 2)[2]

    result = runner.invoke(app, ["mark", record_id, "blocked", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert path.read_text(encoding="utf-8").split("---\n", 2)[2] == before


def test_mark_rejects_a_status_the_project_does_not_declare() -> None:
    """`mark` validates against the project's own statuses, and names them."""
    seed_project()
    record_id = create(GOVERNED, "task", "Move The Backups")

    result = runner.invoke(app, ["mark", record_id, "shipped", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "'shipped' is not a status this project declares" in result.stderr
    assert "open, doing, blocked, done, dropped" in result.stderr
    assert result.stdout.strip() == ""


def test_mark_on_an_ungoverned_project_writes_unchecked() -> None:
    """An absent vocabulary means ungoverned, never "use the defaults" (GAPS W4)."""
    seed_project(UNGOVERNED, governed=False)
    record_id = create(UNGOVERNED, "task", "Unchecked Work")

    result = runner.invoke(app, ["mark", record_id, "shipped", "-p", UNGOVERNED])

    assert result.exit_code == 0, result.output
    assert "declares no vocabulary" in result.stdout


def test_mark_on_a_record_that_is_not_a_task_exits_one() -> None:
    """No other type has a status, so there is nothing to set (§3)."""
    seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")

    result = runner.invoke(app, ["mark", record_id, "done", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "only a task carries a status" in result.stderr
    assert result.stdout.strip() == ""


def test_done_is_exactly_mark_done() -> None:
    """`bm done` sets the same field to the same value, through the same path."""
    seed_project()
    record_id = create(GOVERNED, "task", "Finish It")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[0].endswith("  done")
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    assert frontmatter_of(path)["status"] == "done"


def test_closing_the_last_open_task_clears_the_headline() -> None:
    """The headline follows the write it came from (GAPS W9), through the verb."""
    project = seed_project()
    record_id = create(GOVERNED, "task", "The Only Task")
    assert "The Only Task" in headline_path(project.external_id).read_text(encoding="utf-8")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    # No open task left, so the file is removed rather than left saying nothing.
    assert not headline_path(project.external_id).exists()


# --- What they print ---


def test_the_write_verbs_print_a_row_a_count_and_an_affordance() -> None:
    """Contract rules 1-4, for all three verbs: identifier first, count, then hints."""
    seed_project()
    task = create(GOVERNED, "task", "A Task")
    guide = create(GOVERNED, "guide", "A Guide")

    marked = runner.invoke(app, ["mark", task, "doing", "-p", GOVERNED])
    edited = runner.invoke(app, ["edit", guide, "-b", "new", "-p", GOVERNED])

    for result, affordance in (
        (marked, record_write.MARK_AFFORDANCE),
        (edited, record_write.EDIT_AFFORDANCE),
    ):
        assert result.exit_code == 0, result.output
        lines = result.stdout.strip().splitlines()
        assert lines[1] == "1 record"
        assert lines[-1] == affordance


def test_quiet_drops_the_affordance_and_keeps_the_payload() -> None:
    """Rule 7: `--quiet` removes the commentary and leaves the payload alone."""
    seed_project()
    record_id = create(GOVERNED, "task", "A Task")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines() == [f"{record_id}  task  done", "1 record"]
