"""Deterministic synthetic observations. Ground truth is deliberately not in sample rows.

The noise is indexed by seed and sample number, so chunking and acquisition strategy do
not change the immutable optical input. This is an engineering simulator, not human data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from backend.algorithms.policies import acquisition_on, matched_periodic_width

SAMPLE_HZ = 100
SAMPLE_US = 10_000
SCENARIOS = {
    "normal": "正常恢复",
    "interference": "关键窗干扰",
    "early": "起点不足",
    "detachment": "佩戴脱落",
    "gap": "丢样",
    "timeout": "传感器超时",
}
SCENARIO_ALIASES = {
    "normal_recovery": "normal",
    "key_interference": "interference",
    "insufficient_history": "early",
    "dropout": "gap",
    "sensor_timeout": "timeout",
    "no_contact": "detachment",
}
FAULT_ALIASES = {
    "white_noise": "noise",
    "spikes": "spike",
    "baseline_drift": "drift",
    "motion_interference": "motion",
    "periodic_motion": "motion",
    "no_contact": "detachment",
    "sample_loss": "gap",
    "dropout": "gap",
    "overflow": "gap",
    "sensor_timeout": "timeout",
    "flat": "flatline",
    "clipping": "saturation",
}


def _noise(indices: np.ndarray, seed: int, salt: int = 0) -> np.ndarray:
    """Stateless pseudo-random normal values, stable across blocks and policies."""
    z = indices.astype(np.uint64) + np.uint64((seed + salt * 104729) & 0xFFFFFFFF)
    z = (z ^ (z >> np.uint64(16))) * np.uint64(0x45D9F3B)
    z = (z ^ (z >> np.uint64(16))) * np.uint64(0x45D9F3B)
    u = ((z ^ (z >> np.uint64(16))) & np.uint64(0xFFFFFFFF)).astype(np.float64) / 4294967296.0
    w = z * np.uint64(1664525) + np.uint64(1013904223)
    v = (w & np.uint64(0xFFFFFFFF)).astype(np.float64) / 4294967296.0
    return np.sqrt(-2.0 * np.log(np.maximum(u, 1e-12))) * np.cos(2.0 * np.pi * v)


def latent_pulse(seconds: np.ndarray | float, t0_seconds: float = 6, config: dict | None = None):
    cfg = config or {}
    resting, peak, tau = (
        float(cfg.get("resting_bpm", 72)),
        float(cfg.get("peak_bpm", 150)),
        float(cfg.get("tau_seconds", 48)),
    )
    return resting + (peak - resting) * np.exp(-np.maximum(np.asarray(seconds) - t0_seconds, 0) / tau)


def _preset_faults(scenario: str) -> list[dict]:
    plans = {
        "interference": [
            {
                "type": "motion",
                "anchor": "t0",
                "start_offset_us": 55_000_000,
                "duration_us": 10_000_000,
                "params": {"amplitude": 7000, "frequency": 2.0},
            }
        ],
        "detachment": [
            {
                "type": "detachment",
                "anchor": "t0",
                "start_offset_us": 55_000_000,
                "duration_us": 12_000_000,
                "params": {},
            }
        ],
        "gap": [
            {
                "type": "gap",
                "anchor": "t0",
                "start_offset_us": 59_000_000,
                "duration_us": 2_000_000,
                "params": {},
            }
        ],
        "timeout": [
            {
                "type": "timeout",
                "anchor": "t0",
                "start_offset_us": 55_000_000,
                "duration_us": 12_000_000,
                "params": {},
            }
        ],
    }
    return plans.get(scenario, [])


class Source:
    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.seed = int(self.config.get("seed", 20261025))
        self.strategy = self.config.get("strategy", "critical")
        self.scenario = SCENARIO_ALIASES.get(
            self.config.get("scenario", "normal"), self.config.get("scenario", "normal")
        )
        self.phase = float(self.config.get("phase", 0))
        self.width = float(self.config.get("periodic_width", matched_periodic_width(self.phase)))
        self.segment_id = 0
        self._was_available = False
        self._last_time_us: int | None = None
        self._preset = _preset_faults(self.scenario)

    def _fault_active(self, plan: dict, t: int, t0_us: int | None) -> bool:
        if plan.get("status") in {"REVOKED", "revoke", "CANCELLED"}:
            return False
        anchor = plan.get("anchor", "t0")
        if plan.get("actual_start_us") is not None:
            start = int(plan["actual_start_us"])
        elif plan.get("start_us") is not None:
            start = int(plan["start_us"])
        elif anchor in {"t0", "recovery", "T0", "after_t0"}:
            if t0_us is None:
                return False
            start = int(t0_us) + int(
                plan.get(
                    "start_offset_us", float(plan.get("offset_seconds", plan.get("start_seconds", 0))) * 1e6
                )
            )
        else:
            start = int(
                plan.get(
                    "start_offset_us", float(plan.get("offset_seconds", plan.get("start_seconds", 0))) * 1e6
                )
            )
        end = start + int(
            plan.get("duration_us", float(plan.get("duration_seconds", plan.get("duration", 5))) * 1e6)
        )
        if plan.get("stopped_at_us") is not None:
            end = min(end, int(plan["stopped_at_us"]))
        return start <= t < end

    def block(
        self, start_us: int, count: int = 10, t0_us: int | None = None, faults: list[dict] | None = None
    ) -> list[dict[str, Any]]:
        if count < 0 or count > 200_000:
            raise ValueError("invalid block length")
        if start_us < 0:
            raise ValueError("negative engine timestamp")
        stamps = int(start_us) + np.arange(count, dtype=np.int64) * SAMPLE_US
        indices = stamps // SAMPLE_US
        seconds = stamps / 1e6
        peak = float(self.config.get("peak_bpm", 150))
        rest = float(self.config.get("resting_bpm", 72))
        tau = float(self.config.get("tau_seconds", 48))
        if tau <= 0:
            raise ValueError("tau_seconds must be positive")
        marker = t0_us / 1e6 if t0_us is not None else math.inf
        elapsed = np.maximum(seconds - marker, 0)
        before = np.minimum(seconds, marker)
        phase = (
            2
            * np.pi
            / 60
            * (peak * before + rest * elapsed + (peak - rest) * tau * (-np.expm1(-elapsed / tau)))
        )
        amplitude = float(self.config.get("ppg_amplitude", 4500))
        ppg = 60000 + amplitude * (np.sin(phase) + 0.22 * np.sin(2 * phase + 0.35))
        ppg += 150 * np.sin(2 * np.pi * 0.15 * seconds) + float(self.config.get("noise_std", 55)) * _noise(
            indices, self.seed
        )
        accel = 0.025 + 0.008 * np.sin(2 * np.pi * 0.7 * seconds)
        plans = list(self._preset) + list(faults or [])
        output = []
        for n, stamp in enumerate(stamps):
            t = int(stamp)
            relative = None if t0_us is None else (t - t0_us) / 1e6
            on = acquisition_on(relative, self.strategy, self.phase, self.width)
            value = float(ppg[n])
            motion = float(accel[n])
            gap = False
            driver_fault = None
            active = [p for p in plans if self._fault_active(p, t, t0_us)]
            # Signal composition is explicit and deterministic; labels stay outside DSP.
            for kind in (
                "drift",
                "motion",
                "noise",
                "spike",
                "detachment",
                "flatline",
                "saturation",
                "timeout",
                "gap",
            ):
                for plan in active:
                    if FAULT_ALIASES.get(plan.get("type"), plan.get("type")) != kind:
                        continue
                    params = plan.get("params") or {}
                    strength = float(
                        params.get(
                            "amplitude",
                            plan.get(
                                "amplitude", {"noise": 2500, "motion": 6500, "drift": 6000}.get(kind, 1)
                            ),
                        )
                    )
                    freq = float(params.get("frequency", params.get("frequency_hz", 2)))
                    if not math.isfinite(freq) or not 0 < freq <= 50:
                        raise ValueError("fault frequency must be in (0,50] Hz")
                    if not math.isfinite(strength) or not 0 <= strength <= 262143:
                        raise ValueError("fault amplitude must be finite in [0,262143]")
                    if kind == "drift":
                        value += strength * math.sin(
                            2 * math.pi * float(params.get("frequency", 0.12)) * seconds[n]
                        )
                    elif kind == "motion":
                        value += strength * math.sin(2 * math.pi * freq * seconds[n])
                        if params.get("with_accel", True):
                            motion += 1.2 * abs(math.sin(2 * math.pi * freq * seconds[n]))
                    elif kind == "noise":
                        value += strength * float(_noise(indices[n : n + 1], self.seed, 7)[0])
                    elif kind == "spike":
                        if int(indices[n]) % max(1, int(100 / freq)) == 0:
                            value += float(params.get("amplitude", 40000))
                    elif kind == "detachment":
                        value = 800 + 12 * float(_noise(indices[n : n + 1], self.seed, 12)[0])
                    elif kind == "flatline":
                        value = float(params.get("value", 60000))
                    elif kind == "saturation":
                        value = min(
                            float(params.get("clip_max", 61000)),
                            max(float(params.get("clip_min", 59000)), value),
                        )
                    elif kind == "timeout":
                        driver_fault = "SENSOR_FAULT"
                    elif kind == "gap":
                        gap = True
            available = on and not gap and driver_fault is None
            if available and (
                not self._was_available or self._last_time_us is None or t - self._last_time_us != SAMPLE_US
            ):
                self.segment_id += 1
            self._was_available = available
            self._last_time_us = t
            output.append(
                {
                    "time_us": t,
                    "seq": int(indices[n]),
                    "segment_id": self.segment_id,
                    "ppg": round(min(262143.0, max(0.0, value)), 5) if available else None,
                    "accel": round(max(0, motion), 6),
                    "on": bool(on),
                    "gap": bool(gap),
                    "fault": driver_fault,
                }
            )
        return output


SampleSource = Source
