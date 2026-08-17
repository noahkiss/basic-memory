"""Tests for `bm mine` — reading transcripts with the speaker attributed (GAPS W1).

These drive the real CLI path, not the parser underneath it: the point of the
verb is what an agent sees, and a guard over a layer proves nothing about
whether the caller uses that layer.

The output contract is asserted directly — identifier first, one record per
line, a count line closing the listing, notices and affordances after the
payload, errors on stderr with exit 1. Addressing failures write nothing to
stdout; a partial-corpus failure is the one case that prints a payload and
still exits 1 (rule 6, amended 2026-08-16 — GAPS O10).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

# Importing the module is what registers the verb on the shared app.
import basic_memory.cli.commands.mine  # noqa: F401
from basic_memory.cli.app import app

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
SESSION = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def mine(runner: CliRunner, *arguments: str):
    return runner.invoke(app, ["mine", *arguments])


def payload(result) -> list[str]:
    """The lines up to and including the count line."""
    lines = result.stdout.splitlines()
    closing = next(index for index, line in enumerate(lines) if line.endswith(" turns"))
    return lines[: closing + 1]


# --- The payload ---


def test_a_hit_renders_one_line_with_its_reference_first(runner: CliRunner):
    """Rule 2: the identifier leads, so a date-ref is findable without counting."""
    result = mine(runner, "sqlite", "--dir", str(FIXTURES))

    assert result.exit_code == 0
    rows = payload(result)
    assert rows[0].startswith(f"{SESSION}#L1 ")
    assert "human" in rows[0]
    assert "we should use sqlite for the index" in rows[0]


def test_the_count_line_closes_the_listing(runner: CliRunner):
    result = mine(runner, "sqlite", "--dir", str(FIXTURES))

    assert payload(result)[-1] == "2 turns"


def test_the_default_speaker_hides_tool_results_and_says_so(runner: CliRunner):
    """Rule 4: a notice states a condition rather than hiding it.

    83% of `type: user` lines in the measured corpus are tool results, so a
    caller who does not know that would read a small count as "nothing there".
    """
    result = mine(runner, "sqlite", "--dir", str(FIXTURES))

    assert "tool_result" not in "\n".join(payload(result))
    assert "more turns matched other speakers" in result.stdout
    assert "tool_result" in result.stdout


def test_speaker_all_shows_every_class_the_parser_assigns(runner: CliRunner):
    result = mine(runner, "sqlite", "--dir", str(FIXTURES), "--speaker", "all")

    rows = "\n".join(payload(result))
    for speaker in ("human", "assistant", "thinking", "tool_use", "tool_result", "meta"):
        assert speaker in rows, speaker
    assert "more turns matched other speakers" not in result.stdout


def test_speaker_assistant_selects_only_the_assistant(runner: CliRunner):
    result = mine(runner, "sqlite", "--dir", str(FIXTURES), "--speaker", "assistant")

    rows = payload(result)
    assert rows[-1] == "2 turns"
    assert all("assistant" in row for row in rows[:-1])


def test_context_lines_follow_their_hit_and_are_marked(runner: CliRunner):
    """A context line must never be mistakable for a hit of its own."""
    result = mine(runner, "sqlite", "--dir", str(FIXTURES), "--context", "1")

    rows = payload(result)
    assert rows[0].startswith(f"{SESSION}#L1 ")
    assert rows[1].startswith("    L2 assistant")


def test_subagent_turns_are_out_until_asked_for(runner: CliRunner):
    """A sub-agent's turns are genuine transcripts and are not the user's."""
    without = mine(runner, "sqlite", "--dir", str(FIXTURES), "--speaker", "all")
    with_them = mine(
        runner, "sqlite", "--dir", str(FIXTURES), "--speaker", "all", "--include-subagents"
    )

    assert "agent-reviewer" not in without.stdout
    assert "agent-reviewer" in with_them.stdout


def test_the_default_directory_comes_from_the_working_directory(
    runner: CliRunner, tmp_path: Path, monkeypatch
):
    """No `--dir` means this directory's own transcripts, found by the slug rule."""
    from basic_memory.mine.locate import project_slug

    work = tmp_path / "work"
    work.mkdir()
    transcripts = tmp_path / ".claude" / "projects" / project_slug(work)
    transcripts.mkdir(parents=True)
    (transcripts / f"{SESSION}.jsonl").write_bytes((FIXTURES / f"{SESSION}.jsonl").read_bytes())
    monkeypatch.chdir(work)

    result = mine(runner, "sqlite")

    assert result.exit_code == 0
    assert payload(result)[-1] == "2 turns"


# --- Results that are not failures ---


def test_a_term_nothing_matches_is_a_result(runner: CliRunner):
    """Rule 5: a well-scoped request whose answer is nothing exits 0."""
    result = mine(runner, "postgres", "--dir", str(FIXTURES))

    assert result.exit_code == 0
    assert payload(result) == ["0 turns"]


def test_a_directory_with_no_transcripts_says_so(runner: CliRunner, tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()

    result = mine(runner, "sqlite", "--dir", str(empty))

    assert result.exit_code == 0
    assert "0 turns" in result.stdout
    assert "No transcripts in" in result.stdout


# --- Failures ---


def test_a_missing_directory_exits_one_with_nothing_on_stdout(runner: CliRunner, tmp_path: Path):
    """Rule 6, and rule 5's dividing line: this request cannot be scoped at all."""
    result = mine(runner, "sqlite", "--dir", str(tmp_path / "nope"))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "No transcript directory" in result.stderr


def test_an_unknown_speaker_is_an_addressing_failure(runner: CliRunner):
    result = mine(runner, "sqlite", "--dir", str(FIXTURES), "--speaker", "robot")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "--speaker must be one of" in result.stderr


def test_a_corrupt_line_still_prints_the_payload_and_still_fails(runner: CliRunner, tmp_path: Path):
    """The partial-corpus case: payload on stdout, damage on stderr, exit 1.

    This is the one place the output contract allows both (rule 6, amended
    2026-08-16). R-O1 requirement 4 still holds in full — the bad line is
    counted, named, and the run exits non-zero — but 3 of 61 real project
    directories hold a damaged line, so refusing to mine them at all serves
    nobody (GAPS O10).
    """
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / f"{SESSION}.jsonl").write_text(
        '{"type":"user","message":{"role":"user","content":"sqlite one"}}\n'
        "not json\n"
        '{"type":"user","message":{"role":"user","content":"sqlite two"}}\n'
    )

    result = mine(runner, "sqlite", "--dir", str(broken))

    assert result.exit_code == 1
    assert "sqlite one" in result.stdout
    assert "sqlite two" in result.stdout
    assert "2 turns" in result.stdout
    assert "is not valid JSON" in result.stderr
    assert ":2" in result.stderr
    assert "1 unreadable line was skipped" in result.stderr


def test_quiet_cannot_hide_a_corrupt_line(runner: CliRunner, tmp_path: Path):
    """`--quiet` drops notices, and damage is not a notice — it is a diagnostic."""
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / f"{SESSION}.jsonl").write_text("not json\n")

    result = mine(runner, "sqlite", "--dir", str(broken), "--quiet")

    assert result.exit_code == 1
    assert "is not valid JSON" in result.stderr


def test_a_clean_corpus_exits_zero(runner: CliRunner):
    """Positive control for the two tests above.

    Without it, an exit 1 on damage proves nothing — the verb could be failing
    for some other reason on every run.
    """
    result = mine(runner, "sqlite", "--dir", str(FIXTURES))

    assert result.exit_code == 0
    assert result.stderr == ""


# --- Notices and affordances ---


def test_the_affordances_name_commands_that_exist(runner: CliRunner):
    """W19 item 5: a static list, and every entry has to be runnable.

    The last assertion is the one that earns its place: an affordance naming a
    verb that answers *no such command* teaches the surface wrongly, which is
    the opposite of what an affordance is for.
    """
    import typer.main
    from typer.core import TyperGroup

    from basic_memory.cli.main import app as registered

    result = mine(runner, "sqlite", "--dir", str(FIXTURES))

    assert "next:" in result.stdout
    assert "--context 2" in result.stdout
    assert "--speaker all" in result.stdout

    # Ask the built click app, not `registered_commands`: the latter holds the
    # declarations, whose `name` is None whenever it comes from the function
    # name, so `mine` would not appear under its own name.
    #
    # The isinstance names TyperGroup and not click.Group. Typer 0.26 vendors
    # its own click core, so `TyperGroup` does not subclass `click.Group` and
    # `isinstance(cli, click.Group)` is unconditionally False — an assert that
    # fails on every run and makes the real check below unreachable.
    shipped_cli = typer.main.get_command(registered)
    assert isinstance(shipped_cli, TyperGroup)
    shipped = set(shipped_cli.commands)
    named = {
        line.split()[1]
        for line in result.stdout.splitlines()
        if line.startswith("  bm ") and len(line.split()) > 1
    }
    assert named <= shipped, named - shipped


def test_quiet_drops_the_notices_and_the_affordances(runner: CliRunner):
    """Rule 7: `--quiet` leaves the payload alone and takes the rest."""
    result = mine(runner, "sqlite", "--dir", str(FIXTURES), "--quiet")

    assert result.stdout.splitlines() == payload(result)
    assert "next:" not in result.stdout
    assert "more turns matched" not in result.stdout
