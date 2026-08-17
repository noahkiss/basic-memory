"""Structural guard for the vocabulary funnel (GAPS W4, retargeted by GAPS T22).

The W4 decision is that *every* note write passes through one checker call, and
that a new write path which skips it is a bug rather than a policy choice.
Hooking only the paths someone remembered is how the predecessor tool ended up
rejecting a type in its CLI while its API wrote the same type to disk
(``.forked/decisions.md`` R5).

**Why this file moved off ``EntityService``.** The W4 guard proved every mutator
on that class reached the funnel, and every one of them did — but no caller
reached *them*. Reject mode was unreachable for the whole of W4's life (GAPS
T22). **A guard over a layer proves nothing about whether callers use that
layer**, so the guard now sits on the layer callers actually use, and
``tests/mcp/test_tool_vocabulary_enforcement.py`` drives the real MCP path to prove they do.
The two together are the check; neither alone is.

**What this file does not cover.** A third caller records: the move planner in
``index/local_moves.py`` (GAPS T23). It is guarded behaviourally instead — the
governed-move tests in ``tests/index/`` assert the violation is logged and the
rewrite is skipped — because it is one call site on one method, not a class of
write paths a new method could quietly join.

These walk the source as an AST rather than importing it: the question is about
the source's shape, and an import would only tell us the functions exist.
"""

import ast
import inspect
import textwrap

from basic_memory.indexing import accepted_note_mutation_runner
from basic_memory.services import entity_service

# --- The accepted-state write path: reject mode ---

RUNNER_FUNNEL = "enforce_accepted_note_vocabulary"

# Every note write an agent can reach lands in one of these.
RUNNER_WRITES = frozenset(
    {
        "_run_accepted_note_create",
        "_run_accepted_note_update",
        "_run_accepted_note_edit",
        "_run_accepted_note_move",
    }
)

RUNNER_HINT = (
    "Every accepted-note write must reach {funnel} before it persists anything. "
    "These do not: {names}. A write path that skips the funnel is how reject mode "
    "silently stops applying (GAPS W4, GAPS T22)."
)

# --- EntityService: record mode, the sync path ---

SERVICE_CLASS = "EntityService"
SERVICE_FUNNEL = "_record_vocabulary_violations"

# Methods that read, resolve, or derive, and persist nothing. The prepare_*
# family is the large group: each one returns accepted content for a caller to
# write, and the caller is what the funnel guards.
READ_ONLY = frozenset(
    {
        "detect_file_path_conflicts",
        "resolve_permalink",
        "resolve_deferred_self_relation",
        "verify_move_destination_absent",
        "get_by_permalink",
        "get_entities_by_id",
        "get_entities_by_permalinks",
        "prepare_create_entity_content",
        "prepare_update_entity_content",
        "prepare_edit_entity_content",
        "prepare_move_entity_content",
    }
)

# Mutators that remove records. They write no frontmatter, so there is nothing
# for the checker to judge, and no vocabulary rule could make a delete illegal:
# refusing to delete an off-vocabulary note would strand exactly the records
# `bm doctor` is meant to help a human clear.
NO_FRONTMATTER_WRITE = frozenset(
    {
        "delete_entity",
        "delete_entity_by_file_path",
        "delete_directory",
    }
)

# Renames a file and, at most, its permalink. It is not on any agent path — the
# API's move endpoints go through the accepted-note runner, which is guarded
# above — and it holds no parsed markdown to hand the checker.
PATH_ONLY_WRITE = frozenset({"move_entity"})

EXEMPT = READ_ONLY | NO_FRONTMATTER_WRITE | PATH_ONLY_WRITE

SERVICE_HINT = (
    "Every public async method on EntityService must reach {funnel}, directly or "
    "through another method that does. These do not: {names}. Either call the "
    "funnel, or add the method to READ_ONLY / NO_FRONTMATTER_WRITE / "
    "PATH_ONLY_WRITE in this file with a reason. Adding it to neither is how a "
    "write path silently stops being validated (GAPS W4)."
)


# --- AST walks ---


def _called_names(node: ast.AST, *, receiver: str | None) -> set[str]:
    """Return the call targets inside ``node``.

    ``receiver`` selects ``self.x()`` style calls on a class; ``None`` selects
    bare ``x()`` calls, which is the shape module-level functions use.
    """
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        if receiver is None and isinstance(call.func, ast.Name):
            names.add(call.func.id)
        elif (
            receiver is not None
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == receiver
        ):
            names.add(call.func.attr)
    return names


def module_call_graph(source: str) -> dict[str, set[str]]:
    """Map each module-level function to the bare function names it calls."""
    tree = ast.parse(source)
    return {
        node.name: _called_names(node, receiver=None)
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
    }


def self_call_graph(source: str, class_name: str) -> dict[str, set[str]]:
    """Map each ``async def`` on ``class_name`` to the ``self.x()`` names it calls."""
    tree = ast.parse(source)
    class_def = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name: _called_names(node, receiver="self")
        for node in class_def.body
        if isinstance(node, ast.AsyncFunctionDef)
    }


def methods_reaching(graph: dict[str, set[str]], target: str) -> set[str]:
    """Return every function that reaches ``target``, at any call depth.

    Transitive rather than one hop: ``run_accepted_note_create`` reaches the
    funnel only through ``_run_accepted_note_create``, and a one-hop rule would
    report that legitimate chain as a hole.
    """
    reaching = {name for name, calls in graph.items() if target in calls}
    while True:
        grown = reaching | {
            name for name, calls in graph.items() if calls & reaching and name not in reaching
        }
        if grown == reaching:
            return reaching
        reaching = grown


def unfunnelled_methods(source: str, class_name: str, exempt: frozenset[str]) -> list[str]:
    """Return the public async methods that neither reach the funnel nor are exempt."""
    graph = self_call_graph(source, class_name)
    reaching = methods_reaching(graph, SERVICE_FUNNEL)
    return sorted(
        name
        for name in graph
        if not name.startswith("_") and name not in reaching and name not in exempt
    )


# --- The guards ---


def test_every_accepted_note_write_reaches_the_funnel() -> None:
    """No accepted-state write may persist frontmatter without being checked."""
    graph = module_call_graph(inspect.getsource(accepted_note_mutation_runner))
    reaching = methods_reaching(graph, RUNNER_FUNNEL)

    unfunnelled = sorted(RUNNER_WRITES - reaching)
    assert not unfunnelled, RUNNER_HINT.format(funnel=RUNNER_FUNNEL, names=", ".join(unfunnelled))


def test_runner_write_list_names_only_functions_that_exist() -> None:
    """A stale name in RUNNER_WRITES guards nothing and hides its replacement."""
    graph = module_call_graph(inspect.getsource(accepted_note_mutation_runner))

    stale = sorted(RUNNER_WRITES - set(graph))
    assert not stale, f"named write paths are not module functions in the runner: {stale}"


def test_every_public_entity_service_mutator_reaches_the_record_funnel() -> None:
    """The sync path records every violation it indexes past."""
    source = inspect.getsource(entity_service)
    unfunnelled = unfunnelled_methods(source, SERVICE_CLASS, EXEMPT)

    assert not unfunnelled, SERVICE_HINT.format(funnel=SERVICE_FUNNEL, names=", ".join(unfunnelled))


def test_allowlists_name_only_methods_that_exist() -> None:
    """An allowlist entry for a deleted method silently exempts nothing.

    Worse, it hides the next method that takes the same name. Fail on the stale
    entry instead.
    """
    source = inspect.getsource(entity_service)
    methods = set(self_call_graph(source, SERVICE_CLASS))

    stale = sorted(EXEMPT - methods)
    assert not stale, f"allowlisted names are not async methods on {SERVICE_CLASS}: {stale}"


def test_walk_detects_a_method_that_misses_the_funnel() -> None:
    """Positive control: the analysis must report a genuinely unfunnelled mutator.

    Without this, an empty result from the tests above could mean "the AST walk is
    broken" rather than "the funnel holds" — the false negative the house
    evidence rules exist to prevent.
    """
    source = textwrap.dedent(
        """
        class EntityService:
            async def _record_vocabulary_violations(self, metadata, *, file_path):
                return []

            async def guarded_write(self):
                await self._record_vocabulary_violations({}, file_path="a.md")

            async def delegating_write(self):
                await self.guarded_write()

            async def unguarded_write(self):
                await self.repository.update()
        """
    )

    assert unfunnelled_methods(source, SERVICE_CLASS, frozenset()) == ["unguarded_write"]


def test_module_walk_detects_a_write_that_misses_the_funnel() -> None:
    """Positive control for the module-level walk, which is a different shape."""
    source = textwrap.dedent(
        """
        def enforce_accepted_note_vocabulary(**kwargs):
            return None

        async def _run_guarded(session):
            enforce_accepted_note_vocabulary(project=None)

        async def _run_delegating(session):
            await _run_guarded(session)

        async def _run_unguarded(session):
            await persist(session)
        """
    )

    reaching = methods_reaching(module_call_graph(source), RUNNER_FUNNEL)
    assert reaching == {"_run_guarded", "_run_delegating"}
