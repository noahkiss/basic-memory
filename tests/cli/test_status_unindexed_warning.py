"""`bm status` must say out loud when observed files are not indexed (GAPS.md T2).

The count alone cannot be trusted: a file the scan observed but never indexed is absent
from search and read, so a bare "N observed files" reads as a clean corpus while every
query against those files misses.
"""

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


def _render(capsys, status: ProjectIndexStatusResponse, verbose: bool = True, quiet: bool = False):
    status_command.display_project_index_status("q3test", status, verbose=verbose, quiet=quiet)
    return capsys.readouterr().out


def test_status_warns_about_unindexed_observed_files(capsys):
    """An unindexed file must be counted separately and point at the fix."""
    output = _render(capsys, _status(True, False, False))

    assert "total files: 3" in output
    assert "unindexed files: 2" in output
    assert "2 files not indexed" in output
    assert "invisible to search and read" in output
    assert "Run 'basic-memory reindex' to index them." in output


def test_status_marks_the_unindexed_file_in_the_verbose_listing(capsys):
    """The verbose listing must name which file is unreachable, not just how many."""
    output = _render(capsys, _status(True, False))

    assert "notes/note-1.md  00000000 not indexed" in output
    assert "notes/note-0.md  00000000" in output


def test_status_stays_quiet_when_everything_is_indexed(capsys):
    """No warning when the observation and the index agree — the flag must mean something."""
    output = _render(capsys, _status(True, True))

    assert "total files: 2" in output
    assert "not indexed" not in output


def test_status_warning_is_singular_for_one_unindexed_file(capsys):
    output = _render(capsys, _status(False))

    assert "1 file not indexed" in output


def test_quiet_drops_the_warning_but_keeps_the_counts(capsys):
    """--quiet removes notices and affordances; the payload stays whole."""
    output = _render(capsys, _status(True, False), verbose=False, quiet=True)

    assert "unindexed files: 1" in output
    assert "Run 'basic-memory reindex'" not in output
