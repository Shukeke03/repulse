"""Run a complete authoritative QEMU recovery and compare archived observations offline."""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.algorithms.processor import Processor
from backend.domain.models import DEFAULTS, TERMINAL
from backend.engines.firmware_port import FirmwarePort
from backend.storage.store import Store, atomic_json


async def main():
    started = time.monotonic()
    directory = Path(tempfile.mkdtemp(prefix="repulse-qemu-verification-"))
    store = Store(directory)
    config = {
        "name": "QEMU full-cycle verification",
        "execution_mode": "FREERTOS_QEMU",
        "scenario": "normal",
        "strategy": "critical",
        "speed": 1,
        "seed": 20261025,
    }
    port = FirmwarePort(store, config, DEFAULTS)
    try:
        port.command("start_prepare", 0)
        while port.time_us < 6_100_000:
            await asyncio.sleep(0.05)
        port.command("mark_end", port.revision)
        print("QEMU t0 locked", port.t0_us, flush=True)
        milestone = 30
        while port.state not in TERMINAL:
            await asyncio.sleep(0.1)
            if port.time_us - (port.t0_us or 0) > milestone * 1e6:
                print("QEMU recovery", milestone, "s", flush=True)
                milestone += 30
            if time.monotonic() - started > 300:
                raise TimeoutError("QEMU full-cycle exceeded300seconds")
        processor = Processor()
        expected = {}
        for sample in port.samples:
            processor.feed([dict(sample)])
            support = sample["time_us"] + 10000
            for center in (0, 60, 120):
                if center not in expected and support >= port.t0_us + (center + 4) * 1_000_000:
                    expected[center] = processor.window(center, port.t0_us, support)
        comparisons = []
        for center in (0, 60, 120):
            actual = next(w for w in port.windows if w["center_s"] == center)
            py = expected[center]
            assert actual["valid"] == py["valid"], (center, actual, py)
            if actual["valid"]:
                assert abs(actual["pulse_bpm"] - py["pulse_bpm"]) <= 2, (center, actual, py)
            comparisons.append({"center_s": center, "firmware": actual, "python_offline": py})
        assert port.state == "COMPLETED"
        assert port.firmware_diagnostics["raw_drops"] == 0
        assert port.firmware_diagnostics["telemetry_drops"] == 0
        report = {
            "status": "PASS",
            "verification_clock": "Default QEMU virtual clock; full 124 guest seconds, wall time recorded separately",
            "target": "mps2-an385",
            "kernel": port.hello["kernel"],
            "state": port.state,
            "t0_us": port.t0_us,
            "sample_count": len(port.samples),
            "duration_seconds": round(time.monotonic() - started, 2),
            "comparisons": comparisons,
            "tasks": port.firmware_tasks,
            "diagnostics": port.firmware_diagnostics,
            "data_directory": str(directory),
        }
        output = Path(__file__).resolve().parents[1] / "docs/verification/qemu-full-cycle.json"
        output.parent.mkdir(exist_ok=True)
        atomic_json(output, report)
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"tasks", "comparisons"}}, ensure_ascii=False
            ),
            flush=True,
        )
    finally:
        await port.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
