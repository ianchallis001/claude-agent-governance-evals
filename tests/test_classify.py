"""Tests for cage.classify (enums, matrix, and the classifier core, P1-02)."""

from __future__ import annotations

import pytest

from cage.classify import (
    MATRIX,
    AutonomyLevel,
    BlastRadius,
    Classification,
    GovernanceIntensity,
    ToolEvidence,
    classify_manifest,
    intensity_for,
)
from cage.manifest import AgentManifest, Tool, ToolKind
from cage.regmap import controls_for_intensity

# --- skeleton contract (unchanged) -----------------------------------------


def test_axis_enums_complete():
    assert [a.name for a in AutonomyLevel] == ["L0", "L1", "L2", "L3", "L4", "L5"]
    assert [b.name for b in BlastRadius] == ["BR1", "BR2", "BR3", "BR4", "BR5"]
    assert [g.name for g in GovernanceIntensity] == ["LOW", "MODERATE", "HIGH", "CRITICAL"]


def test_matrix_is_complete_6x5():
    assert len(MATRIX) == 6 * 5
    for autonomy in AutonomyLevel:
        for blast in BlastRadius:
            assert isinstance(intensity_for(autonomy, blast), GovernanceIntensity)


def test_matrix_monotonic_non_decreasing():
    for autonomy in AutonomyLevel:
        row = [int(intensity_for(autonomy, b)) for b in BlastRadius]
        assert row == sorted(row)
    for blast in BlastRadius:
        col = [int(intensity_for(a, blast)) for a in AutonomyLevel]
        assert col == sorted(col)


def test_matrix_corners():
    assert intensity_for(AutonomyLevel.L0, BlastRadius.BR1) is GovernanceIntensity.LOW
    assert intensity_for(AutonomyLevel.L5, BlastRadius.BR5) is GovernanceIntensity.CRITICAL


def test_classification_to_dict():
    c = Classification(
        agent_name="a",
        autonomy=AutonomyLevel.L3,
        blast=BlastRadius.BR4,
        intensity=intensity_for(AutonomyLevel.L3, BlastRadius.BR4),
        evidence=[ToolEvidence("t", "write", BlastRadius.BR3, True, "note")],
        rationale="because",
    )
    d = c.to_dict()
    assert d["autonomy"] == "L3"
    assert d["intensity"] == "HIGH"
    assert d["evidence"][0]["blast_contribution"] == 3
    # controls defaults to empty when a Classification is built by hand.
    assert d["controls"] == []


# --- helpers ----------------------------------------------------------------


def _one_tool_manifest(kind: ToolKind, reversible: bool) -> AgentManifest:
    return AgentManifest(
        name="probe-agent",
        tools=[Tool("the_tool", "A neutral tool.", kind, reversible)],
    )


# --- blast-radius inference -------------------------------------------------


@pytest.mark.parametrize(
    "kind, reversible, expected",
    [
        (ToolKind.READ_PUBLIC, True, BlastRadius.BR1),
        (ToolKind.READ_SENSITIVE, True, BlastRadius.BR2),
        (ToolKind.WRITE, True, BlastRadius.BR3),
        (ToolKind.WRITE, False, BlastRadius.BR4),
        (ToolKind.EGRESS, True, BlastRadius.BR4),   # egress ignores reversible
        (ToolKind.EGRESS, False, BlastRadius.BR4),
        (ToolKind.DESTRUCTIVE, True, BlastRadius.BR4),
        (ToolKind.DESTRUCTIVE, False, BlastRadius.BR5),
        (ToolKind.CODE_EXEC, True, BlastRadius.BR4),
        (ToolKind.CODE_EXEC, False, BlastRadius.BR5),
    ],
)
def test_blast_contribution_per_kind(kind, reversible, expected):
    result = classify_manifest(_one_tool_manifest(kind, reversible))
    assert result.blast is expected
    assert result.evidence[0].blast_contribution is expected
    assert result.evidence[0].kind == kind.value


def test_blast_is_worst_case_across_tools(sample_manifest):
    # READ_PUBLIC, READ_SENSITIVE, WRITE(rev), DESTRUCTIVE(irrev close_account).
    result = classify_manifest(sample_manifest)
    assert result.blast is BlastRadius.BR5  # driven by the irreversible destructive tool
    assert len(result.evidence) == 4
    assert {e.tool for e in result.evidence} == {
        "search_kb",
        "read_ticket",
        "post_reply",
        "close_account",
    }


# --- autonomy inference -----------------------------------------------------


def test_read_only_agent_defaults_to_inform():
    m = AgentManifest(
        name="reporter",
        tools=[
            Tool("lookup", "Look up a public record.", ToolKind.READ_PUBLIC),
            Tool("peek", "Read an internal file.", ToolKind.READ_SENSITIVE),
        ],
    )
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L0
    assert result.blast is BlastRadius.BR2
    assert result.intensity is GovernanceIntensity.LOW


def test_acting_agent_without_signal_defaults_to_l2():
    m = _one_tool_manifest(ToolKind.WRITE, reversible=True)
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L2


def test_oversight_keyword_sets_autonomy():
    m = AgentManifest(
        name="ops-agent",
        tools=[Tool("apply", "Applies changes within guardrails.", ToolKind.WRITE, False)],
    )
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L3  # "within guardrails"
    assert result.blast is BlastRadius.BR4
    assert result.intensity is intensity_for(AutonomyLevel.L3, BlastRadius.BR4)


def test_fully_autonomous_keyword_sets_l5():
    m = AgentManifest(
        name="auto-agent",
        tools=[Tool("push", "Pushes data to a partner, fully autonomous.", ToolKind.EGRESS)],
    )
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L5
    assert result.blast is BlastRadius.BR4
    assert result.intensity is GovernanceIntensity.CRITICAL


def test_read_only_agent_clamped_even_with_high_signal():
    # "monitored" would be L3, but a read-only agent cannot act: clamp to L1.
    m = AgentManifest(
        name="watcher",
        tools=[Tool("scan", "Reads logs; monitored by an operator.", ToolKind.READ_PUBLIC)],
    )
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L1
    assert "clamped" in result.rationale.lower()


def test_suggest_keyword_gives_l1():
    m = AgentManifest(
        name="advisor",
        tools=[Tool("propose", "Suggests a next action for a human.", ToolKind.READ_SENSITIVE)],
    )
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L1


# --- intensity + controls ---------------------------------------------------


def test_intensity_resolves_via_matrix_and_attaches_controls(sample_manifest):
    result = classify_manifest(sample_manifest)
    # L2 x BR5 -> CRITICAL.
    assert result.intensity is GovernanceIntensity.CRITICAL
    assert result.intensity is intensity_for(result.autonomy, result.blast)
    expected = controls_for_intensity(GovernanceIntensity.CRITICAL)
    assert result.controls == expected
    assert result.controls, "CRITICAL must carry at least one control"


def test_to_dict_serialises_controls(sample_manifest):
    d = classify_manifest(sample_manifest).to_dict()
    assert d["controls"], "controls must serialise non-empty for CRITICAL"
    first = d["controls"][0]
    assert set(first) == {
        "framework", "region", "clause", "title", "requirement", "source_url"
    }


def test_rationale_mentions_axes(sample_manifest):
    r = classify_manifest(sample_manifest).rationale
    assert "L2" in r and "BR5" in r and "CRITICAL" in r


# --- edge + error paths -----------------------------------------------------


def test_empty_manifest_is_low_inform():
    m = AgentManifest(name="noop-agent", tools=[])
    result = classify_manifest(m)
    assert result.autonomy is AutonomyLevel.L0
    assert result.blast is BlastRadius.BR1
    assert result.intensity is GovernanceIntensity.LOW
    assert result.evidence == []
    assert result.controls == controls_for_intensity(GovernanceIntensity.LOW)


def test_classify_rejects_empty_name():
    m = AgentManifest(name="   ", tools=[])
    with pytest.raises(ValueError):
        classify_manifest(m)


def test_classify_rejects_duplicate_tool_names():
    m = AgentManifest(
        name="dup-agent",
        tools=[
            Tool("do", "First.", ToolKind.WRITE),
            Tool("do", "Second with the same name.", ToolKind.WRITE),
        ],
    )
    with pytest.raises(ValueError):
        classify_manifest(m)
