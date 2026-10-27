"""Native64Hz real-record C/Python parity and actual FreeRTOS ingestion checks."""

import asyncio
import ctypes
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from backend.algorithms.processor import Processor
from backend.domain.models import DEFAULTS
from backend.engines.firmware_port import FirmwarePort, availability, write_observations
from backend.engines.session import DomainError
from backend.sources.public_data import DatasetSource, get_dataset
from backend.storage.store import Store
from scripts.verify_qemu import verify_real_record

ROOT = Path(__file__).resolve().parents[2]


# ctypes 字段顺序和类型需与 dsp.h 完全一致，直接调用同一份 C 算法。
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


# 测试仅读取已下载的公开记录，缺失时跳过，不生成替代数据。
def real_source(dataset_id="wearable-aerobic-s02", **config):
    try:
        get_dataset(dataset_id)
    except (ValueError, OSError) as exc:
        pytest.skip(f"先运行 python -m scripts.download_dataset：{exc}")
    return DatasetSource({"dataset_id": dataset_id, **config})


# 将项目 DSP 编译为本机动态库，独立于 ARM 固件进程检查算法一致性。
@pytest.fixture(scope="module")
def c_dsp(tmp_path_factory):
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("本机缺少C编译器")
    binary = tmp_path_factory.mktemp("c-dsp") / "dsp.dylib"
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
    lib.dsp_estimate.argtypes = [ctypes.c_uint32] * 3
    lib.dsp_estimate.restype = Result
    return lib


# 同一真实记录分别应用三种策略，对三个固定窗口比较有效性及有效脉率。
@pytest.mark.parametrize("dataset_id", ["wearable-aerobic-s01", "wearable-aerobic-s02"])
@pytest.mark.parametrize("strategy", ["continuous", "critical", "periodic"])
def test_native_c_and_python_process_identical_real_observations(c_dsp, dataset_id, strategy):
    source = real_source(dataset_id, strategy=strategy)
    python = Processor()
    c_dsp.dsp_reset()
    checked = []
    for start in range(0, source.duration_us, 16 * source.period_us):
        rows = source.block(start, 16)
        python.feed(rows)
        for row in rows:
            native = Sample(
                row["time_us"],
                row["seq"],
                row["segment_id"],
                int(row["on"]),
                int(row["gap"]),
                row["ppg"] or 0,
                row["accel"] or 0,
            )
            c_dsp.dsp_feed(ctypes.byref(native))
        end = start + len(rows) * source.period_us
        for center in (0, 60, 120):
            if end == source.t0_us + (center + 4) * 1_000_000:
                expected = python.window(center, source.t0_us, end)
                actual = c_dsp.dsp_estimate(source.t0_us + (center - 4) * 1_000_000, end, end)
                assert bool(actual.valid) == expected["valid"], (
                    dataset_id,
                    strategy,
                    center,
                    actual.reason,
                    expected,
                )
                if actual.valid:
                    assert abs(actual.pulse_bpm - expected["pulse_bpm"]) <= 0.05
                checked.append(center)
    assert checked == [0, 60, 120]


# 核对二进制头部、原生 64 Hz 样本、策略参数和头部/载荷双重校验。
def test_versioned_real_input_has_exact_native_samples_and_crc(tmp_path):
    source = real_source(strategy="periodic", phase=5)
    path = tmp_path / "observations.bin"
    manifest = write_observations(path, source, {"strategy": "periodic", "phase": 5, "speed": 10})
    data = path.read_bytes()
    values = struct.unpack_from("<8s11I", data)
    assert values[0] == b"RPLREAL2" and values[1:3] == (2, 64)
    assert values[3] == 8576 and values[4] == 10_000_000 and values[5] == 134_000_000
    assert values[6] == zlib.crc32(data[52:]) == manifest["payload_crc32"]
    assert values[7] == 2 and values[8] == 5_000_000 and values[10] == 10_000
    assert values[11] == zlib.crc32(data[:48])
    first = struct.unpack_from("<IIffII", data, 52)
    expected = source.all_samples[0]
    assert first[:2] == (0, 0)
    assert abs(first[2] - expected["ppg"]) < 1e-3
    assert manifest["count"] == len(source.all_samples)


# 短流程验证真实内核任务、不可手改的恢复起点，以及取消后的资源回收。
def test_actual_qemu_kernel_real_input_and_safe_cancel(tmp_path):
    source = real_source()
    if not availability():
        pytest.skip("先构建QEMU目标并安装QEMU")

    async def check():
        store = Store(tmp_path / "data")
        cfg = {
            "name": "真实记录短流程",
            "origin": "PUBLIC_DATA",
            "dataset_id": source.metadata["id"],
            "execution_mode": "FREERTOS_QEMU",
            "strategy": "continuous",
            "speed": 5,
        }
        port = FirmwarePort(store, cfg, DEFAULTS)
        try:
            assert port.hello["protocol_version"] == 2 and port.hello["origin"] == "PUBLIC_DATA"
            assert port.command("start_prepare", 0)["session_state"] == "PREPARING"
            await asyncio.sleep(0.6)
            snapshot = port.snapshot()
            assert snapshot["origin"] == "PUBLIC_DATA" and snapshot["diagnostics"]["rtos"]
            assert len(snapshot["diagnostics"]["tasks"]) == 6
            assert all(task["stack_high_water_words"] > 0 for task in snapshot["diagnostics"]["tasks"])
            assert port.samples
            assert all(row["time_us"] < snapshot["engine_time_us"] for row in snapshot["waveform_tail"])
            assert (
                snapshot["latest_result"]["data_age_s"] is None
                or snapshot["latest_result"]["data_age_s"] >= 0
            )
            assert abs(port.samples[0]["ppg"] - source.all_samples[0]["ppg"]) < 1e-3
            with pytest.raises(DomainError, match="不能手动更改"):
                port.command("mark_end", port.revision)
            port.command("cancel", port.revision)
            await asyncio.sleep(0.1)
            assert port.state == "CANCELLED" and port.writer.closed
            assert (store.session_dir(port.id) / "manifest.json").exists()
            assert port.firmware_diagnostics["raw_drops"] == 0
        finally:
            await port.close()
            assert not port.transport_dir.exists()
            store.close()

    asyncio.run(check())


# 固件未支持动态算法参数时必须拒绝，避免界面配置与实际处理不一致。
def test_qemu_rejects_untransmitted_custom_algorithm_settings(tmp_path):
    if not availability():
        pytest.skip("QEMU不可用")
    store = Store(tmp_path)
    try:
        with pytest.raises(DomainError, match="固定算法参数"):
            FirmwarePort(store, {}, {**DEFAULTS, "motion_threshold": 0.8})
        assert not store.all()
    finally:
        store.close()


# 三种策略都执行完整记录，检查 C 发布结果和独立离线参考。
@pytest.mark.parametrize("strategy", ["continuous", "critical", "periodic"])
def test_actual_qemu_full_real_record_and_offline_reference(tmp_path, strategy):
    real_source()
    if not availability():
        pytest.skip("先构建QEMU目标并安装QEMU")
    report = asyncio.run(verify_real_record(tmp_path / strategy, "wearable-aerobic-s02", strategy, 10))
    assert report["status"] == "PASS" and report["sample_count"] == 8576
    assert report["recovery_duration_seconds"] == 124
    assert report["diagnostics"]["raw_drops"] == 0
    if strategy in {"continuous", "critical"}:
        assert report["comparisons"][1]["firmware"]["valid"]
        assert report["comparisons"][2]["firmware"]["valid"]


# 主动破坏输入头，确认在建立会话档案前报错，并清理传输目录。
def test_actual_qemu_rejects_corrupt_input_header_before_creating_record(tmp_path, monkeypatch):
    real_source()
    if not availability():
        pytest.skip("先构建QEMU目标并安装QEMU")
    import backend.engines.firmware_port as module

    original_writer = module.write_observations

    def corrupt_header(path, source, config):
        manifest = original_writer(path, source, config)
        contents = bytearray(path.read_bytes())
        contents[0] ^= 1
        path.write_bytes(contents)
        return manifest

    monkeypatch.setattr(module, "write_observations", corrupt_header)
    store = Store(tmp_path)
    try:
        with pytest.raises(DomainError, match="输入格式或校验不一致"):
            FirmwarePort(
                store, {"dataset_id": "wearable-aerobic-s02", "strategy": "continuous", "speed": 10}, DEFAULTS
            )
        assert store.all() == []
        assert list((tmp_path / "transports").iterdir()) == []
    finally:
        store.close()
