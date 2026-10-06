"""Regional control profiles: resolve which jurisdictions' controls attach.

An organisation selects the jurisdictions its data passes through (UK, Europe,
the USA, or any combination) and the harness scopes the *cited regulatory
controls* to that selection. This never changes a gate outcome - a dangerous
miss BLOCKs under every profile - it only decides which named controls are
listed alongside the classification and the scorecard.

Two ways to select:

* **Region presets** (``--jurisdictions`` / ``--regions``): ``uk``, ``eu``,
  ``us``, ``all`` / ``global``. Presets are combinable (their regions union),
  and each regional preset includes the ``GLOBAL`` baseline (ISO/IEC 42001):

  =========  ===========================
  preset     regions
  =========  ===========================
  ``uk``     ``{UK, GLOBAL}``
  ``eu``     ``{EU, GLOBAL}``
  ``us``     ``{US, GLOBAL}``
  ``all``    ``{EU, UK, US, GLOBAL}``
  ``global`` ``{GLOBAL}``
  =========  ===========================

* **Explicit frameworks** (``--frameworks``): name frameworks by stable slug
  (``eu-ai-act``, ``gdpr``, ``uk-gdpr``, ``iso-42001``, ``pra-ss1-23``,
  ``sr-11-7``, ``nist-ai-rmf``). This selects exactly those frameworks - the
  ``GLOBAL`` baseline is included only if ``iso-42001`` is named.

Resolution precedence (first that is set wins): CLI argument > the
``CAGE_JURISDICTIONS`` environment variable > a config file (``[tool.cage]`` in
``cage.toml`` or ``pyproject.toml``) > the default (all jurisdictions).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import tomllib

from cage.regmap import (
    FRAMEWORK_SLUGS,
    GLOBAL_REGION,
    REGIONS,
    region_for_framework,
)

ENV_VAR = "CAGE_JURISDICTIONS"

ALL_REGIONS: frozenset[str] = frozenset(REGIONS)

# Region presets -> the regions they resolve to. Each regional preset carries the
# GLOBAL baseline; 'all' and 'global' are the explicit span / baseline-only ends.
REGION_PRESETS: dict[str, frozenset[str]] = {
    "uk": frozenset({"UK", GLOBAL_REGION}),
    "eu": frozenset({"EU", GLOBAL_REGION}),
    "us": frozenset({"US", GLOBAL_REGION}),
    "all": ALL_REGIONS,
    "global": frozenset({GLOBAL_REGION}),
}


@dataclass(frozen=True)
class JurisdictionProfile:
    """A resolved jurisdiction selection.

    ``regions`` is the active region set (always populated, for display and
    region filtering). ``frameworks`` is the explicit framework-name set when the
    selection was made by framework slug, else ``None`` (region selection).
    ``source`` records where the selection came from; ``label`` is a short human
    string for the CLI header.
    """

    regions: frozenset[str]
    frameworks: frozenset[str] | None
    source: str
    label: str

    @property
    def is_all(self) -> bool:
        """True when nothing is scoped (all regions, no framework restriction)."""
        return self.frameworks is None and self.regions == ALL_REGIONS

    @property
    def filter_kwargs(self) -> dict[str, set[str]]:
        """Keyword args for ``regmap.controls_for_*`` - empty when unscoped.

        An unscoped profile returns ``{}`` so the control accessors keep their
        exact default (emit all controls); otherwise it returns a ``frameworks``
        or ``jurisdictions`` set.
        """
        if self.frameworks is not None:
            return {"frameworks": set(self.frameworks)}
        if self.regions == ALL_REGIONS:
            return {}
        return {"jurisdictions": set(self.regions)}

    def describe(self) -> str:
        """One-line description for the output header."""
        regions = ", ".join(sorted(self.regions))
        return f"{self.label} (regions: {regions}; source: {self.source})"


def _tokenize(spec: str | Iterable[str]) -> list[str]:
    """Split a spec (string or iterable) into lowercase tokens on commas/space."""
    parts: list[str] = []
    items = [spec] if isinstance(spec, str) else list(spec)
    for item in items:
        for piece in str(item).replace(",", " ").split():
            piece = piece.strip().lower()
            if piece:
                parts.append(piece)
    return parts


def _profile_from_tokens(tokens: list[str], source: str) -> JurisdictionProfile:
    """Build a profile from classified tokens (all presets, or all frameworks)."""
    if not tokens:
        raise ValueError("empty jurisdiction selection")

    presets = [t for t in tokens if t in REGION_PRESETS]
    frameworks = [t for t in tokens if t in FRAMEWORK_SLUGS]
    unknown = [t for t in tokens if t not in REGION_PRESETS and t not in FRAMEWORK_SLUGS]
    if unknown:
        valid = ", ".join(sorted(REGION_PRESETS) + sorted(FRAMEWORK_SLUGS))
        raise ValueError(
            f"unknown jurisdiction/framework token(s): {', '.join(sorted(set(unknown)))}. "
            f"valid tokens: {valid}"
        )
    if presets and frameworks:
        raise ValueError(
            "cannot mix region presets and framework slugs in one selection; "
            "use one or the other"
        )

    if frameworks:
        names = {FRAMEWORK_SLUGS[t] for t in frameworks}
        regions = frozenset(region_for_framework(n) for n in names)
        label = "frameworks: " + ", ".join(sorted(set(frameworks)))
        return JurisdictionProfile(regions, frozenset(names), source, label)

    regions = frozenset().union(*(REGION_PRESETS[t] for t in presets))
    label = ", ".join(sorted(set(presets)))
    return JurisdictionProfile(regions, None, source, label)


def profile_from_spec(spec: str | Iterable[str], source: str = "cli") -> JurisdictionProfile:
    """Parse a jurisdiction spec (presets or framework slugs) into a profile."""
    return _profile_from_tokens(_tokenize(spec), source)


def profile_from_frameworks(spec: str | Iterable[str], source: str = "cli") -> JurisdictionProfile:
    """Parse an explicit framework-slug selection into a profile.

    Rejects region presets - this path is framework-only.
    """
    tokens = _tokenize(spec)
    if not tokens:
        raise ValueError("empty framework selection")
    bad = [t for t in tokens if t not in FRAMEWORK_SLUGS]
    if bad:
        valid = ", ".join(sorted(FRAMEWORK_SLUGS))
        raise ValueError(
            f"unknown framework slug(s): {', '.join(sorted(set(bad)))}. valid slugs: {valid}"
        )
    return _profile_from_tokens(tokens, source)


def default_profile() -> JurisdictionProfile:
    """The unscoped default: every jurisdiction, no framework restriction."""
    return JurisdictionProfile(ALL_REGIONS, None, "default", "all")


def _config_profile(config_dir: str | Path | None) -> JurisdictionProfile | None:
    """Read a ``[tool.cage]`` selection from cage.toml or pyproject.toml, if any.

    ``cage.toml`` takes precedence over ``pyproject.toml``; within a file,
    ``frameworks`` takes precedence over ``jurisdictions``.
    """
    base = Path(config_dir) if config_dir is not None else Path.cwd()
    for fname in ("cage.toml", "pyproject.toml"):
        path = base / fname
        if not path.exists():
            continue
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        tool = data.get("tool", {})
        cage_cfg = tool.get("cage", {}) if isinstance(tool, Mapping) else {}
        if not isinstance(cage_cfg, Mapping):
            continue
        if cage_cfg.get("frameworks"):
            return profile_from_frameworks(cage_cfg["frameworks"], source="config")
        if cage_cfg.get("jurisdictions"):
            return profile_from_spec(cage_cfg["jurisdictions"], source="config")
    return None


def resolve_profile(
    *,
    cli_jurisdictions: str | Iterable[str] | None = None,
    cli_frameworks: str | Iterable[str] | None = None,
    env: Mapping[str, str] | None = None,
    config_dir: str | Path | None = None,
) -> JurisdictionProfile:
    """Resolve the active jurisdiction profile by precedence.

    CLI argument (``--frameworks`` wins over ``--jurisdictions`` when both are
    given) > ``CAGE_JURISDICTIONS`` env var > ``[tool.cage]`` config file >
    default (all jurisdictions).
    """
    environ = os.environ if env is None else env

    if cli_frameworks:
        return profile_from_frameworks(cli_frameworks, source="cli")
    if cli_jurisdictions:
        return profile_from_spec(cli_jurisdictions, source="cli")

    env_val = environ.get(ENV_VAR)
    if env_val and env_val.strip():
        return profile_from_spec(env_val, source="env")

    cfg = _config_profile(config_dir)
    if cfg is not None:
        return cfg

    return default_profile()
