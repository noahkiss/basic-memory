"""Tests for `bm doctor` — the grouped corpus report (GAPS W2, W5 item 5).

The report is rendered from what `direct_doctor_report` returns, so these stub
that one call and assert on the lines. The queries behind it have their own
tests in `tests/repository/test_entity_repository_hygiene.py`, and the import
guard runs the whole command in a subprocess against a real database.

The self-test tests still cover #1027: str() of a message-less exception is
empty, which used to leave users with a blank "Doctor failed:" line.
"""

from datetime import UTC, datetime
from typing import Callable, NoReturn

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.doctor as doctor_cmd
from basic_memory.cli.app import app
from basic_memory.cli.direct import (
    MissingFile,
    ProjectDoctorReport,
    ProjectHygieneReport,
    ProjectIntegrityReport,
)
from basic_memory.repository.entity_repository import HygieneRecord, PermalinkIntegrityIssue
from basic_memory.repository.relation_repository import UnresolvedRelationReportRow
from basic_memory.repository.violation_repository import ViolationRow

runner = CliRunner()


def _raise(exc: Exception) -> Callable[[], NoReturn]:
    def raiser() -> NoReturn:
        raise exc

    return raiser


def violation(rule: str, field: str, severity: str) -> ViolationRow:
    return ViolationRow(
        file_path="notes/broken.md",
        rule=rule,
        field=field,
        message=f"{rule} on '{field}'",
        severity=severity,
        detected_at=datetime(2026, 8, 16, tzinfo=UTC),
    )


FULL_REPORT = ProjectDoctorReport(
    project_name="alpha",
    integrity=ProjectIntegrityReport(
        unresolved=[
            UnresolvedRelationReportRow(
                file_path="notes/a.md",
                relation_type="supersedes",
                to_name="Ghost Note",
                source_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
        permalink_issues=[
            PermalinkIntegrityIssue(
                file_path="notes/b.md",
                issue="drift",
                permalink="notes/b",
                frontmatter_permalink="notes/b-edited",
            ),
            PermalinkIntegrityIssue(
                file_path="notes/c.md",
                issue="underscore",
                permalink="tnd_c",
                frontmatter_permalink="tnd_c",
            ),
        ],
        errors=[violation("missing-required-field", "source", "error")],
    ),
    hygiene=ProjectHygieneReport(
        review_due=[HygieneRecord("notes/d.md", "notes/d", "2026-01-31")],
        inferred_dates=[HygieneRecord("notes/e.md", "notes/e", "2026-03-01")],
        stale_states=[HygieneRecord("notes/f.md", "notes/f", "2026-01-05")],
        inbox=[
            HygieneRecord("notes/g.md", "notes/g", "runbook"),
            HygieneRecord("notes/h.md", "notes/h", ""),
        ],
        advisories=[violation("unknown-key", "owner", "advisory")],
    ),
)


# A corpus whose only problems need a decision rather than a repair. This is the
# case the verdict has to distinguish: it prints rows and still exits 0 (GAPS U19).
HYGIENE_ONLY_REPORT = ProjectDoctorReport(project_name="alpha", hygiene=FULL_REPORT.hygiene)


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run every test from a directory with no `.bm.yml` above it.

    Scope depends on cwd, so a marker anywhere above the checkout would silently
    turn the unscoped cases into pinned ones. The marker tests write their own
    marker into this same directory.
    """
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def stub_report(monkeypatch):
    """Answer the report call without a database, recording how it was called."""
    calls: list[dict] = []

    def _stub(reports: list[ProjectDoctorReport]):
        async def fake_report(project_names, *, include_integrity=True, include_hygiene=True):
            calls.append(
                {
                    "project_names": project_names,
                    "include_integrity": include_integrity,
                    "include_hygiene": include_hygiene,
                }
            )
            return reports

        monkeypatch.setattr(doctor_cmd, "direct_doctor_report", fake_report)
        return calls

    return _stub


# --- The report ---


def test_doctor_integrity_section_lists_every_check(stub_report):
    """Dangling edges, permalink invariants, and error violations share one section."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "integrity", "--quiet"])

    # Integrity found something, so the verdict is 1 (GAPS U19). The rows are
    # still on stdout — the exit code says there is a problem, it does not
    # withhold what the problem is.
    assert result.exit_code == 1, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "integrity  project 'alpha'"
    assert "notes/a.md  unresolved-relation  -supersedes-> [[Ghost Note]]" in lines[1]
    assert "source touched 2026-01-01" in lines[1]
    assert lines[2] == (
        "  notes/b.md  permalink-drift  permalink=notes/b  frontmatter=notes/b-edited"
    )
    assert lines[3] == "  notes/c.md  permalink-underscore  permalink=tnd_c"
    assert lines[4] == (
        "  notes/broken.md  missing-required-field  source  missing-required-field on 'source'"
    )
    # Contract rule 3: the count closes the listing.
    assert lines[5] == "  4 issues"
    assert "hygiene" not in result.stdout


def test_doctor_hygiene_section_lists_every_check(stub_report):
    """Expired reviews, guessed dates, stale state, the inbox pile, and advisories."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "hygiene", "--quiet"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "hygiene  project 'alpha'"
    assert lines[1] == "  notes/d.md  review-due  review-by 2026-01-31"
    assert lines[2] == "  notes/e.md  date-inferred  date 2026-03-01"
    assert lines[3] == (
        "  notes/f.md  stale-state  unchanged for over 30 days, last changed 2026-01-05"
    )
    assert lines[4] == "  notes/g.md  inbox  proposes 'runbook'"
    assert lines[5] == "  notes/h.md  inbox  unfiled — file it with 'bm new <type>' or leave it"
    assert lines[6] == "  notes/broken.md  unknown-key  owner  unknown-key on 'owner'"
    assert lines[7] == "  6 issues"
    assert "integrity" not in result.stdout


def test_doctor_asks_a_plain_inbox_record_for_something_it_can_do(stub_report):
    """GAPS U5: a deliberate inbox record was asked for a proposal no verb can attach.

    A proposal only ever arrives as a side effect of `bm new <undeclared-type>`, so
    "proposes no type" made doctor's hygiene count unclosable for a corpus that
    used the escape hatch as documented. The row stays — the W5-B notice counts
    the record as unfiled and points here — and the demand becomes satisfiable.

    The positive control is the row above it: a record that *does* carry a
    proposal still reports the type it proposes.
    """
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "hygiene", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "proposes no type" not in result.stdout
    assert "notes/h.md  inbox  unfiled — file it with 'bm new <type>' or leave it" in result.stdout
    assert "notes/g.md  inbox  proposes 'runbook'" in result.stdout


def test_doctor_prints_both_groups_in_order(stub_report):
    """With no --only, integrity comes first and hygiene second."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 1, result.output
    headings = [line for line in result.stdout.splitlines() if line and not line.startswith(" ")]
    assert headings == ["integrity  project 'alpha'", "hygiene  project 'alpha'"]


def test_doctor_clean_corpus_says_so_and_exits_zero(stub_report):
    """An empty result is a result, not a failure (contract rule 5)."""
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.count("No issues") == 2
    assert "issues" not in result.stdout.replace("No issues", "")


def test_doctor_only_integrity_does_not_run_the_hygiene_queries(stub_report):
    """--only is a narrower question, not a filter over work already done."""
    calls = stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "integrity", "--quiet"])

    assert result.exit_code == 1, result.output
    assert calls == [{"project_names": None, "include_integrity": True, "include_hygiene": False}]


def test_doctor_only_hygiene_does_not_run_the_integrity_queries(stub_report):
    """The same, in the other direction."""
    calls = stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "hygiene", "--quiet"])

    assert result.exit_code == 0, result.output
    assert calls == [{"project_names": None, "include_integrity": False, "include_hygiene": True}]


def test_doctor_unknown_only_value_is_an_addressing_failure(stub_report):
    """A group nobody can scope to fails on stderr with exit 1, printing no payload."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "nonsense"])

    assert result.exit_code == 1
    assert "--only must be one of integrity, hygiene" in result.stderr
    assert result.stdout == ""


def test_doctor_unscoped_names_the_project_in_every_section(stub_report):
    """A roll-up must say which project each section is about (GAPS W5-C)."""
    calls = stub_report(
        [
            ProjectDoctorReport(project_name="alpha"),
            ProjectDoctorReport(project_name="beta"),
        ]
    )

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert calls[0]["project_names"] is None
    headings = [line for line in result.stdout.splitlines() if line and not line.startswith(" ")]
    assert headings == [
        "integrity  project 'alpha'",
        "hygiene  project 'alpha'",
        "integrity  project 'beta'",
        "hygiene  project 'beta'",
    ]


def test_doctor_project_flag_pins_one_project(stub_report):
    """--project overrides every other scope rule."""
    calls = stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor", "--project", "alpha", "--quiet"])

    assert result.exit_code == 0, result.output
    assert calls[0]["project_names"] == ("alpha",)


def test_doctor_marker_pins_its_project(stub_report, tmp_path, monkeypatch, write_registry_file):
    """A `.bm.yml` above the working directory scopes the report to its project.

    The registry file is real because `resolve_read_scope` checks that the name
    a marker carries is registered — an unregistered marker raises rather than
    widening to every project (GAPS W5-C).
    """
    write_registry_file({"marked": str(tmp_path)}, default="marked")
    (tmp_path / ".bm.yml").write_text("project: marked\n", encoding="utf-8")
    working = tmp_path / "src" / "deep"
    working.mkdir(parents=True)
    monkeypatch.chdir(working)
    calls = stub_report([ProjectDoctorReport(project_name="marked")])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert calls[0]["project_names"] == ("marked",)


def test_doctor_unreadable_marker_fails_loudly(stub_report, tmp_path, monkeypatch):
    """A marker that exists but cannot be used is an addressing failure, not a roll-up."""
    (tmp_path / ".bm.yml").write_text("project: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "'project' must be a non-empty string" in result.stderr
    assert result.stdout == ""


def test_doctor_unknown_project_fails_loudly(monkeypatch):
    """An unknown project name must fail, never read as a clean corpus."""

    async def fake_report(project_names, **kwargs):
        raise ValueError(f"Project not found: '{project_names[0]}'")

    monkeypatch.setattr(doctor_cmd, "direct_doctor_report", fake_report)

    result = runner.invoke(app, ["doctor", "--project", "nope"])

    assert result.exit_code == 1
    assert "Error: Project not found: 'nope'" in result.stderr


def test_doctor_reports_a_record_whose_file_is_gone(stub_report):
    """A row with no file behind it is an integrity issue, named with its repair (GAPS U10)."""
    stub_report(
        [
            ProjectDoctorReport(
                project_name="scratchpilot",
                integrity=ProjectIntegrityReport(
                    missing_files=[
                        MissingFile(
                            file_path="findings/tnd-pdem7knd--delete-me-a.md",
                            permalink="tnd-pdem7knd",
                        ),
                        MissingFile(file_path="findings/orphan.md", permalink=None),
                    ]
                ),
            )
        ]
    )

    result = runner.invoke(app, ["doctor", "--only", "integrity", "--quiet"])

    assert result.exit_code == 1, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[1] == (
        "  findings/tnd-pdem7knd--delete-me-a.md  missing-file  permalink=tnd-pdem7knd"
    )
    # A row with no permalink still has to print, and its columns must not shift.
    assert lines[2] == "  findings/orphan.md  missing-file  permalink=-"
    # One repair line for the group, naming the project the reindex has to be
    # pointed at — nothing pointed at the repair before.
    assert lines[3] == "  repair: bm reindex -p 'scratchpilot'"
    assert lines[4] == "  2 issues"


def test_doctor_names_no_repair_when_no_file_is_missing(stub_report):
    """The repair line rides on the rows, so a clean corpus must not print it."""
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor", "--only", "integrity", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "repair:" not in result.stdout
    assert "missing-file" not in result.stdout


def test_doctor_empty_registry_is_a_result(stub_report):
    """No projects at all is something to say, not something to fail on."""
    stub_report([])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No projects to check."


# --- The verdict (GAPS U19) ---
#
# W2 made doctor the gate, and a gate that always exits 0 gates nothing. The
# split is integrity vs hygiene: integrity problems have right answers, so they
# fail the run; hygiene problems need a decision and an unfiled inbox record is a
# legitimate resting state (GAPS U5), so they do not. `--strict` fails on either.


def test_doctor_exits_one_when_integrity_found_something(stub_report):
    """Integrity issues fail the run, so a hook or a `just` recipe can gate on it."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 1, result.output


def test_doctor_exits_zero_on_hygiene_issues_alone(stub_report):
    """Advisory rows print and the run still passes."""
    stub_report([HYGIENE_ONLY_REPORT])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    # Positive control: the rows really are there, so exit 0 is a judgment about
    # them rather than a report that found nothing.
    assert "  6 issues" in result.stdout


def test_doctor_strict_exits_one_on_hygiene_alone(stub_report):
    """--strict is for the caller who wants every issue to fail the run."""
    stub_report([HYGIENE_ONLY_REPORT])

    result = runner.invoke(app, ["doctor", "--strict", "--quiet"])

    assert result.exit_code == 1, result.output


def test_doctor_strict_on_a_clean_corpus_exits_zero(stub_report):
    """--strict raises the bar; it does not fail a corpus with nothing wrong."""
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor", "--strict", "--quiet"])

    assert result.exit_code == 0, result.output


def test_doctor_only_hygiene_never_fails_on_integrity_it_did_not_query(stub_report):
    """--only narrows the question, so it narrows the verdict with it.

    Under `--only hygiene` the integrity queries never ran, so the stub's
    integrity rows describe a corpus this invocation did not look at. A verdict
    about them would be a claim nobody checked.
    """
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--only", "hygiene", "--quiet"])

    assert result.exit_code == 0, result.output


def test_doctor_empty_registry_exits_zero_under_strict(stub_report):
    """No projects is still a result, however strict the caller asked to be."""
    stub_report([])

    result = runner.invoke(app, ["doctor", "--strict", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No projects to check."


def test_doctor_prints_the_whole_report_before_exiting_one(stub_report):
    """Exit 1 is a verdict on the payload, not a substitute for it.

    Contract rule 6 normally keeps stdout empty on the error path; this is the
    partial-corpus shape instead (rule 6's clause, GAPS O10) — the command did
    its job, and the exit code is what says the corpus failed.
    """
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "integrity  project 'alpha'"
    assert "  4 issues" in result.stdout
    # The hints still close the report, after the payload (contract rule 4).
    assert lines[-3] == "next:"


def test_doctor_self_test_rejects_strict(stub_report):
    """--strict grades a corpus; the self-test checks the install and already exits 1."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--self-test", "--strict"])

    assert result.exit_code == 1
    assert "--self-test takes no --strict" in result.stderr
    assert result.stdout == ""


# --- Affordances (GAPS W19 item 5) ---


def test_doctor_ends_with_the_next_step_hints(stub_report):
    """A static next-verb list closes the report, after the payload."""
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[-3] == "next:"
    assert lines[-2].startswith("  bm types")
    assert lines[-1].startswith("  bm doctor --only hygiene")


def test_doctor_quiet_drops_the_hints(stub_report):
    """--quiet leaves the payload alone (contract rule 7)."""
    stub_report([ProjectDoctorReport(project_name="alpha")])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "next:" not in result.stdout


# --- The self-test ---


def test_doctor_self_test_failure_prints_error_message(monkeypatch):
    """Exceptions with a message keep printing that message."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError("doctor project missing")))

    result = runner.invoke(app, ["doctor", "--self-test"])

    assert result.exit_code == 1
    assert "Doctor failed: doctor project missing" in result.output


def test_doctor_self_test_failure_message_never_blank(monkeypatch):
    """A message-less expected error falls back to the repr instead of blank output."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError()))

    result = runner.invoke(app, ["doctor", "--self-test"])

    assert result.exit_code == 1
    assert "Doctor failed: ValueError()" in result.output


def test_doctor_self_test_unexpected_failure_message_never_blank(monkeypatch):
    """A message-less unexpected error (generic handler) also shows its repr on stderr."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(RuntimeError()))

    result = runner.invoke(app, ["doctor", "--self-test"])

    assert result.exit_code == 1
    assert "Doctor failed: RuntimeError()" in result.stderr


def test_doctor_self_test_rejects_a_group(stub_report):
    """--only asks about a corpus; the self-test checks the install and has no groups."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--self-test", "--only", "hygiene"])

    assert result.exit_code == 1
    assert "--self-test takes no --only group" in result.stderr
    assert result.stdout == ""
