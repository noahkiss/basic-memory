"""Structural guard for the affordance blocks (GAPS W19 item 5, VERBS_PLAN §5 J).

Every verb closes with a fixed list of next steps. The list is static — no
conditions, no memory of what was printed before — so the only way it can be
wrong is by naming a command that does not exist, and that failure teaches the
surface backwards: an agent that runs the hint gets "no such command" and learns
the tool is unreliable rather than that the hint was.

Two things are asserted here, and the second is what makes the first hold:

1. Every ``bm <verb>`` an affordance names is a command the shipped CLI answers,
   sub-commands included.
2. The set of affordance blocks this file checks is the set the tree defines.
   Without it, a new verb ships an unchecked block and the guard stays green.

The per-command notice lines are checked with them. A notice names a command for
the same reason an affordance does, and the two are written in the same place.
"""

import ast
import re
from collections.abc import Sequence
from pathlib import Path

import typer.main
from typer.core import TyperGroup

import basic_memory.cli.commands as commands_package
from basic_memory.cli.commands import doctor, history, mine, new, record_write, records, rm
from basic_memory.cli.main import app as registered
from basic_memory.cli.notices import NoticeCounts, notice_lines

COMMANDS_DIR = Path(commands_package.__file__).parent

# `bm <verb>`, plus whatever word follows it. The second word matters only when
# the first is a command group: `bm history dirty` has to resolve both halves,
# while `bm show <id> read it back` is one command and then prose.
INVOCATION = re.compile(r"\bbm ([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?")

# Every static affordance block in the tree, by the name the module gives it.
# Compared against a source scan below, so this list cannot fall behind.
AFFORDANCE_CONSTANTS = {
    ("history.py", "UNDO_AFFORDANCE"),
    ("new.py", "NEW_AFFORDANCE"),
    ("record_write.py", "EDIT_AFFORDANCE"),
    ("record_write.py", "MARK_AFFORDANCE"),
    ("records.py", "LS_AFFORDANCE"),
    ("records.py", "SHOW_AFFORDANCE"),
    ("rm.py", "RM_AFFORDANCE"),
    ("doctor.py", "AFFORDANCES"),
}


def affordance_lines() -> list[str]:
    """Every line a verb prints as a next step, from the modules that print them."""
    return [
        new.NEW_AFFORDANCE,
        records.LS_AFFORDANCE,
        records.SHOW_AFFORDANCE,
        record_write.EDIT_AFFORDANCE,
        record_write.MARK_AFFORDANCE,
        history.UNDO_AFFORDANCE,
        rm.RM_AFFORDANCE,
        *(command for command, _ in doctor.AFFORDANCES),
        # `bm mine` builds its list around the search term, so it is rendered
        # rather than declared. The term is arbitrary; the commands are not.
        *mine.affordances("sqlite"),
    ]


def notice_command_lines() -> list[str]:
    """Every per-command notice line, one condition at a time.

    One call per condition because `notice_lines` caps its output at two
    (`MAX_NOTICES`), so a single call with everything set would hide the two
    lowest-priority lines — the two this guard most needs to see.
    """
    conditions = (
        NoticeCounts(violations=1),
        NoticeCounts(review_due=1),
        NoticeCounts(inbox=1),
        NoticeCounts(dirty=1),
    )
    return [line for counts in conditions for line in notice_lines(counts)]


def declared_affordances(directory: Path) -> set[tuple[str, str]]:
    """Find every module-level constant whose name ends in AFFORDANCE(S).

    A source scan rather than a reflection over imported modules: the question is
    which blocks the *tree* declares, so a module that is never registered — or a
    new one nobody wired up yet — still has to answer for its block.
    """
    found: set[tuple[str, str]] = set()
    for path in sorted(directory.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            # Annotated assignments count too. Every affordance constant today is
            # bare, but the house style annotates module constants freely, so a
            # scan that read only `ast.Assign` would let the next one through.
            # Sequence, not list: `list` is invariant, so a `list[ast.Name]` from
            # `node.targets` will not satisfy a `list[ast.expr]` declaration.
            targets: Sequence[ast.expr]
            if isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = list(node.targets)
            else:
                continue
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            found.update(
                (path.name, name) for name in names if name.endswith(("AFFORDANCE", "AFFORDANCES"))
            )
    return found


def shipped_commands() -> dict[str, set[str]]:
    """Each shipped command, mapped to its sub-commands (empty for a leaf verb).

    Built from the click app typer produces, not from `registered_commands`:
    a declaration's `name` is None whenever it comes from the function name, so
    `mine` would not appear under the name a caller types.
    """
    cli = typer.main.get_command(registered)
    assert isinstance(cli, TyperGroup)
    return {
        name: set(command.commands) if isinstance(command, TyperGroup) else set()
        for name, command in cli.commands.items()
    }


def unrunnable(lines: list[str]) -> list[str]:
    """Return every ``bm …`` invocation in ``lines`` the CLI cannot answer."""
    shipped = shipped_commands()
    missing: list[str] = []
    for line in lines:
        for verb, following in INVOCATION.findall(line):
            if verb not in shipped:
                missing.append(f"bm {verb}")
                continue
            # A group is only runnable with a sub-command, so the word after it
            # has to resolve too — `bm history` alone prints help and exits 2.
            if shipped[verb] and following not in shipped[verb]:
                missing.append(f"bm {verb} {following or ''}".strip())
    return missing


# --- The guards ---


def test_every_affordance_names_a_command_that_exists() -> None:
    """W19 item 5: a hint that answers 'no such command' teaches the surface wrongly."""
    missing = unrunnable(affordance_lines())

    assert not missing, f"affordances name commands the CLI does not ship: {sorted(set(missing))}"


def test_every_notice_names_a_command_that_exists() -> None:
    """A notice states a condition and names what answers it (W5-B); same rule."""
    missing = unrunnable(notice_command_lines())

    assert not missing, f"notices name commands the CLI does not ship: {sorted(set(missing))}"


def test_the_checked_affordance_blocks_are_the_ones_the_tree_declares() -> None:
    """A new verb's block must join this guard, not ship unchecked beside it."""
    assert declared_affordances(COMMANDS_DIR) == AFFORDANCE_CONSTANTS


def test_the_walk_reports_a_command_that_does_not_exist() -> None:
    """Positive control: an empty result must mean 'all runnable', not 'nothing read'.

    Both shapes are controlled — an unknown verb, and a real group given a
    sub-command it does not have.
    """
    assert unrunnable(["bm invent something · bm history nope"]) == [
        "bm invent",
        "bm history nope",
    ]
