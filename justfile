# Basic Memory - Modern Command Runner

PYTEST_FLAGS := env_var_or_default("BASIC_MEMORY_PYTEST_FLAGS", "--import-mode=importlib")
TESTMON_SELECT_FLAGS := env_var_or_default("BASIC_MEMORY_TESTMON_SELECT_FLAGS", "--import-mode=importlib --testmon --testmon-forceselect")
TESTMON_REFRESH_FLAGS := env_var_or_default("BASIC_MEMORY_TESTMON_REFRESH_FLAGS", "--import-mode=importlib --testmon-noselect")

# Install dependencies
install:
    uv sync
    @echo ""
    @echo "💡 Remember to activate the virtual environment by running: source .venv/bin/activate"

# ==============================================================================
# TESTING
# ==============================================================================
# SQLite is the only database backend.
#
# Quick Start:
#   just check             # Run static checks only (fix, format, typecheck)
#   just fast-check        # Fast static check: fix, format, typecheck
#   just fast-test         # Run pytest-testmon impacted tests
#   just test              # Run all tests
#   just test-unit-sqlite  # Run unit tests
#   just test-int-sqlite   # Run integration tests
# ==============================================================================

# Run all tests
test: test-sqlite

# Run all tests against SQLite
test-sqlite: test-unit-sqlite test-int-sqlite

# Run unit tests against SQLite
test-unit-sqlite:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} tests

# Run integration tests against SQLite (excludes semantic tests and on-demand benchmarks —
# use just test-semantic / run benchmark files explicitly)
test-int-sqlite:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} -m "not semantic and not benchmark" test-int

# Fast test selection for local iteration; run targeted tests explicitly when possible.
fast-test *args: testmon-seed
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{TESTMON_SELECT_FLAGS}} --testmon-env=local {{args}}

# Run tests impacted by recent changes (requires pytest-testmon).
# Backcompat alias for the fast-test recipe.
testmon *args:
    just fast-test {{args}}

# Seed pytest-testmon data into this worktree from the shared Git cache.
testmon-seed:
    uv run python scripts/testmon_cache.py seed

# Refresh the shared pytest-testmon cache from a full backend test run.
testmon-refresh:
    #!/usr/bin/env bash
    set -euo pipefail
    BASIC_MEMORY_PYTEST_FLAGS="{{TESTMON_REFRESH_FLAGS}}" just test
    uv run python scripts/testmon_cache.py refresh

# Show local and shared pytest-testmon cache locations.
testmon-status:
    uv run python scripts/testmon_cache.py status

# Run MCP smoke test (fast end-to-end loop)
test-smoke:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} -m smoke test-int/mcp/test_smoke_integration.py

# Fast static check: auto-fix lint, format, and typecheck, but do not run tests.
fast-check:
    just fix
    just format
    just typecheck

# Fast local loop with live OpenAI-backed checks disabled.
fast-check-no-openai:
    OPENAI_API_KEY= just fast-check

# ==============================================================================
# Runtime / Event Indexing Refactor
# ==============================================================================

# Focused portable storage-event contract tests.
storage-event-contract-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/test_runtime_storage_events.py \
        tests/index/test_storage_event_operation_processor.py \
        tests/index/test_storage_event_orchestration.py

# Focused provider-neutral project-index orchestration surface tests.
project-index-surface-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_project_index_surface.py

# Focused provider-neutral project-index workflow tests.
project-index-workflow-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_project_index_workflow.py

# Focused provider-neutral project-index coordinator tests.
project-index-runner-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_project_index_runner.py

# Focused provider-neutral change-planning tests.
change-planning-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_change_planning.py

# Focused local project-index adapter tests.
local-project-index-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py

# Focused local project-index scan parity tests.
local-project-index-scan-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_scan_parity.py

# Focused local project-index directory delete parity test.
local-project-index-directory-delete-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_directory_delete_removes_notes_and_repairs_survivors

# Focused local project-index hidden-file parity test.
local-project-index-hidden-file-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_skips_hidden_markdown_files

# Focused local project-index null-checksum repair parity test.
local-project-index-null-checksum-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_repairs_null_checksum_entities

# Focused local project-index file timestamp parity tests.
local-project-index-timestamp-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_uses_file_mtime_for_new_markdown_entities \
        tests/index/test_local_project_index.py::test_local_project_index_updates_entity_mtime_on_file_modification

# Focused local project-index regular-file parity tests.
local-project-index-regular-file-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_indexes_regular_files \
        tests/index/test_local_project_index.py::test_local_project_index_updates_regular_file_checksum \
        tests/index/test_local_project_index.py::test_local_project_index_moves_and_deletes_regular_file_entities \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_regular_file_relations

# Focused local project-index markdown move conflict parity test.
local-project-index-markdown-move-conflict-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_moves_markdown_over_deleted_path_with_permalink_repair

# Focused local project-index changed-during-index parity test.
local-project-index-race-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_reads_current_file_when_file_changes_after_observation

# Focused local project-index duplicate permalink parity test.
local-project-index-permalink-conflict-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_duplicate_permalink_update

# Focused local project-index new duplicate permalink parity test.
local-project-index-new-permalink-conflict-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_new_duplicate_permalink

# Focused local project-index path-derived permalink conflict parity test.
local-project-index-path-conflict-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_assigns_unique_permalinks_for_path_conflicts

# Focused local project-index frontmatter policy parity tests.
local-project-index-frontmatter-policy-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_does_not_add_frontmatter_when_disabled \
        tests/index/test_local_project_index.py::test_local_project_index_indexes_thematic_break_content_without_frontmatter \
        tests/index/test_local_project_index.py::test_local_project_index_writes_frontmatter_when_enabled_even_if_permalinks_disabled

# Focused local project-index thematic-break frontmatter parity test.
local-project-index-thematic-break-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_indexes_thematic_break_content_without_frontmatter

# Focused local project-index relation resolution parity test.
local-project-index-relation-parity-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_resolves_order_dependent_relations_after_batches \
        tests/index/test_local_project_index.py::test_local_project_index_deduplicates_relations_by_type

# Focused local project-index observation category parity test.
local-project-index-observation-category-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_preserves_loose_observation_categories

# Focused local project-index wikilink stability parity test.
local-project-index-wikilink-stability-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_project_index.py::test_local_project_index_keeps_wikilink_source_stable_when_target_appears

# Focused per-file indexing runner/model tests.
file-index-runner-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_index_file_runner.py \
        tests/indexing/test_file_indexer.py \
        tests/indexing/test_models.py

# Focused file-batch indexing runner/payload tests.
file-index-batch-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_file_batch_runner.py \
        tests/indexing/test_job_payloads.py

# Focused batch-index semantic dependency parity test.
file-index-semantic-dependency-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_batch_indexer.py::test_batch_indexer_keeps_file_indexed_when_semantic_dependencies_are_missing

# Focused startup wiring for local project-index fanout.
local-project-index-startup-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/services/test_initialization.py::test_initialize_file_indexing_uses_project_index_runtime_for_initial_sync_by_default

# Focused CLI project-index surface tests.
project-index-cli-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/cli/test_db_reindex.py \
        tests/cli/test_status_wait_timeout.py

# Focused project-wide indexing orchestration surface tests.
project-index-contract-test: project-index-surface-test project-index-workflow-test project-index-runner-test change-planning-test local-project-index-test local-project-index-scan-test local-project-index-markdown-move-conflict-test local-project-index-new-permalink-conflict-test local-project-index-path-conflict-test local-project-index-thematic-break-test local-project-index-observation-category-test local-project-index-wikilink-stability-test local-project-index-startup-test project-index-cli-test

# Focused local event-index regular-file parity tests.
local-event-index-regular-file-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_regular_file_parity.py

# Focused local event-index relation cleanup parity test.
local-event-index-relation-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_regular_file_parity.py::test_local_event_index_deletes_regular_file_relation_target_and_repairs_search

# Focused local event-index atomic-write parity test.
local-event-index-atomic-write-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_stress_parity.py::test_local_event_index_handles_rapid_atomic_writes_to_same_file

# Focused local filesystem event temp/backup filtering parity test.
filesystem-event-temp-file-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_filesystem_events.py::test_editor_swap_and_backup_changes_are_filtered_before_indexing

# Focused local event-index larger watcher batch parity tests.
local-event-index-stress-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/index/test_local_watch_stress_parity.py

# Focused event-based indexing contract tests: storage events, filesystem events,
# watch parity, and event-index startup wiring.
event-index-contract-test: storage-event-contract-test filesystem-event-temp-file-test local-event-index-atomic-write-test local-event-index-stress-test
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_external_file_delete_runner.py \
        tests/index/test_filesystem_events.py \
        tests/index/test_inline_storage_event_processor.py \
        tests/index/test_local_watch_ignore_parity.py \
        tests/index/test_local_watch_regular_file_parity.py \
        tests/index/test_local_watch_orchestration.py \
        tests/index/test_repository_storage_event_project_resolution.py \
        tests/services/test_initialization.py::test_initialize_file_indexing_wires_event_index_runtime_by_default

# Focused parity loop for local project scans and shared storage-event routing.
event-index-parity-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/test_runtime.py::TestRuntimeContracts::test_runtime_storage_event_operation_plans_index_delete_and_skip_work \
        tests/test_runtime_observed_index_files.py \
        tests/index/test_local_project_index.py \
        tests/index/test_filesystem_events.py \
        tests/index/test_storage_event_operation_processor.py \
        tests/index/test_storage_event_orchestration.py

# Full indexing contract suite: per-file, project-wide, and event-based indexing.
index-contract-test: file-index-runner-test file-index-batch-test file-index-semantic-dependency-test project-index-contract-test event-index-contract-test

# Run pytest in the test environment with arbitrary arguments passed through.
runtime-core-pytest *args:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov {{args}}

# Focused PR #1002 Codex feedback regressions.
pr-1002-feedback-test:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED=true uv run pytest -p pytest_mock -q --no-cov \
        tests/runtime/test_deleted_note_response.py \
        tests/repository/test_accepted_note_search_repository.py \
        tests/indexing/test_project_index_workflow.py \
        tests/indexing/test_accepted_note_write_runner.py \
        tests/indexing/test_directory_delete_runner.py

runtime-refactor-contract-test:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -q --no-cov \
        tests/indexing/test_accepted_note_write_runner.py \
        tests/indexing/test_accepted_note_enqueue_runner.py \
        tests/indexing/test_note_content_read_repair_runner.py \
        tests/runtime/test_accepted_note_response_planning.py \
        tests/runtime/test_deleted_note_response.py \
        tests/runtime/test_pending_note_materialization.py \
        tests/runtime/test_note_content_read_planning.py
    just index-contract-test

# Run Windows-specific tests only (only works on Windows platform)
# These tests verify Windows-specific database optimizations (locking mode, NullPool)
# Will be skipped automatically on non-Windows platforms
test-windows:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} -m windows tests test-int

# Run benchmark tests only (performance testing)
# These are slow tests that measure sync performance with various file counts
# Excluded from default test runs to keep CI fast
test-benchmark:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} -m benchmark tests test-int

# Run semantic search quality benchmarks (all combos)
test-semantic:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} -m semantic test-int/semantic/

# Run semantic benchmarks with JSON artifact output, then show report
test-semantic-report:
    BASIC_MEMORY_ENV=test BASIC_MEMORY_BENCHMARK_OUTPUT=.benchmarks/semantic-quality.jsonl uv run pytest -p pytest_mock -v -s --no-cov -m semantic test-int/semantic/
    uv run python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl

# View semantic benchmark results (rich formatted table)
# Usage: just semantic-report [--filter-combo sqlite] [--filter-suite paraphrase] [--sort-by avg_latency_ms]
semantic-report *args:
    uv run python test-int/semantic/report.py .benchmarks/semantic-quality.jsonl {{args}}

# Compare two search benchmark JSONL outputs
# Usage:
#   just benchmark-compare .benchmarks/search-baseline.jsonl .benchmarks/search-candidate.jsonl
#   just benchmark-compare .benchmarks/search-baseline.jsonl .benchmarks/search-candidate.jsonl --format markdown --show-missing
benchmark-compare baseline candidate *args:
    uv run python test-int/compare_search_benchmarks.py "{{baseline}}" "{{candidate}}" --format table {{args}}

# Run all tests including Windows and Benchmarks (comprehensive testing)
# Use this before releasing to ensure everything works across platforms
test-all:
    BASIC_MEMORY_ENV=test uv run pytest -p pytest_mock -v --no-cov {{PYTEST_FLAGS}} tests test-int

# Generate HTML coverage report
coverage:
    #!/usr/bin/env bash
    set -euo pipefail
    
    uv run coverage erase
    
    echo "🔎 Coverage (SQLite)..."
    BASIC_MEMORY_ENV=test uv run coverage run --source=basic_memory -m pytest -p pytest_mock -v --no-cov tests test-int

    # coverage.run sets parallel = true, so the run above still emits per-process
    # data files that have to be combined before reporting.
    echo "🧩 Combining coverage data..."
    uv run coverage combine
    uv run coverage report -m
    uv run coverage html
    echo "Coverage report generated in htmlcov/index.html"

# Lint and fix code (calls fix)
lint: fix

# Lint and fix code
fix:
    uv run ruff check --fix --unsafe-fixes src tests test-int

# Type check code (ty)
typecheck:
    uv run ty check src tests test-int

# Type check code (pyright)
typecheck-pyright:
    uv run pyright

# Type check code (ty)
typecheck-ty:
    just typecheck

# Clean build artifacts and cache files
clean:
    find . -type f -name '*.pyc' -delete
    find . -type d -name '__pycache__' -exec rm -r {} +
    rm -rf installer/build/ installer/dist/ dist/
    rm -f rw.*.dmg .coverage.*

# Format code with ruff
format:
    uv run ruff format .

# Run MCP inspector tool
run-inspector:
    npx @modelcontextprotocol/inspector

# Run doctor checks in an isolated temp home/config
doctor:
    #!/usr/bin/env bash
    set -euo pipefail
    TMP_HOME=$(mktemp -d)
    TMP_CONFIG=$(mktemp -d)
    HOME="$TMP_HOME" \
    BASIC_MEMORY_ENV=test \
    BASIC_MEMORY_HOME="$TMP_HOME/basic-memory" \
    BASIC_MEMORY_CONFIG_DIR="$TMP_CONFIG" \
    ./.venv/bin/python -m basic_memory.cli.main doctor


# Update all dependencies to latest versions
update-deps:
    uv sync --upgrade

# Run static code quality checks. Use `just test` for the actual test suites.
check: lint format typecheck

# Run all code quality checks and all test suites, including semantic benchmarks
check-all: lint format typecheck test test-semantic

# Generate Alembic migration with descriptive message
migration message:
    cd src/basic_memory/alembic && alembic revision --autogenerate -m "{{message}}"


# --- Release ---
# A release here is a git tag and nothing else. Nothing is published: the
# installable artifact is this checkout, and uv-dynamic-versioning derives the
# package version from the tag at install time. See .forked/release-design.md.

# The one gate. Run before pushing anything you would be sad to break.
gate:
    just lint
    just typecheck
    just test-unit-sqlite

# Cut a release tag (e.g. just release v0.23.0)
release version:
    #!/usr/bin/env bash
    set -euo pipefail

    if [[ ! "{{version}}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Invalid version. Use: v0.23.0"
        exit 1
    fi
    VERSION_NUM=$(echo "{{version}}" | sed 's/^v//')

    # Trigger: dirty tree, wrong branch, or a tag that already exists.
    # Why: a tag must name a state reachable from the remote; a dirty tree
    # would tag code nobody else can get. This fork has one line of history.
    # Outcome: abort before anything is written.
    [[ -z "$(git status --porcelain)" ]] || { echo "Uncommitted changes."; exit 1; }
    [[ "$(git branch --show-current)" == "main" ]] || { echo "Not on main."; exit 1; }
    ! git rev-parse -q --verify "refs/tags/{{version}}" >/dev/null \
        || { echo "Tag {{version}} already exists."; exit 1; }

    just gate

    # Trigger: __version__ in src/basic_memory/__init__.py is a hardcoded
    # literal that only moves on release.
    # Why: `basic-memory --version` reads it, so tagging without this bump makes
    # --version report the previous release (upstream hit exactly this on
    # v0.21.2 -> v0.21.3).
    # Outcome: one file changes. Delete this block once __version__ is derived
    # from importlib.metadata, at which point this recipe writes no files.
    sed -i -E "s/^__version__ = \".*\"$/__version__ = \"${VERSION_NUM}\"/" \
        src/basic_memory/__init__.py
    git diff --quiet src/basic_memory/__init__.py \
        || git commit -m "chore: version ${VERSION_NUM}" src/basic_memory/__init__.py

    git tag -a "{{version}}" -m "{{version}}"
    git push origin main "{{version}}"

    echo
    echo "Tagged {{version}}. Nothing was published - that is correct."
    echo "Install/upgrade locally:"
    echo "    uv tool install --reinstall $(pwd)"
    echo "On another machine:"
    echo "    uv tool install --reinstall 'git+https://github.com/noahkiss/basic-memory@{{version}}'"
    echo
    echo "Migrations auto-apply on first run. Before upgrading across a schema"
    echo "change, snapshot the index:  cp ~/.basic-memory/memory.db{,.bak}"
    echo "Recovery if the index goes bad:  bm reset --reindex"

# Show what `just release` would tag, without writing or pushing anything
release-preview version:
    #!/usr/bin/env bash
    set -euo pipefail
    # `skills-latest` is a moving tag left by the deleted publish-skills
    # workflow and sorts newer than every version tag, so an unfiltered
    # describe reports nonsense. Match only release tags.
    echo "would tag:    {{version}}"
    echo "at commit:    $(git rev-parse --short HEAD)"
    echo "current desc: $(git describe --tags --always --dirty --match 'v[0-9]*')"
    echo "__version__:  $(grep -m1 '^__version__' src/basic_memory/__init__.py | cut -d'"' -f2)"

# List all available recipes
default:
    @just --list
