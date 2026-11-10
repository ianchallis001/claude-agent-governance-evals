# ADR-0006: Target sovereignty tier

- **Status:** Accepted
- **Date:** 2026-11-10
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Gate:** LL-00 (#57) - a human sign-off; depends on PP-00 / ADR-0005
- **Related:** ADR-0005 (product thesis); M5 seam (PP-01/PP-04/PP-05/PP-06/PP-07);
  tickets LL-01, LL-02, LL-03, LL-04, LL-05, LL-06, LL-07 (resolved), LL-08, LL-09
- **Decision support:** [`docs/sovereignty-tier-decision-support.md`](../sovereignty-tier-decision-support.md)

## Context

Downstream of the PP-00 thesis (ADR-0005), which fixed sovereignty as the primary
driver and the product as one codebase in two deployment topologies. LL-00 selects
the **target sovereignty tier** and the path to it, from three options:

- **Option 1 - Hybrid:** local vLLM classifier + a pinned frontier hosted judge.
  The classifier is a today-capability (constrained decoding, finding A); the judge
  is validated by construction. Not air-gapped. Low-medium effort.
- **Option 2 - Inspect-routed:** both roles via UK AISI Inspect's model layer. The
  PP-07/LL-07 spikes resolved this *hybrid/reuse* - Inspect's `vllm/` provider
  effectively **is** the provider seam - so it is best read as a **wiring pattern**
  beneath Options 1 and 3, not a product tier. Low effort (a spike).
- **Option 3 - Fully sovereign:** both roles on local open-weight models, fully
  air-gapped. Full residency; load-bearing on LL-04 per-dimension calibration
  (unproven); high effort plus GPU capex.

Binding caveats: every kappa/capability figure is **indicative** (LL-09 open); the
Inspect route is **desk feasibility**, proven offline but not yet on vLLM/GPU
(LL-01); Option 3 completeness is **conditional on LL-04** and may be only
per-dimension. The judge base-model family must differ from the classifier family
(structural gate). Excluded from the control path: Ollama (cannot force a tool
call), Mistral Tekken v11+ (broken forced-call in vLLM); TGI not adopted
(maintenance mode).

## Decision

**Target state: Option 3 - the fully sovereign, air-gapped appliance - named now.**
The destination is stated rather than held conditional, to put real pull behind the
LL-04 gate. The defensible public statement is that the per-dimension agreement bar
**will be established by LL-04, a planned empirical gate - not a projected
benchmark**; no kappa decimal is claimed until LL-09 closes.

**Path: a stepping-stone 1 -> 3, gated by LL-04.**

1. Build the **M5 provider seam** first - largely *reused* via Inspect's `vllm/`
   provider (PP-07/LL-07), not hand-built.
2. Ship **Option 1 (hybrid)** as the first defensible, shippable milestone - local
   classifier + multi-provider agent evaluation, with the hosted judge named as an
   explicitly **temporary** dependency, not the destination.
3. Run **LL-04 (local-judge calibration)** in parallel as the explicit gate. When
   it clears the per-dimension bar, the product **graduates** from hybrid to the
   sovereign appliance - a planned graduation, not a speculative upgrade.

**Option 2 is the implementation pattern, not a tier.** The Inspect route is how
Options 1 and 3 are wired cheaply; it is not sold or scoped as a separate
destination.

**Partial sovereignty is acceptable - per-dimension degradation.** Where LL-04
clears a dimension locally, that dimension runs air-gapped; where it does not, that
dimension stays on a frontier judge and the appliance is described honestly as
**partially sovereign** for it. One hard dimension does not block the tier.

## Consequences

- **Positive.** A single named target with a concrete, gated path. Option 1 ships
  value early while the sovereign appliance is de-risked incrementally; the seam,
  classifier spike and Inspect reuse are shared across both topologies, so nothing
  is thrown away at graduation. The hybrid topology funds the sovereign build.
- **Commitment / trade-off.** **LL-04 is the load-bearing dependency** and the
  graduation gate; **GPU capex and the resident footprint of two separated model
  families** must be within appetite; execution reproducibility needs **hardware
  pinning** (a GPU/runtime change is a drift event, finding C). The sovereign tier
  carries a permanent re-calibration burden.
- **Risk owned, not closed.** Named tool forcing through Inspect's `vllm/` path
  remains an **empirical open question for LL-01** (the API exposes named forcing;
  provider forwarding to vLLM is untested); the classifier capability floor is
  empirical-pending (LL-02). Until then, no sovereign claim is made as proven.
- **Boundary.** This ADR fixes the build target; it adds no enforcement code and
  modifies no consumer pipeline. CAGE stays product-neutral and holds no customer
  data.

## Alternatives considered

- **Commit directly to Option 3.** Rejected: slowest route to value and bets the
  first release on the unrun LL-04 study and a full GPU buildout before anything
  ships.
- **Stop at Option 1 (hybrid).** Rejected: contradicts the PP-00 sovereignty-primary
  thesis; leaves a permanent hosted-judge dependency in the evidence chain.
- **All-or-nothing sovereignty.** Rejected: one dimension that cannot clear LL-04
  locally would block the entire air-gapped tier; per-dimension degradation is more
  honest and more shippable.
