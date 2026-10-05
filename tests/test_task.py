"""Tests for cage.evals.task."""

from __future__ import annotations

import json

import pytest

from cage.classify import Classification
from cage.evals.task import (
    AgentOutput,
    Dataset,
    Sample,
    Scorer,
    Solver,
    Task,
    default_golden_path,
    governance_task,
    load_dataset,
)


def test_sample_from_dict():
    s = Sample.from_dict({"id": "s1", "input": {"manifest": {}}, "target": {"intensity": "HIGH"}})
    assert s.id == "s1"
    assert s.target["intensity"] == "HIGH"


def test_sample_requires_id():
    with pytest.raises(ValueError):
        Sample.from_dict({"input": {}})


def test_dataset_len_and_iter():
    ds = Dataset.from_list([{"id": "a"}, {"id": "b"}], name="t")
    assert len(ds) == 2
    assert [s.id for s in ds] == ["a", "b"]
    assert ds.name == "t"


def test_load_dataset_from_file(tmp_path):
    p = tmp_path / "ds.json"
    p.write_text(json.dumps({"name": "d", "samples": [{"id": "x"}]}), encoding="utf-8")
    ds = load_dataset(p)
    assert ds.name == "d"
    assert ds.samples[0].id == "x"


def test_bundled_golden_set_loads():
    from pathlib import Path

    import cage
    import cage.data
    golden = Path(cage.__file__).resolve().parent / "data" / "golden.json"
    ds = load_dataset(golden)
    assert ds.name == "golden"
    assert isinstance(ds.samples, list)


def test_task_container():
    def solver(sample):  # trivial Solver
        return {"out": sample.id}

    def scorer(sample, output):  # trivial Scorer
        return []

    ds = Dataset.from_list([{"id": "a"}])
    task = Task(dataset=ds, solver=solver, scorer=scorer)
    assert isinstance(task.solver, Solver)
    assert isinstance(task.scorer, Scorer)
    assert task.name == "governance_classification"


def test_default_golden_path_exists():
    assert default_golden_path().is_file()


def test_governance_task_assembles_against_golden(governance_client):
    task = governance_task(client=governance_client)
    assert task.name == "governance_classification"
    assert len(task.dataset) >= 30
    assert isinstance(task.solver, Solver)
    assert isinstance(task.scorer, Scorer)


def test_governance_task_accepts_a_custom_dataset(governance_client):
    ds = load_dataset(default_golden_path())
    task = governance_task(client=governance_client, dataset=ds)
    assert task.dataset is ds


def test_governance_solver_produces_agent_output(governance_client):
    task = governance_task(client=governance_client)
    sample = task.dataset.samples[0]
    output = task.solver(sample)
    assert isinstance(output, AgentOutput)
    assert isinstance(output.classification, Classification)
    # The trajectory and manifest are carried for the scorers.
    assert output.trajectory and output.trajectory[-1]["tool"] == "submit_classification"
    assert "tools" in output.manifest_dict
    d = output.to_dict()
    assert d["classification"]["agent_name"] == output.classification.agent_name


def test_governance_scorer_scores_every_dimension(governance_client):
    from cage.regmap import Dimension

    task = governance_task(client=governance_client)
    sample = task.dataset.samples[0]
    scores = task.scorer(sample, task.solver(sample))
    assert [s.dimension for s in scores] == list(Dimension)
