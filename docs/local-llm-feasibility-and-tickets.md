# Local-LLM feasibility & architecture — research findings, options, board tickets

> Companion to `provider-portability-tickets.md`. That file establishes the
> provider seam (PP-00..PP-08); this one answers a narrower question with a
> deep-research pass behind it: **could a local open-weight LLM run `cage` — the
> classifier and/or the judge — and if so, how should it be architected?**
>
> Suggested home: a new milestone **M6 — Local / sovereign model support**,
> dependent on M5 (the provider seam) and on the PP-04 judge decision.

## The reframe that makes this tractable

`cage` supplies its own domain knowledge. `regmap.py` holds a ~400-line citable
control catalogue (EU AI Act Arts. 12/14/15, GDPR, UK GDPR, ISO 42001, NIST AI
RMF, PRA SS1/23, SR 11-7); the autonomy L0-L5 and blast-radius BR1-BR5
definitions are spelled out inline in the agent's system prompt; and the judge is
handed an explicit per-dimension *rubric-of-claims* plus a strict JSON verdict
schema. So a local model is **not** asked to recall regulation. It is asked to:

1. follow a supplied rubric, and
2. emit a schema-valid *forced* tool call (`submit_classification` /
   `record_verdict`) with typed fields, never free text.

That moves the question from "does an open model know the regulations" (it
doesn't, and doesn't need to) to "can it hold a constrained tool-call loop and
grade a bounded rubric well enough." The research answers both.

---

## What the research found (trusted sources)

### A. Can a local model hold the forced-tool-call loop? — **Yes, via constrained decoding**

The make-or-break mechanism is **vLLM's named-tool-call path** with structural-tag
/ xgrammar constrained decoding: with `tool_choice` set to a named function, free
text, hallucinated tool names, and wrong-type fields are blocked at the token
mask — the model *cannot* emit anything but a schema-valid call to that function.
This is exactly `cage`'s forced-call design.
([vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling.html),
[vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs.html))

- **Smallest viable classifier:** Qwen3-8B (non-thinking) + vLLM — best multi-turn
  score of any sub-14B open model; `cage`'s fixed narrow schema is simpler than
  the BFCL task set, so real error rate should beat the benchmark.
- **Comfortable production classifier:** Qwen3-32B; **Qwen2.5-72B** where VRAM
  allows. **Model family beats parameter count** — Llama-3.3-70B's multi-turn is
  *worse* than Qwen3-8B's, so a bigger Llama is not the answer.
  ([BFCL v4](https://benchmarklist.com/benchmarks/bfcl_v4/),
  [Qwen function calling](https://qwen.readthedocs.io/en/latest/framework/function_call.html))
- **Important limit:** constrained decoding enforces *syntactic* schema (field
  names, JSON types) but **not** semantic value ranges (the float-in-[0,1] score).
  `cage` already has the backstop — `verdict_from_tool_input()` range-validates and
  raises — so the architecture is to keep that check and allow one schema-repair
  retry fed back as a `tool_result`.

**Hard exclusions (encode as a runtime/model allow-list):**
- **Ollama cannot force a tool call** — it is a listed future feature; `format`
  shapes free-text output but can't guarantee the call path. Exclude Ollama from
  `cage`'s control path. ([Ollama tool support](https://ollama.com/blog/tool-support))
- **Mistral Tekken v11+ (Small 3.2, Ministral 3, Magistral)** can answer in plain
  text until `max_tokens` *even with* `tool_choice="required"` in vLLM — a
  documented exception that breaks the forced-call guarantee. Exclude.
- **llama.cpp** enforces the structural JSON wrapper via GBNF but not argument
  value-depth, and silently falls back to generic JSON on unsupported schema
  features — acceptable for dev/CI *with* an app-level validator, not for the
  primary path. ([llama.cpp grammars](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md))

### B. Can a local model be the *judge*? — **Conditional; this is the real gate**

The judge is the scientific heart and the kappa 0.8 bar is tight.
- A 70B-class open judge *can* clear 0.8 on an easy, well-specified rubric but can
  fall far below it on a harder one — **kappa deflation is universal** and
  per-dimension, so a judge must be validated *per `cage` dimension against the
  human gold set*, never in aggregate.
- **Self-preference is maximal when the judge is the agent-under-test**, and
  **same-family bias inflates agreement** even across different checkpoints of one
  base model. A panel of the same family *amplifies* bias rather than cancelling
  it. So `cage`'s cross-model design must become a **structural family-separation
  assertion** (judge base-model family != agent base-model family).
- **Inspect gives this for free:** it can route both roles to local/OpenAI-
  compatible providers (`vllm/<id>`, `ollama/<id>`, `openai-api/<provider>/<id>`),
  and the grader is set with `--model-role grader=...`; passing
  `ModelRole("grader", required=True)` **hard-blocks the fallback to the agent's
  own model** — the exact failure `cage` must prevent. Determinism guidance:
  `temperature=0` + fixed `seed`.
  ([Inspect models](https://inspect.aisi.org.uk/models.html),
  [Inspect model-graded](https://inspect.aisi.org.uk/model-graded.html))

> **Citation-integrity caveat:** the judge-reliability thread returned several
> malformed arXiv identifiers (impossible year-month stems). The *directional*
> findings — kappa deflation, self-preference, family inflation, panel
> amplification — are well established, but the specific kappa figures are
> **indicative and must be verified against primary sources** before any of them
> appear in a customer- or board-facing artefact. This is ticket **LL-09**.

### C. Serving, reproducibility, air-gap — **vLLM first; local = stronger weight anchor, weaker execution anchor**

- **vLLM** is the primary runtime (strongest constrained decoding, explicit
  reproducibility guidance, `seed=0` default, local-path loading). **TGI is in
  maintenance mode — HuggingFace itself now points to vLLM; do not adopt it.**
  llama.cpp is the simplest air-gap (single binary + GGUF, hash it externally);
  Ollama's per-model `digest` is a genuine plus for drift-gating despite its weak
  decoding. ([vLLM serving](https://docs.vllm.ai/en/latest/serving/online_serving/),
  [TGI index](https://huggingface.co/docs/text-generation-inference/en/index))
- **Reproducibility you can anchor:** weight SHA256, quantisation, runtime +
  version, structured-output backend, seed, temperature 0.
  **What stays non-deterministic:** batching/scheduling, GPU-kernel reductions, FP
  accumulation order, kernel compilation, and **hardware generation** (vLLM states
  reproducibility holds only on the *same hardware*). So a local model is a
  *stronger anchor on the weight axis* than any hosted snapshot, but needs
  explicit **hardware pinning** to match hosted execution-level determinism.
  ([vLLM reproducibility](https://docs.vllm.ai/en/latest/usage/reproducibility.html))
- **Local drift fingerprint** must extend today's hosted-snapshot ID to:
  `weight_sha256`, `quantisation`, `model_id`, `model_revision`, `runtime`,
  `runtime_version`, `runtime_container_digest`, `seed`, `temperature`,
  `structured_output_backend`, `cache_prompt` (llama.cpp), `hardware_gpu_model`,
  `cuda_driver_version`, `multiprocessing_disabled`.

---

## The three architecture options

| | **Option 1 — Hybrid** | **Option 2 — Inspect-routed** | **Option 3 — Fully sovereign** |
|---|---|---|---|
| Classifier | local vLLM (Qwen3-32B) | via Inspect `vllm/...` | local vLLM |
| Judge | **pinned frontier** (hosted) | via Inspect `--model-role grader=` | **local, calibrated** |
| Delivers | govern any vendor's agents + partial cost saving | breadth cheaply, reusing the existing inspect adapter | full air-gap / data residency |
| Air-gapped? | **No** — judge still calls out | No (unless grader is local) | **Yes** |
| Main risk | still a hosted dependency | cedes message-level call control; `inspect_tool`/`submit` loop may not survive Inspect's solver | judge calibration + execution reproducibility under GPU nondeterminism |
| Effort | Low-medium | Low (spike) | High |

**Recommendation (to be confirmed at LL-00):** build the **provider seam (M5)**
first, land **Option 1** as the defensible product step (local classifier is a
today-capability; the judge stays on a pinned frontier model), and treat **Option
3** as a separately-scoped *sovereign governance appliance* gated by the LL-04
judge-calibration result. Run the **Option 2 spike (LL-07)** early because it may
do most of Option 1's live wiring for free.

## Reference architecture (how to build it)

```
                 ┌─────────────────────────────────────────┐
  AgentManifest →│ cage.classify / agent loop              │
                 │   ProviderClient (PP-01 seam)           │
                 │     ├─ AnthropicClient (today)           │
                 │     └─ OpenAICompatClient ──────────────┼──► vLLM /v1 (OpenAI-compatible)
                 │        tool_choice = NAMED function      │     Qwen3-32B, named-call +
                 │        + xgrammar constrained decoding   │     xgrammar structural tags
                 │   verdict_from_tool_input() range-check  │
                 │     + 1 schema-repair retry (tool_result)│
                 └─────────────────────────────────────────┘
  Judge: same seam, SEPARATE model, family != classifier family (structural gate)
         frontier-pinned (Opt 1) | local-calibrated (Opt 3)
  Drift: fingerprint extended to the local schema; fail-on-change incl. hardware
  Air-gap (Opt 3): weight-hash manifest on ingest → pinned OCI digest → no egress
                   → drift gate re-verifies hashes at eval startup
```

---

## Board tickets — M6: Local / sovereign model support

Ticket ids `LL-##`. Dependencies into the `PP-##` provider-seam set.

### LL-00 — Decision: target sovereignty tier *(decision gate)*
**Depends:** PP-00 · **Output:** ADR + choice of Option 1 / 2 / 3
Pick the target: hybrid (local classifier, frontier judge), Inspect-routed, or
fully air-gapped. Everything below scopes to the choice. Human sign-off.

### LL-01 — Local vLLM classifier spike (named-call constrained decoding)
**Depends:** PP-01 · **Output:** a working `cage classify` run on local vLLM over a golden subset
Stand up vLLM with Qwen3-32B; drive `cage`'s classifier through the
`OpenAICompatClient` seam using a **named** `tool_choice` + xgrammar; wire `cage`'s
existing `verdict_from_tool_input` range-check plus one schema-repair retry as the
semantic backstop. Prove the `inspect_tool` -> `submit_classification` two-turn
loop survives. Pass/fail on a golden subset.

### LL-02 — Local classifier capability benchmark (empirical floor)
**Depends:** LL-01 · **Output:** per-model scorecard vs the full golden set
Run Qwen3-8B / Qwen3-32B / Qwen2.5-72B through the **whole** golden dataset under
`cage`'s own scorers. Report per-row correctness, schema-violation rate, and
multi-turn handling. Establishes the capability floor empirically rather than from
a BFCL proxy. Confirms (or refutes) "family beats size".

### LL-03 — Runtime & model allow-list (encode the exclusions)
**Depends:** LL-01 · **Output:** config allow-list + a startup assertion · **Effort:** low
Encode the hard exclusions as config + a pre-run check: Ollama out of the control
path (no forced call); Tekken-v11+ Mistral out (broken forced-call guarantee in
vLLM); llama.cpp allowed only with the app-level validator engaged. Fail fast on a
disallowed runtime/model pairing.

### LL-04 — Local judge calibration study *(decision gate, the hard one)*
**Depends:** PP-04, LL-02 · **Output:** per-dimension go/no-go + remapped thresholds
Measure a 70B-class open judge (candidate: Qwen2.5-72B) per-dimension against the
same human gold set that validated the Opus thresholds. Re-measure kappa per
dimension (gate each independently at 0.8), remap PASS/FIX/BLOCK score boundaries,
re-derive the abstain/noise-floor rule, pin temperature 0 + seed first, and add a
**structural family-separation assertion** (judge family != agent family). Output
a per-dimension verdict: which dimensions an open judge may serve and which must
stay on a frontier model.

### LL-05 — Local drift fingerprint & fail-on-change
**Depends:** PP-05 · **Output:** extended `drift.py` fingerprint for local models
Extend the drift fingerprint to the local schema (weight SHA256, quant, runtime +
version + container digest, structured-output backend, seed, temperature,
`cache_prompt`, `hardware_gpu_model`, `cuda_driver_version`,
`multiprocessing_disabled`). Define fail-on-change semantics, including a
hardware-generation change as a drift event, and document cross-hardware re-runs
as a known output-drift source even with identical weights.

### LL-06 — Mock / replay generalisation for a local provider
**Depends:** PP-06 · **Output:** normalised response type + a captured local transcript replay
Introduce `cage`'s own normalised response type so the offline suite stops
assuming the Anthropic SDK surface, and add record/replay of a captured local-vLLM
transcript so the local path is tested with no GPU in CI. Preserve the global DoD
(offline, no key, deterministic).

### LL-07 — Inspect route evaluation (build-vs-reuse) *(early spike)*
**Depends:** PP-07 · **Output:** recommendation feeding LL-01 vs reuse
Test whether Inspect's `vllm/` provider + `--model-role grader=... required=True`
can run `cage`'s governance task end-to-end with a local classifier and a
family-separated local/remote grader — and whether the bespoke
`inspect_tool`/`submit_classification` loop survives Inspect's solver/model
abstraction. Run this early; it may replace much of LL-01's wiring.

**Spike finding (DoD): hybrid (desk feasibility, not executed).** On the
documentation the route holds: Inspect's built-in `vllm/` provider runs a local
classifier ([providers.html](https://inspect.aisi.org.uk/providers.html)) and
`--model-role grader=... required=True` enforces a family-separated grader that
raises rather than falling back to the classifier model
([model-graded.html](https://inspect.aisi.org.uk/model-graded.html)); the
`inspect_tool` → `submit_classification` loop survives inside a custom `@agent`
solver ([agent-custom.html](https://inspect.aisi.org.uk/agent-custom.html)). The
`vllm/` provider effectively **is** the `OpenAICompatClient` seam LL-01 proposed to
build, so that seam is likely not built but reused.

The weak joint is the terminal forcing: named `tool_choice` forcing is **not**
documented as reachable through Inspect's vLLM path and may be silently dropped, so
cage's forced `submit_classification` and the judge's forced verdict must be
rebuilt as constrained decoding – `guided_grammar`/`guided_regex` or
`response_schema` via `extra_body`
([structured.html](https://inspect.aisi.org.uk/structured.html)) – not as a forced
named tool. **Hardware caveat:** no GPU/vLLM was available, so none of this was
run; "the docs say supported" is not "verified by running it".

Forward pointer: **LL-01** must run the classifier through the `vllm/` provider and
confirm (a) whether named `tool_choice` reaches vLLM or is dropped and (b) whether
guided decoding reproduces the forced-verdict control; **LL-02** must confirm the
local model is capable of the two-turn loop and constrained decoding at the
required quality. Adopt the Inspect route as the working hypothesis; fall back to a
bespoke local adapter only if LL-01 shows the forced-verdict control cannot be
reconstructed. Full finding: `docs/inspect-route-build-vs-reuse.md`.

### LL-08 — Air-gapped reference deployment & provenance
**Depends:** LL-05 · **Output:** documented sovereign topology + an ingest/verify script
Document and script the sovereign topology: `snapshot_download(revision=<sha>)` on
a staging host, a signed weight-hash manifest, transfer into the enclave,
SHA256 re-verify on arrival, a runtime container pinned by **OCI digest** with
vLLM kernels pre-compiled, no network egress (`TRANSFORMERS_OFFLINE=1` etc.), and
the `cage` drift gate re-verifying all hashes at eval startup.

### LL-09 — Citation-integrity pass on the research figures
**Depends:** none · **Output:** a verified claims table · **Effort:** low
Verify the specific numbers behind this work against primary sources before any
appear in a customer- or board-facing artefact — in particular the judge-kappa
figures (the research thread returned malformed arXiv IDs) and the BFCL bands.
Keep the directional findings; tier each figure verified / indicative / drop.
(Consistent with the regulatory-claims-register discipline.)

## Dependency order

```
PP-00 ─ PP-01 ─ PP-04/05/06/07  (provider seam + decisions, M5)
                     │
LL-00 (tier) ────────┤
   ├─ LL-07 (Inspect spike — run early)
   ├─ LL-01 ─ LL-02 ─ LL-04 (judge calibration, gate)
   │            └─ LL-03 (allow-list)
   ├─ LL-05 ─ LL-08 (air-gap)
   ├─ LL-06 (mock)
   └─ LL-09 (citations — anytime)
```

## Candidate model shortlist (pinned 2026-11-10 — figures indicative, verify in-spike per LL-09)

**Classifier (agent-under-test)** — runtime vLLM, **named** `tool_choice` + xgrammar:

| Tier | Model | Note |
|---|---|---|
| CI / floor | **Qwen3-8B** (non-thinking) | ~5.6 GB Q4; best multi-turn-per-param of any sub-14B open model |
| Primary | **Qwen3-32B** (non-thinking) | recommended production classifier; dense-rubric following beats 8B |
| Headroom | **Qwen2.5-72B-Instruct** | mature `hermes` tool parser, 128K context |
| Wildcard | **gpt-oss-20B** (OpenAI open-weight — confirm existence / licence / vLLM parser) | the local model closest to the Codex / OpenAI lineage |

**Judge (LL-04)** — must be a **different base-model family** from the classifier:
- **Option 1 (hybrid):** pinned frontier judge (hosted) — no family conflict by construction.
- **Option 3 (fully local):** the local judge family is **left open until LL-02** lands the classifier side. The binding rule is judge family != classifier family — so if the classifier lands on Qwen, the judge cannot be Qwen (incl. Qwen2.5), and vice-versa. Do **not** pre-commit a judge family; pick the local-judge candidate once LL-02 is in.

**Excluded from the control path (LL-03):** Ollama (cannot force a tool call), Mistral Tekken v11+ (broken forced-call in vLLM). **TGI not adopted** (maintenance mode).

**Verified vs indicative:** the exclusions and the vLLM named-call + xgrammar mechanism are verified from primary docs; all benchmark decimals, the kappa figures, and gpt-oss availability are **indicative** and must be verified in-spike (LL-09).
