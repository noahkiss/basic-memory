"""Regression tests for project info error handling."""

import typer
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.project as project_commands

runner = CliRunner()


def test_project_info_does_not_print_wrapper_exit_code(monkeypatch):
    """project info should not append a secondary 'Error getting project info: 1' line."""

    async def fake_fetch_project_info(_project_name: str):
        raise typer.Exit(1)

    monkeypatch.setattr(project_commands, "fetch_project_info", fake_fetch_project_info)

    result = runner.invoke(app, ["project", "info", "demo"])

    assert result.exit_code == 1
    assert "Error getting project info" not in result.output


def test_project_info_unknown_project_is_one_error_line(monkeypatch):
    """An unknown name fails with the service's message, not a traceback."""

    async def fake_fetch_project_info(_project_name: str):
        raise ValueError("Project 'demo' not found in database")

    monkeypatch.setattr(project_commands, "fetch_project_info", fake_fetch_project_info)

    result = runner.invoke(app, ["project", "info", "demo"])

    assert result.exit_code == 1
    assert "Error getting project info: Project 'demo' not found in database" in result.output


def _info_payload(project_repo):
    """The attribute surface `display_project_info` actually reads (GAPS U36)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        project_name="demo",
        project_path="/tmp/demo",
        project_repo=project_repo,
        default_project="demo",
        statistics=SimpleNamespace(
            total_entities=0,
            total_observations=0,
            total_relations=0,
            total_unresolved_relations=0,
            isolated_entities=0,
            note_types={},
        ),
        embedding_status=None,
        system=SimpleNamespace(
            version="test", database_path="/tmp/db", database_size="0 B", timestamp="now"
        ),
    )


def test_project_info_shows_the_repo_when_recorded(monkeypatch):
    async def fake_fetch_project_info(_project_name: str):
        return _info_payload("https://example.com/owner/demo")

    monkeypatch.setattr(project_commands, "fetch_project_info", fake_fetch_project_info)

    result = runner.invoke(app, ["project", "info", "demo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "repo: https://example.com/owner/demo" in result.stdout


def test_project_info_omits_the_repo_line_when_never_captured(monkeypatch):
    """`repo: None` would read as a value; the common historical state prints nothing."""

    async def fake_fetch_project_info(_project_name: str):
        return _info_payload(None)

    monkeypatch.setattr(project_commands, "fetch_project_info", fake_fetch_project_info)

    result = runner.invoke(app, ["project", "info", "demo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "repo:" not in result.stdout
