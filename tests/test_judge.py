"""Tests for cage.evals.judge."""

from __future__ import annotations

import pytest

from cage.evals.judge import (
    RECORD_VERDICT_TOOL_NAME,
    VERDICT_TOOL_SCHEMA,
    DimensionJudge,
    Verdict,
    VerdictLabel,
    build_judge_prompt,
    rubric_for,
    verdict_from_tool_input,
)
from cage.mock import MockAnthropicClient, text_response, tool_use_response
from cage.models import JUDGE_MODEL
from cage.regmap import Dimension


def test_verdict_labels():
    assert {v.value for v in VerdictLabel} == {"PASS", "FIX", "BLOCK"}


def test_tool_schema_shape():
    props = VERDICT_TOOL_SCHEMA["properties"]
    assert set(VERDICT_TOOL_SCHEMA["required"]) == {
        "verdict", "score", "cited_reason", "confidence", "unknown",
    }
    assert props["verdict"]["enum"] == ["PASS", "FIX", "BLOCK"]
    assert VERDICT_TOOL_SCHEMA["additionalProperties"] is False


def test_verdict_from_tool_input_valid():
    v = verdict_from_tool_input(
        Dimension.GROUNDEDNESS,
        {
            "verdict": "FIX",
            "score": 0.42,
            "cited_reason": "uncited claim about SLA",
            "confidence": 0.9,
            "unknown": False,
        },
    )
    assert isinstance(v, Verdict)
    assert v.verdict is VerdictLabel.FIX
    assert v.dimension is Dimension.GROUNDEDNESS
    assert v.to_dict()["verdict"] == "FIX"


@pytest.mark.parametrize("bad", [
    {"verdict": "MAYBE", "score": 0.5, "cited_reason": "x", "confidence": 0.5, "unknown": False},
    {"verdict": "PASS", "score": 1.5, "cited_reason": "x", "confidence": 0.5, "unknown": False},
    {"verdict": "PASS", "score": 0.5, "cited_reason": "x", "confidence": 2.0, "unknown": False},
    {"verdict": "PASS", "score": 0.5, "cited_reason": "x", "confidence": 0.5},  # missing unknown
])
def test_verdict_from_tool_input_rejects_bad(bad):
    with pytest.raises(ValueError):
        verdict_from_tool_input(Dimension.TASK_CORRECTNESS, bad)


def _verdict_response(verdict="PASS", score=1.0, confidence=0.9, unknown=False):
    return tool_use_response(
        RECORD_VERDICT_TOOL_NAME,
        {
            "verdict": verdict,
            "score": score,
            "cited_reason": "cited manifest evidence",
            "confidence": confidence,
            "unknown": unknown,
        },
        model=JUDGE_MODEL,
        tool_id="toolu_verdict",
    )


def test_rubric_is_claims_not_a_scale():
    # Every dimension has a rubric, and it is a rubric-of-claims with an explicit
    # Unknown way-out - never a 1-5 vibe scale.
    for dim in Dimension:
        text = rubric_for(dim)
        assert "claim" in text.lower()
        assert "unknown" in text.lower()
        assert "1-5" not in text or "NOT a" in text  # the only mention disclaims it


def test_build_prompt_is_isolated_to_one_dimension():
    prompt = build_judge_prompt(Dimension.GROUNDEDNESS, {"id": "s1"}, {"x": 1})
    assert "GROUNDEDNESS" in prompt
    # No other dimension's rubric leaks in.
    assert "TASK CORRECTNESS" not in prompt
    assert "SAMPLE" in prompt and "TRANSCRIPT" in prompt


def test_judge_run_parses_forced_tool_call():
    client = MockAnthropicClient(script=[_verdict_response("FIX", 0.4, 0.8, False)])
    judge = DimensionJudge(Dimension.GROUNDEDNESS, client=client)
    v = judge.run(sample={"id": "s1"}, transcript={"rationale": "x"})
    assert isinstance(v, Verdict)
    assert v.verdict is VerdictLabel.FIX
    assert v.dimension is Dimension.GROUNDEDNESS
    assert v.score == 0.4

    # The judge forced the record_verdict tool and ran as its own model call.
    call = client.calls[0]
    assert call["model"] == JUDGE_MODEL
    assert call["tool_choice"] == {"type": "tool", "name": RECORD_VERDICT_TOOL_NAME}
    assert call["tools"][0]["name"] == RECORD_VERDICT_TOOL_NAME


def test_judge_is_separate_call_from_the_agent():
    # The judge never offers the agent's submit_classification tool; it only ever
    # offers record_verdict - it cannot double as the agent under test.
    client = MockAnthropicClient(script=[_verdict_response()])
    DimensionJudge(Dimension.TASK_CORRECTNESS, client=client).run({"id": "s"}, {})
    offered = {t["name"] for t in client.calls[0]["tools"]}
    assert offered == {RECORD_VERDICT_TOOL_NAME}
    assert "submit_classification" not in offered


def test_judge_rejects_free_text_verdict():
    # A judge that answers in prose (no tool call) is rejected, not guessed at.
    client = MockAnthropicClient(script=[text_response("It looks fine to me.", model=JUDGE_MODEL)])
    with pytest.raises(ValueError, match="did not call"):
        DimensionJudge(Dimension.TRAJECTORY, client=client).run({"id": "s"}, {})


def test_judge_unknown_flag_round_trips():
    client = MockAnthropicClient(script=[_verdict_response("PASS", 0.5, 0.2, unknown=True)])
    v = DimensionJudge(Dimension.TASK_CORRECTNESS, client=client).run({"id": "s"}, {})
    assert v.unknown is True
