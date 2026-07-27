"""Runtime-neutral route contracts for project indexing."""

from dataclasses import dataclass
from typing import Protocol

from basic_memory.indexing.project_index_coordinator import ProjectIndexCoordinatorResult
from basic_memory.runtime.jobs import RuntimeObservedIndexFile
from basic_memory.schemas.v2.project_index import ProjectIndexResponse


@dataclass(frozen=True, slots=True)
class ProjectIndexObservation:
    """Files visible to the active project-index runtime.

    Observation is a filesystem scan; indexing is what makes a file reachable by search and
    read. ``indexed_paths`` carries the second fact alongside the first so callers can tell
    "on disk" from "queryable" instead of reporting a scanned file as if it were both.
    """

    observed_files: tuple[RuntimeObservedIndexFile, ...]
    indexed_paths: frozenset[str]

    @property
    def total_files(self) -> int:
        return len(self.observed_files)

    @property
    def unindexed_files(self) -> tuple[RuntimeObservedIndexFile, ...]:
        """Observed files with no index row: present on disk, absent from every read path."""
        return tuple(
            observed_file
            for observed_file in self.observed_files
            if observed_file.path not in self.indexed_paths
        )


class ProjectIndexRunner(Protocol):
    async def index_project(
        self,
        project_id: int,
        *,
        force_full: bool = False,
    ) -> ProjectIndexCoordinatorResult: ...


class ProjectIndexObserver(Protocol):
    async def observe_project(self, project_id: int) -> ProjectIndexObservation: ...


class ProjectIndexScheduler(Protocol):
    def schedule_project_index(self, *, project_id: int, force_full: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class ProjectIndexRouteRequest:
    project_id: int
    project_name: str
    force_full: bool
    run_in_background: bool


class ProjectIndexCommand(Protocol):
    async def index_project(self, request: ProjectIndexRouteRequest) -> ProjectIndexResponse: ...
