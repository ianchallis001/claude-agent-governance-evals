# Integration guide - governing an assessment pipeline with CAGE

Status: **a guide, not an installed integration.** CAGE ships the mechanism; a
consumer pipeline calls it. Wiring CAGE into a pipeline's live renderer changes
that pipeline's own customer-facing output, so it is the caller's human-gated
decision (see [ADR-0004](adr/ADR-0004-cage-as-collector-assessment-mechanism.md)).
This guide gives the generic contract first, then one worked example. CAGE does
not reference any consumer's paths at runtime, and no consumer repository is
changed by this one.

## 1. What CAGE offers

| Module | Public names | Use |
|---|---|---|
| `cage.collectors.findings` | `load_findings(path)`, `load_manifest(path)`, `Finding`, `CollectorManifest` | Strict, read-only load of a run's `findings.jsonl` and manifest |
| `cage.collectors.assessment` | `AssessmentConfig`, `AssessmentGate`, `evaluate_assessment`, `Rating`, `pin_assessment_fingerprint`, `template_hash` | PASS / FIX / BLOCK over a set of synthesised ratings |
| `cage.collectors.annex_iii` | `classify_annex_iii(manifest, annex_iii_area=None)` | Two-axis placement plus EU AI Act Art. 9-15 controls for an AI system found in the estate |

The gate is a judge, not an enforcer (ADR-0002): it returns a verdict and an exit
code; the caller decides what to do with it.

## 2. The contract a caller provides (any pipeline)

Any assessment pipeline - whoever owns it - can call CAGE if it can provide:

1. **A findings pack** - a `findings.jsonl` plus its manifest, conforming to the
   collector findings schema v1 (`schema_version` "1"). This is the only input
   shape CAGE reads, and it reads it read-only.
2. **Synthesised ratings**, each citing its evidence with a
   `[ref: <pack>@<ver>/<finding_id>]` token so anchoring can resolve it.
3. **A pinned configuration** - the synthesis model id and the prompt-template
   text (CAGE hashes the template); a change to either is a drift BLOCK.
4. **Optionally, a second-pass derivation** for the reproducibility check.
5. **A place to keep the pin** - one small file per run - so drift is detectable
   on the next run. Where it lives is the caller's design choice.

CAGE takes explicit paths and holds no state, so the caller keeps every run
scoped to its own folder. The pipeline, its findings and its assessments stay the
caller's and are never copied into CAGE.

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

## 4. Worked example - a collector platform (BOARD ticket C-05)

The pipeline that motivated this work is a findings-based collector platform that
renders branded Word / PPT / Excel assessments from a synthesised narrative. For
that consumer specifically, the integration points - all on its side, named here
only to make the C-05 decision concrete - are:

1. **The branded-artefact renderer** - after the narrative is assembled and
   before any branded file is written, call the gate and act on its verdict.
2. **The narrative-writing agent** - require each rating to carry a
   `[ref: <pack>@<ver>/<finding_id>]` token, and keep its prompt template as a
   single versioned text file so it can be hashed.
3. **The run's `findings.jsonl` and manifest** - the inputs to `load_findings` /
   `load_manifest` (schema v1).
4. **A per-run pin file** holding `AssessmentConfig.fingerprint()`.
5. **Per-engagement containment** - the gate runs pinned to one engagement's
   folder; the caller passes only that folder.

These are that consumer's own artefacts; CAGE neither ships nor references them.

## 5. Rollout (any consumer; each step needs a human go-ahead)

1. **Shadow mode.** Run the gate beside the renderer, log the verdict, change no
   output. Measure the BLOCK / FIX rate on past runs.
2. **Advisory.** Surface the verdict in the rendered artefact's cover notes.
3. **Gating.** Only with an explicit decision: a BLOCK withholds rendering and
   routes to the run owner.

Open decisions for the caller's human gate: where the pin lives, who may re-pin
after an intended template change, and whether a FIX permits issue.

## 6. What this does not do

- It does not prove a cited finding *supports* the rating, only that it exists.
- It does not run the synthesiser; reproducibility compares derivations the
  caller supplies.
- It does not move or copy any consumer's data into this repo.
