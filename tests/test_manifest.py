"""Tests for cage.manifest."""

from __future__ import annotations

import json

import pytest

from cage.manifest import AgentManifest, Tool, ToolKind, load_manifest


def test_toolkind_values():
    assert {k.value for k in ToolKind} == {
        "read_public", "read_sensitive", "write", "egress", "destructive", "code_exec",
    }


def test_tool_from_dict_roundtrip():
    t = Tool.from_dict(
        {"name": "x", "description": "d", "kind": "egress", "reversible": False}
    )
    assert t.kind is ToolKind.EGRESS
    assert t.reversible is False
    assert t.to_dict()["kind"] == "egress"


def test_tool_defaults_reversible_true():
    t = Tool.from_dict({"name": "x", "description": "d", "kind": "read_public"})
    assert t.reversible is True


def test_tool_from_dict_rejects_bad_kind():
    with pytest.raises(ValueError):
        Tool.from_dict({"name": "x", "description": "d", "kind": "nope"})


def test_manifest_from_dict_and_validate():
    m = AgentManifest.from_dict(
        {
            "name": "agent",
            "tools": [{"name": "a", "description": "d", "kind": "write"}],
        }
    )
    assert m.name == "agent"
    assert len(m.tools) == 1


def test_manifest_requires_name():
    with pytest.raises(ValueError):
        AgentManifest.from_dict({"tools": []})


def test_manifest_rejects_duplicate_tool_names():
    with pytest.raises(ValueError):
        AgentManifest.from_dict(
            {
                "name": "agent",
                "tools": [
                    {"name": "dup", "description": "d", "kind": "write"},
                    {"name": "dup", "description": "d", "kind": "write"},
                ],
            }
        )


def test_load_manifest_from_file(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps(
            {"name": "agent", "tools": [{"name": "a", "description": "d", "kind": "code_exec"}]}
        ),
        encoding="utf-8",
    )
    m = load_manifest(p)
    assert m.tools[0].kind is ToolKind.CODE_EXEC


def test_manifest_roundtrips_through_dict(sample_manifest):
    restored = AgentManifest.from_dict(sample_manifest.to_dict())
    assert restored.name == sample_manifest.name
    assert len(restored.tools) == len(sample_manifest.tools)
