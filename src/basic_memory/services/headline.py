"""The per-project headline file (GAPS W9 item D, revised by GAPS U24).

`bm` replaces the hand-written `STATUS.local.md` that a statusline, a
projects-overview script, and a notify script all read. Those consumers re-render
constantly and the measured floor for *any* `bm` command is 0.15 s, so they
cannot call `bm`: the write path leaves a small file behind and they read that.

The value is **composed, never derived** (GAPS U24). The file used to carry the
most recent open task's title, truncated — which produced mush like "Decide
whether the transcript-s", and could not say anything a task list did not
already say. `bm headline` writes the line deliberately instead, and the agent
that just closed a task is the thing that knows what is actually next. No task
write touches this file any more.

Three constraints survive from W9, and each one is a failure that already
happened:

- **The shape is fixed by the strictest parser.** The statusline requires
  `lines[0] == "---"` **and** `lines[1].startswith("headline:")`; the other two
  read line 2 with no check at all. A malformed write fails silently in one
  consumer and displays wrong text in the other two.
- **mtime is a staleness signal, so a no-op must not write.** The overview script
  reads the file's mtime to decide whether a project has gone quiet. A set that
  rewrites unconditionally makes every stale project read as fresh — the precise
  silent failure the flat file was kept for. Hence read-compare-skip.
- **The file lives in the store** (`store/<external_id>/headline.md`, decision
  D6). Writing next to a working directory's `.bm.yml` would be `bm` editing
  someone else's tree.

A fourth is new with U24: **over-limit is an error, never a truncation.** The
30-char cut is what made derived headlines mush; a composed headline that does
not fit is a line its author has to rewrite, not one the tool may silently maim.
"""

from __future__ import annotations

from pathlib import Path

from basic_memory.store.history import store_path

HEADLINE_FILENAME = "headline.md"

# The statusline truncates past this. A derived headline used to be cut to fit;
# a composed one is refused instead, so the file and the display always agree.
MAX_HEADLINE_CHARS = 30

# What line 2 of the file starts with — the whole of what the strictest
# consumer checks after the `---` above it.
HEADLINE_KEY = "headline: "


class HeadlineError(ValueError):
    """A headline that cannot be written, with the message the verb prints."""


def headline_path(project_external_id: str) -> Path:
    """Where one project's headline file lives (decision D6)."""
    return store_path() / project_external_id / HEADLINE_FILENAME


def render_headline(text: str) -> str:
    """Render the three-line block every consumer parses.

    Line 1 is `---` and line 2 starts `headline:`, which is the whole of what the
    strictest consumer checks.
    """
    return f"---\n{HEADLINE_KEY}{text}\n---\n"


def read_headline(project_external_id: str) -> str | None:
    """The project's current headline, or None when none is set.

    Reads the same two lines the consumers read. A file that does not parse is
    reported as unset rather than raised on: every caller here is composing a
    hint or a footer, and none of them can act on a parse error.
    """
    path = headline_path(project_external_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 2 or lines[0] != "---" or not lines[1].startswith(HEADLINE_KEY):
        return None
    return lines[1].removeprefix(HEADLINE_KEY)


def check_headline(text: str) -> str:
    """Validate a headline someone composed, returning the exact text to write.

    Raises `HeadlineError` rather than repairing: a silent strip-and-truncate
    would put a line on the statusline that nobody wrote (GAPS U24). Whitespace
    at the ends is the one thing forgiven — it renders identically, so refusing
    over it would be pedantry.
    """
    cleaned = text.strip()
    if not cleaned:
        raise HeadlineError("a headline cannot be empty — 'bm headline \"\"' clears it instead")
    if "\n" in cleaned:
        raise HeadlineError("a headline is one line; it cannot contain a newline")
    if len(cleaned) > MAX_HEADLINE_CHARS:
        raise HeadlineError(
            f"headline is {len(cleaned)} chars; the statusline shows at most "
            f"{MAX_HEADLINE_CHARS}. Rewrite it shorter rather than letting it truncate."
        )
    return cleaned


def set_headline(project_external_id: str, text: str) -> bool:
    """Write the project's headline, and report whether the file changed.

    Validation is `check_headline`'s; the write keeps the read-compare-skip
    rule because mtime is a consumer's staleness check — setting the same line
    twice must not make a quiet project read as fresh.
    """
    content = render_headline(check_headline(text))
    path = headline_path(project_external_id)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def clear_headline(project_external_id: str) -> bool:
    """Remove the project's headline file, and report whether one existed.

    No work to point at is a real answer, and an empty headline is not: the
    consumers would render a blank bar rather than falling back to their own
    default. Absence is how the file says "nothing is next".
    """
    path = headline_path(project_external_id)
    if not path.exists():
        return False
    path.unlink()
    return True
