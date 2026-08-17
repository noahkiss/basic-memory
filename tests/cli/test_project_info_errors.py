"""Regression tests for project info error handling."""

import typer
from typer.testing import CliRunner

from basic_memory.cli.app import app
import basic_memory.cli.commands.command_utils as command_utils
import basic_memory.cli.commands.project  # noqa: F401  # registers the project subcommands

runner = CliRunner()


def test_project_info_does_not_print_wrapper_exit_code(monkeypatch):
    """project info should not append a secondary 'Error getting project info: 1' line."""

    async def fake_get_project_info(_project_name: str):
        raise typer.Exit(1)

    # Patched on `command_utils`, not on the project module: `project info`
    # imports the helper at call time to keep the MCP client graph off CLI
    # startup (GAPS.md T30), so the command module holds no such attribute.
    monkeypatch.setattr(command_utils, "get_project_info", fake_get_project_info)

    result = runner.invoke(app, ["project", "info", "demo"])

    assert result.exit_code == 1
    assert "Error getting project info" not in result.output
