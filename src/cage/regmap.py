"""Regulatory control mapping.

Translates a :class:`~cage.classify.GovernanceIntensity` and each eval
:class:`Dimension` into named, citable controls across the regulatory
frameworks the harness recognises:

* **EU AI Act** (EU) - Art. 12 (record-keeping), Art. 14 (human oversight),
  Art. 15 (accuracy, robustness, cybersecurity).
* **GDPR** (EU) - Reg. (EU) 2016/679: accuracy, automated decisions, security,
  DPIAs, data-protection-by-design, and international-transfer safeguards.
* **UK GDPR** (UK) - the retained Regulation as amended by the DPA 2018; mirrors
  the EU GDPR article numbering, diverging on the international-transfer route
  (ICO IDTA / UK Addendum).
* **ISO/IEC 42001** (GLOBAL) - AI management system, Annex A controls.
* **NIST AI RMF** (US) - the GOVERN / MAP / MEASURE / MANAGE core functions.
* **PRA SS1/23** (UK) - model risk management for UK banks.
* **US SR 11-7** (US) - Federal Reserve guidance on model risk management.

Every framework carries a single clean jurisdiction (EU / UK / US / GLOBAL),
held once in :data:`FRAMEWORK_REGIONS`; a :class:`ControlRow` derives its region
from its framework rather than storing it twice. The two accessors take an
optional region (or explicit framework) filter so a caller can scope the control
map to the jurisdictions an organisation's data actually passes through -
``cage.profiles`` resolves that selection. Filtering changes *which cited
controls are listed*, never a gate outcome.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from cage.classify import GovernanceIntensity

# The jurisdiction every framework belongs to. Held once here; a ControlRow's
# region is derived from this map (not duplicated onto each row).
GLOBAL_REGION = "GLOBAL"
REGIONS: tuple[str, ...] = ("EU", "UK", "US", GLOBAL_REGION)

FRAMEWORK_REGIONS: dict[str, str] = {
    "EU AI Act": "EU",
    "GDPR": "EU",
    "ISO/IEC 42001": GLOBAL_REGION,
    "NIST AI RMF": "US",
    "US SR 11-7": "US",
    "PRA SS1/23": "UK",
    "UK GDPR": "UK",
}

# Stable, lowercase slug per framework - the vocabulary for an explicit
# ``--frameworks`` selection (see cage.profiles).
FRAMEWORK_SLUGS: dict[str, str] = {
    "eu-ai-act": "EU AI Act",
    "gdpr": "GDPR",
    "iso-42001": "ISO/IEC 42001",
    "nist-ai-rmf": "NIST AI RMF",
    "pra-ss1-23": "PRA SS1/23",
    "sr-11-7": "US SR 11-7",
    "uk-gdpr": "UK GDPR",
}


def region_for_framework(framework: str) -> str:
    """Return the jurisdiction a framework belongs to (GLOBAL if unmapped)."""
    return FRAMEWORK_REGIONS.get(framework, GLOBAL_REGION)


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
    """A single citable control reference.

    ``region`` is derived from ``framework`` via :data:`FRAMEWORK_REGIONS`, so a
    row never carries a region that could drift out of step with the map.
    ``source_url`` is the authoritative source for the clause (empty for the
    pre-existing rows whose citations predate the source-url catalogue).
    """

    framework: str
    clause: str
    title: str
    requirement: str
    source_url: str = ""

    @property
    def region(self) -> str:
        return region_for_framework(self.framework)

    def to_dict(self) -> dict[str, Any]:
        return {
            "framework": self.framework,
            "region": self.region,
            "clause": self.clause,
            "title": self.title,
            "requirement": self.requirement,
            "source_url": self.source_url,
        }


# --- Controls that scale with governance intensity -------------------------
# Each intensity inherits every control of the intensities below it; the table
# lists the controls that *become required at* that intensity.
_GDPR_URL = "https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng"
_UK_GDPR_URL = "https://www.legislation.gov.uk/eur/2016/679/contents"
_UK_IDTA_URL = (
    "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/"
    "international-transfers/appropriate-safeguards/what-are-standard-data-"
    "protection-clauses-the-uk-idta-and-the-addendum/"
)
_NIST_URL = "https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf"

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
        ControlRow(
            "GDPR", "Art. 35",
            "Data protection impact assessment",
            "Carry out a data protection impact assessment for high-risk processing.",
            source_url=_GDPR_URL,
        ),
        ControlRow(
            "GDPR", "Chapter V (Art. 44-49)",
            "International transfer safeguards (residency)",
            "Apply appropriate safeguards before transferring personal data outside "
            "the jurisdiction (adequacy or the EU SCCs).",
            source_url=_GDPR_URL,
        ),
        ControlRow(
            "UK GDPR", "Art. 35",
            "Data protection impact assessment",
            "Carry out a data protection impact assessment for high-risk processing.",
            source_url=_UK_GDPR_URL,
        ),
        ControlRow(
            "UK GDPR", "Chapter V (Art. 44-49)",
            "UK transfer via IDTA/Addendum",
            "Transfer personal data from the UK only under the ICO IDTA or the UK "
            "Addendum to the EU SCCs.",
            source_url=_UK_IDTA_URL,
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
        ControlRow(
            "GDPR", "Art. 25",
            "Data protection by design/default",
            "Build data protection into the design and the default configuration.",
            source_url=_GDPR_URL,
        ),
        ControlRow(
            "UK GDPR", "Art. 25",
            "Data protection by design/default",
            "Build data protection into the design and the default configuration.",
            source_url=_UK_GDPR_URL,
        ),
        ControlRow(
            "NIST AI RMF", "GOVERN",
            "Risk-management policies and accountability",
            "Establish risk-management policies and clear lines of accountability.",
            source_url=_NIST_URL,
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
        ControlRow(
            "NIST AI RMF", "GOVERN",
            "Formal oversight and audit structures",
            "Stand up formal oversight and audit structures for the system.",
            source_url=_NIST_URL,
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
        ControlRow("GDPR", "Art. 5(1)(d)", "Personal data accuracy upkeep",
                   "Keep personal data accurate and up to date; rectify or erase "
                   "inaccurate data without delay.",
                   source_url=_GDPR_URL),
        ControlRow("UK GDPR", "Art. 5(1)(d)", "Personal data accuracy upkeep",
                   "Keep personal data accurate and up to date; rectify or erase "
                   "inaccurate data without delay.",
                   source_url=_UK_GDPR_URL),
        ControlRow("NIST AI RMF", "MEASURE", "Benchmark validity and reliability",
                   "Demonstrate benchmark validity and reliability for the stated task.",
                   source_url=_NIST_URL),
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
        ControlRow("NIST AI RMF", "MEASURE", "Measure AI performance and risk",
                   "Measure AI system performance and risk against defined metrics.",
                   source_url=_NIST_URL),
    ],
    Dimension.DANGEROUS_MISS: [
        ControlRow("EU AI Act", "Art. 15", "Cybersecurity / safety",
                   "No false-negative on a dangerous action (hard safety gate)."),
        ControlRow("PRA SS1/23", "Principle 3", "Risk controls",
                   "Controls prevent material harm from model error."),
        ControlRow("GDPR", "Art. 32", "Security of processing safeguards",
                   "Apply technical and organisational safeguards appropriate to the risk.",
                   source_url=_GDPR_URL),
        ControlRow("UK GDPR", "Art. 32", "Security of processing safeguards",
                   "Apply technical and organisational safeguards appropriate to the risk.",
                   source_url=_UK_GDPR_URL),
    ],
    Dimension.HITL_ESCALATION: [
        ControlRow("EU AI Act", "Art. 14", "Human oversight",
                   "The agent escalates to a human when and only when it should."),
        ControlRow("US SR 11-7", "Sec. VI", "Governance / controls",
                   "Escalation paths are defined, correct, and not over-triggered."),
        ControlRow("GDPR", "Art. 22", "Human intervention in automated decisions",
                   "Give the data subject a route to human intervention in solely "
                   "automated decisions with legal or similar effect.",
                   source_url=_GDPR_URL),
        ControlRow("UK GDPR", "Art. 22", "Human intervention in automated decisions",
                   "Give the data subject a route to human intervention in solely "
                   "automated decisions with legal or similar effect.",
                   source_url=_UK_GDPR_URL),
        ControlRow("NIST AI RMF", "GOVERN", "Human oversight and accountability",
                   "Assign human oversight and accountability for the system's decisions.",
                   source_url=_NIST_URL),
    ],
    Dimension.MODEL_DRIFT: [
        ControlRow("PRA SS1/23", "Principle 2.4", "Change management",
                   "Model / version changes trigger re-validation before use."),
        ControlRow("US SR 11-7", "Sec. VII", "Ongoing monitoring",
                   "Detect version drift and re-validate on change."),
        ControlRow("NIST AI RMF", "MANAGE", "Post-deployment monitoring and response",
                   "Monitor the deployed system and respond to changes in performance "
                   "or context.",
                   source_url=_NIST_URL),
    ],
}


def _filter_rows(
    rows: list[ControlRow],
    jurisdictions: Iterable[str] | None,
    frameworks: Iterable[str] | None,
) -> list[ControlRow]:
    """Scope ``rows`` to selected frameworks or regions.

    * ``frameworks`` given - keep exactly the rows whose framework is named
      (an explicit framework selection; GLOBAL is included only if named).
    * ``jurisdictions`` given (and no ``frameworks``) - keep rows whose region is
      in the set; GLOBAL (e.g. ISO/IEC 42001) is always kept as a baseline.
    * neither given - keep everything (unchanged default semantics).
    """
    if frameworks is not None:
        wanted = {str(f) for f in frameworks}
        return [r for r in rows if r.framework in wanted]
    if jurisdictions is not None:
        allowed = {str(j) for j in jurisdictions} | {GLOBAL_REGION}
        return [r for r in rows if r.region in allowed]
    return list(rows)


def controls_for_intensity(
    intensity: GovernanceIntensity,
    jurisdictions: Iterable[str] | None = None,
    frameworks: Iterable[str] | None = None,
) -> list[ControlRow]:
    """Return all controls required at ``intensity`` (cumulative up the scale).

    With ``jurisdictions`` (a set/list of region strings) or ``frameworks`` (a
    set/list of framework names), the result is scoped to that selection; GLOBAL
    controls are always retained under a region filter. With neither, every
    control across all frameworks is returned (the default).
    """
    rows: list[ControlRow] = []
    for level in GovernanceIntensity:
        if level <= intensity:
            rows.extend(_INTENSITY_CONTROLS.get(level, []))
    return _filter_rows(rows, jurisdictions, frameworks)


def controls_for_dimension(
    dimension: Dimension,
    jurisdictions: Iterable[str] | None = None,
    frameworks: Iterable[str] | None = None,
) -> list[ControlRow]:
    """Return the controls anchored to an evaluation ``dimension``.

    Honours the same optional ``jurisdictions`` / ``frameworks`` scoping as
    :func:`controls_for_intensity`.
    """
    rows = list(_DIMENSION_CONTROLS.get(dimension, []))
    return _filter_rows(rows, jurisdictions, frameworks)
