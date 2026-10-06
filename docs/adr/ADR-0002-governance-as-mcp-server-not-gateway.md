# ADR-0002: Publish the classifier as an MCP server, not as an enforcement gateway

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Context tickets:** P2-03 (`server.py`)

## Context

The governance classifier has to be reachable by other agents and tools, not just
by this repo's CLI. There are two very different ways to expose a
governance capability to a fleet of agents:

- **A gateway / middleware** that sits in the request path and **enforces** policy
  - it intercepts an agent's tool calls, blocks or rewrites them, and is a
    hard dependency on the critical path of every action.
- **An MCP server** that **answers a question** - given a manifest, it returns a
  classification, the governance intensity, and the controls that attach - and is
  called when a caller wants a judgement, off the critical path.

These are not the same product. Treating them as one is a common and costly
mistake: it puts a probabilistic classifier in the blocking path of
production traffic.

## Decision

Publish the classifier as an **MCP server** (`cage.server`, ticket P2-03) that
exposes a `classify_manifest` tool over stdio, with the `mcp` package imported
**lazily** only inside that entrypoint. The server **classifies and judges**; it
does **not** enforce. Enforcement - blocking a tool call, demanding an approval,
tripping a kill-switch - is left to a gateway / middleware layer that
is out of scope for this repo.

## The finding this records

Working out where governance actually binds surfaced a clean separation of
concerns, and this ADR exists to pin it down:

- **Enforcement is a gateway / middleware concern.** It is synchronous, sits on the
  critical path, must fail closed, needs sub-request latency budgets, and is
  specific to the deployment and tenant (who may approve, what "block" means in this
  estate, where the kill-switch is wired). It belongs in the request path, not in
  a model.
- **Classification and judgement are server-shaped.** "What is this agent's
  autonomy and blast radius, what governance intensity does that resolve to, and
  which named controls attach?" is a question with an answer. It is a
  request/response capability other agents call - exactly the shape MCP exists to
  serve - and it has to be free to say **unknown** / abstain (SPEC section 8)
  rather than be forced into a binary allow/deny on the hot path.

Putting the classifier behind MCP keeps the probabilistic, calibrated,
can-abstain part where abstention is safe - a judgement a caller
reasons about - and keeps the deterministic, fail-closed part (enforcement)
apart, since there the harness's readiness to abstain would be a liability.

## Consequences

- **Positive.** The classifier drops into any MCP-aware agent fleet as a
  callable judgement service; it can abstain honestly; it carries no
  critical-path latency or fail-closed duties it cannot meet; and the `mcp`
  dependency stays optional and lazily imported, so the rest of `cage` and the full
  test suite import without it (`tests/test_server.py::test_module_imports_without_mcp`).
- **Negative / trade-off.** On its own, this server **cannot stop a dangerous
  action** - it can only classify and judge one. A real deployment still needs an
  enforcement layer, and the two have to be wired together: the gateway calls the
  classifier, caches the verdict, and enforces.
- **Boundary.** That enforcement layer is deliberately **not** in this repo. This
  ADR records why: building it here would pull deployment-specific, fail-closed,
  critical-path concerns into an offline evaluation harness and blur the
  very separation this decision protects.

## Alternatives considered

- **Ship an enforcing gateway.** Rejected. It is a different product with
  different non-functional requirements (latency, fail-closed, a tenancy-specific
  approval model), and it would put a probabilistic classifier in the blocking path of
  production traffic - the mistake named above.
- **Expose only the CLI / Python API.** Rejected as too little: the whole point
  is for *other agents* to call the classifier, and that is what MCP is for. The CLI
  stays for humans and CI; the MCP server is for agents.
