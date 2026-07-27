"""Tests for `bm project add`."""

import json
from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectStatusResponse

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_config(tmp_path, monkeypatch):
    """Point ConfigManager at a fresh config.json under a temporary HOME."""
    # Invalidate config cache to ensure clean state for each test
    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    config_dir = tmp_path / ".basic-memory"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"

    config_data = {
        "env": "dev",
        "projects": {},
        "default_project": "main",
    }

    config_file.write_text(json.dumps(config_data, indent=2))

    # Set HOME to tmp_path so ConfigManager uses our test config
    monkeypatch.setenv("HOME", str(tmp_path))

    yield config_file


def test_project_add_requires_a_path(runner, mock_config):
    """The path is a required positional; omitting it is a usage error."""
    result = runner.invoke(app, ["project", "add", "test-project"])

    assert result.exit_code == 2


def test_project_add_sends_resolved_absolute_path(runner, mock_config, monkeypatch, tmp_path):
    """A ~-prefixed path is expanded and absolutized before it reaches the API."""
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        calls.append(project_data)
        return ProjectStatusResponse.model_validate(
            {
                "message": "Project 'test-project' added successfully",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": "12345678-1234-1234-1234-123456789012",
                    "name": "test-project",
                    "path": str(tmp_path / "test-project"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "test-project", "~/test-project"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, Stdout: {result.stdout}"
    assert "Project 'test-project' added successfully" in result.stdout
    # HOME is tmp_path (mock_config), so ~ expands under it.
    assert calls == [
        {
            "name": "test-project",
            "path": (tmp_path / "test-project").as_posix(),
            "set_default": False,
        }
    ]


def test_project_add_announces_when_the_default_moves(runner, mock_config, monkeypatch, tmp_path):
    """A default change must be visible in the add output (T5).

    ``project add`` printed only the API's "added successfully" message, so a move
    of the default — which the service performs on its own for the first project
    and when repairing a dangling configured default — was invisible until a later
    unqualified command targeted the wrong project.
    """
    project_dir = tmp_path / "q3test"
    project_dir.mkdir()

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        return ProjectStatusResponse.model_validate(
            {
                "message": "Project 'q3test' added successfully",
                "status": "success",
                "default": True,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": "12345678-1234-1234-1234-123456789012",
                    "name": "q3test",
                    "path": str(project_dir),
                    "is_default": True,
                },
            }
        )

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "q3test", str(project_dir)])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, Stdout: {result.stdout}"
    assert "default project" in result.stdout
