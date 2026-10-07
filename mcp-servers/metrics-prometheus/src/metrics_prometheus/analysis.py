"""Summarize time series so the model reads shape, not thousands of points."""

from __future__ import annotations

from statistics import fmean
from typing import Any

from metrics_prometheus.prom import iso, to_float


def rounded(value: float | None) -> float | None:
    """Four significant digits: enough to compare, cheap in tokens."""
    if value is None:
        return None
    if value == 0:
        return 0.0
    return float(f"{value:.4g}")


def change_point(points: list[tuple[float, float]], min_segment: int = 3) -> dict[str, Any] | None:
    """The split that best separates the series into a 'before' and an 'after' level.

    Picks the index maximizing |mean(after) - mean(before)| (a single mean-shift
    change point) and reports it only when the shift is large relative to the
    level before it — otherwise noise would always produce some "change".
    """
    n = len(points)
    if n < 2 * min_segment:
        return None
    prefix = [0.0]
    for _, v in points:
        prefix.append(prefix[-1] + v)
    total = prefix[-1]
    best_index, best_shift, before, after = None, 0.0, 0.0, 0.0
    for i in range(min_segment, n - min_segment + 1):
        mean_before = prefix[i] / i
        mean_after = (total - prefix[i]) / (n - i)
        shift = abs(mean_after - mean_before)
        if shift > best_shift:
            best_index, best_shift, before, after = i, shift, mean_before, mean_after
    if best_index is None:
        return None
    scale = max(abs(before), abs(after), 1e-9)
    if best_shift / scale < 0.3:
        return None
    return {"at": iso(points[best_index][0]), "before": rounded(before), "after": rounded(after)}


def downsample(points: list[tuple[float, float]], target: int = 20) -> list[list[Any]]:
    """At most ``target`` points, keeping each bucket's maximum so spikes survive."""
    if len(points) <= target:
        return [[iso(ts), rounded(v)] for ts, v in points]
    size = len(points) / target
    out = []
    for b in range(target):
        bucket = points[int(b * size) : int((b + 1) * size)] or [points[-1]]
        ts, value = max(bucket, key=lambda p: p[1])
        out.append([iso(ts), rounded(value)])
    return out


def summarize_series(series: dict[str, Any]) -> dict[str, Any]:
    points = [(float(ts), v) for ts, raw in series["values"] if (v := to_float(raw)) is not None]
    labels = {
        k: v for k, v in series["metric"].items() if k not in ("__name__", "job", "instance", "cluster")
    }
    if not points:
        return {"labels": labels, "points": []}
    values = [v for _, v in points]
    summary: dict[str, Any] = {
        "labels": labels,
        "first": rounded(values[0]),
        "last": rounded(values[-1]),
        "min": rounded(min(values)),
        "max": rounded(max(values)),
        "mean": rounded(fmean(values)),
    }
    if change := change_point(points):
        summary["change"] = change
    summary["points"] = downsample(points)
    return summary
