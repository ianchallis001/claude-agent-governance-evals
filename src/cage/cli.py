"""The ``cage`` command-line interface.

Subcommands:

* ``cage classify MANIFEST.json`` - classify one agent manifest and print its
  two-axis placement, per-tool evidence, and the regulatory controls that attach
  to the resolved governance intensity.
* ``cage eval``                    - run the evaluation suite and print a
  per-dimension, regulator-grounded scorecard.

Shared flags: ``--mock`` (run offline against the scripted dual-role mock
client), ``--threshold`` (gate threshold), ``--max-cost-usd`` (budget guard),
``--repeat`` (self-consistency repeats).

Exit codes map to the gate: ``0`` PASS, ``1`` FIX, ``2`` BLOCK.

Ticket: P1-12.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

# Gate exit codes.
EXIT_PASS = 0
EXIT_FIX = 1
EXIT_BLOCK = 2


def build_parser() -> argparse.ArgumentParser:
    """Build the ``cage`` argument parser (subcommands + shared flags)."""
    parser = argparse.ArgumentParser(
        prog="cage",
        description="Claude Agent Governance Evals - classify and evaluate a "
        "Claude tool-use agent against regulator-grounded controls.",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command", metavar="{classify,eval}")

    def _shared(p: argparse.ArgumentParser) -> None:
        p.add_argument("--mock", action="store_true",
                       help="run offline against the scripted mock client")
        p.add_argument("--threshold", type=float, default=0.7,
                       help="gate threshold in [0,1] (default: 0.7)")
        p.add_argument("--max-cost-usd", type=float, default=None,
                       help="abort if estimated spend exceeds this budget")
        p.add_argument("--repeat", type=int, default=1,
                       help="self-consistency repeats per sample (default: 1)")
        p.add_argument("--jurisdictions", "--regions", dest="jurisdictions",
                       nargs="+", default=None, metavar="REGION",
                       help="scope cited controls by region preset: uk eu us all global "
                            "(combinable). Changes which controls are listed, never the gate.")
        p.add_argument("--frameworks", dest="frameworks",
                       nargs="+", default=None, metavar="SLUG",
                       help="scope cited controls to named frameworks: eu-ai-act gdpr "
                            "uk-gdpr iso-42001 pra-ss1-23 sr-11-7 nist-ai-rmf "
                            "(overrides --jurisdictions).")

    p_classify = sub.add_parser("classify", help="classify one agent manifest")
    p_classify.add_argument("manifest", help="path to an agent/tool manifest JSON file")
    _shared(p_classify)

    p_eval = sub.add_parser("eval", help="run the evaluation suite")
    p_eval.add_argument("--dataset", default=None,
                        help="path to a dataset JSON file (default: bundled golden set)")
    _shared(p_eval)

    return parser


def _build_client(args: argparse.Namespace) -> Any | None:
    """Return the client the commands should drive.

    ``--mock`` builds the offline dual-role governance client (no API key, no
    network). Without it, ``None`` is returned so the agent constructs a live
    ``anthropic.Anthropic`` lazily - the only path that imports the SDK.
    """
    if getattr(args, "mock", False):
        from cage.mock import governance_mock_client

        return governance_mock_client()
    return None


def _resolve_profile(args: argparse.Namespace) -> Any:
    """Resolve the active jurisdiction profile from CLI args, env and config."""
    from cage.profiles import resolve_profile

    return resolve_profile(
        cli_jurisdictions=getattr(args, "jurisdictions", None),
        cli_frameworks=getattr(args, "frameworks", None),
    )


# --------------------------------------------------------------------------- #
# classify                                                                     #
# --------------------------------------------------------------------------- #
def _format_classification(cls: Any, controls: list[Any], profile: Any = None) -> str:
    """Render a classification plus its regulatory control map as ASCII text."""
    lines: list[str] = []
    lines.append(f"Agent:                {cls.agent_name}")
    lines.append(f"Autonomy:             {cls.autonomy.name}")
    lines.append(f"Blast radius:         {cls.blast.name}")
    lines.append(f"Governance intensity: {cls.intensity.name}")
    if profile is not None:
        lines.append(f"Jurisdiction:         {profile.describe()}")
    lines.append("")
    lines.append("Evidence:")
    if cls.evidence:
        for ev in cls.evidence:
            rev = "reversible" if ev.reversible else "IRREVERSIBLE"
            lines.append(
                f"  - {ev.tool} [{ev.kind}] -> {ev.blast_contribution.name} ({rev})"
            )
            if ev.note:
                lines.append(f"      {ev.note}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Rationale:")
    lines.append(f"  {cls.rationale}" if cls.rationale else "  (none)")
    lines.append("")
    lines.append(f"Regulatory controls at {cls.intensity.name} (cumulative):")
    if controls:
        for c in controls:
            lines.append(f"  - {c.framework} {c.clause}: {c.title}")
            lines.append(f"      {c.requirement}")
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _cmd_classify(args: argparse.Namespace) -> int:
    """Handle ``cage classify``: classify one manifest and print it + the regmap."""
    from cage.agent import ClassifierAgent
    from cage.manifest import load_manifest
    from cage.regmap import controls_for_intensity

    manifest = load_manifest(args.manifest)
    client = _build_client(args)
    profile = _resolve_profile(args)
    agent = ClassifierAgent(client=client)
    classification = agent.classify(manifest)
    # The agent-produced Classification leaves controls unset; attach the
    # cumulative control map for the resolved intensity, scoped to the active
    # jurisdiction profile, so classify is itself regulator-grounded.
    controls = controls_for_intensity(classification.intensity, **profile.filter_kwargs)
    print(_format_classification(classification, controls, profile))
    return EXIT_PASS


# --------------------------------------------------------------------------- #
# eval                                                                         #
# --------------------------------------------------------------------------- #
def _fmt_mean(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def _controls_summary(controls: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['framework']} {c['clause']}" for c in controls)


def format_scorecard(result: Any, dataset_name: str, n_samples: int,
                     threshold: float, repeat: int) -> str:
    """Render a :class:`~cage.evals.runner.RunResult` as an ASCII scorecard."""
    detail = result.detail
    manifest = detail.get("run_manifest", {})
    drift = detail.get("drift", {})
    scorecard = detail.get("scorecard", {})

    lines: list[str] = []
    lines.append("cage eval - governance classification")
    lines.append(
        f"dataset: {dataset_name} ({n_samples} sample(s))  "
        f"threshold: {threshold:.2f}  repeat: {repeat}"
    )
    lines.append(
        f"model: {manifest.get('agent_model', '?')}  "
        f"judge: {manifest.get('judge_model', '?')}  "
        f"drift: {'DETECTED' if drift.get('drifted') else 'none'}"
    )
    juris = detail.get("jurisdiction")
    if juris:
        lines.append(f"jurisdiction: {juris}")
    lines.append("")

    # Column layout (space-padded; controls is the free-width trailing column).
    header = f"{'dimension':<18} {'verdict':<7} {'mean':>5} {'n':>3} {'abst':>4}  controls"
    rule = f"{'-' * 18} {'-' * 7} {'-' * 5} {'-' * 3} {'-' * 4}  {'-' * 40}"
    lines.append(header)
    lines.append(rule)

    # Stable dimension order from the enum.
    from cage.regmap import Dimension

    for dim in Dimension:
        row = scorecard.get(dim.value)
        if row is None:
            continue
        lines.append(
            f"{dim.value:<18} {row['verdict']:<7} {_fmt_mean(row['mean_value']):>5} "
            f"{row['n']:>3} {row['n_abstained']:>4}  {_controls_summary(row['controls'])}"
        )

    lines.append("")
    if detail.get("blocked_by"):
        lines.append(f"blocked by: {detail['blocked_by']}")
    block_reasons = detail.get("block_reasons") or []
    if block_reasons:
        lines.append(f"block reasons: {', '.join(block_reasons)}")
    if detail.get("aborted_on_budget"):
        lines.append("note: run aborted on the cost budget (incomplete -> cannot PASS)")
    lines.append(
        f"gate: {result.gate.value}  exit: {result.exit_code}  "
        f"abstained: {len(result.abstained)}  cost: ${result.cost_usd:.2f}"
    )
    return "\n".join(lines)


def _cmd_eval(args: argparse.Namespace) -> int:
    """Handle ``cage eval``: run the harness and print the scorecard."""
    from cage.evals.runner import run_eval
    from cage.evals.task import default_golden_path, governance_task, load_dataset

    if args.dataset is not None:
        dataset = load_dataset(args.dataset)
    else:
        dataset = load_dataset(default_golden_path())

    client = _build_client(args)
    profile = _resolve_profile(args)
    task = governance_task(client=client, dataset=dataset)
    result = run_eval(
        task,
        client=client,
        threshold=args.threshold,
        max_cost_usd=args.max_cost_usd,
        repeat=args.repeat,
        jurisdiction_label=profile.describe(),
        **profile.filter_kwargs,
    )
    print(format_scorecard(
        result, dataset.name, len(dataset), args.threshold, args.repeat
    ))
    return result.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Parses args, dispatches, and returns a gate exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        from cage import __version__

        print(f"cage {__version__}")
        return EXIT_PASS

    if not args.command:
        parser.print_help()
        return EXIT_PASS

    if args.command == "classify":
        return _cmd_classify(args)
    if args.command == "eval":
        return _cmd_eval(args)

    parser.print_help()  # pragma: no cover - argparse guards this
    return EXIT_PASS


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
