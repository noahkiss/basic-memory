"""Tests for the note store's local git history (GAPS W3)."""

import os
import subprocess
import time
from pathlib import Path

import pytest

from basic_memory.store import history
from basic_memory.store.history import (
    HistoryError,
    commit_paths,
    commits_for_session,
    dirty_count,
    dirty_paths,
    ensure_store_repo,
    latest_commit,
    paths_in_commit,
    restore_from_commit,
    store_path,
    sweep_commit,
)


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point the store at a temp data dir, as BASIC_MEMORY_CONFIG_DIR does in use."""
    data = tmp_path / "data"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data))
    # The retries are real waits; the behavior under test is which attempt wins.
    monkeypatch.setattr(history, "_LOCK_RETRY_DELAYS", (0.0, 0.0))
    return data


def git(store: Path, *args: str) -> str:
    """Read from the store repo with a plain git call, independent of the module."""
    return subprocess.run(
        ["git", "-C", str(store), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def write(store: Path, relative: str, text: str) -> str:
    path = store / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def test_store_path_follows_the_data_dir(data_dir: Path) -> None:
    assert store_path() == data_dir / "store"


def test_ensure_store_repo_creates_repo_and_enforces_config(data_dir: Path) -> None:
    store = ensure_store_repo()

    assert (store / ".git").is_dir()
    assert git(store, "config", "--get", "core.excludesFile").strip() == "/dev/null"
    assert git(store, "config", "--get", "core.hooksPath").strip() == "/dev/null"
    assert git(store, "config", "--get", "commit.gpgsign").strip() == "false"
    assert git(store, "config", "--get", "user.name").strip() == "bm-store"
    assert git(store, "config", "--get", "user.email").strip() == "bm-store@localhost"


def test_ensure_store_repo_is_idempotent(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "first\n")
    commit_paths(["notes/one.md"], "add one", actor="cli", session_id=None)

    assert ensure_store_repo() == store
    assert "add one" in git(store, "log", "--format=%s")


def test_ensure_store_repo_repairs_changed_config(data_dir: Path) -> None:
    store = ensure_store_repo()
    subprocess.run(
        ["git", "-C", str(store), "config", "core.hooksPath", "/some/hooks"],
        check=True,
    )

    ensure_store_repo()

    assert git(store, "config", "--get", "core.hooksPath").strip() == "/dev/null"


def test_commit_paths_commits_only_the_named_paths(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/mine.md", "written by the tool\n")
    write(store, "notes/theirs.md", "written by someone else\n")

    result = commit_paths(["notes/mine.md"], "write mine", actor="agent", session_id="s-1")

    assert result is not None
    assert result.paths == ("notes/mine.md",)
    assert result.dirty_others == ("notes/theirs.md",)
    assert git(store, "show", "--name-only", "--format=", "HEAD").split() == ["notes/mine.md"]
    assert "notes/theirs.md" in git(store, "status", "--porcelain")


def test_commit_paths_writes_session_and_actor_trailers(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")

    commit_paths(["notes/one.md"], "write one", actor="agent", session_id="s-42")

    message = git(store, "log", "-1", "--format=%B")
    assert message.splitlines()[:4] == ["write one", "", "Session: s-42", "Actor: agent"]


def test_commit_paths_omits_unknown_trailers(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")

    commit_paths(["notes/one.md"], "write one", actor=None, session_id=None)

    message = git(store, "log", "-1", "--format=%B").strip()
    assert message == "write one"


def test_commit_paths_returns_none_when_nothing_changed(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")
    commit_paths(["notes/one.md"], "write one", actor=None, session_id=None)

    assert commit_paths(["notes/one.md"], "write one again", actor=None, session_id=None) is None


def test_commit_paths_ignores_an_ambient_git_dir(
    data_dir: Path, tmp_path: Path, monkeypatch
) -> None:
    """An exported GIT_DIR must not redirect the write into another repository."""
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init", "--quiet"], check=True)

    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))

    result = commit_paths(["notes/one.md"], "write one", actor=None, session_id=None)

    assert result is not None
    # These reads must scrub GIT_DIR too, or they would read the other repo.
    scrubbed = {key: value for key, value in os.environ.items() if key != "GIT_DIR"}
    store_log = subprocess.run(
        ["git", "-C", str(store), "log", "--format=%s"],
        capture_output=True,
        text=True,
        env=scrubbed,
        check=True,
    )
    assert "write one" in store_log.stdout
    other_log = subprocess.run(
        ["git", "-C", str(other), "log", "--format=%s"],
        capture_output=True,
        text=True,
        env=scrubbed,
    )
    assert other_log.returncode != 0  # the other repo never got a commit


def test_stale_index_lock_is_removed(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")
    lock = store / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (time.time() - 3600, time.time() - 3600))

    result = commit_paths(["notes/one.md"], "write one", actor=None, session_id=None)

    assert result is not None
    assert not lock.exists()


def test_fresh_index_lock_raises_an_actionable_error(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "body\n")
    lock = store / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")

    with pytest.raises(HistoryError) as exc_info:
        commit_paths(["notes/one.md"], "write one", actor=None, session_id=None)

    message = str(exc_info.value)
    assert "index.lock" in message
    assert str(store) in message
    assert lock.exists()  # a lock a live process may hold is left alone


def test_dirty_paths_lists_untracked_and_modified(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/tracked.md", "first\n")
    commit_paths(["notes/tracked.md"], "write tracked", actor=None, session_id=None)
    write(store, "notes/tracked.md", "second\n")
    write(store, "notes/new.md", "fresh\n")

    entries = {path: status for status, path in dirty_paths()}

    assert entries["notes/tracked.md"] == " M"
    assert entries["notes/new.md"] == "??"


def test_dirty_paths_on_a_fresh_store_is_empty(data_dir: Path) -> None:
    assert dirty_paths() == []


def test_dirty_count_leaves_an_absent_store_absent(data_dir: Path) -> None:
    """The per-command notice must not create the repository it reports on."""
    assert dirty_count() == 0
    assert not (store_path() / ".git").exists()


def test_dirty_count_narrows_to_one_project_s_store_directory(data_dir: Path) -> None:
    """A pinned scope must not be handed another project's uncommitted work."""
    store = ensure_store_repo()
    write(store, "alpha/one.md", "one\n")
    write(store, "alpha/two.md", "two\n")
    write(store, "beta/three.md", "three\n")

    assert dirty_count() == 3
    assert dirty_count("alpha") == 2
    # Positive control: the prefix is a directory, not a string prefix.
    assert dirty_count("alph") == 0


def test_sweep_commit_takes_everything_dirty_without_an_actor(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "one\n")
    write(store, "notes/two.md", "two\n")

    result = sweep_commit("bm history commit --all")

    assert result is not None
    assert result.dirty_others == ()
    assert sorted(result.paths) == ["notes/one.md", "notes/two.md"]
    assert "Actor:" not in git(store, "log", "-1", "--format=%B")


def test_sweep_commit_with_nothing_dirty_returns_none(data_dir: Path) -> None:
    ensure_store_repo()

    assert sweep_commit("bm history commit --all") is None


# --- Reading history, for undo (GAPS W3, verbs item H) ---


def test_latest_commit_on_a_store_with_no_history_is_none(data_dir: Path) -> None:
    """An empty repository is a result, not an error: `bm undo` says so and exits 0."""
    ensure_store_repo()

    assert latest_commit() is None


def test_latest_commit_returns_the_newest_commit(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "one\n")
    commit_paths(["notes/one.md"], "create notes/one.md", actor="cli", session_id=None)
    write(store, "notes/one.md", "two\n")
    second = commit_paths(["notes/one.md"], "update notes/one.md", actor="cli", session_id=None)

    assert second is not None
    assert latest_commit() == second.sha
    assert git(store, "rev-parse", "HEAD").strip() == second.sha


def test_commits_for_session_returns_only_that_session_newest_first(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "one\n")
    first = commit_paths(["notes/one.md"], "create one", actor="agent", session_id="s-1")
    write(store, "notes/two.md", "two\n")
    commit_paths(["notes/two.md"], "create two", actor="agent", session_id="s-2")
    write(store, "notes/three.md", "three\n")
    third = commit_paths(["notes/three.md"], "create three", actor="agent", session_id="s-1")

    assert first is not None and third is not None
    assert commits_for_session("s-1") == (third.sha, first.sha)


def test_commits_for_session_anchors_the_whole_trailer_line(data_dir: Path) -> None:
    """A session id that is a prefix of another must not pull in its commits."""
    store = ensure_store_repo()
    write(store, "notes/one.md", "one\n")
    commit_paths(["notes/one.md"], "create one", actor="agent", session_id="s-1-longer")

    assert commits_for_session("s-1") == ()
    # Positive control: the full id does match the commit that carries it.
    assert len(commits_for_session("s-1-longer")) == 1


def test_commits_for_session_on_a_store_with_no_history_is_empty(data_dir: Path) -> None:
    ensure_store_repo()

    assert commits_for_session("s-1") == ()


def test_restore_from_commit_puts_back_the_parent_s_content(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "first\n")
    commit_paths(["notes/one.md"], "create one", actor="cli", session_id=None)
    write(store, "notes/one.md", "second\n")
    second = commit_paths(["notes/one.md"], "update one", actor="cli", session_id=None)

    assert second is not None
    assert restore_from_commit(second.sha) == ("notes/one.md",)
    assert (store / "notes/one.md").read_text(encoding="utf-8") == "first\n"


def test_restore_from_a_commit_that_added_a_file_deletes_it(data_dir: Path) -> None:
    """A path the commit created has no parent version, so restoring it removes it."""
    store = ensure_store_repo()
    write(store, "notes/one.md", "first\n")
    commit_paths(["notes/one.md"], "create one", actor="cli", session_id=None)
    write(store, "notes/two.md", "two\n")
    second = commit_paths(["notes/two.md"], "create two", actor="cli", session_id=None)

    assert second is not None
    assert restore_from_commit(second.sha) == ("notes/two.md",)
    assert not (store / "notes/two.md").exists()
    # Positive control: the earlier commit's file is untouched.
    assert (store / "notes/one.md").read_text(encoding="utf-8") == "first\n"


def test_restore_from_the_root_commit_deletes_every_path_it_added(data_dir: Path) -> None:
    """The root commit has no parent, so nothing it introduced has a prior version."""
    store = ensure_store_repo()
    write(store, "notes/one.md", "one\n")
    write(store, "notes/two.md", "two\n")
    root = commit_paths(
        ["notes/one.md", "notes/two.md"], "create both", actor="cli", session_id=None
    )

    assert root is not None
    assert sorted(restore_from_commit(root.sha)) == ["notes/one.md", "notes/two.md"]
    assert not (store / "notes/one.md").exists()
    assert not (store / "notes/two.md").exists()


def test_restore_from_a_commit_that_deleted_a_file_brings_it_back(data_dir: Path) -> None:
    store = ensure_store_repo()
    write(store, "notes/one.md", "first\n")
    commit_paths(["notes/one.md"], "create one", actor="cli", session_id=None)
    (store / "notes/one.md").unlink()
    removal = commit_paths(["notes/one.md"], "remove one", actor="cli", session_id=None)

    assert removal is not None
    assert restore_from_commit(removal.sha) == ("notes/one.md",)
    assert (store / "notes/one.md").read_text(encoding="utf-8") == "first\n"


def test_paths_in_commit_reads_without_touching_the_worktree(data_dir: Path) -> None:
    """`bm undo` reads what it would overwrite before it overwrites anything."""
    store = ensure_store_repo()
    write(store, "notes/one.md", "first\n")
    commit_paths(["notes/one.md"], "create one", actor="cli", session_id=None)
    write(store, "notes/one.md", "second\n")
    second = commit_paths(["notes/one.md"], "update one", actor="cli", session_id=None)

    assert second is not None
    assert paths_in_commit(second.sha) == ("notes/one.md",)
    # The file is untouched: reading is not restoring.
    assert (store / "notes/one.md").read_text(encoding="utf-8") == "second\n"


# --- latest_undoable_commit: the pair-cancelling walk (GAPS U26) ---


def undo_commit(store: Path, relative: str, reverted: str) -> str:
    """Record a restore commit the way `bm undo` does: with its Undo-Of trailer."""
    write(store, relative, f"restored against {reverted}\n")
    result = commit_paths(
        [relative], f"undo {reverted}", actor="cli", session_id=None, undo_of=[reverted]
    )
    assert result is not None
    return result.sha


def real_commit(store: Path, relative: str, text: str) -> str:
    write(store, relative, text)
    result = commit_paths([relative], f"create {relative}", actor="test", session_id=None)
    assert result is not None
    return result.sha


def test_undo_of_trailer_lands_on_the_restore_commit(data_dir: Path) -> None:
    store = ensure_store_repo()
    first = real_commit(store, "a.md", "one\n")
    undo_commit(store, "a.md", first)

    body = git(store, "log", "-1", "--format=%B")
    assert f"Undo-Of: {first}" in body


def test_latest_undoable_commit_on_an_empty_store_is_none(data_dir: Path) -> None:
    ensure_store_repo()
    assert history.latest_undoable_commit() is None


def test_latest_undoable_commit_skips_a_restore_and_what_it_reverted(data_dir: Path) -> None:
    """Undo then undo peels two real writes instead of ping-ponging (GAPS U26)."""
    store = ensure_store_repo()
    first = real_commit(store, "a.md", "one\n")
    second = real_commit(store, "b.md", "two\n")
    undo_commit(store, "b.md", second)

    # The restore and the commit it reverted cancel; the next real write is first.
    assert history.latest_undoable_commit() == first


def test_latest_undoable_commit_survives_a_redo(data_dir: Path) -> None:
    """R(R(X)) cancels: a redone write is the next target again (GAPS U26)."""
    store = ensure_store_repo()
    target = real_commit(store, "a.md", "one\n")
    restore = undo_commit(store, "a.md", target)
    # The redo is an undo of the restore, which is what --last records.
    undo_commit(store, "a.md", restore)

    # The redo cancelled the restore before its trailer was read, so the
    # original write is in force and is what a bare undo should revert.
    assert history.latest_undoable_commit() == target


def test_latest_undoable_commit_treats_a_legacy_restore_as_a_real_commit(data_dir: Path) -> None:
    """A pre-U26 restore has no trailer, so targeting it (a redo) is preserved."""
    store = ensure_store_repo()
    real_commit(store, "a.md", "one\n")
    # The old shape: an undo-styled subject with no Undo-Of trailer.
    legacy = real_commit(store, "a.md", "restored by an old bm\n")

    assert history.latest_undoable_commit() == legacy


def test_latest_undoable_commit_with_everything_cancelled_is_none(data_dir: Path) -> None:
    """A store whose one write was undone has no next real write to peel."""
    store = ensure_store_repo()
    only = real_commit(store, "a.md", "one\n")
    undo_commit(store, "a.md", only)

    assert history.latest_undoable_commit() is None
