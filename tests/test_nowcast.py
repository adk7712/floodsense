import numpy as np
import pytest

pytest.importorskip("cv2")

from floodsense.radar.nowcast import accumulation_mm, extrapolate, motion_field  # noqa: E402


def _storm(shift_x: int, shift_y: int) -> np.ndarray:
    lv = np.zeros((120, 120), np.int16)
    lv[40:60, 30:50] = 20
    return np.roll(np.roll(lv, shift_x, axis=1), shift_y, axis=0)


def test_motion_recovers_a_moving_storm():
    frames = [_storm(4 * i, 2 * i) for i in range(3)]
    motion = motion_field(frames)
    dx, dy = motion[frames[-1] > 0].mean(axis=0)
    assert dx == pytest.approx(4, abs=0.3) and dy == pytest.approx(2, abs=0.3)


def test_extrapolation_moves_rain_downstream():
    frames = [_storm(4 * i, 2 * i) for i in range(3)]
    fields = extrapolate(frames[-1].astype(float), motion_field(frames), steps=6)
    truth = _storm(4 * 8, 2 * 8) > 0  # 6 more steps after the last frame (shift 8)
    pred = fields[-1] > 0.5
    assert (pred & truth).sum() / (pred | truth).sum() > 0.6


def test_still_rain_stays_put_and_accumulates():
    still = [_storm(0, 0)] * 3
    fields = extrapolate(still[-1].astype(float) * 12.0, motion_field(still), steps=12)
    total = accumulation_mm(fields)
    assert total[50, 40] == pytest.approx(240.0)  # 240 mm/h held for an hour
    assert total[5, 5] == 0
