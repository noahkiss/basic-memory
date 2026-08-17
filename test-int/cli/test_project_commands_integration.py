"""Integration tests for project CLI commands (output contract v2).

`bm project list` renders `{name}  {path}` per line with a `(default)` marker
column and a trailing `N projects` count. `bm project info` renders plain
labelled sections — there is no `--json` and no panel.
"""

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.commands.project import fetch_project_list
from basic_memory.cli.main import app as cli_app

WIDE_TERMINAL_ENV = {"COLUMNS": "240", "LINES": "60"}


def _project_names() -> set[str]:
    """Read the current registered project names via the direct service path.

    The database owns the project registry (GAPS B2); config.json no longer
    tracks it, so tests read the registry the same way `bm project list` does.
    """
    result = run_with_cleanup(fetch_project_list())
    return {project.name for project in result.projects}


def test_project_list(app, app_config, test_project, config_manager):
    """`bm project list` shows one row per project and marks the default."""
    runner = CliRunner()
    # --quiet so the count line is last: a notice may follow the payload on a
    # corpus that has something outstanding (contract rule 4, GAPS W5-B).
    result = runner.invoke(cli_app, ["project", "list", "--quiet"], env=WIDE_TERMINAL_ENV)

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        print(f"Exception: {result.exception}")
    assert result.exit_code == 0

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines[-1].endswith(" projects")

    rows = lines[:-1]
    # Contract rule 2: the name a caller passes to --project leads the row.
    assert any(row.split()[0] == "test-project" for row in rows)
    assert any("(default)" in row for row in rows)


def test_project_info(app, app_config, test_project, config_manager):
    """`bm project info` names the project and reports its statistics."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "info", "test-project"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0
    assert "name: test-project" in result.stdout
    assert "entities: " in result.stdout


def test_project_info_renders_labelled_sections(app, app_config, test_project, config_manager):
    """Every info line is a plain heading or a `key: value` pair — no JSON, no panel."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["project", "info", "test-project"])

    if result.exit_code != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
    assert result.exit_code == 0

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    headings = [line for line in lines if ": " not in line]
    assert "Project" in headings
    assert "Statistics" in headings
    assert "System" in headings

    # Box-drawing and JSON braces are both out of contract.
    assert "{" not in result.stdout
    assert "─" not in result.stdout
    assert "│" not in result.stdout


def test_project_add_and_remove(app, app_config, config_manager):
    """Test adding and removing a project."""
    runner = CliRunner()

    # Use a separate temporary directory to avoid nested path conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        new_project_path = Path(temp_dir) / "new-project"
        new_project_path.mkdir()

        # Add project
        result = runner.invoke(cli_app, ["project", "add", "new-project", str(new_project_path)])

        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0
        assert (
            "Project 'new-project' added successfully" in result.stdout
            or "added" in result.stdout.lower()
        )

        # Verify it shows up in list
        result = runner.invoke(cli_app, ["project", "list"], env=WIDE_TERMINAL_ENV)
        assert result.exit_code == 0
        assert "new-project" in result.stdout

        # Remove project
        result = runner.invoke(cli_app, ["project", "remove", "new-project"])
        assert result.exit_code == 0
        assert "removed" in result.stdout.lower() or "deleted" in result.stdout.lower()


def test_project_set_default(app, app_config, config_manager):
    """Test setting default project."""
    runner = CliRunner()

    # Use a separate temporary directory to avoid nested path conflicts
    with tempfile.TemporaryDirectory() as temp_dir:
        new_project_path = Path(temp_dir) / "another-project"
        new_project_path.mkdir()

        # Add a second project
        result = runner.invoke(
            cli_app, ["project", "add", "another-project", str(new_project_path)]
        )
        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0

        # Set as default
        result = runner.invoke(cli_app, ["project", "default", "another-project"])
        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0
        assert "default" in result.stdout.lower()

        # Verify in list
        result = runner.invoke(cli_app, ["project", "list"], env=WIDE_TERMINAL_ENV)
        assert result.exit_code == 0
        # The new project should carry the (default) marker now
        for line in result.stdout.splitlines():
            if line.split()[:1] == ["another-project"]:
                assert "(default)" in line


def test_remove_main_project(app, app_config, config_manager):
    """Test that removing main project then listing projects prevents main from reappearing (issue #397)."""
    runner = CliRunner()

    # Create separate temp dirs for each project
    with (
        tempfile.TemporaryDirectory() as main_dir,
        tempfile.TemporaryDirectory() as new_default_dir,
    ):
        main_path = Path(main_dir)
        new_default_path = Path(new_default_dir)

        # Ensure main exists
        # Trigger: this test must work on Windows runners where output may contain "runneradmin".
        # Why: substring checks against command output can mistake path text for project names.
        # Outcome: use config state for setup decisions, then validate behavior via CLI invocation.
        if "main" not in _project_names():
            result = runner.invoke(cli_app, ["project", "add", "main", str(main_path)])
            print(result.stdout)
            assert result.exit_code == 0

        # Confirm main is present
        assert "main" in _project_names()

        # Add a second project
        result = runner.invoke(cli_app, ["project", "add", "new_default", str(new_default_path)])
        assert result.exit_code == 0

        # Set new_default as default (if needed)
        result = runner.invoke(cli_app, ["project", "default", "new_default"])
        assert result.exit_code == 0

        # Remove main
        result = runner.invoke(cli_app, ["project", "remove", "main"])
        if result.exit_code != 0:
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
        assert result.exit_code == 0

        # Confirm only new_default exists and main does not
        result = runner.invoke(cli_app, ["project", "list"], env=WIDE_TERMINAL_ENV)
        assert result.exit_code == 0
        names_after = _project_names()
        assert "main" not in names_after
        assert "new_default" in names_after
