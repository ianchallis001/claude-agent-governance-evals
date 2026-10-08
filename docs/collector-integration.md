# Collector integration - wiring plan (NOT implemented)

Status: **plan only.** Nothing in the `ai_rag` repository has been changed, and
the rewire described here is a human-gated stop-line (customer-output-affecting;
see [ADR-0004](adr/ADR-0004-cage-as-collector-assessment-mechanism.md) and BOARD
ticket C-05). This document names the integration points so the decision can be
taken with the facts in front of the decision-maker.

## 1. What CAGE offers

| Module | Public names | Use |
|---|---|---|
| `cage.collectors.findings` | `load_findings(path)`, `load_manifest(path)`, `Finding`, `CollectorManifest` | Strict, read-only load of a run's `findings.jsonl` and manifest |
| `cage.collectors.assessment` | `AssessmentConfig`, `AssessmentGate`, `evaluate_assessment`, `Rating`, `pin_assessment_fingerprint`, `template_hash` | PASS / FIX / BLOCK over a set of synthesised ratings |
| `cage.collectors.annex_iii` | `classify_annex_iii(manifest, annex_iii_area=None)` | Two-axis placement plus EU AI Act Art. 9-15 controls for an AI system found in the estate |

The gate is a judge, not an enforcer (ADR-0002): it returns a verdict and an exit
code; the caller decides what to do with it.

## 2. ai_rag integration points (read-only references)

These are the places in `C:/ai_rag` a later change would touch. None is modified
by this repo.

1. **`Templates/Scripts/render-collector-artefacts.py`** - renders the branded
   Word / PPT / Excel outputs for
   `Customers/{Customer}/Engagements/{engagement-id}/Assessments/{run-id}/`.
   Proposed hook: after the narrative inputs are assembled and before any
   branded file is written, call the gate and act on its verdict.
2. **`AssessmentNarrativeWriter` agent** (`.claude/agents/`) - produces the
   synthesised ratings and narrative. Proposed change: require each rating to
   carry a `[ref: <pack>@<ver>/<finding_id>]` token, and keep its prompt
   template as a single versioned text file so it can be hashed.
3. **`Collectors/{run-id}/findings.jsonl` and the run manifest** - the inputs to
   `load_findings` / `load_manifest`. Schema contract:
   `.claude/agents/references/collector-findings-schema.md` (CAGE accepts
   `schema_version` "1" only).
4. **Pinned-fingerprint store** - a new per-engagement file (suggested:
   `Assessments/{run-id}/assessment.pin.json`) holding the output of
   `AssessmentConfig.fingerprint()`. Its location is a design decision for the
   platform owner.
5. **Cross-customer containment (r28-r31)** - the gate must run pinned to one
   engagement tree. CAGE takes explicit paths and holds no state, so this is a
   matter of the caller passing only that engagement's folder.

## 3. Call sketch (ASCII; illustrative, not committed code)

```python
from cage.collectors import (
    AssessmentConfig, Rating, evaluate_assessment, load_findings,
)

findings = load_findings(run_dir / "findings.jsonl")
config = AssessmentConfig(model=MODEL_ID, template_text=template_path.read_text())

pinned = read_pin(run_dir)          # first run: write config.fingerprint()
ratings = [Rating(r.id, r.text) for r in narrative_ratings]
rederived = [Rating(r.id, r.text) for r in second_pass]   # optional

result = evaluate_assessment(config, pinned, findings, ratings, rederived)
# result.gate is PASS / FIX / BLOCK; map to exit code 0 / 1 / 2.
```

For an AI system discovered in the estate, the Annex III surface is called the
same way: `classify_annex_iii(manifest, annex_iii_area=...)`, or through
`cage classify` / the MCP `classify_manifest` tool.

## 4. Proposed rollout (each step needs a human go-ahead)

1. **Shadow mode.** Run the gate beside the renderer, log the verdict, change no
   output. Measure the BLOCK / FIX rate on past runs.
2. **Advisory.** Surface the verdict in the rendered artefact's cover notes.
3. **Gating.** Only with an explicit decision: a BLOCK withholds rendering and
   routes to the engagement owner.

Open decisions for the human gate: where the pin lives, who may re-pin after an
intended template change, and whether a FIX permits issue.

## 5. What this does not do

- It does not prove a cited finding *supports* the rating, only that it exists.
- It does not run the synthesiser; reproducibility compares derivations the
  caller supplies.
- It does not move or copy customer data into this repo.
