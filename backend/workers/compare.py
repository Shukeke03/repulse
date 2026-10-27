"""Paired acquisition-policy experiments on an immutable public recording."""

import hashlib
import json
from collections import defaultdict

import numpy as np

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.domain.models import DEFAULTS
from backend.sources.public_data import DatasetSource

STRATEGIES = ("continuous", "periodic", "critical")


def _run(source, config):
    processor = Processor(config)
    samples = []
    for start in range(0, source.duration_us, 16 * source.period_us):
        rows = source.block(start, min(16, (source.duration_us - start) // source.period_us), source.t0_us)
        processor.feed(rows)
        samples.extend(rows)
    windows = [processor.window(center, source.t0_us, source.duration_us) for center in (0, 60, 120)]
    for window in windows:
        window["center_s"] = window["center_seconds"]
    recovery_samples = [s for s in samples if source.t0_us <= s["time_us"] < source.t0_us + 124_000_000]
    on_seconds = sum(bool(s["on"]) for s in recovery_samples) / source.sample_hz
    openings = sum(
        bool(s["on"]) and (i == 0 or not recovery_samples[i - 1]["on"])
        for i, s in enumerate(recovery_samples)
    )
    # Declared engineering model; not a measurement of Empatica or STM32 power.
    energy = 3.3 * (2.02 * on_seconds + 0.01 * (124 - on_seconds) + 5.25 * 124) + 0.5 * openings
    return windows, recovery_result(windows), on_seconds, energy


def compare_unit(dataset_id, phase, config=None):
    cfg = {**DEFAULTS, **(config or {}), "dataset_id": dataset_id, "phase": phase, "origin": "PUBLIC_DATA"}
    results = []
    for strategy in STRATEGIES:
        source = DatasetSource({**cfg, "strategy": strategy})
        windows, recovery, on_seconds, energy = _run(source, cfg)
        raw = DatasetSource({**cfg, "strategy": "continuous"}).all_samples
        identity = [[r["time_us"], r.get("ppg"), r.get("accel"), r.get("gap")] for r in raw]
        input_hash = hashlib.sha256(
            json.dumps(identity, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        results.append(
            {
                "dataset_id": dataset_id,
                "dataset_label": source.metadata.get("label", dataset_id),
                "origin": "PUBLIC_DATA",
                "strategy": strategy,
                "phase": phase,
                "input_hash": input_hash,
                "sample_hz": source.sample_hz,
                "t0_us": source.t0_us,
                "windows": windows,
                "key_windows": windows,
                "recovery": recovery,
                "on_seconds": round(on_seconds, 6),
                "energy_estimate_mj": round(energy, 4),
                "energy_model_label": "工程估算，非设备实测",
                "budget_error_seconds": round(on_seconds - 70, 6) if strategy != "continuous" else None,
                "budget_quantization_tolerance_seconds": 7 / source.sample_hz,
            }
        )
    reference = {int(w["center_seconds"]): w for w in results[0]["windows"]}
    for row in results:
        row["same_window_error_bpm"] = {
            str(c): round(abs(w["pulse_bpm"] - reference[c]["pulse_bpm"]), 4)
            if w["valid"] and reference[c]["valid"]
            else None
            for c, w in zip((0, 60, 120), row["windows"])
        }
        row["reference_description"] = "相同片段的连续采集算法结果；非独立心电参考"
    return results


def summarize(results, expected_units=None):
    groups = defaultdict(list)
    for row in results:
        groups[(row["dataset_id"], row["phase"])].append(row)
    paired = [
        unit
        for unit in groups.values()
        if len(unit) == 3
        and {r["strategy"] for r in unit} == set(STRATEGIES)
        and len({r["input_hash"] for r in unit}) == 1
    ]
    all_rows = [row for unit in paired for row in unit]

    def mean(values):
        return round(float(np.mean(values)), 6) if values else None

    metrics = []
    for strategy in STRATEGIES:
        rows = [r for r in all_rows if r["strategy"] == strategy]
        metrics.append(
            {
                "strategy": strategy,
                "runs": len(rows),
                "window_availability": {
                    str(c): mean([int(r["windows"][i]["valid"]) for r in rows])
                    for i, c in enumerate((0, 60, 120))
                },
                "delta60_availability": mean([int(r["recovery"]["delta60"] is not None) for r in rows]),
                "delta120_availability": mean([int(r["recovery"]["delta120"] is not None) for r in rows]),
                "on_seconds": mean([r["on_seconds"] for r in rows]),
                "energy_estimate_mj": mean([r["energy_estimate_mj"] for r in rows]),
                "same_window_error_bpm": {
                    str(c): mean(
                        [
                            r["same_window_error_bpm"][str(c)]
                            for r in rows
                            if r["same_window_error_bpm"][str(c)] is not None
                        ]
                    )
                    for c in (0, 60, 120)
                },
                "same_window_comparable_count": {
                    str(c): sum(r["same_window_error_bpm"][str(c)] is not None for r in rows)
                    for c in (0, 60, 120)
                },
            }
        )
    total = len(groups) if expected_units is None else expected_units
    return {
        "paired_units": len(paired),
        "excluded_units": max(0, total - len(paired)),
        "strategies": metrics,
        "unique_recordings": len({r["dataset_id"] for r in all_rows}),
        "reference_description": "连续采集算法结果作为工程对照；不代表医学准确度",
    }
