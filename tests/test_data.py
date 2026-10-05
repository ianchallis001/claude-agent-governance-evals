"""Tests for the bundled datasets (P2-01 golden, P2-02 calibration).

Both files must load through cage.evals.task.load_dataset and schema-validate
against cage.manifest / cage.classify / cage.regmap: every manifest validates,
the reference classifier reproduces each target (axes + intensity), the safety
labels are internally consistent, and the calibration verdicts use the shared
dimension names with legal VerdictLabel values. The suite also asserts the set
is balanced across the two failure-mode axes the dataset is built around.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import cage
from cage.classify import (
    AutonomyLevel,
    BlastRadius,
    GovernanceIntensity,
    ToolKind,
    classify_manifest,
)
from cage.evals.judge import VerdictLabel
from cage.evals.task import load_dataset
from cage.manifest import AgentManifest
from cage.regmap import Dimension

_DATA = Path(cage.__file__).resolve().parent / "data"
GOLDEN = _DATA / "golden.json"
CALIBRATION = _DATA / "calibration.json"

# The five per-sample gradable dimensions (model_drift is a run-level gate).
_SAMPLE_DIMENSIONS = tuple(
    d.value for d in Dimension if d is not Dimension.MODEL_DRIFT
)


def _dangerous(tool) -> bool:
    if tool.kind in (ToolKind.EGRESS, ToolKind.DESTRUCTIVE, ToolKind.CODE_EXEC):
        return True
    return tool.kind is ToolKind.WRITE and not tool.reversible


# --------------------------------------------------------------------------- #
# Loading                                                                     #
# --------------------------------------------------------------------------- #
def test_both_files_load():
    g = load_dataset(GOLDEN)
    c = load_dataset(CALIBRATION)
    assert g.name == "golden"
    assert c.name == "calibration"
    assert len(g) >= 30
    assert len(c) >= 8


def test_golden_sample_ids_unique():
    g = load_dataset(GOLDEN)
    ids = [s.id for s in g]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Schema validation against manifest.py / classify.py                         #
# --------------------------------------------------------------------------- #
def test_golden_manifests_validate_and_classify_to_target():
    g = load_dataset(GOLDEN)
    for s in g:
        assert "manifest" in s.input, f"{s.id}: missing input.manifest"
        manifest = AgentManifest.from_dict(s.input["manifest"])  # validates
        assert isinstance(manifest, AgentManifest)

        target = s.target
        for key in ("autonomy", "blast", "intensity",
                    "dangerous_actions", "required_escalations", "expected_escalation"):
            assert key in target, f"{s.id}: target missing {key!r}"

        # Legal enum members.
        autonomy = AutonomyLevel[target["autonomy"]]
        blast = BlastRadius[target["blast"]]
        intensity = GovernanceIntensity[target["intensity"]]

        # The reference classifier must reproduce the authored target exactly.
        result = classify_manifest(manifest)
        assert result.autonomy == autonomy, f"{s.id}: autonomy drift"
        assert result.blast == blast, f"{s.id}: blast drift"
        assert result.intensity == intensity, f"{s.id}: intensity drift"


def test_golden_safety_labels_are_consistent():
    g = load_dataset(GOLDEN)
    for s in g:
        manifest = AgentManifest.from_dict(s.input["manifest"])
        tool_names = {t.name for t in manifest.tools}
        dangerous = {t.name for t in manifest.tools if _dangerous(t)}

        da = s.target["dangerous_actions"]
        re_ = s.target["required_escalations"]
        assert isinstance(da, list) and isinstance(re_, list)
        assert set(da) == dangerous, f"{s.id}: dangerous_actions mismatch"
        assert set(re_).issubset(tool_names), f"{s.id}: escalation names unknown"
        # Escalations are always a subset of the dangerous actions.
        assert set(re_).issubset(dangerous), f"{s.id}: escalation not dangerous"

        expected_escalation = s.target["expected_escalation"]
        assert isinstance(expected_escalation, bool)
        assert expected_escalation == bool(re_), f"{s.id}: expected_escalation flag"

        safety_critical = s.metadata.get("safety_critical")
        assert isinstance(safety_critical, bool)
        assert safety_critical == bool(dangerous), f"{s.id}: safety_critical flag"

        # Escalation is only required once the agent can act without the human
        # performing the step (autonomy L2+); never below that.
        autonomy = AutonomyLevel[s.target["autonomy"]]
        if re_:
            assert autonomy >= AutonomyLevel.L2, f"{s.id}: escalation below L2"

        assert isinstance(s.metadata.get("tags"), list) and s.metadata["tags"]


# --------------------------------------------------------------------------- #
# Calibration schema                                                          #
# --------------------------------------------------------------------------- #
def test_calibration_has_valid_human_verdicts():
    import json

    raw = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    legal = {v.value for v in VerdictLabel}
    for s in raw["samples"]:
        assert "id" in s
        manifest = AgentManifest.from_dict(s["input"]["manifest"])  # validates
        result = classify_manifest(manifest)
        assert result.intensity.name == s["target"]["intensity"], f"{s['id']}: intensity"

        hv = s.get("human_verdicts")
        assert isinstance(hv, dict), f"{s['id']}: missing human_verdicts"
        assert set(hv) == set(_SAMPLE_DIMENSIONS), f"{s['id']}: dimension set"
        for dim, label in hv.items():
            assert label in legal, f"{s['id']}/{dim}: illegal verdict {label!r}"


def test_calibration_verdicts_span_labels():
    import json

    raw = json.loads(CALIBRATION.read_text(encoding="utf-8"))
    seen = {
        label
        for s in raw["samples"]
        for label in s["human_verdicts"].values()
    }
    # A degenerate all-PASS set cannot calibrate FP/FN; require real spread.
    assert {"PASS", "FIX", "BLOCK"}.issubset(seen)


# --------------------------------------------------------------------------- #
# Balance across the failure-mode axes the set is built around                #
# --------------------------------------------------------------------------- #
def test_golden_is_balanced():
    g = load_dataset(GOLDEN)
    n = len(g)
    high_gov = sum(1 for s in g if s.target["intensity"] in ("HIGH", "CRITICAL"))
    escalate = sum(1 for s in g if s.target["expected_escalation"])
    safety = sum(1 for s in g if s.metadata["safety_critical"])

    # "Balanced" = neither half below ~35% of the set, on both axes.
    floor = n * 0.35
    assert floor <= high_gov <= n - floor, f"intensity split {high_gov}/{n}"
    assert floor <= escalate <= n - floor, f"escalation split {escalate}/{n}"
    # Safety-critical is deliberately weighted up, but both classes present.
    assert 0 < safety < n

    # Every intensity band and every autonomy level is represented.
    assert {s.target["intensity"] for s in g} == {"LOW", "MODERATE", "HIGH", "CRITICAL"}
    assert {s.target["autonomy"] for s in g} == {f"L{i}" for i in range(6)}


@pytest.mark.parametrize("path", [GOLDEN, CALIBRATION])
def test_schema_version_present(path):
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("schema_version") == 1
    assert isinstance(raw.get("description"), str) and raw["description"]
