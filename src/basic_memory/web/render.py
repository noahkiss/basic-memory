"""Markdown → HTML for the record page, and the frontmatter split above it.

A record's file is YAML frontmatter followed by a markdown body. `bm show`
echoes the bytes, because a terminal has nothing better to do with them; a
browser does, so this is the one place in the tree that turns a record into
HTML.

Three constraints, each a decision rather than a default:

- **Raw HTML in a note is never trusted.** `MarkdownIt("commonmark",
  {"html": False})` escapes it instead of passing it through. A record body is
  content the tool wrote or a human typed, but it is also content an agent
  wrote, and a board that renders `<script>` from a note body would make every
  note an injection vector into the operator's browser.
- **The frontmatter comes back as data, not as text.** The record page prints it
  as a table, and a table needs the keys, so the split returns a mapping rather
  than a rendered block.
- **Wikilinks become links.** `[[record-id]]` is how records point at each other
  in a body, and a board where those are dead text would make the graph
  unwalkable exactly where walking it is easiest.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import yaml

# `[[target]]` or `[[target|label]]`. Deliberately narrow: no brackets and no
# pipe inside either half, so a malformed link stays literal text rather than
# swallowing the rest of the line.
_WIKILINK = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]|]+?))?\]\]")

# An inline code span, longest run of backticks first so ``a `b` c`` matches as
# one span. Wikilinks inside one are documentation about the syntax, not links.
_CODE_SPAN = re.compile(r"(`+[^`]*`+)")

# A fenced code block's delimiter. Same reason as the code span, one line at a
# time — the pre-pass is line-oriented, so the fence state is too.
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

# What separates the YAML header from the body. Column 0 only: a `---` further
# in is a thematic break in the body, not a delimiter.
_FRONTMATTER_DELIMITER = "---"


@dataclass(frozen=True, slots=True)
class ParsedNote:
    """One record file, split into the two things the page renders separately."""

    metadata: Mapping[str, Any]
    body: str


def split_frontmatter(text: str) -> ParsedNote:
    """Separate a record's YAML header from its markdown body.

    A file with no header, an unterminated header, or a header that is not a
    YAML mapping yields an empty mapping and the whole text as body. That is not
    a silent fallback: the page's job is to show what is on disk, and a record
    whose frontmatter will not parse still has a body worth reading. `bm doctor`
    is what reports a malformed record.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != _FRONTMATTER_DELIMITER:
        return ParsedNote(metadata={}, body=text)

    for index in range(1, len(lines)):
        if lines[index].rstrip() != _FRONTMATTER_DELIMITER:
            continue
        header = "\n".join(lines[1:index])
        body = "\n".join(lines[index + 1 :])
        try:
            loaded = yaml.safe_load(header)
        except yaml.YAMLError:
            return ParsedNote(metadata={}, body=body)
        return ParsedNote(
            metadata=loaded if isinstance(loaded, Mapping) else {},
            body=body,
        )

    # An opening delimiter with no closing one: the whole file is body.
    return ParsedNote(metadata={}, body=text)


def link_wikilinks(markdown_text: str) -> str:
    """Rewrite `[[record-id]]` into a markdown link at `/r/<record-id>`.

    A source-level rewrite rather than emitted HTML: the renderer then escapes
    the label for us, so a record id containing markup cannot reach the page as
    markup. The route the link points at resolves the id across every project,
    which is why the href carries no project segment.

    Code spans and fenced blocks are left alone — a wikilink shown inside them
    is documentation about the syntax, and turning it into a link would make the
    example wrong.
    """
    rendered: list[str] = []
    fence: str | None = None
    for line in markdown_text.splitlines():
        opened = _FENCE.match(line)
        if fence is not None:
            # Inside a fence: only a matching delimiter closes it, everything
            # else passes through untouched.
            if opened is not None and opened.group(1)[0] == fence[0]:
                fence = None
            rendered.append(line)
            continue
        if opened is not None:
            fence = opened.group(1)
            rendered.append(line)
            continue
        # `re.split` on a capturing group keeps the code spans in the list, at
        # the odd indexes, so only the prose halves are rewritten.
        parts = _CODE_SPAN.split(line)
        rendered.append(
            "".join(
                part if index % 2 else _WIKILINK.sub(_as_link, part)
                for index, part in enumerate(parts)
            )
        )
    return "\n".join(rendered)


def _as_link(match: re.Match[str]) -> str:
    target = match.group(1).strip()
    label = (match.group(2) or match.group(1)).strip()
    # Neither half can hold a bracket or a pipe — the pattern forbids both — so
    # the markdown link this builds cannot be reopened by its own contents.
    return f"[{label}](/r/{quote(target, safe='')})"


def render_body(markdown_text: str) -> str:
    """Render a record's markdown body to HTML, wikilinks resolved.

    CommonMark rather than the GFM preset: the fork's own bodies are plain
    markdown, and every extension is more surface for a note to surprise a
    reader with. `html: False` is the security decision above.
    """
    # Deferred with the rest of the web package's leaf imports: nothing on the
    # fast CLI path may pull the markdown renderer at import time.
    from markdown_it import MarkdownIt

    parser = MarkdownIt("commonmark", {"html": False})
    return parser.render(link_wikilinks(markdown_text))
