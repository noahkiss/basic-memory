# GAPS — what this fork needs to fix

**This file is the running list of everything wrong with, or missing from, upstream Basic Memory
that our work depends on. It is the fork's to-do list and its rationale.**

## The rule

**When you find a gap, write it here in the same session you find it.** Do not leave it in a
design doc, a STATUS file, or a session summary and intend to transfer it later — that transfer is
the return visit that never happens. A gap recorded only in a local design doc is invisible to
anyone working in this repo, which is where it has to get fixed.

A gap belongs here if it is a thing **we would change in this codebase**. Design decisions about
the record schema live in `.forked/schema.md`; findings about the work *plan* live in
`.forked/plan.md`. Both are local and gitignored. This file is only about the code.

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

### S2 — `.claude/commands/` held two upstream-only prompts
**Done 2026-07-29.** Deleted `.claude/commands/spec.md` and `.claude/commands/test-live.md`; the
directory is now empty and gone. Judgment call taken under the campaign's decide-and-flag rule, not
a user decision.

- `spec.md` (51 lines) drove a spec-driven process whose entire state lives in an upstream Basic
  Memory project named `specs`, keyed on notes `SPEC-1` and `SPEC-2`. This fork has no such project
  and does not run that process — `GAPS.md` plus `.forked/campaign.md` are its planning surface. The
  command could only ever have failed here, silently, by searching an absent project.
- `test-live.md` (614 lines) was a manual QA ritual over the MCP tool surface, run by hand against a
  live install. Nothing executes it, nothing verifies it, and no test would notice it going stale —
  the same failure class as the stale baseline in **T18** and the stale command references in
  **W16**. `just doctor` and `just test-smoke` cover the same ground automatically.

Neither file was referenced anywhere else in the tree (`git grep` for both names: no hits outside
the files themselves). Recoverable at any time with `git show 117308fb:.claude/commands/spec.md`.

### S3 — five of the eight coverage `omit` patterns named modules that no longer exist
**Done 2026-07-29.** `[tool.coverage.report].omit` in `pyproject.toml` still excluded
`external_auth_provider.py`, `supabase_auth_provider.py`, `background_sync.py`,
`sync/sync_service.py`, and `services/migration_service.py`. None of those files are in `src/`
any more — they went with the cloud strip (W12) and the Postgres strip (W13), which removed only
the `*/db.py` line from this list (`git show 79e0dad9 -- pyproject.toml`) and left the rest.

A dead omit is worse than no omit: it reads as a deliberate, justified exclusion, so the next
person to audit coverage trusts it and does not check whether the file exists. Pruned to the three
that are real — `*/watch_service.py`, `*/cli/**`, `*/services/initialization.py`.

**This changed no number.** Removing an omit for a path that matches nothing cannot alter the
report, so no re-measurement is needed to make that claim. What the run it prompted *did* find is
recorded as **T20**.

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

### T1 — `--meta` returns 0 instead of erroring on list-valued frontmatter — **RESOLVED 2026-07-31: fixed in-tree since `43d1a3a4` (2026-07-26); end-to-end verified**
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

**Resolved 2026-07-31, no new code.** Both halves were already fixed in this tree by `43d1a3a4`
(2026-07-26, "fix(core): metadata filter handling for list values and multi-value filters"),
which landed the same day the gap was recorded and was never reconciled against it: `eq`/`in`
compile to scalar-equality OR'd with an exact `json_each` element match
(`sqlite_search_repository.py`, comment cites this entry), and boolean queries expand to both
stored spellings via `_boolean_match_values` (`metadata_filters.py`). Eight repository tests
cover it (`tests/repository/test_metadata_filters_list_values.py`), including the substring
decoy. End-to-end CLI verification 2026-07-31, isolated scratch project (list-valued
`supersedes` + booleans, decoy note, `bm reindex`):

```
== --meta supersedes=tnd_aaaa1111 (T1 original repro; expect successor only)
total: 1 ['successor']
== --meta draft=true (boolean half; expect successor only)
total: 1 ['successor']
== --meta draft=false (expect decoy only)
total: 1 ['decoy']
== --meta supersedes=tnd_aaaa (substring decoy; expect zero)
total: 0 []
== positive control: --filter '{"supersedes":["tnd_aaaa1111"]}'
total: 1 ['successor']
```

### T2 — `bm status` reports files as observed that are not indexed — **RESOLVED 2026-07-31: fixed in-tree since `9e4f3c8c` (2026-07-26); verified live**
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

**Resolved 2026-07-31, no new code.** Fixed by `9e4f3c8c` (2026-07-26, the same unreconciled fix
batch as T1/T3/T5) via the "distinguish and warn" branch of the fix list; 4 tests in
`tests/cli/test_status_unindexed_warning.py`. Verified live 2026-07-31, isolated scratch project:
file written to the project dir after init, before any reindex:

```
│ main: Project Index                                                          │
│ ├── 1 observed file                                                          │
│ └── 1 observed file is NOT indexed — invisible to search and read until      │
│     'basic-memory reindex'                                                   │
```

The amended reindex-cost note referenced the retired fork-point embed baseline; the structural
point (tend verbs that write via the filesystem must reindex) stands and is what the warning now
surfaces.

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

### T4 — dangling wikilink relations are stored silently — **SHIPPED 2026-07-31: `bm doctor --project` reports them, oldest source first**
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

**Shipped 2026-07-31** as the first corpus check inside `bm doctor` (per the settled "doctor
absorbs the integrity checks, no `bm check`" decision — W5's later rules land in the same frame).
`bm doctor --project NAME` reports every relation with `to_id IS NULL`:
`date  source_file  -type-> [[target]]`, oldest source first. Relations carry no timestamp, so
the source entity's `updated_at` is the age proxy the entry asked for — recent = target may not
be written yet, old = likely typo/dead. Exit 0 (dangling forward references are legitimate;
this is a report, not a failure); unknown project exits 1 rather than reading as a clean corpus.
Runs on the T18 native path (`direct_unresolved_relation_report` in `cli/direct.py`), not the
ASGI client. Tests: 2 repository (ordering + decoy, cross-project scoping with positive
control), 3 CLI (rows + exit 0, clean corpus, unknown project). Live-verified on a scratch
corpus with a seeded `[[Z Does Not Exist]]` forward reference.

### T5 — `bm project add` silently makes the new project the default — **RESOLVED 2026-07-31: fixed in-tree since `1baceca5` (2026-07-26); verified live**
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

**Resolved 2026-07-31, no new code.** `1baceca5` (2026-07-26) removed the promote-on-add
(`ProjectService.add_project` promoted the new project whenever the configured default had no DB
row — every fresh config), and the CLI now exposes the choice as `project add --default`, the
flag the amendment asked for. Verified live 2026-07-31, scratch config: default stayed `main`
after `project add second`, `project remove second` succeeded (the original repro's failure
mode), and `--default` appears in `project add --help`. The dead default-repair branch this
left behind was cleaned separately under O7.

### T6 — RESOLVED, no defect. See **R-T6** in the RESOLVED section.
The number is retained as a tombstone so existing cross-references do not silently retarget. Retested
against both the fork build and `2b19f1ff`: no corruption in either, and the write path is unchanged
across the whole fix cluster. Nothing was reverted because nothing was ever written for it.

### T7 — `search-notes` rejects an empty or `*` query; metadata-only queries need a `**` idiom — **RESOLVED 2026-07-31, guard SHIPPED**
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

**Re-tested 2026-07-31 — the entry is inverted on this tree.** Omitting the query entirely now
works with `--filter` and with `--meta` (both returned the fixture note in a scratch project), and
the `**` idiom now *hard-errors*: `sqlite3.OperationalError: unknown special query: *`, with the
full SQL dumped to stdout and **exit 0**. So the metadata-only path exists and is the supported
spelling; what remains of T7 is (a) no test guards the queryless path, and (b) the `**` error is
reported as prose on stdout with a success exit — see O8.

**Guard shipped 2026-07-31.** `test_queryless_metadata_search_end_to_end` in
`tests/mcp/test_tool_search.py` runs the gardener's exact query shape — `search_notes` with
`metadata_filters` and no text — through the full client → API → service → repository stack:
matching note returned, non-matching decoy excluded, plus a text-query positive control over the
same corpus (the pre-existing payload-level test could not catch a server-side rejection
reappearing). The `**`-error-as-prose-with-exit-0 half lives on in **O8/W7**; nothing else of T7
remains.

### T8 — `semantic_search_enabled` does not gate the embedding cost it advertises — **RESOLVED 2026-07-31: the flag now gates ~2.2 s CPU / ~250 MB; verified by import trace**
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

**Resolved 2026-07-31, no new code.** The lazy structure (TYPE_CHECKING-only `fastembed` import
in `fastembed_provider.py`, provider imported inside the `provider_name == "fastembed"` branch of
`embedding_provider_factory.py`) dates to upstream #550; what un-gated it at the fork point was
eager imports elsewhere in the graph, since removed by this fork's dependency prune (W17) and
the #886 leaf-deferral work shipped with T18. Measured 2026-07-31, scratch config,
`bm tool search-notes` under `-X importtime` and `/usr/bin/time -v`:

```
flag OFF: 0 fastembed/onnxruntime import lines; 3.14 s user CPU; 197 MB peak RSS
flag ON : 62 fastembed/onnxruntime import lines; 5.32 s user CPU; 445 MB peak RSS
```

The flag-ON run is its own positive control (the trace does catch the import when enabled).
`config set semantic_search_enabled false` now buys exactly what it advertises.

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

### T11 — no newer-schema guard: an older build over a newer DB dies in a raw stack trace — **CONFIRMED 2026-07-31**
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

**Runtime repro captured 2026-07-31** — via a git worktree at `9e3fe26a~1` (one commit before the
newest migration, `n7i8j9k0l1m2`) with its own venv, against a config dir freshly migrated by
current `main`. No install was touched. Two shapes, both worse than useless and both **exit 0**
(the O8 class again):

```
$ (older build) bm project list      # against the newer DB
Error listing projects: Can't locate revision identified by 'n7i8j9k0l1m2'
exit=0
$ (older build) bm reindex
│ ❱  245 │   │   │   raise util.CommandError(resolution) from re              │
╰─────────────────────────────────────────────────────────────────────────────╯
CommandError: Can't locate revision identified by 'n7i8j9k0l1m2'
exit=0                               # full Rich traceback into alembic internals
```

Neither says "this database was migrated by a newer build" nor names a way out. The fix stands as
written above; add a non-zero exit while there.

### T12 — `bm reset` claims your markdown is safe while unflushed writes live only in the DB — **SHIPPED 2026-07-31 (guard + O6 write-through root fix)**
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

**Repro captured 2026-07-31** in an isolated scratch config. `write_note` was invoked and the
process exited between DB-accept and file materialization (the write path is DB-first: the v2
mutation service "accepts" into `note_content` and defers the file write to a materialization
runner — see O6 for the same architecture caught mid-move). State before reset:

```
$ sqlite3 $CONFIG/memory.db "SELECT e.file_path, n.file_write_status FROM note_content n JOIN entity e ON e.id=n.entity_id WHERE e.file_path LIKE '%unflushed%';"
probes/T12 unflushed.md|pending          # no such file exists on disk
$ echo y | bm reset
Note: This only deletes the index database. Your markdown note files will not be
affected.
Use bm reset --reindex to automatically rebuild the index afterward.
Reset the database index? [y/N]: Database reset complete
$ grep -r "IRREPLACEABLE" $PROJ $HOME $CONFIG; echo "exit=$?"
exit=1                                   # the note body is nowhere, including the recreated DB
```

No warning about the pending row, and the content is unrecoverable. The pending state was produced
by killing the process inside the accept→flush window; that window is real in the live server (the
runner is asynchronous by design), so this is the advertised-safe data loss, observed.

**Guard shipped 2026-07-31** (first preference from the fix list — flush, then refuse):

- Before any file is unlinked, `reset` runs `_flush_unflushed_note_content`: query
  `note_content` for `pending`/`writing`/`failed`, drive the same per-project recovery sweep
  startup and `bm reindex` use (`recover_project_materializations`), re-query.
- Anything still unflushed → refusal with a `project/file_path (status)` list and exit 1;
  `--force` proceeds with an explicit "the content above is lost" warning (help text updated).
- The false "your markdown note files will not be affected" message replaced with the truthful
  flush-first description.
- Tests (`tests/cli/test_db_reset_guard.py`): the query reports pending and ignores synced; the
  refusal/force/clean branches; and the flush half end-to-end — a seeded pending row reaches disk
  and drops off the list.

Live repro inverted in the same harness shape as the original capture: seeded `pending` row with
body nowhere on disk → `echo y | bm reset` → flush materializes `probes/unflushed live.md`
(content intact on disk) → reset proceeds. The accept→flush window itself (the root cause) stays
open until the synchronous write-through decision recorded under **O6**.

### T13 — a dependency reference naming `basic-memory` silently resolves to UPSTREAM — **SHIPPED `cece1087` + `e11cc1d7`**
**Done 2026-07-27.** All six benchmark sites repointed at `noahkiss/basic-memory`, plus two the
original report missed: `docker-compose.yml` pulled upstream's GHCR image (now builds from the
checkout — this fork publishes no image), and the README documented `uv tool install basic-memory` /
`uvx basic-memory mcp`, i.e. upstream's PyPI package, in the install line and all five client
configs. `runner.py:47` was the worst of them — it stamped upstream's `main` SHA into every run
manifest whenever `--bm-local-path` was unset, so the AGENTS.md baseline table is still suspect
until re-measured. Same pass removed upstream branding (support@ addresses, docs.basicmemory.com
links, README badges/star-history); upstream *issue* citations were kept as provenance and
relabeled `basicmachines-co#NNN`.

**The standing rule below still applies to new code** — nothing enforces it as a lint yet.

*Original report:*
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

**RESOLVED 2026-07-28 by deletion, in pass 4.** All six live sites were inside `benchmarks/`, which
is gone. `git grep basicmachines-co/basic-memory` now returns only prose in `GAPS.md` and
`AGENTS.md` (the fork-point provenance SHA), no executable default. The lint proposed below was
never written and is no longer needed for this instance — but the *rule* stands for any future code
that resolves a dependency by name: a bare `basic-memory` reference resolves to upstream.

**Doubt cast on the baseline table: also settled, and worse than suspected.** This entry warned the
`AGENTS.md` table might describe upstream's tree. It turns out `benchmarks/` is a retrieval-quality
suite (Recall@5, MRR, LLM-as-judge, mem0/zep providers) and **never produced those numbers at all**,
so the table's provenance was simply unknown rather than upstream's. Re-measuring found one row
wrong by an order of magnitude regardless — see T18. Table restated and partly retired in pass 4.

*Original fix proposal:* repoint every executable default at this fork (or at the local working
tree), and treat the rule above as a lint.

### T14 — `skills-latest` is a stale moving tag that outranks every version tag — **RESOLVED 2026-07-29**
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

**Fix:** delete the tag locally and on the remote. Done 2026-07-29 with the user's explicit
go-ahead, since deleting a remote tag changes published state and `noahkiss/basic-memory` is public.
No code change — this was a repository-state defect, not a code defect, hence RESOLVED not SHIPPED:

```
$ git tag -d skills-latest && git push origin :refs/tags/skills-latest
Deleted tag 'skills-latest' (was 796607fd)
To https://github.com/noahkiss/basic-memory.git
 - [deleted]           skills-latest
$ git describe --tags
v0.22.1-165-g117308fb
```

A bare `git describe` now reports a version tag. `just release-preview`'s `--match 'v[0-9]*'`
workaround is left in place: it is correct independent of this tag and costs nothing.

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

### T16 — `runtime-core-fast-check-no-openai` is a dead byte-identical duplicate recipe — **SHIPPED `dd302010`**
**Done 2026-07-28** in pass 4, the pass that already had the justfile open. Deleted; `git grep`
confirmed zero callers outside this entry. `fast-check-no-openai` at line 77 is unaffected.
**Found:** 2026-07-27, while stripping the Docker surface. **Severity: cosmetic.**

`justfile:299` and `justfile:77` have identical bodies:

```
fast-check-no-openai:                    # line 77
    OPENAI_API_KEY= just fast-check
runtime-core-fast-check-no-openai:       # line 299
    OPENAI_API_KEY= just fast-check
```

The line-299 copy sits in the "Runtime / Event Indexing Refactor" block, where it was presumably
added so that refactor's recipes read as a self-contained group. It takes no argument and is not
scoped to the runtime-core tests, so the name promises a narrowing it does not perform — the two
recipes cannot diverge in behaviour, only in what a reader expects. Nothing references it.

**Fix:** delete `justfile:299-300`. Not urgent, and not bundled into a strip pass — the strip
passes touch the justfile for benchmark recipes (pass 4) and that is the natural moment.

---

### T17 — 10 integration tests fail on `main`; `test-int/` was never run against the W13 tree
**Found:** 2026-07-27. **DIAGNOSED 2026-07-28 by bisect. W13 is exonerated.** All ten are strip
residue — tests left behind asserting on surface this fork deliberately deleted. None is a
regression in shipping code.

**The bisect, four runs of the same ten tests (they reproduce in ~10 s, so the 426 s full suite was
never needed):**

```
HEAD       1d406872                    10 failed
039275af   parent of W13               10 failed        <- W13 EXONERATED, identical failures
ba2bc67e   the W12 cloud strip         10 failed
9b7dd678   parent of the W12 WIP       1 failed, 9 passed
a614cbd0   parent of the T10 fix       test_build_context_underscore.py: 2 passed
```

**Cause 1 — nine of ten: W12, the cloud strip (`3c789d8c` + `ba2bc67e`).** W12 removed the
`--local` / `--cloud` routing flags from every `bm tool` and `bm project` command, and removed the
cloud indexing job's response shape. The tests exercising both were left in place.

- **Eight** die on `No such option: --local`, a Typer usage error — `SystemExit(2)` before any
  application code runs. Two of the eight (`test_cli_search_notes_page_size_zero`,
  `test_remove_main_project`) do not *look* like routing tests and were miscounted in the original
  report as a separate cluster; they merely pass `--local` in passing.
- **One** (`test_recreate_retained_project_indexes_existing_notes`) dies on `KeyError: 'state'`.
  `state` was a field of the cloud indexing job. The local path returns `ProjectIndexRunResult`
  (`indexing/project_index_coordinator.py:165`), captured verbatim at runtime as
  `{'total_files': 0, 'enqueued_files': 0, 'enqueued_batches': 0, 'deleted_files': 0}` — no `state`,
  and none is derivable.

**Cause 2 — one of ten: `829e5af5`, this fork's own T10 fix.**
`test_build_context_underscore_normalization`'s "Test 4" asserted the relaxed-FTS fuzzy fallback
that T10 removed on purpose, and its comment said so in as many words: *"Previously this returned
empty (no exact permalink match). Now LinkResolver resolves to the child entity, so we get its
relations back."* That is precisely the confidently-wrong-answer behaviour T10 exists to kill. Under
the fix the miss correctly returns `"results":[]` / `total_results: 0`. **The test was guarding the
bug.** Tests 1–3 in the same function cover the underscore/hyphen normalization the file is named
for and pass throughout.

**FIXED 2026-07-28 in `879681d4`** — the ten tests were brought in line with the surface that
actually ships: `--local` removed from all 13 call sites, the two conflict-message tests deleted or
narrowed (the validation they assert on cannot exist without the flags), the `state` assertion
dropped, and Test 4 rewritten to assert that a `memory://` miss stays a miss. **`test-int` baseline
is now green: 337 passed / 4 skipped / 0 failed** — the first green `test-int` in this fork's
history, which was the actual defect this entry recorded.

**What this cost, and the protocol it forces.** W12 was verified on `fast-check` + the unit suite
only, and shipped a red `test-int` that then sat undetected across four further passes (W14, T15,
T13, W13) — each of which inherited the red suite and so had no way to tell its own breakage from
the standing failures. W13 was blamed on ordering alone. **A green unit suite is not sufficient
evidence for a strip pass; every pass runs `test-int` before its commit is called green.** The
second lesson is narrower and sharper: **a deletion pass must grep the test tree for the surface it
is deleting, not only the source tree.** Eight of these ten would have been caught by one
`git grep -- --local test-int/` during W12.

**Two more sites of the same residue, outside the test tree — both RESOLVED in `bec90372`.**
Corrected identification: the recipe passing `--local` six times was `telemetry-smoke`, not
`test-smoke` (`test-smoke` is a pytest invocation and was never affected). `telemetry-smoke` existed
to exercise logfire telemetry (`BASIC_MEMORY_LOGFIRE_*`), so pass 3 deleted it outright.

The second site was the one that mattered and nearly escaped: **`just doctor` passed `--local` too**
(`justfile:417`) and had therefore been exiting on `No such option: --local` since W12 — a command
`AGENTS.md` names as one of the four steps of the standard development loop. It was found only
because pass 3 grepped the justfile for `--local` after deleting `telemetry-smoke`, rather than
assuming the scheduled deletion cleared the whole class. Fixed by dropping the flag; `just doctor`
now reports `Doctor checks passed.`

**The lesson is narrower than the T17 one above and worth stating separately:** a gap entry that
says "scheduled into pass N, which deletes that file anyway" closes the *instance* and leaves the
*class* open. Re-grep after the scheduled deletion lands.

*Original report:*
**Severity: unknown until diagnosed — this is undiagnosed, not triaged.**

W13 (Postgres deletion, `79e0dad9`) was verified against `just fast-check` and the **unit** suite
only. `just test-int-sqlite` was never run against it. It has now been run, on `cbc62d41`:

```
10 failed, 327 passed, 4 skipped, 15 deselected in 426.54s
```

```
test-int/mcp/test_build_context_underscore.py::test_build_context_underscore_normalization
test-int/mcp/test_recreate_project_indexing_integration.py::test_recreate_retained_project_indexes_existing_notes
test-int/bughunt_fixes/test_navigation_pagination_integration.py::test_cli_search_notes_page_size_zero
test-int/bughunt_fixes/test_parse_tags_comma_split_integration.py::test_cli_write_note_comma_tags_split_matches_mcp
test-int/cli/test_cli_tool_delete_note_integration.py::test_delete_directory_removes_nested_files_database_records_and_search_results
test-int/cli/test_cli_tool_delete_note_integration.py::test_delete_note_rejects_conflicting_routing_flags
test-int/cli/test_cli_tool_edit_note_integration.py::test_edit_note_project_and_routing_flag_parity
test-int/cli/test_cli_tool_write_note_type_integration.py::test_write_note_type_flag_round_trip
test-int/cli/test_project_commands_integration.py::test_remove_main_project
test-int/cli/test_search_notes_meta_integration.py::test_search_notes_query_plus_meta_filter
```

**Not caused by the Docker pass.** That pass (`59a508ca`) is documentation plus one error-message
string no test asserts on; these fail on the tree as pushed at `cbc62d41`.

**Not yet established: whether W13 caused them at all.** They may equally predate it — nobody has a
green `test-int` baseline to compare against, which is the actual root problem here. **Do not
assume W13 is the cause and do not "fix" them by reverting W13 behaviour** until a run against
`039275af` (the commit before W13) says whether they were already red. That bisect is step one.

Seven of the ten are CLI-surface tests and six of those concern routing/project flags, which is a
suspiciously tight cluster — but clustering is a hypothesis, not a diagnosis. Full tracebacks were
not captured (the run was piped through `tail`); re-run without the pipe.

**Why this matters beyond the ten tests:** the W13 verification protocol treated a green unit suite
as sufficient. It is not. Every remaining strip pass should run `test-int` before its commit is
called green, and this entry stays open until there is a known-good `test-int` baseline to measure
against.

### T18 — `AGENTS.md`'s performance baseline is stale by 9x, and the "fast native command" it names is not fast — **SHIPPED 2026-07-31**
**Found 2026-07-28** while deciding the fate of that table in pass 4. Measured on this tree
(`da4f8a59`), Linux/x86-64, Python 3.13, warm, three runs each.

**Methodology note — read before quoting these.** The host was under sustained load throughout
(6 cores, `loadavg` 5.8-6.5, an unrelated process pinning >100% CPU), so **wall-clock times are not
usable** — the same `project list` invocation measured 4.9 s and 10.3 s depending on contention.
The figures below are therefore **user CPU time** and **peak RSS**, both of which are essentially
contention-independent. The fork-point table records wall time, so these are not directly
comparable to it; the comparison holds only because CPU time is a *lower bound* on wall time, and
the lower bound alone already exceeds the claim by an order of magnitude.

| Path | `AGENTS.md` claims (wall) | Measured (user CPU) | Measured RSS vs claim |
|---|---|---|---|
| CLI native cmd (`project list`) | ~0.55 s / ~73 MB | **4.71 / 5.73 / 5.31 s** | **231 MB** vs 73 MB |
| CLI `--version` floor | 0.33 s / 59 MB | 0.43 / 0.46 / 0.48 s | 65 MB vs 59 MB |

The `--version` floor is roughly honest. `project list` is not: it burns ~5 CPU-seconds against a
documented 0.55 s and holds 3x the claimed memory. RSS is unaffected by load, so the memory
discrepancy needs no methodological caveat at all.

**The stale number is not the real problem.** `AGENTS.md` draws a load-bearing architectural
conclusion from it:

> The decisive structural fact: **commands that avoid importing `basic_memory.mcp.tools` /
> `basic_memory.api.app` cost ~0.55 s; commands that touch them cost ~4 s.** [...] **Any `tend`
> subcommand that needs to be fast must talk to the repository/service layer directly and must not
> reach through the MCP tool layer.**

`project list` is that document's own example of a fast native command, and it **imports both**.
This part of the finding is load-independent and does not rest on any timing:
`python -X importtime -m basic_memory.cli.main project list` lists `basic_memory.api.app` and
`basic_memory.mcp.tools` in its output at all, and a `sys.modules` probe after invoking the command
through `CliRunner` reports both as `True`. The imports are **lazy** — importing
`basic_memory.cli.main` alone pulls in neither (same probe reports both `False`) — so they happen
*inside the command body*, which is why nothing static catches it and why the module-level import
graph looks clean. Largest single leaf: `basic_memory.api.v2.routers.prompt_router`.

**The guidance is still correct; the claim that native commands currently honour it is false.** That
matters for W1-W11: the fast `bm` verbs are specified against a boundary the existing native
commands have already crossed, so "do what `project list` does" is now precisely the wrong
instruction.

**Not diagnosed:** whether this is a regression from a strip pass or has been true since the fork
point. The fork-point numbers themselves are suspect — see the note in pass 4's handoff about
`benchmarks/runner.py` — and no harness in this repo measures CLI startup, so nothing would have
caught a drift either way. `benchmarks/` is a *retrieval-quality* suite (Recall@5, MRR,
LLM-as-judge, mem0/zep providers) and never produced these numbers, so deleting it neither caused
this nor removes the ability to re-measure: `/usr/bin/time -f "%e %M"` and `-X importtime` are the
whole harness.

**Fix:** trace which import chain drags `api.app` into `project list` and cut it, then re-measure.
A cheap regression guard (assert `project list` completes under ~1 s, or assert
`basic_memory.api.app not in sys.modules` after a native command) would make the boundary
structural instead of aspirational — the same reasoning as shipping the flag-only gardener first.

**SHIPPED 2026-07-31.** The chain was exactly the one B4 names: the command body called
`get_client()` → `mcp.async_client._asgi_client` → `from basic_memory.api.app import app`, i.e.
every "native" command was an API command served in-process over ASGI. Two smaller riders came in
through the service layer itself: `search_service` imported `fastapi` for an annotation-only
`BackgroundTasks`, and `markdown.entity_parser` imported `dateparser` (0.14 s) at module level for
one method body.

What shipped:
- `basic_memory.cli.direct` — builds `ProjectService` straight from config → `get_or_create_db`
  → repository, no FastAPI/MCP anywhere on the path. `project list` and `project ls` now run on
  it via `fetch_project_list()` in `cli/commands/project.py` (same merge logic, same output —
  verified byte-identical `--json` on the scratch harness).
- The two leaf deferrals: `BackgroundTasks` behind `TYPE_CHECKING` + quoted annotations;
  `dateparser` imported inside `parse_date` / a `_parse_date` helper.
- The structural guard: `tests/cli/test_native_command_import_guard.py` runs `project list` in a
  **subprocess** (sys.modules is process-global, so in-process assertions lie under pytest) and
  fails if `basic_memory.api.app`, `basic_memory.mcp.tools`, `fastapi`, or `dateparser` loaded.
  A positive-control test forces `fastapi` in and asserts the probe reports it.

Measured (same host/method as the 2026-07-28 numbers; host lightly loaded, user CPU + RSS):

```
before  project list: user=3.57–3.70 s  rss=214 MB
after   project list: user=1.12–1.16 s  rss=115 MB
floor   --version:    user=0.15 s       rss=40 MB
```

The remaining 1.1 s is SQLAlchemy + pydantic + alembic — the irreducible cost of a DB command, and
the budget any fast verb starts from. The other project subcommands (`add`/`remove`/`default`/
`move`/`info`) still route through ASGI deliberately: mutations and one-shots, correctness over
latency. `AGENTS.md`'s baseline table updated.

---

### T19 — an import-grep cannot tell you whether a pytest plugin is dead — **SHIPPED `6f8767a3`**
**Found and fixed 2026-07-28** (strip pass 7). `pytest-aio` was listed in the pass-7 prune as
"dead-regardless, confirmed by import-grep: zero imports". It has zero imports because **pytest
plugins load through the `pytest11` entry point, never through an `import` statement.** An
import-grep returns zero for a plugin that is load-bearing and zero for one that is genuinely
dead — it cannot distinguish them, so it is not evidence.

Removing it turned **55 unit tests red** at once, all with the same message: `async def functions
are not natively supported.` The mechanism: `pyproject.toml` sets `asyncio_mode = "strict"`, so
pytest-asyncio only collects `async def` tests that carry an explicit `@pytest.mark.asyncio`.
`pytest-aio` is what was collecting the bare ones — 55 of them, concentrated in `tests/index/`
(`test_local_project_index.py`, `test_inline_storage_event_processor.py`,
`test_local_inline_result_recorder.py`, and others). Restored, moved from the runtime dependency
list to `dev` where a test plugin belongs, with the reason recorded inline so the next prune does
not repeat this.

**Generalize it:** before deleting a dependency on the strength of "no imports", check whether it
is a *plugin* rather than a library — pytest plugins (`pytest11`), setuptools/console entry
points, SQLAlchemy dialects, codecs, and anything registered by name in config. For pytest
specifically, the fast check is `uv run pytest --trace-config` (lists every active plugin) or
simply deleting it and running the full suite, which is what caught this one. Note the failure
mode is loud but *misattributable*: 55 tests in a subsystem untouched by the pass, failing for a
reason that names asyncio and never names the dependency you removed.

**This is also the case for running the suite before committing, not after.** The unit run is
~6 minutes; the pass would otherwise have been pushed with 55 red tests, which is exactly the T17
shape.

---

### T20 — `AGENTS.md` requires 100% coverage; the tree is at 96% and nothing enforces it
**Found:** 2026-07-29, running the campaign's Phase 0 `just coverage` item.

`AGENTS.md` states, as a rule: *"**Coverage must stay at 100%**: Write tests for new code. Only use
`# pragma: no cover` when tests would require excessive mocking."* The measured value:

```
$ just coverage
========== 3685 passed, 14 skipped, 6 warnings in 1950.55s (0:32:30) ===========
...
TOTAL                                       19463    866    96%
```

**866 uncovered statements across 110 modules**, after the three surviving omit patterns are
applied. This is not a regression from the dead omits pruned in **S3** — those matched no files, so
they cannot have changed the number — and it is not concentrated anywhere a single fix would reach.
The worst offenders are spread across every layer: `mcp/tools/ui_sdk.py` 47%,
`mcp/resources/discovery.py` 0%, `services/note_content_reads.py` 64%, `api/template_loader.py` 68%,
`services/directory_deletes.py` 71%, `index/watch_coordinator.py` 78%, `mcp/tools/recent_activity.py`
81%, `mcp/tools/search.py` 82%, `schemas/request.py` 82%.

**Why it matters:** nothing in the repo enforces the rule. There is no `fail_under` in
`[tool.coverage.report]`, no `--cov-fail-under` in any recipe, and no CI (`just gate` runs lint +
typecheck + unit tests only). So the rule is advisory text that has been false for an unknown
length of time, and an agent reading `AGENTS.md` will either believe a false invariant about this
tree or treat the whole document as unreliable. The second is the expensive outcome — it is the same
failure mode as **T18**'s stale baseline, and the reason W16 existed.

**Judgment call taken 2026-07-29** under the campaign's decide-and-flag rule, since the campaign
offered only "write the missing tests or re-add the omit with a stated reason" and neither fits an
866-statement, tree-wide shortfall: **restate the rule in `AGENTS.md` to match reality** rather than
either write ~866 statements' worth of tests inside a Phase 0 item or silence the gap with a fresh
omit. The reversible half is that the honest number is now written down and the ratchet is
described; raising the floor later is a config line plus tests, and costs nothing that was not
already owed.

**Fix, when it is scheduled:** set `fail_under` to the current measured floor so coverage can only
go up, then raise it as the verb work adds tested code. Do **not** set it to 100 — that turns the
whole suite red on the next commit and violates "never ship on top of a red suite." The floor is a
ratchet, not an aspiration. Deferred deliberately: it is a mechanical change with no dependents, and
it is worth setting once, after **T18**'s fast-path work settles which modules survive.

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

### B3 — `bm tool list-projects --json` fails — **REFUTED 2026-07-31**
**Found:** 2026-07-26. Claimed to exit 1 and emit nothing parseable as JSON. No output was ever
captured, and it stayed in BLOCKERS only because W7 is built on it.

**Re-run 2026-07-31** in an isolated scratch config, 165 commits past the fork point:

```
$ bm tool list-projects --json
{
  "projects": [
    { "name": "main", "external_id": "b9b135b0-…", "path": "…", "is_default": true },
    { "name": "scratch", "external_id": "43c1f547-…", "path": "…", "is_default": false }
  ],
  "default_project": "main",
  "constrained_project": null
}
exit=0
```

Valid JSON, exit 0. Either the original observation was wrong or the intervening strip fixed it;
either way there is nothing to build. W7 can rely on this surface.

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

**Amended 2026-07-31 — the fast path now exists (T18).** `basic_memory.cli.direct` gives native
commands a repository/service route with none of `api.app`/`mcp.tools`/`fastapi`/`dateparser` on
it, guarded by `tests/cli/test_native_command_import_guard.py`; `project list` runs 1.1 s user /
115 MB. B4 stays open for what remains: `bm tool *` one-shots still pay the full MCP import graph
by design, and the ~1.1 s direct floor (SQLAlchemy + pydantic + alembic) plus the 0.15 s
interpreter floor bound every fast verb. Close B4 when the verbs land on the direct path and the
floor is either accepted or reduced.

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

**Decided 2026-07-31:** W4 does not build on `picoschema/`; that subsystem is stripped as the first
commit of this build (see O-picoschema for grounds). The vocabulary source is `.bm.yml`, validated
by a bespoke checker that W5 wires into `bm doctor`.

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
**SHIPPED `79e0dad9`.** 97 files, −6790/+694. Verified `3406 passed / 10 skipped / 0 failed`
(baseline 3444+33 collected, −61 `def test_`), `fast-check` exit 0 with zero `ty` advisories.
No alembic revision file was deleted — whole-body-Postgres revisions are no-ops so the
`down_revision` chain and stamped `alembic_version` rows stay valid. `nest-asyncio` survives
(applied outside any backend gate); `litellm` + the remaining dependency prune shipped later in
`6f8767a3` (W17).

**Residue swept 2026-07-29.** Two test fakes still carried a `get_bind()` returning a fabricated
dialect name — `FakeProjectIndexSession` in `tests/indexing/test_project_index_maintenance.py` (plus
its `dialect_name` field) and `FakeExecuteSession` in `tests/indexing/test_directory_delete_runner.py`.
Nothing in `src/` calls `session.get_bind()` any more; the only remaining hits are alembic's
unrelated `op.get_bind()`. Both removed, along with the now-unused `SimpleNamespace` import in
`test_directory_delete_runner.py`.

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

### W15 — delete the logfire/OpenTelemetry surface — **SHIPPED `bec90372`**
**Done 2026-07-28** (strip pass 3). 58 files, −6109/+2404. This fork ships no telemetry backend and
never will, so every span, metric, and config knob feeding one was overhead on paths that run on
every note write.

Deleted: `src/basic_memory/telemetry.py` (`configure_telemetry()`, `get_logfire_handler()`); ~36
`with logfire.span(...)` call sites across `api/`, `mcp/`, `cli/`, `indexing/`, `repository/`,
`services/`; the `metric_histogram` / `metric_counter` emissions in `semantic_vector_sync`; four
config fields (`logfire_enabled`, `logfire_send_to_logfire`, `logfire_service_name`,
`logfire_environment`); the `ConfigureTelemetry` protocol and `configure_logfire_for_entrypoint()`;
`docs/logfire-instrumentation-strategy.md`; `.agents/skills/instrumentation/` (depended on logfire,
no replacement); the `telemetry-smoke` justfile recipe; the `logfire` dep from both dependency
groups.

**Kept: loguru.** It is this fork's real logging and is unrelated to logfire despite the name.
`initialize_file_logging()` and the `SetupLogging` protocol are untouched. Anyone repeating this
kind of pass should confirm which of the two a call site uses before deleting it.

**The one place this was not a pure deletion, and the trap worth remembering.**
`repository/semantic_vector_sync.py` accumulated `queue_wait_seconds` *inside* the span it was
reported from, so deleting the span silently deleted the measurement.
`queue_wait_seconds_total` is a **field of the returned result object**, not telemetry — it is
consumed by callers. Two unit tests caught it
(`test_sync_entity_vectors_batch_only_attributes_queue_wait_to_flushed_entities`,
`test_sync_entity_vectors_batch_tracks_prepare_and_queue_wait_seconds`, reporting `0.0` against an
expected `1.5` and `0.5` against `3.0`). The tell that these were a real regression and not
telemetry residue: the sibling timings (`prepare_seconds_total`, `embed_seconds_total`,
`write_seconds_total`) still computed correctly, because they happened to accumulate *outside* the
span. Restored as plain `perf_counter` arithmetic matching the siblings.

**Generalize it:** instrumentation blocks are not always pure observers. Before deleting a
`with span(...)` body wholesale, check whether anything computed inside it escapes — a returned
field, a mutated accumulator, an assignment read after the block. Grep the deleted names against
the *test* tree, not just `src/`.

Verified: `just fast-check` exit 0 with zero `ty` advisories; unit **3378 passed / 10 skipped**
(baseline 3406 minus the 28 `def test_` lines the diff deletes, no parametrized cases among them,
so the arithmetic is exact); `test-int` **336 passed / 4 skipped / 0 failed**, unchanged from the
green baseline established in `879681d4`; `just doctor` green and logging a nonzero
`queue_wait_seconds_total`, which is what confirmed the restored accumulation end-to-end rather
than only against mocks.

---

### W16 — trim `README.md` and correct stale command references — **SHIPPED `e8212855`**
**Done 2026-07-28** (strip pass 6). Docs-only.

Deleted from `README.md`: the four "What people are saying" testimonials (upstream's marketing for
upstream's product — this fork ships to one user) and the "What's New" section (release notes with
no release process behind them, since releases here are a git tag and nothing else, so it could
only rot).

The part worth recording is that **every command in the Development section was wrong in a way a
reader could not detect.** `just test` and `just test-int-sqlite` both exclude the `semantic` and
`benchmark` sets; the one-word descriptions implied full runs. The marker list named a
`windows`/`benchmark`/`smoke` trio and pointed at the justfile; the markers are declared in
`pyproject.toml` and there were six. `AGENTS.md` named
`test-int/test_sync_performance_benchmark.py` in two places — a file that does not exist and never
has under that name in this tree; it is `test_search_performance_benchmark.py`.

**Generalize it:** a docs pass over a stripped tree is not a copy edit. Re-run or at least re-read
every command against the file it claims to describe — the deletions that invalidate them are
exactly the ones nobody thinks to re-check.

Every surviving `docs/` link in both files resolves to a pass-5 keeper. Test-inert: the `README.md`
strings under `tests/` are `tmp_path` fixture names, not reads of the repo file.

### W17 — remove the LiteLLM embedding provider and prune dependencies — **SHIPPED `6f8767a3`**
**Done 2026-07-28** (strip pass 7, the last pass). LiteLLM was a second path to a capability the
`openai` provider already had. 13 packages leave the lockfile (litellm plus aiohttp, tiktoken,
yarl, multidict, frozenlist, propcache, aiosignal, aiohappyeyeballs, fastuuid,
importlib-metadata, zipp).

Deleted: `repository/litellm_provider.py` and its factory branch; four config fields
(`semantic_embedding_forward_dimensions`, `..._document_input_type`, `..._query_input_type`, and
their provider-cache-key slots); `tests/repository/test_litellm_provider.py`; the three
`test-int/semantic/` litellm files; the `test-litellm-live` justfile recipe; the now-unreferenced
`live` pytest marker; `docs/litellm-provider.md`. Pruned: `pyright` (a dev tool in the runtime
list, already in `dev` at a newer pin), `pyjwt`, `sniffio`. Moved runtime → dev: `python-dotenv`,
`pytest-asyncio`.

**The one behaviour change, and why it is in scope.** The `openai` branch of
`embedding_provider_factory` now passes `api_key=` and `base_url=` through to the OpenAI SDK.
Before, only the litellm branch read those two config keys. `openai` was the provider kept
*because* `AsyncOpenAI(base_url=...)` serves every OpenAI-compatible server (Ollama, LM Studio,
vLLM, OpenRouter); without the wiring, the deletion would have silently removed custom-endpoint
support and left two config fields dead. Two tests cover it. This is the part of the pass most
worth a second reader.

**A keeper decision can be voided by a later pass.** `docs/litellm-provider.md` was on the pass-5
keep list, correctly — the provider was live and the doc was its only reference. Once the provider
is deleted the doc describes nothing, so it went too and the keeper count is 6, not 7. The
material that outlived the provider was kept: the literal role prefixes work with `fastembed` and
`openai` and were only nested under LiteLLM by accident of history.
`docs/semantic-search.md` now says outright that provider-side `input_type` models (Cohere v3,
NVIDIA NIM) are unsupported rather than leaving the loss silent.

See T19 for the `pytest-aio` trap this pass walked into and the rule it produced.

Verified: `just fast-check` exit 0, zero `ty` advisories; unit **3341 passed / 10 skipped / 0
failed** (3378 − 37: 35 litellm provider tests, 2 sqlite-vector role-settings tests, 2 config
forward-dimensions tests, + 2 added endpoint tests); test-int **330 passed / 4 skipped / 0
failed** (336 − 6, the tests in `test_litellm_live_harness.py`, which carried no `semantic` marker
and so were in the baseline — `test_litellm_live_models.py` was marked `semantic` and never
counted); `just doctor` green.

### W18 — index frontmatter values into full-text search — **SHIPPED 2026-07-31**
**Opened 2026-07-31**, reversing O3's original "adapt around it" consequence — user call: this is
our tool, and frontmatter nobody can find without already knowing the key defeats the point of
frontmatter. When the indexer builds an entity's `search_index` row, include frontmatter key/value
text so plain FTS reaches ids, dates, and vocabulary values. Keep `--meta`/`--filter` as the
exact-match path; this adds discovery, not structure. Schedule alongside the other index-layer
work (T18 fast path, O4's `updated_at` predicate + sort) so the index is touched once. Evidence
and fixture in O3.

**Shipped 2026-07-31.** `SearchService._frontmatter_search_terms(entity)` flattens frontmatter
keys and scalar values (list elements included; `tags` skipped — it has its own indexing path;
`None` and nested dicts contribute nothing) into the entity's `content_stems`. Terms are inserted
**ahead of the body**: `content_stems` truncates at `MAX_CONTENT_STEMS_SIZE` from the tail, so
anything appended after a large body silently falls out of the index — the pre-existing order
already exposes permalink/file-path/tag variants to that crowding on >6000-char notes, which is
worth its own look someday. Tests replay O3's fixture inverted: the frontmatter-only id
(`record_id: zq7-…`) is now reachable by plain FTS next to the body-string positive control, plus
an absent-token negative control. `--meta`/`--filter` unchanged as the exact-match path.

---

## OPEN — observed, not diagnosed

### O-picoschema — `picoschema/` is un-stripped upstream surface, now with no design doc — **DECIDED 2026-07-31: W4 does not build on it; strip lands with W4**
**Found 2026-07-28** in pass 5, while checking the handoff's precondition for deleting
`docs/specs/SPEC-SCHEMA*.md`. That precondition was "check `.forked/schema.md` supersedes them" —
**it does not.** `.forked/schema.md` is this fork's *record-vocabulary* proposal (the closed
vocabulary of W3); `SPEC-SCHEMA` is upstream's *Picoschema* frontmatter-validation design, a
different subject entirely. The two were conflated by name.

`src/basic_memory/picoschema/` is live: `parser.py`, `resolver.py`, `validator.py`, `inference.py`,
`diff.py`, plus 6 test files. The specs were deleted anyway, per the seven-keeper criterion, so that
subsystem now has code and tests but no design documentation.

**The real question this raises is not the doc, it is the code.** Picoschema validates notes against
Picoschema-syntax frontmatter. This fork is building a *closed record vocabulary* (W3) enforced in
the write path. Those are competing designs for the same concern, and nothing has decided whether
picoschema stays. It is a plausible strip candidate on the same grounds as W12/W13 — but that is a
**product decision, not a strip**, so it is recorded here rather than acted on.

Decide before building W3: strip picoschema, or build W3 on top of it. If it is stripped, the
deleted specs need no replacement. If it is kept, recover them from git — they are in history at
`dd302010~1`.

**Decided 2026-07-31 (delegated call, per standing decision 2): the closed vocabulary does not
build on picoschema, and `picoschema/` + `schema_router` + the `bm schema` CLI are scheduled for
strip as the first commit of the W4 build.** Grounds, each checked against the code this session:

1. **Wrong authority model.** Picoschema resolves schemas from the *corpus* — inline
   `frontmatter['schema']`, a referenced schema note, or implicit by type against schema notes
   (`picoschema/resolver.py:1-12`). Schema notes are agent-writable, so the vocabulary is open by
   construction — the exact property W4 exists to remove. The closed vocabulary is declared in
   `.bm.yml`, human-edited, enforced in the write path. Building W4 on picoschema would leave two
   competing sources of truth for the same concern.
2. **Missing semantics.** The syntax expresses required/optional, enum, array, object
   (`parser.py`). It cannot express set-once fields, conditional requirements (`date-ref` mandatory
   on `transcript`/`git`, *forbidden* on the other rungs), the one-date-per-type rule, or a
   vocabulary sourced from a file outside the corpus. Every rule W5 lists needs machinery picoschema
   does not have; the bespoke validator over `.bm.yml` is smaller than the bridge would be.
3. **The mining half cannot see the fields it would govern.** O5's retest showed `schema infer` is
   frontmatter-blind: 32 notes with identical `status`/`decided-on`/`owner` frontmatter produced a
   frequency table containing only the body observation.
4. Per this entry's own consequence: the deleted SPEC-SCHEMA docs need no recovery.

Not stripped tonight: a deletion pass has its own rules (grep the test tree, entry points, W15-class
escape checks) and belongs with the W4 work, where W5's `bm doctor` checks replace `schema
validate`/`diff` in the same program — no window with zero drift detection. Until then the subsystem
is frozen: no fixes land in it (O5's error-shape defect is recorded under O8 as class evidence and
dies with the strip).


*(O1 was diagnosed and closed on 2026-07-26 — it was a measurement artifact, not a defect. See
**R-O1** under RESOLVED, and the four requirements it produced under **W1**. The id is retired
rather than reused, so O2–O6 keep their numbers.)*

### O2 — `bm orphans` reports both endpoints of a frontmatter-encoded edge as orphans — **DIAGNOSED 2026-07-31: orphans is right, frontmatter edges don't exist**
**Found:** 2026-07-26, recorded in the schema §12 comparison as a tested outcome on a 9-note corpus.
No command output was captured, so this is a claim until reproduced.

This is the inverse of T4: T4 is a dangling edge never reported, this is a *resolved* edge producing
two false orphan reports. Between them the built-in hygiene verb is untrustworthy in both directions,
which matters because W2 was expected to lean on it.

**To confirm or refute:** write two notes linked by a frontmatter-encoded edge into a scratch project,
reindex, and run `bm orphans --project <p>` — capture the output verbatim.

Found in: sweep-schema.md:7, sweep-inv-plan.md:7, sweep-status-agents.md:7.

**Diagnosed 2026-07-31 — `bm orphans` is truthful; the frontmatter edge never exists.** Fixture:
`o2-alpha.md` with `depends_on: "[[O2 Beta]]"` in frontmatter, plus a control pair linked by a body
relation (`- relates_to [[O2 Delta]]`). After reindex:

```
$ bm orphans --project scratch        # Alpha and Beta both listed; Gamma and Delta absent
$ sqlite3 $CONFIG/memory.db "SELECT from_id, to_id, relation_type FROM relation;"
5|6|relates_to                        # the body-link control is the ONLY relation row
```

The frontmatter wikilink produced **no relation row at all**, so both endpoints genuinely have no
relations and `orphans` reports them correctly. The defect is not in `orphans`; it is that
frontmatter-encoded edges are invisible to the graph — the same class as O3. Consequence for the
schema: **edges must be body relations** (`- relation_type [[Target]]`), never frontmatter fields.

### O3 — frontmatter values appear not to reach full-text search — **CONFIRMED 2026-07-31**
**Found:** 2026-07-26, same §12 comparison — "Full-text search for the id → 0 hits" against relations,
which are indexed as first-class rows. Recorded as a table outcome, not captured output.

If it holds, any field kept only in frontmatter (ids, dates, sources) is unreachable by the primary
query path and findable only through `--filter`, which T1 and B1 already cripple. It also means the
obvious fallback read path — grep the store for an id — silently returns nothing.

**To confirm or refute:** `bm tool search-notes "<id-string-present-only-in-frontmatter>" --project <p> --json`
against a reindexed note, with a control query on a body string from the same note.

Found in: sweep-schema.md:1, sweep-inv-plan.md:13, sweep-status-agents.md:1.

**Confirmed 2026-07-31.** Fixture note with `record_id: zq7-frontmatter-only-93kx` in frontmatter
only and the phrase `xylophone-body-control-77` in the body, reindexed in a scratch project:

```
$ bm tool search-notes "xylophone-body-control-77" --project scratch --json   # control
  → 1 result, score 1.237
$ bm tool search-notes "zq7-frontmatter-only-93kx" --project scratch --json
  → {"results": [], …}
```

Positive control passed; the frontmatter-only value is unreachable by FTS. Frontmatter *is* stored
(`entity_metadata` — a queryless `--filter` on `review-by` matched this same note, see O4) and is
exact-match filterable via `--meta`/`--filter`, but it never enters the FTS index.

**Decision reversed 2026-07-31 (user call): this is a defect to fix, not a constraint to design
around.** This fork owns the indexer; there is no upstream shape to preserve. Frontmatter that can
only be queried by someone who already knows the key is a trap — a plain search for an id finding
nothing is what misled the original spike. Fix: include frontmatter key/value text when the
indexer builds the entity's `search_index` row, so FTS reaches it. Tracked as **W18**; schedule
with the phase-2/3 index work (T18 / the O4 date predicate). Two parts stand unchanged: edges stay
body relations (`## Relations` carries a typed, link-resolved edge — kept on merit, see O2), and
O5's inference blindness is moot once picoschema is stripped.

### O4 — no demonstrated filter or sort over `updated_at` — **SHIPPED 2026-07-31 (repository layer)**
**Found:** 2026-07-26. Only the `review-by` *frontmatter* filter was actually proven. `updated_at` is
DB metadata rather than frontmatter, and no working `--filter` form for it has been shown.

Two load-bearing features sit on it: the gardener's staleness flag (`type == state AND updated_at <
today - N`) and the derived headline that keeps `noah-statusline.js` alive (the most recently updated
`state` record's title). Both are unbuildable if the field cannot be filtered and sorted.

**To confirm or refute:** `bm tool search-notes "**" --permalink --filter '{"updated_at":{"$lt":"2026-07-25"}}' --project <p> --json`,
and separately whether any sort/order argument exists on that path.

Found in: sweep-schema.md:37.

**Confirmed 2026-07-31.** Three facts, established in a scratch project:

1. **`--filter` on `updated_at` targets the wrong store.** It compiles to
   `json_extract(entity.entity_metadata, '$."updated_at"') < ?` — frontmatter, not the DB column —
   so it matches nothing unless a note carries a literal `updated_at:` frontmatter key. A queryless
   `--filter '{"updated_at":{"$lt":"2026-08-15"}}'` returned 0 rows over a corpus that should match.
2. **`--after_date` is the only DB-column date predicate, and it points the wrong way.** It compiles
   to `datetime(search_index.updated_at) > datetime(:after_date)` (controls behaved: past date → 1
   hit, future date → 0). Strictly `>` — usable for recency, useless for staleness (`$lt`).
3. **No sort argument exists anywhere on the path** (CLI, schema, repository). But passing
   `after_date` appends `, search_index.updated_at DESC` after `ORDER BY score ASC`, and in
   queryless mode every score is `-0.0`, so the tiebreak becomes the effective order. Demonstrated:
   two notes came back newest-first with equal scores.

**Consequences:** W9's headline ("most recently updated `state` record") is buildable today via
queryless `--meta` + `--after_date <epoch> --page-size 1` — fragile (rides an undocumented tiebreak)
but real. W2's staleness sweep (`updated_at < cutoff`) has **no query form at all**; it needs either
a `$lt` predicate over the DB column or set subtraction (all minus `--after_date` survivors). The
fast verbs talk to the repository layer directly (T18/B4), so the right fix is a repository-level
predicate + explicit sort, not a patch to the MCP filter grammar.

**Shipped 2026-07-31, per the decided design — repository layer only, MCP grammar untouched:**

- `search()`/`count()` take `before_date: datetime | None` — compiles to
  `datetime(search_index.updated_at) < datetime(:before_date)`, the DB column, strictly `<`.
  W2's sweep is now `search(before_date=cutoff, order_by=UPDATED_ASC)`.
- `search()` takes `order_by: SearchOrder` (`RELEVANCE` default / `UPDATED_DESC` / `UPDATED_ASC`,
  new enum in `schemas/search.py`). Explicit orders replace score order, with score as tiebreak;
  RELEVANCE preserves the historical after_date tiebreak byte-for-byte. W9's headline no longer
  rides the -0.0-score tiebreak: `order_by=UPDATED_DESC, limit=1`.
- Both are **FTS-only and fail fast**: passing either with VECTOR/HYBRID raises `ValueError` —
  a silently ignored staleness cutoff would return fresh notes from a sweep that promised stale
  ones. Wiring them through the vector fusion path is work the verbs don't need.
- Tests: `tests/repository/test_search_repository.py` — three dated rows; strict-`<` selection
  with positive control, count parity, both explicit orders end-to-end, and the three mode-guard
  raises.

### O5 — `schema-infer` returns an error object where an empty result belongs — **RETESTED 2026-07-31: works on a fair corpus; the error-shape defect stands**
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

**Retested 2026-07-31** against 32 `decision` notes sharing identical frontmatter (`status`,
`decided-on`, `owner`) and one `[context]` observation each (`bm schema infer decision`):

- **Inference is not inert.** It analyzed all 32 notes and suggested `{"context": "string"}` from
  the observation at 100%. The spike's failure was its 4-note corpus, as suspected.
- **The error-shape defect stands.** A type with notes but no pattern still yields
  `{"error": "No schema pattern found for 'note' (threshold: 25%)"}` in `--json` mode, exit 0 — a
  legitimate empty result reported as an error, threshold still not exposed or tunable. See O8.
- **New: frontmatter fields are invisible to inference.** `status`/`decided-on`/`owner` were present
  in 100% of the 32 notes and never appeared in the frequency table — only the observation did.
  Third member of the frontmatter-blindness class (O2, O3): W4/W5's closed vocabulary cannot lean
  on `schema infer` for frontmatter keys.

### O6 — two unaudited write-path behaviours: non-unique replace, and move-vs-rewrite — **RESOLVED (replace) 2026-07-31; move root-caused; synchronous write-through SHIPPED 2026-07-31**
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

**Tested live 2026-07-31**, scratch project:

1. **Non-unique replace: safe.** `edit-note --operation find_replace` on a target occurring twice
   with the default `--expected-replacements 1` refused —
   `Error: Expected 1 occurrences of 'duplicate-needle', but found 2` — and left the file untouched.
   (The error still exits 0; see O8.) No corruption class here.
2. **Move: not `rename(2)` on the live path, despite the source pre-read.** `FileService.move_file`
   *is* an atomic rename, but the only v2 API move endpoint (`knowledge_router.py:772`) calls
   `note_content_mutation_service.move_note` → `run_accepted_note_move`, whose docstring says it
   plainly: *"Accept a note move into DB state without materializing its file."* The rename-based
   `entity_service` path (`entity_service.py:1074`) is not on it. Materialization later *writes* the
   content to the new path — fresh inode, fresh mtime. Observed: after flush the moved file had a
   new inode and a materialization-time mtime. **mtime is not truthful across moves**; W2's
   staleness logic must read `updated_at` from the index, never `stat()`.
3. **Worse, the deferred window corrupts.** With the process stopped between DB-accept and flush
   (same window as T12): DB said `moved/o6-dup.md` + `file_write_status=pending` while the file sat
   untouched at the old path — and the recovery command (`bm reindex`) then materialized the new
   path from the DB *and* re-indexed the leftover old file as a second entity. **An interrupted move
   plus reindex silently duplicates the note** (both `synced` afterwards). `bm gc` needs a dedupe
   check for exactly this shape, and W3's git history is the real safety net.

**Proposed root fix (2026-07-31, not yet scoped): synchronous write-through.** The DB-first
deferred-write architecture exists so a hosted runtime can accept writes without a filesystem —
and this fork stripped the hosted runtime (W12). On a local-only tree it buys nothing and is the
root cause of both this entry's move behaviour and T12: make move = `rename(2)` + index update in
one operation, and write = file first, index second, and the `pending` window disappears entirely.
Touches the mutation service and the materialization runner; scope it in phase 2/3 before the
verbs. The `bm reset` guard (T12's fix) is still worth shipping independently — it is cheap and
guards hand-edited or crashed states regardless.

**Scoped 2026-07-31 — DECISION: ship the inline write-through. SHIPPED same day.** After the
change, the production path re-measured median 116 ms/write with a 0.00 s drain (nothing ever
queued) — better than both pre-change variants.

 Full subsystem map captured
(worker pool + runner + recovery + status plumbing ≈ 4,000 src lines / ~300 tests), but the
write-through itself is small because the seam already exists: every router awaits
`materialize_write_change` (`index/note_content_materialization.py:447`), which *already runs the
full materialization inline under `test_mode`* and only defers to the in-process
`_MaterializationWorkerPool` in production. The change is "make production take the branch tests
have always taken," not a rewrite.

The deferral's stated justification was a "~3x write-load regression (measured; the benchmark
harness that produced the number has since been removed)" plus a cloud-parity invariant
("production must defer"). The cloud is gone (W12), and the figure **does not reproduce** —
same unreproducible-inherited-number pattern as T18. Measured 2026-07-31, scratch harness
(isolated HOME/config, dev env, in-process ASGI client, 50 `create_entity` calls after warmup,
deferred = production path, inline = `materialize_write_change` patched to
`_materialize_write_now`; two runs each, second run shown, first agreed):

```
mode=deferred n=50
per-write latency ms: median=87.9 p90=609.4 max=1929.4
request-loop wall s: 9.86  drain wall s: 2.29
total wall (writes+drain) s: 12.15
user CPU for 50 writes s: 36.54
mode=inline n=50
per-write latency ms: median=169.9 p90=257.8 max=910.9
request-loop wall s: 10.13  drain wall s: 0.00
total wall (writes+drain) s: 10.13
user CPU for 50 writes s: 36.42
```

Equal CPU, inline slightly *less* total wall, and inline's tail latency is better — the deferred
pool contends with incoming accepts on the same event loop, so deferral doesn't even buy accept
latency under load. Median per-write goes 88→170 ms because the response now waits for the file
and its index; that is the price of "return means on disk" and it is well under the W3 git-commit
budget conversation (12 ms/commit was called cheap; 80 ms for durability on a local tool is too).

**Touchpoints of the shipped change** (everything else stays):
- `index/note_content_materialization.py` — `materialize_write_change` always materializes
  inline; delete `_MaterializationWorkerPool`, `_materialization_pool`,
  `_schedule_materialization`, `drain_pending_materializations`, the `test_mode` /
  `materialization_workers` fields, and the cloud-parity docstring.
- Drain call sites: `api/app.py`, `mcp/server.py`, `cli/commands/command_utils.py`
  (`drain_background_tasks` stays — vector sync and relation resolution remain scheduled).
- `deps/services.py` provider wiring; `config_models.py` `materialization_workers` field.
- Tests of the pool/drain in `tests/index/test_note_content_materialization.py`,
  `tests/cli/test_command_utils.py`, `tests/mcp/test_server_lifespan_branches.py`, plus any
  fixture that set `materialization_workers`.

**What this closes:** the observable accept→flush window. When any v2 write/edit/move returns,
the file is on disk and indexed — the T12 kill-between-accept-and-flush repro and this entry's
interrupted-move duplication repro (kill after the CLI call completed) are dead as captured.

**Deliberately out of scope, and why:**
- **The `pending`/`writing` states themselves stay.** They now exist only transiently inside a
  request, plus as the crash-mid-request record. Removing them means inverting to file-first
  (rewriting `accepted_note_mutation_runner.py` (1,020 lines), `note_content_writes.py`, the
  reconciliation planners, and ~300 tests' worth of surface) for no observable behaviour change —
  that is the metastasis case the scoping was told to watch for. The recovery sweep
  (`recover_project_materializations`) and the T12 reset guard stay as the crash-mid-request
  defense; the guard's flush step now normally finds nothing.
- **Move stays write-new + delete-old (not `rename(2)`).** The rename-based
  `EntityService.move_entity` path exists but bypasses `note_content`; grafting it in is real
  design work and the mtime-untruthfulness it would fix is already handled by the standing rule
  (W2 reads `updated_at` from the index, never `stat()`). Revisit only if a real need for inode
  identity appears.
- **Concurrent same-note writes** could previously race the writer guard into false
  `external_change_detected`; the pool serialized them per note. Inline, the db_version
  compare-and-set in materialization preflight still refuses stale materializations, and
  same-note concurrent writes from a single local agent are effectively nonexistent. Accepted.
- **Routers still return 202.** The status code is now cosmetically wrong (the work is done, not
  accepted-for-later); folding it to 200 is wire-shape churn better taken with W7's error
  contract.
- W12 residue found by the mapping — **deleted in the follow-up commit**:
  `indexing/accepted_note_enqueue_runner.py` (no src caller; loaded only by its own 9 tests and
  one justfile recipe line), `runtime/job_payloads.py` (whole queue-job serialization module, no
  src consumer, 7 tests), `RepositoryNoteMaterializationFailureMarker` (served only the dead
  enqueue protocol, 2 tests), and `NoteMaterializationSessionLock`/Noop (existed solely so cloud
  could inject a PG advisory lock; the three repository adapters lose the field and the no-op
  await). `runtime/note_object_metadata.py` stays — prepared writes still carry object metadata
  through the writer signature; pulling it is accept-runner surgery for zero behaviour change.

### O7 — `add_project`'s default-repair logging is unformatted, and one of its branches is now dead — **SHIPPED 2026-07-31**
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

**Shipped 2026-07-31.** The three `'%s'` calls converted to loguru `{}` placeholders (interpolation
verified with a positive control; `'%s'` confirmed inert in the same run). **Judgment call: the
unreachable promote branch was deleted**, not kept under `# pragma: no cover` — house style forbids
speculative fallback, `BasicMemoryConfig.model_post_init` owns the invariant, and the only test that
ever exercised the branch was already replaced by the materialize test. The guarding `elif` stays, so
the impossible state (configured default registered nowhere) now falls through as a no-op instead of
mis-promoting. Comment block updated to say why the state cannot occur.

### O8 — CLI tool failures exit 0, and `--json` mode emits non-JSON or error-shaped prose
**Found:** 2026-07-31, recurring across every phase-1 evidence run. Three instances, all captured
in the entries that hit them:

1. A hard search failure (`sqlite3.OperationalError` from the `**` query, T7) printed a markdown
   error with the full SQL to stdout and **exited 0** — in `--json` mode, so the "machine-readable"
   stream contained prose.
2. `edit-note` refusing a non-unique find/replace (O6) printed `Error: Expected 1 occurrences…` and
   **exited 0**.
3. `schema infer --json` reports a legitimate no-pattern result as `{"error": …}`, exit 0 (O5) —
   the inverse defect: a non-error shaped as one.

Also in the same class: `search-notes --json` returned a result set whose envelope said
`"total": 0, "total_is_exact": false` alongside one actual result (query mode; queryless mode
reported `"total": 1` correctly).

**Why it matters:** W7 (the agent-facing output contract) is unbuildable on a surface where exit
codes never signal failure and the JSON stream is not reliably JSON. Every scripted caller must
currently parse prose to distinguish success from failure. Fold the fix into W7's contract work:
non-zero exit on failure, errors to stderr, `--json` output always parseable JSON.

---

## Docs swept

**2026-07-26.** A ten-reader sweep reconciled the following into this file. The gaps they contained
are now recorded here, and **the next sweep does not need to redo them:**

- The ten design docs now under `.forked/` (they lived at `~/develop/.design/status-system-*.md`
  when the sweep ran) — `prior-art`, `beans-deepdive`, `bm-spike`, `decisions`, `local-history`,
  `handoff`, `migration-handoff`, `inventory`, `plan`, `schema-draft`, and `transcript`.
- The old top-level session log.
- `AGENTS.md` in this repo.

Sweep these again only for material written *after* this date. Design decisions found in them about
the record schema or the work plan were deliberately left where they are — those docs own them.

---

## Where this connects

| | |
|---|---|
| Execution plan, phases, decisions | `.forked/plan.md` (local, gitignored) |
| Record schema (types, fields, supersession) | `.forked/schema.md` (local, gitignored) |
| Settled/reversed decisions with turn cites | `.forked/decisions.md` (local, gitignored) |
| Session-to-session state | `STATUS.local.md` (local, gitignored) |
| Fork point, remotes, license, measured baseline | `AGENTS.md` in this repo |
