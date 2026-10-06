# ADR-0003: Single-author DoD adaptation - CI gate substitutes for peer review

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** Ian Challis (single author)
- **Context:** the global Definition of Done in [BOARD.md](../../BOARD.md) and the
  per-ticket DoD in [SPEC.md](../../SPEC.md) section 11

## Context

The project ships with a real Definition of Done. Most of it is objective and
machine-checkable per ticket:

- runtime is stdlib-only (lazy `anthropic` / `mcp`);
- code and console output are ASCII only;
- no `TODO` or stub is left behind (deferred bodies raise
  `NotImplementedError("<ticket id>")` until their ticket lands);
- the module's tests pass under `python -m pytest`;
- the behaviour added is covered by a new or updated test;
- `ruff check .` is clean.

A conventional engineering DoD also carries a **second-person gate**: a change is
not Done until an *independent reviewer* has approved it. That gate is there to
catch what the author cannot see in their own work, and to stop one person
from being the only thing between a mistake and `main`.

This repository has **one author**. There is no second engineer to approve a pull
request, so the peer-review DoD item cannot be met as written. Pretending
otherwise - marking "reviewed" when no independent review happened - would be
dishonest, and honesty is a stated goal of this project.

## Decision

Record the peer-review item as an explicit, named **DoD exception** for a
single-author repository, and **substitute an automated CI gate** in its place
rather than quietly dropping it. A ticket is Done only when, on top of the
objective items above, the CI workflow
(`.github/workflows/ci.yml`) passes on the change:

1. **`ruff check .`** - lint is clean;
2. **`python -m pytest -q`** - the full offline suite passes (219 tests today);
3. **`cage eval --mock --threshold 0.8`** - the harness runs end to end offline,
   its scorecard is posted to the job summary, and the job **fails on a BLOCK**
   (exit 2 - a tripped safety gate or detected model drift).

This is written down as an exception, not disguised as a review.

## Honest accounting: what this does and does not replace

- **What the CI gate genuinely covers.** Regressions, lint drift, a broken gate,
  a safety BLOCK, model drift, and any behaviour not backed by a passing test.
  An automated gate catches these better and more consistently than a human
  reviewer, and it runs on every push with no wait for a reviewer to be free.
- **What it does NOT replace.** A second pair of eyes on **design judgement,
  label quality, and blind spots**. CI cannot tell me my rubric is wrong,
  that my synthetic manifests are unrepresentative, or that a human verdict I
  wrote is miscalibrated - the very single-author weaknesses listed as
  limits in the [README](../../README.md) and [DATASET.md](../../DATASET.md). The
  substitution closes the *regression* half of peer review, not the *judgement*
  half.
- **Consistency with the datasets.** This is the same honesty the dataset docs
  apply to labels: single-author labels with no inter-annotator agreement are
  recorded as a limit, not claimed as consensus. This ADR records the same gap on
  the *process* side.

## Consequences

- **Positive.** The DoD stays truthful; every ticket still clears an objective,
  repeatable bar on every push; the exception is visible and auditable, not
  an unstated compromise.
- **Negative / residual risk.** Design-level and label-level errors that a human
  reviewer might have caught can still reach `main`. This risk is accepted,
  documented, and bounded by the stated limits.
- **How to close the exception later.** When a second contributor joins, restore
  the peer-review DoD item in full - require an approving review on each pull
  request, e.g. via branch protection - and retire this exception. The CI gate
  then becomes the floor beneath peer review, not a stand-in for it. Likewise,
  adding an independent annotator closes the matching label-quality limit.

## Alternatives considered

- **Drop the peer-review item silently.** Rejected - it would leave the DoD
  claiming a bar the project does not meet.
- **Self-review and mark it reviewed.** Rejected - self-review is not independent
  review, and recording it as such would be dishonest.
- **Require the CI gate *and* call it equivalent to peer review.** Rejected - CI
  does not cover the judgement half, so claiming equivalence would overstate it.
  The decision is a documented substitution for the regression half with the gap
  named, not an equivalence claim.
