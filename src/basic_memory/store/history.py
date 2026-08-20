"""Local git history for the note store.

Every mutation of the store commits into a plain git repository at
``<data dir>/store`` so pruning, overwrites, and imports stay recoverable
(GAPS W3). This module is deliberately dependency-free: no DB, no SQLAlchemy,
no API layer — a write path that already paid for a repository call must not
pay again to record it.

The repository is local-only. It must never gain a public remote.
"""

import os
import re
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from basic_memory.config_models import resolve_data_dir

# --- Repository invariants ---

# Enforced on every ensure_store_repo() call, not only at init.
#
# - core.excludesFile / core.hooksPath: a globally configured pre-commit hook
#   (secret scanners are the common case) blocks automated commits, and a global
#   ignore file can silently exclude note content. Pointing both at /dev/null
#   inside this repo is the fix; `--no-verify` is not, because it is
#   per-invocation and one forgotten call reopens the hole (GAPS W3-F).
# - commit.gpgsign: a signing prompt has no one to answer it here.
# - user.name / user.email: commits must not depend on the machine's global git
#   identity, which may be unset or may carry a work address.
_STORE_CONFIG: dict[str, str] = {
    "core.excludesFile": "/dev/null",
    "core.hooksPath": "/dev/null",
    "commit.gpgsign": "false",
    "user.name": "bm-store",
    "user.email": "bm-store@localhost",
}

# Constraint: index.lock contention is transient here — bm is the only writer —
# so a short backoff clears it. The delays are the wait before each retry.
_LOCK_RETRY_DELAYS: tuple[float, ...] = (0.2, 0.4)

# A lock younger than this may belong to a live process; see _stale_lock().
_STALE_LOCK_SECONDS = 5.0

# Environment variables that redirect git away from the repo named by `-C`.
# With GIT_DIR exported, git run anywhere operates on that dir instead (GAPS W3-F).
_GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


class HistoryError(RuntimeError):
    """A store-history operation failed.

    The message names what failed, which repository, and what to try: an agent
    can clear a stale lock when told to, but cannot act on "git failed"
    (GAPS W3-A).
    """


@dataclass(slots=True)
class CommitResult:
    """What one history commit recorded."""

    sha: str
    paths: tuple[str, ...]
    # Store-relative paths that are dirty but were NOT part of this commit.
    # Reported, never swept in: an uncommitted file may be a human edit, a
    # crashed agent write, or a half-finished import, and a wrong Actor trailer
    # makes `undo --session` act on the wrong change (GAPS W3-B).
    dirty_others: tuple[str, ...]


def store_path() -> Path:
    """Return the note store's path.

    Derived from ``resolve_data_dir()`` so the store honours
    ``BASIC_MEMORY_CONFIG_DIR`` exactly like ``config.json`` and ``memory.db``.
    """
    return resolve_data_dir() / "store"


def ensure_store_repo() -> Path:
    """Create the store repository if absent and enforce its config.

    Idempotent, and safe to call before every write: `git config` costs
    milliseconds, and re-applying it makes the repo self-healing if a user or
    another tool changes a value.
    """
    store = store_path()
    store.mkdir(parents=True, exist_ok=True)

    # `git init` inside the directory (rather than via --git-dir) produces a
    # non-bare repo, so the core.bare trap recorded in GAPS W3 cannot arise.
    if not (store / ".git").exists():
        _require(_git(store, "init", "--quiet"), store, "initialize the note store")

    # Deliberately absent: the `/*` info/exclude pattern from the original W3
    # notes. That guarded a store nested inside another repo's worktree; here
    # the store IS the worktree, so the pattern would exclude every note.

    for key, value in _STORE_CONFIG.items():
        _require(_git(store, "config", key, value), store, f"set {key} on the note store")

    return store


def commit_paths(
    paths: Sequence[str],
    message: str,
    *,
    actor: str | None,
    session_id: str | None,
    undo_of: Sequence[str] | None = None,
) -> CommitResult | None:
    """Commit exactly ``paths`` (store-relative) and report what else is dirty.

    Returns None when the named paths hold no change: a no-op is a result, not
    a failure. Never stages anything it was not given — the tool only commits
    changes it made (GAPS W3-B).
    """
    store = ensure_store_repo()
    targets = tuple(paths)
    if not targets:
        return None

    _require(
        _git_retrying_lock(store, "add", "--", *targets),
        store,
        f"stage {len(targets)} path(s) in the note store",
    )

    staged = _git(store, "diff", "--cached", "--quiet")
    # Exit 0 means the index matches HEAD; 1 means there is something to commit.
    # Anything else is a real git failure, not an answer.
    if staged.returncode == 0:
        return None
    if staged.returncode > 1:
        _require(staged, store, "inspect staged changes in the note store")

    _require(
        _git_retrying_lock(
            store, "commit", "-m", _commit_message(message, actor, session_id, undo_of)
        ),
        store,
        "commit to the note store",
    )

    head = _git(store, "rev-parse", "HEAD")
    _require(head, store, "read the new commit id from the note store")

    committed = set(targets)
    dirty_others = tuple(path for _, path in _porcelain(store) if path not in committed)
    return CommitResult(sha=head.stdout.strip(), paths=targets, dirty_others=dirty_others)


def dirty_paths() -> list[tuple[str, str]]:
    """Return ``(status, store-relative path)`` for every uncommitted change.

    Untracked files are included: they are exactly the notes a crashed write or
    an outside editor left behind.
    """
    return _porcelain(ensure_store_repo())


def dirty_count(path_prefix: str | None = None) -> int:
    """Count uncommitted store changes without creating the repository.

    ``dirty_paths`` goes through ``ensure_store_repo``, which initializes the
    store and rewrites its config. That is right before a write and wrong on a
    read: the per-command notice (GAPS W5-B) runs on every project-touching
    verb, and a report must not create the thing it reports on. An absent repo
    is therefore zero dirty files, not an error.

    ``path_prefix`` narrows the count to one project's store directory, so a
    pinned scope is not handed another project's uncommitted work (GAPS W5-C).
    """
    store = store_path()
    if not (store / ".git").is_dir():
        return 0

    entries = _porcelain(store)
    if path_prefix is None:
        return len(entries)
    return sum(1 for _, path in entries if path.startswith(f"{path_prefix}/"))


def sweep_commit(message: str, paths: Sequence[str] | None = None) -> CommitResult | None:
    """Commit ``paths``, or everything dirty when ``paths`` is None.

    Carries no ``Actor:`` and no ``Session:`` trailer. A sweep collects changes
    whose origin the tool does not know, and a guessed label is recorded as
    fact — silence beats a guess (GAPS W3-B).
    """
    store = ensure_store_repo()
    targets = list(paths) if paths is not None else [path for _, path in _porcelain(store)]
    if not targets:
        return None
    return commit_paths(targets, message, actor=None, session_id=None)


# --- Reading history, for undo ---


def latest_commit() -> str | None:
    """The store's newest commit, or None when nothing has been committed yet.

    An empty repository is a result, not an error: `bm undo` on a store with no
    history says so and exits 0 (output contract rule 5).
    """
    store = ensure_store_repo()
    result = _git(store, "rev-parse", "--quiet", "--verify", "HEAD")
    # `--quiet --verify` exits 1 for "not a valid ref" and 128 for a repository
    # that is broken, so the two cases stay distinguishable.
    if result.returncode == 1:
        return None
    _require(result, store, "read the newest commit from the note store")
    return result.stdout.strip()


def latest_undoable_commit() -> str | None:
    """The newest commit `bm undo` should revert, cancelling undo pairs (GAPS U26).

    Each restore is itself a new commit, so "revert the newest commit" made a
    second undo revert the first — three undos netted one revert. The walk here
    reads the ``Undo-Of:`` trailers restores carry and cancels pairs: a restore
    is skipped and the commits it reverted are skipped with it, so each bare
    `bm undo` peels one more *real* write. An undone-then-redone write stays
    reachable, because the redo (an undo of an undo) cancels the restore it
    reverted before that restore's own trailer is read.

    A restore recorded before the trailer existed looks like a normal commit and
    is returned as the target — reverting it is a redo, which is exactly what
    targeting it used to mean. Accepted edge, noted in GAPS U26.
    """
    if latest_commit() is None:
        return None

    store = store_path()
    # One log call for the whole walk: sha, space, then the commit's Undo-Of
    # values space-separated (shas contain no spaces). No trailer renders as
    # an empty field list.
    result = _git(store, "log", "--format=%H %(trailers:key=Undo-Of,valueonly,separator=%x20)")
    _require(result, store, "read the undo trailers from the note store")

    skip: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        sha, undone = fields[0], fields[1:]
        # Order matters: a restore that was itself reverted is dead history —
        # harvesting its trailer would wrongly skip the write it once reverted,
        # which the later redo put back in force.
        if sha in skip:
            continue
        if undone:
            skip.update(undone)
            continue
        return sha
    return None


def commits_for_session(session_id: str) -> tuple[str, ...]:
    """Every commit carrying ``Session: <session_id>``, newest first.

    This is what W3 meant by "`undo --session` is a `git log --grep` away". The
    pattern is anchored to a whole line and the id is escaped, so one session id
    that is a prefix of another cannot pull in the wrong commits.
    """
    if latest_commit() is None:
        return ()

    store = store_path()
    pattern = f"^Session: {re.escape(session_id)}$"
    result = _git(store, "log", "--extended-regexp", f"--grep={pattern}", "--format=%H")
    _require(result, store, f"find commits for session '{session_id}' in the note store")
    return tuple(result.stdout.split())


def paths_in_commit(sha: str) -> tuple[str, ...]:
    """The store-relative paths one commit changed, touching nothing.

    Read-only on purpose: `bm undo` has to know what it is about to overwrite
    *before* it overwrites anything, so it can refuse a restore that would
    discard an uncommitted edit.
    """
    store = ensure_store_repo()
    return tuple(path for _, path in _commit_changes(store, sha))


def restore_from_commit(sha: str) -> tuple[str, ...]:
    """Put every path ``sha`` touched back to the content its parent held.

    A path the commit *added* has no parent version, so restoring it means
    deleting it. Nothing here commits and nothing resets: the caller writes a new
    commit, because history is the thing this subsystem exists to protect.

    Returns the store-relative paths it acted on, in the order git reported them.
    """
    store = ensure_store_repo()
    parent = _parent_commit(store, sha)
    changes = _commit_changes(store, sha)

    for status, path in changes:
        # A root commit has no parent, so every one of its paths is an addition
        # whichever status git reported.
        if parent is None or status.startswith("A"):
            (store / path).unlink(missing_ok=True)
        else:
            _require(
                _git_retrying_lock(store, "checkout", parent, "--", path),
                store,
                f"restore '{path}' from commit {parent} in the note store",
            )
    return tuple(path for _, path in changes)


def _parent_commit(store: Path, sha: str) -> str | None:
    """The commit before ``sha``, or None when ``sha`` is the root commit."""
    result = _git(store, "rev-parse", "--quiet", "--verify", f"{sha}^")
    if result.returncode == 1:
        return None
    _require(result, store, f"read the commit before {sha} in the note store")
    return result.stdout.strip()


def _commit_changes(store: Path, sha: str) -> tuple[tuple[str, str], ...]:
    """Return ``(status, path)`` for every file one commit changed.

    ``--root`` makes the first commit report its files instead of nothing, and
    ``-z`` keeps paths byte-exact for names git would otherwise quote. Rename
    detection is deliberately off: without it a rename arrives as a delete plus
    an add, which is exactly the pair the restore above already handles.
    """
    result = _git(store, "diff-tree", "--no-commit-id", "--name-status", "--root", "-r", "-z", sha)
    _require(result, store, f"read the files changed by commit {sha} in the note store")

    fields = [field for field in result.stdout.split("\0") if field]
    return tuple(zip(fields[::2], fields[1::2]))


# --- git plumbing ---


def _scrubbed_env() -> dict[str, str]:
    """Return the environment with git's location overrides removed.

    Constraint: an exported GIT_DIR (or GIT_WORK_TREE / GIT_INDEX_FILE) wins
    over `-C`, so an agent running bm inside a project repo whose shell exports
    one would write history into the wrong repository (GAPS W3-F).
    """
    return {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_VARS}


def _git(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command against the store, never raising on exit status."""
    try:
        return subprocess.run(
            ["git", "-C", str(store), "-c", "core.quotePath=false", *args],
            env=_scrubbed_env(),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HistoryError(
            f"git is not installed or not on PATH, so the note history at {store} "
            "cannot be recorded. Install git, then retry."
        ) from exc


def _require(result: subprocess.CompletedProcess[str], store: Path, action: str) -> None:
    """Raise an agent-actionable HistoryError when a git command failed."""
    if result.returncode == 0:
        return
    detail = (result.stderr or result.stdout).strip() or f"git exited {result.returncode}"
    hint = ""
    if _mentions_index_lock(detail):
        hint = (
            f" If no other bm process is running, remove {store / '.git' / 'index.lock'} and retry."
        )
    raise HistoryError(f"Failed to {action} at {store}: {detail}.{hint}")


def _git_retrying_lock(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git, retrying while another writer holds the index lock.

    Trigger: git reports index.lock. Why: the contention is transient — bm is
    the only writer — so a short backoff clears it without touching the repo.
    Outcome: the caller sees a normal result, or a failure it can act on.
    """
    result = _git(store, *args)
    for delay in _LOCK_RETRY_DELAYS:
        if result.returncode == 0 or not _mentions_index_lock(result.stderr):
            return result
        time.sleep(delay)
        result = _git(store, *args)

    if result.returncode != 0 and _mentions_index_lock(result.stderr):
        # Last resort. Removing a lock a live process holds corrupts the index
        # mid-write, so remove only one that outlived every retry above and is
        # older than any plausible in-flight git command.
        lock = store / ".git" / "index.lock"
        if _stale_lock(lock):
            lock.unlink(missing_ok=True)
            result = _git(store, *args)
    return result


def _mentions_index_lock(text: str) -> bool:
    return "index.lock" in text


def _stale_lock(lock: Path) -> bool:
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        # Gone between the failure and this check: nothing to remove.
        return False
    return age > _STALE_LOCK_SECONDS


def _commit_message(
    message: str,
    actor: str | None,
    session_id: str | None,
    undo_of: Sequence[str] | None = None,
) -> str:
    """Build the commit message: subject, blank line, then known trailers.

    The trailers are what `undo --session` and the pair-cancelling walk read
    (GAPS U26), so each is written only when the tool actually knows the value.
    One `Undo-Of:` line per reverted commit, because a session undo reverts
    several at once and each has to cancel individually.
    """
    trailers = [
        line
        for line in (
            f"Session: {session_id}" if session_id else None,
            f"Actor: {actor}" if actor else None,
            *(f"Undo-Of: {sha}" for sha in undo_of or ()),
        )
        if line is not None
    ]
    if not trailers:
        return message
    return "\n".join([message, "", *trailers])


def _porcelain(store: Path) -> list[tuple[str, str]]:
    """Return ``(status, path)`` pairs from `git status --porcelain`.

    -uall expands untracked directories to their files: without it a brand-new
    ``notes/`` directory reports as one ``notes/`` row, and a path-scoped
    commit would list a directory in dirty_others where undo needs file paths.
    """
    result = _git(store, "status", "--porcelain", "-uall")
    _require(result, store, "read the note store's status")

    entries: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        # Renames render as "old -> new"; the new path is the one a caller acts on.
        _, separator, renamed = path.partition(" -> ")
        entries.append((status, renamed if separator else path))
    return entries
