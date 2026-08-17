"""Finding the transcript files for a working directory.

A Claude Code project directory is **not** a directory of transcripts. It is a
set of transcript files plus a sidecar tree: per-session subdirectories holding
`subagents/*.jsonl`, hidden `.context-window-*.json` files, and hook stdout
captures under other extensions. Reading the sidecars is what produced the
phantom "~25% of hits do not parse as JSON" figure diagnosed in GAPS R-O1.

Two rules follow, and both are structural here rather than optional:

1. **The `*.jsonl` filter is an internal positive allowlist.** No caller flag
   turns it off, and it is never expressed as a blocklist of known-bad
   extensions. A blocklist would be worse than nothing: the hidden
   `.context-window-*.json` sidecars are single-line valid JSON, so they would
   parse cleanly and be mined as conversation turns. A loud `JSONDecodeError`
   is the lucky failure mode; that one is silent.
2. **Hidden files never qualify**, whatever their extension, because every
   hidden file in the observed corpus is a sidecar.
"""

from __future__ import annotations

import re
from pathlib import Path

# Every character outside this class becomes a dash in the directory name Claude
# Code derives from a working directory. Verified against a real projects tree:
# `/home/<user>/develop/.tmp-denytest` lands in `...-develop--tmp-denytest`, so
# the leading dot of a hidden directory becomes a dash exactly like the path
# separator does. One rule, no exceptions carved out for dots or underscores.
_NON_SLUG_CHARACTER = re.compile(r"[^A-Za-z0-9]")

# The sidecar subdirectory holding sub-agent transcripts. They are genuine
# transcripts, but a sub-agent's turns are not the user's conversation, so they
# are out by default and `--include-subagents` adds them (GAPS W1, 2026-08-06).
SUBAGENT_DIR = "subagents"

TRANSCRIPT_SUFFIX = ".jsonl"


class TranscriptError(ValueError):
    """A transcript source that cannot be addressed at all.

    An unaddressable request is a failure, not an empty result (output contract
    rule 5): a directory that does not exist means the caller asked about
    something that is not there, which is different from asking correctly and
    finding no match.
    """


def project_slug(working_directory: Path) -> str:
    """Return the directory name Claude Code derives from a working directory."""
    return _NON_SLUG_CHARACTER.sub("-", str(working_directory))


def claude_projects_root() -> Path:
    """Return the directory holding one subdirectory per Claude Code project."""
    return Path.home() / ".claude" / "projects"


def transcripts_dir_for_cwd(working_directory: Path) -> Path:
    """Return the transcript directory for a working directory.

    The path is computed, not searched, so a missing directory is reported
    against the name that was expected rather than as a vague "not found".
    """
    return claude_projects_root() / project_slug(working_directory)


def is_transcript(path: Path) -> bool:
    """Is this file a transcript, by the positive allowlist?

    Both conditions are the allowlist, not a pair of filters: a transcript has
    the `.jsonl` suffix **and** a visible name. Everything else in a project
    directory is a sidecar.
    """
    return path.suffix == TRANSCRIPT_SUFFIX and not path.name.startswith(".")


def transcript_files(directory: Path, *, include_subagents: bool = False) -> list[Path]:
    """List the transcripts under ``directory``, newest session last.

    Sorted by path so the output of two runs over an unchanged tree is
    byte-identical — a caller diffing two mines should see only real changes.

    Raises:
        TranscriptError: the directory does not exist, or is not a directory.
    """
    if not directory.exists():
        raise TranscriptError(f"No transcript directory: {directory}")
    if not directory.is_dir():
        raise TranscriptError(f"Not a directory: {directory}")

    found = (path for path in directory.rglob(f"*{TRANSCRIPT_SUFFIX}") if is_transcript(path))
    if include_subagents:
        return sorted(found)
    return sorted(path for path in found if SUBAGENT_DIR not in path.parts)
