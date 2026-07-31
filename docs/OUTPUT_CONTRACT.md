# CLI Output Contract

**Version 1** — 2026-07-31. This contract governs every `bm` command's machine-facing output. It
exists because `bm` is consumed by scripts and agents throughout (the work-tracking verbs are
machine consumers of `bm` themselves), and a surface where exit codes never signal failure and the
JSON stream is not reliably JSON is unbuildable-on (GAPS O8/W7). New commands must conform; the
version number bumps only on a breaking change to these rules, recorded here.

## Streams

- **stdout carries the payload and nothing else.** In `--json` mode, stdout is exactly one
  parseable JSON document — no banners, no progress, no prose, no ANSI escapes.
- **stderr carries diagnostics**: human-readable error messages, warnings, progress. Rich/color
  formatting is permitted on stderr and in TTY text mode only, never inside JSON.

## Exit codes

- **Exit 0 — the command did its job.** This includes legitimately empty answers: no search hits,
  no schemas defined, no notes of the requested type, no schema for the requested type, no drift,
  no inferable pattern. An empty corpus is a state to report, not a fault.
- **Exit 1 — the command could not do its job**: malformed invocation, a project that does not
  resolve, an I/O or database error, an unexpected exception. In `--json` mode a failure still
  emits a JSON error object on stdout (the stream stays parseable) *and* a diagnostic on stderr.

The dividing line: **addressing vs. content.** A request that cannot be scoped (unknown project,
invalid flag combination) is a failure. A well-scoped request whose answer is "nothing there" is a
result. Precedent: `bm doctor --project UNKNOWN` exits 1; `bm schema infer` over notes with no
common pattern exits 0 (GAPS T4, O5).

## JSON shapes

- **The `error` key is reserved.** A top-level `"error"` appears if and only if the command failed,
  and always accompanies exit 1. No payload may use `error` for anything else.
- **Legitimate empties keep the normal report shape**, with a top-level `reason` string explaining
  why the answer is empty (e.g. `"suggested_schema": null` plus
  `"reason": "No schema pattern found …"`; `"schema_found": false` plus
  `"reason": "No schema found for type 'person'"`). Callers branch on the data fields; `reason`
  is display text.
- **Counts are honest.** `total` is an integer when the count is known and `null` (or absent —
  serializers that drop nulls may omit it) when it is not. Never a sentinel value. There is no
  companion exactness flag: absence of the count *is* the signal. Pagination when the count is
  unknown uses `has_more`.
- Field values are JSON-native — dates as ISO-8601 strings, no Python reprs.

## Coverage

Any command intended for scripted use must offer `--json`. Text/rich rendering is a view over the
same data the JSON mode emits, never a different answer.
