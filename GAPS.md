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

**Fix:** exact-match `memory://` resolution must fail loudly. If a fuzzy fallback is wanted, it must
be opt-in and must mark the result as inexact. Upstream states a "fail-fast / no-silent-fallback"
house style (see #1151), so this is arguably an upstream bug worth reporting.

**NOT TESTED:** whether the fallback is FTS or vector (semantic search was on), and whether
`read_note` / `search-notes` share it. Both are worth knowing before building on this path.

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
