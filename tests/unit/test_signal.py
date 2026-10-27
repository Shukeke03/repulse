"""用已校验的真实BVP验证原生采样率、滤波连续性、质量检查和缺测传播。"""

import copy

import numpy as np
import pytest
from scipy.signal import sosfilt, sosfilt_zi

from backend.algorithms.processor import Processor
from backend.sources.public_data import PERIOD_US, DatasetSource


@pytest.fixture
def samples():
    """仅使用本地已下载记录，测试期间不访问网络。"""
    try:
        return DatasetSource({"dataset_id": "wearable-aerobic-s02", "strategy": "continuous"}).all_samples
    except ValueError:
        pytest.skip("Download the small real records first")


def process(samples, blocksize=16):
    """复制输入后按指定块大小处理，便于核对跨块滤波状态。"""
    processor = Processor({"sample_hz": 64})
    rows = copy.deepcopy(samples)
    for start in range(0, len(rows), blocksize):
        processor.feed(rows[start : start + blocksize])
    return processor


def test_native_64_hz_sos_state_is_independent_of_blocking(samples):
    a = process(samples, 7)
    b = process(samples, len(samples))
    assert a.period_us == PERIOD_US and a.window_samples == 512
    assert np.allclose([r["filtered"] for r in a.samples], [r["filtered"] for r in b.samples], atol=1e-7)
    raw = np.asarray([r["ppg"] for r in samples])
    expected, _ = sosfilt(a.sos, raw, zi=sosfilt_zi(a.sos) * raw[0])
    assert np.allclose(expected, [r["filtered"] for r in a.samples], atol=1e-7)


def test_real_record_uses_exact_fixed_support_and_honest_missing_baseline(samples):
    p = process(samples)
    windows = [p.window(c, 10_000_000, 134_000_000) for c in (0, 60, 120)]
    assert [w["sample_count"] for w in windows] == [512, 512, 512]
    assert [w["start_us"] for w in windows] == [6_000_000, 66_000_000, 126_000_000]
    assert not windows[0]["valid"] and windows[0]["pulse_bpm"] is None
    assert "IRREGULAR_INTERVALS" in windows[0]["reasons"]
    assert windows[1]["valid"] and windows[2]["valid"]
    assert windows[1]["pulse_bpm"] == pytest.approx(142.22, abs=0.01)
    assert windows[2]["pulse_bpm"] == pytest.approx(130.17, abs=0.01)


def test_signed_original_bvp_is_not_treated_as_adc_saturation(samples):
    p = process(samples)
    assert any(s["ppg"] < 0 for s in p.samples)
    valid = p.window(60, 10_000_000, 134_000_000)
    assert valid["valid"]
    assert "SATURATED" not in valid["reasons"]


def test_device_hr_reference_never_changes_dsp_answer(samples):
    # 故意改写设备参考值，证明脉率计算不依赖HR通道。
    a = process(samples)
    changed = copy.deepcopy(samples)
    for row in changed:
        row["reference_bpm"] = 999.0
    b = process(changed)
    assert [a.window(c, 10_000_000, 134_000_000) for c in (0, 60, 120)] == [
        b.window(c, 10_000_000, 134_000_000) for c in (0, 60, 120)
    ]


def test_missing_observation_breaks_continuity_and_propagates_quality(samples):
    # 仅在测试副本中制造单点缺口，后续完整窗口应正常恢复。
    rows = copy.deepcopy(samples)
    index = round(70 * 64)
    rows[index]["ppg"] = None
    rows[index]["gap"] = True
    p = process(rows)
    w = p.window(60, 10_000_000, 134_000_000)
    assert not w["valid"] and "DATA_GAP" in w["reasons"]
    assert p.window(120, 10_000_000, 134_000_000)["valid"]


def test_restart_requires_two_seconds_of_real_warmup(samples):
    rows = copy.deepcopy(samples)
    cut = round(65 * 64)
    for row in rows[cut:]:
        row["segment_id"] = 2
    p = process(rows)
    w = p.window(60, 10_000_000, 134_000_000)
    assert not w["valid"] and "WARMUP" in w["reasons"]
    assert p.window(120, 10_000_000, 134_000_000)["valid"]


def test_short_history_and_pending_result_are_explicit(samples):
    p = process(samples[:128])
    assert p.estimate(2_000_000)["valid"] is False
    w = p.window(0, 10_000_000, 11_000_000)
    assert w["status"] == "COLLECTING" and w["pulse_bpm"] is None
    assert "NOT_READY" in w["reasons"]


def test_unsupported_sample_rate_does_not_silently_resample():
    with pytest.raises(ValueError, match="sample rates"):
        Processor({"sample_hz": 125})
