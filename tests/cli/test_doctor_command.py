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

    assert result.exit_code == 0, result.output
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
    assert lines[5] == "  notes/h.md  inbox  proposes no type"
    assert lines[6] == "  notes/broken.md  unknown-key  owner  unknown-key on 'owner'"
    assert lines[7] == "  6 issues"
    assert "integrity" not in result.stdout


def test_doctor_prints_both_groups_in_order(stub_report):
    """With no --only, integrity comes first and hygiene second."""
    stub_report([FULL_REPORT])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
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

    assert result.exit_code == 0, result.output
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


def test_doctor_marker_pins_its_project(stub_report, tmp_path, monkeypatch):
    """A `.bm.yml` above the working directory scopes the report to its project."""
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


def test_doctor_empty_registry_is_a_result(stub_report):
    """No projects at all is something to say, not something to fail on."""
    stub_report([])

    result = runner.invoke(app, ["doctor", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "No projects to check."


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
