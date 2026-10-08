"""Tests for cage.collectors.assessment - the assessment-governance gate.

Proves the three structural checks and their aggregation:
(a) an unanchored or unresolvable rating BLOCKs (cannot be outvoted);
(b) a prompt-template-hash change BLOCKs via drift, exactly as a model change;
(c) an anchored + pinned + reproducible pack PASSes;
plus BLOCK > FIX > PASS precedence, the 0/1/2 exit-code mapping, and that the
module imports with neither the anthropic nor the mcp SDK installed.
"""

from __future__ import annotations

import importlib
import sys

import pytest

from cage.collectors import assessment as asmt
from cage.collectors.assessment import (
    AssessmentConfig,
    AssessmentGate,
    Rating,
    aggregate_verdict,
    check_anchoring,
    check_reproducibility,
    evaluate_assessment,
    parse_refs,
)
from cage.collectors.findings import parse_finding
from cage.evals.judge import VerdictLabel

TEMPLATE = "Summarise the pack into a client-facing rating.\n{findings}\n"


def _finding(finding_id="p:c:1", pack_name="p", version="1.0.0"):
    data = {
        "schema_version": "1",
        "run_id": "R-1",
        "finding_id": finding_id,
        "pack": {"name": pack_name, "version": version, "sha256": "abc"},
        "control_id": "c",
        "mappings": {},
        "status": "fail",
        "severity": "high",
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
    return parse_finding(data)


def _ref(pack_name="p", version="1.0.0", finding_id="p:c:1"):
    return f"[ref: {pack_name}@{version}/{finding_id}]"


def _config(template_text=TEMPLATE, model="claude-sonnet-5-5"):
    return AssessmentConfig(model=model, template_text=template_text)


def _gate(findings, config=None):
    config = config or _config()
    return AssessmentGate(config, config.fingerprint(), findings)


# --- ref parsing ------------------------------------------------------------


def test_parse_refs_splits_pack_version_finding_id():
    (ref,) = parse_refs(f"rated HIGH {_ref()}")
    assert ref.well_formed
    assert ref.key == ("p", "1.0.0", "p:c:1")


def test_parse_refs_preserves_finding_id_with_colons_and_slashes():
    token = _ref(finding_id="pack:ctrl:8a3f:rg-corp/sub:7e2c")
    (ref,) = parse_refs(f"x {token}")
    assert ref.finding_id == "pack:ctrl:8a3f:rg-corp/sub:7e2c"
    assert ref.version == "1.0.0"


def test_parse_refs_malformed_token_not_well_formed():
    (ref,) = parse_refs("x [ref: no-at-no-slash]")
    assert ref.well_formed is False
    assert ref.key == ("", "", "")


# --- (a) evidence anchoring -------------------------------------------------


def test_unanchored_rating_blocks():
    findings = [_finding()]
    ratings = [Rating("r1", "This estate rates HIGH risk.")]  # no [ref:] token
    result = evaluate_assessment(_config(), _config().fingerprint(), findings, ratings)
    assert result.gate is VerdictLabel.BLOCK
    assert result.exit_code == 2
    assert "evidence_anchoring" in result.block_reasons


def test_unresolvable_ref_blocks():
    findings = [_finding(finding_id="p:c:1")]
    ratings = [Rating("r1", f"HIGH {_ref(finding_id='p:c:DOES-NOT-EXIST')}")]
    anchoring = check_anchoring(ratings, findings)
    assert anchoring.passed is False
    assert anchoring.unresolved and anchoring.unresolved[0]["rating_id"] == "r1"
    assert anchoring.verdict is VerdictLabel.BLOCK


def test_resolving_ref_anchors_even_with_a_second_dangling_ref():
    findings = [_finding(finding_id="p:c:1")]
    ratings = [
        Rating("r1", f"HIGH {_ref(finding_id='p:c:1')} and {_ref(finding_id='ghost')}")
    ]
    assert check_anchoring(ratings, findings).passed is True


def test_anchoring_cannot_be_outvoted_by_many_good_ratings():
    findings = [_finding()]
    good = [Rating(f"g{i}", f"ok {_ref()}") for i in range(10)]
    bad = [Rating("bad", "ungrounded claim")]
    result = evaluate_assessment(_config(), _config().fingerprint(), findings, good + bad)
    assert result.gate is VerdictLabel.BLOCK


# --- (b) template-hash drift ------------------------------------------------


def test_template_hash_change_blocks_via_drift():
    findings = [_finding()]
    ratings = [Rating("r1", f"ok {_ref()}")]
    pinned = _config(template_text="ORIGINAL TEMPLATE").fingerprint()
    # Same model + API version, but the prompt template text changed.
    changed = _config(template_text="EDITED TEMPLATE")
    gate = AssessmentGate(changed, pinned, findings)
    assert gate.check_drift().drifted is True
    result = gate.evaluate(ratings)
    assert result.gate is VerdictLabel.BLOCK
    assert "template_drift" in result.block_reasons


def test_model_id_change_blocks_via_the_same_fingerprint():
    findings = [_finding()]
    ratings = [Rating("r1", f"ok {_ref()}")]
    pinned = _config(model="claude-sonnet-5-5").fingerprint()
    changed = _config(model="claude-opus-5-5")  # same template, different model
    result = AssessmentGate(changed, pinned, findings).evaluate(ratings)
    assert result.gate is VerdictLabel.BLOCK


def test_unchanged_config_does_not_drift():
    findings = [_finding()]
    gate = _gate(findings)
    assert gate.check_drift().drifted is False


# --- (c) reproducibility envelope -------------------------------------------


def test_reproducible_rederivation_is_within_envelope():
    ratings = [Rating("r1", f"ok {_ref()}", value="HIGH")]
    repro = check_reproducibility(ratings, list(ratings))
    assert repro.ok is True
    assert repro.change_rate == 0.0
    assert repro.verdict is VerdictLabel.PASS


def test_non_reproducible_rederivation_is_a_fix_not_a_block():
    findings = [_finding()]
    ratings = [Rating("r1", f"ok {_ref()}", value="HIGH")]
    rederived = [Rating("r1", f"ok {_ref()}", value="LOW")]  # same config, different answer
    repro = check_reproducibility(ratings, rederived)
    assert repro.ok is False
    assert repro.verdict is VerdictLabel.FIX
    result = _gate(findings).evaluate(ratings, rederived)
    assert result.gate is VerdictLabel.FIX
    assert result.exit_code == 1


def test_tolerance_absorbs_a_small_change():
    base = [Rating(f"r{i}", f"ok {_ref()}", value=i) for i in range(10)]
    rederived = [Rating(f"r{i}", f"ok {_ref()}", value=i) for i in range(10)]
    rederived[0] = Rating("r0", f"ok {_ref()}", value=999)  # 1/10 changed
    gate = AssessmentGate(
        _config(), _config().fingerprint(), [_finding()], reproducibility_tolerance=0.1
    )
    assert gate.evaluate(base, rederived).gate is VerdictLabel.PASS


# --- aggregate PASS and precedence ------------------------------------------


def test_anchored_pinned_reproducible_pack_passes():
    findings = [_finding(finding_id="p:c:1"), _finding(finding_id="p:c:2")]
    ratings = [
        Rating("r1", f"control 1 HIGH {_ref(finding_id='p:c:1')}", value="HIGH"),
        Rating("r2", f"control 2 LOW {_ref(finding_id='p:c:2')}", value="LOW"),
    ]
    result = _gate(findings).evaluate(ratings, list(ratings))
    assert result.gate is VerdictLabel.PASS
    assert result.exit_code == 0
    assert result.ok is True


def test_aggregate_verdict_precedence():
    V = VerdictLabel
    make = lambda v: asmt.ComponentVerdict(name="x", verdict=v)  # noqa: E731
    assert aggregate_verdict([make(V.PASS), make(V.PASS)]) is V.PASS
    assert aggregate_verdict([make(V.PASS), make(V.FIX)]) is V.FIX
    assert aggregate_verdict([make(V.PASS), make(V.FIX), make(V.BLOCK)]) is V.BLOCK
    assert aggregate_verdict([]) is V.PASS


def test_block_beats_fix_in_a_full_run():
    # An unanchored rating (BLOCK) alongside a non-reproducible re-derivation
    # (FIX): the BLOCK must win.
    findings = [_finding()]
    ratings = [Rating("r1", "ungrounded")]
    rederived = [Rating("r1", "different ungrounded")]
    result = _gate(findings).evaluate(ratings, rederived)
    assert result.gate is VerdictLabel.BLOCK
    assert result.exit_code == 2


def test_exit_code_mapping_matches_runner():
    assert asmt._SEVERITY == {
        VerdictLabel.PASS: 0,
        VerdictLabel.FIX: 1,
        VerdictLabel.BLOCK: 2,
    }


# --- template_hash edge ------------------------------------------------------


def test_template_hash_is_deterministic_and_type_checked():
    assert asmt.template_hash("abc") == asmt.template_hash("abc")
    assert asmt.template_hash("abc") != asmt.template_hash("abd")
    with pytest.raises(TypeError):
        asmt.template_hash(123)


# --- SDK independence --------------------------------------------------------


def test_module_imports_without_anthropic_or_mcp():
    # Force `import anthropic` / `import mcp` to fail, then reload the module and
    # its import chain: none of it may touch an optional SDK at module load.
    blocked = {}
    for name in list(sys.modules):
        if name == "anthropic" or name == "mcp" or name.startswith(("anthropic.", "mcp.")):
            blocked[name] = sys.modules.pop(name)
    sys.modules["anthropic"] = None  # type: ignore[assignment]
    sys.modules["mcp"] = None  # type: ignore[assignment]
    try:
        reloaded = importlib.reload(asmt)
        assert hasattr(reloaded, "AssessmentGate")
        assert hasattr(reloaded, "check_anchoring")
        with pytest.raises(ImportError):
            import anthropic  # noqa: F401
        with pytest.raises(ImportError):
            import mcp  # noqa: F401
    finally:
        del sys.modules["anthropic"]
        del sys.modules["mcp"]
        sys.modules.update(blocked)
        importlib.reload(asmt)
