"""Two-axis governance classification: autonomy x blast-radius -> intensity.

The classifier reasons over an :class:`~cage.manifest.AgentManifest` and places
the agent on two axes:

* **Autonomy** (L0..L5) - how much the agent decides and acts without a human in
  the loop, from "suggest only" to "fully autonomous".
* **Blast radius** (BR1..BR5) - the worst-case reach of its actions, from
  "reads public data only" to "destructive / systemic".

The two axes combine through a fixed matrix into a **governance intensity**
(LOW / MODERATE / HIGH / CRITICAL), which ``regmap.py`` maps to named regulatory
controls.

The enums, the matrix, and :func:`intensity_for` are the contract everything else
cites. :func:`classify_manifest` (ticket P1-02) derives the two axis positions
and per-tool evidence *from a manifest*, resolves the intensity via the matrix,
and attaches the regulatory control rows for that intensity by calling
``regmap.py``.

Import discipline: ``regmap`` imports from ``classify`` (for
:class:`GovernanceIntensity`), so ``classify`` must not import ``regmap`` at
module load. :func:`classify_manifest` imports ``controls_for_intensity`` lazily
inside the function body, which keeps the import graph acyclic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from cage.manifest import AgentManifest, Tool, ToolKind

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from cage.regmap import ControlRow


class AutonomyLevel(IntEnum):
    """How much the agent acts without a human in the loop."""

    L0 = 0  # No autonomy: human performs every action; agent only informs.
    L1 = 1  # Suggests: proposes actions, human executes.
    L2 = 2  # Acts with per-step approval (human-in-the-loop on each action).
    L3 = 3  # Acts within guardrails, human monitors (human-on-the-loop).
    L4 = 4  # Acts independently; human handles exceptions only.
    L5 = 5  # Fully autonomous; no routine human involvement.


class BlastRadius(IntEnum):
    """Worst-case reach of the agent's actions."""

    BR1 = 1  # Reads public data only.
    BR2 = 2  # Reads sensitive / confidential data.
    BR3 = 3  # Reversible writes to non-critical systems.
    BR4 = 4  # Irreversible writes or data egress.
    BR5 = 5  # Destructive or systemic effect.


class GovernanceIntensity(IntEnum):
    """Required governance intensity, the output of the two-axis matrix."""

    LOW = 1
    MODERATE = 2
    HIGH = 3
    CRITICAL = 4


# The two-axis matrix: (autonomy, blast-radius) -> intensity.
# Monotonic non-decreasing along both axes. This is the single authoritative
# mapping; SPEC.md renders it as a table and tests assert completeness.
_MATRIX_ROWS: dict[AutonomyLevel, tuple[GovernanceIntensity, ...]] = {
    #                 BR1        BR2        BR3        BR4        BR5
    AutonomyLevel.L0: (GovernanceIntensity.LOW, GovernanceIntensity.LOW,
                       GovernanceIntensity.MODERATE, GovernanceIntensity.MODERATE,
                       GovernanceIntensity.HIGH),
    AutonomyLevel.L1: (GovernanceIntensity.LOW, GovernanceIntensity.LOW,
                       GovernanceIntensity.MODERATE, GovernanceIntensity.HIGH,
                       GovernanceIntensity.HIGH),
    AutonomyLevel.L2: (GovernanceIntensity.LOW, GovernanceIntensity.MODERATE,
                       GovernanceIntensity.MODERATE, GovernanceIntensity.HIGH,
                       GovernanceIntensity.CRITICAL),
    AutonomyLevel.L3: (GovernanceIntensity.MODERATE, GovernanceIntensity.MODERATE,
                       GovernanceIntensity.HIGH, GovernanceIntensity.HIGH,
                       GovernanceIntensity.CRITICAL),
    AutonomyLevel.L4: (GovernanceIntensity.MODERATE, GovernanceIntensity.HIGH,
                       GovernanceIntensity.HIGH, GovernanceIntensity.CRITICAL,
                       GovernanceIntensity.CRITICAL),
    AutonomyLevel.L5: (GovernanceIntensity.HIGH, GovernanceIntensity.HIGH,
                       GovernanceIntensity.CRITICAL, GovernanceIntensity.CRITICAL,
                       GovernanceIntensity.CRITICAL),
}

# Flattened (autonomy, blast) -> intensity for direct lookup.
MATRIX: dict[tuple[AutonomyLevel, BlastRadius], GovernanceIntensity] = {
    (autonomy, blast): row[blast.value - 1]
    for autonomy, row in _MATRIX_ROWS.items()
    for blast in BlastRadius
}


def intensity_for(autonomy: AutonomyLevel, blast: BlastRadius) -> GovernanceIntensity:
    """Return the governance intensity for an (autonomy, blast-radius) pair."""
    return MATRIX[(autonomy, blast)]


@dataclass(frozen=True)
class ToolEvidence:
    """Why a single tool pushed the classification where it did."""

    tool: str
    kind: str  # ToolKind value
    blast_contribution: BlastRadius
    reversible: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["blast_contribution"] = int(self.blast_contribution)
        return d


@dataclass
class Classification:
    """The classifier's verdict on one agent manifest.

    The six primary fields (``agent_name`` .. ``rationale``) are the SPEC
    interface. ``controls`` is the cumulative set of regulatory control rows that
    attach to the resolved ``intensity`` - populated by :func:`classify_manifest`
    via ``regmap.controls_for_intensity`` and defaulting to empty when a
    ``Classification`` is built by hand.
    """

    agent_name: str
    autonomy: AutonomyLevel
    blast: BlastRadius
    intensity: GovernanceIntensity
    evidence: list[ToolEvidence] = field(default_factory=list)
    rationale: str = ""
    controls: list[ControlRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "autonomy": self.autonomy.name,
            "blast": self.blast.name,
            "intensity": self.intensity.name,
            "evidence": [e.to_dict() for e in self.evidence],
            "rationale": self.rationale,
            "controls": [c.to_dict() for c in self.controls],
        }


# --- Blast-radius inference -------------------------------------------------
# Each tool kind maps to a base blast contribution; reversibility escalates the
# acting kinds. The aggregate blast radius of an agent is the worst case across
# all its tools (a single destructive tool sets BR5 even amid read-only tools).


def _blast_for_tool(tool: Tool) -> tuple[BlastRadius, str]:
    """Return the blast-radius contribution of one tool and a short rationale."""
    kind = tool.kind
    if kind is ToolKind.READ_PUBLIC:
        return BlastRadius.BR1, "reads public data only"
    if kind is ToolKind.READ_SENSITIVE:
        return BlastRadius.BR2, "reads sensitive / confidential data"
    if kind is ToolKind.WRITE:
        if tool.reversible:
            return BlastRadius.BR3, "reversible write to a non-critical system"
        return BlastRadius.BR4, "irreversible write"
    if kind is ToolKind.EGRESS:
        # Data leaving the trust boundary cannot be recalled, so egress is BR4
        # whatever the reversibility flag claims.
        note = "moves data outside the trust boundary (egress cannot be recalled)"
        if tool.reversible:
            note += "; reversible flag ignored for egress"
        return BlastRadius.BR4, note
    if kind is ToolKind.DESTRUCTIVE:
        if tool.reversible:
            return BlastRadius.BR4, "destructive but recoverable action"
        return BlastRadius.BR5, "irreversible destructive / systemic action"
    if kind is ToolKind.CODE_EXEC:
        if tool.reversible:
            return BlastRadius.BR4, "sandboxed / reversible code execution"
        return BlastRadius.BR5, "arbitrary code execution (systemic reach)"
    # Unreachable while ToolKind is exhaustive; defensive for forward-compat.
    raise ValueError(f"unhandled tool kind: {kind!r}")  # pragma: no cover


# Kinds that let the agent *act* on the world (as opposed to read-only kinds).
# A read-only agent cannot be more autonomous than "suggests" (L1).
_ACTING_KINDS = frozenset(
    {ToolKind.WRITE, ToolKind.EGRESS, ToolKind.DESTRUCTIVE, ToolKind.CODE_EXEC}
)


# --- Autonomy inference -----------------------------------------------------
# A manifest carries no explicit autonomy field, so autonomy is inferred from
# oversight language in the agent name and its tool names/descriptions, bounded
# by whether the agent can act at all. Signals are ordered most- to
# least-autonomous; the highest-matching level wins (governance errs towards
# assuming more autonomy, hence more risk).
_AUTONOMY_SIGNALS: tuple[tuple[AutonomyLevel, tuple[str, ...]], ...] = (
    (AutonomyLevel.L5, (
        "fully autonomous", "without approval", "without human", "no human",
        "unattended", "no oversight", "without oversight",
    )),
    (AutonomyLevel.L4, (
        "independently", "exceptions only", "autonomously", "automatically",
        "automatic", "without confirmation",
    )),
    (AutonomyLevel.L3, (
        "within guardrails", "guardrails", "human monitors", "human-on-the-loop",
        "on-the-loop", "monitored", "under monitoring",
    )),
    (AutonomyLevel.L2, (
        "per-step approval", "requires approval", "human-in-the-loop",
        "human in the loop", "awaits approval", "with approval",
        "must be approved", "after approval", "confirmation required",
    )),
    (AutonomyLevel.L1, (
        "suggests", "suggest", "proposes", "propose", "recommends", "recommend",
        "drafts", "for review", "human executes",
    )),
    (AutonomyLevel.L0, (
        "inform", "notify", "read-only", "report only", "informational",
    )),
)


def _infer_autonomy(manifest: AgentManifest) -> tuple[AutonomyLevel, str]:
    """Infer the autonomy level from oversight language and capability class."""
    acts = any(t.kind in _ACTING_KINDS for t in manifest.tools)
    haystack = " ".join(
        [manifest.name]
        + [t.name for t in manifest.tools]
        + [t.description for t in manifest.tools]
    ).lower()

    matched: AutonomyLevel | None = None
    phrase = ""
    for level, signals in _AUTONOMY_SIGNALS:
        hit = next((s for s in signals if s in haystack), None)
        if hit is not None:
            matched = level
            phrase = hit
            break

    if matched is None:
        level = AutonomyLevel.L2 if acts else AutonomyLevel.L0
        note = (
            "no oversight language found; defaulted to L2 (acts with approval) "
            "because the agent has acting tools"
            if acts
            else "no oversight language found; read-only agent defaults to L0 (inform)"
        )
    else:
        level = matched
        note = f"oversight signal '{phrase}' -> {matched.name}"

    if not acts and level > AutonomyLevel.L1:
        note += f"; clamped from {level.name} to L1 (read-only agent cannot act)"
        level = AutonomyLevel.L1

    return level, note


def classify_manifest(manifest: AgentManifest) -> Classification:
    """Classify an agent manifest onto the two axes, with per-tool evidence.

    Derives the overall blast radius as the worst case across the manifest's
    tools (each :class:`~cage.manifest.ToolKind` and its reversibility maps to a
    blast-radius contribution), infers the autonomy level from oversight language
    bounded by the agent's capability class, resolves the intensity via
    :func:`intensity_for`, and attaches the cumulative regulatory controls for
    that intensity. Records a :class:`ToolEvidence` row per tool.

    Ticket: P1-02.
    """
    manifest.validate()

    # Lazy import keeps the module-load import graph acyclic (regmap -> classify).
    from cage.regmap import controls_for_intensity

    evidence: list[ToolEvidence] = []
    blast = BlastRadius.BR1
    driver = "no tools (reads nothing)"
    for tool in manifest.tools:
        contribution, note = _blast_for_tool(tool)
        evidence.append(
            ToolEvidence(
                tool=tool.name,
                kind=tool.kind.value,
                blast_contribution=contribution,
                reversible=tool.reversible,
                note=note,
            )
        )
        if contribution >= blast:
            blast = contribution
            driver = f"'{tool.name}' ({note} -> {contribution.name})"

    autonomy, autonomy_note = _infer_autonomy(manifest)
    intensity = intensity_for(autonomy, blast)
    controls = controls_for_intensity(intensity)

    rationale = (
        f"Autonomy {autonomy.name} ({autonomy_note}). "
        f"Blast {blast.name} driven by {driver}. "
        f"Intensity {intensity.name} per the autonomy x blast matrix; "
        f"{len(controls)} control(s) attach at {intensity.name}."
    )

    return Classification(
        agent_name=manifest.name,
        autonomy=autonomy,
        blast=blast,
        intensity=intensity,
        evidence=evidence,
        rationale=rationale,
        controls=controls,
    )
