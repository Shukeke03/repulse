"""Acquisition plans in seconds relative to the immutable recovery marker."""

from __future__ import annotations

import math

CRITICAL_INTERVALS = (
    (0.0, 10.0),
    (20.0, 30.0),
    (40.0, 50.0),
    (54.0, 64.0),
    (80.0, 90.0),
    (100.0, 110.0),
    (114.0, 124.0),
)
ALIASES = {
    "A": "continuous",
    "B": "periodic",
    "C": "critical",
    "key_window": "critical",
    "key_windows": "critical",
    "window_priority": "critical",
}


def normalize_strategy(strategy: str) -> str:
    value = ALIASES.get(strategy, strategy)
    if value not in {"continuous", "periodic", "critical"}:
        raise ValueError(f"Unknown acquisition strategy: {strategy}")
    return value


def acquisition_on(
    relative_s: float | None,
    strategy: str = "critical",
    phase: float = 0,
    width: float | None = None,
    periodic_width: float | None = None,
) -> bool:
    """Preparation is continuous; recovery uses half-open intervals, never shifts windows."""
    strategy = normalize_strategy(strategy)
    if relative_s is None or relative_s < 0:
        return True
    if relative_s >= 124:
        return False
    if strategy == "continuous":
        return True
    if strategy == "critical":
        return any(start <= relative_s < stop for start, stop in CRITICAL_INTERVALS)
    actual_width = periodic_width if periodic_width is not None else width
    if actual_width is None:
        actual_width = matched_periodic_width(phase)
    return (relative_s - phase) % 20.0 < actual_width


def periodic_on_seconds(width: float, phase: float, duration: float = 124.0) -> float:
    if not (0 <= width <= 20):
        raise ValueError("periodic width must be between 0 and 20 seconds")
    if not (0 <= phase < 20):
        raise ValueError("phase must be in [0,20)")
    total = 0.0
    for cycle in range(-1, math.ceil(duration / 20) + 1):
        start = phase + cycle * 20
        total += max(0.0, min(duration, start + width) - max(0.0, start))
    return total


def matched_periodic_width(
    phase: float = 0.0, target_seconds: float = 70.0, duration: float = 124.0
) -> float:
    """Solve the *intersected* budget, including cycles crossing observation edges."""
    if not (0 <= phase < 20) or not (0 <= target_seconds <= duration):
        raise ValueError("invalid phase or acquisition budget")
    lo, hi = 0.0, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if periodic_on_seconds(mid, phase, duration) < target_seconds:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def recovery_result(windows: list[dict]) -> dict:
    indexed = {int(window.get("center_seconds", window.get("center", 0))): window for window in windows}
    baseline = indexed.get(0, {})
    output: dict = {"delta60": None, "delta120": None, "dependency_ids": {}, "missing_reasons": {}}
    for center in (60, 120):
        key = f"delta{center}"
        current = indexed.get(center, {})
        output["dependency_ids"][key] = [
            baseline.get("id", "window_0"),
            current.get("id", f"window_{center}"),
        ]
        if baseline.get("valid") and current.get("valid"):
            output[key] = round(float(baseline["pulse_bpm"]) - float(current["pulse_bpm"]), 2)
        else:
            missing = []
            for label, item in (("0", baseline), (str(center), current)):
                if not item.get("valid"):
                    missing.append({"window": label, "reasons": item.get("reasons", ["NOT_AVAILABLE"])})
            output["missing_reasons"][key] = missing
    return output
