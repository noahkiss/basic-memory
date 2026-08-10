"""Bug hunt regression test (#4): `bm tool search-notes` --category filter.

The MCP search_notes tool exposes a `categories` parameter for exact-match
observation-category filtering. The CLI wrapper had no equivalent flag. This
integration test asserts the CLI now exposes `--category` and that it filters
observation results to the requested category exactly.

Under output contract v2 each row leads with the permalink, and an
observation's permalink embeds its category as
`{entity-permalink}/observations/{category}/{slug}` — that is where the filter
is checked.
"""

import re

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

COUNT_LINE = re.compile(r"^\d+ results$")


def _permalinks(stdout: str) -> list[str]:
    """Collect the leading permalink column from search-notes rows."""
    lines = [line for line in stdout.splitlines() if line.strip()]
    rows = [
        line for line in lines if not COUNT_LINE.match(line) and line != "more results available"
    ]
    return [line.split()[0] for line in rows]


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


def test_search_notes_exposes_category_filter(app, app_config, test_project, config_manager):
    """CLI search-notes should expose --category like the MCP `categories` param."""
    _write_note(
        "Category Filter Note",
        "parity-category",
        "# Category Filter Note\n\n"
        "## Observations\n"
        "- [requirement] system must authenticate users CATTOKEN\n"
        "- [decision] use OAuth for auth CATTOKEN\n",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "search-notes",
            "CATTOKEN",
            "--project",
            test_project.name,
            "--entity-type",
            "observation",
            "--category",
            "requirement",
        ],
    )

    assert result.exit_code == 0, (
        "`--category` filter is not supported by the CLI search-notes command "
        "even though the MCP search_notes tool documents a `categories` param. "
        f"exit_code={result.exit_code} output={result.output}"
    )

    permalinks = _permalinks(result.stdout)
    assert permalinks, f"expected the requirement observation, got:\n{result.stdout}"
    assert all("/observations/requirement/" in permalink for permalink in permalinks), (
        "--category requirement should return only requirement observations, "
        f"got permalinks={permalinks}"
    )
    assert not any("/observations/decision/" in permalink for permalink in permalinks)
