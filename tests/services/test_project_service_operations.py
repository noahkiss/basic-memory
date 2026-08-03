"""Additional tests for ProjectService operations."""

import os
import tempfile
from pathlib import Path

import pytest

from basic_memory import db
from basic_memory.services.project_service import ProjectService


@pytest.mark.asyncio
async def test_get_project_from_database(project_service: ProjectService):
    """Test getting projects from the database."""
    # Generate unique project name for testing
    test_project_name = f"test-project-{os.urandom(4).hex()}"
    with tempfile.TemporaryDirectory() as temp_dir:
        test_root = Path(temp_dir)
        test_path = str(test_root / "test-project")

        # Make sure directory exists
        os.makedirs(test_path, exist_ok=True)

        try:
            # Add a project to the database
            project_data = {
                "name": test_project_name,
                "path": test_path,
                "permalink": test_project_name.lower().replace(" ", "-"),
                "is_active": True,
                "is_default": False,
            }
            async with db.scoped_session(project_service.session_maker) as session:
                await project_service.repository.create(session, project_data)

            # Verify we can get the project
            async with db.scoped_session(project_service.session_maker) as session:
                project = await project_service.repository.get_by_name(session, test_project_name)
            assert project is not None
            assert project.name == test_project_name
            assert project.path == test_path

        finally:
            # Clean up
            async with db.scoped_session(project_service.session_maker) as session:
                project = await project_service.repository.get_by_name(session, test_project_name)
                if project:
                    await project_service.repository.delete(session, project.id)
