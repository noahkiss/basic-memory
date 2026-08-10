"""CLI rendering tests for `bm status` and `bm project list` (output contract v2).

The file was originally a `--json` suite. v2 removed `--json` from every verb, so what
survives is the behavior those tests were really guarding: which fields reach stdout,
and the `--wait` parameter check.
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectList
from basic_memory.schemas.project_index import (
    ProjectIndexObservedFileResponse,
    ProjectIndexStatusResponse,
)

# Importing registers subcommands on the shared app instance.
import basic_memory.cli.commands.project as project_cmd  # noqa: F401

runner = CliRunner()


PROJECT_INDEX_STATUS_WITH_FILES = ProjectIndexStatusResponse(
    total_files=2,
    unindexed_file_count=0,
    observed_files=(
        ProjectIndexObservedFileResponse(
            path="notes/new-file.md",
            checksum="abc12345",
            size=123,
            indexed=True,
        ),
        ProjectIndexObservedFileResponse(
            path="notes/existing.md",
            checksum="def67890",
            size=456,
            indexed=True,
        ),
    ),
)

PROJECT_INDEX_STATUS_EMPTY = ProjectIndexStatusResponse(
    total_files=0,
    unindexed_file_count=0,
    observed_files=(),
)


_MOCK_PROJECT_ITEM = MagicMock()
_MOCK_PROJECT_ITEM.name = "test-project"
_MOCK_PROJECT_ITEM.external_id = "11111111-1111-1111-1111-111111111111"


@asynccontextmanager
async def _fake_get_client(project_name=None):
    yield MagicMock()


def _invoke_status(status: ProjectIndexStatusResponse, *args: str):
    """Run `bm status` against a stubbed project-index observation."""
    with (
        patch("basic_memory.cli.commands.status.resolve_cli_project", return_value="test-project"),
        patch(
            "basic_memory.cli.commands.status.get_active_project",
            new_callable=AsyncMock,
            return_value=_MOCK_PROJECT_ITEM,
        ),
        patch("basic_memory.cli.commands.status.get_client", side_effect=_fake_get_client),
        patch.object(ProjectClient, "get_status", AsyncMock(return_value=status)),
    ):
        return runner.invoke(cli_app, ["status", *args])


# ---------------------------------------------------------------------------
# status rendering
# ---------------------------------------------------------------------------


def test_status_reports_the_project_and_file_counts():
    result = _invoke_status(PROJECT_INDEX_STATUS_WITH_FILES)

    assert result.exit_code == 0, result.output
    assert "project: test-project" in result.stdout
    assert "total files: 2" in result.stdout
    assert "unindexed files: 0" in result.stdout
    # Without --verbose the listing stays out of the payload.
    assert "notes/new-file.md" not in result.stdout


def test_status_verbose_lists_each_observed_file_path_first():
    result = _invoke_status(PROJECT_INDEX_STATUS_WITH_FILES, "--verbose")

    assert result.exit_code == 0, result.output
    listed = [line for line in result.stdout.splitlines() if line.startswith("notes/")]
    # Full relative path first, then the short checksum — no directory grouping.
    assert listed == [
        "notes/existing.md  def67890",
        "notes/new-file.md  abc12345",
    ]


def test_status_with_no_observed_files_reports_zero():
    result = _invoke_status(PROJECT_INDEX_STATUS_EMPTY)

    assert result.exit_code == 0, result.output
    assert "total files: 0" in result.stdout


# ---------------------------------------------------------------------------
# status --wait
#
# The event-index status endpoint reports current observed files, not a pending
# change count, so --wait is a compatibility flag and does not poll.
# ---------------------------------------------------------------------------


def test_status_wait_reads_the_observation_once():
    get_status = AsyncMock(return_value=PROJECT_INDEX_STATUS_WITH_FILES)

    with (
        patch("basic_memory.cli.commands.status.resolve_cli_project", return_value="test-project"),
        patch(
            "basic_memory.cli.commands.status.get_active_project",
            new_callable=AsyncMock,
            return_value=_MOCK_PROJECT_ITEM,
        ),
        patch("basic_memory.cli.commands.status.get_client", side_effect=_fake_get_client),
        patch.object(ProjectClient, "get_status", get_status),
    ):
        result = runner.invoke(cli_app, ["status", "--wait"])

    assert result.exit_code == 0, result.output
    assert get_status.await_count == 1


def test_status_wait_with_zero_timeout_still_reports():
    """timeout=0 no longer means "already expired" — there is no polling loop left."""
    result = _invoke_status(PROJECT_INDEX_STATUS_WITH_FILES, "--wait", "--timeout", "0")

    assert result.exit_code == 0, result.output
    assert "total files: 2" in result.stdout


def test_status_wait_negative_timeout_is_rejected():
    """A negative --timeout fails fast with a usage error instead of a confusing
    'Timed out after -5s' message. The guard runs before any client I/O, no mocks needed."""
    result = runner.invoke(cli_app, ["status", "--wait", "--timeout", "-5"])

    assert result.exit_code != 0
    # Typer colorizes the flag name with ANSI codes (so the literal "--timeout" is split),
    # but the message body renders clean — assert on that.
    assert "must be >= 0" in result.output


# ---------------------------------------------------------------------------
# project list rendering
# ---------------------------------------------------------------------------


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict):
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


def test_project_list_renders_name_path_and_default_marker(write_config, tmp_path, monkeypatch):
    """One line per project, name first, then path, then the default marker."""
    alpha_local = (tmp_path / "alpha-local").as_posix()

    write_config({"env": "dev"})

    local_payload = {
        "projects": [
            {
                "id": 1,
                "external_id": "11111111-1111-1111-1111-111111111111",
                "name": "alpha",
                "path": alpha_local,
                "is_default": True,
            },
            {
                "id": 2,
                "external_id": "22222222-2222-2222-2222-222222222222",
                "name": "beta",
                "path": (tmp_path / "beta-local").as_posix(),
                "is_default": False,
            },
        ],
        "default_project": "alpha",
    }

    async def fake_fetch_project_list():
        return ProjectList.model_validate(local_payload)

    monkeypatch.setattr(project_cmd, "fetch_project_list", fake_fetch_project_list)

    result = runner.invoke(cli_app, ["project", "list"])

    assert result.exit_code == 0, result.output
    # HOME is tmp_path here, so the display path collapses to ~ (format_path).
    assert result.stdout.splitlines() == [
        "alpha  ~/alpha-local  (default)",
        "beta   ~/beta-local",
        "2 projects",
    ]
