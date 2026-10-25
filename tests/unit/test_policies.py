import pytest

from backend.algorithms.policies import (
    acquisition_on,
    matched_periodic_width,
    periodic_on_seconds,
    recovery_result,
)


@pytest.mark.parametrize("phase", list(range(20)))
def test_periodic_budget_matches_critical_union_in_observation_interval(phase):
    width = matched_periodic_width(phase)
    assert periodic_on_seconds(width, phase) == pytest.approx(70, abs=1e-10)


def test_critical_half_open_intervals_and_preparation():
    assert acquisition_on(None) and acquisition_on(-30)
    for a, b in [(0, 10), (20, 30), (40, 50), (54, 64), (80, 90), (100, 110), (114, 124)]:
        assert acquisition_on(a)
        assert acquisition_on(b - 0.000001)
        assert not acquisition_on(b)
    assert not acquisition_on(124, "continuous")


def test_invalid_strategy_or_budget_rejected():
    with pytest.raises(ValueError):
        acquisition_on(1, "unknown")
    with pytest.raises(ValueError):
        matched_periodic_width(20)
    with pytest.raises(ValueError):
        matched_periodic_width(0, 125)


def test_missing_dependency_propagates_only_along_own_edges():
    windows = [
        {"id": "window_0", "center_seconds": 0, "valid": True, "pulse_bpm": 145},
        {"id": "window_60", "center_seconds": 60, "valid": False, "pulse_bpm": None, "reasons": ["DATA_GAP"]},
        {"id": "window_120", "center_seconds": 120, "valid": True, "pulse_bpm": 80},
    ]
    result = recovery_result(windows)
    assert result["delta60"] is None
    assert result["delta120"] == 65
    assert result["missing_reasons"]["delta60"] == [{"window": "60", "reasons": ["DATA_GAP"]}]
