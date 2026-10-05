# ADR-0001: Build on the raw Anthropic Messages API, not Bedrock

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Context tickets:** P1-05 (`agent.py`), P1-10 (`drift.py`), P1-12 (`cli.py`)

## Context

The agent under test is a Claude tool-use classifier that runs a
`tool_use` / `tool_result` loop. It can reach Claude in several ways: the native
Anthropic Messages API (`anthropic` SDK), Amazon Bedrock, or Google Vertex. The
harness has two non-negotiable requirements that bear on this choice:

1. **Reproducibility.** Evals must pin the exact model and request version and
   fail on drift (SPEC section 9; PRA SS1/23 Principle 2.4, SR 11-7 Sec. VII).
2. **A free, deterministic, offline path.** The full test suite and the `--mock`
   CLI path must run with `pytest` as the only third-party dependency, with no
   API key and no network.

## Decision

Build directly on the **Anthropic Messages API** via the `anthropic` SDK, and
keep that SDK import **lazy** - `cage.agent._load_anthropic()` imports it only on
the live-API path. All tool-use orchestration (`submit_classification`,
`inspect_*` tools) is expressed in native Messages API shapes: `tool_use` blocks,
`tool_result` blocks, and a forced-tool-call pattern for the judge
(`record_verdict`). The offline mock (`cage.mock`) emulates exactly these
Messages API response shapes, so the agent loop is identical whether it is driven
by a live client or the scripted one.

## Rationale

- **One canonical request/response shape to pin.** Drift control fingerprints the
  model id plus the pinned `anthropic-version` header (`2023-06-01`). The Messages
  API exposes that header directly; Bedrock wraps the model behind its own
  versioning and region routing, which adds a second, platform-owned drift
  surface the harness would have to model and pin separately. Fewer moving parts
  under the fingerprint means the change-management gate stays honest.
- **Dateless model ids are pin-safe here.** From the Claude 4.6 generation onward
  the dateless aliases (`claude-sonnet-5-5`, `claude-opus-5-5`) are frozen
  point-in-time snapshots, not moving `-latest` pointers, so they are legitimate
  pins for an eval. Bedrock model identifiers and their lifecycle are a separate,
  platform-specific naming scheme that would need its own pin-safety argument.
- **Cleanest lazy-import boundary.** A single optional dependency (`anthropic`),
  imported in one place, keeps the stdlib-only runtime invariant trivially
  true and the offline mock a faithful stand-in. A Bedrock path pulls in
  `boto3`, AWS credential resolution and region configuration - more optional
  surface area, and a mock that must emulate a second transport.
- **Tool-use fidelity.** The native API is the reference implementation of
  Claude tool use; emulating native `tool_use`/`tool_result` blocks in the mock
  is exact. Emulating a Bedrock-wrapped variant would be emulating a wrapper.

## Consequences

- **Positive.** Reproducibility and the offline mock are simple and faithful; the
  drift fingerprint has a single clean version surface; the runtime stays
  stdlib-only with one lazily-imported optional dependency.
- **Negative / trade-off.** A deployer who is contractually required to consume
  Claude *through* Bedrock or Vertex (for example a bank with an AWS-only data
  boundary) cannot point this harness at their endpoint without an adapter.
- **Mitigation.** The agent takes an **injected client** (`ClassifierAgent(client=...)`)
  and only constructs `anthropic.Anthropic` itself when no client is supplied.
  A Bedrock/Vertex path is therefore an additive adapter that satisfies the same
  minimal client interface the mock already implements - it does not require
  changing the engine. Should a deployment need it, that adapter is the clean
  extension point, and it would carry its own drift-surface pin per the reasoning
  above.

## Alternatives considered

- **Amazon Bedrock as the primary transport.** Rejected as the *default* for the
  reasons above (second drift surface, heavier optional dependency, wrapper
  emulation). Retained as a documented future adapter behind the injected-client
  seam.
- **A provider-agnostic abstraction layer from day one.** Rejected as premature:
  it would add indirection and a second mock with no present consumer, against
  the "small and honest" goal. The injected-client seam already preserves the
  option at near-zero cost.
