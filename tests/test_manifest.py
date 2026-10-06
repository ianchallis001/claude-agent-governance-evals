"""Tests for cage.manifest."""

from __future__ import annotations

import json

import pytest

from cage.manifest import AgentManifest, ManifestError, Tool, ToolKind, load_manifest


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


# --- P1-01: error taxonomy, nested edge cases, and strict mode ---

def test_manifest_error_is_a_value_error():
    # Existing `except ValueError` callers (e.g. the MCP server) must keep working.
    assert issubclass(ManifestError, ValueError)


def test_error_carries_path_to_the_nested_fault():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict(
            {"name": "a", "tools": [{"name": "t", "description": "d", "kind": "nope"}]}
        )
    assert exc.value.path == "tools[0].kind"
    assert "invalid kind" in exc.value.message


def test_invalid_kind_error_lists_the_valid_kinds():
    with pytest.raises(ManifestError) as exc:
        Tool.from_dict({"name": "x", "description": "d", "kind": "nope"})
    assert "code_exec" in str(exc.value)


def test_rejects_non_mapping_tool_entry():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict({"name": "a", "tools": ["not-an-object"]})
    assert exc.value.path == "tools[0]"


def test_rejects_blank_tool_name():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict(
            {"name": "a", "tools": [{"name": "  ", "description": "d", "kind": "write"}]}
        )
    assert "non-empty" in exc.value.message


def test_tools_must_be_a_list():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict({"name": "a", "tools": {"name": "x"}})
    assert exc.value.path == "tools"


def test_non_strict_coerces_reversible_and_ignores_extra_fields():
    # Default (lenient) mode stays backward compatible.
    m = AgentManifest.from_dict(
        {
            "name": "a",
            "tools": [
                {"name": "t", "description": "d", "kind": "write", "reversible": 1, "note": "x"}
            ],
            "owner": "ops",
        }
    )
    assert m.tools[0].reversible is True


def test_strict_rejects_unknown_manifest_field():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict({"name": "a", "tools": [], "owner": "ops"}, strict=True)
    assert "owner" in str(exc.value)


def test_strict_rejects_unknown_tool_field():
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict(
            {"name": "a", "tools": [{"name": "t", "description": "d", "kind": "write", "note": "x"}]},
            strict=True,
        )
    assert exc.value.path == "tools[0]"
    assert "note" in exc.value.message


def test_strict_rejects_non_bool_reversible():
    with pytest.raises(ManifestError) as exc:
        Tool.from_dict(
            {"name": "t", "description": "d", "kind": "write", "reversible": 1}, strict=True
        )
    assert exc.value.path == "tool.reversible"


def test_strict_rejects_non_string_name():
    with pytest.raises(ManifestError):
        AgentManifest.from_dict({"name": 7, "tools": []}, strict=True)


def test_strict_rejects_empty_description_but_lenient_allows_it():
    data = {"name": "t", "description": "", "kind": "write"}
    # lenient: tolerated
    assert Tool.from_dict(data).description == ""
    # strict: rejected
    with pytest.raises(ManifestError):
        Tool.from_dict(data, strict=True)


def test_strict_round_trips_a_clean_manifest(sample_manifest):
    # to_dict() output carries only known fields, so it passes strict parsing.
    restored = AgentManifest.from_dict(sample_manifest.to_dict(), strict=True)
    assert len(restored.tools) == len(sample_manifest.tools)


def test_load_manifest_strict_from_file(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps({"name": "a", "tools": [], "extra": 1}), encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        load_manifest(p, strict=True)
    # same file loads fine in the default lenient mode
    assert load_manifest(p).name == "a"


def test_load_manifest_reports_bad_json_as_manifest_error(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError) as exc:
        load_manifest(p)
    assert "not valid JSON" in str(exc.value)


def test_validate_catches_duplicate_built_in_code():
    m = AgentManifest(
        name="a",
        tools=[
            Tool("dup", "d", ToolKind.WRITE),
            Tool("dup", "d", ToolKind.WRITE),
        ],
    )
    with pytest.raises(ManifestError) as exc:
        m.validate()
    assert exc.value.path == "tools[1].name"
