"""Bug hunt regression test (#3): `bm tool recent-activity` page_size default.

The MCP recent_activity tool defaults page_size=10; the CLI wrapper used to
default to 50. Because page_size becomes the SQL LIMIT for the query, identical
default invocations returned a different number of rows from CLI vs MCP. This
integration test proves the CLI default now matches the MCP default of 10.

Under output contract v2 the rows are one line each, followed by a count line,
so row parity is counted from the payload lines.
"""

import re

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

MCP_DEFAULT_PAGE_SIZE = 10

COUNT_LINE = re.compile(r"^(\d+) results$")


def _row_count(stdout: str) -> int:
    """Count recent-activity payload rows and cross-check the count line."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "recent-activity wrote nothing to stdout"
    match = COUNT_LINE.match(lines[-1])
    assert match, f"last line is not a count: {lines[-1]!r}"
    rows = lines[:-1]
    assert int(match.group(1)) == len(rows)
    return len(rows)


def _write_note(title: str, folder: str, content: str) -> None:
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            title,
            "--folder",
            folder,
            "--content",
            content,
        ],
    )
    assert result.exit_code == 0, result.output


def test_recent_activity_default_page_size_matches_mcp(
    app, app_config, test_project, config_manager, monkeypatch
):
    """CLI recent-activity default page_size must match the MCP tool default (10)."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)

    for i in range(15):
        _write_note(
            f"Parity Note {i:02d}",
            "parity-recent",
            f"# Parity Note {i:02d}\n\nUnique body token PARITY{i:02d}.",
        )

    mcp_default_result = runner.invoke(
        cli_app,
        [
            "tool",
            "recent-activity",
            "--project",
            test_project.name,
            "--page-size",
            str(MCP_DEFAULT_PAGE_SIZE),
        ],
    )
    assert mcp_default_result.exit_code == 0, mcp_default_result.output
    mcp_default_rows = _row_count(mcp_default_result.stdout)
    assert mcp_default_rows == MCP_DEFAULT_PAGE_SIZE

    cli_default_result = runner.invoke(
        cli_app,
        ["tool", "recent-activity", "--project", test_project.name],
    )
    assert cli_default_result.exit_code == 0, cli_default_result.output
    cli_default_rows = _row_count(cli_default_result.stdout)

    assert cli_default_rows == MCP_DEFAULT_PAGE_SIZE, (
        f"CLI recent-activity default returned {cli_default_rows} rows but "
        f"the MCP tool default (page_size={MCP_DEFAULT_PAGE_SIZE}) returns "
        f"{mcp_default_rows}; the CLI and MCP default page_size must match."
    )
