"""Configuration management for basic-memory."""

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Literal, Optional

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from basic_memory.config_migrations import (
    drop_retired_config_keys,
    migrate_legacy_sync_fields,
)
from basic_memory.utils import generate_permalink


DATABASE_NAME = "memory.db"
APP_DATABASE_NAME = "memory.db"  # Using the same name but in the app directory
DATA_DIR_NAME = "basic-memory"
CONFIG_FILE_NAME = "config.json"
WATCH_STATUS_JSON = "watch-status.json"
CONFIG_DIR_MODE = 0o700
CONFIG_FILE_MODE = 0o600

Environment = Literal["test", "dev", "user"]


def _secure_config_dir(path: Path) -> None:
    """Restrict config directory permissions on platforms with POSIX modes."""
    if os.name != "nt":
        path.chmod(CONFIG_DIR_MODE)


def _secure_config_file(path: Path) -> None:
    """Restrict config file permissions because config can contain API credentials."""
    if os.name != "nt":
        path.chmod(CONFIG_FILE_MODE)


def _default_semantic_search_enabled() -> bool:
    """Enable semantic search by default when required local semantic dependencies exist."""
    required_modules = ("fastembed", "sqlite_vec")
    return all(
        importlib.util.find_spec(module_name) is not None for module_name in required_modules
    )


def resolve_data_dir() -> Path:
    """Resolve the Basic Memory data directory.

    Single source of truth for the per-user state directory, and the rule any
    script that reads the store has to follow (GAPS U21). In order:
    ``BASIC_MEMORY_CONFIG_DIR``, so each process/worktree can isolate config and
    database state; then ``$XDG_CONFIG_HOME/basic-memory`` when that variable is
    set; then ``<user home>/.basic-memory``. ``XDG_CONFIG_HOME`` does **not**
    fall back to ``~/.config`` — an unset variable means the home directory
    branch, not the XDG default.

    Cross-platform: ``Path.home()`` reads ``$HOME`` on POSIX and
    ``%USERPROFILE%`` on Windows, so there's no need to check ``$HOME``
    explicitly here.
    """
    if basic_memory_dir := os.getenv("BASIC_MEMORY_CONFIG_DIR"):
        return Path(basic_memory_dir)
    if xdg_config := os.getenv("XDG_CONFIG_HOME"):
        return Path(xdg_config) / DATA_DIR_NAME
    return Path.home() / ("." + DATA_DIR_NAME)


def shared_fastembed_cache_dir() -> Path:
    """Return the user-level FastEmbed cache shared by every config dir.

    Follows the XDG base-directory convention. Cache data is explicitly *not*
    config or state: it is re-downloadable, so it does not belong under the
    per-instance data dir that ``BASIC_MEMORY_CONFIG_DIR`` isolates.
    """
    if xdg_cache := os.getenv("XDG_CACHE_HOME"):
        return Path(xdg_cache) / "fastembed"
    return Path.home() / ".cache" / "fastembed"


def default_fastembed_cache_dir() -> str:
    """Return the default cache directory used for FastEmbed model artifacts.

    Resolution order:
      1. ``FASTEMBED_CACHE_PATH`` env var — honors FastEmbed's own convention
         so users who already configure it through the environment keep working.
      2. ``<XDG cache home>/fastembed`` — one shared cache for the whole user.
      3. ``<basic-memory data dir>/fastembed_cache`` — the legacy per-config-dir
         location, used only when a model was already downloaded there.

    Why shared rather than per-config-dir?
      ``BASIC_MEMORY_CONFIG_DIR`` isolates config, database, and state so that
      several instances (a real store plus test or lab dirs) can coexist. The
      embedding model is none of those — it is a 64 MB immutable artifact keyed
      by model name — so scoping it per config dir made every additional
      instance re-download and re-store the same bytes.

    Why not ``tempfile.gettempdir()``?
      FastEmbed's own default is ``<system tmp>/fastembed_cache``, which is
      ephemeral in many sandboxed MCP runtimes (e.g. Codex CLI wipes /tmp
      between invocations). The model then disappears and every subsequent
      ONNX load raises ``NO_SUCHFILE``. Both paths below are persistent and
      work identically on macOS, Linux, and Windows.
    """
    if env_override := os.getenv("FASTEMBED_CACHE_PATH"):
        return env_override

    shared = shared_fastembed_cache_dir()

    # Trigger: an install that predates the shared cache, whose model already
    # lives under the data dir.
    # Why: re-pointing it at an empty shared cache would silently re-download
    # 64 MB and orphan the old copy.
    # Outcome: keep using the legacy path until it is gone; new installs and
    # every additional config dir get the shared one.
    if not shared.exists():
        legacy = resolve_data_dir() / "fastembed_cache"
        if legacy.exists():
            return str(legacy)

    return str(shared)


@dataclass
class ProjectConfig:
    """Configuration for a specific basic-memory project."""

    name: str
    home: Path

    @property
    def project(self):
        return self.name  # pragma: no cover

    @property
    def project_url(self) -> str:  # pragma: no cover
        return f"/{generate_permalink(self.name)}"


def is_locally_syncable(project_path: str) -> bool:
    """Whether a project path can be synced/watched on the local filesystem.

    An empty or relative path resolves against the process cwd, so syncing it
    would adopt whatever directory the server was launched from as the project
    root and mutate unrelated files (issue #949). A project entry whose path is
    a bare slug rather than a directory is rejected by the same condition.
    """
    return Path(project_path).is_absolute()


def bootstrap_project_home() -> Path:
    """Return the directory a first-run bootstrap project is created in.

    Honors ``BASIC_MEMORY_HOME`` so an install can place its first project
    somewhere other than ``~/basic-memory`` without pre-seeding a registry.
    """
    return Path(os.getenv("BASIC_MEMORY_HOME", Path.home() / "basic-memory"))


class BasicMemoryConfig(BaseSettings):
    """Pydantic model for Basic Memory global configuration.

    Operational settings only. The project registry — which projects exist,
    where they live, and which is default — is owned by the database `project`
    table (GAPS B2); see ``basic_memory.project_registry``. A ``projects`` key
    left behind by an older release is tolerated on load (``extra="ignore"``),
    imported once by ``ensure_project_registry()``, and never written back.
    """

    if TYPE_CHECKING:
        # Pydantic accepts raw constructor data and validates/coerces it at runtime.
        # Model attributes remain strongly typed after initialization.
        def __init__(self, **data: Any) -> None: ...

    env: Environment = Field(default="dev", description="Environment name")

    # overridden by config.json in the data dir (see ``resolve_data_dir`` above)
    log_level: str = "INFO"

    # Semantic search configuration
    semantic_search_enabled: bool = Field(
        default_factory=_default_semantic_search_enabled,
        description="Enable semantic search (vector/hybrid retrieval). Requires semantic dependencies (included by default).",
    )
    semantic_embedding_provider: str = Field(
        default="fastembed",
        description="Embedding provider for local semantic indexing/search.",
    )
    semantic_embedding_model: str = Field(
        default="bge-small-en-v1.5",
        description="Embedding model identifier used by the local provider.",
    )
    semantic_embedding_api_base: str | None = Field(
        default=None,
        description=(
            "Optional custom API base URL for the 'openai' embedding provider. "
            "Set this to point at any OpenAI-compatible embedding server - Ollama, "
            "LM Studio, vLLM, OpenRouter, or a self-hosted gateway."
        ),
    )
    semantic_embedding_api_key: str | None = Field(
        default=None,
        description=(
            "Optional API key passed directly to the 'openai' embedding provider. "
            "When unset, the provider falls back to the OPENAI_API_KEY environment "
            "variable. Local servers that ignore auth still need a placeholder value."
        ),
    )
    semantic_embedding_dimensions: int | None = Field(
        default=None,
        description=(
            "Embedding vector dimensions. Uses provider defaults when unset "
            "(384 for FastEmbed, 1536 for OpenAI); required for any model whose "
            "output size differs from its provider default."
        ),
    )
    # Trigger: full local rebuilds spend most of their time waiting behind shared
    # embed flushes, not constructing vectors themselves.
    # Why: smaller FastEmbed batches cut queue wait far more than they increase
    # write overhead on real-world projects, which makes full reindex materially faster.
    # Outcome: default to the smaller batch size we benchmarked as
    # the current best end-to-end setting in the shared vector sync pipeline.
    semantic_embedding_batch_size: int = Field(
        default=2,
        description="Batch size for embedding generation.",
        gt=0,
    )
    semantic_embedding_request_concurrency: int = Field(
        default=4,
        description="Maximum number of concurrent provider requests for batched embedding generation when the active provider supports request-level concurrency.",
        gt=0,
    )
    semantic_embedding_document_prefix: str | None = Field(
        default=None,
        description=(
            "Optional literal text prefix prepended to indexed document chunks before "
            "embedding. Use with prefix-sensitive asymmetric embedding models."
        ),
    )
    semantic_embedding_query_prefix: str | None = Field(
        default=None,
        description=(
            "Optional literal text prefix prepended to search queries before embedding. "
            "Use with prefix-sensitive asymmetric embedding models."
        ),
    )
    semantic_embedding_sync_batch_size: int = Field(
        default=2,
        description="Batch size for vector sync orchestration flushes.",
        gt=0,
    )
    semantic_embedding_cache_dir: str | None = Field(
        default=None,
        description=(
            "Optional override for the FastEmbed model cache directory. "
            "When unset, Basic Memory resolves this at runtime to the shared "
            "<XDG cache home>/fastembed (or FASTEMBED_CACHE_PATH when that env "
            "var is set, or the legacy <basic-memory data dir>/fastembed_cache "
            "when a model was already downloaded there) so the model persists "
            "across runs, and is shared across config dirs, without hardcoding "
            "a path into config.json."
        ),
    )
    semantic_embedding_threads: int | None = Field(
        default=None,
        description="Optional FastEmbed runtime thread count override.",
        gt=0,
    )
    semantic_embedding_parallel: int | None = Field(
        default=None,
        description="Optional FastEmbed embed() parallelism override.",
        gt=0,
    )
    semantic_vector_k: int = Field(
        default=100,
        description="Vector candidate count for vector and hybrid retrieval.",
        gt=0,
    )
    semantic_min_similarity: float = Field(
        default=0.55,
        description="Minimum similarity score for vector search results. Results below this threshold are filtered out. 0.0 disables filtering.",
        ge=0.0,
        le=1.0,
    )
    default_search_type: Literal["text", "vector", "hybrid"] | None = Field(
        default=None,
        description="Default search type for search_notes when not specified per-query. "
        "Valid values: text, vector, hybrid. "
        "When unset, defaults to 'hybrid' if semantic search is enabled, otherwise 'text'.",
    )

    # SQLite tuning. The index DB is a cache rebuildable from the markdown files
    # (the source of truth), so durability *could* be traded for throughput — but
    # benchmarks showed OFF buys nothing over the NORMAL default (WAL + NORMAL
    # already skips the per-commit fsync), so the safe default stays.
    sqlite_synchronous: Literal["OFF", "NORMAL", "FULL", "EXTRA"] = Field(
        default="NORMAL",
        description="SQLite `PRAGMA synchronous`. Default NORMAL — safe with WAL "
        "(survives app crashes; only an OS crash/power loss can lose the last "
        "transactions). OFF showed no measurable write-throughput gain in "
        "benchmarks (WAL+NORMAL already avoids the per-commit fsync), so it is only "
        "worth setting for callers that knowingly trade durability — the index DB "
        "rebuilds from the markdown files via sync — e.g. a one-off bulk import.",
    )
    sqlite_mmap_size: int = Field(
        default=268435456,  # 256 MB
        description="SQLite `PRAGMA mmap_size` in bytes (0 disables memory-mapped "
        "I/O). Speeds reads — including the lookups inside writes (link/permalink "
        "resolution, FTS).",
        ge=0,
    )
    sqlite_wal_autocheckpoint: int = Field(
        default=1000,
        description="SQLite `PRAGMA wal_autocheckpoint` in pages (0 disables "
        "automatic checkpoints). Higher values checkpoint less often, reducing "
        "writer stalls under sustained bursts at the cost of a larger WAL.",
        ge=0,
    )
    sqlite_page_size: int = Field(
        default=4096,
        description="SQLite `PRAGMA page_size` in bytes (power of two, 512-65536). "
        "Only takes effect on a freshly created database (or after VACUUM).",
        ge=512,
        le=65536,
    )

    # Watch service configuration
    index_delay: int = Field(
        default=1000, description="Milliseconds to wait after changes before indexing", gt=0
    )

    watch_project_reload_interval: int = Field(
        default=300,
        description="Seconds between reloading project list in watch service. Higher values reduce CPU usage by minimizing watcher restarts. Default 300s (5 min) balances efficiency with responsiveness to new projects.",
        gt=0,
    )

    # update permalinks on move
    update_permalinks_on_move: bool = Field(
        default=False,
        description="Whether to update permalinks when files are moved or renamed. default (False)",
    )

    index_changes: bool = Field(
        default=True,
        description="Whether to index local file changes in real time. default (True)",
    )
    index_batch_size: int = Field(
        default=32,
        description="Maximum number of changed files to load into one indexing batch.",
        gt=0,
    )
    index_batch_max_bytes: int = Field(
        default=8 * 1024 * 1024,
        description="Maximum total bytes to load into one indexing batch. Large files still run as single-file batches.",
        gt=0,
    )
    index_parse_max_concurrent: int = Field(
        default=8,
        description="Maximum number of markdown parse tasks to run concurrently inside one indexing batch.",
        gt=0,
    )
    index_entity_max_concurrent: int = Field(
        default=4,
        description="Maximum number of entity create/update tasks to run concurrently inside one indexing batch.",
        gt=0,
    )
    index_metadata_update_max_concurrent: int = Field(
        default=4,
        description="Maximum number of metadata/search refresh tasks to run concurrently inside one indexing batch.",
        gt=0,
    )

    kebab_filenames: bool = Field(
        default=False,
        description="Format for generated filenames. False preserves spaces and special chars, True converts them to hyphens for consistency with permalinks",
    )

    disable_permalinks: bool = Field(
        default=False,
        description="Disable automatic permalink generation in frontmatter. When enabled, new notes won't have permalinks added and sync won't update permalinks. Existing permalinks will still work for reading.",
    )

    write_note_overwrite_default: bool = Field(
        default=False,
        description=(
            "Default value for write_note's overwrite parameter. "
            "When False (default), write_note errors if note already exists. "
            "Set to True to restore pre-v0.20 upsert behavior. "
            "Env: BASIC_MEMORY_WRITE_NOTE_OVERWRITE_DEFAULT"
        ),
    )

    ensure_frontmatter_on_sync: bool = Field(
        default=True,
        description="Ensure markdown files have frontmatter during sync by adding derived title/type/permalink when missing. When combined with disable_permalinks=True, this setting takes precedence for missing-frontmatter files and still writes permalinks.",
    )

    permalinks_include_project: bool = Field(
        default=True,
        description="When True, generated permalinks are prefixed with the project slug (e.g., 'specs/search'). Existing permalinks remain unchanged unless explicitly updated.",
    )

    # File formatting configuration
    format_on_save: bool = Field(
        default=False,
        description="Automatically format files after saving using configured formatter. Disabled by default.",
    )

    formatter_command: Optional[str] = Field(
        default=None,
        description="External formatter command. Use {file} as placeholder for file path. If not set, uses built-in mdformat (Python, no Node.js required). Set to 'npx prettier --write {file}' for Prettier.",
    )

    formatters: Dict[str, str] = Field(
        default_factory=dict,
        description="Per-extension formatters. Keys are extensions (without dot), values are commands. Example: {'md': 'prettier --write {file}', 'json': 'prettier --write {file}'}",
    )

    formatter_timeout: float = Field(
        default=5.0,
        description="Maximum seconds to wait for formatter to complete",
        gt=0,
    )

    # Bug reports (GAPS U34). `bugs_dir` accepts `~`; the user points it at a
    # dotfiles-synced directory to collect reports across machines, and wires
    # `bugs_followup` to their own sync command — bm never knows about the sync.
    bugs_dir: str = Field(
        default="",
        description=(
            "Directory bug reports are written to. Empty means <data-dir>/bugs. "
            "Point it at a synced directory to collect reports across machines."
        ),
    )

    bugs_autocapture: bool = Field(
        default=True,
        description=(
            "File a deduplicated bug report automatically on any nonzero exit or "
            "uncaught crash. Repeats of one failure shape bump a counter, never a new file."
        ),
    )

    bugs_followup: str = Field(
        default="",
        description=(
            "Command run (shell, best-effort, from bugs_dir) after a report is written — "
            "e.g. a dotfiles add-and-commit. Empty disables."
        ),
    )

    # Project path constraints
    project_root: Optional[str] = Field(
        default=None,
        description="If set, all projects must be created underneath this directory. Paths will be sanitized and constrained to this root. If not set, projects can be created anywhere (default behavior).",
    )

    # Legacy config keys / env vars mapped to their renamed fields.
    _LEGACY_SYNC_FIELDS: ClassVar[dict[str, str]] = {
        "index_changes": "sync_changes",
        "index_delay": "sync_delay",
    }

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_sync_fields(cls, data: Any) -> Any:
        """Honor legacy ``sync_changes``/``sync_delay`` after their rename to
        ``index_changes``/``index_delay``.

        Existing configs that set ``"sync_changes": false`` (disable realtime
        indexing) or a custom ``"sync_delay"`` debounce must keep that behavior
        across the rename. ``extra="ignore"`` would otherwise drop the unknown
        keys and fall back to the new defaults (True / 1000ms), silently
        restarting the watcher or speeding up indexing for users who tuned it.
        The new field takes precedence when both are present.

        Both surfaces must migrate: config.json ships the legacy *dict keys*,
        while env-var users set ``BASIC_MEMORY_SYNC_CHANGES`` /
        ``BASIC_MEMORY_SYNC_DELAY``. pydantic-settings ignores those env vars
        (they aren't model fields), and ConfigManager's env-merge loop only
        probes ``BASIC_MEMORY_{new field}`` — so without this the env-var
        rename is silently dropped. Env overrides the legacy file key to match
        normal env > file precedence; the new field name always wins.
        """
        return migrate_legacy_sync_fields(
            data,
            legacy_fields=cls._LEGACY_SYNC_FIELDS,
            env_prefix=str(cls.model_config["env_prefix"]),
        )

    @model_validator(mode="before")
    @classmethod
    def drop_retired_keys(cls, data: Any) -> Any:
        """Drop keys older releases wrote for surfaces this fork has retired.

        Nothing reads them now and leaving them in would resurrect a concept
        that no longer exists. The registry keys (``projects``,
        ``default_project``) are deliberately *not* dropped here: they are
        ignored as extras so a legacy config still parses, and
        ``ensure_project_registry()`` imports them once into the database.
        """
        return drop_retired_config_keys(data)

    @property
    def is_test_env(self) -> bool:
        """Check if running in a test environment.

        Returns True if any of:
        - env field is set to "test"
        - BASIC_MEMORY_ENV environment variable is "test"
        - PYTEST_CURRENT_TEST environment variable is set (pytest is running)

        Used to disable features like file watchers during tests.
        """
        return (
            self.env == "test"
            or os.getenv("BASIC_MEMORY_ENV", "").lower() == "test"
            or os.getenv("PYTEST_CURRENT_TEST") is not None
        )

    model_config = SettingsConfigDict(
        env_prefix="BASIC_MEMORY_",
        extra="ignore",
    )

    @property
    def app_database_path(self) -> Path:
        """Get the path to the app-level database.

        This is the single database that will store all knowledge data
        across all projects.

        Uses BASIC_MEMORY_CONFIG_DIR when set so each process/worktree can
        isolate both config and database state.
        """
        database_path = self.data_dir_path / APP_DATABASE_NAME
        if not database_path.exists():  # pragma: no cover
            database_path.parent.mkdir(parents=True, exist_ok=True)
            database_path.touch()
        return database_path

    @property
    def database_path(self) -> Path:
        """Get SQLite database path.

        Rreturns the app-level database path
        for backward compatibility in the codebase.
        """

        # Load the app-level database path from the global config
        from basic_memory.config import ConfigManager

        config_manager = ConfigManager()
        config = config_manager.load_config()  # pragma: no cover
        return config.app_database_path  # pragma: no cover

    @property
    def data_dir_path(self) -> Path:
        """Get app state directory for config and default SQLite database."""
        return resolve_data_dir()
