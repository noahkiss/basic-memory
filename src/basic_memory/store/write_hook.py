"""Record every note write in the store's git history (GAPS W3, verbs item C).

`store/history.py` builds the repository and knows how to commit; this module is
what a write path calls. It decides three things the history layer deliberately
does not:

1. **Whether this project has a history at all.** A project whose path is not
   under `store_path()` cannot be committed — its files are not in the
   repository's worktree. That project writes normally and gets one notice
   (decision D3). `bm project add` is what makes new projects store-derived.
2. **What a failed commit costs**, per W3-A's table. A *create* warns and keeps
   the note: nothing is lost, because the note is on disk. An *overwrite*
   refuses, because the prior content is the thing the history exists to protect
   and without the commit it is gone. The refusal is raised **before** the write,
   which is the only point at which refusing still leaves the file untouched.
3. **What the caller has to tell the user.** Nothing here prints. Notices are
   returned so the verb layer emits them after its payload (output contract
   rule 4).

The commit message is byte-stable — operation plus the store-relative path, no
timestamp and no counter. W3 requires stable serialization for the same reason:
without it every touch is a spurious diff and the history is noise before it
exists.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from basic_memory.store.history import (
    HistoryError,
    commit_paths,
    ensure_store_repo,
    store_path,
)

# Claude Code exports this per session. It becomes the `Session:` trailer, which
# is what makes `bm undo --session` a `git log --grep` away (GAPS W3).
SESSION_ENV_VAR = "CLAUDE_SESSION_ID"

# What a write does to prior content. Only `create` has none to lose, which is
# the whole of W3-A's split. No `delete` yet: the local write stack has no delete
# entry point. W3-A's table names delete alongside overwrite, so a delete verb
# adds the literal here **and** to `_DESTRUCTIVE` — a delete that is not
# destructive is the hole this module exists to prevent.
type WriteOperation = Literal["create", "update", "edit"]

_DESTRUCTIVE: frozenset[str] = frozenset({"update", "edit"})

# D3: a project whose notes are not under the store keeps working, and says so
# once per write rather than failing. The store is the only home for note
# content (AGENTS.md), so this is a migration prompt, not an error.
OFF_STORE_NOTICE = (
    "note: notes in this project are not under the store; history is not "
    "recorded — see bm project add"
)


@dataclass(frozen=True, slots=True)
class HistoryOutcome:
    """What recording one write produced: a commit id, and what to tell the user.

    `sha` is None when there was nothing to record — an off-store project, a
    write whose bytes did not change, or a create whose commit failed and was
    downgraded to a notice.
    """

    sha: str | None
    notices: tuple[str, ...] = ()


NO_HISTORY = HistoryOutcome(sha=None)


def project_store_prefix(project_path: str) -> Path | None:
    """The project's directory relative to the store, or None when it is outside.

    Derived from the project's own path rather than from its `external_id`:
    a directory name inside the store is "a human-browsing label that nothing
    reads" (AGENTS.md), so the path on disk is the authority on where the files
    actually sit.
    """
    try:
        return Path(project_path).resolve().relative_to(store_path().resolve())
    except ValueError:
        return None


def store_relative_path(project_path: str, file_path: str) -> str | None:
    """Return the path git stages for one of a project's files, or None."""
    prefix = project_store_prefix(project_path)
    return None if prefix is None else (prefix / file_path).as_posix()


def check_can_record(project_path: str, target: str, operation: WriteOperation) -> None:
    """Refuse a destructive write whose prior content cannot be recorded (W3-A).

    Called **before** the write, because a refusal after it has already
    overwritten the file protects nothing. A create never reaches the raise: it
    has no prior content, so a broken history costs it a warning and no more.
    ``target`` names what the write was about to change, for the message.
    """
    if operation not in _DESTRUCTIVE:
        return
    if project_store_prefix(project_path) is None:
        return

    try:
        ensure_store_repo()
    except HistoryError as exc:
        raise HistoryError(_refusal(target, exc)) from exc


def record_note_write(
    *,
    project_path: str,
    note_path: str,
    operation: WriteOperation,
    actor: str | None,
    extra_paths: Sequence[Path] = (),
) -> HistoryOutcome:
    """Commit the files one write touched and report what else is dirty.

    ``note_path`` is project-relative and is what the commit message names.
    ``extra_paths`` are absolute paths elsewhere in the store that the same write
    produced — the headline file (GAPS W9) is the one caller. They are staged in
    the same commit rather than left behind, because a store file nobody commits
    reports as dirty on every later write.
    """
    store_note_path = store_relative_path(project_path, note_path)
    if store_note_path is None:
        return HistoryOutcome(sha=None, notices=(OFF_STORE_NOTICE,))

    store_paths = [store_note_path, *_within_store(extra_paths)]
    try:
        result = commit_paths(
            store_paths,
            commit_message(operation, store_note_path),
            actor=actor,
            session_id=session_id(),
        )
    except HistoryError as exc:
        # W3-A: an overwrite whose commit failed has already destroyed the prior
        # content, so it must not be reported as a success. A create keeps the
        # note and warns — the note is on disk and a missing entry costs nothing.
        # `check_can_record` is what normally stops this case one step earlier;
        # reaching here means the repository broke between the two calls.
        if operation in _DESTRUCTIVE:
            raise HistoryError(_unrecorded_overwrite(note_path, exc)) from exc
        return HistoryOutcome(sha=None, notices=(_create_warning(exc),))

    if result is None:
        return NO_HISTORY
    return HistoryOutcome(sha=result.sha, notices=dirty_notices(result.dirty_others))


def commit_message(operation: WriteOperation, store_relative: str) -> str:
    """The commit subject: byte-stable for a given operation and path.

    No timestamp, no counter, no title. Two identical writes produce identical
    messages, so a re-run that changes nothing leaves no diff to read past.
    """
    return f"{operation} {store_relative}"


def session_id() -> str | None:
    """The current session id, or None when the tool does not know it.

    Silence beats a guess: the `Session:` trailer is what `undo --session` reads,
    so an invented value would make undo act on the wrong change (GAPS W3-B).
    """
    value = os.environ.get(SESSION_ENV_VAR, "").strip()
    return value or None


def dirty_notices(dirty_others: tuple[str, ...]) -> tuple[str, ...]:
    """Report uncommitted files this commit did not include (GAPS W3-B).

    Reported, never swept in: an uncommitted file may be a human edit, a crashed
    agent write, or a half-finished import, and welding it into this commit makes
    `bm undo` revert two unrelated changes at once.

    The count is store-wide, because the repository is: `commit_paths` reads one
    `git status` over the whole worktree. The notice says so rather than implying
    one project, since the files may belong to any of them — and since the
    per-command notice (`cli/notices.py`) reports the *pinned* count, so the two
    lines can legitimately disagree.
    """
    if not dirty_others:
        return ()
    count = len(dirty_others)
    subject = "1 other file has" if count == 1 else f"{count} other files have"
    return (
        f"note: {subject} uncommitted changes in the note store "
        "(not included in this commit)\n  run 'bm history dirty' to review",
    )


def _within_store(paths: Sequence[Path]) -> list[str]:
    """Store-relative forms of ``paths``, dropping any that sit outside the store.

    A path outside the worktree cannot be staged, and staging is not what a
    caller asked for anyway: it handed over files it wrote and let this module
    decide which of them the repository can see.
    """
    store = store_path().resolve()
    relative: list[str] = []
    for path in paths:
        try:
            relative.append(path.resolve().relative_to(store).as_posix())
        except ValueError:
            continue
    return relative


# --- Messages ---


def _refusal(target: str, error: HistoryError) -> str:
    """Explain a refused overwrite: what was refused, why, and what to fix.

    ``target`` is whatever the caller named the note by — a record id at the
    preflight, because the file path is not resolved until the mutation service
    has run. An agent can clear a stale lock when told to; it cannot act on "git
    failed" (GAPS W3-A), so the underlying error — which already names the
    repository and the fix — is carried through verbatim.
    """
    return (
        f"Refused to overwrite '{target}': its previous content cannot be "
        f"recorded in the note history first, so the overwrite would lose it. "
        f"{error}"
    )


def _unrecorded_overwrite(file_path: str, error: HistoryError) -> str:
    """Report an overwrite that already happened and could not be recorded.

    Deliberately not phrased as a refusal: the file is written by the time a
    commit can fail, and telling the user it was refused would send them looking
    for content that is already gone.
    """
    return (
        f"Overwrote '{file_path}' but could not record its previous content in "
        f"the note history, so that content is no longer recoverable. {error}"
    )


def _create_warning(error: HistoryError) -> str:
    """Warn that a created note is on disk but absent from the history."""
    return f"note: the note was written but not recorded in the note history. {error}"
