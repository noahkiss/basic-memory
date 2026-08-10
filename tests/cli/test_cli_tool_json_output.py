"""Tests for `bm tool` single-record commands and argument passthrough.

Each verb has exactly one rendering (docs/OUTPUT_CONTRACT.md v2): labelled
`key: value` lines with the identifier first, errors on stderr with exit 1 and
nothing on stdout.  Tests mock the MCP tool functions directly.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# --- Shared mock data ---

WRITE_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "checksum": "abc123",
    "action": "created",
}

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "content": "# Test Note\n\nhello world",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

EDIT_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "checksum": "def456",
    "operation": "append",
    "fileCreated": False,
}

DELETE_NOTE_RESULT = {
    "deleted": True,
    "is_directory": False,
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
}

DELETE_DIRECTORY_RESULT = {
    "deleted": True,
    "is_directory": True,
    "identifier": "notes/archive",
    "total_files": 3,
    "successful_deletes": 3,
    "failed_deletes": 0,
}

BUILD_CONTEXT_RESULT = {
    "results": [],
    "metadata": {"uri": "test/topic", "depth": 1},
    "page": 1,
    "page_size": 10,
}

RECENT_ACTIVITY_RESULT = [
    {
        "type": "entity",
        "title": "Note A",
        "permalink": "notes/note-a",
        "file_path": "notes/Note A.md",
        "created_at": "2025-01-01 00:00:00",
    },
    {
        "type": "entity",
        "title": "Note B",
        "permalink": "notes/note-b",
        "file_path": "notes/Note B.md",
        "created_at": "2025-01-02 00:00:00",
    },
]

SEARCH_RESULT = {
    "total": 1,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Test Note",
            "permalink": "notes/test-note",
            "file_path": "notes/Test Note.md",
            "score": 0.95,
            "matched_chunk": "a snippet",
        }
    ],
}


# --- write-note ---


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_renders_identifier_first_record(mock_mcp_write):
    """write-note renders labelled lines, permalink first, without the checksum."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello world",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "permalink: notes/test-note"
    assert "file_path: notes/Test Note.md" in lines
    assert "title: Test Note" in lines
    assert "action: created" in lines
    # Internal bookkeeping an agent cannot act on stays out of the payload.
    assert "checksum" not in result.stdout
    assert "{" not in result.stdout
    mock_mcp_write.assert_called_once()
    assert mock_mcp_write.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value={**WRITE_NOTE_RESULT, "action": "conflict", "error": "NOTE_ALREADY_EXISTS"},
)
def test_write_note_error_writes_nothing_to_stdout(mock_mcp_write):
    """A failed write reports on stderr with exit 1 and leaves stdout empty."""
    result = runner.invoke(
        cli_app,
        ["tool", "write-note", "--title", "Test Note", "--folder", "notes", "--content", "x"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "NOTE_ALREADY_EXISTS" in result.stderr


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_project_id_passthrough(mock_mcp_write):
    """--project-id forwards to the MCP tool's project_id parameter.

    The external id is the only unambiguous project handle: names collide and
    are re-usable, so a caller that knows the id must be able to pass it.
    """
    uuid = "11111111-1111-1111-1111-111111111111"
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello",
            "--project-id",
            uuid,
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_write.call_args.kwargs["project_id"] == uuid


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_with_tags(mock_mcp_write):
    """write-note passes tags through to MCP tool."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello",
            "--tags",
            "python",
            "--tags",
            "async",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_write.call_args.kwargs["tags"] == ["python", "async"]


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_type_passthrough(mock_mcp_write):
    """--type forwards to the MCP tool's note_type parameter."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello",
            "--type",
            "guide",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_write.call_args.kwargs["note_type"] == "guide"


@patch(
    "basic_memory.mcp.tools.write_note",
    new_callable=AsyncMock,
    return_value=WRITE_NOTE_RESULT,
)
def test_write_note_type_defaults_to_note(mock_mcp_write):
    """write-note defaults note_type to 'note' when --type is omitted."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "write-note",
            "--title",
            "Test Note",
            "--folder",
            "notes",
            "--content",
            "hello",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_write.call_args.kwargs["note_type"] == "note"


# --- read-note ---


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_writes_content_only(mock_mcp_read):
    """read-note writes the note body and nothing else."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "# Test Note\n\nhello world\n"
    mock_mcp_read.assert_called_once()
    assert mock_mcp_read.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_include_frontmatter_passthrough(mock_mcp_read):
    """read-note --include-frontmatter passes the flag through to the MCP tool."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note", "--include-frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_read.call_args.kwargs["include_frontmatter"] is True


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value={
        "title": None,
        "permalink": None,
        "content": None,
        "error": "SECURITY_VALIDATION_ERROR",
    },
)
def test_read_note_error_writes_nothing_to_stdout(mock_mcp_read):
    """A blocked read reports on stderr with exit 1 and leaves stdout empty."""
    result = runner.invoke(cli_app, ["tool", "read-note", "../../etc/passwd"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "SECURITY_VALIDATION_ERROR" in result.stderr


# --- delete-note ---


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value=DELETE_NOTE_RESULT,
)
def test_delete_note_renders_identifier_first_record(mock_mcp_delete: AsyncMock) -> None:
    """delete-note renders labelled lines with the permalink first."""
    result = runner.invoke(cli_app, ["tool", "delete-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "permalink: notes/test-note"
    assert "deleted: true" in lines
    assert "{" not in result.stdout
    mock_mcp_delete.assert_called_once()
    assert mock_mcp_delete.call_args.kwargs["output_format"] == "json"
    assert mock_mcp_delete.call_args.kwargs["is_directory"] is False


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value=DELETE_DIRECTORY_RESULT,
)
def test_delete_note_directory_record(mock_mcp_delete: AsyncMock) -> None:
    """delete-note --is-directory reports the directory counts."""
    result = runner.invoke(cli_app, ["tool", "delete-note", "notes/archive", "--is-directory"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "identifier: notes/archive"
    assert "is_directory: true" in lines
    assert "successful_deletes: 3" in lines
    assert mock_mcp_delete.call_args.kwargs["is_directory"] is True


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value={
        "deleted": False,
        "is_directory": False,
        "identifier": "missing-note",
        "error": None,
    },
)
def test_delete_note_not_found_is_a_result(mock_mcp_delete: AsyncMock) -> None:
    """A not-found delete is a result, not a failure: exit 0 with deleted: false."""
    result = runner.invoke(cli_app, ["tool", "delete-note", "missing-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "identifier: missing-note"
    assert "deleted: false" in lines


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value={
        "deleted": False,
        "is_directory": False,
        "identifier": "test-note",
        "error": "Delete failed",
    },
)
def test_delete_note_error_writes_nothing_to_stdout(mock_mcp_delete: AsyncMock) -> None:
    """delete-note reports the MCP error on stderr and exits 1."""
    result = runner.invoke(cli_app, ["tool", "delete-note", "test-note"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Error: Delete failed" in result.stderr


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value={
        "deleted": False,
        "is_directory": True,
        "identifier": "notes/archive",
        "total_files": 3,
        "successful_deletes": 2,
        "failed_deletes": 1,
        "errors": [{"path": "notes/archive/locked.md", "error": "permission denied"}],
    },
)
def test_delete_note_directory_partial_failure_exits_nonzero(
    mock_mcp_delete: AsyncMock,
) -> None:
    """delete-note --is-directory exits 1 when any directory file remains undeleted."""
    result = runner.invoke(cli_app, ["tool", "delete-note", "notes/archive", "--is-directory"])

    assert result.exit_code == 1
    assert "Error: Directory delete incomplete: 1 file(s) failed" in result.stderr
    assert result.stdout == ""


@patch(
    "basic_memory.mcp.tools.delete_note",
    new_callable=AsyncMock,
    return_value=DELETE_NOTE_RESULT,
)
def test_delete_note_project_id_passthrough(mock_mcp_delete: AsyncMock) -> None:
    """--project-id forwards to the MCP tool's project_id parameter."""
    uuid = "11111111-1111-1111-1111-111111111111"
    result = runner.invoke(cli_app, ["tool", "delete-note", "test-note", "--project-id", uuid])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_delete.call_args.kwargs["project_id"] == uuid


# --- edit-note ---


@patch(
    "basic_memory.mcp.tools.edit_note",
    new_callable=AsyncMock,
    return_value=EDIT_NOTE_RESULT,
)
def test_edit_note_renders_identifier_first_record(mock_mcp_edit):
    """edit-note renders labelled lines naming the operation, without the checksum."""
    result = runner.invoke(
        cli_app,
        ["tool", "edit-note", "test-note", "--operation", "append", "--content", "new content"],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "permalink: notes/test-note"
    assert "operation: append" in lines
    assert "file_created: false" in lines
    assert "checksum" not in result.stdout
    mock_mcp_edit.assert_called_once()
    assert mock_mcp_edit.call_args.kwargs["output_format"] == "json"
    assert mock_mcp_edit.call_args.kwargs["replace_subsections"] is True


@patch(
    "basic_memory.mcp.tools.edit_note",
    new_callable=AsyncMock,
    return_value=EDIT_NOTE_RESULT,
)
def test_edit_note_no_replace_subsections_passthrough(mock_mcp_edit):
    """--no-replace-subsections forwards the conservative section boundary mode."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "edit-note",
            "test-note",
            "--operation",
            "replace_section",
            "--section",
            "## Notes",
            "--content",
            "new content",
            "--no-replace-subsections",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_edit.call_args.kwargs["replace_subsections"] is False


@patch(
    "basic_memory.mcp.tools.edit_note",
    new_callable=AsyncMock,
    return_value={"title": "Test", "permalink": "test", "error": "Edit failed: not found"},
)
def test_edit_note_error_writes_nothing_to_stdout(mock_mcp_edit):
    """edit-note reports the MCP error on stderr and exits 1."""
    result = runner.invoke(
        cli_app,
        ["tool", "edit-note", "test-note", "--operation", "append", "--content", "content"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Edit failed: not found" in result.stderr


# --- build-context ---


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_empty_is_a_result(mock_build_ctx):
    """An empty context still prints the uri and a zero count, exit 0."""
    result = runner.invoke(cli_app, ["tool", "build-context", "memory://test/topic"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "Context: test/topic\n0 primary, 0 observations, 0 related\n"
    mock_build_ctx.assert_called_once()
    assert mock_build_ctx.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_with_options(mock_build_ctx):
    """build-context passes depth, timeframe, pagination through."""
    result = runner.invoke(
        cli_app,
        [
            "tool",
            "build-context",
            "memory://test/topic",
            "--depth",
            "2",
            "--timeframe",
            "30d",
            "--page",
            "3",
            "--max-related",
            "5",
        ],
    )

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    kwargs = mock_build_ctx.call_args.kwargs
    assert kwargs["depth"] == 2
    assert kwargs["timeframe"] == "30d"
    assert kwargs["page"] == 3
    assert kwargs["max_related"] == 5


# --- recent-activity ---


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_pagination(mock_mcp_recent):
    """recent-activity passes --page and --page-size through."""
    result = runner.invoke(cli_app, ["tool", "recent-activity", "--page", "2", "--page-size", "10"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    kwargs = mock_mcp_recent.call_args.kwargs
    assert kwargs["page"] == 2
    assert kwargs["page_size"] == 10
    assert mock_mcp_recent.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=[],
)
def test_recent_activity_empty_is_a_result(mock_mcp_recent):
    """No recent activity is a result, not a failure."""
    result = runner.invoke(cli_app, ["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "0 results\n"


# --- search-notes ---


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_with_meta_filter(mock_mcp_search):
    """search-notes --meta key=value builds metadata filters."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "query", "--meta", "status=draft"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_search.call_args.kwargs["metadata_filters"] == {"status": "draft"}
    assert mock_mcp_search.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_permalink_mode(mock_mcp_search):
    """search-notes --permalink sets search_type."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "specs/*", "--permalink"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp_search.call_args.kwargs["search_type"] == "permalink"


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_rejects_two_mode_flags(mock_mcp_search):
    """Contradictory retrieval modes cannot be scoped, so they are a failure."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "q", "--permalink", "--title"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "only one mode flag" in result.stderr
    mock_mcp_search.assert_not_called()


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_rejects_malformed_meta(mock_mcp_search):
    """A --meta entry without '=' cannot be interpreted, so it is a failure."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "q", "--meta", "status"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "key=value" in result.stderr


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value="Error: search failed",
)
def test_search_notes_string_error(mock_mcp_search):
    """A text MCP response is an error message, not a payload."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "query"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "search failed" in result.stderr


# --- list-projects ---

LIST_PROJECTS_RESULT = {
    "projects": [
        {
            "name": "main",
            "external_id": "11111111-1111-1111-1111-111111111111",
            "path": "/notes/main",
            "is_default": True,
        },
        {
            "name": "research",
            "external_id": "22222222-2222-2222-2222-222222222222",
            "path": "/notes/research",
            "is_default": False,
        },
    ],
    "default_project": "main",
    "constrained_project": None,
}


@patch(
    "basic_memory.mcp.tools.list_memory_projects",
    new_callable=AsyncMock,
    return_value=LIST_PROJECTS_RESULT,
)
def test_list_projects_renders_one_line_per_project(mock_mcp):
    """list-projects renders name, path, a default marker, and a count."""
    result = runner.invoke(cli_app, ["tool", "list-projects"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "main  /notes/main  (default)"
    assert lines[1] == "research  /notes/research"
    assert lines[2] == "2 projects"
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch(
    "basic_memory.mcp.tools.list_memory_projects",
    new_callable=AsyncMock,
    return_value={"projects": [], "default_project": None, "constrained_project": None},
)
def test_list_projects_empty_is_a_result(mock_mcp):
    """No projects is a result, not a failure."""
    result = runner.invoke(cli_app, ["tool", "list-projects"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "0 projects\n"
