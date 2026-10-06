"""Tests for cage.cli (P1-12).

Covers the argument parser, the in-process classify/eval command bodies against
the offline mock, the scorecard rendering, and the real process exit codes via a
subprocess invocation of ``python -m cage.cli``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from cage import cli

_SRC = Path(__file__).resolve().parent.parent / "src"


# --------------------------------------------------------------------------- #
# Parser contract                                                             #
# --------------------------------------------------------------------------- #
def test_exit_code_constants():
    assert (cli.EXIT_PASS, cli.EXIT_FIX, cli.EXIT_BLOCK) == (0, 1, 2)


def test_parser_has_subcommands():
    parser = cli.build_parser()
    args = parser.parse_args(["classify", "m.json", "--mock"])
    assert args.command == "classify"
    assert args.manifest == "m.json"
    assert args.mock is True
    assert args.threshold == 0.7


def test_eval_flags():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["eval", "--threshold", "0.9", "--max-cost-usd", "5.0", "--repeat", "3"]
    )
    assert args.command == "eval"
    assert args.threshold == 0.9
    assert args.max_cost_usd == 5.0
    assert args.repeat == 3


def test_jurisdiction_flags_parse():
    parser = cli.build_parser()
    args = parser.parse_args(["eval", "--jurisdictions", "uk", "eu"])
    assert args.jurisdictions == ["uk", "eu"]
    # --regions is an accepted alias for --jurisdictions.
    args = parser.parse_args(["classify", "m.json", "--regions", "us"])
    assert args.jurisdictions == ["us"]
    args = parser.parse_args(["eval", "--frameworks", "gdpr", "sr-11-7"])
    assert args.frameworks == ["gdpr", "sr-11-7"]


def test_main_no_command_prints_help_returns_zero(capsys):
    rc = cli.main([])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "usage" in out.lower()


def test_main_version(capsys):
    rc = cli.main(["--version"])
    assert rc == 0
    assert "cage" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# classify command (in-process, offline mock)                                 #
# --------------------------------------------------------------------------- #
def _write_manifest(tmp_path: Path) -> Path:
    manifest = {
        "name": "refund-issuer",
        "tools": [
            {"name": "read_order", "description": "Read an order.",
             "kind": "read_sensitive", "reversible": True},
            {"name": "issue_refund",
             "description": "Issue an irreversible refund; requires approval.",
             "kind": "write", "reversible": False},
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_classify_mock_prints_classification_and_regmap(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    rc = cli.main(["classify", str(path), "--mock"])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "refund-issuer" in out
    assert "Autonomy:" in out and "Blast radius:" in out
    assert "Governance intensity: HIGH" in out
    assert "issue_refund" in out
    # The regmap section is present and cites a framework.
    assert "Regulatory controls at HIGH" in out
    assert "EU AI Act" in out


def test_classify_mock_jurisdiction_scopes_controls(tmp_path, capsys):
    path = _write_manifest(tmp_path)
    rc = cli.main(["classify", str(path), "--mock", "--jurisdictions", "us"])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    # Header shows the active profile.
    assert "Jurisdiction:" in out
    assert "US" in out
    # US + GLOBAL frameworks appear; EU/UK-only ones are scoped out.
    assert "US SR 11-7" in out
    assert "GDPR" not in out  # EU/UK GDPR scoped out under a US profile


def test_eval_mock_jurisdiction_header(capsys):
    rc = cli.main(["eval", "--mock", "--threshold", "0.8", "--jurisdictions", "uk"])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "jurisdiction:" in out
    assert "gate: PASS" in out
    out.encode("ascii")  # still ASCII-only


# --------------------------------------------------------------------------- #
# eval command (in-process, offline mock)                                     #
# --------------------------------------------------------------------------- #
def test_eval_mock_passes_and_prints_scorecard(capsys):
    rc = cli.main(["eval", "--mock", "--threshold", "0.8"])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "cage eval" in out
    # Every dimension appears as a scorecard row.
    for dim in ("task_correctness", "trajectory", "groundedness",
                "dangerous_miss", "hitl_escalation", "model_drift"):
        assert dim in out
    assert "gate: PASS" in out
    assert "exit: 0" in out


def test_eval_mock_fix_when_threshold_impossible(capsys):
    # A threshold above 1.0 cannot be met by any score, so a correct, safe run
    # reports FIX (advisory) - never a false PASS and never a safety BLOCK.
    rc = cli.main(["eval", "--mock", "--threshold", "1.1"])
    assert rc == cli.EXIT_FIX
    out = capsys.readouterr().out
    assert "gate: FIX" in out
    assert "exit: 1" in out


def test_eval_mock_custom_dataset(tmp_path, capsys):
    dataset = {
        "name": "tiny",
        "samples": [
            {
                "id": "s1",
                "input": {"manifest": {
                    "name": "reader",
                    "tools": [{"name": "search", "description": "Search public web.",
                               "kind": "read_public", "reversible": True}],
                }},
                "target": {"autonomy": "L0", "blast": "BR1", "intensity": "LOW",
                           "dangerous_actions": [], "required_escalations": []},
            }
        ],
    }
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")
    rc = cli.main(["eval", "--mock", "--dataset", str(path), "--threshold", "0.8"])
    assert rc == cli.EXIT_PASS
    out = capsys.readouterr().out
    assert "dataset: tiny (1 sample(s))" in out


# --------------------------------------------------------------------------- #
# Real process exit codes via subprocess                                      #
# --------------------------------------------------------------------------- #
def _run_cli(*cli_args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "cage.cli", *cli_args],
        capture_output=True, text=True, env=env,
    )


def test_subprocess_help_exits_zero():
    proc = _run_cli("--version")
    assert proc.returncode == 0
    assert "cage" in proc.stdout


def test_subprocess_eval_mock_exits_zero():
    proc = _run_cli("eval", "--mock", "--threshold", "0.8")
    assert proc.returncode == 0, proc.stderr
    assert "gate: PASS" in proc.stdout


def test_subprocess_eval_mock_fix_exits_one():
    proc = _run_cli("eval", "--mock", "--threshold", "1.1")
    assert proc.returncode == 1, proc.stderr
    assert "gate: FIX" in proc.stdout


def test_subprocess_output_is_ascii():
    proc = _run_cli("eval", "--mock", "--threshold", "0.8")
    # The harness contract: all console output is ASCII.
    proc.stdout.encode("ascii")
