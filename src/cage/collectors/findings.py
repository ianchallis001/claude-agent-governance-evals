"""Read-only loader for a collector run's ``findings.jsonl`` and ``manifest.json``.

Input contract: a findings pack conforming to the collector findings schema
(``collector-findings-schema.md`` v1). A run folder holds ``manifest.json`` (run
metadata) and ``findings.jsonl`` (NDJSON, UTF-8, LF, one finding per line). This
module only ever *reads* those files; the pack belongs to the caller.

``schema_version`` is gated strictly: ``"1"`` is accepted and anything else is
refused rather than guessed at. Every failure raises
:class:`cage.manifest.ManifestError` with a ``path`` that pinpoints the fault, for
example ``findings.jsonl:3.pack.sha256`` (line 3 of the findings file) or
``manifest.json.run_id``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cage.manifest import ManifestError

SCHEMA_VERSION = "1"

STATUSES = frozenset({"pass", "fail", "partial", "not_applicable", "inconclusive"})
SEVERITIES = frozenset({"critical", "high", "medium", "low", "informational"})
EFFORTS = frozenset({"xs", "s", "m", "l", "xl"})
EVIDENCE_KINDS = frozenset(
    {"api-response", "query-result", "screenshot", "export", "log-extract", "computed"}
)
REDACTIONS = frozenset({"none", "partial", "sealed"})
MAX_DESCRIPTION = 200

_TARGET_FIELDS = ("cloud", "tenant_or_account", "resource_type", "resource_id", "region")


@dataclass(frozen=True)
class PackRef:
    """The producing pack: name, version and content hash."""

    name: str
    version: str
    sha256: str


@dataclass(frozen=True)
class Target:
    """What the finding is about."""

    cloud: str
    tenant_or_account: str
    resource_type: str
    resource_id: str
    region: str


@dataclass(frozen=True)
class Evidence:
    """A pointer to an evidence blob, relative to the run folder."""

    kind: str
    path: str
    sha256: str
    redaction: str


@dataclass(frozen=True)
class Finding:
    """One machine record from ``findings.jsonl``."""

    schema_version: str
    run_id: str
    finding_id: str
    pack: PackRef
    control_id: str
    mappings: dict[str, list[str]]
    status: str
    severity: str
    maturity_level: int | None
    target: Target
    observed_at: str
    description: str
    evidence: list[Evidence]
    recommendation: str | None = None
    effort: str | None = None
    confidence: float | None = None
    related_findings: list[str] = field(default_factory=list)
    narrative_hints: dict[str, Any] = field(default_factory=dict)
    regulator_quote_anchor: str | None = None


@dataclass(frozen=True)
class CollectorManifest:
    """Run metadata from ``manifest.json``."""

    schema_version: str
    run_id: str
    pack: PackRef
    collected_at: str
    engagement_id: str | None = None
    customer: dict[str, Any] = field(default_factory=dict)
    operator: str | None = None
    duration_seconds: float | None = None
    api_operations: list[dict[str, Any]] = field(default_factory=list)
    data_classes: list[str] = field(default_factory=list)
    findings_count: dict[str, int] = field(default_factory=dict)
    sealing: dict[str, Any] = field(default_factory=dict)


# --- field helpers ---------------------------------------------------------


def _join(path: str, key: str) -> str:
    return f"{path}.{key}"


def _need(data: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise ManifestError(f"missing required field {key!r}", path=path)
    return data[key]


def _str(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"must be a string, got {type(value).__name__}", path=path)
    if not value.strip():
        raise ManifestError("must be a non-empty string", path=path)
    return value


def _obj(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"must be an object, got {type(value).__name__}", path=path)
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"must be a list, got {type(value).__name__}", path=path)
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"must be a number, got {type(value).__name__}", path=path)
    return float(value)


def _enum(value: Any, allowed: frozenset[str], path: str) -> str:
    text = _str(value, path)
    if text not in allowed:
        raise ManifestError(
            f"invalid value {text!r}; must be one of: {', '.join(sorted(allowed))}", path=path
        )
    return text


def _gate_version(data: Mapping[str, Any], path: str) -> str:
    raw = _need(data, "schema_version", path)
    if not isinstance(raw, str) or raw != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported schema_version {raw!r}; only {SCHEMA_VERSION!r} is accepted",
            path=_join(path, "schema_version"),
        )
    return raw


def _pack(value: Any, path: str) -> PackRef:
    obj = _obj(value, path)
    return PackRef(
        name=_str(_need(obj, "name", path), _join(path, "name")),
        version=_str(_need(obj, "version", path), _join(path, "version")),
        sha256=_str(_need(obj, "sha256", path), _join(path, "sha256")),
    )


def _target(value: Any, path: str) -> Target:
    obj = _obj(value, path)
    vals = {k: _str(_need(obj, k, path), _join(path, k)) for k in _TARGET_FIELDS}
    return Target(**vals)


def _evidence(value: Any, path: str) -> list[Evidence]:
    out: list[Evidence] = []
    for i, raw in enumerate(_list(value, path)):
        p = f"{path}[{i}]"
        obj = _obj(raw, p)
        out.append(
            Evidence(
                kind=_enum(_need(obj, "kind", p), EVIDENCE_KINDS, _join(p, "kind")),
                path=_str(_need(obj, "path", p), _join(p, "path")),
                sha256=_str(_need(obj, "sha256", p), _join(p, "sha256")),
                redaction=_enum(_need(obj, "redaction", p), REDACTIONS, _join(p, "redaction")),
            )
        )
    return out


def _mappings(value: Any, path: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for slug, ids in _obj(value, path).items():
        p = _join(path, str(slug))
        out[str(slug)] = [_str(x, f"{p}[{i}]") for i, x in enumerate(_list(ids, p))]
    return out


def _maturity(value: Any, path: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"must be an integer 1-6 or null, got {value!r}", path=path)
    if not 1 <= value <= 6:
        raise ManifestError(f"must be between 1 and 6 or null, got {value}", path=path)
    return value


def _opt_str(obj: Mapping[str, Any], key: str, path: str) -> str | None:
    return None if obj.get(key) is None else _str(obj[key], _join(path, key))


def _str_list(value: Any, path: str) -> list[str]:
    return [_str(x, f"{path}[{i}]") for i, x in enumerate(_list(value, path))]


def parse_finding(data: Any, path: str = "finding") -> Finding:
    """Validate one decoded finding object and build a :class:`Finding`."""
    obj = _obj(data, path)
    version = _gate_version(obj, path)

    description = _str(_need(obj, "description", path), _join(path, "description"))
    if len(description) > MAX_DESCRIPTION:
        raise ManifestError(
            f"must be at most {MAX_DESCRIPTION} characters, got {len(description)}",
            path=_join(path, "description"),
        )

    confidence = None
    if obj.get("confidence") is not None:
        confidence = _number(obj["confidence"], _join(path, "confidence"))
        if not 0.0 <= confidence <= 1.0:
            raise ManifestError(
                f"must be between 0 and 1, got {confidence}", path=_join(path, "confidence")
            )

    effort = None
    if obj.get("effort") is not None:
        effort = _enum(obj["effort"], EFFORTS, _join(path, "effort"))

    related: list[str] = []
    if obj.get("related_findings") is not None:
        related = _str_list(obj["related_findings"], _join(path, "related_findings"))

    hints: dict[str, Any] = {}
    if obj.get("narrative_hints") is not None:
        hints = dict(_obj(obj["narrative_hints"], _join(path, "narrative_hints")))

    return Finding(
        schema_version=version,
        run_id=_str(_need(obj, "run_id", path), _join(path, "run_id")),
        finding_id=_str(_need(obj, "finding_id", path), _join(path, "finding_id")),
        pack=_pack(_need(obj, "pack", path), _join(path, "pack")),
        control_id=_str(_need(obj, "control_id", path), _join(path, "control_id")),
        mappings=_mappings(_need(obj, "mappings", path), _join(path, "mappings")),
        status=_enum(_need(obj, "status", path), STATUSES, _join(path, "status")),
        severity=_enum(_need(obj, "severity", path), SEVERITIES, _join(path, "severity")),
        maturity_level=_maturity(_need(obj, "maturity_level", path), _join(path, "maturity_level")),
        target=_target(_need(obj, "target", path), _join(path, "target")),
        observed_at=_str(_need(obj, "observed_at", path), _join(path, "observed_at")),
        description=description,
        evidence=_evidence(_need(obj, "evidence", path), _join(path, "evidence")),
        recommendation=_opt_str(obj, "recommendation", path),
        effort=effort,
        confidence=confidence,
        related_findings=related,
        narrative_hints=hints,
        regulator_quote_anchor=_opt_str(obj, "regulator_quote_anchor", path),
    )


def parse_manifest(data: Any, path: str = "manifest.json") -> CollectorManifest:
    """Validate one decoded ``manifest.json`` object."""
    obj = _obj(data, path)
    version = _gate_version(obj, path)

    duration = None
    if obj.get("duration_seconds") is not None:
        duration = _number(obj["duration_seconds"], _join(path, "duration_seconds"))

    api_ops: list[dict[str, Any]] = []
    if obj.get("api_operations") is not None:
        ap = _join(path, "api_operations")
        api_ops = [
            dict(_obj(x, f"{ap}[{i}]")) for i, x in enumerate(_list(obj["api_operations"], ap))
        ]

    classes: list[str] = []
    if obj.get("data_classes") is not None:
        classes = _str_list(obj["data_classes"], _join(path, "data_classes"))

    counts: dict[str, int] = {}
    if obj.get("findings_count") is not None:
        cp = _join(path, "findings_count")
        for key, n in _obj(obj["findings_count"], cp).items():
            if isinstance(n, bool) or not isinstance(n, int) or n < 0:
                raise ManifestError(
                    f"must be a non-negative integer, got {n!r}", path=_join(cp, str(key))
                )
            counts[str(key)] = n

    customer: dict[str, Any] = {}
    if obj.get("customer") is not None:
        customer = dict(_obj(obj["customer"], _join(path, "customer")))
    sealing: dict[str, Any] = {}
    if obj.get("sealing") is not None:
        sealing = dict(_obj(obj["sealing"], _join(path, "sealing")))

    return CollectorManifest(
        schema_version=version,
        run_id=_str(_need(obj, "run_id", path), _join(path, "run_id")),
        pack=_pack(_need(obj, "pack", path), _join(path, "pack")),
        collected_at=_str(_need(obj, "collected_at", path), _join(path, "collected_at")),
        engagement_id=_opt_str(obj, "engagement_id", path),
        customer=customer,
        operator=_opt_str(obj, "operator", path),
        duration_seconds=duration,
        api_operations=api_ops,
        data_classes=classes,
        findings_count=counts,
        sealing=sealing,
    )


# --- loaders (read-only) ---------------------------------------------------


def load_manifest(path: str | Path) -> CollectorManifest:
    """Load and validate a collector run's ``manifest.json``. Never writes."""
    p = Path(path)
    label = p.name
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"not valid JSON: {exc}", path=label) from exc
    return parse_manifest(data, label)


def load_findings(path: str | Path) -> list[Finding]:
    """Load and validate a ``findings.jsonl`` file. Never writes.

    Findings are returned sorted by ``finding_id`` (the schema's deterministic
    order). Refuses: an unknown ``schema_version``, a blank or malformed line, a
    CR line ending (the contract is LF), a duplicate ``finding_id``, and a
    ``run_id`` that differs between lines. An empty file yields an empty list.
    """
    p = Path(path)
    label = p.name
    text = p.read_bytes().decode("utf-8")
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()  # the single trailing LF that terminates the last record

    findings: list[Finding] = []
    seen: dict[str, int] = {}
    run_id: str | None = None
    for n, line in enumerate(lines, start=1):
        where = f"{label}:{n}"
        if "\r" in line:
            raise ManifestError("CR found; findings.jsonl must use LF line endings", path=where)
        if not line.strip():
            raise ManifestError("blank line; every line must be one finding", path=where)
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"malformed JSON: {exc}", path=where) from exc
        finding = parse_finding(data, where)
        if finding.finding_id in seen:
            raise ManifestError(
                f"duplicate finding_id {finding.finding_id!r} (first seen on line "
                f"{seen[finding.finding_id]})",
                path=_join(where, "finding_id"),
            )
        seen[finding.finding_id] = n
        if run_id is None:
            run_id = finding.run_id
        elif finding.run_id != run_id:
            raise ManifestError(
                f"run_id {finding.run_id!r} differs from {run_id!r} on line 1",
                path=_join(where, "run_id"),
            )
        findings.append(finding)
    return sorted(findings, key=lambda f: f.finding_id)
