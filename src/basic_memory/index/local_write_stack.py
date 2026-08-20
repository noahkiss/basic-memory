"""The accepted-note write path, composed without FastAPI (verbs item A).

A native `bm` verb must reach the same write path an agent reaches — the
accepted-note mutation runner — and it must do so without importing
`basic_memory.deps`, `basic_memory.api`, `basic_memory.mcp`, or fastapi. Those
cost seconds of import time (AGENTS.md, "Measured baseline"), and `deps` is a
FastAPI composition root: importing it for its wiring drags the whole ASGI graph
in behind it. So this module re-wires the same import-safe pieces
`deps/services.py` wires, from config and a session maker.

**Two calls, in this order, or the note is half-written.** The mutation runner
writes rows and returns a *plan*; it does not put the file on disk.
`LocalNoteContentMaterializationProvider.materialize_write_change` is what writes
the markdown and indexes it. The v2 router calls both
(`api/v2/routers/knowledge_router.py`), and a caller that skips the second leaves
a note in the database with nothing on disk — the GAPS T12 shape. Every entry
point here performs both.

**Followups the router schedules in the background, this module awaits.** A CLI
process exits when the verb returns, so a task scheduled onto the event loop is a
task that never runs. Relation resolution back-resolves inbound forward
references that name the new note, and vector sync keeps semantic search current;
both run inline here rather than being scheduled.

**Every write ends in the store's history** (GAPS W3, verbs item C). The
headline file is no longer touched here: it stopped being derived from task
state with GAPS U24, and `bm headline` owns both the write and its commit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_dependencies import build_local_markdown_file_indexer
from basic_memory.index.local_notes import (
    LocalAcceptedNotePreparerFactory,
    LocalCurrentNoteContentFreshener,
)
from basic_memory.index.note_content_materialization import (
    LocalNoteContentMaterializationProvider,
)
from basic_memory.indexing.accepted_note_mutation_runner import (
    AcceptedNoteMutationDependencies,
    AcceptedNoteMutationMovePolicy,
)
from basic_memory.indexing.batch_indexer import BatchIndexer
from basic_memory.indexing.models import StorageIndexFileWriter
from basic_memory.indexing.relation_resolution import RepositoryRelationResolutionRuntime
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models import Project
from basic_memory.repository.accepted_note_repositories import AcceptedNoteRepositories
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.repository.relation_repository import RelationRepository
from basic_memory.repository.search_repository import create_search_repository
from basic_memory.runtime.note_content import (
    RuntimeAcceptedNoteChange,
    RuntimeAcceptedNoteResponse,
    RuntimeNoteContentResponsePayload,
)
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.services.entity_service import EntityService
from basic_memory.services.file_service import FileService
from basic_memory.services.link_resolver import LinkResolver
from basic_memory.services.note_content_writes import (
    NoteContentMutationService,
    NoteContentMutationServiceError,
)
from basic_memory.services.search_service import SearchService
from basic_memory.store.history import HistoryError
from basic_memory.store.write_hook import (
    HistoryOutcome,
    WriteOperation,
    check_can_record,
    record_note_write,
)

# What a write from a native verb records as its origin. The accepted-note row
# keeps it as `last_source`, so it is how a later reader tells a verb's write
# apart from the API's ("api") or the watcher's.
CLI_NOTE_WRITE_SOURCE = "cli"

# Every marker except "synced" that the note_content check constraint allows
# (`models/knowledge.py`). "failed" and "external_change_detected" are what
# materialization sets when the write did not reach disk; "pending" and "writing"
# are what the accept path left behind and nothing overwrote, which means the
# same thing from a verb's point of view. Listed rather than judged against the
# success literal because the constraint enumerates the whole set: a sixth status
# needs a migration, and that migration is where this belongs.
_UNMATERIALIZED_WRITE_STATUSES = frozenset(
    {"pending", "writing", "failed", "external_change_detected"}
)


class LocalNoteWriteError(Exception):
    """One accepted-note write was refused, with the message a verb prints.

    The verb layer turns this into one stderr line and exit 1 (OUTPUT_CONTRACT
    rule 6), so `message` is the whole user-facing text — a vocabulary rejection
    already arrives here as the checker's full explanation.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class LocalNoteWriteResult:
    """What a verb needs after a write: the note's identity and where it landed."""

    entity_id: int
    external_id: str
    permalink: str | None
    file_path: str
    title: str
    note_type: str
    # The history commit this write produced, or None when there was nothing to
    # record: an off-store project, unchanged bytes, or a create whose commit
    # failed and became a notice (GAPS W3-A).
    history_sha: str | None = None
    # Lines the verb prints after its payload (output contract rule 4). The
    # stack never prints: a notice belongs to the command the caller was already
    # reading, and a service that writes to stdout cannot be composed.
    notices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ProjectWriteBundle:
    """Everything one project's write needs, composed once per call."""

    project: Project
    mutation_service: NoteContentMutationService
    materializer: LocalNoteContentMaterializationProvider
    relation_runtime: RepositoryRelationResolutionRuntime
    search_service: SearchService


def note_write_error_message(error: NoteContentMutationServiceError) -> str:
    """Render a mutation-service rejection as one line of text.

    `detail` is either a message or the already-serialized base-checksum conflict
    dict (`services/note_content_writes.py`). The dict's "message" key is the
    human half; its checksum is for a client that rebases, which a verb does not.
    """
    detail = error.detail
    if isinstance(detail, str):
        return detail
    message = detail.get("message")
    return message if message else str(detail)


@dataclass(frozen=True, slots=True)
class LocalNoteWriteStack:
    """The local accepted-note write path, callable from a native CLI verb.

    Holds only config and a session maker. Every per-project piece — file
    service, indexer, repositories — needs the project row's id and path, so it
    is composed per call after the project resolves. A verb writes one note per
    invocation, so there is nothing to amortize by caching it.
    """

    config: BasicMemoryConfig
    session_maker: async_sessionmaker[AsyncSession]

    async def write_note(
        self,
        *,
        project_external_id: str,
        data: EntitySchema,
        source: str = CLI_NOTE_WRITE_SOURCE,
    ) -> LocalNoteWriteResult:
        """Create one note: accept it into DB state, then write and index its file."""
        bundle = await self._project_bundle(project_external_id)
        try:
            accepted = await bundle.mutation_service.create_note(
                project_external_id=project_external_id,
                data=data,
                user_profile_id=None,
                source=source,
            )
        except NoteContentMutationServiceError as error:
            raise LocalNoteWriteError(note_write_error_message(error)) from error
        return await self._materialize_and_follow_up(bundle, accepted, "create", source)

    async def update_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        data: EntitySchema,
        source: str = CLI_NOTE_WRITE_SOURCE,
    ) -> LocalNoteWriteResult:
        """Replace one note's whole content, creating it when it does not exist."""
        bundle = await self._project_bundle(project_external_id)
        operation, target = await self._write_operation(
            bundle.project, entity_external_id, "update"
        )
        _refuse_unrecordable(bundle.project.path, target, operation)
        try:
            accepted = await bundle.mutation_service.update_note(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
                data=data,
                user_profile_id=None,
                source=source,
            )
        except NoteContentMutationServiceError as error:
            raise LocalNoteWriteError(note_write_error_message(error)) from error
        return await self._materialize_and_follow_up(bundle, accepted, operation, source)

    async def edit_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        data: EditEntityRequest,
        source: str = CLI_NOTE_WRITE_SOURCE,
    ) -> LocalNoteWriteResult:
        """Edit one existing note in place (append, replace a section, and so on)."""
        bundle = await self._project_bundle(project_external_id)
        operation, target = await self._write_operation(bundle.project, entity_external_id, "edit")
        _refuse_unrecordable(bundle.project.path, target, operation)
        try:
            accepted = await bundle.mutation_service.edit_note(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
                data=data,
                user_profile_id=None,
                source=source,
            )
        except NoteContentMutationServiceError as error:
            raise LocalNoteWriteError(note_write_error_message(error)) from error
        return await self._materialize_and_follow_up(bundle, accepted, operation, source)

    async def delete_note(
        self,
        *,
        project_external_id: str,
        entity_external_id: str,
        note_path: str,
    ) -> HistoryOutcome:
        """Delete one note: remove its rows, remove its file, record the deletion.

        The same mutation/materialization pair the v2 router's delete endpoint
        calls, so the index ends in the same state an API delete leaves it in.
        ``note_path`` is the record's project-relative file path, resolved by the
        caller before the rows disappear — after the mutation there is nothing
        left to ask. The deletion commit is what makes `bm rm` recoverable
        (GAPS U27): the content is in the parent commit, and `bm undo` restores
        it.
        """
        bundle = await self._project_bundle(project_external_id)
        # Preflight, same as update/edit: a delete whose history cannot record it
        # would be an unrecoverable loss, which is W3-A's refusal case.
        _refuse_unrecordable(bundle.project.path, note_path, "delete")
        try:
            accepted = await bundle.mutation_service.delete_note(
                project_external_id=project_external_id,
                entity_external_id=entity_external_id,
            )
        except NoteContentMutationServiceError as error:
            raise LocalNoteWriteError(note_write_error_message(error)) from error
        await bundle.materializer.materialize_delete_change(accepted)

        try:
            return record_note_write(
                project_path=bundle.project.path,
                note_path=note_path,
                operation="delete",
                actor=CLI_NOTE_WRITE_SOURCE,
            )
        except HistoryError as error:
            raise LocalNoteWriteError(str(error)) from error

    async def _materialize_and_follow_up(
        self,
        bundle: _ProjectWriteBundle,
        accepted: RuntimeAcceptedNoteChange[RuntimeNoteContentResponsePayload],
        operation: WriteOperation,
        actor: str,
    ) -> LocalNoteWriteResult:
        """Write the file, index it, run the followups, and report what landed."""
        materialized = await bundle.materializer.materialize_write_change(accepted)
        result = local_note_write_result(materialized.payload)

        # Back-resolve forward references that name this note. The router
        # schedules this; a verb awaits it, because the process is about to exit.
        await bundle.relation_runtime.resolve_relations()
        if self.config.semantic_search_enabled:
            await bundle.search_service.sync_entity_vectors(result.entity_id)
        return await self._record(bundle.project, result, operation, actor)

    async def _write_operation(
        self,
        project: Project,
        entity_external_id: str,
        operation: WriteOperation,
    ) -> tuple[WriteOperation, str]:
        """Label a write by whether it has prior content to lose, and name it.

        `update_note` creates the note when it does not exist, and an edit of a
        note whose file is gone is not an overwrite either. W3-A's destructive
        half is about content that exists: calling those a create keeps the
        preflight from refusing a write that can lose nothing, and keeps the
        message honest if the commit fails afterwards.

        The name it returns is the file path when there is one, because that is
        what the refusal has to show the reader; the external id is the only
        thing available before the note exists.
        """
        async with db.scoped_session(self.session_maker) as session:
            entity = await EntityRepository(project_id=project.id).get_by_external_id(
                session, entity_external_id
            )
        if entity is None:
            return "create", entity_external_id
        # A row can outlive its file — a half-written note, or one deleted
        # outside `bm`. With nothing on disk there is no prior content to protect.
        if not Path(project.path, entity.file_path).exists():
            return "create", entity.file_path
        return operation, entity.file_path

    async def _record(
        self,
        project: Project,
        result: LocalNoteWriteResult,
        operation: WriteOperation,
        actor: str,
    ) -> LocalNoteWriteResult:
        """Commit the written note as one history entry (GAPS W3).

        The headline used to be refreshed and committed here; since GAPS U24 it
        is composed by `bm headline` and no note write touches it.
        """
        try:
            outcome = record_note_write(
                project_path=project.path,
                note_path=result.file_path,
                operation=operation,
                actor=actor,
            )
        except HistoryError as error:
            raise LocalNoteWriteError(str(error)) from error
        return _with_history(result, outcome)

    async def _project_bundle(self, project_external_id: str) -> _ProjectWriteBundle:
        """Compose this project's write stack, or refuse an unknown project."""
        async with db.scoped_session(self.session_maker) as session:
            project = await ProjectRepository().get_by_external_id(session, project_external_id)
        if project is None:
            raise LocalNoteWriteError(f"Project not found: '{project_external_id}'")
        return self._build_bundle(project)

    def _build_bundle(self, project: Project) -> _ProjectWriteBundle:
        """Wire what `deps/services.py` wires, from config and the session maker."""
        project_path = Path(project.path)
        entity_parser = EntityParser(project_path)
        markdown_processor = MarkdownProcessor(entity_parser, app_config=self.config)
        file_service = FileService(project_path, markdown_processor, app_config=self.config)

        entity_repository = EntityRepository(project_id=project.id)
        observation_repository = ObservationRepository(project_id=project.id)
        relation_repository = RelationRepository(project_id=project.id)
        search_repository = create_search_repository(
            self.session_maker,
            project_id=project.id,
            app_config=self.config,
        )
        search_service = SearchService(
            search_repository,
            entity_repository,
            file_service,
            self.session_maker,
        )
        link_resolver = LinkResolver(
            entity_repository,
            search_service,
            self.session_maker,
            self.config,
        )
        entity_service = EntityService(
            entity_parser,
            entity_repository,
            observation_repository,
            relation_repository,
            file_service,
            link_resolver,
            self.session_maker,
            search_service=search_service,
            app_config=self.config,
        )
        file_indexer = build_local_markdown_file_indexer(
            project_id=project.id,
            file_service=file_service,
            session_maker=self.session_maker,
            entity_repository=entity_repository,
            batch_indexer=BatchIndexer(
                app_config=self.config,
                entity_service=entity_service,
                entity_repository=entity_repository,
                relation_repository=relation_repository,
                search_service=search_service,
                file_writer=StorageIndexFileWriter(storage=file_service),
                session_maker=self.session_maker,
            ),
            search_service=search_service,
        )

        accepted_note_repositories = AcceptedNoteRepositories()
        mutation_service = NoteContentMutationService(
            session_maker=self.session_maker,
            mutation_dependencies=AcceptedNoteMutationDependencies(
                project_repository=ProjectRepository(),
                lookup_repositories=accepted_note_repositories,
                preparer_factory=LocalAcceptedNotePreparerFactory(
                    session_maker=self.session_maker,
                    app_config=self.config,
                ),
                write_repositories=accepted_note_repositories,
                move_policy=AcceptedNoteMutationMovePolicy(
                    disable_permalinks=self.config.disable_permalinks,
                    update_permalinks_on_move=self.config.update_permalinks_on_move,
                ),
                # The local filesystem is the source of truth: a create over a
                # file that exists on disk but is not yet indexed is rejected
                # rather than allowed to diverge DB and file.
                verify_storage_absent_on_create=True,
            ),
            content_freshener=LocalCurrentNoteContentFreshener(
                entity_repository=entity_repository,
                file_service=file_service,
                file_indexer=file_indexer,
                session_maker=self.session_maker,
            ),
        )
        return _ProjectWriteBundle(
            project=project,
            mutation_service=mutation_service,
            # No relation-resolution scheduler: this stack awaits resolution
            # itself, and a scheduled task would outlive the process.
            materializer=LocalNoteContentMaterializationProvider(
                session_maker=self.session_maker,
                file_service=file_service,
                file_indexer=file_indexer,
            ),
            relation_runtime=RepositoryRelationResolutionRuntime(
                session_maker=self.session_maker,
                relation_repository=relation_repository,
                entity_repository=entity_repository,
                link_resolver=link_resolver,
                entity_indexer=search_service,
            ),
            search_service=search_service,
        )


def _refuse_unrecordable(project_path: str, target: str, operation: WriteOperation) -> None:
    """Stop an overwrite the history cannot record, before it destroys anything.

    W3-A's table: a create warns and keeps the note, an overwrite refuses. The
    check runs here rather than after the write because a refusal issued after
    the file has been replaced protects nothing.
    """
    try:
        check_can_record(project_path, target, operation)
    except HistoryError as error:
        raise LocalNoteWriteError(str(error)) from error


def _with_history(result: LocalNoteWriteResult, outcome: HistoryOutcome) -> LocalNoteWriteResult:
    """Attach the history commit and every notice the write produced."""
    return replace(result, history_sha=outcome.sha, notices=outcome.notices)


def local_note_write_result(
    payload: RuntimeNoteContentResponsePayload,
) -> LocalNoteWriteResult:
    """Read the write's outcome, refusing a write whose file never reached disk.

    An accepted write whose materialization failed — or never ran — still returns
    a payload: the row is committed and only the file is missing. Reporting that
    as success is the GAPS T12 failure, so it is an error here instead.
    """
    if not isinstance(payload, RuntimeAcceptedNoteResponse):  # pragma: no cover - local runtime
        raise RuntimeError(f"Unexpected accepted-note payload shape: {type(payload).__name__}")

    if payload.file_write_status in _UNMATERIALIZED_WRITE_STATUSES:
        reason = payload.last_materialization_error or payload.file_write_status
        raise LocalNoteWriteError(f"Wrote no file for '{payload.file_path}': {reason}")

    return LocalNoteWriteResult(
        entity_id=payload.entity_id,
        external_id=payload.external_id,
        permalink=payload.permalink,
        file_path=payload.file_path,
        title=payload.title,
        note_type=payload.note_type,
    )


def build_local_note_write_stack(
    config: BasicMemoryConfig,
    session_maker: async_sessionmaker[AsyncSession],
) -> LocalNoteWriteStack:
    """Build the write stack from an already-opened database.

    Config and the session maker are passed in rather than read here: the caller
    owns the engine lifecycle (`db.get_or_create_db` plus shutdown), which is the
    same contract `cli/direct.py` commands run under.
    """
    return LocalNoteWriteStack(config=config, session_maker=session_maker)


async def direct_note_writer() -> LocalNoteWriteStack:
    """Build the write stack the way a native verb gets one.

    Mirrors `cli/direct.py`'s direct_* helpers: open (or reuse) the process
    engine, make sure the project registry exists, and hand back a stack. It
    lives here rather than in `cli/direct.py` only because that file is being
    edited elsewhere in this phase; moving it is a later, mechanical change.
    """
    # Deferred: the config/registry path pulls in more than a verb needs at
    # import time — only when a command actually runs.
    from basic_memory.config import ConfigManager
    from basic_memory.services.initialization import ensure_project_registry

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config, bootstrap=False)
    return build_local_note_write_stack(config, session_maker)
