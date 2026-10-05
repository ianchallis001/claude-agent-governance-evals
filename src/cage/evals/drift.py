"""Model-version drift: pin, fingerprint, and fail on change.

An eval result is only valid for the exact model + API version it was produced
against. This module pins that configuration (via
:func:`cage.models.model_fingerprint`), re-fingerprints on every run, and fails
the suite if the fingerprint has changed since the pin - forcing re-validation
before the agent is trusted again.

This is the change-management control mapped to **PRA SS1/23 Principle 2.4** and
**US SR 11-7 Section VII** (ongoing monitoring): a model change must trigger
re-validation, not silently flow through.

The :class:`DriftResult` type and the control mapping are delivered by the
skeleton; the pin/compare logic is ticket P1-10.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from cage.models import ANTHROPIC_VERSION, model_fingerprint
from cage.regmap import ControlRow

# Behavioural tolerance: the fraction of baseline decisions a run may differ on
# before it is treated as behavioural drift even when the model id is unchanged.
# Zero by default - the pinned classifier is deterministic over the golden set,
# so any behavioural change is drift until re-validated.
DEFAULT_BEHAVIOURAL_TOLERANCE = 0.0

# The change-management controls this gate enforces.
DRIFT_CONTROLS: list[ControlRow] = [
    ControlRow("PRA SS1/23", "Principle 2.4", "Change management",
               "Model / version changes trigger re-validation before continued use."),
    ControlRow("US SR 11-7", "Sec. VII", "Ongoing monitoring",
               "Detect version drift and re-validate on any change."),
]


@dataclass(frozen=True)
class DriftResult:
    """The outcome of a drift check."""

    pinned_fingerprint: str
    current_fingerprint: str
    drifted: bool
    model: str
    reason: str = ""

    @property
    def ok(self) -> bool:
        """True if no drift was detected."""
        return not self.drifted


def pin_fingerprint(model: str, anthropic_version: str = ANTHROPIC_VERSION,
                    **extra: object) -> str:
    """Compute and return the configuration fingerprint to pin for ``model``.

    A thin wrapper over :func:`cage.models.model_fingerprint` over the model id,
    the pinned API version, and any ``extra`` run-shaping fields. Store the
    returned value in the run manifest; :func:`check_drift` fails the run if a
    fresh fingerprint differs. Ticket: P1-10.
    """
    return model_fingerprint(model, anthropic_version, extra or None)


def check_drift(model: str, pinned: str, anthropic_version: str = ANTHROPIC_VERSION,
                **extra: object) -> DriftResult:
    """Re-fingerprint ``model`` and compare against the ``pinned`` value.

    Returns a :class:`DriftResult`; ``drifted=True`` must fail the run - a silent
    model / API-version change is the change-management failure mapped to
    PRA SS1/23 Principle 2.4 and US SR 11-7 Sec. VII. Ticket: P1-10.
    """
    current = model_fingerprint(model, anthropic_version, extra or None)
    drifted = current != pinned
    reason = (
        ""
        if not drifted
        else (
            "model / API-version fingerprint changed since the pin "
            f"(pinned {pinned[:12]}..., now {current[:12]}...); "
            "re-validation required before continued use (PRA SS1/23 2.4; SR 11-7 VII)"
        )
    )
    return DriftResult(
        pinned_fingerprint=pinned,
        current_fingerprint=current,
        drifted=drifted,
        model=model,
        reason=reason,
    )


# --- Behavioural fingerprint ------------------------------------------------
# A model id can be unchanged while behaviour shifts (server-side change behind a
# stable alias, a silent weights update). Baseline a fingerprint of the agent's
# *decisions* over the golden set and fail when too many differ.


@dataclass(frozen=True)
class BehaviouralDriftResult:
    """The outcome of a behavioural-fingerprint comparison."""

    pinned_fingerprint: str
    current_fingerprint: str
    changed_ids: list[str] = field(default_factory=list)
    total: int = 0
    tolerance: float = DEFAULT_BEHAVIOURAL_TOLERANCE

    @property
    def change_rate(self) -> float:
        return (len(self.changed_ids) / self.total) if self.total else 0.0

    @property
    def drifted(self) -> bool:
        return self.change_rate > self.tolerance

    @property
    def ok(self) -> bool:
        return not self.drifted


def _canonical_decisions(decisions: Mapping[str, Any]) -> str:
    return json.dumps(
        {str(k): _as_plain(v) for k, v in decisions.items()},
        sort_keys=True,
        separators=(",", ":"),
    )


def _as_plain(value: Any) -> Any:
    fn = getattr(value, "to_dict", None)
    if callable(fn):
        return fn()
    if isinstance(value, Mapping):
        return {str(k): _as_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(v) for v in value]
    return value


def behavioural_fingerprint(decisions: Mapping[str, Any]) -> str:
    """SHA-256 over a canonical encoding of per-sample decisions.

    ``decisions`` maps a sample id to its decision summary (e.g. the axes +
    intensity the agent produced). Two runs with identical decisions share a
    fingerprint; any change moves it.
    """
    canonical = _canonical_decisions(decisions)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_behavioural_drift(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    tolerance: float = DEFAULT_BEHAVIOURAL_TOLERANCE,
) -> BehaviouralDriftResult:
    """Compare a run's per-sample decisions against a baseline.

    Drift beyond ``tolerance`` (fraction of samples whose decision changed) fails
    the run. Samples present in only one of the two maps count as changed.
    """
    if not 0.0 <= tolerance <= 1.0:
        raise ValueError(f"tolerance must be in [0,1]: {tolerance}")
    ids = sorted(set(baseline) | set(current))
    changed = [
        i for i in ids
        if _as_plain(baseline.get(i)) != _as_plain(current.get(i))
    ]
    return BehaviouralDriftResult(
        pinned_fingerprint=behavioural_fingerprint(baseline),
        current_fingerprint=behavioural_fingerprint(current),
        changed_ids=changed,
        total=len(ids),
        tolerance=tolerance,
    )
