"""The per-project headline file (GAPS W9 item D, revised by GAPS U24).

The headline is composed by `set_headline`, never derived, so the tests drive
set/clear/read against a real file. The file's *bytes and mtime* are the
assertions — the three consumer scripts read line 1, line 2, and the mtime, and
each of those has already failed in practice.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from basic_memory.models import Project
from basic_memory.services.headline import (
    MAX_HEADLINE_CHARS,
    HeadlineError,
    check_headline,
    clear_headline,
    headline_path,
    read_headline,
    set_headline,
)

# An mtime far enough in the past that no filesystem timestamp resolution can
# confuse "untouched" with "rewritten in the same tick".
STALE_MTIME = 1_600_000_000


# --- What a set writes ---


@pytest.mark.asyncio
async def test_set_writes_the_three_lines_every_consumer_parses(test_project: Project) -> None:
    """The statusline checks line 1 is `---`; two other scripts read line 2 raw."""
    assert set_headline(test_project.external_id, "Ship the verbs") is True

    lines = headline_path(test_project.external_id).read_text(encoding="utf-8").splitlines()
    assert lines == ["---", "headline: Ship the verbs", "---"]


@pytest.mark.asyncio
async def test_set_strips_surrounding_whitespace_but_nothing_else(test_project: Project) -> None:
    """Edge whitespace renders identically, so it is forgiven rather than refused."""
    set_headline(test_project.external_id, "  Ship the verbs ")

    assert read_headline(test_project.external_id) == "Ship the verbs"


@pytest.mark.asyncio
async def test_headline_path_sits_in_the_store(test_project: Project) -> None:
    """Decision D6: the file lives in the store, never beside a working dir's marker."""
    from basic_memory.store.history import store_path

    assert headline_path(test_project.external_id) == (
        store_path() / test_project.external_id / "headline.md"
    )
    assert Path(test_project.path) != store_path()


# --- Validation: over-limit is an error, never a truncation (GAPS U24) ---


@pytest.mark.asyncio
async def test_a_headline_at_the_limit_is_accepted(test_project: Project) -> None:
    exactly = "x" * MAX_HEADLINE_CHARS
    assert set_headline(test_project.external_id, exactly) is True
    assert read_headline(test_project.external_id) == exactly


@pytest.mark.asyncio
async def test_a_headline_over_the_limit_is_refused_not_truncated(test_project: Project) -> None:
    """The 30-char cut is what made derived headlines mush; a composed one is refused."""
    over = "x" * (MAX_HEADLINE_CHARS + 1)
    with pytest.raises(HeadlineError, match="31 chars"):
        set_headline(test_project.external_id, over)
    assert not headline_path(test_project.external_id).exists()


@pytest.mark.asyncio
async def test_an_empty_headline_is_refused_and_names_the_clear_shape() -> None:
    """`bm headline ""` clears; a blank *set* is a mistake, and the message says which."""
    with pytest.raises(HeadlineError, match="clears it"):
        check_headline("   ")


@pytest.mark.asyncio
async def test_a_multi_line_headline_is_refused() -> None:
    """Line 2 of the file is the whole payload; a newline would corrupt every parser."""
    with pytest.raises(HeadlineError, match="one line"):
        check_headline("what is\nnext")


# --- When it writes, and when it must not ---


@pytest.mark.asyncio
async def test_setting_the_same_headline_twice_leaves_mtime_alone(
    test_project: Project,
) -> None:
    """W9's mtime trap: the overview script reads mtime as its staleness signal."""
    set_headline(test_project.external_id, "Ship the verbs")
    path = headline_path(test_project.external_id)
    os.utime(path, (STALE_MTIME, STALE_MTIME))

    assert set_headline(test_project.external_id, "Ship the verbs") is False
    assert path.stat().st_mtime == STALE_MTIME


@pytest.mark.asyncio
async def test_a_real_change_does_move_the_mtime(test_project: Project) -> None:
    """Positive control for the test above: the skip is conditional, not total."""
    set_headline(test_project.external_id, "Ship the verbs")
    path = headline_path(test_project.external_id)
    os.utime(path, (STALE_MTIME, STALE_MTIME))

    assert set_headline(test_project.external_id, "Cut over the statusline") is True
    assert path.stat().st_mtime != STALE_MTIME
    assert read_headline(test_project.external_id) == "Cut over the statusline"


@pytest.mark.asyncio
async def test_clear_removes_the_file(test_project: Project) -> None:
    """An empty headline would render a blank bar; absence lets consumers fall back."""
    set_headline(test_project.external_id, "Ship the verbs")

    assert clear_headline(test_project.external_id) is True
    assert not headline_path(test_project.external_id).exists()


@pytest.mark.asyncio
async def test_clearing_an_absent_headline_reports_no_change(test_project: Project) -> None:
    """False is what tells the verb there is nothing to commit."""
    assert clear_headline(test_project.external_id) is False


# --- Reading ---


@pytest.mark.asyncio
async def test_read_returns_what_set_wrote(test_project: Project) -> None:
    set_headline(test_project.external_id, "Cut over the statusline")
    assert read_headline(test_project.external_id) == "Cut over the statusline"


@pytest.mark.asyncio
async def test_read_reports_a_missing_file_as_unset(test_project: Project) -> None:
    assert read_headline(test_project.external_id) is None


@pytest.mark.asyncio
async def test_read_reports_a_malformed_file_as_unset(test_project: Project) -> None:
    """Every caller is composing a hint; none of them can act on a parse error."""
    path = headline_path(test_project.external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not: the shape\n", encoding="utf-8")

    assert read_headline(test_project.external_id) is None
