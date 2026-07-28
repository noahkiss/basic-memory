# GAPS — what this fork needs to fix

**This file is the running list of everything wrong with, or missing from, upstream Basic Memory
that our work depends on. It is the fork's to-do list and its rationale.**

## The rule

**When you find a gap, write it here in the same session you find it.** Do not leave it in a
design doc, a STATUS file, or a session summary and intend to transfer it later — that transfer is
the return visit that never happens. A gap recorded only in `~/develop/.design/` is invisible to
anyone working in this repo, which is where it has to get fixed.

A gap belongs here if it is a thing **we would change in this codebase**. Design decisions about
the record schema live in `~/develop/.design/status-system-schema-draft.md`; findings about the
work *plan* live in `~/develop/.design/status-system-plan.md`. This file is only about the code.

Each entry gets: what breaks, the evidence (a command and its actual output, not a description of
it), why it matters to us, and where it was found. Evidence matters because several figures in the
design docs turned out to be inherited and never re-checked; an entry without a reproduction is a
claim, not a gap.

**Order of work:** fix the gaps that block the thing being built next, then build. Not the reverse.

---

## Legend

| | |
|---|---|
| **SHIPPED** | Fixed in this fork, commit noted |
| **RESOLVED** | Investigated and closed without a code change — kept because the diagnosis is load-bearing |
| **BLOCKER** | Must be fixed before the dependent work can be correct |
| **TRAP** | Works, but fails silently or misleads — high cost, easy to hit |
| **WANT** | Missing capability we intend to add |
| **OPEN** | Observed, not yet diagnosed |

---

## SHIPPED

### S1 — FastEmbed model cache was per-config-dir
**Commit `58d94d38`.** Upstream cached the 64 MB `qdrant/bge-small-en-v1.5-onnx-q` model inside
`$BASIC_MEMORY_CONFIG_DIR/fastembed_cache/`, so every config dir paid its own download. The model is
an immutable artifact keyed by model name and does not belong inside the isolation boundary
`BASIC_MEMORY_CONFIG_DIR` exists to draw. Default moved to `$XDG_CACHE_HOME/fastembed`;
`FASTEMBED_CACHE_PATH` and `semantic_embedding_cache_dir` still override; an install with the model
already at the legacy path keeps using it rather than silently re-downloading. 551 tests pass.
See `default_fastembed_cache_dir()` in `src/basic_memory/config_models.py`.

---

## RESOLVED — investigated, no code defect

### R-O1 — "~25% of transcript grep hits do not parse as whole-line JSON" was a measurement artifact
**Opened 2026-07-26 as O1. Closed the same day. No defect in the transcripts, in `rg`, or in the
prefix split.**

The original `rg` was run against the Claude Code project *directories* with no `-g '*.jsonl'`
filter. A project directory is not a directory of transcripts — it is a transcript file plus a
sidecar tree (`tool-results/`, `subagents/`, hidden `.context-window-*.json`). The 12 "failures"
were plain Markdown lines from `tool-results/hook-*-stdout.txt` files.

```
$ cd /home/flight/.claude/projects
$ rg "mem_limit" $P | wc -l                    # the 4 pilot slugs, no glob filter
47
$ rg "mem_limit" -g '*.jsonl' $P | wc -l       # filtered to transcripts
35

=== NO -g filter (as originally measured) ===
hit lines: 47  parsed: 35  failed: 12  (25.5%)

=== WITH -g '*.jsonl' ===
hit lines: 35  parsed: 35  failed: 0  (0.0%)
```

47 − 35 = 12, exactly the recorded failure count, and 25.5% is exactly the recorded "~25%". The
reproduction is exact, not approximate. The 12 are **2 distinct Markdown lines × 6 duplicated
captures** — the same auto-injected `STATUS.local.md` (which happens to discuss Docker `mem_limit`)
captured into six sessions' hook stdout.

**The decisive check:** for all 12, the bytes `rg` emitted are byte-identical to the file's line
(`identical: True`), and the line fails `json.loads` **read directly from the file too**, with
`Expecting value: line 1 column 1 (char 0)` — the error you get when the first character is `*` or
`-`. The pipeline is innocent. That data was never JSON, because the file was never a transcript.

Ruled out with corpus-scale evidence:

```
H1 multiline/malformed JSONL: 126 files, 34602 non-empty lines, 0 not valid whole-line JSON, 0 non-UTF8
   longest .jsonl line in pilot corpus: 1,241,093 bytes
H2 rg fidelity on 'decided': 165 hit lines, 0 differ from file bytes, 0 json.loads failures,
   longest hit line 634,573 bytes
H3 with --max-columns 200: 165 lines, 165 json.loads failures
   sample truncated line: ./…/32077dfd-….jsonl:27:[Omitted long matching line]
H4 paths containing ':': 0
H5 naive split(':') no maxsplit, first 50 hits: 50 failures (vs 0 with maxsplit=2 over all 165)
```

Oversized lines, multiline records, non-UTF8 bytes, `rg` truncation, and colons in paths are all
ruled out. Default `rg` reproduced a 634 KB line byte-perfectly. **The true parse rate is 0 failures
in 34,602 lines.**

**This closes an OPEN entry without closing any work.** O1 was an artifact sitting on top of W1's
real problem, not a second problem — see W1, which carries the four requirements this diagnosis
produced. Full working notes and the reproduction scripts (`o1_repro.py`, `o1_rule_out.py`) were
scratch artifacts; everything load-bearing is reproduced above and in W1.

### R-T6 — "a second frontmatter block is prepended to unindexed notes" does not exist in this tree

**Opened as T6 (inherited from the BM spike). Closed 2026-07-26. No defect at any point in this
fork's history — upstream fixed it before our fork point.**

The claim was silent data loss: edit a note that is on disk but not yet indexed, and 8 frontmatter
keys go in while `permalink:` alone comes out, exit 0. Retested against the fork build
(`uv run --project . bm`, editable install verified) with a hand-written 8-key note, never indexed,
no watcher — the state the defect requires:

```
$ bm status --project t6throw
│ └── 1 observed file is NOT indexed — invisible to search and read until 'basic-memory reindex'
$ bm tool edit-note notes/t6-probe --operation append --content "Appended line."
{"operation": "append", "fileCreated": false}   exit 0
```

One frontmatter block after the edit. **All 8 keys survived as authoritative frontmatter**, including
the invented `record-kind:`, `review-by:`, `owner:`. Same for `prepend` and `find_replace`.

Also retested on `2b19f1ff` (before this session's fix cluster) — **no corruption there either**,
which rules out "our change fixed it." The write path is byte-identical across the cluster:

```
$ git diff 2b19f1ff HEAD -- src/basic_memory/mcp/tools/edit_note.py src/basic_memory/markdown/ \
    src/basic_memory/services/file_service.py src/basic_memory/services/entity_service.py \
    src/basic_memory/file_utils.py
                                        # empty — none of the 13 changed src files is in the write path
```

**Nothing was reverted, because nothing was ever written for T6.** See the label-swap note in T3.

**What survives:** the *design* rule this defect motivated is unaffected and still stands —
schema-draft §8's "only `tend` may create or write files in the store, never `Write` then reach for a
BM tool." It now rests on the drop-in-files-are-not-indexed finding (see T2) rather than on this
corruption, which is a weaker but still sufficient basis. Keep the regression test in
`tests/mcp/test_tool_edit_note.py` (`7a09c015`) as a guard against reintroduction.

---

## TRAPS — silent failures, highest priority

These are ranked first because each one returns a plausible wrong answer rather than an error. In a
system whose entire purpose is to be a trustworthy record, a silent wrong answer is the worst
possible failure mode.

### T1 — `--meta` returns 0 instead of erroring on list-valued frontmatter
**Found:** 2026-07-26, schema §11 Q3 testing.

```
$ bm tool search-notes --meta supersedes=tnd_aaaa1111 --project q3test --json
{"results": [], "total": 0}          # note b-frontmatter-successor HAS supersedes: [tnd_aaaa1111]
$ bm tool search-notes --filter '{"supersedes":["tnd_aaaa1111"]}' --project q3test --json
{"total": 2}                          # correct
```

`--meta` does not match element-wise against a YAML list and does not say so. `total=0` is
indistinguishable from "no matches." **Fix:** either match element-wise, or reject the query with
"field `X` is list-valued, use `--filter`."

**Why it matters:** any agent or script that reaches for the obvious flag gets a confident empty
answer. We only caught it because a control query was run alongside.

**Amended 2026-07-26:** the same family, documented by upstream itself — booleans normalise to the
string `"True"` in metadata, so `--meta draft=true` and `{"draft": true}` both silently miss records
whose frontmatter carried a YAML boolean. Found in: sweep-prior-art.md:1.

### T2 — `bm status` reports files as observed that are not indexed
**Found:** 2026-07-26, same session.

```
$ bm status --project q3test
q3test: Project Index
└── 5 observed files
$ bm tool search-notes "supersede" --project q3test --json
{"results": [], "total": 0}           # every query, including controls
$ bm reindex --project q3test         # only now do queries work
```

Files written to a project directory by anything other than Basic Memory itself are counted as
"observed" while being absent from every read path. **Fix:** either index on observation, or have
`status` distinguish observed-but-unindexed and warn.

**Why it matters:** directly load-bearing. Any `tend` verb that writes by touching the filesystem
must reindex or its writes are invisible — and nothing in the tool tells you that.

**Amended 2026-07-26 — what that mandatory reindex costs:** the fork-point baseline measures a full
reindex + embed of 67 files at **81 s and 762 MB peak RSS**. The gardener and the migration both sit
downstream of this, on a corpus already at 53 files and growing, so "just reindex after writing" is
not a cheap workaround. Found in: sweep-status-agents.md:37, sweep-inv-plan.md:19.

### T3 — `bm --version` reports upstream's version, not the installed build
**Found:** at fork setup; re-confirmed 2026-07-26.

```
$ bm --version
Basic Memory version: 0.22.1          # wrong — this is upstream's number
$ uv tool list | grep basic-memory
basic-memory v0.22.2.dev118+232f2c2f  # the actual installed build (ours)
```

**Why it matters:** this is the specific trap the BM spike fell into — a stock install and our fork
both self-report `0.22.1`, so you cannot tell from `bm` alone whether you are testing our changes.
Every measurement taken without checking `uv tool list` is unattributable. **Fix:** report the real
distribution version.

**FIXED 2026-07-26 in `9e4f3c8c`** (`__init__.py`, `cli/app.py`, `tests/test_version.py`). Verified
at HEAD: `bm --version` → `Basic Memory version: 0.22.2.dev120+79dc916e`, and a source tree that is
not installed now says so explicitly rather than reporting a stale number.

> **LABEL SWAP — `git log` will send you to the wrong commit, in both directions.** `9e4f3c8c`'s
> message calls this work **T6**; it is T3. `7a09c015`'s message calls its frontmatter regression
> test **T3**; it is T6 (now R-T6). Both commits are pushed, so the messages cannot be corrected
> without rewriting published history — this note is the correction. Anyone auditing "was T3 fixed?"
> from the log alone lands on the wrong commit either way.

### T4 — dangling wikilink relations are stored silently
**Found:** 2026-07-26, schema §11 Q3 testing.

```
$ bm tool search-notes "supersedes" --entity-type relation --project q3test --json
  d-forward-ref/supersedes/z-does-not-exist     # target never existed
  g-id-link/supersedes/tnd-aaaa1111             # target is an id, not a permalink — never resolves
```

Storing the unresolved relation is *correct behaviour* — it is what makes forward references work,
and we depend on it. The gap is that **nothing ever reports them.** A permanently dangling edge is
indistinguishable from one whose target has not been written yet.

**Fix:** a lint/report verb (`bm tend check`, or extend `bm doctor`) listing unresolved relations,
ideally with age, so "not written yet" and "typo, will never resolve" can be told apart.

**Amended 2026-07-26 — the source of the dangling edges cannot be turned off.** Upstream's own docs:
"The `## Observations` and `## Relations` headings are conventional but not required — the parser
detects observations and relations by their syntax patterns anywhere in the document." Ordinary prose
(a `- [note] ...` bullet, an incidental `[[...]]`) becomes graph data with no opt-out, which is the
mechanism that manufactures these edges in the first place. Found in: sweep-prior-art.md:19.

### T5 — `bm project add` silently makes the new project the default
**Found:** 2026-07-26.

```
$ bm project add q3test <path>        # says only "added successfully"
$ bm project remove q3test
Error removing project: Cannot delete default project 'q3test'.
```

Adding a throwaway project silently repoints the default, so every subsequent unqualified command
targets it. **Fix:** don't change the default on add unless it is the first project, or say so
loudly. Given `tend` will be adding a project per tracked repo, this would otherwise repoint the
default on every single one.

**Amended 2026-07-26 — this is a CLI-surface omission, not a core one.** The MCP tool already takes
the choice as a parameter: `create_memory_project(project_name, project_path, set_default)`. The CLI
`project add` path exposes no equivalent, so the fix is most likely a flag on `project add` rather
than a service-layer change. Found in: sweep-status-agents.md:67.

### T6 — RESOLVED, no defect. See **R-T6** in the RESOLVED section.
The number is retained as a tombstone so existing cross-references do not silently retarget. Retested
against both the fork build and `2b19f1ff`: no corruption in either, and the write path is unchanged
across the whole fix cluster. Nothing was reverted because nothing was ever written for it.

### T7 — `search-notes` rejects an empty or `*` query; metadata-only queries need a `**` idiom
**Found:** BM spike, re-confirmed 2026-07-26.

```
$ bm tool search-notes "" --project X
Error: Hybrid retrieval requires a non-empty text query.
$ bm tool search-notes "**" --project X --permalink --filter '{"review-by":{"$lt":"2026-07-25"}}'
                                      # works — the only spelling that does
```

Every gardener and staleness query is metadata-only: it has a filter and no text term. The supported
spelling is an undocumented double-glob against `--permalink`, and the two obvious spellings
hard-error. Drop `--permalink` and the same `**` becomes a text search instead, silently.

**Why it matters:** this is the single query `tend gc` runs every cycle. An idiom nobody can discover
and nothing tests is one rebase away from regressing without a failure anyone notices.
**Fix:** accept an empty query when a `--filter` is present, and keep a test on the metadata-only path.

Found in: sweep-handoffs.md:1, sweep-spike.md:13, sweep-status-agents.md:19.

### T8 — `semantic_search_enabled` does not gate the embedding cost it advertises
**Found:** BM spike, 2026-07-26.

```
semantic search enabled : 3.32 s
semantic search disabled: 3.09 s     # the flag buys 0.23 s
```

This matches upstream issue **#886**, which measured 5 s → 4.8 s and states the cost "is a
module-level import, not gated by the flag."

**Why it matters:** distinct from B4, which names the import cost. This is a config knob that
advertises a behaviour it does not deliver, so anyone tuning latency through it concludes the
embedding stack is cheap and looks elsewhere. **Fix:** make the flag actually defer the import, or
have `config set semantic_search_enabled false` say plainly that it does not affect startup cost.

Found in: sweep-spike.md:19.

### T9 — the permalink is the only identity BM honours, and it is neither stable nor verbatim
**Found:** 2026-07-26, schema §12 testing and the BM spike. Three findings, one root cause.

```
# 1. permalinks embed the project name
fakerepo/decisions/nested-repo-for-local-history

# 2. wikilinks bind on permalink or title only — an opaque id in a custom field never resolves
[[tnd_aaaa1111]]   (with tnd-id: tnd_aaaa1111 on the target)
  -> dangling relation; never appears as an incoming edge on the target

# 3. the permalink is slugified: _ becomes -
tnd_aaaa1111 -> tnd-aaaa1111
```

**Evidence status:** findings 1 and 3 are structural and directly observable from any permalink.
Finding 2 comes from the same schema §12 comparison as O2 and O3 and its outcome was **recorded, not
captured** — reproduce it (write a note whose id lives only in a custom field, link `[[<id>]]` from a
second note, reindex, and check `build-context` on the target for the incoming edge) before treating
it as settled. T4's captured output shows dangling relations exist; it does not prove this is why.

Together these say: an id is only an edge target if the id **is** the permalink; a permalink is
rewritten wholesale when the project is renamed; and what you write is not necessarily what is
stored. All three fail with a success exit.

**Why it matters:** the store design is central and id-keyed, with the id authoritative and the
directory name a mere label — but BM bakes the project name into identity, so the label is load-bearing
after all. Renaming (measured at ~2×/year across 66 project dirs) therefore rewrites every permalink,
and the path must change in **both** the DB and `config.json` or B2's split registry drifts. The
slugification rule also constrains the `.tend.yml` id format before a line of code is written: ids
must be hyphenated, or every link silently targets a different string than the one written.

**Fix:** confirm whether a project rename updates permalinks in place, and in both registries; make
`id == permalink` a set-once invariant checked by `tend check` (see W5).

Found in: sweep-spike.md:7, sweep-schema.md:13, sweep-schema.md:19, sweep-schema.md:25,
sweep-localhist.md:61, sweep-status-agents.md:13, sweep-status-agents.md:31, sweep-decisions.md:7.

**AMENDED 2026-07-26 — captured at last, and two of the three findings were wrong as stated.**
Finding 2 is CONFIRMED with both halves (the half that was missing). Findings 1 and 3 hold only for
*derived* permalinks — the case the design does not use.

*Finding 2, now captured (negative + positive control).* An id in a custom field never resolves:

```
tnd-id: tnd-aaaa1111 on A, `- supersedes [[tnd-aaaa1111]]` on B
  -> b-links-to-custom-field/supersedes/tnd-aaaa1111   to_entity=None
permalink: tnd-hhhh8888 on C, `- supersedes [[tnd-hhhh8888]]` on D
  -> d-links-to-permalink/supersedes/tnd-hhhh8888      to_entity=tnd-hhhh8888
```

The link is *parsed and stored* and merely fails to resolve — so this is a resolution failure, not
an indexing failure. That distinction is why the positive control was required.

*Finding 3 (`_` → `-`) is FALSE for an explicit `permalink:`.* Verified directly in this session:

```
$ bm tool search-notes "**" --project vproj --permalink
      "permalink": "tnd_uuuu1111",      # written as tnd_uuuu1111 — UNDERSCORE PRESERVED
      "permalink": "tnd-vvvv2222",
```

Slugification and the project-name prefix of finding 1 apply **only to derived permalinks** (notes
with no `permalink:` field). An explicit permalink is stored byte-for-byte and carries no project
prefix. **§12's conclusion survives and is strengthened; only its stated reason was wrong.** Keep
ids hyphenated anyway — relation rows *are* slugified, so `_` and `-` targets collide into one row,
and `memory://` normalization makes underscore permalinks unreliable to address.

*The rename cost is BACKWARDS.* There is **no project rename verb at all** (`bm project` has
`list add remove default move set-cloud set-local ls info`), and `bm project move` is path-only —
it updates config and tells you to move the files yourself. DB and `config.json` agreed afterward,
so no B2 drift. A de-facto rename (`remove` + re-`add`) leaves **every permalink unchanged**,
because BM writes the derived permalink back into the note's frontmatter on first index, freezing
it. So permalinks are not "rewritten wholesale on rename" — they are frozen and silently retain a
dead project name, while links keep resolving. The ~2×/year × 66-dirs rewrite cost this entry used
to justify itself **does not exist**. `update_permalinks_on_move` (config_models.py:489, default
`False`) was NOT tested.

### T10 — `build-context` silently resolves a miss to an arbitrary note
**Found:** 2026-07-26, while capturing T9. **Verified twice, independently.**

A `memory://` URI that matches nothing returns a real note with `exit 0` and no error, and the
response does not even echo the URI you asked for — it is rewritten to whatever was matched:

```
$ bm tool build-context "memory://tnd-zzzz9999-does-not-exist" --project vproj
        "permalink": "tnd_uuuu1111",
        "title": "X Underscore",
    "uri": "tnd_uuuu1111",            # <-- not the URI requested
    "primary_count": 1,
```

There is a silent fuzzy fallback on this path, so **a hit and a miss are indistinguishable to the
caller.** (Two observers disagreed on *which* note comes back for a given miss — arbitrary, not
deterministic-wrong — which is itself the point.)

**Why it matters:** this is the worst failure shape for this project. Every `tend` verb doing reverse
traversal or supersession lookup by id calls exactly this path, and on a typo, a stale id, or a
deleted record it gets a confidently wrong record instead of a not-found. A verifier built on top of
it would validate fabricated links as real. It also makes T9's dangling-relation problem invisible
from the read side.

**Both unknowns are now answered, and both make this smaller than it looked:**

- **The fallback is FTS, not vector.** `SearchQuery.retrieval_mode` defaults to `FTS`
  (`schemas/search.py:72`) and `link_resolver.py:453` never overrides it. Semantic search being on
  was irrelevant. The arbitrariness has a specific cause, visible in the debug log:
  `Strict SQLite FTS returned 0 results; retrying relaxed FTS query strict='root-does-not-exist'
  relaxed='root* OR not* OR exist*'` — the miss is re-run as an **OR of prefix terms**, and
  `results[0]` of that is returned. Which note wins depends on BM25 ranking over whatever the corpus
  happens to contain, which is why two observers got different notes for the same miss.
- **`read_note` and `search-notes` do not share it.** Every MCP read caller resolves with
  `strict=True` (`read_note.py:351,411`, `edit_note.py:140`, `delete_note.py:425`,
  `move_note.py:707`, `write_note.py:332`); `search-notes` queries the search repository directly
  and never resolves at all. `resolve_link`'s *default* is `strict=False`, but the only explicit
  non-strict call in the tree was `context_service.py:193`. **One call site, not a shared path.**

**FIXED 2026-07-27 in `829e5af5`** — `context_service.py` now resolves that fallback with `strict=True,
use_search=False`. Exact permalink, title, and file-path resolution all survive (a `memory://`
URI may legitimately name a note by title); only the relaxed-FTS guess is gone. A miss now returns
`primary_count 0`.

Regression test: `tests/services/test_context_service.py::test_build_context_miss_does_not_fuzzy_match_a_different_note`.
Note the pre-existing `test_build_context_fallback_not_found` did **not** catch this — its
identifier (`completely-nonexistent-note-xyz`) shares no tokens with the test corpus, so relaxed FTS
found nothing to guess with and it passed by vocabulary luck. The new test uses an identifier
containing `root`, which the old code resolved to the Root entity. **A negative test over a corpus
that cannot produce a false positive proves nothing** — the same lesson as T9 finding 2's positive
control.

**Design rule that survives the fix (decided 2026-07-27, user):** `tend` verbs must *still* assert
that the permalink they get back equals the id they asked for, and treat a mismatch as not-found.
Strict resolution can still legitimately rewrite the URI — resolving a title or file path to a
different permalink string is an exact match, not a guess — so the caller cannot infer identity from
a non-empty result alone. Fork fix removes the lie; the verb-level check enforces identity.

**Phase 1's verifier is unblocked by this.** It was gated on T10 and is no longer.

Upstream states a "fail-fast / no-silent-fallback" house style (see #1151) and would probably take
this, but we do not track upstream — reporting it is a courtesy, not a dependency.

### T11 — no newer-schema guard: an older build over a newer DB dies in a raw stack trace
**Found:** 2026-07-27, in `.forked/release-design.md` §2. Code sites re-verified before recording.

`run_migrations` (`src/basic_memory/db.py:525`) builds the Alembic config and calls
`command.upgrade(config, "head")` at `db.py:554`. It never reads the database's `alembic_version`
to compare it against the code's head revision, and nothing in `src/` ever calls
`command.downgrade`:

```
$ grep -rn "alembic_version\|command.downgrade\|get_current_head\|MigrationContext" src/ | grep -v "/alembic/"
src/basic_memory/db.py:530:    Note: Alembic tracks which migrations have been applied via the alembic_version table,
```

One hit in the whole of `src/` outside the migration directory itself, and it is a **comment**.
So installing an *older* build over a database a *newer* build already migrated raises out of the
generic `except Exception` re-raise at `db.py:577-579` — the user gets Alembic's internal error and
no actionable message.

**Why it matters:** in this fork "upgrade" means `git pull` + reinstall, so **rollback is a normal
operation**, not an exotic one — `git checkout <older-commit> && uv tool install --reinstall .` is
one command away. Of all the migration hazards in this tree this is the only one that actually
bites this install.

**Fix:** read `alembic_version` before `command.upgrade`, and if it is a revision the shipped
script directory does not contain, fail with something like "this database was migrated by a newer
Basic Memory; reinstall the newer build or run `bm reset --reindex`".

**The code evidence above is captured; the runtime repro is not.** By this file's own rule the
crash *shape* is still a claim. To capture it: migrate on `main`, `git checkout` a commit before the
newest migration, reinstall, run any command, and paste the traceback here.

### T12 — `bm reset` claims your markdown is safe while unflushed writes live only in the DB
**Found:** 2026-07-27, in `.forked/release-design.md` §2. Both sites re-verified before recording.

`NoteContent` (`src/basic_memory/models/knowledge.py:159-224`) materializes `markdown_content` in
the database alongside a `file_write_status` constrained to
`{pending, writing, synced, failed, external_change_detected}`. While a row is `pending`,
`writing`, or `failed`, **the database row is the only copy of that note** — it has not reached
disk. But `bm reset` opens with the opposite promise (`src/basic_memory/cli/commands/db.py:201-204`):

```python
console.print(
    "[yellow]Note:[/yellow] This only deletes the index database. "
    "Your markdown note files will not be affected.\n"
    "Use [green]bm reset --reindex[/green] to automatically rebuild the index afterward."
)
```

That is true for `entity` / `observation` / `relation` / `search_index`, which are all derivable
from the files. It is **false for un-flushed `note_content`**, which is derivable from nothing.
The `--force` pre-flight guard covers live MCP processes holding the DB open; it says nothing about
unflushed content.

**Why it matters:** this is a data-loss path dressed as a safe operation, and it is reachable by
following the tool's own advice. It also undercuts the "files are source of truth, the index is
disposable" premise the whole fork design leans on — including D9 (store under the config dir),
which was cleared specifically on the grounds that `bm reset` only drops the index and says so.

**Fix, in descending order of preference:** flush pending rows to disk before unlinking; or refuse
to reset while any row is `pending`/`writing`/`failed`, with `--force` to override; or at minimum
correct the message so it stops asserting something that can be false.

**Repro not yet captured** — needs a note held in `pending`/`failed` at reset time. Until then this
is a documented contradiction between two code sites, not an observed loss.

### T13 — a dependency reference naming `basic-memory` silently resolves to UPSTREAM
**Found:** 2026-07-27, from `.forked/release-design.md` §5 and `.forked/hook-design.md`.

**This is a standing rule, not just a bug list.** This fork is not published on PyPI, and its GitHub
path is `noahkiss/basic-memory`, not `basicmachines-co/basic-memory`. Therefore **any** dependency
spec anywhere in this tree that names `basic-memory` — a PEP 723 `# dependencies = [...]` header, a
`pip`/`uv` install line executed by tooling, a `git+https://github.com/basicmachines-co/...` URL, a
`github:basicmachines-co/...` source string — resolves to **upstream's code**, not this tree, and
does so silently and with exit 0. The failure mode is upstream's binary running against this fork's
data.

The PEP 723 instances are gone (both offending Claude hook scripts were deleted this session):

```
$ grep -rIn -- "/// script" --exclude-dir=.git .
.forked/release-design.md:538:**The PEP 723 bug from §5 is fully gone by deletion** — no `# /// script` header remains anywhere
```

The only hit is prose about the fix. Still live, all under `benchmarks/`:

```
$ grep -rn "basicmachines-co/basic-memory" benchmarks/
benchmarks/justfile:242:      "basic-memory @ git+https://github.com/basicmachines-co/basic-memory@{{ref}}"
benchmarks/src/basic_memory_benchmarks/cli.py:223:        "github:basicmachines-co/basic-memory@main",
benchmarks/src/basic_memory_benchmarks/cli.py:385:    bm_source: str = typer.Option("github:basicmachines-co/basic-memory@main", "--bm-source"),
benchmarks/src/basic_memory_benchmarks/models.py:214:    bm_source: str = "github:basicmachines-co/basic-memory@main"
benchmarks/src/basic_memory_benchmarks/runner.py:47:    return resolve_remote_main_sha("https://github.com/basicmachines-co/basic-memory")
benchmarks/docs/write-load-benchmark.md:31:   (`uv pip install 'basic-memory @ git+https://github.com/basicmachines-co/basic-memory@<ref>'`).
```

**Six live sites, not the three that were handed over** — `justfile:242` and `runner.py:47` are
executable code that were missed, and `docs/write-load-benchmark.md:31` documents the same default
to a human. (`benchmarks/docs/benchmarks.md:45` names `basicmachines-co/basic-memory-benchmarks`,
a *different* repo, and is out of scope.)

**Why it matters beyond the benchmarks:** these are the defaults, so `bm-source` unset means
"benchmark upstream." That casts direct doubt on `AGENTS.md`'s "Measured baseline at the fork
point" table — if any of those numbers came from this harness with the default source, they
describe upstream's `main`, not this tree. Worth re-measuring before anything else is designed
around them.

**Fix:** repoint every executable default at this fork (or at the local working tree), and treat
the rule above as a lint. `benchmarks/` is also a plausible strip-policy deletion candidate, which
would close five of the six at once.

### T14 — `skills-latest` is a stale moving tag that outranks every version tag
**Found:** 2026-07-27, by `release-design` while building `just release-preview`.

`skills-latest` was a moving tag maintained by `.github/workflows/publish-skills.yml`, which was
deleted with the rest of `.github/` this session. The tag outlived it, and because it was created
most recently, a bare `git describe` picks it over every `v*` tag:

```
$ git describe --tags
skills-latest-87-gbd5c4d2c
$ git describe --tags --match 'v[0-9]*'
v0.22.1-133-gbd5c4d2c
$ git ls-remote --tags origin | grep -i skills
232f2c2fc4e91564d88bcc312ed3d8bd1e8e051b	refs/tags/skills-latest
```

It exists **both locally and on `origin`**, and it points at the fork point.

**Why it matters:** it does not affect the package version — uv-dynamic-versioning goes through
dunamai, which pattern-matches version tags rather than shelling out to a bare `describe`, which is
why the installed build still reads `0.22.2.dev118+232f2c2f` correctly. What it corrupts is every
*human-facing* `git describe` and anything scripted on one. `just release-preview` already works
around it with `--match 'v[0-9]*'`; the source is unfixed, so the next tool to ask git "what version
is this?" hits it again.

**Fix:** delete the tag locally and on the remote. **Not done deliberately** — deleting a remote tag
changes published state, and `noahkiss/basic-memory` is public. Needs the user's explicit go-ahead.

### T15 — auto-update can silently replace this fork with upstream from PyPI — **SHIPPED `0b755f50`**
**Done 2026-07-27.** Deleted `cli/auto_update.py`, `cli/commands/update.py`, both test files, the
`cli/app.py` call site, the `cli/commands/mcp.py` daemon thread (and its orphaned `threading`
import), the `auto_update` / `update_check_interval` / `auto_update_last_checked_at` config fields
(their `BASIC_MEMORY_*` env vars are derived from the model, so they vanish with the fields), and
the `update` entry in `cli/main.py`. Same commit repointed `pyproject.toml [project.urls]` off
`basicmachines-co` and dropped the upstream PyPI badge. Verified: `just fast-check` exit 0, unit
suite **3444 passed / 33 skipped / 0 failed** — 3471 minus the 27 `def test_` lines the diff
removes, so the delta is fully explained.

*Original report:*
**Found:** 2026-07-27, while auditing runtime dependencies. **Severity: this is the worst trap in
the file** — it is the only one whose failure mode is the fork uninstalling itself.

`src/basic_memory/cli/auto_update.py` fetches `https://pypi.org/pypi/basic-memory/json` and, when
the published version compares newer than the installed one, runs `uv tool upgrade basic-memory`
(or the Homebrew/pip equivalent). `basic-memory` on PyPI is **upstream's** package. There is no
check that the installed build and the PyPI project are the same project.

It is not opt-in. Three separate call sites reach it, and none require the user to ask:

```
config_models.py:630     auto_update: bool = Field(default=True, ...)
cli/app.py:117           maybe_run_periodic_auto_update(ctx.invoked_subcommand)
cli/commands/mcp.py:86   run_auto_update(force=False, check_only=False, silent=True)  # daemon thread
```

`maybe_run_periodic_auto_update` skips only `mcp`, `update`, and non-interactive sessions, so every
other interactive `bm <subcommand>` is a candidate once per `update_check_interval` (86400 s). And
`check_only=False` in both automatic call sites means it **installs**, it does not merely notify.
The MCP path runs it on a background daemon thread at server start, where the user sees nothing.

**Why it has not fired yet is luck, not design.** `uv-dynamic-versioning` with `bump = true` gives
this tree a dev version that currently sorts above upstream's latest release. That ordering is an
accident of where the fork point sits; one upstream minor release inverts it.

This is T13's failure mode ("a dependency reference naming `basic-memory` resolves to upstream")
promoted from documentation to an executable code path. `pyproject.toml` `[project.urls]` still
pointing at `basicmachines-co` is the same root cause, one layer out.

**Fix:** delete the surface. A fork that publishes to no index has no upgrade source, so there is
nothing here to repair — `auto_update.py`, `bm update`, both automatic call sites, and the
`auto_update` / `update_check_interval` / `auto_update_last_checked_at` config fields all go.
Decided with the user 2026-07-27; bundled with the W12/W13/W14 deletion passes.

---

## BLOCKERS / gaps in capability

### B1 — no `contains` operator in metadata filters; multi-value is AND-only
**Found:** 2026-07-26.

```
$ bm tool search-notes --filter '{"supersedes":{"contains":"tnd_aaaa1111"}}'
Error: Unsupported operator 'contains' in metadata filter for 'supersedes'
$ bm tool search-notes --filter '{"supersedes":["tnd_aaaa1111","tnd_zzzz9999"]}'
{"total": 0}                          # AND/subset semantics — no way to express OR
```

Lower priority **now** than when first found: the §11 Q3 decision moved supersession edges out of
frontmatter and into `## Relations`, so we no longer need list-valued frontmatter queries for the
edge store. Kept because any future list-valued field hits the same wall, and because T1 is its
silent-failure sibling.

### B2 — project registry is split between the database and `config.json`
**Found:** 2026-07-26.

```
$ python3 -c "import json,os; print(json.load(open(os.path.expanduser('~/.basic-memory/config.json'))).get('projects').keys())"
dict_keys(['main'])                   # config.json knows only 'main'
$ bm project list                     # ...and does not list 'main' at all
  ~/tmp/.../dc281ff5-...              # two scratch projects, by path only — no names
  ~/tmp/.../c6678c33-...
```

The two sources disagree about which projects exist and which is default, and `project list` renders
only paths, so a project cannot be identified by the name you would pass to `--project`. **Why it
matters:** R8's design has one BM project per tracked repo, keyed by an opaque id, with the human
label carried separately. That is unworkable while the registry is ambiguous and unnamed in output.

### B3 — `bm tool list-projects --json` fails
**Found:** 2026-07-26. Exits 1 and emits nothing parseable as JSON, despite `--json` being the
documented machine-readable path. This is the API `tend` would use to enumerate projects.

**No output was captured.** By this file's own rule that is a claim, not a gap. It stays in BLOCKERS
only because W7 is built on it; capture `bm tool list-projects --json; echo "exit=$?"` verbatim, or
demote it to OPEN.

### B4 — no fast path: anything touching `mcp.tools` / `api.app` costs ~4 s
**Found:** fork-point baseline (see `AGENTS.md`). `bm tool search-notes` is 4.3–4.8 s; a native
command like `project list` is ~0.55 s; the `--version` floor alone is 0.33 s.

This already forced one design decision — `STATUS.local.md` stays a flat file, because a per-prompt
statusline cannot pay 4 s. **Any `tend` subcommand that needs to be fast must talk to the
repository/service layer directly and must not reach through the MCP tool layer.** Worth revisiting
whether the 0.33 s floor itself can come down, since that bounds every fast path we build.

**Amended 2026-07-26 — there is a third import, and B4 as written misses it.** A cold-start breakdown
measured **3.38 s** as `mcp.tools` 1.05 s + `api.app` 0.66 s + **`dateparser` 0.37 s** — process-start
cost, not query cost, and not embeddings. A fast-path fix scoped to the two modules B4 names leaves
~11% of the cost on the path. Found in: sweep-handoffs.md:7.

### B5 — no cwd → project resolution: no walk-up, no marker-file detection
**Found:** BM spike, 2026-07-26.

```
project_resolver.py:56-99 — the whole resolution chain:
  BASIC_MEMORY_MCP_PROJECT env → explicit --project → config default_project
  → discovery (list all) → none
```

There is no walk-up-from-cwd and no marker-file detection anywhere in the package. A repository is
invisible to `bm` until it has been centrally registered by name.

**Why it matters:** `tend` is invoked from inside a project directory and has to map cwd → id through
the `.tend.yml` marker, which is also the opt-in signal for whether a directory is tracked at all.
Neither half exists. This is distinct from B2 (which is about the registry disagreeing with itself);
this is that there is no path from "where I am" to "which project that is." Estimated at ~20 lines of
resolver glue, but it is a change to the resolution chain, not something a wrapper can add.

Found in: sweep-spike.md:1, sweep-decisions.md:1, sweep-status-agents.md:25.

**Amended 2026-07-26 — the ~20-line estimate above is wrong, and B5 is not a fix.** Attempted as part
of the registry cluster and deliberately abandoned as its own project. Three independent blockers:

1. **The `.tend.yml` marker schema does not exist yet**, and it is owned by the store design, not by
   this repo. Writing a resolver against an undefined marker means inventing the schema here.
2. **The marker carries an opaque id, but `--project` resolution is name/permalink-based** through
   `config_manager.get_project()`. So id → project needs a *new lookup layer*: `ProjectResolver` is a
   pure frozen dataclass with no DB access, and the id lives nowhere it can reach.
3. **Every construction site needs cwd wiring** — `index/repository_project_resolution.py`,
   `index/storage_events.py`, `index/local_runtime.py`, plus the MCP and CLI entrypoints. cwd is not
   currently threaded to any of them.

Half of any implementation today would be speculative code written against an undecided schema.
**Blocked on T9 / the store design settling the id-and-permalink question first** — do not start B5
before that lands.

---

## WANT — capabilities to build in

These are the `tend` features, built as `bm` subcommands rather than a wrapper (see `AGENTS.md`,
"What this fork is for"). Listed here so the gap list is the single place to look.

### W1 — `bm mine`: decision mining over Claude Code transcripts
Recovers decisions made in conversation and never written down. **Measured 2026-07-26: no index is
needed** — plain `rg` over the 4 pilot slugs (106 MB / 77 sessions) is ~20 ms, worst case 0.47 s,
and `rg --json` plus a full parse is 0.039 s. An index would add a staleness problem for nothing.

**The real work is a turn classifier, not search.** `role: user` does not mean the human spoke —
tool *results* are recorded as user-role turns. Of 47 hits on one term: 15 `user/user`, 14
`assistant/assistant`, 6 `attachment/-`, and every sampled "user hit" was a `tool_result` block. A
miner that trusts the role field will cite `File created successfully at:` as the moment a decision
was made, with a real timestamp attached — a fabricated `date-ref` manufactured at scale.

Output shape: `{session, line, timestamp, speaker, text, context[]}`, feeding
`date-ref: <session-id>#L<line>`.

**Four search-path requirements, from the O1 diagnosis (see RESOLVED R-O1).** O1 looked like a
second, independent defect blocking this entry; it was a measurement artifact. **The turn classifier
above remains the real work** — nothing in the diagnosis reduces it. What the diagnosis does produce
is four hard constraints on how the miner reads transcripts:

1. **`-g '*.jsonl'` must be an internal positive allowlist, not a caller-supplied flag and not a
   blocklist of known-bad extensions.** A caller who forgets it silently gets the 25% corruption
   back. A blocklist is worse than useless here: the hidden `.context-window-*.json` sidecars are
   single-line valid JSON (11 of 11 sampled lines parse cleanly), so if `--hidden` were ever added
   they would **parse successfully and be mined as conversation turns**. A loud `JSONDecodeError` is
   the lucky failure mode; that one is silent, and it is exactly the class this file exists to catch.
2. **Never pass `--max-columns` or `--max-columns-preview`.** It substitutes the literal marker
   `[Omitted long matching line]` and breaks **100%** of hits (H3: 165/165). If a config file ever
   sets it, override with an explicit `--max-columns 0`. Default `rg` handles 634 KB lines
   byte-perfectly and needs no help.
3. **Prefer `rg --json` over splitting `path:lineno:content` text.** Bare `split(':')` without
   `maxsplit` fails roughly half the time on real transcript content (H5: 50/50), because the JSON
   is saturated with colons. `--json` removes the question entirely and was already measured at
   0.039 s. If text mode is kept anyway, `split(':', 2)` — maxsplit is mandatory — and pass `-n`
   explicitly, never `--no-filename`/`--no-line-number`, since `date-ref: <session-id>#L<line>`
   needs both fields.
4. **Treat any `json.loads` failure as a hard error, never a skip.** With the allowlist in place the
   correct failure rate is **0 of 34,602 lines**, so a single failure means a real regression — a
   corrupted transcript, or a filter that stopped working. Count them, report them, exit nonzero.
   Silently dropping unparseable hits is precisely how O1 went undiagnosed for a session.

One scope choice to make deliberately rather than by default: `-g '*.jsonl'` also matches
`subagents/*.jsonl` sidechain transcripts (1 of the 35 pilot hits). They are genuine transcripts and
parse fine, but a subagent turn's speaker attribution differs from a main-thread turn's — decide
in or out, and record which.

### W2 — `bm tend gc`: the gardener
Strictly lossless — may move, index, dedupe, re-label, and flag; may never summarize, merge, or
resolve. Ship the flag-only version first so the constraint is structural rather than aspirational.

**Reduced in scope 2026-07-26:** it no longer needs to maintain a derived reverse index for
supersession. `build-context` on a predecessor returns incoming edges natively, so the reverse is
derived at read time by the store itself.

### W3 — local git history on the write path
Every mutation commits into a local-only store repo so pruning is recoverable. Two traps: set
`core.excludesFile` **and** `core.hooksPath` to `/dev/null` inside that repo (the global
`betterleaks` pre-commit hook will otherwise block automated commits), and never export `GIT_DIR`.

**Amended 2026-07-26 — a third init trap, a required exclude pattern, and the measured cadence.**

```
$ git --git-dir=$SH init            # creates a BARE repo
warning: core.bare and core.worktree do not make sense
fatal: unable to set up work tree using invalid config
                                    # fix: git config core.bare false immediately after init

$ printf '/*\n!/.beans/\n!/STATUS.local.md\n' > "$SH/info/exclude"
                                    # without this, every commit walks the whole worktree

$ git clean -xdfn                   # in a project dir today
Would remove .beans/
Would remove STATUS.local.md
```

The bare-repo default is a third silent config trap alongside `excludesFile` and `hooksPath`. The
`/*`-plus-re-include exclude is what keeps the commit fast: **12 ms** for an incremental `add -A` +
commit on a 200-file store nested in a 6,200-file worktree, **6 ms** for the no-change check, and 500
sequential auto-commits in 4 s (~8 ms each). That settles the cadence question with numbers — commit
**synchronously inside every mutating call**; no timer, batching, or deferred flush is justified. A
`Stop`/`SessionEnd` hook is specifically the wrong mechanism, because a session that ends badly
(crash, `/clear`, context blowout) never fires it, which is exactly when the history is wanted.

Two write-path requirements that cannot be retrofitted: emit `Session: <claude-session-id>` and
`Actor: agent|human` trailers from the first commit (they are what makes `undo --session` a
`git log --grep` away), and make the serialization byte-stable — deterministic key order, single blank
line after frontmatter, trailing newline — or every touch produces a spurious diff and the history is
noise before it exists. The `git clean` output above is also why the store must live outside the
worktree: a casual command destroys an in-tree store unrecoverably today.

Found in: sweep-localhist.md:1, :7, :13, :19, :31, :37, sweep-handoffs.md:13, sweep-beans.md:1.

### W4 — closed record vocabulary enforced in the write path
Humans extend the vocabulary; agents may only select from it. Upstream's frontmatter vocabulary is
fully open, so enforcement is ours and cannot live in a wrapper.

### W5 — `bm tend check`: schema and integrity lint
Covers T4 (unresolved relations), T6 (doubled frontmatter blocks), set-once field violations, and
`supersedes` appearing on a type other than `finding`.

**Amended 2026-07-26 — five further rules the schema draft names explicitly:**

1. `date-ref` present on an `inline`/`mtime`/`inferred` rung (it is permitted on one rung only).
2. `review-by` missing on a `finding`, and its default injected from `.tend.yml` at write time.
3. `permalink` absent or `!= id` — permalink is a set-once identity field (see T9).
4. **Any** list-valued frontmatter field at all, because `--meta` can never query one (T1).
5. Validation must run **on the read path as well as the write path**. Beans' `check` validated only
   on write, which is why `status: done` drift sat undetected on disk for six months.

Found in: sweep-schema.md:43.

### W6 — an idempotent, resumable importer
The corpus is written by other sessions while a migration runs. Measured over twenty minutes in a
single session: `pegemony` 271 → 368 lines, `palimpsest` 292 → 438, `hn-app` 21 → 31, and the corpus
count 52 → 53 files. A one-shot importer silently drops everything written while it runs, and BM's
import path offers no resume.

It must also **normalize, not copy**: the four existing beans stores (athena 176, just-chatting 40,
trmnl 14, tinyledger 10+37 = ~240 records) carry on-disk schema drift — `tinyledger` uses
`status: done` where `just-chatting` uses `status: in_progress`. And every tool in this space assumes
a greenfield directory (`bm project add` included), so bringing 7,746 lines across 52 heterogeneous
files in is bespoke code either way.

Found in: sweep-status-agents.md:61, sweep-handoffs.md:37, sweep-inv-plan.md:49,
sweep-decisions.md:25, sweep-prior-art.md:31.

### W7 — an agent-facing output contract
B3 records that one `--json` command exits 1. The underlying gap is that no contract exists for it to
violate: JSON to stdout only, diagnostics to stderr, no ANSI inside JSON, non-zero exit on failure,
and a versioned schema published alongside. Coverage is the other half — any command without
machine-readable output forces text scraping, and `tend` is a machine consumer of `bm` throughout.

Found in: sweep-prior-art.md:49, sweep-beans.md:19.

### W8 — a bounded, pointer-shaped session primer
Nothing puts prior state into an agent's context: the `SessionStart` hook was deliberately unwired and
kept unwired, so recall now depends on an agent choosing to read a file. BM writes no
`AGENTS.md`/`CLAUDE.md` equivalent — grepping the package returns no hits — and its
`memory://ai_assistant_guide` resource is pull-based, which agents rarely fetch unprompted.

The primer must be **size-capped and index-only**. The comparison case is instructive: `beans prime` is
189 lines / 1,145 words / 7,383 bytes ≈ 1,850 tokens, static, unconditional, opens with
`<EXTREMELY_IMPORTANT>` and *"You MUST ignore all previous instructions regarding tracking work using
todo lists"* — and `beans prime --help` shows no flags at all, so there is no way to trim it. What we
need is ~50 tokens of one-line-per-record index, never content, plus a search mode that returns
compact pointers rather than whole notes.

Found in: sweep-inv-plan.md:1, sweep-spike.md:37, sweep-transcript.md:31, sweep-decisions.md:19,
sweep-prior-art.md:13, sweep-prior-art.md:37.

### W9 — a `STATUS.local.md` emitter with a validated contract
The headline file stays flat (B4), but it must be written *by* the store rather than by hand — and the
write has to satisfy three consumers with three different parsers: `noah-statusline.js` requires
`lines[0].trim() === '---'` **and** `lines[1].startsWith('headline:')`; `~/bin/projects` reads line 2
with no `---` check; `notify.sh` also reads line 2. A malformed write fails silently in the statusline
while the other two display the wrong text. Three of 52 files were doing exactly this for months:
`blog/landing/STATUS.local.md` has an HTML comment on line 1, and
`well-educated-mind/STATUS.local.md` has `status: active` hoisted above `headline:` — both give the
lint two real, reproducible failure modes to detect.

The emitter must also preserve **mtime semantics**, not just content: `~/bin/projects` uses file mtime
as its staleness check, so a regen that rewrites unconditionally makes every stale project read as
fresh — the precise silent failure the flat file was kept for.

Found in: sweep-decisions.md:13, sweep-inv-plan.md:31, sweep-handoffs.md:19, sweep-handoffs.md:49,
sweep-prior-art.md:13.

### W10 — an exclusion mechanism on the indexing path
BM indexes markdown under the project root with no ignore file and no redaction. Two consequences we
have already committed to needing around: the losslessness guarantee copies each migrated file
verbatim into the store (`_import/STATUS.local.md.2026-07-26`, no frontmatter, not intended as a
record), which without an exclude becomes ~1,600 lines of phantom search hits polluting every read
path; and records living inside project directories mean the indexer will eventually ingest tokens or
`.env`-adjacent prose into both a SQLite index and an embedding store, with nothing to stop it.

Found in: sweep-schema.md:31, sweep-prior-art.md:55.

### W11 — a cross-project read
Every BM query is project-scoped and nothing aggregates across projects. The gardener's staleness
sweep and any "what is open everywhere" question both need one read across all stores; today
`~/bin/projects` is the only cross-project view and it reads line 2 and mtime, nothing more.

Found in: sweep-inv-plan.md:25.

### W12 — delete the cloud / multi-tenancy surface — **SHIPPED `ba2bc67e`**
**Done 2026-07-27.** ~30,300 lines deleted across 175 files: `cli/commands/cloud/`,
`cli/commands/workspace.py`, `schemas/cloud.py`, `mcp/tools/cloud_info.py`, the `RuntimeMode` CLOUD
branch, `skip_local_initialization`, the cloud config fields, and all cloud tests. Verified on a
stable tree: `just fast-check` clean, `just test-unit-sqlite` = **3483 passed / 33 skipped / 0
failed** (down from 4066 — the delta is deleted cloud tests), `bm --help` and `bm --version` both
construct. Zero over-deletions found during the repair audit; nothing needed restoring.

*Original rationale, kept because it is the precedent for W13/W14/W15:*
**Scheduled deletion, decided 2026-07-27 (user).** Cloud sync, rclone, bisync, cloud auth, and the
CLI routing flags that select them. Not "when it costs us twice" — proactively, because it is
*already* costing us: it generated the open question of whether the promote branch should exist for
local installs (see the T1/B1 divergence), which is a design question with no answer worth having.
It also keeps `skip_local_initialization` / `BASIC_MEMORY_CLOUD_MODE` alive as a second
configuration reality that every registry change has to be reasoned about twice.

See AGENTS.md → "We do not track upstream" → "Strip policy" for the rule this falls under.

### W13 — delete the Postgres backend
**Scheduled deletion, decided 2026-07-27 (user).** Postgres is an alternative *index* backend, not
an alternative format — files stay authoritative and the DB is disposable. It exists for the hosted
multi-tenant deployment and buys a local single-user install nothing; SQLite is what `sqlite-vec`
(semantic search, which we want on) plugs into and what AGENTS.md's whole baseline table was
measured against.

It has already met the default strip bar of "made you write the same thing twice": `43d1a3a4`
changed 27 lines of `postgres_search_repository.py` purely to mirror a SQLite fix. Deleting it
halves every future search-repository change — the dual-repository shape is the single most
duplicated surface in this tree.

**Until this lands**, run the Postgres tests on any commit touching `postgres_search_repository.py`:
`BASIC_MEMORY_TEST_POSTGRES=1` over the two metadata-filter files and
`tests/repository/test_postgres_search_repository.py`. Testcontainers pulls
`pgvector/pgvector:pg16` — verify it ran by filtering `docker ps` on that image or on label
`org.testcontainers=true`, **not** `--filter ancestor=postgres`, which cannot match and produced a
false "Postgres never ran" conclusion on 2026-07-26.

### W14 — delete the `bm ci` GitHub CI-capture surface — **SHIPPED `6098d9b0`**
**Done 2026-07-27.** Deleted `src/basic_memory/ci/` (3 files), `cli/commands/ci.py`,
`tests/cli/test_ci_commands.py`, and both registration sites. Verified: `just fast-check` exit 0
(the 9 standing `ty` advisories only — `error-on-warning = false`), unit suite **3471 passed / 33
skipped / 0 failed**, exactly the 3483 baseline minus the 12 deleted `ci` tests.

*Original report:*
**Strip candidate, found 2026-07-27 while verifying the strip.** `src/basic_memory/cli/commands/ci.py`
+ `src/basic_memory/ci/project_updates.py` + `tests/cli/test_ci_commands.py` scaffold a
`.github/workflows/basic-memory.yml` into a *target* repo so a GitHub Action can write project-update
notes. It survived the 2026-07-27 strip only because it writes into `tmp_path` in tests and so kept
passing after `.github/` was deleted — it was never judged on merit.

This fork runs no CI, opens no PRs, and has no `.github/` of its own. Nothing here is a dependency of
anything else. Same class as W12/W13: pure deletion, no replacement.

Not urgent — it costs nothing at runtime (`ci` is one more entry in the `cli/commands` import list).
Bundle it with W12 rather than making a pass of its own.

---

## OPEN — observed, not diagnosed

*(O1 was diagnosed and closed on 2026-07-26 — it was a measurement artifact, not a defect. See
**R-O1** under RESOLVED, and the four requirements it produced under **W1**. The id is retired
rather than reused, so O2–O6 keep their numbers.)*

### O2 — `bm orphans` reports both endpoints of a frontmatter-encoded edge as orphans
**Found:** 2026-07-26, recorded in the schema §12 comparison as a tested outcome on a 9-note corpus.
No command output was captured, so this is a claim until reproduced.

This is the inverse of T4: T4 is a dangling edge never reported, this is a *resolved* edge producing
two false orphan reports. Between them the built-in hygiene verb is untrustworthy in both directions,
which matters because W2 was expected to lean on it.

**To confirm or refute:** write two notes linked by a frontmatter-encoded edge into a scratch project,
reindex, and run `bm orphans --project <p>` — capture the output verbatim.

Found in: sweep-schema.md:7, sweep-inv-plan.md:7, sweep-status-agents.md:7.

### O3 — frontmatter values appear not to reach full-text search
**Found:** 2026-07-26, same §12 comparison — "Full-text search for the id → 0 hits" against relations,
which are indexed as first-class rows. Recorded as a table outcome, not captured output.

If it holds, any field kept only in frontmatter (ids, dates, sources) is unreachable by the primary
query path and findable only through `--filter`, which T1 and B1 already cripple. It also means the
obvious fallback read path — grep the store for an id — silently returns nothing.

**To confirm or refute:** `bm tool search-notes "<id-string-present-only-in-frontmatter>" --project <p> --json`
against a reindexed note, with a control query on a body string from the same note.

Found in: sweep-schema.md:1, sweep-inv-plan.md:13, sweep-status-agents.md:1.

### O4 — no demonstrated filter or sort over `updated_at`
**Found:** 2026-07-26. Only the `review-by` *frontmatter* filter was actually proven. `updated_at` is
DB metadata rather than frontmatter, and no working `--filter` form for it has been shown.

Two load-bearing features sit on it: the gardener's staleness flag (`type == state AND updated_at <
today - N`) and the derived headline that keeps `noah-statusline.js` alive (the most recently updated
`state` record's title). Both are unbuildable if the field cannot be filtered and sorted.

**To confirm or refute:** `bm tool search-notes "**" --permalink --filter '{"updated_at":{"$lt":"2026-07-25"}}' --project <p> --json`,
and separately whether any sort/order argument exists on that path.

Found in: sweep-schema.md:37.

### O5 — `schema-infer` returns an error object where an empty result belongs
**Found:** BM spike, 2026-07-26.

```
$ bm schema-infer decision
{"error": "No schema pattern found for 'decision' (threshold: 25%)"}
```

...on a corpus where decisions were roughly half the notes. The threshold is neither exposed nor
tunable. Same shape as T1 — a legitimate "nothing cleared the bar" reported as a failure. The spike
itself flags the corpus as unfair (4 notes), so **this needs a retest before it needs a fix**; drift
detection is the one shipped thing that looked like a gardener, and if it stays inert then W4 and W5
have to supply all schema enforcement themselves.

**To confirm or refute:** rerun `bm schema-infer decision` against a reindexed corpus of 30+ notes
with a consistent `decision` shape.

Found in: sweep-spike.md:31, sweep-status-agents.md:55.

### O6 — two unaudited write-path behaviours: non-unique replace, and move-vs-rewrite
**Found:** 2026-07-26. Both are claims about our code prompted by observing the failure elsewhere;
neither has been checked against this tree.

1. **Non-unique find/replace.** Does `edit_note`'s find/replace fail loudly when the target text
   occurs more than once, or does it patch the first hit? Silently patching the wrong occurrence is a
   corruption class we have no guard against.
2. **Move vs. rewrite.** Does `move_note` use `rename(2)`, or read-write-truncate? A move that
   reconstructs content destroys mtime, permissions, and inode identity while making the file *look*
   newer — and every staleness and expiry feature in the design is built on mtime being truthful.

**To confirm or refute:** read the implementations of `edit_note` and `move_note` in
`src/basic_memory/mcp/tools/`, then test each against a scratch project.

Found in: sweep-beans.md:7, sweep-transcript.md:55.

### O7 — `add_project`'s default-repair logging is unformatted, and one of its branches is now dead
**Found:** 2026-07-27, while repairing the W12 cloud strip.

Two independent defects in `ProjectService.add_project`'s default-repair block
(`src/basic_memory/services/project_service.py`, ~lines 274–341), both cheap to fix:

1. **The log messages never interpolate.** Four calls there use `logger.info("… '%s' …", value)`.
   loguru formats with `str.format`, not `%`, so the operator sees the literal `'%s'`:
   `Materialized configured default project '%s' that had no database row`. Verified directly.
   These are the only `'%s'` log placeholders in `src/`.
2. **The promote-the-new-project branch is unreachable.** It fires only when `default_project` names
   a project present in neither the database nor `config.json`. `BasicMemoryConfig.model_post_init`
   repairs exactly that state on every load, and the strip removed the only bypass
   (`skip_local_initialization`, a cloud/stateless flag), so nothing can now reach it. It was covered
   by `test_add_project_response_reflects_promoted_default`, which the repair replaced with
   `test_add_project_materializes_configured_default` — the branch that actually runs. Decide whether
   to delete the branch or to keep it as a defensive invariant with a `# pragma: no cover`.

Related: **T5** covers the user-facing half of the same area (the CLI has no `--set-default` flag).

---

## Docs swept

**2026-07-26.** A ten-reader sweep reconciled the following into this file. The gaps they contained
are now recorded here, and **the next sweep does not need to redo them:**

- The ten `~/develop/.design/status-system-*.md` docs — `prior-art`, `beans-deepdive`, `bm-spike`,
  `decisions`, `local-history`, `handoff`, `migration-handoff`, `inventory`, `plan`, `schema-draft`,
  and `transcript`.
- `~/develop/STATUS.local.md`.
- `AGENTS.md` in this repo.

Sweep these again only for material written *after* this date. Design decisions found in them about
the record schema or the work plan were deliberately left where they are — those docs own them.

---

## Where this connects

| | |
|---|---|
| Execution plan, phases, decisions | `~/develop/.design/status-system-plan.md` |
| Record schema (types, fields, supersession) | `~/develop/.design/status-system-schema-draft.md` |
| Settled/reversed decisions with turn cites | `~/develop/.design/status-system-decisions.md` |
| Session-to-session state | `~/develop/STATUS.local.md` |
| Fork point, remotes, license, measured baseline | `AGENTS.md` in this repo |
