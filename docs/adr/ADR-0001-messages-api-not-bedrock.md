# ADR-0001: Build on the raw Anthropic Messages API, not Bedrock

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Context tickets:** P1-05 (`agent.py`), P1-10 (`drift.py`), P1-12 (`cli.py`)

## Context

The agent under test is a Claude tool-use classifier that runs a
`tool_use` / `tool_result` loop. It can reach Claude three ways: the native
Anthropic Messages API (`anthropic` SDK), Amazon Bedrock, or Google Vertex. Two
harness requirements drive the choice, and neither is negotiable:

1. **Reproducibility.** Evals must pin the exact model and request version and
   fail on drift (SPEC section 9; PRA SS1/23 Principle 2.4, SR 11-7 Sec. VII).
2. **A free, deterministic, offline path.** The full test suite and the `--mock`
   CLI path must run with `pytest` as the only third-party dependency, with no
   API key and no network.

## Decision

Build directly on the **Anthropic Messages API** via the `anthropic` SDK, and
keep that SDK import **lazy** - `cage.agent._load_anthropic()` loads it only on
the live-API path. All tool-use orchestration (`submit_classification`,
`inspect_*` tools) uses native Messages API shapes: `tool_use` blocks,
`tool_result` blocks, and a forced-tool-call pattern for the judge
(`record_verdict`). The offline mock (`cage.mock`) returns these same Messages
API response shapes, so the agent loop runs the same way whether a live client or
the scripted one drives it.

## Rationale

- **One request/response shape to pin.** The drift control fingerprints the
  model id plus the pinned `anthropic-version` header (`2023-06-01`). The Messages
  API exposes that header directly. Bedrock wraps the model behind its own
  versioning and region routing, so the harness would have to model and pin a
  second drift surface that the platform owns. Fewer moving parts under the
  fingerprint keep the change-management gate honest.
- **Dateless model ids are safe to pin here.** From the Claude 4.6 generation on,
  the dateless aliases (`claude-sonnet-5-5`, `claude-opus-5-5`) are frozen
  point-in-time snapshots, not moving `-latest` pointers, so they are fair
  pins for an eval. Bedrock model identifiers follow a separate, platform-specific
  naming scheme with its own lifecycle, and that would need its own pin-safety
  argument.
- **A clean lazy-import boundary.** One optional dependency (`anthropic`),
  imported in one place, keeps the stdlib-only runtime invariant
  true and the offline mock a faithful stand-in. A Bedrock path pulls in
  `boto3`, AWS credential resolution and region configuration - more optional
  surface area, and a mock that has to emulate a second transport.
- **Tool-use fidelity.** The native API is the reference implementation of
  Claude tool use, so the mock can copy native `tool_use`/`tool_result` blocks
  exactly. A Bedrock-wrapped variant would mean emulating a wrapper.

## Consequences

- **Positive.** Reproducibility and the offline mock stay simple and faithful; the
  drift fingerprint has one clean version surface; the runtime stays
  stdlib-only with one lazily-imported optional dependency.
- **Negative / trade-off.** A deployer who is contractually bound to reach
  Claude *through* Bedrock or Vertex - say a bank with an AWS-only data
  boundary - cannot point this harness at their endpoint without an adapter.
- **Mitigation.** The agent takes an **injected client** (`ClassifierAgent(client=...)`)
  and builds `anthropic.Anthropic` itself only when no client is supplied.
  A Bedrock or Vertex path is therefore an added adapter that meets the same
  minimal client interface the mock already implements - the engine does not
  change. If a deployment needs it, that adapter is the clean
  extension point, and it would carry its own drift-surface pin for the reasons
  above.

## Alternatives considered

- **Amazon Bedrock as the primary transport.** Rejected as the *default* for the
  reasons above: a second drift surface, a heavier optional dependency, and
  wrapper emulation. Kept as a documented future adapter behind the injected-client
  seam.
- **A provider-agnostic abstraction layer from day one.** Rejected as premature:
  it adds indirection and a second mock with no consumer today, against
  the "small and honest" goal. The injected-client seam already keeps the
  option open at near-zero cost.
