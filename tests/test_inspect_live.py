"""Tests for cage.inspect_live - the LIVE two-turn loop as an Inspect solver and
the family-separated judge as an Inspect model-graded scorer (tickets PP-07/LL-07).

Proves the spike's "hybrid" verdict in code, OFFLINE: no network, no GPU, no key.

Two tiers, mirroring tests/test_inspect_adapter.py:

* **Offline, SDK-free**: the pure prompt / schema / submission-parsing helpers are
  unit-tested with ``inspect_ai`` NOT required (they import only cage). These run
  as part of the core suite even with the optional dependency absent.
* **SDK-gated** (``pytest.importorskip("inspect_ai")``): the live loop and the
  grader-role scorer are driven with a STUBBED Inspect model returning scripted
  tool-call ``ModelOutput``s - no provider, no network - plus one real
  ``inspect eval`` smoke over a golden subset against an offline adaptive model.

The suite has no pytest-asyncio plugin, so the async entry points are driven
synchronously with ``asyncio.run`` rather than an ``@pytest.mark.asyncio`` marker.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cage.classify import AutonomyLevel, BlastRadius, GovernanceIntensity, intensity_for
from cage.evals.judge import VerdictLabel
from cage.evals.task import AgentOutput, Sample
from cage.inspect_adapter import _META_OUTPUT, inspect_sample_metadata
from cage.inspect_live import (
    GRADER_ROLE,
    build_classification_from_submission,
    inspect_tool_lookup,
    trajectory_from_tool_calls,
)
from cage.manifest import AgentManifest
from cage.regmap import Dimension

_SRC = Path(__file__).resolve().parent.parent / "src"


def _golden_sample(sample_id: str = "low-public-reader") -> Sample:
    from cage.inspect_adapter import cage_golden_dataset

    ds = cage_golden_dataset()
    return next(s for s in ds.samples if s.id == sample_id)


def _manifest(sample: Sample) -> AgentManifest:
    return AgentManifest.from_dict(sample.input["manifest"])


# --------------------------------------------------------------------------- #
# Optional-dependency invariant: import cage must not pull in inspect_ai or     #
# the live adapter module.                                                      #
# --------------------------------------------------------------------------- #
def test_import_cage_does_not_import_inspect_live():
    code = (
        "import sys\n"
        "import cage\n"
        "assert 'inspect_ai' not in sys.modules, 'import cage eagerly imported inspect_ai'\n"
        "assert 'cage.inspect_live' not in sys.modules, 'import cage eagerly imported the live adapter'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(_SRC), "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_live_module_imports_without_calling_inspect():
    """Importing cage.inspect_live must not require inspect_ai at module load."""
    import importlib

    import cage.inspect_live as live

    importlib.reload(live)
    assert hasattr(live, "run_live_classification_loop")
    assert hasattr(live, "cage_live_classifier_solver")
    assert hasattr(live, "cage_live_judge_scorer")


# --------------------------------------------------------------------------- #
# Offline, SDK-free: the pure helpers.                                          #
# --------------------------------------------------------------------------- #
def test_build_classification_rederives_intensity_from_matrix():
    """The submission's self-reported intensity is DISCARDED; the matrix is authoritative."""
    sample = _golden_sample("low-public-reader")
    manifest = _manifest(sample)
    # A submission that lies about the intensity (claims CRITICAL for an L0/BR1 read-only agent).
    submission = {
        "autonomy": "L0",
        "blast": "BR1",
        "intensity": "CRITICAL",  # wrong on purpose
        "rationale": "self-reported nonsense",
        "evidence": [],
    }
    cls = build_classification_from_submission(manifest, submission)
    assert cls.autonomy is AutonomyLevel.L0
    assert cls.blast is BlastRadius.BR1
    # Not CRITICAL: re-derived from the matrix.
    assert cls.intensity is intensity_for(AutonomyLevel.L0, BlastRadius.BR1)
    assert cls.intensity is GovernanceIntensity.LOW


def test_inspect_tool_lookup_matches_cage_inspection():
    import json

    sample = _golden_sample("low-public-reader")
    manifest = _manifest(sample)
    name = manifest.tools[0].name
    data = json.loads(inspect_tool_lookup(manifest, {"tool_name": name}))
    assert data["name"] == name
    assert data["kind"] == manifest.tools[0].kind.value
    # Unknown tool -> structured error, not a crash.
    err = json.loads(inspect_tool_lookup(manifest, {"tool_name": "nope"}))
    assert "error" in err


def test_trajectory_from_tool_calls_maps_inspect_shape_to_cage_shape():
    calls = [
        SimpleNamespace(function="inspect_tool", arguments={"tool_name": "x"}, id="tc1"),
        SimpleNamespace(function="submit_classification", arguments={"autonomy": "L0"}, id="tc2"),
    ]
    rows = trajectory_from_tool_calls(calls)
    assert rows == [
        {"tool": "inspect_tool", "input": {"tool_name": "x"}, "id": "tc1"},
        {"tool": "submit_classification", "input": {"autonomy": "L0"}, "id": "tc2"},
    ]
    assert trajectory_from_tool_calls(None) == []


# --------------------------------------------------------------------------- #
# SDK-gated: scripted-model stubs mirroring the _inspect-injection test style.  #
# --------------------------------------------------------------------------- #
def _reference_submission(manifest: AgentManifest) -> dict:
    """The submit_classification input a perfectly-correct agent would send."""
    from cage.classify import classify_manifest

    r = classify_manifest(manifest)
    return {
        "autonomy": r.autonomy.name,
        "blast": r.blast.name,
        "intensity": r.intensity.name,
        "rationale": r.rationale,
        "evidence": [
            {"tool": e.tool, "blast_contribution": e.blast_contribution.name, "note": e.note}
            for e in r.evidence
        ],
    }


def _manifest_from_messages(messages) -> AgentManifest:
    """Recover the manifest from cage's user prompt in a list of Inspect messages."""
    import json

    for msg in messages:
        if getattr(msg, "role", None) == "user":
            text = msg.content if isinstance(msg.content, str) else getattr(msg, "text", "")
            _, _, payload = text.partition("\n\n")
            return AgentManifest.from_dict(json.loads(payload))
    raise AssertionError("no user message carrying a manifest")


def _make_stubs():
    """Build the inspect_ai-dependent stub classes lazily (SDK-gated callers only)."""
    from inspect_ai.model import ModelOutput

    class ScriptedModel:
        """Returns pre-built ModelOutputs in order; records the tool_choice it saw."""

        def __init__(self, outputs):
            self._outputs = list(outputs)
            self.tool_choices = []

        async def generate(self, input, tools=(), tool_choice=None, **kw):
            self.tool_choices.append(tool_choice)
            if not self._outputs:
                raise AssertionError("scripted model exhausted (loop asked for one turn too many)")
            return self._outputs.pop(0)

    def tool_call_output(name, args, model="mockllm/model"):
        return ModelOutput.for_tool_call(model=model, tool_name=name, tool_arguments=args)

    def text_output(text, model="mockllm/model"):
        return ModelOutput.from_content(model=model, content=text)

    class ReferenceAgentModel:
        """Adaptive stub: reads the manifest from the prompt and submits the reference verdict."""

        def __init__(self, model="mockllm/model"):
            self.model = model

        async def generate(self, input, tools=(), tool_choice=None, **kw):
            manifest = _manifest_from_messages(input)
            return tool_call_output("submit_classification", _reference_submission(manifest), self.model)

    class PassGraderModel:
        """Adaptive stub grader: always a confident PASS via a forced record_verdict call."""

        def __init__(self, model="grader/stub"):
            self.model = model

        async def generate(self, input, tools=(), tool_choice=None, **kw):
            return tool_call_output(
                "record_verdict",
                {"verdict": "PASS", "score": 1.0, "cited_reason": "stub grader", "confidence": 0.9, "unknown": False},
                self.model,
            )

    return SimpleNamespace(
        ScriptedModel=ScriptedModel,
        ReferenceAgentModel=ReferenceAgentModel,
        PassGraderModel=PassGraderModel,
        tool_call_output=tool_call_output,
        text_output=text_output,
    )


def test_live_loop_inspect_then_submit_matches_cage_classifier():
    pytest.importorskip("inspect_ai")
    from cage.classify import classify_manifest
    from cage.inspect_live import _load_inspect_live, run_live_classification_loop

    sample = _golden_sample("low-public-reader")
    manifest = _manifest(sample)
    stubs = _make_stubs()
    ref = _reference_submission(manifest)

    # Turn 1: the model inspects a real tool. Turn 2: it submits.
    model = stubs.ScriptedModel(
        [
            stubs.tool_call_output("inspect_tool", {"tool_name": manifest.tools[0].name}),
            stubs.tool_call_output("submit_classification", ref),
        ]
    )
    ins = _load_inspect_live()
    output, messages = asyncio.run(run_live_classification_loop(manifest, model, ins))

    assert isinstance(output, AgentOutput)
    direct = classify_manifest(manifest)
    assert output.classification.autonomy == direct.autonomy
    assert output.classification.blast == direct.blast
    assert output.classification.intensity == direct.intensity
    # Trajectory recorded both turns in cage's shape; submit is last.
    assert [r["tool"] for r in output.trajectory] == ["inspect_tool", "submit_classification"]
    # The loop used react-style tool_choice="any", NOT a named ToolFunction
    # (terminal detection is on the submit_classification NAME) - the documented
    # substitute for SDK-level named tool forcing.
    assert model.tool_choices == ["any", "any"]
    # The inspect_tool result was fed back as a tool message before the submit turn.
    assert any(getattr(m, "role", None) == "tool" for m in messages)


def test_live_loop_submit_on_first_turn_terminates_by_name():
    pytest.importorskip("inspect_ai")
    from cage.inspect_live import _load_inspect_live, run_live_classification_loop

    sample = _golden_sample("low-public-reader")
    manifest = _manifest(sample)
    stubs = _make_stubs()
    model = stubs.ScriptedModel(
        [stubs.tool_call_output("submit_classification", _reference_submission(manifest))]
    )
    output, _ = asyncio.run(
        run_live_classification_loop(manifest, model, _load_inspect_live())
    )
    assert [r["tool"] for r in output.trajectory] == ["submit_classification"]


def test_live_loop_errors_when_model_never_calls_a_tool():
    pytest.importorskip("inspect_ai")
    from cage.inspect_live import _load_inspect_live, run_live_classification_loop

    manifest = _manifest(_golden_sample("low-public-reader"))
    stubs = _make_stubs()
    model = stubs.ScriptedModel([stubs.text_output("I will not use a tool.")])
    with pytest.raises(ValueError, match="without calling submit_classification"):
        asyncio.run(run_live_classification_loop(manifest, model, _load_inspect_live()))


def test_live_loop_rederives_intensity_even_when_model_lies():
    pytest.importorskip("inspect_ai")
    from cage.inspect_live import _load_inspect_live, run_live_classification_loop

    manifest = _manifest(_golden_sample("low-public-reader"))
    stubs = _make_stubs()
    lying = {
        "autonomy": "L0", "blast": "BR1", "intensity": "CRITICAL",
        "rationale": "lie", "evidence": [],
    }
    model = stubs.ScriptedModel([stubs.tool_call_output("submit_classification", lying)])
    output, _ = asyncio.run(
        run_live_classification_loop(manifest, model, _load_inspect_live())
    )
    assert output.classification.intensity is GovernanceIntensity.LOW  # matrix wins


def test_live_solver_populates_state_metadata():
    pytest.importorskip("inspect_ai")
    from cage.inspect_live import cage_live_classifier_solver

    sample = _golden_sample("low-public-reader")
    stubs = _make_stubs()
    solver = cage_live_classifier_solver(model=stubs.ReferenceAgentModel())

    state = SimpleNamespace(
        metadata=dict(inspect_sample_metadata(sample)),
        messages=[],
        output=SimpleNamespace(completion=""),
        completed=False,
    )
    returned = asyncio.run(solver(state, None))
    assert returned.completed is True
    assert _META_OUTPUT in returned.metadata
    assert returned.metadata[_META_OUTPUT]["classification"]["intensity"] == "LOW"
    # state.messages was driven through the loop (system + user + assistant at least).
    assert len(returned.messages) >= 3


# --------------------------------------------------------------------------- #
# SDK-gated: the grader-role scorer + the family-separation guarantee.          #
# --------------------------------------------------------------------------- #
def test_run_grader_verdict_parses_forced_tool_call():
    pytest.importorskip("inspect_ai")
    from cage.inspect_adapter import classify_sample
    from cage.inspect_live import _load_inspect_live, run_grader_verdict

    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    stubs = _make_stubs()
    grader = stubs.PassGraderModel()
    verdict = asyncio.run(
        run_grader_verdict(Dimension.TASK_CORRECTNESS, sample, output, grader, _load_inspect_live())
    )
    assert verdict.verdict is VerdictLabel.PASS
    assert verdict.score == 1.0
    assert verdict.dimension is Dimension.TASK_CORRECTNESS


def test_run_grader_verdict_backstop_rejects_out_of_range_score():
    """verdict_from_tool_input stays the semantic backstop even on a forced call."""
    pytest.importorskip("inspect_ai")
    from inspect_ai.model import ModelOutput

    from cage.inspect_adapter import classify_sample
    from cage.inspect_live import _load_inspect_live, run_grader_verdict

    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)

    class BadGrader:
        async def generate(self, input, tools=(), tool_choice=None, **kw):
            return ModelOutput.for_tool_call(
                model="grader/stub",
                tool_name="record_verdict",
                tool_arguments={"verdict": "PASS", "score": 9.9, "cited_reason": "x",
                                "confidence": 0.5, "unknown": False},
            )

    with pytest.raises(ValueError, match="score out of range"):
        asyncio.run(
            run_grader_verdict(
                Dimension.TASK_CORRECTNESS, sample, output, BadGrader(), _load_inspect_live()
            )
        )


def test_run_grader_verdict_rejects_free_text():
    pytest.importorskip("inspect_ai")
    from cage.inspect_adapter import classify_sample
    from cage.inspect_live import _load_inspect_live, run_grader_verdict

    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    stubs = _make_stubs()
    model = stubs.ScriptedModel([stubs.text_output("The verdict is PASS, trust me.")])
    with pytest.raises(ValueError, match="did not call 'record_verdict'"):
        asyncio.run(
            run_grader_verdict(
                Dimension.TASK_CORRECTNESS, sample, output, model, _load_inspect_live()
            )
        )


def test_grader_role_required_raises_when_unbound():
    """get_model(role='grader', required=True) raises rather than falling back - family separation."""
    pytest.importorskip("inspect_ai")
    from inspect_ai.model import get_model

    with pytest.raises(Exception) as exc:
        get_model(role=GRADER_ROLE, required=True)
    assert "grader" in str(exc.value).lower()


def test_live_judge_scorer_scores_from_state_metadata():
    pytest.importorskip("inspect_ai")
    from cage.inspect_adapter import classify_sample
    from cage.inspect_live import cage_live_judge_scorer

    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    meta = dict(inspect_sample_metadata(sample))
    meta[_META_OUTPUT] = output.to_dict()
    state = SimpleNamespace(metadata=meta)

    stubs = _make_stubs()
    score_fn = cage_live_judge_scorer(Dimension.TASK_CORRECTNESS, grader_model=stubs.PassGraderModel())
    score = asyncio.run(score_fn(state, None))
    assert score.value == 1.0
    assert score.answer == "PASS"
    assert score.metadata["dimension"] == "task_correctness"


# --------------------------------------------------------------------------- #
# SDK-gated: a real `inspect eval` over a golden subset, offline.               #
# --------------------------------------------------------------------------- #
def test_real_inspect_eval_live_route_over_golden_subset():
    """Assemble a real Inspect Task with the LIVE solver + grader scorer and run
    `inspect eval` over a small golden subset against offline adaptive stub models.

    This drives Inspect's full eval runtime (dataset, solver, scorer, logging) with
    no network/GPU/key: the model under test and the grader are local stubs.
    """
    pytest.importorskip("inspect_ai")
    from inspect_ai import Task
    from inspect_ai import eval as inspect_eval

    from cage.inspect_adapter import _load_inspect, cage_golden_dataset, to_inspect_dataset
    from cage.inspect_live import (
        cage_live_classifier_solver,
        cage_live_judge_scorer,
    )

    stubs = _make_stubs()
    dataset = to_inspect_dataset(cage_golden_dataset(), _load_inspect())
    task = Task(
        dataset=dataset,
        solver=cage_live_classifier_solver(model=stubs.ReferenceAgentModel()),
        scorer=[
            cage_live_judge_scorer(dim, grader_model=stubs.PassGraderModel())
            for dim in Dimension
        ],
        name="cage_live_governance_classification",
    )
    logs = inspect_eval(task, model="mockllm/model", limit=3, display="none")
    assert logs
    log = logs[0]
    assert log.status == "success", getattr(log, "error", None)
    assert log.results is not None
    scorer_names = {s.name for s in log.results.scores}
    for dim in Dimension:
        assert f"cage_judge_{dim.value}" in scorer_names
