"""Tests for cage.regmap."""

from __future__ import annotations

from cage.classify import GovernanceIntensity
from cage.regmap import (
    ControlRow,
    Dimension,
    controls_for_dimension,
    controls_for_intensity,
)


def test_six_dimensions():
    assert {d.value for d in Dimension} == {
        "task_correctness",
        "trajectory",
        "groundedness",
        "dangerous_miss",
        "hitl_escalation",
        "model_drift",
    }


def test_controls_for_intensity_is_cumulative():
    low = controls_for_intensity(GovernanceIntensity.LOW)
    critical = controls_for_intensity(GovernanceIntensity.CRITICAL)
    assert len(low) >= 1
    # Higher intensity inherits all lower-intensity controls, plus more.
    assert len(critical) > len(low)
    assert all(isinstance(r, ControlRow) for r in critical)


def test_each_dimension_maps_to_controls():
    frameworks_seen = set()
    for dim in Dimension:
        rows = controls_for_dimension(dim)
        assert rows, f"{dim} has no controls"
        for r in rows:
            assert r.framework and r.clause and r.requirement
            frameworks_seen.add(r.framework)
    # All four named frameworks appear somewhere in the mapping.
    assert {"EU AI Act", "ISO/IEC 42001", "PRA SS1/23", "US SR 11-7"} <= frameworks_seen


def test_control_row_to_dict():
    r = controls_for_dimension(Dimension.HITL_ESCALATION)[0]
    d = r.to_dict()
    assert set(d) == {"framework", "clause", "title", "requirement"}


def test_intensity_controls_are_nested_supersets():
    order = [
        GovernanceIntensity.LOW,
        GovernanceIntensity.MODERATE,
        GovernanceIntensity.HIGH,
        GovernanceIntensity.CRITICAL,
    ]
    prev: set[tuple[str, str]] = set()
    prev_len = -1
    for level in order:
        rows = controls_for_intensity(level)
        keys = {(r.framework, r.clause) for r in rows}
        # each higher intensity inherits every lower-intensity control
        assert prev <= keys, f"{level.name} dropped an inherited control"
        assert len(rows) > prev_len, f"{level.name} added no new control"
        prev, prev_len = keys, len(rows)


def test_dimension_controls_match_spec_section_4_pins():
    # SPEC.md section 4 pins these primary controls; they must not drift.
    def clauses(dim):
        return {(r.framework, r.clause) for r in controls_for_dimension(dim)}

    assert ("EU AI Act", "Art. 15") in clauses(Dimension.TASK_CORRECTNESS)
    assert ("US SR 11-7", "Sec. V.1") in clauses(Dimension.TASK_CORRECTNESS)
    assert ("ISO/IEC 42001", "Annex A.8.3") in clauses(Dimension.TRAJECTORY)
    assert ("EU AI Act", "Art. 12") in clauses(Dimension.TRAJECTORY)
    assert ("ISO/IEC 42001", "Annex A.7.4") in clauses(Dimension.GROUNDEDNESS)
    assert ("PRA SS1/23", "Principle 3") in clauses(Dimension.DANGEROUS_MISS)
    assert ("EU AI Act", "Art. 14") in clauses(Dimension.HITL_ESCALATION)
    assert ("US SR 11-7", "Sec. VI") in clauses(Dimension.HITL_ESCALATION)
    assert ("PRA SS1/23", "Principle 2.4") in clauses(Dimension.MODEL_DRIFT)
    assert ("US SR 11-7", "Sec. VII") in clauses(Dimension.MODEL_DRIFT)


def test_corrected_iso_and_pra_intensity_citations():
    low = {(r.framework, r.clause) for r in controls_for_intensity(GovernanceIntensity.LOW)}
    critical = {
        (r.framework, r.clause)
        for r in controls_for_intensity(GovernanceIntensity.CRITICAL)
    }
    assert ("ISO/IEC 42001", "Annex A.5.2") in low
    assert ("PRA SS1/23", "Principle 5") in critical


def test_every_control_row_is_fully_populated():
    for level in GovernanceIntensity:
        for r in controls_for_intensity(level):
            assert r.framework and r.clause and r.title and r.requirement
