"""The `bm web` server: a read-only board over every project on this machine.

**No route here writes anything.** v1 answers one question — what is stored
where, and what state is it in — for a human at a browser. Marking a card is
still `bm mark`, and the board picks the change up on its next refresh.

Three shape decisions, each a reversal of what the surrounding tree does:

- **Its own lifespan.** `api/app.py`'s lifespan starts a watch coordinator, and
  a board that indexed files while you read it would be a background writer
  wearing a reader's clothes. This one opens the database, ensures the project
  registry, and stops.
- **One session per request, passed down.** The engine's pool holds a single
  connection, so every query in `web/queries.py` takes the request's session
  rather than opening its own — the constraint `search_pointers` already
  carries.
- **Nothing is fetched from the internet.** One inline stylesheet, no scripts,
  and the two typefaces served from this process's own `/static` mount. The
  operator's browser may be on a machine with no route out, and a board that
  renders unstyled there is a board nobody trusts.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.staticfiles import StaticFiles

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.cli.direct import ResolvedRecord

TEMPLATE_DIR = Path(__file__).parent / "templates"

# The typefaces and nothing else. Vendored rather than linked so the board looks
# the same on a machine with no route to the internet, which is the machine this
# server was written for.
STATIC_DIR = Path(__file__).parent / "static"

# How often the board reloads itself. A board is a wall display as much as a
# page: it is read at a glance, and a stale glance is worse than no glance. Long
# enough that a `bm mark` shows up while you are still looking at the terminal,
# short enough that the query cost stays invisible.
REFRESH_SECONDS = 30


def environment() -> Environment:
    """The Jinja environment the routes render through.

    Autoescape is on for every template, not just by extension: record titles,
    headlines and frontmatter values are all corpus content, and an escape rule
    keyed on a filename is a rule that stops applying the day a template is
    renamed.
    """
    return Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )


@dataclass(frozen=True, slots=True)
class RelationLine:
    """One relation on the record page: the sentence, and where it points."""

    text: str
    record_id: str


def page(request: Request, template: str, *, status_code: int = 200, **context) -> HTMLResponse:
    """Render one template against the app's environment."""
    rendered = request.app.state.templates.get_template(template).render(**context)
    return HTMLResponse(rendered, status_code=status_code)


@asynccontextmanager
async def session_for(request: Request) -> AsyncIterator["AsyncSession"]:
    """One session for the whole request, opened from the app's session maker."""
    from basic_memory import db

    async with db.scoped_session(request.app.state.session_maker) as session:
        yield session


async def project_names(session: "AsyncSession") -> list[str]:
    """Every registered project's name, ordered — what the picker lists.

    Ordered by name rather than by registry id, for the reason
    `direct_project_refs` sorts: a registry that did not change must render the
    same navigation twice.
    """
    from basic_memory.cli.direct import projects_in_scope

    return sorted(row.name for row in await projects_in_scope(session, None))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the database once at boot, and shut it down on exit.

    Deliberately not `api/app.py`'s lifespan: that one starts a watch
    coordinator, and this server must never write.
    """
    # Deferred to boot rather than import: the database bootstrap is the one
    # slow thing the server does, and it belongs where a failure is a failed
    # start rather than a failed import.
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _engine, session_maker = await db.get_or_create_db(config.database_path, config=config)
    # bootstrap=False for the same reason every native read verb passes it: a
    # server starting up must not invent a project nobody asked for.
    await ensure_project_registry(config, bootstrap=False)
    app.state.session_maker = session_maker
    app.state.templates = environment()
    try:
        yield
    finally:
        await db.shutdown_db()


def create_app() -> FastAPI:
    """Build the board server.

    A factory rather than a module-level app: the CLI builds one per process,
    and tests build one per test against their own temporary home. A module
    global would share the first caller's database with every later one.
    """
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # --- The board ---

    @app.get("/", response_class=HTMLResponse)
    async def board(request: Request, project: str | None = None) -> HTMLResponse:
        from basic_memory.web.queries import build_lanes, overview

        async with session_for(request) as session:
            known = await project_names(session)
            # Trigger: `?project=` names something the registry does not hold.
            # Why: an unaddressable request is a failure, not an empty board
            #     (contract rule 5) — a blank page would read as "this project
            #     has no work" rather than "there is no such project".
            # Outcome: 404 with the name, and the picker to get back from.
            if project is not None and project not in known:
                return page(
                    request,
                    "error.html",
                    status_code=404,
                    title="Unknown project",
                    message=f"No project named {project!r} is registered on this machine.",
                    projects=known,
                )
            lanes = await build_lanes(session, project)

        # Trigger: no `?project=`, so this is every project on the machine.
        # Why: a kanban per project is a page tens of thousands of pixels tall
        #     on a real machine, and nothing on it is legible at that scale.
        # Outcome: the unscoped board renders one summary card per project, and
        #     the columns appear only once a project is named.
        return page(
            request,
            "board.html",
            lanes=lanes,
            summaries=() if project else overview(lanes),
            projects=known,
            selected=project,
            refresh_seconds=REFRESH_SECONDS,
        )

    # --- One record ---

    @app.get("/r/{record_id}", response_class=HTMLResponse)
    async def record_anywhere(request: Request, record_id: str) -> HTMLResponse:
        return await show_record(request, None, record_id)

    @app.get("/p/{project}/r/{record_id}", response_class=HTMLResponse)
    async def record_in_project(request: Request, project: str, record_id: str) -> HTMLResponse:
        return await show_record(request, project, record_id)

    # --- Search ---

    @app.get("/search", response_class=HTMLResponse)
    async def search(request: Request, q: str = "") -> HTMLResponse:
        from basic_memory.cli.commands.brief import ProjectRow, search_pointers
        from basic_memory.cli.direct import projects_in_scope

        query_text = q.strip()
        hits: Sequence[object] = ()
        async with session_for(request) as session:
            rows = sorted(await projects_in_scope(session, None), key=lambda row: row.name)
            known = [row.name for row in rows]
            if query_text:
                # The caller's session goes through: the pool holds one
                # connection, so letting the search repository open its own
                # inside this scope would deadlock.
                hits = await search_pointers(
                    session,
                    request.app.state.session_maker,
                    [
                        ProjectRow(id=row.id, name=row.name, external_id=row.external_id)
                        for row in rows
                    ],
                    query_text,
                )

        return page(request, "search.html", query=query_text, hits=hits, projects=known)

    # --- Liveness ---

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        """Whether the process is up. Deliberately does not touch the database.

        The systemd unit and anything in front of it poll this, and a check that
        queried SQLite would report a slow disk as a dead server.
        """
        return JSONResponse({"ok": True})

    return app


async def show_record(request: Request, project: str | None, record_id: str) -> HTMLResponse:
    """Resolve one record and render it, or render why it could not be resolved."""
    from basic_memory.cli.direct import AmbiguousRecord, RecordNotFound, resolve_record

    async with session_for(request) as session:
        known = await project_names(session)
        try:
            found = await resolve_record(session, project, record_id)
        except ValueError as exc:
            # An unknown project name, reachable only from the scoped route.
            return page(
                request,
                "error.html",
                status_code=404,
                title="Unknown project",
                message=str(exc),
                projects=known,
            )
        except RecordNotFound:
            where = f"project {project!r}." if project else "any project on this machine."
            return page(
                request,
                "error.html",
                status_code=404,
                title="No such record",
                message=f"No record {record_id!r} in {where}",
                projects=known,
            )
        except AmbiguousRecord:
            # Re-resolve per project rather than splitting the exception's
            # message: the chooser needs the project names as data, and a page
            # built out of an error string breaks silently the day that string
            # is reworded.
            candidates = [name for name in known if await holds_record(session, name, record_id)]
            # 300 Multiple Choices is exactly what happened: the id addresses
            # more than one record and the body lists them.
            return page(
                request,
                "chooser.html",
                status_code=300,
                record_id=record_id,
                candidates=candidates,
                projects=known,
            )

    return render_record(request, found, known)


async def holds_record(session: "AsyncSession", project: str, record_id: str) -> bool:
    """Whether one named project holds a record with this exact id."""
    from basic_memory.cli.direct import RecordNotFound, resolve_record

    try:
        await resolve_record(session, project, record_id)
    except RecordNotFound:
        return False
    return True


def render_record(request: Request, found: "ResolvedRecord", projects: list[str]) -> HTMLResponse:
    """Read the record's file and render the page, or say why it cannot be read."""
    from basic_memory.store.history import store_path
    from basic_memory.web.render import render_body, split_frontmatter

    # Trigger: the record is indexed but its file is not on disk.
    # Why: a note can exist in the database with nothing materialized
    #     (GAPS T12), and rendering an empty body would report that as an empty
    #     record — the same failure `bm show` refuses to make.
    # Outcome: name the path that is missing, and say it as a 404.
    if not found.path.is_file():
        return page(
            request,
            "error.html",
            status_code=404,
            title="File missing",
            message=f"{found.record_id} is indexed but its file is missing: {found.path}",
            projects=projects,
        )

    # `errors="replace"` rather than a raise: this page's job is to show what is
    # on disk, and one bad byte in a note is a rendering artifact, not a reason
    # to refuse the whole record. `bm show` echoes the bytes untouched; a browser
    # has no such option.
    text = found.path.read_bytes().decode("utf-8", errors="replace")
    parsed = split_frontmatter(text)

    try:
        location = str(found.path.relative_to(store_path()))
    except ValueError:
        # A project registered at a path of its own, from before the store
        # became every project's home (decision D3). Its files sit outside the
        # store, so the full path is the only honest answer.
        location = str(found.path)

    return page(
        request,
        "record.html",
        record=found,
        metadata=parsed.metadata,
        body=render_body(parsed.body),
        location=location,
        relations=[
            RelationLine(text=item.describe(), record_id=item.record_id)
            for group in (found.superseded_by, found.references, found.referenced_by)
            for item in group
        ],
        projects=projects,
    )
