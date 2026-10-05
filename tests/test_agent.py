"""Tests for cage.agent - the classifier agent (P1-05)."""

from __future__ import annotations

import pytest

from cage.agent import ClassifierAgent
from cage.classify import AutonomyLevel, BlastRadius, GovernanceIntensity
from cage.mock import (
    MockAnthropicClient,
    text_response,
    tool_use_response,
)
from cage.models import AGENT_MODEL

# --- skeleton contract (unchanged) -----------------------------------------


def test_agent_accepts_injected_client(mock_client):
    agent = ClassifierAgent(client=mock_client)
    # Injected client is used verbatim; no lazy anthropic import triggered.
    assert agent.client is mock_client
    assert agent.model == AGENT_MODEL


def test_default_model_is_agent_model():
    agent = ClassifierAgent(client=object())
    assert agent.model == "claude-sonnet-5-5"


# --- the tool_use / tool_result cycle ---------------------------------------


def _submission(autonomy="L2", blast="BR5", intensity="HIGH"):
    return {
        "autonomy": autonomy,
        "blast": blast,
        "intensity": intensity,
        "rationale": "close_account is an irreversible destructive tool.",
        "evidence": [
            {
                "tool": "close_account",
                "blast_contribution": "BR5",
                "note": "permanent account closure",
            }
        ],
    }


def test_classify_drives_inspect_then_submit(sample_manifest):
    client = MockAnthropicClient(
        script=[
            tool_use_response(
                "inspect_tool", {"tool_name": "close_account"}, model=AGENT_MODEL,
                tool_id="toolu_inspect",
            ),
            tool_use_response(
                "submit_classification", _submission(), model=AGENT_MODEL,
                tool_id="toolu_submit",
            ),
        ]
    )
    agent = ClassifierAgent(client=client)
    result = agent.classify(sample_manifest)

    # Two model calls: one inspect round-trip, one submission.
    assert len(client.calls) == 2

    # The second call fed the inspect result back as a tool_result user turn.
    # (The mock records the live messages list by reference, so locate the
    # tool_result block rather than assuming its position.)
    second_messages = client.calls[1]["messages"]
    tool_results = [
        block
        for msg in second_messages
        if msg["role"] == "user" and isinstance(msg["content"], list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    tr = tool_results[0]
    assert tr["tool_use_id"] == "toolu_inspect"
    assert "destructive" in tr["content"]

    # The result reflects the submission.
    assert result.agent_name == sample_manifest.name
    assert result.autonomy is AutonomyLevel.L2
    assert result.blast is BlastRadius.BR5
    # The matrix is authoritative: L2 x BR5 -> CRITICAL, overriding the model's "HIGH".
    assert result.intensity is GovernanceIntensity.CRITICAL
    # Evidence is reconciled against the manifest (kind + reversibility looked up).
    assert len(result.evidence) == 1
    ev = result.evidence[0]
    assert ev.tool == "close_account"
    assert ev.kind == "destructive"
    assert ev.reversible is False
    assert ev.blast_contribution is BlastRadius.BR5


def test_classify_submits_without_inspecting(sample_manifest):
    client = MockAnthropicClient(
        script=[tool_use_response("submit_classification", _submission(), model=AGENT_MODEL)]
    )
    result = ClassifierAgent(client=client).classify(sample_manifest)
    assert len(client.calls) == 1
    assert result.intensity is GovernanceIntensity.CRITICAL


def test_from_transcript_drives_the_same_loop(sample_manifest):
    transcript = {
        "responses": [
            {
                "model": AGENT_MODEL,
                "stop_reason": "tool_use",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "submit_classification",
                        "id": "toolu_1",
                        "input": _submission(autonomy="L4", blast="BR4"),
                    }
                ],
            }
        ]
    }
    client = MockAnthropicClient.from_transcript(transcript)
    result = ClassifierAgent(client=client).classify(sample_manifest)
    assert result.autonomy is AutonomyLevel.L4
    assert result.blast is BlastRadius.BR4
    assert result.intensity is GovernanceIntensity.CRITICAL


# --- error paths ------------------------------------------------------------


def test_classify_raises_when_agent_never_calls_a_tool(sample_manifest):
    client = MockAnthropicClient(script=[text_response("I refuse to use a tool.", model=AGENT_MODEL)])
    with pytest.raises(ValueError, match="without calling submit_classification"):
        ClassifierAgent(client=client).classify(sample_manifest)


def test_classify_raises_on_invalid_axis(sample_manifest):
    bad = _submission(autonomy="L9")
    client = MockAnthropicClient(
        script=[tool_use_response("submit_classification", bad, model=AGENT_MODEL)]
    )
    with pytest.raises(ValueError, match="invalid or missing axis"):
        ClassifierAgent(client=client).classify(sample_manifest)


def test_classify_exceeds_max_turns(sample_manifest):
    # Always inspect, never submit -> the loop runs out of turns.
    def always_inspect(kwargs, client):
        return tool_use_response("inspect_tool", {"tool_name": "close_account"}, model=AGENT_MODEL)

    client = MockAnthropicClient(on_create=always_inspect)
    with pytest.raises(RuntimeError, match="did not submit"):
        ClassifierAgent(client=client).classify(sample_manifest, max_turns=3)
    assert len(client.calls) == 3


def test_classify_rejects_bad_max_turns(sample_manifest, mock_client):
    with pytest.raises(ValueError, match="max_turns"):
        ClassifierAgent(client=mock_client).classify(sample_manifest, max_turns=0)
