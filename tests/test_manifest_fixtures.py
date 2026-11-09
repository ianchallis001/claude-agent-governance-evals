"""P1-01 fixture suite: every malformed manifest on disk is rejected with a
specific, path-pinpointed message, and every valid one round-trips stably.

The fixtures live under ``tests/fixtures/manifests/``:

* ``invalid/``        - rejected in the default (lenient) mode, so rejected always.
* ``strict_invalid/`` - accepted in lenient mode, rejected only under ``strict=True``.
* ``valid/``          - load and ``to_dict`` -> ``from_dict`` round-trip stably in both modes.

Each malformed fixture is listed in ``EXPECT`` with the ``ManifestError.path`` and a
message fragment it must raise. A fixture with no ``EXPECT`` entry fails the suite, so
adding a fixture file without pinning its expected error is caught here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cage.manifest import AgentManifest, ManifestError, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"

# filename -> (expected ManifestError.path, message fragment)
EXPECT: dict[str, tuple[str | None, str]] = {
    # invalid/ - rejected in lenient mode
    "top_level_not_object.json": (None, "must be an object at the top level"),
    "missing_name.json": (None, "missing required field 'name'"),
    "tools_not_list.json": ("tools", "must be a list"),
    "tool_not_object.json": ("tools[0]", "tool must be an object"),
    "tool_missing_kind.json": ("tools[0]", "missing required field 'kind'"),
    "tool_bad_kind.json": ("tools[0].kind", "invalid kind"),
    "tool_blank_name.json": ("tools[0].name", "non-empty string"),
    "blank_agent_name.json": ("name", "non-empty string"),
    "duplicate_tool_name.json": ("tools[1].name", "duplicate tool name"),
    # strict_invalid/ - rejected only under strict=True
    "manifest_unknown_field.json": (None, "unknown field(s): owner"),
    "tool_unknown_field.json": ("tools[0]", "unknown field(s): danger"),
    "manifest_name_not_string.json": ("name", "name must be a string"),
    "tool_reversible_not_bool.json": ("tools[0].reversible", "must be a boolean"),
    "tool_empty_description.json": ("tools[0].description", "non-empty string"),
}


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _ids(directory: str) -> list[str]:
    return sorted(p.name for p in (FIXTURES / directory).glob("*.json"))


@pytest.mark.parametrize("name", _ids("invalid"))
def test_invalid_fixture_rejected_with_specific_message(name: str) -> None:
    assert name in EXPECT, f"fixture {name} has no pinned expectation in EXPECT"
    expected_path, fragment = EXPECT[name]
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict(_load(FIXTURES / "invalid" / name))
    assert exc.value.path == expected_path
    assert fragment in exc.value.message


@pytest.mark.parametrize("name", _ids("strict_invalid"))
def test_strict_only_fixture_passes_lenient_but_strict_rejects(name: str) -> None:
    assert name in EXPECT, f"fixture {name} has no pinned expectation in EXPECT"
    data = _load(FIXTURES / "strict_invalid" / name)
    # Lenient mode accepts it ...
    AgentManifest.from_dict(data)
    # ... strict mode rejects it with the pinned path + message.
    expected_path, fragment = EXPECT[name]
    with pytest.raises(ManifestError) as exc:
        AgentManifest.from_dict(data, strict=True)
    assert exc.value.path == expected_path
    assert fragment in exc.value.message


@pytest.mark.parametrize("name", _ids("valid"))
def test_valid_fixture_loads_and_round_trips(name: str) -> None:
    data = _load(FIXTURES / "valid" / name)
    manifest = AgentManifest.from_dict(data)
    # Round-trip is stable: to_dict -> from_dict yields the same structure.
    assert AgentManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()
    # A valid fixture also passes strict validation unchanged.
    assert AgentManifest.from_dict(data, strict=True).to_dict() == manifest.to_dict()


def test_valid_fixture_loads_from_file() -> None:
    # Exercise the JSON file loader itself, not just from_dict.
    manifest = load_manifest(FIXTURES / "valid" / "minimal.json")
    assert manifest.name == "reader"
    assert [t.name for t in manifest.tools] == ["lookup"]


def test_every_malformed_fixture_has_an_expectation() -> None:
    # Guards the DoD: every malformed fixture on disk is pinned in EXPECT.
    on_disk = set(_ids("invalid")) | set(_ids("strict_invalid"))
    assert on_disk == set(EXPECT), (
        "malformed fixtures and EXPECT are out of sync: "
        f"only on disk={on_disk - set(EXPECT)}, only in EXPECT={set(EXPECT) - on_disk}"
    )
