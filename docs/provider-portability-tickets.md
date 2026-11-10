# Provider portability — investigation & scoping tickets

> **Status: proposed (investigate + scope only).** None of these tickets build a
> second provider. They establish *whether* `cage` should run on a non-Anthropic
> model — OpenAI / Codex or a local LLM — *which part* of `cage` that means, and
> *what it would cost*. The build tickets come after PP-00 and PP-04 decide.
>
> Suggested home: a new milestone **M5 — Provider portability**, sequenced after
> M2 (live-mode validation) — you cannot validate a second provider until the
> live path against the first one is real and measured.

## Why this is two questions, not one

There are two separable value propositions, and conflating them is the main risk:

1. **Agent-under-test portability** — classify and govern agents that are *built
   on* another provider (an OpenAI/Codex agent, a local-model agent). The
   governance proposition is provider-agnostic by intent; an assessment engine
   that can only inspect Claude-shaped agents is narrower than the pitch. The
   manifest `cage` classifies is already provider-neutral JSON, so this is
   mostly about the *agent runtime* in `agent.py`.
2. **`cage`-runtime portability** — run `cage` *itself* (its agent loop and/or
   its judge) without Anthropic. Drivers: sovereignty / air-gap (a local or
   UK-resident model), cost, and removing a single-vendor dependency from the
   governance tool. This is the harder, more scientific change because it moves
   the **judge**, which is calibrated.

Most likely landing zone (to be confirmed by PP-04): **make the agent-under-test
portable, keep the judge on a pinned frontier model**. Decouple the two first.

## Coupling surface (what PP-01 audits — recorded here for context)

| File | Anthropic assumption |
|---|---|
| `agent.py` | `tool_use`/`tool_result` message loop, `stop_reason` semantics, `submit_classification` / `inspect_tool` forced tool call, lazy `anthropic` SDK import |
| `evals/judge.py` | forced `record_verdict` tool call, typed-verdict-via-tool-schema contract |
| `mock.py` | `MockAnthropicClient` mimics the `anthropic.Anthropic` surface (`.content`, `.stop_reason`, `.model`, `.usage`, TextBlock/ToolUseBlock) — the whole offline suite is shaped against it |
| `models.py` | pinned Anthropic IDs, `anthropic-version` header, first-party USD prices, "dateless alias = frozen" reproducibility argument |
| `evals/drift.py` | fingerprints resolved model + API version, fails on change |
| `cli.py` | `--mock` dual-role client |
| `gateway_stub.py` | **already neutral** (stdlib, no model calls) |
| `collectors/*` | **already neutral** (schema adapter, hash-drift); `annex_iii` inherits the agent runtime via `classify_manifest` |
| `inspect_adapter.py` | **portability lever** — Inspect's model layer already speaks many providers |

---

## Tickets

### PP-00 — Decision spike: scope & product thesis *(decision gate)*
**Type:** decision · **Depends on:** nothing · **Blocks:** all build work · **Output:** ADR-0005 + recommendation
Decide, before any adapter work: (a) is multi-provider in scope for the product,
or is `cage` deliberately a Claude-anchored engine? (b) which axis — agent-under-test,
`cage`-runtime (judge), or both? (c) is the primary driver reach (govern any
vendor's agents), sovereignty/air-gap, or cost? Produce a one-page recommendation
and ADR-0005. Keep `cage` product-neutral (no consumer/client names). **This is a
human sign-off, not a default-and-proceed.**

### PP-01 — Map the Anthropic coupling surface & design a `ProviderClient` interface
**Type:** investigation · **Depends on:** PP-00 · **Output:** coupling inventory + proposed interface (structured)
Turn the table above into a precise inventory: every call site, every response
field consumed, every place `stop_reason` / usage / tool-call shape is assumed.
Propose a normalised `ProviderClient` protocol (request, response, tool-call,
usage as `cage`-owned types) that `agent.py` and `judge.py` call instead of an
SDK-shaped object, with the Anthropic client as the first implementation behind
it. No behaviour change — this is the seam. Estimate blast radius on the ~440 tests.

### PP-02 — Scope an OpenAI / Codex adapter
**Type:** investigation · **Depends on:** PP-01 · **Output:** feasibility + effort + behavioural-diff list
Scope translating the tool-use loop to OpenAI function-calling (Chat Completions
`tools` / the Responses API). Resolve: forced-tool-call equivalent (`tool_choice`
= required/named), parallel tool calls, `finish_reason` ↔ `stop_reason` mapping,
usage shape, structured-output/JSON-schema mode vs Anthropic tool schema, and the
`anthropic-version` → snapshot-pin substitution. **Clarify "Codex" first** — the
OpenAI API generally, or the Codex CLI/agent product specifically (the latter is
an agent *product*, not a raw model endpoint, and may not expose the message-level
control the harness needs). Deliver the list of behavioural differences that could
*move eval results*, not just "it compiles".

### PP-03 — Scope a local-LLM adapter
**Type:** investigation · **Depends on:** PP-01 · **Output:** runtime shortlist + capability-floor test plan
Scope an OpenAI-compatible local path (Ollama / vLLM / llama.cpp / LM Studio / TGI).
Two findings to produce: (1) **capability floor** — small/quantised local models
may not reliably sustain the forced-tool-call / typed-verdict loop the entire
harness depends on; design a capability probe and a graceful-degradation rule, and
identify which golden-dataset rows a 7B/8B class model can and cannot classify.
(2) **reproducibility upside** — a local model gives a real weight + quantisation
hash, a *stronger* drift anchor than any hosted snapshot (feeds PP-05). Note the
sovereignty / air-gap value explicitly.

### PP-04 — Judge portability & re-calibration *(decision gate)*
**Type:** investigation + decision · **Depends on:** PP-01 · **Output:** per-judge calibration cost + go/no-go rubric
The judge is the scientific heart; `JUDGE_MODEL` is pinned to Opus and the whole
calibration (kappa, FP/FN, noise floor, thresholds in `calibration.py`/`safety.py`)
is measured against it. Moving the judge to GPT or a local model **invalidates
that calibration** and introduces cross-family grading bias (a model's self-
preference when judging another family's agent). Scope: the re-calibration each
candidate judge needs, whether cross-provider judging is defensible at all, and
whether the judge should stay on a pinned frontier model *even when the agent-
under-test is another provider* (the likely answer — decouple `AGENT_MODEL` from
`JUDGE_MODEL`). This ticket probably sets the product boundary.

### PP-05 — Reproducibility & drift across providers
**Type:** investigation · **Depends on:** PP-01, PP-03 · **Output:** provider-tagged ModelSpec + drift design
`models.py`/`drift.py` lean on "dateless Anthropic alias = frozen weights".
OpenAI uses dated snapshots (pinnable, but deprecated on a schedule); local models
= file hash (strongest). Design a per-provider pin + fingerprint strategy and
redefine "fail on change" when a provider *retires* a pinned snapshot (a different
failure mode from silent drift). Output a revised, provider-tagged `ModelSpec` and
`drift` fingerprint.

### PP-06 — Mock / replay harness generalisation
**Type:** investigation · **Depends on:** PP-01 · **Output:** refactor plan + test-impact estimate
`MockAnthropicClient` is SDK-shaped, and the offline suite is built against it. To
test adapters with no API key, either add per-provider mocks (duplication) or —
preferred — have adapters emit `cage`'s normalised response type (from PP-01) and
make one mock speak *that*. Scope the refactor and estimate its impact on the
existing tests; the global DoD (offline, no key, deterministic) must survive.

### PP-07 — Inspect AI as the portability shortcut *(build-vs-reuse)*
**Type:** investigation · **Depends on:** PP-01 · **Output:** build-vs-reuse recommendation feeding PP-02/03
`inspect_adapter.py` already exists and Inspect's model layer speaks OpenAI /
Google / Mistral / Ollama / vLLM / local. Investigate routing *live* multi-provider
runs through Inspect's model API instead of hand-building adapters — potentially
the cheapest path to breadth. Trade-off: it cedes the message-level call control
`cage` currently holds, and the `inspect_tool` / `submit_classification` agent loop
may not survive Inspect's own solver/model abstraction. Recommend build (PP-02/03)
vs reuse (Inspect) per axis.

### PP-08 — Collectors subpackage provider-neutrality confirmation
**Type:** investigation · **Depends on:** PP-01 · **Output:** short confirmation + any coupling found · **Effort:** low
Confirm `collectors/{findings,assessment,annex_iii}` carry no hidden model
dependency — `findings` is a schema adapter, `assessment` is hash/anchoring drift,
and `annex_iii` wraps `classify_manifest` (so it inherits whatever the agent
runtime is, and portability there is entirely PP-01/PP-02/PP-03's result). This
closes the loop for the collector platform — the consumer that prompted the
question — with a one-paragraph finding rather than new work.

---

## Dependency order

```
PP-00 (decision: scope)
  └─ PP-01 (coupling map + ProviderClient seam)   ← keystone
       ├─ PP-02 (OpenAI/Codex adapter — scope)
       ├─ PP-03 (local-LLM adapter — scope)
       ├─ PP-04 (judge portability + re-calibration) ← decision gate
       ├─ PP-05 (reproducibility/drift across providers)   [also needs PP-03]
       ├─ PP-06 (mock generalisation)
       ├─ PP-07 (Inspect shortcut — build vs reuse)
       └─ PP-08 (collectors neutrality — confirm)
```

## Recommended reading of the likely outcome (to be tested, not assumed)
- **Decouple `AGENT_MODEL` from `JUDGE_MODEL`.** Agent-under-test portability is
  the defensible product move; judge portability is a research question with a
  calibration bill attached.
- **Local LLM = sovereignty / air-gap story**, gated by a hard capability floor —
  scope it as a *supported-with-caveats* tier, not parity.
- **Inspect may do most of the live breadth for free** — resolve PP-07 before
  committing to hand-built adapters in PP-02/PP-03.
