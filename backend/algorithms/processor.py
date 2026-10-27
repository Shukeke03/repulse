"""Streaming causal PPG processing with explicit data-support and quality reasons.

The processor accepts native-rate, signed BVP observations. Quality thresholds are
engineering defaults in the source units, not clinical cutoffs. Reference-device
HR values are deliberately never consumed. SOS state is preserved across blocks.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy.signal import butter, find_peaks, sosfilt, sosfilt_zi

ALGORITHM_VERSION = "repulse-dsp-2.0"
DEFAULT_CONFIG = {
    "band_low": 0.5,
    "band_high": 5.0,
    "sample_hz": 64,
    "warmup_seconds": 2.0,
    "window_seconds": 8.0,
    "min_signal_std": 0.05,
    "motion_threshold": 0.35,
    "ibi_cv_limit": 0.18,
    "autocorrelation_min": 0.45,
}


class Processor:
    def __init__(self, config: dict | None = None):
        supplied = config or {}
        self.config = {**DEFAULT_CONFIG, **supplied}
        self.sample_hz = int(supplied.get("sample_hz", supplied.get("sample_rate", 64)))
        if self.sample_hz not in {64, 100}:
            raise ValueError("supported sample rates are 64 Hz and 100 Hz")
        self.config["sample_hz"] = self.sample_hz
        self.period_us = 1_000_000 // self.sample_hz
        self.window_samples = self.sample_hz * 8
        self.min_interval = max(1, int(np.floor(self.sample_hz * 60 / 200)))
        self.max_interval = int(np.ceil(self.sample_hz * 60 / 40))
        low = float(self.config.get("band_low", 0.5))
        high = float(self.config.get("band_high", 5))
        if not 0 < low < high < self.sample_hz / 2:
            raise ValueError("filter bounds must lie between zero and the Nyquist frequency")
        self.sos = butter(2, [low, high], fs=self.sample_hz, btype="bandpass", output="sos")
        self._zi: np.ndarray | None = None
        self.samples: list[dict[str, Any]] = []
        self._last_time: int | None = None
        self._last_seq: int | None = None
        self._last_segment: Any = None
        self._segment_start: int | None = None
        self._continuity = False
        self._generation = 0

    def feed(self, samples: list[dict]) -> None:
        """Feed chronological observations; add filtered and warming to archived rows."""
        if not samples:
            return
        runs: list[tuple[list[dict], bool]] = []
        current: list[dict] = []
        for sample in samples:
            t = int(sample["time_us"])
            seq = int(sample.get("seq", t // self.period_us))
            segment = sample.get("segment_id", 1)
            if self._last_time is not None and t <= self._last_time:
                raise ValueError("sample times must be strictly increasing")
            available = (
                bool(sample.get("on", True))
                and sample.get("ppg") is not None
                and not sample.get("gap")
                and not sample.get("fault")
            )
            consecutive = (
                self._continuity
                and self._last_time is not None
                and t - self._last_time == self.period_us
                and seq - int(self._last_seq) == 1
                and segment == self._last_segment
            )
            if not available or not consecutive:
                if current:
                    runs.append((current, False))
                    current = []
                self._segment_start = t if available else None
                self._generation += 1
                # Defer state-reset to filtering below; per-run reset marker is stored.
            sample["filtered"] = None
            sample["warming"] = bool(
                available
                and self._segment_start is not None
                and t - self._segment_start < int(float(self.config["warmup_seconds"]) * 1e6)
            )
            sample["_dsp_segment"] = self._generation
            if available:
                current.append(sample)
            else:
                runs.append(([sample], True))
            self._last_time, self._last_seq, self._last_segment = t, seq, segment
            self._continuity = available
            self.samples.append(sample)
        if current:
            runs.append((current, False))
        # Process each physically continuous run independently. Preserve SOS states
        # only when the immediately preceding processed run has the same generation.
        for run, invalid in runs:
            generation = run[0]["_dsp_segment"]
            if invalid:
                self._zi = None
                self._filtered_generation = generation
                continue
            x = np.asarray([float(row["ppg"]) for row in run], dtype=np.float64)
            if self._zi is None or getattr(self, "_filtered_generation", None) != generation:
                self._zi = sosfilt_zi(self.sos) * x[0]
            y, self._zi = sosfilt(self.sos, x, zi=self._zi)
            self._filtered_generation = generation
            for row, value in zip(run, y):
                row["filtered"] = round(float(value), 8)
        # Continuity generation is internal bookkeeping, never an external label.

    def _result(self, start_us: int, end_us: int, available_us: int) -> dict:
        result: dict = {
            "valid": False,
            "pulse_bpm": None,
            "reasons": [],
            "reason_codes": [],
            "quality": "UNKNOWN",
            "state": "MISSING",
            "status": "MISSING",
            "start_us": start_us,
            "end_us": end_us,
            "support_start_us": start_us,
            "support_end_us": end_us,
            "available_us": available_us,
            "sample_count": 0,
            "segment_ids": [],
            "warmup_start_us": None,
            "algorithm_version": ALGORITHM_VERSION,
            "ibi_count": 0,
            "autocorrelation": None,
        }
        reasons: list[str] = result["reasons"]
        if available_us < end_us:
            result["state"] = result["status"] = "WAITING" if available_us <= start_us else "COLLECTING"
            reasons.append("NOT_READY")
            result["reason_codes"] = list(reasons)
            return result
        rows = [s for s in self.samples if start_us <= int(s["time_us"]) < end_us]
        result["sample_count"] = sum(s.get("ppg") is not None for s in rows)
        result["segment_ids"] = sorted(
            {int(s.get("segment_id", 1)) for s in rows if s.get("ppg") is not None}
        )
        if start_us < 0 or not self.samples or int(self.samples[0]["time_us"]) > start_us:
            reasons.append("INSUFFICIENT_HISTORY")
        if len(rows) != self.window_samples or (
            rows
            and (
                not 0 <= int(rows[0]["time_us"]) - start_us < self.period_us
                or not 0 < end_us - int(rows[-1]["time_us"]) <= self.period_us
            )
        ):
            reasons.append("DATA_GAP")
        if any(s.get("fault") for s in rows):
            reasons.append("SENSOR_FAULT")
        if any(not s.get("on", True) for s in rows):
            reasons.append("ACQUISITION_OFF")
        if any(
            s.get("gap") or s.get("ppg") is None and s.get("on", True) and not s.get("fault") for s in rows
        ):
            reasons.append("DATA_GAP")
        valid_rows = [s for s in rows if s.get("filtered") is not None]
        if rows and (
            len({s.get("_dsp_segment", s.get("segment_id", 1)) for s in valid_rows}) > 1
            or any(
                int(b["time_us"]) - int(a["time_us"]) != self.period_us
                or int(b.get("seq", 0)) - int(a.get("seq", 0)) != 1
                for a, b in zip(rows, rows[1:])
            )
        ):
            reasons.append("DATA_GAP")
        if any(s.get("warming") for s in rows):
            reasons.append("WARMUP")
        if valid_rows:
            generation = valid_rows[0].get("_dsp_segment", valid_rows[0].get("segment_id", 1))
            previous = [
                s
                for s in self.samples
                if s.get("_dsp_segment", s.get("segment_id", 1)) == generation
                and s.get("filtered") is not None
            ]
            if previous:
                result["warmup_start_us"] = int(previous[0]["time_us"])
        if len(valid_rows) != self.window_samples and not reasons:
            reasons.append("DATA_GAP")
        if not reasons:
            raw = np.asarray([float(s["ppg"]) for s in rows])
            y = np.asarray([float(s["filtered"]) for s in rows])
            motion = np.asarray(
                [
                    float(s["accel"])
                    for s in rows
                    if s.get("accel") is not None and np.isfinite(float(s["accel"]))
                ]
            )
            result["motion_available"] = len(motion) == len(rows)
            raw_std = float(np.std(raw))
            filt_std = float(np.std(y))
            contact_threshold = self.config.get("contact_threshold")
            if contact_threshold is not None and float(np.mean(raw)) < float(contact_threshold):
                reasons.append("NO_CONTACT")
            if (
                raw_std < float(self.config["min_signal_std"])
                or filt_std < float(self.config["min_signal_std"]) * 0.4
            ):
                reasons.append("LOW_AMPLITUDE")
            if raw_std < 1e-5:
                reasons.append("FLATLINE")
            minimum, maximum = float(raw.min()), float(raw.max())
            if np.mean(raw == minimum) > 0.08 or np.mean(raw == maximum) > 0.08:
                reasons.append("SATURATED")
            if len(motion) and np.mean(motion > float(self.config["motion_threshold"])) > 0.1:
                reasons.append("MOTION_ARTIFACT")
            if not reasons:
                peaks, _ = find_peaks(y, distance=self.min_interval, prominence=max(1e-5, filt_std * 0.35))
                intervals = np.diff(peaks).astype(float)
                usable = intervals[(intervals >= self.min_interval) & (intervals <= self.max_interval)]
                result["ibi_count"] = int(len(usable))
                if len(usable) < 3 or len(usable) != len(intervals):
                    reasons.append("INSUFFICIENT_INTERVALS")
                else:
                    median = float(np.median(usable))
                    cv = float(np.median(np.abs(usable - median)) / median)
                    result["ibi_variability"] = round(cv, 5)
                    if (
                        cv > float(self.config["ibi_cv_limit"])
                        or np.max(np.abs(usable - median)) / median > 0.35
                    ):
                        reasons.append("IRREGULAR_INTERVALS")
                    centered = y - np.mean(y)
                    correlation = np.correlate(centered, centered, mode="full")[len(y) - 1 :]
                    lo, hi = (
                        max(self.min_interval, int(median * 0.85)),
                        min(self.max_interval, int(median * 1.15) + 1),
                    )
                    if hi <= lo or correlation[0] <= 0:
                        reasons.append("AUTOCORRELATION_MISMATCH")
                    else:
                        lag = lo + int(np.argmax(correlation[lo:hi]))
                        score = float(correlation[lag] / correlation[0])
                        result["autocorrelation"] = round(score, 5)
                        if (
                            score < float(self.config["autocorrelation_min"])
                            or abs(lag - median) / median > 0.15
                        ):
                            reasons.append("AUTOCORRELATION_MISMATCH")
                    if not reasons:
                        bpm = 60.0 * self.sample_hz / median
                        if not 40 <= bpm <= 200:
                            reasons.append("OUT_OF_RANGE")
                        else:
                            result["pulse_bpm"] = round(bpm, 2)
                            result["valid"] = True
                            result["quality"] = "VALID"
                            result["state"] = result["status"] = "VALID"
        result["reasons"] = list(dict.fromkeys(reasons))
        result["reason_codes"] = list(result["reasons"])
        if not result["valid"]:
            if "NO_CONTACT" in reasons:
                result["quality"] = "NO_CONTACT"
            elif "SENSOR_FAULT" in reasons:
                result["quality"] = "SENSOR_FAULT"
            elif "DATA_GAP" in reasons:
                result["quality"] = "DATA_GAP"
            elif "ACQUISITION_OFF" in reasons:
                result["quality"] = "OFF"
            elif "WARMUP" in reasons or "INSUFFICIENT_HISTORY" in reasons:
                result["quality"] = "UNKNOWN"
            else:
                result["quality"] = "POOR_SIGNAL"
        return result

    def estimate(self, end_us: int) -> dict:
        """Current trailing result; OFF and observed loss immediately invalidate it."""
        end_us = int(end_us)
        result = self._result(end_us - 8_000_000, end_us, end_us)
        result["data_age_seconds"] = None
        if self.samples:
            observed = [s for s in self.samples if s.get("ppg") is not None]
            if observed:
                result["data_age_seconds"] = (
                    max(0, end_us - int(observed[-1]["time_us"]) - self.period_us) / 1e6
                )
                if result["data_age_seconds"] > float(self.config.get("stale_seconds", 2)):
                    result["valid"], result["pulse_bpm"], result["quality"] = False, None, "STALE"
                    if "STALE" not in result["reasons"]:
                        result["reasons"].append("STALE")
                        result["reason_codes"] = list(result["reasons"])
        return result

    def window(self, center_seconds: int | float, t0_us: int, available_us: int) -> dict:
        center_us = int(t0_us) + round(float(center_seconds) * 1e6)
        result = self._result(center_us - 4_000_000, center_us + 4_000_000, int(available_us))
        result.update(
            {
                "id": f"window_{int(center_seconds)}",
                "center_seconds": center_seconds,
                "center_us": center_us,
                "nominal_start_us": center_us - 4_000_000,
                "nominal_end_us": center_us + 4_000_000,
            }
        )
        return result


SignalProcessor = Processor
