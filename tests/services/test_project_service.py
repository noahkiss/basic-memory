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
