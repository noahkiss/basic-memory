"""Tests for the transcript reader and the turn classifier (GAPS W1).

The fixture under `tests/fixtures/transcripts/` is synthetic but shaped from a
survey of a real Claude Code projects tree: 1,909 transcripts, 85,092 non-empty
lines, 0 JSON parse failures. Every line shape the survey found has a line in
the fixture, so a classifier change that breaks one of them fails here.

Two guarantees carry most of the weight:

- **A `type: user` line is not human speech.** 83% of them in the survey were
  tool results, and a further class is harness injection.
- **The allowlist is positive.** The hidden `.json` sidecars are valid JSON that
  would classify cleanly as conversation, so excluding them cannot be left to a
  parse failure — which is why one test reads a sidecar on purpose and shows it
  would have been mined.
"""

from pathlib import Path

import pytest

from basic_memory.mine.locate import TranscriptError, project_slug, transcript_files
from basic_memory.mine.search import scan
from basic_memory.mine.turns import SPEAKERS, BadLine, Turn, classify, read_turns

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"
SESSION = "11111111-2222-3333-4444-555555555555"
ALL_SPEAKERS = frozenset(SPEAKERS)


@pytest.fixture
def transcript() -> Path:
    return FIXTURES / f"{SESSION}.jsonl"


def turns_only(path: Path) -> list[Turn]:
    """Every `Turn` from a transcript, asserting the transcript is undamaged.

    `read_turns` yields damage beside turns, so a caller has to say which it
    means. The assertion doubles as a positive control: a fixture that started
    failing to parse would fail here rather than quietly shrink the result.
    """
    read = list(read_turns(path))
    assert [item for item in read if isinstance(item, BadLine)] == []
    return [item for item in read if isinstance(item, Turn)]


@pytest.fixture
def turns(transcript: Path) -> dict[int, tuple[str, str]]:
    """The fixture's turns keyed by line number: `{line: (speaker, text)}`."""
    return {turn.line: (turn.speaker, turn.text) for turn in turns_only(transcript)}


# --- Locating transcripts ---


def test_slug_replaces_every_non_alphanumeric_character():
    """One rule, no carve-outs for dots or underscores.

    The decisive real-world case: `/home/<user>/develop/.tmp-denytest` lands in
    a directory named `-home-<user>-develop--tmp-denytest`, so the leading dot
    of a hidden directory became a dash exactly like the separator did.
    """
    assert project_slug(Path("/home/u/develop/basic-memory")) == "-home-u-develop-basic-memory"
    assert project_slug(Path("/home/u/develop/.tmp-x")) == "-home-u-develop--tmp-x"
    assert project_slug(Path("/home/u/a_b.c")) == "-home-u-a-b-c"


def test_allowlist_finds_the_transcript_and_ignores_every_sidecar():
    """Positive control first: the `.jsonl` IS found, so an empty list means something."""
    found = transcript_files(FIXTURES)

    assert [path.name for path in found] == [f"{SESSION}.jsonl"]

    # The sidecars that must not appear all exist on disk — otherwise this test
    # would pass over a directory that could not have failed it.
    assert (FIXTURES / "session-metadata.json").exists()
    assert (FIXTURES / f".context-window-{SESSION}.json").exists()
    assert (FIXTURES / "tool-results" / "hook-1-stdout.txt").exists()


def test_a_hidden_sidecar_would_have_been_mined_as_conversation():
    """Why the allowlist is positive rather than a blocklist of bad extensions.

    The hidden `.context-window-*.json` files are single-line valid JSON in the
    user-message shape. If they were ever read they would parse cleanly and be
    attributed to the human — a silent fabrication, not a loud failure. A
    blocklist protects only against the extensions someone thought of.
    """
    import json

    sidecar = FIXTURES / f".context-window-{SESSION}.json"
    record = json.loads(sidecar.read_text(encoding="utf-8"))

    speaker, text = classify(record)
    assert speaker == "human"
    assert "sidecar" in text

    assert sidecar not in transcript_files(FIXTURES, include_subagents=True)


def test_a_non_json_sidecar_is_the_shape_that_faked_a_parse_failure_rate():
    """The `tool-results/*.txt` captures are Markdown, and R-O1's 25% was them."""
    import json

    capture = FIXTURES / "tool-results" / "hook-1-stdout.txt"
    first_line = capture.read_text(encoding="utf-8").splitlines()[0]

    with pytest.raises(json.JSONDecodeError):
        json.loads(first_line)

    assert capture not in transcript_files(FIXTURES, include_subagents=True)


def test_subagent_transcripts_are_out_by_default_and_in_on_request():
    """A sub-agent's turns are genuine but they are not the user's conversation."""
    default = transcript_files(FIXTURES)
    widened = transcript_files(FIXTURES, include_subagents=True)

    assert len(widened) == len(default) + 1
    assert any("subagents" in path.parts for path in widened)
    assert not any("subagents" in path.parts for path in default)


def test_session_is_the_file_stem_not_the_recorded_session_id():
    """A sub-agent transcript records its *parent's* sessionId.

    All 205 sub-agent transcripts in the survey did. A `date-ref` built from
    that field points `#L<line>` at a file that does not contain the line, so
    the stem is the only identity that resolves.
    """
    subagent = next(
        path for path in transcript_files(FIXTURES, include_subagents=True) if "agent-" in path.name
    )
    turn = turns_only(subagent)[0]

    assert turn.session == subagent.stem
    assert turn.session != SESSION
    assert turn.ref == f"{subagent.stem}#L1"
    assert turn.subagent is True


def test_a_missing_directory_is_an_error(tmp_path: Path):
    with pytest.raises(TranscriptError):
        transcript_files(tmp_path / "nope")


def test_a_file_where_a_directory_belongs_is_an_error(tmp_path: Path):
    target = tmp_path / "a-file"
    target.write_text("", encoding="utf-8")

    with pytest.raises(TranscriptError):
        transcript_files(target)


# --- Classification ---


def test_every_observed_line_shape_is_classified(turns):
    """One assertion per shape the corpus survey found."""
    assert turns[1] == ("human", "we should use sqlite for the index")
    assert turns[2][0] == "assistant"
    assert turns[3] == ("thinking", "sqlite avoids a server")
    assert turns[4][0] == "tool_use"
    assert turns[5][0] == "tool_result"
    assert turns[6] == ("tool_result", "sqlite3 3.45.0")
    assert turns[7] == ("attachment", "hook says sqlite ok")
    assert turns[8] == ("attachment", "context mentions sqlite")
    assert turns[14] == ("human", "keep sqlite, drop the server")
    assert turns[15] == ("human", "")
    assert turns[16] == ("other", "")
    assert turns[17] == ("other", "")


def test_a_user_role_tool_result_is_never_human(turns):
    """The trap this verb exists for: `role: user` carrying tool output."""
    speaker, text = turns[5]

    assert speaker == "tool_result"
    assert text.startswith("File created successfully at:")


def test_injected_user_turns_are_meta_not_human(turns):
    """Harness injection wears the user's role too, and is not the user."""
    assert turns[9][0] == "meta"  # isMeta, no recognisable tag
    assert turns[10][0] == "meta"  # <command-name>
    assert turns[11][0] == "meta"  # <local-command-stdout>
    assert turns[12][0] == "meta"  # [Request interrupted


def test_multi_agent_traffic_is_meta_not_human():
    """Another agent writing to this one is not the person at the keyboard.

    `<teammate-message>` and `<task-notification>` were 1,449 of the 6,864
    turns the classifier called `human` on the re-surveyed tree — the largest
    remaining misattribution, and exactly the class this module exists for.
    """
    injections = (
        '<teammate-message teammate_id="lead">do X</teammate-message>',
        "<task-notification>\n<title>done</title>\n</task-notification>",
    )
    for injected in injections:
        speaker, _ = classify({"type": "user", "message": {"role": "user", "content": injected}})
        assert speaker == "meta", injected


def test_a_redacted_thinking_block_is_still_thinking():
    """Reasoning is classified by the block, never by whether text survived.

    Real thinking blocks carry an empty `thinking` string — the reasoning is
    redacted and only the signature is written (46,915 of 46,917 in the
    re-survey). Classifying on extracted text made a third of all assistant
    lines claim to be tool calls.
    """
    speaker, text = classify(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "", "signature": "sig"}],
            },
        }
    )

    assert speaker == "thinking"
    assert text == ""


def test_prose_wins_over_a_tool_call_on_a_mixed_assistant_line(turns):
    """A line carrying both prose and a call is attributed to the prose."""
    assert turns[18] == ("assistant", "Done — sqlite it is.")


def test_tool_use_text_carries_the_name_and_the_arguments(turns):
    """Searching for the path a tool touched is a real question."""
    speaker, text = turns[4]

    assert speaker == "tool_use"
    assert text.startswith("Write ")
    assert "/w/sqlite.md" in text


def test_line_numbers_count_the_blank_line(turns, transcript: Path):
    """`#L<line>` has to match what an editor shows, blank lines included."""
    assert 13 not in turns
    assert transcript.read_text(encoding="utf-8").splitlines()[12] == ""
    assert turns[14][0] == "human"


def test_a_timestamp_is_absent_rather_than_invented(turns, transcript: Path):
    metadata_line = next(turn for turn in turns_only(transcript) if turn.line == 16)
    speech_line = next(turn for turn in turns_only(transcript) if turn.line == 1)

    assert metadata_line.timestamp is None
    assert speech_line.timestamp == "2026-08-16T09:00:00.100Z"


# --- Damaged lines: counted and reported, never silent and never fatal ---


def test_a_bad_line_is_reported_and_the_rest_of_the_file_still_reads(tmp_path: Path):
    """R-O1 requirement 4 — count them, report them — without losing the file.

    All three verbs of the requirement survive: the bad line is counted, it is
    named with its file and line number, and the caller exits non-zero. What
    the re-survey removed is aborting: 3 of 61 real project directories hold a
    damaged line, and stopping there costs 463,000 readable lines (GAPS O10).
    """
    broken = tmp_path / "bad.jsonl"
    broken.write_text(
        '{"type":"user","message":{"role":"user","content":"before"}}\n'
        "not json\n"
        '{"type":"user","message":{"role":"user","content":"after"}}\n'
    )

    read = list(read_turns(broken))
    damage = [item for item in read if isinstance(item, BadLine)]
    turns = [item for item in read if not isinstance(item, BadLine)]

    assert [turn.text for turn in turns] == ["before", "after"]
    assert len(damage) == 1
    assert damage[0].line == 2
    assert damage[0].path == broken
    assert "is not valid JSON" in str(damage[0])


def test_a_json_line_that_is_not_a_record_is_damage(tmp_path: Path):
    """Valid JSON is not automatically a turn — a bare list is neither."""
    broken = tmp_path / "bad.jsonl"
    broken.write_text('["a list is valid JSON and is not a turn"]\n')

    read = list(read_turns(broken))

    assert len(read) == 1
    assert isinstance(read[0], BadLine)


def test_two_records_glued_onto_one_line_both_parse(tmp_path: Path):
    """Claude Code sometimes drops the newline between two writes.

    Both records are yielded and both carry the physical line number, because
    `#L<line>` has to agree with what an editor shows. A ref can therefore name
    more than one turn, which is the honest trade.
    """
    glued = tmp_path / "glued.jsonl"
    glued.write_text(
        '{"type":"user","message":{"role":"user","content":"first"}}'
        '{"type":"user","message":{"role":"user","content":"second"}}\n'
    )

    read = turns_only(glued)

    assert [turn.text for turn in read] == ["first", "second"]
    assert [turn.line for turn in read] == [1, 1]


def test_a_torn_record_loses_only_itself(tmp_path: Path):
    """The shape that actually occurs, reproduced from the live tree.

    All 12 damaged lines in a 464,728-line projects tree are torn rather than
    cleanly glued: the first record is cut off mid-value and the next is
    concatenated onto the wound, so the file holds no `}{` at all. A plain
    `raw_decode` loop from column 1 recovers **nothing** from these. Walking
    forward to a record start that runs to the end of the line recovers the
    intact record on 12 of 12.
    """
    torn = tmp_path / "torn.jsonl"
    torn.write_text(
        '{"type":"assistant","parentUuid":"aaa","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"cut off here'
        '{"type":"user","parentUuid":"bbb","message":{"role":"user","content":"survivor"}}\n'
    )

    read = list(read_turns(torn))
    damage = [item for item in read if isinstance(item, BadLine)]
    turns = [item for item in read if not isinstance(item, BadLine)]

    assert [turn.text for turn in turns] == ["survivor"]
    assert turns[0].line == 1
    assert len(damage) == 1
    assert damage[0].line == 1


def test_a_clean_file_reports_no_damage(transcript: Path):
    """Positive control: the damage channel must be empty on a good corpus.

    Without this, an empty `damage` could mean the reader stopped looking.
    """
    report = scan([transcript], "sqlite", speakers=ALL_SPEAKERS)

    assert report.damage == ()
    assert report.hits != ()


# --- Search ---


def test_search_matches_extracted_text_not_the_raw_line(tmp_path: Path):
    """A term hiding in the JSON scaffolding must not produce a hit.

    `tool_use_id` and the type names live in every line; matching the raw line
    would return the whole transcript for a search on `content`.
    """
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text('{"type":"user","message":{"role":"user","content":"plain words"}}\n')

    assert scan([path], "plain", speakers=ALL_SPEAKERS).hits != ()
    assert scan([path], "role", speakers=ALL_SPEAKERS).hits == ()


def test_a_speaker_filter_selects_which_turns_hit(transcript: Path):
    human_only = scan([transcript], "sqlite", speakers=frozenset({"human"})).hits
    everyone = scan([transcript], "sqlite", speakers=ALL_SPEAKERS).hits

    assert [hit.turn.line for hit in human_only] == [1, 14]
    assert len(everyone) > len(human_only)
    assert {hit.turn.speaker for hit in everyone} > {"human"}


def test_context_carries_turns_from_every_speaker(transcript: Path):
    """The turn before a human decision is usually the reply it answers."""
    hits = scan([transcript], "sqlite", speakers=frozenset({"human"}), context=1).hits

    first = next(hit for hit in hits if hit.turn.line == 1)
    assert [turn.line for turn in first.context] == [2]
    assert first.context[0].speaker == "assistant"


def test_context_fills_from_both_sides_and_stays_bounded(transcript: Path):
    """A hit with full leading context must still gather trailing context.

    The first shape of the window held both in one list, which silently starved
    trailing context whenever the leading side was already full.
    """
    hits = scan([transcript], "sqlite", speakers=frozenset({"thinking"}), context=2).hits

    assert len(hits) == 1
    assert [turn.line for turn in hits[0].context] == [1, 2, 4, 5]


def test_hits_come_back_in_line_order_with_no_context(transcript: Path):
    hits = scan([transcript], "sqlite", speakers=ALL_SPEAKERS).hits

    assert [hit.turn.line for hit in hits] == sorted(hit.turn.line for hit in hits)
    assert all(hit.context == () for hit in hits)


def test_negative_context_is_rejected(transcript: Path):
    with pytest.raises(ValueError):
        scan([transcript], "sqlite", speakers=ALL_SPEAKERS, context=-1)


def test_a_term_nothing_matches_returns_nothing(transcript: Path):
    assert scan([transcript], "postgres", speakers=ALL_SPEAKERS).hits == ()


def test_matching_ignores_case(transcript: Path):
    assert scan([transcript], "SQLite", speakers=frozenset({"human"})).hits != ()
