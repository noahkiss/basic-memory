"""Tests for ProjectService."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from basic_memory import db
from basic_memory.models.project import Project
from basic_memory.schemas import (
    ProjectInfoResponse,
    ProjectStatistics,
    ActivityMetrics,
    SystemStatus,
)
from basic_memory.services.project_service import ProjectService
from basic_memory.config import ConfigManager


async def _get_project(project_service: ProjectService, name: str) -> Project | None:
    async with db.scoped_session(project_service.session_maker) as session:
        return await project_service.repository.get_by_name(session, name)


async def _find_projects(project_service: ProjectService) -> list[Project]:
    async with db.scoped_session(project_service.session_maker) as session:
        return list(await project_service.repository.find_all(session))


@pytest.mark.asyncio
async def test_list_projects(project_service: ProjectService, test_project):
    """list_projects returns the registry rows from the database."""
    projects = await project_service.list_projects()

    assert len(projects) > 0
    assert any(p.name == test_project.name for p in projects)


@pytest.mark.asyncio
async def test_get_default_project_name(project_service: ProjectService, test_project):
    """get_default_project_name reads the DB is_default flag."""
    name = await project_service.get_default_project_name()

    assert name == test_project.name


@pytest.mark.asyncio
async def test_get_default_project_name_raises_without_default(
    project_service: ProjectService, test_project
):
    """get_default_project_name raises when no project holds is_default."""
    async with db.scoped_session(project_service.session_maker) as session:
        await project_service.repository.delete(session, test_project.id)

    with pytest.raises(ValueError, match="No default project configured"):
        await project_service.get_default_project_name()


@pytest.mark.asyncio
async def test_get_system_status(project_service: ProjectService):
    """Test getting system status."""
    # Get the system status
    status = project_service.get_system_status()

    # Assert it returns a valid SystemStatus object
    assert isinstance(status, SystemStatus)
    assert status.version
    assert status.database_path
    assert status.database_size


@pytest.mark.asyncio
async def test_get_system_status_reads_watch_status_from_config_dir(
    project_service: ProjectService, tmp_path, monkeypatch
):
    """Regression guard for #742: watch-status.json is read from the configured
    data dir, not hardcoded to ~/.basic-memory."""
    import json as _json
    from basic_memory.config import WATCH_STATUS_JSON

    custom_dir = tmp_path / "instance-v" / "state"
    custom_dir.mkdir(parents=True)
    (custom_dir / WATCH_STATUS_JSON).write_text(
        _json.dumps({"running": True, "error_count": 7}), encoding="utf-8"
    )
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(custom_dir))

    status = project_service.get_system_status()

    assert status.watch_status == {"running": True, "error_count": 7}


@pytest.mark.asyncio
async def test_get_statistics(project_service: ProjectService, test_graph, test_project):
    """Test getting statistics."""
    # Get statistics
    statistics = await project_service.get_statistics(test_project.id)

    # Assert it returns a valid ProjectStatistics object
    assert isinstance(statistics, ProjectStatistics)
    assert statistics.total_entities > 0
    assert "test" in statistics.note_types


@pytest.mark.asyncio
async def test_get_activity_metrics(project_service: ProjectService, test_graph, test_project):
    """Test getting activity metrics."""
    # Get activity metrics
    metrics = await project_service.get_activity_metrics(test_project.id)

    # Assert it returns a valid ActivityMetrics object
    assert isinstance(metrics, ActivityMetrics)
    assert len(metrics.recently_created) > 0
    assert len(metrics.recently_updated) > 0


@pytest.mark.asyncio
async def test_get_project_info(project_service: ProjectService, test_graph, test_project):
    """Test getting full project info."""
    # Get project info
    info = await project_service.get_project_info(test_project.name)

    # Assert it returns a valid ProjectInfoResponse object
    assert isinstance(info, ProjectInfoResponse)
    assert info.project_name
    assert info.project_path
    assert info.default_project
    assert isinstance(info.available_projects, dict)
    assert isinstance(info.statistics, ProjectStatistics)
    assert isinstance(info.activity, ActivityMetrics)
    assert isinstance(info.system, SystemStatus)


@pytest.mark.asyncio
async def test_add_project_async(project_service: ProjectService):
    """Test adding a project with the async method."""
    test_project_name = f"test-async-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = Path(temp_dir) / "test-async-project"
        test_project_path.mkdir(parents=True, exist_ok=True)

        await project_service.add_project(test_project_name, str(test_project_path))

        project = await _get_project(project_service, test_project_name)
        assert project is not None
        assert project.name == test_project_name
        assert Path(project.path) == test_project_path

        await project_service.remove_project(test_project_name)

        assert await _get_project(project_service, test_project_name) is None


@pytest.mark.asyncio
async def test_set_default_project_async(project_service: ProjectService, test_project):
    """Test setting a project as default."""
    test_project_name = f"test-default-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-default-project")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path)
        await project_service.set_default_project(test_project_name)

        assert await project_service.get_default_project_name() == test_project_name

        project = await _get_project(project_service, test_project_name)
        assert project is not None
        assert project.is_default is True

        # The previous default is no longer default
        old_default = await _get_project(project_service, test_project.name)
        assert old_default is not None
        assert old_default.is_default is not True


@pytest.mark.asyncio
async def test_get_project_method(project_service: ProjectService):
    """Test the get_project method directly."""
    test_project_name = f"test-get-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = (Path(temp_dir) / "test-get-project").as_posix()
        os.makedirs(test_project_path, exist_ok=True)

        # Test getting a non-existent project
        result = await project_service.get_project("non-existent-project")
        assert result is None

        # Add a project
        await project_service.add_project(test_project_name, test_project_path)

        # Test getting an existing project
        result = await project_service.get_project(test_project_name)
        assert result is not None
        assert result.name == test_project_name
        assert result.path == test_project_path


@pytest.mark.asyncio
async def test_set_default_project_raises_for_unknown_project(project_service: ProjectService):
    """set_default_project raises when the project has no database row."""
    test_project_name = f"test-unknown-project-{os.urandom(4).hex()}"

    with pytest.raises(ValueError, match=f"Project '{test_project_name}' not found"):
        await project_service.set_default_project(test_project_name)


@pytest.mark.asyncio
async def test_add_project_on_empty_registry_becomes_default(
    project_service: ProjectService, test_project
):
    """add_project on an empty registry makes the new project the default."""
    async with db.scoped_session(project_service.session_maker) as session:
        await project_service.repository.delete(session, test_project.id)

    test_project_name = f"test-first-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-first-project")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path)

        assert await project_service.get_default_project_name() == test_project_name
        new_project = await _get_project(project_service, test_project_name)
        assert new_project is not None
        assert new_project.is_default is True


@pytest.mark.asyncio
async def test_add_project_with_set_default_true(project_service: ProjectService, test_project):
    """add_project(set_default=True) moves the default even when one already exists."""
    test_project_name = f"test-default-true-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-default-true")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path, set_default=True)

        assert await project_service.get_default_project_name() == test_project_name

        new_project = await _get_project(project_service, test_project_name)
        assert new_project is not None
        assert new_project.is_default is True

        old_default = await _get_project(project_service, test_project.name)
        assert old_default is not None
        assert old_default.is_default is not True

        all_projects = await _find_projects(project_service)
        default_projects = [p for p in all_projects if p.is_default is True]
        assert len(default_projects) == 1
        assert default_projects[0].name == test_project_name


@pytest.mark.asyncio
async def test_add_project_with_set_default_false(project_service: ProjectService, test_project):
    """add_project on a registry that already has a default does not repoint it."""
    test_project_name = f"test-default-false-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-default-false")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path, set_default=False)

        assert await project_service.get_default_project_name() == test_project.name

        new_project = await _get_project(project_service, test_project_name)
        assert new_project is not None
        assert new_project.is_default is not True


@pytest.mark.asyncio
async def test_add_project_default_parameter_omitted(project_service: ProjectService, test_project):
    """Adding a project without set_default behaves like set_default=False."""
    test_project_name = f"test-default-omitted-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = str(Path(temp_dir) / "test-default-omitted")
        os.makedirs(test_project_path, exist_ok=True)

        await project_service.add_project(test_project_name, test_project_path)

        assert await project_service.get_default_project_name() == test_project.name

        new_project = await _get_project(project_service, test_project_name)
        assert new_project is not None
        assert new_project.is_default is not True


@pytest.mark.asyncio
async def test_move_project(project_service: ProjectService):
    """Test moving a project to a new location."""
    test_project_name = f"test-move-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        old_path = test_root / "old-location"
        new_path = test_root / "new-location"
        old_path.mkdir(parents=True, exist_ok=True)

        await project_service.add_project(test_project_name, str(old_path))

        project = await _get_project(project_service, test_project_name)
        assert project is not None
        assert Path(project.path) == old_path

        # Move project to new location
        await project_service.move_project(test_project_name, str(new_path))

        # Verify database was updated
        updated_project = await _get_project(project_service, test_project_name)
        assert updated_project is not None
        assert Path(updated_project.path) == new_path

        # Verify new directory was created
        assert os.path.exists(new_path)


@pytest.mark.asyncio
async def test_move_project_nonexistent(project_service: ProjectService):
    """Test moving a project that doesn't exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        new_path = str(Path(temp_dir) / "new-location")

        with pytest.raises(ValueError, match="not found"):
            await project_service.move_project("nonexistent-project", new_path)


@pytest.mark.asyncio
async def test_move_project_expands_path(project_service: ProjectService):
    """Test that move_project expands ~ and relative paths."""
    test_project_name = f"test-move-expand-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        old_path = (Path(temp_dir) / "old-location").as_posix()
        os.makedirs(old_path, exist_ok=True)

        await project_service.add_project(test_project_name, old_path)

        # Use a relative path for the move
        relative_new_path = "./new-location"
        expected_absolute_path = Path(os.path.abspath(relative_new_path)).as_posix()

        try:
            await project_service.move_project(test_project_name, relative_new_path)

            # Verify the path was expanded to absolute
            updated_project = await _get_project(project_service, test_project_name)
            assert updated_project is not None
            assert updated_project.path == expected_absolute_path
        finally:
            # move_project creates the resolved directory on disk (relative to cwd here)
            shutil.rmtree(expected_absolute_path, ignore_errors=True)


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_sanitizes_paths(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT uses sanitized project name, ignoring user path.

    When project_root is set (cloud mode), the system should:
    1. Ignore the user's provided path completely
    2. Use the sanitized project name as the directory name
    3. Create a flat structure: /app/data/test-bisync instead of /app/data/documents/test bisync

    This prevents the bisync auto-discovery bug where nested paths caused duplicate project creation.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        test_cases = [
            # (project_name, user_path, expected_sanitized_name)
            # User path is IGNORED - only project name matters
            ("test", "anything/path", "test"),
            (
                "Test BiSync",
                "~/Documents/Test BiSync",
                "test-bi-sync",
            ),  # BiSync -> bi-sync (dash preserved)
            ("My Project", "/tmp/whatever", "my-project"),
            ("UPPERCASE", "~", "uppercase"),
            ("With Spaces", "~/Documents/With Spaces", "with-spaces"),
        ]

        for i, (project_name, user_path, expected_sanitized) in enumerate(test_cases):
            test_project_name = f"{project_name}-{i}"  # Make unique
            expected_final_segment = f"{expected_sanitized}-{i}"

            # Add the project - user_path should be ignored
            await project_service.add_project(test_project_name, user_path)

            project = await _get_project(project_service, test_project_name)
            assert project is not None
            actual_path = project.path

            # The path should be under project_root (resolve both to handle macOS /private/var)
            assert Path(actual_path).resolve().is_relative_to(Path(project_root_path).resolve()), (
                f"Path {actual_path} should be under {project_root_path}"
            )

            # Verify the final path segment is the sanitized project name
            path_parts = Path(actual_path).parts
            final_segment = path_parts[-1]
            assert final_segment == expected_final_segment, (
                f"Expected path segment '{expected_final_segment}', got '{final_segment}'"
            )


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_rejects_escape_attempts(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT rejects paths that try to escape the project root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        # All of these should succeed by being sanitized to paths under project_root
        # The sanitization removes dangerous patterns, so they don't escape
        safe_after_sanitization = [
            "../../../etc/passwd",
            "../../.env",
            "../../../home/user/.ssh/id_rsa",
        ]

        for i, attack_path in enumerate(safe_after_sanitization):
            test_project_name = f"project-root-attack-test-{i}"

            try:
                await project_service.add_project(test_project_name, attack_path)
            except ValueError:
                # Also acceptable for security
                continue

            # Verify it was sanitized to be under project_root (resolve to handle macOS /private/var)
            project = await _get_project(project_service, test_project_name)
            assert project is not None
            assert Path(project.path).resolve().is_relative_to(Path(project_root_path).resolve()), (
                f"Sanitized path {project.path} should be under {project_root_path}"
            )


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_without_project_root_allows_arbitrary_paths(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that without BASIC_MEMORY_PROJECT_ROOT set, arbitrary paths are allowed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Ensure project_root is not set
        if "BASIC_MEMORY_PROJECT_ROOT" in os.environ:
            monkeypatch.delenv("BASIC_MEMORY_PROJECT_ROOT")

        # Create a test directory
        test_dir = Path(temp_dir) / "arbitrary-location"
        test_dir.mkdir(parents=True, exist_ok=True)

        test_project_name = "no-project-root-test"

        # Without project_root, we should be able to use arbitrary absolute paths
        await project_service.add_project(test_project_name, str(test_dir))

        # Verify the path was accepted as-is
        project = await _get_project(project_service, test_project_name)
        assert project is not None
        assert project.path == str(test_dir)


@pytest.mark.skip(
    reason="Obsolete: project_root mode now uses sanitized project name, not user path. See test_add_project_with_project_root_sanitizes_paths instead."
)
@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_normalizes_case(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT normalizes paths to lowercase.

    NOTE: This test is obsolete. After fixing the bisync duplicate project bug,
    project_root mode now ignores the user's path and uses the sanitized project name instead.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        test_cases = [
            # (input_path, expected_normalized_path)
            ("Documents/my-project", str(project_root_path / "documents" / "my-project")),
            ("UPPERCASE/PATH", str(project_root_path / "uppercase" / "path")),
            ("MixedCase/Path", str(project_root_path / "mixedcase" / "path")),
            ("documents/Test-TWO", str(project_root_path / "documents" / "test-two")),
        ]

        for i, (input_path, expected_path) in enumerate(test_cases):
            test_project_name = f"case-normalize-test-{i}"

            # Add the project
            await project_service.add_project(test_project_name, input_path)

            # Verify the path was normalized to lowercase (resolve both to handle macOS /private/var)
            project = await _get_project(project_service, test_project_name)
            assert project is not None
            actual_path = project.path
            assert Path(actual_path).resolve() == Path(expected_path).resolve(), (
                f"Expected path {expected_path} but got {actual_path} for input {input_path}"
            )


@pytest.mark.skip(
    reason="Obsolete: project_root mode now uses sanitized project name, not user path."
)
@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_with_project_root_detects_case_collisions(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that BASIC_MEMORY_PROJECT_ROOT detects case-insensitive path collisions.

    NOTE: This test is obsolete. After fixing the bisync duplicate project bug,
    project_root mode now ignores the user's path and uses the sanitized project name instead.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up project root environment
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache so it picks up the new env var
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        # First, create a project with lowercase path
        first_project = "documents-project"
        await project_service.add_project(first_project, "documents/basic-memory")

        # Verify it was created with normalized lowercase path (resolve to handle macOS /private/var)
        first = await _get_project(project_service, first_project)
        assert first is not None
        first_path = first.path
        assert (
            Path(first_path).resolve()
            == (project_root_path / "documents" / "basic-memory").resolve()
        )

        # Now try to create a project with the same path but different case
        # This should be normalized to the same lowercase path and not cause a collision
        # since both will be normalized to the same path
        second_project = "documents-project-2"
        try:
            # This should succeed because both get normalized to the same lowercase path
            await project_service.add_project(second_project, "documents/basic-memory")
            # If we get here, both should have the exact same path
            second = await _get_project(project_service, second_project)
            assert second is not None
            assert second.path == first_path
        except ValueError:
            # This is expected if there's already a project with this exact path
            pass


@pytest.mark.asyncio
async def test_add_project_rejects_nested_child_path(project_service: ProjectService):
    """Test that adding a project nested under an existing project fails."""
    parent_project_name = f"parent-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        parent_path = (test_root / "parent").as_posix()
        os.makedirs(parent_path, exist_ok=True)

        await project_service.add_project(parent_project_name, parent_path)

        child_project_name = f"child-project-{os.urandom(4).hex()}"
        child_path = (test_root / "parent" / "child").as_posix()

        with pytest.raises(ValueError, match="nested within existing project"):
            await project_service.add_project(child_project_name, child_path)


@pytest.mark.asyncio
async def test_add_project_rejects_parent_path_over_existing_child(project_service: ProjectService):
    """Test that adding a parent project over an existing nested project fails."""
    child_project_name = f"child-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        child_path = (test_root / "parent" / "child").as_posix()
        os.makedirs(child_path, exist_ok=True)

        await project_service.add_project(child_project_name, child_path)

        parent_project_name = f"parent-project-{os.urandom(4).hex()}"
        parent_path = (test_root / "parent").as_posix()

        with pytest.raises(ValueError, match="is nested within this path"):
            await project_service.add_project(parent_project_name, parent_path)


@pytest.mark.asyncio
async def test_add_project_allows_sibling_paths(project_service: ProjectService):
    """Test that adding sibling projects (same level, different directories) succeeds."""
    project1_name = f"sibling-project-1-{os.urandom(4).hex()}"
    project2_name = f"sibling-project-2-{os.urandom(4).hex()}"

    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        project1_path = (test_root / "sibling1").as_posix()
        project2_path = (test_root / "sibling2").as_posix()
        os.makedirs(project1_path, exist_ok=True)
        os.makedirs(project2_path, exist_ok=True)

        await project_service.add_project(project1_name, project1_path)
        await project_service.add_project(project2_name, project2_path)

        assert await _get_project(project_service, project1_name) is not None
        assert await _get_project(project_service, project2_name) is not None


@pytest.mark.asyncio
async def test_add_project_rejects_deeply_nested_path(project_service: ProjectService):
    """Test that deeply nested paths are also rejected."""
    root_project_name = f"root-project-{os.urandom(4).hex()}"

    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        root_path = (test_root / "root").as_posix()
        os.makedirs(root_path, exist_ok=True)

        await project_service.add_project(root_project_name, root_path)

        nested_project_name = f"nested-project-{os.urandom(4).hex()}"
        nested_path = (test_root / "root" / "level1" / "level2" / "level3").as_posix()

        with pytest.raises(ValueError, match="nested within existing project"):
            await project_service.add_project(nested_project_name, nested_path)


@pytest.mark.skipif(os.name == "nt", reason="Project root constraints only tested on POSIX systems")
@pytest.mark.asyncio
async def test_add_project_nested_validation_with_project_root(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """Test that nested path validation works with BASIC_MEMORY_PROJECT_ROOT set."""
    with tempfile.TemporaryDirectory() as temp_dir:
        project_root_path = Path(temp_dir) / "app" / "data"
        project_root_path.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(project_root_path))

        # Invalidate config cache
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        parent_project_name = f"cloud-parent-{os.urandom(4).hex()}"
        child_project_name = f"cloud-child-{os.urandom(4).hex()}"

        # Add parent project - user path is ignored, uses sanitized project name
        await project_service.add_project(parent_project_name, "parent-folder")

        # Verify it was created using sanitized project name, not user path
        parent_project = await _get_project(project_service, parent_project_name)
        assert parent_project is not None
        # Path should use sanitized project name (cloud-parent-xxx -> cloud-parent-xxx)
        # NOT the user-provided path "parent-folder"
        assert parent_project_name.lower() in parent_project.path.lower()
        # Resolve both to handle macOS /private/var vs /var
        assert Path(parent_project.path).resolve().is_relative_to(Path(project_root_path).resolve())

        # Nested projects should still be prevented, even with user path ignored
        # Since paths use project names, this won't actually be nested
        # But we can test that two projects can coexist
        await project_service.add_project(child_project_name, "parent-folder/child-folder")

        # Both should exist with their own paths
        child_project = await _get_project(project_service, child_project_name)
        assert child_project is not None
        assert child_project_name.lower() in child_project.path.lower()


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_false(project_service: ProjectService):
    """Test that remove_project with delete_notes=False keeps directory intact."""
    test_project_name = f"test-remove-keep-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = Path(temp_dir) / "test-project"
        test_project_path.mkdir()
        test_file = test_project_path / "test.md"
        test_file.write_text("# Test Note")

        await project_service.add_project(test_project_name, str(test_project_path))

        # Remove project without deleting notes (default behavior)
        await project_service.remove_project(test_project_name, delete_notes=False)

        # Verify project is removed from the registry
        assert await _get_project(project_service, test_project_name) is None

        # Verify directory and files still exist
        assert test_project_path.exists()
        assert test_file.exists()


@pytest.mark.asyncio
async def test_remove_project_with_delete_notes_true(project_service: ProjectService):
    """Test that remove_project with delete_notes=True deletes directory."""
    test_project_name = f"test-remove-delete-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_project_path = Path(temp_dir) / "test-project"
        test_project_path.mkdir()
        test_file = test_project_path / "test.md"
        test_file.write_text("# Test Note")

        await project_service.add_project(test_project_name, str(test_project_path))

        # Remove project with delete_notes=True
        await project_service.remove_project(test_project_name, delete_notes=True)

        # Verify project is removed from the registry
        assert await _get_project(project_service, test_project_name) is None

        # Verify directory and files are deleted
        assert not test_project_path.exists()


@pytest.mark.asyncio
async def test_remove_project_delete_notes_missing_directory(project_service: ProjectService):
    """Test that remove_project with delete_notes=True handles missing directory gracefully."""
    test_project_name = f"test-remove-missing-{os.urandom(4).hex()}"
    test_project_path = f"/tmp/nonexistent-directory-{os.urandom(8).hex()}"

    await project_service.add_project(test_project_name, test_project_path)
    assert await _get_project(project_service, test_project_name) is not None

    # Remove project with delete_notes=True (should not fail even if dir doesn't exist)
    await project_service.remove_project(test_project_name, delete_notes=True)

    assert await _get_project(project_service, test_project_name) is None


@pytest.mark.asyncio
async def test_remove_project_rejects_database_default(project_service: ProjectService):
    """Test that remove_project rejects deletion when the project is the DB default."""
    test_project_name = f"test-db-default-{os.urandom(4).hex()}"
    test_project_path = f"/tmp/test-db-default-{os.urandom(8).hex()}"

    await project_service.add_project(test_project_name, test_project_path, set_default=True)

    project = await _get_project(project_service, test_project_name)
    assert project is not None
    assert project.is_default is True

    with pytest.raises(ValueError, match="Cannot remove the default project"):
        await project_service.remove_project(test_project_name, delete_notes=False)

    # Verify project still exists
    assert await _get_project(project_service, test_project_name) is not None


# --- The store-derived home, and the vocabulary that comes with it (verbs item C2) ---


@pytest.mark.asyncio
async def test_add_project_without_a_path_is_homed_in_the_store(project_service: ProjectService):
    """No import source means the project lives at `store/<external_id>/` (D3).

    The path is the id, so the id cannot be left to the row's default: it is
    drawn before the row is written and the directory is named after it.

    This also covers the nested-path exemption, and covers it by accident in
    exactly the way a user hits it: the fixtures register a project at the tmp
    home, the store sits *inside* that home, and the generic "projects cannot
    share directory trees" rule would refuse every store-derived project. It is
    skipped for a store-derived path (see `add_project`).
    """
    from basic_memory.store.history import store_path

    name = f"test-store-home-{os.urandom(4).hex()}"

    await project_service.add_project(name)

    project = await _get_project(project_service, name)
    assert project is not None
    assert Path(project.path) == store_path() / project.external_id
    assert Path(project.path).is_dir()


@pytest.mark.asyncio
async def test_add_project_is_governed_by_default(project_service: ProjectService):
    """Creation writes the default vocabulary, so records are checked (GAPS U49).

    D8 first shipped this default, reversed it to opt-in because a governed
    project refused MCP's `write_note` default type, and 2026-08-22 put it back:
    `DEFAULT_VOCABULARY` declares `note`, so the reason for the reversal is gone.
    W4 is untouched — an absent file still means ungoverned — because creation is
    the deliberate act, and the caller who wants no file says `--ungoverned`.
    """
    from basic_memory.vocabulary.model import (
        DEFAULT_VOCABULARY,
        load_vocabulary,
        vocabulary_path,
    )

    name = f"test-governed-{os.urandom(4).hex()}"

    await project_service.add_project(name)

    project = await _get_project(project_service, name)
    assert project is not None
    assert vocabulary_path(project.external_id).is_file()
    assert load_vocabulary(project.external_id) == DEFAULT_VOCABULARY


@pytest.mark.asyncio
async def test_add_project_ungoverned_writes_no_vocabulary(
    project_service: ProjectService,
):
    """`--ungoverned` is the opt-out, and it leaves no file at all (GAPS W4, U49).

    The gate on the test above: without this, "governed by default" could mean
    "governed always", and the ungoverned path W4 defines would be unreachable.
    """
    from basic_memory.vocabulary.model import load_vocabulary, vocabulary_path

    name = f"test-ungoverned-{os.urandom(4).hex()}"

    await project_service.add_project(name, governed=False)

    project = await _get_project(project_service, name)
    assert project is not None
    assert not vocabulary_path(project.external_id).exists()
    assert load_vocabulary(project.external_id) is None


@pytest.mark.asyncio
async def test_a_governed_project_refuses_an_undeclared_type(project_service: ProjectService):
    """Governing does refuse a type the vocabulary does not declare — but not `note`.

    Both halves matter and they are the D8 follow-up. `note` is MCP's default
    write type and `DEFAULT_VOCABULARY` declares it, so governing a project no
    longer breaks `write_note`; `runbook` is undeclared and is refused, so
    governing still means something. The funnel is checked directly, which is
    the layer that refuses.
    """
    from basic_memory.services.exceptions import VocabularyViolationError
    from basic_memory.services.vocabulary_enforcement import enforce_vocabulary

    name = f"test-refuses-{os.urandom(4).hex()}"
    await project_service.add_project(name, governed=True)
    project = await _get_project(project_service, name)
    assert project is not None

    note = {"id": "tnd-aaaa1111", "permalink": "tnd-aaaa1111", "title": "A Note", "source": "cli"}

    with pytest.raises(VocabularyViolationError):
        enforce_vocabulary(
            {**note, "type": "runbook"},
            project_external_id=project.external_id,
            mode="reject",
            file_path="notes/a-note.md",
        )

    # A declared type on the same project is accepted, so the refusal is about
    # the type and not about the record being malformed. `note` is one of them
    # (GAPS D8): MCP's default write survives governance.
    for accepted in ("state", "note"):
        assert (
            enforce_vocabulary(
                {**note, "type": accepted},
                project_external_id=project.external_id,
                mode="reject",
                file_path=f"notes/a-{accepted}.md",
            )
            == []
        )


@pytest.mark.asyncio
async def test_add_project_with_a_path_keeps_it(project_service: ProjectService):
    """A path argument is an import source: the notes stay where they already are.

    Positive control for the store-derived tests above — that branch is taken
    only when no path is given.
    """
    name = f"test-imported-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        source = Path(temp_dir) / "notes"
        source.mkdir(parents=True, exist_ok=True)

        await project_service.add_project(name, str(source))

        project = await _get_project(project_service, name)
        assert project is not None
        assert Path(project.path) == source


# --- The declared home (skill-homed projects) ---


@pytest.mark.asyncio
async def test_add_project_records_a_declared_external_home(project_service: ProjectService):
    """`home="external"` says the notes live where something else versions them.

    The registry keeps the intent, not just the path: a path outside the store
    is already possible for a legacy off-store project, and the two behave
    differently from here on (the off-store notice, history, `move`).
    """
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-external-home-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        skill_notes = Path(temp_dir) / "skill" / ".bm"
        skill_notes.mkdir(parents=True, exist_ok=True)

        await project_service.add_project(name, str(skill_notes), home=PROJECT_HOME_EXTERNAL)

        project = await _get_project(project_service, name)
        assert project is not None
        assert project.home == PROJECT_HOME_EXTERNAL
        assert project.is_externally_homed is True
        assert Path(project.path) == skill_notes


@pytest.mark.asyncio
async def test_add_project_leaves_the_home_undeclared_by_default(project_service: ProjectService):
    """Every project that says nothing stays NULL — the state before this column.

    Positive control for the test above: without it, "records the declaration"
    could mean "records it for everyone".
    """
    name = f"test-undeclared-home-{os.urandom(4).hex()}"

    await project_service.add_project(name)

    project = await _get_project(project_service, name)
    assert project is not None
    assert project.home is None
    assert project.is_externally_homed is False


@pytest.mark.asyncio
async def test_add_project_rejects_an_unknown_home(project_service: ProjectService):
    """The CLI reaches the service directly, so the value is checked here too."""
    name = f"test-bad-home-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        with pytest.raises(ValueError, match="Unknown project home"):
            await project_service.add_project(name, temp_dir, home="elsewhere")

    assert await _get_project(project_service, name) is None


@pytest.mark.asyncio
async def test_add_project_rejects_an_external_home_without_a_directory(
    project_service: ProjectService,
):
    """An external home names a directory; without one the row contradicts itself."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-external-no-path-{os.urandom(4).hex()}"

    with pytest.raises(ValueError, match="needs the directory"):
        await project_service.add_project(name, home=PROJECT_HOME_EXTERNAL)

    assert await _get_project(project_service, name) is None


@pytest.mark.asyncio
async def test_move_project_refuses_an_externally_homed_project(project_service: ProjectService):
    """Move only rewrites the registry path, so it would orphan external notes."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-move-external-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        skill_notes = Path(temp_dir) / "skill" / ".bm"
        skill_notes.mkdir(parents=True, exist_ok=True)
        destination = Path(temp_dir) / "somewhere-else"

        await project_service.add_project(name, str(skill_notes), home=PROJECT_HOME_EXTERNAL)

        with pytest.raises(ValueError, match="bm project adopt"):
            await project_service.move_project(name, str(destination))

        # The refusal is complete: the registry still points at the notes, and
        # the destination was never created — the check runs before the mkdir.
        project = await _get_project(project_service, name)
        assert project is not None
        assert Path(project.path) == skill_notes
        assert not destination.exists()


# --- `--home-here`: the vocabulary, and the nesting check ---


@pytest.mark.asyncio
async def test_add_project_external_home_holds_the_vocabulary(
    project_service: ProjectService, monkeypatch
):
    """Governance travels with the notes: `vocabulary.yml` lands in the home.

    The registry lookup is stubbed because the unit suite's database is
    in-memory (`DatabaseType.MEMORY`) while `lookup_project_home` opens its own
    connection to the registry file — it can never see a row this service wrote.
    What the stub does not fake is the ordering: the write still runs where
    `add_project` puts it, and the file still lands wherever the lookup says.
    """
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL
    from basic_memory.store.history import store_path
    from basic_memory.vocabulary import model as vocabulary_model

    name = f"test-home-here-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        skill_notes = Path(temp_dir) / "skill" / ".bm"

        vocabulary_model.clear_home_cache()
        monkeypatch.setattr(vocabulary_model, "lookup_project_home", lambda _id: skill_notes)
        try:
            await project_service.add_project(name, str(skill_notes), home=PROJECT_HOME_EXTERNAL)
        finally:
            vocabulary_model.clear_home_cache()

        project = await _get_project(project_service, name)
        assert project is not None
        assert project.is_externally_homed is True
        # The service created the directory the project declared as its home.
        assert skill_notes.is_dir()
        assert (skill_notes / "vocabulary.yml").is_file()
        # Not in the store, which is the whole point of the declaration.
        assert not (store_path() / project.external_id / "vocabulary.yml").exists()


@pytest.mark.asyncio
async def test_add_project_writes_the_vocabulary_after_the_row_is_committed(
    project_service: ProjectService, monkeypatch
):
    """The vocabulary write runs outside the session scope, not inside it.

    It has to: `vocabulary_path` resolves a declared home through a second
    SQLite connection, which sees committed rows only. The observable
    difference is what a failing write leaves behind — inside the scope it
    would roll the registration back, outside it the project stands.
    """
    from basic_memory.services import project_service as project_service_module

    name = f"test-vocab-order-{os.urandom(4).hex()}"

    def explode(external_id: str):
        raise OSError("no room for the vocabulary")

    monkeypatch.setattr(project_service_module, "write_default_vocabulary", explode)

    with pytest.raises(OSError, match="no room"):
        await project_service.add_project(name)

    project = await _get_project(project_service, name)
    assert project is not None


@pytest.mark.asyncio
async def test_add_project_external_home_ignores_an_enclosing_project(
    project_service: ProjectService,
):
    """A skill's `.bm` under a catch-all workspace project is the arrangement.

    A project registered at `~` or `~/.claude` encloses every skill directory
    by construction, so enforcing the nesting rule there would refuse every
    skill project. The catch-all's marker carries `scope: here` and does not
    claim the subdirectory anyway (GAPS U40).
    """
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    suffix = os.urandom(4).hex()
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        workspace.mkdir(parents=True)
        await project_service.add_project(f"test-catchall-{suffix}", str(workspace))

        await project_service.add_project(
            f"test-skill-{suffix}",
            str(workspace / "skills" / "example" / ".bm"),
            home=PROJECT_HOME_EXTERNAL,
        )

        project = await _get_project(project_service, f"test-skill-{suffix}")
        assert project is not None

        # Positive control: the same directory without the declaration is still
        # refused, so the skip is the declaration's doing and not a hole.
        with pytest.raises(ValueError, match="nested within existing project"):
            await project_service.add_project(
                f"test-undeclared-{suffix}", str(workspace / "skills" / "other" / ".bm")
            )


@pytest.mark.asyncio
async def test_add_project_ignores_an_enclosed_external_home(
    project_service: ProjectService,
):
    """The other direction: a catch-all may be registered over existing skills."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    suffix = os.urandom(4).hex()
    with tempfile.TemporaryDirectory() as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        skill_notes = workspace / "skills" / "example" / ".bm"
        await project_service.add_project(
            f"test-skill-first-{suffix}", str(skill_notes), home=PROJECT_HOME_EXTERNAL
        )

        await project_service.add_project(f"test-catchall-after-{suffix}", str(workspace))

        project = await _get_project(project_service, f"test-catchall-after-{suffix}")
        assert project is not None

    # Positive control: an undeclared project below a new one still collides.
    with tempfile.TemporaryDirectory() as other_dir:
        other = Path(other_dir) / "workspace"
        await project_service.add_project(
            f"test-legacy-{suffix}", str(other / "skills" / "example" / ".bm")
        )

        with pytest.raises(ValueError, match="is nested within this path"):
            await project_service.add_project(f"test-catchall-refused-{suffix}", str(other))


# --- `bm project adopt`: arrival on a machine yadm delivered the notes to ---


@pytest.mark.asyncio
async def test_adopt_project_registers_delivered_notes(project_service: ProjectService):
    """Arrival: the directory is here, the registry knows nothing, adopt registers it."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-adopt-new-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "skill" / ".bm"
        notes.mkdir(parents=True)

        adoption = await project_service.adopt_project(name, str(notes))

        assert adoption.action == "registered"
        assert adoption.name == name
        assert Path(adoption.path) == notes

        project = await _get_project(project_service, name)
        assert project is not None
        assert project.external_id == adoption.external_id
        assert project.home == PROJECT_HOME_EXTERNAL
        assert Path(project.path) == notes


@pytest.mark.asyncio
async def test_adopt_project_leaves_governance_to_what_arrived(project_service: ProjectService):
    """Adopt writes no vocabulary: governance travels with the notes, or it did not.

    Writing a default one here would invent a governance the human never
    declared, and dirty a directory another VCS owns. `bm doctor` is what
    reports an external project that arrived without one.
    """
    suffix = os.urandom(4).hex()
    with tempfile.TemporaryDirectory() as temp_dir:
        delivered = Path(temp_dir) / "governed" / ".bm"
        delivered.mkdir(parents=True)
        (delivered / "vocabulary.yml").write_text("types:\n  - decision\n", encoding="utf-8")
        bare = Path(temp_dir) / "ungoverned" / ".bm"
        bare.mkdir(parents=True)

        await project_service.adopt_project(f"test-adopt-governed-{suffix}", str(delivered))
        await project_service.adopt_project(f"test-adopt-bare-{suffix}", str(bare))

        # The delivered file is untouched, byte for byte.
        assert (delivered / "vocabulary.yml").read_text(encoding="utf-8") == (
            "types:\n  - decision\n"
        )
        # And none was invented where none arrived.
        assert not (bare / "vocabulary.yml").exists()


@pytest.mark.asyncio
async def test_adopt_project_run_twice_changes_nothing(project_service: ProjectService):
    """The second run mints no id and adds no row — adopt is arrival, not creation."""
    name = f"test-adopt-twice-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "skill" / ".bm"
        notes.mkdir(parents=True)

        first = await project_service.adopt_project(name, str(notes))
        second = await project_service.adopt_project(name, str(notes))

        assert second.action == "unchanged"
        assert second.external_id == first.external_id
        assert second.path == first.path

        rows = [row for row in await _find_projects(project_service) if row.name == name]
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_adopt_project_repoints_a_moved_external_home(project_service: ProjectService):
    """The skill moved on this machine: the path follows, the id does not change."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-adopt-moved-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        first_home = Path(temp_dir) / "skills" / "example" / ".bm"
        first_home.mkdir(parents=True)
        second_home = Path(temp_dir) / "skills" / "renamed" / ".bm"
        second_home.mkdir(parents=True)

        first = await project_service.adopt_project(name, str(first_home))
        moved = await project_service.adopt_project(name, str(second_home))

        assert moved.action == "repointed"
        assert moved.external_id == first.external_id
        assert Path(moved.path) == second_home

        project = await _get_project(project_service, name)
        assert project is not None
        assert Path(project.path) == second_home
        assert project.home == PROJECT_HOME_EXTERNAL


@pytest.mark.asyncio
async def test_adopt_project_refuses_a_store_homed_project(project_service: ProjectService):
    """Adopting one would point it away from `store/<id>/` and strand every note."""
    name = f"test-adopt-store-{os.urandom(4).hex()}"
    await project_service.add_project(name)
    registered = await _get_project(project_service, name)
    assert registered is not None
    store_home = registered.path

    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "skill" / ".bm"
        notes.mkdir(parents=True)

        with pytest.raises(ValueError, match="is homed in the store"):
            await project_service.adopt_project(name, str(notes))

    # The refusal changed nothing: same path, same undeclared home.
    project = await _get_project(project_service, name)
    assert project is not None
    assert project.path == store_home
    assert project.home is None


@pytest.mark.asyncio
async def test_adopt_project_retrofits_a_legacy_off_store_project(project_service: ProjectService):
    """Adopt is the retrofit path: run it where a legacy project already lives."""
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-adopt-legacy-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "notes"
        notes.mkdir(parents=True)
        await project_service.add_project(name, str(notes))
        before = await _get_project(project_service, name)
        assert before is not None
        assert before.home is None

        adoption = await project_service.adopt_project(name, str(notes))

        assert adoption.action == "adopted"
        assert adoption.external_id == before.external_id
        assert Path(adoption.path) == notes

        project = await _get_project(project_service, name)
        assert project is not None
        assert project.home == PROJECT_HOME_EXTERNAL
        assert Path(project.path) == notes


@pytest.mark.asyncio
async def test_adopt_project_refuses_a_legacy_project_at_another_path(
    project_service: ProjectService,
):
    """Positive control for the retrofit above: only its own directory adopts it.

    Adopt moves no file, so repointing a legacy project at a directory that is
    not the one its notes are in would strand them exactly as `move` would.
    """
    name = f"test-adopt-elsewhere-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "notes"
        notes.mkdir(parents=True)
        somewhere_else = Path(temp_dir) / "skill" / ".bm"
        somewhere_else.mkdir(parents=True)
        await project_service.add_project(name, str(notes))

        with pytest.raises(ValueError, match="keeps its notes at"):
            await project_service.adopt_project(name, str(somewhere_else))

        project = await _get_project(project_service, name)
        assert project is not None
        assert Path(project.path) == notes
        assert project.home is None


@pytest.mark.asyncio
async def test_adopt_project_mints_a_different_id_on_each_machine(
    project_service: ProjectService, file_service, app_config
):
    """Ids are machine-local; the name is the cross-machine key.

    Asserted so no later feature assumes `store/<id>/` names the same directory
    on two machines — the design calls this out as the cost of adopting by name.
    """
    from basic_memory.models import Base
    from basic_memory.repository.project_repository import ProjectRepository

    name = f"test-adopt-two-machines-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "skill" / ".bm"
        notes.mkdir(parents=True)

        this_machine = await project_service.adopt_project(name, str(notes))

        # A second registry, standing in for the next machine yadm delivers to.
        async with db.engine_session_factory(
            db_path=app_config.database_path, db_type=db.DatabaseType.MEMORY
        ) as (engine, other_session_maker):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            other_machine = ProjectService(
                repository=ProjectRepository(),
                file_service=file_service,
                session_maker=other_session_maker,
            )
            next_machine = await other_machine.adopt_project(name, str(notes))

    assert this_machine.name == next_machine.name
    assert this_machine.path == next_machine.path
    assert this_machine.external_id != next_machine.external_id


@pytest.mark.asyncio
async def test_add_project_refuses_an_external_home_under_project_root(
    project_service: ProjectService, config_manager: ConfigManager, monkeypatch
):
    """The two settings answer the same question differently, so both cannot hold.

    `project_root` derives every project's directory from its name and ignores
    the path it was given, which would register the project at `<root>/<name>`
    while its notes stayed in the directory it declared — indexed nowhere.
    """
    from basic_memory.project_registry import PROJECT_HOME_EXTERNAL

    name = f"test-external-under-root-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        notes = Path(temp_dir) / "skill" / ".bm"
        monkeypatch.setenv("BASIC_MEMORY_PROJECT_ROOT", str(Path(temp_dir) / "root"))

        # Invalidate the config cache so the new env var is what the service reads.
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        with pytest.raises(ValueError, match="BASIC_MEMORY_PROJECT_ROOT"):
            await project_service.add_project(name, str(notes), home=PROJECT_HOME_EXTERNAL)

    assert await _get_project(project_service, name) is None
