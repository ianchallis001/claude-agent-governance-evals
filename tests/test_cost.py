"""Cost accounting and the live budget guard (ticket A-fix / #81).

These lock down the wiring that makes ``--max-cost-usd`` bite on *real* measured
token usage rather than a flat guess:

* :func:`cage.models.cost_usd_for` prices measured tokens at the catalogue rate;
* :func:`cage.models.accrue_usage` sums a response's usage defensively;
* the agent loop and the judge each record their own call's usage;
* the runner reconstructs per-sample cost (agent + judge) and stops before a
  sample it projects to carry over the budget.

Everything here runs offline with no API key: the live client is replaced by a
one-shot fake that returns a usage-bearing mock response.
"""

from __future__ import annotations

from cage.agent import ClassifierAgent
from cage.classify import AutonomyLevel, BlastRadius, Classification, intensity_for
from cage.evals.judge import RECORD_VERDICT_TOOL_NAME, DimensionJudge, VerdictLabel
from cage.evals.runner import run_eval
from cage.evals.scorer import Score
from cage.evals.task import AgentOutput, Dataset, Task
from cage.manifest import AgentManifest
from cage.mock import MockMessage, ToolUseBlock, Usage
from cage.models import AGENT_MODEL, JUDGE_MODEL, accrue_usage, cost_usd_for
from cage.regmap import Dimension


# --------------------------------------------------------------------------- #
# pricing                                                                      #
# --------------------------------------------------------------------------- #
def test_cost_usd_for_prices_at_catalogue_rate():
    # Sonnet 5.5 is $2 / $10 per MTok. One MTok in + one MTok out = $12.
    assert cost_usd_for("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12.0
    # Half an MTok of input only.
    assert cost_usd_for("claude-sonnet-5-5", 500_000, 0) == 1.0


def test_cost_usd_for_unknown_model_is_zero():
    assert cost_usd_for("some-off-catalogue-model", 1_000_000, 1_000_000) == 0.0


def test_cost_usd_for_clamps_negative_tokens():
    assert cost_usd_for("claude-sonnet-5-5", -5, -5) == 0.0


# --------------------------------------------------------------------------- #
# accrue_usage                                                                 #
# --------------------------------------------------------------------------- #
def test_accrue_usage_sums_in_place():
    acc = {"input_tokens": 0, "output_tokens": 0}
    accrue_usage(acc, MockMessage(content=[], model="m", usage=Usage(10, 3)))
    accrue_usage(acc, MockMessage(content=[], model="m", usage=Usage(5, 2)))
    assert acc == {"input_tokens": 15, "output_tokens": 5}


def test_accrue_usage_ignores_response_without_usage():
    acc = {"input_tokens": 1, "output_tokens": 1}

    class _NoUsage:
        usage = None

    accrue_usage(acc, _NoUsage())
    assert acc == {"input_tokens": 1, "output_tokens": 1}


# --------------------------------------------------------------------------- #
# a one-shot fake client                                                       #
# --------------------------------------------------------------------------- #
def _one_shot_client(message: MockMessage):
    """A client whose ``messages.create`` always returns ``message``."""

    class _Messages:
        def create(self, **_kwargs):
            return message

    class _Client:
        messages = _Messages()

    return _Client()


# --------------------------------------------------------------------------- #
# agent + judge capture their own usage                                        #
# --------------------------------------------------------------------------- #
def test_agent_records_turn_usage():
    submission = {
        "autonomy": "L0",
        "blast": "BR1",
        "intensity": "LOW",
        "rationale": "inform only",
        "evidence": [],
    }
    msg = MockMessage(
        content=[ToolUseBlock(name="submit_classification", input=submission, id="t1")],
        model=AGENT_MODEL,
        stop_reason="tool_use",
        usage=Usage(1000, 500),
    )
    agent = ClassifierAgent(client=_one_shot_client(msg))
    agent.classify(AgentManifest.from_dict({"name": "a", "tools": []}))
    assert agent.last_usage == {"input_tokens": 1000, "output_tokens": 500}


def test_judge_records_call_usage():
    verdict_input = {
        "verdict": "PASS",
        "score": 1.0,
        "cited_reason": "every claim holds",
        "confidence": 0.9,
        "unknown": False,
    }
    msg = MockMessage(
        content=[ToolUseBlock(name=RECORD_VERDICT_TOOL_NAME, input=verdict_input, id="v1")],
        model=JUDGE_MODEL,
        stop_reason="tool_use",
        usage=Usage(200, 100),
    )
    judge = DimensionJudge(Dimension.TASK_CORRECTNESS, client=_one_shot_client(msg))
    judge.run({"id": "s1"}, {"classification": {}})
    assert judge.last_usage == {"input_tokens": 200, "output_tokens": 100}


# --------------------------------------------------------------------------- #
# the runner reconstructs cost and the budget bites                            #
# --------------------------------------------------------------------------- #
def _benign_classification() -> Classification:
    a, b = AutonomyLevel.L0, BlastRadius.BR1
    return Classification(
        agent_name="t", autonomy=a, blast=b, intensity=intensity_for(a, b),
        evidence=[], rationale="",
    )


def _fixed_cost_task(n: int, *, agent_in: int, judge_in: int) -> Task:
    """A task whose every sample solves to a fixed agent+judge token cost."""
    dataset = Dataset.from_list(
        [{"id": f"s{i}", "input": {}, "target": {}} for i in range(n)], name="fake"
    )

    def solver(_sample):
        return AgentOutput(
            classification=_benign_classification(),
            usage={"input_tokens": agent_in, "output_tokens": 0},
        )

    def scorer(_sample, _output):
        # A Score for every dimension (as the real score_all does); the judge
        # token cost rides on one judged dimension so it is counted once/sample.
        scores = []
        for dim in Dimension:
            detail: dict = {"checks": {}}
            if dim is Dimension.TASK_CORRECTNESS:
                detail["judge_usage"] = {"input_tokens": judge_in, "output_tokens": 0}
            scores.append(Score(dim, 1.0, VerdictLabel.PASS, detail))
        return scores

    return Task(dataset=dataset, solver=solver, scorer=scorer)


def test_no_budget_reports_measured_cost():
    # agent 1 MTok in @ sonnet $2 = $2.00; judge 1 MTok in @ opus $4 = $4.00.
    # Per sample $6.00, over 3 samples = $18.00, nothing aborted.
    task = _fixed_cost_task(3, agent_in=1_000_000, judge_in=1_000_000)
    result = run_eval(task, threshold=0.7, max_cost_usd=None)
    assert result.cost_usd == 18.0
    assert result.detail["aborted_on_budget"] is False


def test_budget_bites_on_measured_usage():
    # Per sample $2.00 (agent only). Cap $5.00: s0 -> $2, s1 -> $4, s2 would be
    # projected to $6 > $5, so the run stops before s2.
    task = _fixed_cost_task(5, agent_in=1_000_000, judge_in=0)
    result = run_eval(task, threshold=0.7, max_cost_usd=5.0)
    assert result.detail["aborted_on_budget"] is True
    assert result.cost_usd == 4.0
    assert result.cost_usd <= 5.0
    # An aborted (incomplete) run can never PASS.
    assert result.gate is VerdictLabel.FIX


def test_budget_none_runs_every_sample():
    task = _fixed_cost_task(4, agent_in=500_000, judge_in=0)  # $1.00 each
    result = run_eval(task, threshold=0.7, max_cost_usd=None)
    assert result.detail["aborted_on_budget"] is False
    assert result.cost_usd == 4.0
