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
    dirty_paths,
    ensure_store_repo,
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
