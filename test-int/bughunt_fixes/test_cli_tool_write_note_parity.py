"""Bug hunt regression tests: `bm tool write-note` CLI/MCP parity.

Covers two confirmed bugs found by the integration-test bug hunt:

- #1 / #5: write-note exits 0 on a conflict/error JSON result (silent failure,
  inconsistent with delete-note/edit-note/search-notes which exit non-zero).
- #2: write-note had no `--overwrite` flag even though the MCP write_note tool
  supports overwrite=True to replace an existing note.

These are integration tests: real CliRunner -> CLI command -> MCP tool ->
in-process ASGI API -> real SQLite DB and filesystem. No mocks.

Under output contract v2 a conflict is an error: the message goes to stderr,
the exit code is 1, and no record lands on stdout. The `action: conflict` field
the MCP layer reports is pinned by the MCP-level test below, which is where it
is still observable.
"""

import asyncio

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app
from basic_memory.mcp.tools import write_note as mcp_write_note

runner = CliRunner()


def _record(stdout: str) -> dict[str, str]:
    """Parse labelled `key: value` lines into a dict."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


# --- #5: blocked NOTE_ALREADY_EXISTS write must not report success ---


def _cli_write(project_name: str):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Conflict Note",
            "--folder",
            "conflict",
            "--content",
            "# Conflict Note\n\nFirst body.\n",
            "--project",
            project_name,
        ],
    )


def test_mcp_write_note_conflict_emits_error(app, app_config, test_project, config_manager):
    """Baseline: the MCP tool reports NOTE_ALREADY_EXISTS on a blocked re-write."""

    async def _go():
        first = await mcp_write_note(
            title="Conflict Note",
            content="# Conflict Note\n\nFirst body.\n",
            directory="conflict",
            project=test_project.name,
            output_format="json",
        )
        # output_format="json" returns a dict; narrow for the type checker.
        assert isinstance(first, dict)
        assert first.get("action") == "created"
        assert "error" not in first

        second = await mcp_write_note(
            title="Conflict Note",
            content="# Conflict Note\n\nSecond body (should be blocked).\n",
            directory="conflict",
            project=test_project.name,
            output_format="json",
        )
        return second

    second = asyncio.run(_go())
    assert isinstance(second, dict)
    assert second.get("error") == "NOTE_ALREADY_EXISTS"
    assert second.get("action") == "conflict"
    assert second.get("file_path") is None


def test_cli_write_note_conflict_should_exit_nonzero(app, app_config, test_project, config_manager):
    """CLI write-note must NOT exit 0 when the write was blocked by a conflict."""
    first = _cli_write(test_project.name)
    assert first.exit_code == 0, first.output
    assert _record(first.stdout)["action"] == "created"

    second = _cli_write(test_project.name)

    assert "NOTE_ALREADY_EXISTS" in second.stderr, second.output
    # Nothing was written, so no record may appear on stdout.
    assert "permalink: " not in second.stdout

    assert second.exit_code == 1, (
        f"write-note exited {second.exit_code} after a blocked NOTE_ALREADY_EXISTS "
        "write; the note was NOT written but the CLI reported success"
    )


# --- #2: write-note --overwrite flag (MCP overwrite=True parity) ---


def _write_overwrite(args_extra: list[str]):
    return runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Overwrite Parity Note",
            "--folder",
            "parity-overwrite",
            "--content",
            "# Overwrite Parity Note\n\nVERSION_BODY",
            *args_extra,
        ],
    )


def test_write_note_cli_can_overwrite_like_mcp(app, app_config, test_project, config_manager):
    """CLI write-note must be able to overwrite an existing note (MCP overwrite=True)."""
    first = _write_overwrite(["--project", test_project.name])
    assert first.exit_code == 0, first.output
    permalink = _record(first.stdout)["permalink"]

    second = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Overwrite Parity Note",
            "--folder",
            "parity-overwrite",
            "--content",
            "# Overwrite Parity Note\n\nNEW_VERSION_BODY",
            "--project",
            test_project.name,
            "--overwrite",
        ],
    )

    assert second.exit_code == 0, (
        "CLI write-note has no way to overwrite an existing note even though "
        "the MCP write_note tool supports overwrite=True. "
        f"exit_code={second.exit_code} output={second.output}"
    )

    read = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--project", test_project.name],
    )
    assert read.exit_code == 0, read.output
    assert "NEW_VERSION_BODY" in read.stdout
