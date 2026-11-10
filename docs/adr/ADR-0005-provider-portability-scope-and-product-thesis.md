# ADR-0005: Provider-portability scope and product thesis

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Gate:** PP-00 (#48) - a human sign-off, not a default-and-proceed
- **Related:** ADR-0006 (target sovereignty tier, LL-00); tickets PP-01, PP-04,
  PP-05, PP-07 (resolved), LL-04, LL-07 (resolved), LL-09
- **Decision support:** [`docs/sovereignty-tier-decision-support.md`](../sovereignty-tier-decision-support.md)

## Context

CAGE classifies and judges AI-agent governance against a control catalogue it
supplies itself (`regmap`: EU AI Act, GDPR, ISO 42001, NIST AI RMF, PRA SS1/23,
SR 11-7). The M5 provider seam (PP-01) makes CAGE runnable on a non-Anthropic
provider; the PP-07/LL-07 spikes (ADR follow-on) found that UK AISI Inspect's
model layer - in particular its `vllm/` provider - can supply most of that seam
by reuse rather than hand-built adapters. Before scoping adapter work, PP-00
fixes the **product thesis**, not the architecture.

Three questions interlock, and (c) reframes (a) and (b):

- **(a) Scope:** is CAGE a Claude-anchored tool, or does it evaluate agents built
  on any provider?
- **(b) Axis:** does portability apply to the agent-under-test, the judge/runtime,
  or both - and if both, staged or simultaneous?
- **(c) Primary driver:** reach (govern any vendor's agents), sovereignty/air-gap,
  or cost?

Binding caveats carried into this decision: every kappa/capability figure behind
the local-model research is **indicative only** (ticket LL-09 open - some source
arXiv identifiers were malformed); the Inspect route is **desk feasibility**
confirmed offline, not yet proven on vLLM/GPU hardware (LL-01).

## Decision

**(a) Scope - multi-provider is in scope.** CAGE is not Claude-anchored.
Provider-neutrality is a product goal, delivered by the M5 seam. CAGE stays
product-neutral in identity (no consumer/client names).

**(b) Axis - both, staged.** Portability applies to both the agent-under-test and
the judge/runtime, but in sequence. The **agent axis is first** - a
today-capability via the M5 seam plus a local classifier (LL-01). The
**judge axis is second**, gated on LL-04 clearing the per-dimension agreement bar
before a local judge is trusted.

**(c) Primary driver - sovereignty.** CAGE is a **sovereign governance appliance
by design**. Cross-vendor **reach is a feature the seam delivers**, not a
competing thesis; **cost is a secondary benefit**, never the headline (at low eval
volume a hosted judge can be cheaper than idle GPUs, so cost alone does not justify
the sovereign build).

**Product shape - one codebase, two topologies.** A single codebase and one rubric
are deployed in two topologies: a **hybrid** topology for buyers without a hard
air-gap need, and a **sovereign** topology for defence, tier-1 regulated financial
services and public sector. This addresses reach and sovereignty without forking
the product. The downstream tier choice and sequencing are fixed in ADR-0006.

## Consequences

- **Positive.** The thesis is decided, so adapter and local-model work (M5/M6) has
  a single north star. Reach and sovereignty stop competing: the seam serves both
  topologies from one codebase. The moat is the control catalogue plus *sovereign
  reproducibility of governance verdicts* - a position hosted-only tooling cannot
  occupy, and one the EU AI Act high-risk deadline (2 December 2027, Reg (EU)
  2026/1744) makes more valuable over time.
- **Commitment / trade-off.** Naming sovereignty primary puts **LL-04 (local-judge
  calibration) on the critical path** and means **GPU capex must be within
  appetite**. There is no cheap version: sovereignty, not cost, is the
  justification. The judge axis cannot be rushed ahead of LL-04.
- **Boundary.** This ADR fixes product thesis only; it changes no consumer
  pipeline and adds no enforcement code (the ADR-0002 split holds). CAGE holds no
  customer data.

## Alternatives considered

- **Claude-anchored harness (no seam).** Rejected: forgoes the agent-reach the
  seam gives cheaply and the sovereign topology outright; a hosted single-vendor
  dependency is exactly what a governance tool should be able to shed.
- **Reach-primary universal harness.** Rejected as the *thesis*: reach alone is a
  commoditising direction (hyperscalers will build governance for their own
  ecosystems) and is not the stated priority. Reach is kept - as a delivered
  feature, not the headline.
- **Single sovereign posture only.** Rejected: forgoes the mid-market hybrid
  topology whose revenue funds the sovereign build, for no sovereignty gain.
