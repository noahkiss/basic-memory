"""Structural guard for the per-command notice (GAPS W5-B).

The W5-B decision is that *every* project-touching verb reports what is
outstanding, because agents do not run `bm doctor` unprompted. A verb that
quietly skips the notice is a hole in that guarantee, and holes of this shape are
invisible: the command still works, still prints its payload, and still exits 0.

The guard therefore walks `cli/commands/` as an AST and asserts that each
`@app.command()` either calls ``emit_notices`` or is named in ``EXEMPT`` with a
reason. The T22 lesson applies — a guard over a helper proves nothing about
whether callers reach it — so this sits on the command functions themselves,
which is the layer a new verb is written at.

The AST rather than an import: the question is about the source's shape, and
importing would only tell us the commands exist.
"""

import ast
from pathlib import Path

import basic_memory.cli.commands as commands_package

NOTICE = "emit_notices"

COMMANDS_DIR = Path(commands_package.__file__).parent

# Verbs that read no project's records. Each entry states why, because an
# unexplained exemption is how the next verb joins the list without a decision.
EXEMPT: dict[tuple[str, str], str] = {
    # Config lives in config.json, not in any project's corpus.
    ("config.py", "config_list"): "reads config.json; touches no project's records",
    ("config.py", "config_get"): "reads config.json; touches no project's records",
    ("config.py", "config_set"): "writes config.json; touches no project's records",
    ("config.py", "config_unset"): "writes config.json; touches no project's records",
    # Database lifecycle. `reset` deliberately runs against a registry it is
    # about to destroy, and a count query mid-teardown would read a moving target.
    ("db.py", "reset"): "destroys and rebuilds the index; there is nothing stable to count",
    ("db.py", "reindex"): "a mutation, not a read; the next read verb carries the notice",
    # Rewrites markdown files through an external formatter. It is a file-level
    # mutation over paths the caller named, not a read of a project.
    ("format.py", "format"): "formats named files; it never resolves a project",
    # `bm undo` restores files in the store repository, across every project it
    # holds. A notice there would count violations in projects the undo did not
    # read (W5-C), and its own output already names `bm history dirty`. The next
    # read verb carries the notice, as it does after `bm db reindex`.
    ("history.py", "undo"): "a store-scoped mutation; its scope is the repo, not a project",
    # Importers are one-shot writes into one project.
    ("import_chatgpt.py", "import_chatgpt"): "a one-shot import, not a read",
    ("import_claude_conversations.py", "import_claude"): "a one-shot import, not a read",
    ("import_claude_projects.py", "import_projects"): "a one-shot import, not a read",
    ("import_memory_json.py", "memory_json"): "a one-shot import, not a read",
    # Copies packaged documentation into place. A broken corpus must not block it.
    ("man.py", "install"): "copies packaged docs; never opens the database",
    # Starts the MCP server. It has no payload to append a line to, and its
    # stdout is a protocol stream.
    ("mcp.py", "mcp"): "a long-running server; its stdout carries the MCP protocol",
    # `bm mine` reads Claude Code transcripts off disk. It opens no database and
    # resolves no project, so there is no scope for a notice to cover — W8's
    # table listed it as project-touching before the verb existed.
    ("mine.py", "mine"): "reads transcripts off disk; it never resolves a project",
    # Project registry mutations. The notice rides on reads.
    ("project.py", "add_project"): "a registry mutation, not a read",
    ("project.py", "remove_project"): "a registry mutation, not a read",
    ("project.py", "set_default_project"): "a registry mutation, not a read",
    ("project.py", "move_project"): "a registry mutation, not a read",
    # `bm path` prints one path and nothing else, for `$EDITOR "$(bm path X)"`.
    # A notice inside a command substitution lands in the filename
    # (VERBS_PLAN D9, `docs/OUTPUT_CONTRACT.md`).
    ("records.py", "path"): "prints one path for command substitution; a notice would corrupt it",
    # The MCP tool layer. W20 treats it separately: it is not on the fast path,
    # and the notice's whole cost argument depends on being there.
    ("tool.py", "write_note"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "read_note"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "delete_note"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "edit_note"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "build_context"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "recent_activity"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "search_notes"): "the MCP tool layer; W20 treats it separately",
    ("tool.py", "list_projects"): "the MCP tool layer; W20 treats it separately",
}

HINT = (
    "Every project-touching verb must call {notice} after its payload, so an "
    "agent that never runs 'bm doctor' still learns what is outstanding (GAPS "
    "W5-B). These do not: {names}. Either call it, or add the verb to EXEMPT in "
    "this file with the reason it reads no project."
)


# --- AST walk ---


type CommandNode = ast.FunctionDef | ast.AsyncFunctionDef


def is_command(node: CommandNode) -> bool:
    """True when a function carries a typer ``@x.command(...)`` decorator."""
    return any(".command" in ast.unparse(decorator) for decorator in node.decorator_list)


def calls(node: ast.AST, target: str) -> bool:
    """True when ``target`` is called anywhere inside ``node``, nesting included."""
    return any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == target
        for call in ast.walk(node)
    )


def command_functions(source: str) -> dict[str, CommandNode]:
    """Map each typer command in ``source`` to its function node."""
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and is_command(node)
    }


def commands_missing_notice(directory: Path) -> list[tuple[str, str]]:
    """Return ``(module, function)`` for every command that skips the notice."""
    missing: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.py")):
        for name, node in command_functions(path.read_text(encoding="utf-8")).items():
            if calls(node, NOTICE) or (path.name, name) in EXEMPT:
                continue
            missing.append((path.name, name))
    return missing


def every_command(directory: Path) -> set[tuple[str, str]]:
    return {
        (path.name, name)
        for path in sorted(directory.glob("*.py"))
        for name in command_functions(path.read_text(encoding="utf-8"))
    }


# --- The guards ---


def test_every_project_touching_verb_emits_notices() -> None:
    """A read verb that skips the notice is a silent hole in W5-B's guarantee."""
    missing = commands_missing_notice(COMMANDS_DIR)

    assert not missing, HINT.format(
        notice=NOTICE, names=", ".join(f"{module}:{name}" for module, name in missing)
    )


def test_the_verbs_the_notice_covers_are_the_ones_w5_named() -> None:
    """The covered set is asserted by name, so a silent drop fails here.

    Without this, deleting a call and adding the verb to EXEMPT would keep the
    guard above green while removing the behaviour it guards.
    """
    covered = every_command(COMMANDS_DIR) - set(EXEMPT)

    assert covered == {
        ("brief.py", "brief"),
        ("doctor.py", "doctor"),
        ("history.py", "dirty"),
        ("history.py", "commit"),
        ("new.py", "new"),
        ("orphans.py", "orphans"),
        ("project.py", "list_projects"),
        ("project.py", "ls_project_command"),
        ("project.py", "display_project_info"),
        ("record_write.py", "edit"),
        ("record_write.py", "mark"),
        ("record_write.py", "done"),
        ("records.py", "ls"),
        ("records.py", "show"),
        ("status.py", "status"),
        ("types.py", "types"),
    }


def test_exempt_names_only_commands_that_exist() -> None:
    """A stale exemption guards nothing and hides the next verb of that name."""
    stale = sorted(set(EXEMPT) - every_command(COMMANDS_DIR))

    assert not stale, f"EXEMPT names commands that do not exist: {stale}"


def test_walk_detects_a_command_that_skips_the_notice(tmp_path) -> None:
    """Positive control: the analysis must report a genuinely silent verb.

    Without this, an empty result from the guard could mean "the AST walk is
    broken" rather than "every verb reports" — the false negative the house
    evidence rules exist to prevent.
    """
    module = tmp_path / "sample.py"
    module.write_text(
        "@app.command()\n"
        "def reporting():\n"
        "    typer.echo('payload')\n"
        "    emit_notices(scope, quiet=False, command='reporting')\n"
        "\n"
        "@app.command()\n"
        "def silent():\n"
        "    typer.echo('payload')\n"
        "\n"
        "def helper():\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert commands_missing_notice(tmp_path) == [("sample.py", "silent")]
