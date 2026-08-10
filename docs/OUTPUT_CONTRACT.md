# CLI Output Contract

**Version 2** — 2026-08-10. This contract governs every `bm` command's output. Version 1 mandated
a `--json` mode; v2 removes it (GAPS W20, decided 2026-08-06): every consumer of `bm` output is an
agent, and JSON spends tokens on braces, quotes, and repeated keys to encode structure an agent
reads from columns just as reliably (measured 2026-08-10 — the v1 envelope for a 5-record search
ran 1151 tokens against 202 for the v2 columns; see GAPS W20). The contract therefore specifies
**rules, not a serialization**, and each verb has exactly **one rendering** — no `--json`, no
`--plain`, no TTY detection, no output-style config. The version number bumps only on a breaking
change to these rules, recorded here.

## Streams

- **stdout carries the payload**, followed by notices and affordances (rule 4). No banners, no
  progress, no box-drawing, no ANSI styling that survives a pipe.
- **stderr carries errors and diagnostics.**

## The rules

1. **One record per line** where records are listed. Fixed column order per verb, alignment only —
   no box-drawing tables. Single records render as labelled lines; grouped reports (e.g. `doctor`)
   render as sections with plain headings.
2. **Identifier first.** The record's identifier (permalink, name, key, path) is the first column,
   so it is findable without counting columns.
3. **A count on its own line at the end** of a record listing (`N results`), or nothing when the
   count is unknown — absence *is* the signal; never a sentinel. Pagination when the count is
   unknown says `more results available` as a notice.
4. **Notices, then affordances, after the payload**, each on its own line, on stdout. A notice
   states a condition (`3 files not indexed — invisible to search until reindex`); an affordance
   names the next command (`Run 'bm reindex' to index them.`). They never interrupt or precede the
   payload.
5. **Empties are results** — a line saying nothing matched (`0 results`, `No drift detected`),
   exit 0. The dividing line is **addressing vs. content**: a request that cannot be scoped
   (unknown project, invalid flags) is a failure; a well-scoped request whose answer is "nothing
   there" is a result.
6. **Errors exit 1**, message on its own line, on stderr. Nothing else is written to stdout on the
   error path.
7. **`--quiet` drops notices and affordances**, leaving the payload alone. Verbs that emit no
   notices need not offer it.

## Content rules (carried from v1)

- **Counts are honest.** A count is printed when known and omitted when not — never a sentinel.
- **Field values are plain** — dates as ISO-8601, no Python reprs, no Rich markup in values.
- **Raw content is byte-exact.** A verb whose payload is file content (`read-note`) writes it
  verbatim — round-tripping is part of the contract.
