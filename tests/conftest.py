"""Shared fixtures."""

import numpy as np
import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.features.feature_pipeline import FeaturePipeline
from floodsense.labels.policy import event_window, label_rows
from floodsense.models.training import LabelledData

SGT = "Asia/Singapore"
ZONES = ["BUKIT TIMAH", "BEDOK", "JURONG WEST"]


@pytest.fixture(scope="session", autouse=True)
def unset_false_alarm_budgets():
    """The repo's configured budgets were chosen on real data; tests start without them and set
    their own where they need thresholds."""
    saved = settings.false_alarm_budget_high, settings.false_alarm_budget_moderate
    settings.false_alarm_budget_high = settings.false_alarm_budget_moderate = None
    yield
    settings.false_alarm_budget_high, settings.false_alarm_budget_moderate = saved


@pytest.fixture(scope="module")
def data() -> LabelledData:
    """SYNTHETIC labelled data: storms in three zones over 2018-2025 where a flood starts 20
    minutes after 30-minute rain first exceeds 30 mm. It exercises the protocol (folds, leakage,
    calibration, thresholds, I/O), not model skill; never quote numbers from it."""
    rng = np.random.default_rng(42)
    frames, events = [], []
    for year in range(2018, 2026):
        for zone in ZONES:
            for s in range(15):
                start = pd.Timestamp(f"{year}-01-01", tz=SGT) + pd.Timedelta(
                    days=int(20 * s + rng.integers(0, 15)), hours=13
                )
                stamps = pd.date_range(start, periods=48, freq="5min")
                peak = rng.gamma(2.0, 3.5)  # mm per 5 min at the storm peak
                shape = np.exp(-(((np.arange(48) - 20) / 6.0) ** 2))
                rain = np.round(peak * shape + rng.uniform(0, 0.3, 48), 1)
                df = pd.DataFrame(
                    {"ura_planning_area": zone, "timestamp": stamps, "rainfall_mm": rain}
                )
                feats = FeaturePipeline().process_batch_dataframe(df, prune_zero_rain=False)
                frames.append(feats)
                crossing = feats.index[feats["rain_30m"] > 30.0]
                if len(crossing):
                    t0 = feats.loc[crossing[0], "timestamp"] + pd.Timedelta(minutes=20)
                    events.append(
                        type(
                            "Ev",
                            (),
                            dict(
                                ura_planning_area=zone,
                                timestamp_start=t0.to_pydatetime(),
                                timestamp_end=(t0 + pd.Timedelta(minutes=45)).to_pydatetime(),
                                time_precision="exact",
                                event_id=f"{zone}-{year}-{s}",
                            ),
                        )()
                    )
    features = pd.concat(frames, ignore_index=True)
    labels, report = label_rows(features, events)
    windows = [event_window(e) for e in events]
    assert not report.events_without_rows
    return LabelledData(features, labels["label_prob"], windows)
