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


def test_current_defaults_are_not_a_superseded_generation():
    # A file already at the current defaults needs no upgrade, so it must not
    # match: the auto path would otherwise rewrite it on every generation bump.
    assert not matches_superseded_defaults(DEFAULT_VOCABULARY)


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
