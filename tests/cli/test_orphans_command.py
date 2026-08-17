"""Tests for `bm orphans` — the notes nothing links to (GAPS W5-C).

Scope is the behaviour under test alongside the rendering: `--project` pins,
an unmarked directory reports every project, and a pinned run's block is
byte-identical to what the command always printed.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from typer.testing import CliRunner

import basic_memory.cli.commands.orphans as orphans_cmd
from basic_memory.cli.main import app as cli_app
from basic_memory.schemas.v2.graph import GraphNode

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


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run from a directory with no `.bm.yml` above it, so scope is unscoped."""
    monkeypatch.chdir(tmp_path)


@asynccontextmanager
async def _fake_get_client(project_name=None):
    yield MagicMock()


@pytest.fixture
def stub_run(monkeypatch):
    """Answer the fetch without a client, recording the projects asked for."""

    def _stub(reports):
        seen: list = []

        async def fake_run_orphans(projects=None):
            seen.append(projects)
            return reports

        monkeypatch.setattr(orphans_cmd, "run_orphans", fake_run_orphans)
        return seen

    return _stub


def test_pinned_orphans_list_title_path_and_type(stub_run):
    """One line per orphan — title first, then file path, then note type."""
    seen = stub_run([("test-project", _ORPHAN_ENTITIES)])

    result = runner.invoke(cli_app, ["orphans", "--project", "test-project"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    # A pinned run prints exactly what it always did: no project label.
    assert result.stdout.splitlines() == [
        "Isolated Note  notes/isolated.md  note",
        "Dangling Spec  specs/dangling.md  spec",
        "2 orphans",
    ]
    assert seen == [["test-project"]]


def test_unscoped_orphans_report_every_project(stub_run):
    """No --project and no marker rolls up, one labelled section per project."""
    seen = stub_run(
        [
            ("alpha", _ORPHAN_ENTITIES[:1]),
            ("beta", []),
        ]
    )

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == [
        "project: alpha",
        "Isolated Note  notes/isolated.md  note",
        "1 orphans",
        "",
        "project: beta",
        "0 orphans",
    ]
    # Unscoped means every project, never the registry default.
    assert seen == [None]


def test_orphans_no_results(stub_run):
    """A graph with no orphans is a result — '0 orphans', exit 0."""
    stub_run([("test-project", [])])

    result = runner.invoke(cli_app, ["orphans", "--project", "test-project"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == ["0 orphans"]


def test_orphans_empty_registry_says_so(stub_run):
    """An empty registry is a result, and silence would read as a clean graph."""
    stub_run([])

    result = runner.invoke(cli_app, ["orphans"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == ["no projects registered"]


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


@pytest.mark.asyncio
@patch("basic_memory.mcp.project_context.get_active_project", new_callable=AsyncMock)
@patch("basic_memory.mcp.async_client.get_client")
@patch("basic_memory.mcp.clients.knowledge.KnowledgeClient")
async def test_run_orphans_pins_to_the_named_project(
    mock_knowledge_cls, mock_get_client, mock_get_active
):
    """The fetch resolves the project it was given, not the configured default."""
    mock_get_active.return_value = _MOCK_PROJECT_ITEM
    mock_get_client.side_effect = _fake_get_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = _ORPHAN_ENTITIES
    mock_knowledge_cls.return_value = mock_knowledge

    reports = await orphans_cmd.run_orphans(["test-project"])

    assert reports == [("test-project", _ORPHAN_ENTITIES)]
    assert mock_get_active.await_args.args[1] == "test-project"


@pytest.mark.asyncio
@patch("basic_memory.mcp.clients.ProjectClient")
@patch("basic_memory.mcp.async_client.get_client")
@patch("basic_memory.mcp.clients.knowledge.KnowledgeClient")
async def test_run_orphans_unscoped_asks_the_registry_for_every_project(
    mock_knowledge_cls, mock_get_client, mock_project_cls
):
    """Positive control on the roll-up: `None` enumerates, it does not resolve one."""
    mock_get_client.side_effect = _fake_get_client
    listed = MagicMock()
    listed.projects = [_MOCK_PROJECT_ITEM]
    mock_project_client = AsyncMock()
    mock_project_client.list_projects.return_value = listed
    mock_project_cls.return_value = mock_project_client
    mock_knowledge = AsyncMock()
    mock_knowledge.get_orphans.return_value = []
    mock_knowledge_cls.return_value = mock_knowledge

    reports = await orphans_cmd.run_orphans(None)

    assert reports == [("test-project", [])]
    mock_project_client.list_projects.assert_awaited_once()
