"""Structural guard for the native-command import boundary (GAPS.md T18).

A native CLI command must talk to the repository/service layer directly and
must never pull in the in-process FastAPI app, the MCP tool layer, or their
heavyweight leaf imports. sys.modules is process-global, so the only honest
probe is a fresh subprocess: run the command there and assert the banned
modules never loaded.
"""

import json
import os
import subprocess
import sys
import textwrap

# Modules whose presence after a native command proves the boundary was
# crossed. api.app/mcp.tools are the seconds-scale offenders; fastapi and
# dateparser are the leaf imports that used to ride in through the service
# layer (search_service annotation, entity_parser.parse_date).
BANNED_MODULES = (
    "basic_memory.api.app",
    "basic_memory.mcp.tools",
    "fastapi",
    "dateparser",
)

PROBE_SOURCE = textwrap.dedent(
    """
    import json
    import sys

    from typer.testing import CliRunner

    from basic_memory.cli.main import app

    result = CliRunner().invoke(app, ["project", "list", "--json"])
    assert result.exit_code == 0, result.output
    json.loads(result.output)

    banned = json.loads(sys.argv[1])
    print(json.dumps([name for name in banned if name in sys.modules]))
    """
)


def test_project_list_stays_off_api_and_mcp(tmp_path):
    """`bm project list` must not import api.app, mcp.tools, fastapi, or dateparser."""
    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env["HOME"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "notes")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")

    completed = subprocess.run(
        [sys.executable, "-c", PROBE_SOURCE, json.dumps(list(BANNED_MODULES))],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    loaded = json.loads(completed.stdout.strip().splitlines()[-1])
    assert loaded == [], f"native command imported banned modules: {loaded}"


def test_guard_probe_detects_a_crossing(tmp_path):
    """Positive control: the probe must report a banned module that IS loaded.

    Without this, an empty result could mean "probe broken" rather than
    "boundary held" — the class of false negative the evidence rules exist for.
    """
    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env["HOME"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "notes")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")

    # Force one banned module in, then run the same probe body.
    source = "import fastapi\n" + PROBE_SOURCE
    completed = subprocess.run(
        [sys.executable, "-c", source, json.dumps(list(BANNED_MODULES))],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    loaded = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "fastapi" in loaded
