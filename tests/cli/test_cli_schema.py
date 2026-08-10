"""Tests for CLI schema commands (output contract v2).

Each verb has exactly one rendering — no --json. Tests mock the MCP tool
functions and assert the line shapes in docs/OUTPUT_CONTRACT.md.
"""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from basic_memory.cli.commands.schema import (
    render_diff_report,
    render_infer_report,
    render_validate_report,
)
from basic_memory.cli.main import app as cli_app

runner = CliRunner()


# --- Shared mock data ---

VALIDATE_REPORT = {
    "note_type": "person",
    "total_notes": 2,
    "total_entities": 2,
    "valid_count": 1,
    "warning_count": 1,
    "error_count": 1,
    "results": [
        {
            "note_identifier": "people/alice",
            "schema_entity": "person",
            "passed": True,
            "warnings": [],
            "errors": [],
        },
        {
            "note_identifier": "people/bob",
            "schema_entity": "person",
            "passed": False,
            "warnings": ["Missing optional field: role"],
            "errors": ["Missing required field: name"],
        },
    ],
}

INFER_REPORT = {
    "note_type": "person",
    "notes_analyzed": 5,
    "field_frequencies": [
        {"name": "name", "source": "observation", "count": 5, "total": 5, "percentage": 1.0},
        {"name": "role", "source": "observation", "count": 3, "total": 5, "percentage": 0.6},
    ],
    "suggested_schema": {"name": "string, full name", "role?": "string, job title"},
    "suggested_required": ["name"],
    "suggested_optional": ["role"],
    "excluded": [],
}

DIFF_REPORT_WITH_DRIFT = {
    "note_type": "person",
    "schema_found": True,
    "new_fields": [
        {"name": "email", "source": "observation", "count": 3, "total": 5, "percentage": 0.6}
    ],
    "dropped_fields": [
        {"name": "phone", "source": "observation", "count": 0, "total": 5, "percentage": 0.0}
    ],
    "cardinality_changes": ["role: schema declares single-value but usage is typically array"],
}

DIFF_REPORT_NO_DRIFT = {
    "note_type": "person",
    "schema_found": True,
    "new_fields": [],
    "dropped_fields": [],
    "cardinality_changes": [],
}


# --- Renderers (shared with `bm tool schema-*`) ---


def test_render_validate_report_lines(capsys):
    """One line per note, identifier first, details indented, count line last."""
    render_validate_report(VALIDATE_REPORT)

    assert capsys.readouterr().out.splitlines() == [
        "people/alice  pass",
        "people/bob    fail",
        "  warning: Missing optional field: role",
        "  error: Missing required field: name",
        "1/2 valid, 1 warnings, 1 errors",
    ]


def test_render_validate_report_warn_status(capsys):
    """A note that passed with warnings renders as warn, not pass."""
    render_validate_report(
        {
            "total_notes": 1,
            "valid_count": 1,
            "warning_count": 1,
            "error_count": 0,
            "results": [
                {
                    "note_identifier": "people/carol",
                    "passed": True,
                    "warnings": ["Missing optional field: role"],
                    "errors": [],
                }
            ],
        }
    )

    assert capsys.readouterr().out.splitlines() == [
        "people/carol  warn",
        "  warning: Missing optional field: role",
        "1/1 valid, 1 warnings, 0 errors",
    ]


def test_render_infer_report_lines(capsys):
    """Field lines, then the suggested schema as key: value — no JSON blob."""
    render_infer_report(INFER_REPORT, quiet=True)

    assert capsys.readouterr().out.splitlines() == [
        "name  observation  5  100%",
        "role  observation  3  60%",
        "Suggested schema:",
        "  name: string, full name",
        "  role?: string, job title",
        "5 notes analyzed",
    ]


def test_render_infer_report_affordance(capsys):
    """The --save affordance trails the payload and --quiet drops it."""
    render_infer_report(INFER_REPORT)

    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith("--save not yet implemented")
    assert lines[-2] == "5 notes analyzed"


def test_render_diff_report_sections(capsys):
    """Sections are plain headings with counts; field name leads each line."""
    render_diff_report(DIFF_REPORT_WITH_DRIFT)

    assert capsys.readouterr().out.splitlines() == [
        "New fields (1):",
        "  email  60% of notes  observation",
        "Dropped fields (1):",
        "  phone  0% of notes  observation",
        "Cardinality changes (1):",
        "  role: schema declares single-value but usage is typically array",
    ]


def test_render_diff_report_omits_empty_sections(capsys):
    """A section with no rows does not appear — absence is the signal."""
    render_diff_report({**DIFF_REPORT_WITH_DRIFT, "dropped_fields": [], "cardinality_changes": []})

    assert capsys.readouterr().out.splitlines() == [
        "New fields (1):",
        "  email  60% of notes  observation",
    ]


# --- validate ---


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_renders_report(mock_mcp, mock_config_cls):
    """bm schema validate renders one line per note plus the summary."""
    result = runner.invoke(cli_app, ["schema", "validate", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "people/alice  pass" in result.output
    assert "people/bob    fail" in result.output
    assert "  error: Missing required field: name" in result.output
    assert "1/2 valid, 1 warnings, 1 errors" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_strict_exits_on_errors(mock_mcp, mock_config_cls):
    """--strict renders the report, then fails on stderr with the error count."""
    result = runner.invoke(cli_app, ["schema", "validate", "person", "--strict"])

    assert result.exit_code == 1
    assert "1/2 valid, 1 warnings, 1 errors" in result.output
    assert "strict: 1 errors" in result.stderr


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value={
        "total_notes": 0,
        "total_entities": 0,
        "valid_count": 0,
        "warning_count": 0,
        "error_count": 0,
        "results": [],
        "reason": "No notes found of type 'person'",
    },
)
def test_validate_empty_is_a_result_not_an_error(mock_mcp, mock_config_cls):
    """A legitimate empty renders the reason and exits 0 (contract rule 5)."""
    result = runner.invoke(cli_app, ["schema", "validate", "person"])

    assert result.exit_code == 0
    assert "No notes found of type 'person'" in result.output


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value={"error": "Schema validation failed: database on fire"},
)
def test_validate_error_response(mock_mcp, mock_config_cls):
    """A genuine failure goes to stderr, exits 1, and writes nothing to stdout."""
    result = runner.invoke(cli_app, ["schema", "validate", "person"])

    assert result.exit_code == 1
    assert "Schema validation failed" in result.stderr
    assert "people/" not in result.output  # no payload rows on the error path


@patch("basic_memory.cli.commands.schema.lookup_project", return_value=(None, None))
def test_validate_unknown_project(mock_lookup):
    """An unscoped request is a failure: stderr, exit 1 (contract rule 5)."""
    result = runner.invoke(cli_app, ["schema", "validate", "person", "--project", "nope"])

    assert result.exit_code == 1
    assert "No project found named: nope" in result.stderr


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_validate",
    new_callable=AsyncMock,
    return_value=VALIDATE_REPORT,
)
def test_validate_identifier_heuristic(mock_mcp, mock_config_cls):
    """bm schema validate treats target with / as identifier."""
    result = runner.invoke(cli_app, ["schema", "validate", "people/alice.md"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["identifier"] == "people/alice.md"
    assert mock_mcp.call_args.kwargs["note_type"] is None


# --- infer ---


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_infer_renders_report(mock_mcp, mock_config_cls):
    """bm schema infer renders field lines and the suggested schema."""
    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "name  observation  5  100%" in result.output
    assert "Suggested schema:" in result.output
    assert "  role?: string, job title" in result.output
    assert "5 notes analyzed" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_infer_save_affordance_and_quiet(mock_mcp, mock_config_cls):
    """--save earns the not-implemented affordance; --quiet drops it."""
    saved = runner.invoke(cli_app, ["schema", "infer", "person", "--save"])
    quiet = runner.invoke(cli_app, ["schema", "infer", "person", "--save", "--quiet"])

    assert saved.exit_code == 0
    assert "--save not yet implemented" in saved.output
    assert quiet.exit_code == 0
    assert "--save not yet implemented" not in quiet.output
    assert "5 notes analyzed" in quiet.output


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value=INFER_REPORT,
)
def test_infer_threshold_passthrough(mock_mcp, mock_config_cls):
    """bm schema infer passes --threshold through to MCP tool."""
    result = runner.invoke(cli_app, ["schema", "infer", "person", "--threshold", "0.5"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert mock_mcp.call_args.kwargs["threshold"] == 0.5


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value={"error": "Schema inference failed: database on fire"},
)
def test_infer_error_response(mock_mcp, mock_config_cls):
    """A genuine failure goes to stderr, exits 1, and writes nothing to stdout."""
    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 1
    assert "Schema inference failed" in result.stderr
    assert "people/" not in result.output  # no payload rows on the error path


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value={
        "note_type": "person",
        "notes_analyzed": 5,
        "threshold": 0.25,
        "suggested_schema": None,
        "reason": "No schema pattern found for 'person' (threshold: 25%)",
    },
)
def test_infer_no_pattern_is_a_result_not_an_error(mock_mcp, mock_config_cls):
    """A legitimate no-pattern answer renders the reason and exits 0 (GAPS O5)."""
    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0
    assert "No schema pattern found for 'person' (threshold: 25%)" in result.output


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_infer",
    new_callable=AsyncMock,
    return_value={
        "note_type": "person",
        "notes_analyzed": 0,
        "field_frequencies": [],
        "suggested_schema": {},
        "suggested_required": [],
        "suggested_optional": [],
        "excluded": [],
    },
)
def test_infer_zero_notes(mock_mcp, mock_config_cls):
    """Zero notes carries no reason from the tool, but is still a result, exit 0."""
    result = runner.invoke(cli_app, ["schema", "infer", "person"])

    assert result.exit_code == 0
    assert "No notes found with type: person" in result.output


# --- diff ---


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_diff",
    new_callable=AsyncMock,
    return_value=DIFF_REPORT_WITH_DRIFT,
)
def test_diff_renders_drift(mock_mcp, mock_config_cls):
    """bm schema diff shows new/dropped fields and cardinality changes."""
    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "New fields (1):" in result.output
    assert "  email  60% of notes  observation" in result.output
    assert "Dropped fields (1):" in result.output
    assert "  phone  0% of notes  observation" in result.output
    assert "Cardinality changes (1):" in result.output
    assert "  role: schema declares single-value but usage is typically array" in result.output
    mock_mcp.assert_called_once()
    assert mock_mcp.call_args.kwargs["output_format"] == "json"


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_diff",
    new_callable=AsyncMock,
    return_value=DIFF_REPORT_NO_DRIFT,
)
def test_diff_no_drift(mock_mcp, mock_config_cls):
    """No drift is a result: one line, exit 0."""
    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert "No drift detected for person schema." in result.output


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_diff",
    new_callable=AsyncMock,
    return_value={
        "note_type": "person",
        "schema_found": False,
        "new_fields": [],
        "dropped_fields": [],
        "cardinality_changes": [],
        "reason": "No schema found for type 'person'",
    },
)
def test_diff_no_schema_is_a_result_not_an_error(mock_mcp, mock_config_cls):
    """No schema to diff against is a result, exit 0 (contract rule 5)."""
    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 0
    assert "No schema found for type 'person'" in result.output


@patch("basic_memory.cli.commands.schema.resolve_cli_project", return_value="test-project")
@patch(
    "basic_memory.mcp.tools.schema_diff",
    new_callable=AsyncMock,
    return_value={"error": "Schema diff failed: database on fire"},
)
def test_diff_error_response(mock_mcp, mock_config_cls):
    """A genuine failure goes to stderr, exits 1, and writes nothing to stdout."""
    result = runner.invoke(cli_app, ["schema", "diff", "person"])

    assert result.exit_code == 1
    assert "Schema diff failed" in result.stderr
    assert "people/" not in result.output  # no payload rows on the error path
