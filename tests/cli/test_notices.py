"""The per-command notice (GAPS W5 mechanism B).

Three layers, because the notice has three separable claims:

- ``notice_lines`` renders W8's order and its two-line cap. Pure, no database.
- The CLI tests drive real verbs through the real command path with the count
  gather stubbed, because the claims there are about *wiring*: after the payload,
  dropped by ``--quiet``, suppressed on ``doctor``, and never able to move an
  exit code.
- The gather tests run the real function against a real database and a real
  ``vocabulary.yml``, because the claim there is that a vocabulary edit changes
  the count on the very next command.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from typer.testing import CliRunner

from basic_memory import db
import basic_memory.cli.notices as notices
from basic_memory.cli.app import app
from basic_memory.cli.notices import NoticeCounts, TopReason, gather_notice_counts, notice_lines
from basic_memory.cli.scope import ReadScope
from basic_memory.models import Entity, Project
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.violation_repository import ViolationRepository
from basic_memory.vocabulary import vocabulary_path

runner = CliRunner()

PINNED = ReadScope(project="alpha", origin="flag")
UNSCOPED = ReadScope(project=None, origin="unscoped")


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run every test from a directory with no `.bm.yml` above it.

    Scope depends on cwd, so a marker anywhere above the checkout would silently
    turn the unscoped cases into pinned ones.
    """
    monkeypatch.chdir(tmp_path)


# --- Rendering: W8's order and cap ---


def test_a_clean_corpus_prints_no_notice() -> None:
    """Silence is the whole point: the line exists only when something is true."""
    assert notice_lines(NoticeCounts()) == []


def test_notices_print_in_w8_priority_order() -> None:
    """Violations, then expired reviews — the two highest of W8's four buildable rows."""
    lines = notice_lines(NoticeCounts(violations=4, review_due=2, inbox=9, dirty=3))

    assert lines[0].startswith("4 records need attention")
    assert lines[1].startswith("2 records past review-by")


def test_at_most_two_notices_print() -> None:
    """W8 caps the notice at two lines; more than two is a report, not a notice."""
    lines = notice_lines(NoticeCounts(violations=4, review_due=2, inbox=9, dirty=3))

    assert len(lines) == 2
    assert "inbox" not in "\n".join(lines)
    assert "uncommitted" not in "\n".join(lines)


def test_the_cap_lets_lower_conditions_through_when_the_higher_ones_are_quiet() -> None:
    """Positive control for the cap: it is a limit, not a filter on the last two rows."""
    lines = notice_lines(NoticeCounts(inbox=9, dirty=3))

    assert lines == [
        "9 unfiled records in the inbox — run 'bm doctor --only hygiene'",
        "3 note files have uncommitted changes — run 'bm history dirty'",
    ]


def test_an_unscoped_notice_names_the_project_its_top_reason_came_from() -> None:
    """A roll-up that says what is wrong but not where is not actionable (W5-C)."""
    counts = NoticeCounts(
        violations=4,
        top_reason=TopReason(rule="unknown-type", field="type", count=3, project="research"),
        pinned=False,
    )

    assert notice_lines(counts) == [
        "4 records need attention (3 unknown-type on 'type' in 'research') — run 'bm doctor'"
    ]


def test_a_pinned_notice_leaves_the_project_out() -> None:
    """You named the project; repeating it in every line buys nothing."""
    counts = NoticeCounts(
        violations=1,
        top_reason=TopReason(rule="unknown-type", field="type", count=1, project="research"),
        pinned=True,
    )

    assert notice_lines(counts) == [
        "1 record needs attention (1 unknown-type on 'type') — run 'bm doctor'"
    ]


def test_an_unreadable_vocabulary_prints_above_every_count() -> None:
    """The line that says the counts are incomplete has to come first (V-J2)."""
    from basic_memory.cli.direct import UnreadableVocabulary

    counts = NoticeCounts(
        violations=4,
        unreadable=(
            UnreadableVocabulary(project="alpha", path="/store/a/vocabulary.yml", reason="boom"),
            UnreadableVocabulary(project="beta", path="/store/b/vocabulary.yml", reason="boom"),
        ),
        pinned=False,
    )

    lines = notice_lines(counts)

    assert lines[0] == (
        "vocabulary unreadable in 'alpha' — its records are not counted below: "
        "/store/a/vocabulary.yml (+1 more) — run 'bm types'"
    )
    # Positive control on the cap: the count below still prints, one line down.
    assert lines[1].startswith("4 records need attention")


def test_a_reason_with_no_field_names_the_rule_alone() -> None:
    """`field` is empty when the record as a whole is at fault, not one key."""
    counts = NoticeCounts(
        violations=2,
        top_reason=TopReason(rule="set-once-changed", field="", count=2, project="alpha"),
        pinned=True,
    )

    assert "(2 set-once-changed)" in notice_lines(counts)[0]


# --- Wiring: where the line lands, and what suppresses it ---


@pytest.fixture
def stub_counts(monkeypatch):
    """Answer the count gather without a database, recording the scopes asked for."""

    def _stub(counts: NoticeCounts) -> list[ReadScope]:
        seen: list[ReadScope] = []

        async def fake_gather(scope: ReadScope) -> NoticeCounts:
            seen.append(scope)
            return counts

        monkeypatch.setattr(notices, "gather_notice_counts", fake_gather)
        return seen

    return _stub


def test_the_notice_follows_the_payload_and_never_precedes_it(stub_counts) -> None:
    """Contract rule 4: notices come after the payload, on stdout."""
    stub_counts(NoticeCounts(violations=4))

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[-2].endswith(" projects")
    assert lines[-1] == "4 records need attention — run 'bm doctor'"


def test_quiet_drops_the_notice(stub_counts) -> None:
    """Contract rule 7: --quiet leaves the payload alone and drops the rest."""
    seen = stub_counts(NoticeCounts(violations=4))

    result = runner.invoke(app, ["project", "list", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "need attention" not in result.stdout
    # Not merely unprinted: --quiet must not pay for the query either.
    assert seen == []


def test_violations_do_not_change_the_exit_code(stub_counts) -> None:
    """Violations are corpus state, not command failure (W5-B)."""
    stub_counts(NoticeCounts(violations=99, review_due=99, inbox=99, dirty=99))

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.output


def test_a_real_addressing_failure_still_exits_one(stub_counts) -> None:
    """Positive control: the notice must not have made every run succeed."""
    stub_counts(NoticeCounts(violations=4))

    result = runner.invoke(app, ["project", "ls", "--name", "no-such-project"])

    assert result.exit_code == 1
    assert "need attention" not in result.stdout


def test_doctor_prints_no_notice(stub_counts) -> None:
    """Doctor is about to print every row the notice would summarize."""
    seen = stub_counts(NoticeCounts(violations=4))

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "need attention" not in result.stdout
    assert seen == []


def test_a_failure_while_gathering_prints_nothing_and_keeps_the_exit_code(monkeypatch) -> None:
    """The payload already succeeded; a broken notice must not fail the command."""

    async def explode(scope: ReadScope) -> NoticeCounts:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(notices, "gather_notice_counts", explode)

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().splitlines()[-1].endswith(" projects")


def test_project_list_asks_about_every_project(stub_counts) -> None:
    """The notice covers exactly what the verb read, and the listing read all of it."""
    seen = stub_counts(NoticeCounts())

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.output
    assert [scope.project for scope in seen] == [None]


# --- Gathering: the real counts, against a real database ---


def govern(project: Project, **content: Any) -> None:
    """Write the project's vocabulary file."""
    path = vocabulary_path(project.external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(content), encoding="utf-8")


async def make_entity(
    session: AsyncSession,
    project_id: int,
    metadata: dict[str, Any],
    file_path: str,
) -> Entity:
    """Create an indexed record carrying ``metadata`` as its frontmatter."""
    return await EntityRepository(project_id=project_id).create(
        session,
        {
            "project_id": project_id,
            "title": metadata["title"],
            "note_type": metadata["type"],
            "permalink": metadata["permalink"],
            "file_path": file_path,
            "content_type": "text/markdown",
            "entity_metadata": metadata,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )


def record(index: int, **overrides: Any) -> dict[str, Any]:
    """Frontmatter for one record, with a unique id and permalink.

    ``runbook`` is a type no default vocabulary declares, and ``type``
    short-circuits the checker, so a governed project sees exactly one violation
    per record and every count below is unambiguous.
    """
    return {
        "type": "runbook",
        "id": f"tnd-w5b-{index:04d}",
        "permalink": f"tnd-w5b-{index:04d}",
        "title": f"Record {index}",
        "source": "human",
        **overrides,
    }


async def make_project(session: AsyncSession, name: str, path: Any) -> Project:
    return await ProjectRepository().create(
        session,
        {"name": name, "path": str(path), "is_active": True, "is_default": False},
    )


@pytest.mark.asyncio
async def test_the_count_follows_the_vocabulary_on_the_very_next_command(
    session_maker,
    test_project: Project,
) -> None:
    """A vocabulary edit changes the count immediately, with no file touched.

    This is the whole reason the gather revalidates before it counts: nothing on
    the index path would look again, because no note changed (GAPS W5 item 4).
    """
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, test_project.id, record(1), "notes/off.md")

    scope = ReadScope(project=test_project.name, origin="flag")

    # Ungoverned: no rule can apply, so there is nothing to report.
    assert (await gather_notice_counts(scope)).violations == 0

    govern(test_project)
    counts = await gather_notice_counts(scope)
    assert counts.violations == 1
    assert counts.top_reason is not None
    assert (counts.top_reason.rule, counts.top_reason.field) == ("unknown-type", "type")

    # Legalising the type clears the row on the next command, not the next index.
    govern(test_project, types=["runbook"])
    assert (await gather_notice_counts(scope)).violations == 0


@pytest.mark.asyncio
async def test_an_unscoped_gather_rolls_up_and_names_the_top_reason_s_project(
    session_maker,
    test_project: Project,
    config_home,
) -> None:
    """Unscoped counts every project and says which one the top reason is in.

    The rows come from the gather's own revalidation pass, not from a seeded
    table: what is under test is the number a real corpus produces.
    """
    async with db.scoped_session(session_maker) as session:
        other = await make_project(session, "other-project", config_home / "other")
        await make_entity(session, test_project.id, record(2), "notes/a.md")
        await make_entity(session, other.id, record(3), "notes/b.md")
        await make_entity(session, other.id, record(4), "notes/c.md")
    govern(test_project)
    govern(other)

    counts = await gather_notice_counts(ReadScope(project=None, origin="unscoped"))

    assert counts.violations == 3
    assert counts.pinned is False
    assert counts.top_reason is not None
    assert counts.top_reason.count == 2
    assert counts.top_reason.project == "other-project"


@pytest.mark.asyncio
async def test_a_pinned_gather_never_reports_another_project_s_rows(
    session_maker,
    test_project: Project,
    config_home,
) -> None:
    """A marked tree must not be handed another project's problems (GAPS W5-C)."""
    async with db.scoped_session(session_maker) as session:
        other = await make_project(session, "noisy-project", config_home / "noisy")
        await make_entity(session, other.id, record(5), "notes/d.md")
    govern(test_project)
    govern(other)

    # One unscoped pass first, so the other project genuinely holds rows. Without
    # it a zero below would prove only that nothing was ever recorded.
    assert (await gather_notice_counts(ReadScope(project=None, origin="unscoped"))).violations == 1

    counts = await gather_notice_counts(ReadScope(project=test_project.name, origin="flag"))

    assert counts.violations == 0
    assert counts.pinned is True
    async with db.scoped_session(session_maker) as session:
        assert await ViolationRepository().count_for_projects(session, [other.id]) == 1


@pytest.mark.asyncio
async def test_expired_reviews_and_the_inbox_pile_are_counted(
    session_maker,
    test_project: Project,
) -> None:
    """W8's second and third conditions, with a positive control on each side."""
    expired = (date.today() - timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=30)).isoformat()

    async with db.scoped_session(session_maker) as session:
        await make_entity(
            session, test_project.id, record(6, **{"review-by": expired}), "notes/e.md"
        )
        # Not counted: the review is still ahead of today.
        await make_entity(
            session, test_project.id, record(7, **{"review-by": future}), "notes/f.md"
        )
        await make_entity(session, test_project.id, record(8, type="inbox"), "notes/g.md")
        # Not counted: a filed record is not in the inbox.
        await make_entity(session, test_project.id, record(9, type="task"), "notes/h.md")

    counts = await gather_notice_counts(ReadScope(project=test_project.name, origin="flag"))

    assert counts.review_due == 1
    assert counts.inbox == 1


@pytest.mark.asyncio
async def test_the_inbox_count_includes_a_record_that_proposes_nothing(
    session_maker,
    test_project: Project,
) -> None:
    """GAPS U5: a deliberate inbox record is still unfiled, and the notice still says so.

    U5 softened `bm doctor`'s demand on such a record. This is the half that must
    not move with it: the pile is a pile whether or not anything in it names a type
    it wants to become. Both shapes are seeded, so a check that counted only
    proposals would come back 1.
    """
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, test_project.id, record(1, type="inbox"), "notes/plain.md")
        await make_entity(
            session,
            test_project.id,
            record(2, type="inbox", **{"proposed-type": "runbook"}),
            "notes/proposing.md",
        )

    counts = await gather_notice_counts(ReadScope(project=test_project.name, origin="flag"))

    assert counts.inbox == 2


def break_vocabulary(project: Project) -> Path:
    """Write a `vocabulary.yml` the parser refuses, and return its path."""
    path = vocabulary_path(project.external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("nonsense_key: 1\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_one_malformed_vocabulary_leaves_the_other_projects_counted(
    session_maker,
    test_project: Project,
    config_home,
) -> None:
    """GAPS V-J2: one typo used to silence the notice for the whole registry.

    Real files on both sides, because the claim is about what the parser does
    with them: the broken project contributes nothing and is named, and the
    readable one still produces its count — the positive control that proves the
    pass ran rather than aborting quietly.
    """
    async with db.scoped_session(session_maker) as session:
        broken = await make_project(session, "broken-project", config_home / "broken")
        await make_entity(session, test_project.id, record(10), "notes/i.md")
        await make_entity(session, broken.id, record(11), "notes/j.md")
    govern(test_project)
    path = break_vocabulary(broken)

    counts = await gather_notice_counts(UNSCOPED)

    assert counts.violations == 1
    assert [report.project for report in counts.unreadable] == ["broken-project"]
    assert counts.unreadable[0].path == str(path)
    assert "nonsense_key" in counts.unreadable[0].reason
    assert notice_lines(counts)[0].startswith("vocabulary unreadable in 'broken-project'")


@pytest.mark.asyncio
async def test_a_pinned_gather_on_a_broken_vocabulary_reports_the_file_and_no_counts(
    session_maker,
    test_project: Project,
) -> None:
    """Pinned to the broken project, the file is the only thing there is to say.

    Its violation rows are stale — the pass that would have refreshed them is the
    one that failed — so reporting them as current is the reduced-count failure
    V-J2 names. The positive control is the second gather: with the file fixed,
    the same corpus produces a count.
    """
    async with db.scoped_session(session_maker) as session:
        await make_entity(session, test_project.id, record(12), "notes/k.md")
    path = break_vocabulary(test_project)
    scope = ReadScope(project=test_project.name, origin="flag")

    counts = await gather_notice_counts(scope)

    assert counts.violations == 0
    assert counts.review_due == 0
    assert counts.inbox == 0
    assert [report.path for report in counts.unreadable] == [str(path)]

    govern(test_project)
    fixed = await gather_notice_counts(scope)
    assert fixed.unreadable == ()
    assert fixed.violations == 1


@pytest.mark.asyncio
async def test_the_dirty_count_does_not_create_the_note_store(
    session_maker,
    test_project: Project,
) -> None:
    """A report must not create the thing it reports on.

    ``dirty_paths`` runs ``ensure_store_repo``, which initializes the repository.
    The notice runs on every project-touching verb, so it uses the counting path
    that leaves an absent store absent.
    """
    from basic_memory.store.history import store_path

    counts = await gather_notice_counts(ReadScope(project=test_project.name, origin="flag"))

    assert counts.dirty == 0
    assert not (store_path() / ".git").exists()
