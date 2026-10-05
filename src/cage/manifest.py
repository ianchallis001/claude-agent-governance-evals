"""Agent / tool manifest model, loader, and validator.

A *manifest* is the declared capability surface of the agent being governed: its
name and the list of tools it may call. Each tool carries the governance-relevant
metadata the classifier reasons over - what the tool touches (``kind``) and
whether its effect can be undone (``reversible``).

Ticket P1-01 hardens schema validation (error taxonomy, nested edge cases, and
fixtures); the dataclasses and the base loader below are delivered by the
architecture skeleton and import cleanly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ToolKind(str, Enum):
    """The governance-relevant category of a tool's effect.

    Ordered loosely from least to most consequential; ``classify.py`` maps each
    kind to a blast-radius contribution.
    """

    READ_PUBLIC = "read_public"
    READ_SENSITIVE = "read_sensitive"
    WRITE = "write"
    EGRESS = "egress"
    DESTRUCTIVE = "destructive"
    CODE_EXEC = "code_exec"


@dataclass(frozen=True)
class Tool:
    """A single tool the agent may invoke."""

    name: str
    description: str
    kind: ToolKind
    reversible: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Tool:
        """Build a :class:`Tool` from a plain mapping, coercing ``kind``."""
        try:
            name = data["name"]
            description = data["description"]
            kind = ToolKind(data["kind"])
        except KeyError as exc:  # pragma: no cover - exercised via validate()
            raise ValueError(f"tool missing required field: {exc.args[0]!r}") from exc
        except ValueError as exc:
            raise ValueError(f"tool has invalid kind: {data.get('kind')!r}") from exc
        reversible = bool(data.get("reversible", True))
        return cls(name=name, description=description, kind=kind, reversible=reversible)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class AgentManifest:
    """The declared capability surface of the agent under governance."""

    name: str
    tools: list[Tool] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentManifest:
        """Build an :class:`AgentManifest` from a plain mapping and validate it."""
        if "name" not in data:
            raise ValueError("manifest missing required field: 'name'")
        raw_tools = data.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ValueError("manifest 'tools' must be a list")
        tools = [Tool.from_dict(t) for t in raw_tools]
        manifest = cls(name=str(data["name"]), tools=tools)
        manifest.validate()
        return manifest

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tools": [t.to_dict() for t in self.tools]}

    def validate(self) -> None:
        """Raise :class:`ValueError` if the manifest is structurally invalid.

        Base checks are delivered here; P1-01 extends the error taxonomy and
        edge-case coverage (duplicate tool names, empty strings, unknown extra
        fields under a strict mode).
        """
        if not self.name or not str(self.name).strip():
            raise ValueError("manifest 'name' must be a non-empty string")
        seen: set[str] = set()
        for tool in self.tools:
            if not isinstance(tool, Tool):
                raise ValueError(f"tool entries must be Tool instances, got {type(tool)!r}")
            if tool.name in seen:
                raise ValueError(f"duplicate tool name: {tool.name!r}")
            seen.add(tool.name)


def load_manifest(path: str | Path) -> AgentManifest:
    """Load and validate an :class:`AgentManifest` from a JSON file."""
    text = Path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, Mapping):
        raise ValueError("manifest JSON must be an object at the top level")
    return AgentManifest.from_dict(data)
