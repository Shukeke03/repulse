"""Verify real observations through Cortex-M3 FreeRTOS, without host-side live DSP."""

import argparse
import asyncio
import hashlib
import json
import math
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.algorithms.processor import Processor
from backend.domain.models import DEFAULT_DATASET, DEFAULTS, TERMINAL
from backend.engines.firmware_port import ELF, FirmwarePort
from backend.sources.public_data import DatasetSource
from backend.storage.store import Store, atomic_json


def offline_reference(samples, t0_us, config):
    """Recompute only for independent verification after firmware publication."""
    processor = Processor(config)
    expected = {}
    period_us = 15_625
    for offset in range(0, len(samples), 16):
        block = [dict(sample) for sample in samples[offset : offset + 16]]
        processor.feed(block)
        end = block[-1]["time_us"] + period_us
        for center in (0, 60, 120):
            if center not in expected and end >= t0_us + (center + 4) * 1_000_000:
                expected[center] = processor.window(center, t0_us, end)
    return expected


def check_observations(actual, expected):
    """Validate masking and native float32 transfer against the selected real file."""
    if len(actual) != len(expected):
        raise AssertionError(("sample count", len(actual), len(expected)))

    def to_float32(value):
        return struct.unpack("<f", struct.pack("<f", value))[0]

    for row, source in zip(actual, expected):
        assert row["time_us"] == source["time_us"]
        assert row["seq"] == source["seq"]
        assert row["on"] == source["on"]
        assert row["gap"] == source["gap"]
        if source["ppg"] is None:
            assert row["ppg"] is None
        else:
            assert row["ppg"] == to_float32(source["ppg"])
        if source["accel"] is not None:
            assert row["accel"] == to_float32(source["accel"])


async def verify_real_record(
    directory, dataset_id="wearable-aerobic-s02", strategy="continuous", speed=10, phase=0
):
    started = time.monotonic()
    store = Store(directory)
    config = {
        "name": "FreeRTOS真实记录验证",
        "origin": "PUBLIC_DATA",
        "execution_mode": "FREERTOS_QEMU",
        "dataset_id": dataset_id,
        "strategy": strategy,
        "speed": speed,
        "phase": phase,
    }
    port = FirmwarePort(store, config, DEFAULTS)
    try:
        port.command("start_prepare", 0)
        deadline = time.monotonic() + max(60, 300 / speed)
        while port.state not in TERMINAL:
            await asyncio.sleep(0.05)
            if time.monotonic() > deadline:
                raise TimeoutError(f"QEMU超时，state={port.state} time_us={port.time_us} error={port._error}")
        assert port.state == "COMPLETED", port.snapshot()
        assert port.t0_us == port.source.t0_us
        assert port.time_us == port.source.duration_us
        source = DatasetSource(config)
        expected_rows = source.block(0, source.duration_us // source.period_us)
        check_observations(port.samples, expected_rows)
        reference = offline_reference(port.samples, port.t0_us, port.config)
        comparisons = []
        for center in (0, 60, 120):
            actual = next(window for window in port.windows if window["center_s"] == center)
            python = reference[center]
            assert actual["valid"] == python["valid"], (center, actual, python)
            if actual["valid"]:
                assert math.isclose(actual["pulse_bpm"], python["pulse_bpm"], abs_tol=0.05), (
                    center,
                    actual,
                    python,
                )
            comparisons.append({"center_s": center, "firmware": actual, "python_offline": python})
        diagnostics = port.firmware_diagnostics
        assert diagnostics["raw_drops"] == diagnostics["telemetry_drops"] == 0
        assert diagnostics["source_rows"] == diagnostics["output_rows"] == len(port.samples)
        assert diagnostics["source_crc32"] == port.input_manifest["payload_crc32"]
        assert diagnostics["output_crc32"] == port._output_crc
        assert len(port.firmware_tasks) == 6
        assert all(task["stack_high_water_words"] > 0 for task in port.firmware_tasks)
        saved = store.get(port.id)
        assert saved["origin"] == "PUBLIC_DATA" and saved["status"] == "COMPLETED"
        report = {
            "status": "PASS",
            "target": "mps2-an385",
            "kernel": port.hello["kernel"],
            "origin": "PUBLIC_DATA",
            "dataset_id": dataset_id,
            "strategy": strategy,
            "phase": phase,
            "playback_speed": speed,
            "state": port.state,
            "t0_us": port.t0_us,
            "recording_duration_seconds": port.source.duration_us / 1e6,
            "recovery_duration_seconds": 124,
            "wall_duration_seconds": round(time.monotonic() - started, 3),
            "sample_count": len(port.samples),
            "sample_hz": 64,
            "input": port.input_manifest,
            "dataset": port.source.metadata,
            "firmware_sha256": hashlib.sha256(ELF.read_bytes()).hexdigest(),
            "comparisons": comparisons,
            "tasks": port.firmware_tasks,
            "diagnostics": diagnostics,
            "transport": "versioned semihosted input/output with CRC32; UART control only",
            "clock": "Original recording timestamps; playback pacing and host duration are separate",
            "reference_note": "Python重新处理固件实际输出的相同观测，只用于离线一致性验证，不替代固件在线结果。HR.csv不是独立ECG参考。",
        }
        return report
    finally:
        await port.close()
        store.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-id", "--dataset", dest="dataset", default=DEFAULT_DATASET)
    parser.add_argument("--strategy", default="continuous", choices=["continuous", "critical", "periodic"])
    parser.add_argument("--speed", type=float, default=10)
    parser.add_argument("--phase", type=int, default=0)
    parser.add_argument("--output", default="docs/verification/qemu-real-data.json")
    args = parser.parse_args()
    # Temporary verification data stay inside the project and are cleaned up.
    temporary_root = Path(__file__).resolve().parents[1] / "data/verification"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qemu-", dir=temporary_root) as directory:
        report = await verify_real_record(directory, args.dataset, args.strategy, args.speed, args.phase)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, report)
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"tasks", "comparisons", "dataset"}},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
