"""Tests for cage.mock - the offline scripted client."""

from __future__ import annotations

import json

import pytest

from cage.mock import (
    MockAnthropicClient,
    MockError,
    load_transcript,
    message_from_dict,
    script_from_transcript,
    text_response,
    tool_use_response,
)
from cage.models import AGENT_MODEL


def test_replays_script_in_order():
    client = MockAnthropicClient(
        script=[
            text_response("first", model=AGENT_MODEL),
            text_response("second", model=AGENT_MODEL),
        ]
    )
    assert client.messages.create(model=AGENT_MODEL, messages=[]).content[0].text == "first"
    assert client.messages.create(model=AGENT_MODEL, messages=[]).content[0].text == "second"
    assert len(client.calls) == 2


def test_tool_use_response_shape():
    resp = tool_use_response("record_verdict", {"verdict": "PASS"}, model=AGENT_MODEL)
    assert resp.stop_reason == "tool_use"
    block = resp.content[0]
    assert block.type == "tool_use"
    assert block.name == "record_verdict"
    assert block.input == {"verdict": "PASS"}


def test_exhausted_script_raises_harness_error():
    client = MockAnthropicClient(script=[text_response("only", model=AGENT_MODEL)])
    client.messages.create(model=AGENT_MODEL, messages=[])
    with pytest.raises(MockError):
        client.messages.create(model=AGENT_MODEL, messages=[])


def test_on_create_overrides_script():
    def handler(kwargs, client):
        return text_response("dynamic", model=kwargs["model"])

    client = MockAnthropicClient(on_create=handler)
    out = client.messages.create(model="claude-x", messages=[])
    assert out.content[0].text == "dynamic"
    assert out.model == "claude-x"


def test_reset_rewinds(mock_client_factory):
    client = mock_client_factory(script=[text_response("a", model=AGENT_MODEL)])
    client.messages.create(model=AGENT_MODEL, messages=[])
    assert client.remaining == 0
    client.reset()
    assert client.remaining == 1
    assert client.calls == []


def test_fixture_supplies_client(mock_client):
    assert isinstance(mock_client, MockAnthropicClient)
    assert mock_client.remaining == 1


# --- transcript record / replay (P1-04) -------------------------------------


def test_message_from_dict_rebuilds_blocks():
    msg = message_from_dict(
        {
            "model": AGENT_MODEL,
            "stop_reason": "tool_use",
            "id": "msg_1",
            "content": [
                {"type": "text", "text": "thinking"},
                {"type": "tool_use", "name": "do", "id": "toolu_1", "input": {"x": 1}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
    )
    assert msg.model == AGENT_MODEL
    assert msg.stop_reason == "tool_use"
    assert msg.id == "msg_1"
    assert msg.content[0].type == "text"
    assert msg.content[1].type == "tool_use"
    assert msg.content[1].name == "do"
    assert msg.content[1].input == {"x": 1}
    assert msg.usage.input_tokens == 10


def test_script_from_transcript_accepts_list_and_envelope():
    entry = {"model": AGENT_MODEL, "content": [{"type": "text", "text": "hi"}]}
    from_list = script_from_transcript([entry])
    from_envelope = script_from_transcript({"responses": [entry]})
    assert len(from_list) == len(from_envelope) == 1
    assert from_list[0].content[0].text == "hi"


def test_from_transcript_replays_in_order(tmp_path):
    path = tmp_path / "transcript.json"
    path.write_text(
        json.dumps(
            {
                "responses": [
                    {"model": AGENT_MODEL, "content": [{"type": "text", "text": "a"}]},
                    {"model": AGENT_MODEL, "content": [{"type": "text", "text": "b"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    client = MockAnthropicClient.from_transcript(path)
    assert client.remaining == 2
    assert client.messages.create(model=AGENT_MODEL, messages=[]).content[0].text == "a"
    assert client.messages.create(model=AGENT_MODEL, messages=[]).content[0].text == "b"


def test_load_transcript_reads_a_file(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(
        json.dumps([{"model": AGENT_MODEL, "content": [{"type": "text", "text": "x"}]}]),
        encoding="utf-8",
    )
    script = load_transcript(path)
    assert len(script) == 1 and script[0].content[0].text == "x"


# --- failure localisation: harness faults surface as MockError --------------


@pytest.mark.parametrize(
    "entry, needle",
    [
        ({"content": [{"type": "text", "text": "x"}]}, "model"),
        ({"model": AGENT_MODEL, "content": "not-a-list"}, "content"),
        ({"model": AGENT_MODEL, "content": [{"type": "image"}]}, "unsupported"),
        ({"model": AGENT_MODEL, "content": [{"type": "tool_use", "input": {}}]}, "name"),
        ({"model": AGENT_MODEL, "content": ["not-a-block"]}, "block"),
    ],
)
def test_malformed_transcript_entry_raises_mockerror(entry, needle):
    with pytest.raises(MockError, match=needle):
        message_from_dict(entry)


def test_script_from_transcript_rejects_bad_top_level():
    with pytest.raises(MockError, match="list of responses"):
        script_from_transcript("not a transcript")


def test_load_transcript_rejects_invalid_json(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MockError, match="not valid JSON"):
        load_transcript(path)


# --------------------------------------------------------------------------- #
# Dual-role governance mock (used by the CLI's --mock mode)                    #
# --------------------------------------------------------------------------- #
def test_governance_mock_serves_both_roles():
    from cage.agent import ClassifierAgent
    from cage.manifest import AgentManifest, Tool, ToolKind
    from cage.mock import governance_mock_client
    from cage.models import JUDGE_MODEL

    client = governance_mock_client()
    manifest = AgentManifest(
        name="refund-issuer",
        tools=[
            Tool("read_order", "Read an order.", ToolKind.READ_SENSITIVE, True),
            Tool("issue_refund", "Issue an irreversible refund.", ToolKind.WRITE, False),
        ],
    )
    # Agent role: the mock replays the reference classifier end to end.
    cls = ClassifierAgent(client=client).classify(manifest)
    assert cls.agent_name == "refund-issuer"
    assert cls.intensity.name == "HIGH"

    # Judge role: a record_verdict request gets a PASS verdict from JUDGE_MODEL.
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=16,
        messages=[{"role": "user", "content": "grade this"}],
        tools=[{"name": "record_verdict", "input_schema": {"type": "object"}}],
    )
    block = resp.content[0]
    assert block.name == "record_verdict"
    assert block.input["verdict"] == "PASS"


def test_governance_mock_rejects_unknown_request():
    from cage.mock import governance_mock_client

    client = governance_mock_client()
    with pytest.raises(MockError):
        client.messages.create(model="x", messages=[{"role": "user", "content": "hi"}], tools=[])
