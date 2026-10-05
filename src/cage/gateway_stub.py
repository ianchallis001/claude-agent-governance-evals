"""A minimal reference enforcement gateway for governed agents.

This is a *gateway*, not a server: it sits in front of an agent's tool calls and
decides, per call, whether to let the call through. It consumes a
:class:`~cage.classify.Classification` (the output of the classifier / the MCP
server) and enforces two things:

1. **Allow-list** - the tool must be one the operator has explicitly permitted
   for this agent. A call to any tool outside the allow-list is denied.
2. **Intensity-aware human-in-the-loop** - the higher the agent's resolved
   governance intensity, the stronger the gate. At or above the configured
   ``hitl_from`` intensity (default CRITICAL) a call must carry explicit human
   approval, reflecting EU AI Act Art. 14(4) stop-authority at the top of the
   scale.

The gateway is deliberately tiny and dependency-free (stdlib + ``cage``
foundations only). It is a reference for where real enforcement belongs - in a
proxy ahead of the tool, driven by the classification - not a production policy
engine. No network, no model calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cage.classify import Classification, GovernanceIntensity


class Decision(str, Enum):
    """The outcome of an authorization check."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class GatewayDecision:
    """A single allow/deny decision with an auditable reason.

    ``decision`` is the outcome; ``reason`` is a short, specific explanation;
    ``tool`` is the tool that was checked; ``intensity`` is the governing
    intensity of the agent at decision time; ``requires_human`` records whether
    the gate demanded human approval for this call.
    """

    decision: Decision
    reason: str
    tool: str
    intensity: GovernanceIntensity
    requires_human: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "tool": self.tool,
            "intensity": self.intensity.name,
            "requires_human": self.requires_human,
        }


class GovernanceGateway:
    """Allow/deny tool calls for one classified agent against an allow-list.

    Built from a :class:`~cage.classify.Classification` and the set of tool names
    the operator permits. ``hitl_from`` is the intensity at or above which a call
    must be human-approved (default :attr:`GovernanceIntensity.CRITICAL`);
    ``max_intensity`` optionally refuses to front an agent whose intensity exceeds
    a ceiling at all (every call denied).
    """

    def __init__(
        self,
        classification: Classification,
        allow_list: set[str] | frozenset[str] | list[str] | tuple[str, ...],
        *,
        hitl_from: GovernanceIntensity = GovernanceIntensity.CRITICAL,
        max_intensity: GovernanceIntensity | None = None,
    ) -> None:
        self.classification = classification
        self.allow_list: frozenset[str] = frozenset(allow_list)
        self.hitl_from = hitl_from
        self.max_intensity = max_intensity
        # The tools the classifier actually saw in the manifest, used to
        # distinguish "unknown tool" from "known but not permitted".
        self._known_tools: frozenset[str] = frozenset(
            e.tool for e in classification.evidence
        )
        self.audit_log: list[GatewayDecision] = []

    @property
    def intensity(self) -> GovernanceIntensity:
        return self.classification.intensity

    def authorize(self, tool: str, *, human_approved: bool = False) -> GatewayDecision:
        """Decide whether ``tool`` may be called; record the decision.

        Precedence (first match wins):

        1. ceiling breach (``max_intensity``) -> DENY;
        2. tool not permitted by the allow-list -> DENY;
        3. intensity at/above ``hitl_from`` without ``human_approved`` -> DENY;
        4. otherwise -> ALLOW.

        An unknown tool (one the manifest never declared) is also denied by step
        2, with a reason that says so.
        """
        intensity = self.intensity
        requires_human = intensity >= self.hitl_from

        if self.max_intensity is not None and intensity > self.max_intensity:
            return self._record(
                Decision.DENY,
                (
                    f"agent intensity {intensity.name} exceeds gateway ceiling "
                    f"{self.max_intensity.name}; no calls permitted"
                ),
                tool,
                intensity,
                requires_human,
            )

        if tool not in self.allow_list:
            if tool in self._known_tools:
                reason = f"tool '{tool}' is declared but not on the allow-list"
            else:
                reason = f"tool '{tool}' is unknown (not in the agent manifest)"
            return self._record(Decision.DENY, reason, tool, intensity, requires_human)

        if requires_human and not human_approved:
            return self._record(
                Decision.DENY,
                (
                    f"intensity {intensity.name} requires human approval "
                    f"(>= {self.hitl_from.name}); none provided"
                ),
                tool,
                intensity,
                requires_human,
            )

        approval = " with human approval" if requires_human else ""
        return self._record(
            Decision.ALLOW,
            f"tool '{tool}' permitted at intensity {intensity.name}{approval}",
            tool,
            intensity,
            requires_human,
        )

    def _record(
        self,
        decision: Decision,
        reason: str,
        tool: str,
        intensity: GovernanceIntensity,
        requires_human: bool,
    ) -> GatewayDecision:
        d = GatewayDecision(
            decision=decision,
            reason=reason,
            tool=tool,
            intensity=intensity,
            requires_human=requires_human,
        )
        self.audit_log.append(d)
        return d
