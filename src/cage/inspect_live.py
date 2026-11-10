"""Live two-turn classifier loop + family-separated judge, expressed for Inspect.

This is the *second* Inspect route for cage, proving the PP-07 / LL-07 ("hybrid")
desk verdict in code, offline. Where :mod:`cage.inspect_adapter` deliberately
wires cage's **deterministic** ``classify_manifest`` as the solver (behavioural
consistency), this module adds the **live two-turn loop** as a custom Inspect
solver and re-expresses the per-dimension judge as an Inspect model-graded scorer
with an enforced grader-model *role* (family separation).

What is proved here (the spike's load-bearing claims, now executable):

* **The loop survives as an Inspect solver.** cage's ``inspect_tool`` ->
  ``submit_classification`` two-turn loop (see :mod:`cage.agent`) is driven through
  Inspect's model API: ``model.generate(input=state.messages, tools=[...])`` ->
  read the assistant tool calls -> ``execute_tools`` the ``inspect_tool`` calls ->
  extend ``state.messages`` -> loop. cage's own system prompt, both tool schemas
  and the ``submit`` -> :class:`~cage.classify.Classification` parsing are reused
  verbatim. Intensity is re-derived from the authoritative matrix
  (:func:`cage.classify.intensity_for`), never trusted from the model's
  self-report - consistent with cage's behavioural-consistency principle.

* **The terminal exit is detected on the tool-call NAME, not SDK forcing.** This
  is the documented "weak joint": Inspect's ``generate`` *does* expose a named
  ``ToolFunction`` tool_choice, but whether that named forcing is forwarded by a
  given provider (notably ``vllm/``) is not guaranteed. So the loop uses
  ``tool_choice="any"`` (react-style) and terminates when a ``submit_classification``
  tool call appears by name. This is safe because the matrix - not the model's
  self-reported intensity - is authoritative.

* **The judge maps to a grader-role model-graded scorer.** The scorer retrieves
  its grader with ``get_model(role="grader", required=True)``, which *raises*
  rather than silently falling back to the model under test when no grader is
  bound - the exact family-separation guarantee cage's judge isolation needs. The
  typed verdict is re-expressed as a forced ``record_verdict`` structured tool
  call, and :func:`cage.evals.judge.verdict_from_tool_input` stays the semantic
  backstop that range-checks the result.

Design invariant (identical to :mod:`cage.inspect_adapter`): ``inspect_ai`` is an
OPTIONAL dependency. Nothing here is imported at cage module load; every
``inspect_ai`` symbol is imported lazily inside :func:`_load_inspect_live`. The
pure helpers (prompt / schema / submission parsing) import only cage, so they are
unit-testable with ``inspect_ai`` NOT installed.

Tickets: PP-07 (#55), LL-07 (#64) - code proof of the "hybrid" route.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from cage.agent import (
    _INSPECT_TOOL,
    _SUBMIT_TOOL,
    _SYSTEM_PROMPT,
    ClassifierAgent,
)
from cage.evals.judge import (
    RECORD_VERDICT_TOOL_NAME,
    VERDICT_TOOL_SCHEMA,
    DimensionJudge,
    Verdict,
    build_judge_prompt,
    verdict_from_tool_input,
)
from cage.evals.task import AgentOutput
from cage.inspect_adapter import (
    _META_OUTPUT,
    _cage_sample_from_meta,
    _completion_text,
    _json_safe,
    _output_from_state_metadata,
)
from cage.manifest import AgentManifest
from cage.regmap import Dimension

__all__ = [
    "build_classification_from_submission",
    "cage_live_classifier_solver",
    "cage_live_judge_scorer",
    "inspect_tool_lookup",
    "run_grader_verdict",
    "run_live_classification_loop",
    "trajectory_from_tool_calls",
]

# The grader model role the judge scorer requires. Bound via `Task(model_roles=...)`
# or the CLI `--model-role grader=<model>`; `required=True` makes its absence an
# error rather than a silent fall-back to the model under test.
GRADER_ROLE = "grader"


# --------------------------------------------------------------------------- #
# Pure, SDK-free helpers (no inspect_ai import; unit-tested with it absent).   #
# --------------------------------------------------------------------------- #
def build_classification_from_submission(
    manifest: AgentManifest, submission: dict[str, Any]
):
    """Parse a ``submit_classification`` tool input into a :class:`Classification`.

    Reuses cage's own terminal parser verbatim, so the governance verdict is
    identical to the native Anthropic loop: the axes come from the submission but
    the intensity is re-derived from the authoritative matrix
    (:func:`cage.classify.intensity_for`), discarding the model's self-report.
    """
    return ClassifierAgent._build_classification(manifest, submission)


def inspect_tool_lookup(manifest: AgentManifest, tool_input: dict[str, Any]) -> str:
    """Return the JSON a cage ``inspect_tool`` call yields for ``tool_input``.

    Reuses :meth:`cage.agent.ClassifierAgent._inspect` so the read-only inspection
    the model sees under Inspect is byte-identical to the native loop.
    """
    by_name = {t.name: t for t in manifest.tools}
    return ClassifierAgent._inspect(by_name, tool_input)


def trajectory_from_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """Map Inspect ``ToolCall`` objects to cage's trajectory-record shape.

    cage records ``{"tool": name, "input": {...}, "id": id}`` per call (see
    :meth:`cage.agent.ClassifierAgent.classify`); the Inspect ``ToolCall`` carries
    the name as ``.function``, the args as ``.arguments`` and the id as ``.id``.
    """
    rows: list[dict[str, Any]] = []
    for call in tool_calls or []:
        rows.append(
            {
                "tool": getattr(call, "function", None),
                "input": dict(getattr(call, "arguments", {}) or {}),
                "id": getattr(call, "id", ""),
            }
        )
    return rows


def _user_prompt(manifest: AgentManifest) -> str:
    """cage's own classifier user prompt (the manifest as pretty JSON)."""
    return ClassifierAgent._user_prompt(manifest)


# --------------------------------------------------------------------------- #
# Inspect SDK glue (lazy: imported only when these functions are called).      #
# --------------------------------------------------------------------------- #
def _load_inspect_live() -> SimpleNamespace:
    """Import and bundle the ``inspect_ai`` symbols the live route needs, lazily.

    Kept out of module scope so importing :mod:`cage.inspect_live` never requires
    the optional SDK. Raises a clear, actionable error if it is absent.
    """
    try:
        from inspect_ai.model import (
            ChatMessageSystem,
            ChatMessageUser,
            execute_tools,
            get_model,
        )
        from inspect_ai.scorer import Score, Target, mean, scorer
        from inspect_ai.solver import Generate, TaskState, solver
        from inspect_ai.tool import ToolDef, ToolFunction, ToolParams
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError(
            "the 'inspect_ai' package is required for the live Inspect route; "
            "install it with `pip install -e .[inspect]`"
        ) from exc
    return SimpleNamespace(
        ChatMessageSystem=ChatMessageSystem,
        ChatMessageUser=ChatMessageUser,
        execute_tools=execute_tools,
        get_model=get_model,
        Score=Score,
        Target=Target,
        mean=mean,
        scorer=scorer,
        Generate=Generate,
        TaskState=TaskState,
        solver=solver,
        ToolDef=ToolDef,
        ToolFunction=ToolFunction,
        ToolParams=ToolParams,
    )


def _tool_params(input_schema: dict[str, Any], ins: SimpleNamespace) -> Any:
    """Convert a cage Anthropic ``input_schema`` into an Inspect ``ToolParams``.

    cage's tool schemas are plain JSON Schema objects, which Inspect's
    ``ToolParams`` mirrors field-for-field, so a direct validate preserves the
    enums and nested evidence shape rather than flattening them to bare strings.
    """
    return ins.ToolParams.model_validate(input_schema)


def _build_tool_defs(
    manifest: AgentManifest, ins: SimpleNamespace
) -> tuple[Any, Any]:
    """Build the two Inspect ``ToolDef`` objects for one manifest.

    ``inspect_tool`` is a real, executable read-only lookup closed over the
    manifest (so ``execute_tools`` returns cage's own inspection JSON).
    ``submit_classification`` is offered to the model but is *terminal*: the loop
    intercepts it by name and never executes it, mirroring cage's native loop,
    which returns the moment ``submit`` is seen without sending a tool result back.
    """
    by_name = {t.name: t for t in manifest.tools}

    async def inspect_tool(tool_name: str) -> str:
        return ClassifierAgent._inspect(by_name, {"tool_name": tool_name})

    async def submit_classification(**kwargs: Any) -> str:  # pragma: no cover - never executed
        # Terminal tool: the loop handles it by name before execute_tools, so this
        # body is a defensive placeholder only.
        return "classification recorded"

    inspect_def = ins.ToolDef(
        inspect_tool,
        name=_INSPECT_TOOL["name"],
        description=_INSPECT_TOOL["description"],
        parameters=_tool_params(_INSPECT_TOOL["input_schema"], ins),
    )
    submit_def = ins.ToolDef(
        submit_classification,
        name=_SUBMIT_TOOL["name"],
        description=_SUBMIT_TOOL["description"],
        parameters=_tool_params(_SUBMIT_TOOL["input_schema"], ins),
    )
    return inspect_def, submit_def


def _verdict_tool_def(ins: SimpleNamespace) -> Any:
    """Build the Inspect ``ToolDef`` for the judge's ``record_verdict`` tool."""

    async def record_verdict(**kwargs: Any) -> str:  # pragma: no cover - never executed
        return "verdict recorded"

    return ins.ToolDef(
        record_verdict,
        name=RECORD_VERDICT_TOOL_NAME,
        description=(
            "Record your typed verdict for this one dimension. You MUST call this "
            "tool; do not answer in free text."
        ),
        parameters=_tool_params(VERDICT_TOOL_SCHEMA, ins),
    )


# --------------------------------------------------------------------------- #
# The live two-turn loop, driven through Inspect's model API.                  #
# --------------------------------------------------------------------------- #
async def run_live_classification_loop(
    manifest: AgentManifest,
    model: Any,
    ins: SimpleNamespace | None = None,
    *,
    messages: list[Any] | None = None,
    max_turns: int = 8,
) -> tuple[AgentOutput, list[Any]]:
    """Drive cage's two-turn ``inspect_tool`` -> ``submit_classification`` loop.

    ``model`` is any object exposing an async ``generate(input, tools, tool_choice)``
    returning a ModelOutput whose ``.message`` carries ``.tool_calls`` - a real
    Inspect ``Model`` in production, a scripted stub in tests. Returns the solved
    :class:`AgentOutput` (classification + cage-shaped trajectory + manifest) and
    the full message list (so a solver can write it back to ``state.messages``).

    Mirrors :meth:`cage.agent.ClassifierAgent.classify`: the system prompt and
    both tool schemas are cage's; ``submit_classification`` is detected by NAME and
    is terminal (the documented substitute for SDK-level named tool forcing).
    """
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")
    ins = ins or _load_inspect_live()

    inspect_def, submit_def = _build_tool_defs(manifest, ins)
    tools = [inspect_def, submit_def]

    if messages is None:
        messages = [
            ins.ChatMessageSystem(content=_SYSTEM_PROMPT),
            ins.ChatMessageUser(content=_user_prompt(manifest)),
        ]

    trajectory: list[dict[str, Any]] = []
    for _ in range(max_turns):
        # tool_choice="any" forces *a* tool call (react-style) but NOT a named one:
        # terminal detection is on the submit_classification name, per the spike.
        output = await model.generate(input=messages, tools=tools, tool_choice="any")
        assistant = output.message
        messages.append(assistant)

        tool_calls = list(getattr(assistant, "tool_calls", None) or [])
        if not tool_calls:
            raise ValueError(
                "agent ended without calling submit_classification "
                f"(stop_reason={getattr(output, 'stop_reason', None)!r})"
            )

        trajectory.extend(trajectory_from_tool_calls(tool_calls))

        submit = next(
            (c for c in tool_calls if getattr(c, "function", None) == "submit_classification"),
            None,
        )
        if submit is not None:
            classification = build_classification_from_submission(
                manifest, dict(submit.arguments or {})
            )
            return (
                AgentOutput(
                    classification=classification,
                    trajectory=trajectory,
                    manifest_dict=manifest.to_dict(),
                ),
                messages,
            )

        # No terminal call this turn: execute the inspect_tool call(s) and feed the
        # results back, exactly as cage feeds tool_result blocks back.
        result = await ins.execute_tools(messages, tools)
        messages.extend(result.messages)

    raise RuntimeError(
        f"agent did not submit a classification within {max_turns} turn(s)"
    )


def cage_live_classifier_solver(
    model: Any | None = None,
    *,
    max_turns: int = 8,
    _inspect: SimpleNamespace | None = None,
) -> Any:
    """Build the Inspect solver that runs cage's LIVE two-turn loop.

    Unlike :func:`cage.inspect_adapter.cage_classifier_solver` (which runs the
    deterministic classifier), this solver drives a real model through the
    ``inspect_tool`` -> ``submit_classification`` loop. ``model`` defaults to
    Inspect's active model (``get_model()``); inject a stub to test offline. The
    solved classification is stashed in ``state.metadata`` for the scorers.
    """
    ins = _inspect or _load_inspect_live()

    @ins.solver
    def _factory() -> Any:
        async def solve(state: Any, generate: Any) -> Any:
            the_model = model if model is not None else ins.get_model()
            cage_sample = _cage_sample_from_meta(state.metadata)
            if "manifest" not in cage_sample.input:
                raise ValueError(
                    f"sample {cage_sample.id!r} has no input.manifest to classify"
                )
            manifest = AgentManifest.from_dict(cage_sample.input["manifest"])

            seed = [
                ins.ChatMessageSystem(content=_SYSTEM_PROMPT),
                ins.ChatMessageUser(content=_user_prompt(manifest)),
            ]
            output, messages = await run_live_classification_loop(
                manifest, the_model, ins, messages=seed, max_turns=max_turns
            )
            state.messages = messages
            state.metadata[_META_OUTPUT] = output.to_dict()
            try:
                state.output.completion = _completion_text(output.classification)
            except Exception:  # pragma: no cover - output shape varies by version
                pass
            state.completed = True
            return state

        return solve

    return _factory()


# --------------------------------------------------------------------------- #
# The judge as a grader-role model-graded scorer.                              #
# --------------------------------------------------------------------------- #
async def run_grader_verdict(
    dimension: Dimension,
    sample: Any,
    transcript: Any,
    grader_model: Any,
    ins: SimpleNamespace | None = None,
) -> Verdict:
    """Grade one ``dimension`` with a family-separated grader model.

    Reuses cage's isolated per-dimension prompt (:func:`build_judge_prompt`) and
    system prompt, forces the typed ``record_verdict`` structured tool call, and
    parses it through :func:`verdict_from_tool_input` - the same semantic backstop
    the native judge uses. ``grader_model`` is a real Inspect ``Model`` (bound to
    the ``grader`` role) in production or a scripted stub in tests.
    """
    ins = ins or _load_inspect_live()
    prompt = build_judge_prompt(dimension, sample, transcript)
    verdict_def = _verdict_tool_def(ins)
    messages = [
        ins.ChatMessageSystem(content=DimensionJudge._SYSTEM_PROMPT),
        ins.ChatMessageUser(content=prompt),
    ]
    # Named tool_choice IS expressible via Inspect's ToolFunction; for API/mock
    # providers it forces the verdict call. (For vllm/ this forwarding is the
    # spike's open risk; the loop above avoids relying on it, the judge keeps the
    # range-check backstop below regardless.)
    output = await grader_model.generate(
        input=messages,
        tools=[verdict_def],
        tool_choice=ins.ToolFunction(name=RECORD_VERDICT_TOOL_NAME),
    )
    assistant = output.message
    for call in getattr(assistant, "tool_calls", None) or []:
        if getattr(call, "function", None) == RECORD_VERDICT_TOOL_NAME:
            return verdict_from_tool_input(dimension, dict(call.arguments or {}))
    raise ValueError(
        f"grader for {dimension.value} did not call {RECORD_VERDICT_TOOL_NAME!r} "
        "(free-text verdicts are rejected)"
    )


def cage_live_judge_scorer(
    dimension: Dimension,
    grader_model: Any | None = None,
    *,
    _inspect: SimpleNamespace | None = None,
) -> Any:
    """Build an Inspect model-graded scorer for one ``dimension`` with a grader role.

    The scorer retrieves its grader with ``get_model(role="grader", required=True)``
    - which raises if no distinct grader is bound, enforcing family separation
    (the grader is never silently the model under test). Inject ``grader_model`` to
    test offline. Reads the solver's classification from the state metadata, grades
    it with a family-separated model-graded call, and maps cage's typed verdict
    onto an Inspect ``Score``.
    """
    ins = _inspect or _load_inspect_live()

    @ins.scorer(metrics=[ins.mean()], name=f"cage_judge_{dimension.value}")
    def _factory() -> Any:
        async def score(state: Any, target: Any) -> Any:
            grader = (
                grader_model
                if grader_model is not None
                else ins.get_model(role=GRADER_ROLE, required=True)
            )
            cage_sample = _cage_sample_from_meta(state.metadata)
            output = _output_from_state_metadata(state.metadata)
            verdict = await run_grader_verdict(
                dimension, cage_sample, output, grader, ins
            )
            return ins.Score(
                value=float(verdict.score),
                answer=verdict.verdict.value,
                explanation=f"{dimension.value}: {verdict.verdict.value} - {verdict.cited_reason}",
                metadata={
                    "dimension": dimension.value,
                    "verdict": verdict.verdict.value,
                    "confidence": verdict.confidence,
                    "unknown": verdict.unknown,
                    "detail": _json_safe(verdict.to_dict()),
                },
            )

        return score

    return _factory()
