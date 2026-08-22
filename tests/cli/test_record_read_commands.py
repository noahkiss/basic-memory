"""`bm ls`, `bm show`, `bm path` — the record read verbs (VERBS_PLAN item G).

These drive the **real** caller path: a real database, real project rows, real
files on disk, and the real Typer command. Nothing below stubs the query layer,
because the claims are about what the verbs do end to end — which records they
list, which id they refuse to guess at, and what lands on stdout.

The corpus is seeded through the same file-backed database the CLI opens, so a
seeding pass and a command invocation see the same rows (the shape
`bootstrapped_registry` establishes for CLI tests).
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers ls/show/path on the app; the tests also read its constants.
from basic_memory.cli.commands import records

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

MAIN = "main"
BETA = "beta"


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run every test from a directory with no `.bm.yml` above it.

    Scope depends on cwd, so a marker anywhere above the checkout would silently
    turn the unscoped cases into pinned ones.
    """
    monkeypatch.chdir(tmp_path)


# --- Seeding ---


@dataclass(frozen=True)
class Seeded:
    """Where each seeded project lives on disk."""

    paths: dict[str, Path]

    def file(self, project: str, file_path: str) -> Path:
        return self.paths[project] / file_path


def frontmatter(record_id: str, note_type: str, title: str, **extra: Any) -> dict[str, Any]:
    """A minimal governed record: id and permalink equal, byte for byte (§2)."""
    return {
        "id": record_id,
        "permalink": record_id,
        "type": note_type,
        "title": title,
        "source": "cli",
        **extra,
    }


def seed(
    corpus: dict[str, list[dict[str, Any]]],
    *,
    supersedes: tuple[str, str] | None = None,
    relations: tuple[tuple[str, str, str], ...] = (),
):
    """Create every project and record in ``corpus``, in the database and on disk.

    ``supersedes`` is a ``(successor_id, predecessor_id)`` pair; the edge is
    written on the successor, which is the only direction the schema stores (§5).
    ``relations`` is the general form: ``(relation_type, source_id, target_id)``
    triples, each written on the source, for the incoming-reference tests (U32).
    """

    async def _seed() -> Seeded:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.models import Relation
        from basic_memory.repository.entity_repository import EntityRepository
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            paths: dict[str, Path] = {}
            by_id: dict[str, Any] = {}
            async with db.scoped_session(session_maker) as session:
                repository = ProjectRepository()
                for project_name, entries in corpus.items():
                    project = await repository.get_by_name(session, project_name)
                    if project is None:
                        home = config.data_dir_path / "projects" / project_name
                        home.mkdir(parents=True, exist_ok=True)
                        project = await repository.create(
                            session,
                            {
                                "name": project_name,
                                "path": str(home),
                                "is_active": True,
                                "is_default": False,
                            },
                        )
                    paths[project_name] = Path(project.path)

                    entities = EntityRepository(project_id=project.id)
                    for entry in entries:
                        metadata = entry["metadata"]
                        file_path = entry["file_path"]
                        entity = await entities.create(
                            session,
                            {
                                "project_id": project.id,
                                "title": entry.get("title", metadata.get("title", "")),
                                "note_type": metadata.get("type", "note"),
                                "permalink": entry.get("permalink", metadata.get("permalink")),
                                "file_path": file_path,
                                "content_type": "text/markdown",
                                "entity_metadata": metadata,
                                "created_at": datetime.now(timezone.utc),
                                "updated_at": datetime.now(timezone.utc),
                            },
                        )
                        by_id[str(entity.permalink)] = entity

                        target = Path(project.path) / file_path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(entry["content"], encoding="utf-8")

                edges = tuple(relations)
                if supersedes is not None:
                    edges += (("supersedes", *supersedes),)
                for relation_type, source_id, target_id in edges:
                    source, target = by_id[source_id], by_id[target_id]
                    session.add(
                        Relation(
                            project_id=source.project_id,
                            from_id=source.id,
                            to_id=target.id,
                            to_name=target.permalink,
                            relation_type=relation_type,
                        )
                    )
                if edges:
                    await session.flush()
            return Seeded(paths=paths)
        finally:
            await db.shutdown_db()

    return asyncio.run(_seed())


def note(record_id: str, note_type: str, title: str, body: str = "", **extra: Any) -> dict:
    """One record, with a file body that round-trips through `bm show`."""
    metadata = frontmatter(record_id, note_type, title, **extra)
    rendered = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    return {
        "metadata": metadata,
        "file_path": f"{note_type}s/{record_id}--seed.md",
        "title": title,
        "content": f"---\n{rendered}\n---\n\n{body or title}\n",
    }


TASK = note("tnd-aaaa1111", "task", "Move backups off-container", status="open", area="ops")
DONE_TASK = note("tnd-bbbb2222", "task", "Rotate the deploy key", status="done", area="ops")
FINDING = note(
    "tnd-cccc3333",
    "finding",
    "In-container backup cannot work",
    body="The container is the thing being backed up.",
    **{"event-date": "2026-07-26", "area": "infra"},
)

BASIC_CORPUS = {MAIN: [TASK, DONE_TASK, FINDING]}


# --- bm ls ---


def test_ls_lists_every_record_with_a_count_line() -> None:
    """Contract rules 1-3: one record per line, id first, count at the end."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "3 records"
    assert lines[0].startswith("tnd-cccc3333")
    assert "finding" in lines[0] and "In-container backup cannot work" in lines[0]
    assert any(line.startswith("tnd-aaaa1111") and "open" in line for line in lines)


def test_ls_count_line_reads_as_english_at_every_count() -> None:
    """GAPS U13: `bm ls` printed `1 records`; `bm new` already printed `1 record`.

    Zero and three are the positive controls — the plural must survive the fix.
    """
    seed(BASIC_CORPUS)

    def count_line(*filters: str) -> str:
        result = runner.invoke(app, ["ls", "--project", MAIN, "--quiet", *filters])
        assert result.exit_code == 0, result.output
        return result.stdout.strip().splitlines()[-1]

    assert count_line() == "3 records"
    assert count_line("--type", "finding") == "1 record"
    assert count_line("--type", "guide") == "0 records"


def test_ls_filters_by_type() -> None:
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--type", "finding", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "tnd-cccc3333" in result.stdout
    assert "tnd-aaaa1111" not in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "1 record"


def test_ls_filters_by_status() -> None:
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--status", "open", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "tnd-aaaa1111" in result.stdout
    assert "tnd-bbbb2222" not in result.stdout
    # A record with no status is a miss, not a wildcard.
    assert "tnd-cccc3333" not in result.stdout


def test_ls_filters_by_area() -> None:
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--area", "infra", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "tnd-cccc3333" in result.stdout
    assert "tnd-aaaa1111" not in result.stdout


def test_ls_with_no_matches_prints_zero_records_and_exits_zero() -> None:
    """Contract rule 5: a well-scoped request whose answer is nothing is a result."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--status", "blocked", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "0 records"


def test_an_unscoped_listing_names_the_project_on_every_row() -> None:
    """W5-C: a roll-up that does not say where a row came from is not actionable."""
    seed({MAIN: [TASK], BETA: [FINDING]})

    result = runner.invoke(app, ["ls", "--quiet"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == "2 records"
    assert any(line.startswith("tnd-aaaa1111") and MAIN in line for line in lines)
    assert any(line.startswith("tnd-cccc3333") and BETA in line for line in lines)


def test_a_pinned_listing_leaves_the_project_column_out() -> None:
    """You named the project; repeating it on every row buys nothing."""
    seed({MAIN: [TASK], BETA: [FINDING]})

    result = runner.invoke(app, ["ls", "--project", BETA, "--quiet"])

    assert result.exit_code == 0, result.output
    assert BETA not in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "1 record"


def test_limit_caps_the_rows_and_says_more_match() -> None:
    """The count is honest about what was printed; the notice says it was cut short."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--limit", "2"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert "2 records" in lines
    assert "more records match — raise --limit above 2" in lines


def test_a_limit_that_fits_says_nothing_about_more_records() -> None:
    """Positive control for the notice: it fires on truncation, not on --limit."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--limit", "9"])

    assert result.exit_code == 0, result.output
    assert "more records match" not in result.stdout
    assert "3 records" in result.stdout


def test_a_limit_below_one_is_an_error() -> None:
    """An impossible listing is an invalid flag, not an empty result (rule 5)."""
    result = runner.invoke(app, ["ls", "--project", MAIN, "--limit", "0"])

    assert result.exit_code == 1
    assert "--limit must be 1 or more" in result.stderr
    assert result.stdout.strip() == ""


def test_a_note_without_a_record_type_is_not_listed() -> None:
    """`bm ls` answers "what records are here"; an ordinary note is not a record."""
    plain = {
        "metadata": {"title": "Just a note"},
        "file_path": "notes/plain.md",
        "title": "Just a note",
        "permalink": "notes/plain",
        "content": "# Just a note\n",
    }
    seed({MAIN: [TASK, plain]})

    result = runner.invoke(app, ["ls", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "notes/plain" not in result.stdout
    # Positive control: the corpus can produce a row, and did.
    assert "tnd-aaaa1111" in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "1 record"


def test_ls_marks_a_superseded_record_in_the_status_column() -> None:
    """GAPS U3: a dead finding and a live one printed the same blank status column.

    Positive control in the same listing: the successor is not marked, so the
    marker is derived from the inbound edge and not from having one at all.
    """
    successor = note(
        "tnd-dddd4444",
        "finding",
        "Backups now run on the host",
        **{"event-date": "2026-08-01"},
    )
    seed({MAIN: [FINDING, successor]}, supersedes=("tnd-dddd4444", "tnd-cccc3333"))

    result = runner.invoke(app, ["ls", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    rows = {line.split()[0]: line for line in result.stdout.splitlines() if line.startswith("tnd-")}
    assert "superseded" in rows["tnd-cccc3333"]
    assert "superseded" not in rows["tnd-dddd4444"]


def test_ls_leaves_an_unsuperseded_record_unmarked() -> None:
    """The negative control the corpus gives for free: no edges, no markers."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["ls", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "superseded" not in result.stdout


def test_ls_closes_with_its_affordance_and_quiet_drops_it() -> None:
    """W19 item 5: the verb names the next command, and --quiet leaves the payload alone."""
    seed(BASIC_CORPUS)

    loud = runner.invoke(app, ["ls", "--project", MAIN])
    quiet = runner.invoke(app, ["ls", "--project", MAIN, "--quiet"])

    assert loud.exit_code == 0, loud.output
    assert loud.stdout.strip().splitlines()[-1] == records.LS_AFFORDANCE
    assert records.LS_AFFORDANCE not in quiet.stdout


def test_ls_on_an_unknown_project_exits_one() -> None:
    """An unaddressable request is a failure, never an empty listing."""
    result = runner.invoke(app, ["ls", "--project", "no-such-project"])

    assert result.exit_code == 1
    assert "Project not found: 'no-such-project'" in result.stderr
    assert result.stdout.strip() == ""


# --- bm show ---


def test_show_prints_the_file_bytes_verbatim() -> None:
    """Raw content is byte-exact: round-tripping is part of the contract."""
    seeded = seed(BASIC_CORPUS)
    on_disk = seeded.file(MAIN, FINDING["file_path"]).read_text(encoding="utf-8")

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout == on_disk


def test_show_writes_the_file_bytes_untranslated() -> None:
    """Byte-exact holds for the two cases a text read silently breaks.

    Reading through the text layer turns CRLF into LF and raises on any file
    that is not valid UTF-8 — the second one as a traceback, where the contract
    wants one stderr line. The regular corpus is all LF and all ASCII, so it
    cannot produce either failure; this file carries both.
    """
    seeded = seed(BASIC_CORPUS)
    raw = b"---\r\nid: tnd-aaaa1111\r\ntype: task\r\n---\r\n\r\nlatin-1 caf\xe9\r\n"
    seeded.file(MAIN, TASK["file_path"]).write_bytes(raw)

    result = runner.invoke(app, ["show", "tnd-aaaa1111", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout_bytes == raw


def test_show_reports_supersession_as_a_notice_after_the_payload() -> None:
    """D10: the payload stays byte-exact, and the derived line follows it."""
    successor = note(
        "tnd-dddd4444",
        "finding",
        "Backups now run on the host",
        **{"event-date": "2026-08-01"},
    )
    seeded = seed({MAIN: [FINDING, successor]}, supersedes=("tnd-dddd4444", "tnd-cccc3333"))
    on_disk = seeded.file(MAIN, FINDING["file_path"]).read_text(encoding="utf-8")

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith(on_disk)
    assert "superseded by tnd-dddd4444 (2026-08-01)" in result.stdout


def test_quiet_drops_the_supersession_notice_and_the_affordance() -> None:
    """Contract rule 7: --quiet leaves exactly the payload."""
    successor = note("tnd-dddd4444", "finding", "Backups now run on the host")
    seeded = seed({MAIN: [FINDING, successor]}, supersedes=("tnd-dddd4444", "tnd-cccc3333"))
    on_disk = seeded.file(MAIN, FINDING["file_path"]).read_text(encoding="utf-8")

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout == on_disk


def test_show_notices_supersession_on_the_replaced_record_not_the_successor() -> None:
    """GAPS U3: the notice belongs to the record that is dead, in that direction only.

    The edge is stored on the successor, so the naive reading puts the notice
    there — on the one record that does not need it. Both halves are asserted so
    the direction cannot silently flip.
    """
    successor = note("tnd-dddd4444", "finding", "Backups now run on the host")
    seed({MAIN: [FINDING, successor]}, supersedes=("tnd-dddd4444", "tnd-cccc3333"))

    replaced = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])
    assert replaced.exit_code == 0, replaced.output
    assert "superseded by tnd-dddd4444" in replaced.stdout

    live = runner.invoke(app, ["show", "tnd-dddd4444", "--project", MAIN])
    assert live.exit_code == 0, live.output
    assert "superseded by" not in live.stdout


# --- Incoming references (GAPS U32) ---

CORRECTION = note(
    "finding-eeee5555",
    "finding",
    "Correction: the pragmas were not the cause",
    **{"event-date": "2026-08-02"},
)


def test_show_renders_an_incoming_reference_on_the_target() -> None:
    """The stale side of a correction points forward, from the record that misleads.

    The edge lives on the correction; the record a reader lands on via search is
    the old one. Both directions are asserted so the rendering cannot flip to
    the side that already knows.
    """
    seed(
        {MAIN: [FINDING, CORRECTION]},
        relations=(("derived_from", "finding-eeee5555", "tnd-cccc3333"),),
    )

    stale = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])
    assert stale.exit_code == 0, stale.output
    assert (
        '← derived_from by finding-eeee5555 "Correction: the pragmas were not the cause"'
        in stale.stdout
    )

    live = runner.invoke(app, ["show", "finding-eeee5555", "--project", MAIN])
    assert live.exit_code == 0, live.output
    assert "← derived_from" not in live.stdout


def test_quiet_drops_the_incoming_reference() -> None:
    """Derived, so it follows the supersession notice out under --quiet."""
    seeded = seed(
        {MAIN: [FINDING, CORRECTION]},
        relations=(("derived_from", "finding-eeee5555", "tnd-cccc3333"),),
    )
    on_disk = seeded.file(MAIN, FINDING["file_path"]).read_text(encoding="utf-8")

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout == on_disk


def test_show_truncates_a_long_incoming_title() -> None:
    """The title is context, not a second payload — it is cut, with a mark."""
    long_title = "Correction: " + "a really long explanation " * 4
    source = note("finding-ffff6666", "finding", long_title)
    seed(
        {MAIN: [FINDING, source]},
        relations=(("derived_from", "finding-ffff6666", "tnd-cccc3333"),),
    )

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert "← derived_from by finding-ffff6666" in result.stdout
    assert long_title not in result.stdout
    assert "…" in result.stdout


def test_show_renders_every_incoming_reference_on_a_hub_record() -> None:
    """No cap: a hub record lists all seven, not five and a count (GAPS U45).

    U32 printed `MAX_INCOMING = 5` and summarized the rest. The summary named no
    id, so the only way to reach the hidden records was a query the reader had to
    invent — and `bm show` is the record's page.
    """
    sources = [
        note(f"finding-aaaa000{index}", "finding", f"Reference {index}") for index in range(7)
    ]
    seed(
        {MAIN: [FINDING, *sources]},
        relations=tuple(
            ("relates_to", f"finding-aaaa000{index}", "tnd-cccc3333") for index in range(7)
        ),
    )

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("← relates_to by") == len(sources)
    for index in range(7):
        assert f"finding-aaaa000{index}" in result.stdout
    assert "more incoming relations" not in result.stdout


def test_show_renders_every_stage_of_an_eight_stage_plan() -> None:
    """The reproduction behind U45: a plan's own checklist was cut at five.

    Stages point at their plan with `part_of`, so the checklist reaches `bm show`
    as *incoming* references — which is exactly what the cap trimmed. A plan with
    more than five stages is the ordinary case, and the record that most needs
    the block was the one that lost it.
    """
    plan = note("plan-gggg7777", "plan", "Uplevel the app", status="doing")
    stages = [
        note(f"tnd-stage{index:03d}", "task", f"Stage {index}", status="open") for index in range(8)
    ]
    seed(
        {MAIN: [plan, *stages]},
        relations=tuple(
            ("part_of", f"tnd-stage{index:03d}", "plan-gggg7777") for index in range(8)
        ),
    )

    result = runner.invoke(app, ["show", "plan-gggg7777", "--project", MAIN])

    assert result.exit_code == 0, result.output
    for index in range(8):
        assert f'← part_of by tnd-stage{index:03d} (open) "Stage {index}"' in result.stdout
    assert "more incoming relations" not in result.stdout


def test_show_renders_a_plan_as_a_live_checklist_in_body_order() -> None:
    """A plan's outgoing stage links print with each stage's status (U38).

    Body order, not alphabetical: relation insertion follows the body top to
    bottom, and the checklist must read in the order the plan states. The
    finding target earns no row — the block is a checklist, and a record
    without a lifecycle has nothing to check.
    """
    plan = note("plan-gggg7777", "plan", "Uplevel the app", status="doing")
    doing = note("tnd-hhhh8888", "task", "Stage two", status="doing")
    seed(
        {MAIN: [plan, TASK, DONE_TASK, doing, FINDING]},
        relations=(
            # Deliberately not alphabetical by target id.
            ("links_to", "plan-gggg7777", "tnd-bbbb2222"),
            ("links_to", "plan-gggg7777", "tnd-hhhh8888"),
            ("links_to", "plan-gggg7777", "tnd-aaaa1111"),
            ("links_to", "plan-gggg7777", "tnd-cccc3333"),
        ),
    )

    result = runner.invoke(app, ["show", "plan-gggg7777", "--project", MAIN])

    assert result.exit_code == 0, result.output
    first = result.stdout.index('→ tnd-bbbb2222 (done) "Rotate the deploy key"')
    second = result.stdout.index('→ tnd-hhhh8888 (doing) "Stage two"')
    third = result.stdout.index('→ tnd-aaaa1111 (open) "Move backups off-container"')
    assert first < second < third
    assert "→ tnd-cccc3333" not in result.stdout


def test_show_stamps_lifecycle_status_on_both_reference_directions() -> None:
    """A stage names its plan with the plan's status; the plan names the stage back."""
    plan = note("plan-gggg7777", "plan", "Uplevel the app", status="doing")
    stage = note("tnd-hhhh8888", "task", "Stage two", status="blocked")
    seed(
        {MAIN: [plan, stage]},
        relations=(("part_of", "tnd-hhhh8888", "plan-gggg7777"),),
    )

    shown_plan = runner.invoke(app, ["show", "plan-gggg7777", "--project", MAIN])
    assert shown_plan.exit_code == 0, shown_plan.output
    assert '← part_of by tnd-hhhh8888 (blocked) "Stage two"' in shown_plan.stdout

    shown_stage = runner.invoke(app, ["show", "tnd-hhhh8888", "--project", MAIN])
    assert shown_stage.exit_code == 0, shown_stage.output
    assert '→ plan-gggg7777 (doing) "Uplevel the app"' in shown_stage.stdout


def test_show_dedupes_a_twice_linked_stage() -> None:
    """A wikilink plus a part_of to the same stage is one checklist row, not two."""
    plan = note("plan-gggg7777", "plan", "Uplevel the app", status="open")
    seed(
        {MAIN: [plan, TASK]},
        relations=(
            ("links_to", "plan-gggg7777", "tnd-aaaa1111"),
            ("relates_to", "plan-gggg7777", "tnd-aaaa1111"),
        ),
    )

    result = runner.invoke(app, ["show", "plan-gggg7777", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("→ tnd-aaaa1111") == 1


def test_show_separates_a_body_with_no_final_newline_from_the_notice() -> None:
    """GAPS U2: without a separator, the body's last word and the notice are one token.

    The corpus's own files all end in a newline, so they cannot produce the
    failure — this one is written without one, which is the shape every record
    file had before U2's writer fix.
    """
    successor = note("tnd-dddd4444", "finding", "Backups now run on the host")
    seeded = seed({MAIN: [FINDING, successor]}, supersedes=("tnd-dddd4444", "tnd-cccc3333"))
    truncated = seeded.file(MAIN, FINDING["file_path"])
    truncated.write_bytes(truncated.read_bytes().rstrip(b"\n"))

    result = runner.invoke(app, ["show", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    # The payload is still byte-exact: the newline is printed after it, not into it.
    assert result.stdout.startswith(truncated.read_text(encoding="utf-8"))
    lines = result.stdout.splitlines()
    # The notice is its own line, and the body's last line ends where the body does.
    assert "superseded by tnd-dddd4444" in lines
    assert "The container is the thing being backed up." in lines


def test_show_on_an_unknown_id_exits_one_with_one_stderr_line() -> None:
    """Contract rule 6: one line, on stderr, nothing on stdout."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["show", "tnd-zzzz9999", "--project", MAIN])

    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: no record 'tnd-zzzz9999' in scope"
    assert result.stdout.strip() == ""


def test_show_refuses_a_record_that_only_matches_by_title() -> None:
    """The identity rule (T9/T10): a title match is not-found, never a near-match."""
    impostor = {
        "metadata": {"type": "guide", "title": "tnd-eeee5555"},
        "file_path": "guides/impostor.md",
        "title": "tnd-eeee5555",
        "permalink": "guides/impostor",
        "content": "not the record you asked for\n",
    }
    seed({MAIN: [impostor]})

    result = runner.invoke(app, ["show", "tnd-eeee5555", "--project", MAIN])

    assert result.exit_code == 1
    assert "no record 'tnd-eeee5555' in scope" in result.stderr
    assert "not the record you asked for" not in result.stdout


def test_show_finds_the_record_whose_permalink_is_the_id() -> None:
    """Positive control for the rule above: a real id still resolves."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["show", "tnd-aaaa1111", "--project", MAIN, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "Move backups off-container" in result.stdout


def test_show_names_the_missing_file_when_a_record_was_never_materialized() -> None:
    """A row with no file is T12's shape; an empty payload would report it as an empty note."""
    seeded = seed(BASIC_CORPUS)
    seeded.file(MAIN, TASK["file_path"]).unlink()

    result = runner.invoke(app, ["show", "tnd-aaaa1111", "--project", MAIN])

    assert result.exit_code == 1
    assert "indexed but its file is missing" in result.stderr
    assert result.stdout.strip() == ""


# --- bm path ---


def test_path_prints_one_absolute_path_and_nothing_else() -> None:
    """D9: the output is consumed by `$EDITOR "$(bm path tnd-x)"`."""
    seeded = seed(BASIC_CORPUS)

    result = runner.invoke(app, ["path", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    printed = result.stdout.strip().splitlines()
    assert printed == [str(seeded.file(MAIN, FINDING["file_path"]))]
    assert Path(printed[0]).is_absolute()
    assert "records" not in result.stdout


def test_path_on_an_unknown_id_exits_one() -> None:
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["path", "tnd-zzzz9999", "--project", MAIN])

    assert result.exit_code == 1
    assert result.stderr.strip() == "Error: no record 'tnd-zzzz9999' in scope"
    assert result.stdout.strip() == ""


def test_path_warns_on_stderr_when_the_file_is_gone() -> None:
    """GAPS U10: still exit 0 with the path — you want it in order to restore the file.

    `bm show` calls the same state an error and exits 1. `bm path` cannot: it
    exists to be substituted into another command, and the path is the useful
    answer even when nothing is behind it. The warning goes to stderr, which a
    command substitution does not capture.
    """
    seeded = seed(BASIC_CORPUS)
    seeded.file(MAIN, FINDING["file_path"]).unlink()

    result = runner.invoke(app, ["path", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(seeded.file(MAIN, FINDING["file_path"]))
    assert "tnd-cccc3333 is indexed but its file is missing" in result.stderr
    assert "bm reindex -p main" in result.stderr


def test_path_says_nothing_on_stderr_when_the_file_is_there() -> None:
    """Control for the warning above: a healthy record must stay silent."""
    seed(BASIC_CORPUS)

    result = runner.invoke(app, ["path", "tnd-cccc3333", "--project", MAIN])

    assert result.exit_code == 0, result.output
    assert result.stderr.strip() == ""


def test_an_unscoped_lookup_finds_a_record_in_any_project() -> None:
    """Reads roll up by default; `bm show <id>` should not need `cd` first."""
    seed({MAIN: [TASK], BETA: [FINDING]})

    result = runner.invoke(app, ["path", "tnd-cccc3333"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip().endswith(FINDING["file_path"])


# --- one id, two projects ---


def test_an_id_in_two_projects_is_an_error_that_names_both() -> None:
    """An unscoped lookup that resolves twice must refuse, not pick one.

    Ids are per-project, so the same permalink can legitimately exist in two
    projects. Printing either one would send `bm show` and `$EDITOR "$(bm path
    …)"` at a file the caller did not ask for (contract rule 6: one stderr line).
    """
    seed({MAIN: [TASK], BETA: [TASK]})

    for verb in ("show", "path"):
        result = runner.invoke(app, [verb, "tnd-aaaa1111"])

        assert result.exit_code == 1, result.output
        assert result.stderr.strip() == (
            f"Error: 'tnd-aaaa1111' is in more than one project ({BETA}, {MAIN}) — name one with -p"
        )
        assert result.stdout.strip() == ""


def test_naming_the_project_resolves_the_same_duplicated_id() -> None:
    """Positive control for the refusal above: `-p` is the fix the error names."""
    seeded = seed({MAIN: [TASK], BETA: [TASK]})

    result = runner.invoke(app, ["path", "tnd-aaaa1111", "--project", BETA])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(seeded.file(BETA, TASK["file_path"]))
