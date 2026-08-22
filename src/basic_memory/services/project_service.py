"""Project management service for Basic Memory."""

import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence


from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.models import Project
from basic_memory.project_registry import PROJECT_HOME_EXTERNAL
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas import (
    ActivityMetrics,
    EmbeddingStatus,
    ProjectInfoResponse,
    ProjectStatistics,
    SystemStatus,
)
from basic_memory.schemas.project_info import ProjectAdoptResponse
from basic_memory.config import (
    WATCH_STATUS_JSON,
    ConfigManager,
    get_project_config,
    ProjectConfig,
)
from basic_memory.store.history import store_path
from basic_memory.store.write_hook import project_store_prefix
from basic_memory.utils import generate_permalink
from basic_memory.vocabulary.model import write_default_vocabulary

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.services.file_service import FileService


class ProjectService:
    """Service for managing Basic Memory projects."""

    repository: ProjectRepository

    def __init__(
        self,
        repository: ProjectRepository,
        session_maker: async_sessionmaker[AsyncSession],
        file_service: Optional["FileService"] = None,
    ):
        """Initialize the project service."""
        super().__init__()
        self.repository = repository
        self.session_maker = session_maker
        self.file_service = file_service

    @property
    def config_manager(self) -> ConfigManager:
        """Get a ConfigManager instance.

        Returns:
            Fresh ConfigManager instance for each access
        """
        return ConfigManager()

    @property
    def config(self) -> ProjectConfig:  # pragma: no cover
        """Get the current project configuration.

        Returns:
            Current project configuration
        """
        return get_project_config()

    async def get_default_project_name(self) -> str:
        """Get the name of the project flagged ``is_default`` in the registry."""
        async with db.scoped_session(self.session_maker) as session:
            db_default = await self.repository.get_default_project(session)
        if db_default is None:
            raise ValueError("No default project configured")
        return db_default.name

    async def list_projects(self) -> Sequence[Project]:
        """List all projects without loading entity relationships.

        Returns only basic project fields (name, path, etc.) without
        eager loading the entities relationship which could load thousands
        of entities for large knowledge bases.
        """
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.find_all(session, use_load_options=False)

    async def get_project(self, name: str) -> Optional[Project]:
        """Get the file path for a project by name or permalink."""
        async with db.scoped_session(self.session_maker) as session:
            return await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)

    def _check_nested_paths(self, path1: str, path2: str) -> bool:
        """Check if two paths are nested (one is a prefix of the other).

        Args:
            path1: First path to compare
            path2: Second path to compare

        Returns:
            True if one path is nested within the other, False otherwise

        Examples:
            _check_nested_paths("/foo", "/foo/bar")     # True (child under parent)
            _check_nested_paths("/foo/bar", "/foo")     # True (parent over child)
            _check_nested_paths("/foo", "/bar")         # False (siblings)
        """
        # Normalize paths to ensure proper comparison
        p1 = Path(path1).resolve()
        p2 = Path(path2).resolve()

        # Check if either path is a parent of the other
        try:
            # Check if p2 is under p1
            p2.relative_to(p1)
            return True
        except ValueError:
            # Not nested in this direction, check the other
            try:
                # Check if p1 is under p2
                p1.relative_to(p2)
                return True
            except ValueError:
                # Not nested in either direction
                return False

    async def add_project(
        self,
        name: str,
        path: str | None = None,
        set_default: bool = False,
        governed: bool = True,
        home: str | None = None,
    ) -> None:
        """Add a new project to the registry.

        Args:
            name: The name of the project
            path: An import source to adopt as the project's directory. Omit it to
                give the project its store-derived home, which is the design
                (AGENTS.md; verbs decision D3): note content lives under
                ``store/<external_id>/`` by default, and that is what puts every write in the
                history repo. A path argument means "the notes are already here",
                and the project keeps living there: W6 closed without an importer,
                so nothing moves it. Such a project takes D3's per-write notice
                unless ``home`` says something else versions its directory.
            set_default: Whether to set this project as the default
            governed: Write ``DEFAULT_VOCABULARY`` into the project's store
                directory, so the checker runs on every write to it. **On by
                default** since GAPS U49 (2026-08-22): a new project is governed
                unless the caller opts out. Passing False leaves no
                ``vocabulary.yml``, which is what "ungoverned" means (GAPS W4).
            home: Declare where the notes live. ``None`` is the default — the
                store, or wherever ``path`` points. ``PROJECT_HOME_EXTERNAL``
                says the directory is versioned by something else (a Claude Code
                skill under yadm), which silences the off-store notice and keeps
                the project's ``vocabulary.yml`` beside its records.

        Raises:
            ValueError: If the project already exists, the path collides with an
                existing project, or ``home`` is neither None nor
                ``PROJECT_HOME_EXTERNAL``
        """
        # Fail fast on the declared home. The API validates it as a Literal, but
        # the CLI reaches this service directly (AGENTS.md, "Measured baseline"),
        # so the invariant has to hold here too: an unreadable value would sit in
        # the registry as a row no reader can interpret.
        if home is not None and home != PROJECT_HOME_EXTERNAL:
            raise ValueError(
                f"Unknown project home '{home}'; the only declared home is "
                f"'{PROJECT_HOME_EXTERNAL}'"
            )
        # An external home names a directory by definition — that directory is
        # the whole point. Without a path the project would be store-homed and
        # the declaration would contradict its own row.
        if home == PROJECT_HOME_EXTERNAL and path is None:
            raise ValueError(
                f"A project homed '{PROJECT_HOME_EXTERNAL}' needs the directory its notes live in"
            )

        # If project_root is set, constrain all projects to that directory
        project_root = self.config_manager.config.project_root

        # Trigger: a declared external home under BASIC_MEMORY_PROJECT_ROOT.
        # Why: project_root ignores the caller's path entirely and derives the
        #     directory from the project name (below), so the row would point at
        #     `<root>/<name>` while the notes stayed in the directory the caller
        #     declared — registered, indexed nowhere, silently wrong.
        # Outcome: refuse. The two settings answer the same question differently.
        if home == PROJECT_HOME_EXTERNAL and project_root:
            raise ValueError(
                f"BASIC_MEMORY_PROJECT_ROOT is set to {project_root}, which puts every "
                f"project under it. A project homed '{PROJECT_HOME_EXTERNAL}' lives in the "
                "directory it names instead, so the two cannot both hold."
            )
        sanitized_name = None
        # Set only for a store-derived project: the store path embeds the id, so
        # the row cannot be allowed to generate its own default afterwards.
        store_external_id: str | None = None
        if project_root:
            base_path = Path(project_root)

            # When project_root is set, ignore the user's path completely and use the
            # sanitized project name as the directory name. This keeps the tree flat:
            # /data/my-project instead of /data/documents/my project.
            sanitized_name = generate_permalink(name)

            # Construct path using sanitized project name only
            resolved_path = (base_path / sanitized_name).resolve().as_posix()

            # Verify the resolved path is actually under project_root
            if not resolved_path.startswith(base_path.resolve().as_posix()):  # pragma: no cover
                raise ValueError(
                    f"BASIC_MEMORY_PROJECT_ROOT is set to {project_root}. "
                    f"All projects must be created under this directory. Invalid path: {path}"
                )  # pragma: no cover

        elif path is None:
            # Decision point: no import source, so the project gets its own store
            # directory. The id has to exist before the path does — the path *is*
            # the id — so it is drawn here rather than left to the row's default.
            store_external_id = str(uuid.uuid4())
            resolved_path = (store_path() / store_external_id).as_posix()

        else:
            resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

        # Bound before the scope so it is unambiguously in scope after it: the
        # vocabulary write below needs the new project's id, and it has to run
        # after the session commits (see the constraint note there).
        new_external_id: str | None = None

        async with db.scoped_session(self.session_maker) as session:
            existing_projects = await self.repository.find_all(session, use_load_options=False)

            # The name/permalink uniqueness this used to get from the config
            # registry is now the `project` table's unique index. Check it up
            # front so a duplicate is a ValueError the API renders as a 400,
            # not an IntegrityError from three layers down.
            new_permalink = generate_permalink(name)
            for existing in existing_projects:
                if generate_permalink(existing.name) == new_permalink:
                    raise ValueError(f"Project '{name}' already exists")

            if project_root:
                # Check for case-insensitive path collisions with existing projects
                for existing in existing_projects:
                    if (
                        existing.path.lower() == resolved_path.lower()
                        and existing.path != resolved_path
                    ):
                        raise ValueError(  # pragma: no cover
                            f"Path collision detected: '{resolved_path}' conflicts with existing project "
                            f"'{existing.name}' at '{existing.path}'. "
                            f"Under project_root, paths are normalized to lowercase to prevent case-sensitivity issues."
                        )  # pragma: no cover

            # Check for nested paths with existing projects.
            #
            # Trigger: the new project is store-derived (no import source given).
            # Why: its home is `store/<external_id>/`, inside the tool's own data
            #     directory, and any user project rooted above that directory —
            #     `~`, or a test's tmp home — encloses it by construction. Applying
            #     the rule there refuses *every* store-derived project because of
            #     one over-broad user project, which makes the store design
            #     unreachable rather than protecting anything. Two store-derived
            #     projects are siblings and can never nest, so nothing is lost.
            # Outcome: skip the check for a store-derived path; an import source
            #     still gets it, which is where tree-sharing actually happens.
            #
            # A declared external home is skipped for the same reason, in both
            # directions. Its notes sit in `<some versioned directory>/.bm`, and
            # the directory above them is very often inside a catch-all
            # workspace project (`~`, `~/.claude`) — a skill's `.bm` under one is
            # the arrangement, not a collision. The catch-all carries a
            # `scope: here` marker, so it never claims the subdirectory anyway
            # (GAPS U40). Enforcing nesting here would refuse every skill
            # project because one broad project exists, which is the same
            # unreachable-by-construction failure the store-derived skip avoids.
            skip_nesting = store_external_id is not None or home == PROJECT_HOME_EXTERNAL
            if not skip_nesting:
                for existing in existing_projects:
                    if existing.is_externally_homed:
                        continue
                    if not self._check_nested_paths(resolved_path, existing.path):
                        continue
                    # Determine which path is nested within which for appropriate error message
                    p_new = Path(resolved_path).resolve()
                    p_existing = Path(existing.path).resolve()

                    # Check if new path is nested under existing project
                    if p_new.is_relative_to(p_existing):
                        raise ValueError(
                            f"Cannot create project at '{resolved_path}': "
                            f"path is nested within existing project '{existing.name}' at '{existing.path}'. "
                            f"Projects cannot share directory trees."
                        )
                    else:
                        # Existing project is nested under new path
                        raise ValueError(
                            f"Cannot create project at '{resolved_path}': "
                            f"existing project '{existing.name}' at '{existing.path}' is nested within this path. "
                            f"Projects cannot share directory trees."
                        )

            # Ensure the project directory exists on disk.
            # Trigger: project_root not set means the caller chose the directory itself
            # Why: FileService owns filesystem writes; direct Path.mkdir() bypasses that
            #      abstraction and its error handling
            # Outcome: directory exists before the registry row is written
            if not self.config_manager.config.project_root:
                if self.file_service is None:
                    raise ValueError(
                        "file_service is required for local project directory creation"
                    )
                await self.file_service.ensure_directory(Path(resolved_path))

            project_data = {
                "name": name,
                "path": resolved_path,
                "permalink": sanitized_name,
                "is_active": True,
                # NULL unless declared: that is the value for a store-homed
                # project and for a legacy off-store one alike.
                "home": home,
                # Don't set is_default=False to avoid UNIQUE constraint issues
                # Let it default to NULL, only set to True when explicitly making default
            }
            if store_external_id is not None:
                project_data["external_id"] = store_external_id
            created_project = await self.repository.create(session, project_data)
            new_external_id = created_project.external_id

            # Trigger: the caller asked for this project to be the default, or the
            #      registry has no default at all (this is the first project).
            # Why: every unqualified command resolves through the default, so a
            #      registry with rows but no default flag is unusable — but an
            #      existing default must never be repointed by a plain add.
            # Outcome: the flag moves only when asked for, or when nothing holds it.
            if set_default or await self.repository.get_default_project(session) is None:
                await self.repository.set_as_default(session, created_project.id)
                logger.info(f"Project '{name}' set as default")

        # Creating a project is the deliberate human act a vocabulary needs
        # (GAPS W4), and since GAPS U49 that act is creation itself: a new
        # project is governed unless the caller opted out. `bm new` never writes
        # one — on an ungoverned project it writes the record unchecked and says
        # so.
        #
        # Constraint: this runs *outside* the session scope, because that is
        # where the row is committed. `vocabulary_path` resolves a declared home
        # through `project_registry.lookup_project_home`, which opens its own
        # SQLite connection and so reads committed rows only. Inside the scope
        # the row is merely flushed, the lookup would find nothing, and an
        # external project's vocabulary would land in the store — leaving the
        # home it declared ungoverned. The cost of the move is that a failed
        # vocabulary write leaves the project registered and ungoverned instead
        # of rolling the registration back; that is the right trade, since the
        # file sits beside the notes and is not part of the registry write.
        #
        # The id is never None here — the scope above either bound it or raised
        # — but the check is what makes that provable to a type checker, which
        # cannot know the scope's `__aexit__` does not swallow the exception.
        if governed and new_external_id is not None:
            write_default_vocabulary(new_external_id)

        logger.info(f"Project '{name}' added at {resolved_path}")

    async def adopt_project(self, name: str, path: str) -> ProjectAdoptResponse:
        """Point this machine's registry at notes another VCS already delivered.

        Arrival: yadm has carried a skill's ``.bm/`` to a new machine, records and
        ``vocabulary.yml`` together, and nothing here knows about it. Resolution is
        **by name**, because names are the cross-machine key and ``external_id`` is
        minted per machine (design, "Registry shape").

        Idempotent, and never destructive: adopt writes the registry only. It moves
        no file, so any outcome that would strand notes is refused instead.

        Args:
            name: The project's registered name.
            path: The directory the notes were delivered to, absolute and
                **unresolved** — under yadm's link mode it is a per-machine symlink.

        Returns:
            What happened, as one of four actions.

        Raises:
            ValueError: If the named project is homed in the store, or is a legacy
                off-store project whose directory is not this one.
        """
        # `os.path.abspath` normalizes without following symlinks, which is the
        # same normalization `add_project` applies before it stores a path — so
        # the comparisons below are against the spelling the registry holds.
        notes_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

        existing = await self.get_project(name)

        if existing is None:
            # One code path for "register an external home": the directory goes to
            # `add_project` exactly as `bm project add --home-here` sends it.
            #
            # Ungoverned on purpose. Governance travels with the notes — the
            # delivered `vocabulary.yml` sits beside them — so writing a default
            # one here would invent a governance the human never declared, and
            # dirty a directory another VCS owns. When none arrived, the project
            # is ungoverned and `bm doctor` says so; adopt does not paper over it.
            await self.add_project(name, notes_path, governed=False, home=PROJECT_HOME_EXTERNAL)
            registered = await self.get_project(name)
            if registered is None:  # pragma: no cover
                raise ValueError(f"Project '{name}' was not registered")
            return ProjectAdoptResponse(
                action="registered",
                name=registered.name,
                external_id=registered.external_id,
                path=registered.path,
            )

        if existing.is_externally_homed:
            # Already adopted. The id and the row stay as they are — a second
            # adopt must never mint a second one — and only a moved directory
            # (a skill renamed, a different home on this machine) changes the path.
            if existing.path == notes_path:
                return ProjectAdoptResponse(
                    action="unchanged",
                    name=existing.name,
                    external_id=existing.external_id,
                    path=existing.path,
                )
            async with db.scoped_session(self.session_maker) as session:
                await self.repository.update_fields(session, existing.id, {"path": notes_path})
            logger.info(f"Repointed project '{existing.name}' to {notes_path}")
            return ProjectAdoptResponse(
                action="repointed",
                name=existing.name,
                external_id=existing.external_id,
                path=notes_path,
            )

        # Trigger: the named project's notes are under the store.
        # Why: adopt rewrites the path and moves nothing, so it would leave every
        #     note in `store/<id>/` unreachable — and the store is where the
        #     history that could recover them lives.
        # Outcome: refuse and state the conflict. No verb re-homes those notes.
        if project_store_prefix(existing.path) is not None:
            raise ValueError(
                f"Project '{name}' is homed in the store at '{existing.path}'. Adopting it "
                f"here would point it at '{notes_path}' and leave those notes unreachable."
            )

        # A legacy off-store project: notes outside the store, no declared home.
        # Adopt is its retrofit path (design, stage 5) — but only where its notes
        # already are, because the alternative is the same silent orphaning.
        if existing.path != notes_path:
            raise ValueError(
                f"Project '{name}' keeps its notes at '{existing.path}', not at "
                f"'{notes_path}'. Adopt records where the notes already are; it moves none."
            )
        async with db.scoped_session(self.session_maker) as session:
            await self.repository.update_fields(
                session, existing.id, {"home": PROJECT_HOME_EXTERNAL}
            )
        logger.info(f"Project '{existing.name}' now declares home '{PROJECT_HOME_EXTERNAL}'")
        return ProjectAdoptResponse(
            action="adopted",
            name=existing.name,
            external_id=existing.external_id,
            path=existing.path,
        )

    async def remove_project(self, name: str, delete_notes: bool = False) -> None:
        """Remove a project from the registry.

        Args:
            name: The name of the project to remove
            delete_notes: If True, delete the project directory from filesystem

        Raises:
            ValueError: If the project doesn't exist or is the default project
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for remove_project")

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database first
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                raise ValueError(f"Project '{name}' not found")  # pragma: no cover

            project_path = project.path

            if project.is_default:
                raise ValueError(f"Cannot remove the default project '{name}'")  # pragma: no cover

            await self.repository.delete(session, project.id)

        logger.info(f"Project '{name}' removed from the registry")

        # Optionally delete the project directory
        if delete_notes and project_path:
            try:
                path_obj = Path(project_path)
                if path_obj.exists() and path_obj.is_dir():
                    await asyncio.to_thread(shutil.rmtree, project_path)
                    logger.info(f"Deleted project directory: {project_path}")
                else:
                    logger.warning(  # pragma: no cover
                        f"Project directory not found or not a directory: {project_path}"
                    )  # pragma: no cover
            except Exception as e:  # pragma: no cover
                logger.warning(  # pragma: no cover
                    f"Failed to delete project directory {project_path}: {e}"
                )

    async def set_default_project(self, name: str) -> None:
        """Set the default project in the registry.

        Args:
            name: The name of the project to set as default

        Raises:
            ValueError: If the project doesn't exist
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for set_default_project")

        async with db.scoped_session(self.session_maker) as session:
            # Look up project in database first to validate it exists
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                raise ValueError(f"Project '{name}' not found")

            await self.repository.set_as_default(session, project.id)

        logger.info(f"Project '{name}' set as default")

    async def move_project(self, name: str, new_path: str) -> None:
        """Move a project to a new location.

        Args:
            name: The name of the project to move
            new_path: The new absolute path for the project

        Raises:
            ValueError: If the project doesn't exist, declares an external home,
                or the repository isn't initialized
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for move_project")  # pragma: no cover

        # Resolve to absolute path
        resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

        async with db.scoped_session(self.session_maker) as session:
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if project is None:
                raise ValueError(f"Project '{name}' not found")

            # Trigger: the project declares its notes live in a directory
            #     something else versions.
            # Why: move only rewrites the registry path — it never moves a file —
            #     so it would leave the notes behind and point the project at an
            #     empty directory. For a store-homed project that is harmless
            #     (the store follows the id); for this one it is data loss.
            # Outcome: refuse, and name the verb that re-homes a project properly.
            if project.is_externally_homed:
                raise ValueError(
                    f"Project '{name}' is homed at '{project.path}', which something else "
                    "versions. Moving it would repoint the registry and leave the notes "
                    "behind. Run `bm project adopt` from the new directory instead."
                )

            # Create the new directory if it doesn't exist.
            # Trigger: project_root not set means the caller chose the directory itself
            # Why: FileService owns filesystem writes; direct Path.mkdir() bypasses it
            # Outcome: destination directory exists before the registry is updated —
            #     and, since the refusal above runs first, a refused move creates
            #     nothing.
            if not self.config_manager.config.project_root:
                if self.file_service is None:
                    raise ValueError(
                        "file_service is required for local project directory creation"
                    )
                await self.file_service.ensure_directory(Path(resolved_path))

            old_path = project.path
            await self.repository.update_path(session, project.id, resolved_path)
            logger.info(f"Moved project '{name}' from {old_path} to {resolved_path}")

    async def update_project(  # pragma: no cover
        self, name: str, updated_path: Optional[str] = None, is_active: Optional[bool] = None
    ) -> None:
        """Update project information in the registry.

        Args:
            name: The name of the project to update
            updated_path: Optional new path for the project
            is_active: Optional flag to set project active status

        Raises:
            ValueError: If project doesn't exist or repository isn't initialized
        """
        if not self.repository:
            raise ValueError("Repository is required for update_project")

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database using robust lookup
            project = await self.repository.get_by_name(
                session, name
            ) or await self.repository.get_by_permalink(session, name)
            if not project:
                raise ValueError(f"Project '{name}' not found")

            # Update path if provided
            if updated_path:
                resolved_path = Path(os.path.abspath(os.path.expanduser(updated_path))).as_posix()
                project.path = resolved_path
                await self.repository.update(session, project.id, project)

                logger.info(f"Updated path for project '{name}' to {resolved_path}")

            # Update active status if provided
            if is_active is not None:
                project.is_active = is_active
                await self.repository.update(session, project.id, project)
                logger.info(f"Set active status for project '{name}' to {is_active}")

            # If project was made inactive and it was the default, we need to pick a new default
            if is_active is False and project.is_default:
                # Find another active project
                active_projects = await self.repository.get_active_projects(session)
                if active_projects:
                    new_default = active_projects[0]
                    await self.repository.set_as_default(session, new_default.id)
                    logger.info(
                        f"Changed default project to '{new_default.name}' as '{name}' was deactivated"
                    )

    async def get_project_info(self, project_name: Optional[str] = None) -> ProjectInfoResponse:
        """Get comprehensive information about the specified Basic Memory project.

        Args:
            project_name: Name of the project to get info for. If None, uses the current config project.

        Returns:
            Comprehensive project information and statistics
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_project_info")

        # Use specified project or fall back to config project
        requested_project_name = project_name or self.config.project
        project_permalink = generate_permalink(requested_project_name)

        async with db.scoped_session(self.session_maker) as session:
            # Get project from database to get project_id
            db_project = await self.repository.get_by_permalink(session, project_permalink)
            if not db_project:  # pragma: no cover
                raise ValueError(f"Project '{requested_project_name}' not found in database")
            db_projects = await self.repository.get_active_projects(session)

        resolved_project_name = db_project.name
        resolved_project_path = db_project.path
        resolved_project_repo = db_project.repo
        resolved_project_home = db_project.home

        # Get statistics for the specified project
        statistics = await self.get_statistics(db_project.id)

        # Get activity metrics for the specified project
        activity = await self.get_activity_metrics(db_project.id)

        # Get embedding status for the specified project
        embedding_status = await self.get_embedding_status(db_project.id)

        # Get system status
        system = self.get_system_status()

        default_project = next(
            (project.name for project in db_projects if project.is_default), None
        )

        enhanced_projects = {
            project.name: {
                "path": project.path,
                "active": project.is_active,
                "id": project.id,
                "is_default": bool(project.is_default),
                "permalink": project.permalink,
                # None for every project that declared nothing, which is all of
                # them until one is created with a declared home.
                "home": project.home,
            }
            for project in db_projects
        }

        # Construct the response
        return ProjectInfoResponse(
            project_name=resolved_project_name,
            project_path=resolved_project_path,
            project_repo=resolved_project_repo,
            project_home=resolved_project_home,
            available_projects=enhanced_projects,
            default_project=default_project,
            statistics=statistics,
            activity=activity,
            system=system,
            embedding_status=embedding_status,
        )

    async def get_statistics(self, project_id: int) -> ProjectStatistics:
        """Get statistics about the specified project.

        Args:
            project_id: ID of the project to get statistics for (required).
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_statistics")

        async with db.scoped_session(self.session_maker) as session:
            # Get basic counts
            entity_count_result = await self.repository.execute_query(
                session,
                text("SELECT COUNT(*) FROM entity WHERE project_id = :project_id"),
                {"project_id": project_id},
            )
            total_entities = entity_count_result.scalar() or 0

            observation_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_observations = observation_count_result.scalar() or 0

            relation_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_relations = relation_count_result.scalar() or 0

            unresolved_count_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE r.to_id IS NULL AND e.project_id = :project_id"
                ),
                {"project_id": project_id},
            )
            total_unresolved = unresolved_count_result.scalar() or 0

            # Get entity counts by note type
            note_types_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT note_type, COUNT(*) FROM entity WHERE project_id = :project_id GROUP BY note_type"
                ),
                {"project_id": project_id},
            )
            note_types = {row[0]: row[1] for row in note_types_result.fetchall()}

            # Get observation counts by category
            category_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT o.category, COUNT(*) FROM observation o JOIN entity e ON o.entity_id = e.id WHERE e.project_id = :project_id GROUP BY o.category"
                ),
                {"project_id": project_id},
            )
            observation_categories = {row[0]: row[1] for row in category_result.fetchall()}

            # Get relation counts by type
            relation_types_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT r.relation_type, COUNT(*) FROM relation r JOIN entity e ON r.from_id = e.id WHERE e.project_id = :project_id GROUP BY r.relation_type"
                ),
                {"project_id": project_id},
            )
            relation_types = {row[0]: row[1] for row in relation_types_result.fetchall()}

            # Find most connected entities (most outgoing relations) - project filtered
            connected_result = await self.repository.execute_query(
                session,
                text("""
                SELECT e.id, e.title, e.permalink, COUNT(r.id) AS relation_count, e.file_path
                FROM entity e
                JOIN relation r ON e.id = r.from_id
                WHERE e.project_id = :project_id
                GROUP BY e.id
                ORDER BY relation_count DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            most_connected = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "relation_count": row[3],
                    "file_path": row[4],
                }
                for row in connected_result.fetchall()
            ]

            # Count isolated entities (no relations) - project filtered
            isolated_result = await self.repository.execute_query(
                session,
                text("""
                SELECT COUNT(e.id)
                FROM entity e
                LEFT JOIN relation r1 ON e.id = r1.from_id
                LEFT JOIN relation r2 ON e.id = r2.to_id
                WHERE e.project_id = :project_id AND r1.id IS NULL AND r2.id IS NULL
            """),
                {"project_id": project_id},
            )
            isolated_count = isolated_result.scalar() or 0

            return ProjectStatistics(
                total_entities=total_entities,
                total_observations=total_observations,
                total_relations=total_relations,
                total_unresolved_relations=total_unresolved,
                note_types=note_types,
                observation_categories=observation_categories,
                relation_types=relation_types,
                most_connected_entities=most_connected,
                isolated_entities=isolated_count,
            )

    async def get_activity_metrics(self, project_id: int) -> ActivityMetrics:
        """Get activity metrics for the specified project.

        Args:
            project_id: ID of the project to get activity metrics for (required).
        """
        if not self.repository:  # pragma: no cover
            raise ValueError("Repository is required for get_activity_metrics")

        async with db.scoped_session(self.session_maker) as session:
            # Get recently created entities (project filtered)
            created_result = await self.repository.execute_query(
                session,
                text("""
                SELECT id, title, permalink, note_type, created_at, file_path
                FROM entity
                WHERE project_id = :project_id
                ORDER BY created_at DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            recently_created = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "note_type": row[3],
                    "created_at": row[4],
                    "file_path": row[5],
                }
                for row in created_result.fetchall()
            ]

            # Get recently updated entities (project filtered)
            updated_result = await self.repository.execute_query(
                session,
                text("""
                SELECT id, title, permalink, note_type, updated_at, file_path
                FROM entity
                WHERE project_id = :project_id
                ORDER BY updated_at DESC
                LIMIT 10
            """),
                {"project_id": project_id},
            )
            recently_updated = [
                {
                    "id": row[0],
                    "title": row[1],
                    "permalink": row[2],
                    "note_type": row[3],
                    "updated_at": row[4],
                    "file_path": row[5],
                }
                for row in updated_result.fetchall()
            ]

            # Get monthly growth over the last 6 months
            # Calculate the start of 6 months ago
            now = datetime.now()
            six_months_ago = datetime(
                now.year - (1 if now.month <= 6 else 0), ((now.month - 6) % 12) or 12, 1
            )

            # Query for monthly entity creation (project filtered)
            date_format = "strftime('%Y-%m', created_at)"

            # SQLite compares datetimes as ISO-8601 text.
            six_months_param = six_months_ago.isoformat()

            entity_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format} AS month,
                    COUNT(*) AS count
                FROM entity
                WHERE created_at >= :six_months_ago AND project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            entity_growth = {row[0]: row[1] for row in entity_growth_result.fetchall()}

            # Query for monthly observation creation (project filtered)
            date_format_entity = "strftime('%Y-%m', entity.created_at)"

            observation_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format_entity} AS month,
                    COUNT(*) AS count
                FROM observation
                INNER JOIN entity ON observation.entity_id = entity.id
                WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            observation_growth = {row[0]: row[1] for row in observation_growth_result.fetchall()}

            # Query for monthly relation creation (project filtered)
            relation_growth_result = await self.repository.execute_query(
                session,
                text(f"""
                SELECT
                    {date_format_entity} AS month,
                    COUNT(*) AS count
                FROM relation
                INNER JOIN entity ON relation.from_id = entity.id
                WHERE entity.created_at >= :six_months_ago AND entity.project_id = :project_id
                GROUP BY month
                ORDER BY month
            """),
                {"six_months_ago": six_months_param, "project_id": project_id},
            )
            relation_growth = {row[0]: row[1] for row in relation_growth_result.fetchall()}

            # Combine all monthly growth data
            monthly_growth = {}
            for month in set(
                list(entity_growth.keys())
                + list(observation_growth.keys())
                + list(relation_growth.keys())
            ):
                monthly_growth[month] = {
                    "entities": entity_growth.get(month, 0),
                    "observations": observation_growth.get(month, 0),
                    "relations": relation_growth.get(month, 0),
                    "total": (
                        entity_growth.get(month, 0)
                        + observation_growth.get(month, 0)
                        + relation_growth.get(month, 0)
                    ),
                }

            return ActivityMetrics(
                recently_created=recently_created,
                recently_updated=recently_updated,
                monthly_growth=monthly_growth,
            )

    async def get_embedding_status(self, project_id: int) -> EmbeddingStatus:
        """Get embedding/vector index status for the specified project.

        Reports config, counts, and whether a reindex is recommended.
        """
        config = self.config_manager.config
        semantic_enabled = config.semantic_search_enabled

        # When semantic search is disabled, return minimal status
        if not semantic_enabled:
            return EmbeddingStatus(semantic_search_enabled=False)

        provider = config.semantic_embedding_provider
        model = config.semantic_embedding_model
        dimensions = config.semantic_embedding_dimensions
        document_prefix_set = bool(config.semantic_embedding_document_prefix)
        query_prefix_set = bool(config.semantic_embedding_query_prefix)

        # --- Check vector table existence ---
        # Both search_vector_chunks and search_vector_embeddings must exist
        # for the detailed stats queries (JOINs between them) to work.
        table_check_sql = text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name IN ('search_vector_chunks', 'search_vector_embeddings')"
        )

        async with db.scoped_session(self.session_maker) as session:
            table_result = await self.repository.execute_query(session, table_check_sql, {})
            vector_tables_exist = (table_result.scalar() or 0) == 2

            if not vector_tables_exist:
                # Count distinct entities in search index for the recommendation message
                si_result = await self.repository.execute_query(
                    session,
                    text(
                        "SELECT COUNT(DISTINCT entity_id) FROM search_index "
                        "WHERE project_id = :project_id"
                    ),
                    {"project_id": project_id},
                )
                total_indexed_entities = si_result.scalar() or 0

                return EmbeddingStatus(
                    semantic_search_enabled=True,
                    embedding_provider=provider,
                    embedding_model=model,
                    embedding_dimensions=dimensions,
                    embedding_document_prefix_set=document_prefix_set,
                    embedding_query_prefix_set=query_prefix_set,
                    total_indexed_entities=total_indexed_entities,
                    vector_tables_exist=False,
                    reindex_recommended=True,
                    reindex_reason=("Vector tables not initialized — run: bm reindex --embeddings"),
                )

            # --- Count queries (tables exist) ---
            # Filter by entity existence to exclude stale rows from deleted entities
            # that remain in derived search tables (search_index, search_vector_chunks)
            entity_exists = (
                "AND entity_id IN (SELECT id FROM entity WHERE project_id = :project_id)"
            )
            # Same filter for aliased chunks table (used in JOIN queries below)
            chunk_entity_exists = (
                "AND c.entity_id IN (SELECT id FROM entity WHERE project_id = :project_id)"
            )

            si_result = await self.repository.execute_query(
                session,
                text(
                    "SELECT COUNT(DISTINCT entity_id) FROM search_index "
                    f"WHERE project_id = :project_id {entity_exists}"
                ),
                {"project_id": project_id},
            )
            total_indexed_entities = si_result.scalar() or 0

            try:
                chunks_result = await self.repository.execute_query(
                    session,
                    text(
                        "SELECT COUNT(*) FROM search_vector_chunks "
                        f"WHERE project_id = :project_id {entity_exists}"
                    ),
                    {"project_id": project_id},
                )
                total_chunks = chunks_result.scalar() or 0

                entities_with_chunks_result = await self.repository.execute_query(
                    session,
                    text(
                        "SELECT COUNT(DISTINCT entity_id) FROM search_vector_chunks "
                        f"WHERE project_id = :project_id {entity_exists}"
                    ),
                    {"project_id": project_id},
                )
                total_entities_with_chunks = entities_with_chunks_result.scalar() or 0

                embeddings_sql = text(
                    "SELECT COUNT(*) FROM search_vector_chunks c "
                    "JOIN search_vector_embeddings e ON e.rowid = c.id "
                    f"WHERE c.project_id = :project_id {chunk_entity_exists}"
                )

                # The embeddings/orphan JOINs read search_vector_embeddings, a vec0
                # virtual table. That table is only visible on a connection that loaded
                # sqlite-vec, so route these through scalar_vec_query which loads the
                # extension first.
                async def _vec_scalar(vec_sql) -> int:
                    count = await self.repository.scalar_vec_query(
                        session, vec_sql, {"project_id": project_id}
                    )
                    # Trigger: sqlite-vec genuinely can't load on this Python build.
                    # Why: without the extension the vec0 JOIN can't run at all.
                    # Outcome: raise the canonical error so the except block emits the
                    # true "sqlite-vec unavailable" fallback instead of reporting 0.
                    if count is None:
                        raise SAOperationalError(
                            str(vec_sql), {}, Exception("no such module: vec0")
                        )
                    return count

                total_embeddings = await _vec_scalar(embeddings_sql)

                # Orphaned chunks (chunks without embeddings — indicates interrupted indexing)
                orphan_sql = text(
                    "SELECT COUNT(*) FROM search_vector_chunks c "
                    "LEFT JOIN search_vector_embeddings e ON e.rowid = c.id "
                    f"WHERE c.project_id = :project_id AND e.rowid IS NULL {chunk_entity_exists}"
                )
                orphaned_chunks = await _vec_scalar(orphan_sql)
            except SAOperationalError as exc:
                # Trigger: sqlite_master can list vec0 virtual tables even when sqlite-vec
                # is not loaded in the current Python runtime.
                # Why: project info should degrade gracefully instead of crashing on stats queries.
                # Outcome: report vector tables as unavailable and point the user to install the
                # missing dependency before rebuilding embeddings.
                if "no such module: vec0" not in str(exc).lower():
                    raise

                return EmbeddingStatus(
                    semantic_search_enabled=True,
                    embedding_provider=provider,
                    embedding_model=model,
                    embedding_dimensions=dimensions,
                    embedding_document_prefix_set=document_prefix_set,
                    embedding_query_prefix_set=query_prefix_set,
                    total_indexed_entities=total_indexed_entities,
                    vector_tables_exist=False,
                    reindex_recommended=True,
                    reindex_reason=(
                        "SQLite vector tables exist but sqlite-vec is unavailable in this Python "
                        "environment — install/update basic-memory, then run: bm reindex --embeddings"
                    ),
                )

            # --- Reindex recommendation logic (priority order) ---
            reindex_recommended = False
            reindex_reason = None

            if total_indexed_entities > 0 and total_chunks == 0:
                reindex_recommended = True
                reindex_reason = "Embeddings have never been built — run: bm reindex --embeddings"
            elif orphaned_chunks > 0:
                reindex_recommended = True
                reindex_reason = (
                    f"{orphaned_chunks} orphaned chunks found (interrupted indexing) "
                    "— run: bm reindex --embeddings"
                )
            elif total_indexed_entities > total_entities_with_chunks:
                missing = total_indexed_entities - total_entities_with_chunks
                reindex_recommended = True
                reindex_reason = (
                    f"{missing} entities missing embeddings — run: bm reindex --embeddings"
                )

            return EmbeddingStatus(
                semantic_search_enabled=True,
                embedding_provider=provider,
                embedding_model=model,
                embedding_dimensions=dimensions,
                embedding_document_prefix_set=document_prefix_set,
                embedding_query_prefix_set=query_prefix_set,
                total_indexed_entities=total_indexed_entities,
                total_entities_with_chunks=total_entities_with_chunks,
                total_chunks=total_chunks,
                total_embeddings=total_embeddings,
                orphaned_chunks=orphaned_chunks,
                vector_tables_exist=True,
                reindex_recommended=reindex_recommended,
                reindex_reason=reindex_reason,
            )

    def get_system_status(self) -> SystemStatus:
        """Get system status information."""
        import basic_memory

        # Get database information
        db_path = self.config_manager.config.database_path
        db_size = db_path.stat().st_size if db_path.exists() else 0
        db_size_readable = f"{db_size / (1024 * 1024):.2f} MB"

        # Get watch service status if available
        watch_status = None
        watch_status_path = self.config_manager.config.data_dir_path / WATCH_STATUS_JSON
        if watch_status_path.exists():
            try:
                watch_status = json.loads(watch_status_path.read_text(encoding="utf-8"))
            except Exception:  # pragma: no cover
                pass

        return SystemStatus(
            version=basic_memory.__version__,
            database_path=str(db_path),
            database_size=db_size_readable,
            watch_status=watch_status,
            timestamp=datetime.now(),
        )
