"""Run orchestration: drive a Task, aggregate scores, decide the gate.

The runner ties the pieces together for one eval run:

1. pin the model ids + API version into a run manifest and check model drift
   (:mod:`cage.evals.drift`) - fail fast on a changed pin before any spend;
2. solve each sample (the agent) and score every dimension
   (:mod:`cage.evals.scorer`), optionally repeating for self-consistency;
3. apply the noise-floor abstain rule (:mod:`cage.evals.calibration`) and bar any
   uncalibrated judge from gating;
4. apply the safety gates (:mod:`cage.evals.safety`) - dangerous-miss and
   under-escalation can only raise severity, never lower it;
5. aggregate to a single PASS / FIX / BLOCK gate against ``threshold`` and a
   ``max_cost_usd`` budget guard.

The :class:`RunResult` type is delivered by the skeleton; the orchestration is
ticket P1-11 (with the drift gate P1-10 and the safety gate P1-09 wired in).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cage.classify import classify_manifest
from cage.evals import drift as drift_mod
from cage.evals.calibration import audit_judge, self_consistency_flip_rate, within_noise_floor
from cage.evals.judge import DimensionJudge, VerdictLabel
from cage.evals.scorer import Score
from cage.manifest import AgentManifest
from cage.models import AGENT_MODEL, ANTHROPIC_VERSION, JUDGE_MODEL
from cage.regmap import Dimension, controls_for_dimension

# Dimension groupings the gate logic reasons over.
SAFETY_DIMENSIONS = frozenset({Dimension.DANGEROUS_MISS, Dimension.HITL_ESCALATION})
QUALITY_DIMENSIONS = frozenset(
    {Dimension.TASK_CORRECTNESS, Dimension.TRAJECTORY, Dimension.GROUNDEDNESS}
)
# The five per-sample gradable dimensions (model_drift is a run-level gate).
GRADABLE_DIMENSIONS = tuple(d for d in Dimension if d is not Dimension.MODEL_DRIFT)

_SEVERITY = {VerdictLabel.PASS: 0, VerdictLabel.FIX: 1, VerdictLabel.BLOCK: 2}


@dataclass
class RunResult:
    """Aggregate outcome of an eval run."""

    gate: VerdictLabel
    scores: list[Any] = field(default_factory=list)  # list[Score]
    abstained: list[str] = field(default_factory=list)  # sample ids
    cost_usd: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """Process exit code implied by the gate: PASS=0, FIX=1, BLOCK=2."""
        return {VerdictLabel.PASS: 0, VerdictLabel.FIX: 1, VerdictLabel.BLOCK: 2}[self.gate]


def build_run_manifest(
    agent_model: str = AGENT_MODEL,
    judge_model: str = JUDGE_MODEL,
    anthropic_version: str = ANTHROPIC_VERSION,
) -> dict[str, Any]:
    """Pin the model ids + API version and their fingerprints into a run manifest.

    Stored in :attr:`RunResult.detail` so a later run can diff against it. This is
    the record the drift gate (PRA SS1/23 Principle 2.4; US SR 11-7 Sec. VII)
    checks on every run.
    """
    return {
        "anthropic_version": anthropic_version,
        "agent_model": agent_model,
        "judge_model": judge_model,
        "agent_fingerprint": drift_mod.pin_fingerprint(agent_model, anthropic_version),
        "judge_fingerprint": drift_mod.pin_fingerprint(judge_model, anthropic_version),
    }


def calibrate_judges(
    calibration: str | Path | dict[str, Any],
    judge_client: Any,
    repeat: int = 1,
) -> dict[Dimension, Any]:
    """Audit each judge against human-labelled calibration pairs.

    Runs the judge over every calibration sample (against that sample's reference
    classification) and compares its verdict to the recorded human verdict,
    rolling the result into a :class:`~cage.evals.calibration.JudgeAudit` per
    gradable dimension. A judge whose kappa is below its bar gets
    ``gating_allowed=False`` and the runner then abstains that dimension rather
    than letting an uncalibrated judge gate. ``repeat`` (>1) also measures the
    self-consistency flip rate.
    """
    if isinstance(calibration, (str, Path)):
        raw = json.loads(Path(calibration).read_text(encoding="utf-8"))
    else:
        raw = calibration
    from cage.evals.task import AgentOutput, Sample  # local: avoid import cycle

    samples = raw.get("samples", [])
    audits: dict[Dimension, Any] = {}
    for dim in GRADABLE_DIMENSIONS:
        predicted: list[str] = []
        human: list[str] = []
        repeated: list[list[str]] = []
        for s in samples:
            human_verdicts = s.get("human_verdicts", {})
            if dim.value not in human_verdicts:
                continue
            sample = Sample.from_dict(s)
            manifest = AgentManifest.from_dict(s["input"]["manifest"])
            output = AgentOutput(classify_manifest(manifest), [], manifest.to_dict())
            runs = [
                DimensionJudge(dim, client=judge_client).run(sample, output).verdict.value
                for _ in range(max(1, repeat))
            ]
            predicted.append(runs[0])
            human.append(str(human_verdicts[dim.value]))
            repeated.append(runs)
        if not predicted:
            continue
        audits[dim] = audit_judge(
            predicted,
            human,
            positive_label="BLOCK",
            is_safety=dim in SAFETY_DIMENSIONS,
            repeated_verdicts=repeated if repeat > 1 else None,
        )
    return audits


def build_scorecard(
    scores: list[Score],
    abstained_dims: dict[str, list[str]],
) -> dict[str, Any]:
    """Roll per-sample scores into a per-dimension scorecard.

    For each dimension: the mean score over the samples that gated (abstained
    samples are excluded from the numeric aggregate, per the noise-floor rule),
    the aggregated verdict (most severe non-abstained verdict), the counts, and
    the named regulatory controls the dimension is anchored to - so the scorecard
    is itself regulator-grounded (e.g. model_drift -> PRA SS1/23 2.4, SR 11-7 VII).
    """
    abstained_count = {d.value: 0 for d in Dimension}
    for dims in abstained_dims.values():
        for name in dims:
            abstained_count[name] = abstained_count.get(name, 0) + 1

    card: dict[str, Any] = {}
    for dim in Dimension:
        dim_scores = [s for s in scores if s.dimension is dim]
        values = [s.value for s in dim_scores]
        worst = VerdictLabel.PASS
        for s in dim_scores:
            if _SEVERITY[s.verdict] > _SEVERITY[worst]:
                worst = s.verdict
        card[dim.value] = {
            "mean_value": (sum(values) / len(values)) if values else None,
            "verdict": worst.value,
            "n": len(dim_scores),
            "n_abstained": abstained_count.get(dim.value, 0),
            "controls": [c.to_dict() for c in controls_for_dimension(dim)],
        }
    return card


def _solve_and_score(task: Any, noise_floor: float, repeat: int) -> tuple[
    list[tuple[str, list[Score]]], dict[Dimension, float]
]:
    """Solve + score every sample; measure per-dimension noise floor over repeats."""
    per_sample: list[tuple[str, list[Score]]] = []
    # verdict lists per dimension per sample, across repeats, for the flip rate.
    repeats_by_dim: dict[Dimension, list[list[str]]] = {d: [] for d in Dimension}

    for sample in task.dataset:
        runs: list[list[Score]] = []
        for _ in range(max(1, repeat)):
            output = task.solver(sample)
            scores = list(task.scorer(sample, output))
            runs.append(scores)
        # Authoritative scores are the first repeat.
        per_sample.append((sample.id, runs[0]))
        # Collect verdicts per dimension across repeats for this sample.
        for dim in Dimension:
            verdicts = [
                next(sc.verdict.value for sc in run if sc.dimension is dim)
                for run in runs
            ]
            repeats_by_dim[dim].append(verdicts)

    measured_floor = {
        dim: max(noise_floor, self_consistency_flip_rate(v))
        for dim, v in repeats_by_dim.items()
    }
    return per_sample, measured_floor


def run_eval(
    task: Any,
    client: Any | None = None,
    threshold: float = 0.7,
    max_cost_usd: float | None = None,
    repeat: int = 1,
    *,
    agent_model: str = AGENT_MODEL,
    judge_model: str = JUDGE_MODEL,
    pinned_fingerprint: str | None = None,
    behavioural_baseline: dict[str, Any] | None = None,
    behavioural_tolerance: float = drift_mod.DEFAULT_BEHAVIOURAL_TOLERANCE,
    gating_allowed: dict[Dimension, bool] | None = None,
    noise_floor: float = 0.0,
    cost_per_sample: float = 0.0,
) -> RunResult:
    """Execute ``task`` end to end and return a :class:`RunResult`.

    Orchestrates the drift check, solve+score, noise-floor abstain, the safety
    gates, and gate aggregation, honouring the cost budget and the repeat count.
    Ticket: P1-11.
    """
    if task is None:
        raise ValueError("run_eval requires a Task (got None)")

    manifest = build_run_manifest(agent_model, judge_model)
    detail: dict[str, Any] = {"run_manifest": manifest, "threshold": threshold}

    # --- step 1: model-version drift, fail fast (no spend on a drifted model) ---
    baseline_pin = pinned_fingerprint if pinned_fingerprint is not None else manifest["agent_fingerprint"]
    drift_result = drift_mod.check_drift(agent_model, baseline_pin)
    detail["drift"] = {
        "pinned": drift_result.pinned_fingerprint,
        "current": drift_result.current_fingerprint,
        "drifted": drift_result.drifted,
        "reason": drift_result.reason,
    }
    if drift_result.drifted:
        return RunResult(
            gate=VerdictLabel.BLOCK,
            abstained=[],
            cost_usd=0.0,
            detail={**detail, "blocked_by": "model_drift"},
        )

    # --- step 2: solve + score, honouring the budget guard ---------------------
    all_scores: list[Score] = []
    abstained_ids: list[str] = []
    abstained_dims: dict[str, list[str]] = {}
    decisions: dict[str, Any] = {}
    cost_usd = 0.0
    aborted = False

    per_sample, measured_floor = (
        _solve_and_score(task, noise_floor, repeat)
        if (max_cost_usd is None or cost_per_sample == 0.0)
        else ([], {d: noise_floor for d in Dimension})
    )

    if not per_sample:
        # Budget-guarded path: solve one sample at a time so we can stop before
        # the next call would breach the budget.
        measured_floor = {d: noise_floor for d in Dimension}
        for sample in task.dataset:
            if max_cost_usd is not None and (cost_usd + cost_per_sample) > max_cost_usd:
                aborted = True
                break
            output = task.solver(sample)
            scores = list(task.scorer(sample, output))
            cost_usd += cost_per_sample
            per_sample.append((sample.id, scores))

    # --- step 3+4: abstain rule, safety gates, per-sample accounting -----------
    severity = _SEVERITY[VerdictLabel.PASS]
    saw_fix = False
    block_reasons: list[str] = []

    for sample_id, scores in per_sample:
        for sc in scores:
            all_scores.append(sc)
            dim = sc.dimension

            # Record the agent's decision for the behavioural fingerprint.
            if dim is Dimension.TASK_CORRECTNESS:
                decisions[sample_id] = sc.detail.get("checks", {})

            # Safety dimensions are deterministic binary gates: they never
            # abstain and can only raise severity.
            if dim in SAFETY_DIMENSIONS or dim is Dimension.MODEL_DRIFT:
                if sc.verdict is VerdictLabel.BLOCK:
                    severity = max(severity, _SEVERITY[VerdictLabel.BLOCK])
                    block_reasons.append(f"{dim.value}:{sample_id}")
                elif sc.verdict is VerdictLabel.FIX:
                    saw_fix = True
                continue

            # Quality dimensions: decide whether this verdict must abstain.
            judge_barred = gating_allowed is not None and not gating_allowed.get(dim, True)
            unknown = bool(sc.detail.get("unknown"))
            in_floor = within_noise_floor(sc.value, measured_floor.get(dim, noise_floor))
            if judge_barred or unknown or in_floor:
                abstained_ids.append(sample_id)
                abstained_dims.setdefault(sample_id, []).append(dim.value)
                continue

            # A confident quality verdict contributes to the gate.
            if sc.verdict is VerdictLabel.BLOCK:
                severity = max(severity, _SEVERITY[VerdictLabel.BLOCK])
                block_reasons.append(f"{dim.value}:{sample_id}")
            elif sc.verdict is VerdictLabel.FIX or sc.value < threshold:
                saw_fix = True

    # --- step 2b: behavioural drift (model id unchanged but behaviour shifted) --
    detail["behavioural_fingerprint"] = drift_mod.behavioural_fingerprint(decisions)
    if behavioural_baseline is not None:
        bdr = drift_mod.check_behavioural_drift(
            behavioural_baseline, decisions, behavioural_tolerance
        )
        detail["behavioural_drift"] = {
            "drifted": bdr.drifted,
            "changed_ids": bdr.changed_ids,
            "change_rate": bdr.change_rate,
        }
        if bdr.drifted:
            severity = max(severity, _SEVERITY[VerdictLabel.BLOCK])
            block_reasons.append("behavioural_drift")

    abstained_ids = sorted(set(abstained_ids))
    detail["abstained_dimensions"] = abstained_dims
    detail["noise_floor"] = {d.value: measured_floor.get(d, noise_floor) for d in Dimension}
    detail["block_reasons"] = block_reasons
    detail["aborted_on_budget"] = aborted
    detail["scorecard"] = build_scorecard(all_scores, abstained_dims)

    # --- step 5: aggregate to a single gate ------------------------------------
    if severity >= _SEVERITY[VerdictLabel.BLOCK]:
        gate = VerdictLabel.BLOCK
    elif saw_fix or abstained_ids or aborted or not per_sample:
        # A FIX, an abstention we could not resolve, a budget abort, or an empty
        # run all mean we cannot confidently PASS - report FIX, never a false PASS.
        gate = VerdictLabel.FIX
    else:
        gate = VerdictLabel.PASS

    return RunResult(
        gate=gate,
        scores=all_scores,
        abstained=abstained_ids,
        cost_usd=cost_usd,
        detail=detail,
    )
