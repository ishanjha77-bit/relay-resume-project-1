"""Two eval batches side by side: the before/after table for the README.

Reads each batch's per-scenario rows (evals/reports/<batch>/*.json), recomputes
both scorecards with the same rules (scoring.aggregate), and writes a Markdown
comparison: the headline metrics, then every scenario's verdict in each batch.
Only scenarios both batches ran are compared head to head, so scenarios added
or removed in between don't skew the deltas. A recalibrated scenario keeps its
id and is compared; the batches' labels say what changed.

    uv run python evals/compare.py 2026-10-05-baseline 2026-10-06-after
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scoring import aggregate

REPORTS = Path(__file__).resolve().parent / "reports"


def rows(batch: str) -> dict[str, dict[str, Any]]:
    batch_dir = REPORTS / batch
    if not batch_dir.is_dir():
        raise SystemExit(f"no batch {batch} in {REPORTS}")
    out = {}
    for path in sorted(batch_dir.glob("*.json")):
        if path.stem not in ("scorecard", "batch"):
            row = json.loads(path.read_text(encoding="utf-8"))
            out[row["scenario"]] = row
    return out


def cache_share(rs: list[dict[str, Any]]) -> float | None:
    prompt = sum(r.get("prompt_tokens") or 0 for r in rs)
    return round(sum(r.get("cache_read_tokens") or 0 for r in rs) / prompt, 3) if prompt else None


def verdict(row: dict[str, Any] | None) -> str:
    if row is None:
        return "-"
    if row["status"] not in ("scored", "agent_failed"):
        return f"({row['status']})"
    mark = "✅" if row["correct"] else ("top 3" if row.get("correct_top3") else "❌")
    top = (
        f"{row.get('top_category')} in {row.get('top_service')}" if row.get("top_category") else "no verdict"
    )
    return f"{mark} {top} · {row.get('seconds') or '?'} s"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument(
        "--out", help="write the Markdown here (default: evals/reports/compare-<before>-<after>.md)"
    )
    args = parser.parse_args()

    before, after = rows(args.before), rows(args.after)
    shared = sorted(set(before) & set(after))
    cards = {
        "before": aggregate(before[s] for s in shared),
        "after": aggregate(after[s] for s in shared),
    }

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.0f}%"

    def num(v: Any, unit: str = "") -> str:
        return "n/a" if v is None else f"{v}{unit}"

    b, a = cards["before"], cards["after"]
    lines = [
        f"# {args.before} → {args.after}",
        "",
        f"{len(shared)} scenarios ran in both batches; only those are compared here.",
        "",
        f"| Metric | {args.before} | {args.after} |",
        "|---|---|---|",
        f"| Root-cause accuracy (top hypothesis) | {pct(b['accuracy'])} of {b['answered']} | "
        f"{pct(a['accuracy'])} of {a['answered']} |",
        f"| Accuracy, any of the top 3 | {pct(b['accuracy_top3'])} | {pct(a['accuracy_top3'])} |",
        f"| Median time to diagnosis (fault → verdict) | {num(b['median_seconds'], ' s')} | "
        f"{num(a['median_seconds'], ' s')} |",
        f"| Median agent time (incident → verdict) | {num(b['median_agent_seconds'], ' s')} | "
        f"{num(a['median_agent_seconds'], ' s')} |",
        f"| Mean tool calls / model calls | {num(b['mean_steps'])} / {num(b['mean_llm_calls'])} | "
        f"{num(a['mean_steps'])} / {num(a['mean_llm_calls'])} |",
        f"| Mean prompt tokens per incident | {num(b['mean_prompt_tokens'])} | {num(a['mean_prompt_tokens'])} |",
        f"| Prompt tokens served from cache | {pct(cache_share([before[s] for s in shared]))} | "
        f"{pct(cache_share([after[s] for s in shared]))} |",
        f"| Citations verified verbatim | {pct(b['citations_verified'])} | {pct(a['citations_verified'])} |",
        f"| Not answered (no alert / timeout) | {b['unanswered']['no_alert']} / {b['unanswered']['timeout']} | "
        f"{a['unanswered']['no_alert']} / {a['unanswered']['timeout']} |",
        "",
        f"| Scenario | {args.before} | {args.after} |",
        "|---|---|---|",
    ]
    lines += [f"| {s} | {verdict(before.get(s))} | {verdict(after.get(s))} |" for s in shared]
    only = sorted(set(before) ^ set(after))
    if only:
        lines += ["", f"Not compared (in one batch only): {', '.join(only)}."]
    out = Path(args.out) if args.out else REPORTS / f"compare-{args.before}-{args.after}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
