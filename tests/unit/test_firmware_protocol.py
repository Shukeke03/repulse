"""Independent C DSP and actual Cortex-M3 FreeRTOS smoke verification."""

import asyncio
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.algorithms.processor import Processor
from backend.sources.simulator import Source

ROOT = Path(__file__).resolve().parents[2]


class Sample(ctypes.Structure):
    _fields_ = [
        ("time_us", ctypes.c_uint32),
        ("seq", ctypes.c_uint32),
        ("segment", ctypes.c_uint16),
        ("on", ctypes.c_uint8),
        ("gap", ctypes.c_uint8),
        ("ppg", ctypes.c_float),
        ("accel", ctypes.c_float),
    ]


class Result(ctypes.Structure):
    _fields_ = [
        ("available_us", ctypes.c_uint32),
        ("start_us", ctypes.c_uint32),
        ("end_us", ctypes.c_uint32),
        ("valid", ctypes.c_int),
        ("reason", ctypes.c_int),
        ("pulse_bpm", ctypes.c_float),
    ]


@pytest.fixture(scope="module")
def c_dsp(tmp_path_factory):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("Host C compiler unavailable")
    binary = tmp_path_factory.mktemp("c_dsp") / "dsp.dylib"
    subprocess.run(
        [
            cc,
            "-shared",
            "-fPIC",
            "-O2",
            str(ROOT / "firmware/common/dsp.c"),
            "-I" + str(ROOT / "firmware/common"),
            "-lm",
            "-o",
            str(binary),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(binary))
    lib.dsp_feed.argtypes = [ctypes.POINTER(Sample)]
    lib.dsp_estimate.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    lib.dsp_estimate.restype = Result
    return lib


@pytest.mark.parametrize("scenario", ["normal", "gap", "interference", "detachment", "timeout"])
def test_c_and_python_same_observations(c_dsp, scenario):
    """Both implementations receive identical observed data, including gaps/restarts."""
    c_dsp.dsp_reset()
    source, python = Source({"scenario": scenario, "seed": 20261025}), Processor()
    t0 = 6_000_000
    checked = []
    for start in range(0, 130_000_000, 100_000):
        rows = source.block(start, 10, t0)
        python.feed(rows)
        for row in rows:
            native = Sample(
                row["time_us"],
                row["seq"],
                row["segment_id"],
                int(row["on"]),
                int(row["gap"] or row.get("fault") is not None),
                row["ppg"] or 0,
                row["accel"],
            )
            c_dsp.dsp_feed(ctypes.byref(native))
        end = start + 100_000
        for center in (0, 60, 120):
            if end == t0 + (center + 4) * 1_000_000:
                expected = python.window(center, t0, end)
                actual = c_dsp.dsp_estimate(t0 + (center - 4) * 1_000_000, end, end)
                assert bool(actual.valid) == expected["valid"], (scenario, center, actual.reason, expected)
                if actual.valid:
                    assert abs(actual.pulse_bpm - expected["pulse_bpm"]) <= 2
                checked.append(center)
    assert checked == [0, 60, 120]


def test_actual_qemu_static_tasks_and_commands(tmp_path):
    from backend.domain.models import DEFAULTS
    from backend.engines.firmware_port import FirmwarePort, availability
    from backend.storage.store import Store

    if not availability():
        pytest.skip("Build QEMU to run integration smoke test")

    async def verify():
        store = Store(tmp_path / "data")
        config = {
            "name": "QEMU smoke",
            "execution_mode": "FREERTOS_QEMU",
            "scenario": "normal",
            "strategy": "critical",
            "speed": 1,
            "seed": 20261025,
        }
        port = FirmwarePort(store, config, DEFAULTS)
        try:
            assert port.hello["kernel"] == "FreeRTOS V11.2.0"
            started = port.command("start_prepare", 0)
            assert started["session_state"] == "PREPARING"
            await asyncio.sleep(0.6)
            snap = port.snapshot()
            assert snap["diagnostics"]["rtos"]
            assert len(snap["diagnostics"]["tasks"]) == 6
            assert all(t["stack_high_water_words"] > 0 for t in snap["diagnostics"]["tasks"])
            assert len(port.samples) > 0
            assert all(s["filtered"] is not None for s in port.samples if s["on"] and not s["gap"])
            reference_source = Source({"seed": 20261025, "strategy": "critical"})
            reference = reference_source.block(0, min(20, len(port.samples)), None)
            assert all(abs(a["ppg"] - b["ppg"]) < 0.05 for a, b in zip(port.samples, reference))
            assert snap["diagnostics"]["raw_drops"] == 0
            marked = port.command("mark_end", port.revision)
            assert marked["session_state"] == "RECOVERING"
            assert marked["t0_us"] is not None
            port.command("cancel", port.revision)
            await asyncio.sleep(0.2)
            assert port.state == "CANCELLED"
            assert port.writer.closed
            assert (store.session_dir(port.id) / "manifest.json").exists()
        finally:
            await port.close()
            store.close()

    asyncio.run(verify())


def test_qemu_rejects_untransmitted_custom_parameters(tmp_path):
    from backend.domain.models import DEFAULTS
    from backend.engines.firmware_port import FirmwarePort, availability
    from backend.engines.session import DomainError
    from backend.storage.store import Store

    if not availability():
        pytest.skip("QEMU unavailable")
    store = Store(tmp_path)
    try:
        with pytest.raises(DomainError, match="固定算法参数"):
            FirmwarePort(
                store,
                {"scenario": "normal", "strategy": "critical", "speed": 1},
                {**DEFAULTS, "stale_seconds": 3},
            )
        assert not store.all()
    finally:
        store.close()
