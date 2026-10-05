"""MCP server publishing the governance classifier over the Model Context Protocol.

This module exposes the deterministic two-axis governance classifier
(:func:`cage.classify.classify_manifest`) and its regulatory-control map as a
full Model Context Protocol surface, implementing all three MCP primitives:

* **Tools** - ``classify_manifest`` (strict input schema) classifies an agent's
  tool manifest and returns the :class:`~cage.classify.Classification` together
  with the regulatory controls that attach to the resolved governance intensity.
  Each classification is also published as a per-agent resource, and a
  ``resources/list_changed`` event is emitted so subscribers can re-list.
* **Resources** - the two-axis matrix (``cage://matrix/two-axis``) and the
  regulatory-control map (``cage://regmap/controls``), both as JSON.
* **Prompts** - ``review_agent_governance``, a reusable prompt template that asks
  a reviewer to assess an agent's governance posture against the classification.

Design discipline:

* The ``mcp`` SDK (and, through it, ``pydantic``) is imported lazily, *only*
  inside :func:`build_server` / :func:`main`. Importing this module never
  requires the optional MCP dependency, so the rest of ``cage`` and the bulk of
  the test suite import with ``pytest`` as the only third-party package.
* The classifier used here is the pure-stdlib deterministic one, so the server
  runs fully offline with no Anthropic client and no network access.
* All logging goes to **stderr only** - stdout is the JSON-RPC channel for the
  stdio transport and must never carry anything else.
* Tool input is validated twice: by the declared strict JSON schema, then by
  :meth:`cage.manifest.AgentManifest.validate` (duplicate tool names, empty
  names, and so on).

Ticket: P2-01 / P2-02 (MCP server + gateway).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, Any

from cage.classify import (
    MATRIX,
    AutonomyLevel,
    BlastRadius,
    GovernanceIntensity,
    classify_manifest,
)
from cage.manifest import AgentManifest, ToolKind
from cage.models import AGENT_MODEL, ANTHROPIC_VERSION, model_fingerprint
from cage.regmap import Dimension, controls_for_dimension, controls_for_intensity

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

# The stable URIs this server publishes as resources.
MATRIX_URI = "cage://matrix/two-axis"
REGMAP_URI = "cage://regmap/controls"
CLASSIFICATION_URI_PREFIX = "cage://classification/"

SERVER_NAME = "cage-governance-classifier"
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = (
    "Governance classifier for Claude tool-use agents. Call the "
    "'classify_manifest' tool with an agent name and its tool manifest to place "
    "the agent on the autonomy (L0-L5) and blast-radius (BR1-BR5) axes, resolve "
    "its governance intensity (LOW/MODERATE/HIGH/CRITICAL), and receive the "
    "named regulatory controls that attach. Read the 'cage://matrix/two-axis' "
    "and 'cage://regmap/controls' resources for the authoritative mapping, and "
    "use the 'review_agent_governance' prompt to review a classification."
)

# A module logger wired to stderr. stdout is reserved for the JSON-RPC channel.
_LOGGER = logging.getLogger("cage.mcp_server")


def _configure_stderr_logging(level: int = logging.INFO) -> None:
    """Attach a single stderr handler to the module logger (idempotent).

    stdout carries JSON-RPC on the stdio transport, so every diagnostic the
    server emits must go to stderr instead.
    """
    # Drop any handler we previously installed and bind a fresh one to the
    # *current* sys.stderr. Rebinding (rather than leaving a stale handler in
    # place) keeps the server pointed at the live stderr even if it was swapped
    # - e.g. under a test capture fixture - and never at stdout.
    for handler in list(_LOGGER.handlers):
        if getattr(handler, "_cage_stderr", False):
            _LOGGER.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._cage_stderr = True  # type: ignore[attr-defined]
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(level)
    _LOGGER.propagate = False


def _load_mcp() -> Any:
    """Import and return the ``mcp`` server API plus the few symbols we need.

    Kept out of module scope so importing :mod:`cage.mcp_server` never requires
    the MCP SDK (nor pydantic, which the SDK pulls in) until the server is
    actually built. Returns a small namespace object with the symbols used by
    :func:`build_server`.
    """
    try:
        from mcp.server.lowlevel import NotificationOptions
        from mcp.server.mcpserver import Context, MCPServer
        from mcp.server.mcpserver.resources import FunctionResource
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "the 'mcp' package (>=2.0) is required to run the MCP server; "
            "install this project's 'mcp' extra: pip install 'mcp[cli]>=2.0.0'"
        ) from exc

    class _Mcp:
        pass

    ns = _Mcp()
    ns.MCPServer = MCPServer
    ns.Context = Context
    ns.FunctionResource = FunctionResource
    ns.NotificationOptions = NotificationOptions
    ns.BaseModel = BaseModel
    ns.ConfigDict = ConfigDict
    ns.Field = Field
    return ns


# --- Resource payload builders (pure stdlib, independently testable) --------


def matrix_payload() -> dict[str, Any]:
    """Return the two-axis matrix as a JSON-serialisable mapping.

    Shape: axis definitions plus a nested ``cells[autonomy][blast] -> intensity``
    table covering all 30 cells of :data:`cage.classify.MATRIX`.
    """
    cells: dict[str, dict[str, str]] = {}
    for autonomy in AutonomyLevel:
        row: dict[str, str] = {}
        for blast in BlastRadius:
            row[blast.name] = MATRIX[(autonomy, blast)].name
        cells[autonomy.name] = row
    return {
        "description": "Two-axis governance matrix: autonomy x blast-radius -> intensity.",
        "autonomy_axis": {a.name: a.value for a in AutonomyLevel},
        "blast_axis": {b.name: b.value for b in BlastRadius},
        "intensity_scale": {i.name: i.value for i in GovernanceIntensity},
        "cells": cells,
    }


def regmap_payload() -> dict[str, Any]:
    """Return the regulatory-control map as a JSON-serialisable mapping.

    Covers the cumulative controls attaching at each governance intensity and the
    controls anchored to each of the six evaluation dimensions.
    """
    by_intensity = {
        intensity.name: [row.to_dict() for row in controls_for_intensity(intensity)]
        for intensity in GovernanceIntensity
    }
    by_dimension = {
        dimension.value: [row.to_dict() for row in controls_for_dimension(dimension)]
        for dimension in Dimension
    }
    return {
        "description": (
            "Regulatory control map across EU AI Act, ISO/IEC 42001, PRA SS1/23 "
            "and US SR 11-7. 'by_intensity' is cumulative up the scale."
        ),
        "by_intensity": by_intensity,
        "by_dimension": by_dimension,
    }


def classify_payload(agent_name: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify a manifest and return the classification plus its control map.

    ``tools`` is a list of plain tool mappings ({name, description, kind,
    reversible}). Raises :class:`ValueError` on any structurally invalid manifest
    (unknown tool kind, duplicate or empty names, and so on).
    """
    manifest = AgentManifest.from_dict({"name": agent_name, "tools": tools})
    classification = classify_manifest(manifest)
    return {
        "classification": classification.to_dict(),
        "regulatory_controls": {
            "intensity": classification.intensity.name,
            "intensity_controls": [c.to_dict() for c in classification.controls],
            "dimension_controls": {
                dim.value: [c.to_dict() for c in controls_for_dimension(dim)]
                for dim in Dimension
            },
        },
        "model_pin": {
            "agent_model": AGENT_MODEL,
            "anthropic_version": ANTHROPIC_VERSION,
            "fingerprint": model_fingerprint(AGENT_MODEL, ANTHROPIC_VERSION),
        },
    }


# The six tool kinds, surfaced as a Literal so the strict input schema constrains
# 'kind' to the valid enum rather than any string.
_TOOL_KIND_VALUES = tuple(k.value for k in ToolKind)


def review_prompt_text(agent_name: str, classification_json: str = "") -> str:
    """Build the body of the 'review this agent's governance' prompt."""
    lines = [
        f"You are reviewing the governance posture of the agent '{agent_name}'.",
        "",
        "Using the two-axis classification (autonomy L0-L5 x blast-radius "
        "BR1-BR5 -> governance intensity) and the attached regulatory controls:",
        "",
        "1. State whether the resolved governance intensity looks correct for "
        "what this agent can do, and why.",
        "2. For the resolved intensity, confirm each named control (EU AI Act, "
        "ISO/IEC 42001, PRA SS1/23, US SR 11-7) is satisfied or flag the gap.",
        "3. Check human-oversight and escalation: does the agent escalate when "
        "and only when it should for its blast radius?",
        "4. Call out any dangerous or irreversible capability that is not matched "
        "by a control.",
        "",
        "Return PASS / FIX / BLOCK with a one-line cited reason per point.",
    ]
    if classification_json.strip():
        lines += ["", "Classification under review:", classification_json.strip()]
    return "\n".join(lines)


def build_server(classifier: Callable[[str, list[dict[str, Any]]], dict[str, Any]] | None = None) -> Any:
    """Build and return the configured MCP server.

    Registers the ``classify_manifest`` tool (strict input schema), the matrix and
    regmap resources, and the ``review_agent_governance`` prompt; declares the
    list-changed capabilities; and wires stderr logging. ``classifier`` is an
    injection seam for tests - it defaults to :func:`classify_payload` (the pure
    deterministic classifier) and takes ``(agent_name, tools)`` returning the
    JSON-able result mapping.

    The ``mcp`` SDK is imported here, lazily, so importing this module stays
    dependency-free.
    """
    _configure_stderr_logging()
    mcp = _load_mcp()
    classify_fn = classifier or classify_payload

    server = mcp.MCPServer(
        name=SERVER_NAME,
        version=SERVER_VERSION,
        instructions=SERVER_INSTRUCTIONS,
    )

    # --- Strict tool input model -------------------------------------------
    # extra='forbid' -> additionalProperties:false; Literal over the six kinds
    # -> a strict enum on 'kind'. Pydantic resolves these from the enclosing
    # frame even under `from __future__ import annotations`.
    from typing import Literal

    class _ToolInput(mcp.BaseModel):
        model_config = mcp.ConfigDict(extra="forbid")
        name: str
        kind: Literal[_TOOL_KIND_VALUES]  # type: ignore[valid-type]
        description: str = ""
        reversible: bool = True

    # --- Tool: classify_manifest -------------------------------------------
    async def classify_manifest_tool(name, tools, ctx):
        """Classify ``name`` + ``tools`` and publish the result as a resource."""
        tool_dicts = [t.model_dump() for t in tools]
        try:
            result = classify_fn(name, tool_dicts)
        except ValueError as exc:
            # A client error (a bad manifest), not a server crash. Log to stderr
            # and surface a specific, actionable tool error.
            _LOGGER.error("invalid manifest for '%s': %s", name, exc)
            raise ValueError(f"invalid manifest: {exc}") from exc

        intensity = result.get("classification", {}).get("intensity", "?")
        _LOGGER.info("classified '%s' -> intensity %s; publishing resource", name, intensity)

        # Publish the classification as a per-agent resource and emit a
        # resources/list_changed notification so subscribers can re-list.
        uri = f"{CLASSIFICATION_URI_PREFIX}{name}"
        body = json.dumps(result, indent=2, sort_keys=True)
        server.add_resource(
            mcp.FunctionResource(
                uri=uri,
                name=f"classification:{name}",
                description=f"Latest governance classification for '{name}'.",
                mime_type="application/json",
                fn=lambda body=body: body,
            )
        )
        await ctx.notify_resources_changed()
        return result

    # Set real (non-string) annotations before registering: the SDK builds the
    # input schema from these, and under `from __future__ import annotations` the
    # locally-defined _ToolInput / Context could not otherwise be resolved.
    classify_manifest_tool.__annotations__ = {
        "name": str,
        "tools": list[_ToolInput],
        "ctx": mcp.Context,
        "return": dict,
    }
    server.add_tool(
        classify_manifest_tool,
        name="classify_manifest",
        title="Classify an agent manifest",
        description=(
            "Classify an agent's tool manifest onto the autonomy x blast-radius "
            "matrix and return its governance intensity plus the attaching "
            "regulatory controls."
        ),
    )

    # --- Resources ---------------------------------------------------------
    @server.resource(
        MATRIX_URI,
        name="two-axis-matrix",
        title="Two-axis governance matrix",
        description="Autonomy x blast-radius -> governance intensity (all 30 cells).",
        mime_type="application/json",
    )
    def matrix_resource() -> str:
        return json.dumps(matrix_payload(), indent=2, sort_keys=True)

    @server.resource(
        REGMAP_URI,
        name="regulatory-control-map",
        title="Regulatory control map",
        description="Named controls per governance intensity and per eval dimension.",
        mime_type="application/json",
    )
    def regmap_resource() -> str:
        return json.dumps(regmap_payload(), indent=2, sort_keys=True)

    # --- Prompt ------------------------------------------------------------
    @server.prompt(
        name="review_agent_governance",
        title="Review this agent's governance",
        description=(
            "A reviewer prompt that walks the governance posture of a classified "
            "agent: intensity sanity-check, control coverage, oversight and "
            "escalation, and unmatched dangerous capabilities."
        ),
    )
    def review_agent_governance(agent_name: str, classification_json: str = "") -> str:
        return review_prompt_text(agent_name, classification_json)

    _LOGGER.info(
        "built MCP server '%s' v%s (1 tool, 2 resources, 1 prompt)",
        SERVER_NAME,
        SERVER_VERSION,
    )
    return server


def initialization_options(server: Any) -> Any:
    """Return stdio initialization options that advertise all list-changed caps.

    Declares ``listChanged`` for tools, resources and prompts so a client knows
    the server emits change notifications (the ``classify_manifest`` tool
    publishes a new resource and fires ``resources/list_changed`` on each call).
    """
    mcp = _load_mcp()
    return server._lowlevel_server.create_initialization_options(
        notification_options=mcp.NotificationOptions(
            prompts_changed=True,
            resources_changed=True,
            tools_changed=True,
        )
    )


def main() -> None:
    """Run the MCP server over stdio.

    Logs to stderr only; stdout is the JSON-RPC transport. Blocks until the
    client disconnects.
    """
    import anyio

    _configure_stderr_logging()
    server = build_server()
    init_opts = initialization_options(server)
    _LOGGER.info("starting '%s' on stdio", SERVER_NAME)

    async def _run() -> None:
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read_stream, write_stream):
            await server._lowlevel_server.run(read_stream, write_stream, init_opts)

    anyio.run(_run)


if __name__ == "__main__":  # pragma: no cover
    main()
