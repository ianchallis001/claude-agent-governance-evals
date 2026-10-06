"""Tests for cage.regmap."""

from __future__ import annotations

from cage.classify import GovernanceIntensity
from cage.regmap import (
    FRAMEWORK_REGIONS,
    ControlRow,
    Dimension,
    controls_for_dimension,
    controls_for_intensity,
    region_for_framework,
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
    assert set(d) == {
        "framework", "region", "clause", "title", "requirement", "source_url"
    }
    # Region is derived from the framework, not stored twice.
    assert d["region"] == "EU"  # EU AI Act Art. 14 is the first HITL control


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


# --------------------------------------------------------------------------- #
# Regional control profiles (P3-01)                                           #
# --------------------------------------------------------------------------- #
def test_every_framework_has_a_clean_single_region():
    assert set(FRAMEWORK_REGIONS.values()) == {"EU", "UK", "US", "GLOBAL"}
    # The seven recognised frameworks, each in exactly one region.
    assert FRAMEWORK_REGIONS == {
        "EU AI Act": "EU",
        "GDPR": "EU",
        "ISO/IEC 42001": "GLOBAL",
        "NIST AI RMF": "US",
        "US SR 11-7": "US",
        "PRA SS1/23": "UK",
        "UK GDPR": "UK",
    }


def test_control_row_region_is_derived_from_framework():
    for level in GovernanceIntensity:
        for r in controls_for_intensity(level):
            assert r.region == region_for_framework(r.framework)
    for dim in Dimension:
        for r in controls_for_dimension(dim):
            assert r.region == region_for_framework(r.framework)


def test_new_gdpr_ukgdpr_nist_rows_present_and_tagged():
    # The new dimension rows land under the right framework + region.
    tc = {(r.framework, r.clause, r.region)
          for r in controls_for_dimension(Dimension.TASK_CORRECTNESS)}
    assert ("GDPR", "Art. 5(1)(d)", "EU") in tc
    assert ("UK GDPR", "Art. 5(1)(d)", "UK") in tc
    assert ("NIST AI RMF", "MEASURE", "US") in tc

    hitl = {(r.framework, r.clause, r.region)
            for r in controls_for_dimension(Dimension.HITL_ESCALATION)}
    assert ("GDPR", "Art. 22", "EU") in hitl
    assert ("UK GDPR", "Art. 22", "UK") in hitl
    assert ("NIST AI RMF", "GOVERN", "US") in hitl

    dm = {(r.framework, r.clause) for r in controls_for_dimension(Dimension.DANGEROUS_MISS)}
    assert ("GDPR", "Art. 32") in dm
    assert ("UK GDPR", "Art. 32") in dm

    md = {(r.framework, r.clause) for r in controls_for_dimension(Dimension.MODEL_DRIFT)}
    assert ("NIST AI RMF", "MANAGE") in md

    # The new intensity rows (transfer/residency, DPIA, NIST GOVERN).
    moderate = {(r.framework, r.clause) for r in controls_for_intensity(GovernanceIntensity.MODERATE)}
    assert ("GDPR", "Art. 35") in moderate
    assert ("GDPR", "Chapter V (Art. 44-49)") in moderate
    assert ("UK GDPR", "Chapter V (Art. 44-49)") in moderate
    critical = {(r.framework, r.clause) for r in controls_for_intensity(GovernanceIntensity.CRITICAL)}
    assert ("NIST AI RMF", "GOVERN") in critical


def test_jurisdiction_filter_emits_only_selected_regions_plus_global():
    rows = controls_for_intensity(GovernanceIntensity.CRITICAL, jurisdictions={"US"})
    regions = {r.region for r in rows}
    assert regions <= {"US", "GLOBAL"}
    assert "US" in regions
    assert "GLOBAL" in regions  # ISO/IEC 42001 baseline always present
    assert "EU" not in regions and "UK" not in regions


def test_uk_filter_excludes_eu_and_us_but_keeps_global():
    rows = controls_for_dimension(Dimension.HITL_ESCALATION, jurisdictions={"UK"})
    frameworks = {r.framework for r in rows}
    assert "UK GDPR" in frameworks
    assert "GDPR" not in frameworks  # EU
    assert "US SR 11-7" not in frameworks  # US
    assert "NIST AI RMF" not in frameworks  # US


def test_none_filter_emits_all_frameworks():
    rows = controls_for_dimension(Dimension.HITL_ESCALATION)
    frameworks = {r.framework for r in rows}
    assert {"EU AI Act", "US SR 11-7", "GDPR", "UK GDPR", "NIST AI RMF"} <= frameworks


def test_explicit_framework_filter_is_exact_and_omits_global_unless_named():
    # Explicit frameworks: exactly those, GLOBAL not auto-added.
    rows = controls_for_intensity(
        GovernanceIntensity.CRITICAL, frameworks={"GDPR", "UK GDPR"}
    )
    assert {r.framework for r in rows} == {"GDPR", "UK GDPR"}
    assert all(r.region in {"EU", "UK"} for r in rows)
    # ISO/IEC 42001 (GLOBAL) appears only when explicitly named.
    iso = controls_for_intensity(GovernanceIntensity.CRITICAL, frameworks={"ISO/IEC 42001"})
    assert {r.framework for r in iso} == {"ISO/IEC 42001"}
