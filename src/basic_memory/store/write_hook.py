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
) -> HistoryOutcome:
    """Commit the file one write touched and report what else is dirty.

    ``note_path`` is project-relative and is what the commit message names. The
    headline file used to ride in here as an extra path; since GAPS U24 the
    headline is written only by `bm headline`, which commits through
    `record_headline_change` below.
    """
    store_note_path = store_relative_path(project_path, note_path)
    if store_note_path is None:
        return HistoryOutcome(sha=None, notices=(OFF_STORE_NOTICE,))

    try:
        result = commit_paths(
            [store_note_path],
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
    # `result.dirty_others` is deliberately dropped here. The per-command notice
    # (`cli/notices.py`) already reports uncommitted note files and names
    # `bm history dirty`, so returning a second line said the same thing twice on
    # every write — with two different counts, because this one is store-wide and
    # that one follows the verb's scope (GAPS C3). One home, and it is the one
    # every verb already prints.
    return HistoryOutcome(sha=result.sha)


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


def record_headline_change(project_external_id: str) -> HistoryOutcome:
    """Commit one project's headline file after `bm headline` changed it.

    The headline lives at `store/<external_id>/headline.md` regardless of where
    the project's notes sit, so this commits even for an off-store project — the
    file is always in the worktree. A failed commit is a notice, never a raise:
    the file is a convenience for the statusline, nothing was destroyed, and the
    set the user asked for has already happened on disk (the same degradation
    W3-A gives a create).

    The filename mirrors `services.headline.HEADLINE_FILENAME` rather than
    importing it: the store layer sits below services, and `ignore_utils` keeps
    the same mirror for the same reason.
    """
    headline_store_path = f"{project_external_id}/headline.md"
    try:
        result = commit_paths(
            [headline_store_path],
            f"headline {headline_store_path}",
            actor="cli",
            session_id=session_id(),
        )
    except HistoryError as exc:
        notice = f"note: the headline changed but was not recorded in the note history. {exc}"
        return HistoryOutcome(sha=None, notices=(notice,))
    return NO_HISTORY if result is None else HistoryOutcome(sha=result.sha)


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
