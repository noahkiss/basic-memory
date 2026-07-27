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

### T6 — a second frontmatter block is prepended to unindexed notes (data loss)
**Found:** BM spike, pre-dates this session. Carried here because it is a code gap, not a design one.

BM's editing tools, applied to a note that exists on disk but is not yet indexed, prepend a *second*
frontmatter block: 8 keys go in, `permalink:` alone comes out, everything else is demoted to prose —
and it exits 0 with a success payload. **This is silent data loss.**

Note this is the same root cause as T2 seen from the other side. **Fix:** detect an existing
frontmatter block before writing one; treat a doubled block as a hard error in `check`.

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

### B4 — no fast path: anything touching `mcp.tools` / `api.app` costs ~4 s
**Found:** fork-point baseline (see `AGENTS.md`). `bm tool search-notes` is 4.3–4.8 s; a native
command like `project list` is ~0.55 s; the `--version` floor alone is 0.33 s.

This already forced one design decision — `STATUS.local.md` stays a flat file, because a per-prompt
statusline cannot pay 4 s. **Any `tend` subcommand that needs to be fast must talk to the
repository/service layer directly and must not reach through the MCP tool layer.** Worth revisiting
whether the 0.33 s floor itself can come down, since that bounds every fast path we build.

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

### W4 — closed record vocabulary enforced in the write path
Humans extend the vocabulary; agents may only select from it. Upstream's frontmatter vocabulary is
fully open, so enforcement is ours and cannot live in a wrapper.

### W5 — `bm tend check`: schema and integrity lint
Covers T4 (unresolved relations), T6 (doubled frontmatter blocks), set-once field violations, and
`supersedes` appearing on a type other than `finding`.

---

## OPEN — observed, not diagnosed

### O1 — ~25% of transcript grep hits do not parse as whole-line JSON
**Found:** 2026-07-26. Of 47 `rg` hit lines across the pilot transcripts, 12 failed
`json.loads()` on the post-`path:lineno:` remainder. Cause not determined — candidates include
oversized lines, records spanning differently than assumed, or an artifact of the split. **This is
ours, not upstream's**, and it blocks W1 claiming completeness: a miner that silently skips a
quarter of its hits is not a record of anything. Diagnose before building W1.

---

## Where this connects

| | |
|---|---|
| Execution plan, phases, decisions | `~/develop/.design/status-system-plan.md` |
| Record schema (types, fields, supersession) | `~/develop/.design/status-system-schema-draft.md` |
| Settled/reversed decisions with turn cites | `~/develop/.design/status-system-decisions.md` |
| Session-to-session state | `~/develop/STATUS.local.md` |
| Fork point, remotes, license, measured baseline | `AGENTS.md` in this repo |
