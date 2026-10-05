"""Tests for cage.models."""

from __future__ import annotations

from cage import models


def test_pinned_ids_exact():
    assert models.AGENT_MODEL == "claude-sonnet-5-5"
    assert models.JUDGE_MODEL == "claude-opus-5-5"
    assert models.ANTHROPIC_VERSION == "2023-06-01"


def test_fingerprint_is_stable_and_hex():
    fp1 = models.model_fingerprint(models.AGENT_MODEL)
    fp2 = models.model_fingerprint(models.AGENT_MODEL)
    assert fp1 == fp2
    assert len(fp1) == 64
    int(fp1, 16)  # hex-decodable


def test_fingerprint_changes_with_inputs():
    base = models.model_fingerprint(models.AGENT_MODEL)
    assert base != models.model_fingerprint(models.JUDGE_MODEL)
    assert base != models.model_fingerprint(models.AGENT_MODEL, anthropic_version="2099-01-01")
    assert base != models.model_fingerprint(models.AGENT_MODEL, extra={"temperature": 0})


def test_pin_safety_heuristic():
    assert models.is_pinned_snapshot("claude-sonnet-5-5") is True
    assert models.is_pinned_snapshot("claude-opus-5-5") is True
    assert models.is_pinned_snapshot("claude-sonnet-5-5-latest") is False
    assert models.is_pinned_snapshot("gpt-4") is False
