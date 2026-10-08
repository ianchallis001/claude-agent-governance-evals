# ADR-0004: CAGE is the assessment-governance mechanism for the Collector platform

- **Status:** Accepted (mechanism); live renderer wiring DEFERRED (see below)
- **Date:** 2026-10-08
- **Deciders:** Ian Challis (single-author repo; see ADR-0003)
- **Context tickets:** C-01 (`collectors/findings.py`), C-02 (`collectors/assessment.py`),
  C-03 (`collectors/annex_iii.py`), C-04 (docs), C-05 (deferred live wiring)

## Context

The Affinity Reply Collector platform gathers machine findings from a customer
estate and then *synthesises* them into client-facing assessments (ratings,
narrative, scorecards). The platform's design document (section 31a) states an
intent that this synthesis step is **"governed as a model"**: that an LLM-written
rating is subject to the same discipline as any other model in production -
evidence it can be traced to, a pinned configuration that fails on change, and a
reproducibility check.

That intent was **design-only**. Nothing in code enforced or even measured it. A
synthesiser could emit a rating that no finding supports, could silently change
when its prompt template or model moved, and could give a different answer on a
second run, and nothing would flag any of it before the output reached a
customer.

CAGE already contains the right parts: a pin-and-fingerprint drift gate
(`cage.evals.drift`), a PASS / FIX / BLOCK aggregation with an exit-code mapping
(`cage.evals.runner`), a structural can't-be-outvoted BLOCK pattern
(`manifest_integrity`, P5-03), and a two-axis classifier with EU AI Act control
rows (`cage.classify`, `cage.regmap`).

## Decision

**CAGE is the assessment-governance mechanism for the Collector platform.** It
provides, as an offline, stdlib-only library under `cage.collectors`:

1. **A read-only findings-pack adapter** (`cage.collectors.findings`): strict
   loaders for `findings.jsonl` and the collector manifest, with pinpointing
   `ManifestError` messages. They never write to a run folder.
2. **An assessment-governance gate** (`cage.collectors.assessment`) that rolls
   three checks into one PASS / FIX / BLOCK verdict using the runner's own
   BLOCK > FIX > PASS precedence and 0/1/2 exit codes:
   - evidence anchoring: every synthesised rating must cite a resolvable
     `[ref: <pack>@<ver>/<finding_id>]`; an unanchored or dangling rating is a
     BLOCK that cannot be outvoted;
   - template-hash drift: the model fingerprint is extended with a SHA-256 of
     the prompt template, so a template change BLOCKs exactly as a model-id
     change does (it reuses `cage.evals.drift`, not a fork);
   - reproducibility envelope: a re-derivation under the same pin must match
     within tolerance; a miss is a FIX, not a BLOCK.
3. **An Annex III reuse surface** (`cage.collectors.annex_iii`): a thin wrapper
   over `cage.classify.classify_manifest` that places an agent on the two-axis
   matrix and returns only the EU AI Act Art. 9-15 high-risk controls, reachable
   by library import, the `cage` CLI or the MCP server.

### The ADR-0002 split still holds

The gate is a **classifier and judge, not an enforcer**. It returns a verdict a
caller reasons about; it does not block a render, delete an output or trip a
kill-switch. Whether a BLOCK stops an assessment being issued is the caller's
decision, in the caller's pipeline. Nothing in `cage.collectors` sits in a
request path, and no enforcement code is added to this repo.

### Live renderer wiring is DEFERRED to a human-gated step

This ADR accepts the **mechanism**. It does **not** authorise rewiring the
Collector platform's live renderer (`render-collector-artefacts.py`) or the
`AssessmentNarrativeWriter` agent to call it. That change alters customer-facing
output (a BLOCK would withhold or alter an assessment a client receives), so it
is a stop-line: it needs an explicit human decision, a rollout plan (shadow mode
first) and a customer-scope check. The wiring plan is written up in
[`docs/collector-integration.md`](../collector-integration.md) and tracked as
BOARD ticket **C-05**, open. Until C-05 is closed, the platform's "governed as a
model" intent remains **unenforced in the live pipeline**, even though the
mechanism now exists and is tested.

## Consequences

- **Positive.** Section 31a stops being aspirational: there is a tested,
  deterministic gate with documented semantics, usable today from a script, CI
  or an MCP-aware agent. The structural controls (anchoring, drift) need no API
  key and no network, so they can run on customer-hosted infrastructure. The
  runtime stays stdlib-only and the SDK imports stay lazy.
- **Negative / trade-off.** Anchoring proves a rating *cites* a real finding,
  not that the finding *supports* the rating; a semantic-support judge would be
  an LLM call and is out of scope here. Reproducibility is compared on supplied
  derivations; this repo does not run the synthesiser. The mechanism protects
  nothing until C-05 is done.
- **Boundary.** No change is made to the `ai_rag` repository by this work. CAGE
  holds no customer data: the adapter reads a pack path the caller supplies and
  is read-only.

## Alternatives considered

- **Build the checks inside the Collector repo.** Rejected: it duplicates the
  fingerprint, aggregation and abstain logic CAGE already tests, and the two
  would drift apart.
- **Wire the renderer in the same change.** Rejected: it changes customer
  output and so needs a human gate; shipping the mechanism first, in shadow
  mode, is reversible.
- **An enforcing gate in the render path.** Rejected for the reasons in
  ADR-0002.
