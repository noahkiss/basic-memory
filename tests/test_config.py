"""Test configuration management."""

import os
import stat
import tempfile
import pytest
from datetime import datetime
from typing import Any, cast

from basic_memory.config import (
    BasicMemoryConfig,
    ConfigManager,
    ProjectEntry,
    default_fastembed_cache_dir,
    resolve_data_dir,
)
from pathlib import Path


def _migrate_legacy_projects(data: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], cast(Any, BasicMemoryConfig.migrate_legacy_projects)(data))


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
    """Test BasicMemoryConfig behavior with BASIC_MEMORY_HOME environment variable."""

    def test_default_behavior_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that config uses default path when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig()

        # Should use the default path (home/basic-memory)
        expected_path = config_home / "basic-memory"
        assert Path(config.projects["main"].path) == expected_path
        assert config.default_project == "main"

    def test_respects_basic_memory_home_environment_variable(self, config_home, monkeypatch):
        """Test that config respects BASIC_MEMORY_HOME environment variable."""
        custom_path = config_home / "app" / "data"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        config = BasicMemoryConfig()

        # Should use the custom path from environment variable
        assert Path(config.projects["main"].path) == custom_path

    def test_model_post_init_respects_basic_memory_home_creates_main(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init creates main project with BASIC_MEMORY_HOME when missing and no other projects."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        # Create config without main project
        config = BasicMemoryConfig()

        # model_post_init should have added main project with BASIC_MEMORY_HOME
        assert "main" in config.projects
        assert Path(config.projects["main"].path) == custom_path

    def test_model_post_init_respects_basic_memory_home_sets_non_main_default(
        self, config_home, monkeypatch
    ):
        """Test that model_post_init does not create main project with BASIC_MEMORY_HOME when another project exists."""
        custom_path = config_home / "custom" / "memory" / "path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", str(custom_path))

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": {"path": str(other_path)}})

        # model_post_init should not add main project with BASIC_MEMORY_HOME
        assert "main" not in config.projects
        assert Path(config.projects["other"].path) == other_path
        assert config.default_project == "other"

    def test_model_post_init_fallback_without_basic_memory_home(self, config_home, monkeypatch):
        """Test that model_post_init can set a non-main default when BASIC_MEMORY_HOME is not set."""
        # Ensure BASIC_MEMORY_HOME is not set
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without main project
        other_path = config_home / "some" / "path"
        config = BasicMemoryConfig(projects={"other": {"path": str(other_path)}})

        # model_post_init should not add main project, but "other" should now be the default
        assert "main" not in config.projects
        assert Path(config.projects["other"].path) == other_path
        assert config.default_project == "other"

    def test_model_post_init_seeds_default_for_postgres(self, config_home, monkeypatch):
        """A Postgres backend seeds a default project exactly like SQLite.

        Without it a fresh Postgres install has no default project and
        create_memory_project raises "No default project configured".
        """
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig(database_backend="postgres")

        assert "main" in config.projects
        assert config.default_project == "main"

    def test_postgres_creates_project_directories(self, config_home, tmp_path):
        """Postgres creates its project directories like SQLite."""
        proj = tmp_path / "pg-project"
        BasicMemoryConfig(
            database_backend="postgres",
            projects={"main": {"path": str(proj)}},
            default_project="main",
        )
        assert proj.exists()

    def test_basic_memory_home_with_relative_path(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME works with relative paths."""
        relative_path = "relative/memory/path"
        monkeypatch.setenv("BASIC_MEMORY_HOME", relative_path)

        config = BasicMemoryConfig()

        # Should normalize to platform-native path format
        assert Path(config.projects["main"].path) == Path(relative_path)

    def test_basic_memory_home_overrides_existing_main_project(self, config_home, monkeypatch):
        """Test that BASIC_MEMORY_HOME is not used when a map is passed in the constructor."""
        custom_path = str(config_home / "override" / "memory" / "path")
        monkeypatch.setenv("BASIC_MEMORY_HOME", custom_path)

        # Try to create config with a different main project path
        original_path = str(config_home / "original" / "path")
        config = BasicMemoryConfig(projects={"main": {"path": original_path}})

        # The default_factory should override with BASIC_MEMORY_HOME value
        # Note: This tests the current behavior where default_factory takes precedence
        assert config.projects["main"].path == original_path

    def test_app_database_path_uses_custom_config_dir(self, tmp_path, monkeypatch):
        """Default SQLite DB should live under BASIC_MEMORY_CONFIG_DIR when set."""
        custom_config_dir = tmp_path / "instance-a" / "state"
        monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_config_dir))

        config = BasicMemoryConfig(projects={"main": {"path": str(tmp_path / "project")}})

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

    def test_explicit_default_project_preserved(self, config_home, monkeypatch):
        """Test that a valid explicit default_project is not overwritten by model_post_init."""
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig(
            projects={
                "alpha": {"path": str(config_home / "alpha")},
                "beta": {"path": str(config_home / "beta")},
            },
            default_project="beta",
        )

        assert config.default_project == "beta"

    def test_invalid_default_project_corrected(self, config_home, monkeypatch):
        """Test that an invalid default_project is corrected to the first project."""
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig(
            projects={
                "alpha": {"path": str(config_home / "alpha")},
                "beta": {"path": str(config_home / "beta")},
            },
            default_project="nonexistent",
        )

        assert config.default_project == "alpha"

    def test_no_default_project_key_uses_first_project(self, config_home, monkeypatch):
        """Test that config without default_project key sets it to the first project."""
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Simulate loading a config file that has no default_project key —
        # the field default (None) kicks in, and model_post_init resolves it
        config = BasicMemoryConfig(
            projects={
                "research": {"path": str(config_home / "research")},
                "notes": {"path": str(config_home / "notes")},
            },
        )

        assert config.default_project == "research"

    def test_empty_string_default_project_corrected(self, config_home, monkeypatch):
        """Test that an empty-string default_project is corrected to the first project."""
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig(
            projects={
                "alpha": {"path": str(config_home / "alpha")},
            },
            default_project="",
        )

        # Empty string is not in projects, so model_post_init corrects it
        assert config.default_project == "alpha"

    def test_single_project_default_always_matches(self, config_home, monkeypatch):
        """Test that a config with one project always resolves default_project to it."""
        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config = BasicMemoryConfig(
            projects={"only": {"path": str(config_home / "only")}},
        )

        assert config.default_project == "only"

    def test_stale_default_project_loaded_from_file(self, config_home, monkeypatch):
        """Test that a config file with a stale default_project is corrected on load."""
        import json
        import basic_memory.config

        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config_manager = ConfigManager()
        config_manager.config_dir = config_home / ".basic-memory"
        config_manager.config_file = config_manager.config_dir / "config.json"
        config_manager.config_dir.mkdir(parents=True, exist_ok=True)

        # Write a config file where default_project references a removed project
        config_data = {
            "projects": {
                "research": {"path": str(config_home / "research")},
                "notes": {"path": str(config_home / "notes")},
            },
            "default_project": "deleted-project",
        }
        config_manager.config_file.write_text(json.dumps(config_data, indent=2))
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

        loaded = config_manager.load_config()
        assert loaded.default_project == "research"

    def test_config_file_without_default_project_key(self, config_home, monkeypatch):
        """Test that a config file with no default_project key resolves dynamically."""
        import json
        import basic_memory.config

        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        config_manager = ConfigManager()
        config_manager.config_dir = config_home / ".basic-memory"
        config_manager.config_file = config_manager.config_dir / "config.json"
        config_manager.config_dir.mkdir(parents=True, exist_ok=True)

        # Write a config file that deliberately omits default_project
        config_data = {
            "projects": {
                "work": {"path": str(config_home / "work")},
                "personal": {"path": str(config_home / "personal")},
            },
        }
        config_manager.config_file.write_text(json.dumps(config_data, indent=2))
        basic_memory.config._CONFIG_CACHE = None
        basic_memory.config._CONFIG_MTIME = None
        basic_memory.config._CONFIG_SIZE = None

        loaded = config_manager.load_config()
        assert loaded.default_project == "work"


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

    @pytest.fixture
    def temp_config_manager(self):
        """Create a ConfigManager with temporary config file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create a test ConfigManager instance
            config_manager = ConfigManager()
            # Override config paths to use temp directory
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.yaml"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create initial config with test projects
            test_config = BasicMemoryConfig(
                default_project="main",
                projects={
                    "main": {"path": str(temp_path / "main")},
                    "test-project": {"path": str(temp_path / "test")},
                    "special-chars": {
                        "path": str(temp_path / "special")
                    },  # This will be the config key for "Special/Chars"
                },
            )
            config_manager.save_config(test_config)

            yield config_manager

    def test_set_default_project_with_exact_name_match(self, temp_config_manager):
        """Test set_default_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Set default to a project that exists with exact name match
        config_manager.set_default_project("test-project")

        # Verify the config was updated
        config = config_manager.load_config()
        assert config.default_project == "test-project"

    def test_set_default_project_with_permalink_lookup(self, temp_config_manager):
        """Test set_default_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Simulate a project that was created with special characters
        # The config key would be the permalink, but user might type the original name

        # First add a project with original name that gets normalized
        config = config_manager.load_config()
        config.projects["special-chars-project"] = ProjectEntry(path=str(Path("/tmp/special")))
        config_manager.save_config(config)

        # Now test setting default using a name that will normalize to the config key
        config_manager.set_default_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the config was updated with the correct config key
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "special-chars-project"

    def test_set_default_project_uses_canonical_name(self, temp_config_manager):
        """Test that set_default_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = ProjectEntry(path=str(Path("/tmp/mytest")))
        config_manager.save_config(config)

        # Set default using input that will match but is different from config key
        config_manager.set_default_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key is used, not the user input
        updated_config = config_manager.load_config()
        assert updated_config.default_project == "my-test-project"
        # Should NOT be the user input
        assert updated_config.default_project != "My Test Project"

    def test_set_default_project_nonexistent_project(self, temp_config_manager):
        """Test set_default_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.set_default_project("nonexistent")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not portable to Windows")
    def test_save_config_uses_private_permissions(self, temp_config_manager):
        """Config can contain provider API keys, so writes should enforce private modes."""
        config_manager = temp_config_manager
        config = config_manager.load_config()
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

    def test_remove_project_with_exact_name_match(self, temp_config_manager):
        """Test remove_project when project name matches config key exactly."""
        config_manager = temp_config_manager

        # Verify project exists
        config = config_manager.load_config()
        assert "test-project" in config.projects

        # Remove the project with exact name match
        config_manager.remove_project("test-project")

        # Verify the project was removed
        config = config_manager.load_config()
        assert "test-project" not in config.projects

    def test_remove_project_with_permalink_lookup(self, temp_config_manager):
        """Test remove_project when input needs permalink normalization."""
        config_manager = temp_config_manager

        # Add a project with normalized key
        config = config_manager.load_config()
        config.projects["special-chars-project"] = ProjectEntry(path=str(Path("/tmp/special")))
        config_manager.save_config(config)

        # Remove using a name that will normalize to the config key
        config_manager.remove_project(
            "Special Chars Project"
        )  # This should normalize to "special-chars-project"

        # Verify the project was removed using the correct config key
        updated_config = config_manager.load_config()
        assert "special-chars-project" not in updated_config.projects

    def test_remove_project_uses_canonical_name(self, temp_config_manager):
        """Test that remove_project uses the canonical config key, not user input."""
        config_manager = temp_config_manager

        # Add a project with a config key that differs from user input
        config = config_manager.load_config()
        config.projects["my-test-project"] = ProjectEntry(path=str(Path("/tmp/mytest")))
        config_manager.save_config(config)

        # Remove using input that will match but is different from config key
        config_manager.remove_project("My Test Project")  # Should find "my-test-project"

        # Verify that the canonical config key was removed
        updated_config = config_manager.load_config()
        assert "my-test-project" not in updated_config.projects

    def test_remove_project_nonexistent_project(self, temp_config_manager):
        """Test remove_project raises ValueError for nonexistent project."""
        config_manager = temp_config_manager

        with pytest.raises(ValueError, match="Project 'nonexistent' not found"):
            config_manager.remove_project("nonexistent")

    def test_remove_project_cannot_remove_default(self, temp_config_manager):
        """Test remove_project raises ValueError when trying to remove default project."""
        config_manager = temp_config_manager

        # Try to remove the default project
        with pytest.raises(ValueError, match="Cannot remove the default project"):
            config_manager.remove_project("main")

    def test_backward_compatibility_loading_old_format_config(self):
        """Test that old config files with Dict[str, str] projects can be loaded and migrated."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Manually write old-style config with Dict[str, str] projects
            import json

            old_config_data = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
                "log_level": "INFO",
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))

            # Clear the config cache to ensure we load from the temp file
            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None
            basic_memory.config._CONFIG_MTIME = None
            basic_memory.config._CONFIG_SIZE = None

            # Should load successfully with migration to ProjectEntry
            config = config_manager.load_config()
            assert isinstance(config.projects["main"], ProjectEntry)
            assert config.projects["main"].path == str(temp_path / "main")

    def test_retired_routing_keys_are_dropped_from_config_file(self):
        """Old project_modes/cloud_projects blocks are dropped, not translated."""
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

            config = config_manager.load_config()

            # The local path recorded in projects wins; the cloud block is gone.
            assert config.projects["research"].path == str(temp_path / "research")
            assert config.projects["main"].path == str(temp_path / "main")

            raw = json.loads(config_manager.config_file.read_text(encoding="utf-8"))
            assert "project_modes" not in raw
            assert "cloud_projects" not in raw
            assert set(raw["projects"]["research"]) == {"path"}

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

    def test_migration_creates_backup_of_old_config(self):
        """Config migration should create a .bak backup before overwriting."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            old_config_data = {
                "env": "dev",
                "projects": {"main": str(temp_path / "main")},
                "default_project": "main",
            }
            config_manager.config_file.write_text(json.dumps(old_config_data, indent=2))
            original_content = config_manager.config_file.read_text()

            import basic_memory.config

            basic_memory.config._CONFIG_CACHE = None
            basic_memory.config._CONFIG_MTIME = None
            basic_memory.config._CONFIG_SIZE = None

            config_manager.load_config()

            # Backup should exist with the original content
            backup_path = config_manager.config_file.with_suffix(".json.bak")
            assert backup_path.exists(), "Migration should create a backup file"
            assert backup_path.read_text() == original_content

    def test_no_backup_when_config_is_current_format(self):
        """No backup should be created when config is already in the current format."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            import json

            # Write config in the current ProjectEntry format — no migration needed
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


class TestPlatformNativePathSeparators:
    """Test that config uses platform-native path separators."""

    def test_project_paths_use_platform_native_separators_in_config(self, monkeypatch):
        """Test that project paths use platform-native separators when created."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager with temp directory
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Create a project path
            project_path = temp_path / "my" / "project"
            project_path.mkdir(parents=True, exist_ok=True)

            # Add project via ConfigManager
            config = BasicMemoryConfig(projects={})
            config.projects["test-project"] = ProjectEntry(path=str(project_path))
            config_manager.save_config(config)

            # Read the raw JSON file
            import json

            config_data = json.loads(config_manager.config_file.read_text())

            # Verify path uses platform-native separators
            saved_path = config_data["projects"]["test-project"]["path"]

            # On Windows, should have backslashes; on Unix, forward slashes
            if platform.system() == "Windows":
                # Windows paths should contain backslashes
                assert "\\" in saved_path or ":" in saved_path  # C:\\ or \\UNC
                assert "/" not in saved_path.replace(":/", "")  # Exclude drive letter
            else:
                # Unix paths should use forward slashes
                assert "/" in saved_path
                # Should not force POSIX on non-Windows
                assert saved_path == str(project_path)

    def test_add_project_uses_platform_native_separators(self, monkeypatch):
        """Test that ConfigManager.add_project() uses platform-native separators."""
        import platform

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Set up ConfigManager
            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            # Initialize with empty projects
            initial_config = BasicMemoryConfig(projects={})
            config_manager.save_config(initial_config)

            # Add project
            project_path = temp_path / "new" / "project"
            config_manager.add_project("new-project", str(project_path))

            # Load and verify
            config = config_manager.load_config()
            saved_path = config.projects["new-project"].path

            # Verify platform-native separators
            if platform.system() == "Windows":
                assert "\\" in saved_path or ":" in saved_path
            else:
                assert "/" in saved_path
                assert saved_path == str(project_path)

    def test_add_project_never_creates_directory(self):
        """Test that ConfigManager.add_project() is pure config management — no mkdir.

        Directory creation is delegated to ProjectService via FileService.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            initial_config = BasicMemoryConfig(projects={})
            config_manager.save_config(initial_config)

            # Use a path that does not exist — ConfigManager should not create it
            nonexistent_path = str(temp_path / "nonexistent" / "project")
            config_manager.add_project("test-project", nonexistent_path)

            # Check directory does NOT exist right after add_project(),
            # before load_config() which triggers the model validator
            assert not Path(nonexistent_path).exists()

            # Verify project was persisted in config
            config = config_manager.load_config()
            assert "test-project" in config.projects
            assert config.projects["test-project"].path == nonexistent_path

    def test_model_post_init_uses_platform_native_separators(self, config_home, monkeypatch):
        """Test that model_post_init uses platform-native separators."""
        import platform

        monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)

        # Create config without projects (triggers model_post_init to add main)
        config = BasicMemoryConfig(projects={})

        # Verify main project path uses platform-native separators
        main_path = config.projects["main"].path

        if platform.system() == "Windows":
            # Windows: should have backslashes or drive letter
            assert "\\" in main_path or ":" in main_path
        else:
            # Unix: should have forward slashes
            assert "/" in main_path


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

    def test_semantic_embedding_forward_dimensions_defaults_to_none(self):
        """Dimension forwarding should default to provider auto-detection."""
        config = BasicMemoryConfig()
        assert config.semantic_embedding_forward_dimensions is None

    def test_semantic_embedding_forward_dimensions_can_be_set(self):
        """Explicit LiteLLM dimension forwarding should be stored on the config object."""
        config = BasicMemoryConfig(semantic_embedding_forward_dimensions=True)
        assert config.semantic_embedding_forward_dimensions is True

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

    def test_semantic_postgres_prepare_concurrency_defaults_to_4(self):
        """Postgres prepare concurrency should default to a conservative window of 4."""
        config = BasicMemoryConfig()
        assert config.semantic_postgres_prepare_concurrency == 4

    def test_semantic_postgres_prepare_concurrency_validation(self):
        """Postgres prepare concurrency must stay within the bounded safe range."""
        config = BasicMemoryConfig(semantic_postgres_prepare_concurrency=8)
        assert config.semantic_postgres_prepare_concurrency == 8

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_postgres_prepare_concurrency=0)

        with pytest.raises(Exception):
            BasicMemoryConfig(semantic_postgres_prepare_concurrency=17)

    def test_semantic_search_enabled_description_mentions_both_backends(self):
        """Description should not say 'SQLite only' anymore."""
        field_info = BasicMemoryConfig.model_fields["semantic_search_enabled"]
        assert "SQLite only" not in (field_info.description or "")

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
                projects={"main": {"path": str(temp_path / "main")}},
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
    """Which configured projects the local watcher is allowed to sync."""

    def test_is_locally_syncable_true_for_config_project_with_absolute_path(self, tmp_path):
        """A project in config with an absolute path is locally syncable."""
        abs_path = str(tmp_path / "research")
        config = BasicMemoryConfig(projects={"research": ProjectEntry(path=abs_path)})
        assert config.is_locally_syncable("research", abs_path) is True

    def test_is_locally_syncable_false_for_empty_path(self):
        """An empty path resolves to cwd, so it is never locally syncable (#949)."""
        config = BasicMemoryConfig(projects={"empty": ProjectEntry(path="")})
        assert config.is_locally_syncable("empty", "") is False

    def test_is_locally_syncable_false_for_relative_path(self):
        """A relative (slug) path is not a directory we own, so it is not syncable."""
        config = BasicMemoryConfig(projects={"slug": ProjectEntry(path="bare-slug")})
        assert config.is_locally_syncable("slug", "bare-slug") is False

    def test_is_locally_syncable_false_for_orphan_not_in_config(self, tmp_path):
        """A DB row absent from config is not syncable even with an absolute path.

        Config is the source of truth; stale rows must not be synced (#949).
        """
        config = BasicMemoryConfig(projects={})
        assert config.is_locally_syncable("orphan", str(tmp_path / "orphan")) is False


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

            test_config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                default_project="main",
            )
            config_manager.save_config(test_config)

            # First load populates cache
            config1 = config_manager.load_config()
            assert config1.default_project == "main"

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

            test_config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                default_project="main",
            )
            config_manager.save_config(test_config)

            # First load populates cache
            config1 = config_manager.load_config()
            assert "second" not in config1.projects

            # Simulate external process modifying the config file
            config_data = json.loads(config_manager.config_file.read_text())
            config_data["projects"]["second"] = {"path": str(temp_path / "second")}

            # Ensure mtime actually changes (some filesystems have 1s granularity)
            time.sleep(0.05)
            config_manager.config_file.write_text(json.dumps(config_data, indent=2))
            # Force mtime change on filesystems with coarse granularity
            new_mtime = os.path.getmtime(config_manager.config_file) + 1
            os.utime(config_manager.config_file, (new_mtime, new_mtime))

            # Next load should detect mtime change and re-read
            config2 = config_manager.load_config()
            assert "second" in config2.projects
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

            test_config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
            )
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


class TestRetiredKeyMigration:
    """The retired cloud/routing keys are dropped, never translated."""

    def test_retired_project_keys_are_removed_from_entries(self):
        """Every retired per-project key disappears, leaving only path."""
        data = {
            "projects": {
                "specs": {
                    "path": "/Users/test/Documents/specs",
                    "mode": "cloud",
                    "workspace_id": "tenant-1",
                    "local_sync_path": "/Users/test/Documents/specs",
                    "cloud_sync_path": "/specs",
                    "bisync_initialized": True,
                    "last_sync": "2026-02-06T17:36:38",
                }
            }
        }
        result = _migrate_legacy_projects(data)
        assert result["projects"]["specs"] == {"path": "/Users/test/Documents/specs"}

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
        result = _migrate_legacy_projects(data)
        assert result == {
            "default_project": "main",
            "projects": {"main": {"path": "/tmp/main"}},
        }

    def test_legacy_cloud_local_path_is_promoted_over_a_slug_path(self):
        """A remote-only entry recorded a slug in path and the real directory in
        cloud_projects.local_path — only the directory survives the strip."""
        data = {
            "projects": {"specs": {"path": "specs", "mode": "cloud"}},
            "cloud_projects": {"specs": {"local_path": "/Users/test/Documents/specs"}},
        }
        result = _migrate_legacy_projects(data)
        assert result["projects"]["specs"] == {"path": "/Users/test/Documents/specs"}

    def test_absolute_path_is_never_overwritten_by_legacy_local_path(self):
        """An absolute path is already the real directory, so it wins."""
        data = {
            "projects": {"specs": {"path": "/Users/test/Documents/specs"}},
            "cloud_projects": {"specs": {"local_path": "/somewhere/else"}},
        }
        result = _migrate_legacy_projects(data)
        assert result["projects"]["specs"]["path"] == "/Users/test/Documents/specs"

    def test_slug_path_without_legacy_entry_is_left_alone(self):
        """Nothing to promote means the entry keeps whatever path it had."""
        data = {"projects": {"orphan": {"path": "orphan", "mode": "cloud"}}}
        result = _migrate_legacy_projects(data)
        assert result["projects"]["orphan"] == {"path": "orphan"}

    def test_string_projects_are_wrapped_before_keys_are_dropped(self, tmp_path):
        """The oldest format (name -> path string) still migrates in one pass."""
        local_path = str(tmp_path / "local")
        data = {
            "projects": {"local-proj": local_path, "slug-proj": "slug-proj"},
            "project_modes": {"slug-proj": "cloud"},
            "cloud_projects": {"slug-proj": {"local_path": str(tmp_path / "promoted")}},
        }
        result = _migrate_legacy_projects(data)
        assert result["projects"]["local-proj"] == {"path": local_path}
        assert result["projects"]["slug-proj"] == {"path": str(tmp_path / "promoted")}
        assert "project_modes" not in result

    def test_non_dict_input_passes_through(self):
        """Anything that is not a config mapping is returned untouched."""
        assert _migrate_legacy_projects("not-a-dict") == "not-a-dict"

    def test_empty_projects_returns_data_unchanged(self):
        """No projects means there is nothing to normalize below the top level."""
        assert _migrate_legacy_projects({"projects": {}}) == {"projects": {}}


class TestAutoUpdateConfig:
    """Test auto-update configuration fields."""

    def test_auto_update_defaults(self):
        """Auto-update should default on with a daily check interval."""
        config = BasicMemoryConfig()
        assert config.auto_update is True
        assert config.update_check_interval == 86400
        assert config.auto_update_last_checked_at is None

    def test_auto_update_env_overrides(self, monkeypatch):
        """Environment variables should override auto-update defaults."""
        monkeypatch.setenv("BASIC_MEMORY_AUTO_UPDATE", "false")
        monkeypatch.setenv("BASIC_MEMORY_UPDATE_CHECK_INTERVAL", "3600")

        config = BasicMemoryConfig()
        assert config.auto_update is False
        assert config.update_check_interval == 3600

    def test_auto_update_round_trip_persistence(self):
        """Auto-update values should survive save/load cycle."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            config_manager = ConfigManager()
            config_manager.config_dir = temp_path / "basic-memory"
            config_manager.config_file = config_manager.config_dir / "config.json"
            config_manager.config_dir.mkdir(parents=True, exist_ok=True)

            checked_at = datetime.now()
            test_config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                auto_update=False,
                update_check_interval=7200,
                auto_update_last_checked_at=checked_at,
            )
            config_manager.save_config(test_config)

            loaded = config_manager.load_config()
            assert loaded.auto_update is False
            assert loaded.update_check_interval == 7200
            assert loaded.auto_update_last_checked_at == checked_at


class TestAtomicConfigSave:
    """Regression tests for #940: saving config must never tear the published file.

    Long-lived readers (the MCP stdio server's mtime-based config reload, the CLI
    background auto-update thread) re-read config.json while other code saves it.
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
            config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                default_project="main",
            )
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
            config = BasicMemoryConfig(
                projects={"main": {"path": str(temp_path / "main")}},
                default_project="main",
            )

            from basic_memory.config import save_basic_memory_config

            save_basic_memory_config(config_file, config)

            assert config_file.exists()
            assert not list(temp_path.glob("*.tmp"))
