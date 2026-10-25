import numpy as np
import pytest
from scipy.signal import sosfilt, sosfilt_zi

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.sources.simulator import Source
from backend.workers.compare import compare_unit, simulate, summarize


def process(config=None, t0_us=6_000_000, count=13000, blocksize=16, faults=None):
    src = Source(config)
    dsp = Processor()
    for offset in range(0, count, blocksize):
        dsp.feed(src.block(offset * 10000, min(blocksize, count - offset), t0_us=t0_us, faults=faults))
    return dsp


@pytest.mark.parametrize("bpm", [40, 60, 90, 120, 150, 180, 200])
def test_stable_pulse_engineering_range(bpm):
    p = process({"strategy": "continuous", "peak_bpm": bpm, "resting_bpm": bpm}, count=1200)
    w = p.window(0, 6_000_000, 12_000_000)
    assert w["valid"], w["reasons"]
    assert abs(w["pulse_bpm"] - bpm) <= 2.5


def test_sos_state_preserved_across_arbitrary_block_boundaries():
    a = process({"strategy": "continuous"}, count=1800, blocksize=7)
    b = process({"strategy": "continuous"}, count=1800, blocksize=1800)
    assert np.allclose([s["filtered"] for s in a.samples], [s["filtered"] for s in b.samples], atol=1e-7)
    raw = np.array([s["ppg"] for s in a.samples])
    expected, _ = sosfilt(a.sos, raw, zi=sosfilt_zi(a.sos) * raw[0])
    assert np.allclose(expected, [s["filtered"] for s in a.samples], atol=1e-7)


def test_normal_critical_windows_have_exact_support_and_warmup():
    p = process()
    for center in (0, 60, 120):
        w = p.window(center, 6_000_000, 130_000_000)
        assert w["valid"], w["reasons"]
        assert w["end_us"] - w["start_us"] == 8_000_000
        assert w["sample_count"] == 800
        assert w["warmup_start_us"] <= w["start_us"] - 2_000_000
    assert not p.window(120, 6_000_000, 129_999_999)["valid"]


def test_window_t0_does_not_need_to_align_with_adc_grid():
    p = process({"strategy": "continuous"}, t0_us=6_003_407, count=13001)
    w = p.window(0, 6_003_407, 10_003_407)
    assert w["valid"], w["reasons"]
    assert w["start_us"] == 2_003_407


def test_early_marker_is_not_shifted_to_make_baseline_valid():
    p = process(t0_us=2_000_000, count=12600)
    baseline = p.window(0, 2_000_000, 126_000_000)
    assert not baseline["valid"]
    assert "INSUFFICIENT_HISTORY" in baseline["reasons"]
    assert baseline["center_us"] == 2_000_000
    recovery = recovery_result(
        [baseline, p.window(60, 2_000_000, 126_000_000), p.window(120, 2_000_000, 126_000_000)]
    )
    assert recovery["delta60"] is None and recovery["delta120"] is None


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("gap", "DATA_GAP"),
        ("timeout", "SENSOR_FAULT"),
        ("detachment", "LOW_AMPLITUDE"),
        ("interference", "MOTION_ARTIFACT"),
    ],
)
def test_60_second_fault_does_not_erase_valid_120_result(scenario, reason):
    p = process({"scenario": scenario})
    windows = [p.window(center, 6_000_000, 130_000_000) for center in (0, 60, 120)]
    assert not windows[1]["valid"]
    assert reason in windows[1]["reasons"]
    assert windows[2]["valid"]
    result = recovery_result(windows)
    assert result["delta60"] is None
    assert result["delta120"] is not None


def test_processor_never_reads_scene_truth():
    samples = Source({"strategy": "continuous"}).block(0, 1200, t0_us=6_000_000)
    expected = Processor()
    expected.feed([dict(s) for s in samples])
    for sample in samples:
        sample.update({"truth_bpm": 11, "truth_valid": False, "scenario": "unreliable"})
    observed = Processor()
    observed.feed(samples)
    assert observed.window(0, 6_000_000, 12_000_000) == expected.window(0, 6_000_000, 12_000_000)


def test_off_immediately_invalidates_current_result_and_breaks_curve():
    p = process(count=1700)  # marker6sec +10sec first critical interval; now OFF
    result = p.estimate(17_000_000)
    assert not result["valid"] and result["pulse_bpm"] is None
    assert "ACQUISITION_OFF" in result["reasons"]
    assert p.samples[-1]["filtered"] is None


def test_restart_reinitializes_filter_and_warmup():
    p = process(count=2700)  # second interval restarts t=26sec
    restarted = [s for s in p.samples if s["time_us"] >= 26_000_000]
    assert all(s["warming"] for s in restarted)
    assert all(s["filtered"] is not None for s in restarted)
    assert len({s["segment_id"] for s in restarted}) == 1


def test_source_chunking_and_marker_during_stream_preserve_input():
    src = Source({"strategy": "continuous"})
    rows = src.block(0, 600)
    rows.extend(src.block(6_000_000, 600, t0_us=6_000_000))
    reference = Source({"strategy": "continuous"}).block(0, 1200, t0_us=6_000_000)
    assert rows == reference


def test_duplicate_or_reversed_samples_are_rejected():
    p = Processor()
    rows = Source().block(0)
    p.feed(rows)
    with pytest.raises(ValueError):
        p.feed(rows)


def test_compare_masks_before_dsp_and_uses_identical_input():
    rows = compare_unit(20261025, 0)
    assert len({row["input_hash"] for row in rows}) == 1
    periodic = next(row for row in rows if row["strategy"] == "periodic")
    critical = next(row for row in rows if row["strategy"] == "critical")
    assert periodic["on_seconds"] == critical["on_seconds"] == 70.0
    assert not periodic["windows"][1]["valid"]
    assert critical["windows"][1]["valid"]
    assert critical["energy_model"]["preparation_included"]


def test_partial_comparison_uses_paired_units_only():
    rows = compare_unit(10, 0)
    rows.append(simulate(11))
    summary = summarize(rows, expected_units=3)
    assert summary["paired_units"] == 1
    assert summary["excluded_units"] == 2
    assert all(row["runs"] == 1 for row in summary["strategies"])


def test_contact_threshold_is_opt_in_and_does_not_claim_unvalidated_calibration():
    samples = Source({"scenario": "detachment", "strategy": "continuous"}).block(0, 13000, t0_us=6_000_000)
    default = Processor()
    default.feed([dict(s) for s in samples])
    calibrated = Processor({"contact_threshold": 1000})
    calibrated.feed([dict(s) for s in samples])
    assert "NO_CONTACT" not in default.window(60, 6_000_000, 130_000_000)["reasons"]
    assert "NO_CONTACT" in calibrated.window(60, 6_000_000, 130_000_000)["reasons"]


def test_stopped_fault_preserves_past_and_has_no_future_effect():
    plan = {
        "type": "flatline",
        "anchor": "t0",
        "start_offset_us": 0,
        "duration_us": 20_000_000,
        "status": "STOPPED",
        "stopped_at_us": 8_000_000,
        "params": {"value": 60000},
    }
    src = Source({"strategy": "continuous"})
    rows = src.block(6_000_000, 400, t0_us=6_000_000, faults=[plan])
    assert all(s["ppg"] == 60000 for s in rows[:200])
    assert any(s["ppg"] != 60000 for s in rows[200:])
    plan["status"] = "REVOKED"
    revoked = Source({"strategy": "continuous"}).block(6_000_000, 100, t0_us=6_000_000, faults=[plan])
    assert any(s["ppg"] != 60000 for s in revoked)


def test_periodic_sample_budget_reports_instead_of_hiding_quantization():
    for phase in range(20):
        r = simulate(20261025, strategy="periodic", phase=phase)
        assert r["budget_error_seconds"] == pytest.approx(r["on_seconds"] - 70)
        assert abs(r["budget_error_seconds"]) <= r["budget_quantization_tolerance_seconds"] + 1e-9
        assert r["budget_model"]["periodic_intervals_unmodified"]


def test_false_valid_summary_does_not_hide_inaccurate_accepted_noisy_result():
    rows = compare_unit(20261025, 0)
    continuous = next(r for r in rows if r["strategy"] == "continuous")
    # A controlled evaluator outcome: a noise-corrupted but structurally complete
    # window passed the algorithm and differs from the clean reference by20 BPM.
    continuous["same_window_error_bpm"]["60"] = 20
    continuous["definite_invalid"]["60"] = False
    continuous["evaluable"]["60"] = True
    continuous["inaccurate_valid"]["60"] = True
    continuous["false_valid"]["60"] = True
    metrics = next(m for m in summarize(rows)["strategies"] if m["strategy"] == "continuous")
    assert metrics["false_valid_rate"]["60"] == 1
    assert metrics["false_valid_counts"]["60"] == {"numerator": 1, "denominator": 1}
    assert metrics["deterministic_false_valid_rate"]["60"] is None
    assert metrics["accepted_error_rate"]["60"] == 1
    assert metrics["accepted_error_counts"]["60"] == {"numerator": 1, "denominator": 1}
