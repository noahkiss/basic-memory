"""Integration coverage for tool search-notes with metadata filters.

Under output contract v2 search-notes emits one row per result,
`{permalink}  {score}  {title}  {snippet}`, then a count line — so the
filter is checked against the leading permalink column.
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


def test_search_notes_query_plus_meta_filter(app, app_config, test_project, config_manager):
    """`bm tool search-notes` should support query + metadata filter together."""
    active_content = "---\nstatus: active\n---\n# Active Meta Note\n\nMetaFilterToken"
    inactive_content = "---\nstatus: inactive\n---\n# Inactive Meta Note\n\nMetaFilterToken"

    active_write = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Active Meta Note",
            "--folder",
            "meta-tests",
            "--content",
            active_content,
        ],
    )
    assert active_write.exit_code == 0, active_write.output
    active_permalink = _record(active_write.stdout)["permalink"]

    inactive_write = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Inactive Meta Note",
            "--folder",
            "meta-tests",
            "--content",
            inactive_content,
        ],
    )
    assert inactive_write.exit_code == 0, inactive_write.output
    inactive_permalink = _record(inactive_write.stdout)["permalink"]

    search = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "MetaFilterToken",
            "--meta",
            "status=active",
            "--page-size",
            "20",
        ],
    )
    assert search.exit_code == 0, search.output

    permalinks = _permalinks(search.stdout)
    assert active_permalink in permalinks
    assert inactive_permalink not in permalinks
