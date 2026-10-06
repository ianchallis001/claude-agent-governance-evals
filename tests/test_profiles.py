"""Tests for cage.profiles - regional control profile resolution (P3-02)."""

from __future__ import annotations

import dataclasses

import pytest

from cage.profiles import (
    ALL_REGIONS,
    ENV_VAR,
    JurisdictionProfile,
    default_profile,
    profile_from_frameworks,
    profile_from_spec,
    resolve_profile,
)


# --------------------------------------------------------------------------- #
# Preset resolution + union                                                    #
# --------------------------------------------------------------------------- #
def test_region_presets_resolve():
    assert profile_from_spec("uk").regions == {"UK", "GLOBAL"}
    assert profile_from_spec("eu").regions == {"EU", "GLOBAL"}
    assert profile_from_spec("us").regions == {"US", "GLOBAL"}
    assert profile_from_spec("global").regions == {"GLOBAL"}
    assert profile_from_spec("all").regions == ALL_REGIONS


def test_presets_are_combinable_by_union():
    # "data passes through EU + US".
    prof = profile_from_spec("eu us")
    assert prof.regions == {"EU", "US", "GLOBAL"}
    # Comma-separated and a list form resolve the same way.
    assert profile_from_spec("eu,us").regions == prof.regions
    assert profile_from_spec(["eu", "us"]).regions == prof.regions


def test_preset_profile_is_region_mode():
    prof = profile_from_spec("uk")
    assert prof.frameworks is None
    assert prof.filter_kwargs == {"jurisdictions": {"UK", "GLOBAL"}}


def test_default_profile_is_all_and_unscoped():
    prof = default_profile()
    assert prof.is_all
    assert prof.regions == ALL_REGIONS
    # Unscoped -> no filter kwargs, so the control accessors keep default semantics.
    assert prof.filter_kwargs == {}


def test_all_preset_is_unscoped_filter():
    assert profile_from_spec("all").filter_kwargs == {}


# --------------------------------------------------------------------------- #
# Framework selection                                                          #
# --------------------------------------------------------------------------- #
def test_framework_slugs_resolve_to_names_and_regions():
    prof = profile_from_frameworks("gdpr sr-11-7")
    assert prof.frameworks == {"GDPR", "US SR 11-7"}
    assert prof.regions == {"EU", "US"}  # no GLOBAL auto-add
    assert prof.filter_kwargs == {"frameworks": {"GDPR", "US SR 11-7"}}


def test_all_seven_framework_slugs_are_valid():
    prof = profile_from_frameworks(
        "eu-ai-act gdpr uk-gdpr iso-42001 pra-ss1-23 sr-11-7 nist-ai-rmf"
    )
    assert prof.frameworks == {
        "EU AI Act", "GDPR", "UK GDPR", "ISO/IEC 42001",
        "PRA SS1/23", "US SR 11-7", "NIST AI RMF",
    }


def test_frameworks_reject_region_presets():
    with pytest.raises(ValueError):
        profile_from_frameworks("uk")


def test_mixing_presets_and_frameworks_is_rejected():
    with pytest.raises(ValueError):
        profile_from_spec("uk gdpr")


def test_unknown_token_is_rejected():
    with pytest.raises(ValueError):
        profile_from_spec("atlantis")


# --------------------------------------------------------------------------- #
# Precedence: CLI > env > config > default                                     #
# --------------------------------------------------------------------------- #
def test_cli_frameworks_override_cli_jurisdictions():
    prof = resolve_profile(cli_jurisdictions=["uk"], cli_frameworks=["gdpr"], env={})
    assert prof.frameworks == {"GDPR"}
    assert prof.source == "cli"


def test_cli_jurisdictions_beat_env(monkeypatch):
    prof = resolve_profile(cli_jurisdictions=["eu"], env={ENV_VAR: "us"})
    assert prof.regions == {"EU", "GLOBAL"}
    assert prof.source == "cli"


def test_env_used_when_no_cli(tmp_path):
    prof = resolve_profile(env={ENV_VAR: "us"}, config_dir=tmp_path)
    assert prof.regions == {"US", "GLOBAL"}
    assert prof.source == "env"


def test_config_file_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.cage]\njurisdictions = ['uk']\n", encoding="utf-8"
    )
    prof = resolve_profile(env={}, config_dir=tmp_path)
    assert prof.regions == {"UK", "GLOBAL"}
    assert prof.source == "config"


def test_config_file_cage_toml_frameworks(tmp_path):
    (tmp_path / "cage.toml").write_text(
        "[tool.cage]\nframeworks = ['gdpr', 'uk-gdpr']\n", encoding="utf-8"
    )
    prof = resolve_profile(env={}, config_dir=tmp_path)
    assert prof.frameworks == {"GDPR", "UK GDPR"}
    assert prof.source == "config"


def test_cage_toml_beats_pyproject(tmp_path):
    (tmp_path / "cage.toml").write_text(
        "[tool.cage]\njurisdictions = ['us']\n", encoding="utf-8"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.cage]\njurisdictions = ['uk']\n", encoding="utf-8"
    )
    prof = resolve_profile(env={}, config_dir=tmp_path)
    assert prof.regions == {"US", "GLOBAL"}


def test_default_when_nothing_set(tmp_path):
    prof = resolve_profile(env={}, config_dir=tmp_path)
    assert prof.is_all
    assert prof.source == "default"


def test_empty_env_falls_through_to_default(tmp_path):
    prof = resolve_profile(env={ENV_VAR: "   "}, config_dir=tmp_path)
    assert prof.source == "default"


def test_describe_is_ascii_and_informative():
    text = profile_from_spec("eu us").describe()
    text.encode("ascii")  # must not raise
    assert "EU" in text and "US" in text and "source" in text


def test_profile_is_frozen():
    prof = default_profile()
    assert isinstance(prof, JurisdictionProfile)
    with pytest.raises(dataclasses.FrozenInstanceError):
        prof.source = "mutated"  # type: ignore[misc]
