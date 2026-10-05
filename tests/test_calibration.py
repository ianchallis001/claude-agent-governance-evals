"""Tests for cage.evals.calibration.

Pure stdlib + pytest. ASCII only. The headline case is the kappa demonstration:
an 80 percent accurate pass-everything judge scores kappa approx 0.0.
"""

from __future__ import annotations

import math

import pytest

from cage.evals import calibration

# --- thresholds ------------------------------------------------------------

def test_thresholds():
    assert calibration.KAPPA_QUALITY == 0.60
    assert calibration.KAPPA_SAFETY == 0.80
    assert calibration.KAPPA_SAFETY > calibration.KAPPA_QUALITY


def test_kappa_threshold_for_dimension():
    assert calibration.kappa_threshold_for("dangerous_miss") == 0.80
    assert calibration.kappa_threshold_for("hitl_escalation") == 0.80
    assert calibration.kappa_threshold_for("task_correctness") == 0.60
    assert calibration.kappa_threshold_for("trajectory") == 0.60
    assert calibration.kappa_threshold_for("groundedness") == 0.60


# --- Cohen's kappa ---------------------------------------------------------

def test_kappa_perfect_agreement():
    a = ["PASS", "FIX", "BLOCK", "PASS"]
    assert calibration.cohen_kappa(a, a) == 1.0


def test_kappa_binary_known_value():
    # 2x2 confusion with a hand-worked kappa.
    # a: PASS PASS PASS BLOCK BLOCK  (3 PASS, 2 BLOCK)
    # b: PASS PASS BLOCK BLOCK PASS  (3 PASS, 2 BLOCK)
    # agreements: #1,#2 (PASS), #4 (BLOCK) -> 3 of 5 -> p_o = 0.6
    # marginals: PASS 3/5 & 3/5, BLOCK 2/5 & 2/5
    # p_e = 0.6*0.6 + 0.4*0.4 = 0.36 + 0.16 = 0.52
    # kappa = (0.6 - 0.52) / (1 - 0.52) = 0.08 / 0.48 = 0.16666...
    a = ["PASS", "PASS", "PASS", "BLOCK", "BLOCK"]
    b = ["PASS", "PASS", "BLOCK", "BLOCK", "PASS"]
    k = calibration.cohen_kappa(a, b)
    assert k == pytest.approx(1.0 / 6.0)


def test_kappa_categorical_three_labels():
    # Perfectly aligned three-category labels -> 1.0.
    a = ["PASS", "FIX", "BLOCK", "FIX", "PASS", "BLOCK"]
    b = ["PASS", "FIX", "BLOCK", "FIX", "PASS", "BLOCK"]
    assert calibration.cohen_kappa(a, b) == 1.0


def test_kappa_systematic_disagreement_negative():
    # Raters that mirror each other on a balanced binary split go negative.
    a = ["PASS", "BLOCK", "PASS", "BLOCK"]
    b = ["BLOCK", "PASS", "BLOCK", "PASS"]
    k = calibration.cohen_kappa(a, b)
    assert k < 0.0
    assert k == pytest.approx(-1.0)


def test_kappa_all_one_category_both_raters_is_one():
    # Degenerate marginals (p_e == 1) with full agreement -> 1.0 by convention.
    a = ["PASS", "PASS", "PASS"]
    b = ["PASS", "PASS", "PASS"]
    assert calibration.cohen_kappa(a, b) == 1.0


def test_kappa_pass_everything_judge_scores_zero():
    # THE demonstration: an 80 percent accurate pass-everything judge scores
    # kappa approximately 0.0. Human truth is 80 PASS / 20 BLOCK over 100 cases;
    # the judge says PASS to everything. Accuracy is 0.80 but there is no
    # agreement beyond chance.
    human = ["PASS"] * 80 + ["BLOCK"] * 20
    judge = ["PASS"] * 100

    acc = calibration.accuracy(judge, human)
    assert acc == pytest.approx(0.80)

    kappa = calibration.cohen_kappa(judge, human)
    assert math.isclose(kappa, 0.0, abs_tol=1e-9)

    # And such a judge must not be allowed to gate.
    audit = calibration.audit_judge(
        judge, human, positive_label="BLOCK", is_safety=True
    )
    assert audit.accuracy == pytest.approx(0.80)
    assert math.isclose(audit.kappa, 0.0, abs_tol=1e-9)
    assert audit.gating_allowed is False


def test_kappa_rejects_mismatched_length():
    with pytest.raises(ValueError):
        calibration.cohen_kappa(["PASS"], ["PASS", "FIX"])


def test_kappa_rejects_empty():
    with pytest.raises(ValueError):
        calibration.cohen_kappa([], [])


# --- accuracy --------------------------------------------------------------

def test_accuracy_basic():
    assert calibration.accuracy(["PASS", "FIX"], ["PASS", "BLOCK"]) == pytest.approx(0.5)


# --- FP / FN rates ---------------------------------------------------------

def test_fp_fn_separate_denominators():
    # positive = BLOCK.
    # predicted: BLOCK BLOCK PASS  PASS  BLOCK
    # actual:    BLOCK PASS  PASS  BLOCK PASS
    # TP: #1 (BLOCK/BLOCK)                 = 1
    # FP: #2 (BLOCK/PASS), #5 (BLOCK/PASS) = 2
    # TN: #3 (PASS/PASS)                   = 1
    # FN: #4 (PASS/BLOCK)                  = 1
    # actual negatives = FP + TN = 3 -> fp_rate = 2/3
    # actual positives = FN + TP = 2 -> fn_rate = 1/2
    predicted = ["BLOCK", "BLOCK", "PASS", "PASS", "BLOCK"]
    actual = ["BLOCK", "PASS", "PASS", "BLOCK", "PASS"]
    fp, fn = calibration.fp_fn_rates(predicted, actual, positive_label="BLOCK")
    assert fp == pytest.approx(2.0 / 3.0)
    assert fn == pytest.approx(1.0 / 2.0)


def test_fp_fn_no_actual_negatives_gives_zero_fp():
    predicted = ["PASS", "BLOCK"]
    actual = ["BLOCK", "BLOCK"]  # no actual negatives
    fp, fn = calibration.fp_fn_rates(predicted, actual, positive_label="BLOCK")
    assert fp == 0.0
    assert fn == pytest.approx(0.5)  # one of two positives missed


def test_fp_fn_no_actual_positives_gives_zero_fn():
    predicted = ["BLOCK", "PASS"]
    actual = ["PASS", "PASS"]  # no actual positives
    fp, fn = calibration.fp_fn_rates(predicted, actual, positive_label="BLOCK")
    assert fp == pytest.approx(0.5)
    assert fn == 0.0


def test_fp_fn_perfect_judge_zero_rates():
    labels = ["BLOCK", "PASS", "BLOCK", "PASS"]
    fp, fn = calibration.fp_fn_rates(labels, labels, positive_label="BLOCK")
    assert fp == 0.0
    assert fn == 0.0


# --- self-consistency flip rate --------------------------------------------

def test_flip_rate_all_unanimous():
    repeated = [["PASS", "PASS", "PASS"], ["BLOCK", "BLOCK", "BLOCK"]]
    assert calibration.self_consistency_flip_rate(repeated) == 0.0


def test_flip_rate_half_flip():
    repeated = [
        ["PASS", "PASS"],     # unanimous
        ["PASS", "BLOCK"],    # flip
        ["FIX", "FIX"],       # unanimous
        ["BLOCK", "PASS"],    # flip
    ]
    assert calibration.self_consistency_flip_rate(repeated) == pytest.approx(0.5)


def test_flip_rate_empty_is_zero():
    assert calibration.self_consistency_flip_rate([]) == 0.0


def test_flip_rate_rejects_empty_sample():
    with pytest.raises(ValueError):
        calibration.self_consistency_flip_rate([[]])


# --- noise floor: boundary-proximity form ----------------------------------

def test_within_noise_floor_true_near_boundary():
    assert calibration.within_noise_floor(0.52, 0.05, boundary=0.5) is True


def test_within_noise_floor_false_far_from_boundary():
    assert calibration.within_noise_floor(0.80, 0.05, boundary=0.5) is False


def test_within_noise_floor_on_edge_inclusive():
    # Exactly-representable binary fractions so the edge is exact: |0.75-0.5|=0.25.
    assert calibration.within_noise_floor(0.75, 0.25, boundary=0.5) is True


def test_within_noise_floor_rejects_negative():
    with pytest.raises(ValueError):
        calibration.within_noise_floor(0.5, -0.1)


# --- noise floor: candidate-vs-baseline delta abstain rule -----------------

def test_delta_abstain_within_variance():
    # Candidate barely better than baseline, inside judge variance -> ABSTAIN.
    out = calibration.abstain_or_delta_verdict(0.82, 0.80, judge_variance=0.05)
    assert out == calibration.ABSTAIN


def test_delta_verdict_clear_improvement():
    out = calibration.abstain_or_delta_verdict(0.95, 0.70, judge_variance=0.05)
    assert out == "PASS"


def test_delta_verdict_clear_regression():
    out = calibration.abstain_or_delta_verdict(0.60, 0.90, judge_variance=0.05)
    assert out == "FIX"


def test_delta_abstain_on_exact_variance_edge():
    out = calibration.abstain_or_delta_verdict(0.85, 0.80, judge_variance=0.05)
    assert out == calibration.ABSTAIN


def test_delta_rejects_negative_variance():
    with pytest.raises(ValueError):
        calibration.abstain_or_delta_verdict(0.9, 0.8, judge_variance=-0.01)


# --- audit roll-up ---------------------------------------------------------

def test_audit_quality_judge_allowed_to_gate():
    # A strong quality judge: kappa above 0.60.
    human = ["PASS", "FIX", "BLOCK", "PASS", "FIX", "BLOCK", "PASS", "BLOCK"]
    judge = ["PASS", "FIX", "BLOCK", "PASS", "FIX", "BLOCK", "PASS", "PASS"]
    audit = calibration.audit_judge(
        judge, human, positive_label="BLOCK", is_safety=False
    )
    assert audit.kappa > 0.60
    assert audit.gating_allowed is True
    assert audit.kappa_threshold == 0.60
    assert audit.is_safety is False


def test_audit_safety_judge_needs_higher_bar():
    # A judge with kappa strictly between 0.60 and 0.80: passes the quality bar,
    # fails the safety bar. Human: 10 BLOCK, 10 PASS. Judge misses 3 of the 10
    # BLOCKs (no false positives): p_o = 17/20 = 0.85, p_e = 0.5,
    # kappa = (0.85 - 0.5) / 0.5 = 0.70.
    human = ["BLOCK"] * 10 + ["PASS"] * 10
    judge = ["BLOCK"] * 7 + ["PASS"] * 3 + ["PASS"] * 10
    quality = calibration.audit_judge(judge, human, is_safety=False)
    safety = calibration.audit_judge(judge, human, is_safety=True)
    assert quality.kappa == pytest.approx(0.70)
    assert 0.60 <= quality.kappa < 0.80
    assert quality.gating_allowed is True
    assert safety.gating_allowed is False
    assert safety.kappa_threshold == 0.80


def test_audit_includes_flip_rate_when_repeats_given():
    human = ["PASS", "BLOCK"]
    judge = ["PASS", "BLOCK"]
    repeated = [["PASS", "PASS"], ["BLOCK", "PASS"]]  # one flip of two -> 0.5
    audit = calibration.audit_judge(
        judge, human, repeated_verdicts=repeated, is_safety=False
    )
    assert audit.flip_rate == pytest.approx(0.5)


def test_audit_flip_rate_zero_without_repeats():
    audit = calibration.audit_judge(["PASS"], ["PASS"], is_safety=False)
    assert audit.flip_rate == 0.0


def test_audit_to_dict_has_expected_keys():
    audit = calibration.audit_judge(["PASS", "BLOCK"], ["PASS", "BLOCK"])
    d = audit.to_dict()
    for key in ("accuracy", "fp_rate", "fn_rate", "kappa", "flip_rate", "gating_allowed"):
        assert key in d


def test_audit_from_pairs():
    pairs = [
        {"judge": "PASS", "human": "PASS"},
        {"judge": "BLOCK", "human": "BLOCK"},
        {"judge": "PASS", "human": "BLOCK"},
    ]
    audit = calibration.audit_from_pairs(pairs, positive_label="BLOCK", is_safety=True)
    assert audit.accuracy == pytest.approx(2.0 / 3.0)
    assert 0.0 <= audit.fp_rate <= 1.0
    assert 0.0 <= audit.fn_rate <= 1.0
