"""Tests for the line renderings of the listing `bm tool` commands.

One record per line, identifier first, a count line at the end when the count is
known, notices after the payload (docs/OUTPUT_CONTRACT.md v2).  Tests mock the
MCP tool functions directly.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from basic_memory.cli.main import app as cli_app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Shared mock payloads
# ---------------------------------------------------------------------------

READ_NOTE_RESULT = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    # Real payloads keep the leading newline left by frontmatter stripping.
    "content": "\n# Test Note\n\nhello world",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

# With --frontmatter the API returns the LITERAL FILE as content
# (frontmatter block included) alongside the parsed frontmatter dict.
READ_NOTE_RESULT_WITH_FRONTMATTER = {
    "title": "Test Note",
    "permalink": "notes/test-note",
    "file_path": "notes/Test Note.md",
    "content": "---\ntitle: Test Note\ntags:\n- test\n---\n\n# Test Note\n\nhello world\n\n",
    "frontmatter": {"title": "Test Note", "tags": ["test"]},
}

READ_NOTE_NOT_FOUND_RESULT = {
    "title": None,
    "permalink": None,
    "file_path": None,
    "content": None,
    "frontmatter": None,
    "related_results": [
        {
            "title": "Related [draft] Note",
            "permalink": "notes/related-note",
            "file_path": "notes/Related Note.md",
        }
    ],
}

SEARCH_RESULT = {
    # Real SearchResponse.model_dump() uses "current_page", not "page".
    "total": 2,
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
            "matched_chunk": "A snippet about test notes",
            "content": None,
        },
        {
            "type": "observation",
            "title": "Another Note",
            "permalink": "notes/another-note",
            "file_path": "notes/Another Note.md",
            "score": 0.72,
            "matched_chunk": None,
            "content": "Full content here",
        },
    ],
}

SEARCH_RESULT_EMPTY = {
    "total": 0,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [],
}

# Search result whose title and snippet contain literal bracket expressions.
SEARCH_RESULT_BRACKETED_TITLE = {
    "total": 1,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Spec [draft] v2",
            "permalink": "specs/spec-draft-v2",
            "file_path": "specs/Spec [draft] v2.md",
            "score": 0.90,
            "matched_chunk": "An important [red] section",
            "content": None,
        },
    ],
}

# Semantic search: the count is unknown, so total is null (never a sentinel).
SEARCH_RESULT_UNKNOWN_TOTAL = {
    "total": None,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Found Note",
            "permalink": "notes/found-note",
            "file_path": "notes/Found Note.md",
            "score": 0.80,
            "matched_chunk": "some content",
            "content": None,
        },
    ],
}

SEARCH_RESULT_UNKNOWN_TOTAL_WITH_MORE = {
    **SEARCH_RESULT_UNKNOWN_TOTAL,
    "page_size": 1,
    "has_more": True,
}

SEARCH_RESULT_LONG_SNIPPET = {
    "total": 1,
    "current_page": 1,
    "page_size": 10,
    "has_more": False,
    "results": [
        {
            "type": "entity",
            "title": "Long Note",
            "permalink": "notes/long-note",
            "file_path": "notes/Long Note.md",
            "score": 0.50,
            "matched_chunk": "first line\n" + "x" * 300,
            "content": None,
        },
    ],
}

BUILD_CONTEXT_RESULT = {
    # Real GraphContext.model_dump() shape: results is a list of ContextResult dicts.
    "results": [
        {
            "primary_result": {
                "type": "entity",
                "external_id": "abc123",
                "title": "Test Note",
                "permalink": "notes/test-note",
                "file_path": "notes/Test Note.md",
                "content": "Primary note prose that must stay visible.",
                "created_at": "2025-01-01T00:00:00",
            },
            "observations": [
                {
                    "type": "observation",
                    "category": "fact",
                    "content": "This is a key fact about the test note",
                    "permalink": "notes/test-note",
                    "file_path": "notes/Test Note.md",
                    "created_at": "2025-01-01T00:00:00",
                }
            ],
            "related_results": [
                {
                    "type": "relation",
                    "title": "Related Note",
                    "permalink": "notes/related",
                    "file_path": "notes/Related Note.md",
                    "relation_type": "references",
                    "created_at": "2025-01-01T00:00:00",
                }
            ],
        }
    ],
    "metadata": {"uri": "notes/test-note", "depth": 1},
    "page": 1,
    "page_size": 10,
    "has_more": False,
}

RECENT_ACTIVITY_RESULT = [
    # Real _extract_recent_rows output keys: type/title/permalink/file_path/created_at
    # (optional: project).  No "updated_at" key in the real output.
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

RECENT_ACTIVITY_PROJECT_RESULT = [
    {**RECENT_ACTIVITY_RESULT[0], "project": "research"},
    {**RECENT_ACTIVITY_RESULT[1], "project": "work"},
]


# ---------------------------------------------------------------------------
# search-notes
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT,
)
def test_search_notes_one_line_per_result_with_count(mock_mcp):
    """Each result is permalink, score, title, snippet; the known total closes it."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "test"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "notes/test-note  0.95  Test Note  A snippet about test notes"
    # The second result has no matched_chunk, so content is the snippet.
    assert lines[1] == "notes/another-note  0.72  Another Note  Full content here"
    assert lines[2] == "2 results"
    assert len(lines) == 3


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_EMPTY,
)
def test_search_notes_empty_is_a_result(mock_mcp):
    """A well-scoped search that matched nothing is a result, not a failure."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "nothing"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "0 results\n"


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_UNKNOWN_TOTAL,
)
def test_search_notes_unknown_total_prints_no_count(mock_mcp):
    """An unknown count is omitted rather than guessed: absence is the signal."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "found"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines == ["notes/found-note  0.80  Found Note  some content"]


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_UNKNOWN_TOTAL_WITH_MORE,
)
def test_search_notes_has_more_notice_follows_the_payload(mock_mcp):
    """Pagination with an unknown count says another page exists, after the results."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "found"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0].startswith("notes/found-note")
    assert lines[-1] == "more results available"


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_UNKNOWN_TOTAL_WITH_MORE,
)
def test_search_notes_quiet_drops_the_notice(mock_mcp):
    """--quiet leaves the payload alone and drops the pagination notice."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "found", "--quiet"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "more results available" not in result.stdout
    assert "notes/found-note" in result.stdout


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_BRACKETED_TITLE,
)
def test_search_notes_brackets_survive_verbatim(mock_mcp):
    """Bracketed user text is data, not markup, and must render literally."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "spec"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "Spec [draft] v2" in result.stdout
    assert "[red]" in result.stdout


@patch(
    "basic_memory.mcp.tools.search_notes",
    new_callable=AsyncMock,
    return_value=SEARCH_RESULT_LONG_SNIPPET,
)
def test_search_notes_snippet_stays_on_one_line(mock_mcp):
    """A multi-line, oversized snippet is flattened and truncated to 120 chars."""
    result = runner.invoke(cli_app, ["tool", "search-notes", "long"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert len(lines) == 2  # one result plus the count line
    snippet = lines[0].split("Long Note  ", 1)[1]
    assert len(snippet) == 120
    assert snippet.startswith("first line x")
    assert snippet.endswith("...")


# ---------------------------------------------------------------------------
# read-note
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT,
)
def test_read_note_emits_the_body_undecorated(mock_mcp):
    """The payload is the note body: no header, no permalink line, no placeholder."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "# Test Note\n\nhello world\n"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT_WITH_FRONTMATTER,
)
def test_read_note_frontmatter_round_trips_byte_exactly(mock_mcp):
    """--frontmatter writes the literal file, so redirection round-trips it."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note", "--frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == READ_NOTE_RESULT_WITH_FRONTMATTER["content"]


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_RESULT_WITH_FRONTMATTER,
)
def test_read_note_include_frontmatter_alias(mock_mcp):
    """--include-frontmatter still works as a deprecated alias for --frontmatter."""
    result = runner.invoke(cli_app, ["tool", "read-note", "test-note", "--include-frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.startswith("---\ntitle: Test Note")


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_NOT_FOUND_RESULT,
)
def test_read_note_not_found_lists_related_identifier_first(mock_mcp):
    """A miss stays distinct from an empty note and keeps its suggestions."""
    result = runner.invoke(cli_app, ["tool", "read-note", "missing-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "Note not found."
    assert lines[1] == "Related results:"
    assert lines[2] == "notes/related-note  Related [draft] Note"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value=READ_NOTE_NOT_FOUND_RESULT,
)
def test_read_note_frontmatter_miss_emits_no_bytes(mock_mcp):
    """Byte-faithful frontmatter mode emits no bytes when no note exists."""
    result = runner.invoke(cli_app, ["tool", "read-note", "missing-note", "--frontmatter"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == ""


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value={
        "title": None,
        "permalink": None,
        "file_path": None,
        "content": None,
        "frontmatter": None,
    },
)
def test_read_note_not_found_without_suggestions(mock_mcp):
    """A miss with no fallback matches still reports a clear, successful result."""
    result = runner.invoke(cli_app, ["tool", "read-note", "missing-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == "Note not found.\nNo note or related content found.\n"


@patch(
    "basic_memory.mcp.tools.read_note",
    new_callable=AsyncMock,
    return_value={"title": "", "permalink": "", "content": "", "frontmatter": {}},
)
def test_read_note_empty_note_prints_nothing(mock_mcp):
    """An empty note is empty output — no placeholder text to mistake for content."""
    result = runner.invoke(cli_app, ["tool", "read-note", "empty-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# build-context
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.build_context",
    new_callable=AsyncMock,
    return_value=BUILD_CONTEXT_RESULT,
)
def test_build_context_outline_with_counts(mock_mcp):
    """Primary records lead their indented observations and related items."""
    result = runner.invoke(cli_app, ["tool", "build-context", "memory://notes/test-note"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "Context: notes/test-note"
    assert lines[1] == "notes/test-note  entity  Test Note"
    assert lines[2] == "  Primary note prose that must stay visible."
    # Literal brackets are data here: the category prefix must survive verbatim.
    assert lines[3] == "  [fact] This is a key fact about the test note"
    assert lines[4] == "  references relation Related Note"
    assert lines[5] == "1 primary, 1 observations, 1 related"


# ---------------------------------------------------------------------------
# recent-activity
# ---------------------------------------------------------------------------


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_RESULT,
)
def test_recent_activity_one_line_per_item_with_count(mock_mcp):
    """Each item is permalink, type, title, timestamp, closed by the count."""
    result = runner.invoke(cli_app, ["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "notes/note-a  entity  Note A  2025-01-01 00:00:00"
    assert lines[1] == "notes/note-b  entity  Note B  2025-01-02 00:00:00"
    assert lines[2] == "2 results"


@patch(
    "basic_memory.mcp.tools.recent_activity",
    new_callable=AsyncMock,
    return_value=RECENT_ACTIVITY_PROJECT_RESULT,
)
def test_recent_activity_adds_project_column_when_present(mock_mcp):
    """Cross-project activity stays attributable through a trailing column."""
    result = runner.invoke(cli_app, ["tool", "recent-activity"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0].endswith("  research")
    assert lines[1].endswith("  work")
