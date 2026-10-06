"""Model-comparison matrix: run the same governance eval across several models.

``cage compare`` runs the identical governance evaluation - the same golden
dataset, the same seven per-dimension scorers, the same gate - against a set of
candidate agent models, and lays the per-dimension scores out as a matrix (rows =
the seven dimensions plus the overall gate; columns = the models), with a DELTA
column that shows where the models diverge.

It reuses the existing runner (:func:`cage.evals.runner.run_eval`) once per model;
the only per-model wiring is (a) the agent model the classifier runs on and (b),
offline, the model-aware comparison mock
(:func:`cage.mock.comparison_mock_client`) that varies the judge's quality score
by model id so the matrix shows reproducible deltas.

Model-version tie-in (SPEC section 9): each model id has its own configuration
fingerprint. Switching the agent to a different model id therefore changes the
fingerprint, which is exactly the drift signal the runner's drift gate fails on
(PRA SS1/23 Principle 2.4; US SR 11-7 Sec. VII). A comparison is thus also a map
of re-validation events: adopting any column other than the pinned one is a
model-drift event that must re-run calibration before the new model is trusted.

Ticket: P5-02.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from cage.evals.runner import run_eval
from cage.evals.scorer import score_all
from cage.evals.task import AgentOutput, Dataset, Sample, Task, default_golden_path, load_dataset
from cage.manifest import AgentManifest
from cage.models import DEFAULT_COMPARE_MODELS, model_spec
from cage.regmap import Dimension

# Exit codes, matching the gate mapping the rest of the CLI uses.
EXIT_PASS = 0
EXIT_FIX = 1
EXIT_BLOCK = 2

# The dimension rows, in the stable enum order, plus a trailing overall-gate row.
_GATE_ROW = "overall gate"


@dataclass
class ModelResult:
    """One model's outcome in a comparison: its scores, gate and fingerprint."""

    model_id: str
    fingerprint: str
    gate: str
    exit_code: int
    means: dict[str, float | None]  # dimension.value -> mean score (None if no data)

    @property
    def short_name(self) -> str:
        """The model id without the ``claude-`` prefix, for a narrow column head."""
        return self.model_id[len("claude-"):] if self.model_id.startswith("claude-") else self.model_id


@dataclass
class ComparisonMatrix:
    """The assembled model-comparison matrix."""

    results: list[ModelResult]
    dataset_name: str
    n_samples: int
    threshold: float
    repeat: int
    mock: bool
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Most severe per-model exit code (see :func:`run_comparison` for the rule)."""
        return max((r.exit_code for r in self.results), default=EXIT_PASS)

    def dimension_delta(self, dimension: str) -> float | None:
        """Spread (max - min) of a dimension's mean across the compared models.

        ``None`` when fewer than two models reported a numeric mean for it.
        """
        values = [
            r.means.get(dimension)
            for r in self.results
            if r.means.get(dimension) is not None
        ]
        if len(values) < 2:
            return None
        return round(max(values) - min(values), 4)

    def divergent_dimensions(self, epsilon: float = 1e-9) -> list[str]:
        """Dimensions whose per-model means are not all equal, most-divergent first."""
        spreads = []
        for dim in Dimension:
            d = self.dimension_delta(dim.value)
            if d is not None and d > epsilon:
                spreads.append((d, dim.value))
        spreads.sort(reverse=True)
        return [name for _, name in spreads]


# --------------------------------------------------------------------------- #
# Running the comparison                                                       #
# --------------------------------------------------------------------------- #
def _build_task(client: Any, dataset: Dataset, model_id: str) -> Task:
    """Build a governance Task whose classifier agent runs on ``model_id``.

    Mirrors :func:`cage.evals.task.governance_task` but pins the agent model so a
    live comparison genuinely drives each candidate model (offline the classifier
    is deterministic, so the model id only shapes the fingerprint). The judge
    client is the same ``client`` - a single offline mock serves both roles.
    """
    from cage.agent import ClassifierAgent  # lazy: keeps the import graph acyclic

    def solver(sample: Sample) -> AgentOutput:
        if "manifest" not in sample.input:
            raise ValueError(f"sample {sample.id!r} has no input.manifest to classify")
        manifest = AgentManifest.from_dict(sample.input["manifest"])
        agent = ClassifierAgent(client=client, model=model_id)
        classification = agent.classify(manifest)
        return AgentOutput(
            classification=classification,
            trajectory=list(agent.last_trajectory),
            manifest_dict=manifest.to_dict(),
        )

    def scorer(sample: Sample, output: Any) -> list[Any]:
        return score_all(sample, output, client)

    return Task(dataset=dataset, solver=solver, scorer=scorer, name="cage_compare")


def _mock_client_factory(model_id: str) -> Any:
    from cage.mock import comparison_mock_client  # lazy intra-package import

    return comparison_mock_client(model_id)


def _live_client_factory() -> Any:
    """Build a single live Anthropic client, reused across all compared models.

    Lazy, and only on the live path, so importing this module never needs the SDK.
    The agent model varies per model via :func:`_build_task`; the one client is
    shared because the account/credentials are the same for every column.
    """
    from cage.agent import _load_anthropic  # lazy: SDK import stays off module load

    anthropic = _load_anthropic()
    return anthropic.Anthropic()


def run_comparison(
    models: list[str] | tuple[str, ...] | None = None,
    *,
    dataset: Dataset | None = None,
    threshold: float = 0.7,
    repeat: int = 1,
    mock: bool = False,
    client_factory: Any | None = None,
) -> ComparisonMatrix:
    """Run the governance eval across ``models`` and assemble a comparison matrix.

    ``models`` defaults to :data:`cage.models.DEFAULT_COMPARE_MODELS`. ``mock``
    runs offline against the model-aware comparison mock; without it the live
    path builds one Anthropic client (requires ``ANTHROPIC_API_KEY``) and drives
    each candidate model through it. ``client_factory`` overrides client
    construction (``(model_id) -> client``); it is the seam the tests inject.

    Exit-code rule: each model's run gates to PASS/FIX/BLOCK exactly as a single
    ``cage eval`` would, and the matrix's ``exit_code`` is the most severe across
    the models - 0 only when every model PASSes. A model "falls below
    ``--threshold``" when any of its gating quality dimensions means under the
    threshold, which the runner turns into a FIX (exit 1); a safety trip would be
    a BLOCK (exit 2). So the comparison is non-zero exactly when a selected model
    falls below the threshold or trips a safety gate.
    """
    model_ids = list(DEFAULT_COMPARE_MODELS) if models is None else list(models)
    if not model_ids:
        raise ValueError("run_comparison needs at least one model id")
    if dataset is None:
        dataset = load_dataset(default_golden_path())

    notes: list[str] = []
    if client_factory is None:
        if mock:
            client_factory = _mock_client_factory
        else:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError(
                    "cage compare live mode requires ANTHROPIC_API_KEY to be set; "
                    "pass --mock to run offline"
                )
            live_client = _live_client_factory()
            client_factory = lambda _model: live_client  # noqa: E731 - small seam

    results: list[ModelResult] = []
    for model_id in model_ids:
        client = client_factory(model_id)
        task = _build_task(client, dataset, model_id)
        run = run_eval(
            task,
            client=client,
            threshold=threshold,
            repeat=repeat,
            agent_model=model_id,
        )
        scorecard = run.detail.get("scorecard", {})
        means = {
            dim.value: (scorecard.get(dim.value) or {}).get("mean_value")
            for dim in Dimension
        }
        results.append(
            ModelResult(
                model_id=model_id,
                fingerprint=run.detail["run_manifest"]["agent_fingerprint"],
                gate=run.gate.value,
                exit_code=run.exit_code,
                means=means,
            )
        )

    return ComparisonMatrix(
        results=results,
        dataset_name=dataset.name,
        n_samples=len(dataset),
        threshold=threshold,
        repeat=repeat,
        mock=mock,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #
def _fmt_cell(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_delta(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def format_matrix(matrix: ComparisonMatrix) -> str:
    """Render a :class:`ComparisonMatrix` as an ASCII table + per-model detail.

    The matrix rows are the seven dimensions plus an overall-gate row; the columns
    are the compared models; a trailing DELTA column carries the per-dimension
    spread. Below the table, each model gets its fingerprint, context/price, gate
    and the one-line model-id -> fingerprint -> re-validation tie-in.
    """
    results = matrix.results
    tag = "  (mock)" if matrix.mock else ""

    lines: list[str] = []
    lines.append(f"cage compare - governance classification across {len(results)} model(s){tag}")
    lines.append(
        f"dataset: {matrix.dataset_name} ({matrix.n_samples} sample(s))  "
        f"threshold: {matrix.threshold:.2f}  repeat: {matrix.repeat}"
    )
    lines.append("")

    # Column widths: dimension label, one per model, and the delta column.
    label_w = max([len("dimension"), len(_GATE_ROW)] + [len(d.value) for d in Dimension])
    col_heads = [r.short_name for r in results]
    col_w = [max(8, len(h)) for h in col_heads]
    delta_w = max(5, len("delta"))

    def _row(label: str, cells: list[str], delta: str) -> str:
        padded = "  ".join(c.rjust(col_w[i]) for i, c in enumerate(cells))
        return f"{label.ljust(label_w)}  {padded}  {delta.rjust(delta_w)}"

    lines.append(_row("dimension", col_heads, "delta"))
    lines.append(
        f"{'-' * label_w}  "
        + "  ".join("-" * w for w in col_w)
        + f"  {'-' * delta_w}"
    )

    for dim in Dimension:
        cells = [_fmt_cell(r.means.get(dim.value)) for r in results]
        delta = _fmt_delta(matrix.dimension_delta(dim.value))
        lines.append(_row(dim.value, cells, delta))

    # Overall-gate row: the per-model gate, with "=" when all gates agree.
    gate_cells = [r.gate for r in results]
    gate_delta = "=" if len(set(gate_cells)) <= 1 else "DIFF"
    lines.append(_row(_GATE_ROW, gate_cells, gate_delta))

    # DELTA section: where the models actually diverge.
    lines.append("")
    divergent = matrix.divergent_dimensions()
    if divergent:
        lines.append("deltas (models differ on):")
        for name in divergent:
            vals = {r.short_name: r.means.get(name) for r in results}
            best = max(vals, key=lambda k: (vals[k] is not None, vals[k]))
            worst = min(vals, key=lambda k: (vals[k] is None, vals[k]))
            lines.append(
                f"  - {name}: spread {matrix.dimension_delta(name):.2f} "
                f"(best {best} {_fmt_cell(vals[best])}, worst {worst} {_fmt_cell(vals[worst])})"
            )
    else:
        lines.append("deltas: none - every model scored identically on every dimension")

    # Per-model detail: fingerprint + context/price + the drift / re-validation tie-in.
    lines.append("")
    for r in results:
        spec = model_spec(r.model_id)
        ctx = f"  context {spec.context_label}  {spec.price_label}" if spec else ""
        lines.append(f"{r.model_id}{tag}  gate {r.gate}  exit {r.exit_code}")
        lines.append(f"  fingerprint: {r.fingerprint[:12]}...{ctx}")
        lines.append(
            "  model id -> fingerprint: a different model id is a different "
            "fingerprint, i.e. a drift / SS1-23 re-validation event (re-run calibration)."
        )

    lines.append("")
    lines.append(
        f"gate: {'PASS' if matrix.exit_code == EXIT_PASS else ('FIX' if matrix.exit_code == EXIT_FIX else 'BLOCK')}"
        f"  exit: {matrix.exit_code}  "
        f"(non-zero if any model falls below threshold {matrix.threshold:.2f} or trips a safety gate)"
    )
    return "\n".join(lines)


__all__ = [
    "EXIT_BLOCK",
    "EXIT_FIX",
    "EXIT_PASS",
    "ComparisonMatrix",
    "ModelResult",
    "format_matrix",
    "run_comparison",
]
