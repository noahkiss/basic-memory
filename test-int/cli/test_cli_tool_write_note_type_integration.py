"""Integration coverage for `bm tool write-note --type` (Issue #875).

Rendered under output contract v2: write-note emits labelled `key: value`
lines, `read-note --include-frontmatter` writes the file byte-exactly, and
search-notes emits one row per result with the permalink first.
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


def _permalinks(stdout: str) -> set[str]:
    """Collect the leading permalink column from search-notes rows."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    rows = [
        line for line in lines if not COUNT_LINE.match(line) and line != "more results available"
    ]
    return {line.split()[0] for line in rows}


def test_write_note_type_flag_round_trip(app, app_config, test_project, config_manager):
    """`--type` sets the persisted note type and is searchable via `--type`."""
    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "CLI Typed Note",
            "--folder",
            "typed",
            "--content",
            "# CLI Typed Note\n\nCliTypeToken body.",
            "--type",
            "guide",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    permalink = _record(write_result.stdout)["permalink"]

    # Read back the frontmatter to confirm the persisted type.
    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--include-frontmatter"],
    )
    assert read_result.exit_code == 0, read_result.output
    assert "type: guide" in read_result.stdout.splitlines()

    # The search note-type filter must return the typed note.
    search_result = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "CliTypeToken",
            "--type",
            "guide",
            "--page-size",
            "20",
        ],
    )
    assert search_result.exit_code == 0, search_result.output
    assert permalink in _permalinks(search_result.stdout)


def test_write_note_content_frontmatter_type_wins_over_flag(
    app, app_config, test_project, config_manager
):
    """A `type:` in content frontmatter takes precedence over `--type` (documented behavior)."""
    content = "---\ntype: session\n---\n# Frontmatter Wins\n\nFrontmatterWinsToken body."

    write_result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Frontmatter Wins",
            "--folder",
            "typed",
            "--content",
            content,
            "--type",
            "guide",
        ],
    )
    assert write_result.exit_code == 0, write_result.output
    permalink = _record(write_result.stdout)["permalink"]

    read_result = runner.invoke(
        cli_app,
        ["tool", "read-note", permalink, "--include-frontmatter"],
    )
    assert read_result.exit_code == 0, read_result.output
    # Content frontmatter "session" wins over the --type "guide" flag.
    frontmatter_lines = read_result.stdout.splitlines()
    assert "type: session" in frontmatter_lines
    assert "type: guide" not in frontmatter_lines
