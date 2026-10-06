import pandas as pd
import pytest

from floodsense.data.flood_prone import FLOOD_PRONE_FILE, build_trend_chart, load_flood_prone_areas


def test_committed_file_is_the_published_dataset_with_its_source():
    df = load_flood_prone_areas()
    assert list(df["year"]) == [2022, 2023, 2024, 2025]
    assert df["flood_prone_hectares"].between(0, 100).all()
    assert df["source_url"].str.contains("d_c4aed98f1533eb3a66f65dbb1a30da46").all()


def test_missing_file_fails_instead_of_inventing_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_flood_prone_areas(tmp_path / "absent.csv")


def test_chart_shows_one_bar_per_year():
    df = pd.read_csv(FLOOD_PRONE_FILE)
    fig = build_trend_chart(df)
    assert list(fig.data[0].x) == [str(y) for y in df["year"]]
