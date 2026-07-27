"""Tests for project list display and project ls behavior."""

import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectList

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict) -> Path:
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


@pytest.fixture
def mock_client(monkeypatch):
    """Mock get_client with a no-op async context manager."""

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    monkeypatch.setattr(project_cmd, "get_client", fake_get_client)


def test_project_list_shows_indexed_project(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """The table and the JSON rows both describe the indexed project."""
    alpha_path = (tmp_path / "alpha-local").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": alpha_path}},
            "default_project": "alpha",
        }
    )

    async def fake_list_projects(self):
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": "alpha",
                        "path": alpha_path,
                        "is_default": True,
                    }
                ],
                "default_project": "alpha",
            }
        )

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Name" in result.stdout
    assert "Path" in result.stdout
    assert "Default" in result.stdout
    assert "alpha-local" in result.stdout
    # A fully indexed project must not be reported as drifting.
    assert "not indexed" not in result.stdout

    json_result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})

    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == {
        "projects": [
            {
                "name": "alpha",
                "permalink": "alpha",
                "path": project_cmd.format_path(alpha_path),
                "is_default": True,
            }
        ]
    }


def test_project_list_falls_back_to_configured_project(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """A project in config that the index does not return must still render (#1003).

    Regression: ``bm project list`` seeded rows only from live query results, so the
    table came up empty while ``bm project add`` reported the same project already
    existed. The two commands must agree that the project exists.
    """
    main_path = (tmp_path / "main").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {"main": {"path": main_path}},
            "default_project": "main",
        }
    )

    # The local index does not know about this project, so the query returns empty.
    async def fake_list_projects(self):
        return ProjectList.model_validate({"projects": [], "default_project": "main"})

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list", "--json"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert json.loads(result.stdout) == {
        "projects": [
            {
                "name": "main",
                "permalink": "main",
                "path": project_cmd.format_path(main_path),
                "is_default": True,
            }
        ]
    }


def test_project_list_shows_name_in_narrow_terminal(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """The Name column must survive a default-width terminal (B2).

    Regression: Path was declared ``no_wrap=True``, so a long project path
    claimed the whole line and Rich squeezed every other column — Name included —
    to zero width. The table then rendered projects by path only, and the name a
    user has to pass to ``--project`` was not recoverable from the output.
    """
    long_path = (tmp_path / ("nested/" * 12) / "alpha-notes").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": long_path}},
            "default_project": "alpha",
        }
    )

    async def fake_list_projects(self):
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": "alpha",
                        "path": long_path,
                        "is_default": True,
                    }
                ],
                "default_project": "alpha",
            }
        )

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "80"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Name" in result.stdout
    assert "alpha" in result.stdout


def test_project_list_warns_when_config_project_missing_from_index(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """A project in config that the index does not know about must be called out (B2).

    config.json and the project table are two registries that can disagree, and the
    row seeded from config to fix #1003 made the disagreement invisible — the table
    looked identical whether or not the project was indexed. A project in this state
    resolves for routing but returns nothing from search.
    """
    alpha_path = (tmp_path / "alpha").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": alpha_path}},
            "default_project": "alpha",
        }
    )

    # The local index has no row for alpha — the fresh-config state, where
    # config.json is written before anything materializes the project table.
    async def fake_list_projects(self):
        return ProjectList.model_validate({"projects": [], "default_project": "alpha"})

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "list"], env={"COLUMNS": "240"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "alpha" in result.stdout
    assert "not indexed" in result.stdout


def test_project_ls_lists_local_files(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """project ls walks the project directory on disk."""
    project_dir = tmp_path / "alpha-files"
    (project_dir / "docs").mkdir(parents=True, exist_ok=True)
    (project_dir / "notes.md").write_text("# local note")
    (project_dir / "docs" / "spec.md").write_text("# spec")

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": project_dir.as_posix()}},
            "default_project": "alpha",
        }
    )

    async def fake_list_projects(self):
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": "alpha",
                        "path": project_dir.as_posix(),
                        "is_default": True,
                    }
                ],
                "default_project": "alpha",
            }
        )

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(app, ["project", "ls", "--name", "alpha"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha:" in result.stdout
    assert "notes.md" in result.stdout
    assert "docs/spec.md" in result.stdout


def test_project_ls_scopes_to_a_subpath(
    runner: CliRunner, write_config, mock_client, tmp_path, monkeypatch
):
    """The optional positional path narrows the listing to one subtree."""
    project_dir = tmp_path / "alpha-files"
    (project_dir / "docs").mkdir(parents=True, exist_ok=True)
    (project_dir / "notes.md").write_text("# local note")
    (project_dir / "docs" / "spec.md").write_text("# spec")

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": project_dir.as_posix()}},
            "default_project": "alpha",
        }
    )

    async def fake_list_projects(self):
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": "alpha",
                        "path": project_dir.as_posix(),
                        "is_default": True,
                    }
                ],
                "default_project": "alpha",
            }
        )

    monkeypatch.setattr(ProjectClient, "list_projects", fake_list_projects)

    result = runner.invoke(
        app, ["project", "ls", "--name", "alpha", "docs"], env={"COLUMNS": "200"}
    )

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.stdout}"
    assert "Files in alpha/docs:" in result.stdout
    assert "docs/spec.md" in result.stdout
    assert "notes.md" not in result.stdout.replace("docs/spec.md", "")


def test_project_add_requires_a_path(runner: CliRunner):
    """path is a required positional, so omitting it is a usage error."""
    result = runner.invoke(app, ["project", "add", "solo"])

    assert result.exit_code == 2
