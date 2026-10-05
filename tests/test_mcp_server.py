"""Tests for cage.mcp_server - the MCP surface over the governance classifier.

Covers each MCP primitive (tool, resources, prompt) end to end over the SDK's
in-memory client<->server transport, plus the strict input schema, input
validation, list-changed capability declaration, change-notification emission,
and the stderr-only logging discipline.

The SDK-backed tests are skipped when the optional ``mcp`` SDK is not installed,
so the rest of the suite still runs with pytest as the only third-party
dependency. The pure-stdlib payload builders are tested without the SDK. The
async flows are driven synchronously via ``anyio.run`` so no pytest async plugin
is required.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest

from cage import mcp_server

# --- Pure-stdlib payload builders (no SDK needed) --------------------------


def test_module_imports_without_mcp():
    # Importing cage.mcp_server (done at top) must not require the mcp SDK.
    assert hasattr(mcp_server, "build_server")
    assert hasattr(mcp_server, "main")


def test_matrix_payload_has_all_30_cells():
    payload = mcp_server.matrix_payload()
    cells = payload["cells"]
    assert len(cells) == 6  # L0..L5
    total = sum(len(row) for row in cells.values())
    assert total == 30  # 6 autonomy x 5 blast
    assert cells["L5"]["BR5"] == "CRITICAL"
    assert cells["L0"]["BR1"] == "LOW"


def test_regmap_payload_covers_intensities_and_dimensions():
    payload = mcp_server.regmap_payload()
    assert set(payload["by_intensity"]) == {"LOW", "MODERATE", "HIGH", "CRITICAL"}
    # Cumulative: CRITICAL carries at least as many controls as LOW.
    assert len(payload["by_intensity"]["CRITICAL"]) >= len(payload["by_intensity"]["LOW"])
    assert len(payload["by_dimension"]) == 6
    for rows in payload["by_dimension"].values():
        assert rows and all("framework" in r and "clause" in r for r in rows)


def test_classify_payload_deterministic_and_grounded():
    tools = [
        {"name": "read_news", "kind": "read_public", "description": "read"},
        {"name": "wipe_db", "kind": "destructive", "reversible": False, "description": "wipe"},
    ]
    result = mcp_server.classify_payload("Ops", tools)
    cls = result["classification"]
    assert cls["agent_name"] == "Ops"
    assert cls["blast"] == "BR5"  # irreversible destructive dominates
    assert result["regulatory_controls"]["intensity"] == cls["intensity"]
    # Evidence cites every tool.
    assert {e["tool"] for e in cls["evidence"]} == {"read_news", "wipe_db"}
    assert result["model_pin"]["agent_model"]


def test_classify_payload_rejects_bad_kind():
    with pytest.raises(ValueError):
        mcp_server.classify_payload("Bad", [{"name": "t", "kind": "not_a_kind"}])


def test_classify_payload_rejects_duplicate_tool_names():
    with pytest.raises(ValueError):
        mcp_server.classify_payload(
            "Dup",
            [
                {"name": "t", "kind": "read_public"},
                {"name": "t", "kind": "write"},
            ],
        )


def test_review_prompt_text_includes_classification_when_given():
    body = mcp_server.review_prompt_text("Agent-7", '{"intensity": "HIGH"}')
    assert "Agent-7" in body
    assert "PASS / FIX / BLOCK" in body
    assert '{"intensity": "HIGH"}' in body


# --- SDK-backed tests (skipped if mcp absent) ------------------------------

pytest.importorskip("mcp", reason="mcp SDK not installed")

SAMPLE_TOOLS = [
    {"name": "read_feed", "kind": "read_public", "description": "read a public feed"},
    {"name": "post_update", "kind": "write", "reversible": True, "description": "post"},
]


class _Harness:
    """Async context manager: an initialized in-memory client against the server.

    Wires a :class:`ClientSession` to ``build_server()`` over the SDK's in-memory
    stream pair - no network, no stdio. ``events`` records change events seen on
    the server's own subscription bus.
    """

    def __init__(self, classifier=None):
        self._classifier = classifier
        self.server = None
        self.client = None
        self.events: list[str] = []

    async def __aenter__(self):
        import anyio
        from mcp.client.session import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams

        self._anyio = anyio
        self.server = mcp_server.build_server(classifier=self._classifier)
        self.server._subscriptions.subscribe(
            lambda ev: self.events.append(type(ev).__name__)
        )
        init_opts = mcp_server.initialization_options(self.server)
        ll = self.server._lowlevel_server

        self._cm = create_client_server_memory_streams()
        (cr, cw), (sr, sw) = await self._cm.__aenter__()
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        self._tg.start_soon(lambda: ll.run(sr, sw, init_opts, raise_exceptions=True))
        self._client_cm = ClientSession(cr, cw)
        self.client = await self._client_cm.__aenter__()
        await self.client.initialize()
        return self

    async def __aexit__(self, *exc):
        try:
            await self._client_cm.__aexit__(None, None, None)
        finally:
            self._tg.cancel_scope.cancel()
            await self._tg.__aexit__(None, None, None)
            await self._cm.__aexit__(None, None, None)
        return False


def _run(async_fn):
    """Run an async test body to completion on asyncio, no plugin required."""
    import anyio

    anyio.run(async_fn)


def test_initialize_declares_list_changed_capabilities():
    async def body():
        async with _Harness() as h:
            init = await h.client.initialize()
            caps = init.capabilities.model_dump(exclude_none=True)
            assert caps["tools"]["list_changed"] is True
            assert caps["resources"]["list_changed"] is True
            assert caps["prompts"]["list_changed"] is True
            assert init.instructions and "governance" in init.instructions.lower()

    _run(body)


def test_tool_listed_with_strict_input_schema():
    async def body():
        async with _Harness() as h:
            tools = (await h.client.list_tools()).tools
            assert "classify_manifest" in {t.name for t in tools}
            schema = next(t for t in tools if t.name == "classify_manifest").input_schema
            assert set(schema["required"]) == {"name", "tools"}
            # 'ctx' must NOT leak into the input schema.
            assert "ctx" not in schema["properties"]
            # Strict: the per-tool item object forbids extras and constrains kind.
            tool_item = schema["$defs"]["_ToolInput"]
            assert tool_item["additionalProperties"] is False
            assert set(tool_item["properties"]["kind"]["enum"]) == {
                "read_public",
                "read_sensitive",
                "write",
                "egress",
                "destructive",
                "code_exec",
            }

    _run(body)


def test_tool_classifies_manifest():
    async def body():
        async with _Harness() as h:
            res = await h.client.call_tool(
                "classify_manifest", {"name": "Poster", "tools": SAMPLE_TOOLS}
            )
            assert res.is_error is False
            payload = json.loads(res.content[0].text)
            cls = payload["classification"]
            assert cls["agent_name"] == "Poster"
            assert cls["blast"] == "BR3"  # reversible write dominates reads
            assert cls["intensity"] in {"LOW", "MODERATE", "HIGH", "CRITICAL"}
            assert payload["regulatory_controls"]["intensity"] == cls["intensity"]

    _run(body)


def test_tool_rejects_invalid_kind_via_schema():
    async def body():
        async with _Harness() as h:
            res = await h.client.call_tool(
                "classify_manifest",
                {"name": "X", "tools": [{"name": "t", "kind": "bogus"}]},
            )
            assert res.is_error is True

    _run(body)


def test_tool_publishes_resource_and_emits_change_notification():
    async def body():
        async with _Harness() as h:
            new_uri = f"{mcp_server.CLASSIFICATION_URI_PREFIX}Poster"
            before = {str(r.uri) for r in (await h.client.list_resources()).resources}
            assert new_uri not in before
            await h.client.call_tool(
                "classify_manifest", {"name": "Poster", "tools": SAMPLE_TOOLS}
            )
            await h._anyio.sleep(0.05)
            # The classify call fired a resources list-changed event on the bus.
            assert "ResourcesListChanged" in h.events
            after = {str(r.uri) for r in (await h.client.list_resources()).resources}
            assert new_uri in after
            read = await h.client.read_resource(new_uri)
            got = json.loads(read.contents[0].text)
            assert got["classification"]["agent_name"] == "Poster"

    _run(body)


def test_matrix_and_regmap_resources_served():
    async def body():
        async with _Harness() as h:
            uris = {str(r.uri) for r in (await h.client.list_resources()).resources}
            assert mcp_server.MATRIX_URI in uris
            assert mcp_server.REGMAP_URI in uris

            matrix = json.loads(
                (await h.client.read_resource(mcp_server.MATRIX_URI)).contents[0].text
            )
            assert matrix["cells"]["L5"]["BR5"] == "CRITICAL"

            regmap = json.loads(
                (await h.client.read_resource(mcp_server.REGMAP_URI)).contents[0].text
            )
            assert "by_intensity" in regmap and "by_dimension" in regmap

    _run(body)


def test_prompt_listed_and_rendered():
    async def body():
        async with _Harness() as h:
            names = {p.name for p in (await h.client.list_prompts()).prompts}
            assert "review_agent_governance" in names

            got = await h.client.get_prompt(
                "review_agent_governance",
                {"agent_name": "Poster", "classification_json": '{"intensity": "HIGH"}'},
            )
            text = got.messages[0].content.text
            assert "Poster" in text
            assert "PASS / FIX / BLOCK" in text

    _run(body)


def test_tool_uses_injected_classifier():
    calls = {}

    def fake_classifier(name, tools):
        calls["name"] = name
        calls["n"] = len(tools)
        return {"classification": {"agent_name": name, "intensity": "LOW"}}

    async def body():
        async with _Harness(classifier=fake_classifier) as h:
            res = await h.client.call_tool(
                "classify_manifest", {"name": "Fake", "tools": SAMPLE_TOOLS}
            )
            payload = json.loads(res.content[0].text)
            assert payload["classification"]["agent_name"] == "Fake"
            assert calls == {"name": "Fake", "n": 2}

    _run(body)


# --- Logging discipline: stderr only, never stdout -------------------------


def test_stderr_logging_configured_not_stdout(capsys):
    mcp_server._configure_stderr_logging()
    # Only assert about the handler cage installs (tagged). Test runners attach
    # their own log-capture handlers to the same logger; those are not ours.
    cage_handlers = [
        hdlr
        for hdlr in mcp_server._LOGGER.handlers
        if getattr(hdlr, "_cage_stderr", False)
    ]
    assert len(cage_handlers) == 1, "exactly one cage stderr handler expected"
    handler = cage_handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stderr  # bound to the current stderr
    assert handler.stream is not sys.stdout

    mcp_server._LOGGER.info("diagnostic line")
    captured = capsys.readouterr()
    assert captured.out == ""  # nothing on stdout (the JSON-RPC channel)
    assert "diagnostic line" in captured.err
