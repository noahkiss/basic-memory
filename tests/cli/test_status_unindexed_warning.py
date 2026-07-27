"""`bm status` must say out loud when observed files are not indexed (GAPS.md T2).

The count alone cannot be trusted: a file the scan observed but never indexed is absent
from search and read, so a bare "N observed files" reads as a clean corpus while every
query against those files misses.
"""

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from basic_memory.cli.commands import status as status_command
from basic_memory.schemas import ProjectIndexStatusResponse
from basic_memory.schemas.project_index import ProjectIndexObservedFileResponse


def _status(*indexed_flags: bool) -> ProjectIndexStatusResponse:
    observed_files = tuple(
        ProjectIndexObservedFileResponse(
            path=f"notes/note-{position}.md",
            checksum="0" * 64,
            size=42,
            indexed=indexed,
        )
        for position, indexed in enumerate(indexed_flags)
    )
    return ProjectIndexStatusResponse(
        total_files=len(observed_files),
        unindexed_file_count=sum(1 for indexed in indexed_flags if not indexed),
        observed_files=observed_files,
    )


def _render(status: ProjectIndexStatusResponse, verbose: bool = True) -> str:
    # A wide, non-color console keeps rich from wrapping the warning mid-sentence, so the
    # assertions test the message rather than the terminal width the test happens to run at.
    buffer = StringIO()
    console = Console(file=buffer, width=200, no_color=True)
    with patch.object(status_command, "console", console):
        status_command.display_project_index_status(
            "q3test", "Project Index", status, verbose=verbose
        )
    return buffer.getvalue()


def test_status_warns_about_unindexed_observed_files():
    """An unindexed file must be counted separately and point at the fix."""
    output = _render(_status(True, False, False))

    assert "3 observed files" in output
    assert "2 observed files are NOT indexed" in output
    assert "invisible to search and read" in output
    assert "basic-memory reindex" in output


def test_status_marks_the_unindexed_file_in_the_verbose_listing():
    """The verbose listing must name which file is unreachable, not just how many."""
    output = _render(_status(True, False))

    assert "note-1.md" in output
    assert "not indexed" in output


def test_status_stays_quiet_when_everything_is_indexed():
    """No warning when the observation and the index agree — the flag must mean something."""
    output = _render(_status(True, True))

    assert "2 observed files" in output
    assert "NOT indexed" not in output


def test_status_warning_is_singular_for_one_unindexed_file():
    output = _render(_status(False))

    assert "1 observed file is NOT indexed" in output
