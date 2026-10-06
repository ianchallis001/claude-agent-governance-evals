"""Inspect-compatible Task = Dataset + Solver + Scorer.

The shapes mirror the Inspect evals framework so the suite reads familiarly and
could be adapted to it: a :class:`Task` bundles a :class:`Dataset` of
:class:`Sample` records, a :class:`Solver` (produces the agent's output /
transcript for a sample), and a :class:`Scorer` (grades it). Solver and Scorer are
:class:`~typing.Protocol` callables.

The containers and the dataset loader are delivered by the skeleton. Assembling
the concrete governance :class:`Task` (wiring the real solver and the composite
scorer) is ticket P1-11.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cage.classify import Classification


@dataclass
class Sample:
    """One evaluation case."""

    id: str
    input: dict[str, Any]  # e.g. {"manifest": {...}}
    target: dict[str, Any]  # expected classification / labels
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Sample:
        if "id" not in data:
            raise ValueError("sample missing required field: 'id'")
        return cls(
            id=str(data["id"]),
            input=dict(data.get("input", {})),
            target=dict(data.get("target", {})),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class Dataset:
    """An ordered collection of samples."""

    samples: list[Sample] = field(default_factory=list)
    name: str = "dataset"

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self):
        return iter(self.samples)

    @classmethod
    def from_list(cls, items: list[dict[str, Any]], name: str = "dataset") -> Dataset:
        return cls(samples=[Sample.from_dict(i) for i in items], name=name)


def load_dataset(path: str | Path) -> Dataset:
    """Load a :class:`Dataset` from the golden/calibration JSON schema.

    Expected top-level shape: ``{"name": str, "samples": [ <sample>, ... ]}``.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("dataset JSON must be an object at the top level")
    samples = data.get("samples", [])
    if not isinstance(samples, list):
        raise ValueError("dataset 'samples' must be a list")
    return Dataset.from_list(samples, name=str(data.get("name", "dataset")))


@runtime_checkable
class Solver(Protocol):
    """Produces an agent output / transcript for a sample."""

    def __call__(self, sample: Sample) -> Any:  # pragma: no cover - protocol
        ...


@runtime_checkable
class Scorer(Protocol):
    """Grades a solved sample, returning per-dimension scores."""

    def __call__(self, sample: Sample, output: Any) -> Any:  # pragma: no cover - protocol
        ...


@dataclass
class AgentOutput:
    """What the solver produces for one sample: the classification plus the
    trace the scorers need (tool-call trajectory and the manifest it saw)."""

    classification: Classification
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    manifest_dict: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.to_dict(),
            "trajectory": list(self.trajectory),
            "manifest": self.manifest_dict,
        }


@dataclass
class Task:
    """An evaluation task: dataset + solver + scorer."""

    dataset: Dataset
    solver: Solver
    scorer: Scorer
    name: str = "governance_classification"


def default_golden_path() -> Path:
    """Absolute path to the bundled golden dataset."""
    import cage

    return Path(cage.__file__).resolve().parent / "data" / "golden.json"


def governance_task(
    client: Any | None = None,
    dataset: Dataset | None = None,
    judge_client: Any | None = None,
) -> Task:
    """Assemble the governance-classification :class:`Task`.

    Loads the golden dataset (if not supplied), wires a solver that drives
    :class:`~cage.agent.ClassifierAgent` against ``client``, and a scorer that
    composes the seven per-dimension scorers from :mod:`cage.evals.scorer`. The
    judges grade with ``judge_client`` (a *separate* call from the agent under
    test); when it is not supplied the solver's ``client`` is reused, which is how
    a single offline mock serves both roles. Ticket: P1-11.
    """
    # Lazy imports keep module load free of the agent layer and the scorer graph,
    # preserving the acyclic import order (agent sits above the evals modules).
    from cage.agent import ClassifierAgent
    from cage.evals.scorer import score_all
    from cage.manifest import AgentManifest

    if dataset is None:
        dataset = load_dataset(default_golden_path())
    grader_client = judge_client if judge_client is not None else client

    def solver(sample: Sample) -> AgentOutput:
        if "manifest" not in sample.input:
            raise ValueError(f"sample {sample.id!r} has no input.manifest to classify")
        manifest = AgentManifest.from_dict(sample.input["manifest"])
        agent = ClassifierAgent(client=client)
        classification = agent.classify(manifest)
        return AgentOutput(
            classification=classification,
            trajectory=list(agent.last_trajectory),
            manifest_dict=manifest.to_dict(),
        )

    def scorer(sample: Sample, output: Any) -> list[Any]:
        return score_all(sample, output, grader_client)

    return Task(dataset=dataset, solver=solver, scorer=scorer)
