# Feature Planning Board - `cage`

Tracks delivery of the `claude-agent-governance-evals` harness. Ticket scope and
Definition of Done (DoD) are authoritative in [SPEC.md](SPEC.md) section 11; this
board tracks **state**. Move a ticket's row between the columns as it progresses.

Legend: `[ ]` Backlog - `[~]` In Progress - `[x]` Done
Global DoD (every ticket): code is stdlib-only at runtime; ASCII-only code/output;
no `TODO`/stub left behind; the module's tests pass under `python -m pytest`; the
behaviour it adds is covered by a new or updated test.

---

## Phase 0 - Architecture (DONE)

- [x] **P0-00** Scaffold + package skeleton + `SPEC.md` + board.
  DoD: all modules import cleanly; 81 skeleton tests pass; SPEC is the SoT.

## Phase 1 - Core harness

| State | Ticket | Module(s) | Deliverable |
|---|---|---|---|
| [ ] | P1-01 | `manifest.py` | Harden schema validation + fixtures |
| [x] | P1-02 | `classify.py` | `classify_manifest()` two-axis inference + evidence |
| [x] | P1-03 | `regmap.py` | Complete + review the control catalogue |
| [x] | P1-04 | `mock.py` | Record/replay transcripts + failure-localisation tests |
| [x] | P1-05 | `agent.py` | Messages API tool_use/tool_result loop |
| [x] | P1-06 | `evals/judge.py` | Isolated per-dimension LLM judge call |
| [x] | P1-07 | `evals/scorer.py` | Six per-dimension scorers + composite |
| [x] | P1-08 | `evals/calibration.py` | Kappa / FP-FN / flip-rate / noise floor |
| [x] | P1-09 | `evals/safety.py` | Dangerous-miss gate + HITL over-escalation penalty |
| [x] | P1-10 | `evals/drift.py` | Pin + fingerprint + fail-on-change |
| [x] | P1-11 | `evals/task.py`, `evals/runner.py` | Task assembly + run orchestration |
| [x] | P1-12 | `cli.py` | `classify` + `eval` command bodies + cost guard |

## Phase 2 - Data + MCP surface

| State | Ticket | Module(s) | Deliverable |
|---|---|---|---|
| [ ] | P2-01 | `data/golden.json` | Author the golden dataset |
| [ ] | P2-02 | `data/calibration.json` | Author calibration pairs + run calibration |
| [ ] | P2-03 | `server.py` | MCP server exposing the classifier |

---

## Dependency order

```
P0-00
  |-- P1-01 --+
  |-- P1-03   |
  |           v
  +-- P1-02 (needs manifest) --> P1-05 (agent) --+
  |-- P1-04 (mock)  ------------------------------+--> P1-11 (task+runner) --> P1-12 (cli)
  |-- P1-06 (judge) --> P1-07 (scorer) ----------+
  |-- P1-08 (calibration) -----------------------+
  |-- P1-09 (safety) ----------------------------+
  |-- P1-10 (drift) -----------------------------+
P2-01, P2-02 depend on P1-02/P1-08; P2-03 depends on P1-05.
```

Each closed ticket flips its `[ ]` to `[x]` here and leaves the deferred
`NotImplementedError(<ticket id>)` replaced by a tested implementation.

---

## Progress log

- **2026-10-05** - Eval harness landed end to end (P1-06, P1-07, P1-09, P1-10,
  P1-11). `judge.py` runs one isolated rubric-of-claims judge per dimension,
  forcing a typed `record_verdict` tool call with an explicit Unknown way-out, as
  a separate model call from the agent under test. `scorer.py` wires all six
  dimensions (deterministic + judge-reinforced), `safety.py` adds the binary
  dangerous-miss gate (category fail-rate budgets, kappa>=0.8 bar) and the HITL
  escalation score with the over-escalation penalty, `drift.py` pins the model
  fingerprint + a behavioural fingerprint and fails on a version bump or drift
  beyond tolerance (PRA SS1/23 2.4, SR 11-7 VII), and `runner.py` orchestrates the
  run: pin -> drift fail-fast -> solve+score -> noise-floor abstain + bar
  uncalibrated judges -> safety gates -> aggregate to PASS/FIX/BLOCK with a
  per-dimension, regulator-grounded scorecard. Full suite runs offline (no API
  key) via the dual-role mock. `python -m pytest`: 211 passed.
- **2026-10-05** - CLI landed (P1-12). `cage classify <manifest.json> [--mock]`
  prints the two-axis classification, per-tool evidence, and the cumulative
  regulatory control map for the resolved intensity; `cage eval --mock
  [--threshold T] [--max-cost-usd C] [--repeat k]` runs the harness offline and
  prints a per-dimension, regulator-grounded ASCII scorecard, propagating the
  gate as the process exit code (0 PASS / 1 FIX / 2 BLOCK). The `--mock` path is
  served by a new shippable dual-role offline client,
  `cage.mock.governance_mock_client` (agent + judge, stdlib-only, no API key).
  Added `.github/workflows/ci.yml`: on push + pull_request it sets up Python
  3.11, installs the package editable with dev extras, runs `ruff check .` and
  `python -m pytest -q`, then runs `cage eval --mock`, posts the scorecard to the
  job summary, and fails the job on BLOCK (exit 2); a commented-out live-mode job
  shows where `ANTHROPIC_API_KEY` would be wired as a secret. An explicit
  `[tool.ruff.lint]` rule set (E/F/I/UP/B/RUF) pins lint reproducibly.
  `python -m pytest`: 219 passed. `cage eval --mock`: PASS (exit 0).
- Remaining Phase 1: P1-01 (`manifest.py` hardening).
