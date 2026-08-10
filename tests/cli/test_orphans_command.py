"""Tests for the 'basic-memory orphans' CLI command."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.server.fastmcp.exceptions import ToolError
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.schemas.v2.graph import GraphNode

import basic_memory.cli.commands.orphans as orphans_cmd  # noqa: F401

runner = CliRunner()

_MOCK_PROJECT_ITEM = MagicMock()
_MOCK_PROJECT_ITEM.name = "test-project"
_MOCK_PROJECT_ITEM.external_id = "11111111-1111-1111-1111-111111111111"

_ORPHAN_ENTITIES = [
    GraphNode(
        external_id="aaaa-1111",
        title="Isolated Note",
        file_path="notes/isolated.md",
        note_type="note",
    ),
    GraphNode(
        external_id="bbbb-2222",
        title="Dangling Spec",
        file_path="specs/dangling.md",
        note_type="spec",
    ),
]


@asynccontextmanager
async def _fake_get_client(project_name=None):
    yield MagicMock()


@patch("basic_memory.cli.commands.orphans.resolve_cli_project")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_lists_title_path_and_type(
    mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls
):
    """One line per orphan — title first, then file path, then note type."""
    mock_config_cls.return_value = "test-project"
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == [
        "Isolated Note  notes/isolated.md  note",
        "Dangling Spec  specs/dangling.md  spec",
        "2 orphans",
    ]
    mock_get_client.assert_called_once_with()
    # No --project was passed, so the command must resolve the configured default.
    assert mock_get_active.await_args.args[1] == "test-project"


@patch("basic_memory.cli.commands.orphans.resolve_cli_project")
@patch("basic_memory.cli.commands.orphans.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.cli.commands.orphans.get_client")
@patch("basic_memory.cli.commands.orphans.KnowledgeClient")
def test_orphans_no_results(mock_knowledge_cls, mock_get_client, mock_get_active, mock_config_cls):
    """A graph with no orphans is a result — '0 orphans', exit 0."""
    mock_config_cls.return_value = "test-project"
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = []
    mock_knowledge_cls.return_value = mock_knowledge

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == ["0 orphans"]


@patch("basic_memory.cli.commands.orphans.run_orphans", new_callable=AsyncMock)
def test_orphans_value_error(mock_run_orphans):
    """User-facing command errors go to stderr, leaving stdout empty."""
    mock_run_orphans.side_effect = ValueError("project not found")

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: project not found" in result.stderr


@patch("basic_memory.cli.commands.orphans.run_orphans", new_callable=AsyncMock)
def test_orphans_tool_error(mock_run_orphans):
    """A ToolError from the client layer takes the same stderr path."""
    mock_run_orphans.side_effect = ToolError("orphan lookup failed")

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: orphan lookup failed" in result.stderr
