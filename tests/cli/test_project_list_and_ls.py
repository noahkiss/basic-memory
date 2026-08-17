"""Tests for project list display and project ls behavior."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
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


def _stub_project_list(monkeypatch, name: str, path: str) -> None:
    async def fake_fetch_project_list():
        return ProjectList.model_validate(
            {
                "projects": [
                    {
                        "id": 1,
                        "external_id": "11111111-1111-1111-1111-111111111111",
                        "name": name,
                        "path": path,
                        "is_default": True,
                    }
                ],
                "default_project": name,
            }
        )

    monkeypatch.setattr(project_cmd, "fetch_project_list", fake_fetch_project_list)


def test_project_list_shows_indexed_project(runner: CliRunner, write_config, tmp_path, monkeypatch):
    """The row carries the name a caller passes to --project, then its path."""
    alpha_path = (tmp_path / "alpha-local").as_posix()

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": alpha_path}},
            "default_project": "alpha",
        }
    )
    _stub_project_list(monkeypatch, "alpha", alpha_path)

    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.output}"
    assert result.stdout.splitlines() == [
        f"alpha  {project_cmd.format_path(alpha_path)}  (default)",
        "1 projects",
    ]


def test_project_ls_lists_local_files(runner: CliRunner, write_config, tmp_path, monkeypatch):
    """project ls walks the project directory on disk, relative path first."""
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
    _stub_project_list(monkeypatch, "alpha", project_dir.as_posix())

    result = runner.invoke(app, ["project", "ls", "--name", "alpha"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.output}"
    assert result.stdout.splitlines() == [
        "docs/spec.md  6",
        "notes.md      12",
        "2 files",
    ]


def test_project_ls_scopes_to_a_subpath(runner: CliRunner, write_config, tmp_path, monkeypatch):
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
    _stub_project_list(monkeypatch, "alpha", project_dir.as_posix())

    result = runner.invoke(app, ["project", "ls", "--name", "alpha", "docs"])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, output: {result.output}"
    assert result.stdout.splitlines() == ["docs/spec.md  6", "1 files"]


def test_project_ls_empty_directory_is_a_result(
    runner: CliRunner, write_config, tmp_path, monkeypatch
):
    """A well-scoped listing with nothing in it is a result, not a failure."""
    project_dir = tmp_path / "alpha-empty"
    project_dir.mkdir()

    write_config(
        {
            "env": "dev",
            "projects": {"alpha": {"path": project_dir.as_posix()}},
            "default_project": "alpha",
        }
    )
    _stub_project_list(monkeypatch, "alpha", project_dir.as_posix())

    result = runner.invoke(app, ["project", "ls", "--name", "alpha"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["0 files"]


def test_project_ls_unknown_project_fails_on_stderr(
    runner: CliRunner, write_config, tmp_path, monkeypatch
):
    """An unscopeable request is a failure: one line on stderr, nothing on stdout."""
    write_config({"env": "dev", "projects": {}, "default_project": "alpha"})
    _stub_project_list(monkeypatch, "alpha", (tmp_path / "alpha").as_posix())

    result = runner.invoke(app, ["project", "ls", "--name", "missing"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Project 'missing' not found" in result.stderr


def test_project_add_requires_a_name(runner: CliRunner):
    """The name is the one required argument; omitting it is a usage error.

    The *path* used to be required too. It is optional now: a project's home is
    store-derived and a path argument means an import source (verbs decision D3).
    That behaviour needs a stubbed client, so it is covered in
    `test_project_add.py` rather than here, where nothing is stubbed.
    """
    result = runner.invoke(app, ["project", "add"])

    assert result.exit_code == 2
