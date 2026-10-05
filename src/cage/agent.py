"""The governance classifier agent, on the raw Anthropic Messages API.

The agent is given an agent/tool manifest and asked to classify it onto the two
axes (autonomy x blast-radius). It runs a tool_use / tool_result loop: the model
may call a read-only ``inspect_tool`` to examine a specific tool, and finishes by
calling ``submit_classification``. The loop feeds tool results back until a final
:class:`~cage.classify.Classification` is produced.

The ``anthropic`` SDK is imported lazily, only when a real client is needed, so
the mock suite and all unit tests run with pytest as the only third-party
dependency. An injected client (e.g. :class:`~cage.mock.MockAnthropicClient`)
bypasses the live import entirely.

Ticket: P1-05.
"""

from __future__ import annotations

import json
from typing import Any

from cage.classify import (
    AutonomyLevel,
    BlastRadius,
    Classification,
    ToolEvidence,
    intensity_for,
)
from cage.manifest import AgentManifest
from cage.models import AGENT_MODEL

# Response ceiling for a classification turn; a classification is small, so this
# is generous. The mock ignores it; a live client enforces it.
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You are a governance classifier. Given an agent's tool manifest, place the "
    "agent on two axes and resolve its governance intensity.\n\n"
    "Autonomy (how much it acts without a human):\n"
    "  L0 inform only; L1 suggests, human acts; L2 acts with per-step approval; "
    "L3 acts within guardrails, human monitors; L4 acts independently, exceptions "
    "only; L5 fully autonomous.\n"
    "Blast radius (worst-case reach of its actions):\n"
    "  BR1 reads public data; BR2 reads sensitive data; BR3 reversible writes; "
    "BR4 irreversible writes or egress; BR5 destructive or systemic.\n\n"
    "The agent's blast radius is the worst case across its tools. Use inspect_tool "
    "to examine any tool you are unsure about, then call submit_classification "
    "exactly once with your verdict and per-tool evidence."
)

# The read-only inspection tool. Lets the model pull one tool's governance
# metadata out of the manifest before deciding.
_INSPECT_TOOL: dict[str, Any] = {
    "name": "inspect_tool",
    "description": "Return the kind, reversibility and description of one named tool.",
    "input_schema": {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
        },
        "required": ["tool_name"],
        "additionalProperties": False,
    },
}

# The terminal tool. Strict schema: every property is required and no extras are
# allowed, so a valid tool_use input maps cleanly onto a Classification.
_SUBMIT_TOOL: dict[str, Any] = {
    "name": "submit_classification",
    "description": "Record the final two-axis classification for the agent.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "autonomy": {
                "type": "string",
                "enum": [a.name for a in AutonomyLevel],
            },
            "blast": {
                "type": "string",
                "enum": [b.name for b in BlastRadius],
            },
            "intensity": {
                "type": "string",
                "enum": ["LOW", "MODERATE", "HIGH", "CRITICAL"],
            },
            "rationale": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "blast_contribution": {
                            "type": "string",
                            "enum": [b.name for b in BlastRadius],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["tool", "blast_contribution", "note"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["autonomy", "blast", "intensity", "rationale", "evidence"],
        "additionalProperties": False,
    },
}

_TOOLS: list[dict[str, Any]] = [_INSPECT_TOOL, _SUBMIT_TOOL]


def _load_anthropic() -> Any:
    """Import and return the ``anthropic`` module, lazily.

    Kept out of module scope so importing :mod:`cage.agent` never requires the
    SDK. Raises a clear error if the optional dependency is absent.
    """
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "the 'anthropic' package is required for live runs; "
            "install it, or inject a client (e.g. cage.mock.MockAnthropicClient) "
            "for offline mode"
        ) from exc
    return anthropic


class ClassifierAgent:
    """Runs the classification agent against a client (live or mock)."""

    def __init__(self, client: Any | None = None, model: str = AGENT_MODEL) -> None:
        self.model = model
        self._client = client
        # The tool-call trajectory of the most recent classify() run, recorded
        # for the trajectory / tool-use scorer. Each entry is
        # {"tool": name, "input": {...}, "id": id}. Reset at the start of every
        # classify() call so a reused agent never leaks a prior run's trajectory.
        self.last_trajectory: list[dict[str, Any]] = []

    @property
    def client(self) -> Any:
        """The Anthropic client, lazily constructing a live one if none injected."""
        if self._client is None:
            anthropic = _load_anthropic()
            self._client = anthropic.Anthropic()
        return self._client

    def classify(self, manifest: AgentManifest, max_turns: int = 8) -> Classification:
        """Classify ``manifest`` by driving the tool_use / tool_result loop.

        Ticket: P1-05. Builds the system prompt and tool schemas, calls
        ``client.messages.create`` in a loop handling ``stop_reason == 'tool_use'``,
        feeds ``inspect_tool`` results back, and parses the final
        ``submit_classification`` call into a :class:`Classification`.
        """
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")

        self.last_trajectory = []
        by_name = {t.name: t for t in manifest.tools}
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._user_prompt(manifest)}
        ]

        for _ in range(max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                tools=_TOOLS,
                messages=messages,
            )

            tool_uses = [
                b for b in response.content if getattr(b, "type", None) == "tool_use"
            ]
            if not tool_uses:
                raise ValueError(
                    "agent ended without calling submit_classification "
                    f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
                )

            # Echo the assistant turn back verbatim before answering its tools.
            messages.append({"role": "assistant", "content": response.content})

            tool_results: list[dict[str, Any]] = []
            for block in tool_uses:
                self.last_trajectory.append(
                    {
                        "tool": block.name,
                        "input": dict(block.input),
                        "id": getattr(block, "id", ""),
                    }
                )
                if block.name == "submit_classification":
                    # Terminal: parse and return. No need to send a result back.
                    return self._build_classification(manifest, block.input)
                if block.name == "inspect_tool":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": self._inspect(by_name, block.input),
                        }
                    )
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"unknown tool: {block.name}",
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            f"agent did not submit a classification within {max_turns} turn(s)"
        )

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _user_prompt(manifest: AgentManifest) -> str:
        return (
            "Classify this agent manifest:\n\n"
            + json.dumps(manifest.to_dict(), indent=2, sort_keys=True)
        )

    @staticmethod
    def _inspect(by_name: dict[str, Any], tool_input: dict[str, Any]) -> str:
        name = tool_input.get("tool_name")
        tool = by_name.get(name)
        if tool is None:
            return json.dumps({"error": f"no such tool: {name!r}"}, sort_keys=True)
        return json.dumps(
            {
                "name": tool.name,
                "kind": tool.kind.value,
                "reversible": tool.reversible,
                "description": tool.description,
            },
            sort_keys=True,
        )

    @staticmethod
    def _build_classification(
        manifest: AgentManifest, submission: dict[str, Any]
    ) -> Classification:
        try:
            autonomy = AutonomyLevel[submission["autonomy"]]
            blast = BlastRadius[submission["blast"]]
        except KeyError as exc:
            raise ValueError(
                f"submit_classification has invalid or missing axis: {exc.args[0]!r}"
            ) from exc

        # The matrix is authoritative: derive intensity from the axes rather than
        # trusting the model's self-reported intensity.
        intensity = intensity_for(autonomy, blast)

        by_name = {t.name: t for t in manifest.tools}
        evidence: list[ToolEvidence] = []
        for row in submission.get("evidence", []):
            tool_name = row.get("tool", "")
            tool = by_name.get(tool_name)
            try:
                contribution = BlastRadius[row["blast_contribution"]]
            except KeyError as exc:
                raise ValueError(
                    f"evidence row for {tool_name!r} has invalid blast_contribution: "
                    f"{row.get('blast_contribution')!r}"
                ) from exc
            evidence.append(
                ToolEvidence(
                    tool=tool_name,
                    kind=tool.kind.value if tool is not None else "unknown",
                    blast_contribution=contribution,
                    reversible=tool.reversible if tool is not None else True,
                    note=row.get("note", ""),
                )
            )

        return Classification(
            agent_name=manifest.name,
            autonomy=autonomy,
            blast=blast,
            intensity=intensity,
            evidence=evidence,
            rationale=submission.get("rationale", ""),
        )
