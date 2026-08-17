"""The turn classifier — who actually spoke (GAPS W1).

This module is the reason `bm mine` exists. Search over transcripts is easy;
attribution is what agents get wrong, and they get it wrong in a way that
produces a citation with a real timestamp attached to something no person said.

**`role: user` does not mean the human spoke.** Re-surveyed 2026-08-16 over a
real projects tree — 1,919 transcripts, 464,253 non-empty lines, classified
through this module:

| `type: user` line shape       | count  |
|-------------------------------|--------|
| a `tool_result`               | 85,434 |
| harness injection (see below) |  5,839 |
| genuine human text            |  5,417 |

So **94% of `type: user` lines were written by something other than the
person**, and 88% are tool output. A miner that trusts the role field cites
`File created successfully at:` as the moment a decision was made.

A second injected class the original gap entry did not name, found in the same
survey: `type: user` lines that are not tool results and still are not human —
`isMeta: true` records, slash-command envelopes (`<command-name>`,
`<command-message>`), captured command output (`<local-command-stdout>`,
`<bash-input>`, `<bash-stdout>`), injected `<system-reminder>` blocks, the
`[Request interrupted...]` marker the harness writes when a user stops a turn,
and multi-agent traffic (`<teammate-message>`, `<task-notification>`). These are
`meta`, never `human`.

**Every parse failure is counted and reported, never silent** (GAPS W1
requirement 4, from R-O1: *count them, report them, exit nonzero*). Losing a
line quietly is exactly how R-O1 went undiagnosed for a session.

What the re-survey changed is the *response*, not the rule. The failure rate is
not zero — 12 lines in that same survey — and those 12 sit in 3 of 61 project
directories, so aborting on the first one made those directories unmineable
rather than degraded. A damaged line now yields a `BadLine`, the caller names
every one of them and exits 1, and the rest of the corpus still reads. See
GAPS O10.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

# A decoded transcript record, or one block inside it. The values really are
# unconstrained — `content` is a string here and a list of blocks there — so
# `object` would only force a narrowing cast at every read without making any
# of those reads safer. Every read goes through `_string` or `_dict_items`,
# which do the narrowing once.
type JsonObject = dict[str, Any]

type Speaker = Literal[
    "human",
    "assistant",
    "thinking",
    "tool_use",
    "tool_result",
    "meta",
    "attachment",
    "other",
]

SPEAKERS: tuple[Speaker, ...] = get_args(Speaker.__value__)

# A `type: user` line whose text opens with one of these was written by the
# harness, not by the person at the keyboard. Counts from the corpus survey are
# in the module docstring; each of these was observed there.
INJECTED_PREFIXES: tuple[str, ...] = (
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<bash-input>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<system-reminder>",
    "<user-prompt-submit-hook>",
    "<ide_",
    "[Request interrupted",
    # Multi-agent harness traffic. Both wear the user's role and neither is the
    # person: `<teammate-message>` is another agent writing to this one, and
    # `<task-notification>` is the harness reporting a sub-agent finished. They
    # are 1,449 of the 6,864 `human` turns on the tree this classifier was
    # re-surveyed against — the largest remaining misattribution.
    "<teammate-message",
    "<task-notification>",
)


@dataclass(frozen=True, slots=True)
class BadLine:
    """A physical line that lost at least one record.

    Carries the file and the 1-based line number, because the point of
    reporting damage is that the caller can go look at it. A bad line does not
    stop the read: the surviving records on it are still yielded, and the
    caller decides what a damaged corpus is worth (GAPS O10).
    """

    path: Path
    line: int
    reason: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} is not valid JSON ({self.reason})"


@dataclass(frozen=True, slots=True)
class Turn:
    """One transcript line, classified.

    ``session`` is the **file stem, not the record's `sessionId` field.** Every
    sub-agent transcript in the measured corpus (1,106 of 1,106) carries its
    *parent's* `sessionId`, while every main transcript (808 of 808) matches
    its stem. A `date-ref: <session-id>#L<line>` built from that field points at
    a file which does not contain that line, so the stem is the only identity
    that makes the reference resolvable.
    """

    session: str
    line: int
    timestamp: str | None
    speaker: Speaker
    text: str
    path: Path
    subagent: bool

    @property
    def ref(self) -> str:
        """The `date-ref` this turn supports: ``<session-id>#L<line>``."""
        return f"{self.session}#L{self.line}"


# --- Text extraction ---


def _string(block: JsonObject, key: str) -> str:
    """The value at ``key`` when it is a string, and empty otherwise.

    Every read of a decoded record goes through here, so a field that is
    missing, null, or the wrong shape becomes an empty string once rather than
    a `None` that travels into the output.
    """
    value = block.get(key)
    return value if isinstance(value, str) else ""


def _dict_items(value: object) -> list[JsonObject]:
    """The dict entries of a JSON list, ignoring whatever else is in it.

    The cast is the one place this module asserts what `json.loads` guarantees
    and the type checker cannot see: a decoded JSON object has string keys. It
    narrows nothing else — the `isinstance` above does the real work.
    """
    if not isinstance(value, list):
        return []
    return [cast(JsonObject, item) for item in value if isinstance(item, dict)]


def _blocks(record: JsonObject) -> list[JsonObject]:
    """The content blocks of a message record, or none if the content is a bare string."""
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    return _dict_items(message.get("content"))


def _message_string(record: JsonObject) -> str:
    """The content of a message recorded as a plain string, if it is one."""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _string(message, "content")


def _text_blocks(blocks: list[JsonObject]) -> str:
    return "\n".join(_string(block, "text") for block in blocks if block.get("type") == "text")


def _tool_result_text(blocks: list[JsonObject]) -> str:
    """Flatten tool results, whose inner content is a string or a block list.

    Observed inner shapes (re-survey 2026-08-16): str (79,933), list[text]
    (3,822), list[tool_reference] (1,308), list[image] (323), list[image,text]
    (72). Only the text-bearing ones contribute; the rest must not raise.
    """
    parts: list[str] = []
    for block in blocks:
        if block.get("type") != "tool_result":
            continue
        content = block.get("content")
        if isinstance(content, str):
            parts.append(content)
        else:
            parts.append(_text_blocks(_dict_items(content)))
    return "\n".join(part for part in parts if part)


def _tool_use_text(blocks: list[JsonObject]) -> str:
    """A tool call rendered as ``name arg=value``, so a call is searchable by name.

    The input is rendered as compact JSON rather than dropped: searching for the
    path a tool touched is a real question, and the alternative is a turn whose
    text is only the tool name.
    """
    parts: list[str] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        arguments = block.get("input")
        rendered = json.dumps(arguments, ensure_ascii=False) if arguments is not None else ""
        parts.append(f"{name} {rendered}".strip())
    return "\n".join(parts)


def _thinking_text(blocks: list[JsonObject]) -> str:
    return "\n".join(
        _string(block, "thinking") for block in blocks if block.get("type") == "thinking"
    )


def _attachment_text(record: JsonObject) -> str:
    """The searchable text of an attachment record.

    Re-survey 2026-08-16: ``attachment.content`` is a string (88,775), a block
    list (36,597), a dict (215), or absent (8,600); of the absent ones
    ``attachment.text`` covers 2,879. The dict form carries no text field worth
    reading, so it contributes nothing.
    """
    attachment = record.get("attachment")
    if not isinstance(attachment, dict):
        return ""
    content = attachment.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _text_blocks(_dict_items(content))
    return _string(attachment, "text")


# --- Classification ---


def _is_injected(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(INJECTED_PREFIXES)


def _classify_user(record: JsonObject, blocks: list[JsonObject]) -> tuple[Speaker, str]:
    """Split a `type: user` line into human speech, tool output, or harness noise.

    Order matters. Tool results are checked first because they are the bulk of
    the class and carry no human text at all. `isMeta` is checked before the
    text prefixes because it is the harness's own explicit marker, and a meta
    record need not open with a recognisable tag.
    """
    if any(block.get("type") == "tool_result" for block in blocks):
        return "tool_result", _tool_result_text(blocks)

    text = _message_string(record) or _text_blocks(blocks)
    if record.get("isMeta") is True or _is_injected(text):
        return "meta", text
    return "human", text


def _classify_assistant(blocks: list[JsonObject]) -> tuple[Speaker, str]:
    """Split an assistant line by the blocks it carries, not by the text it yields.

    Precedence is speech first: a line carrying prose is attributed to the
    assistant even when it also carries a tool call, because the prose is the
    part a reader is looking for. Reasoning is `thinking` rather than
    `assistant` so that a quote pulled from it is never presented as something
    the assistant said out loud.

    The branch order tests **which blocks are present**, never whether the text
    came out non-empty. A real thinking block usually has an empty `thinking`
    string — the reasoning is redacted and only the signature survives (46,915
    of 46,917 thinking blocks in the re-survey). Deciding on extracted text
    therefore fell through and labelled a third of all assistant lines
    `tool_use`, so a reasoning turn shown as context claimed to be a tool call.

    A tool call outranks a thinking block on the rare line carrying both,
    because the call has text a caller can search and the thinking usually
    does not.
    """
    text = _text_blocks(blocks)
    if text:
        return "assistant", text
    if any(block.get("type") == "tool_use" for block in blocks):
        return "tool_use", _tool_use_text(blocks)
    if any(block.get("type") == "thinking" for block in blocks):
        return "thinking", _thinking_text(blocks)
    return "other", ""


def classify(record: JsonObject) -> tuple[Speaker, str]:
    """Return the speaker of one transcript record and the text it carries.

    Records with no speech at all — `mode`, `ai-title`, `file-history-snapshot`,
    `queue-operation`, and the rest of the metadata types — are `other` with
    empty text. They are kept rather than dropped so line numbers stay true to
    the file, which is what makes `#L<line>` a reference someone can open.
    """
    kind = record.get("type")
    blocks = _blocks(record)
    if kind == "user":
        return _classify_user(record, blocks)
    if kind == "assistant":
        return _classify_assistant(blocks)
    if kind == "attachment":
        return "attachment", _attachment_text(record)
    return "other", ""


# --- Reading ---

# Where a JSON object starts. Used only to walk forward out of a torn record;
# the normal path never looks for it.
RECORD_START = '{"'

# How many candidate restarts the recovery below will try on one line. Each
# attempt decodes from that offset, so an unbounded search over a line measured
# at 1.2 MB would be quadratic. Every real torn line recovers within a handful
# of candidates, so the cap only ever fires on damage far worse than observed.
MAX_RECOVERY_ATTEMPTS = 64


def _is_record(value: object) -> bool:
    """Does this decoded value look like a transcript record rather than a fragment?"""
    return isinstance(value, dict) and "type" in value


def _resume_offset(raw: str, after: int) -> int:
    """Find where the next whole record starts on a torn line, or -1.

    A candidate qualifies only if it decodes **and runs to the end of the
    line**. That test is what separates a real record start from a `{"` that
    was merely nested inside the torn prefix: a nested content block decodes
    fine but leaves a tail behind it, and the surviving record never does.

    Two consequences, both preferred to a looser test that would mistake a
    content block for a record:

    - Only the record that reaches the end of the line is recovered. A line
      carrying a tear *and* two intact records after it therefore keeps the
      last one and loses the middle one. That shape has never been observed —
      all 12 damaged lines in the measured tree hold exactly one survivor — and
      the line is still reported as damaged either way, so nothing is lost
      without the caller hearing about it.
    - When the attempt cap fires, the rest of the line goes unread and is
      reported as the one loss the line already carries.
    """
    decoder = json.JSONDecoder()
    candidate = raw.find(RECORD_START, after)
    attempts = 0
    while candidate != -1 and attempts < MAX_RECOVERY_ATTEMPTS:
        attempts += 1
        try:
            value, end = decoder.raw_decode(raw, candidate)
        except json.JSONDecodeError:
            candidate = raw.find(RECORD_START, candidate + 1)
            continue
        if not raw[end:].strip() and _is_record(value):
            return candidate
        candidate = raw.find(RECORD_START, candidate + 1)
    return -1


def records_in_line(raw: str) -> tuple[list[JsonObject], list[str]]:
    """Every whole record on one physical line, plus a reason for anything lost.

    Two shapes make a line hold more than one record, and both are Claude Code
    dropping a newline between writes:

    - **Clean glue** — one record ends and the next begins, `...}{"parentUuid"`.
      The decode loop takes them in order and nothing is lost.
    - **A torn record** — the first record is cut off mid-value and the next is
      concatenated onto the wound. This is the shape that actually occurs: all
      12 damaged lines in the surveyed tree are torn, and none contains a
      `}{` at all. The torn prefix is unrecoverable — its bytes are simply not
      there — so it is reported, and the intact record after it is recovered.
      That is 12 recoveries of 12.
    """
    decoder = json.JSONDecoder()
    records: list[JsonObject] = []
    losses: list[str] = []
    position = 0
    length = len(raw)

    while position < length:
        while position < length and raw[position].isspace():
            position += 1
        if position >= length:
            break

        try:
            value, end = decoder.raw_decode(raw, position)
        except json.JSONDecodeError as error:
            losses.append(f"column {position + 1}: {error.msg}")
            resume = _resume_offset(raw, position + 1)
            if resume == -1:
                break
            position = resume
            continue

        if _is_record(value):
            records.append(cast(JsonObject, value))
        else:
            losses.append(f"column {position + 1}: not a transcript record")
        position = end

    return records, losses


def read_turns(path: Path) -> Iterator[Turn | BadLine]:
    """Yield every record of one transcript, classified, in file order.

    Line numbers are 1-based and count **every** line including blank ones, so
    they match what an editor shows. **A physical line can hold more than one
    record**, so `#L<line>` addresses the line and may name several turns; the
    alternative — renumbering — would make a reference disagree with an editor.

    Damage is yielded as a `BadLine` rather than raised. R-O1 requirement 4 asks
    that parse failures be counted, reported, and exited nonzero, and all three
    survive: the caller reports every `BadLine` and exits 1. What does not
    survive is aborting the directory, because 3 of 61 real project directories
    hold a damaged line and stopping there makes the other 463,000 lines
    unreadable for the sake of one (GAPS O10).
    """
    session = path.stem
    subagent = "subagents" in path.parts

    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue

            records, losses = records_in_line(raw)
            for reason in losses:
                yield BadLine(path=path, line=number, reason=reason)

            for record in records:
                speaker, text = classify(record)
                timestamp = record.get("timestamp")
                yield Turn(
                    session=session,
                    line=number,
                    timestamp=timestamp if isinstance(timestamp, str) else None,
                    speaker=speaker,
                    text=text,
                    path=path,
                    subagent=subagent or record.get("isSidechain") is True,
                )
