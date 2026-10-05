"""Tests for cage.evals.runner (P1-11) - the whole harness, end to end, offline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cage
from cage.evals import drift
from cage.evals.judge import VerdictLabel
from cage.evals.runner import (
    GRADABLE_DIMENSIONS,
    build_run_manifest,
    calibrate_judges,
    run_eval,
)
from cage.evals.task import governance_task
from cage.regmap import Dimension

_CALIB = Path(cage.__file__).resolve().parent / "data" / "calibration.json"


# --------------------------------------------------------------------------- #
# Skeleton contract (unchanged)                                               #
# --------------------------------------------------------------------------- #
def test_exit_code_mapping():
    from cage.evals.runner import RunResult

    assert RunResult(gate=VerdictLabel.PASS).exit_code == 0
    assert RunResult(gate=VerdictLabel.FIX).exit_code == 1
    assert RunResult(gate=VerdictLabel.BLOCK).exit_code == 2


def test_run_eval_requires_a_task():
    with pytest.raises(ValueError):
        run_eval(task=None)


# --------------------------------------------------------------------------- #
# The run manifest pins the model ids + fingerprints                          #
# --------------------------------------------------------------------------- #
def test_run_manifest_pins_models_and_fingerprints():
    m = build_run_manifest()
    assert m["agent_model"] == cage.AGENT_MODEL
    assert m["judge_model"] == cage.JUDGE_MODEL
    assert m["agent_fingerprint"] == drift.pin_fingerprint(cage.AGENT_MODEL)
    assert m["agent_fingerprint"] != m["judge_fingerprint"]


# --------------------------------------------------------------------------- #
# End-to-end mock run of the whole harness                                    #
# --------------------------------------------------------------------------- #
def test_full_mock_run_passes_with_a_correct_agent(governance_client):
    task = governance_task(client=governance_client)
    result = run_eval(task, threshold=0.8)
    assert result.gate is VerdictLabel.PASS
    assert result.exit_code == 0
    assert not result.abstained
    # 6 dimensions x every sample.
    assert len(result.scores) == 6 * len(task.dataset)
    assert result.detail["run_manifest"]["agent_model"] == cage.AGENT_MODEL
    assert result.detail["drift"]["drifted"] is False
    assert "behavioural_fingerprint" in result.detail


def test_scorecard_is_per_dimension_and_regulator_grounded(governance_client):
    task = governance_task(client=governance_client)
    card = run_eval(task, threshold=0.8).detail["scorecard"]
    assert set(card) == {d.value for d in Dimension}
    for dim in Dimension:
        row = card[dim.value]
        assert row["verdict"] == "PASS"
        assert row["n"] == len(task.dataset)
        assert row["controls"], f"{dim.value} has no mapped controls"
    # model-version drift is anchored to the change-management controls.
    frameworks = {c["framework"] for c in card[Dimension.MODEL_DRIFT.value]["controls"]}
    assert {"PRA SS1/23", "US SR 11-7"}.issubset(frameworks)


# --------------------------------------------------------------------------- #
# Drift gate: a simulated model-version bump fails the run                    #
# --------------------------------------------------------------------------- #
def test_model_version_bump_fails_the_drift_gate(governance_client):
    task = governance_task(client=governance_client)
    # The run was validated earlier against an OLD model id; the pinned classifier
    # has since been bumped. The stored pin no longer matches -> BLOCK.
    stale_pin = drift.pin_fingerprint("claude-sonnet-5-4")
    result = run_eval(task, pinned_fingerprint=stale_pin)
    assert result.gate is VerdictLabel.BLOCK
    assert result.exit_code == 2
    assert result.detail["blocked_by"] == "model_drift"
    # Fail-fast: no samples were scored once drift was detected.
    assert result.scores == []


def test_behavioural_drift_blocks_even_with_same_model(governance_client):
    task = governance_task(client=governance_client)
    # Baseline claims every sample classified to a decision that cannot match the
    # real run -> behavioural drift beyond the zero tolerance.
    baseline = {s.id: {"autonomy": False} for s in task.dataset}
    result = run_eval(task, threshold=0.8, behavioural_baseline=baseline)
    assert result.gate is VerdictLabel.BLOCK
    assert "behavioural_drift" in result.detail["block_reasons"]
    assert result.detail["behavioural_drift"]["drifted"] is True


# --------------------------------------------------------------------------- #
# Safety gate: a dangerous miss BLOCKs regardless of other scores             #
# --------------------------------------------------------------------------- #
def test_a_dangerous_miss_blocks_the_whole_run(governance_client_factory):
    # An agent that rates everything as a harmless public read: it will miss every
    # dangerous action and under-escalate. One such miss must BLOCK.
    def blind(manifest):
        return {
            "autonomy": "L1", "blast": "BR1", "intensity": "LOW",
            "rationale": "everything looks harmless",
            "evidence": [
                {"tool": t["name"], "blast_contribution": "BR1", "note": "claimed harmless"}
                for t in manifest.to_dict()["tools"]
            ],
        }
    client = governance_client_factory(classifier=blind)
    task = governance_task(client=client)
    result = run_eval(task, threshold=0.8)
    assert result.gate is VerdictLabel.BLOCK
    assert any(r.startswith("dangerous_miss:") for r in result.detail["block_reasons"])


# --------------------------------------------------------------------------- #
# Abstain: an unknown judge verdict excludes the sample and cannot PASS        #
# --------------------------------------------------------------------------- #
def test_unknown_judge_verdicts_abstain_and_report_fix(governance_client_factory):
    def unknown(kwargs):
        return {"verdict": "PASS", "score": 0.5, "cited_reason": "cannot decide",
                "confidence": 0.1, "unknown": True}
    client = governance_client_factory(judge_verdict=unknown)
    task = governance_task(client=client)
    result = run_eval(task, threshold=0.8)
    # The agent is correct, but the judges abstain on the quality dimensions, so
    # the harness will not emit a confident PASS.
    assert result.gate is VerdictLabel.FIX
    assert result.abstained  # samples were set aside, not failed
    # Safety never abstains, so no BLOCK was raised by the abstentions.
    assert result.gate is not VerdictLabel.BLOCK


# --------------------------------------------------------------------------- #
# Calibration: an uncalibrated judge is barred from gating                    #
# --------------------------------------------------------------------------- #
def test_calibrate_judges_bars_a_constant_judge(governance_client):
    # The default mock judge answers PASS to everything; against a calibration set
    # that contains FIX/BLOCK human labels its kappa collapses and it is barred.
    audits = calibrate_judges(_CALIB, governance_client)
    assert set(audits) == set(GRADABLE_DIMENSIONS)
    assert all(not a.gating_allowed for a in audits.values())


def test_calibrate_judges_allows_a_matching_judge(governance_client_factory):
    raw = json.loads(_CALIB.read_text(encoding="utf-8"))
    hv_by_id = {s["id"]: s["human_verdicts"] for s in raw["samples"]}
    cues = {
        "TASK CORRECTNESS": "task_correctness",
        "TOOL-USE TRAJECTORY": "trajectory",
        "GROUNDEDNESS": "groundedness",
        "MISSED A DANGEROUS": "dangerous_miss",
        "HUMAN-IN-THE-LOOP ESCALATION": "hitl_escalation",
    }

    def perfect(kwargs):
        prompt = kwargs["messages"][0]["content"]
        sid = json.loads(prompt.split("--- SAMPLE ---")[1].split("--- AGENT")[0])["id"]
        dim = next(d for cue, d in cues.items() if cue in prompt)
        verdict = hv_by_id[sid][dim]
        return {"verdict": verdict, "score": 1.0 if verdict == "PASS" else 0.3,
                "cited_reason": "matches the human label", "confidence": 0.95, "unknown": False}

    audits = calibrate_judges(_CALIB, governance_client_factory(judge_verdict=perfect))
    assert all(a.gating_allowed for a in audits.values())
    assert audits[Dimension.TASK_CORRECTNESS].kappa == pytest.approx(1.0)


def test_barred_quality_dimension_abstains_in_run(governance_client):
    # Feed run_eval a gating map that bars the groundedness judge: that dimension
    # must abstain, so the run cannot confidently PASS.
    task = governance_task(client=governance_client)
    gating = {d: True for d in GRADABLE_DIMENSIONS}
    gating[Dimension.GROUNDEDNESS] = False
    result = run_eval(task, threshold=0.8, gating_allowed=gating)
    assert result.gate is VerdictLabel.FIX
    assert result.abstained
    assert all("groundedness" in dims
               for dims in result.detail["abstained_dimensions"].values())


# --------------------------------------------------------------------------- #
# Self-consistency over repeats                                               #
# --------------------------------------------------------------------------- #
def test_repeat_runs_measure_a_zero_noise_floor_for_a_deterministic_mock(governance_client):
    task = governance_task(client=governance_client)
    result = run_eval(task, threshold=0.8, repeat=3)
    assert result.gate is VerdictLabel.PASS
    assert result.detail["noise_floor"][Dimension.TASK_CORRECTNESS.value] == 0.0


# --------------------------------------------------------------------------- #
# Budget guard                                                                #
# --------------------------------------------------------------------------- #
def test_budget_guard_aborts_before_exceeding(governance_client):
    task = governance_task(client=governance_client)
    # Each sample "costs" 1.0; a budget of 3.5 permits only three samples.
    result = run_eval(task, threshold=0.8, max_cost_usd=3.5, cost_per_sample=1.0)
    assert result.detail["aborted_on_budget"] is True
    assert result.cost_usd <= 3.5
    # An aborted, incomplete run never passes.
    assert result.gate is VerdictLabel.FIX
