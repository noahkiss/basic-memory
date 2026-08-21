"""`bm web`'s routes, driven end to end over ASGI (GAPS U41).

Real path throughout: the real app factory, its real lifespan, a real
file-backed database seeded by `conftest.py`, and real files on disk. Nothing
below stubs a query — the claims are about what a reader sees, and a stubbed
board proves nothing about which card is in which column.
"""

import re

import pytest
from httpx import AsyncClient

from tests.web.conftest import ALPHA, ALPHA_ID, BETA, SHARED_ID

pytestmark = pytest.mark.asyncio


def squashed(markup: str) -> str:
    """Collapse every run of whitespace, so assertions ignore template indenting."""
    return re.sub(r"\s+", " ", markup)


def lane(markup: str, project: str) -> str:
    """The markup of one project's lane, from its `<section>` to the next."""
    for chunk in squashed(markup).split('<section class="lane">')[1:]:
        if f'/?project={project}"' in chunk[:200]:
            return chunk
    raise AssertionError(f"no lane for {project!r} in the rendered board")


def column(lane_markup: str, status: str) -> str:
    """The markup of one status column, from its opening `<li>` to the next."""
    for chunk in lane_markup.split('<li data-status="')[1:]:
        if chunk.startswith(f'{status}"'):
            return chunk
    raise AssertionError(f"no {status!r} column in the rendered lane")


# --- The board ---


async def test_the_board_shows_a_lane_for_every_project(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "Move backups off-container" in lane(response.text, ALPHA)
    assert "Beta work" in lane(response.text, BETA)


async def test_a_card_lands_in_the_column_its_status_names(client: AsyncClient) -> None:
    alpha = lane((await client.get("/")).text, ALPHA)

    assert "Move backups off-container" in column(alpha, "open")
    assert "Rotate the deploy key" in column(alpha, "doing")
    assert "Close the old bucket" in column(alpha, "done")
    assert "Someday work" in column(alpha, "shelved")
    # A declared status nothing sits in still gets a column — an empty `blocked`
    # column is the fact that nothing is blocked.
    assert "nothing" in column(alpha, "blocked")


async def test_a_plan_card_carries_a_plan_badge_and_a_task_does_not(
    client: AsyncClient,
) -> None:
    body = squashed((await client.get("/")).text)

    assert '<span class="id">plan-camp0004</span> <span class="badge">plan</span>' in body
    assert '<span class="id">tnd-open0001</span> <span class="badge">plan</span>' not in body


async def test_a_statusless_record_is_filed_under_other_records(client: AsyncClient) -> None:
    """A finding has no lifecycle, so it earns a group rather than a column."""
    alpha = lane((await client.get("/")).text, ALPHA)

    assert "other records (2)" in alpha
    assert "<h4>finding — 1</h4>" in alpha
    assert "<h4>inbox — 1</h4>" in alpha
    assert "In-container backup cannot work" not in column(alpha, "open")


async def test_the_lane_shows_the_projects_composed_headline(client: AsyncClient) -> None:
    body = (await client.get("/")).text

    assert "ship the board" in lane(body, ALPHA)
    # Beta never set one, and the absence is stated rather than blank.
    assert "no headline set" in lane(body, BETA)


async def test_the_lane_counts_live_shelved_and_inbox(client: AsyncClient) -> None:
    """Three open tasks plus a plan are live; the shelved task and the inbox are counts."""
    alpha = lane((await client.get("/")).text, ALPHA)

    assert "4 open · shelved 1 · inbox 1" in alpha


async def test_a_project_query_narrows_the_board_to_one_lane(client: AsyncClient) -> None:
    response = await client.get("/", params={"project": ALPHA})

    assert response.status_code == 200
    assert "Move backups off-container" in response.text
    assert "Beta work" not in response.text


async def test_an_unknown_project_query_is_an_addressing_failure(client: AsyncClient) -> None:
    """Contract rule 5: an unaddressable request is a failure, not an empty board."""
    response = await client.get("/", params={"project": "no-such-project"})

    assert response.status_code == 404
    assert "no-such-project" in response.text


async def test_only_the_board_refreshes_itself(client: AsyncClient) -> None:
    """A record page a reader is part-way through must not reload out from under them."""
    board = await client.get("/")
    record = await client.get("/r/tnd-find0005")

    assert '<meta http-equiv="refresh" content="30">' in board.text
    assert "http-equiv=" not in record.text


# --- One record ---


async def test_the_record_page_renders_the_body_as_html(client: AsyncClient) -> None:
    response = await client.get("/r/tnd-find0005")

    assert response.status_code == 200
    assert "<strong>the thing</strong>" in response.text
    # The body's wikilink is a link, not dead text.
    assert '<a href="/r/tnd-open0001">tnd-open0001</a>' in response.text


async def test_the_record_page_lists_the_frontmatter_as_a_table(client: AsyncClient) -> None:
    body = squashed((await client.get("/r/tnd-find0005")).text)

    assert '<th scope="row">type</th><td>finding</td>' in body
    assert '<th scope="row">area</th><td>infra</td>' in body
    assert '<th scope="row">event-date</th><td>2026-07-26</td>' in body


async def test_the_record_page_links_each_relation(client: AsyncClient) -> None:
    """The finding points at a task, so the checklist line names it and links it."""
    body = squashed((await client.get("/r/tnd-find0005")).text)

    assert "→ tnd-open0001 (open)" in body
    assert '<li><a href="/r/tnd-open0001">→ tnd-open0001 (open)' in body


async def test_the_incoming_edge_shows_on_the_record_it_points_at(client: AsyncClient) -> None:
    """GAPS U32: the record being pointed at is the one that cannot see the edge."""
    body = squashed((await client.get("/r/tnd-open0001")).text)

    assert "← relates_to by tnd-find0005" in body


async def test_the_record_page_names_the_store_relative_path(client: AsyncClient) -> None:
    body = (await client.get("/r/tnd-find0005")).text

    assert f"{ALPHA_ID}/findings/tnd-find0005--seed.md" in body


async def test_an_unknown_record_id_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/r/tnd-nope0000")

    assert response.status_code == 404
    assert "No such record" in response.text


async def test_an_id_in_two_projects_renders_a_chooser(client: AsyncClient) -> None:
    """The id alone does not address one record, and the page says so by name."""
    response = await client.get(f"/r/{SHARED_ID}")

    assert response.status_code == 300
    assert f'href="/p/{ALPHA}/r/{SHARED_ID}"' in response.text
    assert f'href="/p/{BETA}/r/{SHARED_ID}"' in response.text


async def test_the_project_scoped_route_resolves_a_shared_id(client: AsyncClient) -> None:
    response = await client.get(f"/p/{ALPHA}/r/{SHARED_ID}")

    assert response.status_code == 200
    assert "Shared id in alpha" in response.text
    assert "Shared id in beta" not in response.text


async def test_an_unknown_project_on_the_record_route_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/p/no-such-project/r/tnd-open0001")

    assert response.status_code == 404
    assert "Project not found" in response.text


# --- Search and liveness ---


async def test_search_finds_a_record_by_title(client: AsyncClient) -> None:
    response = await client.get("/search", params={"q": "backups"})

    assert response.status_code == 200
    assert "Move backups off-container" in response.text
    assert f'href="/p/{ALPHA}/r/tnd-open0001"' in response.text


async def test_search_that_matches_nothing_states_the_empty_result(client: AsyncClient) -> None:
    """Contract rule 5: an empty result is a result, stated rather than silent."""
    response = await client.get("/search", params={"q": "zzzznomatch"})

    assert response.status_code == 200
    assert "0 results" in response.text


async def test_search_without_a_query_renders_the_form(client: AsyncClient) -> None:
    response = await client.get("/search")

    assert response.status_code == 200
    assert 'name="q"' in response.text
    assert "0 results" not in response.text


async def test_healthz_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
