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

# Agent under test: the governance classifier runs on Sonnet.
AGENT_MODEL = "claude-sonnet-5-5"

# LLM-as-judge: a stronger model grades the agent, one isolated judge per
# dimension. Judge and agent are deliberately different models.
JUDGE_MODEL = "claude-opus-5-5"

# The ``anthropic-version`` request header the harness pins. The live client
# sends this so a server-side default change cannot move results underneath us.
ANTHROPIC_VERSION = "2023-06-01"


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
