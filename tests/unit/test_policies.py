import pytest

from backend.algorithms.policies import (
    acquisition_on,
    matched_periodic_width,
    periodic_on_seconds,
    recovery_result,
)


@pytest.mark.parametrize("phase", range(20))
def test_periodic_budget_solver_matches_same_analytic_budget(phase):
    width = matched_periodic_width(phase)
    assert periodic_on_seconds(width, phase) == pytest.approx(70, abs=1e-9)


def test_critical_intervals_are_half_open_and_prepare_is_continuous():
    assert acquisition_on(-1, "critical")
    for on in [0, 9.99, 20, 29.99, 54, 63.99, 114, 123.99]:
        assert acquisition_on(on, "critical")
    for off in [10, 19.99, 30, 50, 64, 110, 124]:
        assert not acquisition_on(off, "critical")


def test_recovery_dependencies_are_independent():
    windows = [
        {"id": "w0", "center_seconds": 0, "valid": True, "pulse_bpm": 150},
        {"id": "w60", "center_seconds": 60, "valid": False, "pulse_bpm": None, "reasons": ["DATA_GAP"]},
        {"id": "w120", "center_seconds": 120, "valid": True, "pulse_bpm": 100},
    ]
    result = recovery_result(windows)
    assert result["delta60"] is None
    assert result["delta120"] == 50
    assert result["missing_reasons"]["delta60"][0]["window"] == "60"
