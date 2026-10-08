"""Tests for cage.collectors.annex_iii."""

from __future__ import annotations

import json

import pytest

from cage.classify import classify_manifest
from cage.collectors.annex_iii import (
    ANNEX_III_AREAS,
    EU_AI_ACT,
    classify_annex_iii,
    is_annex_iii_clause,
)
from cage.manifest import AgentManifest, ManifestError
from cage.mcp_server import classify_payload
from cage.regmap import ControlRow

_TOOLS = [
    {"name": "read_file", "description": "Reads applicant file",
     "kind": "read_sensitive", "reversible": True},
    {"name": "write_decision", "description": "Writes the decision autonomously",
     "kind": "write", "reversible": False},
]


def _manifest() -> AgentManifest:
    return AgentManifest.from_dict({"name": "credit-agent", "tools": _TOOLS})


def test_axes_match_classify_manifest() -> None:
    m = _manifest()
    base = classify_manifest(m)
    got = classify_annex_iii(m)
    assert (got.autonomy, got.blast, got.intensity) == (
        base.autonomy.name, base.blast.name, base.intensity.name)
    assert [e.tool for e in got.evidence] == ["read_file", "write_decision"]


def test_controls_are_eu_ai_act_articles_9_to_15_only() -> None:
    got = classify_annex_iii(_manifest())
    assert got.controls
    assert {c.framework for c in got.controls} == {EU_AI_ACT}
    assert all(is_annex_iii_clause(c) for c in got.controls)
    assert not any(c.clause.startswith("Art. 50") for c in got.controls)


def test_controls_subset_of_framework_scoped_classification() -> None:
    m = _manifest()
    full = classify_manifest(m, frameworks=[EU_AI_ACT]).controls
    assert all(c in full for c in classify_annex_iii(m).controls)


def test_is_annex_iii_clause_rules() -> None:
    def row(fw: str, clause: str) -> ControlRow:
        return ControlRow(fw, clause, "t", "r")

    assert is_annex_iii_clause(row(EU_AI_ACT, "Art. 14(4)"))
    assert is_annex_iii_clause(row(EU_AI_ACT, "Art. 13(2),(3)(b)"))
    assert not is_annex_iii_clause(row(EU_AI_ACT, "Art. 50(1)"))
    assert not is_annex_iii_clause(row(EU_AI_ACT, "Art. 8"))
    assert not is_annex_iii_clause(row("GDPR", "Art. 12"))
    assert not is_annex_iii_clause(row(EU_AI_ACT, "Annex IV"))


def test_area_declaration_validated_and_echoed() -> None:
    d = classify_annex_iii(_manifest(), annex_iii_area="5").to_dict()
    assert d["annex_iii_area"] == "5"
    assert d["annex_iii_area_name"] == ANNEX_III_AREAS["5"]
    assert classify_annex_iii(_manifest()).to_dict()["annex_iii_area_name"] is None
    with pytest.raises(ManifestError):
        classify_annex_iii(_manifest(), annex_iii_area="9")


def test_invalid_manifest_rejected() -> None:
    with pytest.raises(ValueError):
        classify_annex_iii(AgentManifest(name="", tools=[]))


def test_mcp_path_yields_same_placement() -> None:
    payload = classify_payload("credit-agent", _TOOLS, ["EU"])
    got = classify_annex_iii(_manifest())
    cls = payload["classification"]
    assert (cls["autonomy"], cls["blast"], cls["intensity"]) == (
        got.autonomy, got.blast, got.intensity)


def test_to_dict_is_json_serialisable() -> None:
    data = json.loads(json.dumps(classify_annex_iii(_manifest()).to_dict()))
    assert data["controls"]
    assert data["controls"][0]["framework"] == EU_AI_ACT
