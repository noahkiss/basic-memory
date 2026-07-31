"""Regression tests for doctor CLI failure output (#1027).

str() of a message-less exception (e.g. httpx.ReadTimeout, bare RuntimeError)
is empty, which used to leave users with a blank "Doctor failed:" line.
"""

from typing import Callable, NoReturn

from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.doctor as doctor_cmd

runner = CliRunner()


def _raise(exc: Exception) -> Callable[[], NoReturn]:
    def raiser() -> NoReturn:
        raise exc

    return raiser


def test_doctor_failure_prints_error_message(monkeypatch):
    """Exceptions with a message keep printing that message."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError("doctor project missing")))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: doctor project missing" in result.output


def test_doctor_failure_message_never_blank(monkeypatch):
    """A message-less expected error falls back to the repr instead of blank output."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(ValueError()))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: ValueError()" in result.output


def test_doctor_unexpected_failure_message_never_blank(monkeypatch):
    """A message-less unexpected error (generic handler) also shows its repr on stderr."""
    monkeypatch.setattr(doctor_cmd, "run_doctor", _raise(RuntimeError()))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "Doctor failed: RuntimeError()" in result.stderr


def test_doctor_project_report_lists_unresolved_relations(monkeypatch):
    """--project prints each dangling edge with its age proxy and exits 0 (report, not failure)."""
    from datetime import UTC, datetime

    import basic_memory.cli.direct as direct
    from basic_memory.repository.relation_repository import UnresolvedRelationReportRow

    rows = [
        UnresolvedRelationReportRow(
            file_path="notes/a.md",
            relation_type="supersedes",
            to_name="Ghost Note",
            source_updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    ]

    async def fake_report(project_name: str):
        assert project_name == "alpha"
        return rows

    monkeypatch.setattr(direct, "direct_unresolved_relation_report", fake_report)

    result = runner.invoke(app, ["doctor", "--project", "alpha"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "1 unresolved relation(s) in 'alpha'" in result.output
    assert "2026-01-01  notes/a.md  -supersedes-> [[Ghost Note]]" in result.output


def test_doctor_project_report_clean_corpus(monkeypatch):
    """A project with no dangling edges reports OK."""
    import basic_memory.cli.direct as direct

    async def fake_report(project_name: str):
        return []

    monkeypatch.setattr(direct, "direct_unresolved_relation_report", fake_report)

    result = runner.invoke(app, ["doctor", "--project", "alpha"])

    assert result.exit_code == 0
    assert "no unresolved relations" in result.output


def test_doctor_project_report_unknown_project_fails(monkeypatch):
    """An unknown project name must fail loudly, not read as a clean corpus."""
    import basic_memory.cli.direct as direct

    async def fake_report(project_name: str):
        raise ValueError(f"Project not found: '{project_name}'")

    monkeypatch.setattr(direct, "direct_unresolved_relation_report", fake_report)

    result = runner.invoke(app, ["doctor", "--project", "nope"])

    assert result.exit_code == 1
    assert "Doctor failed: Project not found: 'nope'" in result.output
