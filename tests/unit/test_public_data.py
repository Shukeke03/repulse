"""Contracts on the exact downloaded public observations; no network is used by tests."""

import hashlib

import numpy as np
import pytest

from backend.sources.public_data import (
    PERIOD_US,
    RECORD_DEFINITIONS,
    DatasetSource,
    get_dataset,
    list_datasets,
    record_directory,
)


@pytest.fixture
def record():
    try:
        return DatasetSource({"dataset_id": "wearable-aerobic-s02", "strategy": "continuous"})
    except ValueError:
        pytest.skip("Run python -m scripts.download_dataset to download the small real records")


def test_original_files_match_published_sha256(record):
    folder = record_directory(record.metadata["id"])
    for name, expected in RECORD_DEFINITIONS[record.metadata["id"]]["hashes"].items():
        assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == expected
    assert record.metadata["origin"] == "PUBLIC_DATA"
    assert record.metadata["reference_is_independent"] is False


def test_native_sampling_and_verified_phase_marker(record):
    assert record.sample_hz == 64 and record.period_us == 15_625
    assert record.t0_us == 10_000_000
    assert record.duration_us == 134_000_000
    assert len(record.all_samples) == 8576
    assert record.metadata["phase_event"]["tag_number"] == 11
    assert record.metadata["phase_event"]["tag_index_zero_based"] == 10
    assert record.metadata["recovery_type"] == "active"
    assert record.metadata["resampled"] is False
    assert all(
        b["time_us"] - a["time_us"] == PERIOD_US for a, b in zip(record.all_samples, record.all_samples[1:])
    )


def test_values_are_exact_native_bvp_not_a_generated_waveform(record):
    raw = np.genfromtxt(record_directory(record.metadata["id"]) / "BVP.csv", delimiter=",", skip_header=2)
    start = round(
        (record.metadata["clip_start_timestamp"] - record.metadata["native_record_start_timestamp"]) * 64
    )
    actual = np.asarray([r["ppg"] for r in record.all_samples])
    assert np.array_equal(actual, raw[start : start + 8576])
    assert np.any(actual < 0), "Signed BVP must remain signed; it is not MAX30102 ADC data"


def test_blocking_does_not_change_source_values_or_leak_mutable_rows(record):
    whole = record.block(0, 8576)
    pieces = []
    for i in range(0, 8576, 7):
        pieces.extend(record.block(i * PERIOD_US, min(7, 8576 - i)))
    assert whole == pieces
    whole[0]["ppg"] = 999999
    assert record.block(0, 1)[0]["ppg"] != 999999
    original = record.all_samples
    original[0]["ppg"] = 999999
    assert record.all_samples[0]["ppg"] != 999999


def test_policy_masks_before_processing_and_preserves_input_identity(record):
    critical = DatasetSource({"dataset_id": record.metadata["id"], "strategy": "critical"})
    periodic = DatasetSource({"dataset_id": record.metadata["id"], "strategy": "periodic", "phase": 5})
    assert record.input_hash == critical.input_hash == periodic.input_hash
    assert record.all_samples == critical.all_samples == periodic.all_samples
    rows = critical.block(0, 8576)
    assert any(not r["on"] and r["ppg"] is None for r in rows)
    assert all(r["on"] for r in rows[:640])
    on = sum(r["on"] for r in rows if r["time_us"] >= critical.t0_us) / 64
    assert on == 70
    off_index = round(25 * 64)  # t0 +15s, outside the critical plan
    assert rows[off_index]["on"] is False


def test_no_synthetic_fallback_or_fault_injection(record):
    with pytest.raises(ValueError, match="不存在"):
        DatasetSource({"dataset_id": "../../other"})
    with pytest.raises(ValueError, match="不接受人工故障"):
        record.block(0, 16, faults=[{"type": "noise"}])
    with pytest.raises(ValueError, match="对齐"):
        record.block(1, 16)


def test_missing_files_produce_empty_catalog_and_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setenv("REPULSE_DATASETS_DIR", str(tmp_path))
    assert list_datasets() == []
    with pytest.raises(ValueError, match="download_dataset"):
        get_dataset("wearable-aerobic-s01")


def test_source_timestamp_and_xyz_annotations_match_original_32_hz_rows(record):
    folder = record_directory(record.metadata["id"])
    raw_accel = np.genfromtxt(folder / "ACC.csv", delimiter=",", skip_header=2)
    start = round(
        (record.metadata["clip_start_timestamp"] - record.metadata["native_record_start_timestamp"]) * 32
    )
    rows = record.block(0, 128)
    for index, row in enumerate(rows):
        assert row["source_time_us"] == round(record.metadata["clip_start_timestamp"] * 1e6) + index * 15625
        assert (row["accel_x"], row["accel_y"], row["accel_z"]) == tuple(raw_accel[start + index // 2] / 64)
    assert (rows[0]["accel_x"], rows[0]["accel_y"], rows[0]["accel_z"]) == (
        rows[1]["accel_x"],
        rows[1]["accel_y"],
        rows[1]["accel_z"],
    )
