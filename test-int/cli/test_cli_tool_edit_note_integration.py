"""Integration tests for `basic-memory tool edit-note` (output contract v2).

edit-note renders one labelled `key: value` line per field, identifier first,
and reports failures on stderr with exit 1.
"""

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()


def _record(stdout: str) -> dict[str, str]:
    """Parse labelled `key: value` lines into a dict."""
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            fields[key] = value
    return fields


def _write_note(
    title: str, folder: str, content: str, project: str | None = None
) -> dict[str, str]:
    args = [
        "tool",
        "write-note",
        "--title",
        title,
        "--folder",
        folder,
        "--content",
        content,
    ]
    if project:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return _record(result.stdout)


def _read_note(identifier: str, project: str | None = None) -> str:
    """Return read-note's payload — the note body, written verbatim."""
    args = ["tool", "read-note", identifier]
    if project:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_edit_note_append_success(app, app_config, test_project, config_manager):
    """append operation adds content to the end of the note."""
    note = _write_note(
        "Edit Append Note",
        "edit-tests",
        "# Append\n\nBASE_APPEND_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nAPPENDED_MARKER",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert updated.index("APPENDED_MARKER") > updated.index("BASE_APPEND_MARKER")


def test_edit_note_prepend_success(app, app_config, test_project, config_manager):
    """prepend operation inserts content before existing body content."""
    note = _write_note(
        "Edit Prepend Note",
        "edit-tests",
        "# Prepend\n\nBASE_PREPEND_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "prepend",
            "--content",
            "PREPENDED_MARKER\n",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert updated.index("PREPENDED_MARKER") < updated.index("BASE_PREPEND_MARKER")


def test_edit_note_find_replace_success_with_expected_count(
    app, app_config, test_project, config_manager
):
    """find_replace succeeds when expected replacement count matches actual count."""
    note = _write_note(
        "Edit Replace Note",
        "edit-tests",
        "# Replace\n\nFIND_ME_MARKER and FIND_ME_MARKER",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--content",
            "REPLACED_MARKER",
            "--find-text",
            "FIND_ME_MARKER",
            "--expected-replacements",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert "FIND_ME_MARKER" not in updated
    assert updated.count("REPLACED_MARKER") == 2


def test_edit_note_find_replace_fails_without_find_text(
    app, app_config, test_project, config_manager
):
    """find_replace requires --find-text; the complaint goes to stderr, exit 1."""
    note = _write_note(
        "Edit Missing Find Note",
        "edit-tests",
        "# Missing Find\n\nOriginal",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--content",
            "Replacement",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "find_text parameter is required for find_replace operation" in result.stderr
    assert "permalink: " not in result.stdout


def test_edit_note_replace_section_success(app, app_config, test_project, config_manager):
    """replace_section updates exactly the targeted section body."""
    note = _write_note(
        "Edit Section Note",
        "edit-tests",
        "# Header\n\n## Keep\nKeep body\n\n## Target Section\nOld section body\n\n## After\nAfter body",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "replace_section",
            "--content",
            "New section body",
            "--section",
            "## Target Section",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = _read_note(note["permalink"])
    assert "New section body" in updated
    assert "Old section body" not in updated
    assert "## After" in updated


def test_edit_note_replace_section_fails_without_section(
    app, app_config, test_project, config_manager
):
    """replace_section requires --section; the complaint goes to stderr, exit 1."""
    note = _write_note(
        "Edit Missing Section Note",
        "edit-tests",
        "# Missing Section\n\nBody",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "replace_section",
            "--content",
            "Replacement body",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "section parameter is required for section-based operations" in result.stderr
    assert "permalink: " not in result.stdout


def test_edit_note_append_creates_nonexistent_note_cli(
    app, app_config, test_project, config_manager
):
    """append to a non-existent note via CLI should auto-create and say so."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            "cli-tests/auto-created-note",
            "--operation",
            "append",
            "--content",
            "# Auto Created\n\nCreated via CLI append.",
        ],
    )

    assert result.exit_code == 0, result.output
    record = _record(result.stdout)
    assert record["file_created"] == "true"
    assert record["operation"] == "append"
    assert record["title"]

    # Verify the note is readable
    assert "Auto Created" in _read_note(record["permalink"])


def test_edit_note_record_line_contract(app, app_config, test_project, config_manager):
    """The record carries exactly the fields a caller acts on, identifier first."""
    note = _write_note(
        "Edit JSON Note",
        "edit-tests",
        "# JSON\n\nBody",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nEDIT_MARKER",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("permalink: ")

    record = _record(result.stdout)
    # checksum carries no meaning for a CLI caller and is deliberately absent.
    assert set(record) == {"permalink", "file_path", "title", "operation", "file_created"}
    assert record["operation"] == "append"
    assert record["file_created"] == "false"
    assert record["title"] == "Edit JSON Note"


def test_edit_note_backend_failure_returns_nonzero(app, app_config, test_project, config_manager):
    """Edit should fail on stderr when the backend edit operation fails."""
    note = _write_note(
        "Edit Backend Failure Note",
        "edit-tests",
        "# Failure\n\nGamma",
    )

    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "find_replace",
            "--find-text",
            "Gamma",
            "--content",
            "Delta",
            "--expected-replacements",
            "2",
        ],
    )

    assert result.exit_code == 1, result.output
    assert result.stderr.strip()
    assert "permalink: " not in result.stdout


def test_edit_note_project_flag(app, app_config, test_project, config_manager):
    """edit-note applies the edit to the project named by --project."""
    note = _write_note(
        "Edit Project Flag Note",
        "edit-tests",
        "# Project Flag\n\nPROJECT_FLAG_MARKER",
        project=test_project.name,
    )

    success = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            note["permalink"],
            "--operation",
            "append",
            "--content",
            "\nPROJECT_UPDATE_MARKER",
            "--project",
            test_project.name,
        ],
    )
    assert success.exit_code == 0, success.output
    assert "No such option" not in success.output

    assert "PROJECT_UPDATE_MARKER" in _read_note(note["permalink"], project=test_project.name)
