"""Integration tests for `basic-memory tool delete-note` (output contract v2).

delete-note renders a single record as labelled `key: value` lines, identifier
first. The v2 rendering carries the fields a caller acts on; the per-file list
a directory delete used to emit in JSON is not part of it, so the tests check
the filesystem for that instead.
"""

import re
from pathlib import Path

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


def _write_note(
    title: str,
    folder: str,
    content: str,
    *,
    project: str | None = None,
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
    if project is not None:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return _record(result.stdout)


def _read_note(identifier: str, *, project: str | None = None) -> str:
    """Return read-note's payload — the note body, written verbatim."""
    args = ["tool", "read-note", identifier]
    if project is not None:
        args.extend(["--project", project])

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    return result.stdout


def _delete_note(
    identifier: str,
    *,
    is_directory: bool = False,
    project: str | None = None,
    project_id: str | None = None,
) -> tuple[int, dict[str, str], str]:
    args = ["tool", "delete-note", identifier]
    if is_directory:
        args.append("--is-directory")
    if project is not None:
        args.extend(["--project", project])
    if project_id is not None:
        args.extend(["--project-id", project_id])

    result = runner.invoke(cli_app, args)
    return result.exit_code, _record(result.stdout), result.output


def _search_rows(
    query: str,
    *,
    mode_flag: str | None = None,
    page_size: int = 20,
) -> tuple[list[str], str]:
    """Return search-notes payload rows and its trailing count line."""
    args = ["tool", "search-notes", query, "--page-size", str(page_size)]
    if mode_flag is not None:
        args.append(mode_flag)

    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, "search-notes wrote nothing to stdout"
    count_line = next(line for line in lines if COUNT_LINE.match(line))
    return lines[: lines.index(count_line)], count_line


def _project_file(test_project, file_path: str) -> Path:
    return Path(test_project.path) / file_path


def test_delete_note_removes_file_database_record_and_search_result(
    app, app_config, test_project, config_manager
) -> None:
    """Single-note deletion removes the note from every user-visible surface."""
    note = _write_note(
        "CLI Delete Single Note",
        "delete-cli",
        "# CLI Delete Single Note\n\nUniqueSingleDeleteToken\n\n- [status] ready to delete",
    )
    note_path = _project_file(test_project, note["file_path"])
    assert note_path.exists()

    exit_code, record, output = _delete_note(note["permalink"])

    assert exit_code == 0, output
    assert record == {
        "permalink": note["permalink"],
        "file_path": note["file_path"],
        "title": "CLI Delete Single Note",
        "deleted": "true",
    }
    assert not note_path.exists()

    assert "Note not found." in _read_note(note["permalink"])

    rows, count_line = _search_rows("CLI Delete Single Note", mode_flag="--title")
    assert rows == []
    assert count_line == "0 results"


def test_delete_note_not_found_is_a_result_not_a_failure(
    app, app_config, test_project, config_manager
) -> None:
    """A missing note reports `deleted: false` and does not produce a CLI failure."""
    exit_code, record, output = _delete_note("delete-cli/missing-note")

    assert exit_code == 0, output
    # Absent fields are skipped by the renderer, so nothing survives but the verdict.
    assert record == {"deleted": "false"}


def test_delete_note_case_mismatch_does_not_delete_exact_note(
    app, app_config, test_project, config_manager
) -> None:
    """Strict CLI deletes must not fuzzy-match a differently cased title."""
    note = _write_note(
        "CLI CamelCase Delete Note",
        "delete-cli",
        "# CLI CamelCase Delete Note\n\nCaseSensitiveDeleteToken",
    )

    exit_code, record, output = _delete_note("cli camelcase delete note")

    assert exit_code == 0, output
    assert record["deleted"] == "false"
    still_there = _read_note(note["permalink"])
    assert "CaseSensitiveDeleteToken" in still_there


def test_delete_note_project_id_takes_precedence_over_wrong_project_name(
    app, app_config, test_project, config_manager
) -> None:
    """CLI `--project-id` routes destructive operations to the exact project."""
    note = _write_note(
        "CLI Delete By Project ID",
        "delete-cli",
        "# CLI Delete By Project ID\n\nProjectIdDeleteToken",
    )

    exit_code, record, output = _delete_note(
        note["file_path"],
        project="not-the-test-project",
        project_id=test_project.external_id,
    )

    assert exit_code == 0, output
    assert record["deleted"] == "true"
    assert record["title"] == "CLI Delete By Project ID"
    assert "Note not found." in _read_note(note["permalink"])


def test_delete_note_memory_url_detects_project_from_identifier(
    app, app_config, test_project, config_manager
) -> None:
    """A memory:// URL can select the project without a separate --project flag."""
    note = _write_note(
        "CLI Delete Memory URL",
        "delete-cli",
        "# CLI Delete Memory URL\n\nMemoryUrlDeleteToken",
        project=test_project.name,
    )
    memory_url = f"memory://{test_project.name}/{note['permalink']}"

    exit_code, record, output = _delete_note(memory_url)

    assert exit_code == 0, output
    assert record["deleted"] == "true"
    assert record["permalink"] == note["permalink"]
    assert "Note not found." in _read_note(note["permalink"], project=test_project.name)


def test_delete_directory_removes_nested_files_database_records_and_search_results(
    app, app_config, test_project, config_manager
) -> None:
    """Directory deletion removes nested notes and reports a complete summary record."""
    notes = [
        _write_note(
            "CLI Delete Directory Root",
            "delete-cli-dir",
            "# CLI Delete Directory Root\n\nDirectoryDeleteTokenRoot",
        ),
        _write_note(
            "CLI Delete Directory Child",
            "delete-cli-dir/child",
            "# CLI Delete Directory Child\n\nDirectoryDeleteTokenChild",
        ),
        _write_note(
            "CLI Delete Directory Deep Child",
            "delete-cli-dir/child/deep",
            "# CLI Delete Directory Deep Child\n\nDirectoryDeleteTokenDeep",
        ),
    ]
    note_paths = [_project_file(test_project, note["file_path"]) for note in notes]
    assert all(path.exists() for path in note_paths)

    exit_code, record, output = _delete_note("delete-cli-dir", is_directory=True)

    assert exit_code == 0, output
    assert record["identifier"] == "delete-cli-dir"
    assert record["is_directory"] == "true"
    assert record["deleted"] == "true"
    assert record["total_files"] == "3"
    assert record["successful_deletes"] == "3"
    assert record["failed_deletes"] == "0"

    # The per-file list is not part of the v2 record, so the filesystem is the
    # evidence that every nested note went away.
    assert not any(path.exists() for path in note_paths)

    for note in notes:
        assert "Note not found." in _read_note(note["permalink"])

    rows, count_line = _search_rows("CLI Delete Directory", mode_flag="--title")
    assert rows == []
    assert count_line == "0 results"


def test_delete_directory_without_flag_does_not_delete_child_notes(
    app, app_config, test_project, config_manager
) -> None:
    """The CLI must not treat a directory path as destructive without --is-directory."""
    note = _write_note(
        "CLI Delete Directory Safety",
        "delete-cli-safety",
        "# CLI Delete Directory Safety\n\nDirectorySafetyToken",
    )

    exit_code, record, output = _delete_note("delete-cli-safety")

    assert exit_code == 0, output
    assert record["deleted"] == "false"
    assert "DirectorySafetyToken" in _read_note(note["permalink"])
    assert _project_file(test_project, note["file_path"]).exists()
