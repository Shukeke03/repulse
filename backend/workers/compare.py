"""Pure offline experimental units, suitable for ProcessPoolExecutor workers.

Input hash describes every complete optical observation before policy masking. Each
strategy applies physical OFF periods *before* filtering. Energy is a declared model,
not measured consumption. Ground-truth assessment is isolated from Processor.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

import numpy as np

from backend.algorithms.policies import matched_periodic_width, normalize_strategy, recovery_result
from backend.algorithms.processor import Processor
from backend.sources.simulator import Source, latent_pulse

STRATEGIES = ("continuous", "periodic", "critical")


def _input_digest(samples: list[dict], config: dict) -> str:
    payload = {
        "schema_version": 1,
        "generator": "repulse-synthetic-1.0",
        "config": config,
        "observations": [[s["time_us"], s["ppg"], s["accel"], s["gap"], s["fault"]] for s in samples],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def simulate(
    seed: int = 20261025,
    scenario: str = "normal",
    strategy: str = "critical",
    phase: float = 0,
    config: dict | None = None,
) -> dict:
    cfg = dict(config or {})
    strategy = normalize_strategy(strategy)
    prepare = float(cfg.get("prepare_seconds", 6))
    if scenario in {"early", "insufficient_history"}:
        prepare = 2
    t0_us = int(prepare * 1e6)
    duration_us = t0_us + 124_000_000
    # Exclude acquisition settings from the immutable generator identity.
    generator = {
        key: value for key, value in cfg.items() if key not in {"strategy", "phase", "periodic_width"}
    }
    generator.update({"seed": int(seed), "scenario": scenario, "prepare_seconds": prepare})
    full = Source({**generator, "strategy": "continuous"})
    count = (duration_us + 9999) // 10_000
    complete_samples = full.block(0, count=count, t0_us=t0_us, faults=cfg.get("faults", []))
    input_hash = _input_digest(complete_samples, generator)
    width = matched_periodic_width(float(phase))
    source = Source({**generator, "strategy": strategy, "phase": phase, "periodic_width": width})
    processor = Processor(cfg)
    samples = source.block(0, count=count, t0_us=t0_us, faults=cfg.get("faults", []))
    # Feed blocks, exactly as the online acquisition path; never precompute a full
    # continuous estimate and pretend that policy-specific samples were absent.
    for offset in range(0, len(samples), 16):
        processor.feed(samples[offset : offset + 16])
    windows = [processor.window(center, t0_us, duration_us) for center in (0, 60, 120)]
    recovery = recovery_result(windows)
    recovery_samples = [s for s in samples if s["time_us"] >= t0_us]
    on_seconds = sum(bool(s["on"]) for s in recovery_samples) / 100
    # Count switch-ons in recovery including t0 continuity as one opening if ON.
    openings = 0
    prev = False
    for s in recovery_samples:
        if s["on"] and not prev:
            openings += 1
        prev = bool(s["on"])
    # Sensor-on 2.02mA, idle0.01mA, fixed system5.25mA at3.3V; restart0.5mJ.
    recovery_energy = 3.3 * (2.02 * on_seconds + 0.01 * (124 - on_seconds) + 5.25 * 124) + 0.5 * openings
    prep_energy = 3.3 * (2.02 + 5.25) * prepare
    clean_cfg = {**generator, "scenario": "normal", "strategy": "continuous", "noise_std": 0}
    reference_source = Source(clean_cfg)
    reference_processor = Processor(cfg)
    reference_processor.feed(reference_source.block(0, count=count, t0_us=t0_us))
    clean_windows = [reference_processor.window(center, t0_us, duration_us) for center in (0, 60, 120)]
    errors = {}
    false_valid = {}
    definite_invalid = {}
    latent_errors = {}
    evaluable = {}
    inaccurate_valid = {}
    for window, reference in zip(windows, clean_windows):
        center = int(window["center_seconds"])
        k = str(center)
        errors[k] = (
            round(abs(window["pulse_bpm"] - reference["pulse_bpm"]), 3)
            if window["valid"] and reference["valid"]
            else None
        )
        support = [s for s in samples if window["start_us"] <= s["time_us"] < window["end_us"]]
        values = np.asarray([s["ppg"] for s in support if s["ppg"] is not None], dtype=float)
        # Definite invalid means physical loss, warmup, or flat/saturated observations;
        # the mere presence of noise does not imply a window must be invalid.
        invalid = len(values) != 800 or any(s.get("warming") for s in support)
        if len(values):
            invalid = (
                invalid
                or float(np.std(values)) < 25
                or np.mean(values == values.min()) > 0.08
                or np.mean(values == values.max()) > 0.08
            )
        definite_invalid[k] = bool(invalid)
        inaccurate_valid[k] = bool(window["valid"] and errors[k] is not None and errors[k] > 10)
        evaluable[k] = bool(invalid or reference["valid"])
        false_valid[k] = bool(window["valid"] and (invalid or inaccurate_valid[k]))
        grid = np.arange(window["start_us"], window["end_us"], 10_000) / 1e6
        reference_latent = float(np.median(latent_pulse(grid, prepare, cfg)))
        latent_errors[k] = round(abs(window["pulse_bpm"] - reference_latent), 3) if window["valid"] else None
    return {
        "seed": int(seed),
        "scenario": scenario,
        "strategy": strategy,
        "phase": float(phase),
        "input_hash": input_hash,
        "input_description": "完整合成观测在采集策略遮罩之前的SHA256",
        "periodic_width": width if strategy == "periodic" else None,
        "t0_us": t0_us,
        "windows": windows,
        "key_windows": windows,
        "recovery": recovery,
        "on_seconds": round(on_seconds, 5),
        "prepare_seconds": prepare,
        "openings": openings,
        "budget_error_seconds": round(on_seconds - 70, 5) if strategy != "continuous" else None,
        "budget_quantization_tolerance_seconds": 0.06,
        "budget_model": {
            "target_seconds": 70,
            "matching": "连续时间精确求解；100Hz采样计数保留量化差",
            "sample_seconds": 0.01,
            "comparison_phase_grid_seconds": 1,
            "quantization_tolerance_seconds": 0.06,
            "periodic_intervals_unmodified": True,
        },
        "energy_estimate_mj": round(recovery_energy + prep_energy, 4),
        "recovery_energy_estimate_mj": round(recovery_energy, 4),
        "energy_model": {
            "label": "估算",
            "voltage_v": 3.3,
            "sensor_on_ma": 2.02,
            "sensor_off_ma": 0.01,
            "other_ma": 5.25,
            "restart_mj": 0.5,
            "preparation_included": True,
        },
        "same_window_error_bpm": errors,
        "latent_error_bpm": latent_errors,
        "false_valid": false_valid,
        "definite_invalid": definite_invalid,
        "evaluable": evaluable,
        "inaccurate_valid": inaccurate_valid,
        "error_tolerance_bpm": 10,
        "versions": {"algorithm": "repulse-dsp-1.0", "generator": "repulse-synthetic-1.0"},
    }


def compare_unit(seed: int, phase: float, scenario: str = "normal", config: dict | None = None) -> list[dict]:
    return [simulate(seed, scenario, strategy, phase, config) for strategy in STRATEGIES]


def summarize(results: list[dict], expected_units: int | None = None) -> dict:
    """Only include complete, input-matched three-strategy units in paired metrics."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in results:
        groups[(row["seed"], row["phase"], row.get("scenario", "normal"))].append(row)
    paired = [
        unit
        for unit in groups.values()
        if {r["strategy"] for r in unit} == set(STRATEGIES)
        and len(unit) == 3
        and len({r["input_hash"] for r in unit}) == 1
    ]
    rows = [row for unit in paired for row in unit]
    output = []
    for strategy in STRATEGIES:
        subset = [row for row in rows if row["strategy"] == strategy]
        n = len(subset)

        def averages(values):
            return round(float(np.mean(values)), 5) if values else None

        rates = {
            str(c): averages([int(row["windows"][i]["valid"]) for row in subset])
            for i, c in enumerate((0, 60, 120))
        }
        metrics = {
            "strategy": strategy,
            "runs": n,
            "window_availability": rates,
            "delta60_availability": averages([int(row["recovery"]["delta60"] is not None) for row in subset]),
            "delta120_availability": averages(
                [int(row["recovery"]["delta120"] is not None) for row in subset]
            ),
            "on_seconds": averages([row["on_seconds"] for row in subset]),
            "energy_estimate_mj": averages([row["energy_estimate_mj"] for row in subset]),
            "budget_error_seconds": averages(
                [row["budget_error_seconds"] for row in subset if row.get("budget_error_seconds") is not None]
            ),
            "budget_quantization_tolerance_seconds": 0.06,
            "same_window_error_bpm": {
                str(c): averages(
                    [
                        row["same_window_error_bpm"][str(c)]
                        for row in subset
                        if row["same_window_error_bpm"][str(c)] is not None
                    ]
                )
                for c in (0, 60, 120)
            },
            "false_valid_rate": {
                str(c): averages(
                    [
                        int(row["false_valid"][str(c)])
                        for row in subset
                        if row.get("evaluable", row["definite_invalid"])[str(c)]
                    ]
                )
                for c in (0, 60, 120)
            },
            "false_valid_counts": {
                str(c): {
                    "numerator": sum(
                        int(row["false_valid"][str(c)])
                        for row in subset
                        if row.get("evaluable", row["definite_invalid"])[str(c)]
                    ),
                    "denominator": sum(
                        int(row.get("evaluable", row["definite_invalid"])[str(c)]) for row in subset
                    ),
                }
                for c in (0, 60, 120)
            },
            "deterministic_false_valid_rate": {
                str(c): averages(
                    [int(row["false_valid"][str(c)]) for row in subset if row["definite_invalid"][str(c)]]
                )
                for c in (0, 60, 120)
            },
            "deterministic_invalid_counts": {
                str(c): sum(int(row["definite_invalid"][str(c)]) for row in subset) for c in (0, 60, 120)
            },
            "accepted_error_rate": {
                str(c): averages(
                    [
                        int(row.get("inaccurate_valid", row["false_valid"])[str(c)])
                        for row in subset
                        if row["same_window_error_bpm"][str(c)] is not None
                    ]
                )
                for c in (0, 60, 120)
            },
            "accepted_error_counts": {
                str(c): {
                    "numerator": sum(
                        int(row.get("inaccurate_valid", row["false_valid"])[str(c)])
                        for row in subset
                        if row["same_window_error_bpm"][str(c)] is not None
                    ),
                    "denominator": sum(
                        int(row["same_window_error_bpm"][str(c)] is not None) for row in subset
                    ),
                }
                for c in (0, 60, 120)
            },
            "error_tolerance_bpm": 10,
        }
        output.append(metrics)
    expected = int(expected_units) if expected_units is not None else len(groups)
    return {
        "paired_units": len(paired),
        "excluded_units": max(0, expected - len(paired)),
        "strategies": output,
        "energy_label": "估算",
        "complete_strategy_runs": len(rows),
    }
