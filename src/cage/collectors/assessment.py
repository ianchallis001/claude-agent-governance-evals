"""Generalised assessment-governance gate for synthesised collector output.

A collector run produces machine findings (``cage.collectors.findings``); a
*synthesiser* then turns those findings into narrative ratings / claims for a
client-facing assessment. That synthesis step is where an LLM can hallucinate a
rating that no finding supports, silently drift when its prompt template or model
changes, or fail to reproduce the same answer twice. This module is the offline,
stdlib-only gate over that step. It is a classifier / judge, not an enforcer: it
returns a PASS / FIX / BLOCK verdict a caller reasons about (ADR-0002), off any
critical path.

It rolls three structural checks into one verdict:

* **(a) Evidence anchoring.** Every synthesised rating must cite a resolvable
  evidence reference in the token form ``[ref: <pack>@<ver>/<finding_id>]`` that
  resolves to a finding in the loaded pack. An unanchored rating, or one whose
  ref does not resolve, is a structural hallucination control: it ``BLOCK``s and
  cannot be outvoted, mirroring the manifest-integrity understatement gate.
* **(b) Template-hash drift.** The reproducibility fingerprint
  (:func:`cage.models.model_fingerprint`) is extended to cover a SHA-256 of the
  prompt-template text alongside the model id + ``anthropic-version``. A template
  change moves the fingerprint and fails the run as a drift ``BLOCK`` exactly as a
  model-id change does. This reuses :mod:`cage.evals.drift`; it does not fork it.
* **(c) Reproducibility envelope.** A re-derivation under the same pinned config
  must match the baseline within tolerance. Non-reproducible synthesis cannot be
  confidently trusted, so it is a ``FIX`` (re-run / investigate), not a hard
  structural ``BLOCK``. The synthesis itself is mocked offline here - this module
  only models the interface and compares two derivations.

The :class:`AssessmentGate` aggregates the three with the SAME exit-code mapping
(PASS=0, FIX=1, BLOCK=2) and the SAME BLOCK > FIX > PASS precedence as the eval
runner (:mod:`cage.evals.runner`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from cage.collectors.findings import Finding
from cage.evals import drift as drift_mod
from cage.evals.drift import DriftResult
from cage.evals.judge import VerdictLabel
from cage.models import ANTHROPIC_VERSION

# Exit-code / precedence mapping, identical to cage.evals.runner so the gate
# verdicts aggregate and map to a process exit code the same way everywhere.
_SEVERITY = {VerdictLabel.PASS: 0, VerdictLabel.FIX: 1, VerdictLabel.BLOCK: 2}

# An evidence-reference token embedded in a synthesised rating:
#   [ref: <pack>@<ver>/<finding_id>]
# The inner text is captured and split structurally (first '@', then first '/')
# so a finding_id that itself contains ':' or '/' is preserved.
_REF_TOKEN = re.compile(r"\[ref:\s*([^\]]+?)\s*\]")


@dataclass(frozen=True)
class Rating:
    """One synthesised rating / claim awaiting governance.

    ``statement`` is the synthesised prose; any evidence references it cites are
    embedded in it as ``[ref: <pack>@<ver>/<finding_id>]`` tokens. ``value`` is an
    optional machine summary of the rating (a score, a band, an intensity) used
    only for the reproducibility comparison - two derivations of the same rating
    must agree on both ``statement`` and ``value``.
    """

    rating_id: str
    statement: str
    value: Any = None


@dataclass(frozen=True)
class EvidenceRef:
    """A parsed ``[ref: pack@ver/finding_id]`` token.

    ``well_formed`` is False when the token text did not split into three
    non-empty parts; a malformed token can never resolve and is treated as an
    unresolved citation.
    """

    raw: str
    pack: str = ""
    version: str = ""
    finding_id: str = ""
    well_formed: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.pack, self.version, self.finding_id)


@dataclass(frozen=True)
class ComponentVerdict:
    """One named sub-gate's contribution to the overall verdict."""

    name: str
    verdict: VerdictLabel
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.verdict is VerdictLabel.PASS


@dataclass(frozen=True)
class AnchoringResult:
    """The outcome of the evidence-anchoring check (a)."""

    passed: bool
    ratings_checked: int
    unanchored: list[str] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)

    @property
    def verdict(self) -> VerdictLabel:
        # Structural hallucination control: fail is a BLOCK, cannot be outvoted.
        return VerdictLabel.PASS if self.passed else VerdictLabel.BLOCK

    def as_component(self) -> ComponentVerdict:
        if self.passed:
            reason = ""
        else:
            parts = []
            if self.unanchored:
                parts.append(f"{len(self.unanchored)} unanchored")
            if self.unresolved:
                parts.append(f"{len(self.unresolved)} unresolved")
            reason = (
                "synthesised rating(s) without resolvable evidence: "
                + ", ".join(parts)
                + " (structural hallucination control; cannot be outvoted)"
            )
        return ComponentVerdict(
            name="evidence_anchoring",
            verdict=self.verdict,
            reason=reason,
            detail={
                "ratings_checked": self.ratings_checked,
                "unanchored": list(self.unanchored),
                "unresolved": list(self.unresolved),
            },
        )


@dataclass(frozen=True)
class ReproducibilityResult:
    """The outcome of the reproducibility-envelope check (c)."""

    within_envelope: bool
    change_rate: float
    changed_ids: list[str]
    tolerance: float
    compared: int

    @property
    def ok(self) -> bool:
        return self.within_envelope

    @property
    def verdict(self) -> VerdictLabel:
        # A non-reproducible re-derivation cannot be confidently trusted, but it
        # is not a hallucinated citation or a changed pin: it is a FIX, not BLOCK.
        return VerdictLabel.PASS if self.within_envelope else VerdictLabel.FIX

    def as_component(self) -> ComponentVerdict:
        reason = (
            ""
            if self.within_envelope
            else (
                f"re-derivation under the pinned config differs on {len(self.changed_ids)}"
                f"/{self.compared} rating(s) (change rate {self.change_rate:.3f} > "
                f"tolerance {self.tolerance:.3f}); re-run / investigate before trusting"
            )
        )
        return ComponentVerdict(
            name="reproducibility",
            verdict=self.verdict,
            reason=reason,
            detail={
                "change_rate": self.change_rate,
                "changed_ids": list(self.changed_ids),
                "tolerance": self.tolerance,
                "compared": self.compared,
            },
        )


@dataclass(frozen=True)
class AssessmentResult:
    """Aggregate PASS / FIX / BLOCK outcome of an :class:`AssessmentGate` run."""

    gate: VerdictLabel
    components: list[ComponentVerdict] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        """Process exit code implied by the gate: PASS=0, FIX=1, BLOCK=2."""
        return _SEVERITY[self.gate]

    @property
    def ok(self) -> bool:
        return self.gate is VerdictLabel.PASS

    @property
    def block_reasons(self) -> list[str]:
        return [c.name for c in self.components if c.verdict is VerdictLabel.BLOCK]


# --- (b) template-hash drift: extend the pin, reuse cage.evals.drift --------


def template_hash(template_text: str) -> str:
    """Return the SHA-256 hex digest of a prompt-template's text.

    Pure and deterministic; no I/O. This digest is folded into the model
    fingerprint so a prompt-template edit is caught as drift.
    """
    if not isinstance(template_text, str):
        raise TypeError(f"template_text must be a string, got {type(template_text).__name__}")
    return hashlib.sha256(template_text.encode("utf-8")).hexdigest()


def pin_assessment_fingerprint(
    model: str,
    template_text: str,
    anthropic_version: str = ANTHROPIC_VERSION,
    **extra: object,
) -> str:
    """Pin a fingerprint over the model id, API version AND the template hash.

    A thin extension of :func:`cage.evals.drift.pin_fingerprint`: the template
    hash is added as an ``extra`` run-shaping field, so the existing drift
    machinery fingerprints and compares it with no fork. Store the returned value
    in the run manifest; :func:`check_template_drift` fails the run if a fresh
    fingerprint differs.
    """
    return drift_mod.pin_fingerprint(
        model, anthropic_version, template_sha256=template_hash(template_text), **extra
    )


def check_template_drift(
    model: str,
    template_text: str,
    pinned: str,
    anthropic_version: str = ANTHROPIC_VERSION,
    **extra: object,
) -> DriftResult:
    """Re-fingerprint model + API version + template hash and compare to ``pinned``.

    Returns a :class:`cage.evals.drift.DriftResult`; ``drifted=True`` must fail the
    run. A prompt-template edit moves ``template_sha256`` and therefore the
    fingerprint, so it is caught exactly as a model-id or API-version change is.
    """
    return drift_mod.check_drift(
        model, pinned, anthropic_version, template_sha256=template_hash(template_text), **extra
    )


# --- (a) evidence anchoring --------------------------------------------------


def parse_refs(statement: str) -> list[EvidenceRef]:
    """Extract and structurally parse every ``[ref: ...]`` token in ``statement``.

    Each token's inner text is split on the first ``@`` (pack vs rest) then the
    first ``/`` (version vs finding_id), so a finding_id containing ``:`` or ``/``
    survives. A token that does not yield three non-empty parts is returned with
    ``well_formed=False`` and can never resolve.
    """
    refs: list[EvidenceRef] = []
    for match in _REF_TOKEN.finditer(statement):
        inner = match.group(1).strip()
        pack, sep_at, rest = inner.partition("@")
        version, sep_slash, finding_id = rest.partition("/")
        well_formed = bool(sep_at and sep_slash and pack and version and finding_id)
        refs.append(
            EvidenceRef(
                raw=inner,
                pack=pack if well_formed else "",
                version=version if well_formed else "",
                finding_id=finding_id if well_formed else "",
                well_formed=well_formed,
            )
        )
    return refs


def _finding_index(findings: Iterable[Finding]) -> set[tuple[str, str, str]]:
    """Build the resolvable (pack, version, finding_id) key set for a pack."""
    return {(f.pack.name, f.pack.version, f.finding_id) for f in findings}


def check_anchoring(
    ratings: Iterable[Rating], findings: Iterable[Finding]
) -> AnchoringResult:
    """Check that every rating cites a resolvable evidence reference.

    A rating with no ``[ref: ...]`` token is *unanchored*; a rating whose every
    cited ref fails to resolve to a finding in the pack is *unresolved*. Either is
    a structural hallucination defect: :attr:`AnchoringResult.passed` is False and
    the component verdict is ``BLOCK``. A rating that carries at least one resolving
    ref passes, even if it also carries a non-resolving one (the resolvable
    citation anchors the claim).
    """
    index = _finding_index(findings)
    unanchored: list[str] = []
    unresolved: list[dict[str, str]] = []
    checked = 0
    for rating in ratings:
        checked += 1
        refs = parse_refs(rating.statement)
        if not refs:
            unanchored.append(rating.rating_id)
            continue
        if not any(ref.well_formed and ref.key in index for ref in refs):
            # No cited ref resolves - record each offending token for the report.
            for ref in refs:
                unresolved.append({"rating_id": rating.rating_id, "ref": ref.raw})
    passed = not unanchored and not unresolved
    return AnchoringResult(
        passed=passed,
        ratings_checked=checked,
        unanchored=unanchored,
        unresolved=unresolved,
    )


# --- (c) reproducibility envelope -------------------------------------------


def ratings_decisions(ratings: Iterable[Rating]) -> dict[str, Any]:
    """Project ratings to the per-rating decision map the envelope compares.

    A rating's reproducible content is its ``statement`` plus its machine
    ``value``; two derivations match iff both agree for every rating id.
    """
    return {r.rating_id: {"statement": r.statement, "value": r.value} for r in ratings}


def check_reproducibility(
    baseline: Iterable[Rating],
    rederived: Iterable[Rating],
    tolerance: float = 0.0,
) -> ReproducibilityResult:
    """Compare a re-derivation against the baseline within ``tolerance``.

    Reuses :func:`cage.evals.drift.check_behavioural_drift` over the per-rating
    decision maps: the fraction of ratings whose ``statement``/``value`` changed
    (or that appear in only one derivation) must not exceed ``tolerance``. The
    synthesis itself is mocked offline - this only models the compare interface.
    """
    bdr = drift_mod.check_behavioural_drift(
        ratings_decisions(baseline), ratings_decisions(rederived), tolerance
    )
    return ReproducibilityResult(
        within_envelope=bdr.ok,
        change_rate=bdr.change_rate,
        changed_ids=bdr.changed_ids,
        tolerance=tolerance,
        compared=bdr.total,
    )


# --- aggregation -------------------------------------------------------------


def aggregate_verdict(components: Iterable[ComponentVerdict]) -> VerdictLabel:
    """Roll component verdicts into one, most-severe-wins (BLOCK > FIX > PASS).

    A single BLOCK cannot be outvoted by any number of PASSes, exactly as the
    eval runner aggregates its per-dimension verdicts.
    """
    gate = VerdictLabel.PASS
    for comp in components:
        if _SEVERITY[comp.verdict] > _SEVERITY[gate]:
            gate = comp.verdict
    return gate


@dataclass(frozen=True)
class AssessmentConfig:
    """The pinned synthesis configuration a run is validated against.

    ``extra`` carries any further run-shaping fields (sampling parameters, a
    tool-schema hash) that should also be fingerprinted, matching the ``extra``
    contract of :func:`cage.models.model_fingerprint`.
    """

    model: str
    template_text: str
    anthropic_version: str = ANTHROPIC_VERSION
    extra: dict[str, object] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """The pin for this config (model + API version + template hash + extra)."""
        return pin_assessment_fingerprint(
            self.model, self.template_text, self.anthropic_version, **self.extra
        )


class AssessmentGate:
    """Roll evidence-anchoring (a), template-hash drift (b) and the
    reproducibility envelope (c) into one PASS / FIX / BLOCK verdict.

    The gate is constructed with the *current* synthesis config and the
    ``pinned_fingerprint`` the assessment was validated against, plus the findings
    pack the ratings must anchor to. :meth:`evaluate` checks drift first (a changed
    model or template fails fast), then anchoring, then - if a re-derivation is
    supplied - the reproducibility envelope, and aggregates with the runner's
    BLOCK > FIX > PASS precedence and 0/1/2 exit-code mapping.

    It classifies and judges; it does not enforce (ADR-0002).
    """

    def __init__(
        self,
        config: AssessmentConfig,
        pinned_fingerprint: str,
        findings: Iterable[Finding],
        *,
        reproducibility_tolerance: float = drift_mod.DEFAULT_BEHAVIOURAL_TOLERANCE,
    ) -> None:
        self.config = config
        self.pinned_fingerprint = pinned_fingerprint
        self.findings: list[Finding] = list(findings)
        self.reproducibility_tolerance = reproducibility_tolerance

    def check_drift(self) -> DriftResult:
        """(b) Re-fingerprint the current config and compare to the pin."""
        return check_template_drift(
            self.config.model,
            self.config.template_text,
            self.pinned_fingerprint,
            self.config.anthropic_version,
            **self.config.extra,
        )

    def evaluate(
        self,
        ratings: Iterable[Rating],
        rederived: Iterable[Rating] | None = None,
    ) -> AssessmentResult:
        """Evaluate ``ratings`` and return the aggregate :class:`AssessmentResult`.

        ``rederived`` is an optional second derivation of the same ratings under
        the same pinned config; when supplied, the reproducibility envelope (c) is
        checked against it. When omitted, reproducibility is not asserted and does
        not contribute a verdict.
        """
        ratings = list(ratings)
        components: list[ComponentVerdict] = []
        detail: dict[str, Any] = {}

        # (b) template-hash / model drift - the same BLOCK as a model-id change.
        drift_result = self.check_drift()
        detail["drift"] = {
            "pinned": drift_result.pinned_fingerprint,
            "current": drift_result.current_fingerprint,
            "drifted": drift_result.drifted,
            "reason": drift_result.reason,
        }
        components.append(
            ComponentVerdict(
                name="template_drift",
                verdict=VerdictLabel.BLOCK if drift_result.drifted else VerdictLabel.PASS,
                reason=drift_result.reason,
                detail=detail["drift"],
            )
        )

        # (a) evidence anchoring - structural hallucination control, cannot BLOCK.
        anchoring = check_anchoring(ratings, self.findings)
        anchor_component = anchoring.as_component()
        detail["anchoring"] = anchor_component.detail
        components.append(anchor_component)

        # (c) reproducibility envelope - FIX when a re-derivation is supplied.
        if rederived is not None:
            repro = check_reproducibility(
                ratings, rederived, self.reproducibility_tolerance
            )
            repro_component = repro.as_component()
            detail["reproducibility"] = repro_component.detail
            components.append(repro_component)

        gate = aggregate_verdict(components)
        detail["components"] = {c.name: c.verdict.value for c in components}
        return AssessmentResult(gate=gate, components=components, detail=detail)


def evaluate_assessment(
    config: AssessmentConfig,
    pinned_fingerprint: str,
    findings: Iterable[Finding],
    ratings: Iterable[Rating],
    rederived: Iterable[Rating] | None = None,
    *,
    reproducibility_tolerance: float = drift_mod.DEFAULT_BEHAVIOURAL_TOLERANCE,
) -> AssessmentResult:
    """One-shot convenience over :class:`AssessmentGate` for a single run."""
    gate = AssessmentGate(
        config,
        pinned_fingerprint,
        findings,
        reproducibility_tolerance=reproducibility_tolerance,
    )
    return gate.evaluate(ratings, rederived)
