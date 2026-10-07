from metrics_prometheus.analysis import change_point, downsample, rounded, summarize_series

T0 = 1_791_094_000.0


def series(values: list[float], step: float = 30.0) -> list[tuple[float, float]]:
    return [(T0 + i * step, v) for i, v in enumerate(values)]


def test_change_point_finds_a_step() -> None:
    points = series([0.05] * 20 + [2.8] * 10)
    change = change_point(points)
    assert change is not None
    assert change["at"] == "2026-10-04T06:16:40Z"  # T0 (06:06:40Z) + 20 steps of 30 s
    assert change["before"] == 0.05
    assert change["after"] == 2.8


def test_change_point_ignores_noise() -> None:
    points = series([1.0, 1.1, 0.95, 1.05, 1.0, 0.98, 1.02, 1.04, 0.97, 1.0])
    assert change_point(points) is None


def test_downsample_keeps_spikes() -> None:
    values = [1.0] * 100
    values[57] = 50.0
    points = downsample(series(values), target=10)
    assert len(points) == 10
    assert max(v for _, v in points) == 50.0


def test_rounded_uses_four_significant_digits() -> None:
    assert rounded(0.123456) == 0.1235
    assert rounded(123456.0) == 123500.0
    assert rounded(None) is None


def test_summarize_series_drops_nan_and_noise_labels() -> None:
    raw = {
        "metric": {"__name__": "x", "job": "pods", "service": "orders"},
        "values": [[T0, "NaN"], [T0 + 30, "1"], [T0 + 60, "3"]],
    }
    summary = summarize_series(raw)
    assert summary["labels"] == {"service": "orders"}
    assert summary["first"] == 1.0
    assert summary["max"] == 3.0
