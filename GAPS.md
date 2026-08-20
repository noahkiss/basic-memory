# GAPS — what this fork needs to fix

**This file is the running list of everything wrong with, or missing from, upstream Basic Memory
that our work depends on. It is the fork's to-do list and its rationale.**

## The rule

**When you find a gap, write it here in the same session you find it.** Do not leave it in a
design doc, a STATUS file, or a session summary and intend to transfer it later — that transfer is
the return visit that never happens. A gap recorded only in a local design doc is invisible to
anyone working in this repo, which is where it has to get fixed.

A gap belongs here if it is a thing **we would change in this codebase**. Design decisions about
the record schema live in `.forked/schema.md`, which is local and gitignored. This file is only
about the code. (`.forked/plan.md` and `.forked/campaign.md` held the work plan until the 2026-08-07
reconciliation deleted them — the build order now lives at the end of this file, the working rules
in `AGENTS.md`, and the cursor in `STATUS.local.md`.)

Each entry gets: what breaks, the evidence (a command and its actual output, not a description of
it), why it matters to us, and where it was found. Evidence matters because several figures in the
design docs turned out to be inherited and never re-checked; an entry without a reproduction is a
claim, not a gap.

**Order of work:** fix the gaps that block the thing being built next, then build. Not the reverse.

**An entry's HEADING is its status field, not its title.** A commit that fixes, refutes, or reshapes
a gap must edit that gap's heading in the same commit — append the em-dash **SHIPPED `<sha>`** /
**RESOLVED** / **REFUTED** marker the closed entries below already use, or restate the claim the
heading makes if the fix only narrowed it.
Amending the body and leaving the heading alone is how this file rots: on 2026-08-03 an audit found
**9 of 20 open entries carrying falsified premises**, every one of them a heading that still
advertised a defect its own body recorded as fixed. A reader who greps headings — which is how this
file is read — was being told the opposite of the truth. Body-only amendments are not a status
update.

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

**Amended 2026-08-03 — the test harness and `just doctor` defeated the fix.** Both redirect `HOME`
at a temp dir, so `shared_fastembed_cache_dir()`'s `Path.home() / ".cache"` resolved *inside* that
temp dir and the semantic path re-downloaded all 64 MB per run:

```
$ du -sh ~/.cache/fastembed
65M	/home/<user>/.cache/fastembed
$ du -sh /tmp/pytest-of-.../test_local_watcher_embeds_inde0/.cache/fastembed
65M	                                       # a whole second copy, thrown away with the run
```

Fixed by pinning `FASTEMBED_CACHE_PATH` to the host cache resolved at conftest import time (before
any fixture patches `HOME`) in both `tests/conftest.py` and `test-int/conftest.py`, and to the real
`$HOME` before the redirect in the `doctor` recipe. The resolution-order tests clear the variable
themselves, so they still exercise every branch. After the fix the same test leaves a 16 KB basetemp
and runs in 2.25 s.

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
$ cd <claude-code projects dir>
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

### T3 — `bm --version` reports upstream's version, not the installed build — **SHIPPED `9e4f3c8c`**
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

**FOLLOW-UP 2026-08-17, with the `v0.1.0` cut:** the release fallback literal `__version__ =
"0.22.1"` is deleted. `_resolve_version()` takes no argument and returns `("0.0.0", False)` when no
distribution is installed, so there is no stale number left in the tree to report and `just release`
writes no files. The literal was the last thing keeping upstream's number in this repo.

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

### T9 — the permalink is the only identity BM honours, and it is neither stable nor verbatim — **DECIDED + SHIPPED 2026-07-31: id == permalink set-once, checked by `bm doctor --project`**
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

### T10 — `build-context` silently resolves a miss to an arbitrary note — **SHIPPED `829e5af5`**
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

### T11 — no newer-schema guard: an older build over a newer DB dies in a raw stack trace — **SHIPPED 2026-08-10**
**Found:** 2026-07-27, in `.forked/release-design.md` §2. Code sites re-verified before recording.

`run_migrations` (`src/basic_memory/db.py:401`) builds the Alembic config and calls
`command.upgrade(config, "head")` at `db.py:428`. It never reads the database's `alembic_version`
to compare it against the code's head revision, and nothing in `src/` ever calls
`command.downgrade`:

```
$ grep -rn "alembic_version\|command.downgrade\|get_current_head\|MigrationContext" src/ | grep -v "/alembic/"
src/basic_memory/db.py:406:    Note: Alembic tracks which migrations have been applied via the alembic_version table,
```

One hit in the whole of `src/` outside the migration directory itself, and it is a **comment**.
So installing an *older* build over a database a *newer* build already migrated raises out of the
generic `except Exception` re-raise at `db.py:443-445` — the user gets Alembic's internal error and
no actionable message.

*(Line numbers re-verified against `src/basic_memory/db.py` on 2026-08-03; they had drifted from the
2026-07-27 capture — 525/530/554/577 then, 401/406/428/443 now. The single-hit grep result is
unchanged.)*

**Why it matters:** in this fork "upgrade" means `git pull` + reinstall, so **rollback is a normal
operation**, not an exotic one — `git checkout <older-commit> && uv tool install --reinstall .` is
one command away. Of all the migration hazards in this tree this is the only one that actually
bites this install.

**Fix:** read `alembic_version` before `command.upgrade`, and if it is a revision the shipped
script directory does not contain, fail with something like "this database was migrated by a newer
Basic Memory; reinstall the newer build or run `bm reset --reindex`".

**Amended 2026-08-06 — the fix must also correct the exit code.** Both captured shapes exit 0 on a
hard failure. W20 rule 6 makes that a contract violation, not just a wart: errors exit 1 with the
message on its own line. No design decision is open on this entry; it is a build task.

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

**SHIPPED 2026-08-10.** The exit-code half had already been fixed by the phase-4 exit-code work —
re-reproduced before building: both shapes exited 1 on this tree. What shipped is the guard:

- `_assert_no_newer_stamp()` (`src/basic_memory/db.py`) runs in `get_or_create_db` after the B4
  stamp check fails and before `run_migrations`. It reads `alembic_version` and compares against
  the revisions parsed from `alembic/versions/*.py` (shared `_scan_migration_files()`, split out
  of `_single_alembic_head`). A stamp the shipped tree has never seen raises `NewerSchemaError`
  with the actionable message; every doubtful case (unparseable files, no stamp table) falls
  through to the real migration run. No alembic import — the import guard stays satisfied.
- `run_with_cleanup` (`cli/runner.py` since T30; `cli/commands/command_utils.py` when this shipped)
  catches `NewerSchemaError`: message on its
  own line, exit 1 (W20 rule 6). One catch covers every DB-touching CLI verb.
- **Found while verifying: the advertised way out was circular.** `bm reset` runs two pre-delete
  reads (`_flush_unflushed_note_content`, `_snapshot_registry`) through `get_or_create_db`, which
  migrated — so against a newer DB, reset died telling you to run reset (and before this fix it
  died in `command.upgrade` the same way). Both now pass `ensure_migrations=False`: never migrate
  a database the reset is about to delete.

Verified end-to-end in a scratch config stamped `zzznewer999`: `bm project list` and `bm reindex`
each print the three-line message, exit 1, no traceback; `echo y | bm reset --reindex` exits 0,
restamps at head (`n7i8j9k0l1m2`), and `project list` works after. Tests:
`tests/db/test_migration_head_stamp.py` (guard raises on unknown stamp, passes on fresh and
stale-known), `tests/cli/test_runner.py` (exit 1 + message + cleanup still runs; the file was
`tests/cli/test_command_utils.py` until T30 moved the runner).

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

### T17 — 10 integration tests fail on `main`; `test-int/` was never run against the W13 tree — **SHIPPED `879681d4`: all ten fixed, `test-int` baseline green**
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

### T20 — no coverage ratchet: nothing enforces the 96% floor `AGENTS.md` now states — **CLOSED 2026-08-10: `fail_under = 93.8` lands, measured with the CLI counted**

**The ratchet exists now.** 2026-08-07 added `fail_under` to `[tool.coverage.report]` and removed
the `*/cli/**` omit (this fork's verbs land in `cli/`; omitting it would hide them from coverage on
the day they ship). The first run with the CLI counted, 2026-08-10:

```
=========== 3644 passed, 5 skipped, 6 warnings in 2110.03s (0:35:10) ===========
TOTAL                                                            21216   1309    94%
error: recipe `coverage` failed with exit code 2      # fail_under = 96 correctly tripped
```

Precise total 93.83% — the displayed 94% is rounding, so `fail_under = 94` would start red.
`fail_under` is set to **93.8**, the measured floor. The old 96% (19463/866) was measured with the
CLI omitted and is not comparable; `AGENTS.md` now quotes the honest pair. The floor is still a
backstop, not a gate — no CI, ~35-minute run — and the ratchet rule (raise as tested code lands,
never lower) is unchanged.

Nothing else survives the close: the `*/db.py` exclusion claim below was already verified false
(the entry's own 2026-08-07 note), and the two remaining omits (`*/watch_service.py`,
`*/services/initialization.py`) are documented in `pyproject.toml` itself.

*Entry as it stood before closing:*

**Found:** 2026-07-29, running the campaign's Phase 0 `just coverage` item. **Rewritten down to its
surviving claim 2026-08-03** — half of it shipped and the heading never said so.

**Dead half — the documentation defect is fixed.** `8bff120e` replaced the false
*"Coverage must stay at 100%"* rule with the measured number; `AGENTS.md` now reads *"Coverage is
96%, measured, and must not go down"* and cites `19463` statements / `866` missed on 2026-07-29.
Nothing in this entry's original text about `AGENTS.md` is still true.

**Surviving claim — the ratchet does not exist.** The rule is still enforced by nothing:

```
$ grep -n "fail_under\|fail-under" pyproject.toml justfile
$ echo $?
1
$ grep -n "tool.coverage" pyproject.toml
144:[tool.coverage.run]
149:[tool.coverage.report]
```

Positive control: `[tool.coverage.report]` **does** exist at `pyproject.toml:149`, so the grep is
looking in a section that is there and simply has no `fail_under` key. No recipe passes
`--cov-fail-under`, and there is no CI (`just gate` is lint + typecheck + unit tests). So "must not
go down" is advisory prose, and the next regression is silent — which is how the 100% claim managed
to be false for an unknown length of time in the first place. The fix below is unchanged and still
owed.

*Original entry:*

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

**A `db.py` coverage exclusion was reported and does not exist — but checking it found a real one.**
The reconciliation pass (2026-08-07) rescued a claim from `.forked/w13-postgres-inventory.md` that
`pyproject.toml` omits `*/db.py` from coverage, justified by backend-dependent code that W13 then
deleted. **Verified false:**

```
$ grep -n "db\.py" pyproject.toml
$ echo $?
1
$ grep -c "watch_service.py" pyproject.toml     # positive control
1
```

Either the exclusion was already removed or the inventory was wrong. Recorded because it is a clean
instance of the rule it violated — *agent self-reports are leads, not records* — and this entry
briefly carried the unchecked claim.

**The real finding, from the same check.** The omit list is `*/watch_service.py`,
`*/services/initialization.py`, and **`*/cli/**`** — the last justified as *"CLI is an interactive
wrapper; core logic is covered via API/MCP/service tests."* That premise is being reversed by this
fork's own roadmap: every verb in `AGENTS.md`'s flat list lands in `cli/`, `cli/direct.py` is now the
supported route to the service layer, and W5's notice machinery is CLI-side. **The verbs will be
invisible to coverage the day they ship**, and the 96% floor will not notice. Revisit the `cli/**`
omit before, not after, the verb work starts.

**Also rescued, from `.forked/audit-2026-08-03-test-suite.md`** — the suite-speed ceiling, so it is
not re-derived:

- **Do xdist first, re-measure, and do not delete tests for speed.** xdist shipped
  (`justfile:7`, `-n auto --dist loadfile`; the `loadfile` choice is load-bearing — `tests/mcp`
  mutates the module-level FastAPI app). Fixture-level speedups are capped around 30–45 s by
  `test_graph`'s corpus fixture and the function-scoped `engine_factory` (`tests/conftest.py:173`).
- **One unactioned recommendation with no other home:** consolidate the MCP security and identifier
  test matrices so they stop running against the ASGI stack — roughly 40 s of a 92 s phase. Caveat
  from the audit: it is the only cut that touches Core, and `test-int/` owns the real-filesystem
  coverage, so it needs care.
- The audit's baseline counts are **stale and internally contradictory** (it claimed 236 picoschema
  tests in one place and 134 in another; 134 is correct today). Re-measure; do not inherit.

### T21 — `--filter` on a DB-column date silently matches nothing and exits 0 — **CLOSED 2026-08-16: column names are refused by the filter grammar**

**Close block, 2026-08-16.** The grammar now rejects entity column names, per the decided fix.

- **One list, one message, three surfaces.** `repository/metadata_filters.py` holds
  `_ENTITY_COLUMN_GUIDANCE` and `validate_metadata_filter_keys(...)`, called from the top of
  `parse_metadata_filters` and from `search_notes` before any project routing. A refused key
  raises `ValueError`, which is already a **400** at the API
  (`api/v2/routers/search_router.py` maps it), a **ToolError** on MCP, and **exit 1 with the
  message on stderr** on `bm tool` (`cli/commands/tool.py` catches `ValueError` into `_fail`).
  The message is one line, per `docs/OUTPUT_CONTRACT.md` rule 6.
- **The list is every `Entity` column except the three that are real frontmatter.** `title`,
  `type` and `permalink` are **not** rejected: `markdown/utils.py` stores every raw frontmatter
  key in `entity_metadata`, basic-memory writes all three into frontmatter, and
  `entity_repository.py` already reads `json_extract(entity_metadata, '$.permalink')`. Rejecting
  them would have broken filters that work today. A test walks `Entity.__table__.columns` and
  fails if a new column is added without being classified — the trap this entry describes is a
  column name nobody thought about, so the guard has to notice new ones.
- **A dotted key is frontmatter, not a column.** `schema.updated_at` parses normally; only a
  bare single-segment key can collide, and the match is case-insensitive.
- **`note_type` keeps its alias and never reaches the refusal from the tool.** `search_notes`
  aliases `note_type` → `type` (#642) and *then* validates, so the alias still works. The alias
  table moved to module scope and the validation sits beside it, which also fixes the
  all-projects fan-out: both used to run after project routing. `note_type` is still in the
  refusal list for callers that reach the API or repository directly, where no alias applies.
- **The question the brief asked — keep a note carrying literal `updated_at:` frontmatter
  reachable?** No. A note may legitimately carry the key, and that filter now fails rather than
  answering. Rejecting is still safer: a filter that silently reads the wrong store is the O8
  class, while a refusal is visible and names the fix. Anyone who genuinely stores a date in
  frontmatter can spell the key something other than a column name.
- **Tests drive the real caller paths, each with a positive control.**
  `tests/cli/test_cli_tool_json_output.py` runs the unmocked `bm tool search-notes --filter
  '{"updated_at": ...}'` and asserts exit 1, empty stdout, and the message on stderr;
  `tests/mcp/test_tool_search.py` raises through the live tool for four column names and then
  filters the same corpus by a real frontmatter key; `tests/api/v2/test_search_router.py`
  asserts the 400 and then a 200 over the same entity.

**Opened 2026-08-04**, promoted out of **O4**. O4 established this as its fact 1 and then shipped
its *fix* at the repository layer only — so the entry reads as closed while the trap is still live
on the user-facing path. A defect recorded inside a SHIPPED entry is a defect nobody will find.

`--filter '{"updated_at":{"$lt":"…"}}'` compiles to
`json_extract(entity.entity_metadata, '$."updated_at"') < ?` — it reads **frontmatter**, not the
`entity.updated_at` column. Since notes carry no literal `updated_at:` frontmatter key, the filter
matches nothing:

```
$ bm tool search-notes "**" --permalink --filter '{"updated_at":{"$lt":"2026-08-15"}}' --project <p> --json
{"total": 0}                          # over a corpus where every note qualifies
exit=0
```

**Why it matters:** this is the O8 class — a wrong answer indistinguishable from a true empty
result, at exit 0. Any date-column name a user reasonably reaches for (`updated_at`, `created_at`)
lands in the frontmatter store instead, so the query is not merely unsupported, it is *silently
answered wrong*. The repository-level `before_date` + `order_by` that O4 shipped is unreachable
from the MCP filter grammar and does not mitigate this.

**Fix, one of two:** either reject DB-column names in the frontmatter filter grammar with an error
naming `before_date`/`order_by` as the supported path, or route them to the column. **Rejecting is
the smaller, safer change** and matches the fail-fast rule — routing means the grammar has to know
which names are columns, and gets it wrong the moment a note legitimately carries an
`updated_at:` frontmatter key.

### T22 — W4's reject mode is unreachable: no note write goes through `EntityService` — **CLOSED 2026-08-16: the funnel moved wholesale to the accepted-state write path**

**Close block, 2026-08-16.** Enforcement **moved**; it was not duplicated. The user's decision was
one funnel, one layer, and this is what that cost.

- **One implementation, two entry points.** `services/vocabulary_enforcement.py` holds
  `enforce_vocabulary(...)` and `apply_vocabulary(...)`. The checker, the loader, the glossary, and
  every message are unchanged — T22 was never a defect in any of them.
- **Reject mode now lives in `indexing/accepted_note_mutation_runner.py`.** All four write runners
  (`_run_accepted_note_create` / `_update` / `_edit` / `_move`) call
  `enforce_accepted_note_vocabulary` **after prepare and before persist**. Prepare derives the
  markdown the note will carry, which is the only thing worth judging. Prepare is not read-only —
  it stamps the accepted fields onto the entity row and flushes — so what makes a rejection mean
  "nothing happened" is the transaction, not the ordering: the whole mutation sits inside
  `accepted_note_transaction`'s `session.begin()`, the raise rolls back every flush, and file
  materialization, which runs only after the mutation returns, never happens.
- **The funnel no longer takes a session, and cannot deadlock.** A project's vocabulary is a file
  keyed by `external_id`, and every runner already holds the `Project` row. W4's 30 s
  nested-acquire hazard is now structurally impossible on this path rather than avoided by
  discipline.
- **`move_directory` stopped being a second entry layer.** The endpoint used to call
  `EntityService.move_directory`, which looped `move_entity`. It now reads the batch out of one
  session and loops `note_content_mutation_service.move_note` — the same call the single-note move
  endpoint makes. Per-note rejections become `DirectoryMoveError` rows instead of raising, so one
  refused note does not strand the batch half-moved. `EntityService.move_directory` is deleted.
  The endpoint's `search_service.index_entity` loop went with it: materialization reindexes each
  moved file synchronously, so that loop was doing the same work twice (see **T25**, which filed
  its removal as a regression and was closed on that finding). Non-markdown entities in the
  directory take a path-only arm, because the accepted path is markdown-only (**T26**).
- **`EntityService` keeps record mode and nothing else.** `_enforce_vocabulary` is now
  `_record_vocabulary_violations` and has no mode parameter; the four reject call sites and the
  `vocabulary_checked=True` delegations at those sites are gone. Behaviour on the sync path is
  unchanged. The per-instance vocabulary cache is kept deliberately — a reindex must not re-read
  the file per note, and W5 still owes the revalidation trigger W4 recorded.
- **The guard moved to the layer callers use.** `tests/services/test_entity_service_funnel_guard.py`
  became `tests/services/test_vocabulary_funnel_guard.py`: it now guards the runner's four write
  paths for reject mode *and* `EntityService`'s public mutators for record mode, with a positive
  control for each of the two AST walks. `move_entity` is allowlisted — it is no longer on any
  agent path.
- **The regression drives the real caller path**, which is the whole lesson of this entry.
  `tests/mcp/test_tool_vocabulary_enforcement.py` calls `write_note`, `edit_note`, and `move_note`
  over the live ASGI app: the T22 reproduction is refused with nothing on disk, an on-vocabulary
  write of the same shape succeeds, an ungoverned project still accepts `type: note`, `overwrite`
  is refused on a set-once change, an edit of an off-vocabulary note is refused, a move that would
  rewrite the permalink is refused while one that would not succeeds, and a directory move reports
  the refused note as a failed move. Nine tests; every refusal has its positive control.

**Judgment calls, both stated rather than silent.**

1. **The rejection message is now one line.** `VocabularyViolationError` joined violations with
   newlines and a bullet each. That string travels: HTTP 400 detail → MCP `ToolError` → `bm tool`
   stderr. `docs/OUTPUT_CONTRACT.md` rule 6 puts an error message on its own line, so the
   violations are joined with a space instead. No message text changed.
2. **`EntityService.move_entity` is kept, not deleted.** It has no production caller left, but 21
   test references, and removing it is a change of a different size. Recorded as **T24** below.

**Found while closing this, and fixed here** (it would have made the fix untestable on the
`overwrite=True` path): `mcp/tools/write_note.py` masked the replacement's own failure. On a
create-409 it retried as an update and, if the update failed, re-raised the *original* conflict —
so a vocabulary rejection on an overwrite surfaced as "note already exists". Both lines carried
`# pragma: no cover`, which is why nothing had seen it. It now raises the update's error with the
conflict on the exception chain.

**Also closed here, though it was owed to W5:** the checker short-circuited on `unknown-type` and
lost `set-once-changed`. Set-once compares this write against the previous one field by field and
never consults the type, so it is decidable when nothing else is — and a write that changes `type`
to an undeclared value breaks both rules at once. It now reports both.

---

**Opened 2026-08-10**, found by running the shipped W4 build against a real project rather than a
fixture. W4 ships a funnel whose central promise is *"the caller declares the mode — **reject**
(verbs, MCP, API) or **record-violation** (the sync path)"*. **Reject never fires.** Every note
write in this fork reaches the database through a path that does not touch `EntityService` at all.

**Reproduction**, isolated config dir, project governed by a `vocabulary.yml` with the six types:

```
$ bm tool write-note --title "Another Ordinary Note" --folder notes --content "Just a note."
permalink: main/notes/another-ordinary-note
action: created                      # accepted, written to disk, exit 0
```

The frontmatter written is `type: note`, which is off-vocabulary and should have been refused. The
violation *is* detected — a moment later, on the indexer's pass, in record mode:

```
$ grep "Vocabulary violation" <config-dir>/basic-memory.log
WARNING | entity_service:_enforce_vocabulary:238 - Vocabulary violation in
notes/Another Ordinary Note.md: Type 'note' is not in this project's vocabulary. Pick one of:
task (do it), guide (consult it), … A new type cannot be enabled from a write.
```

**Root cause, verified by reading rather than inferred.** `POST /knowledge/entities`
(`api/v2/routers/knowledge_router.py:559`) calls `note_content_mutation_service.create_note(...)`,
which writes accepted state and then materializes the file. `EntityService` is never on that path.
Outside `entity_service.py` itself, the only mutator any caller reaches is
`upsert_entity_from_markdown`, from `indexing/batch_indexer.py:613` — and that is record mode by
design, because it is the sync path. The one exception is `move_directory`
(`knowledge_router.py:835`), the only API endpoint that takes an `EntityService` for a mutation.

So `create_entity_with_content`, `update_entity_with_content`, `edit_entity_with_content`, and
`move_entity` — every reject-mode call site — have **no external callers in this fork**. The funnel
is correct; it is wired to a layer this fork stopped writing through.

**Why the W4 build did not catch it.** The entry says `entity_service.py:368
create_entity_with_content` *"is the agent write path"*. That was true when the sentence was
written and is not true now, and every W4 test drives `EntityService` directly, so the tests agree
with the stale premise. The funnel guard test proves every mutator *reaches* the funnel; nothing
proved anything *reaches the mutators*. **A guard over a layer proves nothing about whether callers
use that layer** — this is the general lesson, and it is the same shape as the positive-control
rule: the W4 suite could not have produced a rejection on a real path, so its green was not
evidence.

**What still works, so the fix is scoped and not a rebuild.** Record mode is live and correct
end-to-end: hand-edited and agent-written off-vocabulary files are detected, carry the right
plain-English message, and are indexed anyway — which is W4's stated sync-path behaviour. The
checker, the vocabulary loader, `bm types`, and the messages need no change.

**Owed:** enforcement has to move to, or be added at, the accepted-state write path
(`services/note_content_writes.py`, `run_accepted_note_create` and its update/edit siblings) so a
governed project refuses the write before it is accepted. Open question for that work: whether the
funnel belongs in both layers or moves wholesale, given `move_directory` still enters through
`EntityService`. **Decide before W5** — W5's `bm doctor` reporting assumes violations are the
exception, and today they are the only outcome.

**Confirmed independently 2026-08-10** by a cross-model review of the funnel diff, which traced the
same chain from the MCP tools down (`mcp/tools/write_note.py:236` → `mcp/clients/knowledge.py` →
`knowledge_router.py:533-810` → `NoteContentMutationService`) and ran the positive control this
entry should have: `grep -n "vocabulary\|enforce"` over `services/note_content_writes.py`,
`services/note_preparation.py`, and `indexing/accepted_note_write_runner.py` returns nothing, while
the same pattern over `entity_service.py` returns dozens of hits. The review also confirmed, call
site by call site, that the five reject-mode calls inside `EntityService` are each correct in mode,
in `previous`, and in running before the file write — the defect is entirely one of which layer
callers use, not of the funnel itself.

**Also found, and owed to W5 rather than here:** the checker short-circuits on `unknown-type`
(`vocabulary/checker.py:122-124`), so a record-mode write that changes `type` to an undeclared value
records `unknown-type` and loses the `set-once-changed` violation that W5's table would want. It is
harmless on a reject path, where the write stops either way.

### T23 — a move on disk rewrites the set-once `permalink` and the checker never sees it — **CLOSED 2026-08-16: the move planner records the violation, and then does not do the rewrite**

**Close block, 2026-08-16.**

- **The check runs at move time, in the planner.** `LocalProjectIndexMoveContentUpdater.plan_moved_file_content`
  (`src/basic_memory/index/local_moves.py`) calls `enforce_vocabulary(..., mode="record")` after it
  resolves the new permalink and before it plans any bytes. That placement is the entry's whole
  point: the batch stamps the rows with the planned content's checksum, so a rewritten file never
  presents as modified and no later index pass could see it. Nothing here relies on re-indexing.
- **One implementation, a third entry point.** The funnel is unchanged. It has taken no session
  since T22, so the move batch's open transaction is not a hazard and the one-connection deadlock
  W4 recorded cannot arise. Record mode already logs the WARNING itself, with the sync path's
  message text, so this path adds no second message.
- **The project's `external_id` now rides in `LocalIndexProjectDependencies`.** The vocabulary file
  is keyed by it and the updater held only the integer PK. Both runtime factories — scan
  (`local_project.py`) and watcher (`local_runtime.py`) — pass it to the updater. Carrying the id
  beats a lookup: reading a project row inside the move batch's own transaction is exactly the
  shape W4 got bitten by.

**Judgment call — record *and* skip, not record only.** The brief left this open. The design docs
settle it: the permalink is this fork's identity, `id == permalink` is set-once (**T9**), edges bind
to it, and rewriting it orphans every relation pointing at the record. **T22** already refuses this
exact rewrite on the accepted path, so recording it here while still performing it would leave the
watcher as the one door through which identity silently changes. A `set-once-changed` violation on
`permalink` therefore returns `None` from the planner — the pre-existing signal for "no content
update" — which leaves both the file and the entity's permalink as the human left them.

**The skip is narrow on purpose.** Only a `set-once-changed` violation on `permalink` stops the
rewrite (`_changes_set_once_permalink`). An off-vocabulary note — a bad `type`, a missing required
field — is recorded like any other hand-edit and still gets its permalink kept current. Bundling
those would make an unrelated frontmatter mistake silently change move behaviour.

**In practice, on a governed project, a watcher move no longer rewrites permalinks at all.** The
indexer stamps the resolved permalink into frontmatter at first index, so every indexed markdown
file has one, and every later move is a change rather than a first set. That is the intended
outcome, not a side effect; the narrow rule is what keeps it true for the right reason and testable
either way.

**A malformed `vocabulary.yml` now aborts a move batch** with `VocabularyError`, as it already
aborts a write on the sync path. Deliberate and unchanged in spirit: "ungoverned" and "governed by
a file with a typo in it" must not become the same state.

**Tests.** Two drive the real move path with a real database
(`tests/index/test_local_project_index.py`): a governed project with `update_permalinks_on_move`
on logs `'permalink' is set once and cannot change` and keeps both the file's and the row's
permalink; the same project with the flag off logs nothing and changes nothing. The pre-existing
ungoverned move test is the third control — it still rewrites the permalink and now asserts that it
records nothing. Two more sit at the planner
(`tests/index/test_local_move_content_updates.py`) and prove the narrowing: a note with a previous
permalink is skipped, while a note with none takes a first permalink normally even though it is
off-vocabulary in `type`. The `logged_warnings` sink moved to `tests/conftest.py`; loguru does not
feed `caplog`, and two files now need it.

**Owed to W5, and stated here so it is not rediscovered:** this violation must reach W5's table
too. Two paths now feed record mode — `EntityService` (sync) and this planner (moves) — and W5's
mechanism A has to persist from both. The move path is the one that cannot be recovered by a
reindex, so a table fed only from the sync path would silently lose exactly these rows.

**Owed persistence DONE 2026-08-16 (W5 item 3), and narrowed to the rewrite arm.**
`plan_moved_file_content` writes rows through `ViolationRepository.replace_for_entity`, on the move
batch's own session, **only where it rewrites the permalink** — bytes that reach disk.
`LocalProjectIndexMoveContentUpdater` gained a `project_id` field for it, passed by both runtime
factories beside the `external_id` this entry added. The planner parses no relations, so it
preserves the rules that read them rather than clearing them (see W5's item 3+4 PROGRESS block).

**The refused arm persists nothing**, and the WARNING this entry added is its whole record.
Reversed 2026-08-16 from "persist on both arms", which this block first recorded. Three reasons, in
order: the refusal changes no file, so the rows the record's last index pass wrote still describe
it; every violation on that arm was judged against a permalink the arm declines to write, so no row
would describe the file that survives; and a `set-once-changed` row on every hand move is a
permanent nag over a refusal that already did its job, which nothing but a re-index of an unchanged
file could clear. The narrower `permalink-mismatch` filter the first pass added is gone with it —
it existed only to make the refused arm's row set describable.

Covered by `test_local_project_index_refused_move_persists_no_violation_and_logs_it` on the real
move path, which asserts the log line as the positive control for the empty row set. The rewrite
arm is **unreachable governed over that path** — the first index pass stamps `permalink` into the
file's frontmatter, so every later governed move is a set-once change — so its integration control
is the ungoverned move beside it, asserting empty rows, and its write is proved at the planner by
`test_plan_moved_file_content_persists_violations_when_it_rewrites`.

---

**Opened 2026-08-10**, found by the same cross-model review that confirmed T22 and verified here by
reading `src/basic_memory/index/local_moves.py:110-152`. **Filed separately from T22 on purpose:**
fixing T22 at the accepted-state write path does not touch this path, and folding it in would let a
T22 fix appear to close it.

When `update_permalinks_on_move` is on, the local watcher's move handler resolves a new permalink,
merges it into the file's frontmatter, and writes the file itself through
`file_service.write_file`. No funnel call in any mode — **not even record**. Worse than T22 in one
respect: T22 at least logs the violation on the indexer's next pass, and this cannot, because the
move batch deliberately stamps the entity and `note_content` rows with the planned content's
checksum so file and database agree. The file therefore never presents as modified and is never
re-indexed. The invariant is documented in the code and is correct for its own purpose; the
consequence for W4 is that the write is invisible.

Concrete case: `mv a.md sub/b.md` inside a governed store silently rewrites `permalink` — the
strictest set-once field, the one edges bind to, the one whose rewrite orphans every relation
pointing at the record. `EntityService.move_entity` rejects exactly this rewrite on a governed
project; the watcher path performs it with no check at all.

A hand-move is a human act, so **reject is the wrong answer here** — §4 says a human editing a file
by hand is not an error. The right answer is that this path must reach the funnel in **record**
mode, which in turn needs the violation to survive the checksum stamp that suppresses re-indexing.
That is the real work, and it is why this is not a one-line fix.

### T24 — `EntityService.move_entity` has no production caller left — **CLOSED 2026-08-17: deleted, with its 13 tests and its funnel allowlist**
**Opened 2026-08-16** while closing T22. Every note move now goes through
`indexing/accepted_note_mutation_runner.py`: the single-note endpoint always did, and
`move_directory` was rewritten to. `EntityService.move_directory`, its last caller, is deleted.

**Reproduction**, verbatim:

```
$ git grep -n "\.move_entity(" -- src/
src/basic_memory/mcp/tools/move_note.py:926:            result = await knowledge_client.move_entity(resolved_entity_id, destination_path)
```

The one hit is `KnowledgeClient.move_entity`, an HTTP call to the v2 endpoint of the same name — not
`EntityService.move_entity`. **Positive control**, the same grep shape for a method that *is* live:

```
$ git grep -n "\.upsert_entity_from_markdown(" -- src/
src/basic_memory/indexing/batch_indexer.py:613:            entity = await self.entity_service.upsert_entity_from_markdown(
src/basic_memory/services/entity_service.py:479:            entity = await self.upsert_entity_from_markdown(
src/basic_memory/services/entity_service.py:542:            entity = await self.upsert_entity_from_markdown(
src/basic_memory/services/entity_service.py:1067:            entity = await self.upsert_entity_from_markdown(
```

An external caller plus internal ones is what a live service method looks like. `move_entity` has
neither.

**Not deleted in the T22 pass on purpose:** 21 test references, so removing it is a change of a
different size, and T22's diff was already the funnel move plus a router rewrite. It is dead
production code and this fork deletes those (`AGENTS.md`: *"Don't spend tokens on code we will
never run"*). The work is: delete the method, delete or repoint the tests that exercise it, and
drop `move_entity` from `PATH_ONLY_WRITE` in `tests/services/test_vocabulary_funnel_guard.py`.

Note the method is **not** guarded by the vocabulary funnel any more — it is allowlisted in that
guard file. So it must not acquire a caller; if one is ever wanted, route it through the runner
instead.

**Close block, 2026-08-17 — deleted.** The method is gone; every move goes through
`indexing/accepted_note_mutation_runner.py`, as T22 left it.

- `src/basic_memory/services/entity_service.py` — the 104-line `move_entity` removed, plus the two
  imports it was the last user of: `config.ProjectConfig` (the method's `project_config` parameter
  was its only reference) and `runtime.note_move.normalize_note_move_destination_path`. That helper
  is still live for the accepted path and `note_preparation`, so only the import went.
- `tests/services/test_entity_service.py` — the whole `# Move entity tests` block, **12** tests.
- `tests/services/test_entity_service_disable_permalinks.py` —
  `test_move_entity_with_permalinks_disabled`, **1** test.
- `tests/services/test_vocabulary_funnel_guard.py` — `PATH_ONLY_WRITE` removed entirely rather than
  emptied. `move_entity` was its only entry, and `test_allowlists_name_only_methods_that_exist`
  fails on an allowlist naming a method that no longer exists — which is that test working. The
  `SERVICE_HINT` text lost its `PATH_ONLY_WRITE` arm to match.
- **Test count: 0 `def test_` added, 13 removed.** All 13 called `entity_service.move_entity`
  directly and had no second subject.
- **Judgment call — nothing else was deleted.** `prepare_move_entity_content` shares the name and is
  live on the accepted path (`accepted_note_write_runner.py:442`); it stays, and stays in the
  guard's `READ_ONLY`. `KnowledgeClient.move_entity` and the v2 router's `move_entity` endpoint are
  the live HTTP pair the entry's grep already distinguished; untouched.

### T25 — "an accepted note move deletes the note's observation and relation search rows" — **CLOSED 2026-08-16, no code change: the premise was wrong**

**Opened and closed the same day.** Filed during review of the T22 diff, on the grounds that
`AcceptedNoteSearchRepository.refresh_entity` deletes every `search_index` row for an entity and
inserts one back, so a move would strip the note's `OBSERVATION` and `RELATION` rows — and that
T22's `move_directory` rewrite had dropped the router loop that used to repair them.

The first half is true and the second half does not matter. **The accept path already reindexes
the file it materializes, synchronously, in the same request:**

```
$ sed -n '383,388p' src/basic_memory/index/note_content_materialization.py
        file_path = note_content_payload_file_path(accepted.payload)
        if file_path is not None and self.file_indexer is not None:
            await self.file_indexer.index_file(
                file_path,
                source="note-content-materialization",
            )
```

`index_file` runs the full file-index path, which reaches
`SearchService.index_entity_markdown` — "Index an entity and all its observations and relations".
So the rows are rebuilt a moment after `refresh_entity` clears them, on every accepted write, move
included. The router loop T22 removed was doing the same work a second time.

**A runner-side rebuild was built for this and then reverted.** Keeping it would have meant two
mechanisms for one job, with the redundant one carrying a widened `AcceptedNoteSearchRow`, a second
repository insert method, and two more row builders to keep in step with `index_entity_markdown` —
the exact "two paths writing different rows for the same note" hazard it was written to avoid.

**What survives:** `tests/mcp/test_tool_move_note_search_rows.py`
`test_move_keeps_observation_and_relation_search_rows`, kept as a behaviour test. Nothing else
asserts that a move leaves no stale `file_path` on those rows —
`test-int/mcp/test_move_directory_integration.py` only queries body text, which lives on the entity
row.

**The lesson**, stated in full in **T27**, which this entry produced: the review stopped at
`refresh_entity` and never followed the request past the mutation into materialization. A claim
that a write path loses state has to follow the request to its end, not to the end of the function
being read.

### T26 — a directory move now refuses every non-markdown entity in the directory — **CLOSED 2026-08-16: non-markdown entities take a path-only arm**

**Close block, 2026-08-16.** Found in review of the T22 diff and fixed in the same commit. The
entry's first question — should a directory move carry non-markdown entities at all — is answered
**yes**: it did before T22, and a move that silently leaves the images behind while the notes go is
a worse answer than either alternative the entry offered.

- `src/basic_memory/indexing/non_note_move_runner.py` is the second arm: move the file, restamp the
  entity row's `file_path`, `checksum`, and `updated_at`, refresh its single search row. It never
  touches note_content, because there is none.
- **No vocabulary check on that arm, deliberately.** The funnel judges frontmatter; a binary has
  none. This is not a hole in T22's funnel — it is the funnel's subject not existing.
- The permalink is left alone. A binary has no frontmatter to derive one from, and rewriting it
  would orphan every relation bound to it for no gain.
- The router's loop branches on `runtime_content_type_is_markdown(entity)` and now carries a
  `_PlannedDirectoryMove` dataclass rather than a positional tuple, because the branch made the
  third element ambiguous to read.

`move_note` on a single non-markdown entity still returns 415, unchanged. That is right: there is
no note content to accept, and the caller named one file rather than a directory.

**Regression test:** `tests/mcp/test_tool_move_note_search_rows.py`
`test_directory_move_carries_a_non_markdown_file` puts an indexed PNG beside a note, moves the
directory over the real MCP path, and asserts both moved — file on disk at the new path, entity row
repointed, permalink unchanged.

---

**Opened 2026-08-16** during review of the T22 diff. `move_directory` selects the batch with
`EntityRepository.find_by_directory_prefix`, which filters on path and nothing else:

```
$ sed -n '640,646p' src/basic_memory/repository/entity_repository.py
        pattern = f"{directory_prefix}/%"

        query = self.select().where(Entity.file_path.like(pattern))

        # Skip eager loading - we only need basic entity fields for directory trees
        result = await self.execute_query(session, query, use_query_options=False)
        return list(result.scalars().all())
```

Every row it returns is now handed to `note_content_mutation_service.move_note`, which refuses a
non-markdown entity with 415:

```
$ sed -n '957,961p' src/basic_memory/indexing/accepted_note_mutation_runner.py
    if not runtime_content_type_is_markdown(entity):
        reject_accepted_note_mutation(
            AcceptedNoteMutationRejectKind.unsupported_media_type,
            "Only markdown note mutations are supported by the note-content path.",
        )
```

So a directory holding an indexed PDF or image reports that entity as a failed move and leaves it
behind, where `EntityService.move_entity` moved any file. **Positive control** that the old path
was content-type-blind: it took `identifier` and `destination_path` and never consulted
`content_type` — its only file work was `file_service.move_file`.

This hits governed and ungoverned projects alike; it has nothing to do with the vocabulary. It is
the cost of routing the batch through the note-content path, and it was not a stated decision.

**The work:** decide whether a directory move should carry non-markdown entities at all. If it
should, the batch needs a second arm for them — a path-only move that does not enter the
note-content runner. If it should not, say so in the endpoint's docstring and in `move_note`'s
tool description, so the refusal reads as a rule rather than a bug.

---

### T27 — an accepted create, update, or edit never builds observation or relation search rows — **WITHDRAWN 2026-08-16, same day: the premise is false**
**Withdrawn 2026-08-16**, in the second review pass of the same diff that opened it. Kept rather
than deleted because the reasoning that produced it is the reasoning that produced T25, and both
need the same correction on the record.

**The claim was that a note written through `write_note` has no observation search rows until some
later file pass.** It does have them, within the same HTTP request. The accept path materializes
the file and then indexes it synchronously:

```
$ sed -n '384,388p' src/basic_memory/index/note_content_materialization.py
        if file_path is not None and self.file_indexer is not None:
            await self.file_indexer.index_file(
                file_path,
                source="note-content-materialization",
            )
```

`file_indexer` is never `None` on this runtime — `get_note_content_materialization_provider`
(`deps/services.py:461-475`) takes `file_indexer: IndexFileExecutorV2ExternalDep` as a required
parameter and passes it straight through, so every route write has one. The chain
from there rebuilds all three row kinds:

`LocalMarkdownFileIndexer.index_markdown_file` → `index_current_markdown_file(index_search=True)`
→ `BatchIndexer.index_markdown_file(index_search=True)` → `_refresh_search_index`
(`indexing/batch_indexer.py:576`) → `SearchService.index_entity_data` → `index_entity_markdown`,
whose own docstring is *"Index an entity and all its observations and relations."*

**Positive control, and it is the decisive one** — an existing test that has been passing all
along, which could not pass if the claim held:

```
$ sed -n '29,32p' test-int/mcp/test_observation_permalink_collision_integration.py
async def test_duplicate_category_content_observations_both_searchable(
    mcp_server, app, test_project
):
    """Both observations must be indexed even when their synthetic permalinks collide."""
```

It calls `write_note` and then `search_notes` with `entity_types: ["observation"]`, and asserts
both observations come back. No reindex anywhere in it.

**What the original entry got wrong, and how.** It reasoned from the accepted-write transaction
alone — where `refresh_entity` really does delete every row and insert one — and never followed the
router past `move_note` into `materialize_write_change`. The `search_index` DELETE is real; the
conclusion drawn from it is not, because a second, synchronous pass reinserts everything a few
milliseconds later in the same request. **The lesson is the house rule verbatim: a claim without a
reproduction is not a finding.** T25 and T27 were both filed off a code read, and the grep pasted
into T27 as its "reproduction" only proved where a new function is called — it never showed a note
missing an observation row.

**What is still true, and is the only part worth carrying forward:** if materialization returns any
status other than `written`, `index_file` is skipped, and then the accepted write's own search rows
are all the note has — the entity row and nothing else. That window is uncovered on every accepted
path, move included, because the runner-side rebuild that briefly covered it on moves was reverted
with T25. It is a question about that failure window, not about the missing rows this entry
claimed, and it wants a reproduction before anyone builds for it.

### T28 — a permalink rewrite on move leaves `entity_metadata` stale, so `bm doctor` reports drift — **CLOSED 2026-08-16: both move paths write the key**
**Opened 2026-08-16**, found reviewing T23's diff. Not introduced by T23 — T23's skip is what keeps
a *governed* project clear of it. Every move path that rewrites the permalink is affected.

`find_permalink_integrity_issues` (`src/basic_memory/repository/entity_repository.py:65-113`) calls
it **drift** when `Entity.permalink` disagrees with `json_extract(entity_metadata, '$.permalink')`.
That is the T9 identity check `bm doctor --project` prints. Both move paths update the column and
leave the JSON alone:

- **Scan/watcher.** `_build_move_batch_update_values`
  (`src/basic_memory/indexing/project_index_maintenance.py:466-536`) assembles `entity_values` as
  `file_path`, `checksum`, `permalink`. No `entity_metadata`.
- **Accepted (`move_note`).** `prepare_accepted_note_move`
  (`src/basic_memory/indexing/accepted_note_write_runner.py:381`) sets `entity.permalink =
  result.permalink` and nothing writes `entity.entity_metadata`.

`entity_metadata` is the file's whole frontmatter, permalink included
(`src/basic_memory/markdown/utils.py:62-64`), so an indexed markdown note always has the key. And
nothing repairs it later: the move batch stamps the row with the *planned* content's checksum, so
the rewritten file never presents as modified to a later scan.

**Reproduction, by reading, over the existing test.**
`tests/index/test_local_project_index.py::test_local_project_index_move_updates_permalink_when_configured`
ends with `Entity.permalink == "<project>/archive/renamed-note"` while the row's
`entity_metadata["permalink"]` is still `"<project>/notes/rename-me"` — the drift predicate,
satisfied. **Positive control** for the predicate itself: the governed T23 test moves the same way
and leaves both equal, which is why it produces no issue. A runnable check is one assertion added
to each of those two tests; it was not added here because a reviewer does not run the suite.

**Why it matters:** the check exists to catch a hand-edited `permalink:` after first index. If a
routine move manufactures the same signal, the report is noise on every corpus that has ever been
tidied, and the real hand-edit hides in it. This is the O8 class inverted: a true-looking finding
that is an artifact.

**Fix:** carry `permalink` into the row's `entity_metadata` wherever the move writes the column —
one JSON key in `_build_move_batch_update_values` and one assignment beside line 381 — or teach the
predicate that the frontmatter copy is not authoritative. **Writing the key is the smaller change
and the right one:** `entity_metadata` claims to mirror the file, the file *was* rewritten, so a
stale copy is simply wrong regardless of what reads it.

**CLOSED 2026-08-16 — both move paths now write the key, and doctor's integrity section is honest.**
Fixed inside W5 item 5, as the item's plan recommended: the section is not worth printing while a
routine move manufactures its main finding.

- **Scan/watcher.** `_build_move_batch_update_values` adds an `entity_metadata` CASE beside the
  `permalink` one, `json_set(entity_metadata, '$.permalink', <planned>)`, keyed on the same entity
  ids. It is built only when content repair was planned, so a path-only move still touches nothing.
  `json_set` on a NULL mirror stays NULL: a row with no frontmatter copy does not acquire one.
- **Accepted (`move_note`).** `prepare_accepted_note_move` rebinds `entity.entity_metadata` with the
  new permalink. **Judgment call:** the branch is keyed on the permalink *actually changing*, not on
  the caller's `should_update_permalink`. The preparer declines the rewrite when permalinks are
  disabled, and stamping the mirror there would silently repair a real hand-edit — the drift this
  check exists to report.
- **Tests.** A scan move and an MCP `move_note` each assert `find_permalink_integrity_issues`
  returns no drift afterwards, plus the assertion the entry asked for on the existing
  `..._move_updates_permalink_when_configured` test. Both carry positive controls: the scan test
  asserts the check is clean *before* the move, and the accepted test hand-edits the mirror through
  raw SQL and asserts drift is still reported.

**Re-verified 2026-08-17, no further change.** The entry was handed to a later pass as still open,
because only the *heading* had gone unmarked — the close block above was already accurate. The fix
is in the tree at `project_index_maintenance.py:526-537` (the `json_set` CASE) and
`accepted_note_write_runner.py:465-475` (the mirror rebind), and the two tests are
`tests/mcp/test_tool_move_note_permalink_metadata.py` and
`tests/index/test_local_project_index.py:1398-1434`. **0 `def test_` added, 0 removed.** The lesson
is bookkeeping, not code: a close block without a marked heading reads as an open gap, and the next
pass pays for the re-read.

### T29 — an advisory raised by an agent write is logged and then lost forever — **CLOSED 2026-08-17: the accepted create/update/edit sites persist what they accept**

**Opened 2026-08-16** while wiring W5 item 3. W5's mechanism A now persists a violation from the
two paths that *record*: the sync/index path and the move planner. The agent write path does not
record — it **rejects** — and rejection only fires on an error. An advisory is not an error, so the
write is accepted and its violation is written nowhere.

The only advisory the checker emits today is `unknown-key` (`vocabulary/checker.py`, step 12), and
it is the one W4 deliberately made non-blocking: *"flagged, never rejected"*. W5 item 5 then plans a
`bm doctor` hygiene section reading *"every `severity="advisory"` violation"*. On this tree that
section is empty for every key an agent ever wrote.

Two facts, both read from the tree at `4cd26479`:

```
$ git grep -n "entity_service" -- src/basic_memory/indexing/accepted_note_mutation_runner.py src/basic_memory/indexing/accepted_note_write_runner.py
(exit 1)
```

The accepted write path never reaches `EntityService`, so item 3's three persist sites are not on
it. **Positive control** for that grep — the same file does reach the funnel, four times:

```
$ git grep -n "enforce_accepted_note_vocabulary" -- src/
src/basic_memory/indexing/accepted_note_mutation_runner.py:359:def enforce_accepted_note_vocabulary(
src/basic_memory/indexing/accepted_note_mutation_runner.py:551:    enforce_accepted_note_vocabulary(
src/basic_memory/indexing/accepted_note_mutation_runner.py:728:    enforce_accepted_note_vocabulary(
src/basic_memory/indexing/accepted_note_mutation_runner.py:802:    enforce_accepted_note_vocabulary(
src/basic_memory/indexing/accepted_note_mutation_runner.py:907:    enforce_accepted_note_vocabulary(
```

And `enforce_accepted_note_vocabulary` is typed `-> None` (`:359`): it discards the list, so no
caller could persist even if it wanted to. Nor does a later pass recover the row — the accepted
write stamps the entity with its own checksum, so the file never presents as modified.

Reproduction as a test: a `write_note` on a governed project carrying an undeclared frontmatter key
succeeds, logs one DEBUG advisory, and leaves `violation` empty. Not added to the suite here,
because a test that asserts the hole would have to be deleted by the fix.

**Fix:** make `enforce_accepted_note_vocabulary` return its violations, and have the accepted
create/update/edit sites persist them through `ViolationRepository.replace_for_entity` after
`create_accepted_pending_entity` returns the id — the same shape item 3 gave `EntityService`. A
rejection still persists nothing; only an accepted write with advisories has anything to store.
Scoped out of item 3 deliberately: item 3's brief names the two record-mode callers, and reject mode
was explicitly "persists nothing". That is right about *rejections* and wrong about *advisories*,
which is why this is filed rather than folded in.

**Blocks:** W5 item 5's hygiene section, which is otherwise honest only about hand-edited files.

**Close block, 2026-08-17.** `enforce_accepted_note_vocabulary` now returns `list[Violation]`, and
the three accepted write sites persist what it returns through
`persist_accepted_note_violations` → `ViolationRepository.replace_for_entity`, on the mutation's
own session. The create site persists after `create_accepted_pending_entity` returns, because the
rows are keyed by the entity id and that call is what produces it; update and edit persist right
after the check, where the entity already exists. A rejection still persists nothing: the funnel
raises before any of that, and the whole mutation rolls back with it.

Judgment calls:

- **The move site returns its advisories and does not store them.** A move parses no relations, so
  its answer is partial, and a full replace would erase the relation-derived rows a real write
  recorded. `index/local_moves.py` already handles that case with `preserve_rules`, and a DB-first
  move rewrites at most the permalink, which is an error rather than an advisory.
- **The replace runs unconditionally, including on an ungoverned project**, where it clears rather
  than records. One DELETE per accepted write is cheap, and re-reading `vocabulary.yml` to learn
  there is nothing to delete would not be. The sync path skips instead, because it pays that read
  per file across a whole corpus. Same shape as `index/local_moves.py`, for the same reason.
- **An empty answer clears.** The rows are derived state: a record that now checks clean must stop
  being reported, which is the same rule W5 item 3 gave `EntityService`.
- **The repository is injected, not constructed inline.** `AcceptedNoteWriteRepositories` gained
  `violation_repository(project_id)`, alongside the five accessors it already had, and
  `AcceptedNoteRepositories` implements it. The first attempt built a `ViolationRepository` inside
  the runner, which broke 13 unit tests: that runner is driven by fakes passing a stub session, and
  a repository built inside issued real SQL against it. Every other write here already arrived
  through the bundle; this one was the exception, and the exception was the bug.

**The local runtime has a second writer, and it hides this fix from an end-to-end test.** Every
accepted write materializes and then indexes its own file: `materialize_write_change` awaits
`file_indexer.index_file`, which reaches `EntityService.upsert_entity_from_markdown` and so
`_persist_vocabulary_violations` — W5 item 3's record-mode persistence — for the same entity,
milliseconds later. Both writers compute the same set from the same frontmatter and both *replace*,
so nothing is duplicated or lost. But it means a test that writes through the whole stack and then
reads the table passes with this fix reverted. T29 is still worth fixing: the accepted runner is
DB-first and owes its own answer, and a runtime whose index pass is deferred (or absent) has no
second writer at all.

Tests: `tests/index/test_local_write_stack.py` covers the advisory landing as a row, the positive
control that an error still rejects and stores nothing, a later clean write clearing the earlier
rows, and an edit persisting what the edited note still carries. The control that isolates *this*
fix from the index pass is `test_the_accepted_write_alone_persists_the_advisory`, which calls the
mutation service and stops: no file, therefore no index pass, therefore only one writer.
`tests/mcp/test_tool_vocabulary_enforcement.py::test_an_accepted_advisory_persists_a_violation`
asserts the agent-visible half — after `write_note` returns, the row is there. The pre-existing
`test_a_refused_write_persists_no_violation` is unchanged and still passes — a rejection was never
the part that was wrong. At the unit level,
`tests/indexing/test_accepted_note_mutation_runner.py::test_run_accepted_note_create_clears_violations_on_an_ungoverned_project`
pins the injected repository and the unconditional clear.

One test changed shape rather than being fixed: a PUT **merges** the note's existing frontmatter
(`services/note_preparation.py`, `prepare_update_entity_content`), so a key left out of a
replacement stays on the note and its advisory is right to survive. The "a later clean write
clears" case now fixes the record by declaring the field in `vocabulary.yml`, which is the way a
record actually stops breaking that rule.

### T30 — every native command pays the MCP client graph through `run_with_cleanup`'s module — **SHIPPED 2026-08-17**

**Opened 2026-08-16** while moving `bm doctor` onto the fast path (W5 item 5). The plan's premise
for that move was that `cli/commands/doctor.py` importing `basic_memory.mcp.async_client` and
`basic_memory.mcp.clients` at module level made doctor pay "the whole MCP graph". Pushing those
imports inside the self-test is right and the guard now covers `doctor` — but the saving is smaller
than the plan assumed, because **`cli/commands/command_utils.py` imports the same modules at module
level**, and every native verb imports it for `run_with_cleanup`:

```
$ git grep -n "^from basic_memory.mcp" -- src/basic_memory/cli/commands/command_utils.py
src/basic_memory/cli/commands/command_utils.py:10:from basic_memory.mcp.async_client import get_client
src/basic_memory/cli/commands/command_utils.py:11:from basic_memory.mcp.clients import ProjectClient
src/basic_memory/cli/commands/command_utils.py:12:from basic_memory.mcp.project_context import get_active_project
```

Measured on this tree (`.venv`, warm page cache):

```
$ python -c "import time; t=time.perf_counter(); import basic_memory.cli.commands.command_utils; print('%.3fs' % (time.perf_counter()-t))"
0.306s
$ python -c "import time; t=time.perf_counter(); import basic_memory.mcp.async_client, basic_memory.mcp.clients, basic_memory.mcp.project_context; print('%.3fs' % (time.perf_counter()-t))"
0.248s
```

**Positive control** that the modules really are loaded by the CLI helper, rather than the timing
being coincidence: after `import basic_memory.cli.commands.command_utils`, `sys.modules` holds
`basic_memory.mcp.async_client`, `basic_memory.mcp.clients` and each of its six client modules, and
`httpx`.

So ~0.25 s of the ~0.95 s warm floor (`AGENTS.md`, "Measured baseline") is an MCP client graph that
`project list`, `types`, `mine` and now `doctor` never call. **This 0.25 s is wrong — see the
correction in the close block below; the real marginal cost is ~0.04 s.** The import guard does not
catch it: `mcp.async_client` is not `mcp.tools`, and the banned list names the seconds-scale
offenders.

**Fix:** split `run_with_cleanup` (and `NewerSchemaError` handling) into a module that imports
nothing from `basic_memory.mcp`, and leave the client-routed helpers where they are. Then add
`basic_memory.mcp.async_client` to `BANNED_MODULES`, which is the part that keeps it fixed.

Not fixed in W5 item 5: `command_utils` is shared by every verb including the client-routed ones,
so the split is its own change with its own blast radius, and item 5 already moved a command.

**SHIPPED 2026-08-17.** `run_with_cleanup` and the `NewerSchemaError` catch moved to
`src/basic_memory/cli/runner.py`, which imports nothing from `basic_memory.mcp` or
`basic_memory.api`. `command_utils` keeps only the client-routed helpers. Thirteen verb modules
and `test-int/cli/test_project_commands_integration.py` were repointed;
`tests/cli/test_command_utils.py` became `tests/cli/test_runner.py`.

**The split alone was not the fix, and the fix diagnosis above was incomplete.** `cli/main.py`
imports every command module on every invocation, and `project.py`, `orphans.py` and `status.py`
each imported `basic_memory.mcp.async_client` / `mcp.clients` at module level. Those three now
defer the import into the client-routed function that uses it. `bm project info` reaches its
helper as `command_utils.get_project_info`, so the module attribute a test can patch still exists
(`tests/cli/test_project_info_errors.py`).

`basic_memory.mcp.async_client` and `basic_memory.mcp.clients` are in the guard's
`BANNED_MODULES`, and `ls`, `show` and `path` joined `NATIVE_COMMANDS` — the guard now covers all
eight native verbs, cold and warm, and its positive control is parametrized over both ban families.

**Correction: this entry's headline saving was wrong, and the corrected number is ~0.04 s, not
~0.25 s.** Measured on this tree after the fix (`.venv` python, warm page cache, three reps):

```
$ python -c "import time; t=time.perf_counter(); import basic_memory.cli.main; print('%.3fs' % (time.perf_counter()-t))"
0.212s / 0.209s / 0.211s

# the same process, plus the three mcp modules — the old startup set
$ python -c "import time; t=time.perf_counter(); import basic_memory.cli.main, basic_memory.mcp.async_client, basic_memory.mcp.clients, basic_memory.mcp.project_context; print('%.3fs' % (time.perf_counter()-t))"
0.261s / 0.245s / 0.268s

# marginal cost of the mcp graph AFTER cli.main is already imported
$ python -c "import basic_memory.cli.main, time; t=time.perf_counter(); import basic_memory.mcp.async_client, basic_memory.mcp.clients, basic_memory.mcp.project_context; print('%.3fs' % (time.perf_counter()-t))"
0.038s / 0.039s / 0.039s
```

Re-run in review on a loaded host: 0.048 / 0.046 / 0.065 s marginal. Wall clock moves with load,
the conclusion does not — the graph costs tens of milliseconds, not a quarter second.

The 0.248 s above was a fresh-interpreter measurement of the mcp modules alone. It counted
`rich`, `typer`, `pydantic` and `loguru`, which the CLI pays for anyway — so most of it was never
a saving. Same class of error as T18's inherited table: a figure measured in isolation, then
quoted as a cost of the thing that shares its dependencies.

**Positive control** for the structural claim: `sys.modules` after `import basic_memory.cli.main`
now holds none of `basic_memory.mcp.async_client`, `basic_memory.mcp.clients`,
`basic_memory.api.app`, `fastapi`, `httpx`, `dateparser`. Before the change it held the first two
and `httpx`.

**What still justifies the change** is structural, not the stopwatch: eight new verbs would each
have inherited a client graph they never call, and the ban line is what keeps that from drifting
back. The latency claim is retired.

### T31 — `command_utils.run_project_index` has no callers and never had any on this tree — **CLOSED 2026-08-17: deleted**

**Found 2026-08-17** while reviewing T30. `command_utils` is now documented as "helpers for the CLI
verbs that route through the in-process API client", and one of its two helpers is reached by
nobody. Reproduction, on this tree and on `HEAD` before T30:

```
$ git grep -n 'run_project_index\b' -- src tests test-int
src/basic_memory/cli/commands/command_utils.py:22:async def run_project_index(
$ git grep -n 'run_project_index\b' HEAD -- src/basic_memory/cli
HEAD:src/basic_memory/cli/commands/command_utils.py:58:async def run_project_index(
```

Positive control: the sibling helper matches its call site in the same grep —
`get_project_info` returns `cli/commands/project.py:328`. So the empty result is the query working,
not the query being wrong. `run_project_index` is not an entry point and not a plugin, so the
import-grep caveat (T19) does not apply.

**Not deleted here.** A review pass fixing a stated defect is the wrong place to remove a public
helper, and `bm reindex` is the obvious future caller. Delete it, or wire `bm reindex` to it,
whichever the next pass over that module decides.

**Close block, 2026-08-17 — deleted.** `run_project_index` is gone from
`src/basic_memory/cli/commands/command_utils.py`, along with the two imports it was the only user
of (`typing.Optional` and `project_marker.resolve_cli_project`). `get_project_info` is now the
module's only helper and keeps every remaining import.

- **No tests removed:** 0 `def test_` added, 0 removed. The reproduction grep already showed the
  definition was the single hit across `src tests test-int`, so nothing exercised it.
- **Judgment call — deleted rather than wired to `bm reindex`.** `bm reindex` does not exist, and
  this fork does not keep code for a caller that has not been written (`AGENTS.md`: *"Don't spend
  tokens on code we will never run"*). When `bm reindex` lands it will be a native verb, which
  cannot import `command_utils` at all — that module pulls the MCP client graph (T30). So the
  helper was not a head start on it; it was the wrong shape for it.
- `local_schedulers._run_project_index` is a **different, live** method. The leading underscore is
  the whole difference and the T31 grep matched both; it was not touched.

---

## BLOCKERS / gaps in capability

### T32 — a `.bm.yml` in the checkout broke 73 CLI tests — **FIXED 2026-08-17**

**Found 2026-08-17**, the first time `bm` was used from this repo for real. `bm project add
basic-memory` plus a `.bm.yml` (`project: basic-memory`) at the repo root — the intended daily
setup — turned 42 unit tests and 31 integration tests red on the next full run, all with one error:

```
Error: Project marker <repo>/.bm.yml names 'basic-memory', which is not a registered project (see 'bm project list')
```

Every `bm tool …` test invokes the CLI in-process with the developer's cwd, and marker resolution
walks up from `Path.cwd()`. The test config never registers that project, so the marker refuses.
The suite was hermetic about `HOME`, `BASIC_MEMORY_HOME` and the DB and not about cwd — the one
input the fork's own verbs added.

**Fix:** `tests/cli/conftest.py::isolated_home` and `test-int/conftest.py::config_home` now
`monkeypatch.chdir(tmp_path)`. Tests that need a marker write one under `tmp_path` and chdir there
themselves, as they already did. 0 tests added or removed; 73 went from red to green.

**Positive control:** with the marker present and the fixture change reverted, the 73 fail; with
the marker deleted, they pass either way.

### B1 — no `contains` operator in metadata filters; multi-value is AND-only — **RESOLVED 2026-07-31: `$contains` and `$in` exist (since `43d1a3a4`); verified live**
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

**Resolved 2026-07-31, no new code** — the same `43d1a3a4` batch that fixed T1. Verified live on
a scratch corpus (note with `supersedes: [tnd-aaaa1111]`):

```
{"supersedes":{"$contains":"tnd-aaaa1111"}}                  -> ['succ']    # element-wise
{"supersedes":{"$in":["tnd-aaaa1111","tnd-zzzz9999"]}}       -> ['succ']    # OR semantics
{"supersedes":{"contains": ...}}  (the old bare spelling)     -> "Unsupported operator 'contains'
    ... Supported operators: $in, $contains, $gt, $gte, $lt, $lte, $between"
```

The bare-list form keeps AND/contains-all semantics deliberately (`$contains`/`$in` state the
other intents explicitly). The error's exit-0-prose shape is O8/W7's business, not B1's.

### B2 — project registry is split between the database and `config.json` — **SHIPPED 2026-08-03: the DB owns the registry**
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

**Amended + partially SHIPPED 2026-07-31.** Three of the operational bites are gone:

- *Unnamed output*: fixed with T18 — `project list` renders Name/Path/Default and warns when a
  config project is missing from the index.
- *Fresh config unusable*: bit this session twice — most CLI commands skip
  `ensure_initialization` for startup latency (`cli/app.py` skip list), so on a brand-new config
  `bm status` / `bm tool` / project resolution all failed with "no projects are set up" until
  `bm reindex` happened to run the sync. Shipped `reconcile_projects_if_registry_empty`
  (`services/initialization.py`): when the DB registry is empty and config declares projects,
  sync on first touch — wired into the prepared-ASGI seam (`mcp/async_client.py`) and the native
  direct path (`cli/direct.py`). Empty-registry only: a populated registry is never touched, so
  real drift stays the explicit init paths' business. Verified live: first-ever command on a
  fresh config (`bm status`, `bm project list`) now works.
- What REMAINS of B2: the registry is still structurally split (adds/removes must keep two
  stores agreeing), which is the design question R8's id-keyed layout has to answer — revisit
  when `.bm.yml` (W4/B5) lands.

**2026-08-03 — the deferral trigger has fired.** `.bm.yml` landed with B5 (`eb8df433`), so "revisit
when `.bm.yml` lands" is now due, not future. `config_models.py:161` still declares
`projects: Dict[str, ProjectEntry]` keyed by human name, so the split is exactly as recorded.
**This is a user decision, not an agent one** — whether to move to the id-keyed registry layout now
or keep the split until the verbs force it. Nothing is decided here.

**DECIDED 2026-08-03 (user): the database becomes the sole owner of the registry — now, not with
W4.** `config.json` keeps operational settings only; the project list and the default flag live in
the DB alone. Adds/removes/default changes write the DB only. A legacy `config.json` that still
carries a `projects` key gets a one-time import into an empty DB registry (the
`reconcile_projects_if_registry_empty` seam is already the right shape), after which the key is
ignored and never written back. The sync code and the two-sources-of-truth drift risk go away with
the split.

**SHIPPED 2026-08-03.** `BasicMemoryConfig` no longer declares `projects` or `default_project`;
`ConfigManager` no longer has `projects`/`default_project`/`add_project`/`remove_project`/
`set_default_project`/`get_project`; `ProjectService.synchronize_projects()`, the
`POST /v2/projects/config/sync` route, and every config-write half of a mutation path are deleted.
`reconcile_projects_with_config` and `reconcile_projects_if_registry_empty` collapsed into
`ensure_project_registry()`, which fills an **empty** registry — from a legacy `config.json`
`projects` key when one exists, otherwise with one bootstrap project at `BASIC_MEMORY_HOME` — and
never touches a populated one. A legacy key is tolerated on load (`extra="ignore"`) and, since the
field no longer exists on the model, is never written back.

Two judgment calls worth recording:

- **A synchronous registry reader exists** (`basic_memory.project_registry`), reading the SQLite
  file with the stdlib driver. The `.bm.yml` marker chain, `bm brief`, `bm schema`, and the
  importers resolve projects from synchronous Typer code with no event loop; routing them through
  SQLAlchemy would force `async` through ~20 call sites *and* put the 1.1 s import on the CLI
  floor. Writes never go through it.
- **`bm db reset` now snapshots and restores the registry** around the file delete. The registry
  used to survive a reset because it lived in `config.json`; reset is an index rebuild, not a
  de-registration.

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

### B4 — the fast path exists; `bm tool *` still pays the full MCP import graph, and the ~1.1 s direct floor stands — **CLOSED 2026-08-10: alembic made lazy; ~0.95 s accepted as the permanent floor**

**Decision (user, via 2026-08-10 handoff).** The floor is accepted, not fought further:

- **The alembic fix shipped — and the 2026-08-07 spec was incomplete.** Moving the two imports
  inside `run_migrations` saved nothing on its own: `get_or_create_db` calls `run_migrations`
  unconditionally on every process's first engine, so alembic still loaded on **every** LOCAL-mode
  run, warm or cold (verified: two-run probe against one config dir, `ALEMBIC_LOADED: True` both
  times). What shipped is the lazy import **plus a head-stamp skip**: `_single_alembic_head()`
  parses `alembic/versions/*.py` with a regex (no alembic import; parity-tested against alembic's
  own `ScriptDirectory.get_heads()`), and `get_or_create_db` skips `run_migrations` when the DB's
  `alembic_version` stamp equals the sole head. Every doubtful case — no table, several heads,
  unparseable file — falls through to the real migration run, which is always safe. The import
  guard now bans `alembic` on a **warm** run only; a fresh DB legitimately migrates. Measured
  after: warm `project list` runs with `ALEMBIC_LOADED: False`, user CPU 0.90–1.16 s, RSS ~90 MB
  (from ~115 MB).
- **~0.95 s user CPU is the accepted per-command floor.** The remainder is SQLAlchemy plus the
  app's own import graph — the real cost of a DB-backed command. The design has already routed
  around it where latency matters (W8's hook carries no `bm` data; W9's statusline reads a headline
  file), and those routes were judged right on their own merits, not as workarounds. If a *third*
  latency-driven route-around ever appears, reopen this: that would be evidence the floor, not the
  callers, is the problem.
- **`bm tool *` staying on the full MCP import graph is by design** and needs no further tracking.
- Nothing else remains for B4 to track: new verbs are bound to the direct path by the structural
  rule in `AGENTS.md` and enforced by the import guard, so "verbs land on the direct path" is a
  property of each verb's build, not an open item here.

*Entry as it stood before closing:*
**Found:** fork-point baseline. **Retitled 2026-08-03** — the old heading, "no fast path: anything
touching `mcp.tools` / `api.app` costs ~4 s", has been false since `7d3459da` shipped
`basic_memory.cli.direct` (T18). The entry's own 2026-07-31 amendment said so and the heading did
not; it is promoted here.

**What is true now.** `basic_memory.cli.direct` gives a native command a repository/service route
with none of `api.app` / `mcp.tools` / `fastapi` / `dateparser` on it, guarded by
`tests/cli/test_native_command_import_guard.py`, which runs `project list` in a subprocess and fails
if any of those four enters `sys.modules`. `project list` measures 1.1 s user / 115 MB.

**What remains open, and it is the whole of B4 now:**

- `bm tool *` one-shots still pay the full MCP import graph — by design, not by oversight.
- The **~1.1 s direct-path floor** (SQLAlchemy + pydantic + alembic) plus the 0.15 s interpreter
  floor bound every fast verb we build. That floor, not the 4 s one, is the number the verb designs
  have to live with.

Close B4 when the verbs land on the direct path and the 1.1 s floor is either accepted explicitly or
reduced.

**Measured 2026-08-07 — about 15% of the floor is a migration library loaded on every read.**

```
$ for m in sqlalchemy pydantic alembic; do /usr/bin/time -f "%e" uv run python -c "import $m"; done
sqlalchemy  0.24 s
pydantic    0.07 s
alembic     0.41 s      # includes sqlalchemy; marginal cost over it ≈ 0.17 s
$ /usr/bin/time -f "%e" uv run python -c "pass"        # interpreter baseline
0.04 s
```

And `project list` — a pure read on the direct path — loads it:

```
elapsed=0.94s exit=0
  alembic: True     sqlalchemy: True     pydantic: True
  fastapi: False    dateparser: False
```

**Cause:** `src/basic_memory/db.py:10-11` imports `alembic.command` and `alembic.config.Config` at
module level, and `db.py` is on the import path of everything that touches the database. Alembic is
needed only by `run_migrations` (`db.py:401`).

**Fix, unbuilt:** move those two imports inside `run_migrations`. A lazy import of a
migration-only dependency is not speculative — it is the same structural boundary
`tests/cli/test_native_command_import_guard.py` already enforces for `api.app` / `mcp.tools` /
`fastapi` / `dateparser`, and **alembic should join that guard's forbidden list for read commands**
so it cannot creep back.

Expected saving ≈ 0.17 s of a ~1.1 s floor. That does not change the architecture — a fast verb
still costs ~0.95 s, and the statusline still cannot call `bm` (W9 routes around it with a headline
file). It is worth doing because it is small, guarded, and removes a dependency that has no business
on a read path — not because it makes any deferred design newly possible.

**Not done in the same session it was found:** the change needs `just fast-check` +
`just test-unit-sqlite` + `just test-int-sqlite`, and a coverage run held the test machinery.
Concurrent runs corrupt results here.

**The open half of B4 is unchanged and is a judgment call, not a measurement:** whether ~0.95 s is
accepted as the permanent floor for every verb. Two facts bear on it — the interpreter alone is
0.04 s, so the remainder is SQLAlchemy plus the app's own import graph; and the design has already
routed around the floor twice rather than lower it (W8's hook carries no `bm` data, W9's statusline
reads a file). A third workaround would be evidence that the floor, not the callers, is the problem.

*Original entry:*
`bm tool search-notes` is 4.3–4.8 s; a native
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

### B5 — no cwd → project resolution: no walk-up, no marker-file detection — **SHIPPED 2026-07-31: `.bm.yml` walk-up wired into the CLI layer**
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

**SHIPPED 2026-07-31** (T9 landed; decisions delegated by the user). The three blockers dissolved
smaller than feared because `bm brief` had already prototyped the chain in-tree:

- **Marker schema (what B5 needs of it):** `.bm.yml` at a working-directory root (a pointer, not a
  container — note content lives only in the store; see W3's 2026-08-03 decision) with one read key —
  `project: <registered name>`. All other keys are ignored, so the store design remains free to
  add its id (`bm history`/`bm undo`) without churn here. No new identity was invented — that was
  blocker 1's trap, and carrying the name instead of an opaque id removes blocker 2 (no id→name
  lookup layer needed; the name resolves through the existing `ConfigManager.get_project`).
- **Layering:** cwd is a *CLI-boundary* concept. `src/basic_memory/project_marker.py` owns
  `find_marker` (walk-up, nearest wins), `read_marker_project` (strict), and
  `resolve_cli_project` (explicit `--project` > marker > config default, marker names validated
  against the registry so a stale marker fails with its own path in the message). The MCP server,
  API, and `ProjectResolver` never see cwd — resolution happens once at CLI entry and flows down
  as an explicit project name. That kills blocker 3: no construction-site wiring in the index/
  storage layers at all.
- **Strict by default, forgiving where documented:** a malformed marker or unregistered name is a
  `MarkerError` (ValueError → exit 1 at every wired site, per `docs/OUTPUT_CONTRACT.md` —
  addressing failure). `bm brief` keeps its own forgiving wrapper because a broken marker must
  not fail a session start (its constraint 3); that divergence is deliberate and local to brief.
- **Wired sites:** `bm tool *` (all 10 project-taking commands), `bm schema *`, `bm status`,
  `bm orphans`, `run_project_index`, and `bm brief` (already had it). `bm doctor --project` and
  the `bm project *` management commands stay explicit-name-only on purpose — registry
  manipulation should never be implicitly retargeted by cwd.
  **Stale for reads since 2026-08-16 — see W5-C for the live statement.** `bm brief` and
  `bm status` now resolve reads through `cli/scope.resolve_read_scope`, which ends at "every
  project" rather than the default one, and brief's forgiving marker wrapper is gone. This
  bullet remains accurate for the **write** chain (`resolve_cli_project`).
- **Known hazard, accepted:** a `.bm.yml` anywhere above a working directory changes the default
  project for commands run below it. That is the feature; the validation error names the marker
  path so surprises are diagnosable. Nothing writes markers yet — humans create them; the store
  verbs will automate it (W3/W4).

---

## WANT — capabilities to build in

These are the `tend` features, built as `bm` subcommands rather than a wrapper (see `AGENTS.md`,
"What this fork is for"). Listed here so the gap list is the single place to look.

### W1 — `bm mine`: decision mining over Claude Code transcripts — **SHIPPED 2026-08-16**
Recovers decisions made in conversation and never written down. **Measured 2026-07-26: no index is
needed** — plain `rg` over the 4 pilot slugs (106 MB / 77 sessions) is ~20 ms, worst case 0.47 s,
and `rg --json` plus a full parse is 0.039 s. An index would add a staleness problem for nothing.

> **The figures in this entry come from a private local transcript corpus and are not reproducible
> from this repo.** The pilot was one machine's Claude Code project directories; nothing here ships
> them and no fixture recreates them. Treat the numbers as the record of a measurement that was
> taken, not as a check anyone can re-run — the *design constraints* they produced are the durable
> part and stand on their own.

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

**DECIDED 2026-08-06 (user) — `bm mine` is a parser, not a miner; it runs on demand.**

**The split.** Two jobs hide in this entry and they have opposite natures. *Turn classification*
("who actually said this; is this a tool result") is structural, deterministic, and is the thing
agents reliably get wrong — the four search-path constraints above plus the `role: user` trap.
*Decision detection* ("is this passage a decision") is judgment. Same line that closed W6: **code
where agents fail predictably, agent where judgment is needed.**

So `bm mine` emits clean, correctly-attributed turns in the shape this entry already specifies
(`{session, line, timestamp, speaker, text, context[]}`) and **makes no claim that anything is a
decision.** An agent reads that output, judges, and writes any keeper with `bm new` — so the
vocabulary enforcement, `source:`, and the git history all apply through the normal write path with
no special case.

**This dissolves the open question of what `mine` writes.** It writes nothing. There is no
"auto-write records / print candidates / file as `inbox`" choice, and no class of records that
assert a decision no person ever confirmed.

**Sub-agent transcripts: out by default, `--include-subagents` to add them.** A parser whose one job
is correct speaker attribution must not silently mix in turns that are not the user's. Note that
**cass indexes them and does not mark them** — both hits in the 2026-08-06 probe below came from
`subagents/agent-*.jsonl`, distinguishable only by path.

**On demand, never background.** Three grounds:

1. **A session-end trigger does not fire when it matters.** W3 rejected `Stop`/`SessionEnd` hooks
   for the git history because a session that ends badly (crash, `/clear`, context blowout) never
   fires them — and sessions worth mining skew toward exactly those. Same argument, same answer.
2. **A parser produces nothing to act on unprompted.** The judgment step is the expensive one, and
   backgrounding it spends model tokens on every session forever.
3. **There is nothing to pre-compute.** Search was measured at ~20 ms, worst case 0.47 s.

**Accepted exposure, with the mitigation named:** nothing gets mined unless asked, so a decision made
in conversation and never mined stays lost. The fix is **not** a background job — it is making the
ask cheap and habitual (user agreed 2026-08-06). Two additive candidates, neither built yet: a line
in the W8 primer, and a `bm doctor` hygiene check that notices sessions in this project that were
never mined.

**Probed 2026-08-06 — `cass` does not solve this and is not the engine.** The user asked whether the
`cass` transcript tool ("unified TUI search over coding agent histories", `/recall`) classifies tool
results better. It does not. A `cass search --json` hit carries `title`, `snippet`, `content`,
`score`, `source_path`, `agent`, `workspace`, `created_at`, `line_number`, `match_type`,
`source_id`, `origin_kind` — and nothing else. Greps for `role`, `speaker`, `turn*`, `message_type`,
`is_tool*` return **zero hits** across `cass search --json`, `cass introspect --json` (1,122,030
bytes), and `cass robot-docs` (29 lines). *Positive control:* the same greps for `"agent"` and
`search` in those files return 10 and 5 hits, so the pattern and the pipeline both work.

What cass does offer: tool **calls** normalized to a readable `[Tool: Read - <path>]` form (a tool
call was never the trap — tool *results* are), and coverage of Codex/OpenCode transcripts that
`rg` over `~/.claude/projects` does not see. What it costs: an index, hence a staleness problem this
entry deliberately avoided, plus an external dependency for a shipped feature. **Treat it as a
possible later source, never the classifier.**

**SHIPPED 2026-08-16.** `bm mine "<term>"` reads this directory's Claude Code transcripts and
prints correctly attributed turns. It judges nothing — the split decided 2026-08-06 holds exactly.

What landed: `src/basic_memory/mine/` (`locate` finds transcripts, `turns` classifies them,
`search` matches with a context window) and `src/basic_memory/cli/commands/mine.py`. The verb
touches no database, so it is in `skip_init_commands` and is the cheapest verb in the tree; the
import guard covers it cold and warm.

**The survey behind the classifier.** The build recorded 1,909 `.jsonl` files, 85,092 non-empty
lines and 0 parse failures. **The review re-ran it on the same tree and got different numbers**
(2026-08-16): 1,919 files, **464,253** non-empty lines, and **12 parse failures**. The file count
matches, so the build's line count counted something narrower than every line of every transcript;
the figures below are the reproducible ones, and the failures are now GAPS **O10**.

| `type: user` line shape | count |
|---|---|
| carrying a `tool_result` | 85,434 |
| harness injection (`meta`) | 5,839 |
| genuine human text | 5,417 |

So **94% of `role: user` lines were written by something other than the person**, 88% of them tool
output — the trap this entry opened on, now measured over a whole corpus rather than a 47-hit
sample.

**Two findings the entry did not have, both load-bearing:**

1. **Tool results are not the only impostor wearing the user's role.** A second class is harness
   injection: `isMeta: true` (412), and text opening `<command-name>` (238), `<system-reminder>`
   (186), `<local-command-stdout>` (101), `[Request interrupted` (43), `<command-message>` (11),
   `<bash-input>`/`<bash-stdout>` (2/2). These classify as `meta`, never `human`. A classifier that
   only special-cased `tool_result` would still have attributed a slash command to the person.
   **A third impostor the build missed, added 2026-08-16 by the review:** multi-agent traffic.
   `<teammate-message>` (1,121) and `<task-notification>` (328) were classified `human` — 1,449 of
   the 6,864 human turns, 21% of the default output. Both are now in `INJECTED_PREFIXES`, with a
   test.
2. **`sessionId` is the wrong session identity for a sub-agent transcript.** All 1,106 sub-agent
   files carry their *parent's* `sessionId`, while all 808 main files have
   `sessionId == filename stem` (re-verified 2026-08-16 over the whole tree; the build sampled
   205/195). A `date-ref` built from the field points `#L<line>` at a file that
   does not contain that line — a citation that resolves to the wrong place is worse than one that
   fails. `bm mine` uses the file stem, and a test carries it.

**The four search-path requirements, discharged.** (1) The `*.jsonl` filter is a positive allowlist
inside `locate.py`, reachable by no flag, and hidden files never qualify whatever their extension —
a test reads a hidden `.context-window-*.json` sidecar on purpose and shows it *would* have
classified as human speech, which is why a blocklist was never an option. (2)/(3) do not arise:
the reader is pure Python, so there is no `--max-columns` to avoid and no `path:lineno:content`
string to split. (4) Every parse failure is counted, named with its file and line, and exits the run
1 — never silent. **The premise under (4) does not hold at tree scale**: 12 lines in 464,728 fail,
so the first shape of this — aborting the read — left three project directories unmineable rather
than degraded. That was **O10**, closed the same day: the payload now prints, the damaged lines are
named on stderr, the exit stays 1, and a torn line gives up only the record that was actually torn.
`docs/OUTPUT_CONTRACT.md` went to 2.1 to carry the one case where a payload and a non-zero exit
coexist.

**One classifier defect found by the review and fixed in the same pass:** a thinking block is
classified by the block, not by the text it yields. Real thinking blocks carry an **empty**
`thinking` string — the reasoning is redacted and only the signature is written (46,915 of 46,917).
The first cut branched on extracted text, so it fell through and labelled 46,898 reasoning lines
`tool_use` with empty text; a reasoning turn shown under `--context` claimed to be a tool call.
The judgment call below — "Reasoning is `thinking`, not `assistant`" — was right and the code did
not implement it. A tool call now outranks a thinking block on the rare line carrying both, because
the call has text a caller can search.

**Judgment calls:**

- **No `--json`.** The brief for this build asked for one; `docs/OUTPUT_CONTRACT.md` v2 forbids it
  and W20 records that keeping `--json` as a secondary mode was proposed and rejected by the user.
  Shipping one on a new verb would re-open a closed decision, so `bm mine` has a single rendering:
  ref first, then time, speaker, text; a `N turns` count line; notices and affordances after.
- **Speakers are finer than `human|assistant|tool_result|attachment`.** The parser also assigns
  `thinking`, `tool_use`, `meta`, and `other`. `--speaker` still takes only `human` (default),
  `assistant`, or `all`, because that is the question a caller asks — but the speaker column always
  prints the true class, so `all` never flattens a tool result into "assistant".
- **The default filter announces what it hid.** `2 turns` followed by `12 more turns matched other
  speakers — 2 assistant, 2 attachment, 4 meta, 1 thinking, 2 tool_result, 1 tool_use.` A caller
  who does not know how much of a transcript is machinery would otherwise read a small count as
  "nothing there".
- **Reasoning is `thinking`, not `assistant`.** A quote lifted from a thinking block must never be
  presented as something the assistant said out loud.
- **Matching is against extracted text, never the raw JSON line**, so a search for `content` or
  `role` cannot return every line in the corpus.
- **A damaged corpus stays mineable** (added 2026-08-16 with O10). The payload prints, every
  unreadable line is named on stderr, and the run exits 1. `#L<line>` addresses the physical line,
  so two turns share a ref on the rare line holding two records — renumbering would make the
  reference disagree with an editor, which is worse.

**Scoped out, deliberately:** no regex or `--ignore-case` flag (matching is always
case-insensitive substring); no date range; no `rg` backend; no `bm doctor` check for
never-mined sessions — that mitigation is still the additive candidate this entry names, and it
belongs with W5's checks rather than here. Nothing about the "make the ask cheap and habitual"
exposure changed: it is still accepted, and still unmitigated.

### W2 — the gardener — **DECIDED 2026-08-05 (user): no `bm gc` command; the jobs are checks inside `bm doctor`**
Strictly lossless — may move, index, dedupe, re-label, and flag; may never summarize, merge, or
resolve. Ship the flag-only version first so the constraint is structural rather than aspirational.

**Reduced in scope 2026-07-26:** it no longer needs to maintain a derived reverse index for
supersession. `build-context` on a predecessor returns incoming edges natively, so the reverse is
derived at read time by the store itself.

**DECIDED 2026-08-05 (user) — `bm gc` does not ship as a command.** Its five jobs
(`.forked/schema.md` §6) become checks inside `bm doctor`, alongside W5's schema rules.

The grounds are the repo's own no-`bm check` rule, applied one step further than it was written.
Flag-only was already the constraint, so the gardener and the doctor do the identical thing: run a
query, print a list, change nothing. The test case that settles it: an agent gets W5's nag, runs
`bm doctor`, is told about broken links and missing fields, and is told **nothing** about eleven
findings that expired last month — because those live in a command nobody mentioned. That is
precisely *"a second checking command would immediately be the one nobody runs."*

**The integrity/hygiene distinction is real and survives as grouping, not as a second binary.**
Integrity checks (DB ↔ file consistency, dangling links, permalink invariants, missing required
fields) have right answers. Hygiene checks (expired `review-by`, `date-source: inferred`, stale
`state`, the `inbox` pile, W4's proposed types) need a person. Doctor groups its output by category
and takes `--only <category>`; the nag's count spans both, since an agent should not have to know
which kind of problem it has before asking.

**Consequences:**

- The fifth job — *"content that is now re-derivable"* — has no query and needed human judgment even
  in the original design. It does not become a doctor check. It is not built.
- **`AGENTS.md` names `bm gc` twice** — in the planned-features list and in the flat-verb list. Both
  must be corrected when this lands, or the file advertises a command that does not exist.
- Doctor's output grows enough that grouping is now required rather than cosmetic.

**CLOSED 2026-08-16 — the gardener's jobs are `bm doctor --only hygiene`.** Shipped as W5 item 5.

- Four of the five jobs are queries in `EntityRepository` over `json_extract(entity_metadata, …)`:
  expired `review-by`, `date-source: inferred`, `state` records untouched for over 30 days, and the
  `inbox` pile with the type each record proposes. They print under a `hygiene` heading, one record
  per line, alongside every `advisory` violation.
- **The fifth job — "content that is now re-derivable" — is not built**, as this entry said it
  would not be. It has no query and needed human judgment even in the original design.
- **Staleness is 30 days, fixed in code and printed on every stale row.** `vocabulary.yml` declares
  no staleness key (`types`, `statuses`, `areas`, `review_months`, `fields`), so there was nothing
  to read it from. The number sits on the row rather than in a footnote, because a reader who sees
  one line out of context still has to know what "stale" means here.
- **`proposed-type` is now a schema key, legal on `inbox` alone.** Nothing writes it yet — `bm new`
  will — so the check reads empty on today's corpus. That is the alternative this entry ruled out:
  omitting it would be doctor staying silent about the thing it was built to surface.
- **`AGENTS.md` no longer advertises `bm gc`.** Both lists were already correct by the time this
  landed; the one surviving mention is the sentence explaining why the command does *not* exist,
  which is the record, not an advertisement.
- **The test case this entry turned on now runs.** W5-B's notice shipped 2026-08-16 with W5 item
  6b: an agent that never asks is told `N records past review-by — run 'bm doctor --only hygiene'`
  on any project-touching command. The eleven findings that expired last month are no longer in a
  command nobody mentioned.

### W3 — local git history on the write path — **SHIPPED 2026-08-10 (mechanism + verbs; W5's write path wires into it)**
Every mutation commits into a local-only store repo so pruning is recoverable. Two traps: set
`core.excludesFile` **and** `core.hooksPath` to `/dev/null` inside that repo (a globally configured
secret-scanning pre-commit hook will otherwise block automated commits), and never export `GIT_DIR`.

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

**Decided 2026-08-03 (user) — the store is the only home for note content, so W3 has no mirror to
maintain.** The question this closes: does `store/<id>/` *hold* the notes, or does it mirror content
that lives at some arbitrary project root? It holds them. `.bm.yml` is a pointer that maps a working
directory to a project; it never has note content beside it. See `AGENTS.md` for the full statement
and its consequences (project paths become store-derived, a path argument to `bm project add`
becomes an import source, existing projects migrate under W6).

Three things follow for the build:

1. **The commit is a plain `add -A` in one repo.** No copy-on-write, no divergence check, no
   reconcile step. This is what makes the 12 ms measurement above the real cost rather than a
   floor.
2. **The `git clean` hazard is gone by construction**, not by discipline — nothing of value sits
   inside another repo's worktree. The `info/exclude` requirement stands anyway, for speed.
3. **W3 lands before W6.** The importer is the largest destructive operation this fork will run,
   and it should run *into* a repo that already commits every write, so the first entry in the
   history is the import itself, fully revertable.

Found in: sweep-localhist.md:1, :7, :13, :19, :31, :37, sweep-handoffs.md:13, sweep-beans.md:1.

**DECIDED 2026-08-05 (user) — failure handling, and who records changes the tool did not make.**
Both questions are about the same defect: a **hole in the history**. A history with holes is worse
than none, because it is trusted and is silently incomplete.

**A — a failed commit blocks destructive writes only.**

| operation | git broken | grounds |
|---|---|---|
| create a new note | write it, warn | nothing is lost — the note is on disk; a missing history entry costs nothing |
| delete or overwrite | **refuse**, explain | the prior content is the thing the history exists to protect; without the commit it is gone |

This keeps agents working for the common case and blocks them only where the loss is real. Rejected:
refusing all writes (the tool stops working over something unrelated to note-taking, and agents will
route around it), and writing silently (a hole nobody ever learns about).

**Recover before failing.** Transient faults — chiefly `index.lock` — retry with a short backoff.
**Guard the lock removal:** deleting `index.lock` while another process genuinely holds it corrupts
the repo. Remove only as a last resort, only when the file is older than a few seconds and no live
process holds it. The risk is small here (local, single-user, `bm` is the only writer) but the guard
is cheap. Hard faults (full disk) are out of scope by the user's judgment: *"we have bigger issues
to talk about."*

**The error is an agent-actionable requirement, not a nicety.** It must name what failed, which
repo, and what to try. An agent can clear a stale lock if told; it cannot act on `git failed`.
Same standard as W19.

**B — the tool only ever commits changes it made.**

The write path commits **the paths it just touched**, never `add -A`. Anything else that is dirty is
**reported, never assumed**:

```
note: 2 other files have uncommitted changes (not included in this commit)
  ...
  run 'bm history dirty' to review
```

*Why not label those files.* An earlier draft swept them in as `Actor: human`. The user rejected the
premise: an uncommitted file is not necessarily a human edit — it can be a crashed agent write, a
half-finished import, or another tool. Labelling it is a guess recorded as fact, and the
`Actor:`/`Session:` trailers are what `undo --session` reads, so a wrong label makes undo do the
wrong thing. Two unrelated changes welded into one commit have the same effect: undoing the agent's
work also undoes the other change.

*`Actor:` records only what the tool knows* — `agent` for an agent-facing write path, `cli` for a
human-typed command, and **omitted** for a sweep of unexplained files. Silence beats a guess.

*The sweep is opt-in and is its own command*, not a flag on a write verb — a flag would weld the
sweep into an unrelated commit, the exact defect above. It also gives the nag a command to name,
which is what makes an agent act instead of move on:

```
bm history dirty              # what is uncommitted and not ours
bm history commit --all       # commit all of it, one commit, no Actor trailer
bm history commit <path>...   # commit specific paths
```

**C — the watcher is not the mechanism, on three counts.** `WatchService` /
`WatchCoordinator` exist (`src/basic_memory/index/`) but start **only with the MCP or API server**
(`src/basic_memory/mcp/server.py:101`); there is no `bm watch` and no daemon, so nothing watches
during CLI or fast-verb use. It is tied to the subsystem this fork is moving away from. And it
commits per file-change event, so a few minutes of editing yields a commit per editor autosave —
a record of typing, not of changes.

**Re-measure the commit cost.** The 12 ms / 6 ms / ~8 ms figures above were taken on `add -A`.
Staging an explicit path list is a different operation. The difference should be noise, but per the
evidence rules the number must be re-taken rather than inherited.

**Rescued 2026-08-07 from `.forked/local-history.md` before its deletion** — four measured facts
that had no home here.

**D — history does not grow without bound, and the only rule is not to disable `gc.auto`.**
Measured on the prototype store:

| state | size |
|---|---|
| loose objects before gc | 2,207 objects, **12 MB** |
| after `git gc` | **568 KB** on disk; pack **229 KiB** |
| the working copy it is a history of | 804 KB |

So: **never prune history on a schedule** — there is nothing to reclaim. Let git's own `gc.auto` run;
it triggers near 6,700 loose objects and performed the 12 MB → 568 KB compaction unattended. The one
way to get this wrong is to disable it.

**E — cross-machine sync is one branch per machine, never merged.** Verified: one private remote,
a branch per machine (`machine1`, `machine2`), both push cleanly, conflict-free by construction, and
either machine can read the other's tree (`git show origin/machine2:<path>`). The central store makes
this *more* attractive — one remote and one branch pair, not one per project.

> **STANDING RULE — the store repo must never have a public remote.** Recorded 2026-08-07 from
> `.forked/decisions.md` D2/D3, which is gitignored and was therefore the only home for a
> constraint that governs every note this fork will ever write. Sync targets a **private** remote
> only, from personal machines only; work and client machines never push. The unsanitized original
> names a specific host and stays out of this file.

**F — two write-path gotchas this entry previously stated only half of.**

- `--no-verify` is the **wrong** fix for the pre-commit-hook trap. It is per-invocation and easy to
  forget; `core.hooksPath=/dev/null` inside the store repo is the fix.
- The environment scrub covers **`GIT_WORK_TREE` as well as `GIT_DIR`**, and `--git-dir` is passed
  as a flag on each invocation. Reproduced: with `GIT_DIR` exported, `git log` run in a project root
  printed the *store's* history and `git rev-parse --git-dir` returned the store path.

**G — a JSONL journal was considered and rejected as the system of record.** A human editing a note
in `$EDITOR` produces no journal entry, so the journal would confidently describe a state that does
not exist. It survives only as a possible complement, and is unbuilt.

**Still open, inherited from `local-history.md` §9:** off-machine durability (does history die with
the directory?) — partly answered by **E**, not closed.

**SHIPPED 2026-08-10 — the mechanism and the two recorded verbs.** What exists now:

- `src/basic_memory/store/history.py` — DB-free, subprocess-git-only. `store_path()` derives from
  `resolve_data_dir()`; `ensure_store_repo()` is idempotent and self-healing (re-applies
  `core.excludesFile=/dev/null`, `core.hooksPath=/dev/null`, `commit.gpgsign=false`, and a local
  `bm-store` identity on every call); `commit_paths()` stages exactly the named paths, writes
  `Session:`/`Actor:` trailers (omitted when unknown), returns `dirty_others` for the nag;
  `sweep_commit()` never writes a trailer; every invocation scrubs
  `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE` and passes `-C`. index.lock: two retries with
  backoff, then guarded removal only past 5 s age. Errors are agent-actionable
  (`HistoryError` names the repo and the fix).
- `bm history dirty` / `bm history commit [PATHS|--all]` — v2-contract output, registered in the
  CLI's skip-init set (no DB touch; the verbs cost git alone).
- Judgment calls: the `/*` info/exclude pattern recorded above was **deliberately not applied** —
  it guarded the abandoned nested-in-worktree layout; in the central store the worktree *is* the
  store and the pattern would exclude every note. The bare-repo trap cannot arise (`git init` in
  the directory, not via `--git-dir`). `git status --porcelain` runs with `-uall` so an untracked
  directory reports file paths, not one collapsed row (undo needs file paths).
- **Owed re-measurement taken (2026-08-10):** path-scoped incremental commit through
  `commit_paths()` on a 200-file store: **~24 ms** avg over 50; no-change check **~10 ms**
  (includes `ensure_store_repo` config re-apply + the post-commit dirty scan). Same order as the
  12/6 ms `add -A` figures; synchronous commit inside every mutating call stands.
- Not wired to any write path yet, by design — the note-writing verbs arrive with W5 and call
  `commit_paths` with their actor/session; W6's import lands as the first big revertable commit.

Verified: fast-check 0; unit 3309/2 (3285 + 24 new: tests/store/test_history.py +
tests/cli/test_history_command.py); int 329/3 unchanged; doctor skipped — nothing here touches
the file↔DB loop. Live scratch smoke: `history dirty` empty case, per-file listing, path commit,
`--all` sweep with trailer-free message all conform.

**CLOSED 2026-08-17 — the write path is wired in.** The line above ("not wired to any write path
yet, by design") is now stale and this block supersedes it. `src/basic_memory/store/write_hook.py`
is what a write path calls; `index/local_write_stack.py` calls it from all three entry points, so
every native verb built on that stack records history without doing anything itself.

What it decides, and why each decision is there rather than in `history.py`:

- **Whether the project has a history at all.** A project whose path is not under `store_path()`
  is not in the repository's worktree, so nothing about it can be staged. It writes normally and
  gets one notice naming `bm project add` — **decision D3**, accepted by the orchestrator
  2026-08-17. The alternative, refusing the write, stops the tool over a migration the user has
  not been offered yet; the other alternative, silence, is the hole this entry's A/B blocks exist
  to prevent. `test_project` in the unit suite is exactly this shape, so both halves have a caller.
- **Which half of the A table a write falls under is decided by the content on disk, not by the
  verb's name.** `update_note` creates the note when it does not exist, and an edit whose file is
  gone is not an overwrite either. Both are labelled `create`, so the preflight lets them through
  and a later failure message does not claim content was lost that never existed. Found in review:
  the first cut refused an update-as-create on a broken repo, which is W3-A's warn-and-keep case
  wearing the wrong label.
- **What a failed commit costs**, per the W3-A table. A create warns and keeps the note; an
  overwrite refuses. **The refusal runs before the write, not after** — `check_can_record` is
  called ahead of the mutation, because a refusal issued after the file has been replaced protects
  nothing. That is the one shape correction contact with the code produced: the table reads as a
  post-commit decision and cannot be implemented as one.
- **What the caller says.** Nothing here prints. `dirty_others` and the D3 notice come back on
  `LocalNoteWriteResult.notices` for the verb to emit after its payload (contract rule 4). A
  service that writes to stdout cannot be composed, and W20 already settled where notices go.

Judgment calls:

- **The store-relative path is derived from the project's path, not from its `external_id`.**
  `AGENTS.md` says a directory name inside the store is "a human-browsing label that nothing
  reads", so the path on disk is the authority. For the standard layout the two agree.
- **The commit message is `<operation> <store-relative path>`** — no timestamp, no counter, no
  title. Byte-stable by construction, which is what this entry requires of the serialization for
  the same reason.
- **The headline file rides in the note's commit** (see W9). It lives inside the store's worktree,
  so a headline nobody commits would report as another actor's dirty file on every later write —
  W3-B's nag, firing forever over the tool's own output.
- `Actor:` is the write's `source`, which is `cli` for a native verb and whatever an agent-facing
  caller passes. `Session:` is `CLAUDE_SESSION_ID` when the environment sets it, omitted otherwise.

Not built here: **item C2** — `bm project add` creating `store/<external_id>/` as the project's
path — **landed 2026-08-17** with items E and F; see its build-log entry. Projects created before
it are still off-store and still take D3's notice branch on every write.

Tests: `tests/store/test_write_hook.py` (17) drives the module directly against a real repo;
`tests/index/test_local_write_stack.py` gains 9 that drive the real caller path — a store-rooted
project commits the note and its headline in one commit and leaves the store clean, an off-store
project writes and notices, a broken repo refuses an update with the file untouched and lets both
a create and an update-as-create through with a warning.

### W4 — closed record vocabulary enforced in the write path — **SHIPPED 2026-08-10; reject mode relocated 2026-08-16 (T22)**

> **Where enforcement lives, as of 2026-08-16.** One implementation in
> `services/vocabulary_enforcement.py`, three entry points. **Reject** is in
> `indexing/accepted_note_mutation_runner.py`, which is where every agent-facing write lands;
> **record** is in `EntityService` (the sync path) and in `index/local_moves.py` (the move
> planner, which judges a permalink rewrite a watcher move would otherwise make invisible). The
> text below still says `entity_service.py` is the agent write path — **it is not, and was not by
> the time W4 shipped**; that is the whole of **T22**, whose close block states what moved. The
> move planner is **T23**. Read both close blocks before building on this.

**Close block, 2026-08-10.** Shipped in four commits: `2e62e726` (`vocabulary.yml` + the bespoke
checker), `ee5bc1a4` (the shared glossary), `2310dc87` (the funnel), `63ad4fba` (`bm types`).
Verification: unit 3091 → 3220 (+129: 106 checker/loader, 12 funnel, 3 funnel guard, 7 `bm types`,
1 guard parametrization), int 284 unchanged, `just doctor` green.

**Two things this entry did not settle, both found on contact with the code and decided by the user
the same day** — the governance gate and the store id. Both are recorded above as DECIDED blocks
and in `.forked/schema.md` §3 (commit `ef6f5dbc`). Neither was visible from the design docs; each
appeared the moment the checker met a real write path.

**The funnel found a live side door beyond the one this entry names.** The decision block above
predicted MCP `edit_note` → `edit_entity_with_content`. The build also had to cover `move_entity`,
which rewrites `permalink` — a set-once field, and the strictest member of the list. That is the
argument for a funnel rather than hook points, made once more by the code itself.

**A deadlock that only a real write path could show.** The first funnel resolved the project's
`external_id` by opening its own session. Every call sits inside a mutator's open
`scoped_session`, and the pool holds one connection, so the nested acquire waited on a connection
the caller already owned and timed out after 30 s. The unit suite went from 80 s to over 600 s with
~50 failures across four directories. The fix is that `_enforce_vocabulary` takes the caller's
session and never opens one. Recorded because the shape recurs: **any per-write lookup added to a
service method must use the session already in hand.**

**A verification lesson, and it was the orchestrator's error.** One suite run was started while an
editing agent was still writing `entity_service.py` — the file's mtime was one second after the
run began. It reported 10 move-path failures that did not reproduce and were never real. `AGENTS.md`
already says verification runs centrally *after* every agent has reported; the gap was that
"reported" was inferred from the files existing rather than from the agent saying so. **Check that
the tree is stable before starting a suite, not just that the files are present.**

**Deliberately not built here** (W5's scope, unchanged): the violation table, its Alembic migration,
the revalidation trigger when `vocabulary.yml` changes, and `bm doctor` reporting. The checker's
`Violation` is already shaped as a row keyed by entity so W5 persists it without rework.
The write verbs `bm new` and `bm edit` remain in W5's phase as recorded.
Humans extend the vocabulary; agents may only select from it. Upstream's frontmatter vocabulary is
fully open, so enforcement is ours and cannot live in a wrapper.

**DECIDED 2026-08-10 (user) — `bm edit` ships, scoped to the kept-current types.** The schema's
enforcement sentence ("`tend` ships no verb that writes a set-once field after creation") only has
teeth while verbs are the normal write path; with three kept-current types, hand-editing store
files would become routine and set-once protection would degrade from write-time rejection to
doctor-flag-after-the-fact. And a `profile` edit can touch declared fields, which need write-path
validation — there is no write path for edits without a verb. The shape:

- `bm edit <id>` replaces title/body (stdin or `--file`), plus declared fields on a `profile`.
  Validates against the vocabulary; refuses any set-once field change.
- On a `task`: error pointing at `bm done` / `bm mark`. On a `finding`: error pointing at
  supersession — the error message is itself the enforcement of "findings are immutable."
- `inbox` is editable — it is the escape hatch; refusing edits there only pushes people back to
  hand-editing.
- Hand edits remain possible and are not an error; W3's history keeps them visible and `bm doctor`
  flags what they break. The verb makes the sanctioned path the easy one, not the only one.

Builds in W5's phase, where the write-path validation lands. Closes `.forked/schema.md` §11 Q6;
`bm edit` joins the flat verb list in `AGENTS.md` (this resolves the W19-example-vs-verb-list
discrepancy in favour of the example).

**DECIDED 2026-08-10 (user) — two names.** The vocabulary file is **`store/<id>/vocabulary.yml`**:
it says what the file is, and it does not echo the pointer's name — a `bm.yml`/`.bm.yml` pair would
recreate the exact pointer-vs-container confusion schema.md §3 exists to fix. The W19-item-4
explainer verb is **`bm types`**: it matches the question an agent is asking at the moment of
filing. `bm types` joins the flat verb list in `AGENTS.md`. Earlier text in this file that named
`.bm.yml` as the vocabulary source predates the 2026-08-04 pointer/vocabulary split and has been
patched in place to say `vocabulary.yml`.

**Decided 2026-07-31:** W4 does not build on `picoschema/`; that subsystem is stripped as the first
commit of this build (see O-picoschema for grounds). The vocabulary source is the store's
`vocabulary.yml` (this line originally said `.bm.yml`; patched per the 2026-08-10 naming decision),
validated by a bespoke checker that W5 wires into `bm doctor`.

**DECIDED 2026-08-04 (user) — the type set is six, closed, and named in plain English.**
`.forked/schema.md` §1 had three genre types plus `unsorted`. Testing it against four real cases
(relationship notes, coding notes, a forked repo's coding notes, a long-running enterprise
migration) found two of them had no home, and the schema's own axes explain why: types are keyed on
**temporal shape** (lifecycle / date / mutability / supersession), and two shapes were never
considered — every candidate §1 rejected was a flavour of *work*.

| type | lifecycle | world-time date | mutable | superseded | picks it |
|---|---|---|---|---|---|
| `task` | yes | `opened` | `status` | no | **do it** |
| `guide` | no | none | title + body | no | **consult it** |
| `finding` | no | `event-date` req | none | **yes** | **learned it** |
| `profile` | no | `since` (opt) | title + body + declared fields | no | **refer to it** |
| `state` | no | none | title + body | no | **how things are** |
| `inbox` | no | none | — | no | **can't tell** |

`review-by` is required and `vocabulary.yml`-defaulted on **both** `finding` and `guide` — instructions rot
faster than findings do, and it puts guides inside the gardener's expiry query for free.

**Renames from the draft, and why.** `entity` → `profile`: "entity" already means the DB-level
indexed representation of a file in this codebase, so reusing it collides in every conversation.
`fact` → `finding` (kept): `fact` implies settled truth, but this is the only supersedable type —
it is provisional by construction. `snapshot` → `state` (kept): "snapshot" implies a retained
series, and this type is overwritten or deleted with no history. `howto` → `guide`: `howto` excludes
explainers, which would push them into `finding` — the exact misfiling the split exists to prevent.

**`finding` vs `guide` is the split this repo already runs.** `AGENTS.md` is a guide — edited in
place, always current. `GAPS.md` entries are findings — dated, superseded rather than rewritten,
kept after they ship. `AGENTS.md` says so itself: *"Every rule below was bought with a wasted pass,
a wrong diagnosis, or a red suite."* Guide holds the instruction, finding holds the evidence. A
guide edit does **not** require a finding; the test is *"would you want to find this again without
going through the guide?"*

**Reversibility, which is why six and not five.** 6 → 5 later retypes only guides (the smaller
pile); 5 → 6 later means re-reading every finding to sort out which were guides. §2 makes a type
change *a new record*, so neither is free — start on the side that is cheaper to undo. Same shape as
§1's own ">~150 findings" escape hatch.

**Extension rules — fields and types get different answers, because the blast radius differs.**
An extra optional *field* does not fragment queries and is reversible by deleting a line. A new
*type* fragments every type-scoped query permanently and, per §2, cannot be undone per record
without rewriting each one with a new id — orphaning every edge bound to the old permalink.

- **Fields:** agents **may** add a declared optional field. Each is a name plus one of three kinds
  (`string`, `date`, `enum` + values). No required-if, no cross-field rules, no defaults beyond
  `review-by`'s. Declared extras are **optional-only** — a repo-required field makes notes
  unportable and fails every cross-repo import.
- **Types:** agents may **propose, never enable.** `bm new --type runbook` fails; the error says to
  file it `inbox` and records the proposed type on the record. `bm doctor` surfaces *"4 inbox
  records propose type `runbook`"*, and a human promotes it with one command. This reuses the
  existing escape hatch and beats the S7 return-visit problem, because the human is already in
  `doctor` when they see it.

**The vocabulary file moves into the store, and this is load-bearing.** `AGENTS.md` calls `.bm.yml`
*"a pointer, not a container"* at a **working directory** root — usually someone else's code repo.
`schema.md` §3 puts `types:`/`statuses:`/`areas:` in a file at the **store** root. Those are two
files under one name. They must split: the working-dir pointer keeps `.bm.yml`; the vocabulary
becomes a separate file under `store/<id>/` — `vocabulary.yml`, named 2026-08-10. If the
vocabulary lived at the working-dir root, W3's
history could not see it — and then agent field-extension has **no enforcement at all**, since the
only real check is that the change is a commit carrying an `Actor: agent` trailer.

**Enforcement lives in the service layer, not the CLI or the MCP tool.** `entity_service.py:368`
`create_entity_with_content` is the agent write path; `entity_service.py:674`
`upsert_entity_from_markdown` is the sync/watcher path a human's text editor reaches. Checking only
the CLI rebuilds beans' failure exactly (`.forked/decisions.md` R5: the CLI rejected
`maintenance-record` while GraphQL wrote it to disk, and the `types:` config block was silently
ignored).

**DECIDED 2026-08-10 (user) — enforcement is a single funnel, not per-hook-point.** The two
functions above are where GAPS first located enforcement, but `entity_service.py` has six more
mutators (`update_entity` :400, `update_entity_with_content` :406, `update_entity_and_observations`
:587, `update_entity_relations` :707, `edit_entity` :819, `edit_entity_with_content` :861), and
they are live agent paths — MCP `edit_note` routes through `edit_entity_with_content`. Hooking only
the named points leaves MCP edits unvalidated, which rebuilds beans' failure (above) with the roles
recast. So: **every entity mutator passes through one checker call**, and the *caller* declares the
mode — **reject** (verbs, MCP, API) or **record-violation** (the sync path, which never rejects).
A new mutator that skips the funnel is a bug, not a policy choice. Scope also confirmed same day:
this phase ships `vocabulary.yml` + checker + funnel + `bm types` + W19 items 2–3; the write verbs
(`bm new`, `bm edit`) stay in W5's phase as recorded.

**DECIDED 2026-08-10 (user) — the vocabulary file's presence is what governs a project.** Building
the funnel surfaced a collision the entry never addressed: `markdown/entity_parser.py:322` defaults
every note's frontmatter to `type: note`, and nothing generates a `tnd-` id yet, so a default
vocabulary applied everywhere would reject every existing write on the spot. The rule instead:

- **No `vocabulary.yml` → the checker never runs.** An absent file means "this project is not
  governed", not "use the defaults". The default block in `.forked/schema.md` §3 is what `bm new`
  *writes* into a new file, never what an absent file *means*.
- **A `vocabulary.yml` present → strict, with no passthrough.** `type: note` is an off-vocabulary
  type like any other and is rejected on the agent write path. There is no ungoverned seventh type;
  an escape hatch that every write already sets by default would close the vocabulary in name only.

Rejected: gating per record on a `tnd-` id (an agent writing `type: runbook` with no id would be
silently ungoverned, which voids *"agents may propose, never enable"*). Opting a project in is a
deliberate human act, which is the same standard §3 already sets for editing the file.

**DECIDED 2026-08-10 (user) — the store id is `Project.external_id`, and the vocabulary lands in
the store repo now.** W3 shipped `store_path()` as one repo root (`store/history.py:75`); the
per-project `store/<id>/` directories do not exist yet and arrive with W6's importer, while a
project's `path` is still user-chosen (`services/project_service.py:133-166`). So `<id>` had no
referent. It is now `Project.external_id` — the unique UUID4 already on the model
(`models/project.py:41-53`) and already printed by `project list` — and the file lives at
`store_path() / <external_id> / vocabulary.yml` from day one.

The deciding reason is the one this entry already gives for putting the vocabulary in the store at
all: W3's history must see every edit to it, because a commit carrying an `Actor: agent` trailer is
the only real check on agent field-extension. Placing the file at `<project.path>/vocabulary.yml`
until W6 migrates would leave it outside the history repo for the whole intervening period — the
enforcement gap the store placement exists to close. A short ULID column plus a migration was
rejected as W4-scope work for W6's benefit: `AGENTS.md` already says a directory name inside the
store is *"a human-browsing label that nothing reads"*, so a UUID reads worse and costs nothing.
W6 moves note content in beside the vocabulary file that is already there.

**The sync path always indexes and never rejects.** Refusing to index a hand-edited off-vocabulary
file makes it invisible to search *and* to `doctor` — on disk, unfindable, silent. Index it, record
the violation, let `doctor` report it. §4 already says a human hand-editing a file is not an error.

**Unknown frontmatter *keys* are allowed and flagged**, not rejected: frontmatter is BM's open
metadata surface and W18 now indexes it into FTS. Sprawl is a *type* and *value* problem.

**Acceptance condition, set by the user and binding:** six types ship only with (a) CLI help that
says when to use each, (b) a primer that explains the set, and (c) write-path errors that name the
allowed values in the same plain vocabulary. See **W19**. A closed vocabulary an agent cannot
understand at the moment of filing relocates the misfiling rather than preventing it.

~~**Owed before the build:** `.forked/schema.md` §1–§4 predate all of this and must be rewritten~~
— **discharged 2026-08-10**: schema.md's header now reads "§1–§4, §6–§7 rewritten 2026-08-10"; the
type table, per-type sections, the vocabulary example, and §4's mutability count ("four") are
current.

### Verbs phase — build log

Started 2026-08-17. The eight verbs (`bm new`, `edit`, `done`, `mark`, `ls`, `show`, `path`,
`undo`) plus the mechanisms they are the first callers of. One entry per item as it lands.

#### Verbs phase — CLOSED 2026-08-17

Every planned item shipped. What landed, in commit order:

| Item | What shipped | Commit |
|---|---|---|
| 0 | T30: native verbs stop importing the MCP client graph; `cli/runner.py` | `49de1a95` |
| B, G | record ids, `bm ls`, `bm show`, `bm path` | `ef609f22` |
| A | the local note write stack; T29 advisories persist | `4d004c87` |
| I | W8 items 1+2: `bm brief` derives its sections, gains `--query` | `e82fcdf8` |
| C, D | W3's write-path hookup and W9's headline file | `1aae1e7d` |
| H | `bm undo` restores the last `bm` commit as a new commit and reindexes | `6d2a19f7` |
| E, F, C2 | `bm new`, `bm edit`, `bm done`, `bm mark`; `project add` creates the store path | `7f756176` |
| J | affordances, notice wiring, the two guards, C3, brief F1, docs | this commit |

Closed along the way: **T29**, **T30**, **W3**'s hookup, **W8** (both items), **W9**, **W19 item
5**, **E1**, **C3**. Left open at the time and **closed 2026-08-17 in a follow-up pass**: **E2**,
**V-J1**, **V-J2** (all three below). **T28** was left open here too and is closed under its own
heading.

#### Decisions taken by the campaign orchestrator, for the user to confirm or reverse

`AGENTS.md`'s stop-list makes new-verb semantics the user's call. The plan brought back D1–D12 with
a recommendation each; the orchestrator accepted every recommendation so the phase could run, and
four further decisions were taken mid-build. **None of these needs to stand.** Each names where
the code is, so reversing one is a change, not an archaeology exercise.

| # | Decision as shipped |
|---|---|
| D1 | Record ids are `tnd-` + 8 chars from `[a-z0-9]`, drawn with `secrets.choice`, retried against the permalink column up to 5 times, then a loud failure. Never a counter (`vocabulary/ids.py`). |
| D2 | Files land at `<type-dir>/<id>--<slug>.md`, plural type directories, slug lowercased and cut to 60 chars. |
| D3 | Note files must be in the store for the history to see them: `bm project add` homes a new project at `store/<external_id>/`, and a project living elsewhere keeps working with one notice per write. |
| D4 | `bm undo` restores the newest `bm` commit's paths and records that as a **new** commit — never a reset. `--session <id>` walks every commit with that trailer, newest first, and more than one commit needs `--yes`. |
| D5 | `bm mark <id> <status>` sets `status`, on a `task`, and nothing else. `bm done` is `bm mark <id> done`. |
| D6 | The W9 headline file is `store/<external_id>/headline.md`, three lines, rewritten only when its bytes change. |
| D7 | `--source` is optional and defaults to the literal `cli`. |
| D8 | **Reversed mid-build; the breakage behind the reversal is fixed 2026-08-17.** `bm project add --governed` writes `DEFAULT_VOCABULARY`; plain `add` leaves the project ungoverned. Governing by default refused MCP's `write_note` (`type: note`) and broke 7 integration tests and `just doctor` — see item C2 above for the measurement. `DEFAULT_VOCABULARY` now declares `note`, so governing a project no longer breaks `write_note`; `--governed` stays opt-in on W4's own rule (an absent file means ungoverned, and declaring one is the human's act), and governed-by-default remains the user's decision to take. |
| D9 | `bm path` prints one absolute path: no count line, no notices, no affordances, no `--quiet`. Documented as the one exception in `docs/OUTPUT_CONTRACT.md`. |
| D10 | `bm show` prints the file's bytes verbatim; a supersession is a notice after the payload, dropped by `--quiet`. |
| D11 | Body input is `--body <text>`, `--body -` for stdin, or `$EDITOR` when a terminal is attached. No interactive prompts. |
| D12 | `bm edit` accepts `guide`, `profile`, `state`, `inbox`, and moves title, body and — on a `profile` only — the fields the project declares, with `--set name=value` (added 2026-08-17, V-J1). A `task` is pointed at `bm done`/`bm mark`, a `finding` at `bm new --supersedes`. |

Four more, taken while building:

- **The nested-path check is skipped for a store-derived path** (item C2). Two store-derived
  projects are siblings and cannot nest, and the rule otherwise refused every one of them whenever
  a user project sat above the data directory.
- **`bm mine` keeps the O10 partial-corpus shape**: it prints the turns it could read, names every
  unreadable line on stderr, and still exits 1 (`docs/OUTPUT_CONTRACT.md` rule 6).
- **A malformed `vocabulary.yml` degrades one project, not the brief** (item J, W8 F1 below). The
  raise still happens — W4 forbids reading a broken file as "ungoverned" — but it is caught per
  project, and `--verbose` names the file. The per-command notice degrades the same way as of
  V-J2 below (2026-08-17).
- **A write no longer names dirty files at the moment of its commit** (item C3 below, whose own
  recommendation this reverses). `emit_notices` is the single home for that condition, so the
  count a verb prints is scoped to the project it wrote to rather than to the whole store.

One decision was taken and then narrowed by the code: **`bm edit` did not edit a `profile`'s
declared fields**, which `.forked/schema.md` §11 Q6 said it would — title and body only. Filed as
V-J1 below and closed there 2026-08-17: `--set name=value` writes them, so the narrowing is gone.

**Item A — the local write stack. Landed 2026-08-17.**
`src/basic_memory/index/local_write_stack.py`:
`build_local_note_write_stack(config, session_maker) -> LocalNoteWriteStack`, with
`write_note` / `update_note` / `edit_note`. It re-wires what `deps/services.py:305-345,461-480`
wires — `AcceptedNoteRepositories`, `LocalAcceptedNotePreparerFactory`, the move policy from
config, `verify_storage_absent_on_create=True`, `LocalCurrentNoteContentFreshener`,
`ProjectRepository`, `LocalNoteContentMaterializationProvider` — without importing
`basic_memory.deps`, which is a FastAPI composition root.

Three things a verb author must not undo:

- **Both calls, in order.** The mutation runner writes rows and returns a *plan*; the materializer
  writes the file and indexes it. A caller that makes only the first leaves the T12 shape: a note
  in the database with nothing on disk. Every entry point here makes both, and
  `local_note_write_result` refuses a write whose `file_write_status` says the file never landed,
  rather than reporting success.
- **The followups are awaited, not scheduled.** The router schedules relation resolution and vector
  sync onto the event loop; a CLI process exits when the verb returns, so a scheduled task never
  runs. Relation resolution runs inline through `RepositoryRelationResolutionRuntime`; vector sync
  runs inline only when `semantic_search_enabled`, matching the router's condition.
- **Per-project pieces are built per call.** The file service and repositories need the project
  row's id and path, so nothing is composed until the project resolves. A verb writes one note per
  invocation, so there is nothing to amortize by caching.

`direct_note_writer()` lives in this module rather than `cli/direct.py` only because that file was
being edited concurrently; moving it is mechanical and belongs to item J.

Tests: `tests/index/test_local_write_stack.py`, 12 tests, no mocks — create/update/edit each land
on disk *and* answer a search; a forward reference resolves before the write returns; a vocabulary
rejection leaves no file and no row; an unknown project is a failure, not an empty result; and five
cover T29 below. The import guard runs in a subprocess, not in-process: pytest imports every test
module into one interpreter and several of them import fastapi, so an in-process `sys.modules`
assertion would report imports this module never made.

Also closed here: **T29** (advisories from an accepted write now persist).

**Items C and D — the history hookup and the headline. Landed 2026-08-17.**
`src/basic_memory/store/write_hook.py` and `src/basic_memory/services/headline.py`, called from
item A's three entry points. Both GAPS entries carry the full close blocks (**W3**, **W9**); what
belongs here is the shape a later verb author must not undo:

- **The refusal is a preflight, not a post-check.** `check_can_record` runs *before* an update or
  an edit reaches the mutation service. W3-A reads as a decision about a failed commit, but a
  commit fails after the file is already overwritten, and by then the prior content the refusal
  protects is gone. A create needs no preflight and gets none.
- **The headline is written first and committed with the note.** Both files sit in the store's
  worktree. Committing only the note leaves the headline uncommitted, and W3-B's dirty-files notice
  then fires on every subsequent write, about the tool's own output.
- **Notices are returned, never printed.** `LocalNoteWriteResult` carries `history_sha` and
  `notices`; the verb prints them after its payload (contract rule 4).
- **Decisions D3 and D6 are accepted** as VERBS_PLAN recommended them, recorded in the W3 and W9
  close blocks respectively.

Tests: `tests/store/test_write_hook.py` (17), `tests/services/test_headline.py` (12), and 9 added
to `tests/index/test_local_write_stack.py` (12 → 21) that drive the real caller path.

Three corrections came out of review, all before the verifier ran:

- **A failed headline write is a notice**, not an uncaught `OSError` — the note had already
  succeeded (W9's close block states the rule).
- **An update or edit with no prior content on disk is labelled `create`**, so the overwrite
  preflight lets it through (W3's close block).
- **The terminal-status set moved into `vocabulary/model.py`** and both askers call it. It had two
  homes that behaved differently, which is the split a later reader reports as a bug.

Left for J as filed: **item C3**, the two dirty-file notices that can print on one verb.

**Item C2 — `bm project add` creates the project under the store. Landed 2026-08-17.**
D3's other half. `bm project add <name>` now homes the project at `store/<external_id>/`, and the
path argument became optional — it means an *import source*, which is what `AGENTS.md` already said
it means. A project created without one takes D3's normal branch: its notes are in the history
repo's worktree and every write commits.

**The id has to exist before the path does**, because the path *is* the id. `add_project` draws the
UUID itself and passes it in `project_data` rather than letting the row's `external_id` default
fire, which would name the directory after an id the row had not chosen yet.

Three files beyond the two this item named had to move, and the reason is worth keeping: the
optional path could not reach the service at all. `ProjectInfoRequest.path` was `str = Field(...)`,
so `bm project add <name>` failed validation at the router. It is now `Optional[str] = None`, the
router's existing-project comparison treats an absent path as "no disagreement", and both other
callers (`bm doctor`'s round-trip probe, the MCP `create_project` tool) still pass a path.

**D8 lands here too, and its default was REVERSED the same day.** `bm project add --governed`
writes `DEFAULT_VOCABULARY` to `store/<external_id>/vocabulary.yml`; without the flag the project
is ungoverned and nothing is written. Asking for it is what makes it the deliberate human act W4
requires, and it is still the only place a project becomes governed — `bm new` never writes one.

D8 as first built governed **every** new project, and that could not ship. The default vocabulary
declares the six record types; MCP's `write_note` defaults to `type: note`; the checker refuses a
type a project does not declare. So governing by default refused **the primary agent write path**.
Measured by the verifier on the committed tree: 7 integration tests failed and `just doctor` failed,
all on one error —

```
Doctor failed: doctor/Doctor API Note.md is off this project's vocabulary:
Type 'note' is not in this project's vocabulary.
```

— and every existing MCP caller in the wild would have failed the same way on its next write.
Reversed by the orchestrator, flagged for the user. **An absent `vocabulary.yml` means ungoverned
(W4), and opt-in restores that meaning rather than overriding it**, which is the deeper reason the
reversal is right and not merely expedient: the first shape made "ungoverned" unreachable through
the only command that creates projects.

Reproduction of both sides, against a temp `BASIC_MEMORY_CONFIG_DIR`:

```
$ bm project add plain            # no flag
$ bm tool write-note --title "Plain Note" --folder notes --content "…" --project plain
action: created

$ bm project add checked --governed
$ bm tool write-note --title "Plain Note" --folder notes --content "…" --project checked
Error during write_note: notes/Plain Note.md is off this project's vocabulary:
Type 'note' is not in this project's vocabulary. …
```

**Store-derived paths are unaffected** — that is C2 proper and stays the default. Only the
vocabulary moved behind a flag.

**FOLLOW-UP 2026-08-17: `note` is now in `DEFAULT_VOCABULARY`, so the breakage above cannot
recur.** `DEFAULT_VOCABULARY.types` ends with `note` (`vocabulary/model.py`) — an ordinary open
type, no required fields beyond the common four, no status, no glossary entry. The second
reproduction above now reports `action: created`, and a governed project accepts every existing MCP
caller's next write.

Three things this does **not** change, stated because each is a decision rather than an oversight:

- **`--governed` stays opt-in.** The reason is now W4's own rule and nothing else: an absent
  `vocabulary.yml` means ungoverned, and declaring one is the human's act. Governed-by-default is
  the user's decision to take, not this pass's.
- **`note` is not a seventh record type.** It has no picking question in `vocabulary/glossary.py`
  and no summary, so `bm new`'s `--type` help never offers it and the rejection message never lists
  it. It exists so that governance does not break the write path an agent already uses.
- **Off-vocabulary types are still refused.** Six tests asserted that by writing `type: note` under
  a defaults-governed project; each now states an off-vocabulary type (`runbook`) instead, and
  `tests/mcp/test_tool_vocabulary_enforcement.py` gained the positive control for the fix — the
  default type is accepted under the default vocabulary.

**A second defect the reversal exposed: the nested-path rule refused every store-derived project.**
`add_project` rejects a path that shares a directory tree with an existing project, and
`store/<external_id>/` is inside the data directory — so any user project rooted above it (`~`, or a
test's tmp home) encloses it by construction. Two unit tests hit it immediately, and a user whose
default project is `~` would have hit it on the first `bm project add`. The check is now skipped for
a store-derived path: two store-derived projects are siblings and can never nest, so nothing is
lost, and an import source still gets the check — which is where tree-sharing actually happens.

**A gap this item created, found by the smoke run and fixed in the same pass.** The vocabulary file
lands inside the store's worktree, and nothing committed it, so W3-B's dirty-file notice fired on
**every** subsequent note write — about the tool's own output, forever. That is the failure mode
item D's headline file was shaped to avoid, reproduced one file over.
`write_default_vocabulary` now commits it, and follows W3-A's create rule when the repository is
unusable: warn, keep the project, do not fail the add. Reproduction, before the fix, against a
temp `BASIC_MEMORY_CONFIG_DIR`:

```
$ basic-memory new task "Move backups off-container" --body "…" -p smoke
tnd-qbptvkr6  task  …/store/<eid>/tasks/tnd-qbptvkr6--move-backups-off-container.md
1 record
note: 1 other file has uncommitted changes in the note store (not included in this commit)
```

After it, the store's first commit is `create <eid>/vocabulary.yml` and the notice is absent.

Tests: 5 added to `tests/services/test_project_service.py` (33 → 38) — the store-derived path (which
also covers the nesting exemption, the way a user hits it), ungoverned by default, `--governed`
writing the file, the import-source branch as its positive control, and a control that a governed
project really does refuse `type: note`. That last one is the test that would have caught the
reversal before the verifier did. 3 in `tests/cli/test_project_add.py`, including `--governed`
reaching the API. Two existing tests named `test_project_add_requires_a_path` — one in
`test_project_add.py`, one in `test_project_list_and_ls.py` — guarded the behaviour this item
deliberately reversed; the first became `test_project_add_takes_no_path`, the second became
`test_project_add_requires_a_name`, which is the part still true.

**Item E — `bm new`. Landed 2026-08-17.**
`src/basic_memory/cli/commands/new.py`, on a new shared module
`src/basic_memory/cli/record_notes.py`. `bm new <type> <title>` with `--body` (text, `-` for stdin,
or `$EDITOR` when there is a terminal), `--source`, `--area`, `--supersedes`, `--project`,
`--quiet`. Scope is the **write** chain, which still ends at the default project: a read covers
every project when nothing pins it, a write needs one home.

Two mechanics had to be discovered rather than assumed, and a later author will hit both:

- **`EntitySchema.file_path` is computed from `directory` + `safe_title`** (`schemas/base.py`), so
  nothing in the tree could put a note at `<type-dir>/<id>--<slug>.md`. `RecordNote` in
  `cli/record_notes.py` is a Pydantic subclass that redeclares the computed field and states the
  path. That is what makes `.forked/schema.md` §8 reachable, and it is also what lets `bm edit`
  keep a record's path when its title changes.
- **A declared `permalink` survives byte-for-byte only when it arrives in the note's own
  frontmatter text.** `_apply_schema_frontmatter_overrides` reads it there and `resolve_permalink`
  returns it unchanged when no other row claims it (`services/note_preparation.py`). Handed over as
  `entity_metadata` instead, it is dropped and a slugified, project-prefixed permalink is derived —
  and `permalink == id` (§2) quietly stops being true. So `bm new` renders its frontmatter as text.

What it writes: `id`, `permalink` equal to it, `type`, `title`, `source` (defaulting to `cli`, D7),
the type's one date field with `date-source: inline` and `date-confidence: day`, `area` when given,
`status: open` on a task, `proposed-type` on the escape hatch, and a `## Relations` line for
`--supersedes`. **`review-by` is not written here** — `prepare_accepted_note_create` stamps it, and
stamping twice would validate one value and store another.

Four judgment calls:

- **`inline` / `day` for a new record's date.** Not `inferred`: that rung means nobody stated the
  date, and `bm doctor` reports every inferred date for human review — stamping it here would put
  the whole corpus in that pile on day one.
- **The id loop lives in `cli/record_notes.py`.** `vocabulary/ids.py`'s `allocate_record_id` takes a
  *synchronous* predicate and the only honest collision check is a database lookup, so the verb
  loops over `new_record_id()` and raises that module's own `IdAllocationError`.
- **A type a human added to `vocabulary.yml` gets a directory under its bare name.** It is legal to
  write (W4), so refusing it a home would make the extension mechanism unusable, and pluralizing it
  would guess at English.
- **`$EDITOR` is opened by hand, not by `click.edit`.** `typer.edit` does not exist in typer
  0.26.8 (`hasattr(typer, "edit")` is False), which a typecheck caught before anything ran.
  `click.edit` does exist, but **click is not in this tree's `dependencies`** — it arrives only as
  typer's own transitive dependency, so calling it would add an undeclared one for a single call.
  `body_from_editor` in `cli/record_notes.py` is ten lines, both verbs share it, and it opens only
  when stdin is a terminal: an agent has none, and an editor launched there is a hang with no
  prompt to explain it (D11).

  **The branch is tested, and reaching it took a measurement.** A `CliRunner` stdin reports no
  terminal, so the editor path is unreachable from a test until `isatty` is patched — and the class
  to patch is **typer's** `_NamedTextIOWrapper`, not click's. Typer ships its own and installs that
  one; patching `click.testing._NamedTextIOWrapper` changes nothing and the test then silently
  re-tests the non-terminal path and passes. Measured: inside a typer `CliRunner`,
  `type(sys.stdin).__module__` is `typer.testing`. The tests point `$EDITOR` at a real shell script
  and run it as a real subprocess — one that overwrites (for `bm new`) and one that appends (for
  `bm edit`, where the original body surviving is what proves the editor was handed the record
  rather than a blank buffer).

`--supersedes` on a non-finding is refused **by the funnel**, not by the verb, which is the point:
the rule has one home. The verb checks only that the value is shaped like a record id, because a
target that cannot be a permalink lands as a dangling relation that reads as a real edge.

Tests: `tests/cli/test_new_command.py`, 19 tests, real path throughout — real command, real write
stack, real database, real files, real funnel. Two of the 19 close E1, below.

**Item F — `bm edit`, `bm mark`, `bm done`. Landed 2026-08-17.**
`src/basic_memory/cli/commands/record_write.py`. One module because the three share scope
resolution, the identity-verified lookup, the write stack and the print shape; what differs is one
rule each.

- **`bm edit` accepts `guide`, `profile`, `state`, `inbox`** (D12). On a `task` and on a `finding` it
  refuses with the *verb* that applies — `bm done <id>` / `bm mark <id> <status>`, and
  `bm new finding "…" --supersedes <id>`. Naming the rule instead ("a finding is immutable") sends
  an agent to file the correction somewhere else.
- **`bm mark` sets `status`, on a `task`, validated against the project's declared statuses** (D5).
  An ungoverned project declares none, so the write goes through unchecked and says so — absent
  means ungoverned, never "use the defaults".
- **`bm done` is `bm mark <id> done`**, through the same function, not a second path.

**A frontmatter-only change must not reflow the body**, and the tree already had the shape for it:
`prepare_edit_entity_content` treats `operation=append` with empty `content` plus a `metadata` block
as metadata-only and skips the content operation outright (`services/note_preparation.py`). `mark`
and `done` use exactly that, so a status change is a one-line diff in the history.

Two judgment calls:

- **An edited record keeps its file path even when its title changes.** The name carries the id
  other records link by; the slug beside it is a human label. Renaming would be a move, with a
  permalink rewrite behind it (§8, T28).
- **`bm edit` with neither `--title` nor `--body` and no terminal is an error**, not a no-op write.
  A rewrite with nothing stated produces a commit the caller cannot see.

**Fixed in review 2026-08-17: `--title` alone no longer opens `$EDITOR`.** `_next_body` consulted
the editor whenever `--body` was absent and stdin was a terminal, so `bm edit <id> --title "New"`
changed only the title under an agent and title *plus* whatever the editor returned under a human.
One command meaning two things depending on whether a terminal is attached is the shape the output
contract rules out elsewhere; the caller had already stated its change. `_next_body` now takes
`may_open_editor`, which `edit_record` sets from `title is None`. Regression test:
`test_edit_with_a_title_only_leaves_the_editor_shut`, whose positive control is the neighbouring
test that opens the editor with the same fixtures.

**Each command function calls `emit_notices` itself**, rather than a shared reporting helper doing
it. `tests/cli/test_notice_guard.py` looks at the command functions, and it looks there
deliberately — a guard over a helper proves nothing about whether the verbs reach it (T22). The
first draft routed all three through one `_report`, which the guard would have failed.

Tests: `tests/cli/test_record_write_commands.py`, 20 tests, records created by `bm new` rather than
seeded — so every assertion is about a record the tool itself wrote. One of them closes the loop
back to item D: closing the last open task removes the project's `headline.md`.
`tests/cli/test_notice_guard.py` gains the four new verbs in its expected set; that file belongs to
item J, but leaving it red would blind every pass after this one.

**Item C3 — two dirty-file notices will print on one verb. Found in review 2026-08-17; owed to J.**
A write now produces two lines about the same subject, from two places that count it differently:

- `store/write_hook.py`'s `dirty_notices`, from `CommitResult.dirty_others` — **store-wide**, since
  `commit_paths` reads one `git status` over the whole worktree.
- `cli/notices.py`'s `emit_notices`, from `dirty_count(prefix)` — **pinned to the project** when the
  scope is pinned, which is exactly what W5-C added the prefix for.

So `bm new` in a project with one dirty file of its own and three in a sibling prints "1 other file
has uncommitted changes in the note store" and "1 note file has uncommitted changes", or "4" and
"1". Neither line is wrong; together they read as a bug. Reproduction is the pair of counts above —
`tests/index/test_local_write_stack.py::test_another_dirty_store_file_surfaces_as_a_notice` already
builds the store-wide half.

Reviewed and left as-is here, deliberately: suppressing either line is a decision about which scope
a write reports, and J owns the notice block on every verb. The write hook's line now says "in the
note store" so the two are at least distinguishable while both stand. Recommended answer: keep the
write hook's line, which is tied to a specific commit, and let `emit_notices` drop `dirty` when the
payload already carried a history notice.

**Item E1 — `bm new --supersedes` writes an edge to a record that may not exist. Found in review
2026-08-17; CLOSED 2026-08-17.**
The verb checks only that the value is *shaped* like a record id (`is_record_id`,
`cli/commands/new.py`), and the funnel's rule 1 checks only the record's *type*
(`_check_supersedes`, `vocabulary/checker.py:395`). A well-formed id that names nothing therefore
writes `- supersedes [[tnd-aaaa1111]]`, exits 0, and says nothing. The edge lands as a dangling
relation, which `bm doctor`'s integrity section reports later — so the content is not lost, but the
author is told at the wrong moment. Reproduction, against a temp `BASIC_MEMORY_CONFIG_DIR`:

```
$ basic-memory new finding "A successor" -b x --supersedes tnd-aaaa1111 -p smoke
tnd-…  finding  …/findings/tnd-…--a-successor.md
1 record
$ grep supersedes …/findings/tnd-…--a-successor.md
- supersedes [[tnd-aaaa1111]]
```

**Closed 2026-08-17 as recommended.** `create_record` already holds a session and the project row,
so it looks the predecessor up through `record_exists` (a `permalink_exists` query — `permalink ==
id` byte-for-byte, so a permalink query *is* an id query and no title can match it) and refuses
before anything is written. A successor to a record that does not exist is a typo every time, and
the author is the only person who still remembers making it; telling them at `bm doctor` time is
telling the wrong person later.

The refusal is scoped to the project, which also closes a case the entry did not name: an id that
exists in *another* project. Records are project-scoped and a wikilink resolves within the project,
so a foreign id lands as a dangling relation exactly like a typo does.

Same command as the reproduction above, after the fix:

```
$ basic-memory new finding "A successor" -b x --supersedes tnd-aaaa1111 -p smoke
Error: --supersedes names 'tnd-aaaa1111', which is not a record in project 'smoke'
$ echo $?
1
```

The positive control is in the same test: the identical command with a real predecessor exits 0 and
writes `- supersedes [[tnd-…]]`, so the refusal is about the target's existence and nothing else.
Tests: 2 added to `tests/cli/test_new_command.py` (17 → 19) — the missing target with its control,
and the cross-project target.

**E2 was left open here and is CLOSED below**, on the recommendation this paragraph made: the verb
states it, the tree does not forbid the edit.

**Item E2 — the W4 escape hatch fails closed on a project whose vocabulary drops `inbox`. Found in
review 2026-08-17; CLOSED 2026-08-17.**
`resolve_note_type` (`cli/record_notes.py`) filed an undeclared type as `inbox`, but the checker
rejects a `type` that is not in `vocabulary.types` (`checker.py`). A human who removes `inbox` from
their `vocabulary.yml` therefore turned W4's "agents propose, never enable" hatch into a rejection
one layer down, about a type the author never asked for — the one case the hatch exists to prevent.
`DEFAULT_VOCABULARY` declares `inbox`, so it took an edit to that file to reach.

**Decided: `bm new` refuses, and says why.** Never a silent success, never a write. The vocabulary
is the human's to shape, so the tree does not forbid removing `inbox`; the verb states the
consequence at the moment someone hits it.

`resolve_note_type` now takes the project name and raises when the requested type is undeclared
*and* the vocabulary declares no `inbox`. The type is resolved **before** the id is drawn
(`cli/commands/new.py`), so a refused write spends nothing:

```
$ bm new runbook "Restart The Thing" -b steps -p no-inbox
Error: 'runbook' is not a type project 'no-inbox' declares, and its vocabulary declares no 'inbox'
type to file the proposal as — add 'inbox' to its vocabulary.yml or pick a declared type; run
'bm types' to see the set
$ echo $?
1
```

The positive control is in the same test: a declared type on the same project still writes, so the
refusal is about the missing `inbox` and not about the project being governed. The test also asserts
`rglob("*.md")` is empty — the refusal precedes the write rather than following it.

Files: `cli/record_notes.py` (the `INBOX_TYPE` constant and the raise), `cli/commands/new.py`
(resolution moved ahead of the id draw, module docstring), `README.md`.
Tests: 1 added to `tests/cli/test_new_command.py` (19 → 20); its `seed_project` helper gained a
`types=` parameter so a vocabulary without `inbox` is expressible.

**Item J — affordances, guards, and the phase's docs. Landed 2026-08-17.**
The last item. It owns no verb of its own; it closes the ones the phase left half-wired.

- **Affordances.** Every block already matched VERBS_PLAN §5's table, so the work was the
  guarantee, not the wording: `tests/cli/test_affordance_guard.py` resolves every `bm <verb>` in
  every affordance block *and* every notice line against the shipped click app, and a second test
  asserts the checked set is the declared set. See the W19 item 5 close block.
- **The notice guard** gains two exemptions with their reasons. `bm undo` no longer calls
  `emit_notices`: it is a store-scoped mutation, its own output already names `bm history dirty`,
  and a notice there would count violations across every project the undo did not read (W5-C). It
  follows `bm db reindex`'s rule — the next read verb carries the notice. `bm mine`'s exemption
  lost its "the W1 lane owns this file" placeholder and states the real reason: it reads
  transcripts off disk and resolves no project.
- **The import guard** covers `new`, `edit`, `mark`, `done` and `undo` (**H3**). Each probe moves
  the bootstrap project into `store/<external_id>/` first, so the write runs D3's real path rather
  than its off-store degradation, and seeds through `bm new` — itself a native verb, so seeding
  adds no import the probe would not otherwise measure. `undo` is the widest surface any native
  verb has: it reverses its own seed commit and reindexes what it restored.
- **`write_default_vocabulary` moved** from `services/project_service.py` to
  `vocabulary/model.py`, which owns the format. It landed in the service only because this module
  was being edited concurrently.
- **Docs:** `README.md` gains a verb table; `AGENTS.md`'s four numbered capabilities are marked
  shipped and its flat verb list matches what exists; `docs/OUTPUT_CONTRACT.md` cross-references
  the path-verb exception from rule 4 and states `bm show`'s shape; `.forked/schema.md` §11 Q6 is
  marked built, with the one narrowing (V-J1 — since closed, so the narrowing no longer applies).

**Not done, deliberately:** item A's note says `direct_note_writer()` should move from
`index/local_write_stack.py` to `cli/direct.py` in this item. It stays where it is. The move is
cosmetic — every verb imports it by name and the import guard already proves the boundary holds —
and J's own subject is the guards, so shifting a write-path symbol under them in the same pass
would weaken the evidence they produce. Whoever next edits `cli/direct.py` can carry it.

Tests: `tests/cli/test_affordance_guard.py` (4, new), 6 added to `tests/cli/test_brief.py` (31 →
37) for W8 F1, 1 removed net from `tests/store/test_write_hook.py` (17 → 16) for C3, and 5 new
parametrized cases in the import guard (`new`, `edit`, `mark`, `done`, `undo`).

**Item C3 — the double dirty-file notice. CLOSED 2026-08-17. The recommendation above was
reversed.**
The entry recommended keeping the write hook's line and having `emit_notices` drop `dirty` when a
history notice had already printed. The reverse shipped: **`emit_notices` is the one home**, and
`record_note_write` returns nothing about `dirty_others` (`store/write_hook.py`).

Three reasons, in order of weight:

1. The hook's count is **store-wide and cannot be anything else** — `commit_paths` reads one
   `git status` over the whole worktree. W5-C's whole point is that a pinned verb reports its own
   project, and `emit_notices` already does exactly that through `dirty_count(prefix)`.
2. `emit_notices` carries the cap, the priority order and `--quiet`; the hook's line sat outside
   all three, so a write could print three notices where W8 allows two.
3. Suppression in `emit_notices` would have meant the notice layer inspecting what the payload
   said — the coupling the notice module was written to avoid.

What is lost: a write no longer names the dirty file *at the moment of the commit*. The condition
is still reported by the same command, one line lower, with a scoped count and
`run 'bm history dirty'` beside it. `bm history commit` keeps its own `dirty_others` line, because
there the fact being stated is "this sweep excluded these", which is about that commit rather than
about the corpus.

Test: `tests/store/test_write_hook.py::test_the_write_hook_leaves_the_dirty_report_to_the_command_notice`
asserts the empty notice tuple with `dirty_paths()` as its positive control — the other file really
is uncommitted, so the silence is a decision about where to report it rather than an absent
condition.

**Item W8 F1 — one malformed `vocabulary.yml` silenced the whole brief. Found and CLOSED
2026-08-17 (item J).**
`declared_types` loaded every in-scope project's vocabulary in one loop, so a `VocabularyError` from
any one of them reached `bm brief`'s catch-all and the brief went empty — every project's sections
gone, nothing on stdout, and `--verbose` naming only the exception. On an unscoped brief that is
one typo in one project silencing the session primer for all of them.

Reproduction, before the fix, with two projects registered:

```
$ printf 'nonsense_key: 1\n' > ~/.basic-memory/store/<other-eid>/vocabulary.yml
$ bm brief --verbose
brief: VocabularyError: …/vocabulary.yml: unknown key(s) 'nonsense_key'; allowed keys are …
$ echo $?
0
```

Fixed in `cli/commands/brief.py`: `read_vocabularies` catches per project and returns a
`VocabularyScan` of the readable projects, their union of types, and one stated reason per skipped
project. `Brief.skipped` carries the reasons out, and `--verbose` prints them on stderr. The raise
itself is untouched — W4 forbids degrading a broken file into "ungoverned", and a skipped project
contributes no rows either way. `--query` is unaffected: a search reads no vocabulary.

Tests: 4 at the query layer (including the readable-second-project positive control and the pinned
case) and 2 at the verb, in `tests/cli/test_brief.py`.

**Item V-J1 — `bm edit` does not edit a `profile`'s declared fields. Found 2026-08-17; CLOSED
2026-08-17 on the recommended answer.**
`.forked/schema.md` §11 Q6 promised `bm edit` would replace *"title/body (plus declared fields on a
`profile`)"*. What shipped moved the title and the body only, so a declared field on a profile — a
project's own `vocabulary.yml` extension — could be set at creation and never changed except by
hand, which is exactly what D12 exists to keep off the routine path.

**`bm edit <id> --set name=value`**, repeatable, and it is the only frontmatter this verb writes:

```
$ bm edit tnd-q8w3e1r5 --set owner=platform --set tier=gold -p ops
```

Four refusals, each naming what to do instead:

- **not a `profile`** — a profile is the one type whose declared fields are mutable
  (`.forked/schema.md` §1 table, §4 item 4); on every other type the frontmatter is what `bm new`
  wrote.
- **an ungoverned project** — an absent `vocabulary.yml` declares no fields (W4), so there is no
  declared field to set, and inventing one would write a key nothing in the project validates.
- **a field the project does not declare** — the message lists the declared names and points at
  `bm types`. Agents select from the vocabulary; they never extend it from a write.
- **any set-once field** — `checker.py` gained a public `SET_ONCE_FIELDS` (one list, read from two
  places) so the verb refuses before it writes, instead of building the replacement and failing the
  checker's own rule on it.

**Values are deliberately not judged here.** Whether `bronze` is a legal `tier` is
`check_frontmatter`'s rule, and the accepted write path still runs it — a second copy in the verb
would be a second answer to the same question. A test asserts the enum miss is refused with nothing
on stdout, which is what proves the write reaches the funnel.

The fields ride to the write as `RecordNote(entity_metadata=...)`, which
`prepare_update_entity_content` merges *over* the record's existing block — so a later `--set` on
one field leaves the others standing, and no unmentioned field is dropped. `--set` also counts as
"something to change": `bm edit <id> --set …` with no terminal no longer trips the
nothing-to-change guard, and it does not open `$EDITOR`, on the same rule `--title` follows.

Files: `cli/commands/record_write.py`, `vocabulary/checker.py`, `vocabulary/__init__.py`,
`README.md`. No `docs/OUTPUT_CONTRACT.md` change was needed: the refusals are one stderr line and
exit 1 with nothing on stdout (rule 6), and the payload line is unchanged.
Tests: 7 added to `tests/cli/test_record_write_commands.py`; its `seed_project` helper gained a
`fields=` parameter, since no project in the tree declared fields before this.

**Item V-J2 — one malformed `vocabulary.yml` silences the per-command notice for every verb. Found
2026-08-17; CLOSED 2026-08-17 on the recommended answer — both halves.**
The same shape as W8 F1, one layer down and not fixed with it. `gather_notice_counts`
(`cli/notices.py`) calls `direct_revalidate_vocabulary(scope.project)`, which walks **every**
project when the scope is unscoped and raises `VocabularyError` on the first broken file.
`emit_notices` catches everything by design — a notice must never fail a command — so the outcome
is that *no verb prints any notice at all* while one project's file is malformed, with only a
`logger.warning` in the log file to say so. Every W5-B guarantee is off for the whole registry
because of one typo.

Reproduction:

```
$ printf 'nonsense_key: 1\n' > ~/.basic-memory/store/<any-eid>/vocabulary.yml
$ bm ls            # violations, review-due and inbox counts all absent, exit 0
```

Positive control: remove the file and the same command prints the notice again.

Not fixed here because the fix is a decision, not a patch: either revalidation degrades per project
the way `read_vocabularies` now does, or the notice gains a line of its own naming the unreadable
file. Recommended answer: **both** — skip the broken project's counts, and say which file is
broken, because a silently reduced count is the failure W5-B exists to prevent.

**What shipped, 2026-08-17.** Both halves, in the shape `bm brief` already uses (W8 F1).

`direct_revalidate_vocabulary` now returns a `RevalidationScan` — the count it rechecked, plus one
`UnreadableVocabulary(project, path, reason)` per project it could not read — and catches
`VocabularyError` per project instead of aborting the pass on the first one. The raise itself is
untouched: `load_vocabulary` still refuses to degrade a broken file into "ungoverned" (W4), and a
skipped project is *named*, never treated as governed-by-defaults.

`gather_notice_counts` drops the skipped projects from the set it counts rows for — their violation
rows are stale by definition, because the pass that would have refreshed them is the one that failed
— and carries the reports out on `NoticeCounts.unreadable`. `notice_lines` prints one line for them
**above every count**, since it is the line that says the counts below are incomplete:

```
$ printf 'nonsense_key: 1\n' > ~/.basic-memory/store/<other-eid>/vocabulary.yml
$ bm ls
… payload …
vocabulary unreadable in 'other' — its records are not counted below:
/…/store/<other-eid>/vocabulary.yml — run 'bm types'
3 records need attention (…) — run 'bm doctor'
```

Judgment calls, both small and both deliberate:

- **The line points at `bm types`, not `bm doctor`.** Doctor reads no vocabulary file at all — it
  reports rows from the table — so it cannot explain a parse error. `bm types` prints it, naming the
  file and the problem. The full `VocabularyError` message is too long for a notice line, so it goes
  to the log at WARNING, which is where `emit_notices` already sends what it swallows.
- **One line however many files are broken**, naming the first and counting the rest (`(+2 more)`).
  A line per project would eat W8's two-notice cap and turn the notice into the report `bm doctor`
  is for. The unreadable line does count against that cap, and the dirty-file count is skipped when
  the cap is already full — no `git status` fork for a number that cannot print.

A pinned notice on the broken project prints that one line and no counts, which is the same answer
as before with a name attached to it.

Files: `cli/direct.py`, `cli/notices.py`.
Tests: 3 added to `tests/cli/test_notices.py` (19 → 22) — the rendering order at the pure layer, and
two against real files and a real database: an unscoped gather whose *other* project still produces
its count (the positive control that the pass ran rather than aborting), and a pinned gather on the
broken project, with the file repaired at the end so the same corpus produces a count.

### W5 — the remaining schema-validation rules, inside `bm doctor` — **CLOSED 2026-08-16: all six items shipped; never a `bm check` command**
**Rewritten 2026-08-03.** Two things were wrong with this entry, one naming and one substantive.

**Naming.** It was titled `bm tend check`. There is no such verb and there will not be one:
`AGENTS.md` says the verbs ship flat under `bm` and, explicitly, *"There is no `bm check` — the
schema and integrity checks land inside the existing `bm doctor`"*, because a second checking
command would immediately be the one nobody runs. `tend` is the codename for the design, not a
namespace.

**Substantive — four of the eight rules are already closed.** Struck, with the commit that closed
each:

- ~~T4, unresolved relations~~ — **shipped `7e4a0d2e`**. `bm doctor --project` prints dangling
  forward references, oldest source first (`cli/commands/doctor.py:198-218`, reached through
  `direct_corpus_integrity_report` on the fast path).
- ~~`permalink` absent or `!= id`~~ (rule 3) — **shipped `6cf15451`**. The same command reports
  permalink-invariant issues; the query is `EntityRepository.find_permalink_integrity_issues`
  (`cli/direct.py:73`).
- ~~T6, doubled frontmatter blocks~~ — **moot**. The defect does not exist in this tree; see
  **R-T6** in RESOLVED. There is nothing to lint for.
- ~~Rule 4, flag **any** list-valued frontmatter field~~ — **premise reversed**. It existed only
  because `--meta` could not query a list (T1). B1 shipped `$contains` and `$in` in `43d1a3a4`, so
  list-valued fields are queryable and flagging them all would now be noise.

**What W5 still owes** — the schema rules that have no home in `doctor` yet:

1. `supersedes` appearing on a type other than `finding`.
2. Set-once field violations generally (the permalink case is done; the rule is not).
3. `date-ref` present on an `inline`/`mtime`/`inferred` rung — it is permitted on one rung only.
4. `review-by` missing on a `finding`, and its default injected from `vocabulary.yml` at write time.
5. Validation must run **on the read path as well as the write path**. The predecessor tool
   validated only on write, which is why `status: done` drift sat undetected on disk for six months.

All five are `vocabulary.yml` rules, which is why W5 is wired by the bespoke checker W4 builds
and not by `picoschema/` (see O-picoschema for the grounds).

Found in: sweep-schema.md:43.

**DECIDED 2026-08-04 (user) — the mechanism, which governs all five rules.**

W4 settled the policy (agent write path rejects, sync path always indexes and never rejects,
`doctor` reports). W5 owes the mechanism, and one question sits under all five rules: *where does a
violation live between the moment sync indexes a bad file and the moment anything reports it?*
Every check `doctor` runs today is a SQL query over indexed columns, but four of these five rules
are about frontmatter **values**, which are not columns.

**A — sync persists violations; `doctor` queries them. Not a re-parse.**
`upsert_entity_from_markdown` already parses the file, so it runs the checker and writes rows to a
violation table keyed by entity. **Two paths record, not one** (added 2026-08-16, T23): the move
planner in `index/local_moves.py` also runs the checker, and its violations are the ones a reindex
cannot recover — a table fed only from the sync path silently loses exactly those rows. Rejected
alternative: `doctor` walks the store and re-parses. The deciding reason is **B** below — a warning on every command has to be nearly free, and the cheapest
possible re-parse is O(corpus) file I/O, which is the entire latency budget of a fast verb spent on
a banner that usually says nothing. Persisted violations make it one indexed count query.

Secondary grounds: `bm doctor`'s hygiene checks and `bm brief` consume the same rows instead of each
re-parsing (this read `bm gc` until 2026-08-07 — W2 abolished that command the day after W5-A was
written, and the reconciliation pass caught the slip);
and it is the only option where W4's *"index it, record the violation, let doctor report it"* is
literally what the code does — the re-parse options give the sync-path check and the report-path
check two implementations that can drift.

Cost, stated honestly: one table, one Alembic migration, and a revalidation trigger when the
vocabulary file changes (counts are stale until then). Vocabulary edits are *"a deliberate human
act"* per `.forked/schema.md` §3, so that trigger has an obvious home.

**No background `doctor`.** The user's premise was right — agents do not run `doctor` on their own
initiative — but the fix is the nag, not a daemon. Sync already knows the violation at write time;
a background process would re-derive it with a lifecycle to own, against the house rule on hidden
background work.

**B — the nag: any project-touching command reports the count.**
Rationale (user): agents treat corpus hygiene as not-their-problem and will not run `doctor`
unprompted, but they reliably act on a warning delivered as part of a message they were already
reading — the model is a `git push` reporting outstanding advisories.

- **Content: count + top reason + pointer.** `4 records need attention (3 propose type 'runbook')
  — run 'bm doctor' for details`. The top reason is one `GROUP BY` on the same query and it is what
  makes the line actionable; a bare count just relocates the lookup into `doctor`.
- **Verbosity is not a concern.** Erring long is fine here.
- **Never changes an exit code.** Violations are corpus state, not command failure —
  `docs/OUTPUT_CONTRACT.md`'s addressing-vs-content line puts them on the content side. A non-zero
  exit would break every script that runs `bm ls` against an imperfect corpus.
- **No throttle.** Rate-limiting means the one command an agent runs in a session may be the
  suppressed one, and it makes behaviour depend on invisible state.
- **Suppressed only on `bm doctor` itself**, which is about to print all of it.
- **Stream and format are NOT decided here** — stderr, a `notices` field, or trailing the payload
  are all open. That question belongs to **W20**, which reopens the shipped output contract.

A correction worth keeping: an early draft argued for the top reason on the grounds that agents
"learn to ignore" a constant banner. They do not — every session is fresh. The argument that
survives is actionability, not habituation.

**C — nag scope follows `.bm.yml`, which is a scope *narrowing*, not a project lookup.**

| cwd | scope |
|---|---|
| inside a `.bm.yml` tree | pinned to that project — you declared which one you mean |
| anywhere else | **all projects** — unscoped is the honest answer, not a fallback |
| `--project X` | overrides either |

Pinned → that project's count. Unscoped → a rolled-up count, with the top reason naming its
project. This is the user's requirement in both directions: an agent must not be handed another
project's problems while working inside a marked tree, *and* an agent must be able to review all
outstanding `bm` work, pull information out of another project, or hand off a session without first
`cd`-ing anywhere. Both fall out of the table with no flag — `--all-projects` has nothing left to
do and is not built.

Rejected: nagging about the configured default project when no marker is present. It is the exact
behaviour the user ruled out, and naming the project in the line only stops a reader being confused,
not an agent from acting on it.

**Consequence — the default-project fallback retires for reads.** Today an unmarked cwd resolves to
`config.json`'s default project. Under this model reads are unscoped instead. The default project
survives as a **write** target only (`bm new` outside a marker still needs a home for the note).
Reads unscoped, writes explicitly homed.

**What remains mechanical.** With A settled, the five rules above are lints on one code path with
one report surface. Rule 5 (validate on read, not only on write) is largely answered: sync validates
every file it indexes, including hand edits, so drift cannot sit undetected the way `status: done`
did in the predecessor tool. W4 also moves rule 4 — `review-by` is required on **`guide`** as well
as `finding`.

**PROGRESS 2026-08-16 — item 1 shipped: rules 1 and 4, in the checker and the create path.**
Two of the five rules above now hold. W5 stays open: rules 2, 3 and 5 were already done, but
nothing persists a violation and `bm doctor` still reports none of this.

- **Rule 1, `supersedes` only on a `finding`.** It is not a frontmatter rule — `.forked/schema.md`
  §5/§12 moved `supersedes` into a `## Relations` line — so `check_frontmatter` gained
  `relation_types`, threaded through `enforce_vocabulary`/`apply_vocabulary` to every caller that
  parsed the record: the accepted runner's create, PUT and edit sites, and EntityService's three
  record sites. `None` means *the caller did not parse relations*, not *the record has none*, and
  the rule is skipped there — the move planner and `index/local_moves.py` pass nothing, because a
  move rewrites a path and no relation line.
- **Rule 4's default.** `default_review_by(vocabulary, today)` in `vocabulary/model.py` returns
  `review_months` calendar months out, clamped to the target month's last day. It is stamped on
  the schema inside `prepare_accepted_note_create`, **before** `enforce_accepted_note_vocabulary`,
  so the accepted markdown, its checksum, and the entity row all carry the value the checker then
  judges — stamping after prepare would validate one write and store another. Create only, on a
  `finding` or a `guide`, only when the write states no date of its own.
- **Two shipped strings were false and are now true.** `bm types` promised "Defaults to N months
  out" and the glossary promised "bm fills it in from review_months" while nothing filled anything
  in. Both now say what the code does, and both say it is the *missing* field that gets filled.
- **Judgment call:** the supersedes match is case-folded. `Relation.type` is verbatim from the
  line, so an exact match would let `Supersedes [[X]]` through a rule a reader would say it breaks.
- **Judgment call:** a governed create now reads `vocabulary.yml` twice — once for the stamp, once
  inside the funnel. Kept: the alternative was rewriting the funnel's single `project=` entry point
  at all four call sites to pass a loaded vocabulary, which is a wider diff across what T22 shipped,
  for one `stat` and one parse of a few hundred bytes.

**PROGRESS 2026-08-16 — item 2 shipped: the `violation` table, its migration, and its repository.**
Mechanism A now has a place to put a violation. W5 stays open: nothing writes to the table yet.

- `Violation` (`models/knowledge.py`) with `Entity.violations`, cascaded DB-side
  (`ondelete="CASCADE"`) and ORM-side (`all, delete-orphan`), matching `NoteContent` because both
  delete paths are live in this tree. `UniqueConstraint(entity_id, rule, field)` makes one row per
  rule per field per record; `ix_violation_project_severity` serves the W5-B count query.
- Migration `o8j9k0l1m2n3` (down_revision `n7i8j9k0l1m2`, the head at the time) creates the table
  **and** adds `project.vocabulary_stamp` — item 4's revalidation stamp, column only, no logic. One
  migration for both because they are one mechanism: the rows say what is wrong, the stamp says
  which vocabulary said so.
- `repository/violation_repository.py`: `replace_for_entity` (delete-then-insert, so an empty list
  clears and a re-check cannot accumulate), `count_by_reason`, `count_for_projects`, and
  `list_for_project` joined to `entity.file_path`. The reads take their project scope as an
  argument rather than from the repository, because W5-C's unscoped notice rolls up every project.
- **Judgment call:** the plan named the roll-up `count(session, project_ids)`. `Repository.count`
  already exists with an incompatible signature, so it ships as `count_for_projects`.
- **Judgment call:** `replace_for_entity` does not de-duplicate its input. Two violations with the
  same rule and field hit the unique constraint and fail the write loudly, which is correct: that
  is a checker bug, and silently dropping one would lose a row nobody would ever look for.

**PROGRESS 2026-08-16 — items 3 and 4 shipped: both record paths persist, and a vocabulary edit
re-checks the corpus.** The mechanism is now end to end from a hand-edited file to a queryable row.
W5 stays open: nothing reports the rows yet (item 5) and nothing calls the trigger yet (item 6).

- **Item 3, the sync path.** `EntityService._persist_vocabulary_violations` writes the record's
  whole row set after each of the three record sites checks. The create site persists **after**
  `upsert_entity` returns, because that is where the entity id first exists; update and relations
  already hold one. Every write uses the mutator's own session — the funnel still takes none, and a
  second connection is the W4 deadlock.
- **Item 3, the move path.** `LocalProjectIndexMoveContentUpdater.plan_moved_file_content` persists
  where it **rewrites** the permalink, which closes what T23 recorded as owed: the batch stamps the
  entity with the planned checksum, so nothing would re-check those bytes. The updater gained a
  `project_id` field, passed by the scan and watcher factories.
- **The refused arm of that branch persists nothing**, and its WARNING is the whole record. The file
  it declines to rewrite is unchanged and conforming, so its last index pass's rows still describe
  it; the violations were judged against a permalink no file will hold; and a `set-once-changed` row
  per hand move is a nag nothing could ever clear. See T23's close block.
- **Reject mode persists nothing, confirmed.** `enforce_accepted_note_vocabulary` raises from inside
  the funnel before returning, and the whole mutation is one transaction that rolls back. A refused
  write never joined the corpus, so a report about the corpus must not mention it. That is right
  about rejections and wrong about *advisories*, which are accepted and stored nowhere — filed as
  **T29** rather than folded in here.
- **Item 4, the trigger.** `services/vocabulary_revalidation.py`:
  `revalidate_if_vocabulary_changed(session, project)` compares `sha256(vocabulary.yml)` to
  `project.vocabulary_stamp` and returns 0 on a match, which is the whole warm cost. On a mismatch
  it pages the project's entities by primary key, re-checks each one's `entity_metadata`, replaces
  its rows, and stamps last — so a failure part-way leaves the project looking unchecked rather than
  trusting half a rewrite. A malformed file raises `VocabularyError` and does not stamp.
- **`cli/direct.py` gained `direct_revalidate_vocabulary(project_name=None)`**, one project or every
  project, returning a count and printing nothing. Item 6 calls it before its count query.
- **Judgment call — a check that could not decide a rule preserves it rather than clearing it.**
  `replace_for_entity` gained `preserve_rules`, and the checker exports `RELATION_DERIVED_RULES`
  (`supersedes-not-on-type`, needs parsed relations) and `HISTORY_DERIVED_RULES`
  (`set-once-changed`, needs the previous write). Without this the plan's own promise that "rule-1
  rows survive from their last write" was false in two places: a move parses no relations, and
  revalidation reads `entity_metadata` alone, so a plain replace would have deleted the
  `set-once-changed` rows the sync path records from a note's previous write. An **ungoverned**
  project preserves neither —
  no rule survives at all, so every row goes.
- **Judgment call — the sync path skips persistence entirely on an ungoverned project.** There are
  no rows to clear there, and one DELETE per file per index pass is the common case paying for the
  rare one. The single state that does leave rows behind, a deleted `vocabulary.yml`, is cleared in
  one pass by the trigger. The move planner's rewrite arm does **not** repeat the skip: a move is
  rare where an index pass is per-file, so it replaces unconditionally rather than reading the file
  again to learn it has nothing to delete.
- **Judgment call — the trigger is not wired into a scan or sync pass**, against that half of the
  item's brief. Violations go stale when the *vocabulary* changes, not when files do, so firing on
  sync would leave counts wrong for anyone who never syncs and right only by accident for everyone
  else; and the index runner is a Protocol with several implementations and no single start-of-pass
  seam, so it is neither cheap nor obvious. This restates the plan's own recommendation.
- **Judgment call — deleting the vocabulary clears the project in one DELETE**, not through the
  per-record loop. Reversed 2026-08-16 from "one code path is worth more than one statement": with
  no vocabulary there is no rule any record can break, so the loop computes no verdict and inserts
  nothing — it is one DELETE per record to reach the state one DELETE reaches.
  `ViolationRepository.clear_for_project` is that statement, and the count the function returns
  comes from a `COUNT(*)` over the project's entities, because the unit stays "records decided".
- **The plan's item-3 test list named an MCP `write_note` leaving rows. It cannot**, and the tests
  do not pretend otherwise: an off-vocabulary agent write is refused (T22), and an accepted one
  never touches `EntityService`. Rows come from hand-edited files and from moves, which is exactly
  what W4 said record mode was for. The real-path coverage moved to
  `tests/index/test_local_project_index.py` accordingly.

**PROGRESS 2026-08-16 — item 5 shipped: `bm doctor` reports both groups, on the fast path.** The
mechanism is now end to end from a hand-edited file to a printed line. W5 stays open on item 6
alone: nothing calls the revalidation trigger and no command carries the notice.

- **Two groups, one command.** `integrity` prints dangling relations, permalink invariants and
  every `severity="error"` violation; `hygiene` prints expired `review-by`, `date-source:
  inferred`, `state` records untouched for over 30 days, the `inbox` pile with each record's
  `proposed-type`, and every `advisory`. `--only <group>` asks one of them; an unknown value is an
  addressing failure (exit 1, stderr, no payload). A clean corpus prints `No issues` per section
  and exits 0 — violations are corpus state, never a command failure.
- **Doctor is a native command now.** The MCP client graph moved inside the self-test, and
  `doctor` joined `NATIVE_COMMANDS` in the import guard. The guard's probe gained `cwd=tmp_path`,
  because an unscoped verb walks up looking for `.bm.yml` and would otherwise depend on whether
  the checkout carries a marker.
- **`bm doctor` with no flag is the corpus report; the self-test moved to `--self-test`.** The nag
  item 6 builds says *"run 'bm doctor'"*, and the command that answered that had been creating a
  throwaway project and saying nothing about the corpus. `just doctor` and the README name the
  flag now.
- **Scope C lives locally in `doctor` for now**, per the item's brief: `--project`, else a
  `.bm.yml` walk-up, else every project. It does not fall back to the registry default. The shared
  `cli/scope.py` swaps in later; the marker name is validated by the report's own project lookup,
  which fails loudly rather than reporting a clean corpus for a name that resolves to nothing.
- **Judgment call — the report is one call into `cli/direct.py`, not four.** `direct_doctor_report`
  takes the projects and the two group flags, opens one session, and returns dataclasses;
  `doctor.py` only renders. A group nobody asked for is not queried, so `--only` is a saving rather
  than a filter. `direct_corpus_integrity_report` is gone: it was that function minus the
  violations, and two report paths would drift.
- **Judgment call — doctor does not fire the revalidation trigger.** Item 4 shipped it and item 6
  owns its call site. Doctor therefore prints rows judged by the vocabulary that was in force at
  the last write; after a vocabulary edit its violation rows are stale until item 6 lands. Worth
  revisiting then: doctor is the surface that must never lie about the corpus, and the trigger is
  one hash compare on the warm path.
- **Judgment call — `proposed-type` is a fixed schema key, not a declared field.** It went into
  `_SCHEMA_KEYS` and `_TYPE_ONLY_FIELDS` as legal on `inbox` alone, so writing it on any other type
  is a `field-not-on-type` error rather than an `unknown-key` advisory. Nothing writes it yet.

**PROGRESS 2026-08-16 — item 6a shipped: read scope C, in `bm brief` and `bm status`.**
The default-project fallback has retired from the read path. W5 stays open: item 5 (doctor's
report) and item 6's notice half are still owed, and the notice is what mechanism B is.

- **`cli/scope.py` is the whole of C.** `resolve_read_scope(explicit, cwd)` returns a `ReadScope`
  that is either pinned to one project or covers all of them: `--project` > nearest `.bm.yml`
  (`find_marker` already returns the nearest, so a nested marker beats its parent) > every project.
  `project_marker.resolve_cli_project` keeps the default-project tail and its docstring now says
  that tail is for **writes** only.
- **`bm brief` unscoped rolls up.** One query per section across every active project, ordered by
  `updated_at` and capped at `MAX_ROWS` for the whole brief — not per project, which would make a
  brief grow with the registry and defeat W8's cap. Each row carries its project as a label; a
  pinned brief names the project once in the header and leaves rows unlabelled.
- **`bm status` unscoped prints one plain section per project**, separated by a blank line
  (contract rule 1). The per-project block is byte-identical to what a pinned run always printed.
  An empty registry prints `no projects registered` and exits 0 (rule 5) rather than nothing.
- **W8's `--verbose` is built, and it is the ~10 lines W8 estimated.** `bm brief --verbose` states
  on stderr why it printed nothing: the scope it read and where that scope came from, or the
  exception type and message. `bm brief` gained `UnknownProject` for the case W8 named first — a
  bad project name — because an unknown project and a quiet corpus were otherwise the same output.
- **Judgment call — an unusable marker raises rather than widening to all projects.** Brief's old
  forgiving wrapper (`project_from_marker`) is gone. Degrading a broken marker to "read everything"
  would hand a marked tree exactly the cross-project view the marker exists to exclude, and would
  do it precisely when the configuration is wrong. Brief still degrades itself — the failure is
  caught at the verb, prints nothing on stdout, and exits 0 — so constraint 3 holds.
- **Judgment call — `bm brief` keeps exit 0 on an unknown `--project`.** The contract calls an
  unaddressable request a failure (rule 5, exit 1). Brief is the documented exception because it
  runs as a blocking session-start hook where a non-zero exit surfaces as a harness error;
  `--verbose` is what makes it diagnosable instead.
- **`bm status` does exit 1** on an unusable marker or an unknown project, unchanged: `MarkerError`
  is a `ValueError` and the verb already treats that as an addressing failure.
- **B5's "wired sites" list is stale in one line** as of this change: `bm brief` no longer keeps a
  forgiving marker wrapper, and neither brief nor status ends at the default project on a read.
  The live statement of scope is here, in W5-C.
- **`bm tool search-notes` is left alone, deliberately.** It is the MCP path, not the fast path,
  and W20 already treats it separately. Restated from the plan so the omission is not read as an
  oversight.

**CLOSED 2026-08-16 — item 6b shipped: the notice. W5 is done.** Mechanism B is live: every
project-touching verb ends by stating what is outstanding, so an agent that never runs `bm doctor`
still learns that it should.

- **`cli/notices.py` is the whole of B.** `emit_notices(scope, quiet=, command=)` prints, after the
  payload on stdout, at most two lines in W8's order: violations → expired `review-by` → the inbox
  pile → dirty store files. `--quiet` drops it and returns before the query; `bm doctor` suppresses
  it by name, because it is about to print every row the notice summarizes.
- **It revalidates before it counts.** The gather calls `direct_revalidate_vocabulary` for the
  projects in scope first, so a vocabulary edit changes the count on the very next command rather
  than at the next index pass — nothing on the index path would look again, because no note
  changed. This also closes item 5's open worry that doctor's rows go stale after a vocabulary edit:
  doctor resolves scope through the same `cli/scope.py` now, and any read verb run in the same
  session has already refreshed the rows.
- **Warm cost, stated:** one SQLite connection, one `stat` + `sha256` per in-scope `vocabulary.yml`
  (a string compare when nothing changed), three indexed counts, and — only when fewer than two
  higher-priority conditions fired — one `git status --porcelain`. No MCP or API import is on the
  path; `bm brief` joined `NATIVE_COMMANDS` in the import guard, which is what proves it.
- **The verbs covered**: `brief`, `status`, `doctor` (suppressed), `types`, `orphans`,
  `project list`, `project ls`, `project info`, `history dirty`, `history commit`.
  `tests/cli/test_notice_guard.py` walks `cli/commands/` as an AST and fails on a command that
  neither calls the notice nor is exempt with a stated reason, plus asserts the covered set by name
  so a silent drop-plus-exempt cannot pass.
- **`bm orphans` rolls up and `bm types` prints one section per project**, closing the last two
  read verbs still on the write chain (raised by the 6a review). A pinned run of either is
  byte-identical to what it printed before; only a roll-up labels its sections, because only a
  roll-up has something to disambiguate. `bm types` puts each project's `vocabulary.yml` path in
  its section heading when unscoped — one trailing affordance line cannot name five files.
- **Judgment call — the count is violation rows, not distinct records, while the line says
  "records".** `COUNT(DISTINCT entity_id)` would make the noun exact but would stop the top
  reason's number summing into the headline, which reads as a bug in the line itself. The headline
  and the reason are counted the same way on purpose.
- **Judgment call — `count_by_reason` now groups by project as well as rule and field.** W5-C
  requires the unscoped top reason to name its project, and a project-blind `GROUP BY` cannot
  answer that. `ViolationReason` gained `project_id`.
- **Judgment call — the two cross-project hygiene counts are functions, not methods.**
  `EntityRepository` is pinned to one project by construction. Widening its constructor to accept
  `None` would leave every other query on the class silently unscoped, so
  `count_review_due_records` and `count_inbox_records` take their scope as an argument at module
  level in `repository/entity_repository.py`.
- **Judgment call — `store.history` gained `dirty_count`, which does not create the repository.**
  `dirty_paths` goes through `ensure_store_repo`, which initializes the store and rewrites its
  config. That is right before a write and wrong on a read: a report must not create the thing it
  reports on. An absent store is zero dirty files.
- **Judgment call — the notice swallows its own failures.** The payload has already printed and
  the command has already succeeded, so a locked database or an unreadable vocabulary logs a
  warning and prints nothing rather than becoming the run's exit code. It is a *warning*, not a
  debug line, because the CLI configures loguru at INFO: at DEBUG the swallow would be silent, and
  a broken database would turn every command into a success with no trace anywhere. This is the one
  broad `except` the house rules would otherwise forbid, and W5-B's "never changes an exit code" is
  why it is here.
- **Judgment call — a verb passes the scope it actually read.** `bm project list` lists every
  project, so its notice is unscoped without a marker walk; `bm project ls --name X` is pinned by
  its own flag. Re-deriving a scope the payload did not use would be the cross-project leak W5-C
  exists to prevent.
- **W8's third row pointed at `bm ls --type inbox`, which did not exist when this shipped.** The
  inbox notice points at `bm doctor --only hygiene`, which lists the same pile. Naming a verb that
  answers "no such command" teaches the surface wrongly (W19 item 5's own correction). **Swap the
  pointer back the day `bm ls` lands** — it is one string in `cli/notices.py`.
- **Two of W8's six conditions are not built, and `cli/notices.py` says why in code**: "nothing read
  yet this session" needs per-session memory that W5-B's no-throttle rule rules out, and "unmined
  sessions" needs `bm mine` to record what it mined, which it does not.
- **`db.has_active_engine()` is new, and it is load-bearing.** A verb that opened its own engine
  must dispose of it; a verb that borrowed one must not, because disposing a borrowed in-memory
  engine destroys the database. `bm brief` and the notice both ask before they clean up.
- **TODO for the W1 lane: `bm mine` is exempt in the notice guard and should probably not be.** It
  is a project-touching verb by W8's table but reads Claude Code transcripts rather than the corpus.
  The exemption states the reason and names this block; the W1 lane owns the file and the decision.
- **What is left, and where it went.** The verbs phase (`bm new`, `bm edit`, `bm ls`, `bm show`,
  `bm done`, `bm mark`) is next and is not W5's — see the plan's judgment call 1. W8 still owes its
  items 1 and 2 (a pointer-shaped search mode; sections derived from the vocabulary). **T29** (an
  advisory raised by an agent write is logged and then lost) and **T30** (every native command pays
  the MCP client graph through `run_with_cleanup`'s module) are both open and both untouched here.

### W6 — an idempotent, resumable importer — **CLOSED 2026-08-05 (user): no importer ships; it is a Claude workflow**
The corpus is written by other sessions while a migration runs. Measured over twenty minutes in a
single session: `project-a` 271 → 368 lines, `project-b` 292 → 438, `project-c` 21 → 31, and the
corpus count 52 → 53 files. A one-shot importer silently drops everything written while it runs, and
BM's import path offers no resume.

> **These counts come from a private local corpus and are not reproducible from this repo.** They
> are the record of a measurement, not a check to re-run; the requirements they produced
> (idempotence, resumability, normalize-don't-copy) are the durable part.

It must also **normalize, not copy**: the four existing predecessor stores (store-a 176, store-b 40,
store-c 14, store-d 10+37 = ~240 records) carry on-disk schema drift — `store-d` uses
`status: done` where `store-b` uses `status: in_progress`. And every tool in this space assumes
a greenfield directory (`bm project add` included), so bringing 7,746 lines across 52 heterogeneous
files in is bespoke code either way.

**Destination, fixed 2026-08-03:** every import lands in the central store at `store/<id>/` — the
source path is a source and nothing more, and is not adopted as the project's home (see W3's decision
block and `AGENTS.md`). W6 therefore runs **after** W3, so the import is itself a revertable commit.

Found in: sweep-status-agents.md:61, sweep-handoffs.md:37, sweep-inv-plan.md:49,
sweep-decisions.md:25, sweep-prior-art.md:31.

**CLOSED 2026-08-05 (user) — nothing ships for this.** The migration is a Claude workflow: scan
repos first for candidate files (`STATUS.local.md`, `.forked/`, `GAPS`/`backlog`/`todo` and
similar), then one agent per repo, with validator agents checking the output. *"We don't need any
defined code-based importer."*

The three original requirements resolve without code:

- **Resumable** — inherent to one-agent-per-repo. A dead run re-runs the repos that did not finish.
- **Normalizing** — the agents classify into W4's six types, and W4's write-path enforcement is what
  keeps them inside the vocabulary. This is the load the closed vocabulary was built to carry.
- **Idempotent** — not needed. The user does not plan to run it twice, and validator agents cover
  the case. A `source:`-collision check on the write path was proposed and **rejected as
  unnecessary**; if a repeat import ever happens, that is the cheap place to add it (two records
  legitimately share a source, so it would be a warning naming the existing record, never a
  refusal).

**The verbatim `_import/` copy from `.forked/schema.md` §7 is also dropped.** It existed to guarantee
file-level losslessness when extraction quality was uncertain. It is no longer needed: the store now
lives outside every source worktree, the source repos keep their own git history, and `source:`
points back at the original. **This holds only while the source files stay in place** — if imported
`STATUS.local.md` files are later deleted, the copy becomes necessary again and this decision must
be revisited.

**REVISITED 2026-08-07 (user) — the sources do *not* stay in place, so the archive comes back.** The
condition above fired. The user's decision: *"the source files can and should be trimmed, preferably
to basically nothing"*, with *"a snapshot at the time of running"* kept as a backup — *"could even
go into some custom place in the bm location."*

So three things change:

1. **A verbatim snapshot is taken before any trim**, at run time, of every file the migration reads.
   Byte-for-byte, no frontmatter, no schema.
2. **It lives inside the store repo but outside any project** — `store/_archive/<YYYY-MM-DD>/<repo>/`
   — so **W3's history covers it for free** and it is one `git show` away forever. It must be
   **excluded from indexing** (W10 shipped that mechanism); it is a backup, not note content, and
   `AGENTS.md`'s "the store is the only home for note content" is not violated by a file that is
   not a note.
3. **Phase 1's "never edit a source file" rule is replaced**, not merely relaxed. The new rule:
   *snapshot first, extract, then trim the source*. An agent may not trim a file it did not snapshot.

**This closes the open conflict** raised in `.forked/migration-workflow.md` on 2026-08-07 — the old
`migration.md` wanted absorbed content deleted from the source with a pointer left behind, and the
workflow forbade touching sources. The user resolved it in favour of trimming, with the snapshot as
the safety net that makes trimming reversible.

**Deliberately not decided here:** how far "basically nothing" goes — whether a trimmed
`STATUS.local.md` keeps a pointer stub, or the file is removed entirely. W9 makes `bm` replace that
file anyway, so the two decisions should be taken together when W9's dotfiles transition happens.

**Ordering consequence:** "W3 lands before W6" is now advisory rather than a build gate. W3 still
wants to land before the migration runs, so the import is a revertable commit — but nothing in the
tree depends on it.

**The workflow shape is sketched in `.forked/migration-workflow.md`** (2026-08-05): phase 0 scan
with a human-reviewed candidate list, phase 1 one extraction agent per repo, phase 2 validators,
phase 3 report gated on `bm doctor`. It records the agent-brief rules (write only through `bm new`,
never touch a source file, never invent a date, no summarizing) and the carried-over traps.

### W7 — an agent-facing output contract — **SHIPPED 2026-07-31: `docs/OUTPUT_CONTRACT.md` v1; schema commands + search envelope conformed**
B3 records that one `--json` command exits 1. The underlying gap is that no contract exists for it to
violate: JSON to stdout only, diagnostics to stderr, no ANSI inside JSON, non-zero exit on failure,
and a versioned schema published alongside. Coverage is the other half — any command without
machine-readable output forces text scraping, and `tend` is a machine consumer of `bm` throughout.

Found in: sweep-prior-art.md:49, sweep-beans.md:19.

**The contract is `docs/OUTPUT_CONTRACT.md` (v1, 2026-07-31).** Decisions taken (per the user's
"well-reasoned decision" delegation):

- **Empties are results, exit 0.** The dividing line is *addressing vs. content*: a request that
  cannot be scoped (unknown project, bad flags) fails; a well-scoped request whose answer is
  "nothing there" succeeds. `schema validate`'s "No schemas defined" / "No notes found of type" /
  "No schema found for type" and `schema diff`'s "No schema found" all became report-shaped
  results with a `reason` field (mirroring infer's `92d1b6c9` fix); the `error` key is now
  reserved for genuine failures, which exit 1 with stderr diagnostics (error JSON stays on
  stdout in JSON modes so the stream stays parseable). `bm tool schema-{validate,infer,diff}`
  gained the same error branch the other tool commands already had.
- **Counts: `total: int | null`, null/absent = unknown, never a sentinel; `total_is_exact` is
  deleted** (it existed solely to flag the sentinel — with an honest null it is redundant, and
  there is no compat tax in this fork). Shipped in the W7 envelope commit: schema, v2 router,
  client compat shim (removed), multi-project merge (any failed/unknown project → null), CLI
  renderer, and the legacy-sentinel fallback tests that guarded the old semantics.
- **Coverage rule is go-forward**: every new command intended for scripted use ships with
  `--json` conforming to the contract — enforced at review time via `docs/OUTPUT_CONTRACT.md`,
  which every verb built under phase 4 consumes. The 202-vs-200 status-code question noted in O6
  is wire-internal (ASGI in-process) and stays deferred until something actually reads the code.
- **Judgment call — no per-payload `schema_version` field.** The contract document carries the
  version; every consumer is in-repo and in-process, so a wire version field is speculative
  flexibility. Revisit only if an external consumer appears.

### W8 — a bounded, pointer-shaped session primer — **CLOSED 2026-08-17: both items shipped in `bm brief`**
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

**2026-08-03 — `bm brief` now exists and this entry has never mentioned it.** `e0d8631c` replaced
`bm hook` and the harness plugins with `bm brief`. The test is the one this entry already states —
size-capped, index-only, one line per record, pointers rather than content.

**DECIDED 2026-08-03 (user): `bm brief` is the primer's home.** It already passes most of the test:
size-capped (`MAX_BRIEF_CHARS`, `MAX_ROWS`), pointer rows (title/permalink/file_path), empty brief
prints nothing and exits 0, direct DB path (no MCP import cost). W8 closes when `bm brief` adds:

1. **A pointer-shaped search mode** — a query flag that returns compact pointers (one line per hit,
   permalink + title), never note content. Today brief has no search at all.
2. **Full record-vocabulary coverage** — brief hardcodes three note types (`task` active,
   `decision` open, `session` recent). When W4's closed vocabulary lands, brief must derive its
   sections from the vocabulary instead of the hardcoded trio, so new record types appear without
   editing brief.

**Amended 2026-08-04.** W4's decided type set is `task` / `guide` / `finding` / `profile` / `state`
/ `inbox` — none of which is the hardcoded trio, so item 2 is a rewrite of brief's sections rather
than a generalization of them. Two consequences worth stating before the build:

- **Not every type belongs in a session primer.** `task` (non-terminal `status`) and `state` earn
  their rows unconditionally. `finding` wants the `review-by`-expired subset, not recency. `guide`
  and `profile` are consulted on demand and probably rate no rows at all — a brief that lists every
  guide is a table of contents, which is content, which W8 exists to forbid. Deriving *sections*
  from the vocabulary must not mean deriving *rows* uniformly from it.
- **Brief names the types; it does not explain them.** The per-type explainer is W19 item 4, a
  separate on-demand command. Brief's ~50-token cap is spent every session; a filing decision an
  agent makes rarely must not be billed to it.

**DECIDED 2026-08-06 (user) — how the primer reaches an agent. This closes the question
`.forked/decisions.md:597` deferred to W8.**

**S4 is refined, not upheld.** S4 concluded "inject a pointer, not content" from the observation
that injected STATUS was skimmed. The diagnosis was right and the conclusion one step too wide. The
user's counter-evidence: injected *tool* information works reliably — the harness's own
per-tool-call status reminder and usage telemetry both change agent behaviour, and this session is a
live positive control (the assistant's repeated `STATUS.local.md` updates are hook-driven, not
self-initiated). The real split is **timing and repetition**, not injection:

| shape | example | result |
|---|---|---|
| once, at session start, before the task is known | the old STATUS dump | skimmed, then re-read later anyway |
| repeatedly, alongside actions | status reminder, usage telemetry | acted on |

A pointer injected once at startup would have failed for the same reason the content did. And the
mechanisms that work ride on **any** tool call, not on a related one — which is why an in-`bm`
notice alone cannot solve this: it requires the very action it is trying to prompt.

**Three layers. Only the middle one knows anything.**

| layer | what it is | what it knows |
|---|---|---|
| harness reminder | static line after tool calls, in the user's own hook + `CLAUDE.md` | **nothing** — "record things in bm" |
| `bm` output | notices on every `bm` command | everything — counts, what is broken, what to run |
| escalation | a heavier prompt at a milestone | comes from `bm` output; the hook cannot know |

The layers cannot drift, because the hook makes no factual claim. Every fact is produced by `bm` at
the moment it is asked.

**`bm` never installs or edits a hook (user).** *"I'm not a big fan of apps which try to add/modify
hooks themselves."* The README documents a short snippet; wiring it is the user's deliberate act.

**No cached notice file, and no `bm` call from the hook.** An earlier draft proposed `bm` writing a
one-line status file for the hook to `cat`. Retired — the user's design carries no `bm` data in the
hook at all, which removes three problems rather than solving them: the **speed** problem (measured
floor is 0.15 s for `bm --version`, 1.1 s for a real verb — unusable after every tool call),
**staleness**, and the need to label which project a cached line refers to. A static string has none
of these.

**Follow-up suggestions are `bm`'s own verbs, shipped as defaults, not user config.** An earlier
draft proposed a user-authored config list of external tools; the user corrected the premise —
*"most uses of bm are going to probably come down to 5 or 6 main tools anyway."* Once the follow-ups
are bm's own closed verb set, this collapses into the W5-B notice format already decided
(count + top reason + the command to run). There is no separate escalation mechanism; there is only
which notice fires:

| condition | notice points at |
|---|---|
| schema violations exist | `bm doctor` |
| findings past `review-by` | `bm doctor --only hygiene` |
| `inbox` pile not empty | `bm ls --type inbox` |
| dirty files the tool did not write | `bm history dirty` |
| open items exist, nothing read yet this session | `bm brief` |
| sessions in this project never mined | `bm mine` |

W5-B's cap applies: at most two per command, highest priority first.

**Corrected 2026-08-06.** This paragraph first read *"never the same notice twice in one session"*,
which contradicted W5-B's explicit **no throttle** decision and required per-session state `bm` does
not hold. Notices are stateless: the same condition prints the same line every time, because the
condition is still true. See the matching correction on W19 item 5.

**Milestones are condition-based, not invocation counts.** The user noted a hook could detect `bm`
in a tool call and treat that as a milestone, but judged it *"basically the same as follow-up
commands and probably a bit harder to keep track of"* — agreed; it duplicates state `bm` already
holds.

**The one nudge `bm` cannot produce** is the prompt to record something at all: an agent that has not
run `bm` gets no `bm` output. That is the static harness line, and it is the whole reason layer 1
exists.

**No SessionStart injection of the primer.** That part of S4 stands — the primer is content, and
content delivered before the task is known is skimmed.

**Judgment call (assistant, reversible): `guide` and `profile` get no rows in `bm brief`.** A brief
listing every guide is a table of contents, which is content, which W8 exists to forbid. They are
consulted on demand via search. Reverse this only with evidence that an agent failed to find a guide
it needed.

**Rescued 2026-08-07 from `.forked/hook-design.md` before its deletion — an empty brief and a broken
brief are the same output.** This entry records *"empty brief prints nothing and exits 0"* as a
virtue and never states the cost: a bad project name, an un-migrated DB, and a config error **all
degrade to silence too**, so a genuinely broken brief is indistinguishable from a healthy quiet one.
As built, failures log at DEBUG and `bm brief -p <name>` by hand is the only diagnostic. The fix is a
`--verbose` flag, roughly 10 lines, and it was not built. Decide it when W8 lands; note it interacts
with W20 rule 5, which makes an empty result a *stated* result rather than silence.

**SHIPPED 2026-08-16 with W5 item 6a — `bm brief --verbose`, and the scope brief reads.** Two of
this entry's open items are discharged, and W8 stays open on one:

- **The `--verbose` flag above is built**, at the ten lines it was estimated at. It states on
  stderr either the scope brief read and where that scope came from, or the exception type and
  message. A bad project name is now a distinct condition (`UnknownProject`) rather than an empty
  result, which is what made "empty" and "broken" the same output.
- **Brief's project resolution now follows W5-C**: `--project` > nearest `.bm.yml` > every project.
  An unscoped brief rolls every project up, each row labelled with its project, still capped at
  `MAX_ROWS` for the whole brief.
- **Item 1, the pointer-shaped search mode, is not built.** Brief still has no query flag.
- **Item 2, sections derived from the vocabulary, is not built.** Brief still hardcodes
  `task` / `decision` / `session`, and the 2026-08-04 amendment above says why that is a rewrite
  rather than a generalization: W4's type set shares no member with the hardcoded trio, and the
  amendment's own rule — derive *sections* from the vocabulary, not *rows* uniformly from it —
  has to be designed per type. That is the whole of what W8 still owes.

**CLOSED 2026-08-17 — items 1 and 2 shipped; W8 is done.** `bm brief` now derives its sections
from the vocabulary and carries a pointer-shaped search. What changed, in
`src/basic_memory/cli/commands/brief.py`:

- **Item 1 — `--query/-q <text>`.** Pointer rows only: permalink and title, one per line, never
  `content_snippet`. It reaches FTS through `SQLiteSearchRepository` on the direct path, respects
  the W5-C read scope, and shares the brief's `MAX_ROWS` cap across every project it covered. A
  search replaces the sections rather than joining them.
- **Item 2 — sections from `vocabulary.yml`.** The `task`/`decision`/`session` trio is gone.
  `SECTION_RULES` holds the per-type row rule the 2026-08-04 amendment demanded: `task`
  (non-terminal) and `state` unconditionally, `finding` only the `review-by`-expired subset,
  `inbox` a count and no rows, `guide`/`profile` nothing. A declared type absent from that table
  contributes nothing, so a human-added type is silent rather than guessed at. Unscoped, the
  declared types union across projects.

Judgment calls taken, all reversible:

- **A count line for `inbox`, not rows.** The pile's contents are not orientation, and W5-B's
  notice already names it with the command that lists it.
- **Terminal statuses are a constant (`done`, `dropped`), not a vocabulary key.** The vocabulary
  declares statuses but marks none of them terminal, so the set has to live somewhere; brief is
  where it lives until a vocabulary key exists. The predicate is *not* terminal rather than *is*
  declared-open, so a task with a missing or undeclared status is still shown — hiding open work
  over a schema fault the notice already reports would be the worse error.
- **The ungoverned fallback is `task` + `state`.** W4 decided an absent `vocabulary.yml` means
  unchecked, not typeless, and records still carry a frontmatter `type`. Brief assumes only the
  two types whose rows are unconditional; it never applies `DEFAULT_VOCABULARY`.
- **Section order is `SECTION_RULES`' order, not the file's.** An unscoped roll-up spans several
  vocabulary files with no single order between them.
- **`--query` with no hits prints `0 results`; a bare brief with nothing open still prints
  nothing.** A search someone typed is a question, and contract rule 5 answers questions; the
  standing brief has been asked nothing.
- **`--days` is removed.** It existed only to bound the `session` section, and `session` is not a
  type in W4's vocabulary. A flag that no longer bounds anything is worse than no flag.

**Amended 2026-08-17 (verbs item J): a broken vocabulary costs one project, not the brief.** The
first build read every in-scope project's vocabulary in one loop, so one malformed file silenced an
unscoped brief entirely. Recorded and closed as **W8 F1** in the verbs build log above.

**Note — the `beans prime` comparison figures in this entry are no longer reproducible.** Probed
2026-08-06 on the installed build: `beans prime` prints nothing and exits 0 outside a beans project,
and `beans prime --help` shows `-h` plus two global flags, contradicting this entry's *"shows no
flags at all"*. The 189-line / ~1,850-token measurement was taken inside a project on an older
build. Treat it as a historical record, not a check to re-run.

### W9 — replacing `STATUS.local.md` — **DECIDED 2026-08-06 (user): `bm` replaces it fully; no emitter is built**
The headline file stays flat (B4), but it must be written *by* the store rather than by hand — and the
write has to satisfy three consumers with three different parsers: the statusline script requires
`lines[0].trim() === '---'` **and** `lines[1].startsWith('headline:')`; a local projects-overview
script reads line 2 with no `---` check; a notify script also reads line 2. A malformed write fails
silently in the statusline while the other two display the wrong text. Three of 52 files were doing
exactly this for months: `project-a/STATUS.local.md` has an HTML comment on line 1, and
`project-b/STATUS.local.md` has `status: active` hoisted above `headline:` — both give the
lint two real, reproducible failure modes to detect. (The consumers are local, private scripts; the
parser requirements above are the whole of what the emitter has to satisfy.)

The emitter must also preserve **mtime semantics**, not just content: the projects-overview script
uses file mtime
as its staleness check, so a regen that rewrites unconditionally makes every stale project read as
fresh — the precise silent failure the flat file was kept for.

Found in: sweep-decisions.md:13, sweep-inv-plan.md:31, sweep-handoffs.md:19, sweep-handoffs.md:49,
sweep-prior-art.md:13.

**DECIDED 2026-08-06 (user) — *"bm will replace status probably fully."*** So no emitter ships. The
title above is corrected: this was never really "write the file correctly", it was "decide whether
the file survives." `.forked/decisions.md` R3 had already reached the same place from the other
direction — *"if [the tracker] holds in-progress state, **STATUS has no durable job left**."*

Rejected alternatives, both argued for by the assistant and overruled: emitting the file from `bm`
(builds an emitter for a format that does not survive, and still leaves the file hand-written
everywhere `bm` is not), and leaving `bm` out of it while giving the three consumer scripts a shared
strict parser (fixes the reproduced failure, but forecloses the replacement the user wants).

**What the replacement needs:**

1. **A fast headline source for the statusline.** The statusline re-renders constantly and the
   measured floor for *any* `bm` command is 0.15 s — far too slow. So **`bm` writes a small headline
   file on every write, and the statusline reads that.**

   *This is a deliberate reversal of the cache decision taken under W8 an hour earlier, and the two
   are consistent.* W8 retired a cache because the harness hook carries **no `bm` data at all** — a
   static string needs no cache. A statusline is the opposite case: it exists to display `bm` data,
   at a latency `bm` cannot meet. Cache is right there and wrong in the hook.

2. **A headline value.** *(Assistant call, reversible.)* Derive it from the most recently touched
   non-terminal `task` — its title, truncated to the statusline's 30-character limit. No new field,
   no set-once violation, and it matches what the current `headline: next: <action>` convention
   already means. An explicit override can be added later if deriving proves wrong.

3. **The other two consumers.** The projects-overview and notify scripts read line 2 today. They read
   the same headline file. **The mtime trap survives the migration and must be carried:** the
   overview script uses file mtime as its staleness signal, so the headline file must not be
   rewritten when nothing changed, or every stale project reads as fresh. This is the same
   no-op-write discipline W3 already requires for byte-stable serialization.

4. **The habit has to be inherited, not just the data.** `STATUS.local.md` works today because a
   global instruction plus a per-tool-call reminder keeps it current — the assistant updated it four
   times unprompted during this session, which is a live positive control. That is exactly W8's
   layer 1, so the mechanism already exists; the migration is not done until the reminder points at
   `bm` instead.

**Out of scope for this repo, and worth stating so it is not lost.** The global instruction
requiring a `STATUS.local.md` in *every* working directory lives in the user's dotfiles, not here.
Until it changes, both systems run side by side — `bm` in projects that have one, the flat file
everywhere else. That transition is a dotfiles change the user makes, and `bm` must not attempt it
(same rule as W8: `bm` never edits the user's harness config).

**Consequence for the parser bug this entry was opened for.** The two reproduced failures (an HTML
comment on line 1; `status: active` hoisted above `headline:`) stop mattering for `bm` projects once
the headline file replaces the parsed one. They keep mattering everywhere else for as long as the
flat file survives — but that is a dotfiles problem, not a `bm` gap, and it leaves with the
transition.

**CLOSED 2026-08-17 — the headline file ships; the replacement's first half is built.**
`src/basic_memory/services/headline.py` writes `store/<external_id>/headline.md` on every write
through `index/local_write_stack.py`. Point 1 (a fast source the statusline reads), point 2 (the
derived value) and point 3 (the mtime trap) are built. Point 4 — repointing the harness reminder at
`bm` — stays out, and stays a dotfiles change the user makes.

- **Location and shape are decision D6**, accepted by the orchestrator 2026-08-17:
  `store/<external_id>/headline.md`, three lines, `---` / `headline: <text>` / `---`. In the store
  because the stop-list forbids writing outside this repo and `~/.basic-memory/`; writing next to a
  working directory's `.bm.yml` would be `bm` editing someone else's tree. The shape is the
  strictest consumer's parser, so the other two read the right line for free.
- **The value** is the most recently updated non-terminal `task`, its title cut to 30 characters
  and right-stripped. A task carrying no status counts as open: hiding work over incomplete
  frontmatter would suppress the thing the file exists to show, over a fault `bm doctor` reports.
- **Read-compare-skip, not write-always.** `refresh_headline` reads the existing bytes and returns
  without writing when they match, so mtime survives a no-op — the staleness signal this entry was
  opened for. The test asserts an unchanged mtime *and* has a positive control that a real change
  moves it, because a function that never wrote at all would pass the first assertion alone.
- **No open work removes the file** rather than writing an empty headline, which would render a
  blank bar instead of letting a consumer fall back to its own default.

Judgment calls:

- **Which statuses are terminal is code, not vocabulary, and it has one home.** `vocabulary.yml`
  declares statuses but marks none of them terminal, so `{done, dropped}` lives in
  `vocabulary/model.py` as `TERMINAL_STATUSES` plus `terminal_statuses(vocabulary)`. Both askers
  call it: the headline passes the project's vocabulary and gets the set narrowed to the terminal
  names that project actually declares; `bm brief` passes nothing, because its rows span every
  in-scope project and there is no single vocabulary to narrow by. A project declaring neither
  name keeps the defaults, because an empty terminal set would make every task permanently open.
  It started as two constants — `brief.py`'s and the headline's — which review flagged: two
  callers answering "is this task still open" differently is a bug the second reader finds.
- **The headline is committed with the note** (see W3's close block), not left for a later sweep.
- **A headline the filesystem refuses is a notice, not a failed write.** By the time it is written
  the note is on disk and indexed, so an `OSError` there degrades to a line the verb prints. The
  same rule `cli/notices.py` states: the payload already succeeded, so a convenience file must not
  become the exit code. Found in review; the test makes the path a directory, which is a real
  refusal rather than a mock.
- **It takes the caller's session.** A per-write lookup that opens its own session waits on a
  connection the caller already holds, and the pool is one connection — the W4 deadlock, which
  cost 600-second suite runs the first time.

Tests: `tests/services/test_headline.py` (12), plus two in
`tests/index/test_local_write_stack.py` that drive the real write path — one asserts the file it
leaves behind is the three lines a consumer parses, the other that the note's commit contains it.

### W10 — an exclusion mechanism on the indexing path — **SHIPPED**
**Done 2026-08-03.** The entry's premise ("no ignore file") was stale: upstream shipped an ignore
mechanism before the fork point (`ignore_utils.py`, from `e0d8aeb1`) — a global
`<data dir>/.bmignore` plus the project's `.gitignore`, honored by the full scan
(`scan_local_project_index_files`), the watcher, the single-file index endpoint, and zip import.
Reproduction confirmed `.gitignore` exclusion works end-to-end on the scan path (positive control).

The real gap was narrower: the store design commits `_import/` copies verbatim (losslessness), so
those files **cannot** be gitignored — and the global `.bmignore` is instance-wide, not
per-project. Nothing could say "committed in git, excluded from the index" for one project.

**Shipped:** `load_gitignore_patterns()` now also reads a project-root `.bmignore`
(union of global `.bmignore` + project `.bmignore` + project `.gitignore`). Because every
indexing consumer already loads patterns through that function, the scan, watcher, and
single-file endpoint all pick it up with no further wiring. The never-used `use_gitignore`
parameter was deleted. Reproduction after the fix: a project `.bmignore` containing `_import/`
excludes `_import/old.md` from the scan; regression tests at both the pattern-loading and
scan level (`test_load_patterns_with_project_bmignore`, `test_scan_honors_project_root_bmignore`).

**Scope call:** this closes the *path exclusion* half. Content redaction (tokens inside indexed
prose) was never buildable as an ignore rule and stays out — the defense for secrets is exclusion
patterns (`.env` and hidden files are in the defaults) plus the gardener's flag-only pass, not a
redaction rewrite on the indexing path.

*Original entry:* BM indexes markdown under the project root with no ignore file and no redaction.
Two consequences we have already committed to needing around: the losslessness guarantee copies
each migrated file verbatim into the store (`_import/STATUS.local.md.2026-07-26`, no frontmatter,
not intended as a record), which without an exclude becomes ~1,600 lines of phantom search hits
polluting every read path; and records living inside project directories mean the indexer will
eventually ingest tokens or `.env`-adjacent prose into both a SQLite index and an embedding store,
with nothing to stop it.

Found in: sweep-schema.md:31, sweep-prior-art.md:55.

### W11 — a cross-project read — **SUBSUMED 2026-08-06 by the W5-C scope decision**
Every BM query is project-scoped and nothing aggregates across projects. The gardener's staleness
sweep and any "what is open everywhere" question both need one read across all stores; today a local
projects-overview script is the only cross-project view and it reads line 2 and mtime, nothing more.

Found in: sweep-inv-plan.md:25.

**SUBSUMED 2026-08-06.** W5-C makes `.bm.yml` a scope *narrowing* rather than a project lookup:
inside a marked tree a read is pinned to that project; **outside one, every read is unscoped and
spans all projects.** That is this entry's requirement, reached without a dedicated verb or flag —
the user's framing was that an agent must be able to review all outstanding `bm` work, or pull
information out of another project, without `cd`-ing anywhere first.

What remains is a repository-layer aggregate query, which **W5-A already requires** for the
rolled-up notice count outside a marked tree. It is a line of W5's build, not a separate item.

The gardener half of this entry is also gone: W2 folded into `bm doctor`, whose hygiene checks
inherit the same scope rule.

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

### W13 — delete the Postgres backend — **SHIPPED `79e0dad9`**
**SHIPPED `79e0dad9`.** 97 files, −6790/+694. Verified `3406 passed / 10 skipped / 0 failed`
(baseline 3444+33 collected, −61 `def test_`), `fast-check` exit 0 with zero `ty` advisories.
No alembic revision file was deleted — whole-body-Postgres revisions are no-ops so the
`down_revision` chain and stamped `alembic_version` rows stay valid. `nest-asyncio` survives
(applied outside any backend gate); `litellm` + the remaining dependency prune shipped later in
`6f8767a3` (W17).

> **Trap, rescued 2026-08-07 from `.forked/w13-postgres-inventory.md` before its deletion.**
> `nest-asyncio` (`pyproject.toml:35`) is applied **unconditionally in `alembic/env.py`**, not behind
> any backend gate — so it is live on the **SQLite** migration path. Removing it is a behaviour
> change, not a leftover prune. The inventory predicted exactly this and it is the reason the
> dependency is still there.

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

*(A paragraph instructing "until this lands, run the Postgres tests with
`BASIC_MEMORY_TEST_POSTGRES=1`" was deleted 2026-08-03. It survived the deletion it was written
against: `postgres_search_repository.py` and its tests went with `79e0dad9`, so the instruction had
become unrunnable. Recoverable from this file's own history if wanted; the one reusable fact is
restated here — `docker ps --filter ancestor=postgres` cannot match a testcontainers run, so filter
on the image name or on label `org.testcontainers=true`. That mis-filter produced a false "Postgres
never ran" conclusion on 2026-07-26.)*

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

### W19 — the user-facing vocabulary is jargon, starting with the record type names — **CLOSED 2026-08-17: item 5 ships on every verb; items 1–4 shipped earlier**
**Opened 2026-08-04 (user).** Reviewing the `.forked/schema.md` type set, the user could not state
the difference between two proposed types from their names alone: *"I honestly don't know the
difference between procedure and finding."* That is the correct reading — the names describe the
schema's internal axes (mutability, supersession) rather than what a person does with the record.

A record type name is read by an agent choosing where to file something, at the moment it is least
inclined to go look up a definition. A name that needs a glossary produces misfiled records, and
per §1 of the schema draft misfiling is the expensive failure because it is invisible.

Scope is wider than the type names, and the user named the wider scope in the same breath: **CLI
help text too.** `AGENTS.md` requires nothing of the words the tool puts in front of a person, so
the surface drifts into schema jargon by default. The house standard for this is ASD-STE100
Simplified Technical English.

**What this owes:**

1. Record type names that pass a one-question test each, so an agent can classify without a
   glossary. **Done 2026-08-04** — see the naming decision under W4: *do it / consult it / learned
   it / refer to it / how things are / can't tell*.
2. A pass over `--help` text for every shipped verb: one plain sentence per command, condition
   before command, no schema-internal terms in user-facing strings.
3. Error messages on the W4 write path specifically — a rejection must name the allowed values, in
   the same plain vocabulary, or the agent's next guess is uninformed.
4. **A command that explains the type set on demand** — one short paragraph per type, its picking
   question, and its fields. Derived from the live vocabulary file, never a hardcoded copy, or it
   drifts the first time someone declares a field. **Named 2026-08-10 (user): `bm types`** — it
   matches the question an agent is asking (`bm explain` and folding into `bm man` were declined).

**Promoted 2026-08-04 (user) — items 2–4 are a binding acceptance condition on W4, not a follow-up.**
The user agreed to six record types *"as long as we have good cli help docs that explain when to use
each one, along with a good entry / primer bm command that explains all of this."* A closed
vocabulary an agent cannot understand at the moment of filing relocates the misfiling rather than
preventing it, and §1 of the schema draft is explicit that misfiling is the expensive failure
because it is invisible.

5. **Workflow affordances — most verbs suggest their natural next verb.** Added 2026-08-06 (user):
   *"most of the bm tools could carry recommended follow-up tools that fit nicely into whatever
   workflow each one does — ex. if one tool is for searching past entries, maybe it also suggests a
   follow-up for how to edit a past entry."* This is a **different line** from W5-B's condition
   notices: a notice says *something is wrong, run X*; an affordance says *you just did X, the next
   step is Y*. It belongs to W19 rather than W5 because it teaches the surface at the moment the
   agent is standing in it — the same principle as item 3, where a rejection must name the allowed
   values instead of only refusing.

   **The affordance list is static per command.** Each verb carries a fixed list of follow-up verbs,
   each with 5–8 words naming its purpose. No conditions, no ordering logic, no memory:

   ```
   next:
     bm show <id>   read the full entry
     bm edit <id>   change an existing entry
     bm new         record something worth finding again
   ```

   **Corrected 2026-08-06 (user).** An earlier draft gave affordances conditional rules — show only
   when the next step is non-obvious, only when applicable, never twice in one session. The user
   rejected the premise: *"wouldn't that require extra context per-session to handle something like
   that?"* It would. "Never twice in a session" requires `bm` to know what a session is and to
   remember what it already printed. `bm` holds no session state, and adding it to suppress three
   lines is the wrong trade. A static list costs a fixed ~20 tokens and needs no memory.

   Suppressed by `--quiet`, which is stateless. That is the only condition.

**Items 2, 3, and 4 shipped 2026-08-10**, discharging the binding acceptance condition on W4.

- **Item 2** — `342c53da`. Every shipped verb's help text: one plain sentence, condition before
  command, no schema-internal words. One correction worth keeping: the first draft rewrote
  `--quiet` as *"Hide notes and suggested next commands"*, which is worse than the jargon it
  replaced — `note` is this tool's core noun, so it reads as hiding the records. It now says
  *"Hide the status lines and next-step hints"*. **A plain word that collides with a domain noun is
  not plain.**
- **Item 3** — in `2e62e726`, carried by the checker's messages. An unknown type names every allowed
  type with its picking question and says a new type cannot be enabled from a write; a set-once
  rejection names the sanctioned route (`bm done`/`bm mark` on a task, supersession on a finding).
- **Item 4** — `63ad4fba`, `bm types`. The type list comes from the live vocabulary and the prose
  from `vocabulary/glossary.py`, which `ee5bc1a4` created so the rejection and the explainer teach
  from one source. A declared type the glossary does not know still prints; a glossary type the
  project omits does not. Both have a test.

**Item 5 (workflow affordances) is still open** and is not blocking: it lands naturally with W5's
verbs, where there is a next verb worth naming.

**First affordance shipped 2026-08-16, on `bm mine` (W1).** It is the static three-line form this
item specifies — fixed per verb, no conditions, no ordering logic, no memory, suppressed only by
`--quiet`. Two of its three entries widen the search the caller just ran (`--context 2`,
`--speaker all`) and the third names where a keeper goes. Item 5 stays open for the remaining verbs.

**One correction to this item's own illustration:** it names `bm new`, which has not shipped, so the
affordance points at `bm tool write-note` instead. An affordance exists to teach the surface at the
moment the agent is standing in it; naming a verb that answers *no such command* teaches it wrongly.
Swap it back when `bm new` lands.

**Item 5 CLOSED 2026-08-17 (verbs item J).** Every verb that ends in something an agent can act on
now carries a static affordance line, in the shape this item specifies — fixed per verb, no
conditions, no ordering logic, no memory, dropped only by `--quiet`:

```
bm new   → bm show <id> read it back · bm ls list what is here · bm done <id> close a task
bm ls    → bm show <id> read the full entry · bm new record something worth finding again
bm show  → bm edit <id> change it · bm path <id> print its file path
bm edit  → bm show <id> read it back · bm history dirty see uncommitted changes
bm done  → bm ls --status open what is still open · bm new record what you learned
bm mark  → (the same line as done — one status change is like another)
bm undo  → bm history dirty see what is uncommitted · bm show <id> read the restored entry
bm mine  → bm mine … --context 2 · bm mine … --speaker all · bm new finding <title>
bm doctor→ bm types see what this project allows · bm doctor --only hygiene
```

**The illustration is swapped back**, as this item asked: `bm mine`'s third line names `bm new`
now that the verb exists. `bm path` carries none — its whole payload is one machine-consumed value
(`docs/OUTPUT_CONTRACT.md`, "Path verbs").

The guarantee is a test, not a convention: `tests/cli/test_affordance_guard.py` walks every
affordance block *and* every per-command notice line, resolves each `bm <verb>` against the click
app the CLI actually ships — sub-commands included — and fails on anything unrunnable. A second
test asserts that the set of blocks it checks is the set the tree declares, so a new verb cannot
ship an unchecked block beside it. That pairing is what makes the correction above impossible to
re-introduce: the earlier illustration was wrong for months and nothing could have caught it.

**Item 4 does not go inside `bm brief`.** W8 caps brief at ~50 tokens of pointer rows,
unconditionally, every session; per-type explanations would burn that budget on every start for a
decision an agent makes rarely. The split: **brief names the types** (it derives its sections from
the vocabulary anyway, so the list is free), **the write-path error teaches at the moment of
failure** (item 3), and **the explainer command carries the full text on demand** (item 4).

### W20 — `--json` may be the wrong agent-facing surface; W7's contract needs reopening — **SHIPPED 2026-08-10**
**Opened 2026-08-04 (user)**, while deciding W5's nag format: *"I don't even know if JSON is the
best choice for giving data to an agent, anyway — anything could work, markdown, a table, some
smaller structured output, whatever."*

This is a live question against a **SHIPPED** entry. `docs/OUTPUT_CONTRACT.md` v1 (W7) is built on
"JSON to stdout, diagnostics to stderr," and every verb built under phase 4 consumes it. Left
inside W7 it would be a trap of exactly the T21 shape — an open question sitting in a closed entry
where nobody looks — so it gets its own number.

**The premise.** The contract's only consumer is an agent, and JSON spends tokens on braces,
quotes, and repeated keys to encode structure an agent reconstructs from a table or a list just as
reliably. The cost is paid on every single response. The user has delegated the call: *"we're not
currently using bm so it's fine to make changes on the fly if they make sense."*

**What this owes:**

1. A measurement, not an assertion — the same result set rendered as the v1 JSON envelope and as a
   compact line/table form, token counts for both. `AGENTS.md`: a claim without a reproduction is
   not a finding. The token saving is the entire argument and it has not been measured.
2. A decision on the default rendering for agent-facing verbs, and on whether `--json` survives for
   non-agent consumers or is dropped outright.
3. Where notices go under whatever wins — this is the question W5-B deferred. A structured envelope
   can carry a `notices` field; a table cannot, so the notice trails or precedes the payload.
4. The v1 decisions that survive regardless, and must be carried forward rather than re-litigated:
   empties are results and exit 0 (addressing vs. content), `total: int | null` with no sentinel,
   errors exit 1 with the payload still parseable on stdout.

**Not a licence to make each verb bespoke.** The value of W7 was one contract every verb obeys; if
the rendering changes, the contract changes with it and stays single.

**DECIDED 2026-08-06 (user) — JSON goes. The contract specifies rules, not a serialization.**

**`--json` is removed outright, not kept as a secondary mode.** The assistant proposed keeping it
for "anything that actually parses rather than reads"; the user rejected that as a softening of an
already-taken decision. Nothing consumes it — every consumer is in-repo, in-process, and an agent.
There is no compat tax in this fork.

**Why the contract stops naming a format.** `bm` output is not one shape:

| shape | verbs | best rendering |
|---|---|---|
| lists of records | `search`, `ls`, `brief` | aligned columns — genuinely tabular |
| one record | `show` | labelled lines |
| grouped report | `doctor` | sections with headings; a table flattens the grouping that makes it readable |
| error | any | one message |

Forcing all four into one envelope is what JSON did. So v2 specifies **rules that hold whatever the
shape is**, which keeps the contract single — W7's actual value — without a universal envelope.

**The v2 rules:**

1. **One record per line** where records are listed. Fixed column order, alignment only.
2. **Identifier first**, so an id is findable without counting columns.
3. **A count on its own line at the end**, or nothing when the count is unknown. This preserves v1's
   honest `total`: absent means unknown, never a sentinel.
4. **Notices, then affordances, after the payload**, each on its own line. This answers the stream
   question W5-B deferred — no stderr split, no `notices` field, just trailing lines.
5. **Empties are results** — a line saying nothing matched, exit 0. Unchanged from v1.
6. **Errors exit 1**, message on its own line. Unchanged from v1.
7. **`--quiet` drops notices and affordances**, leaving the payload alone.

**Illustrative only — the measurement this entry owes is still owed.** Two records in the v1
envelope run about three times the tokens of the same rows as columns, and the gap widens per row
because JSON repeats key names on every record. That is reasoning, not a measurement. Take the real
figures on real output before calling the change done; do not let this paragraph become the
inherited-figure problem T18 was.

**Measured 2026-08-10** on real output: 5 notes written to a scratch project, then
`bm tool search-notes "sprint planning decisions" --json` captured verbatim (3951 bytes) and the
same result set re-rendered as aligned columns. Token counts via tiktoken `cl100k_base` (a proxy —
Claude's tokenizer is not public; the ratios are the finding, not the absolute counts):

| rendering | tokens |
|---|---|
| v1 JSON envelope, 5 records | 1151 |
| same 11 fields as aligned columns | 764 (−34%) |
| realistic v2 columns: permalink, score, title, snippet + count line | 202 (−82%) |

So the "3×" illustration was wrong in both directions: pure serialization overhead is ~1.5×, not
3× — but the real v2 rendering wins ~5.7×, because the envelope also carries fields that are
duplicates (`entity` ≡ `permalink`, `content` ≡ `matched_chunk`) or agent-useless
(`external_id`, `entity_id`, full ISO timestamps). The token argument for v2 is really two
arguments: columns beat braces, and a curated row beats a full record dump. The header line
amortizes across rows while JSON re-pays its keys per record, so the gap widens with result size.

**SHIPPED 2026-08-10.** `docs/OUTPUT_CONTRACT.md` is now version 2: rules, not a serialization.
What was removed, wholesale: every `--json` flag, `--plain`, the TTY-detection/auto-JSON
machinery in `tool.py`, and the `cli_output_style` config field — each verb has exactly one
rendering. What each shape became: lists are aligned columns, identifier first, count line last
(`search-notes`, `recent-activity`, `list-projects`, `project list`/`ls`, `orphans`,
`config list`); single records are labelled `key: value` lines (`write-note`, `edit-note`,
`delete-note`, `config get/set/unset`); grouped reports are plain sections (`project info` lost
its panel/bar-chart dashboard, `status` lost its Panel/Tree, `schema validate/infer/diff` lost
their Rich tables — the schema renderers are shared functions in `cli/commands/schema.py` called
by both `bm schema *` and `bm tool schema-*`, one contract, two entry points). Notices and
affordances trail the payload on stdout; `--quiet` drops them where they exist. All error paths
now exit 1 with one message line on stderr and no payload on stdout (this also moved T11's
`NewerSchemaError` message to stderr, and unified the two strict messages on `strict: N errors`).

Judgment calls: rich rendering was removed rather than kept as a second mode — keeping it would
have been the same "secondary mode" softening the user rejected for `--json`, and its box-drawing
violates rule 1. The read-note byte-exact plain path became the read-note rendering (round-tripping
is now a contract rule). `tests/cli/test_native_command_import_guard.py` swapped its probe from
`--json`+json.loads to the column output; its import assertions are unchanged.

Verified: fast-check 0; unit 3285/2 (3308 − 23; +67/−90 `def test_` reconciled — the deletions are
the mode-precedence/JSON-envelope tests whose subject no longer exists); int 329/3 (330 − 1;
+7/−8 — one JSON-format-contract test folded into a v2 rewrite); doctor green. Live scratch smoke:
`project list`, `search-notes`, `config list`, `orphans`, `status`, `project info` all render v2
(columns, id first, trailing counts), streams clean.

### W21 — the permalink normalization contract is undocumented, and permalinks are this fork's identity — **CLOSED 2026-08-16: recovered from the code into `docs/IDENTITY.md`; the query surface got `docs/METADATA-QUERIES.md`**

**Close block, 2026-08-16.** Docs only, no code changed.

- **Task 1 was already answered** in this entry (2026-08-10): `docs/NOTE-FORMAT.md` does not cover
  normalization. Re-confirmed, and its permalink section was wrong on top of being thin — it said
  the permalink is "derived from its title" when `generate_permalink` derives it from the **file
  path** (`src/basic_memory/utils.py:30`). That line is corrected.

- **Task 2 shipped as `docs/IDENTITY.md`**, 283 lines, every rule cited to `file:line`. It covers
  what identity is and is not; the ASCII derivation in order (unidecode, camelCase split, lowercase,
  `_`→`-`, apostrophes dropped rather than hyphenated, everything outside `[a-z0-9/.-]` to `-`,
  periods and `/` kept); the separate CJK branch (ideographs preserved, fullwidth punctuation
  deleted, hyphens inserted at CJK↔Latin boundaries, so **a permalink is not guaranteed ASCII**);
  hyphen collapse and per-segment strip; the project-slug prefix and its default; explicit
  `permalink:` stored byte-for-byte; the `-1`/`-2` collision suffix in **both** places that does it;
  the DB unique index; advisory-only conflict detection; the set-once rule and `id == permalink`;
  the three write paths with their two modes; governed vs ungoverned move; where wikilinks,
  relation permalinks, and `memory://` bind; and what `bm doctor` checks.

- **Judgment call — a new file, not a `DOMAIN_MODEL.md` section.** The brief left the choice open.
  `DOMAIN_MODEL.md` was a 209-line invariants doc that cites no code and states *meaning*; this is
  character-level *mechanism* with 71 citations that go stale whenever `utils.py` moves. Folding it
  in would double that file and mix two registers and two maintenance cadences. `DOMAIN_MODEL.md`
  and `NOTE-FORMAT.md` get pointers instead, and `DOMAIN_MODEL.md`'s Move section now states the
  governed-move rule it was silent on.

- **Task 3 decided: the query surface does need a doc.** `docs/METADATA-QUERIES.md`, 139 lines.
  W20's contract governs *rendering*, not the query grammar — it is orthogonal and cannot cover
  this. `--help` is worse than thin: `--filter` is described as "JSON metadata filter (advanced)"
  (`src/basic_memory/cli/commands/tool.py:739-742`) and names **zero** operators. The operator set
  lives in one docstring (`src/basic_memory/repository/metadata_filters.py:152-165`). Positive
  control: `grep -n -iE 'contains|\$in|\$between|metadata filter' src/basic_memory/man/bm.1
  src/basic_memory/mcp/resources/ai_assistant_guide.md` returns two hits, both
  `ai_assistant_guide.md` (:97 and :99) and none in the man page — and :97's operator list omits
  `$contains`. That file is also MCP-facing, not the CLI.
  The doc records the grammar, the AND-combination, dates-as-text, the two boolean spellings, and
  three traps: **T21** (`updated_at`/`created_at` read frontmatter and silently return `0 results`
  — still open, documented rather than papered over), bare-list-means-AND vs `$in`, and
  `--permalink` silently becoming a text search when dropped.

- **Nothing was taken from the deleted `docs/character-handling.md`.** It was read once for
  orientation (`git show 411d6251^:docs/character-handling.md`) and every rule was then re-derived
  from the current tree. Its conflict-resolution half survives as §4's advisory-only note, which is
  the part the code still does.

- **Found while doing this, recorded as O9:** `docs/NOTE-FORMAT.md` still documents Picoschema,
  `schema-infer`, and drift detection as live surface, months after the package was stripped.

**Review pass, 2026-08-16.** All 71 `file:line` occurrences in `IDENTITY.md` were re-opened against
the tree. Two were wrong and are fixed: `entity_repository.py:65-112` for
`find_permalink_integrity_issues` (it is at `91-139`; W5's hygiene constants shifted it) and
`:104-111` for the underscore check (it is at `130-138`). Every `metadata_filters.py` citation in
`METADATA-QUERIES.md` had shifted by T21's 64-line insertion and is re-pointed. The derivation
rules in §2 were re-read end to end and are correct as written.

Four claims were corrected rather than re-cited: set-once also fires on a **dropped** field, not
only a changed one; the funnel has **two** entry points (`enforce_vocabulary` and
`apply_vocabulary`), because the sync path calls the second; the governed-move branch **logs** the
violation and persists nothing, which "records" read as the opposite; and the first-index
frontmatter stamp is gated on the default flags. T21's fix has landed in the tree, so
`METADATA-QUERIES.md` §5.1 now states that column names error. **That section depends on T21's
diff surviving verification** — if T21 is reverted, §5.1 and the `metadata_filters.py` line numbers
revert with it.

**Opened 2026-08-07**, by the `.forked/` reconciliation pass, from `.forked/pass4-5-inventory.md`
before its deletion. That file flagged five doc deletions as *"product decisions, not strips"*. Three
are moot; two are live and were recorded nowhere:

- **`docs/character-handling.md`** (240 lines) was the only written spec for permalink
  unicode→ASCII normalization and collision handling.
- **`docs/metadata-search.md`** (280 lines) was the only doc for the frontmatter query surface.

**Why the first one matters here specifically.** T9 makes the permalink this fork's identity — edges
bind to it, and W4 makes it set-once and the strictest member of that list, because rewriting one
silently orphans every relation pointing at the record. Normalization decides what a permalink *is*.
An undocumented normalization contract is therefore an undocumented identity contract, and the
behaviour it governs "silently changes note identity" (the deleted doc's own phrasing).

`docs/NOTE-FORMAT.md` was flagged at deletion time as *maybe* covering this. **Nobody verified it.**
That check is the first task on this entry, and it is cheap.

**Verified 2026-08-10 — it does not.** `grep -n -i "permalink|normaliz|unicode|collision|slug"
docs/NOTE-FORMAT.md` finds permalinks described only as "generated from title" / "a stable
identifier derived from its title"; zero hits for unicode, collision, or slug rules. (Positive
control: the same grep does hit the permalink prose, so the file was searched correctly.) Task 1 is
answered: the contract must be recovered from the code (task 2), not from existing docs.

**What this owes:**

1. Verify whether `docs/NOTE-FORMAT.md` covers unicode normalization and collision rules. If it
   does, close this half.
2. If it does not, recover the contract **from the code** rather than from the deleted file — the
   deleted doc may itself have drifted — and document it next to the identity rules it governs.
3. Decide whether the frontmatter query surface needs a doc, or whether W20's contract plus
   `--help` now covers it. W18 indexed frontmatter into FTS after that doc was deleted, so it was
   stale on arrival either way.

**Recoverable, not lost:** `git show <sha>:docs/character-handling.md` still resolves. Read history
freely; the deletion was deliberate and only the *record* of what it removed was missing.

---

## VERBS PHASE — the record verbs, item by item

The build plan is `.forked/`-adjacent and lives in the campaign scratchpad, not here; this section
is the ledger entry for each item as it lands. The phase ships `bm new`, `bm edit`, `bm done`,
`bm mark`, `bm ls`, `bm show`, `bm path`, and `bm undo` (AGENTS.md's verb list), plus the
mechanisms they are the first callers of.

### V-B — record ids, slugs, and file paths — **SHIPPED 2026-08-17**

`src/basic_memory/vocabulary/ids.py`. Pure functions: `new_record_id()`, `is_record_id()`,
`allocate_record_id(predicate)`, `record_slug(title)`, `type_dir(type)`, `record_file_path()`.
No database, no config, no vocabulary file — the fast CLI path imports it.

**Decisions recorded, both accepted 2026-08-17 by the campaign orchestrator per VERBS_PLAN D1 and
D2; the user may revisit:**

- **D1 — id scheme.** `tnd-` + 8 characters from `abcdefghijklmnopqrstuvwxyz0123456789`, drawn with
  `secrets.choice` (36^8 ≈ 2.8e12). Collision handling is a caller-supplied predicate, retried 5
  times, then a loud `IdAllocationError` — a second collision at that size means the check is
  wrong, not that the draw was unlucky. Never a counter: `tnd-NNNN` needs a per-project allocator
  and two machines on separate branches allocate the same next number.
- **D2 — file layout.** `<type-dir>/<id>--<slug>.md` (schema.md §8), with plural type directories
  `tasks/ guides/ findings/ profiles/ states/` and `inbox/`.

**Two judgment calls taken while building it:**

1. **`record_slug` drops periods where `generate_permalink` keeps them.** A permalink keeps them so
   `version-2.0.0` stays addressable; a file name that keeps them grows a `.0.md` tail that reads
   as a double extension. Nothing resolves through the slug, so the loss is cosmetic.
2. **`type_dir` raises on a type outside the closed six** rather than filing it under `inbox`. The
   unknown-type escape hatch is real (W4), but it is `bm new`'s decision to take **and state**;
   taking it silently here would make the escape hatch invisible, which is the failure W4's
   "agents propose, never enable" rule exists to prevent.

### V-G — `bm ls`, `bm show`, `bm path` — **SHIPPED 2026-08-17**

`src/basic_memory/cli/commands/records.py`, plus one additive query — `list_records()` and
`RecordListRow` in `repository/entity_repository.py`. Read-only, on the fast path: scope resolution
(W5-C), one indexed query, print. Registered in `cli/main.py`; `ls`, `show`, and `path` join
`app.py`'s `skip_init_commands` for the reason `doctor` and `project` are already there — their own
bootstrap calls `ensure_project_registry`, so `initialize_app` would be a second, slower copy.

**Decisions recorded, both accepted 2026-08-17 by the campaign orchestrator per VERBS_PLAN D9 and
D10; the user may revisit:**

- **D9 — `bm path` prints the path alone**: no count line, no notices, no affordances, and so no
  `--quiet`. It exists for `$EDITOR "$(bm path tnd-x)"`, and every one of those lines would land
  inside the command substitution. Documented as a named exception in `docs/OUTPUT_CONTRACT.md`
  rather than left as undeclared drift.
- **D10 — `bm show` keeps the payload byte-exact** and renders derived supersession as a notice
  after it, dropped by `--quiet`. That satisfies both "raw content is byte-exact" and §5's
  "`bm show` displays superseded by tnd-… (date)" without a second copy of the edge.

**Three judgment calls taken while building it:**

1. **A note with no frontmatter `type` is not a record and `bm ls` does not list it.** `bm ls`
   answers "what records are here"; folding in every imported markdown file buries that answer.
   The predicate is the `$.type` mirror the hygiene queries already read.
2. **`--limit` fetches one row more than it prints.** That row is the whole evidence for the
   `more records match` notice, so an honest count costs no second `COUNT` over the same predicate.
3. ~~**The direct-path wiring (`load_records`, `load_record`) lives in `records.py`, not
   `cli/direct.py`.**~~ **Paid 2026-08-17 by T30.** Both moved into `cli/direct.py` as
   `direct_record_listing` and `direct_record`, with the record dataclasses and the two lookup
   errors. `records.py` is now scope, render, and exit shape only.

**Fixed in review (2026-08-17):** `bm show` read its payload with `Path.read_text`, which
translates CRLF to LF and raises `UnicodeDecodeError` on a file that is not valid UTF-8 — the first
breaks "raw content is byte-exact", the second turns rule 6's one stderr line into a traceback. It
now writes `Path.read_bytes()` through `click.echo`, guarded by a regression test carrying both
cases.

~~**Also owed by this item, and deliberately not done here:** `ls`, `show`, and `path` are not yet
in `tests/cli/test_native_command_import_guard.py`'s `NATIVE_COMMANDS`.~~ **Paid 2026-08-17 by
T30.** All three are in `NATIVE_COMMANDS` and run cold and warm against the ban list, which now
also carries `basic_memory.mcp.async_client` and `basic_memory.mcp.clients`. The probe seeds one
record first, because `show` and `path` exit 1 without one and would otherwise prove nothing.

**Also closed 2026-08-17:** the three `except typer.Exit: raise` clauses in `ls`, `show` and `path`
were dead — `typer.Exit` is neither `ValueError` nor `LookupError`, so it already propagated past
the handlers they guarded. A regression test now covers one id seeded into two projects, which is
the `AmbiguousRecord` path `show` and `path` share, plus its positive control (`-p` resolves it).

### V-H — `bm undo` — **SHIPPED 2026-08-17**

`bm undo [--session <id>] [--yes]`, in `cli/commands/history.py` beside the two verbs that read the
same repository, plus four additive helpers in `store/history.py`: `latest_commit()`,
`commits_for_session(id)`, `restore_from_commit(sha)`, and the two private readers behind it.

The verb restores every path the target commit touched to the version its parent held — a path the
commit *added* has no parent version, so restoring it deletes it — writes a **new** commit, and then
reindexes the projects that own those paths. `--session <id>` does the same for every commit
carrying that trailer, newest first, so the store ends on the content it held before the session
began. This is what W3 meant by "`undo --session` is a `git log --grep` away"; the grep is anchored
to a whole trailer line and the id is `re.escape`d, so one session id that is a prefix of another
cannot pull in the wrong commits.

**Decision recorded, accepted 2026-08-17 by the campaign orchestrator per VERBS_PLAN D4; the user
may revisit:** undo is a **restore plus a new commit, never a `reset`** — history is the thing this
subsystem exists to protect, so undoing a change grows it. Dry-run-by-default was rejected (a verb
that does nothing by default is the one nobody trusts); the verb acts, prints the paths it touched,
and requires `--yes` only when more than one commit is involved.

**Five judgment calls taken while building it:**

1. **The reindex is a project index, not a per-file call.** No public per-file entry point
   reconciles a *deletion*, and undoing a note's creation produces exactly that. The project index
   is incremental — change detection compares mtime, size and checksum — so only the files undo
   changed are re-read and re-indexed. It still walks the project directory to find them, so the
   cost scales with the project's file count, not with the size of the undo. Reviewed 2026-08-17
   and accepted: the alternative is a new per-file entry point that also handles deletes, which is
   a bigger change than this verb warrants. `recover_project_materializations` runs first, for the
   reason `bm reindex` already
   documents (`cli/commands/db.py`): the scan reads a missing file as a delete, so a note stuck
   mid-materialization would be destroyed by the scan that follows.
2. **The undo commit carries `Actor: cli` and no `Session:` trailer.** An undo corrects a session's
   work rather than joining it. Stamping the current id would fold the undo into the set that
   `bm undo --session <that id>` walks, so a second run would undo the undo.
3. **The payload prints before the reindex runs.** The restore and its commit are already on disk by
   then, so a caller must see what moved even if indexing fails afterwards — the partial shape
   output contract rule 6 names.
4. **`emit_notices` is called, against the brief's suggestion that it was unnecessary.** `bm undo`
   has no project scope, but neither do `history dirty` and `history commit`, and both already emit
   against `STORE_SCOPE`. The verb opens the database for its reindex anyway, so the notice is free,
   and item J needs no allowlist entry for it.
5. **A restore that changes nothing is still a result.** `commit_paths` returns None when the store
   already held that content; the verb says so as a notice rather than reporting an unexplained
   missing sha.

Registered flat as `bm undo` (AGENTS.md's verb list) while living in `history.py` — VERBS_PLAN §7:
the documented verb list is the contract, the file layout is not. `undo` joins `app.py`'s
`skip_init_commands` for the reason `ls`/`show`/`path` are there: its own bootstrap calls
`ensure_project_registry`.

Tests: `tests/cli/test_undo_command.py` (21) drives the real Typer command against a real store
repository, real project rows, real files and the real project index — nothing stubs git and nothing
stubs indexing. `tests/store/test_history.py` gains 10 for the helpers. Positive controls throughout:
the "gone from the index" assertions are paired with the listing that shows the record present
first, and the anchored-grep test is paired with the full id that does match.

**Two items found in review 2026-08-17. H2 was fixed in this same commit; H3 stays owed to item J.**

**H2 — `bm undo` silently discarded an uncommitted edit to a path it restores — FIXED 2026-08-17.**
`git checkout <parent> -- <path>` overwrites the worktree with no warning, so a human edit made
since the last commit was gone with no record anywhere. That is the one class of change W3-B is
most careful about elsewhere: `commit_paths` refuses to stage what it did not write, and
`dirty_others` exists so an outside edit is reported rather than swept in. Undo reached past both.

Reproduction, against a temp `BASIC_MEMORY_CONFIG_DIR`:

```
# a note is created and updated, both committed by bm
bm history commit notes/tasks/tnd-x--t.md      # sha A, content v1
# ... bm updates it ...                        # sha B, content v2
printf 'hand-edited\n' >> ~/.basic-memory/store/notes/tasks/tnd-x--t.md   # uncommitted
bm undo                                        # before the fix: restored v1, the line was gone
bm history dirty                               # the edit was not there either
```

After the fix that last `bm undo` exits 1 with
`Error: undo would discard uncommitted changes in: notes/tasks/tnd-x--t.md. Record them first with
'bm history commit --all', then re-run.` and touches nothing.

**The fix, as recommended.** `store/history.py` gains `paths_in_commit(sha)` — a read-only sibling
of `restore_from_commit` — and `bm undo` intersects it across every target commit with
`dirty_paths()`. A non-empty intersection is a refusal: one stderr line naming each path and
`bm history commit --all`, exit 1, nothing on stdout, and nothing touched on disk or in the history.
A refusal, not a notice — a notice after the overwrite protects nothing, the same shape correction
item C already took for `check_can_record`.

**Judgment call: the refusal is checked ahead of the `--yes` gate.** A confirmation flag cannot
clear it, so the run would be refused either way; naming the unfixable problem first spares the
caller a two-step dance. Untracked paths count as dirty, because a path a commit added and a human
then deleted and rewrote is untracked now and the restore would still discard it.

Tests: three in `tests/cli/test_undo_command.py` (the refusal preserves the edit and leaves HEAD
where it was; a dirty file *outside* the target set does not block, which is the positive control;
the refusal precedes the `--yes` gate) and one in `tests/store/test_history.py` asserting
`paths_in_commit` leaves the worktree alone.

**H3 — `undo` is not in the import guard's `NATIVE_COMMANDS`.** `ls`, `show` and `path` were added
there; `undo` was not, so nothing pins its fast path. `tests/cli/test_native_command_import_guard.py`
belongs to item J, which is why this was not fixed here. J should add `(["undo", "--quiet"],
"nothing to undo")` — the empty-store case needs no fixture and still exercises the whole import
graph.

---

## OPEN — observed, not diagnosed

### O-picoschema — `picoschema/` is un-stripped upstream surface, now with no design doc — **STRIPPED 2026-08-10 (first commit of the W4 build, as scheduled)**
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
   the store's `vocabulary.yml`, human-edited, enforced in the write path. Building W4 on
   picoschema would leave two
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
validate`/`diff` in the same program — no window with zero drift detection.

**The freeze clause is corrected 2026-08-03.** It read "the subsystem is frozen: no fixes land in
it". That was already false when written: W7's output-contract work landed changes on this surface
in `92d1b6c9` (`schema infer`'s no-pattern answer became a result, not an error) and `e9db95a3`
(`schema validate`/`diff` conformed to `docs/OUTPUT_CONTRACT.md`) — both in
`cli/commands/schema.py` and `mcp/tools/schema.py`, the CLI/tool skin over `picoschema/`. The
package itself is untouched since the fork (`git log -- src/basic_memory/picoschema/` ends at
`6e9f2fcf`, an upstream rename).

**Current rule:** the subsystem is still strip-scheduled with W4, and no behavioural or feature work
lands in it. Output-contract conformance was the one exception admitted, on the grounds that a
command still shipping has to obey the contract every other command obeys for as long as it ships —
a stripped command costs nothing to have conformed, an unconformed live command costs every scripted
consumer. O5's error-shape defect is recorded under O8 as class evidence and dies with the strip.

**STRIPPED 2026-08-10.** Deleted: `src/basic_memory/picoschema/`, `api/v2/routers/schema_router.py`,
`cli/commands/schema.py`, `mcp/tools/schema.py`, `mcp/clients/schema.py`, `schemas/schema.py`, the
three `bm tool schema-*` commands in `tool.py`, all registrations (api/app.py, routers/__init__,
tools/__init__, clients/__init__, cli main + commands/__init__), the test files
(`tests/picoschema/`, `test-int/test_picoschema/`, `test_cli_schema.py`, `test_tool_schema.py`,
`test_client_schema.py`, `test_schema_router.py`, the schema sections of
`test_cli_tool_json_output.py` and `test_tool_contracts.py`), the orphaned
`test-int/fixtures/schema/` corpus, and the `docs/manual-pages.md` "schema is the linter" workflow
(now points at `bm orphans` + the coming doctor checks).

**Deletion-pass lesson, new instance of the T17 class:** the content grep
(`picoschema|schema_validate|schema_router|…`) missed `tests/api/v2/test_schema_router.py` — 22
tests that exercise the router through HTTP paths and never name a stripped symbol. A **filename**
sweep (`find tests test-int -name "*schema*"`) caught it, plus the orphaned fixture corpus. Grep
the test tree by content AND by filename.

Verified: fast-check 0; unit 3091/2 (3113 − 22 for the missed router file; the 3309 → 3113 drop
is the 196 deleted `def test_` lines, zero parametrize changes); int 284/3 (329 − 45), re-run
after the fixture deletion with a positive control on the grep. W20's schema-render work dies with
the strip as priced in.


*(O1 was diagnosed and closed on 2026-07-26 — it was a measurement artifact, not a defect. See
**R-O1** under RESOLVED, and the four requirements it produced under **W1**. The id is retired
rather than reused, so O2–O6 keep their numbers.)*

### O2 — `bm orphans` reports both endpoints of a frontmatter-encoded edge as orphans — **RESOLVED 2026-07-31: `orphans` is right, frontmatter edges don't exist; no code change owed**
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

**Status 2026-08-03: closed, and the consequence still binds.** Nothing is owed in `orphans`. Note
what W18 (`22008333`) did and did not change: frontmatter text is now reachable by FTS (see O3), but
a frontmatter wikilink still produces **no relation row**, so it is still not an edge. "Edges are
body relations" is a live schema rule, not a historical one.

### O3 — frontmatter values appear not to reach full-text search — **FIXED VIA W18 `22008333`**
> **Read this line before the diagnosis below.** As of `22008333`, **frontmatter keys and values ARE
> reachable by plain FTS** — `SearchService._frontmatter_search_terms` (`search_service.py:682`,
> called at `:769`) folds them into the entity's `content_stems`. Everything under
> "Confirmed 2026-07-31" is the pre-fix diagnosis, kept because it is the evidence W18 was built on
> and the fixture the regression test inverts. Do not read it as current behaviour.

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
today - N`) and the derived headline that keeps the statusline script alive (the most recently
updated `state` record's title). Both are unbuildable if the field cannot be filtered and sorted.

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

### O5 — `schema-infer` returns an error object where an empty result belongs — **SHIPPED 2026-07-31: no-pattern is now a non-error result (see O8)**
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
   plus reindex silently duplicates the note** (both `synced` afterwards). **`bm doctor` needs a
   dedupe check** for exactly this shape (this read `bm gc` until 2026-08-07; see W2), and W3's git
   history is the real safety net.

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
- Drain call sites: `api/app.py`, `mcp/server.py`, `cli/runner.py` (was
  `cli/commands/command_utils.py` before T30)
  (`drain_background_tasks` stays — vector sync and relation resolution remain scheduled).
- `deps/services.py` provider wiring; `config_models.py` `materialization_workers` field.
- Tests of the pool/drain in `tests/index/test_note_content_materialization.py`,
  `tests/cli/test_runner.py`, `tests/mcp/test_server_lifespan_branches.py`, plus any
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

### O8 — CLI tool failures exit 0, and `--json` mode emits non-JSON or error-shaped prose — **CLOSED 2026-07-31: all instances fixed; contract in `docs/OUTPUT_CONTRACT.md` (W7)**
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

**Re-verified 2026-07-31** (W7's opening move, per the phase-1 decision "shared exit path, W7's
first commit"):

- *Instance 1 fixed already*: `search-notes '**' --json` → empty stdout, error on stderr,
  **exit 1**. `tool.py` grew per-command error detection (`result.get("error")` → stderr +
  `typer.Exit(1)`) in the same unreconciled 07-26 fix batch as T1/T2/T5/B1.
- *Instance 2 fixed already*: non-unique `edit-note find_replace` → stderr, **exit 1**, file
  untouched.
- *Instance 3 SHIPPED now*: `schema infer --json` no-pattern returns a non-error shape
  (`{"note_type", "notes_analyzed", "threshold", "suggested_schema": null, "reason"}`), exit 0 —
  "no pattern" is a result. Genuine infer errors now exit 1 with stderr diagnostics (JSON error
  object stays on stdout in `--json` mode so the stream stays parseable). This also closes O5's
  error-shape defect.
- *Validate/diff empties SHIPPED 2026-07-31 with the W7 contract*: all four guard shapes
  ("No schemas defined", "No notes found of type", "No schema found for type" × validate/diff)
  are legitimate empties — now report-shaped results with `reason`, exit 0. `{"error"}` is
  reserved for genuine failures → stderr + exit 1 (both `bm schema *` and `bm tool schema-*`).
  Contract and rationale in `docs/OUTPUT_CONTRACT.md`; decision record in W7.
- *Envelope sentinel FIXED 2026-07-31 (W7 envelope commit)*: `SearchResponse.total` is now
  `int | None` — an exact count under FTS, `null`/absent when semantic modes skip the count.
  `total_is_exact` deleted everywhere (schema, router, client shim, multi-project merge, CLI
  renderer); the multi-project merge reports `total: null` when any project failed or reported
  unknown. Guarded by `tests/mcp/test_search_total_exactness.py` (FTS→int, vector/hybrid→absent)
  and the router test asserting `total: null` under semantic mode. **O8 is CLOSED.**
  Original diagnosis kept below for the record.
- *Envelope miscount DIAGNOSED 2026-07-31 — by design, but the sentinel is a trap.* Reproduced:
  text query with semantic deps present → `{"total": 0, "total_is_exact": false}` beside 2 real
  results; queryless metadata mode → `{"total": 1, "total_is_exact": true}`. Cause: the v2
  search router only counts under FTS retrieval (`search_router.py` — plain text queries default
  to HYBRID when semantic is enabled); semantic modes skip the count to avoid a second retrieval
  and leave `total` at the sentinel `0`, honestly flagged by `total_is_exact: false` (the CLI
  renderer already checks the flag, `tool.py:118`). Not a counting bug — but `0` as
  "unknown" reads as a miscount to any caller that misses the flag. W7's envelope should carry
  `total: null` (or omit it) when the count is unknown; fold into the contract decision.

---

### O9 — `docs/NOTE-FORMAT.md` documents a Picoschema surface that no longer exists — **CLOSED 2026-08-17: every stale mention deleted**
**Opened 2026-08-16**, found by the W21 docs pass.

The `picoschema` package was stripped 2026-08-10 (recorded as **O-picoschema**), but the docs that
described it were never touched. `docs/NOTE-FORMAT.md` still gives Picoschema syntax,
schema-to-note mapping, schema attachment, validation modes, validation output, `schema-infer`, and
drift detection as live features — 210 of its 542 lines, plus two of its three worked examples.

```
$ git ls-files src/basic_memory/picoschema
                                        # 0 files
$ git grep -l -i picoschema -- src
src/basic_memory/man/bm.1
$ git grep -l -i picoschema -- docs
docs/NOTE-FORMAT.md
docs/manual-pages.md
```

**Amended 2026-08-16 by the W21 review.** The entry as first written recorded `git grep -l
picoschema -- src` as returning "no output". That is false: it returns `src/basic_memory/man/bm.1`.
The package directory is gone (`git ls-files` returns 0 files), but the man page still documents the
verb. The corrected commands are above.

Positive control: the `docs/` grep returns hits, so the search reaches both trees.
`docs/manual-pages.md:140-141` is the *correct* mention — it names the strip
and points at O-picoschema. `docs/NOTE-FORMAT.md` is the stale one: `## Schemas` at line 237 runs to
`## Complete Examples` at 447, and two of the three worked examples below that are schema examples.

**Three smaller instances of the same class, in `src/basic_memory/man/bm.1`:**

- `bm.1:52-54` documents a `bm schema` command — "List, validate, infer, and drift-check Picoschema
  note-type contracts." No such command exists: `src/basic_memory/cli/commands/` has no `schema.py`.
  Found 2026-08-16 by the W21 review; O9 missed it because its `src` grep was recorded wrongly.
- `bm.1:30` says the `bm tool *` commands "emit JSON". False since **W20** removed `--json`
  (2026-08-10); `docs/OUTPUT_CONTRACT.md` v2 forbids it.
- `bm.1:101-102` says `~/.basic-memory/config.json` holds "Projects, default project". False since
  **B2** (2026-08-03) made the database the sole owner of the registry.

`AGENTS.md`'s directory-structure list also still names `/picoschema`.

**Why it matters:** an agent reading `NOTE-FORMAT.md` will write `$schema` frontmatter and call
verbs that do not exist, and get no error that explains why. This is the doc-side of the O8 class —
a confident wrong answer.

**Fix:** delete the schema half of `NOTE-FORMAT.md` and its two schema examples, delete the
`bm schema` entry from `bm.1` and correct its two other lines, and drop `/picoschema` from
`AGENTS.md`. Deliberately **not** done in the W21 pass:
it is a ~210-line deletion in a file that pass only needed one paragraph of, and bundling it would
have hidden the permalink work inside a docs strip.

**CLOSED 2026-08-17.** All four instances fixed.

- `docs/NOTE-FORMAT.md`: 542 → 255 lines. Deleted the whole `## Schemas` section (Picoschema
  syntax, schema-to-note mapping, attachment, schema notes, validation modes and output,
  `bm schema infer`, `bm schema diff`), the `schema` frontmatter row, and the two schema worked
  examples. `## Complete Examples` became `## Complete Example` — one example is left, so the
  sub-heading `### Simple Note (No Schema)` went too. Kept untouched: frontmatter, observations,
  relations, permalinks, and the `IDENTITY.md` pointer, all of which describe live behaviour.
- `src/basic_memory/man/bm.1`: deleted the `bm schema` entry and retitled its section
  `Projects and schemas:` → `Projects:`; replaced "These emit JSON" with the one-rendering-per-verb
  rule and an explicit "no `--json`" (W20); rewrote the `config.json` line to say the database owns
  the project registry (B2).
- `AGENTS.md`: dropped the `/picoschema` line from the directory-structure list.

Control, run after the edits — `git grep -in picoschema` hits only `GAPS.md` history and
`docs/manual-pages.md:140-141`, which O9 already identified as the *correct* mention (it names the
strip and points at O-picoschema). Nothing user-facing presents Picoschema as live. The grep is its
own positive control: it still returns hits, so it reaches both trees.

**Judgment call taken.** O9 named only the `config.json` half of the `.SH FILES` block, but the next
line called `memory.db` "derived; safe to rebuild with bm reindex". After B2 that is false and it
would have contradicted the corrected line directly above it, so it now reads "SQLite index and
project registry". This is the same B2 staleness O9 names, one line lower.

### O10 — one malformed transcript line makes `bm mine` print nothing for a whole directory — **CLOSED 2026-08-16: damage is counted, named, and survivable**
**Opened 2026-08-16** by the W1 review, which re-ran W1's own corpus survey against the live
projects tree.

W1 requirement 4 (from R-O1) says a `json.loads` failure is a hard error, and W1's close block
states the correct rate is zero. **It is not zero.** Over the whole tree — 1,919 transcripts,
464,253 non-empty lines, `*.jsonl` allowlist applied — 12 lines fail to parse. They are not
truncation and not a live-write race: every one sits mid-file in a transcript last written hours or
days earlier, ends with a newline, and still fails when re-read.

```
failures: 12
  <session>.jsonl        line 148/346  last=False ends_nl=True still_bad=True age=7576min
  agent-<id>.jsonl       line 199/327  last=False ends_nl=True still_bad=True age=7583min
  ...
  ordinals near the failure point: [110, 116, 123, 34, 112, 97, 114, 101]   # → 'nt{"pare'
```

The signature is the same in 10 of the 12: a record is cut short and the **next record starts on
the same physical line** (`…{"parentUuid":`), so Claude Code occasionally writes two records without
the newline between them. Two more fail at a different offset with the same cause.

They cluster: 3 of 61 project directories hold all 12 (1 of 51,027 lines, 9 of 89,966, 2 of 22,206).
Inside those three, `bm mine` exits 1 with a message and **nothing on stdout** — the directory is
unmineable, not degraded. This repo's own project directory is clean today, which is why the build
never hit it.

**Reproduction** (read-only; run from a checkout, over your own projects tree):

```python
import json
from pathlib import Path
from basic_memory.mine.locate import is_transcript

bad = total = 0
for f in (Path.home() / ".claude" / "projects").rglob("*.jsonl"):
    if not is_transcript(f):
        continue
    for n, raw in enumerate(f.open(encoding="utf-8"), 1):
        if not raw.strip():
            continue
        total += 1
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            bad += 1
print(bad, "of", total)
```

*Positive control:* the same loop without `is_transcript` re-produces R-O1's Markdown sidecar
failures on top of these, so the loop can fail and the allowlist is doing its job.

**The decision this needs, which the reviewer did not take.** R-O1 rule 4's words are "Count them,
report them, exit nonzero" — a counted, named, nonzero-exit skip satisfies all three, and would keep
the other 463,000 lines readable. The shipped behaviour is stronger: it aborts the run and prints no
payload, which `docs/OUTPUT_CONTRACT.md` rule 6 also requires of the error path. Weakening it is a
change to a recorded decision *and* to the contract's error rule, so it belongs to the user, not to
a review pass. Recommended: keep exit 1, still print the payload, and name each bad line in a notice
— with rule 6 amended to say that a partial-corpus failure is the one case where a payload and a
nonzero exit coexist.

**DECIDED and SHIPPED 2026-08-16 (campaign orchestrator, not the user — revisit if the contract amendment is unwanted).** The recommendation was taken: `bm mine` prints the
payload, names every unreadable line, and still exits 1. R-O1 requirement 4 is unchanged in
substance — *count them, report them, exit nonzero* — and all three still happen. What went is
aborting the directory, which requirement 4 never asked for.

**This entry's own diagnosis was half wrong, and the fix it implied recovers nothing.** The text
above calls the signature "two records on one physical line" and reads as clean glue,
`…}{"parentUuid":`. Checked before writing any code:

```
raw.count('}{')            -> 0 on all 12 lines
raw_decode loop from col 1 -> 0 of 12 lines recover any record
                              every one fails "Expecting ',' delimiter" INSIDE the first record
window at the failure      -> ..."cwd":"/{"parentUuid":"45149172-...
```

The first record is **torn** — cut off mid-value — and the next record is concatenated onto the
wound. Its missing bytes are not in the file, so no parser recovers them. A `raw_decode` loop
starting at column 1, which is the obvious reading of "parse multi-record lines", therefore returns
nothing at all on every real case.

**What does work:** on a decode failure, walk forward to the next `{"` whose decode both succeeds
**and consumes the rest of the line**, and which yields a dict carrying a `type` key. The
end-of-line test is the load-bearing half — a `{"` nested inside the torn prefix decodes perfectly
well as a content block and leaves a tail behind it, while the surviving record never does. That
recovers the intact record on **12 of 12**, reports the torn prefix as lost, and still handles a
genuinely clean `}{` glue if one ever appears. Bounded at 64 candidate restarts per line so a
1.2 MB torn line cannot make the scan quadratic.

**Shipped shape.** `read_turns` yields `Turn | BadLine` and never raises; `scan` returns
`ScanReport(hits, damage)`; the verb prints payload, count, notices and affordances on stdout, then
one `Error:` line per bad line plus a total on **stderr**, and exits 1. Stderr rather than a stdout
notice for two reasons: the contract's Streams section already assigns diagnostics there, and
`--quiet` drops stdout notices — a flag that can hide a corruption report is a flag that will.

**`docs/OUTPUT_CONTRACT.md` is now version 2.1**: rule 6 gained a partial-corpus clause. A verb that
read most of its input and lost a named part of it prints what it got, names what it lost on stderr,
and exits 1. The exit code, not the absence of a payload, is what says the run failed.

**Verified.** The shipped reader over the live tree: `files=1920 turns=464905 damage=12
files_with_damage=10`, against 3 project directories that previously exited 1 with an empty stdout.
Tests: a bad line still yields the surrounding turns plus one `BadLine`; a torn line reproduced from
the real shape recovers only the survivor; two cleanly glued records both parse and share the
physical line number; a bare JSON list is damage, not a turn; the CLI prints the payload and exits 1,
and `--quiet` cannot suppress it; positive control — a clean corpus reports empty damage and exits 0.

**One consequence, deliberate:** `#L<line>` addresses the physical line, so two turns can share a ref
when one line holds two records. Renumbering would make a reference disagree with what an editor
shows, which is worse.

**Two limits of the recovery, found by the re-review and left standing.** Both were reproduced
against the shipped code; neither occurs in the measured tree, and the damaged line is reported
either way, so no loss is silent at line granularity:

1. Recovery accepts the record that runs to the **end of the line**, so a line holding a tear
   followed by *two* intact records keeps the last and drops the middle one, with the line's single
   damage report covering both. A looser test would mistake a nested content block for a record,
   which is the worse trade.
2. The 64-restart cap is a time bound, not a recovery guarantee: a torn prefix containing more than
   64 `{"` sequences abandons the rest of the line. Reproduced with a synthetic 1.2 MB line; the
   line is reported as damaged and the scan stays fast (that line parses in under 10 ms).

## USAGE — found by using the verbs on real content

**Opened 2026-08-17.** The first migration of this repo's own local tracking files into a governed
bm project wrote 104 records in one pass (69 `finding`, 23 `task`, 10 `guide`, 1 `state`,
1 `inbox`). Every entry below came out of that pass. **U7-U15 came out of the second pass**,
the same day: the same corpus re-migrated with true dates once U1's six date flags existed, again
104 records with the same type split. Nothing in U1-U6 recurred. They are usage defects, not build defects —
each one was hit by an agent following the documented workflow, and none of them shows up in a
test.

Three things worked without a complaint and are worth recording as the baseline: every verb ran in
0.3–0.6 s user CPU, `--source` accepts free text and round-trips verbatim, and the error messages
are exact and actionable (`only a task carries a status; '<id>' is a finding`; `'finished' is not a
status this project declares. Allowed values: open, doing, blocked, done, dropped.`; `no record
'<id>' in scope`).

**Gate 2026-08-18 — U7 through U15 are all closed below**, in one pass, verified once centrally
rather than per entry: `just fast-check` clean; `just test-unit-sqlite` 3727 passed (3680 baseline
+ 47 this pass); `just test-int-sqlite` 284 passed; `just doctor` pass. Live store after a
`bm reindex`: 104 entities, the `vocabulary.yml` row retired, `bm status` 104 files / 0 unindexed.
Each close block below states what changed and where; none of them restates the gate. **U16-U20
were filed by the same pass** and are open.

### U1 — `bm new` cannot state a date it did not invent, and stamps `date-source: inline` to say so — **FIXED 2026-08-17**

**This is the entry that matters.** `bm new` has no flag for `opened`, `event-date`, `date-source`,
`date-confidence` or `review-by`. It sets the date to today and writes `date-source: inline`
unconditionally.

```
$ bm new --help | grep -E "^│ +--"
│ --body        -b      TEXT  The note's body. Use '-' to read it from stdin;  │
│ --source              TEXT  Where the content came from, e.g.                │
│ --area        -a      TEXT  One of the areas this project declares.          │
│ --supersedes          TEXT  The finding this one replaces, by id.            │
│ --project     -p      TEXT  Project to write to. Defaults to .bm.yml.        │
│ --quiet                     Hide the notices and the next-step hints.        │
│ --help                      Show this message and exit.                      │
```

Migrating a decision the source dates 2026-08-05:

```
$ bm new finding "Store history: git add -A per mutation was reversed; commit only touched paths" \
    --source ".forked/decisions.md#L189-L312" -b "Recorded 2026-08-05 in GAPS W3. ..."
$ bm show tnd-gqntyk6p | sed -n '1,10p'
---
title: 'Store history: git add -A per mutation was reversed; commit only touched paths'
type: finding
permalink: tnd-gqntyk6p
id: tnd-gqntyk6p
source: .forked/decisions.md#L189-L312
event-date: '2026-08-17'
date-source: inline
date-confidence: day
review-by: '2027-08-17'
```

`event-date` is today. The content is twelve days old.

**Why `date-source: inline` makes it worse rather than better.** `bm types` defines the field as
"How you know the date", and the design docs define `inline` as the HIGHEST fidelity rung — a date
written in the source text itself. bm writes the highest-confidence value for a date it invented.
The other four rungs the vocabulary was designed around (`transcript`, `git`, `mtime`, `inferred`)
cannot be expressed at all, and `inferred` is the one that exists specifically so a guess is
visible rather than laundered.

The design that this contradicts is not incidental: the migration workflow makes "never invent a
date — if the source does not carry one, `date-source: inferred`" a hard rule for every extracting
agent, and calls a fabricated date attached to a real record "the single worst failure mode
available here". It is also what a hygiene check is supposed to flag. Neither is reachable from the
CLI.

**Workaround used for all 104 records, and it is a bad one:** the true date is written into the
first line of every body as prose. That is not queryable, not checkable, and not what a validator
reads.

**Why it matters:** this is the pilot for migrating every repo's tracking files. Every record any
of those passes writes will claim it was learned on the day it was migrated, with the frontmatter
asserting the date came from the source text. Retrofitting a date after the fact is not possible
either — `bm edit` sets a title, a body, and declared profile fields, and the date fields are
set-once by design.

**Fix, in the order the workflow needs it:** `bm new` takes `--date <YYYY-MM-DD>` and
`--date-source <inline|transcript|git|mtime|inferred>` (defaulting to today/`inferred`, which is
the honest default, not `inline`), plus `--date-ref` where the vocabulary requires one and
`--review-by` for a finding or guide whose review is not twelve months from the migration run.

**Closed 2026-08-17.** `bm new` takes six date flags, and the date it invents no longer claims the
source text carried it.

- **Flags:** `--opened` (task), `--event-date` (finding), `--review-by` (finding, guide),
  `--date-source`, `--date-confidence`, `--date-ref`. Each date field is named rather than a single
  `--date`, because the field name *is* the type check — `--opened` on a finding is refused, naming
  both the field's owner and this record's type.
- **A stated date requires `--date-source`.** That is the flag's whole purpose; accepting a stated
  date without it would stamp the default rung on a date the writer did know the origin of, which
  reopens this entry through the other door.
- **The default rung is now `inferred`, not `inline`.** *Judgment call:* the ladder has no rung for
  "read off the clock", so per this entry's own Fix line the lowest truthful rung stands. The cost
  is deliberate and was previously the argument against it — `bm doctor`'s hygiene group reports
  every inferred date — but that pile is now the correct signal, because the flags to state a real
  date exist. `date-confidence` stays `day`: confidence is how precise a date is, not how it was
  learned.
- **`--date-ref` was added beyond the flag list above** because without it the `git` and
  `transcript` rungs — the two most useful ones for a migration — are unreachable. The verb
  requires it for those two and refuses it for the other three, so the rule holds in an ungoverned
  project too, where no checker runs.
- **The two ladders had two homes and one of them was prose.** `checker.py` held them as private
  tuples and `glossary.py` spelled the same values out in `FIELD_MEANINGS`. Both now live in
  `vocabulary/glossary.py` as `DATE_SOURCES` / `DATE_CONFIDENCES` / `REF_BEARING_SOURCES`; the
  checker aliases them, the glossary's prose interpolates them, and `bm new` reads them for both
  validation and `--help`. `glossary.py` is stdlib-only, so the fast CLI path pays nothing.

Files: `src/basic_memory/cli/commands/new.py`, `src/basic_memory/vocabulary/glossary.py`,
`src/basic_memory/vocabulary/checker.py`, `README.md` (a new *Dates on a record* subsection),
`tests/cli/test_new_command.py`.

Tests added to `tests/cli/test_new_command.py` (11):
`test_new_writes_a_stated_event_date_and_its_source`,
`test_new_writes_a_stated_opened_date_on_a_task`,
`test_new_writes_a_stated_review_by_instead_of_the_default`,
`test_new_writes_a_date_ref_on_a_ref_bearing_rung`,
`test_new_refuses_a_stated_date_with_no_date_source`,
`test_new_refuses_a_date_flag_the_type_does_not_carry`,
`test_new_refuses_a_review_by_on_a_type_that_has_none`,
`test_new_refuses_provenance_on_a_type_with_no_date_field`,
`test_new_refuses_a_malformed_date`,
`test_new_refuses_a_date_source_off_the_ladder`,
`test_new_refuses_a_ref_bearing_rung_with_no_ref_and_a_ref_without_one`.

None removed. `test_new_writes_the_type_date_with_its_provenance` changed its expectation from
`date-source: inline` to `inferred` — that assertion was the defect, written down.

### U2 — record output has no trailing newline, so the body runs into the notice — **FIXED 2026-08-17**

`bm show` and the note file on disk both end without a newline, so the last word of the body butts
against whatever bm prints next.

```
$ bm show tnd-mmf8je9o | tail -2 | cat -A
`just` is the entry point for every verification recipe in this repo, so a machine without it cannot run the gates. Whether that makes it a workstation-level dependency belonging in a tracked Brewfile is unresolved.1 unfiled record in the inbox M-bM-^@M-^T run 'bm doctor --only hygiene'$
bm edit <id> change it M-BM-7 bm path <id> print its file path$
```

`unresolved.1 unfiled record` is one token to any reader, human or agent. The same is true on disk:

```
$ tail -c 40 "$(bm path tnd-b17jghub)" | cat -A
 Relations$
- supersedes [[tnd-on8v0ha8]]
```

— no `$` at the end, so the file has no final newline. That also makes every record file fail
POSIX line-orientation, which matters for the store's own git history: a later edit that does add a
newline shows the last line as changed.

**Fix:** terminate the body with a newline on write, and again before notices and affordances are
printed.

**Closed 2026-08-17.** The culprit was `file_utils.dump_frontmatter`, which returned
`f"---\n{yaml}---\n\n{post.content}"` with no terminator, over content that
`markdown/utils.schema_to_markdown` had already stripped via `remove_frontmatter`.
`cli/record_notes.record_markdown` was terminating correctly all along and was never the cause.

- **`file_utils.ensure_trailing_newline`** is the new one-line invariant: exactly one `\n`, and
  empty content stays empty. Applied at both `dump_frontmatter` returns, and at the top of
  `services/note_preparation._build_prepared_write` — the single point every accepted create,
  replace and edit funnels through *while the bytes, the checksum and the parsed entity are still
  one string*. An edit needs the second site: its content comes back from `apply_edit_operation`
  without passing through `dump_frontmatter`.
- **`index/local_moves.merged_frontmatter_markdown`** is the one other writer that builds its bytes
  by hand rather than through `dump_frontmatter`, and it calls `body.strip()` first. Terminated
  there too, or a move that rewrote a permalink would undo the fix on the file it touched.
- **`dump_frontmatter`'s no-metadata branch is deliberately left alone**: a post with no metadata is
  not a note, no note-writing path reaches it, and `_build_prepared_write` terminates anything that
  does become a file. Terminating it would only change a documented pass-through.
- **`bm show`** prints one newline after the payload, and only when the file lacks one, so
  byte-exactness (contract: raw content is byte-exact) still holds for a round trip.
- **A reindex does not see every old file as changed.** Checked, because that was the risk: every
  "has this file changed" decision hashes the bytes on disk and compares them to the stored
  `entity.checksum` (`indexing/change_planning.py`, `indexing/file_index_planning.py`,
  `index/local_dependencies.py`). Nothing re-renders a database row to compare against a stored
  checksum, and `bm status` does no checksum comparison at all — it tests path membership. An
  untouched newline-less file still hashes to its stored value; it gains the newline only when
  something rewrites it.

Files: `src/basic_memory/file_utils.py`, `src/basic_memory/services/note_preparation.py`,
`src/basic_memory/index/local_moves.py`, `src/basic_memory/cli/commands/records.py`.

Tests added (3): `test_new_writes_a_file_that_ends_with_exactly_one_newline`
(`tests/cli/test_new_command.py`), `test_edit_leaves_the_file_ending_in_exactly_one_newline`
(`tests/cli/test_record_write_commands.py`),
`test_show_separates_a_body_with_no_final_newline_from_the_notice`
(`tests/cli/test_record_read_commands.py`). None removed.

Seven existing tests changed their expectation, found by a grep sweep for exact note-content
equality (the sweep's own positive control was
`tests/services/test_entity_service_write_result.py`, which already expected the newline):

- `tests/services/test_entity_service.py` — `test_create_entity_file_exists`,
  `test_update_entity_content`, `test_update_with_content` (twice). Four stale literals: three of
  them built the expectation with `dedent(...).strip()`, which is what removed the newline.
- `tests/services/test_entity_service_prepare.py` —
  `test_prepare_edit_entity_content_prepend_without_frontmatter_uses_simple_prepend`, one stale
  literal.
- `tests/services/test_entity_service_prepare.py` —
  `..._metadata_only_edit_preserves_body_exactly` and `..._metadata_only_edit_skips_append_newline`.
  **These two encoded the opposite invariant on purpose** (PR #1090 review: "a missing final newline
  … must round-trip byte-exact") and are a real conflict rather than a stale literal. U2 wins:
  adding the file's terminator is not a reflow — nothing inside the body moves — and the promise
  those tests exist to keep is that a frontmatter-only edit does not touch the body's *own* shape.
  Both now assert the body up to the terminator, plus a new assertion each that the hard-break
  spaces and the blank runs inside the body are still exact and that no second newline was
  appended. Their docstrings say which half is which.

### U3 — a superseded finding is indistinguishable from a live one — **FIXED 2026-08-17**

`--supersedes` works, and it writes the relation into the SUCCESSOR:

```
$ tail -2 "$(bm path tnd-b17jghub)"
## Relations
- supersedes [[tnd-on8v0ha8]]
```

Nothing is written to the superseded record, and nothing surfaces it:

```
$ bm ls | grep "R1 original"
tnd-on8v0ha8  finding  -     R1 original — Never store what you can re-derive
```

Same shape, same blank status column, as any live finding. To learn that a finding is dead you have
to read the body of every other finding in the project looking for a relation that names it.

This is not a corner case for a migration: this repo's own decision ledger keeps a whole section of
reversals precisely because the abandoned position is worth preserving, and the pass wrote eight
such pairs. Sixteen records, eight of them dead, none of them marked. `bm types` says a finding
"is replaced by a successor written with `bm new --supersedes`" — the replacement is real, the
replacedness is invisible.

**Fix:** either give `bm ls` a `superseded` marker in the status column derived from the inbound
relation, or hide superseded findings behind a flag. The relation is already in the graph, so this
is a read-side change.

**Closed 2026-08-17.** `bm ls` prints `superseded` in the status column for any record some other
record supersedes.

- **The marker, not a trailing `← <successor-id>`.** *Judgment call, three reasons.* The status
  column is where a reader already asks "is this row still live", and a superseded record is not.
  It needs no successor id, so the query stays one correlated `EXISTS` — a join would multiply a
  record with two successors into two rows and `--limit` would then cut in the wrong place. And
  `docs/OUTPUT_CONTRACT.md` rule 1 fixes the *column order* per verb; a value in an existing column
  adds no column, where a variable-width suffix on the title would make the last field two fields.
- **One query, not N+1:** `RecordListRow.superseded` comes from a correlated `EXISTS` on the inbound
  `supersedes` relation inside `list_records`, so a listing costs the same whether or not anything
  in it is superseded. It is deliberately not a filter — `--status superseded` asks for records
  whose frontmatter says so, and no record's frontmatter ever does.
- **A record carrying both a status and a successor** takes the marker. Unreachable in a governed
  project (only a finding may be superseded, and a finding may not carry a status), and where it is
  reachable "not live" is the more important of the two facts. `bm show` prints both.
- **`bm show`'s direction was already right** and needed no change: `cli/direct._supersessions`
  reads `entity.incoming_relations`, so the notice lands on the record that was replaced, not on the
  successor that replaced it. That is the naive-implementation trap this entry could have walked
  into, so it is now asserted in both directions rather than left to inspection.
- **Side effect:** `"supersedes"` was spelled out in four modules and this needed a fifth. It moved
  to `vocabulary/glossary.py` as `SUPERSEDES_RELATION`, read from there by `checker.py`,
  `cli/record_notes.py`, `cli/direct.py` and `repository/entity_repository.py`.

Files: `src/basic_memory/repository/entity_repository.py`, `src/basic_memory/cli/direct.py`,
`src/basic_memory/cli/commands/records.py`, `src/basic_memory/vocabulary/glossary.py`,
`src/basic_memory/vocabulary/checker.py`, `src/basic_memory/cli/record_notes.py`, `README.md`.

Tests added to `tests/cli/test_record_read_commands.py` (3):
`test_ls_marks_a_superseded_record_in_the_status_column`,
`test_ls_leaves_an_unsuperseded_record_unmarked`,
`test_show_notices_supersession_on_the_replaced_record_not_the_successor`. None removed.

### U4 — `bm brief`'s section headings report the row count, not the real count — **FIXED 2026-08-17**

With 23 open tasks in the project:

```
$ bm brief | grep -E '^## '
## Open tasks (5)
## Current state (1)

$ bm ls | awk '$2=="task" && $3=="open"' | wc -l
23
```

The brief shows five rows and labels the section `(5)`. Nothing says the list is truncated. An
agent reading the brief at session start — which is the entire purpose of the command — is told
this project has five open tasks.

The output contract's own rule for v2 is that a count is the real count, and that an unknown count
is ABSENT rather than a sentinel. A count that silently means "rows I chose to print" is worse than
either, because it is indistinguishable from a true count.

**Positive control** that the section is genuinely capped rather than the other 18 being filtered
out for some legitimate reason: all 23 carry `status: open`, none carries `not-before`, and
`bm ls` lists every one of them.

**Fix:** print the real count in the heading and say the list is capped — `## Open tasks (23, showing 5)`
— or drop the parenthetical entirely and let `bm ls` own counting.

**Closed 2026-08-17**, taking the first option. A heading now reads `## Open tasks (23, showing 5)`
when `MAX_ROWS` cut the list and `## Open tasks (5)` when it did not, so the number in the
parenthetical is always the real count. *Judgment call:* the form above is this entry's own, chosen
over a `(showing N of M)` paraphrase because it leads with the true count, which is the thing rule 3
is about.

- `base()` in `cli/commands/brief.py` no longer applies `.limit(MAX_ROWS)`. Each row-section now
  runs one `COUNT` over the same predicate with the ordering cleared, for `Section.total`, then the
  capped `SELECT` for the rows. Two indexed queries per section, three sections at most — counting
  in Python instead would make a session-start hook's cost grow with the corpus, which is what
  `MAX_ROWS` exists to prevent.
- The count-only `inbox` section already counted honestly and is untouched.

**Left open, and filed below as U6:** `bm brief --query`'s heading and its `N results` tail both
count hits *returned*, capped per project, rather than total FTS matches. That needs a real search
count, which is a different change from this one.

Files: `src/basic_memory/cli/commands/brief.py`.

Tests added to `tests/cli/test_brief.py` (4): `test_a_capped_section_carries_the_real_total`,
`test_an_uncapped_section_totals_exactly_its_rows`,
`test_the_total_counts_only_what_the_rule_matches`,
`test_render_states_the_real_count_and_says_the_list_is_capped`. None removed.

### U5 — `bm doctor` flags a deliberate `inbox` record with a demand no verb can satisfy — **FIXED 2026-08-17**

```
$ bm new inbox "Not migrated item by item — the six archived design documents" --source ".forked/archive/"
$ bm doctor
integrity  project 'basic-memory'
  No issues

hygiene  project 'basic-memory'
  inbox/tnd-ybb8oq8h--not-migrated-item-by-item-the-six-archived-design-documents.md  inbox  proposes no type
  1 issue
```

"Proposes no type" describes the record correctly and asks for something the CLI cannot produce. A
proposing inbox record is only ever created as a side effect: `bm new <undeclared-type> …` files the
record as `inbox` and records the rejected type. Ask for `inbox` on purpose — which is what the
type is documented for, "use it when you cannot tell which type fits" — and there is no way to
attach a proposal, and no way to add one afterwards.

So the escape hatch the vocabulary provides is permanently one hygiene issue, and the count never
reaches zero for a corpus that used it as intended. The migration workflow makes doctor's hygiene
output the acceptance gate for a migration; a gate that cannot be closed is not a gate.

**Fix:** either accept a bare `inbox` as clean and reserve the flag for records that DO carry a
rejected type (rename the check to something like "unpromoted proposal"), or let `bm new inbox` and
`bm edit` take the proposal explicitly.

**Closed 2026-08-17**, and by neither option as written — both were wrong for the same reason.

The row itself is right: the W5-B notice counts *every* inbox record as unfiled and points the
reader at `bm doctor --only hygiene`, so a doctor that showed nothing for a plain inbox record would
contradict the notice that sent them there. Accepting a bare `inbox` as clean would have made the
two surfaces disagree, which is worse than an awkward message. Letting `bm new inbox` take a
proposal is the other direction: a record filed as `inbox` *on purpose* has no type it wants to
become, so the field would be there to satisfy a check rather than to record anything.

So the row stays and the demand changes. A record carrying `proposed-type` still reports
`proposes '<type>'`; one that carries none now reports
`unfiled — file it with 'bm new <type>' or leave it`. That is satisfiable, including by deciding to
leave it, which for a genuinely unclassifiable note is a real answer rather than a deferral.

The W5-B count is unchanged and still counts a plain inbox record, which is the half of this that
was already correct.

Files: `src/basic_memory/cli/commands/doctor.py` (the render line and the decision comment).

Tests added (2): `test_doctor_asks_a_plain_inbox_record_for_something_it_can_do`
(`tests/cli/test_doctor_command.py`),
`test_the_inbox_count_includes_a_record_that_proposes_nothing`
(`tests/cli/test_notices.py` — locking the half that must not move). None removed.
`test_doctor_hygiene_section_lists_every_check` changed one expected line from `proposes no type` to
the new message; that line was the defect, written down.

### U6 — `bm brief --query` counts the hits it printed, not the hits there are — **OPEN**

Found while closing U4, which fixed the same defect in the standing sections. The search path still
has it: `search_pointers` asks each in-scope project's FTS index for `MAX_ROWS` hits, keeps the best
`MAX_ROWS` across all of them, and then both the section heading and the closing `N results` line
report `len(rows)`.

So a query matching forty notes prints `5 results` and says nothing about the other thirty-five,
which is exactly what U4 called indistinguishable from a true count. It is a smaller wound than U4's
— a search is a question the reader just asked, so they know they may be seeing a sample — but the
number is still wrong rather than absent.

**Fix:** either count the matches (an unlimited `COUNT` over the same FTS predicate, per project,
summed) and print `(40, showing 5)` the way the standing sections now do, or drop the number and say
`more results available` as a notice, which is what `docs/OUTPUT_CONTRACT.md` rule 3 prescribes for a
count that is not known. The second is cheaper and the contract already names it; the first is
better and costs one query per project.

### U7 — an empty `bm brief` is still zero bytes, which W20 rule 5 forbids — **FIXED 2026-08-18**

W8's close block discharged the *broken* half of "an empty brief and a broken brief are the same
silence" with `--verbose`, and left the *empty* half where it was, noting it "interacts with W20
rule 5, which makes an empty result a **stated** result rather than silence." Nothing then stated
it.

```
$ bm project add scratchpilot --governed
Project 'scratchpilot' added successfully
$ bm brief ; echo "exit=$?" ; bm brief | wc -c
exit=0
0
$ bm ls
0 records
bm show <id> read the full entry · bm new record something worth finding again
```

`bm ls` obeys the rule — an empty result prints `0 records`. `bm brief` prints nothing at all, so a
new project, a project whose records are all closed, and a brief that resolved a scope with no
projects in it are one output. The same thing happens on a project that *has* records but none of
the two types brief renders:

```
$ bm brief -p scratchpilot          # 1 finding in the project, no tasks, no state
1 note file has uncommitted changes — run 'bm history dirty'
```

The payload is empty and only the trailing notice prints, which reads as though the notice *is* the
answer.

**Fix:** print the standing sections with a zero count, or one stated line — the rule is that an
empty result is a result. Roughly the shape `bm ls` already uses.

**Closed 2026-08-18**, taking the second option. `bm brief` states an empty result:
`nothing open in 'scratchpilot'` when a project is pinned, `nothing open in any project` when the
scope is the whole registry. The line is payload, so `--quiet` keeps it and the corpus notice still
follows it — the trailing notice can no longer read as though it were the answer.

- **It names the scope**, for the reason `render()` heads a filled brief with the project: an empty
  brief over one project and an empty brief over the whole registry are different answers.
- **No affordance.** *Judgment call.* Contract rule 4 makes affordances optional, and a "where to
  write" hint is exactly the padding brief's own constraint 2 exists to keep out of a session-start
  context window. `bm ls`'s affordance shape was deliberately not copied.
- **The broken path stays silent.** `render()` is never reached when `gather` raises, so the new
  line cannot claim "nothing open" about a read that failed. `--verbose` keeps its stderr line,
  which is now the only thing that says where the scope came from.
- `render()` still returns `""` for an empty brief — the fence and the "treat as data" preamble are
  overhead around a line carrying no data.

Files: `src/basic_memory/cli/commands/brief.py`, `README.md`.

Tests added to `tests/cli/test_brief.py` (2):
`test_brief_states_an_empty_result_instead_of_printing_nothing`,
`test_brief_empty_result_line_is_absent_when_the_read_broke` (the positive control).
`test_brief_verbose_distinguishes_an_empty_corpus` now asserts the stdout line as well as the
stderr one. None removed.

### U8 — `bm status` reports bm's own `headline.md` as an unindexed file, permanently — **FIXED 2026-08-18**

W9 has every write derive `<store>/<id>/headline.md` for the statusline. It is bm's own output, it
is not a record, and it is never indexed — so `bm status` reports it as a file that needs
reindexing, on a project where nothing is wrong.

```
$ bm status --project basic-memory
project: basic-memory
total files: 106
unindexed files: 1
1 file not indexed — invisible to search and read until reindexed
Run 'basic-memory reindex' to index them.
```

The corpus is 104 records. The 106 files are those plus `vocabulary.yml` plus `headline.md`, and the
one unindexed file is `headline.md` (confirmed against the `entity` table: 105 rows, no
`headline%` row among them, and every record accounted for).

This never clears, because the next write rewrites the file. The advice is also wrong in the other
direction: a reindex that obeyed it would index bm's own derived output as note content, and W10's
exclusion mechanism — which already covers `_archive/` — is the place that should have covered it.

**Fix:** exclude the derived headline from the indexing path the way `_archive/` is excluded, so the
count is honest and the advice is not self-defeating.

**Closed 2026-08-18, together with U9** — one exclusion covers both files, and the fix turned out to
have two halves because the first one exposed the second.

*Exclusion.* `ignore_utils.py` gained
`DERIVED_FILE_IGNORE_PATTERNS = {"/headline.md", "/vocabulary.yml"}`, unioned into
`load_gitignore_patterns()` ahead of the global `.bmignore`, the project `.bmignore` and the project
`.gitignore`. W10 put every indexing consumer behind that one function, so the full scan
(`index/local_project.py`), the watcher (`index/watch_service.py`) and the single-file index
endpoint (`api/v2/routers/knowledge_router.py`) all pick it up with no further wiring, and no user
file can switch it off — indexing bm's own output is never correct. The patterns are root-relative
(a leading `/`, which `should_ignore_path` already honours), so a record named `headline.md` in a
subdirectory still indexes; that half is the positive control. `bm status` needed no change of its
own: `unindexed files` is `ProjectIndexObservation`'s arithmetic over the scan's output, so the
honest count follows the exclusion.

*Retirement.* The first live check found the existing `vocabulary.yml` row still in the `entity`
table after a full reindex, which the first close block had claimed was impossible. Delete
*planning* was correct — `indexing/change_planning.py` plans
`deleted_files = all_db_paths - storage_paths - moved_old_paths`. The veto was at apply time:
`indexing/project_index_maintenance.py` re-confirms every planned delete through
`ProjectIndexDeletePathVerifier`, and the local implementation confirmed a path only by proving it
*absent* — a guard added so a note written between snapshot and apply is never destroyed. An
excluded file is present forever, so its row was planned, refused, and re-planned on every run.
`LocalProjectIndexDeletePathVerifier` now also confirms a path the project no longer indexes,
applying the scan's own two-step filter (`local_relative_path_is_filtered` plus
`should_ignore_path`) against ignore patterns it loads exactly the way the scan does. The race the
guard exists for is unaffected: a recreated markdown note is neither absent nor ignored.

No migration. One reindex now retires these rows — and the same fix retires rows for any file a
newly added `.bmignore` or `.gitignore` pattern excludes, which had the identical permanent-row bug
before U8 and U9 existed.

Files: `src/basic_memory/ignore_utils.py`, `src/basic_memory/index/local_project.py`.

Tests added (6): `test_scan_excludes_bm_derived_and_control_files`,
`test_scan_keeps_a_record_named_headline_below_the_root`,
`test_status_counts_exclude_bm_derived_and_control_files`
(`tests/index/test_local_project_scan_parity.py`);
`test_derived_files_are_ignored_at_the_project_root_only` (`tests/cli/test_ignore_utils.py`);
`test_local_project_index_delete_path_verifier_confirms_newly_ignored_paths`,
`test_reindex_retires_a_row_whose_path_the_scan_no_longer_indexes`
(`tests/index/test_local_project_index.py`, the second an end-to-end reindex over a seeded stale row
with a real note as the positive control). None removed. Three tests in
`tests/cli/test_ignore_utils.py` that asserted `patterns == DEFAULT_IGNORE_PATTERNS` now compare
against a `BASELINE_PATTERNS` union — what they measure is "nothing custom was picked up", and the
baseline is now both halves.

**Seen and not fixed:** `importers/project_zip_import.py` filters with `DEFAULT_IGNORE_PATTERNS`
directly rather than through `load_gitignore_patterns`, so it does not see the derived-file
patterns. Harmless — an imported archive has no bm store files at its root — but it is the one
indexing-adjacent path that bypasses the choke point.

### U9 — a governed project's `vocabulary.yml` is indexed as an entity — **FIXED 2026-08-18**

The control file that *defines* the corpus is in the corpus.

```
$ python3 -c "import sqlite3;c=sqlite3.connect('file:\$BASIC_MEMORY_CONFIG_DIR/memory.db?mode=ro',uri=True);\
print(list(c.execute(\"select count(*) from entity\"))[0]);\
print([r[0] for r in c.execute(\"select file_path from entity where file_path not like '%/%'\")])"
(105,)
['vocabulary.yml']
```

104 records were written; the `entity` table holds 105 rows and the extra one is `vocabulary.yml`.
It is not a note, it has no record id, `bm ls` does not list it — and it is still an indexed entity,
so it is reachable by search and it counts anywhere entities are counted.

Same root as U8 and worth fixing in the same pass: the store holds three kinds of file — records,
bm's own derived output, and the project's control file — and only the first kind is note content.

**Fix:** exclude `vocabulary.yml` on the indexing path; it is one pattern beside U8's.

**Closed 2026-08-18 with U8** — one `DERIVED_FILE_IGNORE_PATTERNS` set covers both files, and this
entry's own row is the one that proved the exclusion alone was not enough: it survived a full
reindex until the delete-path verifier learned to confirm a newly ignored path. See U8's close block
above for the mechanism, the files and the tests. Verified live: after `bm reindex`, 104 entities
and no `vocabulary.yml` row.

### U10 — a record deleted on disk keeps its row, and `bm doctor` calls that clean — **FIXED 2026-08-18**

The acceptance gate for a migration is the four commands in *Migrating a repo's tracking files*. One
of them cannot see a record whose file is gone, which is the exact failure mode a hand-edit or an
interrupted prune produces.

```
$ rm "$(bm path tnd-pdem7knd -p scratchpilot)"
$ bm ls -p scratchpilot
tnd-dm9d3vcc  finding  -  Delete me B
tnd-pdem7knd  finding  -  Delete me A
2 records
$ bm doctor -p scratchpilot
integrity  project 'scratchpilot'
  No issues

hygiene  project 'scratchpilot'
  No issues
$ bm show tnd-pdem7knd -p scratchpilot ; echo "exit=$?"
Error: tnd-pdem7knd is indexed but its file is missing: <store>/<id>/findings/tnd-pdem7knd--delete-me-a.md
exit=1
$ bm path tnd-pdem7knd -p scratchpilot ; echo "exit=$?"
<store>/<id>/findings/tnd-pdem7knd--delete-me-a.md
exit=0
```

So one verb knows exactly what is wrong and says so, and the checker that exists to find exactly
this reports no issues in either group. `bm status` does not cover it either — the only warning it
produced during this run was U8's, about an unrelated file. A `bm reindex -p <project>` clears the
row (`project index: 2 observed, 1 indexed, 1 deleted`), so the repair exists and nothing points at
it.

Note the `bm show` message is *good*: it names the condition and the path, and it exits 1. The gap
is that nothing aggregates it.

**Fix:** an integrity check that reads each record's `file_path` and reports the missing ones, with
`bm reindex` named as the repair. It is the same shape as the existing integrity checks and it is
the check that makes the four-command gate mean something.

**Also:** `bm path` returns a path for a file that does not exist, at exit 0. Either it should
refuse the way `bm show` does, or that is deliberate (you may want the path in order to restore the
file) — but it is currently silent about a condition `bm show` treats as an error.

**Closed 2026-08-18.** `EntityRepository.list_indexed_files` returns every row's `file_path` and
`permalink`; `_missing_files` in `cli/direct.py` stats each against the project's path and returns
the rows with nothing behind them. They land in `ProjectIntegrityReport.missing_files` and count
toward `issue_count`, so `bm doctor` prints `  <file>  missing-file  permalink=<id>` per row,
followed by one `  repair: bm reindex -p '<project>'` line for the group. The check is in the
`integrity` group, so `--only hygiene` does not pay for the stats.

- **The stat loop lives above the repository**, in `cli/direct.py`. *Judgment call:* that layer
  answers questions about the database, not about the disk.
- **One repair line per group, not per row** — it is the same command for 1 or 100 files.
- **`bm path` keeps its exit 0 and its bare-path stdout.** This entry called that deliberate, and it
  is what you want in order to restore the file. It now also writes
  `note: <id> is indexed but its file is missing — restore it, or run 'bm reindex -p <project>'`
  to **stderr**, which a `$(bm path …)` command substitution does not capture.

Verified against a real corpus: after `rm "$(bm path tnd-… -p scratchpilot)"`,
`bm doctor -p scratchpilot --only integrity` reports the row, the repair, and `1 issue`.

**Interaction with U8/U9, worth knowing:** an excluded file is now unindexed rather than deleted, so
a `vocabulary.yml` someone removes by hand can no longer show up here — but any record file removed
by hand does.

Files: `src/basic_memory/repository/entity_repository.py`, `src/basic_memory/cli/direct.py`,
`src/basic_memory/cli/commands/doctor.py`, `src/basic_memory/cli/commands/records.py`.

Tests added (7): `tests/repository/test_entity_repository_missing_files.py` (3, real files, with an
all-present control), two render tests in `tests/cli/test_doctor_command.py`, and two in
`tests/cli/test_record_read_commands.py` (including the silent-when-healthy control). None removed.

**Left open, filed below as U19:** `bm doctor` exits 0 even when it reports issues. Pre-existing,
seen while closing this entry, and out of its scope.

### U11 — `bm new` prints the absolute store path on every write, `--quiet` included — **FIXED 2026-08-18**

```
$ bm new finding "Delete me A" -p scratchpilot --source "x.md#L1" \
    --event-date 2026-08-01 --date-source inline -b "body A" --quiet
tnd-pdem7knd  finding  <store>/<id>/findings/tnd-pdem7knd--delete-me-a.md
1 record
```

Over a 104-record migration that is 104 lines of a path the reader cannot use and did not choose —
the store path is store-derived by design, and `bm path <id>` exists for the case where someone
wants it. It is also the single longest field on the line, so it pushes the id and the type apart in
a wrapped terminal, and it is the one field guaranteed to differ between machines, which makes any
captured output non-portable.

`--quiet` drops the notices and the affordance line, per the contract, and this line is payload, so
it stays. That is consistent — the question is whether the path belongs in the payload at all.

**Fix:** print the store-relative path (`findings/tnd-pdem7knd--delete-me-a.md`), which is what the
history subject line already uses, or drop the column and leave `bm path` as the way to get it.

**Closed 2026-08-18**, taking the first option. `bm new` prints the project-relative path —
`findings/tnd-…--delete-me-a.md` — which is the form the history subject line already uses. The
absolute path was the longest field on the payload line, the one field guaranteed to differ between
machines (so captured output was non-portable), and one the reader never chose, the store home being
store-derived by design. `bm path <id>` remains the way to get the absolute path, so nothing is
lost. The change is one expression: `path=result.file_path` replaces
`f"{project.path}/{result.file_path}"`, `result.file_path` having always been project-relative.

**`bm edit` prints the relative path too**, folded into this entry after the first pass left it out:
every argument above applies to it, and `bm edit` was the other verb printing a path on every write.
`WriteOutcome.detail` now carries a project-relative path from `edit_record`, and a one-line comment
says so — `bm mark` and `bm done` put a status in the same field.

Files: `src/basic_memory/cli/commands/new.py`, `src/basic_memory/cli/commands/record_write.py`.

Tests: `tests/cli/test_new_command.py`'s `written_file()` joins the project home back on, and
`test_new_prints_a_store_relative_path` asserts the payload shape and the absence of the store home
from stdout. In `tests/cli/test_record_write_commands.py`, `payload_path()` keeps its meaning — an
absolute path, which is what `bm path` prints — and gains a docstring saying so; a new
`written_path(project, output)` joins the project home onto a write verb's payload, and the twelve
`bm edit` call sites moved to it while the seven `bm path` call sites stayed. Added (2):
`test_new_prints_a_store_relative_path`, `test_edit_prints_a_store_relative_path`. None removed.
`test_edit_replaces_the_title_and_keeps_the_file_path` got stronger for free — it now also asserts
the relative form resolves to the same absolute file `bm path` names.

### U12 — a bare `bm reindex` dies on a raw traceback if any project's directory is missing — **FIXED 2026-08-18**

```
$ bm reindex
...
│   193 │   logger.warning(                                                    │
│   194 │   │   "Recording unreadable directory during project scan",          │
│ in walk:369                                                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
FileNotFoundError: [Errno 2] No such file or directory: '<a registered project path that no longer exists>'
```

No project is reindexed, including the healthy ones, and the output is a stack trace rather than a
message. `bm reindex -p <project>` works fine, so the recovery exists once you know which project is
at fault — the traceback does name the path, which is the only reason it is recoverable at all.

A registered project whose directory has moved or been deleted is not an exotic state: it is
precisely what W6's *projects that still live outside the store* produces, and `bm status` hits the
same wall (`Error checking status: [Errno 2] No such file or directory: ...`).

**Fix:** the same degradation W8 already applies to a broken vocabulary — skip the project, name it,
reindex the rest, exit non-zero. `bm brief` sets the precedent: one broken project must not silence
or kill a whole-registry verb.

**Closed 2026-08-18**, as the fix describes. `_reindex` in `cli/commands/db.py` checks
`Path(proj.path).is_dir()` before the per-project body. A project whose directory has gone prints
`  skipped directory is missing: <path>` under its own heading, is collected, and the run
continues; the command ends with `Reindex incomplete: N project(s) skipped — <names>` and exits 1,
and `Reindex complete!` only prints when nothing was skipped. The same guard is in
`_reindex_projects`, which `bm reset --reindex` uses over the same walk.

`bm status` hit the same wall and gets the same shape: `run_status` returns a
`StatusScan(reports, missing)` — the shape `direct_revalidate_vocabulary`'s `RevalidationScan`
already established — so a missing project's status call is never made and the walk that raised is
never reached. The verb prints the healthy sections to stdout, then one
`Error: project '<name>' has no directory at <path>` per missing project on stderr, then exits 1.

- **Both are `stat` pre-checks, not caught exceptions.** *Judgment call:* fail-fast stays intact, so
  a genuine I/O failure still propagates. A directory deleted mid-run would still traceback —
  accepted, and `bm status`'s `except Exception` catch-all is still what would catch it.

Verified with two registered projects, one of whose directories was deleted: reindex and status each
reported the healthy project, named the broken one, and exited 1 (checked unpiped).

Files: `src/basic_memory/cli/commands/db.py`, `src/basic_memory/cli/commands/status.py`.

Tests added (4): 2 in `tests/cli/test_status_scope.py`, 2 in `tests/cli/test_db_reindex.py`
(including the "healthy registry still says complete and exits 0" control). **Test churn this
forced, worth knowing about:** several existing status and reindex tests used fabricated paths
(`/tmp/foo`, `/tmp/scratch`, `/tmp/alpha`) that do not exist, which the new guard would have turned
into silent skip tests; they now use `tempfile.gettempdir()` behind a named constant with a comment
naming this entry. Same for `_MOCK_PROJECT_ITEM` in `tests/cli/test_json_output.py`, a bare
`MagicMock` with no `path` set, whose `Path(item.path)` was a MagicMock repr — five tests there
turned into skip tests before it was given a real temp dir. Files touched by that churn:
`tests/cli/test_status_scope.py`, `tests/cli/test_status_wait_timeout.py`,
`tests/cli/test_db_reindex.py`, `tests/cli/test_json_output.py`. None removed.

### U13 — `bm ls` prints `1 records` — **FIXED 2026-08-18**

```
$ bm ls -p scratchpilot
tnd-dm9d3vcc  finding  -  Delete me B
1 records
```

`bm new` gets it right (`1 record`) so the two count renderers disagree. Cosmetic, one line, filed
because W19 spent a whole item on making this surface read like English.

**Closed 2026-08-18.** `bm ls` pluralises its count line, so one record reads `1 record` the way
`bm new` already printed it. There is no shared count renderer to reuse — `bm new`, `bm edit`,
`bm mark` and `bm done` each hardcode the singular string, because each writes exactly one record —
so this is one expression in `cli/commands/records.py` rather than a new abstraction.

Four existing expectations of `1 records` were the defect written down, and were updated. One of
them was a live cross-test hazard: `tests/cli/test_native_command_import_guard.py` matched the `ls`
tail on `" records"` against a corpus it seeds with exactly one record; it now matches `"1 record"`.

Files: `src/basic_memory/cli/commands/records.py`.

Tests added (1): `test_ls_count_line_reads_as_english_at_every_count`
(`tests/cli/test_record_read_commands.py`), which locks 0, 1 and 3 so the plural cannot be lost to
the fix. Expectations updated in `tests/cli/test_record_read_commands.py` (3),
`tests/cli/test_undo_command.py` (1) and `tests/cli/test_native_command_import_guard.py` (1). None
removed.

### U14 — a migration cannot connect a record to the record it came from — **FIXED 2026-08-18**

`bm new` writes exactly one relation, `--supersedes`, and only between findings. Nothing else can be
linked at write time, and `bm edit` moves a title and a body, so nothing can be linked afterwards
either.

Over this repository's own corpus that meant 104 records and 8 relations. The links that were
available to write and could not be:

- The task *make `bm brief --query` report the number of matches* came out of a specific finding;
  they are two records with no edge.
- Ten guides point at files by name in prose. A guide about the schema and the findings extracted
  from that schema share nothing a graph query can follow.
- Twenty-three tasks and sixty-nine findings from the same source file are connected only by the
  `source:` string, which is free text.

The graph is the substrate this fork chose over an issue tracker — a wikilink in the body resolves,
so the capability exists at the file level and is simply unreachable from the write verbs. The
result is a flat corpus that keeps its provenance in prose.

**Fix:** a general `--relates-to <id>` (or `--rel <type>:<id>`, since the relation type is what
carries the meaning) on `bm new` and on `bm edit`, refusing an id no record in the project holds —
the check `--supersedes` already performs.

**Closed 2026-08-18**, taking `--rel <type>:<id>`, repeatable, on both `bm new` and `bm edit`. A
record can now be connected to the record it came out of, at write time or afterwards. The target id
must name a record the project holds — the check `--supersedes` already made, now made for every
edge — and the edge is written as `- <type> [[<id>]]` under `## Relations`, which is what
`--supersedes` already wrote and what the markdown parser indexes.

- **The relation type is closed vocabulary.** A new `relations:` key in `vocabulary.yml`, a plain
  string list like `types:` and `statuses:`, defaulting to `relates_to`, `derived_from`,
  `supersedes`. No per-relation mapping, no allowed source/target types, no cardinality. An
  **absent** key means those three, not "no edges" — every vocabulary file written before this item
  omits it, and reading omission as a refusal would turn `--rel` off for every project that exists.
  A **present** key replaces the list outright, the way `types:` does.
- **Enforcement is at the verb, not in `vocabulary/checker.py`.** *The judgment call worth
  explaining.* The checker already receives a record's parsed relation types
  (`accepted_note_relation_types` → `check_frontmatter(relation_types=…)`), so a
  `relation-not-declared` rule there looked like the "same style as types and statuses" answer. It
  is wrong: an inline `[[…]]` anywhere in a body parses to a relation of type `links_to`
  (`markdown/plugins.py`), so a checker rule over that list would make every note whose prose links
  to another note an error and block the write — the D8 `note`-type breakage again, one layer over.
  The closed vocabulary governs **what a verb may write**, not how prose may link, so the check sits
  beside `bm mark`'s status check in `cli/commands/record_write.py`, which is already how a *flag*
  value is measured against the vocabulary. The consequence is filed below as **U20**.
- **`--supersedes` is sugar over the general path** and keeps its own error text. Both flags build
  one list of edges and one rendering path, so they cannot drift; each edge remembers which flag
  produced it, so `--supersedes tnd-xxx` still fails with `--supersedes names 'tnd-xxx'`. It is
  **exempt from the project's `relations:` list** — supersession is schema vocabulary
  (`glossary.SUPERSEDES_RELATION`), already governed by the checker's `supersedes-not-on-type` rule,
  so a project that narrowed its list has not thereby disabled the flag. `--rel supersedes:<id>`
  *is* governed by the list, because it is a `--rel`.
- **`bm edit --rel` appends and never replaces.** Existing edges are facts somebody recorded; an
  edit that states one new link says nothing about the others. A bullet already present is skipped,
  so re-running the same command is a re-run and not a duplicate edge. A record with no
  `## Relations` section gets one at the end of the body; one that already has the heading gets the
  bullets joined to it, after the last bullet of that section rather than at the end of the file — a
  hand-edited record can carry prose after its relations. The write goes through `stack.update_note`
  like every other edit, so the history records it. `--rel` alone satisfies the "nothing to change"
  guard and suppresses `$EDITOR`, for the same reason `--title` and `--set` do.
- **`bm types` prints relations with a sentence each**, unlike `statuses` and `areas`, which are bare
  lists. `derived_from` versus `relates_to` is precisely the choice an agent gets wrong with no
  guidance, and W19's rule is that the write path and `bm types` teach one vocabulary.

Files: `src/basic_memory/vocabulary/model.py` (`DEFAULT_RELATIONS`, `Vocabulary.relations`,
`_ALLOWED_KEYS`, the parser, `vocabulary_document`), `src/basic_memory/vocabulary/glossary.py`
(`RELATION_MEANINGS`, `relation_meaning()`), `src/basic_memory/vocabulary/__init__.py`,
`src/basic_memory/cli/record_notes.py` (`Relation`, `relation_line`, `parse_relations`,
`check_relation_types`, `append_relations`; `record_markdown(relations=…)` replaces
`record_markdown(supersedes=…)`), `src/basic_memory/cli/commands/new.py`,
`src/basic_memory/cli/commands/record_write.py`, `src/basic_memory/cli/commands/types.py`,
`README.md`.

Tests added (13 `def test_`, 16 collected): `tests/cli/test_new_command.py` (5 defs, 8 collected —
one parametrized), `tests/cli/test_record_write_commands.py` (3),
`tests/cli/test_types_command.py` (2), `tests/vocabulary/test_vocabulary_model.py` (3). None
removed. One assertion trap found and recorded in the helper's docstring: on resolution a relation's
`to_name` is deliberately rewritten to the target's **title**
(`indexing/relation_resolution.py`, `indexing/batch_indexer.py`), so identity lives on
`to_id`/`to_entity`. `indexed_relations` now reads `to_entity.permalink`, which is byte-for-byte the
id that was typed.

**Three things left open by this entry**, all filed below: `bm edit --body` wipes an existing
`## Relations` section (**U17**), `bm edit --rel` cannot touch a task or a finding (**U18**), and
`bm doctor` does not judge relation types on records MCP or a hand edit wrote (**U20**). A fourth is
noted and not filed: `--rel` accepts an edge whose target is any type, because nothing in the schema
declares source/target rules to enforce.

### U15 — with no registered project, `bm new` bootstraps a default project at `~/basic-memory` and writes there — **FIXED 2026-08-18**

**Found 2026-08-17** by the re-migration agent, probing with `BASIC_MEMORY_CONFIG_DIR` pointed at
an empty temp dir and cwd outside any marker. `bm new` did not refuse — it created a default
project rooted at `~/basic-memory` (upstream's bootstrap default), wrote the record there, and
committed nothing, because that path is outside the store. The directory did not exist before the
probe; the agent removed it.

That is upstream's "first run makes you a project" behaviour surviving under a verb whose contract
is store-homed projects (D3). Two things are wrong at once: a *write* verb silently created a
project, and it homed it outside the store, so the D3 notice about "no history for this project"
was the only sign. Reproduction:

```
$ export BASIC_MEMORY_CONFIG_DIR=$(mktemp -d); cd /tmp
$ bm new task --title probe --date-source inferred --body x
tnd-…  task  probe
<home>/basic-memory/tasks/tnd-…--probe.md
note: project 'main' lives outside the store, so this write has no history
$ ls -d ~/basic-memory
<home>/basic-memory
```

**Fix:** a native verb with no resolvable project must fail with "no project — run `bm project
add <name> --governed`", not bootstrap one. Where the bootstrap lives (config load) and whether the
MCP path still wants it are the questions; the verbs must not.

**Closed 2026-08-18** — and **the entry's own reproduction no longer reproduced**, which is the part
worth reading. `bm new` already refused in-tree, because `write_project_name` in
`cli/record_notes.py` predates this pass; the entry was written against the installed release build,
which does not carry it. The live defect was the *read and maintenance* verbs: `bm ls`, `bm doctor`,
`bm types`, `bm status` and `bm reindex` each created `<home>/basic-memory` and a project `main`,
and each exited 0.

`ensure_project_registry` takes `bootstrap: bool = True` (`services/initialization.py`); the
fresh-install branch returns early when it is False, while the legacy `config.json` import still runs
— importing projects a user declared is not invention. Every native-verb entry passes
`bootstrap=False`: `cli/direct.py` (six call sites), `index/local_write_stack.py`,
`cli/commands/history.py`, and `cli/commands/db.py`'s two reindex paths. `bm types` was the last verb
still bootstrapping, through `ensure_initialization`; it is now in `cli/app.py`'s
`skip_init_commands`, where AGENTS.md's native-verb list already implied it belonged, which also
removes a duplicate init it was paying for.

- **Reads report empty, writes refuse.** *Judgment call.* `bm ls` → `0 records`, `bm doctor` → `No
  projects to check.`, `bm types` → `no projects registered`, `bm project list` → `0 projects`, all
  exit 0. Only a write is an error, because only a write needs a home. The message is the constant
  `NO_PROJECT_MESSAGE` — `no project — run 'bm project add <name> --governed'` — raised by
  `write_project_name`, which `new`, `edit`, `mark` and `done` all call before opening a database.
- **A `bootstrap` kwarg, not a deleted branch**, so the local-ASGI seam (`mcp/async_client.py`) is
  untouched and the MCP server still bootstraps. That is why `bm project add` on a fresh install
  still leaves a `main` project behind — the remaining half, filed below as **U16**.
- **One knock-on:** `bm project list` on a genuinely empty registry died with
  `Error listing projects: No default project configured` at exit 1, because
  `ProjectService.get_default_project_name` raises rather than returning `None`. `fetch_project_list`
  now skips that lookup when there are no projects; the field was already Optional. The service
  contract was left alone because the API reads it too.
- **A second knock-on:** `tests/cli/test_native_command_import_guard.py`'s probe bootstrapped its
  fixture by invoking `project list`, which no longer creates a registry. It now calls
  `ensure_project_registry` directly, which imports nothing on the ban list.

Files: `src/basic_memory/services/initialization.py`, `src/basic_memory/cli/direct.py`,
`src/basic_memory/index/local_write_stack.py`, `src/basic_memory/cli/commands/history.py`,
`src/basic_memory/cli/commands/db.py`, `src/basic_memory/cli/app.py`,
`src/basic_memory/cli/record_notes.py`, `src/basic_memory/cli/commands/project.py`.

Tests added (3): `tests/cli/test_no_project_bootstrap.py` — subprocess, temp HOME,
`BASIC_MEMORY_HOME` deliberately unset, two of them parametrized over four commands each, plus a
positive control that `ensure_project_registry` at its default *does* still create the directory.
`tests/cli/test_native_command_import_guard.py` changed as described above. None removed.

### U16 — `bm project add` on a fresh install still bootstraps a `main` project at `<home>/basic-memory` — **FIXED 2026-08-18**

**Found 2026-08-18** while closing U15, and it is the remaining half of it. U15 stopped every native
verb from bootstrapping, by giving `ensure_project_registry` a `bootstrap=False` and passing it from
the native entry points. `bm project add` is not a native verb: it is client-routed through the
in-process ASGI app, so its bootstrap is the one in `mcp/async_client.py` (~line 200), which the
local MCP server shares. That path was deliberately left alone — the MCP server genuinely does want
a registry to exist before it serves — so the first `bm project add` on a fresh install still creates
a project `main` rooted at `<home>/basic-memory`, outside the store, alongside the project the user
asked for.

The wound is smaller than U15's, because nothing is *written* to that project: it is a stray
registry row and a stray empty directory, not a record filed somewhere invisible. But it is the same
upstream "first run makes you a project" behaviour, surviving under a command whose contract is
store-homed projects (D3), and it means a clean install cannot reach a state of exactly one project.

**Fix:** decide what the ASGI seam should do when the registry is empty and the caller is
`bm project add` — most likely thread the same `bootstrap=False` through, since the command is about
to create a project itself and needs no default one. Whether the local MCP server still wants the
bootstrap is the separable question; if it does, the seam needs two callers rather than one policy.

**CLOSED 2026-08-18 — `be1fc834`.** The seam in `mcp/async_client.py` serves only client-routed
CLI commands: a local ASGITransport does not run FastAPI lifespan, and the MCP server bootstraps in
its own FastMCP lifespan (`mcp/server.py`), as the API server does in `api/app.py`. So the seam
passes `bootstrap=False` and bootstrap survives only in the two server lifespans. `bm project add
<name> --governed` on a fresh install leaves exactly the project asked for, and the service makes it
the default because it is the only one. Test: `tests/cli/test_no_project_bootstrap.py::
test_project_add_leaves_exactly_the_project_asked_for` (temp HOME + empty config dir).

### U17 — `bm edit --body` wipes an existing `## Relations` section — **FIXED 2026-08-18**

**Found 2026-08-18** while closing U14, and pre-existing — U14 only made it visible, by giving the
relations a way to get there other than `--supersedes`.

`bm edit --body` replaces the body wholesale, and the relations live in the body as
`- <type> [[<id>]]` bullets under `## Relations`. So an edit that restates the prose silently drops
every edge the record held, including one written by `--supersedes`, and nothing warns. The record
is still valid, the graph is quietly smaller, and `bm doctor` cannot tell — a relation that no
longer exists is not a dangling relation.

**Fix:** `--body` should replace the prose and preserve the `## Relations` section, the way
`--rel` appends to it rather than rewriting the body around it. The section is already parsed by
`append_relations` in `cli/record_notes.py`, so the split point exists; `--body` needs to use it.
The alternative — refuse `--body` on a record that carries relations — is worse, because the whole
point of `--body` is to fix prose.

**CLOSED 2026-08-18 — `8b4fb433`.** `carry_relations` in `cli/record_notes.py`: `--body` replaces
the prose and carries the existing `## Relations` section onto the end of it. A replacement that
writes its own `## Relations` heading stands as written, so a file never carries two. The `$EDITOR`
path is deliberately exempt — the editor opens on the whole body, relations included, so a saved
buffer without them is a deletion the user made (tested with a truncating editor). Tests in
`tests/cli/test_record_write_commands.py` (`test_edit_body_carries_the_relations_section_over`,
`..._stands_as_written`, `test_edit_in_the_editor_replaces_relations_the_user_removed`).

### U18 — `bm edit --rel` cannot touch a task or a finding, so U14's own example still cannot be linked afterwards — **FIXED 2026-08-18**

**Found 2026-08-18** while closing U14. `_refuse_edit` allows `bm edit` only on the kept-current
types, on the principle that a finding is evidence and a task is a record of what was decided —
neither is a document you revise. `--rel` inherits that refusal, because it is a flag on `bm edit`.

The consequence is exactly U14's motivating example. *The task `make bm brief --query report the
number of matches` came out of a specific finding* — that pair can now be linked at `bm new` time
with `--rel derived_from:<id>`, but only if the writer knows the other id when the second record is
written. A migration that writes 69 findings and then 23 tasks does not: the finding ids exist by
then, but the connection is usually noticed afterwards, on the read-back. For those records there is
still no way to add the edge.

Widening `bm edit` to accept a relations-only change on a task or a finding is defensible — it adds
an edge, it does not rewrite evidence, and the record's own claims are untouched. But that is
**a change in what the verb is**, which AGENTS.md's stop-list reserves for the user.

**Fix — needs a decision, not an implementation.** The options are: (a) allow `--rel` on any type
while `--title`, `--body` and `--set` keep the current refusal, so `bm edit` becomes two verbs
wearing one name; (b) a separate `bm link <type> <from> <to>` that is never a document edit; (c)
leave it, and accept that provenance must be known at write time. **Do not implement any of these
without asking.**

**CLOSED 2026-08-18 — `8b4fb433`.** Decision taken under the user's delegation ("you decide"): a
relations-only edit — `--rel` with no `--title`, `--body`, `--set`, and no editor — is allowed on
every type, tasks and findings included. An edge adds a link and rewrites nothing the record claims,
so the refusal that guards a task's closure and a finding's evidence does not apply; anything that
rewrites the record keeps the refusal and writes nothing. `relations_only` gate before
`_refuse_edit` in `cli/commands/record_write.py`; README's `bm edit` row says so. Tests:
`test_edit_rel_alone_is_allowed_on_a_closed_type[task|finding]`,
`test_edit_rel_with_a_title_on_a_finding_is_still_refused`.

### U19 — `bm doctor` exits 0 even when it reports issues — **FIXED 2026-08-18**

**Found 2026-08-18** while closing U10, and pre-existing. `bm doctor` prints its integrity and
hygiene rows and its `N issues` count, and then exits 0 whether the count is zero or not:

```
$ bm doctor -p scratchpilot --only integrity ; echo "exit=$?"
integrity  project 'scratchpilot'
  findings/tnd-…--delete-me-a.md  missing-file  permalink=tnd-…
  repair: bm reindex -p 'scratchpilot'
  1 issue
exit=0
```

W2 made doctor the gate — the gardener's jobs are checks inside it, and the migration procedure ends
with doctor as one of its four acceptance commands. A gate that always exits 0 cannot be a gate for
anything automated: no hook, no `just` recipe and no CI step can act on it without parsing the text,
which the output contract does not promise to keep stable.

Note the contrast with the verbs closed the same day: U12 has `bm reindex` and `bm status` exit 1
when they skip a project, and W20 rule 6 already says the exit code, not the payload, is what says a
run failed.

**Fix:** exit 1 when `issue_count > 0`. The open question is whether *hygiene* should count toward
that or only *integrity* — hygiene rows are advisory (an unfiled inbox record is a legitimate
resting state, per U5), so a doctor that exits 1 on hygiene alone would make the count unclosable
again. The likely answer is integrity → exit 1, hygiene → exit 0 with the rows still printed, and
`--strict` for anyone who wants both.

**CLOSED 2026-08-18 — `cf329d74`.** `exit_code()` in `cli/commands/doctor.py`: integrity issues →
1; hygiene-only → 0 (advisory — an unfiled inbox record is a legitimate resting state, U5);
`--strict` → 1 on either; empty registry → 0; under `--only hygiene` the integrity group was never
queried and contributes no verdict. The whole report prints before the exit. `--self-test` refuses
`--strict` rather than ignoring it. Repo callers audited: nothing scripts the exit code except the
tests (8 added in `tests/cli/test_doctor_command.py`). Gate for U16–U19: 3742 unit (3727 + 15),
284 int, doctor pass.

### U20 — `bm doctor` does not judge relation *types* on records MCP or a hand edit wrote — **OPEN (design note, low priority)**

**Recorded 2026-08-18** so the limit is on the ledger, not because it needs fixing. U14 put the
closed vocabulary's `relations:` enforcement at the verb — `bm new` and `bm edit` refuse a `--rel`
type the project does not declare — and deliberately *not* in `vocabulary/checker.py`.

The reason is not effort. An inline `[[…]]` anywhere in a note's prose parses to a relation of type
`links_to` (`markdown/plugins.py`), and the checker receives that list through
`accepted_note_relation_types`. A `relation-not-declared` rule there would therefore refuse every
note whose body links to another note — the D8 `note`-type breakage repeated one layer up. So the
closed vocabulary governs **what a verb may write**, not how prose may link.

The consequence, stated plainly: an edge of any type written by the MCP tools, by a hand edit, or by
an importer is never reported by `bm doctor`. Dangling relations are still reported (T4) and
`supersedes` is still governed on the type it appears on (`supersedes-not-on-type`); it is only the
*type vocabulary* of a relation that goes unchecked outside the verbs.

**Fix, if it is ever wanted:** a checker rule that judges only edges written as
`- <type> [[<id>]]` bullets under `## Relations` — the form the verbs write — and ignores relations
the markdown parser derived from inline prose links. That distinction does not survive into the
parsed relation list today, so it would need the parser to record which relations came from the
section and which from prose. That is real work for a check nothing currently needs, which is why
this is a note rather than a task.

### U21 — `.bm.yml` carries only the project name, so no script can find `store/<id>/headline.md` without calling `bm` — **FIXED 2026-08-18**

**Found 2026-08-18** while drafting the dotfiles cutover (W9's remaining half). Every shell/JS
consumer of the headline — the statusline, `notify.sh`, the projects overview — must resolve
`.bm.yml → store/<id>/headline.md` without a `bm` call (the 0.15 s floor is too slow for a
statusline; W9 point 1). The marker holds `project: <name>`; the name→id map lives in the DB, so no
script can make the hop. `AGENTS.md` already says "the id in [the marker] is authoritative", so this
is finishing the design, not changing it. Note `~/.config/basic-memory/config.json`'s `projects`
map is legacy import state, not the registry — a script must not read it.

**Fix:** `bm project add <name> --governed --here` writes `.bm.yml` in cwd with `project:` and
`id: <external_id>` (refuses to overwrite a marker naming a different project); a way to mark an
existing project too (`bm project mark`?) — spelling is the user's call. The resolver reads `id:`
when present, falls back to `project:`. Store root rule for scripts = `resolve_data_dir()`:
`$BASIC_MEMORY_CONFIG_DIR`, else `$XDG_CONFIG_HOME/basic-memory`, else `~/.basic-memory`. Gates
the statusline cutover; ship before migration batch 2. Decision task in bm: `tnd-k233nmds`.

**Fixed 2026-08-18.** A `.bm.yml` now carries two keys: `project: <name>` and `id: <external_id>`,
where the id is the project's store directory name. `render_marker`/`write_marker` in
`project_marker.py` own the write, `read_marker_id` the read, and `marker_conflict` the refusal —
a marker naming a different project is never repointed, and `bm project add --here` asks that
question *before* it creates the project, so a refusal leaves nothing behind.

Two ways to get one. `bm project add <name> --here` writes it for a project it just created.
`bm project mark [<name>]` writes or refreshes one for a project that already exists; with no
argument it takes the name from the marker already in the directory, which is the retrofit path
for every marker written before the id existed. `mark` is a native verb — it needs two columns of
one registry row, read through the new `lookup_project_external_id` on the synchronous sqlite path
— and `tests/cli/test_native_command_import_guard.py` covers it as `project-mark`.

**Resolution still keys off `project:`.** The id is recorded for external consumers, not yet
authoritative: making it so needs a decision about which key wins when the two disagree, and that
question has no answer until every marker carries both. Stated in `read_marker_id`'s docstring.

The store-root rule scripts must follow is in `README.md` under "Reading a project's files without
running `bm`", as a shell snippet: `$BASIC_MEMORY_CONFIG_DIR`, else `$XDG_CONFIG_HOME/basic-memory`
**when that variable is set**, else `~/.basic-memory`; headline at `<root>/store/<id>/headline.md`.
`resolve_data_dir`'s own docstring had that order backwards and was fixed in the same pass.

### U22 — `[[x]]` inside inline code is indexed as a `links_to` relation — **FIXED 2026-08-18**

**Found 2026-08-18** by the batch-1 migration (briefcase). A finding's body read
"8. `[[wikilinks]]` with a fuzzy dropdown on `[[`" — quotation, in backticks — and the index gained a
`links_to` relation to a page called `wikilinks`, which `bm doctor` reported as
`unresolved-relation -links_to-> [[wikilinks]]`. There was no way to quote a wikilink literally.

```
$ bm new finding "x" -p briefcase --body 'supports `[[wikilinks]]`' --quiet
$ bm doctor -p briefcase --only integrity --quiet
  findings/tnd-…  unresolved-relation  -links_to-> [[wikilinks]]
```

Cause: `relation_rule` in `markdown/plugins.py` read `token.content` — the inline token's raw text,
backticks included — instead of its children. Fenced blocks are separate block tokens and never
reached it, so inline code was the one gap.

**CLOSED 2026-08-18.** `_prose_outside_inline_code(token)` joins the inline token's children with
every `code_inline` span removed, and `relation_rule` parses relations from that. A token with no
children (a plugin test feeding raw content) falls back to its text. Test:
`tests/markdown/test_markdown_plugins.py::test_relation_plugin_ignores_wikilinks_inside_inline_code`
— a bare `[[Real Page]]` beside the code span is the positive control. Gate: 3743 unit (3742 + 1),
284 int, doctor pass. The already-written briefcase record clears on the next reindex once the
release carrying this lands on the machine running the migration; until then the harness validator
fails on any bare or backticked `[[…]]` that is not a `tnd-` id.

### Migrating a repo's tracking files

The procedure the 2026-08-17 pass followed. Every entry above came out of it.

1. **Read the vocabulary first** — `bm types`. Everything below picks from it; nothing invents.
2. **Inventory:** `STATUS*.local.md`, `HANDOFF*`, `PLAN*`, `.forked/**`, `TODO*`, `DECISIONS*`. Read
   each in full first — a record written from a skim needs its source re-read to be trusted.
3. **Map by temporal shape, not topic.** Dated decision or lesson → `finding`, with `--event-date`
   and `--date-source`. Open next-step → `task`, with `--opened`. Kept-current document → `guide`.
   Current state → *one* `state`. Unclassifiable → `inbox`, which is what it is for.
4. **A long design document becomes one `guide` pointing at the file**, never a record per section:
   decomposing it loses the connective tissue and the file is still there to read.
5. **Reversals go in as pairs**, the later one with `--supersedes`. The abandoned position is the
   half worth keeping; that is why the pair exists.
6. **Run the writes sequentially** — they allocate ids against one database.
7. **Finish with `bm ls`, `bm brief`, `bm doctor`, `bm history dirty`.** Those four are the gate.
8. **File every rough edge here in the same session.** A return visit does not happen.

### U23 — a task could be open or closed and nothing else, so work set aside had to be dropped or left open — **FOUND + FIXED 2026-08-18**

**Found 2026-08-18** (user decision): "not dropped forever but also not in our current set". The
vocabulary's five statuses forced a choice between two wrong answers. Left `open`, a parked task
sat in `bm brief` and took the derived headline, which is the one line a statusline shows — so the
statusline advertised work nobody meant to do. Marked `dropped`, the record said the decision was
reversed when it was only deferred.

**Fixed 2026-08-18.** `shelved` joins `DEFAULT_VOCABULARY.statuses`, between `blocked` and `done`,
which is where it sits in meaning: parked, not closed.

The semantics live in `vocabulary/model.py` beside `TERMINAL_STATUSES`, because every caller that
asks "is this task still open" has to get the same answer:

- `PARKED_STATUSES = frozenset({"shelved"})`
- `inactive_statuses(vocabulary)` = terminal ∪ parked, each half narrowed to the names the project
  declares. The terminal half keeps its fallback — a project declaring no terminal name would leave
  every task open forever — and the parked half has none, because "this project has no parked
  state" is a real answer.

`bm brief` and `services/headline.py` both read it, so a shelved task is neither listed as open nor
counted as closed. `bm brief` prints one line under the open-tasks rows, `Shelved: N`, and never
lists them: a parked pile is context, not something to act on. A brief whose only content is parked
work stays empty.

`bm types` explains it, through a new `STATUS_MEANINGS` in `vocabulary/glossary.py` — only statuses
whose name does not speak for itself get a line, which today is `shelved` alone.

**A project governed before today does not get it for free, and that is deliberate.** Its
`vocabulary.yml` carries a full `statuses:` list, and a present key replaces the defaults outright,
so `bm mark <id> shelved` is refused there. Humans extend the vocabulary; `bm` must not edit that
file (W4). So the refusal names the fix instead: `'shelved' is not a status project 'x' declares.
Allowed: …. Add it to <store>/<id>/vocabulary.yml to enable.`

### U24 — the headline was derived from task titles, so it was mush nobody wrote — **FOUND + FIXED 2026-08-19**

**Found 2026-08-19** (user decision, designing the harness cutover): the headline file W9 ships —
`store/<id>/headline.md`, the one line a statusline shows — was *derived*: the most recently
touched open task's title, truncated to 30 chars. That produced lines like
`Decide whether the transcript-s`, and it could not say anything the task list did not already
say. The real headline is often unrelated to any open task, and the thing that knows what is next
is the agent in the session, not a recency query. U23's complaint — a shelved task taking the
headline — was a symptom of the same root: derivation puts a line on the statusline that nobody
composed.

**Fixed 2026-08-19.** The headline is **composed, never derived**:

- **`bm headline "<text>"`** sets it; **`bm headline ""`** clears it (absence is the honest
  "nothing is next" — consumers fall back to their own default on a missing file, exactly as the
  derived no-open-work case behaved); bare **`bm headline`** prints it and teaches the shape. A
  native verb on the fast path, covered by the import guard.
- **Over-limit is a hard error, never a truncation** (`services/headline.py`,
  `MAX_HEADLINE_CHARS = 30`): the 30-char cut is what made derived headlines mush, and a line
  nobody wrote must not reach the statusline. The limit is taught *before* it can be hit — the
  bare verb, the brief footer, and the no-headline nudge all name it.
- **No task write touches the file any more.** `refresh_headline` and its
  `local_write_stack._record` hookup are deleted; `record_note_write` lost its `extra_paths`
  parameter (the headline was its one caller). `bm headline` commits its own change through
  `store/write_hook.record_headline_change` — the headline always sits in the store worktree, so
  it commits even for an off-store project, and a failed commit degrades to a notice.
- **Freshness comes from prompts, not derivation.** `bm done` and `bm mark`, when the new status
  is closing (`inactive_statuses`: done, dropped, shelved), print the current headline and ask:
  `headline: "…" — still right? bm headline "<text>" updates it, bm headline "" clears it` (or a
  set-one nudge when none exists). `bm brief`, pinned, ends with a
  `Headline: "…" — still right? …` payload line — payload, not a hint, because the session hook
  runs `--quiet` and this line is how every session starts knowing the current line and the limit.
- The mtime rule survives: read-compare-skip, so an unchanged set never makes a stale project read
  as fresh, and now *only* deliberate updates move the mtime — a better staleness signal than the
  derived file ever was.

Zero migration: the composed value lives in the same file the derived value did, so existing
headline files simply stop being overwritten. Stale ones stay accurate-as-of-mtime, which the
overview script already reads.

### U25 — the closed vocabulary had no aliases, so `bm new decision` quietly became inbox — **FOUND + FIXED 2026-08-19**

**Found 2026-08-19** in the session that shipped U24. An agent recording a user decision wrote
`bm new decision "…" --quiet` in the governed `basic-memory` project and the record landed as
`inbox`. Not a bug in the hatch — W4's escape hatch worked exactly as designed, the record carried
`proposed-type: decision`, and a notice existed — but the notice was `--quiet`-gated and the only
un-gated signal was the type column of the payload line. The user's framing: the fix is
vocabulary, not more warning — *decision*, *todo*, *idea* are the words people and agents actually
reach for, and a closed vocabulary should catch the reach, not file it as a proposal.

**Fixed 2026-08-19.** Three pieces:

- **Aliases are vocabulary** (`vocabulary/model.py`): `DEFAULT_ALIASES` maps `decision → finding`,
  `todo → task`, `idea → inbox`; a project's `aliases:` key in `vocabulary.yml` replaces the
  default outright, the way `types:` does, and is validated — a target must be a declared type,
  and an alias may not shadow one, because a name that is both makes `bm new <name>` mean two
  writes. Absent, the defaults apply narrowed to the types the project declares. Humans extend;
  agents still only select — an alias only ever lands on a declared type.
- **The write resolves, the record stamps the canonical type, and a notice teaches**
  (`record_notes.resolve_note_type`, now returning a `ResolvedType`): `bm new decision` writes a
  `finding` and prints `finding recorded (alias: decision is an alias for finding)`. The hatch
  still catches everything else, and its notice now names the declared set inline —
  `no type 'foo' — filed as inbox proposing it (types: … · bm types for detail)` — so the writer
  does not need a second command to learn what would have landed as itself.
- **The surfaces report it**: `bm types` gains an `aliases` section beside `statuses`, and
  `bm brief` closes with one line of tool context built from the glossary —
  `types: task (do it) · … · aliases: decision→finding, … — bm types for detail` — because the
  session-start brief is where an agent learns the tool it is about to write with.

### U26 — `bm undo` twice re-did instead of going deeper — **FOUND 2026-08-19, FIXED 2026-08-20**

**Found 2026-08-19** smoke-testing U25. Three `bm undo` runs were meant to revert the last three
writes (`new`, `new`, `done`) and netted exactly one revert: each restore is recorded as a *new*
commit — by design, history is the thing being protected — so the next undo targeted the restore
and re-applied what the first had reverted. Repeated undo ping-ponged between two states, and
"peel back the last N writes", the thing an agent actually reaches for, was unreachable.

```
$ bm undo --quiet; bm undo --quiet; bm undo --quiet   # after new, new, done
# net effect: only the `done` was reverted; both `new` writes survived
```

**Fixed 2026-08-20.** Three pieces:

- **Restores are identifiable**: the restore commit carries one `Undo-Of: <sha>` trailer per
  commit it reverted, beside the existing `Session:`/`Actor:` trailers (`_commit_message`).
- **Bare `bm undo` pair-cancels** (`latest_undoable_commit` in `store/history.py`): walking
  newest→oldest, a commit already in the skip-set is passed over *before* its trailers are read,
  a restore adds what it reverted to the skip-set, and the first commit that is neither is the
  target. Undo·undo peels two real writes; undo-of-an-undo (a redo) cancels the restore it
  reverted, so the redone write stays the next target — the R(R(X)) case.
- **`bm undo --last`** keeps the literal-newest behavior, which is how you redo an undo.
  `--session` is unchanged.

Accepted edge: a restore committed before this fix carries no trailer, reads as a normal commit,
and is offered as the target — reverting it is a redo, exactly what targeting it meant before.
Old history keeps its old semantics; new history gets the useful ones.

### U27 — no verb deleted a record, so inbox triage dead-ended at "leave it" — **FOUND 2026-08-19, FIXED 2026-08-20**

**Found 2026-08-19**, the same smoke-test session: a junk inbox record (`tnd-yxxa4knk`, written to
exercise the unknown-type fallback) had no exit. An inbox record carries no status, so it cannot be
`dropped`; there was no delete verb on any path; and `bm undo` could not reach a write several
commits back (see U26). Triage of the very hatch `bm doctor` reports had no closing move.

**Fixed 2026-08-20: `bm rm <id>…`.** The deletion runs the same mutation/materialization pair the
API's delete endpoint runs, then commits the removal into the store history — which is what makes
the verb safe to have: the content sits in the parent commit and `bm undo` restores it. Shape:

- Native fast path (`cli/commands/rm.py`), write-chain project resolution, import-guard probe.
- `delete` joins `WriteOperation` **and** `_DESTRUCTIVE` in `store/write_hook.py`, exactly as that
  module's comment always demanded — preflight refusal when the history cannot record, and the
  loss messages name the right verb ("refused to delete", not "overwrite").
- A target with uncommitted changes is refused, naming `bm history commit --all` — the same edit
  `bm undo` refuses to overwrite, for the same W3-B reason.
- Several ids are processed independently: per-id error lines on stderr, the rest still deleted,
  exit 1 if any failed.
- Relations that pointed at a deleted record go unresolved and `bm doctor` reports them. Honest
  and deliberate: the edge recorded a claim whose target is gone, and dropping it silently would
  hide that.

`bm project info` moved onto the direct path in the same pass (measured 3.14 s user / 224 MB on
the client route 2026-08-19; the payload is built by `ProjectService.get_project_info`, which
never needed the ASGI app) — see the Measured baseline note in `AGENTS.md`.

### U28 — `bm brief` hid `Shelved: N` when there were no open tasks — **FOUND 2026-08-19, FIXED 2026-08-20**

**Found 2026-08-19** (inbox record, user decision to fix): "bm brief hides 'Shelved: N' when there
are no open tasks — the parked pile is invisible exactly when it is the whole picture." U23 shipped
the parked count as a line *under the open rows*, and `Section.is_empty` deliberately ignored
`parked` ("a parked pile is context, not content: alone it does not make a section"). The
consequence inverted U23's own goal: shelve the last open task and the brief collapses to
`nothing open in '<project>'`, which reads as "nothing at all" — the one state in which work set
aside would actually be forgotten.

**Fixed 2026-08-20.** `parked > 0` now makes a section non-empty. With open rows the rendering is
unchanged (`Shelved: N` under the rows); with none, the section renders as one line —
`Open tasks: 0 — Shelved: N` — so the brief states both facts without listing parked titles. The
"counted, never listed" rule from U23 still holds; the reversed test
(`test_a_brief_with_only_shelved_work_is_empty` → `…_reports_the_pile`) records the flip.

### U29 — the marker walk crossed repo boundaries and searched `$HOME` — **FOUND 2026-08-18, FIXED 2026-08-20**

**Found 2026-08-18** by the batch-1 migration planning (inbox record tnd-b4f4eu4m): `find_marker`
walked from cwd to `/`, so a `.bm.yml` at `~/develop` would silently capture every unmarked repo
below it — writes included — and a marker at `$HOME` would capture everything. The 2026-08-19
session-hook rewrite (dotfiles `bm-session-context.sh`) fixed the *hook's* walk; bm's own resolver
still had the trap, and both the write chain (`resolve_cli_project`) and the read scope
(`resolve_read_scope`) go through it.

**Fixed 2026-08-20**, mirroring the hook's settled rule in `project_marker.find_marker`, which
both chains share:

- The walk stops at the first directory holding a `.git` — directory or file, so worktrees and
  submodules bound it too — **inclusive**, so the repo root's own marker is still the normal case.
- `$HOME` and its ancestors are never searched, for any start point. This is also what keeps a
  dotfiles-style `~/.git` from reading as a repo boundary: the walk stops at `$HOME` before
  consulting it. Judgment call: the ceiling applies to starts outside `$HOME` too (`/` and `/home`
  are above `$HOME`), so a marker at the filesystem root is no longer reachable from anywhere —
  the widest form of the same trap.

Test-fixture consequence, recorded because it will bite again: the CLI and registry fixtures set
`HOME=tmp_path`, so any test that writes a marker *at* `tmp_path` is writing a marker at the fake
`$HOME` — invisible by design after this fix. Six tests did; they now nest the marked tree one
level down. Put new markers in a subdirectory of `tmp_path`, never at its root.

### U30 — record ids opened with `tnd-`, a codename that told the reader nothing — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user): agents report record ids back in conversation — "Recorded as
`tnd-yke6e8dz`" — and several people asked what `tnd` means. It is the `tend` design codename
(AGENTS.md: a codename, not a command), which no user-facing surface explains, so the one string
every report quotes carried zero information. The user's proposal: the record's type, in full.

**Fixed 2026-08-20.** A new id is `<canonical-type>-<8 chars>` from the unchanged 36-symbol
alphabet: `task-yke6e8dz`, `finding-8xk3p2q1`. Decisions, all in `vocabulary/ids.py`:

- **The prefix is the full type word, never an abbreviation.** It cannot lie because type is
  set-once — there is no `bm edit --type`, and inbox triage is `bm new` + `bm rm` (U27). An alias
  write stamps the canonical type before the draw, so `bm new todo` yields a `task-` id; the W4
  hatch stamps `inbox`, so a proposal's id says `inbox-`, never the proposed name.
- **Both shapes are valid forever.** An id is a permanent name: nothing rewrites an existing
  record, and one validation pattern (`^[a-z][a-z0-9-]*-<8 chars>$`) covers old and new because
  `tnd` parses as a well-formed type slug. The pattern stays ignorant of any project's declared
  types — an id from another project or a since-narrowed vocabulary must still parse. The trailing
  `-<8 chars>` is the discriminator; a prefix is never parsed back into a type.
- **Custom declared types slugify into their prefix** (`Run Book` → `run-book-…`) with hyphen runs
  collapsed, so an id can never contain the file name's `--` separator. A type name that folds to
  nothing or opens with a digit takes `record-` — identity is the random body, so a generic prefix
  is a cosmetic loss, never a refusal.
- The old constant survives as `LEGACY_ID_PREFIX`, and `new_record_id`/`allocate_record_id` now
  take the canonical type. Teaching examples in help text moved to the new shape; fixtures that
  guard legacy acceptance deliberately keep `tnd-`.

No migration: the ~3250 migrated records keep their `tnd-` names, links keep resolving, and mixed
listings are expected output, not corruption.

### U31 — the brief taught nothing about the tool, and open tasks rotted unseen — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20**, two ways. First, U25's types line was `--quiet`-gated while the session hook —
the one consumer it was written for — runs `bm brief --quiet`, so no agent ever saw it. Second, the
hn-app usage audit (finding in the basic-memory project): 110 records, agents using the write path
daily — and seventeen open tasks with **zero** ever marked done, the oldest opened 2026-04-01.
Progress was narrated as sequential findings while the task pile rotted, and nothing surfaced the
rot where an agent looks. The user's decision: brief may spend ~1k tokens if it makes everything
downstream cheaper — it should carry the whole toolbox, not a single line.

**Fixed 2026-08-20.** Two pieces, both payload rather than hints:

- **`toolbox_lines()` replaces `types_line()`** and prints on every non-`--query` brief, `--quiet`
  included — the manual is payload; `--quiet` keeps hiding notices and affordances only. Seven
  lines built from the glossary and `DEFAULT_VOCABULARY`, never hardcoded copies: the write verbs,
  the read verbs, the undo pair (U33's wording), types + aliases, statuses with the `shelved` gist,
  the doctrine line — *finished it? `bm done` — learned it? `bm new finding` — will do it?
  `bm new task`* — and the supersession rule naming `--rel supersedes:<old-id>`.
- **A stale-open count in the task section** (`STALE_TASK_DAYS = 60`): open tasks whose
  `updated_at` — the timestamp writes actually move — is older than the window print one line
  under the rows: `N open tasks untouched >60d — still real? bm mark <id> shelved parks one`.
  A count and never rows, like the shelved pile: the line prompts a triage, `bm ls` is the triage.

### U32 — the stale side of a correction showed nothing — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** by the hn-app usage audit. The append-only finding discipline worked — every
`Correction:`/`Retracted:` finding carried `derived_from` to the record it overturned — but the
edge lives on the *new* record only. An agent landing on the old finding via search read a
confidently wrong claim ("the mmap pragmas caused the OOM") with nothing pointing forward. `bm
show` already rendered incoming `supersedes` edges; every other relation type was invisible from
the target side.

**Fixed 2026-08-20.** `direct_record` now returns `referenced_by`: every incoming relation except
`supersedes` (which keeps its richer supersession rendering), read from the same eagerly-loaded
collection — never a file rescan, so the native path stays fast. `bm show` prints them after the
payload as quiet-dropped notices — `← derived_from by finding-0fkuaraa "Correction: …"` — titles
cut at 60 chars, capped at `MAX_INCOMING = 5` with an honest `… and N more incoming relations`
line for hub records. The payload stays byte-exact; only the notice block grew.

### U33 — `--last` said nothing about the one case it exists for — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user, reviewing U26): "are you saying bm undo --last still avoids undoing an
undo? cause that could get a bit confusing" — read exactly backwards, which is the finding. The
flag's whole purpose is the redo case, its name points at mechanism ("the literal newest commit")
rather than intent, and the divergence from bare `bm undo` only exists at the moment nobody reads
help: right after an undo.

**Fixed 2026-08-20.** `--redo` is an alias for `--last` (one fold at the top of the verb, so every
branch and refusal covers both spellings), and the divergence is now spelled out in the three
places an agent actually looks: the help text (they diverge only when the newest commit is a
restore), the static `UNDO_AFFORDANCE` (bare undo peels one write deeper · `--redo` reverts the
newest commit, restores included), and a state-aware post-undo line derived from the walk itself —
`note: bare 'bm undo' next peels <sha> (one write deeper) · 'bm undo --redo' puts this restore
back`. Derived, not asserted: `latest_undoable_commit` runs again after the restore commit, so the
line's answer is the walk's answer. The brief's toolbox (U31) teaches the same pair.

### U34 — bm failures died in the terminal that saw them — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user decision, designed across two sessions of discussion). bm's users are
almost entirely agents on several machines. When bm misbehaved there was no way for the failure to
reach this repo: the agent pasted an error into its session and the session ended. `BM-ISSUES.local.md`
and inbox records worked only on the machine that owns this checkout — a work-machine failure had no
path home. Separately, typos and misreached flags — the best signal about the tool's teaching
surfaces — were invisible.

**Fixed 2026-08-20.** Three pieces, one design:

- **`cmdlog`** (`src/basic_memory/cmdlog.py`): every invocation appends one JSONL line — command
  path, timestamp, exit, duration, project, version — to the XDG *state* dir (machine telemetry is
  not project knowledge, so it lives outside the `BASIC_MEMORY_CONFIG_DIR` boundary, like the
  fastembed cache). Ring-bounded (~150 KB → keep 500 lines). Best-effort throughout: a documented
  exception to fail-fast, because telemetry that can break a verb is worse than none. The console
  entry point moved from `app` to `main()` — the envelope that logs every exit shape; pointing the
  scripts back at `app` would silently disable all of it (pyproject says so in place).
- **`bm bug` + autocapture** (`src/basic_memory/bugs.py`, `cli/commands/bug.py`): `bm bug "…"`
  writes a markdown report — message, version, platform, cwd, project, harness env fingerprint,
  cmdlog tail — into `bugs_dir` (config; default `<data-dir>/bugs`). With `bugs_autocapture` (default
  on), every nonzero exit and uncaught crash files one too, exit-2 typos included by decision.
  Dedup keeps that sane: one file per failure shape (command + kind + first message line), repeats
  bump `count:`/`last-seen:`. A recursion latch keeps a capture failure from capturing itself.
  `bugs_followup` is an opaque user command run best-effort after a report lands — the cross-machine
  story is configuration (a dotfiles-synced `bugs_dir` plus a sync command), never bm code.
- The verb is deliberately DB-free and project-free (notice-guard exempt): a report must be
  writable when the database is the broken thing.

### U35 — nothing said which verbs earn their keep — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user): "it would be good to have some sort of metrics so we know which
commands are called the most, which are never called, across all projects on a machine." Surface
design followed W2's rule — no second checking command — so the metrics landed in doctor.

**Fixed 2026-08-20.** `bm doctor --only usage`: per-command counts and failure counts from the
cmdlog, the coverage window, and a `never run:` list computed from the live Typer registry (a new
verb appears there the day it ships, no list to maintain). Machine-wide and informational by
contract: its header says so, it prints no notices, and it can never affect the exit code —
`--strict` alongside it is refused rather than ignored. It joins `--only` but not the default
all-groups run: every other section is project-scoped corpus checking, and usage is neither.

### U36 — the registry never knew which repo a project was, so every fresh clone faced a human — **FOUND + FIXED 2026-08-20**

**Found 2026-08-19** in the name-collision design discussion, sharpened 2026-08-20. `.bm.yml`
markers are deliberately gitignored, so the marker never travels: every fresh clone of an
already-registered repo arrives unmarked. The session hook's collision rule (decided 2026-08-19:
never guess, never hash-suffix) then prompts a human with `mark`-vs-`add` — correct for a genuine
second repo named `api`, pure friction for the overwhelmingly common case, a re-clone of the same
repo. bm held no evidence to tell the two apart: the registry's `path` column is the store
directory, and nothing recorded which *working* repo a project belonged to.

**Fixed 2026-08-20.** The evidence is the origin URL, captured where certainty exists:

- **`project.repo`** (migration `p9k0l1m2n3o4`): nullable; the directory's
  `remote.origin.url`, trailing `.git` stripped, recorded by `bm project add --here` and
  `bm project mark`. Fill-empty-only: a re-mark backfills NULL (the U21 retrofit pattern), an
  equal value stays silent, and a *different* value warns and keeps what is recorded — two
  directories claiming one project is for the human. **Exact-match semantics by design**: ssh
  and https spellings of one repo do not match each other; a false prompt costs a keystroke,
  a false match sends writes to the wrong project.
- **`bm project mark --if-repo-matches`** is the hook's mechanical path: match this directory's
  origin against the registry — one match marks (the name argument becomes optional), no match
  or no remote exits 3, several claimants exit 4 listing them. Exit codes, not prose, are the
  interface. The session hook slots it between "no marker" and the collision prompt: try the
  repo match first, fall back to the human prompt only when the registry genuinely cannot say.
- `bm project info` prints the `repo:` line when one is recorded. The sync registry reader
  treats a pre-U36 database (no `repo` column yet) as "nothing recorded", the same fail-open
  spirit as its no-such-table guard — the hook must survive a not-yet-migrated registry.

The capture write goes through a narrow, fill-empty-only sqlite UPDATE in `project_registry.py`
— a documented exception to that module's read-only rule, taken so the native marker verb stays
off the SQLAlchemy import it avoids by design.

### U37 — bare `bm` was a usage error, so "what is open here?" had no cheap spelling — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user): running `bm` in-harness (`! bm` in Claude Code) should print the
project at a glance for the human *and* the agent reading the same context — instead it printed
Typer usage help. The equivalent question cost a flagged `bm ls -t task -s open` that misses
doing/blocked, or a `bm brief` nobody runs mid-session. (`bm list` stays a did-you-mean for `ls`
rather than gaining an alias; the usage log will say if it earns one.)

**Fixed 2026-08-20.** Bare `bm` renders the **board**: header (`board: <project> · headline: …`),
every live task — `doing`, then `blocked`, then `open`, recency within each; a missing status
counts as open for brief's reason — and a closing `N open items · shelved N · inbox N` summary.
Three shape decisions:

- **Always pinned.** No marker and no `--project` prints the session hook's own one-line opt-in,
  exit 0 — an unmarked directory is a fact, not an error, and two spellings of "not tracked"
  would teach the surface unreliably.
- **Not the brief.** No toolbox, no sections, no cap: brief orients a session start; the board
  answers one question mid-session.
- **A real (hidden) command.** `bm board` exists so the notice, affordance, and import guards see
  the verb the way they see every other one; the app callback (`invoke_without_command=True`)
  routes bare `bm` to the same function. cmdlog logs a truly-bare invocation as `board`;
  flags-only invocations (`bm --version`) stay `(none)`.

The import-guard probe runs the empty command from a marked subdirectory of its temp `$HOME`,
because the marker walk never reads `$HOME` itself (U29) — the probe's cwd doubles as `$HOME`.

### U38 — a multi-stage effort had no home, so plans lived in PLAN.md files that rotted — **FOUND + FIXED 2026-08-20**

**Found 2026-08-20** (user): the most common root markdown file after STATUS.local.md was a
PLAN.md — "a plan to uplevel hn-app, 7 stages" — a 3k-word document whose stages were dead prose
the moment work started. Records could hold a snippet but nothing larger: no way to relate stages
to a master plan, no roll-up, nothing an agent could follow hands-off.

**Fixed 2026-08-20.** Plans are records; the body is the plan. Four pieces:

- **`plan` is the eighth default type**, beside `task` because they share a lifecycle: status
  (same six values, `bm mark`/`bm done` work), `opened`, and the task's required fields minus
  `not-before` (a plan is followed or shelved, never snoozed). Unlike a task it is **kept
  current**: the body carries the narrative and an *ordered* stage list of `[[task-id]]`
  wikilinks, and `bm edit --body` rewrites it as the plan evolves. Records land in `plans/` with
  `plan-`-prefixed ids (U30 pays for itself: agent reports self-describe).
- **`part_of` joins the default relations**: a stage task writes `--rel part_of:<plan-id>`, so
  membership survives the body rewrite that would lose a wikilink.
- **`bm show` renders the live checklist.** The payload stays byte-exact, so the stage list is
  not annotated in place: a derived block after the payload restates every task/plan the record
  points at — in body order, deduped — as `→ task-x (doing) "title"`, and the U32 incoming block
  gains the same `(status)` stamp when the pointing record has a lifecycle. Showing a plan is
  reading its checklist; showing a stage names the plans it belongs to.
- **The board and brief treat plans as first-class open work**: plans ride the bare-`bm` board
  inline with tasks (the id prefix labels them; within a status rank a plan sorts first), the
  `Open plans` brief section gets rows, a parked count and the >60d stale flag via the same
  `non-terminal` rule tasks use, and the toolbox states the one recommended way — plan record,
  never a PLAN.md file.

**Adoption edge, deliberate:** a present key replaces the defaults, so a governed project's
explicit `types:` list (every file `--governed` has written) lacks `plan` until a human adds it —
`bm new plan` there files as inbox proposing `plan`, which is the W4 hatch working. `relations:`
follows U14's rule instead: a file that omits the key gets the defaults, `part_of` now included;
only an explicit `relations:` list excludes it. Ungoverned and freshly governed projects get both
immediately.

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

**Build order (agreed 2026-08-10, from the reconciliation pass; replaces the deleted
`.forked/campaign.md`):** T11 → W20 → W3 → W4 + picoschema strip + W19 → W5 → W8 → W9 → W1.
W19 items 2–4 are a binding acceptance condition on W4, not a follow-up — W4 is not done
without them.

**That order is fully walked as of 2026-08-17**, and the verbs phase after it is closed — see
*Verbs phase — CLOSED 2026-08-17* under W4 for what shipped, and the decisions list beside it for
the sixteen product decisions the orchestrator took on the user's behalf. **T24**, **T28** and
**T31** — the dead-surface and index-drift entries that were open after it — are all closed as of
2026-08-17: T24 and T31 deleted their dead surface, T28 was already fixed and only needed its
heading marked. The verbs phase's own **E2**, **V-J1** and **V-J2** are closed too, in a follow-up
pass the same day: `bm new` refuses when a vocabulary drops `inbox`, `bm edit --set` writes a
profile's declared fields, and a malformed `vocabulary.yml` degrades one project rather than
silencing every verb's notice. The D8 breakage behind `--governed` being opt-in is fixed in the same
pass — `DEFAULT_VOCABULARY` declares `note` — while the opt-in default itself stands as W4's rule.
Every BLOCKER is closed. There is no agreed order for what remains; the next phase picks one.

| | |
|---|---|
| Phase ordering | the build order above |
| Record schema (types, fields, supersession) | `.forked/schema.md` (local, gitignored) |
| Settled/reversed decisions with turn cites | `.forked/decisions.md` (local, gitignored) |
| Session-to-session state | `STATUS.local.md` (local, gitignored) |
| Fork point, remotes, license, measured baseline | `AGENTS.md` in this repo |
| Defects found by using the verbs, not by building them | the `USAGE` section above (`U*`) |
