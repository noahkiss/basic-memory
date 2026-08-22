"""`bm web`'s routes, driven end to end over ASGI (GAPS U41).

Real path throughout: the real app factory, its real lifespan, a real
file-backed database seeded by `conftest.py`, and real files on disk. Nothing
below stubs a query — the claims are about what a reader sees, and a stubbed
board proves nothing about which card is in which column.
"""

import re

import pytest
from httpx import AsyncClient

from tests.web.conftest import ALPHA, ALPHA_ID, BETA, GAMMA, SHARED_ID

pytestmark = pytest.mark.asyncio


def squashed(markup: str) -> str:
    """Collapse every run of whitespace, so assertions ignore template indenting."""
    return re.sub(r"\s+", " ", markup)


def lane(markup: str, project: str) -> str:
    """The markup of one project's kanban, from its `<section>` onwards."""
    marker = f'<section data-component="lane" data-project="{project}">'
    body = squashed(markup)
    if marker not in body:
        raise AssertionError(f"no lane for {project!r} in the rendered page")
    return body.split(marker, 1)[1]


def column(lane_markup: str, status: str) -> str:
    """The markup of one status column, from its opening `<li>` to the next."""
    for chunk in lane_markup.split('<li data-component="column" data-status="')[1:]:
        if chunk.startswith(f'{status}"'):
            return chunk
    raise AssertionError(f"no {status!r} column in the rendered lane")


def card(markup: str, project: str) -> str:
    """The markup of one project's overview card, from its `<article>` to the next."""
    for chunk in squashed(markup).split('<article data-component="project-card"')[1:]:
        if f'/?project={project}"' in chunk[:200]:
            return chunk
    raise AssertionError(f"no overview card for {project!r} in the rendered board")


# --- The overview: `/` with no project ---


async def test_the_overview_links_every_project(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    body = squashed(response.text)
    assert f'<h2><a href="/?project={ALPHA}">{ALPHA}</a></h2>' in body
    assert f'<h2><a href="/?project={BETA}">{BETA}</a></h2>' in body
    assert f'<h2><a href="/?project={GAMMA}">{GAMMA}</a></h2>' in body


async def test_the_overview_names_only_the_work_that_needs_a_reader(
    client: AsyncClient,
) -> None:
    """At all-projects scale a `doing` or `blocked` record earns a row; `open` does not."""
    body = (await client.get("/")).text

    assert "Rotate the deploy key" in body
    assert "Beta is stuck" in body
    assert "Move backups off-container" not in body
    # Positive control: the record the overview leaves out is on its own lane.
    assert "Move backups off-container" in (await client.get("/", params={"project": ALPHA})).text


async def test_the_overview_draws_a_stacked_status_bar(client: AsyncClient) -> None:
    """Every status with a record gets a segment sized by its total; empty ones get none."""
    alpha = card((await client.get("/")).text, ALPHA)

    assert 'title="open 3 · doing 1 · shelved 1 · done 1"' in alpha
    assert '<span data-status="open" style="flex-grow: 3"></span>' in alpha
    assert '<span data-status="doing" style="flex-grow: 1"></span>' in alpha
    assert 'data-status="blocked"' not in alpha


async def test_the_overview_edge_says_stuck_before_it_says_busy(client: AsyncClient) -> None:
    """The card's one piece of triage: blocked outranks doing, and quiet is neither."""
    body = (await client.get("/")).text

    assert 'data-edge="doing"' in card(body, ALPHA)
    assert 'data-edge="blocked"' in card(body, BETA)


async def test_a_project_with_no_live_work_still_gets_a_card(client: AsyncClient) -> None:
    """Never hide a project: quiet is a state to render, not a reason to disappear."""
    gamma = card((await client.get("/")).text, GAMMA)

    assert 'data-edge="idle"' in gamma
    assert 'data-quiet="yes"' in gamma
    assert "no lifecycle records" in gamma
    assert "0 open" in gamma


async def test_the_headline_shows_on_the_overview_card_and_on_the_lane(
    client: AsyncClient,
) -> None:
    body = (await client.get("/")).text

    assert "ship the board" in card(body, ALPHA)
    # Beta never set one, and the absence is stated rather than blank.
    assert "no headline set" in card(body, BETA)
    assert "ship the board" in lane((await client.get("/", params={"project": ALPHA})).text, ALPHA)


# --- The lane: `/?project=x` ---


async def test_a_card_lands_in_the_column_its_status_names(client: AsyncClient) -> None:
    alpha = lane((await client.get("/", params={"project": ALPHA})).text, ALPHA)

    assert "Move backups off-container" in column(alpha, "open")
    assert "Rotate the deploy key" in column(alpha, "doing")
    assert "Close the old bucket" in column(alpha, "done")
    assert "Someday work" in column(alpha, "shelved")
    # A declared status nothing sits in still gets a column — an empty `blocked`
    # column is the fact that nothing is blocked. It collapses to its header so
    # that `done` stays on screen, which is what `data-empty` records.
    assert 'data-empty="yes"' in column(alpha, "blocked")


async def test_a_plan_card_carries_a_plan_badge_and_a_task_does_not(
    client: AsyncClient,
) -> None:
    body = squashed((await client.get("/", params={"project": ALPHA})).text)

    assert '<span class="id">plan-camp0004</span> <span class="badge">plan</span>' in body
    assert '<span class="id">tnd-open0001</span> <span class="badge">plan</span>' not in body


async def test_a_statusless_record_is_filed_under_other_records(client: AsyncClient) -> None:
    """A finding has no lifecycle, so it earns a group rather than a column."""
    alpha = lane((await client.get("/", params={"project": ALPHA})).text, ALPHA)

    assert "other records (2)" in alpha
    assert "<h3>finding — 1</h3>" in alpha
    assert "<h3>inbox — 1</h3>" in alpha
    assert "In-container backup cannot work" not in column(alpha, "open")


async def test_the_lane_counts_live_shelved_and_inbox(client: AsyncClient) -> None:
    """Three open tasks plus a plan are live; the shelved task and the inbox are counts."""
    alpha = lane((await client.get("/", params={"project": ALPHA})).text, ALPHA)

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


async def test_the_record_page_opens_with_a_meta_strip(client: AsyncClient) -> None:
    """Which record, what kind, what state — before any of the body."""
    body = squashed((await client.get("/r/tnd-open0001")).text)

    assert '<span class="chip id">tnd-open0001</span>' in body
    assert '<span class="chip"><span class="key">type</span>task</span>' in body
    status_chip = '<span class="chip" data-status="open"><span class="key">status</span>open</span>'
    assert status_chip in body


async def test_the_record_page_folds_the_frontmatter_into_a_details(client: AsyncClient) -> None:
    """Folded, never trimmed: the strip shows three keys, the fold still holds all seven."""
    body = squashed((await client.get("/r/tnd-find0005")).text)

    assert '<details class="frontmatter"> <summary>frontmatter (7 keys)</summary>' in body


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


# --- Assets, search and liveness ---


async def test_the_typefaces_are_served_by_this_process(client: AsyncClient) -> None:
    """No route to the internet is the machine this board was written for."""
    response = await client.get("/static/fonts/JetBrainsMono.woff2")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("font/woff2")


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


@pytest.mark.asyncio
async def test_search_count_line_reports_the_true_match_total(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GAPS U6 on the board: the count line names every match, not the cap."""
    from basic_memory.cli.commands import brief as brief_module

    async def capped_search(session, session_maker, rows, query_text):  # noqa: ANN001
        return [], 40

    monkeypatch.setattr(brief_module, "search_pointers", capped_search)
    response = await client.get("/search", params={"q": "anything"})
    assert response.status_code == 200
    assert "40 results, showing 0" in response.text
