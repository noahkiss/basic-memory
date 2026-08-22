"""Regression coverage for retained-project recreation (#1084)."""

import json
from typing import Any

import pytest
from fastmcp import Client


def _json_content(tool_result) -> Any:
    assert len(tool_result.content) == 1
    assert tool_result.content[0].type == "text"
    return json.loads(tool_result.content[0].text)  # pyright: ignore [reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_recreate_retained_project_indexes_existing_notes(
    mcp_server,
    app,
    test_project,
    tmp_path,
):
    """Re-adding a retained local project restores read and search access."""
    project_name = "retained-project"
    project_path = tmp_path.parent / f"{tmp_path.name}-projects" / project_name
    marker = "RETAINED_PROJECT_INDEX_MARKER"

    async with Client(mcp_server) as client:
        initial_create = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": str(project_path),
                "output_format": "json",
            },
        )
        initial_payload = _json_content(initial_create)
        assert initial_payload["indexing"]["total_files"] == 0

        # A project created by the tool is governed (GAPS U49), so the write is
        # stamped with an id and its permalink equals that id (schema §2) — the
        # path-derived `retained/retained-note` no longer names it.
        written = await client.call_tool(
            "write_note",
            {
                "project": project_name,
                "title": "Retained Note",
                "directory": "retained",
                "content": f"# Retained Note\n\n{marker}",
                "output_format": "json",
            },
        )
        retained_permalink = _json_content(written)["permalink"]
        assert retained_permalink.startswith("note-")

        await client.call_tool(
            "delete_project",
            {"project_name": project_name, "delete_notes": False},
        )
        assert list(project_path.rglob("*.md")), "delete_notes=false removed retained notes"

        recreated = await client.call_tool(
            "create_memory_project",
            {
                "project_name": project_name,
                "project_path": str(project_path),
                "output_format": "json",
            },
        )
        recreated_payload = _json_content(recreated)
        assert recreated_payload["created"] is True
        assert recreated_payload["indexing"]["total_files"] >= 1
        assert recreated_payload["indexing"]["enqueued_files"] >= 1

        read_result = await client.call_tool(
            "read_note",
            {
                "project": project_name,
                "identifier": retained_permalink,
                "output_format": "json",
            },
        )
        assert marker in _json_content(read_result)["content"]

        search_result = await client.call_tool(
            "search_notes",
            {
                "project": project_name,
                "query": marker,
                "search_type": "text",
                "output_format": "json",
            },
        )
        search_payload = _json_content(search_result)
        assert any(result["title"] == "Retained Note" for result in search_payload["results"])
