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
- **Divergence is the goal, not a cost.** A test that only guards an upstream-shaped decision we
  have deliberately reversed can be rewritten.
- **Don't spend tokens on code we will never run.** Reading, testing, or "keeping consistent" a
  subsystem this fork doesn't use is wasted work. Deleting it is usually the better answer.

Forked from upstream `main` @ `232f2c2fc4e91564d88bcc312ed3d8bd1e8e051b` (2026-07-26). That SHA
records provenance only — it is not a merge base and nothing needs to update it. The `upstream`
remote is fetch-only (push URL `DISABLED`) for as-needed lookups: `git log 232f2c2..upstream/main`
to see if they already diagnosed something, `git show <sha> -- src/basic_memory` to lift one fix.

**`basic-memory --version` lies.** It reads a hardcoded string that only moves on release, so both
upstream `main` and this tree self-report `0.22.1`. The *package* version is derived from git and is
reliable — use `uv tool list` (or `pip show basic-memory`) and read the `+<sha>` suffix.

## What this fork is for

Building a local work-tracking system directly into this codebase as first-class `bm`
subcommands, **not** as a separate wrapper tool.

Planned, in rough dependency order:

1. **A gardener** (`bm gc`) that keeps the note corpus from rotting. Strictly lossless: it may move,
   index, dedupe, re-label, and flag — it may never summarize, merge, or resolve. Ship the flag-only
   version first so the lossless constraint is structural rather than aspirational.
2. **Local git history on writes.** Every mutation commits into a local-only store repo so pruning is
   recoverable. Two traps: set `core.excludesFile` and `core.hooksPath` to `/dev/null` inside that
   repo (a global pre-commit hook will otherwise block automated commits), and never export `GIT_DIR`.
3. **A closed record vocabulary.** Humans extend the vocabulary; agents may only select from it.
   Upstream's frontmatter vocabulary is fully open and does not enforce this, so enforcement is ours
   and has to live in the write path.
4. **A decision-mining subcommand** over Claude Code transcripts, to recover decisions that were made
   in conversation and never written down.

The store is central and id-keyed (not one repo per project): a single plain git repo at
**`~/.basic-memory/store/`**, with the id written once into a **`.bm.yml`** marker at each project
root. The id is authoritative; any directory name in the store is a human-browsing label that
nothing reads. The store path must derive from `resolve_data_dir()` so it honours
`BASIC_MEMORY_CONFIG_DIR` like `config.json` and `memory.db` do — never hardcode it.

**Naming:** `tend` is a **codename for the design, not a command.** There is no `tend` binary and no
`bm tend` namespace — the verbs ship flat under `bm` (`bm gc`, `bm check`, `bm ls`, `bm new`,
`bm path`, `bm mine`, `bm done`, `bm show`, `bm history`, `bm undo`, `bm mark`).

## Measured baseline at the fork point

Linux/x86-64, Python 3.13, semantic search enabled, 67-file / 888 KB corpus. Re-measure if the tree
moves substantially.

| Path | Wall time | Resident memory |
|---|---|---|
| MCP server, idle → embeddings loaded | 40–80 ms per query | 184 MB → ~477 MB |
| CLI `bm tool search-notes` | 4.3–4.8 s | ~447 MB |
| CLI native cmd (`project list`, `config get`) | ~0.55 s | ~73 MB |
| CLI `--version` floor | 0.33 s | 59 MB |
| Full reindex + embed, 67 files | 81 s | 762 MB peak |

The decisive structural fact: **commands that avoid importing `basic_memory.mcp.tools` /
`basic_memory.api.app` cost ~0.55 s; commands that touch them cost ~4 s.** Those two modules are
~2.1 s and ~2.2 s of import time each. **Any `tend` subcommand that needs to be fast must talk to
the repository/service layer directly and must not reach through the MCP tool layer.**

Embedding model `qdrant/bge-small-en-v1.5-onnx-q` (64 MB) caches to the shared
`$XDG_CACHE_HOME/fastembed` in this fork, not inside `BASIC_MEMORY_CONFIG_DIR` as upstream had it —
it's an immutable artifact keyed by model name and doesn't belong inside that isolation boundary.
See `default_fastembed_cache_dir()` in `src/basic_memory/config_models.py`.

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
- Local consistency check: `just doctor` — end-to-end file ↔ DB loop in a temp project. Runs with a
  temporary HOME/config so it won't touch your local settings; leaves temp dirs in `/tmp`.
- Lint: `just lint` · format: `just format` · typecheck: `just typecheck` (or `just typecheck-pyright`)
- All static checks: `just check` · DB migration: `just migration "message"`
- Coverage HTML: `just coverage` · MCP smoke: `just test-smoke` · MCP Inspector: `just run-inspector`
- Benchmarks: `uv run pytest test-int/test_sync_performance_benchmark.py -v -m "benchmark and not slow"`

Python 3.12+ required (type parameter syntax and `type` aliases).

**The loop:** code → `just fast-check` → `just fast-test` (or a targeted `uv run pytest`) →
`just doctor`. Widen to `just test-sqlite` when the change warrants it. A cold testmon run is slow,
later ones aren't; testmon collecting 0 tests means nothing was impacted, not that it failed.

**Postgres variants** (`just test-postgres`, `test-unit-postgres`, `test-int-postgres`) use
testcontainers and need Docker. Required only while the Postgres backend survives — see Strip policy.

**Releases** are a git tag and nothing else — nothing is published anywhere. `just gate` is the
pre-push check (lint + typecheck + unit tests); `just release vX.Y.Z` tags and pushes; `just
release-preview vX.Y.Z` shows what it would do. See `.forked/release-design.md` for why there is
no PyPI, npm, Homebrew, GitHub Release, or CI.

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
- `/picoschema` - Picoschema parsing, resolution, validation, inference, and drift
- `/repository` - Data access layer
- `/schemas` - Pydantic models for validation
- `/services` - Business logic layer
- `/index` - Local runtime indexing adapters, watch service + `watch_coordinator.py` for lifecycle management
- `/indexing` - Portable indexing runners and planners shared by local and hosted runtimes
- `/runtime` - RuntimeMode resolution + runtime Protocol contracts

**Composition Roots:**
Each entrypoint (API, MCP, CLI) has a composition root that:
- Reads `ConfigManager` (the only place that reads global config)
- Resolves runtime mode via `RuntimeMode` enum (TEST > CLOUD > LOCAL)
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
- By default, tests run against SQLite (fast, no Docker needed)
- Set `BASIC_MEMORY_TEST_POSTGRES=1` to run against Postgres (uses testcontainers - Docker required)
- Each test runs in a standalone environment with isolated database and tmp_path directory
- CI runs SQLite and Postgres tests in parallel for faster feedback
- Performance benchmarks are in `test-int/test_sync_performance_benchmark.py`
- Use pytest markers: `@pytest.mark.benchmark` for benchmarks, `@pytest.mark.slow` for slow tests
- **Coverage must stay at 100%**: Write tests for new code. Only use `# pragma: no cover` when tests would require excessive mocking (e.g., TYPE_CHECKING blocks, error handlers that need failure injection, runtime-mode-dependent code paths)

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

- Sync status: `basic-memory status` · file ↔ DB check: `basic-memory doctor`
- Projects: `project list` / `project add "name" ~/path` / `project info` / `project check`
- Config: `config list` (effective values, env overrides marked) / `config get <key>` /
  `config set <key> <value>` (validated through the config model) / `config unset <key>`
- MCP tools from the shell: `basic-memory tool <tool-name>` — e.g.
  `basic-memory tool continue-conversation --topic="search"`. Note this path imports the MCP tool
  layer and costs ~4 s; see the measured baseline above.
- Importers: `import claude conversations` / `import chatgpt` / `import memory-json` — all strip
  candidates.

Cloud commands (`cloud login`, `cloud sync`, `cloud bisync`, `project set-cloud`, …) exist upstream
and are scheduled for deletion here. Don't build on them.

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
