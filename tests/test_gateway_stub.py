"""Tests for cage.gateway_stub - the reference enforcement gateway.

Pure stdlib: builds Classifications by hand (and via the real classifier) and
asserts the allow/deny decisions, the intensity-aware human-in-the-loop gate, the
ceiling, and the audit log. No SDK, no network.
"""

from __future__ import annotations

from cage.classify import (
    AutonomyLevel,
    BlastRadius,
    Classification,
    GovernanceIntensity,
    ToolEvidence,
    classify_manifest,
)
from cage.gateway_stub import Decision, GovernanceGateway
from cage.manifest import AgentManifest, Tool, ToolKind


def _classification(intensity, tool_names=("read_feed", "post_update")):
    """Hand-build a Classification at a chosen intensity with named tools."""
    evidence = [
        ToolEvidence(tool=n, kind="write", blast_contribution=BlastRadius.BR3, reversible=True)
        for n in tool_names
    ]
    return Classification(
        agent_name="Agent",
        autonomy=AutonomyLevel.L3,
        blast=BlastRadius.BR3,
        intensity=intensity,
        evidence=evidence,
    )


def test_allow_listed_tool_at_low_intensity_is_allowed():
    cls = _classification(GovernanceIntensity.LOW)
    gw = GovernanceGateway(cls, allow_list={"read_feed", "post_update"})
    d = gw.authorize("read_feed")
    assert d.decision is Decision.ALLOW
    assert d.allowed is True
    assert d.requires_human is False
    assert "permitted" in d.reason


def test_tool_not_on_allow_list_is_denied():
    cls = _classification(GovernanceIntensity.LOW, tool_names=("read_feed", "post_update"))
    gw = GovernanceGateway(cls, allow_list={"read_feed"})
    d = gw.authorize("post_update")
    assert d.decision is Decision.DENY
    assert "declared but not on the allow-list" in d.reason


def test_unknown_tool_is_denied_with_distinct_reason():
    cls = _classification(GovernanceIntensity.LOW)
    gw = GovernanceGateway(cls, allow_list={"read_feed"})
    d = gw.authorize("ssh_into_prod")
    assert d.decision is Decision.DENY
    assert "unknown" in d.reason


def test_critical_intensity_requires_human_approval():
    cls = _classification(GovernanceIntensity.CRITICAL)
    gw = GovernanceGateway(cls, allow_list={"read_feed"})

    denied = gw.authorize("read_feed")
    assert denied.decision is Decision.DENY
    assert denied.requires_human is True
    assert "requires human approval" in denied.reason

    allowed = gw.authorize("read_feed", human_approved=True)
    assert allowed.decision is Decision.ALLOW
    assert allowed.requires_human is True


def test_hitl_from_threshold_is_configurable():
    cls = _classification(GovernanceIntensity.HIGH)
    # Lower the HITL bar to HIGH: now a HIGH agent needs approval too.
    gw = GovernanceGateway(
        cls, allow_list={"read_feed"}, hitl_from=GovernanceIntensity.HIGH
    )
    assert gw.authorize("read_feed").decision is Decision.DENY
    assert gw.authorize("read_feed", human_approved=True).decision is Decision.ALLOW


def test_max_intensity_ceiling_denies_everything():
    cls = _classification(GovernanceIntensity.CRITICAL)
    gw = GovernanceGateway(
        cls, allow_list={"read_feed"}, max_intensity=GovernanceIntensity.HIGH
    )
    d = gw.authorize("read_feed", human_approved=True)
    assert d.decision is Decision.DENY
    assert "exceeds gateway ceiling" in d.reason


def test_audit_log_records_every_decision_in_order():
    cls = _classification(GovernanceIntensity.LOW)
    gw = GovernanceGateway(cls, allow_list={"read_feed"})
    gw.authorize("read_feed")
    gw.authorize("post_update")
    gw.authorize("read_feed")
    assert len(gw.audit_log) == 3
    assert [d.decision for d in gw.audit_log] == [
        Decision.ALLOW,
        Decision.DENY,
        Decision.ALLOW,
    ]
    # Each entry round-trips to a serialisable dict.
    row = gw.audit_log[0].to_dict()
    assert row["tool"] == "read_feed"
    assert row["intensity"] == "LOW"


def test_gateway_consumes_real_classification():
    # End to end: classify a dangerous manifest, then enforce against it.
    manifest = AgentManifest(
        name="Remediator",
        tools=[
            Tool("read_logs", "read logs", ToolKind.READ_SENSITIVE),
            Tool("delete_vm", "delete a VM", ToolKind.DESTRUCTIVE, reversible=False),
        ],
    )
    cls = classify_manifest(manifest)
    assert cls.blast is BlastRadius.BR5
    gw = GovernanceGateway(cls, allow_list={"read_logs", "delete_vm"})

    # delete_vm is allow-listed but, at this intensity, needs human sign-off.
    if cls.intensity >= GovernanceIntensity.CRITICAL:
        assert gw.authorize("delete_vm").decision is Decision.DENY
        assert gw.authorize("delete_vm", human_approved=True).decision is Decision.ALLOW
    else:
        assert gw.authorize("delete_vm").decision is Decision.ALLOW

    # A tool the manifest never declared is always denied.
    assert gw.authorize("format_disk").decision is Decision.DENY
