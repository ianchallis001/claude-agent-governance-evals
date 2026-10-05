# claude-agent-governance-evals

A small, honest, regulator-grounded evaluation harness for a Claude tool-use agent — it
measures its own judges, refuses to gate inside their noise floor, re-validates on
model-version drift (mapped to PRA SS1/23 and SR 11-7), and scores dangerous misses and
human-escalation as first-class safety gates. The agent under test is an **autonomy ×
blast-radius governance classifier**, which is also exposed as an authored MCP server.

Built directly on the Anthropic Messages API (`anthropic` SDK), with a free offline mock
mode so the full suite runs in CI without an API key.

> **Status:** under active construction (private). Not yet released.

---

_Author: Ian Challis._
