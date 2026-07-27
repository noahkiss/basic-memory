"""`status` must not report unindexed files as if they were queryable (GAPS.md T2).

A file written into a project directory by anything other than Basic Memory shows up in
the project-index observation immediately, but it is absent from every read path until a
reindex builds its entity row. Counting it as a plain "observed file" is a silent wrong
answer: the count says the corpus is there while search returns nothing for it.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient

from basic_memory.models import Project
from basic_memory.schemas import ProjectIndexStatusResponse


async def _get_status(client: AsyncClient, project: Project) -> ProjectIndexStatusResponse:
    response = await client.post(f"/v2/projects/{project.external_id}/status")
    assert response.status_code == 200
    return ProjectIndexStatusResponse.model_validate(response.json())


@pytest.mark.asyncio
async def test_status_marks_file_written_outside_basic_memory_as_unindexed(
    client: AsyncClient, test_project: Project
):
    """A file only the filesystem knows about must be reported as not indexed."""
    note_path = Path(test_project.path) / "notes" / "written-outside-bm.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text("# Outside\n\nNothing has indexed this.\n", encoding="utf-8")

    status = await _get_status(client, test_project)

    assert status.total_files == 1
    assert status.unindexed_file_count == 1
    observed = {observed_file.path: observed_file for observed_file in status.observed_files}
    assert observed["notes/written-outside-bm.md"].indexed is False


@pytest.mark.asyncio
async def test_status_marks_indexed_file_as_indexed(
    client: AsyncClient, test_project: Project, full_entity
):
    """A note Basic Memory wrote itself is indexed, so status must not cry wolf."""
    status = await _get_status(client, test_project)

    observed = {observed_file.path: observed_file for observed_file in status.observed_files}
    assert observed[full_entity.file_path].indexed is True
    assert status.unindexed_file_count == 0
