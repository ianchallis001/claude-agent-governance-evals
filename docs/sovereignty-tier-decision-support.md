# CAGE sovereignty-tier decision support – PP-00 product thesis and LL-00 target tier

**Status:** decision support for two human sign-off gates. **Nothing here selects an option.**
**Date:** 2026-11-10
**Scope:** product-neutral throughout. No client, buyer or consumer names appear.

> **Resolved 2026-11-10.** The two gates this paper supports have since been
> decided and recorded in [`ADR-0005`](adr/ADR-0005-provider-portability-scope-and-product-thesis.md)
> (PP-00: sovereignty-primary; multi-provider in scope; one codebase, two
> topologies) and [`ADR-0006`](adr/ADR-0006-target-sovereignty-tier.md) (LL-00:
> target Option 3, via a stepping-stone 1 -> 3 gated by LL-04; per-dimension
> partial sovereignty accepted). This document is retained as the decision-support
> record; the draft ADR scaffolds at its end are **superseded** by those two ADRs.

---

## How to read this

This paper equips two linked decisions. It does not make either of them.

- **PP-00** is the upstream product-thesis gate: what *is* CAGE, and what primarily drives it – reach, sovereignty, or cost?
- **LL-00** is the downstream execution gate: which target sovereignty tier (Option 1 hybrid, Option 2 Inspect-routed, Option 3 fully sovereign) do we build toward?

Both are **human sign-off gates**. The Decision fields in the two ADR scaffolds at the end are deliberately left blank. Where this paper offers a view, it is a *proposal for discussion* – a reasoned sequencing suggestion, explicitly caveated – not a recommendation to be rubber-stamped.

Three honesty constraints run through everything below, and should travel with any figure lifted from this paper:

1. **Every kappa and capability figure is indicative only.** The judge-reliability research returned several malformed arXiv identifiers (ticket LL-09, still open). The *directional* findings – kappa deflation on harder rubrics, self-preference, same-family bias, panel amplification – are well established. The specific decimal thresholds are not verified against primary sources. No kappa number may be presented as established.
2. **The Inspect route is desk feasibility only.** The PP-07/LL-07 spike reviewed documentation; no GPU or vLLM was run. "The docs say supported" is not "proven by running it". The load-bearing uncertainty – whether named `tool_choice` forcing survives Inspect's vLLM path, or must be rebuilt as guided/constrained decoding – is handed to LL-01 on real hardware.
3. **Option 3's completeness is conditional on LL-04.** If a local open-weight judge cannot clear the per-dimension agreement bar on some dimensions, those dimensions must stay on a frontier judge, and Option 3 is *partially* sovereign for them, not fully air-gapped.

---

## PP-00 – the product-thesis framing

PP-00 asks three interlocked questions. They must be answered jointly, because the answer to (c) reframes (a) and (b).

**(a) Scope.** Is CAGE a Claude-anchored governance tool, or does it evaluate agents built on any provider? This is not an architecture question – the M5 provider seam enables both. It is a product-positioning decision that sets addressable market, maintenance commitment and regulatory fit.

**(b) Axis of portability.** Does multi-provider portability apply to the *agent-under-test* (what CAGE evaluates), the *judge/runtime* (the model CAGE uses to render verdicts), or both? These are separable, with different capability gates. The agent axis is a today-capability (M5 + LL-01). The judge axis depends on LL-04 clearing the per-dimension agreement bar first.

**(c) Primary driver.** Reach, sovereignty/air-gap, or cost? This is the load-bearing question, because "reach" and "sovereignty" point to *different products*:

- **Reach** → a universal evaluation harness. Maximum addressable market; Option 1 or 2 delivered quickly; an external hosted link remains in the judge. The moat is the control catalogue (the regmap against EU AI Act, GDPR, ISO 42001, NIST AI RMF, PRA SS1/23, SR 11-7) and cross-vendor neutrality – not the runtime. Reach alone is a commoditising direction: the hyperscalers will build governance tooling for their own ecosystems.
- **Sovereignty** → a sovereign governance appliance. The whole pipeline runs locally, no outbound data, reproducible evidence chain (pinned weights + OCI digest + hardware fingerprint). The market narrows to a premium regulated segment; the Annex IV evidence story is the strongest available; the differentiator is closing the AI-oversight evidence loop *without introducing a new hosted AI dependency*.
- **Cost** → a real but secondary benefit. A local classifier removes per-classification spend at scale, and is a credible incidental gain of Option 1. It does not, on its own, justify the LL-04 judge-calibration effort (high effort, high risk). Cost is a strong *second* argument once sovereignty is primary ("the sovereign option also eliminates per-token API exposure"), not a headline.

The stated priority is **sovereignty**, which makes (c) the dominant gate. LL-00 is the downstream execution of whatever PP-00 concludes.

---

## Comparison matrix

Rows are the rubric axes; columns are the three options. Read it alongside the per-option sections – the matrix compresses; the trade-offs live in the prose.

| Axis | Option 1 – Hybrid | Option 2 – Inspect-routed | Option 3 – Fully sovereign |
|---|---|---|---|
| Classifier | Local vLLM (Qwen3-32B primary) | Local via Inspect `vllm/` provider | Local vLLM (Qwen3-32B primary) |
| Judge | Pinned frontier, hosted | Via Inspect `--model-role grader=` (hosted or local) | Local, calibrated open-weight |
| What it delivers | Govern any vendor's agents + partial cost saving | Breadth cheaply by reusing the Inspect adapter set | Full air-gap / data residency on both roles |
| Air-gapped? | No – judge calls out | No, unless the grader is pinned local | Yes – zero egress |
| Capability status | Classifier = today-capability (finding A); judge already validated (frontier) | Classifier = today-capability on docs; forcing control unproven on hardware | Classifier = today-capability; judge = unproven (finding B, LL-04) |
| Sovereignty / regulatory value | Partial, classifier-side only; judged content still leaves the enclave | None intrinsic – a property of the endpoint, not the router | Full – residency, DORA/UK-GDPR/SS1/23 overhead removed for the pipeline |
| Reproducibility | Classifier gains a stronger weight anchor but inherits GPU/FP nondeterminism; judge keeps hosted-snapshot pin | Inherits vLLM envelope + an Inspect version-pin dependency | Strong weight anchor, weak execution anchor; hardware pinning mandatory |
| Main risk | Retained hosted-judge dependency – not sovereign | Cedes message-level call control; loop may not survive the solver abstraction | Judge calibration (LL-04) + execution reproducibility under GPU nondeterminism |
| Effort | Low-medium | Low (a spike) | High |
| Cost profile | Partial saving – classifier off hosted spend; heavier judge call remains | Inherited from whatever endpoints you route to | Removes hosted spend on both roles; GPU capex/opex dominate |
| Reversibility / optionality | Strong – a stepping stone to Option 3; reverts to hosted baseline by config | Highest – a wiring substrate under Options 1 and 3; upgrade by swapping one endpoint | Reachable *through* Option 1; the GPU capex + LL-04 study are the real commitment |
| Critical tickets | PP-00/PP-01, LL-01, LL-02, LL-03, LL-05, LL-06 (not LL-04/LL-08) | PP-07/LL-07 (resolved), LL-01 reframed, PP-05, LL-04 only if grader local | LL-01, LL-02, LL-03, LL-04, LL-05, LL-08 (depends on M5) |

---

## Per-option detail

### Option 1 – Hybrid (local vLLM classifier, pinned frontier hosted judge)

Move the classifier onto local open weights (vLLM named-call + xgrammar constrained decoding) while the judge stays on a pinned frontier hosted model. The defensible first product step: it governs any vendor's agents today and banks a partial cost saving, without betting the release on the unproven local-judge calibration.

**Pros**
- Classifier side is a today-capability on a verified primary-doc mechanism (finding A): vLLM's named `tool_choice` + xgrammar blocks free text, hallucinated tool names and wrong-type fields at the token mask – exactly CAGE's forced-submit design; the existing `verdict_from_tool_input` range-check plus one schema-repair retry backstops the semantic ranges constrained decoding does not cover.
- Sidesteps the single hardest, unproven result – the local-judge per-dimension agreement gate (LL-04) – by keeping the judge on a validated frontier model. No recalibration on the critical path.
- Delivers reach plus a real partial cost saving in one step.
- Judge/classifier family separation holds by construction (a hosted frontier judge cannot share a base family with a local Qwen classifier).
- Reuses the M5 seam and extended drift fingerprint, so it is a clean stepping stone to Option 3 and composes with Option 2.
- Low-medium effort; most seam-build work is removed by the resolved LL-07 reuse finding.

**Cons**
- Not air-gapped and not sovereign – the hosted judge dependency is the headline limitation; judged trajectories and verdicts leave the enclave.
- Only a partial cost saving; the heavier judge call (dense per-dimension rubric) stays on the vendor bill.
- Takes on GPU capex/opex, MLOps, and mandatory hardware pinning the hosted baseline avoided – two runtimes, two drift surfaces.
- Forced-submit control through Inspect's vLLM path is unproven on hardware (LL-01).
- Classifier capability floor still empirical-pending beyond a BFCL proxy (LL-02).

**Best when** the primary driver is reach plus partial cost control rather than air-gap; the buyer can live with a hosted judge; and the organisation wants a defensible, shippable step now while keeping the Option 3 door open, with GPU available to host a 32B-class classifier on pinnable hardware.

**Risks** forced-submit unverified on hardware (LL-01); capability floor unproven (LL-02); must not be positioned as sovereign; new GPU/FP-nondeterminism and hardware-pinning obligations (LL-05).

**Effort** Low-medium. LL-00 → M5 seam (PP-00/PP-01) → LL-01 → LL-02 → LL-03 → LL-05 → LL-06. LL-04 and LL-08 are deliberately out of scope.

**Reversibility** Strong. The seam and drift fingerprint are exactly what Option 3 reuses; upgrading means swapping the hosted judge for a local calibrated one once LL-04 passes, with no classifier rework. Reverts downward to a fully-hosted baseline by config if LL-02 underwhelms. Low lock-in in every direction.

### Option 2 – Inspect-routed (both roles through UK AISI Inspect's model layer)

Route the classifier via Inspect's built-in `vllm/` provider and the judge via `--model-role grader=... required=True`. Now that PP-07/LL-07 has resolved hybrid/reuse, read this **not as a distinct destination but as a low-cost wiring vehicle**: Inspect's `vllm/` provider effectively *is* the `OpenAICompatClient` seam, so this route delivers Option 1's shape (and the classifier half of Option 3) cheaply, with one load-bearing control – named tool forcing – that must be rebuilt as guided decoding and proven on hardware.

**Pros**
- Cheapest path to live multi-provider wiring – reuses ~29 Inspect adapters instead of CAGE owning per-provider wire code.
- Inspect's `vllm/` provider is the seam LL-01 would otherwise have built – avoids writing `OpenAICompatClient` at all.
- Enforced family separation via `required=True` grader role – raises rather than silently falling back to the agent's own model, the exact guarantee CAGE's judge isolation needs.
- Structured JSONL logs and transcripts give Annex IV closed-loop evidence for free.
- Maximal optionality for minimal commitment: delivers Option 1's shape today, wires the classifier half of Option 3, upgrades to full sovereign by swapping one endpoint.
- Architecture question already de-risked – both spikes resolved hybrid, so residual uncertainty is empirical, not design.

**Cons**
- Cedes the one load-bearing control – SDK-level named tool forcing – which must be rebuilt as guided decoding and is unproven to reproduce the "verdict only from the typed call" guarantee.
- Everything is desk-verified; nothing has run on GPU/vLLM, so loop-survival and forcing equivalence are open empirical questions.
- Dependency and lock-in on Inspect's internal `ChatMessage*`/`ToolCall` types and release cadence; version-pinning risk.
- Not itself a sovereignty destination – air-gap still requires pinning the grader local and clearing LL-04.
- Oversells if read as a distinct "destination" rather than plumbing beneath Options 1 and 3.

**Best when** the driver is reach and speed-to-live-wiring with the least net-new code CAGE must own; when you accept ceding SDK-level forcing because the authoritative intensity matrix (not the model's self-report) carries the real control on the agent axis; and when you want the sovereign upgrade kept open. Right as the wiring substrate *beneath* the Option 1 step and the Option 3 appliance, run as an early spike – not as a standalone destination.

**Risks** named `tool_choice` may be silently dropped (LL-01); desk-only, nothing run on GPU; cedes message-level call control to the solver abstraction; Inspect API/version lock-in; if the grader is pushed local, LL-04 becomes a hard unresolved dependency.

**Effort** Low. A spike, not a build – LL-07 is resolved hybrid/reuse on a desk basis, and the live wiring may discharge most of Option 1's LL-01 for free. LL-01 shifts from "build the seam" to "prove the forcing/guided-decoding equivalence on hardware". Weeks, not quarters, assuming GPU access.

**Reversibility** Highest of the three. Reversible in both directions: upgrade toward sovereign without re-plumbing, or fall back to a bespoke hand-built local adapter only if LL-01 shows the forced-verdict control cannot be reconstructed through guided decoding. The only standing threat is lock-in to Inspect's abstraction – pin a version and keep CAGE's governance logic outside Inspect's types to preserve the exit.

### Option 3 – Fully sovereign / air-gapped (both roles on local open-weight models)

A sovereign governance appliance running classifier and judge on local open-weight models inside an air-gapped enclave with zero egress. Maximal sovereignty, but load-bearing on a local-judge calibration result that is not yet proven.

**Pros**
- True sovereignty and air-gap – zero egress, absolute data residency; no agent trajectory, manifest or verdict leaves the boundary.
- No hosted dependency on either role; immune to a hosted model being withdrawn or silently rolled.
- Durable weight-level reproducibility anchor (weight SHA256 pinned indefinitely).
- Closed-loop Annex IV evidence generated and kept inside the boundary.
- Unlocks buyers no hosted option can reach (egress-prohibited, classified, residency-mandated); removes per-call frontier inference spend.
- Reachable incrementally from Option 1 – the seam and classifier work are shared.

**Cons**
- The local judge is unproven – LL-04 per-dimension agreement may fail on the hard dimensions (figures indicative only, LL-09).
- Execution reproducibility is weak under GPU/FP nondeterminism; needs same-hardware pinning.
- High effort plus GPU capex/opex; the operator bears it.
- Heavy, permanent maintenance burden – own the whole stack and re-calibrate on every model/hardware change.
- Family separation (judge family must differ from classifier family) roughly doubles the resident-model footprint.
- Forced-verdict control still to be rebuilt as guided decoding and proven on hardware (LL-01).
- Slowest route to value – wrong as a first product step.

**Best when** sovereignty is the primary driver – a hard egress prohibition or residency mandate no hosted judge can satisfy – AND the buyer accepts GPU capex and full operational ownership of the enclave, AND LL-04 has already cleared for the dimensions that buyer needs governed. Right as a deliberate second step after Option 1 has proven the product and the seam, scoped as a standalone sovereign appliance – not the opening move.

**Risks** LL-04 is the load-bearing dependency (a dimension that cannot clear the agreement bar locally cannot be governed air-gapped); execution reproducibility under GPU/FP nondeterminism; forced-verdict control unproven (LL-01); family-separation constrains model choice and doubles footprint; figures indicative only (LL-09); GPU capex/supply; model/runtime lifecycle events (patch, quantisation change, GPU refresh) are drift events that can force full re-calibration.

**Effort** High – the full M6 sovereign stack, and the only option that must land the judge-calibration study end-to-end. Depends on the M5 seam being in first. Realistically sequenced after Option 1 has proven the product; the critical path runs through LL-04, which can return a partial (per-dimension) answer.

**Reversibility** Best reached *through* Option 1, not instead of it – the seam, the classifier spike and the Inspect reuse are shared. Not a one-way door: ship on Option 1 and add the sovereign appliance as a separately-scoped variant. Reversibility is also per-dimension – dimensions that clear local agreement run air-gapped while the rest stay on a frontier judge (a graceful but hybrid degradation). The genuine commitment is the GPU capex and the calibration study: you cannot cheaply "try" full air-gap.

---

## Sovereignty lens

Because sovereignty is the stated priority, the regulatory and buyer drivers below bear directly on the Option 1/3 choice. Data-residency alone is satisfied by Option 1; *full air-gap* (Option 3) is a genuine hard requirement for a narrower set.

**Who genuinely needs full air-gap (Option 3), not merely data-residency:**

- **Defence and classified government.** An evaluation transcript of an agent that has processed SECRET-or-above data cannot call out to a hosted judge – the transcript contains outputs derived from classified inputs. Option 3 is non-negotiable here; the OCI-digest + no-egress + weight-hash-on-ingest architecture (LL-08) is the minimum viable deployment.
- **Tier-1 regulated UK/EU financial services.** Not a hard air-gap mandate, but converging obligations make a hosted judge costly to defend: under **PRA SS1/23** the judge is a model within the model-risk inventory and validation regime (a local judge with pinned weights and an LL-04 agreement record is materially easier to validate than a rotating hosted snapshot); under **DORA Article 30** a hosted LLM judge is an ICT third-party service provider needing a written contract, exit strategy and concentration-risk reporting (a local judge removes the register entry entirely); under **UK GDPR / GDPR Article 46** transcripts containing AI outputs derived from customer data are personal data, so a US-hosted judge is an international transfer needing SCCs or equivalent (a local judge eliminates the category).
- **UK and EU public sector.** The UK AI Opportunities Action Plan's sovereign-compute commitment (DSIT, January 2025) and NCSC AI guidance steer sensitive workloads toward UK-controllable infrastructure. A local deployment on sovereign compute is more procurable than a tool with a hosted foreign-AI dependency in its evidence chain.
- **Sovereign-AI mandate holders** – critical AI infrastructure designations, export-controlled AI outputs, government-mandated sovereign frameworks. EU AI Act Annex III high-risk categories (critical infrastructure, law enforcement, administration of justice) are exactly where a governance tool's own AI dependency draws scrutiny.

**Satisfied by data-residency + Option 1 (hybrid):** mid-tier commercial FS and insurance on UK-region cloud with a UK-resident frontier judge under a data-processing addendum; SaaS/technology firms without hard residency requirements; EU-jurisdiction organisations using an EU-hosted judge where the transfer issue does not arise. The distinction: full air-gap is required when (a) the data the agent saw cannot leave the evaluation environment, (b) the evidence record itself is classified or legally restricted, or (c) a sovereign mandate prohibits outbound AI dependencies in the governance layer. Data-residency alone suffices when none of (a)–(c) applies.

**Regulatory drivers, load-bearing for the choice:**

- **EU AI Act Annex IV evidence** (Reg (EU) 2024/1689, Art 11 + Annex IV): technical documentation for high-risk systems must be reproducible enough for a competent-authority audit. A local judge with pinned weight hash, OCI-digest-pinned runtime, hardware fingerprint and seed=0/temperature=0 is a materially stronger Annex IV record than a rotating hosted snapshot. The high-risk deadline was **postponed to 2 December 2027** by Reg (EU) 2026/1744 (Digital Omnibus) – not 2 August 2026; Annex I moves to 2 August 2028; Article 50 transparency stays at 2 August 2026. This gives a window to build Option 3 before Annex IV fully bites.
- **EU AI Act Article 12 logging:** traceable, reproducible logs. A hosted judge introduces a third-party artefact into the evidence chain; a local judge makes the log self-contained.
- **DORA Article 30 / PRA SS1/23 / UK GDPR Article 46:** each adds overhead to a hosted judge (third-party register, model inventory, international transfer) that a local judge removes for the governance pipeline.
- **UK AI Opportunities Action Plan + Sovereign AI Unit** (DSIT, January 2025; One Year On, January 2026): sovereign-compute expansion backed by public funding. A governance tool with a hosted foreign-AI judge is structurally misaligned with the direction of UK public-sector AI procurement.

**Market context (November 2026):** open-weight capability for *structured governance tasks* has matured – the Qwen3 family leads multi-turn structured tool-calling among open models (figures indicative, LL-09), and for CAGE's narrow forced-call schema a local classifier is a today-capability; vLLM is the de facto serving stack (TGI is in maintenance mode); there is demonstrated tier-1 UK FS appetite for deployable frontier-class models for sensitive workloads; and UK AISI's Inspect is credible infrastructure to build on. Existing governance/observability tooling focuses on tracing, not on *sovereign reproducibility of governance verdicts* – an unoccupied position the 2 December 2027 high-risk deadline will make more valuable.

---

## Key tensions

- **Reach versus sovereignty is a product-definition fork, not a feature toggle.** Reach points to a universal harness (broad market, commoditising, moat in the catalogue not the runtime); sovereignty points to a premium appliance (narrow market, strongest evidence story, differentiated). The provider seam serves both, but the positioning, buyer and maintenance commitment differ.
- **The classifier is proven; the judge is the real gate.** Local classification is a today-capability (finding A, primary-doc-verified). A local judge rests on LL-04, the hard, unrun calibration study – and its completeness may be only *per-dimension*.
- **Stronger weight anchor, weaker execution anchor.** Local pinned weights beat any hosted snapshot for reproducibility on the weight axis, but GPU/FP nondeterminism means identical weights can drift across hardware generations – so hardware pinning is mandatory and a hardware change is a drift event (finding C).
- **Cost falls but does not vanish in Option 1; Option 3 removes hosted spend but imposes GPU capex.** At low volume a hosted frontier judge is cheaper than idle GPUs; sovereignty, not cost, is the honest justification for Option 3.
- **Desk feasibility is not execution.** The Inspect route is attractive precisely because it removes seam-build effort – but the one control it cedes (SDK-level named forcing) is the one thing unproven on hardware, and must survive as guided decoding (LL-01).
- **Optionality versus commitment.** Options 1 and 2 keep the sovereign door open cheaply; Option 3's genuine commitment (GPU capex + the LL-04 study) cannot be "tried" cheaply. Naming Option 3 as the target *now* creates urgency for LL-04 but risks buyers reading the product as "Option 1 with a possible upgrade" and waiting.

---

## Recommendation for discussion (a proposal, not a decision)

Offered for debate at PP-00/LL-00; the gates remain open.

Given sovereignty as the stated priority, the framing worth testing is: **CAGE is a sovereign governance appliance by design, with cross-vendor reach on the agent axis as a feature the seam delivers rather than a competing thesis.** On that framing:

- **Build the M5 provider seam first** – it is the shared foundation for all three options, and the LL-07 finding means it is largely *reused* (via Inspect's `vllm/` provider) rather than hand-built.
- **Land Option 1 as the first defensible, shippable milestone** – local classifier + multi-provider agent evaluation today, with the hosted judge named as a *temporary* dependency, not the destination.
- **Treat Option 2 as the implementation pattern inside Option 1, not a product tier** – it is the wiring substrate, not a standalone destination.
- **Run LL-04 (judge calibration) in parallel as the explicit gate to the named target.** When it clears the per-dimension bar, the product graduates from hybrid to a fully sovereign appliance – a planned graduation, not a speculative upgrade. Where LL-04 does not clear a dimension, that dimension stays on a frontier judge and the appliance is honestly described as *partially* sovereign there.
- **Consider framing the two tiers as one codebase, one rubric, two deployment topologies** – hybrid for buyers without hard air-gap needs, sovereign for regulated FS, public sector and defence – so the product addresses both reach and sovereignty without a fork.

This is a *sequencing* proposal (a stepping-stone path, Option 1 now → Option 3 as a gated graduation), deliberately not a selection of a single option. The honest complication to surface: the agreement figures underpinning the Option 3 judge argument are indicative and unverified (LL-09 open). The defensible public statement is that the per-dimension bar *will be established by LL-04, a planned empirical gate, not a projected outcome* – more credible with a regulated buyer than any unverified benchmark decimal.

---

## Open decision points – the exact questions for the human

**PP-00 (product thesis):**
1. Scope: is CAGE multi-provider by definition, or deliberately Claude-anchored with provider-neutrality a non-goal?
2. Axis: does portability apply to the agent-under-test only, the judge/runtime only, or both – and if both, staged or simultaneous?
3. Primary driver: reach, sovereignty/air-gap, or cost – and is cost accepted as a secondary benefit rather than a headline?
4. Do we name Option 3 (sovereign appliance) as the target state now, accepting the urgency it places on LL-04, or hold it as an explicitly conditional future tier?
5. Do we adopt the tiered single-codebase framing (hybrid + sovereign as deployment topologies), or commit to one posture?

**LL-00 (target sovereignty tier):**
6. Which tier is the build target – Option 1, Option 2 (as substrate), or Option 3 – and is a stepping-stone path (1 → 3, gated by LL-04) acceptable versus a direct commitment?
7. Is the board content to proceed with every kappa/capability figure marked indicative until LL-09 closes, and with the Inspect route treated as unproven until LL-01 runs on hardware?
8. Is GPU capex (and, for Option 3, the resident-model footprint of two separated families plus enclave operation) within appetite?
9. For Option 3: is a *partially* sovereign outcome (some dimensions local, some on a frontier judge) acceptable if LL-04 does not clear every dimension?
10. What is the trigger and owner for re-visiting the tier once LL-04 and LL-02 report?

---

## Draft ADR scaffolds

Both are **Status: Proposed** with the **Decision section left open**. They are scaffolds for the human sign-off, not pre-filled decisions.

### ADR-0005 – Provider-portability scope (PP-00)

```
# ADR-0005 – Provider-portability scope and product thesis

Status: Proposed
Date: 2026-11-10
Deciders: [human sign-off – to be named]
Gate: PP-00 (human sign-off)

## Context
CAGE classifies and judges AI-agent governance against a control catalogue it
supplies itself (regmap: EU AI Act, GDPR, ISO 42001, NIST AI RMF, PRA SS1/23,
SR 11-7). The M5 provider seam (PP-01) enables both a Claude-anchored and a
multi-provider shape; this ADR fixes the product thesis, not the architecture.
Three questions interlock: (a) scope – Claude-anchored or any-provider;
(b) axis – agent-under-test, judge/runtime, or both; (c) primary driver –
reach, sovereignty/air-gap, or cost. The stated priority is sovereignty.
Caveat: kappa/capability figures are indicative only (LL-09 open); the Inspect
route is desk feasibility only (no GPU run).

## Options considered
- Claude-anchored evaluation harness (narrow scope, no seam investment).
- Multi-provider reach harness (agent axis portable; hosted judge).
- Sovereign governance appliance (both axes portable; local judge, gated by LL-04),
  optionally offered as a tiered single codebase with a hybrid topology.

## Decision
<left open – to be completed at PP-00 human sign-off>

## Consequences
<to be completed once the decision is recorded>

## Related
LL-00 (target sovereignty tier ADR); tickets PP-01, PP-05, LL-04, LL-07, LL-09.
```

### ADR-0006 – Target sovereignty tier (LL-00)

```
# ADR-0006 – Target sovereignty tier

Status: Proposed
Date: 2026-11-10
Deciders: [human sign-off – to be named]
Gate: LL-00 (human sign-off); depends on PP-00 / ADR-0005

## Context
Downstream of the PP-00 thesis. Selects the build target among:
- Option 1 Hybrid: local vLLM classifier + pinned frontier hosted judge.
  Today-capability on the classifier; judge validated by construction; not
  air-gapped. Low-medium effort.
- Option 2 Inspect-routed: both roles via UK AISI Inspect's model layer.
  Best read as the wiring substrate beneath Options 1 and 3, not a destination.
  Low effort (a spike); resolved hybrid/reuse on desk feasibility.
- Option 3 Fully sovereign: both roles on local open-weight models, air-gapped.
  Full residency; load-bearing on LL-04 per-dimension calibration (unproven);
  high effort + GPU capex.
Binding caveats: every kappa/capability figure is indicative (LL-09 open); the
Inspect route is desk-only until LL-01 runs on hardware; Option 3 completeness is
conditional on LL-04 and may be only per-dimension. Judge base-model family must
differ from classifier family (structural gate). Excluded from the control path:
Ollama (cannot force a tool call), Mistral Tekken v11+ (broken forced-call in
vLLM); TGI not adopted (maintenance mode).

## Decision
<left open – to be completed at LL-00 human sign-off>
<record: chosen tier; whether a stepping-stone 1 -> 3 path is adopted; the LL-04
 gate condition for any graduation to sovereign>

## Consequences
<to be completed once the decision is recorded – GPU capex, maintenance surface,
 regulatory posture, which tickets are in/out of scope>

## Related
ADR-0005 (PP-00); M5 provider seam (PP-01/PP-04/PP-05/PP-06/PP-07);
LL-01, LL-02, LL-03, LL-04, LL-05, LL-06, LL-08, LL-09.
```
