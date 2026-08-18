"""Tests for the frontmatter checker (GAPS W4).

The six per-type records below are copied from ``.forked/schema.md`` §3 and are
the positive control: they prove the checker can return zero violations, so a
"no violations" result elsewhere means something.
"""

from datetime import date, datetime
from typing import Any

import pytest

from basic_memory.vocabulary.checker import Violation, check_frontmatter, has_errors
from basic_memory.vocabulary.model import DEFAULT_VOCABULARY, Vocabulary, parse_vocabulary

VOCABULARY = parse_vocabulary(
    {
        "areas": ["ops", "life"],
        "fields": {
            "host-role": "string",
            "commissioned": "date",
            "tier": {"kind": "enum", "values": ["prod", "staging", "dev"]},
        },
    },
    source="vocabulary.yml",
)

TASK = {
    "id": "tnd-7k2m9x4p",
    "permalink": "tnd-7k2m9x4p",
    "type": "task",
    "title": "Move hn-app DB backups off-container",
    "status": "done",
    "opened": "2026-07-26",
    "date-source": "inline",
    "date-confidence": "day",
    "source": "STATUS.local.md#L4,L8-L10",
    "area": "ops",
    "not-before": "2026-07-27",
}

STATE = {
    "id": "tnd-5v1n8b2c",
    "permalink": "tnd-5v1n8b2c",
    "type": "state",
    "title": "Capcom root disk at 89% (14G free)",
    "source": "STATUS.local.md#L11",
    "area": "ops",
}

GUIDE = {
    "id": "tnd-3h6j0k9l",
    "permalink": "tnd-3h6j0k9l",
    "type": "guide",
    "title": "How to restore a Capcom backup",
    "review-by": "2027-07-26",
    "source": "STATUS.local.md#L20-L28",
    "area": "ops",
}

PROFILE = {
    "id": "tnd-4d7f2g8h",
    "permalink": "tnd-4d7f2g8h",
    "type": "profile",
    "title": "Capcom",
    "since": "2026-03-14",
    "date-source": "git",
    "date-confidence": "day",
    "date-ref": "9e0e00d",
    "source": "STATUS.local.md#L30",
    "area": "ops",
    "host-role": "docker",
}

FINDING = {
    "id": "tnd-q8w3e1r5",
    "permalink": "tnd-q8w3e1r5",
    "type": "finding",
    "title": "An in-container SQLite backup cannot work under a 1g mem_limit",
    "event-date": "2026-07-26",
    "date-source": "inline",
    "date-confidence": "day",
    "review-by": "2027-07-26",
    "source": "STATUS.local.md#L6-L7",
    "area": "ops",
}

INBOX = {
    "id": "tnd-1a2b3c4d",
    "permalink": "tnd-1a2b3c4d",
    "type": "inbox",
    "title": "Something about the backup timer, unclear what",
    "source": "STATUS.local.md#L40",
}


def check(metadata: dict[str, Any], **kwargs: Any) -> list[Violation]:
    return check_frontmatter(metadata, VOCABULARY, **kwargs)


def rules(violations: list[Violation]) -> list[str]:
    return [violation.rule for violation in violations]


def one(violations: list[Violation]) -> Violation:
    """Assert exactly one violation and return it."""
    assert len(violations) == 1, rules(violations)
    return violations[0]


# --- Positive control: one clean record per type ---


@pytest.mark.parametrize(
    "record",
    [TASK, STATE, GUIDE, PROFILE, FINDING, INBOX],
    ids=["task", "state", "guide", "profile", "finding", "inbox"],
)
def test_schema_examples_are_clean(record: dict[str, Any]):
    assert check(record) == []


def test_housekeeping_keys_are_not_flagged():
    # Basic Memory writes these itself; flagging them would put a permanent
    # advisory on every governed record.
    record = STATE | {"created": "2026-08-10", "modified": "2026-08-10", "tags": ["a"]}

    assert check(record) == []


# --- 1. unknown-type ---


def test_unknown_type_names_every_choice_with_its_picking_question():
    violation = one(check(STATE | {"type": "runbook"}))

    assert violation.rule == "unknown-type"
    assert violation.field == "type"
    assert violation.severity == "error"
    for choice in (
        "task (do it)",
        "guide (consult it)",
        "finding (learned it)",
        "profile (refer to it)",
        "state (how things are)",
        "inbox (can't tell)",
    ):
        assert choice in violation.message
    assert "cannot be enabled from a write" in violation.message
    assert "inbox" in violation.message


def test_missing_type_is_unknown_type():
    record = dict(STATE)
    del record["type"]

    violation = one(check(record))

    assert violation.rule == "unknown-type"
    assert "Missing required field 'type'" in violation.message


def test_unknown_type_short_circuits_every_other_rule():
    # A record broken in four other ways still reports one thing to fix first.
    record = {"type": "runbook", "permalink": "other", "id": "tnd-x", "status": "nope"}

    assert rules(check(record)) == ["unknown-type"]


def test_unknown_type_still_reports_the_set_once_change_it_is():
    """Changing `type` to an undeclared value breaks two rules, and both are real.

    Every other rule is keyed on the type and would be noise, so the short circuit
    is right for them. Set-once compares field by field and never consults the
    type, so it stays decidable — and dropping it would hide the violation from
    the table W5 builds (GAPS T22 close block).
    """
    violations = check(GUIDE | {"type": "runbook"}, previous=GUIDE)

    assert rules(violations) == ["unknown-type", "set-once-changed"]
    assert violations[1].field == "type"
    assert "'guide'" in violations[1].message and "'runbook'" in violations[1].message


def test_an_unknown_type_on_a_creation_reports_only_the_type():
    """Positive control for the pairing above: no `previous` means no set-once."""
    assert rules(check(GUIDE | {"type": "runbook"})) == ["unknown-type"]


def test_human_added_type_falls_back_to_the_bare_name():
    vocabulary = Vocabulary(
        types=("task", "runbook"),
        statuses=DEFAULT_VOCABULARY.statuses,
        areas=(),
        review_months=12,
    )

    violation = one(check_frontmatter({"type": "guide"}, vocabulary))

    assert "runbook" in violation.message
    assert "runbook (" not in violation.message
    assert "task (do it)" in violation.message


def test_a_declared_type_the_schema_does_not_know_gets_common_rules_only():
    vocabulary = Vocabulary(
        types=("runbook",),
        statuses=DEFAULT_VOCABULARY.statuses,
        areas=(),
        review_months=12,
    )
    record = {
        "id": "tnd-9z8y7x6w",
        "permalink": "tnd-9z8y7x6w",
        "type": "runbook",
        "title": "Restart the ingest worker",
        "source": "STATUS.local.md#L2",
    }

    assert check_frontmatter(record, vocabulary) == []


# --- 2. missing-required-field (common) ---


@pytest.mark.parametrize("name", ["id", "permalink", "title", "source"])
def test_common_fields_are_required(name: str):
    record = dict(STATE)
    del record[name]

    violations = [v for v in check(record) if v.rule == "missing-required-field"]

    assert [v.field for v in violations] == [name]
    assert "id, permalink, title, and source" in violations[0].message


def test_an_empty_string_is_an_absent_field():
    violations = [v for v in check(STATE | {"title": ""}) if v.rule == "missing-required-field"]

    assert [v.field for v in violations] == ["title"]


# --- 3. permalink-mismatch ---


def test_permalink_must_equal_id():
    violation = one(check(STATE | {"permalink": "capcom-root-disk"}))

    assert violation.rule == "permalink-mismatch"
    assert violation.field == "permalink"
    assert "tnd-5v1n8b2c" in violation.message
    assert "capcom-root-disk" in violation.message


# --- 4. status ---


def test_task_without_status():
    record = dict(TASK)
    del record["status"]

    violation = one(check(record))

    assert violation.rule == "missing-status"
    assert violation.field == "status"
    assert "open, doing, blocked, shelved, done, dropped" in violation.message


def test_task_with_off_vocabulary_status():
    violation = one(check(TASK | {"status": "wontfix"}))

    assert violation.rule == "unknown-status"
    assert violation.field == "status"
    assert "open, doing, blocked, shelved, done, dropped" in violation.message


# --- 5. field-not-on-type ---


def test_a_date_field_belonging_to_another_type():
    violation = one(check(STATE | {"event-date": "2026-07-26"}))

    assert violation.rule == "field-not-on-type"
    assert violation.field == "event-date"
    assert "finding" in violation.message
    assert "state has no date field" in violation.message


def test_a_task_may_not_carry_a_findings_date():
    violation = one(check(TASK | {"event-date": "2026-07-26"}))

    assert violation.rule == "field-not-on-type"
    assert "a task uses 'opened'" in violation.message


@pytest.mark.parametrize(
    ("name", "value", "owners"),
    [
        ("status", "open", "task"),
        ("not-before", "2026-08-01", "task"),
        ("review-by", "2027-08-01", "finding, guide"),
        ("proposed-type", "guide", "inbox"),
    ],
)
def test_a_field_that_belongs_to_another_type(name: str, value: str, owners: str):
    violation = one(check(STATE | {name: value}))

    assert violation.rule == "field-not-on-type"
    assert violation.field == name
    assert owners in violation.message


def test_an_inbox_record_may_propose_the_type_it_thinks_it_is():
    """`proposed-type` is a schema key on `inbox`, so it is neither error nor advisory.

    The case above proves the key reports once on the wrong type. This proves it
    reports nothing on `inbox`: without it, adding the key to `_SCHEMA_KEYS`
    could have been wrong in the other direction and still passed.
    """
    assert check(INBOX | {"proposed-type": "guide"}) == []


# --- 6. supersedes-not-on-type ---

# `supersedes` is a `## Relations` line, not a frontmatter key, so these tests
# pass relation types rather than metadata. The checker's other rules never see
# them, which is why this is the only rule with its own input.


def test_a_finding_may_supersede():
    """The positive control the rest of this section leans on."""
    assert check(FINDING, relation_types=["supersedes", "relates-to"]) == []


@pytest.mark.parametrize("record", [GUIDE, TASK, STATE, PROFILE], ids=lambda r: r["type"])
def test_supersedes_is_refused_on_every_other_type(record: dict[str, Any]):
    violation = one(check(record, relation_types=["supersedes"]))

    assert violation.rule == "supersedes-not-on-type"
    assert violation.field == "supersedes"
    assert violation.severity == "error"
    assert record["type"] in violation.message


def test_the_refusal_names_the_route_a_task_takes_instead():
    """A rejection an agent cannot act on relocates the mistake (GAPS W19)."""
    violation = one(check(TASK, relation_types=["supersedes"]))

    assert "finding" in violation.message
    assert "`bm done`" in violation.message


def test_unparsed_relations_skip_the_rule():
    """``None`` means "not known", never "the record has none".

    The move planner rewrites a path and no relation line, so it has nothing to
    pass. Reading that as "no supersedes" would clear a row a real write left.
    """
    assert check(GUIDE, relation_types=None) == []
    assert check(GUIDE) == []


def test_an_empty_relation_list_is_known_and_clean():
    assert check(GUIDE, relation_types=[]) == []


def test_an_unrelated_relation_type_is_not_a_hit():
    """The rule must not fire on a relation that merely mentions the word."""
    assert check(GUIDE, relation_types=["supersedes-nothing", "relates-to"]) == []


def test_the_match_ignores_capitalisation():
    """`Relation.type` is verbatim from the line, so a capital would be a hole."""
    assert one(check(GUIDE, relation_types=["Supersedes"])).rule == "supersedes-not-on-type"


def test_the_frontmatter_key_stays_an_advisory_beside_the_relation_error():
    """Two different faults with the same name, judged separately.

    A `supersedes:` frontmatter key is an undeclared key — kept, indexed, and
    flagged. The relation is an error that blocks the write. Collapsing them
    would either reject a harmless key or accept a broken record.
    """
    violations = check(GUIDE | {"supersedes": "tnd-0001"}, relation_types=["supersedes"])

    by_rule = {violation.rule: violation.severity for violation in violations}
    assert by_rule == {"unknown-key": "advisory", "supersedes-not-on-type": "error"}
    assert has_errors(violations)


# --- 7. missing-required-field (per type) ---


def test_finding_requires_event_date():
    record = {key: value for key, value in FINDING.items() if key != "event-date"}

    violations = check(record)

    # No date on the record, so its provenance triple has nothing to describe.
    assert rules(violations) == ["missing-required-field", "field-not-on-type", "field-not-on-type"]
    assert [v.field for v in violations] == ["event-date", "date-source", "date-confidence"]


@pytest.mark.parametrize("record", [FINDING, GUIDE], ids=["finding", "guide"])
def test_review_by_is_required(record: dict[str, Any]):
    stripped = {key: value for key, value in record.items() if key != "review-by"}

    violation = one(check(stripped))

    assert violation.rule == "missing-required-field"
    assert violation.field == "review-by"


# --- 8. invalid-date ---


@pytest.mark.parametrize(
    "value", ["26-07-2026", "2026-7-26", "20260726", "not a date", "2026-13-01"]
)
def test_a_date_field_must_be_an_iso_calendar_date(value: str):
    violation = one(check(GUIDE | {"review-by": value}))

    assert violation.rule == "invalid-date"
    assert violation.field == "review-by"
    assert "YYYY-MM-DD" in violation.message


def test_a_native_date_is_accepted():
    # The entity parser normalizes dates to ISO strings; a caller that parsed
    # YAML itself has not, so the checker tolerates both.
    assert check(GUIDE | {"review-by": date(2027, 7, 26)}) == []


def test_a_timestamp_is_not_a_calendar_date():
    violation = one(check(GUIDE | {"review-by": datetime(2027, 7, 26, 10, 0)}))

    assert violation.rule == "invalid-date"


def test_a_declared_date_field_is_checked():
    violation = one(check(PROFILE | {"commissioned": "March 2026"}))

    assert violation.rule == "invalid-date"
    assert violation.field == "commissioned"


# --- 9. the provenance triple ---


@pytest.mark.parametrize("name", ["date-source", "date-confidence"])
def test_a_date_requires_its_provenance(name: str):
    record = {key: value for key, value in TASK.items() if key != name}

    violation = one(check(record))

    assert violation.rule == "missing-required-field"
    assert violation.field == name
    assert "opened" in violation.message


def test_unknown_date_source():
    violation = one(check(TASK | {"date-source": "guessed"}))

    assert violation.rule == "unknown-date-source"
    assert violation.field == "date-source"
    assert "inline, transcript, git, mtime, inferred" in violation.message


def test_unknown_date_confidence():
    violation = one(check(TASK | {"date-confidence": "probably"}))

    assert violation.rule == "unknown-date-confidence"
    assert violation.field == "date-confidence"
    assert "exact, day, month, unknown" in violation.message


@pytest.mark.parametrize("source", ["transcript", "git"])
def test_date_ref_is_required_on_the_evidence_rungs(source: str):
    violation = one(check(TASK | {"date-source": source}))

    assert violation.rule == "date-ref-required"
    assert violation.field == "date-ref"
    assert source in violation.message


@pytest.mark.parametrize("source", ["inline", "mtime", "inferred"])
def test_date_ref_is_forbidden_on_every_other_rung(source: str):
    violation = one(check(TASK | {"date-source": source, "date-ref": "9e0e00d"}))

    assert violation.rule == "date-ref-forbidden"
    assert violation.field == "date-ref"
    assert "transcript" in violation.message and "git" in violation.message


def test_an_unknown_date_source_does_not_also_judge_the_ref():
    violation = one(check(TASK | {"date-source": "guessed", "date-ref": "9e0e00d"}))

    assert violation.rule == "unknown-date-source"


@pytest.mark.parametrize("name", ["date-source", "date-confidence", "date-ref"])
def test_provenance_without_a_date_is_not_on_the_type(name: str):
    violation = one(check(STATE | {name: "inline"}))

    assert violation.rule == "field-not-on-type"
    assert violation.field == name


def test_provenance_on_a_type_whose_optional_date_is_absent():
    record = {key: value for key, value in PROFILE.items() if key != "since"}

    assert rules(check(record)) == ["field-not-on-type"] * 3


# --- 10. unknown-area ---


def test_unknown_area_names_the_declared_areas():
    violation = one(check(STATE | {"area": "kitchen"}))

    assert violation.rule == "unknown-area"
    assert violation.field == "area"
    assert "ops, life" in violation.message


def test_a_project_with_no_areas_says_so_plainly():
    record = STATE | {"area": "ops"}

    violation = one(check_frontmatter(record, DEFAULT_VOCABULARY))

    assert violation.rule == "unknown-area"
    assert violation.message == "This project declares no areas, so omit the 'area' field."


# --- 11. declared fields ---


def test_unknown_enum_value_names_the_allowed_values():
    violation = one(check(PROFILE | {"tier": "canary"}))

    assert violation.rule == "unknown-enum-value"
    assert violation.field == "tier"
    assert "prod, staging, dev" in violation.message


def test_a_declared_enum_value_is_accepted():
    assert check(PROFILE | {"tier": "prod"}) == []


def test_a_declared_string_field_accepts_any_string():
    assert check(PROFILE | {"host-role": "anything at all"}) == []


# --- 12. unknown-key (advisory) ---


def test_unknown_key_is_advisory_and_does_not_block_a_write():
    violations = check(STATE | {"severity": "high"})

    violation = one(violations)
    assert violation.rule == "unknown-key"
    assert violation.field == "severity"
    assert violation.severity == "advisory"
    assert not has_errors(violations)


def test_has_errors_is_true_when_any_violation_is_an_error():
    violations = check(STATE | {"severity": "high", "area": "kitchen"})

    assert sorted(rules(violations)) == ["unknown-area", "unknown-key"]
    assert has_errors(violations)


def test_has_errors_on_a_clean_record():
    assert not has_errors(check(STATE))


# --- 13. set-once ---


def test_an_unchanged_set_once_field_is_clean():
    assert check(TASK, previous=TASK) == []


def test_a_changed_status_is_not_a_set_once_violation():
    # status is one of the four deliberately mutable things in the schema.
    assert check(TASK | {"status": "open"}, previous=TASK) == []


def test_first_set_is_not_a_change():
    previous = {key: value for key, value in GUIDE.items() if key != "area"}

    assert check(GUIDE, previous=previous) == []


def test_a_changed_set_once_field_names_both_values():
    violation = one(check(GUIDE | {"source": "NOTES.md#L1"}, previous=GUIDE))

    assert violation.rule == "set-once-changed"
    assert violation.field == "source"
    assert "STATUS.local.md#L20-L28" in violation.message
    assert "NOTES.md#L1" in violation.message


def test_dropping_a_set_once_field_is_a_change():
    record = {key: value for key, value in GUIDE.items() if key != "area"}

    violation = one(check(record, previous=GUIDE))

    assert violation.rule == "set-once-changed"
    assert violation.field == "area"
    assert "absent" in violation.message


def test_a_task_is_routed_to_the_status_verbs():
    violation = one(check(TASK | {"not-before": "2026-09-01"}, previous=TASK))

    assert "`bm done`" in violation.message and "`bm mark`" in violation.message


def test_a_finding_is_routed_to_supersession():
    violation = one(check(FINDING | {"event-date": "2026-07-27"}, previous=FINDING))

    assert "successor that supersedes it" in violation.message


def test_other_types_are_routed_to_bm_new():
    violation = one(check(GUIDE | {"review-by": "2028-07-26"}, previous=GUIDE))

    assert "`bm new`" in violation.message
