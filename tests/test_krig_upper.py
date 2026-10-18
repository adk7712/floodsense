"""Offline tests for the kriged-upper-rain feature builder (pre-registered test T2)."""

import pytest

from floodsense.features import krig_upper


def test_cli_refuses_held_out_years() -> None:
    assert krig_upper.main(["--start-year", "2017", "--end-year", "2024", "--out-dir", "/x"]) == 2
    with pytest.raises(ValueError):
        krig_upper.build_month(2024, 1)


def test_variogram_loaded_from_results_file() -> None:
    v = krig_upper.load_variogram()
    assert v.model == "exponential"
    assert v.nugget == pytest.approx(0.46, abs=0.01)
    assert v.partial_sill == pytest.approx(0.81, abs=0.01)
    assert v.range_km == pytest.approx(32.0, abs=0.5)
