# CLI Output Contract

**Version 2.2** — rule 3 gained a capped-count clause on 2026-08-22 (GAPS U6). Version 2.1 —
rule 6 gained a partial-corpus clause on 2026-08-16 (GAPS O10). `bm path`'s
exception was documented on 2026-08-17; it adds a verb, not a rule change, so the version holds.
Version 2 dates from 2026-08-10. This contract governs every `bm` command's output. Version 1 mandated
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
   unknown says `more results available` as a notice. **When the verb caps the listing and knows
   the true count, it prints both** — `N results, showing M` — and a section heading over the same
   listing says it the same way, `(N, showing M)`. A bare `M results` under a cap is the count of
   the cap wearing a corpus count's clothes, which is the one thing "counts are honest" forbids
   (GAPS U4, U6). When nothing was cut, the plain form stands.
4. **Notices, then affordances, after the payload**, each on its own line, on stdout. A notice
   states a condition (`3 files not indexed — invisible to search until reindex`); an affordance
   names the next command (`Run 'bm reindex' to index them.`). They never interrupt or precede the
   payload. **At most two corpus notices per command**, highest priority first (GAPS W8): more than
   two is a report, and `bm doctor` is the report. A notice never changes the exit code — corpus
   state is content, not an addressing failure (rule 5). One documented exception, below: a verb
   whose whole payload is a single machine-consumed value carries neither notices nor affordances.
5. **Empties are results** — a line saying nothing matched (`0 results`, `No drift detected`),
   exit 0. The dividing line is **addressing vs. content**: a request that cannot be scoped
   (unknown project, invalid flags) is a failure; a well-scoped request whose answer is "nothing
   there" is a result.
6. **Errors exit 1**, message on its own line, on stderr. Nothing else is written to stdout on the
   error path. **One exception — a partial-corpus failure:** a verb that read most of its input and
   lost a named part of it prints the payload it did get, names each unreadable part on stderr, and
   still exits 1, because the exit code rather than a missing payload is what says the run failed
   (GAPS O10).
7. **`--quiet` drops notices and affordances**, leaving the payload alone. Verbs that emit no
   notices need not offer it.

## Path verbs — the one documented exception to rules 3, 4, and 7

**`bm path <id>` prints one absolute path and nothing else.** No count line, no notices, no
affordances, and therefore no `--quiet` to turn them off.

The reason is the verb's only use: `$EDITOR "$(bm path finding-q8w3e1r5)"`. A command substitution
takes every line of stdout, so a count line or a next-step hint would be passed to the editor as a
file name. A verb whose output is an argument cannot carry commentary.

This exception belongs to **verbs whose whole payload is a single machine-consumed value**, and
`bm path` is the only one today. It does not widen rule 6: a failure is still one line on stderr
with exit 1 and nothing on stdout. Decided 2026-08-17 (VERBS_PLAN D9); the user may revisit.

## Content rules (carried from v1)

- **Counts are honest.** A count is printed when known and omitted when not — never a sentinel.
- **Field values are plain** — dates as ISO-8601, no Python reprs, no Rich markup in values.
- **Raw content is byte-exact.** A verb whose payload is file content (`bm show`, `read-note`)
  writes it verbatim — round-tripping is part of the contract. Anything derived from that content
  — `bm show`'s "superseded by" line — follows it as a notice under rule 4, so both rules hold
  together (VERBS_PLAN D10).
