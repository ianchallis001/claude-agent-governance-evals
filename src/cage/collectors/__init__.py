"""cage.collectors - read-only adapters and governance gates for collector output.

Stdlib-only. Nothing here writes to, or mutates, a collector run folder, and
nothing imports the anthropic / mcp / inspect_ai SDKs at module load.
"""

from __future__ import annotations

from cage.collectors.annex_iii import (
    ANNEX_III_AREAS,
    AnnexIIIClassification,
    classify_annex_iii,
    is_annex_iii_clause,
)
from cage.collectors.assessment import (
    AnchoringResult,
    AssessmentConfig,
    AssessmentGate,
    AssessmentResult,
    ComponentVerdict,
    EvidenceRef,
    Rating,
    ReproducibilityResult,
    aggregate_verdict,
    check_anchoring,
    check_reproducibility,
    check_template_drift,
    evaluate_assessment,
    parse_refs,
    pin_assessment_fingerprint,
    template_hash,
)
from cage.collectors.findings import (
    SCHEMA_VERSION,
    CollectorManifest,
    Finding,
    PackRef,
    load_findings,
    load_manifest,
)

__all__ = [
    "ANNEX_III_AREAS",
    "SCHEMA_VERSION",
    "AnchoringResult",
    "AnnexIIIClassification",
    "AssessmentConfig",
    "AssessmentGate",
    "AssessmentResult",
    "CollectorManifest",
    "ComponentVerdict",
    "EvidenceRef",
    "Finding",
    "PackRef",
    "Rating",
    "ReproducibilityResult",
    "aggregate_verdict",
    "check_anchoring",
    "check_reproducibility",
    "check_template_drift",
    "classify_annex_iii",
    "evaluate_assessment",
    "is_annex_iii_clause",
    "load_findings",
    "load_manifest",
    "parse_refs",
    "pin_assessment_fingerprint",
    "template_hash",
]
