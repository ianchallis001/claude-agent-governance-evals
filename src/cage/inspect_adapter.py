"""Inspect AI adapter: run cage's governance eval under ``inspect_ai``.

`Inspect <https://inspect.aisi.org.uk/>`_ (UK AI Safety Institute) is a widely
used evaluation framework. This module exposes cage's governance evaluation as a
genuine Inspect :class:`~inspect_ai.Task` so it can be discovered and run by the
``inspect eval`` CLI and the Inspect log viewer, while the governance logic stays
cage's own:

* **Dataset** - cage's golden dataset (``cage/data/golden.json``) is converted to
  an Inspect ``Dataset`` of ``Sample`` records.
* **Solver** - cage's own classifier (:func:`cage.classify.classify_manifest`) is
  the agent under test, reused verbatim so the governance verdict is *identical*
  whether run under cage's own runner or under Inspect (behavioural consistency).
* **Scorers** - cage's seven per-dimension scorers
  (:data:`cage.evals.scorer.SCORERS`) each become an Inspect scorer, including
  ``manifest_integrity`` - the detector for a tool manifest that *lies* about what
  a tool does (a benign "read-only lookup" wording hiding a destructive / egress /
  code-execution kind).

Design invariant: ``inspect_ai`` is an OPTIONAL dependency. It is imported lazily
inside the adapter (never at the top of cage's core package), so ``import cage``
and the full offline test suite run with ``inspect_ai`` NOT installed. The
``@task``-decorated :func:`cage_governance_task` is registered for CLI discovery
only when ``inspect_ai`` is importable; without it, the name is still importable
as a plain function that raises a clear install hint when called.

Ticket: P5-01.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from cage.classify import (
    AutonomyLevel,
    BlastRadius,
    Classification,
    GovernanceIntensity,
    ToolEvidence,
)
from cage.evals.scorer import SCORERS
from cage.evals.scorer import Score as CageScore
from cage.evals.task import AgentOutput, Dataset, Sample, default_golden_path, load_dataset
from cage.manifest import AgentManifest
from cage.regmap import Dimension

__all__ = [
    "cage_golden_dataset",
    "cage_governance_task",
    "classify_sample",
    "dimension_scorer_specs",
    "grade_output",
    "inspect_input_text",
    "inspect_sample_metadata",
    "inspect_target_text",
    "to_inspect_dataset",
    "to_inspect_sample",
]

# Metadata key under which the solver stashes the cage classification for the
# scorers, and the key carrying the original cage sample for reconstruction.
_META_SAMPLE = "cage_sample"
_META_OUTPUT = "cage_output"


# --------------------------------------------------------------------------- #
# Pure conversion + wiring logic (no inspect_ai import; unit-tested offline).  #
# --------------------------------------------------------------------------- #
def cage_golden_dataset(dataset_path: str | None = None) -> Dataset:
    """Load cage's golden dataset (or a caller-supplied dataset file)."""
    path = dataset_path if dataset_path is not None else default_golden_path()
    return load_dataset(path)


def inspect_input_text(cage_sample: Sample) -> str:
    """Render the Inspect ``Sample.input`` prompt for a cage sample.

    Mirrors the classifier agent's user prompt: the manifest as pretty JSON. The
    cage solver reuses the deterministic classifier rather than a model, so this
    text is the human-readable record of what was classified, not a live prompt.
    """
    manifest = cage_sample.input.get("manifest", {})
    return "Classify this agent manifest:\n\n" + json.dumps(
        manifest, indent=2, sort_keys=True
    )


def inspect_target_text(cage_sample: Sample) -> str:
    """Render a compact ``Sample.target`` string, e.g. ``L3/BR4/HIGH``."""
    t = cage_sample.target
    return "{}/{}/{}".format(
        t.get("autonomy", "?"), t.get("blast", "?"), t.get("intensity", "?")
    )


def inspect_sample_metadata(cage_sample: Sample) -> dict[str, Any]:
    """Carry the whole cage sample in metadata so the scorers can reconstruct it."""
    return {
        _META_SAMPLE: {
            "id": cage_sample.id,
            "input": cage_sample.input,
            "target": cage_sample.target,
            "metadata": cage_sample.metadata,
        }
    }


def classify_sample(cage_sample: Sample) -> AgentOutput:
    """Run cage's classifier over a sample's manifest -> an :class:`AgentOutput`.

    This is the solver's core, reused verbatim from cage for behavioural
    consistency. Deterministic and offline (no model call).
    """
    if "manifest" not in cage_sample.input:
        raise ValueError(f"sample {cage_sample.id!r} has no input.manifest to classify")
    from cage.classify import classify_manifest  # local: keeps classify import lazy

    manifest = AgentManifest.from_dict(cage_sample.input["manifest"])
    return AgentOutput(
        classification=classify_manifest(manifest),
        trajectory=[],
        manifest_dict=manifest.to_dict(),
    )


def dimension_scorer_specs() -> list[tuple[Dimension, Any]]:
    """The dimension -> cage-scorer wiring, in :class:`Dimension` order.

    Each entry is ``(Dimension, scorer_callable)`` where the callable has the cage
    scorer signature ``(sample, output, client=None) -> Score``. This is the exact
    set the Inspect scorers wrap, exposed as data so the wiring is testable
    without importing ``inspect_ai``.
    """
    return [(dim, SCORERS[dim]) for dim in Dimension]


def grade_output(
    dimension: Dimension,
    cage_sample: Sample,
    output: AgentOutput,
    client: Any | None = None,
) -> CageScore:
    """Grade one dimension of a solved sample with cage's own scorer.

    ``client`` is the optional LLM-judge client; when ``None`` (the default for
    the Inspect path) only the deterministic scoring runs, keeping the eval
    offline and at zero cost. The deterministic scorers already carry the safety
    gates, including the ``manifest_integrity`` dishonest-manifest check.
    """
    return SCORERS[dimension](cage_sample, output, client)


# --------------------------------------------------------------------------- #
# Reconstruction helpers (pure; rebuild cage objects from their dict form).    #
# --------------------------------------------------------------------------- #
def _classification_from_dict(data: dict[str, Any]) -> Classification:
    """Rebuild a :class:`Classification` from its :meth:`to_dict` form."""
    evidence = [
        ToolEvidence(
            tool=str(e.get("tool", "")),
            kind=str(e.get("kind", "")),
            blast_contribution=BlastRadius(int(e["blast_contribution"])),
            reversible=bool(e.get("reversible", True)),
            note=str(e.get("note", "")),
        )
        for e in data.get("evidence", [])
    ]
    return Classification(
        agent_name=str(data.get("agent_name", "")),
        autonomy=AutonomyLevel[data["autonomy"]],
        blast=BlastRadius[data["blast"]],
        intensity=GovernanceIntensity[data["intensity"]],
        evidence=evidence,
        rationale=str(data.get("rationale", "")),
    )


def _agent_output_from_dict(data: dict[str, Any]) -> AgentOutput:
    """Rebuild an :class:`AgentOutput` from its :meth:`to_dict` form."""
    return AgentOutput(
        classification=_classification_from_dict(data["classification"]),
        trajectory=list(data.get("trajectory", [])),
        manifest_dict=dict(data.get("manifest", {})),
    )


def _cage_sample_from_meta(meta: dict[str, Any]) -> Sample:
    """Reconstruct the cage :class:`Sample` from an Inspect state's metadata."""
    return Sample.from_dict(meta[_META_SAMPLE])


def _output_from_state_metadata(meta: dict[str, Any]) -> AgentOutput:
    """Prefer the solver's stored classification; else recompute (deterministic)."""
    stored = meta.get(_META_OUTPUT)
    if isinstance(stored, dict):
        return _agent_output_from_dict(stored)
    return classify_sample(_cage_sample_from_meta(meta))


def _json_safe(value: Any) -> Any:
    """Best-effort coerce scorer detail to JSON-serialisable data for Inspect logs."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


def _completion_text(classification: Classification) -> str:
    """A short, human-readable completion string recording the classification."""
    return f"{classification.autonomy.name}/{classification.blast.name}/{classification.intensity.name}"


# --------------------------------------------------------------------------- #
# Inspect SDK glue (lazy: imported only when these functions are called).      #
# --------------------------------------------------------------------------- #
def _load_inspect() -> SimpleNamespace:
    """Import and bundle the ``inspect_ai`` symbols the adapter needs, lazily.

    Kept out of module scope so importing :mod:`cage.inspect_adapter` never
    requires the optional SDK. Raises a clear, actionable error if it is absent.
    """
    try:
        from inspect_ai import Task
        from inspect_ai.dataset import MemoryDataset
        from inspect_ai.dataset import Sample as InspectSample
        from inspect_ai.scorer import Score, Target, mean, scorer
        from inspect_ai.solver import Generate, TaskState, solver
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "the 'inspect_ai' package is required for the Inspect adapter; "
            "install it with `pip install -e .[inspect]`"
        ) from exc
    return SimpleNamespace(
        Task=Task,
        Sample=InspectSample,
        MemoryDataset=MemoryDataset,
        Score=Score,
        Target=Target,
        mean=mean,
        scorer=scorer,
        solver=solver,
        TaskState=TaskState,
        Generate=Generate,
    )


def to_inspect_sample(cage_sample: Sample, _inspect: SimpleNamespace | None = None) -> Any:
    """Convert one cage :class:`Sample` to an Inspect ``Sample`` (lazy import)."""
    ins = _inspect or _load_inspect()
    return ins.Sample(
        input=inspect_input_text(cage_sample),
        target=inspect_target_text(cage_sample),
        id=cage_sample.id,
        metadata=inspect_sample_metadata(cage_sample),
    )


def to_inspect_dataset(
    cage_dataset: Dataset, _inspect: SimpleNamespace | None = None
) -> Any:
    """Convert a cage :class:`Dataset` to an Inspect ``MemoryDataset`` (lazy import)."""
    ins = _inspect or _load_inspect()
    samples = [to_inspect_sample(s, ins) for s in cage_dataset.samples]
    return ins.MemoryDataset(samples=samples, name=cage_dataset.name)


def cage_classifier_solver(_inspect: SimpleNamespace | None = None) -> Any:
    """Build the Inspect solver that runs cage's classifier (behavioural consistency).

    The solver reconstructs the manifest from the sample metadata, runs cage's
    deterministic classifier, and stashes the resulting classification in the task
    state's metadata for the scorers. It does not call a model: cage's own
    classifier is the agent under test, so the governance verdict matches cage's
    native runner exactly.
    """
    ins = _inspect or _load_inspect()

    @ins.solver
    def _factory() -> Any:
        async def solve(state: Any, generate: Any) -> Any:
            cage_sample = _cage_sample_from_meta(state.metadata)
            output = classify_sample(cage_sample)
            state.metadata[_META_OUTPUT] = output.to_dict()
            try:
                state.output.completion = _completion_text(output.classification)
            except Exception:  # pragma: no cover - output shape varies by version
                pass
            return state

        return solve

    return _factory()


def cage_dimension_scorer(
    dimension: Dimension,
    judge_client: Any | None = None,
    _inspect: SimpleNamespace | None = None,
) -> Any:
    """Build an Inspect scorer wrapping cage's scorer for one ``dimension``.

    The Inspect scorer reads the solver's classification from the state metadata
    and grades it with cage's own per-dimension scorer, mapping the cage
    :class:`~cage.evals.scorer.Score` onto an Inspect ``Score`` (numeric value,
    the PASS/FIX/BLOCK verdict as the answer, and the cage detail as metadata).
    """
    ins = _inspect or _load_inspect()
    cage_fn = SCORERS[dimension]

    @ins.scorer(metrics=[ins.mean()], name=f"cage_{dimension.value}")
    def _factory() -> Any:
        async def score(state: Any, target: Any) -> Any:
            output = _output_from_state_metadata(state.metadata)
            cage_sample = _cage_sample_from_meta(state.metadata)
            cs = cage_fn(cage_sample, output, judge_client)
            return ins.Score(
                value=float(cs.value),
                answer=cs.verdict.value,
                explanation=f"{dimension.value}: {cs.verdict.value}",
                metadata={
                    "dimension": dimension.value,
                    "verdict": cs.verdict.value,
                    "detail": _json_safe(dict(cs.detail)),
                },
            )

        return score

    return _factory()


def _build_cage_governance_task(
    dataset_path: str | None = None,
    judge_client: Any | None = None,
) -> Any:
    """Assemble the Inspect :class:`~inspect_ai.Task` for the governance eval.

    This is the body behind the ``@task``-decorated :func:`cage_governance_task`.
    Parameters are overridable at the CLI via ``-T name=value`` (e.g.
    ``-T dataset_path=my.json``).
    """
    ins = _load_inspect()
    dataset = to_inspect_dataset(cage_golden_dataset(dataset_path), ins)
    scorers = [cage_dimension_scorer(dim, judge_client, ins) for dim in Dimension]
    return ins.Task(
        dataset=dataset,
        solver=cage_classifier_solver(ins),
        scorer=scorers,
        name="cage_governance_classification",
    )


# Register the task for `inspect eval cage.inspect_adapter` discovery *only* when
# inspect_ai is importable. Importing this module without inspect_ai installed
# falls through to a plain function that raises a clear install hint on call, so
# the module stays importable and `import cage` never touches the optional SDK.
try:
    from inspect_ai import task as _inspect_task_decorator
except ImportError:  # pragma: no cover - exercised only without inspect_ai
    _inspect_task_decorator = None

if _inspect_task_decorator is not None:
    cage_governance_task = _inspect_task_decorator(_build_cage_governance_task)
else:  # inspect_ai absent: keep the name importable; it raises when invoked.
    cage_governance_task = _build_cage_governance_task
