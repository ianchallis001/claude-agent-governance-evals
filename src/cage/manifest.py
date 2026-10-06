"""Agent / tool manifest model, loader, and validator.

A *manifest* is the declared capability surface of the agent being governed: its
name and the list of tools it may call. Each tool carries the governance-relevant
metadata the classifier reasons over - what the tool touches (``kind``) and
whether its effect can be undone (``reversible``).

Validation has two levels. The default is lenient: it rejects anything the
classifier could not reason over (missing or empty required fields, a bad
``kind``, duplicate or non-mapping tool entries) but coerces benign slack such as
a stringifiable name. **Strict mode** (``strict=True``) additionally rejects
unknown fields and silent type coercions, so a manifest that passes strict
validation is exactly the shape the schema describes and nothing more. Every
failure raises :class:`ManifestError`, which carries a ``path`` pointing at the
offending location (for example ``tools[2].kind``) so a caller can pinpoint a
nested fault without re-parsing the message.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """A manifest or tool is structurally invalid.

    Subclasses :class:`ValueError`, so existing ``except ValueError`` callers keep
    working. ``path`` names the offending location (for example
    ``"tools[2].kind"``) when one applies, and ``message`` is the reason on its
    own; ``str(err)`` joins them as ``"<path>: <message>"``.
    """

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}" if path else message)


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


# Allowed keys per object, used by strict mode to reject anything else.
_TOOL_FIELDS = frozenset({"name", "description", "kind", "reversible"})
_MANIFEST_FIELDS = frozenset({"name", "tools"})


@dataclass(frozen=True)
class Tool:
    """A single tool the agent may invoke."""

    name: str
    description: str
    kind: ToolKind
    reversible: bool = True

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, strict: bool = False, path: str = "tool"
    ) -> Tool:
        """Build a :class:`Tool` from a plain mapping, coercing ``kind``.

        With ``strict=True`` the mapping may contain only the four known fields,
        ``name`` and ``description`` must already be strings, ``reversible`` must
        already be a boolean, and ``description`` must be non-empty.
        """
        if not isinstance(data, Mapping):
            raise ManifestError(
                f"tool must be an object, got {type(data).__name__}", path=path
            )
        if strict:
            extra = set(data) - _TOOL_FIELDS
            if extra:
                raise ManifestError(
                    f"unknown field(s): {', '.join(sorted(extra))}", path=path
                )
        for req in ("name", "description", "kind"):
            if req not in data:
                raise ManifestError(f"missing required field {req!r}", path=path)

        name = data["name"]
        description = data["description"]
        if strict and not isinstance(name, str):
            raise ManifestError(
                f"name must be a string, got {type(name).__name__}", path=f"{path}.name"
            )
        if strict and not isinstance(description, str):
            raise ManifestError(
                f"description must be a string, got {type(description).__name__}",
                path=f"{path}.description",
            )
        name = str(name)
        description = str(description)
        if not name.strip():
            raise ManifestError("name must be a non-empty string", path=f"{path}.name")
        if strict and not description.strip():
            raise ManifestError(
                "description must be a non-empty string", path=f"{path}.description"
            )

        try:
            kind = ToolKind(data["kind"])
        except ValueError as exc:
            valid = ", ".join(k.value for k in ToolKind)
            raise ManifestError(
                f"invalid kind {data['kind']!r}; must be one of: {valid}",
                path=f"{path}.kind",
            ) from exc

        reversible_raw = data.get("reversible", True)
        if strict and not isinstance(reversible_raw, bool):
            raise ManifestError(
                f"reversible must be a boolean, got {type(reversible_raw).__name__}",
                path=f"{path}.reversible",
            )
        return cls(
            name=name, description=description, kind=kind, reversible=bool(reversible_raw)
        )

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
    def from_dict(cls, data: Mapping[str, Any], *, strict: bool = False) -> AgentManifest:
        """Build an :class:`AgentManifest` from a plain mapping and validate it.

        With ``strict=True`` the mapping may contain only ``name`` and ``tools``,
        ``name`` must already be a string, and each tool is parsed strictly too.
        """
        if not isinstance(data, Mapping):
            raise ManifestError("manifest must be an object at the top level")
        if strict:
            extra = set(data) - _MANIFEST_FIELDS
            if extra:
                raise ManifestError(
                    f"manifest has unknown field(s): {', '.join(sorted(extra))}"
                )
        if "name" not in data:
            raise ManifestError("missing required field 'name'")
        name_raw = data["name"]
        if strict and not isinstance(name_raw, str):
            raise ManifestError(
                f"name must be a string, got {type(name_raw).__name__}", path="name"
            )
        raw_tools = data.get("tools", [])
        if not isinstance(raw_tools, list):
            raise ManifestError("'tools' must be a list", path="tools")
        tools = [
            Tool.from_dict(t, strict=strict, path=f"tools[{i}]")
            for i, t in enumerate(raw_tools)
        ]
        manifest = cls(name=str(name_raw), tools=tools)
        manifest.validate()
        return manifest

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tools": [t.to_dict() for t in self.tools]}

    def validate(self) -> None:
        """Raise :class:`ManifestError` if the manifest is structurally invalid.

        Runs on the constructed dataclass, so it also catches a manifest built in
        code rather than parsed: a blank agent or tool name, a non-:class:`Tool`
        entry, or a duplicate tool name. Each error names its ``path``.
        """
        if not self.name or not str(self.name).strip():
            raise ManifestError("'name' must be a non-empty string", path="name")
        seen: set[str] = set()
        for i, tool in enumerate(self.tools):
            if not isinstance(tool, Tool):
                raise ManifestError(
                    f"tool entries must be Tool instances, got {type(tool).__name__}",
                    path=f"tools[{i}]",
                )
            if not tool.name or not tool.name.strip():
                raise ManifestError("tool name must be a non-empty string", path=f"tools[{i}].name")
            if tool.name in seen:
                raise ManifestError(f"duplicate tool name {tool.name!r}", path=f"tools[{i}].name")
            seen.add(tool.name)


def load_manifest(path: str | Path, *, strict: bool = False) -> AgentManifest:
    """Load and validate an :class:`AgentManifest` from a JSON file.

    Pass ``strict=True`` to reject unknown fields and silent type coercions.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ManifestError("manifest JSON must be an object at the top level")
    return AgentManifest.from_dict(data, strict=strict)
