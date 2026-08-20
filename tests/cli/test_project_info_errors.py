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
