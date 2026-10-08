"""Annex III convenience wrapper over the CAGE governance classifier.

Stage 9 collector packs (``aff-ai-readiness-collector``,
``aff-ai-runtime-governance``) need two things for each AI agent they inventory:
the autonomy x blast-radius placement and the EU AI Act high-risk controls that
attach to it. Both already live in :func:`cage.classify.classify_manifest` and
``cage.regmap``; this module is a THIN wrapper that re-uses them. It does not
re-implement the matrix or the control catalogue - it calls
``classify_manifest(..., frameworks=["EU AI Act"])`` (the existing framework
filter) and then keeps only the clauses that carry the Chapter III Section 2
high-risk requirements (Arts. 9-15) that apply to Annex III systems. Art. 50
(transparency for certain AI systems) is not an Annex III obligation and is
dropped.

Whether a use case falls in an Annex III area (employment, credit scoring, and
so on) is a legal determination the classifier cannot make from a tool
manifest. The caller may declare it via ``annex_iii_area``; the wrapper only
validates and echoes that declaration. It never infers one.

Three call paths reach the same classifier:

1. Library import (this wrapper)::

       from cage.collectors.annex_iii import classify_annex_iii
       result = classify_annex_iii(manifest)          # AgentManifest
       result.to_dict()

2. CLI (full classification, framework-scoped)::

       cage classify manifest.json --frameworks eu-ai-act

   The CLI routes through ``ClassifierAgent``; add ``--mock`` to stay offline.
   It prints every EU AI Act row, not the Annex III subset.

3. MCP: the ``classify_manifest`` tool of ``cage.mcp_server`` takes the agent
   name, its tool list and optional ``jurisdictions=["EU"]``, and returns the
   same two-axis placement plus the control map.

Stdlib-only; no SDK is imported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from cage.classify import Classification, ToolEvidence, classify_manifest
from cage.manifest import AgentManifest, ManifestError
from cage.regmap import ControlRow

EU_AI_ACT = "EU AI Act"

# Chapter III Section 2 requirements for high-risk systems (Arts. 9-15).
ANNEX_III_ARTICLE_RANGE = (9, 15)

# The eight Annex III areas (Regulation (EU) 2024/1689, Annex III points 1-8).
ANNEX_III_AREAS: dict[str, str] = {
    "1": "Biometrics",
    "2": "Critical infrastructure",
    "3": "Education and vocational training",
    "4": "Employment, workers management and access to self-employment",
    "5": "Access to essential private and public services and benefits",
    "6": "Law enforcement",
    "7": "Migration, asylum and border control management",
    "8": "Administration of justice and democratic processes",
}

_ARTICLE_RE = re.compile(r"^Art\.\s*(\d+)")


def is_annex_iii_clause(row: ControlRow) -> bool:
    """True if ``row`` is an EU AI Act clause within Arts. 9-15."""
    if row.framework != EU_AI_ACT:
        return False
    match = _ARTICLE_RE.match(row.clause)
    if match is None:
        return False
    low, high = ANNEX_III_ARTICLE_RANGE
    return low <= int(match.group(1)) <= high


@dataclass
class AnnexIIIClassification:
    """Two-axis placement plus Annex III-scoped EU AI Act control rows."""

    agent_name: str
    autonomy: str
    blast: str
    intensity: str
    annex_iii_area: str | None
    evidence: list[ToolEvidence] = field(default_factory=list)
    controls: list[ControlRow] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "autonomy": self.autonomy,
            "blast": self.blast,
            "intensity": self.intensity,
            "annex_iii_area": self.annex_iii_area,
            "annex_iii_area_name": ANNEX_III_AREAS.get(self.annex_iii_area or ""),
            "evidence": [e.to_dict() for e in self.evidence],
            "controls": [c.to_dict() for c in self.controls],
            "rationale": self.rationale,
        }


def classify_annex_iii(
    manifest: AgentManifest,
    annex_iii_area: str | None = None,
) -> AnnexIIIClassification:
    """Classify ``manifest`` and scope the controls to Annex III clauses.

    ``annex_iii_area`` is an optional caller declaration ("1".."8"); anything
    else raises :class:`~cage.manifest.ManifestError`. Axes, intensity and
    per-tool evidence come straight from :func:`cage.classify.classify_manifest`.
    """
    if annex_iii_area is not None and str(annex_iii_area) not in ANNEX_III_AREAS:
        raise ManifestError(
            f"unknown Annex III area {annex_iii_area!r}; expected one of "
            f"{sorted(ANNEX_III_AREAS)}",
            path="annex_iii_area",
        )
    base: Classification = classify_manifest(manifest, frameworks=[EU_AI_ACT])
    controls = [c for c in base.controls if is_annex_iii_clause(c)]
    area = str(annex_iii_area) if annex_iii_area is not None else None
    return AnnexIIIClassification(
        agent_name=base.agent_name,
        autonomy=base.autonomy.name,
        blast=base.blast.name,
        intensity=base.intensity.name,
        annex_iii_area=area,
        evidence=list(base.evidence),
        controls=controls,
        rationale=(
            f"{base.rationale} Scoped to {len(controls)} EU AI Act Art. 9-15 "
            "clause(s) relevant to Annex III high-risk systems."
        ),
    )
