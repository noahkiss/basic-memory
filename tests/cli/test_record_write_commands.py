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


@pytest.fixture
def truncating_editor(tmp_path, monkeypatch):
    """Point `$EDITOR` at a script that *replaces* whatever it is handed.

    The counterpart to `appending_editor`, and the only way to test the GAPS U17
    exception: the editor opens on the whole body, relations included, so a user
    who saves a buffer without them meant to delete them.
    """
    script = tmp_path / "truncating-editor.sh"
    script.write_text('#!/bin/sh\nprintf "Only prose now.\\n" > "$1"\n', encoding="utf-8")
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


def seed_project(
    name: str = GOVERNED,
    *,
    governed: bool = True,
    fields: dict[str, Any] | None = None,
) -> SeededProject:
    """Register one project homed at `store/<external_id>/`, as `bm project add` does.

    ``fields`` declares the project's optional extras, which is what makes
    `bm edit --set` reachable at all (GAPS V-J1).
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
                            "types": list(DEFAULT_VOCABULARY.types),
                            "statuses": list(DEFAULT_VOCABULARY.statuses),
                            "areas": [],
                            "review_months": DEFAULT_VOCABULARY.review_months,
                            "fields": dict(fields or {}),
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


def indexed_relations(project: SeededProject, record_id: str) -> list[tuple[str, str]]:
    """Every *resolved* outgoing edge the index holds for one record, as (type, target).

    A bullet in the file is not yet an edge: a relation row with no target entity
    points at nothing, which is the dangling relation `bm doctor` reports rather
    than a connection between two records. Only resolved rows are returned, so a
    non-empty result is the claim that the write indexed.

    The target is read off the resolved entity's permalink rather than the
    relation's `to_name` column, which resolution rewrites to the target's
    **title** (`indexing/relation_resolution.py`). `permalink == id` byte-for-byte
    (`.forked/schema.md` §2), so the permalink is the id.

    Duplicated from `test_new_command.py` for the reason the `$EDITOR` fixtures
    above are: it belongs to the two modules that assert about edges, not to
    every CLI test.
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


def payload_path(output: str) -> Path:
    """An absolute path a verb printed, taken from its payload line.

    `bm path` prints one; a write verb does not — see `written_path` (GAPS U11).
    """
    return Path(output.strip().splitlines()[0].split("  ")[-1])


def written_path(project: SeededProject, output: str) -> Path:
    """The file a write verb reported, joined onto the project's home.

    `bm edit` prints a **project-relative** path, the same form `bm new` prints
    and the history subject line uses (GAPS U11), so the home goes back on here.
    """
    return project.path / output.strip().splitlines()[0].split("  ")[-1]


def frontmatter_of(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text
    return yaml.safe_load(text.split("---\n", 2)[1])


# --- bm edit ---


@pytest.mark.parametrize("note_type", ["plan", "guide", "profile", "state", "inbox"])
def test_edit_replaces_the_body_of_each_kept_current_type(note_type: str) -> None:
    """The kept-current types are rewritten in place — that is what they are for (§4).

    `plan` is one of them (GAPS U38): its body IS the plan, and rewriting the
    stage list as the plan evolves is what keeping it current means.
    """
    project = seed_project()
    record_id = create(GOVERNED, note_type, "How To Restore")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "Replacement body.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    assert "Replacement body." in body
    assert "Original body." not in body


def test_edit_prints_a_store_relative_path() -> None:
    """GAPS U11: `bm edit`'s payload names the file the way `bm new`'s does.

    `bm path` still prints the absolute path, and this asserts the two agree —
    the relative form has to resolve to the file the verb actually wrote.
    """
    project = seed_project()
    record_id = create(GOVERNED, "guide", "How To Restore")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "Replacement body.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    printed_id, note_type, path = result.stdout.splitlines()[0].split("  ")
    assert (printed_id, note_type) == (record_id, "guide")
    assert path == f"guides/{record_id}--how-to-restore.md"
    # The store home is what U11 removed, so its absence is the claim.
    assert str(project.path) not in result.stdout
    assert (project.path / path) == payload_path(
        runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n"
    )


def test_edit_leaves_the_file_ending_in_exactly_one_newline() -> None:
    """GAPS U2: an edit is a write, so it owes the same line-orientation as a create.

    A replacement body with no newline of its own is the case that broke it: the
    edit path builds its content without passing through `dump_frontmatter`.
    """
    project = seed_project()
    record_id = create(GOVERNED, "guide", "How To Restore")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "no newline here", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    written = written_path(project, result.stdout).read_bytes()
    assert written.endswith(b"no newline here\n"), written[-40:]


def test_edit_replaces_the_title_and_keeps_the_file_path() -> None:
    """The file name carries the id other records link by, so a title change does not move it."""
    project = seed_project()
    record_id = create(GOVERNED, "guide", "Old Title")
    before = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")

    result = runner.invoke(
        app, ["edit", record_id, "--title", "New Title", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    after = written_path(project, result.stdout)
    assert after == before
    assert frontmatter_of(after)["title"] == "New Title"
    assert "Original body." in after.read_text(encoding="utf-8")


def test_edit_keeps_every_field_set_at_creation() -> None:
    """Only the title and the body move; `id`, `permalink`, `type` and `source` are set once (§4)."""
    project = seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")
    before = frontmatter_of(
        payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    )

    result = runner.invoke(app, ["edit", record_id, "-b", "New text.", "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    after = frontmatter_of(written_path(project, result.stdout))
    for field in ("id", "permalink", "type", "source", "review-by"):
        assert after[field] == before[field]


def test_edit_reads_the_body_from_stdin() -> None:
    """`--body -` takes the replacement from stdin (D11)."""
    project = seed_project()
    record_id = create(GOVERNED, "state", "Disk Usage")

    result = runner.invoke(
        app, ["edit", record_id, "--body", "-", "-p", GOVERNED, "--quiet"], input="Piped body.\n"
    )

    assert result.exit_code == 0, result.output
    assert "Piped body." in written_path(project, result.stdout).read_text(encoding="utf-8")


def test_edit_opens_the_editor_on_the_current_body(
    stdin_looks_like_a_terminal, appending_editor
) -> None:
    """With a terminal and no `--body`, `$EDITOR` opens on what the record says now (D11).

    A plain `CliRunner` stdin reports no terminal, so this branch went untested
    until the fixtures above forced it. The editor appends, so the original body
    surviving is the evidence that it was handed the record rather than a blank
    buffer — and it runs as a real subprocess, not a stub.
    """
    project = seed_project()
    record_id = create(GOVERNED, "guide", "A Guide")

    result = runner.invoke(app, ["edit", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
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
    project = seed_project()
    record_id = create(GOVERNED, "guide", "Old Title")

    result = runner.invoke(
        app, ["edit", record_id, "--title", "New Title", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    assert "appended by the editor" not in body
    assert "Original body." in body
    assert frontmatter_of(written_path(project, result.stdout))["title"] == "New Title"


def test_edit_changes_a_task_while_it_is_open_and_after_it_is_closed() -> None:
    """A task takes an edit in every status, and no edit moves the status (GAPS U44).

    Both halves are the point. An uneditable task goes stale and then gets quoted
    as fact, and the old repair — close it and write a replacement — split one
    item's history in two. `bm mark` and `bm done` stay the only things that move
    `status`, which is why it is asserted on both sides of the close.
    """
    project = seed_project()
    record_id = create(GOVERNED, "task", "Move The Backups")

    while_open = runner.invoke(
        app,
        [
            "edit",
            record_id,
            "--title",
            "Move The Backups Off The Old Disk",
            "-b",
            "Rewritten while open.",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert while_open.exit_code == 0, while_open.output
    path = written_path(project, while_open.stdout)
    opened = frontmatter_of(path)
    assert opened["title"] == "Move The Backups Off The Old Disk"
    assert opened["status"] == "open"
    assert "Rewritten while open." in path.read_text(encoding="utf-8")

    closed = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])
    assert closed.exit_code == 0, closed.output

    after_close = runner.invoke(
        app, ["edit", record_id, "-b", "Rewritten after it closed.", "-p", GOVERNED, "--quiet"]
    )

    assert after_close.exit_code == 0, after_close.output
    done = frontmatter_of(path)
    assert done["status"] == "done"
    assert done["title"] == "Move The Backups Off The Old Disk"
    assert "Rewritten after it closed." in path.read_text(encoding="utf-8")


def test_edit_on_a_finding_names_supersession_and_the_override() -> None:
    """A finding is provisional evidence: correcting it in place destroys the record (§5).

    The refusal stands by default and names both ways out — the successor and
    `--override` (GAPS U44).
    """
    seed_project()
    record_id = create(GOVERNED, "finding", "What We Learned")

    result = runner.invoke(app, ["edit", record_id, "-b", "different", "-p", GOVERNED])

    assert result.exit_code == 1
    assert f"--supersedes {record_id}" in result.stderr
    assert "--override" in result.stderr
    assert result.stdout.strip() == ""
    assert "Original body." in payload_path(
        runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n"
    ).read_text(encoding="utf-8")


def test_edit_a_finding_with_override_rewrites_it_in_place() -> None:
    """`--override` is the way through, for a finding that is wrong rather than superseded.

    A successor to a mistyped title is two records saying one thing, and the
    store's history keeps the old text either way (GAPS U44, W3). The positive
    control is the test above: the same edit without the flag is refused and
    writes nothing.
    """
    project = seed_project()
    record_id = create(GOVERNED, "finding", "What We Learnt")

    result = runner.invoke(
        app,
        ["edit", record_id, "--title", "What We Learned", "--override", "-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    path = written_path(project, result.stdout)
    assert frontmatter_of(path)["title"] == "What We Learned"
    assert "Original body." in path.read_text(encoding="utf-8")


def test_override_on_a_task_edits_and_reports_that_the_flag_did_nothing() -> None:
    """A task never refused the edit, so the flag is accepted and named as a no-op.

    Accepting it silently teaches an agent to pass it everywhere, which is how a
    flag that exists to be deliberate becomes boilerplate. It is a notice rather
    than an error because the edit itself is well-formed (contract rules 4 and 5).
    """
    project = seed_project()
    record_id = create(GOVERNED, "task", "Move The Backups")

    result = runner.invoke(
        app, ["edit", record_id, "-b", "Rewritten.", "--override", "-p", GOVERNED]
    )

    assert result.exit_code == 0, result.output
    assert "--override has no effect on a task" in result.stdout
    assert "Rewritten." in written_path(project, result.stdout).read_text(encoding="utf-8")


# --- bm edit --rel (GAPS U14) ---


def test_edit_appends_a_relation_and_leaves_the_title_and_body_alone() -> None:
    """`--rel` adds an edge and changes nothing else about the record.

    Appending is the whole claim: a second `--rel` on the same record joins the
    first under one `## Relations` heading rather than replacing it, because the
    edges already there are facts somebody recorded.
    """
    project = seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")
    first_target = create(GOVERNED, "finding", "Where It Came From")
    second_target = create(GOVERNED, "state", "How Things Stand")

    first = runner.invoke(
        app,
        ["edit", guide, "--rel", f"derived_from:{first_target}", "-p", GOVERNED, "--quiet"],
    )
    assert first.exit_code == 0, first.output

    second = runner.invoke(
        app,
        ["edit", guide, "--rel", f"relates_to:{second_target}", "-p", GOVERNED, "--quiet"],
    )
    assert second.exit_code == 0, second.output

    body = written_path(project, second.stdout).read_text(encoding="utf-8")
    assert body.count("## Relations") == 1
    assert f"- derived_from [[{first_target}]]" in body
    assert f"- relates_to [[{second_target}]]" in body
    # Nothing else moved: the title and the body are what `bm new` wrote.
    assert "Original body." in body
    assert frontmatter_of(written_path(project, second.stdout))["title"] == "How To Restore"


def test_edit_rejects_a_relation_type_the_project_does_not_declare() -> None:
    """The relation type is closed vocabulary here too, with the same message."""
    seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")
    target = create(GOVERNED, "finding", "A Cause")

    result = runner.invoke(app, ["edit", guide, "--rel", f"caused_by:{target}", "-p", GOVERNED])

    assert result.exit_code == 1
    assert f"'caused_by' is not a relation type project '{GOVERNED}' declares" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_rejects_a_rel_target_that_does_not_exist() -> None:
    """An edge to a record the project does not hold is refused before the write."""
    seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")

    result = runner.invoke(app, ["edit", guide, "--rel", "relates_to:tnd-aaaa1111", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "--rel names 'tnd-aaaa1111'" in result.stderr
    assert result.stdout.strip() == ""
    unchanged = payload_path(
        runner.invoke(app, ["path", guide, "-p", GOVERNED]).stdout + "\n"
    ).read_text(encoding="utf-8")
    assert "## Relations" not in unchanged


# --- bm edit --body keeps the edges (GAPS U17) ---


def test_edit_body_carries_the_relations_section_over() -> None:
    """A body edit restates the prose, not the edges: `## Relations` survives it."""
    project = seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")
    target = create(GOVERNED, "finding", "Where It Came From")
    linked = runner.invoke(
        app, ["edit", guide, "--rel", f"derived_from:{target}", "-p", GOVERNED, "--quiet"]
    )
    assert linked.exit_code == 0, linked.output

    result = runner.invoke(
        app, ["edit", guide, "--body", "Only prose now.", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    # Positive control: the prose really was replaced.
    assert "Only prose now." in body
    assert "Original body." not in body
    assert body.count("## Relations") == 1
    assert f"- derived_from [[{target}]]" in body
    assert (("derived_from", target)) in indexed_relations(project, guide)


def test_edit_body_that_writes_its_own_relations_section_stands_as_written() -> None:
    """A replacement that carries a `## Relations` heading says what it means; nothing is
    carried over it, so the file never ends up with two headings."""
    project = seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")
    old_target = create(GOVERNED, "finding", "Where It Came From")
    new_target = create(GOVERNED, "state", "How Things Stand")
    linked = runner.invoke(
        app, ["edit", guide, "--rel", f"derived_from:{old_target}", "-p", GOVERNED, "--quiet"]
    )
    assert linked.exit_code == 0, linked.output

    replacement = f"New prose.\n\n## Relations\n- relates_to [[{new_target}]]\n"
    result = runner.invoke(app, ["edit", guide, "--body", replacement, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    assert body.count("## Relations") == 1
    assert f"- relates_to [[{new_target}]]" in body
    assert f"[[{old_target}]]" not in body


@pytest.mark.usefixtures("stdin_looks_like_a_terminal", "truncating_editor")
def test_edit_in_the_editor_replaces_relations_the_user_removed() -> None:
    """The editor opens on the whole body, relations included: a saved buffer without
    them is a deletion the user made, and nothing is carried back over it."""
    project = seed_project()
    guide = create(GOVERNED, "guide", "How To Restore")
    target = create(GOVERNED, "finding", "Where It Came From")
    linked = runner.invoke(
        app, ["edit", guide, "--rel", f"derived_from:{target}", "-p", GOVERNED, "--quiet"]
    )
    assert linked.exit_code == 0, linked.output

    result = runner.invoke(app, ["edit", guide, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    assert "Only prose now." in body
    assert "## Relations" not in body


# --- a relations-only edit is allowed on every type (GAPS U18) ---


@pytest.mark.parametrize("note_type", ["task", "finding"])
def test_edit_rel_alone_is_allowed_on_a_closed_type(note_type: str) -> None:
    """An edge adds a link and rewrites nothing the record claims, so the type
    refusal that guards a finding's evidence does not apply. A task is covered
    for the same reason it was before U44 widened `bm edit`: `--rel` alone is a
    link, not a rewrite, and it needs no flag on any type."""
    project = seed_project()
    record_id = create(GOVERNED, note_type, "The Record")
    target = create(GOVERNED, "finding", "Where It Came From")

    result = runner.invoke(
        app, ["edit", record_id, "--rel", f"derived_from:{target}", "-p", GOVERNED, "--quiet"]
    )

    assert result.exit_code == 0, result.output
    body = written_path(project, result.stdout).read_text(encoding="utf-8")
    assert f"- derived_from [[{target}]]" in body
    assert "Original body." in body
    assert ("derived_from", target) in indexed_relations(project, record_id)


def test_edit_rel_with_a_title_on_a_finding_is_still_refused() -> None:
    """The exemption is for a relations-only edit; anything that rewrites the record
    keeps the refusal, and nothing is written."""
    seed_project()
    record_id = create(GOVERNED, "finding", "What We Learned")
    target = create(GOVERNED, "finding", "Where It Came From")

    result = runner.invoke(
        app,
        ["edit", record_id, "--rel", f"derived_from:{target}", "--title", "New", "-p", GOVERNED],
    )

    assert result.exit_code == 1
    assert f"--supersedes {record_id}" in result.stderr
    unchanged = payload_path(
        runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n"
    ).read_text(encoding="utf-8")
    assert "## Relations" not in unchanged


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


# --- bm edit --set: a profile's declared fields (GAPS V-J1) ---

# One project's declared extras, one of each kind the vocabulary allows.
PROFILE_FIELDS: dict[str, Any] = {
    "owner": {"kind": "string"},
    "commissioned": {"kind": "date"},
    "tier": {"kind": "enum", "values": ["gold", "silver"]},
}


def test_edit_sets_declared_fields_on_a_profile_and_merges_later_ones() -> None:
    """A profile accretes facts, and its declared fields are where they land (§4 item 4).

    Two claims in one run because they are the same claim from both sides: a
    `--set` writes the field, and a *later* `--set` on one field leaves the other
    alone — the frontmatter is merged, not replaced.
    """
    project = seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "profile", "The Mail Server")

    first = runner.invoke(
        app,
        [
            "edit",
            record_id,
            "--set",
            "owner=platform",
            "--set",
            "tier=gold",
            "-p",
            GOVERNED,
            "--quiet",
        ],
    )

    assert first.exit_code == 0, first.output
    path = written_path(project, first.stdout)
    metadata = frontmatter_of(path)
    assert metadata["owner"] == "platform"
    assert metadata["tier"] == "gold"
    # The body and the file are untouched: `--set` states a field, nothing else.
    assert "Original body." in path.read_text(encoding="utf-8")
    assert path.parent.parent == project.path

    second = runner.invoke(
        app, ["edit", record_id, "--set", "owner=storage", "-p", GOVERNED, "--quiet"]
    )

    assert second.exit_code == 0, second.output
    after = frontmatter_of(written_path(project, second.stdout))
    assert after["owner"] == "storage"
    assert after["tier"] == "gold"


def test_edit_refuses_a_set_on_a_type_that_is_not_a_profile() -> None:
    """Only a profile has declared fields; on a guide the frontmatter is what `bm new` wrote."""
    seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "guide", "A Guide")

    result = runner.invoke(app, ["edit", record_id, "--set", "owner=platform", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "only a profile carries declared fields" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_refuses_a_field_the_project_does_not_declare() -> None:
    """Agents select from the vocabulary; they never extend it from a write (GAPS W4).

    The positive control is the second run: a declared field on the same record
    succeeds, so the refusal is about the name and not about `--set` itself.
    """
    seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "profile", "The Mail Server")

    result = runner.invoke(app, ["edit", record_id, "--set", "invented=x", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "is not a field project 'governed' declares" in result.stderr
    assert "commissioned, owner, tier" in result.stderr
    assert result.stdout.strip() == ""

    allowed = runner.invoke(
        app, ["edit", record_id, "--set", "owner=platform", "-p", GOVERNED, "--quiet"]
    )
    assert allowed.exit_code == 0, allowed.output


def test_edit_refuses_a_set_once_field() -> None:
    """`--set` must not become a way back into the fields `bm new` owns (§4)."""
    seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "profile", "The Mail Server")
    before = frontmatter_of(
        payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    )

    result = runner.invoke(app, ["edit", record_id, "--set", "source=invented", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "'source' is set once and cannot change" in result.stderr
    after = frontmatter_of(
        payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    )
    assert after["source"] == before["source"]


def test_edit_refuses_a_declared_value_the_vocabulary_does_not_allow() -> None:
    """The checker still judges the *value* on the accepted write path (GAPS V-J1).

    `--set` checks the field's name and its mutability; whether `bronze` is a
    legal `tier` is the funnel's rule, and this proves the write reaches it.
    """
    seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "profile", "The Mail Server")

    result = runner.invoke(app, ["edit", record_id, "--set", "tier=bronze", "-p", GOVERNED])

    assert result.exit_code == 1
    assert "bronze" in result.stderr
    assert result.stdout.strip() == ""


def test_edit_refuses_a_set_argument_that_is_not_a_pair() -> None:
    """`--set since` and `--set owner=` both look like a change and are not one."""
    seed_project(fields=PROFILE_FIELDS)
    record_id = create(GOVERNED, "profile", "The Mail Server")

    for argument in ("owner", "owner="):
        result = runner.invoke(app, ["edit", record_id, "--set", argument, "-p", GOVERNED])
        assert result.exit_code == 1, result.output
        assert "--set takes 'name=value'" in result.stderr


def test_edit_refuses_a_set_on_an_ungoverned_project() -> None:
    """An absent vocabulary declares no fields, so there is no declared field to set (W4)."""
    seed_project(UNGOVERNED, governed=False)
    record_id = create(UNGOVERNED, "profile", "The Mail Server")

    result = runner.invoke(app, ["edit", record_id, "--set", "owner=platform", "-p", UNGOVERNED])

    assert result.exit_code == 1
    assert "declares no vocabulary" in result.stderr
    assert "bm types" in result.stderr


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
    assert "'shipped' is not a status project 'governed' declares" in result.stderr
    assert "Allowed: open, doing, blocked, shelved, done, dropped." in result.stderr
    # The fix is a human's edit to the vocabulary file, so the message names it
    # (GAPS U23): a project governed before a status joined the defaults has a
    # `statuses:` list of its own, and a present key replaces the defaults.
    assert "vocabulary.yml to enable." in result.stderr
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
    assert "only a task or a plan carries a status" in result.stderr
    assert result.stdout.strip() == ""


def test_mark_and_done_move_a_plan_like_a_task() -> None:
    """A plan shares the task lifecycle (GAPS U38): mark and done both take it."""
    seed_project()
    record_id = create(GOVERNED, "plan", "Uplevel The App")
    assert record_id.startswith("plan-")

    marked = runner.invoke(app, ["mark", record_id, "doing", "-p", GOVERNED, "--quiet"])
    assert marked.exit_code == 0, marked.output
    assert marked.stdout.strip().splitlines()[0].split("  ") == [record_id, "plan", "doing"]

    closed = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])
    assert closed.exit_code == 0, closed.output
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    assert frontmatter_of(path)["status"] == "done"


def test_a_stage_task_links_part_of_its_plan() -> None:
    """`part_of` is a default relation (GAPS U38): membership survives a body rewrite."""
    project = seed_project()
    plan_id = create(GOVERNED, "plan", "Uplevel The App")
    stage_id = create(GOVERNED, "task", "Stage One")

    result = runner.invoke(
        app,
        ["edit", stage_id, "--rel", f"part_of:{plan_id}", "-p", GOVERNED, "--quiet"],
    )

    assert result.exit_code == 0, result.output
    assert ("part_of", plan_id) in indexed_relations(project, stage_id)


def test_done_is_exactly_mark_done() -> None:
    """`bm done` sets the same field to the same value, through the same path."""
    seed_project()
    record_id = create(GOVERNED, "task", "Finish It")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[0].endswith("  done")
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    assert frontmatter_of(path)["status"] == "done"


def test_closing_a_task_leaves_the_headline_alone_and_asks_about_it() -> None:
    """GAPS U24: the headline is composed, never derived — closing work prompts instead."""
    from basic_memory.services.headline import set_headline

    project = seed_project()
    set_headline(project.external_id, "ship the verbs")
    record_id = create(GOVERNED, "task", "The Only Task")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED])

    assert result.exit_code == 0, result.output
    # The file is untouched: no task write derives it any more.
    lines = headline_path(project.external_id).read_text(encoding="utf-8").splitlines()
    assert lines == ["---", "headline: ship the verbs", "---"]
    # The verb asks whether the line is still right, quoting it.
    assert 'headline: "ship the verbs" — still right?' in result.stdout


def test_a_task_write_creates_no_headline() -> None:
    """The other half of GAPS U24: `bm new` no longer mints a derived headline."""
    project = seed_project()
    create(GOVERNED, "task", "The Only Task")

    assert not headline_path(project.external_id).exists()


def test_closing_with_no_headline_set_nudges_toward_setting_one() -> None:
    """The prompt teaches the 30-char limit before an agent can trip on it."""
    project = seed_project()
    record_id = create(GOVERNED, "task", "The Only Task")
    assert not headline_path(project.external_id).exists()

    result = runner.invoke(app, ["mark", record_id, "shelved", "-p", GOVERNED])

    assert result.exit_code == 0, result.output
    assert "no headline set" in result.stdout
    assert "max 30 chars" in result.stdout


def test_marking_a_task_doing_asks_nothing_about_the_headline() -> None:
    """`doing` leaves the task open, so nothing about "what is next" changed."""
    seed_project()
    record_id = create(GOVERNED, "task", "The Only Task")

    result = runner.invoke(app, ["mark", record_id, "doing", "-p", GOVERNED])

    assert result.exit_code == 0, result.output
    assert "headline" not in result.stdout


def test_quiet_hides_the_headline_prompt() -> None:
    """--quiet is the hint switch, and the prompt is a hint (contract rule 4)."""
    seed_project()
    record_id = create(GOVERNED, "task", "The Only Task")

    result = runner.invoke(app, ["done", record_id, "-p", GOVERNED, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "headline" not in result.stdout


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


def test_mark_shelved_and_back_to_open_round_trips() -> None:
    """`shelved` parks a task; `open` revives it (GAPS U23).

    Nothing about the record moves but the status, so the round trip has to end
    where it started — a park that lost the body would be a park nobody uses.
    """
    seed_project()
    record_id = create(GOVERNED, "task", "Rework The Importer", body="Line one.\n")
    path = payload_path(runner.invoke(app, ["path", record_id, "-p", GOVERNED]).stdout + "\n")
    body_before = path.read_text(encoding="utf-8").split("---\n", 2)[2]

    shelved = runner.invoke(app, ["mark", record_id, "shelved", "-p", GOVERNED, "--quiet"])

    assert shelved.exit_code == 0, shelved.output
    assert frontmatter_of(path)["status"] == "shelved"

    revived = runner.invoke(app, ["mark", record_id, "open", "-p", GOVERNED, "--quiet"])

    assert revived.exit_code == 0, revived.output
    assert frontmatter_of(path)["status"] == "open"
    assert path.read_text(encoding="utf-8").split("---\n", 2)[2] == body_before
