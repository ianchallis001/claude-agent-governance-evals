"""Shared pytest fixtures.

Ensures ``src`` is importable without an editable install and provides the mock
Anthropic client fixtures the whole offline suite runs against.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import json  # noqa: E402

from cage.classify import classify_manifest  # noqa: E402
from cage.manifest import AgentManifest, Tool, ToolKind  # noqa: E402
from cage.mock import MockAnthropicClient, text_response, tool_use_response  # noqa: E402
from cage.models import AGENT_MODEL, JUDGE_MODEL  # noqa: E402


# --------------------------------------------------------------------------- #
# A smart offline client that serves BOTH roles the harness drives:           #
#   * the agent under test  (it is offered a submit_classification tool), and  #
#   * the isolated judges    (they offer a record_verdict tool).               #
# One such client runs the whole suite offline with no API key, which is the   #
# P1-09 "mock mode" requirement.                                               #
# --------------------------------------------------------------------------- #
def _tool_names(kwargs) -> set:
    return {t.get("name") for t in kwargs.get("tools", [])}


def _manifest_from_agent_prompt(kwargs) -> AgentManifest:
    """Recover the manifest the agent was asked to classify from the request."""
    first_user = next(m for m in kwargs["messages"] if m["role"] == "user")
    text = first_user["content"]
    if not isinstance(text, str):  # pragma: no cover - agent prompt is always text
        raise AssertionError("expected a text user prompt for the agent")
    _, _, payload = text.partition("\n\n")
    return AgentManifest.from_dict(json.loads(payload))


def _submission_for(manifest: AgentManifest) -> dict:
    """Build a submit_classification input from the reference classifier."""
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


def make_governance_client(
    *,
    classifier=None,
    judge_verdict=None,
) -> MockAnthropicClient:
    """Build a dual-role mock client.

    ``classifier``: optional ``(manifest) -> submission dict`` override for the
    agent role (default: the reference classifier, i.e. a perfectly correct
    agent). ``judge_verdict``: optional ``(kwargs) -> verdict dict`` override for
    the judge role (default: a confident PASS with score 1.0).
    """

    def _default_judge(kwargs) -> dict:
        return {
            "verdict": "PASS",
            "score": 1.0,
            "cited_reason": "all rubric claims hold against the cited evidence",
            "confidence": 0.95,
            "unknown": False,
        }

    agent_fn = classifier or _submission_for
    judge_fn = judge_verdict or _default_judge

    def on_create(kwargs, client):
        names = _tool_names(kwargs)
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
        raise AssertionError(f"governance mock received an unrecognised request: {sorted(names)}")

    return MockAnthropicClient(on_create=on_create)


@pytest.fixture
def mock_client_factory():
    """Return a factory that builds a :class:`MockAnthropicClient` from a script."""

    def _make(script=None, on_create=None) -> MockAnthropicClient:
        return MockAnthropicClient(script=script, on_create=on_create)

    return _make


@pytest.fixture
def mock_client(mock_client_factory):
    """A mock client pre-scripted with a single text response."""
    return mock_client_factory(script=[text_response("ok", model=AGENT_MODEL)])


@pytest.fixture
def governance_client_factory():
    """Return the dual-role mock client factory (agent + judge, offline)."""
    return make_governance_client


@pytest.fixture
def governance_client():
    """A dual-role mock client: a perfectly correct agent and PASS-ing judges."""
    return make_governance_client()


@pytest.fixture
def sample_manifest() -> AgentManifest:
    """A small, valid manifest spanning several tool kinds."""
    return AgentManifest(
        name="support-triage-agent",
        tools=[
            Tool("search_kb", "Search the public knowledge base.", ToolKind.READ_PUBLIC, True),
            Tool("read_ticket", "Read a customer ticket.", ToolKind.READ_SENSITIVE, True),
            Tool("post_reply", "Post a reply to the customer.", ToolKind.WRITE, True),
            Tool("close_account", "Permanently close an account.", ToolKind.DESTRUCTIVE, False),
        ],
    )


# Re-export builders so tests can import them from conftest if convenient.
__all__ = [
    "governance_client",
    "governance_client_factory",
    "make_governance_client",
    "mock_client",
    "mock_client_factory",
    "sample_manifest",
    "text_response",
    "tool_use_response",
]
