"""Does the reviewer help? Replay it over recorded investigations and compare.

Each eval batch keeps a recording of every investigation (the incident, every
tool output, the final report and its citation checks). This script runs the
reviewer agent over those recordings, one model call each, and compares the
verdicts before and after review against the scenarios' known answers:

- accuracy of the top hypothesis (the reviewer can re-rank);
- calibration: the Brier score of the top hypothesis' confidence (lower is
  better), and the stated-versus-actual table.

No sandbox is needed. Results are cached per scenario, so a run that runs out of
free quota resumes where it stopped.

    uv run python evals/review_replay.py [--batches a,b]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml
from relay_agent.config import Settings
from relay_agent.graph.reviewer import Reviewer, apply
from relay_agent.graph.schemas import Incident
from relay_agent.llm.factory import make_provider

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "evals/reports"
SCENARIOS = ROOT / "evals/scenarios"
OUT = REPORTS / "review-replay"


def expected(scenario: str, batch_dir: Path) -> dict[str, Any] | None:
    row = batch_dir / f"{scenario}.json"
    if row.exists():
        return json.loads(row.read_text(encoding="utf-8")).get("details", {}).get("expected")
    path = SCENARIOS / f"{scenario}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["expected"] if path.exists() else None


def load(
    recording: Path,
) -> tuple[Incident, dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]] | None:
    """The incident, the concluded report, the evidence ledger and the citation checks of one run."""
    events = json.loads((recording / "trace.json").read_text(encoding="utf-8"))["events"]
    concluded = next((e for e in events if e["kind"] == "investigation.concluded"), None)
    if concluded is None:
        return None
    evidence = {
        e["evidence_id"]: {
            "tool": e["tool"],
            "arguments": e.get("arguments", {}),
            "output": e.get("output", ""),
        }
        for e in events
        if e["kind"] == "tool.called"
    }
    incident = Incident.model_validate_json((recording / "incident.json").read_text(encoding="utf-8"))
    return incident, concluded["report"], evidence, concluded.get("checks", [])


def correct(h: dict[str, Any] | None, exp: dict[str, Any]) -> bool:
    return bool(h) and h["category"] in exp["category"] and h["service"] in exp["service"]


async def review_one(reviewer: Reviewer, batch: str, scenario: str, recording: Path) -> dict[str, Any] | None:
    cache = OUT / f"{batch}__{scenario}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    exp = expected(scenario, recording.parent.parent)
    loaded = load(recording)
    if exp is None or loaded is None:
        return None
    incident, report, evidence, checks = loaded
    review, response = await reviewer.review(incident, report, evidence, checks)
    if review is None:
        return None
    outcomes = apply(report, review)
    before = report["hypotheses"][0] if report.get("hypotheses") else None
    after = outcomes[0].hypothesis if outcomes else None
    row = {
        "batch": batch,
        "scenario": scenario,
        "model": response.model,
        "before": {
            "top": f"{before['category']} in {before['service']}",
            "confidence": float(before["confidence"]),
            "correct": correct(before, exp),
        }
        if before
        else None,
        "after": {
            "top": f"{after['category']} in {after['service']}",
            "confidence": float(after["confidence"]),
            "correct": correct(after, exp),
            "verdict": outcomes[0].verdict,
            "reason": outcomes[0].reason,
        }
        if after
        else None,
        "reranked": bool(outcomes) and outcomes[0].original_rank != 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(row, indent=2), encoding="utf-8")
    return row


def brier(rows: list[dict[str, Any]], key: str) -> float:
    return round(statistics.fmean((r[key]["confidence"] - r[key]["correct"]) ** 2 for r in rows), 3)


def report(rows: list[dict[str, Any]]) -> str:
    n = len(rows)
    acc = {k: sum(r[k]["correct"] for r in rows) for k in ("before", "after")}
    stated = {k: round(statistics.fmean(r[k]["confidence"] for r in rows), 3) for k in ("before", "after")}
    lines = [
        "# The reviewer, replayed over recorded investigations",
        "",
        f"{n} investigations from {', '.join(sorted({r['batch'] for r in rows}))}. The reviewer saw what the",
        "investigator saw: the incident, every tool output, the report and its citation checks.",
        "Regenerate with `uv run python evals/review_replay.py`.",
        "",
        "| | Before review | After review |",
        "|---|---|---|",
        f"| Top hypothesis correct | {acc['before']}/{n} ({acc['before'] / n:.0%}) | {acc['after']}/{n} ({acc['after'] / n:.0%}) |",
        f"| Mean stated confidence of the top hypothesis | {stated['before']:.0%} | {stated['after']:.0%} |",
        f"| Brier score (lower is better) | {brier(rows, 'before')} | {brier(rows, 'after')} |",
        f"| Re-ranked by the reviewer | | {sum(r['reranked'] for r in rows)} |",
        "",
        "| Batch | Scenario | Before | After | Verdict |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: (r["batch"], r["scenario"])):
        b, a = r["before"], r["after"]
        lines.append(
            f"| {r['batch']} | {r['scenario']} | {b['top']} {b['confidence']:.2f} {'✓' if b['correct'] else '✗'} | "
            f"{a['top']} {a['confidence']:.2f} {'✓' if a['correct'] else '✗'} | {a['verdict']} |"
        )
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--batches", help="comma-separated batch names (default: every batch with recordings)"
    )
    args = parser.parse_args()
    batches = (
        args.batches.split(",")
        if args.batches
        else sorted(p.parent.name for p in REPORTS.glob("*/recordings") if p.is_dir())
    )
    settings = Settings()
    reviewer = Reviewer(make_provider(settings.reviewer, settings), settings.reviewer)
    rows = []
    for batch in batches:
        for recording in sorted((REPORTS / batch / "recordings").glob("*")):
            try:
                row = await review_one(reviewer, batch, recording.name, recording)
            except Exception as e:  # out of quota, most likely: what's cached so far still counts
                print(
                    f"stopping at {batch}/{recording.name}: {type(e).__name__}: {str(e)[:160]}",
                    file=sys.stderr,
                )
                break
            if row:
                rows.append(row)
                print(
                    f"{batch}/{recording.name}: {row['before']['top']} -> {row['after']['top']} ({row['after']['verdict']})"
                )
    if rows:
        (REPORTS / "review-replay.md").write_text(report(rows), encoding="utf-8")
        print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
