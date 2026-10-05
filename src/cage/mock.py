"""Offline scripted / replay Anthropic client.

The whole suite must run in CI with no API key, so :class:`MockAnthropicClient`
stands in for ``anthropic.Anthropic``. It replays a pre-scripted list of
responses in order, exposing just enough of the real surface
(``client.messages.create(...)`` returning an object with ``.content``,
``.stop_reason``, ``.model``, ``.id``, and ``.usage``) for the agent loop and the
judges to run unchanged.

Design rule: *a mock failure must localise to the harness, not the model.* If the
script is exhausted or a call does not match an expectation, the mock raises
:class:`MockError` (a harness fault) - it never fabricates a model-shaped error
that a scorer might mistake for agent behaviour.

The base client and response builders are delivered by the architecture skeleton
so every later ticket has working offline infrastructure. Ticket P1-04 adds
record/replay from captured real-API transcripts and the failure-localisation
test matrix.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class MockError(RuntimeError):
    """A fault in the mock harness itself (never a simulated model error)."""


@dataclass
class TextBlock:
    """A text content block, shaped like the SDK's text block."""

    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    """A tool-use content block, shaped like the SDK's tool_use block."""

    name: str
    input: dict[str, Any]
    id: str = "toolu_mock"
    type: str = "tool_use"


@dataclass
class Usage:
    """Token usage, shaped like the SDK's usage object."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class MockMessage:
    """A response object shaped like ``anthropic.types.Message``."""

    content: list[Any]
    model: str
    stop_reason: str = "end_turn"
    id: str = "msg_mock"
    role: str = "assistant"
    usage: Usage = field(default_factory=Usage)


def text_response(text: str, model: str, stop_reason: str = "end_turn") -> MockMessage:
    """Build a text-only mock response."""
    return MockMessage(content=[TextBlock(text=text)], model=model, stop_reason=stop_reason)


def tool_use_response(
    name: str,
    tool_input: dict[str, Any],
    model: str,
    tool_id: str = "toolu_mock",
) -> MockMessage:
    """Build a mock response that calls one tool (``stop_reason='tool_use'``)."""
    return MockMessage(
        content=[ToolUseBlock(name=name, input=tool_input, id=tool_id)],
        model=model,
        stop_reason="tool_use",
    )


def message_from_dict(data: Mapping[str, Any]) -> MockMessage:
    """Rebuild a :class:`MockMessage` from a captured-response mapping.

    Content blocks are rebuilt by ``type``: ``text`` -> :class:`TextBlock`,
    ``tool_use`` -> :class:`ToolUseBlock`. A malformed transcript is a harness
    fault and raises :class:`MockError` - never a model-shaped error.
    """
    if not isinstance(data, Mapping):
        raise MockError(f"transcript entry must be an object, got {type(data).__name__}")
    model = data.get("model")
    if not model:
        raise MockError("transcript entry is missing a 'model'")

    raw_blocks = data.get("content", [])
    if not isinstance(raw_blocks, Sequence) or isinstance(raw_blocks, (str, bytes)):
        raise MockError("transcript entry 'content' must be a list of blocks")

    blocks: list[Any] = []
    for raw in raw_blocks:
        if not isinstance(raw, Mapping):
            raise MockError("transcript content block must be an object")
        btype = raw.get("type")
        if btype == "text":
            blocks.append(TextBlock(text=raw.get("text", "")))
        elif btype == "tool_use":
            name = raw.get("name")
            if not name:
                raise MockError("transcript tool_use block is missing a 'name'")
            tool_input = raw.get("input", {})
            if not isinstance(tool_input, Mapping):
                raise MockError("transcript tool_use 'input' must be an object")
            blocks.append(
                ToolUseBlock(
                    name=name,
                    input=dict(tool_input),
                    id=raw.get("id", "toolu_mock"),
                )
            )
        else:
            raise MockError(f"unsupported transcript block type: {btype!r}")

    return MockMessage(
        content=blocks,
        model=model,
        stop_reason=data.get("stop_reason", "end_turn"),
        id=data.get("id", "msg_mock"),
        usage=Usage(
            input_tokens=int(data.get("usage", {}).get("input_tokens", 0)),
            output_tokens=int(data.get("usage", {}).get("output_tokens", 0)),
        ),
    )


def script_from_transcript(data: Any) -> list[MockMessage]:
    """Build a replay script from a captured transcript.

    Accepts either a bare list of response objects or a ``{"responses": [...]}``
    envelope. Raises :class:`MockError` on a shape it cannot replay.
    """
    if isinstance(data, Mapping):
        data = data.get("responses", data.get("script"))
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise MockError("transcript must be a list of responses or a {'responses': [...]} object")
    return [message_from_dict(entry) for entry in data]


def load_transcript(path: str | Path) -> list[MockMessage]:
    """Load a JSON transcript file and build a replay script from it."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MockError(f"transcript {path!s} is not valid JSON: {exc}") from exc
    return script_from_transcript(data)


class _Messages:
    """The ``client.messages`` namespace."""

    def __init__(self, owner: MockAnthropicClient) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> MockMessage:
        """Return the next scripted response, recording the call.

        Raises :class:`MockError` if the script is exhausted - a harness fault,
        surfaced distinctly from any model-level condition.
        """
        owner = self._owner
        owner.calls.append(kwargs)
        if owner.on_create is not None:
            return owner.on_create(kwargs, owner)
        if owner._cursor >= len(owner._script):
            raise MockError(
                f"mock script exhausted after {owner._cursor} response(s); "
                f"call #{len(owner.calls)} has no scripted reply"
            )
        response = owner._script[owner._cursor]
        owner._cursor += 1
        return response


class MockAnthropicClient:
    """A drop-in, offline replacement for ``anthropic.Anthropic``.

    Parameters
    ----------
    script:
        An ordered iterable of :class:`MockMessage` responses to replay.
    on_create:
        Optional callable ``(kwargs, client) -> MockMessage`` for dynamic replies
        (e.g. branching on the request). Overrides ``script`` when set.
    """

    def __init__(
        self,
        script: Iterable[MockMessage] | None = None,
        on_create: Callable[[dict[str, Any], MockAnthropicClient], MockMessage] | None = None,
    ) -> None:
        self._script: list[MockMessage] = list(script or [])
        self._cursor = 0
        self.on_create = on_create
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(self)

    @classmethod
    def from_transcript(cls, source: str | Path | Any) -> MockAnthropicClient:
        """Build a replay client from a captured transcript.

        ``source`` may be a path to a JSON transcript file, or an already-parsed
        transcript (a list of responses or a ``{"responses": [...]}`` object).
        """
        if isinstance(source, (str, Path)):
            script = load_transcript(source)
        else:
            script = script_from_transcript(source)
        return cls(script=script)

    @property
    def remaining(self) -> int:
        """Number of scripted responses not yet consumed."""
        return max(0, len(self._script) - self._cursor)

    def reset(self) -> None:
        """Rewind the script cursor and clear the recorded call log."""
        self._cursor = 0
        self.calls.clear()


# --------------------------------------------------------------------------- #
# Dual-role governance mock                                                    #
# --------------------------------------------------------------------------- #
# The offline client the CLI's --mock mode (and the eval harness) drive. It
# serves BOTH roles the harness calls:
#   * the agent under test  - offered a submit_classification tool, and
#   * the isolated judges    - offered a record_verdict tool.
# One such client runs the whole flow with no API key and no network. It is
# stdlib-only: the lazy imports below are intra-package (no third-party SDK).


def _governance_tool_names(kwargs: Mapping[str, Any]) -> set:
    return {t.get("name") for t in kwargs.get("tools", [])}


def _manifest_from_agent_prompt(kwargs: Mapping[str, Any]) -> Any:
    """Recover the manifest the agent was asked to classify from the request."""
    from cage.manifest import AgentManifest  # intra-package lazy import

    messages = kwargs.get("messages", [])
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    if first_user is None:
        raise MockError("governance mock: agent request carried no user message")
    text = first_user.get("content")
    if not isinstance(text, str):
        raise MockError("governance mock: expected a text user prompt for the agent")
    _, _, payload = text.partition("\n\n")
    try:
        return AgentManifest.from_dict(json.loads(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise MockError(f"governance mock: could not parse the manifest prompt: {exc}") from exc


def _reference_submission(manifest: Any) -> dict[str, Any]:
    """Build a submit_classification input from the reference classifier."""
    from cage.classify import classify_manifest  # intra-package lazy import

    result = classify_manifest(manifest)
    return {
        "autonomy": result.autonomy.name,
        "blast": result.blast.name,
        "intensity": result.intensity.name,
        "rationale": result.rationale,
        "evidence": [
            {
                "tool": ev.tool,
                "blast_contribution": ev.blast_contribution.name,
                "note": ev.note,
            }
            for ev in result.evidence
        ],
    }


def governance_mock_client(
    *,
    classifier: Callable[[Any], dict[str, Any]] | None = None,
    judge_verdict: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> MockAnthropicClient:
    """Build a dual-role offline mock client (agent + judge).

    ``classifier``: optional ``(manifest) -> submission dict`` override for the
    agent role. The default replays the reference classifier
    (:func:`cage.classify.classify_manifest`), i.e. a perfectly correct agent.
    ``judge_verdict``: optional ``(kwargs) -> verdict dict`` override for the
    judge role; the default is a confident PASS with score 1.0.

    This is the client the ``cage`` CLI uses for ``--mock`` runs.
    """
    from cage.models import AGENT_MODEL, JUDGE_MODEL  # intra-package lazy import

    def _default_judge(_kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "verdict": "PASS",
            "score": 1.0,
            "cited_reason": "all rubric claims hold against the cited evidence",
            "confidence": 0.95,
            "unknown": False,
        }

    agent_fn = classifier or _reference_submission
    judge_fn = judge_verdict or _default_judge

    def on_create(kwargs: dict[str, Any], _client: MockAnthropicClient) -> MockMessage:
        names = _governance_tool_names(kwargs)
        if "submit_classification" in names:
            manifest = _manifest_from_agent_prompt(kwargs)
            return tool_use_response(
                "submit_classification", agent_fn(manifest), model=AGENT_MODEL,
                tool_id="toolu_submit",
            )
        if "record_verdict" in names:
            return tool_use_response(
                "record_verdict", judge_fn(kwargs), model=JUDGE_MODEL,
                tool_id="toolu_verdict",
            )
        raise MockError(f"governance mock received an unrecognised request: {sorted(names)}")

    return MockAnthropicClient(on_create=on_create)
