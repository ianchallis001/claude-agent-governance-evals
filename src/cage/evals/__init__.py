"""The cage evaluation engine.

Submodules (imported on demand, never eagerly from this ``__init__`` so that a
bare ``import cage.evals`` stays cheap and dependency-free):

* :mod:`cage.evals.task` - Inspect-compatible Task = Dataset + Solver + Scorer.
* :mod:`cage.evals.judge` - one isolated LLM-as-judge per dimension.
* :mod:`cage.evals.scorer` - the seven per-dimension scorers.
* :mod:`cage.evals.calibration` - Cohen kappa, FP/FN, flip-rate, noise floor.
* :mod:`cage.evals.drift` - model pin + fingerprint + fail-on-change.
* :mod:`cage.evals.safety` - dangerous-miss gate + HITL escalation scoring.
* :mod:`cage.evals.runner` - orchestrates a full eval run into a RunResult.

The shared :class:`~cage.regmap.Dimension` enum lives in :mod:`cage.regmap`.
"""

from __future__ import annotations

__all__: list[str] = []
