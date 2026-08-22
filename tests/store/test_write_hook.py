"""The write path's hookup into the store history (GAPS W3, verbs item C).

These drive `store/write_hook.py` directly against a real git repository in a
temp data dir. The stack-level tests in `tests/index/test_local_write_stack.py`
prove the verbs' write path calls it; these prove what it does when it is called,
including the two failure cases W3-A splits apart.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basic_memory.store import history
from basic_memory.store.history import HistoryError, dirty_paths, store_path
from basic_memory.store.write_hook import (
    OFF_STORE_NOTICE,
    SESSION_ENV_VAR,
    check_can_record,
    commit_message,
    project_store_prefix,
    record_headline_change,
    record_note_write,
    store_relative_path,
)

PROJECT_ID = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point the store at a temp data dir, as BASIC_MEMORY_CONFIG_DIR does in use."""
    data = tmp_path / "data"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data))
    monkeypatch.setattr(history, "_LOCK_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.delenv(SESSION_ENV_VAR, raising=False)
    return data


@pytest.fixture
def project_dir(data_dir: Path) -> Path:
    """A project that lives in the store, which is what makes it recordable."""
    path = store_path() / PROJECT_ID
    path.mkdir(parents=True)
    return path


def git(store: Path, *args: str) -> str:
    """Read from the store repo with a plain git call, independent of the module."""
    return subprocess.run(
        ["git", "-C", str(store), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def write_note_file(project: Path, relative: str, text: str) -> str:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return relative


def break_store_repo() -> None:
    """Leave a `.git` git cannot use, which is what a broken store looks like."""
    store = store_path()
    store.mkdir(parents=True, exist_ok=True)
    (store / ".git").write_text("not a git directory\n", encoding="utf-8")


# --- Where a write is recorded, and where it cannot be ---


def test_store_relative_path_joins_the_project_prefix(project_dir: Path) -> None:
    assert store_relative_path(str(project_dir), "tasks/tnd-abc--x.md") == (
        f"{PROJECT_ID}/tasks/tnd-abc--x.md"
    )


def test_a_project_outside_the_store_has_no_prefix(data_dir: Path, tmp_path: Path) -> None:
    """F3/D3: a user-chosen project path is not in the history repo's worktree."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert project_store_prefix(str(outside)) is None
    assert store_relative_path(str(outside), "note.md") is None


def test_an_off_store_write_skips_the_commit_and_says_so(data_dir: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    write_note_file(outside, "note.md", "body\n")

    outcome = record_note_write(
        project_path=str(outside),
        note_path="note.md",
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    assert outcome.sha is None
    assert outcome.notices == (OFF_STORE_NOTICE,)
    # The report must not create the thing it reports on.
    assert not (store_path() / ".git").exists()


def test_an_external_home_write_skips_the_commit_and_stays_quiet(
    data_dir: Path, tmp_path: Path
) -> None:
    """A project homed elsewhere is versioned by yadm/git, so D3 has nothing to say.

    Same path as the test above — no prefix, no commit — and the only difference
    is the declared intent the caller passes down. The pair is the control: the
    notice is suppressed by that declaration, not by anything about the path.
    """
    outside = tmp_path / "skill" / ".bm"
    outside.mkdir(parents=True)
    write_note_file(outside, "note.md", "body\n")

    outcome = record_note_write(
        project_path=str(outside),
        note_path="note.md",
        operation="create",
        actor="cli",
        externally_homed=True,
    )

    assert outcome.sha is None
    assert outcome.notices == ()
    assert not (store_path() / ".git").exists()


# --- What a successful record leaves behind ---


def test_a_create_commits_the_note_with_both_trailers(project_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv(SESSION_ENV_VAR, "sess-42")
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "body\n")

    outcome = record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="agent",
        externally_homed=False,
    )

    assert outcome.sha is not None
    assert outcome.notices == ()
    message = git(store_path(), "log", "-1", "--format=%B")
    assert message.splitlines()[0] == f"create {PROJECT_ID}/{relative}"
    assert "Session: sess-42" in message
    assert "Actor: agent" in message
    assert git(store_path(), "show", "--name-only", "--format=", "HEAD").strip() == (
        f"{PROJECT_ID}/{relative}"
    )


def test_an_unknown_session_writes_no_session_trailer(project_dir: Path) -> None:
    """Silence beats a guess: the trailer is what `undo --session` reads (W3-B)."""
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "body\n")

    record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    message = git(store_path(), "log", "-1", "--format=%B")
    assert "Session:" not in message
    assert "Actor: cli" in message


def test_the_store_repo_disables_global_hooks_and_excludes(project_dir: Path) -> None:
    """A global pre-commit hook would otherwise block every automated commit (W3)."""
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "body\n")
    record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )
    store = store_path()

    # `--local` is load-bearing, not decoration. A plain `--get` also reads the
    # developer's global config, where `core.hooksPath` is often set — the very
    # hook this repo config exists to defuse. Reading local-only asserts that
    # `ensure_store_repo` wrote the value into this repo, which is the claim.
    assert git(store, "config", "--local", "--get", "core.excludesFile").strip() == "/dev/null"
    assert git(store, "config", "--local", "--get", "core.hooksPath").strip() == "/dev/null"
    # Positive control: the same read on a repo nobody configured exits non-zero,
    # so the two assertions above are reading a value this code set.
    plain = store.parent / "plain-repo"
    plain.mkdir()
    subprocess.run(["git", "-C", str(plain), "init", "--quiet"], check=True)
    unset = subprocess.run(
        ["git", "-C", str(plain), "config", "--local", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert unset.returncode != 0


def test_the_message_is_byte_stable_across_two_identical_writes(project_dir: Path) -> None:
    """Byte-stable subjects are a W3 requirement: otherwise history is noise."""
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "first\n")
    record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )
    write_note_file(project_dir, relative, "second\n")
    record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    subjects = git(store_path(), "log", "--format=%s").splitlines()
    assert subjects == [
        commit_message("create", f"{PROJECT_ID}/{relative}"),
        commit_message("create", f"{PROJECT_ID}/{relative}"),
    ]


def test_an_unchanged_write_records_nothing(project_dir: Path) -> None:
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "body\n")
    record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    repeat = record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="update",
        actor="cli",
        externally_homed=False,
    )

    assert repeat.sha is None
    assert repeat.notices == ()
    assert len(git(store_path(), "log", "--format=%H").splitlines()) == 1


def test_a_headline_change_commits_the_headline_file_alone(project_dir: Path) -> None:
    """`bm headline` owns the headline's history entry (GAPS U24)."""
    (project_dir / "headline.md").write_text("---\nheadline: Ship it\n---\n", encoding="utf-8")

    outcome = record_headline_change(PROJECT_ID)

    assert outcome.sha is not None
    assert outcome.notices == ()
    assert git(store_path(), "show", "--name-only", "--format=", "HEAD").strip() == (
        f"{PROJECT_ID}/headline.md"
    )
    assert git(store_path(), "log", "-1", "--format=%s").strip() == (
        f"headline {PROJECT_ID}/headline.md"
    )


def test_a_headline_clear_commits_the_deletion(project_dir: Path) -> None:
    """A cleared headline must not report as someone else's dirty file forever."""
    headline = project_dir / "headline.md"
    headline.write_text("---\nheadline: Ship it\n---\n", encoding="utf-8")
    record_headline_change(PROJECT_ID)

    headline.unlink()
    outcome = record_headline_change(PROJECT_ID)

    assert outcome.sha is not None
    assert git(store_path(), "status", "--porcelain", "-uall").strip() == ""


def test_a_headline_commit_failure_is_a_notice_not_a_raise(data_dir: Path) -> None:
    """Nothing was destroyed — the set already happened — so the verb only warns."""
    break_store_repo()

    outcome = record_headline_change(PROJECT_ID)

    assert outcome.sha is None
    assert any("not recorded in the note history" in notice for notice in outcome.notices)


def test_the_write_hook_leaves_the_dirty_report_to_the_command_notice(project_dir: Path) -> None:
    """GAPS C3: uncommitted note files are reported once, by `emit_notices`.

    The hook used to return its own dirty line as well, so every write printed
    the same fact twice with two different counts — this one store-wide, the
    command notice's scoped to the verb. The file is still left alone: reported
    elsewhere, never swept into this commit (W3-B).
    """
    relative = write_note_file(project_dir, "tasks/tnd-abc--ship-it.md", "body\n")
    write_note_file(project_dir, "guides/hand-edited.md", "someone else\n")

    outcome = record_note_write(
        project_path=str(project_dir),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    assert outcome.sha is not None
    assert outcome.notices == ()
    assert git(store_path(), "show", "--name-only", "--format=", "HEAD").strip() == (
        f"{PROJECT_ID}/{relative}"
    )
    # Positive control: the other file really is uncommitted, so the empty notice
    # tuple above is a decision about where to report it, not an absent condition.
    assert dirty_paths() == [("??", f"{PROJECT_ID}/guides/hand-edited.md")]


# --- W3-A: a create warns, an overwrite refuses ---


def test_a_broken_repo_refuses_an_overwrite_before_it_touches_the_file(data_dir: Path) -> None:
    """The refusal must precede the write, or the prior content is already gone."""
    project = store_path() / PROJECT_ID
    project.mkdir(parents=True)
    note = project / "tasks/tnd-abc--ship-it.md"
    note.parent.mkdir(parents=True)
    note.write_text("original\n", encoding="utf-8")
    break_store_repo()

    with pytest.raises(HistoryError) as caught:
        check_can_record(str(project), "tnd-abc", "update")

    message = str(caught.value)
    assert "Refused to overwrite 'tnd-abc'" in message
    # Agent-actionable: the underlying error names the repository (GAPS W3-A).
    assert str(store_path()) in message
    assert note.read_text(encoding="utf-8") == "original\n"


def test_a_broken_repo_lets_a_create_through(data_dir: Path) -> None:
    """Nothing is lost: the note is on disk, so a missing entry costs a warning."""
    project = store_path() / PROJECT_ID
    project.mkdir(parents=True)
    break_store_repo()

    check_can_record(str(project), "tnd-abc", "create")


def test_an_off_store_overwrite_is_not_refused(data_dir: Path, tmp_path: Path) -> None:
    """An off-store project has no history to lose, so a broken repo is moot.

    True of an externally homed project too, and deliberately the same test: the
    preflight takes no home flag, because "outside the store's worktree" is the
    whole question and the path already answers it. What holds the prior content
    for an external home — yadm, or whatever versions that directory — is
    outside this process and untouched by the write.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    break_store_repo()

    check_can_record(str(outside), "tnd-abc", "update")


def test_a_create_whose_commit_fails_keeps_the_note_and_warns(data_dir: Path) -> None:
    project = store_path() / PROJECT_ID
    project.mkdir(parents=True)
    relative = write_note_file(project, "tasks/tnd-abc--ship-it.md", "body\n")
    break_store_repo()

    outcome = record_note_write(
        project_path=str(project),
        note_path=relative,
        operation="create",
        actor="cli",
        externally_homed=False,
    )

    assert outcome.sha is None
    assert len(outcome.notices) == 1
    assert "not recorded in the note history" in outcome.notices[0]
    assert (project / relative).read_text(encoding="utf-8") == "body\n"


def test_an_overwrite_whose_commit_fails_raises(data_dir: Path) -> None:
    """A repo that broke between the preflight and the commit still fails loudly.

    The wording changes at this point and the difference is the finding: the file
    is already written, so calling it a refusal would send the reader looking for
    content that is gone.
    """
    project = store_path() / PROJECT_ID
    project.mkdir(parents=True)
    relative = write_note_file(project, "tasks/tnd-abc--ship-it.md", "body\n")
    break_store_repo()

    with pytest.raises(HistoryError, match="no longer recoverable"):
        record_note_write(
            project_path=str(project),
            note_path=relative,
            operation="update",
            actor="cli",
            externally_homed=False,
        )
