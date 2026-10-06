"""Judge calibration: measure the judges before trusting their verdicts.

A judge you have not calibrated is an opinion, not a control. This module scores
the judges themselves against a human-labelled calibration set:

* **Cohen's kappa** - inter-rater agreement (judge vs human), pure stdlib. Works
  for the binary case and the general categorical case (PASS / FIX / BLOCK).
* **FP / FN rates** - false-positive / false-negative against human labels,
  computed per dimension against *separate* denominators (actual negatives for
  the false-positive rate, actual positives for the false-negative rate).
* **Self-consistency flip-rate** - re-run the judge ``--repeat`` times; the flip
  rate is the fraction of samples whose verdict is not unanimous. The flip rate
  defines the measured **noise floor**.
* **Noise-floor abstain rule** - a decision whose candidate-vs-baseline delta
  sits inside the measured judge variance must abstain rather than gate; the
  harness will not issue a PASS/FIX/BLOCK it cannot distinguish from judge noise.
  :func:`within_noise_floor` is the boundary-proximity form used by the scorer;
  :func:`abstain_or_delta_verdict` is the candidate-vs-baseline delta form.
* **Judge audit** - :func:`audit_judge` rolls the metrics into a single
  :class:`JudgeAudit` and decides, from the kappa bar, whether a judge is even
  allowed to gate.

Calibration thresholds (delivered constants):

* ``KAPPA_QUALITY`` = 0.60 - minimum kappa for a quality dimension.
* ``KAPPA_SAFETY``  = 0.80 - minimum kappa for a safety dimension.

A judge whose kappa falls below its bar is not allowed to gate: it reports and
abstains. This keeps an unreliable judge from silently vetoing good work or
waving through bad work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

# Calibration gates (Landis-Koch "substantial"/"almost perfect" bands).
KAPPA_QUALITY = 0.60
KAPPA_SAFETY = 0.80

# The abstain sentinel: returned in place of a PASS/FIX/BLOCK verdict when a
# decision cannot be distinguished from judge noise. Deliberately a plain ASCII
# string so it never collides with a VerdictLabel enum member.
ABSTAIN = "ABSTAIN"

# The safety dimensions carry the higher kappa bar (manifest_integrity included:
# a manifest that understates a dangerous tool is a trust/safety defect).
_SAFETY_DIMENSIONS = frozenset(
    {"dangerous_miss", "hitl_escalation", "manifest_integrity"}
)


def _check_paired(rater_a: Sequence[Any], rater_b: Sequence[Any]) -> None:
    """Validate that two label sequences are non-empty and equal length."""
    if len(rater_a) != len(rater_b):
        raise ValueError(
            "paired label sequences differ in length: "
            f"{len(rater_a)} vs {len(rater_b)}"
        )
    if len(rater_a) == 0:
        raise ValueError("cannot compute agreement over zero samples")


def cohen_kappa(rater_a: Sequence[str], rater_b: Sequence[str]) -> float:
    """Cohen's kappa between two raters over paired categorical labels.

    Pure stdlib. Handles the binary case and the general categorical case (the
    category set is the union of labels seen in either rater). Returns 1.0 for
    perfect agreement, 0.0 for chance-level agreement, and can be negative for
    systematic disagreement.

    Edge case: when every rating lands in a single category for both raters the
    chance-agreement term ``p_e`` is 1.0 and kappa is undefined by the raw
    formula; by convention this returns 1.0 when the raters agree on every
    sample and 0.0 otherwise.
    """
    _check_paired(rater_a, rater_b)
    n = len(rater_a)

    categories = sorted(set(rater_a) | set(rater_b))

    # Observed agreement.
    agree = sum(1 for a, b in zip(rater_a, rater_b, strict=False) if a == b)
    p_o = agree / n

    # Expected (chance) agreement from the marginal distributions.
    count_a = {c: 0 for c in categories}
    count_b = {c: 0 for c in categories}
    for a in rater_a:
        count_a[a] += 1
    for b in rater_b:
        count_b[b] += 1
    p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)

    denom = 1.0 - p_e
    if denom == 0.0:
        # Both marginals degenerate to one category. Perfect agreement -> 1.0;
        # anything else is undefined, reported as 0.0 (no agreement beyond
        # chance can be established).
        return 1.0 if p_o == 1.0 else 0.0
    return (p_o - p_e) / denom


def accuracy(predicted: Sequence[str], actual: Sequence[str]) -> float:
    """Plain fraction of samples where ``predicted`` equals ``actual``."""
    _check_paired(predicted, actual)
    hits = sum(1 for p, a in zip(predicted, actual, strict=False) if p == a)
    return hits / len(predicted)


def fp_fn_rates(
    predicted: Sequence[str],
    actual: Sequence[str],
    positive_label: str,
) -> tuple[float, float]:
    """Return ``(false_positive_rate, false_negative_rate)`` for one label.

    ``positive_label`` is the class treated as the positive case (e.g. "BLOCK"
    for the safety dimension). The two rates use *separate* denominators:

    * false-positive rate = FP / (FP + TN) = FP / (number of actual negatives),
    * false-negative rate = FN / (FN + TP) = FN / (number of actual positives).

    A rate whose denominator is zero (no actual negatives, or no actual
    positives) is reported as 0.0 - there is no error of that kind to make.
    """
    _check_paired(predicted, actual)
    tp = fp = tn = fn = 0
    for p, a in zip(predicted, actual, strict=False):
        p_pos = p == positive_label
        a_pos = a == positive_label
        if p_pos and a_pos:
            tp += 1
        elif p_pos and not a_pos:
            fp += 1
        elif not p_pos and not a_pos:
            tn += 1
        else:  # not predicted positive, but actually positive
            fn += 1

    actual_negatives = fp + tn
    actual_positives = fn + tp
    fp_rate = fp / actual_negatives if actual_negatives else 0.0
    fn_rate = fn / actual_positives if actual_positives else 0.0
    return fp_rate, fn_rate


def self_consistency_flip_rate(repeated_verdicts: Sequence[Sequence[str]]) -> float:
    """Fraction of samples whose repeated verdicts are not unanimous.

    ``repeated_verdicts[i]`` is the list of verdicts from each repeat for sample
    ``i``. A sample "flips" when its repeats are not all identical. The returned
    fraction is the measured noise floor. An empty sample list (or samples each
    with a single repeat) has no flips and returns 0.0.
    """
    samples = [list(rv) for rv in repeated_verdicts]
    if not samples:
        return 0.0
    flipped = 0
    for rv in samples:
        if not rv:
            raise ValueError("a sample has zero repeated verdicts")
        if len(set(rv)) > 1:
            flipped += 1
    return flipped / len(samples)


def within_noise_floor(score: float, noise_floor: float, boundary: float = 0.5) -> bool:
    """True if ``score`` sits within ``noise_floor`` of a decision ``boundary``.

    When True, the harness must abstain on that sample rather than gate: the
    score is too close to the gate boundary to be told apart from judge noise.
    """
    if noise_floor < 0.0:
        raise ValueError(f"noise_floor must be non-negative: {noise_floor}")
    return abs(score - boundary) <= noise_floor


def abstain_or_delta_verdict(
    candidate_score: float,
    baseline_score: float,
    judge_variance: float,
    *,
    improved: str = "PASS",
    regressed: str = "FIX",
) -> str:
    """Noise-floor abstain rule in candidate-vs-baseline delta form.

    If the candidate-vs-baseline delta is within the measured judge variance,
    the difference cannot be told apart from noise, so this returns
    :data:`ABSTAIN` rather than a verdict. Otherwise it returns ``improved``
    when the candidate is meaningfully better and ``regressed`` when it is
    meaningfully worse.
    """
    if judge_variance < 0.0:
        raise ValueError(f"judge_variance must be non-negative: {judge_variance}")
    delta = candidate_score - baseline_score
    if abs(delta) <= judge_variance:
        return ABSTAIN
    return improved if delta > 0 else regressed


@dataclass(frozen=True)
class JudgeAudit:
    """The calibration verdict on a judge itself.

    ``gating_allowed`` is the load-bearing field: a judge whose kappa is below
    its bar is not permitted to gate (it may still report and abstain).
    """

    accuracy: float
    fp_rate: float
    fn_rate: float
    kappa: float
    flip_rate: float
    gating_allowed: bool
    kappa_threshold: float
    is_safety: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def kappa_threshold_for(dimension: str) -> float:
    """The kappa bar a dimension must clear: safety 0.80, otherwise 0.60."""
    return KAPPA_SAFETY if dimension in _SAFETY_DIMENSIONS else KAPPA_QUALITY


def audit_judge(
    predicted: Sequence[str],
    actual: Sequence[str],
    *,
    positive_label: str = "BLOCK",
    is_safety: bool = False,
    repeated_verdicts: Sequence[Sequence[str]] | None = None,
) -> JudgeAudit:
    """Audit a judge over known-verdict cases into a single :class:`JudgeAudit`.

    ``predicted`` are the judge's verdicts; ``actual`` the known (human) ground
    truth. ``positive_label`` is the positive class for the FP/FN split.
    ``is_safety`` selects the kappa bar (``KAPPA_SAFETY`` vs ``KAPPA_QUALITY``).
    ``repeated_verdicts``, if supplied, is the per-sample repeat lists used for
    the self-consistency flip rate (otherwise the flip rate is 0.0).

    A judge whose kappa is below its bar gets ``gating_allowed=False``.
    """
    acc = accuracy(predicted, actual)
    fp_rate, fn_rate = fp_fn_rates(predicted, actual, positive_label)
    kappa = cohen_kappa(predicted, actual)
    flip_rate = (
        self_consistency_flip_rate(repeated_verdicts)
        if repeated_verdicts is not None
        else 0.0
    )
    threshold = KAPPA_SAFETY if is_safety else KAPPA_QUALITY
    gating_allowed = kappa >= threshold
    return JudgeAudit(
        accuracy=acc,
        fp_rate=fp_rate,
        fn_rate=fn_rate,
        kappa=kappa,
        flip_rate=flip_rate,
        gating_allowed=gating_allowed,
        kappa_threshold=threshold,
        is_safety=is_safety,
    )


def audit_from_pairs(
    pairs: Sequence[Mapping[str, str]],
    *,
    predicted_key: str = "judge",
    actual_key: str = "human",
    positive_label: str = "BLOCK",
    is_safety: bool = False,
) -> JudgeAudit:
    """Convenience: audit a judge from a list of ``{judge, human}`` records."""
    predicted = [str(p[predicted_key]) for p in pairs]
    actual = [str(p[actual_key]) for p in pairs]
    return audit_judge(
        predicted,
        actual,
        positive_label=positive_label,
        is_safety=is_safety,
    )
