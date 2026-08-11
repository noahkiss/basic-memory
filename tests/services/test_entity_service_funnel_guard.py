"""Structural guard for the vocabulary funnel (GAPS W4).

The W4 decision is that *every* entity mutator passes through one checker call,
and that a new mutator which skips it is a bug rather than a policy choice.
Hooking only the write paths someone remembered is how the predecessor tool
ended up rejecting a type in its CLI while its API wrote the same type to disk
(``.forked/decisions.md`` R5), so the rule needs a check that survives the next
person adding a method.

This walks ``entity_service.py`` as an AST rather than importing it: the
question is about the source's shape, and an import would only tell us the
methods exist.
"""

import ast
import inspect
import textwrap

from basic_memory.services import entity_service

CLASS_NAME = "EntityService"
FUNNEL = "_enforce_vocabulary"

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

EXEMPT = READ_ONLY | NO_FRONTMATTER_WRITE

FAILURE_HINT = (
    "Every public async method on EntityService must reach {funnel}, directly or "
    "through another method that does. These do not: {names}. Either call the "
    "funnel with the mode this write path declares (reject for verbs/MCP/API, "
    "record for the sync path), or add the method to READ_ONLY / "
    "NO_FRONTMATTER_WRITE in this file with a reason. Adding it to neither is "
    "how a write path silently stops being validated (GAPS W4)."
)


def self_call_graph(source: str, class_name: str) -> dict[str, set[str]]:
    """Map each ``async def`` on ``class_name`` to the ``self.x()`` names it calls."""
    tree = ast.parse(source)
    class_def = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )

    graph: dict[str, set[str]] = {}
    for node in class_def.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        graph[node.name] = {
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
        }
    return graph


def methods_reaching(graph: dict[str, set[str]], target: str) -> set[str]:
    """Return every method that reaches ``target``, at any call depth.

    Transitive rather than one hop: ``create_or_update_entity`` reaches the
    funnel only through ``create_entity`` → ``create_entity_with_content``, and a
    one-hop rule would report that legitimate chain as a hole.
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
    reaching = methods_reaching(graph, FUNNEL)
    return sorted(
        name
        for name in graph
        if not name.startswith("_") and name not in reaching and name not in exempt
    )


def test_every_public_mutator_reaches_the_funnel() -> None:
    """No public async mutator may write frontmatter without being checked."""
    source = inspect.getsource(entity_service)
    unfunnelled = unfunnelled_methods(source, CLASS_NAME, EXEMPT)

    assert not unfunnelled, FAILURE_HINT.format(funnel=FUNNEL, names=", ".join(unfunnelled))


def test_allowlists_name_only_methods_that_exist() -> None:
    """An allowlist entry for a deleted method silently exempts nothing.

    Worse, it hides the next method that takes the same name. Fail on the stale
    entry instead.
    """
    source = inspect.getsource(entity_service)
    methods = set(self_call_graph(source, CLASS_NAME))

    stale = sorted(EXEMPT - methods)
    assert not stale, f"allowlisted names are not async methods on {CLASS_NAME}: {stale}"


def test_walk_detects_a_method_that_misses_the_funnel() -> None:
    """Positive control: the analysis must report a genuinely unfunnelled mutator.

    Without this, an empty result from the test above could mean "the AST walk is
    broken" rather than "the funnel holds" — the false negative the house
    evidence rules exist to prevent.
    """
    source = textwrap.dedent(
        """
        class EntityService:
            async def _enforce_vocabulary(self, metadata, *, mode, file_path):
                return []

            async def guarded_write(self):
                await self._enforce_vocabulary({}, mode="reject", file_path="a.md")

            async def delegating_write(self):
                await self.guarded_write()

            async def unguarded_write(self):
                await self.repository.update()
        """
    )

    assert unfunnelled_methods(source, CLASS_NAME, frozenset()) == ["unguarded_write"]
