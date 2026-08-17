"""`bm mine` — read Claude Code transcripts with the speaker attributed correctly (GAPS W1).

This verb **parses; it does not judge.** It emits turns and makes no claim that
any of them is a decision (GAPS W1, decided 2026-08-06): classification is
structural and is the thing agents reliably get wrong, while deciding what
counts as a decision is judgment. An agent reads this output, judges, and writes
any keeper with the normal write path, so the vocabulary rules, `source:`, and
the git history all apply with no special case.

Each row's first column is a `date-ref` — `<session-id>#L<line>` — which is the
citation a record carries back to the conversation it came from. The line number
is the physical line, which occasionally holds more than one record, so two rows
can share a ref.

A corpus with a damaged line still mines. The payload prints, every unreadable
line is named on stderr, and the run exits 1 — the single case where the output
contract allows both (GAPS O10, `docs/OUTPUT_CONTRACT.md` rule 6).

Imports stay narrow: nothing here touches the database, the API, or the MCP tool
layer. The verb reads files and nothing else.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

import typer

from basic_memory.cli.app import app

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.mine.search import Hit
    from basic_memory.mine.turns import Speaker, Turn

# A row is a pointer, not a transcript. Long turns are cut here so one hit stays
# one line (output contract rule 1); `--context` is how a caller reads around a
# hit that looks promising.
TEXT_WIDTH = 200
ELLIPSIS = "…"

# Two spaces between columns, so values line up without box drawing.
COLUMN_GAP = 2

# The fractional-seconds part of an ISO-8601 stamp, and only that part: the
# offset that may follow it has to survive.
FRACTIONAL_SECONDS = re.compile(r"\.\d+")

# What `--speaker` accepts. The three names are the question a caller actually
# asks; the finer classes the parser assigns (thinking, tool_use, meta,
# attachment, other) are reachable through `all` and are labelled in the
# speaker column, so nothing is hidden behind a coarse name.
SPEAKER_CHOICES = ("human", "assistant", "all")


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- Render ---


def compact(text: str) -> str:
    """One line, whitespace collapsed, cut to width.

    Transcript turns are multi-line by nature and a single line in the corpus
    has been measured at 1.2 MB. Collapsing here is what keeps rule 1 — one
    record per line — true in practice rather than in principle.
    """
    single = " ".join(text.split())
    if len(single) <= TEXT_WIDTH:
        return single
    return single[: TEXT_WIDTH - 1] + ELLIPSIS


def short_timestamp(timestamp: str | None) -> str:
    """ISO-8601 with fractional seconds dropped, or `-` when there is no time.

    Only the fraction goes. An earlier cut took everything after the dot, which
    also ate the UTC offset on a `...09:00:00.100+02:00` stamp and moved the
    turn two hours — a wrong time is worse than a long one.

    The metadata line types carry no timestamp at all. Printing `-` says that
    plainly; inventing one would be the fabrication this verb exists to prevent.
    """
    if timestamp is None:
        return "-"
    return FRACTIONAL_SECONDS.sub("", timestamp)


def render_rows(hits: list[Hit]) -> list[str]:
    """One line per hit: ref, time, speaker, text — identifier first (rule 2).

    Context turns, when asked for, follow their hit indented and marked with the
    line number they came from, so a context line can never be mistaken for a
    hit of its own.
    """
    ref_width = max((len(hit.ref) for hit in hits), default=0)
    time_width = max((len(short_timestamp(hit.turn.timestamp)) for hit in hits), default=0)
    speaker_width = max((len(hit.turn.speaker) for hit in hits), default=0)

    lines: list[str] = []
    for hit in hits:
        lines.append(
            f"{hit.ref:<{ref_width + COLUMN_GAP}}"
            f"{short_timestamp(hit.turn.timestamp):<{time_width + COLUMN_GAP}}"
            f"{hit.turn.speaker:<{speaker_width + COLUMN_GAP}}"
            f"{compact(hit.turn.text)}"
        )
        lines.extend(
            f"    L{turn.line} {turn.speaker}  {compact(turn.text)}" for turn in hit.context
        )
    return lines


def other_speaker_summary(skipped: list[Turn]) -> str:
    """A notice naming what the speaker filter held back, by speaker.

    Rule 4: a notice states a condition. The condition here is that the corpus
    answered more than the caller asked to see — worth saying, because the
    whole point of the classifier is that `tool_result` turns are not human
    speech and a caller may not expect how many of them there are.
    """
    counts: dict[str, int] = {}
    for turn in skipped:
        counts[turn.speaker] = counts.get(turn.speaker, 0) + 1
    breakdown = ", ".join(f"{count} {speaker}" for speaker, count in sorted(counts.items()))
    return f"{len(skipped)} more turns matched other speakers — {breakdown}."


def affordances(term: str) -> list[str]:
    """The static next-step list (GAPS W19 item 5): no conditions, no memory.

    The list is fixed per verb — no ordering logic and no memory of what was
    already printed, because `bm` holds no session state and adding some to
    suppress three lines is the wrong trade. `--quiet` is the only condition.

    Every entry names a command that exists today. W19's own illustration used
    `bm new`, which has not shipped; pointing an agent at a verb that answers
    "no such command" would teach the surface wrongly, which is the opposite of
    what an affordance is for.
    """
    steps = (
        (f'bm mine "{term}" --context 2', "read the turns around each hit"),
        (f'bm mine "{term}" --speaker all', "include tool results and harness turns"),
        ("bm tool write-note", "record what you judged worth keeping"),
    )
    width = max(len(command) for command, _ in steps)
    return ["next:", *(f"  {command:<{width + COLUMN_GAP}}{purpose}" for command, purpose in steps)]


# --- Verb ---


@app.command()
def mine(
    term: Annotated[
        str,
        typer.Argument(help="Text to look for. Matched anywhere in a turn, ignoring case."),
    ],
    directory: Annotated[
        Optional[Path],
        typer.Option(
            "--dir",
            help="Transcript directory to read. Defaults to this directory's own transcripts.",
        ),
    ] = None,
    speaker: Annotated[
        str,
        typer.Option(
            "--speaker",
            help="Whose turns to show: human, assistant, or all.",
        ),
    ] = "human",
    context: Annotated[
        int,
        typer.Option(
            "--context",
            "-C",
            min=0,
            help="Show this many turns either side of each hit.",
        ),
    ] = 0,
    include_subagents: Annotated[
        bool,
        typer.Option(
            "--include-subagents",
            help="Also read sub-agent transcripts. Their turns are not yours.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the status lines and next-step hints."),
    ] = False,
) -> None:
    """Find where a term was said in this project's Claude Code transcripts.

    Each row starts with the reference a record cites it by. A turn recorded
    with the user's role is often a tool result rather than something a person
    said, so the speaker column reports what the turn really is.
    """
    # Deferred so `bm --help` and every other verb pay nothing for this one.
    from basic_memory.mine.locate import TranscriptError, transcript_files, transcripts_dir_for_cwd
    from basic_memory.mine.search import scan
    from basic_memory.mine.turns import SPEAKERS

    if speaker not in SPEAKER_CHOICES:
        raise fail(f"Error: --speaker must be one of {', '.join(SPEAKER_CHOICES)}")

    if directory is not None:
        source = directory.expanduser()
    else:
        source = transcripts_dir_for_cwd(Path.cwd())

    try:
        paths = transcript_files(source, include_subagents=include_subagents)
    except TranscriptError as exc:
        raise fail(f"Error: {exc}")

    # Scan every speaker, then filter at render time. The extra work is free —
    # the file has to be read either way — and it is what lets the notice say
    # how many turns the filter held back instead of hiding them.
    selected: frozenset[Speaker] = frozenset(SPEAKERS)
    report = scan(paths, term, speakers=selected, context=context)

    wanted = set(SPEAKERS) if speaker == "all" else {speaker}
    shown = [hit for hit in report.hits if hit.turn.speaker in wanted]
    skipped = [hit.turn for hit in report.hits if hit.turn.speaker not in wanted]

    for line in render_rows(shown):
        typer.echo(line)

    # Rule 3: the count closes the listing. Rule 5: nothing found is a result.
    typer.echo(f"{len(shown)} turns")

    if not quiet:
        if skipped:
            typer.echo(other_speaker_summary(skipped))
        if not paths:
            typer.echo(f"No transcripts in {source}.")
        for line in affordances(term):
            typer.echo(line)

    # Trigger: at least one physical line lost a record.
    # Why: R-O1 requirement 4 — count them, report them, exit nonzero. The
    #     payload still prints, because 3 of 61 real project directories hold a
    #     damaged line and refusing to mine them at all serves nobody (GAPS
    #     O10). This is the one case the output contract allows a payload and a
    #     non-zero exit together, so the report goes to stderr where diagnostics
    #     belong and `--quiet` cannot hide it.
    # Outcome: every bad line is named, and the run fails.
    if report.damage:
        for bad in report.damage:
            typer.echo(f"Error: {bad}", err=True)
        count = len(report.damage)
        plural = "line was" if count == 1 else "lines were"
        typer.echo(f"Error: {count} unreadable {plural} skipped", err=True)
        raise typer.Exit(1)
