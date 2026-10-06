"""Pinned model identifiers and a reproducibility fingerprint.

Evals must be reproducible, so the model under test and the judge model are
pinned here rather than resolved at call time.

Why these IDs are safe to pin
-----------------------------
From the Claude 4.6 generation onward, Anthropic's *dateless* model aliases are
frozen point-in-time snapshots rather than moving "latest" pointers: once
published, ``claude-sonnet-5-5`` and ``claude-opus-5-5`` resolve to the same
weights for the life of the alias. That makes a dateless ID a legitimate pin for
an evaluation suite - the alias will not silently roll forward underneath a
calibrated judge. (Older generations used ``-latest`` style moving aliases that
must never be pinned for evals; those are deliberately not used here.)

``drift.py`` still fingerprints the resolved model + API version on every run and
fails the suite on any change, so a pin is a convenience, not a trust anchor.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

# Agent under test: the governance classifier runs on Sonnet.
AGENT_MODEL = "claude-sonnet-5-5"

# LLM-as-judge: a stronger model grades the agent, one isolated judge per
# dimension. Judge and agent are deliberately different models.
JUDGE_MODEL = "claude-opus-5-5"

# The ``anthropic-version`` request header the harness pins. The live client
# sends this so a server-side default change cannot move results underneath us.
ANTHROPIC_VERSION = "2023-06-01"


@dataclass(frozen=True)
class ModelSpec:
    """A pinned, first-party Claude API model and its headline attributes.

    Context window and per-MTok prices are the Anthropic first-party API rates;
    they are recorded here so the model-comparison matrix (``cage compare``) can
    show a buyer the cost/capability trade between the models under test without
    a live lookup. Prices are USD per million tokens (input / output).
    """

    model_id: str
    display_name: str
    context_tokens: int
    input_usd_per_mtok: float
    output_usd_per_mtok: float

    @property
    def context_label(self) -> str:
        """A short ASCII context-window label, e.g. ``1M`` or ``200K``."""
        if self.context_tokens % 1_000_000 == 0:
            return f"{self.context_tokens // 1_000_000}M"
        if self.context_tokens % 1_000 == 0:
            return f"{self.context_tokens // 1_000}K"
        return str(self.context_tokens)

    @property
    def price_label(self) -> str:
        """A short ASCII input/output price label, e.g. ``$2/$10 per MTok``."""
        return (
            f"${self.input_usd_per_mtok:g}/${self.output_usd_per_mtok:g} per MTok"
        )


# The current-generation, first-party Claude API model ids the comparison matrix
# knows about. These are the exact, dateless alias ids from the Claude API
# "Current Models" table (cached 2026-09-25): they are complete as-is and MUST
# NOT carry a date suffix (``claude-haiku-4-5``, never ``claude-haiku-4-5-NNN``).
# Dateless 4.6+ aliases are frozen snapshots, so they are safe to pin (see the
# module docstring and SPEC section 9).
MODEL_SPECS: dict[str, ModelSpec] = {
    "claude-sonnet-5-5": ModelSpec(
        "claude-sonnet-5-5", "Claude Sonnet 5.5", 1_000_000, 2.0, 10.0
    ),
    "claude-opus-5-5": ModelSpec(
        "claude-opus-5-5", "Claude Opus 5.5", 1_000_000, 4.0, 20.0
    ),
    "claude-haiku-4-5": ModelSpec(
        "claude-haiku-4-5", "Claude Haiku 4.5", 200_000, 1.0, 5.0
    ),
}

# The default set the ``cage compare`` command runs across, strongest to cheapest:
# Opus 5.5 is the most capable, Sonnet 5.5 the everyday default agent model, and
# Haiku 4.5 the cheap/fast tier. Order is stable so the matrix columns are stable.
DEFAULT_COMPARE_MODELS: tuple[str, ...] = (
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-haiku-4-5",
)

# How a bare first-party model id is addressed on each platform. Anthropic's own
# API (and inspect_ai, which prefixes the provider) take the bare id; Bedrock
# prepends ``anthropic.`` and inspect_ai prepends ``anthropic/``; Vertex takes the
# bare id. Used for documentation and the Inspect-addressing helper below.
_PLATFORM_PREFIX: dict[str, str] = {
    "anthropic": "",
    "api": "",
    "vertex": "",
    "bedrock": "anthropic.",
    "inspect": "anthropic/",
}


def platform_model_id(model: str, platform: str = "anthropic") -> str:
    """Address a first-party model id for a given platform.

    ``anthropic`` / ``api`` / ``vertex`` take the bare id; ``bedrock`` prepends
    ``anthropic.`` and ``inspect`` (for ``inspect_ai`` model strings) prepends
    ``anthropic/``. An unknown platform raises ``ValueError``.
    """
    key = platform.lower()
    if key not in _PLATFORM_PREFIX:
        raise ValueError(
            f"unknown platform {platform!r}; expected one of "
            f"{sorted(_PLATFORM_PREFIX)}"
        )
    return _PLATFORM_PREFIX[key] + model


def model_spec(model: str) -> ModelSpec | None:
    """Return the :class:`ModelSpec` for ``model``, or ``None`` if unknown.

    An unknown id is not an error: a caller may compare an older or custom model
    the catalogue does not carry, and the matrix simply omits its context/price.
    """
    return MODEL_SPECS.get(model)


def model_fingerprint(
    model: str,
    anthropic_version: str = ANTHROPIC_VERSION,
    extra: Mapping[str, object] | None = None,
) -> str:
    """Return a stable, hex-encoded fingerprint for a model configuration.

    The fingerprint is a SHA-256 over a canonical JSON encoding of the model id,
    the pinned API version, and any ``extra`` run-shaping fields (sampling
    parameters, system-prompt hash, tool-schema hash). ``drift.py`` stores this
    value as the pin and fails the run if a fresh fingerprint differs - the
    change-management control mapped to PRA SS1/23 and SR 11-7.

    This is pure and deterministic; it performs no network I/O.
    """
    payload = {
        "model": model,
        "anthropic_version": anthropic_version,
        "extra": dict(extra) if extra else {},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_pinned_snapshot(model: str) -> bool:
    """Return True if ``model`` looks like a frozen, pin-safe snapshot alias.

    Heuristic: a dateless ``claude-<family>-<major>-<minor>`` id (the 4.6+
    convention) is pin-safe; a ``-latest`` suffix is a moving pointer and is
    never pin-safe for evals.
    """
    if not model.startswith("claude-"):
        return False
    return not model.endswith("-latest")
