"""cage - Claude Agent Governance Evals.

A regulator-grounded evaluation harness and governance classifier for Claude
tool-use agents. The agent under test classifies another agent's autonomy x
blast-radius governance intensity; the eval harness measures its own judges,
refuses to gate inside their noise floor, and re-validates on model-version
drift.

This top-level package deliberately imports only the lightweight, dependency-free
foundations (models, manifest, classification, regulatory mapping). The eval
engine lives under ``cage.evals`` and is imported on demand; the live-API agent
(``cage.agent``) and the MCP server (``cage.mcp_server``) import their third-party
dependencies (``anthropic`` / ``mcp``) lazily, so importing ``cage`` never
requires a network client or an API key.

Authoritative design: see ``SPEC.md`` at the repository root.
"""

from __future__ import annotations

from cage.classify import (
    AutonomyLevel,
    BlastRadius,
    Classification,
    GovernanceIntensity,
    ToolEvidence,
    classify_manifest,
    intensity_for,
)
from cage.manifest import AgentManifest, ManifestError, Tool, ToolKind, load_manifest
from cage.models import (
    AGENT_MODEL,
    ANTHROPIC_VERSION,
    JUDGE_MODEL,
    model_fingerprint,
)
from cage.regmap import (
    ControlRow,
    Dimension,
    controls_for_dimension,
    controls_for_intensity,
)

__version__ = "0.1.0"

__all__ = [
    # models
    "AGENT_MODEL",
    "ANTHROPIC_VERSION",
    "JUDGE_MODEL",
    "AgentManifest",
    # classify
    "AutonomyLevel",
    "BlastRadius",
    "Classification",
    "ControlRow",
    # regmap
    "Dimension",
    "GovernanceIntensity",
    "ManifestError",
    "Tool",
    "ToolEvidence",
    # manifest
    "ToolKind",
    "__version__",
    "classify_manifest",
    "controls_for_dimension",
    "controls_for_intensity",
    "intensity_for",
    "load_manifest",
    "model_fingerprint",
]
