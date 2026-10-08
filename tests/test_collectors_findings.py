"""Tests for cage.collectors.findings."""

from __future__ import annotations

import json

import pytest

from cage.collectors import load_findings, load_manifest
from cage.manifest import ManifestError

# The exact example finding line from collector-findings-schema.md section 2.4.
SCHEMA_EXAMPLE_LINE = (
    '{"schema_version":"1","run_id":"ENG-2026-0481-run-01","finding_id":"aff-azure-caf-'
    'wellarchitected:caf.security.identity-baseline-mfa:8a3f...9b21:rg-corp:7e2c","pack":'
    '{"name":"aff-azure-caf-wellarchitected","version":"1.4.2","sha256":"f3a1..."},'
    '"control_id":"caf.security.identity-baseline-mfa","mappings":{"iso27001":["A.5.17",'
    '"A.8.5"],"caf":["Security.IdentityBaseline.MFA"],"ncsc-caf":["B2.a"],"nist-csf-2":'
    '["PR.AA-03"]},"status":"fail","severity":"high","maturity_level":2,"target":'
    '{"cloud":"azure","tenant_or_account":"8a3f...9b21","resource_type":'
    '"microsoft.authorization/policyAssignments","resource_id":"rg-corp/mfa-baseline",'
    '"region":"global"},"observed_at":"2026-05-13T11:42:08Z","description":"MFA Conditional '
    'Access policy not assigned to all privileged roles in tenant 8a3f...9b21.","evidence":'
    '[{"kind":"api-response","path":"evidence/aff-azure-caf-wellarchitected/'
    'caf.security.identity-baseline-mfa/policy-state-001.json","sha256":"7e2c...",'
    '"redaction":"none"}],"recommendation":"Assign baseline Conditional Access MFA policy to '
    'PIM-eligible roles; verify with Workbook entry IDB-01.","effort":"s","confidence":0.92,'
    '"narrative_hints":{"section":"identity","report":"stage7-cspa"}}'
)


def _finding(**over):
    base = {
        "schema_version": "1",
        "run_id": "R-1",
        "finding_id": "p:c:1",
        "pack": {"name": "p", "version": "1.0.0", "sha256": "abc"},
        "control_id": "c",
        "mappings": {},
        "status": "pass",
        "severity": "low",
        "maturity_level": None,
        "target": {
            "cloud": "azure",
            "tenant_or_account": "t",
            "resource_type": "rt",
            "resource_id": "rid",
            "region": "global",
        },
        "observed_at": "2026-05-13T11:42:08Z",
        "description": "ok",
        "evidence": [],
    }
    base.update(over)
    return base


def _write(tmp_path, lines, name="findings.jsonl"):
    p = tmp_path / name
    p.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return p


def test_valid_single_finding_parses(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding())])
    (f,) = load_findings(p)
    assert f.run_id == "R-1"
    assert f.pack.name == "p"
    assert f.maturity_level is None
    assert f.recommendation is None
    assert f.related_findings == []


def test_schema_example_line_parses_to_right_fields(tmp_path):
    p = _write(tmp_path, [SCHEMA_EXAMPLE_LINE])
    (f,) = load_findings(p)
    assert f.run_id == "ENG-2026-0481-run-01"
    assert f.finding_id.startswith("aff-azure-caf-wellarchitected:caf.security.identity-")
    assert f.pack.version == "1.4.2"
    assert f.control_id == "caf.security.identity-baseline-mfa"
    assert f.mappings["iso27001"] == ["A.5.17", "A.8.5"]
    assert f.mappings["nist-csf-2"] == ["PR.AA-03"]
    assert (f.status, f.severity, f.maturity_level) == ("fail", "high", 2)
    assert f.target.cloud == "azure"
    assert f.target.resource_id == "rg-corp/mfa-baseline"
    assert f.observed_at == "2026-05-13T11:42:08Z"
    assert f.evidence[0].kind == "api-response"
    assert f.evidence[0].redaction == "none"
    assert f.effort == "s"
    assert f.confidence == 0.92
    assert f.narrative_hints == {"section": "identity", "report": "stage7-cspa"}


def test_unknown_schema_version_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding()), json.dumps(_finding(schema_version="2"))])
    with pytest.raises(ManifestError) as exc:
        load_findings(p)
    assert exc.value.path == "findings.jsonl:2.schema_version"
    assert "'2'" in str(exc.value)


def test_non_string_schema_version_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding(schema_version=1))])
    with pytest.raises(ManifestError, match="schema_version"):
        load_findings(p)


def test_malformed_line_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding()), "{not json"])
    with pytest.raises(ManifestError) as exc:
        load_findings(p)
    assert exc.value.path == "findings.jsonl:2"
    assert isinstance(exc.value, ValueError)


def test_blank_line_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding()), "", json.dumps(_finding(finding_id="x"))])
    with pytest.raises(ManifestError, match="blank line") as exc:
        load_findings(p)
    assert exc.value.path == "findings.jsonl:2"


def test_duplicate_finding_id_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding()), json.dumps(_finding())])
    with pytest.raises(ManifestError, match="duplicate finding_id"):
        load_findings(p)


def test_crlf_rejected(tmp_path):
    p = tmp_path / "findings.jsonl"
    p.write_bytes((json.dumps(_finding()) + "\r\n").encode("utf-8"))
    with pytest.raises(ManifestError, match="LF"):
        load_findings(p)


def test_mixed_run_ids_rejected(tmp_path):
    p = _write(tmp_path, [json.dumps(_finding()), json.dumps(_finding(finding_id="b", run_id="R-2"))])
    with pytest.raises(ManifestError, match="run_id"):
        load_findings(p)


@pytest.mark.parametrize(
    "over, where",
    [
        ({"status": "maybe"}, "status"),
        ({"severity": "urgent"}, "severity"),
        ({"maturity_level": 7}, "maturity_level"),
        ({"maturity_level": True}, "maturity_level"),
        ({"confidence": 1.5}, "confidence"),
        ({"effort": "xxl"}, "effort"),
        ({"description": "x" * 201}, "description"),
        ({"pack": {"name": "p", "version": "1"}}, "pack"),
    ],
)
def test_field_violations_pinpointed(tmp_path, over, where):
    p = _write(tmp_path, [json.dumps(_finding(**over))])
    with pytest.raises(ManifestError) as exc:
        load_findings(p)
    assert where in exc.value.path


def test_missing_required_field(tmp_path):
    d = _finding()
    del d["target"]
    p = _write(tmp_path, [json.dumps(d)])
    with pytest.raises(ManifestError, match="missing required field 'target'"):
        load_findings(p)


def test_sorted_by_finding_id_and_empty_file(tmp_path):
    p = _write(
        tmp_path,
        [json.dumps(_finding(finding_id="b")), json.dumps(_finding(finding_id="a"))],
    )
    assert [f.finding_id for f in load_findings(p)] == ["a", "b"]
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert load_findings(empty) == []


def test_loading_does_not_mutate_input(tmp_path):
    p = _write(tmp_path, [SCHEMA_EXAMPLE_LINE])
    before = p.read_bytes()
    mtime = p.stat().st_mtime_ns
    load_findings(p)
    assert p.read_bytes() == before
    assert p.stat().st_mtime_ns == mtime


def _manifest(**over):
    base = {
        "schema_version": "1",
        "run_id": "ENG-2026-0481-run-01",
        "engagement_id": "ENG-2026-0481",
        "customer": {"name": "Acme Bank plc", "azure_tenants": ["8a3f...9b21"]},
        "pack": {"name": "aff-azure-caf-wellarchitected", "version": "1.4.2", "sha256": "f3a1"},
        "operator": "op@example.com",
        "collected_at": "2026-05-13T11:42:08Z",
        "duration_seconds": 412,
        "api_operations": [{"service": "azure.policy", "operation": "x", "count": 7}],
        "data_classes": ["configuration"],
        "findings_count": {"pass": 84, "fail": 19},
        "sealing": {"model": "bedrock-only"},
    }
    base.update(over)
    return base


def test_manifest_loads(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_manifest()), encoding="utf-8")
    m = load_manifest(p)
    assert m.run_id == "ENG-2026-0481-run-01"
    assert m.pack.version == "1.4.2"
    assert m.findings_count == {"pass": 84, "fail": 19}
    assert m.duration_seconds == 412.0
    assert m.sealing["model"] == "bedrock-only"


def test_manifest_rejects_unknown_schema_version(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(_manifest(schema_version="2")), encoding="utf-8")
    with pytest.raises(ManifestError) as exc:
        load_manifest(p)
    assert exc.value.path == "manifest.json.schema_version"


def test_manifest_rejects_bad_json_and_non_object(tmp_path):
    p = tmp_path / "manifest.json"
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(p)
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(ManifestError, match="object"):
        load_manifest(p)
