# Inspect AI as the portability route for cage — build vs reuse

**Spikes:** PP-07 (GitHub #55, milestone M5) and LL-07 (GitHub #64, milestone M6)
**Date:** 2026-11-10

> This is a desk feasibility finding grounded in two evidence inputs: a repo analysis of cage's Anthropic coupling surface, and a docs-research pass over the Inspect AI framework. **No GPU or vLLM hardware was available in this environment, so nothing below was run.** Every "supported" claim is a docs claim, not a verified execution. The empirical confirmation is explicitly handed to LL-01 and LL-02.

## Executive verdict

- **PP-07 — hybrid.** Reuse Inspect's model layer for live provider breadth across both axes – it already carries the ~29-provider adapter set cage would otherwise hand-build in PP-02/PP-03 – but keep cage's two-turn loop and its scoring as a custom Inspect solver, and re-express the judge as an Inspect model-graded scorer with a required grader role. Agent axis leans hybrid; judge axis is a near-clean reuse.
- **LL-07 — hybrid.** The route is viable on the documentation: Inspect's built-in `vllm/` provider plus `--model-role grader=... required=True` can, in principle, drive the governance task with a local classifier and a family-separated grader, and the `inspect_tool` → `submit_classification` loop survives inside a custom solver. But the load-bearing control – forcing the terminal `submit_classification` call by name, and forcing the judge's typed verdict – is **not** documented as reachable through Inspect's vLLM path, so it must be rebuilt as guided/constrained decoding, and the whole route is unverified until LL-01 runs it on real hardware.

## Update — offline PoC refines the "weak joint" (2026-11-10)

A follow-on offline proof-of-concept (branch `feat/inspect-live-solver-poc`) wired
cage's live two-turn loop as a custom Inspect `@solver` and the judge as a
model-graded `@scorer`, and ran a real `inspect eval` against an offline mock model
(455 passed / 1 skipped; the optional-dependency invariant held with `inspect_ai`
absent). It sharpens one claim in the LL-07 finding below:

- **Named tool forcing is not broadly unreachable.** Inspect's
  `Model.generate(tool_choice=ToolFunction(name=...))` *does* expose named forcing
  at the API surface, and the judge path uses it directly (with
  `verdict_from_tool_input` as the backstop). The genuine open question is
  narrower: whether a specific backend — notably the `vllm/` provider — *forwards*
  that named choice to vLLM's endpoint. That is untested without hardware and is
  **LL-01's** job. So where this document says named `tool_choice` forcing is "not
  reachable through Inspect's vLLM path", read: *reachable in the Inspect API;
  unproven only in vLLM backend forwarding*.
- **`get_model(role="grader", required=True)` behaves exactly as documented** — it
  raises `PrerequisiteError` rather than silently falling back to the model under
  test, so judge/classifier family separation holds by construction.
- The agent loop's terminal exit via **loop-termination on the
  `submit_classification` tool name cleanly substitutes** for SDK-forced choice —
  the authoritative intensity matrix, not the model's self-report, carries the real
  control.

The rest of the finding stands; the hardware-dependent questions remain open for
LL-01/LL-02.

## What cage holds today (the coupling surface)

cage's portable core and its coupled live paths are cleanly separable:

- **Provider-neutral already:** `classify.py` is pure Python with no SDK – a deterministic matrix – and is exactly what the existing Inspect adapter runs, which is why that path ports for free. The manifest-to-prompt text, the manifest lookup, the trajectory record shape, intensity re-derivation from the matrix (`intensity_for` – the matrix is authoritative; the model's self-reported intensity is discarded), and the `submit_classification` JSON-to-`Classification` mapping all operate on dicts and strings and would survive a provider swap.
- **The live agent loop is tightly Anthropic-coupled** (`agent.py:150-222`). The control cage holds at the message level: each turn calls `client.messages.create(model, max_tokens, system, tools, messages)`, selects `tool_use` blocks by `getattr(b,'type')=='tool_use'`, reads `.name`/`.input`/`.id` off block objects, echoes the assistant turn back verbatim as `{'role':'assistant','content':response.content}`, and feeds tool output back as an Anthropic `tool_result` content block. Tool definitions use the Anthropic `input_schema` key and the Anthropic-only `strict:True` flag. Every message-shaping line needs a seam; the governance logic around it does not.
- **The judge is the hardest axis to port** (`judge.py:306-341`). `DimensionJudge.run` forces a single typed tool call with `tool_choice={'type':'tool','name':record_verdict}` – the Anthropic forced-tool form – and the verdict comes **only** from that typed call; there is no free-text fallback, so the forced-tool-choice shape is load-bearing. The judge reuses `cage.agent._load_anthropic`, so it is bound to the same SDK.
- **Model identity and drift** (`models.py`) pin `claude-*` ids and name the fingerprint field `anthropic_version`; provider-neutral in mechanism (SHA-256 over JSON) but Anthropic in every value and field name. This is PP-05's work, not on the hot wire path.
- **No seam exists.** There is no `ProviderClient`, `OpenAICompatClient`, or any normalisation layer anywhere in `src/cage/`; both names appear only in planning docs. The one existing seam is the duck-typed `client: Any` injection on `ClassifierAgent` and `DimensionJudge` – and that duck type **is** the Anthropic Messages wire shape itself, which `MockAnthropicClient` emulates. Building the live multi-provider path means writing that seam, or delegating to Inspect's model layer, which already has it. That is the build-vs-reuse choice.

A scope note from the evidence: `cage compare` already drives the live `agent.py` loop against a single reused `anthropic.Anthropic()` client, so it is the real live multi-model but single-provider path today; the existing Inspect adapter does not – it runs the deterministic `classify_manifest`. The `anthropic_version` pin is fed into the drift fingerprint but is never actually sent as a header on the live calls, so it is a reproducibility pin, not a wire dependency.

## What Inspect offers (capabilities)

- **~29 providers behind one `get_model()` / `Model.generate()` abstraction**, including `openai`, `anthropic`, `bedrock`, `azureai`, `vllm`, `ollama`, `sglang`, `together`, `fireworks` and the `openai-api/<provider>` passthrough ([providers.html](https://inspect.aisi.org.uk/providers.html), [models.html](https://inspect.aisi.org.uk/models.html)). This is multi-provider portability at zero per-provider adapter cost.
- **Custom tool-calling loops survive.** A custom `@agent` used as a solver via `as_solver()` can call `get_model().generate(input=state.messages, tools=[...])`, append the assistant message, run `execute_tools()`, extend `state.messages`, and loop until no tool calls remain; `generate_loop()` is the convenience shortcut ([agent-custom.html](https://inspect.aisi.org.uk/agent-custom.html), [solvers.html](https://inspect.aisi.org.uk/solvers.html)). cage's `inspect_tool` and `submit_classification` become `@tool`-decorated async functions; the terminal exit is detected by inspecting the tool-call name and setting `state.completed = True`.
- **The judge maps to a model-graded scorer with enforced family separation.** `get_model(role="grader", required=True)` retrieves a named grader model and, with `required=True`, raises rather than silently falling back to the model under evaluation if no grader is bound ([model-graded.html](https://inspect.aisi.org.uk/model-graded.html), [models.html](https://inspect.aisi.org.uk/models.html)). Roles are set via `Task(model_roles=...)` or `--model-role grader=...`. This is a direct, clean fit for cage's family-separated judge.
- **The `vllm/` provider is built in** ([providers.html](https://inspect.aisi.org.uk/providers.html)). `inspect eval task.py --model vllm/<model>` spawns the server automatically unless `VLLM_BASE_URL` points at an existing one; `-M` args forward to the vLLM CLI. Tool use is supported but model-dependent and needs extra configuration.
- **Constrained decoding is reachable on the vLLM path** ([structured.html](https://inspect.aisi.org.uk/structured.html)): JSON schema via `GenerateConfig(response_schema=ResponseSchema(json_schema=...))`, and `guided_grammar` / `guided_regex` / `guided_choice` via `extra_body`.
- **What you cede to the abstraction:** raw SDK-level named tool forcing (Inspect's `TaskState.tool_choice` exists but is not documented to map to a provider's native "force this exact tool" parameter), per-request HTTP controls, sub-event streaming, and raw provider message dicts (messages must conform to Inspect's internal `ChatMessage*` / `ToolCall` types). **What you gain:** the full provider list, structured JSONL eval logs with transcripts, built-in retry/concurrency, and the scorer/grader architecture ([extensions-model-api.html](https://inspect.aisi.org.uk/extensions-model-api.html)).

## PP-07 — per-axis build vs reuse

| Axis | Build (hand adapters: PP-02 OpenAI/Codex + PP-03 local) | Reuse (Inspect model layer) | Verdict |
|---|---|---|---|
| **Agent-under-test provider breadth** | Write the absent `ProviderClient` / `OpenAICompatClient` seam and a per-provider translation of the request tool schema (`input_schema`/`strict` → `functions`/`parameters`) and the response block model (`tool_use` → `tool_calls`, `role:'tool'` results, `finish_reason`, `json.loads` on arguments). Full control over message shape and SDK-level forcing; but every provider is net-new code cage must own and keep current. | Inspect carries the provider adapters already. Keep cage's two-turn echo loop + submit logic as a custom `@agent` solver that calls `get_model().generate(...)` and `execute_tools()`. The terminal `submit_classification` forcing is approximated by loop-termination on the tool-call name (react-style, [react-agent.html](https://inspect.aisi.org.uk/react-agent.html)), not SDK forcing. Acceptable because cage already discards the model's self-reported intensity and re-derives from the authoritative matrix, and submit is only a terminal exit. | **Hybrid** — reuse Inspect for breadth; keep the loop and scoring. |
| **LLM-judge provider breadth** | Hand-build the forced-verdict path per provider (Anthropic `tool_choice={'type':'tool',...}` vs OpenAI `{'type':'function',...}`). Preserves the exact forced-tool mechanism but duplicates Inspect's grader architecture. | Inspect's model-graded scorer + `get_model(role="grader", required=True)` gives the family separation cage wants, directly and enforced. The forced-tool verdict is re-expressed as structured output – `response_schema` (cloud/API graders) or `guided_grammar` (local) – in place of a forced named tool call ([model-graded.html](https://inspect.aisi.org.uk/model-graded.html), [structured.html](https://inspect.aisi.org.uk/structured.html)). | **Reuse** — strongest fit; the grader role is close to purpose-built for this. |

**Reading for PP-02 / PP-03:** do **not** hand-build the per-provider wire adapters – that duplicates Inspect's ~29-provider set, including the local backends PP-03 targets. Re-home PP-02/PP-03 effort into (a) cage's loop expressed as an Inspect solver and (b) the judge expressed as an Inspect model-graded scorer, keeping cage's matrix/scoring and the `verdict_from_tool_input` range-check as the backstop the structured-output path feeds into. The one thing cage genuinely cedes on the agent axis – SDK-level named forcing of the terminal submit – is low-cost here because the matrix, not the model's self-report, is authoritative.

## LL-07 — local-model route feasibility finding

**Question:** can Inspect's `vllm/` provider plus `--model-role grader=... required=True` run cage's governance task end-to-end with a local classifier and a family-separated grader, and does the bespoke `inspect_tool` / `submit_classification` loop survive Inspect's solver/model abstraction?

**On the documentation, the route holds:**

1. **Local classifier** — `--model vllm/<model>` is a built-in provider; Inspect spawns the server or attaches to `VLLM_BASE_URL` ([providers.html](https://inspect.aisi.org.uk/providers.html)). This replaces the bespoke `OpenAICompatClient` seam LL-01 proposed to build: Inspect's `vllm/` provider **is** that seam.
2. **Family-separated grader** — `get_model(role="grader", required=True)` enforces that a distinct grader is bound and raises rather than silently reusing the classifier model ([model-graded.html](https://inspect.aisi.org.uk/model-graded.html)). This is the exact family-separation guarantee cage's judge isolation needs.
3. **Loop survival** — `inspect_tool` and `submit_classification` become `@tool` async functions; a custom `@agent` solver loops `generate()` → `execute_tools()` → extend `state.messages`, setting `state.completed = True` when `submit_classification` is seen ([agent-custom.html](https://inspect.aisi.org.uk/agent-custom.html)). The two-tool structure, the turn sequencing, and the judge isolation survive without a rewrite.

**What must be rebuilt, not reused:**

- **The terminal forcing is the weak joint.** Named `tool_choice` forcing through the `vllm/` provider is **not** documented as reachable – Inspect does not confirm it passes `tool_choice={'type':'function','function':{'name':...}}` to vLLM's endpoint, and it may be silently dropped. The documented backstop is constrained decoding: `guided_grammar` / `guided_regex` via `extra_body`, or `response_schema` JSON schema ([structured.html](https://inspect.aisi.org.uk/structured.html)). So cage's forced `submit_classification` and the judge's forced verdict must be re-expressed as guided/constrained output, not as a forced named tool. Direct vLLM API calls outside Inspect remain the only confirmed path for named-tool forcing on vLLM – which is the control Inspect asks cage to give up here.
- **Per-turn message injection** (system-prompt overrides, metadata) moves from raw SDK kwargs to `state.messages` manipulation before each `generate()`.

**Hardware caveat (binding).** No GPU or vLLM hardware was available, so **this route was not executed**. The above distinguishes "the docs say this is supported" from "verified by running it" – none of it is in the second category. In particular, whether named tool forcing survives the `vllm/` provider, and whether a tool-capable local model plus guided decoding reproduces cage's forced-verdict control, are unconfirmed.

**Hand-off:**
- **LL-01 (vLLM classifier spike)** must run the classifier through Inspect's `vllm/` provider against a real tool-capable model and confirm whether (a) named `tool_choice` reaches vLLM or is dropped, and (b) `guided_grammar`/`guided_regex` reliably reproduces the terminal `submit_classification` and the judge verdict. LL-01's "build the `OpenAICompatClient` seam" item should be reframed: if this route holds, that seam is **not** built – Inspect's provider replaces it.
- **LL-02 (capability benchmark)** must confirm the chosen local model is actually capable of the two-turn tool loop and constrained decoding at the quality cage's governance task needs.

**Reading for LL-01 / LL-02:** adopt the Inspect `vllm/` + grader-role route as the working hypothesis and spend LL-01 proving the forcing/guided-decoding equivalence rather than building a bespoke seam; only fall back to a hand-built local adapter if LL-01 shows the forced-verdict control cannot be reconstructed through Inspect's guided-decoding layer.

## Open questions

1. **Named `tool_choice` through `vllm/`** — does Inspect forward it to vLLM's `/v1/chat/completions`, or silently drop it? Empirical run needed (LL-01).
2. **`guided_grammar` / `response_schema` as a forced-verdict substitute** — does constrained decoding reproduce the judge's "verdict only from the typed call, no free-text" guarantee at required quality? (LL-01/LL-02).
3. **`execute_tools()` and `generate_loop()` API stability** — import path, signature, public-vs-internal status, and tool_choice override are not pinned in the fetched docs; verify against the installed package.
4. **`ModelRole("grader", required=True)` exact constructor** — confirm the import and signature against the reference index.
5. **`TaskState.tool_choice` value set** — the `ToolChoice` type (auto / none / named / `ToolFunction`) and its per-provider mapping are undocumented in the pages seen.
6. **Inspect version pin** — the `@agent` + `execute_tools()` shape may differ across Inspect major versions; pin a version before building against it.
7. **PP-05 dependency** — provider-tagged `ModelSpec` and a provider-neutral version field are still needed regardless of route, so the drift fingerprint and manifest stop assuming `anthropic_version`.

## Decision

Both tickets resolve **hybrid**, and the two reinforce each other: reuse Inspect's provider and runtime layer (its ~29 providers for PP-07, its `vllm/` provider and enforced grader role for LL-07) and stop hand-building per-provider wire adapters; keep cage's governance control – the two-turn loop, the authoritative intensity matrix, the scoring and the verdict range-check – as an Inspect solver and model-graded scorer. The single contested control, SDK-level forcing of the terminal and verdict tool calls, is cheap to cede on the agent axis (the matrix is authoritative) but is the open risk on the judge and local paths, where it must be rebuilt as guided/constrained decoding and then **proven on hardware by LL-01/LL-02** before the route is adopted as built.
