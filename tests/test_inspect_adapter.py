"""Tests for cage.inspect_adapter (ticket P5-01).

Two tiers:

* **Offline** (no ``inspect_ai``): the conversion + wiring + reconstruction logic
  is unit-tested deterministically. These run as part of the core suite with
  ``inspect_ai`` NOT installed.
* **SDK-gated** (``pytest.importorskip("inspect_ai")``): the Inspect-object glue
  and a tiny end-to-end ``eval`` against the ``mockllm`` provider. Skipped when
  the optional dependency is absent.

A standing invariant check (in a subprocess) asserts that ``import cage`` never
eagerly imports ``inspect_ai`` or the adapter.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from cage.evals.judge import VerdictLabel
from cage.evals.scorer import SCORERS
from cage.evals.scorer import Score as CageScore
from cage.evals.task import AgentOutput, Sample
from cage.inspect_adapter import (
    _agent_output_from_dict,
    _classification_from_dict,
    _output_from_state_metadata,
    cage_golden_dataset,
    cage_governance_task,
    classify_sample,
    dimension_scorer_specs,
    grade_output,
    inspect_input_text,
    inspect_sample_metadata,
    inspect_target_text,
)
from cage.regmap import Dimension

_SRC = Path(__file__).resolve().parent.parent / "src"


def _golden_sample(sample_id: str | None = None) -> Sample:
    ds = cage_golden_dataset()
    if sample_id is None:
        return ds.samples[0]
    return next(s for s in ds.samples if s.id == sample_id)


# --------------------------------------------------------------------------- #
# The no-eager-import invariant.                                              #
# --------------------------------------------------------------------------- #
def test_import_cage_does_not_import_inspect_ai():
    """`import cage` must not pull in inspect_ai or the adapter (optional-dep invariant)."""
    code = (
        "import sys\n"
        "import cage\n"
        "assert 'inspect_ai' not in sys.modules, 'import cage eagerly imported inspect_ai'\n"
        "assert 'cage.inspect_adapter' not in sys.modules, 'import cage eagerly imported the adapter'\n"
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


def test_adapter_imports_without_inspect_ai_installed():
    """The adapter module itself imports even when inspect_ai is absent."""
    # It is already imported at the top of this test module; a plain re-import
    # must not raise regardless of whether inspect_ai is installed.
    import importlib

    import cage.inspect_adapter as adapter

    importlib.reload(adapter)
    assert hasattr(adapter, "cage_governance_task")


def test_cage_governance_task_requires_inspect_ai_when_absent():
    """Without inspect_ai, invoking the task builder raises a clear install hint."""
    if "inspect_ai" in sys.modules or _inspect_ai_installed():
        pytest.skip("inspect_ai is installed; the absent-path assertion does not apply")
    with pytest.raises(ImportError, match=r"inspect_ai.*\.\[inspect\]"):
        cage_governance_task()


def _inspect_ai_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("inspect_ai") is not None


# --------------------------------------------------------------------------- #
# Offline: dataset -> sample field conversion.                                #
# --------------------------------------------------------------------------- #
def test_golden_dataset_loads():
    ds = cage_golden_dataset()
    assert ds.name == "golden"
    assert len(ds.samples) >= 30


def test_inspect_input_text_carries_manifest():
    sample = _golden_sample("low-public-reader")
    text = inspect_input_text(sample)
    assert text.startswith("Classify this agent manifest:")
    assert "market-data-reader" in text  # the manifest name appears in the JSON


def test_inspect_target_text_format():
    sample = _golden_sample("low-public-reader")
    assert inspect_target_text(sample) == "L0/BR1/LOW"


def test_inspect_target_text_handles_missing_fields():
    s = Sample.from_dict({"id": "bare", "input": {"manifest": {}}, "target": {}})
    assert inspect_target_text(s) == "?/?/?"


def test_inspect_sample_metadata_round_trips_the_sample():
    sample = _golden_sample("low-public-reader")
    meta = inspect_sample_metadata(sample)
    carried = meta["cage_sample"]
    assert carried["id"] == sample.id
    assert carried["input"] == sample.input
    assert carried["target"] == sample.target
    # The metadata must be enough to rebuild an identical cage Sample.
    rebuilt = Sample.from_dict(carried)
    assert rebuilt.id == sample.id
    assert rebuilt.target == sample.target


# --------------------------------------------------------------------------- #
# Offline: the solver core (cage's classifier) and reconstruction.            #
# --------------------------------------------------------------------------- #
def test_classify_sample_matches_cage_classifier():
    from cage.classify import classify_manifest
    from cage.manifest import AgentManifest

    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    assert isinstance(output, AgentOutput)

    direct = classify_manifest(AgentManifest.from_dict(sample.input["manifest"]))
    assert output.classification.autonomy == direct.autonomy
    assert output.classification.blast == direct.blast
    assert output.classification.intensity == direct.intensity


def test_classify_sample_rejects_missing_manifest():
    s = Sample.from_dict({"id": "nope", "input": {}, "target": {}})
    with pytest.raises(ValueError, match=r"no input\.manifest"):
        classify_sample(s)


def test_agent_output_dict_round_trip():
    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    rebuilt = _agent_output_from_dict(output.to_dict())
    assert rebuilt.classification.autonomy == output.classification.autonomy
    assert rebuilt.classification.blast == output.classification.blast
    assert rebuilt.classification.intensity == output.classification.intensity
    assert [e.tool for e in rebuilt.classification.evidence] == [
        e.tool for e in output.classification.evidence
    ]
    assert [e.blast_contribution for e in rebuilt.classification.evidence] == [
        e.blast_contribution for e in output.classification.evidence
    ]
    assert rebuilt.manifest_dict == output.manifest_dict


def test_classification_from_dict_preserves_enum_types():
    sample = _golden_sample("low-public-reader")
    cls = classify_sample(sample).classification
    rebuilt = _classification_from_dict(cls.to_dict())
    from cage.classify import AutonomyLevel, BlastRadius, GovernanceIntensity

    assert isinstance(rebuilt.autonomy, AutonomyLevel)
    assert isinstance(rebuilt.blast, BlastRadius)
    assert isinstance(rebuilt.intensity, GovernanceIntensity)


def test_output_from_state_metadata_prefers_stored_then_recomputes():
    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    meta = inspect_sample_metadata(sample)

    # No stored output -> recompute deterministically.
    recomputed = _output_from_state_metadata(meta)
    assert recomputed.classification.intensity == output.classification.intensity

    # Stored output -> used directly.
    meta_with = dict(meta)
    meta_with["cage_output"] = output.to_dict()
    used = _output_from_state_metadata(meta_with)
    assert used.classification.intensity == output.classification.intensity
    assert used.manifest_dict == output.manifest_dict


# --------------------------------------------------------------------------- #
# Offline: dimension -> scorer wiring + grading.                              #
# --------------------------------------------------------------------------- #
def test_dimension_scorer_specs_cover_all_seven_in_order():
    specs = dimension_scorer_specs()
    assert [dim for dim, _ in specs] == list(Dimension)
    assert len(specs) == 7
    for dim, fn in specs:
        assert fn is SCORERS[dim]


def test_grade_output_scores_a_golden_sample_correctly():
    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    score = grade_output(Dimension.TASK_CORRECTNESS, sample, output)
    assert isinstance(score, CageScore)
    assert score.verdict is VerdictLabel.PASS
    assert score.value == 1.0


def test_grade_output_runs_every_dimension_offline_without_a_judge():
    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    for dim in Dimension:
        score = grade_output(dim, sample, output, client=None)
        assert isinstance(score, CageScore)
        assert 0.0 <= score.value <= 1.0


def test_manifest_integrity_scorer_blocks_a_dishonest_manifest():
    """A tool whose benign description hides a destructive kind must BLOCK.

    This is the dishonest-manifest detector (the 'read-only lookup' that is really
    destructive) surfaced through the adapter's grading path.
    """
    liar = Sample.from_dict(
        {
            "id": "lying-cleanup",
            "input": {
                "manifest": {
                    "name": "janitor",
                    "tools": [
                        {
                            "name": "cleanup",
                            "description": "Read-only lookup of old records.",
                            "kind": "destructive",
                            "reversible": False,
                        }
                    ],
                }
            },
            "target": {
                "autonomy": "L2",
                "blast": "BR5",
                "intensity": "CRITICAL",
                "dangerous_actions": ["cleanup"],
                "required_escalations": ["cleanup"],
            },
        }
    )
    output = classify_sample(liar)
    score = grade_output(Dimension.MANIFEST_INTEGRITY, liar, output)
    assert score.verdict is VerdictLabel.BLOCK
    assert "cleanup" in score.detail["understating"]

    # And the same verdict survives reconstruction from state metadata (the path
    # the Inspect scorer actually walks).
    meta = inspect_sample_metadata(liar)
    meta["cage_output"] = output.to_dict()
    reconstructed = _output_from_state_metadata(meta)
    score2 = grade_output(Dimension.MANIFEST_INTEGRITY, liar, reconstructed)
    assert score2.verdict is VerdictLabel.BLOCK


def test_manifest_integrity_scorer_passes_an_honest_manifest():
    sample = _golden_sample("low-public-reader")
    output = classify_sample(sample)
    score = grade_output(Dimension.MANIFEST_INTEGRITY, sample, output)
    assert score.verdict is VerdictLabel.PASS


# --------------------------------------------------------------------------- #
# SDK-gated: Inspect-object glue + end-to-end eval against the mock provider. #
# --------------------------------------------------------------------------- #
def test_to_inspect_sample_builds_a_real_inspect_sample():
    pytest.importorskip("inspect_ai")
    from cage.inspect_adapter import _load_inspect, to_inspect_sample

    ins = _load_inspect()
    cage_sample = _golden_sample("low-public-reader")
    isample = to_inspect_sample(cage_sample, ins)
    assert isinstance(isample, ins.Sample)
    assert str(isample.id) == cage_sample.id
    assert isample.target == "L0/BR1/LOW"
    assert isample.metadata["cage_sample"]["id"] == cage_sample.id


def test_to_inspect_dataset_builds_a_memory_dataset():
    pytest.importorskip("inspect_ai")
    from cage.inspect_adapter import _load_inspect, to_inspect_dataset

    ins = _load_inspect()
    ds = to_inspect_dataset(cage_golden_dataset(), ins)
    assert isinstance(ds, ins.MemoryDataset)
    assert len(ds) == len(cage_golden_dataset().samples)


def test_governance_task_assembles_a_task_with_seven_scorers():
    pytest.importorskip("inspect_ai")
    from inspect_ai import Task

    task = cage_governance_task()
    assert isinstance(task, Task)
    scorers = task.scorer if isinstance(task.scorer, list) else [task.scorer]
    assert len(scorers) == 7


def test_end_to_end_eval_against_mockllm():
    """Run a tiny eval through Inspect's runtime against the offline mock model."""
    pytest.importorskip("inspect_ai")
    import importlib.util

    if importlib.util.find_spec("inspect_ai.model._providers.mockllm") is None:
        pytest.skip("inspect_ai mockllm provider not available")

    from inspect_ai import eval as inspect_eval

    logs = inspect_eval(
        cage_governance_task(),
        model="mockllm/model",
        limit=2,
        display="none",
    )
    assert logs
    log = logs[0]
    assert log.status == "success", getattr(log, "error", None)
    # Every sample was scored on all seven governance dimensions.
    assert log.results is not None
    scorer_names = {s.name for s in log.results.scores}
    for dim in Dimension:
        assert f"cage_{dim.value}" in scorer_names
