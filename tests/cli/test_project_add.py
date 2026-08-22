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
    assert calls == [{"name": "test-project", "path": None, "set_default": False, "governed": True}]
    # Nothing to warn about: the project is where the design puts it.
    assert "outside the store" not in result.stdout


@pytest.fixture
def record_create_payload(monkeypatch, tmp_path):
    """Answer `ProjectClient.create_project` and return the payloads it received.

    The governance flags are CLI shape: what the command has to get right is the
    `governed` key it sends. The file this key eventually writes is asserted
    where it is written, in `tests/services/test_project_service.py`.
    """
    calls: list[dict] = []

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_create_project(self, project_data):
        calls.append(project_data)
        return ProjectStatusResponse.model_validate(
            {
                "message": f"Project '{project_data['name']}' added successfully",
                "status": "success",
                "default": False,
                "old_project": None,
                "new_project": {
                    "id": 1,
                    "external_id": "12345678-1234-1234-1234-123456789012",
                    "name": project_data["name"],
                    "path": str(tmp_path / "store" / "12345678"),
                    "is_default": False,
                },
            }
        )

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "create_project", fake_create_project)
    return calls


def test_project_add_asks_for_governance_by_default(runner, mock_config, record_create_payload):
    """A bare `project add` asks for the vocabulary (GAPS U49).

    D8 made this opt-in because a governed project refused MCP's `write_note`
    default type. `DEFAULT_VOCABULARY` declares `note` now, so the reason is
    gone and the checks are on unless the caller says otherwise.
    """
    result = runner.invoke(app, ["project", "add", "checked"])

    assert result.exit_code == 0, result.output
    assert record_create_payload == [
        {"name": "checked", "path": None, "set_default": False, "governed": True}
    ]


def test_project_add_ungoverned_asks_for_no_vocabulary(runner, mock_config, record_create_payload):
    """`--ungoverned` is the opt-out, and it is the only way to get one."""
    result = runner.invoke(app, ["project", "add", "unchecked", "--ungoverned"])

    assert result.exit_code == 0, result.output
    assert record_create_payload == [
        {"name": "unchecked", "path": None, "set_default": False, "governed": False}
    ]


def test_project_add_still_accepts_the_deprecated_governed_flag(
    runner, mock_config, record_create_payload
):
    """`--governed` names the default now, so it is accepted and changes nothing.

    The session hook and the migration skill both still spell it out. Removing
    the option would fail their invocations at Typer's parser, before the
    command ran at all — so it stays until they stop passing it (GAPS U49).
    """
    result = runner.invoke(app, ["project", "add", "checked", "--governed"])

    assert result.exit_code == 0, result.output
    assert record_create_payload == [
        {"name": "checked", "path": None, "set_default": False, "governed": True}
    ]


def test_project_add_refuses_both_governance_flags(runner, mock_config, record_create_payload):
    """Contradictory flags are an error, never a precedence rule."""
    result = runner.invoke(app, ["project", "add", "confused", "--governed", "--ungoverned"])

    assert result.exit_code == 1, result.output
    assert "--governed and --ungoverned contradict" in result.output
    # The refusal comes before the create, so nothing was registered.
    assert record_create_payload == []
    # Contract rule 6: nothing lands on stdout on the error path.
    assert result.stdout == ""


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
            "governed": True,
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


def test_project_add_here_only_here_narrows_the_marker(
    runner,
    mock_config,
    stub_create_project,
    write_registry_file,
    registry_external_id,
    monkeypatch,
    tmp_path,
):
    """`--only-here` is how a catch-all workspace project gets a home (GAPS U40)."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    write_registry_file({"workspace": str(tmp_path / "store")}, default="workspace")

    result = runner.invoke(app, ["project", "add", "workspace", "--here", "--only-here"])

    assert result.exit_code == 0, result.output
    assert (work / ".bm.yml").read_text() == (
        f"project: workspace\nid: {registry_external_id('workspace')}\nscope: here\n"
    )
    assert "(only here)" in result.stdout


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


def test_project_add_here_records_the_repo(
    runner,
    mock_config,
    stub_create_project,
    write_registry_file,
    monkeypatch,
    tmp_path,
):
    """`add --here` in a repo captures the origin URL like `mark` does (GAPS U36)."""
    import subprocess

    from basic_memory.project_registry import lookup_project_repo

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    write_registry_file({"research": str(tmp_path / "store")}, default="research")
    subprocess.run(["git", "init", "-q", str(work)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(work), "remote", "add", "origin", "https://example.com/owner/research"],
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["project", "add", "research", "--here"])

    assert result.exit_code == 0, result.output
    assert lookup_project_repo("research") == "https://example.com/owner/research"
    assert "repo: https://example.com/owner/research" in result.stdout


def test_add_only_here_without_here_is_refused(runner, tmp_path, monkeypatch):
    """`--only-here` qualifies the marker `--here` writes; alone it would be a silent no-op."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["project", "add", "orphan", "--only-here"])
    assert result.exit_code != 0
    assert "--only-here" in result.output
    assert "pass both" in result.output
    assert not (tmp_path / ".bm.yml").exists()
