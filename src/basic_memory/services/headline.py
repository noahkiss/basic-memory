"""The per-project headline file (GAPS W9, verbs item D).

`bm` replaces the hand-written `STATUS.local.md` that a statusline, a
projects-overview script, and a notify script all read. Those consumers re-render
constantly and the measured floor for *any* `bm` command is 0.15 s, so they
cannot call `bm`: the write path leaves a small file behind and they read that.

Three constraints, all from W9, and each one is a failure that already happened:

- **The shape is fixed by the strictest parser.** The statusline requires
  `lines[0] == "---"` **and** `lines[1].startswith("headline:")`; the other two
  read line 2 with no check at all. A malformed write fails silently in one
  consumer and displays wrong text in the other two.
- **mtime is a staleness signal, so a no-op must not write.** The overview script
  reads the file's mtime to decide whether a project has gone quiet. A regen that
  rewrites unconditionally makes every stale project read as fresh — the precise
  silent failure the flat file was kept for. Hence read-compare-skip.
- **The file lives in the store** (`store/<external_id>/headline.md`, decision
  D6). Writing next to a working directory's `.bm.yml` would be `bm` editing
  someone else's tree.

The value is derived, not stored: the most recently updated non-terminal `task`,
its title truncated to the statusline's limit. No new frontmatter field, so no
set-once surface is widened.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import Entity, Project
from basic_memory.store.history import store_path
from basic_memory.vocabulary.model import load_vocabulary, terminal_statuses

HEADLINE_FILENAME = "headline.md"

# The statusline truncates past this, so truncating here is what keeps the file
# and the display in agreement.
MAX_HEADLINE_CHARS = 30

# The record type whose title becomes the headline. Only `task` has a lifecycle,
# so it is the only type whose most recent member answers "what is next".
HEADLINE_NOTE_TYPE = "task"


def headline_path(project_external_id: str) -> Path:
    """Where one project's headline file lives (decision D6)."""
    return store_path() / project_external_id / HEADLINE_FILENAME


def render_headline(text: str) -> str:
    """Render the three-line block every consumer parses.

    Line 1 is `---` and line 2 starts `headline:`, which is the whole of what the
    strictest consumer checks.
    """
    return f"---\nheadline: {text}\n---\n"


def headline_text(title: str) -> str:
    """Truncate a task title to what the statusline can show.

    Right-stripped after the cut so a truncation that lands on a space does not
    leave a trailing one, which reads as a rendering bug in a fixed-width bar.
    """
    return title[:MAX_HEADLINE_CHARS].rstrip()


async def refresh_headline(session: AsyncSession, project: Project) -> bool:
    """Rewrite the project's headline file, and report whether anything changed.

    Takes the caller's ``session``: a per-write lookup that opens its own session
    waits on a connection the caller already holds, and the pool is one
    connection (GAPS W4). Returns True when the file was written or removed, so
    the caller knows whether it has a path to commit.
    """
    title = await _current_task_title(session, project)
    path = headline_path(project.external_id)

    # No open work is a real answer, and an empty headline is not: the consumers
    # would render a blank bar rather than falling back to their own default.
    if title is None:
        if not path.exists():
            return False
        path.unlink()
        return True

    content = render_headline(headline_text(title))
    # The no-op skip, which is the entire reason this file is written by a
    # function rather than by a template: mtime is a consumer's staleness check.
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


async def _current_task_title(session: AsyncSession, project: Project) -> str | None:
    """The most recently updated non-terminal task's title, if there is one."""
    status = Entity.entity_metadata["status"].as_string()
    query = (
        select(Entity.title)
        .where(
            Entity.project_id == project.id,
            Entity.note_type == HEADLINE_NOTE_TYPE,
        )
        # Ties break on file path so an unchanged corpus derives the same
        # headline twice, which is what makes the no-op skip reachable.
        .order_by(Entity.updated_at.desc(), Entity.file_path.asc())
        .limit(1)
    )

    if terminal := terminal_statuses(load_vocabulary(project.external_id)):
        # A task with no status counts as open. Hiding open work because its
        # frontmatter is incomplete would suppress the thing the file exists to
        # show, over a fault `bm doctor` already reports.
        query = query.where(or_(status.is_(None), status.not_in(sorted(terminal))))

    return await session.scalar(query)
