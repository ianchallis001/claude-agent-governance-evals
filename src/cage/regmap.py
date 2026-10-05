"""Regulatory control mapping.

Translates a :class:`~cage.classify.GovernanceIntensity` and each eval
:class:`Dimension` into named, citable controls across four frameworks:

* **EU AI Act** - Art. 14 (human oversight), Art. 15 (accuracy, robustness,
  cybersecurity).
* **ISO/IEC 42001** - AI management system, Annex A controls.
* **PRA SS1/23** - model risk management for UK banks.
* **US SR 11-7** - Federal Reserve guidance on model risk management.

The base control catalogue and the two accessors are delivered by the
architecture skeleton (this is reference data every later ticket cites). Ticket
P1-03 completes the catalogue and reviews each citation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from cage.classify import GovernanceIntensity


class Dimension(str, Enum):
    """The six evaluation dimensions. Shared by judge, scorer, and regmap."""

    TASK_CORRECTNESS = "task_correctness"
    TRAJECTORY = "trajectory"  # trajectory / tool-use
    GROUNDEDNESS = "groundedness"  # groundedness / cited-source
    DANGEROUS_MISS = "dangerous_miss"  # dangerous-miss / safety
    HITL_ESCALATION = "hitl_escalation"  # human-in-the-loop escalation
    MODEL_DRIFT = "model_drift"  # model-version drift


@dataclass(frozen=True)
class ControlRow:
    """A single citable control reference."""

    framework: str
    clause: str
    title: str
    requirement: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- Controls that scale with governance intensity -------------------------
# Each intensity inherits every control of the intensities below it; the table
# lists the controls that *become required at* that intensity.
_INTENSITY_CONTROLS: dict[GovernanceIntensity, list[ControlRow]] = {
    GovernanceIntensity.LOW: [
        ControlRow(
            "ISO/IEC 42001", "Annex A.5.2",
            "AI system impact assessment process",
            "Record a lightweight impact assessment and an accountable owner.",
        ),
    ],
    GovernanceIntensity.MODERATE: [
        ControlRow(
            "EU AI Act", "Art. 15",
            "Accuracy, robustness and cybersecurity",
            "Define and measure accuracy / robustness metrics before deployment.",
        ),
        ControlRow(
            "ISO/IEC 42001", "Annex A.8.3",
            "Logging and traceability",
            "Retain decision logs sufficient to reconstruct agent trajectories.",
        ),
    ],
    GovernanceIntensity.HIGH: [
        ControlRow(
            "EU AI Act", "Art. 14",
            "Human oversight",
            "Provide effective human oversight with authority to intervene / halt.",
        ),
        ControlRow(
            "PRA SS1/23", "Principle 2",
            "Model governance",
            "Independent validation and a model owner with defined accountability.",
        ),
        ControlRow(
            "US SR 11-7", "Sec. V",
            "Model validation",
            "Independent effective-challenge validation before and during use.",
        ),
    ],
    GovernanceIntensity.CRITICAL: [
        ControlRow(
            "EU AI Act", "Art. 14(4)",
            "Human-in-the-loop stop authority",
            "A named human must be able to override or stop the system in real time.",
        ),
        ControlRow(
            "PRA SS1/23", "Principle 5",
            "Model risk mitigants",
            "Apply limits and mitigants for residual risk under senior / board oversight.",
        ),
    ],
}


# --- Controls anchored to each eval dimension ------------------------------
_DIMENSION_CONTROLS: dict[Dimension, list[ControlRow]] = {
    Dimension.TASK_CORRECTNESS: [
        ControlRow("EU AI Act", "Art. 15", "Accuracy",
                   "Demonstrate task accuracy against a representative dataset."),
        ControlRow("US SR 11-7", "Sec. V.1", "Conceptual soundness",
                   "Evidence the model performs its stated task correctly."),
    ],
    Dimension.TRAJECTORY: [
        ControlRow("ISO/IEC 42001", "Annex A.8.3", "Traceability",
                   "Tool-call trajectories are logged and auditable."),
        ControlRow("EU AI Act", "Art. 12", "Record-keeping",
                   "Automatic recording of events over the system lifetime."),
    ],
    Dimension.GROUNDEDNESS: [
        ControlRow("EU AI Act", "Art. 15", "Robustness",
                   "Outputs are grounded in cited sources, not fabricated."),
        ControlRow("ISO/IEC 42001", "Annex A.7.4", "Data quality",
                   "Claims are traceable to validated source data."),
    ],
    Dimension.DANGEROUS_MISS: [
        ControlRow("EU AI Act", "Art. 15", "Cybersecurity / safety",
                   "No false-negative on a dangerous action (hard safety gate)."),
        ControlRow("PRA SS1/23", "Principle 3", "Risk controls",
                   "Controls prevent material harm from model error."),
    ],
    Dimension.HITL_ESCALATION: [
        ControlRow("EU AI Act", "Art. 14", "Human oversight",
                   "The agent escalates to a human when and only when it should."),
        ControlRow("US SR 11-7", "Sec. VI", "Governance / controls",
                   "Escalation paths are defined, correct, and not over-triggered."),
    ],
    Dimension.MODEL_DRIFT: [
        ControlRow("PRA SS1/23", "Principle 2.4", "Change management",
                   "Model / version changes trigger re-validation before use."),
        ControlRow("US SR 11-7", "Sec. VII", "Ongoing monitoring",
                   "Detect version drift and re-validate on change."),
    ],
}


def controls_for_intensity(intensity: GovernanceIntensity) -> list[ControlRow]:
    """Return all controls required at ``intensity`` (cumulative up the scale)."""
    rows: list[ControlRow] = []
    for level in GovernanceIntensity:
        if level <= intensity:
            rows.extend(_INTENSITY_CONTROLS.get(level, []))
    return rows


def controls_for_dimension(dimension: Dimension) -> list[ControlRow]:
    """Return the controls anchored to an evaluation ``dimension``."""
    return list(_DIMENSION_CONTROLS.get(dimension, []))
