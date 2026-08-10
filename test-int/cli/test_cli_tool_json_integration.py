"""Integration tests for CLI tool output, version 2 of docs/OUTPUT_CONTRACT.md.

These tests used to parse a JSON envelope off stdout. v2 removed `--json`:
each verb has exactly one rendering, so the tests now pin the rendering
itself — labelled `key: value` lines for single records, one line per row
plus a trailing count for listings, byte-exact content for `read-note`.
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


def _rows_and_count(stdout: str) -> tuple[list[str], str]:
    """Split a listing into its payload rows and its trailing count line."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "listing wrote nothing to stdout"
    assert COUNT_LINE.match(lines[-1]), f"last line is not a count: {lines[-1]!r}"
    return lines[:-1], lines[-1]


def test_write_note_record_lines(app, app_config, test_project, config_manager):
    """write-note renders one labelled line per field, identifier first."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Integration Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Test\n\nThis is test content.",
        ],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    lines = result.stdout.splitlines()
    # Contract rule 2: the identifier leads the record.
    assert lines[0].startswith("permalink: ")

    fields = _record(result.stdout)
    assert fields["title"] == "Integration Test Note"
    assert fields["permalink"]
    assert fields["file_path"]
    assert fields["action"] == "created"


def test_read_note_writes_content_verbatim(app, app_config, test_project, config_manager):
    """read-note's payload is the note body, not a record."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Read Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Read Test\n\nContent to read back.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    permalink = _record(write_result.stdout)["permalink"]

    result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    assert "# Read Test" in result.stdout
    assert "Content to read back." in result.stdout
    # The body is the whole payload — no labelled metadata wrapped around it.
    assert "permalink: " not in result.stdout


def test_read_note_include_frontmatter(app, app_config, test_project, config_manager):
    """read-note --include-frontmatter writes the literal file, frontmatter first."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Read Frontmatter Note",
            "--folder",
            "test-notes",
            "--content",
            "# Read Frontmatter Note\n\nFrontmatter test content.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    permalink = _record(write_result.stdout)["permalink"]

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "read-note",
            permalink,
            "--include-frontmatter",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("---")
    assert "title: Read Frontmatter Note" in result.stdout
    assert f"permalink: {permalink}" in result.stdout
    assert "Frontmatter test content." in result.stdout


def test_recent_activity_row_lines(app, app_config, test_project, config_manager, monkeypatch):
    """recent-activity renders one row per item and a trailing count."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    # Write a note to ensure there's recent activity
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Activity Test Note",
            "--folder",
            "test-notes",
            "--content",
            "# Activity\n\nTest content for activity.",
        ],
    )
    assert write_result.exit_code == 0, write_result.output

    result = runner.invoke(
        cli_app,
        ["tool", "recent-activity"],
    )

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    rows, count_line = _rows_and_count(result.stdout)
    assert rows, "expected at least the note just written"
    assert count_line == f"{len(rows)} results"

    # Row shape: permalink, type, title, updated — identifier first.
    columns = rows[0].split("  ")
    assert len(columns) >= 4, rows[0]
    assert columns[0].strip()
    assert any("Activity Test Note" in row for row in rows)
