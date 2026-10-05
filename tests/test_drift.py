"""Tests for cage.evals.drift (P1-10)."""

from __future__ import annotations

import pytest

from cage.evals import drift
from cage.models import AGENT_MODEL, ANTHROPIC_VERSION, model_fingerprint


# --------------------------------------------------------------------------- #
# Skeleton contract (unchanged)                                               #
# --------------------------------------------------------------------------- #
def test_drift_controls_mapped():
    frameworks = {r.framework for r in drift.DRIFT_CONTROLS}
    assert "PRA SS1/23" in frameworks
    assert "US SR 11-7" in frameworks


def test_drift_result_ok_property():
    r = drift.DriftResult(pinned_fingerprint="a", current_fingerprint="a",
                          drifted=False, model="claude-sonnet-5-5")
    assert r.ok is True
    r2 = drift.DriftResult(pinned_fingerprint="a", current_fingerprint="b",
                           drifted=True, model="claude-sonnet-5-5")
    assert r2.ok is False


# --------------------------------------------------------------------------- #
# Config fingerprint pin + check                                              #
# --------------------------------------------------------------------------- #
def test_pin_fingerprint_matches_model_fingerprint():
    assert drift.pin_fingerprint(AGENT_MODEL) == model_fingerprint(AGENT_MODEL, ANTHROPIC_VERSION, None)


def test_check_drift_stable_when_config_unchanged():
    pin = drift.pin_fingerprint(AGENT_MODEL)
    result = drift.check_drift(AGENT_MODEL, pin)
    assert result.ok is True
    assert result.drifted is False
    assert result.reason == ""


def test_check_drift_fails_on_a_model_version_bump():
    # A run validated against an OLD model id, then the pinned classifier is
    # bumped to a new id: the fingerprint changes and the gate must fail.
    old_pin = drift.pin_fingerprint("claude-sonnet-5-4")
    result = drift.check_drift("claude-sonnet-5-5", old_pin)
    assert result.drifted is True
    assert result.ok is False
    assert "re-validation required" in result.reason


def test_check_drift_fails_on_api_version_change():
    pin = drift.pin_fingerprint(AGENT_MODEL, anthropic_version="2023-06-01")
    result = drift.check_drift(AGENT_MODEL, pin, anthropic_version="2099-01-01")
    assert result.drifted is True


def test_check_drift_detects_sampling_config_change():
    pin = drift.pin_fingerprint(AGENT_MODEL, temperature=0.0)
    result = drift.check_drift(AGENT_MODEL, pin, temperature=0.7)
    assert result.drifted is True


# --------------------------------------------------------------------------- #
# Behavioural fingerprint                                                     #
# --------------------------------------------------------------------------- #
def test_behavioural_fingerprint_stable_and_order_independent():
    a = {"s1": {"intensity": "HIGH"}, "s2": {"intensity": "LOW"}}
    b = {"s2": {"intensity": "LOW"}, "s1": {"intensity": "HIGH"}}
    assert drift.behavioural_fingerprint(a) == drift.behavioural_fingerprint(b)


def test_behavioural_drift_within_tolerance_passes():
    base = {"s1": {"x": 1}, "s2": {"x": 2}, "s3": {"x": 3}, "s4": {"x": 4}}
    cur = dict(base)
    r = drift.check_behavioural_drift(base, cur)
    assert r.ok is True
    assert r.change_rate == 0.0


def test_behavioural_drift_beyond_tolerance_fails():
    base = {"s1": {"x": 1}, "s2": {"x": 2}}
    cur = {"s1": {"x": 1}, "s2": {"x": 99}}  # one decision changed
    r = drift.check_behavioural_drift(base, cur, tolerance=0.0)
    assert r.drifted is True
    assert r.changed_ids == ["s2"]
    assert r.change_rate == 0.5


def test_behavioural_drift_tolerance_absorbs_small_change():
    base = {f"s{i}": {"x": i} for i in range(10)}
    cur = dict(base)
    cur["s0"] = {"x": 999}  # 1/10 changed
    assert drift.check_behavioural_drift(base, cur, tolerance=0.1).drifted is False
    assert drift.check_behavioural_drift(base, cur, tolerance=0.05).drifted is True


def test_behavioural_tolerance_validated():
    with pytest.raises(ValueError):
        drift.check_behavioural_drift({}, {}, tolerance=2.0)
