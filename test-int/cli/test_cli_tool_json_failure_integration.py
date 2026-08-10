"""Failure-path integration tests for CLI tool output (contract v2).

Contract rule 6: an error exits 1, writes one message line to stderr, and
leaves no payload on stdout. Rule 5: a well-scoped request whose answer is
"nothing there" is a result, not a failure — it exits 0.
"""

import re

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

COUNT_LINE = re.compile(r"^\d+ results$")


def _record(stdout: str) -> dict[str, str]:
    """Parse labelled `key: value` lines into a dict."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


def test_read_note_not_found_is_a_result(app, app_config, test_project, config_manager):
    """A miss is an empty result: it says so on stdout and exits 0."""
    result = runner.invoke(
        cli_app,
        ["tool", "read-note", "nonexistent-note-that-does-not-exist"],
    )

    assert result.exit_code == 0, result.output
    assert "Note not found." in result.stdout


def test_write_note_missing_content(app, app_config, test_project, config_manager):
    """write-note without content or stdin fails on stderr with no payload."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "No Content Note",
            "--folder",
            "test",
        ],
        input="",  # Empty stdin
    )

    assert result.exit_code == 1, result.output
    assert "Empty content provided" in result.stderr
    # No record was written, so no labelled payload line may appear.
    assert "permalink: " not in result.stdout


def test_write_note_then_read_note_roundtrip(app, app_config, test_project, config_manager):
    """The permalink line from write-note is what read-note takes as its argument."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Roundtrip Test",
            "--folder",
            "test-roundtrip",
            "--content",
            "# Roundtrip Test\n\nContent for roundtrip.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    permalink = _record(write_result.stdout)["permalink"]
    assert permalink

    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink],
    )
    assert read_result.exit_code == 0, read_result.output
    assert "Content for roundtrip." in read_result.stdout


def test_recent_activity_empty_project(app, app_config, test_project, config_manager, monkeypatch):
    """recent-activity with nothing to report still renders a count line, exit 0."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, "recent-activity wrote nothing"
    assert COUNT_LINE.match(lines[-1]), lines[-1]
