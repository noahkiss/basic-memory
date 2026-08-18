# FORK NOTICE — read this first

This repo is a **hard fork** of [`basicmachines-co/basic-memory`](https://github.com/basicmachines-co/basic-memory),
maintained at [`noahkiss/basic-memory`](https://github.com/noahkiss/basic-memory). It is not a GitHub
fork and it is not upstream-track. Nothing here goes back upstream, so upstream's contribution
standards — DCO sign-off, semantic PR scopes, the CLA — do not apply. **The repo is public**, so
don't commit local paths or personal material; design docs live in `.forked/` (gitignored).

## → `GAPS.md` is this fork's to-do list. Write to it as you work.

**[`GAPS.md`](./GAPS.md)** holds every known defect, trap, and missing capability we intend to fix
here, with reproductions. Read it before planning work in this repo.

**If you find a gap during a session, record it there in that same session** — not in a design doc
or a STATUS file with the intention of transferring it later. That transfer is a return visit, and
return visits do not happen.

The working order is **fix the gaps that block the next thing, then build** — not build now and
clean up after.

## We do not track upstream

The point of this fork is to **strip and reshape** the tree into exactly what `bm` needs. We do
not merge, rebase, or cherry-pick from upstream, and we do not keep this tree mergeable with it.
There is no compat tax to pay.

- **Fix our own bugs in whatever shape suits this fork.** Never weigh a fix by how hard it would be
  to reconcile with upstream later — there is no later.
- **Upstream behavior is not a design constraint — do what is right, not what matches.** This is a
  divergent tool, not a patched copy. When a subsystem behaves in a way that is wrong for `bm`
  (frontmatter invisible to search, no staleness query, deferred writes on a local-only tree),
  the default answer is *change the subsystem*, not *design the feature around it*. "Adapt around
  upstream's shape" is only ever correct when the workaround is better on its own merits — and
  then the record should say so. (Rule earned 2026-07-31: O3 was first closed as "adapt", and the
  user reversed it into W18.)
- **Divergence is the goal, not a cost.** A test that only guards an upstream-shaped decision we
  have deliberately reversed can be rewritten.
- **Don't spend tokens on code we will never run.** Reading, testing, or "keeping consistent" a
  subsystem this fork doesn't use is wasted work. Deleting it is usually the better answer.

Forked from upstream `main` @ `232f2c2fc4e91564d88bcc312ed3d8bd1e8e051b` (2026-07-26). That SHA
records provenance only — it is not a merge base and nothing needs to update it. The `upstream`
remote is fetch-only (push URL `DISABLED`) for as-needed lookups: `git log 232f2c2..upstream/main`
to see if they already diagnosed something, `git show <sha> -- src/basic_memory` to lift one fix.

**`basic-memory --version` reports the installed build** — purely from `importlib.metadata`, since
there is no `__version__` literal to fall out of date (GAPS T3). A tagged install prints the plain
tag (`0.1.0`); a build between tags prints `0.1.0.dev165+117308fb`, and that `+<sha>` reflects the
last `uv sync`/install, not the working tree's HEAD, so re-sync after pulling before trusting it. A
source tree with nothing installed prints `0.0.0 (source tree; not installed)`.

## What this fork is for

Building a local work-tracking system directly into this codebase as first-class `bm`
subcommands, **not** as a separate wrapper tool.

Four capabilities, in rough dependency order. **All four have shipped** — each names the GAPS
entry that records how, and the constraints below still bind anything that changes them.

1. **A gardener**, which is **not a command** — its jobs are checks inside `bm doctor` (GAPS W2,
   decided 2026-08-05). Strictly lossless: it may flag — it may never summarize, merge, or resolve.
   Flag-only was already the constraint, so a separate `bm gc` would have been a second checking
   command, i.e. the one nobody runs. **Shipped:** `bm doctor`'s two groups, `integrity` and
   `hygiene`, each selectable with `--only` (GAPS W2, W5 item 5).
2. **Local git history on writes.** Every mutation commits into a local-only store repo so pruning is
   recoverable. Two traps: set `core.excludesFile` and `core.hooksPath` to `/dev/null` inside that
   repo (a global pre-commit hook will otherwise block automated commits), and never export `GIT_DIR`.
   **Shipped:** `store/history.py` and `store/write_hook.py`, with `bm history` and `bm undo` over
   them (GAPS W3, verbs items C and H).
3. **A closed record vocabulary.** Humans extend the vocabulary; agents may only select from it.
   Upstream's frontmatter vocabulary is fully open and does not enforce this, so enforcement is ours
   and has to live in the write path. **Shipped:** `vocabulary/`, enforced on the accepted write
   path and reported by `bm doctor` and the per-command notice (GAPS W4, W5).
4. **A decision-mining subcommand** over Claude Code transcripts, to recover decisions that were made
   in conversation and never written down. **Shipped:** `bm mine`, which parses and never judges —
   an agent reads its output and writes any keeper with `bm new` (GAPS W1).

**The store is the only home for note content.** Every note lives in a single plain git repo at
**`~/.basic-memory/store/`**, under `store/<id>/` — central and id-keyed, not one repo per project,
and never a mirror of content that lives somewhere else. Nothing is copied on write, so there is no
project-root-versus-store divergence to reconcile. The store path must derive from
`resolve_data_dir()` so it honours `BASIC_MEMORY_CONFIG_DIR` like `config.json` and `memory.db` do
— never hardcode it.

A **`.bm.yml`** marker is a **pointer, not a container**. It sits at the root of a *working*
directory — usually a code repo you run `bm` from — and says "when I am here, I mean this project."
It never has note content beside it. The id in it is authoritative; any directory name inside the
store is a human-browsing label that nothing reads.

Consequences, all deliberate:

- A project's path is **store-derived** (`store/<id>/`), not user-chosen. A path argument to
  `bm project add` therefore means an *import source*, not the project's home.
- Existing projects that live at arbitrary paths keep working and get one notice on every write:
  their files are outside the history repo, so nothing records them (verbs decision D3). Moving
  them is a Claude workflow — W6 closed without an importer.
- The `git clean -xdfn` hazard recorded in `GAPS.md` W3 cannot arise: nothing of value sits inside
  another repo's worktree.

**Naming:** `tend` is a **codename for the design, not a command.** There is no `tend` binary and no
`bm tend` namespace — the verbs ship flat under `bm` (`bm ls`, `bm new`, `bm edit`, `bm path`,
`bm mine`, `bm done`, `bm show`, `bm history`, `bm undo`, `bm mark`, `bm types`, `bm brief`,
`bm doctor`, `bm status`, `bm project`). **There is no `bm check`** —
the schema and integrity checks land inside the existing `bm doctor` (see `GAPS.md` W5), because a
second checking command would immediately be the one nobody runs.

## Measured baseline

Linux/x86-64, Python 3.13. Measured 2026-07-31 on this tree, after the T18 fast path landed —
the original fork-point table's numbers were unreproducible and one of them was wrong by an order
of magnitude (see `GAPS.md` T18). Re-measure if the tree moves substantially. The direct-path row
was re-measured 2026-08-10 after B4's head-stamp skip took alembic off the warm read path.

| Path | User CPU time | Resident memory |
|---|---|---|
| CLI direct-path cmd (`project list`, warm DB) | 0.9–1.2 s | 90 MB |
| CLI `--version` floor | 0.15 s | 40 MB |

Before the T18 fix, `project list` measured 3.6–3.7 s / 214 MB on the same host and method — the
difference is the ASGI/FastAPI/MCP import graph the direct path no longer touches. What remains is
SQLAlchemy + pydantic, the real cost of a DB-backed command — alembic left the warm path 2026-08-10
(GAPS B4: a head-stamp check skips `run_migrations` when the DB is already current).

Figures are **user CPU time**, not wall clock — CPU time and RSS hold steady under host load while
wall clock varies 2x. Treat them as a lower bound on wall time.

The rows the fork-point table carried for the MCP server, `bm tool search-notes`, and a full
reindex have been **retired rather than restated**. They were tied to a specific 67-file / 888 KB
corpus that no longer exists, so nothing here could reproduce them, and leaving unreproducible
numbers standing is what produced T18.

The structural rule still holds, and is the part that actually governs design: **any fast `bm`
subcommand must talk to the repository/service layer directly and must not reach through the MCP
tool layer.** `basic_memory.mcp.tools` and `basic_memory.api.app` are each seconds of import time.

The boundary is structural now, not aspirational: `basic_memory.cli.direct` is the supported way
for a native command to reach the service layer, and
`tests/cli/test_native_command_import_guard.py` runs each native verb — `project list`, `types`,
`mine`, `doctor`, `brief`, `ls`, `show`, `path`, `new`, `edit`, `mark`, `done`, `undo` — in a
subprocess, cold and warm, and fails if
`api.app`, `mcp.tools`, `mcp.async_client`, `mcp.clients`, `fastapi`, or `dateparser` ever enter
`sys.modules`. Model new fast verbs on `fetch_project_list` in `cli/commands/project.py`.

Two rules follow from that ban list (GAPS.md T30). A native verb takes its event loop from
**`basic_memory.cli.runner`**, never from `cli/commands/command_utils.py` — importing
`command_utils` pulls the MCP client graph. And a command module that *is* client-routed must
defer its `basic_memory.mcp` imports into the function that uses them, because `cli/main.py`
imports every command module on every invocation. The *other* project subcommands (`add`,
`remove`, `default`, `move`, `info`) still route through the in-process ASGI app and cost ~3.5 s —
they are mutations or one-shots where correctness, not latency, is the constraint.

Embedding model `qdrant/bge-small-en-v1.5-onnx-q` (64 MB) caches to the shared
`$XDG_CACHE_HOME/fastembed` in this fork, not inside `BASIC_MEMORY_CONFIG_DIR` as upstream had it —
it's an immutable artifact keyed by model name and doesn't belong inside that isolation boundary.
See `default_fastembed_cache_dir()` in `src/basic_memory/config_models.py`.

## How we work here

Every rule below was bought with a wasted pass, a wrong diagnosis, or a red suite that blinded the
passes after it. They are cheap to follow and expensive to re-learn, which is why they live in the
repo rather than in a local scratch file that gets pruned.

### Verification — before every commit, not at the end

1. **`just fast-check`** — exit 0.
2. **`just test-unit-sqlite`** — the *full* suite, not testmon. **Explain the count as arithmetic:**
   previous baseline ± the `def test_` lines this diff adds or removes. A green suite whose count
   you cannot explain is not a pass — it is a suite that may have silently stopped collecting.
3. **`just test-int-sqlite`** — the full suite. A green unit suite is not evidence for a deletion:
   testmon cannot select a test whose subject no longer exists, so it reports success by omission.
4. **`just doctor`** — for anything touching the file ↔ DB loop.

**Never pipe a test run through `tail`.** It buffers, tracebacks are lost, and the pipeline's exit
status is `tail`'s — so it reports 0 on a red suite. Use `tee`.

**Never ship on top of a known-red suite.** An inherited red suite blinds every pass after it. That
cost this fork four passes and one wrong diagnosis (see `GAPS.md` T17).

**One commit per closed item**, not one at the end. The message states what changed, why, and any
judgment call taken. No Claude co-author line, no generated-with footer.

### Delegating to sub-agents

- **Sub-agents edit only.** No `git`, no `just`, no `pytest` (`git grep` is fine). Verification runs
  once, centrally, after every agent has reported. Concurrent runs have corrupted results here.
- **Never write a brief that names a branch an agent might check out.** Say "stay on the branch you
  are on." One stopped agent resumed after its own compaction and staged a 30k-line unverified
  change onto `main`.
- **Stopping an agent is not reliably terminal.** After stopping one, re-verify branch pointers and
  the state of `origin`.
- **Agent self-reports are leads, not records.** The diff and the captured command output are the
  record.
- **Brief agents to report immediately after their last edit — no waiting, no monitors.** One
  implementation agent finished its work, then sat ~35 minutes watching a monitor on its own
  superseded test log for a "completion notification" that could never fire (2026-08-03, B2).
  The no-`pytest` rule already makes waiting pointless: there is nothing for an editing agent
  to wait on.

### Evidence rules

- **A claim without a reproduction is not a finding.** Paste the command and its verbatim output.
  Several figures in this repo's own docs turned out to be inherited and never re-checked.
- **Positive controls are mandatory.** Before believing any "no hits" result, ask what *would* have
  produced a hit and run that too. A negative result over a corpus that cannot produce a positive
  proves nothing. This file has been bitten by that three times.
- **An import-grep cannot prove a plugin is dead.** Plugins load through entry points (`pytest11`,
  setuptools console scripts, SQLAlchemy dialects, codecs) and never appear in an `import`. This
  nearly shipped 55 red tests — see `GAPS.md` T19.

### Deletion passes

- **Grep the test tree for any surface you delete, not just `src/`.** One `git grep` would have
  caught eight of T17's ten failures.
- **Before deleting a `with span(...)` or any wrapper block, check whether anything computed inside
  it escapes** — a returned field, a mutated accumulator, a value read after the block. See W15.
- **A gap entry that says "a later pass deletes that file anyway" closes the instance, not the
  class.** Re-grep after the scheduled deletion actually lands.
- **Only restore what the current session itself deleted.** Anything else that is absent was
  deliberately removed earlier. Reading history with `git show <sha>:<path>` is always fine.
- **Inventory files are maps, never diffs.** One was wrong in both directions.

### Publishing

The repo is public. No local paths, no personal material, nothing from `.forked/` committed. A bare
`gh repo view` in this directory reports **upstream** — always name the fork explicitly.

### The stop-list — the only things that still require asking

Everything else in this file is a rule to follow. These four are decisions to bring back:

- Deleting or force-pushing anything already published to `origin`.
- Deleting a subsystem not already named for deletion in `GAPS.md`.
- Anything touching the user's machine outside this repo and `~/.basic-memory/`.
- A change in what the product *is* — new verbs, dropped verbs, a different store design.

Moved here 2026-08-07 from `.forked/campaign.md`, which was deleted; it was the only home for this.

---

*Everything below is upstream's project guide, trimmed to what this fork actually runs. Where it
describes the codebase it is accurate.*

# AGENTS.md - Basic Memory Project Guide

## Project Overview

Basic Memory is a local-first knowledge management system built on the Model Context Protocol (MCP). It enables
bidirectional communication between LLMs (like Claude) and markdown files, creating a personal knowledge graph that can
be traversed using links between documents.

## CODEBASE DEVELOPMENT

### Project information

See the [README.md](README.md) file for a project overview.

### Build and Test Commands

- Install: `just install`
- Fast static check: `just fast-check` (fix, format, typecheck; no tests)
- Impacted tests: `just fast-test` (pytest-testmon)
- Unit: `just test-unit-sqlite` · integration: `just test-int-sqlite` · everything: `just test-sqlite`
- Single test: `uv run pytest tests/path/to/test_file.py::test_function_name`
- Local consistency check: `just doctor` — the `--self-test` file ↔ DB loop in a temp project.
  Runs with a temporary HOME/config so it won't touch your local settings; leaves temp dirs in
  `/tmp`.
- Lint: `just lint` · format: `just format` · typecheck: `just typecheck` (or `just typecheck-pyright`)
- All static checks: `just check` · DB migration: `just migration "message"`
- Coverage HTML: `just coverage` · MCP smoke: `just test-smoke` · MCP Inspector: `just run-inspector`
- Benchmarks: `uv run pytest test-int/test_search_performance_benchmark.py -v -m "benchmark and not slow"`

Python 3.12+ required (type parameter syntax and `type` aliases).

**The loop:** code → `just fast-check` → `just fast-test` (or a targeted `uv run pytest`) →
`just doctor`. Widen to `just test-sqlite` when the change warrants it. A cold testmon run is slow,
later ones aren't; testmon collecting 0 tests means nothing was impacted, not that it failed.

**Releases** are a git tag plus a GitHub Release with generated notes — nothing goes to a package
index. `just gate` is the pre-push check (lint + typecheck + unit tests); `just release vX.Y.Z`
tags, pushes, and runs `gh release create` against `noahkiss/basic-memory` explicitly; `just
release-preview vX.Y.Z` shows what it would do. Install is the Homebrew tap
(`brew install noahkiss/tap/basic-memory`), with `uv tool install git+…@vX.Y.Z` as the fallback on a
machine without the tap. Fork versioning starts at `v0.1.0` (2026-08-17); upstream's `v0.x` tags
were deleted here. See `.forked/release-design.md` for why there is still no PyPI, npm, or CI.

### Test Structure

- `tests/` - Unit tests for individual components (mocked, fast)
- `test-int/` - Integration tests for real-world scenarios (no mocks, realistic)
- Both directories are covered by unified coverage reporting
- Benchmark tests in `test-int/` are marked with `@pytest.mark.benchmark`
- Slow tests are marked with `@pytest.mark.slow`
- Smoke tests are marked with `@pytest.mark.smoke`

### Code Style Guidelines

- Line length: 100 characters max
- Python 3.12+ with full type annotations (uses type parameters and type aliases)
- Format with ruff (consistent styling)
- Import order: standard lib, third-party, local imports
- Naming: snake_case for functions/variables, PascalCase for classes
- Prefer async patterns with SQLAlchemy 2.0
- Use Pydantic v2 for data validation and schemas
- CLI uses Typer for command structure
- API uses FastAPI for endpoints
- Follow the repository pattern for data access
- Tools communicate to api routers via the httpx ASGI client (in process)

### Programming Style

See [docs/ENGINEERING_STYLE.md](docs/ENGINEERING_STYLE.md) for the fuller house style and
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) for product language, ownership, identity, and
source-of-truth rules. The short version for agents:

For nontrivial Python writing, refactoring, or review, read and follow
[`.agents/skills/pythonic-code/SKILL.md`](.agents/skills/pythonic-code/SKILL.md) in full. Use
the skill's Write, Refactor, or Review mode that matches the task. GitHub coding and review
agents must apply this skill before changing or evaluating Python code.

- Prefer type-safe, explicit designs over object-heavy indirection. Use Python 3.12 `type`
  aliases, full annotations, and narrow `Protocol`s when a caller only needs a capability.
- Prefer functions and typed values before classes, and concrete classes before abstract base
  classes. Treat private-helper sprawl as a prompt to simplify the data flow.
- Use dataclasses for internal value objects and operation results; use Pydantic v2 at API,
  CLI, MCP, and persistence boundaries where validation and serialization matter.
- Keep async boundaries obvious. Resource-owning code should use context managers, propagate
  cancellation, and avoid hidden background work unless the lifecycle is explicit.
- Fail fast. Do not add silent fallback logic, broad exception swallowing, speculative
  `getattr`, or casts that hide an unclear model shape.
- Keep control flow simple and local. Push branching decisions up, keep leaf helpers focused,
  and name values after the domain concept they carry.
- Use evidence-first testing. Add or update meaningful regression tests for bugs and risky
  behavior, prefer real code paths over mocks, and run the narrowest command that proves the
  change before widening verification.
- Comments should explain why a branch, invariant, or constraint exists. Avoid comments that
  merely narrate obvious code.

### Code Change Guidelines

- **Full file read before edits**: Before editing any file, read it in full first to ensure complete context; partial reads lead to corrupted edits
- **Minimize diffs**: Prefer the smallest change that satisfies the request. Avoid unrelated refactors or style rewrites unless necessary for correctness
- **House style is canonical**: Follow the Programming Style section above for type-safe,
  fail-fast code; do not hide unclear models with speculative attributes, broad exception
  handling, casts, or unapproved fallback logic
- **No guessing**: Do not say "The issue is..." before you actually know what the issue is. Investigate first.

### Literate Programming Style

Comments explain **why**, never what. `counter += 1  # increment counter` is noise;
`counter += 1  # track retries for backoff calculation` earns its line.

- **Section headers** (`# --- Authentication ---`) when a file has distinct phases, so control flow
  reads like chapters.
- **Decision points** — for conditionals that materially change behavior (gates, fallbacks, retries,
  feature flags), state the trigger, the rationale, and what changes downstream.
- **Constraints** — when code exists because of an external limit (async requirements, rate limits,
  schema compatibility), explain the constraint next to the code that obeys it.

### Codebase Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for code layers and dependency direction. See
[docs/DOMAIN_MODEL.md](docs/DOMAIN_MODEL.md) for the meaning and invariants of the concepts those
layers implement.

**Directory Structure:**
- `/alembic` - Alembic db migrations
- `/api` - FastAPI REST endpoints + `container.py` composition root
- `/cli` - Typer CLI + `container.py` composition root
- `/deps` - Feature-scoped FastAPI dependencies (config, db, projects, repositories, services, importers)
- `/importers` - Import functionality for Claude, ChatGPT, and other sources
- `/markdown` - Markdown parsing and processing
- `/mcp` - MCP server + `container.py` composition root + `clients/` typed API clients
- `/models` - SQLAlchemy ORM models
- `/repository` - Data access layer
- `/schemas` - Pydantic models for validation
- `/services` - Business logic layer
- `/index` - Local runtime indexing adapters, watch service + `watch_coordinator.py` for lifecycle management
- `/indexing` - Portable indexing runners and planners shared by local and hosted runtimes
- `/runtime` - RuntimeMode resolution + runtime Protocol contracts

**Composition Roots:**
Each entrypoint (API, MCP, CLI) has a composition root that:
- Reads `ConfigManager` (the only place that reads global config)
- Resolves runtime mode via `RuntimeMode` enum (TEST > LOCAL)
- Provides dependencies to downstream code explicitly

**Typed API Clients (MCP):**
MCP tools use typed clients in `mcp/clients/` to communicate with the API:
- `KnowledgeClient` - Entity CRUD operations
- `SearchClient` - Search operations
- `MemoryClient` - Context building
- `DirectoryClient` - Directory listing
- `ResourceClient` - Resource reading
- `ProjectClient` - Project management

Flow: MCP Tool → Typed Client → HTTP API → Router → Service → Repository

### Development Notes

- MCP tools are defined in src/basic_memory/mcp/tools/
- MCP prompts are defined in src/basic_memory/mcp/prompts/
- MCP tools should be atomic, composable operations
- Use `textwrap.dedent()` for multi-line string formatting in prompts and tools
- MCP Prompts are used to invoke tools and format content with instructions for an LLM
- Schema changes require Alembic migrations
- SQLite is used for indexing and full text search, files are source of truth
- Testing uses pytest with asyncio support (strict mode)
- Unit tests (`tests/`) use mocks when necessary; integration tests (`test-int/`) use real implementations
- SQLite is the only database backend; tests need no external services
- Each test runs in a standalone environment with isolated database and tmp_path directory
- Performance benchmarks are in `test-int/test_search_performance_benchmark.py`
- Use pytest markers: `@pytest.mark.benchmark` for benchmarks, `@pytest.mark.slow` for slow tests
- **Coverage is 93.8% and must not go down.** `just coverage` reported `21216` statements with
  `1309` missed (93.83%) on 2026-08-10 — the first run with `*/cli/**` counted; the old "96%"
  figure was measured with the CLI omitted and is not comparable. `fail_under = 93.8` enforces the
  floor (`pyproject.toml`) — **but only when a coverage run happens.** There is no CI, and the run
  takes ~35 minutes, so this catches a regression at the next run, not at the commit that caused
  it. Treat it as a backstop, not a gate: write tests for new code and leave the number no worse
  than you found it. Raise the floor as tested code lands; never lower it to make a run pass.
  Upstream's rule here read "must stay at 100%", which was false in this tree for an unknown length
  of time and enforced by nothing. Use `# pragma: no cover` only where a test would demand
  excessive mocking (TYPE_CHECKING blocks, error handlers needing failure injection,
  runtime-mode-dependent paths). **`*/cli/**` is no longer omitted** (removed 2026-08-07): this
  fork's verbs land in `cli/`, and omitting it would make them invisible to coverage on the day
  they ship. See `GAPS.md` T20.

### Async Client Pattern

MCP tools use `get_project_client()`; CLI commands and non-project-scoped code use `get_client()`.

```python
from basic_memory.mcp.project_context import get_project_client

@mcp.tool()
async def my_tool(project: str | None = None, context: Context | None = None):
    async with get_project_client(project, context) as (client, active_project):
        return await call_get(client, "/path")
```

```python
from basic_memory.mcp.async_client import get_client

async with get_client() as client:              # or get_client(project_name="research")
    return await call_get(client, "/path")
```

Auth happens at client creation, not per-request, and the context manager owns the lifecycle. Do
**not** use the deprecated module-level `async_client.client`, hand-rolled auth headers, or a
separate `get_client()` + `get_active_project()` pair inside an MCP tool.

## BASIC MEMORY PRODUCT USAGE

### Knowledge Structure

- Project: The knowledge and isolation boundary for entities, graph state, and search
- Note: A user-facing Markdown document and the canonical representation of its knowledge
- Entity: The project-scoped indexed representation of a file or resource
- Observation: A categorized fact about an entity (`- [category] content`)
- Relation: A directed semantic link owned by its source entity (`- relation_type [[Target]]`)
- Frontmatter: YAML metadata at the top of markdown files
- Knowledge representation follows precise markdown format:
    - Observations with [category] prefixes
    - Relations with WikiLinks [[Entity]]
    - Frontmatter with metadata

### Basic Memory Commands

- Sync status: `basic-memory status` · corpus check: `basic-memory doctor` (integrity and
  hygiene, `--only <group>`, exit 1 on integrity issues, `--strict` for any; `--self-test` checks the
  file ↔ DB loop instead)
- Projects: `project list` / `project ls` / `project add "name" ~/path` / `project info` /
  `project default` / `project move` / `project remove`
- Config: `config list` (effective values, env overrides marked) / `config get <key>` /
  `config set <key> <value>` (validated through the config model) / `config unset <key>`
- MCP tools from the shell: `basic-memory tool <tool-name>` — e.g.
  `basic-memory tool search-notes "sqlite"`. Note this path imports the MCP tool
  layer and costs ~4 s; see the measured baseline above.
- Importers: `import claude conversations` / `import chatgpt` / `import memory-json` — all strip
  candidates.

### MCP Capabilities

Tools live in `src/basic_memory/mcp/tools/`, prompts in `src/basic_memory/mcp/prompts/`. Tools
should be atomic and composable. Read the module for exact signatures — the surface is:

- **Content** — `write_note`, `read_note`, `read_content` (raw bytes, no graph processing),
  `view_note`, `edit_note` (append / prepend / find-replace / replace_section), `move_note`,
  `delete_note`
- **Graph navigation** — `build_context` (memory:// URLs), `recent_activity`, `list_directory`
- **Search** — `search_notes`
- **Projects** — `list_memory_projects`, `create_memory_project`, `delete_project`
- **ChatGPT-compatible** — `search`, `fetch`
- **Prompts** — `ai_assistant_guide`, `continue_conversation`, `search`, `recent_activity`
