"""Checks the committed static zone features (``data/reference/zone_static.csv``). Offline."""

import pandas as pd
import pytest

from floodsense.common.config import settings
from floodsense.spatial.singapore_geo import URA_PLANNING_AREAS

CSV = settings.root_dir / "data" / "reference" / "zone_static.csv"
COLUMNS = [
    "ura_planning_area",
    "built_up_pct",
    "built_up_land_pct",
    "elevation_m_mean",
    "low_lying_pct",
    "source_name",
    "source_url",
    "retrieved_at",
]


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return pd.read_csv(CSV, dtype={"source_name": str, "source_url": str, "retrieved_at": str})


def test_columns_and_row_count(df):
    assert list(df.columns) == COLUMNS
    assert len(df) == 55


def test_zone_names_match_registry(df):
    assert df["ura_planning_area"].is_unique
    assert set(df["ura_planning_area"]) == set(URA_PLANNING_AREAS)


def test_no_blanks(df):
    assert not df.isna().any().any()
    for col in ("source_name", "source_url", "retrieved_at"):
        assert (df[col].str.strip() != "").all()


def test_value_ranges(df):
    for col in ("built_up_pct", "built_up_land_pct", "low_lying_pct"):
        assert df[col].between(0, 100).all(), col
    assert df["elevation_m_mean"].between(-5, 170).all()
    pd.to_datetime(df["retrieved_at"], format="%Y-%m-%d")


def test_built_up_land_pct_at_least_built_up_pct(df):
    # Water is removed from the denominator, so the land share can only be equal or larger.
    assert (df["built_up_land_pct"] >= df["built_up_pct"]).all()
