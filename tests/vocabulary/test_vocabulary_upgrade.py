"""The default-vocabulary upgrade path (GAPS U39).

A governed project's ``vocabulary.yml`` is a serialization of the defaults *as
of the day it was written*. These tests pin the two halves of the upgrade:
provable machine snapshots are detected against the recorded generation
history, and the additive merge that `bm project vocab-sync` applies never
disturbs what a human declared.
"""

import yaml

from basic_memory.vocabulary.model import (
    DEFAULT_VOCABULARY,
    HISTORICAL_DEFAULT_DOCUMENTS,
    DeclaredField,
    Vocabulary,
    defaults_delta,
    defaults_fingerprint,
    matches_superseded_defaults,
    merged_with_defaults,
    parse_vocabulary,
    serialize_vocabulary,
)

# --- Generation detection ---


def test_every_recorded_generation_is_detected():
    for document in HISTORICAL_DEFAULT_DOCUMENTS:
        parsed = parse_vocabulary(document, source="generation")
        assert matches_superseded_defaults(parsed), document


def test_current_defaults_match_and_upgrade_is_a_byte_noop():
    # Reversal (U39 follow-up): the current defaults DO match, so a value-equal
    # but non-canonically ordered file re-enters the auto lane. The cost that
    # made the old assertion right — a rewrite on every cold pass — is paid by
    # upgrade_snapshot_vocabulary's byte no-op skip instead.
    assert matches_superseded_defaults(DEFAULT_VOCABULARY)


def test_detection_survives_yaml_reformatting():
    # The same generation spelled with different key order and flow style: the
    # compare is parse-vs-parse, so bytes must not matter.
    generation = HISTORICAL_DEFAULT_DOCUMENTS[3]
    reordered = yaml.safe_load(yaml.safe_dump(dict(reversed(list(generation.items())))))
    assert matches_superseded_defaults(parse_vocabulary(reordered, source="reformatted"))


def test_one_human_declaration_defeats_detection():
    edited = dict(HISTORICAL_DEFAULT_DOCUMENTS[3])
    edited["types"] = [*edited["types"], "runbook"]
    assert not matches_superseded_defaults(parse_vocabulary(edited, source="edited"))


# --- The tripwire for the next defaults change ---


def test_defaults_fingerprint_is_the_recorded_one():
    """FAILS when DEFAULT_VOCABULARY (or its serialization) changes.

    That is its purpose: before updating this literal, append the *previous*
    generation's document to HISTORICAL_DEFAULT_DOCUMENTS — otherwise every
    file that snapshot wrote loses its upgrade path (GAPS U39).
    """
    assert defaults_fingerprint() == "90f220ea59b5"


def test_canonical_serialization_round_trips():
    reparsed = parse_vocabulary(
        yaml.safe_load(serialize_vocabulary(DEFAULT_VOCABULARY)), source="roundtrip"
    )
    assert reparsed == DEFAULT_VOCABULARY


# --- The additive merge ---

HAND_EDITED = Vocabulary(
    # Human order preserved: runbook first is a declaration, not an accident.
    types=("runbook", "task", "finding"),
    statuses=("open", "done"),
    areas=("ops",),
    review_months=3,
    fields={"tier": DeclaredField(name="tier", kind="string")},
    relations=("relates_to",),
    aliases={"sop": "runbook"},
)


def test_delta_names_only_what_is_missing():
    delta = defaults_delta(HAND_EDITED)

    assert delta.types == ("plan", "guide", "profile", "state", "inbox", "note")
    assert delta.statuses == ("doing", "blocked", "shelved", "dropped")
    assert delta.relations == ("derived_from", "part_of", "supersedes")
    # `todo` and `idea` arrive because their targets exist after the merge;
    # `decision` too — `finding` is already declared.
    assert delta.aliases == ("decision", "todo", "idea")
    assert not delta.empty
    assert "type plan" in delta.describe() and "relation part_of" in delta.describe()


def test_merge_appends_and_never_reorders():
    merged = merged_with_defaults(HAND_EDITED)

    assert merged.types[:3] == ("runbook", "task", "finding")
    assert set(DEFAULT_VOCABULARY.types) <= set(merged.types)
    assert merged.statuses[:2] == ("open", "done")
    assert merged.relations[0] == "relates_to"
    # The human's own alias survives beside the arriving defaults.
    assert merged.aliases["sop"] == "runbook"
    assert merged.aliases["todo"] == "task"
    # The human-only settings pass through untouched.
    assert merged.areas == ("ops",)
    assert merged.review_months == 3
    assert merged.fields["tier"].kind == "string"


def test_merge_is_idempotent():
    once = merged_with_defaults(HAND_EDITED)
    assert defaults_delta(once).empty
    assert merged_with_defaults(once) == once


def test_current_defaults_have_an_empty_delta():
    assert defaults_delta(DEFAULT_VOCABULARY).empty


# --- Order-insensitive detection (U39 follow-up) ---------------------------


def test_merge_shaped_defaults_still_read_as_a_snapshot():
    """A file value-equal to current defaults but append-ordered stays in the
    auto lane — the hn-app/zellij case: defaults merged onto a G4 snapshot."""
    from basic_memory.vocabulary.model import matches_superseded_defaults, parse_vocabulary

    merged = parse_vocabulary(
        {
            "types": ["task", "guide", "finding", "profile", "state", "inbox", "note", "plan"],
            "statuses": ["open", "doing", "blocked", "shelved", "done", "dropped"],
            "areas": [],
            "relations": ["relates_to", "derived_from", "supersedes", "part_of"],
            "review_months": 12,
            "fields": {},
        },
        source="v.yml",
    )
    assert matches_superseded_defaults(merged)


def test_a_human_addition_is_never_a_snapshot():
    from basic_memory.vocabulary.model import matches_superseded_defaults, parse_vocabulary

    edited = parse_vocabulary({"types": ["task", "guide", "runbook"]}, source="v.yml")
    assert not matches_superseded_defaults(edited)


def test_upgrade_skips_an_already_canonical_file(monkeypatch, tmp_path):
    """The current defaults now match the fingerprint, so without the byte
    no-op skip every cold pass would mint an empty history commit."""
    from basic_memory.vocabulary.model import (
        DEFAULT_VOCABULARY,
        serialize_vocabulary,
        upgrade_snapshot_vocabulary,
        vocabulary_path,
    )

    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "data"))
    external_id = "0d0b2f1e-6d3a-4a4e-9d2e-2f8a1b7c5e40"
    path = vocabulary_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_vocabulary(DEFAULT_VOCABULARY), encoding="utf-8")
    before = path.stat().st_mtime_ns

    assert upgrade_snapshot_vocabulary(external_id) == path
    assert path.stat().st_mtime_ns == before
