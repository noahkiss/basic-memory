"""Tests for `bm project add`."""

import json
from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectStatusResponse

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project  # noqa: F401

# `project` imports the MCP client graph inside the function body, not at
# module scope, so CLI startup stays off it (GAPS.md T30). Patch the source
# module, which the function-local import resolves against at call time.
import basic_memory.mcp.async_client as async_client_module
from basic_memory.project_marker import read_marker_id, read_marker_project


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


def test_project_add_takes_no_path(runner, mock_config, monkeypatch, tmp_path):
    """A project's home is store-derived, so the path argument is optional (D3).

    It used to be required, which made `store/<external_id>/` unreachable from
    the CLI: the request carried a user-chosen directory or nothing at all.
    """
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
                    "path": str(tmp_path / "store" / "12345678"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "test-project"])

    assert result.exit_code == 0, result.output
    assert calls == [
        {"name": "test-project", "path": None, "set_default": False, "governed": False}
    ]
    # Nothing to warn about: the project is where the design puts it.
    assert "outside the store" not in result.stdout


def test_project_add_governed_flag_reaches_the_api(runner, mock_config, monkeypatch, tmp_path):
    """`--governed` is what turns the schema checks on, so it must survive the hop.

    The default is off because an absent `vocabulary.yml` means ungoverned (GAPS
    W4) and declaring one is the human's act. It is no longer off because it
    broke MCP: `DEFAULT_VOCABULARY` declares `note`, `write_note`'s default type,
    so governing a project keeps that path working (verbs decision D8).
    """
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        calls.append(project_data)
        return ProjectStatusResponse.model_validate(
            {
                "message": "Project 'checked' added successfully",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": "12345678-1234-1234-1234-123456789012",
                    "name": "checked",
                    "path": str(tmp_path / "store" / "12345678"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "checked", "--governed"])

    assert result.exit_code == 0, result.output
    assert calls == [{"name": "checked", "path": None, "set_default": False, "governed": True}]


def test_project_add_with_a_path_says_it_is_off_store(runner, mock_config, monkeypatch, tmp_path):
    """A path argument is an import source, and notes there are outside the history."""

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        return ProjectStatusResponse.model_validate(
            {
                "message": "Project 'imported' added successfully",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": "12345678-1234-1234-1234-123456789012",
                    "name": "imported",
                    "path": str(tmp_path / "elsewhere"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "imported", str(tmp_path / "elsewhere")])

    assert result.exit_code == 0, result.output
    assert "outside the store" in result.stdout
    assert "not recorded in the note history" in result.stdout


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

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
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
            "governed": False,
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

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)

    result = runner.invoke(app, ["project", "add", "q3test", str(project_dir)])

    assert result.exit_code == 0, f"Exit code: {result.exit_code}, Stdout: {result.stdout}"
    assert "default project" in result.stdout


# --- `--here`: the marker `bm project add` leaves behind (GAPS U21) ---


@pytest.fixture
def stub_create_project(monkeypatch, tmp_path, registry_external_id):
    """Answer `ProjectClient.create_project` without a server, for any name.

    `--here` runs after the create, so these tests need the create to succeed
    and to say nothing about the marker — the marker is written from the
    registry, not from this response.
    """

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        return ProjectStatusResponse.model_validate(
            {
                "message": f"Project '{project_data['name']}' added successfully",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": registry_external_id(project_data["name"]),
                    "name": project_data["name"],
                    "path": str(tmp_path / "store"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)


def test_project_add_here_writes_both_marker_keys(
    runner,
    mock_config,
    stub_create_project,
    write_registry_file,
    registry_external_id,
    monkeypatch,
    tmp_path,
):
    """`--here` is what makes `store/<id>/` reachable without a `bm` call."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    write_registry_file({"research": str(tmp_path / "store")}, default="research")

    result = runner.invoke(app, ["project", "add", "research", "--here"])

    assert result.exit_code == 0, result.output
    marker = work / ".bm.yml"
    assert read_marker_project(marker) == "research"
    assert read_marker_id(marker) == registry_external_id("research")
    assert str(marker) in result.stdout


def test_project_add_without_here_writes_no_marker(
    runner, mock_config, stub_create_project, write_registry_file, monkeypatch, tmp_path
):
    """Positive control for the test above: the marker is the flag's doing."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    write_registry_file({"research": str(tmp_path / "store")}, default="research")

    result = runner.invoke(app, ["project", "add", "research"])

    assert result.exit_code == 0, result.output
    assert not (work / ".bm.yml").exists()


def test_project_add_here_refuses_a_foreign_marker(
    runner, mock_config, stub_create_project, write_registry_file, monkeypatch, tmp_path
):
    """The refusal comes before the create, so nothing is left half-done."""
    work = tmp_path / "work"
    work.mkdir()
    (work / ".bm.yml").write_text("project: someone-else\n")
    monkeypatch.chdir(work)
    write_registry_file({"research": str(tmp_path / "store")}, default="research")

    result = runner.invoke(app, ["project", "add", "research", "--here"])

    assert result.exit_code == 1, result.output
    assert "already names project 'someone-else'" in result.output
    assert (work / ".bm.yml").read_text() == "project: someone-else\n"
    # Contract rule 6: nothing lands on stdout on the error path.
    assert result.stdout == ""
