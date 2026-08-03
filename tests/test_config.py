"""Test configuration management."""

import os
import stat
import tempfile
import pytest
from typing import Any, cast

from basic_memory.config import (
    BasicMemoryConfig,
    ConfigManager,
    default_fastembed_cache_dir,
    resolve_data_dir,
)
from basic_memory.config_migrations import drop_retired_config_keys, normalize_legacy_projects
from basic_memory.config_models import is_locally_syncable
from pathlib import Path


def _migrate_legacy_sync_changes(data: Any) -> Any:
    return cast(Any, BasicMemoryConfig.migrate_legacy_sync_fields)(data)


class TestLegacySyncChangesMigration:
    """Backward compatibility for the sync_changes/sync_delay renames."""

    def test_legacy_sync_changes_false_disables_indexing(self):
        assert _migrate_legacy_sync_changes({"sync_changes": False})["index_changes"] is False

    def test_legacy_sync_changes_true_enables_indexing(self):
        assert _migrate_legacy_sync_changes({"sync_changes": True})["index_changes"] is True

    def test_new_index_changes_takes_precedence(self):
        data = _migrate_legacy_sync_changes({"sync_changes": False, "index_changes": True})
        assert data["index_changes"] is True

    def test_absent_legacy_key_is_left_untouched(self):
        assert "index_changes" not in _migrate_legacy_sync_changes({})

    def test_non_dict_input_passes_through(self):
        assert _migrate_legacy_sync_changes("not-a-dict") == "not-a-dict"

    def test_legacy_sync_delay_migrates_to_index_delay(self):
        assert _migrate_legacy_sync_changes({"sync_delay": 5000})["index_delay"] == 5000

    def test_new_index_delay_takes_precedence(self):
        data = _migrate_legacy_sync_changes({"sync_delay": 5000, "index_delay": 2000})
        assert data["index_delay"] == 2000

    def test_constructed_config_honors_legacy_opt_out(self):
        config = BasicMemoryConfig(
            env="test",
            projects={"main": {"path": "/tmp/legacy"}},
            default_project="main",
            sync_changes=False,
        )
        assert config.index_changes is False

    def test_constructed_config_honors_legacy_sync_delay(self):
        config = BasicMemoryConfig(
            env="test",
            projects={"main": {"path": "/tmp/legacy"}},
            default_project="main",
            sync_delay=5000,
        )
        assert config.index_delay == 5000

    def test_legacy_sync_changes_env_maps_to_index_changes(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_SYNC_CHANGES", "false")
        assert _migrate_legacy_sync_changes({})["index_changes"] == "false"

    def test_legacy_sync_env_overrides_legacy_file_key(self, monkeypatch):
        # env > file precedence, matching how the new fields resolve in ConfigManager
        monkeypatch.setenv("BASIC_MEMORY_SYNC_DELAY", "9000")
        assert _migrate_legacy_sync_changes({"sync_delay": 5000})["index_delay"] == "9000"

    def test_constructed_config_honors_legacy_sync_changes_env(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_SYNC_CHANGES", "false")
        config = BasicMemoryConfig(
            env="test",
            projects={"main": {"path": "/tmp/legacy"}},
            default_project="main",
        )
        assert config.index_changes is False

    def test_constructed_config_honors_legacy_sync_delay_env(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_SYNC_DELAY", "5000")
        config = BasicMemoryConfig(
            env="test",
            projects={"main": {"path": "/tmp/legacy"}},
            default_project="main",
        )
        assert config.index_delay == 5000

    def test_new_index_env_takes_precedence_over_legacy_env(self, monkeypatch):
        monkeypatch.setenv("BASIC_MEMORY_SYNC_CHANGES", "false")
        monkeypatch.setenv("BASIC_MEMORY_INDEX_CHANGES", "true")
        config = BasicMemoryConfig(
            env="test",
            projects={"main": {"path": "/tmp/legacy"}},
            default_project="main",
        )
        assert config.index_changes is True


class TestBasicMemoryConfig:
    """Test BasicMemoryConfig behavior unrelated to the project registry.

    The project registry (which projects exist, their paths, the default) is
    owned by the database (GAPS B2) — see ``tests/services/test_initialization.py``
    for ``ensure_project_registry`` coverage and ``tests/test_config.py``'s
    ``TestNormalizeLegacyProjects``/``TestLegacyProjectsToleratedButNotPersisted``
    for the config-side migration/tolerance behavior.
    """

    def test_app_database_path_uses_custom_config_dir(self, tmp_path, monkeypatch):
        """Default SQLite DB should live under BASIC_MEMORY_CONFIG_DIR when set."""
        custom_config_dir = tmp_path / "instance-a" / "state"
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_config_dir))

        config = BasicMemoryConfig()

        assert config.data_dir_path == custom_config_dir
        assert config.app_database_path == custom_config_dir / "memory.db"
        assert config.app_database_path.exists()

    def test_app_database_path_defaults_to_home_data_dir(self, config_home, monkeypatch):
        """Without BASIC_MEMORY_CONFIG_DIR, default DB stays at ~/.basic-memory/memory.db."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        config = BasicMemoryConfig()

        assert config.data_dir_path == config_home / ".basic-memory"
        assert config.app_database_path == config_home / ".basic-memory" / "memory.db"

    def test_semantic_embedding_cache_dir_field_stays_none_by_default(
        self, config_home, monkeypatch
    ):
        """The raw config field stays None so it isn't persisted into config.json.

        Resolution to a concrete path happens in embedding_provider_factory at
        provider construction time, so ``BASIC_MEMORY_CONFIG_DIR`` and
        ``FASTEMBED_CACHE_PATH`` changes take effect on every run instead of
        being frozen by the first save. See #741.
        """
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)

        config = BasicMemoryConfig()

        assert config.semantic_embedding_cache_dir is None

    def test_semantic_embedding_cache_dir_not_persisted_in_model_dump(
        self, config_home, monkeypatch
    ):
        """model_dump must not bake a resolved cache path into config.json.

        Regression guard for #741: persisting the default would freeze stale
        paths when users later change BASIC_MEMORY_CONFIG_DIR or
        FASTEMBED_CACHE_PATH.
        """
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)

        dumped = BasicMemoryConfig().model_dump(mode="json")

        assert dumped["semantic_embedding_cache_dir"] is None

    def test_semantic_embedding_cache_dir_explicit_user_value_preserved(
        self, config_home, monkeypatch
    ):
        """An explicit user override still round-trips through model_dump."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)

        config = BasicMemoryConfig(semantic_embedding_cache_dir="/custom/explicit/path")

        assert config.semantic_embedding_cache_dir == "/custom/explicit/path"
        assert (
            config.model_dump(mode="json")["semantic_embedding_cache_dir"]
            == "/custom/explicit/path"
        )


class TestDataDirHelpers:
    """Module-level helpers that resolve the Basic Memory data directory."""

    def test_resolve_data_dir_defaults_to_home_dot_basic_memory(self, config_home, monkeypatch):
        """Without BASIC_MEMORY_CONFIG_DIR and XDG_CONFIG_HOME, resolver returns ~/.basic-memory."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        assert resolve_data_dir() == config_home / ".basic-memory"

    def test_resolve_data_dir_honors_config_dir_env(self, tmp_path, monkeypatch):
        """BASIC_MEMORY_CONFIG_DIR overrides the default location."""
        custom = tmp_path / "elsewhere"
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom))

        assert resolve_data_dir() == custom

    def test_resolve_data_dir_honors_xdg_config_home(self, tmp_path, monkeypatch):
        """XDG_CONFIG_HOME is honored when BASIC_MEMORY_CONFIG_DIR is not set."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        xdg_config = tmp_path / "xdg-config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))

        assert resolve_data_dir() == xdg_config / "basic-memory"

    def test_basic_memory_config_dir_takes_precedence_over_xdg(self, tmp_path, monkeypatch):
        """BASIC_MEMORY_CONFIG_DIR takes precedence over XDG_CONFIG_HOME."""
        xdg_config = tmp_path / "xdg-config"
        custom = tmp_path / "custom-config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom))

        assert resolve_data_dir() == custom

    def test_default_fastembed_cache_dir_uses_shared_xdg_cache(self, config_home, monkeypatch):
        """Default cache path is the user-level XDG cache, not the data dir."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        assert default_fastembed_cache_dir() == str(config_home / ".cache" / "fastembed")

    def test_default_fastembed_cache_dir_honors_xdg_cache_home(self, config_home, monkeypatch):
        """XDG_CACHE_HOME relocates the shared cache."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        xdg_cache = config_home / "xdg-cache"
        monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_cache))

        assert default_fastembed_cache_dir() == str(xdg_cache / "fastembed")

    def test_default_fastembed_cache_dir_is_shared_across_config_dirs(
        self, config_home, monkeypatch
    ):
        """Isolating config/state must not fork the 64 MB model download.

        ``BASIC_MEMORY_CONFIG_DIR`` exists so a test or lab instance can keep
        its own config, database, and state. The embedding model is an
        immutable artifact keyed by model name, so it is deliberately outside
        that isolation boundary.
        """
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        shared = str(config_home / ".cache" / "fastembed")

        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_home / "lab-a"))
        assert default_fastembed_cache_dir() == shared

        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_home / "lab-b"))
        assert default_fastembed_cache_dir() == shared

    def test_default_fastembed_cache_dir_keeps_existing_legacy_cache(
        self, config_home, monkeypatch
    ):
        """An install that already downloaded the model keeps using that copy.

        Repointing it at an empty shared cache would silently re-download 64 MB
        and orphan the old directory.
        """
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        legacy = config_home / ".basic-memory" / "fastembed_cache"
        legacy.mkdir(parents=True)

        assert default_fastembed_cache_dir() == str(legacy)

    def test_default_fastembed_cache_dir_prefers_shared_once_it_exists(
        self, config_home, monkeypatch
    ):
        """When both exist, the shared cache wins — the legacy path is a bridge."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        (config_home / ".basic-memory" / "fastembed_cache").mkdir(parents=True)
        shared = config_home / ".cache" / "fastembed"
        shared.mkdir(parents=True)

        assert default_fastembed_cache_dir() == str(shared)

    def test_default_fastembed_cache_dir_env_override(self, tmp_path, monkeypatch):
        """FASTEMBED_CACHE_PATH is preferred when set."""
        custom = tmp_path / "custom-cache"
        monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(custom))
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "state"))

        assert default_fastembed_cache_dir() == str(custom)

    def test_default_fastembed_cache_dir_never_falls_back_to_fastembed_tmp_default(
        self, config_home, monkeypatch
    ):
        """Regression guard for #741.

        FastEmbed's own fallback when ``cache_dir`` is ``None`` is
        ``<system tmp>/fastembed_cache`` — the exact path that disappears in
        sandboxed MCP runtimes (Codex CLI). Ensure Basic Memory's resolver
        never lands on that path.

        Compared as exact paths rather than ``startswith(tempfile.gettempdir())``
        because the test runner itself can legitimately live under ``/tmp``
        (pytest's ``tmp_path`` does on Linux CI), and that's not the bug we
        care about here.
        """
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)
        monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        resolved = Path(default_fastembed_cache_dir())

        # Must equal the shared user-level default.
        assert resolved == config_home / ".cache" / "fastembed"
        # And must not equal FastEmbed's own <tempdir>/fastembed_cache fallback.
        assert resolved != Path(tempfile.gettempdir()) / "fastembed_cache"


class TestConfigManager:
    """Test ConfigManager functionality."""

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
    def test_save_config_uses_private_permissions(self, tmp_path):
        """Config can contain provider API keys, so writes should enforce private modes."""
        config_manager = ConfigManager()
        config_manager.config_dir = tmp_path / "basic-memory"
        config_manager.config_file = config_manager.config_dir / "config.json"
        config_manager.config_dir.mkdir(parents=True, exist_ok=True)

        config = BasicMemoryConfig()
        config_manager.save_config(config)

        config.semantic_embedding_api_key = "sk-test123"
        config_manager.config_dir.chmod(0o777)
        config_manager.config_file.chmod(0o666)
        config_manager.save_config(config)

        dir_mode = stat.S_IMODE(config_manager.config_dir.stat().st_mode)
        file_mode = stat.S_IMODE(config_manager.config_file.stat().st_mode)

        assert dir_mode == 0o700
        assert file_mode == 0o600

    def test_disable_permalinks_flag_default(self):
        """Test that disable_permalinks flag defaults to False."""
        config = BasicMemoryConfig()
        assert config.disable_permalinks is False

    def test_disable_permalinks_flag_can_be_enabled(self):
        """Test that disable_permalinks flag can be set to True."""
        config = BasicMemoryConfig(disable_permalinks=True)
        assert config.disable_permalinks is True

    def test_ensure_frontmatter_on_sync_flag_default(self):
        """Test that ensure_frontmatter_on_sync defaults to True."""
        config = BasicMemoryConfig()
        assert config.ensure_frontmatter_on_sync is True

    def test_ensure_frontmatter_on_sync_flag_can_be_disabled(self):
        """Test that ensure_frontmatter_on_sync can be set to False."""
        config = BasicMemoryConfig(ensure_frontmatter_on_sync=False)
        assert config.ensure_frontmatter_on_sync is False

    def test_permalinks_include_project_flag_default(self):
        """Test that permalinks_include_project defaults to True."""
        config = BasicMemoryConfig()
        assert config.permalinks_include_project is True

    def test_permalinks_include_project_flag_can_be_disabled(self):
        """Test that permalinks_include_project can be set to False."""
        config = BasicMemoryConfig(permalinks_include_project=False)
        assert config.permalinks_include_project is False

    def test_config_manager_respects_custom_config_dir(self, monkeypatch):
        """Test that ConfigManager respects BASIC_MEMORY_CONFIG_DIR environment variable."""
        with tempfile.TemporaryDirectory() as temp_dir:
            custom_config_dir = Path(temp_dir) / "custom" / "config"
            monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_config_dir))

            config_manager = ConfigManager()

            # Verify config_dir is set to the custom path
            assert config_manager.config_dir == custom_config_dir
            # Verify config_file is in the custom directory
            assert config_manager.config_file == custom_config_dir / "config.json"
            # Verify the directory was created
            assert config_manager.config_dir.exists()

    def test_config_manager_default_without_custom_config_dir(self, config_home, monkeypatch):
        """Test that ConfigManager uses default location when BASIC_MEMORY_CONFIG_DIR is not set."""
        monkeypatch.delenv("BASIC_MEMORY_CONFIG_DIR", raising=False)

        config_manager = ConfigManager()

        # Should use default location
        assert config_manager.config_dir == config_home / ".basic-memory"
        assert config_manager.config_file == config_home / ".basic-memory" / "config.json"

    def test_legacy_projects_key_tolerated_but_not_written_back(self):
        """A pre-B2 config.json with a full legacy registry still parses (GAPS B2).

        The registry (``projects``/``default_project``) is DB-owned now; loading
        a legacy file must not raise, and the format-migration resave triggered by
        the retired routing keys must not resurrect those registry keys in the
        rewritten file — ``BasicMemoryConfig`` no longer declares those fields at
        all, so ``model_dump()`` cannot carry them forward.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            old_config_data = {
                "env": "dev",
                "projects": {
                    "main": str(temp_path / "main"),
                    "research": str(temp_path / "research"),
                },
                "default_project": "main",
                "project_modes": {"research": "cloud"},
                "cloud_projects": {
                    "research": {
                        "local_path": str(temp_path / "research-local"),
                        "bisync_initialized": True,
                        "last_sync": "2026-02-06T17:36:38",
                    }
                },
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None
            basic_memory.config._CONFIG_MTIME = None
            basic_memory.config._CONFIG_SIZE = None

            # Parses without error.
            config = config_manager.load_config()
            assert isinstance(config, BasicMemoryConfig)

            raw = json.loads(config_manager.config_file.read_text(encoding="utf-8"))
            assert "project_modes" not in raw
            assert "cloud_projects" not in raw
            assert "projects" not in raw
            assert "default_project" not in raw

    def test_legacy_cloud_mode_key_is_stripped_on_normalization_save(self):
        """Legacy cloud_mode should be removed from config.json after load/save normalization."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            legacy_config = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
                "cloud_mode": True,
            }
            config_manager.config_file.write_text(json.dumps(legacy_config, indent=2))

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None
            basic_memory.config._CONFIG_MTIME = None
            basic_memory.config._CONFIG_SIZE = None

            loaded = config_manager.load_config()
            assert isinstance(loaded, BasicMemoryConfig)

            raw = json.loads(config_manager.config_file.read_text(encoding="utf-8"))
            assert "cloud_mode" not in raw

    def test_no_backup_when_config_is_current_format(self):
        """No backup should be created when config has no retired top-level keys."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            # No RETIRED_TOP_LEVEL_KEYS present — no migration needed
            current_config_data = {
                "env": "dev",
                "projects": {"main": {"path": str(temp_path / "main")}},
                "default_project": "main",
            }
            config_manager.config_file.write_text(json.dumps(current_config_data, indent=2))

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None
            basic_memory.config._CONFIG_MTIME = None
            basic_memory.config._CONFIG_SIZE = None

            config_manager.load_config()

            backup_path = config_manager.config_file.with_suffix(".json.bak")
            assert not backup_path.exists(), "No backup should be created for current-format config"


class TestSemanticSearchConfig:
    """Test semantic search configuration options."""

    def test_semantic_search_enabled_defaults_to_true_when_semantic_modules_are_available(
        self, monkeypatch
    ):
        """Semantic search defaults on when fastembed and sqlite_vec are importable."""
        import basic_memory.config as config_module

        monkeypatch.delenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", raising=False)
        monkeypatch.setattr(
            config_module.importlib.util,
            "find_spec",
            lambda name: object() if name in {"fastembed", "sqlite_vec"} else None,
        )
        config = BasicMemoryConfig()
        assert config.semantic_search_enabled is True

    def test_semantic_search_enabled_defaults_to_false_when_any_semantic_module_is_unavailable(
        self, monkeypatch
    ):
        """Semantic search defaults off when required semantic modules are missing."""
        import basic_memory.config as config_module

        monkeypatch.delenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", raising=False)
        monkeypatch.setattr(
            config_module.importlib.util,
            "find_spec",
            lambda name: object() if name == "fastembed" else None,
        )
        config = BasicMemoryConfig()
        assert config.semantic_search_enabled is False

    def test_semantic_search_enabled_env_var_overrides_dependency_default(self, monkeypatch):
        """Environment overrides should win over dependency-based defaults."""
        import basic_memory.config as config_module

        monkeypatch.setattr(config_module.importlib.util, "find_spec", lambda name: None)

        monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "true")
        enabled = BasicMemoryConfig()
        assert enabled.semantic_search_enabled is True

        monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")
        disabled = BasicMemoryConfig()
        assert disabled.semantic_search_enabled is False

    def test_semantic_embedding_dimensions_defaults_to_none(self):
        """Dimensions should default to None, letting the provider choose."""
        config = BasicMemoryConfig()
        assert config.semantic_embedding_dimensions is None

    def test_semantic_embedding_dimensions_can_be_set(self):
        """Explicit dimensions should be stored on the config object."""
        config = BasicMemoryConfig(semantic_embedding_dimensions=1536)
        assert config.semantic_embedding_dimensions == 1536

    def test_semantic_embedding_prefixes_default_to_none(self):
        """Literal embedding text prefixes should be disabled by default."""
        config = BasicMemoryConfig()
        assert config.semantic_embedding_document_prefix is None
        assert config.semantic_embedding_query_prefix is None

    def test_semantic_embedding_prefixes_can_be_set(self):
        """Document and query embedding prefixes should be stored independently."""
        config = BasicMemoryConfig(
            semantic_embedding_document_prefix="title: none | text: ",
            semantic_embedding_query_prefix="task: search result | query: ",
        )
        assert config.semantic_embedding_document_prefix == "title: none | text: "
        assert config.semantic_embedding_query_prefix == "task: search result | query: "

    def test_semantic_min_similarity_defaults_to_055(self):
        """Threshold defaults to 0.55 to filter irrelevant vector results."""
        config = BasicMemoryConfig()
        assert config.semantic_min_similarity == 0.55

    def test_semantic_min_similarity_bounds_validation(self):
        """Threshold must be between 0.0 and 1.0."""
        config = BasicMemoryConfig(semantic_min_similarity=0.55)
        assert config.semantic_min_similarity == 0.55

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_min_similarity=-0.1)

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_min_similarity=1.1)

    def test_default_search_type_defaults_to_none(self):
        """default_search_type should be None by default (auto-detect)."""
        config = BasicMemoryConfig()
        assert config.default_search_type is None

    def test_default_search_type_accepts_valid_values(self):
        """default_search_type accepts text, vector, hybrid."""
        for search_type in ("text", "vector", "hybrid"):
            config = BasicMemoryConfig(default_search_type=search_type)
            assert config.default_search_type == search_type

    def test_default_search_type_rejects_invalid_values(self):
        """default_search_type rejects unknown values."""
        with pytest.raises(Exception):
            BasicMemoryConfig(default_search_type="invalid")


class TestFormattingConfig:
    """Test file formatting configuration options."""

    def test_format_on_save_defaults_to_false(self):
        """Test that format_on_save is disabled by default."""
        config = BasicMemoryConfig()
        assert config.format_on_save is False

    def test_format_on_save_can_be_enabled(self):
        """Test that format_on_save can be set to True."""
        config = BasicMemoryConfig(format_on_save=True)
        assert config.format_on_save is True

    def test_formatter_command_defaults_to_none(self):
        """Test that formatter_command defaults to None (uses built-in mdformat)."""
        config = BasicMemoryConfig()
        assert config.formatter_command is None

    def test_formatter_command_can_be_set(self):
        """Test that formatter_command can be configured."""
        config = BasicMemoryConfig(formatter_command="prettier --write {file}")
        assert config.formatter_command == "prettier --write {file}"

    def test_formatters_defaults_to_empty_dict(self):
        """Test that formatters defaults to empty dict."""
        config = BasicMemoryConfig()
        assert config.formatters == {}

    def test_formatters_can_be_configured(self):
        """Test that per-extension formatters can be configured."""
        config = BasicMemoryConfig(
            formatters={
                "md": "prettier --write {file}",
                "json": "jq . {file} > {file}.tmp && mv {file}.tmp {file}",
            }
        )
        assert config.formatters["md"] == "prettier --write {file}"
        assert "json" in config.formatters

    def test_formatter_timeout_defaults_to_5_seconds(self):
        """Test that formatter_timeout defaults to 5.0 seconds."""
        config = BasicMemoryConfig()
        assert config.formatter_timeout == 5.0

    def test_formatter_timeout_can_be_customized(self):
        """Test that formatter_timeout can be set to a different value."""
        config = BasicMemoryConfig(formatter_timeout=10.0)
        assert config.formatter_timeout == 10.0

    def test_formatter_timeout_must_be_positive(self):
        """Test that formatter_timeout validation rejects non-positive values."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            BasicMemoryConfig(formatter_timeout=0)

        with pytest.raises(pydantic.ValidationError):
            BasicMemoryConfig(formatter_timeout=-1)

    def test_formatting_env_vars(self, monkeypatch):
        """Test that formatting config can be set via environment variables."""
        monkeypatch.setenv("BASIC_MEMORY_FORMAT_ON_SAVE", "true")
        monkeypatch.setenv("BASIC_MEMORY_FORMATTER_COMMAND", "prettier --write {file}")
        monkeypatch.setenv("BASIC_MEMORY_FORMATTER_TIMEOUT", "10.0")

        config = BasicMemoryConfig()

        assert config.format_on_save is True
        assert config.formatter_command == "prettier --write {file}"
        assert config.formatter_timeout == 10.0

    def test_formatters_env_var_json(self, monkeypatch):
        """Test that formatters dict can be set via JSON environment variable."""
        import json

        formatters_json = json.dumps({"md": "prettier --write {file}", "json": "jq . {file}"})
        monkeypatch.setenv("BASIC_MEMORY_FORMATTERS", formatters_json)

        config = BasicMemoryConfig()

        assert config.formatters == {"md": "prettier --write {file}", "json": "jq . {file}"}

    def test_save_and_load_formatting_config(self):
        """Test that formatting config survives save/load cycle."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create config with formatting settings
            test_config = BasicMemoryConfig(
                format_on_save=True,
                formatter_command="prettier --write {file}",
                formatters={"md": "prettier --write {file}", "json": "prettier --write {file}"},
                formatter_timeout=10.0,
            )
            config_manager.save_config(test_config)

            # Load and verify
            loaded_config = config_manager.load_config()
            assert loaded_config.format_on_save is True
            assert loaded_config.formatter_command == "prettier --write {file}"
            assert loaded_config.formatters == {
                "md": "prettier --write {file}",
                "json": "prettier --write {file}",
            }
            assert loaded_config.formatter_timeout == 10.0


class TestLocalSyncability:
    """Which registered projects the local watcher is allowed to sync.

    ``is_locally_syncable`` is a module-level function in ``config_models``
    (GAPS B2): the project registry moved to the database, so this check no
    longer takes a config/name pair — it only asks whether a path is absolute.
    """

    def test_is_locally_syncable_true_for_absolute_path(self, tmp_path):
        """An absolute path is locally syncable."""
        abs_path = str(tmp_path / "research")
        assert is_locally_syncable(abs_path) is True

    def test_is_locally_syncable_false_for_empty_path(self):
        """An empty path resolves to cwd, so it is never locally syncable (#949)."""
        assert is_locally_syncable("") is False

    def test_is_locally_syncable_false_for_relative_path(self):
        """A relative (slug) path is not a directory we own, so it is not syncable."""
        assert is_locally_syncable("bare-slug") is False


class TestConfigCacheMtimeInvalidation:
    """Test that config cache is invalidated when file is modified externally."""

    def test_cache_returns_same_config_when_file_unchanged(self, config_home):
        """Verify cache hit when config file mtime has not changed."""
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            test_config = BasicMemoryConfig(log_level="INFO")
            config_manager.save_config(test_config)

            # First load populates cache
            config1 = config_manager.load_config()
            assert config1.log_level == "INFO"

            # Second load should return cached config (same object)
            config2 = config_manager.load_config()
            assert config1 is config2

    def test_cache_invalidated_when_file_modified(self, config_home):
        """Verify cache miss when config file is modified by another process."""
        import json
        import os
        import time

        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            test_config = BasicMemoryConfig(log_level="INFO")
            config_manager.save_config(test_config)

            # First load populates cache
            config1 = config_manager.load_config()
            assert config1.log_level == "INFO"

            # Simulate external process modifying the config file
            config_data = json.loads(config_manager.config_file.read_text())
            config_data["log_level"] = "DEBUG"

            # Ensure mtime actually changes (some filesystems have 1s granularity)
            time.sleep(0.05)
            config_manager.config_file.write_text(json.dumps(config_data, indent=2))
            # Force mtime change on filesystems with coarse granularity
            new_mtime = os.path.getmtime(config_manager.config_file) + 1
            os.utime(config_manager.config_file, (new_mtime, new_mtime))

            # Next load should detect mtime change and re-read
            config2 = config_manager.load_config()
            assert config2.log_level == "DEBUG"
            assert config1 is not config2

    def test_save_config_resets_mtime(self, config_home):
        """Verify save_config clears both cache and mtime."""
        import basic_memory.config

        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            test_config = BasicMemoryConfig()
            config_manager.save_config(test_config)

            # Load to populate cache
            config_manager.load_config()
            assert basic_memory.config._CONFIG_CACHE is not None
            assert basic_memory.config._CONFIG_MTIME is not None
            assert basic_memory.config._CONFIG_SIZE is not None

            # Save should clear all cache state
            config_manager.save_config(test_config)
            assert basic_memory.config._CONFIG_CACHE is None
            assert basic_memory.config._CONFIG_MTIME is None
            assert basic_memory.config._CONFIG_SIZE is None


class TestDropRetiredConfigKeys:
    """The retired cloud/routing top-level keys are dropped, never translated.

    ``drop_retired_config_keys`` (config_migrations.py) only strips
    ``RETIRED_TOP_LEVEL_KEYS`` — it does not touch ``projects``/``default_project``,
    which are tolerated separately (see ``TestLegacyProjectsToleratedButNotPersisted``)
    and normalized by ``normalize_legacy_projects`` (``TestNormalizeLegacyProjects``).
    """

    def test_retired_top_level_keys_are_removed(self):
        """default_project_mode, cloud_mode, project_modes, cloud_projects all go."""
        data = {
            "default_project": "main",
            "default_project_mode": "cloud",
            "cloud_mode": True,
            "project_modes": {"specs": "cloud"},
            "cloud_projects": {},
            "projects": {"main": {"path": "/tmp/main"}},
        }
        result = drop_retired_config_keys(data)
        assert result == {
            "default_project": "main",
            "projects": {"main": {"path": "/tmp/main"}},
        }

    def test_non_dict_input_passes_through(self):
        """Anything that is not a config mapping is returned untouched."""
        assert drop_retired_config_keys("not-a-dict") == "not-a-dict"


class TestNormalizeLegacyProjects:
    """``config_migrations.normalize_legacy_projects`` reads a legacy registry.

    Returns a flat ``name -> path`` mapping straight off a pre-B2 config.json,
    for the one-time import into the database registry (``ensure_project_registry``).
    """

    def test_bare_string_path_shape(self):
        """The oldest format: ``{"name": "/path"}``."""
        data = {"projects": {"main": "/tmp/main"}}
        assert normalize_legacy_projects(data) == {"main": "/tmp/main"}

    def test_dict_with_path_key_shape(self):
        """The ``{"name": {"path": ..., ...}}`` shape — extra keys are ignored."""
        data = {
            "projects": {
                "specs": {
                    "path": "/Users/test/Documents/specs",
                    "mode": "cloud",
                    "workspace_id": "tenant-1",
                    "bisync_initialized": True,
                    "last_sync": "2026-02-06T17:36:38",
                }
            }
        }
        assert normalize_legacy_projects(data) == {"specs": "/Users/test/Documents/specs"}

    def test_legacy_cloud_local_path_is_promoted_over_a_slug_path(self):
        """A remote-only entry recorded a slug in path and the real directory in
        cloud_projects.local_path — only the directory survives the strip."""
        data = {
            "projects": {"specs": {"path": "specs", "mode": "cloud"}},
            "cloud_projects": {"specs": {"local_path": "/Users/test/Documents/specs"}},
        }
        assert normalize_legacy_projects(data) == {"specs": "/Users/test/Documents/specs"}

    def test_absolute_path_is_never_overwritten_by_legacy_local_path(self):
        """An absolute path is already the real directory, so it wins."""
        data = {
            "projects": {"specs": {"path": "/Users/test/Documents/specs"}},
            "cloud_projects": {"specs": {"local_path": "/somewhere/else"}},
        }
        assert normalize_legacy_projects(data) == {"specs": "/Users/test/Documents/specs"}

    def test_slug_path_without_legacy_entry_is_left_alone(self):
        """Nothing to promote means the entry keeps whatever path it had."""
        data = {"projects": {"orphan": {"path": "orphan", "mode": "cloud"}}}
        assert normalize_legacy_projects(data) == {"orphan": "orphan"}

    def test_string_projects_promoted_via_cloud_projects(self, tmp_path):
        """A bare string entry can still be promoted by the cloud_projects quirk."""
        local_path = str(tmp_path / "local")
        data = {
            "projects": {"local-proj": local_path, "slug-proj": "slug-proj"},
            "cloud_projects": {"slug-proj": {"local_path": str(tmp_path / "promoted")}},
        }
        result = normalize_legacy_projects(data)
        assert result["local-proj"] == local_path
        # A bare string entry is never checked against cloud_projects (only dict
        # entries with a non-absolute path are), so it keeps its own path.
        assert result["slug-proj"] == "slug-proj"

    def test_projects_value_not_a_dict_returns_empty(self):
        """A ``projects`` value that isn't a mapping normalizes to nothing."""
        assert normalize_legacy_projects({"projects": ["not", "a", "dict"]}) == {}

    def test_no_projects_key_returns_empty(self):
        """A config with no ``projects`` key at all has nothing to import."""
        assert normalize_legacy_projects({}) == {}

    def test_empty_projects_returns_empty(self):
        """No projects means there is nothing to normalize."""
        assert normalize_legacy_projects({"projects": {}}) == {}


class TestLegacyProjectsToleratedButNotPersisted:
    """A legacy ``projects``/``default_project`` key parses but never round-trips.

    The registry is DB-owned now (GAPS B2); ``BasicMemoryConfig`` tolerates the
    keys as extras on load (``extra="ignore"``) so a pre-B2 config.json still
    parses, and ``model_dump()`` never re-emits them since the model no longer
    declares those fields.
    """

    def test_legacy_projects_key_tolerated_on_load(self):
        """extra='ignore' lets a legacy config.json parse without error."""
        config = BasicMemoryConfig(
            projects={"main": {"path": "/tmp/main"}},
            default_project="main",
        )
        assert isinstance(config, BasicMemoryConfig)

    def test_model_dump_never_contains_projects_or_default_project(self):
        """save_config must never write the registry back into config.json (GAPS B2)."""
        config = BasicMemoryConfig(
            projects={"main": {"path": "/tmp/main"}},
            default_project="main",
        )
        dumped = config.model_dump(mode="json")
        assert "projects" not in dumped
        assert "default_project" not in dumped


class TestAtomicConfigSave:
    """Regression tests for #940: saving config must never tear the published file.

    Long-lived readers (the MCP stdio server's mtime-based config reload) re-read
    config.json while other code saves it.
    An in-place write truncates the file first, so a concurrent reader can observe
    empty/partial JSON — and load_config() raises SystemExit on invalid JSON.
    """

    def test_interrupted_save_preserves_published_config(self, config_home, monkeypatch):
        """A write that dies mid-stream must leave the existing config untouched."""
        import json

        from basic_memory.config import save_basic_memory_config

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_file = temp_path / "config.json"
            config = BasicMemoryConfig()
            save_basic_memory_config(config_file, config)
            published = config_file.read_text(encoding="utf-8")
            json.loads(published)  # sanity: complete, valid document

            def torn_write_text(self, content, *args, **kwargs):
                # Fault injection: the write dies halfway through. For an in-place
                # write this is exactly the truncated state a concurrent reader
                # observes mid-save; an atomic save must confine it to a temp file.
                with open(self, "w", encoding="utf-8") as fh:
                    fh.write(content[: len(content) // 2])
                raise OSError("simulated interrupted write")

            monkeypatch.setattr(Path, "write_text", torn_write_text)
            # save_basic_memory_config logs write failures instead of raising
            save_basic_memory_config(config_file, config)
            monkeypatch.undo()

            assert config_file.read_text(encoding="utf-8") == published

    def test_save_leaves_no_temp_files(self, config_home):
        """The atomic-write temp file must not survive a successful save."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_file = temp_path / "config.json"
            config = BasicMemoryConfig()

            from basic_memory.config import save_basic_memory_config

            save_basic_memory_config(config_file, config)

            assert config_file.exists()
            assert not list(temp_path.glob("*.tmp"))
